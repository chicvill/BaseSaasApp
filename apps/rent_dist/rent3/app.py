"""apps/rent3/app.py — 차세대 비대면 직거래 스마트임차 (Rent3) 앱 정의서.

BaseSaasApp 표준 규격 상속 및 게이트웨이 제로터치 마운트.
비즈니스 및 도메인 운영 규칙: .agents/skills/bass-rent3-ops/SKILL.md
"""
import os
from fastapi import APIRouter
from core import BaseSaaSApp
from .api import router


class Rent3App(BaseSaaSApp):
    app_id = "rent3"
    app_name = "차세대 비대면 스마트임차 (Rent3)"
    icon = "🏡"
    category = "business"
    description = "공실 광고부터 비대면 전자계약, 첫 결제, 입퇴실 점검 및 세입자 소통(민원/수리)까지 올인원 직거래 임대차 관리 솔루션."
    version = "3.0.0"
    features = [
        "온라인 비대면 직거래 계약",
        "첫 결제 및 모바일 전자서명",
        "공인중개사법 안전 SaaS 구조",
        "세입자 전용 라운지 (민원/수리/수납)",
        "디지털 입퇴실 시설 점검표",
        "rent2 FIFO 수납 엔진 100% 계승"
    ]
    subdomains = ["rent3.localhost", "directrent.localhost"]
    order = 19

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        """기동 시 공동 JSON Data Pool 을 확인·생성한다."""
        from apps.rent_dist._rent3_core import ensure_pool_seeded
        ensure_pool_seeded()

    def health_check(self):
        return {
            "status": "HEALTHY",
            "app_id": self.app_id,
            "engine": "Rent3 Direct Lifecycle + Rent2 FIFO Core",
            "version": self.version
        }


app_instance = Rent3App()
