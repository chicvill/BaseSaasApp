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
from apps.rent4.backend import accounts, bank_parser, excel_import, ledger
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

    def start_link(self, link, password, home="contract.html"):
        """계약 시작 링크 / 재설정 링크(reset.html?t=)에서 비밀번호를 정하면 바로 로그인."""
        res = self.client.post("/api/rent4/reset/set", json={"t": link.split("t=")[1], "new": password})
        self.assertEqual(res.status_code, 200, res.text)
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
        party_auth = self.start_link(res.json()["link"], f"pw-{code}-owner")
        return self.conclude(party_auth, cid, self.admin, "admin/contracts", "임대인.html")

    def import_sheet(self, auth=None, branch=""):
        q = f"?branch={branch}" if branch else ""
        return self.client.post(f"/api/rent4/lessor/import/commit{q}", json={"text": SHEET}, headers=auth or self.chic).json()

    def tenant_login(self, room="103", password=None):
        """임대인 '비밀번호 초기화' 링크로 시작하는 임차인."""
        r = self.client.post(f"/api/rent4/lessor/accounts/{room}/reset", headers=self.chic).json()
        return self.start_link(f"reset.html?t={r['token']}", password or "secret-" + room, "임차인.html")


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
        for page in ("index.html", "login.html", "find.html", "admin.html", "contract.html", "임대인.html", "임차인.html"):
            self.assertEqual(self.client.get(f"/rent4/{page}").status_code, 200, page)
        for page in ("invite.html", "password.html"):
            self.assertEqual(self.client.get(f"/rent4/{page}").status_code, 404, page)

    def test_month_summary_and_building_detail(self):
        self.import_sheet()
        ym = ledger.ym_of(ledger.today())
        Payment(branch="main", room="101", ym=ym, rent=200000, mgmt=50000, source="manual").save()
        m = self.client.get("/api/rent4/lessor/summary", headers=self.chic).json()
        rooms = [r for r in Room.find(branch="main") if r.status != "owner"]
        occupied = [r for r in rooms if r.status == "occupied"]
        self.assertEqual(m["ym"], ym)
        self.assertEqual(m["expected"], sum(r.monthly_due for r in occupied))
        self.assertGreaterEqual(m["collected"], 250000)
        self.assertEqual(m["paid_rooms"] + m["unpaid_rooms"], len(occupied))
        self.assertEqual(m["vacant_rooms"], len(rooms) - len(occupied))
        d = self.client.get("/api/rent4/admin/buildings/main", headers=self.admin).json()
        self.assertEqual((d["building"]["name"], len(d["totals"]), d["contracts"][0]["status"]), ("시크빌", 12, "active"))
        self.assertEqual(self.client.get("/api/rent4/admin/buildings/main", headers=self.chic).status_code, 403)

    def test_sms_routes_are_gone(self):
        for path in ("/api/rent4/otp/request", "/api/rent4/otp/set-password", "/api/rent4/admin/sms",
                     "/api/rent4/lessor/accounts/101/notify"):
            self.assertIn(self.client.post(path, json={}, headers=self.admin).status_code, (404, 405), path)

    def test_contract_start_link_rules(self):
        self.import_sheet()
        res = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "101", "party_name": "신입주", "party_phone": "010-7777-4321", "start": "2026-03-15"}).json()
        cid, old = res["contract"]["id"], res["link"].split("t=")[1]
        self.assertEqual(res["expires_hours"], 24)
        view = self.client.get(f"/api/rent4/reset/view?t={old}").json()
        self.assertEqual((view["name"], view["login"]), ("신입주", "휴대폰 번호 010-7777-4321"))
        new = self.client.post(f"/api/rent4/lessor/contracts/{cid}/link", headers=self.chic).json()["link"]
        self.assertEqual(self.client.get(f"/api/rent4/reset/view?t={old}").status_code, 404)        # 다시 만들면 이전 링크 무효
        weak = self.client.post("/api/rent4/reset/set", json={"t": new.split("t=")[1], "new": "111111"})
        self.assertEqual(weak.status_code, 422)
        self.start_link(new, "new-tenant-pw")
        self.assertEqual(self.client.post("/api/rent4/reset/set", json={"t": new.split("t=")[1], "new": "again-pw-1"}).status_code, 404)
        by_phone = self.client.post("/api/rent4/login", json={"username": "010-7777-4321", "password": "new-tenant-pw"})
        self.assertEqual(by_phone.json()["user"]["username"], f"k{cid}")

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
        self.tenant_login("101", "new-tenant-101")

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

    def test_accounts_list(self):
        self.import_sheet()
        rows = self.client.get("/api/rent4/lessor/accounts", headers=self.chic).json()["accounts"]
        self.assertEqual([r["username"] for r in rows], ["chic101", "chic102", "chic103"])
        self.assertNotIn("initial_code", rows[0])

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
        self.tenant_login("101", "forgot-new-101")
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)
        self.login("chic101", "forgot-new-101")


