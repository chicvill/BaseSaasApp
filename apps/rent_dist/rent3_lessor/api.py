"""
apps/rent_dist/rent3_lessor/api.py — 임대인 전용 API 통합 라우터.

실제 엔드포인트는 도메인별 하위 라우터(`routers/*.py`)로 분리되어 있으며,
이 모듈은 공통 인증·지점 권한 가드(`api_headers`, `lessor_scope`) 아래에 모든 라우터를 통합한다.
"""
from fastapi import APIRouter, Depends

from apps.rent_dist._rent3_core.authz import api_headers, lessor_scope
from .routers._deps import APP_SRC, FRANCHISE_AI_MAX_CALLS
from .routers import comms, contracts, franchise, payments, rooms, tax

router = APIRouter(dependencies=[Depends(api_headers), Depends(lessor_scope)])

for _sub in (
    franchise.router,
    rooms.router,
    payments.router,
    contracts.router,
    tax.router,
    comms.router,
):
    router.include_router(_sub)

__all__ = ["router", "APP_SRC", "FRANCHISE_AI_MAX_CALLS"]
