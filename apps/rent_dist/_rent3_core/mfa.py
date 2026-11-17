"""
apps/rent_dist/_rent3_core/mfa.py — 관리인 2단계 인증(TOTP, RFC 6238).

 - 인증 앱(Google Authenticator·Microsoft Authenticator·Authy 등)이 30초마다 만드는 6자리 숫자를 비밀번호 다음에 한 번 더 입력한다. 문자(SMS) 요금·외부 서비스가 필요 없다.
 - 켠 관리인은 비밀번호만으로 토큰을 받지 못한다: 로그인은 짧은 '2단계 대기표'(5분, 이 표로는 아무 API도 못 씀)를 주고, 코드를 맞춘 뒤에야 `mfa=1` 표시가 붙은 정식 토큰을 준다.
 - **우회 차단**: 플랫폼 코어의 일반 로그인(/auth/login)은 이 검사를 모르지만, 2단계를 켠 관리인의 토큰에 `mfa` 표시가 없으면 rent3 의 모든 관리인 API가 거절한다(authz.claims_from·not_revoked).
 - 같은 코드를 두 번 쓸 수 없고(마지막으로 쓴 30초 구간 기록), 5번 틀리면 15분 잠긴다(ratelimit).
 - 복구 코드 8개(한 번씩만, 해시로 저장)를 켤 때 한 번 보여 준다. 폰을 잃으면 복구 코드로 들어와 끄고 다시 설정한다. 복구 코드도 없으면 다른 최고관리자가 해제하거나 서버에서 scripts/reset_admin_mfa.py 를 쓴다.
 - 비밀 키(totp_secret)는 디스크에서 암호화(fieldcrypto)되고, 어떤 화면·API 에도 설정 때 한 번을 빼고 내려가지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from core.auth import Unauthorized
from core.errors import DomainError

from . import ratelimit
from .authz import issue_claims_token
from .models import Admin
from .pool import now_iso

STEP = 30
DIGITS = 6
ISSUER = "Rent3"
MFA_TICKET_SECONDS = 300


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_at(secret_b32: str, ts: float, digits: int = DIGITS) -> str:
    return _hotp(secret_b32, int(ts // STEP), digits)


def match_step(secret_b32: str, code: str, now: Optional[float] = None, window: int = 1) -> Optional[int]:
    """코드가 맞으면 맞은 30초 구간 번호, 아니면 None (앞뒤 1구간 = 시계 오차 ±30초 허용)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    cur = int((now if now is not None else time.time()) // STEP)
    for s in range(cur - window, cur + window + 1):
        if hmac.compare_digest(_hotp(secret_b32, s), code):
            return s
    return None


def otpauth_uri(username: str, secret_b32: str) -> str:
    return f"otpauth://totp/{quote(ISSUER)}:{quote(username)}?secret={secret_b32}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def _h(s: str) -> str:
    return hashlib.sha256(("rent3-mfa-recovery:" + s.strip().lower().replace("-", "")).encode()).hexdigest()


def admin_by_username(username: str) -> Optional[Admin]:
    return Admin.first(username=str(username or ""))


def emergency_off() -> bool:
    """서버 환경변수 RENT3_ADMIN_MFA_DISABLE=1 이면 2단계 인증을 모두 건너뛴다 — 폰·복구 코드를 모두 잃고 서버 터미널도 없을 때의 비상구(Coolify 환경변수 변경 후 재시작).
    켜 둔 동안은 비밀번호만으로 로그인되므로 복구한 뒤 반드시 지운다."""
    import os
    return os.getenv("RENT3_ADMIN_MFA_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def active(admin: Optional[Admin]) -> bool:
    return bool(admin and admin.totp_enabled and admin.totp_secret and not emergency_off())


def enrolled(username: str) -> bool:
    return active(admin_by_username(username))


def blocked_without_mfa(claims: Optional[Dict[str, Any]]) -> bool:
    """2단계 인증을 켠 관리인 계정의 토큰인데 mfa 표시가 없으면 True(= 코어 일반 로그인으로 받은 토큰 등).
    코어(require_role)는 토큰의 역할이 아니라 토큰의 사용자 이름(u)으로 계정을 찾으므로, 역할이 아니라 u 로 판단한다(임대인·임차인 토큰의 u 는 'lessor:…'·'tenant:…')."""
    if not claims or claims.get("mfa"):
        return False
    u = str(claims.get("u", ""))
    return bool(u) and ":" not in u and enrolled(u)


def status(admin: Admin) -> Dict[str, Any]:
    return {"enabled": bool(admin.totp_enabled and admin.totp_secret), "pending": bool(admin.totp_secret and not admin.totp_enabled),
            "recovery_left": len(admin.recovery_hashes or []), "enabled_at": admin.totp_enabled_at}


def enroll_start(admin: Admin) -> Dict[str, Any]:
    if admin.totp_enabled:
        raise DomainError("이미 2단계 인증이 켜져 있습니다.", hint="다시 설정하려면 먼저 현재 코드로 끈 뒤 새로 설정해 주세요.")
    admin.totp_secret = new_secret()
    admin.totp_enabled = False
    admin.totp_last_step = 0
    admin.save(action="admin_mfa_enroll_started", app_source="rent3_mfa")
    return {"secret": admin.totp_secret, "otpauth_uri": otpauth_uri(admin.username, admin.totp_secret)}


def _new_recovery() -> List[str]:
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(8)]


def enroll_confirm(admin: Admin, code: str) -> Dict[str, Any]:
    if admin.totp_enabled:
        raise DomainError("이미 2단계 인증이 켜져 있습니다.")
    if not admin.totp_secret:
        raise DomainError("먼저 [설정 시작]을 눌러 비밀 키를 만들어 주세요.")
    _throttle_check(admin.username)
    step = match_step(admin.totp_secret, code)
    if step is None:
        _throttle_fail(admin.username)
        raise DomainError("코드가 맞지 않습니다.", hint="인증 앱에 표시된 6자리 숫자를 입력해 주세요(30초마다 바뀝니다). 폰 시계가 자동 설정인지도 확인해 주세요.")
    codes = _new_recovery()
    admin.totp_enabled, admin.totp_last_step, admin.totp_enabled_at = True, step, now_iso()
    admin.recovery_hashes = [_h(c) for c in codes]
    admin.save(action="admin_mfa_enabled", app_source="rent3_mfa")
    _throttle_clear(admin.username)
    return {"recovery_codes": codes}


def _key(username: str) -> List[str]:
    return [f"u:{str(username).lower()}"]


def _throttle_check(username: str) -> None:
    ratelimit.check("mfa", _key(username))


def _throttle_fail(username: str) -> None:
    ratelimit.fail("mfa", _key(username))


def _throttle_clear(username: str) -> None:
    ratelimit.clear("mfa", _key(username))


def check_code(admin: Admin, code: str, allow_recovery: bool = True) -> str:
    """로그인·끄기 때 코드 확인. 'totp' 또는 'recovery' 를 돌려주고, 틀리면 DomainError(실패 횟수 제한)."""
    _throttle_check(admin.username)
    raw = (code or "").strip()
    step = match_step(admin.totp_secret, raw) if admin.totp_secret else None
    if step is not None:
        if step <= int(admin.totp_last_step or 0):                        # 같은 구간(또는 이전)의 코드는 다시 쓸 수 없다
            _throttle_fail(admin.username)
            raise DomainError("이미 사용한 코드입니다.", hint="다음 코드(30초 뒤)를 입력해 주세요.")
        admin.totp_last_step = step
        admin.save(action="admin_mfa_code_used", app_source="rent3_mfa")
        _throttle_clear(admin.username)
        return "totp"
    if allow_recovery and raw and not raw.isdigit():
        hh = _h(raw)
        if hh in (admin.recovery_hashes or []):
            admin.recovery_hashes = [x for x in admin.recovery_hashes if x != hh]
            admin.save(action="admin_mfa_recovery_used", app_source="rent3_mfa")
            _throttle_clear(admin.username)
            return "recovery"
    _throttle_fail(admin.username)
    raise DomainError("코드가 맞지 않습니다.", hint="인증 앱의 6자리 숫자 또는 복구 코드를 입력해 주세요(5번 틀리면 15분 잠깁니다).")


def disable(admin: Admin, code: str) -> None:
    if not admin.totp_enabled:
        raise DomainError("2단계 인증이 켜져 있지 않습니다.")
    check_code(admin, code)
    reset(admin, "admin_mfa_disabled")


def reset(admin: Admin, action: str = "admin_mfa_reset") -> None:
    admin.totp_secret, admin.totp_enabled, admin.totp_last_step, admin.recovery_hashes, admin.totp_enabled_at = "", False, 0, [], ""
    admin.save(action=action, app_source="rent3_mfa")


def issue_ticket(admin: Admin, hint: str) -> str:
    """비밀번호를 통과한 관리인에게 주는 2단계 대기표(5분, 역할 'mfa' — 어떤 API 도 열지 못한다)."""
    # u 를 실제 사용자 이름으로 두면 코어가 이 표를 그 관리인의 토큰으로 받아들인다 → 어떤 계정과도 겹치지 않는 값('mfa:…')을 쓴다
    return issue_claims_token("mfa:" + admin.username, "mfa", None, ttl=MFA_TICKET_SECONDS, extra={"aid": admin.id, "hint": hint or ""})


def read_ticket(ticket: str) -> Dict[str, Any]:
    from core.auth import verify_token
    c = verify_token(ticket or "")
    if not c or c.get("r") != "mfa":
        raise Unauthorized("2단계 인증 시간이 지났습니다. 처음부터 다시 로그인해 주세요.")
    return c
