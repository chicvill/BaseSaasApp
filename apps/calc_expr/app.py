"""apps/calc_expr/app.py — 수식 괄호 연산기 BaseSaaSApp 정의서."""
from fastapi import APIRouter, HTTPException
from core import BaseSaaSApp
from .engine import ExprEngine

router = APIRouter()
engine = ExprEngine()


@router.get("/compute")
def compute(expr: str = "(3 + 5) * 2"):
    try:
        return {"success": True, "app_id": "calc_expr", "data": engine.evaluate(expr)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
def status():
    return {"success": True, "app_id": "calc_expr", "status": "HEALTHY"}


class CalcExprApp(BaseSaaSApp):
    app_id = "calc_expr"
    app_name = "수식 괄호 연산기"
    icon = "🧮"
    category = "utility"
    description = "괄호 복합 수식을 안전하게 계산하는 (예: (3 + 5) * 2 = 16) 독립 연산 마이크로 앱"
    version = "1.0.0"
    features = ["괄호 우선순위 파싱", "AST 안전 연산", "독립 객체 플러그인"]
    order = 75

    def get_router(self) -> APIRouter:
        return router


app_instance = CalcExprApp()
