"""
apps/rent4/backend/api.py — Rent4 API (/api/rent4, 단독 실행 시 /api).

임대인 (core.auth 로그인, 지점 관리자 이상)
    GET    /lessor/board?year=2026         호실 × 12개월 수납 현황
    POST   /lessor/bank/preview            입금 문자 붙여넣기 → 분석·매칭 제안 (저장 안 함)
    POST   /lessor/bank/commit             제안 확정 → Knowledge Pool 에 수납 기록 누적
    DELETE /lessor/payments/{id}           잘못 반영한 은행/수동 기록 취소
    POST   /lessor/import/preview|commit   연간 수납 Excel 표 붙여넣기 → 분석 / 반영
임차인 (호실 + 전화번호 뒤 4자리)
    POST   /tenant/lookup                  내 입금 반영 결과 조회
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends

from core.auth import User, require_role
from core.errors import Conflict, DomainError, NotFound

from . import bank_parser, excel_import, ledger
from .models import BankMessage, MonthMark, Payment, Room

router = APIRouter()
lessor = require_role("branch_admin", "superadmin")


def _branch(user: User) -> Optional[str]:
    return None if user.role == "superadmin" else user.branch


def _own_room(user: User, room_no: str) -> Room:
    room = Room.first(room=room_no)
    if not room or (_branch(user) and room.branch != _branch(user)):
        raise NotFound(f"{room_no}호를 찾을 수 없습니다.")
    return room


@router.get("/summary")
def summary() -> Dict[str, Any]:
    rooms = Room.all()
    return {"success": True, "rooms": len(rooms), "payments": len(Payment.all()),
            "bank_messages": len(BankMessage.all()), "buildings": sorted({r.building for r in rooms})}


# ── 임대인 ──
@router.get("/lessor/board")
def lessor_board(year: int = 0, user: User = Depends(lessor)) -> Dict[str, Any]:
    return {"success": True, **ledger.board(year or date.today().year, _branch(user))}


@router.post("/lessor/bank/preview")
async def bank_preview(payload: Dict[str, Any] = Body(...), user: User = Depends(lessor)) -> Dict[str, Any]:
    messages = bank_parser.split_messages(str(payload.get("text", "")))
    if not messages:
        raise DomainError("붙여 넣은 문자가 없습니다.", hint="은행 앱의 입금 알림 문자를 복사해 붙여 넣어 주세요.")
    seen = {m.fingerprint for m in BankMessage.all()}
    items = []
    for raw in messages[:100]:
        dep = await bank_parser.refine_with_ai(bank_parser.parse_message(raw))
        item = ledger.propose(dep, seen)
        if item.get("room") and _branch(user) and Room.first(room=item["room"]).branch != _branch(user):
            item.update(room=None, status="needs_review", message="다른 지점 호실과 일치합니다.")
        seen.add(dep.fingerprint)
        items.append(item)
    counts: Dict[str, int] = defaultdict(int)
    for it in items:
        counts[it["status"]] += 1
    return {"success": True, "items": items, "counts": counts}


@router.post("/lessor/bank/commit")
def bank_commit(payload: Dict[str, Any] = Body(...), user: User = Depends(lessor)) -> Dict[str, Any]:
    results = []
    for item in payload.get("items") or []:
        _own_room(user, str(item.get("room") or ""))
        results.append(ledger.commit(item))
    saved = [r for r in results if r["status"] == "saved"]
    if not saved and results:
        raise Conflict("새로 반영할 입금이 없습니다 (모두 이미 반영됨).")
    return {"success": True, "saved": len(saved), "results": results}


@router.delete("/lessor/payments/{payment_id}")
def delete_payment(payment_id: str, user: User = Depends(lessor)) -> Dict[str, Any]:
    p = Payment.first(id=payment_id)
    if not p:
        raise NotFound("수납 기록을 찾을 수 없습니다.")
    _own_room(user, p.room)
    if p.source == "excel":
        raise Conflict("Excel 에서 가져온 기록은 여기서 지울 수 없습니다.", hint="Excel 을 고친 뒤 다시 가져오세요.")
    p.delete()
    if p.bank_message_id:
        msg = BankMessage.first(id=p.bank_message_id)
        if msg:
            msg.payment_ids = [i for i in msg.payment_ids if i != p.id]
            msg.delete() if not msg.payment_ids else msg.save()
    return {"success": True, "deleted": payment_id}


@router.post("/lessor/import/preview")
def import_preview(payload: Dict[str, Any] = Body(...), user: User = Depends(lessor)) -> Dict[str, Any]:
    result = _parse(payload, user)
    return {"success": True, **result.summary(),
            "flagged_items": [{"room": p.room, "ym": p.ym, "raw": p.paid_date_raw, "flags": p.flags}
                              for p in result.payments if p.flags]}


@router.post("/lessor/import/commit")
def import_commit(payload: Dict[str, Any] = Body(...), user: User = Depends(lessor)) -> Dict[str, Any]:
    return {"success": True, **excel_import.apply_sheet(_parse(payload, user))}


def _parse(payload: Dict[str, Any], user: User) -> excel_import.SheetResult:
    branch = user.branch or "main"
    try:
        return excel_import.parse_sheet(str(payload.get("text", "")),
                                        building=str(payload.get("building") or "시크빌"), branch=branch)
    except ValueError as e:
        raise DomainError(str(e), hint="Excel 에서 제목 행부터 합계 행까지 선택해 복사(Ctrl+C)한 뒤 붙여 넣어 주세요.")


# ── 임차인 ──
_failures: Dict[str, List[float]] = defaultdict(list)
MAX_FAILURES, WINDOW_SECONDS = 5, 600


@router.post("/tenant/lookup")
def tenant_lookup(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    room_no = str(payload.get("room", "")).strip().removesuffix("호")
    phone4 = str(payload.get("phone4", "")).strip()
    now = time.time()
    recent = [t for t in _failures[room_no] if now - t < WINDOW_SECONDS]
    _failures[room_no] = recent
    if len(recent) >= MAX_FAILURES:
        raise Conflict("확인 실패가 반복되어 잠시 조회를 막았습니다.", hint="10분 뒤 다시 시도해 주세요.")
    try:
        view = ledger.tenant_view(room_no, phone4)
    except NotFound:
        _failures[room_no].append(now)
        raise
    _failures.pop(room_no, None)
    return {"success": True, **view}


def stats() -> Dict[str, Any]:
    return {"rooms": len(Room.all()), "payments": len(Payment.all()), "marks": len(MonthMark.all())}
