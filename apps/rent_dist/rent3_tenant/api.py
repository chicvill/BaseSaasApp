"""
apps/rent3_tenant/api.py — 임차인 및 예비 세입자 전용 API 라우터.
"""
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from apps.rent_dist._rent3_core.pool import now_iso

from core.errors import DomainError, NotFound
from apps.rent_dist._rent3_core.authz import any_user, api_headers, public_room, staff_only, tenant_scope
from apps.rent_dist._rent3_core import (
    BrokerContractOnboarding,
    Building,
    CheckOutSettlement,
    CoSignSession,
    Contract,
    DirectChat,
    ExpiryRenewalCheck,
    FifoPaymentEngine,
    Lessor,
    MaintenanceTicket,
    Pay,
    RentBillingNotice,
    Room,
    TaxAndCertificateService,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter(dependencies=[Depends(api_headers)])
LOCKED_TEXT = "입주 당일 잔금 완납 후 공개"
APP_SRC = "rent3_tenant"


class InquiryReq(BaseModel):
    branch: str = "main"
    room_no: str
    sender_name: str
    message: str


class OnlinePayReq(BaseModel):
    amount: int
    method: str = "KAKAOPAY"


class VehicleReq(BaseModel):
    car_number: str
    car_model: str = ""


class CheckinReq(BaseModel):
    memo: str = "도어락 및 실내 시설 정상 확인"


class TicketSubmitReq(BaseModel):
    category: str = "설비/배관"
    title: str
    description: str = ""


class CheckoutAgreeReq(BaseModel):
    bank_account: str
    signature: str


@router.get("/status")
def get_status():
    ensure_pool_seeded()
    listings = [r for r in Room.find(is_published=True) if (not r.is_occupied) or r.pre_moveout_notice]
    return {
        "success": True,
        "app_id": "rent3_tenant",
        "status": "HEALTHY",
        "pool_revision": pool().overview()["total_revision"],
        "open_listings": len(listings),
        "active_tenants": Tenant.count(status="ACTIVE"),
    }


@router.get("/listings")
def list_open_listings(branch: Optional[str] = None):
    ensure_pool_seeded()
    rooms = Room.find(branch=normalize_branch_id(branch)) if branch else Room.all()
    bldgs = {b.id: b for b in Building.all()}
    lessors = {l.id: l for l in Lessor.all()}
    sessions_by_room = {
        s.id_room: s for s in CoSignSession.find(status="IN_PROGRESS")
    }

    cards = []
    for r in sorted(rooms, key=lambda x: (x.branch, x.room_no)):
        if (r.is_occupied and not r.pre_moveout_notice) or not r.is_published:
            continue
        if not Lessor.franchise_open(r.branch):
            continue
        b = bldgs.get(r.id_building)
        lessor = lessors.get(b.id_lessor) if (b and b.id_lessor) else None
        sess = sessions_by_room.get(r.id)
        cards.append({
            **public_room(r.model_dump()),  # 도어락·등기·입주자 정보는 공개 목록에 싣지 않는다
            "is_pre_moveout_listing": r.is_occupied and r.pre_moveout_notice,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "lessor_name": lessor.name if lessor else "임대인",
            "lessor_phone": lessor.phone if lessor else "",
            "active_cosign_session": {"id": sess.id, "session_code": sess.session_code} if sess else None,
        })

    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "listings": cards,
    }


