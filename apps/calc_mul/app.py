"""apps/calc_mul/app.py — 고속 곱셈기 BaseSaaSApp 정의서."""
from fastapi import APIRouter
from core import BaseSaaSApp
from .engine import MulEngine

router = APIRouter()
engine = MulEngine()


@router.get("/compute")
def compute(a: float = 3.0, b: float = 5.0):
    return {"success": True, "app_id": "calc_mul", "data": engine.execute(a, b)}


@router.get("/status")
def status():
    return {"success": True, "app_id": "calc_mul", "status": "HEALTHY"}


class CalcMulApp(BaseSaaSApp):
    app_id = "calc_mul"
    app_name = "고속 곱셈기"
    icon = "✖️"
    category = "utility"
    description = "두 숫자의 곱을 계산하는 (예: 3 * 5 = 15) 독립 연산 마이크로 앱"
    version = "1.0.0"
    features = ["실시간 곱셈 연산", "대규모 승수 처리", "독립 객체 플러그인"]
    order = 73

    def get_router(self) -> APIRouter:
        return router


app_instance = CalcMulApp()
