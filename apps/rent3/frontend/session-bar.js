/* apps/rent3/frontend/session-bar.js — 스마트폰 전용 앱 레이아웃(PC 확대 지원) & 통합 햄버거 메뉴(☰) & 역할별 워크스테이션 권한(RBAC) 가드 */
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

  function ensureZoomViewport() {
    let vp = document.querySelector('meta[name="viewport"]');
    if (!vp) {
      vp = document.createElement("meta");
      vp.name = "viewport";
      document.head.prepend(vp);
    }
    vp.content = "width=device-width, initial-scale=1.0, minimum-scale=0.5, maximum-scale=3.0, user-scalable=yes";
  }

  function detectCurrentAppLabel() {
    const p = window.location.pathname;
    if (p.startsWith("/rent3_admin")) return { icon: "👑", name: "총괄 관리인 앱", id: "rent3_admin", reqRoles: ["admin"] };
    if (p.startsWith("/rent3_lessor")) return { icon: "🏢", name: "임대인 자산·수납 앱", id: "rent3_lessor", reqRoles: ["lessor", "admin"] };
    if (p.startsWith("/rent3_tenant")) return { icon: "🔑", name: "임차인 마이홈·매물 앱", id: "rent3_tenant", reqRoles: ["tenant", "prospect", "admin"] };
    if (p.startsWith("/rent3_contract")) return { icon: "🤝", name: "공동 임대계약 앱", id: "rent3_contract", reqRoles: ["tenant", "lessor", "prospect", "cosign", "admin"] };
    if (p.includes("pool.html")) return { icon: "🌐", name: "통합 Data Pool 허브", id: "pool", reqRoles: ["admin", "lessor", "tenant", "prospect"] };
    return { icon: "🏡", name: "OpenRent 포털", id: "rent3", reqRoles: [] };
  }

  function checkAppAccess(appId, user) {
    if (!user) return false;
    if (user.role === "admin") return true; // 총괄 관리인은 모든 페이지 접근 가능!
    if (appId === "rent3") return true;
    if (appId === "rent3_admin") return user.role === "admin";
    if (appId === "rent3_lessor") return user.role === "lessor";
    if (appId === "rent3_tenant") return user.role === "tenant" || user.role === "prospect";
    if (appId === "rent3_contract") return ["tenant", "lessor", "prospect", "cosign"].includes(user.role);
    if (appId === "pool") return true;
    return true;
  }

  /* ── 워크스테이션 진입 권한 검사 (비인가 접속 시 포털 로그인으로 안전 리다이렉트) ── */
  function enforcePageAccess(appInfo, user) {
    if (!appInfo.reqRoles || appInfo.reqRoles.length === 0) return true; // 공개 포털

    // admin은 모든 워크스페이스 무조건 통과!
    if (user && user.role === "admin") return true;

    // 1. 비로그인 상태인 경우 -> 로그인 창으로 안전 리다이렉트
    if (!user || !user.name) {
      window.__rent3_blocked = true;
      const targetHint = appInfo.id === "rent3_admin" ? "admin" : (appInfo.id === "rent3_lessor" ? "lessor" : (appInfo.id === "rent3_tenant" ? "tenant" : "cosign"));
      window.location.replace(`/rent3/?login=1&hint=${encodeURIComponent(targetHint)}`);
      return false;
    }

    // 2. 로그인되어 있으나 권한이 없는 경우 -> 안내 후 로그인으로 이동
    if (!appInfo.reqRoles.includes(user.role)) {
      window.__rent3_blocked = true;
      alert(`접근 권한이 없습니다.\n현재 계정: ${user.name} (${user.role_label || user.role})\n이 페이지는 관리인(admin) 또는 해당 권한 계정으로 로그인해야 합니다.`);
      window.location.replace(`/rent3/?login=1`);
      return false;
    }

    return true;
  }

  function injectSmartphoneFirstStyles() {
    if (document.getElementById("rent3-smartphone-first-style")) return;
    const st = document.createElement("style");
    st.id = "rent3-smartphone-first-style";
    st.textContent = `
      /* ── 스마트폰 전용 고밀도 앱 기본 레이아웃 ── */
      html {
        -webkit-text-size-adjust: 100%;
        overflow-x: auto;
      }
      body {
        overflow-x: auto;
      }
      .app-shell, .hub-shell {
        max-width: 100% !important;
        margin: 0 auto !important;
        padding: 8px 10px 48px !important;
      }

      /* 상단 통합 앱바 + 햄버거 드로어 */
      .r3-appbar {
        position: sticky;
        top: 0;
        z-index: 200;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        background: hsl(230 35% 11% / 0.96);
        backdrop-filter: blur(10px);
        border: 1px solid var(--border-strong, rgba(255,255,255,0.14));
        border-radius: 12px;
        padding: 6px 10px;
        margin-bottom: 8px;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
      }
      .r3-appbar__brand {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        text-decoration: none;
        color: #fff;
        font-weight: 800;
        font-size: 13.5px;
        white-space: nowrap;
      }
      .r3-appbar__chip {
        font-size: 11px;
        font-weight: 700;
        padding: 2px 7px;
        border-radius: 999px;
        background: hsl(255 85% 60% / 0.22);
        border: 1px solid hsl(255 85% 65% / 0.45);
        color: #e0e7ff;
        white-space: nowrap;
      }
      .r3-appbar__right {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        flex-shrink: 0;
      }
      .r3-appbar__btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        padding: 5px 8px;
        border-radius: 8px;
        font-size: 11.5px;
        font-weight: 700;
        color: #f8fafc;
        background: hsl(230 28% 18%);
        border: 1px solid rgba(255, 255, 255, 0.16);
        text-decoration: none;
        cursor: pointer;
        white-space: nowrap;
      }
      .r3-appbar__btn:hover {
        border-color: hsl(200 95% 60%);
        color: #fff;
      }
      .r3-appbar__hamburger {
        width: 32px;
        height: 30px;
        padding: 0;
        font-size: 16px;
        background: linear-gradient(135deg, hsl(258 85% 60%), hsl(198 90% 52%));
        border: none;
        color: #fff;
        border-radius: 8px;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
      }
      .r3-drawer {
        display: none;
        position: sticky;
        top: 48px;
        z-index: 199;
        background: hsl(230 35% 10% / 0.98);
        border: 1px solid rgba(255, 255, 255, 0.18);
        border-radius: 12px;
        padding: 10px;
        margin-bottom: 10px;
        box-shadow: 0 14px 32px rgba(0, 0, 0, 0.55);
      }
      .r3-drawer.is-open {
        display: block;
      }
      .r3-drawer__grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 6px;
      }
      .r3-drawer__link {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
        padding: 8px 10px;
        border-radius: 9px;
        background: hsl(230 28% 15%);
        border: 1px solid rgba(255, 255, 255, 0.1);
        color: #f1f5f9;
        text-decoration: none;
        font-size: 12px;
        font-weight: 700;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .r3-drawer__link.is-current {
        border-color: hsl(196 95% 58%);
        background: hsl(210 60% 20%);
        color: #fff;
      }
      .r3-drawer__link.is-locked {
        opacity: 0.55;
        background: hsl(230 25% 12%);
        cursor: not-allowed;
      }
      .r3-role-badge {
        font-size: 9.5px;
        padding: 1px 5px;
        border-radius: 4px;
        background: rgba(255,255,255,0.1);
      }

      /* 기존 중복 상단 알약바 숨김 */
      .nav-pills {
        display: none !important;
      }

      /* 스마트폰 고밀도 카드·KPI·입력폼 */
      .bass-card {
        padding: 10px 12px !important;
        border-radius: 12px !important;
      }
      header.bass-card h1 {
        font-size: 15.5px !important;
        line-height: 1.3 !important;
        margin-bottom: 2px !important;
      }
      header.bass-card .bass-muted {
        font-size: 11px !important;
        line-height: 1.35 !important;
      }
      .bass-card h3 {
        font-size: 13.5px !important;
        margin: 0 0 6px 0 !important;
        line-height: 1.35 !important;
      }
      .kpi-grid {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 6px !important;
        margin: 8px 0 2px !important;
      }
      .kpi-box {
        padding: 8px 10px !important;
        border-radius: 10px !important;
      }
      .kpi-box .bass-muted {
        font-size: 10.5px !important;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .kpi-val {
        font-size: 15px !important;
        margin-top: 2px !important;
        white-space: nowrap;
      }
      .tabs {
        display: flex !important;
        flex-wrap: nowrap !important;
        overflow-x: auto !important;
        gap: 5px !important;
        margin: 8px 0 !important;
        padding-bottom: 2px !important;
        scrollbar-width: none;
      }
      .tabs::-webkit-scrollbar { display: none; }
      .tabs .tab-btn {
        flex-shrink: 0 !important;
        font-size: 11.5px !important;
        padding: 6px 10px !important;
        border-radius: 8px !important;
        white-space: nowrap !important;
      }
      .field-grid {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 6px 8px !important;
      }
      .bass-label {
        font-size: 11px !important;
        margin-bottom: 2px !important;
      }
      .bass-input, .bass-select {
        padding: 6px 8px !important;
        font-size: 12px !important;
        border-radius: 8px !important;
        min-height: 32px !important;
      }
      .item-card, .room-card, .listing-card {
        padding: 9px 11px !important;
        border-radius: 10px !important;
        margin-bottom: 7px !important;
      }
      .bass-btn {
        font-size: 12px !important;
        padding: 6px 11px !important;
        border-radius: 8px !important;
      }
      .bass-btn--sm {
        font-size: 11px !important;
        padding: 4px 8px !important;
      }
      .bass-badge {
        font-size: 10.5px !important;
        padding: 2px 7px !important;
      }
      .stepper {
        gap: 4px !important;
        margin: 8px 0 !important;
      }
      .step-pill {
        padding: 6px 2px !important;
        font-size: 10.5px !important;
      }
      .app-grid, .file-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 6px !important;
        margin: 8px 0 !important;
      }

      /* ── PC 화면에서는 스마트폰 전용 앱 레이아웃을 확대(Scale-Up) 및 다열 확장하여 표시 ── */
      @media (min-width: 768px) {
        .app-shell, .hub-shell {
          max-width: 1040px !important;
          padding: 14px 18px 60px !important;
          zoom: 1.08;
        }
        .kpi-grid {
          grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
          gap: 10px !important;
        }
        .field-grid {
          grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
          gap: 8px 10px !important;
        }
        .r3-drawer__grid {
          grid-template-columns: repeat(4, minmax(0, 1fr));
        }
        .app-grid, .file-grid {
          grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
        }
      }
    `;
    document.head.appendChild(st);
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
          callLink.className = "r3-appbar__btn";
          callLink.dataset.incomingCosign = "1";
          callLink.style.background = "#f59e0b";
          callLink.style.color = "#fff";
          callLink.style.borderColor = "#d97706";
          container.prepend(callLink);
        }
        const targetRole = isLessor ? "lessor" : "tenant";
        callLink.href = `/rent3_contract/?role=${targetRole}&session=${encodeURIComponent(topCall.id)}`;
        callLink.textContent = `🔔 ${topCall.room_no}호 계약호출 응답`;
      } else if (callLink) {
        callLink.remove();
      }
    } catch (_) {
      // background poll ignore
    }
  }

  function mountBar() {
    ensureZoomViewport();
    injectSmartphoneFirstStyles();

    const appInfo = detectCurrentAppLabel();
    const user = readSession();

    // 1. 현재 페이지 권한 검사 (권한 없으면 포털 로그인으로 안전 리다이렉트)
    if (!enforcePageAccess(appInfo, user)) {
      return;
    }

    const pills = document.querySelector(".nav-pills");
    const shell = document.querySelector(".app-shell, .hub-shell") || document.body;
    if (!shell || document.getElementById("r3UnifiedAppbar")) return;

    const appbar = document.createElement("nav");
    appbar.id = "r3UnifiedAppbar";
    appbar.className = "r3-appbar no-print";

    const brandWrap = document.createElement("div");
    brandWrap.style.display = "inline-flex";
    brandWrap.style.alignItems = "center";
    brandWrap.style.gap = "6px";
    brandWrap.style.minWidth = "0";

    const brandLink = document.createElement("a");
    brandLink.className = "r3-appbar__brand";
    brandLink.href = "/rent3/";
    brandLink.textContent = "🏡 Rent3";

    const appChip = document.createElement("span");
    appChip.className = "r3-appbar__chip";
    appChip.textContent = `${appInfo.icon} ${appInfo.name}`;

    brandWrap.append(brandLink, appChip);

    const rightBox = document.createElement("div");
    rightBox.className = "r3-appbar__right";

    if (user && user.name) {
      const userTag = document.createElement("a");
      userTag.className = "r3-appbar__btn";
      userTag.href = "/rent3/?login=1";
      userTag.title = "클릭하여 계정 전환";
      const isAdmin = user.role === "admin";
      userTag.textContent = isAdmin ? `👑 관리자 ${user.name}` : `🛡️ ${user.name}`;
      rightBox.append(userTag);
      refreshIncomingCallBadge(rightBox, user);
      window.setInterval(() => refreshIncomingCallBadge(rightBox, user), 5000);
    } else {
      const loginBtn = document.createElement("a");
      loginBtn.className = "r3-appbar__btn";
      loginBtn.href = "/rent3/?login=1";
      loginBtn.textContent = "🔐 로그인";
      rightBox.append(loginBtn);
    }

    const menuBtn = document.createElement("button");
    menuBtn.type = "button";
    menuBtn.className = "r3-appbar__hamburger";
    menuBtn.setAttribute("aria-label", "전체 앱 메뉴 열기");
    menuBtn.setAttribute("aria-expanded", "false");
    menuBtn.textContent = "☰";
    rightBox.append(menuBtn);

    appbar.append(brandWrap, rightBox);

    const drawer = document.createElement("div");
    drawer.id = "r3UnifiedDrawer";
    drawer.className = "r3-drawer no-print";

    const menuItems = [
      { href: "/rent3/", label: "🏡 직거래 포털 홈", id: "rent3", roleReq: "" },
      { href: "/rent3_admin/", label: "👑 총괄 관리인 앱", id: "rent3_admin", roleReq: "admin" },
      { href: "/rent3_lessor/", label: "🏢 임대인 자산·수납 앱", id: "rent3_lessor", roleReq: "lessor" },
      { href: "/rent3_tenant/", label: "🔑 임차인 마이홈·매물 앱", id: "rent3_tenant", roleReq: "tenant" },
      { href: "/rent3_contract/", label: "🤝 공동 임대계약 앱", id: "rent3_contract", roleReq: "cosign" },
      { href: "/rent3/pool.html", label: "🌐 통합 Data Pool 허브", id: "pool", roleReq: "admin" },
      { href: "manual.html", label: "📘 현재 화면 매뉴얼", id: "manual", roleReq: "" },
      user && user.name
        ? { href: "/rent3/?logout=1", label: `🔓 로그아웃 (${user.name})`, id: "logout", roleReq: "" }
        : { href: "/rent3/?login=1", label: "🔐 본인인증 로그인/가입", id: "login", roleReq: "" },
    ];

    const isAdmin = Boolean(user && user.role === "admin");

    drawer.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;font-size:11.5px;color:#94a3b8;">
        <span>📱 <strong>Rent3 권한별 워크스페이스 메뉴</strong></span>
        <span>${isAdmin ? "👑 <strong>총괄 관리자: 모든 페이지 확인 가능</strong>" : (user && user.name ? `🛡️ ${user.name} (${user.role_label || user.role})` : "🔒 로그인 필요")}</span>
      </div>
      <div class="r3-drawer__grid">
        ${menuItems.map((m) => {
          const hasAccess = !m.roleReq || checkAppAccess(m.id, user);
          const lockBadge = isAdmin && m.roleReq ? `<span class="r3-role-badge" style="color:#a7f3d0;">열람가능</span>` : (!hasAccess ? `<span class="r3-role-badge" style="color:#f87171;">🔒 권한필요</span>` : "");
          return `
            <a href="${m.href}" class="r3-drawer__link ${m.id === appInfo.id ? "is-current" : ""} ${!hasAccess ? "is-locked" : ""}" data-menu-id="${m.id}" data-has-access="${hasAccess ? '1' : '0'}">
              <span>${m.label}</span>
              ${lockBadge}
            </a>
          `;
        }).join("")}
      </div>
    `;

    drawer.addEventListener("click", (e) => {
      const link = e.target.closest(".r3-drawer__link");
      if (!link) return;
      const canAccess = link.dataset.hasAccess === "1";
      if (!canAccess) {
        e.preventDefault();
        alert("이 워크스페이스는 해당 권한 계정 또는 총괄 관리인(admin)으로 로그인해야 입장할 수 있습니다.");
        window.location.href = `/rent3/?login=1`;
      }
    });

    function closeDrawer() {
      drawer.classList.remove("is-open");
      menuBtn.setAttribute("aria-expanded", "false");
      menuBtn.textContent = "☰";
    }

    menuBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = !drawer.classList.contains("is-open");
      drawer.classList.toggle("is-open", open);
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
      menuBtn.textContent = open ? "✕" : "☰";
    });

    document.addEventListener("click", (e) => {
      if (!appbar.contains(e.target) && !drawer.contains(e.target)) {
        closeDrawer();
      }
    });

    if (pills && pills.parentNode) {
      pills.parentNode.insertBefore(appbar, pills);
      pills.parentNode.insertBefore(drawer, pills);
    } else {
      shell.prepend(drawer);
      shell.prepend(appbar);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountBar);
  } else {
    mountBar();
  }
})();
