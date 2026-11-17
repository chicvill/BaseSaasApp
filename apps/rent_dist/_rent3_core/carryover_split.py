"""
apps/rent_dist/_rent3_core/carryover_split.py — 이관 '이전 입금 합산(:carry)' 납부 건을 달별 납부 건으로 분해한다.

배경: 장부 이관 때 과거 여러 달의 입금을 한 건(이월 합산, 청구월=이관 시점)으로 넣었다. 이 한 건이 여러 달에 걸쳐 있어서 원장이 옛 FIFO(남는 금액을 오래된 달부터)에
기대야 하고, 달 옮기기·무효 처리가 불가능하다. 달별 건으로 풀면 모든 납부가 '건 하나 = 달 하나'가 되어 계산이 단순해진다.

안전 원칙
 - plan(): 읽기 전용. 어떤 건이 어느 달에 얼마씩 풀리는지, 풀리지 않는(검토 필요) 건은 무엇인지.
 - 분해는 그 건의 합계를 '그 달의 남은 청구 자리(청구액 − 다른 납부)'에 오래된 달부터 채우는 방식이라 합계가 1원도 달라지지 않는다.
 - apply(): 원본 건은 지우지 않고 VOIDED(사유 기록)로 두고 달별 건을 새로 만든다(이월 표시 is_carryover=True 유지 → 세무 집계에서 계속 이월로 취급).
   남는 금액(합계가 달들의 빈 자리보다 큼)이 있는 건은 건드리지 않고 '검토 필요'로 남긴다.
 - 실제 반영 전 검증은 scripts/split_carryover.py 가 풀 복사본에서 먼저 돌려 계약별 미납·입금 합계가 같은지 확인한다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .models import Contract, Pay
from .pool import now_iso

VOID_REASON = "이관 합산 건을 달별 납부 건으로 분해(원본 보관)"


def is_unsplit_carry(p: Pay) -> bool:
    """아직 분해하지 않은 이월 합산 원본인가(멱등키가 ':carry'로 끝남 — 이미 분해된 자식 건은 ':carry:YYYY-MM'이라 제외)."""
    return p.payment_kind == "MONTHLY_RENT" and not (p.id_contract or "").startswith("former:") and (p.idempotency_key or "").endswith(":carry")


def plan(branch: str = "") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in sorted(Pay.find(), key=lambda x: (x.branch, x.room_no, x.paid_date)):
        if branch and p.branch != branch:
            continue
        if not is_unsplit_carry(p):
            continue
        ct = Contract.get(p.id_contract)
        row: Dict[str, Any] = {"pay_id": p.id, "branch": p.branch, "room_no": p.room_no, "contract": p.id_contract, "tenant": getattr(ct, "tenant_name", ""),
                               "total": int(p.total_amount or 0), "label": p.billing_month, "paid_date": p.paid_date, "months": [], "leftover": 0, "status": "OK", "note": ""}
        if ct is None:
            row.update(status="REVIEW", note="계약을 찾을 수 없습니다")
            rows.append(row)
            continue
        end_ym = (p.billing_month or "")[:7]
        if not re.fullmatch(r"\d{4}-\d{2}", end_ym):
            row.update(status="REVIEW", note="청구월이 올바르지 않습니다")
            rows.append(row)
            continue
        others: Dict[str, int] = {}
        for q in Pay.find(id_contract=ct.id):
            if q.id != p.id and q.payment_kind == "MONTHLY_RENT":
                k = (q.billing_month or "")[:7]
                others[k] = others.get(k, 0) + int(q.total_amount or 0)
        remaining = int(p.total_amount or 0)
        for m in ct.ledger(up_to_ym=end_ym)["months"]:
            if remaining <= 0:
                break
            due = int(m.get("due") or 0)
            if due <= 0 or m["ym"] > end_ym:
                continue
            room = due - others.get(m["ym"], 0)
            if room <= 0:
                continue
            take = min(remaining, room)
            row["months"].append({"ym": m["ym"], "amount": take, "due": due})
            remaining -= take
        row["leftover"] = remaining
        if remaining > 0:
            row.update(status="REVIEW", note=f"달들의 빈 자리보다 {remaining:,}원이 많습니다(선수금 성격) — 자동으로 분해하지 않습니다")
        elif not row["months"]:
            row.update(status="REVIEW", note="채울 달이 없습니다")
        rows.append(row)
    return rows


def snapshot() -> Dict[str, Dict[str, Any]]:
    """계약별 합계(입금·미납·선수금)와 미납 달 집합 — 분해 전후 비교용."""
    out: Dict[str, Dict[str, Any]] = {}
    for ct in Contract.all():
        if ct.status != "ACTIVE":
            continue
        lg = ct.ledger()
        out[ct.id] = {"branch": ct.branch, "room_no": ct.room_no, "tenant": ct.tenant_name, "total_paid": lg["total_paid"], "total_unpaid": lg["total_unpaid"],
                      "advance_credit": lg.get("advance_credit", 0), "unpaid_months": [m["ym"] for m in lg["months"] if m["unpaid"] > 0]}
    return out


def _snap_one(ct: Contract) -> Dict[str, Any]:
    lg = ct.ledger()
    return {"total_paid": lg["total_paid"], "total_unpaid": lg["total_unpaid"], "advance_credit": lg.get("advance_credit", 0),
            "unpaid_months": [m["ym"] for m in lg["months"] if m["unpaid"] > 0]}


def apply(rows: Optional[List[Dict[str, Any]]] = None, app_source: str = "rent3_carryover_split", verify: bool = True) -> Dict[str, Any]:
    """plan() 의 OK 건만 분해한다. 원본은 무효 처리(보관), 달별 건을 새로 만든다.
    verify=True 면 건마다 그 계약의 입금·미납·선수금 합계와 미납 달 분포를 전후로 비교해, 1원이라도 다르면 그 건만 즉시 되돌린다(새 건 삭제, 원본 복구)."""
    rows = plan() if rows is None else rows
    done = skipped = created = 0
    rolled_back: List[Dict[str, Any]] = []
    for r in rows:
        if r["status"] != "OK":
            skipped += 1
            continue
        p = Pay.get(r["pay_id"])
        if p is None or not is_unsplit_carry(p):
            skipped += 1
            continue
        ct = Contract.get(p.id_contract)
        before_one = _snap_one(ct) if (verify and ct is not None) else None
        kid_ids: List[str] = []
        group = f"dep_{uuid4().hex[:8]}"
        for m in r["months"]:
            ratio = m["amount"] / p.total_amount if p.total_amount else 0
            vat = round(p.vat_amount * ratio)
            kid = Pay(branch=p.branch, id_building=p.id_building, id_room=p.id_room, room_no=p.room_no, id_contract=p.id_contract, id_tenant=p.id_tenant,
                payer_name=p.payer_name, billing_month=m["ym"], paid_date=p.paid_date, rent_amount=m["amount"] - vat - round(p.maintenance_amount * ratio),
                maintenance_amount=round(p.maintenance_amount * ratio), vat_amount=vat, total_amount=m["amount"], payment_kind="MONTHLY_RENT", method=p.method,
                cash_receipt_no=p.cash_receipt_no, is_carryover=True, allocated_months=[{"ym": m["ym"], "allocated": m["amount"], "cleared": m["amount"] >= m["due"]}],
                idempotency_key=f"{p.idempotency_key}:{m['ym']}", deposit_group_id=group, raw_sms=p.raw_sms).save(action="carryover_split_child", app_source=app_source)
            kid_ids.append(kid.id)
        p.status, p.void_reason, p.voided_at, p.voided_by = "VOIDED", VOID_REASON, now_iso(), app_source
        p.save(action="carryover_split_voided", app_source=app_source)
        if before_one is not None and _snap_one(ct) != before_one:      # 합계·미납 분포가 달라지면 이 건만 되돌린다
            for kid_id in kid_ids:
                k = Pay.get(kid_id, include_voided=True)
                if k is not None:
                    k.delete(action="carryover_split_rollback", app_source=app_source)
            p.status, p.void_reason, p.voided_at, p.voided_by = "ACTIVE", "", "", ""
            p.save(action="carryover_split_rolled_back", app_source=app_source)
            rolled_back.append({"room_no": r["room_no"], "branch": r["branch"], "pay_id": r["pay_id"]})
            continue
        created += len(kid_ids)
        done += 1
    return {"split": done, "skipped": skipped, "created": created, "rolled_back": rolled_back}


def compare(before: Dict[str, Dict[str, Any]], after: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """계약별 입금 합계·미납 합계·선수금이 1원도 다르지 않은지, 미납 달 분포가 어떻게 달라졌는지."""
    bad, moved = [], []
    for cid, b in before.items():
        a = after.get(cid)
        if a is None:
            bad.append({"contract": cid, "why": "분해 후 계약을 찾을 수 없음"})
            continue
        for k in ("total_paid", "total_unpaid", "advance_credit"):
            if a[k] != b[k]:
                bad.append({"contract": cid, "room_no": b["room_no"], "field": k, "before": b[k], "after": a[k]})
        if a["unpaid_months"] != b["unpaid_months"]:
            moved.append({"contract": cid, "room_no": b["room_no"], "tenant": b["tenant"], "before": b["unpaid_months"], "after": a["unpaid_months"]})
    return {"identical_totals": not bad, "mismatches": bad, "month_distribution_changed": moved}
