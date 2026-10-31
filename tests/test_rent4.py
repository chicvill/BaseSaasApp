"""
tests/test_rent4.py — Rent4 (입금 문자 수납부) 검증.

  1. Excel 수납부 분석: 표기 흔들림 흡수, 주민번호 미저장, 합계 대조
  2. 은행 입금 문자 분석: 여러 형식 · 여러 건 붙여넣기 · 잔액 오인 방지
  3. 호실 매칭 & FIFO 귀속월 배정, 중복 반영 차단, 건물 간 호실 번호 분리
  4. 로그인: 관리인 · 임대인 · 임차인 역할 판정, 건물 범위, 첫 로그인 비밀번호 변경
  5. 계약 상호 확인: 발행 → 상대방 본인 확인·동의 → 납부 확인 → 임시 비밀번호 → 변경
"""
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("BASS_SEED_PASSWORD", "seed-test-pw")
SEED_PW = os.environ["BASS_SEED_PASSWORD"]

from fastapi.testclient import TestClient

from core.gateway import create_gateway
from apps.rent4.backend import accounts, bank_parser, excel_import, ledger, sms
from apps.rent4.backend.models import Payment, Room, TenantAccount
from apps.rent4.backend.pool import use_pool_dir

H = "\t"
MONTH_HEAD = H.join(["호실", "세입자명", "주민번호", "전화번호", "전입일", "만기일", "전/월", "선/후", "보증금",
                     "2026.01", "", "", "2026.02", "", "", "2026.03", "", ""])
SUB_HEAD = H * 9 + H.join(["월일", "임차료", "관리비"] * 3)
SHEET = "\n".join([
    "2026년도",
    MONTH_HEAD,
    SUB_HEAD,
    H.join(["101", "홍길동", "900101-1234567", "010-1111-2222", "2025. 6. 4", "2026. 6. 3", "월세", "후불", "2,000,000",
            "1. 3", "150,000", "100,000", "2-3", "150,000", "100,000", "", "", ""]),
    H.join(["102", "김철수", "910202-1234567", "010-3333-4444", "2026. 2. 10", "2027. 2. 9", "월세", "후불", "5,000,000",
            "115", "290,000", "150,000", "전입2/10", "", "", "", "", ""]),
    H.join(["103", "이영희", "920303-2234567", "010-5555-6666", "2024. 1. 1", "2025. 1. 1", "전세", "후불", "40,000,000",
            "1:18", "0", "150,000", "미납", "", "", "", "", ""]),
    H.join(["104", "주인세대"]),
    H * 9 + H.join(["", "440,000", "400,000", "", "150,000", "100,000", "", "", ""]),
])
TODAY = date(2026, 3, 20)


class Rent4Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rent4_")
        use_pool_dir(self.tmp)
        ledger.today = lambda: TODAY

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load_sheet(self, branch="main"):
        return excel_import.apply_sheet(excel_import.parse_sheet(SHEET, branch=branch), branch)

    def dep(self, text):
        return bank_parser.parse_message(text, TODAY)


class TestExcelImport(Rent4Case):
    def test_parse_variants_and_totals(self):
        r = excel_import.parse_sheet(SHEET)
        self.assertEqual(r.months, ["2026-01", "2026-02", "2026-03"])
        pays = {(p.room, p.ym): p for p in r.payments}
        self.assertEqual(pays[("101", "2026-02")].paid_date, "2026-02-03")      # "2-3"
        self.assertEqual(pays[("102", "2026-01")].paid_date, "2026-01-15")      # "115" → 1/15
        self.assertEqual(pays[("103", "2026-01")].paid_date, "2026-01-18")      # "1:18"
        self.assertEqual(r.rooms["104"].status, "owner")
        self.assertEqual(r.rooms["101"].rent, 150_000)
        self.assertTrue(all(t["ok"] for t in r.totals_check), r.totals_check)
        self.assertEqual({m.kind for m in r.marks}, {"move_in", "unpaid"})

    def test_resident_number_is_never_stored(self):
        self.load_sheet()
        for name in os.listdir(self.tmp):
            with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
                self.assertNotIn("1234567", f.read(), name)

    def test_reimport_replaces_excel_rows_only(self):
        self.load_sheet()
        Payment(room="101", ym="2026-03", rent=150_000, mgmt=100_000, source="bank").save()
        self.load_sheet()
        self.assertEqual(len(Payment.find(room="101")), 3)

    def test_same_room_number_in_two_buildings(self):
        self.load_sheet("main")
        self.load_sheet("sunho")
        self.assertEqual(len(Payment.find(branch="main", room="101")), 2)
        self.assertEqual(len(Payment.find(branch="sunho", room="101")), 2)


