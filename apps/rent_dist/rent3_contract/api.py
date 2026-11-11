"""
apps/rent3_contract/api.py — 공동 임대계약 전용 API 라우터 (호출·응답 개시 & 상호 신분증·등기 확인 기반).
"""
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from core.errors import Conflict, DomainError
from apps.rent_dist._rent3_core.pool import now_iso
from apps.rent_dist._rent3_core import franchise as fr, contract_chat
from apps.rent_dist._rent3_core.authz import api_headers, claims_from, is_local_request, public_room, contract_scope, is_admin, session_party_ok
from pydantic import BaseModel, Field

from apps.rent_dist._rent3_core import (
    Building,
    CoSignSession,
    Contract,
    Lessor,
    Room,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
    registry_ai,
)

router = APIRouter(dependencies=[Depends(api_headers), Depends(contract_scope)])
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


class CancelReq(BaseModel):
    role: str = "tenant"
    reason: str = ""


class PrefillReq(BaseModel):
    role: str = "lessor"
    updates: Dict[str, Any] = Field(default_factory=dict)


class FacilityPhotoReq(BaseModel):
    role: str = "tenant"
    photo_data_url: str
    caption: str = ""


class FacilityPhotoDeleteReq(BaseModel):
    role: str = "tenant"


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


class TenantRegistryReq(BaseModel):
    registry: Dict[str, Any] = Field(default_factory=dict)
    attachment_data_url: str = ""
    attachment_name: str = ""


class TenantIdentityReq(BaseModel):
    name: str
    phone: str
    birth: str
    code: str = ""


class RegistryAiReq(BaseModel):
    attachment_data_url: str
    consent: bool = False


class RecheckReq(BaseModel):
    changed: bool = False


class Step5CompleteReq(BaseModel):
    payment_method: str = "TRANSFER"
    own_account_checked: bool = False   # 임대인이 본인 계좌의 실제 거래내역에서 입금을 직접 확인함


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
def list_candidate_rooms(request: Request, branch: Optional[str] = None):
    ensure_pool_seeded()
    c = claims_from(request) or {}
    staff_all = is_admin(c)
    my_branch = normalize_branch_id(c.get("b")) if c.get("r") in ("lessor", "branch_admin") else None
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
            **(r.model_dump() if (staff_all or r.branch == my_branch) else public_room(r.model_dump())),
            "is_occupied": r.is_occupied,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "lessor_name": lsr.name if lsr else "건물 소유주",
            "lessor_phone": (lsr.phone if lsr and lsr.phone else (b.manager_phone if b else "")),
            **({"active_tenant_id": occ_t.id if occ_t else "", "active_tenant_name": occ_t.name if occ_t else "",
                "active_tenant_phone": occ_t.phone if occ_t else ""} if (staff_all or r.branch == my_branch) else
               {"active_tenant_id": "", "active_tenant_name": "", "active_tenant_phone": ""}),
        })
    return {
        "success": True,
        "rooms": items,
    }


@router.get("/participants")
def list_participants(request: Request):
    """임대인이 상대방을 고를 때 쓰는 가입자 목록. 임대인·관리자에게만 내려주고, 임차인에게는 빈 목록을 준다(타인 연락처 보호)."""
    ensure_pool_seeded()
    c = claims_from(request) or {}
    if not (is_admin(c) or c.get("r") in ("lessor", "branch_admin")):
        return {"success": True, "lessors": [], "tenants": []}
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


def _tenant_of(request: Request) -> Tenant:
    c = claims_from(request) or {}
    if c.get("r") != "tenant":
        raise DomainError("임차인 계정으로 로그인했을 때만 쓸 수 있습니다.", hint="임대인은 가맹 신청 단계에서 본인인증을 합니다.")
    t = Tenant.get(str(c.get("u", "")).removeprefix("tenant:"))
    if t is None:
        raise DomainError("임차인 정보를 찾을 수 없습니다. 다시 로그인해 주세요.")
    return t


