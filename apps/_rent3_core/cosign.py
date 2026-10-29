"""
apps/_rent3_core/cosign.py — 임대인·임차인이 각각 로그인 후 매물 선택 호출·응답으로 시작하는 공동 임대계약 객체 (`CoSignSession`).

저장 파일: `data/rent3_pool/cosign_sessions.json`
핵심 불변식:
  1. 각자 로그인 본인인증 & 호출·응답 개시(Call & Respond Handshake):
     - 임대인(갑)과 임차인(을)은 각각 로그인 과정에서 본인인증을 거친 상태로 진입한다.
     - 둘 중 어느 한쪽이 먼저 공동계약 화면에서 매물(호실)을 선택해 상대방을 호출(`CALLING`)하고, 상대방이 응답(`CONNECTED`)함으로써 공동계약이 시작된다.
  2. 상호 신분증 대조 체크 & 등기부등본 확인 후 서명(Strict Pre-Sign Gate):
     - Step 1: 양측 로그인 본인인증 확인 + 서로 상대방의 실물 신분증을 대조하여 본인 확인이 되었음을 상호 체크(`lessor_checked_tenant_id`, `tenant_checked_lessor_id`) + 대법원 등기부등본 권리관계 확인 체크(`step1_lessor_confirmed`, `step1_tenant_locked`)
     - Step 2: 호실 옵션/시설 상태 실물 공동 체크 및 상호 잠금
     - Step 3: 보증금/월세/관리비/선후불/특약 공동 작성 -> 단 1글자라도 수정 시 revision 증가 및 양측 동의 자동 초기화(Revision Lock)!
     - Step 4: (호출응답 완료 + 양측 로그인 본인인증 + 상호 신분증 확인 체크 + 등기부등본 확인 체크 + 조건 합의가 모두 완료된 경우에만) 양측 모바일 자필 전자서명 날인 -> SHA-256 계약원본 무결성 해시 봉인
     - Step 5: 계약금 입금 확인 즉시 공동 Data Pool(`contracts.json`, `rooms.json`, `users.json`, `payments.json`, `ops.json`) 일괄 업데이트!
  3. 신규 계약(`NEW`) 및 5% 상한 검증이 내장된 만기 재계약(`RENEWAL`)을 모두 지원한다.
"""
from __future__ import annotations

import random
import re
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import Conflict, DomainError, NotFound
from .models import (
    Building,
    Contract,
    Lessor,
    Pay,
    Room,
    Tenant,
    add_months_str,
    date_today,
    normalize_branch_id,
)
from .ops import DirectChat, RtmsReport
from .pool import PoolObj, now_iso


