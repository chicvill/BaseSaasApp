"""
apps/rent_dist/rent3_lessor/routers/rooms.py — 대시보드 · 호실 등록/공개/다음조건/예약취소 · 등기부 · 출입교부/도어락 · 건물 수정.
"""
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.errors import DomainError, Forbidden
from apps.rent_dist._rent3_core import (
    Building,
    CheckOutSettlement,
    CoSignSession,
    Contract,
    DirectChat,
    EXPENSE_CATEGORIES,
    ExpiryRenewalCheck,
    Lessor,
    MaintenanceTicket,
    Outlay,
    Pay,
    RentBillingNotice,
    Room,
    TaxAndCertificateService,
    Tenant,
    connect,
    date_today,
    ensure_pool_seeded,
    listing_pdf,
    mailpay,
    normalize_branch_id,
    pool,
)
from apps.rent_dist._rent3_core.authz import claims_from
from apps.rent_dist._rent3_core.ops import TransferNotice
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import APP_SRC, _my_lessor, _parse_room_list, _pin_info, _reservation_summary

router = APIRouter()


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
    cleaning_fee: int = 0
    pay_timing: str = "후불"
    status_description: str = "신규 등록 풀옵션 호실"
    total_floors: int = 0
    direction: str = ""
    available_from: str = "즉시입주"
    is_published: bool = True
    pre_moveout_notice: Optional[bool] = None
    option_list: Optional[List[Any]] = None
    listing_spec: Optional[Dict[str, Any]] = None


class PublishToggleReq(BaseModel):
    is_published: bool


class DoorlockReq(BaseModel):
    new_pwd: str


class ReservationCancelReq(BaseModel):
    reason: str = ""


class NextTermsReq(BaseModel):
    deposit: Optional[int] = None
    monthly_rent: Optional[int] = None
    maintenance_fee: Optional[int] = None
    cleaning_fee: Optional[int] = None
    pay_timing: Optional[str] = None           # 후불 / 선불
    enforce_renewal_cap: bool = False           # 연장 계약 시작 창에서 저장 시 법정 5% 상한 검증
    clear: bool = False                         # 저장해 둔 다음 계약 조건 초안을 지운다


class RoomRegistryReadReq(BaseModel):
    attachment_data_url: str
    consent: bool = False


class RoomRegistryConfirmReq(BaseModel):
    checked_original: bool = False     # 인터넷등기소 원본과 직접 대조했음
    all_rooms: bool = False            # 같은 건물(지점)의 모든 호실에 같은 등기부를 적용


class ReleaseKeysReq(BaseModel):
    balance_checked: bool = False   # 임대인이 본인 계좌의 실제 입금 내역에서 잔금을 직접 확인함


class BuildingEditReq(BaseModel):
    branch: str = "main"
    building_name: str
    address: str = ""
    vat_rooms: Optional[Any] = None
    income_tax_rooms: Optional[Any] = None