class TestBankParser(Rent4Case):
    def test_multiline_and_single_line_formats(self):
        text = ("[Web발신]\n신한 03/05 14:32\n110-***-123456\n입금 250,000\n잔액 3,456,789\n홍길동\n\n"
                "[KB]03/06 09:10 123456**789 이영희 입금 150,000 잔액1,234,567")
        msgs = bank_parser.split_messages(text)
        self.assertEqual(len(msgs), 2)
        a, b = (self.dep(m) for m in msgs)
        self.assertEqual((a.amount, a.deposit_date, a.deposit_time, a.bank), (250_000, "2026-03-05", "14:32", "신한"))
        self.assertEqual(a.names[0], "홍길동")
        self.assertEqual((b.amount, b.names[0], b.bank), (150_000, "이영희", "KB"))

    def test_mixed_paste_splits_every_message(self):
        text = ("[Web발신]\n신한 03/05 14:32\n입금 250,000\n홍길동\n\n"
                "[KB]03/06 09:10 이영희 입금 150,000 잔액1,234,567\n\n"
                "농협 입금440,000원 03/07 김철수\n"
                "[Web발신]\n우리 03/08 입금 1,000 박영희")
        names = [self.dep(m).names[0] for m in bank_parser.split_messages(text)]
        self.assertEqual(names, ["홍길동", "이영희", "김철수", "박영희"])

    def test_glued_amount_and_balance_not_taken(self):
        d = self.dep("농협 입금440,000원 03/07 352-****-1234-53 김철수 잔액1,234,567원")
        self.assertEqual((d.amount, d.names[0]), (440_000, "김철수"))

    def test_future_date_means_last_year(self):
        self.assertEqual(self.dep("신한 12/30 입금 1,000 홍길동").deposit_date, "2025-12-30")

    def test_withdrawal_skipped(self):
        self.assertEqual(self.dep("신한 03/05 출금 50,000 편의점").kind, "withdrawal")


class TestLedger(Rent4Case):
    def test_fifo_assigns_unpaid_month_first(self):
        self.load_sheet()
        item = ledger.propose(self.dep("신한 03/05 입금 150,000 이영희"), set(), "main")
        self.assertEqual((item["status"], item["room"], item["months"]), ("ready", "103", ["2026-02"]))

    def test_two_months_and_duplicate_block(self):
        self.load_sheet()
        dep = self.dep("신한 03/05 입금 300,000 이영희")
        item = ledger.propose(dep, set(), "main")
        self.assertEqual(item["months"], ["2026-02", "2026-03"])
        self.assertEqual(ledger.commit(item, "main")["status"], "saved")
        self.assertEqual(ledger.commit(item, "main")["status"], "duplicate")
        self.assertEqual(ledger.propose(dep, {dep.fingerprint}, "main")["status"], "duplicate")

    def test_amount_mismatch_is_diff(self):
        self.load_sheet()
        item = ledger.propose(self.dep("신한 03/05 입금 200,000 홍길동"), set(), "main")
        self.assertEqual((item["status"], item["room"]), ("diff", "101"))

    def test_room_number_in_depositor(self):
        self.load_sheet()
        self.assertEqual(ledger.propose(self.dep("신한 03/05 입금 250,000 101호"), set(), "main")["room"], "101")

    def test_other_building_tenants_not_matched(self):
        self.load_sheet("sunho")
        item = ledger.propose(self.dep("신한 03/05 입금 150,000 이영희"), set(), "main")
        self.assertEqual(item["status"], "needs_review")


