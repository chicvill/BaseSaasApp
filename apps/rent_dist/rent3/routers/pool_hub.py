"""
apps/rent3/routers/pool_hub.py — 통합관리용 공동 JSON Data Pool 관제 및 OpenRent 직거래 포털·역할별 로그인 라우터.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from core.auth import Unauthorized, User as CoreUser, ensure_default_admins, issue_token
from core.auth import require_role
from apps.rent_dist._rent3_core.authz import api_headers, client_ip, contract_scope, is_local_request, lessor_token, not_revoked, tenant_token
from apps.rent_dist._rent3_core import franchise as fr, passwords as pwd, ratelimit
from apps.rent_dist._rent3_core import consent as cs, support as sup, contract_chat, quicklogin as ql, invite as inv, backup as bk

# 원본 Data Pool(개인정보·계정 정보 포함) 열람은 본사 관리자만
ADMIN_ONLY = [Depends(not_revoked), Depends(require_role("superadmin", "admin"))]
from core.errors import Conflict, DomainError, NotFound
from apps.rent_dist._rent3_core import (
    POOL_COLLECTIONS,
    Admin,
    Building,
    CoSignSession,
    Contract,
    DirectChat,
    Lessor,
    Room,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter(prefix="/pool", tags=["rent3-pool-hub"], dependencies=[Depends(api_headers)])

DEFAULT_ROOM_IMAGES = (
    "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
    "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80",
    "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
    "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800&q=80",
)


class PortalLoginReq(BaseModel):
    login_mode: str = "account"          # account | cosign
    identifier: str = ""                 # 아이디 / 휴대폰 / 이메일 / 성명
    password: str = ""
    target_hint: str = ""                # "" | "pool" | "add_listing" | "broker" | "contract" | "contract:{branch}:{room_no}"
    session_code: str = ""
    cosign_role: str = "tenant"          # lessor | tenant


def demo_login_allowed(request: Request) -> bool:
    """공통 데모 비밀번호(1212·0202·전화 뒷자리)는 로컬 개발에서만 허용한다.
    환경변수 RENT3_DEMO_LOGIN=1/0 으로 강제 지정할 수 있고, 지정이 없으면 서버 자신(loopback)에서 온 요청만 허용한다."""
    env = os.getenv("RENT3_DEMO_LOGIN", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    return is_local_request(request)


class PortalRegisterReq(BaseModel):
    role: str = "prospect"               # prospect (예비임차인) | lessor (임대인)
    name: str
    phone: str
    email: str = ""
    username: str = ""
    password: str = "1212"
    building_name: str = ""
    address: str = ""
    branch: str = ""
    target_hint: str = ""
    agree_privacy: bool = False          # (필수) 개인정보 수집·이용 동의
    agree_support: bool = False          # (선택) 관리인 원격 지원 접속 동의


class ConsentReq(BaseModel):
    ticket: str
    agree_privacy: bool = False
    agree_support: bool = False


class SupportToggleReq(BaseModel):
    allow: bool


class PortalInquiryReq(BaseModel):
    branch: str
    room_no: str
    sender_name: str
    sender_phone: str = ""
    visit_date: str = ""
    message: str


class PortalRentNowReq(BaseModel):
    caller_role: str = "tenant"          # lessor | tenant
    branch: str
    room_no: str
    tenant_name: str = ""
    tenant_phone: str = ""
    id_tenant: str = ""


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _build_contract_target_url(
    role_param: str,
    target_hint: str,
    branch: str = "",
    tenant_id: str = "",
) -> str:
    parts = (target_hint or "").split(":")
    q: List[str] = [f"role={role_param}"]
    if role_param == "lessor" and branch:
        q.append(f"branch={branch}")
    if role_param == "tenant" and tenant_id:
        q.append(f"tenant={tenant_id}")
    if len(parts) >= 3 and parts[1] and parts[2]:
        q.append(f"call_branch={normalize_branch_id(parts[1])}")
        q.append(f"call_room={parts[2].strip()}")
    return f"/rent3_contract/?{'&'.join(q)}"


@router.get("/overview", dependencies=ADMIN_ONLY)
def pool_overview() -> Dict[str, Any]:
    ensure_pool_seeded()
    p = pool()
    return {
        "success": True,
        "meta": p.overview(),
        "apps": [
            {"app_id": "rent3", "name": "직거래 포털 홈 (OpenRent)", "icon": "🏡", "url": "/rent3/"},
            {"app_id": "rent3_pool", "name": "통합관리 Data Pool 허브", "icon": "🌐", "url": "/rent3/pool.html"},
            {"app_id": "rent3_admin", "name": "관리인 전용 앱", "icon": "👑", "url": "/rent3_admin/"},
            {"app_id": "rent3_lessor", "name": "임대인 전용 앱", "icon": "🏢", "url": "/rent3_lessor/"},
            {"app_id": "rent3_tenant", "name": "임차인 전용 앱", "icon": "🔑", "url": "/rent3_tenant/"},
            {"app_id": "rent3_contract", "name": "공동 임대계약 전용 앱", "icon": "🤝", "url": "/rent3_contract/"},
        ],
        "recent_events": p.recent_events(limit=30),
    }


@router.get("/files/{collection}", dependencies=ADMIN_ONLY)
def pool_file_raw(collection: str) -> Dict[str, Any]:
    ensure_pool_seeded()
    return {
        "success": True,
        "collections": list(POOL_COLLECTIONS),
        **pool().raw_collection(collection),
    }


@router.get("/landing")
def portal_landing(request: Request) -> Dict[str, Any]:
    """OpenRent 스타일 직거래 포털 메인 화면용 실시간 Data Pool 집계."""
    ensure_pool_seeded()
    meta = pool().overview()
    buildings = Building.all()
    lessors = Lessor.all()
    rooms = Room.all()
    contracts = [c for c in Contract.all() if c.status == "ACTIVE"]
    sessions = [s for s in CoSignSession.all() if s.status != "CANCELLED"]

    bldg_by_branch: Dict[str, Building] = {b.branch: b for b in buildings}
    lessor_by_branch: Dict[str, Lessor] = {l.branch: l for l in lessors}

    franchise_ok = {l.branch: l.status == "ACTIVE" for l in lessors}
    listings: List[Dict[str, Any]] = []
    for idx, r in enumerate(sorted(rooms, key=lambda x: (x.branch, x.room_no))):
        is_public_vacancy = (not r.is_occupied and r.is_published) or r.pre_moveout_notice
        if not is_public_vacancy or not franchise_ok.get(r.branch, True):
            continue
        b = bldg_by_branch.get(r.branch)
        lsr = lessor_by_branch.get(r.branch)
        reg = r.registry_info or {}
        img_url = r.images[0] if r.images else DEFAULT_ROOM_IMAGES[idx % len(DEFAULT_ROOM_IMAGES)]
        opts = [str(o.get("item", "")) for o in (r.option_list or []) if o.get("item")]
        listings.append({
            "id": r.id,
            "branch": r.branch,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "room_no": r.room_no,
            "name": r.name or f"{r.room_no}호",
            "room_category": r.room_category,
            "floor": r.floor,
            "area_sqm": r.area_sqm,
            "lease_type": r.lease_type,
            "pay_timing": r.pay_timing,
            "deposit": r.deposit,
            "monthly_rent": r.monthly_rent,
            "maintenance_fee": r.maintenance_fee,
            "vat_applicable": r.vat_applicable,
            "available_from": r.available_from or "즉시입주",
            "pre_moveout_notice": r.pre_moveout_notice,
            "clean_title": bool(reg.get("clean_title", True)),
            "status_description": r.status_description,
            "options": opts,
            "image": img_url,
            "contract_in_progress": CoSignSession.live_for_room(r.branch, r.room_no) is not None,
            "lessor_name": lsr.name if lsr else "건물 소유주",
            "lessor_phone": (lsr.phone if lsr and lsr.phone else (b.manager_phone if b else "010-2222-1111")),
        })

    open_sessions = [
        {
            "id": s.id,
            "session_code": s.session_code,
            "mode": s.mode,
            "status": s.status,
            "call_status": s.call_status,
            "initiated_by_role": s.initiated_by_role,
            "branch": s.branch,
            "building_name": s.building_name,
            "room_no": s.room_no,
            "lessor_name": s.lessor_name,
            "current_step": s.current_step,
            "tenant_name": str((s.terms or {}).get("tenant_name", "")),
        }
        for s in sessions
        if s.status == "IN_PROGRESS"
    ]

    demo_accounts: List[Dict[str, Any]] = [
        {
            "group": "관리인",
            "badge": "👑 총괄 관리인 전용 앱",
            "label": "본사 총괄 관리인 (admin / 0202)",
            "identifier": "admin",
            "password": "0202",
            "target_desc": "/rent3_admin/",
        },
        {
            "group": "통합허브",
            "badge": "🌐 Data Pool",
            "label": "통합 Data Pool 관제 (admin / 0202)",
            "identifier": "admin",
            "password": "0202",
            "target_hint": "pool",
            "target_desc": "/rent3/pool.html",
        },
    ]
    for lsr in lessors[:4]:
        demo_accounts.append({
            "group": "임대인",
            "badge": f"🏢 임대인({lsr.branch})",
            "label": f"{lsr.name}",
            "identifier": lsr.username or lsr.phone or lsr.id,
            "password": "1212",
            "target_desc": f"/rent3_lessor/?branch={lsr.branch}",
        })
    active_tenants = [t for t in Tenant.all() if t.status == "ACTIVE" and t.room_no]
    for t in active_tenants[:4]:
        demo_accounts.append({
            "group": "임차인",
            "badge": f"🔑 임차인({t.room_no}호)",
            "label": f"{t.name} ({t.phone})",
            "identifier": t.phone or t.name,
            "password": "1212",
            "target_desc": f"/rent3_tenant/?tenant={t.id}",
        })

    occupied_count = sum(1 for r in rooms if r.is_occupied)
    return {
        "success": True,
        "stats": {
            "total_buildings": len(buildings),
            "total_rooms": len(rooms),
            "occupied_rooms": occupied_count,
            "vacant_published_count": len(listings),
            "active_contracts": len(contracts),
            "open_cosign_count": len(open_sessions),
            "pool_revision": meta.get("total_revision", 0),
        },
        "branches": [
            {
                "branch": b.branch,
                "building_name": b.building_name,
                "address": b.address,
                "manager_phone": b.manager_phone,
            }
            for b in buildings
        ],
        "listings": listings,
        "open_sessions": open_sessions,
        "demo_accounts": demo_accounts if demo_login_allowed(request) else [],  # 공통 비밀번호는 로컬에서만 안내
    }


@router.post("/inquiries")
def create_portal_inquiry(req: PortalInquiryReq) -> Dict[str, Any]:
    """OpenRent 포털에서 예비임차인이 임대인에게 직접 1:1 방문투어/문의 전송 (ops.json 실시간 동기화)."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    sender = req.sender_name.strip()
    msg_body = req.message.strip()
    if not sender or not msg_body:
        raise DomainError("성명과 문의 내용을 모두 입력해 주세요.")

    room = Room.first(branch=b_id, room_no=req.room_no.strip())
    prefix_parts: List[str] = []
    if req.visit_date.strip():
        prefix_parts.append(f"[방문투어 희망: {req.visit_date.strip()}]")
    if req.sender_phone.strip():
        prefix_parts.append(f"(연락처: {req.sender_phone.strip()})")
    composed = f"{' '.join(prefix_parts)} {msg_body}".strip()

    chat = DirectChat(
        branch=b_id,
        id_room=room.id if room else "",
        room_no=req.room_no.strip(),
        sender_role="tenant",
        sender_name=sender,
        message=composed,
    ).save("portal_inquiry_sent", app_source="rent3_portal")

    return {
        "success": True,
        "message": f"{req.room_no}호 임대인에게 1:1 직거래 방문문의가 실시간 전달되었습니다.",
        "chat": chat.model_dump(),
    }