@router.get("/dashboard")
def get_dashboard(request: Request, branch: str = "main", up_to_ym: Optional[str] = None, as_of_date: Optional[str] = None):
    ExpiryRenewalCheck.activate_due()           # 공개일이 된 사전 매물을 올린다(스케줄러가 없어 조회 때 확인)
    ensure_pool_seeded()
    from apps.rent_dist._rent3_core import feebilling as _fb
    _fb.ensure_notices(branch=normalize_branch_id(branch))      # 이번 달 가맹비 청구·통보가 아직 없으면 만든다(여러 번 불러도 한 번만)
    b_id = normalize_branch_id(branch)
    ref_dt = RentBillingNotice._parse_ref_date(as_of_date)
    buildings = Building.all()
    bldg = Building.first(branch=b_id) or (buildings[0] if buildings else None)
    lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)

    rooms = sorted(Room.find(branch=b_id), key=lambda r: r.room_no)
    tenants_by_id = {t.id: t for t in Tenant.find(branch=b_id)}

    room_cards: List[Dict[str, Any]] = []
    expiry_notice_rooms: List[Dict[str, Any]] = []
    target_ym = up_to_ym or date_today().isoformat()[:7]
    total_unpaid_branch = 0
    total_overdue_branch = 0     # 납입일이 지난 미납(연체)
    monthly_target = 0
    monthly_collected = 0
    monthly_unpaid = 0
    monthly_due_soon = 0
    monthly_overdue = 0
    for r in rooms:
        ct = Contract.get(r.active_contract_id) if r.active_contract_id else None
        ledger = ct.ledger(up_to_ym=up_to_ym) if ct else None
        if ledger:
            total_unpaid_branch += int(ledger["total_unpaid"])
            total_overdue_branch += int(ledger.get("overdue_unpaid", 0))
            cur_row = next((m for m in ledger.get("months", []) if m.get("ym") == target_ym), None)
            if cur_row:
                cur_unpaid = int(cur_row.get("unpaid", 0))
                monthly_target += int(cur_row.get("due", 0))
                monthly_collected += int(cur_row.get("paid", 0))
                monthly_unpaid += cur_unpaid
                if cur_row.get("phase") == "DUE_SOON":
                    monthly_due_soon += cur_unpaid
                else:
                    monthly_overdue += cur_unpaid
        tenant = tenants_by_id.get(r.id_tenant) if r.id_tenant else None
        exp_win = ct.expiry_window_status() if ct else None
        exp_chk = ExpiryRenewalCheck.for_contract(ct.id) if ct else None
        bill_sched = ct.billing_notice_schedule(as_of=ref_dt) if ct else None
        bill_notices = [bn.model_dump() for bn in RentBillingNotice.for_contract(ct.id, limit=5)] if ct else []
        pays = []
        if ct:
            pays = sorted(
                (x for x in Pay.find(id_contract=ct.id) if x.payment_kind == "MONTHLY_RENT"),
                key=lambda x: (x.paid_date, x.created_at),
                reverse=True,
            )[:60]
        card = {
            **r.model_dump(),
            "pays": [
                {
                    "id": x.id,
                    "paid_date": x.paid_date,
                    "billing_month": x.billing_month,
                    "rent": x.rent_amount,
                    "fee": x.maintenance_amount,
                    "total": x.total_amount,
                    "grp": x.deposit_group_id,
                    "grp_n": len([y for y in pays if x.deposit_group_id and y.deposit_group_id == x.deposit_group_id]),
                }
                for x in pays
            ],
            "is_occupied": r.is_occupied,
            "tenant": tenant.safe_dump() if tenant else None,
            "contract": ct.model_dump() if ct else None,
            "ledger": ledger,
            "expiry_window": exp_win,
            "expiry_check": exp_chk.model_dump() if exp_chk else None,
            "reservation": _reservation_summary(r),
            "checkout_liability": ct.checkout_liability(r) if ct else None,       # 만기 전 퇴실 시 만기일(또는 새 임차인 입주 전)까지 이전 임차인이 부담할 월세
            "billing_schedule": bill_sched,
            "billing_notices": bill_notices,
            "terms_pdf_url": listing_pdf.listing_pdf_url(r.id),
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
    outlays = sorted((o for o in Outlay.find(branch=b_id) if not o.reference_only), key=lambda o: o.expense_date, reverse=True)
    tickets = sorted(MaintenanceTicket.find(branch=b_id), key=lambda t: t.updated_at, reverse=True)
    CheckOutSettlement.auto_confirm_due(branch=b_id)      # 송금 표시 뒤 7일 동안 이의가 없으면 자동 확정
    checkouts = sorted(CheckOutSettlement.find(branch=b_id), key=lambda c: c.updated_at, reverse=True)
    chats = sorted((c for c in DirectChat.find(branch=b_id) if not c.session_id), key=lambda c: c.created_at, reverse=True)[:30]   # 계약방 대화는 계약 화면에서
    cosign_sessions = sorted(CoSignSession.find(branch=b_id), key=lambda s: s.updated_at, reverse=True)
    recent_billing_notices = sorted(
        RentBillingNotice.find(branch=b_id), key=lambda x: x.created_at, reverse=True
    )[:15]
    fin = TaxAndCertificateService.branch_financial_summary(b_id)

    monthly_rate = round(monthly_collected * 100 / monthly_target) if monthly_target > 0 else 0

    return {
        "success": True,
        "today": date_today().isoformat(),
        "as_of_date": ref_dt.isoformat(),
        "pool_revision": pool().overview()["total_revision"],
        "branch": b_id,
        "branches": [{"branch": b.branch, "building_name": b.building_name} for b in buildings],
        "building": bldg.model_dump() if bldg else None,
        "lessor": lessor.safe_dump() if lessor else None,
        "features": {"mail": mailpay.feature_enabled()},
        "target_ym": target_ym,
        "monthly_target": monthly_target,
        "monthly_collected": monthly_collected,
        "monthly_unpaid": monthly_unpaid,
        "monthly_due_soon": monthly_due_soon,
        "monthly_overdue": monthly_overdue,
        "monthly_collection_rate": monthly_rate,
        "total_unpaid_branch": total_unpaid_branch,
        "total_overdue_branch": total_overdue_branch,
        "total_due_soon_branch": total_unpaid_branch - total_overdue_branch,
        "rooms": room_cards,
        "transfer_notices": [n.model_dump() for n in sorted(TransferNotice.find(branch=b_id), key=lambda x: x.created_at) if n.status == "PENDING"],
        "pin": _pin_info(request),
        "connect_requests": connect.pending_for_branch(b_id),
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
    # 승인된 가맹점만 매물을 공개할 수 있다. 심사 중에는 호실을 미리 준비(비공개)만 가능.
    lsr = Lessor.for_branch(b_id)
    if not Lessor.franchise_open(b_id):
        req.is_published = False
    if not existing and lsr and Room.count(branch=b_id) >= lsr.room_limit:
        raise DomainError(
            f"가맹점당 등록 가능 호실 수({lsr.room_limit}실)를 넘었습니다.",
            hint="더 등록하려면 관리자에게 한도 상향을 요청해 주세요.",
        )
    clean_fee = max(0, int(req.cleaning_fee or 0))
    spec_norm: Optional[Dict[str, Any]] = None
    opts_norm: Optional[List[Dict[str, Any]]] = None
    if req.listing_spec is not None:
        base_opts = existing.option_list if existing else None
        spec_norm = listing_pdf.normalize_listing_spec(req.listing_spec, base_opts)
        opts_norm = listing_pdf.options_to_room_list(spec_norm["options"])
    elif req.option_list is not None:
        opts_norm = listing_pdf.options_to_room_list(listing_pdf._clean_str_list(req.option_list))

    if existing:
        if existing.is_occupied:
            if req.is_published:
                if not (req.pre_moveout_notice is True or existing.pre_moveout_notice):
                    raise DomainError(
                        "입주 중인 호실은 전출 예정 확인 후 사전 매물로 등록할 수 있습니다.",
                        hint="세입자의 전출 계획이 확정되었는지 확인 체크 후 입주가능일을 지정해 주세요.",
                    )
                open_d = existing.prelist_open_date()
                if open_d and date_today().isoformat() < open_d and not (existing.pre_moveout_notice and existing.is_published):
                    raise DomainError(
                        f"입주 중인 호실은 퇴실 기준일(만기) 1개월 전인 {open_d}부터 사전 매물로 올릴 수 있습니다.",
                        hint="전출 의사를 미리 받아 두어도 공개는 그날부터 시작되며, 임차인이 전출 예정을 회신해 두었다면 그날 자동으로 공개됩니다. 너무 일찍 공개하면 지금 사는 임차인이 불편합니다.",
                    )
                avail_clean = (req.available_from or "").strip()
                if not avail_clean or avail_clean == "즉시입주":
                    raise DomainError(
                        "입주 중인 호실은 '즉시입주'로 등록할 수 없습니다.",
                        hint="세입자 전출 예정일 이후의 입주가능일을 지정해 주세요.",
                    )
                existing.pre_moveout_notice = True
            elif req.pre_moveout_notice is False:
                existing.pre_moveout_notice = False
        if existing.is_occupied:
            # 입주 중: 현재 계약·방 값은 그대로 두고 '다음 세입자용 조건'만 임시로 저장한다(퇴실 확정 때 반영, 새 계약 체결 때 삭제)
            if req.is_published:
                prev_nl = dict(existing.next_listing or {})
                nl_doc: Dict[str, Any] = {
                    "name": req.name or existing.name,
                    "room_category": req.room_category,
                    "floor": req.floor,
                    "area_sqm": req.area_sqm,
                    "total_floors": req.total_floors,
                    "direction": req.direction,
                    "deposit": req.deposit,
                    "monthly_rent": req.monthly_rent,
                    "maintenance_fee": req.maintenance_fee,
                    "cleaning_fee": clean_fee,
                    "pay_timing": req.pay_timing,
                    "vat_applicable": req.vat_applicable,
                    "available_from": req.available_from,
                    "status_description": req.status_description,
                }
                if spec_norm is not None:
                    nl_doc["listing_spec"] = spec_norm
                    nl_doc["option_list"] = opts_norm
                else:
                    if prev_nl.get("listing_spec"):
                        nl_doc["listing_spec"] = prev_nl["listing_spec"]
                    if opts_norm is not None:
                        nl_doc["option_list"] = opts_norm
                    elif prev_nl.get("option_list"):
                        nl_doc["option_list"] = prev_nl["option_list"]
                existing.next_listing = nl_doc
                if existing.reservation() is None:
                    nt_sync = dict(existing.next_terms or {})
                    nt_sync.update({
                        "deposit": req.deposit,
                        "monthly_rent": req.monthly_rent,
                        "maintenance_fee": req.maintenance_fee,
                        "cleaning_fee": clean_fee,
                        "pay_timing": req.pay_timing,
                    })
                    if spec_norm is not None:
                        nt_sync["lease_term_months"] = spec_norm["lease_term_months"]
                    existing.next_terms = nt_sync
            else:
                existing.next_listing = {}
            existing.is_published = req.is_published
            existing.save(action="room_prelisting_saved", app_source=APP_SRC)
            if req.is_published:
                listing_pdf.save_listing_pdf(existing, building=bldg, lessor=lsr)
            msg = (
                f"{req.room_no}호가 전출 예정 사전 매물로 등록되었습니다. (현재 계약·방 값은 그대로이며, 퇴실이 확정되면 이 조건이 적용됩니다)"
                if existing.pre_moveout_notice
                else f"{req.room_no}호 정보가 저장되었습니다."
            )
            return {
                "success": True,
                "message": msg,
                "room": existing.model_dump(),
                "terms_pdf_url": listing_pdf.listing_pdf_url(existing.id),
            }
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
        existing.cleaning_fee = clean_fee
        existing.pay_timing = req.pay_timing
        existing.status_description = req.status_description
        if spec_norm is not None:
            existing.listing_spec = spec_norm
            existing.option_list = opts_norm or []
        elif opts_norm is not None:
            existing.option_list = opts_norm
        existing.is_published = req.is_published
        existing.save(action="room_updated", app_source=APP_SRC)
        listing_pdf.save_listing_pdf(existing, building=bldg, lessor=lsr)
        msg = (
            f"{req.room_no}호가 전출 예정 사전 매물로 등록되었습니다."
            if existing.is_occupied and existing.pre_moveout_notice
            else f"{req.room_no}호 정보가 저장되었습니다."
        )
        return {
            "success": True,
            "message": msg,
            "room": existing.model_dump(),
            "terms_pdf_url": listing_pdf.listing_pdf_url(existing.id),
        }

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
        cleaning_fee=clean_fee,
        pay_timing=req.pay_timing,
        status_description=req.status_description,
        option_list=opts_norm or [],
        listing_spec=spec_norm or {},
        is_published=req.is_published,
    ).save(action="room_created", app_source=APP_SRC)
    listing_pdf.save_listing_pdf(room, building=bldg, lessor=lsr)
    return {
        "success": True,
        "message": f"신규 {room.room_no}호가 등록되었습니다.",
        "room": room.model_dump(),
        "terms_pdf_url": listing_pdf.listing_pdf_url(room.id),
    }


@router.post("/rooms/{room_id}/next-terms")
def save_room_next_terms(room_id: str, req: NextTermsReq):
    """입주 중인 호실의 '다음 계약 조건'을 저장한다(임대인). 지금 계약 조건은 만기(퇴실)까지 그대로이고,
    신규 임차인 입주 또는 연장 적용 시 현재 계약 조건을 대체한다. 공개되거나 임차인에게 보이지 않는다."""
    room = Room.require(room_id)
    if not room.is_occupied:
        raise DomainError("입주 중인 호실의 다음 계약 조건만 임시 저장할 수 있습니다.", hint="빈 호실은 임대매물 등록 화면에서 조건을 바로 고치세요.")
    if room.reservation() is not None:
        raise DomainError("이미 다음 계약(연장 또는 예약)이 체결되어 조건을 바꿀 수 없습니다.", hint="체결된 예약 계약을 취소한 뒤 다시 저장해 주세요.")
    if req.clear:
        room.next_terms = {}
        room.save(action="room_next_terms_cleared", app_source=APP_SRC)
        return {"success": True, "level": "success", "message": f"{room.room_no}호 다음 계약 조건 초안을 지웠습니다.", "room": room.model_dump()}
    draft = dict(room.next_terms or {})
    for k in ("deposit", "monthly_rent", "maintenance_fee", "cleaning_fee"):
        v = getattr(req, k)
        if v is None:
            continue
        if int(v) < 0:
            raise DomainError("금액은 0 이상이어야 합니다.")
        draft[k] = int(v)
    if req.pay_timing is not None:
        if req.pay_timing not in ("후불", "선불"):
            raise DomainError("월세 납부 시기는 후불 또는 선불이어야 합니다.")
        draft["pay_timing"] = req.pay_timing
    if not draft:
        raise DomainError("저장할 조건을 입력해 주세요.")
    if req.enforce_renewal_cap and room.active_contract_id:
        ct = Contract.get(room.active_contract_id)
        if ct is not None:
            ct.validate_renewal_cap(
                new_deposit=int(draft.get("deposit", ct.deposit)),
                new_rent=int(draft.get("monthly_rent", ct.monthly_rent)),
            )
    room.next_terms = draft
    if room.pre_moveout_notice and room.next_listing:
        nl = dict(room.next_listing)
        for k in ("deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "pay_timing"):
            if k in draft:
                nl[k] = draft[k]
        room.next_listing = nl
    room.save(action="room_next_terms_saved", app_source=APP_SRC)
    return {
        "success": True,
        "level": "success",
        "message": f"{room.room_no}호 다음 계약 조건을 저장했습니다. 현재 계약은 전출·만기까지 유지되며 신규 입주·연장 시 자동 대체됩니다.",
        "hint": "신규 입주자 계약·연장 재계약·사전 매물 공개에 이 조건이 자동 적용됩니다.",
        "room": room.model_dump(),
    }


@router.post("/rooms/{room_id}/reservation/cancel")
def cancel_room_reservation(room_id: str, req: ReservationCancelReq):
    """퇴실 전에 미리 체결한 예약 계약을 취소한다(임대인). 호실은 전출 예정 사전 매물로 다시 공개된다. 받은 계약금 반환은 당사자가 직접 처리한다."""
    room = Room.require(room_id)
    ct = room.cancel_reservation(reason=req.reason, app_source=APP_SRC)
    if ct.renews_contract_id:
        return {
            "success": True,
            "level": "success",
            "message": f"{room.room_no}호 연장 재계약 예약({ct.tenant_name}님)을 취소했습니다. 만기까지는 현재 계약 조건이 그대로 적용됩니다.",
            "hint": "연장을 다시 진행하려면 만기통보에서 연장 예고를 새로 보내 주세요.",
            "room": room.model_dump(),
        }
    return {
        "success": True,
        "level": "success",
        "message": f"{room.room_no}호 예약 계약({ct.tenant_name}님)을 취소했습니다. 호실은 전출 예정 사전 매물로 다시 공개됩니다.",
        "hint": f"이미 받은 계약금({ct.contract_deposit:,}원)이 있으면 임차인에게 직접 반환해 주세요.",
        "room": room.model_dump(),
    }


@router.post("/rooms/{room_id}/publish")
def toggle_room_publish(room_id: str, req: PublishToggleReq):
    room = Room.require(room_id)
    if req.is_published and not Lessor.franchise_open(room.branch):
        raise Forbidden(
            "가맹 승인 후에 매물을 공개할 수 있습니다.",
            hint="관리자의 가맹 심사가 끝나면 공개할 수 있어요. 심사 상태는 임대인 화면 상단에서 확인하세요.",
        )
    if req.is_published and room.is_occupied and not room.pre_moveout_notice:
        raise DomainError(
            "입주 중인 호실은 전출 예정 확인과 입주가능일을 지정해 임대매물 등록 화면에서 사전 공개해 주세요.",
        )
    room.is_published = req.is_published
    if not req.is_published and room.is_occupied:
        room.pre_moveout_notice = False
        room.next_listing = {}
        if room.active_contract_id:
            ExpiryRenewalCheck.cancel_pending_prelist(room.active_contract_id, app_source=APP_SRC)
    room.save(action="room_publish_toggled", app_source=APP_SRC)
    state_label = "직거래 매물 공개" if room.is_published else "매물 비공개"
    return {
        "success": True,
        "message": f"{room.room_no}호가 '{state_label}' 상태로 전환되었습니다 (임차인 앱 즉시 반영).",
        "room": room.model_dump(),
    }


@router.get("/rooms/{room_id}/promo")
def get_room_promo(room_id: str, request: Request):
    """호실의 5대 채널(당근, 피터팬, 에타, 블로그, 카톡) 멀티채널 원클릭 홍보 텍스트 생성."""
    room = Room.require(room_id)
    lsr = _my_lessor(room.branch)
    bldg = Building.get(room.id_building) if room.id_building else Building.first(branch=room.branch)

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    base_url = f"{proto}://{host}" if host else ""

    from apps.rent_dist._rent3_core import promo
    data = promo.generate_promo(room, building=bldg, lessor=lsr, base_url=base_url)
    return {"success": True, "promo": data, "terms_pdf_url": listing_pdf.listing_pdf_url(room.id)}


@router.get("/rooms/{room_id}/terms.pdf")
def get_lessor_room_terms_pdf(room_id: str, download: int = 0):
    """임대인이 본인 호실의 표준 임대조건 안내서 PDF 를 미리보기/다운로드한다(비공개 호실도 허용)."""
    ensure_pool_seeded()
    room = Room.require(room_id)
    bldg = Building.get(room.id_building) if room.id_building else Building.first(branch=room.branch)
    lsr = _my_lessor(room.branch)
    pdf_bytes = listing_pdf.build_listing_terms_pdf(room, building=bldg, lessor=lsr)
    disp = "attachment" if download else "inline"
    fname_ascii = f"rent3_terms_{room.room_no}.pdf"
    fname_utf8 = quote(f"{(bldg.building_name if bldg else room.branch)}_{room.room_no}호_표준임대조건서.pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disp}; filename="{fname_ascii}"; filename*=UTF-8\'\'{fname_utf8}',
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.post("/rooms/{room_id}/registry/read")
def room_registry_read(room_id: str, req: RoomRegistryReadReq):
    """등기사항증명서(PDF·사진)를 올리면 보관하고 AI 로 읽어 '확인 대기'로 저장한다(확정은 /registry/confirm)."""
    from apps.rent_dist._rent3_core import roomregistry as rr
    room = Room.require(room_id)
    info = rr.read(room, req.attachment_data_url, req.consent)
    return {"success": True, "message": "등기부를 읽었습니다. 아래 내용을 원본과 대조한 뒤 [확인 완료]를 눌러 주세요.", "registry": info}


@router.post("/rooms/{room_id}/registry/confirm")
def room_registry_confirm(room_id: str, req: RoomRegistryConfirmReq):
    from apps.rent_dist._rent3_core import roomregistry as rr
    room = Room.require(room_id)
    if not req.checked_original:
        raise DomainError("원본과 대조했는지 확인해 주세요.", hint="인터넷등기소 원본과 AI 가 읽은 값을 비교한 뒤 체크해 주세요.")
    res = rr.confirm(room, all_rooms=req.all_rooms)
    return {
        "success": True,
        "message": f"{room.room_no}호 등기부를 확인 완료로 기록했습니다" + (f" (같은 건물 {res['shared']}개 호실에도 적용)." if res["shared"] else "."),
        **res,
    }


@router.post("/rooms/{room_id}/release-keys")
def release_keys(room_id: str, req: ReleaseKeysReq, request: Request):
    """입주일 잔금 입금을 확인하고 도어락·공동현관 정보를 임차인에게 교부한다 (임차인 화면에서 즉시 열림)."""
    room = Room.require(room_id)
    ct = Contract.get(room.active_contract_id) if room.active_contract_id else None
    if ct is None or ct.keys_released is not False:
        raise DomainError("교부 대기 중인 계약이 없습니다.", hint="이미 교부했거나 잠금 대상이 아닌 계약입니다.")
    br = ct.balance_registry or {}
    if br.get("changed"):
        raise DomainError(
            "임차인이 잔금일 등기부에서 변동을 신고했습니다. 교부할 수 없습니다.",
            hint="등기부를 함께 확인해 변동이 해소되거나 합의된 뒤 임차인이 다시 '변동 없음'으로 확인해야 합니다. 필요하면 본사에 문의하세요.",
        )
    if ct.mortgage_cleanup_required and not br.get("mortgage_cleared"):
        raise DomainError(
            "잔금 시 근저당 말소 특약이 있는 계약입니다. 근저당 말소가 확인되지 않았습니다.",
            hint="선순위 근저당을 말소하고, 임차인이 말소된 등기부를 확인한 뒤에 교부할 수 있습니다.",
        )
    if ct.insurance_proof_required and not (ct.insurance_proof or {}).get("policy_no"):
        raise DomainError(
            "임차인이 '보증보험 가입 예정'으로 계약했고, 가입 증빙이 아직 제출되지 않았습니다.",
            hint="임차인이 보증기관·증권번호를 제출해야 교부할 수 있습니다. 가입이 거절되면 계약서의 특약에 따라 계약은 무효이고 계약금은 반환됩니다.",
        )
    if not br.get("checked_at"):
        raise DomainError(
            "임차인의 잔금일 등기부 확인이 아직 없습니다.",
            hint="잔금일에 임차인이 인터넷등기소에서 직접 등기부를 열람하고 변동 없음을 확인해야 합니다.",
        )
    if not req.balance_checked:
        raise DomainError(
            "본인 계좌의 실제 입금 내역에서 잔금을 직접 확인해야 교부할 수 있습니다.",
            hint="임차인이 보여 주는 이체 완료 화면은 위조될 수 있습니다.",
        )
    who = (claims_from(request) or {}).get("u", "")
    ct.keys_released, ct.keys_released_at, ct.keys_released_by = True, now_iso(), who
    ct.save(action="keys_released", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{room.room_no}호 도어락·공동현관 정보를 임차인에게 교부했습니다 (임차인 화면에 즉시 열립니다).",
        "contract": ct.model_dump(),
    }


@router.post("/rooms/{room_id}/doorlock")
def change_doorlock(room_id: str, req: DoorlockReq):
    room = Room.require(room_id)
    room.update_doorlock(req.new_pwd, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{room.room_no}호 도어락 비밀번호가 변경되어 입주자 정보에도 반영되었습니다.",
        "room": room.model_dump(),
    }


@router.post("/building/edit")
def edit_building(req: BuildingEditReq):
    """건물 이름·주소 및 과세/종소세 대상 호실 리스트 수정 (임대인 더보기 ▸ 건물 정보 수정)."""
    ensure_pool_seeded()
    name = req.building_name.strip()
    if not name:
        raise DomainError("건물 이름을 입력해 주세요.")
    b = Building.first(branch=normalize_branch_id(req.branch))
    if not b:
        raise DomainError("건물 정보를 찾을 수 없습니다.")
    b.building_name, b.address = name, req.address.strip()
    if req.vat_rooms is not None:
        b.vat_rooms = _parse_room_list(req.vat_rooms)
    if req.income_tax_rooms is not None:
        b.income_tax_rooms = _parse_room_list(req.income_tax_rooms)
    b.save(action="building_edited", app_source=APP_SRC)
    return {"success": True, "message": f"건물 정보를 저장했습니다 ({b.building_name}).", "building": b.model_dump()}
