"""
tests/test_rent3_smart_contract_upgrade.py — 스마트 계약 통합 업그레이드 검증.

검증 범위:
1. 4대 진입 경로(임대인 연장 요청 / 포털 신규 가입자 / 기존 임차인 이사 신규 / 보류↔재개)
   - 기존 입주 임차인이 미래 입주일로 다른 호실에 이사 계약 체결 시 현재 호실 유지 + 새 호실 SCHEDULED 예약 체결
2. 진행 중 스마트 계약 잠시 보류(PAUSED, 7일 보관) ↔ 재개(RESUME), 보류 중 문의 채팅 허용 & 단계 변경 차단
3. 임차인 선택형 등기부등본 캡처·AI 검토 토글(기본 REQUIRED 유지, 임차인만 생략/재활성화 가능, 생략 시 1·5단계 통과)
4. 스마트 계약 AI 도우미(쉬운 계약서 요약 / 자연어→표준 특약 다듬기 / 메모 사진 OCR / 등기부 AI 쉬운 요약 plain_summary)
5. 프론트엔드 전역 '스마트 계약' 용어 통일 및 신규 UI 요소·폰트 크기 무결성
"""
import base64
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1x1 PNG 더미 이미지 (base64 data URL)
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)
TINY_PNG_DATA_URL = f"data:image/png;base64,{TINY_PNG_B64}"


class _FakeGeminiClient:
    """테스트용 목(Mock) GeminiClient."""

    def __init__(self, payload: dict):
        self._payload = payload

    @property
    def is_available(self) -> bool:
        return True

    async def generate_text(self, prompt: str) -> str:
        return json.dumps(self._payload, ensure_ascii=False)

    async def generate_with_image(self, prompt: str, b64: str, mime_type: str = "image/png") -> str:
        return json.dumps(self._payload, ensure_ascii=False)


