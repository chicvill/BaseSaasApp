"""
apps/rent_dist/_rent3_core/dupcheck.py — Data Pool 중복 점검(읽기 전용)과 안전한 정리.

점검 항목
 1) 계약·청구월·납부일·금액·종류가 모두 같은 납부 — 엑셀·장부 가져오기·시드를 두 번 돌렸거나 같은 입금을 두 번 기록한 흔적(자동 정리 대상).
 2) 멱등키만 같고 내용이 다른 납부 — 알리기만 한다.
 3) 같은 계약·청구월에 월세 납부가 둘 이상 — 나눠 낸 정상 납부일 수 있어 '참고'로만 알린다(자동 정리 대상 아님).
 4) 같은 지점·호실의 호실·계약·임차인이 둘 이상.
정리(remove)는 1)처럼 **내용이 완전히 같은** 납부만, 가장 먼저 만들어진 한 건을 남기고 지운다. 지우기 전에 풀 전체를 자동 백업한다.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from . import backup as bk
from .models import Contract, Room, Tenant
from .models import Pay


def _same_content(p: Pay) -> tuple:
    return (p.id_contract, p.billing_month, p.paid_date, int(p.total_amount or 0), p.payment_kind, p.room_no)


def scan() -> Dict[str, Any]:
    pays = sorted(Pay.all(), key=lambda p: (p.created_at or "", p.id))
    by_key: Dict[str, List[Pay]] = defaultdict(list)
    by_content: Dict[tuple, List[Pay]] = defaultdict(list)
    by_month: Dict[tuple, List[Pay]] = defaultdict(list)
    for p in pays:
        if p.idempotency_key:
            by_key[p.idempotency_key].append(p)
        by_content[_same_content(p)].append(p)
        if p.payment_kind == "MONTHLY_RENT":
            by_month[(p.id_contract, p.billing_month)].append(p)

    def row(p: Pay) -> Dict[str, Any]:
        return {"id": p.id, "room_no": p.room_no, "billing_month": p.billing_month, "paid_date": p.paid_date, "amount": int(p.total_amount or 0),
                "created_at": p.created_at, "idempotency_key": p.idempotency_key, "branch": p.branch}

    # 정리 대상은 '내용이 완전히 같은' 납부(계약·청구월·납부일·금액·종류·호실)뿐이다. 멱등키만 같고 내용이 다르면 알리기만 한다.
    exact_groups = [{"keep": row(g[0]), "duplicates": [row(x) for x in g[1:]]} for g in by_content.values() if len(g) > 1]
    removable_ids = {x["id"] for gr in exact_groups for x in gr["duplicates"]}
    exact_ids = {x["id"] for gr in exact_groups for x in [gr["keep"], *gr["duplicates"]]}
    key_conflicts = [{"idempotency_key": k, "payments": [row(x) for x in g]} for k, g in by_key.items()
                     if len(g) > 1 and not all(x.id in exact_ids for x in g)]
    multi = [{"contract": cid, "billing_month": ym, "payments": [row(x) for x in g]} for (cid, ym), g in by_month.items()
             if len(g) > 1 and not all(x.id in exact_ids for x in g)]

    def dup_keys(objs, keyf):
        c: Dict[Any, List[Any]] = defaultdict(list)
        for o in objs:
            c[keyf(o)].append(o.id)
        return [{"key": list(k), "ids": v} for k, v in c.items() if len(v) > 1]

    return {
        "payments_total": len(pays),
        "exact_duplicate_groups": exact_groups,
        "removable_count": len(removable_ids),
        "same_month_multiple": multi,
        "key_conflicts": key_conflicts,
        "duplicate_rooms": dup_keys(Room.all(), lambda r: (r.branch, r.room_no)),
        "duplicate_contracts": dup_keys([c for c in Contract.all() if c.status == "ACTIVE"], lambda c: (c.branch, c.room_no)),
        "duplicate_tenants": dup_keys([t for t in Tenant.all() if t.status == "ACTIVE" and t.room_no], lambda t: (t.branch, t.room_no, t.name)),
    }


def remove_exact(apply: bool = False) -> Dict[str, Any]:
    """내용이 완전히 같은 중복 납부를 정리한다(apply=False 면 점검만). 가장 먼저 만든 한 건을 남긴다."""
    rep = scan()
    ids = [x["id"] for g in rep["exact_duplicate_groups"] for x in g["duplicates"]]
    ids = sorted(set(ids))
    if not apply or not ids:
        return {"applied": False, "would_remove": len(ids), "ids": ids, "backup": ""}
    info = bk.create()
    n = 0
    for pid in ids:
        p = Pay.get(pid)
        if p is not None:
            p.delete(action="duplicate_payment_removed", app_source="rent3_dupcheck")
            n += 1
    return {"applied": True, "removed": n, "ids": ids, "backup": info.get("name", "")}
