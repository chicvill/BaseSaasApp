"""
apps/rent_dist/_rent3_core/contract_chat.py — 계약방 대화(문의 · 제안 · 수정 요청 · 시스템 안내).

기존 DirectChat 을 확장해 쓴다(session_id 가 있는 글이 계약방 대화). 관리인 지원 채팅(SupportMessage)과는 완전히 별개다.

종류
  question        자유 문의. 계약이 진행 중인 동안(체결·취소 전) 언제나 가능.
  proposal        숫자·날짜 6개 항목(보증금·계약금·월세·관리비·입주일·계약기간)의 직접 제안. 상대가 수락하면 그 값으로 조건이 바뀐다.
  change_request  문구 협의가 필요한 항목(특약 등). 상대가 직접 조건을 고친 뒤 '확인·반영'으로 닫는다. 자동 반영은 없다.
  system          자동 기록.

규칙
  - 제안·수정 요청은 단계 ③까지만 만든다. 단계 ④(서명) 이후에는 문의만 가능하다.
  - 제안을 수락하면 반드시 CoSignSession.update_terms() 를 호출한다(리비전 증가, 양측 동의·서명 초기화, 재계약 5% 상한 검증 …).
  - 답하지 않은 제안·수정 요청이 있으면 단계 ③ 최종 동의(agree_step3)를 할 수 없다.
  - 체결(COMPLETED)·취소(CANCELLED)된 계약방에는 새 글을 남길 수 없다(읽기 전용).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from core.errors import Conflict, DomainError, Forbidden, NotFound

from .ops import DirectChat
from .pool import now_iso

# 제안 가능한 항목: 필드 → (표시 이름, 형식)
PROPOSAL_FIELDS: Dict[str, tuple] = {
    "deposit": ("보증금", "won"),
    "contract_deposit": ("계약금", "won"),
    "monthly_rent": ("월세", "won"),
    "maintenance_fee": ("관리비", "won"),
    "move_in_date": ("입주일", "date"),
    "lease_term_months": ("계약기간(개월)", "months"),
}
# 수정 요청(문구 협의) 항목
REQUEST_FIELDS: Dict[str, str] = {"special_terms": "특약사항", "other": "기타 조건"}
CONTRACT_KINDS = ("question", "proposal", "change_request", "system")
MAX_BODY = 1000
LAST_NEGOTIATION_STEP = 3


def _won(n: Any) -> str:
    try:
        return f"{int(n):,}원"
    except (TypeError, ValueError):
        return str(n)


def _show(field: str, value: Any) -> str:
    kind = PROPOSAL_FIELDS[field][1]
    if kind == "won":
        return _won(value)
    if kind == "months":
        return f"{value}개월"
    return str(value)


def _clean_body(body: str, required: bool) -> str:
    b = (body or "").strip()
    if required and not b:
        raise DomainError("내용을 입력해 주세요.")
    if len(b) > MAX_BODY:
        raise DomainError(f"한 번에 {MAX_BODY}자까지 보낼 수 있습니다.")
    return b


def _ensure_open_session(s: Any) -> None:
    if s.status != "IN_PROGRESS":
        raise Conflict(
            "체결되었거나 취소된 계약방에는 새 글을 남길 수 없습니다.",
            hint="지난 대화는 읽을 수만 있습니다.",
        )


def _parse_value(field: str, raw: Any) -> Any:
    kind = PROPOSAL_FIELDS[field][1]
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if kind == "date":
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise DomainError("입주일 형식이 올바르지 않습니다 (예: 2026-12-05).")
    if not text.lstrip("-").isdigit():
        raise DomainError(f"{PROPOSAL_FIELDS[field][0]}은(는) 숫자로 입력해 주세요.")
    n = int(text)
    if kind == "months":
        if not 1 <= n <= 120:
            raise DomainError("계약기간은 1~120개월이어야 합니다.")
    elif n < 0:
        raise DomainError("금액은 0원 이상이어야 합니다.")
    return n


def _check_consistent(s: Any, field: str, new: Any) -> None:
    """제안값을 반영했을 때 계약금이 보증금을 넘지 않는지 등 최소한의 일관성 확인."""
    merged = {**(s.terms or {}), field: new}
    if int(merged.get("contract_deposit") or 0) > int(merged.get("deposit") or 0):
        raise DomainError("계약금이 보증금보다 클 수 없습니다.", hint="보증금과 계약금을 함께 확인해 주세요.")


def _sys(s: Any, text: str, step: Optional[int] = None) -> DirectChat:
    return DirectChat(
        branch=s.branch, id_room=getattr(s, "id_room", "") or "", room_no=s.room_no, session_id=s.id, tenant_id=s.id_tenant or "",
        sender_role="system", sender_name="안내", message=text, kind="system", step=step if step is not None else s.current_step,
        read_by_lessor=False, read_by_tenant=False,
    ).save(action="contract_chat_system", app_source="rent3_contract")


def messages_of(session_id: str) -> List[DirectChat]:
    return sorted((m for m in DirectChat.find(session_id=session_id) if m.kind in CONTRACT_KINDS), key=lambda m: (m.created_at, m.id))


def open_items(session_id: str) -> List[DirectChat]:
    return [m for m in messages_of(session_id) if m.kind in ("proposal", "change_request") and m.status == "open"]


def public_item(m: DirectChat) -> Dict[str, Any]:
    return {
        "id": m.id, "kind": m.kind, "sender_role": m.sender_role, "sender_name": m.sender_name, "body": m.message, "step": m.step,
        "field": m.field, "proposal": m.proposal or {}, "status": m.status, "resolved_by": m.resolved_by, "resolved_at": m.resolved_at,
        "created_at": m.created_at,
    }


def list_for(session: Any, role: Optional[str]) -> Dict[str, Any]:
    """대화 목록. role 이 있으면 그 사람이 읽은 것으로 표시한다(관리인 열람은 role=None 이라 읽음 처리 안 함)."""
    items = messages_of(session.id)
    unread = 0
    if role in ("lessor", "tenant"):
        attr = f"read_by_{role}"
        for m in items:
            if m.sender_role != role and not getattr(m, attr):
                unread += 1                       # 이번에 처음 읽는 글 수(읽음 처리 전에 센다)
                setattr(m, attr, True)
                m.save(action="contract_chat_read", app_source="rent3_contract")
    return {
        "messages": [public_item(m) for m in items],
        "open_count": len(open_items(session.id)),
        "can_negotiate": session.status == "IN_PROGRESS" and session.current_step <= LAST_NEGOTIATION_STEP,
        "read_only": session.status != "IN_PROGRESS",
        "negotiable_fields": {k: v[0] for k, v in PROPOSAL_FIELDS.items()},
        "request_fields": REQUEST_FIELDS,
        "unread": unread,
    }


def post(session: Any, role: str, sender_name: str, kind: str, body: str = "", field: str = "", new_value: Any = None) -> DirectChat:
    if role not in ("lessor", "tenant"):
        raise Forbidden("계약 당사자만 글을 남길 수 있습니다.")
    if kind not in ("question", "proposal", "change_request"):
        raise DomainError("글 종류가 올바르지 않습니다.", hint="question / proposal / change_request")
    _ensure_open_session(session)
    base = dict(
        branch=session.branch, id_room=getattr(session, "id_room", "") or "", room_no=session.room_no, session_id=session.id,
        tenant_id=session.id_tenant or "", sender_role=role, sender_name=sender_name or ("임대인" if role == "lessor" else "임차인"),
        step=session.current_step, read_by_lessor=(role == "lessor"), read_by_tenant=(role == "tenant"),
    )
    if kind == "question":
        return DirectChat(kind="question", message=_clean_body(body, True), **base).save(action="contract_question", app_source="rent3_contract")

    if session.current_step > LAST_NEGOTIATION_STEP:
        raise DomainError(
            "단계 ④(서명) 이후에는 문의만 남길 수 있습니다.",
            hint="조건을 바꿔야 하면 '조건 수정'을 쓰세요. 서명이 초기화되고 3단계로 돌아갑니다.",
        )
    if kind == "change_request":
        f = field or "special_terms"
        if f not in REQUEST_FIELDS:
            raise DomainError("수정 요청 항목이 올바르지 않습니다.", hint=" / ".join(REQUEST_FIELDS))
        return DirectChat(kind="change_request", status="open", field=f, message=_clean_body(body, True), **base).save(
            action="contract_change_request", app_source="rent3_contract")

    # proposal
    if field not in PROPOSAL_FIELDS:
        raise DomainError("제안할 수 없는 항목입니다.", hint=" / ".join(f"{k}({v[0]})" for k, v in PROPOSAL_FIELDS.items()))
    new = _parse_value(field, new_value)
    old = (session.terms or {}).get(field)
    if str(old) == str(new):
        raise DomainError("현재 조건과 같은 값입니다.")
    _check_consistent(session, field, new)
    for prev in open_items(session.id):   # 같은 사람이 같은 항목에 올린 이전 제안은 새 제안으로 대체한다
        if prev.kind == "proposal" and prev.sender_role == role and prev.field == field:
            prev.status, prev.resolved_by, prev.resolved_at = "withdrawn", "system", now_iso()
            prev.save(action="contract_proposal_replaced", app_source="rent3_contract")
    label = PROPOSAL_FIELDS[field][0]
    text = _clean_body(body, False) or f"{label}을(를) {_show(field, old)} → {_show(field, new)}(으)로 제안합니다."
    return DirectChat(kind="proposal", status="open", field=field, proposal={"field": field, "label": label, "old": old, "new": new},
                      message=text, **base).save(action="contract_proposal", app_source="rent3_contract")


def respond(session: Any, msg_id: str, role: str, action: str) -> Dict[str, Any]:
    if role not in ("lessor", "tenant"):
        raise Forbidden("계약 당사자만 답할 수 있습니다.")
    if action not in ("accept", "decline", "withdraw"):
        raise DomainError("처리 방식이 올바르지 않습니다.", hint="accept / decline / withdraw")
    m = DirectChat.get(msg_id)
    if m is None or m.session_id != session.id or m.kind not in ("proposal", "change_request"):
        raise NotFound("해당 제안·수정 요청을 찾을 수 없습니다.")
    if m.status != "open":
        raise Conflict("이미 처리된 글입니다.")
    _ensure_open_session(session)
    mine = m.sender_role == role
    if action == "withdraw":
        if not mine:
            raise Forbidden("내가 올린 글만 철회할 수 있습니다.")
    elif mine:
        raise Forbidden("내가 올린 글은 철회만 할 수 있습니다.", hint="수락·거절은 상대방이 합니다.")

    who = "임대인" if role == "lessor" else "임차인"
    result: Dict[str, Any] = {}
    if action == "accept":
        if m.kind == "proposal":
            if session.current_step > LAST_NEGOTIATION_STEP:
                raise DomainError("단계 ④(서명) 이후에는 제안을 수락할 수 없습니다.", hint="조건 수정 경로를 쓰세요. 서명이 초기화됩니다.")
            field, new = m.proposal["field"], m.proposal["new"]
            _check_consistent(session, field, new)
            before_rev = session.revision
            # 반드시 기존 조건 변경 함수를 거친다 — 리비전·동의·서명 초기화, 재계약 5% 상한 검증이 함께 일어난다
            session.update_terms(role=role, updates={field: new}, app_source="rent3_contract")
            _sys(session, f"{who}이(가) {m.proposal['label']} 제안을 수락했습니다: {_show(field, m.proposal['old'])} → {_show(field, new)} "
                          f"(리비전 v{session.revision}, 양측 동의·서명이 초기화되었습니다).", step=min(session.current_step, 3))
            result["revision"] = session.revision
            result["revision_changed"] = session.revision != before_rev
            for other in open_items(session.id):   # 같은 항목에 남아 있던 다른 제안은 값이 달라져 자동 종료
                if other.id != m.id and other.kind == "proposal" and other.field == field:
                    other.status, other.resolved_by, other.resolved_at = "declined", "system", now_iso()
                    other.save(action="contract_proposal_expired", app_source="rent3_contract")
        else:
            _sys(session, f"{who}이(가) 수정 요청을 확인·반영했다고 표시했습니다.")
        m.status = "accepted"
    elif action == "decline":
        m.status = "declined"
        _sys(session, f"{who}이(가) {'제안' if m.kind == 'proposal' else '수정 요청'}을 거절했습니다.")
    else:
        m.status = "withdrawn"
        _sys(session, f"{who}이(가) 올린 {'제안' if m.kind == 'proposal' else '수정 요청'}을 철회했습니다.")
    m.resolved_by, m.resolved_at = role, now_iso()
    m.save(action=f"contract_chat_{action}", app_source="rent3_contract")
    result["item"] = public_item(m)
    return result


def ensure_no_open_requests(session_id: str) -> None:
    """agree_step3 에서 호출: 답하지 않은 제안·수정 요청이 있으면 최종 동의를 막는다."""
    n = len(open_items(session_id))
    if n:
        raise Conflict(
            f"아직 답하지 않은 제안·수정 요청이 {n}건 있습니다.",
            hint="계약방 대화에서 수락·거절·철회(또는 확인·반영)를 먼저 처리한 뒤 동의해 주세요.",
        )
