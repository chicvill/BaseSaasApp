/* apps/rent3_admin/frontend/support.js — 관리인 채팅 · 원격 지원 · 대리 접속 탭 */
(function () {
  "use strict";
  const A = window.R3A;
  const { APP_ID, elTabContent } = A;
  const S = A.state;
  const sp = S.sp;

  // 건물 → 호실 → 입장. 사용자가 원격 지원을 허용했고 지원 채팅(또는 전화 접수)이 있어야 입장하며, 입장하면 보기 전용이다.
  const spFmt = (iso) => (String(iso || "").length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");

  function rooms_sessions(d) {
    return (d.rooms || []).flatMap((r) => (r.sessions || []).map((x) => ({ ...x, room_no: r.room_no })));
  }

  async function renderSupportTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    // 알림창의 '최근 대화'에서 들어오면(?user=lessor:..) 그 사용자의 건물·호실을 고르고 채팅을 바로 연다(한 번만)
    const qUser = new URLSearchParams(location.search).get("user");
    if (qUser && S.currentTab === "support") {
      try {
        const t0 = await Bass.api(APP_ID, `/support/thread?user=${encodeURIComponent(qUser)}`);
        const u0 = t0.user || {};
        sp.branch = u0.branch || sp.branch;
        sp.roomNo = u0.room_no || "";
        sp.user = qUser;
      } catch (_) { /* 사용자를 못 찾으면 평소처럼 목록부터 */ }
      try { const u = new URL(location.href); u.searchParams.delete("user"); history.replaceState(history.state, "", u.toString()); } catch (_) { /* 주소 정리 실패는 무시 */ }
    }
    let d = await Bass.api(APP_ID, "/support/directory" + (sp.branch ? `?branch=${encodeURIComponent(sp.branch)}` : ""));
    if (!sp.branch && d.branches.length) {
      sp.branch = d.branches[0].branch;
      d = await Bass.api(APP_ID, `/support/directory?branch=${encodeURIComponent(sp.branch)}`);
    }
    if (S.currentTab !== "support") return;
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
    const renderSpRows = (arr) => arr.map((m) => m.sender_role === "system"
      ? `<div class="cm-sys">${Bass.escapeHtml(m.body)} <span>${Bass.escapeHtml(spFmt(m.created_at))}</span></div>`
      : `<div class="cm-b ${m.sender_role === "admin" ? "is-mine" : ""}"><div><div class="cm-meta">${Bass.escapeHtml(spFmt(m.created_at))} · ${m.sender_role === "user" ? Bass.escapeHtml(card.name) : "관리인"}</div>${Bass.escapeHtml(m.body)}</div></div>`).join("") || '<div class="bass-muted" style="font-size:13px;">대화가 없습니다.</div>';
    box.innerHTML = `
      <div class="bass-card">
        <h3 style="margin:0 0 8px;">💬 ${Bass.escapeHtml(card.title)} · 지원 채팅</h3>
        <div id="spList" class="cm-list" style="max-height:280px;">${renderSpRows(msgs)}</div>
        <div class="r3-chat-compose"><textarea id="spMsg" class="bass-input" rows="2" placeholder="사용자에게 보낼 글"></textarea><button id="btnSpSend" class="bass-btn bass-btn--sm bass-btn--primary" type="button">보내기</button></div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">대화는 ${t.retention_days}일 뒤 삭제됩니다. 접속 기록(사유 분류 포함)은 1년 보관됩니다.</div>

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
    clearInterval(S.spTimer);
    S.spTimer = setInterval(async () => {
      if (S.currentTab !== "support" || sp.user !== card.user || !document.getElementById("spList")) { clearInterval(S.spTimer); return; }
      if (document.visibilityState === "hidden") return;
      try {
        const nt = await Bass.api(APP_ID, `/support/thread?user=${encodeURIComponent(card.user)}`);
        const nm = nt.messages || [];
        if (nt.user && Boolean(nt.user.support_allowed) !== Boolean(card.support_allowed)) {      // 사용자가 허용을 켜거나 끄면 바로 화면(허용 표시·입장 단추)에 반영
          clearInterval(S.spTimer);
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

  Object.assign(A, { renderSupportTab, renderSupportThread });
})();