@router.post("/inquiries")
def submit_inquiry(req: InquiryReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    chat = DirectChat(
        branch=b_id,
        id_room=room.id if room else "",
        room_no=req.room_no,
        sender_role="tenant",
        sender_name=req.sender_name.strip() or "예비임차인",
        message=req.message.strip(),
    ).save(action="prospect_inquiry_sent", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{req.room_no}호 임대인에게 1:1 방문투어/문의 메시지가 실시간 전달되었습니다 .",
        "chat": chat.model_dump(),
    }


@router.get("/tenants", dependencies=[Depends(staff_only)])
def list_tenants():
    ensure_pool_seeded()
    tenants = Tenant.find(status="ACTIVE")
    bldgs = {b.id: b for b in Building.all()}
    return {
        "success": True,
        "tenants": [
            {
                **t.model_dump(),
                "building_name": bldgs[t.id_building].building_name if t.id_building in bldgs else t.branch,
            }
            for t in tenants
        ],
    }


@router.get("/myhome/{tenant_id}", dependencies=[Depends(tenant_scope)])
def get_myhome(tenant_id: str):
    ensure_pool_seeded()
    t = Tenant.get(tenant_id) or Tenant.first(phone=tenant_id)
    if not t:
        raise NotFound(f"임차인('{tenant_id}') 정보를 찾을 수 없습니다.")

    room = Room.get(t.current_room_id) if t.current_room_id else None
    bldg = Building.get(t.id_building) if t.id_building else (Building.get(room.id_building) if room else None)
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else Contract.first(id_tenant=t.id, status="ACTIVE")
    ledger = ct.ledger() if ct else None
    cert = TaxAndCertificateService.build_tenant_tax_certificate(ct.id) if ct else None
    exp_win = ct.expiry_window_status() if ct else None
    exp_chk = ExpiryRenewalCheck.for_contract(ct.id) if ct else None
    bill_sched = ct.billing_notice_schedule() if ct else None
    bill_notices = [bn.model_dump() for bn in RentBillingNotice.for_contract(ct.id, limit=10)] if ct else []

    tickets = sorted(MaintenanceTicket.find(id_tenant=t.id), key=lambda x: x.updated_at, reverse=True)
    checkouts = [c for c in CheckOutSettlement.find(id_tenant=t.id) if c.status != "COMPLETED"]
    chats = sorted(
        DirectChat.find(branch=t.branch, room_no=t.room_no),
        key=lambda x: x.created_at,
        reverse=True,
    )[:20]
    payments = sorted(Pay.find(id_tenant=t.id), key=lambda p: (p.paid_date, p.created_at), reverse=True)

    tenant_d = t.model_dump()
    bldg_d = bldg.model_dump() if bldg else None
    room_d = room.model_dump() if room else None
    keys_locked = bool(ct and ct.keys_released is False)  # 잔금 확인 전: 출입 정보를 API 단계에서 가린다
    if keys_locked:
        tenant_d["doorlock_pwd"] = LOCKED_TEXT
        if room_d is not None:
            room_d["doorlock_pwd"] = LOCKED_TEXT
        if bldg_d is not None:
            bldg_d["common_entrance_pwd"] = LOCKED_TEXT
    balance = max(int(ct.deposit) - int(ct.contract_deposit), 0) if ct else 0

    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "tenant": tenant_d,
        "keys": {"registry_check": (ct.balance_registry if ct else {}), "locked": keys_locked, "message": LOCKED_TEXT if keys_locked else "",
                 "balance_due": balance if keys_locked else 0,
                 "move_in_date": ct.move_in_date if ct else "", "released_at": ct.keys_released_at if ct else ""},
        "building": bldg_d,
        "room": room_d,
        "contract": ct.model_dump() if ct else None,
        "expiry_window": exp_win,
        "expiry_check": exp_chk.model_dump() if exp_chk else None,
        "billing_schedule": bill_sched,
        "billing_notices": bill_notices,
        "ledger": ledger,
        "certificate": cert,
        "payments": [p.model_dump() for p in payments],
        "tickets": [tk.model_dump() for tk in tickets],
        "pending_checkout": checkouts[0].model_dump() if checkouts else None,
        "chats": [c.model_dump() for c in chats],
    }


class BalanceRegistryReq(BaseModel):
    changed: bool                 # 직접 열람해 보니 계약일 이후 변동이 있다
    issue_number: str = ""        # 이번에 열람한 등기부의 발급확인번호
    mortgage_cleared: bool = False  # (말소 특약 계약) 열람한 등기부에서 선순위 근저당이 모두 말소된 것을 확인했다


