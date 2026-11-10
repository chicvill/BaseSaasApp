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
from apps.rent_dist._rent3_core import POOL_COLLECTIONS, Contract, Pay, Room, ensure_pool_seeded, pool, use_pool_dir

RENT3_SPLIT_APPS = ["rent3", "rent3_admin", "rent3_lessor", "rent3_tenant", "rent3_contract"]


class TestRent3JsonDataPoolSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["RENT3_TODAY"] = "2026-10-29"
        cls.temp_pool = tempfile.mkdtemp(prefix="rent3_pool_test_")
        use_pool_dir(cls.temp_pool)
        ensure_pool_seeded(force=True)
        cls.app = create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:")
        cls.client = TestClient(cls.app)
        # 기본은 본사 관리자 토큰(모든 API 허용). 접근 통제를 시험하는 테스트는 헤더를 바꿔 호출한다.
        r = cls.client.post("/api/rent3/pool/auth/login", json={"login_mode": "account", "identifier": "admin", "password": "1212"})
        cls.admin_token = r.json()["token"]
        cls.client.headers["Authorization"] = f"Bearer {cls.admin_token}"

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

    def _complete_contract(self, lessor_h, tenant_h, tid, room_no, name, phone):
        """호실에 대해 호출 → 5단계 → 체결까지 진행하고 세션 id 를 돌려준다 (기본 클라이언트 = 본사 관리자 권한으로 진행)."""
        c, base = self.client, "/api/rent3_contract"
        sid = c.post(f"{base}/sessions/call", json={"caller_role": "lessor", "branch": "main", "room_no": room_no, "mode": "NEW",
                                                     "tenant_name": name, "tenant_phone": phone, "id_tenant": tid}).json()["session"]["id"]
        c.post(f"{base}/sessions/{sid}/respond", json={"responder_role": "tenant", "responder_name": name, "responder_phone": phone, "id_tenant": tid})
        c.post(f"{base}/sessions/{sid}/step1", json={"role": "lessor", "checked_counterparty_id": True,
                                                     "registry_updates": {"owner_name": "김종심", "mortgage_amount": 0}})
        self._tenant_direct_registry(sid)
        c.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
        c.post(f"{base}/sessions/{sid}/step2", json={"role": "tenant"})
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        for role in ("lessor", "tenant"):
            c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
        for role in ("lessor", "tenant"):
            c.post(f"{base}/sessions/{sid}/step4/sign", json={"role": role, "signature": f"{role}서명", "expected_revision": rev})
        self._tenant_recheck_ok(sid)
        r = c.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
        assert r.status_code == 200, r.text
        return sid

    def _tenant_direct_registry(self, sid):
        """임차인이 인터넷등기소에서 직접 열람한 결과를 임대인 제시값과 같게 입력 (1단계 필수)."""
        sess = self.client.get(f"/api/rent3_contract/sessions/{sid}").json()["session"]
        rd = sess.get("registry_data") or {}
        return self.client.post(f"/api/rent3_contract/sessions/{sid}/tenant-registry", json={"registry": {
            "viewed_directly": True, "viewed_at": "2026-11-08T10:00",
            "owner_name": rd.get("owner_name") or "김임대", "mortgage_amount": int(rd.get("mortgage_amount") or 0),
            "seizure_or_trust": "없음", "guarantee_status": "가입 예정",
        }})

    def _tenant_recheck_ok(self, sid):
        return self.client.post(f"/api/rent3_contract/sessions/{sid}/recheck", json={"changed": False})

    def _admin_headers(self):
        """본사 관리자 토큰 (가맹 심사·정산 API 는 토큰이 필요하다)."""
        r = self.client.post("/api/rent3/pool/auth/login", json={"login_mode": "account", "identifier": "admin", "password": "1212"})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def test_01_immutable_core_zero_changes(self):
        """1. 불변의 BaseSaasApp 코어(core/, run.py)가 단 1줄도 수정되지 않았음을 검증."""
        git_root = os.path.abspath(os.path.join(os.path.realpath(os.path.join(ROOT, "core")), ".."))
        res = subprocess.run(
            ["git", "diff", "--name-only", "core", "run.py"],
            cwd=git_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"git diff 실행 실패: {res.stderr}")
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
        r_admin = self.client.post("/api/rent3_admin/lessors", headers=self._admin_headers(), json={
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
        self._tenant_direct_registry(sid)
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

        self._tenant_recheck_ok(sid)
        r_seal = self.client.post(f"/api/rent3_contract/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
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
        self._tenant_direct_registry(sid)
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
        self._tenant_recheck_ok(sid)
        r_seal = self.client.post(f"/api/rent3_contract/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
        self.assertEqual(r_seal.status_code, 200)
        snap = r_seal.json()["contract"]["registry_snapshot"]
        self.assertTrue(snap["lessor_login_verified"])
        self.assertTrue(snap["tenant_login_verified"])
        self.assertTrue(snap["lessor_checked_tenant_id"])
        self.assertTrue(snap["tenant_checked_lessor_id"])


    def test_10_tenant_must_verify_registry_directly(self):
        """10. 임차인은 등기부를 직접 열람한 결과를 입력해야 1단계를 마칠 수 있고, 불일치는 확인 체크 없이는 막힌다."""
        base = "/api/rent3_contract"
        self.client.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "901", "is_published": True})
        s = self.client.post(f"{base}/sessions/call", json={
            "caller_role": "lessor", "branch": "main", "room_no": "901",
            "tenant_name": "직접열람", "tenant_phone": "010-5555-0001", "mode": "NEW"}).json()["session"]
        sid = s["id"]
        self.client.post(f"{base}/sessions/{sid}/respond", json={
            "responder_role": "tenant", "responder_name": "직접열람", "responder_phone": "010-5555-0001"})
        self.client.post(f"{base}/sessions/{sid}/step1", json={
            "role": "lessor", "checked_counterparty_id": True,
            "registry_updates": {"issue_number": "1101-2026-000001", "owner_name": "김임대", "mortgage_amount": 0}})

        # (a) 직접 열람 입력 없이 임차인 확인 잠금 시도 -> 400
        r = self.client.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
        self.assertEqual(r.status_code, 400)

        def reg(**kw):
            d = {"viewed_directly": True, "viewed_at": "2026-11-08T10:00", "owner_name": "김임대",
                 "mortgage_amount": 0, "seizure_or_trust": "없음", "guarantee_status": "가입 예정"}
            d.update(kw)
            return self.client.post(f"{base}/sessions/{sid}/tenant-registry", json={"registry": d})

        # (b) 직접 열람하지 않았다고 하면 거부
        self.assertEqual(reg(viewed_directly=False).status_code, 400)
        # (c) 근저당이 다르면 확인 체크 없이는 거부, 체크하면 통과 (보증금 500만 월세 = 소액 월세라 소액 보증금 위험 확인도 필요)
        self.assertEqual(reg(mortgage_amount=300000000).status_code, 400)
        self.assertEqual(reg(mortgage_amount=300000000, mismatch_ack=True).status_code, 400)
        self.assertEqual(reg(mortgage_amount=300000000, mismatch_ack=True, low_deposit_risk_ack=True).status_code, 200)
        # (e) 일치하는 정상 입력 -> 1단계 완료
        self.assertEqual(reg().status_code, 200)
        r_ok = self.client.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
        self.assertEqual(r_ok.status_code, 200)
        self.assertTrue(r_ok.json()["session"]["step1_tenant_locked"])

    def test_11_final_recheck_required_and_change_cancels(self):
        """11. 체결 전 임차인 재열람 확인이 없으면 체결 불가, 변동이 있다고 하면 계약이 중단된다."""
        base = "/api/rent3_contract"
        for room, changed in (("902", False), ("903", True)):
            self.client.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": room, "is_published": True})
            s = self.client.post(f"{base}/sessions/call", json={
                "caller_role": "lessor", "branch": "main", "room_no": room,
                "tenant_name": "재열람", "tenant_phone": "010-5555-0002", "mode": "NEW"}).json()["session"]
            sid = s["id"]
            self.client.post(f"{base}/sessions/{sid}/respond", json={
                "responder_role": "tenant", "responder_name": "재열람", "responder_phone": "010-5555-0002"})
            self.client.post(f"{base}/sessions/{sid}/step1", json={
                "role": "lessor", "checked_counterparty_id": True,
                "registry_updates": {"owner_name": "김임대", "mortgage_amount": 0}})
            self._tenant_direct_registry(sid)
            self.client.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
            for role in ("lessor", "tenant"):
                self.client.post(f"{base}/sessions/{sid}/step2", json={"role": role})
            rev = self.client.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
            for role in ("lessor", "tenant"):
                self.client.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
            for role in ("lessor", "tenant"):
                self.client.post(f"{base}/sessions/{sid}/step4/sign", json={"role": role, "signature": role, "expected_revision": rev})

            r_no = self.client.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
            self.assertEqual(r_no.status_code, 400, "재열람 확인 없이는 체결할 수 없어야 한다")

            r_rc = self.client.post(f"{base}/sessions/{sid}/recheck", json={"changed": changed})
            self.assertEqual(r_rc.status_code, 200)
            if changed:
                self.assertEqual(r_rc.json()["session"]["status"], "CANCELLED")
                r_after = self.client.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
                self.assertIn(r_after.status_code, (400, 409))  # 중단된 계약은 체결 불가
            else:
                r_ok = self.client.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
                self.assertEqual(r_ok.status_code, 200)


    def test_12_contract_verify_hash(self):
        """12. 계약서 진위 확인: 일치·변조·없는 해시·계약번호 불일치·잘못된 입력을 구분한다."""
        base = "/api/rent3_contract"
        self.client.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "904", "is_published": True})
        sid = self.client.post(f"{base}/sessions/call", json={
            "caller_role": "lessor", "branch": "main", "room_no": "904",
            "tenant_name": "진위확인", "tenant_phone": "010-5555-0004", "mode": "NEW"}).json()["session"]["id"]
        self.client.post(f"{base}/sessions/{sid}/respond", json={
            "responder_role": "tenant", "responder_name": "진위확인", "responder_phone": "010-5555-0004"})
        self.client.post(f"{base}/sessions/{sid}/step1", json={
            "role": "lessor", "checked_counterparty_id": True,
            "registry_updates": {"owner_name": "김임대", "mortgage_amount": 0}})
        self._tenant_direct_registry(sid)
        self.client.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
        for role in ("lessor", "tenant"):
            self.client.post(f"{base}/sessions/{sid}/step2", json={"role": role})
        rev = self.client.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        for role in ("lessor", "tenant"):
            self.client.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
        for role in ("lessor", "tenant"):
            self.client.post(f"{base}/sessions/{sid}/step4/sign", json={"role": role, "signature": role, "expected_revision": rev})
        self._tenant_recheck_ok(sid)
        done = self.client.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True}).json()
        ct = done["contract"]
        h, no = ct["integrity_hash"], ct["id"]
        self.assertEqual(len(h), 64)

        v = self.client.get(f"{base}/verify", params={"hash": h, "no": no}).json()
        self.assertEqual(v["result"], "INTACT")
        self.assertEqual(v["summary"]["room_no"], "904")
        self.assertNotIn("진위확인", str(v), "응답에 임차인 성명이 들어가면 안 된다")
        # 공백·줄바꿈·대문자로 옮겨 적어도 인식
        spaced = " ".join(h[i:i + 8] for i in range(0, 64, 8)).upper()
        self.assertEqual(self.client.get(f"{base}/verify", params={"hash": spaced}).json()["result"], "INTACT")
        # 없는 해시 / 계약번호 불일치 / 잘못된 입력
        self.assertEqual(self.client.get(f"{base}/verify", params={"hash": "0" * 64}).json()["result"], "NOT_FOUND")
        self.assertEqual(self.client.get(f"{base}/verify", params={"hash": h, "no": "ct_other"}).json()["result"], "MISMATCH")
        self.assertEqual(self.client.get(f"{base}/verify", params={"hash": "abc"}).json()["result"], "INVALID_INPUT")

        # 계약 원장(contracts.json)만 몰래 고치면 LEDGER_MISMATCH (해시 검사로는 걸리지 않는 변조)
        from apps.rent_dist._rent3_core import CoSignSession, Contract
        led = Contract.get(no)
        orig_rent = led.monthly_rent
        led.monthly_rent = orig_rent + 50000
        led.save("test_tamper_ledger")
        v2 = self.client.get(f"{base}/verify", params={"hash": h, "no": no}).json()
        self.assertEqual(v2["result"], "LEDGER_MISMATCH")
        self.assertIn("monthly_rent", [d["field"] for d in v2["diffs"]])
        self.assertNotIn("진위확인", str(v2), "원장 불일치 응답에도 임차인 성명이 들어가면 안 된다")
        led.monthly_rent = orig_rent
        led.save("test_restore_ledger")
        self.assertEqual(self.client.get(f"{base}/verify", params={"hash": h, "no": no}).json()["result"], "INTACT")

        # 서명 뒤 계약 데이터가 바뀌면 ALTERED
        sess = CoSignSession.get(sid)
        sess.terms = {**sess.terms, "monthly_rent": int(sess.terms.get("monthly_rent", 0)) + 100000}
        sess.save("test_tamper")
        self.assertEqual(self.client.get(f"{base}/verify", params={"hash": h, "no": no}).json()["result"], "ALTERED")


    def test_13_registry_ai_reads_and_scores_risk(self):
        """13. Gemini 등기부 읽기: 동의 필수·호출 상한·키 없음 처리, 합계와 위험 신호는 코드가 계산한다."""
        import base64, json
        from datetime import date
        from apps.rent_dist._rent3_core import registry_ai

        base = "/api/rent3_contract"
        self.client.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "905", "is_published": True, "deposit": 5000000})
        sid = self.client.post(f"{base}/sessions/call", json={
            "caller_role": "lessor", "branch": "main", "room_no": "905",
            "tenant_name": "AI읽기", "tenant_phone": "010-5555-0005", "mode": "NEW"}).json()["session"]["id"]
        self.client.post(f"{base}/sessions/{sid}/respond", json={
            "responder_role": "tenant", "responder_name": "AI읽기", "responder_phone": "010-5555-0005"})
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()
        url = f"{base}/sessions/{sid}/registry-ai"

        class FakeGemini:
            is_available = True
            reply = {}
            calls = 0

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                FakeGemini.calls += 1
                return "```json\n" + json.dumps(FakeGemini.reply, ensure_ascii=False) + "\n```"

        class NoKey:
            is_available = False

        orig = registry_ai._client
        try:
            # (a) 동의 없으면 거부, AI 호출도 없음
            registry_ai._client = lambda: FakeGemini()
            r = self.client.post(url, json={"attachment_data_url": pdf, "consent": False})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(FakeGemini.calls, 0)

            # (b) 키가 없으면 오류가 아니라 '직접 입력' 안내
            registry_ai._client = lambda: NoKey()
            r = self.client.post(url, json={"attachment_data_url": pdf, "consent": True})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["available"])
            self.assertFalse(r.json()["success"])

            # (c) 정상: 말소된 근저당은 빼고, AI 가 준 합계(999)는 쓰지 않고 목록에서 코드로 합산
            registry_ai._client = lambda: FakeGemini()
            FakeGemini.reply = {
                "document_type": "registry", "readable": True, "issue_number": "1101-2026-777777",
                "owners": [{"name": "김임대", "share": None}],
                "mortgages": [
                    {"creditor": "A은행", "amount": "1,000,000,000", "registered_date": "2024-01-02", "cancelled": False},
                    {"creditor": "B은행", "amount": 300000000, "registered_date": "2020-01-02", "cancelled": True},
                ],
                "restrictions": [], "ownership_transfers": [{"date": "2019-05-01", "cause": "매매"}],
                "mortgage_total": 999,
            }
            r = self.client.post(url, json={"attachment_data_url": pdf, "consent": True}).json()
            self.assertTrue(r["success"])
            ex = r["result"]["extracted"]
            self.assertEqual(ex["mortgage_total"], 1000000000)
            self.assertEqual(ex["mortgage_count"], 1)
            self.assertEqual(ex["seizure_or_trust"], "없음")
            self.assertIn("LTV_OK", [x["code"] for x in r["result"]["risks"]])  # (10억+500만)/18.5억 ≈ 54%

            # (d) AI 읽기 뒤 직접 열람 제출하면 'AI 보조' 로 기록된다
            self.client.post(f"{base}/sessions/{sid}/step1", json={
                "role": "lessor", "checked_counterparty_id": True,
                "registry_updates": {"owner_name": "김임대", "mortgage_amount": 1000000000}})
            rr = self.client.post(f"{base}/sessions/{sid}/tenant-registry", json={"registry": {
                "viewed_directly": True, "viewed_at": "2026-11-08T10:00", "owner_name": "김임대",
                "mortgage_amount": 1000000000, "seizure_or_trust": "없음", "guarantee_status": "가입 예정",
                "low_deposit_risk_ack": True}}).json()
            self.assertTrue(rr["session"]["tenant_registry"]["ai_assisted"])

            # (e) 호출 상한: 총 5회까지, 그 뒤는 거부
            for _ in range(3):
                self.assertEqual(self.client.post(url, json={"attachment_data_url": pdf, "consent": True}).status_code, 200)
            self.assertEqual(self.client.post(url, json={"attachment_data_url": pdf, "consent": True}).status_code, 200)
            self.assertEqual(self.client.post(url, json={"attachment_data_url": pdf, "consent": True}).status_code, 400)
        finally:
            registry_ai._client = orig

        # (f) 위험 계산은 순수 코드 — 각 신호가 올바르게 켜지는지
        def ex(**kw):
            base_ex = {"document_type": "registry", "readable": True, "owners": [{"name": "김임대", "share": ""}],
                       "owner_name": "김임대", "mortgage_total": 0, "restrictions": [], "ownership_transfers": []}
            base_ex.update(kw)
            return base_ex

        def codes(e, **kw):
            args = {"lessor_names": ["김임대 (시크빌 대표)"], "deposit": 5000000, "property_value": 1850000000,
                    "today": date(2026, 11, 8)}
            args.update(kw)
            return [x["code"] for x in registry_ai.assess_risks(e, **args)]

        self.assertIn("LTV_HIGH", codes(ex(mortgage_total=1500000000)))           # (15억+500만)/18.5억 ≈ 81%
        self.assertIn("LTV_CAUTION", codes(ex(mortgage_total=1150000000)))        # ≈ 62%
        self.assertIn("RESTRICTION", codes(ex(restrictions=[{"type": "가압류", "detail": "", "cancelled": False}])))
        self.assertNotIn("RESTRICTION", codes(ex(restrictions=[{"type": "가압류", "detail": "", "cancelled": True}])))
        self.assertIn("OWNER_MISMATCH", codes(ex(owners=[{"name": "박사기", "share": ""}], owner_name="박사기")))
        self.assertIn("MULTI_OWNER", codes(ex(owners=[{"name": "김임대", "share": "1/2"}, {"name": "이공동", "share": "1/2"}])))
        self.assertIn("RECENT_TRANSFER", codes(ex(ownership_transfers=[{"date": "2026-09-01", "cause": "매매"}])))
        self.assertNotIn("RECENT_TRANSFER", codes(ex(ownership_transfers=[{"date": "2020-09-01", "cause": "매매"}])))
        self.assertEqual(codes(ex(document_type="other")), ["NOT_REGISTRY"])
        self.assertIn("NO_FLAG", codes(ex()))
        self.assertIn("NO_VALUE", codes(ex(mortgage_total=100000000), property_value=0))


    def test_14_live_contract_blocks_other_tenants(self):
        """14. 계약이 진행 중인 호실은 포털에서 '계약 진행 중'으로 표시되고, 다른 임차인은 호출할 수 없다."""
        base = "/api/rent3_contract"
        self.client.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "906", "is_published": True, "deposit": 5000000})

        def flag():
            ls = self.client.get("/api/rent3/pool/landing").json()["listings"]
            return next(l for l in ls if l["room_no"] == "906")["contract_in_progress"]

        self.assertFalse(flag())
        call = {"caller_role": "tenant", "branch": "main", "room_no": "906", "mode": "NEW",
                "tenant_name": "먼저온사람", "tenant_phone": "010-6000-0001"}
        first = self.client.post(f"{base}/sessions/call", json=call)
        self.assertEqual(first.status_code, 200)
        sid = first.json()["session"]["id"]
        self.assertTrue(flag())

        # 다른 임차인은 거부
        other = dict(call, tenant_name="나중에온사람", tenant_phone="010-6000-0002")
        self.assertIn(self.client.post(f"{base}/sessions/call", json=other).status_code, (400, 409))
        # 당사자(같은 연락처, 형식이 달라도)는 이어서 입장
        again = self.client.post(f"{base}/sessions/call", json=dict(call, tenant_phone="01060000001"))
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["session"]["id"], sid)
        # 임대인은 자기 호실 계약에 다시 들어올 수 있다
        les = self.client.post(f"{base}/sessions/call", json=dict(call, caller_role="lessor"))
        self.assertEqual(les.status_code, 200)
        self.assertEqual(les.json()["session"]["id"], sid)


    def test_15_idle_contract_auto_released_after_24h(self):
        """15. 24시간 무응답 계약은 자동 해제되어 호실이 다시 열리고, 방금 만든 계약은 유지된다."""
        from datetime import timedelta
        from apps.rent_dist._rent3_core import cosign

        base = "/api/rent3_contract"
        self.client.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "907", "is_published": True, "deposit": 5000000})
        call = {"caller_role": "tenant", "branch": "main", "room_no": "907", "mode": "NEW",
                "tenant_name": "무응답", "tenant_phone": "010-7000-0001"}
        sid = self.client.post(f"{base}/sessions/call", json=call).json()["session"]["id"]

        def flag():
            ls = self.client.get("/api/rent3/pool/landing").json()["listings"]
            return next(l for l in ls if l["room_no"] == "907")["contract_in_progress"]

        self.assertTrue(flag())  # 방금 만든 계약은 24시간 이내 → 유지
        orig = cosign.IDLE_EXPIRE
        try:
            cosign.IDLE_EXPIRE = timedelta(seconds=-1)  # 모든 진행 계약이 24시간을 넘긴 것으로 간주
            self.assertFalse(flag())  # 자동 해제 → 버튼 다시 활성
            got = self.client.get(f"{base}/sessions/{sid}").json()["session"]
            self.assertEqual(got["status"], "CANCELLED")
            self.assertIn("24시간", got["cancel_reason"])
        finally:
            cosign.IDLE_EXPIRE = orig
        # 해제 뒤에는 다른 임차인이 새로 시작할 수 있다
        other = dict(call, tenant_name="새사람", tenant_phone="010-7000-0002")
        r = self.client.post(f"{base}/sessions/call", json=other)
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.json()["session"]["id"], sid)


    NOAUTH = {"Authorization": ""}

    def _as(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_16_franchise_approval_gates_listing_and_contract(self):
        """16. 신규 임대인은 가맹 심사 전에는 매물 공개·계약 불가, 승인 후 가능. 심사·정지는 본사 관리자만."""
        c = self.client
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "lessor", "name": "신규임대", "phone": "01099990001", "username": "newlsr",
            "password": "pw1234", "building_name": "새빌딩", "address": "인천 서해구 석남동 1", "branch": "newbld"})
        self.assertEqual(reg.status_code, 200)
        lid, lh = reg.json()["user"]["id"], self._as(reg.json()["token"])
        self.assertEqual(c.get("/api/rent3_lessor/status?branch=newbld").json()["franchise"]["status"], "PENDING_APPROVAL")

        # 심사 중: 호실은 준비할 수 있지만 공개로 저장해도 비공개, 공개 전환·계약 호출은 거부
        room = c.post("/api/rent3_lessor/rooms", headers=lh, json={"branch": "newbld", "room_no": "101", "is_published": True, "deposit": 1000000}).json()["room"]
        self.assertFalse(room["is_published"])
        self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{room['id']}/publish", headers=lh, json={"is_published": True}).status_code, 403)
        call = {"caller_role": "lessor", "branch": "newbld", "room_no": "101", "mode": "NEW",
                "tenant_name": "문의자", "tenant_phone": "010-9000-0001"}
        self.assertIn(c.post("/api/rent3_contract/sessions/call", headers=lh, json=call).status_code, (400, 403))

        # 심사는 본사 관리자만: 토큰 없음 401, 임대인 본인 토큰 403
        url = f"/api/rent3_admin/lessors/{lid}/decision"
        self.assertEqual(c.post(url, headers=self.NOAUTH, json={"decision": "approve"}).status_code, 401)
        self.assertIn(c.post(url, headers=lh, json={"decision": "approve"}).status_code, (401, 403))  # 스스로 승인 불가

        self.assertEqual(c.post(url, json={"decision": "reject"}).status_code, 400)  # 사유 필수
        self.assertEqual(c.post(url, json={"decision": "approve"}).status_code, 400)  # 신청 서류 미비 → 거부
        self.assertEqual(c.post(url, json={"decision": "approve", "force": True}).status_code, 400)  # 강제도 사유 필수
        self.assertEqual(c.post(url, json={"decision": "approve", "force": True, "note": "현장 확인 완료"}).status_code, 200)

        # 승인 후: 공개·포털 노출·계약 호출 가능
        self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{room['id']}/publish", headers=lh, json={"is_published": True}).status_code, 200)
        ls = c.get("/api/rent3/pool/landing").json()["listings"]
        self.assertTrue(any(l["branch"] == "newbld" and l["room_no"] == "101" for l in ls))
        self.assertEqual(c.post("/api/rent3_contract/sessions/call", headers=lh, json=call).status_code, 200)

        # 정지: 공개 매물이 모두 내려가고 포털에서 사라진다. 사유가 임대인에게 보인다.
        self.assertEqual(c.post(url, json={"decision": "suspend", "note": "서류 미비"}).status_code, 200)
        ls = c.get("/api/rent3/pool/landing").json()["listings"]
        self.assertFalse(any(l["branch"] == "newbld" for l in ls))
        f = c.get("/api/rent3_lessor/status?branch=newbld").json()["franchise"]
        self.assertEqual((f["status"], f["note"]), ("SUSPENDED", "서류 미비"))

    def test_17_room_limit_per_franchise(self):
        """17. 가맹점당 호실 한도를 넘으면 새 호실 등록이 거부되고, 기존 호실 수정은 가능하다."""
        c = self.client
        c.post("/api/rent3_admin/lessors", json={
            "username": "limitlsr", "name": "한도", "phone": "010-9100-0001", "branch": "limitb", "building_name": "한도빌딩"})
        lid = next(l["id"] for l in c.get("/api/rent3_admin/overview").json()["lessors"] if l["branch"] == "limitb")
        self.assertEqual(c.post(f"/api/rent3_admin/lessors/{lid}/room-limit", headers=self.NOAUTH, json={"room_limit": 2}).status_code, 401)
        self.assertEqual(c.post(f"/api/rent3_admin/lessors/{lid}/room-limit", json={"room_limit": 2}).status_code, 200)
        for no in ("101", "102"):
            self.assertEqual(c.post("/api/rent3_lessor/rooms", json={"branch": "limitb", "room_no": no}).status_code, 200)
        self.assertEqual(c.post("/api/rent3_lessor/rooms", json={"branch": "limitb", "room_no": "103"}).status_code, 400)
        self.assertEqual(c.post("/api/rent3_lessor/rooms", json={"branch": "limitb", "room_no": "101", "deposit": 7000000}).status_code, 200)

    def _login(self, ident, pw="1212"):
        r = self.client.post("/api/rent3/pool/auth/login", headers=self.NOAUTH,
                             json={"login_mode": "account", "identifier": ident, "password": pw})
        self.assertEqual(r.status_code, 200, r.text)
        return self._as(r.json()["token"])

    def test_18_access_control_by_role_and_branch(self):
        """18. 토큰 없으면 401, 다른 지점 임대인·다른 임차인은 403, 본인 지점·본인 계정은 통과."""
        c = self.client
        chic, sunho = self._login("chic"), self._login("sunho")

        # 토큰 없음
        for method, url in (("get", "/api/rent3_lessor/dashboard?branch=main"), ("post", "/api/rent3_lessor/rooms"),
                            ("get", "/api/rent3_tenant/myhome/tenant_201"), ("get", "/api/rent3_contract/sessions"),
                            ("get", "/api/rent3_tenant/tenants")):
            extra = {"json": {"room_no": "1"}} if method == "post" else {}
            r = getattr(c, method)(url, headers=self.NOAUTH, **extra)
            self.assertEqual(r.status_code, 401, url)

        # 임대인: 자기 지점만
        self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).status_code, 200)
        self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=sunho).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/rooms", headers=sunho, json={"branch": "main", "room_no": "999"}).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/rooms", headers=sunho, json={"room_no": "999"}).status_code, 403)  # 지점 생략 = main
        some_room = Room.first(branch="main")
        self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{some_room.id}/publish", headers=sunho, json={"is_published": False}).status_code, 403)

        # 임대인은 본사 관리 API 사용 불가
        self.assertIn(c.post("/api/rent3_admin/lessors/lessor_chic/decision", headers=chic, json={"decision": "approve"}).status_code, (401, 403))

        # 임차인: 본인 마이홈만, 임대인 기능·직원용 목록 불가
        ra = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "갑임차", "phone": "01066660101", "password": "Pw123456"}).json()
        rb = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "을임차", "phone": "01066660102", "password": "Pw123456"}).json()
        self.assertIn("token", ra, ra)
        self.assertIn("token", rb, rb)
        ta, tb = self._as(ra["token"]), self._as(rb["token"])
        ida, idb = ra["user"]["id"], rb["user"]["id"]
        self.assertNotEqual(c.get(f"/api/rent3_tenant/myhome/{ida}", headers=ta).status_code, 403)
        self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{idb}", headers=ta).status_code, 403)
        self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=ta).status_code, 403)
        self.assertEqual(c.get("/api/rent3_tenant/tenants", headers=ta).status_code, 403)
        t201 = tb

        # 공동계약: 임차인 토큰으로 임대인 역할 불가, 남의 계약 세션 접근 불가
        call = {"caller_role": "tenant", "branch": "main", "room_no": "908", "mode": "NEW",
                "tenant_name": "갑임차", "tenant_phone": "010-6666-0101"}
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "908", "is_published": True})
        imcha = ta
        # 본인인증 전에는 계약을 시작할 수 없다 → 인증 후 진행
        self.assertEqual(c.post("/api/rent3_contract/sessions/call", headers=ta, json=call).status_code, 403)
        base_id = {"name": "갑임차", "phone": "01066660101", "birth": "900101"}
        c.post("/api/rent3_contract/identity/start", headers=ta, json=base_id)
        self.assertEqual(c.post("/api/rent3_contract/identity/confirm", headers=ta, json={**base_id, "code": "000000"}).status_code, 200)
        r = c.post("/api/rent3_contract/sessions/call", headers=imcha, json=dict(call, caller_role="lessor"))
        self.assertEqual(r.status_code, 403)  # 임차인 토큰으로 임대인 행세 금지
        r = c.post("/api/rent3_contract/sessions/call", headers=imcha, json=call)
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["session"]["id"]
        self.assertEqual(c.get(f"/api/rent3_contract/sessions/{sid}", headers=imcha).status_code, 200)   # 당사자 임차인
        self.assertEqual(c.get(f"/api/rent3_contract/sessions/{sid}", headers=chic).status_code, 200)    # 해당 지점 임대인
        self.assertEqual(c.get(f"/api/rent3_contract/sessions/{sid}", headers=sunho).status_code, 403)   # 다른 지점 임대인
        self.assertEqual(c.get(f"/api/rent3_contract/sessions/{sid}", headers=t201).status_code, 403)    # 다른 임차인
        # 다른 임차인이 남의 번호로 계약 시작 금지
        r = c.post("/api/rent3_contract/sessions/call", headers=t201, json=call)
        self.assertEqual(r.status_code, 403)
        # 원본 Data Pool(개인정보·계정 정보 포함)은 본사 관리자만
        for u in ("/api/rent3/pool/files/users", "/api/rent3/pool/overview", "/api/rent3_admin/pool/users", "/api/rent3_admin/overview"):
            self.assertEqual(c.get(u, headers=self.NOAUTH).status_code, 401, u)
            self.assertIn(c.get(u, headers=chic).status_code, (401, 403), u)
            self.assertEqual(c.get(u).status_code, 200, u)
        # 진위 확인은 로그인 없이 공개
        self.assertNotEqual(c.get("/api/rent3_contract/verify?hash=x&no=y", headers=self.NOAUTH).status_code, 401)

    def test_19_demo_passwords_only_on_local(self):
        """19. 공통 데모 비밀번호(1212)는 서버 밖 요청에서는 거부된다. 가입 비밀번호는 흔한 값 거부."""
        import os
        c = self.client
        os.environ["RENT3_DEMO_LOGIN"] = "0"
        try:
            for ident in ("admin", "chic", "imcha"):
                r = c.post("/api/rent3/pool/auth/login", headers=self.NOAUTH,
                           json={"login_mode": "account", "identifier": ident, "password": "1212"})
                self.assertEqual(r.status_code, 401, ident)
            weak = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
                "role": "lessor", "name": "약한", "phone": "01077770001", "username": "weakpw", "password": "1212", "branch": "weakb"})
            self.assertEqual(weak.status_code, 400)
            ok = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
                "role": "lessor", "name": "튼튼", "phone": "01077770002", "username": "strongpw", "password": "Str0ngPw9", "branch": "strongb"})
            self.assertEqual(ok.status_code, 200)
            r = c.post("/api/rent3/pool/auth/login", headers=self.NOAUTH,
                       json={"login_mode": "account", "identifier": "strongpw", "password": "Str0ngPw9"})
            self.assertEqual(r.status_code, 200)
        finally:
            os.environ.pop("RENT3_DEMO_LOGIN", None)


    def test_20_ai_monthly_limit_per_franchise(self):
        """20. 가맹점별 월 AI 읽기 한도: 넘으면 Gemini 를 호출하지 않고 거부한다. 한도 변경은 본사 관리자만."""
        import base64, json
        from apps.rent_dist._rent3_core import registry_ai

        c = self.client
        c.post("/api/rent3_admin/lessors", json={
            "username": "aicap", "name": "한도AI", "phone": "010-9200-0001", "branch": "aicap", "building_name": "AI빌딩"})
        lid = next(l["id"] for l in c.get("/api/rent3_admin/overview").json()["lessors"] if l["branch"] == "aicap")
        self.assertEqual(c.post(f"/api/rent3_admin/lessors/{lid}/ai-limit", headers=self.NOAUTH, json={"ai_monthly_limit": 1}).status_code, 401)
        self.assertEqual(c.post(f"/api/rent3_admin/lessors/{lid}/ai-limit", json={"ai_monthly_limit": 1}).status_code, 200)

        c.post("/api/rent3_lessor/rooms", json={"branch": "aicap", "room_no": "101", "is_published": True})
        sid = c.post("/api/rent3_contract/sessions/call", json={
            "caller_role": "lessor", "branch": "aicap", "room_no": "101", "tenant_name": "AI한도", "tenant_phone": "010-9200-0002", "mode": "NEW"}).json()["session"]["id"]
        c.post(f"/api/rent3_contract/sessions/{sid}/respond", json={"responder_role": "tenant", "responder_name": "AI한도", "responder_phone": "010-9200-0002"})
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()
        url = f"/api/rent3_contract/sessions/{sid}/registry-ai"

        class Fake:
            is_available = True
            calls = 0

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                Fake.calls += 1
                return json.dumps({"document_type": "registry", "readable": True, "owners": [{"name": "누구", "share": None}],
                                   "mortgages": [], "restrictions": [], "ownership_transfers": []}, ensure_ascii=False)

        orig = registry_ai._client
        try:
            registry_ai._client = lambda: Fake()
            self.assertEqual(c.post(url, json={"attachment_data_url": pdf, "consent": True}).status_code, 200)
            self.assertEqual(Fake.calls, 1)
            r = c.post(url, json={"attachment_data_url": pdf, "consent": True})
            self.assertEqual(r.status_code, 400)       # 이번 달 한도 소진
            self.assertEqual(Fake.calls, 1)            # Gemini 는 더 호출되지 않았다
        finally:
            registry_ai._client = orig


    def test_21_franchise_application_identity_and_registry_match(self):
        """21. 가맹 신청: 가짜 본인인증 → 건물 주소 → 등기부 대조(동명이인·공동소유·법인·주소 불일치) → 제출 → 관리자 승인."""
        import base64, json
        from apps.rent_dist._rent3_core import registry_ai

        c = self.client
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "lessor", "name": "김종심", "phone": "01082817377", "username": "kjs", "password": "Pw123456",
            "building_name": "석남빌", "address": "인천 서해구 석남동 507-34", "branch": "seoknam"})
        lid, lh = reg.json()["user"]["id"], self._as(reg.json()["token"])
        api = "/api/rent3_lessor/franchise"
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()

        # 본인인증 없이 등기부·제출 불가
        self.assertEqual(c.post(f"{api}/registry", headers=lh, json={"branch": "seoknam", "attachment_data_url": pdf}).status_code, 400)
        self.assertEqual(c.post(f"{api}/submit", headers=lh, json={"branch": "seoknam"}).status_code, 400)

        # 가짜 본인인증: 틀린 번호 거부, 맞으면 통과
        who = {"branch": "seoknam", "name": "김종심", "phone": "01082817377", "birth": "19650312"}
        self.assertEqual(c.post(f"{api}/identity/start", headers=lh, json=who).status_code, 200)
        self.assertEqual(c.post(f"{api}/identity/confirm", headers=lh, json={**who, "code": "111111"}).status_code, 400)
        ok = c.post(f"{api}/identity/confirm", headers=lh, json={**who, "code": "000000"}).json()
        self.assertTrue(ok["application"]["identity"]["verified"])
        self.assertEqual(ok["application"]["identity"]["birth6"], "65****")  # 생년월일은 가린다

        class Fake:
            is_available = True
            reply = {}

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                return json.dumps(Fake.reply, ensure_ascii=False)

        def reg_reply(owners, addr="인천광역시 서해구 석남동 507-34"):
            return {"document_type": "registry", "readable": True, "property_address": addr, "owners": owners,
                    "mortgages": [], "restrictions": [], "ownership_transfers": []}

        def upload():
            return c.post(f"{api}/registry", headers=lh, json={"branch": "seoknam", "attachment_data_url": pdf, "consent": True})

        def code():
            return c.get(f"{api}/application?branch=seoknam", headers=lh).json()["application"]["registry"]["check"]["code"]

        import apps.rent_dist.rent3_lessor.api as lessor_api
        orig, orig_max = registry_ai._client, lessor_api.FRANCHISE_AI_MAX_CALLS
        try:
            lessor_api.FRANCHISE_AI_MAX_CALLS = 6   # 이 테스트는 6가지 경우를 시험한다
            registry_ai._client = lambda: Fake()
            # 동명이인(생년월일 다름) → 자동 통과 안 됨
            Fake.reply = reg_reply([{"name": "김종심", "share": None, "id_front": "700101"}])
            self.assertEqual(upload().status_code, 200); self.assertEqual(code(), "BIRTH_MISMATCH")
            # 공동 소유
            Fake.reply = reg_reply([{"name": "김종심", "share": "1/2", "id_front": "650312"}, {"name": "이공동", "share": "1/2", "id_front": "700202"}])
            upload(); self.assertEqual(code(), "SHARED_OWNER")
            # 법인 소유
            Fake.reply = reg_reply([{"name": "주식회사 석남", "share": None, "id_front": ""}])
            upload(); self.assertEqual(code(), "CORPORATE")
            # 본인이 아닌 소유자
            Fake.reply = reg_reply([{"name": "박사기", "share": None, "id_front": "650312"}])
            upload(); self.assertEqual(code(), "NOT_OWNER")
            # 소재지 불일치
            Fake.reply = reg_reply([{"name": "김종심", "share": None, "id_front": "650312"}], addr="서울특별시 관악구 신림동 1-1")
            upload(); self.assertEqual(code(), "ADDRESS_MISMATCH")
            # 정상: 이름·생년월일 일치, 단독 소유, 소재지 일치
            Fake.reply = reg_reply([{"name": "김종심", "share": None, "id_front": "650312"}])
            r = upload().json()
            self.assertEqual(code(), "AUTO_MATCH")
            self.assertTrue(r["application"]["registry"]["check"]["auto_pass"])
            # 신청 중 AI 호출 상한: 한도를 넘으면 거부
            self.assertEqual(upload().status_code, 400)
        finally:
            registry_ai._client, lessor_api.FRANCHISE_AI_MAX_CALLS = orig, orig_max

        # 제출 → 관리자 심사 목록에 신청 완료로 보인다 (승인 전에는 여전히 비공개·계약 불가)
        self.assertEqual(c.post(f"{api}/submit", headers=lh, json={"branch": "seoknam"}).status_code, 200)
        row = next(l for l in c.get("/api/rent3_admin/overview").json()["lessors"] if l["id"] == lid)
        self.assertTrue(row["application"]["steps"]["submitted"])
        self.assertNotIn("password", row)
        self.assertNotIn("identity", row)  # 원본 생년월일은 목록에 싣지 않는다

        # 제출된 등기부 원본은 관리자만 열람
        f = f"/api/rent3_admin/lessors/{lid}/registry-attachment"
        self.assertEqual(c.get(f, headers=self.NOAUTH).status_code, 401)
        self.assertIn(c.get(f, headers=lh).status_code, (401, 403))
        self.assertEqual(c.get(f).status_code, 200)

        # 서류가 갖춰져도 관리자의 원본 대조(발급확인번호) 기록이 없으면 승인되지 않는다
        dec = f"/api/rent3_admin/lessors/{lid}/decision"
        self.assertEqual(c.post(dec, json={"decision": "approve"}).status_code, 400)
        ver = f"/api/rent3_admin/lessors/{lid}/registry-verify"
        self.assertEqual(c.post(ver, headers=self.NOAUTH, json={"issue_number": "1101-2026-1", "note": "일치"}).status_code, 401)
        self.assertIn(c.post(ver, headers=lh, json={"issue_number": "1101-2026-1", "note": "일치"}).status_code, (401, 403))
        self.assertEqual(c.post(ver, json={"issue_number": "", "note": "일치"}).status_code, 400)       # 번호 필수
        self.assertEqual(c.post(ver, json={"issue_number": "1101-2026-1", "note": ""}).status_code, 400)  # 메모 필수
        rv = c.post(ver, json={"issue_number": "1101-2026-1", "note": "소유자·소재지·발급일시 일치"}).json()
        v = rv["application"]["registry"]["verification"]
        self.assertEqual((v["verified"], v["verified_by"]), (True, "admin"))  # 누가 확인했는지 남는다
        self.assertTrue(v["verified_at"])
        # 원본과 다르다고 기록하면 다시 승인 불가
        c.post(ver, json={"verified": False, "issue_number": "1101-2026-1", "note": "소유자 다름"})
        self.assertEqual(c.post(dec, json={"decision": "approve"}).status_code, 400)
        c.post(ver, json={"issue_number": "1101-2026-1", "note": "재확인 일치"})
        self.assertEqual(c.post(dec, json={"decision": "approve"}).status_code, 200)
        self.assertEqual(c.get("/api/rent3_lessor/status?branch=seoknam").json()["franchise"]["status"], "ACTIVE")

    def test_22_fake_identity_blocked_off_local(self):
        """22. 배포 환경(비 loopback·RENT3_FAKE_IDENTITY=0)에서는 가짜 본인인증이 막힌다."""
        import os
        c = self.client
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "lessor", "name": "배포임대", "phone": "01082810001", "username": "prodlsr", "password": "Pw123456", "branch": "prodb"})
        lh = self._as(reg.json()["token"])
        who = {"branch": "prodb", "name": "배포임대", "phone": "01082810001", "birth": "700101"}
        os.environ["RENT3_FAKE_IDENTITY"] = "0"
        try:
            r = c.post("/api/rent3_lessor/franchise/identity/start", headers=lh, json=who)
            self.assertEqual(r.status_code, 400)
            self.assertIn("연동", r.json()["message"])
            self.assertEqual(c.post("/api/rent3_lessor/franchise/identity/confirm", headers=lh, json={**who, "code": "000000"}).status_code, 400)
        finally:
            os.environ.pop("RENT3_FAKE_IDENTITY", None)


    def test_23_tenant_identity_gate(self):
        """23. 임차인 본인인증: 인증 전 계약 시작·응답·서명 불가, 가입 번호와 다른 번호·이미 인증된 사람의 다른 계정은 거부, 봉인 증거에 업체·시각이 남는다."""
        c = self.client

        def make(name, phone):
            r = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
                "role": "prospect", "name": name, "phone": phone, "password": "Pw123456"}).json()
            return r["user"]["id"], self._as(r["token"])

        tid, th = make("인증전", "01066660201")
        self.assertFalse(c.get("/api/rent3_contract/identity", headers=th).json()["identity"]["verified"])
        c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "909", "is_published": True})
        call = {"caller_role": "tenant", "branch": "main", "room_no": "909", "mode": "NEW",
                "tenant_name": "인증전", "tenant_phone": "010-6666-0201"}
        self.assertEqual(c.post("/api/rent3_contract/sessions/call", headers=th, json=call).status_code, 403)  # 인증 전 불가

        who = {"name": "김실명", "phone": "01066660201", "birth": "19900101"}
        self.assertEqual(c.post("/api/rent3_contract/identity/start", headers=th, json=who).status_code, 200)
        self.assertEqual(c.post("/api/rent3_contract/identity/confirm", headers=th, json={**who, "code": "123456"}).status_code, 400)  # 틀린 번호
        # 가입한 번호와 다른 휴대폰으로는 인증 불가
        bad = c.post("/api/rent3_contract/identity/confirm", headers=th, json={**who, "phone": "01099998888", "code": "000000"})
        self.assertEqual(bad.status_code, 400)
        ok = c.post("/api/rent3_contract/identity/confirm", headers=th, json={**who, "code": "000000"}).json()
        self.assertTrue(ok["identity"]["verified"])
        self.assertEqual(ok["identity"]["birth6"], "90****")
        self.assertTrue(ok["identity"]["fake"])

        # 같은 사람(이름+생년월일)이 다른 계정으로 또 인증 불가
        _, th2 = make("다른계정", "01066660202")
        dup = {"name": "김실명", "phone": "01066660202", "birth": "900101"}
        r = c.post("/api/rent3_contract/identity/confirm", headers=th2, json={**dup, "code": "000000"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("이미", r.json()["message"])

        # 인증 후에는 계약을 시작할 수 있고, 임대인은 본인인증 대상이 아니다
        self.assertEqual(c.post("/api/rent3_contract/sessions/call", headers=th, json=call).status_code, 200)
        self.assertFalse(c.get("/api/rent3_contract/identity", headers=self.NOAUTH).status_code == 200)
        chic = self._login("chic")
        self.assertFalse(c.get("/api/rent3_contract/identity", headers=chic).json()["applies"])

        # 배포 환경(가짜 인증 차단)에서는 인증 자체가 막힌다
        import os
        _, th3 = make("배포임차", "01066660203")
        os.environ["RENT3_FAKE_IDENTITY"] = "0"
        try:
            r = c.post("/api/rent3_contract/identity/start", headers=th3, json={"name": "배포임차", "phone": "01066660203", "birth": "900202"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("연동", r.json()["message"])
        finally:
            os.environ.pop("RENT3_FAKE_IDENTITY", None)


    def test_24_password_hash_lockout_reset_and_admin_temp(self):
        """24. 비밀번호 해시 저장·평문 자동 전환, 로그인 잠금, 본인인증 기반 재설정, 관리자 임시 비밀번호, 배포 환경 데모 계정 숨김."""
        import os
        from apps.rent_dist._rent3_core import Lessor, ratelimit

        c = self.client
        ratelimit.reset_all()
        os.environ["RENT3_DEMO_LOGIN"] = "0"   # 배포 환경처럼 동작
        users = lambda: open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read()

        def login(ident, pw):
            return c.post("/api/rent3/pool/auth/login", headers=self.NOAUTH,
                          json={"login_mode": "account", "identifier": ident, "password": pw})

        try:
            # (a) 가입한 비밀번호는 해시로만 저장된다
            reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
                "role": "lessor", "name": "해시임대", "phone": "01044440001", "username": "hashlsr", "password": "Hash#Pass9", "branch": "hashb"})
            self.assertEqual(reg.status_code, 200)
            self.assertNotIn("Hash#Pass9", users())
            self.assertIn("pbkdf2_sha256$", users())
            self.assertEqual(login("hashlsr", "Hash#Pass9").status_code, 200)
            self.assertEqual(login("hashlsr", "Hash#Pass8").status_code, 401)

            # (b) 예전 평문 저장분: 로그인에 성공하면 해시로 전환, 약한 평문(1212)은 배포 환경에서 거부
            Lessor(username="legacy", name="평문임대", phone="010-4444-0002", branch="legacyb", password="Legacy#Pass9").save("test")
            self.assertEqual(login("legacy", "Legacy#Pass9").status_code, 200)
            self.assertNotIn("Legacy#Pass9", users())
            Lessor(username="weaklegacy", name="약한평문", phone="010-4444-0003", branch="weakl", password="1212").save("test")
            self.assertEqual(login("weaklegacy", "1212").status_code, 401)

            # (c) 5번 틀리면 잠기고, 맞는 비밀번호도 잠금 동안은 거부된다 (429)
            for _ in range(5):
                self.assertEqual(login("lockme", "wrong-pass").status_code, 401)
            locked = login("lockme", "wrong-pass")
            self.assertEqual(locked.status_code, 429)
            self.assertIn("분 뒤", locked.json()["message"])
            self.assertEqual(login("hashlsr", "Hash#Pass9").status_code, 200)  # 다른 계정은 영향 없음(접속 주소 한도 미만)

            # (d) 본인인증 기반 재설정: 본인인증 이력이 있는 계정만 직접 가능
            ratelimit.reset_all()
            reg2 = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
                "role": "lessor", "name": "재설정임대", "phone": "01044440004", "username": "rstlsr", "password": "Old#Pass99", "branch": "rstb"})
            lh = self._as(reg2.json()["token"])
            who = {"branch": "rstb", "name": "재설정임대", "phone": "01044440004", "birth": "19700101"}
            os.environ["RENT3_FAKE_IDENTITY"] = "1"   # 이 구간만 가짜 본인인증 허용(본인인증 이력 만들기용)
            c.post("/api/rent3_lessor/franchise/identity/start", headers=lh, json=who)
            self.assertEqual(c.post("/api/rent3_lessor/franchise/identity/confirm", headers=lh, json={**who, "code": "000000"}).status_code, 200)
            rs = {"identifier": "rstlsr", "name": "재설정임대", "phone": "01044440004", "birth": "700101"}
            R = "/api/rent3/pool/auth/password-reset"
            self.assertEqual(c.post(f"{R}/start", headers=self.NOAUTH, json=rs).status_code, 200)
            bad = c.post(f"{R}/confirm", headers=self.NOAUTH, json={**rs, "name": "다른사람", "code": "000000", "new_password": "New#Pass99"})
            self.assertEqual(bad.status_code, 400)                                                # 정보가 다름
            self.assertEqual(c.post(f"{R}/confirm", headers=self.NOAUTH, json={**rs, "code": "111111", "new_password": "New#Pass99"}).status_code, 400)  # 인증번호 틀림
            self.assertEqual(c.post(f"{R}/confirm", headers=self.NOAUTH, json={**rs, "code": "000000", "new_password": "1212"}).status_code, 400)        # 약한 비밀번호
            self.assertEqual(c.post(f"{R}/confirm", headers=self.NOAUTH, json={**rs, "code": "000000", "new_password": "New#Pass99"}).status_code, 200)
            os.environ["RENT3_FAKE_IDENTITY"] = "0"
            self.assertEqual(login("rstlsr", "Old#Pass99").status_code, 401)
            self.assertEqual(login("rstlsr", "New#Pass99").status_code, 200)

            # (e) 본인인증 이력이 없는 계정은 직접 재설정 불가 — 계정 존재 여부는 알리지 않는다
            nov = {"identifier": "hashlsr", "name": "해시임대", "phone": "01044440001", "birth": "700101"}
            self.assertEqual(c.post(f"{R}/start", headers=self.NOAUTH, json=nov).status_code, 200)
            self.assertEqual(c.post(f"{R}/confirm", headers=self.NOAUTH, json={**nov, "code": "000000", "new_password": "New#Pass99"}).status_code, 400)

            # (f) 관리자 임시 비밀번호: 관리자만, 한 번만 보이고, 해시로 저장
            body = {"kind": "lessor", "id": reg.json()["user"]["id"]}
            self.assertEqual(c.post("/api/rent3_admin/accounts/reset-password", headers=self.NOAUTH, json=body).status_code, 401)
            self.assertIn(c.post("/api/rent3_admin/accounts/reset-password", headers=lh, json=body).status_code, (401, 403))
            tmp = c.post("/api/rent3_admin/accounts/reset-password", json=body).json()["temporary_password"]
            self.assertNotIn(tmp, users())
            self.assertEqual(login("hashlsr", "Hash#Pass9").status_code, 401)
            self.assertEqual(login("hashlsr", tmp).status_code, 200)

            # (g) 배포 환경에서는 데모 계정(공통 비밀번호 안내)을 내려주지 않는다
            self.assertEqual(c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["demo_accounts"], [])
        finally:
            os.environ.pop("RENT3_DEMO_LOGIN", None)
            os.environ.pop("RENT3_FAKE_IDENTITY", None)
            ratelimit.reset_all()
        self.assertTrue(c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["demo_accounts"])  # 로컬 시연에서는 보인다

    def test_25_password_migration_script(self):
        """25. 평문 비밀번호 전환 스크립트: 약한 값은 비우고, 그 밖은 해시로 바꾸며, 다시 실행해도 안전하다."""
        import subprocess
        import tempfile
        from apps.rent_dist._rent3_core import Lessor

        tmp = tempfile.mkdtemp(prefix="rent3_mig_")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        script = os.path.join(ROOT, "scripts", "hash_passwords.py")
        # 임시 풀을 시드하고 평문 비밀번호 두 개를 심는다
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from apps.rent_dist._rent3_core import ensure_pool_seeded, use_pool_dir, Lessor\n"
            "use_pool_dir(%r); ensure_pool_seeded(force=True)\n"
            "Lessor(username='mig1', name='이전1', phone='010-4444-1001', branch='mig1', password='Plain#Pass9').save('t')\n"
            "Lessor(username='mig2', name='이전2', phone='010-4444-1002', branch='mig2', password='1212').save('t')\n"
        ) % (ROOT, tmp)
        subprocess.run([sys.executable, "-c", code], check=True, env=env, capture_output=True)
        out = subprocess.run([sys.executable, script, "--pool-dir", tmp], check=True, env=env, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("약한 비밀번호 비움", out)
        raw = open(os.path.join(tmp, "users.json"), encoding="utf-8").read()
        self.assertNotIn("Plain#Pass9", raw)
        self.assertIn("pbkdf2_sha256$", raw)
        self.assertNotIn('"password": "1212"', raw)
        again = subprocess.run([sys.executable, script, "--pool-dir", tmp], check=True, env=env, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertIn("해시로 전환 0 · 약한 비밀번호 비움 0", again)
        shutil.rmtree(tmp, ignore_errors=True)


    def test_27_logout_revokes_token_and_api_headers(self):
        """27. 로그아웃하면 토큰이 즉시 무효(임대인·관리자 모두), API 응답에 보안 헤더."""
        c = self.client
        chic = self._login("chic")
        r = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic)
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r.headers.get("cache-control", ""))
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(c.post("/api/rent3/pool/auth/logout", headers=chic).status_code, 200)
        self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).status_code, 401)

        adm = self._login("admin")
        self.assertEqual(c.get("/api/rent3_admin/overview", headers=adm).status_code, 200)
        c.post("/api/rent3/pool/auth/logout", headers=adm)
        self.assertEqual(c.get("/api/rent3_admin/overview", headers=adm).status_code, 401)
        # 다시 로그인하면 새 토큰은 정상
        self.assertEqual(c.get("/api/rent3_admin/overview", headers=self._login("admin")).status_code, 200)


    def test_28_tenant_fraud_guards(self):
        """28. 임차인 사기 방어: 한 임차인의 동시 계약 2건 제한(호실 선점), 임대인 본인 계좌 입금 확인 없이는 체결 불가."""
        c = self.client
        r = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "선점자", "phone": "01066660401", "password": "Pw123456"}).json()
        th = self._as(r["token"])
        who = {"name": "선점자", "phone": "01066660401", "birth": "950101"}
        c.post("/api/rent3_contract/identity/start", headers=th, json=who)
        c.post("/api/rent3_contract/identity/confirm", headers=th, json={**who, "code": "000000"})
        for no in ("911", "912", "913"):
            c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": no, "is_published": True})
        call = lambda no: c.post("/api/rent3_contract/sessions/call", headers=th, json={
            "caller_role": "tenant", "branch": "main", "room_no": no, "mode": "NEW", "tenant_name": "선점자", "tenant_phone": "010-6666-0401"})
        self.assertEqual(call("911").status_code, 200)
        self.assertEqual(call("912").status_code, 200)
        self.assertEqual(call("911").status_code, 200)     # 이미 진행 중인 호실 재입장은 허용
        third = call("913")
        self.assertEqual(third.status_code, 400)
        self.assertIn("동시에", third.json()["message"])

        # 체결: 계좌 확인 표시 없이는 거부 (다른 조건과 무관하게 먼저 걸린다)
        sid = call("911").json()["session"]["id"]
        no = c.post(f"/api/rent3_contract/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER"})
        self.assertEqual(no.status_code, 400)
        self.assertIn("입금", no.json()["message"])


    def test_29_sms_identity_provider(self):
        """29. 문자 인증: 해시 보관·5분 만료·5회 제한·재발송 간격, 휴대폰 소유만 확인(phone_only)이라 등기부 자동 일치를 주지 않는다."""
        import os
        from apps.rent_dist._rent3_core import franchise as fr
        c = self.client
        prov = fr.PROVIDERS["sms"]
        sent = {}
        prov._send = lambda phone, text: sent.update({"phone": phone, "text": text})
        prov._pending.clear()
        os.environ["RENT3_IDENTITY_PROVIDER"] = "sms"
        os.environ["RENT3_FAKE_IDENTITY"] = "0"
        try:
            reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
                "role": "lessor", "name": "문자임대", "phone": "01055550501", "username": "smslsr", "password": "Pw123456", "branch": "smsb"})
            lh = self._as(reg.json()["token"])
            who = {"branch": "smsb", "name": "문자임대", "phone": "01055550501", "birth": "19700101"}
            st = c.post("/api/rent3_lessor/franchise/identity/start", headers=lh, json=who)
            self.assertEqual(st.status_code, 200)
            code = sent["text"].split("인증번호 ")[1][:6]
            self.assertNotIn(code, str(prov._pending))                      # 평문 미보관
            self.assertEqual(c.post("/api/rent3_lessor/franchise/identity/start", headers=lh, json=who).status_code, 400)  # 재발송 간격
            bad = c.post("/api/rent3_lessor/franchise/identity/confirm", headers=lh, json={**who, "code": "000000" if code != "000000" else "111111"})
            self.assertEqual(bad.status_code, 400)
            ok = c.post("/api/rent3_lessor/franchise/identity/confirm", headers=lh, json={**who, "code": code})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["application"]["identity"]["assurance"], "phone_only")
            self.assertEqual(c.post("/api/rent3_lessor/franchise/identity/confirm", headers=lh, json={**who, "code": code}).status_code, 400)  # 재사용 불가

            # 휴대폰 소유만 확인된 신원은 등기부가 완벽히 일치해도 자동 일치가 아니다
            chk = fr.check_ownership({"verified": True, "name": "문자임대", "birth6": "700101", "assurance": "phone_only"},
                {"document_type": "registry", "readable": True, "property_address": "인천 서해구 석남동 507-34",
                 "owners": [{"name": "문자임대", "share": "", "id_front": "700101"}]}, "인천 서해구 석남동 507-34")
            self.assertEqual(chk["code"], "PHONE_ONLY")
            self.assertFalse(chk["auto_pass"])

            # 5회 틀리면 폐기, 만료되면 거부
            prov._pending.clear(); prov._pending["01055550501"] = {"h": "x", "sent": 0, "tries": 0}
            self.assertEqual(c.post("/api/rent3_lessor/franchise/identity/confirm", headers=lh, json={**who, "code": "123456"}).status_code, 400)
            # 발송 업체 미설정이면 안내와 함께 거부
            prov._send = fr.SmsProvider._send.__get__(prov)
            prov._pending.clear()
            os.environ.pop("RENT3_SMS_SENDER", None)
            r = c.post("/api/rent3_lessor/franchise/identity/start", headers=lh, json=who)
            self.assertEqual(r.status_code, 400)
            self.assertIn("업체", r.json()["message"])
        finally:
            os.environ.pop("RENT3_IDENTITY_PROVIDER", None)
            os.environ.pop("RENT3_FAKE_IDENTITY", None)
            prov._pending.clear()
            prov.__dict__.pop("_send", None)


    def test_30_no_sensitive_fields_in_public_or_cross_party_listings(self):
        """30. 도어락 비밀번호·등기 정보·입주자 연락처·타 계약 세션이 공개 목록이나 타인에게 내려가지 않는다."""
        c = self.client
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "구경꾼", "phone": "01066660501", "password": "Pw123456"}).json()
        ten = self._as(reg["token"])
        chic, sunho = self._login("chic"), self._login("sunho")
        secret_keys = ("doorlock_pwd", "registry_info", "id_tenant", "active_contract_id")

        for hdr in (self.NOAUTH, ten):   # 비로그인·임차인
            for r in c.get("/api/rent3_tenant/listings", headers=hdr).json()["listings"]:
                for k in secret_keys:
                    self.assertNotIn(k, r, f"공개 매물 목록에 {k} 노출")
                if r.get("active_cosign_session"):
                    self.assertEqual(set(r["active_cosign_session"]), {"id", "session_code"})
        for r in c.get("/api/rent3_contract/rooms", headers=ten).json()["rooms"]:
            for k in secret_keys:
                self.assertNotIn(k, r)
            self.assertEqual(r["active_tenant_phone"], "")
        self.assertEqual(c.get("/api/rent3_contract/participants", headers=ten).json()["tenants"], [])

        # 임대인: 자기 지점은 전체, 다른 지점은 가려진다
        rooms = c.get("/api/rent3_contract/rooms", headers=sunho).json()["rooms"]
        main = [r for r in rooms if r["branch"] == "main"]
        own = [r for r in rooms if r["branch"] == "sunho"]
        self.assertTrue(main and all("doorlock_pwd" not in r and r["active_tenant_phone"] == "" for r in main))
        self.assertTrue(own and any("doorlock_pwd" in r for r in own))
        self.assertTrue(c.get("/api/rent3_contract/participants", headers=chic).json()["tenants"])


    def test_31_sensitive_fields_encrypted_at_rest(self):
        """31. 도어락·공동현관·계좌번호는 키가 있으면 디스크에 암호화되어 남고, 앱에서는 평문으로 보이며, 키 교체·키 없음·변조를 처리한다."""
        import glob, os
        from apps.rent_dist._rent3_core import Lessor, Room, fieldcrypto as fc

        def disk():
            return "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(self.temp_pool, "**", "*.json"), recursive=True))

        orig_master = os.environ.pop("MASTER_CRYPTO_KEY", None)   # 개발 PC·서버에 이미 설정돼 있을 수 있다 — 끝나면 되돌린다
        os.environ["RENT3_CRYPTO_KEY"] = "test-key-A-" + "x" * 30
        try:
            Lessor(id="lessor_enc", username="enc1", name="암호", phone="010-4444-9001", branch="encb",
                   account_number="신한 110-999-ENC-777").save("t")
            room = Room(id="room_encb_1", branch="encb", room_no="1", doorlock_pwd="9876*ENC").save("t")
            raw = disk()
            self.assertNotIn("ENC-777", raw)
            self.assertNotIn("9876*ENC", raw)
            self.assertIn("enc:v1:", raw)
            # 앱에서는 평문으로 읽힌다
            self.assertEqual(Lessor.get("lessor_enc").account_number, "신한 110-999-ENC-777")
            self.assertEqual(Room.get("room_encb_1").doorlock_pwd, "9876*ENC")

            # 같은 값도 매번 다르게 암호화(nonce), 다른 필드로 옮겨 붙이면 복호화 실패(AAD)
            a, b = fc.encrypt_value("doorlock_pwd", "same"), fc.encrypt_value("doorlock_pwd", "same")
            self.assertNotEqual(a, b)
            with self.assertRaises(Exception):
                fc.decrypt_value("account_number", a)

            # 키 교체: 새 키 + 옛 키(PREVIOUS)로 읽히고, 다시 저장하면 새 키로 바뀐다
            os.environ["RENT3_CRYPTO_KEY_PREVIOUS"] = os.environ["RENT3_CRYPTO_KEY"]
            os.environ["RENT3_CRYPTO_KEY"] = "test-key-B-" + "y" * 30
            self.assertEqual(Room.get("room_encb_1").doorlock_pwd, "9876*ENC")
            Room.get("room_encb_1").save("rekey")
            os.environ.pop("RENT3_CRYPTO_KEY_PREVIOUS")
            self.assertEqual(Room.get("room_encb_1").doorlock_pwd, "9876*ENC")           # 새 키만으로 읽힘
            # 엉뚱한 키로는 명확한 오류
            os.environ["RENT3_CRYPTO_KEY"] = "wrong-key-" + "z" * 30
            with self.assertRaises(Exception) as cm:
                Room.get("room_encb_1")
            self.assertIn("복호화", str(cm.exception))
            # 키가 없으면 평문 저장(로컬 개발)이지만 암호문을 읽으려 하면 안내 오류
            os.environ.pop("RENT3_CRYPTO_KEY")
            with self.assertRaises(Exception) as cm:
                Room.get("room_encb_1")
            self.assertIn("RENT3_CRYPTO_KEY", getattr(cm.exception, "hint", "") + str(cm.exception))
            Room(id="room_encb_2", branch="encb", room_no="2", doorlock_pwd="plain-dev").save("t")
            self.assertIn("plain-dev", disk())

            # 서버 마스터 키(MASTER_CRYPTO_KEY, Coolify 에 이미 있는 키)만 있어도 암호화되고, 그 키로 읽힌다
            os.environ["MASTER_CRYPTO_KEY"] = "coolify-master-" + "m" * 20
            Room(id="room_encb_3", branch="encb", room_no="3", doorlock_pwd="MASTER-SECRET-1").save("t")
            self.assertNotIn("MASTER-SECRET-1", disk())
            self.assertEqual(Room.get("room_encb_3").doorlock_pwd, "MASTER-SECRET-1")
            # 나중에 전용 키(RENT3_CRYPTO_KEY)를 추가해도 마스터 키로 쓴 값은 계속 읽힌다
            os.environ["RENT3_CRYPTO_KEY"] = "dedicated-key-" + "d" * 20
            self.assertEqual(Room.get("room_encb_3").doorlock_pwd, "MASTER-SECRET-1")
            # 마스터 키로 도출한 키는 .env.encrypted 용 키(SHA-256(마스터))와 같지 않다
            import hashlib
            self.assertNotEqual(fc._master_derived(), hashlib.sha256(os.environ["MASTER_CRYPTO_KEY"].encode()).digest())
        finally:
            os.environ.pop("RENT3_CRYPTO_KEY", None)
            os.environ.pop("RENT3_CRYPTO_KEY_PREVIOUS", None)
            if orig_master is None:
                os.environ.pop("MASTER_CRYPTO_KEY", None)
            else:
                os.environ["MASTER_CRYPTO_KEY"] = orig_master
            # 키 없이는 읽을 수 없는 암호문 레코드가 다른 테스트에 남지 않도록 id 로 직접 지운다
            for rid in ("room_encb_1", "room_encb_2", "room_encb_3"):
                Room(id=rid).delete()
            Lessor(id="lessor_enc").delete()


    def test_32_keys_locked_until_lessor_confirms_balance(self):
        """32. 체결 직후 도어락·공동현관은 잠금(API 마스킹) → 임대인이 잔금 입금 확인 후 교부해야 임차인에게 열린다. 예전 계약은 그대로 공개."""
        c = self.client
        LOCK = "입주 당일 잔금 완납 후 공개"
        chic = self._login("chic")
        # 예전 계약(플래그 없음)은 그대로 열려 있다
        legacy = c.get("/api/rent3_tenant/myhome/tenant_201").json()
        self.assertFalse(legacy["keys"]["locked"])
        self.assertNotEqual(legacy["room"]["doorlock_pwd"], LOCK)

        # 새 계약 체결
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "914", "is_published": True, "deposit": 6000000})
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "잔금임차", "phone": "01066660601", "password": "Pw123456"}).json()
        th, tid = self._as(reg["token"]), reg["user"]["id"]
        sid = self._complete_contract(chic, th, tid, "914", "잔금임차", "010-6666-0601")
        home = c.get(f"/api/rent3_tenant/myhome/{tid}", headers=th).json()
        self.assertTrue(home["keys"]["locked"])
        self.assertEqual(home["tenant"]["doorlock_pwd"], LOCK)
        self.assertEqual(home["room"]["doorlock_pwd"], LOCK)
        self.assertEqual(home["building"]["common_entrance_pwd"], LOCK)
        self.assertGreater(home["keys"]["balance_due"], 0)

        room = Room.first(branch="main", room_no="914")
        rel = f"/api/rent3_lessor/rooms/{room.id}/release-keys"
        self.assertEqual(c.post(rel, headers=th, json={"balance_checked": True}).status_code, 403)          # 임차인은 못 연다
        self.assertEqual(c.post(rel, headers=self._login("sunho"), json={"balance_checked": True}).status_code, 403)  # 다른 지점도
        self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 400)         # 잔금일 등기부 확인 전
        chk = f"/api/rent3_tenant/myhome/{tid}/balance-registry-check"
        self.assertEqual(c.post(chk, headers=th, json={"changed": False, "issue_number": "1101-2026-555"}).status_code, 200)
        self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": False}).status_code, 400)        # 입금 확인 표시 필수
        self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 200)
        self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 400)         # 이미 교부

        home = c.get(f"/api/rent3_tenant/myhome/{tid}", headers=th).json()
        self.assertFalse(home["keys"]["locked"])
        self.assertEqual(home["room"]["doorlock_pwd"], room.doorlock_pwd)
        self.assertEqual(home["keys"]["released_at"][:4], "2026")


    def test_34_facility_photos_and_join_qr_data(self):
        """34. 시설 점검 사진: 점검 단계에서만·당사자만·본인 사진만 삭제, 올리면 임차인 확인 해제, 체결 시 해시가 계약에 남는다."""
        import base64
        c, base = self.client, "/api/rent3_contract"
        png = "data:image/png;base64," + base64.b64encode(
            bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")).decode()
        chic, sunho = self._login("chic"), self._login("sunho")
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "사진임차", "phone": "01066660801", "password": "Pw123456"}).json()
        ten = self._as(reg["token"]); tid = reg["user"]["id"]
        who = {"name": "사진임차", "phone": "01066660801", "birth": "930303"}
        c.post(f"{base}/identity/start", headers=ten, json=who)
        c.post(f"{base}/identity/confirm", headers=ten, json={**who, "code": "000000"})
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "915", "is_published": True})
        sid = c.post(f"{base}/sessions/call", headers=ten, json={
            "caller_role": "tenant", "branch": "main", "room_no": "915", "mode": "NEW", "tenant_name": "사진임차", "tenant_phone": "010-6666-0801"}).json()["session"]["id"]

        # 호출 대기 화면의 QR 은 계약 id 만 담는다 — 로그인 없이도, 당사자가 아니어도 내용은 열리지 않는다
        self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=self.NOAUTH).status_code, 401)
        self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=sunho).status_code, 403)
        self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=chic).status_code, 200)

        c.post(f"{base}/sessions/{sid}/respond", headers=chic, json={"responder_role": "lessor", "responder_name": "김종심"})
        up = lambda hdr, role, cap="욕실", data=png: c.post(f"{base}/sessions/{sid}/facility-photos", headers=hdr, json={"role": role, "photo_data_url": data, "caption": cap})
        r = up(ten, "tenant")
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["photo"]["id"]
        self.assertEqual(len(r.json()["photo"]["sha256"]), 64)
        # 형식·역할 검사
        self.assertEqual(up(ten, "tenant", data="data:application/pdf;base64,AAAA").status_code, 400)
        self.assertEqual(up(ten, "lessor").status_code, 403)                       # 임차인 토큰으로 임대인 행세 금지
        self.assertEqual(up(sunho, "lessor").status_code, 403)                     # 다른 지점
        # 사진은 당사자만 본다 (img 태그용 ?token= 도 동일)
        url = f"{base}/sessions/{sid}/facility-photos/{pid}"
        self.assertEqual(c.get(url, headers=self.NOAUTH).status_code, 401)
        self.assertEqual(c.get(url, headers=sunho).status_code, 403)
        self.assertEqual(c.get(url, headers=chic).status_code, 200)
        self.assertEqual(c.get(url, headers=self.NOAUTH, params={"token": ten["Authorization"].split()[1]}).status_code, 200)
        # 임대인이 올리면 임차인 잠금이 풀리고, 남의 사진은 못 지운다
        rl = up(chic, "lessor", "싱크대")
        self.assertEqual(rl.status_code, 200)
        self.assertFalse(rl.json()["session"]["step2_tenant_locked"])
        self.assertFalse(rl.json()["session"]["step2_lessor_confirmed"])   # 사진이 바뀌면 양쪽 확인을 다시 받는다
        self.assertEqual(c.post(f"{url}/delete", headers=chic, json={"role": "lessor"}).status_code, 400)
        self.assertEqual(c.post(f"{url}/delete", headers=ten, json={"role": "tenant"}).status_code, 200)
        self.assertEqual(c.get(url, headers=chic).status_code, 404)
        # 12장 상한
        for _ in range(11):
            up(chic, "lessor")
        self.assertEqual(up(chic, "lessor").status_code, 400)

        # 점검 단계가 끝난 계약(체결 완료)에는 사진을 더 올릴 수 없다
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "916", "is_published": True})
        reg2 = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "사진임차2", "phone": "01066660802", "password": "Pw123456"}).json()
        sid2 = self._complete_contract(chic, self._as(reg2["token"]), reg2["user"]["id"], "916", "사진임차2", "010-6666-0802")
        late = c.post(f"{base}/sessions/{sid2}/facility-photos", json={"role": "lessor", "photo_data_url": png})
        self.assertIn(late.status_code, (400, 409))


    def test_35_prefill_before_meeting_and_balance_day_registry(self):
        """35. 대면 전 사전 작성(임대인=조건, 임차인=이메일·차량번호, 상대 연결 전까지만) + 잔금일 등기부 변동 신고 시 교부 불가."""
        c, base = self.client, "/api/rent3_contract"
        chic = self._login("chic")
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "사전임차", "phone": "01066660901", "password": "Pw123456"}).json()
        ten, tid = self._as(reg["token"]), reg["user"]["id"]
        who = {"name": "사전임차", "phone": "01066660901", "birth": "940404"}
        c.post(f"{base}/identity/start", headers=ten, json=who)
        c.post(f"{base}/identity/confirm", headers=ten, json={**who, "code": "000000"})
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "917", "is_published": True, "deposit": 7000000, "monthly_rent": 450000})
        sid = c.post(f"{base}/sessions/call", headers=chic, json={
            "caller_role": "lessor", "branch": "main", "room_no": "917", "mode": "NEW",
            "tenant_name": "사전임차", "tenant_phone": "010-6666-0901", "id_tenant": tid}).json()["session"]["id"]
        pf = f"{base}/sessions/{sid}/prefill"

        # 임대인: 조건 사전 작성(매물 값이 이미 채워져 있고 입주일 등만 바꾼다)
        cur = c.get(f"{base}/sessions/{sid}", headers=chic).json()["session"]["terms"]
        self.assertEqual((cur["deposit"], cur["monthly_rent"]), (7000000, 450000))   # 매물 등록 값이 자동으로 채워짐
        r = c.post(pf, headers=chic, json={"role": "lessor", "updates": {"move_in_date": "2026-11-25", "contract_deposit": 500000, "special_terms": "반려동물 불가"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["session"]["terms"]["move_in_date"], "2026-11-25")
        self.assertTrue(r.json()["session"]["draft_prefilled_at"])
        # 검증: 날짜 형식·계약금>보증금·허용 항목 밖
        self.assertEqual(c.post(pf, headers=chic, json={"role": "lessor", "updates": {"move_in_date": "내일"}}).status_code, 400)
        self.assertEqual(c.post(pf, headers=chic, json={"role": "lessor", "updates": {"contract_deposit": 99999999}}).status_code, 400)
        self.assertEqual(c.post(pf, headers=chic, json={"role": "lessor", "updates": {"tenant_name": "바꿈"}}).status_code, 400)
        # 임차인: 이메일·차량번호만 (금액·입주일은 못 바꾼다), 임대인 행세·타 지점 불가
        self.assertEqual(c.post(pf, headers=ten, json={"role": "tenant", "updates": {"tenant_email": "a@b.kr", "tenant_car_number": "12가3456"}}).status_code, 200)
        self.assertEqual(c.post(pf, headers=ten, json={"role": "tenant", "updates": {"deposit": 1}}).status_code, 400)
        self.assertEqual(c.post(pf, headers=ten, json={"role": "lessor", "updates": {"deposit": 1}}).status_code, 403)
        self.assertEqual(c.post(pf, headers=self._login("sunho"), json={"role": "lessor", "updates": {"deposit": 1}}).status_code, 403)

        # 연결되면 사전 작성은 닫히고(이후 변경은 ③단계 합의), 사전 작성한 값은 그대로 이어진다
        c.post(f"{base}/sessions/{sid}/respond", headers=ten, json={"responder_role": "tenant", "responder_name": "사전임차", "responder_phone": "010-6666-0901", "id_tenant": tid})
        self.assertEqual(c.post(pf, headers=chic, json={"role": "lessor", "updates": {"move_in_date": "2026-12-01"}}).status_code, 400)
        t2 = c.get(f"{base}/sessions/{sid}", headers=chic).json()["session"]["terms"]
        self.assertEqual((t2["move_in_date"], t2["tenant_email"], t2["special_terms"]), ("2026-11-25", "a@b.kr", "반려동물 불가"))

        # 사전 작성한 초안은 72시간까지 보존된다(일반 호출은 24시간)
        from datetime import datetime, timedelta
        from apps.rent_dist._rent3_core import CoSignSession
        sid2 = c.post(f"{base}/sessions/call", headers=chic, json={
            "caller_role": "lessor", "branch": "main", "room_no": "918", "mode": "NEW", "tenant_name": "사전임차", "tenant_phone": "010-6666-0901", "id_tenant": tid}).json()["session"]["id"] \
            if c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "918", "is_published": True}).status_code == 200 else ""
        s2 = CoSignSession.get(sid2)
        later = datetime.fromisoformat(s2.updated_at) + timedelta(hours=30)
        self.assertTrue(s2.is_idle_expired(later))                       # 사전 작성 전: 24시간 규칙
        c.post(f"{base}/sessions/{sid2}/prefill", headers=chic, json={"role": "lessor", "updates": {"move_in_date": "2026-12-05"}})
        s2 = CoSignSession.get(sid2)
        self.assertFalse(s2.is_idle_expired(datetime.fromisoformat(s2.updated_at) + timedelta(hours=30)))
        self.assertTrue(s2.is_idle_expired(datetime.fromisoformat(s2.updated_at) + timedelta(hours=73)))

    def test_36_balance_day_registry_change_blocks_key_release(self):
        """36. 잔금일 등기부에서 변동을 신고하면 교부 불가, 다시 '변동 없음'으로 확인돼야 교부된다."""
        c = self.client
        chic = self._login("chic")
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "919", "is_published": True, "deposit": 6000000})
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "잔금등기", "phone": "01066660902", "password": "Pw123456"}).json()
        th, tid = self._as(reg["token"]), reg["user"]["id"]
        self._complete_contract(chic, th, tid, "919", "잔금등기", "010-6666-0902")
        room = Room.first(branch="main", room_no="919")
        rel = f"/api/rent3_lessor/rooms/{room.id}/release-keys"
        chk = f"/api/rent3_tenant/myhome/{tid}/balance-registry-check"
        # 다른 임차인은 남의 계약에 확인을 기록할 수 없다
        other = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": "남", "phone": "01066660903", "password": "Pw123456"}).json()
        self.assertEqual(c.post(chk, headers=self._as(other["token"]), json={"changed": False}).status_code, 403)
        self.assertEqual(c.post(chk, headers=chic, json={"changed": False}).status_code, 403)   # 임대인이 대신 확인 불가

        self.assertEqual(c.post(chk, headers=th, json={"changed": True, "issue_number": "1101-2026-777"}).status_code, 200)
        r = c.post(rel, headers=chic, json={"balance_checked": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("변동", r.json()["message"])
        home = c.get(f"/api/rent3_tenant/myhome/{tid}", headers=th).json()
        self.assertTrue(home["keys"]["locked"])                                  # 변동 신고 중에는 잠금 유지
        self.assertTrue(home["keys"]["registry_check"]["changed"])
        # 해소 후 임차인이 다시 변동 없음으로 확인하면 교부 가능
        self.assertEqual(c.post(chk, headers=th, json={"changed": False}).status_code, 200)
        self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 200)
        # 교부 후에는 더 기록할 수 없다
        self.assertEqual(c.post(chk, headers=th, json={"changed": True}).status_code, 400)


    def test_26_legacy_api_removed_and_portal_intact(self):
        """26. 레거시 라우터(운영·공개 앱·엔티티·세무)와 구 가입·가짜 신원확인 경로는 제거되었고, 포털(로그인·가입·매물·재설정)은 그대로 동작한다."""
        c = self.client
        for method, u in (("get", "/api/rent3/entities/summary/dashboard"), ("get", "/api/rent3/admin/overview"),
                          ("get", "/api/rent3/tax/annual-statement?year=2026&branch=main"), ("get", "/api/rent3/chat/main/205"),
                          ("get", "/api/rent3/checkout/list?branch=main"), ("get", "/api/rent3/renewals"),
                          ("get", "/api/rent3/tenant/checkin-info/app_main_205"), ("get", "/api/rent3/public/listings"),
                          ("get", "/api/rent3/public/application/app_main_205"), ("post", "/api/rent3/cosign/start"),
                          ("post", "/api/rent3/auth/signup"), ("post", "/api/rent3/auth/verify-phone/send"),
                          ("post", "/api/rent3/auth/social-verify")):
            for hdr in (self.NOAUTH, {}):   # 로그인 여부와 무관하게 경로 자체가 없다
                r = getattr(c, method)(u, headers=hdr)
                self.assertIn(r.status_code, (404, 405), f"{method} {u} → {r.status_code}")
        # 옛 화면 파일도 더는 서빙되지 않는다
        for page in ("admin", "lessor", "tenant", "public", "home", "cosign", "login", "signup"):
            self.assertNotEqual(c.get(f"/rent3/{page}.html", headers=self.NOAUTH).headers.get("content-type", "").split(";")[0], "text/html",
                                f"/rent3/{page}.html 이 아직 서빙됨")
        # 포털은 정상
        self.assertEqual(c.get("/api/rent3/pool/landing", headers=self.NOAUTH).status_code, 200)
        for page in ("", "index.html", "manual.html", "pool.html"):
            self.assertEqual(c.get(f"/rent3/{page}", headers=self.NOAUTH).status_code, 200, page)


    def test_37_proxy_headers_never_make_a_request_local(self):
        """37. 프록시 뒤에서 X-Forwarded-For 를 127.0.0.1 로 위조해도 데모 로그인·가짜 인증이 열리지 않는다. 로그인 제한은 믿을 수 있는 프록시 단수에 따라 접속 주소를 고른다."""
        import os
        from apps.rent_dist._rent3_core.authz import client_ip, is_local_request
        c = self.client
        for env in ("RENT3_DEMO_LOGIN", "RENT3_FAKE_IDENTITY"):
            os.environ.pop(env, None)
        # 로컬 직접 접속: 데모 허용
        self.assertEqual(c.post("/api/rent3/pool/auth/login", headers=self.NOAUTH,
                                json={"login_mode": "account", "identifier": "chic", "password": "1212"}).status_code, 200)
        # 같은 요청에 프록시 흔적 헤더가 있으면(위조 포함) 로컬이 아니다 → 거부
        for h in ({"X-Forwarded-For": "127.0.0.1"}, {"X-Real-IP": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"}):
            r = c.post("/api/rent3/pool/auth/login", headers={**self.NOAUTH, **h},
                       json={"login_mode": "account", "identifier": "admin", "password": "1212"})
            self.assertEqual(r.status_code, 401, h)
        self.assertEqual(c.get("/api/rent3/pool/landing", headers={**self.NOAUTH, "X-Forwarded-For": "127.0.0.1"}).json()["demo_accounts"], [])

        class Req:
            def __init__(self, headers, host="10.0.0.5"):
                self.headers, self.client = headers, type("C", (), {"host": host})()
        self.assertFalse(is_local_request(Req({"x-forwarded-for": "1.2.3.4"}, "127.0.0.1")))
        self.assertTrue(is_local_request(Req({}, "127.0.0.1")))
        os.environ["RENT3_TRUSTED_PROXY_HOPS"] = "1"
        try:
            self.assertEqual(client_ip(Req({"x-forwarded-for": "6.6.6.6, 203.0.113.9"})), "203.0.113.9")   # 믿을 수 있는 프록시가 덧붙인 값
            self.assertEqual(client_ip(Req({})), "10.0.0.5")
        finally:
            os.environ.pop("RENT3_TRUSTED_PROXY_HOPS", None)
        self.assertEqual(client_ip(Req({"x-forwarded-for": "6.6.6.6"})), "10.0.0.5")                       # 기본은 소켓 주소만


    def test_38_deploy_check_script(self):
        """38. 배포 점검 스크립트: 올바른 운영 설정이면 통과(0), 시연용 설정·평문 비밀번호·평문 민감 필드가 있으면 실패(1)."""
        import subprocess, tempfile
        from apps.rent_dist._rent3_core import Lessor, Room, ensure_pool_seeded, use_pool_dir
        script = os.path.join(ROOT, "scripts", "deploy_check.py")
        base = {k: v for k, v in os.environ.items() if not k.startswith(("RENT3_", "BASS_", "MASTER_CRYPTO", "RENT2_", "GEMINI"))}
        base.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

        def run(extra, pool):
            r = subprocess.run([sys.executable, script], env={**base, **extra, "RENT3_POOL_DIR": pool}, capture_output=True, text=True, encoding="utf-8")
            return r.returncode, r.stdout

        good = {"BASS_SECRET": "s" * 32, "BASS_SEED_PASSWORD": "Seed#Pass99", "RENT3_DEMO_LOGIN": "0", "MASTER_CRYPTO_KEY": "m" * 32,
                "RENT3_IDENTITY_PROVIDER": "sms", "RENT3_SMS_SENDER": "solapi", "RENT3_SMS_API_KEY": "k", "RENT3_SMS_API_SECRET": "x",
                "RENT3_SMS_FROM": "0212345678", "RENT3_TRUSTED_PROXY_HOPS": "1", "GEMINI_API_KEY": "g", "RENT3_ALLOW_POOL_IN_APP": "1"}
        pool_dir = tempfile.mkdtemp(prefix="rent3_chk_")
        keep = self.temp_pool
        orig_master = os.environ.get("MASTER_CRYPTO_KEY")   # 개발 PC·서버에 이미 있을 수 있다 — 끝나면 되돌린다
        try:
            os.environ["MASTER_CRYPTO_KEY"] = good["MASTER_CRYPTO_KEY"]
            use_pool_dir(pool_dir)
            ensure_pool_seeded(force=True)           # 암호화 키가 있는 상태로 시드 → 민감 필드 암호화, 비밀번호는 평문이 없도록 비운다
            from apps.rent_dist._rent3_core import Admin, Tenant
            for cls in (Admin, Lessor, Tenant):
                for o in cls.all():
                    o.password = ""; o.save("t")
            code, out = run(good, pool_dir)
            self.assertEqual(code, 0, out)
            self.assertNotIn("m" * 32, out)          # 비밀 값은 출력하지 않는다
            # 시연용 설정·가짜 인증·날짜 고정은 실패
            for bad in ({"RENT3_DEMO_LOGIN": "1"}, {"RENT3_FAKE_IDENTITY": "1"}, {"RENT3_TODAY": "2026-01-01"}, {"BASS_SECRET": ""}):
                code, out = run({**good, **bad}, pool_dir)
                self.assertEqual(code, 1, (bad, out))
            # 평문 비밀번호가 남아 있으면 실패
            Lessor(username="plainpw", name="평문", phone="010-4444-7001", branch="plainb", password="Plain#Pass9").save("t")
            code, out = run(good, pool_dir)
            self.assertEqual(code, 1)
            self.assertIn("평문 비밀번호", out)
        finally:
            if orig_master is None:
                os.environ.pop("MASTER_CRYPTO_KEY", None)
            else:
                os.environ["MASTER_CRYPTO_KEY"] = orig_master
            use_pool_dir(keep)
            shutil.rmtree(pool_dir, ignore_errors=True)


    def test_39_e2e_pair_harness(self):
        """39. 공동 계약 시험 도구(scripts/e2e_pair): 차례가 아니면 거부하고, 자동 통과 19단계와 공격 시험 21항목이 모두 통과한다."""
        import socket, subprocess, tempfile, time as _t, urllib.request
        runtime = tempfile.mkdtemp(prefix="e2e_pair_")
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RENT3_", "BASS_", "RENT2_"))}
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "E2E_RUNTIME": runtime, "E2E_BASE": f"http://127.0.0.1:{port}",
                    "E2E_STATE": os.path.join(runtime, "state.json")})
        script = lambda n: os.path.join(ROOT, "scripts", "e2e_pair", n)
        srv = subprocess.Popen([sys.executable, script("server.py"), "--fresh", "--port", str(port)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                    break
                except Exception:
                    _t.sleep(0.5)
            else:
                self.fail("시험 서버가 뜨지 않았습니다")
            run = lambda *a: subprocess.run([sys.executable, script("play.py"), *a], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run("reset").returncode, 0)
            out_of_turn = run("tenant", "signup")
            self.assertEqual(out_of_turn.returncode, 2)
            self.assertIn("차례가 아닙니다", out_of_turn.stdout)
            self.assertEqual(run("lessor", "fly").returncode, 2)
            first = run("lessor", "prepare")
            self.assertEqual(first.returncode, 0, first.stdout)
            self.assertIn("다음 차례: [tenant]", first.stdout)
            auto = run("auto")
            self.assertEqual(auto.returncode, 0, auto.stdout + auto.stderr)
            self.assertIn("전체 19단계 성공", auto.stdout)
            attack = run("attack")
            self.assertEqual(attack.returncode, 0, attack.stdout + attack.stderr)
            import re as _re
            m = _re.search(r"결과: (\d+)/(\d+) 항목이 기대대로 막혔습니다", attack.stdout)
            self.assertTrue(m and m.group(1) == m.group(2) and int(m.group(2)) >= 38, attack.stdout[-400:])   # 기존 공격 + 등기부 차단 정책 항목
            self.assertIn("[등기부 차단 정책]", attack.stdout)
            # 두 AI 가 각자 한 줄(run)만 실행해도 번갈아 끝까지 진행된다 (임차인이 먼저 시작해도 기다린다)
            self.assertEqual(run("reset").returncode, 0)
            procs = {r: subprocess.Popen([sys.executable, script("play.py"), "run", r, "--timeout", "120"], env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8") for r in ("tenant", "lessor")}
            outs = {r: p.communicate(timeout=180)[0] for r, p in procs.items()}
            for r, p in procs.items():
                self.assertEqual(p.returncode, 0, f"{r}: {outs[r]}")
            self.assertIn("모든 단계가 끝났습니다", outs["lessor"])
            self.assertEqual(run("status").stdout.count("모든 단계"), 1)
            # 상대가 오지 않으면 시간 안에 멈추고 종료 코드 3
            self.assertEqual(run("reset").returncode, 0)
            lonely = subprocess.run([sys.executable, script("play.py"), "run", "tenant", "--timeout", "3"], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(lonely.returncode, 3, lonely.stdout)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except Exception:
                srv.kill()
            shutil.rmtree(runtime, ignore_errors=True)


    def test_40_e2e_failure_scenarios(self):
        """40. 실패 시나리오 시험(scripts/e2e_pair fail): 무응답 자동 해제, 체결 직전·잔금일 등기부 변동, 당사자 취소와 사유 전파, 불일치 확인 기록."""
        import socket, subprocess, tempfile, time as _t, urllib.request
        runtime = tempfile.mkdtemp(prefix="e2e_fail_")
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RENT3_", "BASS_", "RENT2_"))}
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "E2E_RUNTIME": runtime, "E2E_BASE": f"http://127.0.0.1:{port}",
                    "E2E_STATE": os.path.join(runtime, "state.json")})
        script = lambda n: os.path.join(ROOT, "scripts", "e2e_pair", n)
        srv = subprocess.Popen([sys.executable, script("server.py"), "--fresh", "--port", str(port), "--idle-seconds", "6"], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                    break
                except Exception:
                    _t.sleep(0.5)
            else:
                self.fail("시험 서버가 뜨지 않았습니다")
            r = subprocess.run([sys.executable, script("play.py"), "fail"], env=env, capture_output=True, text=True, encoding="utf-8", timeout=240)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            import re as _re
            m = _re.search(r"결과: (\d+)/(\d+) 항목 통과", r.stdout)
            self.assertTrue(m and m.group(1) == m.group(2) and int(m.group(2)) >= 30, r.stdout[-400:])
            self.assertNotIn("건너뜀", r.stdout)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except Exception:
                srv.kill()
            shutil.rmtree(runtime, ignore_errors=True)


    def _policy_session(self, room_no, deposit, rent, lessor_mortgage=0):
        """정책 시험용: 호실을 만들고 계약을 연결해 임대인 등기부 제시까지 마친 세션 id 를 돌려준다."""
        c, base = self.client, "/api/rent3_contract"
        c.post("/api/rent3_admin/lessors/lessor_chic/room-limit", json={"room_limit": 500})   # 시험 호실이 쌓여 가맹점당 한도(30실)에 걸리지 않게
        mk = c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": room_no, "is_published": True, "deposit": deposit, "monthly_rent": rent})
        self.assertEqual(mk.status_code, 200, mk.text)
        made = c.post(f"{base}/sessions/call", json={"caller_role": "lessor", "branch": "main", "room_no": room_no, "mode": "NEW",
                                                    "tenant_name": "정책시험", "tenant_phone": "010-5555-9" + room_no[-3:]})
        self.assertEqual(made.status_code, 200, made.text)
        sid = made.json()["session"]["id"]
        c.post(f"{base}/sessions/{sid}/respond", json={"responder_role": "tenant", "responder_name": "정책시험", "responder_phone": "010-5555-9" + room_no[-3:]})
        c.post(f"{base}/sessions/{sid}/step1", json={"role": "lessor", "checked_counterparty_id": True,
                                                    "registry_updates": {"owner_name": "김종심", "mortgage_amount": lessor_mortgage}})
        return sid

    def _reg(self, sid, **kw):
        d = {"viewed_directly": True, "viewed_at": "2026-11-08T10:00", "owner_name": "김종심", "mortgage_amount": 0,
             "seizure_or_trust": "없음", "guarantee_status": "가입 예정"}
        d.update(kw)
        return self.client.post(f"/api/rent3_contract/sessions/{sid}/tenant-registry", json={"registry": d})

    def test_41_registry_block_policy(self):
        """41. 등기부 차단 정책: 소유자 불일치·압류는 절대 차단(즉시 중단), 전세·고액+근저당은 보증보험 또는 말소 특약 필요, 소액 월세+근저당은 강한 위험 확인."""
        c, base = self.client, "/api/rent3_contract"

        # (a) 소유자 불일치: 확인 체크를 해도 계약이 즉시 중단된다
        sid = self._policy_session("931", 80000000, 450000)
        r = self._reg(sid, owner_name="박사기", mismatch_ack=True, low_deposit_risk_ack=True)
        self.assertEqual(r.status_code, 409)
        ss = c.get(f"{base}/sessions/{sid}").json()["session"]
        self.assertEqual(ss["status"], "CANCELLED")
        self.assertIn("소유자", ss["cancel_reason"])
        self.assertTrue(ss["tenant_registry"]["blocked"])                      # 임차인이 본 값이 근거로 남는다
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True}).status_code, 409)

        # (b) 압류·신탁 등 있음: 역시 절대 차단
        sid = self._policy_session("932", 80000000, 450000)
        self.assertEqual(self._reg(sid, seizure_or_trust="있음", mismatch_ack=True).status_code, 409)
        self.assertEqual(c.get(f"{base}/sessions/{sid}").json()["session"]["status"], "CANCELLED")

        # (c) 소액 월세(보증금 2천만 이하) + 선순위 근저당: 강한 위험 확인이 있어야만 진행
        sid = self._policy_session("933", 10000000, 450000, lessor_mortgage=500000000)
        self.assertEqual(self._reg(sid, mortgage_amount=500000000).status_code, 400)
        self.assertEqual(self._reg(sid, mortgage_amount=500000000, low_deposit_risk_ack=True).status_code, 200)
        # 2천만 원 초과 월세(보증금 3천만)는 이 확인이 필요 없다
        sid = self._policy_session("934", 30000000, 450000, lessor_mortgage=500000000)
        self.assertEqual(self._reg(sid, mortgage_amount=500000000).status_code, 200)

        # (d) 전세 + 선순위 근저당 + 보증보험 '가입 안 함' + 말소 특약 없음 → 합의·서명 차단
        sid = self._policy_session("935", 100000000, 0, lessor_mortgage=300000000)
        self.assertEqual(self._reg(sid, mortgage_amount=300000000, guarantee_status="가입 안 함").status_code, 200)
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        blocked = c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": rev})
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("근저당", blocked.json()["message"])
        # 고액 월세(보증금 6천만)도 같다
        sid_hi = self._policy_session("936", 60000000, 400000, lessor_mortgage=200000000)
        self._reg(sid_hi, mortgage_amount=200000000, guarantee_status="가입 안 함")
        rev_hi = c.get(f"{base}/sessions/{sid_hi}").json()["session"]["revision"]
        self.assertEqual(c.post(f"{base}/sessions/{sid_hi}/step3/agree", json={"role": "tenant", "expected_revision": rev_hi}).status_code, 400)
        # 고액인데 근저당이 없으면 막지 않는다
        sid_ok = self._policy_session("937", 100000000, 0, lessor_mortgage=0)
        self._reg(sid_ok)
        rev_ok = c.get(f"{base}/sessions/{sid_ok}").json()["session"]["revision"]
        self.assertEqual(c.post(f"{base}/sessions/{sid_ok}/step3/agree", json={"role": "lessor", "expected_revision": rev_ok}).status_code, 200)

        # (e) 말소 특약을 넣으면 풀리고, 특약 문구가 특약사항에 들어간다 — 해제하면 문구가 빠진다
        on = c.post(f"{base}/sessions/{sid}/terms", json={"role": "lessor", "updates": {"mortgage_cleanup_clause": True}}).json()["session"]
        self.assertIn("전액 말소", on["terms"]["special_terms"])
        rev = on["revision"]
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": rev}).status_code, 200)
        off = c.post(f"{base}/sessions/{sid}/terms", json={"role": "lessor", "updates": {"mortgage_cleanup_clause": False}}).json()["session"]
        self.assertNotIn("전액 말소", off["terms"]["special_terms"])
        c.post(f"{base}/sessions/{sid}/terms", json={"role": "lessor", "updates": {"mortgage_cleanup_clause": True}})

        # (f) 보증보험 '가입 완료'면 말소 특약 없이도 진행된다
        sid_ins = self._policy_session("938", 100000000, 0, lessor_mortgage=300000000)
        self._reg(sid_ins, mortgage_amount=300000000, guarantee_status="가입 완료")
        rev_ins = c.get(f"{base}/sessions/{sid_ins}").json()["session"]["revision"]
        self.assertEqual(c.post(f"{base}/sessions/{sid_ins}/step3/agree", json={"role": "lessor", "expected_revision": rev_ins}).status_code, 200)

        # (g) 말소 특약 계약은 잔금일에 근저당 말소가 확인돼야 도어락이 교부된다 (끝까지 진행)
        sid = sid  # 말소 특약이 들어간 (d) 세션
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        c.post(f"{base}/sessions/{sid}/step2", json={"role": "lessor"})
        c.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
        c.post(f"{base}/sessions/{sid}/step2", json={"role": "tenant"})
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        for role in ("lessor", "tenant"):
            c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
        for role in ("lessor", "tenant"):
            self.assertEqual(c.post(f"{base}/sessions/{sid}/step4/sign", json={"role": role, "signature": role, "expected_revision": rev}).status_code, 200)
        self._tenant_recheck_ok(sid)
        done = c.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
        self.assertEqual(done.status_code, 200, done.text)
        ct = done.json()["contract"]
        self.assertTrue(ct["mortgage_cleanup_required"])
        tid = ct["id_tenant"]
        chk = f"/api/rent3_tenant/myhome/{tid}/balance-registry-check"
        rel = f"/api/rent3_lessor/rooms/{ct['id_room']}/release-keys"
        self.assertEqual(c.post(chk, json={"changed": False}).status_code, 400)                          # 말소 확인 없이는 기록 불가
        self.assertEqual(c.post(rel, json={"balance_checked": True}).status_code, 400)
        self.assertEqual(c.post(chk, json={"changed": False, "mortgage_cleared": True}).status_code, 200)
        self.assertEqual(c.post(rel, json={"balance_checked": True}).status_code, 200)


    def test_42_insurance_pending_allowed_with_auto_refusal_clause(self):
        """42. 보증보험 '가입 예정'은 허용하되, '가입 거절 시 계약 무효·계약금 전액 반환' 특약이 자동 필수 삽입되고 지워도 다시 들어간다."""
        c, base = self.client, "/api/rent3_contract"
        sid = self._policy_session("941", 100000000, 0, lessor_mortgage=300000000)
        r = self._reg(sid, mortgage_amount=300000000, guarantee_status="가입 예정")
        self.assertEqual(r.status_code, 200, r.text)
        sp = c.get(f"{base}/sessions/{sid}").json()["session"]["terms"]["special_terms"]
        self.assertIn("가입이 거절되면 이 계약은 무효", sp)                      # 자동 삽입
        self.assertIn("계약금 전액을 즉시 반환", sp)
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": rev}).status_code, 200)  # 근저당이 있어도 합의 가능

        # 임대인이 특약사항 본문에서 지워도 다시 들어간다 (필수)
        edited = c.post(f"{base}/sessions/{sid}/terms", json={"role": "lessor", "updates": {"special_terms": "1. 반려동물 불가"}}).json()["session"]["terms"]["special_terms"]
        self.assertIn("반려동물 불가", edited)
        self.assertIn("가입이 거절되면 이 계약은 무효", edited)

        # 임차인이 '가입 안 함'으로 바꾸면 특약이 빠지고 진행이 막힌다
        self.assertEqual(self._reg(sid, mortgage_amount=300000000, guarantee_status="가입 안 함").status_code, 200)
        t2 = c.get(f"{base}/sessions/{sid}").json()["session"]
        self.assertNotIn("가입이 거절되면 이 계약은 무효", t2["terms"]["special_terms"])
        blocked = c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "tenant", "expected_revision": t2["revision"]})
        self.assertEqual(blocked.status_code, 400)

        # 근저당이 없거나 보증금이 작은 월세는 특약을 넣지 않는다
        small = self._policy_session("942", 30000000, 450000, lessor_mortgage=0)
        self._reg(small, guarantee_status="가입 예정")
        self.assertNotIn("가입이 거절되면 이 계약은 무효", c.get(f"{base}/sessions/{small}").json()["session"]["terms"]["special_terms"])


    def test_43_frontends_do_not_call_staff_only_apis_as_tenant(self):
        """43. 회귀 방지: 임차인 마이홈 화면이 직원 전용 API(/tenants)를 세입자 본인 로그인으로 부르면 화면이 비어 버린다(실제로 있었던 사고). 세입자 본인은 목록을 부르지 않는다."""
        src = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn("const isTenantUser", src)
        self.assertIn("if (!tenantList.length && !isTenantUser) await loadTenants();", src)
        # 서버 쪽 약속: 임차인 토큰으로 /tenants 는 403, 본인 마이홈은 200
        reg = self.client.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "마이홈본인", "phone": "01066661201", "password": "Pw123456"}).json()
        th = self._as(reg["token"])
        self.assertEqual(self.client.get("/api/rent3_tenant/tenants", headers=th).status_code, 403)
        self.assertEqual(self.client.get(f"/api/rent3_tenant/myhome/{reg['user']['id']}", headers=th).status_code, 200)

    def test_44_insurance_pending_requires_proof_before_keys(self):
        """44. '보증보험 가입 예정'으로 계약하면 잔금일 전에 증빙(보증기관·증권번호)을 제출해야 도어락이 교부된다."""
        c, base = self.client, "/api/rent3_contract"
        sid = self._policy_session("945", 100000000, 0, lessor_mortgage=300000000)
        self.assertEqual(self._reg(sid, mortgage_amount=300000000, guarantee_status="가입 예정").status_code, 200)
        c.post(f"{base}/sessions/{sid}/step2", json={"role": "lessor"})
        c.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
        c.post(f"{base}/sessions/{sid}/step2", json={"role": "tenant"})
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        for role in ("lessor", "tenant"):
            c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
        for role in ("lessor", "tenant"):
            self.assertEqual(c.post(f"{base}/sessions/{sid}/step4/sign", json={"role": role, "signature": role, "expected_revision": rev}).status_code, 200)
        self._tenant_recheck_ok(sid)
        done = c.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
        self.assertEqual(done.status_code, 200, done.text)
        ct = done.json()["contract"]
        self.assertTrue(ct["insurance_proof_required"])
        tid = ct["id_tenant"]
        chk = f"/api/rent3_tenant/myhome/{tid}/balance-registry-check"
        prf = f"/api/rent3_tenant/myhome/{tid}/insurance-proof"
        rel = f"/api/rent3_lessor/rooms/{ct['id_room']}/release-keys"
        self.assertEqual(c.post(chk, json={"changed": False}).status_code, 200)
        self.assertEqual(c.post(rel, json={"balance_checked": True}).status_code, 400)       # 증빙 없이는 교부 불가
        self.assertEqual(c.post(prf, json={"issuer": "H", "policy_no": "1"}).status_code, 400)  # 너무 짧은 값
        self.assertEqual(c.post(prf, json={"issuer": "HUG", "policy_no": "2026-123456"}).status_code, 200)
        self.assertEqual(c.post(rel, json={"balance_checked": True}).status_code, 200)

        # '가입 완료'로 계약하면 증빙 절차가 없다
        sid2 = self._policy_session("946", 100000000, 0, lessor_mortgage=300000000)
        self._reg(sid2, mortgage_amount=300000000, guarantee_status="가입 완료")
        self.assertFalse(c.get(f"{base}/sessions/{sid2}").json()["session"]["terms"].get("insurance_proof_required", False))

    def test_45_fresh_pool_tool_builds_real_data_only(self):
        """45. scripts/fresh_pool.py: 시험용 시드(chic/1212·샘플 계약·수납) 없이 설정한 임대인·건물·호실만 만들고, 기존 폴더는 --wipe 로 백업한다."""
        import subprocess, tempfile
        from apps.rent_dist._rent3_core import Building, Lessor, Pay, Room, Tenant, use_pool_dir
        work = tempfile.mkdtemp(prefix="fresh_pool_")
        keep = self.temp_pool
        try:
            cfg = {"admin": {"username": "admin"}, "lessors": [{
                "username": "freshlessor", "password": "Fresh#Pass99", "name": "신규임대인", "phone": "01055550001", "branch": "fresh",
                "building": {"name": "신규빌", "address": "인천 서해구 석남동 1-1", "rooms": [{"room_no": "101", "deposit": 3000000, "monthly_rent": 300000}], "rooms_csv": "r.csv"}}]}
            open(os.path.join(work, "r.csv"), "w", encoding="utf-8").write("room_no,deposit,monthly_rent,maintenance_fee,pay_timing\n102,5000000,290000,100000,선불\n")
            cfgp = os.path.join(work, "c.json")
            json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)
            pool_dir = os.path.join(work, "pool")
            script = os.path.join(ROOT, "scripts", "fresh_pool.py")
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            run = lambda *extra: subprocess.run([sys.executable, script, "--config", cfgp, "--pool-dir", pool_dir, *extra], capture_output=True, text=True, encoding="utf-8", env=env)
            r = run(); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotEqual(run().returncode, 0)                 # 데이터가 있으면 --wipe 없이는 거부
            r = run("--wipe"); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue([d for d in os.listdir(work) if d.startswith("pool.bak-")])   # 기존 폴더는 백업으로 남는다
            use_pool_dir(pool_dir)
            self.assertIsNone(Lessor.first(username="chic"))         # 시험용 계정 없음
            self.assertEqual(Tenant.count(), 0); self.assertEqual(Pay.count(), 0)
            self.assertEqual(sorted(x.room_no for x in Room.all()), ["101", "102"])
            ls = Lessor.first(username="freshlessor")
            self.assertTrue(ls.password.startswith("pbkdf2") or ls.password != "Fresh#Pass99")   # 해시 저장
            self.assertEqual(Building.first(branch="fresh").address, "인천 서해구 석남동 1-1")
            ok = self.client.post("/api/rent3/pool/auth/login", headers={"Authorization": ""}, json={"login_mode": "account", "identifier": "freshlessor", "password": "Fresh#Pass99"})
            self.assertEqual(ok.status_code, 200, ok.text)
            bad = self.client.post("/api/rent3/pool/auth/login", headers={"Authorization": ""}, json={"login_mode": "account", "identifier": "freshlessor", "password": "Wrong#Pass99"})
            self.assertEqual(bad.status_code, 401)
        finally:
            use_pool_dir(keep)
            shutil.rmtree(work, ignore_errors=True)


    def test_46_ledger_import_from_paper_ledgers(self):
        """46. scripts/ledger_import.py: 종이 장부 CSV → 세입자·계약(만기=입주일+1년 자동연장)·월별 수납. 장부의 미수·완납 처리·정정값이 원장과 맞는다."""
        import subprocess, tempfile
        from apps.rent_dist._rent3_core import Contract, Tenant, use_pool_dir
        work = tempfile.mkdtemp(prefix="ledger_imp_")
        keep = self.temp_pool
        try:
            sin = ("호실,성명,입주일,보증금(만원),월세(만원),관리비(만원),입금액,전화,타명/연락처 메모,미수,납부일,비고(키)\n"
                   "101,가나다,2026-05-24,200,후불 25,8,,010-1111-2222,,,24,1907\n"
                   "103,라마바,2023-12-31,200,후불 28,5,,010-3333-4444 / 부인 010-5555-6666,부인,231,,1234\n"
                   "404,사아자,2017-04-14,300,선불 32,3,,010-7777-8888,,35,,0000\n")
            sun = ("호실,성명,전화번호,입주일,보증금(만원),월세(만원),1월 납부,2월 납부,3월 납부,4월 납부,5월 납부,6월 납부,7월 납부,8월 납부,9월 납부,10월 납부,11월 납부\n"
                   "201,차카타,8856-4915,2021-02-01,500,50,2/2,3/3,4/11,5/2,8/2,7/6,8/24,9/24,10/4,,\n"
                   "404,파하갑,5349-2190,2020-05-14,500,60,3/31,////,,,,,,,,,\n")
            open(os.path.join(work, "sin.csv"), "w", encoding="utf-8").write(sin)
            open(os.path.join(work, "sun.csv"), "w", encoding="utf-8").write(sun)
            cfg = {"lessors": [
                {"username": "lsin", "password": "Sinbi#Pass99", "name": "신비", "phone": "01011110001", "branch": "ls1",
                 "building": {"name": "가빌", "address": "a", "ledger": {"format": "sinbi", "csv": "sin.csv"}}},
                {"username": "lsun", "password": "Sunho#Pass99", "name": "선호", "phone": "01011110002", "branch": "ls2",
                 "building": {"name": "나빌", "address": "b", "ledger": {"format": "sunho", "csv": "sun.csv", "overrides": {"404": {"settle_through": "2026-10", "deposit": 1000}}}}}]}
            cfgp = os.path.join(work, "c.json")
            json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)
            pool_dir = os.path.join(work, "pool")
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "fresh_pool.py"), "--config", cfgp, "--pool-dir", pool_dir],
                               capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            use_pool_dir(pool_dir)
            g = lambda cid: Contract.get(cid)
            c103 = g("ct_ls1_103")
            self.assertEqual(c103.ledger(up_to_ym="2026-10")["total_unpaid"], 2_310_000)          # 장부의 미수 231만 원 = 7개월
            self.assertEqual(c103.ledger(up_to_ym="2026-10")["oldest_unpaid_ym"], "2026-04")
            self.assertEqual(g("ct_ls1_404").ledger(up_to_ym="2026-10")["total_unpaid"], 350_000)  # 미수 35
            self.assertEqual(g("ct_ls1_101").ledger(up_to_ym="2026-10")["total_unpaid"], 0)       # 10월분 납부
            self.assertEqual(g("ct_ls1_101").expiry_date, "2027-05-24")                          # 입주일 + 1년
            self.assertEqual(c103.expiry_date, "2026-12-31")                                      # 3년째 만기가 오늘 이후
            self.assertEqual(g("ct_ls2_404").deposit, 10_000_000)                                  # 정정값
            self.assertEqual(g("ct_ls2_404").ledger(up_to_ym="2026-10")["total_unpaid"], 0)       # 완납 처리
            self.assertEqual(g("ct_ls2_201").ledger(up_to_ym="2026-09")["total_unpaid"], 0)       # 9월분을 10/4 납부(후불)
            self.assertEqual(Tenant.get("tenant_ls2_201").phone, "010-8856-4915")                 # 8자리 번호에 010 보정
            self.assertNotEqual(Tenant.get("tenant_ls2_201").password, "1212")                    # 기본 비밀번호 1212 로는 로그인 불가
        finally:
            use_pool_dir(keep)
            shutil.rmtree(work, ignore_errors=True)


    def test_47_chic_ledger_two_year_blocks(self):
        """47. 시크빌형 장부(2025·2026 두 덩어리): 공실·전입 표시로 새 세입자 입주일을 잡고, 이전 세입자 입금은 버리며, 날짜가 이상한 칸은 금액이 있으면 입금으로 센다."""
        import csv as _csv, io, subprocess, tempfile
        from apps.rent_dist._rent3_core import Contract, Tenant, use_pool_dir
        work = tempfile.mkdtemp(prefix="chic_ledger_")
        keep = self.temp_pool
        try:
            def room(no, name, move, rent, fee, cells2025, cells2026, deposit="5,000,000", kind="월세"):
                def block(cells):
                    row = []
                    for c in (cells + [""] * 12)[:12]:
                        row += [c, ("%s" % format(rent, ",")) if c and "공실" not in c else "", ("%s" % format(fee, ",")) if c and "공실" not in c else ""]
                    return row
                head = [no, name, "******", "010-1000-%04d" % int(no), move, "", kind, "후불", deposit]
                return head + block(cells2025), head + block(cells2026)
            r1a, r1b = room("101", "가나", "2025. 6. 4", 150000, 100000, ["2. 3", "공실", "공실", "공실", "공실", "전입6/3", "7. 3", "8. 3", "9. 2", "10. 3", "11. 3", "12. 3"], ["1. 3", "2. 3", "3. 3", "4. 3", "5. 3", "6. 3", "7. 2", "8. 3", "9. 2"])
            r2a, r2b = room("201", "다라", "2022. 3. 19", 290000, 150000, ["1. 15", "2. 19", "3. 27", "4. 23", "5. 26", "6. 25", "7. 27", "8. 29", "9. 15", "10. 23", "11. 21", "11.25"], ["1. 22", "2. 23", "3. 13", "4. 19", "5. 15", "6. 17", "7. 20", "8. 21", "9. 18", "10. 21"])
            buf = io.StringIO(); w = _csv.writer(buf)
            for yr, (x, y) in ((2025, (r1a, r2a)), (2026, (r1b, r2b))):
                w.writerow(["%d년도" % yr]); w.writerow(["호실", "세입자명"]); w.writerow([""])
                w.writerow(x); w.writerow(y); w.writerow(["701", "주인세대"])
            open(os.path.join(work, "c.csv"), "w", encoding="utf-8").write(buf.getvalue())
            cfg = {"lessors": [{"username": "lchic", "password": "Chic#Pass999", "name": "시크", "phone": "01011110003", "branch": "ch",
                                "building": {"name": "시크빌", "address": "c", "ledger": {"format": "chic", "csv": "c.csv"}}}]}
            cfgp = os.path.join(work, "c.json"); json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "fresh_pool.py"), "--config", cfgp, "--pool-dir", os.path.join(work, "pool")],
                               capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            use_pool_dir(os.path.join(work, "pool"))
            c1, c2 = Contract.get("ct_ch_101"), Contract.get("ct_ch_201")
            self.assertEqual(c1.move_in_date, "2025-06-04")                  # '전입6/3' 표시 달의 전입일 칸 날짜
            self.assertEqual(c1.expiry_date, "2027-06-04")                   # 계약일 + 1년 자동 연장
            self.assertEqual(c1.ledger(up_to_ym="2026-09")["total_unpaid"], 0)   # 이전 세입자의 1·2월 입금은 버리고 6월 이후 낸 것만 센다
            self.assertEqual(c1.ledger(up_to_ym="2026-10")["total_unpaid"], 250_000)  # 10월칸 비어 있음 = 미납
            self.assertEqual(c2.ledger(up_to_ym="2026-10")["total_unpaid"], 0)    # 2025-12 칸 '11.25'(이상한 날짜)도 금액이 있어 입금으로 센다
            self.assertIsNone(Tenant.get("tenant_ch_701"))                       # 주인세대는 임대 대상이 아님
        finally:
            use_pool_dir(keep)
            shutil.rmtree(work, ignore_errors=True)


    def test_48_bootstrap_from_config_only_when_empty(self):
        """48. RENT3_DEMO_SEED=0 + RENT3_BOOTSTRAP_CONFIG: 비어 있는 Data Pool 은 설정 파일로 한 번 채워지고, 이미 데이터가 있으면 덮어쓰지 않는다."""
        import tempfile
        from apps.rent_dist._rent3_core import Building, Lessor, ensure_pool_seeded, use_pool_dir
        work = tempfile.mkdtemp(prefix="bootstrap_")
        keep = self.temp_pool
        saved = {k: os.environ.get(k) for k in ("RENT3_DEMO_SEED", "RENT3_BOOTSTRAP_CONFIG")}
        try:
            cfg = {"lessors": [{"username": "bootlessor", "password": "Boot#Pass999", "name": "부트", "phone": "01055550009", "branch": "bt",
                                "building": {"name": "부트빌", "address": "x", "rooms": [{"room_no": "101", "deposit": 1000000, "monthly_rent": 100000}]}}]}
            cfgp = os.path.join(work, "boot.json")
            json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)
            os.environ["RENT3_DEMO_SEED"] = "0"
            os.environ["RENT3_BOOTSTRAP_CONFIG"] = cfgp
            use_pool_dir(os.path.join(work, "pool"))
            ensure_pool_seeded()
            self.assertEqual(Building.first(branch="bt").building_name, "부트빌")
            self.assertIsNotNone(Lessor.first(username="bootlessor"))
            self.assertIsNone(Lessor.first(username="chic"))                  # 시험용 시드는 만들지 않는다
            lessor = Lessor.first(username="bootlessor"); lessor.name = "수정됨"; lessor.save("t")
            ensure_pool_seeded()                                              # 두 번째 기동: 이미 있으므로 덮어쓰지 않는다
            self.assertEqual(Lessor.first(username="bootlessor").name, "수정됨")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            use_pool_dir(keep)
            shutil.rmtree(work, ignore_errors=True)

    def test_49_edit_payment_and_contract(self):
        """49. 수납 내역(납부일·월세·관리비)과 계약 내용(금액·만기·연락처·메모)을 고칠 수 있고, 다른 지점은 403."""
        c = self.client
        chic, sunho = self._login("chic"), self._login("sunho")
        dash = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).json()
        card = next(r for r in dash["rooms"] if r.get("pays"))
        self.assertTrue({"id", "paid_date", "billing_month", "rent", "fee", "total"} <= set(card["pays"][0]))
        pay = card["pays"][0]
        body = {"branch": "main", "pay_id": pay["id"], "paid_date": "2026-03-07", "rent_amount": 300000, "maintenance_amount": 20000}
        self.assertEqual(c.post("/api/rent3_lessor/payments/edit", headers=sunho, json=body).status_code, 403)
        r = c.post("/api/rent3_lessor/payments/edit", headers=chic, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(Pay.get(pay["id"]).total_amount, 320000 + Pay.get(pay["id"]).vat_amount)
        self.assertEqual(Pay.get(pay["id"]).paid_date, "2026-03-07")
        self.assertEqual(c.post("/api/rent3_lessor/payments/edit", headers=chic, json={**body, "paid_date": "2026-13-45"}).status_code, 400)
        ct = card["contract"]
        cbody = {"branch": "main", "contract_id": ct["id"], "deposit": 6000000, "monthly_rent": 310000, "maintenance_fee": 55000,
                 "move_in_date": ct["move_in_date"], "expiry_date": "2027-03-01", "tenant_phone": "010-1234-5678", "memo": "연락은 문자로"}
        self.assertEqual(c.post("/api/rent3_lessor/contracts/edit", headers=sunho, json=cbody).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/contracts/edit", headers=chic, json=cbody).status_code, 200)
        ct2 = Contract.get(ct["id"])
        self.assertEqual((ct2.deposit, ct2.monthly_rent, ct2.expiry_date, ct2.lessor_memo), (6000000, 310000, "2027-03-01", "연락은 문자로"))
        self.assertEqual(Room.get(ct2.id_room).monthly_rent, 310000)
        bb = {"branch": "main", "building_name": "시크빌2", "address": "인천 새주소 1"}
        self.assertEqual(c.post("/api/rent3_lessor/building/edit", headers=sunho, json=bb).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/building/edit", headers=chic, json=bb).status_code, 200)
        self.assertEqual(c.post("/api/rent3_lessor/building/edit", headers=chic, json={**bb, "building_name": " "}).status_code, 400)
        self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).json()["building"]["address"], "인천 새주소 1")
        # 부가세·종소세 자료 + 경비 등록·수정·삭제
        yr = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).json()["today"][:4]
        o = c.post("/api/rent3_lessor/outlays", headers=chic, json={"branch": "main", "category": "cleaning", "title": "202호 보일러 수리", "amount": 120000, "expense_date": f"{yr}-03-05"}).json()["outlay"]
        ts = c.get(f"/api/rent3_lessor/tax/summary?branch=main&year={yr}", headers=chic).json()
        self.assertEqual(len(ts["vat_periods"]), 2)
        self.assertTrue(any(x["id"] == o["id"] for x in ts["outlays"]))
        eb = {"branch": "main", "outlay_id": o["id"], "category": "cleaning", "title": "202호 보일러 교체", "amount": 150000, "vendor": "한빛설비", "expense_date": f"{yr}-03-06"}
        self.assertEqual(c.post("/api/rent3_lessor/outlays/edit", headers=sunho, json=eb).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/outlays/edit", headers=chic, json=eb).status_code, 200)
        self.assertEqual(c.post("/api/rent3_lessor/outlays/edit", headers=chic, json={**eb, "amount": 0}).status_code, 400)
        self.assertEqual(c.post("/api/rent3_lessor/outlays/delete", headers=sunho, json={"branch": "main", "outlay_id": o["id"]}).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/outlays/delete", headers=chic, json={"branch": "main", "outlay_id": o["id"]}).status_code, 200)
        self.assertFalse(any(x["id"] == o["id"] for x in c.get(f"/api/rent3_lessor/tax/summary?branch=main&year={yr}", headers=chic).json()["outlays"]))

    def test_50_vat_and_income_tax_rooms_tenant_biz_and_hometax_invoice_csv(self):
        """50. 시크빌 부가세(201~303호)·종소세(201~703호) 대상 호실 리스트 저장·편집, 과세 호실 임차인 사업자 정보 입력, 매입세액 공제(수리비 vs 경조사비 불공제), 세금계산서 발행 대장 및 홈택스 일괄발급 CSV 검증."""
        c = self.client
        chic, sunho = self._login("chic"), self._login("sunho")

        # 1) 시크빌 기본 과세·종소세 대상 호실 리스트 확인
        ts0 = c.get("/api/rent3_lessor/tax/summary?branch=main&year=2026", headers=chic).json()
        b_cfg = ts0["building_tax_config"]
        self.assertEqual(b_cfg["vat_rooms"], ["201", "202", "203", "301", "302", "303"])
        self.assertEqual(
            b_cfg["income_tax_rooms"],
            [
                "201", "202", "203",
                "301", "302", "303",
                "401", "402", "403",
                "501", "502", "503",
                "601", "602", "603",
                "701", "702", "703",
            ],
        )

        # 2) 다른 지점 임대인(sunho)은 시크빌 과세 호실 리스트나 사업자 정보를 수정할 수 없음 (403)
        self.assertEqual(
            c.post("/api/rent3_lessor/building/tax-rooms", headers=sunho, json={"branch": "main", "vat_rooms": ["201"]}).status_code,
            403,
        )
        self.assertEqual(
            c.post("/api/rent3_lessor/tax/tenant-biz", headers=sunho, json={"branch": "main", "room_no": "201", "business_number": "111-11-11111"}).status_code,
            403,
        )

        # 3) 건물 과세·종소세 호실 리스트 편집 및 저장 검증 (따옴표가 섞여 들어와도 자동 제거)
        r_upd = c.post(
            "/api/rent3_lessor/building/tax-rooms",
            headers=chic,
            json={
                "branch": "main",
                "vat_rooms": '"201", "202", "203", "301", "302", "303"',
                "income_tax_rooms": "201, 202, 203, 301, 302, 303, 401, 402, 403, 501, 502, 503, 601, 602, 603, 701, 702, 703",
                "biz_company_name": "시크빌",
                "sync_contracts": False,
            },
        )
        self.assertEqual(r_upd.status_code, 200, r_upd.text)
        bldg_disk = self._read_disk_json("buildings")
        b_main = next(b for b in bldg_disk["items"] if b["branch"] == "main")
        self.assertEqual(b_main["vat_rooms"], ["201", "202", "203", "301", "302", "303"])
        self.assertEqual(len(b_main["income_tax_rooms"]), 18)

        # 4) 계약 시(또는 계약 수정 시) 세입자가 세금계산서 발행을 요구한 호실만 사업자 정보 입력 (주거용 호실 기숙사 사용 포함)
        r_tbiz = c.post(
            "/api/rent3_lessor/tax/tenant-biz",
            headers=chic,
            json={
                "branch": "main",
                "room_no": "201",
                "business_number": "214-86-99887",
                "company_name": "(주)시크디자인연구소",
                "ceo_name": "이지훈",
                "email": "tax@chicdesign.co.kr",
                "vat_target": True,
                "tax_invoice_requested": True,
            },
        )
        self.assertEqual(r_tbiz.status_code, 200, r_tbiz.text)
        self.assertEqual(r_tbiz.json()["contract"]["tenant_business_number"], "214-86-99887")
        self.assertEqual(r_tbiz.json()["contract"]["tenant_company_name"], "(주)시크디자인연구소")
        self.assertTrue(r_tbiz.json()["contract"]["tax_invoice_requested"])
        self.assertEqual(r_tbiz.json()["tenant"]["business_number"], "214-86-99887")

        # 주거용 호실(401호)을 등록하고 법인 기숙사로 계약할 때 세입자가 세금계산서 발행을 요청한 경우
        c.post(
            "/api/rent3_lessor/rooms",
            headers=chic,
            json={
                "branch": "main",
                "room_no": "401",
                "floor": 4,
                "room_category": "주거용 원룸",
                "vat_applicable": False,
                "deposit": 5000000,
                "monthly_rent": 450000,
                "maintenance_fee": 50000,
            },
        )
        r_dorm = c.post(
            "/api/rent3_lessor/contracts/broker-photo",
            headers=chic,
            json={
                "branch": "main",
                "room_no": "401",
                "photo_data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                "photo_filename": "dorm_401.png",
                "tenant_name": "김민지",
                "tenant_phone": "010-5555-7777",
                "lease_type": "월세",
                "pay_timing": "후불",
                "deposit": 5000000,
                "monthly_rent": 450000,
                "maintenance_fee": 50000,
                "contract_deposit": 500000,
                "deposit_received": True,
                "contract_date": "2026-10-01",
                "move_in_date": "2026-10-01",
                "lease_term_months": 12,
                "tax_invoice_requested": True,
                "tenant_business_number": "120-81-55555",
                "tenant_company_name": "(주)한빛기숙사",
                "tenant_ceo_name": "김민지",
                "tenant_email": "dorm@hanbit.co.kr",
            },
        )
        self.assertEqual(r_dorm.status_code, 200, r_dorm.text)
        self.assertTrue(r_dorm.json()["contract"]["tax_invoice_requested"])

        # 5) 경비 지출: 수리비(110,000원 -> 매입세액 10,000원 공제) vs 경조사비(100,000원 -> 매입세액 0원 불공제, 종소세만 반영)
        r_rep = c.post(
            "/api/rent3_lessor/outlays",
            headers=chic,
            json={
                "branch": "main",
                "category": "repair_maint",
                "title": "301호 도어락 및 수전 교체",
                "amount": 110000,
                "vendor": "관악설비",
                "expense_date": "2026-10-15",
            },
        ).json()["outlay"]
        self.assertTrue(r_rep["is_vat_deductible"])
        self.assertEqual(r_rep["input_vat"], 10000)

        r_cong = c.post(
            "/api/rent3_lessor/outlays",
            headers=chic,
            json={
                "branch": "main",
                "category": "congrat_condol",
                "title": "입주사 대표 경조사 화환",
                "amount": 100000,
                "vendor": "사랑플라워",
                "expense_date": "2026-10-16",
            },
        ).json()["outlay"]
        self.assertFalse(r_cong["is_vat_deductible"])
        self.assertEqual(r_cong["input_vat"], 0)

        # 6) 세금계산서 발행 대장 조회(공실/미요청 호실은 제외되고 요청한 호실만 포함) 및 홈택스 일괄발급 CSV 다운로드 검증
        reg = c.get("/api/rent3_lessor/tax/invoice-register?branch=main&year=2026&month=10", headers=chic).json()
        self.assertTrue(reg["success"])
        vc_rooms = [v["room_no"] for v in reg["vat_room_contracts"]]
        self.assertIn("201", vc_rooms)
        self.assertIn("401", vc_rooms)
        self.assertNotIn("202", vc_rooms)  # 세금계산서 발행을 요청하지 않은 호실은 제외
        self.assertNotIn("203", vc_rooms)  # 공실 호실도 제외
        vc_201 = next(v for v in reg["vat_room_contracts"] if v["room_no"] == "201")
        self.assertTrue(vc_201["ready_for_invoice"])
        self.assertEqual(vc_201["company_name"], "(주)시크디자인연구소")
        self.assertGreaterEqual(reg["totals"]["count"], 1)
        inv_201 = next(i for i in reg["invoices"] if i["room_no"] == "201")
        self.assertTrue(inv_201["ready_for_hometax"])
        self.assertEqual(inv_201["buyer_business_number_digits"], "2148699887")
        self.assertEqual(inv_201["buyer_email"], "tax@chicdesign.co.kr")

        r_csv = c.get("/api/rent3_lessor/tax/export-hometax-csv?branch=main&year=2026&month=10", headers=chic)
        self.assertEqual(r_csv.status_code, 200)
        self.assertIn("text/csv", r_csv.headers["content-type"])
        csv_text = r_csv.content.decode("utf-8-sig")
        self.assertIn("전자세금계산서종류(01:일반)", csv_text)
        self.assertIn("2148699887", csv_text)
        self.assertIn("(주)시크디자인연구소", csv_text)
        self.assertIn("tax@chicdesign.co.kr", csv_text)

        # 7) 부가세·종소세 호실별 산출내역 및 납부 상세내역 검증
        ts_after = c.get("/api/rent3_lessor/tax/summary?branch=main&year=2026", headers=chic).json()["summary"]
        self.assertTrue(len(ts_after["vat_room_breakdown"]) >= 1)
        self.assertTrue(len(ts_after["income_tax_room_breakdown"]) >= 1)
        self.assertTrue(len(ts_after["vat_payment_rows"]) >= 1)
        self.assertTrue(len(ts_after["income_tax_payment_rows"]) >= 1)
        r_tax_csv = c.get("/api/rent3_lessor/tax/export-csv?branch=main&year=2026", headers=chic)
        self.assertEqual(r_tax_csv.status_code, 200)
        self.assertIn("[호실별 임대수입 산출내역 (종합소득세 신고대상)]", r_tax_csv.content.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
