"""
apps/rent3_admin/api.py — 플랫폼 총괄 관리인 전용 API 라우터.
"""
from typing import Any, Dict, List, Optional
import re
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.auth import require_role

from core.errors import DomainError
from apps.rent_dist._rent3_core.pool import now_iso
from apps.rent_dist._rent3_core import franchise as fr
from apps.rent_dist._rent3_core import (
    Admin,
    Building,
    Contract,
    Lessor,
    POOL_COLLECTIONS,
    Pay,
    Room,
    RtmsReport,
    TaxAndCertificateService,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter()
APP_SRC = "rent3_admin"
# 가맹 심사·정산은 본사 관리자만 (토큰 필요). 지점 관리자(branch_admin)는 자기 지점 가맹을 스스로 승인할 수 없다.
ADMIN_ONLY = [Depends(require_role("superadmin", "admin"))]


class DecisionReq(BaseModel):
    decision: str                 # approve | reject | suspend
    note: str = ""
    force: bool = False           # 신청 서류가 미비해도 관리자 책임으로 승인(사유 필수·기록됨)


class RegistryVerifyReq(BaseModel):
    verified: bool = True
    issue_number: str = ""      # 관리자가 인터넷등기소에서 실제로 조회한 발급확인번호
    note: str = ""


class RoomLimitReq(BaseModel):
    room_limit: int


class AccountResetReq(BaseModel):
    kind: str        # lessor | tenant
    id: str


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
            {**{k: v for k, v in l.model_dump().items() if k not in ("identity", "franchise_docs", "password")},
             "application": fr.application_summary(l, Building.first(branch=l.branch)),
             "room_count": Room.count(branch=l.branch),
             "published_count": len([r for r in Room.find(branch=l.branch) if r.is_published])}
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
        "lessor": lessor.model_dump(),
        "building": bldg.model_dump(),
    }


@router.post("/lessors/{lessor_id}/approve", dependencies=ADMIN_ONLY)
def approve_lessor(lessor_id: str):
    lessor = Lessor.require(lessor_id)
    lessor.approve(app_source=APP_SRC)
    return {"success": True, "message": f"임대인 '{lessor.name}' 가맹 승인이 완료되었습니다.", "lessor": lessor.model_dump()}


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
    return {"success": True, "message": f"임대인 '{lessor.name}' → {label}", "lessor": lessor.model_dump()}


@router.post("/lessors/{lessor_id}/registry-verify")
def verify_franchise_registry(lessor_id: str, req: RegistryVerifyReq, admin=Depends(require_role("superadmin", "admin"))):
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
    lessor.franchise_docs = {**docs, "admin_verification": {
        "verified": bool(req.verified),
        "issue_number": num,
        "matches_ai_number": bool(ai_num) and re.sub(r"\D", "", ai_num) == re.sub(r"\D", "", num),
        "note": req.note.strip(),
        "verified_by": getattr(admin, "username", ""),
        "verified_at": now_iso(),
        "file_sha256": docs.get("registry_sha256", ""),
    }}
    lessor.save(action="franchise_registry_verified" if req.verified else "franchise_registry_rejected", app_source=APP_SRC)
    return {"success": True, "message": "등기부 원본 대조 결과를 기록했습니다." if req.verified else "원본과 다름으로 기록했습니다.",
            "application": fr.application_summary(lessor, Building.first(branch=lessor.branch))}


@router.get("/lessors/{lessor_id}/registry-attachment", dependencies=ADMIN_ONLY)
def get_franchise_registry(lessor_id: str):
    """가맹 신청 때 제출된 등기부 원본 (본사 관리자만)."""
    path, mime = fr.registry_file_path(Lessor.require(lessor_id))
    return FileResponse(path, media_type=mime)


@router.post("/lessors/{lessor_id}/room-limit", dependencies=ADMIN_ONLY)
def set_room_limit(lessor_id: str, req: RoomLimitReq):
    """가맹점당 등록 가능 호실 수 상한 변경 (가맹 상태는 바꾸지 않는다)."""
    if req.room_limit < 1:
        raise DomainError("호실 한도는 1 이상이어야 합니다.")
    lessor = Lessor.require(lessor_id)
    lessor.room_limit = req.room_limit
    lessor.save(action="lessor_room_limit_changed", app_source=APP_SRC)
    return {"success": True, "message": f"호실 한도를 {req.room_limit}실로 바꿨습니다.", "lessor": lessor.model_dump()}


@router.post("/accounts/reset-password", dependencies=ADMIN_ONLY)
def reset_account_password(req: AccountResetReq):
    """본인인증 이력이 없어 직접 재설정할 수 없는 계정에 임시 비밀번호를 발급한다. 임시 비밀번호는 이 응답에서 한 번만 보인다(해시로 저장)."""
    from apps.rent_dist._rent3_core import passwords as pwd
    obj = Lessor.get(req.id) if req.kind == "lessor" else Tenant.get(req.id) if req.kind == "tenant" else None
    if obj is None:
        raise DomainError("계정을 찾을 수 없습니다. kind 는 lessor 또는 tenant 여야 합니다.")
    temp = pwd.temp_password()
    obj.password = pwd.hash_password(temp)
    obj.save(action="password_reset_by_admin", app_source=APP_SRC)
    return {"success": True, "message": f"'{obj.name}' 님의 임시 비밀번호를 발급했습니다. 본인에게 안전하게 전달하고 바로 바꾸게 하세요.",
            "temporary_password": temp}


@router.post("/lessors/{lessor_id}/ai-limit", dependencies=ADMIN_ONLY)
def set_ai_limit(lessor_id: str, req: AiLimitReq):
    """가맹점당 월 AI(Gemini) 등기부 읽기 호출 상한 변경."""
    if req.ai_monthly_limit < 0:
        raise DomainError("AI 호출 한도는 0 이상이어야 합니다.")
    lessor = Lessor.require(lessor_id)
    lessor.ai_monthly_limit = req.ai_monthly_limit
    lessor.save(action="lessor_ai_limit_changed", app_source=APP_SRC)
    return {"success": True, "message": f"월 AI 읽기 한도를 {req.ai_monthly_limit}회로 바꿨습니다.", "lessor": lessor.model_dump()}


@router.post("/lessors/{lessor_id}/settle-fee", dependencies=ADMIN_ONLY)
def settle_lessor_fee(lessor_id: str):
    lessor = Lessor.require(lessor_id)
    lessor.settle_app_fee(app_source=APP_SRC)
    return {
        "success": True,
        "message": f"임대인 '{lessor.name}' 월 SaaS 이용료({lessor.app_fee:,}원) 완납 정산이 users.json에 반영되었습니다.",
        "lessor": lessor.model_dump(),
    }


@router.get("/pool/{collection}", dependencies=ADMIN_ONLY)
def inspect_pool_collection(collection: str, q: str = ""):
    ensure_pool_seeded()
    envelope = pool().raw_collection(collection)
    if q.strip():
        kw = q.strip().lower()
        envelope["items"] = [
            it for it in envelope["items"]
            if kw in str(it).lower()
        ]
        envelope["filtered_count"] = len(envelope["items"])
    return {"success": True, "collections": list(POOL_COLLECTIONS), **envelope}

