/* apps/rent3_admin/frontend/app.js — 플랫폼 총괄 관리인 관제 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_admin";
  let currentTab = "support";
  let selectedCollection = "contracts";
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

  // 가맹 신청 요약: 본인인증·건물·등기부 자동 대조 결과와 제출된 등기부 원본 링크
  function appSummaryHtml(l) {
    const a = l.application || {};
    const s = a.steps || {};
    const chk = (a.registry || {}).check;
    const mark = (ok) => (ok ? "✅" : "⬜");
    const col = chk ? ({ ok: "var(--ok)", caution: "var(--warn)", high: "var(--err)" }[chk.level] || "var(--warn)") : "";
    const idn = a.identity || {};
    return `<div style="font-size:12.5px;margin:4px 0;line-height:1.6;">
      ${mark(s.identity)} 본인인증${idn.verified ? ` (${Bass.escapeHtml(idn.name)}${idn.fake ? " · <b>시연용 가짜 인증</b>" : ""})` : ""}
      · ${mark(s.building)} 건물 주소 · ${mark(s.registry)} 등기부 · ${mark(s.submitted)} 제출
      ${s.registry ? ` · <button class="bass-btn bass-btn--sm btn-view-reg-att" data-id="${Bass.escapeHtml(l.id)}" type="button">등기부 원본 보기</button>` : ""}
      ${chk ? `<div style="border-left:4px solid ${col};padding:4px 8px;margin-top:4px;">${chk.auto_pass ? "🟢 자동 대조 일치 (심사 우선)" : "🟠 수동 확인 필요"} — ${Bass.escapeHtml(chk.message)}</div>` : ""}
      ${s.registry ? iirosHtml(l, a) : ""}
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
  const spFmt = (iso) => (String(iso || "").length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");

  async function renderSupportTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
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
        <h3 style="margin:0 0 4px;">🎯 원격 지원 · 대리 접속</h3>
        <p class="bass-muted" style="font-size:12.5px;margin:0 0 10px;">사용자가 <strong>원격 지원을 허용</strong>했고 <strong>지원 채팅</strong>(전화로 받았다면 전화 접수)이 있어야 입장할 수 있습니다. 입장하면 <strong>보기 전용</strong>이며 접속 기록이 남습니다.</p>
        <div class="field-grid">
          <label class="r3-fld" style="display:grid;gap:3px;font-size:12.5px;">① 건물<select id="spBranch" class="bass-select">${d.branches.map((b) => `<option value="${Bass.escapeHtml(b.branch)}" ${b.branch === sp.branch ? "selected" : ""}>${Bass.escapeHtml(b.building_name)} (${Bass.escapeHtml(b.branch)})</option>`).join("")}</select></label>
          <label class="r3-fld" style="display:grid;gap:3px;font-size:12.5px;">② 호실<select id="spRoom" class="bass-select"><option value="">건물 전체 (임대인)</option>${rooms.map((r) => `<option value="${Bass.escapeHtml(r.room_no)}" ${r.room_no === sp.roomNo ? "selected" : ""}>${Bass.escapeHtml(r.room_no)}호 ${r.tenant ? Bass.escapeHtml(r.tenant.name) : "공실"}${r.sessions.length ? ` · 계약방 ${r.sessions.length}` : ""}</option>`).join("")}</select></label>
        </div>
        <div style="margin-top:10px;">${cards.map((c) => `
          <div class="item-card" style="margin-top:8px;">
            <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
              <div><strong>${Bass.escapeHtml(c.title)}</strong> <span class="bass-muted" style="font-size:12px;">(끝자리 ${Bass.escapeHtml(c.phone_tail)})</span><div style="margin-top:4px;">${badge(c)}</div></div>
              <button class="bass-btn bass-btn--sm ${c.user === sp.user ? "bass-btn--primary" : ""} btn-sp-open" data-user="${Bass.escapeHtml(c.user)}" type="button">💬 지원 채팅 / 입장</button>
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
    const can = card.support_allowed && msgs.length > 0;
    const sessions = card.role === "lessor" ? rooms_sessions(d) : (room ? room.sessions : []);
    const why = !card.support_allowed ? "이 사용자는 원격 지원을 허용하지 않았습니다. 전화로 방법을 안내하거나, 사용자가 메뉴에서 허용을 켜도록 안내해 주세요."
      : (!msgs.length ? "지원 채팅이 없습니다. 아래 ‘전화 접수’를 남기거나 사용자에게 먼저 글을 보내 주세요." : "");
    box.innerHTML = `
      <div class="bass-card">
        <h3 style="margin:0 0 6px;">💬 ${Bass.escapeHtml(card.title)} · 지원 채팅</h3>
        <div id="spList" style="max-height:260px;overflow-y:auto;border:1px solid var(--border);border-radius:10px;padding:6px 8px;">${msgs.map((m) => `
          <div style="margin:5px 0;font-size:13px;"><span class="bass-muted" style="font-size:11px;">${Bass.escapeHtml(spFmt(m.created_at))} · ${m.sender_role === "user" ? Bass.escapeHtml(card.name) : (m.sender_role === "admin" ? "관리인" : "안내")}</span><br>${Bass.escapeHtml(m.body)}</div>`).join("") || '<div class="bass-muted" style="font-size:13px;">대화가 없습니다.</div>'}</div>
        <div class="bass-row" style="gap:6px;margin-top:8px;"><input id="spMsg" class="bass-input" style="flex:1;" placeholder="사용자에게 보낼 글"><button id="btnSpSend" class="bass-btn bass-btn--sm bass-btn--primary" type="button">보내기</button></div>
        <div class="bass-muted" style="font-size:12px;margin-top:4px;">대화는 ${t.retention_days}일 뒤 삭제됩니다. 접속 기록(사유 분류 포함)은 1년 보관됩니다.</div>

        <div style="margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);">
          <strong style="font-size:13.5px;">📞 전화 접수 (전화로 요청받은 경우)</strong>
          <div class="bass-row" style="gap:6px;margin-top:6px;flex-wrap:wrap;">
            <select id="spCat" class="bass-select" style="width:auto;">${d.categories.map((c) => `<option ${c === sp.cat ? "selected" : ""}>${Bass.escapeHtml(c)}</option>`).join("")}</select>
            <input id="spMemo" class="bass-input" style="flex:1;min-width:160px;" placeholder="접수 메모 (예: 수납 입력 방법을 모르심)">
            <button id="btnSpPhone" class="bass-btn bass-btn--sm" type="button">📞 전화 접수 남기기</button>
          </div>
          <div class="bass-muted" style="font-size:12px;margin-top:4px;">남기면 사용자 지원 채팅에 “[전화 요청 접수] …”가 자동으로 생기고, 바로 입장할 수 있습니다.</div>
        </div>

        <div style="margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);">
          <strong style="font-size:13.5px;">🛡️ 입장 (보기 전용)</strong>
          ${why ? `<div style="font-size:12.5px;color:var(--warn);margin:6px 0;">${Bass.escapeHtml(why)}</div>` : ""}
          <div class="bass-row" style="gap:6px;margin-top:6px;flex-wrap:wrap;">
            ${card.role === "lessor" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-sp-enter" data-view="lessor" type="button" ${can ? "" : "disabled"}>🏢 임대인 입장</button>` : `<button class="bass-btn bass-btn--sm bass-btn--primary btn-sp-enter" data-view="tenant" type="button" ${can ? "" : "disabled"}>🏠 임차인 입장</button>`}
            ${sessions.map((x) => `<button class="bass-btn bass-btn--sm btn-sp-enter" data-view="contract" data-sid="${Bass.escapeHtml(x.id)}" type="button" ${can ? "" : "disabled"}>🤝 계약방 ${Bass.escapeHtml(x.room_no || "")}호 (${card.role === "lessor" ? "임대인" : "임차인"} 시점 · ${x.step}단계)</button>`).join("")}
          </div>
        </div>
      </div>`;
    const list = document.getElementById("spList");
    list.scrollTop = list.scrollHeight;
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
    document.getElementById("btnSpPhone").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/support/phone-intake", { method: "POST", body: { user: card.user, category: document.getElementById("spCat").value, memo: document.getElementById("spMemo").value } });
        Bass.updateAppAlert("success", res.message);
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

  function renderLessorsTab() {
    const lessors = ov.lessors || [];
    elTabContent.innerHTML = `
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
                ${l.app_fee_status !== "PAID" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-settle-fee" data-id="${l.id}" type="button">💰 사용료 완납 정산</button>` : ""}
              </div>
            </div>
          </div>
        `).join("")}
      </div>
    `;

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
    const [bk, tn] = await Promise.all([Bass.api(APP_ID, "/backups"), Bass.api(APP_ID, "/accounts/tenants")]);
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
      <div class="bass-card">
        <h3>🔑 입주 임차인 계정</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">가져온 임차인은 쓸 수 있는 비밀번호가 없습니다. <strong>입주 안내 링크</strong>(7일·1회)를 만들어 전해 주거나, 임대인이 호실 화면의 [입주 안내 카드]로 직접 만들 수도 있습니다. 링크를 못 쓰는 사정이면 <strong>임시 비밀번호</strong>를 발급하세요(본인에게만 전달).</div>
        <select id="opsTenant" class="bass-select" style="width:100%;min-height:44px;margin-bottom:8px;">
          ${tn.tenants.map((t) => `<option value="${Bass.escapeHtml(t.id)}">${Bass.escapeHtml(t.branch)} ${Bass.escapeHtml(t.room_no)}호 ${Bass.escapeHtml(t.name)}${t.invite ? " · 안내 링크 대기" : ""}${t.devices ? ` · 바로가기 ${t.devices}대` : ""}</option>`).join("")}
        </select>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnOpsInvite" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">입주 안내 링크 만들기</button>
          <button id="btnOpsTemp" class="bass-btn" type="button" style="min-height:44px;">임시 비밀번호 발급</button></div>
        <div id="opsResult" style="margin-top:10px;font-size:13px;word-break:break-all;"></div>
      </div>`;
    document.getElementById("btnBkRun").addEventListener("click", function () {
      Bass.withLock(this, async () => { const r = await Bass.api(APP_ID, "/backups/run", { method: "POST", body: {} }); Bass.updateAppAlert(r.verify.ok ? "success" : "warning", r.message); await renderOpsTab(); });
    });
    elTabContent.querySelectorAll(".btn-bk-dl").forEach((b) => b.addEventListener("click", () => downloadBackup(b.dataset.name)));
    const out = document.getElementById("opsResult");
    document.getElementById("btnOpsInvite").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/invite", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        const link = window.location.origin + r.url;
        out.innerHTML = `<strong>${Bass.escapeHtml(r.tenant_name)}님 (${Bass.escapeHtml(r.room_no)}호)</strong> — 아래 링크를 문자로 보내거나 QR 로 보여 주세요. 지금만 보입니다.<br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
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
    const res = await Bass.api(APP_ID, `/pool/${encodeURIComponent(selectedCollection)}`);
    const cols = res.collections || [];
    const events = ov.recent_events || [];

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;">
          <div>
            <h3>📂 공동 실시간 JSON Data Pool 원문 탐색기 (<code>data/rent3_pool/${Bass.escapeHtml(res.file)}</code>)</h3>
            <div class="bass-muted" style="font-size:12.5px;">파일 크기: ${res.size_bytes.toLocaleString()} bytes · 리비전 v${res.revision} · 객체 ${res.count}건 · 최종 갱신: ${Bass.escapeHtml(res.updated_at)}</div>
          </div>
          <select id="colSelect" class="bass-select" style="width:auto;">
            ${cols.map((c) => `<option value="${c}" ${c === selectedCollection ? "selected" : ""}>📄 ${c}.json</option>`).join("")}
          </select>
        </div>
        <pre class="json-view">${Bass.escapeHtml(JSON.stringify(res.items, null, 2))}</pre>
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

    document.getElementById("colSelect").addEventListener("change", (ev) => {
      selectedCollection = ev.target.value;
      renderPoolTab().catch(Bass.showError);
    });
  }

  refresh().catch(Bass.showError);
})();

