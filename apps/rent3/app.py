"""apps/rent3/app.py — 차세대 비대면 직거래 스마트임차 (Rent3) 앱 정의서.

BaseSaasApp 표준 규격 상속 및 게이트웨이 제로터치 마운트.
비즈니스 및 도메인 운영 규칙: .agents/skills/bass-rent3-ops/SKILL.md
"""
import os
from fastapi import APIRouter
from core import BaseSaaSApp
from .api import router
from .domain import RentalListing


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
        """기동 시 기본 공개 매물 시드 데이터 및 공동 JSON Data Pool 확인·생성."""
        from apps._rent3_core import ensure_pool_seeded
        ensure_pool_seeded()
        listings = RentalListing.all()
        if not listings:
            # 시크빌 203호 샘플 매물
            RentalListing(
                id="listing_chic_203",
                branch="main",
                room_id="203",
                title="[시크빌 본점] 햇살 가득 남향 풀옵션 분리형 원룸 (즉시입주)",
                deposit=5000000,
                monthly_rent=520000,
                maintenance_fee=50000,
                fee_includes=["초고속 인터넷", "공용전기/청소", "수도세"],
                options=["천장형 시스템에어컨", "드럼세탁기", "빌트인냉장고", "전자레인지", "디지털도어락"],
                available_from="2026-11-01",
                floor=2,
                area_sqm=23.5,
                images=["https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800"]
            ).save("seeded")

            # 선호아트빌 302호 샘플 매물
            RentalListing(
                id="listing_sunho_302",
                branch="sunho",
                room_id="302",
                title="[선호아트빌] 역세권 조용한 주택가 깔끔한 원룸 (보증금 조절 가능)",
                deposit=3000000,
                monthly_rent=450000,
                maintenance_fee=40000,
                fee_includes=["인터넷", "유선방송", "수도세"],
                options=["벽걸이에어컨", "통돌이세탁기", "냉장고", "가스쿡탑"],
                available_from="즉시입주",
                floor=3,
                area_sqm=19.8,
                images=["https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800"]
            ).save("seeded")

    def health_check(self):
        return {
            "status": "HEALTHY",
            "app_id": self.app_id,
            "engine": "Rent3 Direct Lifecycle + Rent2 FIFO Core",
            "version": self.version
        }


app_instance = Rent3App()
