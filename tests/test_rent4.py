"""
tests/test_rent4.py — Rent4 (입금 문자 수납부) 검증.

  1. Excel 수납부 분석: 표기 흔들림 흡수, 주민번호 미저장, 합계 대조
  2. 은행 입금 문자 분석: 여러 형식 · 여러 건 붙여넣기 · 잔액 오인 방지
  3. 호실 매칭 & FIFO 귀속월 배정, 중복 반영 차단, 건물 간 호실 번호 분리
  4. 로그인: 관리인 · 임대인 · 임차인 역할 판정, 건물 범위, 첫 로그인 비밀번호 변경
  5. 계약 상호 확인: 발행 → 상대방 본인 확인·동의 → 납부 확인 → 임시 비밀번호 → 변경
"""
import os
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

    def set_first_pw(self, auth, new):
        """링크로 들어온 첫 설정: 현재 비밀번호 없이."""
        self.change_pw(auth, "", new)

    def start(self, path, body, home):
        """링크로 바로 로그인 → 세션 헤더. 임시 비밀번호는 응답에 없다."""
        res = self.client.post(f"/api/rent4/{path}", json=body)
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["home"], home)
        self.assertNotIn("temp_password", data)
        return {"Authorization": f"Bearer {data['token']}"}

    def join(self, token, phone, password):
        """① 계약 링크: 휴대폰 뒤 4자리 + 비밀번호 설정 → 계약 진행용 로그인."""
        return self.start("contract/join", {"t": token, "phone4": phone[-4:], "new": password}, "contract.html")

    def sign_and_conclude(self, party, cid, issuer, kind_path, home):
        """② 동의 → ③ 납부 확인 → '시작하기'로 최종 계정 세션 (비밀번호는 그대로 넘어간다)."""
        self.assertEqual(self.client.post("/api/rent4/contract/mine/sign", headers=party, json={"agree": True}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/rent4/{kind_path}/{cid}/paid", headers=issuer).status_code, 200)
        res = self.client.post("/api/rent4/contract/mine/continue", headers=party)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["home"], home)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def make_lessor(self, code, name, party, phone):
        """관리인 가맹 계약 발행 → 임대인이 링크에서 비밀번호 설정 → 동의 → 가맹비 확인 → 시작."""
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": name, "building_code": code, "party_name": party, "party_phone": phone, "fee": 300000})
        self.assertEqual(res.status_code, 200, res.text)
        cid, token = res.json()["contract"]["id"], res.json()["token"]
        party_auth = self.join(token, phone, f"pw-{code}-owner")
        return self.sign_and_conclude(party_auth, cid, self.admin, "admin/contracts", "임대인.html")

    def import_sheet(self, auth=None, branch=""):
        q = f"?branch={branch}" if branch else ""
        return self.client.post(f"/api/rent4/lessor/import/commit{q}", json={"text": SHEET}, headers=auth or self.chic).json()

    def invite(self, room, action="reset"):
        res = self.client.post(f"/api/rent4/lessor/accounts/{room}/{action}", headers=self.chic)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["token"]

    def start_invite(self, token, room):
        phone4 = Room.first(branch="main", room=room).phone[-4:]
        return self.start("invite/start", {"t": token, "phone4": phone4}, "임차인.html")

    def tenant_login(self, room="103"):
        """임대인이 보낸 안내 링크 → 휴대폰 확인 → 바로 로그인 → 비밀번호 설정까지 마친 임차인 헤더."""
        auth = self.start_invite(self.invite(room), room)
        self.set_first_pw(auth, "secret-" + room)
        return auth


class TestApi(ApiCase):
    def test_lessor_requires_login(self):
        self.assertEqual(self.client.get("/api/rent4/lessor/board").status_code, 401)

    def test_me_routes_each_role_home(self):
        self.import_sheet()
        me = self.client.get("/api/rent4/me", headers=self.chic).json()
        self.assertEqual((me["kind"], me["home"], me["building"]["name"]), ("lessor", "임대인.html", "시크빌"))
        admin = self.client.get("/api/rent4/me", headers=self.admin).json()
        self.assertEqual((admin["kind"], admin["home"]), ("admin", "admin.html"))
        auth = self.start_invite(self.invite("101"), "101")
        t = self.client.get("/api/rent4/me", headers=auth).json()
        self.assertEqual((t["kind"], t["room"], t["home"], t["must_change_password"]), ("tenant", "101", "임차인.html", True))

    def test_pages_served_with_korean_names(self):
        for page in ("index.html", "admin.html", "임대인.html", "임차인.html"):
            self.assertEqual(self.client.get(f"/rent4/{page}").status_code, 200, page)

    def test_tenant_must_set_password_first(self):
        self.import_sheet()
        auth = self.start_invite(self.invite("103"), "103")
        self.assertEqual(self.client.get("/api/rent4/tenant/me", headers=auth).status_code, 403)
        weak = self.client.post("/api/rent4/account/password", headers=auth, json={"current": "", "new": "111111"})
        self.assertEqual(weak.status_code, 422)
        self.set_first_pw(auth, "secret-103")
        self.assertEqual(self.client.get("/api/rent4/tenant/me", headers=auth).status_code, 200)
        self.login("chic103", "secret-103")
        self.assertEqual(TenantAccount.first(branch="main", room="103").initial_code, "")

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
        admin = self.admin
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=admin).status_code, 400)
        board = self.client.get("/api/rent4/lessor/board?year=2026&branch=main", headers=admin).json()
        self.assertEqual(len(board["rooms"]), 4)
        names = [b["name"] for b in self.client.get("/api/rent4/admin/buildings", headers=admin).json()["buildings"]]
        self.assertEqual(names, ["시크빌", "선호아트빌", "신비주택"])

    def test_new_tenant_resets_account(self):
        self.import_sheet()
        self.tenant_login("101")
        changed = SHEET.replace("홍길동", "고길동").replace("010-1111-2222", "010-9999-8888")
        self.client.post("/api/rent4/lessor/import/commit", json={"text": changed}, headers=self.chic)
        acc = TenantAccount.first(branch="main", room="101")
        self.assertTrue(acc.must_change and acc.initial_code)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)

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

    def test_accounts_list_hides_codes(self):
        self.import_sheet()
        rows = self.client.get("/api/rent4/lessor/accounts", headers=self.chic).json()["accounts"]
        self.assertEqual([r["username"] for r in rows], ["chic101", "chic102", "chic103"])
        self.assertNotIn("initial_code", rows[0])
        self.assertEqual((rows[0]["phone"], rows[0]["link_sent"]), ("010-1111-2222", False))

    def test_invite_link_needs_phone_then_logs_in_once(self):
        self.import_sheet()
        r = self.client.post("/api/rent4/lessor/accounts/101/reset", headers=self.chic).json()
        self.assertEqual((r["username"], r["tenant_name"], r["phone"]), ("chic101", "홍길동", "010-1111-2222"))
        view = self.client.get(f"/api/rent4/invite/view?t={r['token']}").json()
        self.assertEqual((view["room"], view["phone_masked"], view["activated"]), ("101", "***-****-2222", False))
        bad = self.client.post("/api/rent4/invite/start", json={"t": r["token"], "phone4": "0000"})
        self.assertEqual(bad.status_code, 422)
        auth = self.start_invite(r["token"], "101")
        self.set_first_pw(auth, "invited-101")
        # 비밀번호를 정하면 링크는 만료 — 열리지도, 로그인되지도 않는다
        self.assertEqual(self.client.get(f"/api/rent4/invite/view?t={r['token']}").status_code, 404)
        self.assertEqual(self.client.post("/api/rent4/invite/start", json={"t": r["token"], "phone4": "2222"}).status_code, 404)
        self.assertEqual(self.client.post("/api/rent4/lessor/accounts/101/invite", headers=self.chic).status_code, 404)

    def test_reset_kills_old_link_and_password(self):
        self.import_sheet()
        self.tenant_login("101")
        first = self.invite("101", "reset")
        second = self.invite("101", "reset")
        self.assertEqual(self.client.get(f"/api/rent4/invite/view?t={first}").status_code, 404)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "secret-101"}).status_code, 401)
        auth = self.start_invite(second, "101")
        self.set_first_pw(auth, "after-reset-101")
        self.login("chic101", "after-reset-101")

    def test_existing_tenant_can_log_in_by_phone(self):
        self.import_sheet()
        self.tenant_login("101")
        res = self.client.post("/api/rent4/login", json={"username": "010-1111-2222", "password": "secret-101"}).json()
        self.assertEqual(res["user"]["username"], "chic101")
        wrong = self.client.post("/api/rent4/login", json={"username": "010-1111-2222", "password": "nope-nope"})
        self.assertEqual(wrong.status_code, 401)

    def test_new_tenant_invalidates_old_invite(self):
        self.import_sheet()
        token = self.invite("101")
        changed = SHEET.replace("홍길동", "고길동").replace("010-1111-2222", "010-9999-8888")
        self.client.post("/api/rent4/lessor/import/commit", json={"text": changed}, headers=self.chic)
        self.assertEqual(self.client.get(f"/api/rent4/invite/view?t={token}").status_code, 404)


