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
    try { if (Bass.token()) fetch("/api/rent3/pool/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + Bass.token() }, keepalive: true }); } catch (_) {}
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
    const fr = $("formReset"); if (fr) fr.style.display = "none";
    const fcn = $("formConnect"); if (fcn) fcn.style.display = "none";
    const alertBox = $("authModalAlert");
    if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }

    // 로그인에 입력한 번호가 있으면 회원가입 탭 진입 시 자동 연동
    const ident = ($("loginIdentifier").value || "").trim();
    const digits = ident.replace(/[^0-9]/g, "");
    if (tabName === "register" && !$("regPhone").value && digits.length >= 8) {
      $("regPhone").value = digits.length === 11 ? `${digits.slice(0,3)}-${digits.slice(3,7)}-${digits.slice(7)}` : ident;
    }
  }

  function openAuthModal({ tab = "login", preset = "", targetHint = "" } = {}) {
    const alertBox = $("authModalAlert");
    if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }
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
      sub.textContent = "총괄 관리인 계정으로 로그인하면 관리인 전용 앱(/rent3_admin/)으로 즉시 이동합니다.";
      $("loginIdentifier").value = "admin";
      if (state.demoAccounts.length) $("loginPassword").value = "0202"; // 시연(로컬) 환경에서만 미리 채움
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
              ${esc(l.building_name)} · ${esc(l.room_category)} · ${l.floor}${l.total_floors ? `/${l.total_floors}` : ""}층 · 전용 ${l.area_sqm}㎡${l.direction ? ` · ${esc(l.direction)}` : ""}
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

          <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px;">
            <button type="button" class="or-btn or-btn--outline or-btn--sm js-open-inquiry"
              data-branch="${esc(l.branch)}" data-room="${esc(l.room_no)}" data-bldg="${esc(l.building_name)}">
              💬 1:1 방문문의
            </button>
            <button type="button" class="or-btn or-btn--green or-btn--sm js-open-rentnow"
              data-branch="${esc(l.branch)}" data-room="${esc(l.room_no)}" data-bldg="${esc(l.building_name)}"
              ${l.contract_in_progress ? 'disabled aria-disabled="true" title="다른 분과 계약이 진행 중입니다" style="opacity:.55;cursor:not-allowed;background:#94a3b8;border-color:#94a3b8;"' : ""}>
              ${l.contract_in_progress ? "🔒 계약 진행 중" : "📞 공동계약 호출"}
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
          const done = await resolveConsent(res);
          saveSession(done.user, done.token);
          Bass.updateAppAlert("success", done.message, `이동 경로: ${done.redirect_url}`);
          window.location.href = done.redirect_url;
        }, "본인인증 확인 및 이동 중... ⏳");
      });
    });
  }

  // ── 로그인 결과가 '동의 필요'이면 동의 화면을 거쳐 토큰을 받는다 ──
  function resolveConsent(res) {
    if (!res || !res.needs_consent) return Promise.resolve(res);
    return new Promise((resolve, reject) => {
      const bg = document.createElement("div");
      bg.style.cssText = "position:fixed;inset:0;z-index:100000;background:rgba(15,23,42,.6);display:flex;align-items:center;justify-content:center;padding:14px;overflow-y:auto;";
      bg.innerHTML = `<div style="background:#fff;color:#0f172a;border-radius:16px;max-width:460px;width:100%;padding:18px 16px;line-height:1.6;">
        <div style="font-weight:800;font-size:17px;margin-bottom:6px;">${esc(res.user.name)}님, 동의가 필요합니다</div>
        <div style="font-size:13px;margin-bottom:10px;">서비스를 이용하려면 아래 내용을 확인하고 동의해 주세요.</div>
        <label style="display:flex;gap:8px;align-items:flex-start;font-size:13px;">
          <input type="checkbox" id="csPrivacy" style="margin-top:3px;">
          <span><strong>(필수)</strong> 개인정보 수집·이용에 동의합니다. <a href="privacy.html" target="_blank" rel="noopener">전문 보기</a></span>
        </label>
        <label style="display:flex;gap:8px;align-items:flex-start;font-size:13px;margin-top:8px;">
          <input type="checkbox" id="csSupport" style="margin-top:3px;">
          <span><strong>(선택)</strong> 관리인 원격 지원 접속에 동의합니다. 도움을 요청하신 때에 한해 관리인이 <strong>보기만</strong> 하며 금전·계약 기록은 고칠 수 없고, 접속 기록이 남습니다. 메뉴에서 언제든 끌 수 있습니다.</span>
        </label>
        <div id="csWarn" style="display:none;margin-top:10px;color:#b91c1c;font-size:12.5px;font-weight:700;">⚠️ 개인정보 수집·이용(필수)에 체크해야 계속할 수 있습니다.</div>
        <div style="display:flex;gap:8px;margin-top:14px;">
          <button type="button" id="csOk" class="or-btn or-btn--green" style="flex:1;padding:11px;">동의하고 계속</button>
          <button type="button" id="csNo" class="or-btn" style="padding:11px;">취소</button>
        </div></div>`;
      document.body.appendChild(bg);
      const close = () => bg.remove();
      bg.querySelector("#csNo").addEventListener("click", () => { close(); reject(new Error("동의하지 않아 로그인을 취소했습니다.")); });
      bg.querySelector("#csOk").addEventListener("click", async function () {
        if (!bg.querySelector("#csPrivacy").checked) {
          bg.querySelector("#csWarn").style.display = "block";   // 팝업 안에 바로 보이게 한다(알림창은 팝업 뒤에 가려질 수 있음)
          return;
        }
        bg.querySelector("#csWarn").style.display = "none";
        try {
          const done = await Bass.api(APP_ID, "/pool/auth/consent", {
            method: "POST",
            body: { ticket: res.consent_ticket, agree_privacy: true, agree_support: bg.querySelector("#csSupport").checked },
          });
          close();
          resolve(done);
        } catch (e) { close(); reject(e); }
      });
    });
  }

  // ── 비밀번호 재설정 (본인인증 기반) ──
  function bindPasswordReset() {
    const link = $("lnkForgot");
    if (!link) return;
    link.addEventListener("click", (e) => {
      e.preventDefault();
      $("formAccountLogin").style.display = "none";
      $("formRegister").style.display = "none";
      $("formReset").style.display = "block";
      if (!$("rsIdent").value) $("rsIdent").value = ($("loginIdentifier").value || "").trim();
    });
    $("lnkBackLogin").addEventListener("click", (e) => { e.preventDefault(); switchAuthTab("login"); });
    const body = () => ({
      identifier: $("rsIdent").value.trim(), name: $("rsName").value.trim(),
      phone: $("rsPhone").value.trim(), birth: $("rsBirth").value.trim(), proof: ($("rsProof") ? $("rsProof").value.trim() : ""),
    });
    // 메인 화면의 '비밀번호를 잊으셨나요? 내 페이지 찾기' 링크: 로그인 창을 열고 곧바로 재설정 화면을 보인다
    document.querySelectorAll(".js-forgot").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      openAuthModal({ tab: "login" });
      link.click();
    }));
    $("btnRsStart").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/pool/auth/password-reset/start", { method: "POST", body: body() });
        Bass.updateAppAlert("success", r.message, r.hint || "");
      });
    });
    $("btnRsConfirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const newPw = $("rsNew").value;
        const r = await Bass.api(APP_ID, "/pool/auth/password-reset/confirm", {
          method: "POST", body: { ...body(), code: $("rsCode").value.trim(), new_password: newPw } });
        Bass.updateAppAlert("success", r.message);
        const ident = $("rsIdent").value.trim();
        $("rsNew").value = ""; $("rsCode").value = ""; if ($("rsProof")) $("rsProof").value = "";
        $("loginIdentifier").value = ident;
        $("loginPassword").value = "";
        // 본인 확인을 방금 마쳤으므로 새 비밀번호로 곧바로 내 페이지에 들어간다(실패하면 로그인 화면으로)
        try {
          const res = await Bass.api(APP_ID, "/pool/auth/login", { method: "POST", body: { login_mode: "account", identifier: ident, password: newPw, target_hint: "" } });
          const done = await resolveConsent(res);
          saveSession(done.user, done.token);
          closeAuthModal();
          window.location.href = done.redirect_url;
          return;
        } catch (_) { /* 자동 입장이 안 되면 아래처럼 로그인 화면에서 직접 들어간다 */ }
        switchAuthTab("login");
      });
    });
  }

  // ── 기존 임차인 '내 페이지 연결 요청'(문자 인증 없이, 임대인이 통화로 확인번호를 확인해 승인) ──
  const CC_KEY = "rent3_connect_req";
  function bindConnect() {
    const form = $("formConnect");
    if (!form) return;
    let timer = null;
    const load = () => { try { return JSON.parse(window.localStorage.getItem(CC_KEY) || "null"); } catch (_) { return null; } };
    const store = (v) => { try { if (v) window.localStorage.setItem(CC_KEY, JSON.stringify(v)); else window.localStorage.removeItem(CC_KEY); } catch (_) { /* 저장이 막혀도 이 화면에서는 계속 진행 */ } };
    async function poll() {
      const cur = load();
      if (!cur) return;
      try {
        const r = await Bass.api(APP_ID, "/pool/connect/status", { method: "POST", body: { request_id: cur.id, secret: cur.secret } });
        if (r.invite_url) { clearInterval(timer); store(null); window.location.href = r.invite_url; return; }
        $("ccStatus").textContent = r.message || "";
        if (r.status !== "PENDING" && r.status !== "APPROVED") { clearInterval(timer); store(null); setTimeout(render, 2500); }
      } catch (_) { clearInterval(timer); store(null); render(); }
    }
    function render() {
      const cur = load();
      $("ccInput").style.display = cur ? "none" : "block";
      $("ccWait").style.display = cur ? "block" : "none";
      clearInterval(timer);
      if (cur) { $("ccCode").textContent = cur.code; $("ccRoom2").textContent = `${cur.room_no}호`; $("ccStatus").textContent = "임대인의 확인을 기다리고 있습니다…"; poll(); timer = setInterval(poll, 4000); }
    }
    function show() {
      $("formAccountLogin").style.display = "none"; $("formRegister").style.display = "none"; $("formReset").style.display = "none";
      form.style.display = "block";
      render();
    }
    const lc = $("lnkConnect");
    if (lc) lc.addEventListener("click", (e) => { e.preventDefault(); show(); });
    $("lnkConnectBack").addEventListener("click", (e) => { e.preventDefault(); clearInterval(timer); switchAuthTab("login"); });
    $("lnkCcCancel").addEventListener("click", (e) => { e.preventDefault(); store(null); render(); });
    $("btnCcRequest").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/pool/connect/request", { method: "POST", body: {
          building_name: $("ccBuilding").value.trim(), room_no: $("ccRoom").value.trim(), name: $("ccName").value.trim(), phone: $("ccPhone").value.trim() } });
        store({ id: r.request_id, secret: r.secret, code: r.code, room_no: r.room_no });
        Bass.updateAppAlert("success", r.message, r.hint || "");
        render();
      });
    });
    window.__rent3ConnectShow = () => { openAuthModal({ tab: "login" }); show(); };
    window.__rent3ConnectPending = () => Boolean(load());
  }

  async function loadPortalData() {
    const data = await Bass.api(APP_ID, "/pool/landing");
    const st = data.stats || {};
    state.listings = data.listings || [];
    state.branches = data.branches || [];
    state.openSessions = data.open_sessions || [];
    state.demoAccounts = data.demo_accounts || [];
    // 시연(로컬) 환경에서만 공통 비밀번호를 미리 채우고 데모 칩을 보여 준다. 배포 환경은 빈 칸.
    const demoOn = state.demoAccounts.length > 0;
    if (demoOn) {
      if (!$("loginPassword").value) $("loginPassword").value = "1212";
      if (!$("regPassword").value) $("regPassword").value = "1212";
    }
    if (!demoOn) $("loginIdentifier").placeholder = "아이디, 이메일 또는 휴대폰 번호";   // 운영: 성명 로그인·시연 계정 안내 없음
    const demoBox = $("demoAccountGrid") && $("demoAccountGrid").parentElement;
    if (demoBox) demoBox.style.display = demoOn ? "" : "none";

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
        navigateToCoSignOrLogin();
      } else {
        openAuthModal({ tab: "login", preset });
      }
    });
  });

  $("btnQuickCosignGo").addEventListener("click", () => {
    navigateToCoSignOrLogin();
  });

  $("authModalTabs").addEventListener("click", (e) => {
    const tabBtn = e.target.closest("[data-auth-tab]");
    if (!tabBtn) return;
    switchAuthTab(tabBtn.dataset.authTab);
  });

  $("regRole").addEventListener("change", () => {
    $("regLessorFields").style.display = $("regRole").value === "lessor" ? "block" : "none";
  });

  // 통합 계정 본인인증 로그인 제출 -> 역할 판별 후 자동 리다이렉트
  $("formAccountLogin").addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = $("btnSubmitLogin");
    const alertBox = $("authModalAlert");
    if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }

    const rawIdent = ($("loginIdentifier").value || "").trim();
    const rawDigits = rawIdent.replace(/[^0-9]/g, "");
    const formattedPhone = rawDigits.length === 11
      ? `${rawDigits.slice(0,3)}-${rawDigits.slice(3,7)}-${rawDigits.slice(7)}`
      : rawIdent;

    Bass.withLock(btn, async () => {
      let hint = $("loginTargetHint").value || "";
      if ($("chkAdminPoolHub").checked && !hint) hint = "pool";

      try {
        const res = await Bass.api(APP_ID, "/pool/auth/login", {
          method: "POST",
          body: {
            login_mode: "account",
            identifier: rawIdent,
            password: $("loginPassword").value,
            target_hint: hint,
          },
        });
        const done = await resolveConsent(res);
        saveSession(done.user, done.token);
        closeAuthModal();
        Bass.updateAppAlert("success", done.message, `자동 이동: ${done.redirect_url}`);
        window.location.href = done.redirect_url;
      } catch (err) {
        if (alertBox) {
          const isNotFound = (err.message || "").includes("찾을 수 없습니다");
          let quickBtnHtml = "";
          if (isNotFound && (rawDigits.length >= 8 || rawIdent)) {
            quickBtnHtml = `
              <div style="margin-top:10px; padding-top:8px; border-top:1px dashed #fca5a5;">
                <div style="font-size:12px; color:#991b1b; margin-bottom:6px; font-weight:600;">
                  💡 아직 등록되지 않은 번호입니다. 간편 본인인증으로 즉시 가입하시겠습니까?
                </div>
                <button type="button" id="btnSwitchToRegisterWithPhone" class="or-btn or-btn--green or-btn--sm" style="width:100%; font-size:13px; font-weight:700;">
                  ✨ '${formattedPhone}' 번호로 3초 간편가입 진행 ➔
                </button>
              </div>`;
          }

          alertBox.style.display = "block";
          alertBox.innerHTML = `
            <div style="background:#fef2f2; border:1.5px solid #f87171; border-radius:10px; padding:12px 14px; color:#7f1d1d;">
              <div style="font-weight:800; font-size:14px; display:flex; align-items:center; gap:6px;">
                <span>❌</span> <span>${Bass.escapeHtml(err.message || "로그인에 실패했습니다.")}</span>
              </div>
              ${err.hint ? `<div style="font-size:12.5px; color:#991b1b; margin-top:4px; font-weight:600; line-height:1.4;">${Bass.escapeHtml(err.hint)}</div>` : ""}
              ${quickBtnHtml}
            </div>`;

          const quickBtn = $("btnSwitchToRegisterWithPhone");
          if (quickBtn) {
            quickBtn.addEventListener("click", () => {
              alertBox.style.display = "none";
              switchAuthTab("register");
              $("regPhone").value = formattedPhone;
              $("regName").focus();
            });
          }
        }
        throw err;
      }
    }, "본인인증 확인 및 이동 중... ⏳");
  });

  // 간편 본인인증 회원가입 -> Data Pool 등록 후 역할/공동계약 페이지로 자동 이동
  $("formRegister").addEventListener("submit", (e) => {
    e.preventDefault();
    const alertBox = $("authModalAlert");
    if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }

    if (!$("regAgreePrivacy").checked) {
      Bass.updateAppAlert("warning", "개인정보 수집·이용(필수)에 동의해 주세요.", "동의하지 않으면 가입할 수 없습니다.");
      return;
    }
    Bass.withLock($("btnSubmitRegister"), async () => {
      try {
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
            target_hint: $("loginTargetHint").value || "",
            agree_privacy: $("regAgreePrivacy").checked,
            agree_support: $("regAgreeSupport").checked,
          },
        });
        saveSession(res.user, res.token);
        closeAuthModal();
        Bass.updateAppAlert("success", res.message);
        window.location.href = res.redirect_url;
      } catch (err) {
        if (alertBox) {
          alertBox.style.display = "block";
          alertBox.innerHTML = `
            <div style="background:#fef2f2; border:1.5px solid #f87171; border-radius:10px; padding:12px 14px; color:#7f1d1d;">
              <div style="font-weight:800; font-size:14px; display:flex; align-items:center; gap:6px;">
                <span>❌</span> <span>${Bass.escapeHtml(err.message || "회원가입에 실패했습니다.")}</span>
              </div>
              ${err.hint ? `<div style="font-size:12.5px; color:#991b1b; margin-top:4px; font-weight:600;">${Bass.escapeHtml(err.hint)}</div>` : ""}
            </div>`;
        }
        throw err;
      }
    }, "본인인증 가입 및 이동 중... ⏳");
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

  // Rent Now 상대방 호출 모달
  $("btnCloseRentNowModal").addEventListener("click", () => $("rentNowModal").classList.remove("is-open"));
  $("formRentNow").addEventListener("submit", (e) => {
    e.preventDefault();
    const u = getSession();
    Bass.withLock($("btnSubmitRentNow"), async () => {
      const res = await Bass.api(APP_ID, "/pool/cosign-open", {
        method: "POST",
        body: {
          caller_role: $("rnCallerRole").value || "tenant",
          branch: $("rnBranch").value,
          room_no: $("rnRoomNo").value,
          tenant_name: $("rnTenantName").value,
          tenant_phone: $("rnTenantPhone").value,
          id_tenant: u && (u.tenant_id || (u.role !== "lessor" ? u.id : "")) ? (u.tenant_id || u.id) : "",
        },
      });
      $("rentNowModal").classList.remove("is-open");
      Bass.updateAppAlert("success", res.message);
      window.location.href = res.redirect_url;
    }, "상대방 호출 발송 중... ⏳");
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

  // 이미 로그인되어 있으면 포털 첫 화면 대신 내 업무 화면(임대인=내 건물, 임차인=내 집, 관리인=관리인 앱)으로 바로 들어간다.
  // 다른 건물·계정으로 가려면 로그아웃 후 그 아이디로 다시 로그인한다. 포털 홈을 일부러 보려면 ?home=1.
  (function autoEnter() {
    const u = getSession();
    const tok = Bass.token();
    if (!u || !u.redirect_url || !tok || urlParams.get("login") === "1" || urlParams.get("logout") === "1" || urlParams.get("home") === "1") return;
    if (!["lessor", "tenant", "admin", "superadmin"].includes(u.role)) return;
    try {
      const p = JSON.parse(atob(tok.split(".")[0].replace(/-/g, "+").replace(/_/g, "/")));
      if (!(p.exp * 1000 > Date.now() + 30000)) return;       // 만료된 로그인은 들어가지 않고 포털에서 다시 로그인
    } catch (_) { return; }
    window.location.replace(u.redirect_url);
  })();

  renderSessionUI();
  bindPasswordReset();
  bindConnect();
  loadPortalData()
    .then(() => {
      // 공통 안내 링크(/rent3/?home=1&find=1): 내 페이지 찾기(문자 인증)를 바로 연다. 연결 요청 중인 브라우저면 대기 화면으로.
      if (urlParams.get("find") === "1") {
        if (window.__rent3ConnectPending && window.__rent3ConnectPending()) { window.__rent3ConnectShow(); return; }
        openAuthModal({ tab: "login" });
        const lf = $("lnkForgot");
        if (lf) lf.click();
      }
      if (urlParams.get("login") === "1") {
        openAuthModal({ tab: "login" });
        if (urlParams.get("expired") === "1") {
          Bass.updateAppAlert("warning", "로그인이 만료되었습니다. 다시 로그인해 주세요.", "보안을 위해 로그인은 12시간 동안 유지되고, 서버를 다시 시작하면 새로 로그인해야 할 수 있습니다.");
        }
      }
    })
    .catch(Bass.showError);
})();
