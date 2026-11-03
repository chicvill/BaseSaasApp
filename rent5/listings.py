"""
rent5/listings.py — 매물 올리기 · 공개 목록.

누구나(로그인 없이) 보는 목록에는 주소 · 조건 · 설명 · 임대인이 직접 적은 문의 번호만 나간다.
회원 이름 · 아이디 · 가입 번호는 나가지 않는다.
상태: open(예약 받는 중) → reserved(예약한 한 명과 진행 중, 다른 사람은 고를 수 없다) → closed(계약 종료 · 내림)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from .security import ApiError, clean_text, valid_phone
from .store import Store

MAX_OPEN_LISTINGS = 20


def money(data: Dict[str, Any], key: str, label: str) -> int:
    raw = str(data.get(key) if data.get(key) not in (None, "") else 0).replace(",", "").strip()
    if not raw.isdigit():
        raise ApiError(422, f"{label}은(는) 0 이상의 숫자여야 합니다.")
    value = int(raw)
    if value > 100_000_000_000:
        raise ApiError(422, f"{label} 금액이 너무 큽니다.")
    return value


def iso_date(value: Any, label: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ApiError(422, f"{label}을(를) 입력해 주세요.")
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ApiError(422, f"{label} 형식이 올바르지 않습니다.", hint="YYYY-MM-DD")


def create(store: Store, owner: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock:
        mine = [l for l in store.find("listings", owner=owner) if l["status"] != "closed"]
        if len(mine) >= MAX_OPEN_LISTINGS:
            raise ApiError(409, f"올려 둘 수 있는 매물은 {MAX_OPEN_LISTINGS}개까지입니다.", hint="내려도 되는 매물은 먼저 내려 주세요.")
        lease_type = str(data.get("lease_type") or "월세")
        if lease_type not in ("월세", "전세"):
            raise ApiError(422, "구분은 월세 또는 전세입니다.")
        rent = 0 if lease_type == "전세" else money(data, "rent", "월세")
        return store.insert("listings", {
            "owner": owner,
            "address": clean_text(data.get("address"), "주소", 100, required=True),
            "room": clean_text(data.get("room"), "호실", 20),
            "lease_type": lease_type,
            "deposit": money(data, "deposit", "보증금"),
            "rent": rent,
            "mgmt": money(data, "mgmt", "관리비"),
            "available_from": iso_date(data.get("available_from"), "입주 가능일"),
            "description": clean_text(data.get("description"), "설명", 1000),
            "contact_phone": valid_phone(data.get("contact_phone"), "문의 번호", required=False),
            "status": "open",
            "reserved_by": "",
        })


def public(l: Dict[str, Any]) -> Dict[str, Any]:
    return {k: l[k] for k in ("id", "address", "room", "lease_type", "deposit", "rent", "mgmt", "available_from",
                              "description", "contact_phone", "created_at")} | {"monthly": l["rent"] + l["mgmt"]}


def public_list(store: Store) -> List[Dict[str, Any]]:
    """열린 매물 + 예약된 매물(예약됨 표시만, 문의 번호는 가린다)."""
    items = [l for l in store.all("listings") if l["status"] in ("open", "reserved")]
    return [(public(l) | {"reserved": False}) if l["status"] == "open" else (public(l) | {"reserved": True, "contact_phone": ""})
            for l in sorted(items, key=lambda l: l["created_at"], reverse=True)]


def mine(store: Store, owner: str) -> List[Dict[str, Any]]:
    """내 매물 + 매물마다 진행 중인 신청 수."""
    open_states = ("reserved", "info_ok", "registry_shown", "registry_ok", "account_sent",
                   "requested", "offered", "tenant_signed", "signed", "deposit_paid")
    contracts = store.find("contracts", landlord=owner)
    out = []
    for l in sorted(store.find("listings", owner=owner), key=lambda l: l["created_at"], reverse=True):
        n = sum(1 for c in contracts if c["listing_id"] == l["id"] and c["status"] in open_states)
        out.append(public(l) | {"status": l["status"], "applications": n})
    return out


def withdraw(store: Store, owner: str, listing_id: str) -> Dict[str, Any]:
    with store.lock:
        l = store.first("listings", id=listing_id, owner=owner)
        if not l:
            raise ApiError(404, "매물을 찾을 수 없습니다.")
        if l["status"] == "reserved":
            raise ApiError(409, "진행 중인 계약이 있어 내릴 수 없습니다.", hint="계약 화면에서 먼저 취소해 주세요.")
        if l["status"] == "open":
            # 아직 조건을 제시하지 않은 신청은 함께 마감한다.
            for c in store.find("contracts", listing_id=listing_id, status="requested"):
                store.update("contracts", c["id"], status="cancelled", cancel_reason="임대인이 매물을 내렸습니다.")
            l = store.update("listings", listing_id, status="closed", closed_reason="withdrawn")
        return l
