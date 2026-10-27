import unittest
from apps.rent3.domain import RentalListing, RentalApplication, CheckInRecord, MaintenanceRequest, CoSigningSession


class TestRent3Domain(unittest.TestCase):

    def test_rental_listing_creation(self):
        """공개 매물 광고 엔티티 생성 테스트."""
        l = RentalListing(
            branch="main",
            room_id="205",
            title="[시크빌] 풀옵션 햇살 원룸",
            deposit=5000000,
            monthly_rent=500000,
            options=["에어컨", "세탁기", "냉장고"]
        )
        l.save("test_save")
        self.assertIsNotNone(l.id)
        self.assertEqual(l.room_id, "205")
        self.assertTrue(l.is_active)

    def test_rental_application_lifecycle(self):
        """3단계 비대면 계약 파이프라인 무결성 테스트."""
        # 1. 신청서 접수 (PENDING_REVIEW)
        app = RentalApplication(
            listing_id="listing_chic_203",
            branch="main",
            room_id="203",
            tenant_name="김임차",
            tenant_phone="010-9999-8888",
            move_in_date="2026-11-01",
            deposit=5000000,
            monthly_rent=500000,
            first_payment_amount=300000,
            status="PENDING_REVIEW"
        )
        app.save("test_apply")
        self.assertEqual(app.status, "PENDING_REVIEW")

        # 승인되지 않은 상태에서 결제 시도 시 예외 발생 검증
        with self.assertRaises(ValueError):
            app.confirm_payment_and_sign(tenant_sig="PREMATURE_SIG")

        # 2. 임대인 승인 (APPROVED)
        app.approve(landlord_sig="LANDLORD_SIG_OK")
        self.assertEqual(app.status, "APPROVED")
        self.assertEqual(app.landlord_signature, "LANDLORD_SIG_OK")

        # 3. 임차인 첫 결제 및 전자서명 체결 (PAID_CONFIRMED)
        app.confirm_payment_and_sign(tenant_sig="TENANT_SIG_KIM", method="CARD")
        self.assertEqual(app.status, "PAID_CONFIRMED")
        self.assertEqual(app.tenant_signature, "TENANT_SIG_KIM")
        self.assertTrue(bool(app.paid_at))

    def test_maintenance_request_lifecycle(self):
        """세입자 라운지 민원 접수 및 조치 완료 테스트."""
        m = MaintenanceRequest(
            branch="main",
            room_id="203",
            tenant_name="김임차",
            tenant_phone="010-9999-8888",
            category="facility",
            title="에어컨 필터 청소 요청",
            description="입주 후 에어컨 바람에서 냄새가 납니다."
        )
        m.save("test_maint")
        self.assertEqual(m.status, "SUBMITTED")

        # 조치 완료
        m.resolve(reply="기사님 방문하여 필터 교체 완료하였습니다.")
        self.assertEqual(m.status, "RESOLVED")
        self.assertIn("교체 완료", m.admin_reply)

    def test_cosigning_session_lifecycle(self):
        """현장 동행 실시간 동시 계약(Co-Signing) 5단계 무결성 테스트."""
        session = CoSigningSession(branch="main", room_id="203", deposit=5000000, monthly_rent=520000)
        session.save("test_cosign")
        self.assertEqual(session.current_step, 1)

        # Step 1: 등기부 확인
        session.update_step1(issue_no="2026-TEST-1234", checked=True)
        self.assertTrue(session.registry_checked)
        self.assertEqual(session.current_step, 2)

        # Step 2: 옵션/시설 점검
        session.update_step2(checks={"에어컨": True, "세탁기": True}, notes="옵션 정상", photos=[])
        self.assertEqual(session.current_step, 3)

        # Step 3: 조건 합의
        session.update_step3({
            "tenant_name": "박직객",
            "tenant_phone": "010-1111-2222",
            "car_number": "12가 3456",
            "move_in_date": "2026-11-01"
        })
        self.assertEqual(session.tenant_name, "박직객")
        self.assertEqual(session.car_number, "12가 3456")
        self.assertEqual(session.current_step, 4)

        # Step 4: 양측 서명
        session.sign("landlord", "LANDLORD_SIG_OK")
        self.assertFalse(session.tenant_signed)
        session.sign("tenant", "TENANT_SIG_OK")
        self.assertTrue(session.landlord_signed)
        self.assertTrue(session.tenant_signed)
        self.assertEqual(session.status, "SIGNED")
        self.assertEqual(session.current_step, 5)

        # Step 5: 계약금 결제 완료
        session.complete_payment(method="CARD")
        self.assertTrue(session.down_payment_paid)
        self.assertEqual(session.status, "COMPLETED")

    def test_rtms_and_checkout_domain(self):
        """RTMS 전월세신고/확정일자 및 퇴실 정산 도메인 테스트."""
        from apps.rent3.domain import RtmsContractReport, CheckOutRecord, MonthlyBill

        # RTMS 리포트 생성 및 제출
        report = RtmsContractReport(
            session_id="session_test_1",
            branch="main",
            room_id="203",
            tenant_name="이몽룡",
            tenant_phone="010-3333-4444",
            deposit=5000000,
            monthly_rent=500000
        )
        self.assertEqual(report.status, "READY")
        self.assertTrue(report.report_no.startswith("RTMS-2026-"))
        report.submit_report()
        self.assertEqual(report.status, "SUBMITTED")
        self.assertTrue(report.fixed_date_number.startswith("FIX-"))

        # 퇴실 정산 계산
        checkout = CheckOutRecord(
            branch="main",
            room_id="203",
            original_deposit=5000000,
            utility_deduction=50000,
            damage_deduction=30000
        )
        refund = checkout.calculate_refund()
        self.assertEqual(refund, 4920000)

        # 월세 청구서 자동 합계
        bill = MonthlyBill(
            branch="main",
            room_id="203",
            rent_amount=500000,
            maint_amount=50000
        )
        self.assertEqual(bill.total_amount, 550000)

        # 1:1 인앱 채팅 사진 첨부
        from apps.rent3.domain import ChatMessage
        chat = ChatMessage(
            branch="main",
            room_id="203",
            sender_role="tenant",
            sender_name="박직객",
            message="에어컨 누수 사진 첨부합니다.",
            photos=["data:image/png;base64,mockphoto123"]
        )
        self.assertEqual(len(chat.photos), 1)
        self.assertIn("mockphoto", chat.photos[0])

    def test_lease_renewal_lifecycle(self):
        """계약 만기 D-Day 알림 및 갱신/묵시적 연장 전자서명 테스트."""
        from apps.rent3.domain import LeaseRenewal
        from datetime import datetime, timedelta

        # 1. 갱신 제안 생성 (만기 90일 전 -> 갱신 통지 가능 기간)
        target_end_date = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        renewal = LeaseRenewal(
            branch="main",
            room_id="201",
            room_number="201호",
            tenant_name="김철수",
            tenant_phone="010-1234-5678",
            original_start_date="2025-11-01",
            original_end_date=target_end_date,
            original_deposit=5000000,
            original_rent=500000,
            new_deposit=5000000,
            new_rent=520000, # 4% 인상 (5% 이내 합법)
            rate_increase_pct=4.0
        )
        self.assertEqual(renewal.status, "PROPOSED")
        self.assertEqual(renewal.notice_status, "NOTICE_ACTIVE")
        self.assertGreaterEqual(renewal.days_remaining, 89)

        # 2. 임차인 서명
        renewal.sign_tenant()
        self.assertEqual(renewal.status, "AGREED")
        self.assertTrue(renewal.tenant_signed)

        # 3. 임대인 서명 -> 체결 완료(SIGNED)
        renewal.sign_landlord()
        self.assertEqual(renewal.status, "SIGNED")
        self.assertTrue(renewal.landlord_signed)

        # 4. 묵시적 갱신 테스트
        tacit = LeaseRenewal(
            branch="main",
            room_id="202",
            room_number="202호",
            original_deposit=5000000,
            original_rent=450000
        )
        tacit.confirm_tacit()
        self.assertEqual(tacit.status, "TACIT")
        self.assertEqual(tacit.renewal_type, "TACIT")
        self.assertEqual(tacit.period_months, 24)
        self.assertEqual(tacit.new_rent, 450000)


if __name__ == "__main__":
    unittest.main()



