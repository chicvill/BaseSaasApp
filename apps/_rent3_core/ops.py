"""
apps/_rent3_core/ops.py — 1:1 직통 인앱 메신저, A/S 하자보수 대장, 퇴실 보증금 공제 정산, 국토부 RTMS 전월세 신고 객체.

모든 객체는 `data/rent3_pool/ops.json`에 실시간으로 저장·업데이트된다.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError
from .models import Building, Contract, Outlay, Room, Tenant, date_today, normalize_branch_id
from .pool import PoolObj, now_iso


class DirectChat(PoolObj):
    """외부 알림톡 비용 0원 — 임대인·임차인 1:1 인앱 실시간 직통 메시지 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "1:1 대화"

    id: str = Field(default_factory=lambda: f"chat_{uuid4().hex[:8]}")
    branch: str = "main"
    id_room: str = ""
    room_no: str = ""
    sender_role: str = "tenant"              # lessor / tenant / system
    sender_name: str = ""
    message: str = ""
    quick_action_label: str = ""
    quick_action_url: str = ""
    is_read: bool = False

    def summary(self) -> str:
        return f"채팅 [{self.room_no}호] {self.sender_name}: {self.message[:24]}"


class MaintenanceTicket(PoolObj):
    """5단계 정식 하자보수(A/S) 접수·배정·수리·정산 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "하자보수 티켓"

    id: str = Field(default_factory=lambda: f"as_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    category: str = "설비/배관"               # 에어컨 / 설비/배관 / 전기/조명 / 도어락 / 보일러
    title: str = ""
    description: str = ""
    before_photo: str = ""
    after_photo: str = ""
    status: str = "RECEIVED"                 # RECEIVED -> ASSIGNED -> IN_REPAIR -> COMPLETED -> CLOSED
    vendor_name: str = ""
    vendor_phone: str = ""
    visit_date: str = ""
    cost_burden: str = "LANDLORD"            # LANDLORD (자연마모 무상) / TENANT (과실 실비) / SHARED (50:50)
    repair_cost: int = 0
    resolution_note: str = ""

    def summary(self) -> str:
        return f"A/S [{self.room_no}호] {self.title} ({self.status})"

    def assign_vendor(
        self,
        vendor_name: str,
        vendor_phone: str,
        visit_date: str,
        app_source: str = "rent3_lessor",
    ) -> "MaintenanceTicket":
        self.vendor_name = vendor_name.strip()
        self.vendor_phone = vendor_phone.strip()
        self.visit_date = visit_date.strip()
        self.status = "ASSIGNED"
        self.save(action="ticket_vendor_assigned", app_source=app_source)

        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role="system",
            sender_name="시설관제 안내",
            message=f"[A/S 배정 완료] '{self.title}' 수리를 위해 {self.vendor_name}({self.vendor_phone}) 기사가 {self.visit_date} 방문 예정입니다.",
        ).save(action="chat_auto_sent", app_source=app_source)
        return self

    def complete_repair(
        self,
        repair_cost: int,
        cost_burden: str = "LANDLORD",
        resolution_note: str = "",
        after_photo: str = "",
        app_source: str = "rent3_lessor",
    ) -> "MaintenanceTicket":
        self.repair_cost = max(0, repair_cost)
        self.cost_burden = cost_burden
        self.resolution_note = resolution_note.strip()
        if after_photo:
            self.after_photo = after_photo
        self.status = "COMPLETED"
        self.save(action="ticket_repair_completed", app_source=app_source)

        # 임대인 부담 수선비는 공동 Data Pool outlays.json에 필요경비로 자동 등재
        landlord_share = (
            self.repair_cost if cost_burden == "LANDLORD"
            else (self.repair_cost // 2 if cost_burden == "SHARED" else 0)
        )
        if landlord_share > 0:
            Outlay(
                branch=self.branch,
                id_building=self.id_building,
                id_room=self.id_room,
                category="repair_maint",
                title=f"[{self.room_no}호 A/S] {self.title}",
                amount=landlord_share,
                vendor=self.vendor_name or "협력수리업체",
                memo=self.resolution_note,
            ).save(action="outlay_auto_recorded_from_as", app_source=app_source)

        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role="system",
            sender_name="시설관제 안내",
            message=f"[A/S 수리 완료] '{self.title}' 조치가 완료되었습니다. (비용: {self.repair_cost:,}원, 부담: {cost_burden})",
        ).save(action="chat_auto_sent", app_source=app_source)
        return self


class CheckOutSettlement(PoolObj):
    """퇴실 시설 대조 점검 & 보증금 공제 정산 및 원클릭 공실 자동 복귀 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "퇴실 정산서"

    id: str = Field(default_factory=lambda: f"co_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_contract: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    status: str = "CALCULATED"               # CALCULATED -> AGREED -> COMPLETED
    deposit_original: int = 0
    unpaid_rent_deduction: int = 0           # Contract.ledger() 실시간 미납액 자동 반영
    utility_settlement: int = 0              # 당일 계량기 공과금 실비
    cleaning_fee: int = 70000                # 입주 청소비 약정액
    damage_restoration_fee: int = 0          # 세입자 과실 파손 실비 (자연마모 0원)
    refund_amount: int = 0
    refund_bank_account: str = ""
    tenant_signature: str = ""
    inspection_notes: str = ""
    completed_at: str = ""

    def summary(self) -> str:
        return f"퇴실정산 [{self.room_no}호] 반환액 {self.refund_amount:,}원 ({self.status})"

    @classmethod
    def calculate_for_room(
        cls,
        branch: str,
        room_no: str,
        utility_settlement: int = 0,
        cleaning_fee: int = 70000,
        damage_restoration_fee: int = 0,
        inspection_notes: str = "6대 주요 시설(에어컨·세탁기·냉장고·도어락·벽지·욕실) 자연마모 외 이상 없음",
        app_source: str = "rent3_lessor",
    ) -> "CheckOutSettlement":
        b_id = normalize_branch_id(branch)
        room = Room.first(branch=b_id, room_no=room_no)
        if not room or not room.active_contract_id:
            raise DomainError(f"{room_no}호는 현재 퇴실 정산할 활성 계약이 없습니다.")

        ct = Contract.require(room.active_contract_id)
        ledger = ct.ledger()
        unpaid = int(ledger["total_unpaid"])
        total_deduct = unpaid + utility_settlement + cleaning_fee + damage_restoration_fee
        refund = max(0, ct.deposit - total_deduct)

        settlement = cls(
            branch=b_id,
            id_building=room.id_building,
            id_room=room.id,
            room_no=room.room_no,
            id_contract=ct.id,
            id_tenant=ct.id_tenant,
            tenant_name=ct.tenant_name,
            status="CALCULATED",
            deposit_original=ct.deposit,
            unpaid_rent_deduction=unpaid,
            utility_settlement=utility_settlement,
            cleaning_fee=cleaning_fee,
            damage_restoration_fee=damage_restoration_fee,
            refund_amount=refund,
            inspection_notes=inspection_notes,
        ).save(action="checkout_calculated", app_source=app_source)

        DirectChat(
            branch=b_id,
            id_room=room.id,
            room_no=room.room_no,
            sender_role="system",
            sender_name="퇴실정산 안내",
            message=f"[보증금 정산 명세서 도착] 원보증금 {ct.deposit:,}원 중 공제 {total_deduct:,}원을 제외한 실반환 예정액은 {refund:,}원입니다. 임차인 앱에서 계좌 입력 및 동의 서명을 진행해 주세요.",
        ).save(action="chat_auto_sent", app_source=app_source)
        return settlement

    def tenant_agree(self, bank_account: str, signature: str, app_source: str = "rent3_tenant") -> "CheckOutSettlement":
        if not bank_account.strip() or not signature.strip():
            raise DomainError("반환받을 계좌번호와 동의 서명을 모두 입력해 주세요.")
        self.refund_bank_account = bank_account.strip()
        self.tenant_signature = signature.strip()
        self.status = "AGREED"
        return self.save(action="checkout_agreed_by_tenant", app_source=app_source)

    def complete_refund_and_vacate(self, app_source: str = "rent3_lessor") -> "CheckOutSettlement":
        """보증금 송금 완료 승인 즉시: 계약 종료 + 임차인 퇴실 + 호실 공실 및 직거래 매물 자동 복귀!"""
        self.status = "COMPLETED"
        self.completed_at = now_iso()
        self.save(action="checkout_completed", app_source=app_source)

        ct = Contract.get(self.id_contract)
        if ct:
            ct.terminate(app_source=app_source)

        tenant = Tenant.get(self.id_tenant)
        if tenant:
            tenant.status = "MOVED_OUT"
            tenant.current_room_id = ""
            tenant.save(action="tenant_moved_out", app_source=app_source)

        room = Room.get(self.id_room)
        if room:
            room.vacate(app_source=app_source)

        return self


class RtmsReport(PoolObj):
    """국토교통부 주택임대차 계약 신고(전월세 신고제) 및 대법원 확정일자 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "전월세 신고필증"

    id: str = Field(default_factory=lambda: f"rtms_{uuid4().hex[:8]}")
    branch: str = "main"
    id_contract: str = ""
    id_room: str = ""
    room_no: str = ""
    lessor_name: str = ""
    tenant_name: str = ""
    deposit: int = 0
    monthly_rent: int = 0
    contract_date: str = ""
    deadline_date: str = ""
    mandatory: bool = True                   # 보증금 6천만원 초과 또는 월세 30만원 초과 시 의무
    status: str = "FILED"                    # PENDING / FILED
    fixed_date_no: str = ""                  # FIX-YYYYMMDD-XXXXXX
    filed_at: str = ""

    def summary(self) -> str:
        return f"RTMS [{self.room_no}호] {self.status} ({self.fixed_date_no or '신고대기'})"

    def days_left(self) -> int:
        if not self.deadline_date:
            return 30
        try:
            d = date.fromisoformat(self.deadline_date[:10])
            return (d - date_today()).days
        except ValueError:
            return 30

    @classmethod
    def auto_file_for_contract(cls, ct: Contract, app_source: str = "rent3_contract") -> "RtmsReport":
        c_date = date_today()
        try:
            c_date = date.fromisoformat(ct.contract_date[:10])
        except ValueError:
            pass
        deadline = (c_date + timedelta(days=30)).isoformat()
        mandatory = (ct.deposit > 60_000_000) or (ct.monthly_rent > 300_000)
        fix_no = f"FIX-{c_date.strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"

        report = cls(
            branch=ct.branch,
            id_contract=ct.id,
            id_room=ct.id_room,
            room_no=ct.room_no,
            lessor_name=ct.lessor_name,
            tenant_name=ct.tenant_name,
            deposit=ct.deposit,
            monthly_rent=ct.monthly_rent,
            contract_date=ct.contract_date,
            deadline_date=deadline,
            mandatory=mandatory,
            status="FILED",
            fixed_date_no=fix_no,
            filed_at=now_iso(),
        ).save(action="rtms_auto_filed_and_fixed_date_granted", app_source=app_source)

        ct.fixed_date_no = fix_no
        ct.save(action="fixed_date_attached", app_source=app_source)
        return report


class ExpiryRenewalCheck(PoolObj):
    """만기 6~2개월 전 전출계획 확인 및 전출의사 없을 때 연장 재계약 예고 객체 (주택임대차보호법 제6조·제7조)."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "만기 전출확인·재계약예고"

    id: str = Field(default_factory=lambda: f"exp_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_contract: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    lessor_name: str = ""
    expiry_date: str = ""
    notice_deadline_date: str = ""

    # AWAITING_TENANT -> MOVEOUT_PLANNED | STAY_CONFIRMED -> RENEWAL_PREVIEWED -> RENEWAL_ACKNOWLEDGED
    status: str = "AWAITING_TENANT"
    tenant_plan: str = "UNDECIDED"           # UNDECIDED / MOVE_OUT / STAY
    planned_move_out_date: str = ""
    tenant_memo: str = ""
    check_requested_at: str = ""
    tenant_responded_at: str = ""

    renewal_type: str = ""                   # SAME_TERMS / ADJUSTED_WITHIN_5PCT
    current_deposit: int = 0
    current_monthly_rent: int = 0
    current_maintenance_fee: int = 0
    proposed_deposit: int = 0
    proposed_monthly_rent: int = 0
    proposed_maintenance_fee: int = 0
    proposed_term_months: int = 12
    deposit_increase_pct: float = 0.0
    rent_increase_pct: float = 0.0
    renewal_note: str = ""
    cosign_session_id: str = ""
    cosign_session_code: str = ""
    renewal_preview_sent_at: str = ""
    renewal_acknowledged_at: str = ""

    def summary(self) -> str:
        return f"만기확인 [{self.room_no}호 {self.tenant_name}] {self.status} (만기 {self.expiry_date})"

    @classmethod
    def for_contract(cls, contract_id: str) -> Optional["ExpiryRenewalCheck"]:
        if not contract_id:
            return None
        matches = cls.find(id_contract=contract_id)
        if not matches:
            return None
        return sorted(matches, key=lambda x: x.updated_at, reverse=True)[0]

    @classmethod
    def request_moveout_check(
        cls,
        branch: str,
        room_no: str,
        message_note: str = "",
        app_source: str = "rent3_lessor",
    ) -> "ExpiryRenewalCheck":
        b_id = normalize_branch_id(branch)
        room = Room.first(branch=b_id, room_no=room_no)
        if not room or not room.active_contract_id:
            raise DomainError(f"{room_no}호에는 현재 진행 중인 활성 임대차 계약이 없습니다.")

        ct = Contract.require(room.active_contract_id)
        win = ct.expiry_window_status()
        existing = cls.for_contract(ct.id)

        if existing:
            chk = existing
            chk.expiry_date = ct.expiry_date
            chk.notice_deadline_date = win["notice_deadline_date"]
            chk.status = "AWAITING_TENANT"
            chk.tenant_plan = "UNDECIDED"
            chk.check_requested_at = now_iso()
        else:
            chk = cls(
                branch=b_id,
                id_building=ct.id_building,
                id_room=room.id,
                room_no=room.room_no,
                id_contract=ct.id,
                id_tenant=ct.id_tenant,
                tenant_name=ct.tenant_name,
                lessor_name=ct.lessor_name,
                expiry_date=ct.expiry_date,
                notice_deadline_date=win["notice_deadline_date"],
                status="AWAITING_TENANT",
                tenant_plan="UNDECIDED",
                check_requested_at=now_iso(),
                current_deposit=ct.deposit,
                current_monthly_rent=ct.monthly_rent,
                current_maintenance_fee=ct.maintenance_fee,
                proposed_deposit=ct.deposit,
                proposed_monthly_rent=ct.monthly_rent,
                proposed_maintenance_fee=ct.maintenance_fee,
                proposed_term_months=ct.lease_term_months or 12,
            )
        chk.save(action="expiry_moveout_check_requested", app_source=app_source)

        extra = f" ({message_note.strip()})" if message_note.strip() else ""
        DirectChat(
            branch=b_id,
            id_room=room.id,
            room_no=room.room_no,
            sender_role="lessor",
            sender_name=ct.lessor_name or "임대인",
            message=(
                f"[📅 만기 6~2개월 전 전출계획 확인 요청] {room.room_no}호 계약 만기일({ct.expiry_date}, "
                f"법정 통지마감 {win['notice_deadline_date']})을 앞두고 퇴실(전출) 계획 여부를 확인 중입니다. "
                f"임차인 마이홈에서 [전출 예정] 또는 [전출의사 없음(연장 희망)]을 회신해 주세요.{extra}"
            ),
            quick_action_label="마이홈 전출계획 회신",
            quick_action_url=f"/rent3_tenant/?tenant={ct.id_tenant}",
        ).save(action="chat_auto_sent", app_source=app_source)
        return chk

    @classmethod
    def ensure_for_contract(cls, ct: Contract) -> "ExpiryRenewalCheck":
        existing = cls.for_contract(ct.id)
        if existing:
            return existing
        win = ct.expiry_window_status()
        return cls(
            branch=ct.branch,
            id_building=ct.id_building,
            id_room=ct.id_room,
            room_no=ct.room_no,
            id_contract=ct.id,
            id_tenant=ct.id_tenant,
            tenant_name=ct.tenant_name,
            lessor_name=ct.lessor_name,
            expiry_date=ct.expiry_date,
            notice_deadline_date=win["notice_deadline_date"],
            status="AWAITING_TENANT",
            tenant_plan="UNDECIDED",
            check_requested_at=now_iso(),
            current_deposit=ct.deposit,
            current_monthly_rent=ct.monthly_rent,
            current_maintenance_fee=ct.maintenance_fee,
            proposed_deposit=ct.deposit,
            proposed_monthly_rent=ct.monthly_rent,
            proposed_maintenance_fee=ct.maintenance_fee,
            proposed_term_months=ct.lease_term_months or 12,
        )

    def respond_moveout_plan(
        self,
        tenant_plan: str,
        planned_move_out_date: str = "",
        tenant_memo: str = "",
        prelist_vacancy: bool = True,
        app_source: str = "rent3_tenant",
    ) -> "ExpiryRenewalCheck":
        plan = (tenant_plan or "").strip().upper()
        if plan not in ("MOVE_OUT", "STAY"):
            raise DomainError("전출계획 회신 값은 'MOVE_OUT'(전출 예정) 또는 'STAY'(전출의사 없음/연장 희망)여야 합니다.")

        self.tenant_plan = plan
        self.tenant_memo = (tenant_memo or "").strip()
        self.tenant_responded_at = now_iso()

        room = Room.get(self.id_room)

        if plan == "MOVE_OUT":
            self.status = "MOVEOUT_PLANNED"
            self.planned_move_out_date = (planned_move_out_date or "").strip() or self.expiry_date
            self.save(action="tenant_planned_moveout", app_source=app_source)

            if room and prelist_vacancy:
                room.pre_moveout_notice = True
                room.is_published = True
                room.available_from = f"{self.planned_move_out_date} 입주예정(만기퇴실)"
                room.save(action="room_prelisted_from_moveout_plan", app_source=app_source)

            DirectChat(
                branch=self.branch,
                id_room=self.id_room,
                room_no=self.room_no,
                sender_role="tenant",
                sender_name=self.tenant_name,
                message=(
                    f"[📦 만기 전출(퇴실) 예정 회신] {self.room_no}호 {self.tenant_name} 세입자가 "
                    f"{self.planned_move_out_date} 퇴실 예정을 회신했습니다."
                    + (" (공실 쇼케이스에 사전예약 매물로 자동 등록됨)" if prelist_vacancy else "")
                ),
            ).save(action="chat_auto_sent", app_source=app_source)
        else:
            self.status = "STAY_CONFIRMED"
            self.planned_move_out_date = ""
            self.save(action="tenant_confirmed_no_moveout", app_source=app_source)

            if room and room.pre_moveout_notice:
                room.pre_moveout_notice = False
                room.is_published = False
                room.available_from = "즉시입주"
                room.save(action="room_prelist_cancelled_for_stay", app_source=app_source)

            DirectChat(
                branch=self.branch,
                id_room=self.id_room,
                room_no=self.room_no,
                sender_role="tenant",
                sender_name=self.tenant_name,
                message=(
                    f"[🏠 전출의사 없음(거주 연장 희망) 회신] {self.room_no}호 {self.tenant_name} 세입자가 전출 계획이 없음을 "
                    f"확인했습니다. 임대인 앱에서 연장 재계약 조건을 예고해 주세요."
                ),
            ).save(action="chat_auto_sent", app_source=app_source)

        return self

    def send_renewal_preview(
        self,
        proposed_deposit: Optional[int] = None,
        proposed_monthly_rent: Optional[int] = None,
        proposed_maintenance_fee: Optional[int] = None,
        proposed_term_months: int = 12,
        renewal_note: str = "",
        open_cosign_session: bool = True,
        app_source: str = "rent3_lessor",
    ) -> "ExpiryRenewalCheck":
        if self.tenant_plan == "MOVE_OUT":
            raise DomainError(
                "임차인이 '전출(퇴실) 예정'으로 회신한 상태입니다.",
                hint="연장 재계약 예고를 발송하려면 먼저 전출의사 없음(연장 희망) 상태로 전환해 주세요.",
            )

        ct = Contract.require(self.id_contract)
        dep = ct.deposit if proposed_deposit is None else proposed_deposit
        rent = ct.monthly_rent if proposed_monthly_rent is None else proposed_monthly_rent
        maint = ct.maintenance_fee if proposed_maintenance_fee is None else proposed_maintenance_fee
        months = max(1, proposed_term_months or 12)

        # 주택임대차보호법 제7조 법정 5% 상한 검증 (초과 시 Mismatch 422 발생)
        ct.validate_renewal_cap(new_deposit=dep, new_rent=rent)

        dep_inc = round(((dep - ct.deposit) / ct.deposit) * 100, 2) if ct.deposit > 0 else 0.0
        rent_inc = round(((rent - ct.monthly_rent) / ct.monthly_rent) * 100, 2) if ct.monthly_rent > 0 else 0.0
        is_same = (dep == ct.deposit and rent == ct.monthly_rent and maint == ct.maintenance_fee)

        self.tenant_plan = "STAY"
        self.current_deposit = ct.deposit
        self.current_monthly_rent = ct.monthly_rent
        self.current_maintenance_fee = ct.maintenance_fee
        self.proposed_deposit = dep
        self.proposed_monthly_rent = rent
        self.proposed_maintenance_fee = maint
        self.proposed_term_months = months
        self.deposit_increase_pct = dep_inc
        self.rent_increase_pct = rent_inc
        self.renewal_type = "SAME_TERMS" if is_same else "ADJUSTED_WITHIN_5PCT"
        self.renewal_note = (renewal_note or "").strip() or (
            "종전과 동일한 임대 조건으로 연장 재계약을 예고합니다."
            if is_same else f"주택임대차보호법 5% 상한 이내(월세 {rent_inc:+.2f}%)로 조정된 연장 재계약 조건을 예고합니다."
        )

        if open_cosign_session:
            from .cosign import CoSignSession
            sess = CoSignSession.get(self.cosign_session_id) if self.cosign_session_id else None
            if not sess or sess.status != "IN_PROGRESS":
                sess = CoSignSession.open_session(
                    branch=self.branch,
                    room_no=self.room_no,
                    tenant_name=ct.tenant_name,
                    tenant_phone=ct.tenant_phone,
                    mode="RENEWAL",
                    app_source=app_source,
                )
            sess.update_terms(
                role="lessor",
                updates={
                    "deposit": dep,
                    "monthly_rent": rent,
                    "maintenance_fee": maint,
                    "lease_term_months": months,
                    "move_in_date": ct.expiry_date or date_today().isoformat(),
                    "special_terms": (
                        f"[만기 연장 재계약 예고 반영] {self.renewal_note}\n"
                        "자연 마모 수선은 임대인 부담이며, 종전 입실 점검표를 승계함."
                    ),
                },
                app_source=app_source,
            )
            self.cosign_session_id = sess.id
            self.cosign_session_code = sess.session_code

        self.status = "RENEWAL_PREVIEWED"
        self.renewal_preview_sent_at = now_iso()
        self.save(action="renewal_contract_preview_sent", app_source=app_source)

        type_label = "동일조건 연장" if is_same else f"5% 이내 조정({rent_inc:+.1f}%)"
        code_info = f" · 공동재계약 코드 [{self.cosign_session_code}]" if self.cosign_session_code else ""
        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role="lessor",
            sender_name=ct.lessor_name or "임대인",
            message=(
                f"[📝 연장 재계약 예고 도착 — {type_label}] 보증금 {dep:,}원 / 월세 {rent:,}원 / "
                f"관리비 {maint:,}원 ({months}개월 연장){code_info}. 마이홈에서 예고 조건 확인 및 전자서명을 진행해 주세요."
            ),
            quick_action_label="공동 재계약방 열기" if self.cosign_session_id else "",
            quick_action_url=f"/rent3_contract/?session={self.cosign_session_id}&role=tenant" if self.cosign_session_id else "",
        ).save(action="chat_auto_sent", app_source=app_source)

        return self

    def acknowledge_renewal_preview(
        self,
        tenant_memo: str = "",
        app_source: str = "rent3_tenant",
    ) -> "ExpiryRenewalCheck":
        if self.status not in ("RENEWAL_PREVIEWED", "RENEWAL_ACKNOWLEDGED"):
            raise DomainError("아직 임대인의 연장 재계약 예고가 발송되지 않았습니다.")
        if tenant_memo.strip():
            self.tenant_memo = tenant_memo.strip()
        self.status = "RENEWAL_ACKNOWLEDGED"
        self.renewal_acknowledged_at = now_iso()
        self.save(action="renewal_preview_acknowledged_by_tenant", app_source=app_source)

        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role="tenant",
            sender_name=self.tenant_name,
            message=(
                f"[✅ 연장 재계약 예고 동의 완료] {self.room_no}호 {self.tenant_name} 세입자가 예고된 연장 조건"
                f"(보 {self.proposed_deposit:,}원 / 월 {self.proposed_monthly_rent:,}원)을 확인·동의했습니다."
            ),
        ).save(action="chat_auto_sent", app_source=app_source)
        return self