@router.post("/cosign-open", dependencies=[Depends(contract_scope)])
def open_or_join_rent_now(req: PortalRentNowReq) -> Dict[str, Any]:
    """OpenRent 'Rent Now' 버튼 — 로그인한 당사자가 매물을 선택하여 상대방을 호출(CALLING)하고 공동계약 앱 URL을 반환."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    r_no = req.room_no.strip()
    role = "lessor" if req.caller_role == "lessor" else "tenant"
    t_name = req.tenant_name.strip()
    t_phone = req.tenant_phone.strip()
    if role == "tenant" and (not t_name or not t_phone):
        raise DomainError("계약자 성명과 휴대폰 번호를 입력해 주세요.")

    sess = CoSignSession.call_counterparty(
        caller_role=role,
        branch=b_id,
        room_no=r_no,
        tenant_name=t_name,
        tenant_phone=t_phone,
        id_tenant=req.id_tenant,
        app_source="rent3_portal",
    )

    redirect_url = f"/rent3_contract/?role={role}&session={sess.id}"
    callee_ko = "임차인" if role == "lessor" else "임대인"
    return {
        "success": True,
        "session_id": sess.id,
        "session_code": sess.session_code,
        "call_status": sess.call_status,
        "redirect_url": redirect_url,
        "message": f"[{sess.building_name} {sess.room_no}호] {callee_ko}에게 공동계약 호출을 발송했습니다. 공동계약 화면으로 이동합니다.",
    }


def _client_ip(request: Request) -> str:
    return client_ip(request)


def _is_loopback(request: Request) -> bool:
    return is_local_request(request)


@router.post("/auth/login")
def portal_role_login(req: PortalLoginReq, request: Request) -> Dict[str, Any]:
    """로그인(무차별 대입 방어 포함): 계정별 5회·접속 주소별 30회 실패 시 15분 잠금."""
    who = (req.identifier or req.session_code or "").strip().lower()
    keys = [f"u:{who}", f"ip:{_client_ip(request)}"]
    ratelimit.check("login", keys)
    try:
        out = _portal_role_login(req, request)
    except (Unauthorized, DomainError, NotFound):
        ratelimit.fail("login", keys)
        raise
    ratelimit.clear("login", keys[:1])
    if out.get("role") in ("tenant", "lessor"):
        _cls = Lessor if out.get("role") == "lessor" else Tenant
        _t = _cls.get(str((out.get("user") or {}).get("id") or ""))
        if _t is not None:
            ql.unlock_all(_t)   # 아이디·비밀번호로 정상 로그인하면 잠긴 바로가기를 푼다
    sup.purge_expired()   # 스케줄러가 없으므로 로그인 때 기한 지난 지원 채팅·접속 기록을 지운다
    bk.daily()   # 하루 한 번 풀 전체 백업(별도 스레드, 실패해도 로그인에는 영향 없음)
    contract_chat.purge_expired()   # 계약방 대화: 취소 후 90일 · 체결 계약 종료 후 5년(10분에 한 번만 실제로 훑는다)
    out = _gate_consent(out)
    from apps.rent_dist._rent3_core.authz import unrevoke_token
    unrevoke_token(out.get("token", ""))
    return out


def _gate_consent(out: Dict[str, Any]) -> Dict[str, Any]:
    """동의 기록이 없는 임대인·임차인은 토큰을 받기 전에 동의 화면을 거친다(운영 모드). 관리인·공동계약 입장은 해당 없음."""
    role = str((out.get("user") or {}).get("role") or out.get("role") or "")
    if not cs.consent_required() or role not in ("lessor", "tenant", "prospect") or not out.get("token"):
        return out
    uid = str((out.get("user") or {}).get("id") or "")
    user = cs.find_user(role, uid)
    if user is None or cs.has_valid_consent(user):
        return out
    return {
        "success": True,
        "needs_consent": True,
        "consent_ticket": cs.hold_login(out, role, uid),
        "consent_version": cs.CONSENT_VERSION,
        "role": role,
        "user": {"name": (out.get("user") or {}).get("name", ""), "role": role},
        "message": "서비스를 이용하려면 개인정보 수집·이용에 동의해 주세요.",
    }


@router.post("/auth/consent")
def portal_consent(req: ConsentReq) -> Dict[str, Any]:
    """동의 화면에서 동의를 마치면 보류해 둔 로그인 결과(토큰 포함)를 돌려준다."""
    if not req.agree_privacy:
        raise DomainError(
            "개인정보 수집·이용에 동의하지 않으면 서비스를 이용할 수 없습니다.",
            hint="동의는 필수입니다. 원격 지원 접속 동의는 선택입니다.",
        )
    held = cs.take_login(req.ticket)
    if held is None:
        raise Unauthorized("동의 시간이 지났습니다. 다시 로그인해 주세요.")
    out, kind, uid = held
    user = cs.find_user(kind, uid)
    if user is None:
        raise NotFound("계정을 찾을 수 없습니다.")
    cs.record_consent(user, True, bool(req.agree_support))
    from apps.rent_dist._rent3_core.authz import unrevoke_token
    unrevoke_token(out.get("token", ""))
    return out


class SupportPostReq(BaseModel):
    body: str


def _support_user(request: Request) -> Any:
    from apps.rent_dist._rent3_core.authz import need_claims
    user = cs.user_from_claims(need_claims(request))
    if user is None:
        raise DomainError("임대인·임차인 계정으로 로그인해야 합니다.", hint="관리인은 관리인 앱의 원격 지원 화면을 쓰세요.")
    return user


@router.get("/support/thread")
def support_my_thread(request: Request) -> Dict[str, Any]:
    """내 지원 채팅(관리인과의 대화). 읽으면 관리인 글이 읽음 처리된다."""
    user = _support_user(request)
    sup.purge_expired()
    key = sup.thread_key(user)
    sup.mark_read(key, "user")
    return {
        "success": True,
        "messages": [sup.public_message(m) for m in sup.thread_messages(key)],
        "support_access": cs.support_allowed(user),
        "retention_days": sup.SUPPORT_CHAT_DAYS,
    }


@router.post("/support/messages")
def support_my_post(req: SupportPostReq, request: Request) -> Dict[str, Any]:
    user = _support_user(request)
    m = sup.post_user_message(user, req.body)
    return {"success": True, "message": "관리인에게 보냈습니다.", "item": sup.public_message(m)}


@router.get("/support/summary")
def support_my_summary(request: Request) -> Dict[str, Any]:
    """앱이 주기적으로 확인하는 가벼운 상태: 안 읽은 관리인 글 수, 관리인 접속 중 여부."""
    user = _support_user(request)
    key = sup.thread_key(user)
    return {"success": True, "unread": sup.unread_count(key, "user"), "admin_viewing": sup.admin_viewing(key)}


@router.get("/auth/consents")
def my_consents(request: Request) -> Dict[str, Any]:
    """내 동의 상태(개인정보 동의, 관리인 원격 지원 허용 여부)."""
    from apps.rent_dist._rent3_core.authz import need_claims
    user = cs.user_from_claims(need_claims(request))
    if user is None:
        return {"success": True, "applicable": False, "message": "관리인 계정에는 해당하지 않습니다."}
    return {"success": True, "applicable": True, **cs.consent_status(user)}


@router.post("/auth/consents/support")
def set_my_support_access(req: SupportToggleReq, request: Request) -> Dict[str, Any]:
    """관리인 원격 지원 접속 허용을 켜고 끈다(언제든)."""
    from apps.rent_dist._rent3_core.authz import need_claims
    user = cs.user_from_claims(need_claims(request))
    if user is None:
        raise DomainError("관리인 계정에는 해당하지 않습니다.")
    if cs.consent_required() and not cs.has_valid_consent(user):
        raise DomainError("먼저 개인정보 수집·이용에 동의해 주세요.", hint="다시 로그인하면 동의 화면이 나옵니다.")
    cs.set_support_access(user, req.allow)
    return {
        "success": True,
        "applicable": True,
        **cs.consent_status(user),
        "message": "관리인 원격 지원 접속을 허용했습니다." if req.allow else "관리인 원격 지원 접속을 허용하지 않도록 바꿨습니다.",
    }


# ── 스마트폰 바로가기 + 간편 비밀번호(PIN) 로그인 (입주 임차인) ──
class PinSetReq(BaseModel):
    pin: str


class DeviceReq(BaseModel):
    label: str = ""


def _me_tenant(request: Request) -> Any:
    """아이디·비밀번호로 로그인한 본인(입주 임차인, 또는 스위치가 켜진 임대인). PIN 토큰은 need_claims 에서 이미 막힌다."""
    from apps.rent_dist._rent3_core.authz import need_claims
    c = need_claims(request)
    t = ql.load_account(c)
    if t is None:
        raise DomainError("입주 임차인 계정에서만 쓸 수 있습니다.", hint="바로가기·간편 비밀번호는 입주한 임차인 전용입니다.")
    if not ql.eligible(t):
        if ql.kind_of(t) == "lessor":
            raise DomainError("임대인 간편 비밀번호는 아직 열리지 않았습니다.", hint="임차인 사용 검증이 끝난 뒤 열립니다.")
        raise DomainError("입주 중인 호실이 있어야 쓸 수 있습니다.")
    return t


def _start_url(t: Any, qk: str, extra: str = "") -> str:
    return f"/rent3_lessor/?qk={qk}{extra}" if ql.kind_of(t) == "lessor" else f"/rent3_tenant/?tenant={t.id}&qk={qk}{extra}"


@router.get("/shortcut/status")
def shortcut_status(request: Request) -> Dict[str, Any]:
    from apps.rent_dist._rent3_core.authz import need_claims
    c = need_claims(request)
    t = ql.load_account(c)
    if t is None:
        return {"success": True, "applicable": False}
    return {"success": True, "kind": ql.kind_of(t), "applicable": ql.eligible(t), "has_pin": bool(t.pin_hash), "via_pin": c.get("m") == "pin",
            "devices": ql.public_devices(t), "max_devices": ql.max_devices(t), "pin_len": ql.PIN_LEN}


@router.post("/shortcut/pin")
def shortcut_set_pin(req: PinSetReq, request: Request) -> Dict[str, Any]:
    t = _me_tenant(request)
    why = ql.weak_pin_reason(req.pin, t)
    if why:
        raise DomainError(why, hint="다른 4자리 숫자를 입력해 주세요.")
    ql.set_pin(t, req.pin)
    return {"success": True, "has_pin": True, "message": "간편 비밀번호를 저장했습니다."}


@router.post("/shortcut/devices")
def shortcut_add_device(req: DeviceReq, request: Request) -> Dict[str, Any]:
    """이 기기용 바로가기 키를 발급한다. 키 원문은 이 응답에서만 볼 수 있다(서버에는 해시만 남는다)."""
    t = _me_tenant(request)
    if not t.pin_hash:
        raise DomainError("먼저 간편 비밀번호를 정해 주세요.")
    if len(ql.devices_of(t)) >= ql.max_devices(t):
        raise DomainError(f"바로가기 기기는 {ql.max_devices(t)}대까지 등록할 수 있습니다.", hint="쓰지 않는 기기를 먼저 해제해 주세요.")
    dev, qk = ql.new_device(t, req.label)
    return {"success": True, "device": {"id": dev["id"], "label": dev["label"]}, "qk": qk,
            "start_url": _start_url(t, qk), "manifest_url": f"/api/rent3/pool/shortcut/manifest.json?qk={qk}"}


@router.post("/shortcut/devices/{device_id}/revoke")
def shortcut_revoke_device(device_id: str, request: Request) -> Dict[str, Any]:
    t = _me_tenant(request)
    if not ql.revoke_device(t, device_id):
        raise NotFound("등록된 기기를 찾을 수 없습니다.")
    return {"success": True, "devices": ql.public_devices(t), "message": "그 기기의 바로가기를 해제했습니다."}


def _qk_lookup(qk: str, request: Request) -> Any:
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("quickkey", keys)
    found = ql.find_by_qk(qk)
    if found is None:
        ratelimit.fail("quickkey", keys)
        raise NotFound("등록되지 않았거나 해제된 바로가기입니다.", hint="아이디·비밀번호로 로그인한 뒤 바로가기를 다시 만들어 주세요.")
    return found


@router.get("/shortcut/identify")
def shortcut_identify(qk: str, request: Request) -> Dict[str, Any]:
    """PIN 입력 화면에 보일 최소 정보(이름은 가린다)."""
    t, d = _qk_lookup(qk, request)
    if ql.kind_of(t) == "lessor":      # 임대인은 PIN 을 넣기 전에는 건물 이름도 보여 주지 않는다
        return {"success": True, "kind": "lessor", "masked_name": ql.mask_name(t.name), "room_no": "", "building_name": "",
                "eligible": ql.eligible(t), "locked": bool(d.get("locked")), "has_pin": bool(t.pin_hash), "pin_len": ql.PIN_LEN}
    b = Building.get(t.id_building) if t.id_building else None
    return {"success": True, "kind": "tenant", "masked_name": ql.mask_name(t.name), "room_no": t.room_no, "building_name": (b.building_name if b else ""),
            "eligible": ql.eligible(t), "locked": bool(d.get("locked")), "has_pin": bool(t.pin_hash), "pin_len": ql.PIN_LEN}


@router.get("/shortcut/manifest.json")
def shortcut_manifest(qk: str, request: Request):
    """홈 화면에 추가할 때 쓰는 매니페스트. start_url 에 qk 가 들어가므로 홈 화면에서 열면 이 기기의 PIN 화면으로 곧장 온다."""
    from fastapi.responses import JSONResponse
    t, _d = _qk_lookup(qk, request)
    lessor = ql.kind_of(t) == "lessor"
    b = Building.get(t.id_building) if (not lessor and t.id_building) else None
    label = "집주인 바로가기" if lessor else f"{(b.building_name if b else '내 집')} {t.room_no}호".strip()
    ver = "?v=1"
    icons = [
        {"src": f"/rent3_tenant/icons/icon-192.png{ver}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": f"/rent3_tenant/icons/icon-512.png{ver}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": f"/rent3_tenant/icons/icon-maskable-512.png{ver}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    body = {"name": label, "short_name": label[:12], "start_url": _start_url(t, qk), "scope": "/rent3_lessor/" if lessor else "/rent3_tenant/",
            "display": "standalone", "background_color": "#ffffff", "theme_color": "#2563eb", "lang": "ko", "icons": icons}
    return JSONResponse(body, media_type="application/manifest+json", headers={"Cache-Control": "no-store"})


# ── 입주 안내 카드(1회용 링크): 임차인이 비밀번호·PIN 을 정하고 들어온다 ──
class InviteAcceptReq(BaseModel):
    token: str
    password: str
    pin: str = ""
    agree_privacy: bool = False


@router.get("/invite/identify")
def invite_identify(token: str, request: Request) -> Dict[str, Any]:
    t = _invite_lookup(token, request)
    b = Building.get(t.id_building) if t.id_building else None
    return {"success": True, "masked_name": ql.mask_name(t.name), "room_no": t.room_no, "building_name": (b.building_name if b else ""),
            "expires": t.invite_expires, "consent_required": cs.consent_required()}


def _invite_lookup(token: str, request: Request) -> Tenant:
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("invite", keys)
    t = inv.find(token)
    if t is None:
        ratelimit.fail("invite", keys)
        raise NotFound("사용할 수 없는 입주 안내 링크입니다.", hint="이미 사용했거나 기간(7일)이 지났습니다. 임대인에게 새 안내 카드를 요청해 주세요.")
    if not ql.eligible(t):
        raise DomainError("입주 중인 호실이 있어야 쓸 수 있는 링크입니다.")
    return t


@router.post("/auth/invite-accept")
def invite_accept(req: InviteAcceptReq, request: Request) -> Dict[str, Any]:
    """입주 안내 링크로 비밀번호(필수)·PIN(선택)을 정하고 로그인한다. 링크는 한 번만 쓸 수 있다."""
    t = _invite_lookup(req.token, request)
    pwd.validate_new_password(req.password)
    if cs.consent_required() and not req.agree_privacy:
        raise DomainError("개인정보 수집·이용에 동의해야 이용할 수 있습니다.", hint="동의는 필수입니다.")
    pin = (req.pin or "").strip()
    if pin:
        why = ql.weak_pin_reason(pin, t)
        if why:
            raise DomainError(why, hint="다른 4자리 숫자를 입력해 주세요(간편 비밀번호는 비워 두고 나중에 정해도 됩니다).")
    t.password = pwd.hash_password(req.password.strip())
    t.save(action="password_set_by_invite", app_source="rent3_invite")
    if req.agree_privacy:
        cs.record_consent(t, True, bool((t.consents or {}).get("support_access")))
    inv.consume(t)
    ql.unlock_all(t)
    extra: Dict[str, Any] = {}
    if pin:
        ql.set_pin(t, pin)
        dev, qk = ql.new_device(t, "내 휴대폰")
        extra = {"qk": qk, "start_url": f"/rent3_tenant/?tenant={t.id}&qk={qk}&welcome=1", "device": {"id": dev["id"]}}
    from apps.rent_dist._rent3_core.authz import unrevoke_token
    tok = tenant_token(t)
    unrevoke_token(tok)
    url = f"/rent3_tenant/?tenant={t.id}"
    return {
        "success": True, "role": "tenant", "role_label": f"입주 임차인 ({t.room_no}호)", "redirect_url": url, "token": tok,
        "user": {"id": t.id, "username": t.id, "name": t.name, "phone": t.phone, "role": "tenant", "role_label": f"입주 임차인 ({t.room_no}호)",
                 "branch": t.branch, "room_no": t.room_no, "tenant_id": t.id, "login_auth_verified": True, "redirect_url": url},
        "message": f"{t.name}님, 가입을 마쳤습니다.", **extra,
    }


class PinLoginReq(BaseModel):
    qk: str
    pin: str


@router.post("/auth/pin-login")
def pin_login(req: PinLoginReq, request: Request) -> Dict[str, Any]:
    """간편 비밀번호 로그인. 기기별 연속 5회 실패면 그 기기는 영구 잠금(아이디·비밀번호 로그인 시 해제)."""
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("pinlogin", keys)
    found = ql.find_by_qk(req.qk)
    if found is None:
        ratelimit.fail("pinlogin", keys)
        raise Unauthorized("등록되지 않았거나 해제된 바로가기입니다.", hint="아이디·비밀번호로 로그인한 뒤 바로가기를 다시 만들어 주세요.")
    t, d = found
    if d.get("locked"):
        raise Unauthorized("간편 비밀번호를 5번 틀려 이 바로가기가 잠겼습니다.", hint="아이디·비밀번호로 한 번 로그인하면 다시 쓸 수 있습니다.")
    lessor = ql.kind_of(t) == "lessor"
    if not ql.eligible(t):
        if lessor:
            raise Unauthorized("지금은 임대인 바로가기를 쓸 수 없습니다.", hint="아이디·비밀번호로 로그인해 주세요.")
        raise Unauthorized("현재 입주 중인 계정이 아니어서 바로가기를 쓸 수 없습니다.", hint="입주 중인 호실이 있을 때만 쓸 수 있습니다.")
    if not t.pin_hash or not pwd.verify(t.pin_hash, str(req.pin or "")):
        ratelimit.fail("pinlogin", keys)
        info = ql.record_failure(t, d["id"])
        if info["locked"]:
            raise Unauthorized("간편 비밀번호를 5번 틀려 이 바로가기가 잠겼습니다.", hint="아이디·비밀번호로 한 번 로그인하면 다시 쓸 수 있습니다.")
        raise Unauthorized(f"간편 비밀번호가 맞지 않습니다. (남은 시도 {info['remaining']}회)", hint="5번 틀리면 잠깁니다.")
    if cs.consent_required() and not cs.has_valid_consent(t):
        raise Unauthorized("개인정보 동의가 필요합니다.", hint="아이디·비밀번호로 한 번 로그인해 동의를 마친 뒤 이용해 주세요.")
    ratelimit.clear("pinlogin", keys)
    ql.record_success(t, d["id"])
    from apps.rent_dist._rent3_core.authz import unrevoke_token
    if lessor:
        tok = lessor_token(t, via_pin=True, device_id=d["id"])
        unrevoke_token(tok)
        url = f"/rent3_lessor/?branch={t.branch}"
        lbl = f"임대인 ({t.branch})"
        return {
            "success": True, "role": "lessor", "role_label": lbl, "redirect_url": url, "token": tok, "via_pin": True,
            "user": {"id": t.id, "username": t.username or t.id, "name": t.name, "phone": t.phone, "role": "lessor", "role_label": lbl,
                     "branch": t.branch, "login_auth_verified": True, "via_pin": True, "redirect_url": url},
            "message": f"{ql.mask_name(t.name)}님 환영합니다.",
        }
    tok = tenant_token(t, via_pin=True, device_id=d["id"])
    unrevoke_token(tok)
    url = f"/rent3_tenant/?tenant={t.id}"
    return {
        "success": True, "role": "tenant", "role_label": f"입주 임차인 ({t.room_no}호)", "redirect_url": url, "token": tok, "via_pin": True,
        "user": {"id": t.id, "username": t.id, "name": t.name, "phone": t.phone, "role": "tenant", "role_label": f"입주 임차인 ({t.room_no}호)",
                 "branch": t.branch, "room_no": t.room_no, "tenant_id": t.id, "login_auth_verified": True, "via_pin": True, "redirect_url": url},
        "message": f"{ql.mask_name(t.name)}님 환영합니다.",
    }


def _portal_role_login(req: PortalLoginReq, request: Request) -> Dict[str, Any]:
    """통합 본인인증 로그인 — 계정 역할(관리인/임대인/입주임차인/예비임차인) 및 공동계약 호출 목적에 맞춰 전용 URL을 반환."""
    ensure_pool_seeded()
    ensure_default_admins()

    # 1. 레거시 호환: 세션 ID/코드 직접 입장
    if req.login_mode == "cosign":
        code = req.session_code.strip()
        if not code:
            raise DomainError("공동계약 세션 ID를 입력해 주세요.")
        sess = CoSignSession.first(session_code=code) or CoSignSession.get(code)
        if not sess:
            raise NotFound(
                f"세션 '{code}'에 해당하는 공동계약을 찾을 수 없습니다.",
                hint="각자 본인 계정으로 로그인한 뒤 공동계약 화면에서 매물을 선택해 상대를 호출·응답해 주세요.",
            )
        role_param = "lessor" if req.cosign_role == "lessor" else "tenant"
        role_ko = "임대인" if role_param == "lessor" else "임차인"
        target_url = f"/rent3_contract/?role={role_param}&session={sess.id}"
        display_name = (
            sess.lessor_name
            if role_param == "lessor"
            else (str((sess.terms or {}).get("tenant_name", "")) or "계약 임차인")
        )
        return {
            "success": True,
            "role": "cosign",
            "role_label": f"공동계약 ({role_ko})",
            "redirect_url": target_url,
            "token": "",
            "user": {
                "id": sess.id,
                "username": sess.session_code,
                "name": display_name,
                "role": "cosign",
                "role_label": f"공동계약 ({role_ko})",
                "branch": sess.branch,
                "room_no": sess.room_no,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{sess.building_name} {sess.room_no}호 공동계약 화면({role_ko})으로 이동합니다.",
        }

    # 2. 일반 통합 본인인증 로그인 (관리인 / 임대인 / 임차인 / 예비임차인)
    ident = req.identifier.strip()
    ident_low = ident.lower()
    ident_num = _digits(ident)
    pw = req.password.strip()
    is_contract_hint = (req.target_hint or "").startswith("contract")
    demo = demo_login_allowed(request)

    if not ident or not pw:
        raise DomainError("아이디(또는 휴대폰 번호/성명)와 비밀번호를 모두 입력해 주세요.")

    # (A) 총괄 관리인 (Admin) 매칭
    matched_admin = next(
        (
            a for a in Admin.all()
            if a.username.lower() == ident_low
            or (a.email and a.email.lower() == ident_low)
            or (demo and a.name == ident)    # 성명 로그인은 시연 환경에서만(운영에서는 동명이인 충돌·추측 대입 위험)
            or (len(ident_num) >= 8 and _digits(a.phone) == ident_num)
        ),
        None,
    )
    if matched_admin:
        core_u = CoreUser.by_username(matched_admin.username)
        admin_pw = matched_admin.password or "0202"
        valid_pw = (core_u.check_password(pw) if core_u else False) or (
            demo and (pw in ("0202", "1212") or pw == admin_pw)
        )
        if not valid_pw:
            raise Unauthorized("비밀번호가 올바르지 않습니다.", hint="관리인 계정 비밀번호(0202)를 확인해 주세요.")
        tok = issue_token(core_u) if core_u else ""
        if is_contract_hint:
            target_url = _build_contract_target_url("lessor", req.target_hint, branch="main")
        elif req.target_hint == "pool":
            target_url = "/rent3/pool.html"
        else:
            target_url = "/rent3_admin/"
        role_lbl = "통합 Data Pool 관제" if req.target_hint == "pool" else "플랫폼 총괄 관리인 (rent3_admin)"
        return {
            "success": True,
            "role": "admin",
            "role_label": role_lbl,
            "redirect_url": target_url,
            "token": tok,
            "user": {
                "id": matched_admin.id,
                "username": matched_admin.username,
                "name": matched_admin.name,
                "phone": matched_admin.phone,
                "role": "admin",
                "role_label": role_lbl,
                "branch": "all",
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{matched_admin.name}님 본인인증 로그인 완료! {role_lbl} 페이지로 이동합니다.",
        }

    # (B) 임대인 / 건물주 (Lessor) 매칭
    matched_lessor = next(
        (
            lsr for lsr in Lessor.all()
            if (lsr.username and lsr.username.lower() == ident_low)
            or lsr.id.lower() == ident_low
            or (lsr.email and lsr.email.lower() == ident_low)
            or (demo and lsr.name == ident)
            or (len(ident_num) >= 8 and _digits(lsr.phone) == ident_num)
        ),
        None,
    )
    if matched_lessor:
        core_u = CoreUser.by_username(matched_lessor.username) if matched_lessor.username else None
        phone_tail = _digits(matched_lessor.phone)[-4:] if len(_digits(matched_lessor.phone)) >= 4 else ""
        custom_pw = matched_lessor.password
        stored_ok = bool(custom_pw) and custom_pw != "1212" and (demo or pw.lower() not in pwd.WEAK) and pwd.verify(custom_pw, pw)
        valid_pw = (core_u.check_password(pw) if core_u else False) or stored_ok or (
            demo and (pw == "1212" or (bool(custom_pw) and not pwd.is_hashed(custom_pw) and pw == custom_pw)
                      or (bool(phone_tail) and pw == phone_tail))
        )
        if valid_pw and stored_ok and not pwd.is_hashed(custom_pw):  # 예전 평문 → 해시로 전환
            matched_lessor.password = pwd.hash_password(pw)
            matched_lessor.save("password_hashed", app_source="rent3_portal")
        if not valid_pw:
            raise Unauthorized("임대인 비밀번호가 올바르지 않습니다.", hint="가입할 때 설정한 비밀번호를 확인해 주세요.")
        tok = lessor_token(matched_lessor)
        if is_contract_hint:
            target_url = _build_contract_target_url("lessor", req.target_hint, branch=matched_lessor.branch)
            dest_msg = "공동계약 호출·응답 화면으로 이동합니다."
        else:
            tab_q = ""
            if req.target_hint == "add_listing":
                tab_q = "&tab=rooms"
            elif req.target_hint == "broker":
                tab_q = "&tab=broker"
            target_url = f"/rent3_lessor/?branch={matched_lessor.branch}{tab_q}"
            dest_msg = "지점 임대인 관리 페이지로 이동합니다."
        role_lbl = f"임대인 ({matched_lessor.branch})"
        return {
            "success": True,
            "role": "lessor",
            "role_label": role_lbl,
            "redirect_url": target_url,
            "token": tok,
            "user": {
                "id": matched_lessor.id,
                "username": matched_lessor.username or matched_lessor.id,
                "name": matched_lessor.name,
                "phone": matched_lessor.phone,
                "role": "lessor",
                "role_label": role_lbl,
                "branch": matched_lessor.branch,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{matched_lessor.name}님(본인인증 완료) 환영합니다. {dest_msg}",
        }

    # (C) 임차인 / 예비임차인 (Tenant) 매칭 — 입주 ACTIVE 우선 탐색
    ordered_tenants = sorted(Tenant.all(), key=lambda x: (0 if x.status == "ACTIVE" else 1, x.id))
    matched_tenant = next(
        (
            t for t in ordered_tenants
            if t.id.lower() == ident_low
            or (t.email and t.email.lower() == ident_low)
            or (demo and t.name == ident)
            or (len(ident_num) >= 8 and _digits(t.phone) == ident_num)
        ),
        None,
    )
    if matched_tenant:
        phone_tail = _digits(matched_tenant.phone)[-4:] if len(_digits(matched_tenant.phone)) >= 4 else ""
        custom_pw = matched_tenant.password
        stored_ok = bool(custom_pw) and custom_pw != "1212" and (demo or pw.lower() not in pwd.WEAK) and pwd.verify(custom_pw, pw)
        valid_pw = stored_ok or (
            demo and (pw == "1212" or (bool(custom_pw) and not pwd.is_hashed(custom_pw) and pw == custom_pw)
                      or (bool(phone_tail) and pw == phone_tail))
        )
        if valid_pw and stored_ok and not pwd.is_hashed(custom_pw):
            matched_tenant.password = pwd.hash_password(pw)
            matched_tenant.save("password_hashed", app_source="rent3_portal")
        if not valid_pw:
            raise Unauthorized(
                "임차인 비밀번호가 올바르지 않습니다.",
                hint="가입할 때 설정한 비밀번호를 확인해 주세요.",
            )
        is_active_resident = matched_tenant.status == "ACTIVE" and bool(matched_tenant.current_room_id or matched_tenant.room_no)
        if is_active_resident:
            role_key = "tenant"
            role_lbl = f"입주 임차인 ({matched_tenant.room_no}호)"
            default_url = f"/rent3_tenant/?tenant={matched_tenant.id}"
        else:
            role_key = "prospect"
            role_lbl = "예비임차인"
            default_url = "/rent3_tenant/?view=listings"

        if is_contract_hint:
            target_url = _build_contract_target_url("tenant", req.target_hint, tenant_id=matched_tenant.id)
            dest_msg = "공동계약 호출·응답 화면으로 이동합니다."
        else:
            target_url = default_url
            dest_msg = f"{role_lbl} 전용 페이지로 이동합니다."

        return {
            "success": True,
            "role": role_key,
            "role_label": role_lbl,
            "redirect_url": target_url,
            "token": tenant_token(matched_tenant),
            "user": {
                "id": matched_tenant.id,
                "username": matched_tenant.id,
                "name": matched_tenant.name,
                "phone": matched_tenant.phone,
                "role": role_key,
                "role_label": role_lbl,
                "branch": matched_tenant.branch,
                "room_no": matched_tenant.room_no,
                "tenant_id": matched_tenant.id,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{matched_tenant.name}님(본인인증 완료) 환영합니다. {dest_msg}",
        }

    raise Unauthorized(
        "일치하는 사용자 계정을 찾을 수 없습니다.",
        hint="아이디·휴대폰 번호·성명을 다시 확인하거나 '회원가입' 탭에서 간편 본인인증 가입 후 이용해 주세요.",
    )


@router.post("/auth/logout")
def portal_logout(request: Request) -> Dict[str, Any]:
    """로그아웃: 이 토큰을 폐기한다(만료 전에도 더는 쓸 수 없다)."""
    from apps.rent_dist._rent3_core.authz import raw_token, revoke_token
    revoke_token(raw_token(request))
    return {"success": True, "message": "로그아웃되었습니다."}


class PasswordResetReq(BaseModel):
    identifier: str
    name: str
    phone: str
    birth: str
    code: str = ""
    new_password: str = ""


def _find_account(identifier: str):
    """재설정 대상 계정(임대인·임차인). 관리자는 서버 설정(BASS_SEED_PASSWORD)이나 관리 도구로만 바꾼다."""
    ident = identifier.strip().lower()
    num = _digits(identifier)
    for lsr in Lessor.all():
        if (lsr.username and lsr.username.lower() == ident) or lsr.id.lower() == ident or (len(num) >= 8 and _digits(lsr.phone) == num):
            return lsr
    for t in Tenant.all():
        if t.id.lower() == ident or (len(num) >= 8 and _digits(t.phone) == num):
            return t
    return None


def _reset_target(req: PasswordResetReq):
    """본인인증을 마친 계정이고 입력한 실명·생년월일·휴대폰이 그 인증과 같을 때만 대상. 아니면 None (존재 여부를 알리지 않는다)."""
    acct = _find_account(req.identifier)
    idn = (acct.identity if acct else None) or {}
    if not (acct and idn.get("verified")):
        return None
    try:
        birth6 = fr.normalize_birth(req.birth)
    except DomainError:
        return None
    same = (
        fr._norm_name(idn.get("name")) == fr._norm_name(req.name)
        and idn.get("birth6") == birth6
        and _digits(idn.get("phone")) == _digits(req.phone)
    )
    return acct if same else None


@router.post("/auth/password-reset/start")
def password_reset_start(req: PasswordResetReq, request: Request) -> Dict[str, Any]:
    """비밀번호 재설정 1단계: 본인인증 번호 발송. 계정이 있든 없든 같은 응답을 준다."""
    ensure_pool_seeded()
    keys = [f"u:{req.identifier.strip().lower()}", f"ip:{_client_ip(request)}"]
    ratelimit.check("reset", keys)
    ratelimit.fail("reset", keys)  # 인증번호 요청도 횟수로 센다
    if _reset_target(req) is not None:
        fr.provider(_is_loopback(request)).start(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth))
    return {"success": True, "message": "입력한 정보가 가입 때 본인인증한 정보와 같으면 인증번호를 보냅니다.",
            "hint": "시연용 인증번호는 000000 입니다." if fr.fake_allowed(_is_loopback(request)) else ""}


@router.post("/auth/password-reset/confirm")
def password_reset_confirm(req: PasswordResetReq, request: Request) -> Dict[str, Any]:
    """비밀번호 재설정 2단계: 인증번호 확인 후 새 비밀번호 저장 (해시). 로그인은 새 비밀번호로 다시 해야 한다."""
    ensure_pool_seeded()
    keys = [f"u:{req.identifier.strip().lower()}", f"ip:{_client_ip(request)}"]
    ratelimit.check("reset", keys)
    acct = _reset_target(req)
    try:
        if acct is None:
            raise DomainError("입력한 정보가 본인인증한 정보와 다릅니다.",
                              hint="본인인증을 마친 계정만 직접 재설정할 수 있습니다. 그렇지 않으면 본사에 문의해 주세요.")
        pwd.validate_new_password(req.new_password)
        fr.provider(_is_loopback(request)).confirm(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth), req.code)
    except DomainError:
        ratelimit.fail("reset", keys)
        raise
    acct.password = pwd.hash_password(req.new_password.strip())
    acct.save("password_reset_by_identity", app_source="rent3_portal")
    ratelimit.clear("reset", keys[:1])
    ratelimit.clear("login", [f"u:{req.identifier.strip().lower()}"])
    return {"success": True, "message": "비밀번호를 바꿨습니다. 새 비밀번호로 로그인해 주세요."}


@router.post("/auth/register")
def portal_register(req: PortalRegisterReq, request: Request) -> Dict[str, Any]:
    """OpenRent 포털 간편 본인인증 회원가입 — 예비임차인 또는 신규 임대인을 Data Pool에 등록하고 해당 역할 페이지로 즉시 연결."""
    ensure_pool_seeded()
    name = req.name.strip()
    phone = req.phone.strip()
    pw = req.password.strip() or "1212"
    is_contract_hint = (req.target_hint or "").startswith("contract")
    if not demo_login_allowed(request):
        pwd.validate_new_password(pw)
    ip_key = [f"ip:{_client_ip(request)}"]
    ratelimit.check("register", ip_key)  # 같은 주소에서 가입을 반복하면 잠깐 막는다
    ratelimit.fail("register", ip_key)
    if not name or not phone:
        raise DomainError("성명과 휴대폰 번호는 필수 입력 항목입니다.")

    phone_digits = _digits(phone)
    if len(phone_digits) < 8:
        raise DomainError("올바른 휴대폰 번호 형식을 입력해 주세요.", hint="예: 010-1234-5678")
    if cs.consent_required() and not req.agree_privacy:
        raise DomainError("개인정보 수집·이용 동의는 필수입니다.", hint="가입 화면의 '(필수) 개인정보 수집·이용' 항목에 체크해 주세요.")
    new_consents = cs.new_consents(req.agree_privacy, req.agree_support)

    if req.role == "lessor":
        uname = (req.username.strip() or f"host_{phone_digits[-4:]}").lower()
        if Lessor.first(username=uname) or Admin.first(username=uname):
            raise Conflict(f"이미 사용 중인 아이디('{uname}')입니다.", hint="다른 아이디를 입력해 주세요.")
        branch_code = normalize_branch_id(req.branch.strip() or uname)
        b_name = req.building_name.strip() or f"{name} 직거래 하우스"
        b_addr = req.address.strip() or "서울특별시 직거래 등록 건물"

        lsr = Lessor(
            username=uname,
            name=name,
            phone=phone,
            email=req.email.strip(),
            branch=branch_code,
            status="PENDING_APPROVAL",  # 가맹 심사 통과 전에는 매물 공개·직거래 불가
            consents=new_consents,
            password=pwd.hash_password(pw),
        ).save("portal_lessor_registered", app_source="rent3_portal")

        if not Building.first(branch=branch_code):
            Building(
                branch=branch_code,
                building_name=b_name,
                address=b_addr,
                id_lessor=lsr.id,
                manager_phone=phone,
            ).save("portal_building_registered", app_source="rent3_portal")

        target_url = (
            _build_contract_target_url("lessor", req.target_hint, branch=branch_code)
            if is_contract_hint
            else f"/rent3_lessor/?branch={branch_code}&tab=rooms"
        )
        return {
            "success": True,
            "role": "lessor",
            "role_label": f"임대인 ({branch_code})",
            "redirect_url": target_url,
            "token": lessor_token(lsr),
            "user": {
                "id": lsr.id,
                "username": lsr.username,
                "name": lsr.name,
                "phone": lsr.phone,
                "role": "lessor",
                "role_label": f"임대인 ({branch_code})",
                "branch": branch_code,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"임대인 가입이 접수되었습니다. [{b_name}] 가맹 심사가 끝나면 매물을 공개할 수 있어요. 그동안 호실 정보를 미리 준비해 두세요.",
        }

    # 예비임차인 (prospect) 가입
    existing_t = next((t for t in Tenant.all() if _digits(t.phone) == phone_digits), None)
    if existing_t:
        raise Conflict("이미 등록된 휴대폰 번호입니다.", hint="로그인 탭에서 휴대폰 번호로 바로 로그인해 주세요.")

    new_t = Tenant(
        name=name,
        phone=phone,
        email=req.email.strip(),
        status="PROSPECT",
        consents=new_consents,
        password=pwd.hash_password(pw),
    ).save("portal_prospect_registered", app_source="rent3_portal")

    target_url = (
        _build_contract_target_url("tenant", req.target_hint, tenant_id=new_t.id)
        if is_contract_hint
        else "/rent3_tenant/?view=listings"
    )
    return {
        "success": True,
        "role": "prospect",
        "role_label": "예비임차인",
        "redirect_url": target_url,
        "token": tenant_token(new_t),
        "user": {
            "id": new_t.id,
            "username": new_t.id,
            "name": new_t.name,
            "phone": new_t.phone,
            "role": "prospect",
            "role_label": "예비임차인",
            "branch": "main",
            "room_no": "",
            "tenant_id": new_t.id,
            "login_auth_verified": True,
            "redirect_url": target_url,
        },
        "message": f"{name}님 본인인증 회원가입이 완료되었습니다! 전용 화면으로 이동합니다.",
    }


@router.get("/rooms/files", dependencies=ADMIN_ONLY)
def list_room_partition_files() -> Dict[str, Any]:
    """분리 보관된 개별 호실 JSON 파일 목록 조회."""
    p = pool()
    rooms_dir = p.rooms_dir
    files = []
    if os.path.exists(rooms_dir):
        for fname in sorted(os.listdir(rooms_dir)):
            if fname.endswith(".json"):
                fpath = os.path.join(rooms_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fp:
                        doc = json.load(fp)
                    files.append({
                        "file_name": fname,
                        "building_name": doc.get("building_name", ""),
                        "room_no": doc.get("room_no", ""),
                        "move_in_date": doc.get("move_in_date", ""),
                        "updated_at": doc.get("updated_at", ""),
                    })
                except Exception:
                    files.append({"file_name": fname})
    return {"count": len(files), "files": files}


@router.get("/rooms/files/{file_name}", dependencies=ADMIN_ONLY)
def get_room_partition_file(file_name: str) -> Dict[str, Any]:
    """개별 호실 분리 JSON 파일 단건 직접 조회 (O(1))."""
    p = pool()
    fpath = os.path.join(p.rooms_dir, file_name)
    if not os.path.exists(fpath):
        raise NotFound(f"호실 파일 '{file_name}'을 찾을 수 없습니다.")
    with open(fpath, "r", encoding="utf-8") as fp:
        return json.load(fp)
