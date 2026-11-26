"""
apps/rent_dist/rent3/routers/hub_access.py — 지원 채팅 · 스마트폰 바로가기/PIN · 1회용 초대 링크 · 기존 임차인 내 페이지 연결.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.auth import Unauthorized, User as CoreUser
from core.errors import DomainError, NotFound
from apps.rent_dist._rent3_core import (
    Admin,
    Building,
    Lessor,
    chatsessions as chat_sessions,
    connect as cn,
    consent as cs,
    devicebind as dbind,
    ensure_pool_seeded,
    franchise as fr,
    invite as inv,
    normalize_branch_id,
    passwords as pwd,
    quicklogin as ql,
    ratelimit,
    support as sup,
)
from apps.rent_dist._rent3_core.authz import lessor_token, need_claims, tenant_token, unrevoke_token
from apps.rent_dist._rent3_core.pool import now_iso
from ._hub_deps import _client_ip, demo_login_allowed

router = APIRouter()


class SupportPostReq(BaseModel):
    body: str


class PinSetReq(BaseModel):
    pin: str


class DeviceReq(BaseModel):
    label: str = ""


class PayAllowReq(BaseModel):
    allow: bool
    password: str = ""


class PinLoginReq(BaseModel):
    qk: str
    pin: str


class InviteAcceptReq(BaseModel):
    token: str
    password: str
    pin: str = ""
    agree_privacy: bool = False
    room_no: str = ""
    building_name: str = ""
    name: str = ""
    phone: str = ""
    lessor_name: str = ""
    proof: str = ""
    device_id: str = ""


class InviteVerifyReq(BaseModel):
    token: str
    room_no: str = ""
    building_name: str = ""
    name: str = ""
    phone: str = ""
    lessor_name: str = ""
    proof: str = ""


class ConnectReq(BaseModel):
    building_name: str = ""
    branch: str = ""          # 임대인이 보낸 링크의 지점 코드(있으면 건물명 대신 이것으로 건물을 정한다)
    room_no: str
    name: str
    phone: str


class ConnectStatusReq(BaseModel):
    request_id: str
    secret: str


# ── 관리인 지원 채팅(임대인·임차인용) ──
def _support_user(request: Request) -> Any:
    user = cs.user_from_claims(need_claims(request))
    if user is None:
        raise DomainError("임대인·임차인 계정으로 로그인해야 합니다.", hint="관리인은 관리인 앱의 원격 지원 화면을 쓰세요.")
    return user


def _admin_phone() -> str:
    """사용자가 관리인에게 전화할 번호: 지원 전화번호(SUPPORT_PHONE), 없으면 번호가 있는 총괄 관리인(superadmin) 계정의 연락처."""
    if sup.SUPPORT_PHONE:
        return sup.SUPPORT_PHONE
    admins = sorted(Admin.all(), key=lambda a: (a.role != "superadmin", a.created_at))
    return next(((a.phone or "").strip() for a in admins if (a.phone or "").strip()), "")


@router.get("/support/thread")
def support_my_thread(request: Request) -> Dict[str, Any]:
    """내 지원 채팅(관리인과의 대화). 읽으면 관리인 글이 읽음 처리된다."""
    user = _support_user(request)
    sup.purge_expired()
    key = sup.thread_key(user)
    sup.mark_read(key, "user")
    chat_sessions.touch(key, "support", key, "관리인", getattr(user, "branch", "main"))   # 채팅 세션(제목·시작 시각만 기록)
    return {
        "success": True,
        "messages": [sup.public_message(m) for m in sup.thread_messages(key)],
        "support_access": cs.support_allowed(user),
        "retention_days": sup.SUPPORT_CHAT_DAYS,
        "admin_phone": _admin_phone(),          # '관리인에게 전화' 단추용(본인이 문의하는 상대의 연락처)
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


# ── 스마트폰 바로가기 + 간편 비밀번호(PIN) 로그인 ──
def _me_tenant(request: Request) -> Any:
    """아이디·비밀번호로 로그인한 본인(입주 임차인, 또는 스위치가 켜진 임대인). PIN 토큰은 need_claims 에서 이미 막힌다."""
    c = need_claims(request)
    t = ql.load_account(c)
    if t is None:
        raise DomainError("입주 임차인 계정에서만 쓸 수 있습니다.", hint="바로가기·간편 비밀번호는 입주한 임차인 전용입니다.")
    if not ql.eligible(t):
        if ql.kind_of(t) == "lessor":
            raise DomainError("임대인 간편 비밀번호는 아직 열리지 않았습니다.", hint="임차인 사용 검증이 끝난 뒤 열립니다.")
        raise DomainError("입주 중인 호실이 있어야 쓸 수 있습니다.")
    return t


def _lessor_pw_ok(lsr: Any, pw: str, demo: bool) -> bool:
    """임대인 비밀번호 재확인(로그인과 같은 규칙). 수납 허용을 켤 때만 쓴다."""
    core_u = CoreUser.by_username(lsr.username) if lsr.username else None
    custom = lsr.password
    stored_ok = bool(custom) and custom != "1212" and (demo or pw.lower() not in pwd.WEAK) and pwd.verify(custom, pw)
    return bool(
        (core_u.check_password(pw) if core_u else False)
        or stored_ok
        or (demo and (pw == "1212" or (bool(custom) and not pwd.is_hashed(custom) and pw == custom)))
    )


def _start_url(t: Any, qk: str, extra: str = "") -> str:
    return f"/rent3_lessor/?qk={qk}{extra}" if ql.kind_of(t) == "lessor" else f"/rent3_tenant/?tenant={t.id}&qk={qk}{extra}"


def _qk_lookup(qk: str, request: Request) -> Any:
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("quickkey", keys)
    found = ql.find_by_qk(qk)
    if found is None:
        ratelimit.fail("quickkey", keys)
        raise NotFound("등록되지 않았거나 해제된 바로가기입니다.", hint="아이디·비밀번호로 로그인한 뒤 바로가기를 다시 만들어 주세요.")
    return found


@router.post("/shortcut/devices/{device_id}/pay-allow")
def shortcut_pay_allow(device_id: str, req: PayAllowReq, request: Request) -> Dict[str, Any]:
    """임대인: 이 기기에서 간편 비밀번호로도 수납(문자·직접·이체 확인)할 수 있게 켜고 끈다. 켤 때는 비밀번호를 다시 확인한다.
    수정·삭제는 어떤 경우에도 비밀번호 로그인에서만 된다. 하루 한도(25건·1,000만 원)가 적용된다."""
    t = _me_tenant(request)
    if ql.kind_of(t) != "lessor":
        raise DomainError("임대인 전용 설정입니다.")
    if req.allow:
        keys = [f"u:{t.id}", f"ip:{_client_ip(request)}"]
        ratelimit.check("payallow", keys)
        if not _lessor_pw_ok(t, str(req.password or ""), demo_login_allowed(request)):
            ratelimit.fail("payallow", keys)
            raise Unauthorized("비밀번호가 맞지 않습니다.", hint="수납 허용은 아이디·비밀번호를 다시 확인한 뒤 켤 수 있습니다.")
        ratelimit.clear("payallow", keys)
    if not ql.set_pay_ok(t, device_id, req.allow):
        raise NotFound("등록된 기기를 찾을 수 없습니다.")
    return {
        "success": True,
        "devices": ql.public_devices(t),
        "message": "이 기기에서 간편 비밀번호로 수납할 수 있게 했습니다(하루 25건·1,000만 원까지)." if req.allow else "이 기기의 간편 비밀번호 수납을 껐습니다.",
    }


@router.get("/shortcut/status")
def shortcut_status(request: Request) -> Dict[str, Any]:
    c = need_claims(request)
    t = ql.load_account(c)
    if t is None:
        return {"success": True, "applicable": False}
    return {
        "success": True,
        "kind": ql.kind_of(t),
        "applicable": ql.eligible(t),
        "has_pin": bool(t.pin_hash),
        "via_pin": c.get("m") == "pin",
        "devices": ql.public_devices(t),
        "max_devices": ql.max_devices(t),
        "pin_len": ql.PIN_LEN,
    }


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
    return {
        "success": True,
        "device": {"id": dev["id"], "label": dev["label"]},
        "qk": qk,
        "start_url": _start_url(t, qk),
        "manifest_url": f"/api/rent3/pool/shortcut/manifest.json?qk={qk}",
    }


@router.post("/shortcut/devices/{device_id}/revoke")
def shortcut_revoke_device(device_id: str, request: Request) -> Dict[str, Any]:
    t = _me_tenant(request)
    if not ql.revoke_device(t, device_id):
        raise NotFound("등록된 기기를 찾을 수 없습니다.")
    return {"success": True, "devices": ql.public_devices(t), "message": "그 기기의 바로가기를 해제했습니다."}


@router.get("/shortcut/identify")
def shortcut_identify(qk: str, request: Request) -> Dict[str, Any]:
    """PIN 입력 화면에 보일 최소 정보(이름은 가린다)."""
    t, d = _qk_lookup(qk, request)
    if ql.kind_of(t) == "lessor":      # 임대인은 PIN 을 넣기 전에는 건물 이름도 보여 주지 않는다
        return {
            "success": True,
            "kind": "lessor",
            "masked_name": ql.mask_name(t.name),
            "room_no": "",
            "building_name": "",
            "eligible": ql.eligible(t),
            "locked": bool(d.get("locked")),
            "has_pin": bool(t.pin_hash),
            "pin_len": ql.PIN_LEN,
        }
    b = Building.get(t.id_building) if t.id_building else None
    return {
        "success": True,
        "kind": "tenant",
        "masked_name": ql.mask_name(t.name),
        "room_no": t.room_no,
        "building_name": (b.building_name if b else ""),
        "eligible": ql.eligible(t),
        "locked": bool(d.get("locked")),
        "has_pin": bool(t.pin_hash),
        "pin_len": ql.PIN_LEN,
    }


@router.get("/shortcut/manifest.json")
def shortcut_manifest(qk: str, request: Request):
    """홈 화면에 추가할 때 쓰는 매니페스트. start_url 에 qk 가 들어가므로 홈 화면에서 열면 이 기기의 PIN 화면으로 곧장 온다."""
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
    body = {
        "name": label,
        "short_name": label[:12],
        "start_url": _start_url(t, qk),
        "scope": "/rent3_lessor/" if lessor else "/rent3_tenant/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#2563eb",
        "lang": "ko",
        "icons": icons,
    }
    return JSONResponse(body, media_type="application/manifest+json", headers={"Cache-Control": "no-store"})


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
    if lessor:
        tok = lessor_token(t, via_pin=True, device_id=d["id"])
        unrevoke_token(tok)
        url = f"/rent3_lessor/?branch={t.branch}"
        lbl = f"임대인 ({t.branch})"
        return {
            "success": True,
            "role": "lessor",
            "role_label": lbl,
            "redirect_url": url,
            "token": tok,
            "via_pin": True,
            "user": {
                "id": t.id,
                "username": t.username or t.id,
                "name": t.name,
                "phone": t.phone,
                "role": "lessor",
                "role_label": lbl,
                "branch": t.branch,
                "login_auth_verified": True,
                "via_pin": True,
                "redirect_url": url,
            },
            "message": f"{ql.mask_name(t.name)}님 환영합니다.",
        }
    tok = tenant_token(t, via_pin=True, device_id=d["id"])
    unrevoke_token(tok)
    url = f"/rent3_tenant/?tenant={t.id}"
    return {
        "success": True,
        "role": "tenant",
        "role_label": f"입주 임차인 ({t.room_no}호)",
        "redirect_url": url,
        "token": tok,
        "via_pin": True,
        "user": {
            "id": t.id,
            "username": t.id,
            "name": t.name,
            "phone": t.phone,
            "role": "tenant",
            "role_label": f"입주 임차인 ({t.room_no}호)",
            "branch": t.branch,
            "room_no": t.room_no,
            "tenant_id": t.id,
            "login_auth_verified": True,
            "via_pin": True,
            "redirect_url": url,
        },
        "message": f"{ql.mask_name(t.name)}님 환영합니다.",
    }


# ── 입주 안내 카드 / 임대인 암호 설정 링크(1회용 링크) ──
def _invite_lookup(token: str, request: Request) -> Any:
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("invite", keys)
    t = inv.find_any(token)
    if t is None:
        ratelimit.fail("invite", keys)
        raise NotFound("사용할 수 없는 입주 안내 링크입니다.", hint="이미 사용했거나 기간(7일)이 지났습니다. 새 안내 링크를 요청해 주세요.")
    if isinstance(t, Lessor):
        if t.status != "ACTIVE":
            raise DomainError("가맹 승인된 임대인 계정만 쓸 수 있는 링크입니다.")
        return t
    if not ql.eligible(t):
        raise DomainError("입주 중인 호실이 있어야 쓸 수 있는 링크입니다.")
    return t


def _invite_verify(t: Any, req: Any) -> None:
    """임차인(호실·이름·전화번호·임대인 이름) 또는 임대인(건물명·이름·전화번호·사업자/계좌 끝 4자리) 대조. 한 링크에서 5번 틀리면 15분 잠긴다."""
    keys = [f"tok:{hashlib.sha256(str(req.token).encode()).hexdigest()[:16]}"]
    ratelimit.check("invite", keys)
    if isinstance(t, Lessor):
        ok = cn.verify_lessor_identity(t, req.building_name or req.room_no, req.name, req.phone, req.proof or req.lessor_name)
        if not ok:
            ratelimit.fail("invite", keys)
            raise DomainError(
                "입력한 정보가 장부와 맞지 않습니다.",
                hint="건물명·본인 이름·본인 전화번호·사업자번호(또는 계좌번호) 끝 4자리를 다시 확인해 주세요(5번 틀리면 15분 동안 잠깁니다).",
            )
        ratelimit.clear("invite", keys)
        return
    if not cn.verify_identity(t, req.room_no, req.name, req.phone, req.lessor_name):
        ratelimit.fail("invite", keys)
        raise DomainError(
            "입력한 정보가 장부와 맞지 않습니다.",
            hint="호실·본인 이름·본인 전화번호·임대인 이름을 다시 확인해 주세요. 계속 안 되면 임대인에게 문의해 주세요(5번 틀리면 15분 동안 잠깁니다).",
        )
    ratelimit.clear("invite", keys)


@router.get("/invite/identify")
def invite_identify(token: str, request: Request) -> Dict[str, Any]:
    t = _invite_lookup(token, request)
    if isinstance(t, Lessor):
        b = Building.first(branch=normalize_branch_id(t.branch))
        return {
            "success": True,
            "kind": "lessor",
            "masked_name": ql.mask_name(t.name),
            "room_no": "",
            "building_name": (b.building_name if b else ""),
            "expires": t.invite_expires,
            "consent_required": cs.consent_required(),
        }
    b = Building.get(t.id_building) if t.id_building else None
    return {
        "success": True,
        "kind": "tenant",
        "masked_name": ql.mask_name(t.name),
        "room_no": t.room_no,
        "building_name": (b.building_name if b else ""),
        "expires": t.invite_expires,
        "consent_required": cs.consent_required(),
    }


@router.post("/invite/verify")
def invite_verify(req: InviteVerifyReq, request: Request) -> Dict[str, Any]:
    """비밀번호를 정하기 전 단계: 본인 확인만 한다(저장·로그인 없음)."""
    t = _invite_lookup(req.token, request)
    _invite_verify(t, req)
    return {"success": True, "verified": True, "message": "본인 확인이 되었습니다."}


@router.post("/auth/invite-accept")
def invite_accept(req: InviteAcceptReq, request: Request) -> Dict[str, Any]:
    """안내 링크로 비밀번호(필수)·PIN(선택)을 정하고 로그인한다. 링크는 한 번만 쓸 수 있다(권유 링크로 변경 시 기존 등록 기기 유지)."""
    t = _invite_lookup(req.token, request)
    _invite_verify(t, req)       # 서버에서도 다시 대조한다(앞 단계를 건너뛰고 바로 호출해도 통과 못 함)
    pwd.validate_new_password(req.password)
    if cs.consent_required() and not req.agree_privacy:
        raise DomainError("개인정보 수집·이용에 동의해야 이용할 수 있습니다.", hint="동의는 필수입니다.")
    pin = (req.pin or "").strip()
    if pin:
        why = ql.weak_pin_reason(pin, t)
        if why:
            raise DomainError(why, hint="다른 4자리 숫자를 입력해 주세요(간편 비밀번호는 비워 두고 나중에 정해도 됩니다).")
    now = now_iso()
    t.password = pwd.hash_password(req.password.strip())
    if not t.activated_at:
        t.activated_at = now
    t.password_changed_at = now
    if not (t.identity or {}).get("verified"):      # 문자·네이버 등 더 강한 인증이 이미 있으면 덮어쓰지 않는다
        t.identity = fr.invite_identity_record(t)
    t.save(action="password_set_by_invite", app_source="rent3_invite")
    dbind.register(t, req.device_id)      # 이 기기를 등록한다(권유 링크는 기존 등록 기기를 유지한 채 현재 기기를 추가)
    if req.agree_privacy:
        cs.record_consent(t, True, bool((t.consents or {}).get("support_access")))
    inv.consume(t)
    ql.unlock_all(t)
    if isinstance(t, Lessor):
        extra_lsr: Dict[str, Any] = {}
        if pin and ql.eligible(t):
            ql.set_pin(t, pin)
            dev, qk = ql.new_device(t, "내 휴대폰")
            extra_lsr = {"qk": qk, "start_url": f"/rent3_lessor/?branch={t.branch}&qk={qk}&welcome=1", "device": {"id": dev["id"]}}
        tok = lessor_token(t)
        unrevoke_token(tok)
        url = f"/rent3_lessor/?branch={t.branch}"
        lbl = f"임대인 ({t.branch})"
        return {
            "success": True,
            "role": "lessor",
            "role_label": lbl,
            "redirect_url": url,
            "token": tok,
            "user": {
                "id": t.id,
                "username": t.username or t.id,
                "name": t.name,
                "phone": t.phone,
                "role": "lessor",
                "role_label": lbl,
                "branch": t.branch,
                "login_auth_verified": True,
                "redirect_url": url,
            },
            "message": f"{t.name}님, 비밀번호 설정을 마쳤습니다.",
            **extra_lsr,
        }
    extra: Dict[str, Any] = {}
    if pin:
        ql.set_pin(t, pin)
        dev, qk = ql.new_device(t, "내 휴대폰")
        extra = {"qk": qk, "start_url": f"/rent3_tenant/?tenant={t.id}&qk={qk}&welcome=1", "device": {"id": dev["id"]}}
    tok = tenant_token(t)
    unrevoke_token(tok)
    url = f"/rent3_tenant/?tenant={t.id}"
    return {
        "success": True,
        "role": "tenant",
        "role_label": f"입주 임차인 ({t.room_no}호)",
        "redirect_url": url,
        "token": tok,
        "user": {
            "id": t.id,
            "username": t.id,
            "name": t.name,
            "phone": t.phone,
            "role": "tenant",
            "role_label": f"입주 임차인 ({t.room_no}호)",
            "branch": t.branch,
            "room_no": t.room_no,
            "tenant_id": t.id,
            "login_auth_verified": True,
            "redirect_url": url,
        },
        "message": f"{t.name}님, 가입을 마쳤습니다.",
        **extra,
    }


# ── 기존 임차인 '내 페이지 연결' (문자 인증 없이: 4가지 대조 → 임대인이 통화로 확인번호 확인 → 승인) ──
@router.post("/connect/request")
def connect_request(req: ConnectReq, request: Request) -> Dict[str, Any]:
    """기존 임차인이 건물·호실·성명·휴대폰을 입력해 연결을 요청한다. 맞으면 4자리 확인번호를 보여 주고(임대인에게 전화로 불러 준다),
    임대인이 승인하면 비밀번호를 정하는 입주 안내 화면으로 이어진다. 이미 가입한 호실은 덮어쓸 수 없다."""
    ensure_pool_seeded()
    keys = [f"ip:{_client_ip(request)}", f"u:room{cn._room(req.room_no)}"]
    ratelimit.check("connect", keys)
    t = cn.find_tenant(req.building_name, req.room_no, req.name, req.phone, branch=req.branch)
    if t is None:
        ratelimit.fail("connect", keys)
        raise DomainError(
            "입력한 정보가 입주자 정보와 일치하지 않습니다.",
            hint="건물명·호실·성함·휴대폰 번호를 다시 확인해 주세요. 계속 안 되면 임대인에게 문의하세요.",
        )
    if cn.activated(t):
        raise DomainError(
            "이미 가입이 끝난 호실입니다.",
            hint="로그인하거나, 비밀번호를 잊었다면 '비밀번호를 잊으셨나요? 본인 확인으로 내 페이지 찾기'(문자 인증)를 이용해 주세요.",
        )
    ratelimit.fail("connect", keys)   # 성공한 요청도 횟수로 센다(같은 주소에서 요청을 반복해 임대인을 괴롭히지 못하게)
    r, secret, code = cn.create(t)
    return {
        "success": True,
        "request_id": r.id,
        "secret": secret,
        "code": code,
        "room_no": t.room_no,
        "message": "연결을 요청했습니다. 임대인에게 전화해 아래 4자리 확인번호를 불러 주세요.",
        "hint": "임대인이 번호를 확인해 승인하면 이 화면에서 비밀번호를 정할 수 있습니다.",
    }


@router.get("/connect/info")
def connect_info(branch: str, request: Request) -> Dict[str, Any]:
    """연결 화면 머리(건물명·임대인 전화번호). 임대인이 보낸 링크의 지점 코드로 조회한다."""
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("connectinfo", keys)
    info = cn.public_info(branch)
    if info is None:
        ratelimit.fail("connectinfo", keys)
        raise NotFound("건물을 찾을 수 없습니다.", hint="임대인이 보낸 링크를 다시 확인해 주세요.")
    return {"success": True, **info}


@router.post("/connect/status")
def connect_status(req: ConnectStatusReq, request: Request) -> Dict[str, Any]:
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("connectstatus", keys)
    try:
        out = cn.check_status(req.request_id, req.secret)
    except DomainError:
        ratelimit.fail("connectstatus", keys)
        raise
    return {"success": True, **out}
