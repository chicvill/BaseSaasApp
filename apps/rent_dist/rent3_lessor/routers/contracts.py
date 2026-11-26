"""
apps/rent_dist/rent3_lessor/routers/contracts.py — 계약 수정 · 임차인 사업자등록증 · 중개계약서 등록/조회 · 만기·연장 · 퇴실 정산.
"""
from datetime import date
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.errors import DomainError, Forbidden
from apps.rent_dist._rent3_core import (
    BrokerContractOnboarding,
    BrokerContractTextParser,
    Building,
    CheckOutSettlement,
    Contract,
    DirectChat,
    ExpiryRenewalCheck,
    Lessor,
    Room,
    Tenant,
    ensure_pool_seeded,
    franchise as fr,
    normalize_branch_id,
    registry_ai,
    validate_email_format,
)
from apps.rent_dist._rent3_core.authz import claims_from
from apps.rent_dist._rent3_core.billing import validate_kr_business_number
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import APP_SRC

router = APIRouter()


class ContractEditReq(BaseModel):
    branch: str = "main"
    contract_id: str
    deposit: int
    monthly_rent: int
    maintenance_fee: int
    cleaning_fee: Optional[int] = None         # 퇴실 청소비 약정(원, 0 = 약정 없음)
    move_in_date: str = ""
    expiry_date: str = ""
    tenant_phone: str = ""
    payer_names: Optional[List[str]] = None
    memo: str = ""
    tenant_business_number: Optional[str] = None
    tenant_company_name: Optional[str] = None
    tenant_ceo_name: Optional[str] = None
    tenant_email: Optional[str] = None
    tenant_business_address: Optional[str] = None
    vat_target: Optional[bool] = None
    tax_invoice_requested: Optional[bool] = None


class TenantBizRegReq(BaseModel):
    branch: str = "main"
    room_no: str
    attachment_data_url: str = ""
    consent: bool = False                 # AI 로 사업자등록증 읽기 동의
    business_number: str = ""
    company_name: str = ""
    ceo_name: str = ""
    business_address: str = ""
    email: str = ""


class BrokerTextParseReq(BaseModel):
    text: str


class BrokerContractPhotoReq(BaseModel):
    branch: str = "main"
    room_no: str
    photo_data_url: str
    photo_filename: str = ""
    tenant_name: str
    tenant_phone: str
    tenant_car_number: str = ""
    lease_type: str = "월세"
    pay_timing: str = "후불"
    deposit: int = 5000000
    monthly_rent: int = 500000
    maintenance_fee: int = 50000
    cleaning_fee: int = 0
    contract_deposit: int = 300000
    deposit_received: bool = True
    contract_date: Optional[str] = None
    move_in_date: Optional[str] = None
    lease_term_months: int = 12
    special_terms: str = ""
    broker_agency_name: str = ""
    broker_agent_name: str = ""
    broker_reg_no: str = ""
    broker_phone: str = ""
    rtms_filed_by_broker: bool = True
    fixed_date_no: str = ""
    tax_invoice_requested: bool = False
    tenant_business_number: str = ""
    tenant_company_name: str = ""
    tenant_ceo_name: str = ""
    tenant_email: str = ""


class RequestMoveoutCheckReq(BaseModel):
    branch: str = "main"
    room_no: str
    message_note: str = ""


class RecordMoveoutPlanReq(BaseModel):
    branch: str = "main"
    room_no: str
    tenant_plan: str                         # MOVE_OUT / STAY
    planned_move_out_date: str = ""
    tenant_memo: str = ""
    prelist_vacancy: bool = True


class RenewalPreviewReq(BaseModel):
    branch: str = "main"
    room_no: str
    proposed_deposit: Optional[int] = None
    proposed_monthly_rent: Optional[int] = None
    proposed_maintenance_fee: Optional[int] = None
    proposed_cleaning_fee: Optional[int] = None
    proposed_term_months: int = 12
    renewal_note: str = ""
    open_cosign_session: bool = True


class ExpiryRemindReq(BaseModel):
    branch: str = "main"
    room_no: str


