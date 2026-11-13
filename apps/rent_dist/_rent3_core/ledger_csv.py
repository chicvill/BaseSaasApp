"""
apps/rent_dist/_rent3_core/ledger_csv.py — 납부현황 엑셀(CSV) 내려받기 · 올리기.

표 모양(임대인이 쓰던 엑셀 장부와 같다):
    행 1: 제목      행 2: 호실 | 성명 | 선/후 | 1월 | (빈칸) | 2월 | (빈칸) | …     행 3: (빈칸)×3 | 월일 | 금액 | 월일 | 금액 | …
    그 다음 호실마다 한 줄: 호실, 성명, 후불/선불, (낸 날짜 'M. D', 낸 금액) × 월
 - 'N월' 칸 = 그 달분을 낸 날짜와 금액(월세+관리비 합계). 올릴 때는 '1월', '2월' … 머리글을 찾아 칸을 맞추므로 열이 좀 달라도 읽는다.
 - 올리기는 두 단계: plan()=미리보기(저장 안 함) → apply()=반영(먼저 풀 백업). 같은 파일을 두 번 올려도 중복되지 않는다.
 - 기본은 '새로 추가만': 이미 있는 같은 달 기록과 값이 다르면 '다름'으로 알려 주기만 하고, replace_diffs 를 켜야 파일 값으로 바꾼다.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .models import Building, Contract, Pay, Room, date_today, normalize_branch_id

MAX_BYTES = 1_000_000
SRC = "rent3_lessor"


def _ym_add(ym: str, n: int) -> str:
    i = int(ym[:4]) * 12 + (int(ym[5:7]) - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _num(text: str) -> int:
    d = re.sub(r"[^\d]", "", text or "")
    return int(d) if d else 0


def _dot(d: str) -> str:
    return f"{int(d[5:7])}. {int(d[8:10])}" if d else ""


# ───────────────────────── 내려받기 ─────────────────────────
def export_csv(branch: str, year: int) -> str:
    b = normalize_branch_id(branch)
    bldg = Building.first(branch=b)
    rooms = sorted(Room.find(branch=b), key=lambda r: r.room_no)
    pays: Dict[Tuple[str, str], List[Pay]] = {}
    for p in Pay.find(branch=b):
        if p.payment_kind == "MONTHLY_RENT" and p.billing_month.startswith(f"{year}-"):
            pays.setdefault((p.room_no, p.billing_month), []).append(p)
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    head = [f"{bldg.building_name if bldg else b} 임대차관리부 {year}년 (내려받은 날 {date_today().isoformat()})"]
    w.writerow(head)
    w.writerow(["호실", "성명", "선/후"] + [x for m in range(1, 13) for x in (f"{m}월", "")])
    w.writerow(["", "", ""] + ["월일", "금액"] * 12)
    totals = [0] * 12
    for r in rooms:
        ct = Contract.get(r.active_contract_id) if r.active_contract_id else None
        name = ct.tenant_name if ct else ""
        row: List[Any] = [r.room_no, name, (ct.pay_timing if ct else "")]
        for m in range(1, 13):
            ps = pays.get((r.room_no, f"{year}-{m:02d}"), [])
            if ps:
                tot = sum(p.total_amount for p in ps)
                row += [_dot(max(p.paid_date for p in ps)), f"{tot:,}"]
                totals[m - 1] += tot
            else:
                row += ["", ""]
        w.writerow(row)
    w.writerow([f"{len(rooms)}실", "합계", ""] + [x for t in totals for x in (f"{t:,}" if t else "", "")])
    return "﻿" + out.getvalue()


# ───────────────────────── 읽기 ─────────────────────────
def _cell_date(text: str, ym: str) -> Tuple[Optional[date], bool]:
    """'2. 3' '1-15' '3:16' → (날짜, 해석했는가). '1025'·'115'처럼 점이 빠진 칸은 월·일로 풀어 읽고 해석 표시를 남긴다."""
    t = (text or "").strip()
    guessed = False
    m = re.fullmatch(r"(\d{1,2})\s*[.\-:/]\s*(\d{1,2})", t)
    if not m:
        d = re.fullmatch(r"(\d{3,4})", t)
        if not d:
            return None, False
        m = re.match(r"(\d{1,2})(\d{2})$", d.group(1))
        if not m:
            return None, False
        guessed = True
    mo, dy = int(m.group(1)), int(m.group(2))
    y = int(ym[:4])
    if mo < int(ym[5:]) - 6:        # 12월 칸에 적힌 1월 날짜처럼 해를 넘긴 경우
        y += 1
    try:
        return date(y, mo, dy), guessed
    except ValueError:
        return None, False


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse(text: str, year: int) -> Dict[str, Any]:
    """CSV → 칸 목록. 읽지 못한 칸·건너뛴 줄은 issues 에 모은다."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    hdr_i, month_col = None, {}
    for i, r in enumerate(rows[:12]):
        cols = {int(m.group(1)): j for j, c in enumerate(r) if (m := re.fullmatch(r"\s*(\d{1,2})\s*월\s*", c or "")) and 1 <= int(m.group(1)) <= 12}
        if len(cols) >= 6:
            hdr_i, month_col = i, cols
            break
    if hdr_i is None:
        raise ValueError("'1월', '2월' … 머리글이 있는 줄을 찾지 못했습니다. 이 앱에서 내려받은 파일이나 같은 모양의 표를 올려 주세요.")
    cells: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    n_rows = 0
    for r in rows[hdr_i + 1:]:
        room = (r[0] if r else "").strip()
        if not re.fullmatch(r"\d{3,4}", room):
            continue
        name = (r[1] if len(r) > 1 else "").strip()
        n_rows += 1
        if "주인" in name:
            issues.append({"room_no": room, "kind": "skip", "msg": f"{room}호 '{name}' 줄은 임대 대상이 아니어서 건너뜀"})
            continue
        for m, j in sorted(month_col.items()):
            dtxt = (r[j] if len(r) > j else "").strip()
            atxt = (r[j + 1] if len(r) > j + 1 else "").strip()
            if not dtxt and not atxt:
                continue
            ym = f"{year}-{m:02d}"
            amount = _num(atxt)
            d, guessed = _cell_date(dtxt, ym)
            if d is None:
                issues.append({"room_no": room, "ym": ym, "kind": "unread", "msg": f"{room}호 {m}월 칸 '{dtxt}' {atxt} — 날짜로 읽지 못해 반영하지 않음", "raw": f"{dtxt} {atxt}".strip()})
                continue
            if amount <= 0:
                issues.append({"room_no": room, "ym": ym, "kind": "unread", "msg": f"{room}호 {m}월 칸 '{dtxt}' — 금액이 없어 반영하지 않음", "raw": dtxt})
                continue
            cells.append({"room_no": room, "name": name, "ym": ym, "date": d, "amount": amount, "raw_date": dtxt, "guessed": guessed})
    return {"cells": cells, "issues": issues, "rows": n_rows, "months": sorted(month_col)}


