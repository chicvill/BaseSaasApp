"""
apps/rent3/routers/pool_hub.py — 통합관리용 공동 JSON Data Pool 라이브 관제 라우터.
"""
from fastapi import APIRouter
from apps._rent3_core import POOL_COLLECTIONS, ensure_pool_seeded, pool

router = APIRouter(prefix="/pool", tags=["rent3-pool-hub"])


@router.get("/overview")
def pool_overview():
    ensure_pool_seeded()
    p = pool()
    return {
        "success": True,
        "meta": p.overview(),
        "apps": [
            {"app_id": "rent3", "name": "통합관리 Data Pool 허브", "icon": "🌐", "url": "/rent3/pool.html"},
            {"app_id": "rent3_admin", "name": "관리인 전용 앱", "icon": "👑", "url": "/rent3_admin/"},
            {"app_id": "rent3_lessor", "name": "임대인 전용 앱", "icon": "🏢", "url": "/rent3_lessor/"},
            {"app_id": "rent3_tenant", "name": "임차인 전용 앱", "icon": "🔑", "url": "/rent3_tenant/"},
            {"app_id": "rent3_contract", "name": "공동 임대계약 전용 앱", "icon": "🤝", "url": "/rent3_contract/"},
        ],
        "recent_events": p.recent_events(limit=30),
    }


@router.get("/files/{collection}")
def pool_file_raw(collection: str):
    ensure_pool_seeded()
    return {
        "success": True,
        "collections": list(POOL_COLLECTIONS),
        **pool().raw_collection(collection),
    }