class ApiCase(Rent4Case):
    def setUp(self):
        super().setUp()
        os.environ["RENT4_TELEGRAM_POLLING"] = "0"
        for k in ("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "SMS_SENDER", "TELEGRAM_BOT_TOKEN"):
            os.environ.pop(k, None)                      # 개발 모드: 문자는 sms.outbox 로
        sms.outbox.clear()
        self.client = TestClient(create_gateway(db_path=":memory:", only=["rent4"]))
        self.admin = self.login("admin", SEED_PW)
        self.chic = self.make_lessor("chic", "시크빌", "김임대", "010-1234-5678")

    def login(self, username, password):
        res = self.client.post("/auth/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def change_pw(self, auth, current, new):
        res = self.client.post("/api/rent4/account/password", headers=auth, json={"current": current, "new": new})
        self.assertEqual(res.status_code, 200, res.text)

    def last_code(self, phone):
        digits = re.sub(r"\D", "", phone)
        for m in reversed(sms.outbox):
            if m["to"] == digits and "인증번호" in m["text"]:
                return re.search(r"인증번호 (\d{6})", m["text"])[1]
        self.fail(f"{phone} 로 보낸 인증번호가 없습니다")

    def sms_password(self, phone, password, home=None):
        """비밀번호 설정·찾기: 휴대폰 번호 → 문자 인증번호 → 비밀번호 → 바로 로그인."""
        res = self.client.post("/api/rent4/otp/request", json={"phone": phone})
        self.assertEqual(res.status_code, 200, res.text)
        res = self.client.post("/api/rent4/otp/set-password", json={"phone": phone, "code": self.last_code(phone), "new": password})
        self.assertEqual(res.status_code, 200, res.text)
        if home:
            self.assertEqual(res.json()["home"], home)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def conclude(self, party, cid, issuer, kind_path, home):
        """동의 → 납부 확인 → '시작하기'로 최종 계정 세션 (비밀번호는 그대로)."""
        self.assertEqual(self.client.post("/api/rent4/contract/mine/sign", headers=party, json={"agree": True}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/rent4/{kind_path}/{cid}/paid", headers=issuer).status_code, 200)
        res = self.client.post("/api/rent4/contract/mine/continue", headers=party)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["home"], home)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def make_lessor(self, code, name, party, phone):
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": name, "building_code": code, "party_name": party, "party_phone": phone, "fee": 300000})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("token", res.json())
        cid = res.json()["contract"]["id"]
        party_auth = self.sms_password(phone, f"pw-{code}-owner", "contract.html")
        return self.conclude(party_auth, cid, self.admin, "admin/contracts", "임대인.html")

    def import_sheet(self, auth=None, branch=""):
        q = f"?branch={branch}" if branch else ""
        return self.client.post(f"/api/rent4/lessor/import/commit{q}", json={"text": SHEET}, headers=auth or self.chic).json()

    def tenant_login(self, room="103"):
        phone = Room.first(branch="main", room=room).phone
        return self.sms_password(phone, "secret-" + room, "임차인.html")


class TestApi(ApiCase):
    def test_lessor_requires_login(self):
        self.assertEqual(self.client.get("/api/rent4/lessor/board").status_code, 401)

    def test_me_routes_each_role_home(self):
        self.import_sheet()
        me = self.client.get("/api/rent4/me", headers=self.chic).json()
        self.assertEqual((me["kind"], me["home"], me["building"]["name"]), ("lessor", "임대인.html", "시크빌"))
        admin = self.client.get("/api/rent4/me", headers=self.admin).json()
        self.assertEqual((admin["kind"], admin["home"]), ("admin", "admin.html"))
        t = self.client.get("/api/rent4/me", headers=self.tenant_login("101")).json()
        self.assertEqual((t["kind"], t["room"], t["home"], t["must_change_password"]), ("tenant", "101", "임차인.html", False))

    def test_pages_served(self):
        for page in ("index.html", "password.html", "admin.html", "contract.html", "임대인.html", "임차인.html"):
            self.assertEqual(self.client.get(f"/rent4/{page}").status_code, 200, page)
        self.assertEqual(self.client.get("/rent4/invite.html").status_code, 404)

    def test_sms_code_rules(self):
        self.import_sheet()
        self.assertEqual(self.client.post("/api/rent4/otp/request", json={"phone": "010-0000-9999"}).status_code, 404)
        self.client.post("/api/rent4/otp/request", json={"phone": "010-5555-6666"})
        self.assertEqual(self.client.post("/api/rent4/otp/request", json={"phone": "010-5555-6666"}).status_code, 409)   # 1분 재요청 제한
        wrong = self.client.post("/api/rent4/otp/set-password", json={"phone": "010-5555-6666", "code": "000000", "new": "secret-103"})
        self.assertEqual(wrong.status_code, 422)
        weak = self.client.post("/api/rent4/otp/set-password", json={"phone": "010-5555-6666", "code": self.last_code("010-5555-6666"), "new": "111111"})
        self.assertEqual(weak.status_code, 422)
        self.assertNotIn(self.last_code("010-5555-6666"), str(self.client.get("/api/rent4/lessor/accounts", headers=self.chic).json()))

    def test_code_expires_after_five_wrong_tries(self):
        self.import_sheet()
        self.client.post("/api/rent4/otp/request", json={"phone": "010-5555-6666"})
        code = self.last_code("010-5555-6666")
        for _ in range(5):
            self.client.post("/api/rent4/otp/set-password", json={"phone": "010-5555-6666", "code": "000000", "new": "secret-103"})
        res = self.client.post("/api/rent4/otp/set-password", json={"phone": "010-5555-6666", "code": code, "new": "secret-103"})
        self.assertEqual(res.status_code, 422)

    def test_after_setup_current_password_is_required(self):
        self.import_sheet()
        auth = self.tenant_login("103")
        res = self.client.post("/api/rent4/account/password", headers=auth, json={"current": "", "new": "another-103"})
        self.assertEqual(res.status_code, 422)

    def test_import_then_bank_flow_then_tenant_view(self):
        self.assertEqual(self.import_sheet()["payments"], 4)
        prev = self.client.post("/api/rent4/lessor/bank/preview", headers=self.chic,
                                json={"text": "신한 03/05 14:00 입금 150,000 이영희"}).json()
        self.assertEqual(prev["items"][0]["months"], ["2026-02"])
        done = self.client.post("/api/rent4/lessor/bank/commit", json={"items": prev["items"]}, headers=self.chic).json()
        self.assertEqual(done["saved"], 1)
        view = self.client.get("/api/rent4/tenant/me", headers=self.tenant_login("103")).json()
        self.assertEqual({m["ym"]: m["status"] for m in view["months"]},
                         {"2026-01": "paid", "2026-02": "paid", "2026-03": "due"})
        self.assertNotIn("phone", view["room"])

    def test_tenant_sees_only_own_tenancy(self):
        self.import_sheet()
        view = self.client.get("/api/rent4/tenant/me", headers=self.tenant_login("102")).json()
        self.assertEqual([m["ym"] for m in view["months"]], ["2026-03", "2026-02"])   # 1월(이전 임차인) 숨김

    def test_tenant_cannot_use_lessor_api(self):
        self.import_sheet()
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=self.tenant_login("101")).status_code, 403)

    def test_lessor_scoped_to_own_building_admin_picks(self):
        self.import_sheet()
        sunho = self.make_lessor("sunho", "선호아트빌", "박임대", "010-2222-3333")
        self.assertEqual(self.client.get("/api/rent4/lessor/board?branch=main", headers=sunho).status_code, 403)
        own = self.client.get("/api/rent4/lessor/board?year=2026", headers=sunho).json()
        self.assertEqual((own["building"], own["rooms"]), ("선호아트빌", []))
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=self.admin).status_code, 400)
        board = self.client.get("/api/rent4/lessor/board?year=2026&branch=main", headers=self.admin).json()
        self.assertEqual(len(board["rooms"]), 4)
        names = [b["name"] for b in self.client.get("/api/rent4/admin/buildings", headers=self.admin).json()["buildings"]]
        self.assertEqual(names, ["시크빌", "선호아트빌", "신비주택"])

    def test_new_tenant_resets_account(self):
        self.import_sheet()
        self.tenant_login("101")
        changed = SHEET.replace("홍길동", "고길동").replace("010-1111-2222", "010-9999-8888")
        self.client.post("/api/rent4/lessor/import/commit", json={"text": changed}, headers=self.chic)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)
        self.assertEqual(self.client.post("/api/rent4/otp/request", json={"phone": "010-1111-2222"}).status_code, 404)
        self.sms_password("010-9999-8888", "new-tenant-101", "임차인.html")

    def test_board_and_delete_bank_payment(self):
        self.import_sheet()
        prev = self.client.post("/api/rent4/lessor/bank/preview", headers=self.chic,
                                json={"text": "신한 03/05 입금 250,000 홍길동"}).json()
        pid = self.client.post("/api/rent4/lessor/bank/commit", json={"items": prev["items"]},
                               headers=self.chic).json()["results"][0]["payment_ids"][0]
        board = self.client.get("/api/rent4/lessor/board?year=2026", headers=self.chic).json()
        row = next(r for r in board["rooms"] if r["room"]["room"] == "101")
        self.assertEqual(row["months"][2]["status"], "paid")
        self.assertEqual(self.client.delete(f"/api/rent4/lessor/payments/{pid}", headers=self.chic).status_code, 200)
        self.assertEqual(len(Payment.find(room="101", source="bank")), 0)

    def test_accounts_list_and_notify_sms(self):
        self.import_sheet()
        rows = self.client.get("/api/rent4/lessor/accounts", headers=self.chic).json()["accounts"]
        self.assertEqual([r["username"] for r in rows], ["chic101", "chic102", "chic103"])
        self.assertNotIn("initial_code", rows[0])
        res = self.client.post("/api/rent4/lessor/accounts/101/notify", headers=self.chic).json()
        self.assertEqual((res["tenant_name"], res["phone"]), ("홍길동", "010-1111-2222"))
        self.assertIn("비밀번호 설정", sms.outbox[-1]["text"])
        self.assertNotIn("t=", sms.outbox[-1]["text"])                     # 링크·토큰 없이 접속 주소만

    def test_existing_tenant_can_log_in_by_phone(self):
        self.import_sheet()
        self.tenant_login("101")
        res = self.client.post("/api/rent4/login", json={"username": "010-1111-2222", "password": "secret-101"}).json()
        self.assertEqual(res["user"]["username"], "chic101")
        wrong = self.client.post("/api/rent4/login", json={"username": "010-1111-2222", "password": "nope-nope"})
        self.assertEqual(wrong.status_code, 401)

    def test_forgot_password_same_flow(self):
        self.import_sheet()
        self.tenant_login("101")
        self.sms_password("010-1111-2222", "forgot-new-101", "임차인.html")
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)
        self.login("chic101", "forgot-new-101")


