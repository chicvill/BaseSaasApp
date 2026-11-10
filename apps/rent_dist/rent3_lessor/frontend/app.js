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

  const elBranch = document.getElementById("branchSelect");
  const elTabContent = document.getElementById("tabContent");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
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
  function tickClock() {
    const n = new Date();
    const p = (v) => String(v).padStart(2, "0");
    const t = `${n.getFullYear()}.${p(n.getMonth() + 1)}.${p(n.getDate())} ${p(n.getHours())}:${p(n.getMinutes())}`;
    const el = document.getElementById("bldgTitle");
    if (el && hdrName) el.textContent = `🏢 ${hdrName} ${t}`;
  }
  setInterval(tickClock, 30000);

  async function refresh() {
    const qAsOf = selectedAsOfDate ? `&as_of_date=${encodeURIComponent(selectedAsOfDate)}` : "";
    dash = await Bass.api(APP_ID, `/dashboard?branch=${encodeURIComponent(currentBranch)}${qAsOf}`);
    document.getElementById("poolRevBadge").textContent = `JSON Pool v${dash.pool_revision}`;
    renderFranchiseBanner();

    elBranch.innerHTML = (dash.branches || []).map((b) =>
      `<option value="${b.branch}" ${b.branch === dash.branch ? "selected" : ""}>🏢 ${Bass.escapeHtml(b.building_name)} (${b.branch})</option>`
    ).join("");

    const b = dash.building || {};
    const l = dash.lessor || {};
    hdrName = `${b.building_name || ""}${l.username ? `[${l.username}]` : ""}`;
    tickClock();
    const isAdmin = Boolean(savedSession && (savedSession.role === "admin" || savedSession.role === "superadmin"));
    elBranch.hidden = !isAdmin;   // 건물 선택은 본사 관리자만

    const fin = dash.financial_summary || {};
    document.getElementById("kpiGrid").innerHTML = `
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">가동 호실 / 전체</div><div class="kpi-val">${fin.occupied_rooms || 0} / ${fin.total_rooms || 0}실 (${fin.occupancy_rate || 0}%)</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">지점 미납 합계 (가짜체납 0)</div><div class="kpi-val" style="color:${dash.total_unpaid_branch > 0 ? "var(--err)" : "var(--ok)"};">${fmtWon(dash.total_unpaid_branch)}</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">연간 누적 수납액</div><div class="kpi-val">${fmtWon(fin.gross_revenue)}</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">순운영이익 (NOI)</div><div class="kpi-val" style="color:var(--primary-2);">${fmtWon(fin.noi)}</div></div>
    `;

    renderTab();
  }

  elBranch.addEventListener("change", () => {
    currentBranch = elBranch.value;
    refresh().catch(Bass.showError);
  });

  function switchTab(tabName) {
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
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === currentTab));
    if (currentTab === "ledger") renderLedgerTab();
    else if (currentTab === "broker") renderBrokerTab();
    else if (currentTab === "tax") renderTaxTab();
    else if (currentTab === "ops") renderOpsTab();
    else renderRoomsTab();
  }

  // ── 수납 현황 탭: 호실 풀다운 하나 + 버튼 네 개(수납내역·계약내용·만기통보·전출). 자세한 설명은 이용 매뉴얼에 둔다. ──
  // 호실 카드·모달 스타일: index.html 이 브라우저에 캐시돼도 화면이 깨지지 않도록 app.js 가 직접 넣는다
  (function () {
    const st = document.createElement("style");
    st.textContent = `
    .r3-lbl { display: block; font-size: 13px; font-weight: 700; margin: 2px 0 4px; }
    .r3-fld { display: grid; gap: 3px; font-size: 12.5px; }
    .r3-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .r3-msg { width: 100%; font-size: 13px; line-height: 1.5; }
    .rc-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }
    .rc-title { font-size: 17px; }
    .rc-title span { font-size: 15px; margin-left: 4px; }
    .rc-unpaid { font-weight: 800; font-size: 14px; color: var(--ok); }
    .rc-unpaid.is-late { color: var(--err); font-size: 16px; }
    .rc-btns { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-bottom: 8px; }
    .rc-btns .bass-btn { padding-left: 4px; padding-right: 4px; white-space: nowrap; }
    .rc-warn { background: hsl(46 96% 56%) !important; color: #2b2300 !important; border-color: hsl(46 96% 40%) !important; font-weight: 800; }
    .rc-info { font-size: 13px; line-height: 1.7; }
    .r3-modal { position: fixed; inset: 0; z-index: 1000; background: rgba(0, 0, 0, .55); display: flex; align-items: flex-end; justify-content: center; }
    .r3-modal__box { width: 100%; max-width: 560px; max-height: 92vh; overflow-y: auto; background: #17172e; color: #eceaf7; border: 1px solid var(--border); border-radius: 16px 16px 0 0; padding: 14px 14px 22px; }
    @media (min-width: 640px) { .r3-modal { align-items: center; } .r3-modal__box { border-radius: 16px; } }
    .r3-modal__head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 15px; }
    .r3-modal__x { min-width: 40px; min-height: 40px; font-size: 18px; }
    .pr { display: grid; grid-template-columns: 44px minmax(0, 1.5fr) minmax(0, 1fr) minmax(0, 1fr); gap: 4px; align-items: center; padding: 3px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; min-height: 40px; }
    .pr .bass-input { padding: 6px 4px; font-size: 12.5px; min-width: 0; width: 100%; }
    .pr--head { font-weight: 700; min-height: 0; border-bottom: 2px solid var(--border); }
    .pr--un, .pr--ok { grid-template-columns: 44px 1fr; }
    .pr--un .pr__un { color: var(--err); font-weight: 800; }
    .pr-list { max-height: 205px; overflow-y: auto; border-bottom: 1px solid var(--border); }
    `;
    document.head.appendChild(st);
  })();
  let selRoomNo = "";
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

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;opacity:0;";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (_e) { ok = false; }
      ta.remove();
      return ok;
    }
  }

  // 복사 후 휴대폰이면 임차인 문자창을 연다(붙여넣기만 하면 됨)
  async function copyAndSms(text, phone) {
    const ok = await copyText(text);
    Bass.updateAppAlert(ok ? "success" : "warning", ok ? "복사했습니다 — 임차인 문자창에 붙여넣어 보내세요." : "복사에 실패했습니다. 글을 직접 길게 눌러 복사해 주세요.");
    const d = digitsOf(phone);
    if (ok && d && /Android|iPhone|iPad/i.test(navigator.userAgent)) {
      const a = document.createElement("a");
      a.href = "sms:" + d;
      a.click();
    }
  }

  function closeModal() {
    const m = document.getElementById("r3Modal");
    if (m) m.remove();
  }

  function openModal(title, html) {
    closeModal();
    const bg = document.createElement("div");
    bg.className = "r3-modal";
    bg.id = "r3Modal";
    bg.innerHTML = `<div class="r3-modal__box" role="dialog" aria-modal="true">
      <div class="r3-modal__head"><strong>${title}</strong><button type="button" class="bass-btn r3-modal__x" aria-label="닫기">✕</button></div>
      <div class="r3-modal__body">${html}</div></div>`;
    bg.addEventListener("mousedown", (e) => { if (e.target === bg) closeModal(); });
    bg.querySelector(".r3-modal__x").addEventListener("click", closeModal);
    document.body.appendChild(bg);
    return bg.querySelector(".r3-modal__body");
  }

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

  // ① 수납내역: 알림문자 붙여넣기 · 직접수납 · 월별 납부 내역(날짜·금액 고치기)
  function showPayModal(no) {
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
      ps.forEach((p) => rows.push(`<div class="pr" data-pay="${p.id}"><span class="pr__ym">${shortYm(m.ym)}</span>
        <input type="date" class="bass-input pr-date" value="${Bass.escapeHtml(p.paid_date)}" aria-label="납부일">
        <input type="number" class="bass-input pr-rent" value="${p.rent}" aria-label="월세">
        <input type="number" class="bass-input pr-fee" value="${p.fee}" aria-label="관리비"></div>`));
      if (!ps.length && !(m.unpaid > 0)) {
        rows.push(`<div class="pr pr--ok"><span class="pr__ym">${shortYm(m.ym)}</span><span class="bass-muted">${m.status === "EXEMPT_POSTPAID" ? "입주월(후불) — 익월부터 청구" : "완납"}</span></div>`);
      }
    });
    const oldest = (lg.months || []).find((m) => m.unpaid > 0);
    const defAmt = oldest ? oldest.unpaid : Number(r.monthly_rent || 0) + Number(r.maintenance_fee || 0);
    const draft = receiptMsg[no] || (lg.total_unpaid > 0 ? unpaidText(r) : "");
    const body = openModal(`💰 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 수납내역`, `
      <label class="r3-lbl">입금 알림문자 붙여넣기</label>
      <textarea id="pmSms" class="bass-input" rows="2" placeholder="[은행] 10/28 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} ${(defAmt).toLocaleString("ko-KR")}원 입금"></textarea>
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin:6px 0 10px;">
        <button id="pmParse" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="flex:1 1 100%;">⚡ 문자 읽고 수납</button>
        <input id="pmAmt" class="bass-input" type="number" style="width:112px;" value="${defAmt}" aria-label="직접 수납액">
        <button id="pmDirect" class="bass-btn bass-btn--sm" type="button">💰 직접수납</button>
      </div>
      <div class="r3-lbl">월별 납부 내역 <span class="bass-muted">(날짜·금액을 고치면 바로 저장)</span></div>
      <div class="pr pr--head"><span class="pr__ym">월</span><span>납부일</span><span>월세</span><span>관리비</span></div>
      <div class="pr-list">${rows.join("") || '<div class="bass-muted" style="padding:10px;">내역이 없습니다.</div>'}</div>
      <label class="r3-lbl" style="margin-top:12px;">임차인에게 보낼 메시지</label>
      ${msgOf("pmMsg", draft)}
      <button id="pmCopy" class="bass-btn bass-btn--sm" type="button" style="margin-top:6px;">📋 복사 → 임차인 문자창</button>`);

    const after = async (res) => {
      Bass.updateAppAlert(res.level || "success", res.message);
      const pay = res.payment || {};
      const rn = pay.room_no || no;
      receiptMsg[rn] = receiptText(pay, (res.ledger && res.ledger.total_unpaid) || 0);
      selRoomNo = rn;
      await refresh();
      showPayModal(rn);
    };
    $m("pmParse").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        let t = $m("pmSms").value.trim();
        if (!t) { Bass.updateAppAlert("warning", "붙여넣은 입금 문자가 없습니다."); return; }
        if (!/\d{3,4}\s*호/.test(t)) t += ` ${no}호`;
        await after(await Bass.api(APP_ID, "/payments/sms-paste", { method: "POST", body: { branch: currentBranch, sms_text: t } }));
      });
    });
    $m("pmDirect").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        await after(await Bass.api(APP_ID, "/payments/collect", { method: "POST", body: { branch: currentBranch, room_no: no, amount: Number($m("pmAmt").value || 0) } }));
      });
    });
    $m("pmCopy").addEventListener("click", () => copyAndSms($m("pmMsg").value, ct.tenant_phone || (r.tenant && r.tenant.phone)));
    body.querySelectorAll(".pr[data-pay]").forEach((row) => {
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
          showPayModal(no);
        }).catch(() => {});
      }));
    });
  }

  // ② 계약내용: 보기·고치기 + 계약서 사진 + (잔금 확인 전이면) 도어락 교부
  function showContractModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct) return;
    const hasPhoto = Boolean(ct.contract_photo_file);
    const photoUrl = hasPhoto ? `${Bass.apiBase(APP_ID)}/contracts/${encodeURIComponent(ct.id)}/photo?token=${encodeURIComponent(Bass.token())}` : "";
    const f = (id, label, val, type) => `<label class="r3-fld">${label}<input id="${id}" class="bass-input" type="${type || "text"}" value="${Bass.escapeHtml(String(val ?? ""))}"></label>`;
    const body = openModal(`📄 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 계약내용`, `
      <div class="r3-grid">
        ${f("cmIn", "전입일", ct.move_in_date, "date")}
        ${f("cmEx", "만기일", ct.expiry_date, "date")}
        ${f("cmDep", "보증금(원)", ct.deposit, "number")}
        ${f("cmRent", "월세(원)", ct.monthly_rent, "number")}
        ${f("cmFee", "관리비(원)", ct.maintenance_fee, "number")}
        ${f("cmTel", "임차인 전화번호", ct.tenant_phone || (r.tenant && r.tenant.phone) || "", "tel")}
      </div>
      <label class="r3-fld" style="margin-top:8px;">메모<textarea id="cmMemo" class="bass-input" rows="3">${Bass.escapeHtml(ct.lessor_memo || "")}</textarea></label>
      <div class="bass-muted" style="font-size:12.5px;margin:8px 0;">
        계약일 ${Bass.escapeHtml(ct.contract_date || "-")} · ${Bass.escapeHtml(ct.lease_type || "")} · ${Bass.escapeHtml(ct.pay_timing || "")}
        ${ct.fixed_date_no ? ` · 접수번호 <code>${Bass.escapeHtml(ct.fixed_date_no)}</code>` : " · 전월세신고 확인 필요"}
      </div>
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
        <button id="cmSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 저장</button>
        ${hasPhoto ? `<a class="bass-btn bass-btn--sm" href="${photoUrl}" target="_blank" rel="noopener">📸 계약서 사진 보기</a>` : ""}
        <button id="cmPhoto" class="bass-btn bass-btn--sm" type="button">📸 계약서 사진 ${hasPhoto ? "다시 " : ""}등록</button>
        <a class="bass-btn bass-btn--sm" href="/rent3_contract/?role=lessor">🤝 서식 계약</a>
      </div>
      ${ct.keys_released === false ? `<div class="bass-card" style="margin-top:12px;padding:10px;">
        <div style="font-size:13px;margin-bottom:6px;">🔑 잔금 입금 확인 전입니다. 내 은행 앱에서 직접 확인한 뒤 도어락 정보를 열어 주세요.</div>
        <button id="cmKeys" class="bass-btn bass-btn--sm bass-btn--primary" type="button">🔑 잔금 확인 & 도어락 교부</button></div>` : ""}`);
    $m("cmSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/contracts/edit", {
          method: "POST",
          body: {
            branch: currentBranch, contract_id: ct.id,
            deposit: Number($m("cmDep").value || 0), monthly_rent: Number($m("cmRent").value || 0), maintenance_fee: Number($m("cmFee").value || 0),
            move_in_date: $m("cmIn").value, expiry_date: $m("cmEx").value, tenant_phone: $m("cmTel").value, memo: $m("cmMemo").value,
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
    openModal("🏢 건물 정보 수정", `
      <label class="r3-fld">건물 이름<input id="bdName" class="bass-input" value="${Bass.escapeHtml(b.building_name || "")}"></label>
      <label class="r3-fld" style="margin-top:8px;">주소<input id="bdAddr" class="bass-input" value="${Bass.escapeHtml(b.address || "")}"></label>
      <button id="bdSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin-top:10px;">💾 저장</button>`);
    $m("bdSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/building/edit", { method: "POST", body: { branch: currentBranch, building_name: $m("bdName").value, address: $m("bdAddr").value } });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await refresh();
      });
    });
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
    box.innerHTML = `<div class="room-card" style="${need ? "border-color:var(--warn);" : ""}">
      <div class="rc-head">
        <div class="rc-title"><strong>${Bass.escapeHtml(r.room_no)}호</strong> <span>${Bass.escapeHtml(ct.tenant_name || "")}</span></div>
        <span class="rc-unpaid ${n > 0 ? "is-late" : ""}">${n > 0 ? `미납:${n}` : "완납"}</span>
      </div>
      <div class="rc-btns">
        <button class="bass-btn bass-btn--sm" data-act="pay" type="button">수납내역</button>
        <button class="bass-btn bass-btn--sm" data-act="contract" type="button">계약내용</button>
        <button class="bass-btn bass-btn--sm ${need ? "rc-warn" : ""}" data-act="expiry" type="button">만기통보</button>
        <button class="bass-btn bass-btn--sm" data-act="moveout" type="button">전출</button>
      </div>
      <div class="rc-info">
        보증금 ${fmtWon(ct.deposit)} · 월세 ${fmtWon(ct.monthly_rent)} · 관리비 ${fmtWon(ct.maintenance_fee)}<br>
        만기일 ${Bass.escapeHtml(dotDate(ct.expiry_date))}${lg.days_to_expiry != null ? ` (D-${lg.days_to_expiry})` : ""}${bs ? ` · 납부일 매월 ${bs.payment_day}일` : ""}${ec ? ` · ${Bass.escapeHtml(expiryStatusLabel(ec))}` : ""}
      </div></div>`;
    const acts = { pay: showPayModal, contract: showContractModal, expiry: showExpiryModal, moveout: showMoveOutModal };
    box.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => acts[b.dataset.act](r.room_no)));
  }

  function renderLedgerTab() {
    const rooms = dash.rooms || [];
    const noticeRooms = dash.expiry_notice_rooms || [];
    if (!roomOf(selRoomNo)) {
      const late = rooms.find((x) => unpaidCount(x) > 0);
      selRoomNo = (late || rooms[0] || {}).room_no || "";
    }
    const opt = (x) => {
      const n = unpaidCount(x);
      const who = x.is_occupied && x.contract ? ` ${x.contract.tenant_name || ""}` : " 공실";
      return `<option value="${Bass.escapeHtml(x.room_no)}" ${x.room_no === selRoomNo ? "selected" : ""}>${Bass.escapeHtml(x.room_no)}호${Bass.escapeHtml(who)}${n > 0 ? ` · 미납 ${n}` : ""}</option>`;
    };
    elTabContent.innerHTML = `
      <select id="roomPick" class="bass-select" aria-label="호실 선택" style="margin-bottom:10px;font-size:18px;font-weight:700;min-height:48px;">${rooms.map(opt).join("")}</select>
      <div id="roomPanel"></div>

      <details style="margin-bottom:14px;">
        <summary style="cursor:pointer;font-weight:800;padding:12px 4px;">📂 그 밖의 안내 (만기 예고 · 납입일 사전/미납 통보)</summary>
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
      </details>
    `;
    document.getElementById("roomPick").addEventListener("change", (e) => { selRoomNo = e.target.value; renderRoomPanel(); });
    renderRoomPanel();

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
            <input id="brkOcrText" class="bass-input" style="flex:1;" placeholder="예: ${Bass.escapeHtml(selRoom.room_no || "203")}호 보증금 1,000만원 월세 52만원 관리비 5만원 후불 입주일 ${todayIso} 임차인 최중개 010-7788-9900 한강공인중개사사무소 대표 박공인">
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

    document.getElementById("btnSubmitBrokerContract").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const roomNo = document.getElementById("brkRoomNo").value;
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

  function renderTaxTab() {
    const fin = dash.financial_summary || {};
    const cats = dash.expense_categories || {};
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;">
          <div>
            <h3>📊 ${fin.year}년 공용 운영비 실결산 & 순운영이익(NOI)</h3>
            <div class="bass-muted" style="font-size:13px;">총수입 ${fmtWon(fin.gross_revenue)} − 필요경비 ${fmtWon(fin.total_operating_expense)} = <strong>순이익 ${fmtWon(fin.noi)}</strong> (15.4% 분리과세 예상세액 ${fmtWon(fin.estimated_separate_tax)})</div>
          </div>
          <a class="bass-btn bass-btn--primary" href="${Bass.apiBase(APP_ID)}/tax/export-csv?branch=${encodeURIComponent(currentBranch)}&year=${fin.year}&token=${encodeURIComponent(Bass.token())}">📥 5월 종소세 홈택스 CSV 다운로드</a>
        </div>
        <div class="field-grid" style="margin-top:12px;">
          ${(fin.expenses_by_category || []).map((c) => `
            <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">${Bass.escapeHtml(c.label)}</div><div style="font-weight:700;margin-top:4px;">${fmtWon(c.amount)}</div></div>
          `).join("")}
        </div>
      </div>

      <div class="bass-card">
        <h3>🧾 신규 공용 운영비 지출 등록</h3>
        <div class="field-grid" style="margin:10px 0;">
          <div>
            <label class="bass-label">8대 표준 분류</label>
            <select id="outCat" class="bass-select">
              ${Object.entries(cats).map(([k, v]) => `<option value="${k}">${Bass.escapeHtml(v)}</option>`).join("")}
            </select>
          </div>
          <div><label class="bass-label">적요</label><input id="outTitle" class="bass-input" placeholder="예: 복도 센서등 교체"></div>
          <div><label class="bass-label">지출 금액(원)</label><input id="outAmt" type="number" class="bass-input" value="85000"></div>
          <div><label class="bass-label">거래처</label><input id="outVendor" class="bass-input" placeholder="예: 한빛전기"></div>
        </div>
        <button id="btnAddOutlay" class="bass-btn bass-btn--primary" type="button">➕ 공용 운영비 저장</button>
      </div>
    `;

    document.getElementById("btnAddOutlay").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/outlays", {
          method: "POST",
          body: {
            branch: currentBranch,
            category: document.getElementById("outCat").value,
            title: document.getElementById("outTitle").value.trim() || "공용부 유지보수",
            amount: Number(document.getElementById("outAmt").value || 0),
            vendor: document.getElementById("outVendor").value.trim(),
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });
  }

  function renderOpsTab() {
    const tickets = dash.tickets || [];
    const checkouts = dash.checkouts || [];
    const chats = dash.chats || [];
    const occRooms = (dash.rooms || []).filter((r) => r.is_occupied);

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

      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🚪 퇴실 보증금 공제 정산 & 공실 자동 전환</h3>
        <div class="field-grid" style="margin:10px 0;">
          <div>
            <label class="bass-label">퇴실 대상 호실</label>
            <select id="coRoomSel" class="bass-select">
              ${occRooms.map((r) => `<option value="${r.room_no}">${r.room_no}호 (${Bass.escapeHtml(r.tenant ? r.tenant.name : "")})</option>`).join("")}
            </select>
          </div>
          <div><label class="bass-label">당일 공과금 정산액(원)</label><input id="coUtil" type="number" class="bass-input" value="35000"></div>
          <div><label class="bass-label">약정 입주청소비(원)</label><input id="coClean" type="number" class="bass-input" value="70000"></div>
        </div>
        <button id="btnCalcCo" class="bass-btn bass-btn--primary" type="button">📋 미납월세 자동합산 & 퇴실 정산서 발행</button>

        <div style="margin-top:12px;">
          ${checkouts.map((c) => `
            <div class="room-card">
              <div class="bass-row" style="justify-content:space-between;">
                <strong>${c.room_no}호 (${Bass.escapeHtml(c.tenant_name)}) — 실반환액 ${fmtWon(c.refund_amount)}</strong>
                <span class="bass-badge ${c.status === "COMPLETED" ? "bass-badge--ok" : "bass-badge--info"}">${c.status}</span>
              </div>
              <div class="bass-muted" style="font-size:12.5px;margin:4px 0;">원보증금 ${fmtWon(c.deposit_original)} − 미납월세 ${fmtWon(c.unpaid_rent_deduction)} − 공과금 ${fmtWon(c.utility_settlement)} − 청소비 ${fmtWon(c.cleaning_fee)}</div>
              ${c.status !== "COMPLETED" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-complete-co" data-id="${c.id}" type="button">💸 보증금 반환 완료 & 호실 즉시 공실 전환</button>` : ""}
            </div>
          `).join("")}
        </div>
      </div>

      <div class="bass-card">
        <h3>💬 비용 0원 1:1 인앱 직통 메신저</h3>
        <div class="bass-row" style="margin:10px 0;">
          <input id="chatRoomNo" class="bass-input" style="width:100px;" value="203" placeholder="호실">
          <input id="chatMsg" class="bass-input" style="flex:1;" placeholder="세입자/예비임차인에게 보낼 답변 입력">
          <button id="btnSendChat" class="bass-btn bass-btn--primary" type="button">전송</button>
        </div>
        ${chats.slice(0, 8).map((m) => `
          <div style="padding:8px 10px;border-bottom:1px solid var(--border);font-size:13px;">
            <strong>[${Bass.escapeHtml(m.room_no)}호 · ${Bass.escapeHtml(m.sender_name)}]</strong> ${Bass.escapeHtml(m.message)}
          </div>
        `).join("")}
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

    const btnCalc = document.getElementById("btnCalcCo");
    if (btnCalc) {
      btnCalc.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const roomNo = document.getElementById("coRoomSel").value;
          if (!roomNo) {
            Bass.updateAppAlert("warning", "퇴실 정산할 입주 호실이 없습니다.");
            return;
          }
          const res = await Bass.api(APP_ID, "/checkout/calculate", {
            method: "POST",
            body: {
              branch: currentBranch,
              room_no: roomNo,
              utility_settlement: Number(document.getElementById("coUtil").value || 0),
              cleaning_fee: Number(document.getElementById("coClean").value || 0),
            },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    }

    elTabContent.querySelectorAll(".btn-complete-co").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/checkout/${btn.dataset.id}/complete`, { method: "POST" });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    document.getElementById("btnSendChat").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const msg = document.getElementById("chatMsg").value.trim();
        if (!msg) return;
        const res = await Bass.api(APP_ID, "/chats", {
          method: "POST",
          body: { branch: currentBranch, room_no: document.getElementById("chatRoomNo").value.trim(), message: msg },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
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
})();

