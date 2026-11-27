/* apps/rent3/frontend/state.js — Rent3 포털 공통 상태·세션·유입 표식 모듈 (window.R3P) */
(function () {
  "use strict";

  const APP_ID = "rent3";
  const SESSION_KEY = "rent3_session";
  const REFRESH_KEY = "rent3_refresh_token";

  const state = {
    listings: [],
    branches: [],
    openSessions: [],
    demoAccounts: [],
    quickFilter: "all",
    budgetActive: false,
    maxMonthlyWon: 0,
    maxDepositWon: 0,
  };

  const $ = (id) => document.getElementById(id);
  const esc = Bass.escapeHtml;

  function fmtMan(won) {
    const n = Number(won || 0);
    if (n === 0) return "0원";
    if (n % 10000 === 0) return `${(n / 10000).toLocaleString("ko-KR")}만`;
    return `${n.toLocaleString("ko-KR")}원`;
  }

  function getSession() {
    try {
      const raw = window.localStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function getRefreshToken() {
    try {
      return window.localStorage.getItem(REFRESH_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function saveSession(user, tok, refreshTok) {
    if (tok !== undefined) Bass.setToken(tok || "");
    if (refreshTok !== undefined) {
      try {
        if (refreshTok) window.localStorage.setItem(REFRESH_KEY, refreshTok);
        else window.localStorage.removeItem(REFRESH_KEY);
      } catch (_) {}
    }
    if (user) {
      window.localStorage.setItem(SESSION_KEY, JSON.stringify(user));
    } else {
      window.localStorage.removeItem(SESSION_KEY);
      try { window.localStorage.removeItem(REFRESH_KEY); } catch (_) {}
    }
  }

  function clearSession() {
    const tok = Bass.token();
    const rt = getRefreshToken();
    try {
      if (tok || rt) {
        const headers = { "Content-Type": "application/json" };
        if (tok) headers.Authorization = "Bearer " + tok;
        fetch("/api/rent3/pool/auth/logout", {
          method: "POST",
          headers,
          body: JSON.stringify({ refresh_token: rt }),
          keepalive: true,
        });
      }
    } catch (_) {}
    Bass.setToken("");
    window.localStorage.removeItem(SESSION_KEY);
    try { window.localStorage.removeItem(REFRESH_KEY); } catch (_) {}
  }

  async function tryRefreshSession() {
    const rt = getRefreshToken();
    if (!rt) return null;
    try {
      const res = await fetch("/api/rent3/pool/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          refresh_token: rt,
          device_id: Bass.deviceId ? Bass.deviceId() : "",
        }),
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data && data.token && data.user) {
        saveSession(data.user, data.token, data.refresh_token || "");
        return data;
      }
      if (res.status === 401 || res.status === 403) {
        Bass.setToken("");
        window.localStorage.removeItem(SESSION_KEY);
        try { window.localStorage.removeItem(REFRESH_KEY); } catch (_) {}
      }
    } catch (_) {
      // 일시적인 오프라인/네트워크 오류에서는 저장된 세션을 지우지 않는다
    }
    return null;
  }

  function roleIcon(role) {
    if (role === "admin") return "👑";
    if (role === "lessor") return "🏢";
    if (role === "tenant" || role === "prospect") return "🔑";
    if (role === "cosign") return "🤝";
    return "👤";
  }

  function buildContractUrlFromSession(user, callBranch, callRoom) {
    if (!user) return "/rent3_contract/";
    const isLessor = user.role === "lessor" || user.role === "admin";
    const params = new URLSearchParams();
    params.set("role", isLessor ? "lessor" : "tenant");
    if (isLessor && user.branch && user.branch !== "all") {
      params.set("branch", user.branch);
    }
    if (!isLessor && (user.tenant_id || user.id)) {
      params.set("tenant", user.tenant_id || user.id);
    }
    if (callBranch && callRoom) {
      params.set("call_branch", callBranch);
      params.set("call_room", callRoom);
    }
    return `/rent3_contract/?${params.toString()}`;
  }

  function navigateToCoSignOrLogin(callBranch, callRoom) {
    const user = getSession();
    if (user && user.name) {
      window.location.href = buildContractUrlFromSession(user, callBranch, callRoom);
      return;
    }
    const hint = callBranch && callRoom ? `contract:${callBranch}:${callRoom}` : "contract";
    window.R3P.openAuthModal({ tab: "login", preset: "cosign", targetHint: hint });
  }

  let portalNotifyStarted = false;
  function renderSessionUI() {
    const user = getSession();
    const btnSignIn = $("btnNavSignIn");
    const loggedBox = $("navLoggedBox");
    const strip = $("activeSessionStrip");
    const quickAuth = $("quickCosignAuthStatus");

    if (user && user.redirect_url) {
      btnSignIn.style.display = "none";
      loggedBox.style.display = "inline-flex";
      $("navUserBadge").textContent = `${roleIcon(user.role)} ${user.name} · 본인인증됨`;
      $("navGoWorkspace").href = user.redirect_url;
      const bnavAcc = $("bnavAccountLabel");
      if (bnavAcc) bnavAcc.textContent = "내 페이지";
      if (!portalNotifyStarted && window.R3Notify && Bass.token()) {
        portalNotifyStarted = true;
        R3Notify.start({ url: "/api/rent3_contract/notifications", seenUrl: "/api/rent3_contract/notifications/seen", bellHost: "#portalBellSlot" });
      }

      strip.style.display = "none";    // '다른 계정으로 전환' 안내는 두지 않는다 — 로그아웃 후 다른 아이디로 로그인
      $("activeSessionText").innerHTML =
        `🛡️ <strong>${esc(user.name)}</strong>님(<code>${esc(user.role_label || user.role)}</code> · 본인인증 완료)으로 로그인되어 있습니다. 역할 전용 워크스페이스 또는 공동계약 호출 화면으로 바로 이동할 수 있습니다.`;
      $("stripGoBtn").href = user.redirect_url;

      if (quickAuth) {
        quickAuth.style.background = "#f0fdf4";
        quickAuth.style.borderColor = "#86efac";
        quickAuth.style.color = "#166534";
        quickAuth.innerHTML =
          `✅ <strong>${esc(user.name)}</strong>님 (${esc(user.role_label || user.role)}) 본인인증 완료 — 아래 버튼으로 입장해 매물 선택 후 상대방을 호출하거나 도착한 호출에 응답하세요.`;
      }
    } else {
      btnSignIn.style.display = "inline-flex";
      loggedBox.style.display = "none";
      strip.style.display = "none";
      const bnavAcc = $("bnavAccountLabel");
      if (bnavAcc) bnavAcc.textContent = "로그인";
      if (quickAuth) {
        quickAuth.style.background = "#f8fafc";
        quickAuth.style.borderColor = "var(--or-border)";
        quickAuth.style.color = "var(--or-text)";
        quickAuth.innerHTML =
          `🔒 임대인 또는 임차인으로 <strong>본인인증 로그인</strong>하면 매물을 선택해 상대를 호출하거나 도착한 호출에 즉시 응답할 수 있습니다.`;
      }
    }
  }

  // 유입 표식(?ref=채널&campaign=캠페인&from=소개한 임차인 코드): 처음 닿은 값을 30일간 브라우저에 보관했다가 임대인 가입 때 보낸다.
  // 짧은 영숫자만 허용하고, 방문 기록은 서버로 보내지 않는다.
  const ACQ_KEY = "r3_acq", ACQ_DAYS = 30, ACQ_OK = /^[A-Za-z0-9_-]{1,32}$/;
  function readAcq() {
    try {
      const v = JSON.parse(localStorage.getItem(ACQ_KEY) || "null");
      if (v && v.first_seen && (Date.now() - Date.parse(v.first_seen)) / 864e5 <= ACQ_DAYS) return v;
    } catch (_) { /* 저장소를 못 쓰는 환경 */ }
    return null;
  }
  function captureAcq(urlParams) {
    try {
      if (readAcq()) return;                                                     // 처음 닿은 값을 유지(덮어쓰지 않음)
      const pick = (v) => (ACQ_OK.test(v || "") ? v : "");
      const a = { ref: pick(urlParams.get("ref")), campaign: pick(urlParams.get("campaign") || urlParams.get("utm_campaign")), from_tenant: pick(urlParams.get("from")) };
      if (a.ref || a.campaign || a.from_tenant) localStorage.setItem(ACQ_KEY, JSON.stringify({ ...a, first_seen: new Date().toISOString().slice(0, 10) }));
    } catch (_) { /* 무시: 표식이 없어도 가입은 된다 */ }
  }

  window.R3P = Object.assign(window.R3P || {}, {
    APP_ID,
    SESSION_KEY,
    state,
    $,
    esc,
    fmtMan,
    getSession,
    getRefreshToken,
    saveSession,
    clearSession,
    tryRefreshSession,
    roleIcon,
    buildContractUrlFromSession,
    navigateToCoSignOrLogin,
    renderSessionUI,
    readAcq,
    captureAcq,
  });
})();