class TestContracts(ApiCase):
    def test_seeded_branch_account_alone_is_not_a_lessor(self):
        self.assertEqual(self.client.post("/auth/login", json={"username": "sinbi", "password": "1212"}).status_code, 401)
        sinbi = self.login("sinbi", SEED_PW)
        self.assertEqual(self.client.get("/api/rent4/me", headers=sinbi).status_code, 403)

    def test_membership_by_start_link_then_continue_by_phone(self):
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": "새빌딩", "building_code": "newb", "party_name": "최임대", "party_phone": "010-5555-1234"}).json()
        cid = res["contract"]["id"]
        self.assertIn("reset.html?t=", res["link"])
        party = self.start_link(res["link"], "newb-strong-pw")
        me = self.client.get("/api/rent4/me", headers=party).json()
        self.assertEqual((me["kind"], me["home"]), ("party", "contract.html"))
        mine = self.client.get("/api/rent4/contract/mine", headers=party).json()["contract"]
        self.assertEqual((mine["status"], mine["final_username"]), ("sent", "newb"))
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=party).status_code, 403)
        self.assertEqual(self.client.post(f"/api/rent4/admin/contracts/{cid}/paid", headers=self.admin).status_code, 409)
        res = self.client.post("/api/rent4/login", json={"username": "010-5555-1234", "password": "newb-strong-pw"})
        party = {"Authorization": f"Bearer {res.json()['token']}"}
        auth = self.conclude(party, cid, self.admin, "admin/contracts", "임대인.html")
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
        party = self.start_link(res["link"], "new-tenant-pw")
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
        res = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "101", "party_name": "신입주", "party_phone": "010-7777-4321", "rent": 300000, "mgmt": 100000,
            "start": "2026-03-15"}).json()
        return res["contract"]["id"], self.start_link(res["link"], "new-tenant-pw")

    def make_lessor(self, code, name, party, phone):
        """setUp 의 시크빌 임대인도 본인 확인을 거쳐야 동의할 수 있다."""
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": name, "building_code": code, "party_name": party, "party_phone": phone, "fee": 300000})
        cid = res.json()["contract"]["id"]
        party_auth = self.start_link(res.json()["link"], f"pw-{code}-owner")
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
        self.assertTrue(self.kakao("login", "kakao-code-2").endswith("login.html#oauth=unlinked"))
        cid, party = self.new_lease_party()
        self.kakao("link", "kakao-code-1", party)
        other = self.tenant_login("102")
        res = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "103", "party_name": "다른사람", "party_phone": "010-8888-1111", "start": "2026-04-01"}).json()
        other = self.start_link(res["link"], "other-pw-1")
        self.assertTrue(self.kakao("link", "kakao-code-1", other).endswith("contract.html#oauth=used"))

    def test_link_requires_login(self):
        res = self.client.post("/api/rent4/oauth/kakao/start", json={"purpose": "link"})
        self.assertEqual(res.status_code, 400)


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


