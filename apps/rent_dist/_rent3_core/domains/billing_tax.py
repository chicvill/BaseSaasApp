"""
apps/rent_dist/_rent3_core/domains/billing_tax.py — 지점 재무결산(NOI·부가세·종소세), 면세 사업장현황신고 및 연간 세무 CSV 믹스인.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from ..models import (
    Building,
    Contract,
    EXPENSE_CATEGORIES,
    Lessor,
    Outlay,
    Pay,
    Room,
    date_today,
    normalize_branch_id,
)


class TaxSummaryMixin:
    """공용 운영비 NOI 결산, 5월 종소세·2월 사업장현황신고·부가세 요약 및 CSV 내보내기."""

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
            if o.expense_date.startswith(yr_prefix) and not o.reference_only      # 관리비 산출근거용 참고 항목은 세무 경비에서 뺀다
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
            if o.expense_date.startswith(f"{yr:04d}") and not o.reference_only
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
