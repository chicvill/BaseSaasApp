"""
apps/rent3_tenant/api.py — 임차인 및 예비 세입자 전용 API 라우터.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from apps.rent_dist._rent3_core.pool import now_iso
from apps.rent_dist._rent3_core.ops import TransferNotice

from core.errors import DomainError, NotFound
from apps.rent_dist._rent3_core.authz import any_user, api_headers, claims_from, is_admin, public_room, staff_only, tenant_scope
from apps.rent_dist._rent3_core import room_messages, contract_chat, chatsessions as chat_sessions, listing_pdf, roomregistry as room_registry
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


class TransferNoticeReq(BaseModel):
    amount: int
    memo: str = ""


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
    account_holder: str = ""        # 예금주 — 계약자 본인과 같아야 한다


class CheckoutRequestReq(BaseModel):
    desired_date: str = ""
    memo: str = ""


class CheckoutDisputeReq(BaseModel):
    note: str


@router.get("/status")
def get_status():
    ensure_pool_seeded()
    ExpiryRenewalCheck.activate_due()           # 공개일이 된 사전 매물을 올린다
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
    ExpiryRenewalCheck.activate_due()           # 공개일(퇴실 기준일 1개월 전)이 된 사전 매물을 올린다
    rooms = Room.find(branch=normalize_branch_id(branch)) if branch else Room.all()
    bldgs = {b.id: b for b in Building.all()}
    lessors = {l.id: l for l in Lessor.all()}
    sessions_by_room = {
        s.id_room: s for s in CoSignSession.find(status="IN_PROGRESS")
    }

    default_imgs = (
        "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
        "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80",
        "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
        "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800&q=80",
    )
    cards = []
    for idx, r in enumerate(sorted(rooms, key=lambda x: (x.branch, x.room_no))):
        if (r.is_occupied and not r.pre_moveout_notice) or not r.is_published:
            continue
        if not Lessor.franchise_open(r.branch):
            continue
        r = r.as_listing()          # 입주 중 사전 매물은 '다음 세입자용 조건'으로 보여 준다
        b = bldgs.get(r.id_building)
        lessor = lessors.get(b.id_lessor) if (b and b.id_lessor) else None
        sess = sessions_by_room.get(r.id)
        img_url = r.images[0] if r.images else default_imgs[idx % len(default_imgs)]
        spec = listing_pdf.normalize_listing_spec(r.listing_spec, r.option_list)
        cards.append({
            **public_room(r.model_dump()),  # 도어락·등기·입주자 정보는 공개 목록에 싣지 않는다
            "is_pre_moveout_listing": r.is_occupied and r.pre_moveout_notice,
            "clean_title": room_registry.public_clean(r),
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "lessor_name": lessor.name if lessor else "임대인",
            "lessor_phone": lessor.phone if lessor else "",
            "options": spec["options"],
            "listing_spec": spec,
            "terms_pdf_url": listing_pdf.listing_pdf_url(r.id),
            "image": img_url,
            "images": list(r.images) if r.images else [img_url],
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
        "message": f"{req.room_no}호 임대인에게 1:1 방문투어/문의 채팅이 실시간 전달되었습니다 .",
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
                **t.safe_dump(),
                "building_name": bldgs[t.id_building].building_name if t.id_building in bldgs else t.branch,
            }
            for t in tenants
        ],
    }


@router.get("/myhome/{tenant_id}", dependencies=[Depends(tenant_scope)])
def get_myhome(tenant_id: str):
    ensure_pool_seeded()
    ExpiryRenewalCheck.activate_due()           # 시작일이 된 연장 재계약의 새 조건을 먼저 적용한다(청구는 현재 적용 중인 계약 기준)
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
    CheckOutSettlement.auto_confirm_due(tenant_id=t.id)      # 송금 표시 뒤 7일 동안 이의가 없으면 자동 확정
    checkouts = [v for v in (c.tenant_view() for c in CheckOutSettlement.find(id_tenant=t.id) if c.status != "COMPLETED") if v]     # 임대인이 작성 중인 초안의 금액은 임차인에게 보이지 않는다
    chats = sorted(
        [c for c in DirectChat.find(branch=t.branch, room_no=t.room_no) if not c.session_id],   # 계약방 대화는 계약 화면에서만
        key=lambda x: x.created_at,
        reverse=True,
    )[:20]
    payments = sorted(Pay.find(id_tenant=t.id), key=lambda p: (p.paid_date, p.created_at), reverse=True)

    tenant_d = t.safe_dump()
    bldg_d = bldg.model_dump() if bldg else None
    room_d = room.model_dump() if room else None
    if room_d is not None:
        room_d.pop("next_terms", None)          # 임대인이 임시 저장해 둔 다음 계약 조건 초안은 임차인에게 보이지 않는다
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
        "keys": {"registry_check": (ct.balance_registry if ct else {}), "locked": keys_locked,
                 "insurance_proof_required": bool(ct and ct.insurance_proof_required), "insurance_proof": (ct.insurance_proof if ct else {}), "message": LOCKED_TEXT if keys_locked else "",
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
        "transfer_notice": (lambda ns: ns[-1].model_dump() if ns else None)(sorted(TransferNotice.find(tenant_id=t.id), key=lambda x: x.created_at)),
        "tickets": [tk.model_dump() for tk in tickets],
        "pending_checkout": checkouts[0] if checkouts else None,
        "chats": [c.model_dump() for c in chats],
        "messages_unread": (room_messages.unread_count(t.branch, t.room_no, "tenant", t, ct.created_at if ct else "") if t.room_no else 0),   # 임대인 채팅 안 읽은 수(읽음 처리 없음)
    }


class BalanceRegistryReq(BaseModel):
    changed: bool                 # 직접 열람해 보니 계약일 이후 변동이 있다
    issue_number: str = ""        # 이번에 열람한 등기부의 발급확인번호
    mortgage_cleared: bool = False  # (말소 특약 계약) 열람한 등기부에서 선순위 근저당이 모두 말소된 것을 확인했다
    checked: bool = False         # '변동 없음'일 때: 원본과 대조해 확인했음(체크 없으면 진행 불가)


class BalanceRegistryReadReq(BaseModel):
    attachment_data_url: str
    consent: bool = False


class TenantMsgReq(BaseModel):
    body: str


def _tenant_by_id(tenant_id: str) -> Tenant:
    t = Tenant.get(tenant_id) or Tenant.first(phone=tenant_id)
    if not t:
        raise NotFound("임차인을 찾을 수 없습니다.")
    return t


def _tenant_since(t: Tenant):
    room = Room.first(branch=t.branch, room_no=t.room_no) if t.room_no else None
    ct = room_messages.active_contract(room)
    return room, ct, (ct.created_at if ct else "")


@router.get("/myhome/{tenant_id}/messages", dependencies=[Depends(tenant_scope)])
def tenant_messages(tenant_id: str, request: Request, mark_read: bool = True):
    """임대인과의 채팅(내 호실). 계약 협상 기록은 접어서 따로 준다. 관리인 지원 채팅·계약방 대화는 섞지 않는다."""
    ensure_pool_seeded()
    contract_chat.purge_expired()
    t = _tenant_by_id(tenant_id)
    room, ct, since = _tenant_since(t)
    msgs = room_messages.room_messages(t.branch, t.room_no, t, since) if t.room_no else []
    unread = sum(1 for m in msgs if room_messages._unread(m, "tenant"))
    c = claims_from(request)
    if mark_read and not (c and is_admin(c)):
        room_messages.mark_read(msgs, "tenant")
    lessor = Lessor.first(branch=t.branch)
    if not (c and is_admin(c)):                                              # 채팅 세션(제목·시작 시각만 기록)
        bldg = Building.first(branch=t.branch)
        chat_sessions.touch(f"tenant:{t.id}", "room", t.room_no, f"{(bldg.building_name if bldg else '')} 임대인".strip(), t.branch)
    cos = sorted((x for x in CheckOutSettlement.find(id_tenant=t.id) if x.status != "COMPLETED"), key=lambda x: x.updated_at, reverse=True)
    checkout = next((card for card in (x.chat_card("tenant") for x in cos) if card), None)          # 채팅창 위 정산 카드(게시 전 초안은 없는 것과 같다)
    return {"success": True, "room_no": t.room_no, "unread": unread, "messages": [room_messages.public(m) for m in msgs],
            "tenant_name": t.name, "checkout": checkout,
            "lessor_name": (lessor.name if lessor else "") or "임대인", "lessor_phone": ((lessor.phone if lessor else "") or "").strip(),    # 임대인에게 전화 단추용
            "negotiation": room_messages.negotiation_history(ct)}


@router.post("/myhome/{tenant_id}/messages", dependencies=[Depends(tenant_scope)])
def tenant_message_send(tenant_id: str, req: TenantMsgReq):
    ensure_pool_seeded()
    t = _tenant_by_id(tenant_id)
    if not t.room_no:
        raise DomainError("입주한 호실이 없어 채팅을 보낼 수 없습니다.", hint="빈 집 찾기의 1:1 방문문의를 이용해 주세요.")
    m = room_messages.post(t.branch, t.room_no, "tenant", t.name, req.body, tenant_id=t.id)
    return {"success": True, "message": "임대인에게 보냈습니다.", "item": room_messages.public(m)}


def _balance_contract(tenant_id: str):
    t = Tenant.get(tenant_id) or Tenant.first(phone=tenant_id)
    if not t:
        raise NotFound(f"임차인('{tenant_id}') 정보를 찾을 수 없습니다.")
    room = Room.get(t.current_room_id) if t.current_room_id else None
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
    if ct is None or ct.keys_released is not False:
        raise DomainError("잔금일 등기부 확인 대상 계약이 아닙니다.", hint="출입 정보 교부 전의 새 계약에서만 사용합니다.")
    return ct


def _invite_base(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return f"{proto}://{host}" if host else ""


@router.get("/myhome/{tenant_id}/notifications", dependencies=[Depends(tenant_scope)])
def tenant_notifications(tenant_id: str):
    """내게 온 알림 일괄 요약(건수·종류·이동 주소만, 본문·계좌 없음). 앱을 열지 않은 동안 생긴 일도 원본 상태에서 계산해 보여 준다."""
    from apps.rent_dist._rent3_core import notifications as nf
    t = _tenant_by_id(tenant_id)
    return {"success": True, **nf.summary("tenant", t, t.id)}


class NoticeSeenReq(BaseModel):
    kind: str


@router.post("/myhome/{tenant_id}/notifications/seen", dependencies=[Depends(tenant_scope)])
def tenant_notice_seen(tenant_id: str, req: NoticeSeenReq):
    from apps.rent_dist._rent3_core import notifications as nf
    t = _tenant_by_id(tenant_id)
    nf.mark_seen(t, req.kind)
    return {"success": True, **nf.summary("tenant", t, t.id, use_cache=False)}


@router.get("/myhome/{tenant_id}/lessor-invite", dependencies=[Depends(tenant_scope)])
def lessor_invite_info(tenant_id: str, request: Request, label: str = ""):
    """집주인에게 보낼 안심계약 제안 문안·링크·오늘 남은 횟수·이 코드로 가입한 집주인 수(횟수는 올리지 않는다)."""
    from apps.rent_dist._rent3_core import lessorinvite as li
    t = _tenant_by_id(tenant_id)
    return {"success": True, **li.info(t, label, _invite_base(request))}


@router.post("/myhome/{tenant_id}/lessor-invite/sent", dependencies=[Depends(tenant_scope)])
def lessor_invite_sent(tenant_id: str):
    """임차인이 [문자로 보내기]·[복사]를 눌렀음을 기록(하루 한도 적용). 집주인의 번호·이름은 받지 않는다."""
    from apps.rent_dist._rent3_core import lessorinvite as li
    t = _tenant_by_id(tenant_id)
    return {"success": True, **li.record_sent(t)}


@router.post("/myhome/{tenant_id}/balance-registry-read", dependencies=[Depends(tenant_scope)])
def balance_registry_read(tenant_id: str, req: BalanceRegistryReadReq):
    """잔금 직전에 임차인이 인터넷등기소에서 열람한 등기부를 올리면 AI 가 읽고, 계약 때 값과 코드로 비교해 달라진 점을 알려 준다."""
    from apps.rent_dist._rent3_core import balancecheck as bc
    ct = _balance_contract(tenant_id)
    res = bc.read(ct, req.attachment_data_url, req.consent)
    if res["status"] == "unavailable":
        return {"success": False, "available": False, "message": res["message"] + " 인터넷등기소에서 직접 확인한 뒤 '확인했음'에 체크해 기록하세요.", "read": res["read"]}
    rd = res["read"]
    warn = bool(rd["differences"]) or not rd["recent"]
    msg = ("계약 때와 달라진 점이 있습니다. 잔금을 보내지 말고 확인하세요." if rd["differences"]
           else "열람 일시가 오래되었거나 읽지 못했습니다. 잔금 직전에 새로 열람해 다시 올려 주세요." if not rd["recent"]
           else "AI 가 읽은 범위에서 계약 때와 달라진 점이 없습니다. 원본과 직접 대조한 뒤 '확인했음'에 체크해 주세요.")
    return {"success": True, "available": True, "level": "warning" if warn else "success", "message": msg, "read": rd}


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
    from apps.rent_dist._rent3_core import balancecheck as bc
    bc.record(ct, req.changed, req.checked, req.issue_number, req.mortgage_cleared, app_source=APP_SRC)
    msg = ("등기부 변동을 신고했습니다. 임대인·본사 확인 전까지 잔금 납부를 보류하세요." if req.changed
           else "잔금일 등기부 확인을 기록했습니다. 잔금 입금 후 임대인이 확인하면 출입 정보가 열립니다.")
    return {"success": True, "message": msg, "balance_registry": ct.balance_registry}


class InsuranceProofReq(BaseModel):
    issuer: str            # 보증기관 (예: HUG, SGI서울보증, HF)
    policy_no: str         # 증권번호(또는 가입 접수번호)


@router.post("/myhome/{tenant_id}/insurance-proof", dependencies=[Depends(tenant_scope)])
def insurance_proof(tenant_id: str, req: InsuranceProofReq):
    """'보증보험 가입 예정'으로 계약한 임차인이 가입 증빙(보증기관·증권번호)을 제출한다. 제출해야 임대인이 출입 정보를 교부할 수 있다."""
    t = Tenant.get(tenant_id) or Tenant.first(phone=tenant_id)
    if not t:
        raise NotFound(f"임차인('{tenant_id}') 정보를 찾을 수 없습니다.")
    room = Room.get(t.current_room_id) if t.current_room_id else None
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
    if ct is None or not ct.insurance_proof_required or ct.keys_released is not False:
        raise DomainError("보증보험 증빙을 제출할 계약이 아닙니다.", hint="'가입 예정'으로 계약하고 아직 출입 정보를 받기 전인 계약에서만 사용합니다.")
    issuer, no = req.issuer.strip(), req.policy_no.strip()
    if len(issuer) < 2 or len(no) < 4:
        raise DomainError("보증기관과 증권번호(접수번호)를 정확히 입력해 주세요.")
    ct.insurance_proof = {"issuer": issuer, "policy_no": no, "submitted_at": now_iso()}
    ct.save(action="insurance_proof_submitted", app_source=APP_SRC)
    return {"success": True, "message": "보증보험 증빙을 제출했습니다. 임대인이 확인한 뒤 잔금일에 출입 정보를 교부합니다.", "insurance_proof": ct.insurance_proof}


@router.post("/myhome/{tenant_id}/transfer-notice", dependencies=[Depends(tenant_scope)])
def send_transfer_notice(tenant_id: str, req: TransferNoticeReq):
    """'이체했어요' 알림. 장부(수납)는 임대인이 입금을 확인하고 반영할 때만 바뀐다(임차인이 스스로 납부 처리를 할 수 없다).
    같은 호실의 대기 중인 알림이 있으면 금액·메모를 고쳐 하나만 둔다."""
    t = Tenant.require(tenant_id)
    if not t.room_no:
        raise DomainError("현재 입주 중인 호실이 없습니다.")
    if req.amount <= 0 or req.amount > 100_000_000:
        raise DomainError("이체한 금액을 확인해 주세요.", hint="0원보다 크고 1억 원 이하로 입력합니다.")
    memo = (req.memo or "").strip()[:100]
    n = next((x for x in TransferNotice.find(branch=t.branch, room_no=t.room_no, tenant_id=t.id) if x.status == "PENDING"), None)
    if n is None:
        n = TransferNotice(branch=t.branch, room_no=t.room_no, tenant_id=t.id, tenant_name=t.name)
    n.amount, n.memo = req.amount, memo
    n.save(action="transfer_notice_sent", app_source=APP_SRC)
    return {"success": True, "message": f"임대인에게 {req.amount:,}원 이체 사실을 알렸습니다. 임대인이 입금을 확인하면 납부로 기록됩니다.", "notice": n.model_dump()}


@router.post("/myhome/{tenant_id}/vehicle", dependencies=[Depends(tenant_scope)])
def update_vehicle(tenant_id: str, req: VehicleReq):
    t = Tenant.require(tenant_id)
    t.register_vehicle(req.car_number, req.car_model, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"차량번호 '{t.car_number}'가 건물 주차 대장에 등록되었습니다.",
        "tenant": t.safe_dump(),
    }


@router.post("/myhome/{tenant_id}/checkin", dependencies=[Depends(tenant_scope)])
def confirm_checkin(tenant_id: str, req: CheckinReq):
    t = Tenant.require(tenant_id)
    t.confirm_checkin(req.memo, app_source=APP_SRC)
    return {
        "success": True,
        "message": "첫 입실 체크인 확인이 저장되었습니다.",
        "tenant": t.safe_dump(),
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


def _own_settlement(settlement_id: str, request: Request) -> CheckOutSettlement:
    """퇴실 정산서의 당사자 임차인 본인만(임대인·다른 임차인은 불가)."""
    from core.errors import Forbidden
    s = CheckOutSettlement.require(settlement_id)
    c = claims_from(request) or {}
    if c.get("r") != "tenant" or c.get("u") != f"tenant:{s.id_tenant}":
        raise Forbidden("이 정산서의 임차인 본인만 처리할 수 있습니다.")
    return s


@router.post("/checkout/{settlement_id}/confirm-checked", dependencies=[Depends(any_user)])
def confirm_checkout_checked(settlement_id: str, request: Request):
    """임차인이 임대인이 보낸 정산 내역을 확인했다고 표시한다(임대인 채팅에 알림)."""
    s = _own_settlement(settlement_id, request)
    s.tenant_confirm_checked(app_source=APP_SRC)
    return {"success": True, "message": "정산 내역을 확인했다고 임대인에게 알렸습니다. 이어서 반환받을 계좌를 입력하고 동의 서명을 해 주세요.", "settlement": s.model_dump()}


@router.post("/checkout/{settlement_id}/agree", dependencies=[Depends(any_user)])
def agree_checkout(settlement_id: str, req: CheckoutAgreeReq, request: Request):
    s = _own_settlement(settlement_id, request)
    s.tenant_agree(req.bank_account, req.signature, req.account_holder, app_source=APP_SRC)
    return {
        "success": True,
        "message": "퇴실 정산 명세 동의 및 반환계좌 등록이 완료되었습니다 (임대인 앱 즉시 반영).",
        "settlement": s.model_dump(),
    }


class TenantBizRegReq(BaseModel):
    attachment_data_url: str = ""
    email: str = ""
    business_number: str = ""
    company_name: str = ""
    ceo_name: str = ""
    business_address: str = ""
    consent: bool = False


@router.post("/myhome/{tenant_id}/business-registration", dependencies=[Depends(tenant_scope)])
def submit_tenant_business_registration(tenant_id: str, req: TenantBizRegReq):
    """사업자 임차인이 사업자등록증 사진과 수신 이메일을 내면(동의 시 AI가 읽어 채움) 세금계산서 발급 정보에 반영하고 임대인에게 알린다.
    사업자등록번호는 10자리 체크섬이 맞아야 하고, 사업자 상태 확인은 임대인이 세금계산서를 발급할 때 홈택스에서 한다."""
    from apps.rent_dist._rent3_core import franchise as fr, registry_ai, validate_email_format
    from apps.rent_dist._rent3_core.billing import validate_kr_business_number
    ensure_pool_seeded()
    t = Tenant.get(tenant_id)
    if not t or t.status != "ACTIVE":
        raise NotFound("입주 중인 임차인만 사업자 정보를 낼 수 있습니다.")
    ex: dict = {}
    saved = None
    if (req.attachment_data_url or "").strip():
        saved = fr.save_bizreg_file(t.id, req.attachment_data_url)
        if req.consent:
            if int(t.biz_ai_calls or 0) >= 6:
                raise DomainError("AI 읽기 횟수 한도를 넘었습니다.", hint="사업자등록번호·상호·대표자를 직접 입력해 주세요.")
            binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
            ai = registry_ai.analyze_bizreg(binary, mime)
            if ai["status"] == "ok":
                t.biz_ai_calls = int(t.biz_ai_calls or 0) + 1
                ex = ai.get("extracted") or {}
    num = (req.business_number or ex.get("business_number") or "").strip()
    if not num:
        raise DomainError("사업자등록번호를 읽지 못했습니다.", hint="사진이 선명한지 확인하거나 사업자등록번호 10자리를 직접 입력해 주세요.")
    val = validate_kr_business_number(num, strict_checksum=True)
    if not val["valid"]:
        raise DomainError(f"사업자등록번호({num})가 올바르지 않습니다: {val['reason']}", hint="번호를 다시 확인해 주세요. 번호가 없으면 세금계산서 대신 현금영수증을 요청하세요.")
    email = (req.email or t.email or "").strip()
    if not validate_email_format(email):
        raise DomainError("세금계산서를 받을 이메일을 입력해 주세요.", hint="예: name@example.com")
    d = val["digits"]
    t.business_number = f"{d[:3]}-{d[3:5]}-{d[5:]}"
    t.company_name = (req.company_name or ex.get("company_name") or t.company_name or "").strip()
    t.ceo_name = (req.ceo_name or ex.get("representative_name") or t.ceo_name or "").strip()
    t.business_address = (req.business_address or ex.get("business_address") or t.business_address or "").strip()
    t.email = email
    t.biz_submitted_at = now_iso()
    t.save(action="tenant_business_registration_submitted", app_source=APP_SRC)
    room = Room.get(t.current_room_id) if t.current_room_id else Room.first(branch=t.branch, room_no=t.room_no)
    # 세금계산서 대장은 계약 → 임차인 순으로 읽으므로, 계약에 이전 값이 남아 있으면 새로 낸 정보가 가려진다 → 활성 계약에도 같이 반영(부가세 청구 여부 vat_target 은 건드리지 않는다)
    ct = (Contract.first(id_room=room.id, status="ACTIVE") if room else None) or Contract.first(branch=t.branch, room_no=t.room_no, status="ACTIVE")
    if ct:
        ct.tenant_business_number, ct.tenant_email = t.business_number, t.email
        ct.tenant_company_name = t.company_name or ct.tenant_company_name
        ct.tenant_ceo_name = t.ceo_name or ct.tenant_ceo_name
        ct.tenant_business_address = t.business_address or ct.tenant_business_address
        ct.tax_invoice_requested = True
        ct.save(action="tenant_business_registration_submitted", app_source=APP_SRC)
    DirectChat(branch=t.branch, id_room=(room.id if room else ""), room_no=t.room_no, sender_role="tenant", sender_name=t.name,
               message=f"[🏢 사업자 정보 제출] {t.room_no}호 {t.name} 세입자가 사업자등록증 정보(사업자번호 {t.business_number}, 상호 {t.company_name or '-'})와 세금계산서 수신 이메일을 제출했습니다. "
                       "세금계산서 발급 시 홈택스에서 사업자 상태를 확인해 주세요.").save(action="chat_auto_sent", app_source=APP_SRC)
    missing = [k for k, v in (("상호", t.company_name), ("대표자", t.ceo_name)) if not v]
    return {"success": True, "message": "사업자 정보를 제출했습니다. 임대인이 세금계산서를 발급합니다." + (f" (비어 있는 항목: {', '.join(missing)} — 아래에서 직접 입력해 다시 제출하세요)" if missing else ""),
            "business": {"business_number": t.business_number, "company_name": t.company_name, "ceo_name": t.ceo_name, "business_address": t.business_address, "email": t.email}, "file_saved": bool(saved)}


@router.get("/myhome/{tenant_id}/business-registration", dependencies=[Depends(tenant_scope)])
def get_tenant_business_registration(tenant_id: str):
    t = Tenant.get(tenant_id)
    if not t:
        raise NotFound("임차인 정보를 찾을 수 없습니다.")
    return {"success": True, "business": {"business_number": t.business_number, "company_name": t.company_name, "ceo_name": t.ceo_name, "business_address": t.business_address, "email": t.email}, "submitted_at": t.biz_submitted_at}


@router.post("/myhome/{tenant_id}/checkout/request", dependencies=[Depends(tenant_scope)])
def request_checkout(tenant_id: str, req: CheckoutRequestReq):
    """임차인이 퇴실을 먼저 신청한다(채팅 카드로 임대인에게 전달, 연말정산 자료 미리 받기 안내 포함)."""
    ensure_pool_seeded()
    t = Tenant.get(tenant_id)
    if not t or t.status != "ACTIVE":
        raise NotFound("입주 중인 임차인만 퇴실을 신청할 수 있습니다.")
    s = CheckOutSettlement.tenant_request(t, req.desired_date, req.memo, app_source=APP_SRC)
    return {"success": True, "message": "퇴실 신청이 임대인에게 전달되었습니다. 연말정산 납부확인서는 지금 미리 내려받아 두세요.", "settlement": s.tenant_view() or {}}


@router.post("/checkout/{settlement_id}/confirm-received", dependencies=[Depends(any_user)])
def confirm_checkout_received(settlement_id: str, request: Request):
    s = _own_settlement(settlement_id, request)
    s.tenant_confirm_received(app_source=APP_SRC)
    return {"success": True, "message": "수취 확인이 완료되어 퇴실 정산이 끝났습니다. 다음 해 2월 말까지 로그인해서 납부확인서·정산서를 조회할 수 있습니다.", "settlement": s.model_dump()}


@router.post("/checkout/{settlement_id}/dispute", dependencies=[Depends(any_user)])
def dispute_checkout(settlement_id: str, req: CheckoutDisputeReq, request: Request):
    s = _own_settlement(settlement_id, request)
    s.tenant_dispute(req.note, app_source=APP_SRC)
    return {"success": True, "message": "이의가 임대인에게 전달되었습니다. 채팅 또는 통화로 협의해 주세요.", "settlement": s.model_dump()}


@router.get("/myhome/{tenant_id}/archive", dependencies=[Depends(tenant_scope)])
def moved_out_archive(tenant_id: str):
    """퇴실한 임차인의 조회 전용 자료(마지막 계약의 연말정산 납부확인서·퇴실 정산서). 퇴실 후 다음 해 2월 말까지 로그인해서 볼 수 있다."""
    ensure_pool_seeded()
    t = Tenant.get(tenant_id)
    if not t:
        raise NotFound("임차인 정보를 찾을 수 없습니다.")
    cts = sorted(Contract.find(id_tenant=t.id), key=lambda x: x.updated_at, reverse=True)
    ct = cts[0] if cts else None
    cert = TaxAndCertificateService.build_tenant_tax_certificate(ct.id) if ct else None
    sets = sorted((x for x in CheckOutSettlement.find(id_tenant=t.id) if x.status != "DRAFT"), key=lambda x: x.updated_at, reverse=True)
    mask = CheckOutSettlement._mask_account
    return {"success": True, "name": t.name, "moved_out": t.status == "MOVED_OUT", "readonly_until": t.readonly_until, "certificate": cert,
            "settlements": [{**{k: v for k, v in x.model_dump().items() if k not in ("refund_bank_account", "tenant_signature")}, "account_masked": mask(x.refund_bank_account)} for x in sets]}


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
        + (f" 사전 매물 공개는 퇴실 기준일 1개월 전인 {chk.prelist_open_date}부터 시작됩니다." if chk.prelist_pending else "")
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



