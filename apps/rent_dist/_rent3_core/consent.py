"""
apps/rent_dist/_rent3_core/consent.py — 가입·로그인 동의 기록 (개인정보 수집·이용, 관리인 원격 지원 접속).

 - 개인정보 수집·이용 동의: 필수. 운영 모드에서는 동의 기록이 없는 사용자는 로그인할 때 동의 화면을 거쳐야 한다.
 - 관리인 원격 지원 접속 동의: 선택. 가입·첫 로그인 때 정하고, 메뉴에서 언제든 켜고 끌 수 있다.
 - 동의 문구(개인정보 처리방침)가 바뀌면 CONSENT_VERSION 을 올려 다시 받는다.

운영 여부: RENT3_CONSENT_REQUIRED 가 있으면 그 값('0' 이면 끔), 없으면 RENT3_DEMO_SEED=0(실제 운영)일 때만 켠다.
시연·개발 모드(시드 데이터 사용)에서는 시드 사용자에게 동의 기록이 없으므로 꺼 둔다.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

from .models import Lessor, Tenant
from .pool import now_iso

CONSENT_VERSION = "2026-11-11"
_PENDING_TTL = 600  # 동의 화면에서 머무를 수 있는 시간(초)

_pending: Dict[str, Tuple[float, Dict[str, Any], str, str]] = {}
_lock = threading.Lock()


def consent_required() -> bool:
    v = os.environ.get("RENT3_CONSENT_REQUIRED")
    if v is not None:
        return v.strip() != "0"
    return os.environ.get("RENT3_DEMO_SEED", "1").strip() == "0"


def find_user(kind: str, user_id: str) -> Optional[Any]:
    """kind: lessor | tenant(prospect 포함)"""
    if kind == "lessor":
        return Lessor.get(user_id)
    if kind in ("tenant", "prospect"):
        return Tenant.get(user_id)
    return None


def user_from_claims(c: Dict[str, Any]) -> Optional[Any]:
    """토큰의 u 값('lessor:<id>' / 'tenant:<id>')으로 사용자 레코드를 찾는다. 관리인이면 None."""
    u = str(c.get("u", ""))
    if ":" not in u:
        return None
    kind, uid = u.split(":", 1)
    return find_user(kind, uid)


def has_valid_consent(user: Any) -> bool:
    c = getattr(user, "consents", None) or {}
    return bool(c.get("privacy_version") == CONSENT_VERSION and c.get("privacy_at"))


def new_consents(privacy: bool, support: bool) -> Dict[str, Any]:
    """가입 때 만드는 동의 기록. 개인정보 동의가 없으면 빈 기록."""
    if not privacy:
        return {}
    now = now_iso()
    return {"privacy_version": CONSENT_VERSION, "privacy_at": now, "support_access": bool(support), "support_at": now}


def record_consent(user: Any, privacy: bool, support: bool, app_source: str = "rent3_portal") -> Any:
    user.consents = new_consents(privacy, support)
    return user.save(action="consent_recorded", app_source=app_source)


def set_support_access(user: Any, allow: bool, app_source: str = "rent3_portal") -> Any:
    c = dict(user.consents or {})
    c["support_access"] = bool(allow)
    c["support_at"] = now_iso()
    user.consents = c
    return user.save(action="support_access_changed", app_source=app_source)


def support_allowed(user: Any) -> bool:
    """관리인 원격 지원 접속을 허용한 사용자인가 (개인정보 동의도 유효해야 한다)."""
    return bool(user is not None and has_valid_consent(user) and (user.consents or {}).get("support_access"))


def consent_status(user: Any) -> Dict[str, Any]:
    c = getattr(user, "consents", None) or {}
    return {
        "privacy_agreed": has_valid_consent(user),
        "privacy_version": c.get("privacy_version", ""),
        "privacy_at": c.get("privacy_at", ""),
        "support_access": bool(c.get("support_access")),
        "support_at": c.get("support_at", ""),
        "current_version": CONSENT_VERSION,
    }


# ── 동의 전 로그인 보류 ──
# 로그인에 성공했지만 동의 기록이 없는 사용자는 토큰을 받기 전에 동의 화면을 거친다.
# 로그인 결과를 서버 메모리에 잠시 맡겨 두고(비밀번호는 다시 받지 않는다) 확인표만 돌려준다.
def hold_login(out: Dict[str, Any], kind: str, user_id: str) -> str:
    ticket = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if v[0] < now]:
            _pending.pop(k, None)
        _pending[ticket] = (now + _PENDING_TTL, out, kind, user_id)
    return ticket


def take_login(ticket: str) -> Optional[Tuple[Dict[str, Any], str, str]]:
    with _lock:
        item = _pending.pop(str(ticket or ""), None)
    if not item or item[0] < time.time():
        return None
    return item[1], item[2], item[3]
