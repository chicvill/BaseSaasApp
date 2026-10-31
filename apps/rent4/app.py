"""
apps/rent4/app.py — 앱 정의서. 게이트웨이는 이 파일 하나만 보고 앱을 자동 마운트한다.
"""
from fastapi import APIRouter

from core import BaseSaaSApp

from .backend import api, sms
from .backend.pool import pool


class Rent4App(BaseSaaSApp):
    app_id = "rent4"
    app_name = "Rent4 입금 문자 수납부"
    icon = "🏠"
    category = "business"
    description = "임대인은 은행 입금 문자를 붙여 넣어 수납을 쌓고, 임차인은 반영된 입금 결과를 확인합니다."
    version = "0.1.0"
    features = ["관리인·임대인·임차인 역할별 로그인", "입금 문자 자동 분석·호실 매칭", "FIFO 귀속월 배정",
                "Excel 수납부 가져오기", "임차인 입금 조회"]
    order = 40

    def get_router(self) -> APIRouter:
        return api.router

    def on_startup(self) -> None:
        api.startup()      # 건물 목록 보장 + 거주 호실마다 임차인 계정 맞추기
        sms.start_polling()  # 텔레그램 봇을 쓰면 봇 메시지 받기

    def health_check(self):
        return {"pool_dir": pool().directory, **api.stats()}


app_instance = Rent4App()