class TestContracts(ApiCase):
    def test_seeded_branch_account_alone_is_not_a_lessor(self):
        self.assertEqual(self.client.post("/auth/login", json={"username": "sinbi", "password": "1212"}).status_code, 401)
        sinbi = self.login("sinbi", SEED_PW)
        self.assertEqual(self.client.get("/api/rent4/me", headers=sinbi).status_code, 403)

    def test_membership_by_sms_then_continue_by_phone(self):
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": "새빌딩", "building_code": "newb", "party_name": "최임대", "party_phone": "010-5555-1234"}).json()
        cid = res["contract"]["id"]
        self.assertIn("새빌딩 가맹 계약", sms.outbox[-1]["text"])
        party = self.sms_password("010-5555-1234", "newb-strong-pw", "contract.html")
        me = self.client.get("/api/rent4/me", headers=party).json()
        self.assertEqual((me["kind"], me["home"]), ("party", "contract.html"))
        mine = self.client.get("/api/rent4/contract/mine", headers=party).json()["contract"]
        self.assertEqual((mine["status"], mine["final_username"]), ("sent", "newb"))
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=party).status_code, 403)
        self.assertEqual(self.client.post(f"/api/rent4/admin/contracts/{cid}/paid", headers=self.admin).status_code, 409)
        res = self.client.post("/api/rent4/login", json={"username": "010-5555-1234", "password": "newb-strong-pw"})
        party = {"Authorization": f"Bearer {res.json()['token']}"}
        auth = self.conclude(party, cid, self.admin, "admin/contracts", "임대인.html")
        self.assertIn("체결", sms.outbox[-1]["text"])
        me = self.client.get("/api/rent4/me", headers=auth).json()
        self.assertEqual((me["kind"], me["must_change_password"], me["building"]["name"]), ("lessor", False, "새빌딩"))
        self.login("newb", "newb-strong-pw")
        by_phone = self.client.post("/api/rent4/login", json={"username": "01055551234", "password": "newb-strong-pw"}).json()
        self.assertEqual(by_phone["user"]["username"], "newb")
        self.assertEqual(self.client.post("/auth/login", json={"username": f"k{cid}", "password": "newb-strong-pw"}).status_code, 401)

    def test_lease_contract_moves_tenant_in(self):
        self.import_sheet()
        res = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "101", "party_name": "신입주", "party_phone": "010-7777-4321", "deposit": 3000000,
            "rent": 300000, "mgmt": 100000, "start": "2026-03-15", "end": "2027-03-14"}).json()
        cid = res["contract"]["id"]
        party = self.sms_password("010-7777-4321", "new-tenant-pw", "contract.html")
        auth = self.conclude(party, cid, self.chic, "lessor/contracts", "임차인.html")
        room = Room.first(branch="main", room="101")
        self.assertEqual((room.tenant_name, room.monthly_due, room.move_in), ("신입주", 400000, "2026-03-15"))
        self.login("chic101", "new-tenant-pw")
        view = self.client.get("/api/rent4/tenant/me", headers=auth).json()
        self.assertEqual([m["ym"] for m in view["months"]], ["2026-03"])     # 이전 임차인 기록 없음

    def test_other_lessor_cannot_touch_contract(self):
        self.import_sheet()
        cid = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "102", "party_name": "가", "party_phone": "010-1111-0000", "start": "2026-04-01"}).json()["contract"]["id"]
        sunho = self.make_lessor("sunho", "선호아트빌", "박임대", "010-2222-3333")
        self.assertEqual(self.client.post(f"/api/rent4/lessor/contracts/{cid}/paid", headers=sunho).status_code, 404)

    def test_link_actions_are_gone(self):
        cid = self.client.get("/api/rent4/admin/contracts", headers=self.admin).json()["contracts"][0]["id"]
        for action in ("relink", "reset"):
            self.assertEqual(self.client.post(f"/api/rent4/admin/contracts/{cid}/{action}", headers=self.admin).status_code, 404)
        self.assertIn(self.client.post("/api/rent4/contract/join", json={}).status_code, (404, 405))


