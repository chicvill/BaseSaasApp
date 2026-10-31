"""
tests/test_rent4.py — Rent4 (입금 문자 수납부) 검증.

  1. Excel 수납부 분석: 표기 흔들림 흡수, 주민번호 미저장, 합계 대조
  2. 은행 입금 문자 분석: 여러 형식 · 여러 건 붙여넣기 · 잔액 오인 방지
  3. 호실 매칭 & FIFO 귀속월 배정, 중복 반영 차단
  4. API: 임대인 권한, 미리보기 → 확정, 임차인 조회 (입주 전 기록 숨김)
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

from fastapi.testclient import TestClient

from core.gateway import create_gateway
from apps.rent4.backend import bank_parser, excel_import, ledger
from apps.rent4.backend.models import Payment, Room
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


class Rent4Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rent4_")
        use_pool_dir(self.tmp)
        ledger.today = lambda: date(2026, 3, 20)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load_sheet(self):
        return excel_import.apply_sheet(excel_import.parse_sheet(SHEET))


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


class TestBankParser(Rent4Case):
    def test_multiline_and_single_line_formats(self):
        text = ("[Web발신]\n신한 03/05 14:32\n110-***-123456\n입금 250,000\n잔액 3,456,789\n홍길동\n\n"
                "[KB]03/06 09:10 123456**789 이영희 입금 150,000 잔액1,234,567")
        msgs = bank_parser.split_messages(text)
        self.assertEqual(len(msgs), 2)
        a, b = (bank_parser.parse_message(m, date(2026, 3, 20)) for m in msgs)
        self.assertEqual((a.amount, a.deposit_date, a.deposit_time, a.bank), (250_000, "2026-03-05", "14:32", "신한"))
        self.assertEqual(a.names[0], "홍길동")
        self.assertEqual((b.amount, b.names[0], b.bank), (150_000, "이영희", "KB"))

    def test_mixed_paste_splits_every_message(self):
        text = ("[Web발신]\n신한 03/05 14:32\n입금 250,000\n홍길동\n\n"
                "[KB]03/06 09:10 이영희 입금 150,000 잔액1,234,567\n\n"
                "농협 입금440,000원 03/07 김철수\n"
                "[Web발신]\n우리 03/08 입금 1,000 박영희")
        names = [bank_parser.parse_message(m, date(2026, 3, 20)).names[0] for m in bank_parser.split_messages(text)]
        self.assertEqual(names, ["홍길동", "이영희", "김철수", "박영희"])

    def test_glued_amount_and_balance_not_taken(self):
        d = bank_parser.parse_message("농협 입금440,000원 03/07 352-****-1234-53 김철수 잔액1,234,567원", date(2026, 3, 20))
        self.assertEqual((d.amount, d.names[0]), (440_000, "김철수"))

    def test_future_date_means_last_year(self):
        d = bank_parser.parse_message("신한 12/30 입금 1,000 홍길동", date(2026, 3, 20))
        self.assertEqual(d.deposit_date, "2025-12-30")

    def test_withdrawal_skipped(self):
        d = bank_parser.parse_message("신한 03/05 출금 50,000 편의점", date(2026, 3, 20))
        self.assertEqual(d.kind, "withdrawal")


class TestLedger(Rent4Case):
    def test_fifo_assigns_unpaid_month_first(self):
        self.load_sheet()
        dep = bank_parser.parse_message("신한 03/05 입금 150,000 이영희", date(2026, 3, 20))
        item = ledger.propose(dep, set())
        self.assertEqual((item["status"], item["room"], item["months"]), ("ready", "103", ["2026-02"]))

    def test_two_months_and_duplicate_block(self):
        self.load_sheet()
        dep = bank_parser.parse_message("신한 03/05 입금 300,000 이영희", date(2026, 3, 20))
        item = ledger.propose(dep, set())
        self.assertEqual(item["months"], ["2026-02", "2026-03"])
        self.assertEqual(ledger.commit(item)["status"], "saved")
        self.assertEqual(ledger.commit(item)["status"], "duplicate")
        again = ledger.propose(dep, {dep.fingerprint})
        self.assertEqual(again["status"], "duplicate")

    def test_amount_mismatch_is_diff(self):
        self.load_sheet()
        dep = bank_parser.parse_message("신한 03/05 입금 200,000 홍길동", date(2026, 3, 20))
        item = ledger.propose(dep, set())
        self.assertEqual((item["status"], item["room"]), ("diff", "101"))

    def test_room_number_in_depositor(self):
        self.load_sheet()
        dep = bank_parser.parse_message("신한 03/05 입금 250,000 101호", date(2026, 3, 20))
        self.assertEqual(ledger.propose(dep, set())["room"], "101")


class TestApi(Rent4Case):
    def setUp(self):
        super().setUp()
        self.client = TestClient(create_gateway(db_path=":memory:", only=["rent4"]))
        token = self.client.post("/auth/login", json={"username": "chic", "password": "1212"}).json()["token"]
        self.auth = {"Authorization": f"Bearer {token}"}

    def test_lessor_requires_login(self):
        self.assertEqual(self.client.get("/api/rent4/lessor/board").status_code, 401)

    def test_import_then_bank_flow_then_tenant_view(self):
        res = self.client.post("/api/rent4/lessor/import/commit", json={"text": SHEET}, headers=self.auth).json()
        self.assertEqual(res["payments"], 4)
        prev = self.client.post("/api/rent4/lessor/bank/preview", headers=self.auth,
                                json={"text": "신한 03/05 14:00 입금 150,000 이영희"}).json()
        self.assertEqual(prev["items"][0]["months"], ["2026-02"])
        done = self.client.post("/api/rent4/lessor/bank/commit", json={"items": prev["items"]}, headers=self.auth).json()
        self.assertEqual(done["saved"], 1)

        bad = self.client.post("/api/rent4/tenant/lookup", json={"room": "103", "phone4": "0000"})
        self.assertEqual(bad.status_code, 404)
        view = self.client.post("/api/rent4/tenant/lookup", json={"room": "103호", "phone4": "6666"}).json()
        by_ym = {m["ym"]: m["status"] for m in view["months"]}
        self.assertEqual(by_ym, {"2026-01": "paid", "2026-02": "paid", "2026-03": "due"})
        self.assertNotIn("phone", view["room"])

    def test_tenant_sees_only_own_tenancy(self):
        self.client.post("/api/rent4/lessor/import/commit", json={"text": SHEET}, headers=self.auth)
        view = self.client.post("/api/rent4/tenant/lookup", json={"room": "102", "phone4": "4444"}).json()
        self.assertEqual([m["ym"] for m in view["months"]], ["2026-03", "2026-02"])   # 1월(이전 임차인) 숨김

    def test_board_and_delete_bank_payment(self):
        self.client.post("/api/rent4/lessor/import/commit", json={"text": SHEET}, headers=self.auth)
        prev = self.client.post("/api/rent4/lessor/bank/preview", headers=self.auth,
                                json={"text": "신한 03/05 입금 250,000 홍길동"}).json()
        pid = self.client.post("/api/rent4/lessor/bank/commit", json={"items": prev["items"]},
                               headers=self.auth).json()["results"][0]["payment_ids"][0]
        board = self.client.get("/api/rent4/lessor/board?year=2026", headers=self.auth).json()
        row = next(r for r in board["rooms"] if r["room"]["room"] == "101")
        self.assertEqual(row["months"][2]["status"], "paid")
        self.assertEqual(self.client.delete(f"/api/rent4/lessor/payments/{pid}", headers=self.auth).status_code, 200)
        self.assertEqual(len(Payment.find(room="101", source="bank")), 0)


if __name__ == "__main__":
    unittest.main()
