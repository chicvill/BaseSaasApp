"""
apps/rent4/backend/finder.py — 아이디 찾기 · 비밀번호 찾기 (로그인 화면).

    1. 구글 본인 확인 (oauth purpose=find) → 그 구글 계정을 미리 연결해 둔 사람만 (사람 고정)
       → 10분짜리 찾기 권한 (토큰 해시만 저장) → find.html#f=<토큰>
    2. 아이디 찾기   : 이름 · 휴대폰 번호 · 계약된 부동산(예: 시크빌 203호) 이 맞으면 아이디를 알려 준다
       비밀번호 찾기 : 아이디까지 맞으면 새 비밀번호를 정한다 → 바로 로그인, 찾기 권한 만료
틀리면 어느 칸이 틀렸는지 알려 주지 않고, 5번 틀리면 그 권한은 잠긴다 (구글 확인부터 다시).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import ClassVar, Dict, Optional

from core.auth import User, issue_token
from core.errors import DomainError, Forbidden, Mismatch

from . import accounts
from .models import Building, Contract, LessorAccount, Room, TenantAccount
from .pool import PoolObj, now_iso

FIND_TTL, FIND_MAX_TRIES = 600, 5


class FindSession(PoolObj):
    collection: ClassVar[str] = "find_sessions"

    token_hash: str
    username: str                  # 구글 계정에 연결된 사람 (바꿀 수 없음)
    provider: str
    expires_at: float
    tries: int = 0
    pw_ok: bool = False            # 비밀번호 찾기 확인 통과 → 새 비밀번호를 정할 수 있음


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def open_session(provider: str, username: str) -> str:
    for old in FindSession.all():
        if old.expires_at < time.time() or old.username == username:
            old.delete()
    token = secrets.token_urlsafe(24)
    FindSession(token_hash=_hash(token), username=username, provider=provider,
                expires_at=time.time() + FIND_TTL).save()
    return token


def _session(token: str) -> FindSession:
    s = FindSession.first(token_hash=_hash(token or ""))
    if s is None or s.expires_at < time.time():
        raise Forbidden("본인 확인 시간이 지났습니다.", hint="구글 본인 확인부터 다시 해 주세요.")
    if s.tries >= FIND_MAX_TRIES:
        raise Forbidden("입력이 여러 번 틀려 잠겼습니다.", hint="구글 본인 확인부터 다시 해 주세요.")
    return s


def _norm(text: str) -> str:
    return re.sub(r"\s|호$", "", (text or "").strip()).lower()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _facts(username: str) -> Optional[Dict[str, object]]:
    """그 아이디의 이름 · 전화 · 부동산 (맞춰 볼 값). 임차인은 '건물+호실', 임대인은 '건물'."""
    acc = TenantAccount.first(username=username)
    if acc and acc.tenant_key:
        room = Room.first(branch=acc.branch, room=acc.room)
        b = Building.first(branch=acc.branch)
        if room and b:
            return {"name": room.tenant_name, "phone": room.phone,
                    "props": {_norm(f"{b.name}{acc.room}"), _norm(f"{b.code}{acc.room}")}}
    lessor = LessorAccount.first(username=username)
    if lessor:
        c = Contract.first(id=lessor.contract_id) if lessor.contract_id else None
        b = Building.first(branch=lessor.branch)
        if c and b:
            return {"name": c.party_name, "phone": c.party_phone, "props": {_norm(b.name), _norm(b.code)}}
    from .members import MemberAccount
    member = MemberAccount.first(username=username)
    if member:                                # 계약 전 회원: 부동산 칸은 비워 둔다
        return {"name": member.name, "phone": member.phone, "props": {""}}
    return None


def _check(s: FindSession, name: str, phone: str, prop: str, username: Optional[str] = None) -> None:
    f = _facts(s.username)
    ok = (f is not None and _norm(name) == _norm(str(f["name"])) and _digits(phone) == _digits(str(f["phone"]))
          and _norm(prop) in f["props"] and (username is None or username.strip() == s.username))
    if not ok:
        s.tries += 1
        s.save()
        left = FIND_MAX_TRIES - s.tries
        raise Mismatch("입력한 정보가 등록된 정보와 맞지 않습니다.",
                       hint=f"{left}번 더 틀리면 잠깁니다." if left > 0 else "구글 본인 확인부터 다시 해 주세요.")


def view(token: str) -> Dict:
    s = _session(token)
    return {"provider": s.provider, "expires_in": int(s.expires_at - time.time())}


def find_id(token: str, name: str, phone: str, prop: str) -> Dict:
    s = _session(token)
    _check(s, name, phone, prop)
    return {"username": s.username}


def verify_pw(token: str, username: str, name: str, phone: str, prop: str) -> Dict:
    s = _session(token)
    _check(s, name, phone, prop, username)
    s.pw_ok = True
    s.save()
    return {"username": s.username}


def set_pw(token: str, new_password: str) -> Dict:
    s = _session(token)
    if not s.pw_ok:
        raise DomainError("먼저 아이디와 정보를 확인해 주세요.")
    user = User.by_username(s.username)
    if user is None:
        raise Forbidden("계정을 찾을 수 없습니다.")
    accounts.validate_new_password(new_password)
    user.set_password(new_password).save("rent4_password_set_by_find")
    acc = TenantAccount.first(username=s.username) or LessorAccount.first(username=s.username)
    if acc is not None:
        acc.initial_code, acc.must_change = "", False
        acc.activated_at = acc.activated_at or now_iso()
        acc.save()
    accounts.expire_links(s.username)
    s.delete()
    return {"token": issue_token(user), "username": user.username, "home": accounts.whoami(user)["home"]}
