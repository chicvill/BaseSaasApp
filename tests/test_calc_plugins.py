"""
tests/test_calc_plugins.py — BaseSaasApp 제로터치 플러그인 연산 앱 5종 종합 검증 스위트.
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient
from core.gateway import create_gateway

CALC_APP_IDS = ["calc_add", "calc_sub", "calc_mul", "calc_div", "calc_expr"]


class TestCalcPlugins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseSaasApp 게이트웨이 초기화 (모든 앱 자동 발견)
        cls.app = create_gateway(db_path=":memory:")
        cls.client = TestClient(cls.app)

    def test_01_catalog_auto_discovery(self):
        """1. 게이트웨이가 코어 수정 없이 5개 연산 앱을 제로터치로 자동 등록했는지 검증."""
        res = self.client.get("/api/apps")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])

        registered_app_ids = [app["app_id"] for app in data["apps"]]
        for app_id in CALC_APP_IDS:
            self.assertIn(app_id, registered_app_ids, f"앱 {app_id} 가 카탈로그에 자동 등록되지 않았습니다.")
            print(f"  [확인] 카탈로그 등록 완료: {app_id}")

    def test_02_calc_add_compute(self):
        """2. 스마트 덧셈기 (3 + 5 = 8) 연산 통로 검증."""
        res = self.client.get("/api/calc_add/compute?a=3&b=5")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["result"], 8)
        self.assertEqual(payload["data"]["formula"], "3 + 5 = 8")
        print("  [확인] 덧셈기 연산 성공: 3 + 5 = 8")

    def test_03_calc_sub_compute(self):
        """3. 정밀 뺄셈기 (8 - 3 = 5) 연산 통로 검증."""
        res = self.client.get("/api/calc_sub/compute?a=8&b=3")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["result"], 5)
        self.assertEqual(payload["data"]["formula"], "8 - 3 = 5")
        print("  [확인] 뺄셈기 연산 성공: 8 - 3 = 5")

    def test_04_calc_mul_compute(self):
        """4. 고속 곱셈기 (3 * 5 = 15) 연산 통로 검증."""
        res = self.client.get("/api/calc_mul/compute?a=3&b=5")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["result"], 15)
        self.assertEqual(payload["data"]["formula"], "3 * 5 = 15")
        print("  [확인] 곱셈기 연산 성공: 3 * 5 = 15")

    def test_05_calc_div_compute(self):
        """5. 안전 나눗셈기 (15 / 3 = 5) 연산 통로 검증."""
        res = self.client.get("/api/calc_div/compute?a=15&b=3")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["result"], 5)
        self.assertEqual(payload["data"]["formula"], "15 / 3 = 5")
        print("  [확인] 나눗셈기 연산 성공: 15 / 3 = 5")

    def test_06_calc_expr_compute(self):
        """6. 수식 괄호 연산기 ((3 + 5) * 2 = 16) 복합 연산 통로 검증."""
        res = self.client.get("/api/calc_expr/compute?expr=(3%20%2B%205)%20*%202")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["result"], 16)
        self.assertEqual(payload["data"]["formula"], "(3 + 5) * 2 = 16")
        print("  [확인] 수식 연산기 성공: (3 + 5) * 2 = 16")

    def test_07_web_frontends_serve(self):
        """7. 5개 연산 앱의 독립 웹 프론트엔드 서빙 검증."""
        for app_id in CALC_APP_IDS:
            res = self.client.get(f"/{app_id}/")
            self.assertEqual(res.status_code, 200, f"/{app_id}/ 웹 접근 실패")
            self.assertIn("text/html", res.headers.get("content-type", ""))
            print(f"  [확인] 웹 프론트엔드 서빙 정상: /{app_id}/")


if __name__ == "__main__":
    unittest.main()
