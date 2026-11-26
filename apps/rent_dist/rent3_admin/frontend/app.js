/* apps/rent3_admin/frontend/app.js — 플랫폼 총괄 관리인 관제 클라이언트 (탭 라우터 · KPI · 알림 부트스트랩) */
(function () {
  "use strict";
  const A = window.R3A;
  const { APP_ID, fmtWon } = A;
  const S = A.state;

  // 알림 센터 링크(?tab=lessors 등)로 들어오면 그 탭부터 연다
  const tabParam = new URLSearchParams(location.search).get("tab");
  S.currentTab = ["support", "lessors", "portfolio", "ops", "pool"].includes(tabParam) ? tabParam : "support";

  function syncActiveNav(tab) {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === tab));
    document.querySelectorAll("#adminBottomNav [data-atab]").forEach((b) => b.classList.toggle("is-active", b.dataset.atab === tab));
  }

  function renderTab() {
    if (!S.ov) return;
    if (S.currentTab !== "support") clearInterval(S.spTimer);
    if (S.currentTab === "support") A.renderSupportTab().catch(Bass.showError);
    else if (S.currentTab === "lessors") A.renderLessorsTab();
    else if (S.currentTab === "portfolio") A.renderPortfolioTab();
    else if (S.currentTab === "ops") A.renderOpsTab().catch(Bass.showError);
    else A.renderPoolTab();
  }

  function switchAdminTab(tab) {
    S.currentTab = tab;
    syncActiveNav(tab);
    renderTab();
  }

  async function refresh() {
    S.ov = await Bass.api(APP_ID, "/overview");
    const meta = S.ov.pool_meta || {};
    const kpi = S.ov.kpi || {};
    document.getElementById("poolBadge").textContent = `JSON Pool v${meta.total_revision} (${meta.total_objects} 객체)`;

    const occPct = kpi.total_rooms > 0 ? Math.round((Number(kpi.occupied_rooms || 0) * 100) / Number(kpi.total_rooms)) : 0;
    document.getElementById("kpiGrid").innerHTML =
      `<div class="r3-kpi r3-kpi--info"><span class="r3-kpi__lbl">🏢 가맹 건물</span><strong class="r3-kpi__val">${kpi.total_buildings}채</strong></div>` +
      `<div class="r3-kpi r3-kpi--ok"><span class="r3-kpi__lbl">🏠 호실 가동</span><strong class="r3-kpi__val">${kpi.occupied_rooms}/${kpi.total_rooms}실 (${occPct}%)</strong><div class="r3-kpi__bar"><i style="width:${occPct}%;"></i></div></div>` +
      `<div class="r3-kpi"><span class="r3-kpi__lbl">💳 월 이용료</span><strong class="r3-kpi__val">${fmtWon(kpi.monthly_saas_mrr)}</strong></div>` +
      `<div class="r3-kpi"><span class="r3-kpi__lbl">📂 Pool 버전</span><strong class="r3-kpi__val">v${meta.total_revision} (${meta.total_objects})</strong></div>`;

    renderTab();
  }

  Object.assign(A, { refresh, switchAdminTab, renderTab });

  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    switchAdminTab(btn.dataset.tab);
  });

  const adminBNav = document.getElementById("adminBottomNav");
  if (adminBNav) {
    adminBNav.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-atab]");
      if (btn) switchAdminTab(btn.dataset.atab);
    });
  }

  syncActiveNav(S.currentTab);
  if (window.R3Notify) R3Notify.start({ url: "/api/rent3_admin/notifications" });     // 🔔 알림 센터: 문의·가맹 심사·홈택스 확인·가맹비 연체·세금계산서 발행
  refresh().catch(Bass.showError);
})();
