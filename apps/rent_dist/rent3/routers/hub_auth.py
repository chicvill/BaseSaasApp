"""
apps/rent_dist/rent3/routers/hub_auth.py — 포털 통합 로그인 · 관리인 2단계 인증 · 동의 · 로그아웃 · 비밀번호 재설정 · 회원가입.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.auth import Unauthorized, User as CoreUser, ensure_default_admins, issue_token
from core.errors import Conflict, DomainError, NotFound
from apps.rent_dist._rent3_core import (
    Admin,
    Building,
    CoSignSession,
    Lessor,
    Tenant,
    acquisition,
    backup as bk,
    consent as cs,
    contract_chat,
    date_today,
    devicebind as dbind,
    ensure_pool_seeded,
    franchise as fr,
    mfa as mfa_mod,
    normalize_branch_id,
    passwords as pwd,
    quicklogin as ql,
    ratelimit,
    support as sup,
)
from apps.rent_dist._rent3_core.authz import (
    issue_claims_token,
    lessor_token,
    need_claims,
    raw_token,
    revoke_token,
    tenant_token,
    unrevoke_token,
)
from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
from apps.rent_dist._rent3_core.pool import now_iso
from ._hub_deps import _build_contract_target_url, _client_ip, _digits, _is_loopback, demo_login_allowed

router = APIRouter()


class PortalLoginReq(BaseModel):
    login_mode: str = "account"          # account | cosign
    identifier: str = ""                 # 아이디 / 휴대폰 / 이메일 / 성명
    password: str = ""
    device_id: str = ""                  # 이 기기의 무작위 값(Bass.deviceId) — 등록된 기기 확인(devicebind.py)
    target_hint: str = ""                # "" | "pool" | "add_listing" | "broker" | "contract" | "contract:{branch}:{room_no}"
    session_code: str = ""
    cosign_role: str = "tenant"          # lessor | tenant


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
    acq: Dict[str, Any] = {}             # 유입 표식(ref·campaign·from·first_seen). 임대인 가입에만 기록하며 형식이 틀리면 조용히 버린다


class ConsentReq(BaseModel):
    ticket: str
    agree_privacy: bool = False
    agree_support: bool = False


class SupportToggleReq(BaseModel):
    allow: bool


class MfaVerifyReq(BaseModel):
    ticket: str
    code: str


class PasswordResetReq(BaseModel):
    identifier: str
    name: str
    phone: str
    birth: str = ""
    code: str = ""
    new_password: str = ""
    proof: str = ""      # 임대인(본인인증 이력이 없는 기존 계정): 사업자등록번호 또는 계좌번호 끝 4자리 이상


def _admin_login_response(admin: Any, core_u: Any, target_hint: str, mfa_ok: bool) -> Dict[str, Any]:
    if core_u is None:
        tok = ""
    elif mfa_ok:
        tok = issue_claims_token(core_u.username, core_u.role, core_u.branch, extra={"mfa": 1})      # 2단계 인증을 마친 토큰(표시 mfa=1)
    else:
        tok = issue_token(core_u)
    if (target_hint or "").startswith("contract"):
        target_url = _build_contract_target_url("lessor", target_hint, branch="main")
    elif target_hint == "pool":
        target_url = "/rent3/pool.html"
    else:
        target_url = "/rent3_admin/"
    role_lbl = "통합 Data Pool 관제" if target_hint == "pool" else "플랫폼 총괄 관리인 (rent3_admin)"
    return {
        "success": True,
        "role": "admin",
        "role_label": role_lbl,
        "redirect_url": target_url,
        "token": tok,
        "user": {
            "id": admin.id,
            "username": admin.username,
            "name": admin.name,
            "phone": admin.phone,
            "role": "admin",
            "role_label": role_lbl,
            "branch": "all",
            "login_auth_verified": True,
            "redirect_url": target_url,
        },
        "message": f"{admin.name}님 본인인증 로그인 완료! {role_lbl} 페이지로 이동합니다.",
    }


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
        raise DomainError("아이디(또는 휴대폰 번호·이메일)와 비밀번호를 모두 입력해 주세요.")

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
        if mfa_mod.active(matched_admin):      # 2단계 인증: 비밀번호만으로는 토큰을 주지 않는다
            return {
                "success": True,
                "mfa_required": True,
                "mfa_ticket": mfa_mod.issue_ticket(matched_admin, req.target_hint),
                "message": "2단계 인증 코드를 입력해 주세요(인증 앱의 6자리 숫자 또는 복구 코드).",
            }
        return _admin_login_response(matched_admin, core_u, req.target_hint, False)

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
            demo and (
                pw == "1212"
                or (bool(custom_pw) and not pwd.is_hashed(custom_pw) and pw == custom_pw)
                or (bool(phone_tail) and pw == phone_tail)
            )
        )
        if valid_pw and stored_ok and not pwd.is_hashed(custom_pw):  # 예전 평문 → 해시로 전환
            matched_lessor.password = pwd.hash_password(pw)
            matched_lessor.save("password_hashed", app_source="rent3_portal")
        if not valid_pw:
            raise Unauthorized("임대인 비밀번호가 올바르지 않습니다.", hint="가입할 때 설정한 비밀번호를 확인해 주세요.")
        if not demo:
            dbind.enforce(matched_lessor, req.device_id, "임대인")
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
            demo and (
                pw == "1212"
                or (bool(custom_pw) and not pwd.is_hashed(custom_pw) and pw == custom_pw)
                or (bool(phone_tail) and pw == phone_tail)
            )
        )
        if valid_pw and stored_ok and not pwd.is_hashed(custom_pw):
            matched_tenant.password = pwd.hash_password(pw)
            matched_tenant.save("password_hashed", app_source="rent3_portal")
        if not valid_pw:
            raise Unauthorized(
                "임차인 비밀번호가 올바르지 않습니다.",
                hint="가입할 때 설정한 비밀번호를 확인해 주세요.",
            )
        moved_out = matched_tenant.status == "MOVED_OUT"
        if moved_out:
            if not matched_tenant.readonly_until or date_today().isoformat() > matched_tenant.readonly_until:
                raise Unauthorized(
                    "퇴실 후 조회 기간이 끝나 더 이상 로그인할 수 없습니다.",
                    hint="연말정산 납부확인서는 퇴실한 다음 해 2월 말까지 조회할 수 있습니다. 다시 입주하시면 새 안내 링크로 등록해 주세요.",
                )
        elif not demo:
            dbind.enforce(matched_tenant, req.device_id, "임차인")
        is_active_resident = matched_tenant.status == "ACTIVE" and bool(matched_tenant.current_room_id or matched_tenant.room_no)
        if is_active_resident:
            role_key = "tenant"
            role_lbl = f"입주 임차인 ({matched_tenant.room_no}호)"
            default_url = f"/rent3_tenant/?tenant={matched_tenant.id}"
        else:
            role_key = "prospect"
            role_lbl = "예비임차인"
            default_url = "/rent3_tenant/?view=listings"
            if moved_out:
                role_lbl = f"퇴실 임차인 (조회 전용, {matched_tenant.readonly_until}까지)"
                default_url = f"/rent3_tenant/?tenant={matched_tenant.id}&view=moved"

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
        hint="아이디·휴대폰 번호·이메일을 다시 확인하거나 '회원가입' 탭에서 간편 본인인증 가입 후 이용해 주세요." + ("" if demo else " 이름(성명)만으로는 로그인할 수 없습니다."),
    )


@router.post("/auth/mfa-verify")
def mfa_verify(req: MfaVerifyReq, request: Request) -> Dict[str, Any]:
    """관리인 2단계 인증: 비밀번호 단계에서 받은 대기표와 인증 앱 코드(또는 복구 코드)를 맞추면 정식 토큰을 준다."""
    keys = [f"ip:{_client_ip(request)}"]
    ratelimit.check("mfa", keys)
    c = mfa_mod.read_ticket(req.ticket)
    admin = Admin.get(str(c.get("aid", "")))
    if admin is None or not admin.totp_enabled:
        raise Unauthorized("2단계 인증을 확인할 수 없습니다. 처음부터 다시 로그인해 주세요.")
    try:
        method = mfa_mod.check_code(admin, req.code)
    except DomainError:
        ratelimit.fail("mfa", keys)
        raise
    res = _admin_login_response(admin, CoreUser.by_username(admin.username), str(c.get("hint", "")), True)
    res["mfa_method"] = method
    if method == "recovery":
        res["message"] += f" (복구 코드를 사용했습니다 — 남은 복구 코드 {len(admin.recovery_hashes or [])}개. 폰을 바꿨다면 2단계 인증을 다시 설정하세요.)"
    return res


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
    ExpiryRenewalCheck.activate_due()   # 공개일이 된 사전 매물을 올린다
    contract_chat.purge_expired()   # 계약방 대화: 취소 후 90일 · 체결 계약 종료 후 5년(10분에 한 번만 실제로 훑는다)
    out = _gate_consent(out)
    unrevoke_token(out.get("token", ""))
    return out


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
    unrevoke_token(out.get("token", ""))
    return out


@router.get("/auth/consents")
def my_consents(request: Request) -> Dict[str, Any]:
    """내 동의 상태(개인정보 동의, 관리인 원격 지원 허용 여부)."""
    user = cs.user_from_claims(need_claims(request))
    if user is None:
        return {"success": True, "applicable": False, "message": "관리인 계정에는 해당하지 않습니다."}
    return {"success": True, "applicable": True, **cs.consent_status(user)}


@router.post("/auth/consents/support")
def set_my_support_access(req: SupportToggleReq, request: Request) -> Dict[str, Any]:
    """관리인 원격 지원 접속 허용을 켜고 끈다(언제든)."""
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


@router.post("/auth/logout")
def portal_logout(request: Request) -> Dict[str, Any]:
    """로그아웃: 이 토큰을 폐기한다(만료 전에도 더는 쓸 수 없다)."""
    revoke_token(raw_token(request))
    return {"success": True, "message": "로그아웃되었습니다."}


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


def _recovery_ok(acct: Any, req: PasswordResetReq) -> bool:
    """본인인증 이력이 없는 기존(이관) 계정의 '내 페이지 찾기': 장부에 적힌 실명·휴대폰과 같아야 하고(문자 인증번호는 그 번호로만 간다),
    입주 중인 임차인 / 승인된 임대인만 된다. 임대인은 사업자등록번호 또는 계좌번호 끝자리까지 맞아야 한다(임대인 계정은 노출되는 정보가 훨씬 크다)."""
    if len(_digits(acct.phone)) < 10 or _digits(acct.phone) != _digits(req.phone):
        return False
    if fr._norm_name(acct.name) != fr._norm_name(req.name):
        return False
    if isinstance(acct, Lessor):
        if acct.status != "ACTIVE":
            return False
        proof = _digits(req.proof)
        known = [d for d in (_digits(acct.business_number), _digits(acct.account_number)) if len(d) >= 4]
        return len(proof) >= 4 and any(d.endswith(proof) for d in known)
    return ql.eligible(acct)


def _birth6(req: PasswordResetReq) -> str:
    return fr.normalize_birth(req.birth) if (req.birth or "").strip() else "000000"


def _reset_target(req: PasswordResetReq):
    """본인인증 이력이 있는 계정은 입력한 실명·생년월일·휴대폰이 그 인증과 같을 때만 대상(예전과 같음).
    이력이 없는 기존 계정은 _recovery_ok 규칙(장부의 실명·휴대폰 + 임대인은 추가 확인)으로만 대상. 아니면 None (존재 여부를 알리지 않는다)."""
    acct = _find_account(req.identifier)
    if acct is None:
        return None
    idn = acct.identity or {}
    if not idn.get("verified"):
        return acct if _recovery_ok(acct, req) else None
    try:
        birth6 = fr.normalize_birth(req.birth)
    except DomainError:
        return None
    same = (
        fr._norm_name(idn.get("name")) == fr._norm_name(req.name)
        and idn.get("birth6") == birth6
        and _digits(str(idn.get("phone") or "")) == _digits(req.phone)
    )
    return acct if same else None


def _reset_keys(req: PasswordResetReq, request: Request) -> List[str]:
    """시도 제한 키: 아이디별, 접속 주소별, 그리고 휴대폰 번호별(아이디를 바꿔 가며 같은 번호로 문자를 계속 보내게 하는 것을 막는다)."""
    keys = [f"u:{req.identifier.strip().lower()}", f"ip:{_client_ip(request)}"]
    ph = _digits(req.phone)
    if ph:
        keys.append(f"u:ph{ph}")
    return keys


@router.post("/auth/password-reset/start")
def password_reset_start(req: PasswordResetReq, request: Request) -> Dict[str, Any]:
    """비밀번호 재설정 1단계: 본인인증 번호 발송. 계정이 있든 없든 같은 응답을 준다."""
    ensure_pool_seeded()
    keys = _reset_keys(req, request)
    ratelimit.check("reset", keys)
    ratelimit.fail("reset", keys)  # 인증번호 요청도 횟수로 센다
    if _reset_target(req) is not None:
        fr.provider(_is_loopback(request)).start(req.name.strip(), req.phone.strip(), _birth6(req))
    return {
        "success": True,
        "message": "입력한 정보가 가입 때 본인인증한 정보와 같으면 인증번호를 보냅니다.",
        "hint": "시연용 인증번호는 000000 입니다." if fr.fake_allowed(_is_loopback(request)) else "",
    }


@router.post("/auth/password-reset/confirm")
def password_reset_confirm(req: PasswordResetReq, request: Request) -> Dict[str, Any]:
    """비밀번호 재설정 2단계: 인증번호 확인 후 새 비밀번호 저장 (해시). 로그인은 새 비밀번호로 다시 해야 한다."""
    ensure_pool_seeded()
    keys = _reset_keys(req, request)
    ratelimit.check("reset", keys)
    acct = _reset_target(req)
    try:
        if acct is None:
            raise DomainError(
                "입력한 정보가 본인인증한 정보와 다릅니다.",
                hint="본인인증을 마친 계정만 직접 재설정할 수 있습니다. 그렇지 않으면 본사에 문의해 주세요.",
            )
        pwd.validate_new_password(req.new_password)
        fr.provider(_is_loopback(request)).confirm(req.name.strip(), req.phone.strip(), _birth6(req), req.code)
    except DomainError:
        ratelimit.fail("reset", keys)
        raise
    by_record = not (acct.identity or {}).get("verified")
    now = now_iso()
    acct.password = pwd.hash_password(req.new_password.strip())
    if not getattr(acct, "activated_at", ""):
        acct.activated_at = now
    acct.password_changed_at = now
    acct.save("password_reset_by_recovery" if by_record else "password_reset_by_identity", app_source="rent3_portal")
    ql.revoke_all(acct)   # 비밀번호를 새로 정하면 이전에 등록한 바로가기·간편 비밀번호는 모두 해제(분실 폰 대비) — 새 비밀번호로 로그인해 다시 만든다
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
            acq=acquisition.record(req.acq),
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