class TestContracts(ApiCase):
    def test_seeded_branch_account_alone_is_not_a_lessor(self):
        self.assertEqual(self.client.post("/auth/login", json={"username": "sinbi", "password": "1212"}).status_code, 401)
        sinbi = self.login("sinbi", SEED_PW)
        self.assertEqual(self.client.get("/api/rent4/me", headers=sinbi).status_code, 403)

    def test_membership_password_first_then_continue_by_phone(self):
        res = self.client.post("/api/rent4/admin/contracts", headers=self.admin, json={
            "building_name": "새빌딩", "building_code": "newb", "party_name": "최임대", "party_phone": "010-5555-1234"}).json()
        cid, token = res["contract"]["id"], res["token"]
        view = self.client.get(f"/api/rent4/contract/view?t={token}").json()["contract"]
        self.assertEqual((view["status"], view["party_phone_masked"], view["final_username"], view["password_set"]),
                         ("sent", "***-****-1234", "newb", False))
        self.assertNotIn("party_phone", view)
        bad = self.client.post("/api/rent4/contract/join", json={"t": token, "phone4": "0000", "new": "newb-strong-pw"})
        self.assertEqual(bad.status_code, 422)
        party = self.join(token, "010-5555-1234", "newb-strong-pw")
        # ① 비밀번호를 정하면 링크는 만료 — 링크를 잃어버린 셈 치고 휴대폰 번호로 다시 로그인해서 이어 간다
        self.assertEqual(self.client.get(f"/api/rent4/contract/view?t={token}").status_code, 404)
        res = self.client.post("/api/rent4/login", json={"username": "010-5555-1234", "password": "newb-strong-pw"})
        self.assertEqual(res.status_code, 200, res.text)
        party = {"Authorization": f"Bearer {res.json()['token']}"}
        me = self.client.get("/api/rent4/me", headers=party).json()
        self.assertEqual((me["kind"], me["home"]), ("party", "contract.html"))
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=party).status_code, 403)
        self.assertEqual(self.client.post(f"/api/rent4/admin/contracts/{cid}/paid", headers=self.admin).status_code, 409)
        self.assertEqual(self.client.post("/api/rent4/contract/mine/continue", headers=party).status_code, 409)
        auth = self.sign_and_conclude(party, cid, self.admin, "admin/contracts", "임대인.html")
        me = self.client.get("/api/rent4/me", headers=auth).json()
        self.assertEqual((me["kind"], me["must_change_password"], me["building"]["name"]), ("lessor", False, "새빌딩"))
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=auth).status_code, 200)
        # 같은 비밀번호로 최종 아이디와 휴대폰 번호 모두 로그인 — 계약 진행용 로그인은 닫힘
        self.login("newb", "newb-strong-pw")
        by_phone = self.client.post("/api/rent4/login", json={"username": "01055551234", "password": "newb-strong-pw"}).json()
        self.assertEqual(by_phone["user"]["username"], "newb")
        self.assertEqual(self.client.post("/auth/login", json={"username": f"k{cid}", "password": "newb-strong-pw"}).status_code, 401)
        self.assertEqual(self.client.post(f"/api/rent4/admin/contracts/{cid}/relink", headers=self.admin).status_code, 404)

    def test_lease_contract_moves_tenant_in_and_logs_in_by_link(self):
        self.import_sheet()
        res = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "101", "party_name": "신입주", "party_phone": "010-7777-4321", "deposit": 3000000,
            "rent": 300000, "mgmt": 100000, "start": "2026-03-15", "end": "2027-03-14"}).json()
        cid, token = res["contract"]["id"], res["token"]
        party = self.join(token, "010-7777-4321", "new-tenant-pw")
        self.assertEqual(self.client.get("/api/rent4/contract/view?t=" + token).status_code, 404)
        auth = self.sign_and_conclude(party, cid, self.chic, "lessor/contracts", "임차인.html")
        room = Room.first(branch="main", room="101")
        self.assertEqual((room.tenant_name, room.monthly_due, room.move_in), ("신입주", 400000, "2026-03-15"))
        self.login("chic101", "new-tenant-pw")                       # 링크에서 정한 비밀번호가 그대로
        view = self.client.get("/api/rent4/tenant/me", headers=auth).json()
        self.assertEqual([m["ym"] for m in view["months"]], ["2026-03"])     # 이전 임차인 기록 없음

    def test_other_lessor_cannot_touch_contract(self):
        self.import_sheet()
        cid = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "102", "party_name": "가", "party_phone": "010-1111-0000", "start": "2026-04-01"}).json()["contract"]["id"]
        sunho = self.make_lessor("sunho", "선호아트빌", "박임대", "010-2222-3333")
        self.assertEqual(self.client.post(f"/api/rent4/lessor/contracts/{cid}/paid", headers=sunho).status_code, 404)


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
        code = TenantAccount.first(branch="main", room="102").initial_code
        self.assertEqual(len(code), 8)
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic102", "password": "123456"}).status_code, 401)

    def membership(self, code):
        rows = self.client.get("/api/rent4/admin/contracts", headers=self.admin).json()["contracts"]
        return next(c for c in rows if c["building_code"] == code)

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

    def test_lessor_lost_password_reset_by_new_link(self):
        c = self.membership("chic")
        res = self.client.post(f"/api/rent4/admin/contracts/{c['id']}/reset", headers=self.admin).json()
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic", "password": "pw-chic-owner"}).status_code, 401)
        view = self.client.get(f"/api/rent4/contract/view?t={res['token']}").json()["contract"]
        self.assertTrue(view["reset_at"])
        auth = self.start("contract/start", {"t": res["token"]}, "임대인.html")
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=auth).status_code, 403)
        self.set_first_pw(auth, "pw-chic-after-reset")
        self.assertEqual(self.client.get("/api/rent4/lessor/board", headers=auth).status_code, 200)

    def test_reset_again_after_setup_reopens_once(self):
        c = self.membership("chic")
        for i in range(2):
            token = self.client.post(f"/api/rent4/admin/contracts/{c['id']}/reset", headers=self.admin).json()["token"]
            auth = self.start("contract/start", {"t": token}, "임대인.html")
            self.set_first_pw(auth, f"chic-round-{i}")
            self.assertEqual(self.client.get(f"/api/rent4/contract/view?t={token}").status_code, 404)
        self.login("chic", "chic-round-1")

    def test_tenant_reset_only_while_still_resident(self):
        self.import_sheet()
        res = self.client.post("/api/rent4/lessor/contracts", headers=self.chic, json={
            "room": "101", "party_name": "신입주", "party_phone": "010-7777-4321", "rent": 300000, "mgmt": 100000,
            "start": "2026-03-15"}).json()
        cid, token = res["contract"]["id"], res["token"]
        self.sign_and_conclude(self.join(token, "010-7777-4321", "first-pw-101"), cid, self.chic, "lessor/contracts", "임차인.html")
        new = self.client.post(f"/api/rent4/lessor/contracts/{cid}/reset", headers=self.chic).json()["token"]
        self.assertEqual(self.client.post("/auth/login", json={"username": "chic101", "password": "first-pw-101"}).status_code, 401)
        self.assertTrue(self.client.get(f"/api/rent4/contract/view?t={new}").json()["contract"]["login"]["can_start"])
        self.import_sheet()                                     # 수납부상 101호 임차인이 다시 홍길동으로 바뀜
        self.assertEqual(self.client.post(f"/api/rent4/lessor/contracts/{cid}/reset", headers=self.chic).status_code, 409)


