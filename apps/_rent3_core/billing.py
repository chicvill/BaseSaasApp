"""
apps/_rent3_core/billing.py — Rent2 계승 스마트 은행문자 파서, FIFO 순차 상계 수납 엔진, 세무/증명서 산출기.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

from core.errors import Conflict, DomainError, NotFound
from .models import (
    Building,
    Contract,
    EXPENSE_CATEGORIES,
    Lessor,
    Outlay,
    Pay,
    Room,
    Tenant,
    current_ym,
    date_today,
    normalize_branch_id,
)


class SmsDepositParser:
    """은행 입금 알림 문자에서 [호실번호, 입금자명, 금액, 입금일]을 추출하는 순수 파서 객체."""

    @staticmethod
    def parse(text: str) -> Dict[str, Any]:
        s = (text or "").strip()
        if not s:
            raise DomainError("입금 문자 내용이 비어 있습니다.", hint="은행 입금 알림 문자를 복사하여 붙여넣어 주세요.")

        # 1) 금액 추출 ("잔액 ..." 이전의 입금액 우선 추출)
        before_balance = s.split("잔액")[0]
        amt_matches = re.findall(r"(\d{1,3}(?:,\d{3})+|\d{5,})\s*원?", before_balance)
        if not amt_matches:
            raise DomainError(
                "문자에서 입금 금액을 인식하지 못했습니다.",
                hint="예: '[신한] 10/25 201호 홍길동 550,000원 입금' 형태로 입력해 주세요.",
            )
        amount = int(amt_matches[-1].replace(",", ""))

        # 2) 날짜 추출 (MM/DD 또는 YYYY-MM-DD)
        today = date_today()
        paid_date = today.isoformat()
        m_full = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
        if m_full:
            paid_date = f"{int(m_full.group(1)):04d}-{int(m_full.group(2)):02d}-{int(m_full.group(3)):02d}"
        else:
            m_md = re.search(r"(?:^|\s)(\d{1,2})/(\d{1,2})(?:\s|$)", s)
            if m_md:
                paid_date = f"{today.year:04d}-{int(m_md.group(1)):02d}-{int(m_md.group(2)):02d}"

        # 3) 호실 번호 추출 (예: 201호 또는 이름 뒤 3자리 호수)
        room_no = ""
        m_room = re.search(r"\b([1-9]\d{2})\s*호", s)
        if m_room:
            room_no = m_room.group(1)
        else:
            m_room2 = re.search(r"(?:[가-힣]{2,4})\s*([1-9]\d{2})(?:\D|$)", s)
            if m_room2:
                room_no = m_room2.group(1)

        # 4) 입금자 성명 추출 (은행/입금/승인/잔액 키워드 제외 한글 2~4자)
        ignore_words = {"입금", "출금", "승인", "체크", "잔액", "신한", "국민", "우리", "하나", "농협", "카카오", "토스", "기업"}
        kor_tokens = re.findall(r"[가-힣]{2,4}", before_balance)
        payer_name = next((tok for tok in kor_tokens if tok not in ignore_words), "입금자")

        return {
            "room_no": room_no,
            "payer_name": payer_name,
            "amount": amount,
            "paid_date": paid_date,
            "raw_sms": s,
        }


class FifoPaymentEngine:
    """rent2 계승 FIFO(선입선출) 순차 상계 및 중복 입금 원천 차단 수납 엔진."""

    @staticmethod
    def collect(
        branch: str,
        room_no: str,
        amount: int,
        payer_name: str = "",
        paid_date: Optional[str] = None,
        method: str = "TRANSFER",
        raw_sms: str = "",
        up_to_ym: Optional[str] = None,
        app_source: str = "rent3_lessor",
    ) -> Dict[str, Any]:
        b_id = normalize_branch_id(branch)
        if amount <= 0:
            raise DomainError("입금 금액은 0원보다 커야 합니다.", hint="정확한 수납 금액을 입력해 주세요.")

        room = Room.first(branch=b_id, room_no=room_no)
        if not room:
            raise NotFound(f"{b_id} 지점에서 '{room_no}호'를 찾을 수 없습니다.", hint="호실 번호를 다시 확인해 주세요.")

        contract = Contract.get(room.active_contract_id) if room.active_contract_id else None
        if not contract:
            contract = Contract.first(id_room=room.id, status="ACTIVE")
        if not contract:
            raise DomainError(
                f"{room.room_no}호는 현재 활성 임대계약이 없는 공실입니다.",
                hint="먼저 공동 임대계약 앱에서 계약을 체결한 뒤 수납을 등록해 주세요.",
            )

        p_date = paid_date or date_today().isoformat()
        p_name = (payer_name or contract.tenant_name or "세입자").strip()
        idem_key = f"{room.id}:{p_name}:{amount}:{p_date}:{raw_sms.strip()[:40]}"

        # 중복 입금 원천 차단 (Idempotency -> 황색 경고 유도)
        dup = Pay.first(idempotency_key=idem_key)
        if dup:
            raise Conflict(
                f"이미 등록된 동일 입금 내역입니다 ({room.room_no}호 {p_name} {amount:,}원, {p_date}).",
                hint="중복 수납이 자동 차단되었습니다. 수납 내역 장부를 확인해 주세요.",
            )

        # FIFO 상계 전 원장 조회
        target_ym = up_to_ym or current_ym()
        before_ledger = contract.ledger(up_to_ym=target_ym)
        unpaid_rows = [r for r in before_ledger["months"] if r["unpaid"] > 0]

        remaining = amount
        allocations: List[Dict[str, Any]] = []
        rent_part = 0
        maint_part = 0
        vat_part = 0

        for row in unpaid_rows:
            if remaining <= 0:
                break
            take = min(remaining, row["unpaid"])
            remaining -= take
            allocations.append({
                "ym": row["ym"],
                "allocated": take,
                "remaining_unpaid_in_month": row["unpaid"] - take,
                "cleared": (row["unpaid"] - take) == 0,
            })

        # 비례 배분 요약
        monthly_std = contract.monthly_rent + contract.maintenance_fee + contract.compute_vat()
        if monthly_std > 0:
            ratio = amount / monthly_std
            rent_part = round(contract.monthly_rent * ratio)
            vat_part = round(contract.compute_vat() * ratio)
            maint_part = max(0, amount - rent_part - vat_part)
        else:
            rent_part = amount

        billing_ym = allocations[0]["ym"] if allocations else target_ym
        pay_obj = Pay(
            branch=b_id,
            id_building=room.id_building,
            id_room=room.id,
            room_no=room.room_no,
            id_contract=contract.id,
            id_tenant=contract.id_tenant,
            payer_name=p_name,
            billing_month=billing_ym,
            paid_date=p_date,
            rent_amount=rent_part,
            maintenance_amount=maint_part,
            vat_amount=vat_part,
            total_amount=amount,
            payment_kind="MONTHLY_RENT",
            method=method,
            allocated_months=allocations,
            idempotency_key=idem_key,
            raw_sms=raw_sms,
        ).save(action="fifo_payment_collected", app_source=app_source)

        after_ledger = contract.ledger(up_to_ym=target_ym)
        diff = before_ledger["total_unpaid"] - amount
        if after_ledger["total_unpaid"] > 0:
            banner_level = "diff"
            banner_msg = (
                f"{room.room_no}호 {amount:,}원 FIFO 수납 반영 완료 — "
                f"잔여 미납액 {after_ledger['total_unpaid']:,}원이 남아 있습니다."
            )
        elif remaining > 0 and not unpaid_rows:
            banner_level = "warning"
            banner_msg = f"{room.room_no}호 미납액이 없어 {amount:,}원이 선수금(크레딧)으로 적립되었습니다."
        else:
            banner_level = "success"
            banner_msg = f"{room.room_no}호 {p_name}님 {amount:,}원 FIFO 순차 완납 처리되었습니다."

        return {
            "payment": pay_obj.model_dump(),
            "allocations": allocations,
            "ledger": after_ledger,
            "difference": diff,
            "level": banner_level,
            "message": banner_msg,
        }

    @classmethod
    def parse_and_collect_sms(
        cls,
        branch: str,
        sms_text: str,
        up_to_ym: Optional[str] = None,
        app_source: str = "rent3_lessor",
    ) -> Dict[str, Any]:
        parsed = SmsDepositParser.parse(sms_text)
        room_no = parsed["room_no"]
        b_id = normalize_branch_id(branch)
        if not room_no:
            # 입금자명으로 활성 계약 호실 역추적
            matched_ct = Contract.first(branch=b_id, tenant_name=parsed["payer_name"], status="ACTIVE")
            if not matched_ct:
                raise DomainError(
                    f"문자에서 호실 번호를 찾을 수 없으며 '{parsed['payer_name']}' 명의의 활성 계약도 없습니다.",
                    hint="호실 번호를 직접 선택하거나 문자에 '201호'를 포함해 주세요.",
                )
            room_no = matched_ct.room_no
        result = cls.collect(
            branch=b_id,
            room_no=room_no,
            amount=parsed["amount"],
            payer_name=parsed["payer_name"],
            paid_date=parsed["paid_date"],
            method="BANK_SMS",
            raw_sms=sms_text,
            up_to_ym=up_to_ym,
            app_source=app_source,
        )
        result["parsed"] = parsed
        return result


class TaxAndCertificateService:
    """공용 운영비 NOI 결산, 5월 종소세 홈택스 CSV, 세입자 17% 월세 세액공제 납입증명서 생성기."""

    @staticmethod
    def branch_financial_summary(branch: str, year: Optional[int] = None) -> Dict[str, Any]:
        b_id = normalize_branch_id(branch)
        yr = year or date_today().year
        yr_prefix = f"{yr:04d}"

        bldg = Building.first(branch=b_id)
        lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)
        rooms = Room.find(branch=b_id)
        pays = [
            p for p in Pay.find(branch=b_id)
            if p.payment_kind == "MONTHLY_RENT" and p.paid_date.startswith(yr_prefix)
        ]
        outlays = [
            o for o in Outlay.find(branch=b_id)
            if o.expense_date.startswith(yr_prefix)
        ]

        gross_revenue = sum(p.total_amount for p in pays)
        vat_collected = sum(p.vat_amount for p in pays)
        total_expense = sum(o.amount for o in outlays)
        noi = gross_revenue - total_expense

        by_cat: Dict[str, int] = {k: 0 for k in EXPENSE_CATEGORIES}
        for o in outlays:
            by_cat[o.category] = by_cat.get(o.category, 0) + o.amount

        # 분리과세(15.4%: 소득세 14% + 지방소득세 1.4%) 추정 세액
        taxable_income = max(0, noi)
        estimated_separate_tax = round(taxable_income * 0.154)
        prop_val = bldg.property_value if bldg else 1000000000
        cap_rate = round((noi / prop_val) * 100, 2) if prop_val > 0 else 0.0

        total_rooms = len(rooms)
        occupied = sum(1 for r in rooms if r.is_occupied)

        return {
            "branch": b_id,
            "year": yr,
            "building_name": bldg.building_name if bldg else b_id,
            "lessor_name": lessor.name if lessor else "임대사업자",
            "business_number": lessor.business_number if lessor else "",
            "total_rooms": total_rooms,
            "occupied_rooms": occupied,
            "occupancy_rate": round((occupied / total_rooms) * 100, 1) if total_rooms else 0.0,
            "gross_revenue": gross_revenue,
            "vat_collected": vat_collected,
            "total_operating_expense": total_expense,
            "noi": noi,
            "expense_ratio_pct": round((total_expense / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0,
            "estimated_separate_tax": estimated_separate_tax,
            "net_after_tax": noi - estimated_separate_tax,
            "cap_rate_pct": cap_rate,
            "expenses_by_category": [
                {"category": k, "label": EXPENSE_CATEGORIES[k], "amount": by_cat[k]}
                for k in EXPENSE_CATEGORIES
            ],
            "outlay_count": len(outlays),
            "payment_count": len(pays),
        }

    @classmethod
    def export_annual_tax_csv(cls, branch: str, year: Optional[int] = None) -> str:
        summary = cls.branch_financial_summary(branch, year)
        b_id = summary["branch"]
        yr = summary["year"]
        outlays = [
            o for o in Outlay.find(branch=b_id)
            if o.expense_date.startswith(f"{yr:04d}")
        ]

        buf = io.StringIO()
        buf.write("\ufeff")  # Excel 한글 깨짐 원천 방지 UTF-8 BOM
        writer = csv.writer(buf)
        writer.writerow(["[국세청 홈택스 제출용 연간 임대소득·필요경비 표준 명세서]"])
        writer.writerow(["과세연도", yr, "건물명", summary["building_name"], "임대인", summary["lessor_name"], "사업자번호", summary["business_number"]])
        writer.writerow([])
        writer.writerow(["구분", "금액(원)", "비고"])
        writer.writerow(["연간 총수입금액", summary["gross_revenue"], f"수납 {summary['payment_count']}건 합계"])
        writer.writerow(["법정 필요경비 합계", summary["total_operating_expense"], f"지출 {summary['outlay_count']}건 합계"])
        writer.writerow(["순임대소득금액 (NOI)", summary["noi"], f"수익률 {summary['cap_rate_pct']}%"])
        writer.writerow(["분리과세 예상세액 (15.4%)", summary["estimated_separate_tax"], "소득세 14% + 지방소득세 1.4%"])
        writer.writerow([])
        writer.writerow(["[8대 표준 공용 운영비 항목별 집계]"])
        writer.writerow(["코드", "항목명", "연간 지출액(원)"])
        for item in summary["expenses_by_category"]:
            writer.writerow([item["category"], item["label"], item["amount"]])
        writer.writerow([])
        writer.writerow(["[세부 지출 증빙 원장]"])
        writer.writerow(["일자", "분류", "적요", "거래처", "금액(원)"])
        for o in outlays:
            writer.writerow([o.expense_date, o.category_label, o.title, o.vendor, o.amount])
        return buf.getvalue()

    @staticmethod
    def build_tenant_tax_certificate(contract_id: str, year: Optional[int] = None) -> Dict[str, Any]:
        """조세특례제한법 제95조의2 규격 연말정산 월세 세액공제(17%) 납입증명서 데이터."""
        ct = Contract.require(contract_id)
        yr = year or date_today().year
        bldg = Building.get(ct.id_building)
        lessor = Lessor.get(ct.id_lessor) if ct.id_lessor else Lessor.first(branch=ct.branch)
        tenant = Tenant.get(ct.id_tenant) if ct.id_tenant else None

        pays = [
            p for p in Pay.find(id_contract=ct.id)
            if p.payment_kind == "MONTHLY_RENT" and p.paid_date.startswith(f"{yr:04d}")
        ]
        total_paid = sum(p.total_amount for p in pays)
        deductible_base = min(total_paid, 10_000_000)  # 연 1,000만원 한도
        estimated_refund_17pct = round(deductible_base * 0.17)

        return {
            "certificate_no": f"CERT-{yr}-{ct.id[-6:].upper()}",
            "tax_year": yr,
            "statute": "조세특례제한법 제95조의2 (월세액에 대한 세액공제 17%)",
            "lessor": {
                "name": lessor.name if lessor else ct.lessor_name,
                "business_number": lessor.business_number if lessor else "101-86-00000",
                "phone": lessor.phone if lessor else "",
                "address": bldg.address if bldg else "",
                "building_name": bldg.building_name if bldg else "",
            },
            "tenant": {
                "name": tenant.name if tenant else ct.tenant_name,
                "phone": tenant.phone if tenant else ct.tenant_phone,
                "room_no": ct.room_no,
            },
            "contract": {
                "id": ct.id,
                "move_in_date": ct.move_in_date,
                "expiry_date": ct.expiry_date,
                "deposit": ct.deposit,
                "monthly_rent": ct.monthly_rent,
                "fixed_date_no": ct.fixed_date_no,
            },
            "payments": [
                {
                    "billing_month": p.billing_month,
                    "paid_date": p.paid_date,
                    "amount": p.total_amount,
                    "cash_receipt_no": p.cash_receipt_no,
                    "method": p.method,
                }
                for p in pays
            ],
            "total_paid": total_paid,
            "estimated_tax_credit_17pct": estimated_refund_17pct,
            "issued_at": date_today().isoformat(),
        }

