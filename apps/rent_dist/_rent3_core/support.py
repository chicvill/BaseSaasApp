"""
apps/rent_dist/_rent3_core/support.py — 관리인 원격 지원: 지원 채팅과 접속 기록.

 - 지원 채팅(SupportMessage): 사용자(임대인·임차인) ↔ 관리인 1:1. 글은 10일 뒤 지운다(지연 삭제).
   풀 이벤트(덧붙이기 전용 로그)에 남는 요약에는 본문을 넣지 않는다.
 - 접속 기록(SupportAccessLog): 관리인이 대리 접속할 때마다 한 줄. 누가·언제·어디·어떤 입장·사유 분류만 담고 1년 보관한다.
 - 입장 조건: 사용자가 원격 지원을 허용했고(consent.support_allowed), 그 사용자의 지원 채팅이 있어야 한다.
   채팅이 없으면 관리인이 전화 접수(사유 분류 + 메모)를 남겨 '[전화 요청 접수]' 글을 자동으로 만들 수 있다.
 - 대리 접속은 보기 전용이다(authz.admin_proxy_guard).

스케줄러가 없으므로 기한이 지난 글·기록은 지원 API를 부를 때와 로그인할 때 지운다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError, Forbidden, NotFound
from . import consent as cs
from .models import Building, Lessor, Room, Tenant, date_today
from .pool import PoolObj, now_iso

SUPPORT_CHAT_DAYS = 10          # 지원 채팅 글 보관 일수
ACCESS_LOG_DAYS = 365           # 접속 기록 보관 일수 (1년)
ACCESS_WINDOW_MIN = 30          # 접속 후 '관리인이 접속 중' 알림을 보이는 시간(분)
CATEGORIES = ("사용법 문의", "오류 신고", "계정 문제", "기타")
VIEW_KO = {"lessor": "임대인 입장", "tenant": "임차인 입장", "contract": "계약방"}
MAX_BODY = 1000


def _expiry(days: int) -> str:
    return (date_today() + timedelta(days=days)).isoformat()


class SupportMessage(PoolObj):
    """지원 채팅 글 (사용자 ↔ 관리인). 10일 뒤 삭제."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "지원 채팅"

    id: str = Field(default_factory=lambda: f"sup_{uuid4().hex[:8]}")
    branch: str = "main"
    room_no: str = ""
    thread_user: str = ""                    # 'lessor:<id>' | 'tenant:<id>' — 대화 상대 사용자
    thread_role: str = "tenant"              # lessor | tenant
    sender_role: str = "user"                # user | admin | system
    sender_name: str = ""
    kind: str = "message"                    # message | phone_intake | access_start
    category: str = ""
    body: str = ""
    expires_at: str = Field(default_factory=lambda: _expiry(SUPPORT_CHAT_DAYS))
    read_by_user: bool = False
    read_by_admin: bool = False

    def summary(self) -> str:                # 본문은 넣지 않는다(이벤트 로그가 지워지지 않으므로)
        return f"지원 채팅 [{self.room_no or self.branch}] {self.sender_name or self.sender_role}"


