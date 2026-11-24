"""
apps/rent_dist/_rent3_core/chatsessions.py — 채팅 세션 기록(알림창 '최근 대화' 목록용).

채팅창을 열어 두는 동안 앱은 2.5초마다 그 대화를 읽어 새 글을 확인한다. 이 읽기를 '창이 열려 있다'는 신호로 쓴다:
 - 같은 사용자·같은 대화에서 마지막 읽기로부터 SESSION_GAP_SECONDS(30초) 안이면 같은 세션, 그보다 오래 비었다 읽으면 새 세션이 시작된다.
   창을 닫으면 읽기가 멈추므로 30초 뒤 세션이 끝난 것으로 본다(탭이 숨겨져도 마찬가지).
 - 저장하는 것은 '누가 · 어느 대화 · 시작 시각 · 목록에 보일 제목'뿐이다. 대화 내용·미리보기는 저장하지 않는다.
 - 세션 기록은 대화와 같은 10일 뒤 지운다(지연 삭제).
 - 읽을 때마다 저장하면 풀이 계속 바뀌므로, 마지막 읽은 시각은 메모리에만 두고 세션이 '시작'될 때만 한 번 저장한다.
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, ClassVar, Dict, List, Tuple
from uuid import uuid4

from pydantic import Field

from .models import date_today
from .pool import PoolObj

SESSION_GAP_SECONDS = 30
RETENTION_DAYS = 10

_last_seen: Dict[Tuple[str, str, str], float] = {}


def _expiry() -> str:
    return (date_today() + timedelta(days=RETENTION_DAYS)).isoformat()


class ChatSession(PoolObj):
    """채팅창을 연 기록 한 줄(제목과 시작 시각만, 대화 내용은 없다)."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "채팅 세션"

    id: str = Field(default_factory=lambda: f"chs_{uuid4().hex[:8]}")
    viewer: str = ""                         # 'lessor:<id>' | 'tenant:<id>' | 'admin'
    kind: str = "room"                       # room(임대인↔임차인 호실 대화) | support(사용자↔관리인 지원 채팅)
    ref: str = ""                            # room: 호실 번호, support: 'lessor:<id>'|'tenant:<id>'
    branch: str = "main"
    label_text: str = ""                     # 목록에 보일 상대 이름(예: '시크빌 임대인')
    expires_at: str = Field(default_factory=_expiry)

    def summary(self) -> str:                # 이벤트 로그에는 종류만(이름·내용 없음)
        return f"채팅 세션 [{self.kind}]"


def touch(viewer: str, kind: str, ref: str, label_text: str, branch: str = "main") -> None:
    """창이 열려 대화를 읽었다는 신호. 새 세션이면 한 줄 저장한다."""
    key = (viewer, kind, str(ref))
    now = time.time()
    prev = _last_seen.get(key)
    _last_seen[key] = now
    if prev is not None and now - prev < SESSION_GAP_SECONDS:
        return
    ChatSession(viewer=viewer, kind=kind, ref=str(ref), branch=branch, label_text=label_text).save(
        action="chat_session_started", app_source="rent3_chat")


def recent(viewer: str, limit: int = 8) -> List[ChatSession]:
    """이 사용자의 대화별 가장 최근 세션, 최근 시작 순."""
    today = date_today().isoformat()
    latest: Dict[Tuple[str, str], ChatSession] = {}
    for s in ChatSession.find(viewer=viewer):
        if s.expires_at < today:
            s.delete(action="chat_session_expired")
            continue
        k = (s.kind, s.ref)
        if k not in latest or s.created_at > latest[k].created_at:
            latest[k] = s
    return sorted(latest.values(), key=lambda s: s.created_at, reverse=True)[:limit]