def _loopback(request: Request) -> bool:
    return is_local_request(request)


@router.get("/identity")
def my_identity(request: Request):
    """로그인한 임차인의 본인인증 상태 (생년월일은 가려서 돌려준다)."""
    c = claims_from(request) or {}
    if c.get("r") != "tenant":
        return {"success": True, "applies": False, "identity": {"verified": True}}
    t = _tenant_of(request)
    return {"success": True, "applies": True, "identity": fr.mask_identity(t.identity),
            "provider": fr.provider(_loopback(request)).name}


@router.post("/identity/start")
def tenant_identity_start(req: TenantIdentityReq, request: Request):
    t = _tenant_of(request)
    p = fr.provider(_loopback(request))
    out = p.start(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth))
    return {"success": True, "message": "인증번호를 보냈습니다.", "provider": p.name, **out}


@router.post("/identity/confirm")
def tenant_identity_confirm(req: TenantIdentityReq, request: Request):
    """인증번호 확인. 인증된 휴대폰은 가입한 번호와 같아야 하고, 같은 사람(이름+생년월일)은 한 계정만 쓸 수 있다."""
    t = _tenant_of(request)
    p = fr.provider(_loopback(request))
    birth6 = fr.normalize_birth(req.birth)
    res = p.confirm(req.name.strip(), req.phone.strip(), birth6, req.code)
    if re.sub(r"\D", "", res["phone"]) != re.sub(r"\D", "", t.phone):
        raise DomainError("인증한 휴대폰 번호가 가입한 번호와 다릅니다.", hint="가입할 때 입력한 본인 명의 휴대폰으로 인증해 주세요.")
    for other in Tenant.all():
        oi = other.identity or {}
        if other.id != t.id and oi.get("verified") and oi.get("name") == res["name"] and oi.get("birth6") == res["birth6"]:
            raise DomainError("이미 다른 계정에서 인증된 본인입니다.", hint="한 사람이 여러 계정으로 계약할 수 없습니다. 기존 계정으로 로그인해 주세요.")
    t.identity = fr.verify_identity_record(res)
    t.name = res["name"]  # 실명으로 확정
    t.save(action="tenant_identity_verified", app_source=APP_SRC)
    return {"success": True, "message": "본인인증이 완료되었습니다." + (" (시연용 가짜 인증)" if res.get("fake") else ""),
            "identity": fr.mask_identity(t.identity)}


@router.get("/sessions")
def list_sessions(request: Request, branch: Optional[str] = None):
    ensure_pool_seeded()
    CoSignSession.expire_idle()
    sessions = CoSignSession.find(branch=normalize_branch_id(branch)) if branch else CoSignSession.all()
    c = claims_from(request) or {}
    if not is_admin(c):  # 본인이 당사자인 계약만 보인다
        sessions = [x for x in sessions if session_party_ok(c, x)]
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
def get_session(token: str, request: Request):
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(token)
    if s.is_idle_expired():
        CoSignSession.expire_idle(s.branch, s.room_no)
        s = CoSignSession.by_code_or_id(token)
    contract = Contract.get(s.created_contract_id) if s.created_contract_id else None
    who = _chat_actor(request, s) if claims_from(request) else {"role": ""}
    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "session": s.model_dump(),
        "contract": contract.model_dump() if contract else None,
        "chat": contract_chat.summary_for(s, who["role"] or None),   # 읽음 처리 없이 안 읽은 수·열린 요청 수 (2.5초 갱신에 포함)
    }


class ContractMsgReq(BaseModel):
    kind: str = "question"            # question | proposal | change_request
    body: str = ""
    field: str = ""
    new_value: Optional[Any] = None   # 제안 값(숫자 또는 날짜)


class ContractMsgRespondReq(BaseModel):
    action: str                        # accept | decline | withdraw


