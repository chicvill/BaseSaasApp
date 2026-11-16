"""
scripts/ledger_import.py — 종이 장부(수납부) CSV 를 입주 세입자·계약·월별 수납 기록으로 옮긴다 (fresh_pool.py 가 사용).

CSV 형식(두 가지):
  sunho 형식: 호실,성명,전화번호,입주일,보증금(만원),월세(만원),1월 납부..11월 납부   (칸의 값 = 낸 날짜 'M/D')
  sinbi 형식: 호실,성명,입주일,보증금(만원),월세(만원),관리비(만원),입금액,전화,타명/연락처 메모,미수,납부일,비고(키)

처리 규칙 (사용자 확정):
  - 계약 만기 = 입주일 + 1년, 자동 1년 연장으로 보아 '오늘 이후 가장 가까운 만기일'.
  - 2026-01 이전 입금 내역은 장부에 없어 '이월 입금' 한 건으로 묶는다(그 시점까지 낼 돈을 낸 것으로 본다).
  - sunho: 1~11월 칸의 날짜를 월별 수납으로 만든다. 날짜가 아니거나(////, 메모확인, 없는 날짜) 비어 있으면 미납으로 둔다.
  - sinbi: 장부는 10월분 한 달뿐. 미수(만원)가 있으면 그만큼(미수 ÷ 월 청구액)개월이 미납, 없으면 '납부일'에 10월분을 낸 것으로 본다.
  - overrides: {"호실": {"deposit": 만원, "settle_through": "2026-10"}} 로 장부 정정/완납 처리.
세입자 이름·전화번호는 저장하고(전화번호·도어락은 서버 암호화), 비밀번호는 아무도 모르는 무작위 해시로 둔다(본인 확인·재설정으로만 로그인).
"""
import csv
import re
import secrets
from datetime import date

from apps.rent_dist._rent3_core import Contract, Pay, Room, Tenant
from apps.rent_dist._rent3_core import passwords as pwd
from apps.rent_dist._rent3_core.models import date_today

SRC = "ledger_import"
WON = 10_000


def _phones(text: str):
    out = []
    for m in re.finditer(r"0?1[016-9][-\s]?\d{3,4}[-\s]?\d{4}|\b\d{4}-\d{4}\b", text or ""):
        d = re.sub(r"\D", "", m.group())
        if len(d) == 8:
            d = "010" + d
        elif len(d) == 10 and not d.startswith("0"):
            d = "0" + d
        out.append(f"{d[:3]}-{d[3:7]}-{d[7:]}")
    return out


