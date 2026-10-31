"""
apps/rent4/backend/accounts.py — 건물 목록 · 임차인 계정 · 로그인 후 역할 판정.

역할 (core.auth 로그인 하나로 통일)
    관리인  : superadmin / admin        → admin.html  (모든 건물)
    임대인  : branch_admin + LessorAccount → lessor.html (자기 지점 건물) — 가맹 계약 체결로만 생긴다
    임차인  : user + TenantAccount      → tenant.html (자기 호실)
    임대인·임차인 모두 첫 로그인 때 임시 비밀번호를 바꿔야 화면을 쓸 수 있다.

임차인 계정
    - 아이디 = 건물 코드 + 호실 (예: chic603). 거주 중인 호실마다 자동 생성.
    - 첫 비밀번호는 무작위 6자리 코드 — 임대인 화면 '임차인 계정' 탭에서 확인해 임차인에게 전달.
    - 첫 로그인 때 새 비밀번호로 바꿔야 수납 내역을 볼 수 있다. 바꾸면 코드는 지워진다.
    - 호실의 임차인(이름·전화)이 바뀌면 계정을 초기화해 이전 임차인이 더는 들어오지 못하게 한다.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from core.auth import User
from core.errors import Conflict, DomainError, Forbidden, Mismatch, NotFound

from .models import AdminAccount, Building, LessorAccount, Room, TenantAccount
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


def admin_link_view(token: str) -> Dict:
    acc = AdminAccount.first(link_hash=_hash(token or "")) if token else None
    if not acc:
        raise NotFound("설정 링크가 올바르지 않거나 만료되었습니다.", hint="서버 관리자에게 링크를 다시 만들어 달라고 요청하세요.")
    return {"username": acc.username, "temp_password": acc.initial_code if acc.must_change else "",
            "activated": not acc.must_change}


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


def _new_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def _tenant_key(room: Room) -> str:
    raw = f"{room.tenant_name}|{re.sub(r'\D', '', room.phone)}"
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
    stats = {"created": 0, "reset": 0, "locked": 0}
    for room in Room.all():
        acc = accounts.get((room.branch, room.room))
        if room.status != "occupied" or not room.tenant_name:
            if acc and acc.tenant_key:
                _set_core_password(acc.username, acc.branch, "", secrets.token_hex(16))
                acc.tenant_key, acc.initial_code, acc.must_change, acc.invite_hash = "", "", True, ""
                acc.save()
                stats["locked"] += 1
            continue
        key = _tenant_key(room)
        if acc and acc.tenant_key == key:
            continue
        if acc is None:
            prefix = codes.get(room.branch, room.branch)
            acc = TenantAccount(username=f"{prefix}{room.room}", branch=room.branch, room=room.room)
            stats["created"] += 1
        else:
            stats["reset"] += 1
        acc.tenant_key, acc.initial_code, acc.must_change, acc.activated_at = key, _new_code(), True, None
        acc.invite_hash = ""                     # 이전 임차인에게 보낸 안내 링크 무효
        _set_core_password(acc.username, acc.branch, room.tenant_name, acc.initial_code)
        acc.save()
    return stats


def reset_account(branch: str, room_no: str) -> TenantAccount:
    acc = TenantAccount.first(branch=branch, room=room_no)
    room = Room.first(branch=branch, room=room_no)
    if not acc or not room or room.status != "occupied":
        raise NotFound(f"{room_no}호에는 임차인 계정이 없습니다.")
    acc.initial_code, acc.must_change, acc.activated_at = _new_code(), True, None
    _set_core_password(acc.username, branch, room.tenant_name, acc.initial_code)
    return acc.save()


def list_accounts(branch: str) -> List[Dict]:
    """임대인 화면용. 첫 비밀번호 코드는 보여 주지 않는다 — 임차인은 안내 링크에서 본인 확인 후 받는다."""
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


# ── 임차인 안내 링크 (계약 없이 등록된 기존 임차인용) ──
_invite_failures: Dict[str, List[float]] = defaultdict(list)
MAX_INVITE_FAILURES, INVITE_WINDOW = 5, 600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_invite(branch: str, room_no: str, reissue: bool = False) -> Tuple[TenantAccount, str]:
    """안내 링크 발급. reissue=True 면 첫 비밀번호 코드도 새로 만든다 (분실 · 다시 보내기)."""
    acc = reset_account(branch, room_no) if reissue else TenantAccount.first(branch=branch, room=room_no)
    room = Room.first(branch=branch, room=room_no)
    if not acc or not room or room.status != "occupied":
        raise NotFound(f"{room_no}호에는 임차인 계정이 없습니다.")
    if not acc.must_change:
        raise Conflict("이미 임차인이 쓰고 있는 계정입니다.", hint="비밀번호를 잊었다면 '코드 재발급'을 누르세요.")
    token = secrets.token_urlsafe(24)
    acc.invite_hash = _hash(token)
    return acc.save(), token


def _invite(token: str) -> Tuple[TenantAccount, Room]:
    acc = TenantAccount.first(invite_hash=_hash(token or "")) if token else None
    room = Room.first(branch=acc.branch, room=acc.room) if acc else None
    if not acc or not room or room.status != "occupied" or not acc.tenant_key:
        raise NotFound("안내 링크가 올바르지 않거나 만료되었습니다.", hint="임대인에게 링크를 다시 요청하세요.")
    return acc, room


def invite_view(token: str) -> Dict:
    acc, room = _invite(token)
    return {"building": building_of(acc.branch).name, "room": acc.room, "tenant_name": room.tenant_name,
            "phone_masked": f"***-****-{re.sub(r'\D', '', room.phone)[-4:]}", "activated": not acc.must_change}


def invite_open(token: str, phone4: str) -> Dict:
    """휴대폰 뒤 4자리로 본인 확인 → 아이디와 첫 비밀번호 코드 (첫 로그인 전까지만)."""
    acc, room = _invite(token)
    now = time.time()
    recent = [t for t in _invite_failures[acc.id] if now - t < INVITE_WINDOW]
    _invite_failures[acc.id] = recent
    if len(recent) >= MAX_INVITE_FAILURES:
        raise Conflict("본인 확인 실패가 반복되어 잠시 막았습니다.", hint="10분 뒤 다시 시도해 주세요.")
    if re.sub(r"\D", "", room.phone)[-4:] != str(phone4).strip():
        _invite_failures[acc.id].append(now)
        raise Mismatch("휴대폰 번호 뒤 4자리가 등록된 번호와 다릅니다.", hint="임대인에게 등록한 휴대폰 번호를 확인하세요.")
    _invite_failures.pop(acc.id, None)
    return {"username": acc.username, "temp_password": "" if not acc.must_change else acc.initial_code,
            "activated": not acc.must_change}


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


def change_password(user: User, current: str, new: str) -> None:
    """첫 로그인(임시 비밀번호)이든 평소든 같은 규칙으로 바꾼다. 관리인은 계정 기록 없이 core 비밀번호만 바꾼다."""
    if user.role in ADMIN_ROLES:
        acc, min_len = AdminAccount.first(username=user.username), 8
    else:
        acc = lessor_account(user) if user.role in LESSOR_ROLES else tenant_account(user)
        min_len = 6
    if not user.check_password(current):
        raise Mismatch("현재 비밀번호가 맞지 않습니다.", hint="처음이라면 계약 링크에서 받은 임시 비밀번호를 입력하세요.")
    if len(new) < min_len or new.isdigit() and len(set(new)) == 1:
        raise Mismatch(f"새 비밀번호는 {min_len}자 이상, 같은 숫자 반복이 아니어야 합니다.")
    if new == current:
        raise Mismatch("새 비밀번호가 지금 비밀번호와 같습니다.")
    user.set_password(new).save("rent4_password_changed")
    if acc is not None:
        acc.initial_code, acc.must_change = "", False
        acc.activated_at = acc.activated_at or now_iso()
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
        return {**base, "kind": "lessor", "label": "임대인", "home": "lessor.html",
                "must_change_password": acc.must_change,
                "building": {"code": b.code, "name": b.name, "branch": b.branch}}
    acc = tenant_account(user)
    b = building_of(acc.branch)
    return {**base, "kind": "tenant", "label": "임차인", "home": "tenant.html", "room": acc.room,
            "must_change_password": acc.must_change,
            "building": {"code": b.code, "name": b.name, "branch": b.branch}}
