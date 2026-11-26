"""
apps/rent_dist/rent3_lessor/routers/comms.py — 알림 · 임차인 연결/초대 · A/S 티켓 · 호실 1:1 채팅 · 납입/미납 통보.
"""
from typing import Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Building,
    CheckOutSettlement,
    Lessor,
    MaintenanceTicket,
    RentBillingNotice,
    Room,
    Tenant,
    chatsessions as chat_sessions,
    connect,
    contract_chat,
    ensure_pool_seeded,
    normalize_branch_id,
    room_messages,
)
from apps.rent_dist._rent3_core.authz import claims_from, is_admin
from ._deps import APP_SRC, _branch_tenants, _my_lessor

router = APIRouter()


class NoticeSeenReq(BaseModel):
    branch: str = "main"
    kind: str


class ConnectApproveReq(BaseModel):
    branch: str = "main"
    code: str = ""


class TenantInviteReq(BaseModel):
    branch: str = "main"
    room_no: str


class BulkInviteReq(BaseModel):
    branch: str = "main"
    room_no: str = ""      # 비우면 가입 전 전원, 값이 있으면 그 호실 한 곳만(다른 사람이 받은 링크는 그대로 둔다)


class InviteCancelReq(BaseModel):
    branch: str = "main"
    room_no: str


class TicketAssignReq(BaseModel):
    vendor_name: str
    vendor_phone: str
    visit_date: str


class TicketCompleteReq(BaseModel):
    repair_cost: int = 0
    cost_burden: str = "LANDLORD"
    resolution_note: str = ""


class ChatReplyReq(BaseModel):
    branch: str = "main"
    room_no: str
    message: str


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


@router.get("/notifications")
def lessor_notifications(branch: str = "main"):
    """내 건물에 온 알림 일괄 요약(건수·종류·이동 주소만). 관리인이 보는 경우에도 해당 지점 것만."""
    from apps.rent_dist._rent3_core import notifications as nf
    lsr = _my_lessor(branch)
    return {"success": True, **nf.summary("lessor", lsr, lsr.branch)}


@router.post("/notifications/seen")
def lessor_notice_seen(req: NoticeSeenReq):
    from apps.rent_dist._rent3_core import notifications as nf
    lsr = _my_lessor(req.branch)
    nf.mark_seen(lsr, req.kind)
    return {"success": True, **nf.summary("lessor", lsr, lsr.branch, use_cache=False)}


@router.get("/connect-requests")
def list_connect_requests(branch: str = "main"):
    """승인 대기 요청과 최근 승인한 요청의 진행 상태(임대인 앱이 가볍게 주기적으로 조회해 새 요청이 오면 창을 띄운다)."""
    ensure_pool_seeded()
    return {"success": True, "pending": connect.pending_for_branch(branch), "recent": connect.recent_for_branch(branch)}


@router.post("/connect-requests/{req_id}/approve")
def approve_connect_request(req_id: str, req: ConnectApproveReq, request: Request):
    """기존 임차인의 '내 페이지 연결 요청' 승인. 임차인(장부의 휴대폰)에게 전화해 화면에 보이는 4자리 확인번호를 듣고 입력해야 한다."""
    ensure_pool_seeded()
    r = connect.approve(req_id, req.branch, req.code, str((claims_from(request) or {}).get("u") or "lessor"))
    return {"success": True, "message": f"{r.room_no}호 {r.tenant_name}님의 연결을 승인했습니다. 임차인 화면에서 비밀번호를 정하게 됩니다."}


@router.post("/connect-requests/{req_id}/reject")
def reject_connect_request(req_id: str, req: ConnectApproveReq, request: Request):
    ensure_pool_seeded()
    r = connect.reject(req_id, req.branch, str((claims_from(request) or {}).get("u") or "lessor"))
    return {"success": True, "message": f"{r.room_no}호 {r.tenant_name}님의 연결 요청을 거절했습니다."}