def _chat_actor(request: Request, s) -> Dict[str, str]:
    """토큰으로 이 대화의 발화자(역할·이름)를 정한다. 본문에 적은 역할은 믿지 않는다. 관리인은 읽기만 가능(쓰기는 authz 가 막는다)."""
    from apps.rent_dist._rent3_core.authz import need_claims, is_admin, LESSOR_ROLES
    c = need_claims(request)
    if is_admin(c):
        return {"role": "", "name": "관리인"}
    if c.get("r") in LESSOR_ROLES:
        return {"role": "lessor", "name": s.lessor_name or "임대인"}
    return {"role": "tenant", "name": str((s.terms or {}).get("tenant_name") or "임차인")}


@router.get("/sessions/{session_id}/messages")
def contract_messages(session_id: str, request: Request, mark_read: bool = True):
    """계약방 대화 목록(문의·제안·수정 요청·안내). 당사자만. mark_read=false 면 읽음 처리하지 않는다(주기적 확인용)."""
    contract_chat.purge_expired()        # 보관 기간 지난 대화 정리(10분에 한 번)
    s = CoSignSession.by_code_or_id(session_id)
    who = _chat_actor(request, s)
    return {"success": True, **contract_chat.list_for(s, who["role"] or None, mark_read=mark_read)}


@router.post("/sessions/{session_id}/messages")
def contract_message_post(session_id: str, req: ContractMsgReq, request: Request):
    s = CoSignSession.by_code_or_id(session_id)
    who = _chat_actor(request, s)
    m = contract_chat.post(s, who["role"], who["name"], req.kind, body=req.body, field=req.field, new_value=req.new_value)
    msg = {"question": "문의를 남겼습니다.", "proposal": "제안을 보냈습니다. 상대방이 수락하면 조건이 바뀝니다.",
           "change_request": "수정 요청을 보냈습니다."}[m.kind]
    return {"success": True, "message": msg, "item": contract_chat.public_item(m), "open_count": len(contract_chat.open_items(s.id))}


@router.get("/sessions/{session_id}/chat-digest")
def contract_chat_digest(session_id: str):
    """체결 때 기록한 협상 기록 해시와 지금 내용이 맞는지 확인한다(읽음 표시는 해시에 영향이 없다). 당사자만."""
    s = CoSignSession.by_code_or_id(session_id)
    ct = Contract.get(s.created_contract_id) if s.created_contract_id else None
    stored = ct.chat_digest if ct else ""
    if ct and ct.chat_purged_at:         # 보관 기간 만료로 지웠다 — 변조(match=false)와 구분한다
        return {"success": True, "concluded": True, "stored": stored, "computed": "", "count": 0, "match": None, "purged": True,
                "purged_at": ct.chat_purged_at, "original_count": ct.chat_digest_count, "note": contract_chat.PURGED_NOTE}
    computed, n = contract_chat.compute_digest(s.id)
    return {"success": True, "concluded": bool(ct), "stored": stored, "computed": computed, "count": n,
            "match": (computed == stored) if stored else None, "purged": False}


@router.post("/sessions/{session_id}/messages/{msg_id}/respond")
def contract_message_respond(session_id: str, msg_id: str, req: ContractMsgRespondReq, request: Request):
    s = CoSignSession.by_code_or_id(session_id)
    who = _chat_actor(request, s)
    res = contract_chat.respond(s, msg_id, who["role"], req.action)
    text = {"accept": "수락했습니다." if res["item"]["kind"] == "proposal" else "확인·반영으로 표시했습니다.",
            "decline": "거절했습니다.", "withdraw": "철회했습니다."}[req.action]
    return {"success": True, "message": text, "session": s.model_dump(), **res, "open_count": len(contract_chat.open_items(s.id))}


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


