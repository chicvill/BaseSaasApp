"""
core/scaffold.py — 신규 마이크로 SaaS 앱 템플릿 생성 엔진.
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APPS_ROOT = os.path.join(ROOT, "apps")

APP_PY_TEMPLATE = '''"""
apps/{app_id}/app.py — {app_name} 앱 정의서.
게이트웨이가 이 파일 하나만 보고 앱을 자동으로 마운트합니다.
"""
from fastapi import APIRouter
from core import BaseSaaSApp
from .backend import domain
from .backend.api import router


class {class_name}App(BaseSaaSApp):
    app_id = "{app_id}"
    app_name = "{app_name}"
    icon = "{icon}"
    category = "{category}"
    description = "{description}"
    version = "1.0.0"
    features = ["실시간 관제", "객체 도메인 모델", "자동 이력 로깅"]
    order = 50

    def get_router(self) -> APIRouter:
        return router

    def on_startup(self) -> None:
        domain.seed()

    def health_check(self):
        return {{"items_count": domain.Item.count()}}


app_instance = {class_name}App()
'''

DOMAIN_PY_TEMPLATE = '''"""
apps/{app_id}/backend/domain.py — 도메인 객체.
스스로 저장, 조회, 로깅을 수행하는 스마트 도메인 모델.
"""
from typing import Any, Dict, List
from core import Obj, NotFound, Conflict, DomainError


class Item(Obj):
    label = "항목"
    scope_field = "branch_id"

    branch_id: str = "main"
    code: str
    name: str
    status: str = "READY"    # READY | ACTIVE | COMPLETED

    def activate(self) -> "Item":
        if self.status == "ACTIVE":
            raise Conflict(f"{{self.name}}은(는) 이미 활성화되어 있습니다.")
        self.status = "ACTIVE"
        self.save("activated")
        return self

    def complete(self) -> "Item":
        if self.status != "ACTIVE":
            raise Conflict(f"{{self.name}}은(는) 활성 상태가 아닙니다.")
        self.status = "COMPLETED"
        self.save("completed")
        return self

    def card(self) -> Dict[str, Any]:
        return {{
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "status": self.status,
            "scope": self.scope
        }}


def seed(branch_id: str = "main") -> int:
    if Item.count(branch_id):
        return 0
    for i in range(1, 5):
        Item(branch_id=branch_id, code=f"ITM-{{i:02d}}", name=f"{app_name} 기본 항목 {{i}}").save("seed")
    return 4
'''

API_PY_TEMPLATE = '''"""
apps/{app_id}/backend/api.py — HTTP ↔ 도메인 변환 라우터.
"""
from typing import Any, Dict
from fastapi import APIRouter, Body
from .domain import Item

router = APIRouter()


@router.get("/status")
def status(branch_id: str = "main") -> Dict[str, Any]:
    return {{"success": True, "app_id": "{app_id}", "total_items": Item.count(branch_id)}}


@router.get("/items")
def list_items(branch_id: str = "main") -> Dict[str, Any]:
    return {{"success": True, "items": [item.card() for item in Item.find(branch_id)]}}


@router.post("/items/{{code}}/activate")
def activate_item(code: str, branch_id: str = "main") -> Dict[str, Any]:
    item = Item.first(branch_id, code=code.upper())
    if not item:
        raise NotFound(f"{{code}} 항목을 찾을 수 없습니다.")
    item.activate()
    return {{"success": True, "message": f"{{item.name}} 활성화 완료", "item": item.card()}}


@router.post("/items/{{code}}/complete")
def complete_item(code: str, branch_id: str = "main") -> Dict[str, Any]:
    item = Item.first(branch_id, code=code.upper())
    if not item:
        raise NotFound(f"{{code}} 항목을 찾을 수 없습니다.")
    item.complete()
    return {{"success": True, "message": f"{{item.name}} 완료 처리", "item": item.card()}}
'''

INDEX_HTML_TEMPLATE = '''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{app_name} | BaseSaasApp</title>
  <link rel="stylesheet" href="/shared/ui/base.css">
  <style>
    header.top {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 26px 0 12px; flex-wrap: wrap; }}
    .item-card {{ display: flex; justify-content: space-between; align-items: center; padding: 16px; }}
  </style>
</head>
<body>
<main class="bass-container">
  <header class="top">
    <div>
      <a href="/" class="bass-muted" id="back-portal">← 포털</a>
      <h1>{icon} <span class="bass-gradient-text">{app_name}</span></h1>
      <p class="bass-muted" style="margin:0">{description}</p>
    </div>
    <div class="bass-row">
      <a class="bass-btn bass-btn--ghost" href="manual.html">📘 운영 매뉴얼</a>
      <button class="bass-btn" id="refresh-btn">🔄 새로고침</button>
    </div>
  </header>

  <div id="app-alert"></div>

  <section style="margin-top:20px">
    <h2>항목 현황</h2>
    <div class="bass-grid" id="item-grid" style="grid-template-columns:repeat(auto-fill, minmax(280px, 1fr))">
      <div class="bass-skeleton"></div>
    </div>
  </section>
</main>

<script src="/shared/ui/bass.js"></script>
<script src="app.js"></script>
</body>
</html>
'''

APP_JS_TEMPLATE = '''(() => {{
  "use strict";
  const APP = "{app_id}";
  const $ = (id) => document.getElementById(id);
  const esc = Bass.escapeHtml;

  async function load() {{
    try {{
      const res = await Bass.api(APP, "/items");
      $("item-grid").innerHTML = res.items.map((it) => `
        <article class="bass-card item-card bass-reveal" data-code="${{esc(it.code)}}">
          <div>
            <b>${{esc(it.code)}}</b> · ${{esc(it.name)}}<br>
            <span class="bass-badge bass-badge--${{it.status === 'ACTIVE' ? 'info' : it.status === 'COMPLETED' ? 'ok' : 'warn'}}">${{esc(it.status)}}</span>
          </div>
          <div>
            ${{it.status === 'READY'
              ? `<button class="bass-btn bass-btn--primary bass-btn--sm" data-act="activate">활성화</button>`
              : it.status === 'ACTIVE'
              ? `<button class="bass-btn bass-btn--sm" data-act="complete">완료</button>`
              : `<span class="bass-muted" style="font-size:13px">종료됨</span>`}}
          </div>
        </article>`).join("") || "<p class='bass-muted'>항목이 없습니다.</p>";
    }} catch (err) {{
      Bass.showError(err);
    }}
  }}

  document.addEventListener("click", async (e) => {{
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const code = btn.closest("[data-code]").dataset.code;
    const act = btn.dataset.act;
    await Bass.withLock(btn, async () => {{
      const res = await Bass.api(APP, `/items/${{code}}/${{act}}`, {{ method: "POST" }});
      Bass.updateAppAlert("success", res.message);
      await load();
    }});
  }});

  $("refresh-btn").addEventListener("click", () => Bass.withLock($("refresh-btn"), load));
  load();
}})();
'''

MANUAL_HTML_TEMPLATE = '''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{app_name} 공식 운영 매뉴얼 & 5역할 협업 가이드</title>
  <link rel="stylesheet" href="/shared/ui/base.css">
  <link rel="stylesheet" href="/shared/ui/manual.css">
</head>
<body>
<main class="manual">
  <header class="manual-header">
    <a href="./" class="bass-btn bass-btn--ghost no-print">← 앱으로</a>
    <button class="bass-btn bass-btn--primary" onclick="window.print()">🖨️ 매뉴얼 인쇄 / PDF 저장</button>
  </header>

  <h1>{icon} <span class="bass-gradient-text">{app_name}</span> 운영 매뉴얼</h1>
  <p class="bass-muted">앱 ID: <code>{app_id}</code> · 카테고리: {category}</p>

  <h2>1. 🌟 5역할 협업 거버넌스 안내</h2>
  <ul>
    <li><b>🧭 Advisor (자문가)</b>: 나침반 3원칙(간소화, 보안 리스크 최소화, 1분 내 추적성)으로 오버엔지니어링 차단</li>
    <li><b>📚 Librarian (사서)</b>: {app_name} 관련 업무 규칙 및 외부 규격 선행 팩트체크</li>
    <li><b>🎓 Tutor (튜터)</b>: 도메인 산출식 및 사용자 가이드 실시간 해설</li>
    <li><b>🛠️ Editor (엔지니어)</b>: Kernel Obj(domain.py) 기반 도메인 구현 및 테스트 무결성 보장</li>
    <li><b>🤝 Roommate (동거인)</b>: 진도 요약 및 배포/릴리즈 체크</li>
  </ul>

  <h2>2. 개요</h2>
  <p>{description}</p>

  <h2>3. 사용 방법</h2>
  <ol>
    <li>화면에서 <b>READY</b> 상태의 항목의 [활성화] 버튼을 누릅니다.</li>
    <li>진행 완료 후 [완료] 버튼을 누르면 상태가 <b>COMPLETED</b>로 전환됩니다.</li>
    <li>모든 상태 변경은 중앙 변경 이력에 영구 보존됩니다.</li>
  </ol>
</main>
</body>
</html>
'''

SKILL_MD_TEMPLATE = '''---
name: bass-{app_id}-ops
description: Operational business rules, domain invariants, 5-role collaboration governance, and verification instructions for {app_name} (apps/{app_id}).
---

# {icon} BaseSaasApp {app_name} ({app_id}) 운영 거버넌스 스킬

본 스킬은 `{app_name}` 마이크로 SaaS 앱의 비즈니스 불변 규칙과 5역할 협업 사이클을 준수하기 위한 공식 운영 명세서입니다.

---

## 1. 🌟 5역할 협업 개발 거버넌스 (Five-Role Governance)

모든 요구사항 반영 시 다음 5가지 페르소나 단계를 순차적으로 통과해야 합니다:

1. **🧭 Advisor (자문가)**: 나침반 3원칙(수동 대비 간소화, 보안 리스크 최소화, 1분 내 추적성)으로 오버엔지니어링 제동.
2. **📚 Librarian (사서)**: {app_name} 관련 업무 규칙 및 외부 규격 사전 조사 (선행 팩트체크 원칙).
3. **🎓 Tutor (튜터)**: 도메인 산출식 및 manual.html 매뉴얼 실시간 동기화.
4. **🛠️ Editor (엔지니어)**: Kernel Obj(domain.py) 기반 도메인 구현 및 단위 테스트 무결성 보장.
5. **🤝 Roommate (동거인)**: 검증 통과 확인, 진도 요약 및 배포/커밋 완료.

---

## 2. 🏛️ 도메인 비즈니스 불변 규칙 (Domain Invariants)
- 모든 엔티티는 `core.kernel.Obj` 를 상속받아 스키마리스 WAL로 영구 보존됩니다.
- 상태 변경 시 이력 로그(`log()`)가 자동으로 기록되어 감사 추적이 가능해야 합니다.

---

## 3. 🚀 자가 업그레이드 검증 (Self-Upgrading)
- 코드 수정 후 `apps/{app_id}/tests/test_domain.py` 단위 테스트를 반드시 실행하여 100% 통과를 확인합니다.
'''

TEST_DOMAIN_TEMPLATE = '''"""
apps/{app_id}/tests/test_domain.py — {app_name} 도메인 모델 단위 테스트.
"""
import unittest
from core.kernel.obj import use_store
from apps.{app_id}.backend import domain


class Test{class_name}Domain(unittest.TestCase):
    def setUp(self):
        use_store(":memory:")
        self.branch = "test_branch"

    def test_item_lifecycle(self):
        item = domain.Item(
            branch_id=self.branch,
            code="T01",
            name="테스트 항목",
            status="READY"
        ).save("created")

        self.assertEqual(item.status, "READY")
        item.activate()
        self.assertEqual(item.status, "ACTIVE")
        item.complete()
        self.assertEqual(item.status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
'''


def to_camel(s: str) -> str:
    return "".join(part.capitalize() for part in s.split("_"))


def create_app(app_id: str, app_name: str, icon: str, category: str, description: str):
    app_dir = os.path.join(APPS_ROOT, app_id)
    if os.path.exists(app_dir):
        print(f"❌ 이미 존재하는 앱입니다: {app_dir}")
        sys.exit(1)

    backend_dir = os.path.join(app_dir, "backend")
    frontend_dir = os.path.join(app_dir, "frontend")
    tests_dir = os.path.join(app_dir, "tests")
    skill_dir = os.path.join(ROOT, ".agents", "skills", f"bass-{app_id}-ops")

    os.makedirs(backend_dir, exist_ok=True)
    os.makedirs(frontend_dir, exist_ok=True)
    os.makedirs(tests_dir, exist_ok=True)
    os.makedirs(skill_dir, exist_ok=True)

    ctx = {
        "app_id": app_id,
        "app_name": app_name,
        "class_name": to_camel(app_id),
        "icon": icon,
        "category": category,
        "description": description or f"{app_name} 통합 관리 서비스",
    }

    files = {
        os.path.join(app_dir, "__init__.py"): "",
        os.path.join(app_dir, "app.py"): APP_PY_TEMPLATE.format(**ctx),
        os.path.join(backend_dir, "__init__.py"): "",
        os.path.join(backend_dir, "domain.py"): DOMAIN_PY_TEMPLATE.format(**ctx),
        os.path.join(backend_dir, "api.py"): API_PY_TEMPLATE.format(**ctx),
        os.path.join(frontend_dir, "index.html"): INDEX_HTML_TEMPLATE.format(**ctx),
        os.path.join(frontend_dir, "app.js"): APP_JS_TEMPLATE.format(**ctx),
        os.path.join(frontend_dir, "manual.html"): MANUAL_HTML_TEMPLATE.format(**ctx),
        os.path.join(tests_dir, "__init__.py"): "",
        os.path.join(tests_dir, "test_domain.py"): TEST_DOMAIN_TEMPLATE.format(**ctx),
        os.path.join(skill_dir, "SKILL.md"): SKILL_MD_TEMPLATE.format(**ctx),
    }

    for path, content in files.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    safe_print("=" * 60)
    safe_print(f"[OK] 신규 SaaS 앱 및 5역할 거버넌스 스킬 생성 완료: {ctx['icon']} {ctx['app_name']} ({app_id})")
    safe_print(f"• 앱 디렉터리 : apps/{app_id}/")
    safe_print(f"• 거버넌스 스킬: .agents/skills/bass-{app_id}-ops/SKILL.md")
    safe_print("• 구성 요소:")
    safe_print(f"  - apps/{app_id}/app.py (BaseSaaSApp 객체 정의)")
    safe_print(f"  - apps/{app_id}/backend/domain.py (도메인 모델)")
    safe_print(f"  - apps/{app_id}/backend/api.py (API 라우터)")
    safe_print(f"  - apps/{app_id}/frontend/index.html (반응형 UI)")
    safe_print(f"  - apps/{app_id}/frontend/manual.html (인쇄 최적화 운영 매뉴얼)")
    safe_print(f"  - apps/{app_id}/tests/test_domain.py (단위 테스트)")
    safe_print(f"  - .agents/skills/bass-{app_id}-ops/SKILL.md (5역할 협업 운영 스킬)")
    safe_print("\n👉 게이트웨이를 실행하면 포털에 즉시 자동 마운트됩니다!")
    safe_print("=" * 60)

