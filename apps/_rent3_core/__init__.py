"""
apps/_rent3_core — Rent3 5대 분리 앱이 공동으로 사용하는 객체지향 도메인 & 실시간 JSON Data Pool 커널.

언더스코어(`_`)로 시작하는 패키지이므로 `core/loader.py`의 `SaaSAppManager`가 앱 마운트 대상에서 자동 제외하며,
불변의 BaseSaasApp 코어를 1줄도 수정하지 않고 모든 분리 앱이 동일한 JSON Data Pool을 공유한다.
"""
from .billing import FifoPaymentEngine, SmsDepositParser, TaxAndCertificateService
from .cosign import CoSignSession
from .models import (
    Admin,
    Building,
    Contract,
    EXPENSE_CATEGORIES,
    Lessor,
    Outlay,
    Pay,
    Room,
    Tenant,
    current_ym,
    date_today,
    normalize_branch_id,
)
from .ops import CheckOutSettlement, DirectChat, MaintenanceTicket, RtmsReport
from .pool import JsonDataPool, POOL_COLLECTIONS, POOL_REGISTRY, PoolObj, pool, use_pool_dir
from .seed import ensure_pool_seeded

__all__ = [
    "JsonDataPool",
    "POOL_COLLECTIONS",
    "POOL_REGISTRY",
    "PoolObj",
    "pool",
    "use_pool_dir",
    "ensure_pool_seeded",
    "Admin",
    "Lessor",
    "Tenant",
    "Building",
    "Room",
    "Contract",
    "Pay",
    "Outlay",
    "EXPENSE_CATEGORIES",
    "normalize_branch_id",
    "current_ym",
    "date_today",
    "SmsDepositParser",
    "FifoPaymentEngine",
    "TaxAndCertificateService",
    "CoSignSession",
    "DirectChat",
    "MaintenanceTicket",
    "CheckOutSettlement",
    "RtmsReport",
]

