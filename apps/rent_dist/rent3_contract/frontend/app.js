/* apps/rent3_contract/frontend/app.js — 임대계약 전용 화면.
 *
 * 한 화면에 임대인/임차인을 오가는 스위치는 없다. 로그인한 역할로만 동작하므로
 * 임대인과 임차인이 각자의 스마트폰에서 같은 계약을 진행한다.
 *   홈  : 내 역할에 맞는 현황 + 임대계약 버튼
 *   계약: 위쪽 진행 차트 + 그 아래 단계별 모달 (내 차례 → 상대 대기 → 다음 단계)
 */
(function () {
  "use strict";

  const APP_ID = "rent3_contract";
  const SESSION_KEY = "rent3_session";
  const params = new URLSearchParams(window.location.search);

  function readLoggedUser() {
    try {
      const raw = window.localStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  const loggedUser = readLoggedUser();
  if (!loggedUser || !loggedUser.name) {
    window.location.replace("/rent3/?login=1");
    return;
  }
  const currentRole = (loggedUser.role === "tenant" || loggedUser.role === "prospect")
    ? "tenant"
    : (loggedUser.role === "cosign" && params.get("role") === "tenant" ? "tenant" : "lessor");
  const myTenantId = loggedUser.tenant_id || loggedUser.id || "";
  const myBranch = loggedUser.branch && loggedUser.branch !== "all" ? loggedUser.branch : "";
  const ROLE_KO = { lessor: "임대인", tenant: "임차인" };
  const OTHER_KO = currentRole === "lessor" ? "임차인" : "임대인";

  let candidateRooms = [];
  let participantTenants = [];
  let allSessions = [];
  let activeSessionId = "";
  let activeSession = null;
  let sealedContract = null;
  let viewStep = 1;
  let lastRevision = null;
  let lastCallStatus = null;
  let isEditingForm = false;
  let myhome = null;          // 임차인: 기존 입주 호실 납부 현황
  let lessorDash = null;      // 임대인: 호실별 미납 현황
  let homeSig = "";
  let modalKey = "";
  let modalAnimating = false;
  let modalTimer = null;

  const elHome = document.getElementById("homeView");
  const elHomeBody = document.getElementById("homeBody");
  const elContract = document.getElementById("contractView");
  const elModal = document.getElementById("stepModal");
  const elCard = document.getElementById("stepCard");
  const elStepPanel = document.getElementById("stepPanel");
  const elSealedView = document.getElementById("sealedContractView");
  const elDlgHost = document.getElementById("dlgHost");

  const esc = Bass.escapeHtml;
  const digits = (v) => String(v || "").replace(/\D/g, "");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  document.getElementById("whoBadge").textContent =
    `🛡️ ${loggedUser.name} · ${currentRole === "lessor" ? "임대인" : "임차인"}`;

  // ───────────────────────── 데이터 ─────────────────────────
  async function loadCandidateRooms() {
    const [roomData, partData] = await Promise.all([
      Bass.api(APP_ID, "/rooms"),
      Bass.api(APP_ID, "/participants"),
    ]);
    candidateRooms = roomData.rooms || [];
    participantTenants = partData.tenants || [];
  }

  async function loadRoleExtras() {
    try {
      if (currentRole === "tenant" && loggedUser.role === "tenant" && myTenantId) {
        const d = await Bass.api("rent3_tenant", `/myhome/${encodeURIComponent(myTenantId)}`);
        myhome = d && d.room ? d : null;
      } else if (currentRole === "lessor") {
        const q = myBranch ? `?branch=${encodeURIComponent(myBranch)}` : "";
        lessorDash = await Bass.api("rent3_lessor", `/dashboard${q}`);
      }
    } catch (_) {
      myhome = null;
      lessorDash = null;
    }
  }

  function isMine(s) {
    if (currentRole === "lessor") return !myBranch || s.branch === myBranch;
    const terms = s.terms || {};
    const up = digits(loggedUser.phone);
    return Boolean(
      (s.id_tenant && s.id_tenant === myTenantId) ||
      (up.length >= 8 && digits(terms.tenant_phone) === up) ||
      (terms.tenant_name && terms.tenant_name === loggedUser.name)
    );
  }

  const mySessions = () => allSessions.filter(isMine);
  const incomingCalls = () =>
    mySessions().filter((s) => s.status === "IN_PROGRESS" && s.call_status === "CALLING" && s.initiated_by_role !== currentRole);
  const ongoingSessions = () =>
    mySessions().filter((s) => s.status === "IN_PROGRESS" && !(s.call_status === "CALLING" && s.initiated_by_role !== currentRole));

  async function loadSessionList() {
    const data = await Bass.api(APP_ID, "/sessions");
    allSessions = data.sessions || [];
    if (activeSessionId && !elContract.hidden) {
      await fetchActiveSession(true);
    } else {
      renderHome(true);
    }
  }

  // ───────────────────────── 홈 ─────────────────────────
  function sessionTag(s) {
    if (s.call_status === "CALLING") {
      return s.initiated_by_role === currentRole ? `⏳ ${OTHER_KO} 응답 대기` : "🔔 응답 필요";
    }
    return `단계 ${s.current_step}/5`;
  }

  function renderHome(force) {
    if (!elContract.hidden) return;
    const sig = JSON.stringify([
      mySessions().map((s) => [s.id, s.call_status, s.current_step, s.status]),
      candidateRooms.map((r) => `${r.room_no}${r.is_occupied ? 1 : 0}${r.is_published ? 1 : 0}`).join(), myhome ? myhome.pool_revision : 0, lessorDash ? lessorDash.pool_revision : 0,
    ]);
    if (!force && sig === homeSig) return;
    if (elDlgHost.firstChild) return; // 입력 중인 창을 지우지 않는다
    homeSig = sig;

    const incoming = incomingCalls();
    const ongoing = ongoingSessions();
    let html = "";

    if (incoming.length) {
      html += `<div class="call-banner">
        <div style="font-weight:800;margin-bottom:8px;">🔔 ${ROLE_KO[currentRole]}님께 계약 요청이 왔습니다</div>
        ${incoming.map((c) => `
          <div class="bass-row" style="justify-content:space-between;margin-top:6px;">
            <div style="font-size:13px;"><strong>${esc(c.building_name)} ${esc(c.room_no)}호</strong><br>
              <span class="bass-muted">${c.initiated_by_role === "lessor" ? `임대인 ${esc(c.lessor_name)}` : `임차인 ${esc((c.terms && c.terms.tenant_name) || "예비임차인")}`}</span></div>
            <button class="bass-btn bass-btn--primary js-respond" data-sid="${esc(c.id)}" type="button">✅ 응답하고 시작</button>
          </div>`).join("")}
      </div>`;
    }

    if (ongoing.length) {
      html += `<div class="bass-card" style="margin-bottom:14px;">
        <h3 style="margin:0 0 8px;">진행 중인 계약</h3>
        ${ongoing.map((s) => `
          <div class="room-row">
            <div class="room-row__main">
              <div class="room-row__title">${esc(s.building_name)} ${esc(s.room_no)}호</div>
              <div class="room-row__meta bass-muted">${sessionTag(s)} · ${esc((s.terms && s.terms.tenant_name) || "임차인 응답 대기")}</div>
            </div>
            <button class="bass-btn bass-btn--primary bass-btn--sm js-open" data-sid="${esc(s.id)}" type="button">이어서 진행 →</button>
          </div>`).join("")}
      </div>`;
    }

    html += currentRole === "lessor" ? homeLessorHtml() : homeTenantHtml();
    elHomeBody.innerHTML = html;

    elHomeBody.querySelectorAll(".js-respond").forEach((b) =>
      b.addEventListener("click", function () { respondToIncomingCall(this, this.dataset.sid); }));
    elHomeBody.querySelectorAll(".js-open").forEach((b) =>
      b.addEventListener("click", () => openSession(b.dataset.sid)));
    elHomeBody.querySelectorAll(".js-lessor-call").forEach((b) =>
      b.addEventListener("click", () => openLessorCallDialog(b.dataset.room)));
    const tBtn = document.getElementById("btnTenantContract");
    if (tBtn) tBtn.addEventListener("click", () => openTenantPickerDialog());
  }

  function homeLessorHtml() {
    const rooms = candidateRooms.filter((r) => !myBranch || r.branch === myBranch);
    const unpaidByRoom = {};
    if (lessorDash && Array.isArray(lessorDash.rooms)) {
      lessorDash.rooms.forEach((r) => { unpaidByRoom[r.room_no] = r.ledger ? Number(r.ledger.total_unpaid || 0) : 0; });
    }
    const bname = rooms[0] ? rooms[0].building_name : "내 건물";
    const occupied = rooms.filter((r) => r.is_occupied).length;
    return `<div class="bass-card">
      <h2 style="margin:0 0 4px;font-size:19px;">🏢 ${esc(bname)} 호실 현황</h2>
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:12px;">전체 ${rooms.length}실 · 입주 ${occupied}실 · 공실 ${rooms.length - occupied}실</div>
      ${rooms.map((r) => {
        const unpaid = unpaidByRoom[r.room_no] || 0;
        const stateBadge = r.is_occupied
          ? `<span class="bass-badge bass-badge--ok">입주중</span>`
          : `<span class="bass-badge ${r.is_published ? "bass-badge--info" : "bass-badge--warn"}">${r.is_published ? "매물 공개중" : "공실"}</span>`;
        const unpaidBadge = r.is_occupied && unpaid > 0 ? ` <span class="bass-badge bass-badge--err">미납 ${fmtWon(unpaid)}</span>` : "";
        return `<div class="room-row">
          <div class="room-row__main">
            <div class="room-row__title">${esc(r.room_no)}호 ${stateBadge}${unpaidBadge}</div>
            <div class="room-row__meta bass-muted">${r.is_occupied ? `임차인 ${esc(r.active_tenant_name || "-")} · ` : ""}보증금 ${fmtWon(r.deposit)} · 월 ${fmtWon(r.monthly_rent)}</div>
          </div>
          <button class="bass-btn bass-btn--primary bass-btn--sm js-lessor-call" data-room="${esc(r.branch)}|${esc(r.room_no)}" type="button">${r.is_occupied ? "재계약" : "임대계약"}</button>
        </div>`;
      }).join("") || `<div class="bass-muted">등록된 호실이 없습니다.</div>`}
    </div>`;
  }

  function homeTenantHtml() {
    let pay = "";
    if (myhome) {
      const lg = myhome.ledger || { months: [], total_unpaid: 0 };
      const r = myhome.room || {};
      pay = `<div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;margin-bottom:8px;">
          <h3 style="margin:0;">🏠 ${esc((myhome.building && myhome.building.building_name) || "")} ${esc(r.room_no || "")}호 납부 현황</h3>
          <span class="bass-badge ${lg.total_unpaid > 0 ? "bass-badge--err" : "bass-badge--ok"}">${lg.total_unpaid > 0 ? `미납 ${fmtWon(lg.total_unpaid)}` : "완납"}</span>
        </div>
        ${(lg.months || []).slice(-6).map((m) => `
          <div class="pay-row"><span><strong>${esc(m.ym)}</strong> · ${fmtWon(m.due)}</span>
          <span style="color:${m.unpaid > 0 ? "var(--err)" : "var(--ok)"};font-weight:700;">${esc(m.note)}</span></div>`).join("")}
      </div>`;
    }
    const incoming = incomingCalls();
    const ongoing = ongoingSessions();
    const label = incoming.length ? "🔔 임대인 요청 확인하기"
      : ongoing.length ? "▶ 진행 중인 계약 이어서 하기"
      : "📝 임대계약";
    return `${pay}
      <div class="bass-card">
        <h3 style="margin:0 0 6px;">새 집 계약</h3>
        <div class="bass-muted" style="font-size:12.5px;margin-bottom:12px;">공개된 매물을 고르면 임대인에게 계약 요청이 전달됩니다.</div>
        <button id="btnTenantContract" class="bass-btn bass-btn--primary big-cta" type="button">${label}</button>
      </div>`;
  }

  // ───────────────────────── 다이얼로그 ─────────────────────────
  function openDialog(innerHtml) {
    elDlgHost.innerHTML = `<div class="dlg-backdrop"><div class="dlg">${innerHtml}</div></div>`;
    const bd = elDlgHost.firstChild;
    bd.addEventListener("click", (e) => { if (e.target === bd) closeDialog(); });
    return bd;
  }
  function closeDialog() {
    elDlgHost.innerHTML = "";
    renderHome(true);
  }

  function findRoom(val) {
    const [br, no] = String(val).split("|");
    return candidateRooms.find((x) => x.branch === br && x.room_no === no);
  }

  function openLessorCallDialog(val) {
    const room = findRoom(val);
    if (!room) return;
    const renewal = room.is_occupied;
    const prospects = participantTenants.filter((t) => t.status === "PROSPECT");
    const bd = openDialog(`
      <h3 style="margin:0 0 4px;">${esc(room.room_no)}호 ${renewal ? "재계약" : "임대계약"}</h3>
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:12px;">보증금 ${fmtWon(room.deposit)} · 월 ${fmtWon(room.monthly_rent)}</div>
      ${renewal ? "" : `
        <label class="bass-label">계약할 임차인 선택</label>
        <select id="dlgTenantSel" class="bass-select">
          <option value="">-- 직접 입력 --</option>
          ${prospects.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.phone)})</option>`).join("")}
        </select>`}
      <label class="bass-label" style="margin-top:8px;">임차인 성명</label>
      <input id="dlgName" class="bass-input" value="${renewal ? esc(room.active_tenant_name || "") : ""}" ${renewal ? "readonly" : ""}>
      <label class="bass-label" style="margin-top:8px;">임차인 휴대폰</label>
      <input id="dlgPhone" class="bass-input" value="${renewal ? esc(room.active_tenant_phone || "") : ""}" ${renewal ? "readonly" : ""} placeholder="010-0000-0000">
      <div class="bass-row" style="margin-top:14px;gap:8px;">
        <button id="dlgCancel" class="bass-btn" style="flex:1;" type="button">취소</button>
        <button id="dlgGo" class="bass-btn bass-btn--primary" style="flex:2;" type="button">📞 임차인에게 계약 요청</button>
      </div>`);
    const sel = bd.querySelector("#dlgTenantSel");
    if (sel) sel.addEventListener("change", () => {
      const t = participantTenants.find((x) => x.id === sel.value);
      bd.querySelector("#dlgName").value = t ? t.name : "";
      bd.querySelector("#dlgPhone").value = t ? t.phone : "";
    });
    bd.querySelector("#dlgCancel").addEventListener("click", closeDialog);
    bd.querySelector("#dlgGo").addEventListener("click", function () {
      const name = bd.querySelector("#dlgName").value.trim();
      const phone = bd.querySelector("#dlgPhone").value.trim();
      const picked = sel && sel.value ? sel.value : (renewal && room.active_tenant_id ? room.active_tenant_id : "");
      if (!name || !phone) { Bass.updateAppAlert("warning", "임차인 성명과 휴대폰을 입력해 주세요."); return; }
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/sessions/call", {
          method: "POST",
          body: { caller_role: "lessor", branch: room.branch, room_no: room.room_no,
                  mode: renewal ? "RENEWAL" : "NEW", tenant_name: name, tenant_phone: phone, id_tenant: picked },
        });
        elDlgHost.innerHTML = "";
        await afterCall(res);
      }, "요청 보내는 중... ⏳");
    });
  }

  function openTenantPickerDialog() {
    const incoming = incomingCalls();
    if (incoming.length) { openSession(incoming[0].id); return; }
    const ongoing = ongoingSessions();
    if (ongoing.length) { openSession(ongoing[0].id); return; }
    const listings = candidateRooms.filter((r) => !r.is_occupied && r.is_published);
    const bd = openDialog(`
      <h3 style="margin:0 0 4px;">계약할 집 고르기</h3>
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:10px;">임대인이 공개한 빈 집입니다.</div>
      ${listings.length ? listings.map((r) => `
        <div class="room-row">
          <div class="room-row__main">
            <div class="room-row__title">${esc(r.building_name)} ${esc(r.room_no)}호</div>
            <div class="room-row__meta bass-muted">보증금 ${fmtWon(r.deposit)} · 월 ${fmtWon(r.monthly_rent)} · 관리비 ${fmtWon(r.maintenance_fee)}</div>
          </div>
          <button class="bass-btn bass-btn--primary bass-btn--sm js-pick" data-room="${esc(r.branch)}|${esc(r.room_no)}" type="button">이 집으로</button>
        </div>`).join("") : `<div class="bass-muted" style="padding:10px 0;">지금 공개된 매물이 없습니다.</div>`}
      <button id="dlgCancel" class="bass-btn" style="width:100%;margin-top:8px;" type="button">닫기</button>`);
    bd.querySelector("#dlgCancel").addEventListener("click", closeDialog);
    bd.querySelectorAll(".js-pick").forEach((b) => b.addEventListener("click", function () {
      const room = findRoom(this.dataset.room);
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/sessions/call", {
          method: "POST",
          body: { caller_role: "tenant", branch: room.branch, room_no: room.room_no, mode: "NEW",
                  tenant_name: loggedUser.name || "", tenant_phone: loggedUser.phone || "", id_tenant: myTenantId },
        });
        elDlgHost.innerHTML = "";
        await afterCall(res);
      }, "요청 보내는 중... ⏳");
    }));
  }

  async function afterCall(res) {
    Bass.updateAppAlert("success", res.message);
    const list = await Bass.api(APP_ID, "/sessions");
    allSessions = list.sessions || [];
    await openSession(res.session.id);
  }

  function respondToIncomingCall(btnEl, sessionId) {
    Bass.withLock(btnEl, async () => {
      const res = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(sessionId)}/respond`, {
        method: "POST",
        body: {
          responder_role: currentRole,
          responder_name: loggedUser.name || "",
          responder_phone: loggedUser.phone || "",
          id_tenant: currentRole === "tenant" ? myTenantId : "",
        },
      });
      Bass.updateAppAlert("success", res.message);
      await openSession(res.session.id);
    }, "응답 연결 중... ⏳");
  }

  // ───────────────────────── 계약 화면 ─────────────────────────
  async function openSession(id) {
    activeSessionId = id;
    activeSession = null;
    lastRevision = null;
    lastCallStatus = null;
    modalKey = "";
    modalAnimating = false;
    clearTimeout(modalTimer);
    elCard.classList.remove("is-open", "is-closing");
    elHome.hidden = true;
    elContract.hidden = false;
    window.scrollTo(0, 0);
    await fetchActiveSession(true);
  }

  function goHome() {
    activeSessionId = "";
    activeSession = null;
    modalKey = "";
    clearTimeout(modalTimer);
    elModal.hidden = true;
    elContract.hidden = true;
    elHome.hidden = false;
    loadRoleExtras().then(() => loadSessionList()).catch(Bass.showError);
  }
  document.getElementById("btnBackHome").addEventListener("click", goHome);

  async function fetchActiveSession(forceRender = false) {
    if (!activeSessionId) return;
    const res = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(activeSessionId)}`);
    const prevStep = activeSession ? activeSession.current_step : 0;
    const prevStatus = activeSession ? activeSession.status : "";
    activeSession = res.session;
    sealedContract = res.contract;

    const idx = allSessions.findIndex((x) => x.id === activeSession.id);
    if (idx >= 0) allSessions[idx] = activeSession;

    if (forceRender || activeSession.current_step !== prevStep) viewStep = activeSession.current_step;

    if (
      forceRender ||
      lastRevision !== activeSession.revision ||
      lastCallStatus !== activeSession.call_status ||
      prevStep !== activeSession.current_step ||
      prevStatus !== activeSession.status ||
      !isEditingForm
    ) {
      lastRevision = activeSession.revision;
      lastCallStatus = activeSession.call_status;
      renderWorkspace();
    }
  }

  // 단계별로 "내 차례"인지, 이미 마쳐서 상대를 기다려야 하는지 판단한다.
  function stepProgress(s, step) {
    switch (step) {
      case 1: return {
        lessor: s.lessor_checked_tenant_id && s.step1_lessor_confirmed,
        tenant: s.tenant_checked_lessor_id && s.step1_tenant_locked,
        label: "신분증 확인 · 등기부 확인",
      };
      case 2: return { lessor: s.step2_lessor_confirmed, tenant: s.step2_tenant_locked, label: "옵션·시설 점검" };
      case 3: return { lessor: s.step3_lessor_agreed, tenant: s.step3_tenant_agreed, label: "계약 조건 동의" };
      case 4: return { lessor: !!s.lessor_signature, tenant: !!s.tenant_signature, label: "전자서명" };
      default: return { lessor: s.status === "COMPLETED", tenant: s.status === "COMPLETED", label: "계약 체결" };
    }
  }

  function blockedMessage(s, step) {
    if (currentRole !== "tenant") return "";
    if (step === 1 && !s.step1_lessor_confirmed) return "임대인이 신분증 확인과 등기부등본 제시를 마치면 이 창이 열립니다.";
    if (step === 2 && !s.step2_lessor_confirmed) return "임대인이 옵션·시설 점검표를 작성하면 이 창이 열립니다.";
    if (step === 5) return "임대인이 계약금 입금을 확인하고 체결하면 계약이 완료됩니다.";
    return "";
  }

  function planModal(s) {
    if (s.call_status === "CALLING") {
      const callee = currentRole !== s.initiated_by_role;
      return { key: `${s.id}:call:${callee ? "respond" : "wait"}`, render: () => renderCallHandshake(s) };
    }
    const step = viewStep;
    if (step === s.current_step) {
      const prog = stepProgress(s, step);
      const mine = prog[currentRole];
      const blocked = blockedMessage(s, step);
      if (mine || blocked) {
        return { key: `${s.id}:s${step}:wait`, render: () => renderWaiting(s, step, prog, mine, blocked) };
      }
    }
    return { key: `${s.id}:s${step}:act`, render: () => renderStepN(s, step) };
  }

  function renderStepN(s, step) {
    if (step === 1) renderStep1(s);
    else if (step === 2) renderStep2(s);
    else if (step === 3) renderStep3(s);
    else if (step === 4) renderStep4(s);
    else renderStep5(s);
  }

  function renderWaiting(s, step, prog, mine, blocked) {
    const row = (role, done) => `
      <div class="wait-row">
        <span>${role === "lessor" ? "🏢 임대인" : "🔑 임차인"}${role === currentRole ? " (나)" : ""}</span>
        ${done ? `<span class="bass-badge bass-badge--ok">완료 ✓</span>` : `<span class="bass-badge bass-badge--warn">진행 중<span class="wait-dots"></span></span>`}
      </div>`;
    elStepPanel.innerHTML = `
      <h3 style="margin:0 0 6px;">${mine ? "✅ 내 차례를 마쳤습니다" : `⏳ ${OTHER_KO}의 차례입니다`}</h3>
      <p class="bass-muted" style="font-size:13px;margin:0 0 6px;">
        ${esc(blocked || `${OTHER_KO}이 [${prog.label}]을 마치면 자동으로 다음 단계가 열립니다. 이 화면을 닫지 말고 기다려 주세요.`)}
      </p>
      ${row("lessor", prog.lessor)}${row("tenant", prog.tenant)}`;
  }

  function positionModal() {
    const head = document.getElementById("cvHead");
    elModal.style.top = `${Math.round(head.getBoundingClientRect().bottom)}px`;
  }
  window.addEventListener("resize", positionModal);
  window.addEventListener("scroll", positionModal, { passive: true });
  // 알림 배너가 생기고 사라질 때도 모달이 진행 차트 바로 아래에 붙어 있게 한다.
  if (window.ResizeObserver) new ResizeObserver(positionModal).observe(document.body);

  function openCardAnim() {
    elCard.classList.remove("is-closing", "is-open");
    void elCard.offsetWidth;
    elCard.classList.add("is-open");
  }

  function switchModal(s) {
    if (modalAnimating) return;
    const plan = planModal(s);
    elModal.hidden = false;
    positionModal();
    if (plan.key === modalKey) { plan.render(); return; }
    const first = modalKey === "";
    modalKey = plan.key;
    if (first) { plan.render(); openCardAnim(); return; }
    // 기존 창이 닫히고, 다음 창이 열린다
    modalAnimating = true;
    elCard.classList.remove("is-open");
    elCard.classList.add("is-closing");
    clearTimeout(modalTimer);
    modalTimer = setTimeout(() => {
      modalAnimating = false;
      if (!activeSession) return;
      planModal(activeSession).render();
      openCardAnim();
      elModal.scrollTop = 0;
    }, 210);
  }

  function renderWorkspace() {
    if (!activeSession) return;
    elHome.hidden = true;
    elContract.hidden = false;
    const s = activeSession;
    document.getElementById("wsTitle").textContent = `${s.building_name} ${s.room_no}호`;
    document.getElementById("wsMeta").textContent =
      `${s.mode === "RENEWAL" ? "재계약" : "신규 계약"} · 나는 ${ROLE_KO[currentRole]}`;

    const callBadge = document.getElementById("callStateBadge");
    if (s.status === "COMPLETED") {
      callBadge.className = "bass-badge bass-badge--ok";
      callBadge.textContent = "체결 완료";
    } else if (s.call_status === "CALLING") {
      callBadge.className = "bass-badge bass-badge--warn";
      callBadge.textContent = "응답 대기";
    } else {
      callBadge.className = "bass-badge bass-badge--ok";
      callBadge.textContent = "연결됨";
    }

    document.querySelectorAll(".step-pill").forEach((pill) => {
      const n = Number(pill.dataset.step);
      const connected = s.call_status !== "CALLING";
      pill.classList.toggle("is-now", connected && s.status !== "COMPLETED" && n === s.current_step);
      pill.classList.toggle("is-done", connected && (n < s.current_step || s.status === "COMPLETED"));
    });

    if (s.status === "COMPLETED") {
      elModal.hidden = true;
      modalKey = "";
      renderSealedPaper();
      return;
    }
    switchModal(s);
    renderSealedPaper();
  }

  function renderCallHandshake(s) {
    const callerIsLessor = s.initiated_by_role === "lessor";
    const callerKo = callerIsLessor ? `임대인(${s.lessor_name})` : `임차인(${(s.terms && s.terms.tenant_name) || "예비임차인"})`;
    const calleeKo = callerIsLessor ? `임차인(${(s.terms && s.terms.tenant_name) || "예비임차인"})` : `임대인(${s.lessor_name})`;
    const isCalleeView = currentRole !== s.initiated_by_role;

    elStepPanel.innerHTML = `
      <h3>📞 매물 선택 호출 · 응답 연결 대기실</h3>
      <p class="bass-muted" style="font-size:13px;">
        공동계약은 임대인과 임차인이 각자 본인인증 로그인 후, 먼저 입장한 당사자의 <strong>호출(Call)</strong>에 상대방이 <strong>응답(Respond)</strong>함으로써 시작됩니다.
      </p>

      <div class="id-verify-grid">
        <div class="id-card-box ${s.lessor_login_verified ? "is-verified" : ""}">
          <div style="font-weight:800; margin-bottom:4px;">🏢 임대인(갑): ${Bass.escapeHtml(s.lessor_name)}</div>
          <div class="bass-muted" style="font-size:12px;">연락처: ${Bass.escapeHtml(s.lessor_phone || "-")}</div>
          <div style="margin-top:8px;">
            <span class="bass-badge ${s.lessor_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.lessor_login_verified ? "🛡️ 로그인 본인인증 완료 (호출자)" : "⏳ 로그인 호출 응답 대기"}
            </span>
          </div>
        </div>

        <div class="id-card-box ${s.tenant_login_verified ? "is-verified" : ""}">
          <div style="font-weight:800; margin-bottom:4px;">🔑 임차인(을): ${Bass.escapeHtml((s.terms && s.terms.tenant_name) || "응답 시 자동입력")}</div>
          <div class="bass-muted" style="font-size:12px;">연락처: ${Bass.escapeHtml((s.terms && s.terms.tenant_phone) || "-")}</div>
          <div style="margin-top:8px;">
            <span class="bass-badge ${s.tenant_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.tenant_login_verified ? "🛡️ 로그인 본인인증 완료 (호출자)" : "⏳ 로그인 호출 응답 대기"}
            </span>
          </div>
        </div>
      </div>

      ${isCalleeView ? `
        <div class="call-banner" style="margin-top:12px; margin-bottom:0;">
          <div style="font-weight:800; margin-bottom:6px;">
            🔔 ${Bass.escapeHtml(callerKo)}님이 [${Bass.escapeHtml(s.building_name)} ${Bass.escapeHtml(s.room_no)}호] 공동계약을 호출했습니다!
          </div>
          <div class="bass-muted" style="font-size:12.5px; margin-bottom:10px;">
            아래 응답 버튼을 누르면 본인인증 계정이 즉시 연결되며, ① 상호 신분증 확인 및 등기부등본 열람 단계가 시작됩니다.
          </div>
          <button id="btnRespondMain" type="button" class="bass-btn bass-btn--primary" style="width:100%;">
            ✅ 호출에 응답하고 공동계약 시작하기 (${currentRole === "lessor" ? "임대인" : "임차인"} 본인인증 연결)
          </button>
        </div>
      ` : `
        <div class="bass-card" style="padding:14px; margin-top:12px; border:1px dashed var(--border-strong);">
          <div style="font-weight:800; margin-bottom:4px;">📡 ${Bass.escapeHtml(calleeKo)}님의 호출 응답을 기다리고 있습니다...</div>
          <div class="bass-muted" style="font-size:12.5px;">
            상대방이 로그인 후 <strong>[호출 응답 및 공동계약 시작]</strong>을 클릭하면 자동으로 ① 신분증 확인 및 등기부등본 열람 화면으로 전환됩니다.
          </div>
        </div>
      `}
    `;

    const btnResp = document.getElementById("btnRespondMain");
    if (btnResp) {
      btnResp.addEventListener("click", function () {
        respondToIncomingCall(this, s.id);
      });
    }
  }

  function renderStep1(s) {
    const r = s.registry_data || {};
    const step1AllDone =
      s.lessor_login_verified &&
      s.tenant_login_verified &&
      s.lessor_checked_tenant_id &&
      s.tenant_checked_lessor_id &&
      s.step1_lessor_confirmed &&
      s.step1_tenant_locked;

    elStepPanel.innerHTML = `
      <h3>① 로그인 본인인증 · 상호 신분증 확인 체크 & 등기부등본 확인</h3>
      <p class="bass-muted" style="font-size:13px;">
        임대인과 임차인은 로그인 본인인증을 완료했으며, <strong>서로 상대방의 신분증을 확인하여 본인 확인 체크</strong>를 완료하고 <strong>등기부등본</strong>을 확인해야 서명 자격이 부여됩니다.
      </p>

      <!-- 1-A. 상호 신분증 확인 체크 영역 -->
      <div style="margin-top:12px; font-weight:800; font-size:14px;">🪪 1-A. 당사자 로그인 본인인증 및 상호 신분증 대조 확인 체크</div>
      <div class="id-verify-grid">
        <!-- 임대인 신분 카드 (임차인이 확인 체크) -->
        <div class="id-card-box ${s.tenant_checked_lessor_id ? "is-verified" : ""}">
          <div class="bass-row" style="justify-content:space-between; margin-bottom:6px;">
            <strong>🏢 임대인(갑) 신분 정보</strong>
            <span class="bass-badge ${s.lessor_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.lessor_login_verified ? "🛡️ 로그인 본인인증 완료" : "미인증"}
            </span>
          </div>
          <div style="font-size:13px; margin-bottom:8px;">
            성명: <strong>${Bass.escapeHtml(s.lessor_name)}</strong> · 연락처: ${Bass.escapeHtml(s.lessor_phone || "-")}
            <div class="bass-muted" style="font-size:11.5px;">등기부 소유자 대조: ${Bass.escapeHtml(r.owner_name || s.lessor_name)} (일치)</div>
          </div>
          <label class="check-row" style="margin-bottom:0; cursor:${currentRole === "tenant" ? "pointer" : "default"};">
            <span style="font-size:12.5px;">
              <strong>[임차인이 체크]</strong> 임대인의 신분증을 확인하여 본인임을 확인함
            </span>
            <input type="checkbox" id="chkTenantVerifiedLessorId"
              ${s.tenant_checked_lessor_id ? "checked" : ""}
              ${currentRole !== "tenant" ? "disabled" : ""}>
          </label>
        </div>

        <!-- 임차인 신분 카드 (임대인이 확인 체크) -->
        <div class="id-card-box ${s.lessor_checked_tenant_id ? "is-verified" : ""}">
          <div class="bass-row" style="justify-content:space-between; margin-bottom:6px;">
            <strong>🔑 임차인(을) 신분 정보</strong>
            <span class="bass-badge ${s.tenant_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.tenant_login_verified ? "🛡️ 로그인 본인인증 완료" : "미인증"}
            </span>
          </div>
          <div style="font-size:13px; margin-bottom:8px;">
            성명: <strong>${Bass.escapeHtml((s.terms && s.terms.tenant_name) || "-")}</strong> · 연락처: ${Bass.escapeHtml((s.terms && s.terms.tenant_phone) || "-")}
            <div class="bass-muted" style="font-size:11.5px;">로그인 본인인증 휴대폰 명의 일치 확인됨</div>
          </div>
          <label class="check-row" style="margin-bottom:0; cursor:${currentRole === "lessor" ? "pointer" : "default"};">
            <span style="font-size:12.5px;">
              <strong>[임대인이 체크]</strong> 임차인의 신분증을 확인하여 본인임을 확인함
            </span>
            <input type="checkbox" id="chkLessorVerifiedTenantId"
              ${s.lessor_checked_tenant_id ? "checked" : ""}
              ${currentRole !== "lessor" ? "disabled" : ""}>
          </label>
        </div>
      </div>

      <!-- 1-B. 대법원 등기부등본 권리관계 확인 영역 -->
      <div style="margin-top:14px; font-weight:800; font-size:14px;">🏛️ 1-B. 대법원 등기부등본 권리관계 확인</div>
      <div class="field-grid" style="margin:10px 0;">
        <div>
          <label class="bass-label">등기부 발급확인번호</label>
          <input id="regIssue" class="bass-input" value="${Bass.escapeHtml(r.issue_number || "")}" ${currentRole !== "lessor" ? "readonly" : ""}>
        </div>
        <div>
          <label class="bass-label">갑구 소유자 성명</label>
          <input id="regOwner" class="bass-input" value="${Bass.escapeHtml(r.owner_name || "")}" ${currentRole !== "lessor" ? "readonly" : ""}>
        </div>
        <div>
          <label class="bass-label">을구 선순위 채권최고액(원)</label>
          <input id="regMortgage" type="number" class="bass-input" value="${Number(r.mortgage_amount || 0)}" ${currentRole !== "lessor" ? "readonly" : ""}>
        </div>
      </div>
      <div class="bass-muted" style="font-size:12px; margin-bottom:10px;">
        압류·가압류·신탁등기 여부: <strong>${Bass.escapeHtml(r.seizure_or_trust || "없음")}</strong>
      </div>

      <div class="bass-row" style="margin-bottom:12px; flex-wrap:wrap; gap:6px;">
        <span class="bass-badge ${s.lessor_checked_tenant_id ? "bass-badge--ok" : "bass-badge--warn"}">
          임대인→임차인 신분증 확인: ${s.lessor_checked_tenant_id ? "완료 ✓" : "미체크"}
        </span>
        <span class="bass-badge ${s.tenant_checked_lessor_id ? "bass-badge--ok" : "bass-badge--warn"}">
          임차인→임대인 신분증 확인: ${s.tenant_checked_lessor_id ? "완료 ✓" : "미체크"}
        </span>
        <span class="bass-badge ${s.step1_lessor_confirmed ? "bass-badge--ok" : "bass-badge--warn"}">
          임대인 등기부 제시: ${s.step1_lessor_confirmed ? "완료 ✓" : "대기"}
        </span>
        <span class="bass-badge ${s.step1_tenant_locked ? "bass-badge--ok" : "bass-badge--warn"}">
          임차인 등기부 확인 잠금: ${s.step1_tenant_locked ? "완료 ✓" : "미확인"}
        </span>
      </div>

      ${step1AllDone ? `
        <div class="bass-card" style="padding:10px 12px; margin-bottom:10px; border-color:var(--ok); font-size:12.5px;">
          ✅ 양측 로그인 본인인증 + 서로 신분증 확인 체크 + 등기부등본 권리 확인이 모두 완료되었습니다!
        </div>
      ` : ""}

      <button id="btnStep1Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ${currentRole === "lessor"
          ? "🏢 임차인 신분증 확인 체크 & 등기부등본 제시 저장"
          : "🔑 임대인 신분증 확인 체크 & 등기부등본 이상 없음 확인 잠금"}
      </button>
    `;

    // 신분증 체크박스 즉시 반영 바인딩
    const chkTenant = document.getElementById("chkTenantVerifiedLessorId");
    if (chkTenant && currentRole === "tenant") {
      chkTenant.addEventListener("change", async () => {
        try {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/verify-id`, {
            method: "POST",
            body: { role: "tenant", checked: chkTenant.checked },
          });
          activeSession = res.session;
          viewStep = activeSession.current_step;
          Bass.updateAppAlert("success", res.message);
          renderWorkspace();
        } catch (err) {
          Bass.showError(err);
        }
      });
    }

    const chkLessor = document.getElementById("chkLessorVerifiedTenantId");
    if (chkLessor && currentRole === "lessor") {
      chkLessor.addEventListener("change", async () => {
        try {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/verify-id`, {
            method: "POST",
            body: { role: "lessor", checked: chkLessor.checked },
          });
          activeSession = res.session;
          viewStep = activeSession.current_step;
          Bass.updateAppAlert("success", res.message);
          renderWorkspace();
        } catch (err) {
          Bass.showError(err);
        }
      });
    }

    document.getElementById("btnStep1Confirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const myIdChk = currentRole === "lessor"
          ? document.getElementById("chkLessorVerifiedTenantId").checked
          : document.getElementById("chkTenantVerifiedLessorId").checked;
        const body = {
          role: currentRole,
          checked_counterparty_id: myIdChk,
        };
        if (currentRole === "lessor") {
          body.registry_updates = {
            issue_number: document.getElementById("regIssue").value.trim(),
            owner_name: document.getElementById("regOwner").value.trim(),
            mortgage_amount: Number(document.getElementById("regMortgage").value || 0),
          };
        }
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step1`, { method: "POST", body });
        activeSession = res.session;
        viewStep = activeSession.current_step;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep2(s) {
    const opts = s.option_checks || [];
    elStepPanel.innerHTML = `
      <h3>② 호실 상태 및 옵션 실물 공동 점검표</h3>
      <p class="bass-muted" style="font-size:13px;">입실 시 함께 확인한 시설 상태가 퇴실 정산 비교 원본으로 박제되어 억울한 원상복구 분쟁을 차단합니다.</p>
      <div style="margin:12px 0;">
        ${opts.map((o, idx) => `
          <label class="check-row">
            <span><strong>${Bass.escapeHtml(o.item)}</strong> (${o.quantity}개) — <span class="bass-muted">${Bass.escapeHtml(o.condition)}</span></span>
            <input type="checkbox" class="opt-chk" data-idx="${idx}" ${o.checked ? "checked" : ""}>
          </label>
        `).join("")}
      </div>
      <div>
        <label class="bass-label">현장 시설 특기사항 메모</label>
        <input id="facNotes" class="bass-input" value="${Bass.escapeHtml(s.facility_notes || "")}">
      </div>
      <div class="bass-row" style="margin:12px 0;">
        <span class="bass-badge ${s.step2_lessor_confirmed ? "bass-badge--ok" : "bass-badge--warn"}">임대인 확인: ${s.step2_lessor_confirmed ? "완료" : "대기"}</span>
        <span class="bass-badge ${s.step2_tenant_locked ? "bass-badge--ok" : "bass-badge--warn"}">임차인 확인 잠금: ${s.step2_tenant_locked ? "완료" : "대기"}</span>
      </div>
      <button id="btnStep2Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ${currentRole === "lessor" ? "🏢 시설 점검표 확인 완료" : "🔑 실물 옵션 이상 없음 상호 동의 잠금"}
      </button>
    `;
    document.getElementById("btnStep2Confirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const updatedOpts = opts.map((o, i) => ({
          ...o,
          checked: document.querySelector(`.opt-chk[data-idx="${i}"]`).checked,
        }));
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step2`, {
          method: "POST",
          body: {
            role: currentRole,
            option_checks: updatedOpts,
            facility_notes: document.getElementById("facNotes").value.trim(),
          },
        });
        activeSession = res.session;
        viewStep = activeSession.current_step;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep3(s) {
    const t = s.terms || {};
    elStepPanel.innerHTML = `
      <h3>③ 계약 조건 및 특약사항 공동 작성 (Revision v${s.revision})</h3>
      <p class="bass-muted" style="font-size:13px;">어느 한쪽이라도 금액·날짜·특약을 1글자라도 수정하면 리비전이 올라가고 기존 동의가 자동 해제됩니다(위변조 원천 차단).</p>
      <div class="field-grid" style="margin:12px 0;">
        <div><label class="bass-label">임차인 성명 *</label><input id="tName" class="bass-input term-input" value="${Bass.escapeHtml(t.tenant_name || "")}"></div>
        <div><label class="bass-label">임차인 연락처 *</label><input id="tPhone" class="bass-input term-input" value="${Bass.escapeHtml(t.tenant_phone || "")}"></div>
        <div><label class="bass-label">차량 번호 (주차 등록용)</label><input id="tCar" class="bass-input term-input" value="${Bass.escapeHtml(t.tenant_car_number || "")}" placeholder="예: 12가 3456"></div>
        <div><label class="bass-label">입주(전입)일</label><input id="tMoveIn" type="date" class="bass-input term-input" value="${Bass.escapeHtml(t.move_in_date || "")}"></div>
        <div>
          <label class="bass-label">임대 구분 / 납부 시점 (rent2 엔진 직결)</label>
          <select id="tPayTiming" class="bass-select term-input">
            <option value="후불" ${t.pay_timing === "후불" ? "selected" : ""}>월세 후불 (입주월 비과세 · 익월 청구)</option>
            <option value="선불" ${t.pay_timing === "선불" ? "selected" : ""}>월세 선불 (입주월부터 당월 청구)</option>
          </select>
        </div>
        <div><label class="bass-label">계약 기간(개월)</label><input id="tMonths" type="number" class="bass-input term-input" value="${Number(t.lease_term_months || 12)}"></div>
        <div><label class="bass-label">보증금(원)</label><input id="tDeposit" type="number" class="bass-input term-input" value="${Number(t.deposit || 0)}"></div>
        <div><label class="bass-label">월 임차료(원)</label><input id="tRent" type="number" class="bass-input term-input" value="${Number(t.monthly_rent || 0)}"></div>
        <div><label class="bass-label">월 관리비(원)</label><input id="tMaint" type="number" class="bass-input term-input" value="${Number(t.maintenance_fee || 0)}"></div>
        <div><label class="bass-label">당일 계약금(원)</label><input id="tConDep" type="number" class="bass-input term-input" value="${Number(t.contract_deposit || 0)}"></div>
      </div>
      <div>
        <label class="bass-label">상호 합의 특약사항</label>
        <textarea id="tSpecial" rows="4" class="bass-input term-input">${Bass.escapeHtml(t.special_terms || "")}</textarea>
      </div>
      <div class="bass-row" style="margin:12px 0;justify-content:space-between;">
        <div class="bass-row">
          <span class="bass-badge ${s.step3_lessor_agreed ? "bass-badge--ok" : "bass-badge--warn"}">임대인 합의: ${s.step3_lessor_agreed ? "잠금 완료" : "미동의"}</span>
          <span class="bass-badge ${s.step3_tenant_agreed ? "bass-badge--ok" : "bass-badge--warn"}">임차인 합의: ${s.step3_tenant_agreed ? "잠금 완료" : "미동의"}</span>
        </div>
        <button id="btnSaveTerms" class="bass-btn" type="button">✏️ 조건 수정 반영 (공동 화면 갱신)</button>
      </div>
      <button id="btnAgreeStep3" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        🔒 현재 조건(v${s.revision})에 ${currentRole === "lessor" ? "임대인(갑)" : "임차인(을)"} 최종 합의 잠금
      </button>
    `;

    elStepPanel.querySelectorAll(".term-input").forEach((inp) => {
      inp.addEventListener("focus", () => { isEditingForm = true; });
      inp.addEventListener("blur", () => { setTimeout(() => { isEditingForm = false; }, 400); });
    });

    const collectUpdates = () => ({
      tenant_name: document.getElementById("tName").value.trim(),
      tenant_phone: document.getElementById("tPhone").value.trim(),
      tenant_car_number: document.getElementById("tCar").value.trim(),
      move_in_date: document.getElementById("tMoveIn").value,
      pay_timing: document.getElementById("tPayTiming").value,
      lease_term_months: Number(document.getElementById("tMonths").value || 12),
      deposit: Number(document.getElementById("tDeposit").value || 0),
      monthly_rent: Number(document.getElementById("tRent").value || 0),
      maintenance_fee: Number(document.getElementById("tMaint").value || 0),
      contract_deposit: Number(document.getElementById("tConDep").value || 0),
      special_terms: document.getElementById("tSpecial").value,
    });

    document.getElementById("btnSaveTerms").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/terms`, {
          method: "POST",
          body: { role: currentRole, updates: collectUpdates() },
        });
        activeSession = res.session;
        lastRevision = activeSession.revision;
        isEditingForm = false;
        Bass.updateAppAlert(res.level || "success", res.message);
        renderWorkspace();
      });
    });

    document.getElementById("btnAgreeStep3").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const editRes = await Bass.api(APP_ID, `/sessions/${s.id}/terms`, {
          method: "POST",
          body: { role: currentRole, updates: collectUpdates() },
        });
        activeSession = editRes.session;
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step3/agree`, {
          method: "POST",
          body: { role: currentRole, expected_revision: activeSession.revision },
        });
        activeSession = res.session;
        lastRevision = activeSession.revision;
        viewStep = activeSession.current_step;
        isEditingForm = false;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep4(s) {
    const mySig = currentRole === "lessor" ? s.lessor_signature : s.tenant_signature;
    const defaultName = currentRole === "lessor" ? s.lessor_name : ((s.terms && s.terms.tenant_name) || "임차인");

    const okLogin = Boolean(s.lessor_login_verified && s.tenant_login_verified);
    const okMutualId = Boolean(s.lessor_checked_tenant_id && s.tenant_checked_lessor_id);
    const okRegistry = Boolean(s.step1_lessor_confirmed && s.step1_tenant_locked);
    const okStep3 = Boolean(s.step3_lessor_agreed && s.step3_tenant_agreed);
    const canSignNow = okLogin && okMutualId && okRegistry && okStep3;

    elStepPanel.innerHTML = `
      <h3>④ 신분증·등기부 확인 검증 요약 및 자필 전자서명</h3>
      <p class="bass-muted" style="font-size:13px;">
        로그인 본인인증, 서로 신분증 확인 체크, 등기부등본 열람 확인, 계약조건 합의가 모두 완료되어야 서명이 날인되며 SHA-256 해시로 봉인됩니다.
      </p>

      <div class="gate-list">
        <div class="gate-item">
          <span>1. 임대인·임차인 각자 로그인 본인인증 완료</span>
          <span class="bass-badge ${okLogin ? "bass-badge--ok" : "bass-badge--warn"}">${okLogin ? "확인 완료 ✓" : "미완료"}</span>
        </div>
        <div class="gate-item">
          <span>2. 서로 신분증 대조 본인 확인 체크 (임대인 ${s.lessor_checked_tenant_id ? "✓" : "✗"} · 임차인 ${s.tenant_checked_lessor_id ? "✓" : "✗"})</span>
          <span class="bass-badge ${okMutualId ? "bass-badge--ok" : "bass-badge--warn"}">${okMutualId ? "양측 완료 ✓" : "Step 1 체크 필요"}</span>
        </div>
        <div class="gate-item">
          <span>3. 대법원 등기부등본 권리관계 확인 잠금</span>
          <span class="bass-badge ${okRegistry ? "bass-badge--ok" : "bass-badge--warn"}">${okRegistry ? "확인 완료 ✓" : "Step 1 확인 필요"}</span>
        </div>
        <div class="gate-item">
          <span>4. 계약 조건(Revision v${s.revision}) 양측 최종 합의 잠금</span>
          <span class="bass-badge ${okStep3 ? "bass-badge--ok" : "bass-badge--warn"}">${okStep3 ? "합의 완료 ✓" : "Step 3 합의 필요"}</span>
        </div>
      </div>

      ${!canSignNow ? `
        <div class="call-banner" style="margin-bottom:12px;">
          <div style="font-weight:800; font-size:13px; margin-bottom:6px;">⚠️ 전자서명 전 필수 확인 항목이 남아 있습니다.</div>
          <div class="bass-row">
            ${(!okMutualId || !okRegistry) ? `<button type="button" id="btnJumpStep1" class="bass-btn bass-btn--sm">① 신분증 체크 · 등기부등본 확인하러 가기 →</button>` : ""}
            ${!okStep3 ? `<button type="button" id="btnJumpStep3" class="bass-btn bass-btn--sm">③ 계약조건 합의 잠금하러 가기 →</button>` : ""}
          </div>
        </div>
      ` : ""}

      <div class="bass-row" style="margin:10px 0;">
        <span class="bass-badge ${s.lessor_signature ? "bass-badge--ok" : "bass-badge--warn"}">임대인 서명: ${s.lessor_signature ? "날인 완료 ✓" : "대기"}</span>
        <span class="bass-badge ${s.tenant_signature ? "bass-badge--ok" : "bass-badge--warn"}">임차인 서명: ${s.tenant_signature ? "날인 완료 ✓" : "대기"}</span>
      </div>
      ${s.integrity_hash ? `<div class="bass-card" style="padding:12px;margin-bottom:12px;font-size:12px;">🔐 <strong>SHA-256 무결성 봉인 해시:</strong> <code>${Bass.escapeHtml(s.integrity_hash)}</code></div>` : ""}

      <label class="bass-label">${currentRole === "lessor" ? "🏢 임대인(갑)" : "🔑 임차인(을)"} 자필 서명 패드 (마우스/터치 드로잉)</label>
      <canvas id="sigCanvas" class="sig-pad" width="600" height="110"></canvas>
      <div class="field-grid" style="margin-top:10px;">
        <div>
          <label class="bass-label">정자 서명 성명 확인</label>
          <input id="sigText" class="bass-input" value="${Bass.escapeHtml(mySig && !mySig.startsWith("data:") ? mySig : `${defaultName} (자필전자서명)`)}">
        </div>
        <div class="bass-row" style="align-items:flex-end;">
          <button id="btnClearCanvas" class="bass-btn" type="button">지우기</button>
          <button id="btnSubmitSign" class="bass-btn bass-btn--primary" style="flex:1;" type="button" ${!canSignNow ? "disabled" : ""}>
            ✍️ 전자서명 날인 완료
          </button>
        </div>
      </div>
    `;

    const jump1 = document.getElementById("btnJumpStep1");
    if (jump1) jump1.addEventListener("click", () => { viewStep = 1; renderWorkspace(); });
    const jump3 = document.getElementById("btnJumpStep3");
    if (jump3) jump3.addEventListener("click", () => { viewStep = 3; renderWorkspace(); });

    const canvas = document.getElementById("sigCanvas");
    const ctx = canvas.getContext("2d");
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#0f172a";
    let drawing = false;
    let hasStroke = false;

    const pos = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const pt = ev.touches ? ev.touches[0] : ev;
      return {
        x: ((pt.clientX - rect.left) / rect.width) * canvas.width,
        y: ((pt.clientY - rect.top) / rect.height) * canvas.height,
      };
    };
    canvas.addEventListener("pointerdown", (ev) => {
      drawing = true;
      hasStroke = true;
      isEditingForm = true;
      const p = pos(ev);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
    });
    canvas.addEventListener("pointermove", (ev) => {
      if (!drawing) return;
      const p = pos(ev);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    });
    window.addEventListener("pointerup", () => { drawing = false; });

    document.getElementById("btnClearCanvas").addEventListener("click", () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      hasStroke = false;
    });

    document.getElementById("btnSubmitSign").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const textSig = document.getElementById("sigText").value.trim();
        const sigPayload = hasStroke ? canvas.toDataURL("image/png") : textSig;
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step4/sign`, {
          method: "POST",
          body: { role: currentRole, signature: sigPayload, expected_revision: s.revision },
        });
        activeSession = res.session;
        isEditingForm = false;
        viewStep = activeSession.current_step;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep5(s) {
    const t = s.terms || {};
    const done = s.status === "COMPLETED";
    elStepPanel.innerHTML = `
      <h3>⑤ 계약금 입금 확인 & 공동 Data Pool 원장 일괄 생성</h3>
      <p class="bass-muted" style="font-size:13px;">체결 완료 즉시 <code>contracts.json</code>, <code>rooms.json</code>, <code>users.json</code>, <code>payments.json</code>, <code>ops.json(RTMS 확정일자)</code>가 실시간 업데이트됩니다.</p>
      <div class="bass-card" style="padding:14px;margin:12px 0;">
        <div><strong>입금 계좌:</strong> ${Bass.escapeHtml(s.lessor_account)} (예금주: ${Bass.escapeHtml(s.lessor_name)})</div>
        <div><strong>당일 계약금:</strong> ${fmtWon(t.contract_deposit)} / <strong>보증금 총액:</strong> ${fmtWon(t.deposit)} / <strong>월세+관리비:</strong> ${fmtWon(Number(t.monthly_rent || 0) + Number(t.maintenance_fee || 0))} (${Bass.escapeHtml(t.pay_timing)})</div>
        ${s.fixed_date_no ? `<div style="margin-top:6px;color:var(--ok);font-weight:700;">🏛️ 대법원 확정일자 자동 부여 완료: ${Bass.escapeHtml(s.fixed_date_no)}</div>` : ""}
      </div>
      ${done ? `
        <div class="bass-row">
          <button onclick="window.print()" class="bass-btn bass-btn--primary" type="button">🖨️ 표준 임대차 전자계약서 A4 인쇄 / PDF 저장</button>
          <a class="bass-btn" href="/rent3_lessor/">🏢 임대인 수납장부로 이동</a>
          <a class="bass-btn" href="/rent3_tenant/">🔑 임차인 마이홈으로 이동</a>
        </div>
      ` : `
        <div class="field-grid" style="margin-bottom:12px;">
          <div>
            <label class="bass-label">계약금 납부 수단</label>
            <select id="payMethodSel" class="bass-select">
              <option value="TRANSFER">계좌이체 실시간 입금</option>
              <option value="KAKAOPAY">카카오페이 간편송금</option>
              <option value="CARD">신용/체크카드 결제</option>
            </select>
          </div>
        </div>
        <button id="btnCompleteSeal" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
          🎉 계약금 입금 확인 & 임대차 계약 최종 체결 봉인
        </button>
      `}
    `;
    const btn = document.getElementById("btnCompleteSeal");
    if (btn) {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const method = document.getElementById("payMethodSel").value;
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/step5/complete`, {
            method: "POST",
            body: { payment_method: method },
          });
          activeSession = res.session;
          sealedContract = res.contract;
          Bass.updateAppAlert("success", res.message, "아래 생성된 표준 전자계약서를 인쇄하거나 임대인/임차인 앱에서 즉시 동기화된 장부를 확인하세요.");
          await loadSessionList();
          renderWorkspace();
        });
      });
    }
  }

  function renderSigCell(sig) {
    if (!sig) return `(미서명)`;
    if (sig.startsWith("data:image")) {
      return `<img src="${sig}" alt="전자서명" style="max-height:48px;vertical-align:middle;">`;
    }
    return `<strong>${Bass.escapeHtml(sig)}</strong>`;
  }

  function renderSealedPaper() {
    if (!activeSession || activeSession.status !== "COMPLETED" || !sealedContract) {
      elSealedView.style.display = "none";
      elSealedView.innerHTML = "";
      return;
    }
    const c = sealedContract;
    const regSnap = c.registry_snapshot || {};
    elSealedView.style.display = "block";
    elSealedView.innerHTML = `
      <article class="contract-paper">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #000;padding-bottom:10px;margin-bottom:14px;">
          <div>
            <h2 style="margin:0;">표준 부동산 임대차 전자계약서 (${Bass.escapeHtml(c.lease_type)})</h2>
            <div style="font-size:12px;">계약번호: ${Bass.escapeHtml(c.id)} · 체결일: ${Bass.escapeHtml(c.contract_date)}</div>
          </div>
          <div style="border:2px solid #b91c1c;color:#b91c1c;padding:6px 10px;font-weight:800;font-size:12px;text-align:center;">
            확정일자 부여필<br>${Bass.escapeHtml(c.fixed_date_no)}
          </div>
        </div>

        <table>
          <tr><th>목적물 소재지</th><td colspan="3">${Bass.escapeHtml(activeSession.building_address)} (${Bass.escapeHtml(activeSession.building_name)} ${Bass.escapeHtml(c.room_no)}호)</td></tr>
          <tr><th>보증금</th><td>${fmtWon(c.deposit)}</td><th>계약금(보증금 내)</th><td>${fmtWon(c.contract_deposit)} (영수함)</td></tr>
          <tr><th>월 임차료</th><td>${fmtWon(c.monthly_rent)} (${Bass.escapeHtml(c.pay_timing)})</td><th>월 관리비 / 부가세</th><td>${fmtWon(c.maintenance_fee)} / ${fmtWon(c.vat)}</td></tr>
          <tr><th>임대차 기간</th><td colspan="3">${Bass.escapeHtml(c.move_in_date)} ~ ${Bass.escapeHtml(c.expiry_date)} (${c.lease_term_months}개월)</td></tr>
          <tr><th>본인인증 및 신분증·등기 확인</th><td colspan="3">양측 로그인 본인인증 완료 · 임대인↔임차인 상호 신분증 대조 본인 확인 체크 완료 · 등기부등본(${Bass.escapeHtml(regSnap.issue_number || "-")}, 소유자 ${Bass.escapeHtml(regSnap.owner_name || c.lessor_name)}) 열람 확인 필</td></tr>
          <tr><th>특약사항</th><td colspan="3" style="white-space:pre-line;">${Bass.escapeHtml(c.special_terms)}</td></tr>
          <tr><th>임대인(갑) 서명</th><td>${Bass.escapeHtml(c.lessor_name)} &nbsp; ${renderSigCell(c.lessor_signature)}</td><th>임차인(을) 서명</th><td>${Bass.escapeHtml(c.tenant_name)} (${Bass.escapeHtml(c.tenant_phone)}) &nbsp; ${renderSigCell(c.tenant_signature)}</td></tr>
          <tr><th>무결성 해시(SHA-256)</th><td colspan="3" style="font-family:monospace;font-size:11px;word-break:break-all;">${Bass.escapeHtml(c.integrity_hash)}</td></tr>
        </table>
      </article>
    `;
  }

  (async function init() {
    try {
      await loadCandidateRooms();
      await loadRoleExtras();
      const list = await Bass.api(APP_ID, "/sessions");
      allSessions = list.sessions || [];
      renderHome(true);

      const sid = params.get("session");
      const found = sid ? allSessions.find((x) => x.id === sid || x.session_code === sid) : null;
      if (found) {
        await openSession(found.id);
      } else if (params.get("call_branch") && params.get("call_room")) {
        const val = `${params.get("call_branch")}|${params.get("call_room")}`;
        if (currentRole === "lessor") openLessorCallDialog(val);
        else openTenantPickerDialog();
      }

      setInterval(async () => {
        if (document.hidden) return;
        try {
          if (!elContract.hidden) {
            if (activeSessionId && !isEditingForm) await fetchActiveSession(false);
          } else if (!elDlgHost.firstChild) {
            const l = await Bass.api(APP_ID, "/sessions");
            allSessions = l.sessions || [];
            renderHome(false);
          }
        } catch (_) { /* 다음 주기에 재시도 */ }
      }, 2500);

      setInterval(async () => {
        if (document.hidden || !elContract.hidden) return;
        await loadRoleExtras();
        await loadCandidateRooms().catch(() => {});
        renderHome(false);
      }, 15000);
    } catch (err) {
      Bass.showError(err);
    }
  })();
})();