class TestFind(ApiCase):
    """아이디·비밀번호 찾기: 미리 연결한 구글 계정 → 이름·전화·부동산 확인 (구글은 가짜 응답)."""

    def setUp(self):
        super().setUp()                                   # 시크빌 임대인은 구글 키 없이 먼저 만든다
        from unittest import mock
        from apps.rent4.backend import oauth
        self.oauth = oauth
        env = mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "g-id", "GOOGLE_CLIENT_SECRET": "g-secret"})
        env.start()
        self.addCleanup(env.stop)
        fake = mock.patch.object(oauth, "_http_json", side_effect=self._fake_http)
        fake.start()
        self.addCleanup(fake.stop)
        self.import_sheet()
        self.room = Room.first(branch="main", room="103")
        self.tenant = self.tenant_login("103")

    def _fake_http(self, url, data=None, token=""):
        if url == self.oauth.PROVIDERS["google"]["token"]:
            return {"access_token": "at-" + data["code"]}
        return {"sub": token[3:], "name": "구글사용자", "email": "u@gmail.test"}

    def google(self, purpose, sub, auth=None, back=""):
        res = self.client.post("/api/rent4/oauth/google/start", json={"purpose": purpose, "back": back}, headers=auth or {})
        self.assertEqual(res.status_code, 200, res.text)
        state = re.search(r"state=([^&]+)", res.json()["url"])[1]
        loc = self.client.get(f"/api/rent4/oauth/google/callback?code={sub}&state={state}", follow_redirects=False)
        from urllib.parse import unquote
        return unquote(loc.headers["location"])

    def find_token(self, sub="g-103"):
        loc = self.google("find", sub)
        self.assertIn("/rent4/find.html#f=", loc)
        return loc.split("#f=")[1]

    def info(self, f, **kw):
        body = {"f": f, "name": self.room.tenant_name, "phone": self.room.phone, "property": "시크빌 103호", **kw}
        return body

    def test_link_from_account_returns_to_tenant_page(self):
        self.assertTrue(self.google("link", "g-103", self.tenant, "account").endswith("/rent4/임차인.html#oauth=linked"))
        linked = self.client.get("/api/rent4/oauth/providers", headers=self.tenant).json()["linked"]
        self.assertEqual(linked[0]["provider"], "google")

    def test_unlinked_google_cannot_find(self):
        self.assertTrue(self.google("find", "g-nobody").endswith("/rent4/find.html#oauth=unlinked"))

    def test_find_id_with_matching_info(self):
        self.google("link", "g-103", self.tenant, "account")
        f = self.find_token()
        res = self.client.post("/api/rent4/find/id", json=self.info(f, property=" 시크빌103 "))
        self.assertEqual((res.status_code, res.json().get("username")), (200, "chic103"), res.text)

    def test_wrong_info_counts_and_locks(self):
        self.google("link", "g-103", self.tenant, "account")
        f = self.find_token()
        for _ in range(5):
            bad = self.client.post("/api/rent4/find/id", json=self.info(f, property="시크빌 104호"))
            self.assertEqual(bad.status_code, 422)
            self.assertNotIn("부동산", bad.json()["error"]["message"] if "error" in bad.json() else bad.text)
        locked = self.client.post("/api/rent4/find/id", json=self.info(f))
        self.assertEqual(locked.status_code, 403)

    def test_find_pw_sets_new_password_once(self):
        self.google("link", "g-103", self.tenant, "account")
        f = self.find_token()
        early = self.client.post("/api/rent4/find/pw/set", json={"f": f, "new": "brand-new-pw"})
        self.assertEqual(early.status_code, 400)
        wrong_id = self.client.post("/api/rent4/find/pw/verify", json=self.info(f, username="chic104"))
        self.assertEqual(wrong_id.status_code, 422)
        ok = self.client.post("/api/rent4/find/pw/verify", json=self.info(f, username="chic103"))
        self.assertEqual(ok.status_code, 200, ok.text)
        res = self.client.post("/api/rent4/find/pw/set", json={"f": f, "new": "brand-new-pw"})
        self.assertEqual((res.status_code, res.json()["home"]), (200, "임차인.html"), res.text)
        self.assertEqual(self.client.post("/api/rent4/login", json={"username": "chic103", "password": "brand-new-pw"}).status_code, 200)
        again = self.client.post("/api/rent4/find/pw/set", json={"f": f, "new": "another-pw"})
        self.assertEqual(again.status_code, 403)