class TestRent3SmartContractUpgrade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rent3_smart_upgrade_")
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

    def _lessor_headers(self, username: str = "chic") -> dict:
        from apps.rent_dist._rent3_core import Lessor
        from apps.rent_dist._rent3_core.authz import lessor_token

        lsr = Lessor.first(username=username)
        assert lsr is not None
        lsr.room_limit = max(lsr.room_limit, 99)
        lsr.save(action="test_limit")
        return {"Authorization": f"Bearer {lessor_token(lsr)}"}

    def _create_vacant_room(self, lh: dict, room_no: str, deposit: int = 5000000, rent: int = 380000) -> str:
        r = self.client.post(
            "/api/rent3_lessor/rooms",
            headers=lh,
            json={
                "branch": "main",
                "room_no": room_no,
                "deposit": deposit,
                "monthly_rent": rent,
                "maintenance_fee": 50000,
                "cleaning_fee": 100000,
                "is_published": True,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["room"]["id"]

    def _register_verified_prospect(self, name: str, phone: str, birth: str) -> tuple[str, dict]:
        from apps.rent_dist._rent3_core import Tenant
        from apps.rent_dist._rent3_core.authz import tenant_token

        reg = self.client.post(
            "/api/rent3/pool/auth/register",
            json={"role": "prospect", "name": name, "phone": phone, "password": "Pw123456!"},
        )
        self.assertEqual(reg.status_code, 200, reg.text)
        tid = reg.json()["user"]["id"]
        th = {"Authorization": f"Bearer {tenant_token(Tenant.require(tid))}"}
        st = self.client.post(
            "/api/rent3_contract/identity/start",
            headers=th,
            json={"name": name, "phone": phone, "birth": birth},
        )
        self.assertEqual(st.status_code, 200, st.text)
        cf = self.client.post(
            "/api/rent3_contract/identity/confirm",
            headers=th,
            json={"name": name, "phone": phone, "birth": birth, "code": "000000"},
        )
        self.assertEqual(cf.status_code, 200, cf.text)
        return tid, th

    def _cleanup_room_sessions(self, room_no: str):
        from apps.rent_dist._rent3_core import CoSignSession, Contract, Room

        for s in list(CoSignSession.all()):
            if s.room_no == room_no:
                s.delete(action="test_cleanup")
        for c in list(Contract.all()):
            if c.room_no == room_no:
                c.delete(action="test_cleanup")
        r = Room.first(branch="main", room_no=room_no)
        if r:
            r.delete(action="test_cleanup")

    def test_01_four_entry_channels_and_existing_tenant_move_preserves_current_room(self):
        """임대인 연장(LESSOR_RENEWAL), 포털 신규(PORTAL_NEW), 기존 임차인 이사(EXISTING_TENANT_MOVE) 진입과 미래 이사 예약 체결을 검증한다."""
        from apps.rent_dist._rent3_core import CoSignSession, Contract, Room, Tenant
        from apps.rent_dist._rent3_core.authz import tenant_token

        lh = self._lessor_headers("chic")

        # (A) 임대인 → 기존 임차인 연장 계약 (LESSOR_RENEWAL)
        occ_room = next((r for r in Room.find(branch="main") if r.is_occupied), None)
        assert occ_room is not None
        active_tenant = Tenant.first(current_room_id=occ_room.id, status="ACTIVE")
        assert active_tenant is not None

        # 기존 입주 임차인이 본인인증이 안 되어 있다면 시연용 인증 부여
        if not (active_tenant.identity or {}).get("verified"):
            active_tenant.identity = {"verified": True, "name": active_tenant.name, "birth6": "900101"}
            active_tenant.save(action="test_id")
        ath = {"Authorization": f"Bearer {tenant_token(active_tenant)}"}

        ren_call = self.client.post(
            "/api/rent3_contract/sessions/call",
            headers=lh,
            json={
                "caller_role": "lessor",
                "branch": "main",
                "room_no": occ_room.room_no,
                "mode": "RENEWAL",
                "tenant_name": active_tenant.name,
                "tenant_phone": active_tenant.phone,
                "id_tenant": active_tenant.id,
            },
        )
        self.assertEqual(ren_call.status_code, 200, ren_call.text)
        ren_s = ren_call.json()["session"]
        self.assertEqual(ren_s["mode"], "RENEWAL")
        self.assertEqual(ren_s["entry_channel"], "LESSOR_RENEWAL")
        # 정리
        CoSignSession.require(ren_s["id"]).delete(action="test_cleanup")

        # (B) 포털 신규 가입자(예비임차인) → 신규 계약 (PORTAL_NEW)
        self._create_vacant_room(lh, "901")
        self._create_vacant_room(lh, "902")
        try:
            pid, pth = self._register_verified_prospect("포털신규자", "010-9010-9010", "960303")
            p_call = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=pth,
                json={
                    "caller_role": "tenant",
                    "branch": "main",
                    "room_no": "901",
                    "mode": "NEW",
                    "tenant_name": "포털신규자",
                    "tenant_phone": "010-9010-9010",
                    "id_tenant": pid,
                },
            )
            self.assertEqual(p_call.status_code, 200, p_call.text)
            self.assertEqual(p_call.json()["session"]["entry_channel"], "PORTAL_NEW")
            self.assertEqual(p_call.json()["session"]["mode"], "NEW")

            # (C) 기존 입주 임차인(ACTIVE) → 더 좋은 호실(902호)로 이사 신규 계약 (EXISTING_TENANT_MOVE)
            orig_room_id = active_tenant.current_room_id
            orig_room_no = active_tenant.room_no
            m_call = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=ath,
                json={
                    "caller_role": "tenant",
                    "branch": "main",
                    "room_no": "902",
                    "mode": "NEW",
                    "tenant_name": active_tenant.name,
                    "tenant_phone": active_tenant.phone,
                    "id_tenant": active_tenant.id,
                },
            )
            self.assertEqual(m_call.status_code, 200, m_call.text)
            ms = m_call.json()["session"]
            sid = ms["id"]
            self.assertEqual(ms["entry_channel"], "EXISTING_TENANT_MOVE")
            self.assertEqual(ms["moving_from_room_id"], orig_room_id)
            self.assertEqual(ms["moving_from_room_no"], orig_room_no)

            # 임대인 응답 → Step 1(임차인 등기부 생략 선택 + 상호 신원확인) → Step 2 → Step 3(미래 입주일) → Step 4 → Step 5 체결
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/respond",
                    headers=lh,
                    json={"responder_role": "lessor"},
                ).status_code,
                200,
            )
            # 임차인이 등기부 검토 생략 선택 + 신분 확인
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/registry-preference",
                    headers=ath,
                    json={"role": "tenant", "want_check": False, "skip_acknowledged": True},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/verify-id",
                    headers=ath,
                    json={"role": "tenant", "checked": True},
                ).status_code,
                200,
            )
            r_s1 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step1",
                headers=lh,
                json={"role": "lessor", "checked_counterparty_id": True},
            )
            self.assertEqual(r_s1.status_code, 200, r_s1.text)
            self.assertEqual(r_s1.json()["session"]["current_step"], 2)

            # Step 2 양측 확인
            opts = r_s1.json()["session"]["option_checks"]
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/step2",
                    headers=lh,
                    json={"role": "lessor", "option_checks": opts, "facility_notes": "이사 예정 호실 양호"},
                ).status_code,
                200,
            )
            r_s2 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step2",
                headers=ath,
                json={"role": "tenant", "option_checks": opts, "facility_notes": "이사 예정 호실 양호"},
            )
            self.assertEqual(r_s2.status_code, 200, r_s2.text)
            self.assertEqual(r_s2.json()["session"]["current_step"], 3)

            # 미래 입주일(+45일) 제안·수락
            future_move = (date.today() + timedelta(days=45)).isoformat()
            prop = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/messages",
                headers=lh,
                json={"kind": "proposal", "field": "move_in_date", "new_value": future_move, "body": "이사일 지정"},
            )
            self.assertEqual(prop.status_code, 200, prop.text)
            mid = prop.json()["item"]["id"]
            acc = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/messages/{mid}/respond",
                headers=ath,
                json={"action": "accept"},
            )
            self.assertEqual(acc.status_code, 200, acc.text)
            rev = acc.json()["session"]["revision"]

            # Step 3 양측 동의
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
                headers=ath,
                json={"role": "tenant", "expected_revision": rev},
            )
            self.assertEqual(r_s3.status_code, 200, r_s3.text)
            self.assertEqual(r_s3.json()["session"]["current_step"], 4)

            # Step 4 양측 서명
            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/step4/sign",
                    headers=lh,
                    json={"role": "lessor", "signature": "임대인서명", "expected_revision": rev},
                ).status_code,
                200,
            )
            r_s4 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step4/sign",
                headers=ath,
                json={"role": "tenant", "signature": f"{active_tenant.name}서명", "expected_revision": rev},
            )
            self.assertEqual(r_s4.status_code, 200, r_s4.text)
            self.assertEqual(r_s4.json()["session"]["current_step"], 5)

            # Step 5 체결: 등기부 생략 선택했으므로 recheck 없이 바로 체결 가능 + 미래 이사 예약(SCHEDULED)
            r_s5 = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step5/complete",
                headers=lh,
                json={"payment_method": "TRANSFER", "own_account_checked": True},
            )
            self.assertEqual(r_s5.status_code, 200, r_s5.text)
            ct = r_s5.json()["contract"]
            self.assertEqual(ct["status"], "SCHEDULED")
            self.assertEqual(ct["registry_snapshot"]["registry_check_mode"], "SKIPPED_BY_TENANT")

            # 기존 임차인의 현재 거주 호실은 그대로 유지되고, 새 호실(902)은 예약됨!
            t_after = Tenant.require(active_tenant.id)
            self.assertEqual(t_after.current_room_id, orig_room_id)
            self.assertEqual(t_after.room_no, orig_room_no)
            r902 = Room.first(branch="main", room_no="902")
            assert r902 is not None
            self.assertEqual(r902.reserved_contract_id, ct["id"])
            self.assertFalse(r902.is_published)
            self.assertFalse(r902.is_occupied)
        finally:
            self._cleanup_room_sessions("901")
            self._cleanup_room_sessions("902")

    def test_02_pause_and_resume_preserves_step_and_allows_chat(self):
        """진행 중 보류(PAUSED) 시 단계가 보존되고 문의 채팅은 허용되며, 단계 진행·타인 가로채기는 차단되고 재개 시 복원된다."""
        lh = self._lessor_headers("chic")
        self._create_vacant_room(lh, "903")
        try:
            pid, pth = self._register_verified_prospect("보류임차인", "010-9030-9030", "940404")
            pid2, pth2 = self._register_verified_prospect("가로채기시도", "010-9031-9031", "930303")
            call = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=pth,
                json={
                    "caller_role": "tenant",
                    "branch": "main",
                    "room_no": "903",
                    "mode": "NEW",
                    "tenant_name": "보류임차인",
                    "tenant_phone": "010-9030-9030",
                    "id_tenant": pid,
                },
            )
            self.assertEqual(call.status_code, 200, call.text)
            sid = call.json()["session"]["id"]

            self.assertEqual(
                self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/respond",
                    headers=lh,
                    json={"responder_role": "lessor"},
                ).status_code,
                200,
            )

            # 임차인이 보류 요청
            p_res = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/pause",
                headers=pth,
                json={"role": "tenant", "reason": "가족 상의 후 저녁에 재개"},
            )
            self.assertEqual(p_res.status_code, 200, p_res.text)
            ps = p_res.json()["session"]
            self.assertEqual(ps["status"], "PAUSED")
            self.assertEqual(ps["paused_by"], "tenant")
            self.assertEqual(ps["pause_reason"], "가족 상의 후 저녁에 재개")
            self.assertTrue(bool(ps["pause_expires_at"]))

            # /status 에 paused_sessions 노출
            st_res = self.client.get("/api/rent3_contract/status")
            self.assertEqual(st_res.status_code, 200)
            self.assertGreaterEqual(st_res.json()["paused_sessions"], 1)

            # 보류 중 타인이 같은 방을 가로챌 수 없음
            hijack = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=pth2,
                json={
                    "caller_role": "tenant",
                    "branch": "main",
                    "room_no": "903",
                    "mode": "NEW",
                    "tenant_name": "가로채기시도",
                    "tenant_phone": "010-9031-9031",
                    "id_tenant": pid2,
                },
            )
            self.assertGreaterEqual(hijack.status_code, 400)

            # 보류 중 단계 진행(step1)은 차단되지만 문의(question) 채팅은 가능!
            blocked = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step1",
                headers=lh,
                json={"role": "lessor", "checked_counterparty_id": True},
            )
            self.assertGreaterEqual(blocked.status_code, 400)
            self.assertIn("보류", blocked.text)

            q_ok = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/messages",
                headers=pth,
                json={"kind": "question", "body": "저녁 7시에 다시 재개할게요!"},
            )
            self.assertEqual(q_ok.status_code, 200, q_ok.text)

            # 보류 중 조건 제안(proposal)은 차단됨
            prop_blocked = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/messages",
                headers=lh,
                json={"kind": "proposal", "field": "monthly_rent", "new_value": 360000},
            )
            self.assertGreaterEqual(prop_blocked.status_code, 400)

            # 재개(RESUME) → IN_PROGRESS 복원 & 단계 진행 다시 허용
            r_res = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/resume",
                headers=lh,
                json={"role": "lessor"},
            )
            self.assertEqual(r_res.status_code, 200, r_res.text)
            rs = r_res.json()["session"]
            self.assertEqual(rs["status"], "IN_PROGRESS")
            self.assertEqual(rs["current_step"], 1)
            self.assertEqual(rs["paused_at"], "")

            unblocked = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step1",
                headers=lh,
                json={"role": "lessor", "checked_counterparty_id": True},
            )
            self.assertEqual(unblocked.status_code, 200, unblocked.text)
        finally:
            self._cleanup_room_sessions("903")

    def test_03_tenant_opt_in_registry_toggle_guards_and_reenable(self):
        """임차인 등기부 검토 선택 버튼의 보안 가드(기본 REQUIRED 유지, 임대인 조작 금지, 확인 체크 필수, 재활성화 복원)를 검증한다."""
        lh = self._lessor_headers("chic")
        self._create_vacant_room(lh, "904")
        try:
            pid, pth = self._register_verified_prospect("선택등기임차인", "010-9040-9040", "970505")
            call = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=pth,
                json={
                    "caller_role": "tenant",
                    "branch": "main",
                    "room_no": "904",
                    "mode": "NEW",
                    "tenant_name": "선택등기임차인",
                    "tenant_phone": "010-9040-9040",
                    "id_tenant": pid,
                },
            )
            sid = call.json()["session"]["id"]
            self.assertEqual(call.json()["session"]["registry_check_mode"], "REQUIRED")
            self.client.post(
                f"/api/rent3_contract/sessions/{sid}/respond",
                headers=lh,
                json={"responder_role": "lessor"},
            )

            # 1) 기본값 REQUIRED: 등기부 미제출 상태에서 tenant step1 차단 (test_10 불변식)
            r_no_reg = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/step1",
                headers=pth,
                json={"role": "tenant", "checked_counterparty_id": True},
            )
            self.assertEqual(r_no_reg.status_code, 400)

            # 2) 임대인은 등기부 생략을 강제할 수 없음
            r_lessor_skip = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/registry-preference",
                headers=lh,
                json={"role": "lessor", "want_check": False, "skip_acknowledged": True},
            )
            self.assertGreaterEqual(r_lessor_skip.status_code, 400)

            # 3) 임차인이라도 skip_acknowledged=False 이면 생략 거부
            r_no_ack = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/registry-preference",
                headers=pth,
                json={"role": "tenant", "want_check": False, "skip_acknowledged": False},
            )
            self.assertEqual(r_no_ack.status_code, 400)

            # 4) 임차인이 명시 확인 후 생략 → SKIPPED_BY_TENANT
            r_skip = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/registry-preference",
                headers=pth,
                json={"role": "tenant", "want_check": False, "skip_acknowledged": True},
            )
            self.assertEqual(r_skip.status_code, 200, r_skip.text)
            s_skip = r_skip.json()["session"]
            self.assertEqual(s_skip["registry_check_mode"], "SKIPPED_BY_TENANT")
            self.assertTrue(s_skip["step1_tenant_locked"])
            self.assertTrue(str(s_skip["tenant_recheck_at"]).startswith("SKIPPED_BY_TENANT:"))

            # 5) 임차인이 마음을 바꿔 다시 검토를 켜면 REQUIRED 로 복원
            r_on = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/registry-preference",
                headers=pth,
                json={"role": "tenant", "want_check": True, "skip_acknowledged": False},
            )
            self.assertEqual(r_on.status_code, 200, r_on.text)
            s_on = r_on.json()["session"]
            self.assertEqual(s_on["registry_check_mode"], "REQUIRED")
            self.assertFalse(s_on["step1_tenant_locked"])
            self.assertEqual(s_on["tenant_recheck_at"], "")
        finally:
            self._cleanup_room_sessions("904")

    def test_04_ai_assist_explain_clause_ocr_and_registry_plain_summary(self):
        """AI 쉬운 계약 요약, 자연어→표준 특약 다듬기, 메모 사진 OCR, 등기부 OCR plain_summary 를 검증한다."""
        lh = self._lessor_headers("chic")
        self._create_vacant_room(lh, "905")
        try:
            pid, pth = self._register_verified_prospect("에이아이임차인", "010-9050-9050", "980606")
            call = self.client.post(
                "/api/rent3_contract/sessions/call",
                headers=pth,
                json={
                    "caller_role": "tenant",
                    "branch": "main",
                    "room_no": "905",
                    "mode": "NEW",
                    "tenant_name": "에이아이임차인",
                    "tenant_phone": "010-9050-9050",
                    "id_tenant": pid,
                },
            )
            sid = call.json()["session"]["id"]
            self.client.post(
                f"/api/rent3_contract/sessions/{sid}/respond",
                headers=lh,
                json={"responder_role": "lessor"},
            )

            # 1) explain_contract (API 키 없이도 결정론적 쉬운 요약 동작)
            with mock.patch("apps.rent_dist._rent3_core.contract_ai._client") as m_cli:
                m_cli.return_value = mock.MagicMock(is_available=False)
                ex_res = self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/ai-assist",
                    headers=pth,
                    json={"action": "explain_contract"},
                )
            self.assertEqual(ex_res.status_code, 200, ex_res.text)
            ex_data = ex_res.json()["result"]
            self.assertIn("905호", ex_data["summary"])
            self.assertGreaterEqual(len(ex_data["bullets"]), 3)
            self.assertFalse(ex_data["ai_enhanced"])

            # 2) draft_clause (모킹된 Gemini 응답)
            fake_clause = _FakeGeminiClient({
                "clause": "임차인은 소형 반려묘 1마리를 사육할 수 있으며, 반려동물로 인한 벽지·장판 훼손 시 실비로 원상복구한다.",
                "explanation": "반려동물 허용 범위와 원상복구 책임을 명확히 정리했습니다.",
                "caution": "입주 전 벽지 사진을 남겨 두면 분쟁을 예방할 수 있습니다.",
            })
            with mock.patch("apps.rent_dist._rent3_core.contract_ai._client", return_value=fake_clause):
                cl_res = self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/ai-assist",
                    headers=pth,
                    json={"action": "draft_clause", "text": "고양이 1마리 키우고 벽지 긁으면 원상복구"},
                )
            self.assertEqual(cl_res.status_code, 200, cl_res.text)
            cl_data = cl_res.json()["result"]
            self.assertIn("반려묘", cl_data["clause"])
            self.assertTrue(cl_data["ai_enhanced"])

            # 3) ocr_memo (동의 없으면 400, 동의 + 모킹 Gemini OCR 성공)
            no_consent = self.client.post(
                f"/api/rent3_contract/sessions/{sid}/ai-assist",
                headers=lh,
                json={"action": "ocr_memo", "image_data_url": TINY_PNG_DATA_URL, "consent": False},
            )
            self.assertEqual(no_consent.status_code, 400)

            fake_ocr = _FakeGeminiClient({
                "deposit": 7000000,
                "contract_deposit": 700000,
                "monthly_rent": 420000,
                "maintenance_fee": 50000,
                "cleaning_fee": 90000,
                "move_in_date": "2026-12-15",
                "lease_term_months": 12,
                "special_terms": "입주 전 에어컨 청소 완료 조건",
                "facility_notes": "거실 벽지 미세 스크래치 1곳",
                "summary": "보증금 700만, 월세 42만, 입주일 2026-12-15 메모",
            })
            with mock.patch("apps.rent_dist._rent3_core.contract_ai._client", return_value=fake_ocr):
                ocr_res = self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/ai-assist",
                    headers=lh,
                    json={"action": "ocr_memo", "image_data_url": TINY_PNG_DATA_URL, "consent": True},
                )
            self.assertEqual(ocr_res.status_code, 200, ocr_res.text)
            ocr_ex = ocr_res.json()["result"]["extracted"]
            self.assertEqual(ocr_ex["deposit"], 7000000)
            self.assertEqual(ocr_ex["monthly_rent"], 420000)
            self.assertEqual(ocr_ex["move_in_date"], "2026-12-15")
            self.assertIn("에어컨", ocr_ex["special_terms"])

            # 4) registry-ai 가 plain_summary(쉬운 3줄 요약)를 함께 반환한다
            fake_reg = _FakeGeminiClient({
                "document_type": "registry",
                "issue_number": "1234-5678-9012",
                "viewed_at": "2026-11-27 10:00",
                "owners": [{"name": "홍임대", "share": "단독"}],
                "last_transfer_date": "2021-05-01",
                "mortgages": [],
                "restrictions": [],
                "notes": "",
            })
            with mock.patch("apps.rent_dist._rent3_core.registry_ai.GeminiClient", return_value=fake_reg):
                reg_ai = self.client.post(
                    f"/api/rent3_contract/sessions/{sid}/registry-ai",
                    headers=pth,
                    json={"attachment_data_url": TINY_PNG_DATA_URL, "consent": True},
                )
            self.assertEqual(reg_ai.status_code, 200, reg_ai.text)
            self.assertTrue(reg_ai.json()["success"])
            plain = reg_ai.json()["result"].get("plain_summary", "")
            self.assertTrue(len(plain) >= 15, plain)
            self.assertIn("홍임대", plain)
        finally:
            self._cleanup_room_sessions("905")

    def test_05_frontend_smart_contract_unification_and_ui_invariants(self):
        """전역 '스마트 계약' 용어 통일, 보류/재개·등기부 선택 체크·AI 도우미 UI 요소, 본문 폰트 12px 이상 불변식을 검증한다."""
        def read_fe(*rel):
            with open(os.path.join(ROOT, "apps", "rent_dist", *rel), encoding="utf-8") as fp:
                return fp.read()

        c_html = read_fe("rent3_contract", "frontend", "index.html")
        c_js = "\n".join(
            read_fe("rent3_contract", "frontend", f)
            for f in ("core.js", "home.js", "chat.js", "registry.js", "steps.js", "app.js")
        )
        # 핵심 신규 UI 요소 존재 확인
        for needle in (
            'id="btnPauseContract"',
            'id="wsAiExplain"',
            'id="wsAiClause"',
            'id="wsAiOcr"',
            "cg-pause-banner",
            "cg-reg-opt-box",
            "cg-ocr-box",
            "js-cg-reg-opt",
            "js-cg-reg-file",
            "js-cg-reg-ai-apply",
            "js-cg-resume",
            "smartFlowGuideHtml",
            "EXISTING_TENANT_MOVE",
            "registry-preference",
            "ai-assist",
            "plain_summary",
        ):
            self.assertIn(needle, c_html + "\n" + c_js, f"누락된 스마트 계약 UI 요소: {needle}")

        # 전역 '스마트 계약' 통일 확인
        self.assertIn("🤝 스마트 계약", read_fe("rent3_lessor", "frontend", "core.js"))
        self.assertIn("🤝 스마트 계약", read_fe("rent3_tenant", "frontend", "core.js"))
        self.assertIn("🤝 스마트 계약 시작", read_fe("rent3_tenant", "frontend", "myhome.js"))
        p_html = read_fe("rent3", "frontend", "index.html")
        self.assertIn("🤝 스마트 계약하기", p_html)
        self.assertIn("<span>스마트 계약</span>", p_html)
        self.assertNotIn("🤝 직거래 계약하기", p_html)
        self.assertIn("스마트 계약 요청", read_fe("rent3", "frontend", "listings.js"))
        self.assertIn("스마트 계약 호출", read_fe("rent3", "frontend", "session-bar.js"))

        # 인쇄(@media print) 제외 모든 CSS 폰트 크기 >= 12px
        non_print_html = c_html.split("@media print")[0]
        for m in re.finditer(r"font-size\s*:\s*([0-9.]+)px", non_print_html):
            self.assertGreaterEqual(float(m.group(1)), 12.0, f"12px 미만 폰트: {m.group(0)}")


if __name__ == "__main__":
    unittest.main()