class TestPasswords(ApiCase):
    def test_tenant_temp_password_matches_lessor_format(self):
        self.import_sheet()
        from apps.rent4.backend.accounts import TEMP_ALPHABET
        for acc in TenantAccount.find(branch="main"):
            self.assertEqual(len(acc.initial_code), 8, acc.initial_code)
            self.assertTrue(set(acc.initial_code) <= set(TEMP_ALPHABET))

    def test_legacy_six_digit_codes_upgraded_on_sync(self):
        from apps.rent4.backend import accounts as acc_mod
        self.import_sheet()
        legacy = TenantAccount.first(branch="main", room="102")
        legacy.initial_code = "123456"
        legacy.save()
        self.assertEqual(acc_mod.sync_tenant_accounts()["upgraded"], 1)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic102", "password": "123456"}).status_code, 401)

    def test_change_anytime_after_setup(self):
        self.import_sheet()
        auth = self.tenant_login("103")
        self.change_pw(auth, "secret-103", "second-103")
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic103", "password": "secret-103"}).status_code, 401)
        self.login("chic103", "second-103")
        self.change_pw(self.chic, "pw-chic-owner", "pw-chic-second")
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=self.chic).status_code, 200)

    def test_admin_changes_own_password_min_8(self):
        short = self.client.post("/api/rent4/account/password", headers=self.admin, json={"current": SEED_PW, "new": "short1"})
        self.assertEqual(short.status_code, 422)
        self.change_pw(self.admin, SEED_PW, "admin-new-password")
        self.login("admin", "admin-new-password")

    def test_lessor_forgot_password_by_sms(self):
        auth = self.sms_password("010-1234-5678", "pw-chic-found", "임대인.html")
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=auth).status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic", "password": "pw-chic-owner"}).status_code, 401)


