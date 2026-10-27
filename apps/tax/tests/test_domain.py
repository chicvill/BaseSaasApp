"""
apps/tax/tests/test_domain.py — 스마트 세무 도메인 모델 단위 테스트.
"""
import unittest
from core.kernel.obj import use_store
from apps.tax.backend import domain


class TestTaxDomain(unittest.TestCase):
    def setUp(self):
        use_store(":memory:")
        self.branch = "test_branch"

    def test_vat_calculation(self):
        # 1. 과세 매출 전표 (상가 임대료 1,000,000원 -> 세액 100,000원)
        tx1 = domain.VatTransaction.create_tx(
            date="2026-04-10",
            tx_type="SALES",
            category="임대료",
            counterparty="홍길동",
            item_name="1층 상가 임대료",
            supply_amount=1_000_000,
            is_tax_exempt=False,
            branch_id=self.branch
        )
        self.assertEqual(tx1.tax_amount, 100_000)
        self.assertEqual(tx1.period, "2026-1기확정")

        # 2. 면세 매출 전표 (원룸 주택 임대료 500,000원 -> 세액 0원)
        tx2 = domain.VatTransaction.create_tx(
            date="2026-05-15",
            tx_type="SALES",
            category="임대료",
            counterparty="이몽룡",
            item_name="201호 주택 임대료",
            supply_amount=500_000,
            is_tax_exempt=True,
            branch_id=self.branch
        )
        self.assertEqual(tx2.tax_amount, 0)

        # 3. 매입 전표 (보일러 수리비 400,000원 -> 세액 40,000원)
        tx3 = domain.VatTransaction.create_tx(
            date="2026-05-20",
            tx_type="PURCHASE",
            category="시설보수",
            counterparty="대한설비",
            item_name="보일러 수리",
            supply_amount=400_000,
            is_tax_exempt=False,
            branch_id=self.branch
        )
        self.assertEqual(tx3.tax_amount, 40_000)

        # 4. 부가세 합산 검증
        rep = domain.calculate_vat_report(period="2026-1기확정", branch_id=self.branch)
        self.assertEqual(rep["tx_count"], 3)
        self.assertEqual(rep["sales"]["taxable_supply"], 1_000_000)
        self.assertEqual(rep["sales"]["exempt_supply"], 500_000)
        self.assertEqual(rep["sales"]["tax"], 100_000)
        self.assertEqual(rep["purchase"]["tax"], 40_000)
        self.assertEqual(rep["net_tax_payable"], 60_000)    # 100,000 - 40,000 = 60,000
        self.assertFalse(rep["is_refund"])

    def test_property_tax_simulation_single_homeowner(self):
        # 1세대 1주택 14억 주택 (기본공제 12억 적용)
        domain.PropertyAsset(
            branch_id=self.branch,
            name="자택 아파트",
            asset_type="RESIDENTIAL",
            address="서울시 서초구",
            official_price=1_400_000_000,
            ownership_ratio=100.0,
            is_single_homeowner=True,
            holding_years=10,
            owner_age=66
        ).save("test")

        res = domain.calculate_comprehensive_property_tax(branch_id=self.branch)
        self.assertEqual(res["total_official_price"], 1_400_000_000)
        self.assertEqual(res["basic_deduction"], 1_200_000_000)
        self.assertEqual(res["tax_base_raw"], 200_000_000)
        # 과세표준 = 2억 * 60% = 1.2억
        self.assertEqual(res["tax_base"], 120_000_000)
        # 3억 이하 세율 0.5% -> 120,000,000 * 0.005 = 600,000
        self.assertEqual(res["calculated_tax"], 600_000)
        self.assertGreater(res["total_tax_payable"], 0)

    def test_property_tax_exemption_below_threshold(self):
        # 공시지가 8억 (기본공제 9억 이하 -> 전액 비과세)
        domain.PropertyAsset(
            branch_id=self.branch,
            name="소형 원룸건물",
            asset_type="RESIDENTIAL",
            address="인천시 미추홀구",
            official_price=800_000_000,
            ownership_ratio=100.0,
            is_single_homeowner=False
        ).save("test")

        res = domain.calculate_comprehensive_property_tax(branch_id=self.branch)
        self.assertEqual(res["tax_base"], 0)
        self.assertTrue(res["is_exempt"])
        self.assertEqual(res["total_tax_payable"], 0)

    def test_five_role_intelligence(self):
        # 5역할 인텔리전스 응답 확인
        roles = domain.get_five_role_intelligence(branch_id=self.branch)
        for role_name in ["Advisor", "Librarian", "Tutor", "Editor", "Roommate"]:
            self.assertIn(role_name, roles)
            self.assertTrue(len(roles[role_name]["message"]) > 10)

    def test_idempotent_creation(self):
        # 동일 ref_id 전표 중복 생성 방지
        tx_a = domain.VatTransaction.create_tx(
            date="2026-03-01",
            tx_type="SALES",
            category="임대료",
            counterparty="홍길동",
            item_name="월세",
            supply_amount=500_000,
            ref_id="rent_101_2026_03",
            branch_id=self.branch
        )
        tx_b = domain.VatTransaction.create_tx(
            date="2026-03-01",
            tx_type="SALES",
            category="임대료",
            counterparty="홍길동",
            item_name="월세",
            supply_amount=500_000,
            ref_id="rent_101_2026_03",
            branch_id=self.branch
        )
        self.assertEqual(tx_a.id, tx_b.id)
        self.assertEqual(domain.VatTransaction.count(self.branch), 1)


if __name__ == "__main__":
    unittest.main()
