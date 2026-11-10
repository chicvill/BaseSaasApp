"""
apps/rent3_lessor/api.py — 임대인 전용 API 라우터.
"""
from datetime import date
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Request
from core.errors import DomainError, Forbidden
from apps.rent_dist._rent3_core.authz import api_headers, is_local_request, lessor_scope
from apps.rent_dist._rent3_core import franchise as fr, registry_ai
from apps.rent_dist._rent3_core.pool import now_iso
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from apps.rent_dist._rent3_core import (
    BrokerContractOnboarding,
    BrokerContractTextParser,
    Building,
    CheckOutSettlement,
    CoSignSession,
    Contract,
    DirectChat,
    EXPENSE_CATEGORIES,
    ExpiryRenewalCheck,
    FifoPaymentEngine,
    Lessor,
    MaintenanceTicket,
    Outlay,
    Pay,
    RentBillingNotice,
    Room,
    TaxAndCertificateService,
    Tenant,
    date_today,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter(dependencies=[Depends(api_headers), Depends(lessor_scope)])
APP_SRC = "rent3_lessor"


class RoomCreateReq(BaseModel):
    branch: str = "main"
    room_no: str
    name: str = ""
    floor: int = 2
    area_sqm: float = 23.0
    room_category: str = "주거용 원룸"
    vat_applicable: bool = False
    deposit: int = 5000000
    monthly_rent: int = 500000
    maintenance_fee: int = 50000
    pay_timing: str = "후불"
    status_description: str = "신규 등록 풀옵션 호실"
    total_floors: int = 0
    direction: str = ""
    available_from: str = "즉시입주"
    is_published: bool = True


class PublishToggleReq(BaseModel):
    is_published: bool


class DoorlockReq(BaseModel):
    new_pwd: str


class CollectPaymentReq(BaseModel):
    branch: str = "main"
    room_no: str
    amount: int
    payer_name: str = ""
    paid_date: Optional[str] = None
    method: str = "TRANSFER"
    up_to_ym: Optional[str] = None


class SmsPasteReq(BaseModel):
    branch: str = "main"
    sms_text: str
    up_to_ym: Optional[str] = None


class OutlayCreateReq(BaseModel):
    branch: str = "main"
    category: str = "cleaning"
    title: str
    amount: int
    vendor: str = ""
    expense_date: Optional[str] = None
    memo: str = ""


class TicketAssignReq(BaseModel):
    vendor_name: str
    vendor_phone: str
    visit_date: str


class TicketCompleteReq(BaseModel):
    repair_cost: int = 0
    cost_burden: str = "LANDLORD"
    resolution_note: str = ""


class CheckoutCalcReq(BaseModel):
    branch: str = "main"
    room_no: str
    utility_settlement: int = 0
    cleaning_fee: int = 70000
    damage_restoration_fee: int = 0
    inspection_notes: str = "6대 시설 자연마모 외 파손 없음"


class ChatReplyReq(BaseModel):
    branch: str = "main"
    room_no: str
    message: str


def _franchise_info(branch: str) -> Dict[str, Any]:
    lsr = Lessor.for_branch(branch)
    if lsr is None:
        return {"status": "ACTIVE", "label": "가맹 승인", "open": True, "note": "", "room_limit": 0, "room_count": Room.count(branch=branch)}
    return {
        "status": lsr.status,
        "label": Lessor.STATUS_LABELS.get(lsr.status, lsr.status),
        "open": lsr.status == "ACTIVE",
        "note": lsr.status_note,
        "room_limit": lsr.room_limit,
        "room_count": Room.count(branch=branch),
    }


# ── 가맹 신청: 본인인증 → 건물 정보 → 등기부 대조 → 제출 ───────────────────────────
FRANCHISE_AI_MAX_CALLS = 3  # 신청 1건당 등기부 AI 읽기 상한 (남용·비용 방지)


def _my_lessor(branch: str) -> Lessor:
    lsr = Lessor.for_branch(normalize_branch_id(branch))
    if lsr is None:
        raise DomainError("이 지점의 임대인 정보를 찾을 수 없습니다.")
    return lsr


def _is_loopback(request: Request) -> bool:
    return is_local_request(request)


class IdentityStartReq(BaseModel):
    branch: str = "main"
    name: str
    phone: str
    birth: str


class IdentityConfirmReq(IdentityStartReq):
    code: str


class FranchiseBuildingReq(BaseModel):
    branch: str = "main"
    building_name: str = ""
    address: str = ""
    property_value: int = 0


class FranchiseRegistryReq(BaseModel):
    branch: str = "main"
    attachment_data_url: str
    consent: bool = False


class FranchiseSubmitReq(BaseModel):
    branch: str = "main"


@router.get("/franchise/application")
def franchise_application(request: Request, branch: str = "main"):
    lsr = _my_lessor(branch)
    return {"success": True, "franchise": _franchise_info(lsr.branch),
            "application": fr.application_summary(lsr, Building.first(branch=lsr.branch)),
            "identity_provider": fr.provider(_is_loopback(request)).name}


@router.post("/franchise/building")
def franchise_building(req: FranchiseBuildingReq):
    """임대인이 자기 건물의 이름·실제 주소·건물 가치를 입력한다 (등기부 대조의 기준)."""
    lsr = _my_lessor(req.branch)
    b = Building.first(branch=lsr.branch)
    if b is None:
        raise DomainError("건물 정보를 찾을 수 없습니다.")
    if req.building_name.strip():
        b.building_name = req.building_name.strip()
    if req.address.strip():
        b.address = req.address.strip()
    if req.property_value < 0:
        raise DomainError("건물 가치는 0 이상이어야 합니다.")
    if req.property_value:
        b.property_value = req.property_value
    b.save(action="franchise_building_updated", app_source=APP_SRC)
    return {"success": True, "message": "건물 정보를 저장했습니다.", "application": fr.application_summary(lsr, b)}


@router.post("/franchise/identity/start")
def franchise_identity_start(req: IdentityStartReq, request: Request):
    _my_lessor(req.branch)
    p = fr.provider(_is_loopback(request))
    out = p.start(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth))
    return {"success": True, "message": "인증번호를 보냈습니다.", "provider": p.name, **out}


