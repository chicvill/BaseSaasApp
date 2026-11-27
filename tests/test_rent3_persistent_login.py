"""
tests/test_rent3_persistent_login.py — 스마트폰 장기 로그인 유지(Refresh Token) 및 서버 재시작 복구 테스트.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import core.auth as core_auth
from core.gateway import create_gateway
from apps.rent_dist._rent3_core import (
    Lessor,
    Tenant,
    ensure_pool_seeded,
    pool,
    quicklogin as ql,
    refresh_sessions as rfs,
    use_pool_dir,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PL = "/api/rent3/pool"


class TestRent3PersistentLogin(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._orig_pool_dir = pool().pool_dir
        self._orig_pool_env = os.environ.get("RENT3_POOL_DIR")
        use_pool_dir(self._tmp.name)
        self._orig_secret = core_auth._secret_cache
        self._orig_demo = os.environ.get("RENT3_DEMO_LOGIN")
        self._orig_bk = os.environ.get("RENT3_BACKUP_DIR")
        os.environ["RENT3_DEMO_LOGIN"] = "1"
        os.environ["RENT3_BACKUP_DIR"] = os.path.join(self._tmp.name, "backups")
        ensure_pool_seeded(force=True)
        self.app = create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:")
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        core_auth._secret_cache = self._orig_secret
        if self._orig_demo is None:
            os.environ.pop("RENT3_DEMO_LOGIN", None)
        else:
            os.environ["RENT3_DEMO_LOGIN"] = self._orig_demo
        if self._orig_bk is None:
            os.environ.pop("RENT3_BACKUP_DIR", None)
        else:
            os.environ["RENT3_BACKUP_DIR"] = self._orig_bk
        use_pool_dir(self._orig_pool_dir)
        if self._orig_pool_env is None:
            os.environ.pop("RENT3_POOL_DIR", None)
        else:
            os.environ["RENT3_POOL_DIR"] = self._orig_pool_env
        self._tmp.cleanup()

    def _login(self, identifier: str, password: str = "1212", **extra) -> dict:
        r = self.client.post(
            f"{PL}/auth/login",
            json={"login_mode": "account", "identifier": identifier, "password": password, **extra},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_01_keep_logged_in_and_hash_only_storage(self) -> None:
        """로그인 유지 기본 켜짐: refresh_token 발급, 디스크엔 원문 대신 SHA-256 해시만 저장. 끄면 미발급."""
        res_on = self._login("chic", "1212", keep_logged_in=True)
        rt = res_on.get("refresh_token") or ""
        self.assertGreaterEqual(len(rt), 32)

        path = os.path.join(self._tmp.name, "_refresh_sessions.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fp:
            raw_disk = fp.read()
        self.assertNotIn(rt, raw_disk, "원문 refresh_token이 디스크에 평문으로 저장되면 안 됩니다.")
        stored = json.loads(raw_disk)
        self.assertEqual(len(stored), 1)

        res_off = self._login("chic", "1212", keep_logged_in=False)
        self.assertNotIn("refresh_token", res_off)

    def test_02_survives_server_restart_and_rotates_token(self) -> None:
        """서버 재시작(서명 비밀키 교체) 뒤 기존 토큰은 401이지만 /auth/refresh로 무중단 복구되고 1회용 로테이션된다."""
        # 1. 임대인 로그인
        res = self._login("chic", "1212")
        old_tok = res["token"]
        old_rt = res["refresh_token"]
        self.assertEqual(
            self.client.get("/api/rent3_lessor/dashboard?branch=main", headers={"Authorization": f"Bearer {old_tok}"}).status_code,
            200,
        )

        # 2. 서버 재시작 시뮬레이션: core.auth._secret_cache 교체
        core_auth._secret_cache = b"simulated_new_secret_after_restart_2026!!"

        # 기존 액세스 토큰은 401
        self.assertEqual(
            self.client.get("/api/rent3_lessor/dashboard?branch=main", headers={"Authorization": f"Bearer {old_tok}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(f"{PL}/auth/me", headers={"Authorization": f"Bearer {old_tok}"}).status_code,
            401,
        )

        # 3. /auth/refresh 로 조용히 재발급
        rf = self.client.post(f"{PL}/auth/refresh", json={"refresh_token": old_rt})
        self.assertEqual(rf.status_code, 200, rf.text)
        data = rf.json()
        new_tok = data["token"]
        new_rt = data["refresh_token"]
        self.assertNotEqual(new_tok, old_tok)
        self.assertNotEqual(new_rt, old_rt)
        self.assertEqual(data["role"], "lessor")

        # 4. 새 토큰으로 즉시 API 정상 동작
        self.assertEqual(
            self.client.get("/api/rent3_lessor/dashboard?branch=main", headers={"Authorization": f"Bearer {new_tok}"}).status_code,
            200,
        )

        # 5. 이미 쓴 예전 refresh_token 재사용(탈취 재전송)은 401 차단
        replay = self.client.post(f"{PL}/auth/refresh", json={"refresh_token": old_rt})
        self.assertEqual(replay.status_code, 401)

        # 6. 새로 받은 refresh_token 은 계속 유효
        rf2 = self.client.post(f"{PL}/auth/refresh", json={"refresh_token": new_rt})
        self.assertEqual(rf2.status_code, 200, rf2.text)

    def test_03_tenant_and_admin_refresh_and_moved_out_block(self) -> None:
        """임차인·관리인 모두 재시작 뒤 자동 갱신되고, 조회 기한이 끝난 퇴실 임차인은 갱신이 차단된다."""
        # 임차인
        t = next(x for x in Tenant.all() if x.status == "ACTIVE")
        tres = self._login(t.id, "1212")
        # 관리인
        ares = self._login("admin", "0202")

        core_auth._secret_cache = b"another_restart_secret_for_tenant_admin!!"

        trf = self.client.post(f"{PL}/auth/refresh", json={"refresh_token": tres["refresh_token"]})
        self.assertEqual(trf.status_code, 200, trf.text)
        self.assertEqual(
            self.client.get(
                f"/api/rent3_tenant/myhome/{t.id}",
                headers={"Authorization": f"Bearer {trf.json()['token']}"},
            ).status_code,
            200,
        )

        arf = self.client.post(f"{PL}/auth/refresh", json={"refresh_token": ares["refresh_token"]})
        self.assertEqual(arf.status_code, 200, arf.text)
        self.assertEqual(
            self.client.get(
                "/api/rent3_admin/overview",
                headers={"Authorization": f"Bearer {arf.json()['token']}"},
            ).status_code,
            200,
        )

        # 퇴실 후 조회 기한이 끝난 임차인은 갱신 차단
        t_live = Tenant.get(t.id)
        assert t_live is not None
        t_live.status = "MOVED_OUT"
        t_live.readonly_until = "2020-01-01"
        t_live.save("test_moved_out")
        blocked = self.client.post(f"{PL}/auth/refresh", json={"refresh_token": trf.json()["refresh_token"]})
        self.assertEqual(blocked.status_code, 401)

    def test_04_logout_and_password_change_revoke_refresh(self) -> None:
        """명시적 로그아웃은 해당 refresh_token 을, 비밀번호 변경은 계정의 모든 기기 refresh_token 을 즉시 폐기한다."""
        d1 = self._login("chic", "1212")
        d2 = self._login("chic", "1212")

        # d1 로그아웃 (refresh_token 전달)
        lo = self.client.post(
            f"{PL}/auth/logout",
            headers={"Authorization": f"Bearer {d1['token']}"},
            json={"refresh_token": d1["refresh_token"]},
        )
        self.assertEqual(lo.status_code, 200)
        self.assertEqual(self.client.post(f"{PL}/auth/refresh", json={"refresh_token": d1["refresh_token"]}).status_code, 401)
        # 빈 본문 로그아웃 호환성
        lo_empty = self.client.post(f"{PL}/auth/logout", headers={"Authorization": f"Bearer {d1['token']}"})
        self.assertEqual(lo_empty.status_code, 200)

        # d2 는 아직 유효 → 비밀번호 변경 시 모든 기기 refresh 폐기
        d3 = self._login("chic", "1212")
        ch = self.client.post(
            f"{PL}/auth/me/password",
            headers={"Authorization": f"Bearer {d2['token']}"},
            json={"current_password": "1212", "new_password": "NewStrong2026!"},
        )
        self.assertEqual(ch.status_code, 200, ch.text)
        self.assertEqual(self.client.post(f"{PL}/auth/refresh", json={"refresh_token": d2["refresh_token"]}).status_code, 401)
        self.assertEqual(self.client.post(f"{PL}/auth/refresh", json={"refresh_token": d3["refresh_token"]}).status_code, 401)

    def test_05_pin_login_never_issues_refresh_token(self) -> None:
        """간편 비밀번호(PIN) 로그인에는 장기 갱신 토큰을 발급하지 않는다(1시간/3시간 제한 유지)."""
        t = next(x for x in Tenant.all() if x.status == "ACTIVE")
        ql.set_pin(t, "2580")
        _dev, qk = ql.new_device(t, "테스트폰")
        r = self.client.post(f"{PL}/auth/pin-login", json={"qk": qk, "pin": "2580"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("via_pin"))
        self.assertNotIn("refresh_token", body)

    def test_06_frontend_persistent_login_hooks(self) -> None:
        """프론트엔드 로그인 유지 체크박스 · 세션 가드 자동 갱신 · 포털 자동 입장 연결 정적 점검."""
        def rd(*parts: str) -> str:
            with open(os.path.join(ROOT, *parts), encoding="utf-8") as fp:
                return fp.read()
        guard = rd("shared_ui", "rent3_session_guard.js")
        for needle in (
            "/api/rent3/pool/auth/refresh",
            "rent3_refresh_token",
            "isViaPinToken",
            "qk=",
            "아이디·비밀번호로 다시 로그인",
        ):
            self.assertIn(needle, guard, needle)
        self.assertNotIn("window.BASS_ERROR_TIMEOUT", guard)

        idx = rd("apps", "rent_dist", "rent3", "frontend", "index.html")
        self.assertIn('id="chkKeepLoggedIn"', idx)
        self.assertIn("로그인 상태 유지", idx)

        st = rd("apps", "rent_dist", "rent3", "frontend", "state.js")
        for needle in ("rent3_refresh_token", "tryRefreshSession", "refresh_token: rt"):
            self.assertIn(needle, st, needle)

        app_js = rd("apps", "rent_dist", "rent3", "frontend", "app.js")
        for needle in ("autoEnter", "tryRefreshSession", "p.exp * 1000", "window.location.replace(u.redirect_url)"):
            self.assertIn(needle, app_js, needle)

        # max sessions limit 단위 검증
        rfs.revoke_all_for_user("lessor", "lsr_cap")
        tokens = [rfs.issue_refresh("lessor", "lsr_cap", username="cap") for _ in range(rfs.MAX_SESSIONS_PER_USER + 2)]
        self.assertIsNone(rfs.peek_refresh(tokens[0]))
        self.assertIsNotNone(rfs.peek_refresh(tokens[-1]))


if __name__ == "__main__":
    unittest.main()
