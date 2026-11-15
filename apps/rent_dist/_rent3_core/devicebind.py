"""
apps/rent_dist/_rent3_core/devicebind.py — 비밀번호 로그인을 '등록된 기기'로 제한(임차인·임대인).

환경변수 RENT3_DEVICE_BIND=1 일 때만 적용한다(기본 꺼짐 — 켜기 전에 사용자에게 알린다).
 - 기기는 브라우저가 만든 무작위 값(device_id)이고 서버에는 해시만 저장한다.
 - 비밀번호가 맞고 등록된 기기면 통과. 등록 기기가 하나도 없는 계정(기존 가입자)은 첫 로그인 기기를 등록한다.
 - 등록 기기가 있는데 다른 기기면 비밀번호가 맞아도 거절한다 → 임대인에게 전화해 '재등록 링크'를 받아 새로 등록(등록 취소 때 기기 목록이 비워진다).
 - 입주 안내 링크 가입(invite-accept)·재설정 때 그 기기를 등록한다. 기기는 최대 MAX_DEVICES 대(넘으면 가장 오래된 것부터 밀려난다).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from core.auth import Unauthorized

MAX_DEVICES = 3


def enabled() -> bool:
    return os.getenv("RENT3_DEVICE_BIND", "0").strip().lower() in ("1", "true", "yes", "on")


def _h(device_id: str) -> str:
    return hashlib.sha256(("r3dev:" + str(device_id)).encode()).hexdigest()


def valid_id(device_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{24,64}", str(device_id or "")))


def register(acct: Any, device_id: str) -> None:
    """이 기기를 등록한다(이미 있으면 그대로). 스위치와 상관없이 기록해 두어, 나중에 켜도 가입자가 막히지 않게 한다."""
    if not valid_id(device_id):
        return
    h = _h(device_id)
    cur = list(getattr(acct, "login_devices", None) or [])
    if h in cur:
        return
    acct.login_devices = (cur + [h])[-MAX_DEVICES:]
    acct.save(action="login_device_registered", app_source="rent3_devicebind")


def enforce(acct: Any, device_id: str, who: str = "임차인") -> None:
    """비밀번호가 맞은 뒤 호출. 허용되지 않은 기기면 Unauthorized."""
    if not enabled():
        register(acct, device_id)      # 꺼져 있어도 기록해 둔다
        return
    cur = list(getattr(acct, "login_devices", None) or [])
    if not valid_id(device_id):
        raise Unauthorized("이 기기를 확인할 수 없습니다.", hint="브라우저를 새로고침한 뒤 다시 시도해 주세요(저장 공간을 막아 둔 시크릿 창은 쓸 수 없습니다).")
    if not cur or _h(device_id) in cur:
        register(acct, device_id)
        return
    raise Unauthorized("등록되지 않은 새 기기입니다.",
                       hint=("임대인에게 전화해 본인 확인을 받은 뒤 '재등록 링크'를 문자로 받아 다시 가입해 주세요." if who == "임차인"
                             else "관리인에게 본인 확인을 요청해 주세요."))
