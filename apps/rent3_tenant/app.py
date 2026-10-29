"""
apps/rent3_tenant/app.py — 예비 임차인 & 입주 세입자 전용 매물 탐색 · 마이홈 라운지 앱 (`BaseSaaSApp`).

공동 JSON Data Pool(`data/rent3_pool/*.json`)을 실시간으로 참조하여
공실 매물 탐색, 공동 계약방 합류, 월세 납부/영수증, 도어락/주차 등록, A/S 접수, 17% 세액공제 증명서 발급을 수행한다.
"""
from fastapi import APIRouter
from core import BaseSaaSApp
from apps._rent3_core import Room, Tenant, ensure_pool_seeded
from .api import router


class Rent3TenantApp(BaseSaaSApp):
    app_id = "rent3_tenant"
    app_name = "임차인 매물·마이홈 (Rent3 Tenant)"
    icon = "🔑"
    category = "business"
    description = "복비 0원 직거래 공실 탐색부터 마이홈 월세 납부, 도어락·주차 등록, A/S 신청, 연말정산 17% 세액공제 증명서까지 세입자 전용 앱."
    version = "3.1.0"
    features = [
        "중개수수료 0원 직거래 공실 쇼케이스",
        "실시간 월세 납부 & 임대인 FIFO 장부 동기화",
        "디지털 도어락·공동현관·차량 주차 등록",
        "1:1 직통 채팅 & A/S 하자보수 접수",
        "조세특례제한법 17% 월세 세액공제 증명서 인쇄",
    ]
    subdomains = ["tenant.rent3.localhost"]
    order = 17

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        ensure_pool_seeded()

    def health_check(self):
        vacant_published = sum(1 for r in Room.find(is_published=True) if not r.is_occupied)
        return {
            "engine": "JSON Data Pool (rooms.json / contracts.json / payments.json)",
            "published_vacant_rooms": vacant_published,
            "active_tenants": Tenant.count(status="ACTIVE"),
        }


app_instance = Rent3TenantApp()

