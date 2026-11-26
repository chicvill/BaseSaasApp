"""
apps/_rent3_core/domains/ops_checkout.py — 퇴실 시설 대조 점검 & 보증금 공제 정산(CheckOutSettlement) 도메인 객체.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError
from ..models import Contract, Room, Tenant, date_today, normalize_branch_id
from ..pool import PoolObj, now_iso
from .ops_support import DirectChat


class CheckOutSettlement(PoolObj):
    """퇴실 시설 대조 점검 & 보증금 공제 정산 및 원클릭 공실 자동 복귀 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "퇴실 정산서"

    id: str = Field(default_factory=lambda: f"co_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_contract: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    status: str = "CALCULATED"               # DRAFT(임대인이 작성 중 — 임차인에게 안 보임, '게시'해야 CALCULATED) / REQUESTED(임차인 신청) -> CALCULATED(임대인이 게시한 정산서) -> AGREED(임차인 동의·계좌) -> PAID(임대인 송금 표시) -> COMPLETED(임차인 수취 확인 또는 7일 자동 확정) / DISPUTED(이의)
    published_at: str = ""                   # 임대인이 정산서를 임차인에게 게시한 마지막 시각(게시한 적이 있는 정산서를 다시 수정하려고 거두면 '수정 중'으로 보인다)
    requested_at: str = ""
    desired_date: str = ""                   # 임차인이 적은 퇴실 희망일
    tenant_request_memo: str = ""
    account_holder: str = ""                 # 반환 계좌 예금주(계약자와 같아야 한다)
    paid_at: str = ""
    paid_memo: str = ""
    confirm_due: str = ""                    # 송금 표시 뒤 임차인이 확인하지 않으면 자동 확정하는 시각(7일)
    received_at: str = ""
    auto_confirmed: bool = False
    dispute_note: str = ""
    disputed_at: str = ""
    deposit_original: int = 0
    unpaid_rent_deduction: int = 0           # Contract.ledger() 실시간 미납액 자동 반영
    early_rent_deduction: int = 0            # 조기 전출 때 지금 달 이후 ~ 만기일(또는 새 임차인 입주 전)까지 아직 안 낸 월세 — 이전 임차인이 부담하고 보증금에서 공제
    liability_end_date: str = ""             # 위 월세를 부담하는 마지막 날(만기일 또는 새 임차인 입주일)
    liability_reason: str = ""               # EXPIRY(만기일까지) / NEW_TENANT(새 임차인 입주 전날까지)
    early_partial_days: int = 0              # 부담이 끝나는 날이 한 달 기간(전입일~다음 달 전입일 전날) 중간일 때 그 기간의 경과 일수
    early_partial_amount: int = 0            # 그 일수분 일할 금액 = 경과 일수 × 월 임차료 ÷ 30 (early_rent_deduction 에 포함)
    shortfall_amount: int = 0                # 공제액이 보증금보다 커서 보증금으로 못 채운 금액(임차인이 따로 부담)
    # ── 전출 정산 = 임차보증금 − 미납 임차료 − 일할 계산 임차료 − 청소비 − 미납 공과금(전기·수도·가스·기타) (− 파손 복구비). 임대인이 항목별로 수정할 수 있다.
    rent_unpaid_auto: int = 0                # 미납 임차료 자동 계산값(지금 달까지 미납 + 만기까지 끝난 달의 미납 월 임차료)
    rent_unpaid: int = 0                     # 미납 임차료 확정값(임대인이 고치지 않으면 자동 계산값)
    rent_prorated_auto: int = 0              # 일할 계산 임차료 자동 계산값 = (경과 일수 × 월 임차료) ÷ 30
    rent_prorated: int = 0                   # 일할 계산 임차료 확정값
    utility_electric: int = 0                # 미납 공과금 — 전기
    utility_water: int = 0                   # 미납 공과금 — 수도
    utility_gas: int = 0                     # 미납 공과금 — 가스
    utility_other: int = 0                   # 미납 공과금 — 그 밖(인터넷·관리비 정산 등). utility_settlement 는 네 항목의 합계
    edited_by_lessor: bool = False           # 임대인이 자동 계산값(미납 임차료·일할 임차료)을 고쳤는지
    tenant_checked_at: str = ""              # 임차인이 정산 내역을 확인했다고 누른 시각(정산서를 다시 보내면 지워진다)
    utility_settlement: int = 0              # 당일 계량기 공과금 실비
    cleaning_fee: int = 0                    # 이번 정산에서 공제하는 청소비(계약에 약정한 금액 이내)
    cleaning_fee_agreed: int = 0             # 계약서에 약정한 퇴실 청소비(Contract.cleaning_fee) — 이 금액을 넘겨 공제할 수 없다
    damage_restoration_fee: int = 0          # 세입자 과실 파손 실비 (자연마모 0원)
    refund_amount: int = 0
    refund_bank_account: str = ""
    tenant_signature: str = ""
    inspection_notes: str = ""
    completed_at: str = ""
    purged_at: str = ""                      # 민감 정보(계좌·서명·메모)를 지운 시각. 반환액·공제 내역은 남긴다

    # 보증금 반환 확정(수취 확인·자동 확정) 때 또는 퇴실 희망일 후 이 일수가 지나면 아래 항목을 지운다. 금액·공제 내역·계약·수납 기록은 남긴다(연말정산·소득 증빙).
    PURGE_DAYS: ClassVar[int] = 30
    SENSITIVE_FIELDS: ClassVar[tuple] = ("refund_bank_account", "account_holder", "tenant_signature", "dispute_note", "tenant_request_memo", "inspection_notes", "paid_memo")

    def purge_sensitive(self, app_source: str = "rent3_checkout") -> bool:
        """반환 계좌·예금주·서명·이의/점검/요청 메모를 지운다. 이미 지웠거나 지울 것이 없으면 False."""
        if self.purged_at or not any(getattr(self, f) for f in self.SENSITIVE_FIELDS):
            return False
        for f in self.SENSITIVE_FIELDS:
            setattr(self, f, "")
        self.purged_at = now_iso()
        self.save(action="checkout_sensitive_purged", app_source=app_source)
        return True

    @classmethod
    def purge_due(cls, branch: str = "", tenant_id: str = "") -> int:
        """끝나지 않은 정산도 퇴실 희망일(없으면 신청·생성일)로부터 PURGE_DAYS 가 지나면 민감 정보를 지운다. 조회 때 함께 실행한다."""
        limit = (datetime.now() - timedelta(days=cls.PURGE_DAYS)).date().isoformat()
        n = 0
        for s in cls.all():
            if (branch and s.branch != branch) or (tenant_id and s.id_tenant != tenant_id) or s.purged_at:
                continue
            ref = (s.desired_date or s.requested_at or s.created_at or "")[:10]
            if ref and ref <= limit and s.purge_sensitive():
                n += 1
        return n

    def summary(self) -> str:
        return f"퇴실정산 [{self.room_no}호] 반환액 {self.refund_amount:,}원 ({self.status})"

    @classmethod
    def calculate_for_room(
        cls,
        branch: str,
        room_no: str,
        utility_settlement: int = 0,
        cleaning_fee: Optional[int] = None,
        damage_restoration_fee: int = 0,
        inspection_notes: str = "6대 주요 시설(에어컨·세탁기·냉장고·도어락·벽지·욕실) 자연마모 외 이상 없음",
        app_source: str = "rent3_lessor",
        unpaid_rent: Optional[int] = None,
        prorated_rent: Optional[int] = None,
        utility_electric: int = 0,
        utility_water: int = 0,
        utility_gas: int = 0,
        utility_other: int = 0,
        publish: bool = False,
    ) -> "CheckOutSettlement":
        """정산서를 계산해 저장한다. publish=False 면 임시 저장(DRAFT) — 임차인에게 보이지 않고 알림도 가지 않는다(수정 중에 임차인이 보고 이의를 제기하지 않도록).
        publish=True 면 임차인에게 게시(CALCULATED)하고 채팅·알림을 보낸다."""
        b_id = normalize_branch_id(branch)
        room = Room.first(branch=b_id, room_no=room_no)
        if not room or not room.active_contract_id:
            raise DomainError(f"{room_no}호는 현재 퇴실 정산할 활성 계약이 없습니다.")

        ct = Contract.require(room.active_contract_id)
        # 청소비: 계약서에 퇴실 청소비를 부담하기로 약정한 경우에만, 약정한 금액 이내로 보증금에서 공제한다(약정 없으면 0원).
        agreed = max(0, ct.cleaning_fee or 0)
        cleaning_fee = agreed if cleaning_fee is None else min(max(0, cleaning_fee), agreed)
        liab = ct.checkout_liability(room)            # 만기 전 퇴실이면 만기일(또는 새 임차인 입주 전)까지의 월세도 이전 임차인이 부담한다
        unpaid = int(liab["unpaid_now"])
        early = int(liab["early_amount"])
        partial = int(liab["partial_amount"])
        # 정산 항목: 미납 임차료(자동 = 지금까지 미납 + 만기까지 끝난 달) / 일할 계산 임차료(자동 = 경과 일수 × 월 임차료 ÷ 30) — 임대인이 고칠 수 있다
        rent_unpaid_auto = unpaid + max(0, early - partial)
        rent_prorated_auto = partial
        for label, v in (("미납 임차료", unpaid_rent), ("일할 계산 임차료", prorated_rent), ("전기 요금", utility_electric), ("수도 요금", utility_water),
                         ("가스 요금", utility_gas), ("기타 공과금", utility_other), ("파손 복구비", damage_restoration_fee)):
            if v is not None and v < 0:
                raise DomainError(f"{label}은(는) 0원 이상이어야 합니다.")
        rent_unpaid = rent_unpaid_auto if unpaid_rent is None else unpaid_rent
        rent_prorated = rent_prorated_auto if prorated_rent is None else prorated_rent
        u_el, u_wa, u_ga, u_ot = utility_electric or 0, utility_water or 0, utility_gas or 0, utility_other or 0
        if (utility_settlement or 0) > 0 and not (u_el or u_wa or u_ga or u_ot):      # 예전 방식(공과금 한 칸)은 '기타'로 받는다
            u_ot = utility_settlement
        utility_total = u_el + u_wa + u_ga + u_ot
        total_deduct = rent_unpaid + rent_prorated + utility_total + cleaning_fee + (damage_restoration_fee or 0)
        refund = max(0, ct.deposit - total_deduct)
        shortfall = max(0, total_deduct - ct.deposit)

        existing = next((x for x in cls.find(id_room=room.id) if x.status in cls.OPEN_STATES), None)
        if existing is not None and existing.status == "PAID":
            raise DomainError("이미 송금을 표시한 정산서입니다.", hint="임차인이 이의를 제기하면 수정해서 다시 보낼 수 있습니다.")
        fields: Dict[str, Any] = dict(
            branch=b_id, id_building=room.id_building, id_room=room.id, room_no=room.room_no, id_contract=ct.id,
            id_tenant=ct.id_tenant, tenant_name=ct.tenant_name, status="CALCULATED" if publish else "DRAFT", deposit_original=ct.deposit,
            unpaid_rent_deduction=unpaid, early_rent_deduction=early, liability_end_date=liab["end_date"] if early else "",
            liability_reason=liab["reason"] if early else "", shortfall_amount=shortfall,
            early_partial_days=int(liab["partial_days"]) if early else 0, early_partial_amount=int(liab["partial_amount"]) if early else 0,
            utility_settlement=utility_total, cleaning_fee=cleaning_fee, cleaning_fee_agreed=agreed,
            damage_restoration_fee=damage_restoration_fee or 0, refund_amount=refund, inspection_notes=inspection_notes,
            rent_unpaid_auto=rent_unpaid_auto, rent_unpaid=rent_unpaid, rent_prorated_auto=rent_prorated_auto, rent_prorated=rent_prorated,
            utility_electric=u_el, utility_water=u_wa, utility_gas=u_ga, utility_other=u_ot,
            edited_by_lessor=(rent_unpaid != rent_unpaid_auto or rent_prorated != rent_prorated_auto), tenant_checked_at="",
            published_at=now_iso() if publish else (existing.published_at if existing is not None else ""),
        )
        was_published = existing is not None and existing.status in ("CALCULATED", "AGREED", "DISPUTED")
        if existing is not None:        # 임차인 신청·이의 건을 이어서 쓴다(동의·계좌는 다시 받는다)
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.refund_bank_account, existing.tenant_signature, existing.account_holder = "", "", ""
            existing.dispute_note, existing.disputed_at, existing.confirm_due = "", "", ""
            settlement = existing.save(action="checkout_recalculated", app_source=app_source)
        else:
            settlement = cls(**fields).save(action="checkout_calculated", app_source=app_source)

        if not publish:                  # 임시 저장: 임차인에게 알리지 않는다(이미 게시했던 정산서를 거둔 경우에만 '수정 중' 안내)
            if was_published:
                DirectChat(branch=b_id, id_room=room.id, room_no=room.room_no, sender_role="system", sender_name="퇴실정산 안내",
                           message="[✏️ 정산서 수정 중] 임대인이 정산서를 수정하고 있어 내용을 잠시 숨겼습니다. 수정이 끝나 게시되면 다시 알려 드립니다.",
                           quick_action_label="정산 상태 보기", quick_action_url=f"/rent3_tenant/?tenant={ct.id_tenant}&svc=checkout").save(action="chat_auto_sent", app_source=app_source)
            return settlement
        extra_note = ""
        if early:
            who = "새 임차인이 입주하기 전날" if liab["reason"] == "NEW_TENANT" else "계약 만기일"
            if fields["edited_by_lessor"]:        # 임대인이 고친 정산서: 금액은 아래 [명세]에만 두고 여기서는 기준만 설명한다
                extra_note += f"(만기 전 퇴실이라 {who}({liab['end_date']})까지는 이전 임차인이 월세를 부담하는 것으로 계산했습니다.) "
            else:
                part = (f" 이 중 한 달 기간 중간인 {liab['partial_days']}일분은 (경과 일수 × 월 임차료) ÷ 30 = {int(liab['partial_amount']):,}원입니다." if int(liab["partial_days"]) > 0 else "")
                extra_note += f"(공제에는 만기 전 퇴실에 따른 월세 부담분 {early:,}원이 포함됩니다 — {who}({liab['end_date']})까지는 이전 임차인이 월세를 부담합니다.{part}) "
        if shortfall:
            extra_note += f"공제액이 보증금보다 {shortfall:,}원 많아 이 금액은 따로 정산해야 합니다. "
        items = [("미납 임차료", rent_unpaid), ("일할 계산 임차료", rent_prorated), ("청소비", cleaning_fee), ("미납 공과금(전기 %s·수도 %s·가스 %s·기타 %s)" % (f"{u_el:,}", f"{u_wa:,}", f"{u_ga:,}", f"{u_ot:,}"), utility_total),
                 ("파손 복구비", damage_restoration_fee or 0)]
        extra_note += "[명세] 임차보증금 %s원 − " % f"{ct.deposit:,}" + " − ".join(f"{k} {v:,}원" for k, v in items if v) + f" = 반환 예정액 {refund:,}원. "
        if fields["edited_by_lessor"]:
            extra_note += f"(임대인이 자동 계산값(미납 임차료 {rent_unpaid_auto:,}원·일할 임차료 {rent_prorated_auto:,}원)에서 수정했습니다.) "
        DirectChat(
            branch=b_id,
            id_room=room.id,
            room_no=room.room_no,
            sender_role="system",
            sender_name="퇴실정산 안내",
            message=(f"[보증금 정산 명세서 도착] 원보증금 {ct.deposit:,}원 중 공제 {total_deduct:,}원을 제외한 실반환 예정액은 {refund:,}원입니다. {extra_note}"
                     "이 채팅창 위의 정산 카드(또는 임차인 앱의 퇴실 정산)에서 내용을 확인하고, 이의가 있으면 [이의 제기]로 채팅·통화 협의를 요청하세요. 이의가 없으면 [정산 내역 확인] 뒤 같은 카드에서 반환받을 본인 명의 계좌(예금주는 계약자 본인)를 입력하고 동의 서명을 하면, 임대인이 그 계좌로 보증금을 송금합니다.\n"
                     "📄 퇴실하면 로그인 수단이 정리되니 연말정산 납부확인서를 미리 내려받아 두세요."),
            quick_action_label="정산서 확인·연말정산 자료 받기",
            quick_action_url=f"/rent3_tenant/?tenant={ct.id_tenant}&svc=checkout",
        ).save(action="chat_auto_sent", app_source=app_source)
        return settlement

    # ───────── 임차인이 먼저 신청 → 임대인 정산서 → 동의·계좌 → 송금 → 수취 확인 (채팅 카드로 안내)
    OPEN_STATES: ClassVar[tuple] = ("DRAFT", "REQUESTED", "CALCULATED", "AGREED", "PAID", "DISPUTED")
    AUTO_CONFIRM_DAYS: ClassVar[int] = 7

    @staticmethod
    def _mask_account(acct: str) -> str:
        digits = "".join(ch for ch in (acct or "") if ch.isdigit())
        return f"끝 4자리 {digits[-4:]}" if len(digits) >= 4 else "입력됨"

    @staticmethod
    def _norm(name: str) -> str:
        return re.sub(r"\s+", "", re.sub(r"\([^)]*\)", "", name or ""))

    def _chat(self, sender_role: str, sender_name: str, message: str, label: str = "", url: str = "", app_source: str = "rent3_checkout") -> None:
        DirectChat(branch=self.branch, id_room=self.id_room, room_no=self.room_no, sender_role=sender_role, sender_name=sender_name,
                   message=message, quick_action_label=label, quick_action_url=url).save(action="chat_auto_sent", app_source=app_source)

    @classmethod
    def tenant_request(cls, tenant: "Tenant", desired_date: str = "", memo: str = "", app_source: str = "rent3_tenant") -> "CheckOutSettlement":
        room = Room.get(tenant.current_room_id) if tenant.current_room_id else Room.first(branch=tenant.branch, room_no=tenant.room_no)
        if not room or not room.active_contract_id:
            raise DomainError("퇴실을 신청할 수 있는 활성 계약이 없습니다.")
        open_ones = [x for x in cls.find(id_room=room.id) if x.status in cls.OPEN_STATES]
        draft = next((x for x in open_ones if x.status == "DRAFT"), None)
        if open_ones and not (draft is not None and not draft.requested_at and len(open_ones) == 1):
            raise DomainError("이미 진행 중인 퇴실 정산이 있습니다.", hint="채팅에서 진행 상황을 확인해 주세요.")
        ct = Contract.require(room.active_contract_id)
        if draft is not None:        # 임대인이 먼저 작성 중인 정산서가 있으면 신청만 기록한다(아직 게시 전이라 임차인에게는 '신청 접수'로 보인다)
            draft.requested_at = now_iso()
            draft.desired_date = (desired_date or "").strip()
            draft.tenant_request_memo = (memo or "").strip()
            draft.save(action="checkout_requested_on_draft", app_source=app_source)
            draft._chat("tenant", ct.tenant_name, f"[🚪 퇴실 신청] {room.room_no}호 {ct.tenant_name} 세입자가 퇴실을 신청했습니다. 희망일 {draft.desired_date or '미정'}."
                        + (f" 메모: {draft.tenant_request_memo}" if draft.tenant_request_memo else "") + " 작성 중인 정산서를 게시하고, 중도 퇴실이라면 바로 표준 임대조건서(PDF)를 작성해 매물을 등록해 주세요.",
                        "📄 PDF 작성 · 매물 등록", f"/rent3_lessor/?branch={tenant.branch}&tab=rooms&room={room.room_no}", app_source=app_source)
            return draft
        s = cls(branch=tenant.branch, id_building=room.id_building, id_room=room.id, room_no=room.room_no, id_contract=ct.id, id_tenant=tenant.id,
                tenant_name=ct.tenant_name, status="REQUESTED", deposit_original=ct.deposit, requested_at=now_iso(),
                desired_date=(desired_date or "").strip(), tenant_request_memo=(memo or "").strip()).save(action="checkout_requested", app_source=app_source)
        s._chat("tenant", ct.tenant_name, f"[🚪 퇴실 신청] {room.room_no}호 {ct.tenant_name} 세입자가 퇴실을 신청했습니다. 희망일 {s.desired_date or '미정'}."
                + (f" 메모: {s.tenant_request_memo}" if s.tenant_request_memo else "") + " 점검 후 정산서를 보내 주시고, 중도 퇴실이라면 지금 바로 표준 임대조건서(PDF)를 작성해 매물을 등록하실 수 있습니다.",
                "📄 PDF 작성 · 매물 등록", f"/rent3_lessor/?branch={tenant.branch}&tab=rooms&room={room.room_no}", app_source=app_source)
        s._chat("system", "퇴실정산 안내", "퇴실하면 로그인 수단(바로가기·간편 비밀번호·등록 기기)이 정리됩니다. 📄 연말정산 납부확인서를 지금 미리 내려받아 두세요. "
                "(퇴실 후에도 다음 해 2월 말까지는 로그인해서 조회·내려받기만 할 수 있습니다.)", "연말정산 납부확인서 내려받기",
                f"/rent3_tenant/?tenant={tenant.id}&svc=cert", app_source=app_source)
        return s

    def tenant_view(self) -> Optional[Dict[str, Any]]:
        """임차인에게 내려 주는 모양. 임대인이 작성 중(DRAFT)이면 금액을 숨기고, 임차인이 신청했던 건은 '신청 접수', 한 번 게시했다가 거둔 건은 '수정 중'으로만 알린다.
        신청도 게시도 없던 초안은 임차인에게 없는 것과 같다(None)."""
        if self.status != "DRAFT":
            return self.model_dump()
        if self.published_at:
            status = "REVISING"
        elif self.requested_at:
            status = "REQUESTED"
        else:
            return None
        keep = ("id", "branch", "id_room", "room_no", "id_contract", "id_tenant", "tenant_name", "requested_at", "desired_date", "created_at", "updated_at")
        return {**{k: getattr(self, k) for k in keep}, "status": status, "deposit_original": self.deposit_original}

    def chat_card(self, viewer: str) -> Optional[Dict[str, Any]]:
        """채팅창 위에 보여 줄 정산 진행 카드(viewer: 'tenant' | 'lessor'). 임차인에게는 게시 전 금액·계좌를 주지 않고, 계좌 전체는 임대인에게만 준다."""
        if self.status == "COMPLETED":
            return None
        if viewer == "tenant":
            v = self.tenant_view()
            if v is None:
                return None
            out: Dict[str, Any] = {"id": self.id, "status": v["status"], "tenant_name": self.tenant_name}
            if v["status"] in ("CALCULATED", "AGREED", "PAID", "DISPUTED"):
                out.update({"refund_amount": self.refund_amount, "shortfall_amount": self.shortfall_amount, "tenant_checked_at": self.tenant_checked_at,
                            "account_masked": self._mask_account(self.refund_bank_account) if self.refund_bank_account else "", "confirm_due": self.confirm_due})
            return out
        out = {"id": self.id, "status": self.status, "room_no": self.room_no, "tenant_name": self.tenant_name, "refund_amount": self.refund_amount,
               "shortfall_amount": self.shortfall_amount, "tenant_checked_at": self.tenant_checked_at, "desired_date": self.desired_date,
               "paid_at": self.paid_at, "confirm_due": self.confirm_due}
        if self.status in ("AGREED", "PAID"):
            out.update({"account_holder": self.account_holder, "refund_bank_account": self.refund_bank_account})
        return out

    def tenant_confirm_checked(self, app_source: str = "rent3_tenant") -> "CheckOutSettlement":
        """임차인이 임대인이 보낸 정산 내역(항목별 공제)을 확인했다고 표시한다. 임대인 채팅에 알림이 가고, 이어서 반환 계좌 입력·동의를 한다."""
        if self.status != "CALCULATED":
            raise DomainError("확인할 수 있는 정산서가 아닙니다.", hint="임대인이 정산서를 보낸 뒤에 확인할 수 있습니다.")
        if not self.tenant_checked_at:
            self.tenant_checked_at = now_iso()
            self.save(action="checkout_checked_by_tenant", app_source=app_source)
            self._chat("tenant", self.tenant_name, f"[✅ 정산 내역 확인] {self.tenant_name} 세입자가 정산 내역을 확인했습니다. 반환 예정액 {self.refund_amount:,}원. "
                       "이의가 없으면 이어서 반환 계좌를 입력하고 동의 서명을 합니다.", app_source=app_source)
            self._chat("system", "퇴실정산 안내", f"[💳 반환 계좌 입력 안내] 정산 내역을 확인하셨습니다. 이 채팅창 위의 정산 카드에서 반환받을 본인 명의 계좌와 동의 서명을 입력해 주세요. "
                       f"제출하면 임대인이 그 계좌로 {self.refund_amount:,}원을 송금합니다. 예금주는 계약자({self.tenant_name}) 본인이어야 합니다.",
                       "계좌 입력하기", f"/rent3_tenant/?tenant={self.id_tenant}&svc=messages", app_source=app_source)
        return self

    def tenant_agree(self, bank_account: str, signature: str, account_holder: str = "", app_source: str = "rent3_tenant") -> "CheckOutSettlement":
        if self.status != "CALCULATED":
            raise DomainError("동의할 수 있는 정산서가 아닙니다.", hint="임대인이 정산서를 보낸 뒤에만, 한 번만 동의할 수 있습니다(동의 뒤 계좌 변경은 임대인에게 요청).")
        if not bank_account.strip() or not signature.strip():
            raise DomainError("반환받을 계좌번호와 동의 서명을 모두 입력해 주세요.")
        if self._norm(account_holder) != self._norm(self.tenant_name) or not self._norm(account_holder):
            raise DomainError("예금주가 계약자 본인과 같아야 합니다.", hint=f"계약자({self.tenant_name}) 본인 명의 계좌만 쓸 수 있습니다. 예금주를 정확히 입력해 주세요.")
        self.refund_bank_account = bank_account.strip()
        self.account_holder = account_holder.strip()
        self.tenant_signature = signature.strip()
        if not self.tenant_checked_at:
            self.tenant_checked_at = now_iso()      # 동의하면 내역을 확인한 것으로 본다
        self.status = "AGREED"
        self.save(action="checkout_agreed_by_tenant", app_source=app_source)
        self._chat("tenant", self.tenant_name, f"[✍️ 정산 동의·반환 계좌 접수] 반환액 {self.refund_amount:,}원 · 예금주 {self.account_holder} · 계좌 {self._mask_account(bank_account)}. "
                   "계좌 전체는 임대인 화면에서만 보입니다. 임대인은 이 채팅창 위의 정산 카드(또는 [전출 정산] 창)에서 계좌를 확인하고 송금한 뒤 [송금 완료 표시]를 눌러 주세요.", app_source=app_source)
        return self

    def mark_paid(self, memo: str = "", app_source: str = "rent3_lessor") -> "CheckOutSettlement":
        if self.status != "AGREED":
            raise DomainError("임차인이 동의·계좌 입력을 마친 정산서만 송금 표시를 할 수 있습니다.")
        if self._norm(self.account_holder) != self._norm(self.tenant_name):
            raise DomainError("예금주가 계약자와 일치하지 않아 송금 표시를 할 수 없습니다.")
        due = (datetime.now() + timedelta(days=self.AUTO_CONFIRM_DAYS)).isoformat(timespec="seconds")
        self.status, self.paid_at, self.paid_memo, self.confirm_due = "PAID", now_iso(), (memo or "").strip(), due
        self.save(action="checkout_marked_paid", app_source=app_source)
        self._chat("lessor", "임대인", f"[💸 보증금 반환 송금 완료] {self.refund_amount:,}원을 {self.account_holder} 님 계좌({self._mask_account(self.refund_bank_account)})로 보냈습니다."
                   + (f" 메모: {self.paid_memo}" if self.paid_memo else "") + f" 입금을 확인하고 [받았습니다]를 눌러 주세요. {self.AUTO_CONFIRM_DAYS}일 안에 이의가 없으면 자동 확정됩니다.",
                   "받았습니다 / 못 받았습니다", f"/rent3_tenant/?tenant={self.id_tenant}&svc=checkout", app_source=app_source)
        return self

    def tenant_confirm_received(self, app_source: str = "rent3_tenant") -> "CheckOutSettlement":
        if self.status != "PAID":
            raise DomainError("송금이 표시된 정산서만 수취 확인을 할 수 있습니다.")
        return self.complete_refund_and_vacate(app_source=app_source)

    def tenant_dispute(self, note: str, app_source: str = "rent3_tenant") -> "CheckOutSettlement":
        if self.status not in ("CALCULATED", "AGREED", "PAID"):
            raise DomainError("이의를 제기할 수 있는 단계가 아닙니다.")
        note = (note or "").strip()
        if not note:
            raise DomainError("이의 내용을 적어 주세요.", hint="어느 항목이 왜 다른지(또는 입금을 못 받았는지) 적어 주세요.")
        was_paid = self.status == "PAID"
        self.status, self.dispute_note, self.disputed_at, self.confirm_due = "DISPUTED", note, now_iso(), ""
        self.save(action="checkout_disputed_by_tenant", app_source=app_source)
        self._chat("tenant", self.tenant_name, ("[⚠️ 입금 확인 불가] " if was_paid else "[⚠️ 정산 이의 제기] ") + note
                   + " — 채팅 또는 통화로 협의한 뒤 정산서를 수정해 다시 보내 주세요.", app_source=app_source)
        return self

    @classmethod
    def auto_confirm_due(cls, branch: str = "", tenant_id: str = "") -> int:
        """송금 표시 뒤 7일 동안 이의가 없으면 자동 확정(퇴실 처리). 조회 때 함께 실행한다. 처리한 건수를 돌려준다."""
        cls.purge_due(branch=branch, tenant_id=tenant_id)       # 퇴실 후 1개월이 지난 정산의 민감 정보 삭제
        now, n = now_iso(), 0
        for s in cls.find(status="PAID"):
            if (branch and s.branch != branch) or (tenant_id and s.id_tenant != tenant_id):
                continue
            if s.confirm_due and s.confirm_due <= now:
                s.complete_refund_and_vacate(app_source="rent3_checkout", auto=True)
                n += 1
        return n

    @staticmethod
    def _readonly_until(today: Any) -> str:
        """퇴실 후 조회 기간의 끝: 퇴실한 다음 해 2월 말."""
        y = today.year + 1
        return f"{y}-02-{calendar.monthrange(y, 2)[1]:02d}"

    def complete_refund_and_vacate(self, app_source: str = "rent3_lessor", auto: bool = False) -> "CheckOutSettlement":
        """임차인 수취 확인(또는 7일 자동 확정) 시: 계약 종료 + 임차인 퇴실 + 호실 공실 및 직거래 매물 자동 복귀.
        임차인은 로그인 수단(바로가기·기기·링크)만 정리되고, 다음 해 2월 말까지 조회 전용으로 로그인할 수 있다."""
        self.status = "COMPLETED"
        self.completed_at = now_iso()
        self.received_at = self.completed_at
        self.auto_confirmed = auto
        self.save(action="checkout_completed_auto" if auto else "checkout_completed", app_source=app_source)

        ct = Contract.get(self.id_contract)
        if ct:
            ct.terminate(app_source=app_source)

        tenant = Tenant.get(self.id_tenant)
        if tenant:
            from .. import connect as cn
            cn.purge_login_methods(tenant)
            tenant.status = "MOVED_OUT"
            tenant.current_room_id = ""
            tenant.readonly_until = self._readonly_until(date_today())
            tenant.save(action="tenant_moved_out", app_source=app_source)

        room = Room.get(self.id_room)
        if room:
            room.vacate(app_source=app_source)

        self.purge_sensitive(app_source=app_source)             # 보증금 반환이 확정됐으니 계좌·서명·메모를 지운다(금액·공제 내역은 남김)

        self._chat("system", "퇴실정산 안내", "[✅ 정산 완료] 보증금 반환이 확정되어 퇴실 처리되었습니다. "
                   + ("(7일 동안 이의가 없어 자동 확정) " if auto else "") + f"{tenant.readonly_until if tenant else ''}까지 로그인해서 연말정산 납부확인서·정산서를 조회할 수 있습니다.",
                   app_source=app_source)
        return self