class TestAdminSetupLink(ApiCase):
    def test_admin_link_logs_in_then_forced_setup(self):
        _, token = accounts.issue_admin_link("admin")
        self.assertEqual(self.client.post("/auth/login", json={"username": "admin", "password": SEED_PW}).status_code, 401)
        view = self.client.get(f"/api/rent4/setup/view?t={token}").json()
        self.assertEqual((view["username"], view["activated"]), ("admin", False))
        self.assertNotIn("temp_password", view)
        auth = self.start("setup/start", {"t": token}, "admin.html")
        me = self.client.get("/api/rent4/me", headers=auth).json()
        self.assertEqual((me["kind"], me["must_change_password"]), ("admin", True))
        self.assertEqual(self.client.get("/api/rent4/admin/buildings", headers=auth).status_code, 403)
        self.assertEqual(self.client.get("/api/rent4/lessor/board?branch=main", headers=auth).status_code, 403)
        self.set_first_pw(auth, "admin-chosen-pw")
        self.assertEqual(self.client.get("/api/rent4/admin/buildings", headers=auth).status_code, 200)
        self.assertEqual(self.client.get(f"/api/rent4/setup/view?t={token}").status_code, 404)
        self.assertEqual(self.client.post("/api/rent4/setup/start", json={"t": token}).status_code, 404)

    def test_new_link_blocks_previous_link(self):
        _, first = accounts.issue_admin_link("admin")
        _, second = accounts.issue_admin_link("admin")
        self.assertEqual(self.client.get(f"/api/rent4/setup/view?t={first}").status_code, 404)
        self.assertEqual(self.client.post("/api/rent4/setup/start", json={"t": first}).status_code, 404)
        self.start("setup/start", {"t": second}, "admin.html")


if __name__ == "__main__":
    unittest.main()
