"""
apps/rent_dist/rent3_lessor/routers/tax.py — 세무 허브(부가세·종소세·세금계산서·현금영수증·면세신고·공용경비·관리비 산출근거).
"""
from datetime import date
import re
from typing import Any, Optional
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from core.errors import DomainError, Forbidden
from apps.rent_dist._rent3_core import (
    Building,
    Contract,
    EXPENSE_CATEGORIES,
    Outlay,
    Pay,
    Room,
    TaxAndCertificateService,
    Tenant,
    date_today,
    ensure_pool_seeded,
    normalize_branch_id,
)
from ._deps import APP_SRC, _parse_room_list

router = APIRouter()

# 관리비 산출근거에서 기본으로 빼는 항목(건물 운영비가 아니라 가맹비·접대성 경비)
MGMT_FEE_EXCLUDED = ("platform_fee", "congrat_condol")


class BuildingTaxRoomsReq(BaseModel):
    branch: str = "main"
    vat_rooms: Any = None
    income_tax_rooms: Any = None
    biz_company_name: Optional[str] = None
    biz_type: Optional[str] = None
    biz_item: Optional[str] = None
    sync_contracts: bool = False


class TenantTaxBizReq(BaseModel):
    branch: str = "main"
    room_no: str
    business_number: str = ""
    company_name: str = ""
    ceo_name: str = ""
    email: str = ""
    business_address: Optional[str] = None
    vat_target: Optional[bool] = None
    vat_separate: Optional[bool] = None
    tax_invoice_requested: Optional[bool] = None


class OutlayCreateReq(BaseModel):
    branch: str = "main"
    category: str = "cleaning"
    title: str
    amount: int
    vendor: str = ""
    vendor_business_number: str = ""
    vendor_tax_type: str = "GENERAL"  # GENERAL | SIMPLIFIED | EXEMPT
    expense_date: Optional[str] = None
    memo: str = ""
    vat_deductible: Optional[bool] = None
    evidence_type: str = "TAX_INVOICE"
    usage_scope: str = "COMMON"  # COMMON | TAXABLE_ONLY | EXEMPT_ONLY
    reference_only: bool = False  # 관리비 산출근거용 참고 항목: 세무 경비 집계에 넣지 않음


class OutlayEditReq(BaseModel):
    branch: str = "main"
    outlay_id: str
    category: str
    title: str
    amount: int
    vendor: str = ""
    vendor_business_number: Optional[str] = None
    vendor_tax_type: Optional[str] = None
    expense_date: str
    memo: str = ""
    vat_deductible: Optional[bool] = None
    evidence_type: Optional[str] = None
    usage_scope: Optional[str] = None


class OutlayDeleteReq(BaseModel):
    branch: str = "main"
    outlay_id: str


class TaxInvoiceStatusReq(BaseModel):
    branch: str = "main"
    room_no: str
    billing_month: str
    status: str = "ISSUED"  # DRAFT | ISSUED | MODIFIED | CANCELLED
    nts_confirm_no: str = ""
    memo: str = ""
    write_date: str = ""
    supply_amount: Optional[int] = None
    tax_amount: Optional[int] = None


class CashReceiptReq(BaseModel):
    branch: str = "main"
    room_no: str
    billing_month: str
    approval_no: str


