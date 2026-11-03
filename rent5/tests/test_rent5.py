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

REGISTRY = {"owner_match": True, "seizure": False, "mortgage": 0, "note": "깨끗함"}
ACCOUNT = {"bank": "국민은행", "number": "123-456-789012", "holder": "김임대"}


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

    def to_completed(self, landlord, tenant, lid):
        cid = self.post(f"listings/{lid}/reserve", tenant)["contract_id"]
        self.post(f"contracts/{cid}/confirm-landlord-info", tenant)
        self.post(f"contracts/{cid}/confirm-tenant-info", landlord)
        self.post(f"contracts/{cid}/registry", landlord, **REGISTRY)
        self.post(f"contracts/{cid}/confirm-registry", tenant)
        self.post(f"contracts/{cid}/account", landlord, **ACCOUNT)
        self.post(f"contracts/{cid}/pay", tenant)
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

    def test_cannot_reserve_own_or_without_login(self):
        owner = self.signup("owner1")
        lid = self.listing(owner)
        self.post(f"listings/{lid}/reserve", owner, status=409)
        self.post(f"listings/{lid}/reserve", status=401)

    def test_reserved_listing_stays_visible_but_cannot_be_picked(self):
        owner, tenant = self.parties()
        lid = self.listing(owner, contact_phone="010-7777-8888")
        self.post(f"listings/{lid}/reserve", tenant)
        shown = self.get("listings")["listings"][0]
        self.assertEqual((shown["reserved"], shown["contact_phone"]), (True, ""))        # 예약됨 표시, 번호는 가림
        self.post(f"listings/{lid}/reserve", self.signup("tenant2"), status=409)         # 다른 사람은 못 고른다
        self.post(f"listings/{lid}/reserve", tenant, status=409)

    def test_reservation_limit(self):
        owner, tenant = self.parties()
        for _ in range(3):
            self.post(f"listings/{self.listing(owner)}/reserve", tenant)
        self.post(f"listings/{self.listing(owner)}/reserve", tenant, status=409)

    def test_withdraw_blocked_while_reserved(self):
        owner, tenant = self.parties()
        lid = self.listing(owner)
        self.post(f"listings/{lid}/withdraw", self.signup("other1"), status=404)
        self.post(f"listings/{lid}/reserve", tenant)
        self.post(f"listings/{lid}/withdraw", owner, status=409)

    def test_my_listings_show_reservation_count(self):
        owner, tenant = self.parties()
        lid = self.listing(owner)
        self.post(f"listings/{lid}/reserve", tenant)
        mine = self.get("my/listings", owner)["listings"]
        self.assertEqual((mine[0]["status"], mine[0]["applications"]), ("reserved", 1))


