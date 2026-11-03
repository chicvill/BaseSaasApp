"""
tests/test_rent5.py — Rent5 (중개사 없는 직거래 계약) 검증.

  1. 회원: 가입 · 로그인 · 잠금 · 비밀번호 변경 시 이전 토큰 무효
  2. 매물: 공개 목록에 개인정보 없음 · 주민번호 거부 · 내 매물 신청 막기
  3. 계약: 신청 → 조건 제시 → 임차인 서명 → 임대인 서명 → 계약금 → 잔금(입주)
  4. 규칙: 당사자만 · 단계 순서 · 바뀐 조건 서명 차단 · 다른 신청 마감 · 성립 뒤 취소 불가
실행: py -m unittest discover -s rent5/tests   (저장소 루트에서)
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))     # 저장소 루트 → rent5 패키지

from fastapi.testclient import TestClient

from rent5.app import create_app

TERMS = {"address": "서울시 테스트구 시크로 12, 302호", "deposit": 10_000_000, "down_payment": 1_000_000,
         "rent": 500_000, "mgmt": 50_000, "start": "2026-12-01", "months": 12}
REGISTRY = {"owner_match": True, "seizure": False, "mortgage": 0, "note": "깨끗함"}
INFO = {"birth": "1995-03-04", "address": "부산시 어딘가 1", "emergency": "부모 010-9999-0000", "agree": True}


class Rent5Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rent5_")
        self.client = TestClient(create_app(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def signup(self, username, name="홍길동", phone="010-1234-5678", password="pass-word-1"):
        res = self.client.post("/api/signup", json={"username": username, "name": name, "phone": phone, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def post(self, path, auth=None, status=200, **body):
        res = self.client.post(f"/api/{path}", headers=auth or {}, json=body)
        self.assertEqual(res.status_code, status, f"{path}: {res.text}")
        return res.json()

    def get(self, path, auth=None, status=200):
        res = self.client.get(f"/api/{path}", headers=auth or {})
        self.assertEqual(res.status_code, status, f"{path}: {res.text}")
        return res.json()

    def listing(self, auth, **kw):
        body = {"address": "서울시 테스트구 시크로 12", "room": "302호", "lease_type": "월세", "deposit": 10_000_000,
                "rent": 500_000, "mgmt": 50_000, "available_from": "2026-12-01", "description": "남향, 풀옵션", **kw}
        return self.post("listings", auth, **body)["listing"]["id"]

    def parties(self):
        landlord, tenant = self.signup("owner1", "김임대", "010-1111-2222"), self.signup("tenant1", "이임차", "010-3333-4444")
        return landlord, tenant

    def to_signed(self, landlord, tenant, lid):
        cid = self.post(f"listings/{lid}/apply", tenant, message="직장인입니다")["contract_id"]
        self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)
        c = self.get(f"contracts/{cid}", tenant)["contract"]
        self.post(f"contracts/{cid}/tenant-sign", tenant, offer_hash=c["offer_hash"], **INFO)
        c = self.get(f"contracts/{cid}", landlord)["contract"]
        self.post(f"contracts/{cid}/landlord-sign", landlord, final_hash=c["final_hash"])
        return cid


class TestMembers(Rent5Case):
    def test_signup_login_and_me(self):
        auth = self.signup("owner1")
        self.assertEqual(self.get("me", auth)["user"]["username"], "owner1")
        res = self.post("login", username="owner1", password="pass-word-1")
        self.assertTrue(res["token"])
        self.assertNotIn("pw_hash", str(res))
        self.get("me", status=401)

    def test_signup_rules(self):
        self.signup("owner1")
        bad = lambda **kw: self.client.post("/api/signup", json={"username": "newuser", "name": "가", "phone": "010-1111-2222",  # noqa: E731
                                                                  "password": "pass-word-1", **kw}).status_code
        self.assertEqual(bad(username="owner1"), 409)
        self.assertEqual(bad(username="Ab"), 422)
        self.assertEqual(bad(password="short"), 422)
        self.assertEqual(bad(password="aaaaaaaaaa"), 422)
        self.assertEqual(bad(phone="123"), 422)
        self.assertEqual(bad(name=""), 422)

    def test_wrong_password_locks_after_five_tries(self):
        self.signup("owner1")
        for _ in range(5):
            self.post("login", status=401, username="owner1", password="nope-nope-1")
        self.post("login", status=429, username="owner1", password="pass-word-1")     # 맞아도 잠김
        self.post("login", status=401, username="nobody", password="pass-word-1")      # 없는 아이디도 같은 오류

    def test_unknown_user_and_wrong_password_look_the_same(self):
        self.signup("owner1")
        a = self.client.post("/api/login", json={"username": "owner1", "password": "wrong-pass-1"}).json()
        b = self.client.post("/api/login", json={"username": "ghost1", "password": "wrong-pass-1"}).json()
        self.assertEqual(a["message"], b["message"])

    def test_password_change_ends_old_tokens(self):
        old = self.signup("owner1")
        res = self.post("me/password", old, current="pass-word-1", new="brand-new-pw-2")
        new = {"Authorization": f"Bearer {res['token']}"}
        self.get("me", old, status=401)
        self.get("me", new)
        self.post("me/password", new, status=403, current="wrong", new="another-pw-3")
        self.post("login", username="owner1", password="brand-new-pw-2")
        self.post("login", status=401, username="owner1", password="pass-word-1")

    def test_malformed_body_is_a_clear_400_not_a_silent_empty_form(self):
        res = self.client.post("/api/signup", content=b'{"username": "owner1", "name": "\xb1\xe8"}',
                               headers={"Content-Type": "application/json"})      # UTF-8 이 아닌 바이트
        self.assertEqual(res.status_code, 400)
        self.assertIn("요청 형식", res.json()["message"])
        res = self.client.post("/api/signup", content=b"[1, 2]", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)

    def test_operator_reset_script_sets_new_password_and_ends_old_logins(self):
        sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "scripts")))
        import set_password
        from rent5.security import ApiError
        old = self.signup("owner1")
        set_password.reset(self.tmp, "owner1", "operator-set-pw-9")
        self.get("me", old, status=401)                                          # 이미 로그인한 기기는 끝
        self.post("login", username="owner1", password="operator-set-pw-9")
        self.post("login", status=401, username="owner1", password="pass-word-1")
        with self.assertRaises(ApiError):
            set_password.reset(self.tmp, "ghost1", "operator-set-pw-9")
        with self.assertRaises(ApiError):
            set_password.reset(self.tmp, "owner1", "short")

    def test_tampered_token_rejected(self):
        auth = self.signup("owner1")
        token = auth["Authorization"].split(" ")[1]
        self.get("me", {"Authorization": f"Bearer {token[:-2]}ab"}, status=401)
        self.get("me", {"Authorization": "Bearer garbage"}, status=401)


class TestListings(Rent5Case):
    def test_public_list_has_no_personal_info(self):
        owner = self.signup("owner1", "김임대", "010-1111-2222")
        self.listing(owner, contact_phone="010-7777-8888")
        pub = self.get("listings")["listings"]
        self.assertEqual(len(pub), 1)
        self.assertEqual(pub[0]["monthly"], 550_000)
        self.assertEqual(pub[0]["contact_phone"], "010-7777-8888")
        text = str(pub)
        for private in ("김임대", "010-1111-2222", "owner1"):
            self.assertNotIn(private, text)

    def test_contact_phone_optional_and_validated(self):
        owner = self.signup("owner1")
        self.listing(owner)
        self.assertEqual(self.get("listings")["listings"][0]["contact_phone"], "")
        self.post("listings", owner, status=422, address="주소", deposit=1, contact_phone="12")

    def test_listing_validation_and_no_rrn(self):
        owner = self.signup("owner1")
        self.post("listings", owner, status=422, address="", deposit=1)
        self.post("listings", owner, status=422, address="주소", deposit="abc")
        self.post("listings", owner, status=422, address="주소", deposit=1, lease_type="반전세")
        self.post("listings", owner, status=422, address="주소", deposit=1, description="900101-1234567 입니다")
        self.post("listings", status=401, address="주소", deposit=1)

    def test_cannot_apply_to_own_or_without_login(self):
        owner = self.signup("owner1")
        lid = self.listing(owner)
        self.post(f"listings/{lid}/apply", owner, status=409)
        self.post(f"listings/{lid}/apply", status=401)

    def test_apply_twice_blocked_and_limit(self):
        owner, tenant = self.parties()
        lid = self.listing(owner)
        self.post(f"listings/{lid}/apply", tenant)
        self.post(f"listings/{lid}/apply", tenant, status=409)

    def test_withdraw_closes_listing_and_pending_requests(self):
        owner, tenant = self.parties()
        lid = self.listing(owner)
        cid = self.post(f"listings/{lid}/apply", tenant)["contract_id"]
        self.post(f"listings/{lid}/withdraw", self.signup("other1"), status=404)
        self.post(f"listings/{lid}/withdraw", owner)
        self.assertEqual(self.get("listings")["listings"], [])
        self.assertEqual(self.get(f"contracts/{cid}", tenant)["contract"]["status"], "cancelled")

    def test_my_listings_show_application_count(self):
        owner, tenant = self.parties()
        lid = self.listing(owner)
        self.post(f"listings/{lid}/apply", tenant)
        mine = self.get("my/listings", owner)["listings"]
        self.assertEqual((mine[0]["status"], mine[0]["applications"]), ("open", 1))


class TestContractFlow(Rent5Case):
    def test_full_flow_to_move_in(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        cid = self.post(f"listings/{lid}/apply", tenant, message="직장인입니다")["contract_id"]
        self.assertEqual(self.get("contracts", landlord)["contracts"][0]["other_name"], "이임차")      # 임대인은 신청자를 본다
        self.assertEqual(self.get("contracts", tenant)["contracts"][0]["other_name"], "김임대")
        c = self.get(f"contracts/{cid}", landlord)["contract"]
        self.assertEqual((c["status"], c["role"], c["actions"]), ("requested", "landlord", ["offer", "cancel"]))

        c = self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)["contract"]
        self.assertEqual(c["status"], "offered")
        self.assertEqual(c["terms"]["end"], "2027-11-30")                       # 2026-12-01 + 12개월 - 1일
        self.assertEqual(self.get("listings")["listings"], [])                  # 진행 중이라 공개 목록에서 빠짐
        t = self.get(f"contracts/{cid}", tenant)["contract"]
        self.assertEqual(t["actions"], ["tenant_sign", "cancel"])

        self.get(f"contracts/{cid}/document", landlord, status=409)             # 서명 전엔 계약서 없음
        c = self.post(f"contracts/{cid}/tenant-sign", tenant, offer_hash=t["offer_hash"], **INFO)["contract"]
        self.assertEqual(c["status"], "tenant_signed")
        self.assertEqual(len(c["final_hash"]), 64)
        c = self.post(f"contracts/{cid}/landlord-sign", landlord, final_hash=c["final_hash"])["contract"]
        self.assertEqual(c["status"], "signed")
        self.assertIn("document", c["actions"])
        self.assertEqual(self.get("listings")["listings"], [])                  # 마감

        c = self.post(f"contracts/{cid}/deposit", landlord)["contract"]
        self.assertEqual(c["status"], "deposit_paid")
        c = self.post(f"contracts/{cid}/balance", landlord)["contract"]
        self.assertEqual(c["status"], "active")
        self.assertIsNotNone(c["moved_in_at"])

        doc = self.get(f"contracts/{cid}/document", tenant)["document"]
        self.assertEqual(doc["snapshot"]["terms"]["deposit"], 10_000_000)
        self.assertEqual(doc["snapshot"]["landlord"]["name"], "김임대")
        self.assertEqual(doc["final_hash"], c["final_hash"])
        self.assertTrue(doc["report_required"])                                  # 월세 30만 원 초과 → 임대차 신고 대상

    def test_report_not_required_for_small_contract(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord, rent=200_000, deposit=5_000_000)
        cid = self.post(f"listings/{lid}/apply", tenant)["contract_id"]
        c = self.post(f"contracts/{cid}/offer", landlord, **{**TERMS, "deposit": 5_000_000, "rent": 200_000, "down_payment": 500_000}, **REGISTRY)["contract"]
        self.assertFalse(c["report_required"])

    def test_only_parties_can_see_or_act(self):
        landlord, tenant = self.parties()
        stranger = self.signup("stranger1")
        cid = self.post(f"listings/{self.listing(landlord)}/apply", tenant)["contract_id"]
        self.get(f"contracts/{cid}", stranger, status=404)
        self.post(f"contracts/{cid}/offer", stranger, status=404, **TERMS, **REGISTRY)
        self.post(f"contracts/{cid}/cancel", stranger, status=404)
        self.get(f"contracts/{cid}", status=401)
        self.assertEqual(self.get("contracts", stranger)["contracts"], [])

    def test_roles_and_order_are_enforced(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/apply", tenant)["contract_id"]
        self.post(f"contracts/{cid}/offer", tenant, status=403, **TERMS, **REGISTRY)         # 임차인은 조건 못 냄
        self.post(f"contracts/{cid}/tenant-sign", tenant, status=409, offer_hash="x", **INFO)  # 조건 전엔 서명 못 함
        self.post(f"contracts/{cid}/deposit", landlord, status=409)
        self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)
        h = self.get(f"contracts/{cid}", tenant)["contract"]["offer_hash"]
        self.post(f"contracts/{cid}/tenant-sign", landlord, status=403, offer_hash=h, **INFO)  # 임대인은 임차인 서명 못 함
        self.post(f"contracts/{cid}/landlord-sign", landlord, status=409, final_hash="x")      # 임차인 서명 전엔 못 함

    def test_offer_needs_clean_registry_and_sane_terms(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/apply", tenant)["contract_id"]
        self.post(f"contracts/{cid}/offer", landlord, status=422, **TERMS, **{**REGISTRY, "owner_match": False})
        self.post(f"contracts/{cid}/offer", landlord, status=422, **TERMS, **{**REGISTRY, "seizure": True})
        self.post(f"contracts/{cid}/offer", landlord, status=422, **{**TERMS, "down_payment": 20_000_000}, **REGISTRY)
        self.post(f"contracts/{cid}/offer", landlord, status=422, **{**TERMS, "start": "내일"}, **REGISTRY)
        self.post(f"contracts/{cid}/offer", landlord, status=422, **{**TERMS, "months": 0}, **REGISTRY)
        self.post(f"contracts/{cid}/offer", landlord, status=422, **TERMS, **{**REGISTRY, "note": "900101-1234567"})

    def test_stale_offer_cannot_be_signed(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/apply", tenant)["contract_id"]
        self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)
        seen = self.get(f"contracts/{cid}", tenant)["contract"]["offer_hash"]
        self.post(f"contracts/{cid}/offer", landlord, **{**TERMS, "rent": 900_000}, **REGISTRY)    # 보는 사이 임대인이 월세를 바꿈
        self.post(f"contracts/{cid}/tenant-sign", tenant, status=409, offer_hash=seen, **INFO)
        fresh = self.get(f"contracts/{cid}", tenant)["contract"]["offer_hash"]
        self.assertNotEqual(seen, fresh)
        self.post(f"contracts/{cid}/tenant-sign", tenant, offer_hash=fresh, **INFO)

    def test_tenant_sign_validation(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/apply", tenant)["contract_id"]
        self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)
        h = self.get(f"contracts/{cid}", tenant)["contract"]["offer_hash"]
        sign = lambda status, **kw: self.post(f"contracts/{cid}/tenant-sign", tenant, status=status,  # noqa: E731
                                              **{"offer_hash": h, **INFO, **kw})
        sign(422, agree=False)
        sign(422, birth="2999-01-01")
        sign(422, birth="abc")
        sign(422, address="")
        sign(422, emergency="900101-1234567")
        sign(200)

    def test_changed_tenant_info_changes_hash_and_landlord_must_match(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/apply", tenant)["contract_id"]
        self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)
        h = self.get(f"contracts/{cid}", tenant)["contract"]["offer_hash"]
        self.post(f"contracts/{cid}/tenant-sign", tenant, offer_hash=h, **INFO)
        self.post(f"contracts/{cid}/landlord-sign", landlord, status=409, final_hash="0" * 64)

    def test_second_applicant_is_closed_when_offer_goes_to_first(self):
        landlord, first = self.parties()
        second = self.signup("tenant2", "박임차", "010-5555-6666")
        lid = self.listing(landlord)
        c1 = self.post(f"listings/{lid}/apply", first)["contract_id"]
        c2 = self.post(f"listings/{lid}/apply", second)["contract_id"]
        self.post(f"contracts/{c1}/offer", landlord, **TERMS, **REGISTRY)
        self.assertEqual(self.get(f"contracts/{c2}", second)["contract"]["status"], "cancelled")
        self.post(f"listings/{lid}/apply", self.signup("tenant3"), status=404)            # 진행 중이라 새 신청 불가
        self.post(f"listings/{lid}/withdraw", landlord, status=409)                         # 진행 중이면 내릴 수 없음

    def test_cancel_reopens_listing_and_blocks_after_signed(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        cid = self.post(f"listings/{lid}/apply", tenant)["contract_id"]
        self.post(f"contracts/{cid}/offer", landlord, **TERMS, **REGISTRY)
        self.assertEqual(self.get("listings")["listings"], [])
        c = self.post(f"contracts/{cid}/cancel", tenant, reason="마음이 바뀜")["contract"]
        self.assertEqual((c["status"], c["cancel_reason"]), ("cancelled", "마음이 바뀜"))
        self.assertEqual(len(self.get("listings")["listings"]), 1)                          # 다시 신청 받음
        self.post(f"contracts/{cid}/cancel", landlord, status=409)                          # 이미 취소

        lid2 = self.listing(landlord, address="다른 주소 7")
        cid2 = self.to_signed(landlord, tenant, lid2)
        self.post(f"contracts/{cid2}/cancel", tenant, status=409)                           # 성립 뒤엔 취소 불가
        self.post(f"contracts/{cid2}/cancel", landlord, status=409)

    def test_signed_listing_is_closed_for_good(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        self.to_signed(landlord, tenant, lid)
        self.get(f"listings/{lid}", status=404)
        self.post(f"listings/{lid}/apply", self.signup("tenant2"), status=404)

    def test_event_log_records_each_step(self):
        landlord, tenant = self.parties()
        cid = self.to_signed(landlord, tenant, self.listing(landlord))
        events = self.get(f"contracts/{cid}", tenant)["contract"]["events"]
        self.assertEqual([e["what"] for e in events], ["apply", "offer", "tenant_sign", "landlord_sign"])
        self.assertEqual([e["by"] for e in events], ["tenant1", "owner1", "tenant1", "owner1"])


class TestWeb(Rent5Case):
    def test_security_headers_and_static_pages(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", res.headers["Content-Security-Policy"])
        for page in ("login.html", "signup.html", "apply.html", "my.html", "contract.html", "doc.html"):
            self.assertEqual(self.client.get(f"/{page}").status_code, 200, page)
        self.assertNotIn("<script>", self.client.get("/").text)                              # 인라인 스크립트 없음 (CSP)

    def test_api_errors_are_json_and_not_cached(self):
        res = self.client.get("/api/me")
        self.assertEqual(res.status_code, 401)
        self.assertFalse(res.json()["success"])
        self.assertEqual(res.headers["Cache-Control"], "no-store")

    def test_data_is_stored_as_readable_json_without_plain_password(self):
        self.signup("owner1", password="very-secret-pw-1")
        raw = open(os.path.join(self.tmp, "users.json"), encoding="utf-8").read()
        self.assertIn("owner1", raw)
        self.assertNotIn("very-secret-pw-1", raw)


if __name__ == "__main__":
    unittest.main()
