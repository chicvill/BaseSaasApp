"""
apps/rent_dist/rent3/routers/pool_hub.py — 통합관리 공동 JSON Data Pool · OpenRent 포털 통합 라우터.

실제 엔드포인트는 도메인별 모듈(`hub_public.py`, `hub_auth.py`, `hub_access.py`)로 분리되어 있으며,
이 모듈은 `/pool` 프리픽스와 `api_headers` 의존성 아래에 모든 하위 라우터를 통합한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.rent_dist._rent3_core.authz import api_headers
from ._hub_deps import ADMIN_ONLY, demo_login_allowed
from . import hub_access, hub_auth, hub_public

router = APIRouter(prefix="/pool", tags=["rent3-pool-hub"], dependencies=[Depends(api_headers)])

for _sub in (hub_public.router, hub_auth.router, hub_access.router):
    router.include_router(_sub)

__all__ = ["router", "ADMIN_ONLY", "demo_login_allowed"]
