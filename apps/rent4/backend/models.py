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


class Building(PoolObj):
    """건물. branch 는 core.auth 의 지점 코드 (임대인 계정의 branch 와 같다)."""
    collection: ClassVar[str] = "buildings"

    code: str                      # 임차인 아이디 접두어 (예: chic → chic603)
    name: str
    branch: str


class TenantAccount(PoolObj):
    """임차인 로그인 계정과 호실의 연결. 비밀번호 자체는 core.auth User 에 해시로 저장된다."""
    collection: ClassVar[str] = "tenant_accounts"

    username: str
    branch: str
    room: str
    tenant_key: str = ""           # 현 임차인 식별 (이름+전화 해시) — 바뀌면 계정 초기화
    initial_code: str = ""         # 임대인이 전달할 첫 비밀번호. 임차인이 바꾸면 지운다
    must_change: bool = True
    activated_at: Optional[str] = None
    invite_hash: str = ""          # 안내 링크 토큰 해시 (임차인이 바뀌면 비운다)


class AdminAccount(PoolObj):
    """관리인 계정의 첫 로그인 상태. 비밀번호는 core.auth User 에 해시로 저장된다.

    서버 콘솔(scripts/admin_link.py)이 설정 링크와 임시 비밀번호를 만들고, 관리인은 첫 로그인 때 바꾼다.
    """
    collection: ClassVar[str] = "admin_accounts"

    username: str
    initial_code: str = ""
    must_change: bool = True
    link_hash: str = ""
    activated_at: Optional[str] = None


class LessorAccount(PoolObj):
    """가맹 계약으로 만들어진 임대인 계정. 비밀번호는 core.auth User 에 해시로 저장된다."""
    collection: ClassVar[str] = "lessor_accounts"

    username: str
    branch: str
    contract_id: str = ""
    initial_code: str = ""         # 계약 링크에 보여 줄 임시 비밀번호. 바꾸면 지운다
    must_change: bool = True
    activated_at: Optional[str] = None


class Contract(PoolObj):
    """네트워크 상호 확인 계약.

    membership : 관리인 ↔ 임대인 가맹 계약 (가맹비 확인 → 임대인 계정)
    lease      : 임대인 ↔ 임차인 임대차 계약 (잔금 확인 → 호실 입주 + 임차인 계정)

    sent(발행자 확인·링크 발급) → signed(상대방이 링크에서 본인 확인 후 동의)
      → active(발행자가 가맹비/잔금 납부 확인 → 임시 비밀번호 발급) | cancelled
    """
    collection: ClassVar[str] = "contracts"

    kind: str                      # membership | lease
    branch: str
    building_name: str = ""
    building_code: str = ""
    room: str = ""
    party_name: str
    party_phone: str
    deposit: int = 0
    rent: int = 0
    mgmt: int = 0
    fee: int = 0                   # 가맹비 (membership)
    start: Optional[str] = None
    end: Optional[str] = None
    lease_type: str = "월세"
    pay_timing: str = "후불"
    status: str = "sent"
    token_hash: str = ""
    issued_by: str = ""
    signed_at: Optional[str] = None
    paid_at: Optional[str] = None
    activated_at: Optional[str] = None
    username: str = ""             # 활성화 때 발급된 아이디
    reset_at: Optional[str] = None # 비밀번호 분실로 링크·임시 비밀번호를 다시 발급한 시각


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

    branch: str = "main"
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

    branch: str = "main"
    room: str
    ym: str
    kind: str                      # vacant | unpaid | move_in | move_out
    raw: str = ""


class BankMessage(PoolObj):
    """임대인이 붙여 넣은 은행 입금 문자 원문과 처리 결과."""
    collection: ClassVar[str] = "bank_messages"

    raw: str
    fingerprint: str
    branch: str = "main"
    bank: str = ""
    deposit_date: Optional[str] = None
    deposit_time: str = ""
    amount: int = 0
    depositor: str = ""
    room: Optional[str] = None
    months: List[str] = []
    payment_ids: List[str] = []
    parsed_by: str = "rule"        # rule | ai