class CheckoutCalcReq(BaseModel):
    branch: str = "main"
    room_no: str
    utility_settlement: int = 0
    cleaning_fee: Optional[int] = None      # 비우면 계약서에 약정한 청소비. 약정액을 넘길 수 없다(약정 없으면 0원)
    unpaid_rent: Optional[int] = None       # 미납 임차료(원). 비우면 자동 계산값 — 임대인이 고칠 수 있다
    prorated_rent: Optional[int] = None     # 일할 계산 임차료(원). 비우면 자동 계산값(경과 일수 × 월 임차료 ÷ 30)
    utility_electric: int = 0               # 미납 공과금: 전기·수도·가스·기타
    utility_water: int = 0
    utility_gas: int = 0
    utility_other: int = 0
    publish: bool = False                   # True 일 때만 임차인에게 게시(임차인이 보고 확인·이의 제기). 기본은 임시 저장(임차인에게 안 보임)
    damage_restoration_fee: int = 0
    inspection_notes: str = "6대 시설 자연마모 외 파손 없음"


class CheckoutPaidReq(BaseModel):
    branch: str = "main"
    memo: str = ""


@router.post("/contracts/edit")
def edit_contract(req: ContractEditReq):
    """계약 내용(보증금·월세·관리비·전입일·만기·연락처·사업자정보·메모) 수정. 호실 기본 금액과 임차인 연락처·사업자정보도 맞춘다."""
    ensure_pool_seeded()
    ct = Contract.require(req.contract_id)
    if normalize_branch_id(ct.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 계약은 고칠 수 없습니다.")
    for label, v in (("전입일", req.move_in_date), ("만기일", req.expiry_date)):
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise DomainError(f"{label} 형식이 올바르지 않습니다 (예: 2026-03-15).")
    if min(req.deposit, req.monthly_rent, req.maintenance_fee) < 0:
        raise DomainError("금액은 0원 이상이어야 합니다.")
    ct.deposit, ct.monthly_rent, ct.maintenance_fee = req.deposit, req.monthly_rent, req.maintenance_fee
    if req.cleaning_fee is not None:
        if req.cleaning_fee < 0:
            raise DomainError("청소비는 0원 이상이어야 합니다.")
        if req.cleaning_fee > req.deposit:
            raise DomainError("퇴실 청소비가 보증금보다 클 수 없습니다.", hint="청소비는 퇴실 때 보증금에서 공제하는 금액입니다.")
        ct.cleaning_fee = int(req.cleaning_fee)
    if req.move_in_date:
        ct.move_in_date = req.move_in_date
    if req.expiry_date:
        ct.expiry_date = req.expiry_date
    ct.tenant_phone = req.tenant_phone.strip()
    if req.payer_names is not None:
        pn: List[str] = []
        for x in req.payer_names:
            x = (x or "").strip()
            if x and x != (ct.tenant_name or "").strip() and x not in pn:
                pn.append(x)
        if len(pn) > 3:
            raise DomainError("송금자명은 최대 3개까지 등록할 수 있습니다.")
        ct.payer_names = pn
    ct.lessor_memo = req.memo.strip()
    if req.tenant_business_number is not None:
        ct.tenant_business_number = req.tenant_business_number.strip()
    if req.tenant_company_name is not None:
        ct.tenant_company_name = req.tenant_company_name.strip()
    if req.tenant_ceo_name is not None:
        ct.tenant_ceo_name = req.tenant_ceo_name.strip()
    if req.tenant_email is not None:
        ct.tenant_email = req.tenant_email.strip()
    if req.tenant_business_address is not None:
        ct.tenant_business_address = req.tenant_business_address.strip()
    if req.tax_invoice_requested is not None:
        ct.tax_invoice_requested = req.tax_invoice_requested
    elif req.tenant_business_number:
        ct.tax_invoice_requested = True
    if req.vat_target is not None:
        ct.vat_target = req.vat_target
    elif req.tax_invoice_requested is not None:
        ct.vat_target = req.tax_invoice_requested
    ct.vat = ct.compute_vat()
    ct.save(action="contract_edited", app_source=APP_SRC)
    room = Room.get(ct.id_room)
    if room:
        room.deposit, room.monthly_rent, room.maintenance_fee = req.deposit, req.monthly_rent, req.maintenance_fee
        if req.vat_target is not None:
            room.vat_applicable = req.vat_target
        room.save(action="room_terms_synced", app_source=APP_SRC)
    tenant = Tenant.get(ct.id_tenant) if ct.id_tenant else None
    if tenant:
        if req.tenant_phone.strip():
            tenant.phone = req.tenant_phone.strip()
        if req.tenant_business_number is not None:
            tenant.business_number = req.tenant_business_number.strip()
        if req.tenant_company_name is not None:
            tenant.company_name = req.tenant_company_name.strip()
        if req.tenant_ceo_name is not None:
            tenant.ceo_name = req.tenant_ceo_name.strip()
        if req.tenant_email is not None:
            tenant.email = req.tenant_email.strip()
        if req.tenant_business_address is not None:
            tenant.business_address = req.tenant_business_address.strip()
        tenant.save(action="tenant_phone_edited", app_source=APP_SRC)
    return {"success": True, "message": f"{ct.room_no}호 계약 내용을 저장했습니다.", "contract": ct.model_dump()}


@router.post("/contracts/tenant-bizreg")
def tenant_bizreg(req: TenantBizRegReq):
    """임대인이 임차인(법인·사업자)의 사업자등록증을 촬영·선택해 올리면(동의 시 AI 가 읽어 채움) 그 호실 계약·임차인의 세금계산서 발급 정보에 반영한다.
    임차인 정보에 따로 보관하는 곳은 없다: 이미 세금계산서 대장이 읽는 계약(`tenant_*`)·임차인(`business_*`) 필드에 쓴다(사업자인 임차인만 값이 생긴다).
    부가세 청구 여부(vat_target)는 바꾸지 않는다 — 청구액이 달라지므로 계약내용에서 따로 정한다."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no.strip())
    if not room:
        raise DomainError(f"'{req.room_no}호'는 이 건물에 없는 호실입니다.")
    ct = Contract.get(room.active_contract_id) if room.active_contract_id else None
    ct = ct or Contract.first(id_room=room.id, status="ACTIVE")
    if not ct:
        raise DomainError(f"{room.room_no}호는 현재 계약이 없는 공실입니다.", hint="입주 중인 호실의 사업자등록증만 올릴 수 있습니다.")
    tenant = Tenant.get(ct.id_tenant) if ct.id_tenant else None
    ex: Dict[str, Any] = {}
    saved = None
    if (req.attachment_data_url or "").strip():
        owner_id = tenant.id if tenant else ct.id
        saved = fr.save_bizreg_file(owner_id, req.attachment_data_url)
        if req.consent:
            used = int(getattr(tenant, "biz_ai_calls", 0) or 0) if tenant else 0
            if used >= 6:
                raise DomainError("AI 읽기 횟수 한도를 넘었습니다.", hint="사업자등록번호·상호·대표자를 직접 입력해 주세요.")
            binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
            ai = registry_ai.analyze_bizreg(binary, mime)
            if ai["status"] == "ok":
                ex = ai.get("extracted") or {}
                if tenant:
                    tenant.biz_ai_calls = used + 1
    num = (req.business_number or ex.get("business_number") or "").strip()
    if not num:
        raise DomainError("사업자등록번호를 읽지 못했습니다.", hint="사진이 선명한지 확인하거나 사업자등록번호 10자리를 직접 입력해 주세요.")
    val = validate_kr_business_number(num, strict_checksum=True)
    if not val["valid"]:
        raise DomainError(
            f"사업자등록번호({num})가 올바르지 않습니다: {val['reason']}",
            hint="번호를 다시 확인해 주세요. 번호가 없으면 세금계산서 대신 현금영수증을 발행합니다.",
        )
    d = val["digits"]
    number = f"{d[:3]}-{d[3:5]}-{d[5:]}"
    company = (req.company_name or ex.get("company_name") or "").strip()
    ceo = (req.ceo_name or ex.get("representative_name") or "").strip()
    addr = (req.business_address or ex.get("business_address") or "").strip()
    email = (req.email or ct.tenant_email or (tenant.email if tenant else "") or "").strip()
    if email and not validate_email_format(email):
        raise DomainError(
            "이메일 형식이 올바르지 않습니다.",
            hint="세금계산서를 받을 이메일을 정확히 입력해 주세요(예: name@example.com).",
        )
    ct.tenant_business_number, ct.tenant_company_name = number, company or ct.tenant_company_name
    ct.tenant_ceo_name, ct.tenant_business_address = ceo or ct.tenant_ceo_name, addr or ct.tenant_business_address
    if email:
        ct.tenant_email = email
    ct.tax_invoice_requested = True
    ct.save(action="tenant_bizreg_by_lessor", app_source=APP_SRC)
    if tenant:
        tenant.business_number, tenant.company_name = number, company or tenant.company_name
        tenant.ceo_name, tenant.business_address = ceo or tenant.ceo_name, addr or tenant.business_address
        if email:
            tenant.email = email
        tenant.biz_submitted_at = now_iso()
        tenant.save(action="tenant_bizreg_by_lessor", app_source=APP_SRC)
    missing = [k for k, v in (("상호", ct.tenant_company_name), ("대표자", ct.tenant_ceo_name), ("이메일", ct.tenant_email)) if not v]
    return {
        "success": True,
        "room_no": room.room_no,
        "message": (
            f"{room.room_no}호 임차인의 사업자 정보를 저장했습니다. 세무 허브 ▸ 세금계산서에서 발행할 수 있습니다."
            + (f" (비어 있는 항목: {', '.join(missing)} — 계약내용에서 입력)" if missing else "")
        ),
        "business": {
            "business_number": number,
            "company_name": ct.tenant_company_name,
            "ceo_name": ct.tenant_ceo_name,
            "business_address": ct.tenant_business_address,
            "email": ct.tenant_email,
        },
        "vat_target": bool(ct.vat_target),
        "file_saved": bool(saved),
    }


@router.post("/contracts/parse-broker-text")
def parse_broker_contract_text(req: BrokerTextParseReq):
    parsed = BrokerContractTextParser.parse(req.text)
    return {
        "success": True,
        "message": "계약서 문구에서 주요 계약 조건을 자동 추출했습니다. 내용을 확인한 뒤 적용해 주세요.",
        "parsed": parsed,
    }


@router.post("/contracts/broker-photo")
def register_broker_contract_photo(req: BrokerContractPhotoReq):
    ensure_pool_seeded()
    res = BrokerContractOnboarding.register(
        branch=req.branch,
        room_no=req.room_no,
        photo_data_url=req.photo_data_url,
        photo_filename=req.photo_filename,
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        tenant_car_number=req.tenant_car_number,
        lease_type=req.lease_type,
        pay_timing=req.pay_timing,
        deposit=req.deposit,
        monthly_rent=req.monthly_rent,
        maintenance_fee=req.maintenance_fee,
        cleaning_fee=req.cleaning_fee,
        contract_deposit=req.contract_deposit,
        deposit_received=req.deposit_received,
        contract_date=req.contract_date,
        move_in_date=req.move_in_date,
        lease_term_months=req.lease_term_months,
        special_terms=req.special_terms,
        broker_agency_name=req.broker_agency_name,
        broker_agent_name=req.broker_agent_name,
        broker_reg_no=req.broker_reg_no,
        broker_phone=req.broker_phone,
        rtms_filed_by_broker=req.rtms_filed_by_broker,
        fixed_date_no=req.fixed_date_no,
        tax_invoice_requested=req.tax_invoice_requested,
        tenant_business_number=req.tenant_business_number,
        tenant_company_name=req.tenant_company_name,
        tenant_ceo_name=req.tenant_ceo_name,
        tenant_email=req.tenant_email,
        app_source=APP_SRC,
    )
    rtms_status = res["rtms"]["status"]
    level = "warning" if rtms_status == "PENDING" else "success"
    msg = (
        f"📸 {req.room_no}호 공인중개사 계약서 사진 보관 및 계약 원장 적용 완료! "
        f"(계약·호실·입주자 정보 동시 반영)"
    )
    hint = (
        "주의: 중개사 전월세 미신고 상태이므로 체결일로부터 30일 이내 RTMS 신고 마감을 확인해 주세요."
        if rtms_status == "PENDING"
        else "임차인 앱 마이홈에서도 첨부된 계약서 사본과 납부 현황이 즉시 연동됩니다."
    )
    return {"success": True, "level": level, "message": msg, "hint": hint, **res}


@router.get("/contracts/{contract_id}/photo")
def get_contract_photo(contract_id: str):
    ensure_pool_seeded()
    abs_path, mime, filename = BrokerContractOnboarding.resolve_photo_file(contract_id)
    return FileResponse(path=abs_path, media_type=mime, filename=filename)


@router.post("/expiry/request-check")
def request_expiry_moveout_check(req: RequestMoveoutCheckReq):
    ensure_pool_seeded()
    chk = ExpiryRenewalCheck.request_moveout_check(
        branch=req.branch,
        room_no=req.room_no,
        message_note=req.message_note,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "level": "success",
        "message": (
            f"📅 {chk.room_no}호({chk.tenant_name}) 만기 전출계획 확인 요청이 발송되었습니다 "
            f"(법정 통지마감: {chk.notice_deadline_date})."
        ),
        "hint": "세입자가 마이홈에서 [전출 예정] 또는 [전출의사 없음]을 회신하면 즉시 동기화됩니다.",
        "check": chk.model_dump(),
    }


@router.post("/expiry/record-plan")
def record_expiry_moveout_plan(req: RecordMoveoutPlanReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    if not room or not room.active_contract_id:
        raise DomainError(f"{req.room_no}호에 활성 계약이 없습니다.")
    ct = Contract.require(room.active_contract_id)
    chk = ExpiryRenewalCheck.ensure_for_contract(ct).respond_moveout_plan(
        tenant_plan=req.tenant_plan,
        planned_move_out_date=req.planned_move_out_date,
        tenant_memo=req.tenant_memo,
        prelist_vacancy=req.prelist_vacancy,
        app_source=APP_SRC,
    )
    msg = (
        (
            f"📦 {chk.room_no}호 전출 예정({chk.planned_move_out_date}) 기록 완료 — 퇴실 기준일 1개월 전인 {chk.prelist_open_date}부터 사전 매물로 자동 공개됩니다."
            if chk.prelist_pending
            else f"📦 {chk.room_no}호 전출 예정({chk.planned_move_out_date}) 기록 완료 — 공실 쇼케이스에 사전예약 매물로 게시되었습니다."
        )
        if chk.tenant_plan == "MOVE_OUT"
        else f"🏠 {chk.room_no}호 전출의사 없음(거주 연장 희망) 확인 완료 — 이제 연장 재계약 조건을 예고하실 수 있습니다."
    )
    return {"success": True, "level": "success", "message": msg, "check": chk.model_dump()}


@router.post("/expiry/preview-renewal")
def send_expiry_renewal_preview(req: RenewalPreviewReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    if not room or not room.active_contract_id:
        raise DomainError(f"{req.room_no}호에 활성 계약이 없습니다.")
    ct = Contract.require(room.active_contract_id)
    chk = ExpiryRenewalCheck.ensure_for_contract(ct).send_renewal_preview(
        proposed_deposit=req.proposed_deposit,
        proposed_monthly_rent=req.proposed_monthly_rent,
        proposed_maintenance_fee=req.proposed_maintenance_fee,
        proposed_cleaning_fee=req.proposed_cleaning_fee,
        proposed_term_months=req.proposed_term_months,
        renewal_note=req.renewal_note,
        open_cosign_session=req.open_cosign_session,
        app_source=APP_SRC,
    )
    code_hint = (
        f"공동 재계약방(참여코드 {chk.cosign_session_code})이 함께 개설되어 임차인 마이홈에 전달되었습니다."
        if chk.cosign_session_code
        else "임차인 마이홈에 연장 재계약 예고장이 전달되었습니다."
    )
    return {
        "success": True,
        "level": "success",
        "message": (
            f"📝 {chk.room_no}호({chk.tenant_name}) 연장 재계약 예고 발송 완료! "
            f"(보 {chk.proposed_deposit:,}원 / 월 {chk.proposed_monthly_rent:,}원, {chk.proposed_term_months}개월)"
        ),
        "hint": code_hint,
        "check": chk.model_dump(),
    }


@router.post("/expiry/remind-password")
def remind_tenant_renewal_password(req: ExpiryRemindReq, request: Request):
    """만기·재계약(또는 1년 도래) 임차인에게 새 비밀번호 설정 권유 채팅·1회용 링크를 보낸다. 기존 비밀번호와 등록 기기는 유지된다."""
    from apps.rent_dist._rent3_core import connect as cn_chk, invite as inv
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    t = Tenant.get(room.id_tenant) if (room and room.id_tenant) else None
    if t is None or t.status != "ACTIVE":
        raise DomainError(f"{req.room_no}호에 입주 중인 임차인이 없습니다.")
    if not cn_chk.activated(t):
        raise DomainError(
            "아직 앱에 가입하지 않은 임차인입니다.",
            hint="입주 안내 링크를 먼저 보내 주세요(가입 안내 문자 창). 이미 보낸 가입 링크가 있으면 이 권유가 덮어써서 못 쓰게 됩니다.",
        )
    who = str((claims_from(request) or {}).get("u") or "lessor")
    tok = inv.issue(t, who)
    t.password_remind_sent_at = now_iso()
    t.save(action="tenant_password_remind_sent", app_source=APP_SRC)
    b = Building.first(branch=b_id)
    bname = b.building_name if b else b_id
    lsr = Lessor.first(branch=b_id)
    url = f"/rent3_tenant/?invite={tok}"
    sms_text = (
        f"[{bname}] {t.room_no}호 {t.name}님, 재계약(만기 연장)을 맞아 안전한 계정 보호를 위해 새로운 비밀번호 설정을 안내드립니다.\n"
        f"아래 전용 링크(7일 유효, 1회용)에서 본인 확인 후 새 비밀번호를 설정해 주세요(설정 전까지 기존 비밀번호는 그대로 유지됩니다).\n"
        f"{url}"
    )
    DirectChat(
        branch=b_id,
        id_room=room.id if room else "",
        room_no=room.room_no if room else t.room_no,
        sender_role="lessor",
        sender_name=lsr.name if lsr else "임대인",
        message=f"[🔒 재계약 새 비밀번호 설정 안내] {sms_text}",
        quick_action_label="새 비밀번호 설정",
        quick_action_url=url,
    ).save(action="chat_auto_sent", app_source=APP_SRC)
    return {
        "success": True,
        "url": url,
        "expires": t.invite_expires,
        "tenant_name": t.name,
        "room_no": t.room_no,
        "building_name": bname,
        "sms_text": sms_text,
        "message": f"{t.room_no}호 {t.name}님께 재계약 새 비밀번호 설정 안내 채팅와 1회용 링크를 보냈습니다.",
    }


@router.post("/checkout/calculate")
def calculate_checkout(req: CheckoutCalcReq):
    s = CheckOutSettlement.calculate_for_room(
        branch=req.branch,
        room_no=req.room_no,
        utility_settlement=req.utility_settlement,
        cleaning_fee=req.cleaning_fee,
        damage_restoration_fee=req.damage_restoration_fee,
        inspection_notes=req.inspection_notes,
        app_source=APP_SRC,
        unpaid_rent=req.unpaid_rent,
        prorated_rent=req.prorated_rent,
        utility_electric=req.utility_electric,
        utility_water=req.utility_water,
        utility_gas=req.utility_gas,
        utility_other=req.utility_other,
        publish=req.publish,
    )
    return {
        "success": True,
        "message": (
            f"{s.room_no}호 퇴실 정산 명세서(실반환 {s.refund_amount:,}원)가 생성되어 임차인 앱에 공유되었습니다."
            if req.publish
            else f"{s.room_no}호 퇴실 정산 명세서(실반환 {s.refund_amount:,}원)를 임시 저장했습니다. 게시에 체크해 보내기 전까지 임차인에게는 보이지 않습니다."
        ),
        "settlement": s.model_dump(),
    }


@router.post("/checkout/{settlement_id}/mark-paid")
def mark_checkout_paid(settlement_id: str, req: Optional[CheckoutPaidReq] = None):
    """임대인이 보증금을 송금한 뒤 표시한다(임차인이 받았다고 확인하거나 7일 안에 이의가 없으면 퇴실 확정). 예금주가 계약자와 같은 계좌로만 표시할 수 있다."""
    s = CheckOutSettlement.require(settlement_id)
    s.mark_paid((req.memo if req else ""), app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{s.room_no}호 송금을 표시했습니다. 임차인이 받았다고 확인하면(또는 7일 안에 이의가 없으면) 퇴실이 확정되고 공실 매물로 복귀합니다.",
        "settlement": s.model_dump(),
    }


@router.post("/checkout/{settlement_id}/complete")
def complete_checkout(settlement_id: str, req: Optional[CheckoutPaidReq] = None):
    """(예전 이름) 송금 완료 표시 — mark-paid 와 같다. 퇴실 확정은 임차인 수취 확인 뒤에 이루어진다."""
    return mark_checkout_paid(settlement_id, req)
