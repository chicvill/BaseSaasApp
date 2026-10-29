"""
tests/test_rent3_pool_suite.py — 불변 BaseSaasApp 기반 Rent3 공동 JSON Data Pool & 5대 분리 앱 종합 검증 스위트.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient
from core.gateway import create_gateway
from apps._rent3_core import POOL_COLLECTIONS, ensure_pool_seeded, pool, use_pool_dir

RENT3_SPLIT_APPS = ["rent3", "rent3_admin", "rent3_lessor", "rent3_tenant", "rent3_contract"]


class TestRent3JsonDataPoolSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["RENT3_TODAY"] = "2026-10-29"
        cls.temp_pool = tempfile.mkdtemp(prefix="rent3_pool_test_")
        use_pool_dir(cls.temp_pool)
        ensure_pool_seeded(force=True)
        cls.app = create_gateway(db_path=":memory:")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("RENT3_POOL_DIR", None)
        os.environ.pop("RENT3_TODAY", None)
        if os.path.exists(cls.temp_pool):
            shutil.rmtree(cls.temp_pool, ignore_errors=True)

    def _read_disk_json(self, collection: str) -> dict:
        p = os.path.join(self.temp_pool, f"{collection}.json")
        self.assertTrue(os.path.exists(p), f"{collection}.json 파일이 디스크에 생성되지 않았습니다.")
        with open(p, "r", encoding="utf-8") as fp:
            return json.load(fp)

    def test_01_immutable_core_zero_changes(self):
        """1. 불변의 BaseSaasApp 코어(core/, run.py)가 단 1줄도 수정되지 않았음을 검증."""
        res = subprocess.run(
            ["git", "diff", "--name-only", "core", "run.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.stdout.strip(), "", f"불변 코어가 수정되었습니다: {res.stdout}")

    def test_02_zero_touch_mounting_and_manuals(self):
        """2. 5대 분리 앱 자동 마운트, _rent3_core 스킵, 웹 화면 및 manual.html 서빙 검증."""
        cat = self.client.get("/api/apps").json()
        self.assertTrue(cat["success"])
        mounted_ids = [a["app_id"] for a in cat["apps"]]
        for app_id in RENT3_SPLIT_APPS:
            self.assertIn(app_id, mounted_ids)
        self.assertNotIn("_rent3_core", mounted_ids)

        # 모든 JSON 컬렉션 파일 디스크 실존 검증
        for col in POOL_COLLECTIONS:
            data = self._read_disk_json(col)
            self.assertEqual(data["collection"], col)

        for path in [
            "/rent3/pool.html",
            "/rent3_admin/",
            "/rent3_admin/manual.html",
            "/rent3_lessor/",
            "/rent3_lessor/manual.html",
            "/rent3_tenant/",
            "/rent3_tenant/manual.html",
            "/rent3_contract/",
            "/rent3_contract/manual.html",
        ]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, f"정적 페이지 서빙 실패: {path}")

    def test_03_cross_app_realtime_json_pool_and_cosign_lifecycle(self):
        """3. 관리인 -> 임대인 -> 임차인 -> 공동계약 앱 간 실시간 JSON 파일 동기화 & Revision Lock 검증."""
        # [A] 관리인 앱에서 신규 임대인/건물 온보딩 -> users.json, buildings.json 실시간 업데이트 확인
        r_admin = self.client.post("/api/rent3_admin/lessors", json={
            "username": "yeoksam",
            "name": "장역삼",
            "phone": "010-1111-9999",
            "branch": "yeoksam",
            "building_name": "역삼스마트빌",
            "address": "서울시 강남구 역삼로 100",
            "app_fee": 33000,
        })
        self.assertEqual(r_admin.status_code, 200)
        bldg_disk = self._read_disk_json("buildings")
        self.assertTrue(any(b["branch"] == "yeoksam" for b in bldg_disk["items"]))

        # [B] 임대인 앱에서 시크빌(main) 신규 공실 205호 등록 -> rooms.json 업데이트 및 임차인 앱 즉시 노출 확인
        r_room = self.client.post("/api/rent3_lessor/rooms", json={
            "branch": "main",
            "room_no": "205",
            "deposit": 10000000,
            "monthly_rent": 550000,
            "maintenance_fee": 50000,
            "pay_timing": "후불",
            "is_published": True,
        })
        self.assertEqual(r_room.status_code, 200)
        rooms_disk = self._read_disk_json("rooms")
        self.assertTrue(any(r["room_no"] == "205" and r["branch"] == "main" for r in rooms_disk["items"]))

        listings_before = self.client.get("/api/rent3_tenant/listings?branch=main").json()["listings"]
        self.assertTrue(any(l["room_no"] == "205" for l in listings_before))

        # [C] 임차인 앱에서 205호 1:1 방문문의 -> ops.json 실시간 기록 및 임대인 대시보드 공유 확인
        r_inq = self.client.post("/api/rent3_tenant/inquiries", json={
            "branch": "main",
            "room_no": "205",
            "sender_name": "박신규",
            "message": "오늘 저녁 205호 계약하러 방문하겠습니다.",
        })
        self.assertEqual(r_inq.status_code, 200)
        lessor_dash = self.client.get("/api/rent3_lessor/dashboard?branch=main").json()
        self.assertTrue(any(c["sender_name"] == "박신규" for c in lessor_dash["chats"]))

        # [D] 공동 임대계약 앱(rent3_contract)에서 5단계 작성 + Revision Lock 위변조 방지 검증
        r_open = self.client.post("/api/rent3_contract/sessions", json={
            "branch": "main",
            "room_no": "205",
            "tenant_name": "박신규",
            "tenant_phone": "010-2050-2050",
            "mode": "NEW",
        })
        self.assertEqual(r_open.status_code, 200)
        sess = r_open.json()["session"]
        sid = sess["id"]

        # 6자리 참여코드로 임차인 조회 검증
        r_join = self.client.get(f"/api/rent3_contract/sessions/{sess['session_code']}")
        self.assertEqual(r_join.status_code, 200)
        self.assertEqual(r_join.json()["session"]["id"], sid)

        # Step 1 & Step 2 상호 확인 잠금
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step1", json={"role": "tenant"})
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step2", json={"role": "tenant"})

        # Step 3: v1 양측 합의 완료 후, 임대인이 월세를 55만 -> 54만으로 수정하면 양측 합의가 자동 초기화되는지 검증!
        self.client.post(f"/api/rent3_contract/sessions/{sid}/terms", json={
            "role": "lessor",
            "updates": {"move_in_date": "2026-10-15", "pay_timing": "후불"},
        })
        cur_rev = self.client.get(f"/api/rent3_contract/sessions/{sid}").json()["session"]["revision"]
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": cur_rev})
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "tenant", "expected_revision": cur_rev})

        # 조건 1개 수정 -> Revision Lock 발동 검증!
        r_edit = self.client.post(f"/api/rent3_contract/sessions/{sid}/terms", json={
            "role": "lessor",
            "updates": {"monthly_rent": 540000},
        }).json()
        self.assertTrue(r_edit["changed"])
        self.assertGreater(r_edit["session"]["revision"], cur_rev)
        self.assertFalse(r_edit["session"]["step3_lessor_agreed"])
        self.assertFalse(r_edit["session"]["step3_tenant_agreed"])

        # 새 리비전에 양측 재동의 후 Step 4 전자서명 & Step 5 최종 체결
        new_rev = r_edit["session"]["revision"]
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": new_rev})
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "tenant", "expected_revision": new_rev})

        self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={"role": "lessor", "signature": "김임대(전자서명)", "expected_revision": new_rev})
        r_sig = self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={"role": "tenant", "signature": "박신규(전자서명)", "expected_revision": new_rev}).json()
        self.assertTrue(bool(r_sig["session"]["integrity_hash"]))

        r_seal = self.client.post(f"/api/rent3_contract/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER"})
        self.assertEqual(r_seal.status_code, 200)
        sealed = r_seal.json()
        self.assertTrue(sealed["contract"]["fixed_date_no"].startswith("FIX-"))

        # 디스크 JSON Data Pool 4개 파일 동시 업데이트 확인
        ct_disk = self._read_disk_json("contracts")
        self.assertTrue(any(c["id"] == sealed["contract"]["id"] for c in ct_disk["items"]))

        # 공실 쇼케이스 자동 마감 확인
        listings_after = self.client.get("/api/rent3_tenant/listings?branch=main").json()["listings"]
        self.assertFalse(any(l["room_no"] == "205" for l in listings_after))

        # 후불 입주월(2026-10) 가짜 체납 0원(EXEMPT_POSTPAID) 검증!
        dash_after = self.client.get("/api/rent3_lessor/dashboard?branch=main&up_to_ym=2026-10").json()
        room205 = next(r for r in dash_after["rooms"] if r["room_no"] == "205")
        self.assertEqual(room205["ledger"]["total_unpaid"], 0)
        self.assertEqual(room205["ledger"]["months"][0]["status"], "EXEMPT_POSTPAID")

    def test_04_fifo_sms_collection_idempotency_and_checkout_relist(self):
        """4. rent2 계승 은행문자 파싱, FIFO 순차 상계, 중복입금 차단, 5% 재계약 상한, 퇴실 후 공실 자동 복귀 검증."""
        # 시크빌 202호: 2026-08 입주(후불) -> 8월 비과세, 9월(57만) + 10월(57만) = 총 114만 미납 상태
        sms_text = "[신한은행] 10/28 202호 정서연 700,000원 입금 잔액 9,500,000원"
        r_sms = self.client.post("/api/rent3_lessor/payments/sms-paste", json={
            "branch": "main",
            "sms_text": sms_text,
            "up_to_ym": "2026-10",
        })
        self.assertEqual(r_sms.status_code, 200)
        body = r_sms.json()
        self.assertEqual(body["level"], "diff")
        self.assertEqual(body["ledger"]["total_unpaid"], 440000)
        # 9월은 완납(PAID), 10월은 부분납부(PARTIAL)인지 FIFO 검증
        months_by_ym = {m["ym"]: m for m in body["ledger"]["months"]}
        self.assertEqual(months_by_ym["2026-08"]["status"], "EXEMPT_POSTPAID")
        self.assertEqual(months_by_ym["2026-09"]["status"], "PAID")
        self.assertEqual(months_by_ym["2026-10"]["status"], "PARTIAL")
        self.assertEqual(months_by_ym["2026-10"]["unpaid"], 440000)

        # 동일 문자 재전송 -> 409 중복 입금 차단 검증
        r_dup = self.client.post("/api/rent3_lessor/payments/sms-paste", json={
            "branch": "main",
            "sms_text": sms_text,
            "up_to_ym": "2026-10",
        })
        self.assertEqual(r_dup.status_code, 409)
        self.assertEqual(r_dup.json()["level"], "warning")

        # 임차인 앱에서 잔여 440,000원 온라인 납부 -> 완납 및 17% 세액공제 증명서 확인
        r_tpay = self.client.post("/api/rent3_tenant/myhome/tenant_202/pay", json={
            "amount": 440000,
            "method": "KAKAOPAY",
        })
        self.assertEqual(r_tpay.status_code, 200)
        self.assertEqual(r_tpay.json()["ledger"]["total_unpaid"], 0)

        myhome = self.client.get("/api/rent3_tenant/myhome/tenant_202").json()
        self.assertEqual(myhome["certificate"]["total_paid"], 1140000)
        self.assertGreater(myhome["certificate"]["estimated_tax_credit_17pct"], 0)

        # 재계약 5% 상한 초과 시 차단 검증 (201호 기존 월세 50만 -> 55만(+10%) 시도 시 차단!)
        r_ren = self.client.post("/api/rent3_contract/sessions", json={
            "branch": "main",
            "room_no": "201",
            "mode": "RENEWAL",
        }).json()["session"]
        r_cap_fail = self.client.post(f"/api/rent3_contract/sessions/{r_ren['id']}/terms", json={
            "role": "lessor",
            "updates": {"monthly_rent": 550000},
        })
        self.assertEqual(r_cap_fail.status_code, 422)
        self.assertEqual(r_cap_fail.json()["level"], "diff")

        # 202호 퇴실 정산 산출 -> 임차인 동의 -> 임대인 반환 완료 -> 호실 자동 공실 복귀 검증
        r_co = self.client.post("/api/rent3_lessor/checkout/calculate", json={
            "branch": "main",
            "room_no": "202",
            "utility_settlement": 30000,
            "cleaning_fee": 70000,
        }).json()["settlement"]
        self.assertEqual(r_co["refund_amount"], 5000000 - 100000)

        r_ag = self.client.post(f"/api/rent3_tenant/checkout/{r_co['id']}/agree", json={
            "bank_account": "토스 1000-22-334455",
            "signature": "정서연(동의)",
        })
        self.assertEqual(r_ag.status_code, 200)

        r_done = self.client.post(f"/api/rent3_lessor/checkout/{r_co['id']}/complete")
        self.assertEqual(r_done.status_code, 200)

        # 퇴실 즉시 임차인 앱 공실 쇼케이스에 202호 자동 복귀 확인!
        listings_relisted = self.client.get("/api/rent3_tenant/listings?branch=main").json()["listings"]
        self.assertTrue(any(l["room_no"] == "202" for l in listings_relisted))


if __name__ == "__main__":
    unittest.main()
