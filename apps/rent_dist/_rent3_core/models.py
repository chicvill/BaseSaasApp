"""
apps/_rent3_core/models.py — Rent3 전 구성 요소 도메인 객체 파사드 (`domains/models_*.py` 통합 재노출).

모든 구성 요소가 독립 객체(`PoolObj`)이며, `.save()` / `.delete()` 호출 즉시
`data/rent3_pool/*.json` 공동 Data Pool 파일이 실시간 갱신된다.
"""
from __future__ import annotations

from .domains.models_contract import Contract
from .domains.models_finance import Outlay, Pay, TaxInvoiceRecord
from .domains.models_property import Building, Room
from .domains.models_users import Admin, Lessor, Tenant
from .domains.models_utils import (
    BRANCH_ALIASES,
    EXPENSE_CATEGORIES,
    VAT_NON_DEDUCTIBLE_CATEGORIES,
    add_months_str,
    current_ym,
    date_today,
    index_to_ym,
    normalize_branch_id,
    parse_ym,
    validate_email_format,
    validate_kr_business_number,
    ym_index,
)

__all__ = [
    "BRANCH_ALIASES",
    "EXPENSE_CATEGORIES",
    "VAT_NON_DEDUCTIBLE_CATEGORIES",
    "normalize_branch_id",
    "current_ym",
    "date_today",
    "parse_ym",
    "ym_index",
    "index_to_ym",
    "add_months_str",
    "validate_kr_business_number",
    "validate_email_format",
    "Admin",
    "Lessor",
    "Tenant",
    "Building",
    "Room",
    "Contract",
    "Pay",
    "Outlay",
    "TaxInvoiceRecord",
]
