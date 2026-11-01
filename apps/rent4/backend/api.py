"""
apps/rent4/backend/api.py — Rent4 API (/api/rent4, 단독 실행 시 /api).

로그인은 플랫폼 공통 /auth/login. 로그인 뒤 GET /me 가 역할과 이동할 페이지를 알려 준다.

공통
    GET    /me                             역할(admin|lessor|tenant), 소속 건물·호실, 이동할 페이지
    GET    /setup/view?t=  POST /setup/start      관리인 설정 링크 → 바로 로그인 (비밀번호를 정하기 전까지)
관리인 (superadmin)
    GET    /admin/buildings                건물 목록과 이번 달 요약
    GET|POST /admin/contracts              가맹 계약 목록 / 발행 (계약 링크 토큰 반환)
    POST   /admin/contracts/{id}/paid|cancel|reset   (reset = 비밀번호 초기화 + 새 링크)
계약 상대방 (로그인 전, 계약 링크의 토큰 t)
    GET    /contract/view?t=               계약 내용 · 상태
    POST   /contract/join                  1단계: 휴대폰 뒤 4자리 + 비밀번호 설정 → 바로 로그인 (링크 만료)
    GET    /contract/mine  POST /contract/mine/sign|continue   로그인한 상대방: 진행 확인 · 동의 · 체결 뒤 최종 계정으로
    POST   /login                          아이디 또는 휴대폰 번호 + 비밀번호
    POST   /contract/start                 체결된 계약 링크 → 바로 로그인 (비밀번호를 정하기 전까지)
    GET    /invite/view?t=  POST /invite/start  임차인 안내 링크: 휴대폰 뒤 4자리 확인 → 바로 로그인
    POST   /contract/sign                  휴대폰 뒤 4자리 본인 확인 + 동의
임대인 (branch_admin = 자기 건물 / 관리인은 ?branch= 로 건물 지정)
    GET    /lessor/board?year=2026         호실 × 12개월 수납 현황
    POST   /lessor/bank/preview            입금 문자 붙여넣기 → 분석·매칭 제안 (저장 안 함)
    POST   /lessor/bank/commit             제안 확정 → Knowledge Pool 에 수납 기록 누적
    DELETE /lessor/payments/{id}           잘못 반영한 은행/수동 기록 취소
    POST   /lessor/import/preview|commit   연간 수납 Excel 표 붙여넣기 → 분석 / 반영
    GET    /lessor/accounts                임차인 계정 (아이디 · 링크 발송 · 사용 여부)
    POST   /lessor/accounts/{room}/reset   비밀번호 초기화 + 새 안내 링크 (처음 안내도 이것으로)
    GET|POST /lessor/contracts             임대차 계약 목록 / 발행
    POST   /lessor/contracts/{id}/paid|cancel|reset
임차인 (로그인)
    GET    /tenant/me                      내 호실의 입금 반영 결과
    POST   /account/password               비밀번호 설정·변경 (링크로 처음 들어온 경우 현재 비밀번호 없이 설정)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import RedirectResponse

from core.auth import User, current_user, require_role, require_user
from core.errors import Conflict, DomainError, Forbidden, NotFound

from . import accounts, bank_parser, chat, contracts, excel_import, finder, ledger, oauth
from .models import BankMessage, Building, MonthMark, Payment, Room

router = APIRouter()
staff = require_role(*accounts.ADMIN_ROLES, *accounts.LESSOR_ROLES)


def admin_only(user: User = Depends(require_role(*accounts.ADMIN_ROLES))) -> User:
    accounts.require_ready(user)
    return user


tenant_only = require_role(accounts.TENANT_ROLE)


def _scope(user: User, branch: str = "") -> Building:
    """임대인은 자기 건물, 관리인은 ?branch= 로 고른 건물."""
    if user.role in accounts.ADMIN_ROLES:
        accounts.require_ready(user)
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


@router.post("/login")
def login(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """아이디 또는 휴대폰 번호로 로그인 (core /auth/login 과 같은 토큰)."""
    return {"success": True, **accounts.login(str(payload.get("username", "")), str(payload.get("password", "")))}


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
    c = contracts.create_membership(user, payload)
    return {"success": True, "contract": contracts.public(c), **_start_link(c, contracts.issue_start_link(c))}


@router.post("/admin/contracts/{contract_id}/{action}")
def admin_contract_action(contract_id: str, action: str, user: User = Depends(admin_only)) -> Dict[str, Any]:
    return _contract_action(contracts.require(contract_id, "membership"), action)


def _contract_action(c, action: str) -> Dict[str, Any]:
    if action == "paid":
        return {"success": True, "contract": contracts.public(contracts.confirm_paid(c))}
    if action == "cancel":
        return {"success": True, "contract": contracts.public(contracts.cancel(c))}
    if action == "link":
        return {"success": True, "contract": contracts.public(c), **_start_link(c, contracts.issue_start_link(c))}
    raise NotFound(f"알 수 없는 작업 '{action}' 입니다.")


def _start_link(c, token: str) -> Dict[str, Any]:
    return {"link": f"reset.html?t={token}", "expires_hours": contracts.START_LINK_TTL // 3600}


# ── 관리인 설정 링크 (로그인 전, 서버 콘솔 scripts/admin_link.py 가 발급) ──
@router.get("/setup/view")
def setup_view(t: str = "") -> Dict[str, Any]:
    return {"success": True, **accounts.admin_link_view(t)}


@router.post("/setup/start")
def setup_start(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return {"success": True, **accounts.admin_link_start(str(payload.get("t", "")))}


# ── 계약 상대방 (링크 토큰, 로그인 전) ──
@router.get("/contract/mine")
def contract_mine(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, "contract": contracts.public(contracts.party_contract(user), for_party=True)}


@router.post("/contract/mine/sign")
def contract_mine_sign(payload: Dict[str, Any] = Body(...), user: User = Depends(require_user)) -> Dict[str, Any]:
    c = contracts.sign(user, bool(payload.get("agree")))
    return {"success": True, "contract": contracts.public(c, for_party=True)}


@router.post("/contract/mine/continue")
def contract_mine_continue(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, **contracts.continue_as_account(user)}


# ── 임대인 ──
@router.get("/lessor/contracts")
def lessor_contracts(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    return {"success": True, "contracts": contracts.listing("lease", _scope(user, branch).branch)}


@router.post("/lessor/contracts")
def lessor_contract_create(payload: Dict[str, Any] = Body(...), branch: str = "",
                           user: User = Depends(staff)) -> Dict[str, Any]:
    c = contracts.create_lease(user, _scope(user, branch).branch, payload)
    return {"success": True, "contract": contracts.public(c), **_start_link(c, contracts.issue_start_link(c))}


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


@router.get("/oauth/providers")
def oauth_providers(user=Depends(current_user)) -> Dict[str, Any]:
    return {"success": True, "providers": oauth.configured(), "linked": oauth.linked(user.username) if user else []}


@router.post("/oauth/{provider}/start")
def oauth_start(provider: str, request: Request, payload: Dict[str, Any] = Body(...), user=Depends(current_user)) -> Dict[str, Any]:
    redirect_uri = str(request.url).split("?")[0].rsplit("/", 1)[0] + "/callback"
    return {"success": True, **oauth.start(provider, str(payload.get("purpose", "")), redirect_uri, user,
                                           str(payload.get("back", "")))}


@router.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, request: Request, code: str = "", state: str = "") -> RedirectResponse:
    page = oauth.callback(provider, code, state)
    api_base = str(request.url).split("?")[0].rsplit("/oauth/", 1)[0]                 # …/api/rent4 또는 …/api
    site = api_base.replace("/api/rent4", "/rent4/") if api_base.endswith("/api/rent4") else api_base[: -len("/api")] + "/"
    return RedirectResponse(site + page, status_code=302)


@router.post("/lessor/accounts/{room_no}/reset")
def lessor_account_reset(room_no: str, branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    """임차인 비밀번호 초기화 → 한 번만 쓰는 재설정 링크 (임대인이 채팅으로 전달)."""
    acc, room, token = accounts.issue_reset_link(_scope(user, branch).branch, room_no)
    delivered = "share"
    if chat.online(acc.username):        # 임차인이 지금 접속 중이면 채팅창으로 바로 전달
        chat.post(acc.username, user.username, f"임대인 {user.name or user.username}",
                  f"{room.tenant_name}님, 비밀번호가 초기화되었습니다. 아래 버튼을 눌러 새 비밀번호를 정해 주세요. "
                  f"({accounts.RESET_LINK_TTL // 3600}시간 안에, 한 번만 사용)",
                  link=f"reset.html?t={token}", link_label="🔑 새 비밀번호 정하기")
        delivered = "chat"
    return {"success": True, "token": token, "username": acc.username, "room": acc.room, "delivered": delivered,
            "tenant_name": room.tenant_name, "phone": room.phone, "expires_hours": accounts.RESET_LINK_TTL // 3600}


# ── 채팅창 (로그인한 사용자) ──
@router.get("/chat/inbox")
def chat_inbox(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, "messages": chat.inbox(user.username)}


@router.post("/chat/read/{message_id}")
def chat_read(message_id: str, user: User = Depends(require_user)) -> Dict[str, Any]:
    chat.mark_read(user.username, message_id)
    return {"success": True}


# ── 비밀번호 재설정 링크 (로그인 전) ──
@router.get("/reset/view")
def reset_view(t: str = "") -> Dict[str, Any]:
    """임차인 재설정 링크와 계약 시작 링크가 같은 화면(reset.html)을 쓴다."""
    try:
        return {"success": True, **accounts.reset_link_view(t)}
    except NotFound:
        return {"success": True, **contracts.start_link_view(t)}


@router.post("/reset/set")
def reset_set(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    t, new = str(payload.get("t", "")), str(payload.get("new", ""))
    try:
        return {"success": True, **accounts.reset_link_set(t, new)}
    except NotFound:
        return {"success": True, **contracts.start_link_set(t, new)}


# ── 아이디 · 비밀번호 찾기 (로그인 전, 구글 본인 확인 뒤) ──
@router.get("/find/view")
def find_view(f: str = "") -> Dict[str, Any]:
    return {"success": True, **finder.view(f)}


@router.post("/find/id")
def find_id(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    g = lambda k: str(payload.get(k, ""))  # noqa: E731
    return {"success": True, **finder.find_id(g("f"), g("name"), g("phone"), g("property"))}


@router.post("/find/pw/verify")
def find_pw_verify(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    g = lambda k: str(payload.get(k, ""))  # noqa: E731
    return {"success": True, **finder.verify_pw(g("f"), g("username"), g("name"), g("phone"), g("property"))}


@router.post("/find/pw/set")
def find_pw_set(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return {"success": True, **finder.set_pw(str(payload.get("f", "")), str(payload.get("new", "")))}


# ── 임차인 ──
@router.get("/tenant/me")
def tenant_me(user: User = Depends(tenant_only)) -> Dict[str, Any]:
    acc = accounts.tenant_account(user)
    if acc.must_change:
        raise Forbidden("처음 로그인하셨습니다. 비밀번호를 먼저 바꿔 주세요.", hint="안내 링크에서 받은 임시 비밀번호는 첫 로그인에만 쓸 수 있습니다.")
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