@router.post("/franchise/identity/confirm")
def franchise_identity_confirm(req: IdentityConfirmReq, request: Request):
    lsr = _my_lessor(req.branch)
    p = fr.provider(_is_loopback(request))
    res = p.confirm(req.name.strip(), req.phone.strip(), fr.normalize_birth(req.birth), req.code)
    lsr.identity = fr.verify_identity_record(res)
    # 본인인증이 바뀌면 이전 대조 결과는 무효 → 다시 대조
    if lsr.franchise_docs.get("ai"):
        ai = lsr.franchise_docs["ai"]
        b0 = Building.first(branch=lsr.branch)
        lsr.franchise_docs = {**lsr.franchise_docs, "check": fr.check_ownership(
            lsr.identity, ai.get("extracted"), b0.address if b0 else "")}
    lsr.applied_at = ""  # 신원이 바뀌었으니 제출은 다시
    lsr.save(action="franchise_identity_verified", app_source=APP_SRC)
    return {"success": True, "message": "본인인증이 완료되었습니다." + (" (시연용 가짜 인증)" if res.get("fake") else ""),
            "application": fr.application_summary(lsr, Building.first(branch=lsr.branch))}


@router.post("/franchise/registry")
def franchise_registry(req: FranchiseRegistryReq):
    """등기부를 올리면 보관하고(심사용), 동의 시 Gemini 로 읽어 본인인증 값과 대조한다."""
    lsr = _my_lessor(req.branch)
    if not lsr.identity.get("verified"):
        raise DomainError("먼저 휴대폰 본인인증을 해 주세요.")
    bldg = Building.first(branch=lsr.branch)
    saved = fr.save_registry_file(lsr.id, req.attachment_data_url)
    ai: Dict[str, Any] = {"status": "skipped"}
    if req.consent:
        if lsr.franchise_ai_calls >= FRANCHISE_AI_MAX_CALLS:
            raise DomainError(f"가맹 신청 중 AI 읽기 횟수({FRANCHISE_AI_MAX_CALLS}회)를 모두 썼습니다.",
                              hint="AI 없이 제출하면 관리자가 원본을 직접 확인합니다.")
        binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
        ai = registry_ai.analyze(binary, mime, lessor_names=[lsr.identity.get("name", "")], deposit=0,
                                 property_value=int(getattr(bldg, "property_value", 0) or 0))
        if ai["status"] == "ok":
            lsr.franchise_ai_calls += 1
    check = fr.check_ownership(lsr.identity, (ai or {}).get("extracted"), getattr(bldg, "address", ""))
    lsr.franchise_docs = {
        "registry_file": saved["file"], "registry_sha256": saved["sha256"], "registry_mime": saved["mime"],
        "uploaded_at": now_iso(), "ai": {"status": ai["status"], "message": ai.get("message", ""),
                                         "extracted": ai.get("extracted")},
        "check": check,
    }
    lsr.applied_at = ""
    lsr.save(action="franchise_registry_uploaded", app_source=APP_SRC)
    return {"success": True, "message": check["message"], "level": "success" if check["auto_pass"] else "warning",
            "application": fr.application_summary(lsr, bldg)}


@router.post("/franchise/submit")
def franchise_submit(req: FranchiseSubmitReq):
    lsr = _my_lessor(req.branch)
    summ = fr.application_summary(lsr, Building.first(branch=lsr.branch))
    if not summ["ready_to_submit"]:
        missing = [k for k, v in (("본인인증", summ["steps"]["identity"]), ("건물 주소(번지 포함)", summ["steps"]["building"]),
                                  ("등기부", summ["steps"]["registry"])) if not v]
        raise DomainError(f"아직 입력하지 않은 항목이 있습니다: {', '.join(missing)}")
    if lsr.status not in ("PENDING_APPROVAL", "REJECTED"):
        raise DomainError("이미 심사가 끝났거나 이용이 정지된 계정입니다.")
    lsr.status = "PENDING_APPROVAL"
    lsr.status_note = ""
    lsr.applied_at = now_iso()
    lsr.save(action="franchise_applied", app_source=APP_SRC)
    return {"success": True, "message": "가맹 신청이 제출되었습니다. 본사 심사 후 결과를 알려 드립니다.",
            "application": fr.application_summary(lsr, Building.first(branch=lsr.branch))}


