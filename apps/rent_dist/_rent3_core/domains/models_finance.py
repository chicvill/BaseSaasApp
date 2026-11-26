"""
apps/_rent3_core/domains/models_finance.py — 수납(Pay), 지출(Outlay), 세금계산서 발행이력(TaxInvoiceRecord) 도메인 객체.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from ..pool import PoolObj
from .models_utils import (
    EXPENSE_CATEGORIES,
    VAT_NON_DEDUCTIBLE_CATEGORIES,
    current_ym,
    date_today,
)


class Pay(PoolObj):
    """수납 기록 객체 (월세·관리비·부가세·계약금)."""
    collection: ClassVar[str] = "payments"
    label: ClassVar[str] = "수납"

    id: str = Field(default_factory=lambda: f"pay_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_contract: str = ""
    id_tenant: str = ""
    payer_name: str = ""
    billing_month: str = Field(default_factory=current_ym)
    paid_date: str = Field(default_factory=lambda: date_today().isoformat())
    rent_amount: int = 0
    maintenance_amount: int = 0
    vat_amount: int = 0
    total_amount: int = 0
    payment_kind: str = "MONTHLY_RENT"       # MONTHLY_RENT / CONTRACT_DEPOSIT
    method: str = "TRANSFER"                 # BANK_SMS / CARD / KAKAOPAY / TRANSFER
    cash_receipt_no: str = ""                # 실제 국세청 현금영수증 발급 시에만 승인번호 기록 (임의 생성 금지)
    is_carryover: bool = False               # 장부이관 등 과년도 이월 정산 수납 여부
    allocated_months: List[Dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str = ""
    raw_sms: str = ""
    deposit_group_id: str = ""               # 한 번의 입금이 여러 달로 나뉘어 저장된 건들을 묶는 표시(통장·문자 1건과 맞춰 보기 위함)
    status: str = "ACTIVE"                   # ACTIVE / VOIDED(무효 처리: 기록은 남기되 원장·세금·중복 점검 계산에서는 빠진다)
    void_reason: str = ""
    voided_at: str = ""
    voided_by: str = ""

    # 무효(VOIDED) 건은 기본 조회에서 빠진다 — 원장·세무·중복 점검·문자 중복 차단이 모두 같은 규칙을 따르게 하려는 것(include_voided=True 로만 보인다)
    @classmethod
    def find(cls, include_voided: bool = False, **where: Any) -> List["Pay"]:
        rows = super().find(**where)
        return rows if include_voided else [r for r in rows if r.status != "VOIDED"]

    @classmethod
    def get(cls, obj_id: str, include_voided: bool = False) -> Optional["Pay"]:
        o = super().get(obj_id)
        if o is not None and o.status == "VOIDED" and not include_voided:
            return None
        return o

    def summary(self) -> str:
        return f"수납 {self.room_no}호 {self.payer_name} {self.total_amount:,}원 ({self.billing_month})"


class Outlay(PoolObj):
    """건물 공용 운영비 및 호실 수선비 지출 객체."""
    collection: ClassVar[str] = "outlays"
    label: ClassVar[str] = "지출"

    id: str = Field(default_factory=lambda: f"out_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    category: str = "cleaning"
    title: str = ""
    expense_date: str = Field(default_factory=lambda: date_today().isoformat())
    amount: int = 0
    vendor: str = ""
    vendor_business_number: str = ""         # 거래처 사업자등록번호
    vendor_tax_type: str = "GENERAL"         # GENERAL(일반과세자) / SIMPLIFIED(간이과세자-공제불가) / EXEMPT(면세·비사업자-공제불가)
    memo: str = ""
    vat_deductible: Optional[bool] = None    # True=매입세액 공제, False=불공제(경조비·면세 보험료·간이영수증 등), None=카테고리·증빙 규칙
    evidence_type: str = "TAX_INVOICE"       # TAX_INVOICE(세금계산서) / CARD(사업용카드·현금영수증) / EXEMPT_RECEIPT(면세·간이영수증) / NONE(증빙없음)
    reference_only: bool = False             # True=관리비 산출근거용 참고 항목(세무 경비·부가세 집계에 넣지 않는다)
    usage_scope: str = "COMMON"              # COMMON(공통매입-과세비율안분) / TAXABLE_ONLY(과세호실전용-100%) / EXEMPT_ONLY(주거면세전용-0%)

    @property
    def category_label(self) -> str:
        return EXPENSE_CATEGORIES.get(self.category, self.category)

    @property
    def is_vat_deductible(self) -> bool:
        if self.vat_deductible is False:
            return False
        if self.evidence_type not in ("TAX_INVOICE", "CARD"):
            return False
        if self.vendor_tax_type in ("SIMPLIFIED", "EXEMPT"):
            return False
        if self.usage_scope == "EXEMPT_ONLY":
            return False
        if self.vat_deductible is True:
            return True
        return self.category not in VAT_NON_DEDUCTIBLE_CATEGORIES

    @property
    def input_vat(self) -> int:
        """지출 합계액(부가세 포함) 기준 전액 매입세액 (안분 전)."""
        if not self.is_vat_deductible or self.amount <= 0:
            return 0
        return round(self.amount - (self.amount / 1.1))

    def effective_input_vat(self, taxable_ratio: float = 1.0) -> int:
        """과세·면세 겸영 건물의 공통매입세액 안분(taxable_ratio)을 반영한 실제 공제 가능 매입세액."""
        base = self.input_vat
        if base <= 0:
            return 0
        if self.usage_scope == "TAXABLE_ONLY":
            return base
        if self.usage_scope == "EXEMPT_ONLY":
            return 0
        clamped = max(0.0, min(1.0, taxable_ratio))
        return round(base * clamped)

    @property
    def supply_amount(self) -> int:
        return self.amount - self.input_vat

    def summary(self) -> str:
        return f"지출 [{self.category_label}] {self.title} {self.amount:,}원"


class TaxInvoiceRecord(PoolObj):
    """청구월(약정 지급일) 기준 월 1건 전자세금계산서 발행 상태·국세청 승인번호·수정/취소 이력 객체."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "세금계산서 발행이력"

    id: str = Field(default_factory=lambda: f"tinv_{uuid4().hex[:8]}")
    branch: str = "main"
    id_contract: str = ""
    room_no: str = ""
    billing_month: str = ""                  # YYYY-MM (청구 귀속월)
    write_date: str = ""                     # YYYYMMDD (약정 지급일 = 공급시기)
    status: str = "DRAFT"                    # DRAFT(미발행) / ISSUED(발행완료) / MODIFIED(수정발행) / CANCELLED(발행취소)
    nts_confirm_no: str = ""                 # 국세청 24자리 승인번호 (예: 20261001-41000000-12345678)
    issued_at: str = ""
    supply_amount: int = 0
    tax_amount: int = 0
    total_amount: int = 0
    buyer_business_number: str = ""
    buyer_company_name: str = ""
    buyer_ceo_name: str = ""
    buyer_email: str = ""
    memo: str = ""
    history: List[Dict[str, Any]] = Field(default_factory=list)

    def summary(self) -> str:
        return f"세금계산서 [{self.branch}:{self.room_no}호 {self.billing_month}] {self.status} ({self.supply_amount:,}원)"