class TestChat(ApiCase):
    """대화: 임차인 ↔ 자기 건물 임대인 · 임대인 ↔ 관리인 · 관리인 ↔ 모두. 분류 고장 · 문의 · 안내."""

    def setUp(self):
        from apps.rent4.backend import chat
        chat._last_seen.clear()                   # 앞 테스트의 '접속 중' 기록이 재설정 링크를 채팅으로 보내지 않게
        super().setUp()

    def send(self, auth, to, text="보일러가 안 켜져요", category="고장"):
        return self.client.post("/api/rent4/chat/send", headers=auth, json={"to": to, "text": text, "category": category})

    def test_contacts_by_role(self):
        self.import_sheet()
        tenant = self.tenant_login("101")
        names = lambda auth: [c["username"] for c in self.client.get("/api/rent4/chat/contacts", headers=auth).json()["contacts"]]  # noqa: E731
        self.assertEqual(names(tenant), ["chic"])
        self.assertIn("admin", names(self.chic))
        self.assertIn("chic101", names(self.chic))
        self.assertTrue({"chic", "chic101"} <= set(names(self.admin)))

    def test_two_way_thread_and_read(self):
        self.import_sheet()
        tenant = self.tenant_login("101")
        res = self.send(tenant, "chic")
        self.assertEqual((res.status_code, res.json()["message"]["category"]), (200, "고장"), res.text)
        c = self.client.get("/api/rent4/chat/contacts", headers=self.chic).json()["contacts"]
        self.assertEqual(next(x for x in c if x["username"] == "chic101")["unread"], 1)
        self.assertEqual(self.send(self.chic, "chic101", "내일 수리 기사 방문합니다", "안내").status_code, 200)
        thread = self.client.get("/api/rent4/chat/thread/chic101", headers=self.chic).json()["messages"]
        self.assertEqual([(m["mine"], m["category"]) for m in thread], [(False, "고장"), (True, "안내")])
        self.assertTrue(thread[0]["read_at"])                                  # 열면 읽음
        mine = self.client.get("/api/rent4/chat/thread/chic", headers=tenant).json()["messages"]
        self.assertEqual(mine[-1]["text"], "내일 수리 기사 방문합니다")

    def test_rules(self):
        self.import_sheet()
        tenant = self.tenant_login("101")
        self.assertEqual(self.send(tenant, "chic", category="잡담").status_code, 422)
        self.assertEqual(self.send(tenant, "chic", text="  ").status_code, 422)
        self.assertEqual(self.send(tenant, "admin").status_code, 403)          # 임차인 → 관리인 직접은 안 됨
        self.assertEqual(self.send(tenant, "chic102").status_code, 403)
        self.assertEqual(self.client.get("/api/rent4/chat/thread/chic102", headers=tenant).status_code, 403)
        self.assertEqual(self.send(self.admin, "chic101", "관리인 공지", "안내").status_code, 200)


