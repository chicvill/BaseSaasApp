"""
apps/rent4/backend/members.py — 회원가입 (매물에 계약을 신청하려는 새 임차인).

    1. 구글 본인 확인 (oauth purpose=signup)
         이미 연결된 구글 계정 = 기존 회원 → 가입 건너뛰고 바로 로그인
         처음이면 10분짜리 가입 권한 (토큰 해시) → signup.html#s=<토큰>
    2. 이름 · 휴대폰 · 아이디(본인이 정함) · 비밀번호 → core User(role=user) + MemberAccount + 구글 연결
       → 바로 로그인. 그 구글 계정은 아이디 · 비밀번호 찾기에도 쓰인다.
회원은 매물에 계약을 신청할 수 있다. 체결되면 그 호실의 임차인, 전출하면 다시 일반 회원.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import ClassVar, Dict, Optional

from core.auth import User, issue_token
from core.errors import Conflict, Forbidden, Mismatch

from .pool import PoolObj, now_iso

SIGNUP_TTL = 600
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,19}$")


class MemberAccount(PoolObj):
    """매물 계약을 신청할 수 있는 회원 (본인이 정한 아이디). 비밀번호는 core User 에 해시로."""
    collection: ClassVar[str] = "member_accounts"

    username: str
    name: str
    phone: str
    provider: str = "google"
    last_active: Optional[str] = None


class SignupSession(PoolObj):
    collection: ClassVar[str] = "signup_sessions"

    token_hash: str
    provider: str
    subject: str
    name: str = ""
    email: str = ""
    expires_at: float


INACTIVE_DAYS = 365


def touch(username: str) -> None:
    """로그인할 때 마지막 활동 시각 (1년 동안 안 쓰면 삭제 기준)."""
    m = MemberAccount.first(username=username)
    if m is not None:
        m.last_active = now_iso()
        m.save()


def purge_inactive(days: int = INACTIVE_DAYS) -> int:
    """오래 쓰지 않은 일반 회원(임차인 아님 · 진행 중 계약 없음)은 계정 · 구글 연결 · 개인정보를 지운다."""
    from datetime import datetime, timedelta
    from . import oauth
    from .models import OPEN_STATUSES, Contract, TenantAccount
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    removed = 0
    for m in MemberAccount.all():
        if (m.last_active or m.created_at) > cutoff:
            continue
        tenant = TenantAccount.first(username=m.username)
        if tenant and tenant.tenant_key:
            continue
        if any(c.status in OPEN_STATUSES for c in Contract.find(party_username=m.username)):
            continue
        for link in oauth.SocialLink.find(username=m.username):
            link.delete()
        user = User.by_username(m.username)
        if user is not None:
            user.delete("rent4_member_purged")
        m.delete()
        removed += 1
    return removed


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def open_session(provider: str, profile: Dict[str, str]) -> str:
    for old in SignupSession.all():
        if old.expires_at < time.time() or (old.provider == provider and old.subject == profile["subject"]):
            old.delete()
    token = secrets.token_urlsafe(24)
    SignupSession(token_hash=_hash(token), provider=provider, subject=profile["subject"], name=profile.get("name", ""),
                  email=profile.get("email", ""), expires_at=time.time() + SIGNUP_TTL).save()
    return token


def _session(token: str) -> SignupSession:
    s = SignupSession.first(token_hash=_hash(token or ""))
    if s is None or s.expires_at < time.time():
        raise Forbidden("본인 확인 시간이 지났습니다.", hint="구글 본인 확인부터 다시 해 주세요.")
    return s


def view(token: str) -> Dict:
    s = _session(token)
    return {"name": s.name, "email": s.email, "expires_in": int(s.expires_at - time.time())}


def signup(token: str, username: str, name: str, phone: str, password: str) -> Dict:
    from . import accounts, oauth
    s = _session(token)
    username, name = username.strip().lower(), name.strip()
    digits = re.sub(r"\D", "", phone or "")
    if not USERNAME_RE.match(username):
        raise Mismatch("아이디는 영문 소문자로 시작하는 영문 소문자 · 숫자 · _ 4~20자입니다.")
    if User.by_username(username) or any(username == b.code or username.startswith(b.code) and username[len(b.code):].isdigit()
                                         for b in accounts.Building.all()):
        raise Conflict(f"'{username}' 는 쓸 수 없는 아이디입니다.", hint="다른 아이디를 정해 주세요.")
    if not name:
        raise Mismatch("이름을 입력해 주세요.")
    if len(digits) < 10:
        raise Mismatch("휴대폰 번호를 정확히 입력해 주세요.")
    accounts.validate_new_password(password)
    if oauth.SocialLink.first(provider=s.provider, subject=s.subject):
        raise Conflict("이미 가입한 구글 계정입니다.", hint="로그인해 주세요.")
    user = User(username=username, role=accounts.TENANT_ROLE, name=name)
    user.set_password(password).save("rent4_member_signup")
    MemberAccount(username=username, name=name, phone=phone.strip(), provider=s.provider, last_active=now_iso()).save()
    oauth.SocialLink(provider=s.provider, subject=s.subject, username=username, name=s.name, email=s.email).save()
    s.delete()
    return {"token": issue_token(user), "username": username, "home": "index.html"}
