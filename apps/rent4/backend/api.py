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
from fastapi.responses import FileResponse, RedirectResponse

from core.auth import Unauthorized, User, current_user, require_role, require_user
from core.errors import Conflict, DomainError, Forbidden, NotFound

from . import accounts, bank_parser, chat, contracts, excel_import, finder, ledger, leaseflow, listings, members, oauth, paste
from .models import BankMessage, Building, Contract, MonthMark, Payment, Room

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


# ── 매물 (공개 목록은 로그인 없이) ──
SEED_LISTINGS = [
    {
        "id": "room_main_203",
        "branch": "main",
        "building_name": "시크빌 본점",
        "address": "서울특별시 관악구 남부순환로 1820-12",
        "room_no": "203",
        "name": "203호 (채광 최상 남향 즉시입주)",
        "room_category": "주거용 원룸",
        "floor": 2,
        "area_sqm": 23.8,
        "lease_type": "월세",
        "pay_timing": "후불",
        "deposit": 5000000,
        "monthly_rent": 500000,
        "maintenance_fee": 50000,
        "vat_applicable": False,
        "available_from": "즉시입주",
        "pre_moveout_notice": False,
        "clean_title": True,
        "status_description": "신규 천장형 에어컨·빌트인 냉장고 완비, 복비 0원 직거래 즉시입주",
        "options": [
            "시스템 에어컨",
            "드럼 세탁기",
            "빌트인 냉장고",
            "인덕션 쿡탑",
            "디지털 도어락"
        ],
        "image": "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
        "lessor_name": "김임대 (시크빌 대표)",
        "lessor_phone": "010-2222-1111"
    },
    {
        "id": "room_sinbi_202",
        "branch": "sinbi",
        "building_name": "신비주택 & 상가",
        "address": "서울특별시 마포구 월드컵북로 64-5",
        "room_no": "202",
        "name": "202호 (복층형 오피스텔 즉시입주)",
        "room_category": "오피스텔",
        "floor": 2,
        "area_sqm": 29.0,
        "lease_type": "월세",
        "pay_timing": "후불",
        "deposit": 10000000,
        "monthly_rent": 620000,
        "maintenance_fee": 60000,
        "vat_applicable": False,
        "available_from": "즉시입주",
        "pre_moveout_notice": False,
        "clean_title": True,
        "status_description": "홍대입구 인접 층고 높은 복층 구조, 채광 우수",
        "options": [
            "시스템 에어컨",
            "드럼 세탁기",
            "빌트인 냉장고",
            "인덕션 쿡탑",
            "디지털 도어락"
        ],
        "image": "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
        "lessor_name": "최신비 (신비주택 대표)",
        "lessor_phone": "010-4444-3333"
    },
    {
        "id": "room_sunho_302",
        "branch": "sunho",
        "building_name": "선호아트빌",
        "address": "서울특별시 동작구 상도로 245-8",
        "room_no": "302",
        "name": "302호 (조용한 주택가 풀옵션 공실)",
        "room_category": "주거용 원룸",
        "floor": 3,
        "area_sqm": 20.4,
        "lease_type": "월세",
        "pay_timing": "후불",
        "deposit": 3000000,
        "monthly_rent": 450000,
        "maintenance_fee": 40000,
        "vat_applicable": False,
        "available_from": "즉시입주",
        "pre_moveout_notice": False,
        "clean_title": True,
        "status_description": "도배·장판 신규 시공 완료, 지하철 도보 4분",
        "options": [
            "시스템 에어컨",
            "드럼 세탁기",
            "빌트인 냉장고",
            "인덕션 쿡탑",
            "디지털 도어락"
        ],
        "image": "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
        "lessor_name": "박선호 (선호아트빌 대표)",
        "lessor_phone": "010-3333-2222"
    }
]


