"""
apps/rent_dist/rent3_admin/routers/lessors.py — 상태 · 통합 개요 · 임대인 가맹 등재/심사/등기부·홈택스 대조/한도 관리.
"""
import re
from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.auth import require_role
from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Admin,
    Building,
    Contract,
    Lessor,
    Room,
    RtmsReport,
    TaxAndCertificateService,
    Tenant,
    ensure_pool_seeded,
    franchise as fr,
    normalize_branch_id,
    pool,
    support as sup,
)
from apps.rent_dist._rent3_core.authz import not_revoked
from apps.rent_dist._rent3_core.billing import validate_kr_business_number
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import ADMIN_ONLY, APP_SRC

router = APIRouter()


class DecisionReq(BaseModel):
    decision: str                 # approve | reject | suspend
    note: str = ""
    force: bool = False           # 신청 서류가 미비해도 관리자 책임으로 승인(사유 필수·기록됨)


class RegistryVerifyReq(BaseModel):
    verified: bool = True
    issue_number: str = ""      # 관리자가 인터넷등기소에서 실제로 조회한 발급확인번호
    note: str = ""


class BizRegVerifyReq(BaseModel):
    verified: bool = True
    business_number: str = ""   # 관리자가 국세청 홈택스에서 조회·확인한 10자리 사업자등록번호
    hometax_status: str = "계속사업자"   # 계속사업자(일반/면세/간이) | 휴업자 | 폐업자
    note: str = ""


class RoomLimitReq(BaseModel):
    room_limit: int


class AiLimitReq(BaseModel):
    ai_monthly_limit: int


class LessorRegisterReq(BaseModel):
    username: str
    name: str
    phone: str
    account_number: str = ""
    business_number: str = ""
    email: str = ""
    branch: str
    building_name: str
    address: str = ""
    app_fee: int = 33000


@router.get("/status")
def get_status():
    ensure_pool_seeded()
    meta = pool().overview()
    return {
        "success": True,
        "app_id": "rent3_admin",
        "status": "HEALTHY",
        "pool_revision": meta["total_revision"],
        "total_objects": meta["total_objects"],
        "lessors": Lessor.count(),
        "buildings": Building.count(),
        "rooms": Room.count(),
    }


@router.get("/overview", dependencies=ADMIN_ONLY)
def get_admin_overview():
    ensure_pool_seeded()
    p = pool()
    meta = p.overview()

    lessors = Lessor.all()
    buildings = Building.all()
    rooms = Room.all()
    contracts = Contract.find(status="ACTIVE")
    rtms_list = RtmsReport.all()

    branch_summaries: List[Dict[str, Any]] = []
    portfolio_gross = 0
    portfolio_expense = 0
    portfolio_noi = 0

    for b in buildings:
        fin = TaxAndCertificateService.branch_financial_summary(b.branch)
        portfolio_gross += fin["gross_revenue"]
        portfolio_expense += fin["total_operating_expense"]
        portfolio_noi += fin["noi"]
        branch_summaries.append({
            **b.model_dump(),
            **fin,
        })

    monthly_saas_mrr = sum(l.app_fee for l in lessors if l.status == "ACTIVE")
    pending_fee_lessors = [l for l in lessors if l.app_fee_status != "PAID"]

    return {
        "success": True,
        "pool_meta": meta,
        "admins": [a.model_dump() for a in Admin.all()],
        "kpi": {
            "total_buildings": len(buildings),
            "total_rooms": len(rooms),
            "occupied_rooms": sum(1 for r in rooms if r.is_occupied),
            "active_contracts": len(contracts),
            "active_tenants": Tenant.count(status="ACTIVE"),
            "monthly_saas_mrr": monthly_saas_mrr,
            "pending_fee_count": len(pending_fee_lessors),
            "portfolio_gross_revenue": portfolio_gross,
            "portfolio_operating_expense": portfolio_expense,
            "portfolio_noi": portfolio_noi,
        },
        "lessors": [
            {
                **{k: v for k, v in l.safe_dump().items() if k not in ("identity", "franchise_docs", "password")},
                "application": fr.application_summary(l, Building.first(branch=l.branch)),
                "room_count": Room.count(branch=l.branch),
                "published_count": len([r for r in Room.find(branch=l.branch) if r.is_published]),
            }
            for l in lessors
        ],
        "branches": branch_summaries,
        "rtms_reports": [
            {**r.model_dump(), "days_left": r.days_left()}
            for r in sorted(rtms_list, key=lambda x: x.updated_at, reverse=True)
        ],
        "recent_events": p.recent_events(limit=40),
    }


