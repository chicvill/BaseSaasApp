/* apps/rent3_admin/frontend/app.js — 플랫폼 총괄 관리인 관제 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_admin";
  // 알림 센터 링크(?tab=lessors 등)로 들어오면 그 탭부터 연다
  let currentTab = ["support", "lessors", "portfolio", "ops"].includes(new URLSearchParams(location.search).get("tab")) ? new URLSearchParams(location.search).get("tab") : "support";
  let ov = null;

  const FR_LABEL = { PENDING_APPROVAL: "⏳ 가맹 심사 중", ACTIVE: "✅ 가맹 승인", SUSPENDED: "⛔ 이용 정지", REJECTED: "❌ 가맹 반려" };
  const FR_BADGE = { PENDING_APPROVAL: "bass-badge--warn", ACTIVE: "bass-badge--ok", SUSPENDED: "bass-badge--warn", REJECTED: "bass-badge--warn" };

  // 인터넷등기소 발급확인: 번호 복사 → 등기소 열기 → 원본 대조 결과 기록 (누가·언제 확인했는지 남는다)
  function iirosHtml(l, a) {
    const reg = a.registry || {};
    const v = reg.verification;
    const num = reg.issue_number || "";
    return `<div style="border:1px dashed var(--border, #475569);border-radius:8px;padding:6px 8px;margin-top:6px;">
      <div><strong>등기부 원본 확인</strong> (인터넷등기소 발급문서 진위확인)</div>
      <div>AI가 읽은 발급확인번호: <code>${Bass.escapeHtml(num || "읽지 못함 — 서류에서 직접 확인")}</code>
        ${num ? `<button class="bass-btn bass-btn--sm btn-copy-num" data-num="${Bass.escapeHtml(num)}" type="button">번호 복사</button>` : ""}
        <button class="bass-btn bass-btn--sm btn-open-iros" data-num="${Bass.escapeHtml(num)}" type="button">인터넷등기소 열기 ↗</button></div>
      ${v ? `<div style="margin-top:4px;">${v.verified ? "✅ 원본 대조 완료" : "⚠️ 원본과 다름으로 기록"} — ${Bass.escapeHtml(v.verified_by || "")} · ${Bass.escapeHtml(String(v.verified_at || "").slice(0, 16).replace("T", " "))}
        · 조회 번호 <code>${Bass.escapeHtml(v.issue_number || "-")}</code>${v.matches_ai_number ? "" : " (AI가 읽은 번호와 다름)"}${v.note ? `<br>메모: ${Bass.escapeHtml(v.note)}` : ""}</div>` : `<div class="bass-muted" style="margin-top:4px;">아직 원본 대조를 기록하지 않았습니다. 승인하려면 먼저 기록해야 합니다.</div>`}
      <button class="bass-btn bass-btn--sm bass-btn--primary btn-reg-verify" data-id="${l.id}" data-num="${Bass.escapeHtml(num)}" type="button">📝 원본 대조 결과 기록</button>
    </div>`;
  }

  // 국세청 홈택스 사업자등록증 확인: 번호 복사 → 홈택스 열기 → 확인·승인 결과 기록 (승인 시 장부 사업자번호 자동 반영)
  function hometaxBizHtml(l, a) {
    const biz = a.bizreg || {};
    const bchk = biz.check;
    const bv = biz.verification;
    const bno = biz.business_number || "";
    const col = bchk ? ({ ok: "var(--ok)", caution: "var(--warn)", high: "var(--err)" }[bchk.level] || "var(--warn)") : "var(--warn)";
    return `<div style="border:1px dashed var(--border, #475569);border-radius:8px;padding:6px 8px;margin-top:6px;">
      <div><strong>📷 사업자등록증 홈택스 확인·승인</strong> (국세청 홈택스 사업자상태·진위확인)</div>
      <div>사업자등록번호: <code>${Bass.escapeHtml(bno || "미입력 — 원본 사진에서 확인")}</code>
        ${biz.representative_name ? ` · 대표자: <b>${Bass.escapeHtml(biz.representative_name)}</b>` : ""}
        ${biz.company_name ? ` · 상호: ${Bass.escapeHtml(biz.company_name)}` : ""}
        ${biz.tax_type ? ` · (${Bass.escapeHtml(biz.tax_type)})` : ""}
        ${bno ? `<button class="bass-btn bass-btn--sm btn-copy-biz" data-num="${Bass.escapeHtml(bno)}" type="button">사업자번호 복사</button>` : ""}
        <button class="bass-btn bass-btn--sm btn-open-hometax" data-num="${Bass.escapeHtml(bno)}" type="button">국세청 홈택스 열기 ↗</button>
      </div>
      ${bchk ? `<div style="border-left:4px solid ${col};padding:4px 8px;margin-top:4px;">${bchk.auto_pass ? "🟢 10자리 체크섬·대표자 일치" : "🟠 수동 확인 필요"} — ${Bass.escapeHtml(bchk.message)}</div>` : ""}
      ${bv ? `<div style="margin-top:4px;">${bv.verified ? "✅ 홈택스 확인·승인 완료" : "⚠️ 반려됨"} — ${Bass.escapeHtml(bv.verified_by || "")} · ${Bass.escapeHtml(String(bv.verified_at || "").slice(0, 16).replace("T", " "))}
        · 번호 <code>${Bass.escapeHtml(bv.business_number || "-")}</code> (${Bass.escapeHtml(bv.hometax_status || "")})${bv.note ? `<br>메모: ${Bass.escapeHtml(bv.note)}` : ""}</div>`
        : `<div class="bass-muted" style="margin-top:4px;">아직 홈택스 확인·승인을 기록하지 않았습니다. 승인하면 임대인 장부 사업자번호에 반영됩니다.</div>`}
      <button class="bass-btn bass-btn--sm bass-btn--primary btn-biz-verify" data-id="${l.id}" data-num="${Bass.escapeHtml(bno)}" type="button">✅ 홈택스 확인·승인 기록</button>
    </div>`;
  }

  // 가맹 신청 요약: 본인인증·건물·등기부·사업자등록증 자동 대조 결과와 제출된 원본 링크
  function appSummaryHtml(l) {
    const a = l.application || {};
    const s = a.steps || {};
    const chk = (a.registry || {}).check;
    const mark = (ok) => (ok ? "✅" : "⬜");
    const col = chk ? ({ ok: "var(--ok)", caution: "var(--warn)", high: "var(--err)" }[chk.level] || "var(--warn)") : "";
    const idn = a.identity || {};
    return `<div style="font-size:12.5px;margin:4px 0;line-height:1.6;">
      ${mark(s.identity)} 본인인증${idn.verified ? ` (${Bass.escapeHtml(idn.name)}${idn.fake ? " · <b>시연용 가짜 인증</b>" : ""})` : ""}
      · ${mark(s.building)} 건물 주소 · ${mark(s.registry)} 등기부 · ${mark(s.bizreg)} 사업자등록증 · ${mark(s.submitted)} 제출
      ${s.registry ? ` · <button class="bass-btn bass-btn--sm btn-view-reg-att" data-id="${Bass.escapeHtml(l.id)}" type="button">등기부 원본 보기</button>` : ""}
      ${s.bizreg ? ` · <button class="bass-btn bass-btn--sm btn-view-biz-att" data-id="${Bass.escapeHtml(l.id)}" type="button">사업자등록증 원본 보기</button>` : ""}
      ${chk ? `<div style="border-left:4px solid ${col};padding:4px 8px;margin-top:4px;">${chk.auto_pass ? "🟢 자동 대조 일치 (심사 우선)" : "🟠 수동 확인 필요"} — ${Bass.escapeHtml(chk.message)}</div>` : ""}
      ${s.registry ? iirosHtml(l, a) : ""}
      ${s.bizreg ? hometaxBizHtml(l, a) : ""}
    </div>`;
  }

  const elTabContent = document.getElementById("tabContent");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  async function refresh() {
    ov = await Bass.api(APP_ID, "/overview");
    const meta = ov.pool_meta || {};
    const kpi = ov.kpi || {};
    document.getElementById("poolBadge").textContent = `JSON Pool v${meta.total_revision} (${meta.total_objects} 객체)`;

    document.getElementById("kpiGrid").innerHTML =
      `<span>가맹 ${kpi.total_buildings}채 · ${kpi.occupied_rooms}/${kpi.total_rooms}실</span>` +
      `<span class="bass-muted">월 이용료 ${fmtWon(kpi.monthly_saas_mrr)}</span>`;

    renderTab();
  }

  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    currentTab = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    renderTab();
  });

  function renderTab() {
    if (!ov) return;
    if (currentTab === "support") renderSupportTab();
    else if (currentTab === "lessors") renderLessorsTab();
    else if (currentTab === "portfolio") renderPortfolioTab();
    else if (currentTab === "ops") renderOpsTab().catch(Bass.showError);
    else renderPoolTab();
  }


  // ───────── 원격 지원 · 대리 접속 ─────────
  // 건물 → 호실 → 입장. 사용자가 원격 지원을 허용했고 지원 채팅(또는 전화 접수)이 있어야 입장하며, 입장하면 보기 전용이다.
  const sp = { branch: "", roomNo: "", user: "", cat: "사용법 문의" };
  let spTimer = null;
  const spFmt = (iso) => (String(iso || "").length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");

  async function renderSupportTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    // 알림창의 '최근 대화'에서 들어오면(?user=lessor:..) 그 사용자의 건물·호실을 고르고 채팅을 바로 연다(한 번만)
    const qUser = new URLSearchParams(location.search).get("user");
    if (qUser && currentTab === "support") {
      try {
        const t0 = await Bass.api(APP_ID, `/support/thread?user=${encodeURIComponent(qUser)}`);
        const u0 = t0.user || {};
        sp.branch = u0.branch || sp.branch; sp.roomNo = u0.room_no || ""; sp.user = qUser;
      } catch (_) { /* 사용자를 못 찾으면 평소처럼 목록부터 */ }
      try { const u = new URL(location.href); u.searchParams.delete("user"); history.replaceState(history.state, "", u.toString()); } catch (_) { /* 주소 정리 실패는 무시 */ }
    }
    let d = await Bass.api(APP_ID, "/support/directory" + (sp.branch ? `?branch=${encodeURIComponent(sp.branch)}` : ""));
    if (!sp.branch && d.branches.length) {
      sp.branch = d.branches[0].branch;
      d = await Bass.api(APP_ID, `/support/directory?branch=${encodeURIComponent(sp.branch)}`);
    }
    if (currentTab !== "support") return;
    const rooms = d.rooms || [];
    if (!rooms.some((r) => r.room_no === sp.roomNo)) sp.roomNo = "";
    const room = rooms.find((r) => r.room_no === sp.roomNo) || null;
    const cards = [];
    if (d.lessor) cards.push({ ...d.lessor, role: "lessor", title: `🏢 임대인 ${d.lessor.name}` });
    if (room && room.tenant) cards.push({ ...room.tenant, role: "tenant", title: `🏠 ${room.room_no}호 임차인 ${room.tenant.name}` });
    if (!cards.some((c) => c.user === sp.user)) sp.user = "";
    const badge = (c) => `<span class="bass-badge ${c.support_allowed ? "bass-badge--ok" : "bass-badge--warn"}">원격 지원 ${c.support_allowed ? "허용 ON" : "허용 OFF"}</span>` +
      ` <span class="bass-badge ${c.has_thread ? "bass-badge--info" : ""}">${c.has_thread ? "채팅 있음" : "채팅 없음"}</span>` +
      (c.unread > 0 ? ` <span class="bass-badge bass-badge--err">안 읽음 ${c.unread}</span>` : "");
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3 style="margin:0 0 4px;">💬 임대인·임차인 채팅 · 원격 지원</h3>
        <p class="bass-muted" style="font-size:12.5px;margin:0 0 10px;">① 건물·호실을 고르고 임대인 또는 임차인 카드의 <strong>[💬 채팅하기]</strong>를 누르면 그 사용자와 채팅(관리인이 먼저 글을 보내 시작 가능)할 수 있습니다. 화면 <strong>입장</strong>은 그 사용자 본인이 <strong>원격 지원을 허용</strong>했을 때만 되며, 보기 전용이고 접속 기록이 남습니다.</p>
        <div class="field-grid">
          <label class="r3-fld" style="display:grid;gap:3px;font-size:12.5px;">① 건물<select id="spBranch" class="bass-select">${d.branches.map((b) => `<option value="${Bass.escapeHtml(b.branch)}" ${b.branch === sp.branch ? "selected" : ""}>${Bass.escapeHtml(b.building_name)} (${Bass.escapeHtml(b.branch)})</option>`).join("")}</select></label>
          <label class="r3-fld" style="display:grid;gap:3px;font-size:12.5px;">② 호실<select id="spRoom" class="bass-select"><option value="">건물 전체 (임대인)</option>${rooms.map((r) => `<option value="${Bass.escapeHtml(r.room_no)}" ${r.room_no === sp.roomNo ? "selected" : ""}>${Bass.escapeHtml(r.room_no)}호 ${r.tenant ? Bass.escapeHtml(r.tenant.name) : "공실"}${r.sessions.length ? ` · 계약방 ${r.sessions.length}` : ""}</option>`).join("")}</select></label>
        </div>
        <div style="margin-top:10px;">${cards.map((c) => `
          <div class="item-card" style="margin-top:8px;">
            <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
              <div><strong>${Bass.escapeHtml(c.title)}</strong> <span class="bass-muted" style="font-size:12px;">(끝자리 ${Bass.escapeHtml(c.phone_tail)})</span><div style="margin-top:4px;">${badge(c)}</div></div>
              <button class="bass-btn bass-btn--sm ${c.user === sp.user ? "bass-btn--primary" : ""} btn-sp-open" data-user="${Bass.escapeHtml(c.user)}" type="button">💬 채팅하기</button>
            </div>
          </div>`).join("") || '<div class="bass-muted">이 건물에서 보여 줄 사용자가 없습니다.</div>'}</div>
      </div>
      <div id="spThread"></div>`;
    document.getElementById("spBranch").addEventListener("change", (e) => { sp.branch = e.target.value; sp.roomNo = ""; sp.user = ""; renderSupportTab().catch(Bass.showError); });
    document.getElementById("spRoom").addEventListener("change", (e) => { sp.roomNo = e.target.value; sp.user = ""; renderSupportTab().catch(Bass.showError); });
    elTabContent.querySelectorAll(".btn-sp-open").forEach((b) => b.addEventListener("click", () => { sp.user = b.dataset.user; renderSupportThread(d, cards.find((c) => c.user === sp.user), room).catch(Bass.showError); }));
    if (sp.user) await renderSupportThread(d, cards.find((c) => c.user === sp.user), room);
  }

  async function renderSupportThread(d, card, room) {
    const box = document.getElementById("spThread");
    if (!card || !box) return;
    const t = await Bass.api(APP_ID, `/support/thread?user=${encodeURIComponent(card.user)}`);
    const msgs = t.messages || [];
    const callDigits = String((t.user && t.user.phone) || "").replace(/\D/g, "");
    const callPhone = callDigits.length === 11 ? `${callDigits.slice(0, 3)}-${callDigits.slice(3, 7)}-${callDigits.slice(7)}` : callDigits.length === 10 ? `${callDigits.slice(0, 3)}-${callDigits.slice(3, 6)}-${callDigits.slice(6)}` : callDigits;
    const can = !!card.support_allowed;                        // 허용만 되어 있으면 지원 채팅이 없어도 입장할 수 있다
    const sessions = card.role === "lessor" ? rooms_sessions(d) : (room ? room.sessions : []);
    const who = card.role === "lessor" ? "임대인" : "임차인";
    const why = !card.support_allowed ? `이 ${who}(${card.name || ""})는 원격 지원을 허용하지 않았습니다. ${who} 본인이 문의 창의 ‘원격 지원 허용’을 켜야 합니다(다른 사람의 허용으로는 열 수 없습니다). 전화로 방법을 안내해 주세요.` : "";
    box.innerHTML = `
      <div class="bass-card">
        <h3 style="margin:0 0 6px;">💬 ${Bass.escapeHtml(card.title)} · 지원 채팅</h3>
        <div id="spList" style="max-height:260px;overflow-y:auto;border:1px solid var(--border);border-radius:10px;padding:6px 8px;">${msgs.map((m) => `
          <div style="margin:5px 0;font-size:13px;"><span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(spFmt(m.created_at))} · ${m.sender_role === "user" ? Bass.escapeHtml(card.name) : (m.sender_role === "admin" ? "관리인" : "안내")}</span><br>${Bass.escapeHtml(m.body)}</div>`).join("") || '<div class="bass-muted" style="font-size:13px;">대화가 없습니다.</div>'}</div>
        <div class="bass-row" style="gap:6px;margin-top:8px;"><input id="spMsg" class="bass-input" style="flex:1;" placeholder="사용자에게 보낼 글"><button id="btnSpSend" class="bass-btn bass-btn--sm bass-btn--primary" type="button">보내기</button></div>
        <div class="bass-muted" style="font-size:12px;margin-top:4px;">대화는 ${t.retention_days}일 뒤 삭제됩니다. 접속 기록(사유 분류 포함)은 1년 보관됩니다.</div>

        <div style="margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);">
          <div class="bass-row" style="gap:8px;align-items:center;flex-wrap:wrap;"><strong style="font-size:13.5px;">📞 ${Bass.escapeHtml(card.title)}에게 전화 :</strong>
            ${callPhone ? `<a class="bass-btn bass-btn--sm bass-btn--primary" href="tel:${Bass.escapeHtml(callDigits)}" style="min-height:40px;">${Bass.escapeHtml(callPhone)}</a>` : '<span class="bass-muted" style="font-size:12.5px;">등록된 전화번호가 없습니다</span>'}</div>
          <div class="bass-muted" style="font-size:12.5px;margin-top:6px;">💡 중요한 통화 내용은 위 채팅창에 기록을 남겨 주세요.</div>
        </div>

        <div style="margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);">
          <div class="bass-row" style="justify-content:space-between;gap:8px;"><strong style="font-size:13.5px;">🛡️ 입장 (보기 전용)</strong>
            <label class="bass-muted" style="font-size:12.5px;display:flex;align-items:center;gap:6px;">사유 <select id="spCat" class="bass-select" style="width:auto;">${d.categories.map((c) => `<option ${c === sp.cat ? "selected" : ""}>${Bass.escapeHtml(c)}</option>`).join("")}</select></label></div>
          ${why ? `<div style="font-size:12.5px;color:var(--warn);margin:6px 0;">${Bass.escapeHtml(why)}</div>` : ""}
          <div class="bass-row" style="gap:6px;margin-top:6px;flex-wrap:wrap;">
            ${card.role === "lessor" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-sp-enter" data-view="lessor" type="button" ${can ? "" : "disabled"}>🏢 임대인 입장</button>` : `<button class="bass-btn bass-btn--sm bass-btn--primary btn-sp-enter" data-view="tenant" type="button" ${can ? "" : "disabled"}>🏠 임차인 입장</button>`}
            ${sessions.map((x) => `<button class="bass-btn bass-btn--sm btn-sp-enter" data-view="contract" data-sid="${Bass.escapeHtml(x.id)}" type="button" ${can ? "" : "disabled"}>🤝 계약방 ${Bass.escapeHtml(x.room_no || "")}호 (${card.role === "lessor" ? "임대인" : "임차인"} 시점 · ${x.step}단계)</button>`).join("")}
          </div>
        </div>
      </div>`;
    const list = document.getElementById("spList");
    list.scrollTop = list.scrollHeight;
    let spMsgs = msgs;
    const renderSpRows = (arr) => arr.map((m) => `
          <div style="margin:5px 0;font-size:13px;"><span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(spFmt(m.created_at))} · ${m.sender_role === "user" ? Bass.escapeHtml(card.name) : (m.sender_role === "admin" ? "관리인" : "안내")}</span><br>${Bass.escapeHtml(m.body)}</div>`).join("") || '<div class="bass-muted" style="font-size:13px;">대화가 없습니다.</div>';
    clearInterval(spTimer);
    spTimer = setInterval(async () => {
      if (currentTab !== "support" || sp.user !== card.user || !document.getElementById("spList")) { clearInterval(spTimer); return; }
      if (document.visibilityState === "hidden") return;
      try {
        const nt = await Bass.api(APP_ID, `/support/thread?user=${encodeURIComponent(card.user)}`);
        const nm = nt.messages || [];
        if (nt.user && Boolean(nt.user.support_allowed) !== Boolean(card.support_allowed)) {      // 사용자가 허용을 켜거나 끄면 바로 화면(허용 표시·입장 단추)에 반영
          clearInterval(spTimer);
          renderSupportTab().catch(() => {});
          return;
        }
        const boxNow = document.getElementById("spList");
        if (boxNow && nm.length !== spMsgs.length) {
          spMsgs = nm;
          const atBottom = boxNow.scrollHeight - boxNow.scrollTop - boxNow.clientHeight < 40;
          boxNow.innerHTML = renderSpRows(nm);
          if (atBottom) boxNow.scrollTop = boxNow.scrollHeight;
        }
      } catch (_) { /* 다음에 다시 */ }
    }, 2500);
    const reload = () => renderSupportTab().catch(Bass.showError);
    document.getElementById("spCat").addEventListener("change", (e) => { sp.cat = e.target.value; });
    document.getElementById("btnSpSend").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const body = document.getElementById("spMsg").value.trim();
        if (!body) return;
        await Bass.api(APP_ID, "/support/messages", { method: "POST", body: { user: card.user, body } });
        await reload();
      });
    });
    box.querySelectorAll(".btn-sp-enter").forEach((btn) => btn.addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/support/enter", {
          method: "POST",
          body: { user: card.user, view: btn.dataset.view, session_id: btn.dataset.sid || "", room_no: sp.roomNo, category: document.getElementById("spCat").value },
        });
        window.location.href = res.redirect_url;
      });
    }));
  }

  function rooms_sessions(d) {
    return (d.rooms || []).flatMap((r) => (r.sessions || []).map((x) => ({ ...x, room_no: r.room_no })));
  }

  // 💳 가맹비 징수 통보·납부 등록·세금계산서 기록 (앱은 국세청에 직접 발행하지 않는다 — 홈택스에서 발행한 뒤 승인번호를 기록)
  async function loadFeeBox() {
    const box = document.getElementById("feeBox");
    if (!box) return;
    const E = Bass.escapeHtml, won = (n) => Number(n || 0).toLocaleString("ko-KR") + "원";
    const r = await Bass.api(APP_ID, "/fees");
    const sm = r.summary || {}, pl = r.platform || {}, et = r.etax || {};
    const chip = (f) => `<span class="bass-badge ${f.status === "PAID" ? "bass-badge--ok" : "bass-badge--warn"}">${E(f.state_label)}</span> <span class="bass-badge ${f.invoice_status === "ISSUED" ? "bass-badge--ok" : ""}">🧾 ${E(f.invoice_label)}</span>`;
    box.innerHTML = `
      <div style="font-size:13px;line-height:1.7;margin-bottom:6px;">청구 <strong>${won(sm.billed)}</strong> · 입금 <strong>${won(sm.paid)}</strong> · 미납 <strong>${won(sm.unpaid)}</strong>${sm.overdue ? ` · <span style="color:var(--err);font-weight:700;">연체 ${sm.overdue}건</span>` : ""}
        ${sm.invoice_waiting ? ` · <span style="color:var(--warn);font-weight:700;">🧾 세금계산서 발행 대기 ${sm.invoice_waiting}건</span>` : ""}</div>
      <div class="bass-muted" style="font-size:12px;margin-bottom:6px;">${et.enabled ? `🧾 전자세금계산서 API 연계 켜짐(${et.sandbox ? "테스트 키 · 국세청 미연동" : "운영 키"}) — ${et.auto ? "납부 등록 즉시 자동 발행" : "[🧾 API로 발행] 단추로 발행"}${et.webhook_configured ? "" : " · ⚠️ 웹훅 서명 키 없음(상태 확인 단추로만 결과 반영)"}` : `⚪ 전자세금계산서 API 연계 꺼짐 — 홈택스에서 발행한 뒤 승인번호를 기록하세요.${(et.missing || []).length ? ` (설정 필요: ${E((et.missing || []).join(", "))})` : ""}`}</div>
      <div class="bass-muted" style="font-size:12px;margin-bottom:6px;">${r.auto ? "🟢 자동 청구·통보 켜짐 — 매달 화면을 열 때 이번 달 청구가 없으면 만들어 통보합니다." : "⚪ 자동 청구·통보 꺼짐 — 월 가맹비 금액과 공급자 사업자번호를 확인한 뒤 서버 환경변수 RENT3_FEE_AUTO=1 로 켜세요. 그 전에는 아래 [이번 달 청구·통보 만들기]로 직접 만듭니다."}</div>
      ${pl.business_number ? "" : `<div class="bass-muted" style="font-size:12px;margin-bottom:6px;">⚠️ 공급자(플랫폼) 사업자번호가 서버 환경변수(RENT3_PLATFORM_BIZ_NO)에 없어 CSV·장부 경비에 비어 보입니다.</div>`}
      ${(r.fees || []).map((f) => `<div class="fee-row" data-id="${E(f.id)}" style="padding:8px 0;border-bottom:1px solid var(--border);font-size:13px;line-height:1.6;">
          <strong>${E(f.billing_month)}</strong> · ${E(f.lessor_name)} (${E(f.branch)}) · <strong>${won(f.total_amount)}</strong> <span class="bass-muted">(공급가 ${won(f.supply_amount)} + 부가세 ${won(f.vat_amount)})</span> · 기한 ${E(f.due_date)}<br>
          ${chip(f)}${f.receipt_type === "CASH_RECEIPT" && f.invoice_status === "ISSUED" ? ` <span class="bass-badge">💵 현금영수증</span>` : ""}${f.paid_at ? ` <span class="bass-muted">입금 ${E(f.paid_at)}</span>` : ""}${f.invoice_nts_no ? ` <code>${E(f.invoice_nts_no)}</code>` : ""}<br>
          ${f.status === "NOTICED" ? `<button type="button" class="bass-btn bass-btn--sm bass-btn--primary fee-paid" style="min-height:44px;">💰 납부 등록</button>` : ""}
          ${f.invoice_error ? `<span style="color:var(--err);font-size:12.5px;">⚠️ ${E(f.invoice_error)}</span><br>` : ""}
          ${f.status === "PAID" && ["NONE", "FAILED"].includes(f.invoice_status) ? `<button type="button" class="bass-btn bass-btn--sm bass-btn--primary fee-card" style="min-height:44px;">📋 발급 정보 · 홈택스</button>` : ""}
          ${f.status === "PAID" && ["NONE", "FAILED"].includes(f.invoice_status) && et.enabled ? `<button type="button" class="bass-btn bass-btn--sm fee-api" style="min-height:44px;">🧾 API로 발행</button>` : ""}
          ${f.invoice_status === "PENDING" ? `<button type="button" class="bass-btn bass-btn--sm bass-btn--primary fee-refresh" style="min-height:44px;">🔄 발행 상태 확인</button>` : ""}
          ${f.status === "PAID" && ["NONE", "FAILED"].includes(f.invoice_status) ? `<button type="button" class="bass-btn bass-btn--sm fee-inv" style="min-height:44px;">✍️ 홈택스 발행분 승인번호 기록</button>` : ""}
          ${f.invoice_status === "ISSUED" ? `<button type="button" class="bass-btn bass-btn--sm fee-invcancel" style="min-height:44px;">✖ 발행 기록 취소</button>` : ""}
        </div>`).join("") || '<div class="bass-muted">청구 내역이 없습니다. 월 가맹비가 있는 가맹 임대인에게 매달 자동으로 청구됩니다.</div>'}
      <div class="bass-row" style="gap:8px;flex-wrap:wrap;margin-top:10px;">
        <button id="feeGen" type="button" class="bass-btn bass-btn--sm" style="min-height:44px;">🔔 이번 달 청구·통보 만들기</button>
        <button id="feeCsv" type="button" class="bass-btn bass-btn--sm" style="min-height:44px;">📥 홈택스 발행용 목록(CSV)</button>
        <select id="feeLessor" class="bass-select" style="min-height:44px;">${(r.lessors || []).map((l) => `<option value="${E(l.id)}">${E(l.name)} · ${E(l.branch)} (${won(l.app_fee)})</option>`).join("")}</select>
        <button id="feePlan" type="button" class="bass-btn bass-btn--sm" style="min-height:44px;">⚙️ 월 가맹비 설정</button>
      </div>`;
    const fid = (el) => el.closest(".fee-row").dataset.id;
    box.querySelectorAll(".fee-paid").forEach((b) => b.addEventListener("click", function () {
      const f = r.fees.find((x) => x.id === fid(this));
      const amt = Number((window.prompt(`입금액(원) — 청구액 ${won(f.total_amount)}과 같아야 합니다.`, String(f.total_amount)) || "").replace(/\D/g, ""));
      if (!amt) return;
      const date = (window.prompt("입금일 (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "").trim();
      if (!date) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/fees/${f.id}/paid`, { method: "POST", body: { amount: amt, paid_date: date } });
        Bass.updateAppAlert("success", res.message);
        await loadFeeBox();
      });
    }));
    // 📋 홈택스 수동 발급: 값을 복사해 홈택스에 입력하고, 끝나면 승인번호를 기록한다(앱은 국세청에 직접 발행하지 않음)
    const recordFee = (f) => {
      const no = (window.prompt("홈택스 발급 후 국세청 승인번호 24자리 (예: 20261116-41000000-12345678)") || "").trim();
      if (!no) return;
      const date = (window.prompt("작성일 (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "").trim();
      Bass.withLock(null, async () => {
        const res = await Bass.api(APP_ID, `/fees/${f.id}/invoice`, { method: "POST", body: { nts_confirm_no: no, issue_date: date } });
        Bass.updateAppAlert("success", res.message);
        await loadFeeBox();
      });
    };
    const recordCash = (f) => {
      const no = (window.prompt("홈택스 현금영수증 발급 후 승인번호 (숫자 6~12자리)") || "").trim();
      if (!no) return;
      const date = (window.prompt("발급일 (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "").trim();
      Bass.withLock(null, async () => {
        const res = await Bass.api(APP_ID, `/fees/${f.id}/cash-receipt`, { method: "POST", body: { approval_no: no, issue_date: date } });
        Bass.updateAppAlert("success", res.message);
        await loadFeeBox();
      });
    };
    box.querySelectorAll(".fee-card").forEach((b) => b.addEventListener("click", function () {
      const f = r.fees.find((x) => x.id === fid(this));
      Bass.withLock(this, async () => {
        const { card } = await Bass.api(APP_ID, `/fees/${f.id}/invoice-card`);
        R3Tax.openCard({ title: card.title, writeDate: card.write_date, purpose: card.purpose, taxType: card.tax_type, supplier: card.supplier, buyer: card.buyer,
          items: card.items, note: card.note, missing: card.missing, cash: card.cash, taxAvailable: card.tax_available,
          onRecord: (mode) => (mode === "cash" ? recordCash(f) : recordFee(f)) });
      });
    }));
    box.querySelectorAll(".fee-api").forEach((b) => b.addEventListener("click", function () {
      const f = r.fees.find((x) => x.id === fid(this));
      if (!window.confirm(`${f.lessor_name}에게 ${won(f.total_amount)} 전자세금계산서를 API로 발행합니다.${et.sandbox ? "\n(테스트 키라 국세청에는 전송되지 않습니다)" : "\n발행하면 국세청에 전송됩니다."}\n계속할까요?`)) return;
      Bass.withLock(this, async () => {
        try { const res = await Bass.api(APP_ID, `/fees/${f.id}/invoice-auto`, { method: "POST", body: {} }); Bass.updateAppAlert("success", res.message); }
        finally { await loadFeeBox(); }
      });
    }));
    box.querySelectorAll(".fee-refresh").forEach((b) => b.addEventListener("click", function () {
      const f = r.fees.find((x) => x.id === fid(this));
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/fees/${f.id}/invoice-refresh`, { method: "POST", body: {} });
        Bass.updateAppAlert(res.fee.invoice_status === "ISSUED" ? "success" : "warning", res.message);
        await loadFeeBox();
      });
    }));
    box.querySelectorAll(".fee-inv").forEach((b) => b.addEventListener("click", function () {
      const f = r.fees.find((x) => x.id === fid(this));
      window.alert(`홈택스에서 ${f.lessor_name} (사업자번호 ${f.lessor_business_number || "미승인"})에게 ${won(f.total_amount)}(공급가 ${won(f.supply_amount)} + 부가세 ${won(f.vat_amount)}) 전자세금계산서를 발행한 뒤, 승인번호 24자리를 입력해 주세요.`);
      const no = (window.prompt("국세청 승인번호 24자리 (예: 20261116-41000000-12345678)") || "").trim();
      if (!no) return;
      const date = (window.prompt("작성일 (YYYY-MM-DD)", new Date().toISOString().slice(0, 10)) || "").trim();
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/fees/${f.id}/invoice`, { method: "POST", body: { nts_confirm_no: no, issue_date: date } });
        Bass.updateAppAlert("success", res.message);
        await loadFeeBox();
      });
    }));
    box.querySelectorAll(".fee-invcancel").forEach((b) => b.addEventListener("click", function () {
      const f = r.fees.find((x) => x.id === fid(this));
      const memo = (window.prompt(`세금계산서(${f.invoice_nts_no}) 기록을 취소합니다. 취소 사유를 적어 주세요.`) || "").trim();
      if (!memo) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/fees/${f.id}/invoice-cancel`, { method: "POST", body: { memo } });
        Bass.updateAppAlert("success", res.message);
        await loadFeeBox();
      });
    }));
    document.getElementById("feeGen").addEventListener("click", function () {
      Bass.withLock(this, async () => { const res = await Bass.api(APP_ID, "/fees/generate", { method: "POST", body: {} }); Bass.updateAppAlert("success", res.message); await loadFeeBox(); });
    });
    document.getElementById("feeCsv").addEventListener("click", async () => {
      const resp = await fetch(`${Bass.apiBase(APP_ID)}/fees/export-hometax-csv`, { headers: { Authorization: "Bearer " + Bass.token() } });
      if (!resp.ok) { Bass.updateAppAlert("error", "내려받지 못했습니다."); return; }
      const a = document.createElement("a"); a.href = URL.createObjectURL(await resp.blob()); a.download = "franchise_fee_hometax.csv"; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    });
    document.getElementById("feePlan").addEventListener("click", function () {
      const id = document.getElementById("feeLessor").value;
      const cur = (r.lessors.find((l) => l.id === id) || {}).app_fee || 0;
      const v = window.prompt("월 가맹비(원, 부가세 포함 금액). 0 이면 청구하지 않습니다.", String(cur));
      if (v === null) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/lessors/${encodeURIComponent(id)}/fee-plan`, { method: "POST", body: { app_fee: Number(String(v).replace(/\D/g, "") || 0) } });
        Bass.updateAppAlert("success", res.message);
        await loadFeeBox();
      });
    });
  }

  function renderLessorsTab() {
    const lessors = ov.lessors || [];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>💳 가맹비 징수 · 납부 등록 · 세금계산서</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:6px;">매달 가맹 임대인에게 <strong>자동으로 청구·통보</strong>됩니다. 입금을 확인하면 <strong>납부 등록</strong>, 홈택스에서 전자세금계산서를 발행한 뒤 <strong>승인번호를 기록</strong>하면 임대인 장부의 경비로 자동 등록되어 종합소득세 증빙이 됩니다. (앱이 국세청에 직접 발행하지는 않습니다.)</div>
        <div id="feeBox" class="bass-muted">불러오는 중...</div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>➕ 신규 임대인(건물주) 및 건물 가맹 등록 (users.json · buildings.json 즉시 반영)</h3>
        <div class="field-grid" style="margin:10px 0;">
          <div><label class="bass-label">지점 코드</label><input id="nlBranch" class="bass-input" placeholder="예: gangnam"></div>
          <div><label class="bass-label">건물명</label><input id="nlBldg" class="bass-input" placeholder="예: 강남스타빌"></div>
          <div><label class="bass-label">임대인 성명</label><input id="nlName" class="bass-input" placeholder="예: 오건물"></div>
          <div><label class="bass-label">연락처</label><input id="nlPhone" class="bass-input" placeholder="010-5555-7777"></div>
          <div><label class="bass-label">소재지 주소</label><input id="nlAddr" class="bass-input" placeholder="서울 강남구 역삼동 ..."></div>
          <div><label class="bass-label">월 SaaS 이용료(원)</label><input id="nlFee" type="number" class="bass-input" value="33000"></div>
        </div>
        <button id="btnAddLessor" class="bass-btn bass-btn--primary" type="button">🏢 가맹 임대인 및 건물 등재</button>
      </div>

      <div class="bass-card">
        <h3>👑 가맹 임대인 목록 & 월 SaaS 솔루션 이용료 정산</h3>
        <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">건물 전체 임대차관리를 맡기는 <strong>승인된 가맹점만</strong> 매물을 공개하고 직거래 계약을 할 수 있습니다. 가입 직후는 "심사 중"입니다.</div>
        ${lessors.slice().sort((a, b) => (a.status === "PENDING_APPROVAL" ? 0 : 1) - (b.status === "PENDING_APPROVAL" ? 0 : 1)).map((l) => `
          <div class="item-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <div>
                <strong>${Bass.escapeHtml(l.name)}</strong> (지점: <code>${Bass.escapeHtml(l.branch)}</code> · 연락처: ${Bass.escapeHtml(l.phone)})
                <div class="bass-muted" style="font-size:12.5px;">사업자: ${Bass.escapeHtml(l.business_number || "-")} · 계좌: ${Bass.escapeHtml(l.account_number || "-")} · 월 사용료: ${fmtWon(l.app_fee)}</div>
                ${l.status === "PENDING_APPROVAL" || l.status === "REJECTED" ? appSummaryHtml(l) : ""}
                <div class="bass-muted" style="font-size:12.5px;">호실 ${l.room_count}/${l.room_limit}실 등록 · 공개 ${l.published_count}실${l.status_note ? ` · 사유: ${Bass.escapeHtml(l.status_note)}` : ""}</div>
              </div>
              <div class="bass-row" style="flex-wrap:wrap;">
                <span class="bass-badge ${FR_BADGE[l.status] || ""}">${FR_LABEL[l.status] || Bass.escapeHtml(l.status)}</span>
                ${l.status !== "ACTIVE" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-decide" data-id="${l.id}" data-d="approve" type="button">✅ 가맹 승인</button>` : ""}
                ${l.status === "PENDING_APPROVAL" ? `<button class="bass-btn bass-btn--sm btn-decide" data-id="${l.id}" data-d="reject" type="button">반려</button>` : ""}
                ${l.status === "ACTIVE" ? `<button class="bass-btn bass-btn--sm btn-decide" data-id="${l.id}" data-d="suspend" type="button">⛔ 정지</button>` : ""}
                <button class="bass-btn bass-btn--sm btn-limit" data-id="${l.id}" data-limit="${l.room_limit}" type="button">호실 한도</button>
                <span class="bass-badge ${l.app_fee_status === "PAID" ? "bass-badge--ok" : "bass-badge--warn"}">사용료 ${l.app_fee_status}</span>
                ${l.app_fee_status !== "PAID" ? `<span class="bass-muted" style="font-size:12px;">납부 등록은 위 💳 가맹비 청구에서</span>` : ""}
              </div>
            </div>
          </div>
        `).join("")}
      </div>
    `;

    loadFeeBox().catch(Bass.showError);
    document.getElementById("btnAddLessor").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const branch = document.getElementById("nlBranch").value.trim();
        const bldg = document.getElementById("nlBldg").value.trim();
        const name = document.getElementById("nlName").value.trim();
        if (!branch || !bldg || !name) {
          Bass.updateAppAlert("warning", "지점 코드, 건물명, 임대인 성명은 필수 항목입니다.");
          return;
        }
        const res = await Bass.api(APP_ID, "/lessors", {
          method: "POST",
          body: {
            username: branch,
            branch,
            building_name: bldg,
            name,
            phone: document.getElementById("nlPhone").value.trim(),
            address: document.getElementById("nlAddr").value.trim(),
            app_fee: Number(document.getElementById("nlFee").value || 33000),
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });

    elTabContent.querySelectorAll(".btn-decide").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const d = btn.dataset.d;
          let note = "";
          if (d !== "approve") {
            note = (window.prompt(d === "reject" ? "반려 사유 (임대인에게 표시됩니다)" : "정지 사유 (임대인에게 표시됩니다)") || "").trim();
            if (!note) { Bass.updateAppAlert("warning", "사유를 입력해야 합니다."); return; }
          }
          const cur = (ov.lessors || []).find((x) => x.id === btn.dataset.id) || {};
          const app = cur.application || {};
          let force = false;
          if (d === "approve" && cur.status === "PENDING_APPROVAL" && !(app.steps && app.steps.submitted && app.ready_to_submit)) {
            note = (window.prompt("가맹 신청 서류가 완료되지 않았습니다. 그래도 승인하려면 사유를 입력하세요 (기록으로 남습니다).") || "").trim();
            if (!note) { Bass.updateAppAlert("warning", "사유 없이는 서류 미비 상태로 승인할 수 없습니다."); return; }
            force = true;
          }
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/decision`, { method: "POST", body: { decision: d, note, force } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-copy-num").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(btn.dataset.num); Bass.updateAppAlert("success", "발급확인번호를 복사했습니다. 인터넷등기소 입력칸에 붙여 넣으세요."); }
        catch (_) { Bass.updateAppAlert("warning", "복사하지 못했습니다. 번호를 직접 선택해 복사해 주세요."); }
      });
    });

    elTabContent.querySelectorAll(".btn-open-iros").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.num && navigator.clipboard) navigator.clipboard.writeText(btn.dataset.num).catch(() => {});
        window.open("https://www.iros.go.kr/", "_blank", "noopener");
      });
    });

    elTabContent.querySelectorAll(".btn-view-reg-att").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const tk = Bass.token();
          const resp = await fetch(`${Bass.apiBase(APP_ID)}/lessors/${encodeURIComponent(btn.dataset.id)}/registry-attachment`, {
            headers: tk ? { Authorization: "Bearer " + tk } : {},
          });
          if (!resp.ok) throw new Error(`등기부 원본을 불러오지 못했습니다 (${resp.status})`);
          const blobUrl = URL.createObjectURL(await resp.blob());
          window.open(blobUrl, "_blank", "noopener");
          setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        });
      });
    });

    elTabContent.querySelectorAll(".btn-reg-verify").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const num = (window.prompt("인터넷등기소에서 조회한 발급확인번호를 입력하세요.", btn.dataset.num || "") || "").trim();
          if (!num) return;
          const same = window.confirm("조회된 원본이 제출 서류와 같습니까? (소유자·소재지·근저당·발급일시)\n\n확인 = 같음 / 취소 = 다름");
          const note = (window.prompt(same ? "대조 메모 (예: 소유자·소재지·발급일시 모두 일치)" : "다른 점을 적어 주세요") || "").trim();
          if (!note) { Bass.updateAppAlert("warning", "메모를 남겨야 기록됩니다."); return; }
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/registry-verify`, { method: "POST", body: { verified: same, issue_number: num, note } });
          Bass.updateAppAlert(same ? "success" : "warning", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-copy-biz").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(btn.dataset.num); Bass.updateAppAlert("success", "사업자등록번호를 복사했습니다. 국세청 홈택스 조회창에 붙여 넣으세요."); }
        catch (_) { Bass.updateAppAlert("warning", "복사하지 못했습니다. 번호를 직접 복사해 주세요."); }
      });
    });

    elTabContent.querySelectorAll(".btn-open-hometax").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.num && navigator.clipboard) navigator.clipboard.writeText(btn.dataset.num).catch(() => {});
        window.open("https://www.hometax.go.kr/", "_blank", "noopener");
      });
    });

    elTabContent.querySelectorAll(".btn-view-biz-att").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const tk = Bass.token();
          const resp = await fetch(`${Bass.apiBase(APP_ID)}/lessors/${encodeURIComponent(btn.dataset.id)}/bizreg-attachment`, {
            headers: tk ? { Authorization: "Bearer " + tk } : {},
          });
          if (!resp.ok) throw new Error(`사업자등록증 원본을 불러오지 못했습니다 (${resp.status})`);
          const blobUrl = URL.createObjectURL(await resp.blob());
          window.open(blobUrl, "_blank", "noopener");
          setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        });
      });
    });

    elTabContent.querySelectorAll(".btn-biz-verify").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const num = (window.prompt("국세청 홈택스에서 확인한 사업자등록번호(10자리)를 입력하세요.", btn.dataset.num || "") || "").trim();
          if (!num) return;
          const same = window.confirm("국세청 홈택스 조회 결과 정상 '계속사업자'이며 대표자 성명이 일치합니까?\n\n확인 = 승인(장부 반영) / 취소 = 반려");
          const hometaxStatus = same ? ((window.prompt("홈택스 사업자 상태 (예: 계속사업자(면세), 계속사업자(일반))", "계속사업자") || "").trim() || "계속사업자") : "반려";
          const note = (window.prompt(same ? "홈택스 확인 메모 (예: 계속사업자·대표자 성명 일치 확인)" : "반려 사유를 적어 주세요 (예: 휴·폐업 또는 대표자 불일치)") || "").trim();
          if (!note) { Bass.updateAppAlert("warning", "확인 메모(또는 반려 사유)를 남겨야 기록됩니다."); return; }
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/bizreg-verify`, {
            method: "POST",
            body: { verified: same, business_number: num, hometax_status: hometaxStatus, note },
          });
          Bass.updateAppAlert(same ? "success" : "warning", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-limit").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const v = Number(window.prompt("가맹점당 등록 가능 호실 수", btn.dataset.limit) || 0);
          if (!v || v < 1) return;
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/room-limit`, { method: "POST", body: { room_limit: v } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-settle-fee").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/settle-fee`, { method: "POST" });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });
  }

  function renderPortfolioTab() {
    const branches = ov.branches || [];
    const rtms = ov.rtms_reports || [];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🏛️ 전 지점 건물별 가동률 · 총수입 · 공용경비 · NOI 비교</h3>
        ${branches.map((b) => `
          <div class="item-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <strong>${Bass.escapeHtml(b.building_name)} (<code>${Bass.escapeHtml(b.branch)}</code>)</strong>
              <span class="bass-badge bass-badge--info">가동률 ${b.occupancy_rate}% (${b.occupied_rooms}/${b.total_rooms}실) · Cap Rate ${b.cap_rate_pct}%</span>
            </div>
            <div class="bass-muted" style="font-size:13px;margin-top:4px;">
              총수입 ${fmtWon(b.gross_revenue)} − 공용경비 ${fmtWon(b.total_operating_expense)} = <strong>순이익(NOI) ${fmtWon(b.noi)}</strong> (부가세 예수금 ${fmtWon(b.vat_collected)})
            </div>
          </div>
        `).join("")}
      </div>

      <div class="bass-card">
        <h3>🛡️ 국토교통부 전월세 신고제(RTMS) 30일 마감 & 대법원 확정일자 관제</h3>
        ${rtms.map((r) => `
          <div class="item-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <div>
                <strong>[${Bass.escapeHtml(r.branch)} · ${Bass.escapeHtml(r.room_no)}호]</strong> 임대인 ${Bass.escapeHtml(r.lessor_name)} ↔ 임차인 ${Bass.escapeHtml(r.tenant_name)}
                <div class="bass-muted" style="font-size:12.5px;">보증금 ${fmtWon(r.deposit)} / 월세 ${fmtWon(r.monthly_rent)} · 체결일 ${Bass.escapeHtml(r.contract_date)} · 신고기한 ${Bass.escapeHtml(r.deadline_date)}</div>
              </div>
              ${r.status === "FILED"
                ? `<span class="bass-badge bass-badge--ok">확정일자 ${Bass.escapeHtml(r.fixed_date_no || "신고완료")} (FILED)</span>`
                : `<span class="bass-badge bass-badge--warn">⚠️ 미신고 D-${r.days_left}일 · 30일내 신고필수 (PENDING)</span>`
              }
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  // ───────── 운영 안전: 백업 · 입주 안내 링크 · 임시 비밀번호 ─────────
  const fmtKb = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`);
  async function downloadBackup(name) {
    const r = await fetch(`/api/rent3_admin/backups/${encodeURIComponent(name)}/download`, { headers: { Authorization: `Bearer ${Bass.token()}` } });
    if (!r.ok) { Bass.updateAppAlert("error", "내려받지 못했습니다."); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  async function renderOpsTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    const [bk, tn, ls] = await Promise.all([Bass.api(APP_ID, "/backups"), Bass.api(APP_ID, "/accounts/tenants"), Bass.api(APP_ID, "/accounts/lessors")]);
    const rows = bk.backups.map((b) => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">
      <span><code>${Bass.escapeHtml(b.name)}</code><br><span class="bass-muted" style="font-size:12px;">${Bass.escapeHtml(b.created_at.replace("T", " "))} · ${fmtKb(b.size)}</span></span>
      <button class="bass-btn bass-btn--sm btn-bk-dl" data-name="${Bass.escapeHtml(b.name)}" type="button" style="min-height:44px;">내려받기</button></div>`).join("") || '<div class="bass-muted" style="font-size:13px;">아직 백업이 없습니다.</div>';
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🛟 Data Pool 백업</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">풀 전체(JSON·감사 로그·업로드 파일)를 하루 한 번 자동으로 압축해 최근 ${bk.keep}개를 남깁니다${bk.daily ? "" : " (지금은 자동 실행이 꺼져 있습니다)"}.
          <strong>같은 디스크에만 두면 디스크 고장 때 함께 사라집니다 — 가끔 내려받아 다른 곳에 보관하세요.</strong> 백업에는 개인정보가 들어 있습니다.</div>
        <button id="btnBkRun" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">지금 백업 만들기</button>
        <div style="margin-top:8px;">${rows}</div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;" id="mfaCard">
        <h3>🔐 내 계정 2단계 인증 (인증 앱)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">비밀번호가 유출돼도 <strong>내 폰의 인증 앱 코드가 없으면 로그인할 수 없게</strong> 합니다(Google Authenticator·Microsoft Authenticator·Authy 등, 문자 요금 없음). 켜면 복구 코드 8개를 <strong>한 번만</strong> 보여 주니 안전한 곳에 보관하세요.</div>
        <div id="mfaBody" class="bass-muted">불러오는 중...</div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📈 임대인 유입 현황 (채널별 가입 → 승인 → 첫 방 → 첫 수납)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">홍보 링크의 <code>?ref=채널&amp;campaign=캠페인</code> 값으로 어느 채널에서 가입했는지 셉니다. 방문 기록·연락처는 저장하지 않고, 임대인 가입 때 짧은 표식만 남깁니다. 표식 없이 가입한 임대인은 '(출처 없음)'입니다.</div>
        <button id="btnAcq" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">📈 현황 보기</button>
        <div id="acqResult" style="margin-top:10px;overflow-x:auto;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🖨️ 오프라인 QR 리플릿 (인천 시범)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">구·군을 고르면 그 지역 표식(<code>ref=offline-incheon-…</code>)이 든 QR 리플릿(A4)을 엽니다. 인쇄하거나 PDF로 저장해 열쇠점·도배점 등 <strong>허락받은 곳에 비치</strong>하세요. 우편함 투입은 건물 관리 규정을 확인하고, 이름·연락처를 수집하지 않습니다. 효과는 위 '유입 현황'에서 지역별 가입·승인 수로 봅니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <select id="lfRegion" class="bass-select" style="width:auto;min-height:44px;">
            ${[["incheon","인천 전체"],["incheon-namdong","인천 남동구"],["incheon-bupyeong","인천 부평구"],["incheon-michuhol","인천 미추홀구"],["incheon-yeonsu","인천 연수구"],["incheon-gyeyang","인천 계양구"],["incheon-seo","인천 서구"],["incheon-jung","인천 중구"],["incheon-dong","인천 동구"]].map((x) => `<option value="${x[0]}">${x[1]}</option>`).join("")}
          </select>
          <button id="btnLf" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🖨️ 리플릿 열기</button>
        </div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📣 임대인 대상 홍보 글 (카페·밴드·블로그)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">운영자가 직접 올리는 글 초안입니다. 맨 앞 <strong>[광고] 표시</strong>를 지우거나 후기처럼 바꾸지 마세요. 요금은 실제 조건에서 읽어 옵니다. 올리기 전 아래 규정 확인 목록을 지켜 주세요.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <select id="lpChannel" class="bass-select" style="width:auto;min-height:44px;"><option value="cafe">☕ 네이버 카페</option><option value="band">👥 네이버 밴드</option><option value="blog">📝 네이버 블로그</option></select>
          <select id="lpSeason" class="bass-select" style="width:auto;min-height:44px;"><option value="none">상시</option><option value="vat_jan">1월 부가세철</option><option value="tax_may">5월 종소세철</option><option value="vat_jul">7월 부가세철</option></select>
          <button id="btnLp" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✍️ 글 만들기</button>
        </div>
        <div id="lpResult" style="margin-top:10px;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📜 기존 입주자 등기부 확인 완료 처리</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">입주 전 등기부 확인은 <strong>공동 전자계약으로 새로 체결한 계약</strong>에서 임차인이 합니다. 전자계약이 아닌 <strong>기존 입주자</strong>는 이미 살고 있어 다시 할 대상이 아니므로 '확인 완료(이전 입주자)'로 기록합니다. 출입 정보 잠금·수납·계약 조건은 바꾸지 않습니다. [미리보기]는 아무것도 바꾸지 않고, [백업 후 기록]은 자동 백업 뒤에 진행합니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;"><button id="btnLgPrev" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 미리보기</button><button id="btnLgApply" class="bass-btn" type="button" style="min-height:44px;" disabled>📜 백업 후 기록</button></div>
        <div id="lgResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🧹 방의 샘플 값 정리 (가짜 등기부·옵션)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">예전에는 모든 방에 <strong>샘플 등기부(소유자 '김임대'·권리 깨끗함)·가전 옵션 5개·상태 설명</strong>이 자동으로 들어갔습니다. 실제 정보가 아닌데 공개 매물에 '등기클린'으로 나올 수 있어, <strong>샘플과 정확히 같은 값만</strong> 비웁니다(직접 고친 값·실제 등기부 값은 그대로). [미리보기]는 아무것도 바꾸지 않고, [백업 후 정리]는 자동 백업 뒤에 진행합니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;"><button id="btnRdPrev" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 미리보기</button><button id="btnRdApply" class="bass-btn" type="button" style="min-height:44px;" disabled>🧹 백업 후 정리</button></div>
        <div id="rdResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🧮 이관 '이전 입금 합산' 건 달별 분해</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">장부 이관 때 여러 달의 입금을 한 건으로 합쳐 넣은 납부를 <strong>달마다 한 건</strong>으로 풀어, 달별 옮기기·무효 처리가 가능하게 합니다. 합계는 바뀌지 않습니다. <strong>[미리보기]는 아무것도 바꾸지 않고</strong>, [백업 후 반영]은 풀 전체를 <strong>자동 백업</strong>한 뒤 진행하며 계약마다 입금·미납·선수금 합계가 다르면 그 건만 즉시 되돌립니다. 원본은 지우지 않고 보관합니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <button id="btnCarryPrev" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 미리보기</button>
          <button id="btnCarryApply" class="bass-btn" type="button" style="min-height:44px;" disabled>🧮 백업 후 반영</button>
        </div>
        <div id="carryResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🔎 Data Pool 중복 점검</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">엑셀·장부를 여러 번 읽어 들였거나 같은 입금이 두 번 기록됐는지 점검합니다. <strong>점검만으로는 아무것도 바뀌지 않습니다.</strong> 내용이 완전히 같은 납부만 [중복 정리]로 지울 수 있고(가장 먼저 만든 한 건을 남김), 지우기 전에 자동 백업합니다.</div>
        <button id="btnDupScan" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 중복 점검</button>
        <div id="dupResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🏢 임대인 암호 설정 · 6개월 변경 권유</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">전화로 본인 확인을 마친 임대인에게 <strong>1회용 비밀번호 설정 링크</strong>(7일 유효, 4칸 대조 필수)를 보내거나, 6개월(180일)이 지난 임대인에게 <strong>새 암호 설정 권유 채팅·링크</strong>를 보냅니다(새 비밀번호를 정하기 전까지 기존 비밀번호와 등록 기기는 유지됩니다).</div>
        <select id="opsLessor" class="bass-select" style="width:100%;min-height:44px;margin-bottom:8px;">
          ${(ls.lessors || []).map((l) => `<option value="${Bass.escapeHtml(l.id)}">${Bass.escapeHtml(l.building_name || l.branch)} · ${Bass.escapeHtml(l.name)}${l.activated ? " (가입됨" + (l.days_since_change != null ? ` · ${l.days_since_change}일 경과` : "") + ")" : " (미설정)"}${l.due_6m ? " · ⚠️ 6개월 경과 권유 대상" : ""}${l.invite_pending ? " · 링크 대기" : ""}</option>`).join("")}
        </select>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnLessorInvite" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔑 임대인 암호 설정 링크 발급</button>
          <button id="btnLessorRemind" class="bass-btn" type="button" style="min-height:44px;">🔒 6개월 새 암호 설정 권유 발송</button>
        </div>
        <div id="opsLessorResult" style="margin-top:10px;font-size:13px;word-break:break-all;"></div>
      </div>
      <div class="bass-card">
        <h3>🔑 입주 임차인 계정</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">가져온 임차인은 쓸 수 있는 비밀번호가 없습니다. <strong>입주 안내 링크</strong>(7일·1회)를 만들어 전해 주거나, 임대인이 호실 화면의 [입주 안내 카드]로 직접 만들 수도 있습니다. 링크를 못 쓰는 사정이면 <strong>임시 비밀번호</strong>를 발급하세요(본인에게만 전달).</div>
        <select id="opsTenant" class="bass-select" style="width:100%;min-height:44px;margin-bottom:8px;">
          ${tn.tenants.map((t) => `<option value="${Bass.escapeHtml(t.id)}">${Bass.escapeHtml(t.branch)} ${Bass.escapeHtml(t.room_no)}호 ${Bass.escapeHtml(t.name)}${t.invite ? " · 안내 링크 대기" : ""}${t.devices ? ` · 바로가기 ${t.devices}대` : ""}</option>`).join("")}
        </select>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnOpsInvite" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">입주 안내 링크 만들기</button>
          <button id="btnOpsTemp" class="bass-btn" type="button" style="min-height:44px;">임시 비밀번호 발급</button>
          <button id="btnOpsRevoke" class="bass-btn" type="button" style="min-height:44px;">📵 분실 폰 바로가기 전체 해제</button>
          <button id="btnOpsResetAct" class="bass-btn" type="button" style="min-height:44px;">↩ 가입 상태 초기화 (재시험)</button></div>
        <div id="opsResult" style="margin-top:10px;font-size:13px;word-break:break-all;"></div>
      </div>
      <div class="bass-card" style="margin-top:14px;">
        <h3>🧪 시험 임차인 등록</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">실사용 확인용으로 호실 하나와 <strong>0원 계약</strong> 임차인을 등록합니다(공개 매물에는 나오지 않음). 먼저 <strong>점검</strong>으로 확인한 뒤 <strong>등록</strong>하세요 — 등록 전에 풀 전체를 자동 백업하고, 같은 호실·임차인이 있으면 아무것도 만들지 않습니다.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <input id="ttBranch" class="bass-input" value="main" placeholder="지점 코드 (예: main)" aria-label="지점 코드" style="min-height:44px;">
          <input id="ttRoom" class="bass-input" value="701" placeholder="호실" aria-label="호실" inputmode="numeric" style="min-height:44px;">
          <input id="ttName" class="bass-input" value="민흥식" placeholder="성함" aria-label="성함" style="min-height:44px;">
          <input id="ttPhone" class="bass-input" value="010-3269-3343" placeholder="휴대폰" aria-label="휴대폰" inputmode="tel" style="min-height:44px;">
        </div>
        <label style="display:block;min-height:44px;padding:10px 0;font-size:13.5px;"><input id="ttRaise" type="checkbox"> 호실 한도가 모자라면 한 칸 올리기</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnTtCheck" class="bass-btn" type="button" style="min-height:44px;">🔎 점검(변경 없음)</button>
          <button id="btnTtApply" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✅ 등록</button></div>
        <div id="ttResult" style="margin-top:10px;font-size:13px;line-height:1.6;"></div>
      </div>
      <div class="bass-card" style="margin-top:14px;">
        <h3>🧪 시험 예비임차인 본인인증 처리</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">가짜 번호라 문자 인증번호를 받을 수 없는 <strong>시험용 예비임차인</strong>(포털에서 가입만 한 계정)을 본인인증 완료로 표시합니다. 입주 중인 임차인·이미 인증된 계정은 바꾸지 않고, 기록에는 <strong>시험용 인증</strong>으로 남습니다. 먼저 <strong>점검</strong>으로 확인한 뒤 <strong>처리</strong>하세요 — 처리 전에 풀 전체를 자동 백업합니다.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <input id="tiName" class="bass-input" value="홍길동" placeholder="성함" aria-label="성함" style="min-height:44px;">
          <input id="tiPhone" class="bass-input" value="010-1111-2222" placeholder="휴대폰" aria-label="휴대폰" inputmode="tel" style="min-height:44px;">
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
          <button id="btnTiCheck" class="bass-btn" type="button" style="min-height:44px;">🔎 점검(변경 없음)</button>
          <button id="btnTiApply" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✅ 본인인증 처리</button></div>
        <div id="tiResult" style="margin-top:10px;font-size:13px;line-height:1.6;"></div>
      </div>`;
    const ttRun = (apply) => async function () {
      Bass.withLock(this, async () => {
        const body = { branch: document.getElementById("ttBranch").value.trim(), room_no: document.getElementById("ttRoom").value.trim(),
          name: document.getElementById("ttName").value.trim(), phone: document.getElementById("ttPhone").value.trim(), apply, raise_limit: document.getElementById("ttRaise").checked };
        if (apply && !window.confirm(`${body.room_no}호 ${body.name}님(0원 계약)을 등록합니다. 등록 전에 자동 백업합니다. 계속할까요?`)) return;
        const r = await Bass.api(APP_ID, "/accounts/test-tenant", { method: "POST", body });
        const color = r.status === "BLOCKED" ? "var(--warn)" : "var(--ok)";
        document.getElementById("ttResult").innerHTML = `<span style="color:${color};font-weight:800;">${{ DRY: "점검 결과", EXISTS: "이미 있음", BLOCKED: "멈춤", DONE: "등록 완료" }[r.status] || ""}</span><br>${Bass.escapeHtml(r.head || "")}<br>${Bass.escapeHtml(r.message)}${r.status === "DONE" ? "<br><strong>서버는 등록한 풀을 바로 사용합니다.</strong> 이제 포털 '연결 요청'에서 이 정보로 접수할 수 있습니다." : ""}`;
        if (r.status === "DONE") Bass.updateAppAlert("success", r.message);
      });
    };
    document.getElementById("btnTtCheck").addEventListener("click", ttRun(false));
    document.getElementById("btnTtApply").addEventListener("click", ttRun(true));
    const tiRun = (apply) => async function () {
      Bass.withLock(this, async () => {
        const body = { name: document.getElementById("tiName").value.trim(), phone: document.getElementById("tiPhone").value.trim(), apply };
        if (apply && !window.confirm(`${body.name}님(${body.phone})을 시험용 본인인증 완료로 처리합니다. 처리 전에 자동 백업합니다. 계속할까요?`)) return;
        const r = await Bass.api(APP_ID, "/accounts/test-identity", { method: "POST", body });
        const color = r.status === "BLOCKED" ? "var(--warn)" : "var(--ok)";
        document.getElementById("tiResult").innerHTML = `<span style="color:${color};font-weight:800;">${{ DRY: "점검 결과", EXISTS: "이미 인증됨", BLOCKED: "멈춤", DONE: "처리 완료" }[r.status] || ""}</span><br>${Bass.escapeHtml(r.message)}`;
        if (r.status === "DONE") Bass.updateAppAlert("success", r.message);
      });
    };
    document.getElementById("btnTiCheck").addEventListener("click", tiRun(false));
    document.getElementById("btnTiApply").addEventListener("click", tiRun(true));
    document.getElementById("btnBkRun").addEventListener("click", function () {
      Bass.withLock(this, async () => { const r = await Bass.api(APP_ID, "/backups/run", { method: "POST", body: {} }); Bass.updateAppAlert(r.verify.ok ? "success" : "warning", r.message); await renderOpsTab(); });
    });
    elTabContent.querySelectorAll(".btn-bk-dl").forEach((b) => b.addEventListener("click", () => downloadBackup(b.dataset.name)));
    async function loadMfa() {
      const E = Bass.escapeHtml, box = document.getElementById("mfaBody");
      if (!box) return;
      let st;
      try { st = await Bass.api(APP_ID, "/mfa/status"); } catch (e) { box.innerHTML = `<span class="bass-muted">${E(e.message || "")}</span>`; return; }
      if (st.enabled) {
        box.innerHTML = `<div style="font-size:13.5px;line-height:1.8;">🟢 <strong>켜짐</strong> (${E(String(st.enabled_at || "").slice(0, 10))}) · 남은 복구 코드 ${st.recovery_left}개${st.this_session_verified ? "" : " · ⚠️ 이 로그인은 2단계 인증을 거치지 않았습니다(다시 로그인하세요)"}</div>
          <div class="bass-row" style="gap:8px;margin-top:8px;flex-wrap:wrap;"><input id="mfaOffCode" class="bass-input" inputmode="numeric" placeholder="인증 앱 6자리 또는 복구 코드" style="max-width:240px;min-height:44px;"><button id="mfaOff" class="bass-btn" type="button" style="min-height:44px;">끄기</button></div>`;
        document.getElementById("mfaOff").addEventListener("click", function () {
          if (!window.confirm("2단계 인증을 끄면 비밀번호만으로 로그인됩니다. 끌까요?")) return;
          Bass.withLock(this, async () => {
            const r = await Bass.api(APP_ID, "/mfa/disable", { method: "POST", body: { code: document.getElementById("mfaOffCode").value } });
            Bass.updateAppAlert("success", r.message); await loadMfa();
          });
        });
      } else {
        box.innerHTML = `<div style="font-size:13.5px;">⚪ <strong>꺼짐</strong>${st.pending ? " (설정하던 중)" : ""}</div><button id="mfaStart" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;margin-top:8px;">🔐 설정 시작</button><div id="mfaSetup" style="margin-top:10px;"></div>`;
        document.getElementById("mfaStart").addEventListener("click", function () {
          Bass.withLock(this, async () => {
            const r = await Bass.api(APP_ID, "/mfa/enroll/start", { method: "POST", body: {} });
            const sp = document.getElementById("mfaSetup");
            sp.innerHTML = `<ol style="margin:0 0 8px 18px;padding:0;font-size:13px;line-height:1.8;"><li>폰에서 인증 앱을 열고 <strong>QR을 스캔</strong>하거나 <strong>키를 직접 입력</strong>합니다.</li><li>앱에 나온 6자리 숫자를 아래에 입력하고 [확인하고 켜기]를 누릅니다.</li></ol>
              <div id="mfaQr" style="background:#fff;padding:8px;display:inline-block;border-radius:8px;"></div>
              <div style="margin:6px 0;font-size:12.5px;">키: <code style="user-select:all;word-break:break-all;">${E(r.secret)}</code></div>
              <div class="bass-row" style="gap:8px;flex-wrap:wrap;"><input id="mfaCode" class="bass-input" inputmode="numeric" maxlength="8" placeholder="6자리 숫자" style="max-width:160px;min-height:44px;"><button id="mfaOn" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">확인하고 켜기</button></div>`;
            try { if (window.QRCode) new window.QRCode(document.getElementById("mfaQr"), { text: r.otpauth_uri, width: 180, height: 180 }); } catch (_) { document.getElementById("mfaQr").remove(); }
            document.getElementById("mfaOn").addEventListener("click", function () {
              Bass.withLock(this, async () => {
                const c = await Bass.api(APP_ID, "/mfa/enroll/confirm", { method: "POST", body: { code: document.getElementById("mfaCode").value } });
                if (c.token) Bass.setToken(c.token);              // 지금 세션이 끊기지 않게 2단계를 마친 토큰으로 교체
                sp.innerHTML = `<div style="border:2px solid var(--warn);border-radius:10px;padding:10px;"><strong>🔑 복구 코드 (지금 한 번만 보입니다 — 안전한 곳에 보관)</strong><div style="font-family:monospace;font-size:15px;line-height:1.9;margin:6px 0;user-select:all;">${c.recovery_codes.map(E).join("<br>")}</div>
                  <button id="mfaCopy" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">📋 복사</button></div>`;
                document.getElementById("mfaCopy").addEventListener("click", () => { try { navigator.clipboard.writeText(c.recovery_codes.join("\n")); Bass.updateAppAlert("success", "복구 코드를 복사했습니다."); } catch (_) { Bass.updateAppAlert("warning", "복사하지 못했습니다. 직접 선택해 복사하세요."); } });
                Bass.updateAppAlert("success", c.message);
              });
            });
          });
        });
      }
    }
    loadMfa();
    document.getElementById("btnLf").addEventListener("click", () => {
      const sel = document.getElementById("lfRegion");
      const label = sel.options[sel.selectedIndex].text;
      window.open(`/rent3/leaflet.html?region=${encodeURIComponent(sel.value)}&label=${encodeURIComponent(label)}&campaign=leaflet1`, "_blank", "noopener");
    });
    document.getElementById("btnLp").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const E = Bass.escapeHtml;
        const r = await Bass.api(APP_ID, `/landlord-promo?channel=${document.getElementById("lpChannel").value}&season=${document.getElementById("lpSeason").value}`);
        const p = r.post;
        document.getElementById("lpResult").innerHTML = `
          <div style="font-weight:700;margin-bottom:4px;">제목</div><input id="lpTitle" class="bass-input" style="width:100%;min-height:44px;" value="${E(p.title)}">
          <div style="font-weight:700;margin:8px 0 4px;">본문 (직접 고칠 수 있어요)</div><textarea id="lpBody" class="bass-input" rows="16" style="width:100%;font-size:13px;line-height:1.6;">${E(p.content)}</textarea>
          ${p.tags ? `<div class="bass-muted" style="font-size:12.5px;">태그: ${p.tags.map((x) => "#" + E(x)).join(" ")}</div>` : ""}
          <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">유입 추적 링크: <code>${E(p.link)}</code></div>
          <div class="bass-row" style="gap:8px;margin-top:8px;"><button id="lpCopy" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">📋 제목+본문 복사</button></div>
          <div style="margin-top:10px;font-weight:700;">올리기 전 확인</div>
          <ul style="margin:4px 0 0 18px;font-size:12.5px;line-height:1.7;">${p.checklist.map((x) => `<li>${E(x)}</li>`).join("")}</ul>`;
        document.getElementById("lpCopy").addEventListener("click", async () => {
          await R3.copyText(document.getElementById("lpTitle").value + "\n\n" + document.getElementById("lpBody").value);
          Bass.updateAppAlert("success", "복사했습니다. 광고 표시는 지우지 말고 올려 주세요.");
        });
      });
    });
    document.getElementById("btnAcq").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/acquisition/summary");
        const E = Bass.escapeHtml;
        const tbl = (title, rows) => `<div style="font-weight:700;margin:10px 0 4px;">${title}</div><table class="bass-table" style="width:100%;font-size:12.5px;"><thead><tr><th>구분</th><th>가입</th><th>가맹 승인</th><th>첫 방 등록</th><th>첫 수납</th></tr></thead><tbody>${rows.map((x) => `<tr><td>${E(x.key)}</td><td>${x.signups}</td><td>${x.approved}</td><td>${x.with_room}</td><td>${x.with_payment}</td></tr>`).join("") || `<tr><td colspan="5">자료 없음</td></tr>`}</tbody></table>`;
        document.getElementById("acqResult").innerHTML = `전체 임대인 ${r.total_lessors}명 중 유입 표식이 있는 가입 <strong>${r.tracked}</strong>명` + tbl("채널(ref)별", r.by_ref) + tbl("캠페인별", r.by_campaign) + (r.by_from.length ? tbl("임차인 소개 코드별(상위 20)", r.by_from) : "") + `<div class="bass-muted" style="font-size:12.5px;margin-top:6px;">임차인이 '집주인에게 제안'을 보냈다고 기록한 횟수: ${r.invites_sent}회</div>`;
      });
    });
    document.getElementById("btnLgPrev").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const out = document.getElementById("lgResult"), ap = document.getElementById("btnLgApply");
        const r = await Bass.api(APP_ID, "/legacy-registry/preview");
        out.innerHTML = `계약 ${r.contracts_total}건 중 기록 대상 <strong>${r.to_mark}</strong>건(전자계약이 아닌 진행 중 계약)`;
        ap.disabled = r.to_mark === 0;
        ap.onclick = function () {
          if (!window.confirm(`${r.to_mark}건의 기존 입주자에게 '입주 전 등기부 확인 완료(이전 입주자)'를 기록합니다. 풀 전체를 자동 백업한 뒤 진행하며 출입 잠금·수납은 바꾸지 않습니다. 계속할까요?`)) return;
          Bass.withLock(ap, async () => {
            const res = await Bass.api(APP_ID, "/legacy-registry/apply", { method: "POST", body: { confirm: true } });
            Bass.updateAppAlert("success", res.message); out.innerHTML = `<strong>${Bass.escapeHtml(res.message)}</strong>`; ap.disabled = true;
          });
        };
      });
    });
    document.getElementById("btnRdPrev").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const out = document.getElementById("rdResult"), ap = document.getElementById("btnRdApply");
        const r = await Bass.api(APP_ID, "/room-defaults/preview");
        out.innerHTML = `호실 ${r.rooms_total}개 중 정리 대상 <strong>${r.rooms_to_clean}</strong>개 — 샘플 등기부 ${r.counts.registry} · 가전 옵션 ${r.counts.options} · 상태 설명 ${r.counts.status}${r.public_now ? ` · <span style="color:var(--err);font-weight:700;">현재 공개 매물 ${r.public_now}개 포함</span>` : ""}
          ${r.counts.door ? `<div class="bass-muted" style="font-size:12.5px;">도어락 기본값('2580*')인 방 ${r.counts.door}개는 실제로 쓰고 있을 수 있어 자동으로 바꾸지 않습니다(임대인이 직접 확인).</div>` : ""}`;
        ap.disabled = r.rooms_to_clean === 0;
        ap.onclick = function () {
          if (!window.confirm(`${r.rooms_to_clean}개 방의 샘플 값(등기부·옵션·상태 설명)을 비웁니다. 풀 전체를 자동 백업한 뒤 진행하며, 직접 고친 값은 건드리지 않습니다. 계속할까요?`)) return;
          Bass.withLock(ap, async () => {
            const res = await Bass.api(APP_ID, "/room-defaults/apply", { method: "POST", body: { confirm: true } });
            Bass.updateAppAlert("success", res.message); out.innerHTML = `<strong>${Bass.escapeHtml(res.message)}</strong>`; ap.disabled = true;
          });
        };
      });
    });
    document.getElementById("btnCarryPrev").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const E = Bass.escapeHtml, out = document.getElementById("carryResult");
        const r = await Bass.api(APP_ID, "/carryover-split/preview");
        out.innerHTML = `<div>이월 합산 건 <strong>${r.total}</strong>개 — 분해 가능 <strong>${r.ok}</strong>개(새 납부 건 ${r.new_records}개 예정) · 검토 필요 <strong style="color:${r.review.length ? "var(--err)" : "var(--ok)"};">${r.review.length}</strong>개<br>
          금액 합계: 원본 ${Number(r.sum_ok_amount).toLocaleString("ko-KR")}원 → 분해 후 ${Number(r.sum_planned_amount).toLocaleString("ko-KR")}원 ${r.sum_ok_amount === r.sum_planned_amount ? "✅ 일치" : "❌ 불일치"}</div>
          ${(r.rows || []).slice(0, 12).map((x) => `<div class="bass-muted" style="font-size:12.5px;">${E(x.branch)} ${E(x.room_no)}호 ${E(x.tenant)} · ${Number(x.total).toLocaleString("ko-KR")}원 → ${x.months}개월 (${E(x.from)}~${E(x.to)}) ${x.status !== "OK" ? `<strong style="color:var(--err);">검토 필요: ${E(x.note)}</strong>` : ""}</div>`).join("")}
          ${r.rows.length > 12 ? `<div class="bass-muted" style="font-size:12.5px;">… 외 ${r.rows.length - 12}건</div>` : ""}`;
        const ap = document.getElementById("btnCarryApply");
        ap.disabled = !(r.ok > 0 && r.sum_ok_amount === r.sum_planned_amount);
        ap.onclick = function () {
          if (!window.confirm(`이월 합산 ${r.ok}건을 달별 ${r.new_records}개 건으로 분해합니다.\n풀 전체를 자동 백업한 뒤 진행하고, 계약마다 합계가 다르면 그 건만 되돌립니다.\n원본은 무효로 보관됩니다.\n\n계속할까요?`)) return;
          Bass.withLock(ap, async () => {
            const res = await Bass.api(APP_ID, "/carryover-split/apply", { method: "POST", body: { confirm: true } });
            Bass.updateAppAlert(res.identical_totals && !(res.rolled_back || []).length ? "success" : "warning", res.message);
            out.innerHTML = `<strong>${E(res.message)}</strong>`;
            ap.disabled = true;
          });
        };
      });
    });
    document.getElementById("btnDupScan").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const E = Bass.escapeHtml, out = document.getElementById("dupResult");
        const r = await Bass.api(APP_ID, "/pool-duplicates");
        const ex = r.exact_duplicate_groups || [];
        const line = (p) => `${E(p.branch)} ${E(p.room_no)}호 ${E(p.billing_month)} · ${Number(p.amount).toLocaleString("ko-KR")}원 · 납부일 ${E(p.paid_date)} · <code>${E(p.id)}</code>`;
        out.innerHTML = `<div>납부 ${r.payments_total}건 점검 — <strong style="color:${r.removable_count ? "var(--err)" : "var(--ok)"};">내용이 같은 중복 ${r.removable_count}건</strong> · 같은 달 여러 건(참고) ${(r.same_month_multiple || []).length}건 · 같은 호실 계약 중복 ${(r.duplicate_contracts || []).length}건 · 임차인 중복 ${(r.duplicate_tenants || []).length}건 · 호실 중복 ${(r.duplicate_rooms || []).length}건</div>
          ${ex.slice(0, 20).map((g) => `<div style="padding:6px 0;border-bottom:1px solid var(--border);">남김: ${line(g.keep)}<br>${g.duplicates.map((d) => `<span style="color:var(--err);">지움 대상: ${line(d)}</span>`).join("<br>")}</div>`).join("")}
          ${(r.same_month_multiple || []).slice(0, 10).map((m) => `<div class="bass-muted" style="padding:4px 0;">참고(나눠 낸 정상 납부일 수 있음) ${E(m.contract)} ${E(m.billing_month)}: ${m.payments.map((p) => `${Number(p.amount).toLocaleString("ko-KR")}원(${E(p.paid_date)})`).join(" + ")}</div>`).join("")}
          ${r.removable_count ? `<button id="btnDupRemove" class="bass-btn" type="button" style="min-height:44px;margin-top:8px;">🧹 중복 ${r.removable_count}건 정리 (자동 백업 후)</button>` : ""}`;
        const rm = document.getElementById("btnDupRemove");
        if (rm) rm.addEventListener("click", function () {
          if (!window.confirm(`내용이 완전히 같은 중복 납부 ${r.removable_count}건을 지웁니다(가장 먼저 만든 건은 남김). 지우기 전에 풀 전체를 백업합니다. 계속할까요?`)) return;
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, "/pool-duplicates/remove", { method: "POST", body: { apply: true } });
            Bass.updateAppAlert("success", res.message);
            out.innerHTML = `<strong>${E(res.message)}</strong>`;
          });
        });
      });
    });
    const lsrOut = document.getElementById("opsLessorResult");
    document.getElementById("btnLessorInvite").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/lessor-invite", { method: "POST", body: { id: document.getElementById("opsLessor").value } });
        const link = window.location.origin + r.url;
        lsrOut.innerHTML = `<strong>${Bass.escapeHtml(r.lessor_name)}님 (${Bass.escapeHtml(r.building_name)})</strong> — 아래 링크를 전달해 주세요(7일·1회).<br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
      });
    });
    document.getElementById("btnLessorRemind").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const lid = document.getElementById("opsLessor").value;
        const r = await Bass.api(APP_ID, `/accounts/lessors/${encodeURIComponent(lid)}/remind-password`, { method: "POST", body: {} });
        const link = window.location.origin + r.url;
        lsrOut.innerHTML = `<strong>${Bass.escapeHtml(r.message)}</strong><br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
        Bass.updateAppAlert("success", r.message);
      });
    });
    const out = document.getElementById("opsResult");
    document.getElementById("btnOpsInvite").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/invite", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        const link = window.location.origin + r.url;
        out.innerHTML = `<strong>${Bass.escapeHtml(r.tenant_name)}님 (${Bass.escapeHtml(r.room_no)}호)</strong> — 아래 링크를 문자로 보내거나 QR 로 보여 주세요. 지금만 보입니다.<br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
      });
    });
    document.getElementById("btnOpsRevoke").addEventListener("click", function () {
      if (!window.confirm("이 임차인의 간편 비밀번호와 바로가기 기기를 모두 해제합니다. 계속할까요?")) return;
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/revoke-shortcuts", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        out.textContent = r.message;
        await renderOpsTab();
      });
    });
    document.getElementById("btnOpsResetAct").addEventListener("click", function () {
      const label = document.getElementById("opsTenant").selectedOptions[0].textContent.trim();
      if (!window.confirm(`[${label}]\n비밀번호·간편 비밀번호·바로가기·입주 안내 링크·동의 기록·연결 요청을 모두 지우고 '가입 전' 처음 상태로 되돌립니다. (호실·계약·납부 내역은 그대로)\n연결·가입 과정을 다시 시험할 때 쓰세요. 계속할까요?`)) return;
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/reset-activation", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        out.innerHTML = `${Bass.escapeHtml(r.message)}<br><span class="bass-muted">${Bass.escapeHtml(r.hint || "")}</span>`;
        await renderOpsTab();
      });
    });
    document.getElementById("btnOpsTemp").addEventListener("click", function () {
      if (!window.confirm("임시 비밀번호를 발급하면 기존 비밀번호는 못 씁니다. 계속할까요?")) return;
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/reset-password", { method: "POST", body: { kind: "tenant", id: document.getElementById("opsTenant").value } });
        out.innerHTML = `${Bass.escapeHtml(r.message)}<br>임시 비밀번호: <code style="font-size:16px;">${Bass.escapeHtml(r.temporary_password)}</code>`;
      });
    });
  }

  async function renderPoolTab() {
    const events = ov.recent_events || [];

    // 원문 보기·검색·수정은 '공동 JSON Data Pool 허브' 한 화면으로 합쳤다(파일 카드 선택 → 검색 → 원문/JSON 수정)
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📂 공동 JSON Data Pool — 원문 보기 · 검색 · 수정</h3>
        <div class="bass-muted" style="font-size:13px;line-height:1.7;margin:6px 0 10px;">파일별 현황 카드를 눌러 원문을 보고, 글자로 검색하고, 찾은 건을 JSON 으로 고칠 수 있습니다(수정은 사유 필수·전후 기록). 다른 앱에 로그인하지 않아도 됩니다.</div>
        <a class="bass-btn bass-btn--primary" href="/rent3/pool.html" style="min-height:46px;display:inline-flex;align-items:center;">📂 Data Pool 열기</a>
      </div>

      <div class="bass-card">
        <h3>⚡ 실시간 객체 업데이트 감사 스트림 (<code>_events.jsonl</code> 최근 20건)</h3>
        ${events.slice(0, 20).map((e) => `
          <div style="padding:7px 8px;border-bottom:1px solid var(--border);font-size:12.5px;">
            <code>${Bass.escapeHtml(e.at)}</code> · <span class="bass-badge">${Bass.escapeHtml(e.app_source)}</span>
            <strong>${Bass.escapeHtml(e.collection)}.json</strong> [${Bass.escapeHtml(e.action)}] — ${Bass.escapeHtml(e.summary)}
          </div>
        `).join("")}
      </div>
    `;
  }

  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === currentTab));
  if (window.R3Notify) R3Notify.start({ url: "/api/rent3_admin/notifications" });     // 🔔 알림 센터: 문의·가맹 심사·홈택스 확인·가맹비 연체·세금계산서 발행
  refresh().catch(Bass.showError);
})();

