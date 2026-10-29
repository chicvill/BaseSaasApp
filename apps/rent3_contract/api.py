"""
apps/rent3_contract/api.py — 공동 임대계약 전용 API 라우터.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from apps._rent3_core import (
    Building,
    CoSignSession,
    Contract,
    Room,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter()
APP_SRC = "rent3_contract"


class SessionCreateReq(BaseModel):
    branch: str = "main"
    room_no: str
    tenant_name: str = ""
    tenant_phone: str = ""
    mode: str = "NEW"  # NEW / RENEWAL


class Step1ConfirmReq(BaseModel):
    role: str = "tenant"  # lessor / tenant
    registry_updates: Optional[Dict[str, Any]] = None


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
    return {
        "success": True,
        "app_id": "rent3_contract",
        "status": "HEALTHY",
        "pool_revision": meta["total_revision"],
        "active_sessions": CoSignSession.count(status="IN_PROGRESS"),
        "completed_sessions": CoSignSession.count(status="COMPLETED"),
        "total_contracts": Contract.count(),
    }


@router.get("/rooms")
def list_candidate_rooms(branch: Optional[str] = None):
    ensure_pool_seeded()
    rooms = Room.find(branch=normalize_branch_id(branch)) if branch else Room.all()
    bldgs = {b.id: b for b in Building.all()}
    return {
        "success": True,
        "rooms": [
            {
                **r.model_dump(),
                "is_occupied": r.is_occupied,
                "building_name": bldgs[r.id_building].building_name if r.id_building in bldgs else r.branch,
            }
            for r in sorted(rooms, key=lambda x: (x.branch, x.room_no))
        ],
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
        "message": f"{s.room_no}호 공동 임대계약 세션(코드: {s.session_code})이 개설되었습니다.",
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
    s.confirm_step1(role=req.role, registry_updates=req.registry_updates, app_source=APP_SRC)
    return {
        "success": True,
        "message": "Step 1 등기부등본 권리 확인 상태가 실시간 반영되었습니다.",
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
            "양측 전자서명 완료 및 SHA-256 계약원본 무결성 봉인이 완료되었습니다!"
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

