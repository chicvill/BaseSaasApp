"""
apps/rent3_lessor/app.py — 건물주/임대인 전용 자산·FIFO 수납·세무·시설 관제 앱 (`BaseSaaSApp`).

공동 JSON Data Pool(`data/rent3_pool/*.json`)을 실시간으로 읽고 쓰며,
rent2의 객체지향 수납 규칙(후불 입주월 비과세, 가짜 체납 차단, FIFO 순차 상계, 문자 파싱)을 100% 수행한다.
"""
from fastapi import APIRouter
from core import BaseSaaSApp
from apps._rent3_core import Building, Pay, Room, ensure_pool_seeded
from .api import router


class Rent3LessorApp(BaseSaaSApp):
    app_id = "rent3_lessor"
    app_name = "임대인 자산·수납 관제 (Rent3 Lessor)"
    icon = "🏢"
    category = "business"
    description = "건물주 전용 호실·공실 관리, 은행 입금문자 파싱 및 FIFO 월세 수납, 공용경비·종소세 결산, A/S·퇴실 정산 솔루션."
    version = "3.1.0"
    features = [
        "rent2 계승 FIFO 순차 상계 & 가짜체납 차단",
        "은행 입금문자 붙여넣기 자동 파싱 수납",
        "원클릭 공실 게시 & 공동 계약방 호출",
        "도어락 원격 변경 · 주차 단속 · A/S 배정",
        "8대 공용경비 NOI 결산 & 5월 종소세 CSV",
    ]
    subdomains = ["lessor.rent3.localhost"]
    order = 16

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        ensure_pool_seeded()

    def health_check(self):
        return {
            "engine": "JSON Data Pool + Rent2 OOP FIFO Engine",
            "buildings": Building.count(),
            "rooms": Room.count(),
            "payments": Pay.count(),
        }


app_instance = Rent3LessorApp()

