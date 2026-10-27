"""
apps/tax/app.py — 스마트 부동산 세무 (Tax) 앱 정의서.
게이트웨이가 이 파일 하나만 보고 앱을 자동으로 마운트합니다.
"""
from fastapi import APIRouter
from core import BaseSaaSApp
from .backend import domain
from .backend.api import router


class TaxApp(BaseSaaSApp):
    app_id = "tax"
    app_name = "스마트 부동산 세무 (Tax)"
    icon = "⚖️"
    category = "business"
    description = "부동산 임대사업자 부가세 자동 집계, 종부세 산출 및 5역할 협업 세무 어드바이저"
    version = "1.0.0"
    features = ["부가세 자동 집계", "종부세 정밀 시뮬레이터", "5역할 AI 협업 거버넌스", "rent2 원클릭 동기화"]
    subdomains = ["tax.localhost"]
    order = 25

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        domain.seed()

    def health_check(self):
        return {
            "status": "HEALTHY",
            "vat_transactions": domain.VatTransaction.count(),
            "property_assets": domain.PropertyAsset.count()
        }


app_instance = TaxApp()
