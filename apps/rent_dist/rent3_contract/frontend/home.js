/* apps/rent_dist/rent3_contract/frontend/home.js — 계약 홈 현황, 본인인증 카드, 호출·매물 선택 다이얼로그. */
(function () {
  "use strict";
  const C = window.R3C;
  if (!C) return;

  const { APP_ID, loggedUser, currentRole, myTenantId, myBranch, ROLE_KO, OTHER_KO, state: S, dom: D, esc, fmtWon } = C;

  function sessionTag(s) {
    if (s.call_status === "CALLING") {
      return s.initiated_by_role === currentRole ? `⏳ ${OTHER_KO} 응답 대기` : "🔔 응답 필요";
    }
    return `단계 ${s.current_step}/5`;
  }

  function renderHome(force) {
    if (!D.elContract.hidden) return;
    const sig = JSON.stringify([
      C.mySessions().map((s) => [s.id, s.call_status, s.current_step, s.status]),
      S.candidateRooms.map((r) => `${r.room_no}${r.is_occupied ? 1 : 0}${r.is_published ? 1 : 0}`).join(),
      S.myhome ? S.myhome.pool_revision : 0,
      S.lessorDash ? S.lessorDash.pool_revision : 0,
      S.myIdentity ? !!S.myIdentity.verified : null,
    ]);
    if (!force && sig === S.homeSig) return;
    if (D.elDlgHost.firstChild) return; // 입력 중인 창을 지우지 않는다
    S.homeSig = sig;

    const incoming = C.incomingCalls();
    const ongoing = C.ongoingSessions();
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
    html += `<div style="text-align:center;margin-top:14px;"><a class="bass-btn bass-btn--sm" href="verify.html">🔍 계약서 진위 확인</a></div>`;
    D.elHomeBody.innerHTML = html;

    D.elHomeBody.querySelectorAll(".js-respond").forEach((b) =>
      b.addEventListener("click", function () { respondToIncomingCall(this, this.dataset.sid); }));
    D.elHomeBody.querySelectorAll(".js-open").forEach((b) =>
      b.addEventListener("click", () => C.openSession(b.dataset.sid)));
    D.elHomeBody.querySelectorAll(".js-lessor-call").forEach((b) =>
      b.addEventListener("click", () => openLessorCallDialog(b.dataset.room)));
    const tBtn = document.getElementById("btnTenantContract");
    if (tBtn) tBtn.addEventListener("click", () => openTenantPickerDialog());
    bindIdentityCard();
  }

  function bindIdentityCard() {
    const startBtn = document.getElementById("btnIdStart");
    if (!startBtn) return;
    const body = () => ({
      name: document.getElementById("idName").value.trim(),
      phone: document.getElementById("idPhone").value.trim(),
      birth: document.getElementById("idBirth").value.trim(),
    });
    startBtn.addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/identity/start", { method: "POST", body: body() });
        Bass.updateAppAlert("success", r.message, r.hint || "");
      });
    });
    document.getElementById("btnIdConfirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/identity/confirm", { method: "POST", body: { ...body(), code: document.getElementById("idCode").value.trim() } });
        Bass.updateAppAlert("success", r.message);
        await C.loadIdentity();
        renderHome(true);
      });
    });
  }

  function homeLessorHtml() {
    const rooms = S.candidateRooms.filter((r) => !myBranch || r.branch === myBranch);
    const unpaidByRoom = {};
    if (S.lessorDash && Array.isArray(S.lessorDash.rooms)) {
      S.lessorDash.rooms.forEach((r) => { unpaidByRoom[r.room_no] = r.ledger ? Number(r.ledger.total_unpaid || 0) : 0; });
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
    if (S.myhome) {
      const lg = S.myhome.ledger || { months: [], total_unpaid: 0 };
      const r = S.myhome.room || {};
      pay = `<div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;margin-bottom:8px;">
          <h3 style="margin:0;">🏠 ${esc((S.myhome.building && S.myhome.building.building_name) || "")} ${esc(r.room_no || "")}호 납부 현황</h3>
          <span class="bass-badge ${lg.total_unpaid > 0 ? "bass-badge--err" : "bass-badge--ok"}">${lg.total_unpaid > 0 ? `미납 ${fmtWon(lg.total_unpaid)}` : "완납"}</span>
        </div>
        ${(lg.months || []).slice(-6).map((m) => `
          <div class="pay-row"><span><strong>${esc(m.ym)}</strong> · ${fmtWon(m.due)}</span>
          <span style="color:${m.unpaid > 0 ? "var(--err)" : "var(--ok)"};font-weight:700;">${esc(m.note)}</span></div>`).join("")}
      </div>`;
    }
    const incoming = C.incomingCalls();
    const ongoing = C.ongoingSessions();
    const label = incoming.length ? "🔔 임대인 요청 확인하기"
      : ongoing.length ? "▶ 진행 중인 계약 이어서 하기"
      : "📝 임대계약";
    const needId = S.myIdentity && !S.myIdentity.verified;
    const idDone = S.myIdentity && S.myIdentity.verified
      ? `<div class="bass-muted" style="font-size:12px;margin-bottom:8px;">✅ 본인인증 완료 — ${esc(S.myIdentity.name)}${S.myIdentity.fake ? " <span class='bass-badge bass-badge--warn'>시연용 가짜 인증</span>" : ""}</div>` : "";
    const idCard = needId ? `<div class="bass-card" style="margin-bottom:14px;border-left:4px solid var(--warn);">
        <h3 style="margin:0 0 4px;">🔐 휴대폰 본인인증이 필요합니다</h3>
        <div class="bass-muted" style="font-size:12.5px;margin-bottom:8px;line-height:1.55;">명의 도용 사기를 막기 위해, 계약을 시작·응답·서명하기 전에 <strong>가입한 본인 명의 휴대폰</strong>으로 인증해야 합니다.</div>
        <input id="idName" class="bass-input" placeholder="실명" style="margin-bottom:6px;">
        <input id="idPhone" class="bass-input" placeholder="가입한 휴대폰 010-0000-0000" style="margin-bottom:6px;">
        <input id="idBirth" class="bass-input" placeholder="생년월일 6자리(900101) 또는 8자리" style="margin-bottom:6px;">
        <div class="bass-row" style="gap:6px;">
          <button id="btnIdStart" class="bass-btn bass-btn--sm" type="button">인증번호 받기</button>
          <input id="idCode" class="bass-input" placeholder="인증번호" style="flex:1;min-width:0;">
          <button id="btnIdConfirm" class="bass-btn bass-btn--sm bass-btn--primary" type="button">인증 확인</button>
        </div>
      </div>` : "";
    return `${idCard}${pay}
      <div class="bass-card">
        <h3 style="margin:0 0 6px;">새 집 계약</h3>
        ${idDone}
        <div class="bass-muted" style="font-size:12.5px;margin-bottom:12px;">공개된 매물을 고르면 임대인에게 계약 요청이 전달됩니다.</div>
        <button id="btnTenantContract" class="bass-btn bass-btn--primary big-cta" type="button" ${needId ? "disabled" : ""}>${needId ? "🔒 본인인증 후 계약할 수 있어요" : label}</button>
      </div>`;
  }

  // ───────────────────────── 다이얼로그 ─────────────────────────
  function openDialog(innerHtml) {
    D.elDlgHost.innerHTML = `<div class="dlg-backdrop"><div class="dlg">${innerHtml}</div></div>`;
    const bd = D.elDlgHost.firstChild;
    bd.addEventListener("click", (e) => { if (e.target === bd) closeDialog(); });
    return bd;
  }
  function closeDialog() {
    D.elDlgHost.innerHTML = "";
    renderHome(true);
  }

  function findRoom(val) {
    const [br, no] = String(val).split("|");
    return S.candidateRooms.find((x) => x.branch === br && x.room_no === no);
  }

  function openLessorCallDialog(val) {
    const room = findRoom(val);
    if (!room) return;
    const renewal = room.is_occupied;
    const prospects = S.participantTenants.filter((t) => t.status === "PROSPECT");
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
      const t = S.participantTenants.find((x) => x.id === sel.value);
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
        D.elDlgHost.innerHTML = "";
        await afterCall(res);
      }, "요청 보내는 중... ⏳");
    });
  }

  function openTenantPickerDialog() {
    const incoming = C.incomingCalls();
    if (incoming.length) { C.openSession(incoming[0].id); return; }
    const ongoing = C.ongoingSessions();
    if (ongoing.length) { C.openSession(ongoing[0].id); return; }
    // 공실 + 입주 중이지만 전출 예정으로 공개된 '사전 매물'(퇴실 전에 미리 계약해 전입일·전출일을 맞춘다). 이미 예약이 걸린 호실은 뺀다.
    const listings = S.candidateRooms.filter((r) => r.is_published && !r.has_reservation && (!r.is_occupied || r.pre_moveout_notice));
    const bd = openDialog(`
      <h3 style="margin:0 0 4px;">계약할 집 고르기</h3>
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:10px;">임대인이 공개한 빈 집입니다. 입주 중이어도 퇴실이 예정된 집은 <strong>퇴실 전에 미리 계약</strong>할 수 있습니다.</div>
      ${listings.length ? listings.map((r) => {
        const pdfUrl = r.terms_pdf_url || `/api/rent3/pool/listings/${encodeURIComponent(r.id)}/terms.pdf`;
        const imgUrl = r.image || "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80";
        return `
        <div class="room-row" style="align-items:flex-start;">
          <div class="js-ct-detail" data-room-id="${esc(r.id)}" role="button" tabindex="0" style="position:relative;flex:0 0 92px;width:92px;height:76px;border-radius:10px;overflow:hidden;cursor:pointer;background:#f1f5f9;">
            <img src="${esc(imgUrl)}" alt="${esc(r.building_name)} ${esc(r.room_no)}호" style="width:100%;height:100%;object-fit:cover;display:block;">
            <a href="${esc(pdfUrl)}" class="js-ct-detail" data-room-id="${esc(r.id)}" style="position:absolute;left:3px;right:3px;bottom:3px;background:rgba(15,23,42,.88);color:#fff;font-size:12px;font-weight:700;padding:2px 4px;border-radius:6px;text-align:center;text-decoration:none;">📄 PDF</a>
          </div>
          <div class="room-row__main" style="flex:1;">
            <div class="room-row__title">${esc(r.building_name)} ${esc(r.room_no)}호${r.is_occupied ? ' <span class="bass-badge bass-badge--warn" style="font-size:12px;">📅 퇴실 전 미리 계약</span>' : ""}</div>
            ${r.is_occupied ? `<div class="bass-muted" style="font-size:12px;">현재 입주 중 · ${r.expected_vacate_date ? `<strong>${esc(r.expected_vacate_date)}</strong> 이후 입주` : "퇴실 확정 후 입주"} (전입일과 전출일은 같은 날까지 가능)</div>` : ""}
            <div class="room-row__meta bass-muted">보증금 ${fmtWon(r.deposit)} · 월 ${fmtWon(r.monthly_rent)} · 관리비 ${fmtWon(r.maintenance_fee)}<br>${r.floor}${r.total_floors ? `/${r.total_floors}` : ""}층 · 전용 ${r.area_sqm}㎡${r.direction ? ` · ${esc(r.direction)}` : ""} · 입주 ${esc(r.available_from || "즉시")}</div>
          </div>
          <button class="bass-btn bass-btn--primary bass-btn--sm js-pick" data-room="${esc(r.branch)}|${esc(r.room_no)}" type="button">이 집으로</button>
        </div>`;
      }).join("") : `<div class="bass-muted" style="padding:10px 0;">지금 공개된 매물이 없습니다.</div>`}
      <button id="dlgCancel" class="bass-btn" style="width:100%;margin-top:8px;" type="button">닫기</button>`);
    bd.querySelector("#dlgCancel").addEventListener("click", closeDialog);
    const startCallForRoom = async (room) => {
      const res = await Bass.api(APP_ID, "/sessions/call", {
        method: "POST",
        body: { caller_role: "tenant", branch: room.branch, room_no: room.room_no, mode: "NEW",
                tenant_name: loggedUser.name || "", tenant_phone: loggedUser.phone || "", id_tenant: myTenantId },
      });
      D.elDlgHost.innerHTML = "";
      await afterCall(res);
    };
    bd.querySelectorAll(".js-ct-detail").forEach((el) => el.addEventListener("click", (e) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
      e.preventDefault();
      e.stopPropagation();
      const room = listings.find((x) => x.id === el.dataset.roomId);
      if (room && window.R3ListingModal) {
        window.R3ListingModal.open(room, {
          rentNowLabel: "🤝 이 집으로 계약 요청",
          onRentNow: (pickedRoom) => { startCallForRoom(pickedRoom).catch(Bass.showError); },
        });
      }
    }));
    bd.querySelectorAll(".js-pick").forEach((b) => b.addEventListener("click", function () {
      const room = findRoom(this.dataset.room);
      Bass.withLock(this, () => startCallForRoom(room), "요청 보내는 중... ⏳");
    }));
  }

  async function afterCall(res) {
    Bass.updateAppAlert("success", res.message);
    const list = await Bass.api(APP_ID, "/sessions");
    S.allSessions = list.sessions || [];
    await C.openSession(res.session.id);
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
      await C.openSession(res.session.id);
    }, "응답 연결 중... ⏳");
  }

  Object.assign(C, {
    sessionTag, renderHome, bindIdentityCard, homeLessorHtml, homeTenantHtml,
    openDialog, closeDialog, findRoom, openLessorCallDialog, openTenantPickerDialog,
    afterCall, respondToIncomingCall,
  });
})();
