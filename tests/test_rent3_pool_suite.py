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
            "/rent3/",
            "/rent3/index.html",
            "/rent3/openrent.css",
            "/rent3/app.js",
            "/rent3/session-bar.js",
            "/rent3/manual.html",
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

        # Step 1 상호 신분증 본인 확인 체크 & 등기부등본 확인 잠금 + Step 2 옵션 점검
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step1", json={"role": "lessor", "checked_counterparty_id": True})
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
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

    def test_05_broker_paper_contract_photo_onboarding(self):
        """5. 부동산중개사 개입 지면계약서 사진 첨부, OCR 텍스트 추출, Data Pool 5종 동기화 및 사진 조회 검증."""
        # [A] 스마트폰 OCR 문구 자동 파싱 검증
        ocr_sample = (
            "주택임대차표준계약서 선호타워 302호 보증금 1,000만원 계약금 50만원 월세 48만원 관리비 4만원 후불 "
            "입주일 2026-10-20 임차인 강중개 010-3232-4545 행복공인중개사사무소 대표 이공인 02-333-4444"
        )
        r_parse = self.client.post("/api/rent3_lessor/contracts/parse-broker-text", json={"text": ocr_sample})
        self.assertEqual(r_parse.status_code, 200)
        parsed = r_parse.json()["parsed"]
        self.assertEqual(parsed["room_no"], "302")
        self.assertEqual(parsed["deposit"], 10_000_000)
        self.assertEqual(parsed["monthly_rent"], 480_000)
        self.assertEqual(parsed["maintenance_fee"], 40_000)
        self.assertEqual(parsed["contract_deposit"], 500_000)
        self.assertEqual(parsed["pay_timing"], "후불")
        self.assertEqual(parsed["move_in_date"], "2026-10-20")
        self.assertEqual(parsed["tenant_name"], "강중개")
        self.assertEqual(parsed["tenant_phone"], "010-3232-4545")
        self.assertEqual(parsed["broker_agency_name"], "행복공인중개사사무소")
        self.assertEqual(parsed["broker_agent_name"], "이공인")

        # [B] 사진 미첨부 시 DomainError(400) 차단 검증
        r_no_photo = self.client.post("/api/rent3_lessor/contracts/broker-photo", json={
            "branch": "sunho",
            "room_no": "302",
            "photo_data_url": "",
            "tenant_name": "강중개",
            "tenant_phone": "010-3232-4545",
        })
        self.assertEqual(r_no_photo.status_code, 400)

        # [C] 유효 1x1 PNG Data URL 첨부 + 중개사 전월세 미신고(rtms_filed_by_broker=False)로 원장 일괄 적용
        sample_png_b64 = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
        )
        r_reg = self.client.post("/api/rent3_lessor/contracts/broker-photo", json={
            "branch": "sunho",
            "room_no": "302",
            "photo_data_url": sample_png_b64,
            "photo_filename": "sunho_302_broker_contract.png",
            "tenant_name": parsed["tenant_name"],
            "tenant_phone": parsed["tenant_phone"],
            "tenant_car_number": "77가 8899",
            "lease_type": "월세",
            "pay_timing": "후불",
            "deposit": parsed["deposit"],
            "monthly_rent": parsed["monthly_rent"],
            "maintenance_fee": parsed["maintenance_fee"],
            "contract_deposit": parsed["contract_deposit"],
            "deposit_received": True,
            "contract_date": "2026-10-18",
            "move_in_date": parsed["move_in_date"],
            "lease_term_months": 12,
            "special_terms": "반려동물 금지, 퇴실 청소비 7만원",
            "broker_agency_name": parsed["broker_agency_name"],
            "broker_agent_name": parsed["broker_agent_name"],
            "broker_phone": "02-333-4444",
            "rtms_filed_by_broker": False,
        })
        self.assertEqual(r_reg.status_code, 200)
        reg_body = r_reg.json()
        self.assertEqual(reg_body["level"], "warning")  # RTMS 미신고 주의 안내 배너
        ct = reg_body["contract"]
        ct_id = ct["id"]
        self.assertEqual(ct["contract_channel"], "BROKER_PAPER")
        self.assertTrue(ct["contract_photo_file"].startswith("uploads/contracts/"))
        self.assertEqual(ct["broker_info"]["agency_name"], "행복공인중개사사무소")

        # [D] 디스크 물리 이미지 파일 존재 & 임대인/임차인 양쪽 앱에서 계약서 원본 이미지 서빙 검증
        abs_img = os.path.join(self.temp_pool, ct["contract_photo_file"])
        self.assertTrue(os.path.isfile(abs_img), "계약서 사진 파일이 디스크 uploads/contracts/에 저장되지 않았습니다.")

        r_img_lessor = self.client.get(f"/api/rent3_lessor/contracts/{ct_id}/photo")
        self.assertEqual(r_img_lessor.status_code, 200)
        self.assertEqual(r_img_lessor.headers["content-type"], "image/png")
        self.assertGreater(len(r_img_lessor.content), 20)

        r_img_tenant = self.client.get(f"/api/rent3_tenant/contracts/{ct_id}/photo")
        self.assertEqual(r_img_tenant.status_code, 200)
        self.assertEqual(r_img_tenant.content, r_img_lessor.content)

        # [E] 공동 JSON Data Pool 5종 실시간 동기화 및 후불 입주월 가짜 체납 0원 검증
        listings_sunho = self.client.get("/api/rent3_tenant/listings?branch=sunho").json()["listings"]
        self.assertFalse(any(l["room_no"] == "302" for l in listings_sunho), "중개 계약 체결 호실이 공실 목록에서 닫히지 않았습니다.")

        dash_sunho = self.client.get("/api/rent3_lessor/dashboard?branch=sunho&up_to_ym=2026-10").json()
        room302 = next(r for r in dash_sunho["rooms"] if r["room_no"] == "302")
        self.assertTrue(room302["is_occupied"])
        self.assertEqual(room302["ledger"]["total_unpaid"], 0)
        self.assertEqual(room302["ledger"]["months"][0]["status"], "EXEMPT_POSTPAID")

        # 임차인 마이홈에서 첨부 계약서 경로 및 차량 등록 동기화 확인
        tenant_id = reg_body["tenant"]["id"]
        myhome_302 = self.client.get(f"/api/rent3_tenant/myhome/{tenant_id}").json()
        self.assertEqual(myhome_302["contract"]["contract_photo_file"], ct["contract_photo_file"])
        self.assertEqual(myhome_302["tenant"]["car_number"], "77가 8899")

        # 관리인 앱 RTMS 신고 관제에 PENDING 상태로 노출되는지 검증
        admin_ov = self.client.get("/api/rent3_admin/overview").json()
        rtms_302 = next(x for x in admin_ov["rtms_reports"] if x["id_contract"] == ct_id)
        self.assertEqual(rtms_302["status"], "PENDING")

    def test_06_expiry_6_to_2_months_moveout_check_and_renewal_preview(self):
        """6. 만기 6~2개월 전 전출계획 확인, 사전공실 연동 및 전출의사 없을 때 연장 재계약 예고(5% 상한·공동계약방) 검증."""
        # [A] 기준일(2026-10-29) 대비 만기일(2027-03-01, D-123)인 201호가 6~2개월 통지구간(NOTICE_WINDOW)으로 자동 탐지되는지 검증
        dash = self.client.get("/api/rent3_lessor/dashboard?branch=main").json()
        notice_rooms = dash["expiry_notice_rooms"]
        self.assertTrue(any(r["room_no"] == "201" for r in notice_rooms))
        r201 = next(r for r in dash["rooms"] if r["room_no"] == "201")
        self.assertTrue(r201["expiry_window"]["in_6_to_2_months_window"])
        self.assertEqual(r201["expiry_window"]["window_state"], "NOTICE_WINDOW")

        # [B] 임대인이 201호에 전출계획 확인 요청 발송 -> AWAITING_TENANT 및 채팅 발송 확인
        r_req = self.client.post("/api/rent3_lessor/expiry/request-check", json={
            "branch": "main",
            "room_no": "201",
        })
        self.assertEqual(r_req.status_code, 200)
        self.assertEqual(r_req.json()["check"]["status"], "AWAITING_TENANT")

        # [C] 임차인(tenant_201)이 우선 '만기 전출(MOVE_OUT)'로 회신 시 -> 공실 쇼케이스에 사전공개 매물로 자동 노출되는지 확인
        r_move = self.client.post("/api/rent3_tenant/myhome/tenant_201/expiry-plan", json={
            "tenant_plan": "MOVE_OUT",
            "planned_move_out_date": "2027-03-01",
            "tenant_memo": "타 지역 발령 가능성 검토 중",
            "prelist_vacancy": True,
        })
        self.assertEqual(r_move.status_code, 200)
        self.assertEqual(r_move.json()["check"]["status"], "MOVEOUT_PLANNED")

        listings_after_move = self.client.get("/api/rent3_tenant/listings?branch=main").json()["listings"]
        pre_item = next((l for l in listings_after_move if l["room_no"] == "201"), None)
        self.assertIsNotNone(pre_item, "만기 전출 예정 호실이 사전공개 매물로 노출되지 않았습니다.")
        self.assertTrue(pre_item["is_pre_moveout_listing"])

        # 전출 예정 상태에서 연장 재계약 예고 발송 시 DomainError(400) 차단 검증
        r_blocked_move = self.client.post("/api/rent3_lessor/expiry/preview-renewal", json={
            "branch": "main",
            "room_no": "201",
            "proposed_deposit": 5_000_000,
            "proposed_monthly_rent": 500_000,
        })
        self.assertEqual(r_blocked_move.status_code, 400)

        # [D] 임차인이 '전출의사 없음(STAY)'으로 회신 변경 -> STAY_CONFIRMED 및 사전공실 노출 즉시 해제 검증
        r_stay = self.client.post("/api/rent3_tenant/myhome/tenant_201/expiry-plan", json={
            "tenant_plan": "STAY",
            "tenant_memo": "발령 취소되어 전출의사 없으며 1년 연장 희망합니다.",
            "prelist_vacancy": False,
        })
        self.assertEqual(r_stay.status_code, 200)
        self.assertEqual(r_stay.json()["check"]["status"], "STAY_CONFIRMED")
        listings_after_stay = self.client.get("/api/rent3_tenant/listings?branch=main").json()["listings"]
        self.assertFalse(any(l["room_no"] == "201" for l in listings_after_stay), "전출의사 없음 전환 후에도 사전공실이 남아 있습니다.")

        # [E] 전출의사 없을 때 임대인이 법정 5% 상한 초과(월세 50만 -> 55만, +10%) 연장 예고 시 422(Mismatch) 차단 검증
        r_over_5pct = self.client.post("/api/rent3_lessor/expiry/preview-renewal", json={
            "branch": "main",
            "room_no": "201",
            "proposed_deposit": 5_000_000,
            "proposed_monthly_rent": 550_000,
            "proposed_maintenance_fee": 50_000,
            "proposed_term_months": 12,
        })
        self.assertEqual(r_over_5pct.status_code, 422)
        self.assertEqual(r_over_5pct.json()["level"], "diff")

        # [F] 적법한 5% 이내(+4.0%, 월세 50만 -> 52만) 연장 재계약 예고 발송 + 공동 재계약방 동시 개설 검증
        r_preview = self.client.post("/api/rent3_lessor/expiry/preview-renewal", json={
            "branch": "main",
            "room_no": "201",
            "proposed_deposit": 5_000_000,
            "proposed_monthly_rent": 520_000,
            "proposed_maintenance_fee": 50_000,
            "proposed_term_months": 12,
            "renewal_note": "전출의사가 없으셔서 법정 5% 이내(+4%)로 1년 연장 재계약을 예고드립니다.",
            "open_cosign_session": True,
        })
        self.assertEqual(r_preview.status_code, 200)
        chk_prev = r_preview.json()["check"]
        self.assertEqual(chk_prev["status"], "RENEWAL_PREVIEWED")
        self.assertEqual(chk_prev["renewal_type"], "ADJUSTED_WITHIN_5PCT")
        self.assertEqual(chk_prev["rent_increase_pct"], 4.0)
        self.assertTrue(chk_prev["cosign_session_id"])
        self.assertEqual(len(chk_prev["cosign_session_code"]), 6)

        # 공동 재계약방(cosign_sessions.json)에 예고 조건(월세 52만, RENEWAL 모드)이 세팅되었는지 확인
        sess_res = self.client.get(f"/api/rent3_contract/sessions/{chk_prev['cosign_session_id']}").json()["session"]
        self.assertEqual(sess_res["mode"], "RENEWAL")
        self.assertEqual(sess_res["terms"]["monthly_rent"], 520_000)

        # [G] 임차인 마이홈에서 연장 재계약 예고장 확인·동의 -> RENEWAL_ACKNOWLEDGED 동기화 검증
        myhome_201 = self.client.get("/api/rent3_tenant/myhome/tenant_201").json()
        self.assertEqual(myhome_201["expiry_check"]["status"], "RENEWAL_PREVIEWED")

        r_ack = self.client.post("/api/rent3_tenant/myhome/tenant_201/acknowledge-renewal", json={
            "tenant_memo": "예고해주신 52만원 연장 조건 확인 및 동의합니다.",
        })
        self.assertEqual(r_ack.status_code, 200)
        self.assertEqual(r_ack.json()["check"]["status"], "RENEWAL_ACKNOWLEDGED")

    def test_07_monthly_rent_due_3d_before_and_overdue_1d_after_notices(self):
        """7. 매월 납입일(=입주일) 3일 전 사전 통보, 납입일 1일 후 미납 통보, 완납 오발송 차단 및 수납 시 자동 해소 검증."""
        # [A] 2026-10-29 기준: 입주일 1일인 201호(main)의 차기 납입일(2026-11-01)이 정확히 D-3(3일 전)인지 검증
        dash = self.client.get("/api/rent3_lessor/dashboard?branch=main&as_of_date=2026-10-29").json()
        r201 = next(r for r in dash["rooms"] if r["room_no"] == "201")
        self.assertEqual(r201["billing_schedule"]["payment_day"], 1)
        self.assertEqual(r201["billing_schedule"]["upcoming_due_date"], "2026-11-01")
        self.assertEqual(r201["billing_schedule"]["days_until_due"], 3)
        self.assertTrue(r201["billing_schedule"]["is_pre_due_3d"])
        self.assertFalse(r201["billing_schedule"]["is_overdue_eligible"])  # 10월분까지 완납 상태

        # 완납 상태인 201호에 미납 통보 발송 시도 -> DomainError(400) 원천 차단 검증
        r_false_ov = self.client.post("/api/rent3_lessor/billing-notices/send", json={
            "branch": "main",
            "room_no": "201",
            "notice_type": "OVERDUE_1D",
            "as_of_date": "2026-10-29",
        })
        self.assertEqual(r_false_ov.status_code, 400)

        # [B] 2026-10-29 정기 자동통보 실행 -> main(201호) 및 sunho(301호)에 D-3 PRE_DUE_3D 발송 검증
        r_batch_d3 = self.client.post("/api/rent3_lessor/billing-notices/auto-dispatch", json={
            "branch": "main",
            "as_of_date": "2026-10-29",
            "include_catchup_overdue": False,
        })
        self.assertEqual(r_batch_d3.status_code, 200)
        b_d3 = r_batch_d3.json()
        self.assertEqual(len(b_d3["sent_pre_due"]), 1)
        self.assertEqual(b_d3["sent_pre_due"][0]["room_no"], "201")
        self.assertEqual(len(b_d3["sent_overdue"]), 0)

        r_batch_sunho_d3 = self.client.post("/api/rent3_lessor/billing-notices/auto-dispatch", json={
            "branch": "sunho",
            "as_of_date": "2026-10-29",
            "include_catchup_overdue": False,
        }).json()
        self.assertEqual(len(r_batch_sunho_d3["sent_pre_due"]), 1)
        self.assertEqual(r_batch_sunho_d3["sent_pre_due"][0]["room_no"], "301")

        # 동일 기준일 재실행 시 중복 스킵 & 단건 중복 발송 시 409 Conflict 차단 검증
        r_batch_dup = self.client.post("/api/rent3_lessor/billing-notices/auto-dispatch", json={
            "branch": "main",
            "as_of_date": "2026-10-29",
            "include_catchup_overdue": False,
        }).json()
        self.assertEqual(r_batch_dup["total_sent"], 0)
        self.assertEqual(r_batch_dup["skipped_duplicates"], 1)

        r_dup_single = self.client.post("/api/rent3_lessor/billing-notices/send", json={
            "branch": "main",
            "room_no": "201",
            "notice_type": "PRE_DUE_3D",
            "as_of_date": "2026-10-29",
        })
        self.assertEqual(r_dup_single.status_code, 409)

        # [C] 임차인(tenant_201) 마이홈에서 도착한 D-3 사전 통보 확인 -> 11/01 정상 납부 완료
        bn_201_id = b_d3["sent_pre_due"][0]["id"]
        r_ack_bn = self.client.post(f"/api/rent3_tenant/myhome/tenant_201/billing-notices/{bn_201_id}/acknowledge")
        self.assertEqual(r_ack_bn.status_code, 200)
        self.assertEqual(r_ack_bn.json()["notice"]["status"], "ACKNOWLEDGED")

        r_pay_201 = self.client.post("/api/rent3_lessor/payments/collect", json={
            "branch": "main",
            "room_no": "201",
            "amount": 550_000,
            "paid_date": "2026-11-01",
            "up_to_ym": "2026-11",
        })
        self.assertEqual(r_pay_201.status_code, 200)

        # 납부 즉시 201호의 11월분 사전통보가 RESOLVED_PAID로 자동 해소되었는지 검증
        mh_201_after = self.client.get("/api/rent3_tenant/myhome/tenant_201").json()
        bn_201_after = next(x for x in mh_201_after["billing_notices"] if x["id"] == bn_201_id)
        self.assertEqual(bn_201_after["status"], "RESOLVED_PAID")

        # [D] 납입일 1일 후(2026-11-02, D+1) 정기 자동통보 실행:
        #   - 완납한 main(201호)에는 미납통보 0건 스킵
        #   - 미납 상태인 sunho(301호, 입주일 2026-09-01 -> 매월 1일 납입)에는 OVERDUE_1D 발송!
        r_main_d1 = self.client.post("/api/rent3_lessor/billing-notices/auto-dispatch", json={
            "branch": "main",
            "as_of_date": "2026-11-02",
            "include_catchup_overdue": False,
        }).json()
        self.assertEqual(len(r_main_d1["sent_overdue"]), 0)

        r_sunho_d1 = self.client.post("/api/rent3_lessor/billing-notices/auto-dispatch", json={
            "branch": "sunho",
            "as_of_date": "2026-11-02",
            "include_catchup_overdue": False,
        })
        self.assertEqual(r_sunho_d1.status_code, 200)
        b_d1 = r_sunho_d1.json()
        self.assertEqual(len(b_d1["sent_overdue"]), 1)
        ov_301 = b_d1["sent_overdue"][0]
        self.assertEqual(ov_301["room_no"], "301")
        self.assertEqual(ov_301["notice_type"], "OVERDUE_1D")
        self.assertEqual(ov_301["due_date"], "2026-11-01")
        self.assertEqual(ov_301["days_offset"], 1)
        self.assertEqual(ov_301["amount_due"], 500_000)  # 11월분 미납 500,000원

        # [E] 301호가 미납액(500,000원) 납부 시 미납 통보가 즉시 RESOLVED_PAID로 자동 해소되는지 검증
        r_pay_301 = self.client.post("/api/rent3_lessor/payments/collect", json={
            "branch": "sunho",
            "room_no": "301",
            "amount": 500_000,
            "paid_date": "2026-11-03",
            "up_to_ym": "2026-11",
        })
        self.assertEqual(r_pay_301.status_code, 200)

        mh_301_after = self.client.get("/api/rent3_tenant/myhome/tenant_sunho_301").json()
        ov_301_after = next(x for x in mh_301_after["billing_notices"] if x["id"] == ov_301["id"])
        self.assertEqual(ov_301_after["status"], "RESOLVED_PAID")

    def test_08_openrent_portal_landing_and_role_based_login_routing(self):
        """8. OpenRent 직거래 포털(index.html) 실시간 매물 연동 및 로그인 후 역할별 전용 페이지 자동 분기 검증."""
        # [A] 포털 랜딩 API 조회 (공실 매물·통계·데모계정 반환 검증)
        r_land = self.client.get("/api/rent3/pool/landing")
        self.assertEqual(r_land.status_code, 200)
        land = r_land.json()
        self.assertTrue(land["success"])
        self.assertGreaterEqual(land["stats"]["total_buildings"], 3)
        self.assertGreater(len(land["listings"]), 0)

        # [B] 총괄 관리인(admin / 0202) 로그인 -> 관리인 전용 앱(/rent3_admin/) 및 Data Pool 허브 분기 검증
        r_adm = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "admin",
            "password": "0202",
        })
        self.assertEqual(r_adm.status_code, 200)
        self.assertEqual(r_adm.json()["role"], "admin")
        self.assertEqual(r_adm.json()["redirect_url"], "/rent3_admin/")
        self.assertTrue(bool(r_adm.json()["token"]))

        r_adm_pool = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "admin",
            "password": "1212",
            "target_hint": "pool",
        })
        self.assertEqual(r_adm_pool.json()["redirect_url"], "/rent3/pool.html")

        # [C] 지점별 임대인 로그인 -> 본인 건물 지점 콘솔 및 매물등록 탭 분기 검증
        r_lsr_sunho = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "sunho",
            "password": "1212",
        })
        self.assertEqual(r_lsr_sunho.status_code, 200)
        self.assertEqual(r_lsr_sunho.json()["role"], "lessor")
        self.assertEqual(r_lsr_sunho.json()["redirect_url"], "/rent3_lessor/?branch=sunho")

        r_lsr_add = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "chic",
            "password": "1212",
            "target_hint": "add_listing",
        })
        self.assertEqual(r_lsr_add.json()["redirect_url"], "/rent3_lessor/?branch=main&tab=rooms")

        # [D] 입주 임차인(휴대폰 번호 + 뒷 4자리 비밀번호) 로그인 -> 내 호실 마이홈 자동 분기 검증
        r_tnt = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "010-8888-2010",
            "password": "2010",
        })
        self.assertEqual(r_tnt.status_code, 200)
        self.assertEqual(r_tnt.json()["role"], "tenant")
        self.assertEqual(r_tnt.json()["redirect_url"], "/rent3_tenant/?tenant=tenant_201")

        # 오입력 비밀번호 401 차단 검증
        r_bad = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "admin",
            "password": "wrong_password",
        })
        self.assertEqual(r_bad.status_code, 401)

        # [E] 예비임차인 간편 가입 -> 공실 탐색 페이지(/rent3_tenant/?view=listings) 자동 분기 검증
        r_reg = self.client.post("/api/rent3/pool/auth/register", json={
            "role": "prospect",
            "name": "오픈렌트예비",
            "phone": "010-9191-8282",
            "password": "8282",
        })
        self.assertEqual(r_reg.status_code, 200)
        self.assertEqual(r_reg.json()["role"], "prospect")
        self.assertEqual(r_reg.json()["redirect_url"], "/rent3_tenant/?view=listings")

        # [F] 본인인증 공동계약 진입(target_hint="contract") 및 Rent Now 매물 호출 분기 검증
        r_lsr_contract = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "chic",
            "password": "1212",
            "target_hint": "contract",
        })
        self.assertEqual(r_lsr_contract.status_code, 200)
        self.assertTrue(r_lsr_contract.json()["user"]["login_auth_verified"])
        self.assertEqual(r_lsr_contract.json()["redirect_url"], "/rent3_contract/?role=lessor&branch=main")

        r_tnt_contract = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "010-9191-8282",
            "password": "8282",
            "target_hint": "contract:sinbi:202",
        })
        self.assertEqual(r_tnt_contract.status_code, 200)
        self.assertTrue(r_tnt_contract.json()["user"]["login_auth_verified"])
        self.assertIn("call_branch=sinbi&call_room=202", r_tnt_contract.json()["redirect_url"])

        sample_listing = land["listings"][0]
        r_rn = self.client.post("/api/rent3/pool/cosign-open", json={
            "caller_role": "tenant",
            "branch": sample_listing["branch"],
            "room_no": sample_listing["room_no"],
            "tenant_name": "오픈렌트예비",
            "tenant_phone": "010-9191-8282",
        })
        self.assertEqual(r_rn.status_code, 200)
        rn_data = r_rn.json()
        self.assertEqual(rn_data["call_status"], "CALLING")
        self.assertTrue(rn_data["redirect_url"].startswith("/rent3_contract/?role=tenant&session="))

        # [G] 포털 1:1 방문문의 -> ops.json 실시간 기록 확인
        r_inq = self.client.post("/api/rent3/pool/inquiries", json={
            "branch": sample_listing["branch"],
            "room_no": sample_listing["room_no"],
            "sender_name": "오픈렌트예비",
            "visit_date": "토요일 15:00",
            "message": "OpenRent 포털에서 실물 투어 신청합니다.",
        })
        self.assertEqual(r_inq.status_code, 200)

    def test_09_cosign_call_respond_mutual_id_and_registry_before_sign(self):
        """9. 양측 본인인증 로그인 -> 매물 선택 상대 호출·응답 연결 -> 상호 신분증 체크 & 등기부등본 확인 후 서명 검증."""
        # [A] 임대인(sinbi) 및 신규 임차인 각각 본인인증 로그인/가입 확인
        r_lessor_login = self.client.post("/api/rent3/pool/auth/login", json={
            "identifier": "sinbi",
            "password": "1212",
            "target_hint": "contract",
        })
        self.assertEqual(r_lessor_login.status_code, 200)
        self.assertTrue(r_lessor_login.json()["user"]["login_auth_verified"])

        r_tenant_reg = self.client.post("/api/rent3/pool/auth/register", json={
            "role": "prospect",
            "name": "최호출",
            "phone": "010-7788-6655",
            "password": "6655",
            "target_hint": "contract",
        })
        self.assertEqual(r_tenant_reg.status_code, 200)
        self.assertTrue(r_tenant_reg.json()["user"]["login_auth_verified"])
        tenant_id = r_tenant_reg.json()["user"]["tenant_id"]

        # [B] 임차인이 먼저 공동계약 화면에서 신비주택(sinbi) 202호 선택 -> 임대인 호출(CALLING)
        r_call = self.client.post("/api/rent3_contract/sessions/call", json={
            "caller_role": "tenant",
            "branch": "sinbi",
            "room_no": "202",
            "tenant_name": "최호출",
            "tenant_phone": "010-7788-6655",
            "id_tenant": tenant_id,
            "mode": "NEW",
        })
        self.assertEqual(r_call.status_code, 200)
        sess = r_call.json()["session"]
        sid = sess["id"]
        self.assertEqual(sess["call_status"], "CALLING")
        self.assertEqual(sess["initiated_by_role"], "tenant")
        self.assertTrue(sess["tenant_login_verified"])
        self.assertFalse(sess["lessor_login_verified"])

        # 임대인(sinbi)에게 도착한 호출 수신 목록에 노출되는지 검증
        r_inc = self.client.get("/api/rent3_contract/incoming-calls?role=lessor&branch=sinbi")
        self.assertEqual(r_inc.status_code, 200)
        self.assertTrue(any(c["id"] == sid for c in r_inc.json()["calls"]))

        # [C] 안전장치 1: 상대방 응답(CONNECTED) 전 단계 진행 시 400 차단 & 본인 자가응답 400 차단
        r_premature = self.client.post(f"/api/rent3_contract/sessions/{sid}/step1", json={"role": "tenant"})
        self.assertEqual(r_premature.status_code, 400)

        r_self_resp = self.client.post(f"/api/rent3_contract/sessions/{sid}/respond", json={
            "responder_role": "tenant",
        })
        self.assertEqual(r_self_resp.status_code, 400)

        # [D] 임대인(sinbi)이 호출에 응답 -> CONNECTED 전환 및 양측 로그인 본인인증 확정
        r_resp = self.client.post(f"/api/rent3_contract/sessions/{sid}/respond", json={
            "responder_role": "lessor",
        })
        self.assertEqual(r_resp.status_code, 200)
        conn = r_resp.json()["session"]
        self.assertEqual(conn["call_status"], "CONNECTED")
        self.assertTrue(conn["lessor_login_verified"])
        self.assertTrue(conn["tenant_login_verified"])

        # [E] 안전장치 2: 조건 합의(Step 3)를 먼저 마쳐도, 서로 신분증 확인 체크 또는 등기부 확인이 누락되면 서명 차단(400)!
        cur_rev = conn["revision"]
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": cur_rev})
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "tenant", "expected_revision": cur_rev})

        # 신분증 확인 전 서명 시도 -> 400 차단
        r_no_id = self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={
            "role": "lessor",
            "signature": "신비임대인(서명)",
            "expected_revision": cur_rev,
        })
        self.assertEqual(r_no_id.status_code, 400)

        # 임대인만 임차인 신분증 확인 체크하고 임차인은 아직 임대인 신분증을 체크하지 않은 경우 -> 여전히 400 차단!
        self.client.post(f"/api/rent3_contract/sessions/{sid}/verify-id", json={"role": "lessor", "checked": True})
        r_one_side_id = self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={
            "role": "lessor",
            "signature": "신비임대인(서명)",
            "expected_revision": cur_rev,
        })
        self.assertEqual(r_one_side_id.status_code, 400)

        # 임차인도 임대인 신분증 확인 체크 완료, 하지만 아직 등기부등본(step1_tenant_locked) 미확인 상태 -> 여전히 400 차단!
        self.client.post(f"/api/rent3_contract/sessions/{sid}/verify-id", json={"role": "tenant", "checked": True})
        r_no_reg = self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={
            "role": "tenant",
            "signature": "최호출(서명)",
            "expected_revision": cur_rev,
        })
        self.assertEqual(r_no_reg.status_code, 400)

        # [F] 등기부등본 확인 잠금 + 옵션 확인 완료 -> 이제 비로소 양측 서명 통과 및 SHA-256 무결성 해시 봉인 성공!
        r_reg_ok = self.client.post(f"/api/rent3_contract/sessions/{sid}/step1", json={"role": "tenant"})
        self.assertEqual(r_reg_ok.status_code, 200)
        self.assertTrue(r_reg_ok.json()["session"]["step1_tenant_locked"])

        self.client.post(f"/api/rent3_contract/sessions/{sid}/step2", json={"role": "tenant"})

        r_sig1 = self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={
            "role": "lessor",
            "signature": "신비임대인(전자서명)",
            "expected_revision": cur_rev,
        })
        self.assertEqual(r_sig1.status_code, 200)

        r_sig2 = self.client.post(f"/api/rent3_contract/sessions/{sid}/step4/sign", json={
            "role": "tenant",
            "signature": "최호출(전자서명)",
            "expected_revision": cur_rev,
        })
        self.assertEqual(r_sig2.status_code, 200)
        self.assertTrue(bool(r_sig2.json()["session"]["integrity_hash"]))

        # [G] 최종 계약 체결 봉인 -> 생성된 계약서 스냅샷에 상호 신분증 확인 증적이 영구 박제되었는지 검증
        r_seal = self.client.post(f"/api/rent3_contract/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER"})
        self.assertEqual(r_seal.status_code, 200)
        snap = r_seal.json()["contract"]["registry_snapshot"]
        self.assertTrue(snap["lessor_login_verified"])
        self.assertTrue(snap["tenant_login_verified"])
        self.assertTrue(snap["lessor_checked_tenant_id"])
        self.assertTrue(snap["tenant_checked_lessor_id"])


if __name__ == "__main__":
    unittest.main()
