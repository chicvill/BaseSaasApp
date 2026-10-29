"""
apps/rent3_contract/api.py — 공동 임대계약 전용 API 라우터 (호출·응답 개시 & 상호 신분증·등기 확인 기반).
"""
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from apps._rent3_core import (
    Building,
    CoSignSession,
    Contract,
    Lessor,
    Room,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter()
APP_SRC = "rent3_contract"


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


class SessionCreateReq(BaseModel):
    branch: str = "main"
    room_no: str
    tenant_name: str = ""
    tenant_phone: str = ""
    mode: str = "NEW"  # NEW / RENEWAL


class SessionCallReq(BaseModel):
    caller_role: str = "lessor"  # lessor / tenant
    branch: str = "main"
    room_no: str
    tenant_name: str = ""
    tenant_phone: str = ""
    id_tenant: str = ""
    mode: str = "NEW"  # NEW / RENEWAL


class SessionRespondReq(BaseModel):
    responder_role: str = "tenant"  # lessor / tenant
    responder_name: str = ""
    responder_phone: str = ""
    id_tenant: str = ""


class VerifyIdReq(BaseModel):
    role: str = "tenant"  # lessor / tenant
    checked: bool = True


class Step1ConfirmReq(BaseModel):
    role: str = "tenant"  # lessor / tenant
    registry_updates: Optional[Dict[str, Any]] = None
    checked_counterparty_id: Optional[bool] = None


class Step2ConfirmReq(BaseModel):
    role: str = "tenant"
    option_checks: Optional[List[Dict[str, Any]]] = None
    facility_notes: Optional[str] = None


class TermsUpdateReq(BaseModel):
    role: str = "lessor"
    updates: Dict[str, Any] = Field(default_factory=dict)


class Step3AgreeReq(BaseModel):
    role: str = "tenant"
    expected_revision: Optional[int] = None


class Step4SignReq(BaseModel):
    role: str = "tenant"
    signature: str
    expected_revision: Optional[int] = None


class Step5CompleteReq(BaseModel):
    payment_method: str = "TRANSFER"


@router.get("/status")
def get_status():
    ensure_pool_seeded()
    meta = pool().overview()
    in_progress = CoSignSession.find(status="IN_PROGRESS")
    return {
        "success": True,
        "app_id": "rent3_contract",
        "status": "HEALTHY",
        "pool_revision": meta["total_revision"],
        "active_sessions": len(in_progress),
        "calling_sessions": sum(1 for s in in_progress if s.call_status == "CALLING"),
        "completed_sessions": CoSignSession.count(status="COMPLETED"),
        "total_contracts": Contract.count(),
    }


@router.get("/rooms")
def list_candidate_rooms(branch: Optional[str] = None):
    ensure_pool_seeded()
    rooms = Room.find(branch=normalize_branch_id(branch)) if branch else Room.all()
    bldgs = {b.id: b for b in Building.all()}
    lessors_by_branch = {lsr.branch: lsr for lsr in Lessor.all()}
    tenants_by_room = {t.current_room_id: t for t in Tenant.all() if t.status == "ACTIVE" and t.current_room_id}
    items: List[Dict[str, Any]] = []
    for r in sorted(rooms, key=lambda x: (x.branch, x.room_no)):
        b = bldgs.get(r.id_building)
        lsr = lessors_by_branch.get(r.branch)
        occ_t = tenants_by_room.get(r.id)
        items.append({
            **r.model_dump(),
            "is_occupied": r.is_occupied,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "lessor_name": lsr.name if lsr else "건물 소유주",
            "lessor_phone": (lsr.phone if lsr and lsr.phone else (b.manager_phone if b else "")),
            "active_tenant_id": occ_t.id if occ_t else "",
            "active_tenant_name": occ_t.name if occ_t else "",
            "active_tenant_phone": occ_t.phone if occ_t else "",
        })
    return {
        "success": True,
        "rooms": items,
    }


@router.get("/participants")
def list_participants():
    """본인인증 가입된 임대인·임차인(예비임차인 포함) 목록 조회 — 매물 선택 후 상대방 호출 시 활용."""
    ensure_pool_seeded()
    lessors = [
        {
            "id": lsr.id,
            "username": lsr.username,
            "name": lsr.name,
            "phone": lsr.phone,
            "branch": lsr.branch,
        }
        for lsr in Lessor.all()
    ]
    tenants = [
        {
            "id": t.id,
            "name": t.name,
            "phone": t.phone,
            "status": t.status,
            "branch": t.branch,
            "room_no": t.room_no,
        }
        for t in sorted(Tenant.all(), key=lambda x: (0 if x.status == "PROSPECT" else 1, x.name))
    ]
    return {
        "success": True,
        "lessors": lessors,
        "tenants": tenants,
    }


@router.get("/incoming-calls")
def list_incoming_calls(
    role: str = "lessor",
    branch: Optional[str] = None,
    tenant_id: Optional[str] = None,
    phone: Optional[str] = None,
    name: Optional[str] = None,
):
    """로그인한 임대인 또는 임차인에게 도착한 공동계약 호출(CALLING) 목록 조회."""
    ensure_pool_seeded()
    norm_role = "lessor" if role == "lessor" else "tenant"
    phone_digits = _digits(phone or "")
    candidates = [
        s for s in CoSignSession.find(status="IN_PROGRESS")
        if s.call_status == "CALLING" and s.initiated_by_role != norm_role
    ]
    matched: List[CoSignSession] = []
    for s in candidates:
        if norm_role == "lessor":
            if branch and branch != "all" and s.branch != normalize_branch_id(branch):
                continue
            matched.append(s)
        else:
            s_tphone = _digits(str((s.terms or {}).get("tenant_phone", "")))
            s_tname = str((s.terms or {}).get("tenant_name", "")).strip()
            if tenant_id and s.id_tenant and s.id_tenant == tenant_id:
                matched.append(s)
            elif len(phone_digits) >= 8 and s_tphone == phone_digits:
                matched.append(s)
            elif name and s_tname and s_tname == name.strip():
                matched.append(s)
            elif not tenant_id and not phone_digits and not name:
                matched.append(s)
    matched.sort(key=lambda x: x.updated_at, reverse=True)
    return {
        "success": True,
        "count": len(matched),
        "calls": [s.model_dump() for s in matched],
    }


@router.get("/sessions")
def list_sessions(branch: Optional[str] = None):
    ensure_pool_seeded()
    sessions = CoSignSession.find(branch=normalize_branch_id(branch)) if branch else CoSignSession.all()
    sessions_sorted = sorted(sessions, key=lambda s: s.updated_at, reverse=True)
    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "sessions": [s.model_dump() for s in sessions_sorted],
    }


