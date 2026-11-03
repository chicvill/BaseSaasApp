"""
rent5/flow.py — 계약 진행 규칙. 중개사 없이 임대인 ↔ 임차인이 직접 맺는다.

    requested      임차인이 매물에 신청 (임대인은 신청자 이름 · 번호를 본다)
    offered        임대인이 조건(주소 · 보증금 · 월세 · 입주일 · 기간)과 등기 확인 결과를 제시
    tenant_signed  임차인이 본인 정보를 적고 같은 내용에 서명 (지문 = SHA-256)
    signed         임대인이 같은 지문에 서명 → 계약 성립, 매물 마감, 계약서 발급
    deposit_paid   임대인이 계약금 입금을 확인
    active         임대인이 잔금 입금을 확인 → 입주
    cancelled      성립 전에 어느 쪽이든 취소

돈은 두 사람 사이에서 직접 오간다. 이 프로그램은 입금을 확인한 기록만 남긴다.
"""
from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date, timedelta
from typing import Any, Dict, List

from listings import iso_date, money
from security import ApiError, clean_text
from store import Store, now_iso

STATUS_LABEL = {
    "requested": "신청함 — 임대인 조건 대기",
    "offered": "조건 제시됨 — 임차인 서명 대기",
    "tenant_signed": "임차인 서명 — 임대인 서명 대기",
    "signed": "계약 성립 — 계약금 대기",
    "deposit_paid": "계약금 확인 — 잔금 대기",
    "active": "잔금 확인 — 입주",
    "cancelled": "취소됨",
}
STEPS = ["requested", "offered", "tenant_signed", "signed", "deposit_paid", "active"]
IN_PROGRESS = ("requested", "offered", "tenant_signed", "signed", "deposit_paid")
MAX_OPEN_APPLICATIONS = 5
REPORT_DEPOSIT, REPORT_RENT = 60_000_000, 300_000     # 주택임대차 신고 대상 기준


# ── 공통 ──
def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def add_months(d: date, n: int) -> date:
    year, month0 = divmod(d.month - 1 + n, 12)
    year, month = d.year + year, month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _get(store: Store, cid: str) -> Dict[str, Any]:
    c = store.first("contracts", id=cid)
    if not c:
        raise ApiError(404, "계약을 찾을 수 없습니다.")
    return c


def role_of(c: Dict[str, Any], actor: str) -> str:
    if actor == c["landlord"]:
        return "landlord"
    if actor == c["tenant"]:
        return "tenant"
    raise ApiError(404, "계약을 찾을 수 없습니다.")          # 당사자가 아니면 있는지도 알려 주지 않는다


def _need(c: Dict[str, Any], actor: str, role: str, *statuses: str) -> None:
    if role_of(c, actor) != role:
        raise ApiError(403, "이 단계는 상대방이 진행합니다.")
    if c["status"] not in statuses:
        raise ApiError(409, "지금 단계에서는 할 수 없습니다.", hint=STATUS_LABEL.get(c["status"], ""))


def _update(store: Store, c: Dict[str, Any], actor: str, what: str, **changes: Any) -> Dict[str, Any]:
    events = c["events"] + [{"at": now_iso(), "by": actor, "what": what}]
    return store.update("contracts", c["id"], events=events, **changes)


def _party(store: Store, username: str) -> Dict[str, str]:
    u = store.first("users", username=username) or {}
    return {"username": username, "name": u.get("name", ""), "phone": u.get("phone", "")}


# ── 1. 신청 ──
def apply(store: Store, listing_id: str, tenant: str, message: str = "") -> Dict[str, Any]:
    with store.lock:
        l = store.first("listings", id=listing_id)
        if not l or l["status"] != "open":
            raise ApiError(404, "마감되었거나 없는 매물입니다.")
        if l["owner"] == tenant:
            raise ApiError(409, "내가 올린 매물에는 신청할 수 없습니다.")
        mine = [c for c in store.find("contracts", tenant=tenant) if c["status"] in IN_PROGRESS]
        if any(c["listing_id"] == listing_id for c in mine):
            raise ApiError(409, "이미 신청한 매물입니다.", hint="내 화면의 계약 목록에서 이어서 진행하세요.")
        if len(mine) >= MAX_OPEN_APPLICATIONS:
            raise ApiError(409, f"동시에 진행할 수 있는 신청은 {MAX_OPEN_APPLICATIONS}건까지입니다.")
        c = store.insert("contracts", {
            "listing_id": listing_id, "landlord": l["owner"], "tenant": tenant, "status": "requested",
            "message": clean_text(message, "남길 말", 300), "terms": None, "registry": None, "tenant_info": None,
            "offer_hash": "", "final_hash": "", "snapshot": None, "events": [],
        })
        return _update(store, c, tenant, "apply")