# ───────────────────────── 미리보기 · 반영 ─────────────────────────
def _split(contract: Contract, total: int) -> Tuple[int, int]:
    base = contract.monthly_rent + contract.maintenance_fee
    fee = round(total * contract.maintenance_fee / base) if base and contract.maintenance_fee else 0
    return total - fee, fee


def plan(branch: str, year: int, text: str) -> Dict[str, Any]:
    b = normalize_branch_id(branch)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("파일이 너무 큽니다(1MB 이하).")
    parsed = parse(text, year)
    today = date_today()
    existing: Dict[Tuple[str, str], List[Pay]] = {}
    for p in Pay.find(branch=b):
        if p.payment_kind == "MONTHLY_RENT":
            existing.setdefault((p.room_no, p.billing_month), []).append(p)
    items: List[Dict[str, Any]] = []
    for c in parsed["cells"]:
        item = {"room_no": c["room_no"], "name": c["name"], "ym": c["ym"], "date": c["date"].isoformat(), "amount": c["amount"], "raw_date": c["raw_date"], "notes": []}
        room = Room.first(branch=b, room_no=c["room_no"])
        ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
        if c["guessed"]:
            item["notes"].append(f"'{c['raw_date']}'를 {c['date'].month}월 {c['date'].day}일로 읽음")
        if room is None:
            item.update(status="skip", reason="앱에 없는 호실")
        elif ct is None or ct.status != "ACTIVE":
            item.update(status="skip", reason="입주 계약이 없는 호실")
        elif c["date"] > today:
            item.update(status="skip", reason="오늘 이후 날짜")
        elif c["ym"] < ct.move_in_date[:7]:
            item.update(status="skip", reason="입주 전 달")
        else:
            if ct.tenant_name and c["name"] and ct.tenant_name != c["name"]:
                item["notes"].append(f"파일의 이름({c['name']})이 앱의 입주자({ct.tenant_name})와 다름")
            due = ct.monthly_rent + ct.maintenance_fee
            if due and c["amount"] != due:
                item["notes"].append(f"월 청구액({due:,}원)과 금액이 다름")
            lag = (c["date"].year * 12 + c["date"].month) - (int(c["ym"][:4]) * 12 + int(c["ym"][5:]))
            if lag > 3:
                item["notes"].append("낸 날짜가 청구월보다 3개월 넘게 늦음(몰아서 낸 것으로 보임)")
            ex = existing.get((c["room_no"], c["ym"]), [])
            if not ex:
                item["status"] = "new"
            else:
                tot = sum(p.total_amount for p in ex)
                item["existing"] = {"date": max(p.paid_date for p in ex), "amount": tot, "count": len(ex)}
                same = tot == c["amount"] and any(p.paid_date == item["date"] for p in ex)
                item["status"] = "same" if same else "diff"
        items.append(item)
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ("new", "same", "diff", "skip")}
    return {"success": True, "year": year, "rows": parsed["rows"], "items": items, "issues": parsed["issues"], "counts": counts,
            "notes_count": sum(1 for i in items if i["notes"])}