@router.post("/tenant-shortcuts/revoke-all")
def revoke_tenant_shortcuts(req: TenantInviteReq):
    """임차인이 폰을 잃어버렸을 때: 그 임차인의 간편 비밀번호·바로가기 기기를 모두 해제한다(이미 열린 화면도 바로 막힌다). 비밀번호 로그인은 그대로 된다."""
    from apps.rent_dist._rent3_core import quicklogin as ql
    ensure_pool_seeded()
    room = Room.first(branch=normalize_branch_id(req.branch), room_no=req.room_no)
    t = Tenant.get(room.id_tenant) if (room and room.id_tenant) else None
    if t is None:
        raise DomainError(f"{req.room_no}호에 입주한 임차인이 없습니다.")
    n = ql.revoke_all(t)
    return {"success": True, "message": f"{t.name}님의 바로가기 {n}대와 간편 비밀번호를 해제했습니다. 새 폰에서는 비밀번호로 로그인해 다시 설정하게 하세요."}


@router.get("/tenant-invites")
def tenant_invites_status(branch: str = "main"):
    """입주 임차인별 가입 현황(링크 원문은 없다): 가입 완료 여부, 유효한 안내 링크가 있는지."""
    from apps.rent_dist._rent3_core import connect as cn, invite as inv
    ensure_pool_seeded()
    return {
        "success": True,
        "tenants": [
            {
                "room_no": t.room_no,
                "tenant_name": t.name,
                "phone": t.phone,
                "activated": cn.activated(t),
                "invite_pending": inv.status(t)["pending"],
            }
            for t in _branch_tenants(branch)
        ],
    }


@router.post("/tenant-invites/cancel")
def tenant_invite_cancel(req: InviteCancelReq):
    """한 호실의 연결을 취소한다. 가입을 마친 호실은 '가입 상태 초기화'(비밀번호·간편 비밀번호·바로가기·동의 기록을 지워 가입 전으로), 아직 가입 전이면 보낸 링크를 못 쓰게 한다. 호실·계약·납부 내역은 그대로."""
    from apps.rent_dist._rent3_core import connect as cn
    ensure_pool_seeded()
    t = next((x for x in _branch_tenants(req.branch) if x.room_no == req.room_no), None)
    if t is None:
        raise DomainError(f"{req.room_no}호에 입주한 임차인이 없습니다.")
    if cn.activated(t):
        cn.reset_activation(t)
        return {"success": True, "kind": "reset", "message": f"{t.room_no}호 {t.name}님의 가입을 취소했습니다(비밀번호·간편 비밀번호·바로가기 삭제). 다시 안내 링크를 보내야 합니다."}
    if cn.cancel_invite(t):
        return {"success": True, "kind": "link", "message": f"{t.room_no}호 {t.name}님께 보낸 안내 링크를 취소했습니다."}
    # 링크도 가입 기록도 없어 보여도, 이전 등록의 찌꺼기(비밀번호·동의 기록·연결 요청)가 남았을 수 있어 깨끗이 초기화한다
    cn.reset_activation(t)
    return {"success": True, "kind": "reset", "message": f"{t.room_no}호 {t.name}님의 등록 기록을 초기화했습니다(호실·계약·납부는 그대로). 다시 안내 링크를 보내야 합니다."}


@router.post("/tenant-invites/bulk")
def tenant_invites_bulk(req: BulkInviteReq, request: Request):
    """아직 가입하지 않은 모든 입주 임차인에게 줄 **본인 전용 입주 안내 링크**(7일·1회)를 한꺼번에 만든다. 링크는 이 응답에서만 보인다(서버에는 해시만 남는다).
    가입을 마친 호실은 건너뛴다. 이전에 만든 링크는 새 링크로 바뀌어 쓸 수 없게 된다."""
    from apps.rent_dist._rent3_core import connect as cn, invite as inv
    ensure_pool_seeded()
    who = str((claims_from(request) or {}).get("u") or "lessor")
    links, skipped = [], 0
    for t in _branch_tenants(req.branch):
        if req.room_no and t.room_no != req.room_no:
            continue
        if cn.activated(t):
            skipped += 1
            continue
        tok = inv.issue(t, who)
        links.append({"room_no": t.room_no, "tenant_name": t.name, "phone": t.phone, "path": f"/rent3_tenant/?invite={tok}", "expires": t.invite_expires})
    return {
        "success": True,
        "links": links,
        "skipped_activated": skipped,
        "message": f"{len(links)}명의 개별 안내 링크를 만들었습니다(가입 완료 {skipped}명은 건너뜀). 7일 안에 한 번씩 쓸 수 있습니다.",
    }


