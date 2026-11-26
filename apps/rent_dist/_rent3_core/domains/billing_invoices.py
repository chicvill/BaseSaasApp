"""
apps/rent_dist/_rent3_core/domains/billing_invoices.py — 세금계산서 발행대장·홈택스 일괄발급 CSV·임차인 세액공제 증명서 믹스인.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

from core.errors import DomainError
from ..models import (
    Building,
    Contract,
    Lessor,
    Pay,
    Room,
    TaxInvoiceRecord,
    Tenant,
    date_today,
    normalize_branch_id,
    validate_email_format,
    validate_kr_business_number,
)
from ..pool import now_iso
from .billing_tax import TaxSummaryMixin


class TaxInvoiceAndCertMixin(TaxSummaryMixin):
    """전자세금계산서 대장·상태 갱신·홈택스 CSV 및 임차인 월세 세액공제 납부확인서 생성."""

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
                "tenant_phone": (ct.tenant_phone if ct and ct.tenant_phone else (tn.phone if tn else "")),
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
