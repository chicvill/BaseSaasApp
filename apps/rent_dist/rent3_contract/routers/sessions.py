"""
apps/rent_dist/rent3_contract/routers/sessions.py — 상태 · 후보 호실/참여자 · 임차인 본인인증 · 호출/응답/세션 조회 · 알림.
"""
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Building,
    CoSignSession,
    Contract,
    Lessor,
    Room,
    Tenant,
    contract_chat,
    ensure_pool_seeded,
    franchise as fr,
    listing_pdf,
    normalize_branch_id,
    notifications as nf,
    pool,
)
from apps.rent_dist._rent3_core.authz import LESSOR_ROLES, claims_from, is_admin, need_claims, public_room, session_party_ok
from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
from ._deps import APP_SRC, _chat_actor, _digits, _loopback, _tenant_of

router = APIRouter()

_NO_NOTICE = {"success": True, "total": 0, "urgent": 0, "items": [], "recent_chats": []}


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


class TenantIdentityReq(BaseModel):
    name: str
    phone: str
    birth: str
    code: str = ""


class NoticeSeenReq(BaseModel):
    kind: str = ""


def _notice_owner(request: Request):
    """알림 대상: ('lessor'|'tenant', 사용자 객체) — 관리인·알 수 없는 계정은 None(알림 없음)."""
    c = need_claims(request)
    if is_admin(c):
        return None
    if c.get("r") in LESSOR_ROLES:
        me = Lessor.for_branch(str(c.get("b") or ""))
        return ("lessor", me) if me else None
    t = Tenant.get(str(c.get("u", "")).removeprefix("tenant:"))
    return ("tenant", t) if t else None


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
    ExpiryRenewalCheck.activate_due()           # 공개일이 된 사전 매물을 올린다
    c = claims_from(request) or {}
    staff_all = is_admin(c)
    my_branch = normalize_branch_id(c.get("b")) if c.get("r") in ("lessor", "branch_admin") else None
    rooms = Room.find(branch=normalize_branch_id(branch)) if branch else Room.all()
    bldgs = {b.id: b for b in Building.all()}
    lessors_by_branch = {lsr.branch: lsr for lsr in Lessor.all()}
    tenants_by_room = {t.current_room_id: t for t in Tenant.all() if t.status == "ACTIVE" and t.current_room_id}
    default_imgs = (
        "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
        "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80",
        "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
        "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800&q=80",
    )
    items: List[Dict[str, Any]] = []
    for idx, r in enumerate(sorted(rooms, key=lambda x: (x.branch, x.room_no))):
        b = bldgs.get(r.id_building)
        lsr = lessors_by_branch.get(r.branch)
        occ_t = tenants_by_room.get(r.id)
        shown = r.as_listing() if r.is_occupied else r          # 입주 중인 전출 예정 호실은 사전 매물로 올린 다음 세입자용 조건을 보여 준다
        img_url = shown.images[0] if shown.images else default_imgs[idx % len(default_imgs)]
        spec = listing_pdf.normalize_listing_spec(shown.listing_spec, shown.option_list)
        items.append({
            **(shown.model_dump() if (staff_all or r.branch == my_branch) else public_room(shown.model_dump())),
            "is_occupied": r.is_occupied,
            "has_reservation": r.reservation() is not None,
            "expected_vacate_date": r.expected_vacate_date() if (r.is_occupied and r.pre_moveout_notice) else "",
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "lessor_name": lsr.name if lsr else "건물 소유주",
            "lessor_phone": (lsr.phone if lsr and lsr.phone else (b.manager_phone if b else "")),
            "options": spec["options"],
            "listing_spec": spec,
            "terms_pdf_url": listing_pdf.listing_pdf_url(r.id),
            "image": img_url,
            "images": list(shown.images) if shown.images else [img_url],
            **(
                {
                    "active_tenant_id": occ_t.id if occ_t else "",
                    "active_tenant_name": occ_t.name if occ_t else "",
                    "active_tenant_phone": occ_t.phone if occ_t else "",
                }
                if (staff_all or r.branch == my_branch)
                else {"active_tenant_id": "", "active_tenant_name": "", "active_tenant_phone": ""}
            ),
        })
    return {"success": True, "rooms": items}