@router.post("/tenant-invite")
def issue_tenant_invite(req: TenantInviteReq, request: Request):
    """입주 임차인에게 줄 1회용 입주 안내 카드(링크·QR)를 발급한다. 이전 링크는 무효가 된다. 링크 원문은 이 응답에서만 보인다."""
    from apps.rent_dist._rent3_core import invite as inv
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    t = Tenant.get(room.id_tenant) if (room and room.id_tenant) else None
    if t is None:
        raise DomainError(f"{req.room_no}호에 입주한 임차인이 없습니다.", hint="계약이 체결되어 입주한 호실에서만 발급할 수 있습니다.")
    tok = inv.issue(t, str((claims_from(request) or {}).get("u") or "lessor"))
    b = Building.first(branch=b_id)
    return {
        "success": True,
        "url": f"/rent3_tenant/?invite={tok}",
        "expires": t.invite_expires,
        "tenant_name": t.name,
        "room_no": t.room_no,
        "building_name": b.building_name if b else "",
        "message": "입주 안내 카드를 만들었습니다(7일 안에 1회 사용).",
    }


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


@router.post("/chats")
def send_lessor_chat(req: ChatReplyReq):
    b_id = normalize_branch_id(req.branch)
    room = Room.first(branch=b_id, room_no=req.room_no)
    lessor = Lessor.first(branch=b_id)
    msg = room_messages.post(
        b_id,
        req.room_no,
        "lessor",
        lessor.name if lessor else "임대인",
        req.message,
        tenant_id=(room.id_tenant if room else "") or "",
    )
    return {"success": True, "message": "채팅을 보냈습니다.", "chat": msg.model_dump()}


@router.get("/messages/rooms")
def messages_rooms(branch: str = "main"):
    """채팅 화면 목록: 호실별 마지막 글·안 읽은 수 (관리인 지원 채팅·계약방 대화는 섞지 않는다)."""
    ensure_pool_seeded()
    contract_chat.purge_expired()
    return {"success": True, "rooms": room_messages.rooms_overview(branch)}


@router.get("/messages/rooms/{room_no}")
def messages_room(room_no: str, request: Request, branch: str = "main", mark_read: bool = True):
    """한 호실의 대화와 계약 협상 기록(접어서 보여 줄 것, 체결 때 기록한 해시 무결성 포함)."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    room = Room.first(branch=b_id, room_no=room_no)
    if not room:
        raise DomainError(f"{room_no}호를 찾을 수 없습니다.")
    msgs = room_messages.room_messages(b_id, room_no)
    unread = sum(1 for m in msgs if room_messages._unread(m, "lessor"))
    c = claims_from(request)
    if mark_read and not (c and is_admin(c)):
        room_messages.mark_read(msgs, "lessor")
    tenant = Tenant.get(room.id_tenant) if room.id_tenant else None
    lsr_me = Lessor.for_branch(b_id)
    if lsr_me is not None and not (c and is_admin(c)):                      # 채팅 세션(제목·시작 시각만 기록)
        chat_sessions.touch(f"lessor:{lsr_me.id}", "room", room_no, f"{room_no}호 임차인", b_id)
    return {
        "success": True,
        "room_no": room_no,
        "tenant_name": tenant.name if tenant else "",
        "unread": unread,
        "tenant_phone": ((tenant.phone if tenant else "") or "").strip(),          # 임차인에게 전화 단추용
        "checkout": next(
            (x.chat_card("lessor") for x in sorted(CheckOutSettlement.find(id_room=room.id), key=lambda x: x.updated_at, reverse=True) if x.status != "COMPLETED"),
            None,
        ),   # 채팅창 위 정산 카드(계좌·송금 완료 표시)
        "messages": [room_messages.public(m) for m in msgs],
        "negotiation": room_messages.negotiation_history(room_messages.active_contract(room)),
    }


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
        "hint": "임차인 마이홈 상단 알림 배너와 1:1 채팅으로 실시간 전달되었습니다.",
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
