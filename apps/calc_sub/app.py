"""apps/calc_sub/app.py — 정밀 뺄셈기 BaseSaaSApp 정의서."""
from fastapi import APIRouter
from core import BaseSaaSApp
from .engine import SubEngine

router = APIRouter()
engine = SubEngine()


@router.get("/compute")
def compute(a: float = 8.0, b: float = 3.0):
    return {"success": True, "app_id": "calc_sub", "data": engine.execute(a, b)}


@router.get("/status")
def status():
    return {"success": True, "app_id": "calc_sub", "status": "HEALTHY"}


class CalcSubApp(BaseSaaSApp):
    app_id = "calc_sub"
    app_name = "정밀 뺄셈기"
    icon = "➖"
    category = "utility"
    description = "두 숫자의 차를 계산하는 (예: 8 - 3 = 5) 독립 연산 마이크로 앱"
    version = "1.0.0"
    features = ["실시간 뺄셈 연산", "음수 결과 지원", "독립 객체 플러그인"]
    order = 72

    def get_router(self) -> APIRouter:
        return router


app_instance = CalcSubApp()
