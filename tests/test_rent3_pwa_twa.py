"""
tests/test_rent3_pwa_twa.py — Rent3 PWA(Network-First) · Google Play TWA · 포털 ☰ 앱 설치 안내 검증.
"""
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRent3PwaTwa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="rent3_pwa_test_")
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

    def test_01_manifest_and_icons_served_valid(self):
        """/rent3/manifest.json 및 모든 선언 아이콘이 200 OK · 유효한 PWA 규격으로 응답한다."""
        r = self.client.get("/rent3/manifest.json")
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["start_url"], "/rent3/")
        self.assertEqual(m["scope"], "/")
        self.assertEqual(m["short_name"], "Rent3")
        self.assertGreaterEqual(len(m["icons"]), 3)
        for ic in m["icons"]:
            ir = self.client.get(ic["src"].split("?")[0])
            self.assertEqual(ir.status_code, 200, ic["src"])
            self.assertEqual(ir.content[:8], b"\x89PNG\r\n\x1a\n", ic["src"])

    def test_02_network_first_sw_and_twa_assetlinks_api(self):
        """Network-First SW(/rent3/sw.js)는 API를 캐시하지 않으며, TWA assetlinks API는 환경변수를 반영한다."""
        sw = self.client.get("/rent3/sw.js")
        self.assertEqual(sw.status_code, 200)
        sw_txt = sw.text
        for needle in (
            'req.method !== "GET"',
            'url.pathname.startsWith("/api/")',
            "self.skipWaiting()",
            "self.clients.claim()",
            'req.mode === "navigate"',
            "인터넷 연결을 확인해 주세요",
        ):
            self.assertIn(needle, sw_txt, needle)

        # 기본 TWA assetlinks.json (익명 공개 접근 가능)
        al0 = self.client.get("/api/rent3/pool/pwa/assetlinks.json")
        self.assertEqual(al0.status_code, 200, al0.text)
        arr0 = al0.json()
        self.assertIsInstance(arr0, list)
        self.assertEqual(arr0[0]["relation"], ["delegate_permission/common.handle_all_urls"])
        self.assertEqual(arr0[0]["target"]["namespace"], "android_app")
        self.assertEqual(arr0[0]["target"]["package_name"], "io.mqnet.rent3")

        # 환경변수 지정 시 패키지명·지문 목록 반영
        with mock.patch.dict(
            os.environ,
            {
                "RENT3_TWA_PACKAGE_NAME": "kr.mqnet.rent3.app",
                "RENT3_TWA_SHA256_CERT": "AA:BB:CC, DD:EE:FF",
            },
        ):
            al1 = self.client.get("/api/rent3/pool/pwa/assetlinks.json").json()
            self.assertEqual(al1[0]["target"]["package_name"], "kr.mqnet.rent3.app")
            self.assertEqual(al1[0]["target"]["sha256_cert_fingerprints"], ["AA:BB:CC", "DD:EE:FF"])

    def test_03_all_5_apps_wired_and_portal_hamburger_has_pwa_twa_guide(self):
        """Rent3 5개 앱 모두 PWA 메타/스크립트가 연결되어 있고, 포털 ☰ 메뉴에 앱 설치·PWA/TWA 안내가 포함된다."""
        base = os.path.join(ROOT, "apps", "rent_dist")
        for app_id in ("rent3", "rent3_lessor", "rent3_tenant", "rent3_contract", "rent3_admin"):
            with open(os.path.join(base, app_id, "frontend", "index.html"), encoding="utf-8") as fp:
                html = fp.read()
            self.assertIn('rel="manifest"', html, app_id)
            self.assertIn("apple-touch-icon", html, app_id)
            self.assertIn('name="theme-color"', html, app_id)
            self.assertIn("pwa.js", html, app_id)

        # 포털 ☰ 메뉴에 앱 설치·PWA/TWA 안내 버튼 포함 + 연락처 data-r3-plain 불변식(2개) 유지
        with open(os.path.join(base, "rent3", "frontend", "index.html"), encoding="utf-8") as fp:
            portal_html = fp.read()
        drawer = portal_html[portal_html.index("data-r3-drawer"):portal_html.index("</aside>")]
        self.assertIn('id="btnPwaGuide"', drawer)
        self.assertIn("앱 설치 · PWA/TWA 안내", drawer)
        self.assertIn('/rent3/contest-plan.html', drawer)
        self.assertIn("공모전 출품 계획서", drawer)
        self.assertEqual(drawer.count("data-r3-plain"), 2)

        # 공모전 출품 계획서 페이지(HTML) 및 원본 MD 검증
        cp = self.client.get("/rent3/contest-plan.html")
        self.assertEqual(cp.status_code, 200)
        for needle in (
            "부동산서비스산업 창업경진대회",
            "국토교통 데이터활용 경진대회",
            "초기창업패키지",
            "공공데이터포털",
            "Google Play TWA",
            "apps/equip_rent",
            "contestChecklist",
        ):
            self.assertIn(needle, cp.text, needle)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "rent3_contest_plan.md")))
        self.assertFalse(re.compile(r"font-size:\s*(?:9|10|11)(?:\.\d)?px").search(cp.text))

        # pwa.js 안내 모달 핵심 내용 및 폰트 >= 12px 검증
        with open(os.path.join(base, "rent3", "frontend", "pwa.js"), encoding="utf-8") as fp:
            pwa_js = fp.read()
        for needle in (
            "PWA (Progressive Web App)",
            "TWA (Trusted Web Activity)",
            "Network-First",
            "Google Play",
            "홈 화면에 추가",
            "beforeinstallprompt",
            "/rent3/sw.js",
            "/rent3/manifest.json",
            "/api/rent3/pool/pwa/assetlinks.json",
            "@bubblewrap/cli",
            "openGuideModal",
        ):
            self.assertIn(needle, pwa_js, needle)
        small_font = re.compile(r"font-size:\s*(?:9|10|11)(?:\.\d)?px")
        self.assertFalse(small_font.search(pwa_js), "pwa.js에 12px 미만 폰트 감지")


if __name__ == "__main__":
    unittest.main()