@router.get("/status")
def get_status(branch: str = "main"):
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    bldg = Building.first(branch=b_id)
    return {
        "success": True,
        "app_id": "rent3_lessor",
        "status": "HEALTHY",
        "branch": b_id,
        "building_name": bldg.building_name if bldg else b_id,
        "franchise": _franchise_info(b_id),
        "pool_revision": pool().overview()["total_revision"],
    }


@router.get("/dashboard")
def get_dashboard(branch: str = "main", up_to_ym: Optional[str] = None, as_of_date: Optional[str] = None):
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    ref_dt = RentBillingNotice._parse_ref_date(as_of_date)
    buildings = Building.all()
    bldg = Building.first(branch=b_id) or (buildings[0] if buildings else None)
    lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)

    rooms = sorted(Room.find(branch=b_id), key=lambda r: r.room_no)
    tenants_by_id = {t.id: t for t in Tenant.find(branch=b_id)}

    room_cards: List[Dict[str, Any]] = []
    expiry_notice_rooms: List[Dict[str, Any]] = []
    total_unpaid_branch = 0
    for r in rooms:
        ct = Contract.get(r.active_contract_id) if r.active_contract_id else None
        ledger = ct.ledger(up_to_ym=up_to_ym) if ct else None
        if ledger:
            total_unpaid_branch += int(ledger["total_unpaid"])
        tenant = tenants_by_id.get(r.id_tenant) if r.id_tenant else None
        exp_win = ct.expiry_window_status() if ct else None
        exp_chk = ExpiryRenewalCheck.for_contract(ct.id) if ct else None
        bill_sched = ct.billing_notice_schedule(as_of=ref_dt) if ct else None
        bill_notices = [bn.model_dump() for bn in RentBillingNotice.for_contract(ct.id, limit=5)] if ct else []
        pays = []
        if ct:
            pays = sorted((x for x in Pay.find(id_contract=ct.id) if x.payment_kind == "MONTHLY_RENT"),
                          key=lambda x: (x.paid_date, x.created_at), reverse=True)[:60]
        card = {
            **r.model_dump(),
            "pays": [{"id": x.id, "paid_date": x.paid_date, "billing_month": x.billing_month, "rent": x.rent_amount,
                      "fee": x.maintenance_amount, "total": x.total_amount, "vat": x.vat_amount,
                      "tax_invoice": x.tax_invoice or None} for x in pays],
            "is_occupied": r.is_occupied,
            "tenant": tenant.model_dump() if tenant else None,
            "contract": ct.model_dump() if ct else None,
            "ledger": ledger,
            "expiry_window": exp_win,
            "expiry_check": exp_chk.model_dump() if exp_chk else None,
            "billing_schedule": bill_sched,
            "billing_notices": bill_notices,
        }
        room_cards.append(card)
        if ct and ((exp_win and exp_win["in_6_to_2_months_window"]) or exp_chk is not None):
            expiry_notice_rooms.append({
                "room_no": r.room_no,
                "tenant_name": ct.tenant_name,
                "expiry_date": ct.expiry_date,
                "days_to_expiry": exp_win["days_to_expiry"] if exp_win else None,
                "notice_deadline_date": exp_win["notice_deadline_date"] if exp_win else "",
                "check_status": exp_chk.status if exp_chk else "UNREQUESTED",
            })

    payments = sorted(Pay.find(branch=b_id), key=lambda p: (p.paid_date, p.created_at), reverse=True)
    outlays = sorted(Outlay.find(branch=b_id), key=lambda o: o.expense_date, reverse=True)
    tickets = sorted(MaintenanceTicket.find(branch=b_id), key=lambda t: t.updated_at, reverse=True)
    checkouts = sorted(CheckOutSettlement.find(branch=b_id), key=lambda c: c.updated_at, reverse=True)
    chats = sorted(DirectChat.find(branch=b_id), key=lambda c: c.created_at, reverse=True)[:30]
    cosign_sessions = sorted(CoSignSession.find(branch=b_id), key=lambda s: s.updated_at, reverse=True)
    recent_billing_notices = sorted(
        RentBillingNotice.find(branch=b_id), key=lambda x: x.created_at, reverse=True
    )[:15]
    fin = TaxAndCertificateService.branch_financial_summary(b_id)

    return {
        "success": True,
        "today": date_today().isoformat(),
        "as_of_date": ref_dt.isoformat(),
        "pool_revision": pool().overview()["total_revision"],
        "branch": b_id,
        "branches": [{"branch": b.branch, "building_name": b.building_name} for b in buildings],
        "building": bldg.model_dump() if bldg else None,
        "lessor": lessor.model_dump() if lessor else None,
        "total_unpaid_branch": total_unpaid_branch,
        "rooms": room_cards,
        "expiry_notice_rooms": expiry_notice_rooms,
        "recent_billing_notices": [bn.model_dump() for bn in recent_billing_notices],
        "payments": [p.model_dump() for p in payments[:30]],
        "outlays": [{**o.model_dump(), "category_label": o.category_label} for o in outlays],
        "expense_categories": EXPENSE_CATEGORIES,
        "tickets": [t.model_dump() for t in tickets],
        "checkouts": [c.model_dump() for c in checkouts],
        "chats": [c.model_dump() for c in chats],
        "cosign_sessions": [s.model_dump() for s in cosign_sessions[:10]],
        "financial_summary": fin,
    }


