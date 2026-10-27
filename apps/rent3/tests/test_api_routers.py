"""apps/rent3/tests/test_api_routers.py — 모듈화된 서브 라우터 회귀 테스트 스위트.

FastAPI TestClient를 활용하여 모듈화된 4개 라우터(public_apps, operations, tax_expenses, entities)의
핵심 REST API 엔드포인트 무결성 및 응답성을 검증합니다.
"""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.rent3.api import router
from apps.rent3.entities import seed_default_entities_if_empty

class TestRent3ModularApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(router, prefix="/api/rent3")
        cls.client = TestClient(app)
        seed_default_entities_if_empty()

    def test_01_public_listings(self):
        """공개 매물 목록 조회 API 검증."""
        res = self.client.get("/api/rent3/public/listings")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("listings", data)
        self.assertIsInstance(data["listings"], list)

    def test_02_cosign_session(self):
        """Co-Signing 실시간 계약 세션 생성 및 조회 검증."""
        res = self.client.post("/api/rent3/cosign/start", json={
            "branch": "main",
            "room_id": "205",
            "deposit": 5000000,
            "monthly_rent": 530000
        })
        self.assertEqual(res.status_code, 200)
        s_data = res.json()
        self.assertTrue(s_data["success"])
        session_id = s_data["session_id"]

        get_res = self.client.get(f"/api/rent3/cosign/{session_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["session"]["room_id"], "205")

    def test_03_auth_verification(self):
        """휴대폰 본인인증 발송 및 확인 검증."""
        send_res = self.client.post("/api/rent3/auth/verify-phone/send", json={
            "name": "홍길동",
            "phone": "010-1234-5678"
        })
        self.assertEqual(send_res.status_code, 200)
        self.assertTrue(send_res.json()["success"])

        confirm_res = self.client.post("/api/rent3/auth/verify-phone/confirm", json={
            "phone": "010-1234-5678",
            "code": "123456"
        })
        self.assertEqual(confirm_res.status_code, 200)
        self.assertTrue(confirm_res.json()["is_verified"])

    def test_04_admin_overview(self):
        """임대인 관제 대시보드 요약 API 검증."""
        res = self.client.get("/api/rent3/admin/overview?branch=main")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("kpi", data)
        self.assertIn("occupancy_rate", data["kpi"])

    def test_05_tax_roi_dashboard(self):
        """통합 세무·수익률 분석 API 검증."""
        res = self.client.get("/api/rent3/analytics/tax-roi?tax_mode=SEPARATE_14")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("aggregate", data["data"])
        self.assertIn("branches", data["data"])

    def test_06_operating_expenses_summary(self):
        """공용 운영비 월별 결산 API 검증."""
        res = self.client.get("/api/rent3/expenses/monthly-summary?branch=main&year=2026&month=10")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("summary", data)

    def test_07_entities_crud_api(self):
        """8대 표준 엔티티 REST API CRUD 및 O(1) 인덱스 검색 검증."""
        # 1. 대시보드 카운트
        dash_res = self.client.get("/api/rent3/entities/summary/dashboard")
        self.assertEqual(dash_res.status_code, 200)
        self.assertTrue(dash_res.json()["success"])
        self.assertIn("counts", dash_res.json())

        # 2. 임차인 엔티티 생성
        create_res = self.client.post("/api/rent3/entities/tenant", json={
            "name": "최모듈",
            "phone": "010-5555-4444",
            "account_number": "신한 110-222-333333",
            "car_number": "11하 1111",
            "email": "module@test.com"
        })
        self.assertEqual(create_res.status_code, 200)
        tenant_item = create_res.json()["item"]
        id_tenant = tenant_item["id_tenant"]

        # 3. 단일 상세 조회
        detail_res = self.client.get(f"/api/rent3/entities/tenant/{id_tenant}")
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(detail_res.json()["item"]["name"], "최모듈")

        # 4. 키워드 검색
        search_res = self.client.get("/api/rent3/entities/tenant?query=최모듈")
        self.assertEqual(search_res.status_code, 200)
        self.assertEqual(len(search_res.json()["items"]), 1)

        # 5. 수정
        update_res = self.client.put(f"/api/rent3/entities/tenant/{id_tenant}", json={
            "car_number": "22하 2222"
        })
        self.assertEqual(update_res.status_code, 200)
        self.assertEqual(update_res.json()["item"]["car_number"], "22하 2222")

        # 6. 삭제
        del_res = self.client.delete(f"/api/rent3/entities/tenant/{id_tenant}")
        self.assertEqual(del_res.status_code, 200)

        # 7. 삭제 확인 (404 Not Found)
        not_found_res = self.client.get(f"/api/rent3/entities/tenant/{id_tenant}")
        self.assertEqual(not_found_res.status_code, 404)

    def test_08_workflow_action_api(self):
        """16단계 이벤트 라이프사이클 액션 실행 API 검증."""
        res = self.client.post("/api/rent3/entities/workflow/app_publish_info", json={})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["status"], "PUBLISHED_ACTIVE")

if __name__ == "__main__":
    unittest.main()