class TestAdminSetupLink(ApiCase):
    def test_admin_link_logs_in_then_forced_setup(self):
        _, token = accounts.issue_admin_link("admin")
        self.assertEqual(self.client.post("/auth/login", json={"username": "admin", "password": SEED_PW}).status_code, 401)
        view = self.client.get(f"/api/rent4/setup/view?t={token}").json()
        self.assertEqual((view["username"], view["activated"]), ("admin", False))
        res = self.client.post("/api/rent4/setup/start", json={"t": token}).json()
        auth = {"Authorization": f"Bearer {res['token']}"}
        self.assertEqual(self.client.get("/api/rent4/admin/buildings", headers=auth).status_code, 403)
        self.change_pw(auth, "", "admin-chosen-pw")
        self.assertEqual(self.client.get("/api/rent4/admin/buildings", headers=auth).status_code, 200)
        self.assertEqual(self.client.get(f"/api/rent4/setup/view?t={token}").status_code, 404)


class TestOAuth(ApiCase):
    """카카오·구글은 실제 서버 대신 가짜 응답으로 시험한다."""

    def setUp(self):
        from unittest import mock
        from apps.rent4.backend import oauth
        self.oauth = oauth
        env = mock.patch.dict(os.environ, {"KAKAO_REST_API_KEY": "kakao-test-key"})
        env.start()
        self.addCleanup(env.stop)
        self.profiles = {"kakao-code-1": 111, "kakao-code-2": 222}
        fake = mock.patch.object(oauth, "_http_json", side_effect=self._fake_http)
        fake.start()
        self.addCleanup(fake.stop)
        super().setUp()

    def _fake_http(self, url, data=None, token=""):
        if url == self.oauth.PROVIDERS["kakao"]["token"]:
            return {"access_token": "at-" + data["code"]}
        uid = self.profiles[token[3:]]
        return {"id": uid, "kakao_account": {"profile": {"nickname": f"사용자{uid}"}, "email": f"{uid}@kakao.test"}}

    def kakao(self, purpose, code, auth=None):
        res = self.client.post("/api/rent4/oauth/kakao/start", json={"purpose": purpose}, headers=auth or {})
        self.assertEqual(res.status_code, 200, res.text)
        url = res.json()["url"]
        self.assertIn("client_id=kakao-test-key", url)
        state = re.search(r"state=([^&]+)", url)[1]
        back = self.client.get(f"/api/rent4/oauth/kakao/callback?code={code}&state={state}", follow_redirects=False)
        self.assertEqual(back.status_code, 302)
        return back.headers["location"]

    def new_lease_party(self):
        self.import_sheet()
        cid = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "101", "party_name": "신입주", "party_phone": "010-7777-4321", "rent": 300000, "mgmt": 100000,
            "start": "2026-03-15"}).json()["contract"]["id"]
        return cid, self.sms_password("010-7777-4321", "new-tenant-pw", "contract.html")

    def make_lessor(self, code, name, party, phone):
        """setUp 의 시크빌 임대인도 본인 확인을 거쳐야 동의할 수 있다."""
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": name, "building_code": code, "party_name": party, "party_phone": phone, "fee": 300000})
        cid = res.json()["contract"]["id"]
        party_auth = self.sms_password(phone, f"pw-{code}-owner", "contract.html")
        self.profiles[f"kakao-{code}"] = hash(code) % 10000 + 1000
        self.kakao("link", f"kakao-{code}", party_auth)
        return self.conclude(party_auth, cid, self.admin, "admin/contracts", "임대인.html")

    def test_providers_listed(self):
        res = self.client.get("/api/rent4/oauth/providers").json()
        self.assertEqual(res["providers"], [{"id": "kakao", "label": "카카오"}])

    def test_sign_requires_verification_then_allowed(self):
        cid, party = self.new_lease_party()
        mine = self.client.get("/api/rent4/contract/mine", headers=party).json()["contract"]
        self.assertEqual((mine["verify_required"], mine["verified"]), (True, None))
        blocked = self.client.post("/api/rent4/contract/mine/sign", headers=party, json={"agree": True})
        self.assertEqual(blocked.status_code, 422)
        self.assertTrue(self.kakao("link", "kakao-code-1", party).endswith("/rent4/contract.html#oauth=linked"))
        mine = self.client.get("/api/rent4/contract/mine", headers=party).json()["contract"]
        self.assertEqual((mine["verified"]["provider"], mine["verified"]["name"]), ("kakao", "사용자111"))
        self.conclude(party, cid, self.chic, "lessor/contracts", "임차인.html")

    def test_login_with_linked_account_goes_to_final_id(self):
        cid, party = self.new_lease_party()
        self.kakao("link", "kakao-code-1", party)
        self.conclude(party, cid, self.chic, "lessor/contracts", "임차인.html")
        loc = self.kakao("login", "kakao-code-1")
        token = re.search(r"#token=(.+)$", loc)[1]
        me = self.client.get("/api/rent4/me", headers={"Authorization": f"Bearer {token}"}).json()
        self.assertEqual((me["kind"], me["username"]), ("tenant", "chic101"))

    def test_unlinked_and_reused_accounts(self):
        self.assertTrue(self.kakao("login", "kakao-code-2").endswith("index.html#oauth=unlinked"))
        cid, party = self.new_lease_party()
        self.kakao("link", "kakao-code-1", party)
        other = self.tenant_login("102")
        self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "103", "party_name": "다른사람", "party_phone": "010-8888-1111", "start": "2026-04-01"})
        other = self.sms_password("010-8888-1111", "other-pw-1", "contract.html")
        self.assertTrue(self.kakao("link", "kakao-code-1", other).endswith("contract.html#oauth=used"))

    def test_link_requires_login(self):
        res = self.client.post("/api/rent4/oauth/kakao/start", json={"purpose": "link"})
        self.assertEqual(res.status_code, 400)