def _sync_vat_list(bldg, room_no: str, vat_on: bool) -> None:
    """호실 화면에서 과세 여부를 바꾸면 건물의 부가세 대상 호실 목록에도 맞춘다."""
    if not bldg:
        return
    cur = list(bldg.vat_rooms or [])
    if vat_on and room_no not in cur:
        cur.append(room_no)
    elif not vat_on and room_no in cur:
        cur.remove(room_no)
    bldg.apply_vat_rooms(cur, app_source=APP_SRC)


@router.post("/rooms")
def create_or_update_room(req: RoomCreateReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    bldg = Building.first(branch=b_id)
    existing = Room.first(branch=b_id, room_no=req.room_no)
    # 승인된 가맹점만 매물을 공개할 수 있다. 심사 중에는 호실을 미리 준비(비공개)만 가능.
    lsr = Lessor.for_branch(b_id)
    if not Lessor.franchise_open(b_id):
        req.is_published = False
    if not existing and lsr and Room.count(branch=b_id) >= lsr.room_limit:
        raise DomainError(
            f"가맹점당 등록 가능 호실 수({lsr.room_limit}실)를 넘었습니다.",
            hint="더 등록하려면 관리자에게 한도 상향을 요청해 주세요.",
        )
    if existing:
        existing.name = req.name or existing.name
        existing.floor = req.floor
        existing.area_sqm = req.area_sqm
        existing.total_floors = req.total_floors
        existing.direction = req.direction
        existing.available_from = req.available_from or "즉시입주"
        existing.room_category = req.room_category
        existing.vat_applicable = req.vat_applicable
        existing.deposit = req.deposit
        existing.monthly_rent = req.monthly_rent
        existing.maintenance_fee = req.maintenance_fee
        existing.pay_timing = req.pay_timing
        existing.status_description = req.status_description
        existing.is_published = req.is_published
        existing.save(action="room_updated", app_source=APP_SRC)
        _sync_vat_list(bldg, req.room_no, req.vat_applicable)
        return {"success": True, "message": f"{req.room_no}호 정보가 저장되었습니다.", "room": Room.get(existing.id).model_dump()}

    room = Room(
        id=f"room_{b_id}_{req.room_no}",
        branch=b_id,
        id_building=bldg.id if bldg else "",
        room_no=req.room_no,
        name=req.name or f"{req.room_no}호 ({req.room_category})",
        floor=req.floor,
        area_sqm=req.area_sqm,
        total_floors=req.total_floors,
        direction=req.direction,
        available_from=req.available_from or "즉시입주",
        room_category=req.room_category,
        vat_applicable=req.vat_applicable,
        deposit=req.deposit,
        monthly_rent=req.monthly_rent,
        maintenance_fee=req.maintenance_fee,
        pay_timing=req.pay_timing,
        status_description=req.status_description,
        is_published=req.is_published,
    ).save(action="room_created", app_source=APP_SRC)
    _sync_vat_list(bldg, req.room_no, req.vat_applicable)
    return {"success": True, "message": f"신규 {room.room_no}호가 등록되었습니다.", "room": Room.get(room.id).model_dump()}


@router.post("/rooms/{room_id}/publish")
def toggle_room_publish(room_id: str, req: PublishToggleReq):
    room = Room.require(room_id)
    if req.is_published and not Lessor.franchise_open(room.branch):
        raise Forbidden(
            "가맹 승인 후에 매물을 공개할 수 있습니다.",
            hint="관리자의 가맹 심사가 끝나면 공개할 수 있어요. 심사 상태는 임대인 화면 상단에서 확인하세요.",
        )
    room.is_published = req.is_published
    room.save(action="room_publish_toggled", app_source=APP_SRC)
    state_label = "직거래 매물 공개" if room.is_published else "매물 비공개"
    return {
        "success": True,
        "message": f"{room.room_no}호가 '{state_label}' 상태로 전환되었습니다 (임차인 앱 즉시 반영).",
        "room": room.model_dump(),
    }


class ReleaseKeysReq(BaseModel):
    balance_checked: bool = False   # 임대인이 본인 계좌의 실제 입금 내역에서 잔금을 직접 확인함


@router.post("/rooms/{room_id}/release-keys")
def release_keys(room_id: str, req: ReleaseKeysReq, request: Request):
    """입주일 잔금 입금을 확인하고 도어락·공동현관 정보를 임차인에게 교부한다 (임차인 화면에서 즉시 열림)."""
    room = Room.require(room_id)
    ct = Contract.get(room.active_contract_id) if room.active_contract_id else None
    if ct is None or ct.keys_released is not False:
        raise DomainError("교부 대기 중인 계약이 없습니다.", hint="이미 교부했거나 잠금 대상이 아닌 계약입니다.")
    br = ct.balance_registry or {}
    if br.get("changed"):
        raise DomainError("임차인이 잔금일 등기부에서 변동을 신고했습니다. 교부할 수 없습니다.",
                          hint="등기부를 함께 확인해 변동이 해소되거나 합의된 뒤 임차인이 다시 '변동 없음'으로 확인해야 합니다. 필요하면 본사에 문의하세요.")
    if ct.mortgage_cleanup_required and not br.get("mortgage_cleared"):
        raise DomainError("잔금 시 근저당 말소 특약이 있는 계약입니다. 근저당 말소가 확인되지 않았습니다.",
                          hint="선순위 근저당을 말소하고, 임차인이 말소된 등기부를 확인한 뒤에 교부할 수 있습니다.")
    if ct.insurance_proof_required and not (ct.insurance_proof or {}).get("policy_no"):
        raise DomainError("임차인이 '보증보험 가입 예정'으로 계약했고, 가입 증빙이 아직 제출되지 않았습니다.",
                          hint="임차인이 보증기관·증권번호를 제출해야 교부할 수 있습니다. 가입이 거절되면 계약서의 특약에 따라 계약은 무효이고 계약금은 반환됩니다.")
    if not br.get("checked_at"):
        raise DomainError("임차인의 잔금일 등기부 확인이 아직 없습니다.",
                          hint="잔금일에 임차인이 인터넷등기소에서 직접 등기부를 열람하고 변동 없음을 확인해야 합니다.")
    if not req.balance_checked:
        raise DomainError("본인 계좌의 실제 입금 내역에서 잔금을 직접 확인해야 교부할 수 있습니다.",
                          hint="임차인이 보여 주는 이체 완료 화면은 위조될 수 있습니다.")
    from apps.rent_dist._rent3_core.authz import claims_from
    who = (claims_from(request) or {}).get("u", "")
    ct.keys_released, ct.keys_released_at, ct.keys_released_by = True, now_iso(), who
    ct.save(action="keys_released", app_source=APP_SRC)
    return {"success": True, "message": f"{room.room_no}호 도어락·공동현관 정보를 임차인에게 교부했습니다 (임차인 화면에 즉시 열립니다).", "contract": ct.model_dump()}


@router.post("/rooms/{room_id}/doorlock")
def change_doorlock(room_id: str, req: DoorlockReq):
    room = Room.require(room_id)
    room.update_doorlock(req.new_pwd, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{room.room_no}호 도어락 비밀번호가 변경되어 입주자 정보에도 반영되었습니다.",
        "room": room.model_dump(),
    }


@router.post("/payments/collect")
def collect_payment(req: CollectPaymentReq):
    ensure_pool_seeded()
    res = FifoPaymentEngine.collect(
        branch=req.branch,
        room_no=req.room_no,
        amount=req.amount,
        payer_name=req.payer_name,
        paid_date=req.paid_date,
        method=req.method,
        up_to_ym=req.up_to_ym,
        app_source=APP_SRC,
    )
    return {"success": True, **res}


class PayEditReq(BaseModel):
    branch: str = "main"
    pay_id: str
    paid_date: str = ""
    rent_amount: int = 0
    maintenance_amount: int = 0


@router.post("/payments/edit")
def edit_payment(req: PayEditReq):
    """수납 내역 한 건의 납부일·임차료·관리비를 고친다 (장부 이관 오류 정정용)."""
    ensure_pool_seeded()
    pay = Pay.require(req.pay_id)
    if normalize_branch_id(pay.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 수납 내역은 고칠 수 없습니다.")
    if pay.tax_invoice:
        raise DomainError("세금계산서가 발행된 수납은 고칠 수 없습니다.", hint="수정이 필요하면 발행 기록을 정정(수정세금계산서)해야 합니다.")
    try:
        d = date.fromisoformat(req.paid_date)
    except ValueError:
        raise DomainError("납부일 형식이 올바르지 않습니다 (예: 2026-03-15).")
    if req.rent_amount < 0 or req.maintenance_amount < 0 or req.rent_amount + req.maintenance_amount <= 0:
        raise DomainError("금액은 0원보다 커야 합니다.")
    pay.paid_date = d.isoformat()
    pay.rent_amount = req.rent_amount
    pay.maintenance_amount = req.maintenance_amount
    pay.total_amount = req.rent_amount + req.maintenance_amount + pay.vat_amount
    pay.save(action="payment_edited", app_source=APP_SRC)
    return {"success": True, "message": f"{pay.room_no}호 수납 내역을 고쳤습니다 ({pay.paid_date}, {pay.total_amount:,}원).", "payment": pay.model_dump()}


class ContractEditReq(BaseModel):
    branch: str = "main"
    contract_id: str
    deposit: int
    monthly_rent: int
    maintenance_fee: int
    move_in_date: str = ""
    expiry_date: str = ""
    tenant_phone: str = ""
    memo: str = ""


@router.post("/contracts/edit")
def edit_contract(req: ContractEditReq):
    """계약 내용(보증금·월세·관리비·전입일·만기·연락처·메모) 수정. 호실 기본 금액과 임차인 연락처도 맞춘다."""
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
    if req.move_in_date:
        ct.move_in_date = req.move_in_date
    if req.expiry_date:
        ct.expiry_date = req.expiry_date
    ct.tenant_phone = req.tenant_phone.strip()
    ct.lessor_memo = req.memo.strip()
    ct.vat = ct.compute_vat()
    ct.save(action="contract_edited", app_source=APP_SRC)
    room = Room.get(ct.id_room)
    if room:
        room.deposit, room.monthly_rent, room.maintenance_fee = req.deposit, req.monthly_rent, req.maintenance_fee
        room.vat_amount = ct.vat
        room.save(action="room_terms_synced", app_source=APP_SRC)
    tenant = Tenant.get(ct.id_tenant) if ct.id_tenant else None
    if tenant and req.tenant_phone.strip():
        tenant.phone = req.tenant_phone.strip()
        tenant.save(action="tenant_phone_edited", app_source=APP_SRC)
    return {"success": True, "message": f"{ct.room_no}호 계약 내용을 저장했습니다.", "contract": ct.model_dump()}


class BuildingEditReq(BaseModel):
    branch: str = "main"
    building_name: str
    address: str = ""
    vat_rooms: Optional[List[str]] = None   # 부가세 납부 대상 호실 (None = 그대로)


@router.post("/building/edit")
def edit_building(req: BuildingEditReq):
    """건물 이름·주소 수정 (임대인 더보기 ▸ 건물 정보 수정)."""
    ensure_pool_seeded()
    name = req.building_name.strip()
    if not name:
        raise DomainError("건물 이름을 입력해 주세요.")
    b = Building.first(branch=normalize_branch_id(req.branch))
    if not b:
        raise DomainError("건물 정보를 찾을 수 없습니다.")
    b.building_name, b.address = name, req.address.strip()
    b.save(action="building_edited", app_source=APP_SRC)
    extra = ""
    if req.vat_rooms is not None:
        want = [x.strip() for x in req.vat_rooms if x and x.strip()]
        known = {r.room_no for r in Room.find(id_building=b.id)}
        unknown = [x for x in want if x not in known]
        if unknown:
            raise DomainError(f"없는 호실입니다: {', '.join(unknown)}", hint="호실 번호를 쉼표로 구분해 정확히 적어 주세요. (예: 201, 202)")
        b.apply_vat_rooms(want, app_source=APP_SRC)
        extra = f" 부가세 대상 호실: {', '.join(want) if want else '없음'}."
    return {"success": True, "message": f"건물 정보를 저장했습니다 ({b.building_name}).{extra}", "building": Building.get(b.id).model_dump()}


class TaxInvoiceReq(BaseModel):
    branch: str = "main"
    pay_id: str


@router.post("/payments/tax-invoice")
def issue_tax_invoice(req: TaxInvoiceReq):
    """부가세가 들어 있는 수납 건의 세금계산서 발행 기록을 만든다(국세청 전송은 하지 않는다 — 홈택스 입력·인쇄용)."""
    ensure_pool_seeded()
    pay = Pay.require(req.pay_id)
    if normalize_branch_id(pay.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 수납 내역입니다.")
    if pay.vat_amount <= 0:
        raise DomainError("부가세가 없는 수납은 세금계산서를 발행하지 않습니다.", hint="부가세 대상 호실의 부가세 포함 수납 건에서만 발행할 수 있습니다.")
    if pay.tax_invoice:
        return {"success": True, "already": True, "message": f"이미 발행된 세금계산서입니다 ({pay.tax_invoice.get('no')}).", "invoice": pay.tax_invoice}
    bldg = Building.first(branch=normalize_branch_id(pay.branch))
    lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=normalize_branch_id(pay.branch))
    ct = Contract.get(pay.id_contract)
    tenant = Tenant.get(pay.id_tenant) if pay.id_tenant else None
    supply = pay.total_amount - pay.vat_amount
    pay.tax_invoice = {
        "no": f"TI-{pay.paid_date.replace('-', '')}-{pay.id[-5:].upper()}",
        "issued_at": now_iso(),
        "write_date": pay.paid_date,
        "billing_month": pay.billing_month,
        "item": f"{pay.room_no}호 임대료·관리비 ({pay.billing_month}분)",
        "supply": supply,
        "vat": pay.vat_amount,
        "total": pay.total_amount,
        "supplier": {"name": (bldg.building_name if bldg else ""), "rep": (lessor.name if lessor else ""),
                     "biz_no": (lessor.business_number if lessor else ""), "address": (bldg.address if bldg else "")},
        "buyer": {"name": (ct.tenant_name if ct else pay.payer_name), "biz_no": (tenant.business_number if tenant else ""),
                  "phone": ((ct.tenant_phone if ct else "") or (tenant.phone if tenant else ""))},
    }
    pay.save(action="tax_invoice_issued", app_source=APP_SRC)
    return {"success": True, "already": False, "message": f"{pay.room_no}호 세금계산서 발행 기록을 만들었습니다 ({pay.tax_invoice['no']}).", "invoice": pay.tax_invoice}


@router.post("/payments/sms-paste")
def paste_sms_payment(req: SmsPasteReq):
    ensure_pool_seeded()
    res = FifoPaymentEngine.parse_and_collect_sms(
        branch=req.branch,
        sms_text=req.sms_text,
        up_to_ym=req.up_to_ym,
        app_source=APP_SRC,
    )
    return {"success": True, **res}


@router.post("/outlays")
def create_outlay(req: OutlayCreateReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    bldg = Building.first(branch=b_id)
    item = Outlay(
        branch=b_id,
        id_building=bldg.id if bldg else "",
        category=req.category,
        title=req.title,
        amount=req.amount,
        vendor=req.vendor,
        memo=req.memo,
        **({"expense_date": req.expense_date} if req.expense_date else {}),
    ).save(action="outlay_recorded", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"공용 운영비 '{item.title}' ({item.amount:,}원)가 등록되었습니다.",
        "outlay": item.model_dump(),
    }


@router.get("/tax/summary")
def tax_summary(branch: str = "main", year: Optional[int] = None):
    """부가세·종합소득세 자료와 경비 지출 목록 (더보기 ▸ 부가세·종소세·경비)."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    yr = year or date_today().year
    fin = TaxAndCertificateService.branch_financial_summary(b_id, yr)
    pays = [p for p in Pay.find(branch=b_id) if p.payment_kind == "MONTHLY_RENT" and p.paid_date.startswith(f"{yr:04d}")]
    periods = []
    for label, months in (("1기 (1~6월)", range(1, 7)), ("2기 (7~12월)", range(7, 13))):
        ps = [p for p in pays if int(p.paid_date[5:7]) in months]
        periods.append({
            "label": label,
            "supply": sum(p.rent_amount + p.maintenance_amount for p in ps),
            "vat": sum(p.vat_amount for p in ps),
            "taxable_count": sum(1 for p in ps if p.vat_amount > 0),
            "issued_count": sum(1 for p in ps if p.vat_amount > 0 and p.tax_invoice),
        })
    outlays = sorted((o for o in Outlay.find(branch=b_id) if o.expense_date.startswith(f"{yr:04d}")), key=lambda o: o.expense_date, reverse=True)
    return {"success": True, "year": yr, "summary": fin, "vat_periods": periods,
            "outlays": [{**o.model_dump(), "category_label": o.category_label} for o in outlays],
            "expense_categories": EXPENSE_CATEGORIES}


class OutlayEditReq(BaseModel):
    branch: str = "main"
    outlay_id: str
    category: str
    title: str
    amount: int
    vendor: str = ""
    expense_date: str
    memo: str = ""


@router.post("/outlays/edit")
def edit_outlay(req: OutlayEditReq):
    ensure_pool_seeded()
    o = Outlay.require(req.outlay_id)
    if normalize_branch_id(o.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 지출은 고칠 수 없습니다.")
    if req.category not in EXPENSE_CATEGORIES:
        raise DomainError("분류가 올바르지 않습니다.")
    if req.amount <= 0:
        raise DomainError("금액은 0원보다 커야 합니다.")
    try:
        date.fromisoformat(req.expense_date)
    except ValueError:
        raise DomainError("지출일 형식이 올바르지 않습니다 (예: 2026-03-15).")
    o.category, o.title, o.amount = req.category, req.title.strip() or o.title, req.amount
    o.vendor, o.expense_date, o.memo = req.vendor.strip(), req.expense_date, req.memo.strip()
    o.save(action="outlay_edited", app_source=APP_SRC)
    return {"success": True, "message": f"지출 '{o.title}'을 고쳤습니다.", "outlay": o.model_dump()}


class OutlayDeleteReq(BaseModel):
    branch: str = "main"
    outlay_id: str


@router.post("/outlays/delete")
def delete_outlay(req: OutlayDeleteReq):
    ensure_pool_seeded()
    o = Outlay.require(req.outlay_id)
    if normalize_branch_id(o.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 지출은 지울 수 없습니다.")
    title = o.title
    o.delete(action="outlay_deleted", app_source=APP_SRC)
    return {"success": True, "message": f"지출 '{title}'을 지웠습니다."}


@router.get("/tax/export-csv")
def export_tax_csv(branch: str = "main", year: Optional[int] = None):
    ensure_pool_seeded()
    csv_body = TaxAndCertificateService.export_annual_tax_csv(branch=branch, year=year)
    b_id = normalize_branch_id(branch)
    filename = f"rent3_tax_statement_{b_id}_{year or 2026}.csv"
    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: str, req: TicketAssignReq):
    t = MaintenanceTicket.require(ticket_id)
    t.assign_vendor(req.vendor_name, req.vendor_phone, req.visit_date, app_source=APP_SRC)
    return {"success": True, "message": f"{t.room_no}호 A/S 기사 배정 완료 및 세입자 채팅 안내가 발송되었습니다.", "ticket": t.model_dump()}


@router.post("/tickets/{ticket_id}/complete")
def complete_ticket(ticket_id: str, req: TicketCompleteReq):
    t = MaintenanceTicket.require(ticket_id)
    t.complete_repair(
        repair_cost=req.repair_cost,
        cost_burden=req.cost_burden,
        resolution_note=req.resolution_note,
        app_source=APP_SRC,
    )
    return {"success": True, "message": f"{t.room_no}호 A/S 완료 및 필요경비 자동 등록이 완료되었습니다.", "ticket": t.model_dump()}


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
    )
    return {
        "success": True,
        "message": f"{s.room_no}호 퇴실 정산 명세서(실반환 {s.refund_amount:,}원)가 생성되어 임차인 앱에 공유되었습니다.",
        "settlement": s.model_dump(),
    }


@router.post("/checkout/{settlement_id}/complete")
def complete_checkout(settlement_id: str):
    s = CheckOutSettlement.require(settlement_id)
    s.complete_refund_and_vacate(app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{s.room_no}호 보증금 반환 종결 — 호실이 즉시 '공실 매물 공개' 상태로 자동 복귀되었습니다!",
        "settlement": s.model_dump(),
    }


@router.post("/chats")
def send_lessor_chat(req: ChatReplyReq):
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    lessor = Lessor.first(branch=b_id)
    msg = DirectChat(
        branch=b_id,
        id_room=room.id if room else "",
        room_no=req.room_no,
        sender_role="lessor",
        sender_name=lessor.name if lessor else "임대인",
        message=req.message.strip(),
    ).save(action="lessor_chat_sent", app_source=APP_SRC)
    return {"success": True, "message": "메시지가 전송되었습니다.", "chat": msg.model_dump()}


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
        if rtms_status == "PENDING" else "임차인 앱 마이홈에서도 첨부된 계약서 사본과 납부 현황이 즉시 연동됩니다."
    )
    return {"success": True, "level": level, "message": msg, "hint": hint, **res}


@router.get("/contracts/{contract_id}/photo")
def get_contract_photo(contract_id: str):
    ensure_pool_seeded()
    abs_path, mime, filename = BrokerContractOnboarding.resolve_photo_file(contract_id)
    return FileResponse(path=abs_path, media_type=mime, filename=filename)


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
    proposed_term_months: int = 12
    renewal_note: str = ""
    open_cosign_session: bool = True


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
        from core.errors import DomainError
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
        f"📦 {chk.room_no}호 전출 예정({chk.planned_move_out_date}) 기록 완료 — 공실 쇼케이스에 사전예약 매물로 게시되었습니다."
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
        from core.errors import DomainError
        raise DomainError(f"{req.room_no}호에 활성 계약이 없습니다.")
    ct = Contract.require(room.active_contract_id)
    chk = ExpiryRenewalCheck.ensure_for_contract(ct).send_renewal_preview(
        proposed_deposit=req.proposed_deposit,
        proposed_monthly_rent=req.proposed_monthly_rent,
        proposed_maintenance_fee=req.proposed_maintenance_fee,
        proposed_term_months=req.proposed_term_months,
        renewal_note=req.renewal_note,
        open_cosign_session=req.open_cosign_session,
        app_source=APP_SRC,
    )
    code_hint = (
        f"공동 재계약방(참여코드 {chk.cosign_session_code})이 함께 개설되어 임차인 마이홈에 전달되었습니다."
        if chk.cosign_session_code else "임차인 마이홈에 연장 재계약 예고장이 전달되었습니다."
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


class SendBillingNoticeReq(BaseModel):
    branch: str = "main"
    room_no: str
    notice_type: str                         # PRE_DUE_3D / OVERDUE_1D
    as_of_date: Optional[str] = None
    force_pre_due: bool = True


class AutoDispatchBillingReq(BaseModel):
    branch: str = "main"
    as_of_date: Optional[str] = None
    include_catchup_overdue: bool = False


@router.post("/billing-notices/send")
def send_room_billing_notice(req: SendBillingNoticeReq):
    ensure_pool_seeded()
    bn = RentBillingNotice.send_for_room(
        branch=req.branch,
        room_no=req.room_no,
        notice_type=req.notice_type,
        as_of_date=req.as_of_date,
        force_pre_due=req.force_pre_due,
        app_source=APP_SRC,
    )
    is_overdue = bn.notice_type == "OVERDUE_1D"
    return {
        "success": True,
        "level": "warning" if is_overdue else "success",
        "message": (
            f"🚨 {bn.room_no}호({bn.tenant_name}) 미납 통보 발송 완료 — 납입일({bn.due_date}) 경과 미납액 {bn.amount_due:,}원"
            if is_overdue
            else f"🔔 {bn.room_no}호({bn.tenant_name}) 납입일({bn.due_date}) 사전 안내 발송 완료 — 예정액 {bn.amount_due:,}원"
        ),
        "hint": "임차인 마이홈 상단 알림 배너와 1:1 메시지로 실시간 전달되었습니다.",
        "notice": bn.model_dump(),
    }


@router.post("/billing-notices/auto-dispatch")
def auto_dispatch_billing_notices(req: AutoDispatchBillingReq):
    ensure_pool_seeded()
    res = RentBillingNotice.run_daily_auto_dispatch(
        branch=req.branch,
        as_of_date=req.as_of_date,
        include_catchup_overdue=req.include_catchup_overdue,
        app_source=APP_SRC,
    )
    pre_cnt = len(res["sent_pre_due"])
    ov_cnt = len(res["sent_overdue"])
    skip_cnt = int(res["skipped_duplicates"])
    if pre_cnt == 0 and ov_cnt == 0:
        msg = (
            f"ℹ️ 기준일({res['as_of_date']}) 현재 신규 발송할 통보 대상이 없습니다 "
            f"(납입 3일전 0건 · 납입+1일 미납 0건, 기발송 중복 스킵 {skip_cnt}건)."
        )
        lvl = "warning"
    else:
        msg = (
            f"⚡ 정기 자동통보 실행 완료 ({res['as_of_date']} 기준) — "
            f"납입일 3일전 사전통보 {pre_cnt}건 · 납입일+1일 미납통보 {ov_cnt}건 발송!"
        )
        lvl = "success"
    return {"success": True, "level": lvl, "message": msg, **res}



