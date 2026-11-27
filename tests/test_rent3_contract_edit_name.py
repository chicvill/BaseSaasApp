"""
tests/test_rent3_contract_edit_name.py — 임대인 계약내용 수정 모달 및 API 임차인명 수정·동기화 검증.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRent3ContractEditTenantName(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rent3_edit_name_")
        cls._env_patch = mock.patch.dict(
            os.environ,
            {
                "RENT3_POOL_DIR": cls._tmp,
                "RENT3_BACKUP_DIR": os.path.join(cls._tmp, "backups"),
            },
        )
        cls._env_patch.start()
        from core.gateway import create_gateway
        from apps.rent_dist._rent3_core import ensure_pool_seeded, use_pool_dir
        use_pool_dir(cls._tmp)
        ensure_pool_seeded(force=True)
        cls.app = create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.stop()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _lessor_headers(self, username: str) -> dict:
        from apps.rent_dist._rent3_core import Lessor
        from apps.rent_dist._rent3_core.authz import lessor_token
        lsr = Lessor.first(username=username)
        assert lsr is not None
        return {"Authorization": f"Bearer {lessor_token(lsr)}"}

    def test_01_edit_contract_updates_tenant_name_and_syncs_linked_objects(self):
        """계약내용 수정 시 임차인명이 계약·임차인·만기확인·미완료 전출정산에 모두 동기화되고 송금자명 중복이 제거된다."""
        from apps.rent_dist._rent3_core import CheckOutSettlement, Contract, ExpiryRenewalCheck, Room, Tenant
        chic = self._lessor_headers("chic")
        sunho = self._lessor_headers("sunho")
        rm = Room.first(branch="main", room_no="201")
        assert rm is not None and rm.active_contract_id
        ct = Contract.require(rm.active_contract_id)
        old_name = ct.tenant_name

        # 만기확인·미완료 전출정산 객체 생성 후 이름 동기화 확인
        chk = ExpiryRenewalCheck(
            branch="main", id_room=rm.id, room_no=rm.room_no, id_contract=ct.id,
            id_tenant=ct.id_tenant, tenant_name=old_name, expiry_date=ct.expiry_date, status="NOTIFIED",
        ).save(action="t")
        co = CheckOutSettlement(
            branch="main", id_room=rm.id, room_no=rm.room_no, id_contract=ct.id,
            id_tenant=ct.id_tenant, tenant_name=old_name, status="DRAFT", deposit_original=ct.deposit,
        ).save(action="t")
        try:
            base_body = {
                "branch": "main",
                "contract_id": ct.id,
                "deposit": ct.deposit,
                "monthly_rent": ct.monthly_rent,
                "maintenance_fee": ct.maintenance_fee,
                "tenant_phone": ct.tenant_phone or "",
            }
            # 1) 타 지점 임대인은 403 차단
            self.assertEqual(
                self.client.post("/api/rent3_lessor/contracts/edit", headers=sunho, json={**base_body, "tenant_name": "타지점변경"}).status_code,
                403,
            )
            # 2) 빈 임차인명 거부(400)
            bad = self.client.post("/api/rent3_lessor/contracts/edit", headers=chic, json={**base_body, "tenant_name": "   "})
            self.assertEqual(bad.status_code, 400, bad.text)

            # 3) 정상 수정: 계약·임차인·만기확인·전출정산 동기화 + 송금자명에서 새 임차인명 자동 중복 제거
            ok = self.client.post(
                "/api/rent3_lessor/contracts/edit",
                headers=chic,
                json={**base_body, "tenant_name": "  정수정임차인  ", "payer_names": ["정수정임차인", "가족송금"]},
            )
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["contract"]["tenant_name"], "정수정임차인")
            ct2 = Contract.require(ct.id)
            self.assertEqual(ct2.tenant_name, "정수정임차인")
            self.assertEqual(ct2.payer_names, ["가족송금"])
            if ct2.id_tenant:
                t2 = Tenant.require(ct2.id_tenant)
                self.assertEqual(t2.name, "정수정임차인")
            chk2 = ExpiryRenewalCheck.get(chk.id)
            assert chk2 is not None
            self.assertEqual(chk2.tenant_name, "정수정임차인")
            co2 = CheckOutSettlement.get(co.id)
            assert co2 is not None
            self.assertEqual(co2.tenant_name, "정수정임차인")

            # 4) tenant_name 미전달 시 기존 이름 그대로 유지 (하위 호환)
            keep = self.client.post("/api/rent3_lessor/contracts/edit", headers=chic, json={**base_body, "memo": "메모만 수정"})
            self.assertEqual(keep.status_code, 200, keep.text)
            self.assertEqual(Contract.require(ct.id).tenant_name, "정수정임차인")
        finally:
            if ExpiryRenewalCheck.get(chk.id):
                ExpiryRenewalCheck.require(chk.id).delete(action="t")
            if CheckOutSettlement.get(co.id):
                CheckOutSettlement.require(co.id).delete(action="t")
            self.client.post(
                "/api/rent3_lessor/contracts/edit",
                headers=chic,
                json={
                    "branch": "main",
                    "contract_id": ct.id,
                    "deposit": ct.deposit,
                    "monthly_rent": ct.monthly_rent,
                    "maintenance_fee": ct.maintenance_fee,
                    "tenant_name": old_name,
                    "tenant_phone": ct.tenant_phone or "",
                    "payer_names": [],
                },
            )

    def test_02_lessor_contract_modal_has_tenant_name_input(self):
        """임대인 계약내용 모달(contracts.js)에 임차인명 입력칸(#cmName)과 tenant_name 전송 로직이 있다."""
        p = os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "contracts.js")
        with open(p, encoding="utf-8") as fp:
            js = fp.read()
        for needle in ('"cmName"', '"임차인명"', "tenant_name: tenantName", "임차인명을 입력해 주세요."):
            self.assertIn(needle, js, needle)


if __name__ == "__main__":
    unittest.main()