@router.post("/myhome/{tenant_id}/balance-registry-check", dependencies=[Depends(tenant_scope)])
def balance_registry_check(tenant_id: str, req: BalanceRegistryReq):
    """잔금일 등기부 재열람 결과. 임차인이 인터넷등기소에서 직접 열람한 뒤 변동 여부를 기록한다.
    변동 없음이어야 임대인이 도어락·공동현관 정보를 교부할 수 있다. 변동이 있으면 잠금이 유지되고 임대인 화면에 표시된다."""
    t = Tenant.get(tenant_id) or Tenant.first(phone=tenant_id)
    if not t:
        raise NotFound(f"임차인('{tenant_id}') 정보를 찾을 수 없습니다.")
    room = Room.get(t.current_room_id) if t.current_room_id else None
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
    if ct is None or ct.keys_released is not False:
        raise DomainError("잔금일 등기부 확인 대상 계약이 아닙니다.", hint="출입 정보 교부 전의 새 계약에서만 사용합니다.")
    if ct.mortgage_cleanup_required and not req.changed and not req.mortgage_cleared:
        raise DomainError(
            "이 계약은 '잔금 시 근저당 말소 특약'이 있습니다. 등기부에서 근저당이 말소된 것을 확인해야 합니다.",
            hint="아직 말소되지 않았다면 '변동 있음'이 아니라 잔금을 보내지 말고 임대인에게 말소를 요구하세요. 말소된 등기부를 확인한 뒤 다시 확인해 주세요.",
        )
    ct.balance_registry = {"checked_at": now_iso(), "changed": bool(req.changed), "issue_number": req.issue_number.strip(),
                           "mortgage_cleared": bool(req.mortgage_cleared)}
    ct.save(action="balance_registry_changed_reported" if req.changed else "balance_registry_checked", app_source=APP_SRC)
    msg = ("등기부 변동을 신고했습니다. 임대인·본사 확인 전까지 잔금 납부를 보류하세요." if req.changed
           else "잔금일 등기부 확인을 기록했습니다. 잔금 입금 후 임대인이 확인하면 출입 정보가 열립니다.")
    return {"success": True, "message": msg, "balance_registry": ct.balance_registry}


@router.post("/myhome/{tenant_id}/pay", dependencies=[Depends(tenant_scope)])
def pay_monthly_rent(tenant_id: str, req: OnlinePayReq):
    t = Tenant.require(tenant_id)
    if not t.room_no:
        raise DomainError("현재 입주 중인 호실이 없습니다.")
    res = FifoPaymentEngine.collect(
        branch=t.branch,
        room_no=t.room_no,
        amount=req.amount,
        payer_name=t.name,
        method=req.method,
        app_source=APP_SRC,
    )
    return {"success": True, **res}


@router.post("/myhome/{tenant_id}/vehicle", dependencies=[Depends(tenant_scope)])
def update_vehicle(tenant_id: str, req: VehicleReq):
    t = Tenant.require(tenant_id)
    t.register_vehicle(req.car_number, req.car_model, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"차량번호 '{t.car_number}'가 건물 주차 대장에 등록되었습니다.",
        "tenant": t.model_dump(),
    }


@router.post("/myhome/{tenant_id}/checkin", dependencies=[Depends(tenant_scope)])
def confirm_checkin(tenant_id: str, req: CheckinReq):
    t = Tenant.require(tenant_id)
    t.confirm_checkin(req.memo, app_source=APP_SRC)
    return {
        "success": True,
        "message": "첫 입실 체크인 확인이 저장되었습니다.",
        "tenant": t.model_dump(),
    }


@router.post("/myhome/{tenant_id}/tickets", dependencies=[Depends(tenant_scope)])
def create_ticket(tenant_id: str, req: TicketSubmitReq):
    t = Tenant.require(tenant_id)
    tk = MaintenanceTicket(
        branch=t.branch,
        id_building=t.id_building,
        id_room=t.current_room_id,
        room_no=t.room_no,
        id_tenant=t.id,
        tenant_name=t.name,
        category=req.category,
        title=req.title.strip(),
        description=req.description.strip(),
        status="RECEIVED",
    ).save(action="ticket_submitted_by_tenant", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"하자보수(A/S) 요청이 접수되어 임대인 화면에 실시간 전달되었습니다.",
        "ticket": tk.model_dump(),
    }


