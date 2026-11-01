"""
apps/rent4/backend/chat.py — 서버 안 채팅창 (로그인한 사용자끼리).

- 로그인한 화면은 몇 초마다 GET /chat/inbox 를 부른다 → 새 메시지 + '지금 접속 중' 기록.
- 받는 사람이 접속 중이면 임대인이 만든 비밀번호 재설정 링크 등을 카카오·문자 대신 채팅창으로 바로 전달한다.
- 접속 기록은 메모리(프로세스 하나)에만 둔다. 메시지는 pool(messages)에 남는다.
- 서로 주고받는 대화: 분류(고장 · 문의 · 안내). 대화 상대는 정해져 있다
    임차인 ↔ 자기 건물 임대인 · 임대인 ↔ 관리인 · 관리인은 모든 임대인 · 임차인과 대화.
"""
from __future__ import annotations

import time
from typing import ClassVar, Dict, List, Optional

from core.auth import User
from core.errors import Forbidden, Mismatch

from .models import Building, Contract, LessorAccount, Room, TenantAccount
from .pool import PoolObj, now_iso

ONLINE_SECONDS = 20
CATEGORIES = ("고장", "문의", "안내")
MAX_TEXT = 1000
_last_seen: Dict[str, float] = {}


class Message(PoolObj):
    collection: ClassVar[str] = "messages"

    to_username: str
    sender: str
    sender_label: str = ""
    text: str
    link: str = ""                 # 화면 기준 상대 주소 (예: reset.html?t=...)
    link_label: str = ""
    category: str = ""             # 고장 | 문의 | 안내 | "" (시스템 알림)
    ts: float = 0                  # 보낸 시각 (초 단위 created_at 보다 정밀 — 같은 초 안의 순서)
    read_at: Optional[str] = None


def touch(username: str) -> None:
    _last_seen[username] = time.time()


def online(username: str) -> bool:
    return time.time() - _last_seen.get(username, 0) < ONLINE_SECONDS


def post(to_username: str, sender: str, sender_label: str, text: str, link: str = "", link_label: str = "") -> Message:
    return Message(to_username=to_username, sender=sender, sender_label=sender_label, text=text,
                   link=link, link_label=link_label, ts=time.time()).save()


def inbox(username: str, limit: int = 30) -> List[Dict]:
    touch(username)
    msgs = sorted(Message.find(to_username=username), key=lambda m: (m.created_at, m.ts))[-limit:]
    return [m.model_dump() for m in msgs]


def mark_read(username: str, message_id: str) -> None:
    m = Message.first(id=message_id, to_username=username)
    if m and not m.read_at:
        m.read_at = now_iso()
        m.save()


# ── 대화 상대 · 대화 ──
ADMIN_ROLES = ("superadmin", "admin")


def _building_name(branch: str) -> str:
    b = Building.first(branch=branch)
    return b.name if b else branch


def _tenant_label(acc: TenantAccount) -> str:
    room = Room.first(branch=acc.branch, room=acc.room)
    return f"임차인 {_building_name(acc.branch)} {acc.room}호 {room.tenant_name if room else ''}".strip()


def _tenants(branch: Optional[str] = None) -> List[Dict]:
    out = []
    for acc in sorted(TenantAccount.all(), key=lambda a: (a.branch, a.room)):
        if acc.tenant_key and (branch is None or acc.branch == branch):
            out.append({"username": acc.username, "label": _tenant_label(acc), "group": "임차인"})
    return out


def _lessors(branch: Optional[str] = None) -> List[Dict]:
    out = []
    for acc in LessorAccount.all():
        if branch is None or acc.branch == branch:
            u = User.by_username(acc.username)
            out.append({"username": acc.username, "group": "임대인",
                        "label": f"임대인 {_building_name(acc.branch)} {u.name if u else ''}".strip()})
    return out


OPEN = ("requested", "sent", "signed")