# ── 2. 조건 제시 (임대인) ──
def _terms(data: Dict[str, Any], listing: Dict[str, Any]) -> Dict[str, Any]:
    address = clean_text(data.get("address") or f"{listing['address']} {listing['room']}".strip(), "주소", 120, required=True)
    deposit = money(data, "deposit", "보증금")
    down = money(data, "down_payment", "계약금")
    if down > deposit:
        raise ApiError(422, "계약금은 보증금보다 클 수 없습니다.")
    rent = 0 if listing["lease_type"] == "전세" else money(data, "rent", "월세")
    try:
        raw_months = data.get("months")
        months = 12 if raw_months in (None, "") else int(raw_months)
    except (TypeError, ValueError):
        raise ApiError(422, "계약 기간(개월)이 숫자가 아닙니다.")
    if not 1 <= months <= 120:
        raise ApiError(422, "계약 기간은 1~120개월입니다.")
    start = date.fromisoformat(iso_date(data.get("start"), "입주일", required=True))
    return {"address": address, "lease_type": listing["lease_type"], "deposit": deposit, "down_payment": down,
            "rent": rent, "mgmt": money(data, "mgmt", "관리비"), "start": start.isoformat(), "months": months,
            "end": (add_months(start, months) - timedelta(days=1)).isoformat()}


