"""
apps/_rent3_core/domains/ops_support.py — 1:1 대화(DirectChat), 하자보수(MaintenanceTicket), 전월세 신고(RtmsReport), 연결/이체 요청(ConnectRequest, TransferNotice).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, ClassVar, Dict
from uuid import uuid4

from pydantic import Field

from ..models import Contract, Outlay, date_today
from ..pool import PoolObj, now_iso


class DirectChat(PoolObj):
    """외부 알림톡 비용 0원 — 임대인·임차인 1:1 인앱 실시간 직통 채팅 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "1:1 대화"

    id: str = Field(default_factory=lambda: f"chat_{uuid4().hex[:8]}")
    branch: str = "main"
    id_room: str = ""
    room_no: str = ""
    sender_role: str = "tenant"              # lessor / tenant / system
    sender_name: str = ""
    message: str = ""
    quick_action_label: str = ""
    quick_action_url: str = ""
    is_read: bool = False
    # ── 계약방 대화(contract_chat.py): session_id 가 있는 글. 기존 1:1 대화는 모두 기본값 그대로다. ──
    session_id: str = ""                     # 공동계약 세션 id (없으면 입주 후 1:1 대화)
    tenant_id: str = ""                      # 예비 임차인 단계에서는 비어 있을 수 있다
    kind: str = "chat"                       # chat | question | proposal | change_request | system
    step: int = 0                            # 글을 올린 시점의 계약 단계(1~5)
    field: str = ""                          # 제안·수정 요청 대상 항목
    proposal: Dict[str, Any] = Field(default_factory=dict)   # {field, label, old, new}
    status: str = ""                         # 제안·수정 요청: open | accepted | declined | withdrawn
    resolved_by: str = ""
    resolved_at: str = ""
    read_by_lessor: bool = False
    read_by_tenant: bool = False

    def summary(self) -> str:                # 본문은 넣지 않는다(풀 이벤트 로그는 지워지지 않으므로)
        tag = f"계약방 {self.kind}" if self.session_id else "채팅"
        return f"{tag} [{self.room_no}호] {self.sender_name}"


