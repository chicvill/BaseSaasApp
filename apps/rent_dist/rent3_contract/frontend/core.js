/* apps/rent_dist/rent3_contract/frontend/core.js — 공통 상태·인증·포맷·데이터 로더. */
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
    try { if (params.get("session")) window.localStorage.setItem("rent3_pending_join", params.get("session")); } catch (_) {}
    window.location.replace("/rent3/?login=1");
    window.R3C = null;
    return;
  }

  // 관리인 대리 접속(보기 전용): 관리인 원격 지원 카드가 정한 입장(role)으로 계약방을 연다. 동의·서명은 서버가 막는다.
  const adminProxy = Boolean((loggedUser.role === "admin" || loggedUser.role === "superadmin") && params.get("proxy"));
  const currentRole = adminProxy
    ? (params.get("role") === "tenant" ? "tenant" : "lessor")
    : (loggedUser.role === "tenant" || loggedUser.role === "prospect")
      ? "tenant"
      : (loggedUser.role === "cosign" && params.get("role") === "tenant" ? "tenant" : "lessor");

  (function injectStyles() {
    const st = document.createElement("style");
    st.textContent = `
    .turn-line { margin-top: 8px; padding: 7px 10px; border-radius: 10px; font-size: 13px; font-weight: 700; background: var(--surface); border: 1px solid var(--border); color: var(--muted, #94a3b8); }
    .turn-line.is-mine { background: hsl(258 90% 60% / .22); border-color: var(--primary); color: inherit; }
    .dotline { display: flex; gap: 18px; margin: 10px 0 2px; font-size: 14px; }
    .dotline__item { display: inline-flex; align-items: center; gap: 6px; }
    .dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
    .dot--ok { background: #22c55e; } .dot--wait { background: #f59e0b; }
    .term-grid { display: grid; gap: 6px; margin: 10px 0; }
    .term-row { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; align-items: center; padding: 8px 10px; border: 1px solid var(--border); border-radius: 10px; font-size: 14px; }
    .term-row b { font-size: 15px; }
    .chat-fab { position: fixed; right: 14px; bottom: 16px; z-index: 900; width: 56px; height: 56px; border-radius: 50%; border: 1px solid var(--border); background: linear-gradient(120deg, var(--primary), var(--primary-2)); color: #fff; font-size: 24px; box-shadow: 0 6px 18px rgba(0,0,0,.4); cursor: pointer; }
    .chat-fab__n, .chat-fab__o { position: absolute; top: -4px; min-width: 20px; height: 20px; padding: 0 5px; border-radius: 10px; font-size: 12px; font-weight: 800; line-height: 20px; text-align: center; }
    .chat-fab__n { right: -4px; background: #ef4444; color: #fff; } .chat-fab__o { left: -4px; background: #f59e0b; color: #1f1500; }
    .cm-list { max-height: 60vh; overflow-y: auto; padding: 2px 2px 6px; }
    .cm-b { margin: 6px 0; display: flex; } .cm-b.is-mine { justify-content: flex-end; }
    .cm-b > div { max-width: 88%; padding: 8px 11px; border-radius: 12px; font-size: 13.5px; line-height: 1.5; background: #ffffff; border: 1px solid #e2e8f0; color: #0f172a; }
    .cm-b.is-mine > div { background: #dbeafe; border-color: #93c5fd; color: #0f172a; }
    .cm-meta { font-size:12.5px; opacity: .75; margin-bottom: 2px; }
    .cm-sys { margin: 8px 0; text-align: center; font-size: 12px; opacity: .82; }
    .cm-msg-card { margin: 8px 0; padding: 10px 12px; background: #ffffff; border: 1px solid var(--border); border-radius: 12px; font-size: 13.5px; line-height: 1.55; }
    .cm-msg-card.is-open { border-color: #f59e0b; background: #fffbeb; }
    .reopen-box { margin-top: 16px; padding-top: 10px; border-top: 1px dashed var(--border); }
    `;
    document.head.appendChild(st);
  })();

  if (adminProxy) {
    const st = document.createElement("style");
    st.textContent = ".r3-proxy #stepPanel button,.r3-proxy #stepPanel input,.r3-proxy #stepPanel select,.r3-proxy #stepPanel textarea,.r3-proxy #dlgHost button,.r3-proxy #btnCancelContract{pointer-events:none;opacity:.5}";
    document.head.appendChild(st);
    document.body.classList.add("r3-proxy");
  }

  const myTenantId = loggedUser.tenant_id || loggedUser.id || "";
  const myBranch = loggedUser.branch && loggedUser.branch !== "all" ? loggedUser.branch : "";
  const ROLE_KO = { lessor: "임대인", tenant: "임차인" };
  const OTHER_KO = currentRole === "lessor" ? "임차인" : "임대인";

  const state = {
    candidateRooms: [],
    participantTenants: [],
    allSessions: [],
    activeSessionId: "",
    activeSession: null,
    sealedContract: null,
    renewalContext: null,
    roomInfo: null,
    latestChatMessages: [],
    viewStep: 1,
    lastRevision: null,
    lastCallStatus: null,
    isEditingForm: false,
    chatSummary: { unread: 0, open_count: 0 },
    myhome: null,
    lessorDash: null,
    homeSig: "",
    modalKey: "",
    lastStamp: "",
    modalAnimating: false,
    modalTimer: null,
    stepModalOpen: false,
    modalForceDoc: false,
    reviewedStdContractRev: null,
    wsChatLastSig: "",
    myIdentity: null,
    pendingFacNotes: null,
  };

  const dom = {
    elHome: document.getElementById("homeView"),
    elHomeBody: document.getElementById("homeBody"),
    elContract: document.getElementById("contractView"),
    elChatWs: document.getElementById("chatWorkspace"),
    elModal: document.getElementById("stepModal"),
    elCard: document.getElementById("stepCard"),
    elStepPanel: document.getElementById("stepPanel"),
    elSealedView: document.getElementById("sealedContractView"),
    elDlgHost: document.getElementById("dlgHost"),
  };

  const esc = Bass.escapeHtml;
  const digits = (v) => String(v || "").replace(/\D/g, "");

  function acctLabel(s) {
    const a = (s && s.lessor_account || "").trim();
    return a ? `${Bass.escapeHtml(a)} (예금주: ${Bass.escapeHtml(s.lessor_name || "")})` : "임대인이 수납 계좌를 아직 등록하지 않았습니다(임대인에게 계좌 안내를 요청하세요)";
  }
  function autoPayGuideHtml() {
    return "💡 매월 연체 없이 납부하려면 <strong>은행 앱의 자동이체</strong>를 등록하길 권합니다. (신용카드·페이 결제는 추후 지원 예정입니다.)";
  }
  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  const FIELD_LABEL = { deposit: "보증금", contract_deposit: "계약금", monthly_rent: "월세", maintenance_fee: "관리비", cleaning_fee: "퇴실 청소비", move_in_date: "입주일", lease_term_months: "계약기간" };
  const fmtField = (f, v) => (f === "move_in_date" ? String(v) : f === "lease_term_months" ? `${v}개월` : fmtWon(v));
  const hhmm = (iso) => (iso ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");
  const STATUS_KO = { open: "답 기다림", accepted: "수락됨", declined: "거절됨", withdrawn: "철회됨" };

  document.getElementById("whoBadge").textContent =
    `🛡️ ${loggedUser.name} · ${currentRole === "lessor" ? "임대인" : "임차인"}`;

  // ───────────────────────── 데이터 로더 ─────────────────────────
  async function loadCandidateRooms() {
    const [roomData, partData] = await Promise.all([
      Bass.api(APP_ID, "/rooms"),
      Bass.api(APP_ID, "/participants"),
    ]);
    state.candidateRooms = roomData.rooms || [];
    state.participantTenants = partData.tenants || [];
  }

  async function loadIdentity() {
    if (currentRole !== "tenant") { state.myIdentity = null; return; }
    try {
      const d = await Bass.api(APP_ID, "/identity");
      state.myIdentity = d && d.applies ? d.identity : null;
    } catch (_) { state.myIdentity = null; }
  }

  async function loadRoleExtras() {
    await loadIdentity();
    try {
      if (currentRole === "tenant" && loggedUser.role === "tenant" && myTenantId) {
        const d = await Bass.api("rent3_tenant", `/myhome/${encodeURIComponent(myTenantId)}`);
        state.myhome = d && d.room ? d : null;
      } else if (currentRole === "lessor") {
        const q = myBranch ? `?branch=${encodeURIComponent(myBranch)}` : "";
        state.lessorDash = await Bass.api("rent3_lessor", `/dashboard${q}`);
      }
    } catch (_) {
      state.myhome = null;
      state.lessorDash = null;
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

  const mySessions = () => state.allSessions.filter(isMine);
  const incomingCalls = () =>
    mySessions().filter((s) => s.status === "IN_PROGRESS" && s.call_status === "CALLING" && s.initiated_by_role !== currentRole);
  const ongoingSessions = () =>
    mySessions().filter((s) => (s.status === "IN_PROGRESS" || s.status === "PAUSED") && !(s.call_status === "CALLING" && s.initiated_by_role !== currentRole));

  async function loadSessionList() {
    const data = await Bass.api(APP_ID, "/sessions");
    state.allSessions = data.sessions || [];
    if (state.activeSessionId && !dom.elContract.hidden) {
      await window.R3C.fetchActiveSession(true);
    } else {
      window.R3C.renderHome(true);
    }
  }

  window.R3C = {
    APP_ID, SESSION_KEY, params, loggedUser, adminProxy, currentRole,
    myTenantId, myBranch, ROLE_KO, OTHER_KO, state, dom,
    esc, digits, acctLabel, autoPayGuideHtml, fmtWon,
    FIELD_LABEL, fmtField, hhmm, STATUS_KO,
    loadCandidateRooms, loadIdentity, loadRoleExtras,
    isMine, mySessions, incomingCalls, ongoingSessions, loadSessionList,
  };
})();
