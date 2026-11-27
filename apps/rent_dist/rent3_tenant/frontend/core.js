/* apps/rent3_tenant/frontend/core.js — 임차인 앱 공통 상태·메뉴·포맷 헬퍼 (window.R3T) */
(function () {
  "use strict";
  // 바로가기(qk)로 열렸고 아직 로그인 전이면 R3Shell 이 간편 비밀번호 화면을 덮어 띄운다 — 로그인되면 새로 불러오므로 앱은 그때 시작한다
  if (window.R3Shell && window.R3Shell.pinPending && window.R3Shell.pinPending()) return;

  const APP_ID = "rent3_tenant";
  const params = new URLSearchParams(window.location.search);
  let savedSession = null;
  try {
    savedSession = JSON.parse(window.localStorage.getItem("rent3_session") || "null");
  } catch (_) {
    savedSession = null;
  }

  // 세입자 본인 로그인이면 본인 계정만 본다. 전체 세입자 목록(/tenants)은 임대인·관리자 전용이라 세입자는 부를 수 없다.
  const isTenantUser = Boolean(savedSession && (savedSession.role === "tenant" || savedSession.role === "prospect"));
  const initialMode = params.get("view") === "listings" || (!params.get("view") && savedSession && savedSession.role === "prospect")
    ? "listings"
    : "myhome";
  const initialTenantId = (isTenantUser ? (savedSession.tenant_id || savedSession.id) : "") || params.get("tenant") || (savedSession && savedSession.tenant_id ? savedSession.tenant_id : "");

  const elMyhome = document.getElementById("viewMyhome");
  const elListings = document.getElementById("viewListings");

  // ☰ 메뉴: 첫 화면은 '내 집(납부 확인)' 하나뿐이고, 나머지는 모두 여기서 들어간다(빠진 기능 0개 — test_56 이 점검).
  const TENANT_MENU = [
    { items: [{ label: "🏠 내 집 (납부 확인)", id: "btnSvcHome" }] },
    { title: "계약 · 증빙 서류", items: [
      { label: "🤝 스마트 계약", href: "/rent3_contract/?role=tenant" },
      { label: "📸 중개 계약서 사본", id: "btnSvcPaper" },
      { label: "🧾 연말정산 납부확인서", id: "btnSvcCert" },
    ] },
    { title: "계약 기간 · 퇴실", items: [
      { label: "📅 만기 · 연장 안내", id: "btnSvcExpiry" },
      { label: "🚪 퇴실 정산", id: "btnSvcCheckout" },
    ] },
    { title: "생활 · 요청", items: [
      { label: "💬 임대인과 채팅", id: "btnSvcMsg" },
      { label: "🔧 A/S 요청", id: "btnSvcAs" },
      { label: "🔑 도어락 · 차량", id: "btnSvcDoor" },
    ] },
    { title: "내 설정", items: [
      { label: "🔐 내 정보 · 비밀번호 변경", id: "btnMyAccount", userOnly: true },
      { label: "🏢 사업자 정보 · 세금계산서 요청", id: "btnBizInfo", userOnly: true },
      { label: "📲 바로가기 · 간편 비밀번호", id: "btnShortcut", userOnly: true },
      { label: "🛡️ 원격 지원 허용 설정", id: "btnSupportSetting", userOnly: true },
    ] },
    { title: "도움말 · 문의", items: [
      { label: "💬 관리인과 채팅", id: "btnSupportChat", userOnly: true },
      { label: "📘 이용 매뉴얼", href: "manual.html" },
    ] },
    { title: "다른 집 찾기", items: [
      { label: "🔍 빈 집 찾기", id: "btnMenuListings" },
      { label: "💌 집주인에게 안심계약 제안하기", id: "btnLessorInvite", userOnly: true },
    ] },
    { title: "이동 (관리인)", items: [
      { label: "🏢 임대인 앱", href: "/rent3_lessor/", adminOnly: true },
      { label: "👑 관리인 앱", href: "/rent3_admin/", adminOnly: true },
      { label: "🌐 Data Pool 관제", href: "/rent3/pool.html", adminOnly: true },
    ] },
  ];
  const ME_ADMIN = R3Shell.isAdminSession();
  // 관리인: 다른 앱으로 건너가는 링크(임대인·임차인 앱)는 입장 없이는 막히므로 숨기고, 원격 지원 입장 중일 때만 돌아갈 곳(관리인 앱·Data Pool)을 보인다
  const adminLinkOk = (i) => R3Shell.isAdminProxy() && /^\/(rent3_admin\/|rent3\/pool)/.test(i.href || "");
  const MY_MENU = TENANT_MENU.map((g) => ({
    ...g,
    items: g.items.filter((i) => (ME_ADMIN ? (!i.userOnly && (!i.adminOnly || adminLinkOk(i))) : !i.adminOnly)),
  })).filter((g) => g.items.length);
  R3Shell.renderMenu(document.querySelector("[data-r3-drawer]"), MY_MENU);

  const SVC_TITLES = {
    home: "",
    notice: "납입 안내 · 미납 통보",
    expiry: "만기 · 연장 안내",
    checkout: "퇴실 정산",
    paper: "중개 계약서 사본",
    as: "A/S 요청",
    messages: "임대인과 채팅",
    cert: "연말정산 납부확인서",
    door: "도어락 · 차량",
  };
  const MENU_VIEWS = {
    btnSvcHome: "home",
    btnSvcCert: "cert",
    btnSvcPaper: "paper",
    btnSvcExpiry: "expiry",
    btnSvcCheckout: "checkout",
    btnSvcAs: "as",
    btnSvcDoor: "door",
    btnSvcMsg: "messages",
  };

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  function closeDrawer() {
    const dr = document.querySelector("[data-r3-drawer]");
    if (dr) dr.classList.remove("is-open");
    const ov = document.querySelector(".r3-overlay");
    if (ov) ov.classList.remove("is-open");
  }

  // 전출 정산 명세: 임차보증금 − 미납 임차료 − 일할 계산 임차료 − 청소비 − 미납 공과금(전기·수도·가스·기타) (− 파손 복구비) = 반환 예정액
  function coItemsHtml(co) {
    const W = (n) => fmtWon(Number(n || 0));
    const hasFinals = Number(co.rent_unpaid_auto || 0) + Number(co.rent_prorated_auto || 0) + Number(co.rent_unpaid || 0) + Number(co.rent_prorated || 0) > 0;
    const unpaid = hasFinals ? Number(co.rent_unpaid || 0) : Number(co.unpaid_rent_deduction || 0) + Math.max(0, Number(co.early_rent_deduction || 0) - Number(co.early_partial_amount || 0));
    const prorated = hasFinals ? Number(co.rent_prorated || 0) : Number(co.early_partial_amount || 0);
    const utilParts = [["전기", co.utility_electric], ["수도", co.utility_water], ["가스", co.utility_gas], ["기타", co.utility_other]].filter(([, v]) => Number(v || 0) > 0);
    const util = Number(co.utility_settlement || 0);
    const row = (label, val, sub) => `<tr><td style="padding:3px 0;">${label}${sub ? `<div class="bass-muted" style="font-size:12px;">${sub}</div>` : ""}</td><td style="text-align:right;padding:3px 0;white-space:nowrap;">${val}</td></tr>`;
    return `<table style="width:100%;border-collapse:collapse;font-size:13.5px;margin:6px 0;">
      ${row("임차보증금", W(co.deposit_original))}
      ${row("− 미납 임차료", W(unpaid), Number(co.early_rent_deduction || 0) - Number(co.early_partial_amount || 0) > 0 ? "만기(또는 새 임차인 입주 전날)까지 아직 내지 않은 월 임차료 포함" : "")}
      ${row(`− 일할 계산 임차료${Number(co.early_partial_days || 0) > 0 ? ` (${Number(co.early_partial_days)}일분)` : ""}`, W(prorated), Number(co.early_partial_days || 0) > 0 ? "(경과 일수 × 월 임차료) ÷ 30" : "")}
      ${row("− 청소비", W(co.cleaning_fee), Number(co.cleaning_fee_agreed || 0) > 0 ? `계약에 약정한 퇴실 청소비 ${W(co.cleaning_fee_agreed)} 이내` : "")}
      ${row("− 미납 공과금", W(util), utilParts.length ? utilParts.map(([k, v]) => `${k} ${W(v)}`).join(" · ") : "")}
      ${Number(co.damage_restoration_fee || 0) > 0 ? row("− 파손 복구비", W(co.damage_restoration_fee)) : ""}
      <tr style="border-top:2px solid var(--border);"><td style="padding:6px 0;"><strong>= 반환 예정액</strong></td><td style="text-align:right;padding:6px 0;"><strong>${W(co.refund_amount)}</strong></td></tr>
    </table>
    ${co.edited_by_lessor ? `<p class="bass-muted" style="font-size:12.5px;">ℹ️ 임대인이 자동 계산값(미납 임차료 ${W(co.rent_unpaid_auto)} · 일할 계산 임차료 ${W(co.rent_prorated_auto)})에서 수정했습니다. 다르다고 생각하면 이의 제기로 협의하세요.</p>` : ""}`;
  }

  window.R3T = {
    APP_ID,
    params,
    savedSession,
    isTenantUser,
    ME_ADMIN,
    SVC_TITLES,
    MENU_VIEWS,
    elMyhome,
    elListings,
    state: {
      mode: initialMode,
      selectedTenantId: initialTenantId,
      showDoorlock: false,
      svcView: SVC_TITLES[params.get("svc")] !== undefined ? params.get("svc") : "home",
      tnMsgTimer: null,
      tnMsgCount: -1,
      tnCoSig: "",
    },
    fmtWon,
    closeDrawer,
    coItemsHtml,
  };
})();
