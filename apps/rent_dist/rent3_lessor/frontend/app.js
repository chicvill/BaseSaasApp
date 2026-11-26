/* apps/rent_dist/rent3_lessor/frontend/app.js — 대시보드 갱신, 탭 라우팅, 헤더 시계, 서랍 메뉴 바인딩 오케스트레이터. */
(function () {
  "use strict";
  const L = window.R3L;
  if (!L) return;

  const {
    APP_ID, VIA_PIN, PIN_TABS, TAB_TITLES, elTabContent, state: S,
    buildMenu, fmtWon, pinLimit, applyPinBar, fullLogin, closeDrawer, openModal,
  } = L;

  // 날짜·시각을 🔔 바로 위(오른쪽 끝)에 둔다: ☰·🔔 이 있는 머리줄에 오른쪽 세로 묶음(시각 위, 🔔 아래)을 만든다.
  (function placeClockAboveBell() {
    const put = () => {
      const bell = document.querySelector(".r3-bell:not(.r3-bell--float)"), clk = document.getElementById("bldgClock");
      if (!bell || !clk || !bell.parentNode) return !!(bell && bell.parentNode && bell.parentNode.classList.contains("r3-hdr-right"));
      if (bell.parentNode.classList.contains("r3-hdr-right")) return true;
      const col = document.createElement("div");
      col.className = "r3-hdr-right";
      col.style.cssText = "display:flex;flex-direction:column;align-items:flex-end;flex:none;margin-left:auto;";
      bell.parentNode.insertBefore(col, bell);
      bell.style.marginLeft = "0";
      col.appendChild(clk);
      col.appendChild(bell);
      return true;
    };
    if (put()) return;
    const hdr = document.querySelector("header.bass-card") || document.body;
    const mo = new MutationObserver(() => { if (put()) mo.disconnect(); });
    mo.observe(hdr, { childList: true, subtree: true });
    setTimeout(() => mo.disconnect(), 15000);
  })();
  const tickClock = R3Shell.startClock(document.getElementById("bldgTitle"), () => S.hdrName, document.getElementById("bldgClock"));

  async function refresh() {
    const qAsOf = S.selectedAsOfDate ? `&as_of_date=${encodeURIComponent(S.selectedAsOfDate)}` : "";
    S.dash = await Bass.api(APP_ID, `/dashboard?branch=${encodeURIComponent(S.currentBranch)}${qAsOf}`);
    document.getElementById("poolRevBadge").textContent = `JSON Pool v${S.dash.pool_revision}`;
    const mailOn = !!(S.dash.features && S.dash.features.mail);
    if (mailOn !== S.MAIL_ON) {
      S.MAIL_ON = mailOn;
      R3Shell.renderMenu(document.querySelector("[data-r3-drawer]"), buildMenu());
      bindDrawerActions();
    }
    L.renderFranchiseBanner();
    applyPinBar();

    const b = S.dash.building || {};
    const l = S.dash.lessor || {};
    S.hdrName = `${b.building_name || ""}${l.username ? `[${l.username}]` : ""}`;
    tickClock();
    R3Shell.proxyBanner(`${b.building_name || ""} ${S.selRoomNo ? S.selRoomNo + "호 " : ""}(임대인 입장)`);

    const fin = S.dash.financial_summary || {};
    const ymMonth = Number((S.dash.target_ym || "").slice(5, 7)) || (new Date().getMonth() + 1);
    const mTarget = Number(S.dash.monthly_target || 0);
    const mPaid = Number(S.dash.monthly_collected || 0);
    const mRate = Number(S.dash.monthly_collection_rate ?? (mTarget > 0 ? Math.round((mPaid * 100) / mTarget) : 0));
    const mDueSoon = Number(S.dash.monthly_due_soon ?? S.dash.total_due_soon_branch ?? 0);
    const totalOverdue = S.dash.total_overdue_branch != null ? Number(S.dash.total_overdue_branch) : Number(S.dash.total_unpaid_branch || 0);
    const mOverdue = S.dash.monthly_overdue != null ? Number(S.dash.monthly_overdue) : Math.max(0, Number(S.dash.monthly_unpaid || 0) - mDueSoon);
    const priorOverdue = Math.max(0, totalOverdue - mOverdue);
    const occRooms = Number(fin.occupied_rooms || 0);
    const totRooms = Number(fin.total_rooms || 0);
    const occPct = Math.max(0, Math.min(100, Number(fin.occupancy_rate || 0)));
    const paidPct = Math.max(0, Math.min(100, mRate));
    const dueSoonPct = mTarget > 0 ? Math.max(0, Math.min(100, Math.round((mDueSoon * 100) / mTarget))) : 0;
    const overduePct = mTarget > 0 ? Math.max(0, Math.min(100, Math.round((mOverdue * 100) / mTarget))) : 0;
    const overdueColor = (mOverdue > 0 || priorOverdue > 0) ? "var(--err)" : "var(--ok)";
    const priorHtml = priorOverdue > 0
      ? `<div class="r3-kpi__sub" style="color:var(--err);">이전 달 이월 +${fmtWon(priorOverdue)} (누적 ${fmtWon(totalOverdue)})</div>`
      : "";
    document.getElementById("kpiGrid").innerHTML =
      `<div class="r3-kpi r3-kpi--split">` +
        `<div class="r3-kpi__half" aria-label="${ymMonth}월 목표액"><span class="r3-kpi__lbl">🎯 ${ymMonth}월 목표</span><strong class="r3-kpi__val">${fmtWon(mTarget)}</strong></div>` +
        `<div class="r3-kpi__half r3-kpi__half--occ"><span class="r3-kpi__lbl">🏢 가동률</span><strong class="r3-kpi__val">${occRooms}실/${totRooms}실 (${occPct}%)</strong><div class="r3-kpi__bar"><i style="width:${occPct}%;"></i></div></div>` +
      `</div>` +
      `<div class="r3-kpi r3-kpi--ok"><span class="r3-kpi__lbl">💰 수납액</span><strong class="r3-kpi__val" style="color:var(--ok);">${fmtWon(mPaid)} (${mRate}%)</strong><div class="r3-kpi__bar"><i style="width:${paidPct}%;"></i></div></div>` +
      `<div class="r3-kpi ${mDueSoon > 0 ? "r3-kpi--warn" : ""}"><span class="r3-kpi__lbl">⏳ 대기액 <em class="r3-kpi__hint">납입일 이전/까지</em></span><strong class="r3-kpi__val" style="color:${mDueSoon > 0 ? "#B45309" : "var(--muted)"};">${fmtWon(mDueSoon)}${mTarget > 0 ? ` (${dueSoonPct}%)` : ""}</strong><div class="r3-kpi__bar"><i style="width:${dueSoonPct}%;"></i></div></div>` +
      `<div class="r3-kpi ${(mOverdue > 0 || priorOverdue > 0) ? "r3-kpi--err" : "r3-kpi--ok"}"><span class="r3-kpi__lbl">❗ 미납액 <em class="r3-kpi__hint">납입일 경과</em></span><strong class="r3-kpi__val" style="color:${overdueColor};">${fmtWon(mOverdue)}${mTarget > 0 ? ` (${overduePct}%)` : ""}</strong><div class="r3-kpi__bar"><i style="width:${overduePct}%;"></i></div>${priorHtml}</div>`;

    renderTab();
  }

  function switchTab(tabName) {
    if (tabName !== "messages") { S.msgRoom = ""; }
    if (tabName === "tax" && S.currentTab !== "tax") { S.taxView = "hub"; S.taxMonth = 0; }
    S.currentTab = tabName;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === tabName));
    document.querySelectorAll("#lessorBottomNav [data-ltab]").forEach((b) => b.classList.toggle("is-active", b.dataset.ltab === tabName));
    renderTab();
  }

  function renderTab() {
    if (!S.dash) return;
    const MAIL_ON = S.MAIL_ON;
    if (S.currentTab !== "ledger" && !TAB_TITLES[S.currentTab]) S.currentTab = "ledger";
    if (S.currentTab === "mail" && !MAIL_ON) S.currentTab = "ledger";
    if (VIA_PIN && !PIN_TABS.includes(S.currentTab)) S.currentTab = "ledger";
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === S.currentTab));
    document.getElementById("backBar").hidden = S.currentTab === "ledger";
    document.getElementById("backTitle").textContent = TAB_TITLES[S.currentTab] || "";
    if (S.currentTab === "ledger") L.renderLedgerTab();
    else if (S.currentTab === "broker") L.renderBrokerTab();
    else if (S.currentTab === "tax") L.renderTaxTab();
    else if (S.currentTab === "ops") L.renderOpsTab();
    else if (S.currentTab === "expiry") L.renderExpiryTab();
    else if (S.currentTab === "notice") L.renderNoticeTab();
    else if (S.currentTab === "messages") L.renderMessagesTab();
    else if (S.currentTab === "bldgtax") L.renderBldgTaxTab();
    else if (S.currentTab === "mail") L.renderMailTab();
    else if (S.currentTab === "rooms") L.renderRoomsTab();
    else if (S.currentTab === "registry") {
      S.regOpen = true;
      L.renderRoomsTab();
      const rc = document.getElementById("regCard");
      if (rc) rc.scrollIntoView({ block: "start" });
    }
    pinLimit(elTabContent);
  }

  function bindDrawerActions() {
    const bind = (id, fn) => {
      const el = document.getElementById(id);
      if (!el || el.dataset.bound === "1") return;
      el.dataset.bound = "1";
      el.addEventListener("click", () => { closeDrawer(); fn(); });
    };
    bind("btnSupportChat", () => R3Shell.openSupportChat());
    bind("btnSupportSetting", () => R3Shell.openSupportSettings());
    bind("btnShortcut", () => R3Shell.openShortcutSettings());
    bind("btnFullLogin", () => fullLogin());
    bind("btnEditBuilding", () => L.showBuildingModal());
    bind("btnLedgerCsv", () => L.showLedgerCsvModal());
    bind("btnTaxInvoice", () => L.goTaxView("invoice"));
    bind("btnMgmtFee", () => L.showMgmtFeeModal().catch(Bass.showError));
    bind("btnFeeEvidence", () => L.showFeeEvidenceModal().catch(Bass.showError));
    bind("btnBizRegModal", () => L.showBizRegModal().catch(Bass.showError));
    bind("btnConnectMsg", () => {
      L.showInviteSender().catch((err) => {
        const body = openModal("📨 기존 임차인 앱 연결 안내", `<div style="font-size:14.5px;line-height:1.7;">창을 여는 중 문제가 생겼습니다.<br><strong>${Bass.escapeHtml((err && err.message) || String(err))}</strong><br><span class="bass-muted">${Bass.escapeHtml((err && err.hint) || "화면을 새로고침(PC: Ctrl+Shift+R)한 뒤 다시 눌러 주세요.")}</span></div>
          <button id="ivRetry" class="bass-btn bass-btn--primary" type="button" style="min-height:48px;margin-top:10px;">다시 시도</button>`);
        body.querySelector("#ivRetry").addEventListener("click", () => {
          const b = document.getElementById("btnConnectMsg");
          if (b) b.click();
        });
      });
    });
  }

  const bNav = document.getElementById("lessorBottomNav");
  if (bNav) {
    bNav.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-ltab]");
      if (btn) switchTab(btn.dataset.ltab);
    });
  }
  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (btn) switchTab(btn.dataset.tab);
  });
  document.getElementById("btnBackLedger").addEventListener("click", () => switchTab("ledger"));

  Object.assign(L, { refresh, switchTab, renderTab });
  bindDrawerActions();
  // 🔔 알림 센터: 채팅·정산·A/S·연결 요청 등 내가 처리할 일을 한곳에 모아 건수로 알린다
  if (window.R3Notify && !VIA_PIN) {
    R3Notify.start({
      url: `/api/rent3_lessor/notifications?branch=${encodeURIComponent(S.currentBranch)}`,
      seenUrl: "/api/rent3_lessor/notifications/seen",
      seenBody: { branch: S.currentBranch },
      openSupport: () => R3Shell.openSupportChat(),
    });
  }
  refresh().catch(Bass.showError);
  L.watchConnect();
})();