@router.get("/pool/landing")
def portal_landing() -> Dict[str, Any]:
    """OpenRent 직거래 포털 랜딩 페이지용 실시간 매물 및 통계 반환."""
    bldgs = Building.all()
    rooms = Room.all()
    user_listings = listings.public_list()
    portal_listings = list(SEED_LISTINGS)
    if user_listings:
        portal_listings = user_listings + portal_listings

    branches_info = [
        {"branch": "main", "building_name": "시크빌 본점", "address": "서울특별시 관악구 남부순환로 1820-12", "manager_phone": "010-2222-1111"},
        {"branch": "sunho", "building_name": "선호아트빌", "address": "서울특별시 동작구 상도로 245-8", "manager_phone": "010-3333-2222"},
        {"branch": "sinbi", "building_name": "신비주택 & 상가", "address": "서울특별시 마포구 월드컵북로 64-5", "manager_phone": "010-4444-3333"},
    ]

    demo_accounts = [
        {
            "group": "관리인",
            "badge": "👑 총괄 관리인 전용 앱",
            "label": "본사 총괄 관리인 (admin / 0202)",
            "identifier": "admin",
            "password": "0202",
            "target_desc": "/rent4/관리인.html"
        },
        {
            "group": "임대인",
            "badge": "🏢 임대인(main)",
            "label": "김임대 (시크빌 대표)",
            "identifier": "chic",
            "password": "1212",
            "target_desc": "/rent4/임대인.html?branch=main"
        },
        {
            "group": "임대인",
            "badge": "🏢 임대인(sunho)",
            "label": "박선호 (선호아트빌 대표)",
            "identifier": "sunho",
            "password": "1212",
            "target_desc": "/rent4/임대인.html?branch=sunho"
        },
        {
            "group": "임대인",
            "badge": "🏢 임대인(sinbi)",
            "label": "최신비 (신비주택 대표)",
            "identifier": "sinbi",
            "password": "1212",
            "target_desc": "/rent4/임대인.html?branch=sinbi"
        },
        {
            "group": "임차인",
            "badge": "🔑 임차인(201호)",
            "label": "이민수 (010-8888-2010)",
            "identifier": "010-8888-2010",
            "password": "1212",
            "target_desc": "/rent4/임차인.html"
        }
    ]

    return {
        "success": True,
        "stats": {
            "total_buildings": len(bldgs) or 3,
            "total_rooms": len(rooms) or 7,
            "occupied_rooms": sum(1 for r in rooms if r.status == "occupied"),
            "vacant_published_count": len(portal_listings),
            "active_contracts": len([c for c in Contract.all() if c.status == "active"]),
            "open_cosign_count": 0,
            "pool_revision": 1,
        },
        "branches": branches_info,
        "listings": portal_listings,
        "open_sessions": [],
        "demo_accounts": demo_accounts,
    }


def _verification(user: User) -> Dict[str, Any]:
    """임대인 보호: 예비 임차인은 구글(또는 카카오) 본인 확인을 마친 회원만 임대인과 만날 수 있다."""
    from .members import MemberAccount
    links = oauth.linked(user.username)
    google = next((l for l in links if l["provider"] == "google"), None)
    member = MemberAccount.first(username=user.username)
    staff_role = user.role in accounts.ADMIN_ROLES or user.role in accounts.LESSOR_ROLES
    return {
        "verified": bool(staff_role or links),
        "provider": (google or (links[0] if links else {})).get("label", ""),
        "name": (member.name if member else "") or user.name,
        "phone": member.phone if member else "",
    }


def _require_verified(user: Optional[User]) -> Dict[str, Any]:
    if user is None:
        raise Forbidden("임대인 보호를 위해 로그인 후 이용할 수 있습니다.",
                        hint="회원가입(구글 본인 확인) 또는 로그인한 뒤 다시 시도해 주세요.")
    v = _verification(user)
    if not v["verified"]:
        raise Forbidden("임대인 보호를 위해 본인 확인(구글)을 마친 회원만 방문 문의 · 계약 신청을 할 수 있습니다.",
                        hint="내 정보에서 구글 계정을 연결하거나 회원가입(구글 본인 확인)을 진행해 주세요.")
    return v


def _session_payload(user: User, token: str, hint: str = "") -> Dict[str, Any]:
    info = accounts.whoami(user)
    v = _verification(user)
    home = info.get("home", "index.html")
    if info.get("kind") == "lessor" and hint in ("add_listing", "broker"):
        home = "임대인.html#listings"
    redirect_url = "/rent4/" + home
    return {
        "success": True,
        "message": f"{info.get('name') or user.username}님, 로그인되었습니다.",
        "token": token,
        "user": {
            "id": user.username,
            "name": info.get("name") or user.username,
            "role": info.get("kind", "member"),
            "role_label": info.get("label", ""),
            "branch": (info.get("building") or {}).get("branch", ""),
            "phone": v["phone"],
            "verified": v["verified"],
            "verified_by": v["provider"],
            "redirect_url": redirect_url,
        },
        "redirect_url": redirect_url,
    }


