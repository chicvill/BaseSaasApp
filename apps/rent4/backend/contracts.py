"""
apps/rent4/backend/contracts.py — 네트워크 상호 확인 계약 → 계정 발급.

    1. 발행 (발행자 확인)     관리인이 가맹 계약, 임대인이 임대차 계약을 작성 → 계약 링크 발급
    2. 서명 (상대방 확인)     상대방이 링크를 열어 계약 내용을 보고, 등록 휴대폰 뒤 4자리로 본인 확인 후 동의
    3. 납부 확인 → 활성화     발행자가 가맹비 / 잔금 입금을 확인 → 계정 생성 + 임시 비밀번호
    4. 첫 로그인             상대방은 같은 링크에서 아이디·임시 비밀번호를 확인하고 로그인 → 비밀번호 변경 필수

링크 토큰은 원문을 저장하지 않고 해시만 저장한다 (분실하면 발행자가 링크를 다시 만든다).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from core.auth import User
from core.errors import Conflict, Mismatch, NotFound

from . import accounts
from .models import Building, Contract, LessorAccount, Room
from .pool import now_iso

CODE_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
KIND_LABEL = {"membership": "가맹 계약", "lease": "임대차 계약"}
STATUS_LABEL = {"sent": "상대방 확인 대기", "signed": "납부 확인 대기", "active": "체결 완료", "cancelled": "취소"}
MAX_SIGN_FAILURES, WINDOW_SECONDS = 5, 600
_sign_failures: Dict[str, List[float]] = defaultdict(list)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token(c: Contract) -> str:
    token = secrets.token_urlsafe(24)
    c.token_hash = _hash(token)
    return token


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _date(text: Optional[str], label: str) -> Optional[str]:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)).isoformat()
    except ValueError:
        raise Mismatch(f"{label} 형식이 올바르지 않습니다.", hint="YYYY-MM-DD 로 입력해 주세요.")


def _party(data: Dict) -> Tuple[str, str]:
    name, phone = str(data.get("party_name", "")).strip(), str(data.get("party_phone", "")).strip()
    if not name:
        raise Mismatch("계약 상대방 이름을 입력해 주세요.")
    if len(_digits(phone)) < 10:
        raise Mismatch("계약 상대방 휴대폰 번호를 정확히 입력해 주세요.", hint="본인 확인에 이 번호의 뒤 4자리를 씁니다.")
    return name, phone


def _money(data: Dict, key: str) -> int:
    try:
        value = int(str(data.get(key) or 0).replace(",", ""))
    except ValueError:
        raise Mismatch(f"{key} 금액이 숫자가 아닙니다.")
    if value < 0:
        raise Mismatch("금액은 0 이상이어야 합니다.")
    return value


# ── 1. 발행 ──
def create_membership(issuer: User, data: Dict) -> Tuple[Contract, str]:
    name = str(data.get("building_name", "")).strip()
    code = str(data.get("building_code", "")).strip().lower()
    if not name:
        raise Mismatch("건물 이름을 입력해 주세요.")
    if not CODE_RE.match(code):
        raise Mismatch("건물 코드는 영문 소문자로 시작하는 영문·숫자 2~16자입니다.", hint="임대인 아이디와 임차인 아이디(코드+호실)에 쓰입니다.")
    existing = Building.first(code=code)
    branch = existing.branch if existing else code
    if existing and existing.name != name:
        raise Conflict(f"건물 코드 '{code}' 는 이미 '{existing.name}' 이 쓰고 있습니다.")
    if any(c.kind == "membership" and c.branch == branch and c.status in ("sent", "signed", "active") for c in Contract.all()):
        raise Conflict(f"'{name}' 에는 이미 진행 중이거나 체결된 가맹 계약이 있습니다.")
    other = User.by_username(code)
    if other and not (other.role == "branch_admin" and other.branch == branch):
        raise Conflict(f"아이디 '{code}' 가 이미 다른 계정으로 쓰이고 있습니다.", hint="다른 건물 코드를 정해 주세요.")
    party_name, party_phone = _party(data)
    c = Contract(kind="membership", branch=branch, building_name=name, building_code=code,
                 party_name=party_name, party_phone=party_phone, fee=_money(data, "fee"),
                 start=_date(data.get("start"), "계약 시작일"), end=_date(data.get("end"), "계약 종료일"),
                 issued_by=issuer.username)
    token = _new_token(c)
    return c.save(), token


def create_lease(issuer: User, branch: str, data: Dict) -> Tuple[Contract, str]:
    room_no = str(data.get("room", "")).strip().removesuffix("호")
    room = Room.first(branch=branch, room=room_no)
    if not room or room.status == "owner":
        raise NotFound(f"{room_no}호를 찾을 수 없습니다.", hint="Excel 수납부로 호실을 먼저 등록해 주세요.")
    if any(c.kind == "lease" and c.branch == branch and c.room == room_no and c.status in ("sent", "signed")
           for c in Contract.all()):
        raise Conflict(f"{room_no}호에는 이미 진행 중인 계약이 있습니다.", hint="기존 계약을 취소한 뒤 다시 작성하세요.")
    party_name, party_phone = _party(data)
    start = _date(data.get("start"), "입주일")
    if not start:
        raise Mismatch("입주일(계약 시작일)을 입력해 주세요.")
    c = Contract(kind="lease", branch=branch, building_name=accounts.building_of(branch).name, room=room_no,
                 party_name=party_name, party_phone=party_phone,
                 deposit=_money(data, "deposit"), rent=_money(data, "rent"), mgmt=_money(data, "mgmt"),
                 start=start, end=_date(data.get("end"), "만기일"),
                 lease_type=str(data.get("lease_type") or "월세"), pay_timing=str(data.get("pay_timing") or "후불"),
                 issued_by=issuer.username)
    token = _new_token(c)
    return c.save(), token



def cancel(c: Contract) -> Contract:
    if c.status == "active":
        raise Conflict("이미 체결된 계약은 취소할 수 없습니다.")
    c.status, c.token_hash = "cancelled", ""
    return c.save()


# ── 2. 상대방 확인 (링크) ──
def by_token(token: str) -> Contract:
    c = Contract.first(token_hash=_hash(token or "")) if token else None
    if not c:
        raise NotFound("계약 링크가 올바르지 않거나 만료되었습니다.", hint="비밀번호를 이미 정했다면 로그인 페이지에서 로그인하세요. 링크가 다시 필요하면 비밀번호 초기화를 요청하세요.")
    return c


def join(token: str, phone4: str, new_password: str) -> Dict:
    """계약 링크 첫 단계: 휴대폰 뒤 4자리 확인 + 비밀번호 설정 → 바로 로그인.

    링크는 이 순간 만료된다. 링크를 잃어버려도 로그인 페이지에서 휴대폰 번호와 이 비밀번호로 이어서 진행하고,
    체결되면 같은 비밀번호가 최종 아이디(건물 코드 / 건물 코드+호실)로 넘어간다.
    """
    c = by_token(token)
    if c.status != "sent":
        raise Conflict("이미 진행 중이거나 끝난 계약입니다." if c.status != "cancelled" else "취소된 계약입니다.",
                       hint="로그인 페이지에서 휴대폰 번호와 비밀번호로 들어오세요.")
    if c.party_username:
        raise Conflict("이미 비밀번호를 정했습니다.", hint="로그인 페이지에서 휴대폰 번호와 비밀번호로 이어서 진행하세요.")
    _check_phone(c, phone4)
    accounts.validate_new_password(new_password)
    c.party_username = f"k{c.id}"
    User(username=c.party_username, role=accounts.TENANT_ROLE, name=c.party_name).set_password(new_password).save("rent4_contract_party")
    c.token_hash = ""
    c.save()
    return {**accounts.link_session(c.party_username, "contract.html"), "contract": public(c, for_party=True)}


def _check_phone(c: Contract, phone4: str) -> None:
    now = time.time()
    recent = [t for t in _sign_failures[c.id] if now - t < WINDOW_SECONDS]
    _sign_failures[c.id] = recent
    if len(recent) >= MAX_SIGN_FAILURES:
        raise Conflict("본인 확인 실패가 반복되어 잠시 막았습니다.", hint="10분 뒤 다시 시도해 주세요.")
    if _digits(c.party_phone)[-4:] != str(phone4).strip():
        _sign_failures[c.id].append(now)
        raise Mismatch("휴대폰 번호 뒤 4자리가 계약서와 다릅니다.", hint="계약서에 등록한 휴대폰 번호를 확인하세요.")
    _sign_failures.pop(c.id, None)


# ── 로그인한 계약 상대방 (비밀번호를 정한 뒤) ──
def party_contract(user: User) -> Contract:
    c = Contract.first(party_username=user.username)
    if c is None:
        raise NotFound("진행 중인 계약이 없습니다.")
    return c


def sign(user: User, agree: bool) -> Contract:
    """2단계: 계약 내용 동의 (로그인한 상태)."""
    c = party_contract(user)
    if c.status != "sent":
        raise Conflict("이미 동의한 계약입니다." if c.status != "cancelled" else "취소된 계약입니다.")
    if not agree:
        raise Mismatch("계약 내용에 동의해야 확인이 완료됩니다.")
    c.status, c.signed_at = "signed", now_iso()
    return c.save()


def continue_as_account(user: User) -> Dict:
    """체결 뒤: 계약 진행용 로그인을 최종 계정 로그인으로 바꾼다 (비밀번호는 이미 넘어가 있다)."""
    c = party_contract(user)
    if c.status != "active":
        raise Conflict("아직 체결되지 않았습니다.", hint="납부가 확인되면 다시 눌러 주세요.")
    return accounts.link_session(c.username, "임대인.html" if c.kind == "membership" else "임차인.html")


# ── 3. 납부 확인 → 활성화 ──
def confirm_paid(c: Contract) -> Contract:
    if c.status != "signed":
        raise Conflict("상대방이 계약을 확인한 뒤에 납부를 확인할 수 있습니다." if c.status == "sent"
                       else f"이 계약은 '{STATUS_LABEL.get(c.status, c.status)}' 상태입니다.")
    c.paid_at = now_iso()
    (_activate_membership if c.kind == "membership" else _activate_lease)(c)
    _carry_password(c)
    c.status, c.activated_at = "active", now_iso()
    return c.save()


def _carry_password(c: Contract) -> None:
    """계약 링크에서 정한 비밀번호를 최종 아이디로 옮기고, 계약 진행용 로그인은 막는다."""
    party = User.by_username(c.party_username) if c.party_username else None
    final = User.by_username(c.username)
    if party is None or final is None:
        return
    final.salt, final.password_hash = party.salt, party.password_hash
    final.save("rent4_password_carried")
    acc = _account(c)
    if acc is not None:
        acc.initial_code, acc.must_change = "", False
        acc.activated_at = acc.activated_at or now_iso()
        acc.save()
    party.set_password(secrets.token_hex(16)).save("rent4_contract_party_closed")


def _activate_membership(c: Contract) -> None:
    if not Building.first(code=c.building_code):
        Building(code=c.building_code, name=c.building_name, branch=c.branch).save()
    temp = accounts.temp_password()
    user = User.by_username(c.building_code) or User(username=c.building_code, role="branch_admin", branch=c.branch)
    user.name, user.role, user.branch = c.party_name, "branch_admin", c.branch
    user.set_password(temp).save("rent4_membership_activated")
    acc = LessorAccount.first(username=c.building_code) or LessorAccount(username=c.building_code, branch=c.branch)
    acc.branch, acc.contract_id, acc.initial_code, acc.must_change, acc.activated_at = c.branch, c.id, temp, True, None
    acc.save()
    c.username = acc.username


def reset_password(c: Contract) -> str:
    """비밀번호 초기화 (요청 · 분실 · 계약 정리 · 처음 설정 등 모든 경우): 체결된 계약의 계정을 '설정 전'으로 되돌리고
    새 계약 링크를 만든다. 이전 링크와 비밀번호는 바로 막히고, 상대방은 새 링크로 바로 로그인해 비밀번호를 정한다.
    정하는 즉시 그 링크도 만료된다.
    """
    if c.status != "active":
        raise Conflict("체결된 계약에서만 비밀번호를 초기화할 수 있습니다.")
    if c.kind == "membership":
        acc = LessorAccount.first(username=c.username)
        user = User.by_username(c.username)
        if not acc or not user or acc.contract_id != c.id:
            raise Conflict("이 가맹 계약의 임대인 계정을 찾을 수 없습니다.")
        temp = accounts.temp_password()
        user.set_password(temp).save("rent4_lessor_password_reset")
        acc.initial_code, acc.must_change = temp, True
        acc.save()
    else:
        acc = accounts.TenantAccount.first(branch=c.branch, room=c.room)
        room = Room.first(branch=c.branch, room=c.room)
        if not acc or acc.username != c.username or not room or room.tenant_name != c.party_name:
            raise Conflict("이 계약의 임차인이 더 이상 이 호실에 거주하지 않습니다.")
        accounts.reset_account(c.branch, c.room)
    c.reset_at = now_iso()
    token = _new_token(c)
    c.save()
    return token


def _activate_lease(c: Contract) -> None:
    room = Room.first(branch=c.branch, room=c.room)
    if not room:
        raise NotFound(f"{c.room}호를 찾을 수 없습니다.")
    room.status, room.tenant_name, room.phone = "occupied", c.party_name, c.party_phone
    room.move_in, room.expire, room.deposit = c.start, c.end, c.deposit
    room.rent, room.mgmt, room.lease_type, room.pay_timing = c.rent, c.mgmt, c.lease_type, c.pay_timing
    room.save()
    accounts.sync_tenant_accounts()
    acc = accounts.TenantAccount.first(branch=c.branch, room=c.room)
    c.username = acc.username if acc else ""


# ── 화면용 표현 ──
def public(c: Contract, for_party: bool = False) -> Dict:
    data = {"id": c.id, "kind": c.kind, "kind_label": KIND_LABEL[c.kind], "status": c.status,
            "status_label": STATUS_LABEL.get(c.status, c.status), "building_name": c.building_name,
            "building_code": c.building_code, "room": c.room, "party_name": c.party_name,
            "deposit": c.deposit, "rent": c.rent, "mgmt": c.mgmt, "fee": c.fee, "start": c.start, "end": c.end,
            "lease_type": c.lease_type, "pay_timing": c.pay_timing, "created_at": c.created_at,
            "signed_at": c.signed_at, "paid_at": c.paid_at, "activated_at": c.activated_at, "username": c.username,
            "reset_at": c.reset_at}
    if not for_party:
        data["party_phone"] = c.party_phone
    else:
        data["party_phone_masked"] = f"***-****-{_digits(c.party_phone)[-4:]}"
        data["final_username"] = c.username or (c.building_code if c.kind == "membership"
                                                else f"{accounts.building_of(c.branch).code}{c.room}")
        data["password_set"] = bool(c.party_username)
        data["login"] = _login_info(c) if c.status == "active" else None
    return data


def _account(c: Contract):
    if c.kind == "membership":
        return LessorAccount.first(username=c.username)
    acc = accounts.TenantAccount.first(branch=c.branch, room=c.room)
    return acc if acc and acc.username == c.username else None


def _login_info(c: Contract) -> Optional[Dict]:
    """체결된 계약 링크의 상태: 아직 비밀번호를 정하지 않았으면 '시작하기'로 바로 로그인할 수 있다."""
    acc = _account(c)
    if acc is None:
        return None
    return {"username": c.username, "activated": not acc.must_change, "can_start": acc.must_change}


def start_session(token: str) -> Dict:
    """체결된 계약 링크 → 바로 로그인 (임시 비밀번호는 보여 주지 않는다). 비밀번호를 정한 뒤에는 쓸 수 없다."""
    c = by_token(token)
    if c.status != "active":
        raise Conflict("계약이 아직 체결되지 않았습니다.", hint="납부 확인이 끝나면 다시 열어 주세요.")
    acc = _account(c)
    if acc is None:
        raise Conflict("이 계약의 계정을 찾을 수 없습니다.", hint="계약을 보낸 분에게 문의해 주세요.")
    if not acc.must_change:
        return {"activated": True}
    return accounts.link_session(c.username, "임대인.html" if c.kind == "membership" else "임차인.html")


def listing(kind: str, branch: Optional[str] = None) -> List[Dict]:
    items = [c for c in Contract.find(kind=kind) if branch is None or c.branch == branch]
    return [public(c) for c in sorted(items, key=lambda c: c.created_at, reverse=True)]


def require(contract_id: str, kind: str, branch: Optional[str] = None) -> Contract:
    c = Contract.first(id=contract_id, kind=kind)
    if not c or (branch is not None and c.branch != branch):
        raise NotFound("계약을 찾을 수 없습니다.")
    return c

