"""
apps/_rent3_core/ops.py — 운영 도메인 객체 파사드 (`domains/ops_*.py` 통합 재노출).

모든 객체는 `data/rent3_pool/ops.json`에 실시간으로 저장·업데이트된다.
"""
from __future__ import annotations

from .domains.ops_checkout import CheckOutSettlement
from .domains.ops_expiry import ExpiryRenewalCheck
from .domains.ops_notice import RentBillingNotice
from .domains.ops_support import (
    ConnectRequest,
    DirectChat,
    MaintenanceTicket,
    RtmsReport,
    TransferNotice,
)

__all__ = [
    "DirectChat",
    "MaintenanceTicket",
    "CheckOutSettlement",
    "RtmsReport",
    "ExpiryRenewalCheck",
    "RentBillingNotice",
    "ConnectRequest",
    "TransferNotice",
]