class TestPaste(ApiCase):
    """만능 붙여 넣기: 텍스트 · 엑셀 파일 · 구글 시트 주소 · 사진(가짜 Gemini) → bank / excel / contract."""

    def paste(self, **body):
        return self.client.post("/api/rent4/lessor/paste", headers=self.chic, json=body)

    def test_text_detection(self):
        self.assertEqual(self.paste(text="신한 03/05 입금 250,000 홍길동").json()["kind"], "bank")
        r = self.paste(text=SHEET).json()
        self.assertEqual(r["kind"], "excel")
        self.assertEqual(self.paste(text="https://example.com/a.xlsx").status_code, 422)
        self.assertEqual(self.paste(text="  ").status_code, 422)

    def test_xlsx_file_becomes_sheet_text(self):
        import base64, io, openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        for line in SHEET.split("\n"):
            ws.append([int(c) if c.isdigit() and len(c) > 4 else (c or None) for c in line.split("\t")])
        buf = io.BytesIO()
        wb.save(buf)
        r = self.paste(file=base64.b64encode(buf.getvalue()).decode(), filename="2026.xlsx").json()
        self.assertEqual(r["kind"], "excel")
        prev = self.client.post("/api/rent4/lessor/import/preview", headers=self.chic, json={"text": r["text"]})
        self.assertEqual(prev.status_code, 200, prev.text)
        self.assertEqual(prev.json()["year"], 2026)

    def test_google_sheet_url(self):
        from unittest import mock
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = SHEET.encode()
        fake.__enter__.return_value.headers = {"Content-Type": "text/tab-separated-values"}
        with mock.patch("urllib.request.urlopen", return_value=fake) as urlopen:
            r = self.paste(text="https://docs.google.com/spreadsheets/d/AbC123_x/edit#gid=42").json()
        self.assertEqual(urlopen.call_args[0][0].full_url,
                         "https://docs.google.com/spreadsheets/d/AbC123_x/export?format=tsv&gid=42")
        self.assertEqual((r["kind"], r["source"]), ("excel", "url"))

    def test_photo_of_paper_contract_then_register(self):
        import json as _json
        from unittest import mock
        from shared.ai import gemini_client
        self.import_sheet()
        reply = _json.dumps({"kind": "contract", "contract": {
            "room": "102호", "party_name": "종이임차", "party_phone": "010-3333-4444", "deposit": "5,000,000",
            "rent": 350000, "mgmt": 50000, "start": "2026-11-01", "end": "2027-10-31", "lease_type": "월세",
            "pay_timing": "선불", "jumin": "900101-1234567"}})
        with mock.patch.object(gemini_client.GeminiClient, "is_available", True, create=True), \
             mock.patch.object(gemini_client.GeminiClient, "generate_with_image", mock.AsyncMock(return_value=reply)):
            r = self.paste(file="aGVsbG8=", filename="contract.jpg", mime="image/jpeg").json()
        self.assertEqual(r["kind"], "contract", r)
        draft = r["contract"]
        self.assertEqual((draft["room"], draft["deposit"], draft["pay_timing"]), ("102", 5000000, "선불"))
        self.assertNotIn("900101", str(r))                                  # 주민번호는 옮기지 않는다
        res = self.client.post("/api/rent4/lessor/contracts/paper", headers=self.chic, json=draft)
        self.assertEqual(res.status_code, 200, res.text)
        c = res.json()["contract"]
        self.assertEqual((c["status"], c["paper"], c["username"]), ("active", True, "chic102"))
        room = Room.first(branch="main", room="102")
        self.assertEqual((room.tenant_name, room.monthly_due, room.move_in), ("종이임차", 400000, "2026-11-01"))

    def test_photo_without_ai_key(self):
        from unittest import mock
        from shared.ai import gemini_client
        with mock.patch.object(gemini_client.GeminiClient, "is_available", False, create=True):
            self.assertEqual(self.paste(file="aGVsbG8=", filename="a.png", mime="image/png").status_code, 400)


