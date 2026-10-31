"""
apps/rent4/backend/api.py — Rent4 API (/api/rent4, 단독 실행 시 /api).

로그인은 플랫폼 공통 /auth/login. 로그인 뒤 GET /me 가 역할과 이동할 페이지를 알려 준다.

공통
    GET    /me                             역할(admin|lessor|tenant), 소속 건물·호실, 이동할 페이지
관리인 (superadmin)
    GET    /admin/buildings                건물 목록과 이번 달 요약
    GET|POST /admin/contracts              가맹 계약 목록 / 발행 (계약 링크 토큰 반환)
    POST   /admin/contracts/{id}/relink|paid|cancel|reset   (reset = 비밀번호 분실: 새 임시 비밀번호 + 새 링크)
계약 상대방 (로그인 전, 계약 링크의 토큰 t)
    GET    /contract/view?t=               계약 내용 · 상태 · (체결 후) 아이디와 임시 비밀번호
    POST   /contract/sign                  휴대폰 뒤 4자리 본인 확인 + 동의
임대인 (branch_admin = 자기 건물 / 관리인은 ?branch= 로 건물 지정)
    GET    /lessor/board?year=2026         호실 × 12개월 수납 현황
    POST   /lessor/bank/preview            입금 문자 붙여넣기 → 분석·매칭 제안 (저장 안 함)
    POST   /lessor/bank/commit             제안 확정 → Knowledge Pool 에 수납 기록 누적
    DELETE /lessor/payments/{id}           잘못 반영한 은행/수동 기록 취소
    POST   /lessor/import/preview|commit   연간 수납 Excel 표 붙여넣기 → 분석 / 반영
    GET    /lessor/accounts                임차인 계정 (아이디 · 첫 비밀번호 코드 · 사용 여부)
    POST   /lessor/accounts/{room}/reset   임차인 계정 첫 비밀번호 재발급
    GET|POST /lessor/contracts             임대차 계약 목록 / 발행
    POST   /lessor/contracts/{id}/relink|paid|cancel|reset
임차인 (로그인)
    GET    /tenant/me                      내 호실의 입금 반영 결과
    POST   /account/password               비밀번호 변경 (관리인·임대인·임차인, 언제든. 첫 로그인 때는 필수)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends

from core.auth import User, require_role, require_user
from core.errors import Conflict, DomainError, Forbidden, NotFound

from . import accounts, bank_parser, contracts, excel_import, ledger
from .models import BankMessage, Building, MonthMark, Payment, Room

router = APIRouter()
admin_only = require_role(*accounts.ADMIN_ROLES)
staff = require_role(*accounts.ADMIN_ROLES, *accounts.LESSOR_ROLES)
tenant_only = require_role(accounts.TENANT_ROLE)


def _scope(user: User, branch: str = "") -> Building:
    """임대인은 자기 건물, 관리인은 ?branch= 로 고른 건물."""
    if user.role in accounts.ADMIN_ROLES:
        if not branch:
            raise DomainError("건물을 선택해 주세요.", hint="관리인 화면에서 건물을 고른 뒤 들어오세요.")
        return accounts.building_of(branch)
    acc = accounts.lessor_account(user)
    if acc.must_change:
        raise Forbidden("처음 로그인하셨습니다. 비밀번호를 먼저 바꿔 주세요.")
    if branch and branch != acc.branch:
        raise Forbidden("다른 건물의 정보는 볼 수 없습니다.")
    return accounts.building_of(acc.branch)


@router.get("/summary")
def summary() -> Dict[str, Any]:
    return {"success": True, "buildings": len(Building.all()), "rooms": len(Room.all())}


@router.get("/me")
def me(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, **accounts.whoami(user)}


# ── 관리인 ──
@router.get("/admin/buildings")
def admin_buildings(user: User = Depends(admin_only)) -> Dict[str, Any]:
    items = [{"code": b.code, "name": b.name, "branch": b.branch, **ledger.building_stats(b.branch)}
             for b in Building.all()]
    return {"success": True, "buildings": items}


@router.get("/admin/contracts")
def admin_contracts(user: User = Depends(admin_only)) -> Dict[str, Any]:
    return {"success": True, "contracts": contracts.listing("membership")}


@router.post("/admin/contracts")
def admin_contract_create(payload: Dict[str, Any] = Body(...), user: User = Depends(admin_only)) -> Dict[str, Any]:
    c, token = contracts.create_membership(user, payload)
    return {"success": True, "contract": contracts.public(c), "token": token}


@router.post("/admin/contracts/{contract_id}/{action}")
def admin_contract_action(contract_id: str, action: str, user: User = Depends(admin_only)) -> Dict[str, Any]:
    return _contract_action(contracts.require(contract_id, "membership"), action)


def _contract_action(c, action: str) -> Dict[str, Any]:
    if action == "relink":
        return {"success": True, "token": contracts.relink(c), "contract": contracts.public(c)}
    if action == "paid":
        return {"success": True, "contract": contracts.public(contracts.confirm_paid(c))}
    if action == "cancel":
        return {"success": True, "contract": contracts.public(contracts.cancel(c))}
    if action == "reset":
        return {"success": True, "token": contracts.reset_password(c), "contract": contracts.public(c)}
    raise NotFound(f"알 수 없는 작업 '{action}' 입니다.")


# ── 계약 상대방 (링크 토큰, 로그인 전) ──
@router.get("/contract/view")
def contract_view(t: str = "") -> Dict[str, Any]:
    return {"success": True, "contract": contracts.public(contracts.by_token(t), for_party=True)}


@router.post("/contract/sign")
def contract_sign(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    c = contracts.sign(str(payload.get("t", "")), str(payload.get("phone4", "")), bool(payload.get("agree")))
    return {"success": True, "contract": contracts.public(c, for_party=True)}


# ── 임대인 ──
@router.get("/lessor/contracts")
def lessor_contracts(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    return {"success": True, "contracts": contracts.listing("lease", _scope(user, branch).branch)}


@router.post("/lessor/contracts")
def lessor_contract_create(payload: Dict[str, Any] = Body(...), branch: str = "",
                           user: User = Depends(staff)) -> Dict[str, Any]:
    c, token = contracts.create_lease(user, _scope(user, branch).branch, payload)
    return {"success": True, "contract": contracts.public(c), "token": token}


@router.post("/lessor/contracts/{contract_id}/{action}")
def lessor_contract_action(contract_id: str, action: str, branch: str = "",
                           user: User = Depends(staff)) -> Dict[str, Any]:
    return _contract_action(contracts.require(contract_id, "lease", _scope(user, branch).branch), action)


# ── 임대인 ──
@router.get("/lessor/board")
def lessor_board(year: int = 0, branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    return {"success": True, "building": b.name, **ledger.board(year or date.today().year, b.branch)}


@router.post("/lessor/bank/preview")
async def bank_preview(payload: Dict[str, Any] = Body(...), branch: str = "",
                       user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    messages = bank_parser.split_messages(str(payload.get("text", "")))
    if not messages:
        raise DomainError("붙여 넣은 문자가 없습니다.", hint="은행 앱의 입금 알림 문자를 복사해 붙여 넣어 주세요.")
    seen = {m.fingerprint for m in BankMessage.find(branch=b.branch)}
    items = []
    for raw in messages[:100]:
        dep = await bank_parser.refine_with_ai(bank_parser.parse_message(raw))
        items.append(ledger.propose(dep, seen, b.branch))
        seen.add(dep.fingerprint)
    counts: Dict[str, int] = defaultdict(int)
    for it in items:
        counts[it["status"]] += 1
    return {"success": True, "items": items, "counts": counts}


@router.post("/lessor/bank/commit")
def bank_commit(payload: Dict[str, Any] = Body(...), branch: str = "",
                user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    results = [ledger.commit(item, b.branch) for item in payload.get("items") or []]
    saved = [r for r in results if r["status"] == "saved"]
    if not saved and results:
        raise Conflict("새로 반영할 입금이 없습니다 (모두 이미 반영됨).")
    return {"success": True, "saved": len(saved), "results": results}


@router.delete("/lessor/payments/{payment_id}")
def delete_payment(payment_id: str, branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    p = Payment.first(id=payment_id, branch=b.branch)
    if not p:
        raise NotFound("수납 기록을 찾을 수 없습니다.")
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
def import_preview(payload: Dict[str, Any] = Body(...), branch: str = "",
                   user: User = Depends(staff)) -> Dict[str, Any]:
    result = _parse(payload, _scope(user, branch))
    return {"success": True, **result.summary(),
            "flagged_items": [{"room": p.room, "ym": p.ym, "raw": p.paid_date_raw, "flags": p.flags}
                              for p in result.payments if p.flags]}


@router.post("/lessor/import/commit")
def import_commit(payload: Dict[str, Any] = Body(...), branch: str = "",
                  user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    report = excel_import.apply_sheet(_parse(payload, b), b.branch)
    return {"success": True, **report, "accounts": accounts.sync_tenant_accounts()}


def _parse(payload: Dict[str, Any], b: Building) -> excel_import.SheetResult:
    try:
        return excel_import.parse_sheet(str(payload.get("text", "")), building=b.name, branch=b.branch)
    except ValueError as e:
        raise DomainError(str(e), hint="Excel 에서 제목 행부터 합계 행까지 선택해 복사(Ctrl+C)한 뒤 붙여 넣어 주세요.")


@router.get("/lessor/accounts")
def lessor_accounts(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    return {"success": True, "building": b.name, "accounts": accounts.list_accounts(b.branch)}


@router.post("/lessor/accounts/{room_no}/reset")
def lessor_account_reset(room_no: str, branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    acc = accounts.reset_account(_scope(user, branch).branch, room_no)
    return {"success": True, "username": acc.username, "initial_code": acc.initial_code}


# ── 임차인 ──
@router.get("/tenant/me")
def tenant_me(user: User = Depends(tenant_only)) -> Dict[str, Any]:
    acc = accounts.tenant_account(user)
    if acc.must_change:
        raise Forbidden("처음 로그인하셨습니다. 비밀번호를 먼저 바꿔 주세요.", hint="임대인에게 받은 코드는 첫 로그인에만 쓸 수 있습니다.")
    return {"success": True, "building": accounts.building_of(acc.branch).name, **ledger.tenant_view(acc.branch, acc.room)}


@router.post("/account/password")
def account_password(payload: Dict[str, Any] = Body(...), user: User = Depends(require_user)) -> Dict[str, Any]:
    accounts.change_password(user, str(payload.get("current", "")), str(payload.get("new", "")))
    return {"success": True}


def stats() -> Dict[str, Any]:
    return {"buildings": len(Building.all()), "rooms": len(Room.all()), "payments": len(Payment.all()),
            "marks": len(MonthMark.all())}


def startup() -> Optional[Dict[str, int]]:
    accounts.ensure_buildings()
    return accounts.sync_tenant_accounts()