class TestContractFlow(Rent5Case):
    def reserve(self, landlord, tenant, **kw):
        return self.post(f"listings/{self.listing(landlord, **kw)}/reserve", tenant)["contract_id"]

    def to_info_ok(self, landlord, tenant, cid):
        self.post(f"contracts/{cid}/confirm-landlord-info", tenant)
        self.post(f"contracts/{cid}/confirm-tenant-info", landlord)

    def test_full_flow_to_completed(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        cid = self.post(f"listings/{lid}/reserve", tenant)["contract_id"]
        c = self.get(f"contracts/{cid}", landlord)["contract"]
        self.assertEqual((c["status"], c["role"], c["terms"]["down_payment"]), ("reserved", "landlord", 1_000_000))   # 보증금의 10%
        self.assertEqual(c["actions"], ["confirm_tenant_info", "cancel"])
        self.assertEqual(self.get(f"contracts/{cid}", tenant)["contract"]["actions"], ["confirm_landlord_info", "cancel"])
        self.assertEqual(self.get("contracts", landlord)["contracts"][0]["other_name"], "이임차")
        self.assertEqual(self.get("contracts", tenant)["contracts"][0]["other_name"], "김임대")

        c = self.post(f"contracts/{cid}/confirm-landlord-info", tenant)["contract"]       # 임차인이 임대인 정보 확인
        self.assertEqual(c["status"], "reserved")
        c = self.post(f"contracts/{cid}/confirm-tenant-info", landlord)["contract"]       # 임대인이 임차인 정보 확인
        self.assertEqual(c["status"], "info_ok")
        self.assertEqual(self.get(f"contracts/{cid}", landlord)["contract"]["actions"], ["show_registry", "cancel"])

        c = self.post(f"contracts/{cid}/registry", landlord, **REGISTRY)["contract"]
        self.assertEqual(c["status"], "registry_shown")
        self.assertEqual(self.get(f"contracts/{cid}", tenant)["contract"]["actions"], ["confirm_registry", "cancel"])
        c = self.post(f"contracts/{cid}/confirm-registry", tenant)["contract"]
        self.assertEqual(c["status"], "registry_ok")

        c = self.post(f"contracts/{cid}/account", landlord, **ACCOUNT)["contract"]
        self.assertEqual((c["status"], c["account"]["amount"], c["account"]["holder_match"]), ("account_sent", 1_000_000, True))
        self.assertEqual(self.get(f"contracts/{cid}", tenant)["contract"]["actions"], ["pay", "cancel"])
        c = self.post(f"contracts/{cid}/pay", tenant)["contract"]
        self.assertEqual(c["status"], "completed")
        self.assertEqual(c["actions"], ["document"])                                        # 끝난 계약은 취소 불가
        self.assertEqual(len(c["final_hash"]), 64)
        self.assertEqual(self.get("listings")["listings"], [])                              # 매물 마감

        doc = self.get(f"contracts/{cid}/document", landlord)["document"]
        self.assertEqual(doc["snapshot"]["version"], 2)
        self.assertEqual(doc["snapshot"]["account"]["number"], "123-456-789012")
        self.assertEqual(doc["snapshot"]["terms"]["deposit"], 10_000_000)
        self.assertEqual([e["what"] for e in c["events"]], ["reserve", "landlord_info_ok", "tenant_info_ok", "registry_shown", "registry_ok", "account_sent", "pay"])
        self.assertTrue(all(e["text"] for e in c["events"]))                                # 단계마다 말풍선 문장이 있다
        self.assertNotIn("by", c["events"][0])

    def test_each_check_belongs_to_one_side_only(self):
        landlord, tenant = self.parties()
        cid = self.reserve(landlord, tenant)
        self.post(f"contracts/{cid}/confirm-landlord-info", landlord, status=403)           # 임대인 정보는 임차인만 확인
        self.post(f"contracts/{cid}/confirm-tenant-info", tenant, status=403)               # 임차인 정보는 임대인만 확인
        self.post(f"contracts/{cid}/confirm-landlord-info", tenant)
        self.post(f"contracts/{cid}/confirm-landlord-info", tenant, status=409)             # 두 번 확인할 수 없다
        self.post(f"contracts/{cid}/confirm-tenant-info", landlord)
        self.post(f"contracts/{cid}/registry", tenant, status=403, **REGISTRY)              # 등기부 열람은 임대인
        self.post(f"contracts/{cid}/registry", landlord, **REGISTRY)
        self.post(f"contracts/{cid}/confirm-registry", landlord, status=403)                # 등기부 확인은 임차인만
        self.post(f"contracts/{cid}/confirm-registry", tenant)
        self.post(f"contracts/{cid}/account", tenant, status=403, **ACCOUNT)
        self.post(f"contracts/{cid}/account", landlord, **ACCOUNT)
        self.post(f"contracts/{cid}/pay", landlord, status=403)                             # 송금은 임차인만

    def test_order_is_enforced(self):
        landlord, tenant = self.parties()
        cid = self.reserve(landlord, tenant)
        self.post(f"contracts/{cid}/registry", landlord, status=409, **REGISTRY)            # 서로 확인하기 전엔 등기부 못 보임
        self.post(f"contracts/{cid}/confirm-registry", tenant, status=409)
        self.post(f"contracts/{cid}/account", landlord, status=409, **ACCOUNT)
        self.post(f"contracts/{cid}/pay", tenant, status=409)
        self.to_info_ok(landlord, tenant, cid)
        self.post(f"contracts/{cid}/account", landlord, status=409, **ACCOUNT)              # 등기부 확인 전엔 계좌 못 알림
        self.get(f"contracts/{cid}/document", tenant, status=409)

    def test_registry_needs_clean_conditions(self):
        landlord, tenant = self.parties()
        cid = self.reserve(landlord, tenant)
        self.to_info_ok(landlord, tenant, cid)
        self.post(f"contracts/{cid}/registry", landlord, status=422, **{**REGISTRY, "owner_match": False})
        self.post(f"contracts/{cid}/registry", landlord, status=422, **{**REGISTRY, "seizure": True})
        self.post(f"contracts/{cid}/registry", landlord, status=422, **{**REGISTRY, "mortgage": "abc"})
        self.post(f"contracts/{cid}/registry", landlord, status=422, **{**REGISTRY, "note": "900101-1234567"})

    def test_account_is_validated_and_name_mismatch_is_warned(self):
        landlord, tenant = self.parties()
        cid = self.reserve(landlord, tenant)
        self.to_info_ok(landlord, tenant, cid)
        self.post(f"contracts/{cid}/registry", landlord, **REGISTRY)
        self.post(f"contracts/{cid}/confirm-registry", tenant)
        self.post(f"contracts/{cid}/account", landlord, status=422, **{**ACCOUNT, "number": "abc"})
        self.post(f"contracts/{cid}/account", landlord, status=422, **{**ACCOUNT, "bank": ""})
        self.post(f"contracts/{cid}/account", landlord, status=422, **{**ACCOUNT, "holder": ""})
        c = self.post(f"contracts/{cid}/account", landlord, **{**ACCOUNT, "holder": "박사기"})["contract"]
        self.assertFalse(c["account"]["holder_match"])
        self.assertIn("예금주가 임대인 이름과 다릅니다", c["events"][-1]["text"])

    def test_report_required_only_for_big_contracts(self):
        landlord, tenant = self.parties()
        small = self.reserve(landlord, tenant, rent=200_000, deposit=5_000_000)
        self.assertFalse(self.get(f"contracts/{small}", tenant)["contract"]["report_required"])
        big = self.reserve(landlord, tenant)
        self.assertTrue(self.get(f"contracts/{big}", tenant)["contract"]["report_required"])

    def test_only_parties_can_see_or_act(self):
        landlord, tenant = self.parties()
        cid = self.reserve(landlord, tenant)
        stranger = self.signup("stranger1", "제삼자", "010-5555-6666")
        self.get(f"contracts/{cid}", stranger, status=404)
        self.post(f"contracts/{cid}/confirm-tenant-info", stranger, status=404)
        self.post(f"contracts/{cid}/cancel", stranger, status=404)
        self.get(f"contracts/{cid}", status=401)

    def test_either_side_can_cancel_and_listing_reopens(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        cid = self.post(f"listings/{lid}/reserve", tenant)["contract_id"]
        self.assertEqual(self.get("listings")["listings"][0]["reserved"], True)
        c = self.post(f"contracts/{cid}/cancel", landlord, reason="오지 않았어요")["contract"]
        self.assertEqual((c["status"], c["actions"]), ("cancelled", []))
        self.assertIn("오지 않았어요", c["events"][-1]["text"])
        self.assertEqual(self.get("listings")["listings"][0]["reserved"], False)            # 다시 고를 수 있다
        cid2 = self.post(f"listings/{lid}/reserve", tenant)["contract_id"]
        self.post(f"contracts/{cid2}/cancel", tenant)
        self.post(f"contracts/{cid2}/cancel", tenant, status=409)

    def test_cancel_works_midway_but_not_after_completed(self):
        landlord, tenant = self.parties()
        cid = self.reserve(landlord, tenant)
        self.to_info_ok(landlord, tenant, cid)
        self.post(f"contracts/{cid}/registry", landlord, **REGISTRY)
        self.post(f"contracts/{cid}/cancel", tenant)                                        # 등기부 열람 뒤에도 취소 가능
        done = self.to_completed(landlord, tenant, self.listing(landlord))
        self.post(f"contracts/{done}/cancel", landlord, status=409)

    def test_completed_listing_is_closed_for_good(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        self.to_completed(landlord, tenant, lid)
        self.assertEqual(self.get("listings")["listings"], [])
        self.post(f"listings/{lid}/reserve", self.signup("tenant2"), status=404)

    def test_old_style_contracts_are_kept_and_only_cancellable(self):
        landlord, tenant = self.parties()
        lid = self.listing(landlord)
        from rent5.store import Store
        old = Store(self.tmp).insert("contracts", {
            "listing_id": lid, "landlord": "owner1", "tenant": "tenant1", "status": "offered", "message": "예전 신청",
            "terms": {"address": "예전 주소", "lease_type": "월세", "deposit": 1, "down_payment": 0, "rent": 1, "mgmt": 0,
                      "start": "2026-12-01", "months": 12, "end": "2027-11-30"},
            "registry": {"mortgage": 0, "note": "", "checked_on": "2026-11-01"}, "tenant_info": None,
            "offer_hash": "x", "final_hash": "", "snapshot": None,
            "events": [{"at": "2026-11-01T00:00:00", "by": "tenant1", "what": "apply"}]})
        c = self.get(f"contracts/{old['id']}", tenant)["contract"]
        self.assertEqual(c["steps"][0], "requested")
        self.assertEqual(c["actions"], ["cancel"])
        self.assertEqual(c["events"][0]["text"], "")
        self.post(f"contracts/{old['id']}/confirm-landlord-info", tenant, status=409)


class TestChat(Rent5Case):
    def test_two_parties_talk_and_each_sees_it_from_their_side(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/reserve", tenant)["contract_id"]
        self.post(f"contracts/{cid}/messages", tenant, text="  안녕하세요, 방 잘 봤습니다.  ")
        c = self.post(f"contracts/{cid}/messages", landlord, text="네, 감사합니다.")["contract"]
        self.assertEqual([(m["mine"], m["role"], m["text"]) for m in c["chat"]],
                         [(False, "tenant", "안녕하세요, 방 잘 봤습니다."), (True, "landlord", "네, 감사합니다.")])
        seen = self.get(f"contracts/{cid}", tenant)["contract"]["chat"]
        self.assertEqual([m["mine"] for m in seen], [True, False])               # 같은 대화를 상대 쪽에서 본다
        self.assertNotIn("by", seen[0])                                           # 아이디는 내려보내지 않는다

    def test_only_the_two_parties_can_talk_and_text_is_checked(self):
        landlord, tenant = self.parties()
        cid = self.post(f"listings/{self.listing(landlord)}/reserve", tenant)["contract_id"]
        stranger = self.signup("stranger1", "제삼자", "010-5555-6666")
        self.post(f"contracts/{cid}/messages", stranger, status=404, text="끼어들기")
        self.post(f"contracts/{cid}/messages", tenant, status=422, text="   ")
        self.post(f"contracts/{cid}/messages", tenant, status=422, text="x" * 501)
        self.post(f"contracts/{cid}/messages", tenant, status=422, text="주민번호 900101-1234567")
        self.post(f"contracts/{cid}/messages", status=401, text="로그인 없이")
        self.assertEqual(self.get(f"contracts/{cid}", tenant)["contract"]["chat"], [])

    def test_can_still_talk_after_completed_and_chat_does_not_move_the_flow(self):
        landlord, tenant = self.parties()
        cid = self.to_completed(landlord, tenant, self.listing(landlord))
        c = self.post(f"contracts/{cid}/messages", tenant, text="입주일에 뵐게요")["contract"]
        self.assertEqual(c["status"], "completed")
        self.assertEqual(len(c["chat"]), 1)


class TestDemo(Rent5Case):
    def start(self):
        return TestClient(create_app(self.tmp, demo=True))

    def login(self, client, username, password="a123456"):
        res = client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def test_demo_accounts_and_listing_exist_after_every_start(self):
        for _ in range(3):                                              # 다시 시작해도 같은 상태 (중복 없음)
            client = self.start()
            listings = client.get("/api/listings").json()["listings"]
            self.assertEqual(len(listings), 1)
            self.assertEqual((listings[0]["address"], listings[0]["room"], listings[0]["deposit"], listings[0]["rent"]),
                             ("아트빌", "202호", 5_000_000, 500_000))
        landlord, tenant = self.login(client, "aaaa"), self.login(client, "bbbb")
        self.assertEqual(client.get("/api/me", headers=landlord).json()["user"]["name"], "김임대")
        me = client.get("/api/me", headers=tenant).json()["user"]
        self.assertEqual((me["name"], me["phone"]), ("김임차", "0101112222"))

    def test_demo_flow_works_and_a_new_demo_listing_returns_after_completion(self):
        client = self.start()
        landlord, tenant = self.login(client, "aaaa"), self.login(client, "bbbb")
        lid = client.get("/api/listings").json()["listings"][0]["id"]
        cid = client.post(f"/api/listings/{lid}/reserve", headers=tenant).json()["contract_id"]
        self.assertEqual(client.get("/api/listings").json()["listings"][0]["reserved"], True)
        client.post(f"/api/contracts/{cid}/cancel", headers=tenant, json={})
        self.assertEqual(client.get("/api/listings").json()["listings"][0]["reserved"], False)
        self.assertEqual(len(self.start().get("/api/listings").json()["listings"]), 1)        # 다시 시작해도 하나

    def test_changed_demo_password_is_kept_and_demo_can_be_turned_off(self):
        client = self.start()
        auth = self.login(client, "bbbb")
        client.post("/api/me/password", headers=auth, json={"current": "a123456", "new": "new-pass-9999"})
        client = self.start()
        self.assertEqual(client.post("/api/login", json={"username": "bbbb", "password": "a123456"}).status_code, 401)
        self.login(client, "bbbb", "new-pass-9999")                                           # 바꾼 비밀번호를 덮어쓰지 않는다
        import tempfile
        from unittest import mock
        other = tempfile.mkdtemp(prefix="rent5_off_")
        self.addCleanup(shutil.rmtree, other, True)
        with mock.patch.dict(os.environ, {"RENT5_DEMO": "0"}):
            self.assertEqual(TestClient(create_app(other, demo=True)).get("/api/listings").json()["listings"], [])


class TestWeb(Rent5Case):
    def test_security_headers_and_static_pages(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", res.headers["Content-Security-Policy"])
        for page in ("login.html", "signup.html", "my.html", "contract.html", "doc.html"):
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