class TestListings(ApiCase):
    """매물 공개: 임대인 공개 → 누구나 목록 (임차인 정보 없음) → 체결되면 닫힘."""

    def publish(self, room="102", **kw):
        body = {"room": room, "deposit": 5000000, "rent": 350000, "mgmt": 50000, "available_from": "2026-12-01",
                "description": "남향, 풀옵션", **kw}
        return self.client.post("/api/rent4/lessor/listings", headers=self.chic, json=body)

    def test_publish_public_list_and_close(self):
        self.import_sheet()
        res = self.publish()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.publish().status_code, 409)                    # 같은 호실 두 번 공개 안 됨
        pub = self.client.get("/api/rent4/listings").json()["listings"]      # 로그인 없이
        self.assertEqual([(l["building"], l["room"], l["monthly"]) for l in pub], [("시크빌", "102", 400000)])
        self.assertNotIn("김철수", str(pub))
        self.assertNotIn("phone", str(pub))
        lid = res.json()["listing"]["id"]
        self.assertEqual(self.client.post(f"/api/rent4/lessor/listings/{lid}/close", headers=self.chic).status_code, 200)
        self.assertEqual(self.client.get("/api/rent4/listings").json()["listings"], [])

    def test_other_lessor_and_tenant_cannot_publish(self):
        self.import_sheet()
        tenant = self.tenant_login("101")
        self.assertEqual(self.client.post("/api/rent4/lessor/listings", headers=tenant, json={"room": "102"}).status_code, 403)
        self.assertEqual(self.publish(room="999").status_code, 404)

    def test_paper_contract_closes_listing(self):
        self.import_sheet()
        self.publish(room="102")
        res = self.client.post("/api/rent4/lessor/contracts/paper", headers=self.chic, json={
            "room": "102", "party_name": "새임차", "party_phone": "010-4444-5555", "start": "2026-12-01"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.client.get("/api/rent4/listings").json()["listings"], [])
        mine = self.client.get("/api/rent4/lessor/listings", headers=self.chic).json()["listings"]
        self.assertEqual(mine[0]["closed_reason"], "contracted")


class GoogleCase(ApiCase):
    """구글은 가짜 응답 (code = 구글 sub)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        from apps.rent4.backend import oauth
        self.oauth = oauth
        env = mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "g-id", "GOOGLE_CLIENT_SECRET": "g-secret"})
        env.start()
        self.addCleanup(env.stop)
        fake = mock.patch.object(oauth, "_http_json", side_effect=self._fake_http)
        fake.start()
        self.addCleanup(fake.stop)

    def _fake_http(self, url, data=None, token=""):
        if url == self.oauth.PROVIDERS["google"]["token"]:
            return {"access_token": "at-" + data["code"]}
        return {"sub": token[3:], "name": "구글이름", "email": "me@gmail.test"}

    def google(self, purpose, sub, auth=None):
        from urllib.parse import unquote
        res = self.client.post("/api/rent4/oauth/google/start", json={"purpose": purpose}, headers=auth or {})
        self.assertEqual(res.status_code, 200, res.text)
        state = re.search(r"state=([^&]+)", res.json()["url"])[1]
        loc = self.client.get(f"/api/rent4/oauth/google/callback?code={sub}&state={state}", follow_redirects=False)
        return unquote(loc.headers["location"])

    def member(self, sub="g-new", username="hong77", phone="010-2468-1357", password="member-pw-1"):
        loc = self.google("signup", sub)
        self.assertIn("signup.html#s=", loc)
        res = self.client.post("/api/rent4/signup", json={"s": loc.split("#s=")[1], "username": username,
                               "name": "홍새로", "phone": phone, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['token']}"}


class TestSignup(GoogleCase):
    def test_signup_then_member_home_and_login(self):
        auth = self.member()
        me = self.client.get("/api/rent4/me", headers=auth).json()
        self.assertEqual((me["kind"], me["home"], me["username"]), ("member", "index.html", "hong77"))
        self.assertEqual(self.client.post("/api/rent4/login", json={"username": "hong77", "password": "member-pw-1"}).status_code, 200)
        by_phone = self.client.post("/api/rent4/login", json={"username": "010-2468-1357", "password": "member-pw-1"}).json()
        self.assertEqual(by_phone["user"]["username"], "hong77")
        self.assertEqual(self.client.get("/api/rent4/tenant/me", headers=auth).status_code, 403)
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=auth).status_code, 403)

    def test_existing_member_skips_signup(self):
        self.member()
        loc = self.google("signup", "g-new")
        self.assertIn("signup.html#token=", loc)

    def test_signup_rules(self):
        loc = self.google("signup", "g-x")
        sess = loc.split("#s=")[1]
        bad = lambda **kw: self.client.post("/api/rent4/signup", json={"s": sess, "username": "goodname", "name": "가",  # noqa: E731
                                                                       "phone": "010-1111-9999", "password": "pass-123", **kw})
        self.assertEqual(bad(username="Ab").status_code, 422)
        self.assertEqual(bad(username="chic101").status_code, 409)             # 호실 아이디 모양은 못 씀
        self.assertEqual(bad(username="admin").status_code, 409)
        self.assertEqual(bad(phone="123").status_code, 422)
        self.assertEqual(bad(password="111111").status_code, 422)
        self.assertEqual(self.client.post("/api/rent4/signup", json={"s": "nope", "username": "goodname", "name": "가",
                         "phone": "010-1111-9999", "password": "pass-123"}).status_code, 403)

    def test_member_find_id_without_property(self):
        self.member()
        loc = self.google("find", "g-new")
        f = loc.split("#f=")[1]
        res = self.client.post("/api/rent4/find/id", json={"f": f, "name": "홍새로", "phone": "01024681357", "property": ""})
        self.assertEqual(res.json().get("username"), "hong77", res.text)


class TestApply(GoogleCase):
    """매물 → 회원 신청 → 임대인 채팅 안내 → 수락 → 동의 → 잔금 확인 → 회원 아이디로 입주."""

    def listing(self, room="102"):
        self.import_sheet()
        res = self.client.post("/api/rent4/lessor/listings", headers=self.chic, json={
            "room": room, "deposit": 5000000, "rent": 350000, "mgmt": 50000, "available_from": "2026-12-01"})
        return res.json()["listing"]["id"]

    def apply(self, auth, lid, **kw):
        return self.client.post(f"/api/rent4/listings/{lid}/apply", headers=auth, json={"start": "2026-12-05", "message": "직장인입니다", **kw})

    def test_full_flow_member_becomes_tenant(self):
        from apps.rent4.backend import chat
        lid = self.listing()
        member = self.member()
        res = self.apply(member, lid)
        self.assertEqual(res.status_code, 200, res.text)
        cid = res.json()["contract"]["id"]
        me = self.client.get("/api/rent4/me", headers=member).json()
        self.assertEqual(me["kind"], "party")
        inbox = self.client.get("/api/rent4/chat/inbox", headers=self.chic).json()["messages"]
        self.assertTrue(any("102호 계약 신청" in m["text"] and m["category"] == "문의" for m in inbox))
        contacts = [c["username"] for c in self.client.get("/api/rent4/chat/contacts", headers=member).json()["contacts"]]
        self.assertEqual(contacts, ["chic"])                                  # 신청자 ↔ 임대인 채팅
        self.assertEqual(self.client.post(f"/api/rent4/lessor/contracts/{cid}/link", headers=self.chic).status_code, 409)
        self.assertEqual(self.client.post(f"/api/rent4/lessor/contracts/{cid}/accept", headers=self.chic).status_code, 200)
        self.assertEqual(self.client.post("/api/rent4/contract/mine/sign", headers=member, json={"agree": True}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/rent4/lessor/contracts/{cid}/paid", headers=self.chic).status_code, 200)
        me = self.client.get("/api/rent4/me", headers=member).json()
        self.assertEqual((me["kind"], me["username"], me["room"]), ("tenant", "hong77", "102"))
        self.assertEqual(self.client.get("/api/rent4/tenant/me", headers=member).status_code, 200)
        self.login("hong77", "member-pw-1")                                   # 비밀번호 그대로
        self.assertEqual(self.client.get("/api/rent4/listings").json()["listings"], [])
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic102", "password": "secret-102"}).status_code, 401)

    def test_one_open_application_and_others_closed_on_accept(self):
        lid = self.listing()
        a = self.member("g-a", "aaaa1", "010-1000-0001")
        b = self.member("g-b", "bbbb2", "010-1000-0002")
        ca = self.apply(a, lid).json()["contract"]["id"]
        self.assertEqual(self.apply(a, lid).status_code, 409)                  # 하나만
        cb = self.apply(b, lid).json()["contract"]["id"]
        self.client.post(f"/api/rent4/lessor/contracts/{ca}/accept", headers=self.chic)
        mine_b = self.client.get("/api/rent4/contract/mine", headers=b).json()["contract"]
        self.assertEqual((mine_b["id"], mine_b["status"]), (cb, "cancelled"))
        self.assertEqual(self.client.get("/api/rent4/me", headers=b).json()["kind"], "member")   # 다시 일반 회원
        texts = [m["text"] for m in self.client.get("/api/rent4/chat/inbox", headers=b).json()["messages"]]
        self.assertTrue(any("마감" in t for t in texts))

    def test_non_member_cannot_apply(self):
        lid = self.listing()
        tenant = self.tenant_login("101")
        self.assertEqual(self.apply(tenant, lid).status_code, 409)
        self.assertEqual(self.client.post(f"/api/rent4/listings/{lid}/apply", json={}).status_code, 401)


class TestMoveOut(TestApply):
    """전출: 공실 + 임차인 자격 끝. 회원은 일반 회원으로 돌아가고, 1년 안 쓰면 삭제."""

    def concluded_member(self):
        lid = self.listing()
        member = self.member()
        cid = self.apply(member, lid).json()["contract"]["id"]
        self.client.post(f"/api/rent4/lessor/contracts/{cid}/accept", headers=self.chic)
        self.client.post("/api/rent4/contract/mine/sign", headers=member, json={"agree": True})
        self.client.post(f"/api/rent4/lessor/contracts/{cid}/paid", headers=self.chic)
        return member

    def test_member_moves_out_back_to_member(self):
        member = self.concluded_member()
        res = self.client.post("/api/rent4/lessor/rooms/102/move-out", headers=self.chic, json={"date": "2027-05-31"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(Room.first(branch="main", room="102").status, "vacant")
        me = self.client.get("/api/rent4/me", headers=member).json()
        self.assertEqual(me["kind"], "member")
        self.assertEqual(self.client.get("/api/rent4/tenant/me", headers=member).status_code, 403)
        self.login("hong77", "member-pw-1")                                   # 회원 비밀번호는 그대로
        self.assertEqual(TenantAccount.first(branch="main", room="102").username, "chic102")
        self.assertEqual([c["username"] for c in self.client.get("/api/rent4/chat/contacts", headers=member).json()["contacts"]], [])

    def test_legacy_tenant_moves_out_locked(self):
        self.import_sheet()
        self.tenant_login("101")
        self.assertEqual(self.client.post("/api/rent4/lessor/rooms/101/move-out", headers=self.chic, json={}).status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)
        self.assertEqual(self.client.post("/api/rent4/lessor/rooms/101/move-out", headers=self.chic, json={}).status_code, 404)

    def test_inactive_member_purged_after_a_year(self):
        from apps.rent4.backend import members
        self.member()
        m = members.MemberAccount.first(username="hong77")
        m.last_active = "2020-01-01T00:00:00"
        m.save()
        self.assertEqual(members.purge_inactive(), 1)
        self.assertEqual(self.client.post("/auth/login", json={"username": "hong77", "password": "member-pw-1"}).status_code, 401)
        self.assertIsNone(self.oauth.SocialLink.first(username="hong77"))

    def test_tenant_member_not_purged(self):
        from apps.rent4.backend import members
        self.concluded_member()
        m = members.MemberAccount.first(username="hong77")
        m.last_active = "2020-01-01T00:00:00"
        m.save()
        self.assertEqual(members.purge_inactive(), 0)

if __name__ == "__main__":
    unittest.main()
