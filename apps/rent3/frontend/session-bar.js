/* apps/rent3/frontend/session-bar.js — 분리 앱 공통 OpenRent 홈 복귀 및 세션 상태 배지 */
(function () {
  "use strict";

  const SESSION_KEY = "rent3_session";

  function readSession() {
    try {
      const raw = window.localStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function mountBar() {
    const pills = document.querySelector(".nav-pills");
    if (!pills) return;

    pills.style.alignItems = "center";

    // 맨 앞에 OpenRent 직거래 포털 홈 버튼 추가 (중복 방지)
    if (!pills.querySelector("[data-or-home]")) {
      const homeLink = document.createElement("a");
      homeLink.className = "bass-btn bass-btn--sm bass-btn--primary";
      homeLink.href = "/rent3/";
      homeLink.dataset.orHome = "1";
      homeLink.textContent = "🏡 직거래 포털 홈";
      pills.prepend(homeLink);
    }

    const spacer = document.createElement("span");
    spacer.style.marginLeft = "auto";
    spacer.style.display = "inline-flex";
    spacer.style.alignItems = "center";
    spacer.style.gap = "6px";

    const user = readSession();
    if (user && user.name) {
      const badge = document.createElement("span");
      badge.className = "bass-badge bass-badge--info";
      badge.textContent = `👤 ${user.name} · ${user.role_label || user.role}`;

      const logoutBtn = document.createElement("a");
      logoutBtn.className = "bass-btn bass-btn--sm";
      logoutBtn.href = "/rent3/?logout=1";
      logoutBtn.textContent = "로그아웃";

      spacer.append(badge, logoutBtn);
    } else {
      const loginBtn = document.createElement("a");
      loginBtn.className = "bass-btn bass-btn--sm";
      loginBtn.href = "/rent3/?login=1";
      loginBtn.textContent = "🔐 역할별 로그인";
      spacer.append(loginBtn);
    }

    pills.append(spacer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountBar);
  } else {
    mountBar();
  }
})();

