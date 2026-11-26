/* apps/rent3_tenant/frontend/app.js — 임차인 앱 화면 전환·메뉴 연결·알림 초기화 */
(function () {
  "use strict";
  const T = window.R3T;
  if (!T) return;
  const { params, ME_ADMIN, SVC_TITLES, MENU_VIEWS, elMyhome, elListings, closeDrawer } = T;
  const S = T.state;

  // 마이홈 안의 화면 전환: 해당 서비스 구역만 보이고 위에 '← 내 집' 줄이 나온다
  function applyView() {
    elMyhome.querySelectorAll("section.svc").forEach((sec) => { sec.style.display = sec.dataset.svc === S.svcView ? "" : "none"; });
    const keep = elMyhome.querySelector(".svc-home-only");
    if (keep) keep.style.display = S.svcView === "home" ? "" : "none";
    const back = document.getElementById("svcBack");
    if (back) {
      back.style.display = S.svcView === "home" ? "none" : "flex";
      document.getElementById("svcTitle").textContent = SVC_TITLES[S.svcView] || "";
    }
    clearInterval(S.tnMsgTimer);
    if (S.svcView === "messages") T.loadTenantMessages();
    const tCur = S.mode === "listings" ? "listings" : S.svcView;
    document.querySelectorAll("#tenantBottomNav [data-tview]").forEach((b) => b.classList.toggle("is-active", b.dataset.tview === tCur));
  }

  function setMode(m) {
    S.mode = m;
    document.getElementById("hdrTitle").textContent = m === "listings" ? "🔍 빈 집 찾기" : "🔑 내 집 · 월세 납부 확인";
    elMyhome.style.display = m === "myhome" ? "block" : "none";
    elListings.style.display = m === "listings" ? "block" : "none";
    if (m === "myhome") T.loadMyhome().catch(Bass.showError);
    else T.loadListings().catch(Bass.showError);
  }

  function go(view) {
    S.svcView = SVC_TITLES[view] !== undefined ? view : "home";
    if (S.mode !== "myhome") setMode("myhome");
    else applyView();
    window.scrollTo(0, 0);
  }

  Object.assign(T, { applyView, setMode, go });

  const tnBNav = document.getElementById("tenantBottomNav");
  if (tnBNav) {
    tnBNav.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-tview]");
      if (!btn) return;
      const v = btn.dataset.tview;
      if (v === "listings") {
        setMode("listings");
        document.querySelectorAll("#tenantBottomNav [data-tview]").forEach((b) => b.classList.toggle("is-active", b.dataset.tview === "listings"));
      } else {
        go(v);
      }
    });
  }

  Object.entries(MENU_VIEWS).forEach(([id, view]) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", () => { closeDrawer(); go(view); });
  });

  const bindMenu = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  };
  bindMenu("btnMenuListings", () => { closeDrawer(); setMode("listings"); });
  bindMenu("btnSupportChat", () => { closeDrawer(); R3Shell.openSupportChat(); });
  bindMenu("btnMyAccount", () => { closeDrawer(); R3Shell.openAccountSettings(); });
  bindMenu("btnShortcut", () => { closeDrawer(); R3Shell.openShortcutSettings(); });
  bindMenu("btnLessorInvite", () => T.openLessorInviteModal());
  bindMenu("btnBizInfo", () => T.openTenantBizInfoModal().catch(Bass.showError));
  bindMenu("btnSupportSetting", () => { closeDrawer(); R3Shell.openSupportSettings(); });

  R3Shell.watchSupport();
  // 🔔 알림 센터: 정산서·만기 회신·채팅·미납 등 내가 확인할 일을 한곳에 모아 건수로 알린다
  if (window.R3Notify && S.selectedTenantId && !ME_ADMIN) {
    R3Notify.start({
      url: `/api/rent3_tenant/myhome/${encodeURIComponent(S.selectedTenantId)}/notifications`,
      seenUrl: `/api/rent3_tenant/myhome/${encodeURIComponent(S.selectedTenantId)}/notifications/seen`,
      openSupport: () => R3Shell.openSupportChat(),
    });
  }
  if (params.get("welcome")) {    // 입주 안내 링크로 가입을 마친 직후: 홈 화면 추가 방법을 한 번 보여 주고 주소에서 welcome 을 뺀다
    try { const u = new URL(window.location.href); u.searchParams.delete("welcome"); window.history.replaceState(null, "", u.toString()); } catch (_) { /* 무시 */ }
    setTimeout(() => R3Shell.installHelp(), 600);
  }

  setMode(S.mode);
})();