@router.get("/participants")
def list_participants(request: Request):
    """임대인이 상대방을 고를 때 쓰는 가입자 목록. 임대인·관리자에게만 내려주고, 임차인에게는 빈 목록을 준다(타인 연락처 보호)."""
    ensure_pool_seeded()
    c = claims_from(request) or {}
    if not (is_admin(c) or c.get("r") in ("lessor", "branch_admin")):
        return {"success": True, "lessors": [], "tenants": []}
    lessors = [
        {"id": lsr.id, "username": lsr.username, "name": lsr.name, "phone": lsr.phone, "branch": lsr.branch}
        for lsr in Lessor.all()
    ]
    tenants = [
        {"id": t.id, "name": t.name, "phone": t.phone, "status": t.status, "branch": t.branch, "room_no": t.room_no}
        for t in sorted(Tenant.all(), key=lambda x: (0 if x.status == "PROSPECT" else 1, x.name))
    ]
    return {"success": True, "lessors": lessors, "tenants": tenants}


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
    return {"success": True, "count": len(matched), "calls": [s.model_dump() for s in matched]}


@router.get("/identity")
def my_identity(request: Request):
    """로그인한 임차인의 본인인증 상태 (생년월일은 가려서 돌려준다)."""
    c = claims_from(request) or {}
    if c.get("r") != "tenant":
        return {"success": True, "applies": False, "identity": {"verified": True}}
    t = _tenant_of(request)
    return {
        "success": True,
        "applies": True,
        "identity": fr.mask_identity(t.identity),
        "provider": fr.provider(_loopback(request)).name,
    }


@router.post("/identity/start")
def tenant_identity_start(req: TenantIdentityReq, request: Request):
    _tenant_of(request)
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
    return {
        "success": True,
        "message": "본인인증이 완료되었습니다." + (" (시연용 가짜 인증)" if res.get("fake") else ""),
        "identity": fr.mask_identity(t.identity),
    }


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
    s.ensure_renewal_ready()
    contract = Contract.get(s.created_contract_id) if s.created_contract_id else None
    renewal_ctx = None
    if s.mode == "RENEWAL" and s.prev_contract_id:
        prev_ct = Contract.get(s.prev_contract_id)
        if prev_ct is not None:
            chk = ExpiryRenewalCheck.for_contract(prev_ct.id)
            renewal_ctx = {
                "prev_deposit": prev_ct.deposit,
                "prev_monthly_rent": prev_ct.monthly_rent,
                "prev_maintenance_fee": prev_ct.maintenance_fee,
                "prev_cleaning_fee": prev_ct.cleaning_fee,
                "prev_expiry_date": prev_ct.expiry_date,
                "max_deposit": round(prev_ct.deposit * 1.05),
                "max_monthly_rent": round(prev_ct.monthly_rent * 1.05),
                "notice_sent": bool(chk and chk.status in ("RENEWAL_PREVIEWED", "RENEWAL_ACKNOWLEDGED")),
                "notice_deposit": chk.proposed_deposit if chk else None,
                "notice_monthly_rent": chk.proposed_monthly_rent if chk else None,
                "notice_maintenance_fee": chk.proposed_maintenance_fee if chk else None,
                "notice_term_months": chk.proposed_term_months if chk else None,
                "notice_sent_at": chk.renewal_preview_sent_at if chk else "",
            }
    room = Room.get(s.id_room) if s.id_room else None
    room_info = None
    if room is not None:
        shown_r = room.as_listing() if (s.mode != "RENEWAL" and room.is_occupied) else room
        spec = listing_pdf.normalize_listing_spec(shown_r.listing_spec, shown_r.option_list)
        room_info = {
            "id": room.id,
            "room_category": shown_r.room_category,
            "floor": shown_r.floor,
            "total_floors": shown_r.total_floors,
            "area_sqm": shown_r.area_sqm,
            "direction": shown_r.direction,
            "listing_spec": spec,
            "terms_pdf_url": listing_pdf.listing_pdf_url(room.id),
        }
    who = _chat_actor(request, s) if claims_from(request) else {"role": ""}
    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "session": s.model_dump(),
        "contract": contract.model_dump() if contract else None,
        "renewal_context": renewal_ctx,
        "room_info": room_info,
        "chat": contract_chat.summary_for(s, who["role"] or None),   # 읽음 처리 없이 안 읽은 수·열린 요청 수 (2.5초 갱신에 포함)
    }


@router.get("/notifications")
def contract_notifications(request: Request):
    """전자계약 앱의 🔔: 임대인·임차인 앱과 같은 알림 요약(내 계약방 채팅·답할 제안 + 최근 대화)."""
    who = _notice_owner(request)
    if not who:
        return dict(_NO_NOTICE)
    role, u = who
    return {"success": True, **nf.summary(role, u, u.branch if role == "lessor" else u.id)}


@router.post("/notifications/seen")
def contract_notice_seen(req: NoticeSeenReq, request: Request):
    who = _notice_owner(request)
    if not who:
        return dict(_NO_NOTICE)
    role, u = who
    nf.mark_seen(u, req.kind)
    return {"success": True, **nf.summary(role, u, u.branch if role == "lessor" else u.id, use_cache=False)}
