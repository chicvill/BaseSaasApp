"""
apps/rent_dist/_rent3_core/notifications.py — 알림 일괄 수집(알림 센터).

관리인·임대인·임차인이 '내가 처리하거나 확인할 일'을 한 번에 본다. 앱을 열지 않은 동안 생긴 일은 서버의 원본 데이터(메시지·정산서·접수 등)에 그대로 남아 있다가,
열 때 이 모듈이 **원본 상태에서 계산**해 알려 준다(알림을 따로 저장하지 않으므로 어긋나지 않고, 할 일이 끝나면 저절로 사라진다).

- 수집기(collector): 역할별 함수 목록. 하나가 오류를 내도 나머지는 계속한다.
- 항목: {kind, title, count, link, level(urgent|normal|info), action?, detail?}. 메시지 본문·계좌 등 개인정보는 싣지 않는다.
- '확인함' 처리가 필요한 안내성 알림(이체 거절 통보 등)만 사용자별 notice_seen[kind] 시각 이후 것만 센다.
- 계산은 컬렉션을 훑으므로 같은 사람의 요청은 풀 변경이 없으면 CACHE_SECONDS 동안 재사용한다.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from . import support as sup
from .models import Admin, Contract, Lessor, Room, Tenant, normalize_branch_id
from .ops import (CheckOutSettlement, ConnectRequest, ExpiryRenewalCheck, MaintenanceTicket, RentBillingNotice, TransferNotice)
from .pool import now_iso, pool

CACHE_SECONDS = 60
LEVEL_ORDER = {"urgent": 0, "normal": 1, "info": 2}
_cache: Dict[str, Any] = {}


def _item(kind: str, title: str, count: int, link: str = "", level: str = "normal", action: str = "", detail: str = "") -> Optional[Dict[str, Any]]:
    if count <= 0:
        return None
    return {"kind": kind, "title": title, "count": int(count), "link": link, "level": level, "action": action, "detail": detail}


def _seen(user: Any, kind: str) -> str:
    return str((getattr(user, "notice_seen", None) or {}).get(kind) or "")


# ───────────────────────── 임차인 ─────────────────────────
def _t_messages(t: Tenant) -> Optional[Dict[str, Any]]:
    from . import room_messages as rm
    if not t.room_no:
        return None
    room = Room.first(branch=t.branch, room_no=t.room_no)
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
    n = rm.unread_count(t.branch, t.room_no, "tenant", t, ct.created_at if ct else "")
    return _item("messages", "임대인이 보낸 채팅", n, f"/rent3_tenant/?tenant={t.id}&svc=messages", "normal")


def _t_support(t: Tenant) -> Optional[Dict[str, Any]]:
    return _item("support", "관리인 채팅 답변", sup.unread_count(sup.thread_key(t), "user"), "", "normal", action="support")


def _t_checkout(t: Tenant) -> List[Optional[Dict[str, Any]]]:
    rows = CheckOutSettlement.find(id_tenant=t.id)
    link = f"/rent3_tenant/?tenant={t.id}&svc=checkout"
    return [
        _item("checkout_agree", "퇴실 정산서 확인·동의 필요", sum(1 for s in rows if s.status == "CALCULATED"), link, "urgent"),
        _item("checkout_receive", "보증금 반환 송금 — 받았는지 확인 필요", sum(1 for s in rows if s.status == "PAID"), link, "urgent"),
        _item("checkout_dispute", "정산 이의 진행 중(임대인 회신 대기)", sum(1 for s in rows if s.status == "DISPUTED"), link, "info"),
    ]


def _t_expiry(t: Tenant) -> Optional[Dict[str, Any]]:
    n = sum(1 for e in ExpiryRenewalCheck.find(id_tenant=t.id) if e.status == "AWAITING_TENANT")
    return _item("expiry_reply", "만기 후 계획(전출·연장) 회신 요청", n, f"/rent3_tenant/?tenant={t.id}&svc=expiry", "urgent")


def _t_billing(t: Tenant) -> Optional[Dict[str, Any]]:
    n = sum(1 for b in RentBillingNotice.find(id_tenant=t.id) if b.status == "SENT")
    return _item("billing_notice", "월세 납입·미납 안내", n, f"/rent3_tenant/?tenant={t.id}&svc=home", "normal")


def _t_unpaid(t: Tenant) -> Optional[Dict[str, Any]]:
    total = 0
    for c in Contract.find(id_tenant=t.id, status="ACTIVE"):
        try:
            total += int(c.ledger()["total_unpaid"])
        except Exception:
            continue
    return _item("unpaid", "미납 월세가 있습니다", 1 if total > 0 else 0, f"/rent3_tenant/?tenant={t.id}&svc=home", "urgent", detail=f"{total:,}원")


def _t_keys(t: Tenant) -> List[Optional[Dict[str, Any]]]:
    pend = [c for c in Contract.find(id_tenant=t.id, status="ACTIVE") if c.keys_released is False]
    need = [c for c in pend if not (c.balance_registry or {}).get("checked_at")]
    wait = [c for c in pend if (c.balance_registry or {}).get("checked_at") and not (c.balance_registry or {}).get("changed")]
    return [
        _item("balance_registry", "입주 전 등기부 재확인 필요", len(need), "/rent3_contract/?role=tenant", "urgent"),
        _item("keys_waiting", "잔금 확인 후 출입 정보가 열립니다", len(wait), f"/rent3_tenant/?tenant={t.id}&svc=door", "info"),
    ]


def _t_transfer_rejected(t: Tenant) -> Optional[Dict[str, Any]]:
    seen = _seen(t, "transfer_rejected")
    n = sum(1 for x in TransferNotice.find(tenant_id=t.id) if x.status == "REJECTED" and (x.handled_at or "") > seen)
    return _item("transfer_rejected", "이체 확인 요청이 거절되었습니다", n, f"/rent3_tenant/?tenant={t.id}&svc=home", "normal", detail="확인하면 사라집니다")


def _t_tickets(t: Tenant) -> Optional[Dict[str, Any]]:
    n = sum(1 for k in MaintenanceTicket.find(id_tenant=t.id) if k.status == "COMPLETED")
    return _item("as_completed", "A/S 수리 완료 — 확인해 주세요", n, f"/rent3_tenant/?tenant={t.id}&svc=as", "info")


def _contract_rooms(role: str, user: Any) -> List[Optional[Dict[str, Any]]]:
    """내가 당사자인 진행 중 계약방마다: 안 읽은 채팅 수, 상대가 보내 내가 답할 제안·수정 요청 수. 누르면 그 계약방 채팅이 바로 열린다."""
    import re
    from urllib.parse import quote
    from . import contract_chat as cc
    from .cosign import CoSignSession
    digits = lambda v: re.sub(r"\D", "", str(v or ""))
    out: List[Optional[Dict[str, Any]]] = []
    if role == "lessor":
        mine = [s for s in CoSignSession.find(branch=user.branch)]
    else:
        mine = [s for s in CoSignSession.all()
                if (s.id_tenant and s.id_tenant == user.id) or (digits(user.phone) and digits((s.terms or {}).get("tenant_phone")) == digits(user.phone))]
    for s in mine:
        if s.status != "IN_PROGRESS":
            continue
        link = f"/rent3_contract/?session={quote(str(s.id))}&chat=1"
        if s.call_status == "CALLING" and s.initiated_by_role != role:
            kind_label = "연장 재계약" if s.mode == "RENEWAL" else "신규 계약"
            out.append(_item(f"contract_call:{s.id}", f"{s.room_no}호 계약 요청 — 확인하기", 1, link, "urgent", detail=f"{s.building_name} {s.room_no}호 ({kind_label})"))
        sm = cc.summary_for(s, role)
        waiting = sum(1 for m in cc.open_items(s.id) if m.sender_role != role)       # 내가 답해야 하는 것(내가 보낸 것은 제외)
        out.append(_item(f"contract_chat:{s.id}", f"{s.room_no}호 계약방 채팅", int(sm["unread"]), link, "normal", detail=f"{s.room_no}호 계약방"))
        out.append(_item(f"contract_open:{s.id}", f"{s.room_no}호 계약방 — 답할 제안·수정 요청", waiting, link, "urgent", detail=f"{s.room_no}호 계약방"))
    return out


def _t_contract(t: Tenant) -> List[Optional[Dict[str, Any]]]:
    return _contract_rooms("tenant", t)


def _l_contract(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    return _contract_rooms("lessor", l)


TENANT_COLLECTORS: List[Callable[[Tenant], Any]] = [_t_checkout, _t_expiry, _t_unpaid, _t_keys, _t_messages, _t_support, _t_billing, _t_transfer_rejected, _t_tickets, _t_contract]


# ───────────────────────── 임대인 ─────────────────────────
def _l_base(l: Lessor) -> str:
    return f"/rent3_lessor/?branch={l.branch}"


def _l_messages(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    """호실별로 한 줄씩: 누르면 그 호실 대화가 바로 열린다(?tab=messages&room=호실)."""
    from urllib.parse import quote
    from . import room_messages as rm
    out = []
    for r in rm.rooms_overview(l.branch):
        who = f" · {r['tenant_name']}" if r.get("tenant_name") else ""
        out.append(_item(f"messages:{r['room_no']}", f"{r['room_no']}호 임차인 채팅", int(r["unread"]),
                         _l_base(l) + f"&tab=messages&room={quote(str(r['room_no']))}", "normal", detail=f"{r['room_no']}호{who}"))
    return out


def _l_support(l: Lessor) -> Optional[Dict[str, Any]]:
    return _item("support", "관리인 채팅 답변", sup.unread_count(sup.thread_key(l), "user"), "", "normal", action="support")


def _l_expiry(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    rows = [e for e in ExpiryRenewalCheck.find(branch=l.branch)]
    link = _l_base(l) + "&tab=expiry"
    checked = {e.id_contract for e in rows}
    soon = 0
    for c in Contract.find(branch=l.branch, status="ACTIVE"):
        try:
            if c.id not in checked and (c.expiry_window_status() or {}).get("in_6_to_2_months_window"):
                soon += 1
        except Exception:
            continue
    return [
        _item("moveout_reply", "세입자 전출 회신 도착", sum(1 for e in rows if e.status == "MOVEOUT_PLANNED"), link, "urgent"),
        _item("stay_reply", "세입자 연장 의사 확인 — 재계약 예고 필요", sum(1 for e in rows if e.status == "STAY_CONFIRMED"), link, "normal"),
        _item("expiry_soon", "만기 6~2개월 전 — 전출 의사 확인 필요", soon, link, "normal"),
    ]


def _l_ops(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    base = _l_base(l)
    conn = sum(1 for c in ConnectRequest.find(branch=l.branch) if c.status == "PENDING" and (not c.expires_at or c.expires_at > now_iso()))
    return [
        _item("as_received", "A/S 접수 — 배정 필요", sum(1 for k in MaintenanceTicket.find(branch=l.branch) if k.status == "RECEIVED"), base + "&tab=ops", "normal"),
        _item("connect_request", "세입자 연결 요청 — 승인 대기", conn, base + "&tab=ledger", "urgent"),
        _item("transfer_pending", "이체 확인 요청 — 입금 확인 필요", sum(1 for x in TransferNotice.find(branch=l.branch) if x.status == "PENDING"), base + "&tab=ledger", "urgent"),
    ]


def _l_checkout(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    rows = CheckOutSettlement.find(branch=l.branch)
    link = _l_base(l) + "&tab=ledger"
    return [
        _item("checkout_requested", "퇴실 신청 — 정산서 작성 필요", sum(1 for s in rows if s.status == "REQUESTED"), link, "urgent"),
        _item("checkout_disputed", "정산 이의 — 협의 후 정산서 수정 필요", sum(1 for s in rows if s.status == "DISPUTED"), link, "urgent"),
        _item("checkout_draft", "퇴실 정산서 작성 중 — 마치고 '게시'해야 임차인에게 보임", sum(1 for s in rows if s.status == "DRAFT"), link, "normal"),
        _item("checkout_agreed", "임차인 동의 완료 — 송금 후 '송금 표시' 필요", sum(1 for s in rows if s.status == "AGREED"), link, "normal"),
    ]


def _l_unpaid(l: Lessor) -> Optional[Dict[str, Any]]:
    n = 0
    for c in Contract.find(branch=l.branch, status="ACTIVE"):
        try:
            if int(c.ledger().get("overdue_unpaid", 0)) > 0:      # 납입일이 지난 미납만(납부 예정은 제외)
                n += 1
        except Exception:
            continue
    return _item("unpaid_rooms", "미납 호실", n, _l_base(l) + "&tab=ledger", "normal")


def _l_keys(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    pend = [c for c in Contract.find(branch=l.branch, status="ACTIVE") if c.keys_released is False]
    link = _l_base(l) + "&tab=ledger"
    return [
        _item("registry_changed", "임차인이 잔금 전 등기부 변동을 신고했습니다", sum(1 for c in pend if (c.balance_registry or {}).get("changed")), link, "urgent"),
        _item("keys_release", "잔금 확인 후 출입 정보 교부 대기", sum(1 for c in pend if (c.balance_registry or {}).get("checked_at") and not (c.balance_registry or {}).get("changed")), link, "normal"),
        _item("keys_wait_tenant", "임차인의 잔금 전 등기부 재확인 대기", sum(1 for c in pend if not (c.balance_registry or {}).get("checked_at")), link, "info"),
    ]


def _l_franchise(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    from .feebilling import FranchiseFee
    label = Lessor.STATUS_LABELS.get(l.status, l.status)
    fees = [f for f in FranchiseFee.find(id_lessor=l.id) if f.status == "NOTICED"]
    return [
        _item("franchise_status", f"가맹 상태: {label}", 0 if l.status == "ACTIVE" else 1, _l_base(l), "urgent", detail=l.status_note or ""),
        _item("fee_due", "가맹비 납부 안내", len(fees), _l_base(l), "normal", detail=f"{sum(f.total_amount for f in fees):,}원"),
    ]


def _l_mail(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    """입금 메일 자동 정리: 확인 대기(처리하면 사라짐)·자동 수납 결과·해석 실패(안내성, 확인함 뒤 다시 안 뜸). 꺼져 있으면 없음."""
    from .mailpay import MailIntake, feature_enabled
    if not feature_enabled() or not (l.mail_opt or {}).get("active"):
        return []
    rows = MailIntake.find(lessor_id=l.id)
    link = _l_base(l) + "&tab=mail"
    seen_a, seen_f = _seen(l, "mail_auto"), _seen(l, "mail_failed")
    return [
        _item("mail_pending", "확인 대기 입금 메일", sum(1 for x in rows if x.status == "PENDING"), link, "normal"),
        _item("mail_auto", "입금 메일 자동 수납", sum(1 for x in rows if x.status == "AUTO_APPLIED" and (x.received_at or "") > seen_a), link, "info", detail="확인하면 사라집니다"),
        _item("mail_failed", "입금 메일 처리 실패", sum(1 for x in rows if x.status == "FAILED" and (x.received_at or "") > seen_f), link, "normal", detail="확인하면 사라집니다"),
    ]


def _l_prelist_alerts(l: Lessor) -> List[Optional[Dict[str, Any]]]:
    """중도 퇴실 확인 즉시 또는 만기 전출 예정 30일 전 — 임대인에게 PDF 작성·매물 등록을 알린다."""
    from urllib.parse import quote
    out: List[Optional[Dict[str, Any]]] = []
    for a in ExpiryRenewalCheck.lessor_listing_alerts(l.branch):
        link = _l_base(l) + f"&tab=rooms&room={quote(str(a['room_no']))}"
        out.append(_item(f"listing_needed:{a['room_no']}", a["title"], 1, link, "urgent", detail=a["detail"]))
    return out


LESSOR_COLLECTORS: List[Callable[[Lessor], Any]] = [_l_prelist_alerts, _l_mail, _l_checkout, _l_ops, _l_expiry, _l_keys, _l_franchise, _l_messages, _l_support, _l_unpaid, _l_contract]


# ───────────────────────── 관리인 ─────────────────────────
def _a_all(_: Any) -> List[Optional[Dict[str, Any]]]:
    from .feebilling import FranchiseFee
    from .support import SupportMessage
    today = date.today().isoformat()
    lessors = Lessor.all()
    review = sum(1 for l in lessors if l.status == "PENDING_APPROVAL" and l.applied_at)
    hometax = sum(1 for l in lessors if ((l.franchise_docs or {}).get("bizreg") or {}).get("file") and not ((l.franchise_docs or {}).get("bizreg") or {}).get("admin_verification"))
    fees = FranchiseFee.all()
    overdue = sum(1 for f in fees if f.status == "NOTICED" and f.due_date and f.due_date < today)
    invoice = sum(1 for f in fees if f.status == "PAID" and f.invoice_status in ("NONE", "FAILED"))
    unread = sum(1 for m in SupportMessage.all() if m.sender_role == "user" and not m.read_by_admin)
    base = "/rent3_admin/?tab="
    return [
        _item("support", "임대인·임차인 채팅 — 안 읽음", unread, base + "support", "urgent"),
        _item("franchise_review", "가맹 심사 대기", review, base + "lessors", "urgent"),
        _item("hometax_verify", "홈택스 사업자 상태 확인 대기", hometax, base + "lessors", "urgent"),
        _item("fee_overdue", "가맹비 연체", overdue, base + "lessors", "urgent"),
        _item("tax_invoice", "가맹비 전자세금계산서 발행 필요", invoice, base + "lessors", "normal"),
    ]


ADMIN_COLLECTORS: List[Callable[[Any], Any]] = [_a_all]


# ───────────────────────── 최근 대화 (새 글이 없어도 알림창에서 이전 대화로 돌아가는 목록) ─────────────────────────
RECENT_CHATS_MAX = 8


def _when(iso: str) -> str:
    """'2026-11-25T12:30:05' → '11/25 12:30'"""
    try:
        return f"{int(iso[5:7])}/{int(iso[8:10])} {iso[11:16]}"
    except Exception:
        return ""


def _recent(role: str, ctx: Any) -> List[Dict[str, Any]]:
    """알림창 '최근 대화': 채팅창을 연 기록(세션)을 '채팅 : 상대(시작 M/D HH:MM)' 제목과 이동 주소만으로 보여 준다(대화 내용·미리보기 없음)."""
    from urllib.parse import quote
    from . import chatsessions as cht
    from . import room_messages as rm
    try:
        viewer = {"tenant": f"tenant:{getattr(ctx, 'id', '')}", "lessor": f"lessor:{getattr(ctx, 'id', '')}", "admin": "admin"}[role]
        out = []
        for s in cht.recent(viewer, RECENT_CHATS_MAX):
            title = f"채팅 : {s.label_text}({_when(s.created_at)})"
            link, action, unread = "", "", 0
            if s.kind == "support" and role == "admin":
                link = f"/rent3_admin/?tab=support&user={s.ref}"
                unread = sup.unread_count(s.ref, "admin")
            elif s.kind == "support":
                action = "support"
                unread = sup.unread_count(sup.thread_key(ctx), "user")
            elif s.kind == "contract":
                from .cosign import CoSignSession
                from . import contract_chat as cc
                cs = CoSignSession.by_code_or_id(s.ref)
                link = f"/rent3_contract/?session={quote(str(s.ref))}&chat=1"
                unread = cc.summary_for(cs, role)["unread"]
            elif role == "lessor":
                link = f"/rent3_lessor/?branch={s.branch}&tab=messages&room={quote(str(s.ref))}"
                unread = rm.unread_count(s.branch, str(s.ref), "lessor")
            else:
                link = f"/rent3_tenant/?tenant={getattr(ctx, 'id', '')}&svc=messages"
                room = Room.first(branch=ctx.branch, room_no=ctx.room_no) if ctx.room_no else None
                ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
                unread = rm.unread_count(ctx.branch, ctx.room_no, "tenant", ctx, ct.created_at if ct else "") if ctx.room_no else 0
            # unread 는 안 읽은 글 '개수'뿐(내용 아님) — 알림창에서 엷은 분홍색으로 구분한다
            out.append({"title": title, "preview": "", "at": s.created_at, "link": link, "action": action, "unread": int(unread)})
        return out
    except Exception:                          # 최근 대화를 못 만들어도 알림은 계속 보여 준다
        return []


# ───────────────────────── 일괄 실행 ─────────────────────────
def _run(collectors: List[Callable[[Any], Any]], ctx: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for fn in collectors:
        try:
            got = fn(ctx)
        except Exception:                      # 한 종류가 실패해도 나머지 알림은 계속 보여 준다
            continue
        for it in (got if isinstance(got, list) else [got]):
            if it:
                items.append(it)
    items.sort(key=lambda x: (LEVEL_ORDER.get(x["level"], 9), x["title"]))
    return items


def _revision() -> int:
    try:
        return int(pool().overview()["total_revision"])
    except Exception:
        return -1


def summary(role: str, ctx: Any, key: str, use_cache: bool = True) -> Dict[str, Any]:
    """역할별 알림 요약. role: tenant|lessor|admin. key: 캐시·본인 구분용(예: 'tenant:<id>')."""
    ExpiryRenewalCheck.activate_due()           # 시작일이 된 연장 재계약·공개일이 된 사전 매물을 먼저 반영한다(조회 때 확인, 스케줄러 없음)
    ck, rev = f"{role}:{key}", _revision()
    hit = _cache.get(ck)
    if use_cache and hit and hit["rev"] == rev and time.time() - hit["at"] < CACHE_SECONDS:
        return hit["data"]
    colls = {"tenant": TENANT_COLLECTORS, "lessor": LESSOR_COLLECTORS, "admin": ADMIN_COLLECTORS}[role]
    items = _run(colls, ctx)
    data = {"total": sum(i["count"] for i in items), "urgent": sum(i["count"] for i in items if i["level"] == "urgent"), "items": items,
            "recent_chats": _recent(role, ctx), "generated_at": now_iso()}
    _cache[ck] = {"rev": rev, "at": time.time(), "data": data}
    return data


def mark_seen(user: Any, kind: str) -> None:
    """안내성 알림 '확인함'. 지금 시각 이후에 생긴 것만 다시 알린다."""
    if kind not in ("transfer_rejected", "mail_auto", "mail_failed") or not hasattr(user, "notice_seen"):
        return
    seen = dict(user.notice_seen or {})
    seen[kind] = now_iso()
    user.notice_seen = seen
    user.save(action="notice_seen", app_source="rent3_notify")
    _cache.clear()
