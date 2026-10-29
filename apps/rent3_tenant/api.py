"""
apps/rent3_tenant/api.py — 임차인 및 예비 세입자 전용 API 라우터.
"""
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.errors import DomainError, NotFound
from apps._rent3_core import (
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
    Room,
    TaxAndCertificateService,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter()
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
        b = bldgs.get(r.id_building)
        lessor = lessors.get(b.id_lessor) if (b and b.id_lessor) else None
        sess = sessions_by_room.get(r.id)
        cards.append({
            **r.model_dump(),
            "is_pre_moveout_listing": r.is_occupied and r.pre_moveout_notice,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "lessor_name": lessor.name if lessor else "임대인",
            "lessor_phone": lessor.phone if lessor else "",
            "active_cosign_session": sess.model_dump() if sess else None,
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
        "message": f"{req.room_no}호 임대인에게 1:1 방문투어/문의 메시지가 실시간 전달되었습니다 (ops.json 반영).",
        "chat": chat.model_dump(),
    }


@router.get("/tenants")
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


@router.get("/myhome/{tenant_id}")
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

    tickets = sorted(MaintenanceTicket.find(id_tenant=t.id), key=lambda x: x.updated_at, reverse=True)
    checkouts = [c for c in CheckOutSettlement.find(id_tenant=t.id) if c.status != "COMPLETED"]
    chats = sorted(
        DirectChat.find(branch=t.branch, room_no=t.room_no),
        key=lambda x: x.created_at,
        reverse=True,
    )[:20]
    payments = sorted(Pay.find(id_tenant=t.id), key=lambda p: (p.paid_date, p.created_at), reverse=True)

    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "tenant": t.model_dump(),
        "building": bldg.model_dump() if bldg else None,
        "room": room.model_dump() if room else None,
        "contract": ct.model_dump() if ct else None,
        "expiry_window": exp_win,
        "expiry_check": exp_chk.model_dump() if exp_chk else None,
        "ledger": ledger,
        "certificate": cert,
        "payments": [p.model_dump() for p in payments],
        "tickets": [tk.model_dump() for tk in tickets],
        "pending_checkout": checkouts[0].model_dump() if checkouts else None,
        "chats": [c.model_dump() for c in chats],
    }


@router.post("/myhome/{tenant_id}/pay")
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


@router.post("/myhome/{tenant_id}/vehicle")
def update_vehicle(tenant_id: str, req: VehicleReq):
    t = Tenant.require(tenant_id)
    t.register_vehicle(req.car_number, req.car_model, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"차량번호 '{t.car_number}'가 건물 주차 대장(users.json)에 등록되었습니다.",
        "tenant": t.model_dump(),
    }


@router.post("/myhome/{tenant_id}/checkin")
def confirm_checkin(tenant_id: str, req: CheckinReq):
    t = Tenant.require(tenant_id)
    t.confirm_checkin(req.memo, app_source=APP_SRC)
    return {
        "success": True,
        "message": "첫 입실 체크인 확인이 저장되었습니다.",
        "tenant": t.model_dump(),
    }


@router.post("/myhome/{tenant_id}/tickets")
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
        "message": f"하자보수(A/S) 요청이 접수되어 임대인 관제 대장(ops.json)에 실시간 전달되었습니다.",
        "ticket": tk.model_dump(),
    }


@router.post("/checkout/{settlement_id}/agree")
def agree_checkout(settlement_id: str, req: CheckoutAgreeReq):
    s = CheckOutSettlement.require(settlement_id)
    s.tenant_agree(req.bank_account, req.signature, app_source=APP_SRC)
    return {
        "success": True,
        "message": "퇴실 정산 명세 동의 및 반환계좌 등록이 완료되었습니다 (임대인 앱 즉시 반영).",
        "settlement": s.model_dump(),
    }


@router.get("/contracts/{contract_id}/photo")
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


@router.post("/myhome/{tenant_id}/expiry-plan")
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


@router.post("/myhome/{tenant_id}/acknowledge-renewal")
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