class SupportAccessLog(PoolObj):
    """관리인 대리 접속 기록. 1년 보관, 대화 내용은 담지 않는다."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "대리 접속 기록"

    id: str = Field(default_factory=lambda: f"sac_{uuid4().hex[:8]}")
    admin_user: str = ""
    branch: str = "main"
    room_no: str = ""
    target_user: str = ""
    target_role: str = "tenant"
    view: str = "tenant"                     # lessor | tenant | contract
    session_id: str = ""
    category: str = ""
    entered_at: str = Field(default_factory=now_iso)
    retain_until: str = Field(default_factory=lambda: _expiry(ACCESS_LOG_DAYS))

    def summary(self) -> str:
        return f"대리 접속 [{self.branch} {self.room_no}호] {VIEW_KO.get(self.view, self.view)} {self.category}"


# ── 보관 기간 지난 것 지우기 (지연 삭제) ──
def purge_expired() -> Dict[str, int]:
    today = date_today().isoformat()
    n_msg = n_log = 0
    for m in SupportMessage.all():
        if m.expires_at and m.expires_at < today:
            m.delete(action="support_message_expired", app_source="rent3_support")
            n_msg += 1
    for g in SupportAccessLog.all():
        if g.retain_until and g.retain_until < today:
            g.delete(action="support_access_log_expired", app_source="rent3_support")
            n_log += 1
    return {"messages": n_msg, "logs": n_log}


# ── 사용자 식별 ──
def thread_key(user: Any) -> str:
    return f"{'lessor' if isinstance(user, Lessor) else 'tenant'}:{user.id}"


def thread_role(user: Any) -> str:
    return "lessor" if isinstance(user, Lessor) else "tenant"


def user_by_thread(thread_user: str) -> Any:
    if ":" not in str(thread_user):
        raise DomainError("대상 사용자를 찾을 수 없습니다.")
    kind, uid = str(thread_user).split(":", 1)
    user = cs.find_user(kind, uid)
    if user is None:
        raise NotFound("대상 사용자를 찾을 수 없습니다.")
    return user


def _room_of(user: Any) -> str:
    return "" if isinstance(user, Lessor) else (getattr(user, "room_no", "") or "")


def thread_messages(thread_user: str) -> List[SupportMessage]:
    return sorted(SupportMessage.find(thread_user=thread_user), key=lambda m: (m.created_at, m.id))


def public_message(m: SupportMessage) -> Dict[str, Any]:
    return {
        "id": m.id, "sender_role": m.sender_role, "sender_name": m.sender_name, "kind": m.kind,
        "category": m.category, "body": m.body, "created_at": m.created_at, "expires_at": m.expires_at,
    }


def _clean_body(body: str) -> str:
    b = (body or "").strip()
    if not b:
        raise DomainError("내용을 입력해 주세요.")
    if len(b) > MAX_BODY:
        raise DomainError(f"한 번에 {MAX_BODY}자까지 보낼 수 있습니다.")
    return b


def post_user_message(user: Any, body: str) -> SupportMessage:
    purge_expired()
    return SupportMessage(
        branch=user.branch, room_no=_room_of(user), thread_user=thread_key(user), thread_role=thread_role(user),
        sender_role="user", sender_name=user.name, body=_clean_body(body), read_by_user=True,
    ).save(action="support_user_message", app_source="rent3_support")


def post_admin_message(user: Any, admin_name: str, body: str, kind: str = "message", category: str = "",
                       sender_role: str = "admin") -> SupportMessage:
    purge_expired()
    return SupportMessage(
        branch=user.branch, room_no=_room_of(user), thread_user=thread_key(user), thread_role=thread_role(user),
        sender_role=sender_role, sender_name=admin_name or "관리인", kind=kind, category=category,
        body=_clean_body(body), read_by_admin=True,
    ).save(action=f"support_{kind}", app_source="rent3_support")


def mark_read(thread_user: str, by: str) -> None:
    """by: user(사용자가 관리인 글을 읽음) | admin(관리인이 사용자 글을 읽음)"""
    for m in thread_messages(thread_user):
        if by == "user" and m.sender_role != "user" and not m.read_by_user:
            m.read_by_user = True
            m.save(action="support_read", app_source="rent3_support")
        elif by == "admin" and m.sender_role == "user" and not m.read_by_admin:
            m.read_by_admin = True
            m.save(action="support_read", app_source="rent3_support")


def unread_count(thread_user: str, by: str) -> int:
    ms = thread_messages(thread_user)
    if by == "user":
        return sum(1 for m in ms if m.sender_role != "user" and not m.read_by_user)
    return sum(1 for m in ms if m.sender_role == "user" and not m.read_by_admin)


def admin_viewing(thread_user: str) -> Optional[str]:
    """최근 ACCESS_WINDOW_MIN 분 안에 관리인이 접속했으면 그 시각, 아니면 None."""
    since = datetime.now() - timedelta(minutes=ACCESS_WINDOW_MIN)
    latest = ""
    for g in SupportAccessLog.find(target_user=thread_user):
        try:
            if datetime.fromisoformat(g.entered_at) >= since and g.entered_at > latest:
                latest = g.entered_at
        except ValueError:
            continue
    return latest or None


def phone_intake(user: Any, admin_name: str, category: str, memo: str) -> SupportMessage:
    """전화로 요청받은 경우: 사유 분류와 접수 메모를 지원 채팅에 '[전화 요청 접수]' 글로 남긴다. 원격 지원 허용 필요."""
    if category not in CATEGORIES:
        raise DomainError("사유 분류를 골라 주세요.", hint=" / ".join(CATEGORIES))
    if not cs.support_allowed(user):
        raise Forbidden("이 사용자는 원격 지원을 허용하지 않았습니다.", hint="전화로 방법을 안내하거나, 사용자가 메뉴에서 원격 지원 허용을 켜도록 안내해 주세요.")
    text = f"[전화 요청 접수] ({category}) {memo.strip()}".strip()
    return post_admin_message(user, admin_name, text, kind="phone_intake", category=category)


def enter(admin_user: str, user: Any, view: str, session_id: str = "", category: str = "", room_no: str = "") -> Dict[str, Any]:
    """대리 접속 시작: 조건을 확인하고 접속 기록·채팅 안내를 남긴 뒤 이동할 주소를 돌려준다."""
    purge_expired()
    if view not in VIEW_KO:
        raise DomainError("입장 종류가 올바르지 않습니다.", hint="lessor / tenant / contract")
    role = thread_role(user)
    if view == "lessor" and role != "lessor":
        raise DomainError("임대인 입장은 임대인 사용자에게만 열 수 있습니다.")
    if view == "tenant" and role != "tenant":
        raise DomainError("임차인 입장은 임차인 사용자에게만 열 수 있습니다.")
    if not cs.support_allowed(user):
        raise Forbidden("이 사용자는 원격 지원을 허용하지 않았습니다.",
                        hint="전화로 방법을 안내하거나, 사용자가 메뉴에서 원격 지원 허용을 켜도록 안내해 주세요.")
    key = thread_key(user)
    if not thread_messages(key):
        raise DomainError("먼저 사용자의 지원 채팅을 열거나 전화 접수를 남겨 주세요.",
                          hint="접속 사유는 지원 채팅에 남습니다. 전화로 요청받았다면 '전화 접수'를 먼저 입력하세요.")
    if category not in CATEGORIES:
        category = "기타"
    branch = user.branch
    room_no = _room_of(user) or (room_no or "").strip()

    if view == "contract":
        from .cosign import CoSignSession
        from .authz import session_party_ok
        s = CoSignSession.first(session_code=session_id) or CoSignSession.get(session_id) if session_id else None
        if s is None:
            raise NotFound("계약방을 찾을 수 없습니다.")
        if not session_party_ok({"r": role, "b": user.branch, "u": key}, s):
            raise Forbidden("이 사용자가 당사자인 계약방이 아닙니다.")
        room_no = room_no or s.room_no
        url = f"/rent3_contract/?session={s.id}&role={role}&proxy=contract"
    elif view == "lessor":
        if room_no and not Room.first(branch=branch, room_no=room_no):
            raise NotFound(f"{branch} 건물에 {room_no}호가 없습니다.")
        url = f"/rent3_lessor/?branch={branch}&proxy=lessor" + (f"&room={room_no}" if room_no else "")
    else:
        url = f"/rent3_tenant/?tenant={user.id}&proxy=tenant"

    log = SupportAccessLog(
        admin_user=admin_user, branch=branch, room_no=room_no, target_user=key, target_role=role, view=view,
        session_id=session_id if view == "contract" else "", category=category,
    ).save(action="support_access_started", app_source="rent3_support")
    post_admin_message(
        user, admin_user, f"🛡️ 관리인이 {VIEW_KO[view]}으로 접속했습니다 (보기 전용). 사유: {category}",
        kind="access_start", category=category, sender_role="system",
    )
    return {"redirect_url": url, "log_id": log.id, "view": view}
