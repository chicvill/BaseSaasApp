"""
apps/rent_dist/rent3_lessor/routers/franchise.py — 임대인 가맹 신청(본인인증·건물·등기부·사업자등록증·제출) · 상태 · 가맹비 증빙.
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Building,
    date_today,
    ensure_pool_seeded,
    franchise as fr,
    normalize_branch_id,
    pool,
    registry_ai,
    validate_email_format,
)
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import APP_SRC, _franchise_info, _is_loopback, _my_lessor, get_franchise_ai_max_calls

router = APIRouter()


class IdentityStartReq(BaseModel):
    branch: str = "main"
    name: str
    phone: str
    birth: str


class IdentityConfirmReq(IdentityStartReq):
    code: str


class FranchiseBuildingReq(BaseModel):
    branch: str = "main"
    building_name: str = ""
    address: str = ""
    property_value: int = 0


class FranchiseRegistryReq(BaseModel):
    branch: str = "main"
    attachment_data_url: str
    consent: bool = False


class FranchiseBizRegReq(BaseModel):
    branch: str = "main"
    attachment_data_url: str
    email: str = ""                  # 세금계산서·현금영수증 수신 이메일(필수에 가깝다 — 비면 발급 정보 카드가 경고한다)
    business_type: str = ""          # 업태
    business_item: str = ""          # 종목
    business_number: str = ""
    representative_name: str = ""
    company_name: str = ""
    opening_date: str = ""
    consent: bool = False


class FranchiseSubmitReq(BaseModel):
    branch: str = "main"


@router.get("/franchise/application")
def franchise_application(request: Request, branch: str = "main"):
    lsr = _my_lessor(branch)
    return {
        "success": True,
        "franchise": _franchise_info(lsr.branch),
        "application": fr.application_summary(lsr, Building.first(branch=lsr.branch)),
        "identity_provider": fr.provider(_is_loopback(request)).name,
    }


@router.post("/franchise/building")
def franchise_building(req: FranchiseBuildingReq):
    """임대인이 자기 건물의 이름·실제 주소·건물 가치를 입력한다 (등기부 대조의 기준)."""
    lsr = _my_lessor(req.branch)
    b = Building.first(branch=lsr.branch)
    if b is None:
        raise DomainError("건물 정보를 찾을 수 없습니다.")
    if req.building_name.strip():
        b.building_name = req.building_name.strip()
    if req.address.strip():
        b.address = req.address.strip()
    if req.property_value < 0:
        raise DomainError("건물 가치는 0 이상이어야 합니다.")
    if req.property_value:
        b.property_value = req.property_value
    b.save(action="franchise_building_updated", app_source=APP_SRC)
    return {"success": True, "message": "건물 정보를 저장했습니다.", "application": fr.application_summary(lsr, b)}


@router.post("/franchise/identity/start")
def franchise_identity_start(req: IdentityStartReq, request: Request):
    _my_lessor(req.branch)
    p = fr.provider(_is_loopback(request))
    out = p.start(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth))
    return {"success": True, "message": "인증번호를 보냈습니다.", "provider": p.name, **out}


@router.post("/franchise/identity/confirm")
def franchise_identity_confirm(req: IdentityConfirmReq, request: Request):
    lsr = _my_lessor(req.branch)
    p = fr.provider(_is_loopback(request))
    res = p.confirm(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth), req.code)
    lsr.identity = fr.verify_identity_record(res)
    # 본인인증이 바뀌면 이전 대조 결과는 무효 → 다시 대조
    if lsr.franchise_docs.get("ai"):
        ai = lsr.franchise_docs["ai"]
        b0 = Building.first(branch=lsr.branch)
        lsr.franchise_docs = {
            **lsr.franchise_docs,
            "check": fr.check_ownership(lsr.identity, ai.get("extracted"), b0.address if b0 else ""),
        }
    lsr.applied_at = ""  # 신원이 바뀌었으니 제출은 다시
    lsr.save(action="franchise_identity_verified", app_source=APP_SRC)
    return {
        "success": True,
        "message": "본인인증이 완료되었습니다." + (" (시연용 가짜 인증)" if res.get("fake") else ""),
        "application": fr.application_summary(lsr, Building.first(branch=lsr.branch)),
    }


@router.post("/franchise/registry")
def franchise_registry(req: FranchiseRegistryReq):
    """등기부를 올리면 보관하고(심사용), 동의 시 Gemini 로 읽어 본인인증 값과 대조한다."""
    lsr = _my_lessor(req.branch)
    if not lsr.identity.get("verified"):
        raise DomainError("먼저 휴대폰 본인인증을 해 주세요.")
    bldg = Building.first(branch=lsr.branch)
    saved = fr.save_registry_file(lsr.id, req.attachment_data_url)
    ai: Dict[str, Any] = {"status": "skipped"}
    max_calls = get_franchise_ai_max_calls()
    if req.consent:
        if lsr.franchise_ai_calls >= max_calls:
            raise DomainError(
                f"가맹 신청 중 AI 읽기 횟수({max_calls}회)를 모두 썼습니다.",
                hint="AI 없이 제출하면 관리자가 원본을 직접 확인합니다.",
            )
        binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
        ai = registry_ai.analyze(
            binary,
            mime,
            lessor_names=[lsr.identity.get("name", "")],
            deposit=0,
            property_value=int(getattr(bldg, "property_value", 0) or 0),
        )
        if ai["status"] == "ok":
            lsr.franchise_ai_calls += 1
    check = fr.check_ownership(lsr.identity, (ai or {}).get("extracted"), getattr(bldg, "address", ""))
    lsr.franchise_docs = {
        **(lsr.franchise_docs or {}),
        "registry_file": saved["file"],
        "registry_sha256": saved["sha256"],
        "registry_mime": saved["mime"],
        "uploaded_at": now_iso(),
        "ai": {"status": ai["status"], "message": ai.get("message", ""), "extracted": ai.get("extracted")},
        "check": check,
        "admin_verification": None,
    }
    lsr.applied_at = ""
    lsr.save(action="franchise_registry_uploaded", app_source=APP_SRC)
    return {
        "success": True,
        "message": check["message"],
        "level": "success" if check["auto_pass"] else "warning",
        "application": fr.application_summary(lsr, bldg),
    }


@router.post("/franchise/bizreg")
def franchise_bizreg(req: FranchiseBizRegReq):
    """임대인이 촬영·선택한 사업자등록증을 보관하고, 동의 시 Gemini 로 읽어 국세청 10자리 체크섬·대표자명·소재지를 대조한다.
    최종 반영(lessor.business_number 확정)은 관리인이 국세청 홈택스에서 사업자 상태를 확인하고 승인할 때 이루어진다."""
    lsr = _my_lessor(req.branch)
    bldg = Building.first(branch=lsr.branch)
    saved = fr.save_bizreg_file(lsr.id, req.attachment_data_url)
    ai: Dict[str, Any] = {"status": "skipped"}
    max_calls = get_franchise_ai_max_calls()
    if req.consent:
        if lsr.franchise_ai_calls >= max_calls * 2:
            raise DomainError(
                "AI 읽기 횟수 한도를 초과했습니다.",
                hint="사업자등록번호를 직접 입력해 제출하면 관리자가 원본을 확인합니다.",
            )
        binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
        ai = registry_ai.analyze_bizreg(binary, mime)
        if ai["status"] == "ok":
            lsr.franchise_ai_calls += 1
    check = fr.check_bizreg(
        lsr,
        (ai or {}).get("extracted"),
        manual_biz_no=req.business_number,
        manual_rep_name=req.representative_name,
        manual_company=req.company_name,
        manual_opening_date=req.opening_date,
        building_address=getattr(bldg, "address", "") if bldg else "",
    )
    ex = (ai or {}).get("extracted") or {}
    email = (req.email or "").strip()
    if email:
        if not validate_email_format(email):
            raise DomainError(
                "이메일 형식이 올바르지 않습니다.",
                hint="세금계산서를 받을 이메일을 정확히 입력해 주세요(예: name@example.com).",
            )
        lsr.email = email
    btype = (req.business_type or ex.get("business_type") or "").strip()
    bitem = (req.business_item or ex.get("business_item") or "").strip()
    if bldg is not None and (btype or bitem):  # 공급자 업태·종목(세금계산서 공급자 정보)
        if btype:
            bldg.biz_type = btype
        if bitem:
            bldg.biz_item = bitem
        bldg.save(action="franchise_bizreg_biz_type", app_source=APP_SRC)
    docs = dict(lsr.franchise_docs or {})
    docs["bizreg"] = {
        "file": saved["file"],
        "sha256": saved["sha256"],
        "mime": saved["mime"],
        "uploaded_at": now_iso(),
        "ai": {"status": ai["status"], "message": ai.get("message", ""), "extracted": ai.get("extracted")},
        "check": check,
        "admin_verification": None,
    }
    lsr.franchise_docs = docs
    lsr.save(action="franchise_bizreg_uploaded", app_source=APP_SRC)
    from apps.rent_dist._rent3_core import support as sup_chat
    sup_chat.post_user_message(
        lsr,
        f"[📷 사업자등록증 제출] 사업자등록증({check.get('business_number') or '번호 미입력'})을 제출했습니다. 국세청 홈택스에서 사업자 상태를 확인한 뒤 승인해 주세요.",
    )
    return {
        "success": True,
        "message": check["message"],
        "level": "success" if check["auto_pass"] else "warning",
        "application": fr.application_summary(lsr, bldg),
    }


@router.post("/franchise/submit")
def franchise_submit(req: FranchiseSubmitReq):
    lsr = _my_lessor(req.branch)
    summ = fr.application_summary(lsr, Building.first(branch=lsr.branch))
    if not summ["ready_to_submit"]:
        missing = [
            k
            for k, v in (
                ("본인인증", summ["steps"]["identity"]),
                ("건물 주소(번지 포함)", summ["steps"]["building"]),
                ("등기부", summ["steps"]["registry"]),
            )
            if not v
        ]
        raise DomainError(f"아직 입력하지 않은 항목이 있습니다: {', '.join(missing)}")
    if lsr.status not in ("PENDING_APPROVAL", "REJECTED"):
        raise DomainError("이미 심사가 끝났거나 이용이 정지된 계정입니다.")
    lsr.status = "PENDING_APPROVAL"
    lsr.status_note = ""
    lsr.applied_at = now_iso()
    lsr.save(action="franchise_applied", app_source=APP_SRC)
    return {
        "success": True,
        "message": "가맹 신청이 제출되었습니다. 본사 심사 후 결과를 알려 드립니다.",
        "application": fr.application_summary(lsr, Building.first(branch=lsr.branch)),
    }


@router.get("/status")
def get_status(branch: str = "main"):
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    bldg = Building.first(branch=b_id)
    return {
        "success": True,
        "app_id": "rent3_lessor",
        "status": "HEALTHY",
        "branch": b_id,
        "building_name": bldg.building_name if bldg else b_id,
        "franchise": _franchise_info(b_id),
        "pool_revision": pool().overview()["total_revision"],
    }


@router.get("/franchise-fees")
def my_franchise_fees(branch: str = "main", year: Optional[int] = None):
    """내 가맹비 청구·납부·세금계산서 내역과 연도별 종합소득세 증빙 요약. 조회할 때 이번 달 청구가 없으면 자동으로 만들어 통보한다."""
    from apps.rent_dist._rent3_core import feebilling as fb
    ensure_pool_seeded()
    lsr = _my_lessor(branch)
    fb.ensure_notices(branch=lsr.branch)
    yr = year or date_today().year
    fees = sorted(fb.FranchiseFee.find(id_lessor=lsr.id), key=lambda f: f.billing_month, reverse=True)
    return {
        "success": True,
        "monthly_fee": lsr.app_fee,
        "business_number": lsr.business_number,
        "platform": fb.platform_info(),
        "fees": [fb.public(f) for f in fees if f.status != "CANCELLED"],
        "evidence": fb.evidence(lsr.id, yr),
    }


@router.get("/franchise-fees/export-csv")
def export_franchise_fees_csv(branch: str = "main", year: Optional[int] = None):
    """종합소득세 증빙용 가맹비 납부·세금계산서 내역 CSV(발행분과 발행 대기분을 구분)."""
    from apps.rent_dist._rent3_core import feebilling as fb
    ensure_pool_seeded()
    lsr = _my_lessor(branch)
    yr = year or date_today().year
    return Response(
        content=fb.evidence_csv(lsr, yr).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="franchise_fee_evidence_{lsr.branch}_{yr}.csv"'},
    )