@router.post("/sessions")
def create_session(req: SessionCreateReq):
    ensure_pool_seeded()
    s = CoSignSession.open_session(
        branch=req.branch,
        room_no=req.room_no,
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        mode=req.mode,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": f"{s.room_no}호 공동 임대계약 세션이 개설되었습니다.",
        "session": s.model_dump(),
    }


@router.post("/sessions/call")
def call_counterparty_session(req: SessionCallReq):
    """로그인한 임대인 또는 임차인이 먼저 매물을 선택하여 상대방을 호출(CALLING)합니다."""
    ensure_pool_seeded()
    s = CoSignSession.call_counterparty(
        caller_role=req.caller_role,
        branch=req.branch,
        room_no=req.room_no,
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        id_tenant=req.id_tenant,
        mode=req.mode,
        app_source=APP_SRC,
    )
    caller_ko = "임대인" if s.initiated_by_role == "lessor" else "임차인"
    callee_ko = "임차인" if s.initiated_by_role == "lessor" else "임대인"
    return {
        "success": True,
        "message": (
            f"[{s.building_name} {s.room_no}호] {caller_ko}이(가) {callee_ko}을(를) 호출했습니다! "
            f"{callee_ko}이(가) 로그인 화면에서 호출에 응답하면 공동계약이 시작됩니다."
        ),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/respond")
def respond_to_call_session(session_id: str, req: SessionRespondReq):
    """호출받은 상대방(임대인 또는 임차인)이 응답하여 양측 연결(CONNECTED) 및 공동계약을 시작합니다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.respond_to_call(
        responder_role=req.responder_role,
        responder_name=req.responder_name,
        responder_phone=req.responder_phone,
        id_tenant=req.id_tenant,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": (
            f"[{s.building_name} {s.room_no}호] 호출 응답 완료! 양측 본인인증 계정이 연결되었습니다. "
            f"서로 신분증을 확인 체크하고 등기부등본을 확인한 뒤 계약을 진행해 주세요."
        ),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/verify-id")
def verify_counterparty_id(session_id: str, req: VerifyIdReq):
    """임대인·임차인이 서로의 신분증을 대조하여 본인 확인이 완료되었음을 체크합니다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.verify_counterparty_id(role=req.role, checked=req.checked, app_source=APP_SRC)
    actor_ko = "임대인" if req.role == "lessor" else "임차인"
    target_ko = "임차인" if req.role == "lessor" else "임대인"
    state_ko = "본인 확인 완료 체크" if req.checked else "체크 해제"
    return {
        "success": True,
        "message": f"{actor_ko}이(가) {target_ko} 신분증 대조 [{state_ko}]를 반영했습니다.",
        "session": s.model_dump(),
    }


@router.get("/sessions/{token}")
def get_session(token: str):
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(token)
    contract = Contract.get(s.created_contract_id) if s.created_contract_id else None
    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "session": s.model_dump(),
        "contract": contract.model_dump() if contract else None,
    }


