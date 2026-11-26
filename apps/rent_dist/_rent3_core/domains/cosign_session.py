"""
apps/_rent3_core/domains/cosign_session.py — 공동계약 세션 개설·호출/응답·신분확인·1~2단계 확인 믹스인.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.errors import DomainError, NotFound
from ..models import (
    Building,
    Contract,
    Lessor,
    Room,
    Tenant,
    date_today,
    normalize_branch_id,
)
from ..ops import DirectChat
from ..pool import now_iso
from .cosign_registry import re_is_date

if TYPE_CHECKING:
    from ..cosign import CoSignSession

MAX_LIVE_PER_TENANT = 2  # 한 임차인이 동시에 진행할 수 있는 계약 수 (호실 선점·잠금 악용 방지)


def open_session(
    cls: type[CoSignSession],
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
) -> CoSignSession:
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

    # 입주 중인 전출 예정 호실(사전 계약): 현재 계약 값이 아니라 사전 매물로 올린 조건(as_listing)을 쓰고, 입주일은 예상 퇴실일 이후로 시작한다.
    pre_contract = (mode != "RENEWAL") and room.is_occupied
    listing = room.as_listing() if pre_contract else room
    reserve_after = room.expected_vacate_date() if pre_contract else ""
    move_in_default = (
        prev_ct.expiry_date if prev_ct and prev_ct.expiry_date
        else (reserve_after if reserve_after
              else (room.available_from if re_is_date(room.available_from) else date_today().isoformat()))
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
        for o in (listing.option_list or [])
    ]

    is_renewal = (mode == "RENEWAL")
    nt = dict(room.next_terms or {})
    base_dep = (prev_ct.deposit if prev_ct else listing.deposit) or 0
    base_rent = prev_ct.monthly_rent if prev_ct else listing.monthly_rent
    renewal_rent_5pct = max(base_rent, (int(base_rent * 1.05) // 1000) * 1000)
    # 연장 재계약 시 임대인이 문자 예고(ExpiryRenewalCheck)나 '다음 계약 조건'(next_terms)에 저장해 둔 값(법정 5% 상한 이내)을 우선 적용한다.
    ren_dep = prev_ct.deposit if prev_ct else listing.deposit
    ren_rent = renewal_rent_5pct
    ren_fee = prev_ct.maintenance_fee if prev_ct else listing.maintenance_fee
    ren_clean = prev_ct.cleaning_fee if prev_ct else (listing.cleaning_fee or 0)
    ren_months = 12
    if is_renewal and prev_ct is not None:
        from .ops_expiry import ExpiryRenewalCheck
        chk_prev = ExpiryRenewalCheck.for_contract(prev_ct.id)
        has_notice = bool(chk_prev and chk_prev.status in ("RENEWAL_PREVIEWED", "RENEWAL_ACKNOWLEDGED"))
        if nt:
            cand_dep = int(nt["deposit"]) if nt.get("deposit") is not None else ren_dep
            cand_rent = int(nt["monthly_rent"]) if nt.get("monthly_rent") is not None else prev_ct.monthly_rent
            try:
                prev_ct.validate_renewal_cap(new_deposit=cand_dep, new_rent=cand_rent)
                ren_dep, ren_rent = cand_dep, cand_rent
            except Exception:
                pass
            if nt.get("maintenance_fee") is not None:
                ren_fee = int(nt["maintenance_fee"])
            if nt.get("cleaning_fee") is not None:
                ren_clean = int(nt["cleaning_fee"])
            if nt.get("lease_term_months") is not None:
                ren_months = max(1, int(nt["lease_term_months"]))
        elif has_notice and chk_prev is not None:
            cand_dep = chk_prev.proposed_deposit
            cand_rent = chk_prev.proposed_monthly_rent
            try:
                prev_ct.validate_renewal_cap(new_deposit=cand_dep, new_rent=cand_rent)
                ren_dep, ren_rent = cand_dep, cand_rent
            except Exception:
                pass
            ren_fee = chk_prev.proposed_maintenance_fee
            ren_months = max(1, chk_prev.proposed_term_months or 12)
    new_clean = int(listing.cleaning_fee or nt.get("cleaning_fee") or 0)
    new_months = 12
    extra_spec_terms: List[str] = []
    if not is_renewal and listing.listing_spec:
        from .. import listing_pdf as _lpdf
        norm_spec = _lpdf.normalize_listing_spec(listing.listing_spec, listing.option_list)
        new_months = norm_spec["lease_term_months"]
        extra_spec_terms = _lpdf.build_special_terms_from_spec(norm_spec, cleaning_fee=new_clean)
    base_new_terms = [
        "본 계약은 공인중개사 중개수수료 없는 당사자 직접 계약임.",
        "임대인과 임차인은 각자 본인인증 로그인 후 서로의 실물 신분증과 등기부등본을 대조·확인하였음.",
        "자연 마모에 의한 시설 고장은 임대인이 무상 수리하며, 퇴실 시 통상 손모는 공제하지 않음.",
        "월세 후불 약정 시 입주 당월은 비과세이며 익월부터 전월 사용분을 납부함.",
        *extra_spec_terms,
    ]
    new_special_terms = "\n".join(f"{i}. {line}" for i, line in enumerate(base_new_terms, start=1))
    now_str = now_iso()
    session = cls(
        mode=mode,
        prev_contract_id=prev_ct.id if prev_ct else "",
        reserve_after=reserve_after,
        branch=b_id,
        id_building=bldg.id if bldg else "",
        building_name=bldg.building_name if bldg else b_id,
        building_address=bldg.address if bldg else "",
        id_room=room.id,
        room_no=room.room_no,
        id_lessor=lessor.id if lessor else "",
        lessor_name=lessor.name if lessor else "김임대",
        lessor_phone=lessor.phone if lessor else "010-1212-3434",
        lessor_account=(lessor.account_number or "") if lessor else "",     # 가짜 계좌를 대신 넣지 않는다(미등록이면 빈 값)
        id_tenant=resolved_tid,
        initiated_by_role=initiated_by_role,
        call_status=call_status,
        called_at=now_str,
        responded_at=now_str if call_status == "CONNECTED" else "",
        lessor_login_verified=lessor_login_verified,
        tenant_login_verified=tenant_login_verified,
        current_step=3 if is_renewal else 1,
        status="IN_PROGRESS",
        revision=1,
        # 방에 임대인이 확정한 등기부가 있을 때만 그 값을 쓴다(없으면 비워 '미확인'으로 — 가짜 번호·'깨끗함'을 만들지 않는다)
        registry_data=({
            "issue_number": room.registry_info.get("issue_number", ""),
            "owner_name": lessor.name if lessor else room.registry_info.get("owner_name", ""),
            "mortgage_amount": room.registry_info.get("mortgage_amount", 0),
            "clean_title": bool(room.registry_info.get("clean_title")),
            "checked_date": room.registry_info.get("verified_at") or date_today().isoformat(),
        } if room.registry_checked else {
            "issue_number": "", "owner_name": lessor.name if lessor else "", "mortgage_amount": 0,
            "clean_title": False, "unverified": True, "checked_date": "",
        }),
        lessor_checked_tenant_id=is_renewal,
        tenant_checked_lessor_id=is_renewal,
        step1_lessor_confirmed=True if is_renewal else (initiated_by_role == "lessor" or call_status == "CONNECTED"),
        step1_tenant_locked=is_renewal,
        step2_lessor_confirmed=is_renewal,
        step2_tenant_locked=is_renewal,
        tenant_recheck_at=now_str if is_renewal else "",
        option_checks=opts,
        terms={
            "tenant_name": t_name,
            "tenant_phone": t_phone,
            "tenant_email": matched_t.email if matched_t else (prev_ct.tenant_email if prev_ct else ""),
            "tenant_car_number": matched_t.car_number if matched_t else "",
            "tax_invoice_requested": bool(
                (prev_ct.tax_invoice_requested or prev_ct.tenant_business_number) if prev_ct else False
            ),
            "tenant_business_number": matched_t.business_number if matched_t else (prev_ct.tenant_business_number if prev_ct else ""),
            "tenant_company_name": matched_t.company_name if matched_t else (prev_ct.tenant_company_name if prev_ct else ""),
            "tenant_ceo_name": matched_t.ceo_name if matched_t else (prev_ct.tenant_ceo_name if prev_ct else ""),
            "lease_type": listing.lease_type or "월세",
            "pay_timing": (nt.get("pay_timing") if (is_renewal and nt.get("pay_timing") in ("후불", "선불")) else listing.pay_timing) or "후불",
            "vat_target": prev_ct.vat_target if prev_ct else listing.vat_applicable,
            "vat_separate": True,
            "contract_deposit": 0 if is_renewal else (base_dep // 10),
            "deposit": ren_dep if is_renewal else listing.deposit,
            "monthly_rent": ren_rent if is_renewal else listing.monthly_rent,
            "maintenance_fee": ren_fee if is_renewal else listing.maintenance_fee,
            "cleaning_fee": ren_clean if is_renewal else new_clean,        # 퇴실 청소비 약정(0 = 약정 없음): 협의해 정하면 전출 정산 때 보증금에서 공제
            "move_in_date": move_in_default,
            "lease_term_months": ren_months if is_renewal else new_months,
            "special_terms": (
                "1. 본 계약은 기존 임대차 계약의 만기 연장(재계약)으로, 본인 확인 및 기존 시설·옵션을 승계하며 비대면으로 체결함.\n"
                "2. 보증금 및 월세 조정은 주택임대차보호법 제7조(5% 상한) 범위 내에서 합의함.\n"
                "3. 자연 마모에 의한 시설 고장은 임대인이 무상 수리하며, 퇴실 시 통상 손모는 공제하지 않음."
            ) if is_renewal else new_special_terms,
        },
    ).save(action="cosign_session_opened", app_source=app_source)

    callee_role = "tenant" if initiated_by_role == "lessor" else "lessor"
    caller_name = session.lessor_name if initiated_by_role == "lessor" else (t_name or "임차인")
    msg_prefix = "[📞 실시간 공동계약 호출]" if call_status == "CALLING" else "[🤝 공동 임대계약방 개설]"
    if pre_contract:
        return session          # 입주 중인 호실의 채팅은 지금 사는 임차인도 보므로, 다음 입주자와의 계약 호출은 그 채팅에 남기지 않는다(앱 알림·홈 화면으로 전달)
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

def expire_idle(cls: type[CoSignSession], branch: str = "", room_no: str = "") -> int:
    """무응답 세션을 자동 해제(CANCELLED)한다. 해제한 건수를 돌려준다."""
    kw: Dict[str, Any] = {"status": "IN_PROGRESS"}
    if branch:
        kw["branch"] = normalize_branch_id(branch)
    if room_no:
        kw["room_no"] = room_no
    n = 0
    for x in cls.find(**kw):
        if x.is_idle_expired():
            x.status = "CANCELLED"
            x.cancel_reason = "24시간 이상 응답이 없어 자동 해제되었습니다."
            x.cancelled_by, x.cancelled_at = "system", now_iso()
            x.save(action="cosign_expired_idle", app_source="rent3_contract")
            n += 1
    return n

def ai_calls_this_month(cls: type[CoSignSession], branch: str) -> int:
    ym = now_iso()[:7]
    return sum(1 for x in cls.find(branch=normalize_branch_id(branch)) for t in x.registry_ai_times if t.startswith(ym))

def live_for_room(cls: type[CoSignSession], branch: str, room_no: str) -> Optional[CoSignSession]:
    """해당 호실에 진행 중(IN_PROGRESS)인 계약 세션. 없으면 None. (무응답 세션은 먼저 해제)"""
    cls.expire_idle(branch, room_no)
    return next(iter(cls.find(branch=normalize_branch_id(branch), room_no=room_no, status="IN_PROGRESS")), None)

def _tenant_identity_evidence(self: CoSignSession) -> Dict[str, Any]:
    """봉인 때 남기는 임차인 본인인증 증거 (업체·시각·가짜 여부). 생년월일 등 개인정보는 싣지 않는다."""
    t = Tenant.get(self.id_tenant) if self.id_tenant else None
    if t is None:
        ph = re.sub(r"\D", "", str((self.terms or {}).get("tenant_phone") or ""))
        t = next((x for x in Tenant.all() if ph and re.sub(r"\D", "", x.phone) == ph), None)
    idn = (t.identity if t else {}) or {}
    return {"verified": bool(idn.get("verified")), "provider": idn.get("provider", ""), "assurance": idn.get("assurance", ""),
            "fake": bool(idn.get("fake")), "verified_at": idn.get("verified_at", "")}

def is_tenant_party(self: CoSignSession, id_tenant: str = "", phone: str = "") -> bool:
    """이 계약의 임차인 당사자인지(계정 ID 또는 연락처 일치)."""
    if id_tenant and self.id_tenant and id_tenant.strip() == self.id_tenant:
        return True
    digits = re.sub(r"\D", "", phone or "")
    mine = re.sub(r"\D", "", str((self.terms or {}).get("tenant_phone") or ""))
    return bool(digits and mine and digits == mine)

def call_counterparty(
    cls: type[CoSignSession],
    caller_role: str,
    branch: str,
    room_no: str,
    tenant_name: str = "",
    tenant_phone: str = "",
    id_tenant: str = "",
    mode: str = "NEW",
    app_source: str = "rent3_contract",
) -> CoSignSession:
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

    if not Lessor.franchise_open(normalize_branch_id(branch)):
        raise DomainError(
            "가맹 승인된 건물만 직거래 계약을 시작할 수 있습니다.",
            hint="이 건물은 가맹 심사 중이거나 이용이 정지되었습니다.",
        )

    if role_norm == "tenant":
        mine = [x for x in cls.find(status="IN_PROGRESS") if x.is_tenant_party(id_tenant, tenant_phone)
                and not (x.branch == normalize_branch_id(branch) and x.room_no == room_no)]
        if len(mine) >= MAX_LIVE_PER_TENANT:
            raise DomainError(
                f"동시에 진행할 수 있는 계약은 {MAX_LIVE_PER_TENANT}건까지입니다.",
                hint="진행 중인 계약을 마치거나 취소한 뒤 다른 호실을 선택해 주세요.",
            )

    room0 = Room.first(branch=normalize_branch_id(branch), room_no=room_no)
    if room0 is not None and mode == "RENEWAL" and room0.reservation() is not None:
        raise DomainError(f"{room_no}호는 이미 다음 계약(연장 또는 예약)이 체결되어 있습니다.", hint="체결된 계약을 취소한 뒤에 다시 진행해 주세요.")
    if room0 is not None and mode != "RENEWAL":
        if room0.reservation() is not None:
            raise DomainError(f"{room_no}호는 이미 다음 입주자와 예약 계약이 체결되었습니다.",
                              hint="예약이 취소되면 다시 계약을 시작할 수 있습니다.")
        if room0.is_occupied and not room0.pre_moveout_notice:
            raise DomainError(f"{room_no}호는 입주 중입니다.",
                              hint="임차인의 전출 예정이 확인되어 사전 매물로 공개된 호실만 퇴실 전에 미리 계약할 수 있습니다.")

    # 같은 호실에 진행 중인 계약이 이미 있으면 새로 만들지 않고 그것을 이어서 쓴다.
    # (버튼을 두 번 누르거나 양쪽이 동시에 시작해도 계약이 중복 생성되지 않게 한다.)
    cls.expire_idle(branch, room_no)
    b_id = normalize_branch_id(branch)
    live = next(
        (x for x in cls.find(branch=b_id, room_no=room_no, status="IN_PROGRESS") if x.mode == mode),
        None,
    )
    if live:
        # 진행 중인 계약에는 그 계약의 당사자만 다시 들어올 수 있다. 다른 임차인은 접근 불가.
        if role_norm == "tenant" and not live.is_tenant_party(id_tenant, tenant_phone):
            raise DomainError(
                f"{room_no}호는 다른 임차인과 계약이 진행 중입니다.",
                hint="진행 중인 계약이 끝나거나 취소된 뒤에 다시 시도해 주세요.",
            )
        return live

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
    self: CoSignSession,
    responder_role: str,
    responder_name: str = "",
    responder_phone: str = "",
    id_tenant: str = "",
    app_source: str = "rent3_contract",
) -> CoSignSession:
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

def by_code_or_id(cls: type[CoSignSession], token: str) -> CoSignSession:
    key = (token or "").strip()
    found = cls.get(key) or cls.first(session_code=key)
    if not found:
        raise NotFound(
            f"공동 계약 세션('{key}')을 찾을 수 없습니다.",
            hint="공동계약 호출 목록에서 세션을 선택해 주세요.",
        )
    return found

def verify_counterparty_id(
    self: CoSignSession,
    role: str,
    checked: bool = True,
    app_source: str = "rent3_contract",
) -> CoSignSession:
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
    self: CoSignSession,
    role: str,
    registry_updates: Optional[Dict[str, Any]] = None,
    checked_counterparty_id: Optional[bool] = None,
    app_source: str = "rent3_contract",
) -> CoSignSession:
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
        if not (self.tenant_registry or {}).get("submitted"):
            raise DomainError(
                "임차인이 인터넷등기소에서 직접 열람한 결과를 먼저 입력해야 합니다.",
                hint="임대인이 보여준 서류만으로는 확인 완료 처리할 수 없습니다.",
            )
        self.step1_tenant_locked = True

    if self.is_step1_fully_verified() and self.current_step < 2:
        self.current_step = 2
    return self.save(action=f"step1_confirmed_by_{role}", app_source=app_source)

def confirm_step2(
    self: CoSignSession,
    role: str,
    option_checks: Optional[List[Dict[str, Any]]] = None,
    facility_notes: Optional[str] = None,
    app_source: str = "rent3_contract",
) -> CoSignSession:
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