class MaintenanceTicket(PoolObj):
    """5단계 정식 하자보수(A/S) 접수·배정·수리·정산 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "하자보수 티켓"

    id: str = Field(default_factory=lambda: f"as_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    category: str = "설비/배관"               # 에어컨 / 설비/배관 / 전기/조명 / 도어락 / 보일러
    title: str = ""
    description: str = ""
    before_photo: str = ""
    after_photo: str = ""
    status: str = "RECEIVED"                 # RECEIVED -> ASSIGNED -> IN_REPAIR -> COMPLETED -> CLOSED
    vendor_name: str = ""
    vendor_phone: str = ""
    visit_date: str = ""
    cost_burden: str = "LANDLORD"            # LANDLORD (자연마모 무상) / TENANT (과실 실비) / SHARED (50:50)
    repair_cost: int = 0
    resolution_note: str = ""

    def summary(self) -> str:
        return f"A/S [{self.room_no}호] {self.title} ({self.status})"

    def assign_vendor(
        self,
        vendor_name: str,
        vendor_phone: str,
        visit_date: str,
        app_source: str = "rent3_lessor",
    ) -> "MaintenanceTicket":
        self.vendor_name = vendor_name.strip()
        self.vendor_phone = vendor_phone.strip()
        self.visit_date = visit_date.strip()
        self.status = "ASSIGNED"
        self.save(action="ticket_vendor_assigned", app_source=app_source)

        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role="system",
            sender_name="시설관제 안내",
            message=f"[A/S 배정 완료] '{self.title}' 수리를 위해 {self.vendor_name}({self.vendor_phone}) 기사가 {self.visit_date} 방문 예정입니다.",
        ).save(action="chat_auto_sent", app_source=app_source)
        return self

    def complete_repair(
        self,
        repair_cost: int,
        cost_burden: str = "LANDLORD",
        resolution_note: str = "",
        after_photo: str = "",
        app_source: str = "rent3_lessor",
    ) -> "MaintenanceTicket":
        self.repair_cost = max(0, repair_cost)
        self.cost_burden = cost_burden
        self.resolution_note = resolution_note.strip()
        if after_photo:
            self.after_photo = after_photo
        self.status = "COMPLETED"
        self.save(action="ticket_repair_completed", app_source=app_source)

        # 임대인 부담 수선비는 공동 Data Pool outlays.json에 필요경비로 자동 등재
        landlord_share = (
            self.repair_cost if cost_burden == "LANDLORD"
            else (self.repair_cost // 2 if cost_burden == "SHARED" else 0)
        )
        if landlord_share > 0:
            Outlay(
                branch=self.branch,
                id_building=self.id_building,
                id_room=self.id_room,
                category="repair_maint",
                title=f"[{self.room_no}호 A/S] {self.title}",
                amount=landlord_share,
                vendor=self.vendor_name or "협력수리업체",
                memo=self.resolution_note,
            ).save(action="outlay_auto_recorded_from_as", app_source=app_source)

        DirectChat(
            branch=self.branch,
            id_room=self.id_room,
            room_no=self.room_no,
            sender_role="system",
            sender_name="시설관제 안내",
            message=f"[A/S 수리 완료] '{self.title}' 조치가 완료되었습니다. (비용: {self.repair_cost:,}원, 부담: {cost_burden})",
        ).save(action="chat_auto_sent", app_source=app_source)
        return self


class RtmsReport(PoolObj):
    """국토교통부 주택임대차 계약 신고(전월세 신고제) 및 대법원 확정일자 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "전월세 신고필증"

    id: str = Field(default_factory=lambda: f"rtms_{uuid4().hex[:8]}")
    branch: str = "main"
    id_contract: str = ""
    id_room: str = ""
    room_no: str = ""
    lessor_name: str = ""
    tenant_name: str = ""
    deposit: int = 0
    monthly_rent: int = 0
    contract_date: str = ""
    deadline_date: str = ""
    mandatory: bool = True                   # 보증금 6천만원 초과 또는 월세 30만원 초과 시 의무
    status: str = "FILED"                    # PENDING / FILED
    fixed_date_no: str = ""                  # FIX-YYYYMMDD-XXXXXX
    filed_at: str = ""

    def summary(self) -> str:
        return f"RTMS [{self.room_no}호] {self.status} ({self.fixed_date_no or '신고대기'})"

    def days_left(self) -> int:
        if not self.deadline_date:
            return 30
        try:
            d = date.fromisoformat(self.deadline_date[:10])
            return (d - date_today()).days
        except ValueError:
            return 30

    @classmethod
    def auto_file_for_contract(cls, ct: Contract, app_source: str = "rent3_contract") -> "RtmsReport":
        c_date = date_today()
        try:
            c_date = date.fromisoformat(ct.contract_date[:10])
        except ValueError:
            pass
        deadline = (c_date + timedelta(days=30)).isoformat()
        mandatory = (ct.deposit > 60_000_000) or (ct.monthly_rent > 300_000)
        fix_no = f"FIX-{c_date.strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"

        report = cls(
            branch=ct.branch,
            id_contract=ct.id,
            id_room=ct.id_room,
            room_no=ct.room_no,
            lessor_name=ct.lessor_name,
            tenant_name=ct.tenant_name,
            deposit=ct.deposit,
            monthly_rent=ct.monthly_rent,
            contract_date=ct.contract_date,
            deadline_date=deadline,
            mandatory=mandatory,
            status="FILED",
            fixed_date_no=fix_no,
            filed_at=now_iso(),
        ).save(action="rtms_auto_filed_and_fixed_date_granted", app_source=app_source)

        ct.fixed_date_no = fix_no
        ct.save(action="fixed_date_attached", app_source=app_source)
        return report


class ConnectRequest(PoolObj):
    """기존 임차인의 '내 페이지 연결 요청'. 건물·호실·성명·휴대폰 4가지가 장부와 같으면 접수하고, 임대인이 임차인과 직접 통화해 확인한 뒤 승인해야 입주 안내 링크(비밀번호·PIN 설정)가 나온다.
    요청한 쪽 브라우저만 아는 비밀값(secret)과 화면에 보인 4자리 확인번호(code)는 해시로만 보관한다."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "내 페이지 연결 요청"

    id: str = Field(default_factory=lambda: f"cnr_{uuid4().hex[:8]}")
    branch: str = "main"
    room_no: str = ""
    tenant_id: str = ""
    tenant_name: str = ""
    secret_hash: str = ""
    code_hash: str = ""
    code_fails: int = 0
    status: str = "PENDING"                  # PENDING | APPROVED | DELIVERED | REJECTED | EXPIRED
    expires_at: str = ""
    approved_at: str = ""
    handled_by: str = ""

    def summary(self) -> str:
        return f"연결 요청 {self.room_no}호 {self.tenant_name} ({self.status})"


class TransferNotice(PoolObj):
    """임차인의 '이체했어요' 알림. 돈이 들어온 것을 임대인이 확인(수납)하기 전에는 장부(Pay)에 아무것도 쓰지 않는다."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "이체 확인 요청"

    id: str = Field(default_factory=lambda: f"trn_{uuid4().hex[:8]}")
    branch: str = "main"
    room_no: str = ""
    tenant_id: str = ""
    tenant_name: str = ""
    amount: int = 0
    method: str = "TRANSFER"
    memo: str = ""
    status: str = "PENDING"                  # PENDING | CONFIRMED | REJECTED
    handled_at: str = ""
    handled_by: str = ""
    reject_reason: str = ""
    pay_id: str = ""

    def summary(self) -> str:
        return f"이체 확인 요청 {self.room_no}호 {self.amount:,}원 ({self.status})"
