"""
apps/rent_dist/rent3_admin/api.py — 플랫폼 총괄 관리인 전용 통합 API 라우터.

실제 엔드포인트는 도메인별 하위 라우터(`routers/*.py`)로 분리되어 있으며,
이 모듈은 공통 `api_headers` 의존성 아래에 모든 하위 라우터를 통합한다.
"""
from fastapi import APIRouter, Depends

from apps.rent_dist._rent3_core.authz import api_headers
from .routers._deps import ADMIN_ONLY, APP_SRC
from .routers import accounts, fees, lessors, ops, support

router = APIRouter(dependencies=[Depends(api_headers)])

for _sub in (lessors.router, fees.router, accounts.router, ops.router, support.router):
    router.include_router(_sub)

__all__ = ["router", "APP_SRC", "ADMIN_ONLY"]
