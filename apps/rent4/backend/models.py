"""
apps/rent4/backend/models.py — Rent4 도메인 객체 (Room · Payment · MonthMark · BankMessage).

주민번호는 어떤 객체에도 저장하지 않는다 (Excel 가져오기 단계에서 버린다).
"""
from __future__ import annotations

import re
from datetime import date
from typing import ClassVar, List, Optional

from .pool import PoolObj


# ── 연월(ym, "YYYY-MM") 유틸 ──
def ym_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def ym_add(ym: str, months: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def ym_range(start: str, end: str) -> List[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = ym_add(cur, 1)
    return out


class Room(PoolObj):
    collection: ClassVar[str] = "rooms"

    room: str                      # 호실 번호 (예: "303")
    building: str = "시크빌"
    branch: str = "main"           # core.auth 의 지점 코드 (chic 계정 = main)
    status: str = "occupied"       # occupied | vacant | owner
    tenant_name: str = ""
    phone: str = ""
    move_in: Optional[str] = None  # YYYY-MM-DD
    expire: Optional[str] = None
    lease_type: str = "월세"        # 월세 | 전세
    pay_timing: str = "후불"        # 선불 | 후불 | 반선불
    deposit: int = 0
    rent: int = 0
    mgmt: int = 0

    @property
    def monthly_due(self) -> int:
        return self.rent + self.mgmt

    @property
    def phone_last4(self) -> str:
        digits = re.sub(r"\D", "", self.phone)
        return digits[-4:] if len(digits) >= 4 else ""

    @property
    def move_in_ym(self) -> Optional[str]:
        return self.move_in[:7] if self.move_in else None


class Payment(PoolObj):
    collection: ClassVar[str] = "payments"

    room: str
    ym: str                        # 귀속 연월 (YYYY-MM)
    paid_date: Optional[str] = None
    paid_date_raw: str = ""        # Excel 원문 또는 은행 문자 원문 날짜
    rent: int = 0
    mgmt: int = 0
    source: str = "excel"          # excel | bank | manual
    bank_message_id: Optional[str] = None
    depositor: str = ""
    flags: List[str] = []

    @property
    def amount(self) -> int:
        return self.rent + self.mgmt


class MonthMark(PoolObj):
    """Excel 의 '공실 / 미납 / 전입 / 전출' 표기를 그대로 보존한다."""
    collection: ClassVar[str] = "marks"

    room: str
    ym: str
    kind: str                      # vacant | unpaid | move_in | move_out
    raw: str = ""


class BankMessage(PoolObj):
    """임대인이 붙여 넣은 은행 입금 문자 원문과 처리 결과."""
    collection: ClassVar[str] = "bank_messages"

    raw: str
    fingerprint: str
    bank: str = ""
    deposit_date: Optional[str] = None
    deposit_time: str = ""
    amount: int = 0
    depositor: str = ""
    room: Optional[str] = None
    months: List[str] = []
    payment_ids: List[str] = []
    parsed_by: str = "rule"        # rule | ai
