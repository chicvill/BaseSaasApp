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
    TaxInvoiceRecord,
    Tenant,
    current_ym,
    date_today,
    normalize_branch_id,
    validate_email_format,
    validate_kr_business_number,
)
from .pool import now_iso


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
    """공용 운영비 NOI 결산, 5월 종소세·2월 사업장현황신고·부가세 홈택스 CSV, 세입자 월세 세액공제 납부확인서 생성기."""

    TAX_DISCLAIMER: str = (
        "⚠️ 본 화면·파일의 부가가치세·종합소득세·사업장현황신고 수치는 장부 입력값 기반의 참고용 추정치입니다. "
        "실제 국세청 신고 전 반드시 담당 세무사 또는 홈택스에서 최종 확인하시기 바랍니다."
    )

    @staticmethod
    def is_carryover_payment(p: Pay) -> bool:
        """장부이관 시 과거 수년치 누적 입금을 한 날짜에 몰아 만든 이월 수납(:carry)인지 판별."""
        if p.is_carryover:
            return True
        if (p.idempotency_key or "").endswith(":carry"):
            return True
        if "장부 이전 입금 합산" in (p.raw_sms or ""):
            return True
        return False

    @classmethod
    def payment_tax_ym(cls, p: Pay) -> str:
        """수입 귀속년월 산출 — 입금일(paid_date)이 아닌 약정 청구월(billing_month) 우선 귀속."""
        bm = (p.billing_month or "").strip()
        if len(bm) >= 7 and bm[:4].isdigit() and bm[5:7].isdigit():
            return bm[:7]
        pd = (p.paid_date or "").strip()
        if len(pd) >= 7 and pd[:4].isdigit() and pd[5:7].isdigit():
            return pd[:7]
        return ""

    @classmethod
    def estimate_progressive_income_tax(cls, net_income: int, basic_deduction: int = 1_500_000) -> Dict[str, Any]:
        """소득세법 제55조 종합소득 과세표준 구간별 누진세율(6% ~ 45%) 및 지방소득세(10%) 참고용 추정.
        - net_income: 필요경비 차감 후 순임대소득금액 (타 소득 합산 전 단독 임대소득 기준)
        - basic_deduction: 본인 기본 인적공제(150만 원) 기본 반영
        """
        taxable_base = max(0, net_income - max(0, basic_deduction))
        brackets = [
            (14_000_000, 0.06, 0, "1,400만 원 이하 (기본세율 6% · 지방세 포함 6.6%)"),
            (50_000_000, 0.15, 1_260_000, "1,400만~5,000만 원 이하 (세율 15% · 지방세 포함 16.5% · 누진공제 126만)"),
            (88_000_000, 0.24, 5_760_000, "5,000만~8,800만 원 이하 (세율 24% · 지방세 포함 26.4% · 누진공제 576만)"),
            (150_000_000, 0.35, 15_440_000, "8,800만~1억5,000만 원 이하 (세율 35% · 지방세 포함 38.5% · 누진공제 1,544만)"),
            (300_000_000, 0.38, 19_940_000, "1억5,000만~3억 원 이하 (세율 38% · 지방세 포함 41.8% · 누진공제 1,994만)"),
            (500_000_000, 0.40, 25_940_000, "3억~5억 원 이하 (세율 40% · 지방세 포함 44.0% · 누진공제 2,594만)"),
            (1_000_000_000, 0.42, 35_940_000, "5억~10억 원 이하 (세율 42% · 지방세 포함 46.2% · 누진공제 3,594만)"),
            (10**15, 0.45, 65_940_000, "10억 원 초과 (세율 45% · 지방세 포함 49.5% · 누진공제 6,594만)"),
        ]
        chosen = brackets[0]
        for limit_amt, rate, ded, label in brackets:
            chosen = (limit_amt, rate, ded, label)
            if taxable_base <= limit_amt:
                break
        _, rate, prog_ded, bracket_label = chosen
        income_tax = max(0, round(taxable_base * rate) - prog_ded) if taxable_base > 0 else 0
        local_tax = round(income_tax * 0.1)
        return {
            "net_income": max(0, net_income),
            "basic_deduction": basic_deduction if net_income > 0 else 0,
            "taxable_base": taxable_base,
            "rate_pct": round(rate * 100, 1),
            "rate_with_local_pct": round(rate * 110, 1),
            "progressive_deduction": prog_ded,
            "bracket_label": bracket_label,
            "income_tax": income_tax,
            "local_income_tax": local_tax,
            "total_tax_inc_local": income_tax + local_tax,
        }

    @classmethod
    def branch_financial_summary(cls, branch: str, year: Optional[int] = None) -> Dict[str, Any]:
        b_id = normalize_branch_id(branch)
        yr = year or date_today().year
        yr_prefix = f"{yr:04d}"

        bldg = Building.first(branch=b_id)
        lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)
        rooms = sorted(Room.find(branch=b_id), key=lambda r: r.room_no)
        all_branch_pays = [
            p for p in Pay.find(branch=b_id)
            if p.payment_kind == "MONTHLY_RENT"
        ]
        # 이월 누적 정산분(:carry) 분리 및 약정 청구월(billing_month) 기준 당해 귀속 수납 필터링
        carryover_pays = [
            p for p in all_branch_pays
            if cls.is_carryover_payment(p) and (p.paid_date.startswith(yr_prefix) or (p.billing_month or "").startswith(yr_prefix))
        ]
        carryover_excluded_amount = sum(p.total_amount for p in carryover_pays)
        pays = [
            p for p in all_branch_pays
            if not cls.is_carryover_payment(p) and cls.payment_tax_ym(p).startswith(yr_prefix)
        ]
        outlays = [
            o for o in Outlay.find(branch=b_id)
            if o.expense_date.startswith(yr_prefix)
        ]

        active_ct_by_room = {
            c.room_no: c for c in Contract.find(branch=b_id) if c.status == "ACTIVE"
        }
        vat_rooms = bldg.clean_vat_rooms() if (bldg and bldg.vat_rooms) else [
            r.room_no for r in rooms if r.vat_applicable
        ]
        income_tax_rooms = bldg.clean_income_tax_rooms() if (bldg and bldg.income_tax_rooms) else [
            r.room_no for r in rooms
        ]

        # 부가세(예수금)는 총수입금액에서 제외한다 (총수납액 vs 공급가액 분리)
        gross_receipts_inc_vat = sum(p.total_amount for p in pays)
        vat_collected = sum(p.vat_amount for p in pays)
        gross_revenue = sum(max(0, p.total_amount - p.vat_amount) for p in pays)
        total_expense = sum(o.amount for o in outlays)

        # 과세 공급가액 vs 면세(주거) 공급가액 분리 -> 공통매입세액 안분 비율 산출
        def _is_vat_room_or_pay(p: Pay) -> bool:
            ct = active_ct_by_room.get(p.room_no)
            return (
                p.room_no in vat_rooms
                or p.vat_amount > 0
                or bool(ct and (ct.vat_target or ct.tax_invoice_requested))
            )

        vat_room_pays = [p for p in pays if _is_vat_room_or_pay(p)]
        exempt_room_pays = [p for p in pays if not _is_vat_room_or_pay(p)]
        taxable_supply_total = sum(max(0, p.total_amount - p.vat_amount) for p in vat_room_pays)
        exempt_supply_total = sum(max(0, p.total_amount - p.vat_amount) for p in exempt_room_pays)

        if (taxable_supply_total + exempt_supply_total) > 0:
            taxable_supply_ratio = taxable_supply_total / (taxable_supply_total + exempt_supply_total)
        elif len(rooms) > 0:
            taxable_supply_ratio = len(vat_rooms) / len(rooms)
        else:
            taxable_supply_ratio = 1.0

        raw_input_vat_total = sum(o.input_vat for o in outlays)
        input_vat_deductible = sum(o.effective_input_vat(taxable_supply_ratio) for o in outlays)
        input_vat_non_deductible_apportion = max(0, raw_input_vat_total - input_vat_deductible)
        deductible_expense_total = sum(o.amount for o in outlays if o.is_vat_deductible)
        non_deductible_expense_total = sum(o.amount for o in outlays if not o.is_vat_deductible)
        estimated_vat_payable = vat_collected - input_vat_deductible
        noi = gross_revenue - total_expense

        income_tax_pays = [p for p in pays if not income_tax_rooms or p.room_no in income_tax_rooms]
        income_tax_revenue = sum(max(0, p.total_amount - p.vat_amount) for p in income_tax_pays)
        vat_room_revenue = sum(max(0, p.total_amount - p.vat_amount) for p in vat_room_pays)

        # 종합소득세: ① 주택임대(주거용 면세 호실) vs ② 상가·사무실 임대(과세 호실) 분리 산출
        housing_pays = [p for p in income_tax_pays if p.room_no not in vat_rooms and p.vat_amount == 0]
        commercial_pays = [p for p in income_tax_pays if p.room_no in vat_rooms or p.vat_amount > 0]
        housing_revenue = sum(max(0, p.total_amount - p.vat_amount) for p in housing_pays)
        commercial_revenue = sum(max(0, p.total_amount - p.vat_amount) for p in commercial_pays)

        # 주택임대 분리과세(연 2,000만 원 이하 선택 가능): 실제 경비가 아닌 필요경비율(미등록 50%, 등록 60%) 및 기본공제(200만/400만) 적용
        housing_separate_eligible = housing_revenue <= 20_000_000
        housing_taxable_general = max(0, round(housing_revenue * 0.50) - 2_000_000) if housing_separate_eligible else 0
        housing_separate_tax_general = round(housing_taxable_general * 0.154) if housing_separate_eligible else 0
        housing_taxable_registered = max(0, round(housing_revenue * 0.40) - 4_000_000) if housing_separate_eligible else 0
        housing_separate_tax_registered = round(housing_taxable_registered * 0.154) if housing_separate_eligible else 0

        # 상가·사무실 임대는 분리과세 불가 -> 실제 경비 차감 후 종합과세(6.6% ~ 49.5% 누진세율)
        commercial_expense_allocated = round(total_expense * taxable_supply_ratio) if len(rooms) > len(vat_rooms) else total_expense
        housing_expense_allocated = max(0, total_expense - commercial_expense_allocated)
        commercial_noi = commercial_revenue - commercial_expense_allocated
        housing_noi = housing_revenue - housing_expense_allocated

        # 시크빌 등 주택임대 연 수입 2,000만 원 초과 시: 주택+상가 전체 순임대소득을 합산해 종합과세 누진세율 구간(6.6%~49.5%)으로 산출
        income_tax_noi_val = income_tax_revenue - total_expense
        comprehensive_all_est = cls.estimate_progressive_income_tax(income_tax_noi_val)
        comprehensive_commercial_est = cls.estimate_progressive_income_tax(commercial_noi)
        comprehensive_housing_only_est = cls.estimate_progressive_income_tax(housing_noi)

        def _build_room_breakdown(target_pays: List[Pay], room_filter: List[str]) -> List[Dict[str, Any]]:
            grouped: Dict[str, Dict[str, Any]] = {}
            for r_no in room_filter:
                ct = active_ct_by_room.get(r_no)
                grouped[r_no] = {
                    "room_no": r_no,
                    "tenant_name": ct.tenant_name if ct else "",
                    "count": 0,
                    "rent_amount": 0,
                    "maintenance_amount": 0,
                    "supply_amount": 0,
                    "vat_amount": 0,
                    "total_amount": 0,
                    "h1_supply": 0,
                    "h1_vat": 0,
                    "h2_supply": 0,
                    "h2_vat": 0,
                }
            for p in sorted(target_pays, key=lambda x: (x.room_no, cls.payment_tax_ym(x), x.paid_date)):
                r_no = p.room_no
                if r_no not in grouped:
                    ct = active_ct_by_room.get(r_no)
                    grouped[r_no] = {
                        "room_no": r_no,
                        "tenant_name": (ct.tenant_name if ct else "") or p.payer_name,
                        "count": 0,
                        "rent_amount": 0,
                        "maintenance_amount": 0,
                        "supply_amount": 0,
                        "vat_amount": 0,
                        "total_amount": 0,
                        "h1_supply": 0,
                        "h1_vat": 0,
                        "h2_supply": 0,
                        "h2_vat": 0,
                    }
                g = grouped[r_no]
                if not g["tenant_name"] and p.payer_name:
                    g["tenant_name"] = p.payer_name
                # 부가세를 임의로 10% 추정하지 않고 실제 수납 기록(p.vat_amount)만 그대로 집계하여 요약과 100% 일치시킨다.
                vat_val = p.vat_amount
                sup = max(0, p.total_amount - vat_val)
                g["count"] += 1
                g["rent_amount"] += p.rent_amount
                g["maintenance_amount"] += p.maintenance_amount
                g["supply_amount"] += sup
                g["vat_amount"] += vat_val
                g["total_amount"] += p.total_amount
                tax_ym = cls.payment_tax_ym(p)
                mo_num = int(tax_ym[5:7]) if len(tax_ym) >= 7 and tax_ym[5:7].isdigit() else 1
                if mo_num <= 6:
                    g["h1_supply"] += sup
                    g["h1_vat"] += vat_val
                else:
                    g["h2_supply"] += sup
                    g["h2_vat"] += vat_val
            return [grouped[k] for k in sorted(grouped.keys())]

        def _build_pay_rows(target_pays: List[Pay]) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            for p in sorted(target_pays, key=lambda x: (cls.payment_tax_ym(x), x.paid_date, x.room_no), reverse=True):
                vat_val = p.vat_amount
                sup = max(0, p.total_amount - vat_val)
                rows.append({
                    "id": p.id,
                    "paid_date": p.paid_date,
                    "billing_month": p.billing_month,
                    "room_no": p.room_no,
                    "payer_name": p.payer_name,
                    "rent_amount": p.rent_amount,
                    "maintenance_amount": p.maintenance_amount,
                    "supply_amount": sup,
                    "vat_amount": vat_val,
                    "total_amount": p.total_amount,
                })
            return rows

        vat_room_breakdown = _build_room_breakdown(vat_room_pays, vat_rooms)
        vat_payment_rows = _build_pay_rows(vat_room_pays)
        income_tax_room_breakdown = _build_room_breakdown(income_tax_pays, income_tax_rooms)
        income_tax_payment_rows = _build_pay_rows(income_tax_pays)

        by_cat: Dict[str, int] = {k: 0 for k in EXPENSE_CATEGORIES}
        by_cat_vat: Dict[str, int] = {k: 0 for k in EXPENSE_CATEGORIES}
        for o in outlays:
            by_cat[o.category] = by_cat.get(o.category, 0) + o.amount
            by_cat_vat[o.category] = by_cat_vat.get(o.category, 0) + o.effective_input_vat(taxable_supply_ratio)

        income_tax_noi = income_tax_revenue - total_expense
        # 주택임대 연 2,000만 원 이하이면 분리과세(일반 50% 경비율) + 상가 종합과세 추정, 2,000만 원 초과(시크빌 등)이면 전체 합산 종합과세 누진세율 추정
        estimated_separate_tax = housing_separate_tax_general
        estimated_comprehensive_tax = (
            comprehensive_commercial_est["total_tax_inc_local"]
            if housing_separate_eligible
            else comprehensive_all_est["total_tax_inc_local"]
        )
        estimated_total_income_tax = estimated_separate_tax + estimated_comprehensive_tax
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
            "gross_receipts_inc_vat": gross_receipts_inc_vat,
            "gross_revenue": gross_revenue,
            "carryover_excluded_amount": carryover_excluded_amount,
            "carryover_excluded_count": len(carryover_pays),
            "income_tax_revenue": income_tax_revenue,
            "income_tax_noi": income_tax_noi,
            "housing_revenue": housing_revenue,
            "housing_expense_allocated": housing_expense_allocated,
            "housing_noi": housing_noi,
            "housing_separate_eligible": housing_separate_eligible,
            "housing_taxable_general": housing_taxable_general,
            "housing_separate_tax_general": housing_separate_tax_general,
            "housing_taxable_registered": housing_taxable_registered,
            "housing_separate_tax_registered": housing_separate_tax_registered,
            "housing_comprehensive_estimate": comprehensive_housing_only_est,
            "commercial_revenue": commercial_revenue,
            "commercial_expense_allocated": commercial_expense_allocated,
            "commercial_noi": commercial_noi,
            "commercial_comprehensive_estimate": comprehensive_commercial_est,
            "comprehensive_combined_estimate": comprehensive_all_est,
            "estimated_comprehensive_tax": estimated_comprehensive_tax,
            "estimated_total_income_tax": estimated_total_income_tax,
            "commercial_tax_method": "상가·사무실 임대는 분리과세(15.4%) 대상이 아니며 실제 필요경비 차감 후 종합과세(6.6~49.5%) 대상입니다.",
            "vat_room_revenue": vat_room_revenue,
            "vat_collected": vat_collected,
            "raw_input_vat_total": raw_input_vat_total,
            "taxable_supply_ratio_pct": round(taxable_supply_ratio * 100, 1),
            "input_vat_deductible": input_vat_deductible,
            "input_vat_non_deductible_apportion": input_vat_non_deductible_apportion,
            "deductible_expense_total": deductible_expense_total,
            "non_deductible_expense_total": non_deductible_expense_total,
            "estimated_vat_payable": estimated_vat_payable,
            "total_operating_expense": total_expense,
            "noi": noi,
            "expense_ratio_pct": round((total_expense / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0,
            "estimated_separate_tax": estimated_separate_tax,
            "net_after_tax": noi - estimated_total_income_tax,
            "cap_rate_pct": cap_rate,
            "tax_disclaimer": cls.TAX_DISCLAIMER,
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
            "vat_room_breakdown": vat_room_breakdown,
            "vat_payment_rows": vat_payment_rows,
            "income_tax_room_breakdown": income_tax_room_breakdown,
            "income_tax_payment_rows": income_tax_payment_rows,
        }

    @classmethod
    def build_exempt_housing_report(cls, branch: str, year: Optional[int] = None) -> Dict[str, Any]:
        """매년 2월 10일 마감 면세사업자(주거용 주택임대) 사업장현황신고용 호실별 보증금·월세 수입금액 검토표."""
        b_id = normalize_branch_id(branch)
        yr = year or date_today().year
        yr_prefix = f"{yr:04d}"
        bldg = Building.first(branch=b_id)
        lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)
        rooms = sorted(Room.find(branch=b_id), key=lambda r: r.room_no)
        vat_rooms = set(bldg.clean_vat_rooms() if (bldg and bldg.vat_rooms) else [r.room_no for r in rooms if r.vat_applicable])
        active_ct_by_room = {c.room_no: c for c in Contract.find(branch=b_id) if c.status == "ACTIVE"}
        pays = [
            p for p in Pay.find(branch=b_id)
            if p.payment_kind == "MONTHLY_RENT"
            and not cls.is_carryover_payment(p)
            and cls.payment_tax_ym(p).startswith(yr_prefix)
        ]

        exempt_rooms = [r for r in rooms if r.room_no not in vat_rooms]
        rows: List[Dict[str, Any]] = []
        total_deposit = 0
        total_annual_rent = 0
        total_annual_maint = 0

        for r in exempt_rooms:
            ct = active_ct_by_room.get(r.room_no)
            r_pays = [p for p in pays if p.room_no == r.room_no]
            rent_sum = sum(p.rent_amount or max(0, p.total_amount - p.maintenance_amount - p.vat_amount) for p in r_pays)
            maint_sum = sum(p.maintenance_amount for p in r_pays)
            dep = ct.deposit if ct else (r.deposit if r.is_occupied else 0)
            m_rent = ct.monthly_rent if ct else r.monthly_rent
            total_deposit += dep
            total_annual_rent += rent_sum
            total_annual_maint += maint_sum
            rows.append({
                "room_no": r.room_no,
                "area_sqm": r.area_sqm,
                "is_small_housing": r.area_sqm <= 40.0,
                "tenant_name": ct.tenant_name if ct else ("(공실)" if not r.is_occupied else ""),
                "move_in_date": ct.move_in_date if ct else "",
                "expiry_date": ct.expiry_date if ct else "",
                "deposit": dep,
                "monthly_rent": m_rent,
                "payment_count": len(r_pays),
                "annual_rent_revenue": rent_sum,
                "annual_maintenance_revenue": maint_sum,
                "annual_total_excl_vat": rent_sum + maint_sum,
            })

        return {
            "branch": b_id,
            "year": yr,
            "filing_deadline": f"{yr + 1}-02-10",
            "building_name": bldg.building_name if bldg else b_id,
            "lessor_name": lessor.name if lessor else "임대인",
            "business_number": lessor.business_number if lessor else "",
            "exempt_room_count": len(exempt_rooms),
            "total_deposit": total_deposit,
            "total_annual_rent_revenue": total_annual_rent,
            "total_annual_maintenance_revenue": total_annual_maint,
            "deemed_rent_note": (
                "보증금 합계 3억 원 초과(부부합산 3주택 이상 기준, 전용 40㎡ 이하 소형주택은 특례 제외 가능) 시에만 간주임대료 계산 대상입니다. "
                "세무사·홈택스에서 간주임대료 해당 여부를 확인하세요."
            ),
            "tax_disclaimer": cls.TAX_DISCLAIMER,
            "rooms": rows,
        }

    @classmethod
    def export_exempt_housing_csv(cls, branch: str, year: Optional[int] = None) -> str:
        """매년 2월 10일 주택임대(면세사업자) 사업장현황신고용 수입금액 검토표 CSV."""
        rep = cls.build_exempt_housing_report(branch, year)
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow(["[주택임대(면세사업자) 사업장현황신고용 호실별 수입금액 검토표 — 참고용]"])
        writer.writerow(["귀속연도", rep["year"], "신고기한", rep["filing_deadline"], "건물명", rep["building_name"], "임대인", rep["lessor_name"]])
        writer.writerow(["주의사항", rep["tax_disclaimer"]])
        writer.writerow([])
        writer.writerow(["호실", "전용면적(㎡)", "임차인", "입주일", "만기일", "보증금(원)", "약정월세(원)", "수납건수", "연간월세수입(원)", "연간관리비수입(원)", "합계(원)"])
        for r in rep["rooms"]:
            writer.writerow([
                f"{r['room_no']}호",
                r["area_sqm"],
                r["tenant_name"],
                r["move_in_date"],
                r["expiry_date"],
                r["deposit"],
                r["monthly_rent"],
                r["payment_count"],
                r["annual_rent_revenue"],
                r["annual_maintenance_revenue"],
                r["annual_total_excl_vat"],
            ])
        writer.writerow([])
        writer.writerow(["합계", "", f"{rep['exempt_room_count']}개 호실", "", "", rep["total_deposit"], "", "", rep["total_annual_rent_revenue"], rep["total_annual_maintenance_revenue"], rep["total_annual_rent_revenue"] + rep["total_annual_maintenance_revenue"]])
        return buf.getvalue()

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
        writer.writerow(["[국세청 홈택스 제출용 연간 임대소득·필요경비 표준 명세서 (참고용 추정치 — 신고 전 세무사 확인)]"])
        writer.writerow(["과세연도", yr, "건물명", summary["building_name"], "임대인", summary["lessor_name"], "사업자번호", summary["business_number"]])
        writer.writerow([])
        writer.writerow(["구분", "금액(원)", "비고"])
        writer.writerow(["연간 총수입금액 (부가세 제외)", summary["gross_revenue"], f"약정월 귀속 수납 {summary['payment_count']}건 합계 (부가세 {summary['vat_collected']}원 제외)"])
        writer.writerow(["  - 주택임대 수입금액 (면세 호실)", summary["housing_revenue"], "연 2,000만원 이하 시 분리과세(필요경비율 50%/60%) 선택 가능"])
        writer.writerow(["  - 상가·사무실 임대 수입금액 (과세 호실)", summary["commercial_revenue"], "실제 필요경비 차감 후 종합과세(6.6~49.5%) 대상"])
        writer.writerow(["법정 필요경비 합계", summary["total_operating_expense"], f"지출 {summary['outlay_count']}건 합계"])
        writer.writerow(["순임대소득금액 (NOI, 부가세 제외)", summary["noi"], f"수익률 {summary['cap_rate_pct']}%"])
        comp_est = summary.get("comprehensive_combined_estimate") or {}
        if summary.get("housing_separate_eligible"):
            writer.writerow(["주택임대 분리과세 예상세액 (미등록 50% 경비율·15.4%)", summary["estimated_separate_tax"], "연 2,000만원 이하 분리과세 선택 시 참고용 추정치"])
            comm_est = summary.get("commercial_comprehensive_estimate") or {}
            writer.writerow(["상가임대 종합과세 예상세액 (누진세율·지방세 포함)", comm_est.get("total_tax_inc_local", 0), comm_est.get("bracket_label", "")])
        else:
            writer.writerow(["종합소득세 누진세율 예상세액 (주택+상가 합산·지방세 포함)", comp_est.get("total_tax_inc_local", 0), f"주택수입 2,000만원 초과 종합과세 대상 · {comp_est.get('bracket_label', '')}"])
        writer.writerow([])
        writer.writerow(["[8대 표준 공용 운영비 항목별 집계]"])
        writer.writerow(["코드", "항목명", "연간 지출액(원)", "안분후 공제매입세액(원)"])
        for item in summary["expenses_by_category"]:
            writer.writerow([item["category"], item["label"], item["amount"], item["input_vat"]])
        writer.writerow([])
        writer.writerow(["[세부 지출 증빙 원장]"])
        writer.writerow(["일자", "분류", "적요", "거래처", "거래처사업자번호", "증빙유형", "적용범위", "금액(원)", "부가세공제여부", "공제매입세액(원)"])
        ratio = (summary.get("taxable_supply_ratio_pct", 100.0) or 100.0) / 100.0
        for o in outlays:
            writer.writerow([
                o.expense_date,
                o.category_label,
                o.title,
                o.vendor,
                o.vendor_business_number,
                o.evidence_type,
                o.usage_scope,
                o.amount,
                "공제" if o.is_vat_deductible else "불공제(종소세만)",
                o.effective_input_vat(ratio),
            ])
        writer.writerow([])
        writer.writerow(["[호실별 임대수입 산출내역 (종합소득세 신고대상)]"])
        writer.writerow(["호실", "임차인", "수납건수", "월세합계(원)", "관리비합계(원)", "공급가액합계(원)", "실수납부가세(원)", "총수납액(원)"])
        for rb in summary.get("income_tax_room_breakdown", []):
            writer.writerow([
                f"{rb['room_no']}호",
                rb["tenant_name"] or "-",
                rb["count"],
                rb["rent_amount"],
                rb["maintenance_amount"],
                rb["supply_amount"],
                rb["vat_amount"],
                rb["total_amount"],
            ])
        writer.writerow([])
        writer.writerow(["[호실별 월세·관리비 납부 상세내역]"])
        writer.writerow(["수납일자", "귀속월", "호실", "입금자", "월세(원)", "관리비(원)", "공급가액(원)", "실수납부가세(원)", "총수납액(원)"])
        for pr in summary.get("income_tax_payment_rows", []):
            writer.writerow([
                pr["paid_date"],
                pr["billing_month"],
                f"{pr['room_no']}호",
                pr["payer_name"],
                pr["rent_amount"],
                pr["maintenance_amount"],
                pr["supply_amount"],
                pr["vat_amount"],
                pr["total_amount"],
            ])
        return buf.getvalue()

    @classmethod
    def build_tax_invoice_register(
        cls,
        branch: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> Dict[str, Any]:
        """청구월(약정 지급일) 기준 월 1건 세금계산서 발행 대장 및 발행 상태(DRAFT/ISSUED/MODIFIED/CANCELLED) 산출."""
        b_id = normalize_branch_id(branch)
        yr = year or date_today().year
        mo = month if month else None
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

        # 공급자(임대인) 사업자등록번호 검증
        sup_raw_biz = (lessor.business_number if lessor else "") or ""
        sup_biz_val = validate_kr_business_number(sup_raw_biz)
        supplier_biz_no = sup_biz_val["digits"]
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

        def _extract_buyer_info(ct: Optional[Contract], tn: Optional[Tenant]) -> Dict[str, Any]:
            biz_no_raw = (
                (ct.tenant_business_number if ct and ct.tenant_business_number else "")
                or (tn.business_number if tn and tn.business_number else "")
            ).strip()
            biz_val = validate_kr_business_number(biz_no_raw)
            comp_name = (
                (ct.tenant_company_name if ct and ct.tenant_company_name else "")
                or (tn.company_name if tn and tn.company_name else "")
            ).strip()
            # 대표자명이 비어 있을 때 임차인 개인 이름으로 자동 대체하지 않는다 (사업자 발행 오류 방지)
            ceo_name = (
                (ct.tenant_ceo_name if ct and ct.tenant_ceo_name else "")
                or (tn.ceo_name if tn and tn.ceo_name else "")
            ).strip()
            email = (
                (ct.tenant_email if ct and ct.tenant_email else "")
                or (tn.email if tn and tn.email else "")
            ).strip()
            custom_addr = (
                (ct.tenant_business_address if ct and ct.tenant_business_address else "")
                or (tn.business_address if tn and tn.business_address else "")
            ).strip()
            missing: List[str] = []
            if not biz_val["valid"]:
                missing.append("사업자등록번호" if not biz_no_raw else f"사업자등록번호({biz_val['reason']})")
            if not comp_name:
                missing.append("상호(법인명)")
            if not ceo_name:
                missing.append("대표자명")
            if not validate_email_format(email):
                missing.append("수신이메일" if not email else "수신이메일(형식오류)")
            if not sup_biz_val["valid"]:
                missing.append("공급자사업자번호")
            return {
                "biz_no_raw": biz_no_raw,
                "biz_no_digits": biz_val["digits"],
                "biz_checksum_ok": biz_val["checksum_ok"],
                "comp_name": comp_name,
                "ceo_name": ceo_name,
                "email": email,
                "custom_addr": custom_addr,
                "missing": missing,
            }

        vat_room_contracts: List[Dict[str, Any]] = []
        for r_no in candidate_room_nos:
            rm = room_by_no.get(r_no)
            ct = active_ct_by_room.get(r_no)
            tn = tenants_by_id.get(ct.id_tenant) if (ct and ct.id_tenant) else (
                tenants_by_id.get(rm.id_tenant) if (rm and rm.id_tenant) else None
            )
            buyer = _extract_buyer_info(ct, tn)
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
                "tax_invoice_requested": ct.tax_invoice_requested if ct else True,
                "business_number": buyer["biz_no_raw"],
                "business_number_checksum_ok": buyer["biz_checksum_ok"],
                "company_name": buyer["comp_name"],
                "ceo_name": buyer["ceo_name"],
                "email": buyer["email"],
                "business_address": buyer["custom_addr"],
                "effective_address": buyer["custom_addr"] or f"{supplier_addr} {r_no}호".strip(),
                "vat_target": ct.vat_target if ct else True,
                "vat_separate": ct.vat_separate if ct else True,
                "monthly_rent": m_rent,
                "maintenance_fee": m_maint,
                "monthly_vat": m_vat,
                "ready_for_invoice": len(buyer["missing"]) == 0,
                "missing_fields": buyer["missing"],
            })

        # 2) 입금 건마다 쪼개지 않고 (호실 × 청구월 YYYY-MM) 기준 월 1건으로 세금계산서 산출
        #    작성일자는 입금일(paid_date)이 아닌 해당 청구월의 약정 지급일(due_date_for_ym)로 고정한다.
        period_pays = [
            p for p in Pay.find(branch=b_id)
            if p.payment_kind == "MONTHLY_RENT"
            and not cls.is_carryover_payment(p)
            and cls.payment_tax_ym(p).startswith(ym_prefix)
        ]
        saved_records = {
            (rec.room_no, rec.billing_month): rec
            for rec in TaxInvoiceRecord.find(branch=b_id)
            if rec.billing_month.startswith(ym_prefix)
        }

        # (room_no, billing_month) 그룹핑
        grouped_pays: Dict[tuple[str, str], List[Pay]] = {}
        for p in period_pays:
            former = (p.id_contract or "").startswith("former:")      # 앞 입주자 시기 납부: 현재 입주자의 사업자 정보를 붙이지 않는다
            ct = ct_by_id.get(p.id_contract) or (None if former else active_ct_by_room.get(p.room_no))
            tn = tenants_by_id.get(p.id_tenant) or (tenants_by_id.get(ct.id_tenant) if ct else None)
            is_taxable = (
                bool(ct and (ct.tax_invoice_requested or ct.tenant_business_number or ct.vat_target))
                or bool(tn and tn.business_number)
                or (p.vat_amount > 0)
                or (p.room_no in vat_rooms)
            )
            if not is_taxable:
                continue
            ym = cls.payment_tax_ym(p)
            if not ym:
                continue
            grouped_pays.setdefault((p.room_no, ym), []).append(p)

        # 세금계산서 요청 계약 중 해당 월 약정 청구액이 있거나 발행이력이 있는 호실×월도 포함 (미수/청구 발행 지원)
        if mo:
            target_ym = f"{yr:04d}-{mo:02d}"
            for r_no in candidate_room_nos:
                ct = active_ct_by_room.get(r_no)
                if ct and ct.due_for_month(target_ym)["due"] > 0:
                    grouped_pays.setdefault((r_no, target_ym), [])
        for key_pair in saved_records:
            grouped_pays.setdefault(key_pair, [])

        invoices: List[Dict[str, Any]] = []
        for (r_no, ym) in sorted(grouped_pays.keys(), key=lambda k: (k[1], k[0])):
            ym_pays = grouped_pays[(r_no, ym)]
            rm = room_by_no.get(r_no)
            ct = (
                (ct_by_id.get(ym_pays[0].id_contract) if ym_pays else None)
                or (None if (ym_pays and (ym_pays[0].id_contract or "").startswith("former:")) else active_ct_by_room.get(r_no))
            )
            tn = (
                (tenants_by_id.get(ym_pays[0].id_tenant) if ym_pays else None)
                or (tenants_by_id.get(ct.id_tenant) if ct else None)
            )
            buyer = _extract_buyer_info(ct, tn)
            rec = saved_records.get((r_no, ym))

            # 작성일자 = 해당 청구월의 계약상 약정 지급일 (공급시기)
            due_dt = ct.due_date_for_ym(ym) if ct else None
            due_date_iso = due_dt.isoformat() if due_dt else f"{ym}-01"
            w_date = re.sub(r"\D", "", due_date_iso)
            mmdd = f"{w_date[4:6]}.{w_date[6:8]}" if len(w_date) == 8 else "01.01"

            # 월 1건 공급가액·세액 산출 (분할 입금이 여러 건이어도 해당 청구월 1건으로 합산)
            if rec and rec.supply_amount > 0:
                supply_amt = rec.supply_amount
                tax_amt = rec.tax_amount
            elif ct and (ct.monthly_rent + ct.maintenance_fee) > 0:
                base_contract_amt = ct.monthly_rent + ct.maintenance_fee
                if ct.vat_target and not ct.vat_separate:
                    supply_amt = round(base_contract_amt / 1.1)
                    tax_amt = base_contract_amt - supply_amt
                else:
                    supply_amt = base_contract_amt
                    tax_amt = ct.compute_vat() if ct.vat_target else round(supply_amt * 0.1)
            elif ym_pays:
                paid_total = sum(p.total_amount for p in ym_pays)
                paid_vat = sum(p.vat_amount for p in ym_pays)
                if paid_vat > 0:
                    supply_amt = paid_total - paid_vat
                    tax_amt = paid_vat
                else:
                    supply_amt = paid_total
                    tax_amt = round(supply_amt * 0.1)
            else:
                continue

            # 품목 문구: 호실 용도에 맞게 동적 구분 (사무실·상가 vs 기숙사·주거용)
            room_cat = (rm.room_category if rm else "") or ""
            is_office = ("상가" in room_cat) or ("사무실" in room_cat) or (r_no in vat_rooms)
            purpose_label = "사무실·상가 임대료 및 관리비" if is_office else "기숙사·주거용 임대료 및 관리비"
            item_name = f"{ym}월분 {purpose_label} ({r_no}호)"

            # 영수(01)/청구(02): 공급가액과 세액을 합친 합계금액(supply_amt + tax_amt) 이상 입금되었을 때만 영수(01), 미입금·일부입금은 청구(02)
            paid_sum = sum(p.total_amount for p in ym_pays)
            total_invoice_amt = supply_amt + tax_amt
            invoice_charge_code = "01" if (total_invoice_amt > 0 and paid_sum >= total_invoice_amt) else "02"
            last_paid_date = max((p.paid_date for p in ym_pays), default="")

            inv_status = rec.status if rec else "DRAFT"
            nts_no = rec.nts_confirm_no if rec else ""
            ready_for_hometax = len(buyer["missing"]) == 0
            fallback_addr = f"{supplier_addr} {r_no}호".strip()
            buyer_addr = buyer["custom_addr"] or fallback_addr

            invoices.append({
                "invoice_key": f"{b_id}:{r_no}:{ym}",
                "record_id": rec.id if rec else "",
                "payment_id": ym_pays[0].id if ym_pays else "",
                "payment_count": len(ym_pays),
                "paid_amount": paid_sum,
                "room_no": r_no,
                "room_category": room_cat or "주거용 원룸",
                "billing_month": ym,
                "due_date": due_date_iso,
                "paid_date": last_paid_date or due_date_iso,
                "write_date": w_date,
                "day_mmdd": mmdd,
                "buyer_business_number": buyer["biz_no_raw"],
                "buyer_business_number_digits": buyer["biz_no_digits"],
                "buyer_business_number_checksum_ok": buyer["biz_checksum_ok"],
                "buyer_company_name": buyer["comp_name"],
                "buyer_ceo_name": buyer["ceo_name"],
                "buyer_email": buyer["email"],
                "buyer_address": buyer_addr,
                "buyer_business_address_custom": buyer["custom_addr"],
                "buyer_address_is_fallback": not bool(buyer["custom_addr"]),
                "supply_amount": supply_amt,
                "tax_amount": tax_amt,
                "total_amount": total_invoice_amt,
                "item_name": item_name,
                "charge_type": invoice_charge_code,
                "charge_type_label": "영수(01)" if invoice_charge_code == "01" else "청구(02)",
                "status": inv_status,
                "nts_confirm_no": nts_no,
                "issued_at": rec.issued_at if rec else "",
                "history": rec.history if rec else [],
                "ready_for_hometax": ready_for_hometax,
                "exportable_to_csv": ready_for_hometax and inv_status not in ("ISSUED", "CANCELLED"),
                "missing_fields": buyer["missing"],
            })

        return {
            "branch": b_id,
            "year": yr,
            "month": mo,
            "building_name": bldg.building_name if bldg else b_id,
            "vat_rooms": vat_rooms,
            "income_tax_rooms": income_tax_rooms,
            "tax_disclaimer": cls.TAX_DISCLAIMER,
            "supplier": {
                "business_number": sup_raw_biz,
                "business_number_digits": supplier_biz_no,
                "business_number_valid": sup_biz_val["valid"],
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
                "exportable_count": sum(1 for i in invoices if i["exportable_to_csv"]),
                "issued_count": sum(1 for i in invoices if i["status"] == "ISSUED"),
                "incomplete_count": sum(1 for i in invoices if not i["ready_for_hometax"]),
            },
        }

    @classmethod
    def update_tax_invoice_status(
        cls,
        *,
        branch: str,
        room_no: str,
        billing_month: str,
        status: str,
        nts_confirm_no: str = "",
        memo: str = "",
        write_date: str = "",
        supply_amount: Optional[int] = None,
        tax_amount: Optional[int] = None,
        app_source: str = "rent3_lessor",
    ) -> TaxInvoiceRecord:
        """세금계산서 발행 완료·국세청 승인번호 기록·수정발행·취소 상태 및 이력 저장."""
        b_id = normalize_branch_id(branch)
        r_no = re.sub(r"[\"'`\[\]\s]|호$", "", (room_no or "").strip())
        ym = (billing_month or "").strip()[:7]
        if status not in ("DRAFT", "ISSUED", "MODIFIED", "CANCELLED"):
            raise DomainError("유효하지 않은 세금계산서 상태입니다 (DRAFT / ISSUED / MODIFIED / CANCELLED).")

        ct = Contract.first(branch=b_id, room_no=r_no, status="ACTIVE")
        due_dt = ct.due_date_for_ym(ym) if ct else None
        w_date = re.sub(r"\D", "", write_date or (due_dt.isoformat() if due_dt else f"{ym}-01"))

        rec = TaxInvoiceRecord.first(branch=b_id, room_no=r_no, billing_month=ym)
        if not rec:
            rec = TaxInvoiceRecord(
                id=f"tinv_{b_id}_{r_no}_{ym.replace('-', '')}",
                branch=b_id,
                id_contract=ct.id if ct else "",
                room_no=r_no,
                billing_month=ym,
                write_date=w_date,
            )

        prev_status = rec.status
        rec.status = status
        if nts_confirm_no.strip():
            rec.nts_confirm_no = nts_confirm_no.strip()
        if memo.strip():
            rec.memo = memo.strip()
        if supply_amount is not None:
            rec.supply_amount = supply_amount
            rec.tax_amount = tax_amount if tax_amount is not None else round(supply_amount * 0.1)
            rec.total_amount = rec.supply_amount + rec.tax_amount
        if ct:
            rec.buyer_business_number = ct.tenant_business_number
            rec.buyer_company_name = ct.tenant_company_name
            rec.buyer_ceo_name = ct.tenant_ceo_name
            rec.buyer_email = ct.tenant_email
        if status in ("ISSUED", "MODIFIED"):
            rec.issued_at = now_iso()

        rec.history = list(rec.history or []) + [{
            "at": now_iso(),
            "from_status": prev_status,
            "to_status": status,
            "nts_confirm_no": rec.nts_confirm_no,
            "memo": memo.strip(),
        }]
        return rec.save(action=f"tax_invoice_{status.lower()}", app_source=app_source)

    @classmethod
    def export_hometax_tax_invoice_csv(
        cls,
        branch: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
        include_issued: bool = False,
    ) -> str:
        """국세청 홈택스 전자세금계산서 일괄발급 참고용 CSV 생성.
        - 필수 정보(사업자번호·상호·대표자명·이메일)가 빠진 건(ready_for_hometax=False)은 절대 내보내지 않는다.
        - 이미 발행 완료(ISSUED) 또는 취소(CANCELLED)된 건도 이중 발행 방지를 위해 기본 제외한다.
        """
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
            # 정보가 빠진 건은 절대 CSV에 내보내지 않는다
            if not inv["ready_for_hometax"]:
                continue
            # 이미 발행 완료되었거나 취소된 건은 이중 발행 방지를 위해 제외한다
            if not include_issued and inv["status"] in ("ISSUED", "CANCELLED"):
                continue
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
                inv.get("charge_type", "01"),
            ])
        return buf.getvalue()

    @classmethod
    def build_tenant_tax_certificate(cls, contract_id: str, year: Optional[int] = None) -> Dict[str, Any]:
        """조세특례제한법 제95조의2 월세 세액공제(15%·17%) 참고용 납부확인서 데이터.
        - 가짜 현금영수증 승인번호 및 가짜 임대인 사업자번호(101-86-00000) 생성 원천 제거
        - 관리비·부가세를 제외한 순수 월세 납부액만 세액공제 대상(연 1,000만 원 한도)으로 산출
        - 총급여 5,500만 원 이하(17%) 및 5,500만~8,000만 원 이하(15%) 구간별 예상 공제액 분리 안내
        """
        ct = Contract.require(contract_id)
        yr = year or date_today().year
        yr_prefix = f"{yr:04d}"
        bldg = Building.get(ct.id_building)
        lessor = Lessor.get(ct.id_lessor) if ct.id_lessor else Lessor.first(branch=ct.branch)
        tenant = Tenant.get(ct.id_tenant) if ct.id_tenant else None

        pays = [
            p for p in Pay.find(id_contract=ct.id)
            if p.payment_kind == "MONTHLY_RENT"
            and not cls.is_carryover_payment(p)
            and cls.payment_tax_ym(p).startswith(yr_prefix)
        ]
        total_paid = sum(p.total_amount for p in pays)
        maintenance_paid = sum(p.maintenance_amount for p in pays)
        # 관리비·부가세를 뺀 순수 월세만 세액공제 대상
        rent_only_paid = sum(
            p.rent_amount if p.rent_amount > 0 else max(0, p.total_amount - p.maintenance_amount - p.vat_amount)
            for p in pays
        )
        deductible_base = min(rent_only_paid, 10_000_000)  # 연 1,000만원 한도 (관리비 제외 순수 월세 기준)
        estimated_refund_17pct = round(deductible_base * 0.17)  # 총급여 5,500만원 이하 (종합소득 4,500만원 이하)
        estimated_refund_15pct = round(deductible_base * 0.15)  # 총급여 5,500만~8,000만원 이하 (종합소득 7,000만원 이하)

        real_biz_no = (lessor.business_number if lessor and lessor.business_number else "").strip()
        real_cash_no = lambda v: "" if (not v or str(v).startswith("CASH-")) else str(v)

        return {
            "certificate_no": f"CERT-{yr}-{ct.id[-6:].upper()}",
            "tax_year": yr,
            "statute": "조세특례제한법 제95조의2 (월세액 세액공제 15%·17% — 관리비 제외 순수 월세 기준)",
            "guidance_notice": (
                "월세 세액공제는 임차인이 [임대차계약서 사본 + 주민등록등본 + 계좌이체 내역(무통장입금증 등)]으로 "
                "연말정산에 직접 신청하며, 임대인의 별도 법정 증명서 발급이나 현금영수증 발급이 필수는 아닙니다. "
                "본 문서는 연간 납부 내역 확인을 돕기 위한 참고용 자료입니다."
            ),
            "lessor": {
                "name": lessor.name if lessor else ct.lessor_name,
                "business_number": real_biz_no,
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
                "maintenance_fee": ct.maintenance_fee,
                "fixed_date_no": ct.fixed_date_no,
            },
            "payments": [
                {
                    "billing_month": p.billing_month,
                    "paid_date": p.paid_date,
                    "rent_amount": p.rent_amount if p.rent_amount > 0 else max(0, p.total_amount - p.maintenance_amount - p.vat_amount),
                    "maintenance_amount": p.maintenance_amount,
                    "amount": p.total_amount,
                    "cash_receipt_no": real_cash_no(p.cash_receipt_no),
                    "method": p.method,
                }
                for p in pays
            ],
            "total_paid": total_paid,
            "rent_only_paid": rent_only_paid,
            "maintenance_paid": maintenance_paid,
            "deductible_rent_base": deductible_base,
            "estimated_tax_credit_17pct": estimated_refund_17pct,
            "estimated_tax_credit_15pct": estimated_refund_15pct,
            "issued_at": date_today().isoformat(),
        }


