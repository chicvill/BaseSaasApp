/* apps/rent3/frontend/app.js — OpenRent 벤치마킹 직거래 포털 & 역할 기반 자동 라우팅 클라이언트 */
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

  function renderSessionUI() {
    const user = getSession();
    const btnSignIn = $("btnNavSignIn");
    const loggedBox = $("navLoggedBox");
    const strip = $("activeSessionStrip");

    if (user && user.redirect_url) {
      btnSignIn.style.display = "none";
      loggedBox.style.display = "inline-flex";
      $("navUserBadge").textContent = `${roleIcon(user.role)} ${user.name} · ${user.role_label || user.role}`;
      $("navGoWorkspace").href = user.redirect_url;

      strip.style.display = "block";
      $("activeSessionText").innerHTML =
        `현재 <strong>${esc(user.name)}</strong>님(<code>${esc(user.role_label || user.role)}</code>)으로 로그인되어 있습니다. 역할에 맞는 전용 워크스페이스로 바로 이동할 수 있습니다.`;
      $("stripGoBtn").href = user.redirect_url;
    } else {
      btnSignIn.style.display = "inline-flex";
      loggedBox.style.display = "none";
      strip.style.display = "none";
    }
  }

  function switchAuthTab(tabName) {
    document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.authTab === tabName);
    });
    $("formAccountLogin").style.display = tabName === "login" ? "block" : "none";
    $("formCosignLogin").style.display = tabName === "cosign" ? "block" : "none";
    $("formRegister").style.display = tabName === "register" ? "block" : "none";
  }

  function openAuthModal({ tab = "login", preset = "", targetHint = "" } = {}) {
    switchAuthTab(tab);
    $("loginTargetHint").value = targetHint || "";
    $("chkAdminPoolHub").checked = targetHint === "pool";

    const sub = $("authModalSub");
    if (targetHint === "add_listing") {
      sub.textContent = "임대인으로 로그인(또는 신규 가입)하면 보유 지점의 호실·매물 등록 탭으로 바로 이동합니다.";
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
      sub.textContent = "총괄 관리인(admin)으로 로그인하면 가맹·정산·전사 수익률 관제 페이지로 이동합니다.";
      $("loginIdentifier").value = "admin";
    } else {
      sub.textContent = "로그인 즉시 계정의 역할에 맞는 전용 페이지로 자동 이동합니다.";
    }

    if (tab === "cosign" && state.openSessions.length && !$("modalSessionCode").value) {
      $("modalSessionCode").value = state.openSessions[0].session_code;
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
              🤝 Rent Now 계약
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
        $("inquiryModal").classList.add("is-open");
      });
    });

    grid.querySelectorAll(".js-open-rentnow").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("rnBranch").value = btn.dataset.branch;
        $("rnRoomNo").value = btn.dataset.room;
        $("rnModalTitle").textContent = `🤝 [${btn.dataset.bldg} ${btn.dataset.room}호] Rent Now 공동계약 시작`;
        const u = getSession();
        if (u && u.name) $("rnTenantName").value = u.name;
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
          const res = await Bass.api(APP_ID, "/pool/auth/login", {
            method: "POST",
            body: {
              login_mode: "account",
              identifier: acc.identifier,
              password: acc.password,
              target_hint: acc.target_hint || $("loginTargetHint").value || "",
            },
          });
          saveSession(res.user, res.token);
          Bass.updateAppAlert("success", res.message, `이동 경로: ${res.redirect_url}`);
          window.location.href = res.redirect_url;
        }, "로그인 이동 중... ⏳");
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

    // 진행중 공동계약 세션 빠른 선택 바인딩
    const sessWrap = $("openSessionsWrap");
    const sessPicker = $("openSessionPicker");
    if (state.openSessions.length) {
      sessWrap.style.display = "block";
      sessPicker.innerHTML = state.openSessions.map((s) =>
        `<option value="${esc(s.session_code)}">[코드 ${esc(s.session_code)}] ${esc(s.building_name)} ${esc(s.room_no)}호 (Step ${s.current_step}/5)</option>`
      ).join("");
      if (!$("quickSessionCode").value) {
        $("quickSessionCode").value = state.openSessions[0].session_code;
      }
    } else {
      sessWrap.style.display = "none";
    }

    renderListings();
    renderDemoAccounts();
  }

  // ── Event Bindings ──
  $("btnNavSignIn").addEventListener("click", () => openAuthModal({ tab: "login" }));
  $("btnOpenLoginSection").addEventListener("click", () => openAuthModal({ tab: "login" }));
  $("stripSwitchBtn").addEventListener("click", () => openAuthModal({ tab: "login" }));
  $("btnCloseAuthModal").addEventListener("click", closeAuthModal);

  $("btnNavAddListing").addEventListener("click", () => {
    const user = getSession();
    if (user && user.role === "lessor" && user.branch) {
      window.location.href = `/rent3_lessor/?branch=${encodeURIComponent(user.branch)}&tab=rooms`;
      return;
    }
    openAuthModal({ tab: "login", preset: "lessor", targetHint: "add_listing" });
  });

  $("btnPillarBroker").addEventListener("click", () => {
    const user = getSession();
    if (user && user.role === "lessor" && user.branch) {
      window.location.href = `/rent3_lessor/?branch=${encodeURIComponent(user.branch)}&tab=broker`;
      return;
    }
    openAuthModal({ tab: "login", preset: "lessor", targetHint: "broker" });
  });

  $("btnNavLogout").addEventListener("click", () => {
    clearSession();
    renderSessionUI();
    Bass.updateAppAlert("success", "안전하게 로그아웃되었습니다.", "다시 로그인하면 역할에 맞는 전용 페이지로 이동합니다.");
  });

  document.querySelectorAll(".js-role-login").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.dataset.rolePreset || "";
      if (preset === "cosign") {
        openAuthModal({ tab: "cosign" });
      } else {
        openAuthModal({ tab: "login", preset });
      }
    });
  });

  $("authModalTabs").addEventListener("click", (e) => {
    const tabBtn = e.target.closest("[data-auth-tab]");
    if (!tabBtn) return;
    switchAuthTab(tabBtn.dataset.authTab);
  });

  $("regRole").addEventListener("change", () => {
    $("regLessorFields").style.display = $("regRole").value === "lessor" ? "block" : "none";
  });

  // 통합 계정 로그인 제출 -> 역할 판별 후 자동 리다이렉트
  $("formAccountLogin").addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = $("btnSubmitLogin");
    Bass.withLock(btn, async () => {
      let hint = $("loginTargetHint").value || "";
      if ($("chkAdminPoolHub").checked && !hint) hint = "pool";

      const res = await Bass.api(APP_ID, "/pool/auth/login", {
        method: "POST",
        body: {
          login_mode: "account",
          identifier: $("loginIdentifier").value,
          password: $("loginPassword").value,
          target_hint: hint,
        },
      });
      saveSession(res.user, res.token);
      closeAuthModal();
      Bass.updateAppAlert("success", res.message, `자동 이동: ${res.redirect_url}`);
      window.location.href = res.redirect_url;
    }, "역할 확인 및 이동 중... ⏳");
  });

  // 공동계약 6자리 참여코드 로그인 제출 -> rent3_contract 자동 이동
  $("formCosignLogin").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnSubmitCosign"), async () => {
      const res = await Bass.api(APP_ID, "/pool/auth/login", {
        method: "POST",
        body: {
          login_mode: "cosign",
          session_code: $("modalSessionCode").value,
          cosign_role: $("modalCosignRole").value,
        },
      });
      saveSession(res.user, "");
      closeAuthModal();
      window.location.href = res.redirect_url;
    }, "공동계약방 연결 중... ⏳");
  });

  // 메인 배너 공동계약 코드 즉시 입장
  $("openSessionPicker").addEventListener("change", () => {
    $("quickSessionCode").value = $("openSessionPicker").value;
  });

  $("quickCosignForm").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnQuickCosignGo"), async () => {
      const res = await Bass.api(APP_ID, "/pool/auth/login", {
        method: "POST",
        body: {
          login_mode: "cosign",
          session_code: $("quickSessionCode").value,
          cosign_role: $("quickCosignRole").value,
        },
      });
      saveSession(res.user, "");
      window.location.href = res.redirect_url;
    }, "공동계약방 연결 중... ⏳");
  });

  // 간편 회원가입 -> Data Pool 등록 후 역할 페이지로 자동 이동
  $("formRegister").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnSubmitRegister"), async () => {
      const res = await Bass.api(APP_ID, "/pool/auth/register", {
        method: "POST",
        body: {
          role: $("regRole").value,
          name: $("regName").value,
          phone: $("regPhone").value,
          username: $("regUsername").value,
          building_name: $("regBuildingName").value,
          address: $("regAddress").value,
          password: $("regPassword").value,
        },
      });
      saveSession(res.user, res.token);
      closeAuthModal();
      Bass.updateAppAlert("success", res.message);
      window.location.href = res.redirect_url;
    }, "가입 및 워크스페이스 생성 중... ⏳");
  });

  // 검색 & 필터 바인딩
  $("heroSearchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    renderListings();
    $("listingsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("searchBranch").addEventListener("change", renderListings);
  $("searchCategory").addEventListener("change", renderListings);
  $("btnRefreshListings").addEventListener("click", function () {
    Bass.withLock(this, loadPortalData, "갱신 중...");
  });

  $("quickFilterBar").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-filter]");
    if (!chip) return;
    state.quickFilter = chip.dataset.filter;
    document.querySelectorAll("#quickFilterBar [data-filter]").forEach((b) => {
      b.classList.toggle("is-active", b === chip);
    });
    renderListings();
  });

  // Affordability Calculator
  $("btnToggleAfford").addEventListener("click", () => {
    const p = $("affordPanel");
    const open = p.style.display === "none";
    p.style.display = open ? "block" : "none";
    $("btnToggleAfford").classList.toggle("is-active", open);
    updateAffordPreview();
  });
  $("calcIncomeMan").addEventListener("input", updateAffordPreview);
  $("btnApplyBudget").addEventListener("click", () => {
    state.maxMonthlyWon = updateAffordPreview();
    state.maxDepositWon = Number($("calcDepositMan").value || 0) * 10000;
    state.budgetActive = true;
    renderListings();
    Bass.updateAppAlert(
      "success",
      `월세+관리비 ${fmtMan(state.maxMonthlyWon)} 이하 · 보증금 ${fmtMan(state.maxDepositWon)} 이하 조건으로 필터링했습니다.`
    );
    $("listingsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("btnResetBudget").addEventListener("click", () => {
    state.budgetActive = false;
    renderListings();
    Bass.updateAppAlert("warning", "월세 예산 필터가 해제되었습니다.");
  });

  // 1:1 방문투어 문의 모달
  $("btnCloseInquiryModal").addEventListener("click", () => $("inquiryModal").classList.remove("is-open"));
  $("formInquiry").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnSubmitInquiry"), async () => {
      const res = await Bass.api(APP_ID, "/pool/inquiries", {
        method: "POST",
        body: {
          branch: $("inqBranch").value,
          room_no: $("inqRoomNo").value,
          sender_name: $("inqSenderName").value,
          sender_phone: $("inqSenderPhone").value,
          visit_date: $("inqVisitDate").value,
          message: $("inqMessage").value,
        },
      });
      $("inquiryModal").classList.remove("is-open");
      $("inqMessage").value = "";
      Bass.updateAppAlert("success", res.message, "임대인 콘솔(rent3_lessor) 1:1 채팅함에 즉시 기록되었습니다.");
    }, "전송 중... ⏳");
  });

  // Rent Now 공동계약 시작 모달
  $("btnCloseRentNowModal").addEventListener("click", () => $("rentNowModal").classList.remove("is-open"));
  $("formRentNow").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnSubmitRentNow"), async () => {
      const res = await Bass.api(APP_ID, "/pool/cosign-open", {
        method: "POST",
        body: {
          branch: $("rnBranch").value,
          room_no: $("rnRoomNo").value,
          tenant_name: $("rnTenantName").value,
          tenant_phone: $("rnTenantPhone").value,
        },
      });
      $("rentNowModal").classList.remove("is-open");
      Bass.updateAppAlert("success", res.message);
      window.location.href = res.redirect_url;
    }, "공동계약 개설 중... ⏳");
  });

  // 모달 바깥 배경 클릭 시 닫기
  ["authModal", "inquiryModal", "rentNowModal"].forEach((mid) => {
    $(mid).addEventListener("click", (e) => {
      if (e.target === $(mid)) $(mid).classList.remove("is-open");
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

