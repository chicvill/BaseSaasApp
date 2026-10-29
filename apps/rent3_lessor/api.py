"""
apps/rent3_lessor/api.py — 임대인 전용 API 라우터.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from apps._rent3_core import (
    Building,
    CheckOutSettlement,
    CoSignSession,
    Contract,
    DirectChat,
    EXPENSE_CATEGORIES,
    FifoPaymentEngine,
    Lessor,
    MaintenanceTicket,
    Outlay,
    Pay,
    Room,
    TaxAndCertificateService,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter()
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
        "pool_revision": pool().overview()["total_revision"],
    }


@router.get("/dashboard")
def get_dashboard(branch: str = "main", up_to_ym: Optional[str] = None):
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    buildings = Building.all()
    bldg = Building.first(branch=b_id) or (buildings[0] if buildings else None)
    lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)

    rooms = sorted(Room.find(branch=b_id), key=lambda r: str(r.room_no))
    tenants_by_id = {t.id: t for t in Tenant.find(branch=b_id)}

    room_cards: List[Dict[str, Any]] = []
    total_unpaid_branch = 0
    for r in rooms:
        ct = Contract.get(r.active_contract_id) if r.active_contract_id else None
        ledger = ct.ledger(up_to_ym=up_to_ym) if ct else None
        if ledger:
            total_unpaid_branch += int(ledger["total_unpaid"])
        tenant = tenants_by_id.get(r.id_tenant) if r.id_tenant else None
        room_cards.append({
            **r.model_dump(),
            "is_occupied": r.is_occupied,
            "tenant": tenant.model_dump() if tenant else None,
            "contract": ct.model_dump() if ct else None,
            "ledger": ledger,
        })

    payments = sorted(Pay.find(branch=b_id), key=lambda p: (p.paid_date, p.created_at), reverse=True)
    outlays = sorted(Outlay.find(branch=b_id), key=lambda o: o.expense_date, reverse=True)
    tickets = sorted(MaintenanceTicket.find(branch=b_id), key=lambda t: t.updated_at, reverse=True)
    checkouts = sorted(CheckOutSettlement.find(branch=b_id), key=lambda c: c.updated_at, reverse=True)
    chats = sorted(DirectChat.find(branch=b_id), key=lambda c: c.created_at, reverse=True)[:30]
    cosign_sessions = sorted(CoSignSession.find(branch=b_id), key=lambda s: s.updated_at, reverse=True)
    fin = TaxAndCertificateService.branch_financial_summary(b_id)

    return {
        "success": True,
        "pool_revision": pool().overview()["total_revision"],
        "branch": b_id,
        "branches": [{"branch": b.branch, "building_name": b.building_name} for b in buildings],
        "building": bldg.model_dump() if bldg else None,
        "lessor": lessor.model_dump() if lessor else None,
        "total_unpaid_branch": total_unpaid_branch,
        "rooms": room_cards,
        "payments": [p.model_dump() for p in payments[:30]],
        "outlays": [{**o.model_dump(), "category_label": o.category_label} for o in outlays],
        "expense_categories": EXPENSE_CATEGORIES,
        "tickets": [t.model_dump() for t in tickets],
        "checkouts": [c.model_dump() for c in checkouts],
        "chats": [c.model_dump() for c in chats],
        "cosign_sessions": [s.model_dump() for s in cosign_sessions[:10]],
        "financial_summary": fin,
    }


@router.post("/rooms")
def create_or_update_room(req: RoomCreateReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    bldg = Building.first(branch=b_id)
    existing = Room.first(branch=b_id, room_no=str(req.room_no))
    if existing:
        existing.name = req.name or existing.name
        existing.floor = req.floor
        existing.area_sqm = req.area_sqm
        existing.room_category = req.room_category
        existing.vat_applicable = req.vat_applicable
        existing.deposit = req.deposit
        existing.monthly_rent = req.monthly_rent
        existing.maintenance_fee = req.maintenance_fee
        existing.pay_timing = req.pay_timing
        existing.status_description = req.status_description
        existing.is_published = req.is_published
        existing.save(action="room_updated", app_source=APP_SRC)
        return {"success": True, "message": f"{req.room_no}호 정보가 rooms.json에 즉시 업데이트되었습니다.", "room": existing.model_dump()}

    room = Room(
        id=f"room_{b_id}_{req.room_no}",
        branch=b_id,
        id_building=bldg.id if bldg else "",
        room_no=str(req.room_no),
        name=req.name or f"{req.room_no}호 ({req.room_category})",
        floor=req.floor,
        area_sqm=req.area_sqm,
        room_category=req.room_category,
        vat_applicable=req.vat_applicable,
        deposit=req.deposit,
        monthly_rent=req.monthly_rent,
        maintenance_fee=req.maintenance_fee,
        pay_timing=req.pay_timing,
        status_description=req.status_description,
        is_published=req.is_published,
    ).save(action="room_created", app_source=APP_SRC)
    return {"success": True, "message": f"신규 {room.room_no}호가 rooms.json에 등록되었습니다.", "room": room.model_dump()}


@router.post("/rooms/{room_id}/publish")
def toggle_room_publish(room_id: str, req: PublishToggleReq):
    room = Room.require(room_id)
    room.is_published = req.is_published
    room.save(action="room_publish_toggled", app_source=APP_SRC)
    state_label = "직거래 매물 공개" if room.is_published else "매물 비공개"
    return {
        "success": True,
        "message": f"{room.room_no}호가 '{state_label}' 상태로 전환되었습니다 (임차인 앱 즉시 반영).",
        "room": room.model_dump(),
    }


@router.post("/rooms/{room_id}/doorlock")
def change_doorlock(room_id: str, req: DoorlockReq):
    room = Room.require(room_id)
    room.update_doorlock(req.new_pwd, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{room.room_no}호 도어락 비밀번호가 변경되어 rooms.json 및 입주자 정보에 동기화되었습니다.",
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
        "message": f"공용 운영비 '{item.title}' ({item.amount:,}원)가 outlays.json에 반영되었습니다.",
        "outlay": item.model_dump(),
    }


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
    return {"success": True, "message": f"{t.room_no}호 A/S 완료 및 필요경비(outlays.json) 자동 등재가 완료되었습니다.", "ticket": t.model_dump()}


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
    room = Room.first(branch=b_id, room_no=str(req.room_no))
    lessor = Lessor.first(branch=b_id)
    msg = DirectChat(
        branch=b_id,
        id_room=room.id if room else "",
        room_no=str(req.room_no),
        sender_role="lessor",
        sender_name=lessor.name if lessor else "임대인",
        message=req.message.strip(),
    ).save(action="lessor_chat_sent", app_source=APP_SRC)
    return {"success": True, "message": "메시지가 전송되었습니다.", "chat": msg.model_dump()}

