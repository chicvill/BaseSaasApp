"""
apps/rent3_admin/app.py — 플랫폼 총괄 관리인 전용 가맹·사용료·전사 포트폴리오 관제 앱 (`BaseSaaSApp`).

공인중개사법 준수(중개수수료 0원, 호실 관리 월 SaaS 솔루션 이용료 과금) 원칙에 따라
임대인 가맹 승인, 사용료 정산, 전 지점 가동률/세무 집계, RTMS 30일 신고 감시 및 공동 JSON Data Pool 마스터 감사를 수행한다.
"""
from fastapi import APIRouter
from core import BaseSaaSApp
from apps._rent3_core import Building, Lessor, RtmsReport, ensure_pool_seeded
from .api import router


class Rent3AdminApp(BaseSaaSApp):
    app_id = "rent3_admin"
    app_name = "플랫폼 총괄 관리인 (Rent3 Admin)"
    icon = "👑"
    category = "business"
    description = "SaaS 총괄 관리인 전용 임대인 가맹 심사, 월 앱 사용료 정산, 전 지점 포트폴리오 NOI 관제 및 RTMS 신고 마감 감시 센터."
    version = "3.1.0"
    features = [
        "임대인 가맹 승인 & 월 SaaS 사용료 정산(복비 0원)",
        "전 지점 건물·호실 포트폴리오 가동률/NOI 관제",
        "국토부 RTMS 전월세 신고 30일 마감 D-Day 감시",
        "공동 JSON Data Pool 전체 객체 탐색기",
        "실시간 _events.jsonl 변경 감사 타임라인",
    ]
    subdomains = ["admin.rent3.localhost"]
    order = 18

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        ensure_pool_seeded()

    def health_check(self):
        return {
            "engine": "JSON Data Pool Master Governance",
            "lessors": Lessor.count(),
            "buildings": Building.count(),
            "rtms_reports": RtmsReport.count(),
        }


app_instance = Rent3AdminApp()

