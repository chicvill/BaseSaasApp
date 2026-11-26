"""
apps/_rent3_core/domains/models_property.py — 부동산 자산 객체군(Building, Room) 도메인 정의 (`buildings.json`, `rooms.json`).
"""
from __future__ import annotations

import re
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError
from .. import fieldcrypto
from ..pool import PoolObj, pool
from .models_contract import Contract
from .models_users import Tenant
from .models_utils import add_months_str, date_today


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
    vat_rooms: List[str] = Field(default_factory=list)         # 부가가치세 납부 대상 호실 리스트 (예: ["201", "202", ...])
    income_tax_rooms: List[str] = Field(default_factory=list)  # 종합소득세 신고 대상 호실 리스트 (예: ["201", ..., "703"])
    biz_company_name: str = ""               # 공급자 상호(미입력 시 building_name 사용)
    biz_type: str = "부동산업"               # 공급자 업태
    biz_item: str = "비주거용 건물 임대업"   # 공급자 종목

    @staticmethod
    def _clean_room_tok(x: Any) -> str:
        return re.sub(r"[\"'`\[\]\s]|호$", "", str(x or "").strip())

    def clean_vat_rooms(self) -> List[str]:
        out: List[str] = []
        for x in (self.vat_rooms or []):
            t = self._clean_room_tok(x)
            if t and t not in out:
                out.append(t)
        return out

    def clean_income_tax_rooms(self) -> List[str]:
        out: List[str] = []
        for x in (self.income_tax_rooms or []):
            t = self._clean_room_tok(x)
            if t and t not in out:
                out.append(t)
        return out

    def is_vat_room(self, room_no: str) -> bool:
        rn = self._clean_room_tok(room_no)
        return rn in self.clean_vat_rooms()

    def is_income_tax_room(self, room_no: str) -> bool:
        rn = self._clean_room_tok(room_no)
        cleaned = self.clean_income_tax_rooms()
        if not cleaned:
            return True
        return rn in cleaned

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
    building_name: Optional[str] = ""
    room_no: str = ""
    move_in_date: Optional[str] = ""
    payments: Optional[List[Any]] = Field(default_factory=list)
    name: str = ""
    floor: int = 2
    area_sqm: float = 23.0                   # 전용면적(㎡)
    total_floors: int = 0                    # 건물 전체 층수 (0 = 미입력)
    direction: str = ""                      # 향 (남향·동향 등)
    room_category: str = "주거용 원룸"        # 주거용 원룸 / 오피스텔 / 상가/사무실
    vat_applicable: bool = False             # 상가/업무용 시 True
    id_tenant: str = ""
    active_contract_id: str = ""
    # 아래 값들은 '실제로 입력·확인된 값'만 담는다 — 샘플 기본값을 넣지 않는다(예전에는 가짜 등기부·가전 옵션·도어락 번호가 모든 방에 자동으로 들어가 공개 매물에 나올 수 있었다)
    doorlock_pwd: str = ""
    registry_checked: bool = False
    registry_info: Dict[str, Any] = Field(default_factory=dict)
    status_description: str = ""
    option_list: List[Dict[str, Any]] = Field(default_factory=list)
    lease_type: str = "월세"
    pay_timing: str = "후불"
    deposit: int = 5000000
    monthly_rent: int = 500000
    maintenance_fee: int = 50000
    cleaning_fee: int = 0
    available_from: str = "즉시입주"
    pre_moveout_notice: bool = False         # 만기 전출예정 회신 시 사전 공실 매물 예고 플래그
    # 입주 중인 호실을 사전 매물로 등록할 때 '다음 세입자용 조건'을 여기에 따로 둔다(현재 계약과 방 값은 건드리지 않는다).
    # 퇴실이 확정돼 호실이 비면(vacate) 방 값에 반영하고 곧바로 비운다. 새 계약이 체결되면 계약 값으로 덮이고 이 값도 비워진다.
    next_listing: Dict[str, Any] = Field(default_factory=dict)
    reserved_contract_id: str = ""           # 입주 중인 전출 예정 호실에 미리 체결한 '예약 계약'(status=SCHEDULED). 퇴실 확정(vacate) 때 새 입주자로 바뀐다
    next_terms: Dict[str, Any] = Field(default_factory=dict)   # 임대인이 저장해 둔 '다음 계약 조건'(보증금·월세·관리비·청소비·납부시기). 지금 계약은 만기까지 그대로이고, 신규 입주·연장 적용 시 현재 계약 조건을 대체한다
    TERMS_FIELDS: ClassVar[tuple] = ("deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "pay_timing")
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

    LISTING_FIELDS: ClassVar[tuple] = ("name", "room_category", "floor", "area_sqm", "total_floors", "direction", "deposit", "monthly_rent",
                                       "maintenance_fee", "cleaning_fee", "pay_timing", "vat_applicable", "available_from", "status_description")

    def as_listing(self) -> "Room":
        """공개 목록·홍보글이 읽는 값: 입주 중이고 다음 세입자용 조건이 있으면 그 조건을 덮어쓴 복사본(저장하지 않는다)."""
        if self.is_occupied and (self.next_listing or self.next_terms):
            merged = {**{k: v for k, v in (self.next_terms or {}).items() if k in self.TERMS_FIELDS}, **(self.next_listing or {})}
            return self.model_copy(update={k: v for k, v in merged.items() if k in self.LISTING_FIELDS})
        return self

    def seed_listing_from_terms(self) -> None:
        """사전 매물로 공개할 때 매물 조건(next_listing)이 비어 있으면 임대인이 저장해 둔 '다음 계약 조건'을 매물 조건으로 쓴다."""
        if not self.next_listing and self.next_terms:
            self.next_listing = {k: v for k, v in self.next_terms.items() if k in self.TERMS_FIELDS}

    def reservation(self) -> Optional["Contract"]:
        """이 호실에 미리 체결해 둔 예약 계약(SCHEDULED). 없으면 None."""
        ct = Contract.get(self.reserved_contract_id) if self.reserved_contract_id else None
        return ct if (ct is not None and ct.status == "SCHEDULED") else None

    def expected_vacate_date(self) -> str:
        """입주 중인 호실의 예상 퇴실일(YYYY-MM-DD): 임차인이 회신한 전출 예정일 → 입주가능일에 적은 날짜 → 현재 계약 만기일. 모르면 ''."""
        if not self.active_contract_id:
            return ""
        try:
            from ..ops import ExpiryRenewalCheck
            chk = ExpiryRenewalCheck.for_contract(self.active_contract_id)
            if chk and re.match(r"^\d{4}-\d{2}-\d{2}$", chk.planned_move_out_date or ""):
                return chk.planned_move_out_date
        except Exception:
            pass
        m = re.search(r"\d{4}-\d{2}-\d{2}", self.available_from or "")
        if m:
            return m.group(0)
        ct = Contract.get(self.active_contract_id)
        return (ct.expiry_date or "") if ct else ""

    PRELIST_MONTHS_BEFORE: ClassVar[int] = 1      # 사전 매물은 퇴실 기준일(만기)의 1개월 전부터 공개한다 — 너무 일찍 공개하면 지금 사는 임차인이 불편하다

    def prelist_open_date(self, planned: str = "") -> str:
        """입주 중인 호실을 사전 매물로 공개할 수 있는 첫날(YYYY-MM-DD) = 퇴실 기준일의 1개월 전.
        퇴실 기준일은 임차인이 회신한 전출 예정일(planned 로 직접 줄 수도 있음), 없으면 현재 계약 만기일. 모르면 ''(제한 없음)."""
        ref = (planned or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", ref):
            ref = ""
            if self.active_contract_id:
                try:
                    from ..ops import ExpiryRenewalCheck
                    chk = ExpiryRenewalCheck.for_contract(self.active_contract_id)
                    if chk and re.match(r"^\d{4}-\d{2}-\d{2}$", chk.planned_move_out_date or ""):
                        ref = chk.planned_move_out_date
                except Exception:
                    pass
        if not ref and self.active_contract_id:
            ct = Contract.get(self.active_contract_id)
            ref = (ct.expiry_date or "") if ct else ""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", ref):
            return ""
        return add_months_str(ref, -self.PRELIST_MONTHS_BEFORE)

    def cancel_reservation(self, reason: str = "", app_source: str = "rent3_lessor") -> "Contract":
        """예약 계약을 취소한다(체결 후 아직 입주 전). 계약은 CANCELLED, 호실은 전출 예정 사전 매물로 다시 공개한다.
        이미 받은 계약금 반환은 당사자가 직접 처리한다(수납 기록은 지우지 않는다)."""
        ct = self.reservation()
        if ct is None:
            raise DomainError("취소할 예약 계약이 없습니다.")
        ct.status = "CANCELLED"
        ct.ended_at = date_today().isoformat()
        ct.special_terms = (ct.special_terms or "") + (f"\n[예약 취소 사유] {reason.strip()}" if reason.strip() else "\n[예약 취소]")
        ct.save(action="reserved_contract_cancelled", app_source=app_source)
        self.reserved_contract_id = ""
        if not ct.renews_contract_id:           # 새 입주자 예약이었다면 전출 예정 사전 매물로 다시 공개(연장 예약은 매물이 아니다)
            self.is_published = True
        self.save(action="room_reservation_cancelled", app_source=app_source)
        return ct

    def reservation_is_renewal(self) -> bool:
        ct = self.reservation()
        return bool(ct is not None and ct.renews_contract_id)

    def activate_renewal(self, ct: "Contract", app_source: str = "rent3_system") -> "Room":
        """미리 체결한 연장 재계약의 시작일이 되면: 이전 계약을 RENEWED 로 닫고 새 계약을 ACTIVE 로 올리며 호실의 조건을 새 조건으로 바꾼다.
        그 전까지는 이전 계약 조건으로 청구한다. 임차인·입주 상태는 그대로다."""
        old = Contract.get(ct.renews_contract_id)
        if old is not None and old.status == "ACTIVE":
            old.status = "RENEWED"
            old.ended_at = ct.move_in_date or date_today().isoformat()
            old.save(action="contract_renewed_replaced", app_source=app_source)
        ct.status = "ACTIVE"
        ct.save(action="renewal_contract_activated", app_source=app_source)
        self.active_contract_id = ct.id
        self.deposit, self.monthly_rent, self.maintenance_fee = ct.deposit, ct.monthly_rent, ct.maintenance_fee
        self.cleaning_fee = ct.cleaning_fee or 0
        self.lease_type, self.pay_timing = ct.lease_type, ct.pay_timing
        self.pre_moveout_notice = False
        self.next_listing = {}
        self.next_terms = {}
        self.reserved_contract_id = ""
        self.available_from = "즉시입주"
        self.is_published = False
        return self.save(action="room_renewal_applied", app_source=app_source)

    def _activate_reservation(self, ct: "Contract", app_source: str) -> "Room":
        """퇴실이 확정되어 방이 비는 순간, 미리 체결한 예약 계약이 입주 계약(ACTIVE)이 되고 임차인이 입주한다."""
        ct.status = "ACTIVE"
        ct.save(action="reserved_contract_activated", app_source=app_source)
        self.id_tenant = ct.id_tenant
        self.active_contract_id = ct.id
        self.deposit, self.monthly_rent, self.maintenance_fee = ct.deposit, ct.monthly_rent, ct.maintenance_fee
        self.cleaning_fee = ct.cleaning_fee or 0
        self.lease_type, self.pay_timing = ct.lease_type, ct.pay_timing
        self.pre_moveout_notice = False
        self.next_listing = {}
        self.next_terms = {}
        self.reserved_contract_id = ""
        self.available_from = "즉시입주"
        self.is_published = False
        t = Tenant.get(ct.id_tenant)
        if t is not None:
            t.status = "ACTIVE"
            t.current_room_id, t.room_no, t.branch, t.id_building = self.id, self.room_no, self.branch, self.id_building
            t.doorlock_pwd = self.doorlock_pwd
            t.save(action="tenant_activated_from_reservation", app_source=app_source)
        return self.save(action="room_assigned_from_reservation", app_source=app_source)

    def vacate(self, app_source: str = "rent3_lessor") -> "Room":
        """퇴실 정산 완료 시 공실 전환 및 직거래 매물 자동 재노출. 다음 세입자용 조건(next_listing)이 있으면 방 값으로 반영하고 비운다.
        미리 체결한 예약 계약이 있으면 공실로 내놓지 않고 곧바로 그 계약의 임차인이 입주한다."""
        reserved = self.reservation()
        if reserved is not None and reserved.renews_contract_id:
            # 지금 임차인의 연장 재계약 예약인데 퇴실이 확정됐다 → 연장은 효력이 없으니 취소하고 보통의 퇴실 처리로 이어 간다
            reserved.status = "CANCELLED"
            reserved.ended_at = date_today().isoformat()
            reserved.special_terms = (reserved.special_terms or "") + "\n[연장 예약 자동 취소] 만기 전에 퇴실이 확정되어 연장 재계약이 취소되었습니다."
            reserved.save(action="renewal_reservation_cancelled_on_vacate", app_source=app_source)
            self.reserved_contract_id = ""
            reserved = None
        if reserved is not None:
            self.id_tenant = ""
            self.active_contract_id = ""
            return self._activate_reservation(reserved, app_source)
        nl = {k: v for k, v in (self.next_listing or {}).items() if k in self.LISTING_FIELDS}
        for k, v in nl.items():
            setattr(self, k, v)
        for k, v in (self.next_terms or {}).items():            # 임대인이 저장해 둔 다음 계약 조건(매물 조건이 따로 없는 항목만)
            if k in self.TERMS_FIELDS and k not in nl:
                setattr(self, k, v)
        self.next_listing = {}
        self.next_terms = {}
        self.id_tenant = ""
        self.active_contract_id = ""
        self.pre_moveout_notice = False
        self.is_published = True
        if not str(nl.get("available_from") or "").strip():
            self.available_from = "즉시입주"
        return self.save(action="room_vacated_and_relisted", app_source=app_source)

    def save(self, action: str = "updated", app_source: str = "rent3") -> "Room":
        res = super().save(action=action, app_source=app_source)
        try:
            pool().save_room_partition(fieldcrypto.seal(self.model_dump()))
        except Exception:
            pass
        return res
