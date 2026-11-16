"""apps/rent_dist/rent3/api.py — Rent3 포털 라우터.

예전의 레거시 라우터(운영·공개 앱·엔티티 CRUD·세무)는 새 앱으로 완전히 대체되어 제거되었다.
  - 임대인 장부·가맹 신청·매물 등록 : /rent3_lessor
  - 임차인 마이홈                 : /rent3_tenant
  - 계약(호출·점검·서명·체결)       : /rent3_contract
  - 관리자(가맹 심사·정산)         : /rent3_admin
이 라우터에는 포털(매물 노출·로그인·가입·문의·비밀번호 재설정·Data Pool 관제)만 남는다.
"""
from fastapi import APIRouter

from .routers.pool_hub import router as pool_hub_router

router = APIRouter(tags=["rent3"])
router.include_router(pool_hub_router)

__all__ = ["router", "pool_hub_router"]
