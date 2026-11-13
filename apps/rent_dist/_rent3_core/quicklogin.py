"""
apps/rent_dist/_rent3_core/quicklogin.py — 스마트폰 바로가기 + 간편 비밀번호(PIN) 로그인 (입주 임차인 전용).

 - 입주한 임차인(status == ACTIVE, room_no 배정)이 4자리 숫자 PIN 을 정하고, 기기마다 바로가기 키(qk)를 받는다.
   서버에는 PIN 해시와 qk 의 SHA-256 만 남기고 qk 원문은 발급할 때 한 번만 보여 준다.
 - qk 만으로는 로그인할 수 없다. 같은 qk 로 연속 5번 틀리면 그 기기는 **영구 잠금**되며, 아이디·비밀번호로 로그인하면 풀린다.
 - PIN 으로 들어온 토큰에는 m="pin" 이 표시되어, 법적·민감 행위는 서버가 막는다(authz.pin_guard).
 - 퇴실(status != ACTIVE 또는 room_no 없음)하면 바로가기는 즉시 거부된다.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import os

from .models import Lessor, Tenant
from .pool import now_iso

PIN_LEN = 4
MAX_FAILS = 5
MAX_DEVICES = 5            # 임차인
LESSOR_MAX_DEVICES = 2     # 임대인(본인 폰 + 태블릿 정도)


def lessor_pin_enabled() -> bool:
    """임대인 간편 비밀번호 사용 스위치(RENT3_LESSOR_PIN=1). 임차인 실사용 검증이 끝나기 전에는 꺼 둔다."""
    return os.getenv("RENT3_LESSOR_PIN", "").strip().lower() in ("1", "true", "on", "yes")


def kind_of(a: Any) -> str:
    return "lessor" if isinstance(a, Lessor) else "tenant"


def max_devices(a: Any) -> int:
    return LESSOR_MAX_DEVICES if kind_of(a) == "lessor" else MAX_DEVICES


def eligible(t: Any) -> bool:
    """바로가기를 쓸 수 있는 계정인가. 임차인: 입주 중(퇴실하면 거짓). 임대인: 스위치가 켜져 있고 가맹 승인(ACTIVE) 상태."""
    if t is None:
        return False
    if kind_of(t) == "lessor":
        return lessor_pin_enabled() and getattr(t, "status", "") == "ACTIVE"
    return bool(getattr(t, "status", "") == "ACTIVE" and getattr(t, "room_no", ""))


def load_account(c: Dict[str, Any]) -> Any:
    """토큰 클레임(u, r)으로 임차인·임대인 계정을 찾는다."""
    u, r = str(c.get("u", "")), c.get("r")
    if r == "tenant" and u.startswith("tenant:"):
        return Tenant.get(u.removeprefix("tenant:"))
    if r == "lessor" and u.startswith("lessor:"):
        return Lessor.get(u.removeprefix("lessor:"))
    return None


def _kh(qk: str) -> str:
    return hashlib.sha256(str(qk).encode()).hexdigest()


def mask_name(name: str) -> str:
    """홍길동 → 홍*동, 남궁민수 → 남**수, 두 글자는 홍*."""
    n = (name or "").strip()
    if len(n) <= 1:
        return n
    if len(n) == 2:
        return n[0] + "*"
    return n[0] + "*" * (len(n) - 2) + n[-1]


def weak_pin_reason(pin: str, tenant: Any = None) -> Optional[str]:
    """설정을 거부할 약한 번호의 이유(없으면 None)."""
    p = str(pin or "")
    if not (p.isdigit() and len(p) == PIN_LEN):
        return f"간편 비밀번호는 숫자 {PIN_LEN}자리여야 합니다."
    if len(set(p)) == 1:
        return "같은 숫자를 반복한 번호는 쓸 수 없습니다 (예: 0000, 7777)."
    ds = [int(c) for c in p]
    steps = {ds[i + 1] - ds[i] for i in range(len(ds) - 1)}
    if steps in ({1}, {-1}):
        return "연속된 숫자는 쓸 수 없습니다 (예: 1234, 4321)."
    if p in ("0000", "1004", "2580", "1212", "0202", "1357", "2468", "8282"):
        return "너무 흔한 번호입니다. 다른 번호를 골라 주세요."
    phone = "".join(ch for ch in str(getattr(tenant, "phone", "") or "") if ch.isdigit())
    if phone and p == phone[-PIN_LEN:]:
        return "휴대폰 번호 끝 4자리는 쓸 수 없습니다."
    return None


def devices_of(t: Any) -> List[Dict[str, Any]]:
    return list(getattr(t, "quick_devices", None) or [])


def public_devices(t: Any) -> List[Dict[str, Any]]:
    return [{"id": d["id"], "label": d.get("label", ""), "created_at": d.get("created_at", ""), "last_used_at": d.get("last_used_at", ""),
             "locked": bool(d.get("locked")), "failed": int(d.get("failed", 0))} for d in devices_of(t)]


def set_pin(t: Any, pin: str) -> None:
    from . import passwords as pwd
    t.pin_hash = pwd.hash_password(pin)
    for d in devices_of(t):               # 본인이 비밀번호로 로그인한 상태에서 새로 정했으므로 잠금은 푼다
        d["failed"], d["locked"] = 0, False
    t.quick_devices = devices_of(t)
    t.save(action="quick_pin_set", app_source="rent3_portal")


def new_device(t: Any, label: str) -> Tuple[Dict[str, Any], str]:
    qk = secrets.token_urlsafe(24)
    dev = {"id": f"qd_{uuid4().hex[:8]}", "kh": _kh(qk), "label": (label or "내 휴대폰").strip()[:30] or "내 휴대폰",
           "created_at": now_iso(), "last_used_at": "", "failed": 0, "locked": False}
    t.quick_devices = devices_of(t) + [dev]
    t.save(action="quick_device_added", app_source="rent3_portal")
    return dev, qk


def revoke_device(t: Any, device_id: str) -> bool:
    devs = devices_of(t)
    keep = [d for d in devs if d["id"] != device_id]
    if len(keep) == len(devs):
        return False
    t.quick_devices = keep
    t.save(action="quick_device_revoked", app_source="rent3_portal")
    return True


def find_by_qk(qk: str) -> Optional[Tuple[Any, Dict[str, Any]]]:
    if not qk or len(str(qk)) < 16:
        return None
    h = _kh(qk)
    for t in list(Tenant.all()) + list(Lessor.all()):
        for d in devices_of(t):
            if d.get("kh") == h:
                return t, d
    return None


def record_failure(t: Any, device_id: str) -> Dict[str, Any]:
    """틀린 횟수를 올리고, MAX_FAILS 번째에 그 기기를 잠근다. 남은 시도 수와 잠김 여부를 돌려준다."""
    devs = devices_of(t)
    info = {"remaining": 0, "locked": False}
    for d in devs:
        if d["id"] == device_id:
            d["failed"] = int(d.get("failed", 0)) + 1
            if d["failed"] >= MAX_FAILS:
                d["locked"] = True
            info = {"remaining": max(0, MAX_FAILS - d["failed"]), "locked": bool(d["locked"])}
    t.quick_devices = devs
    t.save(action="quick_pin_failed", app_source="rent3_portal")
    return info


def record_success(t: Any, device_id: str) -> None:
    devs = devices_of(t)
    for d in devs:
        if d["id"] == device_id:
            d["failed"], d["last_used_at"] = 0, now_iso()
    t.quick_devices = devs
    t.save(action="quick_pin_login", app_source="rent3_portal")


def revoke_all(t: Any) -> int:
    """분실·도난 대응: 이 계정의 간편 비밀번호와 바로가기 기기를 모두 없앤다. 이미 받은 PIN 토큰도 바로 무효가 된다. 해제한 기기 수를 돌려준다."""
    n = len(devices_of(t))
    t.pin_hash, t.quick_devices = "", []
    t.save(action="quick_revoked_all", app_source="rent3_portal")
    return n


def unlock_all(t: Any) -> None:
    """아이디·비밀번호 로그인에 성공하면 잠긴 바로가기를 푼다."""
    devs = devices_of(t)
    if any(d.get("locked") or d.get("failed") for d in devs):
        for d in devs:
            d["failed"], d["locked"] = 0, False
        t.quick_devices = devs
        t.save(action="quick_unlocked_by_login", app_source="rent3_portal")
