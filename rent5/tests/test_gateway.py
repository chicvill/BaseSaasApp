"""
tests/test_gateway.py — rent5 를 서버처럼 게이트웨이(core.gateway)에 올려서 확인한다.

  - /rent5/ 화면 · /api/rent5/ API 가 마운트된다
  - 오류가 500 이 아니라 올바른 상태코드 + 안내 JSON 으로 나온다 (ApiError → DomainError)
  - 가입부터 예약 · 계약금 송금(계약 종료)까지 게이트웨이 경로로 한 번 끝까지 간다
실행: py -m unittest discover -s rent5/tests   (저장소 루트에서)
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
os.environ.setdefault("BASS_SEED_PASSWORD", "seed-test-pw")

from fastapi.testclient import TestClient

from core.gateway import create_gateway

API = "/api/rent5"


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rent5_gw_")
        env = mock.patch.dict(os.environ, {"RENT5_DATA_DIR": self.tmp})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.client = TestClient(create_gateway(db_path=":memory:", only=["rent5"]))

    def call(self, method, path, auth=None, status=200, **body):
        res = self.client.request(method, f"{API}{path}", headers=auth or {}, json=body or None)
        self.assertEqual(res.status_code, status, f"{method} {path}: {res.text}")
        return res.json()

    def signup(self, username, name, phone):
        r = self.call("POST", "/signup", username=username, name=name, phone=phone, password="pass-word-1")
        return {"Authorization": f"Bearer {r['token']}"}


class TestMount(GatewayCase):
    def test_pages_and_assets_are_served_under_rent5(self):
        for path in ("/rent5/", "/rent5/index.html", "/rent5/login.html", "/rent5/contract.html", "/rent5/app.js", "/rent5/style.css"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertIn("Content-Security-Policy", self.client.get("/rent5/index.html").text)      # 게이트웨이에서도 meta CSP

    def test_app_is_healthy_in_catalog(self):
        health = self.client.get("/api/health").json()
        self.assertIn("rent5", str(health))
        self.assertNotIn("DEGRADED", str(health))
        self.assertTrue(any(a["app_id"] == "rent5" for a in self.client.get("/api/apps").json()["apps"]))

    def test_screens_pick_the_gateway_api_prefix(self):
        js = self.client.get("/rent5/app.js").text
        self.assertIn('"/api/rent5"', js)
        self.assertIn("startsWith(\"/rent5/\")", js)


class TestErrorsAreNot500(GatewayCase):
    def test_status_codes_and_messages_survive_the_gateway(self):
        self.call("GET", "/me", status=401)
        self.call("POST", "/login", status=401, username="nobody", password="pass-word-1")
        self.call("POST", "/signup", status=422, username="Ab", name="가", phone="010-1111-2222", password="pass-word-1")
        auth = self.signup("owner1", "김임대", "010-1111-2222")
        self.call("POST", "/signup", status=409, username="owner1", name="가", phone="010-1111-2222", password="pass-word-1")
        self.call("GET", "/listings/nope", status=404)
        self.call("GET", "/contracts/nope", auth, status=404)
        res = self.client.post(f"{API}/signup", content=b"{broken", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)
        body = self.call("POST", "/login", status=401, username="owner1", password="wrong-pass-1")
        self.assertFalse(body["success"])
        self.assertIn("맞지 않습니다", body["message"])
        self.assertIn("level", body)                                   # 플랫폼의 안내 배너 규격

    def test_lockout_returns_429(self):
        self.signup("owner1", "김임대", "010-1111-2222")
        for _ in range(5):
            self.call("POST", "/login", status=401, username="owner1", password="wrong-pass-1")
        self.call("POST", "/login", status=429, username="owner1", password="pass-word-1")


class TestFlowThroughGateway(GatewayCase):
    def test_signup_to_completed(self):
        landlord = self.signup("owner1", "김임대", "010-1111-2222")
        tenant = self.signup("tenant1", "이임차", "010-3333-4444")
        lid = self.call("POST", "/listings", landlord, address="서울 시크로 12", room="302호", deposit=10_000_000, rent=500_000,
                        mgmt=50_000, available_from="2026-12-01", contact_phone="010-1111-2222")["listing"]["id"]
        self.assertEqual(len(self.call("GET", "/listings")["listings"]), 1)
        cid = self.call("POST", f"/listings/{lid}/reserve", tenant)["contract_id"]
        self.call("POST", f"/listings/{lid}/reserve", self.signup("tenant2", "박다른", "010-5555-6666"), status=409)
        self.call("POST", f"/contracts/{cid}/messages", tenant, text="방 잘 봤습니다")
        self.call("POST", f"/contracts/{cid}/confirm-landlord-info", tenant)
        self.call("POST", f"/contracts/{cid}/confirm-tenant-info", landlord)
        self.call("POST", f"/contracts/{cid}/registry", landlord, owner_match=True, seizure=False, mortgage=0)
        self.call("POST", f"/contracts/{cid}/confirm-registry", tenant)
        self.call("POST", f"/contracts/{cid}/account", landlord, bank="국민은행", number="123-456-789012", holder="김임대")
        c = self.call("POST", f"/contracts/{cid}/pay", tenant)["contract"]
        self.assertEqual(c["status"], "completed")
        self.assertEqual(self.call("GET", f"/contracts/{cid}/document", tenant)["document"]["final_hash"], c["final_hash"])
        self.assertEqual(self.call("GET", "/listings")["listings"], [])
        self.call("GET", f"/contracts/{cid}", self.signup("stranger1", "제삼자", "010-7777-8888"), status=404)

    def test_data_lands_in_the_configured_folder(self):
        self.signup("owner1", "김임대", "010-1111-2222")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "users.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "secret.key")))


if __name__ == "__main__":
    unittest.main()
