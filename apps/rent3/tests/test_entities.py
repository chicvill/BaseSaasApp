"""apps/rent3/tests/test_entities.py — 8대 표준 엔티티 및 워크플로우 단위 테스트.
"""
import unittest
from apps.rent3.entities import (
    Admin, Lessor, Tenant, BuildingEntity, RoomEntity, ContractEntity,
    Pay, Outlay, Rent3EntityManager, Rent3WorkflowEngine, seed_default_entities_if_empty
)


class TestRent3EntitiesAndWorkflow(unittest.TestCase):
    def setUp(self):
        seed_default_entities_if_empty()

    def test_eight_standard_entities_creation_and_fields(self):
        """8대 표준 엔티티의 모든 필수 필드가 정확히 존재하는지 검증."""
        # 1. Admin 관리인
        admins = Admin.all()
        self.assertGreater(len(admins), 0)
        admin = admins[0]
        self.assertTrue(hasattr(admin, "name"))
        self.assertTrue(hasattr(admin, "phone"))
        self.assertTrue(hasattr(admin, "account_number"))
        self.assertTrue(hasattr(admin, "business_number"))
        self.assertTrue(hasattr(admin, "email"))

        # 2. Lessor 임대인
        lessors = Lessor.all()
        self.assertGreater(len(lessors), 0)
        lessor = lessors[0]
        self.assertTrue(hasattr(lessor, "id_lessor"))
        self.assertTrue(hasattr(lessor, "name"))
        self.assertTrue(hasattr(lessor, "phone"))
        self.assertTrue(hasattr(lessor, "account_number"))
        self.assertTrue(hasattr(lessor, "app_fee"))
        self.assertTrue(hasattr(lessor, "joined_at"))
        self.assertTrue(hasattr(lessor, "business_number"))
        self.assertTrue(hasattr(lessor, "email"))

        # 3. Tenant 임차인
        tenants = Tenant.all()
        self.assertGreater(len(tenants), 0)
        tenant = tenants[0]
        self.assertTrue(hasattr(tenant, "id_tenant"))
        self.assertTrue(hasattr(tenant, "name"))
        self.assertTrue(hasattr(tenant, "phone"))
        self.assertTrue(hasattr(tenant, "account_number"))
        self.assertTrue(hasattr(tenant, "car_number"))
        self.assertTrue(hasattr(tenant, "doorlock_pwd"))
        self.assertTrue(hasattr(tenant, "business_number"))
        self.assertTrue(hasattr(tenant, "email"))

        # 4. Building 건물
        buildings = BuildingEntity.all()
        self.assertGreater(len(buildings), 0)
        bldg = buildings[0]
        self.assertTrue(hasattr(bldg, "id_building"))
        self.assertTrue(hasattr(bldg, "building_name"))
        self.assertTrue(hasattr(bldg, "address"))
        self.assertTrue(hasattr(bldg, "id_lessor"))

        # 5. Room 임대상세
        rooms = RoomEntity.all()
        self.assertGreater(len(rooms), 0)
        room = rooms[0]
        self.assertTrue(hasattr(room, "id_room"))
        self.assertTrue(hasattr(room, "name"))
        self.assertTrue(hasattr(room, "id_building"))
        self.assertTrue(hasattr(room, "id_tenant"))
        self.assertTrue(hasattr(room, "registry_checked"))
        self.assertTrue(hasattr(room, "status_description"))
        self.assertTrue(hasattr(room, "option_list"))
        self.assertIsInstance(room.option_list, list)

        # 6. Contract 계약
        contracts = ContractEntity.all()
        self.assertGreater(len(contracts), 0)
        ct = contracts[0]
        self.assertTrue(hasattr(ct, "id_contract"))
        self.assertTrue(hasattr(ct, "id_room"))
        self.assertTrue(hasattr(ct, "id_tenant"))
        self.assertTrue(hasattr(ct, "contract_date"))
        self.assertTrue(hasattr(ct, "contract_deposit"))
        self.assertTrue(hasattr(ct, "move_in_date"))
        self.assertTrue(hasattr(ct, "lease_term"))
        self.assertTrue(hasattr(ct, "deposit"))
        self.assertTrue(hasattr(ct, "monthly_rent"))
        self.assertTrue(hasattr(ct, "maintenance_fee"))
        self.assertTrue(hasattr(ct, "vat"))
        self.assertTrue(hasattr(ct, "rent_type"))
        self.assertTrue(hasattr(ct, "payment_timing"))
        self.assertTrue(hasattr(ct, "deposit_capture_img"))

        # 7. Pay 수납
        pays = Pay.all()
        self.assertGreater(len(pays), 0)
        pay = pays[0]
        self.assertTrue(hasattr(pay, "id_pay"))
        self.assertTrue(hasattr(pay, "id_room"))
        self.assertTrue(hasattr(pay, "billing_month"))
        self.assertTrue(hasattr(pay, "paid_date"))
        self.assertTrue(hasattr(pay, "monthly_rent"))
        self.assertTrue(hasattr(pay, "maintenance_fee"))
        self.assertTrue(hasattr(pay, "vat"))

        # 8. Outlay 지출
        outlays = Outlay.all()
        self.assertGreater(len(outlays), 0)
        outlay = outlays[0]
        self.assertTrue(hasattr(outlay, "id_outlay"))
        self.assertTrue(hasattr(outlay, "id_room"))
        self.assertTrue(hasattr(outlay, "summary"))
        self.assertTrue(hasattr(outlay, "expense_date"))
        self.assertTrue(hasattr(outlay, "amount"))

    def test_entity_crud_and_search(self):
        """Rent3EntityManager를 통한 생성, 검색, 편집, 삭제 테스트."""
        # 신규 임차인 생성
        created_tenant = Rent3EntityManager.create("tenant", {
            "name": "박테스트",
            "phone": "010-9999-8888",
            "account_number": "하나 111-222-33333",
            "car_number": "55오 1234",
            "doorlock_pwd": "7777*",
            "email": "test@tenant.com"
        })
        self.assertEqual(created_tenant.name, "박테스트")

        # 검색
        search_res = Rent3EntityManager.search("tenant", query="박테스트")
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0].phone, "010-9999-8888")

        # 편집
        updated_tenant = Rent3EntityManager.update("tenant", created_tenant.id_tenant, {
            "car_number": "99구 9999"
        })
        self.assertEqual(updated_tenant.car_number, "99구 9999")

        # 삭제
        del_res = Rent3EntityManager.delete("tenant", created_tenant.id_tenant)
        self.assertTrue(del_res)
        self.assertIsNone(Rent3EntityManager.find_by_custom_id("tenant", created_tenant.id_tenant))

    def test_event_lifecycle_workflow(self):
        """16단계 이벤트 라이프사이클 비즈니스 흐름 전체 순차 실행 검증."""
        # 1. 앱 게시 정보 확인
        pub_info = Rent3WorkflowEngine.step1_app_published_info()
        self.assertEqual(pub_info["status"], "PUBLISHED_ACTIVE")

        # 2. 임대인 회원 가입
        lessor = Rent3WorkflowEngine.step2_register_lessor(
            name="최임대",
            phone="010-3333-7777",
            account_number="기업 010-3333-7777",
            business_number="111-22-33333",
            email="choi@landlord.com",
            branch="main"
        )
        self.assertIn("lessor_", lessor.id_lessor)

        # 3. 임대 매물 게시
        room = Rent3WorkflowEngine.step3_publish_room_listing(
            id_building="bldg_test",
            name="501호",
            deposit=10000000,
            monthly_rent=700000,
            maintenance_fee=70000,
            status_description="고층 파노라마 뷰"
        )
        self.assertIn("room_", room.id_room)
        self.assertTrue(room.is_published)

        # 4. 임차인 회원 가입
        tenant = Rent3WorkflowEngine.step4_register_tenant(
            name="정임차",
            phone="010-4444-8888",
            account_number="농협 302-0000-1111",
            car_number="88하 8888",
            email="jung@tenant.com"
        )
        self.assertIn("tenant_", tenant.id_tenant)

        # 5. 등기부등본 확인 및 크로스체크 DB 등록
        check_res = Rent3WorkflowEngine.step5_verify_and_crosscheck_registry(
            id_room=room.id_room,
            id_lessor=lessor.id_lessor,
            id_tenant=tenant.id_tenant,
            issue_number="REG-2026-TEST501",
            mortgage_amount=0
        )
        self.assertTrue(check_res["clean_title"])
        self.assertTrue(check_res["verified"])

        # 6. 계약 체결 (id_contract 등록)
        contract = Rent3WorkflowEngine.step8_sign_contract(
            id_room=room.id_room,
            id_tenant=tenant.id_tenant,
            move_in_date="2026-11-01",
            lease_term="24개월",
            deposit=10000000,
            monthly_rent=700000,
            maintenance_fee=70000
        )
        self.assertIn("ct_", contract.id_contract)
        self.assertEqual(contract.status, "CONFIRMED")

        # 7. 계약금 송금 및 전자 영수증
        dep_res = Rent3WorkflowEngine.step9_remit_contract_deposit(
            id_contract=contract.id_contract,
            capture_img_url="https://receipt.test/img.png"
        )
        self.assertEqual(dep_res["status"], "DEPOSIT_CONFIRMED")

        # 8. 전입 및 입주 (도어락 비번, 공과금)
        checkin_res = Rent3WorkflowEngine.step10_process_checkin(
            id_room=room.id_room,
            id_tenant=tenant.id_tenant,
            doorlock_pwd="7890*"
        )
        self.assertEqual(checkin_res["status"], "CHECKIN_COMPLETED")

        # 9. 1개월 후 임차료 송금 & 수납 등록 (id_pay)
        pay = Rent3WorkflowEngine.step12_record_monthly_pay(
            id_room=room.id_room,
            billing_month="2026.11",
            monthly_rent=700000,
            maintenance_fee=70000
        )
        self.assertEqual(pay.total_amount, 770000)

        # 10. 경비 지출 등록 (id_outlay)
        outlay = Rent3WorkflowEngine.step13_record_outlay(
            id_room=room.id_room,
            summary="복도 센서등 교체",
            amount=25000,
            category="repair_maint"
        )
        self.assertEqual(outlay.amount, 25000)

        # 11. 세무/소득세 결산 자료 자동 생성
        tax_res = Rent3WorkflowEngine.step14_generate_tax_materials(year=2026)
        self.assertGreater(tax_res["total_income"], 0)
        self.assertGreater(tax_res["total_expense"], 0)


if __name__ == "__main__":
    unittest.main()
