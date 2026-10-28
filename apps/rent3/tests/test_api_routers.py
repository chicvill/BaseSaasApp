"""apps/rent3/tests/test_api_routers.py — 모듈화된 서브 라우터 회귀 테스트 스위트.

FastAPI TestClient를 활용하여 모듈화된 4개 라우터(public_apps, operations, tax_expenses, entities)의
핵심 REST API 엔드포인트 무결성 및 응답성을 검증합니다.
"""
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.rent3.api import router
from apps.rent3.entities import seed_default_entities_if_empty
from apps.rent3.domain import RentalApplication

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

    def test_09_admin_create_listing_and_apply_flow(self):
        """공실 매물 등록 -> 온라인 입주 신청 -> 임대인 승인 -> 첫 결제 및 전자서명 체결 검증."""
        # 1. 관리자 매물 등록 (ListingCreateReq 검증)
        list_res = self.client.post("/api/rent3/admin/listings", json={
            "branch": "main",
            "room_id": "901",
            "title": "[테스트] 901호 풀옵션 신축 원룸",
            "deposit": 10000000,
            "monthly_rent": 600000,
            "maintenance_fee": 60000,
            "options": ["에어컨", "세탁기", "냉장고", "전자레인지"]
        })
        self.assertEqual(list_res.status_code, 200)
        self.assertTrue(list_res.json()["success"])
        listing_id = list_res.json()["listing_id"]

        # 2. 임차인 입주 신청
        apply_res = self.client.post("/api/rent3/public/apply", json={
            "listing_id": listing_id,
            "branch": "main",
            "room_id": "901",
            "tenant_name": "테스터",
            "tenant_phone": "010-8888-7777",
            "move_in_date": "2026-11-15",
            "deposit": 10000000,
            "monthly_rent": 600000
        })
        self.assertEqual(apply_res.status_code, 200)
        app_id = apply_res.json()["application_id"]

        # 3. 임대인 승인
        appr_res = self.client.post(f"/api/rent3/admin/applications/{app_id}/approve", json={
            "landlord_sig": "SIG_LANDLORD_901"
        })
        self.assertEqual(appr_res.status_code, 200)
        self.assertTrue(appr_res.json()["success"])

        # 4. 임차인 첫 결제 및 서명 체결
        sign_res = self.client.post(f"/api/rent3/public/application/{app_id}/sign-and-pay", json={
            "tenant_signature": "SIG_TENANT_TESTER",
            "payment_method": "CARD"
        })
        self.assertEqual(sign_res.status_code, 200)
        self.assertTrue(sign_res.json()["success"])
        self.assertEqual(sign_res.json()["status"], "PAID_CONFIRMED")

    def test_10_cosign_full_5steps_pipeline(self):
        """Co-Signing 현장 동행 실시간 동시 계약 5단계 전체 파이프라인 검증."""
        # 세션 시작
        start_res = self.client.post("/api/rent3/cosign/start", json={
            "branch": "main",
            "room_id": "902",
            "deposit": 5000000,
            "monthly_rent": 500000
        })
        self.assertEqual(start_res.status_code, 200)
        s_id = start_res.json()["session_id"]

        # Step 1: 등기부 확인
        s1_res = self.client.post(f"/api/rent3/cosign/{s_id}/step1", json={
            "issue_no": "2026-REG-902",
            "checked": True
        })
        self.assertEqual(s1_res.status_code, 200)
        self.assertEqual(s1_res.json()["session"]["current_step"], 2)

        # Step 2: 옵션 점검
        s2_res = self.client.post(f"/api/rent3/cosign/{s_id}/step2", json={
            "checks": {"에어컨": True, "수압": True},
            "notes": "모든 옵션 정상 확인"
        })
        self.assertEqual(s2_res.status_code, 200)
        self.assertEqual(s2_res.json()["session"]["current_step"], 3)

        # Step 3: 조건 합의
        s3_res = self.client.post(f"/api/rent3/cosign/{s_id}/step3", json={
            "deposit": 5000000,
            "monthly_rent": 500000,
            "tenant_name": "동시계약자",
            "tenant_phone": "010-7777-6666",
            "car_number": "33허 3333",
            "move_in_date": "2026-11-20"
        })
        self.assertEqual(s3_res.status_code, 200)
        self.assertEqual(s3_res.json()["session"]["current_step"], 4)

        # Step 4: 양측 서명
        sign_landlord = self.client.post(f"/api/rent3/cosign/{s_id}/sign", json={
            "role": "landlord",
            "signature": "SIG_LANDLORD_902"
        })
        self.assertEqual(sign_landlord.status_code, 200)
        sign_tenant = self.client.post(f"/api/rent3/cosign/{s_id}/sign", json={
            "role": "tenant",
            "signature": "SIG_TENANT_902"
        })
        self.assertEqual(sign_tenant.status_code, 200)
        self.assertEqual(sign_tenant.json()["session"]["status"], "SIGNED")

        # Step 5: 계약금 결제
        pay_res = self.client.post(f"/api/rent3/cosign/{s_id}/pay", json={
            "method": "CARD"
        })
        self.assertEqual(pay_res.status_code, 200)
        self.assertEqual(pay_res.json()["session"]["status"], "COMPLETED")

    def test_11_chat_messenger_and_quick_reply(self):
        """앱내 1:1 직통 메신저 송수신, 읽음 처리 및 퀵 액션 연동 검증."""
        # 1. 메시지 발신 (임차인 -> 임대인)
        send_res = self.client.post("/api/rent3/chat/main/903/send", json={
            "branch": "main",
            "room_id": "903",
            "sender_role": "tenant",
            "sender_name": "예비임차인",
            "message": "방 보러 가고 싶습니다.",
            "is_tour_request": True,
            "tour_date": "2026-10-29 15:00"
        })
        self.assertEqual(send_res.status_code, 200)
        self.assertTrue(send_res.json()["success"])

        # 2. 대화 내역 조회
        get_res = self.client.get("/api/rent3/chat/main/903")
        self.assertEqual(get_res.status_code, 200)
        self.assertGreater(len(get_res.json()["messages"]), 0)

        # 3. 읽음 처리
        read_res = self.client.post("/api/rent3/chat/main/903/read?role=landlord")
        self.assertEqual(read_res.status_code, 200)
        self.assertTrue(read_res.json()["success"])

        # 4. 퀵 리플라이 템플릿
        quick_res = self.client.post("/api/rent3/chat/main/903/quick-reply?template=tour_accept")
        self.assertEqual(quick_res.status_code, 200)
        self.assertTrue(quick_res.json()["success"])

        # 5. 관리자 활성 채팅 목록
        admin_chats_res = self.client.get("/api/rent3/admin/chats?branch=main")
        self.assertEqual(admin_chats_res.status_code, 200)
        self.assertIn("rooms", admin_chats_res.json())

    def test_12_tenant_bills_and_payment_flow(self):
        """세입자 당월 월세 청구서 조회 및 결제(secrets 모듈 정상 동작 및 영수증 발급) 검증."""
        # 1. 청구서 조회
        bills_res = self.client.get("/api/rent3/tenant/bills/main/904")
        self.assertEqual(bills_res.status_code, 200)
        b_data = bills_res.json()
        self.assertIn("bills", b_data)
        self.assertIsNotNone(b_data["current_unpaid"])

        # 2. 미납 청구서 결제 (이전에 secrets NameError가 발생했던 지점 회귀 검증)
        pay_res = self.client.post("/api/rent3/tenant/bills/main/904/pay", json={
            "payment_method": "CARD",
            "tenant_name": "홍길동"
        })
        self.assertEqual(pay_res.status_code, 200)
        p_data = pay_res.json()
        self.assertTrue(p_data["success"])
        self.assertIn("receipt", p_data)
        receipt_id = p_data["receipt"]["receipt_no"]

        # 3. 전자 영수증 조회
        rcp_res = self.client.get(f"/api/rent3/tenant/receipt/{receipt_id}")
        self.assertEqual(rcp_res.status_code, 200)
        self.assertEqual(rcp_res.json()["receipt"]["room_id"], "904")

    def test_13_checkin_and_parking_management(self):
        """첫 입실 체크인 확인, 주차 등록 및 도어락 번호 관리 API 검증."""
        # 1. 주차 등록
        park_res = self.client.post("/api/rent3/tenant/parking/register", json={
            "branch": "main",
            "room_id": "905",
            "tenant_name": "김주차",
            "tenant_phone": "010-1111-9999",
            "car_number": "77가 7777",
            "car_model": "아반떼",
            "notes": "지하 1층 주차"
        })
        self.assertEqual(park_res.status_code, 200)
        self.assertTrue(park_res.json()["success"])

        # 2. 관리자 주차 대장 조회
        admin_park_res = self.client.get("/api/rent3/admin/parking?branch=main")
        self.assertEqual(admin_park_res.status_code, 200)
        self.assertIn("vehicles", admin_park_res.json())

        # 3. 도어락 비밀번호 원격 변경
        door_res = self.client.post("/api/rent3/admin/doorlock/update", json={
            "branch": "main",
            "room_id": "905",
            "door_lock_code": "9999*"
        })
        self.assertEqual(door_res.status_code, 200)
        self.assertTrue(door_res.json()["success"])

        # 4. 첫 입실 체크인 완료 처리
        app = RentalApplication(
            branch="main",
            room_id="905",
            tenant_name="김주차",
            status="PAID_CONFIRMED"
        )
        app.save("test_checkin_app")
        cin_res = self.client.post("/api/rent3/tenant/checkin-confirm", json={
            "application_id": app.id,
            "inspection_notes": "도어락 및 조명 정상 확인"
        })
        self.assertEqual(cin_res.status_code, 200)
        self.assertTrue(cin_res.json()["success"])

    def test_14_rtms_report_and_submission(self):
        """국토교통부 전월세신고서 생성 및 대법원 확정일자 부여 제출 API 검증."""
        # 1. RTMS 보고서 자동 조회/생성 (CoSigningSession NameError 회귀 검증)
        rep_res = self.client.get("/api/rent3/rtms/906/report?branch=main")
        self.assertEqual(rep_res.status_code, 200)
        self.assertTrue(rep_res.json()["success"])
        rep_id = rep_res.json()["report"]["id"]

        # 2. RTMS 신고 제출 및 확정일자 번호 발급
        submit_res = self.client.post(f"/api/rent3/rtms/{rep_id}/submit")
        self.assertEqual(submit_res.status_code, 200)
        s_data = submit_res.json()
        self.assertTrue(s_data["success"])
        self.assertTrue(s_data["report"]["fixed_date_number"].startswith("FIX-"))

        # 3. 관리자 미신고 D-Day 관제 목록
        pending_res = self.client.get("/api/rent3/admin/rtms/pending?branch=main")
        self.assertEqual(pending_res.status_code, 200)
        self.assertIn("reports", pending_res.json())

    def test_15_lease_renewals_and_5pct_cap(self):
        """주택임대차보호법 만기 재계약 제안, 법정 5% 상한선 검증 및 묵시적 갱신 API 검증."""
        # 1. 재계약 관제 목록 조회
        list_res = self.client.get("/api/rent3/renewals?branch=main")
        self.assertEqual(list_res.status_code, 200)
        self.assertTrue(list_res.json()["success"])

        # 2. 5% 상한선 초과 제안 시 400 Bad Request 에러 반환 검증
        excess_res = self.client.post("/api/rent3/renewals/propose", json={
            "branch": "main",
            "room_id": "907",
            "renewal_type": "EXPLICIT",
            "original_rent": 500000,
            "new_deposit": 5000000,
            "new_rent": 600000 # 20% 인상 초과
        })
        self.assertEqual(excess_res.status_code, 400)
        self.assertIn("5.0%", excess_res.json()["detail"])

        # 3. 5% 이내 합법 제안 (4% 인상: 500,000 -> 520,000)
        ok_res = self.client.post("/api/rent3/renewals/propose", json={
            "branch": "main",
            "room_id": "907",
            "renewal_type": "EXPLICIT",
            "original_rent": 500000,
            "new_deposit": 5000000,
            "new_rent": 520000,
            "period_months": 24
        })
        self.assertEqual(ok_res.status_code, 200)
        renewal_id = ok_res.json()["renewal"]["id"]

        # 4. 임차인 전자서명 날인 -> 체결(SIGNED)
        sign_res = self.client.post(f"/api/rent3/renewals/{renewal_id}/sign", json={
            "role": "tenant",
            "signature": "SIG_TENANT_RENEWAL_907"
        })
        self.assertEqual(sign_res.status_code, 200)
        self.assertEqual(sign_res.json()["status"], "SIGNED")

        # 5. 묵시적 갱신 확정
        tacit_res = self.client.post(f"/api/rent3/renewals/{renewal_id}/tacit-confirm")
        self.assertEqual(tacit_res.status_code, 200)
        self.assertEqual(tacit_res.json()["renewal"]["status"], "TACIT")

    def test_16_maintenance_ticket_full_pipeline(self):
        """정식 A/S 티켓 접수 -> 협력업체 배정 -> 수리 완료(비용 분담) -> 정산 종결 API 검증."""
        # 1. A/S 티켓 접수
        create_res = self.client.post("/api/rent3/maintenance/tickets", json={
            "branch": "main",
            "room_id": "908",
            "tenant_name": "김고객",
            "tenant_phone": "010-4444-3333",
            "category": "plumbing",
            "title": "욕실 세면대 수전 누수",
            "description": "물틀면 아래 호스로 물이 흘러내립니다.",
            "initial_photos": ["data:image/png;base64,leak_test"]
        })
        self.assertEqual(create_res.status_code, 200)
        ticket_id = create_res.json()["ticket"]["id"]

        # 2. 협력업체 배정
        assign_res = self.client.post(f"/api/rent3/maintenance/tickets/{ticket_id}/assign", json={
            "contractor_name": "서울설비",
            "contractor_phone": "010-8888-2222",
            "scheduled_visit_at": "2026-10-30 11:00",
            "notes": "패킹 교체 필요"
        })
        self.assertEqual(assign_res.status_code, 200)
        self.assertEqual(assign_res.json()["ticket"]["status"], "ASSIGNED")

        # 3. 수리 완료 및 비용 분담 등록
        complete_res = self.client.post(f"/api/rent3/maintenance/tickets/{ticket_id}/complete", json={
            "repair_notes": "신규 수전 교체 완료",
            "completion_photos": ["data:image/png;base64,fixed_test"],
            "repair_cost": 80000,
            "cost_burden": "LANDLORD"
        })
        self.assertEqual(complete_res.status_code, 200)
        self.assertEqual(complete_res.json()["ticket"]["status"], "COMPLETED")
        self.assertEqual(complete_res.json()["ticket"]["landlord_share"], 80000)

        # 4. 정산 종결
        close_res = self.client.post(f"/api/rent3/maintenance/tickets/{ticket_id}/close")
        self.assertEqual(close_res.status_code, 200)
        self.assertEqual(close_res.json()["ticket"]["status"], "CLOSED")

    def test_17_checkout_deposit_settlement_pipeline(self):
        """퇴실 점검 착수 -> 공과금/청소비 정산 -> 임차인 서명 동의 -> 보증금 반환 완결 API 검증."""
        # 1. 퇴실 검수 및 공제 등록
        inspect_res = self.client.post("/api/rent3/checkout/main/909/inspect", json={
            "tenant_name": "김퇴실",
            "tenant_phone": "010-2222-3333",
            "unpaid_rent": 200000,
            "water_fee": 10000,
            "electric_fee": 15000,
            "gas_fee": 5000,
            "cleaning_fee": 70000,
            "repair_deduction": 30000,
            "damage_notes": "도어락 키패드 흠집 실비 공제"
        })
        self.assertEqual(inspect_res.status_code, 200)
        self.assertTrue(inspect_res.json()["success"])

        # 2. 임차인 동의 서명 및 반환 계좌 등록
        agree_res = self.client.post("/api/rent3/checkout/main/909/tenant-agree", json={
            "signature": "SIG_TENANT_CHECKOUT_909",
            "refund_bank": "카카오뱅크",
            "refund_account": "3333-01-9999999",
            "refund_holder": "김퇴실"
        })
        self.assertEqual(agree_res.status_code, 200)
        self.assertTrue(agree_res.json()["success"])

        # 3. 보증금 송금 완료 및 호실 공실 전환
        complete_res = self.client.post("/api/rent3/checkout/main/909/complete", json={
            "tx_ref": "TX-KB-990011"
        })
        self.assertEqual(complete_res.status_code, 200)
        self.assertTrue(complete_res.json()["success"])

        # 4. 정산 영수증 조회
        rcp_res = self.client.get("/api/rent3/checkout/main/909/receipt")
        self.assertEqual(rcp_res.status_code, 200)
        self.assertEqual(rcp_res.json()["room_number"], "909호")

    def test_18_tax_statement_csv_and_certificate(self):
        """연간 종소세 명세서 조회, 국세청/엑셀 UTF-8 BOM CSV 다운로드 및 세액공제 증명서 발급 API 검증."""
        # 1. 연간 종소세 명세서 조회
        stmt_res = self.client.get("/api/rent3/tax/annual-statement?branch=main&year=2026")
        self.assertEqual(stmt_res.status_code, 200)
        self.assertTrue(stmt_res.json()["success"])

        # 2. UTF-8 BOM CSV 내보내기 검증
        csv_res = self.client.get("/api/rent3/tax/annual-statement/export-csv?branch=main&year=2026")
        self.assertEqual(csv_res.status_code, 200)
        self.assertTrue(csv_res.text.startswith("\ufeff"))
        self.assertIn("부동산 임대소득 및 필요경비", csv_res.text)

        # 3. 연말정산 공식 세액공제 증명서 조회
        cert_res = self.client.get("/api/rent3/certificate/rent-tax/main/201?year=2026")
        self.assertEqual(cert_res.status_code, 200)
        self.assertTrue(cert_res.json()["success"])
        self.assertTrue(cert_res.json()["certificate"]["cert_no"].startswith("CERT-2026-MAIN-201"))

    def test_19_openapi_schema_integrity(self):
        """FastAPI OpenAPI 스키마 정상 생성 및 엔드포인트 무결성 검증 (ListingCreateReq ForwardRef 회귀 방지)."""
        schema = self.client.app.openapi()
        self.assertIsNotNone(schema)
        self.assertIn("paths", schema)
        self.assertGreater(len(schema["paths"]), 50)


if __name__ == "__main__":
    unittest.main()
