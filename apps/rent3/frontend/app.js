/* apps/rent3/frontend/app.js — OpenRent 벤치마킹 직거래 포털 & 본인인증 역할별 자동 라우팅 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3";
  const SESSION_KEY = "rent3_session";

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
  const safeOn = (id, event, fn) => {
    const el = $(id);
    if (el) el.addEventListener(event, fn);
  };
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

  function saveSession(user, tok) {
    if (tok !== undefined) Bass.setToken(tok || "");
    if (user) {
      window.localStorage.setItem(SESSION_KEY, JSON.stringify(user));
    } else {
      window.localStorage.removeItem(SESSION_KEY);
    }
  }

  function clearSession() {
    Bass.setToken("");
    window.localStorage.removeItem(SESSION_KEY);
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
    openAuthModal({ tab: "login", preset: "cosign", targetHint: hint });
  }

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

      strip.style.display = "block";
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
      if (quickAuth) {
        quickAuth.style.background = "#f8fafc";
        quickAuth.style.borderColor = "var(--or-border)";
        quickAuth.style.color = "var(--or-text)";
        quickAuth.innerHTML =
          `🔒 임대인 또는 임차인으로 <strong>본인인증 로그인</strong>하면 매물을 선택해 상대를 호출하거나 도착한 호출에 즉시 응답할 수 있습니다.`;
      }
    }
  }

  function switchAuthTab(tabName) {
    document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.authTab === tabName);
    });
    $("formAccountLogin").style.display = tabName === "login" ? "block" : "none";
    $("formRegister").style.display = tabName === "register" ? "block" : "none";
  }

  function openAuthModal({ tab = "login", preset = "", targetHint = "" } = {}) {
    switchAuthTab(tab === "register" ? "register" : "login");
    $("loginTargetHint").value = targetHint || "";
    $("chkAdminPoolHub").checked = targetHint === "pool";

    const sub = $("authModalSub");
    if ((targetHint || "").startsWith("contract")) {
      sub.textContent = "임대인 또는 임차인으로 본인인증 로그인(또는 회원가입)하면 즉시 공동계약 호출·응답 화면으로 이동합니다.";
    } else if (targetHint === "add_listing") {
      sub.textContent = "임대인으로 본인인증 로그인(또는 신규 가입)하면 보유 지점의 호실·매물 등록 탭으로 바로 이동합니다.";
    } else if (targetHint === "broker") {
      sub.textContent = "임대인으로 로그인하면 공인중개사 지면계약서 사진 등록 탭으로 바로 이동합니다.";
    } else if (preset === "tenant") {
      sub.textContent = "입주 세입자는 내 호실 마이홈으로, 예비임차인은 공실 탐색 화면으로 자동 이동합니다.";
      const ex = state.demoAccounts.find((d) => d.group === "임차인");
      if (ex) $("loginIdentifier").value = ex.identifier;
    } else if (preset === "lessor") {
      sub.textContent = "임대인 계정으로 로그인하면 본인 소유 건물 지점 장부 페이지로 자동 이동합니다.";
      const ex = state.demoAccounts.find((d) => d.group === "임대인");
      if (ex) $("loginIdentifier").value = ex.identifier;
    } else if (preset === "admin") {
      sub.textContent = "총괄 관리인(admin / 0202)으로 로그인하면 관리인 전용 앱(/rent3_admin/)으로 즉시 이동합니다.";
      $("loginIdentifier").value = "admin";
      $("loginPassword").value = "0202";
    } else {
      sub.textContent = "본인인증 로그인 즉시 계정의 역할에 맞는 전용 페이지로 자동 이동합니다.";
    }

    $("authModal").classList.add("is-open");
  }

  function closeAuthModal() {
    $("authModal").classList.remove("is-open");
  }

  function updateAffordPreview() {
    const incMan = Number($("calcIncomeMan").value || 0);
    const recMan = Math.max(20, Math.round(incMan * 0.3));
    $("calcRecommendedText").textContent = `월 ${recMan.toLocaleString("ko-KR")}만원 이하`;
    return recMan * 10000;
  }

  function filteredListings() {
    const kw = ($("searchKeyword").value || "").trim().toLowerCase();
    const br = $("searchBranch").value;
    const cat = $("searchCategory").value;

    return state.listings.filter((item) => {
      if (br && item.branch !== br) return false;
      if (cat && item.room_category !== cat) return false;

      if (state.quickFilter === "instant" && item.pre_moveout_notice) return false;
      if (state.quickFilter === "pre_moveout" && !item.pre_moveout_notice) return false;
      if (state.quickFilter === "clean_title" && !item.clean_title) return false;
      if (state.quickFilter === "commercial" && item.room_category === "주거용 원룸") return false;

      if (state.budgetActive) {
        const monthlyTotal = Number(item.monthly_rent || 0) + Number(item.maintenance_fee || 0);
        if (state.maxMonthlyWon > 0 && monthlyTotal > state.maxMonthlyWon) return false;
        if (state.maxDepositWon > 0 && Number(item.deposit || 0) > state.maxDepositWon) return false;
      }

      if (kw) {
        const hay = [
          item.building_name,
          item.address,
          item.room_no,
          item.name,
          item.room_category,
          item.status_description,
          ...(item.options || []),
        ].join(" ").toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }

  function renderListings() {
    const list = filteredListings();
    $("listingCountSub").innerHTML =
      `총 <strong>${list.length}건</strong>의 직거래 매물이 조건과 일치합니다 (전체 공개 ${state.listings.length}건 · 중개수수료 0원).`;

    const grid = $("listingsGrid");
    if (!list.length) {
      grid.innerHTML = `
        <div class="or-pillar" style="grid-column:1/-1; text-align:center; padding:36px;">
          <div style="font-size:16px; font-weight:800; color:var(--or-navy); margin-bottom:6px;">조건에 맞는 공개 매물이 없습니다.</div>
          <div style="font-size:13.5px; color:var(--or-muted); margin-bottom:14px;">검색어나 예산 필터를 초기화하면 전체 직거래 공실을 다시 확인할 수 있습니다.</div>
          <button type="button" id="btnEmptyReset" class="or-btn or-btn--outline or-btn--sm">필터 전체 초기화</button>
        </div>
      `;
      const resetBtn = $("btnEmptyReset");
      if (resetBtn) {
        resetBtn.addEventListener("click", () => {
          $("searchKeyword").value = "";
          $("searchBranch").value = "";
          $("searchCategory").value = "";
          state.quickFilter = "all";
          state.budgetActive = false;
          document.querySelectorAll("#quickFilterBar [data-filter]").forEach((b) => {
            b.classList.toggle("is-active", b.dataset.filter === "all");
          });
          renderListings();
        });
      }
      return;
    }

    grid.innerHTML = list.map((l) => `
      <article class="or-prop-card">
        <div class="or-prop-media">
          <img src="${esc(l.image)}" alt="${esc(l.building_name)} ${esc(l.room_no)}호">
          <div class="or-prop-badges">
            <span class="or-pill or-pill--green">복비 0원</span>
            ${l.pre_moveout_notice
              ? `<span class="or-pill or-pill--amber">📦 만기전출 사전공개 (${esc(l.available_from)})</span>`
              : `<span class="or-pill or-pill--navy">⚡ ${esc(l.available_from || "즉시입주")}</span>`}
            ${l.clean_title ? `<span class="or-pill or-pill--navy">🛡️ 등기클린</span>` : ""}
          </div>
        </div>
        <div class="or-prop-body">
          <div>
            <div style="font-size:12px; font-weight:700; color:var(--or-muted);">
              ${esc(l.building_name)} · ${esc(l.room_category)} · ${l.floor}층 (${l.area_sqm}㎡)
            </div>
            <h3 style="margin:4px 0 2px; font-size:17px; color:var(--or-navy);">
              [${esc(l.building_name)}] ${esc(l.name)}
            </h3>
            <div class="or-prop-price">
              보증금 ${fmtMan(l.deposit)} / 월 ${fmtMan(l.monthly_rent)}
              <span style="font-size:12.5px; font-weight:600; color:var(--or-muted);"> (관리비 ${fmtMan(l.maintenance_fee)})</span>
            </div>
            <div class="or-prop-meta">
              📍 ${esc(l.address || "주소 정보 등록됨")} · ${esc(l.pay_timing)} · 소유주 ${esc(l.lessor_name)}
            </div>
            <div class="or-tag-list">
              ${(l.options || []).slice(0, 5).map((opt) => `<span class="or-tag">${esc(opt)}</span>`).join("")}
            </div>
          </div>

          <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
            <button type="button" class="or-btn or-btn--outline or-btn--sm js-open-inquiry"
              data-branch="${esc(l.branch)}" data-room="${esc(l.room_no)}" data-bldg="${esc(l.building_name)}">
              💬 1:1 방문문의
            </button>
            <button type="button" class="or-btn or-btn--green or-btn--sm js-open-rentnow"
              data-branch="${esc(l.branch)}" data-room="${esc(l.room_no)}" data-bldg="${esc(l.building_name)}">
              📞 공동계약 호출
            </button>
          </div>
        </div>
      </article>
    `).join("");

    grid.querySelectorAll(".js-open-inquiry").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("inqBranch").value = btn.dataset.branch;
        $("inqRoomNo").value = btn.dataset.room;
        $("inqModalTitle").textContent = `💬 [${btn.dataset.bldg} ${btn.dataset.room}호] 집주인 1:1 방문문의`;
        const u = getSession();
        if (u && u.name) $("inqSenderName").value = u.name;
        if (u && u.phone) $("inqSenderPhone").value = u.phone;
        $("inquiryModal").classList.add("is-open");
      });
    });

    grid.querySelectorAll(".js-open-rentnow").forEach((btn) => {
      btn.addEventListener("click", () => {
        const u = getSession();
        if (!u || !u.name) {
          // 비로그인 시: 본인인증 로그인 후 해당 매물 호출 화면으로 즉시 이동!
          openAuthModal({
            tab: "login",
            preset: "cosign",
            targetHint: `contract:${btn.dataset.branch}:${btn.dataset.room}`,
          });
          return;
        }
        $("rnBranch").value = btn.dataset.branch;
        $("rnRoomNo").value = btn.dataset.room;
        const isLessor = u.role === "lessor" || u.role === "admin";
        $("rnCallerRole").value = isLessor ? "lessor" : "tenant";
        $("rnModalTitle").textContent = `📞 [${btn.dataset.bldg} ${btn.dataset.room}호] 상대방 호출 및 공동계약 시작`;
        $("rnVerifiedNotice").innerHTML =
          `🛡️ <strong>${esc(u.name)}</strong>님(본인인증 완료) 계정으로 <strong>${isLessor ? "임차인" : "임대인"}</strong>을 호출합니다.`;
        if (!isLessor) {
          $("rnTenantName").value = u.name || "";
          $("rnTenantPhone").value = u.phone || "";
        }
        $("rentNowModal").classList.add("is-open");
      });
    });
  }

  function renderDemoAccounts() {
    const box = $("demoAccountGrid");
    box.innerHTML = state.demoAccounts.map((acc, idx) => `
      <button type="button" class="or-demo-btn" data-demo-idx="${idx}">
        <div style="font-weight:800; color:var(--or-blue);">${esc(acc.badge)}</div>
        <div style="font-weight:600; margin-top:2px;">${esc(acc.label)}</div>
        <div style="font-size:11px; color:var(--or-muted);">이동: <code>${esc(acc.target_desc)}</code></div>
      </button>
    `).join("");

    box.querySelectorAll("[data-demo-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const acc = state.demoAccounts[Number(btn.dataset.demoIdx)];
        if (!acc) return;
        Bass.withLock(btn, async () => {
          const currentHint = $("loginTargetHint").value || "";
          const effectiveHint = currentHint.startsWith("contract")
            ? currentHint
            : (acc.target_hint || currentHint);
          const res = await Bass.api(APP_ID, "/pool/auth/login", {
            method: "POST",
            body: {
              login_mode: "account",
              identifier: acc.identifier,
              password: acc.password,
              target_hint: effectiveHint,
            },
          });
          saveSession(res.user, res.token);
          Bass.updateAppAlert("success", res.message, `이동 경로: ${res.redirect_url}`);
          window.location.href = res.redirect_url;
        }, "본인인증 확인 및 이동 중... ⏳");
      });
    });
  }

  async function loadPortalData() {
    const data = await Bass.api(APP_ID, "/pool/landing");
    const st = data.stats || {};
    state.listings = data.listings || [];
    state.branches = data.branches || [];
    state.openSessions = data.open_sessions || [];
    state.demoAccounts = data.demo_accounts || [];

    $("statBuildingsRooms").textContent = `${st.total_buildings || 0}채 · ${st.total_rooms || 0}실`;
    $("statVacancies").textContent = `${st.vacant_published_count || 0}건 공개 중`;
    $("statContractsPool").textContent = `${st.active_contracts || 0}건 활성 (Pool v${st.pool_revision || 0})`;

    const brSel = $("searchBranch");
    const prevBr = brSel.value;
    brSel.innerHTML = `<option value="">전체 건물 지점</option>` + state.branches.map((b) =>
      `<option value="${esc(b.branch)}">${esc(b.building_name)} (${esc(b.branch)})</option>`
    ).join("");
    brSel.value = prevBr;

    // 진행중 공동계약 호출/응답 현황 요약 바인딩
    const sessWrap = $("openSessionsWrap");
    const sessSummary = $("openSessionsSummary");
    if (state.openSessions.length && sessWrap && sessSummary) {
      sessWrap.style.display = "block";
      sessSummary.innerHTML = state.openSessions.slice(0, 3).map((s) => {
        const isCalling = s.call_status === "CALLING";
        const callerKo = s.initiated_by_role === "lessor" ? "임대인" : "임차인";
        const calleeKo = s.initiated_by_role === "lessor" ? "임차인" : "임대인";
        return `
          <div style="padding:6px 9px; border-radius:6px; background:${isCalling ? "#fef3c7" : "#eef2ff"}; color:${isCalling ? "#92400e" : "#1e3a8a"}; font-weight:600;">
            ${isCalling ? "🔔 호출 응답 대기" : "🤝 양측 연결됨"} · [${esc(s.building_name)} ${esc(s.room_no)}호]
            ${isCalling ? `${callerKo} 호출 → ${calleeKo} 응답 대기` : `Step ${s.current_step}/5 진행 중`}
          </div>
        `;
      }).join("");
    } else if (sessWrap) {
      sessWrap.style.display = "none";
    }

    renderListings();
    renderDemoAccounts();
  }

  // ── Event Bindings ──
  const btnMobileMenuToggle = $("btnMobileMenuToggle");
  const orNavMenu = $("orNavMenu");
  function closeMobileMenu() {
    if (!orNavMenu || !btnMobileMenuToggle) return;
    orNavMenu.classList.remove("is-open");
    btnMobileMenuToggle.classList.remove("is-active");
    btnMobileMenuToggle.setAttribute("aria-expanded", "false");
    btnMobileMenuToggle.textContent = "☰";
  }
  if (btnMobileMenuToggle && orNavMenu) {
    btnMobileMenuToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !orNavMenu.classList.contains("is-open");
      orNavMenu.classList.toggle("is-open", willOpen);
      btnMobileMenuToggle.classList.toggle("is-active", willOpen);
      btnMobileMenuToggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
      btnMobileMenuToggle.textContent = willOpen ? "✕" : "☰";
    });
    orNavMenu.addEventListener("click", (e) => {
      const link = e.target.closest("a.or-navlink");
      if (!link) {
        if (e.target.closest(".or-navlink")) closeMobileMenu();
        return;
      }
      const href = link.getAttribute("href") || "";
      const user = getSession();
      const isAdmin = Boolean(user && user.role === "admin");

      if (href.startsWith("/rent3_admin")) {
        if (!isAdmin) {
          e.preventDefault();
          closeMobileMenu();
          openAuthModal({ tab: "login", preset: "admin" });
          Bass.updateAppAlert("warning", "총괄 관리인(admin) 로그인이 필요합니다.");
          return;
        }
      } else if (href.startsWith("/rent3_lessor")) {
        if (!isAdmin && (!user || user.role !== "lessor")) {
          e.preventDefault();
          closeMobileMenu();
          openAuthModal({ tab: "login", preset: "lessor" });
          Bass.updateAppAlert("warning", "임대인(건물주) 또는 총괄 관리인 로그인이 필요합니다.");
          return;
        }
      } else if (href.startsWith("/rent3_tenant")) {
        if (!isAdmin && (!user || (user.role !== "tenant" && user.role !== "prospect"))) {
          e.preventDefault();
          closeMobileMenu();
          openAuthModal({ tab: "login", preset: "tenant" });
          Bass.updateAppAlert("warning", "입주 임차인 또는 예비 임차인 로그인이 필요합니다.");
          return;
        }
      } else if (href.startsWith("/rent3_contract")) {
        if (!isAdmin && (!user || !user.name)) {
          e.preventDefault();
          closeMobileMenu();
          navigateToCoSignOrLogin();
          return;
        }
      }
      closeMobileMenu();
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".or-navbar")) closeMobileMenu();
    });
  }

  safeOn("btnNavSignIn", "click", () => {
    closeMobileMenu();
    openAuthModal({ tab: "login" });
  });
  safeOn("btnOpenLoginSection", "click", () => openAuthModal({ tab: "login" }));
  safeOn("stripSwitchBtn", "click", () => openAuthModal({ tab: "login" }));
  safeOn("btnCloseAuthModal", "click", closeAuthModal);

  safeOn("btnNavAddListing", "click", () => {
    closeMobileMenu();
    const user = getSession();
    if (user && user.role === "lessor" && user.branch) {
      window.location.href = `/rent3_lessor/?branch=${encodeURIComponent(user.branch)}&tab=rooms`;
      return;
    }
    openAuthModal({ tab: "login", preset: "lessor", targetHint: "add_listing" });
  });

  safeOn("btnPillarBroker", "click", () => {
    const user = getSession();
    if (user && user.role === "lessor" && user.branch) {
      window.location.href = `/rent3_lessor/?branch=${encodeURIComponent(user.branch)}&tab=broker`;
      return;
    }
    openAuthModal({ tab: "login", preset: "lessor", targetHint: "broker" });
  });

  safeOn("btnNavLogout", "click", () => {
    clearSession();
    renderSessionUI();
    Bass.updateAppAlert("success", "안전하게 로그아웃되었습니다.", "다시 로그인하면 역할에 맞는 전용 페이지로 이동합니다.");
  });

  document.querySelectorAll(".js-role-login").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.dataset.rolePreset || "";
      if (preset === "cosign") {
        navigateToCoSignOrLogin();
      } else {
        openAuthModal({ tab: "login", preset });
      }
    });
  });

  safeOn("btnQuickCosignGo", "click", () => {
    navigateToCoSignOrLogin();
  });

  safeOn("authModalTabs", "click", (e) => {
    const tabBtn = e.target.closest("[data-auth-tab]");
    if (!tabBtn) return;
    switchAuthTab(tabBtn.dataset.authTab);
  });

  safeOn("regRole", "change", () => {
    const rf = $("regLessorFields");
    if (rf) rf.style.display = $("regRole").value === "lessor" ? "block" : "none";
  });

  // 통합 계정 본인인증 로그인 제출 -> 역할 판별 후 자동 리다이렉트
  safeOn("formAccountLogin", "submit", (e) => {
    e.preventDefault();
    const btn = $("btnSubmitLogin");
    Bass.withLock(btn, async () => {
      let hint = ($("loginTargetHint") && $("loginTargetHint").value) || "";
      if ($("chkAdminPoolHub") && $("chkAdminPoolHub").checked && !hint) hint = "pool";

      const res = await Bass.api(APP_ID, "/pool/auth/login", {
        method: "POST",
        body: {
          login_mode: "account",
          identifier: ($("loginIdentifier") && $("loginIdentifier").value) || "",
          password: ($("loginPassword") && $("loginPassword").value) || "",
          target_hint: hint,
        },
      });
      saveSession(res.user, res.token);
      closeAuthModal();
      Bass.updateAppAlert("success", res.message, `자동 이동: ${res.redirect_url}`);
      window.location.href = res.redirect_url;
    }, "본인인증 확인 및 이동 중... ⏳");
  });

  // 간편 본인인증 회원가입 -> Data Pool 등록 후 역할/공동계약 페이지로 자동 이동
  safeOn("formRegister", "submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnSubmitRegister"), async () => {
      const res = await Bass.api(APP_ID, "/pool/auth/register", {
        method: "POST",
        body: {
          role: ($("regRole") && $("regRole").value) || "tenant",
          name: ($("regName") && $("regName").value) || "",
          phone: ($("regPhone") && $("regPhone").value) || "",
          username: ($("regUsername") && $("regUsername").value) || "",
          building_name: ($("regBuildingName") && $("regBuildingName").value) || "",
          address: ($("regAddress") && $("regAddress").value) || "",
          password: ($("regPassword") && $("regPassword").value) || "",
          target_hint: ($("loginTargetHint") && $("loginTargetHint").value) || "",
        },
      });
      saveSession(res.user, res.token);
      closeAuthModal();
      Bass.updateAppAlert("success", res.message);
      window.location.href = res.redirect_url;
    }, "본인인증 가입 및 이동 중... ⏳");
  });

  // 검색 & 필터 바인딩
  safeOn("heroSearchForm", "submit", (e) => {
    e.preventDefault();
    renderListings();
    if ($("listingsSection")) $("listingsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  safeOn("searchBranch", "change", renderListings);
  safeOn("searchCategory", "change", renderListings);
  safeOn("btnRefreshListings", "click", function () {
    Bass.withLock(this, loadPortalData, "갱신 중...");
  });

  safeOn("quickFilterBar", "click", (e) => {
    const chip = e.target.closest("[data-filter]");
    if (!chip) return;
    state.quickFilter = chip.dataset.filter;
    document.querySelectorAll("#quickFilterBar [data-filter]").forEach((b) => {
      b.classList.toggle("is-active", b === chip);
    });
    renderListings();
  });

  // Affordability Calculator
  safeOn("btnToggleAfford", "click", () => {
    const p = $("affordPanel");
    if (!p) return;
    const open = p.style.display === "none";
    p.style.display = open ? "block" : "none";
    if ($("btnToggleAfford")) $("btnToggleAfford").classList.toggle("is-active", open);
    updateAffordPreview();
  });
  safeOn("calcIncomeMan", "input", updateAffordPreview);
  safeOn("btnApplyBudget", "click", () => {
    state.maxMonthlyWon = updateAffordPreview();
    state.maxDepositWon = Number(($("calcDepositMan") && $("calcDepositMan").value) || 0) * 10000;
    state.budgetActive = true;
    renderListings();
    Bass.updateAppAlert(
      "success",
      `월세+관리비 ${fmtMan(state.maxMonthlyWon)} 이하 · 보증금 ${fmtMan(state.maxDepositWon)} 이하 조건으로 필터링했습니다.`
    );
    if ($("listingsSection")) $("listingsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  safeOn("btnResetBudget", "click", () => {
    state.budgetActive = false;
    renderListings();
    Bass.updateAppAlert("warning", "월세 예산 필터가 해제되었습니다.");
  });

  // 1:1 방문투어 문의 모달
  safeOn("btnCloseInquiryModal", "click", () => { if ($("inquiryModal")) $("inquiryModal").classList.remove("is-open"); });
  safeOn("formInquiry", "submit", (e) => {
    e.preventDefault();
    const btn = $("btnSubmitInquiry");
    Bass.withLock(btn, async () => {
      const res = await Bass.api(APP_ID, "/pool/inquiries", {
        method: "POST",
        body: {
          branch: ($("inqBranch") && $("inqBranch").value) || "",
          room_no: ($("inqRoomNo") && $("inqRoomNo").value) || "",
          sender_name: ($("inqSenderName") && $("inqSenderName").value) || "",
          sender_phone: ($("inqSenderPhone") && $("inqSenderPhone").value) || "",
          visit_date: ($("inqVisitDate") && $("inqVisitDate").value) || "",
          message: ($("inqMessage") && $("inqMessage").value) || "",
        },
      });
      if ($("inquiryModal")) $("inquiryModal").classList.remove("is-open");
      if ($("inqMessage")) $("inqMessage").value = "";
      Bass.updateAppAlert("success", res.message, "임대인 콘솔(rent3_lessor) 1:1 채팅함에 즉시 기록되었습니다.");
    }, "전송 중... ⏳");
  });

  // Rent Now 상대방 호출 모달
  safeOn("btnCloseRentNowModal", "click", () => { if ($("rentNowModal")) $("rentNowModal").classList.remove("is-open"); });
  safeOn("formRentNow", "submit", (e) => {
    e.preventDefault();
    const u = getSession();
    const btn = $("btnSubmitRentNow");
    Bass.withLock(btn, async () => {
      const res = await Bass.api(APP_ID, "/pool/cosign-open", {
        method: "POST",
        body: {
          caller_role: ($("rnCallerRole") && $("rnCallerRole").value) || "tenant",
          branch: ($("rnBranch") && $("rnBranch").value) || "",
          room_no: ($("rnRoomNo") && $("rnRoomNo").value) || "",
          tenant_name: ($("rnTenantName") && $("rnTenantName").value) || "",
          tenant_phone: ($("rnTenantPhone") && $("rnTenantPhone").value) || "",
          id_tenant: u && (u.tenant_id || (u.role !== "lessor" ? u.id : "")) ? (u.tenant_id || u.id) : "",
        },
      });
      if ($("rentNowModal")) $("rentNowModal").classList.remove("is-open");
      Bass.updateAppAlert("success", res.message);
      window.location.href = res.redirect_url;
    }, "상대방 호출 발송 중... ⏳");
  });

  // 모달 바깥 배경 클릭 시 닫기
  ["authModal", "inquiryModal", "rentNowModal"].forEach((mid) => {
    safeOn(mid, "click", (e) => {
      const m = $(mid);
      if (m && e.target === m) m.classList.remove("is-open");
    });
  });

  // 초기 진입 처리 (?logout=1 또는 ?login=1 지원)
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get("logout") === "1") {
    clearSession();
    window.history.replaceState({}, "", window.location.pathname);
    Bass.updateAppAlert("success", "로그아웃되어 OpenRent 직거래 포털 홈으로 이동했습니다.");
  }

  renderSessionUI();
  loadPortalData()
    .then(() => {
      if (urlParams.get("login") === "1") {
        openAuthModal({ tab: "login" });
      }
    })
    .catch(Bass.showError);
})();
