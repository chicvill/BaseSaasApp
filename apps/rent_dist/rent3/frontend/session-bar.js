/* apps/rent3/frontend/session-bar.js — 분리 앱 공통 OpenRent 홈 복귀, 본인인증 세션 배지 & 공동계약 호출 수신 알림 */
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

  async function refreshIncomingCallBadge(container, user) {
    if (!user || !user.name) return;
    if (window.location.pathname.startsWith("/rent3_contract")) return;
    const isLessor = user.role === "lessor" || user.role === "admin";
    const params = new URLSearchParams();
    params.set("role", isLessor ? "lessor" : "tenant");
    if (isLessor && user.branch && user.branch !== "all") params.set("branch", user.branch);
    if (!isLessor) {
      if (user.tenant_id || user.id) params.set("tenant_id", user.tenant_id || user.id);
      if (user.phone) params.set("phone", user.phone);
      if (user.name) params.set("name", user.name);
    }
    try {
      const resp = await window.fetch(`/api/rent3_contract/incoming-calls?${params.toString()}`);
      if (!resp.ok) return;
      const data = await resp.json();
      let callLink = container.querySelector("[data-incoming-cosign]");
      if (data.calls && data.calls.length > 0) {
        const topCall = data.calls[0];
        if (!callLink) {
          callLink = document.createElement("a");
          callLink.className = "bass-btn bass-btn--sm";
          callLink.dataset.incomingCosign = "1";
          callLink.style.background = "#f59e0b";
          callLink.style.color = "#fff";
          callLink.style.borderColor = "#d97706";
          callLink.style.fontWeight = "800";
          container.prepend(callLink);
        }
        const targetRole = isLessor ? "lessor" : "tenant";
        callLink.href = `/rent3_contract/?role=${targetRole}&session=${encodeURIComponent(topCall.id)}`;
        callLink.textContent = `🔔 공동계약 호출 (${topCall.building_name} ${topCall.room_no}호 응답하기 →)`;
      } else if (callLink) {
        callLink.remove();
      }
    } catch (_) {
      // background poll ignore
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
    spacer.style.flexWrap = "wrap";

    const user = readSession();
    if (user && user.name) {
      const badge = document.createElement("span");
      badge.className = "bass-badge bass-badge--info";
      badge.textContent = `🛡️ ${user.name} · ${user.role_label || user.role} (본인인증됨)`;

      const logoutBtn = document.createElement("a");
      logoutBtn.className = "bass-btn bass-btn--sm";
      logoutBtn.href = "/rent3/?logout=1";
      logoutBtn.textContent = "로그아웃";

      spacer.append(badge, logoutBtn);
      refreshIncomingCallBadge(spacer, user);
      window.setInterval(() => refreshIncomingCallBadge(spacer, user), 5000);
    } else {
      const loginBtn = document.createElement("a");
      loginBtn.className = "bass-btn bass-btn--sm";
      loginBtn.href = "/rent3/?login=1";
      loginBtn.textContent = "🔐 본인인증 로그인";
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
