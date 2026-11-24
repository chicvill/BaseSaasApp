/* apps/rent3_tenant/frontend/app.js — 임차인 매물 탐색 & 세입자 마이홈 클라이언트 */
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

  let mode = params.get("view") === "listings" || (!params.get("view") && savedSession && savedSession.role === "prospect")
    ? "listings"
    : "myhome";
  // 세입자 본인 로그인이면 본인 계정만 본다. 전체 세입자 목록(/tenants)은 임대인·관리자 전용이라 세입자는 부를 수 없다.
  const isTenantUser = Boolean(savedSession && (savedSession.role === "tenant" || savedSession.role === "prospect"));
  let selectedTenantId = (isTenantUser ? (savedSession.tenant_id || savedSession.id) : "") || params.get("tenant") || (savedSession && savedSession.tenant_id ? savedSession.tenant_id : "");
  let showDoorlock = false;

  const elMyhome = document.getElementById("viewMyhome");
  const elListings = document.getElementById("viewListings");
  // ☰ 메뉴: 첫 화면은 '내 집(납부 확인)' 하나뿐이고, 나머지는 모두 여기서 들어간다(빠진 기능 0개 — test_56 이 점검).
  const TENANT_MENU = [
    { items: [{ label: "🏠 내 집 (납부 확인)", id: "btnSvcHome" }] },
    { title: "계약 · 증빙 서류", items: [
      { label: "🤝 직거래 계약", href: "/rent3_contract/?role=tenant" },
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
  const MY_MENU = TENANT_MENU.map((g) => ({ ...g, items: g.items.filter((i) => (ME_ADMIN ? (!i.userOnly && (!i.adminOnly || adminLinkOk(i))) : !i.adminOnly)) })).filter((g) => g.items.length);
  R3Shell.renderMenu(document.querySelector("[data-r3-drawer]"), MY_MENU);

  const SVC_TITLES = { home: "", notice: "납입 안내 · 미납 통보", expiry: "만기 · 연장 안내", checkout: "퇴실 정산", paper: "중개 계약서 사본", as: "A/S 요청", messages: "임대인과 채팅", cert: "연말정산 납부확인서", door: "도어락 · 차량" };
  let svcView = SVC_TITLES[params.get("svc")] !== undefined ? params.get("svc") : "home";

  function closeDrawer() {
    const dr = document.querySelector("[data-r3-drawer]");
    if (dr) dr.classList.remove("is-open");
    const ov = document.querySelector(".r3-overlay");
    if (ov) ov.classList.remove("is-open");
  }

  // 마이홈 안의 화면 전환: 해당 서비스 구역만 보이고 위에 '← 내 집' 줄이 나온다
  function applyView() {
    elMyhome.querySelectorAll("section.svc").forEach((sec) => { sec.style.display = sec.dataset.svc === svcView ? "" : "none"; });
    const keep = elMyhome.querySelector(".svc-home-only");
    if (keep) keep.style.display = svcView === "home" ? "" : "none";
    const back = document.getElementById("svcBack");
    if (back) {
      back.style.display = svcView === "home" ? "none" : "flex";
      document.getElementById("svcTitle").textContent = SVC_TITLES[svcView] || "";
    }
    clearInterval(tnMsgTimer);
    if (svcView === "messages") loadTenantMessages();
  }

  // ── 임대인과 채팅 (내 호실 1:1 대화 + 접어 둔 계약 협상 기록). 관리인 지원 문의·계약방 대화는 섞지 않는다 ──
  let tnMsgTimer = null;
  let tnMsgCount = -1;
  const tnTime = (iso) => (iso && iso.length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");
  async function loadTenantMessages(keepComposer) {
    const box = document.getElementById("tnMsgList");
    if (!box || !selectedTenantId) return;
    const d = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}/messages`);
    const neg = d.negotiation || { count: 0, items: [] };
    const callEl = document.getElementById("tnMsgCall");
    const lessorDigits = String(d.lessor_phone || "").replace(/\D/g, "");
    if (callEl) callEl.innerHTML = lessorDigits.length >= 9 ? `<a class="bass-btn bass-btn--sm bass-btn--primary" href="tel:${lessorDigits}" style="min-height:40px;">📞 ${Bass.escapeHtml(d.lessor_name || "임대인")}에게 전화 ${Bass.escapeHtml(d.lessor_phone)}</a>` : "";
    const bubble = (m) => m.kind === "system"
      ? `<div class="cm-sys">${Bass.escapeHtml(m.body)} <span>${tnTime(m.created_at)}</span></div>`
      : `<div class="cm-b ${m.sender_role === "tenant" ? "is-mine" : ""}"><div><div class="cm-meta">${m.sender_role === "tenant" ? "나" : Bass.escapeHtml(m.sender_name || "임대인")} · ${tnTime(m.created_at)}</div>${Bass.escapeHtml(m.body)}</div></div>`;
    const nextCount = (d.messages || []).length;
    if (!keepComposer || nextCount !== tnMsgCount) {
      tnMsgCount = nextCount;
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.innerHTML = d.messages.map(bubble).join("") || '<div class="bass-muted" style="font-size:13px;">아직 대화가 없습니다. 궁금한 점을 남겨 보세요.</div>';
      if (atBottom || !keepComposer) box.scrollTop = box.scrollHeight;
    }
    const negBox = document.getElementById("tnMsgNeg");
    if (negBox && !keepComposer) {
      const digestLine = neg.purged ? (neg.purged_note || "보관 기간이 지나 삭제되었습니다.") : neg.digest_ok === true ? "✅ 체결 때 기록한 대화 기록 해시와 일치합니다" : neg.digest_ok === false ? "⚠️ 체결 때 기록한 해시와 달라졌습니다" : "";
      negBox.innerHTML = (neg.count || neg.purged) ? `<details style="margin-top:12px;"><summary class="bass-muted" style="cursor:pointer;font-size:13px;min-height:32px;">계약 협상 기록 ${neg.purged ? `(보관 기간 만료로 삭제됨 · 당시 ${neg.original_count}건)` : `${neg.count}건 보기`}</summary>
        <div class="bass-muted" style="font-size:12px;margin:4px 0;">${Bass.escapeHtml(digestLine)}</div>
        ${neg.items.map((m) => `<div style="font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--border);">[${m.step}단계] ${Bass.escapeHtml(m.sender_role === "lessor" ? "임대인" : m.sender_role === "tenant" ? "나" : "안내")}: ${Bass.escapeHtml(m.body)}</div>`).join("")}</details>` : "";
    }
    clearInterval(tnMsgTimer);
    tnMsgTimer = setInterval(() => {
      if (svcView !== "messages" || !document.getElementById("tnMsgList")) { clearInterval(tnMsgTimer); return; }
      if (document.visibilityState === "hidden") return;
      loadTenantMessages(true).catch(() => {});
    }, 2500);
  }
  function go(view) {
    svcView = SVC_TITLES[view] !== undefined ? view : "home";
    if (mode !== "myhome") setMode("myhome");
    else applyView();
    window.scrollTo(0, 0);
  }
  const MENU_VIEWS = { btnSvcHome: "home", btnSvcCert: "cert", btnSvcPaper: "paper", btnSvcExpiry: "expiry", btnSvcCheckout: "checkout", btnSvcAs: "as", btnSvcDoor: "door", btnSvcMsg: "messages" };
  Object.entries(MENU_VIEWS).forEach(([id, view]) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", () => { closeDrawer(); go(view); });
  });
  const elMenuListings = document.getElementById("btnMenuListings");
  if (elMenuListings) elMenuListings.addEventListener("click", () => { closeDrawer(); setMode("listings"); });
  const btnSupportChat = document.getElementById("btnSupportChat");
  if (btnSupportChat) btnSupportChat.addEventListener("click", () => { closeDrawer(); R3Shell.openSupportChat(); });
  const btnShortcut = document.getElementById("btnShortcut");
  if (btnShortcut) btnShortcut.addEventListener("click", () => { closeDrawer(); R3Shell.openShortcutSettings(); });
  // 🏢 사업자 임차인: 사업자등록증 촬영·선택(AI가 읽어 채움) + 세금계산서 받을 이메일. 번호가 없으면 세금계산서 대신 현금영수증을 요청한다.
  // 💌 집주인에게 안심계약 제안: 문안을 만들어 임차인의 폰으로 직접 보내게 한다(집주인 번호는 서버로 보내지 않는다).
  const btnLessorInvite = document.getElementById("btnLessorInvite");
  if (btnLessorInvite) btnLessorInvite.addEventListener("click", () => {
    const tid = selectedTenantId || (savedSession && (savedSession.tenant_id || savedSession.id));
    if (!tid) { Bass.updateAppAlert("warning", "로그인한 뒤 이용해 주세요."); return; }
    const E = Bass.escapeHtml;
    const body = R3.openModal("💌 집주인에게 안심계약 제안하기", "");
    let cur = null;
    const load = async (label) => {
      cur = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(tid)}/lessor-invite?label=${encodeURIComponent(label || "")}`);
      return cur;
    };
    const draw = () => {
      body.innerHTML = `
        <div style="font-size:13px;line-height:1.7;">
          마음에 드는 집의 집주인에게 <strong>직접 계약(중개수수료 없음)</strong>을 제안하는 문자입니다. 문자는 <strong>내 폰에서 내가 직접</strong> 보내고, 집주인의 번호·이름은 이 서비스로 전송되거나 저장되지 않습니다.
          <label class="r3-fld" style="margin:8px 0;">관심 있는 집 (선택 · 40자)
            <input id="liLabel" class="bass-input" maxlength="40" value="${E(cur.label)}" placeholder="예: 신림동 ○○빌라 201호" style="min-height:44px;">
          </label>
          <label class="r3-fld">보낼 문구 (직접 고칠 수 있어요)
            <textarea id="liMsg" class="bass-input" rows="9" style="font-size:13px;line-height:1.6;">${E(cur.message)}</textarea>
          </label>
          <div class="bass-muted" style="font-size:12.5px;margin-top:6px;">오늘 ${cur.sent_today}/${cur.daily_limit}회 보냄 · 내가 보낸 제안으로 가입한 집주인 <strong>${cur.joined}</strong>명<br>같은 집주인에게 반복해서 보내지 마세요.</div>
          <div class="bass-row" style="gap:8px;margin-top:10px;flex-wrap:wrap;">
            <button id="liSms" class="bass-btn bass-btn--primary" type="button" style="min-height:46px;">📱 문자로 보내기</button>
            <button id="liCopy" class="bass-btn" type="button" style="min-height:46px;">📋 문구 복사 (카톡·당근 채팅에 붙여넣기)</button>
          </div>
        </div>`;
      body.querySelector("#liLabel").addEventListener("change", async function () {
        await load(this.value);
        draw();
      });
      const sent = async () => { const r = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(tid)}/lessor-invite/sent`, { method: "POST", body: {} }); cur.sent_today = r.sent_today; };
      body.querySelector("#liCopy").addEventListener("click", function () {
        Bass.withLock(this, async () => { await sent(); await R3.copyText(body.querySelector("#liMsg").value); Bass.updateAppAlert("success", "복사했습니다. 집주인에게 보낼 곳(문자·카톡·당근 채팅)에 붙여넣어 보내세요."); draw(); });
      });
      body.querySelector("#liSms").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          await sent();
          const text = body.querySelector("#liMsg").value;
          await R3.copyText(text);
          // 받는 사람은 비워 둔다: 문자 앱에서 집주인 번호를 직접 고른다(번호를 이 서비스가 알 필요가 없다)
          const sep = /iPhone|iPad|iPod/i.test(navigator.userAgent) ? "&" : "?";
          if (/Android|iPhone|iPad/i.test(navigator.userAgent)) { const a = document.createElement("a"); a.href = "sms:" + sep + "body=" + encodeURIComponent(text); a.click(); }
          else Bass.updateAppAlert("success", "문구를 복사했습니다. 휴대폰 문자 앱에 붙여넣어 집주인 번호로 보내세요.");
          draw();
        });
      });
    };
    load("").then(draw).catch(Bass.showError);
  });

  const btnBizInfo = document.getElementById("btnBizInfo");
  if (btnBizInfo) btnBizInfo.addEventListener("click", async () => {
    closeDrawer();
    const E = Bass.escapeHtml;
    const cur = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}/business-registration`);
    const b = cur.business || {};
    const body = R3.openModal("🏢 사업자 정보 · 세금계산서 요청", `
      <div style="font-size:13px;line-height:1.7;margin-bottom:8px;">사업자등록증을 올리면 번호·상호·대표자를 읽어 채웁니다(AI 읽기는 아래 동의 시에만). <strong>사업자등록번호가 없으면</strong> 세금계산서 대신 <strong>현금영수증</strong>을 임대인에게 요청하세요(☰ 임대인과 채팅).</div>
      ${cur.submitted_at ? `<div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">마지막 제출 ${E(String(cur.submitted_at).slice(0, 16).replace("T", " "))}</div>` : ""}
      <div class="bass-row" style="gap:8px;flex-wrap:wrap;margin:6px 0;"><button type="button" id="tbCam" class="bass-btn bass-btn--primary" style="min-height:48px;">📷 촬영하기</button><button type="button" id="tbPic" class="bass-btn" style="min-height:48px;">🖼 사진·PDF 선택</button></div>
      <input id="tbFileCam" type="file" accept="image/*" capture="environment" hidden><input id="tbFile" type="file" accept="image/*,application/pdf" hidden>
      <div id="tbPrev" class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">선택한 파일 없음</div>
      <label class="check-row" style="margin:4px 0;"><span style="font-size:12px;">사업자등록증을 AI로 읽어 채우는 것에 동의합니다.</span><input type="checkbox" id="tbConsent"></label>
      <div class="r3-grid" style="margin-top:6px;">
        <label class="r3-fld">사업자등록번호<input id="tbNo" class="bass-input" placeholder="123-45-67890" value="${E(b.business_number || "")}"></label>
        <label class="r3-fld">상호<input id="tbComp" class="bass-input" value="${E(b.company_name || "")}"></label>
        <label class="r3-fld">대표자<input id="tbCeo" class="bass-input" value="${E(b.ceo_name || "")}"></label>
        <label class="r3-fld">세금계산서 받을 이메일<input id="tbEmail" class="bass-input" type="email" value="${E(b.email || "")}"></label>
      </div>
      <label class="r3-fld" style="margin-top:6px;">사업장 주소<input id="tbAddr" class="bass-input" value="${E(b.business_address || "")}"></label>
      <button id="tbSubmit" class="bass-btn bass-btn--primary" type="button" style="min-height:48px;margin-top:10px;width:100%;">제출</button>`);
    let picked = null;
    const take = (inp) => { const f = inp.files && inp.files[0]; if (!f) return; picked = f; body.querySelector("#tbPrev").textContent = "📎 " + (f.name || "촬영한 사진"); };
    body.querySelector("#tbCam").addEventListener("click", () => body.querySelector("#tbFileCam").click());
    body.querySelector("#tbPic").addEventListener("click", () => body.querySelector("#tbFile").click());
    body.querySelector("#tbFileCam").addEventListener("change", (e) => take(e.target));
    body.querySelector("#tbFile").addEventListener("change", (e) => take(e.target));
    const toUrl = async (file) => {
      const raw = () => new Promise((res, rej) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.onerror = rej; rd.readAsDataURL(file); });
      if (!/^image\/(jpeg|png|webp)$/.test(file.type) || !window.createImageBitmap) return raw();
      try {
        const bmp = await createImageBitmap(file); const k = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
        const cv = document.createElement("canvas"); cv.width = Math.max(1, Math.round(bmp.width * k)); cv.height = Math.max(1, Math.round(bmp.height * k));
        cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height); return cv.toDataURL("image/jpeg", 0.85);
      } catch (_) { return raw(); }
    };
    body.querySelector("#tbSubmit").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const g = (id) => body.querySelector("#" + id).value.trim();
        const res = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}/business-registration`, { method: "POST", body: {
          attachment_data_url: picked ? await toUrl(picked) : "", consent: body.querySelector("#tbConsent").checked,
          business_number: g("tbNo"), company_name: g("tbComp"), ceo_name: g("tbCeo"), email: g("tbEmail"), business_address: g("tbAddr") } });
        Bass.updateAppAlert("success", res.message);
        const bb = res.business || {};
        body.querySelector("#tbNo").value = bb.business_number || ""; body.querySelector("#tbComp").value = bb.company_name || ""; body.querySelector("#tbCeo").value = bb.ceo_name || "";
        body.querySelector("#tbAddr").value = bb.business_address || ""; body.querySelector("#tbEmail").value = bb.email || "";
      });
    });
  });
  const btnSupportSetting = document.getElementById("btnSupportSetting");
  if (btnSupportSetting) btnSupportSetting.addEventListener("click", () => { closeDrawer(); R3Shell.openSupportSettings(); });
  R3Shell.watchSupport();
  // 🔔 알림 센터: 정산서·만기 회신·채팅·미납 등 내가 확인할 일을 한곳에 모아 건수로 알린다
  if (window.R3Notify && selectedTenantId && !ME_ADMIN) R3Notify.start({ url: `/api/rent3_tenant/myhome/${encodeURIComponent(selectedTenantId)}/notifications`, seenUrl: `/api/rent3_tenant/myhome/${encodeURIComponent(selectedTenantId)}/notifications/seen`, openSupport: () => R3Shell.openSupportChat() });
  if (params.get("welcome")) {    // 입주 안내 링크로 가입을 마친 직후: 홈 화면 추가 방법을 한 번 보여 주고 주소에서 welcome 을 뺀다
    try { const u = new URL(window.location.href); u.searchParams.delete("welcome"); window.history.replaceState(null, "", u.toString()); } catch (_) { /* 무시 */ }
    setTimeout(() => R3Shell.installHelp(), 600);
  }

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  function setMode(m) {
    mode = m;
    document.getElementById("hdrTitle").textContent = m === "listings" ? "🔍 빈 집 찾기" : "🔑 내 집 · 월세 납부 확인";
    elMyhome.style.display = m === "myhome" ? "block" : "none";
    elListings.style.display = m === "listings" ? "block" : "none";
    if (m === "myhome") loadMyhome().catch(Bass.showError);
    else loadListings().catch(Bass.showError);
  }

  async function loadMyhome() {
    if (!selectedTenantId) {
      elMyhome.innerHTML = R3Shell.isAdminSession()
        ? `<div class="bass-card">관리인 계정입니다. 관리인 페이지의 <strong>원격 지원</strong>에서 호실을 골라 들어오세요. <a class="bass-btn bass-btn--sm" href="/rent3_admin/">← 관리인 페이지로</a></div>`
        : `<div class="bass-card">현재 활성 입주 세입자가 없습니다. '빈 집 찾기'에서 계약을 진행해 보세요.</div>`;
      return;
    }
    if (params.get("view") === "moved") {       // 퇴실 후 조회 전용(다음 해 2월 말까지): 연말정산 납부확인서·퇴실 정산서만 본다
      const ar = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}/archive`);
      const E = Bass.escapeHtml;
      const cert = ar.certificate || {};
      const rows = Object.entries(cert).filter(([, v]) => ["string", "number"].includes(typeof v)).map(([k, v]) => `<tr><th>${E(k)}</th><td>${E(String(v))}</td></tr>`).join("");
      elMyhome.innerHTML = `<div class="bass-card"><h3>🧾 퇴실 후 조회 전용</h3>
        <p class="bass-muted" style="font-size:13px;">${E(ar.name)}님은 퇴실 처리되었습니다. <strong>${E(ar.readonly_until)}</strong>까지 로그인해서 연말정산 납부확인서와 정산서를 조회·내려받을 수 있고, 그 밖의 기능은 쓸 수 없습니다.</p></div>
        <div class="bass-card cert-paper"><h3>연말정산 납부확인서</h3>${rows ? `<table>${rows}</table>` : `<p class="bass-muted">표시할 납부확인서가 없습니다.</p>`}
          <button class="bass-btn no-print" type="button" onclick="window.print()">🖨️ 인쇄 / PDF 저장</button></div>
        <div class="bass-card"><h3>퇴실 정산서</h3>${(ar.settlements || []).map((x) => `<p style="font-size:13.5px;line-height:1.7;">${E(x.room_no)}호 · 보증금 ${fmtWon(x.deposit_original)} → 반환 ${fmtWon(x.refund_amount)} · 상태 ${E(x.status)} · 계좌 ${E(x.account_masked || "")} · 완료 ${E((x.completed_at || "").slice(0, 10))}</p>`).join("") || `<p class="bass-muted">정산서가 없습니다.</p>`}</div>`;
      return;
    }
    const data = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}`);
    document.getElementById("poolBadge").textContent = `JSON Pool v${data.pool_revision}`;

    const t = data.tenant;
    const b = data.building || {};
    const r = data.room || {};
    const ct = data.contract || {};
    const lg = data.ledger || { months: [], total_unpaid: 0 };
    const cert = data.certificate;
    const co = data.pending_checkout;
    const ew = data.expiry_window;
    const ec = data.expiry_check;
    const bs = data.billing_schedule;
    const bns = data.billing_notices || [];
    const activeBns = bns.filter((x) => x.status !== "RESOLVED_PAID");
    const showExpiryCard = Boolean((ew && ew.in_6_to_2_months_window) || ec);

    // 잔금 전에는 도어락·공동현관이 잠긴다 — 잔금일 등기부 확인 버튼이 있어 접힌 메뉴가 아니라 맨 위에 보여 준다
    const keysLockHtml = (data.keys && data.keys.locked) ? `<div class="bass-card no-print" style="margin-bottom:14px;border:2px solid var(--warn);"><div style="margin:6px 0;padding:8px 10px;border-left:4px solid var(--warn);font-size:12.5px;line-height:1.6;">🔒 <strong>도어락·공동현관은 입주 당일 잔금 완납 후 공개됩니다.</strong><br>잔금 ${fmtWon(data.keys.balance_due)} · 입주일 ${Bass.escapeHtml(data.keys.move_in_date || "-")}<br>임대인이 잔금 입금을 확인하면 이 화면에 바로 열립니다.
              <div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);">
                <strong>잔금을 보내기 전에 등기부를 직접 열람하세요.</strong> 계약 이후 근저당·압류가 새로 생겼는지 확인하는 마지막 기회입니다.<br>
                ${data.keys.registry_check && data.keys.registry_check.checked_at
                  ? (data.keys.registry_check.changed ? "⚠️ 변동을 신고했습니다. 해소되기 전에는 잔금을 보내지 마세요." : "✅ 잔금일 등기부 확인 완료 (" + Bass.escapeHtml(String(data.keys.registry_check.checked_at).slice(0, 16).replace("T", " ")) + ")")
                  : "아직 확인하지 않았습니다."}
                ${data.keys.insurance_proof_required ? `<div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);"><strong>보증보험 가입 증빙</strong> (가입 예정으로 계약하셨습니다)<br>${data.keys.insurance_proof && data.keys.insurance_proof.policy_no ? "✅ 제출 완료: " + Bass.escapeHtml(data.keys.insurance_proof.issuer) + " " + Bass.escapeHtml(data.keys.insurance_proof.policy_no) : "아직 제출하지 않았습니다. 가입이 거절되면 계약은 무효이고 계약금은 전액 반환됩니다."}<div class="bass-row" style="margin-top:6px;gap:6px;"><button class="bass-btn bass-btn--sm bass-btn--primary btn-ins-proof" type="button">증빙 제출</button></div></div>` : ""}
                <div style="margin-top:6px;font-size:12.5px;line-height:1.7;">계약 뒤에 대출(근저당)이 새로 생긴 사례가 있어, <strong>잔금을 보내기 직전에</strong> 인터넷등기소에서 다시 열람해 확인해야 합니다. 확인은 <strong>계약 화면</strong>에서 합니다(등기부 올리기 → AI 비교 → '확인했음' 체크).</div>
                <div class="bass-row" style="margin-top:6px;gap:6px;flex-wrap:wrap;">
                  <a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?role=tenant" style="min-height:44px;display:inline-flex;align-items:center;">📑 계약 화면에서 등기부 재확인 ↗</a>
                  <a class="bass-btn bass-btn--sm" href="https://www.iros.go.kr" target="_blank" rel="noopener" style="min-height:44px;display:inline-flex;align-items:center;">🏛 인터넷등기소 ↗</a>
                </div>
              </div></div></div>` : "";

    const blk = (html, empty) => (String(html).trim() ? html : `<div class="bass-card no-print"><div class="bass-muted" style="font-size:13.5px;">${empty}</div></div>`);
    // 납부 현황: 미납이 남은 달과 최근 12개월은 펼치고, 오래된 완납 달은 접어 둔다
    const monthRow = (m) => `
      <div style="display:flex;justify-content:space-between;padding:7px 10px;border-bottom:1px solid var(--border);font-size:13px;">
        <span><strong>${m.ym}</strong> · 청구 ${fmtWon(m.due)}</span>
        <span style="color:${m.unpaid > 0 ? (m.phase === "DUE_SOON" ? "var(--warn)" : "var(--err)") : "var(--ok)"};font-weight:700;">${m.phase === "DUE_SOON" ? "🕒 " : ""}${Bass.escapeHtml(m.note)}</span>
      </div>`;
    const allMonths = lg.months || [];
    const recentMonths = allMonths.slice(-12);
    const olderMonths = allMonths.slice(0, Math.max(allMonths.length - 12, 0));
    const olderUnpaid = olderMonths.filter((m) => m.unpaid > 0);
    const olderOk = olderMonths.filter((m) => !(m.unpaid > 0));
    const monthsHtml = olderUnpaid.map(monthRow).join("") + recentMonths.map(monthRow).join("") +
      (olderOk.length ? `<details style="margin-top:4px;"><summary class="bass-muted no-print" style="cursor:pointer;font-size:12.5px;min-height:32px;padding:6px 4px;">이전 완납 ${olderOk.length}개월 보기</summary>${olderOk.map(monthRow).join("")}</details>` : "");

    // 할 일 배너: 미납 > 퇴실 정산 > 만기 확인 (해당할 때만, 최대 2줄)
    const todos = [];
    const overdueAmt = lg.overdue_unpaid != null ? Number(lg.overdue_unpaid) : Number(lg.total_unpaid || 0);
    const dueSoonAmt = Math.max(Number(lg.total_unpaid || 0) - overdueAmt, 0);
    if (overdueAmt > 0) todos.push({ icon: "🚨", text: `미납 ${fmtWon(overdueAmt)}이 있습니다`, label: "납부하기", go: "pay", hot: true });
    else if (dueSoonAmt > 0) todos.push({ icon: "🕒", text: `납부 예정 ${fmtWon(dueSoonAmt)} — 납입일까지 입금해 주세요`, label: "납부하기", go: "pay", hot: false });
    if (co && ["CALCULATED", "PAID"].includes(co.status)) todos.push({ icon: "🚪", text: co.status === "PAID" ? "보증금이 송금되었습니다 — 받으셨나요?" : "퇴실 정산서가 도착했습니다", label: co.status === "PAID" ? "수취 확인" : "확인·동의", go: "checkout", hot: true });
    if (ec && ec.status === "AWAITING_TENANT") todos.push({ icon: "📅", text: "만기 후 전출 계획을 알려 주세요", label: "답변하기", go: "expiry", hot: true });
    else if (ec && ec.status === "RENEWAL_PREVIEWED") todos.push({ icon: "📝", text: "연장 재계약 예고가 도착했습니다", label: "확인하기", go: "expiry", hot: true });
    else if (ew && ew.in_6_to_2_months_window && !ec) todos.push({ icon: "📅", text: "만기가 다가옵니다. 전출 계획을 알릴 수 있습니다", label: "열기", go: "expiry", hot: false });
    if ((data.messages_unread || 0) > 0) todos.push({ icon: "✉️", text: `임대인 채팅 ${data.messages_unread}건이 있습니다`, label: "보기", go: "messages", hot: false });
    if (activeBns.some((x) => x.status !== "ACKNOWLEDGED")) todos.push({ icon: "🔔", text: "납입 안내가 도착했습니다", label: "보기", go: "notice", hot: false });
    const todoHtml = todos.length ? `<div class="bass-card no-print" style="margin-bottom:14px;padding:10px 12px;">${todos.slice(0, 2).map((x) => `
      <div class="bass-row" style="justify-content:space-between;align-items:center;gap:8px;padding:4px 0;">
        <span style="font-size:14px;${x.hot ? "font-weight:700;" : ""}">${x.icon} ${Bass.escapeHtml(x.text)}</span>
        <button class="bass-btn bass-btn--sm ${x.hot ? "bass-btn--primary" : ""} btn-todo" data-go="${x.go}" type="button">${x.label}</button>
      </div>`).join("")}</div>` : "";
    const contractLine = `<div class="bass-card no-print" style="margin-bottom:14px;font-size:13.5px;line-height:1.7;">
      <strong>내 계약</strong><br>보증금 ${fmtWon(ct.deposit)} · 월세 ${fmtWon(ct.monthly_rent)} · 관리비 ${fmtWon(ct.maintenance_fee)}<br>
      입주 ${Bass.escapeHtml(ct.move_in_date || "-")} · 만기 ${Bass.escapeHtml(ct.expiry_date || "-")}${lg.days_to_expiry != null ? ` (D-${lg.days_to_expiry})` : ""}</div>`;

    R3Shell.proxyBanner(`${b.building_name || ""} ${r.room_no || ""}호 ${t.name} (임차인 입장)`);   // 관리인 대리 접속 때만 보인다
    elMyhome.innerHTML = `
      <div class="svc-home-only">${keysLockHtml}</div>
      <div id="svcBack" class="bass-row no-print" style="gap:8px;margin:0 0 12px;display:none;"><button id="btnSvcBack" class="bass-btn bass-btn--sm" type="button">← 내 집</button><strong id="svcTitle" style="font-size:15px;"></strong></div>
      <section class="svc" data-svc="home">
      <div class="bass-card no-print" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
          <div><strong>${Bass.escapeHtml(t.name)}</strong> 님의 마이홈 · ${Bass.escapeHtml(b.building_name || "")} ${Bass.escapeHtml(r.room_no || "")}호</div>
          <div class="bass-row" style="flex-wrap:wrap;gap:6px;">
            <span class="bass-badge ${(lg.overdue_unpaid ?? lg.total_unpaid) > 0 ? "bass-badge--err" : (lg.total_unpaid > 0 ? "bass-badge--warn" : "bass-badge--ok")}">${(lg.overdue_unpaid ?? lg.total_unpaid) > 0 ? `미납 ${fmtWon(lg.overdue_unpaid ?? lg.total_unpaid)}` : (lg.total_unpaid > 0 ? `🕒 납부 예정 ${fmtWon(lg.total_unpaid)}` : "미납 0원 (완납)")}</span>
            ${bs ? `<span class="bass-badge ${bs.is_pre_due_3d ? "bass-badge--warn" : "bass-badge--info"}">매월 ${bs.payment_day}일 납입 · 차기 ${Bass.escapeHtml(bs.upcoming_due_date)} (D-${bs.days_until_due})</span>` : ""}
            <span class="bass-badge ${ew && ew.in_6_to_2_months_window ? "bass-badge--warn" : "bass-badge--info"}">만기 D-${lg.days_to_expiry ?? "-"}${ew && ew.in_6_to_2_months_window ? " (6~2개월 통지구간)" : ""}</span>
            ${(data.messages_unread || 0) > 0 ? `<span class="bass-badge bass-badge--info">✉️ 새 채팅 ${data.messages_unread}</span>` : ""}
          </div>
        </div>

      </div>

      ${todoHtml}
      <div class="bass-card no-print" style="margin-bottom:14px;">
        <h3 style="margin:0 0 8px;">💳 월세 납부 · 이체 알리기</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.6;margin-bottom:8px;">임대인 계좌로 이체한 뒤 아래 [이체했어요]를 누르세요. <strong>임대인이 입금을 확인하면 납부로 기록됩니다.</strong></div>
        ${(() => { const n = data.transfer_notice; if (!n) return ""; return n.status === "PENDING" ? `<div class="bass-badge bass-badge--warn" style="margin-bottom:8px;">⏳ ${fmtWon(n.amount)} 이체 확인 대기 중</div>` : n.status === "REJECTED" ? `<div class="bass-badge bass-badge--err" style="margin-bottom:8px;">입금 확인이 안 되어 반려되었습니다${n.reject_reason ? ` — ${Bass.escapeHtml(n.reject_reason)}` : ""}. 임대인에게 문의해 주세요.</div>` : ""; })()}
        <div class="bass-row">
          <input id="payAmtInput" type="number" class="bass-input" style="width:170px;" value="${lg.total_unpaid > 0 ? lg.total_unpaid : (Number(ct.monthly_rent || 0) + Number(ct.maintenance_fee || 0))}">
          <button id="btnPayNow" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">💸 이체했어요</button>
        </div>
        <div class="bass-muted" style="font-size:12.5px;margin:12px 0 2px;">월별 납부 현황</div>
        <div style="margin:10px 0;">
          ${monthsHtml}
        </div>
      </div>

      ${contractLine}
      </section>
      <section class="svc" data-svc="notice">
      ${blk(`      ${activeBns.map((bn) => `
        <div class="bass-card no-print" style="margin-bottom:14px;border-left:4px solid ${bn.notice_type === "OVERDUE_1D" ? "var(--err)" : "var(--primary-2)"};">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
            <div>
              <h3 style="margin:0;">${bn.notice_type === "OVERDUE_1D" ? "🚨 임대료 미납 통보 도착 (납입기한 경과)" : "🔔 임대료 납입일 사전 안내 (납입일 도래)"}</h3>
              <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                약정 납입일: <strong>매월 ${bn.payment_day}일</strong>(=입주일) · 대상 귀속월: <strong>${Bass.escapeHtml(bn.billing_ym)}</strong> (기한 ${Bass.escapeHtml(bn.due_date)})
                · 입금계좌: <code>${Bass.escapeHtml(bn.lessor_account)}</code>
              </div>
            </div>
            <span class="bass-badge ${bn.status === "ACKNOWLEDGED" ? "bass-badge--ok" : (bn.notice_type === "OVERDUE_1D" ? "bass-badge--err" : "bass-badge--info")}">
              ${bn.status === "ACKNOWLEDGED" ? "확인 완료" : (bn.notice_type === "OVERDUE_1D" ? `미납 ${fmtWon(bn.amount_due)}` : `예정액 ${fmtWon(bn.amount_due)}`)}
            </span>
          </div>
          <div style="font-size:13px;margin-top:8px;line-height:1.5;">${Bass.escapeHtml(bn.message)}</div>
          <div class="bass-row" style="margin-top:10px;gap:8px;flex-wrap:wrap;">
            <button class="bass-btn bass-btn--sm bass-btn--primary btn-jump-pay" data-amt="${bn.amount_due}" type="button">💳 ${fmtWon(bn.amount_due)} 즉시 납부하기</button>
            ${bn.status !== "ACKNOWLEDGED" ? `<button class="bass-btn bass-btn--sm btn-ack-bn" data-id="${bn.id}" type="button">✅ 통보 내용 확인 완료</button>` : ""}
          </div>
        </div>
      `).join("")}

`, "도착한 납입 안내·미납 통보가 없습니다.")}
      </section>
      <section class="svc" data-svc="expiry">
      ${blk(`      ${showExpiryCard ? `
        <div class="bass-card no-print" style="margin-bottom:14px;border-left:4px solid var(--warn);">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
            <div>
              <h3 style="margin:0;">📅 만기 6~2개월 전 전출계획 확인 & 연장 재계약 예고</h3>
              <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                계약 만기일: <strong>${Bass.escapeHtml(ct.expiry_date || "-")}</strong> (D-${ew ? ew.days_to_expiry : "-"})
                ${ew ? ` · 법정 통지마감(만기 2개월 전): <strong>${Bass.escapeHtml(ew.notice_deadline_date)}</strong>` : ""}
              </div>
            </div>
            <span class="bass-badge ${ec && (ec.status === "RENEWAL_ACKNOWLEDGED" || ec.status === "STAY_CONFIRMED") ? "bass-badge--ok" : "bass-badge--warn"}">
              ${ec ? (
                ec.status === "AWAITING_TENANT" ? "📨 임대인 전출계획 확인 요청 도착" :
                ec.status === "MOVEOUT_PLANNED" ? `📦 만기전출 예정 회신됨 (${Bass.escapeHtml(ec.planned_move_out_date)})` :
                ec.status === "STAY_CONFIRMED" ? "🏠 전출의사 없음 회신 완료" :
                ec.status === "RENEWAL_PREVIEWED" ? "📝 임대인 연장 재계약 예고 도착" :
                ec.status === "RENEWAL_ACKNOWLEDGED" ? "✅ 연장 재계약 예고 조건 동의 완료" : Bass.escapeHtml(ec.status)
              ) : "⚖️ 만기 6~2개월 전 사전확인 구간"}
            </span>
          </div>

          <div class="field-grid" style="margin-top:10px;">
            <div>
              <label class="bass-label">전출(퇴실) 희망 시 예정일 (거주 연장 시 자동 무시)</label>
              <input id="expMoveOutDate" type="date" class="bass-input" value="${Bass.escapeHtml((ec && ec.planned_move_out_date) || ct.expiry_date || "")}">
            </div>
            <div>
              <label class="bass-label">임대인 전달 메모 (선택)</label>
              <input id="expTenantMemo" class="bass-input" value="${Bass.escapeHtml((ec && ec.tenant_memo) || "")}" placeholder="예: 전출 의사 없으며 동일 조건으로 1년 더 거주 희망합니다.">
            </div>
          </div>
          <div class="bass-row" style="margin-top:8px;flex-wrap:wrap;gap:8px;">
            <button id="btnPlanStay" class="bass-btn bass-btn--primary" type="button">🏠 전출의사 없음 (거주 연장 희망 회신)</button>
            <button id="btnPlanMoveOut" class="bass-btn" type="button">📦 만기 전출(퇴실) 예정 회신</button>
          </div>

          ${ec && (ec.status === "RENEWAL_PREVIEWED" || ec.status === "RENEWAL_ACKNOWLEDGED") ? `
            <div class="listing-card" style="margin:12px 0 0;border-color:var(--primary-2);">
              <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
                <strong style="font-size:15px;">📝 임대인 연장 재계약 예고장 (${ec.renewal_type === "SAME_TERMS" ? "종전 동일조건 연장" : "법정 5% 상한 이내 조정"})</strong>
                <span class="bass-badge ${ec.status === "RENEWAL_ACKNOWLEDGED" ? "bass-badge--ok" : "bass-badge--info"}">${ec.status === "RENEWAL_ACKNOWLEDGED" ? "확인·동의 완료" : "임차인 확인 대기"}</span>
              </div>
              <div style="font-size:13.5px;margin-top:8px;line-height:1.6;">
                • 보증금: ${fmtWon(ec.current_deposit)} ➔ <strong>${fmtWon(ec.proposed_deposit)}</strong> (${ec.deposit_increase_pct >= 0 ? "+" : ""}${ec.deposit_increase_pct}%)<br>
                • 월세 / 관리비: ${fmtWon(ec.current_monthly_rent)} ➔ <strong>${fmtWon(ec.proposed_monthly_rent)}</strong> (${ec.rent_increase_pct >= 0 ? "+" : ""}${ec.rent_increase_pct}%) / 관리비 <strong>${fmtWon(ec.proposed_maintenance_fee)}</strong><br>
                • 예정 연장기간: <strong>${ec.proposed_term_months}개월</strong>
                ${ec.renewal_note ? `<br>• 임대인 안내: <span class="bass-muted">${Bass.escapeHtml(ec.renewal_note)}</span>` : ""}
              </div>
              <div class="bass-row" style="margin-top:10px;flex-wrap:wrap;gap:8px;">
                ${ec.status !== "RENEWAL_ACKNOWLEDGED" ? `
                  <button id="btnAckRenewal" class="bass-btn bass-btn--primary" type="button">✅ 연장 재계약 예고 조건 확인·동의</button>
                ` : ""}
                ${ec.cosign_session_id ? `
                  <a class="bass-btn bass-btn--sm ${ec.status === "RENEWAL_ACKNOWLEDGED" ? "bass-btn--primary" : ""}" href="/rent3_contract/?session=${encodeURIComponent(ec.cosign_session_id)}&role=tenant">🤝 공동 재계약방 즉시 입장 (참여코드 ${Bass.escapeHtml(ec.cosign_session_code)})</a>
                ` : ""}
              </div>
            </div>
          ` : ""}
        </div>
      ` : ""}

`, "만기 6~2개월 전부터 이곳에서 전출 계획을 알리고 연장 재계약 예고를 확인할 수 있습니다.")}
      </section>
      <section class="svc" data-svc="checkout">
      ${blk(`      ${co ? `
        <div class="bass-card no-print" style="margin-bottom:14px;border-color:var(--warn);">
          ${co.status === "REQUESTED" ? `<h3>🚪 퇴실 신청이 접수되었습니다</h3><p class="bass-muted" style="font-size:13px;">임대인이 점검 후 정산서를 보냅니다. 📄 <a href="?tenant=${t.id}&svc=cert">연말정산 납부확인서</a>는 퇴실 전에 미리 내려받아 두세요.</p>` : `
          <h3>🚪 퇴실 보증금 정산 명세서 (실반환 예정액: ${fmtWon(co.refund_amount)})</h3>
          <p class="bass-muted" style="font-size:13px;">원보증금 ${fmtWon(co.deposit_original)} − 미납월세 ${fmtWon(co.unpaid_rent_deduction)} − 공과금 ${fmtWon(co.utility_settlement)} − 청소비 ${fmtWon(co.cleaning_fee)} − 파손 ${fmtWon(co.damage_restoration_fee)}</p>
          <p style="font-size:13px;">📄 퇴실하면 로그인 수단이 정리됩니다. <a href="?tenant=${t.id}&svc=cert">연말정산 납부확인서</a>를 미리 내려받아 두세요(퇴실 후에도 다음 해 2월 말까지 조회는 가능).</p>`}
          ${co.status === "CALCULATED" ? `
            <div class="field-grid" style="margin-top:8px;">
              <input id="coBank" class="bass-input" value="${Bass.escapeHtml(t.account_number || "")}" placeholder="반환받을 은행 계좌번호">
              <input id="coHolder" class="bass-input" value="" placeholder="예금주 (계약자 본인 ${Bass.escapeHtml(t.name)})">
              <input id="coSig" class="bass-input" value="${Bass.escapeHtml(t.name)} (동의서명)" placeholder="동의 전자서명">
              <button id="btnAgreeCo" data-id="${co.id}" class="bass-btn bass-btn--primary" type="button">✍️ 정산 내역 확인 및 동의 서명</button>
              <button id="btnDisputeCo" data-id="${co.id}" class="bass-btn" type="button">⚠️ 이의 제기 (채팅·통화로 협의)</button>
            </div>
          ` : co.status === "AGREED" ? `<span class="bass-badge bass-badge--ok">동의 완료 — 임대인 송금 대기 중</span> <button id="btnDisputeCo" data-id="${co.id}" class="bass-btn bass-btn--sm" type="button">⚠️ 이의 제기</button>`
          : co.status === "PAID" ? `<div style="font-size:14px;line-height:1.7;">💸 임대인이 ${fmtWon(co.refund_amount)}을 송금했다고 표시했습니다. 입금을 확인해 주세요. (표시 후 7일 안에 이의가 없으면 자동 확정)</div>
            <div class="bass-row" style="gap:8px;margin-top:8px;"><button id="btnReceivedCo" data-id="${co.id}" class="bass-btn bass-btn--primary" type="button" style="min-height:48px;">✅ 받았습니다</button><button id="btnDisputeCo" data-id="${co.id}" class="bass-btn" type="button" style="min-height:48px;">❌ 못 받았습니다</button></div>`
          : co.status === "DISPUTED" ? `<span class="bass-badge bass-badge--warn">이의 접수 — 임대인과 채팅·통화로 협의 중</span><div class="bass-muted" style="font-size:13px;">${Bass.escapeHtml(co.dispute_note || "")}</div>` : ""}
        </div>
      ` : `
        <div class="bass-card no-print" style="margin-bottom:14px;">
          <h3>🚪 퇴실 신청</h3>
          <p class="bass-muted" style="font-size:13px;">퇴실을 신청하면 임대인이 점검 후 정산서를 보냅니다. 퇴실하면 로그인 수단이 정리되니 <strong>연말정산 납부확인서를 먼저 내려받아 두세요.</strong></p>
          <div class="field-grid"><input id="coWant" class="bass-input" type="date" aria-label="퇴실 희망일"><input id="coMemo" class="bass-input" placeholder="메모(선택)"><button id="btnRequestCo" class="bass-btn bass-btn--primary" type="button">🚪 퇴실 신청</button></div>
        </div>
      `}

`, "받은 퇴실 정산 명세서가 없습니다.")}
      </section>
      <section class="svc" data-svc="paper">
      ${blk(`      ${ct && ct.contract_photo_file ? `
        <div class="bass-card no-print" style="margin-bottom:14px;">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <h3 style="margin:0;">📸 공인중개사 입회 지면계약서 원본 사본</h3>
              <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                중개업소: <strong>${Bass.escapeHtml((ct.broker_info && ct.broker_info.agency_name) || "공인중개사사무소")}</strong>
                ${ct.broker_info && ct.broker_info.agent_name ? ` (대표 ${Bass.escapeHtml(ct.broker_info.agent_name)})` : ""}
                · 특약: ${Bass.escapeHtml(ct.special_terms || "-")}
              </div>
            </div>
            <button id="btnOpenContractPhoto" data-cid="${Bass.escapeHtml(ct.id)}" class="bass-btn bass-btn--sm bass-btn--primary" type="button">🔍 계약서 원본 열기/다운로드</button>
          </div>
          ${String(ct.contract_photo_file).toLowerCase().endsWith(".pdf")
            ? `<div class="bass-muted" style="margin-top:8px;font-size:13px;">PDF 스캔본 계약서가 보관되어 있습니다. 우측 상단 버튼으로 확인하세요.</div>`
            : `<img id="imgContractPhotoPreview" alt="내 호실 임대차계약서 사본" style="max-width:100%;max-height:320px;border-radius:12px;border:1px solid var(--border);margin-top:10px;background:#fff;display:none;">`
          }
        </div>
      ` : ""}

`, "보관된 중개 계약서 사본이 없습니다. (직거래 계약은 계약 화면에서 확인합니다)")}
      </section>
      <section class="svc" data-svc="as">
      <div class="bass-card no-print" style="margin-bottom:14px;">
        <h3>🔧 1:1 시설 수리(A/S) 요청 접수</h3>
        <div class="field-grid" style="margin:10px 0;">
          <select id="asCat" class="bass-select">
            <option value="설비/배관">설비/배관</option>
            <option value="에어컨/냉난방">에어컨/냉난방</option>
            <option value="전기/조명">전기/조명</option>
            <option value="도어락">디지털 도어락</option>
          </select>
          <input id="asTitle" class="bass-input" placeholder="수리 요청 제목 (예: 욕실 샤워기 호스 누수)">
        </div>
        <button id="btnSubmitAs" class="bass-btn" type="button">📩 임대인에게 수리 요청 보내기</button>
        <div style="margin-top:10px;">
          ${(data.tickets || []).map((tk) => `<div style="font-size:12.5px;padding:6px 0;border-bottom:1px solid var(--border);">[${tk.status}] ${Bass.escapeHtml(tk.title)} ${tk.vendor_name ? `— 배정: ${Bass.escapeHtml(tk.vendor_name)} (${Bass.escapeHtml(tk.visit_date)})` : ""}</div>`).join("")}
        </div>
      </div>

      </section>
      <section class="svc" data-svc="cert">
      ${blk(`      ${cert ? `
        <div class="bass-card no-print" style="margin-bottom:8px;">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
            <div>
              <h3 style="margin:0;">🧾 연말정산 월세 세액공제 참고용 납부확인서 (관리비 제외 순수 월세 기준)</h3>
              <div class="bass-muted" style="font-size:12.5px;">
                공제대상 순수 월세: <strong>${fmtWon(cert.rent_only_paid ?? cert.total_paid)}</strong> (관리비 ${fmtWon(cert.maintenance_paid || 0)} 제외) ·
                예상 세액공제: 총급여 5,500만 이하(17%) <strong>${fmtWon(cert.estimated_tax_credit_17pct)}</strong> / 5,500만~8,000만 이하(15%) <strong>${fmtWon(cert.estimated_tax_credit_15pct || 0)}</strong>
              </div>
              <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                💡 ${Bass.escapeHtml(cert.guidance_notice || "월세 세액공제는 임대차계약서 사본과 계좌이체 내역으로 신청하며, 임대인의 법정 증명서 발급 의무는 없습니다.")}
              </div>
            </div>
            <button onclick="window.print()" class="bass-btn bass-btn--primary" type="button">🖨️ 납부확인서 A4 인쇄 / PDF</button>
          </div>
        </div>
        <article class="cert-paper">
          <div style="display:flex;justify-content:space-between;border-bottom:2px solid #000;padding-bottom:8px;margin-bottom:12px;">
            <div>
              <h2 style="margin:0;color:#000;">월세 납부 내역 확인서 (${cert.tax_year}년 귀속 · 연말정산 참고용)</h2>
              <div style="font-size:12px;">문서번호: ${Bass.escapeHtml(cert.certificate_no)} · 근거: ${Bass.escapeHtml(cert.statute)}</div>
            </div>
            <div style="border:2px solid #b91c1c;color:#b91c1c;padding:6px 10px;font-weight:800;font-size:12px;">확인필<br>${Bass.escapeHtml(cert.lessor.building_name)}</div>
          </div>
          <table>
            <tr><th>임대인</th><td>${Bass.escapeHtml(cert.lessor.name)} (${cert.lessor.business_number ? `사업자 ${Bass.escapeHtml(cert.lessor.business_number)}` : "개인임대/미등록"})</td><th>임차인</th><td>${Bass.escapeHtml(cert.tenant.name)} (${Bass.escapeHtml(cert.tenant.room_no)}호)</td></tr>
            <tr><th>임대 목적물 주소</th><td colspan="3">${Bass.escapeHtml(cert.lessor.address)}</td></tr>
            <tr><th>계약기간</th><td>${Bass.escapeHtml(cert.contract.move_in_date)} ~ ${Bass.escapeHtml(cert.contract.expiry_date)}</td><th>계약 접수번호</th><td>${Bass.escapeHtml(cert.contract.fixed_date_no || "-")} <span class="bass-muted" style="font-size:11px;">(접수번호, 확정일자 아님)</span></td></tr>
            <tr><th>연간 순수 월세 납입액<br><span style="font-size:11px;font-weight:400;">(관리비 ${fmtWon(cert.maintenance_paid || 0)} 제외)</span></th><td><strong>${fmtWon(cert.rent_only_paid ?? cert.total_paid)}</strong><br><span style="font-size:11px;">(총 납부액 ${fmtWon(cert.total_paid)})</span></td><th>예상 세액공제(참고치)</th><td><strong>17%: ${fmtWon(cert.estimated_tax_credit_17pct)}</strong> (총급여 5,500만 이하)<br><strong>15%: ${fmtWon(cert.estimated_tax_credit_15pct || 0)}</strong> (총급여 5,500만~8,000만)</td></tr>
          </table>
          <table>
            <thead><tr><th>귀속년월</th><th>납부일자</th><th>납부수단</th><th>현금영수증 승인번호(실제 발급분만)</th><th>순수 월세(공제대상)</th><th>관리비(제외)</th><th>납부합계</th></tr></thead>
            <tbody>
              ${(cert.payments || []).map((p) => `<tr><td>${Bass.escapeHtml(p.billing_month)}</td><td>${Bass.escapeHtml(p.paid_date)}</td><td>${Bass.escapeHtml(p.method)}</td><td>${Bass.escapeHtml(p.cash_receipt_no || "계좌이체 증빙(미발급)")}</td><td><strong>${fmtWon(p.rent_amount ?? p.amount)}</strong></td><td>${fmtWon(p.maintenance_amount || 0)}</td><td>${fmtWon(p.amount)}</td></tr>`).join("")}
            </tbody>
          </table>
          <div style="font-size:11.5px;margin-top:8px;color:#334155;">
            ※ ${Bass.escapeHtml(cert.guidance_notice || "")}
          </div>
        </article>
      ` : ""}
`, "납부확인서를 만들 월세 납부 내역이 아직 없습니다.")}
      </section>
      <section class="svc" data-svc="messages">
        <div class="bass-card no-print">
          <h3 style="margin:0 0 6px;">💬 임대인과 채팅</h3>
          <div class="bass-muted" style="font-size:12.5px;margin-bottom:8px;">내 호실의 임대인과 주고받는 앱 안 대화입니다. 관리인에게 도움을 청할 때는 메뉴의 ‘관리인과 채팅’를 쓰세요.</div>
          <div id="tnMsgCall" style="margin-bottom:8px;"></div>
          <div id="tnMsgList" class="cm-list" style="max-height:50vh;"></div>
          <div id="tnMsgComposer"${R3Shell.isAdminSession() ? " hidden" : ""}>
            <textarea id="tnMsgInput" class="bass-input" rows="2" placeholder="임대인에게 보낼 채팅" style="width:100%;margin-top:8px;"></textarea>
            <button id="btnTnMsgSend" class="bass-btn bass-btn--primary" type="button" style="margin-top:6px;">보내기</button>
          </div>
          <div id="tnMsgNeg"></div>
        </div>
      </section>
      <section class="svc" data-svc="door">
        <div class="bass-card no-print" style="margin-bottom:14px;">
        <div class="field-grid" style="margin-top:14px;">
          <div class="listing-card" style="margin:0;">
            <div class="bass-muted" style="font-size:12px;">🔑 출입 · 와이파이 (계약 체결자 전용)</div>
            ${(data.keys && data.keys.locked) ? `<div class="bass-muted" style="font-size:12.5px;margin:6px 0;">🔒 도어락은 잠겨 있습니다. 화면 맨 위의 안내를 확인하세요.</div>` : ""}
            <div style="margin-top:6px;">세대 도어락: <strong>${showDoorlock ? Bass.escapeHtml(r.doorlock_pwd || t.doorlock_pwd) : "••••*"}</strong>
              <button id="btnToggleDl" class="bass-btn bass-btn--sm" style="margin-left:6px;" type="button">${showDoorlock ? "숨기기" : "보기"}</button>
            </div>
            <div style="font-size:12.5px;margin-top:4px;">공동현관: <code>${Bass.escapeHtml(b.common_entrance_pwd || "#1004*")}</code> · Wi-Fi: <code>${Bass.escapeHtml(b.wifi_info || "-")}</code></div>
          </div>

          <div class="listing-card" style="margin:0;">
            <div class="bass-muted" style="font-size:12px;">🚗 내 차량 건물 주차 등록</div>
            <div class="bass-row" style="margin-top:6px;">
              <input id="carNoInput" class="bass-input" style="flex:1;" value="${Bass.escapeHtml(t.car_number || "")}" placeholder="예: 12가 3456">
              <button id="btnSaveCar" class="bass-btn bass-btn--sm bass-btn--primary" type="button">등록</button>
            </div>
          </div>
        </div>
        </div>

      </section>
    `;

    const msgMenuBtn = document.getElementById("btnSvcMsg");
    if (msgMenuBtn) msgMenuBtn.textContent = "💬 임대인과 채팅" + ((data.messages_unread || 0) > 0 ? ` (${data.messages_unread})` : "");   // 안 읽은 수는 메뉴에도 보인다
    applyView();
    document.getElementById("btnTnMsgSend").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const inp = document.getElementById("tnMsgInput");
        const text = inp.value.trim();
        if (!text) { Bass.updateAppAlert("warning", "내용을 입력해 주세요."); return; }
        await Bass.api(APP_ID, `/myhome/${encodeURIComponent(t.id)}/messages`, { method: "POST", body: { body: text } });
        inp.value = "";
        await loadTenantMessages();
      });
    });
    document.getElementById("btnSvcBack").addEventListener("click", () => go("home"));
    elMyhome.querySelectorAll(".btn-todo").forEach((bt) => bt.addEventListener("click", () => {
      if (bt.dataset.go === "pay") {
        const inp = document.getElementById("payAmtInput");
        if (inp) { inp.scrollIntoView({ behavior: "smooth", block: "center" }); inp.focus(); }
      } else {
        go(bt.dataset.go);
      }
    }));

    const btnOpenPhoto = document.getElementById("btnOpenContractPhoto");
    const imgPhotoPrev = document.getElementById("imgContractPhotoPreview");
    if (btnOpenPhoto && ct && ct.id) {
      const fetchPhotoBlobUrl = async () => {
        const headers = {};
        const tk = Bass.token();
        if (tk) headers["Authorization"] = "Bearer " + tk;
        const resp = await fetch(`${Bass.apiBase(APP_ID)}/contracts/${encodeURIComponent(ct.id)}/photo`, { headers });
        if (!resp.ok) throw new Error("계약서 사본을 불러올 수 없습니다.");
        const blob = await resp.blob();
        return URL.createObjectURL(blob);
      };
      if (imgPhotoPrev) {
        fetchPhotoBlobUrl().then((url) => {
          imgPhotoPrev.src = url;
          imgPhotoPrev.style.display = "block";
        }).catch(() => {});
      }
      btnOpenPhoto.addEventListener("click", () => {
        fetchPhotoBlobUrl().then((url) => {
          window.open(url, "_blank", "noopener");
          setTimeout(() => URL.revokeObjectURL(url), 60000);
        }).catch(Bass.showError);
      });
    }

    document.querySelectorAll(".btn-ins-proof").forEach((btn) => btn.addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const issuer = window.prompt("보증기관 (예: HUG, SGI서울보증)");
        if (!issuer) return;
        const no = window.prompt("증권번호 또는 가입 접수번호");
        if (!no) return;
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/insurance-proof`, { method: "POST", body: { issuer, policy_no: no } });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    }));

    document.getElementById("btnToggleDl").addEventListener("click", () => {
      showDoorlock = !showDoorlock;
      loadMyhome().catch(Bass.showError);
    });

    document.getElementById("btnSaveCar").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/vehicle`, {
          method: "POST",
          body: { car_number: document.getElementById("carNoInput").value },
        });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });

    if (window.R3Shell && R3Shell.isViaPin && R3Shell.isViaPin()) {
      // 간편 비밀번호로 들어온 상태: 서버가 막는 작업은 미리 비활성화하고 이유를 알려 준다
      ["btnAgreeCo", "btnPlanStay", "btnPlanMoveOut", "btnAckRenewal"].forEach((id) => {
        const b = document.getElementById(id);
        if (b) { b.disabled = true; b.title = "아이디·비밀번호로 로그인해야 할 수 있습니다"; b.insertAdjacentHTML("afterend", '<div class="bass-muted" style="font-size:12px;">🔐 이 작업은 아이디·비밀번호로 로그인해야 합니다.</div>'); }
      });
    }
    document.getElementById("btnPayNow").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/transfer-notice`, {
          method: "POST",
          body: { amount: Number(document.getElementById("payAmtInput").value || 0) },
        });
        Bass.updateAppAlert(res.level || "success", res.message);
        await loadMyhome();
      });
    });

    document.getElementById("btnSubmitAs").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const title = document.getElementById("asTitle").value.trim();
        if (!title) {
          Bass.updateAppAlert("warning", "수리 요청 제목을 입력해 주세요.");
          return;
        }
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/tickets`, {
          method: "POST",
          body: { category: document.getElementById("asCat").value, title, description: title },
        });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });

    const btnAgreeCo = document.getElementById("btnAgreeCo");
    if (btnAgreeCo) {
      btnAgreeCo.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/checkout/${btnAgreeCo.dataset.id}/agree`, {
            method: "POST",
            body: {
              bank_account: document.getElementById("coBank").value,
              account_holder: document.getElementById("coHolder").value,
              signature: document.getElementById("coSig").value,
            },
          });
          Bass.updateAppAlert("success", res.message);
          await loadMyhome();
        });
      });
    }

    const btnRequestCo = document.getElementById("btnRequestCo");
    if (btnRequestCo) btnRequestCo.addEventListener("click", function () {
      if (!window.confirm("퇴실을 신청할까요?\n퇴실이 확정되면 바로가기·간편 비밀번호가 정리됩니다. 연말정산 납부확인서는 먼저 내려받아 두세요.")) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}/checkout/request`, { method: "POST", body: { desired_date: document.getElementById("coWant").value, memo: document.getElementById("coMemo").value } });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });
    const btnReceivedCo = document.getElementById("btnReceivedCo");
    if (btnReceivedCo) btnReceivedCo.addEventListener("click", function () {
      if (!window.confirm("보증금이 입금된 것을 확인하셨나요? 확인하면 퇴실이 확정됩니다.")) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/checkout/${btnReceivedCo.dataset.id}/confirm-received`, { method: "POST", body: {} });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });
    const btnDisputeCo = document.getElementById("btnDisputeCo");
    if (btnDisputeCo) btnDisputeCo.addEventListener("click", function () {
      const note = window.prompt("이의 내용을 적어 주세요(예: 청소비가 과하다 / 입금을 받지 못했다).");
      if (!note || !note.trim()) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/checkout/${btnDisputeCo.dataset.id}/dispute`, { method: "POST", body: { note: note.trim() } });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });

    const btnPlanStay = document.getElementById("btnPlanStay");
    if (btnPlanStay) {
      btnPlanStay.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const memo = (document.getElementById("expTenantMemo") && document.getElementById("expTenantMemo").value) || "";
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/expiry-plan`, {
            method: "POST",
            body: {
              tenant_plan: "STAY",
              tenant_memo: memo || "전출의사 없음 — 계속 거주 및 연장 희망",
              prelist_vacancy: false,
            },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    }

    const btnPlanMoveOut = document.getElementById("btnPlanMoveOut");
    if (btnPlanMoveOut) {
      btnPlanMoveOut.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const moveDate = (document.getElementById("expMoveOutDate") && document.getElementById("expMoveOutDate").value) || ct.expiry_date || "";
          const memo = (document.getElementById("expTenantMemo") && document.getElementById("expTenantMemo").value) || "";
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/expiry-plan`, {
            method: "POST",
            body: {
              tenant_plan: "MOVE_OUT",
              planned_move_out_date: moveDate,
              tenant_memo: memo || `만기일(${moveDate}) 퇴실 예정`,
              prelist_vacancy: true,
            },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    }

    const btnAckRenewal = document.getElementById("btnAckRenewal");
    if (btnAckRenewal) {
      btnAckRenewal.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const memo = (document.getElementById("expTenantMemo") && document.getElementById("expTenantMemo").value) || "";
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/acknowledge-renewal`, {
            method: "POST",
            body: { tenant_memo: memo },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    }

    elMyhome.querySelectorAll(".btn-ack-bn").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/billing-notices/${btn.dataset.id}/acknowledge`, {
            method: "POST",
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    });

    elMyhome.querySelectorAll(".btn-jump-pay").forEach((btn) => {
      btn.addEventListener("click", () => {
        go("home");
        const payInp = document.getElementById("payAmtInput");
        if (payInp) {
          payInp.value = btn.dataset.amt || payInp.value;
          payInp.scrollIntoView({ behavior: "smooth", block: "center" });
          payInp.focus();
        }
      });
    });
  }

  async function loadListings() {
    const data = await Bass.api(APP_ID, "/listings");
    document.getElementById("poolBadge").textContent = `JSON Pool v${data.pool_revision}`;
    const list = data.listings || [];

    elListings.innerHTML = `
      <div class="bass-row no-print" style="gap:8px;margin:0 0 12px;"><button id="btnBackHome" class="bass-btn bass-btn--sm" type="button">← 내 집</button><strong style="font-size:15px;">빈 집 찾기</strong></div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📢 지금 계약 가능한 빈 집 (${list.length}실)</h3>
        <p class="bass-muted" style="font-size:13px;">임대인 공실 호실 및 만기 전출 확정 호실이 실시간으로 표시되며, 계약 체결 즉시 자동으로 목록에서 마감됩니다.</p>
      </div>
      ${list.map((r) => `
        <div class="listing-card">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <strong style="font-size:17px;">[${Bass.escapeHtml(r.building_name)}] ${Bass.escapeHtml(r.name || `${r.room_no}호`)}</strong>
              <div class="bass-muted" style="font-size:13px;">${Bass.escapeHtml(r.address)} · ${r.floor}${r.total_floors ? `/${r.total_floors}` : ""}층 · 전용 ${r.area_sqm}㎡${r.direction ? ` · ${Bass.escapeHtml(r.direction)}` : ""} · 입주 ${Bass.escapeHtml(r.available_from)}</div>
            </div>
            <div class="bass-row" style="gap:6px;">
              ${r.is_pre_moveout_listing ? `<span class="bass-badge bass-badge--warn">📅 만기퇴실 예정 사전공개</span>` : ""}
              <span class="bass-badge bass-badge--ok">등기부 근저당 0원 클린</span>
            </div>
          </div>
          <div style="font-size:16px;font-weight:800;margin:8px 0;color:var(--primary-2);">
            보증금 ${fmtWon(r.deposit)} / 월세 ${fmtWon(r.monthly_rent)} (관리비 ${fmtWon(r.maintenance_fee)}, ${Bass.escapeHtml(r.pay_timing)})
          </div>
          <div class="bass-muted" style="font-size:13px;margin-bottom:10px;">${Bass.escapeHtml(r.status_description)}</div>
          <div class="bass-row">
            <input class="bass-input inq-name" data-room="${r.room_no}" style="width:120px;" placeholder="내 이름">
            <input class="bass-input inq-msg" data-room="${r.room_no}" style="flex:1;" value="이번 주말 현장 방문투어 예약하고 싶습니다!">
            <button class="bass-btn bass-btn--sm btn-send-inq" data-branch="${r.branch}" data-room="${r.room_no}" type="button">💬 1:1 방문문의</button>
            ${r.active_cosign_session
              ? `<a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?session=${r.active_cosign_session.id}&role=tenant">🤝 열린 계약방 입장(코드 ${r.active_cosign_session.session_code})</a>`
              : `<a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?role=tenant">🤝 공동 임대계약 앱 이동</a>`
            }
          </div>
        </div>
      `).join("")}
    `;

    document.getElementById("btnBackHome").addEventListener("click", () => go("home"));
    elListings.querySelectorAll(".btn-send-inq").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const roomNo = btn.dataset.room;
          const name = elListings.querySelector(`.inq-name[data-room="${roomNo}"]`).value.trim() || "예비임차인";
          const msg = elListings.querySelector(`.inq-msg[data-room="${roomNo}"]`).value.trim();
          const res = await Bass.api(APP_ID, "/inquiries", {
            method: "POST",
            body: { branch: btn.dataset.branch, room_no: roomNo, sender_name: name, message: msg },
          });
          Bass.updateAppAlert("success", res.message);
        });
      });
    });
  }

  setMode(mode);
})();

