"""
apps/rent_dist/rent3_contract/routers/chat.py — 계약방 실시간 채팅 · 조건 제안/일괄 제시 · 협상 해시 검증.
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import CoSignSession, Contract, contract_ai, contract_chat
from ._deps import _chat_actor, _touch_chat_session

router = APIRouter()


class ContractMsgReq(BaseModel):
    kind: str = "question"            # question | proposal | bundle | change_request
    body: str = ""
    field: str = ""
    new_value: Optional[Any] = None   # 제안 값(숫자 또는 날짜)
    bundle: Optional[Dict[str, Any]] = None   # 일괄 조건 제시용 {deposit, contract_deposit, monthly_rent, ...}


class ContractMsgRespondReq(BaseModel):
    action: str                        # accept | decline | withdraw


class AiAssistReq(BaseModel):
    action: str = "explain_contract"   # explain_contract | draft_clause | ocr_memo
    text: str = ""
    image_data_url: str = ""
    consent: bool = False


@router.get("/sessions/{session_id}/messages")
def contract_messages(session_id: str, request: Request, mark_read: bool = True):
    """계약방 채팅 목록(문의·제안·수정 요청·안내). 당사자만. mark_read=false 면 읽음 처리하지 않는다(주기적 확인용)."""
    contract_chat.purge_expired()        # 보관 기간 지난 대화 정리(10분에 한 번)
    s = CoSignSession.by_code_or_id(session_id)
    who = _chat_actor(request, s)
    if who["role"] and mark_read:                      # 채팅 세션(제목·시작 시각만 기록) — 알림창 '최근 대화'용
        _touch_chat_session(s, who["role"], request)
    return {"success": True, **contract_chat.list_for(s, who["role"] or None, mark_read=mark_read)}


@router.post("/sessions/{session_id}/messages")
def contract_message_post(session_id: str, req: ContractMsgReq, request: Request):
    s = CoSignSession.by_code_or_id(session_id)
    who = _chat_actor(request, s)
    m = contract_chat.post(
        s,
        who["role"],
        who["name"],
        req.kind,
        body=req.body,
        field=req.field,
        new_value=req.new_value,
        bundle=req.bundle,
    )
    msg = {
        "question": "문의를 남겼습니다.",
        "proposal": "제안을 보냈습니다. 상대방이 수락하면 조건이 바뀝니다.",
        "bundle": "계약 조건 일괄 제시를 채팅방에 보냈습니다.",
        "change_request": "수정 요청을 보냈습니다.",
    }[m.kind]
    return {
        "success": True,
        "message": msg,
        "item": contract_chat.public_item(m),
        "open_count": len(contract_chat.open_items(s.id)),
    }


@router.get("/sessions/{session_id}/chat-digest")
def contract_chat_digest(session_id: str):
    """체결 때 기록한 협상 기록 해시와 지금 내용이 맞는지 확인한다(읽음 표시는 해시에 영향이 없다). 당사자만."""
    s = CoSignSession.by_code_or_id(session_id)
    ct = Contract.get(s.created_contract_id) if s.created_contract_id else None
    stored = ct.chat_digest if ct else ""
    if ct and ct.chat_purged_at:         # 보관 기간 만료로 지웠다 — 변조(match=false)와 구분한다
        return {
            "success": True,
            "concluded": True,
            "stored": stored,
            "computed": "",
            "count": 0,
            "match": None,
            "purged": True,
            "purged_at": ct.chat_purged_at,
            "original_count": ct.chat_digest_count,
            "note": contract_chat.PURGED_NOTE,
        }
    computed, n = contract_chat.compute_digest(s.id)
    return {
        "success": True,
        "concluded": bool(ct),
        "stored": stored,
        "computed": computed,
        "count": n,
        "match": (computed == stored) if stored else None,
        "purged": False,
    }


@router.post("/sessions/{session_id}/messages/{msg_id}/respond")
def contract_message_respond(session_id: str, msg_id: str, req: ContractMsgRespondReq, request: Request):
    s = CoSignSession.by_code_or_id(session_id)
    who = _chat_actor(request, s)
    res = contract_chat.respond(s, msg_id, who["role"], req.action)
    text = {
        "accept": (
            "계약 조건을 확인·반영했습니다."
            if res["item"]["kind"] == "bundle"
            else ("수락했습니다." if res["item"]["kind"] == "proposal" else "확인·반영으로 표시했습니다.")
        ),
        "decline": "거절했습니다.",
        "withdraw": "철회했습니다.",
    }[req.action]
    return {"success": True, "message": text, "session": s.model_dump(), **res, "open_count": len(contract_chat.open_items(s.id))}


@router.post("/sessions/{session_id}/ai-assist")
def contract_ai_assist(session_id: str, req: AiAssistReq):
    """스마트 계약 AI 문해력·OCR 도우미: 쉬운 계약서 요약, 자연어→표준 특약 다듬기, 계약 메모 사진 OCR."""
    s = CoSignSession.by_code_or_id(session_id)
    act = (req.action or "explain_contract").strip()
    if act == "explain_contract":
        data = contract_ai.explain_contract(s)
        return {"success": True, "action": act, "result": data, "message": "스마트 계약서 핵심 내용을 쉽게 요약했습니다."}
    if act == "draft_clause":
        data = contract_ai.draft_special_clause(req.text, s)
        return {"success": True, "action": act, "result": data, "message": "입력하신 내용을 표준 특약 문구로 다듬었습니다."}
    if act == "ocr_memo":
        if not req.consent:
            raise DomainError("사진을 AI로 분석하려면 전송 동의 체크가 필요합니다.")
        if not req.image_data_url:
            raise DomainError("분석할 사진 또는 캡처 이미지를 선택해 주세요.")
        data = contract_ai.ocr_memo_image(req.image_data_url)
        return {
            "success": data["status"] == "ok",
            "level": "success" if data["status"] == "ok" else "warning",
            "action": act,
            "result": data,
            "message": data["message"],
        }
    raise DomainError("지원하지 않는 AI 도우미 기능입니다.", hint="explain_contract / draft_clause / ocr_memo")
