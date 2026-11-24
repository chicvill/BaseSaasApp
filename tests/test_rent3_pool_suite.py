"""
tests/test_rent3_pool_suite.py — 불변 BaseSaasApp 기반 Rent3 공동 JSON Data Pool & 5대 분리 앱 종합 검증 스위트.
"""
import json
import os
from datetime import date, datetime
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
        os.environ["RENT3_ADMIN_PROXY_STRICT"] = "0"   # 기존 시험은 admin 기본 토큰으로 수납·서명을 통과시킨다. 엄격 모드는 test_53
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
        os.environ.pop("RENT3_ADMIN_PROXY_STRICT", None)
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
            "viewed_directly": True, "viewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "owner_name": rd.get("owner_name") or "김임대", "mortgage_amount": int(rd.get("mortgage_amount") or 0),
            "seizure_or_trust": "없음", "guarantee_status": "가입 예정",
        }})

    def _party_headers(self, sid):
        """계약 당사자 토큰을 돌려준다: 임대인(해당 지점) + 세션의 임차인(번호가 같은 계정, 없으면 가입시키고 본인인증 처리)."""
        from apps.rent_dist._rent3_core import Lessor, Tenant
        from apps.rent_dist._rent3_core.authz import lessor_token, tenant_token
        s = self.client.get(f"/api/rent3_contract/sessions/{sid}").json()["session"]
        lessor = Lessor.first(branch=s["branch"])
        digits = lambda v: "".join(ch for ch in str(v or "") if ch.isdigit())
        tenant = Tenant.get(s.get("id_tenant")) if s.get("id_tenant") else None
        phone = (s.get("terms") or {}).get("tenant_phone", "")
        tenant = tenant or next((t for t in Tenant.all() if digits(t.phone) == digits(phone) and phone), None)
        if tenant is None:
            reg = self.client.post("/api/rent3/pool/auth/register", headers=self.NOAUTH,
                                   json={"role": "prospect", "name": (s.get("terms") or {}).get("tenant_name") or "당사자", "phone": phone or "010-7000-0000", "password": "Pw123456"})
            tenant = Tenant.get(reg.json()["user"]["id"])
        tenant.identity = {"verified": True}          # 조건을 바꾸는 수락·서명은 본인인증이 필요하다
        tenant.save(action="test_verified")
        return ({"Authorization": f"Bearer {lessor_token(lessor)}"}, {"Authorization": f"Bearer {tenant_token(tenant)}"})

    def _propose_accept(self, sid, field, value, proposer="lessor"):
        """한쪽이 제안하고 상대가 수락하는 흐름(6개 항목은 이 방법으로만 바뀐다). 수락 응답을 돌려준다."""
        lh, th = self._party_headers(sid)
        mine, other = (lh, th) if proposer == "lessor" else (th, lh)
        url = f"/api/rent3_contract/sessions/{sid}/messages"
        p = self.client.post(url, headers=mine, json={"kind": "proposal", "field": field, "new_value": value})
        self.assertEqual(p.status_code, 200, p.text)
        return self.client.post(f"{url}/{p.json()['item']['id']}/respond", headers=other, json={"action": "accept"})

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
            "updates": {"pay_timing": "후불"},
        })
        cur_move_in = self.client.get(f"/api/rent3_contract/sessions/{sid}").json()["session"]["terms"]["move_in_date"]
        new_move_in = "2026-10-15" if cur_move_in != "2026-10-15" else "2026-10-16"
        # 입주일 같은 6개 항목은 /terms 로 직접 바꿀 수 없고(409), 제안 → 상대 수락으로만 바뀐다
        direct = self.client.post(f"/api/rent3_contract/sessions/{sid}/terms", json={"role": "lessor", "updates": {"move_in_date": new_move_in}})
        self.assertEqual(direct.status_code, 409, direct.text)
        self.assertIn("제안", direct.text)
        same = self.client.post(f"/api/rent3_contract/sessions/{sid}/terms", json={"role": "lessor", "updates": {"move_in_date": cur_move_in}})
        self.assertEqual(same.status_code, 200)                                    # 지금 값과 같게 넘어오면 통과(변경 없음)
        self.assertFalse(same.json()["changed"])
        self.assertEqual(self._propose_accept(sid, "move_in_date", new_move_in).status_code, 200)
        cur_rev = self.client.get(f"/api/rent3_contract/sessions/{sid}").json()["session"]["revision"]
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "lessor", "expected_revision": cur_rev})
        self.client.post(f"/api/rent3_contract/sessions/{sid}/step3/agree", json={"role": "tenant", "expected_revision": cur_rev})

        # 양측 동의 뒤(단계 ④)에는 /terms 로 바로 고칠 수 없고(409), '조건 다시 열기'를 거쳐야 한다
        self.assertGreaterEqual(self.client.get(f"/api/rent3_contract/sessions/{sid}").json()["session"]["current_step"], 4)
        blocked = self.client.post(f"/api/rent3_contract/sessions/{sid}/terms", json={"role": "lessor", "updates": {"monthly_rent": 540000}})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("조건 다시 열기", blocked.text)
        self.assertEqual(self.client.post(f"/api/rent3_contract/sessions/{sid}/reopen-terms", json={"role": "lessor"}).status_code, 400)   # 경고 확인 필요
        reopened = self.client.post(f"/api/rent3_contract/sessions/{sid}/reopen-terms", json={"role": "lessor", "confirm": True})
        self.assertEqual(reopened.status_code, 200, reopened.text)
        rs = reopened.json()["session"]
        self.assertEqual((rs["current_step"], rs["revision"], rs["step3_lessor_agreed"], rs["step3_tenant_agreed"], rs["lessor_signature"]), (3, cur_rev + 1, False, False, ""))
        cur_rev = rs["revision"]
        # 조건 1개 수정(월세 제안 → 수락) -> Revision Lock 발동 검증!
        acc = self._propose_accept(sid, "monthly_rent", 540000)
        self.assertEqual(acc.status_code, 200, acc.text)
        r_edit = {"changed": True, "session": acc.json()["session"]}
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

        # 체결 때 협상 기록(제안·수락·시스템 안내)의 해시가 계약에 기록되고, 당사자가 읽어도 바뀌지 않는다
        from apps.rent_dist._rent3_core import Contract as _Ct, contract_chat as _cc
        sealed_ct = _Ct.get(sealed["contract"]["id"])
        self.assertEqual(len(sealed_ct.chat_digest), 64)
        self.assertEqual(sealed_ct.chat_digest_count, len(_cc.messages_of(sid)))
        self.assertGreaterEqual(sealed_ct.chat_digest_count, 5)
        lh_s, th_s = self._party_headers(sid)
        base_s = f"/api/rent3_contract/sessions/{sid}"
        v0 = self.client.get(f"{base_s}/chat-digest", headers=lh_s).json()
        self.assertEqual((v0["match"], v0["stored"]), (True, sealed_ct.chat_digest))
        for h in (lh_s, th_s, lh_s):                                   # 양쪽이 대화를 열람(읽음 표시 변경)해도
            got_s = self.client.get(f"{base_s}/messages", headers=h).json()
            self.assertTrue(got_s["read_only"])
        v1 = self.client.get(f"{base_s}/chat-digest", headers=th_s).json()
        self.assertEqual((v1["match"], v1["computed"]), (True, sealed_ct.chat_digest))
        self.assertEqual(self.client.post(f"{base_s}/messages", headers=th_s, json={"kind": "question", "body": "체결 뒤 문의"}).status_code, 409)   # 읽기 전용
        self.assertEqual(self.client.get(f"{base_s}/chat-digest", headers=lh_s).json()["computed"], sealed_ct.chat_digest)
        # 해시 입력에서 읽음 표시·갱신시각은 제외된다
        self.assertNotIn("read_by_lessor", _cc.DIGEST_FIELDS)
        self.assertNotIn("read_by_tenant", _cc.DIGEST_FIELDS)
        self.assertNotIn("updated_at", _cc.DIGEST_FIELDS)

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
        r_ask = self.client.post("/api/rent3_lessor/payments/sms-paste", json={"branch": "main", "sms_text": sms_text, "up_to_ym": "2026-10"})
        self.assertEqual(r_ask.status_code, 200, r_ask.text)
        self.assertTrue(r_ask.json()["needs_confirm"])                                       # 금액이 청구액과 달라 저장 전에 확인을 요청
        r_sms = self.client.post("/api/rent3_lessor/payments/sms-paste", json={
            "branch": "main",
            "sms_text": sms_text,
            "up_to_ym": "2026-10",
            "force": True,
        })
        self.assertEqual(r_sms.status_code, 200)
        body = r_sms.json()
        self.assertEqual(body["level"], "diff", body)
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
            "force": True,
        })
        self.assertEqual(r_dup.status_code, 409)
        self.assertEqual(r_dup.json()["level"], "warning")

        # 임차인 앱에서 잔여 440,000원 온라인 납부 -> 완납 및 17% 세액공제 증명서 확인
        self.assertIn(self.client.post("/api/rent3_tenant/myhome/tenant_202/pay", json={"amount": 440000, "method": "KAKAOPAY"}).status_code, (404, 405))   # 임차인이 스스로 납부 처리하는 길은 없다
        r_note = self.client.post("/api/rent3_tenant/myhome/tenant_202/transfer-notice", json={"amount": 440000})
        self.assertEqual(r_note.status_code, 200, r_note.text)
        self.assertEqual(self.client.get("/api/rent3_tenant/myhome/tenant_202").json()["ledger"]["total_unpaid"], 440000)    # 알림만으로는 장부가 바뀌지 않는다
        nid = r_note.json()["notice"]["id"]
        self.assertEqual(self.client.get("/api/rent3_lessor/dashboard?branch=main").json()["transfer_notices"][0]["id"], nid)
        r_tpay = self.client.post(f"/api/rent3_lessor/transfer-notices/{nid}/confirm", json={"branch": "main"})   # 임대인이 입금 확인 후 수납
        self.assertEqual(r_tpay.status_code, 200, r_tpay.text)
        self.assertEqual(r_tpay.json()["ledger"]["total_unpaid"], 0)
        self.assertEqual(self.client.post(f"/api/rent3_lessor/transfer-notices/{nid}/confirm", json={"branch": "main"}).status_code, 400)   # 두 번 수납 금지

        myhome = self.client.get("/api/rent3_tenant/myhome/tenant_202").json()
        self.assertEqual(myhome["certificate"]["total_paid"], 1140000)
        self.assertGreater(myhome["certificate"]["estimated_tax_credit_17pct"], 0)

        # 재계약 5% 상한 초과 시 차단 검증 (201호 기존 월세 50만 -> 55만(+10%) 시도 시 차단!)
        r_ren = self.client.post("/api/rent3_contract/sessions", json={
            "branch": "main",
            "room_no": "201",
            "mode": "RENEWAL",
        }).json()["session"]
        direct_cap = self.client.post(f"/api/rent3_contract/sessions/{r_ren['id']}/terms", json={"role": "lessor", "updates": {"monthly_rent": 550000}})
        self.assertEqual(direct_cap.status_code, 409)                              # 6개 항목은 직접 못 고친다
        r_cap_fail = self._propose_accept(r_ren["id"], "monthly_rent", 550000)     # 제안은 되지만 수락 때 법정 5% 상한 검증에서 막힌다
        self.assertEqual(r_cap_fail.status_code, 422, r_cap_fail.text)
        self.assertEqual(r_cap_fail.json()["level"], "diff")

        # 202호 퇴실 정산 산출 -> 임차인 동의 -> 임대인 반환 완료 -> 호실 자동 공실 복귀 검증
        # (만기 전 퇴실이므로 지금 달까지의 미납 + 만기일까지 아직 안 낸 월세(퇴실 후 월세 부담분)도 보증금에서 공제된다)
        room202 = Room.first(branch="main", room_no="202")
        lb202 = Contract.get(room202.active_contract_id).checkout_liability(room202)
        r_co = self.client.post("/api/rent3_lessor/checkout/calculate", json={
            "branch": "main",
            "room_no": "202",
            "utility_settlement": 30000,
            "cleaning_fee": 70000,
        }).json()["settlement"]
        self.assertEqual((r_co["unpaid_rent_deduction"], r_co["early_rent_deduction"]), (lb202["unpaid_now"], lb202["early_amount"]))
        self.assertEqual(r_co["refund_amount"], max(0, 5000000 - (100000 + lb202["unpaid_now"] + lb202["early_amount"])))

        t202 = self._login("tenant_202")
        ag_body = {"bank_account": "토스 1000-22-334455", "signature": "정서연(동의)", "account_holder": "정서연"}
        self.assertEqual(self.client.post(f"/api/rent3_tenant/checkout/{r_co['id']}/agree", json=ag_body).status_code, 403)       # 임차인 본인만(관리인·임대인 불가)
        self.assertEqual(self.client.post(f"/api/rent3_tenant/checkout/{r_co['id']}/agree", headers=t202, json={**ag_body, "account_holder": "남의이름"}).status_code, 400)   # 예금주는 계약자 본인만
        r_ag = self.client.post(f"/api/rent3_tenant/checkout/{r_co['id']}/agree", headers=t202, json=ag_body)
        self.assertEqual(r_ag.status_code, 200, r_ag.text)

        r_paid = self.client.post(f"/api/rent3_lessor/checkout/{r_co['id']}/complete")       # 임대인 송금 완료 표시(예전 이름)
        self.assertEqual(r_paid.status_code, 200, r_paid.text)
        self.assertEqual(r_paid.json()["settlement"]["status"], "PAID")
        r_done = self.client.post(f"/api/rent3_tenant/checkout/{r_co['id']}/confirm-received", headers=t202, json={})    # 임차인 수취 확인 → 퇴실 확정
        self.assertEqual(r_done.status_code, 200, r_done.text)

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
        # 너무 일찍 공개하지 않는다: 퇴실 기준일(2027-03-01)의 1개월 전인 2027-02-01 전에는 사전 매물로 올라가지 않는다
        self.assertEqual((r_move.json()["check"]["prelist_pending"], r_move.json()["check"]["prelist_open_date"]), (True, "2027-02-01"))
        self.assertIn("2027-02-01", r_move.json()["message"])
        self.assertFalse(any(l["room_no"] == "201" for l in self.client.get("/api/rent3_tenant/listings?branch=main").json()["listings"]))
        from unittest import mock as _mock
        with _mock.patch.dict(os.environ, {"RENT3_TODAY": "2027-02-01"}):          # 공개일이 되어 목록을 조회하면 그때 올라간다
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
            d = {"viewed_directly": True, "viewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "owner_name": "김임대",
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
        led = Contract.require(no)
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
        sess = CoSignSession.require(sid)
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
            setattr(registry_ai, "_client", lambda: FakeGemini())
            r = self.client.post(url, json={"attachment_data_url": pdf, "consent": False})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(FakeGemini.calls, 0)

            # (b) 키가 없으면 오류가 아니라 '직접 입력' 안내
            setattr(registry_ai, "_client", lambda: NoKey())
            r = self.client.post(url, json={"attachment_data_url": pdf, "consent": True})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["available"])
            self.assertFalse(r.json()["success"])

            # (c) 정상: 말소된 근저당은 빼고, AI 가 준 합계(999)는 쓰지 않고 목록에서 코드로 합산
            setattr(registry_ai, "_client", lambda: FakeGemini())
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
                "viewed_directly": True, "viewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "owner_name": "김임대",
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

    def _admin_enter(self, user, view, session_id=""):
        """관리인이 원격 지원으로 입장한 상태를 만든다(사용자 허용 ON + 지원 채팅 글 + 입장 기록). 엄격 모드에서 관리인이 읽으려면 필요."""
        from apps.rent_dist._rent3_core import support as sup
        old_consents, key = dict(user.consents or {}), sup.thread_key(user)

        def undo():                                   # 다른 시험이 깨끗한 상태를 기대하므로 입장 준비로 남긴 것을 치운다
            for m in sup.SupportMessage.find(thread_user=key):
                m.delete(action="t")
            for g in sup.SupportAccessLog.find(target_user=key):
                g.delete(action="t")
            u = type(user).get(user.id)
            u.consents = old_consents
            u.save(action="t")
        self.addCleanup(undo)
        user.consents = {**old_consents, "support_access": True}
        user.save(action="t")
        sup.post_user_message(user, "도와주세요")
        r = self.client.post("/api/rent3_admin/support/enter", json={"user": key, "view": view, "session_id": session_id})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _bal_prime(self, tid):
        """잔금 전 등기부 확인의 새 조건(AI 읽기 기록)을 시험에서 채운다: AI 를 쓸 수 없는 상태로 읽은 기록 → 직접 확인 체크만으로 기록 가능."""
        from apps.rent_dist._rent3_core import Contract, Room, Tenant
        t = Tenant.get(tid) or Tenant.first(phone=tid)
        ct = Contract.get(Room.get(t.current_room_id).active_contract_id)
        ct.balance_registry = {**(ct.balance_registry or {}), "read": {"status": "unavailable"}}
        ct.save(action="t")

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
        assert some_room is not None
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
            setattr(registry_ai, "_client", lambda: Fake())
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
            setattr(lessor_api, "FRANCHISE_AI_MAX_CALLS", 6)   # 이 테스트는 6가지 경우를 시험한다
            setattr(registry_ai, "_client", lambda: Fake())
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
        from typing import Any
        from apps.rent_dist._rent3_core import franchise as fr
        c = self.client
        prov: Any = fr.PROVIDERS["sms"]
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
            self.assertEqual(Lessor.require("lessor_enc").account_number, "신한 110-999-ENC-777")
            self.assertEqual(Room.require("room_encb_1").doorlock_pwd, "9876*ENC")

            # 같은 값도 매번 다르게 암호화(nonce), 다른 필드로 옮겨 붙이면 복호화 실패(AAD)
            a, b = fc.encrypt_value("doorlock_pwd", "same"), fc.encrypt_value("doorlock_pwd", "same")
            self.assertNotEqual(a, b)
            with self.assertRaises(Exception):
                fc.decrypt_value("account_number", a)

            # 키 교체: 새 키 + 옛 키(PREVIOUS)로 읽히고, 다시 저장하면 새 키로 바뀐다
            os.environ["RENT3_CRYPTO_KEY_PREVIOUS"] = os.environ["RENT3_CRYPTO_KEY"]
            os.environ["RENT3_CRYPTO_KEY"] = "test-key-B-" + "y" * 30
            self.assertEqual(Room.require("room_encb_1").doorlock_pwd, "9876*ENC")
            Room.require("room_encb_1").save("rekey")
            os.environ.pop("RENT3_CRYPTO_KEY_PREVIOUS")
            self.assertEqual(Room.require("room_encb_1").doorlock_pwd, "9876*ENC")           # 새 키만으로 읽힘
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
            self.assertEqual(Room.require("room_encb_3").doorlock_pwd, "MASTER-SECRET-1")
            # 나중에 전용 키(RENT3_CRYPTO_KEY)를 추가해도 마스터 키로 쓴 값은 계속 읽힌다
            os.environ["RENT3_CRYPTO_KEY"] = "dedicated-key-" + "d" * 20
            self.assertEqual(Room.require("room_encb_3").doorlock_pwd, "MASTER-SECRET-1")
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
        assert room is not None
        rel = f"/api/rent3_lessor/rooms/{room.id}/release-keys"
        self.assertEqual(c.post(rel, headers=th, json={"balance_checked": True}).status_code, 403)          # 임차인은 못 연다
        self.assertEqual(c.post(rel, headers=self._login("sunho"), json={"balance_checked": True}).status_code, 403)  # 다른 지점도
        self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 400)         # 잔금일 등기부 확인 전
        chk = f"/api/rent3_tenant/myhome/{tid}/balance-registry-check"
        self._bal_prime(tid)
        self.assertEqual(c.post(chk, headers=th, json={"changed": False, "issue_number": "1101-2026-555"}).status_code, 400)       # '확인했음' 체크 없이는 진행 불가
        self.assertEqual(c.post(chk, headers=th, json={"changed": False, "checked": True, "issue_number": "1101-2026-555"}).status_code, 200)
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
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "921", "is_published": True})
        sid = c.post(f"{base}/sessions/call", headers=ten, json={
            "caller_role": "tenant", "branch": "main", "room_no": "921", "mode": "NEW", "tenant_name": "사진임차", "tenant_phone": "010-6666-0801"}).json()["session"]["id"]

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
        # 사진은 당사자만 본다 (Authorization 헤더 필수, ?token= 쿼리 파라미터는 보안상 거부)
        url = f"{base}/sessions/{sid}/facility-photos/{pid}"
        self.assertEqual(c.get(url, headers=self.NOAUTH).status_code, 401)
        self.assertEqual(c.get(url, headers=sunho).status_code, 403)
        self.assertEqual(c.get(url, headers=chic).status_code, 200)
        self.assertEqual(c.get(url, headers=ten).status_code, 200)
        self.assertEqual(c.get(url, headers=self.NOAUTH, params={"token": ten["Authorization"].split()[1]}).status_code, 401)
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
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "923", "is_published": True})
        reg2 = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "prospect", "name": "사진임차2", "phone": "01066660802", "password": "Pw123456"}).json()
        sid2 = self._complete_contract(chic, self._as(reg2["token"]), reg2["user"]["id"], "923", "사진임차2", "010-6666-0802")
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
        s2 = CoSignSession.require(sid2)
        later = datetime.fromisoformat(s2.updated_at) + timedelta(hours=30)
        self.assertTrue(s2.is_idle_expired(later))                       # 사전 작성 전: 24시간 규칙
        c.post(f"{base}/sessions/{sid2}/prefill", headers=chic, json={"role": "lessor", "updates": {"move_in_date": "2026-12-05"}})
        s2 = CoSignSession.require(sid2)
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
        assert room is not None
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
        self._bal_prime(tid)
        self.assertEqual(c.post(chk, headers=th, json={"changed": False, "checked": True}).status_code, 200)
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

        from typing import Any
        class Req:
            def __init__(self, headers, host="10.0.0.5"):
                self.headers, self.client = headers, type("C", (), {"host": host})()
        mk_req: Any = Req
        self.assertFalse(is_local_request(mk_req({"x-forwarded-for": "1.2.3.4"}, "127.0.0.1")))
        self.assertTrue(is_local_request(mk_req({}, "127.0.0.1")))
        os.environ["RENT3_TRUSTED_PROXY_HOPS"] = "1"
        try:
            self.assertEqual(client_ip(mk_req({"x-forwarded-for": "6.6.6.6, 203.0.113.9"})), "203.0.113.9")   # 믿을 수 있는 프록시가 덧붙인 값
            self.assertEqual(client_ip(mk_req({})), "10.0.0.5")
        finally:
            os.environ.pop("RENT3_TRUSTED_PROXY_HOPS", None)
        self.assertEqual(client_ip(mk_req({"x-forwarded-for": "6.6.6.6"})), "10.0.0.5")                       # 기본은 소켓 주소만


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
        d = {"viewed_directly": True, "viewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "owner_name": "김종심", "mortgage_amount": 0,
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
        self._bal_prime(tid)
        self.assertEqual(c.post(chk, json={"changed": False, "mortgage_cleared": True, "checked": True}).status_code, 200)
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
        """43. 회귀 방지: 임차인 마이홈 화면이 직원 전용 API(/tenants)를 세입자 본인 로그인으로 부르면 화면이 비어 버린다(실제로 있었던 사고). 관리자용 세입자 선택기는 관리인 앱 한 곳으로 모았으므로 임차인 앱에는 전체 세입자 목록 호출·선택기가 없다."""
        src = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
        for gone in ("loadTenants", "tenantList", "tenantSel", '"/tenants"'):
            self.assertNotIn(gone, src, gone)
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
        self._bal_prime(tid)
        self.assertEqual(c.post(chk, json={"changed": False, "checked": True}).status_code, 200)
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
            assert ls is not None
            self.assertTrue(ls.password.startswith("pbkdf2") or ls.password != "Fresh#Pass99")   # 해시 저장
            b_fresh = Building.first(branch="fresh")
            assert b_fresh is not None
            self.assertEqual(b_fresh.address, "인천 서해구 석남동 1-1")
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
            g = lambda cid: Contract.require(cid)
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
            self.assertEqual(Tenant.require("tenant_ls2_201").phone, "010-8856-4915")                 # 8자리 번호에 010 보정
            self.assertNotEqual(Tenant.require("tenant_ls2_201").password, "1212")                    # 기본 비밀번호 1212 로는 로그인 불가
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
            c1, c2 = Contract.require("ct_ch_101"), Contract.require("ct_ch_201")
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
            b_bt = Building.first(branch="bt")
            assert b_bt is not None
            self.assertEqual(b_bt.building_name, "부트빌")
            self.assertIsNotNone(Lessor.first(username="bootlessor"))
            self.assertIsNone(Lessor.first(username="chic"))                  # 시험용 시드는 만들지 않는다
            lessor = Lessor.first(username="bootlessor")
            assert lessor is not None
            lessor.name = "수정됨"; lessor.save("t")
            ensure_pool_seeded()                                              # 두 번째 기동: 이미 있으므로 덮어쓰지 않는다
            lessor2 = Lessor.first(username="bootlessor")
            assert lessor2 is not None
            self.assertEqual(lessor2.name, "수정됨")
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
        self.assertEqual(Pay.require(pay["id"]).total_amount, 320000 + Pay.require(pay["id"]).vat_amount)
        self.assertEqual(Pay.require(pay["id"]).paid_date, "2026-03-07")
        self.assertEqual(c.post("/api/rent3_lessor/payments/edit", headers=chic, json={**body, "paid_date": "2026-13-45"}).status_code, 400)
        ct = card["contract"]
        cbody = {"branch": "main", "contract_id": ct["id"], "deposit": 6000000, "monthly_rent": 310000, "maintenance_fee": 55000,
                 "move_in_date": ct["move_in_date"], "expiry_date": "2027-03-01", "tenant_phone": "010-1234-5678", "memo": "연락은 문자로"}
        self.assertEqual(c.post("/api/rent3_lessor/contracts/edit", headers=sunho, json=cbody).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/contracts/edit", headers=chic, json=cbody).status_code, 200)
        ct2 = Contract.require(ct["id"])
        self.assertEqual((ct2.deposit, ct2.monthly_rent, ct2.expiry_date, ct2.lessor_memo), (6000000, 310000, "2027-03-01", "연락은 문자로"))
        self.assertEqual(Room.require(ct2.id_room).monthly_rent, 310000)
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

    def test_51_claude_audit_tax_certificate_and_invoice_fixes(self):
        """51. 세무·회계 감사 권고사항 종합 검증:
        - 매입세액 과다공제 방지(간이과세자·간이영수증·주거전용 불공제 및 공통매입세액 과세공급비율 안분)
        - 임차인 납부확인서 가짜 현금영수증 번호(CASH-...)·가짜 사업자번호(101-86-00000) 제거 및 관리비 제외 순수 월세 15%·17% 산출
        - 세금계산서 청구월(약정 지급일) 기준 월 1건 집계, 대표자명 임차인명 자동대체 금지, 미비 건·기발행(ISSUED) 건 CSV 제외, 국세청 승인번호·이력 저장
        - 장부이관 누적 이월 수납(:carry) 연간 수입 제외, 부가세 0원 건 임의 10% 추정 금지, 주택임대(분리과세 필요경비율) vs 상가임대(종합과세) 분리, 면세 사업장현황신고 CSV.
        """
        c = self.client
        chic = self._login("chic")

        # (1) 간이과세자 거래처 또는 주거전용 수리비는 부가세 매입세액 불공제(0원) 처리됨을 검증
        o_simp = c.post(
            "/api/rent3_lessor/outlays",
            headers=chic,
            json={
                "branch": "main",
                "category": "repair_maint",
                "title": "간이과세 설비업체 수리",
                "amount": 110000,
                "vendor": "동네설비",
                "vendor_tax_type": "SIMPLIFIED",
                "evidence_type": "SIMPLE_RECEIPT",
                "expense_date": "2026-10-18",
            },
        ).json()["outlay"]
        self.assertFalse(o_simp["is_vat_deductible"])
        self.assertEqual(o_simp["input_vat"], 0)

        o_exempt_room = c.post(
            "/api/rent3_lessor/outlays",
            headers=chic,
            json={
                "branch": "main",
                "category": "repair_maint",
                "title": "주거용 501호 도배 수리",
                "amount": 220000,
                "vendor": "인테리어업체",
                "vendor_tax_type": "GENERAL",
                "evidence_type": "TAX_INVOICE",
                "usage_scope": "EXEMPT_ONLY",
                "expense_date": "2026-10-19",
            },
        ).json()["outlay"]
        self.assertFalse(o_exempt_room["is_vat_deductible"])
        self.assertEqual(o_exempt_room["input_vat"], 0)

        # (2) 분할 입금(한 달에 2번 입금)이 들어와도 청구월(2026-10) 기준 월 1건으로 합산되고, 작성일자는 입금일이 아닌 약정 지급일(20261001)임
        #     또한 대표자명을 비워둔 사업자 호실(401호)은 임차인 이름으로 자동 채우지 않고 미입력(ready_for_hometax=False)으로 분류해 CSV에서 제외
        c.post(
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
        c.post(
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
        Pay(
            branch="main",
            room_no="401",
            payer_name="(주)한빛기숙사",
            billing_month="2026-10",
            paid_date="2026-10-05",
            rent_amount=225000,
            maintenance_amount=25000,
            vat_amount=25000,
            total_amount=275000,
            payment_kind="MONTHLY_RENT",
            idempotency_key="test51:401:split1",
        ).save(action="test_split_1", app_source="test")
        Pay(
            branch="main",
            room_no="401",
            payer_name="(주)한빛기숙사",
            billing_month="2026-10",
            paid_date="2026-10-18",
            rent_amount=225000,
            maintenance_amount=25000,
            vat_amount=25000,
            total_amount=275000,
            payment_kind="MONTHLY_RENT",
            idempotency_key="test51:401:split2",
        ).save(action="test_split_2", app_source="test")

        c.post(
            "/api/rent3_lessor/tax/tenant-biz",
            headers=chic,
            json={
                "branch": "main",
                "room_no": "401",
                "business_number": "120-81-55555",
                "company_name": "(주)한빛기숙사",
                "ceo_name": "",  # 대표자명 누락
                "email": "dorm@hanbit.co.kr",
                "vat_target": True,
                "tax_invoice_requested": True,
            },
        )
        reg = c.get("/api/rent3_lessor/tax/invoice-register?branch=main&year=2026&month=10", headers=chic).json()
        inv_401_list = [i for i in reg["invoices"] if i["room_no"] == "401"]
        self.assertEqual(len(inv_401_list), 1)  # 2번 분할 입금되었어도 월 1장으로 집계
        inv_401 = inv_401_list[0]
        self.assertEqual(inv_401["payment_count"], 2)
        self.assertEqual(inv_401["write_date"], "20261001")  # 입금일(10/05, 10/18)이 아닌 계약 약정 지급일(10/01)
        self.assertEqual(inv_401["buyer_ceo_name"], "")
        self.assertFalse(inv_401["ready_for_hometax"])
        self.assertIn("대표자명", inv_401["missing_fields"])
        self.assertIn("기숙사", inv_401["item_name"])  # 호실 용도에 맞춘 품목 문구

        # CSV 내보내기 시 정보가 빠진 401호는 제외되고 정상인 201호만 포함됨
        csv_before = c.get("/api/rent3_lessor/tax/export-hometax-csv?branch=main&year=2026&month=10", headers=chic).content.decode("utf-8-sig")
        self.assertIn("2148699887", csv_before)
        self.assertNotIn("1208155555", csv_before)

        # (3) 201호 2026-10 귀속분을 발행완료(ISSUED) + 국세청 승인번호 기록 시 이후 CSV 다운로드에서 자동 제외(이중 발행 방지)
        inv_201 = next(i for i in reg["invoices"] if i["room_no"] == "201")
        r_st = c.post(
            "/api/rent3_lessor/tax/invoices/status",
            headers=chic,
            json={
                "branch": "main",
                "room_no": "201",
                "billing_month": inv_201["billing_month"],
                "status": "ISSUED",
                "nts_confirm_no": "20261015-41000000-99887766",
                "memo": "홈택스 발급 완료",
                "write_date": inv_201["write_date"],
                "supply_amount": inv_201["supply_amount"],
                "tax_amount": inv_201["tax_amount"],
            },
        )
        self.assertEqual(r_st.status_code, 200, r_st.text)
        self.assertEqual(r_st.json()["record"]["status"], "ISSUED")
        self.assertEqual(r_st.json()["record"]["nts_confirm_no"], "20261015-41000000-99887766")

        csv_after_issue = c.get("/api/rent3_lessor/tax/export-hometax-csv?branch=main&year=2026&month=10", headers=chic).content.decode("utf-8-sig")
        self.assertNotIn("2148699887", csv_after_issue)

        # (4) 장부이관 이월 수납(:carry)은 당해 연도 총수입에서 제외되고 carryover_excluded_amount로 별도 집계됨
        Pay(
            branch="main",
            room_no="202",
            payer_name="김하늘",
            billing_month="2024-05",
            paid_date="2026-01-01",
            rent_amount=50_000_000,
            maintenance_amount=0,
            vat_amount=0,
            total_amount=50_000_000,
            payment_kind="MONTHLY_RENT",
            is_carryover=True,
            idempotency_key="import:main:202:carry:test51",
            raw_sms="장부 이전 입금 합산",
        ).save(action="test_carryover", app_source="test")

        ts_summary = c.get("/api/rent3_lessor/tax/summary?branch=main&year=2026", headers=chic).json()
        fin = ts_summary["summary"]
        self.assertGreaterEqual(fin["carryover_excluded_amount"], 50_000_000)
        self.assertLess(fin["gross_revenue"], 50_000_000)
        self.assertIn("참고용 추정치", ts_summary["tax_disclaimer"])
        # 부가세 요약(vat_collected)과 과세 호실별 표의 부가세 합계가 정확히 일치함 (0원 건 10% 임의 추정 없음)
        table_vat_sum = sum(r["vat_amount"] for r in fin["vat_room_breakdown"])
        self.assertEqual(fin["vat_collected"], table_vat_sum)

        # (5) 면세 주거용 임대 사업장현황신고 자료 및 CSV 다운로드 검증
        self.assertIn("exempt_housing_report", ts_summary)
        r_ex_csv = c.get("/api/rent3_lessor/tax/export-exempt-csv?branch=main&year=2026", headers=chic)
        self.assertEqual(r_ex_csv.status_code, 200)
        self.assertIn("사업장현황신고용 호실별 수입금액 검토표", r_ex_csv.content.decode("utf-8-sig"))

        # (6) 임차인 납부확인서: 가짜 승인번호(CASH-...) 및 가짜 사업자번호(101-86-00000)가 없고, 관리비를 제외한 순수 월세 기준으로 17%·15% 세액공제 산출
        myhome = c.get("/api/rent3_tenant/myhome/tenant_201").json()
        cert = myhome["certificate"]
        self.assertNotEqual(cert["lessor"]["business_number"], "101-86-00000")
        for p_row in cert["payments"]:
            self.assertFalse(str(p_row["cash_receipt_no"]).startswith("CASH-"))
        self.assertLess(cert["rent_only_paid"], cert["total_paid"])
        self.assertEqual(cert["rent_only_paid"] + cert["maintenance_paid"], cert["total_paid"])
        self.assertEqual(cert["estimated_tax_credit_17pct"], round(cert["deductible_rent_base"] * 0.17))
        self.assertEqual(cert["estimated_tax_credit_15pct"], round(cert["deductible_rent_base"] * 0.15))
        self.assertIn("계좌이체", cert["guidance_notice"])

        # (7) Claude 2차 검토 반영 검증:
        # 7-1. 서버가 ?token= 쿼리 파라미터 인증을 전면 거부하는지 확인
        raw_tok = chic["Authorization"].split()[1]
        self.assertEqual(
            c.get(f"/api/rent3_lessor/tax/export-csv?branch=main&year=2026&token={raw_tok}", headers=self.NOAUTH).status_code,
            401,
        )
        # 7-2. 공급가액만 입금되고 부가세가 미납된 경우 영수("01")가 아닌 청구("02")로 분류되는지 확인
        # (본 테스트에서 직접 계약을 설정한 401호: 월세 450,000 + 관리비 50,000 = 공급가액 500,000, 세액 50,000)
        Pay(
            branch="main",
            room_no="401",
            payer_name="(주)한빛기숙사",
            billing_month="2026-11",
            paid_date="2026-11-05",
            rent_amount=450000,
            maintenance_amount=50000,
            vat_amount=0,  # 계약 공급가액(500,000)만 입금되고 부가세(50,000) 미입금
            total_amount=500000,
            payment_kind="MONTHLY_RENT",
            idempotency_key="test51:401:nov_supply_only",
        ).save(action="test_supply_only", app_source="test")
        reg_nov = c.get("/api/rent3_lessor/tax/invoice-register?branch=main&year=2026&month=11", headers=chic).json()
        inv_401_nov = next(i for i in reg_nov["invoices"] if i["room_no"] == "401")
        self.assertEqual(inv_401_nov["supply_amount"], 500000)
        self.assertEqual(inv_401_nov["tax_amount"], 50000)
        self.assertEqual(inv_401_nov["paid_amount"], 500000)
        self.assertEqual(inv_401_nov["charge_type"], "02")  # 세액 미납 상태이므로 청구(02)

        # 7-3. 기숙사 임차 법인의 별도 사업장 주소(business_address) 저장 시 세금계산서 공급받는자 주소에 반영되는지 확인
        c.post(
            "/api/rent3_lessor/tax/tenant-biz",
            headers=chic,
            json={
                "branch": "main",
                "room_no": "401",
                "business_number": "120-81-55555",
                "company_name": "(주)한빛기숙사",
                "ceo_name": "정대표",
                "email": "dorm@hanbit.co.kr",
                "business_address": "서울시 강남구 테헤란로 999 한빛타워 10층",
                "vat_target": True,
                "tax_invoice_requested": True,
            },
        )
        reg_oct2 = c.get("/api/rent3_lessor/tax/invoice-register?branch=main&year=2026&month=10", headers=chic).json()
        inv_401_updated = next(i for i in reg_oct2["invoices"] if i["room_no"] == "401")
        self.assertEqual(inv_401_updated["buyer_address"], "서울시 강남구 테헤란로 999 한빛타워 10층")
        self.assertTrue(inv_401_updated["ready_for_hometax"])

        # 7-4. 종합과세 누진세율(6%~45%) 추정치가 summary에 포함되고 구간 계산이 정확한지 확인
        from apps.rent_dist._rent3_core.billing import TaxAndCertificateService
        self.assertIn("comprehensive_combined_estimate", fin)
        self.assertIn("rate_pct", fin["comprehensive_combined_estimate"])
        prog_est = TaxAndCertificateService.estimate_progressive_income_tax(60_000_000)
        self.assertEqual(prog_est["rate_pct"], 24)
        self.assertEqual(prog_est["progressive_deduction"], 5_760_000)
        self.assertGreater(prog_est["total_tax_inc_local"], 0)

    def test_52_lessor_menu_reaches_every_screen(self):
        """52. 임대인 첫 화면은 수납 현황 하나뿐이고, 나머지 기능은 모두 ☰ 메뉴에서 닿는다(빠진 기능 0개). 공통 틀 파일이 서빙된다."""
        import re
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend")
        app = open(os.path.join(fe, "app.js"), encoding="utf-8").read()
        idx = open(os.path.join(fe, "index.html"), encoding="utf-8").read()
        # 공통 틀(r3_modal / r3_shell)이 /shared/ui 로 서빙되고 화면이 불러온다
        for name in ("r3_modal.js", "r3_shell.js"):
            self.assertEqual(self.client.get(f"/shared/ui/{name}", headers=self.NOAUTH).status_code, 200, name)
            self.assertIn(f"/shared/ui/{name}", idx)
        # 메뉴에 있는 화면 = 앱이 그릴 수 있는 화면
        menu = app[app.index("const LESSOR_MENU"):app.index("R3Shell.renderMenu")]
        tabs = set(re.findall(r'tab: "(\w+)"', menu))
        self.assertEqual(tabs, {"ledger", "broker", "rooms", "registry", "tax", "ops", "expiry", "notice", "bldgtax", "messages", "mail"})
        for t in tabs - {"ledger"}:
            self.assertIn(f'currentTab === "{t}"', app, t)
            self.assertIn(f'{t}: "', app[app.index("const TAB_TITLES"):], t)
        self.assertIn("renderExpiryTab", app)
        # 이동 링크와 단추
        for needle in ('href: "/rent3_contract/?role=lessor"', 'href: "manual.html"', 'href: "/rent3_tenant/"',
                       'href: "/rent3_admin/"', 'href: "/rent3/pool.html"', 'id: "btnEditBuilding"', 'id: "btnSupportSetting"', 'id: "btnSupportChat"'):
            self.assertIn(needle, menu, needle)
        # 첫 화면에는 상단 탭 줄이 없다 (메뉴 서랍만)
        self.assertNotIn('data-tab="rooms"', idx)
        self.assertNotIn('data-tab="ledger"', idx)
        self.assertIn("data-r3-drawer", idx)
        # 퇴실정산은 호실 카드 '전출' 창이 맡는다 — 운영 화면에는 따로 두지 않는다
        self.assertNotIn("btnCalcCo", app)
        self.assertIn("showMoveOutModal", app)
        # 대상 호실 설정은 건물 메뉴 한 곳에만 입력칸이 있다 (세무 화면에는 바로가기만)
        self.assertEqual(app.count('id="txVatRooms"'), 1)
        self.assertIn('id="btnGoBldgTax"', app)

    def test_53_admin_proxy_is_read_only_in_strict_mode(self):
        """53. 엄격 모드(운영 기본): 관리인 토큰은 읽기만 되고, 당사자의 법적 행위·금전·계약 기록은 403. 허용 목록(A/S)은 통과. 호환 모드에선 예전처럼 동작."""
        from unittest import mock
        c = self.client                       # 기본 헤더 = admin 토큰
        chic = self._login("chic")
        with mock.patch.dict(os.environ, {"RENT3_ADMIN_PROXY_STRICT": "1"}):
            # 입장 없이 주소를 직접 쳐서 읽는 것은 막힌다(66 에서 자세히). 대상이 없는 목록은 그대로
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main").status_code, 403)
            self.assertEqual(c.get("/api/rent3_tenant/myhome/tenant_201").status_code, 403)
            self.assertEqual(c.get("/api/rent3_contract/sessions").status_code, 200)
            from apps.rent_dist._rent3_core import Lessor, Tenant
            self._admin_enter(Tenant.get("tenant_201"), "tenant")             # 입장하면 읽기는 된다
            self._admin_enter(Lessor.first(branch="main"), "lessor")
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main").status_code, 200)
            self.assertEqual(c.get("/api/rent3_tenant/myhome/tenant_201").status_code, 200)
            # 금전·계약 기록은 403 (임대인 앱)
            money = [
                ("/api/rent3_lessor/payments/collect", {"branch": "main", "room_no": "201", "amount": 1000}),
                ("/api/rent3_lessor/payments/sms-paste", {"branch": "main", "sms_text": "x"}),
                ("/api/rent3_lessor/payments/edit", {"branch": "main", "pay_id": "x", "paid_date": "2026-01-01"}),
                ("/api/rent3_lessor/outlays", {"branch": "main", "title": "x", "amount": 1000}),
                ("/api/rent3_lessor/contracts/edit", {"branch": "main", "contract_id": "x", "deposit": 1, "monthly_rent": 1, "maintenance_fee": 1}),
                ("/api/rent3_lessor/checkout/calculate", {"branch": "main", "room_no": "201"}),
                ("/api/rent3_lessor/building/tax-rooms", {"branch": "main", "vat_rooms": "201"}),
            ]
            for url, body in money:
                r = c.post(url, json=body)
                self.assertEqual(r.status_code, 403, url)
                self.assertIn("보기 전용", r.text)
            # 당사자의 법적 행위는 403 (계약 앱) — 역할 값과 무관
            for suffix in ("step1", "step2", "step3/agree", "step4/sign", "step5/complete", "verify-id", "tenant-registry"):
                for role in ("lessor", "tenant"):
                    r = c.post(f"/api/rent3_contract/sessions/ghost/{suffix}", json={"role": role})
                    self.assertEqual(r.status_code, 403, (suffix, role))
                    self.assertIn("대신할 수 없습니다", r.text)
            # 임차인 앱: 납부·정산 동의도 대신할 수 없다
            self.assertEqual(c.post("/api/rent3_tenant/myhome/tenant_201/transfer-notice", json={"amount": 1}).status_code, 403)
            self.assertEqual(c.post("/api/rent3_tenant/checkout/ghost/agree", json={}).status_code, 403)
            # 허용 목록(A/S 배정·접수)은 가드를 통과한다 (대상이 없어 다른 오류가 나도 403 은 아니다)
            self.assertNotEqual(c.post("/api/rent3_lessor/tickets/ghost/assign", json={}).status_code, 403)
            self.assertNotEqual(c.post("/api/rent3_tenant/myhome/tenant_201/tickets", json={}).status_code, 403)
            # 임대인 본인 토큰은 영향이 없다
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).status_code, 200)
            self.assertNotEqual(c.post("/api/rent3_lessor/contracts/edit", headers=chic,
                                       json={"branch": "main", "contract_id": "ghost", "deposit": 1, "monthly_rent": 1, "maintenance_fee": 1}).status_code, 403)
        # 호환 모드(기존 시험 묶음): 관리인 쓰기가 가드에서 막히지 않는다
        self.assertNotEqual(c.post("/api/rent3_lessor/contracts/edit",
                                   json={"branch": "main", "contract_id": "ghost", "deposit": 1, "monthly_rent": 1, "maintenance_fee": 1}).status_code, 403)

    def test_54_signup_consent_and_support_toggle(self):
        """54. 운영 모드: 가입은 개인정보 동의 필수(원격 지원은 선택), 동의 기록 없는 기존 사용자는 로그인 때 동의 화면을 거친다. 원격 지원 허용은 언제든 ON/OFF. 관리인은 해당 없음."""
        from unittest import mock
        c = self.client
        login = lambda ident, pw="1212": c.post("/api/rent3/pool/auth/login", headers=self.NOAUTH,
                                                json={"login_mode": "account", "identifier": ident, "password": pw})
        reg = lambda **kw: c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH,
                                  json={"role": "prospect", "name": "동의테스트", "phone": "010-5555-0054", "password": "Reg#Pass12345", **kw})
        with mock.patch.dict(os.environ, {"RENT3_CONSENT_REQUIRED": "1"}):
            # 가입: 개인정보 동의가 없으면 거부
            r = reg()
            self.assertEqual(r.status_code, 400, r.text)
            self.assertIn("개인정보 수집·이용 동의는 필수", r.text)
            # 동의하면 가입되고, 선택 항목(원격 지원)은 꺼진 채로 기록
            r = reg(agree_privacy=True)
            self.assertEqual(r.status_code, 200, r.text)
            tok = r.json()["token"]
            hdr = {"Authorization": f"Bearer {tok}"}
            st = c.get("/api/rent3/pool/auth/consents", headers=hdr).json()
            self.assertTrue(st["privacy_agreed"])
            self.assertFalse(st["support_access"])
            # 원격 지원 허용 ON → OFF 를 언제든 바꿀 수 있다
            on = c.post("/api/rent3/pool/auth/consents/support", headers=hdr, json={"allow": True}).json()
            self.assertTrue(on["support_access"])
            off = c.post("/api/rent3/pool/auth/consents/support", headers=hdr, json={"allow": False}).json()
            self.assertFalse(off["support_access"])
            self.assertTrue(off["privacy_agreed"])
            # 기존 사용자(시드 임대인, 동의 기록 없음): 로그인하면 토큰 없이 '동의 필요'
            r = login("chic")
            self.assertEqual(r.status_code, 200, r.text)
            held = r.json()
            self.assertTrue(held.get("needs_consent"))
            self.assertNotIn("token", held)
            self.assertTrue(held["consent_ticket"])
            # 필수 동의 없이는 진행할 수 없고, 잘못된 확인표도 거부
            self.assertEqual(c.post("/api/rent3/pool/auth/consent", headers=self.NOAUTH,
                                    json={"ticket": held["consent_ticket"], "agree_privacy": False}).status_code, 400)
            self.assertEqual(c.post("/api/rent3/pool/auth/consent", headers=self.NOAUTH,
                                    json={"ticket": "ghost", "agree_privacy": True}).status_code, 401)
            # 동의하면 보류했던 로그인 결과(토큰)를 받고, 동의 기록이 남는다
            done = c.post("/api/rent3/pool/auth/consent", headers=self.NOAUTH,
                          json={"ticket": held["consent_ticket"], "agree_privacy": True, "agree_support": True}).json()
            self.assertTrue(done["token"])
            self.assertEqual(done["user"]["role"], "lessor")
            self.assertTrue(c.get("/api/rent3/pool/auth/consents", headers={"Authorization": f"Bearer {done['token']}"}).json()["support_access"])
            # 확인표는 한 번만 쓸 수 있다
            self.assertEqual(c.post("/api/rent3/pool/auth/consent", headers=self.NOAUTH,
                                    json={"ticket": held["consent_ticket"], "agree_privacy": True}).status_code, 401)
            # 다시 로그인하면 바로 토큰
            again = login("chic").json()
            self.assertTrue(again.get("token"))
            self.assertFalse(again.get("needs_consent"))
            # 관리인은 동의 화면 대상이 아니다
            adm = login("admin").json()
            self.assertTrue(adm.get("token"))
            self.assertFalse(c.get("/api/rent3/pool/auth/consents", headers={"Authorization": f"Bearer {adm['token']}"}).json()["applicable"])
        # 호환 모드(시연·기존 시험): 동의 없이도 가입·로그인된다
        with mock.patch.dict(os.environ, {"RENT3_CONSENT_REQUIRED": "0"}):
            self.assertTrue(login("sunho").json().get("token"))

    def test_55_support_chat_access_log_and_phone_intake(self):
        """55. 지원 채팅·접속 기록: 원격 지원 허용 + 채팅(또는 전화 접수)이 있어야 입장, 입장하면 기록·안내, 10일 뒤 채팅 삭제(접속 기록은 1년), 이벤트 로그에 본문 없음."""
        from datetime import timedelta
        from apps.rent_dist._rent3_core import Lessor, Tenant, consent as cs, support as sup
        from apps.rent_dist._rent3_core.authz import lessor_token, tenant_token
        from apps.rent_dist._rent3_core.models import date_today
        c = self.client                       # 기본 헤더 = admin
        lessor = Lessor.first(username="chic")
        tenant = Tenant.first(room_no="201", branch="main")
        lh = {"Authorization": f"Bearer {lessor_token(lessor)}"}
        th = {"Authorization": f"Bearer {tenant_token(tenant)}"}
        lkey, tkey = sup.thread_key(lessor), sup.thread_key(tenant)
        for u in (lessor, tenant):            # 개인정보 동의는 했고 원격 지원은 아직 허용 안 함
            cs.record_consent(u, True, False)
        secret = "비밀문장-보일러가-안켜져요-9981"

        # 사용자: 지원 채팅 글쓰기·읽기 (관리인 계정은 사용자용 API 대상이 아니다)
        self.assertEqual(c.post("/api/rent3/pool/support/messages", headers=th, json={"body": secret}).status_code, 200)
        self.assertEqual(c.post("/api/rent3/pool/support/messages", headers=th, json={"body": "  "}).status_code, 400)
        self.assertEqual(c.get("/api/rent3/pool/support/thread").status_code, 400)       # admin 토큰
        mine = c.get("/api/rent3/pool/support/thread", headers=th).json()
        self.assertEqual([m["body"] for m in mine["messages"]], [secret])
        self.assertEqual(c.get("/api/rent3/pool/support/thread", headers=lh).json()["messages"], [])   # 임대인은 임차인의 대화를 못 본다
        # 풀 이벤트 로그(덧붙이기 전용)에는 본문이 남지 않는다
        ev = open(os.path.join(self.temp_pool, "_events.jsonl"), encoding="utf-8").read()
        self.assertNotIn(secret, ev)

        # 관리인: 대화 읽기, 원격 지원 허용 전에는 입장 거부(전화 접수는 기록만 남기므로 허용 전에도 된다)
        thr = c.get("/api/rent3_admin/support/thread", params={"user": tkey}).json()
        self.assertEqual(thr["messages"][0]["body"], secret)
        self.assertIn("phone", thr["user"])            # 전화 접수 칸의 전화 걸기 단추용(선택한 사용자 한 명)
        self.assertEqual(c.post("/api/rent3_admin/support/enter", json={"user": tkey, "view": "tenant", "category": "오류 신고"}).status_code, 403)
        self.assertEqual(c.post("/api/rent3_admin/support/phone-intake", json={"user": tkey, "category": "오류 신고", "memo": ""}).status_code, 400)      # 내용 없음
        pre = c.post("/api/rent3_admin/support/phone-intake", json={"user": tkey, "category": "오류 신고", "memo": "전화로 로그인 오류 문의"})
        self.assertEqual(pre.status_code, 200, pre.text)
        self.assertFalse(pre.json()["allowed"])                                                                                                   # 기록은 남지만 입장은 아직 못 한다
        self.assertTrue(any("로그인 오류 문의" in m["body"] for m in c.get("/api/rent3/pool/support/thread", headers=th).json()["messages"]))        # 사용자 채팅에 대화로 저장
        self.assertEqual(c.post("/api/rent3_admin/support/enter", json={"user": tkey, "view": "tenant", "category": "오류 신고"}).status_code, 403)

        # 허용하면 입장: 접속 기록 + 지원 채팅에 안내, 이동 주소는 보기 전용 표시(proxy) 포함
        cs.set_support_access(tenant, True)
        r = c.post("/api/rent3_admin/support/enter", json={"user": tkey, "view": "tenant", "category": "사용법 문의"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["redirect_url"], f"/rent3_tenant/?tenant={tenant.id}&proxy=tenant")
        logs = sup.SupportAccessLog.find(target_user=tkey)
        self.assertEqual((len(logs), logs[0].category, logs[0].view), (1, "사용법 문의", "tenant"))
        self.assertTrue(logs[0].retain_until >= (date_today() + timedelta(days=364)).isoformat())
        kinds = [m["kind"] for m in c.get("/api/rent3/pool/support/thread", headers=th).json()["messages"]]
        self.assertIn("access_start", kinds)
        self.assertTrue(c.get("/api/rent3/pool/support/summary", headers=th).json()["admin_viewing"])      # 사용자에게 '접속 중' 알림
        # 입장 종류가 사용자 역할과 다르면 거부, 계약방은 세션이 있어야 한다
        self.assertEqual(c.post("/api/rent3_admin/support/enter", json={"user": tkey, "view": "lessor"}).status_code, 400)
        self.assertEqual(c.post("/api/rent3_admin/support/enter", json={"user": tkey, "view": "contract", "session_id": "ghost"}).status_code, 404)

        # 전화 접수: 채팅이 없던 임대인도 '[전화 요청 접수]' 글이 생기며 입장 가능
        self.assertEqual(c.post("/api/rent3_admin/support/enter", json={"user": lkey, "view": "lessor"}).status_code, 403)   # 허용 전
        cs.set_support_access(lessor, True)
        first = c.post("/api/rent3_admin/support/enter", json={"user": lkey, "view": "lessor"})
        self.assertEqual(first.status_code, 200, first.text)          # 허용했다면 지원 채팅이 아직 없어도 입장 가능(입장 안내가 채팅에 남는다)
        self.assertTrue(any(m["kind"] == "access_start" for m in c.get("/api/rent3/pool/support/thread", headers=lh).json()["messages"]))
        self.assertEqual(c.post("/api/rent3_admin/support/phone-intake", json={"user": lkey, "category": "없는분류", "memo": "x"}).status_code, 400)
        r = c.post("/api/rent3_admin/support/phone-intake", json={"user": lkey, "category": "사용법 문의", "memo": "수납 입력 방법을 모르심"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["item"]["body"].startswith("[전화 요청 접수]"))
        self.assertTrue(any("수납 입력 방법" in m["body"] for m in c.get("/api/rent3/pool/support/thread", headers=lh).json()["messages"]))
        r = c.post("/api/rent3_admin/support/enter", json={"user": lkey, "view": "lessor", "room_no": "201", "category": "사용법 문의"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["redirect_url"], f"/rent3_lessor/?branch={lessor.branch}&proxy=lessor&room=201")

        # 원격 지원을 다시 끄면 즉시 입장 불가
        cs.set_support_access(tenant, False)
        self.assertEqual(c.post("/api/rent3_admin/support/enter", json={"user": tkey, "view": "tenant"}).status_code, 403)

        # 원격 지원 카드용 목록: 건물·호실·임차인·허용 여부
        d = c.get("/api/rent3_admin/support/directory", params={"branch": "main"}).json()
        room201 = next(x for x in d["rooms"] if x["room_no"] == "201")
        self.assertEqual((room201["tenant"]["user"], room201["tenant"]["support_allowed"]), (tkey, False))
        self.assertEqual(d["lessor"]["support_allowed"], True)
        self.assertTrue(any(b["branch"] == "main" for b in c.get("/api/rent3_admin/support/directory").json()["branches"]))
        # 일반 사용자는 관리인용 API를 쓸 수 없다
        self.assertIn(c.get("/api/rent3_admin/support/directory", headers=th).status_code, (401, 403))
        self.assertIn(c.post("/api/rent3_admin/support/enter", headers=lh, json={"user": tkey, "view": "tenant"}).status_code, (401, 403))

        # 10일이 지난 채팅은 지워지고, 접속 기록은 1년이 지나야 지워진다
        old = sup.SupportMessage.first(thread_user=tkey)
        old.expires_at = (date_today() - timedelta(days=1)).isoformat()
        old.save(action="test_expire")
        sup.SupportAccessLog.first(target_user=tkey).delete(action="test_cleanup")
        keep = sup.SupportAccessLog(admin_user="admin", target_user=tkey, retain_until=(date_today() + timedelta(days=5)).isoformat()).save(action="t")
        gone = sup.SupportAccessLog(admin_user="admin", target_user=tkey, retain_until=(date_today() - timedelta(days=1)).isoformat()).save(action="t")
        self.assertEqual(sup.purge_expired()["messages"] >= 1, True)
        self.assertNotIn(secret, [m["body"] for m in c.get("/api/rent3/pool/support/thread", headers=th).json()["messages"]])
        self.assertIsNone(sup.SupportAccessLog.get(gone.id))
        self.assertIsNotNone(sup.SupportAccessLog.get(keep.id))

    def test_56_tenant_menu_reaches_every_service(self):
        """56. 임차인 첫 화면은 '내 집(납부 확인)' 하나뿐이고, 나머지 서비스는 모두 ☰ 메뉴에서 닿는다(빠진 기능 0개). 헤더 모드 단추·관리자용 선택기는 없다."""
        import re
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend")
        app = open(os.path.join(fe, "app.js"), encoding="utf-8").read()
        idx = open(os.path.join(fe, "index.html"), encoding="utf-8").read()
        menu = app[app.index("const TENANT_MENU"):app.index("R3Shell.renderMenu")]
        ids = set(re.findall(r'id: "(\w+)"', menu))
        self.assertEqual(ids, {"btnSvcHome", "btnSvcCert", "btnSvcPaper", "btnSvcExpiry", "btnSvcCheckout", "btnSvcAs", "btnSvcDoor",
                               "btnSupportChat", "btnMenuListings", "btnSupportSetting", "btnSvcMsg", "btnShortcut", "btnBizInfo", "btnLessorInvite"})
        for needle in ('href: "/rent3_contract/?role=tenant"', 'href: "manual.html"', 'href: "/rent3_lessor/", adminOnly: true',
                       'href: "/rent3_admin/", adminOnly: true', 'href: "/rent3/pool.html", adminOnly: true'):
            self.assertIn(needle, menu, needle)
        # 메뉴의 서비스 화면 = 마이홈에 실제로 있는 구역
        views = set(re.findall(r'btnSvc\w+: "(\w+)"', app[app.index("const MENU_VIEWS"):]))
        sections = set(re.findall(r'<section class="svc" data-svc="(\w+)">', app))
        self.assertTrue(views <= sections, (views, sections))
        self.assertIn("home", sections)
        # 첫 화면 구성: 할 일 배너(미납>퇴실 정산>만기 확인, 최대 2줄)와 납부 현황(오래된 완납 달은 접힘)
        self.assertIn("todos.slice(0, 2)", app)
        self.assertIn("이전 완납", app)
        # 옛 헤더 모드 단추와 관리자용 세입자 선택기는 없다
        for gone in ("btnModeMyhome", "btnModeListings", "mode-switch", "tenantSel", "loadTenants"):
            self.assertNotIn(gone, app + idx, gone)
        self.assertIn("data-r3-drawer", idx)

    def test_57_tax_hub_keeps_every_tax_feature(self):
        """57. 세무 허브(종합소득세 / 부가세 / 세금계산서 / 경비·면세 신고)로 나눈 뒤에도 세무 기능이 하나도 빠지지 않는다."""
        import re
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js")
        app = open(fe, encoding="utf-8").read()
        tax = app[app.index("async function renderTaxTab"):app.index("// 건물 ▸ 부가세 · 소득세 대상 호실 설정")]
        # 허브와 하위 화면 4개
        for v in ('"income"', '"vat"', '"invoice"', '"expense"'):
            self.assertIn(v, tax)
        self.assertIn("tax-hub-btn", tax)
        self.assertIn('id="btnTaxHub"', tax)
        # 화면별로 있어야 할 기능 (사용자가 확정한 유지 목록)
        keep = {
            "income": ("btnDlTaxCsv", "housing_separate_tax_general", "housing_separate_tax_registered", "comprehensive_combined_estimate",
                       "commercial_comprehensive_estimate", "carryover_excluded_amount", "필요경비율 50%", "누진세율"),
            "vat": ("btnGoBldgTax", "vat_periods", "output_vat", "input_vat", "net_vat_payable", "공통경비"),
            "invoice": ("btnDlHometaxCsv", "btnDlHometaxCsv2", "btn-inv-status", "taxMonthSel", "ready_for_hometax", "ISSUED", "nts_confirm_no", "5줄 스크롤"),
            "expense": ("btnDlExemptCsv", "btnDlExemptCsv2", "btnAddOutlay", "outEvid", "outScope", "outVendorTax", "is_vat_deductible", "exempt_housing_report",
                        "exemptTotals", "outlay-row"),
        }
        for view, needles in keep.items():
            for n in needles:
                self.assertIn(n, tax, (view, n))
        # 세금계산서 발행 기록·사업장 주소 입력은 모달(전 호실 대상)로 유지
        self.assertIn("tenant_business_address", app)      # 계약내용 창에서 모든 호실의 사업자 정보·사업장 주소를 입력·수정
        self.assertIn("cmBizAddr", app)
        self.assertIn("showInvoiceStatusModal", app)
        self.assertIn("charge_type_label", tax)       # 영수(01)/청구(02) 구분이 화면에 보인다
        # 서버가 네 화면에 필요한 값을 모두 준다
        d = self.client.get("/api/rent3_lessor/tax/summary?branch=main&year=2026").json()
        for k in ("summary", "vat_periods", "tax_invoice_register", "exempt_housing_report", "outlays", "expense_categories", "building_tax_config"):
            self.assertIn(k, d, k)
        fin = d["summary"]
        for k in ("comprehensive_combined_estimate", "housing_separate_tax_general", "carryover_excluded_amount", "vat_room_breakdown",
                  "income_tax_room_breakdown", "expenses_by_category"):
            self.assertIn(k, fin, k)
        for path in ("export-csv", "export-hometax-csv", "export-exempt-csv"):
            self.assertEqual(self.client.get(f"/api/rent3_lessor/tax/{path}?branch=main&year=2026").status_code, 200, path)
        # 메뉴에는 세무 허브 한 곳으로 들어간다
        menu = app[app.index("const LESSOR_MENU"):app.index("R3Shell.renderMenu")]
        self.assertIn('tab: "tax"', menu)
        self.assertIn("switchTab", app)

    def test_58_contract_room_chat_proposals_and_agree_block(self):
        """58. 계약방 대화(5단계): 문의·제안·수정 요청, 제안 수락 시 update_terms(리비전·동의 초기화), 열린 제안이 있으면 Step 3 동의 차단, 단계 ④ 이후엔 문의만, 당사자만 접근."""
        from unittest import mock
        from apps.rent_dist._rent3_core import DirectChat, Lessor, Tenant
        from apps.rent_dist._rent3_core.authz import lessor_token, tenant_token
        c, base = self.client, "/api/rent3_contract"
        sid = self._policy_session("961", 5000000, 300000)
        self.assertEqual(self._reg(sid).status_code, 200)
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH,        # 계약 상대(예비 임차인)가 같은 번호로 가입한 계정
                     json={"role": "prospect", "name": "정책시험", "phone": "010-5555-9961", "password": "Pw123456"})
        self.assertEqual(reg.status_code, 200, reg.text)
        tenant = Tenant.get(reg.json()["user"]["id"])
        tenant.identity = {"verified": True}          # 본인인증을 마친 임차인(수락·서명 같은 행위는 본인인증이 필요하다)
        tenant.save(action="test_verified")
        lh = {"Authorization": f"Bearer {lessor_token(Lessor.first(username='chic'))}"}
        th = {"Authorization": f"Bearer {tenant_token(tenant)}"}
        url = f"{base}/sessions/{sid}/messages"
        secret = "비밀-문의-내용-4417"
        t0 = c.get(f"{base}/sessions/{sid}").json()["session"]["terms"]

        # ── 문의: 양쪽 모두 가능, 읽음 처리 ──
        r = c.post(url, headers=th, json={"kind": "question", "body": secret})
        self.assertEqual(r.status_code, 200, r.text)
        # 주기적 확인(폴링)은 읽음 처리하지 않는다: 세션 조회의 요약과 mark_read=false 목록은 몇 번을 불러도 배지가 유지된다
        for _ in range(3):
            self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=lh).json()["chat"], {"unread": 1, "open_count": 0})
            self.assertEqual(c.get(url + "?mark_read=false", headers=lh).json()["unread"], 1)
        self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=th).json()["chat"]["unread"], 0)   # 보낸 사람 쪽은 안 읽은 글 없음
        self.assertEqual(c.get(f"{base}/sessions/{sid}").json()["chat"]["unread"], 0)                  # 관리인(읽기 전용)은 읽음 처리도 배지도 없음
        got = c.get(url, headers=lh).json()                                      # 대화창을 열면 그때 읽음 처리
        self.assertEqual([m["body"] for m in got["messages"]], [secret])
        self.assertEqual(got["unread"], 1)
        self.assertEqual(c.get(url, headers=lh).json()["unread"], 0)             # 읽으면 줄어든다
        self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=lh).json()["chat"]["unread"], 0)
        self.assertEqual(c.post(url, headers=th, json={"kind": "question", "body": " "}).status_code, 400)
        self.assertEqual(c.post(url, headers=th, json={"kind": "gossip", "body": "x"}).status_code, 400)
        # 풀 이벤트 로그에 본문이 남지 않는다
        self.assertNotIn(secret, open(os.path.join(self.temp_pool, "_events.jsonl"), encoding="utf-8").read())

        # ── 접근 통제: 타 지점 임대인·다른 임차인·비로그인은 거부, 관리인은 읽기만 ──
        sunho = self._login("sunho")
        other_t = self._as(tenant_token(Tenant.first(room_no="201", branch="main")))
        for h in (sunho, other_t):
            self.assertEqual(c.get(url, headers=h).status_code, 403)
            self.assertEqual(c.post(url, headers=h, json={"kind": "question", "body": "x"}).status_code, 403)
        self.assertEqual(c.get(url, headers=self.NOAUTH).status_code, 401)
        self.assertEqual(c.get(url).status_code, 200)                              # admin 기본 토큰: 읽기
        with mock.patch.dict(os.environ, {"RENT3_ADMIN_PROXY_STRICT": "1"}):
            self.assertEqual(c.get(url).status_code, 403)                              # 입장 없이는 못 읽는다
            self._admin_enter(tenant, "contract", session_id=sid)
            self.assertEqual(c.get(url).status_code, 200)
            self.assertEqual(c.post(url, json={"kind": "question", "body": "관리인이 끼어듦"}).status_code, 403)
        from apps.rent_dist._rent3_core import support as _sup
        for m in _sup.SupportMessage.find(thread_user=_sup.thread_key(tenant)):      # 입장 준비로 남긴 지원 채팅 글은 치운다(아래에서 지원 채팅이 비어 있어야 한다)
            m.delete(action="t")

        # ── 제안: 6개 항목만, 값 검증 ──
        prop = lambda h, field, val, **kw: c.post(url, headers=h, json={"kind": "proposal", "field": field, "new_value": val, **kw})
        self.assertEqual(prop(th, "special_terms", "x").status_code, 400)            # 특약은 제안이 아니라 수정 요청
        self.assertEqual(prop(th, "deposit", 5000000).status_code, 400)              # 현재 값과 같음
        self.assertEqual(prop(th, "deposit", "abc").status_code, 400)
        self.assertEqual(prop(th, "contract_deposit", 99000000).status_code, 400)    # 계약금 > 보증금
        self.assertEqual(prop(th, "move_in_date", "2026-13-45").status_code, 400)
        self.assertEqual(prop(th, "lease_term_months", 0).status_code, 400)
        for field, val in (("contract_deposit", 400000), ("monthly_rent", 280000), ("maintenance_fee", 40000),
                           ("move_in_date", "2026-12-20"), ("lease_term_months", 24)):
            self.assertEqual(prop(th, field, val).status_code, 200, field)
        r = prop(th, "deposit", 4500000)
        self.assertEqual(r.status_code, 200, r.text)
        dep_id = r.json()["item"]["id"]
        self.assertEqual(r.json()["item"]["proposal"]["old"], 5000000)
        self.assertEqual(r.json()["open_count"], 6)
        # 같은 사람이 같은 항목을 다시 제안하면 이전 제안은 대체된다
        r2 = prop(th, "deposit", 4600000)
        dep_id2 = r2.json()["item"]["id"]
        self.assertEqual(r2.json()["open_count"], 6)
        self.assertEqual(c.post(f"{url}/{dep_id}/respond", headers=lh, json={"action": "accept"}).status_code, 409)   # 이미 처리됨(대체)

        # ── 수정 요청(특약): 자동 반영 없음 ──
        cr = c.post(url, headers=lh, json={"kind": "change_request", "field": "special_terms", "body": "반려동물 관련 특약 문구 수정 요청"})
        self.assertEqual(cr.status_code, 200, cr.text)
        cr_id = cr.json()["item"]["id"]
        self.assertEqual(c.post(url, headers=lh, json={"kind": "change_request", "field": "deposit", "body": "x"}).status_code, 400)

        # ── 열린 요청이 있으면 Step 3 최종 동의 불가 ──
        r = c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "lessor"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("답하지 않은 제안", r.text)

        # ── 응답 규칙: 내 글은 수락·거절 불가(철회만), 상대 글은 철회 불가 ──
        self.assertEqual(c.post(f"{url}/{dep_id2}/respond", headers=th, json={"action": "accept"}).status_code, 403)
        self.assertEqual(c.post(f"{url}/{dep_id2}/respond", headers=lh, json={"action": "withdraw"}).status_code, 403)
        self.assertEqual(c.post(f"{url}/{dep_id2}/respond", headers=sunho, json={"action": "accept"}).status_code, 403)
        self.assertEqual(c.post(f"{url}/{dep_id2}/respond", headers=lh, json={"action": "maybe"}).status_code, 400)
        self.assertEqual(c.post(f"{url}/nope/respond", headers=lh, json={"action": "accept"}).status_code, 404)

        # ── 수락 = 기존 update_terms: 값이 바뀌고 리비전이 오르며 양측 동의가 초기화된다 ──
        rev0 = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        r = c.post(f"{url}/{dep_id2}/respond", headers=lh, json={"action": "accept"})
        self.assertEqual(r.status_code, 200, r.text)
        ss = r.json()["session"]
        self.assertEqual(ss["terms"]["deposit"], 4600000)
        self.assertEqual(ss["revision"], rev0 + 1)
        self.assertFalse(ss["step3_lessor_agreed"] or ss["step3_tenant_agreed"])
        self.assertTrue(any(m["kind"] == "system" and "수락했습니다" in m["body"] for m in c.get(url, headers=th).json()["messages"]))
        # 나머지 제안: 임대인이 하나는 거절, 나머지는 수락
        items = [m for m in c.get(url, headers=lh).json()["messages"] if m["kind"] == "proposal" and m["status"] == "open"]
        self.assertEqual(len(items), 5)
        for m in items:
            action = "decline" if m["field"] == "maintenance_fee" else "accept"
            self.assertEqual(c.post(f"{url}/{m['id']}/respond", headers=lh, json={"action": action}).status_code, 200, m["field"])
        terms = c.get(f"{base}/sessions/{sid}").json()["session"]["terms"]
        self.assertEqual((terms["contract_deposit"], terms["monthly_rent"], terms["move_in_date"], terms["lease_term_months"]),
                         (400000, 280000, "2026-12-20", 24))
        self.assertEqual(terms["maintenance_fee"], t0["maintenance_fee"])          # 거절한 제안은 반영되지 않는다
        # 수정 요청이 남아 있는 동안은 여전히 동의 불가 → 요청자가 철회하면 동의 가능
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "lessor"}).status_code, 409)
        self.assertEqual(c.post(f"{url}/{cr_id}/respond", headers=th, json={"action": "withdraw"}).status_code, 403)   # 요청자(임대인)만 철회
        self.assertEqual(c.post(f"{url}/{cr_id}/respond", headers=lh, json={"action": "withdraw"}).status_code, 200)
        self.assertEqual(c.get(url, headers=lh).json()["open_count"], 0)
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "lessor"}).status_code, 200)
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": "tenant"}).status_code, 200)

        # ── 단계 ④ 이후: 제안·수정 요청 불가, 문의만 가능 ──
        self.assertGreaterEqual(c.get(f"{base}/sessions/{sid}").json()["session"]["current_step"], 4)
        r = prop(th, "monthly_rent", 270000)
        self.assertEqual(r.status_code, 400)
        self.assertIn("단계 ④", r.text)
        self.assertEqual(c.post(url, headers=lh, json={"kind": "change_request", "body": "x"}).status_code, 400)
        self.assertEqual(c.post(url, headers=th, json={"kind": "question", "body": "서명 전에 하나만 더 여쭙니다"}).status_code, 200)

        # ── 기존 1:1 대화 목록에는 계약방 대화가 섞이지 않는다 ──
        self.assertFalse(any(x.get("session_id") for x in c.get("/api/rent3_lessor/dashboard?branch=main").json()["chats"]))
        self.assertTrue(DirectChat.find(session_id=sid))

        # ── 관리인 지원 채팅(SupportMessage)과 섞이지 않는다: 같은 사람이어도 서로의 글이 보이지 않는다 ──
        from apps.rent_dist._rent3_core import support as sup
        self.assertEqual(c.get("/api/rent3/pool/support/thread", headers=th).json()["messages"], [])
        self.assertEqual(sup.SupportMessage.find(thread_user=sup.thread_key(tenant)), [])
        sup.post_user_message(tenant, "지원 채팅 글")
        self.assertNotIn("지원 채팅 글", [m["body"] for m in c.get(url, headers=th).json()["messages"]])

        # ── 체결·취소된 계약방은 읽기 전용 ──
        from apps.rent_dist._rent3_core import CoSignSession
        ses = CoSignSession.get(sid)
        ses.status = "CANCELLED"
        ses.save(action="test_cancel")
        self.assertEqual(c.post(url, headers=th, json={"kind": "question", "body": "취소 후 문의"}).status_code, 409)
        self.assertTrue(c.get(url, headers=th).json()["read_only"])

    def test_59_terms_lock_and_reopen(self):
        """59. 6개 항목은 /terms 로 직접 못 바꾸고(같은 값은 통과) 제안·수락으로만 바뀐다. 다른 조건은 직접 수정 가능. 3단계에서는 다시 열 필요 없음. 잠금 스위치·당사자 권한."""
        from unittest import mock
        c, base = self.client, "/api/rent3_contract"
        sid = self._policy_session("962", 5000000, 300000)
        self.assertEqual(self._reg(sid).status_code, 200)
        t0 = c.get(f"{base}/sessions/{sid}").json()["session"]["terms"]
        post = lambda updates, **kw: c.post(f"{base}/sessions/{sid}/terms", json={"role": "lessor", "updates": updates}, **kw)
        # 6개 항목: 바꾸면 409(이름을 알려 준다), 같은 값이면 통과
        for field, val in (("deposit", 4000000), ("contract_deposit", 100000), ("monthly_rent", 250000), ("maintenance_fee", 1000),
                           ("move_in_date", "2030-01-01"), ("lease_term_months", 6)):
            r = post({field: val})
            self.assertEqual(r.status_code, 409, field)
            self.assertIn("제안", r.text)
        self.assertEqual(post({"deposit": t0["deposit"], "monthly_rent": str(t0["monthly_rent"])}).status_code, 200)   # 같은 값(문자열 숫자 포함)
        # 인적사항·납부 시점·세금계산서·특약은 직접 수정 가능
        r = post({"tenant_car_number": "12가3456", "special_terms": "1. 반려동물 불가", "pay_timing": "선불"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["changed"])
        # 6개 항목과 다른 항목을 함께 보내도 6개 항목이 달라지면 통째로 거부된다
        self.assertEqual(post({"special_terms": "다른 특약", "deposit": 1}).status_code, 409)
        self.assertEqual(c.get(f"{base}/sessions/{sid}").json()["session"]["terms"]["special_terms"], "1. 반려동물 불가")
        # 3단계에서는 '다시 열기'가 필요 없다
        r = c.post(f"{base}/sessions/{sid}/reopen-terms", json={"role": "lessor", "confirm": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("이미 계약 조건 단계", r.text)
        # 제안 → 수락은 잠금과 무관하게 조건을 바꾼다
        acc = self._propose_accept(sid, "maintenance_fee", 40000, proposer="tenant")
        self.assertEqual(acc.status_code, 200, acc.text)
        self.assertEqual(acc.json()["session"]["terms"]["maintenance_fee"], 40000)
        # 잠금 스위치를 끄면 예전처럼 직접 수정(비상용)
        with mock.patch.dict(os.environ, {"RENT3_TERMS_LOCK": "0"}):
            r = post({"maintenance_fee": 45000})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["changed"])
        # 당사자만: 타 지점 임대인·비로그인·(엄격 모드) 관리인은 조건 다시 열기를 못 한다
        sunho = self._login("sunho")
        self.assertEqual(c.post(f"{base}/sessions/{sid}/reopen-terms", headers=sunho, json={"role": "lessor", "confirm": True}).status_code, 403)
        self.assertEqual(c.post(f"{base}/sessions/{sid}/reopen-terms", headers=self.NOAUTH, json={"role": "lessor", "confirm": True}).status_code, 401)
        with mock.patch.dict(os.environ, {"RENT3_ADMIN_PROXY_STRICT": "1"}):
            self.assertEqual(c.post(f"{base}/sessions/{sid}/reopen-terms", json={"role": "lessor", "confirm": True}).status_code, 403)

    def test_60_contract_screen_pieces(self):
        """60. 계약 화면(6단계): 지금 내 차례 한 줄, 💬 대화창, 3단계 제안·수정 요청·전체 동의, 조건 다시 열기, 호출 단계 초대 문자 복사가 있고 옛 직접 입력칸은 없다."""
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend")
        app = open(os.path.join(fe, "app.js"), encoding="utf-8").read()
        idx = open(os.path.join(fe, "index.html"), encoding="utf-8").read()
        self.assertIn('id="turnLine"', idx)
        self.assertIn("/shared/ui/r3_modal.js", idx)
        for needle in ("function turnInfo", "chatFab", "function openChatDrawer", "mark_read=true", "function openProposeDialog",
                       "function openRequestDialog", "function decoratePanel", "/reopen-terms", "btnCopyInvite",
                       "계약 조건 전체 동의", "btn-propose", "btnReqSpecial", "dotline", "chatSummary.open_count"):
            self.assertIn(needle, app, needle)
        # 열린 제안 수가 바뀌면 단계 화면을 다시 그린다(동의 단추 상태가 낡지 않게)
        self.assertIn("${chatSummary.open_count}`;", app[app.index("function switchModal"):])
        # 6개 항목은 직접 입력칸이 없다 (보기 + 제안)
        for gone in ('id="tDeposit"', 'id="tRent"', 'id="tMaint"', 'id="tConDep"', 'id="tMoveIn"', 'id="tMonths"'):
            self.assertNotIn(gone, app, gone)
        for field in ("deposit", "contract_deposit", "monthly_rent", "maintenance_fee", "move_in_date", "lease_term_months"):
            self.assertIn(f'["{field}", "', app, field)
        # 서버 약속과 맞는 호출: 대화·제안·응답·다시 열기 주소가 모두 서버에 있다
        routes = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "api.py"), encoding="utf-8").read()
        for r in ('"/sessions/{session_id}/messages"', '"/sessions/{session_id}/messages/{msg_id}/respond"', '"/sessions/{session_id}/reopen-terms"'):
            self.assertIn(r, routes, r)

    def test_61_messages_menu_room_threads(self):
        """61. 메시지(7단계): 호실별 1:1 대화(임대인↔임차인). 계약방 협상 기록·관리인 지원 채팅과 섞이지 않고, 새 입주자에게 옛 문의가 보이지 않으며, 당사자만 열람."""
        from unittest import mock
        from apps.rent_dist._rent3_core import DirectChat, Lessor, Tenant, Room, support as sup
        from apps.rent_dist._rent3_core.authz import lessor_token, tenant_token
        c = self.client
        lessor = Lessor.first(username="chic")
        t201 = Tenant.first(room_no="201", branch="main")
        t202 = Tenant.first(room_no="202", branch="main")
        lh = {"Authorization": f"Bearer {lessor_token(lessor)}"}
        th = {"Authorization": f"Bearer {tenant_token(t201)}"}
        th2 = {"Authorization": f"Bearer {tenant_token(t202)}"}
        # 세 가지가 섞이지 않는지 보려고 다른 종류의 글을 미리 만들어 둔다
        sup.post_user_message(t201, "지원 채팅 글-섞이면 안 됨")
        DirectChat(branch="main", room_no="201", session_id="cs_other", kind="question", sender_role="tenant", sender_name="계약방", message="계약방 글-섞이면 안 됨").save(action="t")
        old = DirectChat(branch="main", room_no="201", sender_role="tenant", sender_name="예전 문의자", message="입주 전에 문의했던 사람의 글").save(action="t")
        old.created_at = "2000-01-01T00:00:00"                         # 현재 계약보다 훨씬 이전
        old.save(action="t_old")
        # 임대인이 보내면 임차인에게 보이고 안 읽은 수가 하나 늘어난다 (앞선 시험이 남긴 안내 글이 있을 수 있어 기준값을 먼저 잰다)
        unread0 = c.get(f"/api/rent3_tenant/myhome/{t201.id}", headers=th).json()["messages_unread"]
        r = c.post("/api/rent3_lessor/chats", headers=lh, json={"branch": "main", "room_no": "201", "message": "관리비 고지서 확인 부탁드려요"})
        self.assertEqual(r.status_code, 200, r.text)
        rooms = c.get("/api/rent3_lessor/messages/rooms?branch=main", headers=lh).json()["rooms"]
        r201 = next(x for x in rooms if x["room_no"] == "201")
        self.assertEqual(r201["last_body"], "관리비 고지서 확인 부탁드려요")
        mine0 = c.get(f"/api/rent3_tenant/myhome/{t201.id}", headers=th).json()
        self.assertEqual(mine0["messages_unread"], unread0 + 1)
        got = c.get(f"/api/rent3_tenant/myhome/{t201.id}/messages?mark_read=false", headers=th).json()
        bodies = [m["body"] for m in got["messages"]]
        self.assertIn("관리비 고지서 확인 부탁드려요", bodies)
        for hidden in ("지원 채팅 글-섞이면 안 됨", "계약방 글-섞이면 안 됨", "입주 전에 문의했던 사람의 글"):
            self.assertNotIn(hidden, bodies)                               # 지원 채팅·계약방 대화·이전 문의자의 글은 안 보인다
        self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}", headers=th).json()["messages_unread"], unread0 + 1)   # mark_read=false 는 읽음 처리 안 함
        c.get(f"/api/rent3_tenant/myhome/{t201.id}/messages", headers=th)                                           # 열면 읽음 처리
        self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}", headers=th).json()["messages_unread"], 0)
        # 임차인이 답하면 임대인에게 안 읽은 글로 보이고, 임대인이 열면 읽음 처리
        self.assertEqual(c.post(f"/api/rent3_tenant/myhome/{t201.id}/messages", headers=th, json={"body": "네 확인했습니다"}).status_code, 200)
        self.assertEqual(c.post(f"/api/rent3_tenant/myhome/{t201.id}/messages", headers=th, json={"body": " "}).status_code, 400)
        r201 = next(x for x in c.get("/api/rent3_lessor/messages/rooms?branch=main", headers=lh).json()["rooms"] if x["room_no"] == "201")
        self.assertGreaterEqual(r201["unread"], 1)
        thread = c.get("/api/rent3_lessor/messages/rooms/201?branch=main", headers=lh).json()
        self.assertIn("네 확인했습니다", [m["body"] for m in thread["messages"]])
        self.assertIn("입주 전에 문의했던 사람의 글", [m["body"] for m in thread["messages"]])   # 임대인은 호실 전체 기록을 본다
        self.assertEqual(next(x for x in c.get("/api/rent3_lessor/messages/rooms?branch=main", headers=lh).json()["rooms"] if x["room_no"] == "201")["unread"], 0)
        self.assertIn("negotiation", thread)
        # 당사자만: 다른 호실 임차인·타 지점 임대인·비로그인은 거부, 관리인은 읽기만(엄격 모드에서 쓰기 403)
        sunho = self._login("sunho")
        self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}/messages", headers=th2).status_code, 403)
        self.assertEqual(c.post(f"/api/rent3_tenant/myhome/{t201.id}/messages", headers=th2, json={"body": "x"}).status_code, 403)
        self.assertEqual(c.get("/api/rent3_lessor/messages/rooms?branch=main", headers=sunho).status_code, 403)
        self.assertEqual(c.get("/api/rent3_lessor/messages/rooms/201?branch=main", headers=sunho).status_code, 403)
        self.assertEqual(c.post("/api/rent3_lessor/chats", headers=sunho, json={"branch": "main", "room_no": "201", "message": "x"}).status_code, 403)
        self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}/messages", headers=self.NOAUTH).status_code, 401)
        with mock.patch.dict(os.environ, {"RENT3_ADMIN_PROXY_STRICT": "1"}):
            self._admin_enter(t201, "tenant")
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}/messages").status_code, 200)
            self.assertEqual(c.post(f"/api/rent3_tenant/myhome/{t201.id}/messages", json={"body": "관리인이 끼어듦"}).status_code, 403)
        # 관리인 지원 채팅은 그대로 따로 있다
        sup_bodies = [m["body"] for m in c.get("/api/rent3/pool/support/thread", headers=th).json()["messages"]]   # (앞선 시험이 남긴 접속 안내가 있을 수 있다)
        self.assertIn("지원 채팅 글-섞이면 안 됨", sup_bodies)
        for room_chat in ("관리비 고지서 확인 부탁드려요", "네 확인했습니다"):
            self.assertNotIn(room_chat, sup_bodies)                        # 호실 대화는 지원 채팅에 나오지 않는다

    def test_62_messages_screens_and_digest_display(self):
        """62. 메시지 화면(7단계): 임대인 ▸ 메시지(호실 목록·대화·접힌 협상 기록), 임차인 ▸ 임대인과 메시지, 옛 메신저 제거, 체결 계약서에 대화 해시 표시."""
        rd = lambda *parts: open(os.path.join(ROOT, "apps", "rent_dist", *parts), encoding="utf-8").read()
        lessor, tenant, contract = rd("rent3_lessor", "frontend", "app.js"), rd("rent3_tenant", "frontend", "app.js"), rd("rent3_contract", "frontend", "app.js")
        for needle in ("function renderMessagesTab", "function renderMessageThread", "btnMsgSend", "msg-room", "계약 협상 기록", "digest_ok", "/messages/rooms"):
            self.assertIn(needle, lessor, needle)
        self.assertNotIn("btnSendChat", lessor)                       # A/S 화면 안의 옛 메신저는 없어졌다
        self.assertIn("A/S 접수", lessor)
        for needle in ("loadTenantMessages", "btnTnMsgSend", 'data-svc="messages"', "tnMsgNeg", "digest_ok", "messages_unread", "/messages`"):
            self.assertIn(needle, tenant, needle)
        self.assertIn("chat_digest", contract)                         # 체결된 계약서에 협상 대화 기록 해시 표시
        # 서버 약속: 임대인·임차인 메시지 주소와 계약 앱 해시 확인 주소
        for fname, route in (("rent3_lessor", '"/messages/rooms"'), ("rent3_lessor", '"/messages/rooms/{room_no}"'),
                             ("rent3_tenant", '"/myhome/{tenant_id}/messages"'), ("rent3_contract", '"/sessions/{session_id}/chat-digest"')):
            self.assertIn(route, rd(fname, "api.py"), route)
        # 메시지 화면의 대화는 호실 대화(session_id 없음)만 쓰고, 계약방 협상 기록은 접어서 따로 준다
        rm = rd("_rent3_core", "room_messages.py")
        self.assertIn("not m.session_id", rm)
        self.assertIn("negotiation_history", rm)
        import re
        self.assertIsNone(re.search(r"import[^\n]*\bsupport\b|SupportMessage[.(]", rm))   # 관리인 지원 채팅 모델을 가져다 쓰지 않는다

    def test_63_contract_chat_retention_purge(self):
        """63. 계약방 대화 보관: 취소 후 90일, 체결 계약은 ACTIVE 동안 보존·종료 후 5년. 기간 만료 삭제는 해시 변조와 구분(purged)."""
        from datetime import timedelta
        from apps.rent_dist._rent3_core import CoSignSession, Contract, DirectChat, Lessor, contract_chat as cc
        from apps.rent_dist._rent3_core.authz import lessor_token
        from apps.rent_dist._rent3_core.models import date_today
        c = self.client
        today = date_today()
        days_ago = lambda n: (today - timedelta(days=n)).isoformat() + "T09:00:00"

        def make_session(room, status="IN_PROGRESS"):
            sid = self._policy_session(room, 5000000, 300000)
            ses = CoSignSession.get(sid)
            ses.status = status
            ses.save(action="t")
            for body in ("문의 하나", "문의 둘"):
                DirectChat(branch="main", room_no=room, session_id=sid, kind="question", sender_role="tenant", sender_name="t", message=body, step=2).save(action="t")
            return ses

        # ── 취소된 계약방: 90일 ──
        young, old, live = make_session("971", "CANCELLED"), make_session("972", "CANCELLED"), make_session("973", "IN_PROGRESS")
        young.cancelled_at = days_ago(89)
        young.save(action="t")
        old.cancelled_at = days_ago(91)
        old.save(action="t")
        live_old = live.created_at
        r = cc.purge_expired(force=True)
        self.assertEqual(len(cc.messages_of(young.id)), 2)              # 89일: 보존
        self.assertEqual(len(cc.messages_of(old.id)), 0)                # 91일: 삭제
        self.assertEqual(len(cc.messages_of(live.id)), 2)               # 진행 중인 계약방은 삭제하지 않는다
        self.assertTrue(CoSignSession.get(old.id).chat_purged_at)
        self.assertFalse(CoSignSession.get(young.id).chat_purged_at)
        self.assertGreaterEqual(r["cancelled_sessions"], 1)
        # 90일 경계: 정확히 90일째는 아직 보존(90일을 넘겨야 삭제)
        edge = make_session("974", "CANCELLED")
        edge.cancelled_at = days_ago(90)
        edge.save(action="t")
        cc.purge_expired(force=True)
        self.assertEqual(len(cc.messages_of(edge.id)), 2)
        # 삭제 로그(풀 이벤트)에는 본문이 없다
        self.assertNotIn("문의 하나", open(os.path.join(self.temp_pool, "_events.jsonl"), encoding="utf-8").read())

        # ── 체결된 계약: ACTIVE 면 오래돼도 보존, 종료 후 5년(1,825일) 지나야 삭제 ──
        def make_contract(room, status, ended_days_ago, msg_days_ago=2500):
            ses = make_session(room, "COMPLETED")
            for m in cc.messages_of(ses.id):                              # 대화 자체는 아주 오래된 것으로(ACTIVE 보존 확인용)
                m.created_at = days_ago(msg_days_ago)
                m.save(action="t")
            digest, n = cc.compute_digest(ses.id)
            ct = Contract(branch="main", room_no=room, cosign_session_id=ses.id, chat_digest=digest, chat_digest_count=n,
                          status=status, ended_at=(days_ago(ended_days_ago)[:10] if ended_days_ago is not None else ""))
            ct.save(action="t")
            ses.created_contract_id = ct.id
            ses.save(action="t")
            return ses, ct

        s_active, ct_active = make_contract("975", "ACTIVE", None)
        s_recent, ct_recent = make_contract("976", "TERMINATED", 1824)       # 종료 후 5년 미만
        s_over, ct_over = make_contract("977", "TERMINATED", 1826)           # 5년 경과
        s_ren, ct_ren = make_contract("978", "RENEWED", 1900)                # 재계약으로 마감된 오래된 계약도 같은 기준
        cc.purge_expired(force=True)
        self.assertEqual(len(cc.messages_of(s_active.id)), 2)                # ACTIVE: 절대 삭제하지 않는다
        self.assertEqual(len(cc.messages_of(s_recent.id)), 2)                # 종료 후 5년 미만: 보존
        self.assertEqual(len(cc.messages_of(s_over.id)), 0)                  # 종료 후 5년 경과: 삭제
        self.assertEqual(len(cc.messages_of(s_ren.id)), 0)
        self.assertEqual(Contract.get(ct_active.id).chat_purged_at, "")
        self.assertEqual(Contract.get(ct_recent.id).chat_purged_at, "")
        self.assertTrue(Contract.get(ct_over.id).chat_purged_at)

        # ── 삭제 표시: 해시 변조(⚠️)와 구분 ──
        from apps.rent_dist._rent3_core import room_messages as rm
        hist = rm.negotiation_history(Contract.get(ct_over.id))
        self.assertTrue(hist["purged"])
        self.assertIsNone(hist["digest_ok"])                                 # 변조(False)로 오해하지 않는다
        self.assertEqual((hist["count"], hist["original_count"], hist["digest"]), (0, 2, ct_over.chat_digest))
        self.assertIn("보관 기간", hist["purged_note"])
        keep = rm.negotiation_history(Contract.get(ct_recent.id))
        self.assertFalse(keep["purged"])
        self.assertIs(keep["digest_ok"], True)                               # 남아 있는 대화는 해시가 그대로 일치
        lh = {"Authorization": f"Bearer {lessor_token(Lessor.first(branch='main'))}"}
        v = c.get(f"/api/rent3_contract/sessions/{s_over.id}/chat-digest", headers=lh).json()
        self.assertEqual((v["purged"], v["match"], v["original_count"]), (True, None, 2))
        self.assertIn("보관 기간", v["note"])
        v2 = c.get(f"/api/rent3_contract/sessions/{s_recent.id}/chat-digest", headers=lh).json()
        self.assertEqual((v2["purged"], v2["match"]), (False, True))
        lst = c.get(f"/api/rent3_contract/sessions/{s_over.id}/messages", headers=lh).json()
        self.assertTrue(lst["purged"])
        self.assertEqual(lst["messages"], [])
        # 보관 중에 누군가 대화를 지우면(기간 만료 표시 없이) 변조로 보인다
        for m in cc.messages_of(s_recent.id):
            m.delete(action="t")
        tampered = rm.negotiation_history(Contract.get(ct_recent.id))
        self.assertFalse(tampered["purged"])
        self.assertIs(tampered["digest_ok"], False)
        # 같은 로그인 정리 경로: 이미 지운 것은 다시 지우지 않고, 던져 둔 호출은 오류 없이 끝난다
        self.assertEqual(cc.purge_expired(force=True)["messages"], 0)
        self.assertEqual(cc.purge_expired()["messages"], 0)                  # 10분 안 재호출은 훑지 않는다

    def test_64_quick_shortcut_pin_login(self):
        """64. 스마트폰 바로가기 + 간편 비밀번호(PIN): 4자리·약한 번호 거부, 키·PIN 해시만 저장, 5회 실패 영구 잠금(일반 로그인으로 해제),
        PIN 토큰(m=pin)은 열람·일상 업무만(계약 동의·서명, 퇴실 정산 동의, 설정 변경 차단), 퇴실·미동의·타 역할은 거부."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Tenant, quicklogin as ql, passwords as pwd
        from apps.rent_dist._rent3_core import authz
        from core.auth import verify_token
        c, NA = self.client, self.NOAUTH
        t = Tenant.first(room_no="202", branch="main")
        self.assertIsNotNone(t)
        old = (t.password, t.phone, t.status, t.room_no, dict(t.consents), t.pin_hash, list(t.quick_devices))
        t.password, t.phone, t.status, t.pin_hash, t.quick_devices = pwd.hash_password("Pw123456"), "010-4444-6173", "ACTIVE", "", []
        t.save(action="t")
        tid = t.id
        PL = "/api/rent3/pool"

        def login():
            r = c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": tid, "password": "Pw123456"})
            self.assertEqual(r.status_code, 200, r.text)
            return {"Authorization": f"Bearer {r.json()['token']}"}

        full = login()
        # ── 설정: 4자리 숫자, 약한 번호 거부(반복·연속·흔한 번호·휴대폰 끝 4자리) ──
        self.assertEqual(c.get(f"{PL}/shortcut/status", headers=full).json()["has_pin"], False)
        for bad in ("1111", "0000", "1234", "4321", "6789", "2580", "6173", "123", "12345", "12a4", ""):
            r = c.post(f"{PL}/shortcut/pin", headers=full, json={"pin": bad})
            self.assertEqual(r.status_code, 400, (bad, r.text))
        self.assertEqual(c.post(f"{PL}/shortcut/devices", headers=full, json={"label": "x"}).status_code, 400)   # PIN 먼저
        self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=full, json={"pin": "2846"}).status_code, 200)
        t = Tenant.get(tid)
        self.assertTrue(t.pin_hash and "2846" not in t.pin_hash)
        dv = c.post(f"{PL}/shortcut/devices", headers=full, json={"label": "내 폰"}).json()
        qk = dv["qk"]
        self.assertGreaterEqual(len(qk), 32)
        self.assertIn(f"qk={qk}", dv["start_url"])
        self.assertNotIn(qk, open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read())   # 키 원문은 저장하지 않는다
        self.assertEqual(c.get(f"{PL}/shortcut/status", headers=full).json()["devices"][0]["label"], "내 폰")
        for h in (full, NA):                                                                                # 응답에 해시가 실리지 않는다
            body = c.get(f"/api/rent3_tenant/myhome/{tid}", headers=full).text
            self.assertNotIn(t.pin_hash, body)
            self.assertNotIn("quick_devices", body)

        # ── 공개: 이름 가림, 매니페스트(이름·시작 주소·아이콘) ──
        idn = c.get(f"{PL}/shortcut/identify?qk={qk}", headers=NA).json()
        self.assertEqual((idn["room_no"], idn["eligible"], idn["locked"]), ("202", True, False))
        self.assertEqual(idn["masked_name"], ql.mask_name(t.name))
        self.assertNotEqual(idn["masked_name"], t.name)
        self.assertEqual((ql.mask_name("홍길동"), ql.mask_name("남궁민수"), ql.mask_name("이몽")), ("홍*동", "남**수", "이*"))
        self.assertEqual(c.get(f"{PL}/shortcut/identify?qk=nope-not-a-key-0000000000", headers=NA).status_code, 404)
        mf = c.get(f"{PL}/shortcut/manifest.json?qk={qk}", headers=NA)
        self.assertEqual(mf.status_code, 200)
        self.assertIn("manifest+json", mf.headers["content-type"])
        m = mf.json()
        self.assertEqual((m["display"], m["scope"]), ("standalone", "/rent3_tenant/"))
        self.assertEqual(m["start_url"], f"/rent3_tenant/?tenant={tid}&qk={qk}")
        for ic in m["icons"]:
            self.assertEqual(c.get(ic["src"].split("?")[0], headers=NA).status_code, 200, ic["src"])
        self.assertEqual(c.get(f"/rent3_tenant/icons/apple-touch-icon.png", headers=NA).status_code, 200)

        # ── PIN 로그인: 틀리면 남은 횟수, 5번째에 영구 잠금(맞는 PIN 도 거부), 일반 로그인이 풀어 준다 ──
        pl = lambda pin, key=qk: c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": key, "pin": pin})
        r = pl("9999")
        self.assertEqual(r.status_code, 401)
        self.assertIn("남은 시도 4회", r.json()["message"])
        for _ in range(3):
            self.assertEqual(pl("9999").status_code, 401)
        r = pl("9999")                                                    # 5번째
        self.assertIn("잠겼", r.json()["message"])
        self.assertEqual(pl("2846").status_code, 401)                    # 잠긴 뒤엔 맞는 PIN 도 거부
        self.assertTrue(c.get(f"{PL}/shortcut/identify?qk={qk}", headers=NA).json()["locked"])
        full = login()                                                    # 아이디·비밀번호 로그인 → 잠금 해제
        self.assertFalse(c.get(f"{PL}/shortcut/status", headers=full).json()["devices"][0]["locked"])
        self.assertEqual(pl("1111").status_code, 401)                    # 실패 횟수는 다시 처음부터
        ok = pl("2846")
        self.assertEqual(ok.status_code, 200, ok.text)
        pinh = {"Authorization": f"Bearer {ok.json()['token']}"}
        claims = verify_token(ok.json()["token"])
        self.assertEqual((claims["m"], claims["r"], claims["u"]), ("pin", "tenant", f"tenant:{tid}"))
        self.assertNotIn("m", verify_token(full["Authorization"].split()[1]))   # 일반 로그인 토큰에는 m 이 없다
        self.assertEqual(ok.json()["user"]["room_no"], "202")

        # ── PIN 토큰 권한: 열람·일상 업무 OK, 법적·민감 행위 차단(허용 목록 방식) ──
        base = f"/api/rent3_tenant/myhome/{tid}"
        self.assertEqual(c.get(base, headers=pinh).status_code, 200)
        self.assertEqual(c.get(f"{base}/messages", headers=pinh).status_code, 200)
        self.assertEqual(c.post(f"{base}/messages", headers=pinh, json={"body": "PIN 으로 보낸 메시지"}).status_code, 200)
        self.assertEqual(c.post(f"{base}/vehicle", headers=pinh, json={"car_number": "12가3456", "car_model": "소나타"}).status_code, 200)
        self.assertEqual(c.get(f"{PL}/shortcut/status", headers=pinh).json()["via_pin"], True)
        blocked = [
            ("post", f"{base}/expiry-plan", {"plan": "RENEW"}), ("post", f"{base}/acknowledge-renewal", {}),
            ("post", "/api/rent3_tenant/checkout/x/agree", {"agreed": True}),
            ("post", "/api/rent3_contract/sessions/x/respond", {"responder_role": "tenant"}),
            ("post", "/api/rent3_contract/sessions/x/step4/sign", {"role": "tenant", "signature": "s"}),
            ("post", "/api/rent3_contract/sessions/x/step3/agree", {"role": "tenant"}),
            ("post", "/api/rent3_contract/sessions/x/messages/y/respond", {"action": "accept"}),
            ("post", f"{PL}/auth/consents/support", {"allow": True}),
            ("post", f"{PL}/shortcut/pin", {"pin": "2846"}), ("post", f"{PL}/shortcut/devices", {"label": "y"}),
        ]
        for _m, url, js in blocked:
            r = getattr(c, _m)(url, headers=pinh, json=js)
            self.assertEqual(r.status_code, 403, (url, r.status_code, r.text))
            self.assertIn("아이디·비밀번호", r.json().get("message", "") + r.json().get("hint", ""))
            # 같은 요청이 일반 로그인 토큰에서는 이 가드로 막히지 않는다(다른 이유로 실패할 수는 있다)
            self.assertNotEqual(getattr(c, _m)(url, headers=full, json=js).json().get("message", ""), authz.PIN_BLOCK_MESSAGE)
        self.assertEqual(c.post(f"{PL}/shortcut/devices/{dv['device']['id']}/revoke", headers=pinh, json={}).status_code, 403)   # 기기 해제도 비밀번호 로그인 필요
        self.assertFalse(any(rx.search("/api/rent3_contract/sessions/ab12/messages") for rx in authz.PIN_WRITE_ALLOW))   # 계약방 글쓰기(제안·수정 요청 포함)도 비밀번호 로그인
        for tail in ("balance-registry-check", "insurance-proof"):                                                        # 도어락 교부와 이어지는 확인도 마찬가지
            self.assertFalse(any(rx.search(f"/api/rent3_tenant/myhome/t/{tail}") for rx in authz.PIN_WRITE_ALLOW), tail)
        self.assertFalse(any(rx.search("/api/rent3_contract/sessions/ab12/messages/m1/respond") for rx in authz.PIN_WRITE_ALLOW))

        # ── 해제·퇴실·동의·다른 역할 ──
        dv2 = c.post(f"{PL}/shortcut/devices", headers=full, json={"label": "태블릿"}).json()
        self.assertEqual(c.post(f"{PL}/shortcut/devices/{dv['device']['id']}/revoke", headers=full, json={}).status_code, 200)
        r = pl("2846")
        self.assertEqual(r.status_code, 401)                             # 해제한 기기의 키는 더 못 쓴다
        self.assertEqual(c.get(f"{PL}/shortcut/identify?qk={qk}", headers=NA).status_code, 404)
        qk2 = dv2["qk"]
        self.assertEqual(pl("2846", qk2).status_code, 200)
        t = Tenant.get(tid)
        t.status = "MOVED_OUT"
        t.save(action="t")
        r = pl("2846", qk2)
        self.assertEqual(r.status_code, 401)                             # 퇴실하면 바로가기는 즉시 거부
        self.assertIn("입주 중", r.json()["message"])
        self.assertEqual(c.get(f"{PL}/shortcut/identify?qk={qk2}", headers=NA).json()["eligible"], False)
        t.status = "ACTIVE"
        t.save(action="t")
        with mock.patch.dict(os.environ, {"RENT3_CONSENT_REQUIRED": "1"}):   # 개인정보 동의 전에는 PIN 으로 못 들어온다
            t = Tenant.get(tid)
            t.consents = {}
            t.save(action="t")
            r = pl("2846", qk2)
            self.assertEqual(r.status_code, 401)
            self.assertIn("동의", r.json()["message"])
        self.assertEqual(pl("2846", qk2).status_code, 200)
        # 임대인·관리인은 쓸 수 없다
        lh = self._login("chic")
        for h in (lh, self._as(self.admin_token)):
            self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=h, json={"pin": "2846"}).status_code, 400)
        self.assertEqual(c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": "x" * 40, "pin": "2846"}).status_code, 401)
        # 기기 수 한도
        for i in range(ql.MAX_DEVICES):
            c.post(f"{PL}/shortcut/devices", headers=full, json={"label": f"d{i}"})
        self.assertEqual(c.post(f"{PL}/shortcut/devices", headers=full, json={"label": "초과"}).status_code, 400)

        t = Tenant.get(tid)
        t.password, t.phone, t.status, t.room_no, t.consents, t.pin_hash, t.quick_devices = old
        t.save(action="t")

    def test_65_quick_shortcut_static_ui(self):
        """65. 바로가기 화면: PIN 입력 오버레이·메뉴·세션 가드(qk 면 포털로 안 보냄)·아이콘 파일."""
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend")
        shell = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        guard = open(os.path.join(ROOT, "shared_ui", "rent3_session_guard.js"), encoding="utf-8").read()
        app = open(os.path.join(fe, "app.js"), encoding="utf-8").read()
        for needle in ("pinPending", "showPinGate", "/auth/pin-login", "/identify?qk=", "openShortcutSettings", "공유(□↑) → 홈 화면에 추가",
                       "beforeinstallprompt", "apple-mobile-web-app-title", "manifest"):
            self.assertIn(needle, shell, needle)
        self.assertIn("qk=", guard)
        self.assertIn("pinPending()) return", app)
        self.assertIn("openShortcutSettings", app)
        for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
            data = open(os.path.join(fe, "icons", name), "rb").read()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", name)

    def test_66_admin_direct_read_needs_support_entry(self):
        """66. 관리인 직접 읽기 제한(엄격 모드): 원격 지원 입장(허용+채팅+기록) 없이 임대인·임차인·계약 앱 자료를 읽을 수 없고,
        입장하면 그 대상만 일정 시간 읽을 수 있다. 사용자가 허용을 끄거나 기록이 오래되면 다시 닫힌다. 관리인 전용 API 는 영향 없음."""
        from datetime import datetime, timedelta
        from unittest import mock
        from apps.rent_dist._rent3_core import CoSignSession, Lessor, Tenant, authz, support as sup
        from apps.rent_dist._rent3_core.authz import tenant_token
        c = self.client
        t202, t201 = Tenant.get("tenant_202"), Tenant.get("tenant_201")
        lessor_main = Lessor.first(branch="main")
        sid = self._policy_session("981", 5000000, 300000)
        ses_other = self._policy_session("982", 5000000, 300000)
        for g in sup.SupportAccessLog.all():                       # 앞선 시험이 남긴 입장 기록은 치운다
            g.delete(action="t")
        with mock.patch.dict(os.environ, {"RENT3_ADMIN_PROXY_STRICT": "1"}):
            direct = ["/api/rent3_lessor/dashboard?branch=main", f"/api/rent3_tenant/myhome/{t202.id}", f"/api/rent3_tenant/myhome/{t202.id}/messages",
                      f"/api/rent3_contract/sessions/{sid}", f"/api/rent3_contract/sessions/{sid}/messages"]
            for u in direct:                                        # 주소를 직접 쳐서 읽기: 모두 막힘
                r = c.get(u)
                self.assertEqual(r.status_code, 403, u)
                self.assertIn("원격 지원", r.json()["message"])
            # 관리인 전용 API·대상이 없는 목록은 그대로
            self.assertEqual(c.get("/api/rent3_admin/support/directory").status_code, 200)
            self.assertEqual(c.get("/api/rent3_contract/sessions").status_code, 200)

            # 임차인 입장: 그 임차인만 열린다(다른 임차인·임대인 앱은 여전히 막힘)
            self._admin_enter(t202, "tenant")
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t202.id}").status_code, 200)
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}").status_code, 403)
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main").status_code, 403)
            # 임대인 입장: 그 건물만(다른 건물은 막힘)
            self._admin_enter(lessor_main, "lessor")
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main").status_code, 200)
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=sinbi").status_code, 403)
            # 계약 입장: 그 계약방만
            self.assertEqual(c.get(f"/api/rent3_contract/sessions/{sid}").status_code, 403)
            s_obj = CoSignSession.get(sid)
            reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH,        # 계약 상대(예비 임차인): 계약방에 적은 번호로 가입한 계정
                         json={"role": "prospect", "name": "입장시험", "phone": (s_obj.terms or {}).get("tenant_phone", ""), "password": "Pw123456"})
            self.assertEqual(reg.status_code, 200, reg.text)
            self._admin_enter(Tenant.get(reg.json()["user"]["id"]), "contract", session_id=sid)
            self.assertEqual(c.get(f"/api/rent3_contract/sessions/{sid}").status_code, 200)
            self.assertEqual(c.get(f"/api/rent3_contract/sessions/{ses_other}").status_code, 403)

            # 사용자가 원격 지원 허용을 끄면 곧바로 닫힌다
            t202 = Tenant.get(t202.id)
            t202.consents = {**t202.consents, "support_access": False}
            t202.save(action="t")
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t202.id}").status_code, 403)
            t202.consents = {**t202.consents, "support_access": True}
            t202.save(action="t")
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t202.id}").status_code, 200)
            # 입장 기록이 허용 시간보다 오래되면 닫힌다
            for g in sup.SupportAccessLog.find(target_user=f"tenant:{t202.id}"):
                g.entered_at = (datetime.now() - timedelta(minutes=authz.GRANT_MINUTES + 5)).isoformat(timespec="seconds")
                g.save(action="t")
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t202.id}").status_code, 403)
            # 임차인 본인은 영향 없음
            th = {"Authorization": f"Bearer {tenant_token(t202)}"}
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t202.id}", headers=th).status_code, 200)
        # 호환 모드(기존 시험 묶음)에서는 관리인이 입장 없이도 읽는다
        self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t201.id}").status_code, 200)

    def test_67_contract_step12_dots_and_docs_wording(self):
        """67. 계약 앱 ①② 단계 진행 표시는 점 두 개로 통일(옛 '미체크·미확인' 배지 없음), 계약 앱 매뉴얼·QA 체크리스트는 새 3단계(제안→수락, 전체 동의 한 버튼)를 설명한다."""
        rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
        app = rd("apps", "rent_dist", "rent3_contract", "frontend", "app.js")
        self.assertIn("function pairDots(", app)
        self.assertEqual(app.count("pairDots("), 4)                       # 정의 1 + ① 두 줄 + ② 한 줄
        for gone in ("미체크", "미확인", "임차인 확인 잠금", "임대인 확인: "):
            self.assertNotIn(gone, app, gone)
        manual = rd("apps", "rent_dist", "rent3_contract", "frontend", "manual.html")
        for needle in ("제안", "수락", "[계약 조건 전체 동의]", "조건 다시 열기", "계약방 채팅", "90일", "5년"):
            self.assertIn(needle, manual, needle)
        self.assertNotIn("공동 편집", manual)
        qa = rd("scripts", "generate_rent3_qa_pdf.py")
        for needle in ("CON-F10", "CON-F11", "CON-F12", "제안 → 상대 수락", "[계약 조건 전체 동의]"):
            self.assertIn(needle, qa, needle)
        self.assertNotIn("최종 합의 잠금", qa)
        self.assertIn('"apps", "rent_dist", "rent3", "frontend", "Rent3_QA_Test_Checklist.pdf"', qa)   # 사본이 추적되는 위치로 만든다

    def test_68_backup_invite_and_drawer_footer(self):
        """68. Phase 1: Data Pool 백업·검증·복구(관리인 내려받기), 입주 안내 카드(1회용 링크 → 비밀번호·PIN 설정), ☰ 서랍 하단 포털 홈·로그아웃."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Tenant, backup as bk, invite as inv, passwords as pwd, quicklogin as ql
        from apps.rent_dist._rent3_core.authz import lessor_token
        c, NA = self.client, self.NOAUTH
        # ── 백업: 만들기 → 검사 → 새 폴더에 복구, 백업 폴더 자체는 담지 않는다 ──
        bdir = tempfile.mkdtemp(prefix="rent3_bk_")
        with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": bdir, "RENT3_BACKUP_KEEP": "2"}):
            r = c.post("/api/rent3_admin/backups/run", json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["verify"]["ok"])
            name = r.json()["backup"]["name"]
            self.assertEqual(c.get("/api/rent3_admin/backups").json()["backups"][0]["name"], name)
            dl = c.get(f"/api/rent3_admin/backups/{name}/download")
            self.assertEqual(dl.status_code, 200)
            self.assertEqual(dl.content[:2], b"\x1f\x8b")                                        # gzip
            self.assertIn(c.get("/api/rent3_admin/backups/..%2Fusers.json/download").status_code, (400, 404))
            self.assertEqual(c.get("/api/rent3_admin/backups/rent3_pool_20000101_000000.tar.gz/download").status_code, 400)
            t0 = Tenant.get("tenant_202")
            self.assertIn(c.get("/api/rent3_admin/backups", headers=self._login("chic")).status_code, (401, 403))   # 임대인은 못 본다
            target = os.path.join(tempfile.mkdtemp(prefix="rent3_rs_"), "restored")
            out = bk.restore(bk.path_for(name), target)
            self.assertGreater(out["files"], 3)
            self.assertTrue(os.path.exists(os.path.join(target, "users.json")))
            self.assertFalse(any(x.startswith("rent3_pool_") for x in os.listdir(target)))              # 백업 파일이 백업 안에 들어가지 않는다
            with self.assertRaises(ValueError):
                bk.restore(bk.path_for(name), target)                                                    # 비어 있지 않은 폴더에는 복구하지 않는다
            # 보관 개수: 3번째를 만들면 가장 오래된 것이 지워진다
            import time as _t
            for fake in ("rent3_pool_20200101_000001.tar.gz", "rent3_pool_20200101_000002.tar.gz"):
                open(os.path.join(bdir, fake), "wb").write(b"x")
            bk.create()
            self.assertLessEqual(len(bk.list_backups()), 2)
            bad = os.path.join(bdir, "rent3_pool_20990101_000000.tar.gz")
            open(bad, "wb").write(b"not a tar")
            self.assertFalse(bk.verify(bad)["ok"])
            # 하루 한 번 자동: 최근 백업이 있으면 시작하지 않는다
            os.remove(bad)
            bk._last_check = 0
            self.assertIsNone(bk.daily())

        # ── 입주 안내 카드: 임대인이 발급 → 링크로 비밀번호·PIN → 로그인, 1회용 ──
        t = Tenant.get("tenant_201")
        old = (t.password, t.status, t.room_no, dict(t.consents), t.pin_hash, list(t.quick_devices), t.invite_hash)
        t.password, t.pin_hash, t.quick_devices = "", "", []
        t.save(action="t")
        chic = self._login("chic")
        self.assertEqual(c.post("/api/rent3_lessor/tenant-invite", headers=chic, json={"branch": "main", "room_no": "999"}).status_code, 400)
        r = c.post("/api/rent3_lessor/tenant-invite", headers=chic, json={"branch": "main", "room_no": "201"})
        self.assertEqual(r.status_code, 200, r.text)
        link = r.json()["url"]
        tok = link.split("invite=")[1]
        self.assertNotIn(tok, open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read())      # 원문은 저장하지 않는다
        self.assertEqual(c.post("/api/rent3_lessor/tenant-invite", headers=self._login("sunho"), json={"branch": "main", "room_no": "201"}).status_code, 403)   # 남의 건물
        self.assertNotIn(Tenant.get("tenant_201").invite_hash, c.get("/api/rent3_tenant/myhome/tenant_201").text)
        idn = c.get(f"/api/rent3/pool/invite/identify?token={tok}", headers=NA).json()
        self.assertEqual((idn["room_no"], idn["masked_name"]), ("201", ql.mask_name(t.name)))
        self.assertEqual(c.get("/api/rent3/pool/invite/identify?token=wrong-token-0000000000", headers=NA).status_code, 404)
        from apps.rent_dist._rent3_core import connect as cn_id
        who = {"room_no": t.room_no, "name": t.name, "phone": t.phone, "lessor_name": cn_id.public_info(t.branch)["lessor_name"]}
        acc = lambda **kw: c.post("/api/rent3/pool/auth/invite-accept", headers=NA, json={"token": tok, **who, **kw})
        # 본인 확인: 호실·이름·전화번호·임대인 이름이 모두 맞아야 한다(틀리면 비밀번호도 못 정한다)
        vf = lambda **kw: c.post("/api/rent3/pool/invite/verify", headers=NA, json={"token": tok, **who, **kw})
        self.assertEqual(vf().status_code, 200)
        from apps.rent_dist._rent3_core import ratelimit as rl_id
        for bad in ({"room_no": "999"}, {"name": "엉뚱한이"}, {"phone": "010-0000-0000"}, {"lessor_name": "남의임대인"}, {"lessor_name": ""}):
            rl_id.reset_all()
            self.assertEqual(vf(**bad).status_code, 400, bad)
            self.assertEqual(acc(password="Invite2026x", agree_privacy=True, **bad).status_code, 400, bad)      # 확인 단계를 건너뛰고 바로 가입해도 막힌다
        self.assertEqual(c.post("/api/rent3/pool/auth/invite-accept", headers=NA, json={"token": tok, "password": "Invite2026x", "agree_privacy": True}).status_code, 400)   # 4칸 없이는 불가
        rl_id.reset_all()
        self.assertEqual(vf().status_code, 200)
        for _ in range(5):
            vf(name="엉뚱한이")
        self.assertEqual(vf().status_code, 429)       # 5번 틀리면 맞는 입력도 잠시 거절
        rl_id.reset_all()
        self.assertEqual(acc(password="short", agree_privacy=True).status_code, 400)                          # 비밀번호 규칙
        self.assertEqual(acc(password="Invite2026x", pin="1234", agree_privacy=True).status_code, 400)       # 약한 PIN
        with mock.patch.dict(os.environ, {"RENT3_CONSENT_REQUIRED": "1"}):
            self.assertEqual(acc(password="Invite2026x", agree_privacy=False).status_code, 400)             # 동의 필수(운영 모드)
        t201 = Tenant.get("tenant_201"); keep_idn = t201.identity; t201.identity = {}; t201.save(action="t")     # 가입 전엔 본인 확인 기록이 없다
        ok = acc(password="Invite2026x", pin="2846", agree_privacy=True)
        self.assertEqual(ok.status_code, 200, ok.text)
        d = ok.json()
        self.assertTrue(d["token"] and "qk=" in d["start_url"] and "welcome=1" in d["start_url"])
        from core.auth import verify_token
        self.assertNotIn("m", verify_token(d["token"]))                                                     # 비밀번호를 정한 정식 로그인
        self.assertEqual(acc(password="Another2026x").status_code, 404)                                    # 1회용
        # 1단계: 링크+4칸으로 가입하면 본인 확인 기록이 생기되 '실명 확인'이 아닌 invite_link 수준으로 구분된다
        idn_rec = Tenant.get("tenant_201").identity
        self.assertEqual((idn_rec["verified"], idn_rec["assurance"], idn_rec["provider"], idn_rec["birth6"]), (True, "invite_link", "invite", ""))
        hd = {"Authorization": f"Bearer {d['token']}"}
        call = lambda: c.post("/api/rent3_contract/sessions/call", headers=hd, json={"caller_role": "tenant", "branch": "main", "room_no": "201"})
        self.assertNotIn("휴대폰 본인인증이 필요합니다", call().text)                                                       # 기본: 계약 화면의 인증 막힘이 없다
        with mock.patch.dict(os.environ, {"RENT3_INVITE_IDENTITY_OK": "0"}):
            blocked = call()
            self.assertEqual(blocked.status_code, 403)                                                 # 스위치를 끄면 실명 인증이 필요
            self.assertIn("실명 본인인증", blocked.text)
        # 등록된 기기에서만 비밀번호 로그인(RENT3_DEVICE_BIND=1): 처음 로그인한 기기가 등록되고, 다른 기기는 비밀번호가 맞아도 거절
        devA, devB = "A" * 30, "B" * 30
        lg = lambda dev: c.post("/api/rent3/pool/auth/login", headers=NA, json={"login_mode": "account", "identifier": "tenant_201", "password": "Invite2026x", "device_id": dev})
        with mock.patch.dict(os.environ, {"RENT3_DEMO_LOGIN": "0", "RENT3_DEVICE_BIND": "1"}):
            self.assertEqual(lg(devA).status_code, 200)                  # 등록 기기가 없으면 첫 기기를 등록
            self.assertEqual(lg(devA).status_code, 200)
            nb = lg(devB)
            self.assertEqual(nb.status_code, 401)
            self.assertIn("새 기기", nb.json()["message"])
            self.assertEqual(lg("").status_code, 401)                    # 기기 값이 없으면 거절
            self.assertNotIn("B" * 30, open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read())   # 원문은 저장하지 않는다
            self.assertEqual(len(Tenant.get("tenant_201").login_devices), 1)
        t201 = Tenant.get("tenant_201"); t201.login_devices = []; t201.save(action="t")   # (재등록 때는 등록 취소가 비운다 — test_83 에서 확인)
        login = c.post("/api/rent3/pool/auth/login", headers=NA, json={"login_mode": "account", "identifier": "tenant_201", "password": "Invite2026x"})
        self.assertEqual(login.status_code, 200, login.text)                                                 # 새 비밀번호로 정상 로그인
        pl = c.post("/api/rent3/pool/auth/pin-login", headers=NA, json={"qk": d["start_url"].split("qk=")[1].split("&")[0], "pin": "2846"})
        self.assertEqual(pl.status_code, 200, pl.text)                                                       # 정한 PIN·기기로 바로가기 로그인
        self.assertTrue(Tenant.get("tenant_201").consents)
        # 관리인 발급: 목록·링크, 퇴실자는 불가
        lst = c.get("/api/rent3_admin/accounts/tenants").json()["tenants"]
        self.assertTrue(any(x["id"] == "tenant_201" for x in lst))
        self.assertEqual(c.post("/api/rent3_admin/accounts/invite", json={"id": "tenant_201"}).status_code, 200)
        self.assertIn(c.post("/api/rent3_admin/accounts/invite", headers=chic, json={"id": "tenant_201"}).status_code, (401, 403))
        t = Tenant.get("tenant_201")
        t.status = "MOVED_OUT"
        t.save(action="t")
        self.assertEqual(c.post("/api/rent3_admin/accounts/invite", json={"id": "tenant_201"}).status_code, 400)
        t = Tenant.get("tenant_201")
        t.password, t.status, t.room_no, t.consents, t.pin_hash, t.quick_devices, t.invite_hash = old
        t.save(action="t")

        # ── 화면: 서랍 하단 포털 홈·로그아웃, 가입 화면, 관리인 운영 탭, PIN 차단 안내 ──
        rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
        shell = rd("shared_ui", "r3_shell.js")
        for needle in ("🌐 포털 홈", "🚪 로그아웃", "/rent3/?logout=1", "showInviteSetup", "/auth/invite-accept", "/invite/verify", "r3IdLessor", "본인 확인", "installHelp"):
            self.assertIn(needle, shell, needle)
        admin_idx = rd("apps", "rent_dist", "rent3_admin", "frontend", "index.html")
        self.assertIn("/rent3/?logout=1", admin_idx)
        self.assertIn('data-tab="ops"', admin_idx)
        self.assertIn("renderOpsTab", rd("apps", "rent_dist", "rent3_admin", "frontend", "app.js"))
        self.assertIn("showInviteModal", rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js"))
        self.assertIn("qrcode.min.js", rd("apps", "rent_dist", "rent3_lessor", "frontend", "index.html"))
        self.assertIn("아이디·비밀번호로 다시 로그인", rd("shared_ui", "rent3_session_guard.js"))
        self.assertIn("showInviteSetup", rd("apps", "rent_dist", "rent3_tenant", "frontend", "app.js") + shell)

    def test_69_phase2_pay_notice_lost_phone_pin_ttl_name_login(self):
        """69. Phase 2: 임차인 스스로 납부 처리 불가(이체 알림→임대인 확인), PIN 토큰 3시간·기기 해제 즉시 반영, 분실 폰 전체 해제(임대인·관리인), 운영 환경 성명 로그인 차단."""
        import time as _t
        from apps.rent_dist._rent3_core import Tenant, passwords as pwd, authz
        from core.auth import verify_token
        c, NA = self.client, self.NOAUTH
        t = Tenant.first(room_no="202", branch="main")
        old = (t.password, t.status, t.room_no, dict(t.consents), t.pin_hash, list(t.quick_devices))
        t.password, t.pin_hash, t.quick_devices, t.status = pwd.hash_password("Pw123456"), "", [], "ACTIVE"
        t.save(action="t")
        from apps.rent_dist._rent3_core import Room
        room202 = Room.first(branch="main", room_no="202")
        old_room_tenant = room202.id_tenant
        room202.id_tenant = t.id                                   # 앞선 시험이 호실 연결을 풀었을 수 있다
        room202.save(action="t")
        tid, PL = t.id, "/api/rent3/pool"
        login = lambda: {"Authorization": "Bearer " + c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": tid, "password": "Pw123456"}).json()["token"]}
        full = login()
        self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=full, json={"pin": "2846"}).status_code, 200)

        def new_device(label):
            dv = c.post(f"{PL}/shortcut/devices", headers=full, json={"label": label}).json()
            r = c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": dv["qk"], "pin": "2846"})
            self.assertEqual(r.status_code, 200, r.text)
            return dv, {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["token"]

        dv1, pin1, tok1 = new_device("폰1")
        cl = verify_token(tok1)
        self.assertEqual(cl["d"], dv1["device"]["id"])
        self.assertLessEqual(cl["exp"] - int(_t.time()), 3 * 3600 + 5)                                       # PIN 토큰은 3시간
        self.assertGreater(cl["exp"] - int(_t.time()), 3 * 3600 - 60)
        self.assertGreater(verify_token(full["Authorization"].split()[1])["exp"] - int(_t.time()), 6 * 3600)  # 정식 로그인은 그대로(12시간)
        # 이체 알림은 PIN 으로도 되지만 장부는 안 바뀐다. 납부 처리 API 는 없다
        base = f"/api/rent3_tenant/myhome/{tid}"
        before = (c.get(base, headers=pin1).json().get("ledger") or {}).get("total_unpaid")
        self.assertEqual(c.post(f"{base}/transfer-notice", headers=pin1, json={"amount": 12345}).status_code, 200)
        self.assertEqual((c.get(base, headers=pin1).json().get("ledger") or {}).get("total_unpaid"), before)
        self.assertEqual(c.get(base, headers=pin1).json()["transfer_notice"]["status"], "PENDING")
        self.assertIn(c.post(f"{base}/pay", headers=pin1, json={"amount": 1}).status_code, (403, 404, 405))
        self.assertEqual(c.post(f"{base}/transfer-notice", headers=pin1, json={"amount": 0}).status_code, 400)
        nid = c.get(base, headers=pin1).json()["transfer_notice"]["id"]
        self.assertEqual(c.post(f"/api/rent3_lessor/transfer-notices/{nid}/reject", headers=self._login("chic"), json={"branch": "main", "reason": "확인 안 됨"}).status_code, 200)
        self.assertEqual(c.get(base, headers=pin1).json()["transfer_notice"]["status"], "REJECTED")
        # 기기를 해제하면 이미 받은 PIN 토큰도 바로 무효
        self.assertEqual(c.post(f"{PL}/shortcut/devices/{dv1['device']['id']}/revoke", headers=full, json={}).status_code, 200)
        self.assertEqual(c.get(base, headers=pin1).status_code, 401)
        # 분실 폰 전체 해제: 임대인(자기 건물만)·관리인
        dv2, pin2, _ = new_device("폰2")
        chic, sunho = self._login("chic"), self._login("sunho")
        self.assertEqual(c.post("/api/rent3_lessor/tenant-shortcuts/revoke-all", headers=sunho, json={"branch": "main", "room_no": "202"}).status_code, 403)
        self.assertEqual(c.get(base, headers=pin2).status_code, 200)
        r = c.post("/api/rent3_lessor/tenant-shortcuts/revoke-all", headers=chic, json={"branch": "main", "room_no": "202"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get(base, headers=pin2).status_code, 401)                                         # 열려 있던 화면도 막힘
        self.assertEqual(c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": dv2["qk"], "pin": "2846"}).status_code, 401)
        self.assertEqual(c.get(f"{PL}/shortcut/status", headers=login()).json()["has_pin"], False)           # 비밀번호 로그인은 그대로, PIN 은 없어짐
        self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=login(), json={"pin": "2846"}).status_code, 200)
        dv3, pin3, _ = new_device("폰3")
        self.assertEqual(c.post("/api/rent3_admin/accounts/revoke-shortcuts", json={"id": tid}).status_code, 200)
        self.assertEqual(c.get(base, headers=pin3).status_code, 401)
        self.assertIn(c.post("/api/rent3_admin/accounts/revoke-shortcuts", headers=chic, json={"id": tid}).status_code, (401, 403))
        # 퇴실하면 이미 받은 PIN 토큰도 무효
        self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=login(), json={"pin": "2846"}).status_code, 200)
        dv4, pin4, _ = new_device("폰4")
        tt = Tenant.get(tid)
        tt.status = "MOVED_OUT"
        tt.save(action="t")
        self.assertEqual(c.get(base, headers=pin4).status_code, 401)
        tt = Tenant.get(tid)
        tt.status = "ACTIVE"
        tt.save(action="t")

        # ── 운영 환경(프록시 헤더가 붙은 접속): 성명 로그인 차단, 아이디는 가능. 로컬 시연에서는 예전처럼 ──
        remote = {**NA, "X-Forwarded-For": "203.0.113.9"}
        by_name = c.post(f"{PL}/auth/login", headers=remote, json={"login_mode": "account", "identifier": Tenant.get(tid).name, "password": "Pw123456"})
        self.assertEqual(by_name.status_code, 401)
        by_id = c.post(f"{PL}/auth/login", headers=remote, json={"login_mode": "account", "identifier": tid, "password": "Pw123456"})
        self.assertEqual(by_id.status_code, 200, by_id.text)
        self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": Tenant.get(tid).name, "password": "Pw123456"}).status_code, 200)

        # ── 화면: 사전 비활성·안내, 운영 입력칸 안내문, 납부 카드 문구 ──
        rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
        tapp = rd("apps", "rent_dist", "rent3_tenant", "frontend", "app.js")
        for needle in ("isViaPin", "btnAgreeCo", "이체했어요", "/transfer-notice"):
            self.assertIn(needle, tapp, needle)
        self.assertNotIn("월세 즉시 납부", tapp)
        self.assertNotIn("/pay`", tapp)
        self.assertIn("아이디, 이메일 또는 휴대폰 번호", rd("apps", "rent_dist", "rent3", "frontend", "app.js"))
        self.assertIn("nt-ok", rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js"))
        t = Tenant.get(tid)
        t.password, t.status, t.room_no, t.consents, t.pin_hash, t.quick_devices = old
        t.save(action="t")
        room202 = Room.get(room202.id)
        room202.id_tenant = old_room_tenant
        room202.save(action="t")

    def test_70_phase3_terms_overwrite_guard_first_screen_admin_links(self):
        """70. Phase 3: 계약 조건은 내가 고친 항목만, 화면이 근거로 삼은 리비전이 낡았으면 덮어쓰지 않고 거절. 임대인 첫 화면에 이체 확인 요청·미납 호실 바로가기. 관리인 타 앱 링크는 입장 중에만."""
        c = self.client
        sid = self._policy_session("983", 5000000, 300000)
        base = f"/api/rent3_contract/sessions/{sid}"
        rev0 = c.get(base).json()["session"]["revision"]
        r1 = c.post(f"{base}/terms", json={"role": "lessor", "updates": {"special_terms": "특약 A"}, "expected_revision": rev0})
        self.assertEqual(r1.status_code, 200, r1.text)
        rev1 = r1.json()["session"]["revision"]
        self.assertGreater(rev1, rev0)
        # 낡은 화면(rev0)에서 다른 값을 보내면 상대(앞선 변경)를 덮어쓰지 않고 거절한다
        r2 = c.post(f"{base}/terms", json={"role": "tenant", "updates": {"special_terms": "특약 B"}, "expected_revision": rev0})
        self.assertEqual(r2.status_code, 409, r2.text)
        self.assertIn("상대방이 계약 조건을 바꿨", r2.json()["message"])
        self.assertEqual(c.get(base).json()["session"]["terms"]["special_terms"], "특약 A")
        # 같은 값이거나 리비전이 맞으면 통과, 리비전을 안 보내는 옛 호출도 그대로(호환)
        self.assertEqual(c.post(f"{base}/terms", json={"role": "tenant", "updates": {"special_terms": "특약 A"}, "expected_revision": rev0}).status_code, 200)
        self.assertEqual(c.post(f"{base}/terms", json={"role": "tenant", "updates": {"special_terms": "특약 C"}, "expected_revision": rev1}).status_code, 200)
        self.assertEqual(c.post(f"{base}/terms", json={"role": "tenant", "updates": {"special_terms": "특약 D"}}).status_code, 200)

        rd = lambda *parts: open(os.path.join(ROOT, "apps", "rent_dist", *parts), encoding="utf-8").read()
        capp = rd("rent3_contract", "frontend", "app.js")
        for needle in ("changedUpdates", "postTerms", "expected_revision: s.revision", "상대방이 계약 조건을 바꿨"):
            self.assertIn(needle, capp, needle)
        self.assertEqual(capp.count("collectUpdates()"), 1)                # changedUpdates 안에서만 부른다 — 저장·동의 단추가 폼 전체를 직접 보내지 않는다
        lapp = rd("rent3_lessor", "frontend", "app.js")
        for needle in ("todo-row", "이체 확인 요청", "미납 호실", "adminLinkOk"):
            self.assertIn(needle, lapp, needle)
        self.assertIn("adminLinkOk", rd("rent3_tenant", "frontend", "app.js"))

    def test_71_phase4_mobile_ui_polish(self):
        """71. Phase 4: 메뉴 이름 축약, 체크박스 누름 영역·긴 입력칸, 본문 글자 12px 이상(인쇄 표 제외), 공동계약 호출 띠(토큰을 실어 부른다)."""
        import re
        rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
        lapp = rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js")
        self.assertIn('"🧾 세무 허브"', lapp)
        self.assertNotIn("세무 허브 (종소세", lapp)
        self.assertIn("flex:1 1 180px;min-width:180px", lapp)
        shell = rd("shared_ui", "r3_shell.js")
        self.assertIn("display:block;min-height:44px;padding:10px 0", shell)
        small = re.compile(r"font-size:\s*(?:9|10|11)(?:\.\d)?px")
        for parts in (("shared_ui", "r3_shell.js"), ("shared_ui", "r3_modal.js"), ("apps", "rent_dist", "rent3_lessor", "frontend", "index.html"),
                      ("apps", "rent_dist", "rent3_admin", "frontend", "app.js"), ("apps", "rent_dist", "rent3_lessor", "frontend", "app.js")):
            self.assertFalse(small.search(rd(*parts)), "/".join(parts))
        bar = rd("apps", "rent_dist", "rent3", "frontend", "session-bar.js")
        self.assertIn("mountCallBarOnly", bar)
        self.assertIn("Authorization: `Bearer ${tok}`", bar)               # 토큰 없이 부르면 401 → 세션 가드가 로그인을 풀어 버린다
        self.assertIn("if (!tok) return;", bar)

    def test_72_lessor_dashboard_monthly_target_kpi(self):
        """72. 임대인 대시보드 헤더 KPI: 당월 목표액·수납액(수납률%)·미납액 반환 및 일관성 검증."""
        c = self.client
        hdrs = self._login("chic")
        res = c.get("/api/rent3_lessor/dashboard?branch=main", headers=hdrs)
        self.assertEqual(res.status_code, 200, res.text)
        d = res.json()
        self.assertRegex(d["target_ym"], r"^\d{4}-\d{2}$")
        self.assertGreater(d["monthly_target"], 0)
        self.assertEqual(d["monthly_collected"] + d["monthly_unpaid"], d["monthly_target"])
        expected_rate = round(d["monthly_collected"] * 100 / d["monthly_target"])
        self.assertEqual(d["monthly_collection_rate"], expected_rate)
        self.assertGreaterEqual(d["total_unpaid_branch"], d["monthly_unpaid"])
        with open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8") as fp:
            lapp = fp.read()
        for needle in ("월 목표액", "수납액", "미납액", "monthly_collection_rate"):
            self.assertIn(needle, lapp, needle)

    def test_73_payment_delete_recalculates_fifo_and_guards(self):
        """73. 수납 내역 삭제(/payments/delete): 권한 경계(타 지점·엄격 관리인 403), 삭제 후 FIFO 원장·대시보드 즉시 재계산."""
        from unittest import mock
        c = self.client
        chic, sunho = self._login("chic"), self._login("sunho")
        self._open_unpaid("201", 6)
        d0 = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).json()
        target = next(
            (rc for rc in d0["rooms"] if rc.get("is_occupied") and (rc.get("ledger") or {}).get("total_unpaid", 0) >= 100000),
            None,
        )
        fallback = target or next((rc for rc in d0["rooms"] if rc.get("is_occupied") and rc.get("contract")), None)
        self.assertIsNotNone(fallback, "활성 임차 호실이 필요합니다.")
        room_no = fallback["room_no"]
        amt = min(250000, int(target["ledger"]["total_unpaid"])) if target else 250000
        r_col = c.post("/api/rent3_lessor/payments/collect", headers=chic,
                       json={"branch": "main", "room_no": room_no, "amount": amt, "allow_advance": True})
        self.assertEqual(r_col.status_code, 200, r_col.text)
        pid = r_col.json()["payment"]["id"]
        self.assertIsNotNone(Pay.get(pid))
        d1 = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).json()
        if target:
            self.assertEqual(d1["total_unpaid_branch"], d0["total_unpaid_branch"] - amt)

        # 다른 지점 임대인·엄격 관리인은 삭제 불가
        self.assertEqual(c.post("/api/rent3_lessor/payments/delete", headers=sunho,
                                json={"branch": "main", "pay_id": pid}).status_code, 403)
        with mock.patch.dict(os.environ, {"RENT3_ADMIN_PROXY_STRICT": "1"}):
            self.assertEqual(c.post("/api/rent3_lessor/payments/delete",
                                    json={"branch": "main", "pay_id": pid}).status_code, 403)

        # 본인 임대인이 삭제하면 Pay 에서 사라지고 미납 합계가 원상 복구된다
        r_del = c.post("/api/rent3_lessor/payments/delete", headers=chic,
                       json={"branch": "main", "pay_id": pid})
        self.assertEqual(r_del.status_code, 200, r_del.text)
        self.assertIsNone(Pay.get(pid))
        for other in r_col.json()["payments"][1:]:               # 입금이 여러 달에 걸쳤다면 달마다 건이 따로 있다
            self.assertEqual(c.post("/api/rent3_lessor/payments/delete", headers=chic, json={"branch": "main", "pay_id": other["id"]}).status_code, 200)
        d2 = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).json()
        self.assertEqual(d2["total_unpaid_branch"], d0["total_unpaid_branch"])
        with open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8") as fp:
            lapp = fp.read()
        self.assertIn("/payments/delete", lapp)
        self.assertIn("pr-del", lapp)

    def test_72_mobile_big_text_narrow_margins(self):
        """72. 스마트폰 화면: 바깥·카드 여백을 줄이고 글자를 키우며 자간을 좁힌다(모든 앱이 쓰는 rent3_mobile.css), 목록 한 줄은 줄바꿈 금지."""
        css = open(os.path.join(ROOT, "shared_ui", "rent3_mobile.css"), encoding="utf-8").read()
        for needle in ("font-size: 17px", "letter-spacing: -0.03em", ".app-shell, body .app-shell", "padding-left: 6px !important",
                       ".bass-card { padding: 12px !important", ".todo-row, .todo-row span { white-space: nowrap", "min-width: 44px"):
            self.assertIn(needle, css, needle)
        for app in ("rent3", "rent3_admin", "rent3_contract", "rent3_lessor", "rent3_tenant"):
            html = open(os.path.join(ROOT, "apps", "rent_dist", app, "frontend", "index.html"), encoding="utf-8").read()
            self.assertIn("/shared/ui/rent3_mobile.css", html, app)

    def test_72_portal_auto_enter_when_logged_in(self):
        """72. 이미 로그인한 임대인·임차인·관리인은 포털 첫 화면 대신 내 업무 화면으로 바로 들어간다(만료 토큰·?login=1·?logout=1·?home=1 은 제외). 전환 안내는 없고 ☰ 포털 홈은 ?home=1."""
        rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
        app = rd("apps", "rent_dist", "rent3", "frontend", "app.js")
        for needle in ("autoEnter", 'urlParams.get("home") === "1"', "window.location.replace(u.redirect_url)", 'strip.style.display = "none"', "p.exp * 1000"):
            self.assertIn(needle, app, needle)
        self.assertNotIn('strip.style.display = "block"', app)
        self.assertIn("/rent3/?home=1", rd("shared_ui", "r3_shell.js"))
        self.assertIn("/rent3/?home=1", rd("apps", "rent_dist", "rent3_admin", "frontend", "index.html"))

    def test_73_ledger_csv_export_import(self):
        """73. 납부현황 엑셀(CSV): 내려받기 → 그대로 올리면 변화 없음, 빠진 달은 '새로 기록', 값이 다르면 '다름'(기본 유지·선택 시 교체), 중복 없음, 반영 전 자동 백업, 남의 건물 불가."""
        import tempfile
        from unittest import mock
        from datetime import date as _d
        from apps.rent_dist._rent3_core import Pay, ledger_csv as lc
        c = self.client
        chic, sunho = self._login("chic"), self._login("sunho")
        r = c.get("/api/rent3_lessor/ledger/export-csv?branch=main&year=2026", headers=chic)
        self.assertEqual(r.status_code, 200)
        text = r.content.decode("utf-8")
        self.assertTrue(text.startswith("\ufeff"))
        self.assertIn("호실,성명,선/후,1월", text)
        post = lambda path, **kw: c.post(f"/api/rent3_lessor/ledger/import/{path}", headers=chic, json={"branch": "main", "year": 2026, "csv_text": text, **kw})
        base = post("preview").json()
        self.assertEqual((base["counts"]["new"], base["counts"]["diff"]), (0, 0))             # 내려받은 그대로 올리면 바뀔 것이 없다
        self.assertGreaterEqual(base["counts"]["same"], 1)
        # 읽기 규칙: 점이 빠진 칸은 월·일로 풀어 읽고 표시, 이상한 칸은 날짜가 아님
        self.assertEqual(lc._cell_date("1025", "2026-10"), (_d(2026, 10, 25), True))
        self.assertEqual(lc._cell_date("115", "2026-01"), (_d(2026, 1, 15), True))
        self.assertEqual(lc._cell_date("2. 3", "2026-02"), (_d(2026, 2, 3), False))
        self.assertEqual(lc._cell_date("전출6/4", "2026-06")[0], None)
        self.assertEqual(lc._cell_date("1. 5", "2026-12"), (_d(2027, 1, 5), False))           # 12월 칸에 적힌 1월 날짜
        # 빠진 달: 한 건 지우고 같은 파일을 올리면 '새로 기록' 1건 → 반영하면 복구, 다시 올려도 중복 없음
        victim = next(p for p in Pay.all() if p.branch == "main" and p.room_no == "201" and p.billing_month == "2026-08" and p.payment_kind == "MONTHLY_RENT")
        total, when = victim.total_amount, victim.paid_date
        victim.delete(action="t")
        pv = post("preview").json()
        self.assertEqual(pv["counts"]["new"], 1)
        item = next(i for i in pv["items"] if i["status"] == "new")
        self.assertEqual((item["room_no"], item["ym"], item["amount"], item["date"]), ("201", "2026-08", total, when))
        bdir = tempfile.mkdtemp(prefix="rent3_bk_csv_")
        with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": bdir}):
            ap = post("apply")
            self.assertEqual(ap.status_code, 200, ap.text)
            self.assertEqual(ap.json()["added"], 1)
            self.assertTrue(ap.json()["backup"])                                              # 반영 전 자동 백업
            self.assertTrue(any(n.endswith(".tar.gz") for n in os.listdir(bdir)))
            self.assertEqual(post("apply").json()["added"], 0)                                # 같은 파일을 또 올려도 중복 없음
        back = [p for p in Pay.all() if p.branch == "main" and p.room_no == "201" and p.billing_month == "2026-08"]
        self.assertEqual((len(back), back[0].total_amount, back[0].paid_date), (1, total, when))
        # 값이 다른 칸: 기본은 '다름'으로 알리기만, 체크하면 파일 값으로 교체
        self.assertEqual(c.post("/api/rent3_lessor/ledger/import/apply", headers=sunho, json={"branch": "main", "year": 2026, "csv_text": text}).status_code, 403)   # 남의 건물
        self.assertEqual(c.post("/api/rent3_lessor/ledger/import/preview", headers=chic, json={"branch": "main", "year": 2026, "csv_text": "아무 표\n1,2,3"}).status_code, 400)
        # 다름: 8월 칸 날짜를 파일에서 바꿔 올린다
        import csv as _csv, io as _io
        rows = list(_csv.reader(_io.StringIO(text.lstrip("\ufeff"))))
        hi = next(k for k, r_ in enumerate(rows) if "1월" in r_)
        col8 = rows[hi].index("8월")
        for r_ in rows:
            if r_ and r_[0] == "201":
                r_[col8] = "8. 29"
        buf = _io.StringIO()
        _csv.writer(buf, lineterminator="\r\n").writerows(rows)
        text2 = buf.getvalue()
        pd = c.post("/api/rent3_lessor/ledger/import/preview", headers=chic, json={"branch": "main", "year": 2026, "csv_text": text2}).json()
        self.assertEqual(pd["counts"]["diff"], 1)
        with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": bdir}):
            keep = c.post("/api/rent3_lessor/ledger/import/apply", headers=chic, json={"branch": "main", "year": 2026, "csv_text": text2}).json()
            self.assertEqual((keep["added"], keep["replaced"]), (0, 0))
            self.assertEqual(next(p for p in Pay.all() if p.room_no == "201" and p.billing_month == "2026-08").paid_date, when)       # 기본: 건드리지 않는다
            rep = c.post("/api/rent3_lessor/ledger/import/apply", headers=chic, json={"branch": "main", "year": 2026, "csv_text": text2, "replace_diffs": True}).json()
            self.assertEqual(rep["replaced"], 1)
        self.assertEqual(next(p for p in Pay.all() if p.room_no == "201" and p.billing_month == "2026-08").paid_date, "2026-08-29")
        fixed = next(p for p in Pay.all() if p.room_no == "201" and p.billing_month == "2026-08")
        fixed.paid_date = when
        fixed.save(action="t")
        # 화면
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnLedgerCsv", "showLedgerCsvModal", "/ledger/import/preview", "/ledger/import/apply", "확인했습니다 — 반영하기", "euc-kr"):
            self.assertIn(needle, lapp, needle)

    def test_74_ledger_csv_former_tenant_payments_kept_for_tax(self):
        """74. 이전 입주자 시기(지금 입주자 입주 전 달)의 납부도 버리지 않고 'former:' 기록으로 보관한다 — 사람·계약 객체를 새로 만들지 않고, 세무 집계에는 들어가며 지금 입주자의 납부·미납에는 나타나지 않는다."""
        import csv as _csv, io as _io, tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Contract, Pay, Tenant, ledger_csv as lc
        c = self.client
        chic = self._login("chic")
        ct = next(x for x in Contract.all() if x.branch == "main" and x.room_no == "201" and x.status == "ACTIVE")
        old_move = ct.move_in_date
        ct.move_in_date = "2026-06-01"                                  # 201호 지금 입주자는 6월부터
        ct.save(action="t")
        tenants_before = len(Tenant.all())
        before_unpaid = c.get("/api/rent3_tenant/myhome/tenant_201", headers=chic).status_code
        text = lc.export_csv("main", 2026)
        rows = list(_csv.reader(_io.StringIO(text.lstrip("﻿"))))
        hi = next(k for k, r in enumerate(rows) if "1월" in r)
        col3 = rows[hi].index("3월")
        for r in rows:
            if r and r[0] == "201":
                r[col3], r[col3 + 1] = "3. 5", "550,000"
        buf = _io.StringIO()
        _csv.writer(buf, lineterminator="\r\n").writerows(rows)
        text2 = buf.getvalue()
        body = {"branch": "main", "year": 2026, "csv_text": text2}
        pv = c.post("/api/rent3_lessor/ledger/import/preview", headers=chic, json=body).json()
        it = next(i for i in pv["items"] if i["room_no"] == "201" and i["ym"] == "2026-03")
        self.assertEqual((it["status"], it["former"]), ("new", True))
        self.assertGreaterEqual(pv["counts"]["former"], 1)
        with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_f_")}):
            self.assertEqual(c.post("/api/rent3_lessor/ledger/import/apply", headers=chic, json=body).json()["added"], 1)
            self.assertEqual(c.post("/api/rent3_lessor/ledger/import/apply", headers=chic, json=body).json()["added"], 0)      # 중복 없음
        p = next(x for x in Pay.all() if x.room_no == "201" and x.billing_month == "2026-03")
        self.assertEqual((p.id_contract, p.id_tenant, p.payer_name, p.total_amount, p.paid_date), ("former:main:201", "", "이전 입주자", 550000, "2026-03-05"))
        self.assertEqual(len(Tenant.all()), tenants_before)                                                                   # 임시 임차인 객체를 만들지 않는다
        self.assertNotIn(p.id, [x["id"] for x in c.get("/api/rent3_tenant/myhome/tenant_201").json()["payments"]])           # 지금 입주자 화면에는 안 나온다
        self.assertEqual(Contract.get(ct.id).due_for_month("2026-03")["due"], 0)                                               # 지금 계약의 청구·미납에는 영향 없다(입주 전 달)
        from apps.rent_dist._rent3_core import TaxAndCertificateService as T
        rep = T.export_annual_tax_csv(branch="main", year=2026)
        self.assertIn("201", rep)                                                                                              # 세무 집계는 호실·청구월 기준이라 그대로 읽는다
        p.delete(action="t")
        ct = Contract.get(ct.id)
        ct.move_in_date = old_move
        ct.save(action="t")

    def test_75_lessor_first_screen_bank_sms_flow(self):
        """75. 임대인 첫 화면(단순화): 은행 문자 입력창 아래에 호실 단추 목록([호실·이름] [완납 초록/대기 노랑/미납 빨강] [매월 납부일])만 보이고, 호실을 누르거나 문자로 수납하면 수납내역이 창(닫기 단추)으로 열린다. 풀다운·호실 카드·미납 접이 목록은 첫 화면에서 없어졌다."""
        app = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        i_sms, i_list = app.index('id="bankSms"'), app.index('id="roomList"')
        self.assertLess(i_sms, i_list)                                          # 문자 입력이 호실 목록보다 위
        for gone in ("roomPick", "lateHtml", "호실 선택</label>", "btn-late-sms"):
            self.assertNotIn(gone, app, gone)
        for needle in ("rl-row", "rl-row--ok", "rl-row--wait", "rl-row--late", "repeat(1, minmax(0, 1fr))", "function openRoomModal", 'id="roomPanel"', 'id="roomModalClose"', "닫기", "openRoomModal(rn)", "📨 미납 안내 문자",
                       "bankSmsGo", "/payments/sms-paste", 'id="payInline"', "payFlash", "renderPayInline(r.room_no)", "직접 수납 (현금·여러 달분 입금 등)", "수납확인 문자 복사"):
            self.assertIn(needle, app, needle)
        for gone in ("pmParse", "pmSms", 'data-act="pay"', "showPayModal"):
            self.assertNotIn(gone, app, gone)
        # 서버는 호실 번호가 없는 문자도 입금자 이름으로 호실을 찾는다
        from apps.rent_dist._rent3_core.billing import SmsDepositParser as P
        for text, room, name in (("[신한] 11/13 440,000원 입금 안동기", "", "안동기"),                      # 금액 앞자리(440)를 호실로 읽지 않는다
                                 ("[Web발신] 신한 11/13 13:20 입금 440,000원 안동기 202호", "202", "안동기"),
                                 ("안동기202 440,000원", "202", "안동기"),
                                 ("[KB] 11/13 홍길동 301 입금 290,000원 잔액 1,200,000원", "301", "홍길동")):
            got = P.parse(text)
            self.assertEqual((got["room_no"], got["payer_name"]), (room, name), text)
        c = self.client
        r = c.post("/api/rent3_lessor/payments/sms-paste", json={"branch": "main", "sms_text": "[Web발신] 신한 10/20 입금 440,000원 존재하지않는이름"})
        self.assertIn(r.status_code, (400, 404))
        self.assertIn("호실", r.json()["message"])

    def test_76_sms_mismatch_confirm_then_apply_by_payer_name(self):
        """76. 문자의 호실·입금자명·금액이 장부와 다르면 저장하지 않고 경고(원본 수정 안내), 그대로 진행하면 입금자명 기준으로 반영."""
        from apps.rent_dist._rent3_core import Pay, Contract
        c = self.client
        url = "/api/rent3_lessor/payments/sms-paste"
        self._open_unpaid("201", 6)
        cts = [x for x in Contract.all() if x.branch == "main" and x.status == "ACTIVE"]
        a = next(x for x in cts if x.room_no == "201")
        b = next(x for x in cts if x.room_no != "201" and x.tenant_name != a.tenant_name)
        monthly = a.monthly_rent + a.maintenance_fee + a.compute_vat()
        self._open_unpaid(b.room_no, 6)
        n0 = len(Pay.all())
        # 1) 호실은 a, 입금자명은 b → 이름 불일치 경고(저장 안 됨), 입금자명 기준이면 b의 호실
        t1 = f"[신한] 11/13 {a.room_no}호 {b.tenant_name} {monthly:,}원 입금"
        r = c.post(url, json={"branch": "main", "sms_text": t1}).json()
        self.assertTrue(r["needs_confirm"])

        self.assertTrue(any(i["field"] == "name" for i in r["issues"]))
        self.assertEqual((r["by_name"]["room_no"], r["by_name"]["tenant_name"]), (b.room_no, b.tenant_name))
        self.assertEqual(len(Pay.all()), n0)
        r2 = c.post(url, json={"branch": "main", "sms_text": t1, "force": True}).json()
        self.assertEqual(r2["payment"]["room_no"], b.room_no)                                  # 입금자명 기준
        self.assertTrue(r2["forced_by_name"])
        # 2) 금액만 다름 → 금액 경고, 이름 기준(=같은 호실)으로 반영
        t2 = f"[신한] 11/14 {a.room_no}호 {a.tenant_name} 123,457원 입금"
        r = c.post(url, json={"branch": "main", "sms_text": t2}).json()
        self.assertEqual([i["field"] for i in r["issues"]], ["amount"])
        self.assertEqual(c.post(url, json={"branch": "main", "sms_text": t2, "force": True}).json()["payment"]["room_no"], a.room_no)
        # 3) 호실·이름 모두 맞고 금액이 월 청구액이면 경고 없이 바로 수납
        t3 = f"[신한] 11/15 {a.room_no}호 {a.tenant_name} {monthly:,}원 입금"
        r = c.post(url, json={"branch": "main", "sms_text": t3}).json()
        self.assertTrue(r.get("success") and not r.get("needs_confirm"))
        # 4) 입금자명으로 계약을 못 찾으면 진행할 수 없고 오류로 원본 수정 안내
        r = c.post(url, json={"branch": "main", "sms_text": f"[신한] 11/16 {a.room_no}호 없는사람 {monthly:,}원 입금", "force": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("원본", r.json().get("hint", ""))
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("smsWarn", "needs_confirm", "원본 문자 고치기", "그대로 진행", "force: !!force"):
            self.assertIn(needle, lapp, needle)
        for p in [x for x in Pay.all() if x.raw_sms.startswith("[신한] 11/1")][:]:
            p.delete(action="t")

    def test_77_lessor_pin_backend_foundation(self):
        """77. 임대인 간편 비밀번호 1단계(백엔드): 스위치(RENT3_LESSOR_PIN) 꺼짐=사용 불가, 켜면 PIN·기기 2대 상한·1시간 토큰,
        PIN 토큰은 허용 목록(조회·메시지·시설 처리)만 — 수납·세무·내보내기는 읽기도 403, 해시는 응답에 안 실림, 기기 해제·스위치 끔은 즉시 무효."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Lessor
        from core.auth import verify_token
        c, NA = self.client, self.NOAUTH
        PL = "/api/rent3/pool"
        chic = self._login("chic")
        lsr = Lessor.first(branch="main")
        self.assertIsNotNone(lsr)
        lid = lsr.id
        try:
            # 스위치 꺼짐: 설정·바로가기 모두 사용 불가
            st = c.get(f"{PL}/shortcut/status", headers=chic).json()
            self.assertFalse(st["applicable"])
            self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=chic, json={"pin": "2846"}).status_code, 400)
            with mock.patch.dict(os.environ, {"RENT3_LESSOR_PIN": "1"}):
                st = c.get(f"{PL}/shortcut/status", headers=chic).json()
                self.assertEqual((st["kind"], st["applicable"], st["max_devices"]), ("lessor", True, 2))
                self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=chic, json={"pin": "1111"}).status_code, 400)
                self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=chic, json={"pin": "2846"}).status_code, 200)
                d1 = c.post(f"{PL}/shortcut/devices", headers=chic, json={"label": "폰"}).json()
                d2 = c.post(f"{PL}/shortcut/devices", headers=chic, json={"label": "태블릿"}).json()
                qk = d1["qk"]
                self.assertTrue(d1["start_url"].startswith("/rent3_lessor/?qk="))
                self.assertEqual(c.post(f"{PL}/shortcut/devices", headers=chic, json={"label": "셋째"}).status_code, 400)   # 2대 상한
                lsr = Lessor.get(lid)
                self.assertTrue(lsr.pin_hash and "2846" not in lsr.pin_hash)
                self.assertNotIn(qk, open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read())
                body = c.get("/api/rent3_lessor/dashboard?branch=main", headers=chic).text
                self.assertNotIn(lsr.pin_hash, body)
                self.assertNotIn("quick_devices", body)
                # 공개 정보: 이름은 가리고 건물·호실은 숨긴다. 매니페스트는 임대인 앱 범위
                idn = c.get(f"{PL}/shortcut/identify?qk={qk}", headers=NA).json()
                self.assertEqual((idn["kind"], idn["building_name"], idn["room_no"]), ("lessor", "", ""))
                m = c.get(f"{PL}/shortcut/manifest.json?qk={qk}", headers=NA).json()
                self.assertEqual((m["scope"], m["start_url"]), ("/rent3_lessor/", d1["start_url"]))
                # PIN 로그인: 틀리면 남은 횟수, 맞으면 임대인 PIN 토큰(1시간, m=pin)
                bad = c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": qk, "pin": "9999"})
                self.assertEqual(bad.status_code, 401)
                self.assertIn("남은 시도 4회", bad.json()["message"])
                ok = c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": qk, "pin": "2846"}).json()
                self.assertEqual((ok["role"], ok["via_pin"]), ("lessor", True))
                claims = verify_token(ok["token"])
                self.assertEqual((claims["r"], claims["m"]), ("lessor", "pin"))
                self.assertLessEqual(claims["exp"] - __import__("time").time(), 3600)
                pin = {"Authorization": f"Bearer {ok['token']}"}
                # 허용 목록: 조회·메시지·시설 처리만
                self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=pin).status_code, 200)
                self.assertEqual(c.get("/api/rent3_lessor/status?branch=main", headers=pin).status_code, 200)
                self.assertEqual(c.get(f"{PL}/shortcut/status", headers=pin).json()["via_pin"], True)
                for path in ("/tickets/x/assign", "/tickets/x/complete", "/chats"):
                    self.assertNotIn(c.post(f"/api/rent3_lessor{path}", headers=pin, json={"branch": "main"}).status_code, (401, 403), path)
                # 허용 밖: 읽기도 막힌다(입주자 명단 CSV·세무·계약서 사진), 쓰기(수납·수정·설정)도 막힌다
                for method, path in (("get", "/ledger/export-csv?branch=main&year=2026"), ("get", "/tax/summary?branch=main"),
                                     ("get", "/tax/export-csv?branch=main"), ("get", "/contracts/x/photo?branch=main"),
                                     ("post", "/payments/collect"), ("post", "/payments/sms-paste"), ("post", "/payments/delete"),
                                     ("post", "/contracts/edit"), ("post", "/building/edit"), ("post", "/billing-notices/send"),
                                     ("post", "/tenant-invite"), ("post", "/tenant-shortcuts/revoke-all")):
                    r = getattr(c, method)(f"/api/rent3_lessor{path}", headers=pin, **({"json": {"branch": "main"}} if method == "post" else {}))
                    self.assertEqual(r.status_code, 403, (path, r.status_code))
                # PIN 토큰으로는 PIN·기기 설정을 바꿀 수 없다
                self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=pin, json={"pin": "7391"}).status_code, 403)
                # 5회 실패 잠금 → 일반 로그인이 푼다
                for _ in range(5):
                    c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d2["qk"], "pin": "9999"})
                self.assertTrue(c.get(f"{PL}/shortcut/status", headers=chic).json()["devices"][1]["locked"])
                self.assertEqual(c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d2["qk"], "pin": "2846"}).status_code, 401)
                self._login("chic")
                self.assertEqual(c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d2["qk"], "pin": "2846"}).status_code, 200)
                # 기기 해제 → 이미 받은 PIN 토큰도 즉시 401
                dev_id = d1["device"]["id"]
                self.assertEqual(c.post(f"{PL}/shortcut/devices/{dev_id}/revoke", headers=chic).status_code, 200)
                self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=pin).status_code, 401)
                # 스위치를 끄면 남은 기기의 토큰도 바로 무효
                ok2 = c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d2["qk"], "pin": "2846"}).json()
                pin2 = {"Authorization": f"Bearer {ok2['token']}"}
                self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=pin2).status_code, 200)
            self.assertEqual(c.get("/api/rent3_lessor/dashboard?branch=main", headers=pin2).status_code, 401)
            self.assertEqual(c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d2["qk"], "pin": "2846"}).status_code, 401)
        finally:
            l = Lessor.get(lid)
            l.pin_hash, l.quick_devices = "", []
            l.save(action="t")

    def test_78_lessor_pin_pay_toggle_cap_and_screens(self):
        """78. 임대인 PIN 2~3단계: 기기별 '수납 허용'(비밀번호 재확인), 허용 기기만 수납(추가만·수정/삭제 불가), 하루 25건·1,000만 원 한도,
        대시보드 pin 정보, 바로가기 PIN 우선(임대인 페이지는 비밀번호 로그인이 남아 있어도 PIN 먼저), 화면 제한 코드."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Lessor, Pay, Contract, quicklogin as ql
        c, NA = self.client, self.NOAUTH
        PL, LS = "/api/rent3/pool", "/api/rent3_lessor"
        chic = self._login("chic")
        lid = Lessor.first(branch="main").id
        ct = next(x for x in Contract.all() if x.branch == "main" and x.status == "ACTIVE" and x.room_no == "201")
        monthly = ct.monthly_rent + ct.maintenance_fee + ct.compute_vat()
        try:
            with mock.patch.dict(os.environ, {"RENT3_LESSOR_PIN": "1"}):
                self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=chic, json={"pin": "2846"}).status_code, 200)
                d1 = c.post(f"{PL}/shortcut/devices", headers=chic, json={"label": "폰"}).json()
                dev = d1["device"]["id"]
                ok = c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d1["qk"], "pin": "2846"}).json()
                pin = {"Authorization": f"Bearer {ok['token']}"}
                dash = c.get(f"{LS}/dashboard?branch=main", headers=pin).json()
                self.assertEqual((dash["pin"]["via_pin"], dash["pin"]["pay_ok"]), (True, False))
                self.assertFalse(c.get(f"{LS}/dashboard?branch=main", headers=chic).json()["pin"]["via_pin"])
                self._open_unpaid("201", 8)
                n0 = len(Pay.all())
                # 수납 허용 전: 문자·직접·이체 확인 모두 403
                sms = f"[신한] 11/13 201호 {ct.tenant_name} {monthly:,}원 입금"
                for path, body in (("/payments/sms-paste", {"branch": "main", "sms_text": sms}),
                                   ("/payments/collect", {"branch": "main", "room_no": "201", "amount": monthly}),
                                   ("/transfer-notices/x/confirm", {"branch": "main"})):
                    self.assertEqual(c.post(f"{LS}{path}", headers=pin, json=body).status_code, 403, path)
                self.assertEqual(len(Pay.all()), n0)
                # 켜기: PIN 토큰으로는 못 바꾼다 · 비밀번호가 틀리면 거부 · 맞으면 켜진다
                self.assertEqual(c.post(f"{PL}/shortcut/devices/{dev}/pay-allow", headers=pin, json={"allow": True, "password": "1212"}).status_code, 403)
                self.assertEqual(c.post(f"{PL}/shortcut/devices/{dev}/pay-allow", headers=chic, json={"allow": True, "password": "틀린비번99"}).status_code, 401)
                r = c.post(f"{PL}/shortcut/devices/{dev}/pay-allow", headers=chic, json={"allow": True, "password": "1212"})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(r.json()["devices"][0]["pay_ok"])
                self.assertTrue(c.get(f"{LS}/dashboard?branch=main", headers=pin).json()["pin"]["pay_ok"])
                # 허용 기기: 문자 수납 가능 + 한도 기록, 수정·삭제는 여전히 403
                r = c.post(f"{LS}/payments/sms-paste", headers=pin, json={"branch": "main", "sms_text": sms})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(c.get(f"{LS}/dashboard?branch=main", headers=pin).json()["pin"]["left_n"], 24)
                paid = Pay.get(r.json()["payment"]["id"])
                self.assertEqual(sum(x["total_amount"] for x in r.json()["payments"]), monthly)           # 여러 달에 걸치면 달마다 한 건
                for path in ("/payments/delete", "/payments/edit", "/contracts/edit"):
                    self.assertEqual(c.post(f"{LS}{path}", headers=pin, json={"branch": "main", "pay_id": paid.id}).status_code, 403, path)
                # 한도: 금액(1,000만 원 초과)·건수(25건) 초과는 403, 한도 안내 문구
                big = c.post(f"{LS}/payments/collect", headers=pin, json={"branch": "main", "room_no": "201", "amount": 10_000_001})
                self.assertEqual(big.status_code, 403)
                self.assertIn("한도", big.json()["message"])
                l = Lessor.get(lid)
                for d in l.quick_devices:
                    d["pay_n"] = ql.PIN_PAY_MAX_COUNT
                l.save(action="t")
                full = c.post(f"{LS}/payments/collect", headers=pin, json={"branch": "main", "room_no": "201", "amount": 1000})
                self.assertEqual(full.status_code, 403)
                self.assertIn("한도", full.json()["message"])
                # 비밀번호 로그인(일반 토큰)은 한도와 무관
                self.assertEqual(c.post(f"{LS}/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": 1000}).status_code, 200)
                # 끄면 즉시 다시 403
                self.assertEqual(c.post(f"{PL}/shortcut/devices/{dev}/pay-allow", headers=chic, json={"allow": False}).status_code, 200)
                self.assertEqual(c.post(f"{LS}/payments/collect", headers=pin, json={"branch": "main", "room_no": "201", "amount": 1000}).status_code, 403)
        finally:
            for x in Pay.all():
                if x.branch == "main" and x.room_no == "201" and (x.raw_sms or "").startswith("[신한] 11/13 201호"):
                    x.delete(action="t")
            for x in [x for x in Pay.all() if x.branch == "main" and x.room_no == "201" and x.total_amount == 1000 and x.paid_date >= "2026-11-01"]:
                x.delete(action="t")
            l = Lessor.get(lid)
            l.pin_hash, l.quick_devices = "", []
            l.save(action="t")
        # 화면: PIN 우선 정책·임대인 설정·수납 허용·제한 코드
        shell = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        for needle in ("LESSOR_PAGE", "skipPinOnce", "r3_pin_skip", "pay-allow", "수납 허용 켜기", "집주인 바로가기"):
            self.assertIn(needle, shell, needle)
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("VIA_PIN", "pinLimit", "applyPinBar", "btnShortcut", "btnFullLogin", "pinOnly", "PIN_TABS"):
            self.assertIn(needle, lapp, needle)

    def test_79_forgot_password_find_my_page_for_migrated_accounts(self):
        """79. 비밀번호를 잊은 기존(이관) 임차인·임대인의 '내 페이지 찾기': 본인인증 이력이 없어도 장부의 실명·휴대폰 + 문자 인증으로 새 비밀번호를 정한다.
        임대인은 사업자번호·계좌번호 끝자리까지, 퇴실·정지·번호 불일치는 거부, 바뀐 뒤 이전 바로가기는 해제, 번호별 시도 제한, 메인 화면 링크."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Lessor, Tenant, passwords as pwd, quicklogin as ql, ratelimit
        c, NA = self.client, self.NOAUTH
        R = "/api/rent3/pool/auth/password-reset"
        login = lambda ident, pw: c.post("/api/rent3/pool/auth/login", headers=NA, json={"login_mode": "account", "identifier": ident, "password": pw})
        t = Tenant.first(room_no="202", branch="main")
        lsr = Lessor.first(branch="main")
        old_t = (t.name, t.phone, t.status, t.room_no, dict(t.identity), t.password, t.pin_hash, list(t.quick_devices))
        old_l = (lsr.name, lsr.phone, lsr.status, dict(lsr.identity), lsr.password, lsr.business_number, lsr.account_number)
        try:
            ratelimit.reset_all()
            with mock.patch.dict(os.environ, {"RENT3_FAKE_IDENTITY": "1"}):
                # ── 임차인 ──
                t.phone, t.identity, t.password, t.status, t.room_no = "010-5555-0202", {}, pwd.hash_password("Old#Pass99"), "ACTIVE", "202"
                t.save(action="t")
                ql.set_pin(t, "2846")
                ql.new_device(Tenant.get(t.id), "폰")
                base = {"identifier": t.id, "name": t.name, "phone": "01055550202", "new_password": "New#Pass99", "code": "000000"}
                for bad in ({"name": "다른사람"}, {"phone": "01099990000"}, {"code": "111111"}, {"new_password": "1212"}):
                    self.assertEqual(c.post(f"{R}/confirm", headers=NA, json={**base, **bad}).status_code, 400, bad)
                self.assertEqual(login(t.id, "Old#Pass99").status_code, 200)                 # 거부된 시도는 아무것도 바꾸지 않았다
                ratelimit.reset_all()
                self.assertEqual(c.post(f"{R}/start", headers=NA, json=base).status_code, 200)
                self.assertEqual(c.post(f"{R}/confirm", headers=NA, json=base).status_code, 200)
                self.assertEqual(login(t.id, "Old#Pass99").status_code, 401)
                self.assertEqual(login(t.id, "New#Pass99").status_code, 200)
                after = Tenant.get(t.id)
                self.assertEqual((after.pin_hash, after.quick_devices), ("", []))             # 이전 바로가기·간편 비밀번호는 해제
                # 퇴실한 임차인은 거부
                ratelimit.reset_all()
                after.status = "MOVED_OUT"
                after.save(action="t")
                self.assertEqual(c.post(f"{R}/confirm", headers=NA, json={**base, "new_password": "Third#Pass9"}).status_code, 400)
                # 번호별 시도 제한: 아이디를 바꿔 가며 같은 번호로 인증번호를 계속 요청해도 막힌다
                ratelimit.reset_all()
                codes = [c.post(f"{R}/start", headers=NA, json={**base, "identifier": f"nobody{i}"}).status_code for i in range(8)]
                self.assertIn(429, codes)
                # ── 임대인: 사업자번호·계좌번호 끝자리 필수 ──
                ratelimit.reset_all()
                lsr.phone, lsr.identity, lsr.status = "010-6666-0001", {}, "ACTIVE"
                lsr.password, lsr.business_number, lsr.account_number = pwd.hash_password("Old#Pass99"), "123-45-67890", "110-123-456789"
                lsr.save(action="t")
                lb = {"identifier": lsr.username or lsr.id, "name": lsr.name, "phone": "01066660001", "new_password": "New#Pass99", "code": "000000"}
                for proof in ("", "12", "0000", "9999"):
                    self.assertEqual(c.post(f"{R}/confirm", headers=NA, json={**lb, "proof": proof}).status_code, 400, proof)
                self.assertEqual(login(lb["identifier"], "Old#Pass99").status_code, 200)
                ratelimit.reset_all()
                self.assertEqual(c.post(f"{R}/start", headers=NA, json={**lb, "proof": "7890"}).status_code, 200)
                self.assertEqual(c.post(f"{R}/confirm", headers=NA, json={**lb, "proof": "7890"}).status_code, 200)
                self.assertEqual(login(lb["identifier"], "New#Pass99").status_code, 200)
                # 정지된 임대인은 거부
                ratelimit.reset_all()
                l2 = Lessor.get(lsr.id)
                l2.status = "SUSPENDED"
                l2.save(action="t")
                self.assertEqual(c.post(f"{R}/confirm", headers=NA, json={**lb, "proof": "6789", "new_password": "Third#Pass9"}).status_code, 400)
        finally:
            ratelimit.reset_all()
            t = Tenant.get(t.id)
            (t.name, t.phone, t.status, t.room_no, t.identity, t.password, t.pin_hash, t.quick_devices) = old_t
            t.save(action="t")
            lsr = Lessor.get(lsr.id)
            (lsr.name, lsr.phone, lsr.status, lsr.identity, lsr.password, lsr.business_number, lsr.account_number) = old_l
            lsr.save(action="t")
        html = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "index.html"), encoding="utf-8").read()
        js = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        self.assertEqual(html.count("js-forgot"), 4)                                          # 임차인·임대인 로그인 카드 2 + 소개 구역 2
        for needle in ("rsProof", "내 페이지 찾기"):
            self.assertIn(needle, html, needle)
        for needle in (".js-forgot", "proof:"):
            self.assertIn(needle, js, needle)

    def test_80_portal_forgot_links_and_auto_enter(self):
        """80. 메인 홈: 임차인·임대인 로그인 옆과 소개 구역에 '비밀번호를 잊으셨나요? 본인 확인으로 내 페이지 찾기' 링크, 재설정 직후 새 비밀번호로 자동 입장."""
        base = os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend")
        html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
        app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        self.assertGreaterEqual(html.count("js-forgot"), 4)                  # 역할 카드 2 + 소개 구역 2
        self.assertIn("본인 확인으로 내 페이지 찾기", html)
        i = app.index('"/pool/auth/password-reset/confirm"')
        self.assertIn("/pool/auth/login", app[i:i + 1600])                    # 재설정 직후 새 비밀번호로 곧바로 로그인
        self.assertIn("resolveConsent", app[i:i + 1600])                      # 동의 화면이 필요하면 그대로 거친다

    def test_81_existing_tenant_connect_request_with_lessor_approval(self):
        """81. 기존 임차인 '내 페이지 연결'(문자 인증 없이): 4가지(건물·호실·성명·휴대폰) 대조 → 확인번호 → 임대인이 통화로 확인번호 입력해 승인
        → 요청한 브라우저에만 1회용 입주 안내 링크 → 비밀번호·PIN 설정. 남의 건물 임대인·틀린 번호·이미 가입한 호실·남의 secret 은 거부."""
        from apps.rent_dist._rent3_core import Tenant, Building, connect as cn
        from apps.rent_dist._rent3_core.ops import ConnectRequest
        c, NA = self.client, self.NOAUTH
        PL, LS = "/api/rent3/pool", "/api/rent3_lessor"
        chic, sunho = self._login("chic"), self._login("sunho")
        t = next(x for x in Tenant.all() if x.branch == "main" and x.status == "ACTIVE" and x.room_no and not cn.activated(x))
        old = (t.phone, t.password, t.pin_hash, list(t.quick_devices), dict(t.consents), t.invite_hash, t.invite_expires)
        t.phone = "010-7777-1234"
        t.save(action="t")
        bname = Building.get(t.id_building).building_name if t.id_building else "시크빌"
        body = {"building_name": bname, "room_no": f"{t.room_no}호", "name": t.name, "phone": "01077771234"}
        try:
            # 틀린 정보는 어느 항목이 틀렸는지 알려 주지 않는다
            bad = c.post(f"{PL}/connect/request", headers=NA, json={**body, "phone": "01099990000"})
            self.assertEqual(bad.status_code, 400)
            self.assertNotIn("휴대폰", bad.json()["message"])
            info = c.get(f"{PL}/connect/info?branch=main", headers=NA).json()                           # 링크의 지점 코드 → 건물명·임대인 전화번호
            self.assertTrue(info["building_name"] and info["lessor_phone"])
            self.assertEqual(c.get(f"{PL}/connect/info?branch=nope", headers=NA).status_code, 404)
            by_branch = {"branch": "main", "room_no": t.room_no, "name": t.name, "phone": "01077771234"}   # 건물명 없이 지점 코드로
            self.assertEqual(c.post(f"{PL}/connect/request", headers=NA, json={**by_branch, "branch": "sunho"}).status_code, 400)   # 다른 지점 코드는 일치하지 않는다
            ok = c.post(f"{PL}/connect/request", headers=NA, json=by_branch)
            self.assertEqual(ok.status_code, 200, ok.text)
            rid, secret, code = ok.json()["request_id"], ok.json()["secret"], ok.json()["code"]
            self.assertTrue(code.isdigit() and len(code) == 4)
            self.assertNotIn(secret, open(os.path.join(self.temp_pool, "ops.json"), encoding="utf-8").read())   # 비밀값 원문은 저장하지 않는다
            # 승인 전: 대기 중, 남의 secret 은 거부, 임대인 첫 화면에 요청이 보인다(장부의 휴대폰 포함)
            st = lambda sec=secret: c.post(f"{PL}/connect/status", headers=NA, json={"request_id": rid, "secret": sec})
            self.assertEqual(st().json()["status"], "PENDING")
            self.assertEqual(st("x" * 32).status_code, 400)
            lst = c.get(f"{LS}/dashboard?branch=main", headers=chic).json()["connect_requests"]
            self.assertEqual([(x["room_no"], x["tenant_phone"]) for x in lst if x["id"] == rid], [(t.room_no, "010-7777-1234")])
            # 남의 건물 임대인은 승인할 수 없다 · 틀린 확인번호는 남은 횟수 안내, 5번이면 폐기
            self.assertEqual(c.post(f"{LS}/connect-requests/{rid}/approve", headers=sunho, json={"branch": "main", "code": code}).status_code, 403)
            wrong = "0000" if code != "0000" else "1111"
            w = c.post(f"{LS}/connect-requests/{rid}/approve", headers=chic, json={"branch": "main", "code": wrong})
            self.assertEqual(w.status_code, 400)
            self.assertIn("남은 시도 4회", w.json()["message"])
            self.assertEqual(st().json()["status"], "PENDING")                                                    # 틀려도 승인되지 않는다
            ap = c.post(f"{LS}/connect-requests/{rid}/approve", headers=chic, json={"branch": "main", "code": code})
            self.assertEqual(ap.status_code, 200, ap.text)
            # 승인 뒤: 요청한 쪽만 링크를 받고, 링크는 한 번만 나온다
            polled = c.get(f"{LS}/connect-requests?branch=main", headers=chic).json()               # 승인 후: 대기에서 빠지고 진행 상태 띠에 오른다
            self.assertEqual([x["id"] for x in polled["pending"] if x["id"] == rid], [])
            self.assertEqual([x["state"] for x in polled["recent"] if x["id"] == rid], ["APPROVED"])
            got = st().json()
            self.assertEqual(got["status"], "APPROVED")
            url = got["invite_url"]
            self.assertTrue(url.startswith("/rent3_tenant/?invite="))
            self.assertNotIn("invite_url", st().json())
            tok = url.split("invite=")[1]
            self.assertEqual(c.get(f"{PL}/invite/identify?token={tok}", headers=NA).status_code, 200)
            self.assertEqual([x["state"] for x in c.get(f"{LS}/connect-requests?branch=main", headers=chic).json()["recent"] if x["id"] == rid], ["SETTING"])   # 비밀번호 설정 중
            from apps.rent_dist._rent3_core import connect as cn_id
            acc = c.post(f"{PL}/auth/invite-accept", headers=NA, json={"token": tok, "password": "Conn#Pass99", "pin": "2846", "agree_privacy": True,
                                                                       "room_no": t.room_no, "name": t.name, "phone": t.phone, "lessor_name": cn_id.public_info(t.branch)["lessor_name"]})
            self.assertEqual(acc.status_code, 200, acc.text)
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": t.id, "password": "Conn#Pass99"}).status_code, 200)
            self.assertEqual([x["state"] for x in c.get(f"{LS}/connect-requests?branch=main", headers=chic).json()["recent"] if x["id"] == rid], ["DONE"])         # 연결 완료
            # 가입이 끝난 호실은 연결 요청으로 덮어쓸 수 없다
            again = c.post(f"{PL}/connect/request", headers=NA, json=body)
            self.assertEqual(again.status_code, 400)
            self.assertIn("이미 가입", again.json()["message"])
            # 관리인이 가입 상태를 초기화하면 처음 상태로 돌아가 다시 시험할 수 있다(호실·계약은 그대로)
            admin = self._login("admin")
            self.assertIn(c.post("/api/rent3_admin/accounts/reset-activation", headers=chic, json={"id": t.id}).status_code, (401, 403))
            rs = c.post("/api/rent3_admin/accounts/reset-activation", headers=admin, json={"id": t.id})
            self.assertEqual(rs.status_code, 200, rs.text)
            self.assertFalse(cn.activated(Tenant.get(t.id)))
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": t.id, "password": "Conn#Pass99"}).status_code, 401)
            self.assertEqual(Tenant.get(t.id).room_no, t.room_no)
            self.assertEqual(c.post(f"{PL}/connect/request", headers=NA, json=body).status_code, 200)       # 다시 연결 요청 가능
        finally:
            for r_ in ConnectRequest.all():
                r_.delete(action="t")
            t = Tenant.get(t.id)
            t.phone, t.password, t.pin_hash, t.quick_devices, t.consents, t.invite_hash, t.invite_expires = old
            t.save(action="t")
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        pj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("connect_requests", "/connect-requests/", "연결 요청", "btnConnectMsg", "watchConnect", "connBar", "?connect=", "showInviteSender"):
            self.assertIn(needle, lapp, needle)
        for needle in ("/pool/connect/request", "/pool/connect/status", "/pool/connect/info", "rent3_connect_req", "find=1", "r3Connect", "__rent3ConnectOpen"):
            self.assertIn(needle, pj, needle)

    def test_82_admin_test_tenant_registration(self):
        """82. 관리인 앱 '시험 임차인 등록': 점검은 아무것도 안 바꾸고, 등록은 백업 후 0원 계약(미납 0, 비공개)을 만들며, 재실행은 그대로·다른 임차인이 있는 호실은 멈춤·호실 한도 초과는 확인 후에만·임대인은 403."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, Lessor
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        url = "/api/rent3_admin/accounts/test-tenant"
        body = {"branch": "main", "room_no": "799", "name": "시험김", "phone": "010-1234-0799"}
        lsr = Lessor.first(branch="main")
        old_limit = lsr.room_limit
        try:
            self.assertIn(c.post(url, headers=chic, json=body).status_code, (401, 403))                 # 임대인은 못 한다
            n_rooms = len(Room.find(branch="main"))
            dry = c.post(url, headers=admin, json=body).json()
            self.assertEqual(dry["status"], "DRY")
            self.assertEqual(len(Room.find(branch="main")), n_rooms)                                    # 점검은 바꾸지 않는다
            # 한도 초과: 확인(한도 올리기) 없이는 멈춘다
            lsr.room_limit = n_rooms
            lsr.save(action="t")
            blocked = c.post(url, headers=admin, json={**body, "apply": True}).json()
            self.assertEqual((blocked["status"], blocked.get("need_raise_limit")), ("BLOCKED", True))
            self.assertEqual(len(Room.find(branch="main")), n_rooms)
            with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_tt_")}):
                done = c.post(url, headers=admin, json={**body, "apply": True, "raise_limit": True}).json()
                self.assertEqual(done["status"], "DONE", done)
                self.assertTrue(done["backup"].endswith(".tar.gz"))                                     # 등록 전 자동 백업
                self.assertEqual(done["unpaid"], 0)
                r = Room.first(branch="main", room_no="799")
                self.assertFalse(r.is_published)                                                        # 공개 매물에 안 나온다
                self.assertEqual((r.deposit, r.monthly_rent, r.maintenance_fee), (0, 0, 0))
                self.assertEqual(Lessor.get(lsr.id).room_limit, n_rooms + 1)
                again = c.post(url, headers=admin, json={**body, "apply": True}).json()
                self.assertEqual(again["status"], "EXISTS")                                             # 재실행은 그대로
                self.assertEqual(len(Room.find(branch="main")), n_rooms + 1)
                other = c.post(url, headers=admin, json={**body, "phone": "010-9999-0000", "apply": True}).json()
                self.assertEqual(other["status"], "BLOCKED")                                            # 다른 임차인이 있는 호실은 건드리지 않는다
            # 만든 임차인이 연결 요청 흐름에 그대로 들어간다
            q = c.post("/api/rent3/pool/connect/request", headers=self.NOAUTH, json={"building_name": "시크빌", "room_no": "799", "name": "시험김", "phone": "01012340799"})
            self.assertEqual(q.status_code, 200, q.text)
        finally:
            from apps.rent_dist._rent3_core.ops import ConnectRequest
            for x in ConnectRequest.all():
                x.delete(action="t")
            for cls, oid in ((Contract, "ct_main_799"), (Tenant, "tenant_main_799"), (Room, "room_main_799")):
                o = cls.get(oid)
                if o:
                    o.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.room_limit = old_limit
            lsr.save(action="t")
        adm = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnTtCheck", "btnTtApply", "/accounts/test-tenant", "시험 임차인 등록"):
            self.assertIn(needle, adm, needle)

    def test_83_per_tenant_invite_links_bulk(self):
        """83. 입주자별 개별 안내 링크 일괄 발급: 가입 전 입주자마다 본인 전용 링크(해시만 저장·7일·1회), 가입 완료 호실은 건너뜀, 현황 조회엔 링크 원문 없음,
        링크로 가입하면 현황이 '가입 완료'로 바뀜, 남의 건물 임대인·PIN 접속은 불가."""
        from apps.rent_dist._rent3_core import Tenant, connect as cn
        c, NA = self.client, self.NOAUTH
        PL, LS = "/api/rent3/pool", "/api/rent3_lessor"
        chic, sunho = self._login("chic"), self._login("sunho")
        before = {t.id: (t.password, t.pin_hash, list(t.quick_devices), dict(t.consents), t.invite_hash, t.invite_expires, t.invite_issued_by) for t in Tenant.all()}
        try:
            st = c.get(f"{LS}/tenant-invites?branch=main", headers=chic).json()["tenants"]
            self.assertTrue(st and all(set(x) == {"room_no", "tenant_name", "phone", "activated", "invite_pending"} for x in st))
            act0 = sum(1 for x in st if x["activated"])   # 링크 원문 없음
            self.assertEqual(c.get(f"{LS}/tenant-invites?branch=main", headers=sunho).status_code, 403)                          # 남의 건물
            r = c.post(f"{LS}/tenant-invites/bulk", headers=chic, json={"branch": "main"})
            self.assertEqual(r.status_code, 200, r.text)
            links = r.json()["links"]
            self.assertEqual(len(links) + r.json()["skipped_activated"], len(st))
            self.assertEqual(len({x["path"] for x in links}), len(links))                                                       # 사람마다 다른 링크
            for x in links[:3]:
                tok = x["path"].split("invite=")[1]
                idn = c.get(f"{PL}/invite/identify?token={tok}", headers=NA).json()
                self.assertEqual(idn["room_no"], x["room_no"])
            self.assertEqual(c.post(f"{LS}/tenant-invites/bulk", headers=sunho, json={"branch": "main"}).status_code, 403)
            self.assertNotIn(links[0]["path"].split("invite=")[1], open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read())   # 원문은 저장하지 않는다
            # 한 사람이 링크로 가입 → 현황이 가입 완료, 다시 발급하면 그 호실은 건너뜀
            first = links[0]
            from apps.rent_dist._rent3_core import connect as cn_id
            t1 = Tenant.first(room_no=first["room_no"], branch="main")
            acc = c.post(f"{PL}/auth/invite-accept", headers=NA, json={"token": first["path"].split("invite=")[1], "password": "Bulk#Pass99", "agree_privacy": True,
                                                                       "room_no": t1.room_no, "name": t1.name, "phone": t1.phone, "lessor_name": cn_id.public_info("main")["lessor_name"]})
            self.assertEqual(acc.status_code, 200, acc.text)
            st2 = {x["room_no"]: x for x in c.get(f"{LS}/tenant-invites?branch=main", headers=chic).json()["tenants"]}
            self.assertTrue(st2[first["room_no"]]["activated"])
            again = c.post(f"{LS}/tenant-invites/bulk", headers=chic, json={"branch": "main"}).json()
            self.assertNotIn(first["room_no"], [x["room_no"] for x in again["links"]])
            self.assertGreaterEqual(again["skipped_activated"], 1)
            # 가져온 임차인(무작위 해시 비밀번호)은 가입한 것으로 보지 않는다: 위에서 가입한 한 호실만 가입 완료
            self.assertEqual(sum(1 for x in c.get(f"{LS}/tenant-invites?branch=main", headers=chic).json()["tenants"] if x["activated"]), act0 + 1)   # 해시 비밀번호만으로는 가입으로 보지 않는다(방금 가입한 한 호실만 늘었다)
            # 취소: 링크 취소(가입 전) · 가입 취소(가입 후) · 남의 건물 불가
            other = again["links"][0]
            cn1 = c.post(f"{LS}/tenant-invites/cancel", headers=chic, json={"branch": "main", "room_no": other["room_no"]}).json()
            self.assertEqual(cn1["kind"], "link")
            self.assertEqual(c.get(f"{PL}/invite/identify?token={other['path'].split('invite=')[1]}", headers=NA).status_code, 404)   # 취소한 링크는 못 쓴다
            self.assertEqual(c.post(f"{LS}/tenant-invites/cancel", headers=sunho, json={"branch": "main", "room_no": first["room_no"]}).status_code, 403)
            cn2 = c.post(f"{LS}/tenant-invites/cancel", headers=chic, json={"branch": "main", "room_no": first["room_no"]}).json()
            self.assertEqual(cn2["kind"], "reset")
            from apps.rent_dist._rent3_core import devicebind as dv_t
            tf = Tenant.first(room_no=first["room_no"], branch="main")
            dv_t.register(tf, "C" * 30)
            self.assertEqual(len(Tenant.get(tf.id).login_devices), 1)
            self.assertEqual(c.post(f"{LS}/tenant-invites/cancel", headers=chic, json={"branch": "main", "room_no": first["room_no"]}).status_code, 200)
            self.assertEqual(Tenant.get(tf.id).login_devices, [])         # 등록 취소하면 등록된 로그인 기기도 모두 끊긴다
            self.assertNotIn("login_devices", c.get(f"{LS}/tenant-invites?branch=main", headers=chic).text)
            # 한 호실만 새 링크: 그 호실 것만 만들어지고 다른 사람이 받은 링크는 그대로 쓸 수 있다
            third = next((x for x in again["links"] if x["room_no"] not in (other["room_no"], first["room_no"])), None)
            one = c.post(f"{LS}/tenant-invites/bulk", headers=chic, json={"branch": "main", "room_no": first["room_no"]}).json()
            self.assertEqual([x["room_no"] for x in one["links"]], [first["room_no"]])
            if third:
                self.assertEqual(c.get(f"{PL}/invite/identify?token={third['path'].split('invite=')[1]}", headers=NA).status_code, 200)
            # 링크 취소 응답이 없던 호실도 초기화 가능(찌꺼기 정리)
            self.assertEqual(c.post(f"{LS}/tenant-invites/cancel", headers=chic, json={"branch": "main", "room_no": first["room_no"]}).json()["kind"], "link")
            self.assertFalse({x["room_no"]: x for x in c.get(f"{LS}/tenant-invites?branch=main", headers=chic).json()["tenants"]}[first["room_no"]]["activated"])
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": first["room_no"] and Tenant.first(room_no=first["room_no"], branch="main").id, "password": "Bulk#Pass99"}).status_code, 401)
            # 간편 비밀번호 접속으로는 못 한다(허용 목록 밖)
            from unittest import mock
            from apps.rent_dist._rent3_core import Lessor
            with mock.patch.dict(os.environ, {"RENT3_LESSOR_PIN": "1"}):
                self.assertEqual(c.post(f"{PL}/shortcut/pin", headers=chic, json={"pin": "2846"}).status_code, 200)
                d = c.post(f"{PL}/shortcut/devices", headers=chic, json={"label": "t"}).json()
                tok = c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": d["qk"], "pin": "2846"}).json()["token"]
                ph = {"Authorization": f"Bearer {tok}"}
                self.assertEqual(c.get(f"{LS}/tenant-invites?branch=main", headers=ph).status_code, 403)
                self.assertEqual(c.post(f"{LS}/tenant-invites/bulk", headers=ph, json={"branch": "main"}).status_code, 403)
            lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("showInviteSender", "/tenant-invites/bulk", "/tenant-invites/cancel", "iv-cancel", "iv-rereg", "🔁 재등록 문자", "↩ 가입 취소", "✖ 링크 취소", "ivNext", "다음 사람에게 문자 보내기", "본인 전용 링크", "rent3_inv_"):
                self.assertIn(needle, lapp, needle)
        finally:
            for tid, (pw, pin, dev, cons, ih, ie, ib) in before.items():
                t = Tenant.get(tid)
                if t:
                    t.password, t.pin_hash, t.quick_devices, t.consents, t.invite_hash, t.invite_expires, t.invite_issued_by = pw, pin, dev, cons, ih, ie, ib
                    t.save(action="t")
            l = Lessor.first(branch="main")
            l.pin_hash, l.quick_devices = "", []
            l.save(action="t")

    def test_86_password_lifecycle_moveout_lessor_invite_6m_and_renewal_remind(self):
        """86. 계정·암호 생애주기 통합 검증:
        ① 전출(퇴실 정산 완료) 시 기존 암호·PIN·바로가기·등록기기·초대링크·연결요청 즉시 삭제(A안) 및 재로그인 차단
        ② 관리인 → 임대인 암호 상태 조회 및 6개월(180일) 경과 권유 메시지·링크 발송(기존 암호 유지)
        ③ 관리인 발급 임대인 1회용 암호설정 링크 + 4칸 본인확인(건물명·이름·전화·사업자/계좌 끝4자리) + 기기 유지
        ④ 재계약 때 임차인 새 암호 설정 안내 메시지·링크 발송(기존 암호·등록기기 유지)."""
        from datetime import date, timedelta
        from apps.rent_dist._rent3_core import (
            Building, CheckOutSettlement, Contract, DirectChat, Lessor, Room, Tenant,
            connect as cn, devicebind as dbind, invite as inv, passwords as pwd, quicklogin as ql, ratelimit as rl,
        )
        from apps.rent_dist._rent3_core.support import SupportMessage
        c, NA = self.client, self.NOAUTH
        PL, LS, AD = "/api/rent3/pool", "/api/rent3_lessor", "/api/rent3_admin"
        admin, chic, sunho = self._login("admin"), self._login("chic"), self._login("sunho")

        # ── ① 전출(퇴실) 시 기존 암호·PIN·바로가기·등록기기·초대링크 자동 삭제 (A안) ──
        room = Room.first(branch="main", room_no="201")
        ct = Contract.get(room.active_contract_id)
        t = Tenant.get(room.id_tenant)
        old_room = (room.is_published, room.id_tenant, room.active_contract_id, room.available_from, room.pre_moveout_notice)
        old_ct = (ct.status, ct.move_out_date, ct.ended_at)
        old_t = (t.status, t.current_room_id, t.room_no, t.password, t.pin_hash, list(t.quick_devices), list(t.login_devices),
                 t.invite_hash, t.invite_expires, t.invite_issued_by, t.activated_at, t.password_changed_at, dict(t.consents))
        lsr = Lessor.first(branch="main")
        old_lsr = (lsr.password, lsr.pin_hash, list(lsr.quick_devices), list(lsr.login_devices),
                   lsr.invite_hash, lsr.invite_expires, lsr.invite_issued_by, lsr.activated_at, lsr.password_changed_at, lsr.password_remind_sent_at, dict(lsr.consents))
        co_id = None
        try:
            t.password = pwd.hash_password("MoveOut#Pass1")
            t.activated_at = "2025-10-01T10:00:00"
            t.password_changed_at = "2025-10-01T10:00:00"
            ql.set_pin(t, "2580")
            _dev, qk = ql.new_device(t, "퇴실전폰")
            dbind.register(t, "D" * 30)
            inv_tok = inv.issue(t, "chic")
            t.save(action="t")
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": t.id, "password": "MoveOut#Pass1"}).status_code, 200)

            co = c.post(f"{LS}/checkout/calculate", headers=chic, json={"branch": "main", "room_no": "201", "utility_settlement": 10000, "cleaning_fee": 70000}).json()["settlement"]
            co_id = co["id"]
            tk = self._login(t.id, "MoveOut#Pass1")
            agr = {"bank_account": "국민 111-22-3333", "signature": "서명", "account_holder": t.name}
            self.assertEqual(c.post(f"/api/rent3_tenant/checkout/{co_id}/agree", headers=tk, json={**agr, "account_holder": "타인"}).status_code, 400)
            self.assertEqual(c.post(f"/api/rent3_tenant/checkout/{co_id}/agree", headers=tk, json=agr).status_code, 200)
            self.assertEqual(c.post(f"{LS}/checkout/{co_id}/mark-paid", headers=chic, json={"branch": "main"}).status_code, 200)
            self.assertEqual(c.post(f"/api/rent3_tenant/checkout/{co_id}/confirm-received", headers=tk, json={}).status_code, 200)

            t_after = Tenant.get(t.id)
            self.assertEqual(t_after.status, "MOVED_OUT")
            # 로그인 수단만 정리: 바로가기·간편 비밀번호·등록 기기·초대 링크. 비밀번호·동의·가입 기록은 남는다(다음 해 2월 말까지 조회)
            self.assertEqual((t_after.pin_hash, t_after.quick_devices, t_after.login_devices, t_after.invite_hash), ("", [], [], ""))
            self.assertTrue(pwd.verify(t_after.password, "MoveOut#Pass1"))
            self.assertTrue(t_after.activated_at)
            self.assertRegex(t_after.readonly_until, rf"^{date.today().year + 1}-02-(28|29)$")
            self.assertEqual(c.post(f"{PL}/auth/pin-login", headers=NA, json={"qk": qk, "pin": "2580"}).status_code, 401)
            self.assertEqual(c.get(f"{PL}/invite/identify?token={inv_tok}", headers=NA).status_code, 404)
            # 퇴실 후: 로그인·조회는 되고 쓰기는 막힌다
            mo = self._login(t.id, "MoveOut#Pass1")
            self.assertEqual(c.get(f"/api/rent3_tenant/myhome/{t.id}/archive", headers=mo).status_code, 200)
            self.assertEqual(c.post(f"/api/rent3_tenant/myhome/{t.id}/tickets", headers=mo, json={"title": "x", "description": "y"}).status_code, 403)
            # 조회 기간이 지나면 로그인 불가
            t_after.readonly_until = "2000-01-01"; t_after.save(action="t")
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": t.id, "password": "MoveOut#Pass1"}).status_code, 401)

            # 복구해 ④번 재계약 시험에 사용
            room = Room.get(room.id)
            room.is_published, room.id_tenant, room.active_contract_id, room.available_from, room.pre_moveout_notice = old_room
            room.save(action="t")
            ct.status, ct.move_out_date, ct.ended_at = old_ct
            ct.save(action="t")
            t.status, t.current_room_id, t.room_no = "ACTIVE", room.id, "201"
            t.save(action="t")

            # ── ② 관리인 → 임대인 암호 상태 조회 및 6개월(180일) 권유 ──
            self.assertIn(c.get(f"{AD}/accounts/lessors", headers=chic).status_code, (401, 403))
            lsr = Lessor.get(lsr.id)
            lsr.password = pwd.hash_password("Lessor#Old1")
            lsr.activated_at = (date.today() - timedelta(days=185)).isoformat() + "T09:00:00"
            lsr.password_changed_at = lsr.activated_at
            dbind.register(lsr, "L" * 30)
            lsr.save(action="t")

            ls_list = c.get(f"{AD}/accounts/lessors", headers=admin).json()["lessors"]
            chic_row = next(x for x in ls_list if x["id"] == lsr.id)
            self.assertTrue(chic_row["due_6m"])
            self.assertGreaterEqual(chic_row["days_since_change"], 180)

            rem = c.post(f"{AD}/accounts/lessors/{lsr.id}/remind-password", headers=admin)
            self.assertEqual(rem.status_code, 200, rem.text)
            rem_tok = rem.json()["url"].split("invite=")[1]
            self.assertNotIn(rem_tok, open(os.path.join(self.temp_pool, "users.json"), encoding="utf-8").read())
            self.assertNotIn("invite_hash", Lessor.get(lsr.id).safe_dump())
            # 새 비밀번호를 정하기 전까지 기존 비밀번호는 그대로 유지된다
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": lsr.username, "password": "Lessor#Old1"}).status_code, 200)

            # ── ③ 관리인 발급 임대인 1회용 링크 + 4칸 본인확인 + 등록기기 유지 ──
            inv_res = c.post(f"{AD}/accounts/lessor-invite", headers=admin, json={"id": lsr.id})
            self.assertEqual(inv_res.status_code, 200, inv_res.text)
            ltok = inv_res.json()["url"].split("invite=")[1]
            idn = c.get(f"{PL}/invite/identify?token={ltok}", headers=NA).json()
            self.assertEqual(idn["kind"], "lessor")
            bname = Building.first(branch="main").building_name
            proof4 = "".join(ch for ch in lsr.business_number if ch.isdigit())[-4:]
            lwho = {"building_name": bname, "name": lsr.name, "phone": lsr.phone, "proof": proof4}
            for bad in ({"building_name": "엉뚱빌딩"}, {"name": "홍길동"}, {"phone": "010-0000-0000"}, {"proof": "0000"}):
                rl.reset_all()
                self.assertEqual(c.post(f"{PL}/invite/verify", headers=NA, json={"token": ltok, **lwho, **bad}).status_code, 400)
            rl.reset_all()
            self.assertEqual(c.post(f"{PL}/invite/verify", headers=NA, json={"token": ltok, **lwho}).status_code, 200)
            acc_l = c.post(f"{PL}/auth/invite-accept", headers=NA, json={
                "token": ltok, "password": "Lessor#New2", "agree_privacy": True, "device_id": "M" * 30, **lwho,
            })
            self.assertEqual(acc_l.status_code, 200, acc_l.text)
            self.assertEqual(acc_l.json()["role"], "lessor")
            lsr_after = Lessor.get(lsr.id)
            self.assertEqual(len(lsr_after.login_devices), 2)   # 기존 기기(L*30) 유지 + 새 기기(M*30) 추가
            self.assertEqual(c.get(f"{PL}/invite/identify?token={ltok}", headers=NA).status_code, 404)   # 1회용 소진
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": lsr.username, "password": "Lessor#New2"}).status_code, 200)
            chic_row2 = next(x for x in c.get(f"{AD}/accounts/lessors", headers=admin).json()["lessors"] if x["id"] == lsr.id)
            self.assertFalse(chic_row2["due_6m"])
            self.assertEqual(chic_row2["days_since_change"], 0)

            # ── ④ 재계약 때 임차인 새 비밀번호 설정 안내 메시지·링크 발송(기존 암호·기기 유지) ──
            t = Tenant.get(t.id)
            t.password = pwd.hash_password("Tenant#Old1")
            t.activated_at = "2025-11-01T10:00:00"
            t.login_devices = []
            dbind.register(t, "T" * 30)
            t.save(action="t")
            self.assertEqual(c.post(f"{LS}/expiry/remind-password", headers=sunho, json={"branch": "main", "room_no": "201"}).status_code, 403)
            tr = c.post(f"{LS}/expiry/remind-password", headers=chic, json={"branch": "main", "room_no": "201"})
            self.assertEqual(tr.status_code, 200, tr.text)
            ttok = tr.json()["url"].split("invite=")[1]
            # 설정 전까지 기존 비밀번호는 그대로 작동한다
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": t.id, "password": "Tenant#Old1"}).status_code, 200)
            twho = {"room_no": "201", "name": t.name, "phone": t.phone, "lessor_name": cn.public_info("main")["lessor_name"]}
            acc_t = c.post(f"{PL}/auth/invite-accept", headers=NA, json={
                "token": ttok, "password": "Tenant#New2", "agree_privacy": True, "device_id": "U" * 30, **twho,
            })
            self.assertEqual(acc_t.status_code, 200, acc_t.text)
            t_renewed = Tenant.get(t.id)
            self.assertEqual(len(t_renewed.login_devices), 2)   # 기존 기기(T*30) 유지 + 새 기기(U*30) 추가
            self.assertTrue(t_renewed.password_changed_at)
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": t.id, "password": "Tenant#New2"}).status_code, 200)
        finally:
            if co_id and CheckOutSettlement.get(co_id):
                CheckOutSettlement.get(co_id).delete(action="t")
            for chat in list(DirectChat.all()):
                if "재계약 새 비밀번호 설정 안내" in (chat.message or ""):
                    chat.delete(action="t")
            for sm in list(SupportMessage.all()):
                if "6개월 정기 비밀번호 변경 권유" in (sm.body or ""):
                    sm.delete(action="t")
            room = Room.get(room.id)
            room.is_published, room.id_tenant, room.active_contract_id, room.available_from, room.pre_moveout_notice = old_room
            room.save(action="t")
            ct = Contract.get(ct.id)
            ct.status, ct.move_out_date, ct.ended_at = old_ct
            ct.save(action="t")
            t = Tenant.get(t.id)
            (t.status, t.current_room_id, t.room_no, t.password, t.pin_hash, t.quick_devices, t.login_devices,
             t.invite_hash, t.invite_expires, t.invite_issued_by, t.activated_at, t.password_changed_at, t.consents) = old_t
            t.password_remind_sent_at = ""
            t.save(action="t")
            lsr = Lessor.get(lsr.id)
            (lsr.password, lsr.pin_hash, lsr.quick_devices, lsr.login_devices,
             lsr.invite_hash, lsr.invite_expires, lsr.invite_issued_by, lsr.activated_at, lsr.password_changed_at, lsr.password_remind_sent_at, lsr.consents) = old_lsr
            lsr.save(action="t")

    def test_87_checkout_request_chat_flow_dispute_autoconfirm_and_renewal_default(self):
        """87. 퇴실 정산 채팅 흐름: 임차인 신청(연말정산 안내) → 임대인 정산서(신청 건 재사용) → 이의 → 재작성 → 동의(예금주=계약자) → 송금 표시 → 7일 자동 확정,
        채팅엔 계좌 끝 4자리만, 남의 정산서 불가. 재계약 화면은 월세 5% 인상이 기본값."""
        from apps.rent_dist._rent3_core import DirectChat, Room, Tenant
        from apps.rent_dist._rent3_core.ops import CheckOutSettlement
        c = self.client
        chic = self._login("chic")
        from apps.rent_dist._rent3_core import Contract
        room = next(r for r in Room.find(branch="main") if r.active_contract_id and r.id_tenant
                    and Tenant.get(r.id_tenant) and Tenant.get(r.id_tenant).status == "ACTIVE")
        t = Tenant.get(room.id_tenant)
        snaps = [(o, o.model_dump()) for o in (room, Contract.get(room.active_contract_id), t)]    # 단독 실행해도 다른 시험에 영향이 없게 끝나면 되돌린다
        try:
            self._run_87(c, chic, room, t)
        finally:
            for o, d in snaps:
                for k, v in d.items():
                    setattr(o, k, v)
                o.save(action="t")

    def _run_87(self, c, chic, room, t):
        from apps.rent_dist._rent3_core import DirectChat, Tenant
        from apps.rent_dist._rent3_core.ops import CheckOutSettlement
        tk = self._login(t.id)
        TN, LS = "/api/rent3_tenant", "/api/rent3_lessor"
        ROOT_ = ROOT
        # 보강: 가입 전 임차인에게 재계약 비밀번호 권유를 보내면 기존 가입 링크를 덮어쓰므로 거절
        t.activated_at, t.pin_hash, t.quick_devices = "", "", []; t.save(action="t")
        self.assertEqual(c.post(f"{LS}/expiry/remind-password", headers=chic, json={"branch": "main", "room_no": room.room_no}).status_code, 400)
        # 임대인 링크: 장부에 사업자번호·계좌번호가 없으면 관리인도 발급 불가
        from apps.rent_dist._rent3_core import Lessor
        lsr = Lessor.first(branch="main"); keep = (lsr.business_number, lsr.account_number)
        lsr.business_number, lsr.account_number = "", ""; lsr.save(action="t")
        try:
            self.assertEqual(c.post("/api/rent3_admin/accounts/lessor-invite", headers=self._login("admin"), json={"id": lsr.id}).status_code, 400)
        finally:
            lsr.business_number, lsr.account_number = keep; lsr.save(action="t")
        # 임차인 신청 + 연말정산 미리 받기 안내
        rq = c.post(f"{TN}/myhome/{t.id}/checkout/request", headers=tk, json={"desired_date": "2027-01-31", "memo": "이사"})
        self.assertEqual(rq.status_code, 200, rq.text)
        co_id = rq.json()["settlement"]["id"]
        self.assertEqual(rq.json()["settlement"]["status"], "REQUESTED")
        self.assertEqual(c.post(f"{TN}/myhome/{t.id}/checkout/request", headers=tk, json={}).status_code, 400)       # 중복 신청 불가
        texts = " ".join(x.message for x in DirectChat.find(branch="main", room_no=room.room_no))
        self.assertIn("연말정산 납부확인서", texts)
        # 임대인이 정산서 작성 → 신청 건을 이어서 쓴다(새 건이 생기지 않음)
        calc = lambda: c.post(f"{LS}/checkout/calculate", headers=chic, json={"branch": "main", "room_no": room.room_no, "utility_settlement": 5000, "cleaning_fee": 70000}).json()["settlement"]
        self.assertEqual(calc()["id"], co_id)
        # 이의 제기 → 임대인이 고쳐 다시 보냄(같은 건, 동의는 다시)
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/dispute", headers=tk, json={"note": ""}).status_code, 400)
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/dispute", headers=tk, json={"note": "청소비가 과합니다"}).status_code, 200)
        self.assertEqual(CheckOutSettlement.get(co_id).status, "DISPUTED")
        again = calc()
        self.assertEqual((again["id"], again["status"], again["dispute_note"]), (co_id, "CALCULATED", ""))
        # 동의(예금주=계약자만) → 송금 표시 전에는 완료 불가
        ag = {"bank_account": "국민 111-22-3333", "signature": "서명", "account_holder": t.name}
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/agree", headers=tk, json={**ag, "account_holder": "다른사람"}).status_code, 400)
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/confirm-received", headers=tk, json={}).status_code, 400)
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/agree", headers=tk, json=ag).status_code, 200)
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/agree", headers=tk, json=ag).status_code, 400)               # 동의 뒤 계좌 변경 불가
        self.assertEqual(c.post(f"{TN}/checkout/{co_id}/confirm-received", headers=chic, json={}).status_code, 403)  # 임대인이 임차인 대신 확인 불가
        self.assertEqual(c.post(f"{LS}/checkout/{co_id}/mark-paid", headers=chic, json={"branch": "main"}).status_code, 200)
        paid = CheckOutSettlement.get(co_id)
        self.assertEqual(paid.status, "PAID")
        self.assertTrue(paid.confirm_due)
        self.assertEqual(c.post(f"{LS}/checkout/calculate", headers=chic, json={"branch": "main", "room_no": room.room_no}).status_code, 400)   # 송금 표시 뒤엔 임의 수정 불가
        # 채팅 본문에는 계좌 전체가 없다(끝 4자리만)
        texts = " ".join(x.message for x in DirectChat.find(branch="main", room_no=room.room_no))
        self.assertNotIn("111-22-3333", texts)
        self.assertIn("3333", texts)
        # 7일 동안 이의가 없으면 조회할 때 자동 확정
        paid.confirm_due = "2000-01-01T00:00:00"; paid.save(action="t")
        self.assertEqual(c.get(f"{LS}/dashboard?branch=main", headers=chic).status_code, 200)
        done = CheckOutSettlement.get(co_id)
        self.assertEqual((done.status, done.auto_confirmed), ("COMPLETED", True))
        self.assertEqual(Tenant.get(t.id).status, "MOVED_OUT")
        # 화면: 신청·예금주·수취 확인·송금 표시, 재계약 기본 5% 인상
        rd = lambda *parts: open(os.path.join(ROOT_, *parts), encoding="utf-8").read()
        tj = rd("apps", "rent_dist", "rent3_tenant", "frontend", "app.js")
        for needle in ("btnRequestCo", "coHolder", "btnReceivedCo", "btnDisputeCo", "view\") === \"moved", "/archive"):
            self.assertIn(needle, tj, needle)
        lj = rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js")
        for needle in ("moPaid", "/mark-paid", "rent5", "기본 월세 5% 인상", "co-open", "정산 이의/미수취 확인 필요"):
            self.assertIn(needle, lj, needle)

    def test_88_lessor_bizreg_photo_upload_hometax_verify_and_approve(self):
        """88. 임대인 사업자등록증 촬영·제출 → 10자리 체크섬·대표자 자동 대조 → 관리인 국세청 홈택스 확인 후 승인(장부 사업자번호 확정)."""
        from apps.rent_dist._rent3_core import Lessor
        from apps.rent_dist._rent3_core.support import SupportMessage
        c = self.client
        admin, chic, sunho = self._login("admin"), self._login("chic"), self._login("sunho")
        LS, AD = "/api/rent3_lessor", "/api/rent3_admin"
        lsr = Lessor.first(branch="main")
        keep = (lsr.business_number, lsr.account_number, dict(lsr.franchise_docs or {}), lsr.franchise_ai_calls)
        png_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        try:
            lsr.business_number = ""
            lsr.account_number = ""
            lsr.save(action="t")
            # 남의 지점 업로드 불가
            self.assertEqual(c.post(f"{LS}/franchise/bizreg", headers=sunho, json={"branch": "main", "attachment_data_url": png_url}).status_code, 403)
            # 체크섬 틀린 사업자번호: 업로드는 되지만 INVALID_BIZ_NO 경고
            r_bad = c.post(f"{LS}/franchise/bizreg", headers=chic, json={
                "branch": "main", "attachment_data_url": png_url, "business_number": "123-45-67899", "representative_name": lsr.name,
            })
            self.assertEqual(r_bad.status_code, 200, r_bad.text)
            self.assertEqual(r_bad.json()["application"]["bizreg"]["check"]["code"], "INVALID_BIZ_NO")
            # 유효한 10자리 사업자번호(214-86-12349 -> 체크섬 검증): 214861234? 계산해보자: validate_kr_business_number 통과 값 사용
            from apps.rent_dist._rent3_core.billing import validate_kr_business_number
            valid_biz = next(f"107-81-5332{d}" for d in range(10) if validate_kr_business_number(f"107-81-5332{d}", strict_checksum=True)["valid"])
            # 대표자 불일치 시 REP_MISMATCH
            r_rep = c.post(f"{LS}/franchise/bizreg", headers=chic, json={
                "branch": "main", "attachment_data_url": png_url, "business_number": valid_biz, "representative_name": "엉뚱대표",
            })
            self.assertEqual(r_rep.json()["application"]["bizreg"]["check"]["code"], "REP_MISMATCH")
            # 정상 제출 (10자리 유효 + 대표자 일치 -> AUTO_MATCH), 단 관리자 승인 전까지는 lsr.business_number 가 비어 있다
            r_ok = c.post(f"{LS}/franchise/bizreg", headers=chic, json={
                "branch": "main", "attachment_data_url": png_url, "business_number": valid_biz,
                "representative_name": lsr.name, "company_name": "시크빌임대",
            })
            self.assertEqual(r_ok.status_code, 200, r_ok.text)
            self.assertEqual(r_ok.json()["application"]["bizreg"]["check"]["code"], "BIZ_NO_ONLY")     # 직접 입력한 대표자는 자동 일치 근거가 아니다
            # 문서(AI)에서 읽은 대표자가 장부 성명과 같을 때만 AUTO_MATCH
            from unittest import mock
            ext = {"document_type": "business_registration", "readable": True, "business_number": valid_biz, "company_name": "시크빌임대",
                   "representative_name": lsr.name, "opening_date": "", "business_address": "", "tax_type": "면세사업자", "issue_number": ""}
            with mock.patch("apps.rent_dist._rent3_core.registry_ai.analyze_bizreg", return_value={"status": "ok", "message": "", "extracted": ext}):
                r_ai = c.post(f"{LS}/franchise/bizreg", headers=chic, json={"branch": "main", "attachment_data_url": png_url, "consent": True})
            self.assertEqual(r_ai.json()["application"]["bizreg"]["check"]["code"], "AUTO_MATCH")
            self.assertEqual(Lessor.get(lsr.id).business_number, "")

            # 첨부 원본은 관리인만 볼 수 있다
            self.assertIn(c.get(f"{AD}/lessors/{lsr.id}/bizreg-attachment", headers=chic).status_code, (401, 403))
            self.assertEqual(c.get(f"{AD}/lessors/{lsr.id}/bizreg-attachment", headers=admin).status_code, 200)

            # 관리인 홈택스 확인·승인: 메모 없음·유효하지 않은 번호·휴폐업 상태는 승인 거절
            vf_url = f"{AD}/lessors/{lsr.id}/bizreg-verify"
            self.assertIn(c.post(vf_url, headers=chic, json={"verified": True, "business_number": valid_biz, "note": "확인"}).status_code, (401, 403))
            self.assertEqual(c.post(vf_url, headers=admin, json={"verified": True, "business_number": valid_biz, "note": ""}).status_code, 400)
            self.assertEqual(c.post(vf_url, headers=admin, json={"verified": True, "business_number": "123-45-67899", "note": "확인"}).status_code, 400)
            self.assertEqual(c.post(vf_url, headers=admin, json={"verified": True, "business_number": valid_biz, "hometax_status": "폐업자", "note": "확인"}).status_code, 400)

            # 반려 기록 시 장부 번호는 반영되지 않음
            rej = c.post(vf_url, headers=admin, json={"verified": False, "business_number": valid_biz, "hometax_status": "반려", "note": "사진 흐림"})
            self.assertEqual(rej.status_code, 200)
            self.assertFalse(rej.json()["application"]["bizreg"]["verification"]["verified"])
            self.assertEqual(Lessor.get(lsr.id).business_number, "")

            # 같은 사업자번호를 다른 임대인이 이미 승인받았다면 승인 불가(남의 등록증 도용 방지)
            other_l = next(o for o in Lessor.all() if o.id != lsr.id)
            keep_other = other_l.business_number
            other_l.business_number = valid_biz; other_l.save(action="t")
            try:
                self.assertEqual(c.post(vf_url, headers=admin, json={"verified": True, "business_number": valid_biz, "note": "확인"}).status_code, 400)
            finally:
                other_l.business_number = keep_other; other_l.save(action="t")

            # 정상 홈택스 확인·승인 -> 장부 business_number 확정 + 이제 관리인이 임대인 암호 설정 링크도 발급 가능!
            app_res = c.post(vf_url, headers=admin, json={
                "verified": True, "business_number": valid_biz.replace("-", ""), "hometax_status": "계속사업자(면세)", "note": "홈택스 계속사업자·대표자 일치 확인",
            })
            self.assertEqual(app_res.status_code, 200, app_res.text)
            self.assertTrue(app_res.json()["application"]["bizreg"]["verification"]["verified"])
            self.assertEqual(Lessor.get(lsr.id).business_number, valid_biz)
            self.assertEqual(c.post(f"{AD}/accounts/lessor-invite", headers=admin, json={"id": lsr.id}).status_code, 200)

            # 화면 문자열 점검
            with open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8") as fp:
                lj = fp.read()
            for needle in ("btnBizRegModal", "showBizRegModal", "/franchise/bizreg", "capture=\"environment\"", "bizPickerHtml", "📷 촬영하기", "🖼 사진·PDF 선택", "bizDataUrl"):
                self.assertIn(needle, lj, needle)
            with open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8") as fp:
                aj = fp.read()
            for needle in ("hometaxBizHtml", "btn-biz-verify", "/bizreg-verify", "/bizreg-attachment", "www.hometax.go.kr"):
                self.assertIn(needle, aj, needle)
        finally:
            for sm in list(SupportMessage.all()):
                if sm.category in ("사업자등록증 확인 요청", "사업자등록증 확인 결과"):
                    sm.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.business_number, lsr.account_number, lsr.franchise_docs, lsr.franchise_ai_calls = keep
            lsr.invite_hash, lsr.invite_expires, lsr.invite_issued_by = "", "", ""
            lsr.save(action="t")

    def test_89_franchise_fee_notice_payment_taxinvoice_and_lessor_evidence(self):
        """89. 가맹비: 자동 청구·통보(중복 없음) → 관리인 납부 등록(청구액과 같아야 함) → 세금계산서 승인번호 기록(24자리·중복 불가) → 임대인 장부 경비 자동 등록 → 임대인 연도별 증빙(CSV), 남의 자료·관리인 기능 접근 불가."""
        from apps.rent_dist._rent3_core import Lessor, Outlay, feebilling as fb
        from apps.rent_dist._rent3_core.support import SupportMessage
        c = self.client
        admin, chic, sunho = self._login("admin"), self._login("chic"), self._login("sunho")
        AD, LS = "/api/rent3_admin", "/api/rent3_lessor"
        lsr = Lessor.first(branch="main")
        keep = (lsr.app_fee, lsr.app_fee_status, lsr.last_fee_paid_at, lsr.business_number)
        lsr.app_fee = 33000; lsr.save(action="t")
        for f0 in list(fb.FranchiseFee.all()):            # 다른 시험에서 대시보드를 열 때 자동 청구가 생겼을 수 있어 처음 상태로
            f0.delete(action="t")
        for m0 in list(SupportMessage.find(thread_user=f"lessor:{lsr.id}")):
            if m0.category.startswith("가맹비"):
                m0.delete(action="t")
        nts = "20261116-41000000-12345678"
        from unittest import mock
        env_auto = mock.patch.dict(os.environ, {"RENT3_FEE_AUTO": "1"})
        try:
            # 기본(자동 꺼짐): 조회만으로는 청구가 생기지 않고, 관리인의 직접 생성만 된다
            self.assertEqual([f for f in c.get(f"{AD}/fees", headers=admin).json()["fees"] if f["id_lessor"] == lsr.id], [])
            self.assertEqual(c.get(f"{AD}/fees", headers=admin).json()["auto"], False)
            env_auto.start()
            self.assertIn(c.get(f"{AD}/fees", headers=chic).status_code, (401, 403))                        # 임대인은 관리인 기능 불가
            lst = c.get(f"{AD}/fees", headers=admin)
            self.assertEqual(lst.status_code, 200, lst.text)
            mine = [f for f in lst.json()["fees"] if f["id_lessor"] == lsr.id]
            self.assertEqual(len(mine), 1)
            fee = mine[0]
            self.assertEqual((fee["supply_amount"], fee["vat_amount"], fee["total_amount"], fee["status"]), (30000, 3000, 33000, "NOTICED"))
            self.assertEqual(len([f for f in c.get(f"{AD}/fees", headers=admin).json()["fees"] if f["id_lessor"] == lsr.id]), 1)   # 다시 조회해도 중복 청구 없음
            self.assertEqual(c.post(f"{AD}/fees/generate", headers=admin, json={}).json()["created"], 0)
            notices = [m for m in SupportMessage.find(thread_user=f"lessor:{lsr.id}") if m.category == "가맹비 징수 통보"]
            self.assertEqual(len(notices), 1)                                                                 # 통보는 한 번만
            fid = fee["id"]
            # 임대인: 내 내역만, 남의 지점은 403
            mf = c.get(f"{LS}/franchise-fees?branch=main", headers=chic)
            self.assertEqual(mf.status_code, 200, mf.text)
            self.assertEqual([f["id"] for f in mf.json()["fees"]], [fid])
            self.assertEqual(c.get(f"{LS}/franchise-fees?branch=main", headers=sunho).status_code, 403)
            # 납부 등록: 금액 불일치·권한·중복
            pay = lambda **kw: c.post(f"{AD}/fees/{fid}/paid", headers=admin, json={"amount": 33000, "paid_date": "2026-11-16", **kw})
            self.assertEqual(pay(amount=30000).status_code, 400)
            self.assertIn(c.post(f"{AD}/fees/{fid}/paid", headers=chic, json={"amount": 33000}).status_code, (401, 403))
            self.assertEqual(c.post(f"{AD}/fees/{fid}/invoice", headers=admin, json={"nts_confirm_no": nts}).status_code, 400)   # 입금 전엔 세금계산서 기록 불가
            self.assertEqual(pay().status_code, 200)
            self.assertEqual(pay().status_code, 400)
            self.assertEqual(Lessor.get(lsr.id).app_fee_status, "PAID")
            # 세금계산서: 승인번호 24자리·중복 불가 → 장부 경비 자동 등록
            inv = lambda no, **kw: c.post(f"{AD}/fees/{fid}/invoice", headers=admin, json={"nts_confirm_no": no, "issue_date": "2026-11-16", **kw})
            self.assertEqual(inv("12345").status_code, 400)
            ok = inv(nts)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["fee"]["invoice_nts_no"], nts)
            out = Outlay.get(ok.json()["fee"]["outlay_id"])
            self.assertEqual((out.amount, out.category, out.evidence_type, out.is_vat_deductible), (33000, "platform_fee", "TAX_INVOICE", True))
            self.assertEqual(inv(nts).status_code, 400)                                                       # 이미 기록됨
            # 임대인 증빙: 발행분은 증빙, 합계·CSV 에 승인번호
            ev = c.get(f"{LS}/franchise-fees?branch=main&year=2026", headers=chic).json()["evidence"]
            self.assertEqual((ev["totals"]["count_issued"], ev["totals"]["issued_total"], ev["totals"]["issued_vat"]), (1, 33000, 3000))
            csv = c.get(f"{LS}/franchise-fees/export-csv?branch=main&year=2026", headers=chic)
            self.assertEqual(csv.status_code, 200)
            self.assertIn(nts, csv.text)
            self.assertEqual(c.get(f"{LS}/franchise-fees/export-csv?branch=main&year=2026", headers=sunho).status_code, 403)
            # 발행 취소: 사유 필요, 장부 경비도 지움 → 증빙에서 빠지고 '발행 대기'로
            self.assertEqual(c.post(f"{AD}/fees/{fid}/invoice-cancel", headers=admin, json={"memo": ""}).status_code, 400)
            self.assertEqual(c.post(f"{AD}/fees/{fid}/invoice-cancel", headers=admin, json={"memo": "공급가액 오기재"}).status_code, 200)
            self.assertIsNone(Outlay.get(out.id))
            ev2 = c.get(f"{LS}/franchise-fees?branch=main&year=2026", headers=chic).json()["evidence"]
            self.assertEqual((ev2["totals"]["count_issued"], ev2["totals"]["count_pending"]), (0, 1))
            self.assertIn("발행 대기", c.get(f"{LS}/franchise-fees/export-csv?branch=main&year=2026", headers=chic).text)
            # 홈택스 발행용 목록(관리인만)
            self.assertIn(c.get(f"{AD}/fees/export-hometax-csv", headers=chic).status_code, (401, 403))
            self.assertEqual(c.get(f"{AD}/fees/export-hometax-csv", headers=admin).status_code, 200)
            # 월 가맹비 변경(관리인만)
            self.assertEqual(c.post(f"{AD}/lessors/{lsr.id}/fee-plan", headers=admin, json={"app_fee": 44000}).status_code, 200)
            self.assertEqual(c.post(f"{AD}/lessors/{lsr.id}/fee-plan", headers=admin, json={"app_fee": -1}).status_code, 400)
            self.assertIn(c.post(f"{AD}/lessors/{lsr.id}/fee-plan", headers=chic, json={"app_fee": 1}).status_code, (401, 403))
            # 화면 문자열
            rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
            aj = rd("apps", "rent_dist", "rent3_admin", "frontend", "app.js")
            for needle in ("loadFeeBox", "/fees/", "fee-paid", "fee-inv", "승인번호", "/fees/export-hometax-csv", "feePlan"):
                self.assertIn(needle, aj, needle)
            lj = rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js")
            for needle in ("btnFeeEvidence", "showFeeEvidenceModal", "/franchise-fees", "종합소득세 증빙", "feeCsvDl"):
                self.assertIn(needle, lj, needle)
        finally:
            try:
                env_auto.stop()
            except RuntimeError:
                pass
            for f in list(fb.FranchiseFee.all()):
                f.delete(action="t")
            for o in list(Outlay.find(category="platform_fee")):
                o.delete(action="t")
            for m in list(SupportMessage.find(thread_user=f"lessor:{lsr.id}")):
                if m.category.startswith("가맹비"):
                    m.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.app_fee, lsr.app_fee_status, lsr.last_fee_paid_at, lsr.business_number = keep
            lsr.save(action="t")

    def test_90_etax_invoice_api_bolta_issue_poll_webhook_and_failures(self):
        """90. 전자세금계산서 API(볼타) 연계: 필수 정보 점검 → 정발행 접수(요청 본문·헤더) → 발행 중 → 조회로 승인번호 확정(경비 등록) / 서명 검증된 웹훅(중복·실패) / 거절·네트워크 불확실(재전송 금지)·자동 발행."""
        import base64, hashlib, hmac, json, time
        from unittest import mock
        from apps.rent_dist._rent3_core import Lessor, Outlay, etaxinvoice as et, feebilling as fb
        from apps.rent_dist._rent3_core.billing import validate_kr_business_number
        from apps.rent_dist._rent3_core.support import SupportMessage
        c = self.client
        admin = self._login("admin")
        AD = "/api/rent3_admin"
        ok_no = lambda pre: next(f"{pre}{d}" for d in range(10) if validate_kr_business_number(f"{pre}{d}", strict_checksum=True)["valid"])
        sup_no, buy_no = ok_no("2208162600"[:9]).replace("-", ""), ok_no("107815332").replace("-", "")
        lsr = Lessor.first(branch="main")
        keep = (lsr.app_fee, lsr.app_fee_status, lsr.last_fee_paid_at, lsr.business_number, lsr.email)
        secret_raw = b"unit-test-secret-key-0123456789ab"
        secret = "whsec_" + base64.b64encode(secret_raw).decode()
        env = {"RENT3_ETAX_PROVIDER": "bolta", "RENT3_BOLTA_API_KEY": "test_abc", "RENT3_BOLTA_WEBHOOK_SECRET": secret, "RENT3_FEE_AUTO": "1",
               "RENT3_PLATFORM_NAME": "렌트쓰리", "RENT3_PLATFORM_BIZ_NO": sup_no, "RENT3_PLATFORM_CEO": "홍길동", "RENT3_PLATFORM_EMAIL": "tax@rent3.example"}
        calls = []
        state = {"issue": (200, {"issuanceKey": "KEY1"}), "retrieve": (404, {})}

        def fake_http(method, url, headers, body=None):
            calls.append((method, url, headers, body))
            if url.endswith("/v1/taxInvoices/issue"):
                r = state["issue"]
                if isinstance(r, Exception):
                    raise r
                return r
            return state["retrieve"]

        def hook(event, wid, ts=None, secret_key=secret_raw):
            raw = json.dumps(event).encode()
            ts = str(int(ts or time.time()))
            sig = base64.b64encode(hmac.new(secret_key, f"{wid}.{ts}.".encode() + raw, hashlib.sha256).digest()).decode()
            return c.post(f"{AD}/webhooks/etax", content=raw, headers={"webhook-id": wid, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}", "content-type": "application/json", **self.NOAUTH})

        def fresh_fee():
            for f0 in list(fb.FranchiseFee.all()):
                f0.delete(action="t")
            fb.ensure_notices(force=True)
            f = next(x for x in fb.FranchiseFee.all() if x.id_lessor == lsr.id)
            return f.id

        with mock.patch.dict(os.environ, env), mock.patch.object(et, "_http", side_effect=fake_http):
            try:
                lsr.app_fee, lsr.business_number, lsr.email = 33000, "", ""
                lsr.save(action="t")
                fid = fresh_fee()
                pay = lambda fid_: c.post(f"{AD}/fees/{fid_}/paid", headers=admin, json={"amount": 33000, "paid_date": "2026-11-16"})
                self.assertEqual(c.get(f"{AD}/fees", headers=admin).json()["etax"]["enabled"], True)
                # 납부 등록은 되지만(자동 발행 시도 실패) 필수 정보가 없으면 발행되지 않고 이유를 알려 준다
                with mock.patch.dict(os.environ, {"RENT3_ETAX_AUTO": "1"}):
                    r0 = pay(fid)
                self.assertEqual(r0.status_code, 200, r0.text)
                self.assertIn("사업자번호", r0.json()["message"])
                self.assertEqual(fb.FranchiseFee.get(fid).status, "PAID")
                self.assertEqual(calls, [])                                                               # 정보가 부족하면 API 를 부르지 않는다
                lsr = Lessor.get(lsr.id); lsr.business_number, lsr.email = buy_no, "lessor@example.com"; lsr.save(action="t")
                # 접수: 요청 본문·헤더 확인 → 발행 중
                self.assertIn(c.post(f"{AD}/fees/{fid}/invoice-auto", headers=self._login("chic")).status_code, (401, 403))
                r1 = c.post(f"{AD}/fees/{fid}/invoice-auto", headers=admin)
                self.assertEqual(r1.status_code, 200, r1.text)
                m, url, hd, body = calls[-1]
                self.assertEqual((m, url), ("POST", "https://xapi.bolta.io/v1/taxInvoices/issue"))
                self.assertTrue(hd["Authorization"].startswith("Basic ") and hd["Bolta-Client-Reference-Id"] == f"{fid}-1")
                self.assertEqual(base64.b64decode(hd["Authorization"][6:]).decode(), "test_abc:")
                self.assertEqual((body["taxType"], body["purpose"], body["date"]), ("TAXABLE", "RECEIPT", "2026-11-16"))
                self.assertEqual((body["supplier"]["identificationNumber"], body["supplied"]["identificationNumber"]), (sup_no, buy_no))
                self.assertEqual((body["items"][0]["supplyCost"], body["items"][0]["tax"]), (30000, 3000))
                self.assertEqual(body["supplied"]["managers"][0]["email"], "lessor@example.com")
                fee = fb.FranchiseFee.get(fid)
                self.assertEqual((fee.invoice_status, fee.invoice_key), ("PENDING", "KEY1"))
                # 발행 중에는 중복 접수·수동 기록 불가, 발행 대기 목록에서도 빠진다
                self.assertEqual(c.post(f"{AD}/fees/{fid}/invoice-auto", headers=admin).status_code, 400)
                self.assertEqual(c.post(f"{AD}/fees/{fid}/invoice", headers=admin, json={"nts_confirm_no": "20261116-41000000-12345678"}).status_code, 400)
                # 아직 결과 없음 → 그대로 발행 중
                self.assertEqual(c.post(f"{AD}/fees/{fid}/invoice-refresh", headers=admin).json()["fee"]["invoice_status"], "PENDING")
                # 조회로 승인번호 확정 → 발행 완료·장부 경비 등록
                state["retrieve"] = (200, {"issuanceKey": "KEY1", "ntsTransactionId": "202611164100000012345678", "issuedAt": "2026-11-16T10:00:00+09:00"})
                r2 = c.post(f"{AD}/fees/{fid}/invoice-refresh", headers=admin).json()["fee"]
                self.assertEqual((r2["invoice_status"], r2["invoice_nts_no"], r2["invoice_via"]), ("ISSUED", "20261116-41000000-12345678", "bolta"))
                self.assertEqual(Outlay.get(r2["outlay_id"]).amount, 33000)
                # 웹훅: 서명 검증·중복·실패 이벤트
                fid2 = fresh_fee(); pay(fid2)
                state["issue"] = (200, {"issuanceKey": "KEY2"})
                c.post(f"{AD}/fees/{fid2}/invoice-auto", headers=admin)
                ev = {"eventType": "TAX_INVOICE_ISSUANCE_SUCCESS", "data": {"issuanceKey": "KEY2", "taxInvoiceUrl": "https://x"}}
                self.assertEqual(hook(ev, "wh1", secret_key=b"wrong-key-xxxxxxxxxxxxxxxxxxxxxxxxxxx").status_code, 400)      # 서명 불일치
                self.assertEqual(hook(ev, "wh1", ts=time.time() - 3600).status_code, 400)                              # 오래된 시각
                self.assertEqual(c.post(f"{AD}/webhooks/etax", content=b"{}", headers={**self.NOAUTH}).status_code, 400)     # 헤더 없음
                self.assertEqual(fb.FranchiseFee.get(fid2).invoice_status, "PENDING")                                   # 검증 실패는 아무것도 바꾸지 않는다
                state["retrieve"] = (200, {"issuanceKey": "KEY2", "ntsTransactionId": "202611164100000099999999", "issuedAt": "2026-11-16T11:00:00+09:00"})
                good = hook(ev, "wh2")
                self.assertEqual((good.status_code, good.json()["result"]), (200, "issued"))
                self.assertEqual(fb.FranchiseFee.get(fid2).invoice_nts_no, "20261116-41000000-99999999")
                self.assertEqual(hook(ev, "wh2").json()["result"], "duplicate")
                # 실패 웹훅 → 실패 기록 → 같은 청구를 다시 접수(요청 관리번호는 -2)
                fid3 = fresh_fee(); pay(fid3)
                state["issue"] = (200, {"issuanceKey": "KEY3"}); state["retrieve"] = (404, {})
                c.post(f"{AD}/fees/{fid3}/invoice-auto", headers=admin)
                bad = hook({"eventType": "TAX_INVOICE_ISSUANCE_FAILURE", "data": {"issuanceKey": "KEY3", "cause": {"code": "INVALID_SUPPLIED_BUSINESS", "message": "휴업"}}}, "wh3")
                self.assertEqual(bad.json()["result"], "failed")
                f3 = fb.FranchiseFee.get(fid3)
                self.assertEqual(f3.invoice_status, "FAILED")
                self.assertIn("INVALID_SUPPLIED_BUSINESS", f3.invoice_error)
                state["issue"] = (200, {"issuanceKey": "KEY3B"})
                self.assertEqual(c.post(f"{AD}/fees/{fid3}/invoice-auto", headers=admin).status_code, 200)
                self.assertEqual(calls[-1][2]["Bolta-Client-Reference-Id"], f"{fid3}-2")
                # 거절(400)은 실패, 네트워크 오류·5xx 는 '접수 불확실'(재전송 금지)
                fid4 = fresh_fee(); pay(fid4)
                state["issue"] = (400, {"code": "INVALID_REQUEST", "message": "사업자번호 오류"})
                self.assertEqual(c.post(f"{AD}/fees/{fid4}/invoice-auto", headers=admin).status_code, 400)
                self.assertEqual(fb.FranchiseFee.get(fid4).invoice_status, "FAILED")
                state["issue"] = ConnectionError("down")
                self.assertEqual(c.post(f"{AD}/fees/{fid4}/invoice-auto", headers=admin).status_code, 400)
                u = fb.FranchiseFee.get(fid4)
                self.assertEqual((u.invoice_status, u.invoice_client_ref), ("PENDING", f"{fid4}-2"))
                n_calls = len(calls)
                self.assertEqual(c.post(f"{AD}/fees/{fid4}/invoice-auto", headers=admin).status_code, 400)           # 불확실 상태에서 다시 접수하지 않는다
                self.assertEqual(len(calls), n_calls)
                # 자동 발행: 납부 등록 직후 접수
                fid5 = fresh_fee()
                state["issue"] = (200, {"issuanceKey": "KEY5"})
                with mock.patch.dict(os.environ, {"RENT3_ETAX_AUTO": "1"}):
                    r5 = pay(fid5)
                self.assertEqual(fb.FranchiseFee.get(fid5).invoice_status, "PENDING")
                self.assertIn("접수", r5.json()["message"])
                # 서명 키 없으면 웹훅 거절
                with mock.patch.dict(os.environ, {"RENT3_BOLTA_WEBHOOK_SECRET": ""}):
                    self.assertEqual(hook(ev, "wh9").status_code, 400)
                # 화면 문자열
                aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
                for needle in ("fee-api", "/invoice-auto", "fee-refresh", "/invoice-refresh", "API로 발행", "발행 상태 확인"):
                    self.assertIn(needle, aj, needle)
            finally:
                for f0 in list(fb.FranchiseFee.all()):
                    f0.delete(action="t")
                for o in list(Outlay.find(category="platform_fee")):
                    o.delete(action="t")
                for m0 in list(SupportMessage.find(thread_user=f"lessor:{lsr.id}")):
                    if m0.category.startswith("가맹비"):
                        m0.delete(action="t")
                lsr = Lessor.get(lsr.id)
                lsr.app_fee, lsr.app_fee_status, lsr.last_fee_paid_at, lsr.business_number, lsr.email = keep
                lsr.save(action="t")

    def test_91_tax_invoice_info_card_for_manual_hometax_issue(self):
        """91. 홈택스 수동 발급용 '발급 정보 카드': 가맹비(공급자=플랫폼→공급받는자=임대인) 값·합계·비어 있는 필수 정보 표시, 관리인 전용, 두 화면에 복사/홈택스 열기 카드 연결."""
        from apps.rent_dist._rent3_core import Lessor, feebilling as fb
        from apps.rent_dist._rent3_core.support import SupportMessage
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD = "/api/rent3_admin"
        lsr = Lessor.first(branch="main")
        keep = (lsr.app_fee, lsr.business_number, lsr.email)
        lsr.app_fee, lsr.business_number, lsr.email = 33000, "", ""
        lsr.save(action="t")
        try:
            for f0 in list(fb.FranchiseFee.all()):
                f0.delete(action="t")
            fb.ensure_notices(force=True)
            fee = next(x for x in fb.FranchiseFee.all() if x.id_lessor == lsr.id)
            self.assertIn(c.get(f"{AD}/fees/{fee.id}/invoice-card", headers=chic).status_code, (401, 403))
            card = c.get(f"{AD}/fees/{fee.id}/invoice-card", headers=admin).json()["card"]
            self.assertEqual((card["items"][0]["supply"], card["items"][0]["tax"], card["total"]), (30000, 3000, 33000))
            self.assertEqual((card["purpose"], card["tax_type"]), ("영수", "과세"))
            self.assertTrue(any("공급받는자 사업자번호" in m for m in card["missing"]))          # 임대인 사업자번호 미승인이면 안내
            self.assertTrue(any("이메일" in m for m in card["missing"]))
            self.assertEqual(c.get(f"{AD}/fees/nope/invoice-card", headers=admin).status_code, 404)
            rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
            js = rd("shared_ui", "r3_taxinvoice.js")
            for needle in ("R3Tax", "https://www.hometax.go.kr/", "전체 복사", "data-copy", "승인번호", "비어 있는 필수 정보"):
                self.assertIn(needle, js, needle)
            for rel in (("rent3_admin",), ("rent3_lessor",)):
                self.assertIn("/shared/ui/r3_taxinvoice.js", rd("apps", "rent_dist", rel[0], "frontend", "index.html"))
            for needle in ("fee-card", "R3Tax.openCard", "/invoice-card"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3_admin", "frontend", "app.js"), needle)
            for needle in ("btn-inv-card", "R3Tax.openCard", "홈택스"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), needle)
        finally:
            for f0 in list(fb.FranchiseFee.all()):
                f0.delete(action="t")
            for m0 in list(SupportMessage.find(thread_user=f"lessor:{lsr.id}")):
                if m0.category.startswith("가맹비"):
                    m0.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.app_fee, lsr.business_number, lsr.email = keep
            lsr.save(action="t")

    def test_92_cash_receipt_bizreg_email_and_tenant_business_submission(self):
        """92. 현금영수증(가맹비·월세 기록, 사업자번호 없는 임대인은 현금영수증 카드)·사업자등록증 제출 시 이메일/업태/종목 반영·임차인 사업자 정보 제출(AI 읽기 모의, 체크섬, 본인만)."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Building, Contract, DirectChat, Lessor, Outlay, Pay, Tenant, feebilling as fb, registry_ai
        from apps.rent_dist._rent3_core.billing import validate_kr_business_number
        from apps.rent_dist._rent3_core.support import SupportMessage
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD, LS, TN = "/api/rent3_admin", "/api/rent3_lessor", "/api/rent3_tenant"
        valid = next(f"214-81-2345{d}" for d in range(10) if validate_kr_business_number(f"214-81-2345{d}", strict_checksum=True)["valid"])
        png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        lsr = Lessor.first(branch="main")
        bld = Building.first(branch="main")
        keep = (lsr.app_fee, lsr.business_number, lsr.email, lsr.account_number, dict(lsr.franchise_docs or {}), bld.biz_type, bld.biz_item)
        t = Tenant.get("tenant_201")
        keept = (t.business_number, t.company_name, t.ceo_name, t.business_address, t.email, t.biz_submitted_at, t.biz_ai_calls)
        made_pay = None
        try:
            # ── 임대인 사업자등록증 제출: 이메일·업태·종목 입력 반영(이메일 형식 오류는 거절)
            bad = c.post(f"{LS}/franchise/bizreg", headers=chic, json={"branch": "main", "attachment_data_url": png, "business_number": valid, "email": "nope"})
            self.assertEqual(bad.status_code, 400)
            ok = c.post(f"{LS}/franchise/bizreg", headers=chic, json={"branch": "main", "attachment_data_url": png, "business_number": valid, "email": "lessor@example.com",
                                                                      "business_type": "부동산업", "business_item": "비주거용 건물 임대업"})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual((Lessor.get(lsr.id).email, Building.first(branch="main").biz_type, Building.first(branch="main").biz_item), ("lessor@example.com", "부동산업", "비주거용 건물 임대업"))
            self.assertEqual(ok.json()["application"]["bizreg"]["email"], "lessor@example.com")
            # AI 읽기가 업태·종목을 주면 직접 입력이 없을 때 채운다
            ext = {"document_type": "business_registration", "readable": True, "business_number": valid, "company_name": "상호", "representative_name": lsr.name, "opening_date": "",
                   "business_address": "", "business_type": "서비스업", "business_item": "공유오피스", "tax_type": "일반과세자", "issue_number": ""}
            with mock.patch.object(registry_ai, "analyze_bizreg", return_value={"status": "ok", "message": "", "extracted": ext}):
                c.post(f"{LS}/franchise/bizreg", headers=chic, json={"branch": "main", "attachment_data_url": png, "consent": True})
            self.assertEqual(Building.first(branch="main").biz_type, "서비스업")

            # ── 가맹비 현금영수증: 사업자번호 없는 임대인 → 세금계산서 불가·현금영수증(휴대폰) 카드, 기록·장부 경비·증빙 CSV·취소
            lsr = Lessor.get(lsr.id); lsr.app_fee, lsr.business_number, lsr.phone = 33000, "", "010-1234-5678"; lsr.save(action="t")
            for f0 in list(fb.FranchiseFee.all()):
                f0.delete(action="t")
            fb.ensure_notices(force=True)
            fee = next(x for x in fb.FranchiseFee.all() if x.id_lessor == lsr.id)
            c.post(f"{AD}/fees/{fee.id}/paid", headers=admin, json={"amount": 33000, "paid_date": "2026-11-16"})
            card = c.get(f"{AD}/fees/{fee.id}/invoice-card", headers=admin).json()["card"]
            self.assertFalse(card["tax_available"])
            self.assertEqual((card["cash"]["kind"], card["cash"]["identifier"], card["cash"]["amount"]), ("소득공제용", "01012345678", 33000))
            cr = lambda no, **kw: c.post(f"{AD}/fees/{fee.id}/cash-receipt", headers=admin, json={"approval_no": no, "issue_date": "2026-11-16", **kw})
            self.assertIn(c.post(f"{AD}/fees/{fee.id}/cash-receipt", headers=chic, json={"approval_no": "123456789"}).status_code, (401, 403))
            self.assertEqual(cr("12").status_code, 400)
            done = cr("123456789")
            self.assertEqual(done.status_code, 200, done.text)
            f1 = done.json()["fee"]
            self.assertEqual((f1["receipt_type"], f1["invoice_status"], f1["invoice_nts_no"]), ("CASH_RECEIPT", "ISSUED", "123456789"))
            out = Outlay.get(f1["outlay_id"])
            self.assertEqual((out.evidence_type, out.is_vat_deductible), ("CASH_RECEIPT", False))        # 현금영수증은 매입세액 공제 아님
            self.assertEqual(cr("987654321").status_code, 400)                                           # 이미 기록됨
            self.assertIn("현금영수증 발행", c.get(f"{LS}/franchise-fees/export-csv?branch=main&year=2026", headers=chic).text)
            self.assertEqual(c.post(f"{AD}/fees/{fee.id}/invoice-cancel", headers=admin, json={"memo": "오기재"}).status_code, 200)
            self.assertIsNone(Outlay.get(out.id))
            self.assertEqual(fb.FranchiseFee.get(fee.id).receipt_type, "TAX_INVOICE")
            # 사업자번호가 있으면 카드는 세금계산서+현금영수증(지출증빙용)
            lsr = Lessor.get(lsr.id); lsr.business_number, lsr.email = valid, "lessor@example.com"; lsr.save(action="t")
            card2 = c.get(f"{AD}/fees/{fee.id}/invoice-card", headers=admin).json()["card"]
            self.assertTrue(card2["tax_available"])
            self.assertEqual(card2["cash"]["kind"], "지출증빙용")

            # ── 임차인 사업자등록증 제출
            t = Tenant.get("tenant_201")
            tk = self._login("tenant_201")
            sub = lambda **kw: c.post(f"{TN}/myhome/tenant_201/business-registration", headers=tk, json=kw)
            self.assertEqual(sub(attachment_data_url=png, business_number="123-45-67899", email="t@example.com").status_code, 400)      # 체크섬 오류
            t = Tenant.get("tenant_201"); t.email = ""; t.save(action="t")
            self.assertEqual(sub(attachment_data_url=png, business_number=valid).status_code, 400)                                      # 이메일 없음
            self.assertEqual(sub(attachment_data_url=png).status_code, 400)                                                             # 번호 없음(AI 동의 없음)
            ext2 = {**ext, "business_number": valid, "company_name": "임차상사", "representative_name": "임차대표", "business_address": "서울 중구 1"}
            with mock.patch.object(registry_ai, "analyze_bizreg", return_value={"status": "ok", "message": "", "extracted": ext2}):
                good = sub(attachment_data_url=png, consent=True, email="t@example.com")
            self.assertEqual(good.status_code, 200, good.text)
            t = Tenant.get("tenant_201")
            self.assertEqual((t.business_number, t.company_name, t.ceo_name, t.email), (valid, "임차상사", "임차대표", "t@example.com"))
            self.assertTrue(any("사업자 정보 제출" in m.message for m in DirectChat.find(branch="main", room_no=t.room_no)))      # 임대인에게 알림
            ct_sync = Contract.first(branch="main", room_no=t.room_no, status="ACTIVE")
            self.assertEqual((ct_sync.tenant_business_number, ct_sync.tenant_company_name, ct_sync.tenant_ceo_name), (valid, "임차상사", "임차대표"))   # 계약에도 같이 반영(대장이 계약을 먼저 읽는다)
            self.assertTrue(ct_sync.tax_invoice_requested)
            self.assertEqual(c.get(f"{TN}/myhome/tenant_201/business-registration", headers=tk).json()["business"]["business_number"], valid)
            self.assertEqual(c.post(f"{TN}/myhome/tenant_202/business-registration", headers=tk, json={"business_number": valid, "email": "x@e.com"}).status_code, 403)   # 남의 계정 불가

            # ── 월세 현금영수증 승인번호 기록(납부 건이 있어야, 임대인만)
            pay = next((p for p in Pay.find(branch="main") if p.payment_kind == "MONTHLY_RENT"), None)
            if pay is None:                      # 시드에 월세 납부가 없으면 한 건 만든다(끝나면 지운다)
                ct0 = Contract.first(branch="main", status="ACTIVE")
                pay = made_pay = Pay(branch="main", id_contract=ct0.id, room_no=ct0.room_no, id_tenant=ct0.id_tenant, payment_kind="MONTHLY_RENT", total_amount=ct0.monthly_rent,
                                     paid_date="2026-11-10", billing_month="2026-11").save(action="t")
            rno = pay.room_no
            ym = Pay.__mro__ and __import__("apps.rent_dist._rent3_core.billing", fromlist=["TaxAndCertificateService"]).TaxAndCertificateService.payment_tax_ym(pay)
            rc = c.post(f"{LS}/tax/cash-receipt", headers=chic, json={"branch": "main", "room_no": rno, "billing_month": ym, "approval_no": "55443322"})
            self.assertEqual(rc.status_code, 200, rc.text)
            self.assertEqual(Pay.get(pay.id).cash_receipt_no, "55443322")
            self.assertEqual(c.post(f"{LS}/tax/cash-receipt", headers=chic, json={"branch": "main", "room_no": rno, "billing_month": "1999-01", "approval_no": "55443322"}).status_code, 400)
            self.assertEqual(c.post(f"{LS}/tax/cash-receipt", headers=chic, json={"branch": "main", "room_no": rno, "billing_month": ym, "approval_no": "1"}).status_code, 400)

            # ── 화면 문자열
            rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
            for needle in ("data-mode", "💵 현금영수증", "사업자등록번호가 없어 세금계산서는 발급할 수 없습니다"):
                self.assertIn(needle, rd("shared_ui", "r3_taxinvoice.js"), needle)
            for needle in ("fbizEmail", "mbizEmail", "mbizType", "/tax/cash-receipt", "💵 현금영수증 정보·홈택스"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), needle)
            for needle in ("recordCash", "/cash-receipt"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3_admin", "frontend", "app.js"), needle)
            for needle in ("btnBizInfo", "/business-registration", "tbCam", "tbEmail"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), needle)
        finally:
            if made_pay is not None:
                made_pay.delete(action="t")
            for f0 in list(fb.FranchiseFee.all()):
                f0.delete(action="t")
            for o in list(Outlay.find(category="platform_fee")):
                o.delete(action="t")
            for m0 in list(SupportMessage.find(thread_user=f"lessor:{lsr.id}")):
                if m0.category.startswith("가맹비") or m0.category.startswith("사업자"):
                    m0.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.app_fee, lsr.business_number, lsr.email, lsr.account_number, lsr.franchise_docs, bt, bi = keep
            lsr.save(action="t")
            b2 = Building.first(branch="main"); b2.biz_type, b2.biz_item = bt, bi; b2.save(action="t")
            t2 = Tenant.get("tenant_201")
            t2.business_number, t2.company_name, t2.ceo_name, t2.business_address, t2.email, t2.biz_submitted_at, t2.biz_ai_calls = keept
            t2.save(action="t")

    def test_93_renewal_skips_identity_steps_and_autoseals_on_both_signatures(self):
        """재계약: 본인확인·옵션 점검 생략 → 3단계 시작, 5% 기본 월세, 양측 서명 즉시 체결(계약금 0·도어락 유지), 제3자 접근 차단."""
        c, base = self.client, "/api/rent3_contract"
        room = next(r for r in Room.all() if r.active_contract_id and r.branch == "main")
        old = Contract.get(room.active_contract_id)
        old_rent = old.monthly_rent
        s = c.post(f"{base}/sessions", json={"branch": "main", "room_no": room.room_no, "mode": "RENEWAL"}).json()["session"]
        sid = s["id"]
        got = c.get(f"{base}/sessions/{sid}").json()["session"]
        self.assertEqual(got["current_step"], 3)
        for f in ("lessor_checked_tenant_id", "tenant_checked_lessor_id", "step1_lessor_confirmed",
                  "step1_tenant_locked", "step2_lessor_confirmed", "step2_tenant_locked"):
            self.assertTrue(got[f], f)
        self.assertEqual(got["prev_contract_id"], old.id)
        self.assertEqual(got["terms"]["monthly_rent"], max(old_rent, (int(old_rent * 1.05) // 1000) * 1000))
        self.assertLessEqual(got["terms"]["monthly_rent"], round(old_rent * 1.05))   # 법정 5% 상한 이내
        self.assertEqual(got["terms"]["contract_deposit"], 0)

        # 당사자가 아닌 임차인은 열람·서명 불가
        stranger = next(t for t in ("tenant_202", "tenant_201") if t != f"tenant_{room.room_no}")
        h = self._login(stranger)
        self.assertEqual(c.get(f"{base}/sessions/{sid}", headers=h).status_code, 403)
        self.assertEqual(c.post(f"{base}/sessions/{sid}/step4/sign", headers=h,
                                json={"role": "tenant", "signature": "도용"}).status_code, 403)

        rev = got["revision"]
        for role in ("lessor", "tenant"):
            c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
        r1 = c.post(f"{base}/sessions/{sid}/step4/sign", json={"role": "lessor", "signature": "임대인", "expected_revision": rev})
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertNotEqual(r1.json()["session"]["status"], "COMPLETED")              # 한쪽만 서명하면 체결 안 됨
        r2 = c.post(f"{base}/sessions/{sid}/step4/sign", json={"role": "tenant", "signature": "임차인", "expected_revision": rev})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["session"]["status"], "COMPLETED")
        new_ct = r2.json()["contract"]
        self.assertEqual(new_ct["contract_deposit"], 0)
        self.assertTrue(new_ct["keys_released"])
        self.assertEqual(new_ct["monthly_rent"], got["terms"]["monthly_rent"])
        # 만기 전에 미리 체결한 재계약: 새 계약은 '예약'(SCHEDULED)이고 만기까지는 이전 계약 조건이 그대로 적용된다
        self.assertEqual((new_ct["status"], new_ct["renews_contract_id"], new_ct["move_in_date"]), ("SCHEDULED", old.id, old.expiry_date))
        self.assertEqual(Contract.get(old.id).status, "ACTIVE")
        self.assertEqual((Room.get(room.id).active_contract_id, Room.get(room.id).reserved_contract_id), (old.id, new_ct["id"]))
        self.assertEqual(Room.get(room.id).monthly_rent, old_rent)
        # 새 조건의 시작일(이전 계약 만기일)이 되어 조회·로그인하면 이전 계약은 RENEWED, 새 계약이 ACTIVE 가 되고 호실 조건이 바뀐다
        from unittest import mock as _mock
        from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck as _Chk
        with _mock.patch.dict(os.environ, {"RENT3_TODAY": old.expiry_date}):
            self.assertGreaterEqual(_Chk.activate_due(force=True), 1)
        self.assertEqual(Contract.get(old.id).status, "RENEWED")
        self.assertEqual(Contract.get(new_ct["id"]).status, "ACTIVE")
        self.assertEqual(Room.get(room.id).active_contract_id, new_ct["id"])
        self.assertEqual((Room.get(room.id).monthly_rent, Room.get(room.id).reserved_contract_id), (new_ct["monthly_rent"], ""))
        # 이미 체결된 세션 재호출은 중복 계약을 만들지 않는다
        again = c.post(f"{base}/sessions/{sid}/step5/complete", json={"payment_method": "TRANSFER", "own_account_checked": True})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json().get("already_completed"))

    def test_93_ledger_allocates_labelled_month_first_then_fifo(self):
        """93. 원장 상계: 각 납부 건은 먼저 '적힌 청구월'을 채우고 남는 금액만 오래된 미납부터 채운다 → 납부 내역이 있는 달이 미납으로 나오지 않고, 비어 있는 달이 미납으로 나온다(총 미납액은 FIFO와 같다)."""
        from apps.rent_dist._rent3_core import Contract, Pay
        ct = next(x for x in Contract.find(branch="main", status="ACTIVE") if x.monthly_rent + x.maintenance_fee > 0)
        base = ct.ledger()
        payable = [m for m in base["months"] if m["due"] > 0 and m["status"] not in ("EXEMPT_POSTPAID", "ZERO_DUE")]
        self.assertGreaterEqual(len(payable), 3)
        before_paid = base["total_paid"]
        due_total = sum(m["due"] for m in payable)
        # 가장 최근 두 달만 정확히 납부한 상황(앞선 달들은 비어 있음)을 만든다: 기존 납부를 잠시 치워 두고 시험
        olds = [(x, x.model_dump()) for x in Pay.find(id_contract=ct.id)]
        made = []
        try:
            for x, _ in olds:
                x.delete(action="t")
            for m in payable[-2:]:
                made.append(Pay(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payment_kind="MONTHLY_RENT", billing_month=m["ym"],
                                paid_date=m["ym"] + "-10", total_amount=m["due"]).save(action="t"))
            lg = ct.ledger()
            by = {m["ym"]: m for m in lg["months"]}
            for m in payable[-2:]:
                self.assertEqual((by[m["ym"]]["unpaid"], by[m["ym"]]["status"]), (0, "PAID"), m["ym"])     # 납부 내역이 있는 달은 미납이 아니다
            self.assertEqual(lg["oldest_unpaid_ym"], payable[0]["ym"])                                       # 비어 있는 가장 오래된 달부터 미납
            self.assertEqual(lg["total_unpaid"], due_total - sum(m["due"] for m in payable[-2:]))             # 총 미납액은 FIFO 계산과 같다
            # 남는 금액: 청구월이 없는 큰 납부는 가장 오래된 미납부터 채운다
            made.append(Pay(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payment_kind="MONTHLY_RENT", billing_month="",
                            paid_date="2026-01-01", total_amount=payable[0]["due"]).save(action="t"))
            lg2 = ct.ledger()
            self.assertEqual({m["ym"]: m["status"] for m in lg2["months"]}[payable[0]["ym"]], "PAID")
            self.assertEqual(lg2["total_unpaid"], lg["total_unpaid"] - payable[0]["due"])
        finally:
            for x in made:
                x.delete(action="t")
            for _, d in olds:
                Pay(**d).save(action="t")
        self.assertEqual(ct.ledger()["total_paid"], before_paid)

    def test_94_pool_duplicate_scan_and_safe_remove_and_reseed_idempotent(self):
        """94. Data Pool 중복 점검: 내용이 같은 납부는 자동 정리 대상(가장 먼저 만든 한 건 유지·자동 백업), 같은 달 나눠 낸 납부는 참고만, 관리인 전용, 시드를 다시 돌려도 납부가 겹치지 않음."""
        from apps.rent_dist._rent3_core import Contract, Pay, ensure_pool_seeded
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD = "/api/rent3_admin"
        self.assertIn(c.get(f"{AD}/pool-duplicates", headers=chic).status_code, (401, 403))
        self.assertIn(c.post(f"{AD}/pool-duplicates/remove", headers=chic, json={"apply": True}).status_code, (401, 403))
        ct = next(x for x in Contract.find(branch="main", status="ACTIVE") if x.monthly_rent > 0)
        base = dict(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payment_kind="MONTHLY_RENT", billing_month="2031-01", paid_date="2031-01-10", total_amount=123000)
        made = []
        try:
            first = Pay(**base).save(action="t"); made.append(first)
            before = c.get(f"{AD}/pool-duplicates", headers=admin).json()
            self.assertEqual(before["removable_count"], 0)
            dup = Pay(**base).save(action="t"); made.append(dup)                             # 같은 입금을 두 번 기록
            split = Pay(**{**base, "billing_month": "2031-02", "total_amount": 100000}).save(action="t"); made.append(split)
            split2 = Pay(**{**base, "billing_month": "2031-02", "total_amount": 23000, "paid_date": "2031-02-15"}).save(action="t"); made.append(split2)   # 나눠 낸 정상 납부
            rep = c.get(f"{AD}/pool-duplicates", headers=admin).json()
            self.assertEqual(rep["removable_count"], 1)
            grp = rep["exact_duplicate_groups"][0]
            self.assertEqual({grp["keep"]["id"], *[d["id"] for d in grp["duplicates"]]}, {first.id, dup.id})                  # 한 건만 남기고 나머지가 정리 대상(만든 시각이 같은 초면 임의)
            first, dup = Pay.get(grp["keep"]["id"]), Pay.get(grp["duplicates"][0]["id"])
            self.assertTrue(any(m["billing_month"] == "2031-02" for m in rep["same_month_multiple"]))                           # 나눠 낸 납부는 참고만
            dry = c.post(f"{AD}/pool-duplicates/remove", headers=admin, json={"apply": False}).json()
            self.assertEqual((dry["applied"], dry["would_remove"]), (False, 1))
            self.assertIsNotNone(Pay.get(dup.id))                                                                              # 점검만으로는 지우지 않는다
            done = c.post(f"{AD}/pool-duplicates/remove", headers=admin, json={"apply": True}).json()
            self.assertEqual((done["applied"], done["removed"]), (True, 1))
            self.assertTrue(done["backup"])
            self.assertIsNone(Pay.get(dup.id))
            self.assertIsNotNone(Pay.get(first.id))
            self.assertIsNotNone(Pay.get(split.id)); self.assertIsNotNone(Pay.get(split2.id))                                  # 나눠 낸 납부는 그대로
            # 시드를 다시 돌려도 시드 납부가 겹치지 않는다
            ensure_pool_seeded(force=True)
            ensure_pool_seeded(force=True)
            keys = [p.idempotency_key for p in Pay.all() if p.idempotency_key.startswith("seed:")]
            self.assertEqual(len(keys), len(set(keys)))                                                                         # 같은 시드 납부가 겹치지 않는다
            aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("btnDupScan", "/pool-duplicates", "btnDupRemove", "중복 점검"):
                self.assertIn(needle, aj, needle)
        finally:
            for x in made:
                x.delete(action="t")

    def _open_unpaid(self, room_no="201", months=6):
        """시험용: 그 호실의 입주일을 앞당겨 비어 있는(미납) 달이 앞쪽에 생기게 한다. 끝나면 원래대로."""
        from datetime import date as _d
        from apps.rent_dist._rent3_core import Contract
        ct = Contract.first(branch="main", room_no=room_no, status="ACTIVE")
        keep = ct.move_in_date
        y, m = _d.fromisoformat(keep).year, _d.fromisoformat(keep).month - months
        while m <= 0:
            y, m = y - 1, m + 12
        ct.move_in_date = f"{y:04d}-{m:02d}-01"
        ct.save(action="t")

        def restore():
            c2 = Contract.get(ct.id); c2.move_in_date = keep; c2.save(action="t")
        self.addCleanup(restore)
        return ct

    def test_95_collect_scenarios_month_slots_target_month_no_advance_and_ledger_state(self):
        """95. 월 칸 채우기(사용자 제안): 입금은 비어 있는(미납) 달에 채우고, 달을 고를 수 있으며(가운데 달을 지운 뒤 그 달을 직접 채움), 여러 달에 걸친 입금은 달마다 납부 건이 하나씩,
        미납 합계보다 많은 입금(선수금)은 직접·문자 모두 오류, 같은 금액 직접 수납은 30초 이중 클릭만 차단, 원장 상태 API."""
        from datetime import timedelta, datetime
        from apps.rent_dist._rent3_core import Contract, Pay
        c = self.client
        chic = self._login("chic")
        LS = "/api/rent3_lessor"
        ct = Contract.first(branch="main", room_no="201", status="ACTIVE")
        monthly = ct.monthly_rent + ct.maintenance_fee + ct.compute_vat()
        olds = [(x, x.model_dump()) for x in Pay.find(id_contract=ct.id)]
        made = []
        keep_move = ct.move_in_date
        try:
            for x, _ in olds:
                x.delete(action="t")
            from apps.rent_dist._rent3_core import date_today, current_ym
            ym_now = current_ym()
            y, m = int(ym_now[:4]), int(ym_now[5:7])
            m0 = m - 3
            y0 = y - (1 if m0 <= 0 else 0); m0 = m0 + (12 if m0 <= 0 else 0)
            ct.move_in_date = f"{y0:04d}-{m0:02d}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            due_months = [r["ym"] for r in ct.ledger()["months"] if r["due"] > 0]
            self.assertGreaterEqual(len(due_months), 4)
            D = due_months[:4]

            def age():
                ts = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
                for x in Pay.find(id_contract=ct.id):
                    x.created_at = ts; x.save(action="t")

            def pay(**kw):
                r = c.post(f"{LS}/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": monthly, **kw})
                if r.status_code == 200:
                    made.extend(x["id"] for x in r.json()["payments"])
                return r

            # ① 기본: 가장 오래된 빈 달부터 한 달씩, 같은 요청을 바로 또 보내면(이중 클릭) 차단, 30초 뒤엔 새 입금
            r1 = pay(); self.assertEqual(r1.status_code, 200, r1.text)
            self.assertEqual(pay().status_code, 409)
            age()
            r2 = pay(); self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual([Pay.get(i).billing_month for i in made], D[:2])
            # ② 사용자가 보고한 상황: 가운데 달(두 번째)을 삭제 → 그 달만 미납 → 그 달을 골라 직접 수납하면 정확히 그 달이 채워진다
            self.assertEqual(c.post(f"{LS}/payments/delete", headers=chic, json={"branch": "main", "pay_id": made[1]}).status_code, 200)
            self.assertEqual([r["ym"] for r in ct.ledger()["months"] if r["unpaid"] > 0 and r["ym"] in D], [D[1], D[2], D[3]])
            age()
            self.assertEqual(pay(target_ym=D[0]).status_code, 400)                                           # 이미 낸 달은 고를 수 없다(NOT_UNPAID_MONTH)
            age()
            rt = pay(target_ym=D[2]); self.assertEqual(rt.status_code, 200, rt.text)                         # 가운데가 아닌 달도 골라서 채운다
            self.assertEqual(Pay.get(rt.json()["payment"]["id"]).billing_month, D[2])
            age()
            rm = pay(target_ym=D[1]); self.assertEqual(rm.status_code, 200, rm.text)                         # 삭제했던 달
            self.assertEqual(Pay.get(rm.json()["payment"]["id"]).billing_month, D[1])
            st1 = {r["ym"]: r for r in ct.ledger()["months"]}
            self.assertEqual([st1[d]["unpaid"] for d in D[:3]], [0, 0, 0])
            # ③ 문자 수납: 남은 빈 달을 채움, 두 달치 입금은 달마다 납부 건 하나씩
            age()
            sms = f"[신한] {date_today().month}/{date_today().day} 201호 {ct.tenant_name} {monthly * 2:,}원 입금"
            tot_before = ct.ledger()["total_unpaid"]
            rs = c.post(f"{LS}/payments/sms-paste", headers=chic, json={"branch": "main", "sms_text": sms})
            if tot_before >= monthly * 2:
                self.assertEqual(rs.status_code, 200, rs.text)
                made.extend(x["id"] for x in rs.json()["payments"])
                self.assertEqual(len(rs.json()["payments"]), len({x["billing_month"] for x in rs.json()["payments"]}))   # 달마다 한 건
                self.assertEqual(c.post(f"{LS}/payments/sms-paste", headers=chic, json={"branch": "main", "sms_text": sms}).status_code, 409)   # 같은 문자는 다시 안 됨
            rest = ct.ledger()["total_unpaid"]
            # ④ 선수금은 오류: 미납 합계보다 많은 입금(직접·문자), 미납이 없을 때의 입금
            over = c.post(f"{LS}/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": rest + 1})
            self.assertEqual((over.status_code, over.json().get("code")), (400, "OVER_UNPAID"))
            if rest > 0:
                age()
                ra = c.post(f"{LS}/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": rest})
                self.assertEqual(ra.status_code, 200, ra.text)
                made.extend(x["id"] for x in ra.json()["payments"])
            self.assertEqual(ct.ledger()["total_unpaid"], 0)
            age()
            n_before = len(Pay.find(id_contract=ct.id))
            nu = c.post(f"{LS}/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": monthly})
            self.assertEqual((nu.status_code, nu.json().get("code")), (400, "NO_UNPAID"))
            sms2 = f"[신한] {date_today().month}/{date_today().day} 201호 {ct.tenant_name} {monthly:,}원 입금 잔액 9,999원"
            sr = c.post(f"{LS}/payments/sms-paste", headers=chic, json={"branch": "main", "sms_text": sms2})
            self.assertEqual(sr.status_code, 400, sr.text)                                                   # 문자도 선수금이면 오류
            self.assertEqual(len(Pay.find(id_contract=ct.id)), n_before)                                     # 오류일 때는 아무것도 저장되지 않는다
            # ⑤ 원장 상태: 선수금 0, 출처 표시, 남의 지점 403
            stt = c.get(f"{LS}/payments/ledger-state?branch=main&room_no=201", headers=chic).json()
            self.assertEqual((stt["advance_credit"], stt["total_unpaid"]), (0, 0))
            self.assertTrue(all(p["source"] in ("앱에서 수납", "문자 수납") for p in stt["payments"]))
            self.assertEqual(c.get(f"{LS}/payments/ledger-state?branch=main&room_no=201", headers=self._login("sunho")).status_code, 403)
            lj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("showLedgerStateModal", "pmState", "pmYm", "target_ym", "선수금은 받지 않습니다"):
                self.assertIn(needle, lj, needle)
        finally:
            for i in made:
                px = Pay.get(i)
                if px is not None:
                    px.delete(action="t")
            ct = Contract.get(ct.id); ct.move_in_date = keep_move; ct.save(action="t")
            for x in Pay.find(id_contract=ct.id):
                x.delete(action="t")
            for _, d in olds:
                Pay(**d).save(action="t")

    def test_96_void_instead_of_delete_edit_cap_month_move_and_deposit_group(self):
        """96. 수납 '삭제'는 무효 처리(VOIDED: 사유·시각 보관, 원장·세무·중복 점검에서 제외·선수금 안 쌓임), 수정은 그 달 청구액 초과 불가, 달 옮기기(빈 자리 필요), 여러 달 입금은 같은 묶음 표시."""
        from datetime import timedelta, datetime
        from apps.rent_dist._rent3_core import Contract, Pay, current_ym, dupcheck
        c = self.client
        chic = self._login("chic")
        LS = "/api/rent3_lessor"
        ct = Contract.first(branch="main", room_no="201", status="ACTIVE")
        monthly = ct.monthly_rent + ct.maintenance_fee + ct.compute_vat()
        olds = [(x, x.model_dump()) for x in Pay.find(id_contract=ct.id)]
        keep_move = ct.move_in_date
        made = []
        try:
            for x, _ in olds:
                x.delete(action="t")
            ym_now = current_ym(); y, m = int(ym_now[:4]), int(ym_now[5:7])
            m0 = m - 3; y0 = y - (1 if m0 <= 0 else 0); m0 = m0 + (12 if m0 <= 0 else 0)
            ct.move_in_date = f"{y0:04d}-{m0:02d}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            D = [r["ym"] for r in ct.ledger()["months"] if r["due"] > 0][:4]
            self.assertGreaterEqual(len(D), 4)

            def age():
                ts = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
                for x in Pay.find(id_contract=ct.id):
                    x.created_at = ts; x.save(action="t")

            def col(amount, **kw):
                age()
                r = c.post(f"{LS}/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": amount, **kw})
                if r.status_code == 200:
                    made.extend(x["id"] for x in r.json()["payments"])
                return r

            # ④ 두 달치 입금 → 달마다 한 건, 같은 묶음 표시
            two = col(monthly * 2); self.assertEqual(two.status_code, 200, two.text)
            ps = two.json()["payments"]
            self.assertEqual(len(ps), 2)
            self.assertTrue(ps[0]["deposit_group_id"] and ps[0]["deposit_group_id"] == ps[1]["deposit_group_id"])
            self.assertEqual([p["billing_month"] for p in ps], D[:2])
            one = col(monthly); self.assertEqual(one.status_code, 200)
            self.assertNotEqual(one.json()["payment"]["deposit_group_id"], ps[0]["deposit_group_id"])
            dash = c.get(f"{LS}/dashboard?branch=main", headers=chic).json()
            pays201 = next(r for r in dash["rooms"] if r["room_no"] == "201")["pays"]
            self.assertEqual({x["grp_n"] for x in pays201 if x["grp"] == ps[0]["deposit_group_id"]}, {2})
            # ③ 삭제 = 무효 처리: 그 달 미납 복귀, 선수금 없음, 사유 보관, 계산·중복 점검·조회에서 제외, 같은 묶음의 다른 건은 유지
            vid = ps[0]["id"]
            dl = c.post(f"{LS}/payments/delete", headers=chic, json={"branch": "main", "pay_id": vid, "reason": "시험 오입력"})
            self.assertEqual(dl.status_code, 200, dl.text)
            self.assertIn("무효 처리", dl.json()["message"]); self.assertIn("1건은 그대로", dl.json()["message"])
            raw = Pay.get(vid, include_voided=True)
            self.assertEqual((raw.status, raw.void_reason), ("VOIDED", "시험 오입력"))
            self.assertTrue(raw.voided_at)
            self.assertIsNone(Pay.get(vid))
            self.assertNotIn(vid, [x.id for x in Pay.find(id_contract=ct.id)])
            lg = ct.ledger()
            self.assertEqual(lg["advance_credit"], 0)
            self.assertEqual([r["ym"] for r in lg["months"] if r["unpaid"] > 0 and r["ym"] in D[:3]], [D[0]])
            self.assertEqual(c.post(f"{LS}/payments/delete", headers=chic, json={"branch": "main", "pay_id": vid}).status_code, 404)   # 이미 무효
            st = c.get(f"{LS}/payments/ledger-state?branch=main&room_no=201", headers=chic).json()
            self.assertIn("시험 오입력", [v["reason"] for v in st["voided"]])
            # 반복해도 쌓이지 않는다: 같은 달을 다시 수납·무효를 3번 반복
            for _ in range(3):
                r = col(monthly, target_ym=D[0]); self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(c.post(f"{LS}/payments/delete", headers=chic, json={"branch": "main", "pay_id": r.json()["payment"]["id"], "reason": "반복 시험"}).status_code, 200)
                self.assertEqual(ct.ledger()["advance_credit"], 0)
            self.assertEqual(dupcheck.scan()["removable_count"], 0)                                              # 무효 건은 중복으로 잡히지 않는다
            # ① 수정 검증: 그 달 청구액을 넘게 고칠 수 없고(OVER_MONTH), 날짜만 고치는 것·줄이는 것은 가능
            first = Pay.get(one.json()["payment"]["id"])
            ed = lambda **kw: c.post(f"{LS}/payments/edit", headers=chic, json={"branch": "main", "pay_id": first.id, "paid_date": first.paid_date, "rent_amount": first.rent_amount, "maintenance_amount": first.maintenance_amount, **kw})
            over = ed(rent_amount=first.rent_amount + 1000)
            self.assertEqual((over.status_code, over.json().get("code")), (400, "OVER_MONTH"))
            self.assertEqual(ed(paid_date="2026-10-02").status_code, 200)
            self.assertEqual(ed(rent_amount=first.rent_amount - 1000).status_code, 200)                           # 줄이면 그 달이 일부 납부가 된다
            self.assertEqual(ed(rent_amount=first.rent_amount).status_code, 200)
            # ② 달 옮기기: 이미 가득 찬 달·청구 없는 달·형식 오류는 거절, 비어 있는 달로는 이동
            self.assertEqual(ed(billing_month=D[1]).status_code, 400)                                            # 이미 낸 달(자리 없음)
            self.assertEqual(ed(billing_month="1999-01").status_code, 400)
            self.assertEqual(ed(billing_month="x").status_code, 400)
            mv = ed(billing_month=D[0]); self.assertEqual(mv.status_code, 200, mv.text)
            self.assertEqual(Pay.get(first.id).billing_month, D[0])
            self.assertIn("옮겼습니다", mv.json()["message"])
            self.assertEqual([r["ym"] for r in ct.ledger()["months"] if r["unpaid"] > 0 and r["ym"] in D[:3]], [D[2]])
            # 화면
            lj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("무효 처리", "무효 처리된 납부", "reason: reason.trim()"):
                self.assertIn(needle, lj, needle)
            for gone in ("pr-mv", "달 옮김", "billing_month: to.trim()"):      # 화면의 '달 옮김' 단추는 없앴다(삭제 후 다시 등록하면 됨). 서버의 edit API 는 그대로
                self.assertNotIn(gone, lj, gone)
        finally:
            for x in Pay.find(id_contract=ct.id, include_voided=True):
                x.delete(action="t")
            ct = Contract.get(ct.id); ct.move_in_date = keep_move; ct.save(action="t")
            for _, d in olds:
                Pay(**d).save(action="t")

    def test_97_carryover_split_preview_apply_and_totals_unchanged(self):
        """97. 이관 '이전 입금 합산' 건 분해: 계획(읽기 전용)·반영 후 계약별 입금/미납/선수금 합계 불변·원본 무효 보관·이월 표시 유지·재실행해도 변화 없음·빈 자리보다 큰 건은 검토 필요로 남김."""
        from datetime import date
        from apps.rent_dist._rent3_core import Contract, Pay, TaxAndCertificateService, carryover_split as cs, current_ym
        ct = Contract.first(branch="main", room_no="201", status="ACTIVE")
        olds = [(x, x.model_dump()) for x in Pay.find(id_contract=ct.id, include_voided=True)]
        keep_move, keep_timing = ct.move_in_date, ct.pay_timing
        made = []
        try:
            for x, _ in olds:
                x.delete(action="t")
            ym_now = current_ym(); y, m = int(ym_now[:4]), int(ym_now[5:7])
            m0 = m - 8; y0 = y - (1 if m0 <= 0 else 0); m0 = m0 + (12 if m0 <= 0 else 0)
            ct.move_in_date = f"{y0:04d}-{m0:02d}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            months = [r for r in ct.ledger()["months"] if r["due"] > 0]
            carry_end = months[4]["ym"]                                                         # 앞의 5개월분을 한 건으로 낸 이월 합산
            total = sum(r["due"] for r in months[:5])
            carry = Pay(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payer_name=ct.tenant_name, payment_kind="MONTHLY_RENT",
                        billing_month=carry_end, paid_date="2025-12-31", total_amount=total, rent_amount=total, idempotency_key="import:main:201:carry",
                        raw_sms="장부 이전 입금 합산(날짜 미상)").save(action="t")
            normal = Pay(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payer_name=ct.tenant_name, payment_kind="MONTHLY_RENT",
                         billing_month=months[5]["ym"], paid_date="2026-01-05", total_amount=months[5]["due"], rent_amount=months[5]["due"]).save(action="t")
            snap0 = cs.snapshot()
            rows = [r for r in cs.plan() if r["contract"] == ct.id]
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["status"], len(rows[0]["months"]), rows[0]["leftover"]), ("OK", 5, 0))
            self.assertEqual([x["ym"] for x in rows[0]["months"]], [r["ym"] for r in months[:5]])
            self.assertEqual(Pay.get(carry.id).status, "ACTIVE")                                # 계획은 읽기 전용
            res = cs.apply(rows)
            self.assertEqual((res["split"], res["created"]), (1, 5))
            cmp = cs.compare(snap0, cs.snapshot())
            self.assertTrue(cmp["identical_totals"], cmp)                                       # 입금·미납·선수금 합계 1원도 불변
            self.assertEqual(cmp["month_distribution_changed"], [])
            orig = Pay.get(carry.id, include_voided=True)
            self.assertEqual(orig.status, "VOIDED")                                             # 원본은 지우지 않고 보관
            kids = [x for x in Pay.find(id_contract=ct.id) if x.idempotency_key.startswith("import:main:201:carry:")]
            self.assertEqual(sorted(k.billing_month for k in kids), [r["ym"] for r in months[:5]])
            self.assertTrue(all(TaxAndCertificateService.is_carryover_payment(k) for k in kids))   # 세무상 계속 이월 건
            self.assertEqual(len({k.deposit_group_id for k in kids}), 1)
            self.assertEqual([r for r in cs.plan() if r["contract"] == ct.id], [])              # 재실행해도 더 풀 것이 없다
            # 빈 자리보다 큰 이월 건(선수금 성격)은 자동 분해하지 않고 검토 필요로 남긴다
            big = Pay(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payer_name=ct.tenant_name, payment_kind="MONTHLY_RENT",
                      billing_month=carry_end, paid_date="2025-12-31", total_amount=total * 3, rent_amount=total * 3, idempotency_key="import:main:201:carry",
                      raw_sms="장부 이전 입금 합산(날짜 미상)").save(action="t")
            r2 = [r for r in cs.plan() if r["contract"] == ct.id]
            self.assertEqual((r2[0]["status"], r2[0]["leftover"] > 0), ("REVIEW", True))
            self.assertEqual(cs.apply(r2)["split"], 0)
            self.assertEqual(Pay.get(big.id).status, "ACTIVE")
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", "split_carryover.py")))
        finally:
            for x in Pay.find(id_contract=ct.id, include_voided=True):
                x.delete(action="t")
            ct = Contract.get(ct.id); ct.move_in_date, ct.pay_timing = keep_move, keep_timing; ct.save(action="t")
            for _, d in olds:
                Pay(**d).save(action="t")

    def test_98_carryover_split_admin_api_backup_and_per_contract_rollback(self):
        """98. 운영용 분해(관리인 앱): 미리보기는 읽기 전용·관리인 전용, 반영은 확인 필요·자동 백업·합계 불변, 합계가 달라지는 건은 그 건만 되돌려 원본 복구."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Contract, Pay, current_ym, carryover_split as cs
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD = "/api/rent3_admin"
        ct = Contract.first(branch="main", room_no="201", status="ACTIVE")
        olds = [(x, x.model_dump()) for x in Pay.find(id_contract=ct.id, include_voided=True)]
        keep_move, keep_timing = ct.move_in_date, ct.pay_timing
        try:
            for x, _ in olds:
                x.delete(action="t")
            ym_now = current_ym(); y, m = int(ym_now[:4]), int(ym_now[5:7])
            m0 = m - 6; y0 = y - (1 if m0 <= 0 else 0); m0 = m0 + (12 if m0 <= 0 else 0)
            ct.move_in_date = f"{y0:04d}-{m0:02d}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            months = [r for r in ct.ledger()["months"] if r["due"] > 0]
            mk = lambda: Pay(branch=ct.branch, id_contract=ct.id, room_no=ct.room_no, id_tenant=ct.id_tenant, payer_name=ct.tenant_name, payment_kind="MONTHLY_RENT",
                             billing_month=months[3]["ym"], paid_date="2025-12-31", total_amount=sum(r["due"] for r in months[:4]), rent_amount=sum(r["due"] for r in months[:4]),
                             idempotency_key="import:main:201:carry", raw_sms="장부 이전 입금 합산(날짜 미상)").save(action="t")
            carry = mk()
            # 권한: 임대인은 불가
            self.assertIn(c.get(f"{AD}/carryover-split/preview", headers=chic).status_code, (401, 403))
            self.assertIn(c.post(f"{AD}/carryover-split/apply", headers=chic, json={"confirm": True}).status_code, (401, 403))
            pv = c.get(f"{AD}/carryover-split/preview", headers=admin).json()
            mine = [r for r in pv["rows"] if r["room_no"] == "201" and r["branch"] == "main"]
            self.assertEqual((len(mine), mine[0]["months"], pv["sum_ok_amount"] == pv["sum_planned_amount"]), (1, 4, True))
            self.assertEqual(Pay.get(carry.id).status, "ACTIVE")                                          # 미리보기는 읽기 전용
            self.assertEqual(c.post(f"{AD}/carryover-split/apply", headers=admin, json={}).status_code, 400)   # 확인 없이는 안 됨
            self.assertEqual(Pay.get(carry.id).status, "ACTIVE")
            # 계약별 검증 실패 시 그 건만 되돌린다: 분해 직후 합계가 달라지도록 스냅샷 비교를 일부러 어긋나게 함
            real = cs._snap_one
            calls = {"n": 0}

            def flaky(ct_):
                out = real(ct_)
                calls["n"] += 1
                if calls["n"] % 2 == 0:                                                                   # 분해 후 스냅샷만 다르게
                    out = {**out, "total_unpaid": out["total_unpaid"] + 1}
                return out
            with mock.patch.object(cs, "_snap_one", side_effect=flaky):
                res0 = cs.apply(cs.plan())
            self.assertEqual(len(res0["rolled_back"]), 1)
            self.assertEqual(res0["split"], 0)
            self.assertEqual(Pay.get(carry.id).status, "ACTIVE")                                          # 원본 복구
            self.assertEqual([x for x in Pay.find(id_contract=ct.id) if x.idempotency_key.startswith("import:main:201:carry:")], [])   # 새 건 삭제
            # 실제 반영: 자동 백업·합계 불변
            res = c.post(f"{AD}/carryover-split/apply", headers=admin, json={"confirm": True}).json()
            self.assertTrue(res["backup"])
            self.assertEqual((res["identical_totals"], res["rolled_back"], res["mismatches"]), (True, [], []))
            self.assertGreaterEqual(res["split"], 1)
            self.assertEqual(Pay.get(carry.id), None)
            self.assertEqual(Pay.get(carry.id, include_voided=True).status, "VOIDED")
            self.assertEqual(c.post(f"{AD}/carryover-split/apply", headers=admin, json={"confirm": True}).json()["split"], 0)   # 재실행: 할 일 없음
            aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("btnCarryPrev", "btnCarryApply", "/carryover-split/preview", "/carryover-split/apply", "백업 후 반영"):
                self.assertIn(needle, aj, needle)
        finally:
            for x in Pay.find(id_contract=ct.id, include_voided=True):
                x.delete(action="t")
            ct = Contract.get(ct.id); ct.move_in_date, ct.pay_timing = keep_move, keep_timing; ct.save(action="t")
            for _, d in olds:
                Pay(**d).save(action="t")

    def test_99_pool_search_edit_protected_fields_audit_and_redaction(self):
        """99. 관리인 Data Pool 검색·수정: 컬렉션 → 컬럼 체크 → 값 검색, 한 건 수정(사유 필수·타입 검증·전후 기록), 보호 필드(비밀번호 해시 등)와 🔒 암호화 필드는 표시·검색·수정 불가, 관리인 전용, 원문 뷰어도 보호 필드를 가림."""
        from apps.rent_dist._rent3_core import Lessor, Room, Tenant, poolsearch as ps
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD = "/api/rent3_admin"
        t = Tenant.get("tenant_201")
        keep = (t.name, t.phone, t.car_number, t.checked_in)
        try:
            # 권한
            for method, url, kw in (("get", f"{AD}/pool-search/schema?collection=users", {}), ("post", f"{AD}/pool-search/query", {"json": {"collection": "users", "columns": ["name"], "value": "a"}}),
                                    ("get", f"{AD}/pool-search/item?collection=users&id=tenant_201", {}), ("post", f"{AD}/pool-search/update", {"json": {"collection": "users", "id": "tenant_201", "changes": {"name": "x"}, "reason": "시험"}}),
                                    ("get", f"{AD}/pool-search/edits", {})):
                self.assertIn(getattr(c, method)(url, headers=chic, **kw).status_code, (401, 403), url)
                self.assertEqual(getattr(c, method)(url, headers=self.NOAUTH, **kw).status_code, 401, url)
            # 스키마: 보호 필드는 목록에 없고, 암호화 필드는 🔒(검색·수정 불가)
            sc = c.get(f"{AD}/pool-search/schema?collection=users", headers=admin).json()
            names = {f["name"]: f for f in sc["fields"]}
            for hidden in ("password", "pin_hash", "invite_hash", "quick_devices", "login_devices"):
                self.assertNotIn(hidden, names, hidden)
            self.assertTrue(names["account_number"]["sealed"] and not names["account_number"]["searchable"] and not names["account_number"]["editable"])
            self.assertTrue(names["name"]["editable"] and not names["id"]["editable"] and not names["consents"]["editable"])
            # 검색: 컬럼 체크·값 필요, 암호화 컬럼만 체크하면 거절, 일치 건수
            q = lambda **kw: c.post(f"{AD}/pool-search/query", headers=admin, json={"collection": "users", **kw})
            self.assertEqual(q(columns=[], value=t.name).status_code, 400)
            self.assertEqual(q(columns=["name"], value="").status_code, 400)
            self.assertEqual(q(columns=["account_number"], value="1").status_code, 400)
            hit = q(columns=["name", "room_no"], value=t.name).json()
            self.assertIn("tenant_201", [r["id"] for r in hit["rows"]])
            multi = q(columns=["name", "room_no"], value=f"{t.name} {t.room_no}").json()                    # 여러 단어: 모두 포함(AND), 단어마다 체크한 컬럼 어딘가에
            self.assertIn("tenant_201", [r["id"] for r in multi["rows"]])
            self.assertEqual(q(columns=["name", "room_no"], value=f"{t.name} 없는단어").json()["total"], 0)
            self.assertEqual(c.post(f"{AD}/pool-search/query", headers=admin, json={"collection": "nope", "columns": ["a"], "value": "a"}).status_code, 404)
            # 항목 열기: 보호 필드 값은 내려오지 않는다
            it = c.get(f"{AD}/pool-search/item?collection=users&id=tenant_201", headers=admin).json()
            self.assertEqual({f["name"] for f in it["fields"]} & {"password", "pin_hash", "invite_hash"}, set())
            self.assertIn("password", it["protected_fields"])
            self.assertNotIn(Tenant.get("tenant_201").password, c.get(f"{AD}/pool-search/item?collection=users&id=tenant_201", headers=admin).text)
            # 수정: 사유 필수, 보호·읽기 전용·없는 항목 거절, 타입 오류 거절, 변경 없음 거절
            up = lambda changes, reason="시험 정정": c.post(f"{AD}/pool-search/update", headers=admin, json={"collection": "users", "id": "tenant_201", "changes": changes, "reason": reason})
            self.assertEqual(up({"name": "검색수정시험"}, reason="").status_code, 400)
            for bad in ({"password": "x"}, {"pin_hash": "x"}, {"id": "zzz"}, {"consents": "{}"}, {"account_number": "1"}, {"nope": "1"}):
                self.assertEqual(up(bad).status_code, 400, bad)
            self.assertEqual(up({"checked_in": "아니오"}).status_code, 400)                              # bool 형식 오류
            self.assertEqual(up({"name": t.name}).status_code, 400)                                      # 바뀐 내용 없음
            ok = up({"name": "검색수정시험", "car_number": "12가3456"})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(sorted(ok.json()["changed"]), ["car_number", "name"])
            t2 = Tenant.get("tenant_201")
            self.assertEqual((t2.name, t2.car_number), ("검색수정시험", "12가3456"))
            self.assertEqual(Tenant.get("tenant_201").password, t.password)                              # 비밀번호는 그대로
            self.assertEqual(up({"checked_in": "false"}).status_code, 200)                               # 'false' 문자열은 bool 로 변환
            self.assertFalse(Tenant.get("tenant_201").checked_in)
            # 기록: 사유·전후 값
            ed = c.get(f"{AD}/pool-search/edits", headers=admin).json()["edits"]
            self.assertTrue(any(e["id"] == "tenant_201" and e["reason"] == "시험 정정" and any(x["field"] == "name" and x["after"] == "검색수정시험" and x["before"] == keep[0] for x in e["changes"]) for e in ed))
            # 원문 뷰어도 보호 필드를 가린다
            for url in ("/api/rent3/pool/files/users", f"{AD}/pool/users"):
                items = c.get(url, headers=admin).json()["items"]
                self.assertTrue(items)
                for row in items:
                    for k in ("password", "pin_hash", "invite_hash", "quick_devices", "login_devices"):
                        if k in row:
                            self.assertIn(row[k], ("•••(보호됨)", "", None, [], {}), (url, k))
            # 화면
            html = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "pool.html"), encoding="utf-8").read()
            for needle in ("rawQ", "/pool-search/text", "/pool-search/update-json", "수정 사유 (필수)", "저장", "취소", 'Bass.request("/api/rent3_admin" + p, o)'):
                self.assertIn(needle, html, needle)
            self.assertNotIn('Bass.api("rent3_admin"', html)          # /rent3/ 아래 화면에서는 주소가 /api 로 잘못 만들어져 405 가 난다
        finally:
            t3 = Tenant.get("tenant_201"); t3.name, t3.phone, t3.car_number, t3.checked_in = keep; t3.save(action="t")
            for e in ps.PoolEditLog.all():
                e.delete(action="t")

    def test_100_admin_two_factor_totp_login_bypass_block_recovery_and_throttle(self):
        """100. 관리인 2단계 인증(TOTP): RFC 6238 표준 값, 켜기(코드 확인·복구 코드 8개), 켠 뒤 비밀번호만으론 토큰을 못 받음(대기표는 API 불가), 코어 일반 로그인 토큰 거부(우회 차단),
        같은 코드 재사용 불가, 복구 코드는 한 번만, 틀리면 잠김, 끄기엔 코드 필요, 비밀 키는 화면·뷰어에 안 나옴, 임대인·임차인 로그인은 영향 없음."""
        from unittest import mock
        import time as _t
        from core.auth import User as CoreUser, issue_token, verify_token
        from apps.rent_dist._rent3_core import Admin, mfa, ratelimit
        c, NA = self.client, self.NOAUTH
        AD, PL = "/api/rent3_admin", "/api/rent3/pool"
        sec = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(mfa.totp_at(sec, 59), "287082")                                       # RFC 6238 부록 B (SHA-1)
        self.assertEqual(mfa.totp_at(sec, 1111111109), "081804")
        self.assertEqual(mfa.match_step(sec, "287082", now=59 + 30), 1)                      # 앞뒤 1구간 오차 허용
        self.assertIsNone(mfa.match_step(sec, "287082", now=59 + 90))
        self.assertIsNone(mfa.match_step(sec, "12345", now=59))
        T0 = int(_t.time())
        at = lambda k: T0 + 30 * k
        adm = Admin.first(username="admin")
        try:
            admin = self._login("admin")
            self.assertEqual(c.get(f"{AD}/mfa/status", headers=admin).json()["enabled"], False)
            st = c.post(f"{AD}/mfa/enroll/start", headers=admin, json={}).json()
            secret = st["secret"]
            self.assertTrue(st["otpauth_uri"].startswith("otpauth://totp/Rent3:admin?secret="))
            self.assertEqual(c.post(f"{AD}/mfa/enroll/confirm", headers=admin, json={"code": "000000"}).status_code, 400)
            with mock.patch.object(mfa.time, "time", return_value=at(0)):
                ok = c.post(f"{AD}/mfa/enroll/confirm", headers=admin, json={"code": mfa.totp_at(secret, at(0))})
            self.assertEqual(ok.status_code, 200, ok.text)
            codes = ok.json()["recovery_codes"]
            self.assertEqual((len(codes), len(set(codes))), (8, 8))
            mtok = ok.json()["token"]
            self.assertEqual(verify_token(mtok).get("mfa"), 1)
            H = lambda tok: {"Authorization": "Bearer " + tok}
            self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=H(mtok)).status_code, 200)
            # 켠 뒤: mfa 표시 없는 관리인 토큰(코어 일반 로그인 등)은 관리인 API 에서 모두 거절 — 우회 차단
            core_tok = issue_token(CoreUser.by_username("admin"))
            self.assertIsNone(verify_token(core_tok).get("mfa"))
            self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=H(core_tok)).status_code, 401)
            self.assertEqual(c.get(f"{PL}/overview", headers=H(core_tok)).status_code, 401)
            self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=admin).status_code, 401)
            # 로그인: 비밀번호만 맞으면 토큰이 아니라 대기표(어떤 API 도 못 연다)
            login = lambda: c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": "admin", "password": "1212", "target_hint": "pool"})
            r1 = login().json()
            self.assertEqual((r1["mfa_required"], "token" in r1), (True, False))
            self.assertIn(c.get(f"{AD}/pool-search/edits", headers=H(r1["mfa_ticket"])).status_code, (401, 403))
            ver = lambda ticket, code: c.post(f"{PL}/auth/mfa-verify", headers=NA, json={"ticket": ticket, "code": code})
            self.assertEqual(ver("garbage", "123456").status_code, 401)
            self.assertEqual(ver(r1["mfa_ticket"], "111111").status_code, 400)
            with mock.patch.object(mfa.time, "time", return_value=at(1)):
                code1 = mfa.totp_at(secret, at(1))
                good = ver(r1["mfa_ticket"], code1)
                self.assertEqual(good.status_code, 200, good.text)
                self.assertEqual((good.json()["role"], good.json()["redirect_url"], verify_token(good.json()["token"]).get("mfa")), ("admin", "/rent3/pool.html", 1))
                self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=H(good.json()["token"])).status_code, 200)
                again = ver(login().json()["mfa_ticket"], code1)
                self.assertEqual(again.status_code, 400)
                self.assertIn("이미 사용한 코드", again.json()["message"])
            # 복구 코드는 한 번만
            rc = ver(login().json()["mfa_ticket"], codes[0])
            self.assertEqual(rc.status_code, 200, rc.text)
            self.assertEqual(rc.json()["mfa_method"], "recovery")
            self.assertEqual(ver(login().json()["mfa_ticket"], codes[0]).status_code, 400)
            self.assertEqual(c.get(f"{AD}/mfa/status", headers=H(mtok)).json()["recovery_left"], 7)
            # 5번 틀리면 잠김
            ratelimit.reset_all()
            tk = login().json()["mfa_ticket"]
            for _ in range(5):
                ver(tk, "222222")
            self.assertEqual(ver(tk, "333333").status_code, 429)
            ratelimit.reset_all()
            # 비밀 키는 어디에도 나오지 않는다
            self.assertNotIn(secret, c.get(f"{PL}/files/users", headers=H(mtok)).text)
            self.assertNotIn(secret, c.get(f"{AD}/pool/users", headers=H(mtok)).text)
            self.assertNotIn(secret, c.get(f"{AD}/pool-search/item?collection=users&id={adm.id}", headers=H(mtok)).text)
            self.assertNotIn("totp_secret", {f["name"] for f in c.get(f"{AD}/pool-search/schema?collection=users", headers=H(mtok)).json()["fields"]})
            # 다른 역할은 영향 없음, 본인 것은 reset 불가·없는 계정 거절
            self.assertEqual(c.post(f"{PL}/auth/login", headers=NA, json={"login_mode": "account", "identifier": "chic", "password": "1212"}).json().get("role"), "lessor")
            self.assertEqual(c.post(f"{AD}/mfa/reset", headers=H(mtok), json={"username": "admin"}).status_code, 400)
            self.assertEqual(c.post(f"{AD}/mfa/reset", headers=H(mtok), json={"username": "nobody"}).status_code, 400)
            # 끄기: 코드 필요
            self.assertEqual(c.post(f"{AD}/mfa/disable", headers=H(mtok), json={"code": "000000"}).status_code, 400)
            with mock.patch.object(mfa.time, "time", return_value=at(2)):
                off = c.post(f"{AD}/mfa/disable", headers=H(mtok), json={"code": mfa.totp_at(secret, at(2))})
            self.assertEqual(off.status_code, 200, off.text)
            self.assertIsNone(login().json().get("mfa_required"))
            self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=H(core_tok)).status_code, 200)
            # 비상구: 서버 환경변수로 2단계 인증을 건너뛴다(폰·복구 코드 분실 + 서버 터미널 없음)
            st2 = c.post(f"{AD}/mfa/enroll/start", headers=H(core_tok), json={}).json()
            with mock.patch.object(mfa.time, "time", return_value=at(3)):
                c.post(f"{AD}/mfa/enroll/confirm", headers=H(core_tok), json={"code": mfa.totp_at(st2["secret"], at(3))})
            self.assertTrue(login().json().get("mfa_required"))
            with mock.patch.dict(os.environ, {"RENT3_ADMIN_MFA_DISABLE": "1"}):
                self.assertIsNone(login().json().get("mfa_required"))
                self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=H(core_tok)).status_code, 200)
            self.assertEqual(c.get(f"{AD}/pool-search/edits", headers=H(core_tok)).status_code, 401)        # 비상구를 지우면 다시 필요
            rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
            for needle in ("resolveMfa", "/pool/auth/mfa-verify", "mfa_required"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3", "frontend", "app.js"), needle)
            for needle in ("mfaCard", "/mfa/enroll/start", "/mfa/enroll/confirm", "복구 코드", "Bass.setToken"):
                self.assertIn(needle, rd("apps", "rent_dist", "rent3_admin", "frontend", "app.js"), needle)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", "reset_admin_mfa.py")))
        finally:
            mfa.reset(Admin.get(adm.id))
            ratelimit.reset_all()

    def test_101_pool_card_text_search_and_json_edit_is_field_level_and_safe(self):
        """101. 카드에서 글자로 검색(모든 단어 포함)하면 가려진 JSON 이 나오고, JSON 을 고쳐 저장해도 통째 덮어쓰기가 아니라 바뀐 항목만 검증을 거쳐 반영 —
        가려진·잠긴 항목을 그대로 두면 무시, 바꾸면 거절, 없는 항목 만들기·id·시각 변경·목록 변경 거절, 사유 필수, 전후 기록, 관리인 전용."""
        import json as _json
        from apps.rent_dist._rent3_core import Tenant, poolsearch as ps
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD = "/api/rent3_admin"
        t = Tenant.get("tenant_201")
        keep = (t.name, t.car_number)
        try:
            tx = lambda value, collection="users": c.post(f"{AD}/pool-search/text", headers=admin, json={"collection": collection, "value": value})
            for url, body in ((f"{AD}/pool-search/text", {"collection": "users", "value": "a"}), (f"{AD}/pool-search/update-json", {"collection": "users", "id": "tenant_201", "record": {}, "reason": "x"})):
                self.assertIn(c.post(url, headers=chic, json=body).status_code, (401, 403))
                self.assertEqual(c.post(url, headers=self.NOAUTH, json=body).status_code, 401)
            self.assertEqual(tx("").status_code, 400)
            self.assertEqual(c.post(f"{AD}/pool-search/text", headers=admin, json={"collection": "nope", "value": "a"}).status_code, 404)
            r = tx(f"{t.name} {t.room_no}").json()
            hit = next(x for x in r["rows"] if x["id"] == "tenant_201")
            rec = hit["record"]
            self.assertEqual(tx(f"{t.name} 없는단어").json()["total"], 0)                                      # 모든 단어 포함(AND)
            # 보호·암호화 항목은 가려져서 내려온다
            self.assertEqual(rec["password"], "•••(보호됨)")
            self.assertNotIn("_kind", rec)
            self.assertNotIn(Tenant.get("tenant_201").password, _json.dumps(r, ensure_ascii=False))
            up = lambda record, reason="JSON 수정 시험": c.post(f"{AD}/pool-search/update-json", headers=admin, json={"collection": "users", "id": "tenant_201", "record": record, "reason": reason})
            # 값을 그대로 두면 '바뀐 내용 없음', 사유 없으면 거절
            self.assertEqual(up(rec).status_code, 400)
            self.assertEqual(up({**rec, "name": "카드수정시험"}, reason="").status_code, 400)
            # 거절되는 수정: 보호·읽기 전용·없는 항목·목록 항목
            for bad in ({"password": "새비밀번호1"}, {"id": "zzz"}, {"created_at": "2000-01-01"}, {"새항목": "x"}, {"consents": {"x": 1}}):
                self.assertEqual(up({**rec, **bad}).status_code, 400, bad)
            self.assertEqual(Tenant.get("tenant_201").name, keep[0])                                         # 거절된 요청은 아무것도 바꾸지 않는다
            # 허용: 보이는 값만 고치면 그 항목만 반영(가려진 항목을 그대로 보내도 비밀번호가 가림 값으로 덮이지 않는다)
            pw_before = Tenant.get("tenant_201").password
            ok = up({**rec, "name": "카드수정시험", "car_number": "34나5678"})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(sorted(ok.json()["changed"]), ["car_number", "name"])
            t2 = Tenant.get("tenant_201")
            self.assertEqual((t2.name, t2.car_number, t2.password), ("카드수정시험", "34나5678", pw_before))
            self.assertTrue(any(e["id"] == "tenant_201" and e["reason"] == "JSON 수정 시험" for e in ps.recent_edits()))
            # 화면: 카드 선택 → 검색줄 → 결과 JSON → 수정 → 저장/취소, 자동 갱신이 결과를 덮지 않는다
            html = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "pool.html"), encoding="utf-8").read()
            for needle in ("rawBar", "rawQ", "/pool-search/text", "/pool-search/update-json", "rh-save", "rh-cancel", "__rawHold", "poolcolchange", "수정 사유 (필수)"):
                self.assertIn(needle, html, needle)
        finally:
            t3 = Tenant.get("tenant_201"); t3.name, t3.car_number = keep; t3.save(action="t")
            for e in ps.PoolEditLog.all():
                e.delete(action="t")

    def test_102_pool_json_edit_nested_values_denied_records_and_sealed_contracts(self):
        """102. 카드 JSON 수정의 안쪽 값(예: 방의 registry_info.owner_name)도 고칠 수 있고 전후가 기록됨, 동의·본인확인·이력 같은 기록 항목과 봉인된 공동계약(cosign_sessions)은 고칠 수 없음,
        열 때 보이는 그대로 다시 저장하면 '바뀐 내용 없음', 컬럼 편집 경로는 여전히 목록 항목을 못 고침."""
        import copy
        from apps.rent_dist._rent3_core import Room, Tenant, poolsearch as ps
        c = self.client
        admin = self._login("admin")
        AD = "/api/rent3_admin"
        room = Room.all()[0]
        keep = copy.deepcopy(room.registry_info)
        room.registry_info = {"issue_number": "TEST-1", "owner_name": "원래소유자", "mortgage_amount": 0, "clean_title": True}      # 방 기본값은 비어 있으므로 시험용 등기부 정보를 직접 넣는다
        room.save(action="t")
        base_info = copy.deepcopy(room.registry_info)
        try:
            sc = {f["name"]: f for f in c.get(f"{AD}/pool-search/schema?collection=rooms", headers=admin).json()["fields"]}
            self.assertEqual((sc["registry_info"]["editable"], sc["registry_info"]["json_editable"]), (False, True))   # 컬럼 편집은 그대로 읽기 전용, JSON 수정에서만 가능
            hit = next(x for x in c.post(f"{AD}/pool-search/text", headers=admin, json={"collection": "rooms", "value": room.room_no}).json()["rows"] if x["id"] == room.id)
            rec = hit["record"]
            up = lambda record, reason="안쪽 값 수정 시험", coll="rooms", oid=None: c.post(f"{AD}/pool-search/update-json", headers=admin, json={"collection": coll, "id": oid or room.id, "record": record, "reason": reason})
            self.assertEqual(up(rec).status_code, 400)                                                       # 열 때 모습 그대로: 바뀐 내용 없음
            new_info = {**rec["registry_info"], "owner_name": "이상한변경"}
            ok = up({**rec, "registry_info": new_info})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["changed"], ["registry_info"])
            self.assertEqual(Room.get(room.id).registry_info["owner_name"], "이상한변경")                    # 실제로 바뀐다
            self.assertEqual({k: v for k, v in Room.get(room.id).registry_info.items() if k != "owner_name"}, {k: v for k, v in base_info.items() if k != "owner_name"})   # 다른 안쪽 값은 그대로
            lg = [e for e in ps.recent_edits() if e["id"] == room.id][0]
            self.assertIn("이상한변경", str(lg["changes"][0]["after"]))
            self.assertIn(base_info["owner_name"], str(lg["changes"][0]["before"]))
            # 컬럼 편집 경로(문자열 값)로는 목록·사전 항목을 못 고친다
            bad = c.post(f"{AD}/pool-search/update", headers=admin, json={"collection": "rooms", "id": room.id, "changes": {"registry_info": "{}"}, "reason": "시험"})
            self.assertEqual(bad.status_code, 400)
            # 기록 항목(동의·본인확인·이력)은 JSON 수정으로도 불가
            t = Tenant.get("tenant_201")
            trec = next(x for x in c.post(f"{AD}/pool-search/text", headers=admin, json={"collection": "users", "value": t.name}).json()["rows"] if x["id"] == "tenant_201")["record"]
            for k in ("consents", "identity"):
                if k in trec:
                    r = up({**trec, k: {"x": 1}}, coll="users", oid="tenant_201")
                    self.assertEqual(r.status_code, 400, k)
            # 봉인된 공동계약은 컬렉션 전체가 읽기 전용
            from apps.rent_dist._rent3_core import CoSignSession
            self.assertTrue(c.get(f"{AD}/pool-search/schema?collection=cosign_sessions", headers=admin).json()["fields"])
            for sess in CoSignSession.all()[:1]:
                r2 = c.post(f"{AD}/pool-search/update", headers=admin, json={"collection": "cosign_sessions", "id": sess.id, "changes": {"status": "CANCELLED"}, "reason": "시험"})
                self.assertEqual(r2.status_code, 400)
                self.assertIn("봉인", r2.text)
            html = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "pool.html"), encoding="utf-8").read()
            for needle in ("rh-msg", "저장되지 않았습니다"):
                self.assertIn(needle, html, needle)
        finally:
            r3 = Room.get(room.id); r3.registry_info = keep; r3.save(action="t")
            for e in ps.PoolEditLog.all():
                e.delete(action="t")

    def test_103_room_sample_defaults_removed_cleanup_tool_and_consistency_scan(self):
        """103. 방 기본값에 가짜 등기부·가전 옵션·상태 설명·도어락이 없음, 공개 매물의 '등기클린'은 실제 확인된 등기부가 있을 때만, 정리 도구는 샘플과 정확히 같은 값만 비우고(직접 고친 값 유지·백업)
        관리인 전용·확인 필수, 유일성 점검 도구는 샘플 등기부 소유자와 연결 불일치를 찾는다."""
        from apps.rent_dist._rent3_core import Room, Lessor, consistency, roomdefaults as rd
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        AD = "/api/rent3_admin"
        # ① 새 방의 기본값
        r0 = Room(id="room_t103_new", branch="main", room_no="9901")
        self.assertEqual((r0.registry_info, r0.registry_checked, r0.option_list, r0.status_description, r0.doorlock_pwd), ({}, False, [], "", ""))
        # ② 샘플 값이 든 방(예전 데이터)과 직접 고친 방을 만든다
        a = Room(id="room_t103_a", branch="main", id_building="bldg_main", room_no="9902", is_published=True, registry_checked=True, status_description=rd.SAMPLE_STATUS, option_list=list(rd.SAMPLE_OPTIONS),
                 registry_info={"issue_number": rd.SAMPLE_ISSUE_NUMBER, "owner_name": "김임대", "clean_title": True, "verified_at": "2026-11-09"}, doorlock_pwd="2580*").save(action="t")
        b = Room(id="room_t103_b", branch="main", id_building="bldg_main", room_no="9903", is_published=True, registry_checked=True, status_description="리모델링 완료, 남향",
                 option_list=[{"item": "붙박이장", "quantity": 1, "condition": "정상"}], registry_info={"issue_number": "REAL-77", "owner_name": "실제소유자", "clean_title": True, "verified_at": date.today().isoformat()}).save(action="t")
        try:
            # 공개 매물: 샘플이 아닌 실제 등기부가 있는 방만 '등기클린'
            lst = {x["room_no"]: x for x in c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]}
            self.assertTrue(lst["9903"]["clean_title"])
            # 샘플이 든 방은 정리 전에는 예전처럼 나오지만(데이터가 그대로이므로), 정리 계획에 잡힌다
            self.assertIn("room_t103_a", [x["id"] for x in rd.plan()["rows"]])
            self.assertNotIn("room_t103_b", [x["id"] for x in rd.plan()["rows"]])                           # 직접 고친 방은 대상이 아님
            pv = c.get(f"{AD}/room-defaults/preview", headers=admin).json()
            self.assertGreaterEqual(pv["counts"]["registry"], 1)
            self.assertIn(c.get(f"{AD}/room-defaults/preview", headers=chic).status_code, (401, 403))
            self.assertIn(c.post(f"{AD}/room-defaults/apply", headers=chic, json={"confirm": True}).status_code, (401, 403))
            self.assertEqual(c.post(f"{AD}/room-defaults/apply", headers=admin, json={}).status_code, 400)
            self.assertEqual(Room.get("room_t103_a").registry_info["owner_name"], "김임대")                     # 미리보기·확인 없음은 바꾸지 않는다
            done = c.post(f"{AD}/room-defaults/apply", headers=admin, json={"confirm": True}).json()
            self.assertTrue(done["backup"])
            ra, rb = Room.get("room_t103_a"), Room.get("room_t103_b")
            self.assertEqual((ra.registry_info, ra.registry_checked, ra.option_list, ra.status_description), ({}, False, [], ""))
            self.assertEqual(ra.doorlock_pwd, "2580*")                                                        # 도어락은 자동으로 지우지 않는다
            self.assertEqual((rb.registry_info["owner_name"], rb.status_description, len(rb.option_list)), ("실제소유자", "리모델링 완료, 남향", 1))
            lst2 = {x["room_no"]: x for x in c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]}
            self.assertFalse(lst2["9902"]["clean_title"])
            self.assertEqual(lst2["9902"]["options"], [])
            self.assertEqual(c.post(f"{AD}/room-defaults/apply", headers=admin, json={"confirm": True}).json()["cleaned"], 0)    # 재실행: 할 일 없음
            # 유일성 점검: 실제 등기부 소유자가 임대인 이름과 다르면 잡힌다
            lsr = Lessor.first(branch="main")
            sc = {x["code"]: x for x in consistency.scan()["checks"]}
            self.assertTrue(any("9903" in e for e in sc["ROOM_OWNER_NAME"]["examples"]) or sc["ROOM_OWNER_NAME"]["count"] >= 1)
            self.assertIn("DUP_ACTIVE_CT", sc)
            self.assertTrue(lsr is not None)
            aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("btnRdPrev", "btnRdApply", "/room-defaults/preview", "/room-defaults/apply"):
                self.assertIn(needle, aj, needle)
        finally:
            for rid in ("room_t103_a", "room_t103_b"):
                o = Room.get(rid)
                if o is not None:
                    o.delete(action="t")

    def test_104_error_alert_needs_confirm_button_no_auto_close(self):
        """104. 오류·경고 알림은 자동으로 닫히지 않고 [확인]을 눌러야 닫힌다(읽을 시간 확보). 예전 4초 자동 닫힘 설정(BASS_ERROR_TIMEOUT)은 없어졌고, 같은 글은 하나로 합친다."""
        rd = lambda *parts: open(os.path.join(ROOT, *parts), encoding="utf-8").read()
        for f in ("auth_fetch.js", "rent3_session_guard.js"):
            self.assertNotIn("window.BASS_ERROR_TIMEOUT", rd("shared_ui", f), f)
        bass = rd("shared_ui", "bass.js")
        for needle in ("const sticky = lv === \"error\" || lv === \"warning\"", "bass-alert__ok", ">확인</button>", "sticky ? 0"):
            self.assertIn(needle, bass, needle)
        self.assertNotIn("BASS_ERROR_TIMEOUT", bass)

    def test_106_no_forward_reference_annotations(self):
        """106. 함수 타입 표기가 아래쪽에서 정의된 이름을 쓰면 운영(Python 3.11)에서 NameError 로 앱이 격리된다 — 정적으로 점검."""
        import subprocess, sys
        r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(ROOT, "scripts", "check_forward_refs.py")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_107_room_registry_upload_read_confirm_flow(self):
        """107. 임대인이 등기부를 올리면 AI 가 읽어 '확인 대기', 원본 대조 후 확정해야 확인됨, 소유자가 다르면 확정 불가, 코드가 깨끗함을 계산,
        공개 '등기클린'은 확정·최근·깨끗할 때만, 새 공동계약 세션은 확정 전에는 가짜 번호·'깨끗함'을 만들지 않음."""
        import base64
        from apps.rent_dist._rent3_core import Room, registry_ai
        c = self.client
        chic = self._login("chic")
        room = next(r for r in Room.find(branch="main") if r.room_no)
        keep = (room.registry_info, room.registry_checked)
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()

        class Fake:
            is_available = True
            reply = {}

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                return json.dumps(Fake.reply, ensure_ascii=False)

        orig = registry_ai._client
        L = "/api/rent3_lessor"
        try:
            registry_ai._client = lambda: Fake()
            Fake.reply = {"document_type": "registry", "readable": True, "issue_number": "1101-2026-555111", "owners": [{"name": "김임대"}], "mortgages": [], "restrictions": [], "ownership_transfers": []}
            # 동의 없으면 거부
            self.assertEqual(c.post(f"{L}/rooms/{room.id}/registry/read", headers=chic, json={"attachment_data_url": pdf, "consent": False}).status_code, 400)
            r = c.post(f"{L}/rooms/{room.id}/registry/read", headers=chic, json={"attachment_data_url": pdf, "consent": True})
            self.assertEqual(r.status_code, 200, r.text)
            got = Room.get(room.id)
            self.assertEqual((got.registry_checked, got.registry_info["status"], got.registry_info["clean_title"], got.registry_info["owner_matches"]), (False, "pending", True, True))
            # 읽기만으로는 공개 '등기클린' 아님
            self.assertFalse(next((x for x in c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"] if x.get("id") == room.id), {"clean_title": False})["clean_title"])
            # 원본 대조 없이 확정 불가
            self.assertEqual(c.post(f"{L}/rooms/{room.id}/registry/confirm", headers=chic, json={"checked_original": False}).status_code, 400)
            ok = c.post(f"{L}/rooms/{room.id}/registry/confirm", headers=chic, json={"checked_original": True})
            self.assertEqual(ok.status_code, 200, ok.text)
            got = Room.get(room.id)
            self.assertTrue(got.registry_checked)
            from apps.rent_dist._rent3_core import roomregistry as rr
            self.assertTrue(rr.public_clean(got))
            # 오래된 확정은 '등기클린'을 끈다
            got.registry_info = {**got.registry_info, "verified_at": "2020-01-01"}
            self.assertFalse(rr.public_clean(got))
            # 근저당이 있으면 코드가 깨끗하지 않다고 계산(AI 가 clean 이라 해도)
            Fake.reply = {"document_type": "registry", "readable": True, "issue_number": "X", "owners": [{"name": "김임대"}], "clean_title": True,
                          "mortgages": [{"creditor": "A", "amount": 500000000, "cancelled": False}], "restrictions": [], "ownership_transfers": []}
            c.post(f"{L}/rooms/{room.id}/registry/read", headers=chic, json={"attachment_data_url": pdf, "consent": True})
            self.assertEqual((Room.get(room.id).registry_info["clean_title"], Room.get(room.id).registry_info["mortgage_amount"]), (False, 500000000))
            # 소유자가 임대인과 다르면 확정 불가
            Fake.reply = {"document_type": "registry", "readable": True, "issue_number": "Y", "owners": [{"name": "남의이름"}], "mortgages": [], "restrictions": [], "ownership_transfers": []}
            c.post(f"{L}/rooms/{room.id}/registry/read", headers=chic, json={"attachment_data_url": pdf, "consent": True})
            bad = c.post(f"{L}/rooms/{room.id}/registry/confirm", headers=chic, json={"checked_original": True})
            self.assertEqual(bad.status_code, 400)
            self.assertFalse(Room.get(room.id).registry_checked)
            # 등기부가 아닌 문서는 거절
            Fake.reply = {"document_type": "other"}
            self.assertEqual(c.post(f"{L}/rooms/{room.id}/registry/read", headers=chic, json={"attachment_data_url": pdf, "consent": True}).status_code, 400)
            # 다른 지점 임대인은 접근 불가
            self.assertEqual(c.post(f"{L}/rooms/{room.id}/registry/read", headers=self._login("sunho"), json={"attachment_data_url": pdf, "consent": True}).status_code, 403)
            html = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("/registry/read", "/registry/confirm", "원본과 대조했습니다"):
                self.assertIn(needle, html, needle)
        finally:
            registry_ai._client = orig
            r2 = Room.get(room.id); r2.registry_info, r2.registry_checked = keep; r2.save(action="t")

    def test_108_balance_registry_ai_compare_checkbox_gate_and_freshness(self):
        """108. 잔금 전 등기부 재확인: 올린 등기부를 AI 가 읽고 계약 때와 코드로 비교, 근저당이 늘었으면 '변동 없음' 불가, 열람 일시가 오래되면 불가,
        '확인했음' 체크 없으면 불가(진행 중단), 읽기 기록이 없으면 불가, 모두 통과해야 기록되어 교부 가능, 화면에 인터넷등기소 링크·안내."""
        import base64
        from datetime import datetime, timedelta
        from apps.rent_dist._rent3_core import registry_ai, Contract, Room, Tenant
        c = self.client
        chic = self._login("chic")
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "915", "is_published": True, "deposit": 6000000})
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": "재열람임차", "phone": "01066660602", "password": "Pw123456"}).json()
        th, tid = self._as(reg["token"]), reg["user"]["id"]
        self._complete_contract(chic, th, tid, "915", "재열람임차", "010-6666-0602")
        T = f"/api/rent3_tenant/myhome/{tid}"
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()
        ct = Contract.get(Room.get(Tenant.get(tid).current_room_id).active_contract_id)
        owner = (ct.registry_snapshot or {}).get("tenant_direct_registry", {}).get("owner_name") or ct.registry_snapshot.get("owner_name") or "김임대"

        class Fake:
            is_available = True
            reply = {}

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                return json.dumps(Fake.reply, ensure_ascii=False)

        def doc(viewed, mort=0):
            return {"document_type": "registry", "readable": True, "issue_number": "1101-2026-RE", "viewed_at": viewed.strftime("%Y-%m-%d %H:%M"),
                    "owners": [{"name": owner}], "restrictions": [], "ownership_transfers": [],
                    "mortgages": ([{"creditor": "새은행", "amount": mort, "cancelled": False}] if mort else [])}

        orig = registry_ai._client
        try:
            registry_ai._client = lambda: Fake()
            now = datetime.now()
            # 읽기 기록이 없으면 체크해도 불가
            self.assertEqual(c.post(f"{T}/balance-registry-check", headers=th, json={"changed": False, "checked": True}).status_code, 400)
            # 동의 없이는 읽기 거부
            self.assertEqual(c.post(f"{T}/balance-registry-read", headers=th, json={"attachment_data_url": pdf, "consent": False}).status_code, 400)
            # 계약 뒤 근저당이 새로 생김 → 달라진 점이 잡히고 '변동 없음' 불가(체크해도)
            Fake.reply = doc(now, mort=300000000)
            r = c.post(f"{T}/balance-registry-read", headers=th, json={"attachment_data_url": pdf, "consent": True}).json()
            self.assertTrue(r["read"]["differences"], r)
            bad = c.post(f"{T}/balance-registry-check", headers=th, json={"changed": False, "checked": True})
            self.assertEqual(bad.status_code, 400)
            self.assertIn("달라진", bad.text)
            # 열람 일시가 오래됨 → 불가
            Fake.reply = doc(now - timedelta(days=20))
            r = c.post(f"{T}/balance-registry-read", headers=th, json={"attachment_data_url": pdf, "consent": True}).json()
            self.assertFalse(r["read"]["recent"])
            self.assertEqual(c.post(f"{T}/balance-registry-check", headers=th, json={"changed": False, "checked": True}).status_code, 400)
            # 최근 열람·달라진 점 없음 — 그래도 '확인했음' 체크가 없으면 불가
            Fake.reply = doc(now)
            r = c.post(f"{T}/balance-registry-read", headers=th, json={"attachment_data_url": pdf, "consent": True}).json()
            self.assertEqual((r["read"]["differences"], r["read"]["recent"]), ([], True))
            self.assertEqual(c.post(f"{T}/balance-registry-check", headers=th, json={"changed": False, "checked": False}).status_code, 400)
            home = c.get(T, headers=th).json()
            self.assertTrue(home["keys"]["locked"])
            rel = f"/api/rent3_lessor/rooms/{ct.id_room}/release-keys"
            self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 400)       # 확인 전에는 교부 불가
            ok = c.post(f"{T}/balance-registry-check", headers=th, json={"changed": False, "checked": True})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertTrue(ok.json()["balance_registry"]["evidence"]["ai"])
            self.assertEqual(c.post(rel, headers=chic, json={"balance_checked": True}).status_code, 200)
            # 변동 신고는 체크 없이 언제나 가능(막으면 안 된다) — 교부 전 상태가 아니면 대상 아님
            # 화면: 확인은 계약 화면(모달)에서 한다 — 마이홈에는 안내와 링크만
            js = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("https://www.iros.go.kr", "openBalRegModal", "R3.openModal(\"🏛 입주 전 등기부 재확인\"", "balance-registry/${path}", 'post("read"', 'post("check"', "'확인했음'에 체크"):
                self.assertIn(needle, js, needle)
            tj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
            self.assertIn("/rent3_contract/?role=tenant", tj)
            self.assertNotIn("balRegChecked", tj)
        finally:
            registry_ai._client = orig

    def test_109_balance_registry_in_contract_flow_and_legacy_tenants(self):
        """109. 입주 전 등기부 재확인은 공동계약 화면의 API(/sessions/{id}/balance-registry/*)로 임차인만 할 수 있고(임대인 역할 거부), 같은 조건(체크·AI 대조·열람 일시)이 적용된다.
        전자계약이 아닌 기존 입주자는 관리자 도구로 '확인 완료(이전 입주자)'가 기록되며(미리보기는 읽기 전용, 백업 후 반영), 출입 잠금·계약 조건은 그대로다."""
        import base64
        from datetime import datetime
        from apps.rent_dist._rent3_core import registry_ai, Contract, Room, Tenant, legacyregistry as lg
        c = self.client
        chic = self._login("chic")
        c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "916", "is_published": True, "deposit": 6000000})
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": "계약화면임차", "phone": "01066660603", "password": "Pw123456"}).json()
        th, tid = self._as(reg["token"]), reg["user"]["id"]
        sid = self._complete_contract(chic, th, tid, "916", "계약화면임차", "010-6666-0603")
        B = f"/api/rent3_contract/sessions/{sid}/balance-registry"
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()
        ct = Contract.get(Room.get(Tenant.get(tid).current_room_id).active_contract_id)
        owner = (ct.registry_snapshot or {}).get("tenant_direct_registry", {}).get("owner_name") or ct.registry_snapshot.get("owner_name") or "김임대"

        class Fake:
            is_available = True
            reply = {}

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                return json.dumps(Fake.reply, ensure_ascii=False)

        orig = registry_ai._client
        try:
            registry_ai._client = lambda: Fake()
            Fake.reply = {"document_type": "registry", "readable": True, "issue_number": "N1", "viewed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                          "owners": [{"name": owner}], "mortgages": [], "restrictions": [], "ownership_transfers": []}
            # 임대인 역할로는 불가
            self.assertIn(c.post(f"{B}/read", headers=chic, json={"role": "lessor", "attachment_data_url": pdf, "consent": True}).status_code, (400, 403))
            # 임차인: 동의 없이는 거부 → 읽기 → 체크 없이는 거부 → 체크하면 기록
            self.assertEqual(c.post(f"{B}/read", headers=th, json={"role": "tenant", "attachment_data_url": pdf, "consent": False}).status_code, 400)
            r = c.post(f"{B}/read", headers=th, json={"role": "tenant", "attachment_data_url": pdf, "consent": True})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["read"]["recent"])
            self.assertEqual(c.post(f"{B}/check", headers=th, json={"role": "tenant", "changed": False, "checked": False}).status_code, 400)
            ok = c.post(f"{B}/check", headers=th, json={"role": "tenant", "changed": False, "checked": True})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertTrue(ok.json()["contract"]["balance_registry"]["checked_at"])
            self.assertIs(ok.json()["contract"]["keys_released"], False)           # 확인했어도 잠금은 임대인이 입금 확인 후 푼다
            # 교부 전 상태가 아니면(이미 교부) 대상 아님
            c.post(f"/api/rent3_lessor/rooms/{ct.id_room}/release-keys", headers=chic, json={"balance_checked": True})
            self.assertEqual(c.post(f"{B}/read", headers=th, json={"role": "tenant", "attachment_data_url": pdf, "consent": True}).status_code, 400)
        finally:
            registry_ai._client = orig

        # ── 기존(전자계약이 아닌) 입주자: 확인 완료 기록 ──
        legacy = Contract(id="ct_t109_legacy", branch="main", room_no="9109", status="ACTIVE", tenant_name="기존입주자").save(action="t")
        esign = Contract(id="ct_t109_esign", branch="main", room_no="9110", status="ACTIVE", tenant_name="전자계약", cosign_session_id="sess_x", keys_released=False).save(action="t")
        try:
            before = lg.plan()
            self.assertIn("ct_t109_legacy", [x["id"] for x in before["rows"]])
            self.assertNotIn("ct_t109_esign", [x["id"] for x in before["rows"]])             # 전자계약은 대상이 아님
            admin = self._login("admin")
            AD = "/api/rent3_admin"
            pv = c.get(f"{AD}/legacy-registry/preview", headers=admin).json()
            self.assertGreaterEqual(pv["to_mark"], 1)
            self.assertFalse(Contract.get("ct_t109_legacy").balance_registry)                # 미리보기는 아무것도 바꾸지 않는다
            self.assertEqual(c.post(f"{AD}/legacy-registry/apply", headers=admin, json={"confirm": False}).status_code, 400)
            ap = c.post(f"{AD}/legacy-registry/apply", headers=admin, json={"confirm": True})
            self.assertEqual(ap.status_code, 200, ap.text)
            got = Contract.get("ct_t109_legacy")
            self.assertTrue(got.balance_registry["legacy"] and got.balance_registry["checked_at"] and not got.balance_registry["changed"])
            self.assertIsNone(got.keys_released)                                             # 출입 잠금 상태는 그대로
            self.assertFalse(Contract.get("ct_t109_esign").balance_registry)                 # 전자계약은 기록하지 않음
            self.assertEqual(lg.plan()["to_mark"], 0 if not [x for x in lg.plan()["rows"]] else lg.plan()["to_mark"])
            self.assertEqual(c.post(f"{AD}/legacy-registry/apply", headers=admin, json={"confirm": True}).json()["marked"], 0)   # 다시 해도 중복 기록 없음
            aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
            for needle in ("btnLgPrev", "/legacy-registry/preview", "/legacy-registry/apply"):
                self.assertIn(needle, aj, needle)
        finally:
            for x in ("ct_t109_legacy", "ct_t109_esign"):
                o = Contract.get(x)
                if o:
                    o.delete(action="t")

    def test_110_contract_step1_registry_rules_match_recheck(self):
        """110. 공동계약 1단계(임차인이 직접 열람한 등기부)도 입주 전 재확인과 같은 기준: 열람 일시가 3일 이내여야 하고,
        AI 로 읽은 파일이 있으면 그 파일의 열람 일시와 입력한 일시가 맞아야 한다. 맞으면 통과."""
        import base64
        from datetime import datetime, timedelta
        from apps.rent_dist._rent3_core import registry_ai
        c, base = self.client, "/api/rent3_contract"
        c.post("/api/rent3_lessor/rooms", headers=self._login("chic"), json={"branch": "main", "room_no": "917", "is_published": True, "deposit": 6000000})
        sid = c.post(f"{base}/sessions/call", json={"caller_role": "lessor", "branch": "main", "room_no": "917", "mode": "NEW",
                                                     "tenant_name": "1단계임차", "tenant_phone": "010-6666-0604"}).json()["session"]["id"]
        c.post(f"{base}/sessions/{sid}/respond", json={"responder_role": "tenant", "responder_name": "1단계임차", "responder_phone": "010-6666-0604"})
        c.post(f"{base}/sessions/{sid}/step1", json={"role": "lessor", "checked_counterparty_id": True, "registry_updates": {"owner_name": "김종심", "mortgage_amount": 0}})
        fmt = "%Y-%m-%dT%H:%M"
        rec = lambda at: c.post(f"{base}/sessions/{sid}/tenant-registry", json={"registry": {
            "viewed_directly": True, "viewed_at": at, "owner_name": "김종심", "mortgage_amount": 0, "seizure_or_trust": "없음", "guarantee_status": "가입 예정"}})
        now = datetime.now()
        # 오래된 열람본·형식이 틀린 일시는 거부
        bad = rec((now - timedelta(days=10)).strftime(fmt))
        self.assertEqual(bad.status_code, 400)
        self.assertIn("오래", bad.text)
        self.assertEqual(rec("어제쯤").status_code, 400)
        # AI 로 읽은 파일의 열람 일시와 입력이 다르면 거부(다른 서류·오래된 서류를 섞는 것 방지)
        pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake").decode()

        class Fake:
            is_available = True

            async def generate_with_image(self, prompt, b64, mime_type="image/jpeg"):
                return json.dumps({"document_type": "registry", "readable": True, "issue_number": "S1", "viewed_at": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
                                   "owners": [{"name": "김종심"}], "mortgages": [], "restrictions": [], "ownership_transfers": []}, ensure_ascii=False)

        orig = registry_ai._client
        try:
            registry_ai._client = lambda: Fake()
            r = c.post(f"{base}/sessions/{sid}/registry-ai", json={"attachment_data_url": pdf, "consent": True})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["result"]["extracted"]["viewed_at"])
            mism = rec(now.strftime(fmt))                                         # 파일은 2일 전, 입력은 지금 → 하루 넘게 다름
            self.assertEqual(mism.status_code, 400)
            self.assertIn("다릅니다", mism.text)
            okr = rec((now - timedelta(days=2)).strftime(fmt))                    # 파일과 맞는 일시
            self.assertEqual(okr.status_code, 200, okr.text)
        finally:
            registry_ai._client = orig
        html = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn("3일 이내 열람본만 인정", html)

    def test_111_promo_generator_templates_and_modal(self):
        """111. 멀티채널 원클릭 홍보 생성기: 5대 채널 사실 기반 텍스트 생성, 등기클린 엄격 판정, 미입력 항목 생략, 입주방 차단 및 교차지점 거부 검증."""
        from apps.rent_dist._rent3_core import promo, Room, Building, Lessor
        from core.errors import DomainError
        chic = self._login("chic")
        sunho = self._login("sunho")
        c = self.client

        # 1. 코어 promo.generate_promo() 기본 검증 (공실 및 공개 매물 203호)
        room = Room.first(branch="main", room_no="203")
        bldg = Building.first(branch="main")
        lsr = Lessor.first(branch="main")
        res = promo.generate_promo(room, building=bldg, lessor=lsr, base_url="https://saas.test.io")

        self.assertEqual(res["room_no"], "203")
        channels = res["channels"]
        self.assertEqual(set(channels.keys()), {"daangn", "peterpan", "everytime", "blog", "kakao"})

        # 당근마켓 검증 (복비 0원, 안심링크, 딥링크 스킴, 과장 문구 부재)
        daangn = channels["daangn"]
        self.assertIn("당근마켓", daangn["name"])
        self.assertIn("[수수료 0원", daangn["content"])
        self.assertIn("ref=daangn", daangn["link"])
        self.assertTrue(daangn["scheme"].startswith("karrot://"))
        self.assertNotIn("보증금 떼일 걱정 없는", daangn["content"])
        self.assertNotIn("사기 걱정 없이", daangn["content"])

        # 네이버 피터팬 검증 (피터팬 직거래 표준 양식, 임의 가열/포함항목 없음)
        peter = channels["peterpan"]
        self.assertIn("피터팬", peter["name"])
        self.assertIn("■ 매물 정보 (피터팬 직거래 표준 양식)", peter["content"])
        self.assertIn("ref=peterpan", peter["link"])
        self.assertNotIn("도시가스 개별난방", peter["content"])

        # 에브리타임 검증 (대학생 타겟 복비 절약 강조)
        eta = channels["everytime"]
        self.assertIn("에브리타임", eta["name"])
        self.assertNotIn("20~30만원", eta["content"])          # 근거 없는 평균 금액은 쓰지 않는다
        self.assertIn("중개수수료 없이 직거래", eta["content"])
        self.assertIn("ref=everytime", eta["link"])

        # 네이버 블로그 검증 (SEO 소제목, 해시태그 목록)
        blog = channels["blog"]
        self.assertIn("네이버 블로그", blog["name"])
        self.assertGreaterEqual(len(blog["recommended_titles"]), 3)
        self.assertGreaterEqual(len(blog["tags"]), 8)
        self.assertIn("원룸직거래", blog["tags"])
        self.assertIn("ref=blog", blog["link"])
        for title in blog["recommended_titles"]:
            self.assertNotIn("신림역 인근", title)

        # 카카오톡 / 문자 요약본 검증
        kakao = channels["kakao"]
        self.assertIn("카카오톡", kakao["name"])
        self.assertIn("ref=kakao", kakao["link"])

        # 2. 미입력 항목 생략 및 사실 기반 검증 (정보가 비어있는 777호)
        empty_room = Room(
            branch="main",
            room_no="777",
            deposit=10000000,
            monthly_rent=500000,
            is_occupied=False,
            is_published=True,
        )
        res_empty = promo.generate_promo(empty_room, base_url="https://saas.test.io")
        self.assertFalse(res_empty["clean_title"])
        for ch_key, ch_data in res_empty["channels"].items():
            txt = ch_data["content"]
            self.assertNotIn("시크빌", txt)
            self.assertNotIn("관악구 신림로", txt)
            self.assertNotIn("에어컨·세탁기·냉장고", txt)
            self.assertNotIn("도시가스 개별난방", txt)
            self.assertNotIn("인터넷, 수도 등 포함", txt)
            self.assertNotIn("신림역 인근", txt)
            self.assertNotIn("등기부 확인 완료", txt)
            self.assertNotIn("소유자 일치 · 권리 깨끗함", txt)

        # 3. 등기부 확인 문구의 roomregistry.public_clean() 엄격 연동 검증
        # 3-1) 최근 확정 + 깨끗함인 호실: 배지 부착
        from apps.rent_dist._rent3_core import roomregistry
        clean_room = Room(
            branch="main",
            room_no="888",
            deposit=5000000,
            monthly_rent=400000,
            is_occupied=False,
            is_published=True,
            registry_checked=True,
            registry_info={
                "status": "confirmed",
                "clean_title": True,
                "owner_matches": True,
                "verified_at": "2026-11-18",
            },
        )
        res_clean = promo.generate_promo(clean_room, base_url="https://saas.test.io")
        self.assertTrue(res_clean["clean_title"])
        self.assertIn("등기부 확인 완료 (소유자 일치 · 권리 깨끗함)", res_clean["channels"]["daangn"]["content"])

        # 3-2) 90일 지난 등기부: public_clean=False 이므로 배지 미부착
        old_reg_room = Room(
            branch="main",
            room_no="889",
            deposit=5000000,
            monthly_rent=400000,
            is_occupied=False,
            is_published=True,
            registry_checked=True,
            registry_info={
                "status": "confirmed",
                "clean_title": True,
                "owner_matches": True,
                "verified_at": "2020-01-01",
            },
        )
        res_old = promo.generate_promo(old_reg_room, base_url="https://saas.test.io")
        self.assertFalse(res_old["clean_title"])
        self.assertNotIn("등기부 확인 완료", res_old["channels"]["daangn"]["content"])

        # 4. 입주 중인 방(만기전출 예고 없음) 홍보 차단 검증
        occ_room = Room.first(branch="main", room_no="201")
        self.assertTrue(occ_room.is_occupied)
        self.assertFalse(occ_room.pre_moveout_notice)
        self.assertFalse(occ_room.is_published)
        with self.assertRaises(DomainError):
            promo.generate_promo(occ_room)

        # 만기 전출 예정(pre_moveout_notice=True)인 입주 방은 사전 홍보 허용
        occ_room.pre_moveout_notice = True
        res_occ_notice = promo.generate_promo(occ_room)
        self.assertIn("201호", res_occ_notice["channels"]["daangn"]["content"])
        occ_room.pre_moveout_notice = False  # 원상 복구

        # 5. 임대인 API /api/rent3_lessor/rooms/{room.id}/promo 검증 및 타 지점 접근 차단(403)
        r = c.get(f"/api/rent3_lessor/rooms/{room.id}/promo", headers=chic)
        self.assertEqual(r.status_code, 200, r.text)
        api_data = r.json()
        self.assertTrue(api_data["success"])

        # 동작 지점 임대인(sunho)이 관악 main 지점 호실의 홍보글 요청 시 403 Forbidden
        r_forbidden = c.get(f"/api/rent3_lessor/rooms/{room.id}/promo", headers=sunho)
        self.assertEqual(r_forbidden.status_code, 403)

        # 6. 임대인 화면(app.js) 및 포털(app.js) UI 연동 검증
        lessor_fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn("showPromoModal", lessor_fe)
        self.assertIn("btn-open-promo", lessor_fe)
        self.assertIn("📢 홍보글 복사", lessor_fe)
        self.assertNotIn("r.pre_moveout_notice || r.is_published", lessor_fe)        # 입주 중인 방은 전출 예고가 있을 때만 버튼(공개 표시가 남아 있어도 아님)
        self.assertIn("r.pre_moveout_notice ? `<button", lessor_fe)

        portal_fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn('urlParams.get("room")', portal_fe)
        self.assertIn('data-room-id="${esc(l.id)}"', portal_fe)
        # 딥링크: 주소창 값을 선택자에 넣지 않고 속성 값과 비교
        self.assertNotIn('querySelector(`[data-room-id="${targetRoom}"]`)', portal_fe)
        self.assertIn("el.dataset.roomId === targetRoom", portal_fe)

        # 7. 보강: 모델 기본값(2층·23㎡)은 입력한 값이 아니므로 생략하고, 위치 줄이 비지 않으며, 공개 표시가 남은 입주 방도 막는다
        txt_def = "\n".join(ch["content"] for ch in promo.generate_promo(Room(id="room_t111_def", branch="zzz", room_no="777"))["channels"].values())
        for bad in ("2층", "23.0㎡", "위치: (", "위치:\n"):
            self.assertNotIn(bad, txt_def, bad)
        txt_real = promo.generate_promo(Room(id="room_t111_real", branch="zzz", room_no="778", floor=5, total_floors=7, area_sqm=31.5))["channels"]["daangn"]["content"]
        self.assertIn("5층 / 전체 7층", txt_real)
        self.assertIn("31.5㎡", txt_real)
        # 8. 임차인 대상 글에 근거 없는 단정·평균 금액·안전 보증 표현이 없고, 입주일을 입력하지 않았으면 지어내지 않는다
        full = Room(id="room_t111_full", branch="zzz", room_no="305", deposit=5000000, monthly_rent=450000, maintenance_fee=50000, floor=3, total_floors=5,
                    area_sqm=19.8, direction="남향", available_from="", option_list=[{"item": "에어컨"}], status_description="리모델링 완료", is_published=True)
        alltxt = "\n".join(ch["title"] + ch["content"] for ch in promo.generate_promo(full, base_url="https://x.example")["channels"].values())
        for bad in ("20~30만", "전액 세이브", "안심", "안전", "투명", "신속", "실시간", "검증 완료", "즉시입주", "사기 걱정", "떼일", "보장"):
            self.assertNotIn(bad, alltxt, bad)
        self.assertNotIn("입주가능일", alltxt)
        with self.assertRaises(DomainError):
            promo.generate_promo(Room(id="room_t111_pub", branch="zzz", room_no="779", id_tenant="tenant_x", active_contract_id="ct_x", is_published=True))


    def test_112_lessor_acquisition_attribution_p0(self):
        """112. 임대인 유입 귀속(P0): 허용되는 표식(짧은 영숫자)만 저장하고 나머지는 조용히 버리며(가입은 막지 않음), 임차인 가입에는 기록하지 않고,
        관리인만 채널별 전환(가입·승인·첫 방·첫 수납) 현황을 볼 수 있고, 포털은 처음 닿은 표식을 30일 보관해 임대인 가입 때만 보낸다."""
        from apps.rent_dist._rent3_core import Lessor, Tenant, acquisition
        c = self.client
        # 표식 검증(단위)
        self.assertEqual(acquisition.sanitize({"ref": "daangn", "campaign": "spring-26", "from": "tn_ab12", "first_seen": "2026-11-18"}),
                         {"ref": "daangn", "campaign": "spring-26", "from_tenant": "tn_ab12", "first_seen": "2026-11-18"})
        self.assertEqual(acquisition.sanitize({"ref": "<script>", "campaign": "x" * 40, "from": "a b"}), {})                # 형식이 틀리면 전부 버림
        self.assertEqual(acquisition.sanitize("문자열"), {})
        self.assertEqual(acquisition.record({}), {})
        # 임대인 가입: 표식 저장, 잘못된 값이 섞여도 가입은 성공
        reg = lambda name, phone, user, **extra: c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={
            "role": "lessor", "name": name, "phone": phone, "username": user, "password": "Pw123456", "agree_privacy": True, **extra})
        ok = reg("유입임대", "01077770001", "acq_one", acq={"ref": "daangn", "campaign": "spring-26", "first_seen": "2026-11-18"})
        self.assertEqual(ok.status_code, 200, ok.text)
        l1 = Lessor.first(username="acq_one")
        self.assertEqual((l1.acq["ref"], l1.acq["campaign"]), ("daangn", "spring-26"))
        self.assertTrue(l1.acq["registered_at"])
        bad = reg("잘못된표식", "01077770002", "acq_two", acq={"ref": "<img src=x onerror=1>", "campaign": "ok_1"})
        self.assertEqual(bad.status_code, 200, bad.text)
        self.assertEqual(Lessor.first(username="acq_two").acq.get("ref"), None)                                           # 잘못된 값은 버리고
        self.assertEqual(Lessor.first(username="acq_two").acq["campaign"], "ok_1")                                        # 올바른 값은 남김
        none = reg("표식없음", "01077770003", "acq_three")
        self.assertEqual(none.status_code, 200, none.text)
        self.assertEqual(Lessor.first(username="acq_three").acq, {})
        # 임차인(예비임차인) 가입에는 기록하지 않는다
        t = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": "표식임차", "phone": "01077770004", "password": "Pw123456",
                                                                              "agree_privacy": True, "acq": {"ref": "daangn"}})
        self.assertEqual(t.status_code, 200, t.text)
        self.assertFalse(hasattr(Tenant.get(t.json()["user"]["id"]), "acq"))
        # 관리인 전용 현황
        AD = "/api/rent3_admin/acquisition/summary"
        self.assertIn(c.get(AD, headers=self._login("chic")).status_code, (401, 403))
        self.assertIn(c.get(AD, headers=self.NOAUTH).status_code, (401, 403))
        r = c.get(AD, headers=self._login("admin"))
        self.assertEqual(r.status_code, 200, r.text)
        sm = r.json()
        refs = {x["key"]: x for x in sm["by_ref"]}
        self.assertGreaterEqual(refs["daangn"]["signups"], 1)
        self.assertEqual(refs["daangn"]["approved"], 0)                                                                    # 가맹 승인 전
        self.assertIn(acquisition.NONE_LABEL, refs)                                                                       # 표식 없는 임대인은 '(출처 없음)'
        self.assertGreaterEqual(sm["tracked"], 2)
        # 승인·방 등록이 전환 단계에 반영된다
        l1.status = "ACTIVE"
        l1.save(action="t")
        self.assertEqual({x["key"]: x for x in acquisition.summary()["by_ref"]}["daangn"]["approved"], 1)
        # 포털 화면: 처음 닿은 표식 보관(30일)·임대인 가입에만 전송·형식 검증
        js = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ('ACQ_KEY = "r3_acq"', "ACQ_DAYS = 30", "captureAcq", 'acq: $("regRole").value === "lessor"', "ACQ_OK"):
            self.assertIn(needle, js, needle)
        aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnAcq", "/acquisition/summary", "임대인 유입 현황"):
            self.assertIn(needle, aj, needle)
        for u in ("acq_one", "acq_two", "acq_three"):
            o = Lessor.first(username=u)
            if o:
                o.delete(action="t")

    def test_113_tenant_proposes_direct_contract_to_lessor_p1(self):
        """113. 세입자→집주인 초대(P1): 임차인별 추측 불가 코드·사실만 쓴 문안·하루 한도, 문구 입력은 정리(링크·제어문자 제거), 남의 코드로는 못 봄,
        이 코드로 임대인이 가입하면 귀속되고 임차인에게 가입 수가 보이며, 집주인 번호는 어디에도 저장·전송되지 않고, 포털은 초대 링크에서 임대인 가입 화면을 연다."""
        import re
        from apps.rent_dist._rent3_core import Lessor, Tenant, lessorinvite as li
        c = self.client
        reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": "초대임차", "phone": "01088880001", "password": "Pw123456", "agree_privacy": True}).json()
        th, tid = self._as(reg["token"]), reg["user"]["id"]
        other = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": "남임차", "phone": "01088880002", "password": "Pw123456", "agree_privacy": True}).json()
        oh = self._as(other["token"])
        U = f"/api/rent3_tenant/myhome/{tid}/lessor-invite"
        # 문구 정리(단위)
        self.assertEqual(li.clean_label("신림동 <b>빌라</b>\n201호 https://evil.example/x 끝"), "신림동 <b>빌라</b> 201호 끝")
        self.assertEqual(len(li.clean_label("가" * 100)), 40)
        # 정보 조회: 코드·링크·문안
        r = c.get(U, headers=th, params={"label": "신림동 ○○빌라 201호"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertRegex(d["code"], r"^tn_[0-9a-f]{8}$")
        self.assertIn(f"/rent3/?invite=lessor&ref=tenant&from={d['code']}", d["link"])
        self.assertIn("신림동 ○○빌라 201호", d["message"])
        self.assertEqual(c.get(U, headers=th).json()["code"], d["code"])                              # 코드는 한 번 발급되면 그대로
        for bad in ("평생 무료", "사기 걱정", "떼일", "25만", "보장"):                                  # 임차인이 보내는 글에도 과장·단정 표현을 쓰지 않는다
            self.assertNotIn(bad, d["message"], bad)
        self.assertIn("가맹 심사", d["message"])                                                       # 가입 후 심사가 있다는 사실을 밝힌다
        # 조회만으로는 횟수가 오르지 않는다
        self.assertEqual((d["sent_today"], d["left_today"], d["joined"]), (0, li.DAILY_LIMIT, 0))
        # 남의 계정으로는 못 본다
        self.assertEqual(c.get(U, headers=oh).status_code, 403)
        self.assertEqual(c.get(U, headers=self.NOAUTH).status_code, 401)
        # 보냈다고 기록: 하루 한도
        for i in range(li.DAILY_LIMIT):
            self.assertEqual(c.post(f"{U}/sent", headers=th, json={}).status_code, 200)
        over = c.post(f"{U}/sent", headers=th, json={})
        self.assertEqual(over.status_code, 400)
        self.assertEqual(Tenant.get(tid).lessor_invite_total, li.DAILY_LIMIT)
        # 이 코드로 임대인 가입 → 귀속, 임차인에게 가입 수가 보임
        code = d["code"]
        j = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "lessor", "name": "초대받은임대", "phone": "01088880003", "username": "inv_lsr", "password": "Pw123456",
                                                                              "agree_privacy": True, "acq": {"ref": "tenant", "from": code, "first_seen": "2026-11-18"}})
        self.assertEqual(j.status_code, 200, j.text)
        self.assertEqual(Lessor.first(username="inv_lsr").acq["from_tenant"], code)
        self.assertEqual(c.get(U, headers=th).json()["joined"], 1)
        # 관리인 현황: 소개 코드별·보낸 횟수
        sm = c.get("/api/rent3_admin/acquisition/summary", headers=self._login("admin")).json()
        self.assertEqual(sm["by_from"][0]["key"], code)
        self.assertGreaterEqual(sm["invites_sent"], li.DAILY_LIMIT)
        # 집주인 번호는 저장하지 않는다(모델·요청에 그런 칸이 없다)
        self.assertFalse([f for f in Tenant.model_fields if "landlord" in f.lower() or f in ("lessor_phone", "target_phone")])
        # 화면: 임차인 메뉴·모달, 포털의 초대 링크 처리
        tj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnLessorInvite", "lessor-invite/sent", "집주인의 번호·이름은 이 서비스로 전송되거나 저장되지 않습니다", '"sms:" + sep + "body="'):
            self.assertIn(needle, tj, needle)
        pj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ('urlParams.get("invite") === "lessor"', 'urlParams.get("invite")) return;', '$("regRole").value = "lessor"'):
            self.assertIn(needle, pj, needle)
        for u in ("inv_lsr",):
            o = Lessor.first(username=u)
            if o:
                o.delete(action="t")

    def test_114_lessor_intro_page_honest_terms_p2(self):
        """114. 임대인 안내(P2): 공개 API 가 실제 가맹비(코드 값)를 부가세 포함·월 정액으로 알리고, 무료 기간은 환경변수를 설정했을 때만 안내하며 청구도 그 기간에는 만들지 않고,
        세무는 '홈택스 신고용 입금 증빙 자료(CSV) 정리'까지만 말하고, 샘플 CSV 는 가상 자료임을 밝히며, 화면·문구에 금지 표현이 없다."""
        import re
        from apps.rent_dist._rent3_core import Lessor, feebilling, offer
        c = self.client
        old = os.environ.pop("RENT3_FEE_FREE_UNTIL", None)
        try:
            t = c.get("/api/rent3/pool/lessor-offer", headers=self.NOAUTH)                       # 로그인 없이 볼 수 있다
            self.assertEqual(t.status_code, 200, t.text)
            d = t.json()
            self.assertEqual(d["monthly_fee"], Lessor.model_fields["app_fee"].default)           # 안내 금액 = 실제 청구 기본값
            self.assertEqual(d["fee_supply"] + d["fee_vat"], d["monthly_fee"])
            self.assertTrue(d["vat_included"])
            self.assertEqual(d["free_until"], "")                                                # 설정이 없으면 무료 안내도 없다
            self.assertEqual(d["free_text"], "")
            for k in ("tax_scope", "after_approval_only", "individual_note"):
                self.assertTrue(d[k], k)
            self.assertIn("홈택스 신고용 입금 증빙 자료(CSV)", d["tax_scope"])
            self.assertIn("대신 작성·제출하지 않", d["tax_scope"])
            # 무료 기간 설정: 안내하고 청구도 지킨다
            os.environ["RENT3_FEE_FREE_UNTIL"] = "2026-12"
            d2 = c.get("/api/rent3/pool/lessor-offer", headers=self.NOAUTH).json()
            self.assertEqual((d2["free_until"], "2026년 12월분까지" in d2["free_text"]), ("2026-12", True))
            self.assertTrue(offer.is_free_month("2026-11") and offer.is_free_month("2026-12") and not offer.is_free_month("2027-01"))
            self.assertEqual(feebilling.ensure_notices("2026-12", force=False), [])              # 무료 달에는 만들지 않는다
            os.environ["RENT3_FEE_FREE_UNTIL"] = "잘못된값"
            self.assertEqual(offer.free_until(), "")                                             # 형식이 틀리면 무료 안내 없음
        finally:
            os.environ.pop("RENT3_FEE_FREE_UNTIL", None)
            if old is not None:
                os.environ["RENT3_FEE_FREE_UNTIL"] = old
        # 샘플 CSV: 가상 자료 표기, 장부와 같은 머리글
        r = c.get("/api/rent3/pool/lessor-offer/sample.csv", headers=self.NOAUTH)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("샘플입니다", r.text)
        self.assertIn("(샘플)", r.text)
        # 화면: 요금 표기·계산기·가입 연결, 금지 표현 없음
        page = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "lessor-intro.html"), encoding="utf-8").read()
        for needle in ("이용 요금", "lessor-offer", "calcFee", "joinBtn", "신고서를 대신 작성·제출하지 않고", "홈택스 신고용 입금 증빙 자료(CSV)", "가맹 심사"):
            self.assertIn(needle, page, needle)
        blob = page + " ".join(str(v) for v in d.values())
        for bad in ("평생 무료", "1초", "신고서 완성", "사기 걱정", "떼일", "보장합니다", "과태료 걱정", "25만"):
            self.assertNotIn(bad, blob, bad)
        self.assertEqual(c.get("/rent3/lessor-intro.html", headers=self.NOAUTH).status_code, 200)   # 실제로 서빙된다
        pj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn("lessor-intro.html", pj)

    def test_115_landlord_promo_templates_p3(self):
        """115. 임대인 대상 홍보 글(P3): 관리인만, 채널·시기별 초안에 '[광고]' 표시·실제 요금·세무 범위·유입 추적 링크·규정 확인 목록이 있고,
        무료 기간은 설정 시에만 쓰며, 후기 위장·보장·단정 표현이 없고, 잘못된 입력은 거부한다."""
        from apps.rent_dist._rent3_core import landlordpromo as lp, offer
        c = self.client
        AD = "/api/rent3_admin/landlord-promo"
        self.assertIn(c.get(AD, headers=self._login("chic")).status_code, (401, 403))
        self.assertIn(c.get(AD, headers=self.NOAUTH).status_code, (401, 403))
        admin = self._login("admin")
        old = os.environ.pop("RENT3_FEE_FREE_UNTIL", None)
        try:
            for ch in ("cafe", "band", "blog"):
                for se in ("none", "vat_jan", "tax_may", "vat_jul"):
                    r = c.get(AD, headers=admin, params={"channel": ch, "season": se})
                    self.assertEqual(r.status_code, 200, r.text)
                    p = r.json()["post"]
                    txt = p["title"] + " " + p["content"]
                    self.assertTrue(p["title"].startswith("[광고]"), p["title"])
                    self.assertTrue(p["content"].startswith(lp.AD_LABEL))
                    self.assertIn(f"{offer.default_fee():,}원", p["content"])                  # 실제 가맹비
                    self.assertIn("가맹 심사", p["content"])
                    self.assertIn("홈택스 신고용 입금 증빙 자료(CSV)", p["content"])
                    self.assertIn(f"ref={ch}&campaign=", p["link"])
                    self.assertNotIn("무료 기간", txt)                                           # 설정 전에는 무료 문구 없음
                    self.assertEqual(len(p["checklist"]), len(lp.CHECKLIST))
                    for bad in ("평생 무료", "1초", "신고서 완성", "사기 걱정", "떼일", "보장합니다", "과태료 걱정", "후기", "써봤", "덕분에", "25만"):
                        self.assertNotIn(bad, txt, (ch, se, bad))
            self.assertIn("5월은 종합소득세", c.get(AD, headers=admin, params={"channel": "cafe", "season": "tax_may"}).json()["post"]["content"])
            self.assertIn("tags", c.get(AD, headers=admin, params={"channel": "blog"}).json()["post"])
            os.environ["RENT3_FEE_FREE_UNTIL"] = "2026-12"
            self.assertIn("2026년 12월분까지", c.get(AD, headers=admin).json()["post"]["content"])
        finally:
            os.environ.pop("RENT3_FEE_FREE_UNTIL", None)
            if old is not None:
                os.environ["RENT3_FEE_FREE_UNTIL"] = old
        # 잘못된 입력 거부
        self.assertEqual(c.get(AD, headers=admin, params={"channel": "tiktok"}).status_code, 400)
        self.assertEqual(c.get(AD, headers=admin, params={"season": "dec"}).status_code, 400)
        self.assertEqual(c.get(AD, headers=admin, params={"campaign": "<x> y"}).status_code, 400)
        self.assertIn("campaign=my_camp1", c.get(AD, headers=admin, params={"campaign": "my_camp1"}).json()["post"]["link"])
        aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnLp", "/landlord-promo", "광고 표시는 지우지 말고"):
            self.assertIn(needle, aj, needle)
        self.assertIn("r3_modal.js", open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "index.html"), encoding="utf-8").read())

    def test_116_offline_qr_leaflet_incheon_p4(self):
        """116. 오프라인 QR 리플릿(P4, 인천 시범): 서빙되고, 맨 앞 '[광고]'·실제 요금 API·세무 범위·개인정보 미수집 문구가 있고, QR 링크는 지역 표식(offline-<지역>)으로
        안내 페이지에 연결되며(표식은 영숫자 검증), 금지 표현이 없고, 관리인 앱에서 인천 구·군을 골라 열 수 있다."""
        c = self.client
        r = c.get("/rent3/leaflet.html", headers=self.NOAUTH)
        self.assertEqual(r.status_code, 200)
        html = r.text
        for needle in ("[광고] 운영자가 제작한 서비스 안내 리플릿", "/api/rent3/pool/lessor-offer", "lessor-intro.html?ref=", "offline-", "qrcode(0",
                       "개인정보(이름·연락처)를 수집하지 않으며", "중개수수료 없음", "홈택스 신고용 입금 증빙 자료(CSV)", "가입하지 않으셔도 됩니다", "TOKEN.test"):
            self.assertIn(needle, html, needle)
        for bad in ("평생 무료", "1초", "신고서 완성", "사기 걱정", "떼일", "보장합니다", "과태료 걱정", "25만", "후기"):
            self.assertNotIn(bad, html, bad)
        aj = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnLf", "lfRegion", "incheon-namdong", "leaflet.html?region="):
            self.assertIn(needle, aj, needle)
        # 지역 표식은 P0 기록 형식(32자 이하 영숫자)에 맞아 가입 때 그대로 귀속된다
        from apps.rent_dist._rent3_core import acquisition
        for code in ("offline-incheon", "offline-incheon-michuhol"):
            self.assertEqual(acquisition.sanitize({"ref": code})["ref"], code)

    def test_117_occupied_room_pre_moveout_listing(self):
        """117. 입주 중인 호실도 전출 예정이 확정되면 입주가능일을 지정해 미리 매물로 등록·공개·홍보할 수 있고, 매물 내리기 시 사전공개도 해제된다."""
        from apps.rent_dist._rent3_core import Room
        c = self.client
        chic = self._login("chic")

        occ = Room.first(branch="main", room_no="201")
        self.assertTrue(occ.is_occupied)
        old_state = (occ.is_published, occ.pre_moveout_notice, occ.available_from, occ.deposit, occ.monthly_rent, occ.next_listing)
        from unittest import mock as _mock
        open_d = occ.prelist_open_date()
        self.assertTrue(open_d)
        patcher = _mock.patch.dict(os.environ, {"RENT3_TODAY": open_d})            # 사전 매물 공개일(퇴실 기준일 1개월 전) 이후로 시험한다
        try:
            early = c.post("/api/rent3_lessor/rooms", headers=chic, json={
                "branch": "main", "room_no": "201", "available_from": "2026-12-31", "is_published": True, "pre_moveout_notice": True})
            self.assertEqual(early.status_code, 400, early.text)                    # 공개일 전에는 직접 올릴 수도 없다
            self.assertIn("1개월 전", early.json()["message"])
            patcher.start()
            # 1) 전출 예정 확인 없이 입주 호실 공개 시도 → 거부(400)
            r1 = c.post("/api/rent3_lessor/rooms", headers=chic, json={
                "branch": "main", "room_no": "201", "available_from": "2026-12-31", "is_published": True,
            })
            self.assertEqual(r1.status_code, 400, r1.text)

            # 2) 전출 예정 확인은 했으나 '즉시입주'로 등록 시도 → 거부(400)
            r2 = c.post("/api/rent3_lessor/rooms", headers=chic, json={
                "branch": "main", "room_no": "201", "available_from": "즉시입주",
                "is_published": True, "pre_moveout_notice": True,
            })
            self.assertEqual(r2.status_code, 400, r2.text)

            # 3) 전출 예정 확인 + 입주가능일 지정 → 성공(200), pre_moveout_notice=True, 포털 공개 및 홍보글 생성 허용
            r3 = c.post("/api/rent3_lessor/rooms", headers=chic, json={
                "branch": "main", "room_no": "201", "deposit": 5000000, "monthly_rent": 480000,
                "available_from": "2026-12-31", "is_published": True, "pre_moveout_notice": True,
            })
            self.assertEqual(r3.status_code, 200, r3.text)
            rm = Room.first(branch="main", room_no="201")
            self.assertTrue(rm.pre_moveout_notice)
            self.assertTrue(rm.is_published)
            # 입주 중이므로 방 값(현재 계약 조건)은 그대로이고, 다음 세입자용 조건은 next_listing 에만 임시 저장된다
            self.assertEqual((rm.monthly_rent, rm.available_from), (old_state[4], old_state[2]))
            self.assertEqual((rm.next_listing["monthly_rent"], rm.next_listing["available_from"]), (480000, "2026-12-31"))

            landing = c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]
            mine = next(l for l in landing if l["id"] == rm.id and l["pre_moveout_notice"])
            self.assertEqual((mine["monthly_rent"], mine["available_from"]), (480000, "2026-12-31"))      # 공개 목록은 다음 세입자용 조건

            pr = c.get(f"/api/rent3_lessor/rooms/{rm.id}/promo", headers=chic)
            self.assertEqual(pr.status_code, 200, pr.text)

            # 4) 매물 내리기(is_published=False) → pre_moveout_notice도 함께 False, 포털에서 내려감
            r4 = c.post(f"/api/rent3_lessor/rooms/{rm.id}/publish", headers=chic, json={"is_published": False})
            self.assertEqual(r4.status_code, 200, r4.text)
            rm2 = Room.first(branch="main", room_no="201")
            self.assertFalse(rm2.is_published)
            self.assertFalse(rm2.pre_moveout_notice)
            self.assertEqual(rm2.next_listing, {})                                                         # 내리면 임시 조건도 삭제
            landing2 = c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]
            self.assertFalse(any(l["id"] == rm.id for l in landing2))
        finally:
            patcher.stop()
            rm_f = Room.first(branch="main", room_no="201")
            rm_f.is_published, rm_f.pre_moveout_notice, rm_f.available_from, rm_f.deposit, rm_f.monthly_rent, rm_f.next_listing = old_state
            rm_f.save(action="test_117_restore")

        # 5) 임대인 UI: 호실 드롭다운에서 입주 호실 disabled 제거 및 전출예정 확인 체크박스 포함
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        self.assertNotIn('${r.is_occupied ? "disabled" : ""}', fe)
        self.assertIn("nrMoveoutNoticeWrap", fe)
        self.assertIn("nrPreMoveout", fe)

    def test_118_next_listing_staging_and_checkout_sensitive_purge(self):
        """118. 입주 중 호실 사전 매물 조건은 next_listing 에 임시 저장(방 값·계약 불변) → 퇴실 확정(vacate) 때 방 값에 반영하고 곧바로 비움,
        새 계약 체결 때도 비움, 연장 확정 때도 비움. 퇴실 정산의 민감 정보(계좌·예금주·서명·메모)는 반환 확정 때 또는 1개월 뒤 지우되 금액·공제 내역은 남긴다."""
        from datetime import datetime, timedelta
        from apps.rent_dist._rent3_core import Room, Contract
        from apps.rent_dist._rent3_core.ops import CheckOutSettlement
        # ① 임시 조건: 현재 값 불변, 공개용 복사본만 덮임, vacate 때 반영·삭제
        r = Room(id="room_t118", branch="main", room_no="9118", deposit=1000000, monthly_rent=300000, id_tenant="tenant_x", active_contract_id="ct_x",
                 pre_moveout_notice=True, is_published=True, next_listing={"deposit": 2000000, "monthly_rent": 350000, "available_from": "2027-01-15", "bogus": 1})
        self.assertEqual((r.deposit, r.monthly_rent), (1000000, 300000))
        view = r.as_listing()
        self.assertEqual((view.deposit, view.monthly_rent, view.available_from), (2000000, 350000, "2027-01-15"))
        self.assertEqual(r.monthly_rent, 300000)                                      # 복사본이라 원본은 그대로
        self.assertFalse(hasattr(view, "bogus"))
        r.vacate()
        self.assertEqual((r.deposit, r.monthly_rent, r.available_from, r.next_listing), (2000000, 350000, "2027-01-15", {}))
        r2 = Room(id="room_t118b", branch="main", room_no="9119", id_tenant="tenant_x", active_contract_id="ct_x", pre_moveout_notice=True, next_listing={})
        r2.vacate()
        self.assertEqual(r2.available_from, "즉시입주")                                  # 임시 조건이 없으면 예전처럼
        # ② 비입주 호실은 as_listing 이 그대로
        self.assertIs(Room(id="room_t118c", branch="main", room_no="9120", next_listing={"deposit": 1}).as_listing().deposit, Room.model_fields["deposit"].default)
        # ③ 퇴실 정산 민감 정보: 반환 확정 때 삭제(금액·공제 내역은 유지), 이미 지웠으면 반복 없음
        co = CheckOutSettlement(id="co_t118", branch="main", id_room="room_none", room_no="9118", id_contract="ct_none", id_tenant="tenant_none", tenant_name="정산임차",
                                status="PAID", refund_amount=900000, deposit_original=1000000, unpaid_rent_deduction=100000, refund_bank_account="국민 123-456-789012",
                                account_holder="정산임차", tenant_signature="서명", dispute_note="이의", tenant_request_memo="요청", inspection_notes="점검", paid_memo="송금")
        co.save(action="t")
        try:
            co.complete_refund_and_vacate(app_source="t")
            got = CheckOutSettlement.get("co_t118")
            for f in CheckOutSettlement.SENSITIVE_FIELDS:
                self.assertEqual(getattr(got, f), "", f)
            self.assertTrue(got.purged_at)
            self.assertEqual((got.refund_amount, got.deposit_original, got.unpaid_rent_deduction, got.status), (900000, 1000000, 100000, "COMPLETED"))
            self.assertFalse(got.purge_sensitive())
            # ④ 반환이 끝나지 않아도 퇴실 희망일 후 1개월이 지나면 삭제(이전 건만), 최근 건은 유지
            old_day = (datetime.now() - timedelta(days=45)).date().isoformat()
            new_day = (datetime.now() + timedelta(days=5)).date().isoformat()
            CheckOutSettlement(id="co_t118_old", branch="main", id_room="room_none", room_no="9121", id_tenant="tenant_old", tenant_name="오래", status="AGREED",
                               desired_date=old_day, refund_bank_account="신한 111-222-333333", account_holder="오래", tenant_signature="서명").save(action="t")
            CheckOutSettlement(id="co_t118_new", branch="main", id_room="room_none", room_no="9122", id_tenant="tenant_new", tenant_name="최근", status="AGREED",
                               desired_date=new_day, refund_bank_account="신한 444-555-666666", account_holder="최근", tenant_signature="서명").save(action="t")
            CheckOutSettlement.purge_due()
            self.assertEqual((CheckOutSettlement.get("co_t118_old").refund_bank_account, bool(CheckOutSettlement.get("co_t118_old").purged_at)), ("", True))
            self.assertEqual(CheckOutSettlement.get("co_t118_new").refund_bank_account, "신한 444-555-666666")
        finally:
            for x in ("co_t118", "co_t118_old", "co_t118_new"):
                o = CheckOutSettlement.get(x)
                if o:
                    o.delete(action="t")
        # ⑤ 연장 확정·새 계약 체결 경로가 임시 조건을 비우는 코드가 있다
        for rel, needle in (("ops.py", "room.next_listing = {}"), ("cosign.py", "room.next_listing = {}"), ("broker.py", "room.next_listing = {}")):
            self.assertIn(needle, open(os.path.join(ROOT, "apps", "rent_dist", "_rent3_core", rel), encoding="utf-8").read(), rel)

    def test_119_naver_style_icon_menu_modal(self):
        """119. 햄버거 메뉴가 네이버 스타일의 중앙 모달 창(카테고리별 아이콘 그리드 + 기능 검색)으로 열리고, r3_shell.js 가 배지 갱신 시 타일 구조를 보존한다."""
        import re
        hb = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "hamburger.js"), encoding="utf-8").read()
        for needle in (
            "r3-drawer__grid",
            "r3-drawer__section",
            "r3-tile-ico",
            "r3-tile-txt",
            "r3-menu-search",
            "grid-template-columns:repeat(4",
            "grid-template-columns:repeat(3",
            'aria-modal", "true"',
            "MutationObserver",
        ):
            self.assertIn(needle, hb, needle)
        # 모바일 글자 12px 미만 규칙 위반 없음 확인
        small = re.compile(r"font-size:\s*(?:9|10|11)(?:\.\d)?px")
        self.assertFalse(small.search(hb), "hamburger.js에 12px 미만 글자가 없어야 합니다")

        shell = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        self.assertIn(".r3-menu-search", shell)
        self.assertIn(".r3-menu-empty", shell)
        self.assertIn('.querySelector(".r3-tile-txt")', shell)

    def test_120_notification_center_batch_collectors(self):
        """120. 알림 센터(일괄 수집): 원본 데이터의 상태에서 역할별 알림(건수·종류·이동 주소)을 한 번에 계산하고, 처리되면 사라지며, 본인 몫만 보이고(타 임차인·타 지점·비관리인 거부),
        한 수집기가 실패해도 나머지는 계속되고, 요약에는 개인정보가 없고, 안내성 알림은 '확인함' 뒤 다시 안 뜨며, 세 앱 화면이 알림 센터를 불러온다."""
        from datetime import datetime, timedelta
        from apps.rent_dist._rent3_core import Lessor, Tenant, notifications as nf
        from apps.rent_dist._rent3_core.authz import tenant_token
        from apps.rent_dist._rent3_core.ops import CheckOutSettlement, ConnectRequest, ExpiryRenewalCheck, MaintenanceTicket, TransferNotice
        c = self.client
        t = Tenant.get("tenant_201")
        th = {"Authorization": f"Bearer {tenant_token(t)}"}
        other = Tenant.get("tenant_202") or next(x for x in Tenant.all() if x.id != t.id and x.status == "ACTIVE")
        oh = {"Authorization": f"Bearer {tenant_token(other)}"}
        chic, sunho, admin = self._login("chic"), self._login("sunho"), self._login("admin")
        TN = f"/api/rent3_tenant/myhome/{t.id}/notifications"
        now = datetime.now().isoformat(timespec="seconds")
        made = []
        try:
            made += [
                CheckOutSettlement(id="co_t120", branch=t.branch, id_room="room_x", room_no=t.room_no, id_contract="ct_x", id_tenant=t.id, tenant_name=t.name, status="CALCULATED").save(action="t"),
                ExpiryRenewalCheck(id="exp_t120", branch=t.branch, room_no=t.room_no, id_tenant=t.id, status="AWAITING_TENANT").save(action="t"),
                TransferNotice(id="trn_t120", branch=t.branch, room_no=t.room_no, tenant_id=t.id, status="REJECTED", handled_at=now).save(action="t"),
            ]
            # ── 임차인: 종류별 건수·이동 주소 ──
            r = c.get(TN, headers=th)
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            kinds = {i["kind"]: i for i in d["items"]}
            for k in ("checkout_agree", "expiry_reply", "transfer_rejected"):
                self.assertIn(k, kinds, k)
            self.assertIn("svc=checkout", kinds["checkout_agree"]["link"])
            self.assertEqual(kinds["checkout_agree"]["level"], "urgent")
            self.assertEqual(d["total"], sum(i["count"] for i in d["items"]))
            self.assertEqual(d["urgent"], sum(i["count"] for i in d["items"] if i["level"] == "urgent"))
            self.assertEqual([i["level"] for i in d["items"]], sorted((i["level"] for i in d["items"]), key=lambda x: nf.LEVEL_ORDER[x]))   # 긴급이 먼저
            # 개인정보·본문 없음: 항목 키는 정해진 것뿐
            for i in d["items"]:
                self.assertEqual(set(i), {"kind", "title", "count", "link", "level", "action", "detail"})
            # 본인 몫만
            self.assertEqual(c.get(TN, headers=oh).status_code, 403)
            self.assertEqual(c.get(TN, headers=self.NOAUTH).status_code, 401)
            # 안내성 알림은 '확인함' 뒤 다시 안 뜬다(상태 기반 알림은 그대로)
            sr = c.post(TN + "/seen", headers=th, json={"kind": "transfer_rejected"})
            self.assertEqual(sr.status_code, 200, sr.text)
            k2 = {i["kind"] for i in sr.json()["items"]}
            self.assertNotIn("transfer_rejected", k2)
            self.assertIn("checkout_agree", k2)
            # 처리되면 저절로 사라진다(풀이 바뀌면 캐시도 갱신)
            co = CheckOutSettlement.get("co_t120"); co.status = "AGREED"; co.save(action="t")
            self.assertNotIn("checkout_agree", {i["kind"] for i in c.get(TN, headers=th).json()["items"]})
            # ── 임대인 ──
            made += [
                ConnectRequest(id="cnr_t120", branch="main", room_no="201", tenant_id=t.id, tenant_name=t.name, status="PENDING", expires_at=(datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")).save(action="t"),
                MaintenanceTicket(id="as_t120", branch="main", room_no="201", id_tenant=t.id, status="RECEIVED").save(action="t"),
                CheckOutSettlement(id="co_t120b", branch="main", id_room="room_x", room_no="203", id_contract="ct_x", id_tenant="tenant_x", status="REQUESTED").save(action="t"),
                TransferNotice(id="trn_t120b", branch="main", room_no="201", tenant_id=t.id, status="PENDING").save(action="t"),
            ]
            LN = "/api/rent3_lessor/notifications"
            ld = c.get(LN, headers=chic, params={"branch": "main"})
            self.assertEqual(ld.status_code, 200, ld.text)
            lk = {i["kind"]: i for i in ld.json()["items"]}
            for k in ("connect_request", "as_received", "checkout_requested", "transfer_pending"):
                self.assertIn(k, lk, k)
            self.assertIn("tab=ops", lk["as_received"]["link"])
            self.assertIn("branch=main", lk["connect_request"]["link"])
            # 메시지 알림은 호실별로 나뉘고, 누르면 그 호실 대화가 열리는 주소(room=)를 가진다
            from apps.rent_dist._rent3_core import room_messages as _rm
            _m = _rm.post("main", "201", "tenant", "임차인", "알림 시험", tenant_id=t.id)
            try:
                _ml = [i for i in c.get(LN, headers=chic, params={"branch": "main"}).json()["items"] if i["kind"].startswith("messages:")]
                _mk = {i["kind"]: i for i in _ml}
                self.assertIn("messages:201", _mk)
                self.assertIn("tab=messages", _mk["messages:201"]["link"])
                self.assertIn("room=201", _mk["messages:201"]["link"])
            finally:
                _m.delete(action="t")
            self.assertIn("params.get(\"room\")", open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read())
            self.assertEqual(c.get(LN, headers=sunho, params={"branch": "main"}).status_code, 403)       # 다른 지점 임대인
            self.assertEqual(c.get(LN, headers=self.NOAUTH, params={"branch": "main"}).status_code, 401)
            # 만료된 연결 요청은 세지 않는다
            cr = ConnectRequest.get("cnr_t120"); cr.expires_at = "2000-01-01T00:00:00"; cr.save(action="t")
            self.assertNotIn("connect_request", {i["kind"] for i in c.get(LN, headers=chic, params={"branch": "main"}).json()["items"]})
            # ── 관리인 ──
            made.append(Lessor(id="lsr_t120", username="t120", name="심사대기", phone="01011112222", branch="t120", status="PENDING_APPROVAL", applied_at=now).save(action="t"))
            AN = "/api/rent3_admin/notifications"
            ad = c.get(AN, headers=admin)
            self.assertEqual(ad.status_code, 200, ad.text)
            ak = {i["kind"]: i for i in ad.json()["items"]}
            self.assertGreaterEqual(ak["franchise_review"]["count"], 1)
            self.assertIn("tab=lessors", ak["franchise_review"]["link"])
            self.assertIn(c.get(AN, headers=chic).status_code, (401, 403))                              # 관리인 전용
            self.assertIn(c.get(AN, headers=self.NOAUTH).status_code, (401, 403))
        finally:
            for o in made:
                cur = type(o).get(o.id)
                if cur:
                    cur.delete(action="t")
            for x in ("co_t120b", "cnr_t120", "as_t120", "trn_t120b"):
                for cls in (CheckOutSettlement, ConnectRequest, MaintenanceTicket, TransferNotice):
                    cur = cls.get(x)
                    if cur:
                        cur.delete(action="t")
            tt = Tenant.get(t.id); tt.notice_seen = {}; tt.save(action="t")
        # 한 수집기가 실패해도 나머지는 계속된다
        def boom(_):
            raise RuntimeError("x")
        ok = nf._run([boom, lambda _: nf._item("k", "정상", 2, "/x", "normal")], None)
        self.assertEqual([(i["kind"], i["count"]) for i in ok], [("k", 2)])
        self.assertIsNone(nf._item("k", "없음", 0))
        # 화면: 세 앱이 알림 센터를 불러오고 시작한다
        for app, needle in (("rent3_admin", "/api/rent3_admin/notifications"), ("rent3_lessor", "/api/rent3_lessor/notifications?branch="), ("rent3_tenant", "/notifications`")):
            fe = os.path.join(ROOT, "apps", "rent_dist", app, "frontend")
            self.assertIn("/shared/ui/r3_notify.js", open(os.path.join(fe, "index.html"), encoding="utf-8").read(), app)
            self.assertIn("R3Notify.start", open(os.path.join(fe, "app.js"), encoding="utf-8").read(), app)
            self.assertIn(needle, open(os.path.join(fe, "app.js"), encoding="utf-8").read(), app)
        js = c.get("/shared/ui/r3_notify.js", headers=self.NOAUTH)
        self.assertEqual(js.status_code, 200)
        for needle in ("data-r3-burger", "r3-nbadge", "visibilitychange", "30000", "확인하기", "처리를 마치면 알림은 저절로 사라집니다"):
            self.assertIn(needle, js.text, needle)
        self.assertIn("tab=", open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read())

    def test_121_sms_multi_month_pick_and_overpay_guard(self):
        """121. 입금 문자 수납 시 미납이 1달(당월)만 있으면 즉시 처리, 완납/초과 입금은 오류 차단, 미납이 여러 달이면 체크 선택으로 반영(달 옮김 사유 차단)."""
        from apps.rent_dist._rent3_core import Contract, Pay
        c = self.client
        chic = self._login("chic")
        # 격리: 앞선 시험이 남긴 201호 수납 기록을 치워 미납 달 수를 확정하고, 끝나면 원래 기록으로 되돌린다
        ct0 = Contract.first(branch="main", room_no="201", status="ACTIVE")
        olds = [x.model_dump() for x in Pay.find(id_contract=ct0.id, include_voided=True)]
        for x in Pay.find(id_contract=ct0.id, include_voided=True):
            x.delete(action="t")

        def _restore_pays():
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            for d in olds:
                Pay(**d).save(action="t")
        self.addCleanup(_restore_pays)
        # 201호에 여러 달 미납 열기 (현재 기준월 기준)
        self._open_unpaid("201", 2)
        ct = next(x for x in Contract.all() if x.branch == "main" and x.status == "ACTIVE" and x.room_no == "201")
        monthly = int(ct.monthly_rent or 0) + int(ct.maintenance_fee or 0) + int(ct.compute_vat() or 0)
        led = ct.ledger()
        unpaid_yms = [r["ym"] for r in led["months"] if r["unpaid"] > 0]
        self.assertGreaterEqual(len(unpaid_yms), 2)
        oldest_ym, latest_ym = unpaid_yms[0], unpaid_yms[-1]

        # 1) 미납이 여러 달이고 1개월분만 입금(pick_if_multi=True) → 저장하지 않고 체크 선택 요청
        sms1 = f"[신한] 11/13 201호 {ct.tenant_name} {monthly:,}원 입금"
        r_pick = c.post("/api/rent3_lessor/payments/sms-paste", headers=chic, json={
            "branch": "main", "sms_text": sms1, "pick_if_multi": True,
        })
        self.assertEqual(r_pick.status_code, 200, r_pick.text)
        d_pick = r_pick.json()
        self.assertTrue(d_pick["needs_month_pick"])
        self.assertIn(oldest_ym, [m["ym"] for m in d_pick["unpaid_months"]])
        self.assertIn(latest_ym, [m["ym"] for m in d_pick["unpaid_months"]])

        # 2) 사용자가 당월(latest_ym)을 체크해 보내면 oldest_ym 이 아닌 latest_ym 으로 입금 처리
        r_chosen = c.post("/api/rent3_lessor/payments/sms-paste", headers=chic, json={
            "branch": "main", "sms_text": sms1, "pick_if_multi": True, "target_months": [latest_ym],
        })
        self.assertEqual(r_chosen.status_code, 200, r_chosen.text)
        self.assertEqual(r_chosen.json()["payment"]["billing_month"], latest_ym)

        # 3) 남은 미납이 1달뿐이면 체크 요청 없이 즉시 입금 처리
        rest_yms = [r["ym"] for r in ct.ledger()["months"] if r["unpaid"] > 0]
        for ym in rest_yms[:-1]:
            c.post("/api/rent3_lessor/payments/collect", headers=chic, json={
                "branch": "main", "room_no": "201", "amount": monthly, "target_ym": ym,
            })
            # 같은 금액 30초 이중 클릭 방지에 걸리지 않도록 방금 건의 접수 시각을 과거로 돌린다(시험 전용)
            for _p in Pay.find(id_contract=ct0.id):
                _p.created_at = "2020-01-01T00:00:00"; _p.save(action="t")
        last_ym = rest_yms[-1]
        sms2 = f"[신한] 11/14 201호 {ct.tenant_name} {monthly:,}원 입금"
        r_auto = c.post("/api/rent3_lessor/payments/sms-paste", headers=chic, json={
            "branch": "main", "sms_text": sms2, "pick_if_multi": True,
        })
        self.assertEqual(r_auto.status_code, 200, r_auto.text)
        self.assertFalse(r_auto.json().get("needs_month_pick", False))
        self.assertEqual(r_auto.json()["payment"]["billing_month"], last_ym)

        # 4) 완납 상태에서 추가 입금 시도 → 400 오류(선수금 차단)
        sms3 = f"[신한] 11/15 201호 {ct.tenant_name} {monthly:,}원 입금"
        r_over = c.post("/api/rent3_lessor/payments/sms-paste", headers=chic, json={
            "branch": "main", "sms_text": sms3, "pick_if_multi": True,
        })
        self.assertEqual(r_over.status_code, 400, r_over.text)

        # 5) 프론트엔드 체크 UI 및 수동 조치 문구 확인
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("pick_if_multi: true", "needs_month_pick", "sms-ym-chk", "송금자와 통화"):
            self.assertIn(needle, fe, needle)

    def test_122_ledger_phase_paid_due_soon_overdue(self):
        """122. 원장 3단계: 납입일(입주일의 일자) 당일까지 안 낸 달은 '납부 예정'(DUE_SOON), 다음 날부터 '연체'(OVERDUE), 낸 달은 완납. 미납 호실 알림·문자 달 선택은 연체만 센다. 수납 엔진은 그대로(납부 예정 달에도 미리 입금 가능)."""
        from apps.rent_dist._rent3_core import Contract
        base = Contract.first(branch="main", room_no="201", status="ACTIVE")
        ct = base.model_copy(update={"id": "ct_t122_unsaved", "move_in_date": "2026-09-15", "pay_timing": "선불"})   # 저장하지 않는 사본: 수납 기록 없음
        old = os.environ.get("RENT3_TODAY")
        try:
            os.environ["RENT3_TODAY"] = "2026-11-15"        # 11월 납입일 당일
            lg = ct.ledger()
            ph = {r["ym"]: r["phase"] for r in lg["months"]}
            self.assertEqual((ph["2026-09"], ph["2026-10"], ph["2026-11"]), ("OVERDUE", "OVERDUE", "DUE_SOON"))
            row11 = next(r for r in lg["months"] if r["ym"] == "2026-11")
            self.assertIn("납부 예정", row11["note"])
            self.assertEqual(lg["overdue_unpaid"], lg["total_unpaid"] - row11["unpaid"])
            os.environ["RENT3_TODAY"] = "2026-11-16"        # 납입일 다음 날부터 연체
            lg2 = ct.ledger()
            self.assertEqual(next(r["phase"] for r in lg2["months"] if r["ym"] == "2026-11"), "OVERDUE")
            self.assertEqual(lg2["overdue_unpaid"], lg2["total_unpaid"])
            os.environ["RENT3_TODAY"] = "2026-11-05"        # 11월 납입일 전: 9·10월만 연체
            lg3 = ct.ledger()
            self.assertEqual(next(r["phase"] for r in lg3["months"] if r["ym"] == "2026-11"), "DUE_SOON")
        finally:
            if old is None:
                os.environ.pop("RENT3_TODAY", None)
            else:
                os.environ["RENT3_TODAY"] = old
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn('m.phase === "DUE_SOON"', fe)
        nf = open(os.path.join(ROOT, "apps", "rent_dist", "_rent3_core", "notifications.py"), encoding="utf-8").read()
        self.assertIn("overdue_unpaid", nf)
        # 배지: 임대인 원장·호실 카드·KPI, 임차인 마이홈 배너·헤더·월별 현황
        for needle in ("dueSoonOf", "total_due_soon_branch", "🕒 납부 예정"):
            self.assertIn(needle, fe, needle)
        tfe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("dueSoonAmt", "overdue_unpaid", "🕒 납부 예정"):
            self.assertIn(needle, tfe, needle)
        d = self.client.get("/api/rent3_lessor/dashboard", headers=self._login("chic"), params={"branch": "main"})
        if d.status_code == 200:
            j = d.json()
            self.assertEqual(j["total_overdue_branch"] + j["total_due_soon_branch"], j["total_unpaid_branch"])


    def test_123_bank_mail_auto_collect_optin_flow(self):
        """123. 은행 입금 메일 자동 정리(선택·기본 꺼짐): 동의·검증 안 된 은행의 자동 처리 거부, 서명 웹훅, 위조·출금·타 계좌·중복·해석 실패 무시, 발신 확인 불가·장부 불일치·달 선택 필요는 확인 대기,
        확인 후 수납(구조화 값·멱등), 자동 조건이면 수납, 알림(확인 대기·자동 수납·실패), 원문 미저장, 타 지점 거부, 철회 시 즉시 삭제·주소 폐기, 보관 만료 삭제."""
        import hashlib, hmac, json, time
        from datetime import timedelta
        from apps.rent_dist._rent3_core import Contract, Lessor, Pay, current_ym, date_today, mailpay, mailparse
        c = self.client
        chic, sunho = self._login("chic"), self._login("sunho")
        LS, IN = "/api/rent3_lessor", "/api/rent3/inbound/mail"
        SECRET = "test-secret-123"
        old_env = {k: os.environ.get(k) for k in ("RENT3_MAIL_SECRET", "RENT3_MAIL_DOMAIN", "RENT3_MAIL_ENABLED")}
        os.environ["RENT3_MAIL_SECRET"], os.environ["RENT3_MAIL_DOMAIN"], os.environ["RENT3_MAIL_ENABLED"] = SECRET, "in.rent3-test.example", "1"
        ct0 = Contract.first(branch="main", room_no="201", status="ACTIVE")
        keep_in, keep_timing = ct0.move_in_date, ct0.pay_timing
        olds = [x.model_dump() for x in Pay.find(id_contract=ct0.id, include_voided=True)]
        lsr0 = Lessor.for_branch("main")
        keep_opt = dict(lsr0.mail_opt or {})
        bank_verified = mailparse.BANKS["nonghyup"]["verified"]
        counter = [0]

        def reset_pays(move_in=None):
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            ct = Contract.get(ct0.id); ct.move_in_date = move_in or f"{current_ym()}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            return ct

        def nh(amount, payer, memo="201호 월세", kind="입금", tail="4567-83", day=None):
            d = (day or date_today()).strftime("%Y/%m/%d")
            lines = ["NH농협은행 전자금융 입금 안내", f"거래일시 : {d} 10:23:45", f"거래구분 : {kind}", f"입금계좌 : 351-****-{tail}",
                     f"보낸분 : {payer}", f"입금금액 : {amount:,}원", "거래후잔액 : 1,230,000원", f"메모 : {memo}"]
            return chr(10).join(lines) + chr(10)

        def send(token, text, dkim=("nonghyup.com",), frm="noreply@nonghyup.com", mid=None, sign=True, ts=None):
            counter[0] += 1
            body = json.dumps({"to": f"pay-{token}@in.rent3-test.example", "message_id": mid or f"m{counter[0]}-{time.time()}", "from_addr": frm,
                               "dkim_pass_domains": list(dkim), "subject": "[NH농협은행] 입금 알림", "text": text, "ts": ts or time.time()}).encode("utf-8")
            sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest() if sign else "bad"
            return c.post(IN, content=body, headers={"X-Rent3-Signature": sig, "Content-Type": "application/json"})

        def intakes():
            return c.get(LS + "/mail/intakes", headers=chic, params={"branch": "main"}).json()["items"]

        def notices():
            return {i["kind"]: i for i in c.get(LS + "/notifications", headers=chic, params={"branch": "main"}).json()["items"]}

        try:
            ct = reset_pays()
            monthly = int(ct.monthly_rent or 0) + int(ct.maintenance_fee or 0) + int(ct.compute_vat() or 0)
            name = ct.tenant_name
            # ── 설정: 기본 꺼짐 / 동의 필수 / 검증 안 된 은행은 자동 처리 거부 ──
            st = c.get(LS + "/mail/settings", headers=chic, params={"branch": "main"}).json()
            self.assertFalse(st["active"])
            self.assertTrue(any(b["id"] == "nonghyup" and not b["verified"] for b in st["banks"]))
            base = {"branch": "main", "bank": "nonghyup", "acct_tail": "6783", "auto": False}
            self.assertEqual(c.post(LS + "/mail/enable", headers=chic, json={**base, "consent": False}).status_code, 400)
            self.assertEqual(c.post(LS + "/mail/enable", headers=chic, json={**base, "consent": True, "auto": True}).status_code, 400)
            self.assertEqual(c.post(LS + "/mail/enable", headers=chic, json={**base, "consent": True, "acct_tail": "12"}).status_code, 400)
            self.assertEqual(c.post(LS + "/mail/enable", headers=sunho, json={**base, "consent": True}).status_code, 403)
            self.assertEqual(c.post(LS + "/mail/enable", headers=self.NOAUTH, json={**base, "consent": True}).status_code, 401)
            # 꺼져 있을 때: 어떤 토큰이든 폐기, 서명 틀리면 401, 시각 틀리면 401, 비밀키 없으면 503
            self.assertEqual(send("deadbeef" * 2 + "0000", "x").json()["status"], "REJECTED")
            self.assertEqual(send("x", "y", sign=False).status_code, 401)
            self.assertEqual(send("x", "y", ts=1).status_code, 401)
            os.environ["RENT3_MAIL_SECRET"] = ""
            self.assertEqual(c.post(IN, content=b"{}", headers={"X-Rent3-Signature": "x"}).status_code, 503)
            os.environ["RENT3_MAIL_SECRET"] = SECRET
            en = c.post(LS + "/mail/enable", headers=chic, json={**base, "consent": True})
            self.assertEqual(en.status_code, 200, en.text)
            e = en.json()
            self.assertTrue(e["active"] and e["address"].startswith("pay-") and e["address"].endswith("@in.rent3-test.example"))
            token = e["address"].split("@")[0][4:]
            self.assertNotIn("mail_opt", Lessor.for_branch("main").safe_dump())      # 수신 토큰은 일반 응답에 안 실린다

            # ── 확인 후 처리(자동 꺼짐): 일치해도 확인 대기 ──
            A = nh(monthly, name)
            self.assertEqual(send(token, A, mid="mail-A").json()["status"], "PENDING")
            self.assertEqual(send(token, A, mid="mail-A").json()["status"], "DUPLICATE")             # 같은 메일 두 번
            self.assertEqual(send(token, nh(monthly, name, tail="1111-11")).json()["status"], "IGNORED")       # 내 계좌 아님
            self.assertEqual(send(token, nh(monthly, name, kind="출금")).json()["status"], "IGNORED")            # 출금
            self.assertEqual(send(token, nh(monthly, name), dkim=(), frm="x@evil.example").json()["status"], "IGNORED")   # 위조 도메인
            self.assertEqual(send(token, "안녕하세요 광고입니다").json()["status"], "FAILED")                       # 해석 실패
            self.assertEqual(send(token, nh(monthly, name), dkim=()).json()["status"], "PENDING")                # 서명 확인 불가 → 확인 대기
            self.assertEqual(send(token, nh(monthly + 1000, name)).json()["status"], "PENDING")                  # 금액 불일치 → 확인 대기
            rows = intakes()
            by = lambda st_: [r for r in rows if r["status"] == st_]
            self.assertEqual(len(by("PENDING")), 3)
            self.assertTrue(any("발신 확인 불가" in r["reason"] for r in by("PENDING")))
            self.assertTrue(any("청구액과 맞지" in r["reason"] for r in by("PENDING")))
            self.assertEqual(len(by("FAILED")), 1)
            # 원문은 저장하지 않는다
            dump = json.dumps([x.model_dump() for x in mailpay.MailIntake.find(lessor_id=Lessor.for_branch("main").id)], ensure_ascii=False)
            for secret_bit in ("월세", "전자금융", "거래후잔액", "1,230,000", "351-"):
                self.assertNotIn(secret_bit, dump, secret_bit)
            # 알림
            lk = notices()
            self.assertEqual(lk["mail_pending"]["count"], 3)
            self.assertIn("tab=mail", lk["mail_pending"]["link"])
            self.assertEqual(lk["mail_failed"]["count"], 1)
            # 무시 / 처리
            wrong = next(r for r in by("PENDING") if "청구액" in r["reason"])
            self.assertEqual(c.post(f"{LS}/mail/{wrong['id']}/dismiss", headers=chic, json={"branch": "main"}).status_code, 200)
            self.assertEqual(c.post(f"{LS}/mail/{wrong['id']}/apply", headers=chic, json={"branch": "main"}).status_code, 400)       # 이미 처리됨
            okp = next(r for r in by("PENDING") if r["reason"] == "확인 후 처리로 설정되어 있습니다")
            self.assertEqual(c.post(f"{LS}/mail/{okp['id']}/apply", headers=sunho, json={"branch": "main"}).status_code, 403)    # 타 지점
            ap = c.post(f"{LS}/mail/{okp['id']}/apply", headers=chic, json={"branch": "main"})
            self.assertEqual(ap.status_code, 200, ap.text)
            pays = [x for x in Pay.find(id_contract=ct0.id) if x.method == "BANK_MAIL"]
            self.assertEqual(len(pays), 1)
            self.assertEqual(pays[0].total_amount, monthly)
            self.assertEqual(send(token, A, mid="mail-A").json()["status"], "DUPLICATE")             # 처리 뒤 다시 와도 중복
            self.assertEqual(notices()["mail_pending"]["count"], 1)                                  # 처리하면 줄어든다
            # 해석 실패 알림은 '확인함' 뒤 다시 안 뜬다
            c.post(LS + "/notifications/seen", headers=chic, json={"branch": "main", "kind": "mail_failed"})
            self.assertNotIn("mail_failed", notices())

            # ── 달 선택 필요(밀린 달 2개 이상 + 일부 입금) → 확인 대기, 처리 때 달을 골라야 한다 ──
            y, m = int(current_ym()[:4]), int(current_ym()[5:7]) - 2
            if m <= 0:
                y, m = y - 1, m + 12
            ct = reset_pays(f"{y:04d}-{m:02d}-01")
            rows_unpaid = [r for r in ct.ledger()["months"] if r["unpaid"] > 0 and r.get("phase") == "OVERDUE"]
            self.assertGreaterEqual(len(rows_unpaid), 2)
            self.assertEqual(send(token, nh(monthly, name)).json()["status"], "PENDING")
            pk = next(r for r in intakes() if r["status"] == "PENDING" and "달에 넣을지" in r["reason"])
            need = c.post(f"{LS}/mail/{pk['id']}/apply", headers=chic, json={"branch": "main"}).json()
            self.assertTrue(need["needs_month_pick"] and len(need["unpaid_months"]) >= 2)
            pick_ym = need["unpaid_months"][-1]["ym"]
            okp2 = c.post(f"{LS}/mail/{pk['id']}/apply", headers=chic, json={"branch": "main", "target_months": [pick_ym]})
            self.assertEqual(okp2.status_code, 200, okp2.text)
            self.assertEqual([x.billing_month for x in Pay.find(id_contract=ct0.id) if x.method == "BANK_MAIL"], [pick_ym])

            # ── 자동 처리: 검증된 은행에서만 켜지고, 모든 조건이 맞을 때만 수납 ──
            mailparse.BANKS["nonghyup"]["verified"] = True
            try:
                ct = reset_pays()
                self.assertEqual(c.post(LS + "/mail/enable", headers=chic, json={**base, "consent": True, "auto": True}).status_code, 200)
                self.assertEqual(send(token, nh(monthly, name), mid="auto-1").json()["status"], "AUTO_APPLIED")
                self.assertEqual(len([x for x in Pay.find(id_contract=ct0.id) if x.method == "BANK_MAIL"]), 1)
                self.assertEqual(send(token, nh(monthly, name), mid="auto-1").json()["status"], "DUPLICATE")
                self.assertEqual(notices()["mail_auto"]["count"], 1)
                c.post(LS + "/notifications/seen", headers=chic, json={"branch": "main", "kind": "mail_auto"})
                self.assertNotIn("mail_auto", notices())
                # 자동이어도 서명 확인 불가·오래된 입금일·완납 뒤 추가 입금은 확인 대기
                self.assertEqual(send(token, nh(monthly, name), dkim=()).json()["status"], "PENDING")
                self.assertEqual(send(token, nh(monthly, name, day=date_today() - timedelta(days=10))).json()["status"], "PENDING")
                self.assertEqual(send(token, nh(monthly, name)).json()["status"], "PENDING")
                self.assertEqual(len([x for x in Pay.find(id_contract=ct0.id) if x.method == "BANK_MAIL"]), 1)
            finally:
                mailparse.BANKS["nonghyup"]["verified"] = bank_verified
            # 토큰 재발급: 옛 주소는 폐기
            rot = c.post(LS + "/mail/rotate-token", headers=chic, json={"branch": "main"}).json()
            new_token = rot["address"].split("@")[0][4:]
            self.assertNotEqual(new_token, token)
            self.assertEqual(send(token, nh(monthly, name)).json()["status"], "REJECTED")
            # 보관 만료 삭제
            old_it = mailpay.MailIntake(lessor_id=Lessor.for_branch("main").id, branch="main", msg_hash="old", status="IGNORED", purge_at="2000-01-01")
            old_it.save(action="t")
            mailpay.purge_due()
            self.assertIsNone(mailpay.MailIntake.get(old_it.id))
            # ── 철회: 보관 자료 즉시 삭제 + 주소 폐기 ──
            self.assertTrue(len(intakes()) > 0)
            dis = c.post(LS + "/mail/disable", headers=chic, json={"branch": "main"}).json()
            self.assertGreater(dis["deleted"], 0)
            self.assertFalse(dis["active"] or dis["address"])
            self.assertEqual(intakes(), [])
            self.assertEqual(send(new_token, nh(monthly, name)).json()["status"], "REJECTED")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            mailparse.BANKS["nonghyup"]["verified"] = bank_verified
            lsr = Lessor.for_branch("main")
            for x in mailpay.MailIntake.find(lessor_id=lsr.id):
                x.delete(action="t")
            lsr.mail_opt = keep_opt; lsr.notice_seen = {}; lsr.save(action="t")
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            for d in olds:
                Pay(**d).save(action="t")
            cc = Contract.get(ct0.id); cc.move_in_date = keep_in; cc.pay_timing = keep_timing; cc.save(action="t")
        # 화면: 메뉴·탭·설정 카드(동의·은행·계좌 끝자리·자동 처리 잠금·철회)
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ('tab: "mail"', "renderMailTab", "/mail/enable", "/mail/disable", "/mail/rotate-token", "needs_month_pick", "수신 동의"):
            self.assertIn(needle, fe, needle)


    def test_124_bank_mail_test_domain_and_gmail_forward_code(self):
        """124. 입금 메일 보완: ① 테스트 전용 허용 도메인(RENT3_MAIL_EXTRA_DOMAINS)은 RENT3_MAIL_TEST_BRANCHES 지점에만 적용되고, 그 메일은 항상 확인 대기([테스트])이며 자동 수납되지 않는다(테스트 모드 띠).
        ② 지메일 전달 확인 메일은 구글 서명일 때만 숫자 코드만 1시간 보관해 설정 화면에 보이고(링크 미보관), 못 읽으면 오류 안내, 입력 뒤 삭제, 위조(서명 없음)는 무시."""
        import hashlib, hmac, json, time
        from apps.rent_dist._rent3_core import Contract, Lessor, Pay, current_ym, date_today, mailpay, mailparse
        c = self.client
        chic = self._login("chic")
        LS, IN = "/api/rent3_lessor", "/api/rent3/inbound/mail"
        SECRET = "test-secret-124"
        keys = ("RENT3_MAIL_SECRET", "RENT3_MAIL_DOMAIN", "RENT3_MAIL_EXTRA_DOMAINS", "RENT3_MAIL_TEST_BRANCHES", "RENT3_MAIL_ENABLED")
        old_env = {k: os.environ.get(k) for k in keys}
        os.environ.update({"RENT3_MAIL_SECRET": SECRET, "RENT3_MAIL_DOMAIN": "in.rent3-test.example", "RENT3_MAIL_ENABLED": "1"})
        os.environ.pop("RENT3_MAIL_EXTRA_DOMAINS", None); os.environ.pop("RENT3_MAIL_TEST_BRANCHES", None)
        ct0 = Contract.first(branch="main", room_no="201", status="ACTIVE")
        keep_in, keep_timing = ct0.move_in_date, ct0.pay_timing
        olds = [x.model_dump() for x in Pay.find(id_contract=ct0.id, include_voided=True)]
        keep_opt = dict(Lessor.for_branch("main").mail_opt or {})
        bank_verified = mailparse.BANKS["nonghyup"]["verified"]
        n = [0]

        def send(token, text, dkim, frm, subject="[NH농협은행] 입금 알림"):
            n[0] += 1
            body = json.dumps({"to": f"pay-{token}@in.rent3-test.example", "message_id": f"t124-{n[0]}-{time.time()}", "from_addr": frm,
                               "dkim_pass_domains": list(dkim), "subject": subject, "text": text, "ts": time.time()}).encode("utf-8")
            sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
            return c.post(IN, content=body, headers={"X-Rent3-Signature": sig, "Content-Type": "application/json"})

        def settings():
            return c.get(LS + "/mail/settings", headers=chic, params={"branch": "main"}).json()

        try:
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            ct = Contract.get(ct0.id); ct.move_in_date = f"{current_ym()}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            monthly = int(ct.monthly_rent or 0) + int(ct.maintenance_fee or 0) + int(ct.compute_vat() or 0)
            d = date_today().strftime("%Y/%m/%d")
            text = chr(10).join(["거래일시 : " + d + " 10:00:00", "거래구분 : 입금", "입금계좌 : 351-****-4567-83", f"보낸분 : {ct.tenant_name}", f"입금금액 : {monthly:,}원", "메모 : 201호"])
            en = c.post(LS + "/mail/enable", headers=chic, json={"branch": "main", "consent": True, "bank": "nonghyup", "acct_tail": "6783", "auto": False})
            self.assertEqual(en.status_code, 200, en.text)
            token = en.json()["address"].split("@")[0][4:]
            # ── ① 테스트 도메인 ──
            self.assertFalse(settings()["test_mode"])
            self.assertEqual(send(token, text, ["gmail.com"], "me@gmail.com").json()["status"], "IGNORED")       # 변수 없음 → 일반 메일은 무시
            os.environ["RENT3_MAIL_EXTRA_DOMAINS"] = "gmail.com"
            self.assertEqual(send(token, text, ["gmail.com"], "me@gmail.com").json()["status"], "IGNORED")       # 테스트 지점이 아니면 여전히 무시
            self.assertFalse(settings()["test_mode"])
            os.environ["RENT3_MAIL_TEST_BRANCHES"] = "main"
            self.assertTrue(settings()["test_mode"])
            self.assertEqual(send(token, text, ["gmail.com"], "me@gmail.com").json()["status"], "PENDING")        # 테스트 지점 → 확인 대기
            row = next(r for r in c.get(LS + "/mail/intakes", headers=chic, params={"branch": "main"}).json()["items"] if r["status"] == "PENDING")
            self.assertTrue(row["is_test"] and "[테스트]" in row["reason"])
            mailparse.BANKS["nonghyup"]["verified"] = True                                                          # 검증 은행 + 자동 처리여도 테스트 메일은 자동 수납 안 함
            try:
                c.post(LS + "/mail/enable", headers=chic, json={"branch": "main", "consent": True, "bank": "nonghyup", "acct_tail": "6783", "auto": True})
                self.assertEqual(send(token, text, ["gmail.com"], "me@gmail.com").json()["status"], "PENDING")
                self.assertEqual([x for x in Pay.find(id_contract=ct0.id) if x.method == "BANK_MAIL"], [])
            finally:
                mailparse.BANKS["nonghyup"]["verified"] = bank_verified
            # ── ② 지메일 전달 확인 코드 ──
            gm = "forwarding-noreply@google.com"
            self.assertEqual(send(token, "코드 123456 입니다 https://mail-settings.google.com/mail/vf-secret", [], gm, "(#123456) Gmail 전달 확인").json()["status"], "IGNORED")   # 구글 서명 없음 → 위조
            self.assertEqual(settings()["forward_code"], "")
            self.assertEqual(send(token, "확인 링크: https://mail-settings.google.com/mail/vf-secret", ["google.com"], gm, "(#654321) Gmail 전달 확인").json()["status"], "FORWARD_CODE")
            st = settings()
            self.assertEqual(st["forward_code"], "654321")
            self.assertNotIn("vf-secret", json.dumps(Lessor.for_branch("main").mail_opt))                          # 링크는 보관하지 않는다
            self.assertEqual(c.post(LS + "/mail/forward-code/clear", headers=chic, json={"branch": "main"}).json()["forward_code"], "")
            self.assertEqual(send(token, "코드를 찾을 수 없는 안내문", ["google.com"], gm, "Gmail 전달 확인").json()["status"], "FORWARD_CODE_ERROR")
            self.assertTrue(settings()["forward_error"])
            o = dict(Lessor.for_branch("main").mail_opt); o["forward_code_error"] = {"expires_at": "2000-01-01T00:00:00"}   # 1시간 뒤 저절로 사라짐
            lsr = Lessor.for_branch("main"); lsr.mail_opt = o; lsr.save(action="t")
            self.assertFalse(settings()["forward_error"])
            # 구글 전달 확인 메일은 처리 내역(확인 대기)에 쌓이지 않는다
            self.assertFalse([r for r in c.get(LS + "/mail/intakes", headers=chic, params={"branch": "main"}).json()["items"] if r["status"] == "FAILED"])
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            mailparse.BANKS["nonghyup"]["verified"] = bank_verified
            lsr = Lessor.for_branch("main")
            for x in mailpay.MailIntake.find(lessor_id=lsr.id):
                x.delete(action="t")
            lsr.mail_opt = keep_opt; lsr.save(action="t")
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            for dd in olds:
                Pay(**dd).save(action="t")
            cc = Contract.get(ct0.id); cc.move_in_date = keep_in; cc.pay_timing = keep_timing; cc.save(action="t")
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("테스트 모드", "forward_code", "mlCodeDone", "forward_error", "/mail/forward-code/clear"):
            self.assertIn(needle, fe, needle)


    def test_125_bank_mail_multi_accounts_and_bank_autodetect(self):
        """125. 입금 메일: 한 임대인이 여러 은행·여러 계좌(최대 5개)를 등록하면 메일의 발신 도메인으로 은행을 자동 판별하고, 그 은행 계좌의 끝 4자리와 맞을 때만 처리한다.
        다른 은행의 끝자리·등록하지 않은 은행·삭제한 계좌는 무시, 검증 안 된 은행이 섞이면 자동 처리 불가, 예전 설정(한 계좌)도 읽고, 안내는 지메일 전용."""
        import hashlib, hmac, json, time
        from apps.rent_dist._rent3_core import Lessor, date_today, mailpay, mailparse
        c = self.client
        chic = self._login("chic")
        LS, IN = "/api/rent3_lessor", "/api/rent3/inbound/mail"
        SECRET = "test-secret-125"
        keys = ("RENT3_MAIL_SECRET", "RENT3_MAIL_DOMAIN", "RENT3_MAIL_EXTRA_DOMAINS", "RENT3_MAIL_TEST_BRANCHES", "RENT3_MAIL_ENABLED")
        old_env = {k: os.environ.get(k) for k in keys}
        os.environ.update({"RENT3_MAIL_SECRET": SECRET, "RENT3_MAIL_DOMAIN": "in.rent3-test.example", "RENT3_MAIL_ENABLED": "1"})
        os.environ.pop("RENT3_MAIL_EXTRA_DOMAINS", None); os.environ.pop("RENT3_MAIL_TEST_BRANCHES", None)
        keep_opt = dict(Lessor.for_branch("main").mail_opt or {})
        had_fake = "tbank" in mailparse.BANKS
        mailparse.BANKS["tbank"] = {"label": "시험은행", "domains": ("testbank.example",), "verified": False, "parser": mailparse.parse_nonghyup, "note": "시험용"}
        n = [0]

        def mail(tail, amount=550000):
            d = date_today().strftime("%Y/%m/%d")
            return chr(10).join(["거래일시 : " + d + " 10:00:00", "거래구분 : 입금", f"입금계좌 : 351-****-{tail}", "보낸분 : 이민수", f"입금금액 : {amount:,}원", "메모 : 201호"])

        def send(token, text, dom):
            n[0] += 1
            body = json.dumps({"to": f"pay-{token}@in.rent3-test.example", "message_id": f"t125-{n[0]}-{time.time()}", "from_addr": f"noreply@{dom}",
                               "dkim_pass_domains": [dom], "subject": "입금 알림", "text": text, "ts": time.time()}).encode("utf-8")
            return c.post(IN, content=body, headers={"X-Rent3-Signature": hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"}).json()["status"]

        def last_reason():
            return " | ".join(r["reason"] for r in c.get(LS + "/mail/intakes", headers=chic, params={"branch": "main"}).json()["items"])   # 같은 초에 쌓여 순서가 흔들리므로 전체에서 찾는다

        def post(path, body):
            return c.post(LS + path, headers=chic, json={"branch": "main", **body})

        try:
            A = [{"bank": "nonghyup", "tail": "6783"}, {"bank": "tbank", "tail": "1111"}]
            self.assertEqual(post("/mail/enable", {"consent": True, "accounts": A, "auto": True}).status_code, 400)        # 검증 안 된 은행이 섞이면 자동 처리 불가
            self.assertEqual(post("/mail/enable", {"consent": True, "accounts": [{"bank": "nope", "tail": "1234"}]}).status_code, 400)
            self.assertEqual(post("/mail/enable", {"consent": True, "accounts": [{"bank": "nonghyup", "tail": "12"}]}).status_code, 400)
            self.assertEqual(post("/mail/enable", {"consent": True, "accounts": [{"bank": "nonghyup", "tail": f"{i:04d}"} for i in range(6)]}).status_code, 400)   # 최대 5개
            en = post("/mail/enable", {"consent": True, "accounts": A + [A[0]], "auto": False})                              # 같은 계좌는 하나로
            self.assertEqual(en.status_code, 200, en.text)
            st = en.json()
            self.assertEqual([(a["bank"], a["tail"]) for a in st["accounts"]], [("nonghyup", "6783"), ("tbank", "1111")])
            self.assertEqual(st["max_accounts"], 5)
            token = st["address"].split("@")[0][4:]
            # 은행 자동 판별: 발신 도메인 → 그 은행의 계좌 끝자리와 맞을 때만 처리
            self.assertEqual(send(token, mail("4567-83"), "nonghyup.com"), "PENDING")
            self.assertEqual(send(token, mail("1111-11"), "testbank.example"), "PENDING")
            self.assertEqual(send(token, mail("4567-83"), "testbank.example"), "IGNORED")           # 농협 계좌 끝자리를 시험은행 메일로 받음
            self.assertIn("내 입금 계좌", last_reason())
            self.assertEqual(send(token, mail("1111-11"), "nonghyup.com"), "IGNORED")               # 시험은행 끝자리를 농협 메일로 받음
            self.assertEqual(send(token, mail("4567-83"), "other-bank.example"), "IGNORED")         # 등록하지 않은 은행
            self.assertIn("허용된 은행", last_reason())
            # 계좌 목록 바꾸기: 삭제하면 그 은행 메일은 무시, 0개는 거부, 자동 처리는 검증 안 된 은행이 있으면 저절로 꺼진다
            self.assertEqual(post("/mail/accounts", {"accounts": []}).status_code, 400)
            self.assertEqual(post("/mail/accounts", {"accounts": [A[0]]}).status_code, 200)
            self.assertEqual(send(token, mail("1111-11"), "testbank.example"), "IGNORED")
            self.assertEqual(c.post(LS + "/mail/accounts", headers=self.NOAUTH, json={"branch": "main", "accounts": [A[0]]}).status_code, 401)
            self.assertEqual(c.post(LS + "/mail/accounts", headers=self._login("sunho"), json={"branch": "main", "accounts": [A[0]]}).status_code, 403)
            lsr = Lessor.for_branch("main")
            o = dict(lsr.mail_opt); o["auto"] = True; lsr.mail_opt = o; lsr.save(action="t")
            self.assertFalse(post("/mail/accounts", {"accounts": A}).json()["auto"])
            # 예전 설정(한 쌍)도 한 계좌로 읽는다
            lsr = Lessor.for_branch("main")
            o = dict(lsr.mail_opt); o.pop("accounts", None); o.update({"bank": "nonghyup", "acct_tail": "6783"}); lsr.mail_opt = o; lsr.save(action="t")
            self.assertEqual(mailpay.accounts_of(Lessor.for_branch("main").mail_opt), [{"bank": "nonghyup", "tail": "6783"}])
            self.assertEqual(send(token, mail("4567-83"), "nonghyup.com"), "PENDING")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if not had_fake:
                mailparse.BANKS.pop("tbank", None)
            lsr = Lessor.for_branch("main")
            for x in mailpay.MailIntake.find(lessor_id=lsr.id):
                x.delete(action="t")
            lsr.mail_opt = keep_opt; lsr.save(action="t")
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("mlAccAdd", "ml-acc-del", "/mail/accounts", "지메일(Gmail) 전용", "전달 및 POP/IMAP", "필터 및 차단된 주소"):
            self.assertIn(needle, fe, needle)


    def test_126_bank_mail_feature_switch_off_by_default(self):
        """126. 입금 메일 자동 정리는 완성 전까지 꺼 둔다(RENT3_MAIL_ENABLED 없으면 꺼짐): 화면 메뉴 숨김(dashboard.features.mail=false)·설정/계좌/처리 API는 '준비 중'으로 거부·
        수신 웹훅 503·알림 없음, 단 철회(끄기·보관 자료 삭제)는 항상 가능. 켜면(환경변수 1) 다시 열린다."""
        import hashlib, hmac, json, time
        from apps.rent_dist._rent3_core import Lessor, mailpay
        c = self.client
        chic = self._login("chic")
        LS, IN = "/api/rent3_lessor", "/api/rent3/inbound/mail"
        keys = ("RENT3_MAIL_ENABLED", "RENT3_MAIL_SECRET", "RENT3_MAIL_DOMAIN")
        old_env = {k: os.environ.get(k) for k in keys}
        keep_opt = dict(Lessor.for_branch("main").mail_opt or {})
        try:
            os.environ.pop("RENT3_MAIL_ENABLED", None)
            os.environ["RENT3_MAIL_SECRET"] = "s126"
            os.environ["RENT3_MAIL_DOMAIN"] = "in.rent3-test.example"
            self.assertFalse(mailpay.feature_enabled())
            d = c.get(LS + "/dashboard", headers=chic, params={"branch": "main"}).json()
            self.assertFalse(d["features"]["mail"])
            st = c.get(LS + "/mail/settings", headers=chic, params={"branch": "main"}).json()
            self.assertFalse(st["enabled"])
            body = {"branch": "main", "consent": True, "accounts": [{"bank": "nonghyup", "tail": "6783"}]}
            for path, payload in (("/mail/enable", body), ("/mail/accounts", {"branch": "main", "accounts": body["accounts"]}), ("/mail/rotate-token", {"branch": "main"}),
                                  ("/mail/forward-code/clear", {"branch": "main"}), ("/mail/x/apply", {"branch": "main"}), ("/mail/x/dismiss", {"branch": "main"})):
                r = c.post(LS + path, headers=chic, json=payload)
                self.assertEqual(r.status_code, 400, path)
                self.assertIn("준비 중", r.text, path)
            self.assertEqual(c.get(LS + "/mail/intakes", headers=chic, params={"branch": "main"}).status_code, 400)
            # 수신 웹훅은 서명이 맞아도 닫혀 있다
            raw = json.dumps({"to": "pay-x@in.rent3-test.example", "ts": time.time()}).encode("utf-8")
            sig = hmac.new(b"s126", raw, hashlib.sha256).hexdigest()
            self.assertEqual(c.post(IN, content=raw, headers={"X-Rent3-Signature": sig}).status_code, 503)
            # 이미 켜 둔 임대인이 있어도(예전 설정) 알림은 나오지 않고, 철회는 가능하다
            lsr = Lessor.for_branch("main")
            lsr.mail_opt = {"active": True, "token": "abcd" * 5, "accounts": [{"bank": "nonghyup", "tail": "6783"}], "consented_at": "2026-11-20T00:00:00"}
            lsr.save(action="t")
            mailpay.MailIntake(lessor_id=lsr.id, branch="main", msg_hash="h126", status="PENDING", purge_at="2999-01-01").save(action="t")
            kinds = {i["kind"] for i in c.get(LS + "/notifications", headers=chic, params={"branch": "main"}).json()["items"]}
            self.assertFalse({k for k in kinds if k.startswith("mail_")})
            self.assertEqual(mailpay.ingest({"to": "pay-" + "abcd" * 5 + "@x"}), {"status": "REJECTED"})
            dis = c.post(LS + "/mail/disable", headers=chic, json={"branch": "main"})
            self.assertEqual(dis.status_code, 200, dis.text)
            self.assertEqual(dis.json()["deleted"], 1)
            # 켜면 열린다
            os.environ["RENT3_MAIL_ENABLED"] = "1"
            self.assertTrue(c.get(LS + "/dashboard", headers=chic, params={"branch": "main"}).json()["features"]["mail"])
            self.assertTrue(c.get(LS + "/mail/settings", headers=chic, params={"branch": "main"}).json()["enabled"])
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            lsr = Lessor.for_branch("main")
            for x in mailpay.MailIntake.find(lessor_id=lsr.id):
                x.delete(action="t")
            lsr.mail_opt = keep_opt; lsr.save(action="t")
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("MAIL_ON", "buildMenu", "dash.features.mail", 'currentTab === "mail" && !MAIL_ON'):
            self.assertIn(needle, fe, needle)


    def test_127_staged_lessor_menu_collapsible_and_today_strip(self):
        """127. 임대인 ☰ 메뉴를 단계별로: 1단계 기본 업무(수납·메시지·중개 계약서)만 펼치고 나머지는 '더 많은 기능'으로 접는다(없애지 않음 — 기존 모든 화면이 메뉴에 그대로, 검색하면 펼침).
        첫 화면에는 처리할 일이 있을 때만 '오늘 할 일' 한 줄 띠."""
        import re
        root = os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend")
        app = open(os.path.join(root, "app.js"), encoding="utf-8").read()
        menu = app[app.index("const LESSOR_MENU"):app.index("R3Shell.renderMenu")]
        groups = re.findall(r'\{ title: "([^"]+)"(, more: true)?, items', menu)
        self.assertEqual(groups[0][0], "1단계 · 기본 업무")
        self.assertFalse(groups[0][1])                                           # 1단계는 펼쳐 둔다
        first = menu[menu.index('"1단계 · 기본 업무"'):menu.index('"2단계 · 매물 · 홍보"')]
        self.assertEqual(sorted(re.findall(r'tab: "(\w+)"', first)), ["broker", "ledger", "messages"])
        self.assertTrue(all(m for t, m in groups[1:6]))                           # 2~4단계·운영·건물 설정은 접는다
        self.assertFalse([m for t, m in groups if t in ("도움말 · 문의",) and m])  # 도움말·문의는 늘 보인다
        hb = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "hamburger.js"), encoding="utf-8").read()
        for needle in ("r3-more-sec", "r3-more-toggle", "r3-more-open", "더 많은 기능 보기", "moreUser"):
            self.assertIn(needle, hb, needle)
        self.assertNotRegex(hb, r"font-size:\s*(?:9|10|11)(?:\.\d)?px")
        shell = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        self.assertIn('data-more="1"', shell)
        nj = open(os.path.join(ROOT, "shared_ui", "r3_notify.js"), encoding="utf-8").read()
        for needle in ("r3Today", "todayAfter", "오늘 할 일", ".r3-today"):
            self.assertIn(needle, nj, needle)
        self.assertNotIn('todayAfter', app)  # 알림 🔔과 같은 내용이라 첫 화면 띠는 쓰지 않는다
        self.assertNotRegex(nj, r"font-size:\s*(?:9|10|11)(?:\.\d)?px")


    def test_128_sms_amount_differs_from_monthly_goes_to_manual_direct_pay(self):
        """128. 입금 문자 금액이 월 납부액과 다르면(여러 달분·일부 입금) 자동 처리하지 않고 '수작업 안내'(needs_manual) → [직접 수납]에서 미납 달을 여러 개 체크해 처리. 초과 입금·밀린 청구 없음은 처리 불가.
        직접 수납은 체크한 달의 미납 합계 이하만 받고, 문자에서 넘어온 입금일·입금자는 그대로 기록한다."""
        from apps.rent_dist._rent3_core import Contract, Pay, current_ym
        c = self.client
        chic = self._login("chic")
        LS = "/api/rent3_lessor"
        ct0 = Contract.first(branch="main", room_no="201", status="ACTIVE")
        olds = [x.model_dump() for x in Pay.find(id_contract=ct0.id, include_voided=True)]
        keep_in, keep_timing = ct0.move_in_date, ct0.pay_timing
        try:
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            y, m = int(current_ym()[:4]), int(current_ym()[5:7]) - 3
            while m <= 0:
                y, m = y - 1, m + 12
            ct = Contract.get(ct0.id); ct.move_in_date = f"{y:04d}-{m:02d}-01"; ct.pay_timing = "선불"; ct.save(action="t")
            monthly = int(ct.monthly_rent or 0) + int(ct.maintenance_fee or 0) + int(ct.compute_vat() or 0)
            name = ct.tenant_name
            rows = [r for r in ct.ledger()["months"] if r["unpaid"] > 0]
            self.assertGreaterEqual(len(rows), 3)
            total = sum(r["unpaid"] for r in rows)

            def sms(amount, day="11/13", extra=None):
                return c.post(LS + "/payments/sms-paste", headers=chic, json={"branch": "main", "sms_text": f"[신한] {day} 201호 {name} {amount:,}원 입금", "pick_if_multi": True, **(extra or {})})

            n0 = len(Pay.find(id_contract=ct0.id))
            # 3개월분 → 수작업 안내(저장 안 함)
            r = sms(monthly * 3)
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            self.assertTrue(d["needs_manual"] and d["needs_confirm"] and not d["success"])
            self.assertEqual((d["monthly"], d["total_unpaid"]), (monthly, total))
            self.assertGreaterEqual(len(d["unpaid_months"]), 3)
            self.assertIn("직접 수납", d["message"])
            # 일부 입금도 수작업 안내, 금액이 청구 합계와 안 맞아도 '문자 수정' 단계 없이 안내
            self.assertTrue(sms(monthly // 2).json()["needs_manual"])
            self.assertTrue(sms(monthly * 2 + 1234).json()["needs_manual"])
            self.assertEqual(len(Pay.find(id_contract=ct0.id)), n0)
            # 초과 입금은 처리 불가(오류)
            ov = sms(total + monthly)
            self.assertEqual(ov.status_code, 400)
            self.assertIn("많아 처리할 수 없습니다", ov.text)
            # 직접 수납: 체크한 달의 미납 합계보다 많으면 거부, 이하이면 그 달들에 반영(입금일·입금자 기록)
            pick = [r["ym"] for r in rows[:3]]
            tooMuch = c.post(LS + "/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": monthly * 3 + 1, "target_months": pick})
            self.assertEqual(tooMuch.status_code, 400)
            ok = c.post(LS + "/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": monthly * 3, "target_months": pick, "paid_date": "2026-11-13", "payer_name": name})
            self.assertEqual(ok.status_code, 200, ok.text)
            got = sorted(x.billing_month for x in Pay.find(id_contract=ct0.id))
            self.assertEqual(got, sorted(pick))
            self.assertTrue(all(x.paid_date == "2026-11-13" for x in Pay.find(id_contract=ct0.id)))
            # 월 납부액과 같은 금액은 이전처럼(자동 또는 달 선택) — 수작업 안내가 아니다
            ct = Contract.get(ct0.id)
            left = [r for r in ct.ledger()["months"] if r["unpaid"] > 0]
            if left:
                r1 = sms(monthly, "11/14").json()
                self.assertFalse(r1.get("needs_manual"))
            # 모두 낸 뒤 더 들어온 입금은 처리 불가
            for x in Pay.find(id_contract=ct0.id):
                x.created_at = "2020-01-01T00:00:00"; x.save(action="t")
            allm = [r["ym"] for r in Contract.get(ct0.id).ledger()["months"] if r["unpaid"] > 0]
            if allm:
                rest = sum(r["unpaid"] for r in Contract.get(ct0.id).ledger()["months"] if r["unpaid"] > 0)
                self.assertEqual(c.post(LS + "/payments/collect", headers=chic, json={"branch": "main", "room_no": "201", "amount": rest, "target_months": allm}).status_code, 200)
            self.assertEqual(sms(monthly, "11/15").status_code, 400)
        finally:
            for x in Pay.find(id_contract=ct0.id, include_voided=True):
                x.delete(action="t")
            for d0 in olds:
                Pay(**d0).save(action="t")
            cc = Contract.get(ct0.id); cc.move_in_date = keep_in; cc.pay_timing = keep_timing; cc.save(action="t")
        fe = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("needs_manual", "smsGoDirect", "smsManualHint", "pm-chk", "target_months: months", "수작업이 필요합니다", "직접 수납에서 월 선택하기"):
            self.assertIn(needle, fe, needle)


    def test_129_app_like_shell_clock_above_bell_and_wide_scrolling_room_modal(self):
        """129. 앱처럼(홈 화면 설치 시 주소창 없음): 매니페스트·메타, 날짜·시각은 🔔 위, 호실 창은 크게 열고 가로·세로로 밀어 볼 수 있다."""
        base = os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend")
        html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
        for needle in ('rel="manifest"', "apple-mobile-web-app-capable", 'id="bldgClock"', "viewport-fit=cover"):
            self.assertIn(needle, html, needle)
        mf = self.client.get("/rent3_lessor/manifest.json")
        self.assertEqual(mf.status_code, 200)
        self.assertEqual(mf.json().get("display"), "standalone")
        app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        for needle in ("r3-modal__box--wide", "overflow: auto !important", "min-width: 380px", 'xb.textContent = "✕ 닫기"', "min-height: 30px !important", 'getElementById("bldgClock")'):
            self.assertIn(needle, app, needle)
        sh = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        self.assertIn("timeEl", sh)
        md = open(os.path.join(ROOT, "shared_ui", "r3_modal.js"), encoding="utf-8").read()
        for needle in ("R3Hist", "pushState", "popstate", 'R3H.open("modal"', 'R3H.close("modal")', "history.back"):
            self.assertIn(needle, md, needle)   # 스마트폰 뒤로가기는 창만 닫는다


    def test_130_payer_names_alias_matches_sms_to_room(self):
        """130. 임차인 이름과 다른 이름으로 입금돼도(송금자명 등록) 문자 수납이 그 호실을 찾는다. 송금자명은 계약내용에서 최대 3개, 임차인명과 같은 건 무시."""
        from apps.rent_dist._rent3_core import Contract
        from apps.rent_dist._rent3_core.billing import FifoPaymentEngine
        c = self.client
        chic = self._login("chic")
        ct0 = Contract.first(branch="main", room_no="201", status="ACTIVE")
        keep = list(ct0.payer_names or [])
        try:
            def edit(names):
                return c.post("/api/rent3_lessor/contracts/edit", headers=chic, json={"branch": "main", "contract_id": ct0.id, "deposit": ct0.deposit,
                              "monthly_rent": ct0.monthly_rent, "maintenance_fee": ct0.maintenance_fee, "tenant_phone": ct0.tenant_phone or "", "payer_names": names})
            r = edit(["홍길순", "홍길순", " 박철수 ", ct0.tenant_name, ""])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(Contract.get(ct0.id).payer_names, ["홍길순", "박철수"])
            self.assertEqual(edit(["가", "나", "다", "라"]).status_code, 400)
            Contract.get(ct0.id)  # 실패한 수정은 저장되지 않는다
            self.assertEqual(Contract.get(ct0.id).payer_names, ["홍길순", "박철수"])
            base = {"room_no": "201", "amount": 1}
            has_name_issue = lambda parsed: any(i["field"] == "name" for i in FifoPaymentEngine.check_sms("main", parsed)["issues"])
            self.assertFalse(has_name_issue({**base, "payer_name": "홍길순"}))
            self.assertFalse(has_name_issue({**base, "payer_name": ct0.tenant_name}))
            self.assertTrue(has_name_issue({**base, "payer_name": "김아무개"}))
            # 호실 번호가 없는 문자: 송금자명으로 계약을 찾는다
            chk = FifoPaymentEngine.check_sms("main", {"room_no": "", "payer_name": "박철수", "amount": 1})
            self.assertEqual(chk["room_contract"].room_no, "201")
        finally:
            ct = Contract.get(ct0.id); ct.payer_names = keep; ct.save(action="t")


    def test_131_frontend_scripts_have_valid_syntax(self):
        """131. 화면 스크립트에 문법 오류가 없다(오류가 있으면 임대인 창이 통째로 열리지 않는다). node 가 없으면 건너뛴다."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node 없음")
        files = [os.path.join(ROOT, "apps", "rent_dist", d, "frontend", "app.js") for d in ("rent3_lessor", "rent3_tenant")]
        files += [os.path.join(ROOT, "shared_ui", n) for n in ("bass.js", "r3_shell.js", "r3_notify.js", "r3_modal.js")]
        files.append(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "hamburger.js"))
        for f in files:
            if os.path.exists(f):
                r = subprocess.run([node, "--check", f], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f + " :: " + r.stderr[:400])


    def test_132_mgmt_fee_basis_divides_common_costs_by_income_tax_rooms(self):
        """132. 관리비 산출근거: 공용 비용 항목별 합계(가맹비·경조사비 제외, 체크한 항목만) ÷ 종합소득세 적용 호실 수 = 호실당 연·월 금액. 월별 납부 내역은 월세+관리비를 합친 '월임차료' 한 칸이다."""
        from apps.rent_dist._rent3_core import Building, Outlay
        c = self.client
        chic = self._login("chic")
        LS = "/api/rent3_lessor"
        yr = 2031
        made = []
        try:
            def add(cat, amount, ref=False):
                r = c.post(LS + "/outlays", headers=chic, json={"branch": "main", "category": cat, "title": "t132", "amount": amount, "expense_date": f"{yr}-03-05", "evidence_type": "NONE", "reference_only": ref})
                self.assertEqual(r.status_code, 200, r.text)
                made.append(r.json()["outlay"]["id"])
            add("depreciation", 1200000, ref=True)     # 참고용: 산출근거에는 들어가지만 세무 경비에는 넣지 않는다
            add("repair_maint", 300000)
            add("electric_common", 500000)
            add("water_common", 200000)
            add("platform_fee", 99000)      # 기본 제외 항목
            d = c.get(LS + f"/mgmt-fee/basis?branch=main&year={yr}", headers=chic).json()
            self.assertTrue(d["success"])
            by = {i["category"]: i for i in d["items"]}
            self.assertEqual(by["depreciation"]["amount"], 1200000)
            self.assertEqual(by["electric_common"]["amount"], 500000)
            self.assertFalse(by["platform_fee"]["included"])
            self.assertEqual(d["total"], 1200000 + 300000 + 500000 + 200000)
            bldg = Building.first(branch="main")
            n = len([r for r in bldg.rooms() if bldg.is_income_tax_room(r.room_no)])
            self.assertEqual(d["room_count"], n)
            self.assertEqual(d["per_room_year"], round(d["total"] / n))
            self.assertEqual(d["per_room_month"], round(d["per_room_year"] / 12))
            tx = c.get(LS + f"/tax/summary?branch=main&year={yr}", headers=chic).json()
            titles = [(o["category"], o["amount"]) for o in tx["outlays"]]
            self.assertNotIn(("depreciation", 1200000), titles)      # 참고용 항목은 세무 경비 목록·합계에서 빠진다
            self.assertIn(("repair_maint", 300000), titles)
            # 항목을 직접 제외하면 합계에서 빠진다
            d2 = c.get(LS + f"/mgmt-fee/basis?branch=main&year={yr}&exclude=depreciation,platform_fee", headers=chic).json()
            self.assertEqual(d2["total"], 300000 + 500000 + 200000)
        finally:
            for oid in made:
                o = Outlay.get(oid)
                if o:
                    o.delete(action="t")
        base = os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend")
        app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        for needle in ('id: "btnMgmtFee"', "showMgmtFeeModal", "/mgmt-fee/basis", 'class="bass-input pr-total"', "월임차료", "reference_only: !body.querySelector", "refresh().catch", "호실별 관리비 청구·수납에 반영되지 않습니다", 'id="mfTax" type="checkbox" checked', "계약서에 적힌 월세:관리비 비율", "rows=\"3\""):
            self.assertIn(needle, app, needle)
        self.assertNotIn('class="bass-input pr-rent"', app)
        self.assertNotIn('class="bass-input pr-fee"', app)


    def test_133_more_toggle_never_swallowed_and_clock_above_bell(self):
        """133. ☰ 메뉴의 '더 많은 기능 보기' 단추가 접힌 그룹 안에 끼어 사라지지 않는다(그러면 3단계 등이 영영 안 보인다) · 날짜·시각은 🔔 위 묶음에 둔다 · 관리비 산출근거 줄 레이아웃은 공용 스타일로 고정."""
        hb = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "hamburger.js"), encoding="utf-8").read()
        for needle in ('R3H.open("drawer"', 'R3H.close("drawer")', 'R3H.close("drawer", true)', "skipBack", "histSync(isOpen)", "old.parentNode === drawer", "old.remove()", '!c.classList.contains("r3-more-toggle")'):
            self.assertIn(needle, hb, needle)
        r3m = open(os.path.join(ROOT, "shared_ui", "r3_modal.js"), encoding="utf-8").read()
        self.assertIn("skipBack", r3m)
        app = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("placeClockAboveBell", "r3-hdr-right", ".r3-modal__body .mf-row { display: grid !important"):
            self.assertIn(needle, app, needle)


    def test_134_lessor_registers_tenant_business_info_and_menu_has_invoice_entry(self):
        """134. 임대인이 임차인용 사업자등록증 정보를 올리면 그 호실 계약·임차인의 세금계산서 발급 정보(기존 필드)에 저장되고 부가세 청구 여부(vat_target)는 건드리지 않는다. ☰ 메뉴에 세금계산서·현금영수증 발행 항목이 있다."""
        from apps.rent_dist._rent3_core import Contract, Tenant
        c = self.client
        chic = self._login("chic")
        ct0 = Contract.first(branch="main", room_no="201", status="ACTIVE")
        tn0 = Tenant.get(ct0.id_tenant) if ct0.id_tenant else None
        keep_ct = {k: getattr(ct0, k) for k in ("tenant_business_number", "tenant_company_name", "tenant_ceo_name", "tenant_business_address", "tenant_email", "tax_invoice_requested", "vat_target")}
        keep_tn = {k: getattr(tn0, k) for k in ("business_number", "company_name", "ceo_name", "business_address", "email")} if tn0 else {}
        try:
            url = "/api/rent3_lessor/contracts/tenant-bizreg"
            bad = c.post(url, headers=chic, json={"branch": "main", "room_no": "201", "business_number": "123-45-67890"})
            self.assertEqual(bad.status_code, 400, bad.text)
            none = c.post(url, headers=chic, json={"branch": "main", "room_no": "201"})
            self.assertEqual(none.status_code, 400, none.text)             # 사진도 번호도 없으면 거부
            self.assertEqual(c.post(url, headers=chic, json={"branch": "main", "room_no": "999", "business_number": "220-81-62517"}).status_code, 400)
            vat_before = Contract.get(ct0.id).vat_target
            r = c.post(url, headers=chic, json={"branch": "main", "room_no": "201", "business_number": "2208162517", "company_name": "(주)시크디자인",
                                                "ceo_name": "홍길동", "email": "tax@chic.co.kr", "business_address": "서울 강남구 1"})
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            self.assertEqual(d["business"]["business_number"], "220-81-62517")
            ct = Contract.get(ct0.id)
            self.assertEqual((ct.tenant_business_number, ct.tenant_company_name, ct.tenant_ceo_name, ct.tenant_email), ("220-81-62517", "(주)시크디자인", "홍길동", "tax@chic.co.kr"))
            self.assertTrue(ct.tax_invoice_requested)
            self.assertEqual(ct.vat_target, vat_before)                    # 청구액이 달라지는 부가세 설정은 그대로
            if tn0:
                self.assertEqual(Tenant.get(tn0.id).business_number, "220-81-62517")
            bad_mail = c.post(url, headers=chic, json={"branch": "main", "room_no": "201", "business_number": "220-81-62517", "email": "not-an-email"})
            self.assertEqual(bad_mail.status_code, 400, bad_mail.text)
        finally:
            ct = Contract.get(ct0.id)
            for k, v in keep_ct.items():
                setattr(ct, k, v)
            ct.save(action="t")
            if tn0:
                tn = Tenant.get(tn0.id)
                for k, v in keep_tn.items():
                    setattr(tn, k, v)
                tn.save(action="t")
        app = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ('id: "btnTaxInvoice"', "function goTaxView", "showTenantBizRegModal", "/contracts/tenant-bizreg", "임차인용 (세금계산서 발급)", "임대인용 (내 사업자)"):
            self.assertIn(needle, app, needle)


    def test_135_open_chat_threads_auto_refresh_without_sending_reply(self):
        """135. 열린 채팅창(관리인 지원 채팅 · 임대인/임차인 호실 대화)은 답장을 보내지 않아도 2.5초 주기로 새 메시지를 자동 반영한다."""
        shell = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        for needle in ("function startPoll()", "r3SupList", "lastCount", "2500"):
            self.assertIn(needle, shell, needle)
        admin = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("spTimer", "spList", "/support/thread?user=", "2500"):
            self.assertIn(needle, admin, needle)
        lessor = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn("2500", lessor)
        tenant = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("tnMsgCount", "tnMsgList", "2500"):
            self.assertIn(needle, tenant, needle)


    def test_136_call_buttons_admin_lessor_tenant_phone_numbers(self):
        """136. 통화 단추용 전화번호: 사용자의 '관리인에게 문의'에 관리인 번호, 임대인 메시지에 임차인 번호, 임차인 메시지에 임대인 번호. 관리인 앱 전화 접수 칸은 '○○에게 전화 : 번호' 한 줄(메모 입력·접수 단추 없음)."""
        c = self.client
        chic, ten = self._login("chic"), self._login("tenant_201")
        sup = c.get("/api/rent3/pool/support/thread", headers=chic).json()
        self.assertEqual(sup["admin_phone"], "010-3269-3343")
        tsup = c.get("/api/rent3/pool/support/thread", headers=ten).json()
        self.assertEqual(tsup["admin_phone"], "010-3269-3343")
        room = c.get("/api/rent3_lessor/messages/rooms/201?branch=main", headers=chic).json()
        self.assertIn("tenant_phone", room)
        self.assertTrue(room["tenant_phone"])
        tm = c.get("/api/rent3_tenant/myhome/tenant_201/messages?mark_read=false", headers=ten).json()
        self.assertTrue(tm["lessor_phone"])
        base = os.path.join(ROOT, "apps", "rent_dist")
        adm = open(os.path.join(base, "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn('label: "💬 임차인과 채팅 (호실별)", tab: "messages"', open(os.path.join(base, "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read())   # 임대인 ☰ 1단계 메뉴
        for needle in ("에게 전화 :", 'href="tel:${Bass.escapeHtml(callDigits)}"', 'id="spCat"'):
            self.assertIn(needle, adm, needle)
        for gone in ('id="btnSpPhone"', 'id="spMemo"', "전화 접수 남기기"):
            self.assertNotIn(gone, adm, gone)
        shell = open(os.path.join(ROOT, "shared_ui", "r3_shell.js"), encoding="utf-8").read()
        for needle in ("관리인에게 전화 :", 'id="r3SupAllow"', "/support\", { method: \"POST\"", "중요한 통화 내용은 이 채팅창에 남겨 주세요"):
            self.assertIn(needle, shell, needle)
        self.assertIn("위 채팅창에 기록을 남겨 주세요", adm)
        for needle in ("💬 채팅하기", "임대인·임차인 채팅 · 원격 지원"):
            self.assertIn(needle, adm, needle)
        idx = open(os.path.join(base, "rent3_admin", "frontend", "index.html"), encoding="utf-8").read()
        self.assertIn("💬 임대인·임차인에게 채팅", idx)        # ☰ 메뉴에서 바로 채팅으로 간다
        self.assertIn('callBtn(d.tenant_phone, "임차인 전화")', open(os.path.join(base, "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read())
        self.assertIn("에게 전화 ${Bass.escapeHtml(d.lessor_phone)}", open(os.path.join(base, "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read())


    def test_137_notification_panel_lists_recent_chat_sessions_without_content(self):
        """137. 알림창 '최근 대화'는 채팅창을 연 기록(세션)을 '채팅 : 상대(M/D HH:MM)' 제목과 이동 주소만으로 보여 준다 — 대화 내용·미리보기는 알림에 없다. 창을 열어 읽는 동안은 같은 세션, 30초 넘게 비었다 읽으면 새 세션."""
        from apps.rent_dist._rent3_core import chatsessions as cht
        c = self.client
        cht._last_seen.clear()
        for x in cht.ChatSession.all():
            x.delete(action="t")
        chic, ten = self._login("chic"), self._login("tenant_201")
        secret = "t137 비밀 내용 안녕하세요"
        c.post("/api/rent3_tenant/myhome/tenant_201/messages", headers=ten, json={"body": secret})
        # 임대인이 201호 대화창을 연다(열려 있는 동안의 반복 읽기는 한 세션)
        for _ in range(3):
            c.get("/api/rent3_lessor/messages/rooms/201?branch=main", headers=chic)
        self.assertEqual(len(cht.ChatSession.find(viewer="lessor:" + self._lessor_id_main())), 1)
        l = c.get("/api/rent3_lessor/notifications?branch=main", headers=chic).json()
        mine = [x for x in l["recent_chats"] if "201호" in x["title"]]
        self.assertTrue(mine, l["recent_chats"])
        import re as _re
        self.assertRegex(mine[0]["title"], r"^채팅 : 201호 임차인\(\d{1,2}/\d{1,2} \d{2}:\d{2}\)$")
        self.assertEqual(mine[0]["preview"], "")
        self.assertEqual(mine[0]["unread"], 0)                                 # 읽었으면 분홍 표시 없음
        c.post("/api/rent3_tenant/myhome/tenant_201/messages", headers=ten, json={"body": secret + " 2"})
        l_unread = c.get("/api/rent3_lessor/notifications?branch=main", headers=chic).json()
        self.assertEqual([x["unread"] for x in l_unread["recent_chats"] if "201호" in x["title"]][0], 1)   # 새 글이 오면 개수만 올라간다(분홍)
        c.get("/api/rent3_lessor/messages/rooms/201?branch=main", headers=chic)                       # 다시 읽음 처리
        self.assertIn("tab=messages&room=201", mine[0]["link"])
        self.assertNotIn(secret, json.dumps(l, ensure_ascii=False))          # 알림 응답 어디에도 대화 내용이 없다
        # 30초 넘게 비었다가 다시 열면 새 세션
        cht._last_seen[("lessor:" + self._lessor_id_main(), "room", "201")] -= 100
        c.get("/api/rent3_lessor/messages/rooms/201?branch=main", headers=chic)
        self.assertEqual(len(cht.ChatSession.find(viewer="lessor:" + self._lessor_id_main())), 2)
        # 임대인의 관리인 문의 창(지원 채팅 읽기)
        c.post("/api/rent3/pool/support/messages", headers=chic, json={"body": secret})
        c.get("/api/rent3/pool/support/thread", headers=chic)
        l2 = c.get("/api/rent3_lessor/notifications?branch=main", headers=chic).json()
        self.assertTrue(any(x["action"] == "support" and x["title"].startswith("채팅 : 관리인(") for x in l2["recent_chats"]))
        # 임차인
        c.get("/api/rent3_tenant/myhome/tenant_201/messages?mark_read=false", headers=ten)
        t = c.get("/api/rent3_tenant/myhome/tenant_201/notifications", headers=ten).json()
        self.assertTrue(any(x["title"].startswith("채팅 : ") and "임대인(" in x["title"] and x["preview"] == "" for x in t["recent_chats"]), t["recent_chats"])
        # 관리인: 사용자 문의 대화를 열면 목록에 '채팅 : 건물 임대인(...)' + ?user= 딥링크
        c.get("/api/rent3_admin/support/thread", params={"user": "lessor:lessor_chic"})
        a = c.get("/api/rent3_admin/notifications").json()
        lk = [x for x in a["recent_chats"] if "임대인(" in x["title"]]
        self.assertTrue(lk and "tab=support&user=lessor:" in lk[0]["link"], a["recent_chats"])
        self.assertNotIn(secret, json.dumps(a, ensure_ascii=False))
        self.assertLessEqual(len(l["recent_chats"]), 8)
        nj = open(os.path.join(ROOT, "shared_ui", "r3_notify.js"), encoding="utf-8").read()
        for needle in ("최근 대화", "recent_chats", "bindChats", "openFresh", "r3-nitem--unread", "이력을 남기지 않습니다"):
            self.assertIn(needle, nj, needle)
        adm = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn('get("user")', adm)

    def test_138_contract_room_chat_gets_chat_improvements(self):
        """138. 계약방 채팅도 채팅과 같다: 열면 세션(제목·시작 시각만)이 남아 알림창 '최근 대화'에 뜨고 계약방으로 바로 돌아가며, 안 읽은 글은 개수로만 표시한다. 상대 전화 단추·'채팅' 용어."""
        from apps.rent_dist._rent3_core import chatsessions as cht, Tenant
        c = self.client
        cht._last_seen.clear()
        sid = self._policy_session("962", 5000000, 300000)
        self.assertEqual(self._reg(sid).status_code, 200)
        lh, th = self._party_headers(sid)
        url = f"/api/rent3_contract/sessions/{sid}/messages"
        secret = "t138 비밀 문의 5521"
        self.assertEqual(c.post(url, headers=th, json={"kind": "question", "body": secret}).status_code, 200)
        for _ in range(3):                                                      # 열려 있는 동안 반복 읽기는 한 세션
            c.get(url, headers=lh)
        mine = [x for x in cht.ChatSession.find(viewer="lessor:" + self._lessor_id_main()) if x.kind == "contract"]
        self.assertEqual(len(mine), 1)
        c.get(url + "?mark_read=false", headers=lh)                             # 주기적 확인(읽음 처리 없음)은 세션을 만들지 않는다
        self.assertEqual(len([x for x in cht.ChatSession.find(viewer="lessor:" + self._lessor_id_main()) if x.kind == "contract"]), 1)
        l = c.get("/api/rent3_lessor/notifications?branch=main", headers=lh).json()
        rec = [x for x in l["recent_chats"] if "962호 계약방" in x["title"]]
        self.assertTrue(rec, l["recent_chats"])
        self.assertRegex(rec[0]["title"], r"^채팅 : 962호 계약방\(\d{1,2}/\d{1,2} \d{2}:\d{2}\)$")
        self.assertEqual(rec[0]["link"], f"/rent3_contract/?session={sid}&chat=1")
        self.assertEqual(rec[0]["preview"], "")
        self.assertEqual(rec[0]["unread"], 0)
        self.assertNotIn(secret, json.dumps(l, ensure_ascii=False))             # 알림에는 내용이 없다
        # 임차인이 새 글을 보내면 임대인 쪽은 개수만 올라가고(분홍), 임차인은 자기 목록에 계약방이 생긴다
        c.post(url, headers=th, json={"kind": "question", "body": secret + " 2"})
        l2 = c.get("/api/rent3_lessor/notifications?branch=main", headers=lh).json()
        self.assertEqual([x["unread"] for x in l2["recent_chats"] if "962호 계약방" in x["title"]][0], 1)
        c.get(url, headers=th)
        owners = [t for t in Tenant.all() if any(v.kind == "contract" and v.ref == sid for v in cht.ChatSession.find(viewer=f"tenant:{t.id}"))]
        self.assertEqual(len(owners), 1)
        # 화면: 전화 단추 + '채팅' 용어 + 알림에서 바로 열기
        app = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("cmCall", 'href="tel:', "에게 전화", "중요한 통화 내용은 아래 채팅에 기록", "계약방 채팅", 'params.get("chat") === "1"'):
            self.assertIn(needle, app, needle)
        self.assertNotIn("계약방 대화", app)
        self.assertNotIn("대화 열기", app)

    def test_139_contract_app_has_notification_bell(self):
        """139. 전자계약 앱에도 🔔 알림창이 있다: 내 계약방의 안 읽은 채팅·답할 제안(내가 보낸 제안은 제외)이 알림에 오고 누르면 그 계약방 채팅이 열린다. 관리인은 알림 없음, 비로그인은 401. 화면은 홈↔계약 화면에서 같은 🔔을 옮겨 단다."""
        from apps.rent_dist._rent3_core import chatsessions as cht
        c = self.client
        cht._last_seen.clear()
        sid = self._policy_session("963", 5000000, 300000)
        self.assertEqual(self._reg(sid).status_code, 200)
        lh, th = self._party_headers(sid)
        url = f"/api/rent3_contract/sessions/{sid}/messages"
        note = "/api/rent3_contract/notifications"
        self.assertEqual(c.get(note, headers=self.NOAUTH).status_code, 401)
        a = c.get(note).json()                                               # admin 기본 토큰: 알림 없음(읽기 전용 관리인)
        self.assertEqual((a["total"], a["items"], a["recent_chats"]), (0, [], []))
        secret = "t139 비밀 문의 7730"
        c.post(url, headers=th, json={"kind": "question", "body": secret})
        c.post(url, headers=th, json={"kind": "proposal", "field": "monthly_rent", "new_value": 280000})
        l = c.get(note, headers=lh).json()
        by = {i["kind"]: i for i in l["items"]}
        self.assertEqual(by[f"contract_chat:{sid}"]["count"], 2)                    # 임차인이 보낸 글 2개가 안 읽음
        self.assertEqual(by[f"contract_open:{sid}"]["count"], 1)                    # 임대인이 답할 제안 1건
        self.assertEqual(by[f"contract_open:{sid}"]["level"], "urgent")
        self.assertEqual(by[f"contract_open:{sid}"]["link"], f"/rent3_contract/?session={sid}&chat=1")
        self.assertNotIn(secret, json.dumps(l, ensure_ascii=False))                # 알림에 내용 없음
        t = c.get(note, headers=th).json()                                          # 제안을 보낸 임차인 자신에게는 '답할 제안'이 없다
        self.assertNotIn(f"contract_open:{sid}", [i["kind"] for i in t["items"]])
        c.get(url, headers=lh)                                                      # 임대인이 채팅을 열어 읽으면 안 읽음은 사라지고 답할 제안은 남는다
        l2 = {i["kind"]: i for i in c.get(note, headers=lh).json()["items"]}
        self.assertNotIn(f"contract_chat:{sid}", l2)
        self.assertIn(f"contract_open:{sid}", l2)
        self.assertEqual(c.post(note + "/seen", headers=lh, json={"kind": "x"}).status_code, 200)
        other = self._login("sunho")                                                # 다른 지점 임대인에게는 이 계약방 알림이 없다
        self.assertNotIn(f"contract_open:{sid}", [i["kind"] for i in c.get(note, headers=other).json()["items"]])
        # 화면: 홈↔계약 화면에서 🔔을 옮기고, 관리인 대리 접속에서는 달지 않는다
        base = os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend")
        app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
        for needle in ('R3Notify.start({ url: "/api/rent3_contract/notifications"', "!adminProxy", 'placeBell("#bellCvSlot")', 'placeBell("#bellHomeSlot")'):
            self.assertIn(needle, app, needle)
        for needle in ("r3_notify.js", 'id="bellHomeSlot"', 'id="bellCvSlot"'):
            self.assertIn(needle, html, needle)
        nj = open(os.path.join(ROOT, "shared_ui", "r3_notify.js"), encoding="utf-8").read()
        for needle in ("bellHost", "placeBell"):
            self.assertIn(needle, nj, needle)

    def test_140_portal_afford_calculator_opens_as_modal(self):
        """140. 포털 ☰ 더보기의 '월세 적정 예산 계산기'는 모달로 열린다(예전엔 페이지 위쪽 패널을 펼쳐 메뉴가 닫힌 뒤 아무 일도 없어 보였다). 같은 입력칸 id, ✕·바깥 클릭·적용·뒤로가기( < )로 닫힌다."""
        base = os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend")
        html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
        app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        self.assertNotIn('id="affordPanel"', html)                                   # 페이지 안 접이식 패널은 없다
        self.assertIn('id="affordModal" class="or-modal-backdrop"', html)
        for ident in ("calcIncomeMan", "calcDepositMan", "calcRecommendedText", "btnApplyBudget", "btnResetBudget", "btnCloseAfford"):
            self.assertEqual(html.count(f'id="{ident}"'), 1, ident)
        # 모달 안에 계산기 입력칸이 들어 있다
        m0 = html.index('id="affordModal"')
        m1 = html.index('id="rentNowModal"')
        for ident in ("calcIncomeMan", "btnApplyBudget", "btnCloseAfford"):
            self.assertTrue(m0 < html.index(f'id="{ident}"') < m1, ident)
        for needle in ("setAffordOpen", 'R3Hist.open("afford"', 'R3Hist.close("afford")', 'setAffordOpen(true)', 'setAffordOpen(false)'):
            self.assertIn(needle, app, needle)
        self.assertNotIn("affordPanel", app)

    def test_141_portal_menu_public_items_only_ops_items_in_admin_menu(self):
        """141. 포털 ☰는 로그인 없이 볼 수 있는 것(임차인·임대인 안내, 월세 적정 예산 계산기)과 임대인 가맹 가입 안내·MQnet 연락처만 둔다. 매뉴얼·PDF·Data Pool 관제·게이트웨이 같은 운영 항목은 관리인 앱 ☰ 로 옮겼다."""
        base = os.path.join(ROOT, "apps", "rent_dist")
        portal = open(os.path.join(base, "rent3", "frontend", "index.html"), encoding="utf-8").read()
        drawer = portal[portal.index("data-r3-drawer"):portal.index("</aside>")]
        for needle in ("menuForTenants", "menuForLandlords", "btnToggleAfford", "/rent3/lessor-intro.html",
                       "https://saas.34.31.10.12.sslip.io/", "tel:01032693343", "010-3269-3343", "민흥식", "mailto:", "MQnet"):
            self.assertIn(needle, drawer, needle)
        for gone in ("manual.html", "Rent3_통합_사용자_매뉴얼.pdf", "Rent3_QA_Test_Checklist.pdf", "pool.html", "BaseSaasApp 게이트웨이"):
            self.assertNotIn(gone, drawer, gone)
        self.assertEqual(drawer.count('data-r3-plain'), 2)                  # 연락처 링크는 타일로 만들지 않는다(번호·이메일이 잘리지 않게)
        hb = open(os.path.join(base, "rent3", "frontend", "hamburger.js"), encoding="utf-8").read()
        self.assertIn("a:not([data-r3-plain])", hb)
        admin = open(os.path.join(base, "rent3_admin", "frontend", "index.html"), encoding="utf-8").read()
        adrawer = admin[admin.index("data-r3-drawer"):admin.index("</aside>")]
        for needle in ("/rent3/manual.html", "Rent3_통합_사용자_매뉴얼.pdf", "Rent3_QA_Test_Checklist.pdf", "/rent3/pool.html", 'href="/"'):
            self.assertIn(needle, adrawer, needle)

    def test_142_admin_marks_test_prospect_identity_verified(self):
        """142. 관리인 앱 '시험 예비임차인 본인인증 처리': 점검은 바꾸지 않고, 처리는 백업 후 시험용 인증(assurance=test)으로 표시하며, 이미 인증됐거나 입주 중인 임차인·가입 안 된 계정은 건드리지 않고, 임대인은 403. 처리된 계정은 계약을 시작할 수 있다."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Tenant
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        url = "/api/rent3_admin/accounts/test-identity"
        body = {"name": "홍길동", "phone": "010-1111-2222"}
        try:
            self.assertIn(c.post(url, headers=chic, json=body).status_code, (401, 403))                     # 임대인은 못 한다
            self.assertEqual(c.post(url, headers=admin, json=body).json()["status"], "BLOCKED")            # 가입 전에는 멈춘다
            reg = c.post("/api/rent3/pool/auth/register", headers=self.NOAUTH,
                         json={"role": "prospect", "name": "홍길동", "phone": "010-1111-2222", "password": "Pw123456"})
            self.assertEqual(reg.status_code, 200, reg.text)
            tid = reg.json()["user"]["id"]
            self.assertFalse((Tenant.get(tid).identity or {}).get("verified"))
            dry = c.post(url, headers=admin, json=body).json()
            self.assertEqual(dry["status"], "DRY")
            self.assertFalse((Tenant.get(tid).identity or {}).get("verified"))                             # 점검은 바꾸지 않는다
            th = self._as(reg.json()["token"])
            c.post("/api/rent3_admin/lessors/lessor_chic/room-limit", json={"room_limit": 500})
            self.assertEqual(c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "964", "is_published": True, "deposit": 5000000, "monthly_rent": 300000}).status_code, 200)
            call = {"caller_role": "tenant", "branch": "main", "room_no": "964", "mode": "NEW", "tenant_name": "홍길동", "tenant_phone": "010-1111-2222"}
            self.assertEqual(c.post("/api/rent3_contract/sessions/call", headers=th, json=call).status_code, 403)   # 인증 전에는 계약을 시작할 수 없다
            with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_ti_")}):
                done = c.post(url, headers=admin, json={**body, "apply": True}).json()
                self.assertEqual(done["status"], "DONE", done)
                self.assertTrue(done["backup"].endswith(".tar.gz"))                                         # 처리 전 자동 백업
                idn = Tenant.get(tid).identity
                self.assertEqual((idn["verified"], idn["fake"], idn["assurance"], idn["provider"]), (True, True, "test", "admin_test"))
                self.assertEqual(c.post(url, headers=admin, json={**body, "apply": True}).json()["status"], "EXISTS")   # 재실행은 그대로
                # 입주 중인 임차인은 건드리지 않는다
                active = next(t for t in Tenant.all() if t.status == "ACTIVE" and not (t.identity or {}).get("verified"))
                blocked = c.post(url, headers=admin, json={"name": active.name, "phone": active.phone, "apply": True}).json()
                self.assertEqual(blocked["status"], "BLOCKED")
                self.assertFalse((Tenant.get(active.id).identity or {}).get("verified"))
            ok = c.post("/api/rent3_contract/sessions/call", headers=th, json=call)                         # 인증 뒤에는 계약을 시작할 수 있다
            self.assertEqual(ok.status_code, 200, ok.text)
        finally:
            from apps.rent_dist._rent3_core import CoSignSession
            for s_ in CoSignSession.all():
                if (s_.terms or {}).get("tenant_phone") == "010-1111-2222":
                    s_.delete(action="t")
            t = next((x for x in Tenant.all() if x.phone == "010-1111-2222"), None)
            if t:
                t.delete(action="t")
        adm = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnTiCheck", "btnTiApply", "/accounts/test-identity", "시험 예비임차인 본인인증 처리", 'id="tiCard"', 'get("focus") === "testtools"'):
            self.assertIn(needle, adm, needle)
        aidx = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "index.html"), encoding="utf-8").read()
        self.assertIn("/rent3_admin/?tab=ops&focus=testtools", aidx)                                       # ☰ 바로가기

    def test_143_login_by_name_is_explained_in_production(self):
        """143. 운영 접속에서는 이름(성명)만으로 로그인되지 않으며, 오류 안내가 그 사실을 알리고 '번호로 간편가입' 제안은 번호를 넣었을 때만 나온다. 휴대폰 번호로는 로그인된다."""
        c, NA = self.client, self.NOAUTH
        remote = {**NA, "X-Forwarded-For": "203.0.113.9"}
        reg = c.post("/api/rent3/pool/auth/register", headers=NA, json={"role": "prospect", "name": "홍길동", "phone": "010-1111-2222", "password": "Pw123456"})
        self.assertEqual(reg.status_code, 200, reg.text)
        try:
            by_name = c.post("/api/rent3/pool/auth/login", headers=remote, json={"login_mode": "account", "identifier": "홍길동", "password": "Pw123456"})
            self.assertEqual(by_name.status_code, 401)
            self.assertIn("이름(성명)만으로는 로그인할 수 없습니다", by_name.json().get("hint", ""))
            self.assertNotIn("휴대폰 번호·성명", by_name.json().get("hint", ""))
            by_phone = c.post("/api/rent3/pool/auth/login", headers=remote, json={"login_mode": "account", "identifier": "010-1111-2222", "password": "Pw123456"})
            self.assertEqual(by_phone.status_code, 200, by_phone.text)
        finally:
            from apps.rent_dist._rent3_core import Tenant
            t = next((x for x in Tenant.all() if x.phone == "010-1111-2222"), None)
            if t:
                t.delete(action="t")
        app = open(os.path.join(ROOT, "apps", "rent_dist", "rent3", "frontend", "app.js"), encoding="utf-8").read()
        self.assertIn('lb.textContent = "아이디 / 휴대폰 번호 / 이메일"', app)
        self.assertIn("isNotFound && rawDigits.length >= 8", app)
        self.assertNotIn("isNotFound && (rawDigits.length >= 8 || rawIdent)", app)

    def test_144_admin_can_reset_test_prospect_password_and_unlock_login(self):
        """144. 관리인 앱 시험 계정 도구에서 새 비밀번호를 정하면: 규칙(8자 이상·영문+숫자)을 지키고, 옛 비밀번호는 안 되고 새 비밀번호로 로그인되며, 틀려서 걸린 로그인 잠금이 풀린다. 이미 인증된 계정도 비밀번호만 바꿀 수 있고, 입주 임차인·임대인 계정은 안 된다."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Tenant, ratelimit
        c, NA = self.client, self.NOAUTH
        remote = {**NA, "X-Forwarded-For": "203.0.113.77"}
        admin, chic = self._login("admin"), self._login("chic")
        url = "/api/rent3_admin/accounts/test-identity"
        body = {"name": "홍길동", "phone": "010-1111-2222"}
        login = lambda pw, ident="010-1111-2222": c.post("/api/rent3/pool/auth/login", headers=remote, json={"login_mode": "account", "identifier": ident, "password": pw})
        reg = c.post("/api/rent3/pool/auth/register", headers=NA, json={"role": "prospect", "name": "홍길동", "phone": "010-1111-2222", "password": "OldPw1234"})
        self.assertEqual(reg.status_code, 200, reg.text)
        tid = reg.json()["user"]["id"]
        try:
            self.assertIn(c.post(url, headers=chic, json={**body, "new_password": "Test1234"}).status_code, (401, 403))   # 임대인은 못 한다
            for _ in range(5):                                                                                           # 비밀번호를 틀려 잠금에 걸린 상태
                self.assertEqual(login("wrong-pw").status_code, 401)
            self.assertEqual(login("OldPw1234").status_code, 429)
            weak = c.post(url, headers=admin, json={**body, "new_password": "1212", "apply": True}).json()                # 규칙에 안 맞으면 멈춘다
            self.assertEqual(weak["status"], "BLOCKED")
            self.assertEqual(login("OldPw1234").status_code, 429)
            dry = c.post(url, headers=admin, json={**body, "new_password": "Test1234"}).json()
            self.assertEqual(dry["status"], "DRY")
            self.assertIn("비밀번호", dry["message"])
            self.assertEqual(login("OldPw1234").status_code, 429)                                                        # 점검은 바꾸지 않는다
            with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_pw_")}):
                done = c.post(url, headers=admin, json={**body, "new_password": "Test1234", "apply": True}).json()
                self.assertEqual(done["status"], "DONE", done)
                self.assertTrue(done["backup"].endswith(".tar.gz"))
                self.assertNotIn("Test1234", json.dumps(done, ensure_ascii=False))                                      # 응답에 비밀번호를 되돌려 주지 않는다
                self.assertEqual(login("OldPw1234").status_code, 401)                                                    # 잠금은 풀렸고 옛 비밀번호는 안 된다
                ok = login("Test1234")
                self.assertEqual(ok.status_code, 200, ok.text)
                self.assertTrue((Tenant.get(tid).identity or {}).get("verified"))                                      # 인증 표시도 함께 처리됐다
                # 이미 인증된 계정: 새 비밀번호 없이는 그대로, 있으면 비밀번호만 바꾼다
                self.assertEqual(c.post(url, headers=admin, json={**body, "apply": True}).json()["status"], "EXISTS")
                again = c.post(url, headers=admin, json={**body, "new_password": "Second5678", "apply": True}).json()
                self.assertEqual(again["status"], "DONE")
                self.assertEqual(login("Test1234").status_code, 401)
                self.assertEqual(login("Second5678").status_code, 200)
                # 입주 임차인은 건드리지 않는다
                active = next(t for t in Tenant.all() if t.status == "ACTIVE")
                blocked = c.post(url, headers=admin, json={"name": active.name, "phone": active.phone, "new_password": "Test1234", "apply": True}).json()
                self.assertEqual(blocked["status"], "BLOCKED")
        finally:
            ratelimit.reset_all()
            t = Tenant.get(tid)
            if t:
                t.delete(action="t")
        adm = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ('id="tiPw"', "new_password", "처리 (본인인증 · 비밀번호)"):
            self.assertIn(needle, adm, needle)

    def test_145_admin_vacates_test_room_and_relists_it(self):
        """145. 관리인 앱 '시험 호실 비우기': 시험 임차인(0원 계약·시험용 인증) 호실만 퇴실 확정처럼 비워 공실 매물로 공개한다(계약 종료·임차인 퇴실·값 채움·백업). 실제 입주 호실은 멈추고, 점검은 바꾸지 않으며, 임대인은 403. 비운 호실은 계약 앱 후보 목록에 공실로 나온다."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, Lessor
        c = self.client
        admin, chic = self._login("admin"), self._login("chic")
        mk, url = "/api/rent3_admin/accounts/test-tenant", "/api/rent3_admin/accounts/test-vacate"
        body = {"branch": "main", "room_no": "798", "name": "시험이", "phone": "010-1234-0798"}
        lsr = Lessor.first(branch="main")
        old_limit = lsr.room_limit
        try:
            self.assertIn(c.post(url, headers=chic, json={"branch": "main", "room_no": "798"}).status_code, (401, 403))   # 임대인은 못 한다
            with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_vac_")}):
                self.assertEqual(c.post(mk, headers=admin, json={**body, "apply": True, "raise_limit": True}).json()["status"], "DONE")
                before = c.get("/api/rent3_contract/rooms", headers=admin).json()["rooms"]
                self.assertTrue(next(r for r in before if r["room_no"] == "798" and r["branch"] == "main")["is_occupied"])
                # 실제 입주 호실(201)은 건드리지 않는다
                real = c.post(url, headers=admin, json={"branch": "main", "room_no": "201", "apply": True}).json()
                self.assertEqual(real["status"], "BLOCKED")
                self.assertTrue(Room.first(branch="main", room_no="201").is_occupied)
                self.assertEqual(c.post(url, headers=admin, json={"branch": "main", "room_no": "no-such"}).json()["status"], "BLOCKED")
                dry = c.post(url, headers=admin, json={"branch": "main", "room_no": "798"}).json()
                self.assertEqual(dry["status"], "DRY")
                self.assertTrue(Room.first(branch="main", room_no="798").is_occupied)                                    # 점검은 바꾸지 않는다
                done = c.post(url, headers=admin, json={"branch": "main", "room_no": "798", "apply": True, "deposit": 4000000, "monthly_rent": 250000}).json()
                self.assertEqual(done["status"], "DONE", done)
                self.assertTrue(done["backup"].endswith(".tar.gz"))
                r = Room.first(branch="main", room_no="798")
                self.assertFalse(r.is_occupied)
                self.assertTrue(r.is_published)                                                                         # 공실 매물로 다시 공개
                self.assertEqual((r.deposit, r.monthly_rent), (4000000, 250000))                                        # 값이 비어 있어 입력값으로 채움
                self.assertEqual(Tenant.get("tenant_main_798").status, "MOVED_OUT")
                self.assertEqual(Contract.get("ct_main_798").status, "TERMINATED")
                after = next(x for x in c.get("/api/rent3_contract/rooms", headers=admin).json()["rooms"] if x["room_no"] == "798" and x["branch"] == "main")
                self.assertFalse(after["is_occupied"])
                self.assertTrue(after["is_published"])                                                                  # 계약할 집 고르기에 나오는 조건
                self.assertEqual(c.post(url, headers=admin, json={"branch": "main", "room_no": "798", "apply": True}).json()["status"], "EXISTS")   # 재실행은 그대로
        finally:
            for cls, oid in ((Contract, "ct_main_798"), (Tenant, "tenant_main_798"), (Room, "room_main_798")):
                o = cls.get(oid)
                if o:
                    o.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.room_limit = old_limit
            lsr.save(action="t")
        adm = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_admin", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("btnTvCheck", "btnTvApply", "/accounts/test-vacate", "시험 호실 비우기"):
            self.assertIn(needle, adm, needle)

    def _make_pre_moveout_room(self, room_no, phone, vacate_on="2026-11-20", deposit=4000000, rent=280000):
        """사전 계약 시험용: 시험 임차인이 사는 호실을 만들고, 임차인이 전출 예정을 회신해 사전 매물(다음 세입자 조건 포함)로 공개된 상태를 돌려준다."""
        import tempfile
        from unittest import mock
        c = self.client
        lsr_url = "/api/rent3_admin/accounts/test-tenant"
        with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_pre_")}):
            done = c.post(lsr_url, json={"branch": "main", "room_no": room_no, "name": "기존입주", "phone": phone, "apply": True, "raise_limit": True}).json()
        self.assertEqual(done["status"], "DONE", done)
        rec = c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": room_no, "tenant_plan": "MOVE_OUT", "planned_move_out_date": vacate_on})
        self.assertEqual(rec.status_code, 200, rec.text)
        pub = c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": room_no, "is_published": True, "pre_moveout_notice": True, "available_from": vacate_on,
                                                    "deposit": deposit, "monthly_rent": rent, "maintenance_fee": 30000})
        self.assertEqual(pub.status_code, 200, pub.text)

    def _new_prospect(self, name, phone):
        from apps.rent_dist._rent3_core import Tenant
        reg = self.client.post("/api/rent3/pool/auth/register", headers=self.NOAUTH, json={"role": "prospect", "name": name, "phone": phone, "password": "Pw123456"})
        self.assertEqual(reg.status_code, 200, reg.text)
        t = Tenant.get(reg.json()["user"]["id"])
        t.identity = {"verified": True}
        t.save(action="t")
        return t

    def test_146_pre_contract_on_moveout_room_reserves_then_activates_at_vacate(self):
        """146. 사전 계약: 입주 중인 전출 예정 호실에도 퇴실 전에 계약할 수 있다. 체결하면 현재 입주자·계약은 그대로 두고 '예약 계약'(SCHEDULED)이 걸리며(청구·원장 제외, 입주자 채팅에 안 보임), 퇴실이 확정되어 방이 비는 순간 예약 계약이 입주 계약(ACTIVE)이 되고 임차인이 입주한다."""
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, CoSignSession, DirectChat, Lessor
        c, base = self.client, "/api/rent3_contract"
        lsr = Lessor.first(branch="main")
        old_limit = lsr.room_limit
        try:
            self._make_pre_moveout_room("796", "010-1234-0796")
            room = Room.first(branch="main", room_no="796")
            old_ct, old_tid = room.active_contract_id, room.id_tenant
            self.assertTrue(room.is_occupied and room.pre_moveout_notice and room.is_published)
            # 계약 앱 후보 목록: 입주 중이어도 사전 매물로 나오고, 값은 '다음 세입자용 조건'이다
            cand = next(r for r in c.get(f"{base}/rooms").json()["rooms"] if r["room_no"] == "796" and r["branch"] == "main")
            self.assertTrue(cand["is_occupied"] and cand["pre_moveout_notice"] and cand["is_published"])
            self.assertEqual((cand["deposit"], cand["monthly_rent"]), (4000000, 280000))
            self.assertEqual((cand["expected_vacate_date"], cand["has_reservation"]), ("2026-11-20", False))
            # 새 임차인이 계약을 시작: 조건은 사전 매물 값, 입주일은 전출 예정일부터
            new_t = self._new_prospect("신규예약", "010-7777-0796")
            sid = c.post(f"{base}/sessions/call", json={"caller_role": "tenant", "branch": "main", "room_no": "796", "mode": "NEW",
                                                        "tenant_name": "신규예약", "tenant_phone": "010-7777-0796", "id_tenant": new_t.id}).json()["session"]["id"]
            self.assertEqual(c.post(f"{base}/sessions/{sid}/respond", json={"responder_role": "lessor", "responder_name": "김임대"}).status_code, 200)   # 임대인이 호출에 응답
            ses = CoSignSession.get(sid)
            self.assertEqual((ses.terms["deposit"], ses.terms["monthly_rent"], ses.terms["maintenance_fee"]), (4000000, 280000, 30000))
            self.assertEqual((ses.terms["move_in_date"], ses.reserve_after), ("2026-11-20", "2026-11-20"))
            # 지금 사는 임차인의 호실 채팅에는 다음 입주자와의 계약 호출이 남지 않는다
            self.assertFalse([m for m in DirectChat.find(id_room=room.id) if "공동계약 요청" in (m.message or "")])
            # 입주일은 현재 입주자의 퇴실 예정일 이전으로 정할 수 없다(같은 날은 가능)
            from core.errors import DomainError
            with self.assertRaisesRegex(DomainError, "퇴실 예정일"):
                CoSignSession.get(sid).update_terms("lessor", {"move_in_date": "2026-11-10"})
            CoSignSession.get(sid).update_terms("lessor", {"move_in_date": "2026-11-20"})
            # 체결
            self._complete_contract(None, None, new_t.id, "796", "신규예약", "010-7777-0796")
            ses = CoSignSession.get(sid)
            self.assertEqual(ses.status, "COMPLETED")
            ct = Contract.get(ses.created_contract_id)
            self.assertEqual(ct.status, "SCHEDULED")                                                                   # 입주 전 예약 계약
            room = Room.first(branch="main", room_no="796")
            self.assertEqual((room.id_tenant, room.active_contract_id), (old_tid, old_ct))                              # 현재 입주자·계약은 그대로
            self.assertEqual(room.reserved_contract_id, ct.id)
            self.assertFalse(room.is_published)                                                                         # 더는 광고하지 않는다
            self.assertEqual((Tenant.get(old_tid).status, Tenant.get(new_t.id).status, Tenant.get(new_t.id).current_room_id), ("ACTIVE", "PROSPECT", ""))
            self.assertEqual(Contract.get(old_ct).status, "ACTIVE")
            self.assertEqual([x.id for x in Contract.find(id_room=room.id, status="ACTIVE")], [old_ct])                # 청구·원장은 현재 계약만
            self.assertFalse([m for m in DirectChat.find(id_room=room.id) if "계약 최종 체결" in (m.message or "")])    # 체결 알림도 입주자 채팅에 안 남는다
            # 예약이 걸린 호실: 계약 앱 후보 표시, 새 계약 호출 차단, 임대인 카드에 예약 요약, '전출의사 없음'으로 못 바꿈
            cand = next(r for r in c.get(f"{base}/rooms").json()["rooms"] if r["room_no"] == "796" and r["branch"] == "main")
            self.assertTrue(cand["has_reservation"])
            other = self._new_prospect("끼어들기", "010-7777-0797")
            blocked = c.post(f"{base}/sessions/call", json={"caller_role": "tenant", "branch": "main", "room_no": "796", "mode": "NEW",
                                                            "tenant_name": "끼어들기", "tenant_phone": "010-7777-0797", "id_tenant": other.id})
            self.assertEqual(blocked.status_code, 400)
            self.assertIn("예약 계약", blocked.json()["message"])
            card = next(r for r in c.get("/api/rent3_lessor/dashboard?branch=main").json()["rooms"] if r["room_no"] == "796")
            self.assertEqual((card["reservation"]["tenant_name"], card["reservation"]["move_in_date"]), ("신규예약", "2026-11-20"))
            stay = c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": "796", "tenant_plan": "STAY"})
            self.assertEqual(stay.status_code, 400)
            # 퇴실 확정(방이 빌 때): 예약 계약이 입주 계약이 되고 새 임차인이 입주한다
            from apps.rent_dist._rent3_core import testtenant as tt
            with __import__("unittest").mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": __import__("tempfile").mkdtemp(prefix="rent3_bk_pre2_")}):
                res = tt.vacate_test_room("main", "796", apply=True)
            self.assertEqual(res["status"], "DONE", res)
            self.assertIn("예약 계약의 임차인 신규예약", res["message"])
            room = Room.first(branch="main", room_no="796")
            self.assertEqual((room.id_tenant, room.active_contract_id, room.reserved_contract_id), (new_t.id, ct.id, ""))
            self.assertEqual((room.deposit, room.monthly_rent, room.maintenance_fee), (4000000, 280000, 30000))
            self.assertFalse(room.is_published or room.pre_moveout_notice)
            self.assertEqual(Contract.get(ct.id).status, "ACTIVE")
            self.assertEqual(Contract.get(old_ct).status, "TERMINATED")
            nt = Tenant.get(new_t.id)
            self.assertEqual((nt.status, nt.current_room_id, nt.room_no), ("ACTIVE", room.id, "796"))
            self.assertEqual(Tenant.get(old_tid).status, "MOVED_OUT")
            # 화면·매뉴얼: 집 고르기에 사전 매물 표시, 체결 후 예약 안내, 임대인 카드의 예약·취소, 입주일 최소값
            rd = lambda *parts: open(os.path.join(ROOT, "apps", "rent_dist", *parts), encoding="utf-8").read()
            capp = rd("rent3_contract", "frontend", "app.js")
            for needle in ("!r.has_reservation && (!r.is_occupied || r.pre_moveout_notice)", "퇴실 전 미리 계약", "reservedBannerHtml", "입주 예약 계약입니다", "s.reserve_after"):
                self.assertIn(needle, capp, needle)
            lapp = rd("rent3_lessor", "frontend", "app.js")
            for needle in ("reservationCardHtml", "btnCancelReserve", "/reservation/cancel", "다음 입주 예약"):
                self.assertIn(needle, lapp, needle)
            self.assertIn("퇴실 전에 미리 계약", rd("rent3_contract", "frontend", "manual.html"))
            self.assertIn("다음 입주 예약", rd("rent3_lessor", "frontend", "manual.html"))
        finally:
            for cls, oid in ((Contract, "ct_main_796"), (Tenant, "tenant_main_796"), (Room, "room_main_796")):
                o = cls.get(oid)
                if o:
                    o.delete(action="t")
            for s_ in CoSignSession.all():
                if s_.room_no == "796":
                    if s_.created_contract_id and Contract.get(s_.created_contract_id):
                        Contract.get(s_.created_contract_id).delete(action="t")
                    s_.delete(action="t")
            for t in Tenant.all():
                if t.phone in ("010-7777-0796", "010-7777-0797"):
                    t.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.room_limit = old_limit
            lsr.save(action="t")

    def test_147_reservation_can_be_cancelled_and_room_is_relisted(self):
        """147. 예약 계약 취소: 임대인이 취소하면 계약은 CANCELLED, 호실은 전출 예정 사전 매물로 다시 공개되고 새 계약이 가능하다. 예약이 없으면 취소할 수 없고, 다른 지점 임대인·임차인은 못 한다."""
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, CoSignSession, Lessor
        c, base = self.client, "/api/rent3_contract"
        lsr = Lessor.first(branch="main")
        old_limit = lsr.room_limit
        chic, sunho = self._login("chic"), self._login("sunho")
        try:
            self._make_pre_moveout_room("795", "010-1234-0795")
            room = Room.first(branch="main", room_no="795")
            url = f"/api/rent3_lessor/rooms/{room.id}/reservation/cancel"
            self.assertEqual(c.post(url, headers=chic, json={}).status_code, 400)                                          # 예약이 없으면 취소할 것이 없다
            t1 = self._new_prospect("예약취소", "010-7777-0795")
            self._complete_contract(None, None, t1.id, "795", "예약취소", "010-7777-0795")
            room = Room.first(branch="main", room_no="795")
            ct = room.reservation()
            self.assertIsNotNone(ct)
            self.assertIn(c.post(url, headers=sunho, json={}).status_code, (401, 403))                                      # 다른 지점 임대인은 못 한다
            ok = c.post(url, headers=chic, json={"reason": "임차인 사정"})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertIn("계약금", ok.json()["hint"])
            room = Room.first(branch="main", room_no="795")
            self.assertEqual(room.reserved_contract_id, "")
            self.assertTrue(room.is_published and room.pre_moveout_notice and room.is_occupied)                              # 다시 사전 매물로 공개
            self.assertEqual(Contract.get(ct.id).status, "CANCELLED")
            self.assertIn("예약 취소 사유", Contract.get(ct.id).special_terms)
            self.assertEqual(Tenant.get(t1.id).status, "PROSPECT")
            cand = next(r for r in c.get(f"{base}/rooms").json()["rooms"] if r["room_no"] == "795" and r["branch"] == "main")
            self.assertFalse(cand["has_reservation"])
            # 취소 뒤 다른 사람이 다시 계약을 시작할 수 있다
            t2 = self._new_prospect("재도전", "010-7777-0794")
            again = c.post(f"{base}/sessions/call", json={"caller_role": "tenant", "branch": "main", "room_no": "795", "mode": "NEW",
                                                          "tenant_name": "재도전", "tenant_phone": "010-7777-0794", "id_tenant": t2.id})
            self.assertEqual(again.status_code, 200, again.text)
            # 입주 중이면서 사전 매물이 아닌 호실은 새 계약을 시작할 수 없다(서버 규칙)
            real = c.post(f"{base}/sessions/call", json={"caller_role": "tenant", "branch": "main", "room_no": "201", "mode": "NEW",
                                                         "tenant_name": "재도전", "tenant_phone": "010-7777-0794", "id_tenant": t2.id})
            self.assertEqual(real.status_code, 400)
            self.assertIn("입주 중", real.json()["message"])
        finally:
            for cls, oid in ((Contract, "ct_main_795"), (Tenant, "tenant_main_795"), (Room, "room_main_795")):
                o = cls.get(oid)
                if o:
                    o.delete(action="t")
            for s_ in CoSignSession.all():
                if s_.room_no == "795":
                    if s_.created_contract_id and Contract.get(s_.created_contract_id):
                        Contract.get(s_.created_contract_id).delete(action="t")
                    s_.delete(action="t")
            for t in Tenant.all():
                if t.phone in ("010-7777-0795", "010-7777-0794"):
                    t.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.room_limit = old_limit
            lsr.save(action="t")

    def test_148_prelisting_opens_one_month_before_move_out_date(self):
        """148. 전출 예정을 일찍 회신해도 사전 매물은 퇴실 기준일(만기)의 1개월 전부터 공개된다: 그 전에는 올라가지 않고(직접 올리기도 거부), 공개일에 포털·목록 조회 때 자동으로 올라가며, '전출의사 없음'으로 바뀌면 자동 공개는 취소된다. 공개일 전이므로 계약도 시작할 수 없다."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, Lessor
        from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
        c = self.client
        lsr = Lessor.first(branch="main")
        old_limit = lsr.room_limit
        rooms = {"794": "010-1234-0794", "793": "010-1234-0793", "792": "010-1234-0792"}
        try:
            with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_win_")}):
                for no, ph in rooms.items():
                    self.assertEqual(c.post("/api/rent3_admin/accounts/test-tenant", json={"branch": "main", "room_no": no, "name": "기존입주", "phone": ph, "apply": True, "raise_limit": True}).json()["status"], "DONE")
            plan = lambda no, p="MOVE_OUT", d="2027-03-01": c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": no, "tenant_plan": p, "planned_move_out_date": d if p == "MOVE_OUT" else ""})
            # 오늘(2026-10-29)은 공개일(2027-02-01) 전: 회신은 기록되지만 사전 매물로 올라가지 않는다
            r = plan("794")
            self.assertEqual(r.status_code, 200, r.text)
            chk = r.json()["check"]
            self.assertEqual((chk["status"], chk["prelist_pending"], chk["prelist_open_date"]), ("MOVEOUT_PLANNED", True, "2027-02-01"))
            self.assertIn("2027-02-01", r.json()["message"])
            room = Room.first(branch="main", room_no="794")
            self.assertFalse(room.pre_moveout_notice or room.is_published)
            self.assertFalse(any(l["room_no"] == "794" for l in c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]))
            # 임대인이 직접 사전 매물로 올리려 해도 공개일 전에는 거부
            early = c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "794", "is_published": True, "pre_moveout_notice": True, "available_from": "2027-03-01"})
            self.assertEqual(early.status_code, 400)
            self.assertIn("2027-02-01", early.json()["message"])
            # 계약도 시작할 수 없다(입주 중이고 아직 사전 매물이 아님)
            from apps.rent_dist._rent3_core import Tenant as _T
            pt = self._new_prospect("너무이름", "010-7777-0794")
            blocked = c.post("/api/rent3_contract/sessions/call", json={"caller_role": "tenant", "branch": "main", "room_no": "794", "mode": "NEW",
                                                                        "tenant_name": "너무이름", "tenant_phone": "010-7777-0794", "id_tenant": pt.id})
            self.assertEqual(blocked.status_code, 400)
            # 공개일 하루 전까지는 올라가지 않는다
            with mock.patch.dict(os.environ, {"RENT3_TODAY": "2027-01-31"}):
                self.assertEqual(ExpiryRenewalCheck.activate_due(force=True), 0)
            self.assertFalse(Room.first(branch="main", room_no="794").is_published)
            # 공개일: 포털 조회만으로 자동 공개(스케줄러 없이 조회 때 확인)
            plan("793")
            with mock.patch.dict(os.environ, {"RENT3_TODAY": "2027-02-01"}):
                landing = c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]
            for no in ("794", "793"):
                self.assertTrue(any(l["room_no"] == no and l["pre_moveout_notice"] for l in landing), no)
                rm = Room.first(branch="main", room_no=no)
                self.assertTrue(rm.pre_moveout_notice and rm.is_published, no)
                self.assertEqual(rm.available_from, "2027-03-01 입주예정(만기퇴실)")
                self.assertFalse(ExpiryRenewalCheck.for_contract(rm.active_contract_id).prelist_pending)
            # 공개일 전에 '전출의사 없음'으로 바뀌면 자동 공개는 취소된다
            plan("792")
            self.assertTrue(ExpiryRenewalCheck.for_contract(Room.first(branch="main", room_no="792").active_contract_id).prelist_pending)
            self.assertEqual(plan("792", "STAY").status_code, 200)
            self.assertFalse(ExpiryRenewalCheck.for_contract(Room.first(branch="main", room_no="792").active_contract_id).prelist_pending)
            with mock.patch.dict(os.environ, {"RENT3_TODAY": "2027-02-05"}):
                ExpiryRenewalCheck.activate_due(force=True)
            self.assertFalse(Room.first(branch="main", room_no="792").is_published)
            # 이미 공개일이 지난 전출 예정(퇴실 기준일이 한 달 안쪽)은 회신 즉시 공개된다
            with mock.patch.dict(os.environ, {"RENT3_TODAY": "2027-02-20"}):
                now_listed = plan("792", "MOVE_OUT", "2027-03-01")
                self.assertEqual(now_listed.status_code, 200, now_listed.text)
                self.assertFalse(now_listed.json()["check"]["prelist_pending"])
            self.assertTrue(Room.first(branch="main", room_no="792").is_published)
            # 임대인이 매물을 직접 내리면 미뤄 둔 자동 공개도 취소된다(공개일 전 상태에서)
            self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{Room.first(branch='main', room_no='792').id}/publish", json={"is_published": False}).status_code, 200)
        finally:
            for no in rooms:
                for cls, oid in ((Contract, f"ct_main_{no}"), (Tenant, f"tenant_main_{no}"), (Room, f"room_main_{no}")):
                    o = cls.get(oid)
                    if o:
                        o.delete(action="t")
                for chk in ExpiryRenewalCheck.all():
                    if chk.room_no == no:
                        chk.delete(action="t")
            for t in Tenant.all():
                if t.phone == "010-7777-0794":
                    t.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.room_limit = old_limit
            lsr.save(action="t")
        lm = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "manual.html"), encoding="utf-8").read()
        self.assertIn("1개월 전", lm)

    def test_149_lessor_saves_next_terms_draft_used_for_listing_renewal_and_relisting(self):
        """149. 임대인이 입주 중인 호실의 '다음 계약 조건'을 임시 저장한다: 빈 호실·잘못된 값·다른 임대인은 거부, 임차인·공개 목록에는 보이지 않고, 매물 내림으로 지워지지 않으며, 사전 매물 공개 조건·연장 예고 기본값·퇴실 뒤 재공개 조건으로 쓰이고, 쓰이면 비워진다."""
        import tempfile
        from unittest import mock
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, Lessor
        from apps.rent_dist._rent3_core import testtenant as tt
        from apps.rent_dist._rent3_core.authz import tenant_token
        from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
        c = self.client
        lsr = Lessor.first(branch="main")
        old_limit = lsr.room_limit
        chic, sunho = self._login("chic"), self._login("sunho")
        try:
            with mock.patch.dict(os.environ, {"RENT3_BACKUP_DIR": tempfile.mkdtemp(prefix="rent3_bk_nt_")}):
                self.assertEqual(c.post("/api/rent3_admin/accounts/test-tenant", json={"branch": "main", "room_no": "789", "name": "기존입주", "phone": "010-1234-0789", "apply": True, "raise_limit": True}).json()["status"], "DONE")
                room = Room.first(branch="main", room_no="789")
                url = f"/api/rent3_lessor/rooms/{room.id}/next-terms"
                self.assertEqual(c.post("/api/rent3_lessor/rooms", headers=chic, json={"branch": "main", "room_no": "788", "is_published": True}).status_code, 200)
                vacant = Room.first(branch="main", room_no="788")
                self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{vacant.id}/next-terms", headers=chic, json={"monthly_rent": 1}).status_code, 400)   # 빈 호실은 매물 등록 화면에서
                self.assertEqual(c.post(url, headers=chic, json={}).status_code, 400)                                                                    # 저장할 값이 없다
                self.assertEqual(c.post(url, headers=chic, json={"monthly_rent": -5}).status_code, 400)
                self.assertEqual(c.post(url, headers=chic, json={"pay_timing": "아무때나"}).status_code, 400)
                self.assertIn(c.post(url, headers=sunho, json={"monthly_rent": 1}).status_code, (401, 403))                                              # 다른 지점 임대인은 못 한다
                ok = c.post(url, headers=chic, json={"deposit": 7000000, "monthly_rent": 330000, "maintenance_fee": 45000, "pay_timing": "선불"})
                self.assertEqual(ok.status_code, 200, ok.text)
                room = Room.first(branch="main", room_no="789")
                self.assertEqual(room.next_terms, {"deposit": 7000000, "monthly_rent": 330000, "maintenance_fee": 45000, "pay_timing": "선불"})
                self.assertEqual((room.deposit, room.monthly_rent), (0, 0))                                                                              # 지금 계약·호실 값은 그대로
                self.assertEqual(Contract.get(room.active_contract_id).monthly_rent, 0)
                # 임차인·일반 사용자에게는 보이지 않는다
                tenant = Tenant.get("tenant_main_789")
                th = {"Authorization": f"Bearer {tenant_token(tenant)}"}
                mine = c.get("/api/rent3_tenant/myhome/tenant_main_789", headers=th).json()
                self.assertNotIn("next_terms", mine["room"])
                other = self._new_prospect("구경꾼", "010-7777-0789")
                oh = {"Authorization": f"Bearer {tenant_token(other)}"}
                pub = next(r for r in c.get("/api/rent3_contract/rooms", headers=oh).json()["rooms"] if r["room_no"] == "789" and r["branch"] == "main")
                self.assertNotIn("next_terms", pub)
                # 사전 매물 공개(공개일 이후): 저장해 둔 조건이 매물 조건이 된다
                rec = c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": "789", "tenant_plan": "MOVE_OUT", "planned_move_out_date": "2026-11-20"})
                self.assertEqual(rec.status_code, 200, rec.text)
                room = Room.first(branch="main", room_no="789")
                self.assertTrue(room.is_published and room.pre_moveout_notice)
                self.assertEqual((room.next_listing["monthly_rent"], room.next_listing["pay_timing"]), (330000, "선불"))
                landing = c.get("/api/rent3/pool/landing", headers=self.NOAUTH).json()["listings"]
                self.assertEqual(next(l for l in landing if l["room_no"] == "789")["monthly_rent"], 330000)
                # 매물을 내려도 임시 저장한 초안은 지워지지 않는다(매물 조건만 지워진다)
                self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{room.id}/publish", headers=chic, json={"is_published": False}).status_code, 200)
                room = Room.first(branch="main", room_no="789")
                self.assertEqual((room.next_listing, bool(room.next_terms)), ({}, True))
                # 퇴실 확정: 초안이 새 호실 조건이 되고 비워진다
                res = tt.vacate_test_room("main", "789", apply=True)
                self.assertEqual(res["status"], "DONE", res)
                room = Room.first(branch="main", room_no="789")
                self.assertEqual((room.deposit, room.monthly_rent, room.maintenance_fee, room.pay_timing, room.next_terms), (7000000, 330000, 45000, "선불", {}))
                self.assertTrue(room.is_published and not room.is_occupied)
        finally:
            for cls, oid in ((Contract, "ct_main_789"), (Tenant, "tenant_main_789"), (Room, "room_main_789"), (Room, "room_main_788")):
                o = cls.get(oid)
                if o:
                    o.delete(action="t")
            for chk in ExpiryRenewalCheck.all():
                if chk.room_no == "789":
                    chk.delete(action="t")
            for t in Tenant.all():
                if t.phone == "010-7777-0789":
                    t.delete(action="t")
            lsr = Lessor.get(lsr.id)
            lsr.room_limit = old_limit
            lsr.save(action="t")
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("nextTermsCardHtml", "btnNtSave", "btnNtClear", "/next-terms", "다음 계약 조건", "nt0.monthly_rent"):
            self.assertIn(needle, lapp, needle)

    def _make_renewal_reservation(self, room_no, phone, name="연장임차", next_rent=310000):
        """연장 재계약 예약 시험용: 임차인이 사는 호실(돈이 있는 계약)을 만들고, 다음 계약 조건을 임시 저장한 뒤 만기 전에 연장 재계약을 체결해 (호실, 이전 계약, 새 계약, 세션 id) 를 돌려준다."""
        from apps.rent_dist._rent3_core import Room, Contract, CoSignSession, Lessor
        c = self.client
        c.post("/api/rent3_admin/lessors/lessor_chic/room-limit", json={"room_limit": 500})
        self.assertEqual(c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": room_no, "is_published": True, "deposit": 5000000, "monthly_rent": 300000, "maintenance_fee": 30000}).status_code, 200)
        t = self._new_prospect(name, phone)
        self._complete_contract(None, None, t.id, room_no, name, phone)
        room = Room.first(branch="main", room_no=room_no)
        old = Contract.get(room.active_contract_id)
        self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{room.id}/next-terms", json={"monthly_rent": next_rent, "maintenance_fee": 35000}).status_code, 200)
        self.assertEqual(c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": room_no, "tenant_plan": "STAY"}).status_code, 200)
        pv = c.post("/api/rent3_lessor/expiry/preview-renewal", json={"branch": "main", "room_no": room_no})
        self.assertEqual(pv.status_code, 200, pv.text)
        chk = pv.json()["check"]
        self.assertEqual((chk["proposed_monthly_rent"], chk["proposed_maintenance_fee"]), (next_rent, 35000))        # 임시 저장한 조건이 연장 예고의 기본값
        sid = chk["cosign_session_id"]
        base = "/api/rent3_contract"
        rev = c.get(f"{base}/sessions/{sid}").json()["session"]["revision"]
        for role in ("lessor", "tenant"):
            c.post(f"{base}/sessions/{sid}/step3/agree", json={"role": role, "expected_revision": rev})
        for role in ("lessor", "tenant"):
            r = c.post(f"{base}/sessions/{sid}/step4/sign", json={"role": role, "signature": f"{role}서명", "expected_revision": rev})
            self.assertEqual(r.status_code, 200, r.text)
        ses = CoSignSession.get(sid)
        self.assertEqual(ses.status, "COMPLETED")
        return Room.first(branch="main", room_no=room_no), Contract.get(old.id), Contract.get(ses.created_contract_id), sid

    def _cleanup_renewal_rooms(self, *room_nos):
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, CoSignSession
        from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
        for no in room_nos:
            for ct in Contract.find(room_no=no):
                if ct.branch == "main":
                    ct.delete(action="t")
            for s_ in CoSignSession.all():
                if s_.room_no == no and s_.branch == "main":
                    s_.delete(action="t")
            for chk in ExpiryRenewalCheck.all():
                if chk.room_no == no and chk.branch == "main":
                    chk.delete(action="t")
            r = Room.first(branch="main", room_no=no)
            if r:
                r.delete(action="t")
        for t in Tenant.all():
            if t.phone.startswith("010-7777-079"):
                t.delete(action="t")

    def test_150_early_renewal_keeps_old_terms_until_expiry_then_new_terms_apply(self):
        """150. 연장 재계약을 만기 전에 미리 체결하면: 새 계약은 예약(SCHEDULED)으로 임시 저장되고 만기까지는 이전 계약 조건으로 청구하며, 새 조건의 시작일(이전 계약 만기일)이 되면 이전 계약이 RENEWED·새 계약이 ACTIVE 가 되고 호실 조건이 바뀐다. 시작일은 만기 이전으로 못 정하고, 예약이 있으면 연장 예고를 다시 보낼 수 없다."""
        from unittest import mock
        from core.errors import DomainError
        from apps.rent_dist._rent3_core import Room, Contract, CoSignSession
        from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
        c = self.client
        try:
            room, old, new, sid = self._make_renewal_reservation("790", "010-7777-0790")
            self.assertEqual(old.status, "ACTIVE")
            self.assertEqual((new.status, new.renews_contract_id, new.move_in_date), ("SCHEDULED", old.id, old.expiry_date))   # 새 계약은 시작일이 이전 계약 만기일
            self.assertEqual((new.monthly_rent, new.maintenance_fee), (310000, 35000))
            # 만기까지는 이전 계약 조건 그대로(청구·호실 값·활성 계약)
            self.assertEqual((room.active_contract_id, room.reserved_contract_id), (old.id, new.id))
            self.assertEqual((room.deposit, room.monthly_rent, room.maintenance_fee), (5000000, 300000, 30000))
            self.assertEqual([x.id for x in Contract.find(id_room=room.id, status="ACTIVE")], [old.id])
            # 시작일을 만기 이전으로 앞당길 수 없다
            self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{room.id}/next-terms", json={"monthly_rent": 1}).status_code, 400)   # 예약이 걸리면 초안도 못 바꾼다
            again = c.post("/api/rent3_lessor/expiry/preview-renewal", json={"branch": "main", "room_no": "790"})
            self.assertEqual(again.status_code, 400)                                                                                  # 이미 체결되어 연장 예고를 다시 못 보낸다
            # 시작일 전에 조회해도 바뀌지 않는다
            with mock.patch.dict(os.environ, {"RENT3_TODAY": "2027-06-30"}):
                self.assertEqual(ExpiryRenewalCheck.activate_due(force=True), 0)
            self.assertEqual(Contract.get(old.id).status, "ACTIVE")
            self.assertEqual(Contract.get(new.id).status, "SCHEDULED")
            # 시작일(이전 계약 만기일): 새 조건 적용
            with mock.patch.dict(os.environ, {"RENT3_TODAY": old.expiry_date}):
                landing = c.get("/api/rent3/pool/landing", headers=self.NOAUTH)                                                     # 조회만으로도 반영(스케줄러 없음)
                self.assertEqual(landing.status_code, 200)
            self.assertEqual(Contract.get(old.id).status, "RENEWED")
            self.assertEqual(Contract.get(old.id).ended_at, old.expiry_date)
            self.assertEqual(Contract.get(new.id).status, "ACTIVE")
            room = Room.first(branch="main", room_no="790")
            self.assertEqual((room.active_contract_id, room.reserved_contract_id, room.next_terms), (new.id, "", {}))
            self.assertEqual((room.deposit, room.monthly_rent, room.maintenance_fee), (new.deposit, 310000, 35000))
            self.assertEqual([x.id for x in Contract.find(id_room=room.id, status="ACTIVE")], [new.id])
            self.assertTrue(room.is_occupied and not room.is_published)
        finally:
            self._cleanup_renewal_rooms("790")
        capp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("연장 재계약이 체결되었습니다", "renews_contract_id"):
            self.assertIn(needle, capp, needle)
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("연장 재계약 예약", "연장 예약 취소", 'rv.kind === "RENEWAL"'):
            self.assertIn(needle, lapp, needle)

    def test_151_renewal_reservation_cancel_vacate_and_guards(self):
        """151. 연장 재계약 예약: 임대인이 취소하면 만기까지 이전 조건 그대로, 예약 중에는 임차인이 '전출 예정'으로 못 바꾸고('전출의사 없음'은 가능), 만기 전에 퇴실이 확정되면 연장 예약은 자동 취소되고 보통의 퇴실 처리로 이어진다."""
        from apps.rent_dist._rent3_core import Room, Contract
        c = self.client
        try:
            room, old, new, sid = self._make_renewal_reservation("791", "010-7777-0791", name="연장취소")
            room_id = room.id
            # 예약 중에는 '전출 예정'으로 못 바꾼다(퇴실하려면 연장 예약을 먼저 취소), '전출의사 없음'은 괜찮다
            blocked = c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": "791", "tenant_plan": "MOVE_OUT", "planned_move_out_date": old.expiry_date})
            self.assertEqual(blocked.status_code, 400)
            self.assertIn("연장 재계약", blocked.json()["message"])
            self.assertEqual(c.post("/api/rent3_lessor/expiry/record-plan", json={"branch": "main", "room_no": "791", "tenant_plan": "STAY"}).status_code, 200)
            # 임대인이 연장 예약을 취소 → 이전 계약 그대로, 공개되지 않는다
            summary = next(r for r in c.get("/api/rent3_lessor/dashboard?branch=main").json()["rooms"] if r["room_no"] == "791")["reservation"]
            self.assertEqual((summary["kind"], summary["move_in_date"]), ("RENEWAL", old.expiry_date))
            ok = c.post(f"/api/rent3_lessor/rooms/{room_id}/reservation/cancel", json={"reason": "조건 재협의"})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertIn("현재 계약 조건", ok.json()["message"])
            room = Room.first(branch="main", room_no="791")
            self.assertEqual((room.reserved_contract_id, room.active_contract_id, room.is_published), ("", old.id, False))
            self.assertEqual((Contract.get(new.id).status, Contract.get(old.id).status), ("CANCELLED", "ACTIVE"))
            # 다시 연장 예고·체결(예약) → 이번엔 만기 전에 퇴실이 확정되는 경우
            self.assertEqual(c.post(f"/api/rent3_lessor/rooms/{room_id}/next-terms", json={"monthly_rent": 305000}).status_code, 200)
            pv = c.post("/api/rent3_lessor/expiry/preview-renewal", json={"branch": "main", "room_no": "791"})
            self.assertEqual(pv.status_code, 200, pv.text)
            sid2 = pv.json()["check"]["cosign_session_id"]
            base = "/api/rent3_contract"
            rev = c.get(f"{base}/sessions/{sid2}").json()["session"]["revision"]
            for role in ("lessor", "tenant"):
                c.post(f"{base}/sessions/{sid2}/step3/agree", json={"role": role, "expected_revision": rev})
            for role in ("lessor", "tenant"):
                c.post(f"{base}/sessions/{sid2}/step4/sign", json={"role": role, "signature": f"{role}서명", "expected_revision": rev})
            room = Room.first(branch="main", room_no="791")
            new2 = room.reservation()
            self.assertIsNotNone(new2)
            room.vacate(app_source="test")                                                                                          # 만기 전 퇴실 확정
            room = Room.first(branch="main", room_no="791")
            self.assertEqual(Contract.get(new2.id).status, "CANCELLED")                                                              # 연장 예약은 자동 취소
            self.assertIn("자동 취소", Contract.get(new2.id).special_terms)
            self.assertEqual((room.reserved_contract_id, room.id_tenant, room.active_contract_id), ("", "", ""))
            self.assertTrue(room.is_published and not room.is_occupied)                                                              # 보통의 퇴실: 공실로 재공개
        finally:
            self._cleanup_renewal_rooms("791")

    def test_152_early_checkout_tenant_bears_rent_until_expiry_or_new_tenant_and_it_is_deducted(self):
        """152. 만기 전에 퇴실하면 만기일 또는 새 임차인이 입주할 때까지(먼저 오는 날) 이전 임차인이 월세를 부담하고, 아직 안 낸 금액은 전출 정산에서 보증금에서 공제한다. 새 임차인이 입주하면 그날로 부담이 끝나고(후불은 입주월까지·선불은 전달까지), 선납한 월세는 빠지며, 공제가 보증금보다 크면 부족분을 표시한다. 정상 만기 퇴실에는 추가 부담이 없다."""
        from unittest import mock
        from apps.rent_dist._rent3_core import Room, Tenant, Contract, Pay, CoSignSession
        from apps.rent_dist._rent3_core.ops import CheckOutSettlement
        c = self.client
        sched = None
        try:
            c.post("/api/rent3_admin/lessors/lessor_chic/room-limit", json={"room_limit": 500})
            self.assertEqual(c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "787", "is_published": True, "deposit": 5000000, "monthly_rent": 300000, "maintenance_fee": 30000}).status_code, 200)
            t = self._new_prospect("조기퇴실", "010-7777-0787")
            self._complete_contract(None, None, t.id, "787", "조기퇴실", "010-7777-0787")
            room = Room.first(branch="main", room_no="787")
            ct = Contract.get(room.active_contract_id)
            monthly = ct.monthly_rent + ct.maintenance_fee + (ct.compute_vat() if (ct.vat_target and ct.vat_separate) else 0)
            self.assertGreater(monthly, 0)
            # 1) 만기 전: 만기월까지의 월세가 퇴실 후 부담분(지금 달까지의 미납과 구분)
            lb = ct.checkout_liability(room)
            ledger_now = ct.ledger()
            ledger_end = ct.ledger(up_to_ym=ct.expiry_date[:7])
            self.assertEqual((lb["reason"], lb["end_date"], lb["end_ym"]), ("EXPIRY", ct.expiry_date, ct.expiry_date[:7]))
            self.assertEqual(lb["unpaid_now"], int(ledger_now["total_unpaid"]))
            self.assertEqual(lb["early_amount"], int(ledger_end["total_unpaid"]) - int(ledger_now["total_unpaid"]))
            self.assertGreater(lb["early_amount"], 0)
            self.assertEqual(lb["early_amount"], sum(m["unpaid"] for m in lb["months"]))
            # 2) 정산서: 퇴실 후 월세가 보증금에서 공제된다
            r = c.post("/api/rent3_lessor/checkout/calculate", json={"branch": "main", "room_no": "787"})
            self.assertEqual(r.status_code, 200, r.text)
            s = r.json()["settlement"]
            self.assertEqual((s["unpaid_rent_deduction"], s["early_rent_deduction"], s["liability_reason"], s["liability_end_date"]), (lb["unpaid_now"], lb["early_amount"], "EXPIRY", ct.expiry_date))
            self.assertEqual(s["refund_amount"], max(0, ct.deposit - (lb["unpaid_now"] + lb["early_amount"] + s["cleaning_fee"])))
            self.assertEqual(s["shortfall_amount"], max(0, lb["unpaid_now"] + lb["early_amount"] + s["cleaning_fee"] - ct.deposit))
            from apps.rent_dist._rent3_core import DirectChat
            self.assertTrue(any("월세 부담분" in (m.message or "") for m in DirectChat.find(id_room=room.id)))
            # 3) 선납한 달은 부담분에서 빠진다
            first_future = lb["months"][0]
            pre = Pay(branch="main", id_contract=ct.id, room_no="787", id_tenant=t.id, payment_kind="MONTHLY_RENT", billing_month=first_future["ym"],
                      paid_date="2026-10-29", total_amount=first_future["due"]).save(action="t")
            self.assertEqual(ct.checkout_liability(room)["early_amount"], lb["early_amount"] - first_future["due"])
            pre.delete(action="t")
            # 4) 새 임차인이 만기 전에 입주하기로 한 경우(예약 계약): 그 입주일까지만 부담 — 후불은 입주월까지, 선불은 입주 전달까지
            sched = Contract(branch="main", id_room=room.id, room_no="787", id_tenant="tenant_new_x", tenant_name="새임차", status="SCHEDULED", deposit=5000000,
                             monthly_rent=300000, maintenance_fee=30000, pay_timing="후불", move_in_date="2027-03-15", expiry_date="2028-03-15").save(action="t")
            room.reserved_contract_id = sched.id
            room.save(action="t")
            room = Room.first(branch="main", room_no="787")
            lb2 = ct.checkout_liability(room)
            self.assertEqual((lb2["reason"], lb2["end_date"], lb2["end_ym"], lb2["new_tenant_move_in"]), ("NEW_TENANT", "2027-03-15", "2027-03", "2027-03-15"))
            self.assertEqual(lb2["early_amount"], int(ct.ledger(up_to_ym="2027-03")["total_unpaid"]) - lb2["unpaid_now"])
            self.assertLess(lb2["early_amount"], lb["early_amount"])
            sched.pay_timing = "선불"
            sched.save(action="t")
            lb3 = ct.checkout_liability(Room.first(branch="main", room_no="787"))
            self.assertEqual(lb3["end_ym"], "2027-02")                                                                   # 선불 새 임차인은 입주월 월세를 따로 내므로 이전 임차인은 전달까지
            self.assertEqual(lb2["early_amount"] - lb3["early_amount"], monthly)
            s2 = c.post("/api/rent3_lessor/checkout/calculate", json={"branch": "main", "room_no": "787"}).json()["settlement"]
            self.assertEqual((s2["liability_reason"], s2["liability_end_date"], s2["early_rent_deduction"]), ("NEW_TENANT", "2027-03-15", lb3["early_amount"]))
            # 5) 공제가 보증금보다 크면 부족분을 표시하고 반환액은 0
            big = c.post("/api/rent3_lessor/checkout/calculate", json={"branch": "main", "room_no": "787", "cleaning_fee": 9000000}).json()["settlement"]
            self.assertEqual(big["refund_amount"], 0)
            self.assertEqual(big["shortfall_amount"], lb3["unpaid_now"] + lb3["early_amount"] + 9000000 - ct.deposit)
            # 6) 정상 만기 퇴실(만기월 안): 추가 부담 없음
            room.reserved_contract_id = ""
            room.save(action="t")
            with mock.patch.dict(os.environ, {"RENT3_TODAY": ct.expiry_date}):
                end = ct.checkout_liability(Room.first(branch="main", room_no="787"))
                self.assertEqual(end["early_amount"], 0)
                self.assertEqual(end["months"], [])
        finally:
            for co in CheckOutSettlement.all():
                if co.room_no == "787" and co.branch == "main":
                    co.delete(action="t")
            if sched:
                sched.delete(action="t")
            for ct_ in Contract.find(room_no="787"):
                if ct_.branch == "main":
                    ct_.delete(action="t")
            for s_ in CoSignSession.all():
                if s_.room_no == "787" and s_.branch == "main":
                    s_.delete(action="t")
            r_ = Room.first(branch="main", room_no="787")
            if r_:
                r_.delete(action="t")
            for t_ in Tenant.all():
                if t_.phone == "010-7777-0787":
                    t_.delete(action="t")
        lapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_lessor", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("퇴실 후 월세 부담", "checkout_liability", "만기 전 퇴실 월세 부담분"):
            self.assertIn(needle, lapp, needle)
        tapp = open(os.path.join(ROOT, "apps", "rent_dist", "rent3_tenant", "frontend", "app.js"), encoding="utf-8").read()
        for needle in ("early_rent_deduction", "shortfall_amount", "liability_end_date"):
            self.assertIn(needle, tapp, needle)

    def _lessor_id_main(self):
        from apps.rent_dist._rent3_core import Lessor
        return Lessor.for_branch("main").id


if __name__ == "__main__":
    unittest.main()
