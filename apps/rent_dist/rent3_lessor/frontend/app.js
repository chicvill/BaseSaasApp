/* apps/rent3_lessor/frontend/app.js — 임대인 전용 자산·FIFO 수납·세무·운영 클라이언트 */
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
  let currentBranch = params.get("branch") || (savedSession && savedSession.role === "lessor" && savedSession.branch ? savedSession.branch : "main");
  let currentTab = params.get("tab") || "ledger";
  let dash = null;
  let brokerTargetRoomNo = "";
  let brokerPhotoDataUrl = "";
  let brokerPhotoFilename = "";
  const openPhotoRooms = new Set();
  const openExpiryRooms = new Set();
  const closedExpiryRooms = new Set();
  let selectedAsOfDate = "";

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

  const elTabContent = document.getElementById("tabContent");

  // ☰ 메뉴: 첫 화면(수납 현황) 외의 모든 기능은 여기서 들어간다. 항목을 빼면 기능에 닿을 수 없으니 test_52 가 목록을 점검한다.
  const LESSOR_MENU = [
    { items: [{ label: "💰 수납 현황", tab: "ledger" }, { label: "📥 납부현황 엑셀(CSV)", id: "btnLedgerCsv" }] },
    { title: "계약 · 매물", items: [
      { label: "🤝 직거래 계약", href: "/rent3_contract/?role=lessor" },
      { label: "📸 중개 계약서 사진 등록", tab: "broker" },
      { label: "🏷️ 임대매물 등록", tab: "rooms" },
    ] },
    { title: "세무 · 경비", items: [{ label: "🧾 세무 허브", tab: "tax" }] },
    { title: "운영", items: [
      { label: "💬 메시지", tab: "messages" },
      { label: "🔧 A/S 접수", tab: "ops" },
      { label: "⚖️ 만기 통보", tab: "expiry" },
      { label: "🔔 일괄 납입 통보", tab: "notice" },
      { label: "📨 기존 임차인 앱 연결 안내 문자", id: "btnConnectMsg" },
    ] },
    { title: "건물", items: [
      { label: "🏢 건물 정보 수정", id: "btnEditBuilding" },
      { label: "📑 부가세 · 소득세 대상 호실 설정", tab: "bldgtax" },
    ] },
    { title: "도움말 · 이동", items: [
      { label: "💬 관리인에게 문의", id: "btnSupportChat", userOnly: true },
      { label: "🛡️ 원격 지원 허용 설정", id: "btnSupportSetting", userOnly: true },
      { label: "📘 이용 매뉴얼", href: "manual.html" },
      { label: "📲 바로가기 · 간편 비밀번호", id: "btnShortcut", userOnly: true },
      { label: "🔓 아이디·비밀번호로 로그인", id: "btnFullLogin", pinOnly: true },
      { label: "🔑 임차인 앱", href: "/rent3_tenant/", adminOnly: true },
      { label: "👑 관리인 앱", href: "/rent3_admin/", adminOnly: true },
      { label: "🌐 Data Pool 관제", href: "/rent3/pool.html", adminOnly: true },
    ] },
  ];
  // 사용자 전용 항목(문의·원격 지원 설정)은 관리인에게, 이동 링크(다른 앱)는 일반 임대인에게 보이지 않는다
  const ME_ADMIN = R3Shell.isAdminSession();
  // 관리인: 다른 앱으로 건너가는 링크(임대인·임차인 앱)는 입장 없이는 막히므로 숨기고, 원격 지원 입장 중일 때만 돌아갈 곳(관리인 앱·Data Pool)을 보인다
  const adminLinkOk = (i) => R3Shell.isAdminProxy() && /^\/(rent3_admin\/|rent3\/pool)/.test(i.href || "");
  // 간편 비밀번호(PIN)로 들어온 화면: 서버가 허용한 일(현황 조회·메시지·시설 처리·수납[허용 기기])만 메뉴에 둔다
  const VIA_PIN = R3Shell.isViaPin();
  const PIN_TABS = ["ledger", "messages", "ops"];
  const pinOk = (i) => i.pinOnly || i.id === "btnShortcut" || i.href === "manual.html" || PIN_TABS.includes(i.tab);
  const MY_MENU = LESSOR_MENU.map((g) => ({ ...g, items: g.items.filter((i) => (ME_ADMIN ? (!i.userOnly && (!i.adminOnly || adminLinkOk(i))) : !i.adminOnly) && (VIA_PIN ? pinOk(i) : !i.pinOnly)) })).filter((g) => g.items.length);
  R3Shell.renderMenu(document.querySelector("[data-r3-drawer]"), MY_MENU);
  R3Shell.watchSupport();
  const TAB_TITLES = { rooms: "임대매물 등록", broker: "중개 계약서 사진 등록", tax: "세무 · 경비", ops: "A/S 접수", messages: "메시지", expiry: "만기 통보", notice: "일괄 납입 통보", bldgtax: "부가세 · 소득세 대상 호실 설정" };

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  // 🔐 간편 비밀번호로 들어온 화면: 서버가 어차피 막지만, 눌러서 거절당하지 않도록 미리 감추거나 끄고 안내 띠를 보인다
  function pinLimit(root) {
    if (!VIA_PIN || !root) return;
    const pin = (dash && dash.pin) || {};
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
    const pin = (dash && dash.pin) || {};
    const pay = pin.pay_ok ? `수납 허용 기기 — 오늘 남은 한도 <strong>${pin.left_n}건 · ${fmtWon(pin.left_amount)}</strong>` : "이 기기는 <strong>수납 불가</strong>(바로가기 설정에서 '수납 허용'을 켤 수 있습니다)";
    bar.innerHTML = `🔐 간편 비밀번호로 접속 중 · 조회·메시지·A/S 처리만 가능 · ${pay}<br><span class="bass-muted">계약·세무·내보내기·설정은 </span><a href="#" id="pinBarLogin" style="font-weight:700;">아이디·비밀번호로 로그인</a>`;
    document.getElementById("pinBarLogin").addEventListener("click", (e) => { e.preventDefault(); fullLogin(); });
  }
  function fullLogin() {
    try { window.localStorage.removeItem("bass_token"); window.localStorage.removeItem("rent3_session"); } catch (_) { /* 저장소를 못 지워도 포털에서 다시 로그인한다 */ }
    window.location.href = "/rent3/?login=1";
  }

  // ── 가맹 신청 패널: 본인인증 → 건물 정보 → 등기부 대조 → 제출 ──
  const CHECK_STYLE = { ok: "var(--ok)", caution: "var(--warn)", high: "var(--err)" };

  async function renderApplication(box, f) {
    const data = await Bass.api(APP_ID, `/franchise/application?branch=${encodeURIComponent(currentBranch)}`);
    const a = data.application, st = a.steps, idn = a.identity, chk = a.registry.check;
    const done = (ok) => (ok ? "✅" : "⬜");
    const esc = Bass.escapeHtml;
    box.innerHTML = `
      <div class="bass-card" style="margin-top:10px;border-left:4px solid var(--warn);padding:12px;font-size:13px;line-height:1.6;">
        <strong>${esc(f.label || "")}</strong> — 매물 공개와 직거래 계약은 가맹 승인 후에 가능합니다. 아래 4단계를 마치고 제출해 주세요.
        ${f.note ? `<div>사유: ${esc(f.note)}</div>` : ""}
        <div style="margin-top:8px;">
          <div><strong>${done(st.identity)} 1. 휴대폰 본인인증</strong>${idn.verified ? ` — ${esc(idn.name)} · ${esc(idn.phone)} ${idn.fake ? "<span class='bass-badge bass-badge--warn'>시연용 가짜 인증</span>" : ""}` : ""}</div>
          ${idn.verified ? "" : `
          <div class="field-grid" style="margin:6px 0;">
            <input id="fiName" class="bass-input" placeholder="실명">
            <input id="fiPhone" class="bass-input" placeholder="휴대폰 010-0000-0000">
            <input id="fiBirth" class="bass-input" placeholder="생년월일 6자리(650312) 또는 8자리">
          </div>
          <button id="btnIdStart" class="bass-btn bass-btn--sm" type="button">인증번호 받기</button>
          <input id="fiCode" class="bass-input" style="width:140px;display:inline-block;" placeholder="인증번호">
          <button id="btnIdConfirm" class="bass-btn bass-btn--sm bass-btn--primary" type="button">인증 확인</button>`}
        </div>
        <div style="margin-top:8px;">
          <div><strong>${done(st.building)} 2. 건물 정보</strong> — 등기부 소재지와 대조하는 기준입니다 (번지 포함).</div>
          <div class="field-grid" style="margin:6px 0;">
            <input id="fbName" class="bass-input" placeholder="건물명" value="${esc(a.building.building_name)}">
            <input id="fbAddr" class="bass-input" placeholder="실제 주소 (예: 인천 서해구 석남동 507-34)" value="${esc(a.building.address)}">
            <input id="fbValue" type="number" class="bass-input" placeholder="건물 가치(원, 선택)" value="${a.building.property_value || ""}">
          </div>
          <button id="btnBldgSave" class="bass-btn bass-btn--sm" type="button">건물 정보 저장</button>
        </div>
        <div style="margin-top:8px;">
          <div><strong>${done(st.registry)} 3. 등기부 대조</strong> — 인터넷등기소에서 받은 PDF·캡처를 올리세요.</div>
          <input id="frFile" type="file" accept="image/*,application/pdf" class="bass-input" ${st.identity ? "" : "disabled"}>
          <label class="check-row" style="margin:4px 0;"><span style="font-size:12px;">등기부 내용이 Google Gemini로 전송되어 자동 대조되는 것에 동의합니다 (미동의 시 관리자가 직접 확인).</span><input type="checkbox" id="frConsent"></label>
          <button id="btnRegUpload" class="bass-btn bass-btn--sm bass-btn--primary" type="button" ${st.identity ? "" : "disabled"}>올리고 대조하기</button>
          ${chk ? `<div style="margin-top:6px;border-left:4px solid ${CHECK_STYLE[chk.level] || "var(--warn)"};padding:6px 10px;background:hsl(232 30% 12% / .55);border-radius:8px;">${esc(chk.message)}${chk.auto_pass ? "" : "<br><span class='bass-muted'>관리자가 원본을 직접 확인한 뒤 결정합니다.</span>"}</div>` : ""}
        </div>
        <div style="margin-top:10px;">
          <div><strong>${done(st.submitted)} 4. 가맹 신청 제출</strong>${a.applied_at ? ` — ${esc(String(a.applied_at).slice(0, 16).replace("T", " "))} 접수, 심사 대기 중` : ""}</div>
          <button id="btnApply" class="bass-btn bass-btn--primary" type="button" ${a.ready_to_submit ? "" : "disabled"} style="margin-top:4px;">${a.applied_at ? "다시 제출" : "가맹 신청 제출"}</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">호실 ${f.room_count}/${f.room_limit}실 등록 · 호실 정보는 승인 전에도 미리 준비할 수 있습니다.</div>
      </div>`;
    const $q = (id) => document.getElementById(id);
    const who = () => ({ branch: currentBranch, name: $q("fiName").value.trim(), phone: $q("fiPhone").value.trim(), birth: $q("fiBirth").value.trim() });
    const act = (id, fn, rerender = true) => {
      const el = $q(id);
      if (el) el.addEventListener("click", function () { Bass.withLock(this, async () => { await fn(); if (rerender) await renderFranchiseBanner(); }); });
    };
    act("btnIdStart", async () => {  // 입력 중인 이름·번호가 지워지지 않도록 다시 그리지 않는다
      const r = await Bass.api(APP_ID, "/franchise/identity/start", { method: "POST", body: who() });
      Bass.updateAppAlert("success", r.message, r.hint || "");
    }, false);
    act("btnIdConfirm", async () => {
      const r = await Bass.api(APP_ID, "/franchise/identity/confirm", { method: "POST", body: { ...who(), code: $q("fiCode").value.trim() } });
      Bass.updateAppAlert("success", r.message);
    });
    act("btnBldgSave", async () => {
      const r = await Bass.api(APP_ID, "/franchise/building", { method: "POST", body: {
        branch: currentBranch, building_name: $q("fbName").value.trim(), address: $q("fbAddr").value.trim(), property_value: Number($q("fbValue").value || 0) } });
      Bass.updateAppAlert("success", r.message);
    });
    act("btnRegUpload", async () => {
      const file = $q("frFile").files[0];
      if (!file) { Bass.updateAppAlert("warning", "등기부 파일(PDF·캡처)을 먼저 골라 주세요."); return; }
      if (file.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다."); return; }
      const dataUrl = await new Promise((res, rej) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.onerror = rej; rd.readAsDataURL(file); });
      const r = await Bass.api(APP_ID, "/franchise/registry", { method: "POST", body: { branch: currentBranch, attachment_data_url: dataUrl, consent: $q("frConsent").checked } });
      Bass.updateAppAlert(r.level === "success" ? "success" : "warning", r.message);
    });
    act("btnApply", async () => {
      const r = await Bass.api(APP_ID, "/franchise/submit", { method: "POST", body: { branch: currentBranch } });
      Bass.updateAppAlert("success", r.message);
    });
  }

  async function renderFranchiseBanner() {
    const box = document.getElementById("franchiseBanner");
    if (!box) return;
    try {
      const st = await Bass.api(APP_ID, `/status?branch=${encodeURIComponent(currentBranch)}`);
      const f = st.franchise || {};
      if (f.open) { box.innerHTML = ""; return; }
      if (f.status === "PENDING_APPROVAL" || f.status === "REJECTED") { await renderApplication(box, f); return; }
      const msg = {
        PENDING_APPROVAL: "가맹 심사 중입니다. 승인되기 전에는 호실 정보를 미리 준비할 수 있고, 매물 공개와 직거래 계약은 승인 후에 가능합니다.",
        SUSPENDED: "이용이 정지되었습니다. 매물이 모두 비공개로 바뀌었고 새 계약을 시작할 수 없습니다.",
        REJECTED: "가맹 심사가 반려되었습니다.",
      }[f.status] || "가맹 승인 후에 매물을 공개할 수 있습니다.";
      box.innerHTML = `<div class="bass-card" style="margin-top:10px;border-left:4px solid var(--warn);padding:10px 12px;font-size:13px;line-height:1.6;">
        <strong>${Bass.escapeHtml(f.label || "")}</strong> — ${msg}${f.note ? `<br>사유: ${Bass.escapeHtml(f.note)}` : ""}
        <br><span class="bass-muted">호실 ${f.room_count}/${f.room_limit}실 등록</span></div>`;
    } catch (e) { box.innerHTML = ""; }
  }

  // 머리글: 건물이름[아이디] 현재 일시 (1분마다 갱신)
  let hdrName = "";
  const tickClock = R3Shell.startClock(document.getElementById("bldgTitle"), () => hdrName);

  async function refresh() {
    const qAsOf = selectedAsOfDate ? `&as_of_date=${encodeURIComponent(selectedAsOfDate)}` : "";
    dash = await Bass.api(APP_ID, `/dashboard?branch=${encodeURIComponent(currentBranch)}${qAsOf}`);
    document.getElementById("poolRevBadge").textContent = `JSON Pool v${dash.pool_revision}`;
    renderFranchiseBanner();
    applyPinBar();

    const b = dash.building || {};
    const l = dash.lessor || {};
    hdrName = `${b.building_name || ""}${l.username ? `[${l.username}]` : ""}`;
    tickClock();
    R3Shell.proxyBanner(`${b.building_name || ""} ${selRoomNo ? selRoomNo + "호 " : ""}(임대인 입장)`);   // 관리인 대리 접속 때만 보인다

    const fin = dash.financial_summary || {};
    const ymMonth = Number((dash.target_ym || "").slice(5, 7)) || (new Date().getMonth() + 1);
    const mTarget = Number(dash.monthly_target || 0);
    const mPaid = Number(dash.monthly_collected || 0);
    const mRate = Number(dash.monthly_collection_rate ?? (mTarget > 0 ? Math.round((mPaid * 100) / mTarget) : 0));
    const mUnpaid = Number(dash.monthly_unpaid || 0);
    const totalUnpaid = Number(dash.total_unpaid_branch || 0);
    const unpaidColor = totalUnpaid > 0 ? "var(--err)" : "var(--ok)";
    const unpaidExtra = totalUnpaid > mUnpaid
      ? ` <span class="bass-muted" style="font-size:12.5px;">(당월 ${fmtWon(mUnpaid)})</span>`
      : "";
    document.getElementById("kpiGrid").innerHTML =
      `<span>${ymMonth}월 목표액 <strong>${fmtWon(mTarget)}</strong></span>` +
      `<span>수납액 <strong style="color:var(--ok);">${fmtWon(mPaid)} (${mRate}%)</strong></span>` +
      `<span>미납액 <strong style="color:${unpaidColor};">${fmtWon(totalUnpaid)}</strong>${unpaidExtra}</span>` +
      `<span class="bass-muted">가동 ${fin.occupied_rooms || 0}/${fin.total_rooms || 0}실 (${fin.occupancy_rate || 0}%)</span>`;

    renderTab();
  }

  function switchTab(tabName) {
    if (tabName !== "messages") { msgRoom = ""; }
    if (tabName === "tax" && currentTab !== "tax") { taxView = "hub"; taxMonth = 0; }   // 메뉴에서 들어오면 세무 허브부터
    currentTab = tabName;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === tabName));
    renderTab();
  }

  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    switchTab(btn.dataset.tab);
  });

  function renderTab() {
    if (!dash) return;
    if (currentTab !== "ledger" && !TAB_TITLES[currentTab]) currentTab = "ledger";
    if (VIA_PIN && !PIN_TABS.includes(currentTab)) currentTab = "ledger";   // 간편 비밀번호로는 허용된 화면만
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === currentTab));
    document.getElementById("backBar").hidden = currentTab === "ledger";
    document.getElementById("backTitle").textContent = TAB_TITLES[currentTab] || "";
    if (currentTab === "ledger") renderLedgerTab();
    else if (currentTab === "broker") renderBrokerTab();
    else if (currentTab === "tax") renderTaxTab();
    else if (currentTab === "ops") renderOpsTab();
    else if (currentTab === "expiry") renderExpiryTab();
    else if (currentTab === "notice") renderNoticeTab();
    else if (currentTab === "messages") renderMessagesTab();
    else if (currentTab === "bldgtax") renderBldgTaxTab();
    else if (currentTab === "rooms") renderRoomsTab();
    pinLimit(elTabContent);
  }
  document.getElementById("btnBackLedger").addEventListener("click", () => switchTab("ledger"));

  // ── 수납 현황 탭: 호실 풀다운 하나 + 버튼 네 개(수납내역·계약내용·만기통보·전출). 자세한 설명은 이용 매뉴얼에 둔다. ──
  // 호실 카드·모달 스타일: index.html 이 브라우저에 캐시돼도 화면이 깨지지 않도록 app.js 가 직접 넣는다
  (function () {
    const st = document.createElement("style");
    st.textContent = `
    .rc-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }
    .rc-title { font-size: 17px; }
    .rc-title span { font-size: 15px; margin-left: 4px; }
    .rc-unpaid { font-weight: 800; font-size: 14px; color: var(--ok); }
    .rc-unpaid.is-late { color: var(--err); font-size: 16px; }
    .rc-btns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin-bottom: 8px; }
    .rc-btns .bass-btn { padding-left: 4px; padding-right: 4px; white-space: nowrap; }
    .rc-warn { background: hsl(46 96% 56%) !important; color: #2b2300 !important; border-color: hsl(46 96% 40%) !important; font-weight: 800; }
    .rc-info { font-size: 13px; line-height: 1.75; padding: 10px 12px; border-radius: 10px; background: hsla(224, 34%, 12%, .42); border: 1px solid hsla(224, 35%, 62%, .18); margin-top: 8px; }
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
    .pr { display: grid; grid-template-columns: 40px minmax(0, 1.45fr) minmax(0, 1fr) minmax(0, 1fr) 44px; gap: 4px; align-items: center; padding: 3px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; min-height: 44px; }
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
  let selRoomNo = params.get("room") || "";   // 관리인 원격 지원 카드가 호실을 지정해서 들어온다
  let payFlash = null;     // 방금 문자로 수납한 결과 안내({no, text}) — 그 호실의 수납 화면 맨 위에 한 번 보인다
  const receiptMsg = {};   // 호실 → 임차인에게 보낼 문자 초안(수납 직후 영수 문구)

  const roomOf = (no) => (dash.rooms || []).find((x) => x.room_no === no);
  const digitsOf = (s) => String(s || "").replace(/\D/g, "");
  const shortYm = (ym) => String(ym).slice(2).replace("-", ".");
  const dotDate = (s) => String(s || "").replace(/-/g, ".");
  const bldgName = () => (dash.building && dash.building.building_name) || "";

  // 미납 횟수 = 납부일(매월 전입일)이 이미 지난 달 중 아직 못 낸 달 수
  function unpaidCount(r) {
    if (!r.ledger) return 0;
    const bs = r.billing_schedule;
    const cur = (dash.today || "").slice(0, 7);
    const day = Number((dash.today || "").slice(8, 10)) || 0;
    return (r.ledger.months || []).filter((m) => {
      if (!(m.unpaid > 0)) return false;
      if (m.ym === cur && bs && bs.payment_day && day < bs.payment_day) return false;
      return true;
    }).length;
  }

  const copyText = R3.copyText;
  const copyAndSms = R3.copyAndSms;
  const closeModal = R3.closeModal;
  const openModal = R3.openModal;

  const $m = (id) => document.getElementById(id);
  const msgOf = (n, v) => `<textarea id="${n}" class="bass-input r3-msg" rows="6">${Bass.escapeHtml(v)}</textarea>`;

  function receiptText(pay, remain) {
    return `[${bldgName()}] ${pay.room_no}호 ${pay.payer_name || ""}님, ${dotDate(pay.paid_date)} ${fmtWon(pay.total_amount)} 입금 확인했습니다. ` +
      (remain > 0 ? `현재 남은 미납액은 ${fmtWon(remain)}입니다.` : "미납 없이 정상 납부 상태입니다.") + " 감사합니다.";
  }

  function unpaidText(r) {
    const rows = ((r.ledger && r.ledger.months) || []).filter((m) => m.unpaid > 0);
    const list = rows.map((m) => `${m.ym} ${fmtWon(m.unpaid)}`).join(", ");
    return `[${bldgName()}] ${r.room_no}호 ${(r.contract && r.contract.tenant_name) || ""}님, 아래 월세가 아직 확인되지 않았습니다.\n${list}\n합계 ${fmtWon(r.ledger.total_unpaid)}. 확인 후 입금 부탁드립니다.`;
  }

  // 📵 분실 폰: 그 임차인의 간편 비밀번호·바로가기를 모두 해제
  function revokeShortcuts(no) {
    if (!window.confirm(`${no}호 임차인의 간편 비밀번호와 바로가기를 모두 해제할까요? (비밀번호 로그인은 그대로 됩니다)`)) return;
    Bass.withLock(null, async () => {
      const r = await Bass.api(APP_ID, "/tenant-shortcuts/revoke-all", { method: "POST", body: { branch: currentBranch, room_no: no } });
      Bass.updateAppAlert("success", r.message);
    });
  }

  // 📲 입주 안내 카드: 1회용 링크(QR). 임차인이 비밀번호·간편 비밀번호를 정하고 홈 화면에 추가하도록 안내한다. 링크 원문은 지금 한 번만 보인다.
  function showInviteModal(no) {
    Bass.withLock(null, async () => {
      const d = await Bass.api(APP_ID, "/tenant-invite", { method: "POST", body: { branch: currentBranch, room_no: no } });
      const link = window.location.origin + d.url;
      let svg = "";
      try { const qr = qrcode(0, "M"); qr.addData(link); qr.make(); svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); } catch (_) { svg = ""; }
      const msg = `[${d.building_name} ${d.room_no}호] ${d.tenant_name}님, 입주를 환영합니다. 아래 링크에서 비밀번호를 정하면 바로 시작할 수 있습니다(7일 안, 1회).\n${link}`;
      const body = openModal(`📲 ${Bass.escapeHtml(no)}호 입주 안내 카드`, `
        <div style="text-align:center;"><div style="max-width:220px;margin:0 auto;background:#fff;padding:6px;border-radius:8px;">${svg || "QR 을 만들지 못했습니다. 아래 링크를 보내 주세요."}</div></div>
        <div class="bass-muted" style="font-size:12.5px;margin:8px 0;">${Bass.escapeHtml(d.tenant_name)}님에게 QR 을 보여 주거나 문자로 보내세요. <strong>7일 안에 한 번</strong> 쓸 수 있고, 다시 만들면 이전 링크는 못 씁니다.</div>
        <textarea id="invMsg" class="bass-input r3-msg" rows="5" readonly>${Bass.escapeHtml(msg)}</textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
          <button id="invCopy" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">문구 복사</button>
          <button id="invPrint" class="bass-btn" type="button" style="min-height:44px;">🖨 인쇄</button></div>`);
      body.querySelector("#invCopy").addEventListener("click", () => copyText(msg));
      body.querySelector("#invPrint").addEventListener("click", () => {
        const w = window.open("", "_blank");
        if (!w) return;
        w.document.write(`<title>입주 안내 카드</title><body style="font-family:sans-serif;text-align:center;padding:30px;"><h2>${Bass.escapeHtml(d.building_name)} ${Bass.escapeHtml(d.room_no)}호 입주 안내</h2><p>${Bass.escapeHtml(d.tenant_name)}님, 입주를 환영합니다.</p><div style="width:260px;margin:0 auto;">${svg}</div><p>QR 을 카메라로 찍어 비밀번호를 정해 주세요.<br>(7일 안, 1회 사용)</p><p style="font-size:12px;word-break:break-all;">${Bass.escapeHtml(link)}</p></body>`);
        w.document.close(); w.focus(); w.print();
      });
    }).catch(Bass.showError);
  }

  // ① 수납내역(호실 카드 아래에 항상 펼쳐 둔다): 직접 수납(현금 등) · 월별 납부 내역(날짜·금액 고치기) · 수납확인 메시지
  function renderPayInline(no) {
    const r = roomOf(no);
    if (!r || !r.ledger) return;
    const lg = r.ledger;
    const ct = r.contract || {};
    const curY = Number((dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const moveYm = (ct.move_in_date || "").slice(0, 7);
    const floorYm = `${curY - 1}-01`;
    const startYm = moveYm > floorYm ? moveYm : floorYm;
    const byYm = {};
    (r.pays || []).forEach((p) => { (byYm[p.billing_month] = byYm[p.billing_month] || []).push(p); });
    const rows = [];
    [...(lg.months || [])].reverse().forEach((m) => {
      if (m.ym < startYm) return;
      if (m.unpaid > 0) {
        rows.push(`<div class="pr pr--un"><span class="pr__ym">${shortYm(m.ym)}</span><span class="pr__un">미납 ${fmtWon(m.unpaid)}${m.status === "PARTIAL" ? " (일부 납부)" : ""}</span></div>`);
      }
      const ps = byYm[m.ym] || [];
      ps.forEach((p) => rows.push(`<div class="pr" data-pay="${p.id}" data-total="${p.total}"><span class="pr__ym">${shortYm(m.ym)}</span>
        <input type="date" class="bass-input pr-date" value="${Bass.escapeHtml(p.paid_date)}" aria-label="납부일">
        <input type="number" class="bass-input pr-rent" value="${p.rent}" aria-label="월세">
        <input type="number" class="bass-input pr-fee" value="${p.fee}" aria-label="관리비">
        <button type="button" class="bass-btn bass-btn--sm pr-del" aria-label="수납 내역 삭제">삭제</button></div>`));
      if (!ps.length && !(m.unpaid > 0)) {
        rows.push(`<div class="pr pr--ok"><span class="pr__ym">${shortYm(m.ym)}</span><span class="bass-muted">${m.status === "EXEMPT_POSTPAID" ? "입주월(후불) — 익월부터 청구" : "완납"}</span></div>`);
      }
    });
    const oldest = (lg.months || []).find((m) => m.unpaid > 0);
    const defAmt = oldest ? oldest.unpaid : Number(r.monthly_rent || 0) + Number(r.maintenance_fee || 0);
    const draft = receiptMsg[no] || (lg.total_unpaid > 0 ? unpaidText(r) : "");
    const notices = (dash.transfer_notices || []).filter((n) => n.room_no === no);
    const noticeHtml = notices.map((n) => `<div class="bass-card" style="padding:10px;margin-bottom:10px;border-color:var(--warn);" data-notice="${Bass.escapeHtml(n.id)}">
        <div style="font-size:13.5px;font-weight:700;">📨 임차인이 ${fmtWon(n.amount)} 이체했다고 알렸습니다</div>
        <div class="bass-muted" style="font-size:12px;margin:2px 0 6px;">통장에서 입금을 확인한 뒤 수납하세요.${n.memo ? ` 메모: ${Bass.escapeHtml(n.memo)}` : ""}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;"><input type="number" class="bass-input nt-amt" value="${n.amount}" style="width:120px;" aria-label="실제 입금액">
        <button class="bass-btn bass-btn--sm bass-btn--primary nt-ok" type="button" style="min-height:44px;">✅ 입금 확인·수납</button>
        <button class="bass-btn bass-btn--sm nt-no" type="button" style="min-height:44px;">반려</button></div></div>`).join("");
    const host = document.getElementById("payInline");
    if (!host) return;
    const flashHtml = payFlash && payFlash.no === no ? `<div id="payFlashBox" role="status" style="padding:10px 12px;margin-bottom:10px;border-radius:12px;background:rgba(34,197,94,.16);border:1px solid var(--ok);font-size:14px;line-height:1.5;cursor:pointer;">✅ ${Bass.escapeHtml(payFlash.text)}<br><span class="bass-muted" style="font-size:13px;">아래 월별 내역을 확인하고, 맨 아래 <strong>수납확인 메시지</strong>를 임차인에게 보내세요.</span></div>` : "";
    if (flashHtml) {      // 수납 완료 안내: 화면을 터치하면 바로, 아니면 2초 뒤에 닫힌다
      const flashRef = payFlash;
      const dismiss = () => { if (payFlash !== flashRef) return; payFlash = null; const el = document.getElementById("payFlashBox"); if (el) el.remove(); };
      setTimeout(() => document.addEventListener("pointerdown", dismiss, { once: true }), 0);   // 화면 어디를 눌러도 닫힌다(누른 버튼은 그대로 동작)
      setTimeout(dismiss, 2000);
    }
    host.innerHTML = `
      <div style="font-weight:800;font-size:16px;margin-bottom:8px;">💰 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 수납내역</div>
      ${flashHtml}
      ${noticeHtml}
      <div class="r3-subbox r3-subbox--direct">
        <label class="r3-lbl" style="margin:0 0 6px;">직접 수납 (현금 등 은행 문자가 없는 경우)</label>
        <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
          <input id="pmAmt" class="bass-input" type="number" style="width:124px;" value="${defAmt}" aria-label="직접 수납액">
          <button id="pmDirect" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="min-height:44px;">💰 직접 수납</button>
        </div>
      </div>
      <div class="r3-lbl">월별 납부 내역 <span class="bass-muted">(날짜·금액을 고치면 바로 저장)</span></div>
      <div class="pr pr--head"><span class="pr__ym">월</span><span>납부일</span><span>월세</span><span>관리비</span><span></span></div>
      <div class="pr-list">${rows.join("") || '<div class="bass-muted" style="padding:10px;">내역이 없습니다.</div>'}</div>
      <div class="r3-subbox r3-subbox--msg">
        <label class="r3-lbl" style="margin:0 0 6px;">✉️ 임차인에게 보낼 수납확인 메시지</label>
        ${msgOf("pmMsg", draft)}
        <button id="pmCopy" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin-top:8px;min-height:44px;">📋 수납확인 메시지 복사 → 임차인 문자창</button>
      </div>`;
    const body = host;
    pinLimit(host);

    const after = async (res) => {
      Bass.updateAppAlert(res.level || "success", res.message);
      const pay = res.payment || {};
      const rn = pay.room_no || no;
      receiptMsg[rn] = receiptText(pay, (res.ledger && res.ledger.total_unpaid) || 0);
      payFlash = { no: rn, text: `${rn}호 ${fmtWon(pay.total_amount || 0)} 수납 처리 완료 (${dotDate(pay.paid_date || "")})` };
      selRoomNo = rn;
      await refresh();
    };
    body.querySelectorAll("[data-notice]").forEach((card) => {
      const id = card.dataset.notice;
      card.querySelector(".nt-ok").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/transfer-notices/${encodeURIComponent(id)}/confirm`, { method: "POST", body: { branch: currentBranch, amount: Number(card.querySelector(".nt-amt").value || 0) } });
          await after(res);
        });
      });
      card.querySelector(".nt-no").addEventListener("click", function () {
        const why = window.prompt("반려 사유(임차인에게 보입니다)", "입금 내역이 확인되지 않습니다");
        if (why === null) return;
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/transfer-notices/${encodeURIComponent(id)}/reject`, { method: "POST", body: { branch: currentBranch, reason: why } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });
    $m("pmDirect").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        await after(await Bass.api(APP_ID, "/payments/collect", { method: "POST", body: { branch: currentBranch, room_no: no, amount: Number($m("pmAmt").value || 0) } }));
      });
    });
    $m("pmCopy").addEventListener("click", () => copyAndSms($m("pmMsg").value, ct.tenant_phone || (r.tenant && r.tenant.phone)));
    body.querySelectorAll(".pr[data-pay]").forEach((row) => {
      const delBtn = row.querySelector(".pr-del");
      if (delBtn) {
        delBtn.addEventListener("click", () => {
          const dt = row.querySelector(".pr-date").value;
          const tot = Number(row.dataset.total || (Number(row.querySelector(".pr-rent").value || 0) + Number(row.querySelector(".pr-fee").value || 0)));
          if (!window.confirm(`${no}호 ${dt} (${fmtWon(tot)}) 수납 내역을 삭제할까요?`)) return;
          Bass.withLock(delBtn, async () => {
            const res = await Bass.api(APP_ID, "/payments/delete", {
              method: "POST",
              body: { branch: currentBranch, pay_id: row.dataset.pay },
            });
            Bass.updateAppAlert("success", res.message);
            delete receiptMsg[no];
            await refresh();
          });
        });
      }
      row.querySelectorAll("input").forEach((inp) => inp.addEventListener("change", () => {
        Bass.withLock(inp, async () => {
          const res = await Bass.api(APP_ID, "/payments/edit", {
            method: "POST",
            body: {
              branch: currentBranch, pay_id: row.dataset.pay,
              paid_date: row.querySelector(".pr-date").value,
              rent_amount: Number(row.querySelector(".pr-rent").value || 0),
              maintenance_amount: Number(row.querySelector(".pr-fee").value || 0),
            },
          });
          Bass.updateAppAlert("success", res.message);
          delete receiptMsg[no];
          await refresh();
        }).catch(() => {});
      }));
    });
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

  // ② 계약내용: 보기·고치기 + 계약서 사진 + (잔금 확인 전이면) 도어락 교부
  function showContractModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct) return;
    const hasPhoto = Boolean(ct.contract_photo_file);
    const bizNo = ct.tenant_business_number || (r.tenant && r.tenant.business_number) || "";
    const compName = ct.tenant_company_name || (r.tenant && r.tenant.company_name) || "";
    const ceoName = ct.tenant_ceo_name || (r.tenant && r.tenant.ceo_name) || "";
    const bizEmail = ct.tenant_email || (r.tenant && r.tenant.email) || "";
    const bizAddr = ct.tenant_business_address || (r.tenant && r.tenant.business_address) || "";
    const taxReq = Boolean(ct.tax_invoice_requested || bizNo || compName);
    const f = (id, label, val, type, ph) => `<label class="r3-fld">${label}<input id="${id}" class="bass-input" type="${type || "text"}" value="${Bass.escapeHtml(String(val ?? ""))}" ${ph ? `placeholder="${Bass.escapeHtml(ph)}"` : ""}></label>`;
    const body = openModal(`📄 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 계약내용`, `
      <div class="r3-grid">
        ${f("cmIn", "전입일", ct.move_in_date, "date")}
        ${f("cmEx", "만기일", ct.expiry_date, "date")}
        ${f("cmDep", "보증금(원)", ct.deposit, "number")}
        ${f("cmRent", "월세(원)", ct.monthly_rent, "number")}
        ${f("cmFee", "관리비(원)", ct.maintenance_fee, "number")}
        ${f("cmTel", "임차인 전화번호", ct.tenant_phone || (r.tenant && r.tenant.phone) || "", "tel")}
      </div>
      <div class="bass-card" style="margin-top:10px;padding:10px;background:var(--surface-2, #f8fafc);">
        <label style="display:flex;align-items:center;gap:6px;font-weight:700;font-size:13.5px;cursor:pointer;">
          <input id="cmTaxInvReq" type="checkbox" ${taxReq ? "checked" : ""}>
          🧾 세입자가 세금계산서 발행을 요청함 (사무실·법인 기숙사 등 모든 호실 가능)
        </label>
        <div id="cmTaxBizWrap" class="r3-grid" style="display:${taxReq ? "grid" : "none"};margin-top:8px;">
          ${f("cmBizNo", "사업자등록번호", bizNo, "text", "예: 214-86-12345")}
          ${f("cmComp", "상호(법인명)", compName, "text", "예: (주)시크디자인")}
          ${f("cmCeo", "대표자명 (필수·임차인명 자동대체 안함)", ceoName, "text", "예: 홍길동")}
          ${f("cmEmail", "세금계산서 수신 이메일", bizEmail, "email", "예: tax@company.co.kr")}
          ${f("cmBizAddr", "사업자등록상 주소 (미입력 시 호실 주소)", bizAddr, "text", "법인 본점 등 주소가 다를 때 입력")}
        </div>
      </div>
      <label class="r3-fld" style="margin-top:8px;">메모<textarea id="cmMemo" class="bass-input" rows="3">${Bass.escapeHtml(ct.lessor_memo || "")}</textarea></label>
      <div class="bass-muted" style="font-size:12.5px;margin:8px 0;">
        계약일 ${Bass.escapeHtml(ct.contract_date || "-")} · ${Bass.escapeHtml(ct.lease_type || "")} · ${Bass.escapeHtml(ct.pay_timing || "")}
        ${ct.fixed_date_no ? ` · 접수번호 <code>${Bass.escapeHtml(ct.fixed_date_no)}</code>` : " · 전월세신고 확인 필요"}
      </div>
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
        <button id="cmSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 저장</button>
        ${hasPhoto ? `<button id="cmViewPhoto" class="bass-btn bass-btn--sm" type="button">📸 계약서 사진 보기</button>` : ""}
        <button id="cmPhoto" class="bass-btn bass-btn--sm" type="button">📸 계약서 사진 ${hasPhoto ? "다시 " : ""}등록</button>
        <a class="bass-btn bass-btn--sm" href="/rent3_contract/?role=lessor">🤝 서식 계약</a>
      </div>
      ${ct.keys_released === false ? `<div class="bass-card" style="margin-top:12px;padding:10px;">
        <div style="font-size:13px;margin-bottom:6px;">🔑 잔금 입금 확인 전입니다. 내 은행 앱에서 직접 확인한 뒤 도어락 정보를 열어 주세요.</div>
        <button id="cmKeys" class="bass-btn bass-btn--sm bass-btn--primary" type="button">🔑 잔금 확인 & 도어락 교부</button></div>` : ""}`);
    $m("cmTaxInvReq").addEventListener("change", (ev) => {
      $m("cmTaxBizWrap").style.display = ev.target.checked ? "grid" : "none";
    });
    const vp = $m("cmViewPhoto");
    if (vp) {
      vp.addEventListener("click", () => {
        downloadWithAuth(`/contracts/${encodeURIComponent(ct.id)}/photo`, `contract_${ct.room_no}.png`, true);
      });
    }
    $m("cmSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const reqInv = Boolean($m("cmTaxInvReq").checked);
        const res = await Bass.api(APP_ID, "/contracts/edit", {
          method: "POST",
          body: {
            branch: currentBranch, contract_id: ct.id,
            deposit: Number($m("cmDep").value || 0), monthly_rent: Number($m("cmRent").value || 0), maintenance_fee: Number($m("cmFee").value || 0),
            move_in_date: $m("cmIn").value, expiry_date: $m("cmEx").value, tenant_phone: $m("cmTel").value, memo: $m("cmMemo").value,
            tax_invoice_requested: reqInv,
            vat_target: reqInv,
            tenant_business_number: reqInv ? $m("cmBizNo").value.trim() : "",
            tenant_company_name: reqInv ? $m("cmComp").value.trim() : "",
            tenant_ceo_name: reqInv ? $m("cmCeo").value.trim() : "",
            tenant_email: reqInv ? $m("cmEmail").value.trim() : "",
            tenant_business_address: reqInv && $m("cmBizAddr") ? $m("cmBizAddr").value.trim() : "",
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
        showContractModal(no);
      });
    });
    $m("cmPhoto").addEventListener("click", () => { closeModal(); brokerTargetRoomNo = no; switchTab("broker"); });
    const kb = $m("cmKeys");
    if (kb) {
      kb.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const bal = Math.max(Number(ct.deposit || 0) - Number(ct.contract_deposit || 0), 0);
          const ok = window.confirm(`${no}호 입주일(${ct.move_in_date || "-"})의 잔금 ${fmtWon(bal)}이 내 은행 앱의 실제 입금 내역에 들어온 것을 직접 확인했습니까?\n\n확인하면 도어락·공동현관 정보가 임차인 화면에 바로 열립니다. (임차인이 보여 주는 이체 완료 화면은 위조될 수 있습니다)`);
          if (!ok) return;
          const res = await Bass.api(APP_ID, `/rooms/${r.id}/release-keys`, { method: "POST", body: { balance_checked: true } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
          showContractModal(no);
        });
      });
    }
    void body;
  }

  // ③ 만기통보: 전출계획 확인 문자 → 결과 기록 → (전출 의사 없으면) 5% 이내 갱신 안내 문자
  function showExpiryModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct) return;
    const ew = r.expiry_window;
    const ec = r.expiry_check;
    const phone = ct.tenant_phone || (r.tenant && r.tenant.phone) || "";
    const deadline = ew && ew.notice_deadline_date ? `${dotDate(ew.notice_deadline_date)}까지 ` : "";
    const msg1 = `[${bldgName()}] ${no}호 ${ct.tenant_name}님 안녕하세요, 임대인입니다.\n계약 만기가 ${dotDate(ct.expiry_date)}입니다. 만기 후 이사(전출) 계획이 있으신지 ${deadline}알려 주시기 바랍니다.\n이사 계획이 없으시면 갱신 조건을 따로 안내드리겠습니다.`;
    const stay = Boolean(ec && (ec.tenant_plan === "STAY" || /RENEWAL/.test(ec.status || "")));
    const moveOut = Boolean(ec && ec.tenant_plan === "MOVE_OUT");
    const dep0 = (ec && ec.proposed_deposit) || r.deposit;
    const rent0 = (ec && ec.proposed_monthly_rent) || r.monthly_rent;
    const fee0 = (ec && ec.proposed_maintenance_fee != null && ec.status !== "AWAITING_TENANT") ? ec.proposed_maintenance_fee : r.maintenance_fee;
    const maxDep = Math.round(Number(r.deposit || 0) * 1.05);
    const maxRent = Math.round(Number(r.monthly_rent || 0) * 1.05);
    const body = openModal(`📅 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 만기통보`, `
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">만기 ${Bass.escapeHtml(dotDate(ct.expiry_date))}${ew ? ` · 법정 통지기간 ${Bass.escapeHtml(dotDate(ew.notice_open_date))} ~ ${Bass.escapeHtml(dotDate(ew.notice_deadline_date))}` : ""}
        ${ec ? `<br>현재 상태: <strong>${Bass.escapeHtml(expiryStatusLabel(ec))}</strong>` : "<br>통보 실적이 없습니다."}</div>
      <div class="r3-lbl">① 전출계획 확인 메시지</div>
      ${msgOf("exMsg1", msg1)}
      <button id="exCopy1" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin:6px 0 12px;">📋 복사 → 임차인 문자창</button>
      <div class="r3-lbl">② 임차인 답변</div>
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-bottom:6px;">
        <button id="exStay" class="bass-btn bass-btn--sm" type="button">🏠 전출 의사 없음</button>
        <input id="exOutDate" class="bass-input" type="date" style="width:150px;" value="${Bass.escapeHtml(ct.expiry_date || "")}" aria-label="전출 예정일">
        <button id="exOut" class="bass-btn bass-btn--sm" type="button">📦 전출 예정</button>
      </div>
      ${moveOut ? `<div class="bass-muted" style="font-size:12.5px;">📦 전출 예정(${Bass.escapeHtml(ec.planned_move_out_date || "")})으로 기록되어 있습니다. 전출 정산은 [전출] 버튼에서 하세요.</div>` : ""}
      ${stay ? `
        <div class="r3-lbl" style="margin-top:12px;">③ 계약 갱신 안내 (법정 5% 이내)</div>
        <div class="r3-grid">
          <label class="r3-fld">보증금(원) <span class="bass-muted">최대 ${fmtWon(maxDep)}</span><input id="exDep" class="bass-input" type="number" value="${dep0}"></label>
          <label class="r3-fld">월세(원) <span class="bass-muted">최대 ${fmtWon(maxRent)}</span><input id="exRent" class="bass-input" type="number" value="${rent0}"></label>
          <label class="r3-fld">관리비(원)<input id="exFee" class="bass-input" type="number" value="${fee0}"></label>
          <label class="r3-fld">연장 개월<input id="exTerm" class="bass-input" type="number" value="${(ec && ec.proposed_term_months) || 12}"></label>
        </div>
        ${msgOf("exMsg2", "")}
        <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-top:6px;">
          <button id="exCopy2" class="bass-btn bass-btn--sm" type="button">📋 복사 → 임차인 문자창</button>
          <button id="exSend" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📝 갱신 예고 기록·앱 전달</button>
        </div>` : ""}`);
    void body;
    $m("exCopy1").addEventListener("click", async () => {
      await copyAndSms($m("exMsg1").value, phone);
      if (!ec) {   // 통보 실적 기록(임차인 마이홈에도 회신 요청이 뜬다)
        try {
          await Bass.api(APP_ID, "/expiry/request-check", { method: "POST", body: { branch: currentBranch, room_no: no } });
          await refresh();
          showExpiryModal(no);
        } catch (e) { Bass.showError(e); }
      }
    });
    const plan = (p) => function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/expiry/record-plan", {
          method: "POST",
          body: {
            branch: currentBranch, room_no: no, tenant_plan: p,
            planned_move_out_date: p === "MOVE_OUT" ? ($m("exOutDate").value || ct.expiry_date || "") : "",
            tenant_memo: p === "MOVE_OUT" ? "임차인 문자 답변 — 만기 퇴실 예정" : "임차인 문자 답변 — 전출의사 없음(거주 연장 희망)",
            prelist_vacancy: true,
          },
        });
        Bass.updateAppAlert(res.level || "success", res.message, res.hint);
        await refresh();
        showExpiryModal(no);
      });
    };
    $m("exStay").addEventListener("click", plan("STAY"));
    $m("exOut").addEventListener("click", plan("MOVE_OUT"));
    if (stay) {
      const compose = () => {
        const d = Number($m("exDep").value || 0), rt = Number($m("exRent").value || 0), fe = Number($m("exFee").value || 0), tm = Number($m("exTerm").value || 12);
        $m("exMsg2").value = `[${bldgName()}] ${no}호 ${ct.tenant_name}님, 계약 만기(${dotDate(ct.expiry_date)}) 이후 ${tm}개월 연장 조건을 안내드립니다.\n보증금 ${fmtWon(d)} / 월세 ${fmtWon(rt)} / 관리비 ${fmtWon(fe)}\n(법정 증액 한도 5% 이내입니다.) 의견 있으시면 알려 주세요.`;
      };
      ["exDep", "exRent", "exFee", "exTerm"].forEach((id) => $m(id).addEventListener("input", compose));
      compose();
      $m("exCopy2").addEventListener("click", () => copyAndSms($m("exMsg2").value, phone));
      $m("exSend").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, "/expiry/preview-renewal", {
            method: "POST",
            body: {
              branch: currentBranch, room_no: no,
              proposed_deposit: Number($m("exDep").value || 0), proposed_monthly_rent: Number($m("exRent").value || 0),
              proposed_maintenance_fee: Number($m("exFee").value || 0), proposed_term_months: Number($m("exTerm").value || 12),
              renewal_note: "전출의사가 없으셔서 연장 재계약 조건을 사전 예고드립니다.", open_cosign_session: true,
            },
          });
          Bass.updateAppAlert(res.level || "success", res.message, res.hint);
          await refresh();
          showExpiryModal(no);
        });
      });
    }
  }

  // ④ 전출: 임차인 정보 · 공제 후 환불액 문자 요약 → 복사 → 정산 확정(공실 매물 자동 공개)
  function showMoveOutModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct || !r.ledger) return;
    const unpaid = Number(r.ledger.total_unpaid || 0);
    const dep = Number(ct.deposit || 0);
    const phone = ct.tenant_phone || (r.tenant && r.tenant.phone) || "";
    const num = (id) => Number(($m(id) && $m(id).value) || 0);
    const body = openModal(`📦 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 전출`, `
      <div style="font-size:13px;line-height:1.7;margin-bottom:8px;">
        👤 <strong>${Bass.escapeHtml(ct.tenant_name || "")}</strong> ${phone ? `· ${Bass.escapeHtml(phone)}` : ""}<br>
        전입 ${Bass.escapeHtml(dotDate(ct.move_in_date))} · 만기 ${Bass.escapeHtml(dotDate(ct.expiry_date))}
      </div>
      <div class="r3-grid">
        <label class="r3-fld">전출일<input id="moDate" class="bass-input" type="date" value="${Bass.escapeHtml(dash.today || "")}"></label>
        <label class="r3-fld">임차보증금<input class="bass-input" type="text" value="${fmtWon(dep)}" readonly></label>
        <label class="r3-fld">체납액 <span class="bass-muted">(자동)</span><input class="bass-input" type="text" value="${fmtWon(unpaid)}" readonly></label>
        <label class="r3-fld">청소비(원)<input id="moClean" class="bass-input" type="number" value="70000"></label>
        <label class="r3-fld">체납 공과금(원)<input id="moUtil" class="bass-input" type="number" value="0"></label>
        <label class="r3-fld">파손 복구비(원)<input id="moDmg" class="bass-input" type="number" value="0"></label>
      </div>
      ${msgOf("moMsg", "")}
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-top:6px;">
        <button id="moCopy" class="bass-btn bass-btn--sm" type="button">📋 복사 → 임차인 문자창</button>
        <button id="moDone" class="bass-btn bass-btn--sm bass-btn--primary" type="button">✅ 정산 확정 · 임대 매물 등록</button>
      </div>
      <div class="bass-muted" style="font-size:12px;margin-top:6px;">정산 확정을 누르면 보증금 반환을 마친 것으로 보고 호실이 공실이 되어 홈페이지 임대 매물로 공개됩니다.</div>`);
    void body;
    const refund = () => Math.max(0, dep - unpaid - num("moClean") - num("moUtil") - num("moDmg"));
    const compose = () => {
      const lines = [`[${bldgName()}] ${no}호 ${ct.tenant_name}님 전출 정산 안내 (전출일 ${dotDate($m("moDate").value)})`, `임차보증금 ${fmtWon(dep)}`];
      [["체납 월세", unpaid], ["청소비", num("moClean")], ["체납 공과금", num("moUtil")], ["파손 복구비", num("moDmg")]].forEach(([k, v]) => { if (v > 0) lines.push(`- ${k} ${fmtWon(v)}`); });
      lines.push(`환불 예정액 ${fmtWon(refund())}`, "확인 후 말씀해 주시면 등록된 계좌로 보내드리겠습니다.");
      $m("moMsg").value = lines.join("\n");
    };
    ["moDate", "moClean", "moUtil", "moDmg"].forEach((id) => $m(id).addEventListener("input", compose));
    compose();
    $m("moCopy").addEventListener("click", () => copyAndSms($m("moMsg").value, phone));
    $m("moDone").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        if (!window.confirm(`${no}호 정산을 확정할까요?\n환불 예정액 ${fmtWon(refund())}을 임차인에게 돌려준 뒤 눌러 주세요. 호실이 공실이 되고 임대 매물로 공개됩니다.`)) return;
        const calc = await Bass.api(APP_ID, "/checkout/calculate", {
          method: "POST",
          body: { branch: currentBranch, room_no: no, utility_settlement: num("moUtil"), cleaning_fee: num("moClean"), damage_restoration_fee: num("moDmg") },
        });
        const res = await Bass.api(APP_ID, `/checkout/${calc.settlement.id}/complete`, { method: "POST", body: { branch: currentBranch } });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await refresh();
      });
    });
  }

  // 더보기 ▸ 건물 정보 수정
  function showBuildingModal() {
    const b = (dash && dash.building) || {};
    const vatStr = (b.vat_rooms || []).join(", ");
    const incStr = (b.income_tax_rooms || []).join(", ");
    openModal("🏢 건물 정보 및 과세·종소세 대상 호실 수정", `
      <label class="r3-fld">건물 이름<input id="bdName" class="bass-input" value="${Bass.escapeHtml(b.building_name || "")}"></label>
      <label class="r3-fld" style="margin-top:8px;">주소<input id="bdAddr" class="bass-input" value="${Bass.escapeHtml(b.address || "")}"></label>
      <label class="r3-fld" style="margin-top:8px;">부가가치세 납부 대상 호실 (사무실·상가, 쉼표 구분)
        <input id="bdVatRooms" class="bass-input" value="${Bass.escapeHtml(vatStr)}" placeholder="예: 201, 202, 203, 301, 302, 303">
      </label>
      <label class="r3-fld" style="margin-top:8px;">종합소득세 신고 대상 호실 (쉼표 구분)
        <input id="bdIncRooms" class="bass-input" value="${Bass.escapeHtml(incStr)}" placeholder="예: 201, 202, 203, 301, 302, 303, 401, 402, 403...">
      </label>
      <button id="bdSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin-top:10px;">💾 저장</button>`);
    $m("bdSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/building/edit", {
          method: "POST",
          body: {
            branch: currentBranch,
            building_name: $m("bdName").value,
            address: $m("bdAddr").value,
            vat_rooms: $m("bdVatRooms").value,
            income_tax_rooms: $m("bdIncRooms").value,
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await refresh();
      });
    });
  }
  const btnSupportChat = document.getElementById("btnSupportChat");
  if (btnSupportChat) {
    btnSupportChat.addEventListener("click", () => {
      const dr = document.querySelector("[data-r3-drawer]");
      if (dr) dr.classList.remove("is-open");
      R3Shell.openSupportChat();
    });
  }
  const btnSupportSetting = document.getElementById("btnSupportSetting");
  if (btnSupportSetting) {
    btnSupportSetting.addEventListener("click", () => {
      const dr = document.querySelector("[data-r3-drawer]");
      if (dr) dr.classList.remove("is-open");
      R3Shell.openSupportSettings();
    });
  }
  const closeDrawer = () => { const dr = document.querySelector("[data-r3-drawer]"); if (dr) dr.classList.remove("is-open"); };
  const btnShortcut = document.getElementById("btnShortcut");
  if (btnShortcut) btnShortcut.addEventListener("click", () => { closeDrawer(); R3Shell.openShortcutSettings(); });
  const btnFullLogin = document.getElementById("btnFullLogin");
  if (btnFullLogin) btnFullLogin.addEventListener("click", fullLogin);

  // 📨 기존 임차인 안내: 입주자마다 '본인 전용 링크'(7일·1회)를 앱이 만들어 주고, 큰 단추로 다음 사람에게 문자 창을 연다(문구는 자동 복사).
  //    링크를 문자로 받은 사람만 가입할 수 있으므로 임대인 승인·전화 확인이 필요 없다. 만든 링크는 이 기기에만 보관(7일)해 창을 닫았다 열어도 이어서 보낸다.
  const btnConnectMsg = document.getElementById("btnConnectMsg");
  if (btnConnectMsg) {
    btnConnectMsg.addEventListener("click", () => { closeDrawer(); showInviteSender().catch(Bass.showError); });
  }
  async function showInviteSender() {
    const E = Bass.escapeHtml;
    const key = `rent3_inv_${currentBranch}`;
    const loadB = () => { try { const v = JSON.parse(window.localStorage.getItem(key) || "null"); return v && (Date.now() - v.at) < 6 * 86400000 ? v : null; } catch (_) { return null; } };
    const saveB = (v) => { try { window.localStorage.setItem(key, JSON.stringify(v)); } catch (_) { /* 저장이 막혀도 이 창에서는 계속 쓸 수 있다 */ } };
    const b = dash.building || {}, l = dash.lessor || {};
    let batch = loadB();
    const body = openModal("📨 기존 임차인 앱 연결 안내", '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>');
    async function draw() {
      const st = await Bass.api(APP_ID, `/tenant-invites?branch=${encodeURIComponent(currentBranch)}`);
      const rows = st.tenants;
      const todo = rows.filter((p) => !p.activated);
      const link = (p) => (batch && batch.links[p.room_no]) ? window.location.origin + batch.links[p.room_no] : "";
      const sent = (p) => batch && batch.sent && batch.sent[p.room_no];
      const msgFor = (p) => `[${b.building_name || "건물"} 안내] ${p.tenant_name}님, 안녕하세요. 임대인 ${l.name || ""}입니다. 입주자 앱을 열었습니다. 월세 납부 확인·수리(A/S) 요청·연말정산 납부확인서를 편하게 이용하실 수 있어요.\n아래 본인 전용 링크를 누르고 비밀번호만 정하시면 바로 시작됩니다(7일 안에 한 번만 사용, 다른 분께 전달하지 마세요).\n👉 ${link(p)}\n문의: ${l.phone || ""}`;
      const sendable = todo.filter((p) => p.phone && link(p) && !sent(p));
      const done = rows.length - todo.length;
      const list = rows.map((p, i) => {
        const chip = p.activated ? '<span style="color:var(--ok);font-weight:700;">✅ 가입 완료</span>' : (sent(p) ? '<span style="color:var(--warn);font-weight:700;">📩 보냄</span>' : '<span class="bass-muted">미발송</span>');
        return `<div class="iv-row" data-i="${i}" style="display:flex;align-items:center;gap:6px;padding:6px 4px;border-bottom:1px solid var(--border);">
          <span style="flex:1;min-width:0;font-size:14px;line-height:1.4;"><strong>${E(p.room_no)}호 ${E(p.tenant_name)}</strong> ${chip}<br><span class="bass-muted">${E(p.phone || "번호 없음")}</span></span>
          ${p.activated ? `<button type="button" class="bass-btn bass-btn--sm iv-cancel" style="min-height:44px;">↩ 가입 취소</button>`
            : `${!p.phone || !link(p) ? "" : `<button type="button" class="bass-btn bass-btn--sm iv-one" style="min-height:44px;">📩 문자</button>`}${(p.invite_pending || sent(p)) ? `<button type="button" class="bass-btn bass-btn--sm iv-cancel" style="min-height:44px;">✖ 링크 취소</button>` : ""}`}</div>`;
      }).join("");
      body.innerHTML = `
        <div style="font-size:13px;line-height:1.65;margin-bottom:8px;">입주자마다 <strong>본인 전용 링크</strong>(7일·1회)를 앱이 만들어 줍니다. <strong>링크를 문자로 받은 사람만</strong> 가입할 수 있어서 임대인 승인이나 전화 확인이 필요 없어요. 아래 큰 단추를 누르면 <strong>다음 사람의 문자 창</strong>이 받는 사람·문구가 입력된 채 열리니 <strong>보내기</strong>만 누르고 돌아오세요. <span class="bass-muted">(문구가 비어 있으면 자동 복사된 문구를 붙여넣으세요. PC에서는 복사만 됩니다.)</span><br><span class="bass-muted">사람마다 링크가 달라서 단체문자·그룹채팅에는 넣을 수 없습니다(다른 사람 링크가 보입니다).</span></div>
        <div style="font-weight:800;margin:6px 0;">가입 ${done}/${rows.length}명 · 미발송 ${sendable.length}명</div>
        ${batch ? "" : '<div class="bass-muted" style="margin-bottom:6px;">아직 링크를 만들지 않았습니다.</div>'}
        <button id="ivMake" class="bass-btn ${batch ? "" : "bass-btn--primary"}" type="button" style="width:100%;min-height:48px;margin-bottom:8px;">${batch ? "🔁 링크 다시 만들기 (이전 링크는 못 씀)" : "🔗 입주자별 링크 만들기"}</button>
        <button id="ivNext" class="bass-btn bass-btn--primary" type="button" style="width:100%;min-height:56px;font-size:17px;margin-bottom:10px;" ${sendable.length ? "" : "disabled"}>📩 다음 사람에게 문자 보내기 (${sendable.length}명 남음)</button>
        <div id="ivList" style="max-height:34vh;overflow-y:auto;">${list || '<div class="bass-muted" style="padding:10px;">입주 임차인이 없습니다.</div>'}</div>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.6;margin-top:8px;">📞 문자를 못 받았다는 분은 공통 주소 <code>${E(window.location.origin)}/rent3/?connect=${E(currentBranch)}</code> 로 연결을 요청하게 하세요(이때는 임대인 승인 창이 뜹니다).</div>`;
      // 휴대폰이면 받는 사람 번호와 문구가 입력된 문자 창을 바로 연다(보내기만 누르면 됨). 클립보드에도 복사해 두고, PC 에서는 복사만 한다.
      const smsTo = (phone, text) => {
        copyText(text);
        const d = String(phone || "").replace(/\D/g, "");
        if (d && /Android|iPhone|iPad/i.test(navigator.userAgent)) {
          const a = document.createElement("a");
          a.href = `sms:${d}${/iPhone|iPad/i.test(navigator.userAgent) ? "&" : "?"}body=${encodeURIComponent(text)}`;
          a.click();
        } else { Bass.updateAppAlert("success", "문구를 복사했습니다 — 문자 창에 붙여넣어 보내세요."); }
      };
      const send = (p) => { smsTo(p.phone, msgFor(p)); batch.sent = batch.sent || {}; batch.sent[p.room_no] = true; saveB(batch); setTimeout(draw, 400); };
      body.querySelector("#ivMake").addEventListener("click", function () {
        if (batch && !window.confirm("링크를 다시 만들면 이미 보낸 링크는 쓸 수 없게 됩니다. 계속할까요?")) return;
        Bass.withLock(this, async () => {
          const r = await Bass.api(APP_ID, "/tenant-invites/bulk", { method: "POST", body: { branch: currentBranch } });
          batch = { at: Date.now(), links: Object.fromEntries(r.links.map((x) => [x.room_no, x.path])), sent: {} };
          saveB(batch);
          Bass.updateAppAlert("success", r.message);
          await draw();
        });
      });
      body.querySelector("#ivNext").addEventListener("click", () => { if (sendable.length) send(sendable[0]); });
      body.querySelectorAll(".iv-row").forEach((row) => {
        const p = rows[Number(row.dataset.i)];
        const one = row.querySelector(".iv-one");
        if (one) one.addEventListener("click", () => send(p));
        const cancel = row.querySelector(".iv-cancel");
        if (cancel) cancel.addEventListener("click", function () {
          const msg = p.activated
            ? `${p.room_no}호 ${p.tenant_name}님의 가입을 취소할까요?\n비밀번호·간편 비밀번호·바로가기가 지워져 가입 전 상태로 돌아갑니다. (호실·계약·납부는 그대로)\n다시 쓰려면 안내 링크를 새로 보내야 합니다.`
            : `${p.room_no}호 ${p.tenant_name}님께 보낸 안내 링크를 취소할까요?\n받은 링크는 더 이상 쓸 수 없습니다.`;
          if (!window.confirm(msg)) return;
          Bass.withLock(this, async () => {
            const r = await Bass.api(APP_ID, "/tenant-invites/cancel", { method: "POST", body: { branch: currentBranch, room_no: p.room_no } });
            if (batch && batch.links) { delete batch.links[p.room_no]; if (batch.sent) delete batch.sent[p.room_no]; saveB(batch); }
            Bass.updateAppAlert("success", r.message);
            await draw();
          });
        });
      });
    }
    await draw();
  }

  const btnLedgerCsv = document.getElementById("btnLedgerCsv");
  if (btnLedgerCsv) {
    btnLedgerCsv.addEventListener("click", () => {
      const dr = document.querySelector("[data-r3-drawer]");
      if (dr) dr.classList.remove("is-open");
      showLedgerCsvModal();
    });
  }

  // 📥 납부현황 엑셀(CSV): 내려받기(백업·엑셀 편집) / 올리기(미리보기 → 확인 → 반영). 반영 전에 서버가 자동 백업한다.
  function showLedgerCsvModal() {
    const yr = Number((dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const body = openModal("📥 납부현황 엑셀(CSV)", `
      <div class="bass-muted" style="font-size:13px;line-height:1.6;margin-bottom:8px;">호실×월 납부일·금액 표를 엑셀로 받거나, 엑셀 장부를 올려 한꺼번에 기록합니다. 파일에는 <strong>입주자 이름</strong>이 들어 있으니 안전하게 보관하세요.</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;">
        <select id="lcYear" class="bass-select" style="width:110px;min-height:44px;">${[yr + 1, yr, yr - 1, yr - 2].map((y) => `<option value="${y}" ${y === yr ? "selected" : ""}>${y}년</option>`).join("")}</select>
        <button id="lcDown" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">⬇️ 내려받기</button>
      </div>
      <div class="r3-lbl">엑셀(CSV) 올리기</div>
      <input id="lcFile" type="file" accept=".csv,text/csv" class="bass-input" style="min-height:44px;">
      <div id="lcOut" style="margin-top:10px;"></div>`);
    const $ = (id) => body.querySelector("#" + id);
    let csvText = "";
    $("lcDown").addEventListener("click", () => downloadWithAuth(`/ledger/export-csv?branch=${encodeURIComponent(currentBranch)}&year=${$("lcYear").value}`, `납부현황_${currentBranch}_${$("lcYear").value}.csv`));
    const statusKo = { new: "새로 기록", same: "이미 같음", diff: "다름", skip: "건너뜀" };
    const statusColor = { new: "var(--ok)", same: "var(--muted)", diff: "var(--warn)", skip: "var(--err)" };
    async function readText(file) {
      const buf = await file.arrayBuffer();
      let t = new TextDecoder("utf-8").decode(buf);
      if (t.includes("\uFFFD")) { try { t = new TextDecoder("euc-kr").decode(buf); } catch (_) { /* 그대로 */ } }
      return t;
    }
    async function preview() {
      const year = Number($("lcYear").value);
      const d = await Bass.api(APP_ID, "/ledger/import/preview", { method: "POST", body: { branch: currentBranch, year, csv_text: csvText } });
      const c = d.counts;
      const attention = d.items.filter((i) => i.status === "diff" || i.status === "skip" || i.notes.length);
      const rows = attention.map((i) => `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:13.5px;"><strong>${Bass.escapeHtml(i.room_no)}호 ${Number(i.ym.slice(5))}월</strong> ${Bass.escapeHtml(i.date.slice(5).replace("-", "."))} ${fmtWon(i.amount)} <span style="color:${statusColor[i.status]};font-weight:700;">${statusKo[i.status]}</span>${i.reason ? ` · ${Bass.escapeHtml(i.reason)}` : ""}${i.existing ? `<br><span class="bass-muted">지금 앱: ${Bass.escapeHtml(i.existing.date.slice(5).replace("-", "."))} ${fmtWon(i.existing.amount)}</span>` : ""}${i.notes.map((n) => `<br><span class="bass-muted">· ${Bass.escapeHtml(n)}</span>`).join("")}</div>`).join("");
      const unread = d.issues.map((i) => `<div style="padding:5px 0;font-size:13px;color:var(--muted);">· ${Bass.escapeHtml(i.msg)}</div>`).join("");
      $("lcOut").innerHTML = `
        <div class="bass-card" style="padding:10px 12px;">
          <div style="font-weight:800;font-size:15px;margin-bottom:6px;">미리보기 — ${year}년 ${d.rows}호실</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:14px;"><span style="color:var(--ok);font-weight:800;">새로 기록 ${c.new}건</span><span>이미 같음 ${c.same}건</span><span style="color:var(--warn);font-weight:800;">다름 ${c.diff}건</span><span style="color:var(--err);">건너뜀 ${c.skip}건</span>${c.former ? `<span>이전 입주자 시기 ${c.former}건 포함</span>` : ""}<span>읽지 못한 칸 ${d.issues.length}곳</span></div>
        </div>
        ${rows || unread ? `<details style="margin-top:8px;" ${attention.length + d.issues.length <= 12 ? "open" : ""}><summary style="cursor:pointer;min-height:40px;font-weight:700;">확인이 필요한 칸 ${attention.length + d.issues.length}건 보기</summary><div style="max-height:34vh;overflow-y:auto;">${rows}${unread}</div></details>` : ""}
        ${c.diff ? `<label style="margin-top:8px;"><input id="lcReplace" type="checkbox"> '다름' ${c.diff}건은 <strong>파일 내용으로 바꾸기</strong> (끄면 지금 앱의 값을 그대로 둡니다)</label>` : ""}
        <div style="margin-top:10px;"><button id="lcApply" class="bass-btn bass-btn--primary" type="button" style="min-height:48px;width:100%;" ${c.new + c.diff === 0 ? "disabled" : ""}>✅ 확인했습니다 — 반영하기</button>
        <div class="bass-muted" style="font-size:12.5px;margin-top:6px;">반영하기 전에 전체 자료를 자동으로 백업합니다. 같은 파일을 다시 올려도 중복되지 않습니다.</div></div>`;
      const ap = $("lcApply");
      if (ap) ap.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const rep = $("lcReplace") ? $("lcReplace").checked : false;
          const r = await Bass.api(APP_ID, "/ledger/import/apply", { method: "POST", body: { branch: currentBranch, year, csv_text: csvText, replace_diffs: rep } });
          Bass.updateAppAlert("success", r.message);
          await refresh();
          closeModal();
        });
      });
    }
    $("lcFile").addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      if (f.size > 1000000) { Bass.updateAppAlert("warning", "파일이 너무 큽니다(1MB 이하)."); return; }
      csvText = await readText(f);
      $("lcOut").innerHTML = '<div class="bass-muted">읽는 중...</div>';
      try { await preview(); } catch (err) { $("lcOut").innerHTML = ""; Bass.showError(err); }
    });
    $("lcYear").addEventListener("change", () => { if (csvText) preview().catch(Bass.showError); });
  }

  const btnEditBuilding = document.getElementById("btnEditBuilding");
  if (btnEditBuilding) {
    btnEditBuilding.addEventListener("click", () => {
      const dr = document.querySelector("[data-r3-drawer]");
      if (dr) dr.classList.remove("is-open");
      showBuildingModal();
    });
  }

  function renderRoomPanel() {
    const box = document.getElementById("roomPanel");
    const r = roomOf(selRoomNo);
    if (!box) return;
    if (!r) { box.innerHTML = ""; return; }
    const ct = r.contract;
    const lg = r.ledger;
    if (!(r.is_occupied && lg && ct)) {
      box.innerHTML = `<div class="room-card">
        <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
          <div><strong style="font-size:17px;">${Bass.escapeHtml(r.room_no)}호</strong> <span class="bass-badge ${r.is_published ? "bass-badge--info" : "bass-badge--warn"}">${r.is_published ? "공실 · 매물 공개중" : "공실 · 비공개"}</span></div>
          <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
            <button class="bass-btn bass-btn--sm btn-pub-toggle" data-id="${r.id}" data-pub="${!r.is_published}" type="button">${r.is_published ? "비공개 전환" : "직거래 매물 공개"}</button>
            <button class="bass-btn bass-btn--sm btn-go-broker" data-room="${r.room_no}" type="button">📸 중개계약 사진등록</button>
            <a class="bass-btn bass-btn--sm" href="/rent3_contract/?role=lessor">🤝 서식 계약</a>
          </div>
        </div>
        <div class="bass-muted" style="font-size:13px;margin-top:6px;">보증금 ${fmtWon(r.deposit)} · 월세 ${fmtWon(r.monthly_rent)} · 관리비 ${fmtWon(r.maintenance_fee)}</div>
      </div>`;
      box.querySelectorAll(".btn-pub-toggle").forEach((btn) => btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/publish`, { method: "POST", body: { is_published: btn.dataset.pub === "true" } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      }));
      box.querySelectorAll(".btn-go-broker").forEach((btn) => btn.addEventListener("click", () => { brokerTargetRoomNo = btn.dataset.room || ""; switchTab("broker"); }));
      return;
    }
    const ew = r.expiry_window;
    const ec = r.expiry_check;
    const inWindow = Boolean(ew && ew.in_6_to_2_months_window);
    const need = inWindow && !ec;     // 통지 구간인데 통보 실적이 없으면 노란색
    const n = unpaidCount(r);
    const bs = r.billing_schedule;
    box.innerHTML = `<div class="room-card r3-zone r3-zone--room" style="${need ? "border-color:var(--warn);" : ""}">
      <div class="rc-btns">
        <button class="bass-btn bass-btn--sm" data-act="contract" type="button">계약내용</button>
        <button class="bass-btn bass-btn--sm ${need ? "rc-warn" : ""}" data-act="expiry" type="button">만기통보</button>
        <button class="bass-btn bass-btn--sm" data-act="moveout" type="button">전출</button>
      </div>
      <div class="rc-subbtns"><button class="bass-btn bass-btn--sm" data-act="invite" type="button" style="min-height:44px;border-color:hsla(199,80%,60%,.4);">📲 입주 안내 카드 (QR)</button>
        <button class="bass-btn bass-btn--sm" data-act="lostphone" type="button" style="min-height:44px;border-color:hsla(356,75%,62%,.4);">📵 분실 폰 해제</button></div>
      <div class="rc-info">
        <span class="rc-unpaid ${n > 0 ? "is-late" : ""}">${n > 0 ? `미납:${n}` : "완납"}</span><br>
        보증금 ${fmtWon(ct.deposit)} · 월세 ${fmtWon(ct.monthly_rent)} · 관리비 ${fmtWon(ct.maintenance_fee)}<br>
        만기일 ${Bass.escapeHtml(dotDate(ct.expiry_date))}${lg.days_to_expiry != null ? ` (D-${lg.days_to_expiry})` : ""}${bs ? ` · 납부일 매월 ${bs.payment_day}일` : ""}${ec ? ` · ${Bass.escapeHtml(expiryStatusLabel(ec))}` : ""}
      </div></div>
      <div id="payInline" class="room-card r3-zone r3-zone--pay" style="margin-top:10px;"></div>`;
    const acts = { contract: showContractModal, expiry: showExpiryModal, moveout: showMoveOutModal, invite: showInviteModal, lostphone: revokeShortcuts };
    box.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => acts[b.dataset.act](r.room_no)));
    renderPayInline(r.room_no);      // 호실을 고르면 그 호실의 수납 내용이 바로 펼쳐진 채로 열린다
  }

  function renderLedgerTab() {
    const rooms = dash.rooms || [];
    if (!roomOf(selRoomNo)) {
      const late = rooms.find((x) => unpaidCount(x) > 0);
      selRoomNo = (late || rooms[0] || {}).room_no || "";
    }
    const opt = (x) => {
      const n = unpaidCount(x);
      const who = x.is_occupied && x.contract ? ` ${x.contract.tenant_name || ""}` : " 공실";
      return `<option value="${Bass.escapeHtml(x.room_no)}" ${x.room_no === selRoomNo ? "selected" : ""}>${Bass.escapeHtml(x.room_no)}호${Bass.escapeHtml(who)}${n > 0 ? ` · 미납 ${n}` : ""}</option>`;
    };
    // 지금 챙길 일: 임차인이 이체했다고 알린 건(바로 보임), 미납 호실(접어 두었다가 눌러 펼친다, 많이 밀린 순)
    const lateRows = rooms.filter((x) => x.is_occupied && x.contract && unpaidCount(x) > 0)
      .sort((a, b) => ((b.ledger && b.ledger.total_unpaid) || 0) - ((a.ledger && a.ledger.total_unpaid) || 0));
    const conn = dash.connect_requests || [];
    const connHtml = conn.length ? `
      <div class="bass-card" style="padding:10px 12px;margin-bottom:12px;border-color:var(--warn);">
        <div style="font-weight:800;font-size:14px;margin-bottom:4px;">🔗 내 페이지 연결 요청 ${conn.length}건</div>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.5;margin-bottom:8px;">임차인의 <strong>장부 휴대폰으로 전화</strong>해, 임차인 화면에 보이는 <strong>4자리 확인번호</strong>를 들은 뒤 입력하세요. 임차인이 요청한 적이 없다고 하면 거절하세요.</div>
        ${conn.map((n) => `<div data-conn="${Bass.escapeHtml(n.id)}" style="padding:8px 0;border-top:1px solid var(--border);">
          <div style="font-size:14px;margin-bottom:6px;"><strong>${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)}</strong> · <a href="tel:${Bass.escapeHtml(n.tenant_phone)}">📞 ${Bass.escapeHtml(n.tenant_phone)}</a></div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;"><input class="bass-input cn-code" inputmode="numeric" maxlength="4" placeholder="확인번호 4자리" aria-label="확인번호" style="width:140px;min-height:44px;">
          <button class="bass-btn bass-btn--sm bass-btn--primary cn-ok" type="button" style="min-height:44px;">✅ 승인</button>
          <button class="bass-btn bass-btn--sm cn-no" type="button" style="min-height:44px;">거절</button></div></div>`).join("")}
      </div>` : "";
    const pend = dash.transfer_notices || [];
    const pendHtml = pend.length ? `
      <div class="bass-card r3-zone r3-zone--notice" style="padding:10px 12px;margin-bottom:12px;">
        <div style="font-weight:800;font-size:14px;margin-bottom:6px;">📨 이체 확인 요청 ${pend.length}건</div>
        ${pend.map((n) => `<button type="button" class="bass-btn todo-row" data-room="${Bass.escapeHtml(n.room_no)}" style="width:100%;min-height:48px;text-align:left;margin-bottom:6px;border-color:var(--warn);">${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)} · ${fmtWon(n.amount)} 이체했다고 알림 → 확인하기</button>`).join("")}
      </div>` : "";
    const lateHtml = lateRows.length ? `
      <details id="lateBox" class="bass-card r3-zone r3-zone--late" style="padding:0;margin-top:12px;">
        <summary style="cursor:pointer;min-height:52px;padding:14px 14px;font-weight:800;font-size:15px;display:flex;align-items:center;gap:8px;">⚠️ 미납 호실 ${lateRows.length}곳 <span class="bass-muted" style="font-weight:500;">눌러서 펼치기</span></summary>
        <div style="padding:0 10px 10px;max-height:44vh;overflow-y:auto;">${lateRows.map((x) => `<button type="button" class="bass-btn todo-row" data-room="${Bass.escapeHtml(x.room_no)}" style="width:100%;min-height:48px;text-align:left;margin-bottom:6px;display:flex;justify-content:space-between;gap:8px;"><span>${Bass.escapeHtml(x.room_no)}호 ${Bass.escapeHtml(x.contract.tenant_name || "")}</span><span style="color:var(--err);font-weight:800;white-space:nowrap;">${unpaidCount(x)}개월 · ${fmtWon((x.ledger && x.ledger.total_unpaid) || 0)}</span></button>`).join("")}</div>
      </details>` : "";
    elTabContent.innerHTML = `
      <div class="bass-card r3-zone r3-zone--sms" style="padding:12px;margin-bottom:12px;">
        <label class="r3-lbl" for="bankSms" style="margin:0 0 6px;font-weight:800;">🏦 은행 입금 문자 붙여넣기 <span class="bass-muted" style="font-weight:500;">— 호실·입금자 이름으로 호실을 자동으로 찾습니다</span></label>
        <textarea id="bankSms" class="bass-input" rows="2" placeholder="[은행] 11/13 201호 홍길동 440,000원 입금"></textarea>
        <button id="bankSmsGo" class="bass-btn bass-btn--primary" type="button" style="width:100%;min-height:48px;margin-top:8px;">⚡ 문자 읽고 수납 처리</button>
        <div id="smsWarn"></div>
      </div>
      ${connHtml}
      ${pendHtml}
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <label class="r3-lbl" for="roomPick" style="margin:0;white-space:nowrap;">호실 선택</label>
        <select id="roomPick" class="bass-select" style="flex:1;min-width:0;font-size:18px;font-weight:700;min-height:48px;">${rooms.map(opt).join("")}</select>
      </div>
      <div id="roomPanel"></div>
      ${lateHtml}
    `;
    document.getElementById("roomPick").addEventListener("change", (e) => { selRoomNo = e.target.value; payFlash = null; renderRoomPanel(); });
    const goRoom = (no) => {
      selRoomNo = no;
      document.getElementById("roomPick").value = no;
      renderRoomPanel();
      document.getElementById("roomPick").scrollIntoView({ behavior: "smooth", block: "start" });
    };
    elTabContent.querySelectorAll(".todo-row").forEach((b) => b.addEventListener("click", () => goRoom(b.dataset.room)));
    elTabContent.querySelectorAll("[data-conn]").forEach((card) => {
      const id = card.dataset.conn;
      card.querySelector(".cn-ok").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(id)}/approve`, { method: "POST", body: { branch: currentBranch, code: card.querySelector(".cn-code").value.trim() } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
      card.querySelector(".cn-no").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(id)}/reject`, { method: "POST", body: { branch: currentBranch } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });
    // 문자 → 수납 처리 → 그 호실이 선택되어 수납 결과가 펼쳐지고 → 수납확인 메시지를 보낸다
    // 호실·입금자명·금액이 장부와 다르면 저장하지 않고 경고 → '원본 고치기' 또는 '입금자명 기준으로 그대로 반영'
    const smsWarn = document.getElementById("smsWarn");
    async function runSms(force) {
      const t = document.getElementById("bankSms").value.trim();
      if (!t) { Bass.updateAppAlert("warning", "붙여넣은 입금 문자가 없습니다."); return; }
      smsWarn.innerHTML = "";
      const res = await Bass.api(APP_ID, "/payments/sms-paste", { method: "POST", body: { branch: currentBranch, sms_text: t, force: !!force } });
      if (res.needs_confirm) {
        const p = res.parsed || {}, bn = res.by_name || {};
        smsWarn.innerHTML = `
          <div role="alert" style="margin-top:10px;padding:10px 12px;border-radius:12px;border:1px solid var(--warn);background:rgba(234,179,8,.14);font-size:14px;line-height:1.55;">
            <div style="font-weight:800;margin-bottom:4px;">⚠️ 문자 내용이 장부와 다릅니다</div>
            ${res.issues.map((i) => `<div>· ${Bass.escapeHtml(i.msg)}</div>`).join("")}
            <div class="bass-muted" style="font-size:13px;margin:6px 0;">읽은 값: ${p.room_no ? Bass.escapeHtml(p.room_no) + "호 " : "호실 없음 "}${Bass.escapeHtml(p.payer_name || "")} ${fmtWon(p.amount || 0)}<br>원본 문자의 호실·입금자명·금액을 확인해 고친 뒤 다시 처리하세요. 그대로 진행하면 <strong>입금자명 기준</strong>으로 ${Bass.escapeHtml(bn.room_no || "")}호 ${Bass.escapeHtml(bn.tenant_name || "")}님 계약에 반영합니다.</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <button id="smsFix" class="bass-btn" type="button" style="flex:1;min-width:120px;min-height:46px;">✏️ 원본 문자 고치기</button>
              <button id="smsForce" class="bass-btn bass-btn--primary" type="button" style="flex:1;min-width:160px;min-height:46px;">그대로 진행 (${Bass.escapeHtml(bn.room_no || "")}호 ${Bass.escapeHtml(bn.tenant_name || "")})</button>
            </div>
          </div>`;
        Bass.updateAppAlert("warning", res.message);
        document.getElementById("smsFix").addEventListener("click", () => { smsWarn.innerHTML = ""; const ta = document.getElementById("bankSms"); ta.focus(); });
        document.getElementById("smsForce").addEventListener("click", function () { Bass.withLock(this, () => runSms(true)); });
        return;
      }
      await doneSms(res);
    }
    document.getElementById("bankSmsGo").addEventListener("click", function () {
      Bass.withLock(this, () => runSms(false));
    });
    async function doneSms(res) {
      Bass.updateAppAlert(res.level || "success", res.message);
      const pay = res.payment || {};
      const rn = pay.room_no || selRoomNo;
      receiptMsg[rn] = receiptText(pay, (res.ledger && res.ledger.total_unpaid) || 0);
      const note = res.forced_by_name ? " — 입금자명 기준으로 반영" : "";
      payFlash = { no: rn, text: `${rn}호 ${fmtWon(pay.total_amount || 0)} 수납 처리 완료 (${dotDate(pay.paid_date || "")})${note}` };
      selRoomNo = rn;
      await refresh();
      const pi = document.getElementById("roomPick");
      if (pi) pi.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    renderRoomPanel();
  }

  // 운영 ▸ 만기 통보: 만기 6~2개월 법정 통보 대상 목록 + 전체 입주 호실 만기 임박 순 현황 (클릭 시 만기통보 창)
  function renderExpiryTab() {
    const noticeRooms = [...(dash.expiry_notice_rooms || [])]
      .sort((a, b) => (a.days_to_expiry ?? 9999) - (b.days_to_expiry ?? 9999));
    const stLabel = (st) => ({
      UNREQUESTED: "미통보",
      AWAITING_TENANT: "회신 대기",
      MOVEOUT_PLANNED: "전출 예정",
      STAY_CONFIRMED: "연장 희망",
      RENEWAL_PREVIEWED: "예고 발송",
      RENEWAL_ACKNOWLEDGED: "동의 완료",
    }[st] || st || "미통보");
    const stBadge = (st) => {
      if (st === "STAY_CONFIRMED" || st === "RENEWAL_PREVIEWED" || st === "RENEWAL_ACKNOWLEDGED") return "bass-badge--ok";
      if (st === "AWAITING_TENANT" || st === "MOVEOUT_PLANNED") return "bass-badge--info";
      return "bass-badge--warn";
    };
    const allOccupied = (dash.rooms || [])
      .filter((r) => r.is_occupied && r.contract)
      .sort((a, b) => ((a.ledger && a.ledger.days_to_expiry) ?? 99999) - ((b.ledger && b.ledger.days_to_expiry) ?? 99999));

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;${noticeRooms.length ? "border-left:4px solid var(--warn);" : ""}">
        <div class="bass-row" style="justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <div>
            <h3 style="margin:0;">⚖️ 만기 6~2개월 법정 통보 대상 (${noticeRooms.length}실)</h3>
            <p class="bass-muted" style="font-size:13px;margin:4px 0 0;">
              만기 <strong>6개월 ~ 2개월 전</strong> 호실입니다. 눌러서 전출계획 확인이나 연장 예고를 보내세요.
            </p>
          </div>
          ${noticeRooms.length ? `<span class="bass-badge bass-badge--warn">법정 통지구간</span>` : ""}
        </div>
        <div style="margin-top:10px;">
          ${noticeRooms.length ? noticeRooms.map((nr) => `
            <button type="button" class="bass-btn exp-room-btn" data-room="${Bass.escapeHtml(nr.room_no)}"
              style="width:100%;min-height:52px;text-align:left;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 12px;">
              <div>
                <strong>${Bass.escapeHtml(nr.room_no)}호 ${Bass.escapeHtml(nr.tenant_name || "")}</strong>
                <div class="bass-muted" style="font-size:12.5px;margin-top:2px;">만기 ${Bass.escapeHtml(dotDate(nr.expiry_date))}${nr.days_to_expiry != null ? ` (D-${nr.days_to_expiry})` : ""} · 마감 ${Bass.escapeHtml(dotDate(nr.notice_deadline_date))}</div>
              </div>
              <span class="bass-badge ${stBadge(nr.check_status)}" style="white-space:nowrap;">${Bass.escapeHtml(stLabel(nr.check_status))}</span>
            </button>
          `).join("") : `<div class="bass-muted" style="font-size:13px;">현재 만기 6~2개월 전 구간에 해당하는 호실이 없습니다.</div>`}
        </div>
      </div>

      <div class="bass-card">
        <h3 style="margin:0 0 8px;">📅 전체 입주 호실 만기 현황 (${allOccupied.length}실 · 임박 순)</h3>
        <div style="max-height:42vh;overflow-y:auto;">
          ${allOccupied.map((r) => {
            const ct = r.contract;
            const days = r.ledger && r.ledger.days_to_expiry;
            const inWin = Boolean(r.expiry_window && r.expiry_window.in_6_to_2_months_window);
            const ec = r.expiry_check;
            return `
              <button type="button" class="bass-btn exp-room-btn" data-room="${Bass.escapeHtml(r.room_no)}"
                style="width:100%;min-height:48px;text-align:left;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px;${inWin ? "border-color:var(--warn);" : ""}">
                <span><strong>${Bass.escapeHtml(r.room_no)}호</strong> ${Bass.escapeHtml(ct.tenant_name || "")} · ${Bass.escapeHtml(dotDate(ct.expiry_date))}</span>
                <span class="bass-badge ${inWin ? "bass-badge--warn" : ""}" style="white-space:nowrap;">${days != null ? (days >= 0 ? `D-${days}` : `만기+${Math.abs(days)}일`) : "-"}${ec ? ` · ${Bass.escapeHtml(stLabel(ec.status))}` : ""}</span>
              </button>`;
          }).join("") || `<div class="bass-muted" style="font-size:13px;">입주 중인 호실이 없습니다.</div>`}
        </div>
      </div>
    `;
    elTabContent.querySelectorAll(".exp-room-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        selRoomNo = btn.dataset.room;
        showExpiryModal(btn.dataset.room);
      });
    });
  }

  // 운영 ▸ 일괄 납입 통보: 만기 6~2개월 구간 호실 현황 + D-3 사전 통보 · D+1 미납 통보 일괄 실행
  function renderNoticeTab() {
    const noticeRooms = dash.expiry_notice_rooms || [];
    elTabContent.innerHTML = `
        <div style="margin-top:12px;">
      ${noticeRooms.length > 0 ? `
        <div class="bass-card" style="margin-bottom:14px;border-left:4px solid var(--warn);">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <h3 style="margin:0;">⚖️ 만기 6~2개월 전 전출계획 확인 · 연장 재계약 예고 대상 (${noticeRooms.length}실)</h3>
              <p class="bass-muted" style="font-size:13px;margin:4px 0 0;">
                주택임대차보호법 제6조에 따라 만기 <strong>6개월 ~ 2개월 전</strong>에 전출계획을 확인하고, 전출의사가 없을 때 조건 변경(5% 상한) 또는 동일조건 연장 재계약을 예고해야 묵시적 갱신 분쟁을 예방합니다.
              </p>
            </div>
            <span class="bass-badge bass-badge--warn">법정 통지구간 도래</span>
          </div>
          <div class="bass-row" style="margin-top:8px;flex-wrap:wrap;gap:6px;">
            ${noticeRooms.map((nr) => `
              <span class="bass-badge ${nr.check_status === "STAY_CONFIRMED" || nr.check_status === "RENEWAL_PREVIEWED" || nr.check_status === "RENEWAL_ACKNOWLEDGED" ? "bass-badge--ok" : "bass-badge--warn"}">
                ${Bass.escapeHtml(nr.room_no)}호 ${Bass.escapeHtml(nr.tenant_name)} · 만기 ${Bass.escapeHtml(nr.expiry_date)} (D-${nr.days_to_expiry} · 통지마감 ${Bass.escapeHtml(nr.notice_deadline_date)})
              </span>
            `).join("")}
          </div>
        </div>
      ` : ""}

      <div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
          <div>
            <h3 style="margin:0;">🔔 매월 임대료 납입일(=입주일) 3일 전 통보 · 납입일 1일 후 미납 통보</h3>
            <p class="bass-muted" style="font-size:13px;margin:4px 0 0;">
              입주일 일자를 매월 납입일로 자동 산정하여 <strong>납입일 3일 전(D-3)</strong> 사전 안내 및 <strong>납입일 1일 후(D+1)</strong> 미납 호실 독촉 통보를 발송합니다 (완납 호실 오발송 원천 차단).
            </p>
          </div>
          <div class="bass-row" style="flex-wrap:wrap;gap:8px;">
            <label style="font-size:12px;">기준일
              <input id="billingRefDate" type="date" class="bass-input" style="width:145px;" value="${Bass.escapeHtml(dash.as_of_date || dash.today || "")}">
            </label>
            <label style="font-size:12.5px;display:flex;align-items:center;gap:5px;cursor:pointer;">
              <input id="chkCatchupOverdue" type="checkbox" checked>
              납입일+1일 이후 미통보 미납 포함
            </label>
            <button id="btnAutoDispatchBilling" class="bass-btn bass-btn--primary" type="button">⚡ D-3 납입통보 · D+1 미납통보 일괄 실행</button>
          </div>
        </div>
        ${(dash.recent_billing_notices || []).length > 0 ? `
          <div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);display:flex;flex-wrap:wrap;gap:6px;">
            ${(dash.recent_billing_notices || []).slice(0, 6).map((bn) => `
              <span class="bass-badge ${bn.status === "RESOLVED_PAID" ? "bass-badge--ok" : (bn.notice_type === "OVERDUE_1D" ? "bass-badge--err" : "bass-badge--info")}">
                ${bn.notice_type === "OVERDUE_1D" ? "🚨 미납통보" : "🔔 3일전통보"} · ${Bass.escapeHtml(bn.room_no)}호 ${Bass.escapeHtml(bn.tenant_name)} (${Bass.escapeHtml(bn.billing_ym)} · ${fmtWon(bn.amount_due)} · ${bn.status === "RESOLVED_PAID" ? "완납해소" : (bn.status === "ACKNOWLEDGED" ? "세입자확인" : "발송됨")})
              </span>
            `).join("")}
          </div>
        ` : ""}
      </div>

        </div>
    `;

    const elRefDate = document.getElementById("billingRefDate");
    if (elRefDate) {
      elRefDate.addEventListener("change", () => {
        selectedAsOfDate = elRefDate.value || "";
        refresh().catch(Bass.showError);
      });
    }

    const btnAutoDispatch = document.getElementById("btnAutoDispatchBilling");
    if (btnAutoDispatch) {
      btnAutoDispatch.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const refDate = document.getElementById("billingRefDate").value;
          selectedAsOfDate = refDate || "";
          const catchup = document.getElementById("chkCatchupOverdue").checked;
          const res = await Bass.api(APP_ID, "/billing-notices/auto-dispatch", {
            method: "POST",
            body: { branch: currentBranch, as_of_date: refDate || null, include_catchup_overdue: catchup },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await refresh();
        });
      });
    }
  }

  function buildSampleBrokerPaperDataUrl(roomObj) {
    const c = document.createElement("canvas");
    c.width = 760;
    c.height = 500;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fcfbf7";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 3;
    ctx.strokeRect(18, 18, c.width - 36, c.height - 36);

    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 24px sans-serif";
    ctx.fillText("주택임대차표준계약서 (공인중개사 입회 원본 촬영본)", 95, 62);

    ctx.lineWidth = 1;
    ctx.strokeStyle = "#94a3b8";
    ctx.beginPath();
    ctx.moveTo(40, 82);
    ctx.lineTo(720, 82);
    ctx.stroke();

    const bldgName = (dash && dash.building && dash.building.building_name) || "시크빌";
    const addr = (dash && dash.building && dash.building.address) || "서울특별시";
    const rNo = (roomObj && roomObj.room_no) || "203";
    const dep = fmtWon((roomObj && roomObj.deposit) || 10000000);
    const rent = fmtWon((roomObj && roomObj.monthly_rent) || 520000);
    const maint = fmtWon((roomObj && roomObj.maintenance_fee) || 50000);

    ctx.font = "16px sans-serif";
    ctx.fillStyle = "#1e293b";
    const lines = [
      `1. 부동산의 표시 : ${addr} (${bldgName} ${rNo}호)`,
      `2. 보증금 : 금 ${dep}정  /  계약금 : 금 500,000원정 (계약시 영수함)`,
      `3. 차임(월세) : 금 ${rent}정 (후불)  /  관리비 : 금 ${maint}정`,
      `4. 임대차기간 : 인도일(2026-10-29)로부터 12개월간`,
      `5. 특약사항 : 현 시설물 상태 임대차 계약이며, 퇴실 시 기본 입주청소비 약정함.`,
      `6. 임대인(갑) : ${(dash && dash.lessor && dash.lessor.name) || "임대인"} (인)`,
      `7. 임차인(을) : 최중개  (연락처: 010-7788-9900)  (인)`,
      `8. 개업공인중개사 : 한강공인중개사사무소 대표 박공인 (등록 11440-2024-00112)`,
    ];
    lines.forEach((ln, idx) => {
      ctx.fillText(ln, 46, 122 + idx * 42);
    });

    ctx.strokeStyle = "#dc2626";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(650, 405, 36, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#dc2626";
    ctx.font = "bold 14px sans-serif";
    ctx.fillText("공인중개사", 616, 401);
    ctx.fillText("직인날인", 622, 420);

    return c.toDataURL("image/png");
  }

  function renderBrokerTab() {
    const rooms = dash.rooms || [];
    const vacantFirst = rooms.find((r) => !r.is_occupied) || rooms[0];
    const selectedNo = brokerTargetRoomNo || (vacantFirst ? vacantFirst.room_no : "");
    const selRoom = rooms.find((r) => r.room_no === selectedNo) || vacantFirst || {};
    const todayIso = new Date().toISOString().slice(0, 10);

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📸 부동산중개사 개입 지면계약서 사진 첨부 & 계약 원장 일괄 적용</h3>
        <p class="bass-muted" style="font-size:13px;">
          공인중개사 사무소에서 종이 계약서로 계약이 체결된 경우, 촬영한 계약서 사진을 첨부하고 계약 조건을 적용하면
          <code>uploads/contracts/</code> 이미지 보관과 함께 <code>contracts.json</code> · <code>rooms.json</code>(공실 자동마감) · <code>users.json</code>(임차인 마이홈 개통) · <code>payments.json</code> · <code>ops.json</code>(RTMS 신고관제)이 원클릭으로 동기화됩니다.
        </p>

        <div class="room-card" style="margin-top:12px;">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
            <div>
              <label class="bass-label">1단계 · 종이 임대차계약서 촬영 사진 / 스캔본 첨부 (필수, JPG/PNG/WEBP/PDF)</label>
              <input id="brkPhotoFile" type="file" accept="image/*,application/pdf" class="bass-input" style="max-width:360px;">
            </div>
            <button id="btnGenSamplePhoto" class="bass-btn bass-btn--sm" type="button">🧪 샘플 중개계약서 촬영본 자동 생성</button>
          </div>
          <div id="brkPhotoStatus" class="bass-muted" style="font-size:12.5px;margin-top:6px;">
            ${brokerPhotoDataUrl ? `✅ 첨부 완료: ${Bass.escapeHtml(brokerPhotoFilename || "contract_photo.png")}` : "아직 첨부된 계약서 사진이 없습니다. 파일을 선택하거나 우측 샘플 생성 버튼을 눌러보세요."}
          </div>
          <img id="brkPhotoPreview" class="contract-photo-preview" style="display:${brokerPhotoDataUrl && !brokerPhotoDataUrl.startsWith("data:application/pdf") ? "block" : "none"};" src="${brokerPhotoDataUrl || ""}" alt="첨부 계약서 미리보기">
        </div>

        <div class="room-card">
          <label class="bass-label">💡 선택 기능 · 스마트폰 카메라 텍스트 복사(OCR) 붙여넣기 자동 입력</label>
          <div class="bass-row" style="margin-top:6px;">
            <input id="brkOcrText" class="bass-input" style="flex:1 1 180px;min-width:180px;" placeholder="예: ${Bass.escapeHtml(selRoom.room_no || "203")}호 보증금 1,000만원 월세 52만원 관리비 5만원 후불 입주일 ${todayIso} 임차인 최중개 010-7788-9900 한강공인중개사사무소 대표 박공인">
            <button id="btnParseOcr" class="bass-btn bass-btn--sm" type="button">✨ 문구에서 계약조건 자동 채우기</button>
          </div>
        </div>

        <div class="room-card">
          <h4 style="margin:0 0 10px;">2단계 · 호실 · 임차인 · 계약 금액 및 기간 확인</h4>
          <div class="field-grid">
            <div>
              <label class="bass-label">대상 호실</label>
              <select id="brkRoomNo" class="bass-select">
                ${rooms.map((r) => `<option value="${Bass.escapeHtml(r.room_no)}" ${r.room_no === selectedNo ? "selected" : ""}>${Bass.escapeHtml(r.room_no)}호 (${r.is_occupied ? "입주중-교체/재계약" : "공실"})</option>`).join("")}
              </select>
            </div>
            <div>
              <label class="bass-label">임대 유형</label>
              <select id="brkLeaseType" class="bass-select">
                <option value="월세" ${selRoom.lease_type !== "전세" ? "selected" : ""}>월세</option>
                <option value="전세" ${selRoom.lease_type === "전세" ? "selected" : ""}>전세</option>
              </select>
            </div>
            <div>
              <label class="bass-label">납부 방식 (후불 입주월 비과세 자동적용)</label>
              <select id="brkPayTiming" class="bass-select">
                <option value="후불" ${selRoom.pay_timing !== "선불" ? "selected" : ""}>후불</option>
                <option value="선불" ${selRoom.pay_timing === "선불" ? "selected" : ""}>선불</option>
              </select>
            </div>
            <div><label class="bass-label">보증금(원)</label><input id="brkDeposit" type="number" class="bass-input" value="${Number(selRoom.deposit || 10000000)}"></div>
            <div><label class="bass-label">월세(원)</label><input id="brkRent" type="number" class="bass-input" value="${Number(selRoom.monthly_rent || 500000)}"></div>
            <div><label class="bass-label">관리비(원)</label><input id="brkMaint" type="number" class="bass-input" value="${Number(selRoom.maintenance_fee || 50000)}"></div>
            <div><label class="bass-label">영수 계약금(원)</label><input id="brkContractDep" type="number" class="bass-input" value="500000"></div>
            <div><label class="bass-label">계약 체결일</label><input id="brkContractDate" type="date" class="bass-input" value="${todayIso}"></div>
            <div><label class="bass-label">입주(잔금)일</label><input id="brkMoveInDate" type="date" class="bass-input" value="${todayIso}"></div>
            <div><label class="bass-label">계약 기간(개월)</label><input id="brkMonths" type="number" class="bass-input" value="12"></div>
            <div><label class="bass-label">임차인 성명 (필수)</label><input id="brkTenantName" class="bass-input" placeholder="예: 최중개"></div>
            <div><label class="bass-label">임차인 연락처 (필수)</label><input id="brkTenantPhone" class="bass-input" placeholder="010-7788-9900"></div>
            <div><label class="bass-label">임차인 차량번호 (선택)</label><input id="brkCarNo" class="bass-input" placeholder="예: 34나 7890"></div>
          </div>
          <div style="margin-top:8px;">
            <label style="font-size:13px;cursor:pointer;">
              <input id="brkDepReceived" type="checkbox" checked> 계약금 수령 완료 (체크하면 계약금 수납으로 바로 기록됩니다)
            </label>
          </div>
          <div class="bass-card" style="margin-top:10px;padding:10px;">
            <label style="font-size:13.5px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:6px;">
              <input id="brkTaxInvReq" type="checkbox"> 🧾 세입자가 세금계산서 발행을 요청함 (사무실·법인 기숙사 등 모든 호실 가능)
            </label>
            <div id="brkTaxBizWrap" class="field-grid" style="display:none;margin-top:8px;">
              <div><label class="bass-label">사업자등록번호</label><input id="brkBizNo" class="bass-input" placeholder="예: 214-86-12345"></div>
              <div><label class="bass-label">상호(법인명)</label><input id="brkCompName" class="bass-input" placeholder="예: (주)시크디자인"></div>
              <div><label class="bass-label">대표자명</label><input id="brkCeoName" class="bass-input" placeholder="예: 홍길동"></div>
              <div><label class="bass-label">세금계산서 수신 이메일</label><input id="brkBizEmail" type="email" class="bass-input" placeholder="예: tax@company.co.kr"></div>
            </div>
          </div>
        </div>

        <div class="room-card">
          <h4 style="margin:0 0 10px;">3단계 · 개업공인중개사 정보 및 전월세 신고제(RTMS) 확인</h4>
          <div class="field-grid">
            <div><label class="bass-label">중개사무소 상호</label><input id="brkAgency" class="bass-input" placeholder="예: 한강공인중개사사무소"></div>
            <div><label class="bass-label">대표 공인중개사 성명</label><input id="brkAgent" class="bass-input" placeholder="예: 박공인"></div>
            <div><label class="bass-label">중개업소 연락처</label><input id="brkBrokerPhone" class="bass-input" placeholder="02-555-1234"></div>
            <div><label class="bass-label">확정일자 번호 (부여 시 입력)</label><input id="brkFixedNo" class="bass-input" placeholder="미입력 시 중개신고 기준 자동채번"></div>
          </div>
          <div style="margin-top:10px;">
            <label class="bass-label">지면 계약서 주요 특약사항 메모</label>
            <input id="brkSpecial" class="bass-input" placeholder="예: 애완동물 사육 금지, 퇴실 시 입주청소비 7만원 정산 약정">
          </div>
          <div style="margin-top:10px;">
            <label style="font-size:13px;cursor:pointer;">
              <input id="brkRtmsFiled" type="checkbox" checked> 공인중개사가 주택임대차(전월세) 신고서 대행 접수 완료 (해제 시 30일 마감 알림 <code>PENDING</code>으로 관리인·임대인 관제)
            </label>
          </div>
        </div>

        <div class="bass-row" style="justify-content:flex-end;margin-top:10px;">
          <button id="btnSubmitBrokerContract" class="bass-btn bass-btn--primary" type="button">📸 계약서 사진 보관 및 계약내용 즉시 적용</button>
        </div>
      </div>
    `;

    document.getElementById("brkRoomNo").addEventListener("change", (ev) => {
      brokerTargetRoomNo = ev.target.value;
      const picked = rooms.find((x) => x.room_no === brokerTargetRoomNo);
      if (picked) {
        document.getElementById("brkDeposit").value = Number(picked.deposit || 0);
        document.getElementById("brkRent").value = Number(picked.monthly_rent || 0);
        document.getElementById("brkMaint").value = Number(picked.maintenance_fee || 0);
        document.getElementById("brkLeaseType").value = picked.lease_type || "월세";
        document.getElementById("brkPayTiming").value = picked.pay_timing || "후불";
      }
    });

    document.getElementById("brkPhotoFile").addEventListener("change", (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        brokerPhotoDataUrl = String(reader.result || "");
        brokerPhotoFilename = file.name;
        document.getElementById("brkPhotoStatus").textContent = `✅ 첨부 완료: ${file.name} (${Math.round(file.size / 1024)} KB)`;
        const prev = document.getElementById("brkPhotoPreview");
        if (brokerPhotoDataUrl.startsWith("data:image/")) {
          prev.src = brokerPhotoDataUrl;
          prev.style.display = "block";
        } else {
          prev.style.display = "none";
        }
      };
      reader.readAsDataURL(file);
    });

    document.getElementById("btnGenSamplePhoto").addEventListener("click", () => {
      const curRoomNo = document.getElementById("brkRoomNo").value;
      const picked = rooms.find((x) => x.room_no === curRoomNo) || selRoom;
      brokerPhotoDataUrl = buildSampleBrokerPaperDataUrl(picked);
      brokerPhotoFilename = `broker_contract_${curRoomNo || "sample"}.png`;
      document.getElementById("brkPhotoStatus").textContent = `✅ 샘플 촬영 이미지 생성 완료: ${brokerPhotoFilename}`;
      const prev = document.getElementById("brkPhotoPreview");
      prev.src = brokerPhotoDataUrl;
      prev.style.display = "block";
      if (!document.getElementById("brkTenantName").value.trim()) {
        document.getElementById("brkTenantName").value = "최중개";
        document.getElementById("brkTenantPhone").value = "010-7788-9900";
        document.getElementById("brkAgency").value = "한강공인중개사사무소";
        document.getElementById("brkAgent").value = "박공인";
      }
      Bass.updateAppAlert("info", "샘플 공인중개사 표준계약서 촬영 이미지가 첨부되었습니다. 내용을 확인한 뒤 하단 적용 버튼을 눌러주세요.");
    });

    document.getElementById("btnParseOcr").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const raw = document.getElementById("brkOcrText").value.trim()
          || document.getElementById("brkOcrText").getAttribute("placeholder").replace(/^예:\s*/, "");
        const res = await Bass.api(APP_ID, "/contracts/parse-broker-text", {
          method: "POST",
          body: { text: raw },
        });
        const p = res.parsed || {};
        if (p.room_no && rooms.some((r) => r.room_no === p.room_no)) {
          document.getElementById("brkRoomNo").value = p.room_no;
          brokerTargetRoomNo = p.room_no;
        }
        if (p.deposit !== undefined) document.getElementById("brkDeposit").value = p.deposit;
        if (p.monthly_rent !== undefined) document.getElementById("brkRent").value = p.monthly_rent;
        if (p.maintenance_fee !== undefined) document.getElementById("brkMaint").value = p.maintenance_fee;
        if (p.contract_deposit !== undefined) document.getElementById("brkContractDep").value = p.contract_deposit;
        if (p.lease_type) document.getElementById("brkLeaseType").value = p.lease_type;
        if (p.pay_timing) document.getElementById("brkPayTiming").value = p.pay_timing;
        if (p.move_in_date) document.getElementById("brkMoveInDate").value = p.move_in_date;
        if (p.tenant_name) document.getElementById("brkTenantName").value = p.tenant_name;
        if (p.tenant_phone) document.getElementById("brkTenantPhone").value = p.tenant_phone;
        if (p.broker_agency_name) document.getElementById("brkAgency").value = p.broker_agency_name;
        if (p.broker_agent_name) document.getElementById("brkAgent").value = p.broker_agent_name;
        if (p.broker_phone) document.getElementById("brkBrokerPhone").value = p.broker_phone;
        Bass.updateAppAlert("info", res.message);
      });
    });

    document.getElementById("brkTaxInvReq").addEventListener("change", (ev) => {
      document.getElementById("brkTaxBizWrap").style.display = ev.target.checked ? "grid" : "none";
    });

    document.getElementById("btnSubmitBrokerContract").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const roomNo = document.getElementById("brkRoomNo").value;
        const reqInv = Boolean(document.getElementById("brkTaxInvReq").checked);
        const res = await Bass.api(APP_ID, "/contracts/broker-photo", {
          method: "POST",
          body: {
            branch: currentBranch,
            room_no: roomNo,
            photo_data_url: brokerPhotoDataUrl,
            photo_filename: brokerPhotoFilename,
            tenant_name: document.getElementById("brkTenantName").value.trim(),
            tenant_phone: document.getElementById("brkTenantPhone").value.trim(),
            tenant_car_number: document.getElementById("brkCarNo").value.trim(),
            lease_type: document.getElementById("brkLeaseType").value,
            pay_timing: document.getElementById("brkPayTiming").value,
            deposit: Number(document.getElementById("brkDeposit").value || 0),
            monthly_rent: Number(document.getElementById("brkRent").value || 0),
            maintenance_fee: Number(document.getElementById("brkMaint").value || 0),
            contract_deposit: Number(document.getElementById("brkContractDep").value || 0),
            deposit_received: document.getElementById("brkDepReceived").checked,
            contract_date: document.getElementById("brkContractDate").value,
            move_in_date: document.getElementById("brkMoveInDate").value,
            lease_term_months: Number(document.getElementById("brkMonths").value || 12),
            special_terms: document.getElementById("brkSpecial").value.trim(),
            broker_agency_name: document.getElementById("brkAgency").value.trim(),
            broker_agent_name: document.getElementById("brkAgent").value.trim(),
            broker_phone: document.getElementById("brkBrokerPhone").value.trim(),
            rtms_filed_by_broker: document.getElementById("brkRtmsFiled").checked,
            fixed_date_no: document.getElementById("brkFixedNo").value.trim(),
            tax_invoice_requested: reqInv,
            tenant_business_number: reqInv ? document.getElementById("brkBizNo").value.trim() : "",
            tenant_company_name: reqInv ? document.getElementById("brkCompName").value.trim() : "",
            tenant_ceo_name: reqInv ? document.getElementById("brkCeoName").value.trim() : "",
            tenant_email: reqInv ? document.getElementById("brkBizEmail").value.trim() : "",
          },
        });
        brokerPhotoDataUrl = "";
        brokerPhotoFilename = "";
        openPhotoRooms.add(roomNo);
        currentTab = "ledger";
        Bass.updateAppAlert(res.level || "success", res.message, res.hint);
        await refresh();
      });
    });
  }

  // ── 더보기 ▸ 부가세 · 종소세 · 경비: 연도별 자료, 과세/종소세 대상 호실, 세금계산서 대장 & 홈택스 CSV, 면세 사업장현황신고, 경비 지출 등록·수정·삭제 ──
  let taxYear = 0;
  let taxMonth = 0;
  let taxView = "hub";          // hub | income | vat | invoice | expense
  let taxCache = null;
  async function renderTaxTab(useCache) {
    if (!useCache || !taxCache) elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    const yr = taxYear || Number((dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const moParam = taxMonth > 0 ? `&month=${taxMonth}` : "";
    let d;
    const cacheKey = `${currentBranch}|${yr}|${taxMonth}`;
    if (useCache && taxCache && taxCache.key === cacheKey) {
      d = taxCache.d;
    } else {
      try {
        d = await Bass.api(APP_ID, `/tax/summary?branch=${encodeURIComponent(currentBranch)}&year=${yr}${moParam}`);
      } catch (e) { Bass.showError(e); return; }
      taxCache = { key: cacheKey, d };
    }
    if (currentTab !== "tax") return;
    taxYear = d.year;
    const fin = d.summary || {};
    const bCfg = d.building_tax_config || {};
    const invReg = d.tax_invoice_register || {};
    const exemptRep = d.exempt_housing_report || {};
    const exemptRooms = exemptRep.rooms || [];
    const exemptTotals = exemptRep.totals || { room_count: 0, deposit_total: 0, annual_rent_total: 0 };
    const vatRoomsList = bCfg.vat_rooms || fin.vat_rooms || [];
    const incRoomsList = bCfg.income_tax_rooms || fin.income_tax_rooms || [];
    const vatRoomBreakdown = fin.vat_room_breakdown || [];
    const vatPayRows = fin.vat_payment_rows || [];
    const incRoomBreakdown = fin.income_tax_room_breakdown || [];
    const incPayRows = fin.income_tax_payment_rows || [];
    const stickyTh = 'style="position:sticky;top:0;background:var(--surface, #1e293b);z-index:1;"';
    const scrollBox5 = 'style="max-height:215px;overflow-y:auto;overflow-x:auto;border:1px solid var(--border, rgba(148,163,184,0.22));border-radius:8px;"';
    const invoices = invReg.invoices || [];
    const invTotals = invReg.totals || { count: 0, supply_amount: 0, tax_amount: 0, total_amount: 0, ready_for_export_count: 0, issued_count: 0, incomplete_count: 0 };
    const supInfo = invReg.supplier || {};
    const cats = d.expense_categories || {};
    const years = [d.year + 1, d.year, d.year - 1, d.year - 2].filter((v, i, a) => a.indexOf(v) === i && v <= Number((dash.today || "").slice(0, 4)) + 1);
    const inputVatTotal = fin.input_vat_deductible || 0;
    const rawInputVatTotal = fin.raw_input_vat_total || 0;
    const ratioPct = fin.taxable_supply_ratio_pct ?? 100;
    const disclaimerText = d.tax_disclaimer || fin.tax_disclaimer || "⚠️ 참고용 추정치입니다. 실제 신고 전 반드시 세무사·홈택스에서 확인하세요.";

    const statusBadge = (inv) => {
      if (inv.status === "ISSUED") return `<span class="bass-badge bass-badge--ok">발행완료${inv.nts_confirm_no ? ` (${Bass.escapeHtml(inv.nts_confirm_no)})` : ""}</span>`;
      if (inv.status === "MODIFIED") return `<span class="bass-badge bass-badge--info">수정발행${inv.nts_confirm_no ? ` (${Bass.escapeHtml(inv.nts_confirm_no)})` : ""}</span>`;
      if (inv.status === "CANCELLED") return `<span class="bass-badge bass-badge--err">발행취소</span>`;
      if (!inv.ready_for_hometax) return `<span class="bass-badge bass-badge--warn">⚠️ 미비(내보내기 제외): ${Bass.escapeHtml((inv.missing_fields || []).join(", "))}</span>`;
      return `<span class="bass-badge bass-badge--info">✅ 발급가능(미발행)</span>`;
    };

    const yearRow = `<div class="bass-row" style="gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap;">
        <label class="r3-lbl" for="taxYearSel" style="margin:0;white-space:nowrap;">귀속연도</label>
        <select id="taxYearSel" class="bass-select" style="width:auto;font-size:16px;">${years.map((y) => `<option value="${y}" ${y === d.year ? "selected" : ""}>${y}년</option>`).join("")}</select>
      </div>`;
    const monthRow = `<div class="bass-row" style="gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap;">
        <label class="r3-lbl" for="taxMonthSel" style="margin:0;white-space:nowrap;">발급 대상 월</label>
        <select id="taxMonthSel" class="bass-select" style="width:auto;font-size:16px;">
          <option value="0" ${taxMonth === 0 ? "selected" : ""}>전체 월 (연간)</option>
          ${[1,2,3,4,5,6,7,8,9,10,11,12].map((m) => `<option value="${m}" ${m === taxMonth ? "selected" : ""}>${m}월</option>`).join("")}
        </select>
        <button id="btnDlHometaxCsv" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 홈택스 세금계산서 일괄발급 CSV</button>
      </div>`;
    const incBtns = `<div class="bass-row" style="gap:8px;margin-bottom:10px;"><button id="btnDlTaxCsv" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 종소세 명세서 CSV</button></div>`;
    const exeBtns = `<div class="bass-row" style="gap:8px;margin-bottom:10px;"><button id="btnDlExemptCsv" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 면세 사업장현황신고 CSV</button></div>`;
    const compAllHub = fin.comprehensive_combined_estimate || {};
    const vatNetHub = (d.vat_periods || []).reduce((a, x) => a + Number(x.net_vat_payable ?? x.vat ?? 0), 0);
    const hubBtn = (v, icon, title, line) => `<button type="button" class="bass-btn tax-hub-btn" data-v="${v}" style="display:block;width:100%;text-align:left;padding:14px;margin-bottom:10px;">
        <div style="font-size:16px;font-weight:800;">${icon} ${title}</div><div class="bass-muted" style="font-size:13px;margin-top:4px;line-height:1.5;">${line}</div></button>`;
    const hub = hubBtn("income", "🧮", "종합소득세", `총수입 ${fmtWon(fin.gross_revenue)} · 예상 종합소득세 ${fmtWon(compAllHub.total_tax_inc_local || 0)}`) +
      hubBtn("vat", "🧾", "부가세", `1기·2기 매출세액 − 매입세액 · 연간 예상 납부세액 ${fmtWon(vatNetHub)}${vatNetHub < 0 ? " (마이너스는 환급 예상)" : ""}`) +
      hubBtn("invoice", "📑", "세금계산서", `전체 ${invTotals.count || 0}건 · 발행완료 ${invTotals.issued_count || 0}건 · 내보내기 가능 ${invTotals.ready_for_export_count || 0}건 · 정보미비 ${invTotals.incomplete_count || 0}건`) +
      hubBtn("expense", "🗂️", "경비 · 면세 신고", `경비 ${(d.outlays || []).length}건 · 면세 주거 호실 ${exemptTotals.room_count || 0}개 · 사업장현황신고 자료`);
    const TAX_TITLES = { income: "종합소득세", vat: "부가세", invoice: "세금계산서", expense: "경비 · 면세 신고" };
    const taxBody = {
      income: incBtns + `      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 6px;">🧮 종합소득세 자료 (${d.year}년 약정 귀속월 기준 · 부가세 제외 공급가액)</h3>
        ${(() => {
          const compAll = fin.comprehensive_combined_estimate || {};
          const compComm = fin.commercial_comprehensive_estimate || {};
          return `<div style="font-size:13.5px;line-height:1.8;">
            • 연간 총수입금액(부가세 제외 공급가액) <strong>${fmtWon(fin.gross_revenue)}</strong>
              <span class="bass-muted" style="font-size:12px;">(부가세 포함 총수납액 ${fmtWon(fin.gross_receipts_inc_vat ?? fin.gross_revenue)} 중 부가세 예수금 ${fmtWon(fin.vat_collected || 0)} 제외 · 수납 ${fin.payment_count || 0}건)</span><br>
            ${fin.carryover_excluded_amount ? `• <span style="color:var(--warn, #f59e0b);font-size:12.5px;">장부이관 과거 누적 이월 수납분(<strong>${fmtWon(fin.carryover_excluded_amount)}</strong>, ${fin.carryover_excluded_count || 0}건)은 연간 수입 왜곡 방지를 위해 당해 수입에서 자동 제외되었습니다.</span><br>` : ""}
            • <strong>① 주택임대 수입 (주거용 면세 호실)</strong>: <strong>${fmtWon(fin.housing_revenue || 0)}</strong>
              ${fin.housing_separate_eligible
                ? `→ 연 2,000만 원 이하 분리과세(15.4%) 선택 가능: 일반(필요경비율 50%·공제 200만) 예상 <strong>${fmtWon(fin.housing_separate_tax_general || 0)}</strong> / 등록임대(60%·400만) 예상 <strong>${fmtWon(fin.housing_separate_tax_registered || 0)}</strong>`
                : `→ 연 2,000만 원 초과로 분리과세 대상 아님 (아래 <strong>종합과세 누진세율 구간</strong>으로 합산 산출)`}<br>
            • <strong>② 상가·사무실 임대 수입 (과세 호실)</strong>: <strong>${fmtWon(fin.commercial_revenue || 0)}</strong>
              → 분리과세 불가 · 실제 필요경비(안분 ${fmtWon(fin.commercial_expense_allocated || 0)}) 차감 후 순소득 <strong>${fmtWon(fin.commercial_noi || 0)}</strong>
              ${fin.housing_separate_eligible && (fin.commercial_noi || 0) > 0
                ? `(상가 단독 종합과세 추정: <strong>${fmtWon(compComm.total_tax_inc_local || 0)}</strong> · ${Bass.escapeHtml(compComm.bracket_label || "")})`
                : ""}<br>
            • 건물 전체 실제 필요경비 합계: <strong>${fmtWon(fin.total_operating_expense)}</strong> · 전체 순임대소득(NOI): <strong style="color:var(--primary-2);">${fmtWon(fin.noi)}</strong><br>
            • <strong>③ 종합소득세 누진세율 추정 (주택+상가 합산 순임대소득 ${fmtWon(compAll.net_income || 0)} · 기본공제 ${fmtWon(compAll.basic_deduction || 0)} 반영 과세표준 ${fmtWon(compAll.taxable_base || 0)})</strong>:<br>
              &nbsp;&nbsp;적용 구간: <strong>${Bass.escapeHtml(compAll.bracket_label || "-")}</strong> →
              산출세액 ${fmtWon(compAll.income_tax || 0)} + 지방소득세(10%) ${fmtWon(compAll.local_income_tax || 0)} =
              예상 종합소득세 합계 <strong style="color:var(--primary-2);">${fmtWon(compAll.total_tax_inc_local || 0)}</strong>
              <span class="bass-muted" style="font-size:12px;">(타 소득 미합산 단독 임대소득 기준 참고용 추정치)</span>
          </div>`;
        })()}
        <div style="font-weight:700;font-size:13px;margin:8px 0 4px;">📊 종합소득세 호실별 수입 산출내역 (5줄 스크롤)</div>
        ${incRoomBreakdown.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>호실</th><th>임차인</th><th>수납건수</th><th>월세 합계</th><th>관리비 합계</th><th>종소세 수입(공급가액)</th><th>총수납액(VAT포함)</th></tr>
            </thead>
            <tbody>
              ${incRoomBreakdown.map((rb) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(rb.room_no)}호</strong></td>
                  <td>${Bass.escapeHtml(rb.tenant_name || "-")}</td>
                  <td>${rb.count}건</td>
                  <td>${fmtWon(rb.rent_amount)}</td>
                  <td>${fmtWon(rb.maintenance_amount)}</td>
                  <td><strong>${fmtWon(rb.supply_amount)}</strong></td>
                  <td>${fmtWon(rb.total_amount)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:12.5px;">종소세 대상 호실 수납 내역이 없습니다.</div>'}
        <details style="margin-top:8px;">
          <summary style="cursor:pointer;font-size:12.5px;font-weight:700;">🔍 호실별 월세·관리비 건별 납부 상세내역 보기 (${incPayRows.length}건)</summary>
          <div ${scrollBox5} style="max-height:215px;overflow-y:auto;overflow-x:auto;margin-top:6px;border:1px solid var(--border, rgba(148,163,184,0.22));border-radius:8px;">
            <table class="bass-table" style="width:100%;font-size:12px;margin:0;">
              <thead ${stickyTh}>
                <tr><th>납부일</th><th>귀속월(약정)</th><th>호실</th><th>입금자</th><th>월세</th><th>관리비</th><th>종소세 수입(공급가액)</th><th>부가세</th><th>총수납액</th></tr>
              </thead>
              <tbody>
                ${incPayRows.map((pr) => `
                  <tr>
                    <td>${Bass.escapeHtml(pr.paid_date)}</td>
                    <td>${Bass.escapeHtml(pr.billing_month)}</td>
                    <td><strong>${Bass.escapeHtml(pr.room_no)}호</strong></td>
                    <td>${Bass.escapeHtml(pr.payer_name || "-")}</td>
                    <td>${fmtWon(pr.rent_amount)}</td>
                    <td>${fmtWon(pr.maintenance_amount)}</td>
                    <td><strong>${fmtWon(pr.supply_amount)}</strong></td>
                    <td>${fmtWon(pr.vat_amount)}</td>
                    <td>${fmtWon(pr.total_amount)}</td>
                  </tr>
                `).join("") || '<tr><td colspan="9" class="bass-muted">내역 없음</td></tr>'}
              </tbody>
            </table>
          </div>
        </details>
      </div>`,
      vat: `      <div class="bass-row" style="justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
        <span class="bass-muted" style="font-size:13px;">부가세 대상 ${vatRoomsList.length}개 호실 · 종소세 대상 ${incRoomsList.length}개 호실</span>
        <button id="btnGoBldgTax" class="bass-btn bass-btn--sm" type="button">📑 대상 호실 설정 →</button>
      </div>` + `      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 6px;">🧾 부가가치세 자료 (${d.year}년 · 매출세액 − 공제 매입세액 · 공통매입 안분 반영)</h3>
        ${(d.vat_periods || []).map((p) => `<div style="font-size:13.5px;line-height:1.8;margin-bottom:4px;">
          <strong>${Bass.escapeHtml(p.label)}</strong> · 과세 공급가액 ${fmtWon(p.supply)} ·
          매출세액(실수납) <strong>${fmtWon(p.output_vat ?? p.vat)}</strong> −
          공제 매입세액(안분후) <strong>${fmtWon(p.input_vat || 0)}</strong> =
          예상 납부세액 <strong style="color:var(--primary-2);">${fmtWon(p.net_vat_payable ?? p.vat)}</strong>
          ${p.non_deductible_expense ? `<span class="bass-muted" style="font-size:12px;">(불공제 경비 ${fmtWon(p.non_deductible_expense)} 제외)</span>` : ""}
        </div>`).join("")}
        <div class="bass-muted" style="font-size:12px;margin-top:4px;margin-bottom:8px;">
          • 과세(상가·사무실)와 면세(주거)가 함께 있는 건물의 <strong>공통경비(엘리베이터·공용전기·청소 등) 매입세액은 과세공급비율(${ratioPct}%)로 안분</strong>하여 공제합니다 (원본 매입세액 ${fmtWon(rawInputVatTotal)} → 공제 가능 ${fmtWon(inputVatTotal)}).<br>
          • <strong>적격증빙(세금계산서·사업용카드·현금영수증) + 일반과세자 거래처</strong> 지출만 매입세액 공제되며, 간이과세자 영수증·면세(화재보험)·경조사비·주거전용 수리비는 매입세액에서 제외(종소세 경비로만 반영)됩니다.<br>
          • 기존 수납 중 부가세가 0원인 건은 임의로 10%를 추정하지 않고 <strong>실제 수납된 부가세만</strong> 집계하여 요약과 표가 100% 일치합니다.
        </div>
        <div style="font-weight:700;font-size:13px;margin:8px 0 4px;">📊 부가세 호실별 산출내역 (과세 호실별 1기·2기·연간 집계 · 5줄 스크롤)</div>
        ${vatRoomBreakdown.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>호실</th><th>임차인</th><th>수납건수</th><th>1기 공급가액(세액)</th><th>2기 공급가액(세액)</th><th>연간 공급가액</th><th>연간 부가세(실수납)</th></tr>
            </thead>
            <tbody>
              ${vatRoomBreakdown.map((rb) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(rb.room_no)}호</strong></td>
                  <td>${Bass.escapeHtml(rb.tenant_name || "-")}</td>
                  <td>${rb.count}건</td>
                  <td>${fmtWon(rb.h1_supply)} (${fmtWon(rb.h1_vat)})</td>
                  <td>${fmtWon(rb.h2_supply)} (${fmtWon(rb.h2_vat)})</td>
                  <td><strong>${fmtWon(rb.supply_amount)}</strong></td>
                  <td><strong>${fmtWon(rb.vat_amount)}</strong></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:12.5px;">과세 호실 수납 내역이 없습니다.</div>'}
        <details style="margin-top:8px;">
          <summary style="cursor:pointer;font-size:12.5px;font-weight:700;">🔍 과세 호실 건별 납부 상세내역 보기 (${vatPayRows.length}건)</summary>
          <div ${scrollBox5} style="max-height:215px;overflow-y:auto;overflow-x:auto;margin-top:6px;border:1px solid var(--border, rgba(148,163,184,0.22));border-radius:8px;">
            <table class="bass-table" style="width:100%;font-size:12px;margin:0;">
              <thead ${stickyTh}>
                <tr><th>납부일</th><th>귀속월(약정)</th><th>호실</th><th>입금자</th><th>월세</th><th>관리비</th><th>공급가액</th><th>부가세</th><th>합계</th></tr>
              </thead>
              <tbody>
                ${vatPayRows.map((pr) => `
                  <tr>
                    <td>${Bass.escapeHtml(pr.paid_date)}</td>
                    <td>${Bass.escapeHtml(pr.billing_month)}</td>
                    <td><strong>${Bass.escapeHtml(pr.room_no)}호</strong></td>
                    <td>${Bass.escapeHtml(pr.payer_name || "-")}</td>
                    <td>${fmtWon(pr.rent_amount)}</td>
                    <td>${fmtWon(pr.maintenance_amount)}</td>
                    <td>${fmtWon(pr.supply_amount)}</td>
                    <td>${fmtWon(pr.vat_amount)}</td>
                    <td><strong>${fmtWon(pr.total_amount)}</strong></td>
                  </tr>
                `).join("") || '<tr><td colspan="9" class="bass-muted">내역 없음</td></tr>'}
              </tbody>
            </table>
          </div>
        </details>
      </div>`,
      invoice: monthRow + `<div class="bass-muted" style="font-size:12.5px;margin:0 0 8px;line-height:1.6;">정보미비로 표시된 건은 <strong>수납 현황 ▸ 해당 호실 ▸ 계약내용</strong>에서 사업자등록번호·상호·대표자명·이메일·사업자등록상 주소를 입력하면 발급 가능해집니다 (주거용·기숙사 호실 포함).</div>` + `      <div class="bass-card" style="margin-bottom:12px;">
        <div class="bass-row" style="justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;">
          <h3 style="margin:0;">📑 세금계산서 발급 리스트 (청구월 약정 지급일 기준 월 1건 · 5줄 스크롤)</h3>
          <button id="btnDlHometaxCsv2" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 홈택스 일괄발급 포맷(CSV)</button>
        </div>
        ${supInfo.supplier_ready === false ? `<div style="font-size:12.5px;color:var(--warn, #f59e0b);margin-bottom:6px;">⚠️ 공급자(임대인) 사업자등록번호(${Bass.escapeHtml(supInfo.business_number || "미입력")})를 확인해 주세요: ${Bass.escapeHtml((supInfo.supplier_issues || []).join(", "))}</div>` : ""}
        <div class="bass-muted" style="font-size:12px;margin-bottom:6px;">
          월세 세금계산서는 입금 건별이 아니라 <strong>청구월(약정 지급일) 기준 호실당 월 1장</strong>으로 집계됩니다.
          필수 정보(사업자번호 10자리·상호·대표자명·이메일)가 빠진 건이나 이미 <strong>발행완료(ISSUED)</strong>로 기록된 건은 이중 발행·오류 방지를 위해 CSV 내보내기에서 자동 제외됩니다.
        </div>
        <div style="font-size:13.5px;margin-bottom:8px;">
          전체 <strong>${invTotals.count || 0}건</strong> (CSV 내보내기 가능 <strong>${invTotals.ready_for_export_count || 0}건</strong> · 발행완료 <strong>${invTotals.issued_count || 0}건</strong> · 정보미비 제외 <strong>${invTotals.incomplete_count || 0}건</strong>) ·
          공급가액 합계 <strong>${fmtWon(invTotals.supply_amount || 0)}</strong> ·
          세액(10%) 합계 <strong>${fmtWon(invTotals.tax_amount || 0)}</strong> ·
          합계금액 <strong>${fmtWon(invTotals.total_amount || 0)}</strong>
        </div>
        ${invoices.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>귀속월(약정일)</th><th>호실 / 품목</th><th>사업자번호</th><th>상호 / 대표자</th><th>이메일</th><th>공급가액 / 세액</th><th>검증·발행상태</th><th>관리</th></tr>
            </thead>
            <tbody>
              ${invoices.map((inv, idx) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(inv.billing_month)}</strong><br><span class="bass-muted" style="font-size:12.5px;">작성일 ${Bass.escapeHtml(inv.write_date)}</span></td>
                  <td><strong>${Bass.escapeHtml(inv.room_no)}호</strong><br><span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(inv.item_name || "")}</span></td>
                  <td>${Bass.escapeHtml(inv.buyer_business_number || "-")}${inv.buyer_biz_checksum_warning ? `<br><span style="color:var(--warn);font-size:12.5px;">${Bass.escapeHtml(inv.buyer_biz_checksum_warning)}</span>` : ""}</td>
                  <td>${Bass.escapeHtml(inv.buyer_company_name || "-")} (${Bass.escapeHtml(inv.buyer_ceo_name || "-")})</td>
                  <td>${Bass.escapeHtml(inv.buyer_email || "-")}</td>
                  <td>${fmtWon(inv.supply_amount)}<br><span class="bass-muted" style="font-size:12.5px;">세액 ${fmtWon(inv.tax_amount)}</span><br><span class="bass-badge ${inv.charge_type === "02" ? "bass-badge--warn" : "bass-badge--ok"}">${Bass.escapeHtml(inv.charge_type_label || (inv.charge_type === "02" ? "청구(02)" : "영수(01)"))}</span></td>
                  <td>${statusBadge(inv)}</td>
                  <td><button type="button" class="bass-btn bass-btn--sm btn-inv-status" data-idx="${idx}">발행기록</button></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:13px;">해당 기간에 세금계산서 발급 대상 내역이 없습니다.</div>'}
      </div>`,
      expense: exeBtns + `      <div class="bass-card" style="margin-bottom:12px;">
        <div class="bass-row" style="justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;">
          <h3 style="margin:0;">🏠 주택임대(면세) 사업장현황신고 자료 (${d.year}년 귀속 · 매년 2월 10일 마감 · 5줄 스크롤)</h3>
          <button id="btnDlExemptCsv2" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 사업장현황신고 CSV</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-bottom:6px;">
          주거용(면세) 호실 임대는 부가가치세 대신 <strong>매년 2월 10일까지 전년도 호실별 보증금·월세 수입금액(수입금액 검토표)</strong>을 국세청에 신고합니다.
          (대상 ${exemptTotals.room_count || 0}개 호실 · 보증금 합계 <strong>${fmtWon(exemptTotals.deposit_total || 0)}</strong> · 연간 월세수입 합계 <strong>${fmtWon(exemptTotals.annual_rent_total || 0)}</strong> · ${Bass.escapeHtml(exemptTotals.deemed_rent_note || "")})
        </div>
        ${exemptRooms.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>호실</th><th>임차인</th><th>임대기간</th><th>보증금</th><th>약정 월세</th><th>연간 월세수입</th><th>수납건수</th></tr>
            </thead>
            <tbody>
              ${exemptRooms.map((er) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(er.room_no)}호</strong></td>
                  <td>${Bass.escapeHtml(er.tenant_name || "-")}</td>
                  <td>${Bass.escapeHtml(er.move_in_date || "-")} ~ ${Bass.escapeHtml(er.expiry_date || "-")}</td>
                  <td>${fmtWon(er.deposit)}</td>
                  <td>${fmtWon(er.monthly_rent)}</td>
                  <td><strong>${fmtWon(er.annual_rent_revenue)}</strong></td>
                  <td>${er.payment_count}건</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:12.5px;">면세 주거용 호실이 없습니다.</div>'}
      </div>` + `      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 8px;">➕ 경비 지출 등록 (적격증빙·거래처 과세유형·공통매입 안분 구분)</h3>
        <div class="r3-grid">
          <label class="r3-fld">지출일<input id="outDate" class="bass-input" type="date" value="${Bass.escapeHtml(dash.today || "")}"></label>
          <label class="r3-fld">분류<select id="outCat" class="bass-select">${Object.entries(cats).map(([k, v]) => `<option value="${k}">${Bass.escapeHtml(v)}</option>`).join("")}</select></label>
          <label class="r3-fld">적요<input id="outTitle" class="bass-input" placeholder="예: 202호 보일러 수리"></label>
          <label class="r3-fld">금액(원)<input id="outAmt" type="number" class="bass-input" value="0"></label>
          <label class="r3-fld">거래처 상호<input id="outVendor" class="bass-input" placeholder="예: 한빛설비"></label>
          <label class="r3-fld">거래처 사업자번호<input id="outVendorBiz" class="bass-input" placeholder="예: 120-81-12345"></label>
          <label class="r3-fld">거래처 과세유형
            <select id="outVendorTax" class="bass-select">
              <option value="GENERAL">일반과세자 (매입공제 가능)</option>
              <option value="SIMPLIFIED">간이과세자 (영수증 불공제)</option>
              <option value="EXEMPT">면세사업자·비사업자 (불공제)</option>
            </select>
          </label>
          <label class="r3-fld">증빙유형
            <select id="outEvid" class="bass-select">
              <option value="TAX_INVOICE">전자세금계산서</option>
              <option value="CARD">사업용 신용카드</option>
              <option value="CASH_RECEIPT">지출증빙 현금영수증</option>
              <option value="SIMPLE_RECEIPT">간이영수증·계좌이체 (불공제)</option>
              <option value="EXEMPT">면세·경조사 청첩장 (불공제)</option>
            </select>
          </label>
          <label class="r3-fld">과세·면세 사용구분
            <select id="outScope" class="bass-select">
              <option value="COMMON">건물 공통경비 (과세비율 안분)</option>
              <option value="TAXABLE_ONLY">상가·과세호실 전용 (100% 공제)</option>
              <option value="EXEMPT_ONLY">주거·면세호실 전용 (부가세 불공제)</option>
            </select>
          </label>
          <label class="r3-fld">메모<input id="outMemo" class="bass-input"></label>
        </div>
        <button id="btnAddOutlay" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin-top:8px;">💾 저장</button>
      </div>` + `      <div class="bass-card">
        <h3 style="margin:0 0 6px;">📋 경비 내역 (${(d.outlays || []).length}건) <span class="bass-muted" style="font-size:12px;">터치하면 고칠 수 있습니다</span></h3>
        ${(fin.expenses_by_category || []).filter((c) => c.amount > 0).map((c) => `<span class="bass-badge bass-badge--info" style="margin:2px;">${Bass.escapeHtml(c.label)} ${fmtWon(c.amount)}</span>`).join("")}
        <div id="outlayList" style="margin-top:8px;">${(d.outlays || []).map((o) => `
          <button type="button" class="outlay-row bass-btn" data-id="${o.id}" style="display:grid;grid-template-columns:78px 1fr auto;gap:6px;width:100%;text-align:left;margin-bottom:4px;font-size:13px;">
            <span>${Bass.escapeHtml(o.expense_date.slice(2).replace(/-/g, "."))}</span>
            <span>${Bass.escapeHtml(o.title)}<br><span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(o.category_label)}${o.vendor ? " · " + Bass.escapeHtml(o.vendor) : ""} · ${o.is_vat_deductible === false ? "부가세 불공제(종소세 경비)" : `공제 매입세액 ${fmtWon(o.effective_input_vat ?? o.input_vat ?? 0)}`}</span></span>
            <strong>${fmtWon(o.amount)}</strong></button>`).join("") || '<div class="bass-muted">등록된 경비가 없습니다.</div>'}</div>
      </div>`,
    };
    const backRow = `<div class="bass-row" style="gap:8px;margin:0 0 10px;"><button id="btnTaxHub" class="bass-btn bass-btn--sm" type="button">← 세무 허브</button><strong style="font-size:15px;">${TAX_TITLES[taxView] || ""}</strong></div>`;
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:10px;padding:10px 12px;border-left:4px solid var(--warn, #f59e0b);background:rgba(245,158,11,0.08);font-size:13px;font-weight:700;">
        ${Bass.escapeHtml(disclaimerText)}
      </div>
      ${yearRow}
      ${taxView === "hub" || !taxBody[taxView] ? hub : backRow + taxBody[taxView]}`;

    const on = (id, ev, fn2) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn2); };
    on("taxYearSel", "change", (e) => { taxYear = Number(e.target.value); renderTaxTab(); });
    on("taxMonthSel", "change", (e) => { taxMonth = Number(e.target.value); renderTaxTab(); });
    on("btnDlTaxCsv", "click", () => {
      downloadWithAuth(`/tax/export-csv?branch=${encodeURIComponent(currentBranch)}&year=${d.year}`, `rent3_tax_statement_${currentBranch}_${d.year}.csv`);
    });
    const dlHometax = () => {
      const moQ = taxMonth > 0 ? `&month=${taxMonth}` : "";
      const moSuf = taxMonth > 0 ? `_${String(taxMonth).padStart(2, "0")}` : "";
      downloadWithAuth(`/tax/export-hometax-csv?branch=${encodeURIComponent(currentBranch)}&year=${d.year}${moQ}`, `hometax_tax_invoice_${currentBranch}_${d.year}${moSuf}.csv`);
    };
    on("btnDlHometaxCsv", "click", dlHometax);
    on("btnDlHometaxCsv2", "click", dlHometax);
    const dlExempt = () => {
      downloadWithAuth(`/tax/export-exempt-csv?branch=${encodeURIComponent(currentBranch)}&year=${d.year}`, `exempt_housing_status_${currentBranch}_${d.year}.csv`);
    };
    on("btnDlExemptCsv", "click", dlExempt);
    on("btnDlExemptCsv2", "click", dlExempt);

    // 세무 허브 ↔ 하위 화면
    elTabContent.querySelectorAll(".tax-hub-btn").forEach((b) => b.addEventListener("click", () => {
      taxView = b.dataset.v;
      if (taxView !== "invoice") taxMonth = 0;
      renderTaxTab(true);
      window.scrollTo(0, 0);
    }));
    on("btnTaxHub", "click", () => { taxView = "hub"; taxMonth = 0; renderTaxTab(true); window.scrollTo(0, 0); });

    elTabContent.querySelectorAll(".btn-inv-status").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inv = invoices[Number(btn.dataset.idx)];
        if (inv) showInvoiceStatusModal(inv);
      });
    });

    on("btnGoBldgTax", "click", () => switchTab("bldgtax"));
    on("btnAddOutlay", "click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/outlays", {
          method: "POST",
          body: {
            branch: currentBranch,
            category: document.getElementById("outCat").value,
            title: document.getElementById("outTitle").value.trim() || "유지보수",
            amount: Number(document.getElementById("outAmt").value || 0),
            vendor: document.getElementById("outVendor").value.trim(),
            vendor_business_number: document.getElementById("outVendorBiz").value.trim(),
            vendor_tax_type: document.getElementById("outVendorTax").value,
            evidence_type: document.getElementById("outEvid").value,
            usage_scope: document.getElementById("outScope").value,
            memo: document.getElementById("outMemo").value.trim(),
            expense_date: document.getElementById("outDate").value || null,
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });
    elTabContent.querySelectorAll(".outlay-row").forEach((row) => row.addEventListener("click", () => {
      const o = (d.outlays || []).find((x) => x.id === row.dataset.id);
      if (o) showOutlayModal(o, cats);
    }));
  }

  // 건물 ▸ 부가세 · 소득세 대상 호실 설정 (설정 화면은 이곳 한 곳에만 둔다)
  async function renderBldgTaxTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    let d;
    try {
      d = await Bass.api(APP_ID, `/tax/summary?branch=${encodeURIComponent(currentBranch)}&year=${taxYear || Number((dash.today || "").slice(0, 4))}`);
    } catch (e) { Bass.showError(e); return; }
    if (currentTab !== "bldgtax") return;
    const fin = d.summary || {};
    const bCfg = d.building_tax_config || {};
    const vatRoomsList = bCfg.vat_rooms || fin.vat_rooms || [];
    const incRoomsList = bCfg.income_tax_rooms || fin.income_tax_rooms || [];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 6px;">🏢 건물 부가세 · 종합소득세 대상 호실 설정</h3>
        <div class="r3-grid">
          <label class="r3-fld">부가세 납부 대상 호실 (사무실·상가)
            <input id="txVatRooms" class="bass-input" value="${Bass.escapeHtml(vatRoomsList.join(", "))}" placeholder="예: 201, 202, 203, 301, 302, 303">
          </label>
          <label class="r3-fld">종합소득세 신고 대상 호실
            <input id="txIncRooms" class="bass-input" value="${Bass.escapeHtml(incRoomsList.join(", "))}" placeholder="예: 201, 202, 203, 301, 302, 303, 401~703">
          </label>
        </div>
        <div class="bass-row" style="gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap;">
          <label style="font-size:13px;display:flex;align-items:center;gap:6px;cursor:pointer;">
            <input id="txSyncContracts" type="checkbox"> 현재 입주 계약에도 과세 여부 즉시 동기화
          </label>
          <button id="btnSaveTaxRooms" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 대상 호실 저장</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">
          현재 설정: 부가세 대상 <strong>${vatRoomsList.length}개 호실</strong> (${Bass.escapeHtml(vatRoomsList.join(", ") || "없음")}) ·
          종소세 대상 <strong>${incRoomsList.length}개 호실</strong>
        </div>
      </div>
    `;
    document.getElementById("btnSaveTaxRooms").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/building/tax-rooms", {
          method: "POST",
          body: {
            branch: currentBranch,
            vat_rooms: document.getElementById("txVatRooms").value,
            income_tax_rooms: document.getElementById("txIncRooms").value,
            sync_contracts: Boolean(document.getElementById("txSyncContracts").checked),
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });
  }

  function showInvoiceStatusModal(inv) {
    const hist = inv.history || [];
    openModal(`📑 ${Bass.escapeHtml(inv.room_no)}호 (${Bass.escapeHtml(inv.billing_month)}) 세금계산서 발행 상태 기록`, `
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:8px;">
        작성일자(약정일): <strong>${Bass.escapeHtml(inv.write_date)}</strong> · 공급가액 ${fmtWon(inv.supply_amount)} · 세액 ${fmtWon(inv.tax_amount)}<br>
        공급받는자: ${Bass.escapeHtml(inv.buyer_company_name || "-")} (${Bass.escapeHtml(inv.buyer_business_number || "-")})
      </div>
      <div class="r3-grid">
        <label class="r3-fld">발행 상태
          <select id="isStatus" class="bass-select">
            <option value="DRAFT" ${inv.status === "DRAFT" ? "selected" : ""}>미발행 (작성대기 · CSV 포함)</option>
            <option value="ISSUED" ${inv.status === "ISSUED" ? "selected" : ""}>발행완료 (중복발행 방지 CSV 제외)</option>
            <option value="MODIFIED" ${inv.status === "MODIFIED" ? "selected" : ""}>수정발행</option>
            <option value="CANCELLED" ${inv.status === "CANCELLED" ? "selected" : ""}>발행취소 (CSV 제외)</option>
          </select>
        </label>
        <label class="r3-fld">국세청 승인번호 (24자리 등)
          <input id="isNtsNo" class="bass-input" value="${Bass.escapeHtml(inv.nts_confirm_no || "")}" placeholder="예: 20260715-41000000-12345678">
        </label>
      </div>
      <label class="r3-fld" style="margin-top:8px;">비고 / 수정·취소 사유
        <input id="isMemo" class="bass-input" placeholder="예: 홈택스 일괄발급 완료">
      </label>
      ${hist.length ? `<div class="bass-muted" style="font-size:12.5px;margin-top:8px;">
        <strong>상태 변경 이력:</strong><br>
        ${hist.map((h) => `• ${Bass.escapeHtml((h.at || "").slice(0, 16))} : ${Bass.escapeHtml(h.from_status)} → <strong>${Bass.escapeHtml(h.to_status)}</strong> ${h.nts_confirm_no ? `(${Bass.escapeHtml(h.nts_confirm_no)})` : ""} ${Bass.escapeHtml(h.memo || "")}`).join("<br>")}
      </div>` : ""}
      <div class="bass-row" style="gap:6px;margin-top:10px;">
        <button id="isSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 발행 상태 저장</button>
      </div>`);
    $m("isSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/tax/invoices/status", {
          method: "POST",
          body: {
            branch: currentBranch,
            room_no: inv.room_no,
            billing_month: inv.billing_month,
            status: $m("isStatus").value,
            nts_confirm_no: $m("isNtsNo").value.trim(),
            memo: $m("isMemo").value.trim(),
            write_date: inv.write_date,
            supply_amount: inv.supply_amount,
            tax_amount: inv.tax_amount,
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await renderTaxTab();
      });
    });
  }

  function showTenantBizModal(vc) {
    openModal(`🧾 ${Bass.escapeHtml(vc.room_no)}호 세금계산서 사업자 정보`, `
      <div class="r3-grid">
        <label class="r3-fld">사업자등록번호<input id="tbBizNo" class="bass-input" value="${Bass.escapeHtml(vc.business_number || "")}" placeholder="예: 214-86-12345"></label>
        <label class="r3-fld">상호 (법인명)<input id="tbComp" class="bass-input" value="${Bass.escapeHtml(vc.company_name || "")}" placeholder="예: (주)시크디자인"></label>
        <label class="r3-fld">대표자명<input id="tbCeo" class="bass-input" value="${Bass.escapeHtml(vc.ceo_name || "")}" placeholder="예: 홍길동"></label>
        <label class="r3-fld">세금계산서 수신 이메일<input id="tbEmail" class="bass-input" type="email" value="${Bass.escapeHtml(vc.email || "")}" placeholder="예: tax@company.co.kr"></label>
      </div>
      <label style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:13px;cursor:pointer;">
        <input id="tbVatTarget" type="checkbox" ${vc.vat_target !== false ? "checked" : ""}> 부가가치세 과세 계약 (10% 세금계산서 발행 대상)
      </label>
      <div class="bass-row" style="gap:6px;margin-top:10px;">
        <button id="tbSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 저장</button>
      </div>`);
    $m("tbSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/tax/tenant-biz", {
          method: "POST",
          body: {
            branch: currentBranch,
            room_no: vc.room_no,
            business_number: $m("tbBizNo").value.trim(),
            company_name: $m("tbComp").value.trim(),
            ceo_name: $m("tbCeo").value.trim(),
            email: $m("tbEmail").value.trim(),
            vat_target: Boolean($m("tbVatTarget").checked),
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await refresh();
      });
    });
  }

  function showOutlayModal(o, cats) {
    openModal("✏️ 경비 내역 수정", `
      <div class="r3-grid">
        <label class="r3-fld">지출일<input id="oeDate" class="bass-input" type="date" value="${Bass.escapeHtml(o.expense_date)}"></label>
        <label class="r3-fld">분류<select id="oeCat" class="bass-select">${Object.entries(cats).map(([k, v]) => `<option value="${k}" ${k === o.category ? "selected" : ""}>${Bass.escapeHtml(v)}</option>`).join("")}</select></label>
        <label class="r3-fld">적요<input id="oeTitle" class="bass-input" value="${Bass.escapeHtml(o.title)}"></label>
        <label class="r3-fld">금액(원)<input id="oeAmt" type="number" class="bass-input" value="${o.amount}"></label>
        <label class="r3-fld">거래처 상호<input id="oeVendor" class="bass-input" value="${Bass.escapeHtml(o.vendor || "")}"></label>
        <label class="r3-fld">거래처 사업자번호<input id="oeVendorBiz" class="bass-input" value="${Bass.escapeHtml(o.vendor_business_number || "")}"></label>
        <label class="r3-fld">거래처 과세유형
          <select id="oeVendorTax" class="bass-select">
            <option value="GENERAL" ${(o.vendor_tax_type || "GENERAL") === "GENERAL" ? "selected" : ""}>일반과세자</option>
            <option value="SIMPLIFIED" ${o.vendor_tax_type === "SIMPLIFIED" ? "selected" : ""}>간이과세자 (영수증 불공제)</option>
            <option value="EXEMPT" ${o.vendor_tax_type === "EXEMPT" ? "selected" : ""}>면세사업자·비사업자</option>
          </select>
        </label>
        <label class="r3-fld">증빙유형
          <select id="oeEvid" class="bass-select">
            <option value="TAX_INVOICE" ${(o.evidence_type || "TAX_INVOICE") === "TAX_INVOICE" ? "selected" : ""}>전자세금계산서</option>
            <option value="CARD" ${o.evidence_type === "CARD" ? "selected" : ""}>사업용 신용카드</option>
            <option value="CASH_RECEIPT" ${o.evidence_type === "CASH_RECEIPT" ? "selected" : ""}>지출증빙 현금영수증</option>
            <option value="SIMPLE_RECEIPT" ${o.evidence_type === "SIMPLE_RECEIPT" ? "selected" : ""}>간이영수증·계좌이체</option>
            <option value="EXEMPT" ${o.evidence_type === "EXEMPT" ? "selected" : ""}>면세·경조사 청첩장</option>
          </select>
        </label>
        <label class="r3-fld">사용구분
          <select id="oeScope" class="bass-select">
            <option value="COMMON" ${(o.usage_scope || "COMMON") === "COMMON" ? "selected" : ""}>건물 공통경비 (안분)</option>
            <option value="TAXABLE_ONLY" ${o.usage_scope === "TAXABLE_ONLY" ? "selected" : ""}>상가·과세호실 전용</option>
            <option value="EXEMPT_ONLY" ${o.usage_scope === "EXEMPT_ONLY" ? "selected" : ""}>주거·면세호실 전용</option>
          </select>
        </label>
        <label class="r3-fld">메모<input id="oeMemo" class="bass-input" value="${Bass.escapeHtml(o.memo || "")}"></label>
      </div>
      <div class="bass-row" style="gap:6px;margin-top:10px;">
        <button id="oeSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 저장</button>
        <button id="oeDel" class="bass-btn bass-btn--sm bass-btn--danger" type="button">🗑️ 삭제</button>
      </div>`);
    $m("oeSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/outlays/edit", {
          method: "POST",
          body: {
            branch: currentBranch,
            outlay_id: o.id,
            category: $m("oeCat").value,
            title: $m("oeTitle").value,
            amount: Number($m("oeAmt").value || 0),
            vendor: $m("oeVendor").value,
            vendor_business_number: $m("oeVendorBiz").value,
            vendor_tax_type: $m("oeVendorTax").value,
            evidence_type: $m("oeEvid").value,
            usage_scope: $m("oeScope").value,
            expense_date: $m("oeDate").value,
            memo: $m("oeMemo").value,
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await refresh();
      });
    });
    $m("oeDel").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        if (!window.confirm(`'${o.title}' (${fmtWon(o.amount)}) 지출을 지울까요?`)) return;
        const res = await Bass.api(APP_ID, "/outlays/delete", { method: "POST", body: { branch: currentBranch, outlay_id: o.id } });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await refresh();
      });
    });
  }

  // ── 운영 ▸ 메시지: 호실별 1:1 대화(임차인과) + 접어 둔 계약 협상 기록. 관리인 지원 채팅·계약방 대화는 섞지 않는다 ──
  let msgRoom = "";
  let msgTimer = null;
  const fmtMsgTime = (iso) => (iso && iso.length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");

  async function renderMessagesTab() {
    clearInterval(msgTimer);
    if (msgRoom) { await renderMessageThread(); return; }
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    let d;
    try { d = await Bass.api(APP_ID, `/messages/rooms?branch=${encodeURIComponent(currentBranch)}`); } catch (e) { Bass.showError(e); return; }
    if (currentTab !== "messages") return;
    const rows = d.rooms.filter((r) => r.count > 0 || r.is_occupied).sort((a, b) => (b.unread - a.unread) || String(b.last_at).localeCompare(String(a.last_at)) || String(a.room_no).localeCompare(String(b.room_no), "ko", { numeric: true }));
    elTabContent.innerHTML = `
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:10px;">호실을 누르면 입주자와의 대화가 열립니다. 수납·미납 안내문은 [수납 현황]에서 문자로 보내고, 이곳은 앱 안 대화입니다.</div>
      ${rows.map((r) => `
        <button type="button" class="bass-btn msg-room" data-room="${Bass.escapeHtml(r.room_no)}" style="display:block;width:100%;text-align:left;margin-bottom:6px;padding:10px 12px;">
          <div class="bass-row" style="justify-content:space-between;gap:8px;">
            <strong>${Bass.escapeHtml(r.room_no)}호 ${Bass.escapeHtml(r.tenant_name || "공실")}</strong>
            ${r.unread > 0 ? `<span class="bass-badge bass-badge--err">안 읽음 ${r.unread}</span>` : ""}
          </div>
          <div class="bass-muted" style="font-size:12.5px;margin-top:2px;">${r.count ? `${Bass.escapeHtml(r.last_role === "lessor" ? "나: " : "")}${Bass.escapeHtml(r.last_body)} · ${fmtMsgTime(r.last_at)}` : "대화 없음"}</div>
        </button>`).join("") || '<div class="bass-muted">대화할 호실이 없습니다.</div>'}`;
    elTabContent.querySelectorAll(".msg-room").forEach((b) => b.addEventListener("click", () => { msgRoom = b.dataset.room; renderMessagesTab(); }));
  }

  async function renderMessageThread() {
    const room = msgRoom;
    let d;
    try { d = await Bass.api(APP_ID, `/messages/rooms/${encodeURIComponent(room)}?branch=${encodeURIComponent(currentBranch)}`); } catch (e) { Bass.showError(e); msgRoom = ""; return; }
    if (currentTab !== "messages" || msgRoom !== room) return;
    const neg = d.negotiation || { count: 0, items: [] };
    const digestLine = neg.purged ? (neg.purged_note || "보관 기간이 지나 삭제되었습니다.") : neg.digest_ok === true ? "✅ 체결 때 기록한 대화 기록 해시와 일치합니다"
      : neg.digest_ok === false ? "⚠️ 체결 때 기록한 해시와 달라졌습니다 — 기록이 바뀌었을 수 있습니다"
      : "체결 전 기록입니다";
    const bubble = (m) => m.kind === "system"
      ? `<div class="cm-sys">${Bass.escapeHtml(m.body)} <span>${fmtMsgTime(m.created_at)}</span></div>`
      : `<div class="cm-b ${m.sender_role === "lessor" ? "is-mine" : ""}"><div><div class="cm-meta">${Bass.escapeHtml(m.sender_role === "lessor" ? "나" : m.sender_name || "임차인")} · ${fmtMsgTime(m.created_at)}</div>${Bass.escapeHtml(m.body)}</div></div>`;
    elTabContent.innerHTML = `
      <div class="bass-row" style="gap:8px;margin:0 0 10px;"><button id="btnMsgBack" class="bass-btn bass-btn--sm" type="button">← 목록</button><strong>${Bass.escapeHtml(room)}호 ${Bass.escapeHtml(d.tenant_name || "공실")}</strong></div>
      <div id="msgList" class="cm-list" style="max-height:52vh;">${d.messages.map(bubble).join("") || '<div class="bass-muted" style="font-size:13px;">아직 대화가 없습니다.</div>'}</div>
      <textarea id="msgInput" class="bass-input" rows="2" placeholder="입주자에게 보낼 메시지" style="width:100%;margin-top:8px;"></textarea>
      <button id="btnMsgSend" class="bass-btn bass-btn--primary" type="button" style="margin-top:6px;">보내기</button>
      <details style="margin-top:14px;"><summary class="bass-muted" style="cursor:pointer;font-size:13px;min-height:32px;">계약 협상 기록 ${neg.purged ? `(보관 기간 만료로 삭제됨 · 당시 ${neg.original_count}건)` : `${neg.count}건 ${neg.count ? "보기" : ""}`}</summary>
        <div class="bass-muted" style="font-size:12px;margin:4px 0;">${Bass.escapeHtml(digestLine)}</div>
        ${neg.items.map((m) => `<div style="font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--border);">[${m.step}단계] ${Bass.escapeHtml(m.sender_role === "lessor" ? "임대인" : m.sender_role === "tenant" ? "임차인" : "안내")}: ${Bass.escapeHtml(m.body)}${m.status ? ` <span class="bass-badge">${Bass.escapeHtml(({ open: "답 기다림", accepted: "수락됨", declined: "거절됨", withdrawn: "철회됨" })[m.status] || m.status)}</span>` : ""}</div>`).join("")}
      </details>`;
    const list = document.getElementById("msgList");
    list.scrollTop = list.scrollHeight;
    document.getElementById("btnMsgBack").addEventListener("click", () => { msgRoom = ""; renderMessagesTab(); });
    document.getElementById("btnMsgSend").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const text = document.getElementById("msgInput").value.trim();
        if (!text) { Bass.updateAppAlert("warning", "내용을 입력해 주세요."); return; }
        await Bass.api(APP_ID, "/chats", { method: "POST", body: { branch: currentBranch, room_no: room, message: text } });
        await renderMessageThread();
      });
    });
    // 열어 둔 동안 새 글만 가져온다(입력 중인 글은 건드리지 않는다)
    msgTimer = setInterval(async () => {
      if (currentTab !== "messages" || msgRoom !== room || !document.getElementById("msgList")) { clearInterval(msgTimer); return; }
      try {
        const nd = await Bass.api(APP_ID, `/messages/rooms/${encodeURIComponent(room)}?branch=${encodeURIComponent(currentBranch)}`);
        const box = document.getElementById("msgList");
        if (box && nd.messages.length !== d.messages.length) {
          d = nd;
          const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
          box.innerHTML = nd.messages.map(bubble).join("");
          if (atBottom) box.scrollTop = box.scrollHeight;
        }
      } catch (_) { /* 다음에 다시 */ }
    }, 8000);
  }

  function renderOpsTab() {
    const tickets = dash.tickets || [];

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🔧 A/S 하자보수 접수 대장 & 협력업체 원클릭 배정</h3>
        ${tickets.length ? tickets.map((t) => `
          <div class="room-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <strong>[${Bass.escapeHtml(t.room_no)}호 · ${Bass.escapeHtml(t.category)}] ${Bass.escapeHtml(t.title)}</strong>
              <span class="bass-badge ${t.status === "COMPLETED" ? "bass-badge--ok" : "bass-badge--warn"}">${t.status}</span>
            </div>
            <div class="bass-muted" style="font-size:13px;margin:6px 0;">${Bass.escapeHtml(t.description)} (요청자: ${Bass.escapeHtml(t.tenant_name)})</div>
            ${t.status === "RECEIVED" ? `
              <button class="bass-btn bass-btn--sm bass-btn--primary btn-assign-as" data-id="${t.id}" type="button">👷 협력 설비기사 즉시 배정 (채팅 자동안내)</button>
            ` : t.status === "ASSIGNED" ? `
              <button class="bass-btn bass-btn--sm bass-btn--primary btn-done-as" data-id="${t.id}" type="button">✅ 수리 완료 & 필요경비 자동 등록</button>
            ` : `<div style="font-size:12.5px;color:var(--ok);">완료 내역: ${fmtWon(t.repair_cost)} (${t.cost_burden})</div>`}
          </div>
        `).join("") : `<p class="bass-muted">접수된 A/S 티켓이 없습니다.</p>`}
      </div>
    `;

    elTabContent.querySelectorAll(".btn-assign-as").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/tickets/${btn.dataset.id}/assign`, {
            method: "POST",
            body: { vendor_name: "우리설비 김기사", vendor_phone: "010-5050-8282", visit_date: "내일 14:00" },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-done-as").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/tickets/${btn.dataset.id}/complete`, {
            method: "POST",
            body: { repair_cost: 60000, cost_burden: "LANDLORD", resolution_note: "노후 부품 신품 무상 교체 완료" },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

  }

  function renderRoomsTab() {
    const rooms = dash.rooms || [];
    const sortedRooms = rooms.slice().sort((a, b) => String(a.room_no).localeCompare(String(b.room_no), "ko", { numeric: true }));
    const DIRECTIONS = ["", "남향", "남동향", "남서향", "동향", "서향", "북향", "북동향", "북서향"];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🏷️ 임대매물 등록</h3>
        <p class="bass-muted" style="font-size:13px;margin:4px 0 10px;">호실을 고르고 조건을 확인한 뒤 등록하면 임차인 화면에 공개됩니다. 입주 중인 호실은 고를 수 없습니다.</p>
        <div class="field-grid" style="margin:10px 0;">
          <div>
            <label class="bass-label">호실</label>
            <select id="nrRoom" class="bass-select">
              <option value="">호실을 선택하세요</option>
              ${sortedRooms.map((r) => `<option value="${Bass.escapeHtml(r.id)}" ${r.is_occupied ? "disabled" : ""}>${Bass.escapeHtml(r.room_no)}호 — ${r.is_occupied ? "입주중" : (r.is_published ? "매물 공개중" : "공실")}</option>`).join("")}
              <option value="__new__">➕ 목록에 없는 새 호실 추가…</option>
            </select>
          </div>
          <div id="nrNewWrap" style="display:none;"><label class="bass-label">새 호실 번호</label><input id="nrNo" class="bass-input" placeholder="예: 704"></div>
          <div>
            <label class="bass-label">부동산 유형</label>
            <select id="nrCat" class="bass-select">
              <option value="주거용 원룸">주거용 원룸 (면세)</option>
              <option value="오피스텔">오피스텔</option>
              <option value="상가/사무실">상가/사무실 (VAT 10%)</option>
            </select>
          </div>
          <div><label class="bass-label">입주가능일</label>
            <input id="nrAvail" type="date" class="bass-input">
            <label style="font-size:12.5px;display:flex;align-items:center;gap:6px;margin-top:6px;cursor:pointer;"><input id="nrAvailNow" type="checkbox" checked> 즉시입주</label>
          </div>
          <div><label class="bass-label">보증금(원)</label><input id="nrDep" type="number" class="bass-input" value="5000000"></div>
          <div><label class="bass-label">월세(원)</label><input id="nrRent" type="number" class="bass-input" value="500000"></div>
          <div><label class="bass-label">관리비(원)</label><input id="nrMaint" type="number" class="bass-input" value="50000"></div>
          <div><label class="bass-label">전용면적(㎡)</label><input id="nrArea" type="number" step="0.1" class="bass-input" value="23"></div>
          <div><label class="bass-label">향</label>
            <select id="nrDir" class="bass-select">${DIRECTIONS.map((d) => `<option value="${d}">${d || "선택 안 함"}</option>`).join("")}</select>
          </div>
          <div><label class="bass-label">층</label><input id="nrFloor" type="number" class="bass-input" value="2"></div>
          <div><label class="bass-label">전체 층수</label><input id="nrTotalFloors" type="number" class="bass-input" value="0" placeholder="예: 7"></div>
        </div>
        <div style="margin-bottom:10px;">
          <label class="bass-label">매물 소개 (선택)</label>
          <input id="nrDesc" class="bass-input" placeholder="예: 남향 풀옵션, 도배·장판 새로 시공">
        </div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <button id="btnCreateRoom" class="bass-btn bass-btn--primary" type="button">📢 임대매물 등록</button>
          <button id="btnUnpublishRoom" class="bass-btn" type="button" style="display:none;">매물 내리기</button>
        </div>
      </div>

      <details class="bass-card">
        <summary style="cursor:pointer;font-weight:800;">🔑 호실별 도어락 · 등록 차량 관리</summary>
        <div style="margin-top:10px;">
        ${rooms.map((r) => `
          <div class="room-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <div>
                <strong>${r.room_no}호</strong> · 현재 도어락: <code>${Bass.escapeHtml(r.doorlock_pwd)}</code>
                · 등록차량: <strong>${Bass.escapeHtml((r.tenant && r.tenant.car_number) || "없음")}</strong>
              </div>
              <div class="bass-row">
                <input class="bass-input dl-inp" data-id="${r.id}" style="width:110px;" value="${Bass.escapeHtml(r.doorlock_pwd)}">
                <button class="bass-btn bass-btn--sm btn-dl-save" data-id="${r.id}" type="button">비번 변경</button>
              </div>
            </div>
          </div>
        `).join("")}
        </div>
      </details>
    `;

    const $nr = (id) => document.getElementById(id);
    const roomById = (id) => rooms.find((x) => x.id === id);

    function syncAvailInputs() {
      $nr("nrAvail").disabled = $nr("nrAvailNow").checked;
    }
    $nr("nrAvailNow").addEventListener("change", syncAvailInputs);
    syncAvailInputs();

    $nr("nrRoom").addEventListener("change", () => {
      const v = $nr("nrRoom").value;
      $nr("nrNewWrap").style.display = v === "__new__" ? "block" : "none";
      const r = roomById(v);
      $nr("btnUnpublishRoom").style.display = r && r.is_published ? "inline-flex" : "none";
      $nr("btnCreateRoom").textContent = r && r.is_published ? "📢 변경 내용으로 다시 등록" : "📢 임대매물 등록";
      if (!r) return;
      $nr("nrCat").value = r.room_category || "주거용 원룸";
      $nr("nrDep").value = r.deposit;
      $nr("nrRent").value = r.monthly_rent;
      $nr("nrMaint").value = r.maintenance_fee;
      $nr("nrArea").value = r.area_sqm;
      $nr("nrDir").value = r.direction || "";
      $nr("nrFloor").value = r.floor;
      $nr("nrTotalFloors").value = r.total_floors || 0;
      $nr("nrDesc").value = r.status_description || "";
      const af = r.available_from || "즉시입주";
      const isDate = /^\d{4}-\d{2}-\d{2}$/.test(af);
      $nr("nrAvailNow").checked = !isDate;
      $nr("nrAvail").value = isDate ? af : "";
      syncAvailInputs();
    });

    $nr("btnUnpublishRoom").addEventListener("click", function () {
      const r = roomById($nr("nrRoom").value);
      if (!r) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/publish`, {
          method: "POST",
          body: { is_published: false },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });

    $nr("btnCreateRoom").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const sel = $nr("nrRoom").value;
        const picked = roomById(sel);
        const roomNo = sel === "__new__" ? $nr("nrNo").value.trim() : (picked ? picked.room_no : "");
        if (!roomNo) {
          Bass.updateAppAlert("warning", sel === "__new__" ? "새 호실 번호를 입력해 주세요." : "등록할 호실을 선택해 주세요.");
          return;
        }
        const avail = $nr("nrAvailNow").checked ? "즉시입주" : $nr("nrAvail").value;
        if (!avail) {
          Bass.updateAppAlert("warning", "입주가능일을 고르거나 '즉시입주'를 체크해 주세요.");
          return;
        }
        const cat = $nr("nrCat").value;
        const res = await Bass.api(APP_ID, "/rooms", {
          method: "POST",
          body: {
            branch: currentBranch,
            room_no: roomNo,
            room_category: cat,
            vat_applicable: cat === "상가/사무실",
            deposit: Number($nr("nrDep").value || 0),
            monthly_rent: Number($nr("nrRent").value || 0),
            maintenance_fee: Number($nr("nrMaint").value || 0),
            area_sqm: Number($nr("nrArea").value || 0),
            direction: $nr("nrDir").value,
            floor: Number($nr("nrFloor").value || 0),
            total_floors: Number($nr("nrTotalFloors").value || 0),
            available_from: avail,
            status_description: $nr("nrDesc").value.trim() || "신규 등록 호실",
            is_published: true,
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });

    elTabContent.querySelectorAll(".btn-dl-save").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const inp = elTabContent.querySelector(`.dl-inp[data-id="${btn.dataset.id}"]`);
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/doorlock`, {
            method: "POST",
            body: { new_pwd: inp.value.trim() },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });
  }

  refresh().catch(Bass.showError);

  // 🔗 기존 임차인 연결 요청: 임차인이 링크에서 요청하고 전화를 걸어 오면, 로그인해 있는 이 화면에 승인 창이 자동으로 뜬다.
  //    임차인이 전화로 불러 주는 4자리 확인번호를 입력해 승인하면 창이 닫힌다. 승인한 뒤 임차인의 진행 상태는 화면 위 띠로 계속 보인다.
  (function watchConnect() {
    if (VIA_PIN || ME_ADMIN) return;
    const shown = new Set();
    const telOf = (p) => "tel:" + String(p || "").replace(/[^0-9+]/g, "");
    function renderBar(recent) {
      let bar = document.getElementById("connBar");
      if (!recent.length) { if (bar) bar.remove(); return; }
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "connBar";
        bar.style.cssText = "margin:8px 0;padding:10px 12px;border-radius:12px;border:1px solid var(--ok);background:rgba(34,197,94,.12);font-size:13.5px;line-height:1.6;";
        elTabContent.parentNode.insertBefore(bar, elTabContent);
      }
      const icon = { DONE: "✅", SETTING: "⏳", APPROVED: "📲" };
      bar.innerHTML = "<strong>🔗 임차인 연결 진행</strong><br>" + recent.map((n) => `${icon[n.state] || ""} ${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)} — ${Bass.escapeHtml(n.label)}`).join("<br>");
    }
    function showModal(n) {
      shown.add(n.id);
      const body = openModal(`🔗 ${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)}님 연결 요청`, `
        <div style="font-size:14.5px;line-height:1.7;margin-bottom:4px;">임차인이 전화를 걸어 <strong>4자리 확인번호</strong>를 불러 줍니다. 그 번호를 아래에 입력하세요.</div>
        <a href="${Bass.escapeHtml(telOf(n.tenant_phone))}" style="display:block;font-size:18px;font-weight:800;margin:6px 0 10px;">📞 임차인 ${Bass.escapeHtml(n.tenant_phone || "")} (눌러서 전화)</a>
        <input id="cmCode" class="bass-input" inputmode="numeric" maxlength="4" autocomplete="off" placeholder="확인번호" aria-label="확인번호" style="font-size:30px;letter-spacing:12px;text-align:center;min-height:60px;">
        <div style="display:flex;gap:8px;margin-top:12px;">
          <button id="cmOk" class="bass-btn bass-btn--primary" type="button" style="flex:1;min-height:54px;font-size:17px;">✅ 승인</button>
          <button id="cmNo" class="bass-btn" type="button" style="min-height:54px;">거절</button></div>`);
      const code = body.querySelector("#cmCode");
      setTimeout(() => code.focus(), 50);
      body.querySelector("#cmOk").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(n.id)}/approve`, { method: "POST", body: { branch: currentBranch, code: code.value.trim() } });
          closeModal();
          Bass.updateAppAlert("success", res.message);
          await refresh();
          poll();
        });
      });
      body.querySelector("#cmNo").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(n.id)}/reject`, { method: "POST", body: { branch: currentBranch } });
          closeModal();
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    }
    async function poll() {
      if (document.visibilityState === "hidden") return;
      let d;
      try { d = await Bass.api(APP_ID, `/connect-requests?branch=${encodeURIComponent(currentBranch)}`); } catch (_) { return; }
      renderBar(d.recent || []);
      const next = (d.pending || []).find((n) => !shown.has(n.id));
      if (next && !document.getElementById("r3Modal")) showModal(next);   // 다른 창을 보고 있으면 닫힐 때까지 기다린다
    }
    setTimeout(poll, 1500);
    setInterval(poll, 6000);
  })();
})();
