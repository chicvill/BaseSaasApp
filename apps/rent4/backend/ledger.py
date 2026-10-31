"""
apps/rent4/backend/ledger.py — 수납 원장 계산 (월별 상태 · 입금 매칭 · FIFO 배정 · 임차인 조회).

월별 상태
    paid     : 해당 연월 수납 기록 있음
    vacant   : 공실 표기
    move_in  : 입주월 (후불이면 첫 납부는 다음 달) — 미납으로 보지 않음
    move_out : 전출 표기
    unpaid   : 거주 중인데 지난달까지 기록 없음
    due      : 이번 달, 아직 기록 없음 (납부 대기)
    prior    : 현 임차인 입주 전 (이전 임차인 기간, 임차인 화면에는 보이지 않음)
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional

from core.errors import Mismatch, NotFound

from .bank_parser import Deposit
from .models import BankMessage, MonthMark, Payment, Room, ym_add, ym_of, ym_range


def today() -> date:
    return date.today()


def first_data_ym() -> Optional[str]:
    yms = [p.ym for p in Payment.all()] + [m.ym for m in MonthMark.all()]
    return min(yms) if yms else None


def month_status(room: Room, ym: str, pays: List[Payment], marks: List[MonthMark], current_ym: str) -> Dict:
    paid = [p for p in pays if p.ym == ym]
    mark = next((m for m in marks if m.ym == ym), None)
    cell: Dict = {"ym": ym, "amount": sum(p.amount for p in paid),
                  "rent": sum(p.rent for p in paid), "mgmt": sum(p.mgmt for p in paid),
                  "paid_dates": [p.paid_date or p.paid_date_raw for p in paid],
                  "payments": [{"id": p.id, "paid_date": p.paid_date or p.paid_date_raw, "source": p.source,
                                "amount": p.amount} for p in paid],
                  "sources": sorted({p.source for p in paid}),
                  "flags": [f for p in paid for f in p.flags], "mark": mark.raw if mark else ""}
    before_tenant = bool(room.move_in_ym and ym < room.move_in_ym)
    if paid:
        status = "paid"
    elif mark and mark.kind in ("vacant", "move_out"):
        status = mark.kind
    elif before_tenant:
        status = "prior"
    elif room.move_in_ym == ym or (mark and mark.kind == "move_in"):
        status = "move_in"
    elif ym > current_ym:
        status = "future"
    elif ym == current_ym:
        status = "due"
    else:
        status = "unpaid"
    cell["status"] = status
    cell["prior"] = before_tenant
    return cell


def room_months(room: Room, yms: List[str], current_ym: Optional[str] = None) -> List[Dict]:
    current_ym = current_ym or ym_of(today())
    pays = Payment.find(room=room.room)
    marks = MonthMark.find(room=room.room)
    return [month_status(room, ym, pays, marks, current_ym) for ym in yms]


def board(year: int, branch: Optional[str]) -> Dict:
    yms = [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    rooms = [r for r in Room.all() if branch is None or r.branch == branch]
    rows = []
    for r in rooms:
        months = room_months(r, yms) if r.status != "owner" else []
        rows.append({"room": public_room(r, full=True), "months": months,
                     "unpaid": sum(1 for c in months if c["status"] == "unpaid")})
    totals = []
    for i, ym in enumerate(yms):
        cells = [row["months"][i] for row in rows if row["months"]]
        totals.append({"ym": ym, "rent": sum(c["rent"] for c in cells), "mgmt": sum(c["mgmt"] for c in cells),
                       "amount": sum(c["amount"] for c in cells)})
    return {"year": year, "rooms": rows, "totals": totals}


def public_room(r: Room, full: bool = False) -> Dict:
    data = {"room": r.room, "building": r.building, "status": r.status, "tenant_name": r.tenant_name,
            "move_in": r.move_in, "expire": r.expire, "lease_type": r.lease_type, "pay_timing": r.pay_timing,
            "deposit": r.deposit, "rent": r.rent, "mgmt": r.mgmt, "monthly_due": r.monthly_due}
    if full:
        data["phone"] = r.phone
    return data


# ── 임차인 조회 ──
def tenant_view(room_no: str, phone4: str) -> Dict:
    room = Room.first(room=room_no.strip())
    if not room or room.status != "occupied" or not room.phone_last4 or room.phone_last4 != phone4.strip():
        raise NotFound("호실 또는 전화번호 뒤 4자리가 일치하지 않습니다.",
                       hint="임대차 계약 때 등록한 휴대폰 번호의 마지막 4자리를 입력해 주세요.")
    current = ym_of(today())
    start = room.move_in_ym or first_data_ym() or current
    start = max(start, first_data_ym() or start)
    months = [c for c in room_months(room, ym_range(start, current), current) if not c["prior"]]
    for c in months:
        for p in c["payments"]:
            p.pop("id", None)
        c.pop("prior", None)
    unpaid = [c for c in months if c["status"] == "unpaid"]
    return {
        "room": public_room(room),
        "months": list(reversed(months)),
        "summary": {"paid_months": sum(1 for c in months if c["status"] == "paid"),
                    "unpaid_months": len(unpaid), "unpaid_amount": len(unpaid) * room.monthly_due,
                    "last_paid": next((c["paid_dates"][-1] for c in reversed(months) if c["status"] == "paid"), None)},
    }


# ── 입금 문자 → 호실 매칭 & FIFO 배정 제안 ──
def match_room(dep: Deposit, rooms: List[Room]) -> Dict:
    occupied = [r for r in rooms if r.status == "occupied"]
    for name in dep.names:
        num = re.search(r"\d{3,4}", name)
        if num:
            hit = [r for r in occupied if r.room == num[0]]
            if hit:
                return {"room": hit[0], "by": f"입금자명의 호실 번호 '{num[0]}'", "candidates": []}
        letters = re.sub(r"[\d\s호]", "", name)
        hits = [r for r in occupied if r.tenant_name and len(letters) >= 2
                and (r.tenant_name == letters or r.tenant_name in letters or letters in r.tenant_name)]
        if len(hits) == 1:
            return {"room": hits[0], "by": f"입금자명 '{name}'", "candidates": []}
        if len(hits) > 1:
            return {"room": None, "by": "", "candidates": [h.room for h in hits]}
    return {"room": None, "by": "", "candidates": []}


def open_months(room: Room, current_ym: str) -> List[str]:
    """미납 → 이번 달(대기) → 다음 달 순으로, 수납 기록이 없는 귀속월."""
    start = max(room.move_in_ym or current_ym, first_data_ym() or current_ym)
    cells = room_months(room, ym_range(start, ym_add(current_ym, 12)), current_ym)
    return [c["ym"] for c in cells if c["status"] in ("unpaid", "due", "future")]


def propose(dep: Deposit, existing_fingerprints: set) -> Dict:
    """입금 1건의 처리 제안 (아직 저장하지 않음)."""
    out: Dict = {**dep.to_dict(), "status": "ready", "room": None, "months": [], "message": "", "matched_by": ""}
    if dep.kind != "deposit":
        out.update(status="skip", message="입금 문자가 아니어서 건너뜁니다.")
        return out
    if dep.fingerprint in existing_fingerprints:
        out.update(status="duplicate", message="이미 반영된 입금 문자입니다.")
        return out
    if not dep.amount:
        out.update(status="needs_review", message="입금액을 찾지 못했습니다. 금액을 직접 입력해 주세요.")
        return out

    m = match_room(dep, Room.all())
    room: Optional[Room] = m["room"]
    if room is None:
        hint = f" 후보: {', '.join(m['candidates'])}호" if m["candidates"] else ""
        out.update(status="needs_review", message=f"입금자 '{', '.join(dep.names) or '?'}' 와 일치하는 호실이 없습니다.{hint}")
        return out

    out.update(room=room.room, tenant_name=room.tenant_name, matched_by=m["by"], monthly_due=room.monthly_due)
    current = ym_of(date.fromisoformat(dep.deposit_date)) if dep.deposit_date else ym_of(today())
    months = open_months(room, current)
    due = room.monthly_due
    if due <= 0:
        out.update(status="needs_review", message=f"{room.room}호의 월 청구액이 0원입니다. 호실 정보를 확인해 주세요.")
        return out
    k, rest = divmod(dep.amount, due)
    if rest == 0 and k >= 1:
        out["months"] = months[:k]
        out["message"] = f"{room.room}호 {room.tenant_name} · {k}개월분 ({', '.join(out['months'])})"
    else:
        out["months"] = months[:max(k, 1)]
        out.update(status="diff",
                   message=f"월 청구액 {due:,}원과 맞지 않습니다 (입금 {dep.amount:,}원, 차이 {dep.amount - due * max(k, 1):+,}원). "
                           f"귀속월을 확인한 뒤 반영하세요.")
    return out


def commit(item: Dict) -> Dict:
    """제안(또는 임대인이 고친 제안) 1건을 수납 기록으로 저장한다."""
    room = Room.first(room=str(item.get("room") or ""))
    if not room:
        raise NotFound("호실을 지정해 주세요.", hint="미확인 입금은 호실을 고른 뒤 다시 반영하세요.")
    months: List[str] = [m for m in item.get("months") or [] if re.match(r"^\d{4}-\d{2}$", m)]
    amount = int(item.get("amount") or 0)
    if not months or amount <= 0:
        raise Mismatch("귀속월과 입금액이 필요합니다.", hint="귀속월(YYYY-MM)을 1개 이상 선택해 주세요.")
    fingerprint = item.get("fingerprint") or ""
    if fingerprint and BankMessage.first(fingerprint=fingerprint):
        return {"status": "duplicate", "room": room.room, "payment_ids": []}

    msg = BankMessage(raw=item.get("raw", ""), fingerprint=fingerprint, bank=item.get("bank", ""),
                      deposit_date=item.get("deposit_date"), deposit_time=item.get("deposit_time", ""),
                      amount=amount, depositor=", ".join(item.get("names") or []), room=room.room,
                      months=months, parsed_by=item.get("parsed_by", "rule"))
    payments = []
    remaining = amount
    for i, ym in enumerate(months):
        share = remaining if i == len(months) - 1 else min(remaining, room.monthly_due)
        remaining -= share
        mgmt = min(room.mgmt, share)
        flags = [] if share == room.monthly_due else [f"청구액 {room.monthly_due:,}원과 다른 입금 {share:,}원"]
        payments.append(Payment(room=room.room, ym=ym, paid_date=item.get("deposit_date"),
                                paid_date_raw=f"{item.get('deposit_date') or ''} {item.get('deposit_time') or ''}".strip(),
                                rent=share - mgmt, mgmt=mgmt, source="bank", bank_message_id=msg.id,
                                depositor=msg.depositor, flags=flags))
    for p in payments:
        p.save()
    msg.payment_ids = [p.id for p in payments]
    msg.save()
    return {"status": "saved", "room": room.room, "months": months, "payment_ids": msg.payment_ids}
