"""
apps/rent_dist/rent3_admin/routers/support.py — 관리인 원격 지원 · 대리 접속 · 지원 채팅 · 전화 접수.
"""
from typing import Any, Dict
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Building,
    CoSignSession,
    Lessor,
    Room,
    Tenant,
    chatsessions as chat_sessions,
    consent as cs,
    ensure_pool_seeded,
    normalize_branch_id,
    support as sup,
)
from ._deps import ADMIN_ONLY, _admin_name

router = APIRouter()


class SupportMsgReq(BaseModel):
    user: str                      # 'lessor:<id>' | 'tenant:<id>'
    body: str


class PhoneIntakeReq(BaseModel):
    user: str
    category: str
    memo: str = ""


class SupportEnterReq(BaseModel):
    user: str
    view: str                      # lessor | tenant | contract
    session_id: str = ""
    room_no: str = ""
    category: str = ""


def _user_card(user: Any) -> Dict[str, Any]:
    key = sup.thread_key(user)
    msgs = sup.thread_messages(key)
    return {
        "id": user.id,
        "name": user.name,
        "user": key,
        "branch": user.branch,
        "room_no": sup._room_of(user),
        "role": sup.thread_role(user),
        "phone_tail": "".join(ch for ch in (user.phone or "") if ch.isdigit())[-4:],
        "support_allowed": cs.support_allowed(user),
        "has_thread": bool(msgs),
        "unread": sup.unread_count(key, "admin"),
        "last_at": msgs[-1].created_at if msgs else "",
    }


@router.get("/support/directory", dependencies=ADMIN_ONLY)
def support_directory(branch: str = ""):
    """원격 지원 카드: 건물 목록, 선택한 건물의 호실별 임대인·현 임차인·진행 중 계약방과 원격 지원 허용 여부."""
    ensure_pool_seeded()
    sup.purge_expired()
    buildings = Building.all()
    out: Dict[str, Any] = {
        "success": True,
        "branches": [{"branch": b.branch, "building_name": b.building_name} for b in buildings],
        "categories": list(sup.CATEGORIES),
    }
    if not branch:
        return out
    b_id = normalize_branch_id(branch)
    bldg = Building.first(branch=b_id)
    if not bldg:
        raise DomainError(f"'{branch}' 건물을 찾을 수 없습니다.")
    lessor = Lessor.get(bldg.id_lessor) if bldg.id_lessor else Lessor.first(branch=b_id)
    sessions = [x for x in CoSignSession.find(branch=b_id) if getattr(x, "status", "") == "IN_PROGRESS"]
    rooms = []
    for r in sorted(Room.find(branch=b_id), key=lambda x: x.room_no):
        tenant = Tenant.get(r.id_tenant) if r.id_tenant else None
        rooms.append({
            "room_no": r.room_no,
            "is_occupied": r.is_occupied,
            "tenant": _user_card(tenant) if tenant else None,
            "sessions": [
                {
                    "id": x.id,
                    "step": x.current_step,
                    "call_status": x.call_status,
                    "tenant_name": str((x.terms or {}).get("tenant_name", "")),
                }
                for x in sessions
                if x.room_no == r.room_no
            ],
        })
    out.update({"branch": b_id, "building_name": bldg.building_name, "lessor": _user_card(lessor) if lessor else None, "rooms": rooms})
    return out


@router.get("/support/thread", dependencies=ADMIN_ONLY)
def support_thread(user: str):
    sup.purge_expired()
    u = sup.user_by_thread(user)
    sup.mark_read(user, "admin")
    _bn = Building.first(branch=u.branch)
    _who = f"{(_bn.building_name if _bn else u.branch)} " + ("임대인" if sup.thread_role(u) == "lessor" else f"{sup._room_of(u)}호 임차인")
    chat_sessions.touch("admin", "support", user, _who.strip(), u.branch)       # 채팅 세션(제목·시작 시각만 기록)
    return {
        "success": True,
        "user": {**_user_card(u), "phone": (u.phone or "").strip()},      # 전화 접수 칸에서 바로 걸 수 있도록 선택한 사용자 한 명의 번호만 내려 준다
        "messages": [sup.public_message(m) for m in sup.thread_messages(user)],
        "retention_days": sup.SUPPORT_CHAT_DAYS,
    }


@router.post("/support/messages", dependencies=ADMIN_ONLY)
def support_post(req: SupportMsgReq, request: Request):
    u = sup.user_by_thread(req.user)
    m = sup.post_admin_message(u, _admin_name(request), req.body)
    return {"success": True, "message": "사용자에게 보냈습니다.", "item": sup.public_message(m)}


@router.post("/support/phone-intake", dependencies=ADMIN_ONLY)
def support_phone_intake(req: PhoneIntakeReq, request: Request):
    """전화로 요청받은 경우: 사유 분류와 접수 메모를 지원 채팅에 '[전화 요청 접수]'로 남긴다."""
    u = sup.user_by_thread(req.user)
    m = sup.phone_intake(u, _admin_name(request), req.category, req.memo)
    ok = cs.support_allowed(u)
    return {
        "success": True,
        "allowed": ok,
        "item": sup.public_message(m),
        "message": (
            "전화 요청 접수를 지원 채팅에 남겼습니다. 이제 입장할 수 있습니다."
            if ok
            else "전화 요청 접수를 지원 채팅에 남겼습니다. 입장하려면 사용자가 메뉴에서 원격 지원 허용을 켜야 합니다."
        ),
    }


@router.post("/support/enter", dependencies=ADMIN_ONLY)
def support_enter(req: SupportEnterReq, request: Request):
    u = sup.user_by_thread(req.user)
    res = sup.enter(_admin_name(request), u, req.view, session_id=req.session_id, category=req.category, room_no=req.room_no)
    return {"success": True, "message": "접속 기록을 남기고 보기 전용으로 입장합니다.", **res}
