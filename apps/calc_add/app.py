"""apps/calc_add/app.py — 스마트 덧셈기 BaseSaaSApp 정의서."""
from fastapi import APIRouter
from core import BaseSaaSApp
from .engine import AddEngine

router = APIRouter()
engine = AddEngine()


@router.get("/compute")
def compute(a: float = 3.0, b: float = 5.0):
    return {"success": True, "app_id": "calc_add", "data": engine.execute(a, b)}


@router.get("/status")
def status():
    return {"success": True, "app_id": "calc_add", "status": "HEALTHY"}


class CalcAddApp(BaseSaaSApp):
    app_id = "calc_add"
    app_name = "스마트 덧셈기"
    icon = "➕"
    category = "utility"
    description = "두 숫자를 더하는 (예: 3 + 5 = 8) 독립 연산 마이크로 앱"
    version = "1.0.0"
    features = ["실시간 덧셈 연산", "수식 스트링 포맷팅", "독립 객체 플러그인"]
    order = 71

    def get_router(self) -> APIRouter:
        return router


app_instance = CalcAddApp()
