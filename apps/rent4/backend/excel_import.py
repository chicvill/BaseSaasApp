"""
apps/rent4/backend/excel_import.py — 연간 수납 Excel 시트(복사·붙여넣기 TSV) 분석기.

시트 형식 (시크빌 2025/2026 수납부):
    1행  : "2025년도"
    2행  : 호실 | 세입자명 | 주민번호 | 전화번호 | 전입일 | 만기일 | 전/월 | 선/후 | 보증금 | 2025.01 | | | 2025.02 ...
    3행  : (월별) 월일 | 임차료 | 관리비
    데이터: 호실별 1행, 월마다 [입금 월일, 임차료, 관리비] 3칸
    끝 2행: 월별 임차료/관리비 합계, 월별 총합계 → 분석 결과 검증에 사용

손으로 적은 수납부의 흔한 표기 흔들림을 흡수한다:
    "2. 3" "3-26" "3:16" "11.25" "1015"(10/15) "115"(1/15) "공실" "미납" "전입6/3" "전출3/2"
주민번호 칸은 읽지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .models import MonthMark, Payment, Room, ym_add

INFO_COLS = 9          # 호실 ~ 보증금
ROOM_RE = re.compile(r"^\d{3,4}$")
MONEY_RE = re.compile(r"^-?[\d,]+$")


@dataclass
class SheetResult:
    year: int
    months: List[str]
    rooms: Dict[str, Room] = field(default_factory=dict)
    payments: List[Payment] = field(default_factory=list)
    marks: List[MonthMark] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    totals_check: List[Dict] = field(default_factory=list)

    def summary(self) -> Dict:
        return {
            "year": self.year,
            "months": self.months,
            "rooms": len(self.rooms),
            "payments": len(self.payments),
            "marks": len(self.marks),
            "flagged": sum(1 for p in self.payments if p.flags),
            "warnings": self.warnings,
            "totals_check": self.totals_check,
        }


def money(text: str) -> Optional[int]:
    t = (text or "").strip()
    if not t or not MONEY_RE.match(t):
        return None
    return int(t.replace(",", ""))


def parse_full_date(text: str) -> Optional[str]:
    """'2025. 6. 4' → '2025-06-04'"""
    m = re.match(r"^\s*(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", text or "")
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def parse_month_day(text: str) -> Optional[Tuple[int, int]]:
    """입금 '월일' 칸 → (월, 일). 해석 불가면 None."""
    t = (text or "").strip()
    m = re.match(r"^(\d{1,2})\s*[.\-:/]\s*(\d{1,2})$", t)
    if m:
        mo, d = int(m[1]), int(m[2])
    elif re.match(r"^\d{3,4}$", t):        # "1015" → 10/15, "115" → 1/15
        mo, d = int(t[:-2]), int(t[-2:])
    else:
        return None
    return (mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else None


def resolve_paid_date(ym: str, md: Tuple[int, int]) -> Optional[date]:
    """귀속월과 입금 월일로 입금 연도를 추정한다 (12월분을 1월에 내면 이듬해)."""
    y, ym_month = int(ym[:4]), int(ym[5:7])
    mo, d = md
    if ym_month >= 11 and mo <= 2:
        y += 1
    elif ym_month <= 2 and mo >= 11:
        y -= 1
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _month_labels(header: List[str]) -> Tuple[int, int]:
    """헤더의 첫 월 라벨('2026.01')에서 (연, 월) 을 얻는다. 이후 월은 순서대로 증가한다고 본다."""
    for cell in header[INFO_COLS:]:
        m = re.match(r"^\s*(\d{4})\.(\d{1,2})", cell or "")
        if m:
            return int(m[1]), int(m[2])
    raise ValueError("헤더에서 '2025.01' 형식의 월 라벨을 찾지 못했습니다.")


def parse_sheet(text: str, building: str = "시크빌", branch: str = "main") -> SheetResult:
    lines = [ln.rstrip("\r") for ln in (text or "").split("\n")]
    rows = [ln.split("\t") for ln in lines if ln.strip()]
    header_idx = next((i for i, r in enumerate(rows) if r and r[0].strip() == "호실"), None)
    if header_idx is None:
        raise ValueError("'호실' 로 시작하는 헤더 행을 찾지 못했습니다. Excel 표 전체(제목·헤더 포함)를 복사해 주세요.")

    header = rows[header_idx]
    y0, m0 = _month_labels(header)
    n_months = max(0, (len(header) - INFO_COLS + 2) // 3)
    first_ym = f"{y0:04d}-{m0:02d}"
    months = [ym_add(first_ym, i) for i in range(n_months)]
    result = SheetResult(year=y0, months=months)

    # 헤더 라벨과 순서 추정이 다르면 알려 준다 (예: 2026 시트의 '2030.09')
    for i, ym in enumerate(months):
        col = INFO_COLS + 3 * i
        label = header[col].strip() if col < len(header) else ""
        m = re.match(r"^(\d{4})\.(\d{1,2})$", label)
        if label and m and f"{int(m[1]):04d}-{int(m[2]):02d}" != ym:
            result.warnings.append(f"헤더 '{label}' 은 순서상 {ym} 로 해석했습니다.")

    total_rows: List[List[str]] = []
    for r in rows[header_idx + 1:]:
        r = r + [""] * (INFO_COLS + 3 * n_months - len(r))
        key = r[0].strip()
        if not key:
            if any(money(c) for c in r[INFO_COLS:]):
                total_rows.append(r)
            continue
        if not ROOM_RE.match(key):
            continue
        _parse_room_row(result, r, key, building, branch)

    _check_totals(result, total_rows)
    return result


def _parse_room_row(result: SheetResult, r: List[str], room_no: str, building: str, branch: str) -> None:
    name = r[1].strip()
    if "주인" in name:
        result.rooms[room_no] = Room(room=room_no, building=building, branch=branch, status="owner", tenant_name=name)
        return

    room = Room(
        room=room_no, building=building, branch=branch, status="occupied" if name else "vacant",
        tenant_name=name, phone=r[3].strip(),                 # r[2] = 주민번호 → 읽지 않음
        move_in=parse_full_date(r[4]), expire=parse_full_date(r[5]),
        lease_type=r[6].strip() or "월세", pay_timing=r[7].strip() or "후불",
        deposit=money(r[8]) or 0,
    )

    for i, ym in enumerate(result.months):
        c = INFO_COLS + 3 * i
        d_raw, rent, mgmt = r[c].strip(), money(r[c + 1]), money(r[c + 2])
        _parse_cell(result, room, ym, d_raw, rent, mgmt)

    last = [p for p in result.payments if p.room == room_no and p.amount > 0]
    if last:
        room.rent, room.mgmt = last[-1].rent, last[-1].mgmt
    result.rooms[room_no] = room


def _parse_cell(result: SheetResult, room: Room, ym: str, d_raw: str,
                rent: Optional[int], mgmt: Optional[int]) -> None:
    for word, kind in (("공실", "vacant"), ("미납", "unpaid"), ("전입", "move_in"), ("전출", "move_out")):
        if word in d_raw:
            result.marks.append(MonthMark(room=room.room, ym=ym, kind=kind, raw=d_raw))
            if (rent or 0) + (mgmt or 0) > 0:      # 예: 전출월에 관리비만 정산
                result.payments.append(Payment(room=room.room, ym=ym, paid_date_raw=d_raw,
                                               rent=rent or 0, mgmt=mgmt or 0,
                                               flags=[f"'{d_raw}' 표기와 함께 입금액이 있어 입금일 미상"]))
            return

    if rent is None and mgmt is None:
        if d_raw:
            result.warnings.append(f"{room.room}호 {ym}: '{d_raw}' 를 해석하지 못해 건너뜁니다.")
        return

    flags: List[str] = []
    paid: Optional[date] = None
    md = parse_month_day(d_raw)
    if md:
        paid = resolve_paid_date(ym, md)
        if paid is None:
            flags.append(f"입금일 '{d_raw}' 이 달력에 없는 날짜")
        elif paid < date(int(ym[:4]), int(ym[5:7]), 1) - timedelta(days=31) and room.pay_timing == "후불":
            flags.append(f"입금일 '{d_raw}' 이 귀속월보다 2개월 이상 앞섬 — 오기 의심")
    elif money(d_raw) is not None:
        flags.append(f"입금일 칸에 금액 '{d_raw}' 이 입력됨 — 칸 밀림 의심")
    elif d_raw:
        flags.append(f"입금일 '{d_raw}' 해석 불가")
    else:
        flags.append("입금일 비어 있음")

    result.payments.append(Payment(
        room=room.room, ym=ym, paid_date=paid.isoformat() if paid else None, paid_date_raw=d_raw,
        rent=rent or 0, mgmt=mgmt or 0, flags=flags,
    ))


def _check_totals(result: SheetResult, total_rows: List[List[str]]) -> None:
    """시트 하단 합계 행과 분석 결과 합계를 월별로 대조한다."""
    if not total_rows:
        return
    sums = total_rows[0]
    for i, ym in enumerate(result.months):
        c = INFO_COLS + 3 * i
        sheet_rent, sheet_mgmt = money(sums[c + 1]), money(sums[c + 2])
        if sheet_rent is None and sheet_mgmt is None:
            continue
        got_rent = sum(p.rent for p in result.payments if p.ym == ym)
        got_mgmt = sum(p.mgmt for p in result.payments if p.ym == ym)
        ok = (sheet_rent or 0) == got_rent and (sheet_mgmt or 0) == got_mgmt
        result.totals_check.append({
            "ym": ym, "ok": ok,
            "sheet_rent": sheet_rent or 0, "sheet_mgmt": sheet_mgmt or 0,
            "parsed_rent": got_rent, "parsed_mgmt": got_mgmt,
        })
        if not ok:
            result.warnings.append(
                f"{ym} 합계 불일치: 시트 임차료 {sheet_rent or 0:,} / 관리비 {sheet_mgmt or 0:,}, "
                f"분석 임차료 {got_rent:,} / 관리비 {got_mgmt:,}")


def apply_sheet(result: SheetResult) -> Dict:
    """분석 결과를 Knowledge Pool 에 반영한다.

    - 호실 정보: 시트 값으로 갱신 (나중에 가져온 시트가 최신)
    - 해당 연월의 Excel 출처 수납·표기는 교체, 은행 문자/수동 입력은 보존
    - 같은 호실·연월에 은행/수동 기록이 이미 있으면 Excel 행은 건너뛴다
    """
    months = set(result.months)
    rooms = {r.room: r for r in Room.all()}
    for no, new in result.rooms.items():
        if no in rooms:
            new.id, new.created_at = rooms[no].id, rooms[no].created_at
        rooms[no] = new
    Room.replace_all(sorted(rooms.values(), key=lambda r: r.room))

    kept, skipped = [], 0
    for p in Payment.all():
        if p.source == "excel" and p.ym in months and p.room in result.rooms:
            continue
        kept.append(p)
    other = {(p.room, p.ym) for p in kept if p.source != "excel"}
    for p in result.payments:
        if (p.room, p.ym) in other:
            skipped += 1
            continue
        kept.append(p)
    Payment.replace_all(sorted(kept, key=lambda p: (p.room, p.ym, p.paid_date or "")))

    marks = [m for m in MonthMark.all() if not (m.ym in months and m.room in result.rooms)] + result.marks
    MonthMark.replace_all(sorted(marks, key=lambda m: (m.room, m.ym)))
    return {**result.summary(), "skipped_existing": skipped}