@router.post("/sessions/{session_id}/tenant-registry")
def submit_tenant_registry(session_id: str, req: TenantRegistryReq):
    """임차인이 인터넷등기소에서 직접 열람한 결과(+선택 첨부)를 기록한다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.submit_tenant_registry(
        req.registry,
        attachment_data_url=req.attachment_data_url,
        attachment_name=req.attachment_name,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": "직접 열람한 등기부 확인 내용이 기록되었습니다.",
        "session": s.model_dump(),
    }


REGISTRY_AI_MAX_CALLS = 5  # 계약 1건당 Gemini 호출 상한 (비용 보호)


@router.post("/sessions/{session_id}/registry-ai")
def read_registry_with_ai(session_id: str, req: RegistryAiReq):
    """임차인이 올린 등기사항증명서를 Gemini 로 읽어 항목을 추출하고, 위험 신호는 코드로 계산한다.

    - 전송 동의가 있어야 한다. 올린 파일은 분석에 쓰고 서버에 저장하지 않는다.
    - 결과는 참고용이다. AI 는 서류의 진위를 가리지 못한다.
    """
    s = CoSignSession.by_code_or_id(session_id)
    s._ensure_ready_for_steps()
    if not req.consent:
        raise DomainError(
            "등기부 내용이 Google Gemini 로 전송되는 것에 동의해야 합니다.",
            hint="동의하지 않으면 AI 읽기 없이 직접 입력해 주세요.",
        )
    if s.registry_ai_calls >= REGISTRY_AI_MAX_CALLS:
        raise DomainError(
            f"이 계약의 AI 읽기 횟수({REGISTRY_AI_MAX_CALLS}회)를 모두 썼습니다.",
            hint="직접 입력해 주세요.",
        )
    lsr = Lessor.for_branch(s.branch)
    if lsr and CoSignSession.ai_calls_this_month(s.branch) >= lsr.ai_monthly_limit:
        raise DomainError(
            f"이 건물의 이번 달 AI 읽기 한도({lsr.ai_monthly_limit}회)를 모두 썼습니다.",
            hint="직접 입력해 주세요. 한도는 본사 관리자에게 문의할 수 있습니다.",
        )
    binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
    bldg = Building.get(s.id_building)
    t = s.terms or {}
    result = registry_ai.analyze(
        binary, mime,
        lessor_names=[n for n in (s.lessor_name, (s.registry_data or {}).get("owner_name")) if n],
        deposit=int(t.get("deposit") or 0),
        property_value=int(getattr(bldg, "property_value", 0) or 0) if bldg else 0,
    )
    if result["status"] == "ok":
        s.registry_ai_calls += 1
        s.registry_ai_times = [*s.registry_ai_times, now_iso()]
        s.registry_ai_result = result
        s.save(action="registry_ai_read", app_source=APP_SRC)
    return {
        "success": result["status"] == "ok",
        "level": "success" if result["status"] == "ok" else "warning",
        "message": ("AI가 등기부를 읽었습니다. 참고용이니 원본과 꼭 대조하세요." if result["status"] == "ok"
                    else result["message"]),
        "available": result["status"] != "unavailable",
        "result": result,
        "calls_left": max(REGISTRY_AI_MAX_CALLS - s.registry_ai_calls, 0),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/recheck")
def recheck_registry(session_id: str, req: RecheckReq):
    """체결 직전 임차인의 등기부 재열람 결과. 변동이 있으면 계약을 중단한다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.recheck_registry(changed=req.changed, app_source=APP_SRC)
    return {
        "success": True,
        "message": ("등기부에 변동이 확인되어 계약이 중단되었습니다." if req.changed
                    else "체결 직전 재열람 확인이 기록되었습니다."),
        "session": s.model_dump(),
    }


@router.get("/sessions/{session_id}/registry-attachment")
def get_registry_attachment(session_id: str):
    s = CoSignSession.by_code_or_id(session_id)
    path, mime, name = s.registry_attachment_path()
    return FileResponse(path, media_type=mime, filename=name)


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


