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

        # 월세·관리비·부가세 나누기: 상계된 달의 청구 구성비를 따른다 (부가세가 없던 달에 부가세가 잡히지 않게)
        row_by_ym = {r["ym"]: r for r in before_ledger["months"]}
        alloc_total = sum(a["allocated"] for a in allocations)
        if alloc_total > 0:
            rp = mp = vp = 0.0
            for a in allocations:
                row = row_by_ym.get(a["ym"]) or {}
                due = row.get("due") or 0
                if due > 0:
                    rp += a["allocated"] * (row.get("rent") or 0) / due
                    mp += a["allocated"] * (row.get("maintenance") or 0) / due
                    vp += a["allocated"] * (row.get("vat") or 0) / due
            vat_part = round(vp)
            maint_part = round(mp)
            rent_part = amount - vat_part - maint_part
        elif contract.monthly_rent + contract.maintenance_fee + contract.compute_vat() > 0:
            monthly_std = contract.monthly_rent + contract.maintenance_fee + contract.compute_vat()
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
        from .ops import RentBillingNotice
        RentBillingNotice.resolve_paid_for_contract(contract.id, after_ledger, app_source=app_source)
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
        input_vat_deductible = sum(o.input_vat for o in outlays)
        deductible_expense_total = sum(o.amount for o in outlays if o.is_vat_deductible)
        non_deductible_expense_total = sum(o.amount for o in outlays if not o.is_vat_deductible)
        estimated_vat_payable = vat_collected - input_vat_deductible
        noi = gross_revenue - total_expense

        vat_rooms = bldg.clean_vat_rooms() if (bldg and bldg.vat_rooms) else [
            r.room_no for r in rooms if r.vat_applicable
        ]
        income_tax_rooms = bldg.clean_income_tax_rooms() if (bldg and bldg.income_tax_rooms) else [
            r.room_no for r in rooms
        ]
        income_tax_pays = [p for p in pays if not income_tax_rooms or p.room_no in income_tax_rooms]
        income_tax_revenue = sum(p.total_amount - p.vat_amount for p in income_tax_pays)
        vat_room_pays = [p for p in pays if p.room_no in vat_rooms or p.vat_amount > 0]
        vat_room_revenue = sum(p.total_amount for p in vat_room_pays)

        by_cat: Dict[str, int] = {k: 0 for k in EXPENSE_CATEGORIES}
        by_cat_vat: Dict[str, int] = {k: 0 for k in EXPENSE_CATEGORIES}
        for o in outlays:
            by_cat[o.category] = by_cat.get(o.category, 0) + o.amount
            by_cat_vat[o.category] = by_cat_vat.get(o.category, 0) + o.input_vat

        # 분리과세(15.4%: 소득세 14% + 지방소득세 1.4%) 추정 세액 (종소세 대상 호실 수입 기준)
        income_tax_noi = income_tax_revenue - total_expense
        taxable_income = max(0, noi)
        estimated_separate_tax = round(taxable_income * 0.154)
        prop_val = bldg.property_value if bldg else 1000000000
        cap_rate = round((noi / prop_val) * 100, 2) if prop_val > 0 else 0.0

        total_rooms = len(rooms)
        occupied = sum(1 for r in rooms if r.is_occupied)

        return {
            "branch": b_id,
            "year": yr,
            "building_id": bldg.id if bldg else "",
            "building_name": bldg.building_name if bldg else b_id,
            "lessor_name": lessor.name if lessor else "임대사업자",
            "business_number": lessor.business_number if lessor else "",
            "vat_rooms": vat_rooms,
            "income_tax_rooms": income_tax_rooms,
            "total_rooms": total_rooms,
            "occupied_rooms": occupied,
            "occupancy_rate": round((occupied / total_rooms) * 100, 1) if total_rooms else 0.0,
            "gross_revenue": gross_revenue,
            "income_tax_revenue": income_tax_revenue,
            "income_tax_noi": income_tax_noi,
            "vat_room_revenue": vat_room_revenue,
            "vat_collected": vat_collected,
            "input_vat_deductible": input_vat_deductible,
            "deductible_expense_total": deductible_expense_total,
            "non_deductible_expense_total": non_deductible_expense_total,
            "estimated_vat_payable": estimated_vat_payable,
            "total_operating_expense": total_expense,
            "noi": noi,
            "expense_ratio_pct": round((total_expense / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0,
            "estimated_separate_tax": estimated_separate_tax,
            "net_after_tax": noi - estimated_separate_tax,
            "cap_rate_pct": cap_rate,
            "expenses_by_category": [
                {
                    "category": k,
                    "label": EXPENSE_CATEGORIES[k],
                    "amount": by_cat[k],
                    "input_vat": by_cat_vat[k],
                    "vat_deductible": k not in {"insurance", "congrat_condol"},
                }
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
        writer.writerow(["일자", "분류", "적요", "거래처", "금액(원)", "부가세공제여부", "공제매입세액(원)"])
        for o in outlays:
            writer.writerow([
                o.expense_date,
                o.category_label,
                o.title,
                o.vendor,
                o.amount,
                "공제" if o.is_vat_deductible else "불공제(종소세만)",
                o.input_vat,
            ])
        return buf.getvalue()

    @classmethod
    def build_tax_invoice_register(
        cls,
        branch: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> Dict[str, Any]:
        """과세 호실(vat_rooms) 임차인 사업자 현황 및 월별/연간 세금계산서 발행 대장 산출."""
        b_id = normalize_branch_id(branch)
        yr = int(year or date_today().year)
        mo = int(month) if month else None
        ym_prefix = f"{yr:04d}-{mo:02d}" if mo else f"{yr:04d}"

        bldg = Building.first(branch=b_id)
        lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)
        rooms = sorted(Room.find(branch=b_id), key=lambda r: r.room_no)
        room_by_no = {r.room_no: r for r in rooms}
        contracts = Contract.find(branch=b_id)
        active_ct_by_room = {c.room_no: c for c in contracts if c.status == "ACTIVE"}
        ct_by_id = {c.id: c for c in contracts}
        tenants_by_id = {t.id: t for t in Tenant.find(branch=b_id)}

        vat_rooms: List[str] = bldg.clean_vat_rooms() if (bldg and bldg.vat_rooms) else [
            r.room_no for r in rooms if r.vat_applicable
        ]
        income_tax_rooms: List[str] = (
            bldg.clean_income_tax_rooms()
            if (bldg and bldg.income_tax_rooms)
            else [r.room_no for r in rooms]
        )

        # 1) 세입자가 세금계산서 발행을 요청한 계약/호실 목록 (사무실·주거용 기숙사 등 모든 호실 대상, 계약 시 요청한 경우만)
        candidate_room_nos: List[str] = []
        for r in rooms:
            ct = active_ct_by_room.get(r.room_no)
            tn = tenants_by_id.get(ct.id_tenant) if (ct and ct.id_tenant) else (
                tenants_by_id.get(r.id_tenant) if r.id_tenant else None
            )
            requested = bool(
                (ct and (ct.tax_invoice_requested or ct.tenant_business_number or ct.vat_target))
                or (tn and tn.business_number)
            )
            if requested and r.room_no not in candidate_room_nos:
                candidate_room_nos.append(r.room_no)

        vat_room_contracts: List[Dict[str, Any]] = []
        for r_no in candidate_room_nos:
            rm = room_by_no.get(r_no)
            ct = active_ct_by_room.get(r_no)
            tn = tenants_by_id.get(ct.id_tenant) if (ct and ct.id_tenant) else (
                tenants_by_id.get(rm.id_tenant) if (rm and rm.id_tenant) else None
            )
            biz_no = (
                (ct.tenant_business_number if ct and ct.tenant_business_number else "")
                or (tn.business_number if tn and tn.business_number else "")
            ).strip()
            comp_name = (
                (ct.tenant_company_name if ct and ct.tenant_company_name else "")
                or (tn.company_name if tn and tn.company_name else "")
            ).strip()
            ceo_name = (
                (ct.tenant_ceo_name if ct and ct.tenant_ceo_name else "")
                or (tn.ceo_name if tn and tn.ceo_name else "")
                or (ct.tenant_name if ct else (tn.name if tn else ""))
            ).strip()
            email = (
                (ct.tenant_email if ct and ct.tenant_email else "")
                or (tn.email if tn and tn.email else "")
            ).strip()
            missing: List[str] = []
            if not biz_no:
                missing.append("사업자등록번호")
            if not comp_name:
                missing.append("상호(법인명)")
            if not ceo_name:
                missing.append("대표자명")
            if not email:
                missing.append("수신이메일")

            m_rent = ct.monthly_rent if ct else (rm.monthly_rent if rm else 0)
            m_maint = ct.maintenance_fee if ct else (rm.maintenance_fee if rm else 0)
            m_vat = ct.compute_vat() if (ct and ct.vat_target) else round((m_rent + m_maint) * 0.1)
            vat_room_contracts.append({
                "room_no": r_no,
                "room_id": rm.id if rm else "",
                "room_category": rm.room_category if rm else "주거용 원룸",
                "is_occupied": bool(ct or (rm and rm.is_occupied)),
                "contract_id": ct.id if ct else "",
                "tenant_id": tn.id if tn else (ct.id_tenant if ct else ""),
                "tenant_name": ct.tenant_name if ct else (tn.name if tn else ""),
                "tenant_phone": ct.tenant_phone if ct else (tn.phone if tn else ""),
                "tax_invoice_requested": bool(ct.tax_invoice_requested) if ct else True,
                "business_number": biz_no,
                "company_name": comp_name,
                "ceo_name": ceo_name,
                "email": email,
                "vat_target": bool(ct.vat_target) if ct else True,
                "vat_separate": bool(ct.vat_separate) if ct else True,
                "monthly_rent": m_rent,
                "maintenance_fee": m_maint,
                "monthly_vat": m_vat,
                "ready_for_invoice": len(missing) == 0,
                "missing_fields": missing,
            })

        # 2) 기간 내 세금계산서 발행 요청 호실 및 과세 수납 내역 기반 세금계산서 발행 대장 산출
        all_pays = [
            p for p in Pay.find(branch=b_id)
            if p.payment_kind == "MONTHLY_RENT"
            and (p.paid_date.startswith(ym_prefix) or p.billing_month.startswith(ym_prefix))
        ]
        all_pays.sort(key=lambda p: (p.paid_date, p.room_no))

        supplier_biz_no = re.sub(r"\D", "", (lessor.business_number if lessor else "") or "")
        supplier_company = (
            (bldg.biz_company_name if bldg and bldg.biz_company_name else "")
            or (bldg.building_name if bldg else b_id)
        )
        raw_lessor_name = (lessor.name if lessor else "임대인").strip()
        supplier_ceo = re.sub(r"\s*\([^)]*\)", "", raw_lessor_name).strip() or raw_lessor_name
        supplier_addr = bldg.address if bldg else ""
        supplier_type = bldg.biz_type if bldg else "부동산업"
        supplier_item = bldg.biz_item if bldg else "비주거용 건물 임대업"
        supplier_email = (lessor.email if lessor else "") or ""

        invoices: List[Dict[str, Any]] = []
        for p in all_pays:
            ct = ct_by_id.get(p.id_contract) or active_ct_by_room.get(p.room_no)
            tn = tenants_by_id.get(p.id_tenant) or (tenants_by_id.get(ct.id_tenant) if ct else None)
            is_taxable = (
                bool(ct and (ct.tax_invoice_requested or ct.tenant_business_number or ct.vat_target))
                or bool(tn and tn.business_number)
                or (p.vat_amount > 0)
                or (p.room_no in vat_rooms)
            )
            if not is_taxable:
                continue
            biz_no_raw = (
                (ct.tenant_business_number if ct and ct.tenant_business_number else "")
                or (tn.business_number if tn and tn.business_number else "")
            ).strip()
            biz_no_digits = re.sub(r"\D", "", biz_no_raw)
            comp_name = (
                (ct.tenant_company_name if ct and ct.tenant_company_name else "")
                or (tn.company_name if tn and tn.company_name else "")
                or p.payer_name
            ).strip()
            ceo_name = (
                (ct.tenant_ceo_name if ct and ct.tenant_ceo_name else "")
                or (tn.ceo_name if tn and tn.ceo_name else "")
                or (ct.tenant_name if ct else p.payer_name)
            ).strip()
            email = (
                (ct.tenant_email if ct and ct.tenant_email else "")
                or (tn.email if tn and tn.email else "")
            ).strip()

            if p.vat_amount > 0:
                supply_amt = p.total_amount - p.vat_amount
                tax_amt = p.vat_amount
            elif ct and not ct.vat_separate:
                supply_amt = round(p.total_amount / 1.1)
                tax_amt = p.total_amount - supply_amt
            else:
                supply_amt = p.total_amount
                tax_amt = round(supply_amt * 0.1)

            w_date = re.sub(r"\D", "", p.paid_date[:10])
            mmdd = f"{w_date[4:6]}.{w_date[6:8]}" if len(w_date) == 8 else "01.01"

            missing: List[str] = []
            if not biz_no_digits:
                missing.append("사업자등록번호")
            if not comp_name:
                missing.append("상호")
            if not ceo_name:
                missing.append("대표자명")
            if not email:
                missing.append("이메일")

            invoices.append({
                "payment_id": p.id,
                "room_no": p.room_no,
                "billing_month": p.billing_month,
                "paid_date": p.paid_date,
                "write_date": w_date,
                "day_mmdd": mmdd,
                "buyer_business_number": biz_no_raw,
                "buyer_business_number_digits": biz_no_digits,
                "buyer_company_name": comp_name,
                "buyer_ceo_name": ceo_name,
                "buyer_email": email,
                "buyer_address": f"{supplier_addr} {p.room_no}호".strip(),
                "supply_amount": supply_amt,
                "tax_amount": tax_amt,
                "total_amount": supply_amt + tax_amt,
                "item_name": f"{p.billing_month}월분 사무실 임대료 및 관리비 ({p.room_no}호)",
                "ready_for_hometax": len(missing) == 0,
                "missing_fields": missing,
            })

        return {
            "branch": b_id,
            "year": yr,
            "month": mo,
            "building_name": bldg.building_name if bldg else b_id,
            "vat_rooms": vat_rooms,
            "income_tax_rooms": income_tax_rooms,
            "supplier": {
                "business_number": lessor.business_number if lessor else "",
                "business_number_digits": supplier_biz_no,
                "company_name": supplier_company,
                "ceo_name": supplier_ceo,
                "address": supplier_addr,
                "biz_type": supplier_type,
                "biz_item": supplier_item,
                "email": supplier_email,
            },
            "vat_room_contracts": vat_room_contracts,
            "invoices": invoices,
            "totals": {
                "count": len(invoices),
                "supply_amount": sum(i["supply_amount"] for i in invoices),
                "tax_amount": sum(i["tax_amount"] for i in invoices),
                "total_amount": sum(i["total_amount"] for i in invoices),
                "ready_count": sum(1 for i in invoices if i["ready_for_hometax"]),
                "incomplete_count": sum(1 for i in invoices if not i["ready_for_hometax"]),
            },
        }

    @classmethod
    def export_hometax_tax_invoice_csv(
        cls,
        branch: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> str:
        """국세청 홈택스 전자세금계산서 일괄발급 표준 포맷(UTF-8 BOM CSV) 생성."""
        reg = cls.build_tax_invoice_register(branch=branch, year=year, month=month)
        sup = reg["supplier"]

        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([
            "전자세금계산서종류(01:일반)",
            "작성일자",
            "공급자등록번호",
            "공급자종사업장번호",
            "공급자상호",
            "공급자성명",
            "공급자사업장주소",
            "공급자업태",
            "공급자종목",
            "공급자이메일",
            "공급받는자등록번호",
            "공급받는자종사업장번호",
            "공급받는자상호",
            "공급받는자성명",
            "공급받는자사업장주소",
            "공급받는자업태",
            "공급받는자종목",
            "공급받는자이메일1",
            "공급가액합계",
            "세액합계",
            "비고",
            "일자1(MM.DD)",
            "품목1",
            "규격1",
            "수량1",
            "단가1",
            "공급가액1",
            "세액1",
            "품목비고1",
            "영수(01)/청구(02)",
        ])
        for inv in reg["invoices"]:
            writer.writerow([
                "01",
                inv["write_date"],
                sup["business_number_digits"],
                "",
                sup["company_name"],
                sup["ceo_name"],
                sup["address"],
                sup["biz_type"],
                sup["biz_item"],
                sup["email"],
                inv["buyer_business_number_digits"],
                "",
                inv["buyer_company_name"],
                inv["buyer_ceo_name"],
                inv["buyer_address"],
                "",
                "",
                inv["buyer_email"],
                inv["supply_amount"],
                inv["tax_amount"],
                f"{inv['room_no']}호 임대료({inv['billing_month']})",
                inv["day_mmdd"],
                inv["item_name"],
                "",
                1,
                inv["supply_amount"],
                inv["supply_amount"],
                inv["tax_amount"],
                "",
                "01",
            ])
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

