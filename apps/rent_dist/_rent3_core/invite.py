"""
apps/rent_dist/_rent3_core/invite.py — 입주 안내 카드(1회용 초대 링크).

 - 임대인(자기 건물 호실)·관리인이 입주 임차인에게 발급한다. 링크 = `/rent3_tenant/?invite=<토큰>`, QR 로 인쇄해 건넨다.
 - 임차인이 링크에서 비밀번호(필수)·간편 비밀번호 PIN(선택)을 정하고 개인정보 동의를 하면 바로 로그인되고, 링크는 한 번 쓰면 없어진다.
 - 서버에는 토큰의 SHA-256 만 저장한다(원문은 발급 응답에서 한 번만). 유효기간 7일, 다시 발급하면 이전 링크는 무효.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from .models import Tenant

TTL_DAYS = 7


def _h(tok: str) -> str:
    return hashlib.sha256(str(tok).encode()).hexdigest()


def issue(t: Any, issued_by: str) -> str:
    tok = secrets.token_urlsafe(24)
    t.invite_hash = _h(tok)
    t.invite_expires = (datetime.now() + timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    t.invite_issued_by = issued_by
    t.save(action="invite_issued", app_source="rent3_invite")
    return tok


def find(tok: str) -> Optional[Any]:
    if not tok or len(str(tok)) < 16:
        return None
    h = _h(tok)
    now = datetime.now().isoformat(timespec="seconds")
    for t in Tenant.all():
        if t.invite_hash and t.invite_hash == h and t.invite_expires >= now:
            return t
    return None


def consume(t: Any) -> None:
    t.invite_hash, t.invite_expires = "", ""
    t.save(action="invite_used", app_source="rent3_invite")


def status(t: Any) -> Dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    pending = bool(t.invite_hash and t.invite_expires >= now)
    return {"pending": pending, "expires": t.invite_expires if pending else ""}