@router.post("/building/tax-rooms")
def update_building_tax_rooms(req: BuildingTaxRoomsReq):
    """건물의 부가가치세 납부 대상 호실 리스트 및 종합소득세 신고 대상 호실 리스트를 저장하고 호실 속성에 동기화한다."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    bldg = Building.first(branch=b_id)
    if not bldg:
        raise DomainError("건물 정보를 찾을 수 없습니다.")
    if req.vat_rooms is not None:
        bldg.vat_rooms = _parse_room_list(req.vat_rooms)
    if req.income_tax_rooms is not None:
        bldg.income_tax_rooms = _parse_room_list(req.income_tax_rooms)
    if req.biz_company_name is not None:
        bldg.biz_company_name = req.biz_company_name.strip()
    if req.biz_type is not None and req.biz_type.strip():
        bldg.biz_type = req.biz_type.strip()
    if req.biz_item is not None and req.biz_item.strip():
        bldg.biz_item = req.biz_item.strip()
    bldg.save(action="building_tax_rooms_updated", app_source=APP_SRC)

    vat_set = set(bldg.vat_rooms or [])
    for r in Room.find(branch=b_id):
        should_vat = r.room_no in vat_set
        changed = False
        if r.vat_applicable != should_vat:
            r.vat_applicable = should_vat
            changed = True
        if should_vat and r.room_category == "주거용 원룸":
            r.room_category = "상가/사무실"
            changed = True
        if changed:
            r.save(action="room_vat_synced_from_building", app_source=APP_SRC)

    if req.sync_contracts:
        for ct in Contract.find(branch=b_id, status="ACTIVE"):
            should_vat = ct.room_no in vat_set
            if ct.vat_target != should_vat:
                ct.vat_target = should_vat
                ct.vat = ct.compute_vat()
                ct.save(action="contract_vat_synced_from_building", app_source=APP_SRC)

    return {
        "success": True,
        "message": (
            f"과세·종소세 대상 호실이 저장되었습니다 "
            f"(부가세 {len(bldg.vat_rooms)}개 호실 / 종소세 {len(bldg.income_tax_rooms)}개 호실)."
        ),
        "building": bldg.model_dump(),
    }


@router.post("/tax/tenant-biz")
def upsert_tenant_tax_biz(req: TenantTaxBizReq):
    """과세 호실 임차인의 세금계산서 발행용 사업자 정보(사업자등록번호, 상호, 대표자명, 이메일) 입력·수정."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    r_no = req.room_no.strip().replace("호", "")
    room = Room.first(branch=b_id, room_no=r_no)
    if not room:
        raise DomainError(f"{b_id} 지점에서 '{r_no}호'를 찾을 수 없습니다.")

    ct = Contract.get(room.active_contract_id) if room.active_contract_id else Contract.first(branch=b_id, room_no=r_no, status="ACTIVE")
    tenant = (
        (Tenant.get(ct.id_tenant) if (ct and ct.id_tenant) else None)
        or (Tenant.get(room.id_tenant) if room.id_tenant else None)
        or Tenant.first(branch=b_id, room_no=r_no)
    )

    biz_no = req.business_number.strip()
    comp_name = req.company_name.strip()
    ceo_name = req.ceo_name.strip()
    email = req.email.strip()
    biz_addr = req.business_address.strip() if req.business_address is not None else None

    if ct:
        ct.tenant_business_number = biz_no
        ct.tenant_company_name = comp_name
        ct.tenant_ceo_name = ceo_name
        ct.tenant_email = email
        if biz_addr is not None:
            ct.tenant_business_address = biz_addr
        ct.tax_invoice_requested = req.tax_invoice_requested if req.tax_invoice_requested is not None else bool(biz_no or comp_name or req.vat_target)
        if req.vat_target is not None:
            ct.vat_target = req.vat_target
        elif ct.tax_invoice_requested:
            ct.vat_target = True
        if req.vat_separate is not None:
            ct.vat_separate = req.vat_separate
        ct.vat = ct.compute_vat()
        ct.save(action="contract_tenant_biz_updated", app_source=APP_SRC)

    if tenant:
        tenant.business_number = biz_no
        tenant.company_name = comp_name
        tenant.ceo_name = ceo_name
        if email:
            tenant.email = email
        if biz_addr is not None:
            tenant.business_address = biz_addr
        tenant.save(action="tenant_biz_updated", app_source=APP_SRC)
    elif ct:
        tenant = Tenant(
            name=ct.tenant_name or ceo_name or f"{r_no}호 임차인",
            phone=ct.tenant_phone,
            email=email,
            business_number=biz_no,
            company_name=comp_name,
            ceo_name=ceo_name,
            branch=b_id,
            id_building=room.id_building,
            current_room_id=room.id,
            room_no=r_no,
            status="ACTIVE",
        ).save(action="tenant_created_for_tax_biz", app_source=APP_SRC)
        ct.id_tenant = tenant.id
        ct.save(action="contract_linked_tenant_biz", app_source=APP_SRC)
    else:
        tenant = Tenant(
            name=ceo_name or comp_name or f"{r_no}호 임차사업자",
            email=email,
            business_number=biz_no,
            company_name=comp_name,
            ceo_name=ceo_name,
            branch=b_id,
            id_building=room.id_building,
            current_room_id=room.id,
            room_no=r_no,
            status="ACTIVE",
        ).save(action="tenant_created_for_tax_biz", app_source=APP_SRC)
        room.id_tenant = tenant.id

    if req.vat_target is not None:
        room.vat_applicable = req.vat_target
        room.save(action="room_vat_updated", app_source=APP_SRC)

    return {
        "success": True,
        "message": f"{r_no}호 세금계산서 사업자 정보({comp_name or biz_no})가 저장되었습니다.",
        "contract": ct.model_dump() if ct else None,
        "tenant": tenant.safe_dump() if tenant else None,
    }


