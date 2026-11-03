"""
apps/rent5/app.py — 게이트웨이 어댑터. 실제 로직 · 화면은 저장소 루트의 rent5/ 패키지에 있다.

게이트웨이가 이 파일을 보고 rent5 를 자동 마운트한다.
    화면  /rent5/            (rent5/static)
    API   /api/rent5/...     (rent5.app.build_router)
데이터는 data/rent5_pool/ (환경변수 RENT5_DATA_DIR 로 교체). git · 도커 이미지에서 제외된다.
운영에서는 이 폴더를 이미지 밖 영구 저장소에 연결해야 재배포해도 회원 · 계약이 남는다.
"""
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from core import BaseSaaSApp
from rent5.app import STATIC_DIR, build_router, data_dir_from_env
from rent5.security import load_secret
from rent5.store import Store

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POOL = ROOT / "data" / "rent5_pool"


class Rent5App(BaseSaaSApp):
    app_id = "rent5"
    app_name = "Rent5 직거래 계약"
    icon = "🤝"
    category = "business"
    description = "중개사 없이 임대인과 임차인이 직접 계약합니다. 매물 → 신청 → 조건 제시 → 양쪽 서명 → 계약금 · 잔금."
    version = "0.1.0"
    features = ["회원 누구나 임대인 · 임차인", "매물 공개 · 계약 신청", "등기 확인 + 조건 제시", "SHA-256 지문 양쪽 서명",
                "계약서 인쇄 · PDF"]
    order = 45

    def __init__(self, base_dir: Optional[str] = None):
        super().__init__(base_dir)
        self.store: Optional[Store] = None

    def get_router(self) -> APIRouter:
        data_dir = data_dir_from_env(DEFAULT_POOL)
        self.store = Store(data_dir)
        return build_router(self.store, load_secret(data_dir))

    def get_static_dir(self) -> Optional[str]:
        return str(STATIC_DIR)

    def health_check(self) -> Dict[str, Any]:
        if self.store is None:
            return {}
        return {"users": len(self.store.all("users")), "listings": len(self.store.all("listings")),
                "contracts": len(self.store.all("contracts"))}


app_instance = Rent5App()
