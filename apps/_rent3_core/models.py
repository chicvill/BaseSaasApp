"""
apps/_rent3_core/models.py — Rent3 전 구성 요소 도메인 객체 정의 (`PoolObj` 하위 클래스).

모든 구성 요소가 독립 객체이며, `.save()` / `.delete()` 호출 즉시
`data/rent3_pool/*.json` 공동 Data Pool 파일이 실시간 갱신된다:
  - users.json     : Admin (관리인), Lessor (임대인), Tenant (임차인)
  - buildings.json : Building (건물)
  - rooms.json     : Room (호실 / 옵션 / 등기 / 공실매물)
  - contracts.json : Contract (체결 계약 원장 + rent2 후불 비과세/가짜체납 차단 규칙)
  - payments.json  : Pay (월세/관리비/부가세/계약금 수납 객체)
  - outlays.json   : Outlay (8대 표준 공용 운영비 및 수선 지출 객체)
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from uuid import uuid4

from pydantic import Field

from core.errors import Conflict, DomainError, Mismatch
from .pool import PoolObj, now_iso

BRANCH_ALIASES = {
    "main": "main",
    "chic": "main",
    "chicvill": "main",
    "imsi": "main",
    "시크빌": "main",
    "sunho": "sunho",
    "선호": "sunho",
    "선호아트빌": "sunho",
    "sinbi": "sinbi",
    "신비": "sinbi",
    "신비주택": "sinbi",
}

EXPENSE_CATEGORIES = {
    "cleaning": "계단·공용부 정기 청소비",
    "elevator": "승강기(엘리베이터) 유지관리",
    "utility_common": "공용 전기·수도·가스료",
    "safety_fire": "소방·전기안전관리 대행료",
    "insurance": "건물 화재보험·배상책임",
    "repair_maint": "공용시설 수선 및 소모품",
    "internet_tv": "건물 인터넷·IPTV 단체선",
    "other": "기타 일반 잡비",
}


def normalize_branch_id(raw: Optional[str]) -> str:
    """단일 진입점 지점 ID 정규화 (Single Source of Truth)."""
    if not raw:
        return "main"
    key = raw.strip().lower()
    return BRANCH_ALIASES.get(key, key)


def current_ym() -> str:
    env_today = date_today()
    return f"{env_today.year:04d}-{env_today.month:02d}"


def date_today() -> date:
    import os
    v = os.getenv("RENT3_TODAY") or os.getenv("RENT2_TODAY")
    if v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            pass
    return date.today()


def parse_ym(value: str) -> Optional[Tuple[int, int]]:
    s = (value or "").strip()
    m = re.search(r"(\d{4})\s*[-./]\s*(\d{1,2})", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if 1 <= mo <= 12:
        return (y, mo)
    return None


def ym_index(y: int, m: int) -> int:
    return y * 12 + m


def index_to_ym(idx: int) -> str:
    y = (idx - 1) // 12
    m = (idx - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def add_months_str(start_date: str, months: int) -> str:
    ym = parse_ym(start_date)
    if not ym:
        return start_date
    day = 1
    m_day = re.search(r"\d{4}[-./]\d{1,2}[-./](\d{1,2})", start_date)
    if m_day:
        day = min(int(m_day.group(1)), 28)
    end_idx = ym_index(ym[0], ym[1]) + months
    ey = (end_idx - 1) // 12
    em = (end_idx - 1) % 12 + 1
    return f"{ey:04d}-{em:02d}-{day:02d}"


# ─────────────────────────────────────────────────────────────
# 1. 사용자 객체군 (users.json) — Admin, Lessor, Tenant
# ─────────────────────────────────────────────────────────────
class Admin(PoolObj):
    """플랫폼 총괄 관리인 객체."""
    collection: ClassVar[str] = "users"
    label: ClassVar[str] = "관리인"

    id: str = Field(default_factory=lambda: f"admin_{uuid4().hex[:6]}")
    username: str = "admin"
    name: str = "본사 총괄 관리인"
    phone: str = "010-1212-3434"
    account_number: str = "국민 123-456-789012"
    business_number: str = "101-86-12345"
    email: str = "admin@rent3.saas.kr"
    role: str = "superadmin"

    def summary(self) -> str:
        return f"관리인 {self.name} ({self.username})"


class Lessor(PoolObj):
    """건물주 / 임대인 객체 (중개수수료 0원, 월 SaaS 앱 사용료 과금 대상)."""
    collection: ClassVar[str] = "users"
    label: ClassVar[str] = "임대인"

    id: str = Field(default_factory=lambda: f"lessor_{uuid4().hex[:6]}")
    username: str = ""
    name: str = ""
    phone: str = ""
    account_number: str = ""
    business_number: str = ""
    email: str = ""
    branch: str = "main"
    app_fee: int = 33000                     # 월 SaaS 솔루션 이용료 (원)
    app_fee_status: str = "PAID"             # PAID / PENDING / OVERDUE
    last_fee_paid_at: str = ""
    joined_at: str = Field(default_factory=lambda: date_today().isoformat())
    status: str = "ACTIVE"                   # PENDING_APPROVAL / ACTIVE / SUSPENDED

    def summary(self) -> str:
        return f"임대인 {self.name} ({self.branch}, 상태={self.status})"

    def approve(self, app_source: str = "rent3_admin") -> "Lessor":
        self.status = "ACTIVE"
        return self.save(action="lessor_approved", app_source=app_source)

    def settle_app_fee(self, paid_date: Optional[str] = None, app_source: str = "rent3_admin") -> "Lessor":
        self.app_fee_status = "PAID"
        self.last_fee_paid_at = paid_date or date_today().isoformat()
        return self.save(action="app_fee_settled", app_source=app_source)


class Tenant(PoolObj):
    """임차인 / 예비 세입자 객체."""
    collection: ClassVar[str] = "users"
    label: ClassVar[str] = "임차인"

    id: str = Field(default_factory=lambda: f"tenant_{uuid4().hex[:6]}")
    name: str = ""
    phone: str = ""
    email: str = ""
    account_number: str = ""
    business_number: str = ""
    car_number: str = ""
    car_model: str = ""
    doorlock_pwd: str = "1234*"
    branch: str = "main"
    id_building: str = ""
    current_room_id: str = ""
    room_no: str = ""
    checked_in: bool = False
    checked_in_at: str = ""
    checkin_memo: str = ""
    status: str = "ACTIVE"                   # PROSPECT / ACTIVE / MOVED_OUT

    def summary(self) -> str:
        return f"임차인 {self.name} ({self.room_no or '미배정'}호)"

    def register_vehicle(self, car_number: str, car_model: str = "", app_source: str = "rent3_tenant") -> "Tenant":
        self.car_number = car_number.strip()
        if car_model:
            self.car_model = car_model.strip()
        return self.save(action="vehicle_registered", app_source=app_source)

    def confirm_checkin(self, memo: str = "", app_source: str = "rent3_tenant") -> "Tenant":
        self.checked_in = True
        self.checked_in_at = now_iso()
        self.checkin_memo = memo.strip()
        return self.save(action="checked_in", app_source=app_source)


# ─────────────────────────────────────────────────────────────
# 2. 부동산 자산 객체군 (buildings.json, rooms.json)
# ─────────────────────────────────────────────────────────────
class Building(PoolObj):
    """건물 객체."""
    collection: ClassVar[str] = "buildings"
    label: ClassVar[str] = "건물"

    id: str = Field(default_factory=lambda: f"bldg_{uuid4().hex[:6]}")
    branch: str = "main"
    building_name: str = ""
    address: str = ""
    id_lessor: str = ""
    default_maintenance_fee: int = 50000
    common_entrance_pwd: str = "#1004*"
    wifi_info: str = "Rent3_Giga_WiFi / pass1234"
    property_value: int = 1200000000         # 자산 평가액 (세무/수익률 산출용)
    manager_phone: str = "010-1212-3434"

    def summary(self) -> str:
        return f"건물 {self.building_name} ({self.branch})"

    def rooms(self) -> List["Room"]:
        return sorted(Room.find(id_building=self.id), key=lambda r: r.room_no)

    def occupancy_stats(self) -> Dict[str, Any]:
        rms = self.rooms()
        total = len(rms)
        occupied = sum(1 for r in rms if r.is_occupied)
        vacant = total - occupied
        rate = round((occupied / total) * 100, 1) if total else 0.0
        return {
            "total_rooms": total,
            "occupied_rooms": occupied,
            "vacant_rooms": vacant,
            "occupancy_rate": rate,
        }


class Room(PoolObj):
    """호실 / 임대상세 / 공실 매물 객체."""
    collection: ClassVar[str] = "rooms"
    label: ClassVar[str] = "호실"

    id: str = Field(default_factory=lambda: f"room_{uuid4().hex[:6]}")
    branch: str = "main"
    id_building: str = ""
    room_no: str = ""
    name: str = ""
    floor: int = 2
    area_sqm: float = 23.0
    room_category: str = "주거용 원룸"        # 주거용 원룸 / 오피스텔 / 상가/사무실
    vat_applicable: bool = False             # 상가/업무용 시 True
    id_tenant: str = ""
    active_contract_id: str = ""
    doorlock_pwd: str = "2580*"
    registry_checked: bool = True
    registry_info: Dict[str, Any] = Field(default_factory=lambda: {
        "issue_number": "1101-2026-884920",
        "owner_name": "김임대",
        "mortgage_amount": 0,
        "clean_title": True,
        "verified_at": date_today().isoformat(),
    })
    status_description: str = "남향 풀옵션, 도배·장판 클린 완료"
    option_list: List[Dict[str, Any]] = Field(default_factory=lambda: [
        {"item": "시스템 에어컨", "quantity": 1, "condition": "정상(최상)"},
        {"item": "드럼 세탁기", "quantity": 1, "condition": "정상(양호)"},
        {"item": "빌트인 냉장고", "quantity": 1, "condition": "정상(최상)"},
        {"item": "인덕션 쿡탑", "quantity": 1, "condition": "정상"},
        {"item": "디지털 도어락", "quantity": 1, "condition": "신규 교체"},
    ])
    lease_type: str = "월세"
    pay_timing: str = "후불"
    deposit: int = 5000000
    monthly_rent: int = 500000
    maintenance_fee: int = 50000
    available_from: str = "즉시입주"
    pre_moveout_notice: bool = False         # 만기 전출예정 회신 시 사전 공실 매물 예고 플래그
    is_published: bool = True
    images: List[str] = Field(default_factory=list)

    @property
    def is_occupied(self) -> bool:
        return bool(self.id_tenant and self.active_contract_id)

    def summary(self) -> str:
        state = "입주중" if self.is_occupied else ("공실게시" if self.is_published else "공실비공개")
        return f"호실 {self.branch}:{self.room_no}호 ({state})"

    def update_doorlock(self, new_pwd: str, app_source: str = "rent3_lessor") -> "Room":
        if not new_pwd or len(new_pwd.strip()) < 4:
            raise DomainError("도어락 비밀번호는 4자리 이상이어야 합니다.", hint="예: 4860*")
        self.doorlock_pwd = new_pwd.strip()
        if self.id_tenant:
            t = Tenant.get(self.id_tenant)
            if t:
                t.doorlock_pwd = self.doorlock_pwd
                t.save(action="doorlock_synced", app_source=app_source)
        return self.save(action="doorlock_updated", app_source=app_source)

    def vacate(self, app_source: str = "rent3_lessor") -> "Room":
        """퇴실 정산 완료 시 공실 전환 및 직거래 매물 자동 재노출."""
        self.id_tenant = ""
        self.active_contract_id = ""
        self.pre_moveout_notice = False
        self.is_published = True
        self.available_from = "즉시입주"
        return self.save(action="room_vacated_and_relisted", app_source=app_source)


# ─────────────────────────────────────────────────────────────
# 3. 계약 & 수납 & 경비 객체군 (contracts.json, payments.json, outlays.json)
# ─────────────────────────────────────────────────────────────
class Contract(PoolObj):
    """임대차 계약 원장 객체 — rent2의 후불 비과세·가짜체납 방지·FIFO 청구 계산 규칙 내장."""
    collection: ClassVar[str] = "contracts"
    label: ClassVar[str] = "임대계약"

    id: str = Field(default_factory=lambda: f"ct_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_lessor: str = ""
    lessor_name: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    contract_date: str = Field(default_factory=lambda: date_today().isoformat())
    move_in_date: str = ""
    expiry_date: str = ""
    move_out_date: str = ""
    lease_term_months: int = 12
    lease_type: str = "월세"                 # 월세 / 전세
    pay_timing: str = "후불"                 # 후불 / 선불
    vat_target: bool = False                 # 주거용 원룸 False(면세), 상가/업무용 True(과세)
    vat_separate: bool = True                # 과세 시 10% 별도 청구 여부
    contract_deposit: int = 300000           # 계약금
    deposit: int = 5000000                   # 보증금
    monthly_rent: int = 500000               # 월 임차료
    maintenance_fee: int = 50000             # 월 관리비
    vat: int = 0                             # 월 부가세
    special_terms: str = ""
    option_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    registry_snapshot: Dict[str, Any] = Field(default_factory=dict)
    lessor_signature: str = ""
    tenant_signature: str = ""
    integrity_hash: str = ""
    cosign_session_id: str = ""
    contract_channel: str = "DIRECT"         # DIRECT (직거래 공동계약) / BROKER_PAPER (공인중개사 지면계약 사진등록)
    contract_photo_file: str = ""            # data/rent3_pool/ 기준 상대 경로 (예: uploads/contracts/ct_xxx.jpg)
    contract_photo_name: str = ""            # 원본 업로드 파일명
    broker_info: Dict[str, Any] = Field(default_factory=dict)  # 중개사무소 상호, 공인중개사명, 등록번호, 연락처 등
    fixed_date_no: str = ""                  # 대법원 확정일자 번호 (FIX-YYYYMMDD-XXXXXX)
    status: str = "ACTIVE"                   # ACTIVE / TERMINATED / RENEWED

    def summary(self) -> str:
        ch = "중개계약" if self.contract_channel == "BROKER_PAPER" else "직거래"
        return f"계약[{ch}] {self.room_no}호 {self.tenant_name} ({self.lease_type}/{self.pay_timing})"

    def compute_vat(self) -> int:
        if not self.vat_target or self.lease_type == "전세":
            return 0
        base = self.monthly_rent + self.maintenance_fee
        if self.vat_separate:
            return round(base * 0.1)
        return round(base - (base / 1.1))

    def days_to_expiry(self) -> Optional[int]:
        if not self.expiry_date:
            return None
        try:
            exp = date.fromisoformat(self.expiry_date[:10])
            return (exp - date_today()).days
        except ValueError:
            return None

    def expiry_window_status(self) -> Dict[str, Any]:
        """주택임대차보호법 제6조 만기 6개월(180일)~2개월(60일) 전 전출확인·재계약예고 구간 판별."""
        days = self.days_to_expiry()
        if days is None or not self.expiry_date:
            return {
                "days_to_expiry": None,
                "months_to_expiry": None,
                "window_state": "UNKNOWN",
                "in_6_to_2_months_window": False,
                "notice_open_date": "",
                "notice_deadline_date": "",
                "days_to_notice_deadline": None,
            }
        try:
            exp = date.fromisoformat(self.expiry_date[:10])
        except ValueError:
            return {
                "days_to_expiry": days,
                "months_to_expiry": None,
                "window_state": "UNKNOWN",
                "in_6_to_2_months_window": False,
                "notice_open_date": "",
                "notice_deadline_date": "",
                "days_to_notice_deadline": None,
            }

        open_dt = exp - timedelta(days=180)
        deadline_dt = exp - timedelta(days=60)
        days_to_deadline = (deadline_dt - date_today()).days
        if days < 0:
            w_state = "EXPIRED"
        elif days < 60:
            w_state = "TACIT_LOCKED"
        elif days <= 180:
            w_state = "NOTICE_WINDOW"
        else:
            w_state = "EARLY"

        return {
            "days_to_expiry": days,
            "months_to_expiry": round(days / 30.0, 1),
            "window_state": w_state,
            "in_6_to_2_months_window": (60 <= days <= 180),
            "notice_open_date": open_dt.isoformat(),
            "notice_deadline_date": deadline_dt.isoformat(),
            "days_to_notice_deadline": days_to_deadline,
        }

    def due_for_month(self, ym: str) -> Dict[str, Any]:
        """rent2 핵심 불변식 계승:
        - 전입일 이전 월은 절대 청구하지 않는다 (가짜 체납 Ghost Overdue 원천 차단).
        - 월세가 '후불'인 경우 입주월 당월은 청구액 0원(POSTPAID_EXEMPT)이며, 익월부터 청구한다.
        - 퇴실월 이후는 청구하지 않는다.
        """
        target = parse_ym(ym)
        move_in = parse_ym(self.move_in_date)
        if not target or not move_in:
            return {"ym": ym, "due": 0, "rent": 0, "maintenance": 0, "vat": 0, "rule_state": "INVALID_DATE"}

        t_idx = ym_index(*target)
        in_idx = ym_index(*move_in)

        if t_idx < in_idx:
            return {"ym": ym, "due": 0, "rent": 0, "maintenance": 0, "vat": 0, "rule_state": "BEFORE_MOVE_IN"}

        if self.move_out_date:
            out_ym = parse_ym(self.move_out_date)
            if out_ym and t_idx > ym_index(*out_ym):
                return {"ym": ym, "due": 0, "rent": 0, "maintenance": 0, "vat": 0, "rule_state": "AFTER_MOVE_OUT"}

        if self.pay_timing == "후불" and t_idx == in_idx:
            return {
                "ym": ym,
                "due": 0,
                "rent": 0,
                "maintenance": 0,
                "vat": 0,
                "rule_state": "POSTPAID_MOVE_IN_EXEMPT",
            }

        rent_amt = 0 if self.lease_type == "전세" else self.monthly_rent
        maint_amt = self.maintenance_fee
        vat_amt = self.compute_vat() if (self.vat_target and self.vat_separate) else 0
        total_due = rent_amt + maint_amt + vat_amt
        return {
            "ym": ym,
            "due": total_due,
            "rent": rent_amt,
            "maintenance": maint_amt,
            "vat": vat_amt,
            "rule_state": "BILLABLE",
        }

    def ledger(self, up_to_ym: Optional[str] = None) -> Dict[str, Any]:
        """계약 시작월부터 기준월까지 FIFO 수납 상계가 반영된 실시간 원장 산출."""
        end_ym = up_to_ym or current_ym()
        move_in = parse_ym(self.move_in_date)
        target_end = parse_ym(end_ym)
        if not move_in or not target_end:
            return {"months": [], "total_due": 0, "total_paid": 0, "total_unpaid": 0, "oldest_unpaid_ym": None}

        start_idx = ym_index(*move_in)
        end_idx = max(start_idx, ym_index(*target_end))

        pays = [
            p for p in Pay.find(id_contract=self.id)
            if p.payment_kind == "MONTHLY_RENT"
        ]
        total_pool_paid = sum(p.total_amount for p in pays)
        remaining_credit = total_pool_paid

        rows: List[Dict[str, Any]] = []
        total_due = 0
        for idx in range(start_idx, end_idx + 1):
            ym_str = index_to_ym(idx)
            spec = self.due_for_month(ym_str)
            due = spec["due"]
            total_due += due

            if spec["rule_state"] == "POSTPAID_MOVE_IN_EXEMPT":
                rows.append({
                    **spec,
                    "paid": 0,
                    "unpaid": 0,
                    "status": "EXEMPT_POSTPAID",
                    "note": "후불 입주월 비과세 (익월 청구)",
                })
                continue

            if due == 0:
                rows.append({
                    **spec,
                    "paid": 0,
                    "unpaid": 0,
                    "status": "ZERO_DUE",
                    "note": "청구 없음",
                })
                continue

            allocated = min(due, remaining_credit)
            remaining_credit -= allocated
            unpaid = due - allocated
            if unpaid == 0:
                st = "PAID"
                note = "완납"
            elif allocated > 0:
                st = "PARTIAL"
                note = f"부분납부 (잔여 {unpaid:,}원)"
            else:
                st = "UNPAID"
                note = "미납"

            rows.append({
                **spec,
                "paid": allocated,
                "unpaid": unpaid,
                "status": st,
                "note": note,
            })

        total_unpaid = sum(r["unpaid"] for r in rows)
        oldest = next((r["ym"] for r in rows if r["unpaid"] > 0), None)
        return {
            "contract_id": self.id,
            "room_no": self.room_no,
            "tenant_name": self.tenant_name,
            "pay_timing": self.pay_timing,
            "months": rows,
            "total_due": total_due,
            "total_paid": total_pool_paid,
            "advance_credit": remaining_credit,
            "total_unpaid": total_unpaid,
            "oldest_unpaid_ym": oldest,
            "days_to_expiry": self.days_to_expiry(),
        }

    def validate_renewal_cap(self, new_deposit: int, new_rent: int) -> Dict[str, Any]:
        """주택임대차보호법 제7조 5% 임대료 증액 상한선 검증."""
        old_equiv = self.monthly_rent
        max_rent = round(old_equiv * 1.05)
        if old_equiv > 0 and new_rent > max_rent:
            inc_pct = round(((new_rent - old_equiv) / old_equiv) * 100, 2)
            raise Mismatch(
                f"재계약 월세 증액률({inc_pct}%)이 법정 상한선(5.0%)을 초과했습니다.",
                hint=f"종전 월세 {old_equiv:,}원 기준 최대 {max_rent:,}원까지만 설정할 수 있습니다.",
                expected=max_rent,
                actual=new_rent,
            )
        max_dep = round(self.deposit * 1.05)
        if self.deposit > 0 and new_deposit > max_dep:
            inc_pct = round(((new_deposit - self.deposit) / self.deposit) * 100, 2)
            raise Mismatch(
                f"재계약 보증금 증액률({inc_pct}%)이 법정 상한선(5.0%)을 초과했습니다.",
                hint=f"종전 보증금 {self.deposit:,}원 기준 최대 {max_dep:,}원까지만 설정할 수 있습니다.",
                expected=max_dep,
                actual=new_deposit,
            )
        return {"valid": True, "max_rent": max_rent, "max_deposit": max_dep}

    def terminate(self, move_out_date: Optional[str] = None, app_source: str = "rent3_lessor") -> "Contract":
        self.move_out_date = move_out_date or date_today().isoformat()
        self.status = "TERMINATED"
        return self.save(action="contract_terminated", app_source=app_source)

    @staticmethod
    def compute_integrity_hash(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Pay(PoolObj):
    """수납 기록 객체 (월세·관리비·부가세·계약금)."""
    collection: ClassVar[str] = "payments"
    label: ClassVar[str] = "수납"

    id: str = Field(default_factory=lambda: f"pay_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_contract: str = ""
    id_tenant: str = ""
    payer_name: str = ""
    billing_month: str = Field(default_factory=current_ym)
    paid_date: str = Field(default_factory=lambda: date_today().isoformat())
    rent_amount: int = 0
    maintenance_amount: int = 0
    vat_amount: int = 0
    total_amount: int = 0
    payment_kind: str = "MONTHLY_RENT"       # MONTHLY_RENT / CONTRACT_DEPOSIT
    method: str = "TRANSFER"                 # BANK_SMS / CARD / KAKAOPAY / TRANSFER
    cash_receipt_no: str = Field(default_factory=lambda: f"CASH-{date_today().year}-{uuid4().hex[:6].upper()}")
    allocated_months: List[Dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str = ""
    raw_sms: str = ""

    def summary(self) -> str:
        return f"수납 {self.room_no}호 {self.payer_name} {self.total_amount:,}원 ({self.billing_month})"


class Outlay(PoolObj):
    """건물 공용 운영비 및 호실 수선비 지출 객체."""
    collection: ClassVar[str] = "outlays"
    label: ClassVar[str] = "지출"

    id: str = Field(default_factory=lambda: f"out_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    category: str = "cleaning"
    title: str = ""
    expense_date: str = Field(default_factory=lambda: date_today().isoformat())
    amount: int = 0
    vendor: str = ""
    memo: str = ""

    @property
    def category_label(self) -> str:
        return EXPENSE_CATEGORIES.get(self.category, self.category)

    def summary(self) -> str:
        return f"지출 [{self.category_label}] {self.title} {self.amount:,}원"

