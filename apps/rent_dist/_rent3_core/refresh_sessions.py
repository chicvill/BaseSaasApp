"""
apps/rent_dist/_rent3_core/refresh_sessions.py — 장기 로그인 유지(Refresh Token) 저장소.

스마트폰·PC에서 한 번 로그인하면 12시간이 지나거나 서버가 재시작되어도 다시 로그인하지 않도록,
암호학적으로 무작위인 갱신 토큰(refresh_token)의 SHA-256 해시만 Data Pool 디렉터리
(`_refresh_sessions.json`)에 원자적으로 보관한다.

보안 원칙:
  - 디스크에는 원문 토큰을 절대 저장하지 않고 SHA-256 해시만 기록한다.
  - 갱신(/auth/refresh) 시 기존 해시를 즉시 폐기하고 새 갱신 토큰을 발급(1회용 로테이션)한다.
  - 명시적 로그아웃(/auth/logout) 시 즉시 폐기한다.
  - 비밀번호 변경·재설정 시 해당 계정의 모든 기기 갱신 세션을 일괄 폐기한다.
  - 간편 비밀번호(PIN) 로그인에는 발급하지 않는다(PIN 1시간/3시간 제한 유지).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional

_lock = threading.Lock()
MAX_SESSIONS_PER_USER = 10


def refresh_ttl_seconds(role: str = "") -> int:
    """기본 30일 유지(환경변수 RENT3_REFRESH_DAYS 로 조정 가능, 최소 1일·최대 180일)."""
    try:
        days = int(os.getenv("RENT3_REFRESH_DAYS", "30") or 30)
    except ValueError:
        days = 30
    days = max(1, min(180, days))
    if role == "admin":
        try:
            admin_days = int(os.getenv("RENT3_ADMIN_REFRESH_DAYS", str(min(days, 14))) or min(days, 14))
        except ValueError:
            admin_days = min(days, 14)
        days = max(1, min(days, admin_days))
    return days * 86400


def _sessions_path() -> str:
    from .pool import pool
    return os.path.join(pool().pool_dir, "_refresh_sessions.json")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(("rent3-refresh:" + str(raw or "")).encode("utf-8")).hexdigest()


def _norm_role(role: str) -> str:
    r = str(role or "").strip().lower()
    if r == "prospect":
        return "tenant"
    if r in ("superadmin", "admin"):
        return "admin"
    if r in ("lessor", "branch_admin"):
        return "lessor"
    return r


def _user_key(role: str, user_id: str) -> str:
    return f"{_norm_role(role)}:{str(user_id or '').strip()}"


def _load_unlocked() -> Dict[str, Dict[str, Any]]:
    path = _sessions_path()
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return {}
    except (OSError, ValueError):
        return {}
    now = int(time.time())
    return {
        k: v for k, v in data.items()
        if isinstance(v, dict) and int(v.get("expires_at", 0) or 0) > now
    }


def _save_unlocked(data: Dict[str, Dict[str, Any]]) -> None:
    path = _sessions_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)
    os.replace(tmp, path)


def _prune_user_limit(data: Dict[str, Dict[str, Any]], ukey: str) -> None:
    owned = [
        (k, int(v.get("last_used_at", v.get("created_at", 0)) or 0))
        for k, v in data.items()
        if v.get("user_key") == ukey
    ]
    if len(owned) <= MAX_SESSIONS_PER_USER:
        return
    owned.sort(key=lambda item: item[1])
    for old_k, _ in owned[: len(owned) - MAX_SESSIONS_PER_USER]:
        data.pop(old_k, None)


def issue_refresh(
    role: str,
    user_id: str,
    username: str = "",
    branch: str = "",
    device_id: str = "",
    mfa_ok: bool = False,
    ttl_seconds: Optional[int] = None,
) -> str:
    """새 장기 로그인 갱신 토큰을 발급하고 해시만 디스크에 기록한다."""
    norm_role = _norm_role(role)
    uid = str(user_id or "").strip()
    if not norm_role or not uid:
        return ""
    raw = secrets.token_urlsafe(32)
    h = _hash_token(raw)
    now = int(time.time())
    ttl = int(ttl_seconds) if ttl_seconds is not None else refresh_ttl_seconds(norm_role)
    ukey = _user_key(norm_role, uid)
    rec: Dict[str, Any] = {
        "user_key": ukey,
        "role": norm_role,
        "user_id": uid,
        "username": str(username or ""),
        "branch": str(branch or ""),
        "device_hash": hashlib.sha256(("r3dev:" + device_id).encode("utf-8")).hexdigest()[:24] if device_id else "",
        "mfa_ok": bool(mfa_ok),
        "created_at": now,
        "last_used_at": now,
        "expires_at": now + max(60, ttl),
    }
    with _lock:
        data = _load_unlocked()
        data[h] = rec
        _prune_user_limit(data, ukey)
        _save_unlocked(data)
    return raw


def peek_refresh(raw_token: str) -> Optional[Dict[str, Any]]:
    """갱신 토큰이 유효한지 조회한다(소비하지 않음)."""
    tok = str(raw_token or "").strip()
    if len(tok) < 16:
        return None
    h = _hash_token(tok)
    with _lock:
        data = _load_unlocked()
        rec = data.get(h)
        return dict(rec) if isinstance(rec, dict) else None


def rotate_refresh(
    raw_token: str,
    *,
    device_id: str = "",
    branch: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Optional[str]:
    """기존 갱신 토큰을 즉시 폐기하고 새 갱신 토큰으로 교체(슬라이딩 만료 연장)한다."""
    tok = str(raw_token or "").strip()
    if len(tok) < 16:
        return None
    old_h = _hash_token(tok)
    now = int(time.time())
    with _lock:
        data = _load_unlocked()
        old = data.pop(old_h, None)
        if not isinstance(old, dict):
            return None
        norm_role = _norm_role(str(old.get("role") or ""))
        ttl = int(ttl_seconds) if ttl_seconds is not None else refresh_ttl_seconds(norm_role)
        new_raw = secrets.token_urlsafe(32)
        new_h = _hash_token(new_raw)
        ukey = str(old.get("user_key") or _user_key(norm_role, str(old.get("user_id") or "")))
        data[new_h] = {
            "user_key": ukey,
            "role": norm_role,
            "user_id": str(old.get("user_id") or ""),
            "username": str(old.get("username") or ""),
            "branch": str(branch if branch is not None else (old.get("branch") or "")),
            "device_hash": (
                hashlib.sha256(("r3dev:" + device_id).encode("utf-8")).hexdigest()[:24]
                if device_id
                else str(old.get("device_hash") or "")
            ),
            "mfa_ok": bool(old.get("mfa_ok", False)),
            "created_at": int(old.get("created_at", now) or now),
            "last_used_at": now,
            "expires_at": now + max(60, ttl),
        }
        _prune_user_limit(data, ukey)
        _save_unlocked(data)
        return new_raw


def revoke_refresh(raw_token: str) -> bool:
    """단일 갱신 토큰을 폐기한다(로그아웃)."""
    tok = str(raw_token or "").strip()
    if len(tok) < 16:
        return False
    h = _hash_token(tok)
    with _lock:
        data = _load_unlocked()
        removed = data.pop(h, None) is not None
        if removed:
            _save_unlocked(data)
        return removed


def revoke_all_for_user(role: str, user_id: str) -> int:
    """특정 계정의 모든 기기 장기 로그인 세션을 일괄 폐기한다(비밀번호 변경·재설정)."""
    ukey = _user_key(role, user_id)
    if not ukey or ukey.endswith(":"):
        return 0
    with _lock:
        data = _load_unlocked()
        keys = [k for k, v in data.items() if v.get("user_key") == ukey]
        if not keys:
            return 0
        for k in keys:
            data.pop(k, None)
        _save_unlocked(data)
        return len(keys)
