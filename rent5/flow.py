"""
rent5/flow.py — 계약 진행 규칙. 중개사 없이 임대인 ↔ 임차인이 직접 맺는다.

    매물 목록에서 [계약 신청] → 임대인에게 전화 · 현장 방문 → [예약] → 계약방(대화창)이 열린다

    reserved        예약 — 임차인은 임대인(매물) 정보를, 임대인은 임차인 정보를 서로 확인한다 (각자 상대 것만 체크)
    info_ok         두 쪽 모두 확인 — 임대인이 등기부등본을 열람해 임차인에게 보여 주고 선순위 조건을 올린다
    registry_shown  임차인이 "등기부등본 확인했음"을 체크한다
    registry_ok     임대인이 계약금(보증금의 10%) 송금 계좌를 알린다
    account_sent    임차인이 계약금을 송금했다고 알린다
    completed       계약 종료 (기록 · 확인서 발급)
    cancelled       종료 전에 어느 쪽이든 취소 → 매물은 다시 열린다

단계마다 한 줄씩 대화창에 말풍선으로 남는다. 돈은 두 사람 사이에서 직접 오간다.
이 프로그램은 누가 언제 무엇을 확인했는지만 기록한다.
(이전의 조건 제시 · 서명 방식 계약은 기록으로만 남아 있고, 진행 중이던 것은 취소만 할 수 있다.)
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Dict, List

from .listings import money
from .security import ApiError, clean_text
from .store import Store, now_iso

STATUS_LABEL = {
    "reserved": "예약됨 — 서로 정보 확인",
    "info_ok": "정보 확인 완료 — 임대인 등기부 열람",
    "registry_shown": "등기부 열람 — 임차인 확인",
    "registry_ok": "등기부 확인 — 임대인 계좌 안내",
    "account_sent": "계약금 계좌 안내 — 임차인 송금",
    "completed": "계약금 송금 — 계약 종료",
    "cancelled": "취소됨",
    # 이전 방식(서명) 계약 — 기록만 남아 있다
    "requested": "신청함 — 임대인 조건 대기 (이전 방식)",
    "offered": "조건 제시됨 — 임차인 서명 대기 (이전 방식)",
    "tenant_signed": "임차인 서명 — 임대인 서명 대기 (이전 방식)",
    "signed": "계약 성립 — 계약금 대기 (이전 방식)",
    "deposit_paid": "계약금 확인 — 잔금 대기 (이전 방식)",
    "active": "잔금 확인 — 입주 (이전 방식)",
}
STEPS = ["reserved", "info_ok", "registry_shown", "registry_ok", "account_sent", "completed"]
STEP_LABELS = ["예약", "정보 확인", "등기부 열람", "등기부 확인", "계좌 안내", "송금 · 종료"]
LEGACY_STEPS = ["requested", "offered", "tenant_signed", "signed", "deposit_paid", "active"]
LEGACY_LABELS = ["신청", "조건 제시", "임차인 서명", "계약 성립", "계약금", "잔금 · 입주"]
IN_PROGRESS = ("reserved", "info_ok", "registry_shown", "registry_ok", "account_sent")
LEGACY_IN_PROGRESS = ("requested", "offered", "tenant_signed", "signed", "deposit_paid")
CANCELLABLE = IN_PROGRESS + ("requested", "offered", "tenant_signed")
MAX_OPEN_RESERVATIONS = 3
REPORT_DEPOSIT, REPORT_RENT = 60_000_000, 300_000     # 주택임대차 신고 대상 기준
ACCOUNT_RE = re.compile(r"^[0-9][0-9\- ]{4,28}$")


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
        raise ApiError(403, "이 단계는 상대방만 할 수 있습니다.", hint="임차인 확인은 임차인이, 임차인 정보 확인은 임대인이 합니다.")
    if c["status"] not in statuses:
        raise ApiError(409, "지금 단계에서는 할 수 없습니다.", hint=STATUS_LABEL.get(c["status"], ""))


def _update(store: Store, c: Dict[str, Any], actor: str, what: str, text: str = "", **changes: Any) -> Dict[str, Any]:
    events = c["events"] + [{"at": now_iso(), "by": actor, "what": what, "text": text}]
    return store.update("contracts", c["id"], events=events, **changes)


def _party(store: Store, username: str) -> Dict[str, str]:
    u = store.first("users", username=username) or {}
    return {"username": username, "name": u.get("name", ""), "phone": u.get("phone", "")}


def _won(n: int) -> str:
    return f"{n:,}원"


def _terms_from(l: Dict[str, Any]) -> Dict[str, Any]:
    start, months = l.get("available_from") or "", 12
    end = (add_months(date.fromisoformat(start), months) - timedelta(days=1)).isoformat() if start else ""
    return {"address": f"{l['address']} {l['room']}".strip(), "lease_type": l["lease_type"], "deposit": l["deposit"],
            "down_payment": l["deposit"] // 10, "rent": l["rent"], "mgmt": l["mgmt"], "start": start, "months": months, "end": end}


# ── 1. 예약 (임차인) — 매물은 예약한 사람 한 명만 진행한다 ──
def reserve(store: Store, listing_id: str, tenant: str) -> Dict[str, Any]:
    with store.lock:
        l = store.first("listings", id=listing_id)
        if not l or l["status"] == "closed":
            raise ApiError(404, "마감되었거나 없는 매물입니다.")
        if l["status"] != "open":
            raise ApiError(409, "이미 다른 분이 예약한 매물입니다.", hint="다른 매물을 골라 주세요.")
        if l["owner"] == tenant:
            raise ApiError(409, "내가 올린 매물은 예약할 수 없습니다.")
        mine = [c for c in store.find("contracts", tenant=tenant) if c["status"] in IN_PROGRESS + LEGACY_IN_PROGRESS]
        if len(mine) >= MAX_OPEN_RESERVATIONS:
            raise ApiError(409, f"동시에 예약할 수 있는 매물은 {MAX_OPEN_RESERVATIONS}개까지입니다.", hint="진행하지 않을 예약은 먼저 취소해 주세요.")
        c = store.insert("contracts", {
            "listing_id": listing_id, "landlord": l["owner"], "tenant": tenant, "status": "reserved", "flow": 2, "message": "",
            "terms": _terms_from(l), "registry": None, "account": None, "tenant_info": None,
            "landlord_info_ok_at": "", "tenant_info_ok_at": "", "registry_shown_at": "", "registry_ok_at": "",
            "account_sent_at": "", "paid_at": "", "offer_hash": "", "final_hash": "", "snapshot": None, "events": [], "chat": [],
        })
        store.update("listings", listing_id, status="reserved", reserved_by=c["id"])
        return _update(store, c, tenant, "reserve", "이 매물을 예약했습니다. 직접 보고 확인할게요.")


# ── 2. 서로 정보 확인 — 임차인은 임대인(매물) 것만, 임대인은 임차인 것만 체크한다 ──
def confirm_landlord_info(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "tenant", "reserved")
        if c["landlord_info_ok_at"]:
            raise ApiError(409, "이미 확인했습니다.")
        status = "info_ok" if c["tenant_info_ok_at"] else "reserved"
        return _update(store, c, actor, "landlord_info_ok", "임대인 · 매물 정보를 확인했습니다.", status=status, landlord_info_ok_at=now_iso())


def confirm_tenant_info(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "reserved")
        if c["tenant_info_ok_at"]:
            raise ApiError(409, "이미 확인했습니다.")
        status = "info_ok" if c["landlord_info_ok_at"] else "reserved"
        return _update(store, c, actor, "tenant_info_ok", "임차인 정보를 확인했습니다.", status=status, tenant_info_ok_at=now_iso())


# ── 3. 등기부등본 열람 (임대인, 대면) → 선순위 조건 ──
def _registry(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("owner_match") is not True:
        raise ApiError(422, "등기부 소유자가 임대인 본인임을 확인해야 합니다.", hint="소유자가 다르면 계약을 진행할 수 없습니다.")
    if data.get("seizure") is not False:
        raise ApiError(422, "압류·가압류 여부를 '없음'으로 확인해야 올릴 수 있습니다.")
    return {"owner_match": True, "seizure": False, "mortgage": money(data, "mortgage", "근저당 채권최고액"),
            "note": clean_text(data.get("note"), "선순위 메모", 300), "checked_on": date.today().isoformat()}


def show_registry(store: Store, cid: str, actor: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "info_ok")
        registry = _registry(data)
        text = (f"등기부등본을 열람해 임차인에게 보여 주었습니다.\n소유자 본인 일치 · 압류·가압류 없음 · 선순위 근저당 {_won(registry['mortgage'])}"
                + (f"\n메모: {registry['note']}" if registry["note"] else ""))
        return _update(store, c, actor, "registry_shown", text, status="registry_shown", registry=registry, registry_shown_at=now_iso())


def confirm_registry(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "tenant", "registry_shown")
        return _update(store, c, actor, "registry_ok", "등기부등본과 선순위 조건을 확인했습니다.", status="registry_ok", registry_ok_at=now_iso())


# ── 4. 계약금(보증금의 10%) 계좌 안내 (임대인) ──
def send_account(store: Store, cid: str, actor: str, data: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "landlord", "registry_ok")
        bank = clean_text(data.get("bank"), "은행", 20, required=True)
        number = clean_text(data.get("number"), "계좌번호", 30, required=True)
        holder = clean_text(data.get("holder"), "예금주", 20, required=True)
        if not ACCOUNT_RE.match(number) or not 6 <= len(re.sub(r"\D", "", number)) <= 20:
            raise ApiError(422, "계좌번호는 숫자와 하이픈(-)으로 적어 주세요.")
        norm = lambda s: re.sub(r"\s", "", s)
        landlord = _party(store, c["landlord"])
        amount = c["terms"]["down_payment"]
        account = {"bank": bank, "number": number, "holder": holder, "amount": amount, "holder_match": norm(holder) == norm(landlord["name"])}
        text = (f"계약금 {_won(amount)}(보증금의 10%)을 아래 계좌로 보내 주세요.\n{bank} {number} · 예금주 {holder}"
                + ("" if account["holder_match"] else "\n⚠ 예금주가 임대인 이름과 다릅니다. 송금 전에 꼭 확인하세요."))
        return _update(store, c, actor, "account_sent", text, status="account_sent", account=account, account_sent_at=now_iso())


# ── 5. 임차인 송금 → 계약 종료 ──
def pay(store: Store, cid: str, actor: str) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        _need(c, actor, "tenant", "account_sent")
        paid_at = now_iso()
        snapshot = {"version": 2, "contract_id": c["id"], "landlord": _party(store, c["landlord"]), "tenant": _party(store, c["tenant"]),
                    "terms": c["terms"], "registry": c["registry"], "account": c["account"],
                    "times": {"landlord_info_ok_at": c["landlord_info_ok_at"], "tenant_info_ok_at": c["tenant_info_ok_at"],
                              "registry_shown_at": c["registry_shown_at"], "registry_ok_at": c["registry_ok_at"],
                              "account_sent_at": c["account_sent_at"], "paid_at": paid_at}}
        store.update("listings", c["listing_id"], status="closed", closed_reason="contracted", reserved_by=c["id"])
        return _update(store, c, actor, "pay", f"계약금 {_won(c['account']['amount'])}을 송금했습니다. 계약이 종료되었습니다.",
                       status="completed", paid_at=paid_at, snapshot=snapshot, final_hash=digest(snapshot))


# ── 취소 (종료 전) — 매물은 다시 열린다 ──
def cancel(store: Store, cid: str, actor: str, reason: str = "") -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        role_of(c, actor)
        if c["status"] not in CANCELLABLE:
            raise ApiError(409, "이미 끝난 계약은 이 화면에서 취소할 수 없습니다.", hint="두 사람이 직접 합의해 해지해야 합니다.")
        l = store.first("listings", id=c["listing_id"])
        if l and l["status"] == "reserved" and l.get("reserved_by") == cid:
            store.update("listings", l["id"], status="open", reserved_by="")
        reason = clean_text(reason, "취소 사유", 200) or "취소"
        return _update(store, c, actor, "cancel", f"예약을 취소했습니다. ({reason})", status="cancelled", cancelled_by=actor, cancel_reason=reason)


# ── 대화 (계약 당사자 둘만, 어느 단계에서나) ──
MAX_CHAT = 500


def say(store: Store, cid: str, actor: str, text: Any) -> Dict[str, Any]:
    with store.lock:
        c = _get(store, cid)
        role_of(c, actor)
        text = clean_text(text, "메시지", 500, required=True)
        chat = c.get("chat") or []
        if len(chat) >= MAX_CHAT:
            raise ApiError(409, f"대화는 계약마다 {MAX_CHAT}개까지 남길 수 있습니다.", hint="전화로 이어서 이야기해 주세요.")
        return store.update("contracts", cid, chat=chat + [{"at": now_iso(), "by": actor, "text": text}])


# ── 화면용 ──
def actions(c: Dict[str, Any], role: str) -> List[str]:
    s, out = c["status"], []
    if role == "tenant":
        if s == "reserved" and not c.get("landlord_info_ok_at"):
            out.append("confirm_landlord_info")
        if s == "registry_shown":
            out.append("confirm_registry")
        if s == "account_sent":
            out.append("pay")
    else:
        if s == "reserved" and not c.get("tenant_info_ok_at"):
            out.append("confirm_tenant_info")
        if s == "info_ok":
            out.append("show_registry")
        if s == "registry_ok":
            out.append("send_account")
    if s in CANCELLABLE:
        out.append("cancel")
    if s in ("completed", "signed", "deposit_paid", "active"):
        out.append("document")
    return out


def view(store: Store, c: Dict[str, Any], actor: str) -> Dict[str, Any]:
    role = role_of(c, actor)
    l = store.first("listings", id=c["listing_id"]) or {}
    keys = ("id", "listing_id", "status", "message", "terms", "registry", "account", "tenant_info", "offer_hash", "final_hash",
            "landlord_info_ok_at", "tenant_info_ok_at", "registry_shown_at", "registry_ok_at", "account_sent_at", "paid_at",
            "tenant_signed_at", "signed_at", "deposit_confirmed_at", "moved_in_at", "cancel_reason", "created_at")
    legacy = c.get("flow") != 2                          # 이전 방식(서명) 계약
    names = {"landlord": c["landlord"], "tenant": c["tenant"]}
    who = lambda username: "landlord" if username == names["landlord"] else "tenant"
    events = [{"at": e["at"], "what": e["what"], "text": e.get("text", ""), "role": who(e["by"]), "mine": e["by"] == actor} for e in c["events"]]
    chat = [{"at": m["at"], "mine": m["by"] == actor, "role": who(m["by"]), "text": m["text"]} for m in c.get("chat") or []]
    terms = c.get("terms") or {}
    return {k: c.get(k) for k in keys} | {
        "events": events, "chat": chat, "status_label": STATUS_LABEL[c["status"]], "role": role, "actions": actions(c, role),
        "steps": LEGACY_STEPS if legacy else STEPS, "step_labels": LEGACY_LABELS if legacy else STEP_LABELS,
        "landlord": _party(store, c["landlord"]), "tenant": _party(store, c["tenant"]),
        "listing": {k: l.get(k) for k in ("address", "room", "lease_type", "deposit", "rent", "mgmt", "available_from", "description")},
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
    if c["status"] == "completed":
        terms = c["snapshot"]["terms"]
        return {"id": c["id"], "status": c["status"], "snapshot": c["snapshot"], "final_hash": c["final_hash"], "paid_at": c["paid_at"],
                "report_required": terms["deposit"] > REPORT_DEPOSIT or terms["rent"] > REPORT_RENT}
    if c["status"] in ("signed", "deposit_paid", "active"):                     # 이전 방식 계약서
        terms = c["snapshot"]["terms"]
        return {"id": c["id"], "status": c["status"], "snapshot": c["snapshot"], "final_hash": c["final_hash"],
                "tenant_signed_at": c["tenant_signed_at"], "signed_at": c["signed_at"],
                "deposit_confirmed_at": c.get("deposit_confirmed_at"), "moved_in_at": c.get("moved_in_at"),
                "report_required": terms["deposit"] > REPORT_DEPOSIT or terms["rent"] > REPORT_RENT}
    raise ApiError(409, "계약이 끝난 뒤에 확인서가 나옵니다.")
