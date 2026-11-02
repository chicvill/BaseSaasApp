"""
apps/rent4/backend/listings.py — 매물 공개 (누구나 보는 index.html 매물 목록).

임대인이 호실에 [매물 공개] → 조건(보증금 · 월세 · 관리비 · 입주 가능일 · 설명)을 정한다.
공개 목록에는 건물 · 호실 · 조건만 나간다. 이전 · 현재 임차인 정보는 내보내지 않는다.
계약이 체결되거나 임대인이 내리면 닫힌다 (closed_at).
"""
from __future__ import annotations

from datetime import date
from typing import ClassVar, Dict, List, Optional

from core.errors import Conflict, Mismatch, NotFound

from .models import Building, Room
from .pool import PoolObj, now_iso

MAX_DESC = 1000


class Listing(PoolObj):
    collection: ClassVar[str] = "listings"

    branch: str
    room: str
    deposit: int = 0
    rent: int = 0
    mgmt: int = 0
    lease_type: str = "월세"
    pay_timing: str = "후불"
    available_from: Optional[str] = None
    description: str = ""
    published_by: str = ""
    closed_at: Optional[str] = None
    closed_reason: str = ""        # contracted | withdrawn


def _money(data: Dict, key: str) -> int:
    try:
        v = int(str(data.get(key) or 0).replace(",", ""))
    except ValueError:
        raise Mismatch(f"{key} 금액이 숫자가 아닙니다.")
    if v < 0:
        raise Mismatch("금액은 0 이상이어야 합니다.")
    return v


def open_listing(branch: str, room_no: str) -> Optional[Listing]:
    return next((l for l in Listing.find(branch=branch, room=room_no) if not l.closed_at), None)


def publish(branch: str, user: str, data: Dict) -> Listing:
    room_no = str(data.get("room", "")).strip().removesuffix("호")
    room = Room.first(branch=branch, room=room_no)
    if not room or room.status == "owner":
        raise NotFound(f"{room_no}호를 찾을 수 없습니다.")
    if open_listing(branch, room_no):
        raise Conflict(f"{room_no}호는 이미 공개 중입니다.", hint="내린 뒤 다시 공개하세요.")
    avail = str(data.get("available_from") or "") or None
    if avail:
        try:
            avail = date.fromisoformat(avail).isoformat()
        except ValueError:
            raise Mismatch("입주 가능일 형식이 올바르지 않습니다.", hint="YYYY-MM-DD")
    desc = str(data.get("description") or "").strip()
    if len(desc) > MAX_DESC:
        raise Mismatch(f"설명은 {MAX_DESC}자까지 쓸 수 있습니다.")
    return Listing(branch=branch, room=room_no, deposit=_money(data, "deposit"), rent=_money(data, "rent"),
                   mgmt=_money(data, "mgmt"), lease_type=str(data.get("lease_type") or "월세"),
                   pay_timing=str(data.get("pay_timing") or "후불"), available_from=avail,
                   description=desc, published_by=user).save()


def close(branch: str, listing_id: str, reason: str = "withdrawn") -> Listing:
    l = Listing.first(id=listing_id, branch=branch)
    if not l:
        raise NotFound("매물을 찾을 수 없습니다.")
    if not l.closed_at:
        l.closed_at, l.closed_reason = now_iso(), reason
        l.save()
    return l


def public(l: Listing) -> Dict:
    b = Building.first(branch=l.branch)
    return {"id": l.id, "building": b.name if b else l.branch, "building_code": b.code if b else "",
            "room": l.room, "deposit": l.deposit, "rent": l.rent, "mgmt": l.mgmt, "monthly": l.rent + l.mgmt,
            "lease_type": l.lease_type, "pay_timing": l.pay_timing, "available_from": l.available_from,
            "description": l.description, "published_at": l.created_at}


def public_list() -> List[Dict]:
    items = [l for l in Listing.all() if not l.closed_at]
    return [public(l) for l in sorted(items, key=lambda l: (l.branch, l.room))]


def lessor_list(branch: str) -> List[Dict]:
    items = sorted(Listing.find(branch=branch), key=lambda l: l.created_at, reverse=True)
    return [{**public(l), "closed_at": l.closed_at, "closed_reason": l.closed_reason} for l in items]


def get_open(listing_id: str) -> Listing:
    l = Listing.first(id=listing_id)
    if not l or l.closed_at:
        raise NotFound("마감되었거나 없는 매물입니다.")
    return l