@router.post("/outlays")
def create_outlay(req: OutlayCreateReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    bldg = Building.first(branch=b_id)
    item = Outlay(
        branch=b_id,
        id_building=bldg.id if bldg else "",
        category=req.category,
        title=req.title,
        amount=req.amount,
        vendor=req.vendor,
        vendor_business_number=req.vendor_business_number.strip(),
        vendor_tax_type=(req.vendor_tax_type or "GENERAL").strip().upper(),
        memo=req.memo,
        vat_deductible=req.vat_deductible,
        evidence_type=req.evidence_type or "TAX_INVOICE",
        usage_scope=(req.usage_scope or "COMMON").strip().upper(),
        reference_only=bool(req.reference_only),
        **({"expense_date": req.expense_date} if req.expense_date else {}),
    ).save(action="outlay_recorded", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"공용 운영비 '{item.title}' ({item.amount:,}원)가 등록되었습니다.",
        "outlay": {
            **item.model_dump(),
            "category_label": item.category_label,
            "is_vat_deductible": item.is_vat_deductible,
            "input_vat": item.input_vat,
            "supply_amount": item.supply_amount,
        },
    }


@router.get("/mgmt-fee/basis")
def mgmt_fee_basis(branch: str = "main", year: Optional[int] = None, exclude: str = ""):
    """건물별 관리비 산출근거: 연간 공용 비용(감가상각·수리·공동 전기·수도·승강기·청소 등) 항목별 합계 ÷ 종합소득세 적용 호실 수 = 호실당 금액.
    exclude(쉼표로 구분한 항목 코드)로 뺀 항목은 합계에 넣지 않는다. 기본은 가맹비·경조사비만 뺀다."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    yr = year or date_today().year
    bldg = Building.first(branch=b_id)
    excluded = {x.strip() for x in (exclude or "").split(",") if x.strip()} if exclude else set(MGMT_FEE_EXCLUDED)
    outs = [o for o in Outlay.find(branch=b_id) if o.expense_date.startswith(f"{yr:04d}")]
    items: list[dict[str, Any]] = []
    for code, label in EXPENSE_CATEGORIES.items():
        rows = [o for o in outs if o.category == code]
        items.append({"category": code, "label": label, "amount": sum(int(o.amount or 0) for o in rows), "count": len(rows), "included": code not in excluded})
    total = sum(int(i["amount"]) for i in items if i["included"])
    all_rooms = [r for r in (bldg.rooms() if bldg else [])]
    tax_rooms = [r for r in all_rooms if bldg and bldg.is_income_tax_room(r.room_no)]
    n = len(tax_rooms)
    per_year = round(total / n) if n else 0
    fees = [int(r.maintenance_fee or 0) for r in tax_rooms]
    return {
        "success": True,
        "year": yr,
        "items": items,
        "total": total,
        "rooms": [r.room_no for r in tax_rooms],
        "room_count": n,
        "all_room_count": len(all_rooms),
        "tax_rooms_configured": bool(bldg and bldg.clean_income_tax_rooms()),
        "per_room_year": per_year,
        "per_room_month": int(round(per_year / 12)),
        "charged_fee_avg": int(round(sum(fees) / len(fees))) if fees else 0,
        "note": "계산식: 포함한 항목의 연 합계 ÷ 종합소득세 적용 호실 수 = 호실당 연 금액, 그것을 12로 나누면 월 금액입니다. 참고용 자료이며 세무 신고는 세무사와 확인하세요.",
    }


@router.get("/tax/summary")
def tax_summary(branch: str = "main", year: Optional[int] = None, month: Optional[int] = None):
    """부가세·종합소득세·면세사업장현황신고 자료, 세금계산서 발행 대장 및 경비 지출 목록 (더보기 ▸ 부가세·종소세·경비)."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    yr = year or date_today().year
    fin = TaxAndCertificateService.branch_financial_summary(b_id, yr)
    ratio = float(fin.get("taxable_supply_ratio", 1.0))
    bldg = Building.first(branch=b_id)
    pays = [
        p
        for p in Pay.find(branch=b_id)
        if p.payment_kind == "MONTHLY_RENT"
        and not TaxAndCertificateService.is_carryover_payment(p)
        and TaxAndCertificateService.payment_tax_ym(p).startswith(f"{yr:04d}")
    ]
    outlays = sorted(
        (o for o in Outlay.find(branch=b_id) if o.expense_date.startswith(f"{yr:04d}") and not o.reference_only),
        key=lambda o: o.expense_date,
        reverse=True,
    )
    periods = []
    for label, months in (("1기 (1~6월)", range(1, 7)), ("2기 (7~12월)", range(7, 13))):
        ps = [p for p in pays if int(TaxAndCertificateService.payment_tax_ym(p)[5:7]) in months]
        os_period = [o for o in outlays if int(o.expense_date[5:7]) in months]
        out_vat = sum(p.vat_amount for p in ps)
        in_vat = sum(o.effective_input_vat(ratio) for o in os_period)
        ded_exp = sum(o.amount for o in os_period if o.is_vat_deductible)
        non_ded_exp = sum(o.amount for o in os_period if not o.is_vat_deductible)
        periods.append({
            "label": label,
            "supply": sum(max(0, p.total_amount - p.vat_amount) for p in ps),
            "vat": out_vat,
            "output_vat": out_vat,
            "input_vat": in_vat,
            "deductible_expense": ded_exp,
            "non_deductible_expense": non_ded_exp,
            "net_vat_payable": out_vat - in_vat,
            "taxable_count": sum(1 for p in ps if p.vat_amount > 0),
        })
    invoice_reg = TaxAndCertificateService.build_tax_invoice_register(b_id, year=yr, month=month)
    exempt_report = TaxAndCertificateService.build_exempt_housing_report(b_id, year=yr)
    return {
        "success": True,
        "year": yr,
        "month": month,
        "tax_disclaimer": TaxAndCertificateService.TAX_DISCLAIMER,
        "summary": fin,
        "building_tax_config": {
            "building_name": bldg.building_name if bldg else b_id,
            "biz_company_name": (bldg.biz_company_name if bldg and bldg.biz_company_name else (bldg.building_name if bldg else b_id)),
            "biz_type": bldg.biz_type if bldg else "부동산업",
            "biz_item": bldg.biz_item if bldg else "비주거용 건물 임대업",
            "vat_rooms": bldg.clean_vat_rooms() if bldg else [],
            "income_tax_rooms": bldg.clean_income_tax_rooms() if bldg else [],
        },
        "vat_periods": periods,
        "tax_invoice_register": invoice_reg,
        "exempt_housing_report": exempt_report,
        "outlays": [
            {
                **o.model_dump(),
                "category_label": o.category_label,
                "is_vat_deductible": o.is_vat_deductible,
                "input_vat": o.input_vat,
                "effective_input_vat": o.effective_input_vat(ratio),
                "supply_amount": o.supply_amount,
            }
            for o in outlays
        ],
        "expense_categories": EXPENSE_CATEGORIES,
    }


@router.post("/outlays/edit")
def edit_outlay(req: OutlayEditReq):
    ensure_pool_seeded()
    o = Outlay.require(req.outlay_id)
    if normalize_branch_id(o.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 지출은 고칠 수 없습니다.")
    if req.category not in EXPENSE_CATEGORIES:
        raise DomainError("분류가 올바르지 않습니다.")
    if req.amount <= 0:
        raise DomainError("금액은 0원보다 커야 합니다.")
    try:
        date.fromisoformat(req.expense_date)
    except ValueError:
        raise DomainError("지출일 형식이 올바르지 않습니다 (예: 2026-03-15).")
    o.category, o.title, o.amount = req.category, req.title.strip() or o.title, req.amount
    o.vendor, o.expense_date, o.memo = req.vendor.strip(), req.expense_date, req.memo.strip()
    if req.vendor_business_number is not None:
        o.vendor_business_number = req.vendor_business_number.strip()
    if req.vendor_tax_type is not None:
        o.vendor_tax_type = req.vendor_tax_type.strip().upper()
    if req.vat_deductible is not None:
        o.vat_deductible = req.vat_deductible
    if req.evidence_type is not None:
        o.evidence_type = req.evidence_type
    if req.usage_scope is not None:
        o.usage_scope = req.usage_scope.strip().upper()
    o.save(action="outlay_edited", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"지출 '{o.title}'을 고쳤습니다.",
        "outlay": {
            **o.model_dump(),
            "category_label": o.category_label,
            "is_vat_deductible": o.is_vat_deductible,
            "input_vat": o.input_vat,
            "supply_amount": o.supply_amount,
        },
    }


@router.post("/outlays/delete")
def delete_outlay(req: OutlayDeleteReq):
    ensure_pool_seeded()
    o = Outlay.require(req.outlay_id)
    if normalize_branch_id(o.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 지출은 지울 수 없습니다.")
    title = o.title
    o.delete(action="outlay_deleted", app_source=APP_SRC)
    return {"success": True, "message": f"지출 '{title}'을 지웠습니다."}


@router.post("/tax/cash-receipt")
def record_rent_cash_receipt(req: CashReceiptReq):
    """홈택스에서 발급한 월세 현금영수증 승인번호를 그 호실·청구월의 납부 건에 기록한다(임차인 연말정산 납부확인서에 표시)."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    no = re.sub(r"\D", "", req.approval_no or "")
    if not 6 <= len(no) <= 12:
        raise DomainError("현금영수증 승인번호는 숫자 6~12자리로 입력해 주세요.")
    pays = [p for p in Pay.find(branch=b_id, room_no=req.room_no) if p.payment_kind == "MONTHLY_RENT" and TaxAndCertificateService.payment_tax_ym(p) == req.billing_month]
    if not pays:
        raise DomainError(f"{req.room_no}호 {req.billing_month} 월세 납부 내역이 없어 기록할 수 없습니다.")
    for p in pays:
        p.cash_receipt_no = no
        p.save(action="rent_cash_receipt_recorded", app_source=APP_SRC)
    return {"success": True, "message": f"{req.room_no}호 {req.billing_month} 월세 {len(pays)}건에 현금영수증 승인번호({no})를 기록했습니다."}


@router.post("/tax/invoices/status")
def update_tax_invoice_status(req: TaxInvoiceStatusReq):
    """월별 전자세금계산서 발행 완료·국세청 승인번호·수정·취소 이력 저장."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    rec = TaxAndCertificateService.update_tax_invoice_status(
        branch=b_id,
        room_no=req.room_no,
        billing_month=req.billing_month,
        status=req.status,
        nts_confirm_no=req.nts_confirm_no,
        memo=req.memo,
        write_date=req.write_date,
        supply_amount=req.supply_amount,
        tax_amount=req.tax_amount,
        app_source=APP_SRC,
    )
    status_ko = {
        "DRAFT": "미발행(작성대기)",
        "ISSUED": "발행완료",
        "MODIFIED": "수정발행",
        "CANCELLED": "발행취소",
    }.get(rec.status, rec.status)
    return {
        "success": True,
        "message": f"{rec.room_no}호 {rec.billing_month} 귀속 세금계산서 상태가 '{status_ko}'로 저장되었습니다.",
        "record": rec.model_dump(),
    }


@router.get("/tax/export-csv")
def export_tax_csv(branch: str = "main", year: Optional[int] = None):
    ensure_pool_seeded()
    yr = year or date_today().year
    csv_body = TaxAndCertificateService.export_annual_tax_csv(branch=branch, year=yr)
    b_id = normalize_branch_id(branch)
    filename = f"rent3_tax_statement_{b_id}_{yr}.csv"
    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tax/export-exempt-csv")
def export_exempt_housing_csv(branch: str = "main", year: Optional[int] = None):
    """주거용(면세) 주택임대 사업장현황신고(매년 2월 10일) 호실별 수입금액 명세 CSV 다운로드."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    yr = year or date_today().year
    csv_body = TaxAndCertificateService.export_exempt_housing_csv(branch=b_id, year=yr)
    filename = f"exempt_housing_status_{b_id}_{yr}.csv"
    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tax/invoice-register")
def get_tax_invoice_register(branch: str = "main", year: Optional[int] = None, month: Optional[int] = None):
    """과세 호실 임차인 사업자 현황 및 월별/연간 세금계산서 발행 대장 조회."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    reg = TaxAndCertificateService.build_tax_invoice_register(branch=b_id, year=year, month=month)
    return {"success": True, **reg}


@router.get("/tax/export-hometax-csv")
def export_hometax_tax_invoice_csv(branch: str = "main", year: Optional[int] = None, month: Optional[int] = None):
    """국세청 홈택스 전자세금계산서 일괄발급 포맷(CSV) 다운로드."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    yr = year or date_today().year
    csv_body = TaxAndCertificateService.export_hometax_tax_invoice_csv(branch=b_id, year=yr, month=month)
    mo_suffix = f"_{month:02d}" if month else ""
    filename = f"hometax_tax_invoice_{b_id}_{yr}{mo_suffix}.csv"
    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