@router.post("/checkout/{settlement_id}/agree", dependencies=[Depends(any_user)])
def agree_checkout(settlement_id: str, req: CheckoutAgreeReq):
    s = CheckOutSettlement.require(settlement_id)
    s.tenant_agree(req.bank_account, req.signature, app_source=APP_SRC)
    return {
        "success": True,
        "message": "퇴실 정산 명세 동의 및 반환계좌 등록이 완료되었습니다 (임대인 앱 즉시 반영).",
        "settlement": s.model_dump(),
    }


@router.get("/contracts/{contract_id}/photo", dependencies=[Depends(any_user)])
def get_tenant_contract_photo(contract_id: str):
    ensure_pool_seeded()
    abs_path, mime, filename = BrokerContractOnboarding.resolve_photo_file(contract_id)
    return FileResponse(path=abs_path, media_type=mime, filename=filename)


class TenantMoveoutPlanReq(BaseModel):
    tenant_plan: str                         # MOVE_OUT / STAY
    planned_move_out_date: str = ""
    tenant_memo: str = ""
    prelist_vacancy: bool = True


class TenantAckRenewalReq(BaseModel):
    tenant_memo: str = ""


@router.post("/myhome/{tenant_id}/expiry-plan", dependencies=[Depends(tenant_scope)])
def submit_tenant_expiry_plan(tenant_id: str, req: TenantMoveoutPlanReq):
    ensure_pool_seeded()
    t = Tenant.require(tenant_id)
    room = Room.get(t.current_room_id) if t.current_room_id else None
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else Contract.first(id_tenant=t.id, status="ACTIVE")
    if not ct:
        raise DomainError("현재 진행 중인 임대차 계약을 찾을 수 없습니다.")

    chk = ExpiryRenewalCheck.ensure_for_contract(ct).respond_moveout_plan(
        tenant_plan=req.tenant_plan,
        planned_move_out_date=req.planned_move_out_date,
        tenant_memo=req.tenant_memo,
        prelist_vacancy=req.prelist_vacancy,
        app_source=APP_SRC,
    )
    msg = (
        f"📦 만기 전출(퇴실) 예정일({chk.planned_move_out_date})이 임대인에게 전달되었습니다."
        if chk.tenant_plan == "MOVE_OUT"
        else "🏠 '전출의사 없음(거주 연장 희망)'이 임대인에게 전달되었습니다. 곧 임대인의 연장 재계약 조건 예고가 도착합니다."
    )
    return {"success": True, "level": "success", "message": msg, "check": chk.model_dump()}


@router.post("/myhome/{tenant_id}/acknowledge-renewal", dependencies=[Depends(tenant_scope)])
def acknowledge_tenant_renewal_preview(tenant_id: str, req: TenantAckRenewalReq):
    ensure_pool_seeded()
    t = Tenant.require(tenant_id)
    room = Room.get(t.current_room_id) if t.current_room_id else None
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else Contract.first(id_tenant=t.id, status="ACTIVE")
    if not ct:
        raise DomainError("현재 진행 중인 임대차 계약을 찾을 수 없습니다.")
    chk = ExpiryRenewalCheck.for_contract(ct.id)
    if not chk:
        raise NotFound("발송된 연장 재계약 예고 내역이 없습니다.")
    chk.acknowledge_renewal_preview(tenant_memo=req.tenant_memo, app_source=APP_SRC)
    return {
        "success": True,
        "level": "success",
        "message": "✅ 연장 재계약 예고 조건 확인 및 동의가 임대인에게 전달되었습니다!",
        "check": chk.model_dump(),
    }


@router.post("/myhome/{tenant_id}/billing-notices/{notice_id}/acknowledge", dependencies=[Depends(tenant_scope)])
def acknowledge_tenant_billing_notice(tenant_id: str, notice_id: str):
    ensure_pool_seeded()
    t = Tenant.require(tenant_id)
    bn = RentBillingNotice.require(notice_id)
    if bn.id_tenant != t.id:
        raise DomainError("본인에게 발송된 통보 내역만 확인 처리할 수 있습니다.")
    bn.acknowledge(app_source=APP_SRC)
    return {
        "success": True,
        "level": "success",
        "message": "✅ 임대료 안내 통보 확인 처리가 완료되었습니다 (임대인 앱 실시간 동기화).",
        "notice": bn.model_dump(),
    }



