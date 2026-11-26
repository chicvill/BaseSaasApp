/* apps/rent3/frontend/app.js — OpenRent 벤치마킹 직거래 포털 & 본인인증 역할별 자동 라우팅 오케스트레이터 */
(function () {
  "use strict";

  const P = window.R3P;
  const {
    APP_ID, state, $, esc, getSession, clearSession,
    navigateToCoSignOrLogin, renderSessionUI, captureAcq,
    openAuthModal, closeAuthModal, renderDemoAccounts, bindPasswordReset, bindAuthEvents,
    renderListings, bindListingEvents, bindConnect,
  } = P;

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
    if (!demoOn) {   // 운영: 성명 로그인·시연 계정 안내 없음(이름만으로는 로그인되지 않는다)
      $("loginIdentifier").placeholder = "아이디, 이메일 또는 휴대폰 번호";
      const lb = document.querySelector('label[for="loginIdentifier"]');
      if (lb) lb.textContent = "아이디 / 휴대폰 번호 / 이메일";
    }
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
  P.loadPortalData = loadPortalData;

  // ── 상단 네비게이션 & 역할별 진입 바인딩 ──
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

  // 모달 바깥 배경 클릭 시 닫기
  ["authModal", "inquiryModal", "rentNowModal"].forEach((mid) => {
    $(mid).addEventListener("click", (e) => {
      if (e.target === $(mid)) $(mid).classList.remove("is-open");
    });
  });

  // 서브 모듈 이벤트 바인딩
  bindAuthEvents();
  bindListingEvents();

  // 초기 진입 처리 (?logout=1 또는 ?login=1 지원)
  const urlParams = new URLSearchParams(window.location.search);
  captureAcq(urlParams);

  if (urlParams.get("logout") === "1") {
    clearSession();
    window.history.replaceState({}, "", window.location.pathname);
    Bass.updateAppAlert("success", "로그아웃되어 OpenRent 직거래 포털 홈으로 이동했습니다.");
  }

  // 이미 로그인되어 있으면 포털 첫 화면 대신 내 업무 화면(임대인=내 건물, 임차인=내 집, 관리인=관리인 앱)으로 바로 들어간다.
  // 다른 건물·계정으로 가려면 로그아웃 후 그 아이디로 다시 로그인한다. 포털 홈을 일부러 보려면 ?home=1, 특정 매물 공유 링크는 ?room=.
  (function autoEnter() {
    const u = getSession();
    const tok = Bass.token();
    if (!u || !u.redirect_url || !tok || urlParams.get("connect") || urlParams.get("login") === "1" || urlParams.get("logout") === "1" || urlParams.get("home") === "1" || urlParams.get("room") || urlParams.get("invite")) return;
    if (!["lessor", "tenant", "admin", "superadmin"].includes(u.role)) return;
    try {
      const p = JSON.parse(atob(tok.split(".")[0].replace(/-/g, "+").replace(/_/g, "/")));
      if (!(p.exp * 1000 > Date.now() + 30000)) return;       // 만료된 로그인은 들어가지 않고 포털에서 다시 로그인
    } catch (_) { return; }
    window.location.replace(u.redirect_url);
  })();

  renderSessionUI();
  bindPasswordReset();

  document.querySelectorAll(".or-bottom-nav__item[data-bnav]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".or-bottom-nav__item").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      const act = btn.dataset.bnav;
      if (act === "listings") {
        const sec = $("listingsSection");
        if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (act === "search") {
        const kw = $("searchKeyword");
        const frm = $("heroSearchForm");
        if (frm) frm.scrollIntoView({ behavior: "smooth", block: "center" });
        if (kw) setTimeout(() => kw.focus(), 200);
      } else if (act === "cosign") {
        const rn = $("rentNowSection");
        if (rn) rn.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (act === "account") {
        const u = getSession();
        if (u && u.redirect_url) window.location.href = u.redirect_url;
        else openAuthModal({ tab: "login" });
      }
    });
  });

  bindConnect();
  // 세입자가 보낸 '안심계약 제안' 링크(?invite=lessor): 임대인 가입 화면을 바로 연다(표식은 captureAcq 가 보관)
  if (urlParams.get("invite") === "lessor") {
    openAuthModal({ tab: "register" });
    $("regRole").value = "lessor";
    $("regRole").dispatchEvent(new Event("change"));
    $("authModalSub").innerHTML = 'Rent3 직접 계약(중개수수료 없음)을 제안받았습니다. 가입 후 가맹 심사를 통과해야 호실을 공개할 수 있습니다. <a href="/rent3/lessor-intro.html' + location.search.replace(/&?invite=lessor/, "").replace(/^\?&/, "?") + '" style="color:inherit;font-weight:700;">월 이용 요금·서비스 내용 먼저 보기 →</a>';
  }
  if (urlParams.get("connect")) window.__rent3ConnectOpen(urlParams.get("connect"));   // 임대인이 보낸 링크: 메뉴 없이 연결 창만
  loadPortalData()
    .then(() => {
      // 공유 홍보 링크(?room=room_xxx&ref=daangn): 특정 매물 카드로 부드럽게 스크롤 & 강조 표시
      const targetRoom = urlParams.get("room");
      if (targetRoom) {
        // 주소창 값은 선택자에 넣지 않고 속성 값과 비교한다(따옴표 등이 들어간 값으로 오류가 나지 않게)
        const cards = [...document.querySelectorAll("[data-room-id]")];
        const card = cards.find((el) => el.dataset.roomId === targetRoom) || cards.find((el) => el.dataset.roomNo === targetRoom);
        if (card) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          card.style.outline = "3px solid var(--accent, #0284c7)";
          card.style.borderRadius = "12px";
          card.style.transition = "outline 0.3s ease";
          const ref = urlParams.get("ref");
          const refLabels = {
            daangn: "🥕 당근마켓",
            peterpan: "☕ 네이버 피터팬",
            everytime: "🎓 에브리타임",
            blog: "📝 네이버 블로그",
            kakao: "💬 카카오톡",
          };
          const refName = refLabels[ref] || (ref ? `[${ref}]` : "");
          if (refName) {
            Bass.updateAppAlert("info", `${refName} 공유 링크를 통해 매물을 열었습니다. 중개수수료 0원 안심 직거래 매물입니다.`);
          }
        }
      }

      // 공통 안내 링크(/rent3/?home=1&find=1): 내 페이지 찾기(문자 인증)를 바로 연다. 연결 요청 중인 브라우저면 대기 화면으로.
      if (urlParams.get("find") === "1") {
        if (window.__rent3ConnectPending && window.__rent3ConnectPending()) { window.__rent3ConnectOpen(""); return; }
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
