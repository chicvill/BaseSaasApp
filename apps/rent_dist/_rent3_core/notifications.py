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
    return _item("messages", "임대인이 보낸 메시지", n, f"/rent3_tenant/?tenant={t.id}&svc=messages", "normal")


def _t_support(t: Tenant) -> Optional[Dict[str, Any]]:
    return _item("support", "관리인 문의 답변", sup.unread_count(sup.thread_key(t), "user"), "", "normal", action="support")


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


TENANT_COLLECTORS: List[Callable[[Tenant], Any]] = [_t_checkout, _t_expiry, _t_unpaid, _t_keys, _t_messages, _t_support, _t_billing, _t_transfer_rejected, _t_tickets]


# ───────────────────────── 임대인 ─────────────────────────
def _l_base(l: Lessor) -> str:
    return f"/rent3_lessor/?branch={l.branch}"


def _l_messages(l: Lessor) -> Optional[Dict[str, Any]]:
    from . import room_messages as rm
    n = sum(int(r["unread"]) for r in rm.rooms_overview(l.branch))
    return _item("messages", "세입자가 보낸 메시지", n, _l_base(l) + "&tab=messages", "normal")


def _l_support(l: Lessor) -> Optional[Dict[str, Any]]:
    return _item("support", "관리인 문의 답변", sup.unread_count(sup.thread_key(l), "user"), "", "normal", action="support")


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
        _item("checkout_agreed", "임차인 동의 완료 — 송금 후 '송금 표시' 필요", sum(1 for s in rows if s.status == "AGREED"), link, "normal"),
    ]


def _l_unpaid(l: Lessor) -> Optional[Dict[str, Any]]:
    n = 0
    for c in Contract.find(branch=l.branch, status="ACTIVE"):
        try:
            if int(c.ledger()["total_unpaid"]) > 0:
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


LESSOR_COLLECTORS: List[Callable[[Lessor], Any]] = [_l_checkout, _l_ops, _l_expiry, _l_keys, _l_franchise, _l_messages, _l_support, _l_unpaid]


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
        _item("support", "임대인·임차인 문의 — 안 읽음", unread, base + "support", "urgent"),
        _item("franchise_review", "가맹 심사 대기", review, base + "lessors", "urgent"),
        _item("hometax_verify", "홈택스 사업자 상태 확인 대기", hometax, base + "lessors", "urgent"),
        _item("fee_overdue", "가맹비 연체", overdue, base + "lessors", "urgent"),
        _item("tax_invoice", "가맹비 전자세금계산서 발행 필요", invoice, base + "lessors", "normal"),
    ]


ADMIN_COLLECTORS: List[Callable[[Any], Any]] = [_a_all]


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
    ck, rev = f"{role}:{key}", _revision()
    hit = _cache.get(ck)
    if use_cache and hit and hit["rev"] == rev and time.time() - hit["at"] < CACHE_SECONDS:
        return hit["data"]
    colls = {"tenant": TENANT_COLLECTORS, "lessor": LESSOR_COLLECTORS, "admin": ADMIN_COLLECTORS}[role]
    items = _run(colls, ctx)
    data = {"total": sum(i["count"] for i in items), "urgent": sum(i["count"] for i in items if i["level"] == "urgent"), "items": items,
            "generated_at": now_iso()}
    _cache[ck] = {"rev": rev, "at": time.time(), "data": data}
    return data


def mark_seen(user: Any, kind: str) -> None:
    """안내성 알림 '확인함'. 지금 시각 이후에 생긴 것만 다시 알린다."""
    if kind not in ("transfer_rejected",) or not hasattr(user, "notice_seen"):
        return
    seen = dict(user.notice_seen or {})
    seen[kind] = now_iso()
    user.notice_seen = seen
    user.save(action="notice_seen", app_source="rent3_notify")
    _cache.clear()