@router.post("/lessors", dependencies=ADMIN_ONLY)
def register_lessor_and_building(req: LessorRegisterReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    lessor = Lessor(
        username=req.username.strip(),
        name=req.name.strip(),
        phone=req.phone.strip(),
        account_number=req.account_number.strip(),
        business_number=req.business_number.strip(),
        email=req.email.strip(),
        branch=b_id,
        app_fee=req.app_fee,
        app_fee_status="PENDING",
        status="ACTIVE",
    ).save(action="lessor_onboarded", app_source=APP_SRC)

    bldg = Building.first(branch=b_id)
    if not bldg:
        bldg = Building(
            id=f"bldg_{b_id}",
            branch=b_id,
            building_name=req.building_name.strip(),
            address=req.address.strip(),
            id_lessor=lessor.id,
        ).save(action="building_onboarded", app_source=APP_SRC)

    return {
        "success": True,
        "message": f"임대인 '{lessor.name}' 및 건물 '{bldg.building_name}'이 공동 JSON Data Pool(users.json, buildings.json)에 등재되었습니다.",
        "lessor": lessor.safe_dump(),
        "building": bldg.model_dump(),
    }


@router.post("/lessors/{lessor_id}/approve", dependencies=ADMIN_ONLY)
def approve_lessor(lessor_id: str):
    lessor = Lessor.require(lessor_id)
    lessor.approve(app_source=APP_SRC)
    return {"success": True, "message": f"임대인 '{lessor.name}' 가맹 승인이 완료되었습니다.", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/decision", dependencies=ADMIN_ONLY)
def decide_lessor(lessor_id: str, req: DecisionReq):
    """가맹 심사 결과: 승인 / 반려 / 정지. 반려·정지는 사유 필수, 해당 지점의 공개 매물이 모두 내려간다."""
    lessor = Lessor.require(lessor_id)
    if req.decision == "approve" and lessor.status == "PENDING_APPROVAL":
        summ = fr.application_summary(lessor, Building.first(branch=lessor.branch))
        ver = (summ["registry"] or {}).get("verification") or {}
        if not (summ["steps"]["submitted"] and summ["ready_to_submit"] and ver.get("verified")):
            if not req.force:
                raise DomainError(
                    "가맹 신청이 완료되지 않았거나 등기부 원본 대조(발급확인번호)가 기록되지 않았습니다.",
                    hint="인터넷등기소에서 발급확인번호를 조회해 '원본 대조 완료'를 기록하세요. 건너뛰려면 '강제 승인'과 사유가 필요합니다(기록됨).",
                )
            if not req.note.strip():
                raise DomainError("서류 미비 상태로 승인하려면 사유를 입력해 주세요.")
    lessor.decide(req.decision, req.note, app_source=APP_SRC)
    label = Lessor.STATUS_LABELS.get(lessor.status, lessor.status)
    return {"success": True, "message": f"임대인 '{lessor.name}' → {label}", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/registry-verify")
def verify_franchise_registry(lessor_id: str, req: RegistryVerifyReq, admin=Depends(require_role("superadmin", "admin")), _nr=Depends(not_revoked)):
    """관리자가 인터넷등기소에서 발급확인번호로 원본을 대조했음을 기록한다 (누가·언제·어떤 번호로). 승인의 근거가 된다."""
    lessor = Lessor.require(lessor_id)
    docs = lessor.franchise_docs or {}
    if not docs.get("registry_file"):
        raise DomainError("제출된 등기부가 없습니다.")
    num = req.issue_number.strip()
    if req.verified and not num:
        raise DomainError("조회한 발급확인번호를 입력해 주세요.", hint="서류에 적힌 번호를 인터넷등기소에서 조회한 뒤 기록합니다.")
    if req.verified and not req.note.strip():
        raise DomainError("대조 결과 메모를 남겨 주세요.", hint="예: 소유자·소재지·발급일시 모두 제출 서류와 일치")
    ai_num = ((docs.get("ai") or {}).get("extracted") or {}).get("issue_number") or ""
    lessor.franchise_docs = {
        **docs,
        "admin_verification": {
            "verified": req.verified,
            "issue_number": num,
            "matches_ai_number": bool(ai_num) and re.sub(r"\D", "", ai_num) == re.sub(r"\D", "", num),
            "note": req.note.strip(),
            "verified_by": getattr(admin, "username", ""),
            "verified_at": now_iso(),
            "file_sha256": docs.get("registry_sha256", ""),
        },
    }
    lessor.save(action="franchise_registry_verified" if req.verified else "franchise_registry_rejected", app_source=APP_SRC)
    return {
        "success": True,
        "message": "등기부 원본 대조 결과를 기록했습니다." if req.verified else "원본과 다름으로 기록했습니다.",
        "application": fr.application_summary(lessor, Building.first(branch=lessor.branch)),
    }


@router.get("/lessors/{lessor_id}/registry-attachment", dependencies=ADMIN_ONLY)
def get_franchise_registry(lessor_id: str):
    """가맹 신청 때 제출된 등기부 원본 (본사 관리자만)."""
    path, mime = fr.registry_file_path(Lessor.require(lessor_id))
    return FileResponse(path, media_type=mime)


@router.post("/lessors/{lessor_id}/bizreg-verify")
def verify_franchise_bizreg(lessor_id: str, req: BizRegVerifyReq, admin=Depends(require_role("superadmin", "admin")), _nr=Depends(not_revoked)):
    """관리자가 국세청 홈택스에서 임대인 사업자등록증(사업자상태·대표자·과세유형)을 확인한 뒤 승인 또는 반려를 기록한다.
    승인(verified=True)되면 임대인 장부의 business_number 에 공식 반영된다."""
    lessor = Lessor.require(lessor_id)
    docs = dict(lessor.franchise_docs or {})
    biz = dict(docs.get("bizreg") or {})
    if not biz.get("file"):
        raise DomainError("제출된 사업자등록증이 없습니다.")
    chk = biz.get("check") or {}
    raw_no = (req.business_number or chk.get("business_number") or "").strip()
    ht_status = (req.hometax_status or "계속사업자").strip()
    note = (req.note or "").strip()
    if not note:
        raise DomainError("홈택스 확인 결과 메모를 남겨 주세요.", hint="예: 홈택스 사업자상태 계속사업자(면세/일반) 및 대표자 일치 확인")
    fmt_no = raw_no
    if req.verified:
        if not raw_no:
            raise DomainError("홈택스에서 확인한 사업자등록번호 10자리를 입력해 주세요.")
        val = validate_kr_business_number(raw_no, strict_checksum=True)
        if not val["valid"]:
            raise DomainError(f"사업자등록번호({raw_no})가 유효하지 않아 승인할 수 없습니다: {val['reason']}")
        if any(bad in ht_status for bad in ("폐업", "휴업", "미등록")):
            raise DomainError(
                f"홈택스 조회 상태가 '{ht_status}'인 사업자등록증은 승인할 수 없습니다.",
                hint="계속사업자 상태인 사업자등록증만 승인할 수 있습니다.",
            )
        d = val["digits"]
        fmt_no = f"{d[:3]}-{d[3:5]}-{d[5:10]}"
        dup = next((o for o in Lessor.all() if o.id != lessor.id and re.sub(r"\D", "", o.business_number or "") == d), None)
        if dup is not None:      # 같은 사업자번호를 다른 임대인이 이미 쓰고 있으면 승인하지 않는다(남의 등록증 도용 방지)
            raise DomainError(
                f"이 사업자등록번호({fmt_no})는 이미 다른 임대인({dup.name})에게 승인되어 있습니다.",
                hint="동일 사업자의 다른 지점이라면 관리인이 직접 확인한 뒤 한쪽을 정리해 주세요.",
            )
        lessor.business_number = fmt_no

    ext_no = str(chk.get("business_number") or "")
    biz["admin_verification"] = {
        "verified": req.verified,
        "business_number": fmt_no,
        "hometax_status": ht_status,
        "matches_extracted_number": bool(ext_no) and re.sub(r"\D", "", ext_no) == re.sub(r"\D", "", fmt_no),
        "note": note,
        "verified_by": getattr(admin, "username", "") or "admin",
        "verified_at": now_iso(),
        "file_sha256": biz.get("sha256", ""),
    }
    docs["bizreg"] = biz
    lessor.franchise_docs = docs
    lessor.save(action="franchise_bizreg_verified" if req.verified else "franchise_bizreg_rejected", app_source=APP_SRC)
    sup.post_admin_message(
        lessor,
        getattr(admin, "username", "") or "관리인",
        (
            f"[✅ 사업자등록증 홈택스 확인·승인 완료] 사업자등록번호 {fmt_no} ({ht_status}) 확인이 완료되어 장부에 반영되었습니다. 메모: {note}"
            if req.verified
            else f"[⚠️ 사업자등록증 반려] 홈택스 확인 결과 보완이 필요합니다. 사유: {note}"
        ),
        category="사업자등록증 확인 결과",
    )
    return {
        "success": True,
        "message": f"홈택스 확인 완료: 사업자등록번호({fmt_no})를 승인하고 장부에 반영했습니다." if req.verified else "사업자등록증 반려 사유를 기록했습니다.",
        "application": fr.application_summary(lessor, Building.first(branch=lessor.branch)),
    }


@router.get("/lessors/{lessor_id}/bizreg-attachment", dependencies=ADMIN_ONLY)
def get_franchise_bizreg(lessor_id: str):
    """임대인이 촬영·제출한 사업자등록증 원본 (본사 관리자만)."""
    path, mime = fr.bizreg_file_path(Lessor.require(lessor_id))
    return FileResponse(path, media_type=mime)


@router.post("/lessors/{lessor_id}/room-limit", dependencies=ADMIN_ONLY)
def set_room_limit(lessor_id: str, req: RoomLimitReq):
    """가맹점당 등록 가능 호실 수 상한 변경 (가맹 상태는 바꾸지 않는다)."""
    if req.room_limit < 1:
        raise DomainError("호실 한도는 1 이상이어야 합니다.")
    lessor = Lessor.require(lessor_id)
    lessor.room_limit = req.room_limit
    lessor.save(action="lessor_room_limit_changed", app_source=APP_SRC)
    return {"success": True, "message": f"호실 한도를 {req.room_limit}실로 바꿨습니다.", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/ai-limit", dependencies=ADMIN_ONLY)
def set_ai_limit(lessor_id: str, req: AiLimitReq):
    """가맹점당 월 AI(Gemini) 등기부 읽기 호출 상한 변경."""
    if req.ai_monthly_limit < 0:
        raise DomainError("AI 호출 한도는 0 이상이어야 합니다.")
    lessor = Lessor.require(lessor_id)
    lessor.ai_monthly_limit = req.ai_monthly_limit
    lessor.save(action="lessor_ai_limit_changed", app_source=APP_SRC)
    return {"success": True, "message": f"월 AI 읽기 한도를 {req.ai_monthly_limit}회로 바꿨습니다.", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/settle-fee", dependencies=ADMIN_ONLY)
def settle_lessor_fee(lessor_id: str):
    lessor = Lessor.require(lessor_id)
    lessor.settle_app_fee(app_source=APP_SRC)
    return {
        "success": True,
        "message": f"임대인 '{lessor.name}' 월 SaaS 이용료({lessor.app_fee:,}원) 완납 정산이 users.json에 반영되었습니다.",
        "lessor": lessor.safe_dump(),
    }
