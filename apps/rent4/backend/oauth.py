"""
apps/rent4/backend/oauth.py — 카카오 · 구글 계정 연결 (계약 전 본인 확인) 과 소셜 로그인.

흐름 (OAuth 2.0 authorization code)
    1. POST /oauth/{provider}/start {purpose}   → 카카오/구글 로그인 주소 (state 는 해시로 pool 에 10분 저장)
    2. 사용자가 카카오/구글에서 동의             → GET /oauth/{provider}/callback?code&state
    3. purpose=link  : 로그인한 사용자에게 계정 연결 + 진행 중 계약이면 '본인 확인 완료' → contract.html
       purpose=login : 연결된 계정이면 로그인 토큰 → login.html#token=...
       purpose=find  : 연결된 계정이면 아이디·비밀번호 찾기 권한 (finder) → find.html#f=...
    link 는 back=account 로 시작하면 계약 화면 대신 로그인한 사람의 화면(내 정보)으로 돌아간다.

환경변수 (각 서비스 개발자 콘솔에서 발급)
    KAKAO_REST_API_KEY [, KAKAO_CLIENT_SECRET]   developers.kakao.com > 내 애플리케이션
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET       console.cloud.google.com > API 및 서비스 > 사용자 인증 정보
Redirect URI 로 등록할 주소: <서버>/api/rent4/oauth/kakao/callback, <서버>/api/rent4/oauth/google/callback
(단독 실행이면 <서버>/api/oauth/.../callback)
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from typing import ClassVar, Dict, List, Optional

from core.auth import User, issue_token
from core.errors import DomainError, NotFound

from .models import Contract
from .pool import PoolObj, now_iso

STATE_TTL = 600

PROVIDERS = {
    "kakao": {
        "label": "카카오",
        "authorize": "https://kauth.kakao.com/oauth/authorize",
        "token": "https://kauth.kakao.com/oauth/token",
        "profile": "https://kapi.kakao.com/v2/user/me",
        "scope": "",
        "id_env": "KAKAO_REST_API_KEY", "secret_env": "KAKAO_CLIENT_SECRET", "secret_required": False,
    },
    "google": {
        "label": "구글",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "profile": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "id_env": "GOOGLE_CLIENT_ID", "secret_env": "GOOGLE_CLIENT_SECRET", "secret_required": True,
    },
}


class SocialLink(PoolObj):
    """카카오/구글 계정 ↔ Rent4 로그인 아이디."""
    collection: ClassVar[str] = "social_links"

    provider: str
    subject: str                   # 카카오 회원번호 / 구글 sub
    username: str
    name: str = ""
    email: str = ""


class OAuthState(PoolObj):
    collection: ClassVar[str] = "oauth_states"

    state_hash: str
    provider: str
    purpose: str                   # link | login | find
    username: str = ""
    back: str = ""                 # link 뒤 돌아갈 화면 (account → 임차인/임대인 화면)
    redirect_uri: str
    expires_at: float


def configured() -> List[Dict[str, str]]:
    out = []
    for key, p in PROVIDERS.items():
        if os.getenv(p["id_env"]) and (os.getenv(p["secret_env"]) or not p["secret_required"]):
            out.append({"id": key, "label": p["label"]})
    return out


def _provider(name: str) -> Dict:
    if name not in PROVIDERS or name not in {p["id"] for p in configured()}:
        raise NotFound(f"'{name}' 로그인이 설정되지 않았습니다.", hint="서버에 카카오·구글 키를 등록해야 합니다.")
    return PROVIDERS[name]


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def start(name: str, purpose: str, redirect_uri: str, user: Optional[User], back: str = "") -> Dict:
    p = _provider(name)
    if purpose not in ("link", "login", "find", "signup"):
        raise DomainError("purpose 는 link, login, find, signup 중 하나입니다.")
    if purpose == "link" and user is None:
        raise DomainError("로그인한 뒤 계정을 연결할 수 있습니다.")
    for old in OAuthState.all():
        if old.expires_at < time.time():
            old.delete()
    state = secrets.token_urlsafe(24)
    OAuthState(state_hash=_hash(state), provider=name, purpose=purpose, username=user.username if user else "",
               back=back if back == "account" else "", redirect_uri=redirect_uri,
               expires_at=time.time() + STATE_TTL).save()
    q = {"client_id": os.environ[p["id_env"]], "redirect_uri": redirect_uri, "response_type": "code", "state": state}
    if p["scope"]:
        q["scope"] = p["scope"]
    if name == "google":
        q["prompt"] = "select_account"
    return {"url": f"{p['authorize']}?{urllib.parse.urlencode(q)}"}


def _http_json(url: str, data: Optional[Dict] = None, token: str = "") -> Dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read() or b"{}")


def _profile(name: str, code: str, redirect_uri: str) -> Dict[str, str]:
    p = PROVIDERS[name]
    form = {"grant_type": "authorization_code", "client_id": os.environ[p["id_env"]],
            "redirect_uri": redirect_uri, "code": code}
    if os.getenv(p["secret_env"]):
        form["client_secret"] = os.environ[p["secret_env"]]
    try:
        access = _http_json(p["token"], form)["access_token"]
        info = _http_json(p["profile"], token=access)
    except Exception as e:
        raise DomainError(f"{p['label']} 확인에 실패했습니다.", hint="잠시 뒤 다시 시도해 주세요.", level="error") from e
    if name == "kakao":
        acct = info.get("kakao_account") or {}
        return {"subject": str(info["id"]), "name": (acct.get("profile") or {}).get("nickname", ""),
                "email": acct.get("email", "")}
    return {"subject": str(info["sub"]), "name": info.get("name", ""), "email": info.get("email", "")}


def callback(name: str, code: str, state: str) -> str:
    """처리 뒤 돌아갈 화면 주소(상대 경로)를 돌려준다."""
    _provider(name)
    st = OAuthState.first(state_hash=_hash(state or ""))
    if st is None or st.provider != name or st.expires_at < time.time():
        return "login.html#oauth=expired"
    st.delete()
    prof = _profile(name, code, st.redirect_uri)
    if st.purpose == "link":
        page = _account_page(st.username) if st.back == "account" else "contract.html"
        link = SocialLink.first(provider=name, subject=prof["subject"])
        if link and link.username != st.username:
            return f"{page}#oauth=used"                 # 다른 사람이 이미 연결한 계정
        link = link or SocialLink(provider=name, subject=prof["subject"], username=st.username)
        link.name, link.email = prof["name"], prof["email"]
        link.save()
        c = Contract.first(party_username=st.username)
        if c is not None:
            c.verified_provider, c.verified_name, c.verified_at = name, prof["name"] or prof["email"], now_iso()
            c.save()
        return f"{page}#oauth=linked"
    link = SocialLink.first(provider=name, subject=prof["subject"])
    if st.purpose == "find":
        if link is None or User.by_username(_current_username(link.username)) is None:
            return "find.html#oauth=unlinked"
        from . import finder
        return f"find.html#f={finder.open_session(name, _current_username(link.username))}"
    user = User.by_username(_current_username(link.username)) if link else None
    if st.purpose == "signup":                      # 이미 회원이면 가입을 건너뛰고 로그인
        if user is not None:
            return f"signup.html#token={issue_token(user)}"
        from . import members
        return f"signup.html#s={members.open_session(name, prof)}"
    if user is None:
        return "login.html#oauth=unlinked"
    return f"login.html#token={issue_token(user)}"


def _current_username(username: str) -> str:
    """계약 진행용 아이디로 연결했어도, 체결 뒤에는 최종 아이디로 로그인한다."""
    c = Contract.first(party_username=username)
    return c.username if c is not None and c.status == "active" and c.username else username


def _account_page(username: str) -> str:
    user = User.by_username(username)
    return "임대인.html" if user is not None and user.role == "branch_admin" else "임차인.html"


def linked(username: str) -> List[Dict[str, str]]:
    return [{"provider": l.provider, "label": PROVIDERS[l.provider]["label"], "name": l.name}
            for l in SocialLink.find(username=username)]
