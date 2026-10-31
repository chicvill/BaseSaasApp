"""
apps/rent3_lessor/api.py — 임대인 전용 API 라우터.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from apps._rent3_core import (
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
def get_dashboard(branch: str = "main", up_to_ym: Optional[str] = None, as_of_date: Optional[str] = None, year: Optional[int] = None):
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
    cur_ym_str = ref_dt.strftime("%Y-%m")
    cur_month_due_target = 0
    cur_month_paid_amount = 0
    overdue_rooms_summary: List[Dict[str, Any]] = []

    for r in rooms:
        ct = Contract.get(r.active_contract_id) if r.active_contract_id else None
        ledger = ct.ledger(up_to_ym=up_to_ym) if ct else None
        tenant = tenants_by_id.get(r.id_tenant) if r.id_tenant else None

        if ledger:
            total_unpaid_branch += int(ledger["total_unpaid"])
            # 미납 개월(횟수) 계산: UNPAID 또는 PARTIAL 상태의 청구월 목록
            unpaid_months = [
                m["ym"] for m in (ledger.get("months") or [])
                if m.get("status") in ("UNPAID", "PARTIAL") and m.get("unpaid", 0) > 0
            ]
            unpaid_count = len(unpaid_months)
            room_unpaid_amt = int(ledger["total_unpaid"])

            # 당월(현재월) 수납 목표 및 실제 수납액 집계
            for m in (ledger.get("months") or []):
                if m.get("ym") == cur_ym_str:
                    cur_month_due_target += int(m.get("due", 0))
                    cur_month_paid_amount += int(m.get("paid", 0))

            if room_unpaid_amt > 0:
                overdue_rooms_summary.append({
                    "room_no": r.room_no,
                    "tenant_name": (tenant.name if tenant else (ct.tenant_name if ct else "세입자")),
                    "tenant_phone": (tenant.phone if tenant else (ct.tenant_phone if ct else "")),
                    "unpaid_count": unpaid_count,
                    "unpaid_amount": room_unpaid_amt,
                    "unpaid_months": unpaid_months,
                    "monthly_rent": r.monthly_rent,
                    "maintenance_fee": r.maintenance_fee,
                    "pay_timing": r.pay_timing,
                    "payment_day": getattr(ct, "payment_day", 1) or 1,
                    "is_critical_2plus": unpaid_count >= 2, # 주임법 2기 연체 해지 요건
                })

        exp_win = ct.expiry_window_status() if ct else None
        exp_chk = ExpiryRenewalCheck.for_contract(ct.id) if ct else None
        bill_sched = ct.billing_notice_schedule(as_of=ref_dt) if ct else None
        bill_notices = [bn.model_dump() for bn in RentBillingNotice.for_contract(ct.id, limit=5)] if ct else []
        card = {
            **r.model_dump(),
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

    # 미납 횟수 및 금액 내림차순 정렬
    overdue_rooms_summary.sort(key=lambda x: (x["unpaid_count"], x["unpaid_amount"]), reverse=True)

    # 당월 수납 달성률 계산
    cur_rate_pct = round((cur_month_paid_amount / cur_month_due_target * 100.0), 1) if cur_month_due_target > 0 else 100.0
    current_month_kpi = {
        "year_month": cur_ym_str,
        "due_target": cur_month_due_target,
        "paid_amount": cur_month_paid_amount,
        "collection_rate_pct": cur_rate_pct,
        "overdue_room_count": len(overdue_rooms_summary),
        "total_rooms": len(rooms),
        "paid_room_count": max(0, len(rooms) - len(overdue_rooms_summary)),
        "overdue_list": overdue_rooms_summary,
    }

    payments = sorted(Pay.find(branch=b_id), key=lambda p: (p.paid_date, p.created_at), reverse=True)
    outlays = sorted(Outlay.find(branch=b_id), key=lambda o: o.expense_date, reverse=True)
    tickets = sorted(MaintenanceTicket.find(branch=b_id), key=lambda t: t.updated_at, reverse=True)
    checkouts = sorted(CheckOutSettlement.find(branch=b_id), key=lambda c: c.updated_at, reverse=True)
    chats = sorted(DirectChat.find(branch=b_id), key=lambda c: c.created_at, reverse=True)[:30]
    cosign_sessions = sorted(CoSignSession.find(branch=b_id), key=lambda s: s.updated_at, reverse=True)
    recent_billing_notices = sorted(
        RentBillingNotice.find(branch=b_id), key=lambda x: x.created_at, reverse=True
    )[:15]
    fin = TaxAndCertificateService.branch_financial_summary(b_id, year=year)

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
        "current_month_kpi": current_month_kpi,
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


@router.post("/rooms")
def create_or_update_room(req: RoomCreateReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    bldg = Building.first(branch=b_id)
    existing = Room.first(branch=b_id, room_no=req.room_no)
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
        room_no=req.room_no,
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
        f"(contracts.json · rooms.json · users.json 실시간 동기화)"
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
        "hint": "임차인 마이홈 상단 알림 배너 및 1:1 인앱 메시지(ops.json)에 실시간 전달되었습니다.",
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



