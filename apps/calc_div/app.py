"""apps/calc_div/app.py — 안전 나눗셈기 BaseSaaSApp 정의서."""
from fastapi import APIRouter, HTTPException
from core import BaseSaaSApp
from .engine import DivEngine

router = APIRouter()
engine = DivEngine()


@router.get("/compute")
def compute(a: float = 15.0, b: float = 3.0):
    try:
        return {"success": True, "app_id": "calc_div", "data": engine.execute(a, b)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
def status():
    return {"success": True, "app_id": "calc_div", "status": "HEALTHY"}


class CalcDivApp(BaseSaaSApp):
    app_id = "calc_div"
    app_name = "안전 나눗셈기"
    icon = "➗"
    category = "utility"
    description = "두 숫자를 나누는 (예: 15 / 3 = 5.0) 독립 연산 마이크로 앱"
    version = "1.0.0"
    features = ["실시간 나눗셈 연산", "ZeroDivision 예외 처리", "독립 객체 플러그인"]
    order = 74

    def get_router(self) -> APIRouter:
        return router


app_instance = CalcDivApp()
