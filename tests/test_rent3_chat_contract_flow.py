"""
tests/test_rent3_chat_contract_flow.py — 채팅창 기반 스마트 계약 앱 개입 카드 및 좌우·상하 스크롤 문서 모달 검증.
"""
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRent3ChatContractFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rent3_chat_flow_")
        cls._env_patch = mock.patch.dict(
            os.environ,
            {
                "RENT3_POOL_DIR": cls._tmp,
                "RENT3_BACKUP_DIR": os.path.join(cls._tmp, "backups"),
            },
        )
        cls._env_patch.start()
        from core.gateway import create_gateway
        from apps.rent_dist._rent3_core import ensure_pool_seeded, use_pool_dir
        use_pool_dir(cls._tmp)
        ensure_pool_seeded(force=True)
        cls.app = create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.stop()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _login_lessor(self, username: str = "chic") -> dict:
        from apps.rent_dist._rent3_core import Lessor
        from apps.rent_dist._rent3_core.authz import lessor_token
        lsr = Lessor.first(username=username)
        assert lsr is not None
        return {"Authorization": f"Bearer {lessor_token(lsr)}"}

    def _read_contract_files(self) -> tuple[str, str]:
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend")
        with open(os.path.join(fe, "index.html"), encoding="utf-8") as fp:
            html = fp.read()
        js_parts = []
        for name in ("core.js", "home.js", "chat.js", "registry.js", "steps.js", "app.js"):
            with open(os.path.join(fe, name), encoding="utf-8") as fp:
                js_parts.append(fp.read())
        return html, "\n".join(js_parts)

    def test_01_session_get_exposes_room_info_for_standard_contract_modal(self):
        """GET /api/rent3_contract/sessions/{id} 가 표준계약서 모달용 room_info(전용면적·층수·옵션·설비·PDF URL)를 반환한다."""
        from apps.rent_dist._rent3_core import CoSignSession, Lessor, Room
        lsr = Lessor.for_branch("main")
        assert lsr is not None
        lsr.room_limit = max(lsr.room_limit, 99)
        lsr.save(action="t")
        lh = self._login_lessor("chic")
        cr = self.client.post(
            "/api/rent3_lessor/rooms",
            headers=lh,
            json={
                "branch": "main",
                "room_no": "811",
                "room_category": "주거용 원룸",
                "floor": 3,
                "total_floors": 5,
                "area_sqm": 24.8,
                "direction": "남동향",
                "deposit": 8000000,
                "monthly_rent": 450000,
                "maintenance_fee": 60000,
                "cleaning_fee": 100000,
                "listing_spec": {
                    "options": ["에어컨", "세탁기", "냉장고", "인덕션"],
                    "cable_tv": "설치됨",
                    "internet": "설치됨",
                    "elevator": "설치됨",
                    "parking": "가능",
                    "maintenance_includes": ["수도료", "인터넷"],
                    "no_pets": True,
                    "terminate_on_2m_arrears": True,
                },
                "is_published": True,
            },
        )
        self.assertEqual(cr.status_code, 200, cr.text)
        rid = cr.json()["room"]["id"]
        try:
            s_res = self.client.post(
                "/api/rent3_contract/sessions",
                headers=lh,
                json={
                    "branch": "main",
                    "room_no": "811",
                    "mode": "NEW",
                    "tenant_name": "강임차",
                    "tenant_phone": "010-8110-8110",
                },
            )
            self.assertEqual(s_res.status_code, 200, s_res.text)
            sid = s_res.json()["session"]["id"]
            got = self.client.get(f"/api/rent3_contract/sessions/{sid}", headers=lh)
            self.assertEqual(got.status_code, 200, got.text)
            ri = got.json().get("room_info")
            assert ri is not None
            self.assertEqual(ri["id"], rid)
            self.assertEqual(ri["area_sqm"], 24.8)
            self.assertEqual(ri["floor"], 3)
            self.assertEqual(ri["total_floors"], 5)
            self.assertEqual(ri["direction"], "남동향")
            self.assertEqual(ri["listing_spec"]["elevator"], "설치됨")
            self.assertEqual(ri["listing_spec"]["cable_tv"], "설치됨")
            self.assertEqual(ri["terms_pdf_url"], f"/api/rent3/pool/listings/{rid}/terms.pdf")
        finally:
            for s_ in CoSignSession.all():
                if s_.room_no == "811":
                    s_.delete(action="t")
            r_ = Room.first(branch="main", room_no="811")
            if r_:
                r_.delete(action="t")

    def test_02_chat_guided_smart_contract_end_to_end_flow(self):
        """채팅창 스마트 계약 흐름(호출→응답→상호신분확인→등기부열람승인→표준계약서·도배메모확인→양측동의→서명→재열람·체결) 전 단계가 끊김 없이 동작한다."""
        from apps.rent_dist._rent3_core import CoSignSession, Contract, Lessor, Room, Tenant
        from apps.rent_dist._rent3_core.authz import tenant_token
        lsr = Lessor.for_branch("main")
        assert lsr is not None
        lsr.room_limit = max(lsr.room_limit, 99)
        lsr.save(action="t")
        lh = self._login_lessor("chic")
        cr = self.client.post(
            "/api/rent3_lessor/rooms",
            headers=lh,
            json={
                "branch": "main",
                "room_no": "812",
                "deposit": 5000000,
                "monthly_rent": 350000,
                "maintenance_fee": 50000,
                "cleaning_fee": 80000,
                "is_published": True,
            },
        )
        self.assertEqual(cr.status_code, 200, cr.text)
        reg = self.client.post(
            "/api/rent3/pool/auth/register",
            json={"role": "prospect", "name": "강임차테스트", "phone": "010-8120-8120", "password": "Pw123456!"},
        )
        self.assertEqual(reg.status_code, 200, reg.text)
        tid = reg.json()["user"]["id"]
        th = {"Authorization": f"Bearer {tenant_token(Tenant.require(tid))}"}
        id_who = {"name": "강임차테스트", "phone": "010-8120-8120", "birth": "950101"}
        self.assertEqual(self.client.post("/api/rent3_contract/identity/start", headers=th, json=id_who).status_code, 200)
        self.assertEqual(
            self.client.post("/api/rent3_contract/identity/confirm", headers=th, json={**id_who, "code": "000000"}).status_code,
            200,
        )
        try:
            # 1) 호출 & 응답
            call_r = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=lh,
                json={
                    "caller_role": "lessor",
                    "branch": "main",
                    "room_no": "812",
                    "mode": "NEW",
                    "id_tenant": tid,
                    "tenant_name": "강임차테스트",
                    "tenant_phone": "010-8120-8120",
                },
            )
            self.assertEqual(call_r.status_code, 200, call_r.text)
            sid = call_r.json()["session"]["id"]
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/respond",
                    headers=th,
                    json={"responder_role": "tenant", "id_tenant": tid, "responder_name": "강임차테스트", "responder_phone": "010-8120-8120"},
                ).status_code,
                200,
            )

            # 2) 채팅창 인라인 상호 신분 확인 체크
            self.assertEqual(
                self.client.post(f"/api/rent3_contract/sessions/{sid}/verify-id", headers=lh, json={"role": "lessor", "checked": True}).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(f"/api/rent3_contract/sessions/{sid}/verify-id", headers=th, json={"role": "tenant", "checked": True}).status_code,
                200,
            )
            # 채팅 메시지 전송
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/messages",
                    headers=th,
                    json={"kind": "question", "body": "신분증 확인했습니다. 등기부등본 열람할게요."},
                ).status_code,
                200,
            )

            # 3) 법원등기소 등기부등본 열람 · 승인 모달 제출
            sess1 = self.client.get(f"/api/rent3_contract/sessions/{sid}", headers=lh).json()["session"]
            owner = (sess1.get("registry_data") or {}).get("owner_name") or sess1["lessor_name"]
            now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/tenant-registry",
                    headers=th,
                    json={
                        "registry": {
                            "viewed_directly": True,
                            "viewed_at": now_str,
                            "owner_name": owner,
                            "mortgage_amount": 0,
                            "seizure_or_trust": "없음",
                        }
                    },
                ).status_code,
                200,
            )
            r_s1 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step1",
                headers=th,
                json={"role": "tenant", "checked_counterparty_id": True},
            )
            self.assertEqual(r_s1.status_code, 200, r_s1.text)
            self.assertEqual(r_s1.json()["session"]["current_step"], 2)

            # 4) 표준계약서 모달 내 옵션 · 도배상태 세부내역 메모 확인
            memo = "거실 도배 양호, 안방 창측 경미한 스크래치 1곳 메모 완료"
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/step2",
                    headers=lh,
                    json={"role": "lessor", "facility_notes": memo},
                ).status_code,
                200,
            )
            r_s2 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step2",
                headers=th,
                json={"role": "tenant", "facility_notes": memo},
            )
            self.assertEqual(r_s2.status_code, 200, r_s2.text)
            self.assertEqual(r_s2.json()["session"]["current_step"], 3)
            self.assertEqual(r_s2.json()["session"]["facility_notes"], memo)

            # 5) 표준계약서 내용 양측 동의
            rev = r_s2.json()["session"]["revision"]
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/step3/agree",
                    headers=lh,
                    json={"role": "lessor", "expected_revision": rev},
                ).status_code,
                200,
            )
            r_s3 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step3/agree",
                headers=th,
                json={"role": "tenant", "expected_revision": rev},
            )
            self.assertEqual(r_s3.status_code, 200, r_s3.text)
            self.assertEqual(r_s3.json()["session"]["current_step"], 4)

            # 6) 전자서명
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/step4/sign",
                    headers=lh,
                    json={"role": "lessor", "signature": "김중성", "expected_revision": rev},
                ).status_code,
                200,
            )
            r_s4 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step4/sign",
                headers=th,
                json={"role": "tenant", "signature": "강임차테스트", "expected_revision": rev},
            )
            self.assertEqual(r_s4.status_code, 200, r_s4.text)
            self.assertEqual(r_s4.json()["session"]["current_step"], 5)

            # 7) 체결 직전 재열람 & 계약금 확인 최종 체결
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/recheck",
                    headers=th,
                    json={"changed": False},
                ).status_code,
                200,
            )
            r_s5 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step5/complete",
                headers=lh,
                json={"payment_method": "TRANSFER", "own_account_checked": True},
            )
            self.assertEqual(r_s5.status_code, 200, r_s5.text)
            self.assertEqual(r_s5.json()["session"]["status"], "COMPLETED")
            self.assertTrue(r_s5.json()["contract"]["id"])
        finally:
            for ct_ in Contract.find(room_no="812"):
                ct_.delete(action="t")
            for s_ in CoSignSession.all():
                if s_.room_no == "812":
                    s_.delete(action="t")
            r_ = Room.first(branch="main", room_no="812")
            if r_:
                r_.delete(action="t")
            t_ = Tenant.get(tid)
            if t_:
                t_.delete(action="t")

    def test_03_frontend_chat_app_interventions_and_4way_scroll_modal(self):
        """계약 채팅창 내 앱 개입 안내·링크·체크박스 및 좌우·상하 스크롤 문서 모달(등기부등본·표준계약서) 마크업·스타일을 검증한다."""
        html, js = self._read_contract_files()

        # 4방향(좌우·상하) 스크롤 모달 CSS & 구조
        for needle in (
            'id="wsPartyHeader"',
            'id="stepModalTitle"',
            'id="stepModalHint"',
            "cm-scroll-viewport",
            "cm-doc-scroll-box",
            "cm-std-sheet",
            "overflow: auto",
            "min-width: 660px",
            "좌우·상하로 스크롤",
            "cg-party-header",
            "cg-card",
            "cg-sign-paper",
        ):
            self.assertIn(needle, html, needle)

        # 채팅창 내 앱 개입 카드 및 문서 확인·승인 흐름 문구
        for needle in (
            "계약 채팅",
            "지금부터 스마트 계약을 시작합니다.",
            "임대인과 임차인은 서로 신분증을 제시해 주세요.",
            "임대인이 임차인 신분 확인",
            "임차인이 임대인 신분 확인",
            "[ 법원등기소 ]",
            "등기인 = 임대인 명의",
            "근저당 / 채권최고액 확인",
            "압류·가압류·신탁 없음 확인",
            "임차인이 위 사실을 확인했음",
            "[ 표준계약서 내용 확인 ]",
            "도배상태 등 세부내역 메모",
            "임대인은 표준계약서 내용에 동의",
            "임차인은 표준계약서 내용에 동의",
            "상기 내용에 동의합니다.",
            "buildRegistryComparisonSheetHtml",
            "buildStandardContractSheetHtml",
            "buildSmartChatTimelineHtml",
            "closeStepModal",
        ):
            self.assertIn(needle, js, needle)

        # 비인쇄 UI 폰트 12px 미만 금지 (chat.js / core.js / app.js / index.html)
        small_font = re.compile(r"font-size:\s*(?:9|10|11)(?:\.\d)?px")
        fe = os.path.join(ROOT, "apps", "rent_dist", "rent3_contract", "frontend")
        for fname in ("index.html", "core.js", "chat.js", "app.js"):
            with open(os.path.join(fe, fname), encoding="utf-8") as fp:
                src = fp.read()
            self.assertFalse(small_font.search(src), f"12px 미만 폰트 감지: {fname}")

        # 금지된 구식 용어·불변식 확인
        for gone in ("미체크", "미확인", "임차인 확인 잠금", "임대인 확인: ", "계약방 대화", "대화 열기"):
            self.assertNotIn(gone, js, gone)
        self.assertEqual(js.count("pairDots("), 4)
        self.assertEqual(js.count("collectUpdates()"), 1)


if __name__ == "__main__":
    unittest.main()
