"""
apps/rent3_contract/app.py — 임대인·임차인이 함께 보며 작성하는 공동 임대계약 전용 앱 (`BaseSaaSApp`).

불변의 BaseSaasApp 코어 위에서 제로터치로 마운트되며,
계약 작성·동의·전자서명·체결 전 과정이 `data/rent3_pool/*.json`에 실시간 반영된다.
"""
from fastapi import APIRouter
from core import BaseSaaSApp
from apps._rent3_core import CoSignSession, Contract, ensure_pool_seeded
from .api import router


class Rent3ContractApp(BaseSaaSApp):
    app_id = "rent3_contract"
    app_name = "스마트 공동 임대계약 (Rent3 Contract)"
    icon = "🤝"
    category = "business"
    description = "임대인과 임차인이 같은 화면을 함께 보며 등기확인·옵션점검·조건작성·전자서명·확정일자를 완결하는 중립 공동 계약 앱."
    version = "3.1.0"
    features = [
        "6자리 코드 양방향 실시간 미러링",
        "조건 수정 시 자동 재동의 잠금(Revision Lock)",
        "자필 캔버스 전자서명 & SHA-256 봉인",
        "체결 즉시 공동 JSON Data Pool 일괄 반영",
        "5% 상한 검증 재계약 & A4 계약서 인쇄",
    ]
    subdomains = ["contract.rent3.localhost"]
    order = 15

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        ensure_pool_seeded()

    def health_check(self):
        return {
            "engine": "JSON Data Pool (cosign_sessions.json -> contracts.json)",
            "active_sessions": CoSignSession.count(status="IN_PROGRESS"),
            "sealed_contracts": Contract.count(),
        }


app_instance = Rent3ContractApp()