def _md(value: str, year: int = 2026):
    """'M/D' → date, 날짜가 아니면 None."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})\s*", value or "")
    if not m:
        return None
    try:
        return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _ym_add(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    i = y * 12 + (m - 1) + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _expiry(move_in: date, today: date) -> date:
    n = 1
    while True:
        try:
            e = move_in.replace(year=move_in.year + n)
        except ValueError:
            e = move_in.replace(year=move_in.year + n, day=28)
        if e > today:
            return e
        n += 1


def _read(spec: dict, base_dir: str):
    import os
    path = spec["csv"] if os.path.isabs(spec["csv"]) else os.path.join(base_dir, spec["csv"])
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f)) if spec.get("format") == "chic" else list(csv.DictReader(f))


def _num(text: str) -> int:
    d = re.sub(r"[^\d]", "", text or "")
    return int(d) if d else 0


def _cell_date(text: str, col_ym: str):
    """'2. 3' '1-15' '3:16' '1015' '115' → 낸 날짜. 청구월 칸보다 앞선 달이면 다음 해로 본다. 날짜가 아니면 None."""
    t = (text or "").strip()
    m = re.fullmatch(r"(\d{1,2})\s*[.\-:/]\s*(\d{1,2})", t)
    if not m:
        d = re.fullmatch(r"(\d{3,4})", t)       # 점이 빠진 입력: 115 → 1/15, 1015 → 10/15
        if not d:
            return None
        g = d.group(1)
        m = re.match(r"(\d{1,2})(\d{2})$", g)
        if not m:
            return None
    mo, dy = int(m.group(1)), int(m.group(2))
    y = int(col_ym[:4])
    if mo < int(col_ym[5:]):
        y += 1
    try:
        return date(y, mo, dy)
    except ValueError:
        return None


def _parse_ymd(text: str):
    m = re.search(r"(\d{4})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})", text or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _parse_chic(rows: list, overrides: dict, today: date) -> list:
    """시크빌 장부: 위쪽 2025년, 아래쪽 2026년 두 덩어리. 호실당 현재 세입자 한 줄.
    - 칸에 '공실' 은 비어 있던 달, '전입M/D' 는 새 세입자의 입주일(그 앞 달 입금은 이전 세입자의 것이라 버린다).
    - 계약일 = 전입일 칸(갱신일일 수 있다), 만기 = 계약일+1년 자동 연장. 입주(청구 시작)일 = 마지막 '전입' 표시, 없으면 min(계약일, 2025-01-01).
    - 월별 입금은 칸의 날짜·임차료·관리비 그대로 쓰고, 칸이 비었거나 '미납' 이면 미납으로 둔다."""
    blocks = {}
    year = None
    for r in rows:
        head = (r[0] if r else "").strip()
        if re.fullmatch(r"20\d\d\s*년도?", head):
            year = int(head[:4])
            continue
        if year and re.fullmatch(r"\d{3}", head) and len(r) > 9:
            blocks.setdefault(head, {})[year] = r
    out = []
    for no, by_year in blocks.items():
        last = by_year.get(2026) or by_year.get(2025)
        if not (last[1] or "").strip() or "주인" in last[1]:
            continue                       # 701 주인세대는 임대 대상이 아니다
        ov = overrides.get(no, {})
        cdate = _parse_ymd(last[4])
        cells = []                         # (ym, 날짜칸, 임차료, 관리비)
        for y in (2025, 2026):
            r = by_year.get(y)
            if not r:
                continue
            for k in range(12):
                i = 9 + 3 * k
                if i >= len(r):
                    break
                ym = f"{y}-{k + 1:02d}"
                if y == 2026 and k >= 8 and False:
                    pass
                cells.append((ym, (r[i] or "").strip(), _num(r[i + 1]) if i + 1 < len(r) else 0, _num(r[i + 2]) if i + 2 < len(r) else 0))
        marker = None
        for ym, txt, _, _ in cells:
            m = re.search(r"전입\s*(\d{1,2})\s*/\s*(\d{1,2})|(\d{1,2})\s*/\s*(\d{1,2})\s*전입", txt)
            if m:
                mo, dy = [int(x) for x in m.groups() if x]
                marker = date(int(ym[:4]), mo, dy)
        if marker:
            move_in = cdate if (cdate and (cdate.year, cdate.month) == (marker.year, marker.month)) else marker
        else:
            move_in = min(cdate, date(2025, 1, 1)) if cdate else date(2025, 1, 1)
        start_ym = move_in.strftime("%Y-%m")
        pays, notes = [], []
        dated = []
        for ym, txt, rent, fee in cells:
            if ym >= start_ym and txt and not any(w in txt for w in ("공실", "전입", "미납")):
                dated.append((ym, txt, rent, fee, _cell_date(txt, ym)))
        by_ym = {ym0: d for ym0, _, _, _, d in dated}   # 몰아서 늦게 낸 입금: 이웃한 달 칸과 같은 날짜
        for ym, txt, rent, fee, d in dated:
            col_end = date(int(ym[:4]), int(ym[5:]), 28)
            lag = (d.year * 12 + d.month) - (int(ym[:4]) * 12 + int(ym[5:])) if d else 0
            if d is None or d > today or (lag > 3 and by_ym.get(_ym_add(ym, -1)) != d and by_ym.get(_ym_add(ym, 1)) != d):
                # 칸에 임차료·관리비 금액이 있으면 낸 것으로 본다. 날짜만 믿을 수 없어 그 달 말로 두고 확인 목록에 올린다
                if rent + fee > 0:
                    pays.append((ym, min(col_end, today), rent, fee))
                    notes.append(f"{ym} 칸 '{txt}' 날짜가 이상해 입금으로 처리(날짜는 그 달 말로 둠)")
                else:
                    notes.append(f"{ym} 칸 '{txt}' 금액이 없어 미납으로 둠")
                continue
            pays.append((ym, d, rent, fee))
        # 현재 월세·관리비: 가장 최근 입금 칸, 없으면 가장 최근에 금액이 적힌 칸(새로 입주해 아직 입금 전인 호실)
        amounts = [(rt, fe) for _, _, rt, fe in cells if rt + fe > 0]
        cur = (next(((rt, fe) for _, _, rt, fe in reversed(pays) if rt + fe > 0), None)
               or (amounts[-1] if amounts else (0, 0)))
        if "전세" in last[6]:
            cur = (0, cur[1])
        out.append({
            "room_no": no, "name": last[1].strip(), "phones": _phones(last[3]), "memo": "", "door": "",
            "move_in": move_in, "contract_date": cdate or move_in,
            "deposit": int(ov["deposit"]) * WON if "deposit" in ov else _num(last[8]),
            "rent": cur[0], "fee": cur[1], "timing": "후불", "lease_type": "전세" if "전세" in last[6] else "월세",
            "pays": pays, "notes": notes, "overrides": ov, "unpaid_n": None, "months": {},
        })
    return out



def _normalize(fmt: str, rows: list, overrides: dict) -> list:
    out = []
    for r in rows:
        no = str(r["호실"]).strip()
        ov = overrides.get(no, {})
        rec = {"room_no": no, "name": r["성명"].strip(), "move_in": date.fromisoformat(r["입주일"].strip()),
               "deposit": int(ov.get("deposit", r["보증금(만원)"])) * WON, "overrides": ov}
        if fmt == "sunho":
            rec.update(rent=int(r["월세(만원)"]) * WON, fee=0, phones=_phones(r["전화번호"]), memo="", door="",
                       timing="후불", months={})
            for mth in range(1, 12):
                raw = (r.get(f"{mth}월 납부") or "").strip()
                d = _md(raw)
                if d:
                    rec["months"][f"2026-{mth:02d}"] = d
            first = next(iter(sorted(rec["months"].items())), None)
            if first:   # 낸 달이 청구월과 같으면 선불, 다음 달이면 후불
                rec["timing"] = "선불" if first[1].month == int(first[0][5:]) else "후불"
            rec["unpaid_n"] = None
        else:
            timing, rent = r["월세(만원)"].split()
            rec.update(rent=int(rent) * WON, fee=int(r["관리비(만원)"]) * WON, timing=timing,
                       phones=_phones(r["전화"]), door=(r["비고(키)"] or "").strip(), months={},
                       memo=" / ".join(x for x in [(r["전화"] or "").strip(), (r["타명/연락처 메모"] or "").strip()] if x))
            n = 0
            if (r["미수"] or "").strip():
                n = round(int(r["미수"]) * WON / (rec["rent"] + rec["fee"]))
            if "unpaid_months" in ov:
                n = int(ov["unpaid_months"])
            rec["unpaid_n"] = n
            day = (r["납부일"] or "").strip()
            if n == 0 and day.isdigit():
                rec["months"]["2026-10"] = date(2026, 10, min(int(day), 31))
        out.append(rec)
    return out


def import_ledger(lessor, bldg, spec: dict, base_dir: str) -> dict:
    fmt = spec["format"]
    today = date_today()
    raw = _read(spec, base_dir)
    recs = _parse_chic(raw, spec.get("overrides") or {}, today) if fmt == "chic" else _normalize(fmt, raw, spec.get("overrides") or {})
    n_t = n_p = 0
    notes_all = {}
    for rec in recs:
        no, branch = rec["room_no"], bldg.branch
        room = Room.first(branch=branch, room_no=no)
        if room is None:
            room = Room(id=f"room_{branch}_{no}", branch=branch, id_building=bldg.id, room_no=no, name=f"{no}호",
                        floor=int(no[:-2] or 1), room_category="주거용 원룸")
        tid = f"tenant_{branch}_{no}"
        cid = f"ct_{branch}_{no}"
        phone = rec["phones"][0] if rec["phones"] else ""
        extra = [p for p in rec["phones"][1:]]
        memo = rec["memo"] or ("추가 연락처: " + ", ".join(extra) if extra else "")
        Tenant(id=tid, name=rec["name"], phone=phone, doorlock_pwd=rec["door"], branch=branch, id_building=bldg.id,
               current_room_id=room.id, room_no=no, checked_in=True, checked_in_at=rec["move_in"].isoformat() + "T00:00:00",
               checkin_memo=memo[:300], status="ACTIVE",
               password=pwd.hash_password(secrets.token_urlsafe(18))).save("seeded", app_source=SRC)
        ct = Contract(
            id=cid, branch=branch, id_building=bldg.id, id_room=room.id, room_no=no, id_lessor=lessor.id,
            lessor_name=lessor.name, id_tenant=tid, tenant_name=rec["name"], tenant_phone=phone,
            contract_date=rec.get("contract_date", rec["move_in"]).isoformat(), move_in_date=rec["move_in"].isoformat(),
            expiry_date=_expiry(rec.get("contract_date", rec["move_in"]), today).isoformat(), lease_term_months=12,
            lease_type=rec.get("lease_type", "월세"),
            pay_timing=rec["timing"], vat_target=False, contract_deposit=0, deposit=rec["deposit"],
            monthly_rent=rec["rent"], maintenance_fee=rec["fee"],
            special_terms="종이 장부에서 옮긴 계약입니다(원본 계약서는 별도 보관). 만기는 입주일 기준 1년 자동 연장으로 계산했습니다.",
            status="ACTIVE",
        ).save("seeded", app_source=SRC)
        room.id_tenant, room.active_contract_id = tid, cid
        room.deposit, room.monthly_rent, room.maintenance_fee, room.pay_timing = rec["deposit"], rec["rent"], rec["fee"], rec["timing"]
        room.doorlock_pwd, room.is_published = rec["door"], False
        room.save("seeded", app_source=SRC)
        n_t += 1

        def due(ym):
            return ct.due_for_month(ym)["due"]

        def add_pay(ym, total, when, key, label="", fee_amt=None):
            nonlocal n_p
            if total <= 0:
                return
            fee_part = fee_amt if fee_amt is not None else (round(total * rec["fee"] / (rec["rent"] + rec["fee"])) if rec["fee"] else 0)
            Pay(branch=branch, id_building=bldg.id, id_room=room.id, room_no=no, id_contract=cid, id_tenant=tid,
                payer_name=rec["name"], billing_month=ym, paid_date=when.isoformat(),
                rent_amount=total - fee_part, maintenance_amount=fee_part,
                total_amount=total, payment_kind="MONTHLY_RENT", method="TRANSFER", cash_receipt_no="장부이관",
                idempotency_key=f"import:{branch}:{no}:{key}", raw_sms=label).save("seeded", app_source=SRC)
            n_p += 1

        start = rec["move_in"].strftime("%Y-%m")
        settle = rec["overrides"].get("settle_through")
        # 이월 입금: 장부에 없는 과거 입금 — 어디까지 낸 것으로 볼지
        # 이월 입금의 날짜는 2025-12-31(입주가 2026년이면 입주일)로 둔다 — 올해 '연간 수납액'에 과거 누적이 섞이지 않게
        carry_when = max(date(2025, 12, 31), rec["move_in"])
        if fmt == "chic":
            carry_when = max(date(2024, 12, 31), rec["move_in"])
        if settle:
            carry_end = settle
        elif fmt == "chic":
            carry_end = "2024-12"
        elif fmt == "sunho":
            carry_end = "2025-12"
        else:
            carry_end = _ym_add("2026-09" if not rec["unpaid_n"] else "2026-10", -(rec["unpaid_n"] or 0))
        total = 0
        ym = start
        while ym <= carry_end:
            total += due(ym)
            ym = _ym_add(ym, 1)
        add_pay(carry_end, total, carry_when, "carry", "장부 이전 입금 합산(날짜 미상)")
        if fmt == "chic":
            for ym, d, rt, fe in rec["pays"]:
                add_pay(ym, (rt + fe) or due(ym), d, ym, "", fee_amt=fe)
            if rec["notes"]:
                notes_all[no] = rec["notes"]
        elif not settle:
            for ym, d in sorted(rec["months"].items()):
                if ym >= start and due(ym) > 0:
                    add_pay(ym, due(ym), d, ym)
    return {"tenants": n_t, "payments": n_p, "notes": notes_all}