@router.post("/pool/auth/login")
def portal_login(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """포털 로그인 → 역할(관리인 · 임대인 · 임차인 · 회원)에 맞는 페이지 주소와 본인 확인 상태를 돌려준다."""
    from core.auth import issue_token
    ident = str(payload.get("identifier", "")).strip()
    pw = str(payload.get("password", "")).strip()
    if "@" in ident:                                  # 구글 본인 확인 때 연결한 이메일 → 아이디
        link = next((l for l in oauth.SocialLink.all() if (l.email or "").lower() == ident.lower()), None)
        if link is None:
            raise Unauthorized("이 이메일로 가입한 회원이 없습니다.",
                               hint="회원가입 탭에서 구글로 본인 확인 후 가입하거나, 아이디 · 휴대폰 번호로 로그인해 주세요.")
        ident = link.username

    demo_passwords = {"admin": "0202", "chic": "1212", "sunho": "1212", "sinbi": "1212"}
    u = User.by_username(ident)
    if u and ident in demo_passwords and pw == demo_passwords[ident]:
        from .members import touch
        touch(u.username)
        res = {"token": issue_token(u), "user": u.public()}
    else:
        res = accounts.login(ident, pw)
    user = User.by_username(res["user"]["username"])
    return _session_payload(user, res["token"], str(payload.get("target_hint", "")))


@router.get("/pool/auth/session")
def portal_session(request: Request, user=Depends(current_user)) -> Dict[str, Any]:
    """이미 로그인한 토큰(구글 로그인 · 가입 직후 포함)으로 포털 세션 정보를 다시 받는다."""
    if user is None:
        raise Forbidden("로그인이 필요합니다.")
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return _session_payload(user, token)


@router.post("/pool/inquiries")
def portal_inquiry(payload: Dict[str, Any] = Body(...), user=Depends(current_user)) -> Dict[str, Any]:
    """본인 확인된 회원의 방문 문의 → 그 건물 임대인 채팅창으로 (검증된 실명 · 연락처 · 인증 수단 포함)."""
    v = _require_verified(user)
    branch = str(payload.get("branch", "")).strip()
    room_no = str(payload.get("room_no", "")).strip()
    msg = str(payload.get("message", "")).strip()
    visit = str(payload.get("visit_date", "")).strip()
    if not msg:
        raise DomainError("문의 내용을 입력해 주세요.")
    from .models import LessorAccount
    lessor = LessorAccount.first(branch=branch)
    if lessor is None:
        raise NotFound("이 매물의 임대인 계정을 찾을 수 없습니다.", hint="잠시 뒤 다시 시도하거나 관리인에게 문의해 주세요.")
    phone = v["phone"] or str(payload.get("sender_phone", "")).strip()
    text = (f"[{room_no}호 방문 문의 · 🛡️ {v['provider'] or '본인'} 확인 회원]\n"
            f"이름: {v['name']} · 연락처: {phone or '-'}\n"
            + (f"희망 일시: {visit}\n" if visit else "") + msg)
    chat.post(lessor.username, user.username, f"{v['name']} (본인 확인 회원)", text)
    return {"success": True, "message": "본인 확인된 회원 정보와 함께 임대인에게 문의를 보냈습니다."}


@router.post("/pool/cosign-open")
def portal_cosign_open(payload: Dict[str, Any] = Body(...), user=Depends(current_user)) -> Dict[str, Any]:
    _require_verified(user)
    return {"success": True, "message": "계약 신청 화면으로 이동합니다.", "redirect_url": "/rent4/contract.html"}


# ── 매물 (공개 목록은 로그인 없이) ──
@router.get("/listings")
def public_listings() -> Dict[str, Any]:
    return {"success": True, "listings": listings.public_list()}


@router.get("/listings/{listing_id}")
def public_listing(listing_id: str) -> Dict[str, Any]:
    return {"success": True, "listing": listings.public(listings.get_open(listing_id))}


@router.post("/listings/{listing_id}/apply")
def listing_apply(listing_id: str, payload: Dict[str, Any] = Body(...), user: User = Depends(require_user)) -> Dict[str, Any]:
    """회원의 계약 신청 → 임대인 채팅창에 안내 → contract.html 에서 진행."""
    c = contracts.apply(user, listing_id, payload)
    return {"success": True, "contract": contracts.public(c, for_party=True), "home": "contract.html"}


@router.get("/lessor/listings")
def lessor_listings(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    return {"success": True, "listings": listings.lessor_list(_scope(user, branch).branch)}


@router.post("/lessor/listings")
def lessor_listing_publish(payload: Dict[str, Any] = Body(...), branch: str = "",
                           user: User = Depends(staff)) -> Dict[str, Any]:
    l = listings.publish(_scope(user, branch).branch, user.username, payload)
    return {"success": True, "listing": listings.public(l)}


@router.post("/lessor/listings/{listing_id}/close")
def lessor_listing_close(listing_id: str, branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    listings.close(_scope(user, branch).branch, listing_id)
    return {"success": True}


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


@router.get("/admin/buildings/{branch}")
def admin_building_detail(branch: str, year: int = 0, user: User = Depends(admin_only)) -> Dict[str, Any]:
    """가맹점 펼쳐보기: 가맹 계약 · 이번 달 수납현황 · 월별 수납 합계."""
    b = accounts.building_of(branch)
    year = year or date.today().year
    return {"success": True, "building": {"code": b.code, "name": b.name, "branch": b.branch},
            "contracts": contracts.listing("membership", b.branch), "month": ledger.month_summary(b.branch),
            "year": year, "totals": ledger.year_totals(b.branch, year)}


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
        if c.party_username and contracts._is_member(c.party_username):
            raise Conflict("회원이 신청한 계약은 링크 없이 로그인한 상태로 진행합니다.")
        return {"success": True, "contract": contracts.public(c), **_start_link(c, contracts.issue_start_link(c))}
    if action == "downpay":                       # 계약금 확인 = 계약 완료 + 안내문
        return {"success": True, "contract": contracts.public(leaseflow.down_paid(c))}
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


# ── 회원 계약 절차 (leaseflow): 임차인 ──
@router.post("/contract/mine/approve")
def contract_mine_approve(payload: Dict[str, Any] = Body(...), user: User = Depends(require_user)) -> Dict[str, Any]:
    """조건 확인 → 개인정보(주민번호 없음) · 전입일 · 기간 입력 후 승인."""
    c = leaseflow.approve(contracts.party_contract(user), payload)
    return {"success": True, "contract": contracts.public(c, for_party=True)}


@router.post("/contract/mine/confirm")
def contract_mine_confirm(payload: Dict[str, Any] = Body(...), user: User = Depends(require_user)) -> Dict[str, Any]:
    """확정 계약서 최종 승인 → 계약금 송금."""
    c = leaseflow.confirm(contracts.party_contract(user), payload)
    return {"success": True, "contract": contracts.public(c, for_party=True)}


@router.get("/contracts/{contract_id}/document")
def contract_document(contract_id: str, user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, **leaseflow.document(user, contract_id)}


@router.get("/contracts/{contract_id}/registry-file")
def contract_registry_file(contract_id: str, user: User = Depends(require_user)) -> FileResponse:
    c = Contract.first(id=contract_id)
    if c is None or not leaseflow.can_view(user, c):
        raise Forbidden("볼 수 없는 파일입니다.")
    path, mime = leaseflow.registry_file(c)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


# ── 임대인 ──
@router.get("/lessor/contracts")
def lessor_contracts(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    return {"success": True, "contracts": contracts.listing("lease", _scope(user, branch).branch)}


@router.post("/lessor/contracts")
def lessor_contract_create(payload: Dict[str, Any] = Body(...), branch: str = "",
                           user: User = Depends(staff)) -> Dict[str, Any]:
    c = contracts.create_lease(user, _scope(user, branch).branch, payload)
    return {"success": True, "contract": contracts.public(c), **_start_link(c, contracts.issue_start_link(c))}


@router.post("/lessor/contracts/{contract_id}/offer")
def lessor_contract_offer(contract_id: str, payload: Dict[str, Any] = Body(...), branch: str = "",
                          user: User = Depends(staff)) -> Dict[str, Any]:
    """현장 점검 뒤 조건 제시 → 임차인 채팅 + 임대인 휴대폰으로 보낼 문구."""
    c = leaseflow.offer(contracts.require(contract_id, "lease", _scope(user, branch).branch), payload)
    return {"success": True, "contract": contracts.public(c), "share": leaseflow.offer_message(c)}


@router.post("/lessor/contracts/{contract_id}/registry")
def lessor_contract_registry(contract_id: str, payload: Dict[str, Any] = Body(...), branch: str = "",
                             user: User = Depends(staff)) -> Dict[str, Any]:
    """등기부 확인 결과(체크리스트 + 파일) → 계약 내용 확정 저장."""
    c = leaseflow.registry(contracts.require(contract_id, "lease", _scope(user, branch).branch), payload)
    return {"success": True, "contract": contracts.public(c)}


@router.get("/lessor/guide")
def lessor_guide(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    return {"success": True, "guide": b.guide, "default": leaseflow.DEFAULT_GUIDE, "address": b.address}


@router.post("/lessor/guide")
def lessor_guide_save(payload: Dict[str, Any] = Body(...), branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    b = _scope(user, branch)
    b.guide = str(payload.get("guide") or "")[:5000]
    if payload.get("address") is not None:
        b.address = str(payload.get("address") or "").strip()[:200]
    b.save()
    return {"success": True}


@router.post("/lessor/contracts/paper")
def lessor_contract_paper(payload: Dict[str, Any] = Body(...), branch: str = "",
                          user: User = Depends(staff)) -> Dict[str, Any]:
    c = contracts.register_paper(user, _scope(user, branch).branch, payload)
    return {"success": True, "contract": contracts.public(c)}


@router.post("/lessor/paste")
async def lessor_paste(payload: Dict[str, Any] = Body(...), branch: str = "",
                       user: User = Depends(staff)) -> Dict[str, Any]:
    """만능 붙여 넣기: 무엇인지 알아내 bank / excel / contract 로 나눈다 (저장하지 않음)."""
    _scope(user, branch)
    g = lambda k: str(payload.get(k) or "")  # noqa: E731
    return {"success": True, **await paste.analyze(g("text"), g("file"), g("filename"), g("mime"))}


@router.post("/lessor/contracts/{contract_id}/{action}")
def lessor_contract_action(contract_id: str, action: str, branch: str = "",
                           user: User = Depends(staff)) -> Dict[str, Any]:
    return _contract_action(contracts.require(contract_id, "lease", _scope(user, branch).branch), action)


# ── 임대인 ──
@router.get("/lessor/summary")
def lessor_summary(branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    return {"success": True, **ledger.month_summary(_scope(user, branch).branch)}


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


@router.post("/lessor/rooms/{room_no}/move-out")
def lessor_move_out(room_no: str, payload: Dict[str, Any] = Body(default={}), branch: str = "",
                    user: User = Depends(staff)) -> Dict[str, Any]:
    """전출 처리 → 공실 + 임차인 자격 끝 (회원은 일반 회원으로 돌아감)."""
    return {"success": True, **accounts.move_out(_scope(user, branch).branch, room_no, str(payload.get("date") or ""))}


@router.post("/lessor/accounts/{room_no}/reset")
def lessor_account_reset(room_no: str, branch: str = "", user: User = Depends(staff)) -> Dict[str, Any]:
    """임차인 비밀번호 초기화 → 한 번만 쓰는 재설정 링크 (임대인이 채팅으로 전달)."""
    acc, room, token = accounts.issue_reset_link(_scope(user, branch).branch, room_no)
    delivered = "share"
    if chat.online(acc.username):        # 임차인이 지금 접속 중이면 채팅창으로 바로 전달
        chat.post(acc.username, user.username, chat.label_of(user),
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


@router.get("/chat/contacts")
def chat_contacts(user: User = Depends(require_user)) -> Dict[str, Any]:
    chat.touch(user.username)
    return {"success": True, "contacts": chat.contacts(user), "categories": list(chat.CATEGORIES)}


@router.get("/chat/thread/{other}")
def chat_thread(other: str, user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, "messages": chat.thread(user, other)}


@router.post("/chat/send")
def chat_send(payload: Dict[str, Any] = Body(...), user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"success": True, "message": chat.send(user, str(payload.get("to", "")), str(payload.get("text", "")),
                                                   str(payload.get("category", "")))}


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


# ── 회원가입 (로그인 전, 구글 본인 확인 뒤) ──
@router.get("/signup/view")
def signup_view(s: str = "") -> Dict[str, Any]:
    return {"success": True, **members.view(s)}


@router.post("/signup")
def signup(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    g = lambda k: str(payload.get(k, ""))  # noqa: E731
    return {"success": True, **members.signup(g("s"), g("username"), g("name"), g("phone"), g("password"))}


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
    stats = accounts.sync_tenant_accounts()
    stats["members_purged"] = members.purge_inactive()      # 1년 동안 안 쓴 일반 회원 정리
    return stats