@router.post("/sessions/{session_id}/step1")
def confirm_step1(session_id: str, req: Step1ConfirmReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.confirm_step1(
        role=req.role,
        registry_updates=req.registry_updates,
        checked_counterparty_id=req.checked_counterparty_id,
        app_source=APP_SRC,
    )
    msg = (
        "상호 신분증 본인 확인 및 등기부등본 권리 확인이 모두 완료되어 Step 2로 이동합니다!"
        if s.is_step1_fully_verified()
        else "등기부등본 권리 확인 상태가 실시간 반영되었습니다. (양측 신분증 확인 체크 및 등기 확인 완료 시 다음 단계로 진행)"
    )
    return {
        "success": True,
        "message": msg,
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/step2")
def confirm_step2(session_id: str, req: Step2ConfirmReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.confirm_step2(
        role=req.role,
        option_checks=req.option_checks,
        facility_notes=req.facility_notes,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": "Step 2 호실 옵션 및 시설 점검표 상태가 실시간 반영되었습니다.",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/terms")
def update_terms(session_id: str, req: TermsUpdateReq):
    s = CoSignSession.by_code_or_id(session_id)
    prev_rev = s.revision
    s.update_terms(role=req.role, updates=req.updates, app_source=APP_SRC)
    changed = s.revision != prev_rev
    return {
        "success": True,
        "changed": changed,
        "level": "diff" if changed else "success",
        "message": (
            f"계약 조건이 수정되어 리비전(v{s.revision})이 갱신되었으며, 양측 동의 잠금이 재확인 대기로 초기화되었습니다."
            if changed else "변경된 조건이 없습니다."
        ),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/step3/agree")
def agree_step3(session_id: str, req: Step3AgreeReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.agree_step3(role=req.role, expected_revision=req.expected_revision, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{'임대인' if req.role == 'lessor' else '임차인'} 계약 조건 합의 잠금이 완료되었습니다 (v{s.revision}).",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/step4/sign")
def sign_step4(session_id: str, req: Step4SignReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.sign_step4(
        role=req.role,
        signature=req.signature,
        expected_revision=req.expected_revision,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": (
            "상호 신분증·등기 확인 기반 양측 전자서명 및 SHA-256 계약원본 무결성 봉인이 완료되었습니다!"
            if s.integrity_hash else f"{'임대인' if req.role == 'lessor' else '임차인'} 전자서명이 날인되었습니다."
        ),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/step5/complete")
def complete_step5(session_id: str, req: Step5CompleteReq):
    s = CoSignSession.by_code_or_id(session_id)
    result = s.complete_step5_and_seal(payment_method=req.payment_method, app_source=APP_SRC)
    return {
        "success": True,
        "message": (
            f"계약 체결 완료! 공동 Data Pool(contracts.json, rooms.json, users.json, payments.json)이 "
            f"즉시 업데이트되었으며 확정일자({result['contract']['fixed_date_no']})가 부여되었습니다."
        ),
        **result,
    }


@router.get("/contracts/{contract_id}")
def get_sealed_contract(contract_id: str):
    ensure_pool_seeded()
    ct = Contract.require(contract_id)
    bldg = Building.get(ct.id_building)
    return {
        "success": True,
        "contract": ct.model_dump(),
        "building": bldg.model_dump() if bldg else None,
    }
