"""
apps/_rent3_core/domains/ops_notice.py — 매월 임대료 납입일 3일전 사전통보 및 1일후 미납통보(RentBillingNotice) 도메인 객체.
"""
from __future__ import annotations

from datetime import date
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import Conflict, DomainError
from ..models import Contract, Lessor, Room, date_today, normalize_branch_id
from ..pool import PoolObj, now_iso
from .ops_support import DirectChat


class RentBillingNotice(PoolObj):
    """매월 임대료 납입일(=입주일) 3일 전 사전 통보 및 납입일 1일 후 미납 통보 객체 (`ops.json`)."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "임대료 납입·미납 통보"

    id: str = Field(default_factory=lambda: f"bn_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_contract: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    lessor_name: str = ""
    lessor_account: str = ""
    notice_type: str = "PRE_DUE_3D"          # PRE_DUE_3D (납입일 3일전 통보) / OVERDUE_1D (납입일 1일후 미납통보)
    billing_ym: str = ""                     # 대상 귀속년월 (YYYY-MM)
    payment_day: int = 1                     # 매월 약정 납입일 (= 입주일 일자)
    due_date: str = ""                       # 실제 납입기한일 (YYYY-MM-DD)
    trigger_date: str = ""                   # 통보 발송 기준일 (YYYY-MM-DD)
    days_offset: int = -3                    # 납입일 대비 일수 (-3: 3일전, +1: 1일후 등)
    amount_due: int = 0                      # 안내/독촉 금액 (원)
    unpaid_months: List[str] = Field(default_factory=list)
    title: str = ""
    message: str = ""
    idempotency_key: str = ""
    status: str = "SENT"                     # SENT / ACKNOWLEDGED / RESOLVED_PAID
    acknowledged_at: str = ""
    resolved_at: str = ""

    def summary(self) -> str:
        kind = "납입3일전예고" if self.notice_type == "PRE_DUE_3D" else "납입+1일미납통보"
        return f"{kind} [{self.room_no}호 {self.tenant_name}] {self.billing_ym} ({self.amount_due:,}원, {self.status})"

    @staticmethod
    def _parse_ref_date(as_of_date: Optional[str] = None) -> date:
        if as_of_date and as_of_date.strip():
            try:
                return date.fromisoformat(as_of_date.strip()[:10])
            except ValueError:
                raise DomainError(f"올바르지 않은 기준일 형식입니다: '{as_of_date}' (예: 2026-10-29)")
        return date_today()

    @classmethod
    def for_contract(cls, contract_id: str, limit: int = 10) -> List["RentBillingNotice"]:
        if not contract_id:
            return []
        items = RentBillingNotice.find(id_contract=contract_id)
        return sorted(items, key=lambda x: x.created_at, reverse=True)[:limit]

    @classmethod
    def send_for_room(
        cls,
        branch: str,
        room_no: str,
        notice_type: str,
        as_of_date: Optional[str] = None,
        force_pre_due: bool = False,
        app_source: str = "rent3_lessor",
    ) -> "RentBillingNotice":
        b_id = normalize_branch_id(branch)
        room = Room.first(branch=b_id, room_no=room_no)
        if not room or not room.active_contract_id:
            raise DomainError(f"{room_no}호에는 현재 진행 중인 활성 임대차 계약이 없습니다.")
        ct = Contract.require(room.active_contract_id)
        ref = cls._parse_ref_date(as_of_date)
        return cls.send_for_contract(
            ct=ct,
            notice_type=notice_type,
            as_of=ref,
            force_pre_due=force_pre_due,
            app_source=app_source,
        )

    @classmethod
    def send_for_contract(
        cls,
        ct: Contract,
        notice_type: str,
        as_of: Optional[date] = None,
        force_pre_due: bool = False,
        app_source: str = "rent3_lessor",
    ) -> "RentBillingNotice":
        ntype = (notice_type or "").strip().upper()
        if ntype not in ("PRE_DUE_3D", "OVERDUE_1D"):
            raise DomainError("통보 유형은 'PRE_DUE_3D'(납입 3일전 통보) 또는 'OVERDUE_1D'(납입 1일후 미납통보)여야 합니다.")

        ref = as_of or date_today()
        sched = ct.billing_notice_schedule(as_of=ref)
        lessor = Lessor.get(ct.id_lessor) if ct.id_lessor else Lessor.first(branch=ct.branch)
        l_name = lessor.name if lessor else (ct.lessor_name or "임대인")
        l_acct = lessor.account_number if (lessor and lessor.account_number) else "임대인 지정 계좌"
        pay_day = int(sched["payment_day"])

        if ntype == "PRE_DUE_3D":
            billing_ym = str(sched["upcoming_billing_ym"])
            due_date = str(sched["upcoming_due_date"])
            amount_due = int(sched["upcoming_unpaid_amount"])
            days_until = int(sched["days_until_due"])
            if amount_due <= 0:
                raise DomainError(
                    f"{ct.room_no}호는 {billing_ym}월 청구 예정 임대료가 없거나 이미 완납(선수금) 상태입니다."
                )
            if not force_pre_due and not sched["is_pre_due_3d"]:
                raise DomainError(
                    f"{ct.room_no}호는 기준일({ref.isoformat()}) 현재 납입일({due_date}) 3일 전 구간이 아닙니다 (D-{days_until}).",
                    hint="정기 자동통보는 정확히 납입일 3일 전에 발송됩니다.",
                )

            idem = f"bn:{ct.id}:{billing_ym}:PRE_DUE_3D"
            dup = cls.first(idempotency_key=idem)
            if dup:
                raise Conflict(
                    f"{ct.room_no}호({ct.tenant_name})의 {billing_ym}월분 납입일 사전 통보는 이미 발송되었습니다.",
                    hint=f"기존 발송일시: {dup.created_at[:16]}",
                )

            d_tag = "3일 전" if days_until == 3 else f"D-{days_until}"
            title = f"[🔔 임대료 납입일 {d_tag} 안내] {ct.room_no}호 {billing_ym}월분 ({due_date} 납입기한)"
            msg = (
                f"{title} — 매월 약정 납입일(입주일 기준 매월 {pay_day}일, 금회 {due_date})이 도래합니다. "
                f"납부 예정액 {amount_due:,}원을 입금계좌({l_acct}) 또는 마이홈 온라인 결제로 납부해 주세요."
            )
            notice = cls(
                branch=ct.branch,
                id_building=ct.id_building,
                id_room=ct.id_room,
                room_no=ct.room_no,
                id_contract=ct.id,
                id_tenant=ct.id_tenant,
                tenant_name=ct.tenant_name,
                tenant_phone=ct.tenant_phone,
                lessor_name=l_name,
                lessor_account=l_acct,
                notice_type="PRE_DUE_3D",
                billing_ym=billing_ym,
                payment_day=pay_day,
                due_date=due_date,
                trigger_date=ref.isoformat(),
                days_offset=-days_until,
                amount_due=amount_due,
                unpaid_months=[billing_ym],
                title=title,
                message=msg,
                idempotency_key=idem,
                status="SENT",
            ).save(action="rent_pre_due_3d_notice_sent", app_source=app_source)
        else:
            billing_ym = str(sched["past_billing_ym"])
            due_date = str(sched["past_due_date"])
            amount_due = int(sched["past_unpaid_amount"])
            days_past = int(sched["days_past_due"])
            unpaid_months = [str(x) for x in sched["unpaid_months"]]

            if amount_due <= 0:
                raise DomainError(
                    f"{ct.room_no}호는 현재 미납 임대료가 없는 완납 상태이므로 미납 통보를 발송할 수 없습니다."
                )
            if days_past < 1:
                raise DomainError(
                    f"{ct.room_no}호는 아직 납입기한({due_date}) 1일 후가 경과하지 않았습니다."
                )

            idem = f"bn:{ct.id}:{billing_ym}:OVERDUE_1D"
            dup = cls.first(idempotency_key=idem)
            if dup and dup.status != "RESOLVED_PAID":
                raise Conflict(
                    f"{ct.room_no}호({ct.tenant_name})의 {billing_ym}월 기준 미납 통보는 이미 발송되었습니다.",
                    hint=f"기존 발송일시: {dup.created_at[:16]}",
                )

            months_label = ", ".join(unpaid_months) if unpaid_months else billing_ym
            day_label = "1일 경과" if days_past == 1 else f"{days_past}일 경과"
            title = f"[🚨 임대료 미납 통보] {ct.room_no}호 납입일({due_date}) {day_label} · 미납 {amount_due:,}원"
            msg = (
                f"{title} — 약정 납입일(매월 {pay_day}일)이 지나 미납 내역({months_label}, 합계 {amount_due:,}원)이 확인되었습니다. "
                f"입금계좌({l_acct}) 송금 또는 임차인 마이홈에서 즉시 납부 부탁드립니다."
            )
            notice = cls(
                branch=ct.branch,
                id_building=ct.id_building,
                id_room=ct.id_room,
                room_no=ct.room_no,
                id_contract=ct.id,
                id_tenant=ct.id_tenant,
                tenant_name=ct.tenant_name,
                tenant_phone=ct.tenant_phone,
                lessor_name=l_name,
                lessor_account=l_acct,
                notice_type="OVERDUE_1D",
                billing_ym=billing_ym,
                payment_day=pay_day,
                due_date=due_date,
                trigger_date=ref.isoformat(),
                days_offset=days_past,
                amount_due=amount_due,
                unpaid_months=unpaid_months,
                title=title,
                message=msg,
                idempotency_key=idem,
                status="SENT",
            ).save(action="rent_overdue_1d_notice_sent", app_source=app_source)

        DirectChat(
            branch=ct.branch,
            id_room=ct.id_room,
            room_no=ct.room_no,
            sender_role="lessor",
            sender_name=l_name,
            message=notice.message,
            quick_action_label="마이홈 즉시 납부",
            quick_action_url=f"/rent3_tenant/?tenant={ct.id_tenant}",
        ).save(action="chat_auto_sent", app_source=app_source)
        return notice

    @classmethod
    def run_daily_auto_dispatch(
        cls,
        branch: Optional[str] = None,
        as_of_date: Optional[str] = None,
        include_catchup_overdue: bool = False,
        app_source: str = "rent3_lessor",
    ) -> Dict[str, Any]:
        """기준일(as_of_date) 정기 배치 실행:
        1) 납입일 3일 전(D-3) 호실 -> PRE_DUE_3D 자동 통보
        2) 납입일 1일 후(D+1, 또는 include_catchup_overdue=True 시 D+1 이상 미통보분) 미납 호실 -> OVERDUE_1D 자동 통보
        """
        ref = cls._parse_ref_date(as_of_date)
        contracts = (
            Contract.find(branch=normalize_branch_id(branch), status="ACTIVE")
            if branch else Contract.find(status="ACTIVE")
        )

        sent_pre_due: List[Dict[str, Any]] = []
        sent_overdue: List[Dict[str, Any]] = []
        skipped_duplicates = 0

        for ct in sorted(contracts, key=lambda c: (c.branch, c.room_no)):
            sched = ct.billing_notice_schedule(as_of=ref)
            if sched["is_pre_due_3d"]:
                try:
                    bn = cls.send_for_contract(ct, "PRE_DUE_3D", as_of=ref, force_pre_due=False, app_source=app_source)
                    sent_pre_due.append(bn.model_dump())
                except Conflict:
                    skipped_duplicates += 1

            should_overdue = (
                bool(sched["is_overdue_eligible"]) if include_catchup_overdue else bool(sched["is_overdue_1d_exact"])
            )
            if should_overdue:
                try:
                    bn_ov = cls.send_for_contract(ct, "OVERDUE_1D", as_of=ref, app_source=app_source)
                    sent_overdue.append(bn_ov.model_dump())
                except Conflict:
                    skipped_duplicates += 1

        return {
            "as_of_date": ref.isoformat(),
            "sent_pre_due": sent_pre_due,
            "sent_overdue": sent_overdue,
            "total_sent": len(sent_pre_due) + len(sent_overdue),
            "skipped_duplicates": skipped_duplicates,
        }

    def acknowledge(self, app_source: str = "rent3_tenant") -> "RentBillingNotice":
        if self.status != "RESOLVED_PAID":
            self.status = "ACKNOWLEDGED"
        self.acknowledged_at = now_iso()
        return self.save(action="billing_notice_acknowledged", app_source=app_source)

    @classmethod
    def resolve_paid_for_contract(
        cls,
        contract_id: str,
        after_ledger: Dict[str, Any],
        app_source: str = "rent3_lessor",
    ) -> int:
        """월세 수납 즉시 해당 귀속월 미납이 해소된 통보(PRE_DUE_3D / OVERDUE_1D)를 RESOLVED_PAID로 자동 전환."""
        if not contract_id:
            return 0
        unpaid_by_ym = {str(m["ym"]): int(m["unpaid"]) for m in after_ledger.get("months", [])}
        total_unpaid = int(after_ledger.get("total_unpaid", 0))
        resolved_cnt = 0
        for bn in cls.find(id_contract=contract_id):
            if bn.status == "RESOLVED_PAID":
                continue
            if bn.notice_type == "OVERDUE_1D":
                target_cleared = (total_unpaid == 0) or all(unpaid_by_ym.get(ym, 0) == 0 for ym in (bn.unpaid_months or [bn.billing_ym]))
            else:
                target_cleared = (bn.billing_ym in unpaid_by_ym) and (unpaid_by_ym[bn.billing_ym] == 0)
            if target_cleared:
                bn.status = "RESOLVED_PAID"
                bn.resolved_at = now_iso()
                bn.save(action="billing_notice_resolved_by_payment", app_source=app_source)
                resolved_cnt += 1
        return resolved_cnt