@router.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: str, req: CancelReq):
    """체결 전 계약 취소(당사자만). 사유가 상대방 화면에 그대로 전달되고 호실이 다시 열린다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.cancel(req.role, req.reason, app_source=APP_SRC)
    return {"success": True, "message": "계약을 취소했습니다. 호실이 다시 열렸습니다.", "session": s.model_dump()}


@router.post("/sessions/{session_id}/prefill")
def prefill_session(session_id: str, req: PrefillReq):
    """대면 전 사전 작성: 임대인은 계약 조건, 임차인은 이메일·차량번호. 상대가 연결하기 전에만 가능하며 만난 뒤 ③단계에서 다시 합의한다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.prefill(req.role, req.updates, app_source=APP_SRC)
    return {"success": True, "message": "사전 작성 내용을 저장했습니다. 만나서 ③단계에서 양쪽이 다시 확인합니다.", "session": s.model_dump()}


@router.post("/sessions/{session_id}/facility-photos")
def add_facility_photo(session_id: str, req: FacilityPhotoReq):
    """시설 점검 사진 업로드(모바일 카메라). 당사자만, 점검 단계에서만."""
    s = CoSignSession.by_code_or_id(session_id)
    rec = s.add_facility_photo(req.role, req.photo_data_url, req.caption, app_source=APP_SRC)
    return {"success": True, "message": "점검 사진이 기록되었습니다.", "photo": rec, "session": s.model_dump()}


@router.get("/sessions/{session_id}/facility-photos/{photo_id}")
def get_facility_photo(session_id: str, photo_id: str):
    s = CoSignSession.by_code_or_id(session_id)
    path, mime = s.facility_photo_path(photo_id)
    return FileResponse(path, media_type=mime)


