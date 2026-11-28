/* apps/rent_dist/rent3_lessor/frontend/core.js — 공통 상태·메뉴·PIN 제한·포맷·스타일·공유 유틸. */
(function () {
  "use strict";

  const APP_ID = "rent3_lessor";
  const params = new URLSearchParams(window.location.search);
  let savedSession = null;
  try {
    savedSession = JSON.parse(window.localStorage.getItem("rent3_session") || "null");
  } catch (_) {
    savedSession = null;
  }

  const REG_FRESH_DAYS = 90;        // 서버(roomregistry.FRESH_DAYS)와 같게
  const CHECK_STYLE = { ok: "var(--ok)", caution: "var(--warn)", high: "var(--err)" };

  const elTabContent = document.getElementById("tabContent");

  // ☰ 메뉴: 첫 화면(수납 현황) 외의 모든 기능은 여기서 들어간다. 항목을 빼면 기능에 닿을 수 없으니 test_52 가 목록을 점검한다.
  // 단계별 메뉴: 처음에는 1단계(기본 업무)만 펼쳐 보이고, 나머지(more)는 '더 많은 기능'으로 접어 둔다(없애지 않고 접기만 — 검색하면 펼쳐진다).
  const LESSOR_MENU = [
    { title: "1단계 · 기본 업무", items: [
      { label: "💰 수납 현황 · 입금 문자 처리", tab: "ledger" },
      { label: "💬 임차인과 채팅 (호실별)", tab: "messages" },
      { label: "📸 중개 계약서 사진 등록", tab: "broker" },
    ] },
    { title: "2단계 · 매물 · 홍보", more: true, items: [
      { label: "🏷️ 임대매물 등록", tab: "rooms" },
      { label: "📜 등기부 확인 (등기클린)", tab: "registry" },
    ] },
    { title: "3단계 · 스마트 계약 · 세무", more: true, items: [
      { label: "🤝 스마트 계약", href: "/rent3_contract/?role=lessor" },
      { label: "🧾 세무 허브", tab: "tax" },
      { label: "🧾 세금계산서 · 현금영수증 발행", id: "btnTaxInvoice" },
      { label: "🧮 관리비 산출근거", id: "btnMgmtFee" },
      { label: "📥 납부현황 엑셀(CSV)", id: "btnLedgerCsv" },
      { label: "💳 가맹비 납부 · 세금계산서", id: "btnFeeEvidence" },
    ] },
    { title: "4단계 · 자동화 (준비 중)", more: true, items: [
      { label: "📧 입금 메일 자동 정리 (선택)", tab: "mail" },
    ] },
    { title: "운영 · 통보", more: true, items: [
      { label: "🔧 A/S 접수", tab: "ops" },
      { label: "🔔 일괄 납입 통보", tab: "notice" },
      { label: "⚖️ 만기 통보", tab: "expiry" },
      { label: "📨 임차인 앱 가입 안내 문자", id: "btnConnectMsg" },
    ] },
    { title: "건물 설정", more: true, items: [
      { label: "🏢 건물 정보 수정", id: "btnEditBuilding" },
      { label: "📷 사업자등록증 촬영 (임대인용 · 임차인용)", id: "btnBizRegModal" },
      { label: "📑 부가세 · 소득세 대상 호실 설정", tab: "bldgtax" },
    ] },
    { title: "내 설정", more: true, items: [
      { label: "🔐 내 정보 · 비밀번호 변경", id: "btnMyAccount", userOnly: true },
      { label: "📲 바로가기 · 간편 비밀번호", id: "btnShortcut", userOnly: true },
      { label: "🛡️ 원격 지원 허용 설정", id: "btnSupportSetting", userOnly: true },
      { label: "🔓 아이디·비밀번호로 로그인", id: "btnFullLogin", pinOnly: true },
    ] },
    { title: "도움말 · 문의", items: [
      { label: "💬 관리인과 채팅", id: "btnSupportChat", userOnly: true },
      { label: "📘 이용 매뉴얼", href: "manual.html" },
    ] },
    { title: "이동 (관리인)", items: [
      { label: "🔑 임차인 앱", href: "/rent3_tenant/", adminOnly: true },
      { label: "👑 관리인 앱", href: "/rent3_admin/", adminOnly: true },
      { label: "🌐 Data Pool 관제", href: "/rent3/pool.html", adminOnly: true },
    ] },
  ];

  // 사용자 전용 항목(문의·원격 지원 설정)은 관리인에게, 이동 링크(다른 앱)는 일반 임대인에게 보이지 않는다
  const ME_ADMIN = R3Shell.isAdminSession();
  // 관리인: 다른 앱으로 건너가는 링크(임대인·임차인 앱)는 입장 없이는 막히므로 숨기고, 원격 지원 입장 중일 때만 돌아갈 곳(관리인 앱·Data Pool)을 보인다
  const adminLinkOk = (i) => R3Shell.isAdminProxy() && /^\/(rent3_admin\/|rent3\/pool)/.test(i.href || "");
  // 간편 비밀번호(PIN)로 들어온 화면: 서버가 허용한 일(현황 조회·채팅·시설 처리·수납[허용 기기])만 메뉴에 둔다
  const VIA_PIN = R3Shell.isViaPin();
  const PIN_TABS = ["ledger", "messages", "ops"];
  const pinOk = (i) => i.pinOnly || i.id === "btnShortcut" || i.href === "manual.html" || PIN_TABS.includes(i.tab);

  const initialTab = params.get("tab") || "ledger";
  const state = {
    currentBranch: params.get("branch") || (savedSession && savedSession.role === "lessor" && savedSession.branch ? savedSession.branch : "main"),
    currentTab: initialTab,
    dash: null,
    brokerTargetRoomNo: "",
    smsManualHint: null,        // 입금 문자가 월 납부액과 달라 [직접 수납]으로 넘긴 건: 입금액·입금일·입금자
    regOpen: false,             // 등기부 확인 카드: 읽기·확정 뒤 다시 그려도 열린 채로 둔다
    brokerPhotoDataUrl: "",
    brokerPhotoFilename: "",
    selectedAsOfDate: "",
    MAIL_ON: false,             // 서버 스위치(RENT3_MAIL_ENABLED)
    hdrName: "",
    selRoomNo: params.get("room") || "",   // 관리인 원격 지원 카드가 호실을 지정해서 들어온다
    autoRoom: params.get("room") || "",    // 그 호실의 수납 창을 처음 한 번 자동으로 연다
    payFlash: null,             // 방금 문자로 수납한 결과 안내({no, text})
    receiptMsg: {},             // 호실 → 임차인에게 보낼 문자 초안(수납 직후 영수 문구)
    taxYear: 0,
    taxMonth: 0,
    taxView: "hub",             // hub | income | vat | invoice | expense
    taxCache: null,
    msgRoom: initialTab === "messages" ? (params.get("room") || "") : "",
    listingTargetRoomNo: initialTab === "rooms" ? (params.get("room") || "") : "",
    msgTimer: null,
    msgCoSig: "",
    bizPicked: {},
  };

  const buildMenu = () => LESSOR_MENU.map((g) => ({
    ...g,
    items: g.items.filter((i) =>
      (i.tab !== "mail" || state.MAIL_ON) &&
      (ME_ADMIN ? (!i.userOnly && (!i.adminOnly || adminLinkOk(i))) : !i.adminOnly) &&
      (VIA_PIN ? pinOk(i) : !i.pinOnly)
    ),
  })).filter((g) => g.items.length);

  R3Shell.renderMenu(document.querySelector("[data-r3-drawer]"), buildMenu());
  R3Shell.watchSupport();

  const TAB_TITLES = {
    rooms: "임대매물 등록", registry: "등기부 확인", broker: "중개 계약서 사진 등록",
    tax: "세무 · 경비", ops: "A/S 접수", messages: "임차인과 채팅",
    expiry: "만기 통보", notice: "일괄 납입 통보", bldgtax: "부가세 · 소득세 대상 호실 설정",
    mail: "입금 메일 자동 정리",
  };

  // 호실 카드·모달 스타일: index.html 이 브라우저에 캐시돼도 화면이 깨지지 않도록 직접 넣는다
  (function injectStyles() {
    const st = document.createElement("style");
    st.textContent = `
    .rc-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }
    .rc-title { font-size: 17px; }
    .rc-title span { font-size: 15px; margin-left: 4px; }
    .rc-unpaid { font-weight: 800; font-size: 14px; color: var(--ok); }
    .rc-unpaid.is-late { color: var(--err); font-size: 16px; }
    .rl-list { display: grid; grid-template-columns: repeat(1, minmax(0, 1fr)); gap: 6px; margin-bottom: 8px; }
    .rl-wrap { display: flex; flex-direction: row; align-items: stretch; width: 100%; min-width: 0; border-radius: 12px; overflow: hidden; }
    .rl-wrap--ok { background: #bbf7d0; color: #111827; }
    .rl-wrap--wait { background: #fef08a; color: #111827; }
    .rl-wrap--late { background: #fecaca; color: #111827; }
    .rl-wrap--vacant { background: #e2e8f0; color: #334155; }
    .rl-row, .bass-btn.rl-row:not(.bass-btn--primary):not(.is-active) { display: flex; flex-direction: row; align-items: center; justify-content: space-between; gap: 8px; flex: 1 1 auto; width: 100%; min-width: 0; min-height: 46px; padding: 5px 8px 5px 12px !important; text-align: left; border-radius: 12px 0 0 12px !important; border: 0 !important; box-shadow: none !important; line-height: 1.25; overflow: hidden; }
    .rl-more { flex: none; width: 44px; min-height: 46px; padding: 0; border: 0; border-left: 1px solid rgba(15, 23, 42, .12); background: transparent; color: inherit; font-size: 20px; font-weight: 900; line-height: 1; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; -webkit-tap-highlight-color: transparent; }
    .rl-more:hover, .rl-more:active, .rl-more[aria-expanded="true"] { background: rgba(15, 23, 42, .11); }
    .rl-ctx-backdrop { position: fixed; inset: 0; z-index: 1190; background: rgba(15, 23, 42, .14); }
    .rl-ctx-menu { position: fixed; z-index: 1200; min-width: 224px; max-width: min(290px, calc(100vw - 20px)); max-height: min(72vh, 520px); overflow-y: auto; background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 14px; box-shadow: 0 14px 36px rgba(15, 23, 42, .24); padding: 6px; animation: rl-ctx-in .14s ease-out; }
    @keyframes rl-ctx-in { from { opacity: 0; transform: scale(.96) translateY(-4px); } }
    .rl-ctx-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 10px 8px; border-bottom: 1px solid #e2e8f0; margin-bottom: 4px; font-size: 13.5px; font-weight: 800; color: #0f172a; }
    .rl-ctx-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; min-height: 42px; padding: 8px 10px; border: 0; border-radius: 10px; background: transparent; color: #0f172a; font-size: 14px; font-weight: 600; text-align: left; cursor: pointer; line-height: 1.3; }
    .rl-ctx-item:hover, .rl-ctx-item:active { background: #eff6ff; color: #1d4ed8; }
    .rl-ctx-item--danger { color: #b91c1c; }
    .rl-ctx-item--danger:hover, .rl-ctx-item--danger:active { background: #fef2f2; color: #b91c1c; }
    .rl-ctx-item--pink { background: #fce7f3; color: #9d174d; border: 1px solid #fbcfe8; }
    .rl-ctx-item--pink:hover, .rl-ctx-item--pink:active { background: #fbcfe8; color: #831843; }
    .rl-ctx-badge--pink { background: #fbcfe8 !important; color: #9d174d !important; border: 1px solid #f9a8d4 !important; }
    .rl-ctx-sep { height: 1px; background: #e2e8f0; margin: 4px 6px; }
    .rl-no { font-size: 17px; font-weight: 800; white-space: nowrap; } .rl-no small { font-size: 13px; font-weight: 700; }
    .rl-who { font-size: 14px; flex: 1; min-width: 0; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .rl-st { font-size: 14px; font-weight: 800; white-space: nowrap; } .rl-day { font-size: 13px; opacity: .9; white-space: nowrap; }
    .rl-row--ok, .bass-btn.rl-row.rl-row--ok:not(.bass-btn--primary):not(.is-active), .bass-btn.rl-row.rl-row--ok:not(.bass-btn--primary):not(.is-active):hover { background: #bbf7d0 !important; color: #111827 !important; }
    .rl-row--wait, .bass-btn.rl-row.rl-row--wait:not(.bass-btn--primary):not(.is-active), .bass-btn.rl-row.rl-row--wait:not(.bass-btn--primary):not(.is-active):hover { background: #fef08a !important; color: #111827 !important; }
    .rl-row--late, .bass-btn.rl-row.rl-row--late:not(.bass-btn--primary):not(.is-active), .bass-btn.rl-row.rl-row--late:not(.bass-btn--primary):not(.is-active):hover { background: #fecaca !important; color: #111827 !important; }
    .rl-row--vacant, .bass-btn.rl-row.rl-row--vacant:not(.bass-btn--primary):not(.is-active), .bass-btn.rl-row.rl-row--vacant:not(.bass-btn--primary):not(.is-active):hover { background: #e2e8f0 !important; color: #334155 !important; }
    /* 🧮 관리비 산출근거 모달 */
    .r3-modal__body .mf-row { display: grid !important; grid-template-columns: 26px minmax(0, 1fr) 40px 104px; gap: 6px; align-items: center; padding: 6px 2px; border-bottom: 1px solid var(--border, #e2e8f0); font-size: 14px; cursor: pointer; }
    .r3-modal__body .mf-row input[type=checkbox] { width: 20px; height: 20px; margin: 0; }
    .mf-cnt { font-size: 12.5px; opacity: .7; text-align: right; } .mf-amt { text-align: right; white-space: nowrap; }
    .mf-sum { padding: 10px 12px; border-radius: 12px; background: #eef2ff; margin: 10px 0; font-size: 14px; line-height: 1.7; } .mf-big { font-size: 18px; font-weight: 800; }
    .bass-btn.rl-row:active { filter: brightness(.92); transform: none !important; }
    .app-shell { padding: 8px 8px 60px !important; }
    .app-shell > header.bass-card, .app-shell header.bass-card { padding: 8px 10px !important; margin-bottom: 8px !important; }
    #bldgTitle { font-size: 17px !important; margin: 0 !important; line-height: 1.25; }
    #bldgClock { font-size: 12.5px; font-weight: 600; opacity: .75; white-space: nowrap; text-align: right; line-height: 1.2; margin-bottom: 3px; }
    .r3-modal__box--wide { max-width: min(980px, 100vw) !important; width: 100% !important; max-height: 96vh !important; overflow: auto !important; -webkit-overflow-scrolling: touch; overscroll-behavior: contain; padding: 10px 12px 20px !important; }
    .r3-modal__box--wide .r3-modal__head { position: sticky; top: 0; left: 0; z-index: 3; background: #fff; }
    .r3-modal__box--wide .r3-modal__x, .r3-modal__box--wide .r3-modal__head .bass-btn.r3-modal__x:not(.bass-btn--primary):not(.is-active), .r3-modal__box--wide .r3-modal__head .bass-btn.r3-modal__x:not(.bass-btn--primary):not(.is-active):hover { min-width: 76px; min-height: 40px; padding: 0 12px; font-size: 15px; font-weight: 800; background: #dc2626 !important; border-color: #dc2626 !important; color: #fff !important; }
    .r3-modal__box--wide .r3-modal__body { min-width: 380px; }
    .r3-modal__box--wide .pr-list { max-height: none; overflow: visible; }
    .r3-modal__box--wide .pr { grid-template-columns: 44px 136px 112px 52px; justify-content: start; gap: 5px; font-size: 13px; min-height: 36px; padding: 2px 0; }
    .r3-modal__box--wide .pr .bass-input { font-size: 13px; padding: 2px 4px; min-height: 30px !important; height: 30px; border-radius: 8px; }
    .r3-modal__box--wide .pr .pr-del { min-height: 30px; height: 30px; padding: 0; font-size: 12.5px; }
    .r3-modal__box--wide #roomModalClose { position: sticky; left: 0; width: calc(100vw - 48px) !important; max-width: 100%; }
    .app-shell [data-r3-burger] { width: 40px !important; height: 40px !important; min-height: 40px !important; min-width: 40px !important; padding: 0 !important; border-radius: 12px !important; font-size: 18px !important; }
    .app-shell .r3-bell { width: 40px !important; height: 40px !important; min-height: 40px !important; border-radius: 12px !important; font-size: 18px !important; }
    #kpiGrid.kpi-strip { display: grid !important; grid-template-columns: 1fr 1fr; gap: 2px 10px !important; margin: 6px 0 0 !important; font-size: 13px !important; }
    #kpiGrid.kpi-strip strong { font-size: 14.5px !important; }
    #kpiGrid.kpi-strip span { white-space: normal !important; }
    #kpiGrid.kpi-strip .bass-badge { font-size: 12px !important; padding: 1px 6px !important; }
    .r3-zone--sms { padding: 8px 10px !important; margin-bottom: 8px !important; }
    .r3-zone--sms .r3-lbl { font-size: 13.5px !important; margin-bottom: 4px !important; }
    .r3-zone--sms textarea { min-height: 84px !important; padding: 6px 8px !important; font-size: 14px !important; }
    .r3-zone--sms .bass-btn { min-height: 40px !important; font-size: 15px !important; margin-top: 6px !important; }
    .rc-btns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin-bottom: 8px; }
    .rc-btns .bass-btn { padding-left: 4px; padding-right: 4px; white-space: nowrap; }
    .rc-warn { background: hsl(46 96% 56%) !important; color: #2b2300 !important; border-color: hsl(46 96% 40%) !important; font-weight: 800; }
    .rc-info { font-size: 13px; line-height: 1.75; padding: 10px 12px; border-radius: 10px; background: #f8fafc; color: #1e293b; border: 1px solid #cbd5e1; margin-top: 8px; }
    .rc-subbtns { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }
    .r3-zone { transition: border-color .15s ease; }
    .r3-zone--sms { background: linear-gradient(155deg, hsla(206, 86%, 52%, .13), hsla(222, 68%, 40%, .07)) !important; border-color: hsla(206, 86%, 60%, .38) !important; border-left: 4px solid hsl(199, 92%, 56%) !important; }
    .r3-zone--notice { background: hsla(38, 92%, 50%, .10) !important; border-color: hsla(38, 92%, 56%, .45) !important; border-left: 4px solid var(--warn) !important; }
    .r3-zone--room { background: linear-gradient(155deg, hsla(232, 56%, 58%, .11), hsla(240, 44%, 44%, .06)) !important; border-color: hsla(232, 56%, 64%, .35) !important; border-left: 4px solid hsl(235, 80%, 70%) !important; }
    .r3-zone--pay { background: linear-gradient(155deg, hsla(156, 68%, 42%, .12), hsla(172, 60%, 34%, .06)) !important; border-color: hsla(156, 68%, 52%, .36) !important; border-left: 4px solid var(--ok) !important; }
    .r3-zone--late { background: hsla(356, 82%, 56%, .09) !important; border-color: hsla(356, 82%, 60%, .40) !important; border-left: 4px solid var(--err) !important; }
    .r3-subbox { padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border); }
    .r3-subbox--direct { background: hsla(156, 54%, 46%, .09); border-color: hsla(156, 54%, 56%, .26); margin: 8px 0 12px; }
    .r3-subbox--msg { background: hsla(262, 64%, 58%, .10); border-color: hsla(262, 64%, 66%, .28); margin-top: 12px; }
    .pr { display: grid; grid-template-columns: 40px minmax(0, 1.45fr) minmax(0, 1fr) minmax(0, 1fr) 52px; gap: 4px; align-items: center; padding: 3px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; min-height: 44px; }
    .pr .bass-input { padding: 6px 4px; font-size: 12.5px; min-width: 0; width: 100%; }
    .pr .pr-del { padding: 4px; min-height: 40px; font-size: 12.5px; color: var(--err); width: 100%; }
    .pr--head { font-weight: 700; min-height: 0; border-bottom: 2px solid var(--border); }
    .pr--un, .pr--ok { grid-template-columns: 44px 1fr; padding-left: 6px; padding-right: 6px; border-radius: 6px; }
    .pr--un { background: hsla(356, 82%, 56%, .10); }
    .pr--ok { background: hsla(152, 65%, 45%, .06); }
    .pr--un .pr__un { color: var(--err); font-weight: 800; }
    .pr-list { max-height: 205px; overflow-y: auto; border-bottom: 1px solid var(--border); }
    `;
    document.head.appendChild(st);
  })();

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  function expiryStatusLabel(ec) {
    if (!ec) return "";
    const map = {
      AWAITING_TENANT: "📨 전출계획 회신 대기",
      MOVEOUT_PLANNED: `📦 만기전출 예정 (${ec.planned_move_out_date || "미정"})`,
      STAY_CONFIRMED: "🏠 전출의사 없음 (연장 희망)",
      RENEWAL_PREVIEWED: "📝 연장 재계약 예고 발송됨",
      RENEWAL_ACKNOWLEDGED: "✅ 임차인 연장예고 확인·동의",
    };
    return map[ec.status] || ec.status;
  }

  // 🔐 간편 비밀번호로 들어온 화면: 서버가 어차피 막지만, 눌러서 거절당하지 않도록 미리 감추거나 끄고 안내 띠를 보인다
  function pinLimit(root) {
    if (!VIA_PIN || !root) return;
    const pin = (state.dash && state.dash.pin) || {};
    const off = (el, why) => { el.disabled = true; el.title = why; el.style.opacity = ".45"; };
    root.querySelectorAll("[data-act]").forEach((b) => { b.style.display = "none"; });
    root.querySelectorAll(".pr[data-pay] input").forEach((el) => off(el, "수정은 아이디·비밀번호 로그인에서만 됩니다"));
    root.querySelectorAll(".pr-del").forEach((el) => { el.style.display = "none"; });
    root.querySelectorAll(".cn-ok, .cn-no, .cn-code").forEach((el) => off(el, "연결 승인은 아이디·비밀번호 로그인에서만 됩니다"));
    if (!pin.pay_ok) root.querySelectorAll("#pmDirect, #pmAmt, #bankSmsGo, #bankSms, .nt-ok, .nt-no").forEach((el) => off(el, "수납은 '수납 허용'을 켠 기기에서만 됩니다"));
  }

  function applyPinBar() {
    if (!VIA_PIN) return;
    let bar = document.getElementById("pinBar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "pinBar";
      bar.style.cssText = "margin:8px 0;padding:10px 12px;border-radius:12px;border:1px solid var(--warn);background:rgba(234,179,8,.14);font-size:13.5px;line-height:1.55;";
      elTabContent.parentNode.insertBefore(bar, elTabContent);
    }
    const pin = (state.dash && state.dash.pin) || {};
    const pay = pin.pay_ok
      ? `수납 허용 기기 — 오늘 남은 한도 <strong>${pin.left_n}건 · ${fmtWon(pin.left_amount)}</strong>`
      : "이 기기는 <strong>수납 불가</strong>(바로가기 설정에서 '수납 허용'을 켤 수 있습니다)";
    bar.innerHTML = `🔐 간편 비밀번호로 접속 중 · 조회·채팅·A/S 처리만 가능 · ${pay}<br><span class="bass-muted">계약·세무·내보내기·설정은 </span><a href="#" id="pinBarLogin" style="font-weight:700;">아이디·비밀번호로 로그인</a>`;
    document.getElementById("pinBarLogin").addEventListener("click", (e) => { e.preventDefault(); fullLogin(); });
  }

  function fullLogin() {
    try {
      window.localStorage.removeItem("bass_token");
      window.localStorage.removeItem("rent3_session");
      window.localStorage.removeItem("rent3_refresh_token");
    } catch (_) {}
    window.location.href = "/rent3/?login=1";
  }

  const closeDrawer = () => {
    const dr = document.querySelector("[data-r3-drawer]");
    if (dr) dr.classList.remove("is-open");
  };

  const roomOf = (no) => ((state.dash && state.dash.rooms) || []).find((x) => x.room_no === no);
  const shortYm = (ym) => String(ym).slice(2).replace("-", ".");
  const dotDate = (s) => String(s || "").replace(/-/g, ".");
  const bldgName = () => (state.dash && state.dash.building && state.dash.building.building_name) || "";
  const $m = (id) => document.getElementById(id);

  // 납부 예정 금액 = 납입일 당일까지 아직 안 낸 달의 미입금(연체 아님)
  function dueSoonOf(r) {
    return r.ledger ? (r.ledger.months || []).filter((m) => m.phase === "DUE_SOON" && m.unpaid > 0).reduce((a, m) => a + Number(m.unpaid), 0) : 0;
  }

  // 미납 횟수 = 납부일(매월 전입일)이 이미 지난 달 중 아직 못 낸 달 수
  function unpaidCount(r) {
    if (!r.ledger) return 0;
    const bs = r.billing_schedule;
    const cur = ((state.dash && state.dash.today) || "").slice(0, 7);
    const day = Number(((state.dash && state.dash.today) || "").slice(8, 10)) || 0;
    return (r.ledger.months || []).filter((m) => {
      if (!(m.unpaid > 0)) return false;
      if (m.phase) return m.phase === "OVERDUE";
      if (m.ym === cur && bs && bs.payment_day && day < bs.payment_day) return false;
      return true;
    }).length;
  }

  function receiptText(pay, remain) {
    return `[${bldgName()}] ${pay.room_no}호 ${pay.payer_name || ""}님, ${dotDate(pay.paid_date)} ${fmtWon(pay.total_amount)} 입금 확인했습니다. ` +
      (remain > 0 ? `현재 남은 미납액은 ${fmtWon(remain)}입니다.` : "미납 없이 정상 납부 상태입니다.") + " 감사합니다.";
  }

  function unpaidText(r) {
    const rows = ((r.ledger && r.ledger.months) || []).filter((m) => m.unpaid > 0);
    const list = rows.map((m) => `${m.ym} ${fmtWon(m.unpaid)}`).join(", ");
    return `[${bldgName()}] ${r.room_no}호 ${(r.contract && r.contract.tenant_name) || ""}님, 아래 월세가 아직 확인되지 않았습니다.\n${list}\n합계 ${fmtWon(r.ledger.total_unpaid)}. 확인 후 입금 부탁드립니다.`;
  }

  // 수납확인 문자 복사 → 임차인 문자창 자동 실행 (모달 없이 바로 문자창 오픈)
  async function sendReceiptSms(rn, pay, remain) {
    const r = roomOf(rn);
    const ct = r && r.contract;
    const phone = (ct && ct.tenant_phone) || (r && r.tenant && r.tenant.phone) || "";
    const msgVal = state.receiptMsg[rn] || receiptText(pay, remain || 0);
    await R3.copyAndSms(msgVal, phone);
  }

  async function downloadWithAuth(apiPath, fallbackFilename, openInNewTab = false) {
    try {
      const headers = {};
      const tk = Bass.token();
      if (tk) headers["Authorization"] = "Bearer " + tk;
      const resp = await fetch(`${Bass.apiBase(APP_ID)}${apiPath}`, { headers });
      if (!resp.ok) {
        let msg = `다운로드 실패 (${resp.status})`;
        try {
          const errJson = await resp.json();
          if (errJson && errJson.message) msg = errJson.message;
        } catch (_) {}
        throw new Error(msg);
      }
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      if (openInNewTab) {
        window.open(blobUrl, "_blank", "noopener");
        setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        return;
      }
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = fallbackFilename || "download.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);
    } catch (e) {
      Bass.showError(e);
    }
  }

  window.R3L = {
    APP_ID, params, REG_FRESH_DAYS, CHECK_STYLE,
    LESSOR_MENU, ME_ADMIN, adminLinkOk, VIA_PIN, PIN_TABS, TAB_TITLES,
    elTabContent, state, buildMenu,
    fmtWon, expiryStatusLabel, pinLimit, applyPinBar, fullLogin, closeDrawer,
    roomOf, shortYm, dotDate, bldgName, $m, dueSoonOf, unpaidCount,
    receiptText, unpaidText, sendReceiptSms, downloadWithAuth,
    copyText: R3.copyText, copyAndSms: R3.copyAndSms, openModal: R3.openModal, closeModal: R3.closeModal,
  };
})();