def _applicants(branch: str) -> List[Dict]:
    """이 건물에 계약을 신청 · 진행 중인 사람 (아직 임차인 아님)."""
    out, seen = [], set()
    for c in Contract.find(branch=branch, kind="lease"):
        if c.status in OPEN and c.party_username and c.party_username not in seen:
            seen.add(c.party_username)
            out.append({"username": c.party_username, "group": "신청자",
                        "label": f"신청자 {c.room}호 {c.party_name}"})
    return out


def label_of(user: User) -> str:
    if user.role in ADMIN_ROLES:
        return f"관리인 {user.name}".strip() if user.name and user.name != user.username else "관리인"
    if user.role == "branch_admin":
        return f"임대인 {_building_name(user.branch or '')} {user.name}".strip()
    acc = TenantAccount.first(username=user.username)
    return _tenant_label(acc) if acc else (user.name or user.username)


def contacts(user: User) -> List[Dict]:
    """대화할 수 있는 사람 (접속 중 · 안 읽은 메시지 수 포함)."""
    if user.role in ADMIN_ROLES:
        people = _lessors() + _tenants()
    elif user.role == "branch_admin":
        people = [{"username": u.username, "label": label_of(u), "group": "관리인"}
                  for role in ADMIN_ROLES for u in User.find(role=role)] + _tenants(user.branch) + _applicants(user.branch)
    else:
        acc = TenantAccount.first(username=user.username)
        if acc and acc.tenant_key:
            people = _lessors(acc.branch)
        else:                                   # 계약을 신청 · 진행 중인 회원 → 그 건물 임대인
            branches = {c.branch for c in Contract.find(party_username=user.username) if c.status in OPEN}
            people = [p for b in sorted(branches) for p in _lessors(b)]
    unread: Dict[str, int] = {}
    last: Dict[str, str] = {}
    for m in Message.find(to_username=user.username):
        if not m.read_at:
            unread[m.sender] = unread.get(m.sender, 0) + 1
        last[m.sender] = max(last.get(m.sender, ""), m.created_at)
    for m in Message.find(sender=user.username):
        last[m.to_username] = max(last.get(m.to_username, ""), m.created_at)
    for p in people:
        p.update(online=online(p["username"]), unread=unread.get(p["username"], 0), last_at=last.get(p["username"], ""))
    return sorted(people, key=lambda p: (-p["unread"], -_ts(p["last_at"]) if p["last_at"] else 0, p["label"]))


def _ts(iso: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def _allowed(user: User, other: str) -> bool:
    return any(p["username"] == other for p in contacts(user))


def thread(user: User, other: str, limit: int = 100) -> List[Dict]:
    """두 사람 사이의 대화 (오래된 것부터). 받은 메시지는 읽음 처리한다."""
    if not _allowed(user, other):
        raise Forbidden("대화할 수 없는 상대입니다.")
    touch(user.username)
    msgs = [m for m in Message.find(to_username=user.username) if m.sender == other] + \
           [m for m in Message.find(sender=user.username) if m.to_username == other]
    for m in msgs:
        if m.to_username == user.username and not m.read_at:
            m.read_at = now_iso()
            m.save()
    out = []
    for m in sorted(msgs, key=lambda m: (m.created_at, m.ts))[-limit:]:
        d = m.model_dump()
        d["mine"] = m.sender == user.username
        out.append(d)
    return out


def send(user: User, to: str, text: str, category: str) -> Dict:
    text = (text or "").strip()
    if not text:
        raise Mismatch("보낼 내용을 입력해 주세요.")
    if len(text) > MAX_TEXT:
        raise Mismatch(f"메시지는 {MAX_TEXT}자까지 보낼 수 있습니다.")
    if category not in CATEGORIES:
        raise Mismatch("분류(고장 · 문의 · 안내)를 골라 주세요.")
    if not _allowed(user, to):
        raise Forbidden("대화할 수 없는 상대입니다.")
    m = post(to, user.username, label_of(user), text)
    m.category = category
    m.save()
    return {**m.model_dump(), "mine": True}
