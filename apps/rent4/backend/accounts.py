"""
apps/rent4/backend/accounts.py — 건물 목록 · 임차인 계정 · 로그인 후 역할 판정.

역할 (core.auth 로그인 하나로 통일)
    관리인  : superadmin / admin        → admin.html  (모든 건물)
    임대인  : branch_admin              → lessor.html (자기 지점 건물)
    임차인  : user + TenantAccount      → tenant.html (자기 호실)

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
from typing import Dict, List, Optional

from core.auth import User
from core.errors import DomainError, Forbidden, Mismatch, NotFound

from .models import Building, Room, TenantAccount
from .pool import now_iso

BUILDINGS = (            # code, name, core.auth branch
    ("chic", "시크빌", "main"),
    ("sunho", "선호아트빌", "sunho"),
    ("sinbi", "신비주택", "sinbi"),
)
ADMIN_ROLES = ("superadmin", "admin")
LESSOR_ROLES = ("branch_admin",)
TENANT_ROLE = "user"


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
                acc.tenant_key, acc.initial_code, acc.must_change = "", "", True
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
    rooms = {r.room: r for r in Room.find(branch=branch)}
    out = []
    for acc in sorted(TenantAccount.find(branch=branch), key=lambda a: a.room):
        room = rooms.get(acc.room)
        if not room or room.status != "occupied":
            continue
        out.append({"room": acc.room, "tenant_name": room.tenant_name, "username": acc.username,
                    "initial_code": acc.initial_code, "activated": not acc.must_change,
                    "activated_at": acc.activated_at})
    return out


def tenant_account(user: User) -> TenantAccount:
    acc = TenantAccount.first(username=user.username) if user.role == TENANT_ROLE else None
    if not acc or not acc.tenant_key:
        raise Forbidden("임차인 계정이 아닙니다.", hint="임대인에게 받은 임차인 아이디로 로그인해 주세요.")
    return acc


def change_password(user: User, current: str, new: str) -> TenantAccount:
    acc = tenant_account(user)
    if not user.check_password(current):
        raise Mismatch("현재 비밀번호가 맞지 않습니다.", hint="처음이라면 임대인에게 받은 6자리 코드를 입력하세요.")
    if len(new) < 6 or new.isdigit() and len(set(new)) == 1:
        raise Mismatch("새 비밀번호는 6자 이상, 같은 숫자 반복이 아니어야 합니다.")
    if new == current:
        raise Mismatch("새 비밀번호가 지금 비밀번호와 같습니다.")
    user.set_password(new).save("rent4_password_changed")
    acc.initial_code, acc.must_change, acc.activated_at = "", False, now_iso()
    return acc.save()


def whoami(user: User) -> Dict:
    """로그인한 사용자의 역할과 이동할 페이지."""
    base = {"username": user.username, "name": user.name}
    if user.role in ADMIN_ROLES:
        return {**base, "kind": "admin", "label": "관리인", "home": "admin.html"}
    if user.role in LESSOR_ROLES:
        b = building_of(user.branch)
        return {**base, "kind": "lessor", "label": "임대인", "home": "lessor.html",
                "building": {"code": b.code, "name": b.name, "branch": b.branch}}
    acc = tenant_account(user)
    b = building_of(acc.branch)
    return {**base, "kind": "tenant", "label": "임차인", "home": "tenant.html", "room": acc.room,
            "must_change_password": acc.must_change,
            "building": {"code": b.code, "name": b.name, "branch": b.branch}}
