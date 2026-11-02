"""
apps/rent4/backend/accounts.py — 건물 목록 · 계정 · 로그인 · 비밀번호 재설정 링크.

역할 (core.auth 토큰 하나로 통일)
    관리인  : superadmin / admin           → admin.html  (모든 건물)
    임대인  : branch_admin + LessorAccount → 임대인.html (자기 건물) — 가맹 계약 체결로만 생긴다
    임차인  : user + TenantAccount         → 임차인.html (자기 호실)
    계약 진행 중 : user + Contract.party_username → contract.html

로그인 = 아이디 또는 휴대폰 번호 + 비밀번호.
처음 비밀번호 = 계약 시작 링크(contracts) · 비밀번호 분실 = 구글 찾기(finder) 또는 임대인의 재설정 링크.
문자 발송은 쓰지 않는다 — 링크는 대면·채팅(카카오톡 등)으로 전달한다.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Dict, List, Optional, Tuple

from core.auth import Unauthorized, User, issue_token
from core.errors import DomainError, Forbidden, Mismatch, NotFound

from .models import OPEN_STATUSES, AdminAccount, Building, Contract, LessorAccount, Room, TenantAccount
from .pool import now_iso

BUILDINGS = (            # code, name, core.auth branch
    ("chic", "시크빌", "main"),
    ("sunho", "선호아트빌", "sunho"),
    ("sinbi", "신비주택", "sinbi"),
)
ADMIN_ROLES = ("superadmin", "admin")
LESSOR_ROLES = ("branch_admin",)
TENANT_ROLE = "user"
TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"   # 헷갈리는 0/O, 1/l/I 제외


def temp_password(length: int = 8) -> str:
    return "".join(secrets.choice(TEMP_ALPHABET) for _ in range(length))


# ── 관리인 설정 링크 (서버 콘솔에서 발급) ──
def issue_admin_link(username: str = "admin") -> Tuple[AdminAccount, str]:
    """관리인 계정에 임시 비밀번호를 걸고 설정 링크를 만든다. 이전 링크와 비밀번호는 즉시 막힌다."""
    user = User.by_username(username)
    if not user or user.role not in ADMIN_ROLES:
        raise NotFound(f"관리인 계정 '{username}' 이 없습니다.", hint="서버를 한 번 실행하면 기본 관리인 계정이 만들어집니다.")
    temp = temp_password(10)
    user.set_password(temp).save("rent4_admin_setup_link")
    token = secrets.token_urlsafe(24)
    acc = AdminAccount.first(username=username) or AdminAccount(username=username)
    acc.initial_code, acc.must_change, acc.link_hash, acc.activated_at = temp, True, _hash(token), None
    return acc.save(), token


def _admin_by_link(token: str) -> AdminAccount:
    acc = AdminAccount.first(link_hash=_hash(token or "")) if token else None
    if not acc:
        raise NotFound("설정 링크가 올바르지 않거나 만료되었습니다.", hint="비밀번호를 이미 정했다면 로그인 페이지에서 로그인하세요. 링크가 다시 필요하면 비밀번호 초기화를 요청하세요.")
    return acc


def admin_link_view(token: str) -> Dict:
    acc = _admin_by_link(token)
    return {"username": acc.username, "activated": not acc.must_change}


def link_session(username: str, home: str) -> Dict:
    """링크로 들어온 사용자에게 바로 로그인 세션을 준다 (비밀번호를 정하기 전까지만 쓰인다)."""
    user = User.by_username(username)
    if user is None:
        raise NotFound("계정을 찾을 수 없습니다.")
    return {"token": issue_token(user), "home": home, "activated": False}


def admin_link_start(token: str) -> Dict:
    acc = _admin_by_link(token)
    if not acc.must_change:
        return {"activated": True}
    return link_session(acc.username, "admin.html")


def require_ready(user: User) -> None:
    """임시 비밀번호로 들어온 관리인은 비밀번호를 바꾸기 전까지 관리 기능을 쓸 수 없다."""
    acc = AdminAccount.first(username=user.username) if user.role in ADMIN_ROLES else None
    if acc and acc.must_change:
        raise Forbidden("처음 로그인하셨습니다. 비밀번호를 먼저 바꿔 주세요.")


def ensure_buildings() -> None:
    existing = {b.branch for b in Building.all()}
    for code, name, branch in BUILDINGS:
        if branch not in existing:
            Building(code=code, name=name, branch=branch).save()


def building_of(branch: Optional[str]) -> Building:
    b = Building.first(branch=branch or "")
    if not b:
        raise NotFound("소속 건물을 찾을 수 없습니다.", hint="관리인에게 계정의 지점 설정을 확인해 달라고 요청하세요.")
    return b


def _tenant_key(room: Room) -> str:
    clean_phone = re.sub(r"\D", "", room.phone)
    raw = f"{room.tenant_name}|{clean_phone}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _set_core_password(username: str, branch: str, name: str, password: str) -> None:
    user = User.by_username(username)
    if user is None:
        user = User(username=username, role=TENANT_ROLE, branch=branch, name=name)
    elif user.role != TENANT_ROLE:
        raise DomainError(f"아이디 '{username}' 가 이미 다른 용도로 쓰이고 있습니다.")
    user.name, user.branch = name, branch
    user.set_password(password).save("rent4_tenant_account")


def sync_tenant_accounts() -> Dict[str, int]:
    """거주 중인 호실마다 임차인 계정을 맞춘다 (생성 / 임차인 변경 시 초기화 / 퇴거 시 잠금)."""
    codes = {b.branch: b.code for b in Building.all()}
    accounts = {(a.branch, a.room): a for a in TenantAccount.all()}
    stats = {"created": 0, "reset": 0, "locked": 0, "upgraded": 0}
    for room in Room.all():
        acc = accounts.get((room.branch, room.room))
        prefix = codes.get(room.branch, room.branch)
        if room.status != "occupied" or not room.tenant_name:
            if acc and acc.tenant_key:
                _release(acc, prefix)
                acc.tenant_key, acc.initial_code, acc.must_change, acc.invite_hash = "", "", True, ""
                acc.save()
                stats["locked"] += 1
            continue
        key = _tenant_key(room)
        if acc and acc.tenant_key == key:
            if acc.must_change and acc.initial_code.isdigit():      # 예전 6자리 숫자 코드 → 8자 임시 비밀번호
                acc.initial_code = temp_password()
                _set_core_password(acc.username, acc.branch, room.tenant_name, acc.initial_code)
                acc.save()
                stats["upgraded"] += 1
            continue
        if acc is None:
            acc = TenantAccount(username=f"{prefix}{room.room}", branch=room.branch, room=room.room)
            stats["created"] += 1
        else:
            _release(acc, prefix)                # 이전 임차인이 회원이면 회원으로 돌려보내고 호실 아이디로
            stats["reset"] += 1
        acc.tenant_key, acc.initial_code, acc.must_change, acc.activated_at = key, temp_password(), True, None
        acc.invite_hash = ""                     # 이전 임차인에게 보낸 안내 링크 무효
        _set_core_password(acc.username, acc.branch, room.tenant_name, acc.initial_code)
        acc.save()
    return stats


def _release(acc: TenantAccount, prefix: str) -> None:
    """임차인 자격 끝 (전출 · 임차인 변경). 회원이면 비밀번호는 그대로 두고 일반 회원으로 돌아가며
    호실 계정은 호실 아이디(예: chic102)로 되돌린다. 호실 아이디 계정은 잠근다 (무작위 비밀번호)."""
    from .members import MemberAccount
    if MemberAccount.first(username=acc.username):
        user = User.by_username(acc.username)
        if user is not None:
            user.branch = None
            user.save("rent4_member_moved_out")
        acc.username = f"{prefix}{acc.room}"
        if User.by_username(acc.username) is None:
            User(username=acc.username, role=TENANT_ROLE, branch=acc.branch).set_password(secrets.token_hex(16)).save("rent4_room_account")
        return
    _set_core_password(acc.username, acc.branch, "", secrets.token_hex(16))


def move_out(branch: str, room_no: str, date_text: str) -> Dict:
    """임대인의 전출 처리: 호실을 공실로, 그 달에 '전출' 표기, 임차인 자격 끝 (회원은 일반 회원으로)."""
    from datetime import date as _date
    from .models import MonthMark
    room = Room.first(branch=branch, room=room_no)
    if not room or room.status != "occupied":
        raise NotFound(f"{room_no}호에는 거주 중인 임차인이 없습니다.")
    try:
        day = _date.fromisoformat(date_text) if date_text else _date.today()
    except ValueError:
        raise Mismatch("전출일 형식이 올바르지 않습니다.", hint="YYYY-MM-DD")
    name = room.tenant_name
    ym = day.isoformat()[:7]
    if not MonthMark.first(branch=branch, room=room_no, ym=ym, kind="move_out"):
        MonthMark(branch=branch, room=room_no, ym=ym, kind="move_out", raw=f"전출 {day.isoformat()}").save()
    room.status, room.tenant_name, room.phone = "vacant", "", ""
    room.move_in, room.expire = None, None
    room.save()
    sync_tenant_accounts()
    return {"room": room_no, "tenant_name": name, "move_out": day.isoformat()}


def list_accounts(branch: str) -> List[Dict]:
    """임대인 화면용. 임시 비밀번호는 보여 주지 않는다 — 임차인은 안내 링크에서 본인 확인 후 받는다."""
    rooms = {r.room: r for r in Room.find(branch=branch)}
    out = []
    for acc in sorted(TenantAccount.find(branch=branch), key=lambda a: a.room):
        room = rooms.get(acc.room)
        if not room or room.status != "occupied":
            continue
        out.append({"room": acc.room, "tenant_name": room.tenant_name, "phone": room.phone, "username": acc.username,
                    "activated": not acc.must_change, "activated_at": acc.activated_at,
                    "link_sent": bool(acc.invite_hash)})
    return out


# ── 해시 ──


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()






def tenant_account(user: User) -> TenantAccount:
    acc = TenantAccount.first(username=user.username) if user.role == TENANT_ROLE else None
    if not acc or not acc.tenant_key:
        raise Forbidden("임차인 계정이 아닙니다.", hint="임대인에게 받은 임차인 아이디로 로그인해 주세요.")
    return acc


def lessor_account(user: User) -> LessorAccount:
    """가맹 계약으로 활성화된 임대인만 임대인 화면을 쓴다 (core 기본 지점 계정만으로는 안 됨)."""
    acc = LessorAccount.first(username=user.username) if user.role in LESSOR_ROLES else None
    if not acc or acc.branch != user.branch:
        raise Forbidden("가맹 계약이 체결된 임대인 계정이 아닙니다.",
                        hint="관리인이 보낸 가맹 계약 링크에서 계약을 확인하고, 가맹비 확인 후 받은 아이디로 로그인하세요.")
    return acc


def validate_new_password(new: str, min_len: int = 6) -> None:
    if len(new) < min_len or new.isdigit() and len(set(new)) == 1:
        raise Mismatch(f"비밀번호는 {min_len}자 이상, 같은 숫자 반복이 아니어야 합니다.")


def change_password(user: User, current: str, new: str) -> None:
    """처음 설정(링크로 로그인한 상태 — 현재 비밀번호 없이)이든 평소 변경이든 같은 규칙.
    관리인은 AdminAccount 기록이 없으면 core 비밀번호만 바꾼다."""
    if user.role in ADMIN_ROLES:
        acc, min_len = AdminAccount.first(username=user.username), 8
    else:
        acc = lessor_account(user) if user.role in LESSOR_ROLES else tenant_account(user)
        min_len = 6
    first_time = acc is not None and acc.must_change
    if not first_time and not user.check_password(current):
        raise Mismatch("현재 비밀번호가 맞지 않습니다.", hint="비밀번호를 잊었다면 링크를 다시 요청하세요.")
    validate_new_password(new, min_len)
    if not first_time and new == current:
        raise Mismatch("새 비밀번호가 지금 비밀번호와 같습니다.")
    user.set_password(new).save("rent4_password_changed")
    if acc is not None:
        acc.initial_code, acc.must_change = "", False
        acc.activated_at = acc.activated_at or now_iso()
        acc.save()
    if first_time:
        expire_links(user.username)      # acc 저장 뒤에 — 먼저 지우면 위 save 가 예전 링크 해시를 되살린다


def _phone_candidates(digits: str) -> List[str]:
    """이 휴대폰 번호로 로그인할 수 있는 아이디들 (최종 계정 우선, 그다음 계약 진행 중)."""
    same = lambda phone: re.sub(r"\D", "", phone or "") == digits  # noqa: E731
    out: List[str] = []
    for acc in TenantAccount.all():
        room = Room.first(branch=acc.branch, room=acc.room) if acc.tenant_key else None
        if room and same(room.phone):
            out.append(acc.username)
    for acc in LessorAccount.all():
        c = Contract.first(id=acc.contract_id) if acc.contract_id else None
        if c and same(c.party_phone):
            out.append(acc.username)
    for c in Contract.all():
        if c.party_username and c.status in ("sent", "signed") and same(c.party_phone):
            out.append(c.party_username)
    from .members import MemberAccount
    out += [m.username for m in MemberAccount.all() if same(m.phone) and m.username not in out]
    return out


def login(identifier: str, password: str) -> Dict:
    """아이디 또는 휴대폰 번호 + 비밀번호. 계약 진행 중인 사람은 휴대폰 번호로 이어서 들어온다."""
    ident = identifier.strip()
    digits = re.sub(r"\D", "", ident)
    names = _phone_candidates(digits) if len(digits) >= 10 and len(digits) == len(re.sub(r"[\s-]", "", ident)) else [ident]
    for name in names:
        user = User.by_username(name)
        if user and user.check_password(password):
            from .members import touch
            touch(user.username)
            return {"token": issue_token(user), "user": user.public()}
    raise Unauthorized("아이디(휴대폰 번호) 또는 비밀번호가 올바르지 않습니다.",
                       hint="계약 링크에서 정한 비밀번호를 입력하세요. 잊었다면 계약을 보낸 분에게 문의하세요.")




# ── 임대인이 만드는 비밀번호 재설정 링크 (대면 계약 마무리 · 비밀번호 분실) ──
RESET_LINK_TTL = 24 * 3600


def issue_reset_link(branch: str, room_no: str) -> Tuple[TenantAccount, Room, str]:
    """임차인 비밀번호 초기화: 지금 비밀번호는 바로 막고, 한 번만 쓰는 재설정 링크(24시간)를 만든다.

    임대인은 이 링크를 카카오톡 등 채팅으로 보내고, 임차인은 링크에서 새 비밀번호를 정하면 바로 로그인된다.
    """
    room = Room.first(branch=branch, room=room_no)
    acc = TenantAccount.first(branch=branch, room=room_no)
    if not room or room.status != "occupied" or not acc or not acc.tenant_key:
        raise NotFound(f"{room_no}호에는 임차인 계정이 없습니다.")
    _set_core_password(acc.username, branch, room.tenant_name, secrets.token_hex(16))   # 이전 비밀번호 즉시 무효
    token = secrets.token_urlsafe(24)
    acc.invite_hash, acc.invite_expires = _hash(token), time.time() + RESET_LINK_TTL
    acc.initial_code, acc.must_change = "", True
    acc.save()
    return acc, room, token


def _reset_target(token: str) -> Tuple[TenantAccount, Room]:
    acc = TenantAccount.first(invite_hash=_hash(token or "")) if token else None
    room = Room.first(branch=acc.branch, room=acc.room) if acc else None
    if (acc is None or room is None or room.status != "occupied" or not acc.tenant_key
            or acc.invite_expires < time.time()):
        raise NotFound("비밀번호 재설정 링크가 올바르지 않거나 만료되었습니다.",
                       hint="이미 비밀번호를 정했다면 로그인하세요. 다시 필요하면 임대인에게 비밀번호 초기화를 요청하세요.")
    return acc, room


def reset_link_view(token: str) -> Dict:
    acc, room = _reset_target(token)
    b = building_of(acc.branch).name
    return {"building": b, "room": acc.room, "tenant_name": room.tenant_name, "username": acc.username,
            "title": f"{b} {acc.room}호", "name": room.tenant_name, "login": f"아이디 {acc.username}"}


def reset_link_set(token: str, new_password: str) -> Dict:
    """링크에서 새 비밀번호를 정한다 (해시로만 저장). 링크는 이 순간 만료되고 바로 로그인된다."""
    acc, room = _reset_target(token)
    validate_new_password(new_password)
    user = User.by_username(acc.username)
    if user is None:
        raise NotFound("계정을 찾을 수 없습니다.")
    user.set_password(new_password).save("rent4_password_set_by_reset_link")
    acc.initial_code, acc.must_change, acc.invite_hash, acc.invite_expires = "", False, "", 0
    acc.activated_at = acc.activated_at or now_iso()
    acc.save()
    expire_links(acc.username)
    return {"token": issue_token(user), "home": "임차인.html"}


def expire_links(username: str) -> None:
    """비밀번호를 정하면 그 계정으로 들어오던 링크(계약·안내·관리인 설정)는 모두 더 이상 열리지 않는다."""
    for c in Contract.find(username=username):
        if c.token_hash:
            c.token_hash = ""
            c.save()
    for cls, field in ((TenantAccount, "invite_hash"), (AdminAccount, "link_hash")):
        acc = cls.first(username=username)
        if acc and getattr(acc, field):
            setattr(acc, field, "")
            acc.save()


def whoami(user: User) -> Dict:
    """로그인한 사용자의 역할과 이동할 페이지."""
    base = {"username": user.username, "name": user.name}
    if user.role in ADMIN_ROLES:
        acc = AdminAccount.first(username=user.username)
        return {**base, "kind": "admin", "label": "관리인", "home": "admin.html",
                "must_change_password": bool(acc and acc.must_change)}
    if user.role in LESSOR_ROLES:
        acc = lessor_account(user)
        b = building_of(acc.branch)
        return {**base, "kind": "lessor", "label": "임대인", "home": "임대인.html",
                "must_change_password": acc.must_change,
                "building": {"code": b.code, "name": b.name, "branch": b.branch}}
    # 계약 진행 중: 신청 · 동의 · 납부 대기, 또는 체결됐지만 아직 최종 아이디로 넘어가지 않은 진행용 로그인(k<id>)
    pending = next((c for c in Contract.find(party_username=user.username)
                    if c.status in OPEN_STATUSES or (c.status == "active" and c.username != user.username)),
                   None) if user.role == TENANT_ROLE else None
    if pending is not None:
        return {**base, "kind": "party", "label": "계약 진행", "home": "contract.html", "must_change_password": False}
    from .members import MemberAccount
    tenant = TenantAccount.first(username=user.username) if user.role == TENANT_ROLE else None
    if (tenant is None or not tenant.tenant_key) and MemberAccount.first(username=user.username):
        return {**base, "kind": "member", "label": "회원", "home": "index.html", "must_change_password": False}
    acc = tenant_account(user)
    b = building_of(acc.branch)
    return {**base, "kind": "tenant", "label": "임차인", "home": "임차인.html", "room": acc.room,
            "must_change_password": acc.must_change,
            "building": {"code": b.code, "name": b.name, "branch": b.branch}}
