"""
apps/rent_dist/rent3_contract/api.py — 공동 임대계약 전용 통합 API 라우터.

실제 엔드포인트는 도메인별 하위 라우터(`routers/*.py`)로 분리되어 있으며,
이 모듈은 공통 인증·계약방 권한 가드(`api_headers`, `contract_scope`) 아래에 모든 하위 라우터를 통합한다.
"""
from fastapi import APIRouter, Depends

from apps.rent_dist._rent3_core.authz import api_headers, contract_scope
from .routers._deps import APP_SRC, REGISTRY_AI_MAX_CALLS
from .routers.steps import terms_lock_enabled
from .routers import chat, registry, sessions, steps

router = APIRouter(dependencies=[Depends(api_headers), Depends(contract_scope)])

for _sub in (sessions.router, chat.router, registry.router, steps.router):
    router.include_router(_sub)

__all__ = ["router", "APP_SRC", "REGISTRY_AI_MAX_CALLS", "terms_lock_enabled"]
