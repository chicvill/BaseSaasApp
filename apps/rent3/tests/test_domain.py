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


if __name__ == "__main__":
    unittest.main()


