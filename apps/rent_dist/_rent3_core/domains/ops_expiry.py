"""
apps/_rent3_core/domains/ops_expiry.py — 만기 6~2개월 전 전출계획 확인 및 연장 재계약 예고(ExpiryRenewalCheck) 도메인 객체.
"""
from __future__ import annotations

import time
from typing import ClassVar, Optional, Tuple
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError
from ..models import Contract, Room, date_today, normalize_branch_id
from ..pool import PoolObj, now_iso
from .ops_support import DirectChat


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
    prelist_pending: bool = False            # 전출 예정은 회신했지만 아직 사전 매물 공개일(퇴실 기준일 1개월 전) 전이라 공개를 미뤄 둔 상태
    prelist_open_date: str = ""              # 사전 매물로 공개되는 날(YYYY-MM-DD)

    def summary(self) -> str:
        return f"만기확인 [{self.room_no}호 {self.tenant_name}] {self.status} (만기 {self.expiry_date})"

    _last_due_check: ClassVar[Tuple[str, float]] = ("", 0.0)

    @classmethod
    def activate_due(cls, app_source: str = "rent3_system", force: bool = False) -> int:
        """공개를 미뤄 둔 전출 예정 호실 중 공개일이 된 것을 사전 매물로 올린다(스케줄러가 없어 목록 조회·로그인 때 부른다). 올린 건수를 돌려준다.
        같은 날 60초 안의 반복 호출은 건너뛴다(force=True 는 항상)."""
        today = date_today().isoformat()
        last_day, last_at = cls._last_due_check
        if not force and last_day == today and time.time() - last_at < 60:
            return 0
        cls._last_due_check = (today, time.time())
        n = 0
        for ct in Contract.find(status="SCHEDULED"):        # 미리 체결한 연장 재계약: 시작일(이전 계약 만기일)이 되면 새 조건을 적용한다
            if ct.renews_contract_id and (ct.move_in_date or "9999") <= today:
                room = Room.get(ct.id_room)
                if room is not None and room.reserved_contract_id == ct.id:
                    room.activate_renewal(ct, app_source=app_source)
                    n += 1
        for chk in cls.find(prelist_pending=True):
            if chk.status != "MOVEOUT_PLANNED" or chk.tenant_plan != "MOVE_OUT":
                chk.prelist_pending = False
                chk.save(action="prelisting_pending_dropped", app_source=app_source)
                continue
            if chk.prelist_open_date and today < chk.prelist_open_date:
                continue
            room = Room.get(chk.id_room)
            if room is not None and room.is_occupied and room.active_contract_id == chk.id_contract and room.reservation() is None:
                room.pre_moveout_notice = True
                room.is_published = True
                room.available_from = f"{chk.planned_move_out_date} 입주예정(만기퇴실)"
                room.seed_listing_from_terms()
                room.save(action="room_prelisted_on_open_date", app_source=app_source)
            chk.prelist_pending = False
            chk.save(action="prelisting_opened", app_source=app_source)
            n += 1
        return n

    @classmethod
    def cancel_pending_prelist(cls, contract_id: str, app_source: str = "rent3_lessor") -> None:
        """임대인이 사전 매물 공개를 직접 거둔 경우 미뤄 둔 자동 공개도 취소한다."""
        for chk in cls.find(id_contract=contract_id, prelist_pending=True):
            chk.prelist_pending = False
            chk.save(action="prelisting_pending_cancelled", app_source=app_source)

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
                f"연장(전출의사 없음) 시에는 월세 5% 범위 내 인상 조건으로 재계약이 진행됩니다. "
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
        if room is not None and room.reservation() is not None:
            if room.reservation_is_renewal():
                if plan == "MOVE_OUT":
                    raise DomainError(
                        "이미 연장 재계약이 체결되어 '전출 예정'으로 바꿀 수 없습니다.",
                        hint="퇴실하려면 임대인이 먼저 연장 재계약 예약을 취소해야 합니다.",
                    )
            elif plan == "STAY":
                raise DomainError(
                    "이미 다음 입주자와 예약 계약이 체결되어 '전출의사 없음'으로 바꿀 수 없습니다.",
                    hint="연장하려면 임대인이 먼저 예약 계약을 취소해야 합니다.",
                )

        if plan == "MOVE_OUT":
            self.status = "MOVEOUT_PLANNED"
            self.planned_move_out_date = (planned_move_out_date or "").strip() or self.expiry_date
            self.save(action="tenant_planned_moveout", app_source=app_source)

            listed_now = False
            if room and prelist_vacancy:
                open_date = room.prelist_open_date(self.planned_move_out_date)
                if not open_date or date_today().isoformat() >= open_date:
                    room.pre_moveout_notice = True
                    room.is_published = True
                    room.available_from = f"{self.planned_move_out_date} 입주예정(만기퇴실)"
                    room.seed_listing_from_terms()
                    room.save(action="room_prelisted_from_moveout_plan", app_source=app_source)
                    listed_now = True
                    self.prelist_pending = False
                else:                           # 너무 일찍 공개하지 않는다: 공개일(퇴실 기준일 1개월 전)이 되면 자동으로 올린다
                    self.prelist_pending = True
                    self.prelist_open_date = open_date
                self.save(action="prelisting_scheduled" if self.prelist_pending else "prelisting_opened", app_source=app_source)

            DirectChat(
                branch=self.branch,
                id_room=self.id_room,
                room_no=self.room_no,
                sender_role="tenant",
                sender_name=self.tenant_name,
                message=(
                    f"[📦 만기 전출(퇴실) 예정 회신] {self.room_no}호 {self.tenant_name} 세입자가 "
                    f"{self.planned_move_out_date} 퇴실 예정을 회신했습니다."
                    + (" (공실 쇼케이스에 사전예약 매물로 자동 등록됨)" if listed_now
                       else f" (사전 매물 공개는 퇴실 기준일 1개월 전인 {self.prelist_open_date}부터 자동으로 시작됩니다)" if self.prelist_pending else "")
                ),
            ).save(action="chat_auto_sent", app_source=app_source)
        else:
            self.status = "STAY_CONFIRMED"
            self.planned_move_out_date = ""
            self.prelist_pending = False
            self.save(action="tenant_confirmed_no_moveout", app_source=app_source)

            if room and room.pre_moveout_notice:
                room.pre_moveout_notice = False
                room.next_listing = {}
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
        proposed_cleaning_fee: Optional[int] = None,
    ) -> "ExpiryRenewalCheck":
        if self.tenant_plan == "MOVE_OUT":
            raise DomainError(
                "임차인이 '전출(퇴실) 예정'으로 회신한 상태입니다.",
                hint="연장 재계약 예고를 발송하려면 먼저 전출의사 없음(연장 희망) 상태로 전환해 주세요.",
            )

        ct = Contract.require(self.id_contract)
        room_now = Room.get(self.id_room)
        if room_now is not None and room_now.reservation() is not None:
            raise DomainError("이미 다음 계약(연장 또는 예약)이 체결되어 있어 연장 예고를 다시 보낼 수 없습니다.",
                              hint="체결된 예약 계약을 취소한 뒤에 다시 진행해 주세요.")
        draft = (room_now.next_terms or {}) if room_now else {}      # 임대인이 저장해 둔 '다음 계약 조건'이 있으면 기본값으로 쓴다
        dep = int(draft.get("deposit", ct.deposit)) if proposed_deposit is None else proposed_deposit
        rent = int(draft.get("monthly_rent", ct.monthly_rent)) if proposed_monthly_rent is None else proposed_monthly_rent
        maint = int(draft.get("maintenance_fee", ct.maintenance_fee)) if proposed_maintenance_fee is None else proposed_maintenance_fee
        clean = (int(draft["cleaning_fee"]) if draft.get("cleaning_fee") is not None else (ct.cleaning_fee or 0)) if proposed_cleaning_fee is None else proposed_cleaning_fee
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
            from ..cosign import CoSignSession
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
                    "cleaning_fee": clean,
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