@router.post("/sessions/{session_id}/facility-photos/{photo_id}/delete")
def delete_facility_photo(session_id: str, photo_id: str, req: FacilityPhotoDeleteReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.remove_facility_photo(req.role, photo_id, app_source=APP_SRC)
    return {"success": True, "message": "사진을 지웠습니다.", "session": s.model_dump()}


FIELD_KO = {"deposit": "보증금", "contract_deposit": "계약금", "monthly_rent": "월세", "maintenance_fee": "관리비",
            "move_in_date": "입주일", "lease_term_months": "계약기간"}


def terms_lock_enabled() -> bool:
    """RENT3_TERMS_LOCK: 기본 켜짐. '0' 이면 예전처럼 /terms 로 모든 조건을 바로 고칠 수 있다(비상·시연용)."""
    import os
    return os.environ.get("RENT3_TERMS_LOCK", "1").strip() != "0"


class ReopenTermsReq(BaseModel):
    role: str = "lessor"
    confirm: bool = False


@router.post("/sessions/{session_id}/reopen-terms")
def reopen_terms(session_id: str, req: ReopenTermsReq):
    """서명 이후 조건 변경: 경고를 확인(confirm)하면 양측 서명·동의가 지워지고 3단계로 돌아간다."""
    s = CoSignSession.by_code_or_id(session_id)
    if not req.confirm:
        raise DomainError(
            "조건을 다시 열면 양측 서명과 3단계 동의가 초기화되고 3단계로 돌아갑니다.",
            hint="그래도 진행하려면 확인 후 다시 요청해 주세요(confirm=true).",
        )
    s.reopen_terms(role=req.role, app_source=APP_SRC)
    who = "임대인" if req.role == "lessor" else "임차인"
    contract_chat.system_note(s, f"{who}이(가) 조건을 다시 열었습니다. 양측 서명·동의가 초기화되고 3단계로 돌아갔습니다 (리비전 v{s.revision}).", step=3)
    return {
        "success": True, "level": "diff",
        "message": f"조건을 다시 열었습니다. 서명이 초기화되고 3단계로 돌아갑니다 (리비전 v{s.revision}).",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/terms")
def update_terms(session_id: str, req: TermsUpdateReq):
    s = CoSignSession.by_code_or_id(session_id)
    if terms_lock_enabled() and s.call_status != "CALLING" and s.status == "IN_PROGRESS":
        changed_keys = s.terms_changes(req.updates)      # 지금 값과 같게 넘어온 항목은 그대로 통과한다
        if changed_keys and s.current_step >= 4:
            raise Conflict(
                "서명 단계 이후에는 조건을 바로 고칠 수 없습니다.",
                hint="'조건 다시 열기'를 쓰세요. 양측 서명과 3단계 동의가 초기화되고 3단계로 돌아갑니다.",
            )
        locked = [FIELD_KO[k] for k in changed_keys if k in CoSignSession.TERMS_LOCKED_FIELDS]
        if locked:
            raise Conflict(
                f"{', '.join(locked)}은(는) 직접 고칠 수 없습니다. 상대에게 제안하고 수락을 받아야 바뀝니다.",
                hint="계약방 대화(💬)에서 '제안'을 보내세요. 인적사항·납부 시점·세금계산서 정보·특약은 직접 고칠 수 있습니다.",
            )
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
    if not req.own_account_checked:
        raise DomainError(
            "본인 계좌의 실제 거래내역에서 입금을 직접 확인해야 체결할 수 있습니다.",
            hint="임차인이 보여 주는 이체 완료 화면·캡처는 위조될 수 있습니다. 은행 앱의 입금 내역을 확인해 주세요.",
        )
    result = s.complete_step5_and_seal(payment_method=req.payment_method, app_source=APP_SRC)
    return {
        "success": True,
        "message": (
            f"계약 체결 완료! 계약·호실·입주자·수납 정보가 "
            f"즉시 업데이트되었습니다. 계약 접수번호는 {result['contract']['fixed_date_no']} 입니다 (법적 확정일자는 주민센터·등기소에서 별도로 받아야 합니다)."
        ),
        **result,
    }


def _ledger_diffs(ct: Contract, sess: CoSignSession) -> List[Dict[str, Any]]:
    """계약서에 표시되는 원장(contracts.json)이 서명 당시 세션 내용과 같은지 비교한다.

    서명 해시는 세션 데이터로만 계산되므로, 원장만 따로 고치면 해시 검사로는 걸리지 않는다.
    """
    t = sess.terms or {}
    checks = [
        ("deposit", "보증금", int(t.get("deposit", ct.deposit)), int(ct.deposit)),
        ("monthly_rent", "월 임차료", int(t.get("monthly_rent", ct.monthly_rent)), int(ct.monthly_rent)),
        ("maintenance_fee", "월 관리비", int(t.get("maintenance_fee", ct.maintenance_fee)), int(ct.maintenance_fee)),
        ("contract_deposit", "계약금", int(t.get("contract_deposit", ct.contract_deposit)), int(ct.contract_deposit)),
        ("lease_term_months", "임대차 개월 수", int(t.get("lease_term_months", ct.lease_term_months)), int(ct.lease_term_months)),
        ("lease_type", "임대 유형", t.get("lease_type", ct.lease_type), ct.lease_type),
        ("pay_timing", "납부 시기", t.get("pay_timing", ct.pay_timing), ct.pay_timing),
        ("move_in_date", "입주일", t.get("move_in_date") or ct.move_in_date, ct.move_in_date),
        ("special_terms", "특약사항", t.get("special_terms", ct.special_terms), ct.special_terms),
        ("tenant_name", "임차인 성명", t.get("tenant_name", ct.tenant_name), ct.tenant_name),
        ("tenant_phone", "임차인 연락처", t.get("tenant_phone", ct.tenant_phone), ct.tenant_phone),
        ("room_no", "호실", sess.room_no, ct.room_no),
        ("lessor_signature", "임대인 서명", sess.lessor_signature, ct.lessor_signature),
        ("tenant_signature", "임차인 서명", sess.tenant_signature, ct.tenant_signature),
        ("integrity_hash", "무결성 해시", sess.integrity_hash, ct.integrity_hash),
    ]
    diffs: List[Dict[str, Any]] = []
    for key, label, signed, ledger in checks:
        if str(signed) != str(ledger):
            # 서명·해시·개인정보는 값을 그대로 보이지 않고 '다름'만 알린다.
            hide = key in ("lessor_signature", "tenant_signature", "integrity_hash", "tenant_name", "tenant_phone", "special_terms")
            diffs.append({
                "field": key, "label": label,
                "signed": "" if hide else signed,
                "ledger": "" if hide else ledger,
            })
    return diffs


@router.get("/verify")
def verify_contract_integrity(hash: str = "", no: str = ""):
    """계약서 진위 확인 (공개). 인쇄본의 무결성 해시(SHA-256)와 계약번호로 서버 기록과 대조한다.

    개인정보(성명·연락처)는 응답에 넣지 않고, 확인에 필요한 최소 정보만 돌려준다.
    """
    ensure_pool_seeded()
    h = re.sub(r"[^0-9a-fA-F]", "", hash or "").lower()
    contract_no = (no or "").strip()
    if len(h) != 64:
        return {
            "success": True,
            "result": "INVALID_INPUT",
            "message": "무결성 해시는 64자리(0-9, a-f)입니다. 계약서에 적힌 값을 그대로 입력해 주세요.",
        }

    ct = next((c for c in Contract.all() if (c.integrity_hash or "").lower() == h), None)
    if ct is None and contract_no:
        by_no = Contract.get(contract_no)
        if by_no:
            return {
                "success": True,
                "result": "MISMATCH",
                "message": "입력한 계약번호는 존재하지만 서버에 기록된 해시와 다릅니다. 인쇄본이 변조되었거나 다른 계약서일 수 있습니다.",
                "contract_no": by_no.id,
            }
    if ct is None:
        return {
            "success": True,
            "result": "NOT_FOUND",
            "message": "이 해시로 체결된 계약이 서버에 없습니다. 인쇄본이 변조되었거나 이 시스템에서 만든 계약서가 아닐 수 있습니다.",
        }
    if contract_no and contract_no != ct.id:
        return {
            "success": True,
            "result": "MISMATCH",
            "message": "해시는 서버에 있지만 입력한 계약번호와 다른 계약의 것입니다. 인쇄본의 계약번호가 바뀌었을 수 있습니다.",
        }

    summary = {
        "contract_no": ct.id,
        "building_name": ct.id_building and (Building.get(ct.id_building).building_name if Building.get(ct.id_building) else ""),
        "room_no": ct.room_no,
        "contract_date": ct.contract_date,
        "lease_type": ct.lease_type,
        "fixed_date_no": getattr(ct, "fixed_date_no", "") or "",
        "status": ct.status,
    }
    sess = CoSignSession.get(ct.cosign_session_id) if ct.cosign_session_id else None
    if sess is None:
        return {
            "success": True,
            "result": "MATCHED_NO_SOURCE",
            "message": "서버에 기록된 해시와 일치합니다. 다만 원본 계약 데이터가 없어 내용 변경 여부까지는 다시 계산할 수 없습니다.",
            "summary": summary,
        }
    recomputed = sess.compute_integrity_hash()
    if recomputed != (ct.integrity_hash or "").lower():
        return {
            "success": True,
            "result": "ALTERED",
            "message": "서버에 저장된 계약 데이터가 서명 이후 바뀌었습니다. 이 계약서는 신뢰할 수 없습니다. 관리자에게 문의하세요.",
            "summary": summary,
        }
    diffs = _ledger_diffs(ct, sess)
    if diffs:
        return {
            "success": True,
            "result": "LEDGER_MISMATCH",
            "message": "서명 당시 내용은 그대로지만, 계약서에 표시되는 계약 원장이 서명 내용과 다릅니다. 원장이 서명 뒤에 수정되었을 수 있어 이 계약서는 신뢰할 수 없습니다.",
            "summary": summary,
            "diffs": diffs,
        }
    return {
        "success": True,
        "result": "INTACT",
        "message": "인쇄본의 해시가 서버 기록과 일치하고, 서명 이후 계약 내용과 계약 원장이 바뀌지 않았습니다.",
        "summary": summary,
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
