"""
apps/rent_dist/_rent3_core/room_messages.py — '메시지' 화면(임대인·임차인): 호실별 1:1 대화와 계약 협상 기록.

보여 주는 것
  - 호실 대화: DirectChat 중 session_id 가 없는 글(문의·메시지·안내). 계약방 대화는 섞지 않는다.
  - 계약 협상 기록: 그 호실의 현재 계약이 만들어진 계약방(Contract.cosign_session_id)의 대화. 접어서 따로 보여 주고
    체결 때 기록한 chat_digest 와 지금 내용이 맞는지(무결성) 함께 알려 준다.
관리인 지원 채팅(SupportMessage)은 여기에 나오지 않는다.

임차인에게 보이는 범위: 자기 호실의 글 중 (a) 자기 id 로 남긴 글, (b) id 없이 남은 글은 현재 계약이 생긴 시각 이후의 것만.
  → 이 호실에 예전에 문의했던 다른 예비 임차인의 글이 새 입주자에게 보이지 않는다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.errors import DomainError

from . import contract_chat
from .models import Contract, Lessor, Room, Tenant, normalize_branch_id
from .ops import DirectChat

MAX_BODY = 1000


def _plain(m: DirectChat) -> bool:
    return not m.session_id and m.kind in ("chat", "system")


def room_messages(branch: str, room_no: str, tenant: Optional[Tenant] = None, since: str = "") -> List[DirectChat]:
    """호실 대화(계약방 대화 제외), 오래된 것부터. tenant 가 주어지면 그 임차인에게 보이는 범위만."""
    out = []
    for m in DirectChat.find(branch=normalize_branch_id(branch), room_no=room_no):
        if not _plain(m):
            continue
        if tenant is not None:
            mine = m.tenant_id == tenant.id
            legacy = (not m.tenant_id) and (not since or m.created_at >= since)
            if not (mine or legacy):
                continue
        out.append(m)
    return sorted(out, key=lambda m: (m.created_at, m.id))


def public(m: DirectChat) -> Dict[str, Any]:
    return {"id": m.id, "sender_role": m.sender_role, "sender_name": m.sender_name, "body": m.message, "kind": m.kind, "created_at": m.created_at}


def _unread(m: DirectChat, role: str) -> bool:
    if m.sender_role == role:
        return False
    return not getattr(m, f"read_by_{role}") and not (m.is_read and role == "lessor" and m.sender_role == "tenant")


def unread_count(branch: str, room_no: str, role: str, tenant: Optional[Tenant] = None, since: str = "") -> int:
    return sum(1 for m in room_messages(branch, room_no, tenant, since) if _unread(m, role))


def mark_read(msgs: List[DirectChat], role: str) -> None:
    attr = f"read_by_{role}"
    for m in msgs:
        if m.sender_role != role and not getattr(m, attr):
            setattr(m, attr, True)
            m.save(action="room_message_read", app_source="rent3_messages")


def post(branch: str, room_no: str, role: str, sender_name: str, body: str, tenant_id: str = "") -> DirectChat:
    b = (body or "").strip()
    if not b:
        raise DomainError("내용을 입력해 주세요.")
    if len(b) > MAX_BODY:
        raise DomainError(f"한 번에 {MAX_BODY}자까지 보낼 수 있습니다.")
    room = Room.first(branch=normalize_branch_id(branch), room_no=room_no)
    if room is None:
        raise DomainError(f"{room_no}호를 찾을 수 없습니다.")
    return DirectChat(
        branch=normalize_branch_id(branch), id_room=room.id, room_no=room_no, tenant_id=tenant_id, sender_role=role,
        sender_name=sender_name, message=b, kind="chat", read_by_lessor=(role == "lessor"), read_by_tenant=(role == "tenant"),
    ).save(action=f"room_message_{role}", app_source="rent3_messages")


def negotiation_history(contract: Optional[Contract]) -> Dict[str, Any]:
    """현재 계약의 계약방 협상 기록(접어서 보여 줄 것)과 chat_digest 무결성 확인 결과."""
    if contract is None or not contract.cosign_session_id:
        return {"count": 0, "items": [], "digest": "", "digest_ok": None, "purged": False}
    if contract.chat_purged_at:          # 보관 기간 만료로 지운 것 — 해시 변조가 아니다
        return {"count": 0, "items": [], "digest": contract.chat_digest, "digest_ok": None, "purged": True,
                "purged_at": contract.chat_purged_at, "original_count": contract.chat_digest_count, "purged_note": contract_chat.PURGED_NOTE}
    items = contract_chat.messages_of(contract.cosign_session_id)
    digest, n = contract_chat.compute_digest(contract.cosign_session_id)
    return {
        "count": len(items),
        "items": [contract_chat.public_item(m) for m in items],
        "digest": contract.chat_digest,
        "digest_ok": (digest == contract.chat_digest) if contract.chat_digest else None,
        "purged": False,
    }


def active_contract(room: Optional[Room]) -> Optional[Contract]:
    return Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None


def rooms_overview(branch: str) -> List[Dict[str, Any]]:
    """임대인 메시지 목록: 호실별 마지막 글과 안 읽은 수."""
    b = normalize_branch_id(branch)
    out = []
    for r in sorted(Room.find(branch=b), key=lambda x: x.room_no):
        msgs = room_messages(b, r.room_no)
        last = msgs[-1] if msgs else None
        tenant = Tenant.get(r.id_tenant) if r.id_tenant else None
        out.append({
            "room_no": r.room_no, "tenant_name": tenant.name if tenant else "", "is_occupied": r.is_occupied,
            "count": len(msgs), "unread": sum(1 for m in msgs if _unread(m, "lessor")),
            "last_body": (last.message[:60] if last else ""), "last_at": last.created_at if last else "", "last_role": last.sender_role if last else "",
        })
    return out