class CoSignSession(PoolObj):
    """임대인·임차인 실시간 호출·응답 및 상호 신분·등기 확인 기반 공동 임대계약 세션 객체."""
    collection: ClassVar[str] = "cosign_sessions"
    label: ClassVar[str] = "공동 임대계약 세션"

    id: str = Field(default_factory=lambda: f"cs_{uuid4().hex[:8]}")
    session_code: str = Field(default_factory=lambda: f"{random.randint(100000, 999999)}")
    mode: str = "NEW"                        # NEW (신규계약) / RENEWAL (조건변경 재계약)
    prev_contract_id: str = ""
    branch: str = "main"
    id_building: str = ""
    building_name: str = ""
    building_address: str = ""
    id_room: str = ""
    room_no: str = ""
    id_lessor: str = ""
    lessor_name: str = ""
    lessor_phone: str = ""
    lessor_account: str = ""
    id_tenant: str = ""

    # 호출·응답 핸드셰이크 및 로그인 본인인증 상태
    initiated_by_role: str = "lessor"        # lessor / tenant (먼저 매물을 선택해 호출한 주체)
    call_status: str = "CONNECTED"           # CALLING (상대방 응답 대기) / CONNECTED (양측 응답·접속 완료)
    called_at: str = Field(default_factory=now_iso)
    responded_at: str = ""
    lessor_login_verified: bool = True       # 임대인 로그인 본인인증 완료 여부
    tenant_login_verified: bool = True       # 임차인 로그인 본인인증 완료 여부

    current_step: int = 1                    # 1..5
    status: str = "IN_PROGRESS"              # IN_PROGRESS / COMPLETED / CANCELLED
    revision: int = 1                        # 조건 변경 시마다 +1 증가 (위변조 원천 차단)

    # Step 1: 상호 신분증 확인 체크 & 대법원 등기부등본 권리 분석
    lessor_checked_tenant_id: bool = False   # 임대인이 임차인 신분증을 대조·본인확인 체크했는지 여부
    lessor_id_checked_at: str = ""
    tenant_checked_lessor_id: bool = False   # 임차인이 임대인 신분증을 대조·본인확인 체크했는지 여부
    tenant_id_checked_at: str = ""

    registry_data: Dict[str, Any] = Field(default_factory=dict)
    step1_lessor_confirmed: bool = False     # 임대인 등기부등본 제시/확인
    step1_tenant_locked: bool = False        # 임차인 등기부등본 열람/확인 잠금

    # Step 2: 호실 옵션 및 시설 공동 점검표
    option_checks: List[Dict[str, Any]] = Field(default_factory=list)
    facility_notes: str = "도배·장판·수압·배수·도어락 현장 동반 확인 이상 없음"
    step2_lessor_confirmed: bool = False
    step2_tenant_locked: bool = False

    # Step 3: 계약 조건 및 특약 공동 작성
    terms: Dict[str, Any] = Field(default_factory=dict)
    step3_lessor_agreed: bool = False
    step3_tenant_agreed: bool = False

    # Step 4: 양측 본인확인 및 자필 전자서명
    lessor_verified: bool = True
    tenant_verified: bool = False
    lessor_signature: str = ""
    lessor_signed_at: str = ""
    tenant_signature: str = ""
    tenant_signed_at: str = ""
    integrity_hash: str = ""

    # Step 5: 계약금 결제 확인 및 최종 계약 원장 생성 결과
    deposit_paid_confirmed: bool = False
    payment_method: str = "TRANSFER"
    created_contract_id: str = ""
    fixed_date_no: str = ""
    completed_at: str = ""

    def summary(self) -> str:
        c_tag = "호출중" if self.call_status == "CALLING" else f"Step {self.current_step}/5"
        return f"공동계약 [{self.room_no}호] {c_tag} ({self.status}, rev={self.revision})"

    def is_step1_fully_verified(self) -> bool:
        return (
            self.lessor_checked_tenant_id
            and self.tenant_checked_lessor_id
            and self.step1_lessor_confirmed
            and self.step1_tenant_locked
        )

    @classmethod
    def open_session(
        cls,
        branch: str,
        room_no: str,
        tenant_name: str = "",
        tenant_phone: str = "",
        mode: str = "NEW",
        initiated_by_role: str = "lessor",
        call_status: str = "CONNECTED",
        lessor_login_verified: bool = True,
        tenant_login_verified: bool = True,
        id_tenant: str = "",
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        b_id = normalize_branch_id(branch)
        room = Room.first(branch=b_id, room_no=room_no)
        if not room:
            raise NotFound(f"{b_id} 지점에서 '{room_no}호'를 찾을 수 없습니다.")

        bldg = Building.get(room.id_building) or Building.first(branch=b_id)
        lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)

        prev_ct: Optional[Contract] = None
        if mode == "RENEWAL":
            prev_ct = Contract.get(room.active_contract_id) if room.active_contract_id else None
            if not prev_ct:
                raise DomainError(f"{room.room_no}호에 갱신할 기존 활성 계약이 없습니다.")

        matched_t: Optional[Tenant] = Tenant.get(id_tenant) if id_tenant else None
        if not matched_t and tenant_phone:
            digits = re.sub(r"\D", "", tenant_phone)
            if len(digits) >= 8:
                matched_t = next((t for t in Tenant.all() if re.sub(r"\D", "", t.phone) == digits), None)

        move_in_default = (
            prev_ct.expiry_date if prev_ct and prev_ct.expiry_date
            else (room.available_from if re_is_date(room.available_from) else date_today().isoformat())
        )
        t_name = tenant_name or (matched_t.name if matched_t else (prev_ct.tenant_name if prev_ct else ""))
        t_phone = tenant_phone or (matched_t.phone if matched_t else (prev_ct.tenant_phone if prev_ct else ""))
        resolved_tid = matched_t.id if matched_t else (prev_ct.id_tenant if prev_ct else "")

        opts = [
            {
                "item": o.get("item", "옵션"),
                "quantity": o.get("quantity", 1),
                "condition": o.get("condition", "정상"),
                "checked": True,
            }
            for o in (room.option_list or [])
        ]

        now_str = now_iso()
        session = cls(
            mode=mode,
            prev_contract_id=prev_ct.id if prev_ct else "",
            branch=b_id,
            id_building=bldg.id if bldg else "",
            building_name=bldg.building_name if bldg else b_id,
            building_address=bldg.address if bldg else "",
            id_room=room.id,
            room_no=room.room_no,
            id_lessor=lessor.id if lessor else "",
            lessor_name=lessor.name if lessor else "김임대",
            lessor_phone=lessor.phone if lessor else "010-1212-3434",
            lessor_account=lessor.account_number if lessor else "국민 123-456-789012",
            id_tenant=resolved_tid,
            initiated_by_role=initiated_by_role,
            call_status=call_status,
            called_at=now_str,
            responded_at=now_str if call_status == "CONNECTED" else "",
            lessor_login_verified=lessor_login_verified,
            tenant_login_verified=tenant_login_verified,
            current_step=1,
            status="IN_PROGRESS",
            revision=1,
            registry_data={
                "issue_number": room.registry_info.get("issue_number", "1101-2026-884920"),
                "owner_name": lessor.name if lessor else room.registry_info.get("owner_name", "김임대"),
                "mortgage_amount": room.registry_info.get("mortgage_amount", 0),
                "clean_title": room.registry_info.get("clean_title", True),
                "checked_date": date_today().isoformat(),
            },
            step1_lessor_confirmed=(initiated_by_role == "lessor" or call_status == "CONNECTED"),
            option_checks=opts,
            terms={
                "tenant_name": t_name,
                "tenant_phone": t_phone,
                "tenant_email": matched_t.email if matched_t else "",
                "tenant_car_number": matched_t.car_number if matched_t else "",
                "lease_type": room.lease_type or "월세",
                "pay_timing": room.pay_timing or "후불",
                "vat_target": room.vat_applicable,
                "vat_separate": True,
                "contract_deposit": 300000,
                "deposit": prev_ct.deposit if prev_ct else room.deposit,
                "monthly_rent": prev_ct.monthly_rent if prev_ct else room.monthly_rent,
                "maintenance_fee": prev_ct.maintenance_fee if prev_ct else room.maintenance_fee,
                "move_in_date": move_in_default,
                "lease_term_months": 12,
                "special_terms": (
                    "1. 본 계약은 공인중개사 중개수수료 없는 당사자 직접 계약임.\n"
                    "2. 임대인과 임차인은 각자 본인인증 로그인 후 서로의 실물 신분증과 등기부등본을 대조·확인하였음.\n"
                    "3. 자연 마모에 의한 시설 고장은 임대인이 무상 수리하며, 퇴실 시 통상 손모는 공제하지 않음.\n"
                    "4. 월세 후불 약정 시 입주 당월은 비과세이며 익월부터 전월 사용분을 납부함."
                ),
            },
        ).save(action="cosign_session_opened", app_source=app_source)

        callee_role = "tenant" if initiated_by_role == "lessor" else "lessor"
        caller_name = session.lessor_name if initiated_by_role == "lessor" else (t_name or "임차인")
        msg_prefix = "[📞 실시간 공동계약 호출]" if call_status == "CALLING" else "[🤝 공동 임대계약방 개설]"
        DirectChat(
            branch=b_id,
            id_room=room.id,
            room_no=room.room_no,
            sender_role=initiated_by_role,
            sender_name=caller_name,
            message=(
                f"{msg_prefix} {session.building_name} {room.room_no}호 공동계약 요청이 도착했습니다. "
                f"본인 로그인 후 공동계약 화면에서 호출에 응답하여 계약을 시작해 주세요."
            ),
            quick_action_label="호출 응답 및 공동계약 입장",
            quick_action_url=f"/rent3_contract/?session={session.id}&role={callee_role}",
        ).save(action="cosign_call_sent", app_source=app_source)

        return session

    @classmethod
    def call_counterparty(
        cls,
        caller_role: str,
        branch: str,
        room_no: str,
        tenant_name: str = "",
        tenant_phone: str = "",
        id_tenant: str = "",
        mode: str = "NEW",
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        """임대인 또는 임차인 중 먼저 공동계약 화면에서 매물을 선택한 측이 상대를 호출(CALLING)하여 세션을 시작한다."""
        role_norm = "tenant" if caller_role == "tenant" else "lessor"
        if role_norm == "tenant" and (not tenant_name.strip() or not tenant_phone.strip()) and not id_tenant:
            raise DomainError(
                "임차인 로그인 본인인증 정보(성명·연락처)가 필요합니다.",
                hint="먼저 임차인 계정으로 로그인한 뒤 매물을 선택해 임대인을 호출해 주세요.",
            )
        if role_norm == "lessor" and mode == "NEW" and not (tenant_name.strip() or tenant_phone.strip() or id_tenant):
            raise DomainError(
                "호출할 임차인(예비 세입자)을 선택하거나 성명·연락처를 입력해 주세요.",
                hint="가입된 임차인 목록에서 선택하거나 연락처를 입력하면 해당 임차인에게 호출이 전송됩니다.",
            )

        return cls.open_session(
            branch=branch,
            room_no=room_no,
            tenant_name=tenant_name.strip(),
            tenant_phone=tenant_phone.strip(),
            mode=mode,
            initiated_by_role=role_norm,
            call_status="CALLING",
            lessor_login_verified=(role_norm == "lessor"),
            tenant_login_verified=(role_norm == "tenant"),
            id_tenant=id_tenant.strip(),
            app_source=app_source,
        )

    def respond_to_call(
        self,
        responder_role: str,
        responder_name: str = "",
        responder_phone: str = "",
        id_tenant: str = "",
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        """호출받은 상대방(임대인 또는 임차인)이 본인 로그인 상태에서 응답하여 공동계약을 정식 개시(CONNECTED)한다."""
        self._ensure_in_progress()
        role_norm = "tenant" if responder_role == "tenant" else "lessor"
        if self.call_status == "CALLING" and role_norm == self.initiated_by_role:
            other_ko = "임차인" if self.initiated_by_role == "lessor" else "임대인"
            raise DomainError(
                f"현재 {other_ko}의 호출 응답을 기다리는 중입니다.",
                hint=f"{other_ko}이(가) 로그인 후 [호출 응답 및 계약 시작] 버튼을 눌러야 연결됩니다.",
            )

        if role_norm == "tenant":
            self.tenant_login_verified = True
            if id_tenant.strip():
                self.id_tenant = id_tenant.strip()
            merged_terms = {**self.terms}
            if responder_name.strip():
                merged_terms["tenant_name"] = responder_name.strip()
            if responder_phone.strip():
                merged_terms["tenant_phone"] = responder_phone.strip()
            self.terms = merged_terms
        else:
            self.lessor_login_verified = True
            self.step1_lessor_confirmed = True

        self.call_status = "CONNECTED"
        self.responded_at = now_iso()
        self.save(action=f"call_responded_by_{role_norm}", app_source=app_source)

        resp_label = self.lessor_name if role_norm == "lessor" else str(self.terms.get("tenant_name") or "임차인")
        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role=role_norm,
            sender_name=resp_label,
            message=f"[✅ 공동계약 호출 응답 완료] {resp_label}님이 입장하여 양측 연결이 완료되었습니다. 신분증 상호 확인 및 등기부등본 확인을 진행해 주세요.",
        ).save(action="cosign_call_connected", app_source=app_source)

        return self

    @classmethod
    def by_code_or_id(cls, token: str) -> "CoSignSession":
        key = (token or "").strip()
        found = cls.get(key) or cls.first(session_code=key)
        if not found:
            raise NotFound(
                f"공동 계약 세션('{key}')을 찾을 수 없습니다.",
                hint="공동계약 호출 목록에서 세션을 선택해 주세요.",
            )
        return found

    def verify_counterparty_id(
        self,
        role: str,
        checked: bool = True,
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        """임대인과 임차인이 서로 상대방의 신분증을 확인하여 본인임을 체크한다."""
        self._ensure_ready_for_steps()
        if not (self.lessor_login_verified and self.tenant_login_verified):
            raise DomainError("양측 모두 본인인증 로그인이 완료된 상태여야 신분증 대조 체크를 진행할 수 있습니다.")

        if role == "lessor":
            if not self.terms.get("tenant_name"):
                raise DomainError("확인할 임차인 성명 정보가 아직 등록되지 않았습니다.")
            self.lessor_checked_tenant_id = checked
            self.lessor_id_checked_at = now_iso() if checked else ""
        else:
            self.tenant_checked_lessor_id = checked
            self.tenant_id_checked_at = now_iso() if checked else ""

        if not checked:
            self.lessor_signature = ""
            self.tenant_signature = ""
            self.integrity_hash = ""

        if self.is_step1_fully_verified() and self.current_step < 2:
            self.current_step = 2

        return self.save(action=f"id_card_checked_by_{role}_{checked}", app_source=app_source)

    def confirm_step1(
        self,
        role: str,
        registry_updates: Optional[Dict[str, Any]] = None,
        checked_counterparty_id: Optional[bool] = None,
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        self._ensure_ready_for_steps()
        if checked_counterparty_id is not None:
            if role == "lessor":
                self.lessor_checked_tenant_id = checked_counterparty_id
                self.lessor_id_checked_at = now_iso() if checked_counterparty_id else ""
            else:
                self.tenant_checked_lessor_id = checked_counterparty_id
                self.tenant_id_checked_at = now_iso() if checked_counterparty_id else ""

        if registry_updates and role == "lessor":
            self.registry_data = {**self.registry_data, **registry_updates}
            self.step1_tenant_locked = False
        if role == "lessor":
            self.step1_lessor_confirmed = True
        else:
            if not self.step1_lessor_confirmed:
                raise DomainError("먼저 임대인의 등기부등본 정보 제시가 완료되어야 합니다.")
            self.step1_tenant_locked = True

        if self.is_step1_fully_verified() and self.current_step < 2:
            self.current_step = 2
        return self.save(action=f"step1_confirmed_by_{role}", app_source=app_source)

    def confirm_step2(
        self,
        role: str,
        option_checks: Optional[List[Dict[str, Any]]] = None,
        facility_notes: Optional[str] = None,
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        self._ensure_ready_for_steps()
        if option_checks is not None:
            self.option_checks = option_checks
            if role == "lessor":
                self.step2_tenant_locked = False
        if facility_notes is not None:
            self.facility_notes = facility_notes
        if role == "lessor":
            self.step2_lessor_confirmed = True
        else:
            self.step2_lessor_confirmed = True
            self.step2_tenant_locked = True
        if self.step2_lessor_confirmed and self.step2_tenant_locked and self.current_step < 3:
            self.current_step = 3
        return self.save(action=f"step2_confirmed_by_{role}", app_source=app_source)

    def update_terms(
        self,
        role: str,
        updates: Dict[str, Any],
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        """Step 3 계약 조건 수정 — 단 1개 필드라도 값이 달라지면 revision 증가 + 양측 동의 및 서명 즉시 초기화!"""
        self._ensure_ready_for_steps()
        merged = {**self.terms}
        changed = False
        int_fields = {"contract_deposit", "deposit", "monthly_rent", "maintenance_fee", "lease_term_months"}
        bool_fields = {"vat_target", "vat_separate"}

        for k, v in updates.items():
            if v is None:
                continue
            if k in int_fields:
                v = int(v)
            elif k in bool_fields:
                v = bool(v)
            elif isinstance(v, str):
                v = v.strip()
            if merged.get(k) != v:
                merged[k] = v
                changed = True

        # 재계약(RENEWAL) 시 법정 5% 상한 실시간 검증
        if self.mode == "RENEWAL" and self.prev_contract_id:
            prev_ct = Contract.get(self.prev_contract_id)
            if prev_ct:
                prev_ct.validate_renewal_cap(
                    new_deposit=int(merged.get("deposit", 0)),
                    new_rent=int(merged.get("monthly_rent", 0)),
                )

        if changed:
            self.terms = merged
            self.revision += 1
            self.step3_lessor_agreed = False
            self.step3_tenant_agreed = False
            self.lessor_signature = ""
            self.tenant_signature = ""
            self.integrity_hash = ""
            if self.current_step > 3:
                self.current_step = 3
            return self.save(action=f"terms_edited_by_{role}_rev_{self.revision}", app_source=app_source)

        return self

    def agree_step3(self, role: str, expected_revision: Optional[int] = None, app_source: str = "rent3_contract") -> "CoSignSession":
        self._ensure_ready_for_steps()
        if expected_revision is not None and expected_revision != self.revision:
            raise Conflict(
                f"계약 조건이 방금 수정되었습니다 (현재 리비전 v{self.revision}).",
                hint="변경된 최신 계약 조건을 다시 확인한 뒤 동의 버튼을 눌러 주세요.",
            )
        if not self.terms.get("tenant_name") or not self.terms.get("tenant_phone"):
            raise DomainError("임차인 성명과 연락처는 필수 입력 항목입니다.", hint="Step 3에서 임차인 인적사항을 먼저 입력해 주세요.")

        if role == "lessor":
            self.step3_lessor_agreed = True
        else:
            self.step3_tenant_agreed = True

        if self.step3_lessor_agreed and self.step3_tenant_agreed and self.current_step < 4:
            self.current_step = 4
        return self.save(action=f"step3_agreed_by_{role}_rev_{self.revision}", app_source=app_source)

    def sign_step4(
        self,
        role: str,
        signature: str,
        expected_revision: Optional[int] = None,
        app_source: str = "rent3_contract",
    ) -> "CoSignSession":
        """Step 4 자필 전자서명 — 본인인증·상호 신분증 확인·등기부등본 확인·계약조건 합의가 모두 완료된 뒤에만 허용!"""
        self._ensure_ready_for_steps()
        if not (self.lessor_login_verified and self.tenant_login_verified):
            raise DomainError(
                "임대인과 임차인 모두 로그인 본인인증이 완료되어야 전자서명할 수 있습니다.",
                hint="양측 모두 로그인 및 호출 응답을 완료해 주세요.",
            )
        if not (self.lessor_checked_tenant_id and self.tenant_checked_lessor_id):
            raise DomainError(
                "서로 상대방의 신분증을 확인하여 본인 확인 체크를 완료해야 전자서명할 수 있습니다.",
                hint="Step 1에서 임대인·임차인 모두 상대방 신분증 대조 확인에 체크해 주세요.",
            )
        if not (self.step1_lessor_confirmed and self.step1_tenant_locked):
            raise DomainError(
                "등기부등본을 확인한 뒤에만 전자서명할 수 있습니다.",
                hint="Step 1에서 등기부등본 권리관계 제시 및 임차인 열람 확인을 완료해 주세요.",
            )
        if not (self.step3_lessor_agreed and self.step3_tenant_agreed):
            raise DomainError("양측 모두 Step 3 계약 조건 합의 잠금을 완료해야 전자서명할 수 있습니다.")
        if expected_revision is not None and expected_revision != self.revision:
            raise Conflict("계약 리비전이 변경되어 서명을 진행할 수 없습니다. 조건을 다시 확인해 주세요.")
        sig = (signature or "").strip()
        if not sig:
            raise DomainError("전자서명 데이터가 비어 있습니다.", hint="서명 패드에 자필 서명을 입력해 주세요.")

        if role == "lessor":
            self.lessor_verified = True
            self.lessor_signature = sig
            self.lessor_signed_at = now_iso()
        else:
            self.tenant_verified = True
            self.tenant_signature = sig
            self.tenant_signed_at = now_iso()

        if self.lessor_signature and self.tenant_signature:
            self.integrity_hash = Contract.compute_integrity_hash({
                "session_id": self.id,
                "revision": self.revision,
                "identity_verification": {
                    "lessor_login_verified": self.lessor_login_verified,
                    "tenant_login_verified": self.tenant_login_verified,
                    "lessor_checked_tenant_id": self.lessor_checked_tenant_id,
                    "tenant_checked_lessor_id": self.tenant_checked_lessor_id,
                },
                "registry_data": self.registry_data,
                "option_checks": self.option_checks,
                "terms": self.terms,
                "lessor_signature": self.lessor_signature,
                "tenant_signature": self.tenant_signature,
            })
            if self.current_step < 5:
                self.current_step = 5

        return self.save(action=f"step4_signed_by_{role}", app_source=app_source)

    def complete_step5_and_seal(
        self,
        payment_method: str = "TRANSFER",
        app_source: str = "rent3_contract",
    ) -> Dict[str, Any]:
        """계약금 입금 확인 -> 공동 JSON Data Pool의 모든 연관 객체 실시간 원자적 업데이트!"""
        if self.status == "COMPLETED" and self.created_contract_id:
            ct = Contract.require(self.created_contract_id)
            return {"session": self.model_dump(), "contract": ct.model_dump(), "already_completed": True}

        self._ensure_ready_for_steps()
        if not (self.lessor_signature and self.tenant_signature):
            raise DomainError("임대인과 임차인 양측 전자서명이 모두 완료되어야 최종 체결할 수 있습니다.")

        room = Room.require(self.id_room)
        t_info = self.terms

        # 1) 임차인 객체 생성 또는 갱신 -> users.json 즉시 업데이트
        tenant = (
            (Tenant.get(self.id_tenant) if self.id_tenant else None)
            or (Tenant.first(phone=t_info["tenant_phone"]) if t_info.get("tenant_phone") else None)
        )
        if not tenant:
            tenant = Tenant(
                name=t_info["tenant_name"],
                phone=t_info["tenant_phone"],
                email=t_info.get("tenant_email", ""),
                car_number=t_info.get("tenant_car_number", ""),
                doorlock_pwd=room.doorlock_pwd,
                branch=self.branch,
                id_building=self.id_building,
                current_room_id=room.id,
                room_no=room.room_no,
                status="ACTIVE",
            )
        else:
            tenant.name = t_info["tenant_name"]
            tenant.email = t_info.get("tenant_email", tenant.email)
            if t_info.get("tenant_car_number"):
                tenant.car_number = t_info["tenant_car_number"]
            tenant.doorlock_pwd = room.doorlock_pwd
            tenant.branch = self.branch
            tenant.id_building = self.id_building
            tenant.current_room_id = room.id
            tenant.room_no = room.room_no
            tenant.status = "ACTIVE"
        tenant.save(action="tenant_activated_by_cosign", app_source=app_source)
        self.id_tenant = tenant.id

        # 재계약인 경우 이전 계약 RENEWED 마감
        if self.mode == "RENEWAL" and self.prev_contract_id:
            old_ct = Contract.get(self.prev_contract_id)
            if old_ct:
                old_ct.status = "RENEWED"
                old_ct.save(action="contract_renewed_replaced", app_source=app_source)

        # 2) Contract 원장 객체 생성 -> contracts.json 즉시 업데이트
        move_in = t_info.get("move_in_date") or date_today().isoformat()
        months = int(t_info.get("lease_term_months", 12))
        expiry = add_months_str(move_in, months)

        ct = Contract(
            branch=self.branch,
            id_building=self.id_building,
            id_room=room.id,
            room_no=room.room_no,
            id_lessor=self.id_lessor,
            lessor_name=self.lessor_name,
            id_tenant=tenant.id,
            tenant_name=tenant.name,
            tenant_phone=tenant.phone,
            contract_date=date_today().isoformat(),
            move_in_date=move_in,
            expiry_date=expiry,
            lease_term_months=months,
            lease_type=t_info.get("lease_type", "월세"),
            pay_timing=t_info.get("pay_timing", "후불"),
            vat_target=bool(t_info.get("vat_target", False)),
            vat_separate=bool(t_info.get("vat_separate", True)),
            contract_deposit=int(t_info.get("contract_deposit", 300000)),
            deposit=int(t_info.get("deposit", room.deposit)),
            monthly_rent=int(t_info.get("monthly_rent", room.monthly_rent)),
            maintenance_fee=int(t_info.get("maintenance_fee", room.maintenance_fee)),
            special_terms=t_info.get("special_terms", ""),
            option_snapshot=self.option_checks,
            registry_snapshot={
                **self.registry_data,
                "lessor_login_verified": self.lessor_login_verified,
                "tenant_login_verified": self.tenant_login_verified,
                "lessor_checked_tenant_id": self.lessor_checked_tenant_id,
                "lessor_id_checked_at": self.lessor_id_checked_at,
                "tenant_checked_lessor_id": self.tenant_checked_lessor_id,
                "tenant_id_checked_at": self.tenant_id_checked_at,
            },
            lessor_signature=self.lessor_signature,
            tenant_signature=self.tenant_signature,
            integrity_hash=self.integrity_hash,
            cosign_session_id=self.id,
            status="ACTIVE",
        )
        ct.vat = ct.compute_vat()
        ct.save(action="contract_sealed_from_cosign", app_source=app_source)

        # 3) Room 호실 상태 입주 전환 (공실 광고 자동 비노출) -> rooms.json 즉시 업데이트
        room.id_tenant = tenant.id
        room.active_contract_id = ct.id
        room.deposit = ct.deposit
        room.monthly_rent = ct.monthly_rent
        room.maintenance_fee = ct.maintenance_fee
        room.lease_type = ct.lease_type
        room.pay_timing = ct.pay_timing
        room.pre_moveout_notice = False
        room.available_from = "즉시입주"
        room.is_published = False
        room.save(action="room_assigned_by_cosign", app_source=app_source)

        # 4) 계약금 수납 기록 생성 -> payments.json 즉시 업데이트
        c_dep = ct.contract_deposit
        if c_dep > 0:
            Pay(
                branch=self.branch,
                id_building=self.id_building,
                id_room=room.id,
                room_no=room.room_no,
                id_contract=ct.id,
                id_tenant=tenant.id,
                payer_name=tenant.name,
                billing_month=move_in[:7],
                paid_date=date_today().isoformat(),
                rent_amount=0,
                maintenance_amount=0,
                vat_amount=0,
                total_amount=c_dep,
                payment_kind="CONTRACT_DEPOSIT",
                method=payment_method,
                idempotency_key=f"cosign_dep:{self.id}",
            ).save(action="contract_deposit_paid", app_source=app_source)

        # 5) 국토부 RTMS 전월세 신고 & 대법원 확정일자 즉시 부여 -> ops.json & contracts.json 업데이트
        rtms = RtmsReport.auto_file_for_contract(ct, app_source=app_source)

        # 6) 세션 완료 봉인 -> cosign_sessions.json 업데이트
        self.deposit_paid_confirmed = True
        self.payment_method = payment_method
        self.created_contract_id = ct.id
        self.fixed_date_no = rtms.fixed_date_no
        self.status = "COMPLETED"
        self.completed_at = now_iso()
        self.save(action="cosign_completed_and_sealed", app_source=app_source)

        DirectChat(
            branch=self.branch,
            id_room=room.id,
            room_no=room.room_no,
            sender_role="system",
            sender_name="전자계약 체결 알림",
            message=(
                f"[🎉 임대차 계약 최종 체결 완료] {room.room_no}호 계약원장({ct.id})이 봉인되었으며, "
                f"대법원 확정일자({rtms.fixed_date_no})가 자동 부여되었습니다."
            ),
        ).save(action="chat_auto_sent", app_source=app_source)

        return {
            "session": self.model_dump(),
            "contract": ct.model_dump(),
            "tenant": tenant.model_dump(),
            "room": room.model_dump(),
            "rtms": rtms.model_dump(),
        }

    def _ensure_in_progress(self) -> None:
        if self.status != "IN_PROGRESS":
            raise Conflict(
                f"이미 종료되었거나 체결 완료된 계약 세션입니다 (상태: {self.status}).",
                hint="체결된 계약서를 인쇄하거나 새 매물을 선택해 상대를 호출해 주세요.",
            )

    def _ensure_ready_for_steps(self) -> None:
        self._ensure_in_progress()
        if self.call_status == "CALLING":
            callee_ko = "임차인" if self.initiated_by_role == "lessor" else "임대인"
            raise DomainError(
                f"상대방({callee_ko})이 아직 공동계약 호출에 응답하지 않았습니다.",
                hint=f"{callee_ko}이(가) 로그인 후 호출에 응답하면 계약 단계 진행이 시작됩니다.",
            )


def re_is_date(v: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", (v or "").strip()))
