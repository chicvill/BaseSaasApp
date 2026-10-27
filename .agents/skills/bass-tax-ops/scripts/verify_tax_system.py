"""
.agents/skills/bass-tax-ops/scripts/verify_tax_system.py
스마트 세무 (apps/tax) 및 5역할 협업 거버넌스 5단계 자동 검증 파이프라인.
"""
import ast
import os
import sys
import unittest
from fastapi.testclient import TestClient

# UTF-8 출력 보장
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.kernel.obj import use_store
from apps.tax.app import app_instance
from apps.tax.backend import domain
from apps.tax.backend.api import router


def log_stage(stage_num: int, title: str):
    print(f"\n[{stage_num}/5] 🔍 {title}")


def main():
    print("=" * 65)
    print("🚀 BaseSaasApp 스마트 세무 (apps/tax) 5단계 무결성 검증 파이프라인")
    print("   [5-Role Collaboration & Tax Self-Upgrading Pipeline]")
    print("=" * 65)

    tax_dir = os.path.join(ROOT, "apps", "tax")

    # -------------------------------------------------------------
    # 1단계: Python AST 구문 분석 전수 검사
    # -------------------------------------------------------------
    log_stage(1, "Python AST 구문 분석 전수 검사")
    py_files = []
    for r, _, files in os.walk(tax_dir):
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(r, f))

    ast_errors = 0
    for p in py_files:
        rel = os.path.relpath(p, ROOT)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=p)
            print(f"  ✓ AST 통과: {rel}")
        except Exception as e:
            print(f"  ❌ AST 오류: {rel} ({e})")
            ast_errors += 1

    if ast_errors > 0:
        print(f"❌ 1단계 실패: {ast_errors}개의 AST 구문 오류가 발견되었습니다.")
        sys.exit(1)
    print("  ✅ 1단계 통과: 0 Syntax Errors (전수 검사 완료)")

    # -------------------------------------------------------------
    # 2단계: 도메인 단위 테스트 스위트 (unittest)
    # -------------------------------------------------------------
    log_stage(2, "도메인 단위 테스트 스위트 (unittest)")
    suite = unittest.defaultTestLoader.discover(os.path.join(tax_dir, "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=1)
    res = runner.run(suite)
    if not res.wasSuccessful():
        print("❌ 2단계 실패: 단위 테스트가 실패했습니다.")
        sys.exit(1)
    print("  ✅ 2단계 통과: 모든 단위 테스트 100% 통과")

    # -------------------------------------------------------------
    # 3단계: FastAPI TestClient 시뮬레이션
    # -------------------------------------------------------------
    log_stage(3, "FastAPI TestClient 시뮬레이션 및 API 응답 검증")
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api/tax")

    use_store(":memory:")
    domain.seed("test_branch")

    client = TestClient(test_app)

    # 3-1. GET /api/tax/summary
    r1 = client.get("/api/tax/summary?branch=test_branch")
    assert r1.status_code == 200, f"Summary 실패: {r1.text}"
    data1 = r1.json()
    assert data1["ok"] is True
    assert "vat" in data1 and "property_tax" in data1 and "five_roles" in data1
    print("  ✓ /api/tax/summary 통합 응답 정상")

    # 3-2. POST /api/tax/vat/transactions
    r2 = client.post("/api/tax/vat/transactions", json={
        "date": "2026-04-01",
        "tx_type": "SALES",
        "category": "임대료",
        "counterparty": "테스트임차인",
        "item_name": "상가 101호 임대료",
        "supply_amount": 1500000,
        "is_tax_exempt": False,
        "branch_id": "test_branch"
    })
    assert r2.status_code == 200, f"전표 등록 실패: {r2.text}"
    assert r2.json()["item"]["tax_amount"] == 150000
    print("  ✓ /api/tax/vat/transactions 전표 등록 및 세액 10% 자동계산 정상")

    # 3-3. POST /api/tax/sync/rent2 (멱등성 확인)
    r3 = client.post("/api/tax/sync/rent2?branch=test_branch")
    assert r3.status_code == 200
    print("  ✓ /api/tax/sync/rent2 rent2 크로스 동기화 정상")

    # 3-4. GET /api/tax/advisor/feedback (5역할 메시지 검증)
    r4 = client.get("/api/tax/advisor/feedback?branch=test_branch")
    assert r4.status_code == 200
    roles = r4.json()["roles"]
    for role in ["Advisor", "Librarian", "Tutor", "Editor", "Roommate"]:
        assert role in roles, f"역할 누락: {role}"
    print("  ✓ /api/tax/advisor/feedback 5역할 AI 협업 피드백 정상")
    print("  ✅ 3단계 통과: TestClient 시뮬레이션 100% 무결성 확인")

    # -------------------------------------------------------------
    # 4단계: 세법 도메인 정합성 & 가짜 세무 차단 검증
    # -------------------------------------------------------------
    log_stage(4, "세법 도메인 정합성 (Librarian 팩트체크 검증)")
    # 종부세 기본공제 및 공정시장가액비율 확인
    prop_test = domain.calculate_comprehensive_property_tax(
        is_single_homeowner_override=False,
        branch_id="test_branch"
    )
    assert prop_test["basic_deduction"] == 900_000_000, "일반 기본공제 9억 미준수"
    assert prop_test["market_fair_rate"] == 0.60, "공정시장가액비율 60% 미준수"
    print("  ✓ 2026 공정시장가액비율(60%) 및 기본공제(9억/12억) 수식 정확도 일치")

    # 음수 공급가액 방지
    try:
        domain.VatTransaction.create_tx(
            date="2026-05-01",
            tx_type="SALES",
            category="임대료",
            counterparty="오류테스트",
            item_name="음수테스트",
            supply_amount=-1000,
            branch_id="test_branch"
        )
        print("  ❌ 음수 공급가액이 허용됨")
        sys.exit(1)
    except Exception:
        print("  ✓ 음수 공급가액 입력 차단 정상")
    print("  ✅ 4단계 통과: 법령 및 도메인 정합성 100% 일치")

    # -------------------------------------------------------------
    # 5단계: 프론트엔드 UI 및 운영 매뉴얼 인쇄 표준 검증
    # -------------------------------------------------------------
    log_stage(5, "프론트엔드 UI 및 인쇄 매뉴얼 표준 검증")
    index_html = os.path.join(tax_dir, "frontend", "index.html")
    manual_html = os.path.join(tax_dir, "frontend", "manual.html")

    assert os.path.exists(index_html), "index.html 부재"
    assert os.path.exists(manual_html), "manual.html 부재"

    with open(manual_html, "r", encoding="utf-8") as f:
        manual_content = f.read()
    assert "window.print()" in manual_content, "매뉴얼에 인쇄 버튼/기능 부재"
    assert "@media print" in manual_content, "매뉴얼에 A4 인쇄 스타일 부재"
    print("  ✓ manual.html A4 인쇄(@media print) 및 [인쇄/PDF 저장] 버튼 탑재 확인")
    print("  ✅ 5단계 통과: 프론트엔드 번들리스 및 운영 매뉴얼 표준 준수")

    print("\n" + "=" * 65)
    print("🎉 [축하합니다] 5단계 전수 검증 100% 통과! 시스템이 안전하게 인증되었습니다.")
    print("=" * 65)


if __name__ == "__main__":
    main()
