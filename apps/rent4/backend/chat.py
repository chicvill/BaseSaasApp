"""
apps/rent4/backend/chat.py — 서버 안 채팅창 (로그인한 사용자끼리).

- 로그인한 화면은 몇 초마다 GET /chat/inbox 를 부른다 → 새 메시지 + '지금 접속 중' 기록.
- 받는 사람이 접속 중이면 임대인이 만든 비밀번호 재설정 링크 등을 카카오·문자 대신 채팅창으로 바로 전달한다.
- 접속 기록은 메모리(프로세스 하나)에만 둔다. 메시지는 pool(messages)에 남는다.
"""
from __future__ import annotations

import time
from typing import ClassVar, Dict, List, Optional

from .pool import PoolObj, now_iso

ONLINE_SECONDS = 20
_last_seen: Dict[str, float] = {}


class Message(PoolObj):
    collection: ClassVar[str] = "messages"

    to_username: str
    sender: str
    sender_label: str = ""
    text: str
    link: str = ""                 # 화면 기준 상대 주소 (예: reset.html?t=...)
    link_label: str = ""
    read_at: Optional[str] = None


def touch(username: str) -> None:
    _last_seen[username] = time.time()


def online(username: str) -> bool:
    return time.time() - _last_seen.get(username, 0) < ONLINE_SECONDS


def post(to_username: str, sender: str, sender_label: str, text: str, link: str = "", link_label: str = "") -> Message:
    return Message(to_username=to_username, sender=sender, sender_label=sender_label, text=text,
                   link=link, link_label=link_label).save()


def inbox(username: str, limit: int = 30) -> List[Dict]:
    touch(username)
    msgs = sorted(Message.find(to_username=username), key=lambda m: m.created_at)[-limit:]
    return [m.model_dump() for m in msgs]


def mark_read(username: str, message_id: str) -> None:
    m = Message.first(id=message_id, to_username=username)
    if m and not m.read_at:
        m.read_at = now_iso()
        m.save()
