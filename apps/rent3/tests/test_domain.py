import unittest
from apps.rent3.domain import (
    RentalListing, RentalApplication, CheckInRecord, MaintenanceRequest, 
    CoSigningSession, CheckOutRecord, calculate_branch_tax_roi, calculate_portfolio_tax_roi,
    RentPaymentCertificate, generate_rent_certificate
)


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
        self.assertTrue(report.is_subject_to_report)
        self.assertGreaterEqual(report.days_left_to_deadline, 28)
        report.submit_report()
        self.assertEqual(report.status, "SUBMITTED")
        self.assertTrue(report.fixed_date_number.startswith("FIX-"))
        self.assertIn("우선변제권", report.legal_effect_date)
        self.assertTrue(len(report.certificate_hash) >= 16)

        # auto_file_and_grant 원클릭 자동 부여 검증
        auto_rep = RtmsContractReport.auto_file_and_grant(
            session_id="session_auto_99",
            branch="main",
            room_id="204",
            tenant_name="강감찬",
            tenant_phone="010-9999-8888",
            deposit=60000000,
            monthly_rent=400000
        )
        self.assertEqual(auto_rep.status, "SUBMITTED")
        self.assertTrue(auto_rep.fixed_date_number.startswith("FIX-"))

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

    def test_maintenance_ticket_lifecycle(self):
        """하자보수(A/S) 티켓 접수, 업체 배정, 전후 사진 및 비용 정산 테스트."""
        from apps.rent3.domain import MaintenanceTicket

        # 1. 티켓 접수
        ticket = MaintenanceTicket(
            branch="main",
            room_id="203",
            room_number="203호",
            tenant_name="김임차",
            tenant_phone="010-9999-8888",
            category="plumbing",
            title="싱크대 하부 수전 누수",
            description="싱크대 아래 배관에서 물이 조금씩 샙니다.",
            initial_photos=["data:image/png;base64,leak_before_photo"]
        )
        ticket.save("test_ticket_create")
        self.assertEqual(ticket.status, "RECEIVED")
        self.assertTrue(ticket.ticket_no.startswith("AS-"))
        self.assertEqual(len(ticket.initial_photos), 1)

        # 2. 협력 수리업체 배정
        ticket.assign_contractor(
            name="강남 종합설비 (김반장)",
            phone="010-3333-7777",
            visit_at="2026-10-28 14:00",
            notes="밸브 및 패킹 교체 예정"
        )
        self.assertEqual(ticket.status, "ASSIGNED")
        self.assertEqual(ticket.contractor_name, "강남 종합설비 (김반장)")
        self.assertEqual(ticket.scheduled_visit_at, "2026-10-28 14:00")

        # 3. 수리 완료 보고 및 비용 분담 (임대인 100% 부담 - 노후 마모)
        ticket.complete_repair(
            notes="고무 패킹 마모로 신품 교체 및 누수 테스트 완료",
            photos=["data:image/png;base64,leak_after_photo"],
            cost=75000,
            burden="LANDLORD"
        )
        self.assertEqual(ticket.status, "COMPLETED")
        self.assertEqual(ticket.repair_cost, 75000)
        self.assertEqual(ticket.landlord_share, 75000)
        self.assertEqual(ticket.tenant_share, 0)
        self.assertEqual(len(ticket.completion_photos), 1)

        # 4. 수리비 정산 및 종결
        ticket.close_ticket()
        self.assertEqual(ticket.status, "CLOSED")
        self.assertTrue(ticket.is_settled)

    def test_checkout_settlement_full_lifecycle(self):
        """퇴실 점검표, 공과금/청소비 세부 정산, 세입자 서명 및 보증금 반환 완결 테스트."""
        from apps.rent3.domain import CheckOutRecord

        # 1. 퇴실 점검 착수
        rec = CheckOutRecord(
            branch="main",
            room_id="203",
            room_number="203호",
            tenant_name="김임차",
            tenant_phone="010-9999-8888",
            original_deposit=5000000,
            unpaid_rent_deduction=500000,
            water_fee=15000,
            electric_fee=20000,
            gas_fee=10000,
            cleaning_fee=70000,
            repair_deduction=30000,
            damage_notes="벽지 통상마모 인정, 도어락 커버 파손 실비 3만원 공제"
        )
        rec.save("test_checkout_init")
        self.assertEqual(rec.status, "INSPECTING")
        self.assertTrue(rec.settlement_no.startswith("MO-"))

        # 2. 정산금 자동 계산
        refund = rec.calculate_refund()
        self.assertEqual(rec.utility_deduction, 45000)
        self.assertEqual(rec.damage_deduction, 100000)
        self.assertEqual(refund, 4355000)
        self.assertEqual(rec.final_refund_deposit, 4355000)

        # 3. 임차인 정산 동의 및 계좌 등록
        rec.agree_by_tenant(
            signature="SIG_DATA_KIM_2026",
            bank="토스뱅크",
            account="1000-2222-3333",
            holder="김임차"
        )
        self.assertEqual(rec.status, "AGREED")
        self.assertTrue(rec.tenant_agreed)
        self.assertEqual(rec.refund_bank, "토스뱅크")
        self.assertEqual(rec.refund_account, "1000-2222-3333")

        # 4. 보증금 송금 완료 및 호실 공실화
        rec.complete_settlement(tx_ref="TX-TOSS-998877")
        self.assertEqual(rec.status, "COMPLETED")
        self.assertTrue(rec.refund_completed)
        self.assertEqual(rec.refund_tx_ref, "TX-TOSS-998877")
        self.assertTrue(bool(rec.refund_completed_at))

    def test_branch_and_portfolio_tax_roi_analytics(self):
        """지점별(시크빌/선호아트빌/신비주택) 통합 세무 및 수익률 분석 정밀성 테스트."""
        # 1. 시크빌(main) 단일 세무·수익률 검증
        chic_rep = calculate_branch_tax_roi("main", tax_mode="SEPARATE_14")
        self.assertEqual(chic_rep["branch_id"], "main")
        self.assertEqual(chic_rep["branch_name"], "시크빌")
        self.assertTrue(chic_rep["total_rooms"] >= 26)
        self.assertTrue(chic_rep["occupancy_rate"] > 0)
        self.assertTrue(chic_rep["annual_gross_revenue"] > 0)
        self.assertTrue(chic_rep["noi"] > 0)
        self.assertTrue(chic_rep["roi"]["net_roi_pct"] > 0)
        # 세무: 주택 임대 부가세 면세 확인
        self.assertTrue(chic_rep["taxes"]["is_vat_exempt"])
        self.assertEqual(chic_rep["taxes"]["vat"], 0)
        self.assertTrue(chic_rep["taxes"]["rental_income_tax"] > 0)

        # 2. 종합과세 모드 전환 시 세액 변동 검증
        chic_comp = calculate_branch_tax_roi("main", tax_mode="COMPREHENSIVE")
        self.assertTrue(chic_comp["taxes"]["rental_income_tax"] > 0)

        # 3. 3대 지점 통합(Portfolio) 분석 검증
        portfolio = calculate_portfolio_tax_roi(tax_mode="SEPARATE_14")
        agg = portfolio["aggregate"]
        self.assertEqual(agg["total_branches"], 3)
        self.assertTrue(agg["total_rooms"] >= 60)
        self.assertTrue(agg["occupied_rooms"] > 0)
        self.assertTrue(agg["annual_gross_revenue"] > 0)
        self.assertTrue(agg["annual_vacancy_loss"] > 0)
        self.assertEqual(agg["taxes"]["total_vat"], 0) # 전액 주택 면세
        self.assertTrue(agg["taxes"]["grand_total_tax"] > 0)
        self.assertTrue(agg["roi"]["avg_net_roi"] > 0)
        self.assertEqual(len(portfolio["branches"]), 3)
        self.assertTrue(len(portfolio["tax_advisory_tips"]) >= 3)

    def test_rent_payment_certificate_generation(self):
        """연말정산 월세 세액공제용 공식 납입 증명서 발급 무결성 테스트."""
        cert = generate_rent_certificate(branch="main", room_id="201", year=2026)
        self.assertIsNotNone(cert)
        self.assertTrue(cert.cert_no.startswith("CERT-2026-MAIN-201"))
        self.assertEqual(cert.branch, "main")
        self.assertEqual(cert.room_id, "201")
        self.assertEqual(cert.year, 2026)
        self.assertTrue(len(cert.items) > 0)
        self.assertTrue(cert.total_paid_amount > 0)
        self.assertTrue(cert.estimated_tax_refund > 0)
        self.assertTrue(cert.is_sealed)
        
        # 첫 번째 월별 항목의 국세청 현금영수증 승인번호 형식 검증
        first_item = cert.items[0]
        self.assertEqual(first_item["month"], 1)
        self.assertTrue(first_item["paid_date"].startswith("2026-01"))
        self.assertTrue("-" in first_item["cash_receipt_no"])
        self.assertTrue(first_item["is_confirmed"])

    def test_building_operating_expense_and_monthly_summary(self):
        """지점별 공용 운영비(승강기·청소·공용전기수도 등) 실지출 전산 등록 및 월별 실결산(NOI) 테스트."""
        from apps.rent3.domain import BuildingOperatingExpense, calculate_monthly_operating_summary

        # 1. 공용 운영비 신규 전산 등록
        exp = BuildingOperatingExpense(
            branch="main",
            year=2026,
            month=10,
            category="elevator",
            title="10월 승강기 정기 점검 및 안전검사비",
            amount=180000,
            vendor_name="현대엘리베이터(주)",
            vendor_biz_no="107-81-22440",
            payment_date="2026-10-15",
            payment_method="TRANSFER",
            receipt_type="TAX_INVOICE",
            notes="월간 안전검사 필증 수령 완료",
            is_tax_deductible=True
        )
        exp.save("test_save_expense")
        self.assertIsNotNone(exp.id)
        self.assertEqual(exp.amount, 180000)
        self.assertEqual(exp.category, "elevator")
        self.assertEqual(BuildingOperatingExpense.get_category_label("elevator"), "🛗 승강기 유지보수 및 점검료")

        # 2. 추가 운영비 항목 등록 (청소비, 공용전기수도)
        exp2 = BuildingOperatingExpense(
            branch="main",
            year=2026,
            month=10,
            category="cleaning",
            title="10월 계단 및 복도 정기 청소비",
            amount=150000,
            vendor_name="그린종합청소",
            payment_date="2026-10-10",
            payment_method="TRANSFER",
            receipt_type="TAX_INVOICE"
        )
        exp2.save("test_save_expense_2")

        # 3. 월별 실결산 집계 (calculate_monthly_operating_summary)
        summary = calculate_monthly_operating_summary(branch="main", year=2026, month=10)
        self.assertEqual(summary["branch"], "main")
        self.assertEqual(summary["year"], 2026)
        self.assertEqual(summary["month"], 10)
        self.assertTrue(summary["total_revenue"] > 0)
        self.assertTrue(summary["total_expense"] >= 330000) # 180000 + 150000
        # 순운영이익(NOI) = 총수입 - 실제 총운영비
        self.assertEqual(summary["net_operating_income"], summary["total_revenue"] - summary["total_expense"])
        self.assertTrue(summary["expense_ratio"] > 0)
        self.assertTrue(summary["net_margin_rate"] > 0)
        self.assertTrue(len(summary["categories_breakdown"]) >= 2)

        # 4. 세무 & 수익률 분석에 실제 운영비 연동 검증
        roi_res = calculate_branch_tax_roi("main", tax_mode="SEPARATE_14")
        self.assertTrue(roi_res["noi"] > 0)
        self.assertTrue(roi_res["operating_expenses"] >= summary["total_expense"])

    def test_annual_tax_statement_and_csv_generation(self):
        """임대인 5월 종합소득세 홈택스 제출용 연간 소득·필요경비 표준 전산 명세서 및 엑셀(CSV) 생성 테스트."""
        from apps.rent3.domain import generate_annual_tax_statement, generate_tax_statement_csv

        # 1. 시크빌(main) 단일 지점 연간 종소세 명세서 생성 검증
        stmt_main = generate_annual_tax_statement(branch="main", year=2026)
        self.assertIsNotNone(stmt_main)
        self.assertTrue(stmt_main["statement_no"].startswith("TAX-STMT-2026-MAIN-"))
        self.assertEqual(stmt_main["year"], 2026)
        self.assertEqual(stmt_main["branch"], "main")
        self.assertTrue(stmt_main["revenue_summary"]["annual_gross_revenue"] > 0)
        self.assertTrue(stmt_main["revenue_summary"]["is_vat_exempt"]) # 주택면세 확인
        self.assertTrue(stmt_main["expense_summary"]["grand_total_expense"] > 0)
        
        # 소득금액 = 총수입 - 필요경비
        expected_taxable = stmt_main["revenue_summary"]["annual_gross_revenue"] - stmt_main["expense_summary"]["grand_deductible_expense"]
        self.assertEqual(stmt_main["taxable_result"]["taxable_rental_income"], expected_taxable)
        self.assertEqual(stmt_main["taxable_result"]["separate_tax_estimate_14"], int(expected_taxable * 0.154))
        self.assertTrue(len(stmt_main["expense_summary"]["categories"]) >= 8)

        # 2. 3대 지점 통합(all) 포트폴리오 명세서 생성 검증
        stmt_all = generate_annual_tax_statement(branch="all", year=2026)
        self.assertTrue(stmt_all["total_rooms"] >= 60)
        self.assertTrue(stmt_all["revenue_summary"]["annual_gross_revenue"] >= stmt_main["revenue_summary"]["annual_gross_revenue"])

        # 3. 국세청 제출 및 엑셀 호환 UTF-8 BOM CSV 생성 검증
        csv_text = generate_tax_statement_csv(stmt_all)
        self.assertTrue(csv_text.startswith("\ufeff")) # Excel 한글 깨짐 방지 BOM
        self.assertIn("부동산 임대소득 및 필요경비 연간 표준 합산 명세서", csv_text)
        self.assertIn("소득세법 제19조", csv_text)
        self.assertIn("임대소득금액 (총수입 - 필요경비)", csv_text)


if __name__ == "__main__":
    unittest.main()