def apply(branch: str, year: int, text: str, replace_diffs: bool = False) -> Dict[str, Any]:
    from . import backup as bk
    p = plan(branch, year, text)
    b = normalize_branch_id(branch)
    todo = [i for i in p["items"] if i["status"] == "new" or (i["status"] == "diff" and replace_diffs)]
    if not todo:
        return {"success": True, "added": 0, "replaced": 0, "message": "새로 반영할 내용이 없습니다.", "plan": p["counts"], "backup": ""}
    try:
        backup_name = bk.create()["name"]       # 반영 전 안전 백업(실패해도 반영은 막지 않되 알린다)
    except Exception:  # noqa: BLE001
        backup_name = ""
    bldg = Building.first(branch=b)
    added = replaced = 0
    for i in todo:
        room = Room.first(branch=b, room_no=i["room_no"])
        ct = Contract.get(room.active_contract_id)
        rent, fee = _split(ct, i["amount"])
        if i["status"] == "new":
            Pay(branch=b, id_building=bldg.id if bldg else "", id_room=room.id, room_no=room.room_no, id_contract=ct.id, id_tenant=ct.id_tenant,
                payer_name=ct.tenant_name, billing_month=i["ym"], paid_date=i["date"], rent_amount=rent, maintenance_amount=fee,
                total_amount=i["amount"], payment_kind="MONTHLY_RENT", method="TRANSFER", cash_receipt_no="엑셀 올리기",
                idempotency_key=f"csv:{b}:{i['room_no']}:{i['ym']}:{i['date']}:{i['amount']}", raw_sms="납부현황 엑셀(CSV) 올리기").save(action="payment_imported_csv", app_source=SRC)
            added += 1
        else:
            ex = [x for x in Pay.find(branch=b, room_no=i["room_no"]) if x.payment_kind == "MONTHLY_RENT" and x.billing_month == i["ym"]]
            if len(ex) != 1:
                continue                       # 같은 달에 여러 건이면 어느 것을 바꿀지 알 수 없어 건드리지 않는다
            x = ex[0]
            x.paid_date, x.rent_amount, x.maintenance_amount = i["date"], rent, fee
            x.total_amount = i["amount"] + x.vat_amount
            x.save(action="payment_edited_csv", app_source=SRC)
            replaced += 1
    return {"success": True, "added": added, "replaced": replaced, "plan": p["counts"], "backup": backup_name,
            "message": f"새로 {added}건을 기록하고 {replaced}건을 고쳤습니다." + (f" (반영 전 백업: {backup_name})" if backup_name else " (백업은 만들지 못했습니다)")}