class TestTelegram(ApiCase):
    """텔레그램 봇 API 는 가짜로 시험한다."""

    def setUp(self):
        super().setUp()                                   # 시크빌 임대인 준비는 개발 모드로
        from unittest import mock
        env = mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABCdefGHIjklMNOpqrSTUvwx"})
        env.start()
        self.addCleanup(env.stop)
        self.updates, self.sent = [], []
        fake = mock.patch.object(sms, "_telegram", side_effect=self._fake)
        fake.start()
        self.addCleanup(fake.stop)
        sms._bot_username, sms._update_offset = None, 0
        self.import_sheet()

    def _fake(self, method, params=None, timeout=10):
        if method == "getMe":
            return {"username": "rent4_test_bot"}
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        if method == "sendMessage":
            self.sent.append(params)
            return {}
        raise AssertionError(method)

    def bot_message(self, uid, **message):
        self.updates.append({"update_id": len(self.sent) + len(self.updates) + 100,
                             "message": {"chat": {"id": 900 + uid}, "from": {"id": uid}, **message}})

    def test_provider_choice(self):
        info = self.client.get("/api/rent4/sms/info").json()
        self.assertEqual((info["provider"], info["telegram_bot"]), ("telegram", "https://t.me/rent4_test_bot"))
        self.assertEqual([p["id"] for p in info["available"]], ["dev", "telegram"])
        self.assertEqual(self.client.post("/api/rent4/admin/sms", headers=self.admin, json={"provider": "dev"}).json()["provider"], "dev")
        self.assertEqual(self.client.post("/api/rent4/admin/sms", headers=self.admin, json={"provider": "solapi"}).status_code, 400)
        self.assertEqual(self.client.post("/api/rent4/admin/sms", headers=self.chic, json={"provider": "dev"}).status_code, 403)

    def test_link_by_shared_contact_then_code_by_telegram(self):
        res = self.client.post("/api/rent4/otp/request", json={"phone": "010-1111-2222"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("t.me/rent4_test_bot", res.json()["hint"])
        self.bot_message(7, text="/start")
        self.bot_message(7, contact={"phone_number": "+821099990000", "user_id": 8})       # 남의 연락처
        self.bot_message(7, contact={"phone_number": "+82 10-1111-2222", "user_id": 7})    # 본인 연락처
        self.assertEqual(sms.process_updates(), 3)
        self.assertTrue(self.sent[0]["reply_markup"]["keyboard"][0][0]["request_contact"])
        self.assertIn("본인 연락처만", self.sent[1]["text"])
        self.assertIn("01011112222", self.sent[2]["text"])
        res = self.client.post("/api/rent4/otp/request", json={"phone": "010-1111-2222"}).json()
        self.assertEqual(res["mode"], "telegram")
        msg = self.sent[-1]
        self.assertEqual(msg["chat_id"], 907)
        code = re.search(r"인증번호 (\d{6})", msg["text"])[1]
        done = self.client.post("/api/rent4/otp/set-password", json={"phone": "010-1111-2222", "code": code, "new": "tg-pass-101"})
        self.assertEqual(done.json()["home"], "임차인.html")


class TestResetLink(ApiCase):
    """임대인 비밀번호 초기화 → 채팅으로 링크 → 임차인 새 비밀번호 → 로그인."""

    def reset(self, room="101", auth=None):
        res = self.client.post(f"/api/rent4/lessor/accounts/{room}/reset", headers=auth or self.chic)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_reset_then_tenant_sets_password_and_logs_in(self):
        self.import_sheet()
        self.tenant_login("101")                                     # 쓰던 비밀번호 secret-101
        r = self.reset("101")
        self.assertEqual((r["username"], r["tenant_name"], r["expires_hours"]), ("chic101", "홍길동", 24))
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)
        view = self.client.get(f"/api/rent4/reset/view?t={r['token']}").json()
        self.assertEqual((view["room"], view["username"]), ("101", "chic101"))
        weak = self.client.post("/api/rent4/reset/set", json={"t": r["token"], "new": "111111"})
        self.assertEqual(weak.status_code, 422)                          # 약한 비밀번호는 거절, 링크는 그대로
        done = self.client.post("/api/rent4/reset/set", json={"t": r["token"], "new": "after-reset-101"}).json()
        self.assertEqual(done["home"], "임차인.html")
        me = self.client.get("/api/rent4/me", headers={"Authorization": f"Bearer {done['token']}"}).json()
        self.assertEqual((me["kind"], me["must_change_password"]), ("tenant", False))
        self.login("chic101", "after-reset-101")
        self.assertEqual(self.client.get(f"/api/rent4/reset/view?t={r['token']}").status_code, 404)   # 한 번만
        self.assertEqual(self.client.post("/api/rent4/reset/set", json={"t": r["token"], "new": "again-again"}).status_code, 404)

    def test_new_reset_replaces_old_link(self):
        self.import_sheet()
        first = self.reset("102")["token"]
        second = self.reset("102")["token"]
        self.assertEqual(self.client.get(f"/api/rent4/reset/view?t={first}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/rent4/reset/view?t={second}").status_code, 200)

    def test_expired_link(self):
        self.import_sheet()
        token = self.reset("103")["token"]
        acc = TenantAccount.first(branch="main", room="103")
        acc.invite_expires = 1.0
        acc.save()
        self.assertEqual(self.client.post("/api/rent4/reset/set", json={"t": token, "new": "late-pw-103"}).status_code, 404)

    def test_other_building_lessor_cannot_reset(self):
        self.import_sheet()
        sunho = self.make_lessor("sunho", "선호아트빌", "박임대", "010-2222-3333")
        self.assertEqual(self.client.post("/api/rent4/lessor/accounts/101/reset", headers=sunho).status_code, 404)
        self.assertEqual(self.client.post("/api/rent4/lessor/accounts/101/reset").status_code, 401)

    def test_tenant_change_invalidates_link(self):
        self.import_sheet()
        token = self.reset("101")["token"]
        changed = SHEET.replace("홍길동", "고길동").replace("010-1111-2222", "010-9999-8888")
        self.client.post("/api/rent4/lessor/import/commit", json={"text": changed}, headers=self.chic)
        self.assertEqual(self.client.get(f"/api/rent4/reset/view?t={token}").status_code, 404)

    def test_online_tenant_gets_link_in_chat(self):
        from apps.rent4.backend import chat
        chat._last_seen.clear()
        self.import_sheet()
        tenant = self.tenant_login("101")
        self.assertEqual(self.client.get("/api/rent4/chat/inbox", headers=tenant).json()["messages"], [])   # 접속 중 표시
        r = self.reset("101")
        self.assertEqual(r["delivered"], "chat")
        msgs = self.client.get("/api/rent4/chat/inbox", headers=tenant).json()["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["link"], f"reset.html?t={r['token']}")
        self.assertIsNone(msgs[0]["read_at"])
        self.client.post(f"/api/rent4/chat/read/{msgs[0]['id']}", headers=tenant)
        self.assertTrue(self.client.get("/api/rent4/chat/inbox", headers=tenant).json()["messages"][0]["read_at"])
        token = msgs[0]["link"].split("t=", 1)[1]
        done = self.client.post("/api/rent4/reset/set", json={"t": token, "new": "chat-link-101"}).json()
        self.assertEqual(done["home"], "임차인.html")
        self.login("chic101", "chat-link-101")
        # 다른 사람에게는 보이지 않는다
        other = self.tenant_login("102")
        self.assertEqual(self.client.get("/api/rent4/chat/inbox", headers=other).json()["messages"], [])

    def test_offline_tenant_falls_back_to_share(self):
        from apps.rent4.backend import chat
        chat._last_seen.clear()
        self.import_sheet()
        self.assertEqual(self.reset("103")["delivered"], "share")
        self.assertEqual(self.client.get("/api/rent4/chat/inbox").status_code, 401)

    def test_reset_page_served(self):
        self.assertEqual(self.client.get("/rent4/reset.html").status_code, 200)


class TestSms(unittest.TestCase):
    def test_solapi_request_is_signed(self):
        import json as _json
        from unittest import mock
        env = {"SOLAPI_API_KEY": "KEY123", "SOLAPI_API_SECRET": "SECRET", "SMS_SENDER": "010-1111-0000"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
            self.assertEqual(sms.send("010-2222-3333", "안녕")["mode"], "solapi")
            req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, sms.SOLAPI_URL)
        self.assertTrue(req.get_header("Authorization").startswith("HMAC-SHA256 apiKey=KEY123, date="))
        body = _json.loads(req.data)
        self.assertEqual(body["message"], {"to": "01022223333", "from": "01011110000", "text": "안녕"})


if __name__ == "__main__":
    unittest.main()