def _registry(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("owner_match") is not True:
        raise ApiError(422, "등기부 소유자가 임대인 본인임을 확인해야 합니다.", hint="소유자가 다르면 계약을 진행할 수 없습니다.")
    if data.get("seizure") is not False:
        raise ApiError(422, "압류·가압류 여부를 '없음'으로 확인해야 제시할 수 있습니다.")
    return {"owner_match": True, "seizure": False, "mortgage": money(data, "mortgage", "근저당 채권최고액"),
            "note": clean_text(data.get("note"), "등기 메모", 500), "checked_on": date.today().isoformat()}


def offer(store: Store, cid: str, actor: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "requested", "offered")
        l = store.first("listings", id=c["listing_id"])
        others = store.find("contracts", listing_id=c["listing_id"])
        if any(o["id"] != cid and o["status"] in ("tenant_signed", "signed", "deposit_paid", "active") for o in others):
            raise ApiError(409, "이미 다른 신청자와 계약이 진행 중입니다.")
        terms, registry = _terms(data, l), _registry(data)
        for o in others:                                     # 같은 매물의 다른 신청은 마감
            if o["id"] != cid and o["status"] in ("requested", "offered"):
                _update(store, o, actor, "cancel", status="cancelled", cancel_reason="다른 신청자와 계약을 진행합니다.")
        store.update("listings", l["id"], status="reserved", reserved_by=cid)
        return _update(store, c, actor, "offer", status="offered", terms=terms, registry=registry,
                       offer_hash=digest({"terms": terms, "registry": registry, "landlord": c["landlord"], "tenant": c["tenant"]}))


# ── 3. 임차인 서명 ──
def tenant_sign(store: Store, cid: str, actor: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "tenant", "offered")
        if data.get("agree") is not True:
            raise ApiError(422, "계약 내용에 동의해야 서명할 수 있습니다.")
        if data.get("offer_hash") != c["offer_hash"]:
            raise ApiError(409, "그 사이 조건이 바뀌었습니다.", hint="화면을 새로 고쳐 다시 확인해 주세요.")
        birth = date.fromisoformat(iso_date(data.get("birth"), "생년월일", required=True))
        if not date(1900, 1, 1) <= birth < date.today():
            raise ApiError(422, "생년월일이 올바르지 않습니다.")
        info = {"birth": birth.isoformat(),
                "address": clean_text(data.get("address"), "현재 주소", 120, required=True),
                "emergency": clean_text(data.get("emergency"), "비상 연락처", 60, required=True)}
        snapshot = {"contract_id": c["id"], "landlord": _party(store, c["landlord"]), "tenant": _party(store, c["tenant"]),
                    "terms": c["terms"], "registry": c["registry"], "tenant_info": info}
        return _update(store, c, actor, "tenant_sign", status="tenant_signed", tenant_info=info, snapshot=snapshot,
                       final_hash=digest(snapshot), tenant_signed_at=now_iso())


# ── 4. 임대인 서명 → 계약 성립 ──
def landlord_sign(store: Store, cid: str, actor: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "tenant_signed")
        if data.get("final_hash") != c["final_hash"]:
            raise ApiError(409, "서명할 내용이 바뀌었습니다.", hint="화면을 새로 고쳐 다시 확인해 주세요.")
        store.update("listings", c["listing_id"], status="closed", closed_reason="contracted", reserved_by=c["id"])
        return _update(store, c, actor, "landlord_sign", status="signed", signed_at=now_iso())


# ── 5·6. 돈 확인 (임대인) ──
def confirm_deposit(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "signed")
        return _update(store, c, actor, "deposit", status="deposit_paid", deposit_confirmed_at=now_iso())


def confirm_balance(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "deposit_paid")
        return _update(store, c, actor, "balance", status="active", moved_in_at=now_iso())


# ── 취소 (성립 전) ──
def cancel(store: Store, cid: str, actor: str, reason: str = "") -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        role_of(c, actor)
        if c["status"] not in ("requested", "offered", "tenant_signed"):
            raise ApiError(409, "계약이 성립한 뒤에는 이 화면에서 취소할 수 없습니다.", hint="두 사람이 직접 합의해 해지해야 합니다.")
        l = store.first("listings", id=c["listing_id"])
        if l and l["status"] == "reserved" and l.get("reserved_by") == cid:
            store.update("listings", l["id"], status="open", reserved_by="")
        return _update(store, c, actor, "cancel", status="cancelled", cancelled_by=actor,
                       cancel_reason=clean_text(reason, "취소 사유", 200) or "취소")


# ── 화면용 ──
def actions(c: Dict[str, Any], role: str) -> List[str]:
    s, out = c["status"], []
    if role == "landlord":
        out += {"requested": ["offer"], "offered": ["offer"], "tenant_signed": ["landlord_sign"],
                "signed": ["deposit"], "deposit_paid": ["balance"]}.get(s, [])
    elif s == "offered":
        out.append("tenant_sign")
    if s in ("requested", "offered", "tenant_signed"):
        out.append("cancel")
    if s in ("signed", "deposit_paid", "active"):
        out.append("document")
    return out


def view(store: Store, c: Dict[str, Any], actor: str) -> Dict[str, Any]:
    role = role_of(c, actor)
    l = store.first("listings", id=c["listing_id"]) or {}
    keys = ("id", "listing_id", "status", "message", "terms", "registry", "tenant_info", "offer_hash", "final_hash",
            "tenant_signed_at", "signed_at", "deposit_confirmed_at", "moved_in_at", "cancel_reason", "events", "created_at")
    terms = c["terms"] or {}
    return {k: c.get(k) for k in keys} | {
        "status_label": STATUS_LABEL[c["status"]], "role": role, "actions": actions(c, role), "steps": STEPS,
        "landlord": _party(store, c["landlord"]), "tenant": _party(store, c["tenant"]),
        "listing": {k: l.get(k) for k in ("address", "room", "lease_type", "deposit", "rent", "mgmt", "available_from")},
        "report_required": bool(terms) and (terms["deposit"] > REPORT_DEPOSIT or terms["rent"] > REPORT_RENT),
    }


def get_view(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    return view(store, _get(store, cid), actor)


def summaries(store: Store, user: str) -> List[Dict[str, Any]]:
    out = []
    for c in store.all("contracts"):
        if user not in (c["landlord"], c["tenant"]):
            continue
        role = role_of(c, user)
        l = store.first("listings", id=c["listing_id"]) or {}
        other = _party(store, c["tenant"] if role == "landlord" else c["landlord"])
        out.append({"id": c["id"], "role": role, "status": c["status"], "status_label": STATUS_LABEL[c["status"]],
                    "address": ((c["terms"] or {}).get("address")) or f"{l.get('address', '')} {l.get('room', '')}".strip(),
                    "other_name": other["name"], "other_phone": other["phone"], "created_at": c["created_at"],
                    "updated_at": c.get("updated_at") or c["created_at"]})
    return sorted(out, key=lambda x: x["updated_at"], reverse=True)


def document(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    c = _get(store, cid)
    role_of(c, actor)
    if c["status"] not in ("signed", "deposit_paid", "active"):
        raise ApiError(409, "두 사람이 모두 서명한 뒤에 계약서가 나옵니다.")
    terms = c["snapshot"]["terms"]
    return {"id": c["id"], "status": c["status"], "snapshot": c["snapshot"], "final_hash": c["final_hash"],
            "tenant_signed_at": c["tenant_signed_at"], "signed_at": c["signed_at"],
            "deposit_confirmed_at": c.get("deposit_confirmed_at"), "moved_in_at": c.get("moved_in_at"),
            "report_required": terms["deposit"] > REPORT_DEPOSIT or terms["rent"] > REPORT_RENT}
