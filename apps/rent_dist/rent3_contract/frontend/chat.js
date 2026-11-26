/* apps/rent_dist/rent3_contract/frontend/chat.js — 채팅 중심 계약 워크스페이스, 차례 안내, 조건 일괄·개별 제안. */
(function () {
  "use strict";
  const C = window.R3C;
  if (!C) return;

  const {
    APP_ID, adminProxy, currentRole, OTHER_KO, state: S, dom: D,
    esc, digits, acctLabel, fmtWon, FIELD_LABEL, fmtField, hhmm, STATUS_KO,
  } = C;

  function stepProgress(s, step) {
    switch (step) {
      case 1: return {
        lessor: s.lessor_checked_tenant_id && s.step1_lessor_confirmed,
        tenant: s.tenant_checked_lessor_id && s.step1_tenant_locked,
        label: "신분증 확인 · 등기부 확인",
      };
      case 2: return { lessor: s.step2_lessor_confirmed, tenant: s.step2_tenant_locked, label: "옵션·시설 점검" };
      case 3: return { lessor: s.step3_lessor_agreed, tenant: s.step3_tenant_agreed, label: "계약 조건 동의" };
      case 4: return { lessor: !!s.lessor_signature, tenant: !!s.tenant_signature, label: "전자서명" };
      default: return { lessor: s.status === "COMPLETED", tenant: s.status === "COMPLETED", label: "계약 체결" };
    }
  }

  function blockedMessage(s, step) {
    if (currentRole !== "tenant") return "";
    if (step === 1 && !s.step1_lessor_confirmed) return "임대인이 신분증 확인과 등기부등본 제시를 마치면 이 창이 열립니다.";
    if (step === 2 && !s.step2_lessor_confirmed) return "임대인이 옵션·시설 점검표를 작성하면 이 창이 열립니다.";
    if (step === 5) return "임대인이 계약금 입금을 확인하고 체결하면 계약이 완료됩니다.";
    return "";
  }

  function turnInfo(s) {
    if (s.status === "CANCELLED") return { mine: false, text: "취소된 계약입니다" };
    if (s.status === "COMPLETED") return { mine: false, text: "체결이 완료되었습니다" };
    if (s.call_status === "CALLING") {
      return currentRole !== s.initiated_by_role
        ? { mine: true, text: "지금 내 차례: 호출에 응답하세요" }
        : { mine: false, text: `상대 차례: ${OTHER_KO}의 응답을 기다리는 중` };
    }
    const step = s.current_step;
    const prog = stepProgress(s, step);
    const blocked = blockedMessage(s, step);
    const mine = Boolean(prog[currentRole]);
    const other = Boolean(prog[currentRole === "lessor" ? "tenant" : "lessor"]);
    let info;
    if (!mine && !blocked) info = { mine: true, text: `지금 내 차례: ${prog.label}` };
    else if (blocked) info = { mine: false, text: `상대 차례: ${OTHER_KO}이(가) 먼저 진행해야 합니다` };
    else if (!other) info = { mine: false, text: `상대 차례: ${OTHER_KO}이(가) ${prog.label} 중` };
    else info = { mine: false, text: "다음 단계로 넘어가는 중입니다" };
    if (step === 3 && S.chatSummary.open_count > 0) info.text += ` · 답할 제안·수정 요청 ${S.chatSummary.open_count}건`;
    return info;
  }

  function updateTurnLine() {
    const el = document.getElementById("turnLine");
    if (!el || !S.activeSession) return;
    const t = turnInfo(S.activeSession);
    el.textContent = t.text;
    el.classList.toggle("is-mine", t.mine);
  }

  const chatFab = document.createElement("button");
  chatFab.type = "button";
  chatFab.id = "chatFab";
  chatFab.className = "chat-fab no-print";
  chatFab.setAttribute("aria-label", "계약방 채팅");
  chatFab.hidden = true;
  chatFab.innerHTML = '💬<span class="chat-fab__n" hidden></span><span class="chat-fab__o" hidden></span>';
  document.body.appendChild(chatFab);
  chatFab.addEventListener("click", () => openChatDrawer());

  function updateChatFab() {
    chatFab.hidden = D.elContract.hidden || !S.activeSession || (D.elChatWs && !D.elChatWs.hidden);
    const n = chatFab.querySelector(".chat-fab__n");
    const o = chatFab.querySelector(".chat-fab__o");
    n.hidden = !(S.chatSummary.unread > 0);
    n.textContent = String(S.chatSummary.unread);
    o.hidden = !(S.chatSummary.open_count > 0);
    o.textContent = `!${S.chatSummary.open_count}`;
  }

  function renderChatBubble(m, d) {
    const mine = m.sender_role === currentRole;
    const who = m.sender_role === "system" ? "안내" : `${m.sender_role === "lessor" ? "임대인" : "임차인"}${mine ? "(나)" : ""}`;
    if (m.kind === "system") return `<div class="cm-sys">${esc(m.body)} <span>${hhmm(m.created_at)}</span></div>`;
    if (m.kind === "question") {
      return `<div class="cm-b ${mine ? "is-mine" : ""}"><div><div class="cm-meta">${who} · ${hhmm(m.created_at)} · ${m.step}단계</div>${esc(m.body)}</div></div>`;
    }
    if (m.kind === "bundle") {
      const items = (m.proposal && m.proposal.items) || [];
      const canAct = m.status === "open" && d.can_negotiate && !adminProxy && currentRole;
      const buttons = !canAct ? "" : (mine
        ? `<button class="bass-btn bass-btn--sm cm-act" data-id="${esc(m.id)}" data-act="withdraw" type="button">철회</button>`
        : `<button class="bass-btn bass-btn--sm bass-btn--primary cm-act" data-id="${esc(m.id)}" data-act="accept" data-kind="bundle" type="button">✅ 전체 수락</button>
           <button class="bass-btn bass-btn--sm cm-act" data-id="${esc(m.id)}" data-act="decline" type="button">거절</button>`);
      return `<div class="cm-bundle ${m.status === "open" ? "is-open" : ""}">
        <div class="cm-meta">${who} · ${hhmm(m.created_at)} · 📋 계약 조건 일괄 제시</div>
        <div class="cm-bundle__grid">${items.map((it) => `<div class="cm-bundle__row"><span class="bass-muted">${esc(it.label)}</span><strong>${esc(it.new_text || fmtField(it.field, it.new))}</strong></div>`).join("")}</div>
        <div style="margin-top:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;"><span class="bass-badge ${m.status === "open" ? "bass-badge--warn" : (m.status === "accepted" ? "bass-badge--ok" : "")}">${STATUS_KO[m.status] || m.status}</span>${buttons}</div>
      </div>`;
    }
    const isProp = m.kind === "proposal";
    const head = isProp
      ? `💡 <strong>${esc(m.proposal.label)}</strong> ${esc(fmtField(m.field, m.proposal.old))} → <strong>${esc(fmtField(m.field, m.proposal.new))}</strong> 제안`
      : `✏️ <strong>${esc((d.request_fields && d.request_fields[m.field]) || "수정")}</strong> 수정 요청`;
    const canAct = m.status === "open" && d.can_negotiate && !adminProxy && currentRole;
    const buttons = !canAct ? "" : (mine
      ? `<button class="bass-btn bass-btn--sm cm-act" data-id="${esc(m.id)}" data-act="withdraw" type="button">철회</button>`
      : `<button class="bass-btn bass-btn--sm bass-btn--primary cm-act" data-id="${esc(m.id)}" data-act="accept" data-kind="${m.kind}" type="button">${isProp ? "수락" : "확인·반영"}</button>
         <button class="bass-btn bass-btn--sm cm-act" data-id="${esc(m.id)}" data-act="decline" type="button">거절</button>`);
    return `<div class="cm-card ${m.status === "open" ? "is-open" : ""}">
      <div class="cm-meta">${who} · ${hhmm(m.created_at)} · ${m.step}단계</div>
      <div>${head}</div>${m.body && !(isProp && m.body.includes("제안합니다.")) ? `<div class="bass-muted" style="font-size:12.5px;">${esc(m.body)}</div>` : ""}
      <div style="margin-top:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;"><span class="bass-badge ${m.status === "open" ? "bass-badge--warn" : (m.status === "accepted" ? "bass-badge--ok" : "")}">${STATUS_KO[m.status] || m.status}</span>${buttons}</div>
    </div>`;
  }

  function peerCallHtml(s) {
    const tm = s.terms || {};
    const otherName = currentRole === "lessor" ? (tm.tenant_name || "임차인") : (s.lessor_name || "임대인");
    const otherPhone = currentRole === "lessor" ? (tm.tenant_phone || "") : (s.lessor_phone || "");
    const dg = digits(otherPhone);
    if (adminProxy || dg.length < 9) return "";
    return `<div class="r3-chat-peer"><strong>📞 ${esc(otherName)}에게 전화</strong><a class="bass-btn bass-btn--sm bass-btn--primary" href="tel:${esc(dg)}" style="min-height:40px;">${esc(otherPhone)}</a></div><div class="bass-muted" style="font-size:12px;margin:2px 0 4px;">💬 중요한 통화 내용은 아래 채팅에 기록을 남겨 주세요.</div>`;
  }

  // 체결·종료 후 지난 채팅 열람용 모달(진행 중에는 메인 채팅방 #chatWorkspace 로 바로 포커스)
  async function openChatDrawer() {
    if (!S.activeSession) return;
    if (D.elChatWs && !D.elChatWs.hidden) {
      S.stepModalOpen = false;
      D.elModal.hidden = true;
      await refreshInlineChat(true);
      const inp = document.getElementById("wsChatMsg");
      if (inp) inp.focus();
      return;
    }
    const sid = S.activeSession.id;
    const body = R3.openModal(`💬 계약방 채팅 · ${esc(S.activeSession.building_name)} ${esc(S.activeSession.room_no)}호`, `
      <div id="cmCall" style="margin-bottom:8px;"></div>
      <div id="cmList" class="cm-list"></div>
      <div id="cmNote" class="bass-muted" style="font-size:12px;margin:4px 0;"></div>
      <div id="cmComposer">
        <div class="r3-chat-compose">
          <textarea id="cmMsg" class="bass-input" rows="2" placeholder="궁금한 점을 적어 주세요"></textarea>
          <button id="cmSend" type="button" class="bass-btn bass-btn--sm bass-btn--primary">보내기</button>
        </div>
        <div class="bass-row" style="gap:6px;margin-top:6px;flex-wrap:wrap;">
          <button type="button" class="bass-btn bass-btn--sm cm-quick" data-t="확인했습니다">확인했습니다</button>
          <button type="button" class="bass-btn bass-btn--sm cm-quick" data-t="나중에 얘기해요">나중에 얘기해요</button>
        </div>
      </div>`);
    const list = body.querySelector("#cmList");
    let busy = false;

    const load = async () => {
      if (busy) return;
      const d = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(sid)}/messages?mark_read=true`);
      const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
      list.innerHTML = d.messages.map((m) => renderChatBubble(m, d)).join("") || '<div class="bass-muted" style="font-size:13px;padding:8px 0;">아직 채팅이 없습니다. 궁금한 점을 남겨 보세요.</div>';
      if (atBottom) list.scrollTop = list.scrollHeight;
      body.querySelector("#cmCall").innerHTML = peerCallHtml(S.activeSession);
      const readOnly = d.read_only || adminProxy;
      body.querySelector("#cmComposer").style.display = readOnly ? "none" : "";
      body.querySelector("#cmNote").textContent = d.purged ? "보관 기간이 지나 이 채팅은 삭제되었습니다."
        : d.read_only ? "체결되었거나 취소된 계약방입니다. 지난 채팅은 읽을 수만 있습니다."
        : adminProxy ? "관리인은 읽기만 할 수 있습니다."
        : d.can_negotiate ? "조건 제안·수정 요청은 ③ 계약 조건 화면에서 보냅니다. 서명(④) 이후에는 문의만 가능합니다."
        : "서명(④) 이후에는 문의만 남길 수 있습니다. 조건을 바꾸려면 ‘조건 다시 열기’를 쓰세요.";
      S.chatSummary = { unread: 0, open_count: d.open_count };
      updateChatFab();
    };
    const send = async (text) => {
      const t = (text || "").trim();
      if (!t) { Bass.updateAppAlert("warning", "내용을 입력해 주세요."); return; }
      await Bass.api(APP_ID, `/sessions/${encodeURIComponent(sid)}/messages`, { method: "POST", body: { kind: "question", body: t } });
      body.querySelector("#cmMsg").value = "";
      await load();
    };
    body.querySelector("#cmSend").addEventListener("click", function () { Bass.withLock(this, () => send(body.querySelector("#cmMsg").value)); });
    body.querySelectorAll(".cm-quick").forEach((b) => b.addEventListener("click", function () { Bass.withLock(this, () => send(b.dataset.t)); }));
    list.addEventListener("click", (ev) => {
      const b = ev.target.closest(".cm-act");
      if (!b) return;
      Bass.withLock(b, async () => {
        if (b.dataset.act === "accept" && b.dataset.kind === "proposal" && !window.confirm("수락하면 계약 조건이 바뀌고 양측의 3단계 동의·서명이 초기화됩니다. 수락할까요?")) return;
        busy = true;
        try {
          const res = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(sid)}/messages/${encodeURIComponent(b.dataset.id)}/respond`, { method: "POST", body: { action: b.dataset.act } });
          Bass.updateAppAlert(res.level || "success", res.message);
          if (res.session) { S.activeSession = res.session; S.lastRevision = S.activeSession.revision; }
        } finally { busy = false; }
        await load();
        await C.fetchActiveSession(true);
      });
    });
    await load();
    const timer = setInterval(() => {
      if (!document.getElementById("cmList")) { clearInterval(timer); return; }
      load().catch(() => {});
    }, 3000);
  }

  function openProposeDialog(s, field) {
    const label = FIELD_LABEL[field];
    const cur = (s.terms || {})[field];
    const isDate = field === "move_in_date";
    const body = R3.openModal(`💡 ${label} 제안`, `
      <div style="font-size:14px;margin-bottom:8px;">현재 ${label}: <strong>${esc(fmtField(field, cur))}</strong></div>
      <label class="r3-fld">제안할 값${field === "lease_term_months" ? " (개월)" : isDate ? "" : " (원)"}
        <input id="pfVal" class="bass-input" type="${isDate ? "date" : "number"}"${isDate && s.reserve_after ? ` min="${esc(s.reserve_after)}"` : ""} value="${esc(String(cur ?? ""))}"></label>
      <label class="r3-fld" style="margin-top:8px;">사유 (선택)<textarea id="pfWhy" class="bass-input" rows="2" placeholder="예: 이사 일정 때문에 5일 늦추고 싶습니다"></textarea></label>
      <button id="pfSend" class="bass-btn bass-btn--primary" type="button" style="width:100%;margin-top:10px;">제안 보내기</button>
      <p class="bass-muted" style="font-size:12px;margin:8px 0 0;">상대가 수락하면 이 값으로 바뀌고, 양측의 3단계 동의가 초기화됩니다. 같은 항목에 다시 제안하면 이전 제안은 대체됩니다.</p>`);
    body.querySelector("#pfSend").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/messages`, {
          method: "POST", body: { kind: "proposal", field, new_value: body.querySelector("#pfVal").value, body: body.querySelector("#pfWhy").value.trim() },
        });
        R3.closeModal();
        Bass.updateAppAlert("success", res.message);
        await C.fetchActiveSession(true);
      });
    });
  }

  function openRequestDialog(s) {
    const body = R3.openModal("✏️ 특약 수정 요청", `
      <label class="r3-fld">상대에게 요청할 내용<textarea id="rqText" class="bass-input" rows="4" placeholder="예: 반려동물 관련 문구를 ‘소형견 1마리 허용’으로 바꿔 주세요"></textarea></label>
      <button id="rqSend" class="bass-btn bass-btn--primary" type="button" style="width:100%;margin-top:10px;">요청 보내기</button>
      <p class="bass-muted" style="font-size:12px;margin:8px 0 0;">상대가 확인하고 특약을 직접 고친 뒤 ‘확인·반영’으로 닫습니다. 처리되기 전에는 최종 동의를 할 수 없습니다.</p>`);
    body.querySelector("#rqSend").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/messages`, {
          method: "POST", body: { kind: "change_request", field: "special_terms", body: body.querySelector("#rqText").value.trim() },
        });
        R3.closeModal();
        Bass.updateAppAlert("success", res.message);
        await C.fetchActiveSession(true);
      });
    });
  }

  function openBundleDialog(s) {
    const t = s.terms || {};
    const isRen = s.mode === "RENEWAL";
    const depVal = Number(t.deposit || 0);
    const cdepVal = isRen ? 0 : (t.contract_deposit != null ? Number(t.contract_deposit) : Math.floor(depVal / 10));
    const body = R3.openModal("📋 계약 조건 수정 · 일괄 반영", `
      <p class="bass-muted" style="font-size:13px;margin:0 0 10px;">보증금·계약금(보증금의 10%)·월세·관리비·청소비·입주일·계약기간을 한 번에 조정해 채팅방에 올립니다. 상대가 채팅방에서 확인하면 한꺼번에 반영됩니다.</p>
      <div class="field-grid">
        <div><label class="bass-label">보증금 (원)</label><input id="bdDeposit" type="number" class="bass-input" value="${depVal}"></div>
        <div><label class="bass-label">계약금 (보증금의 10%)</label><input id="bdContractDeposit" type="number" class="bass-input" value="${cdepVal}"></div>
        <div><label class="bass-label">월세 (원)</label><input id="bdMonthlyRent" type="number" class="bass-input" value="${Number(t.monthly_rent || 0)}"></div>
        <div><label class="bass-label">관리비 (원)</label><input id="bdMaintenance" type="number" class="bass-input" value="${Number(t.maintenance_fee || 0)}"></div>
        <div><label class="bass-label">퇴실 청소비 (원)</label><input id="bdCleaning" type="number" class="bass-input" value="${Number(t.cleaning_fee || 0)}"></div>
        <div><label class="bass-label">입주일</label><input id="bdMoveIn" type="date" class="bass-input" value="${esc(t.move_in_date || "")}"></div>
        <div><label class="bass-label">계약기간 (개월)</label><input id="bdTermMonths" type="number" class="bass-input" value="${Number(t.lease_term_months || 12)}"></div>
      </div>
      <textarea id="bdNote" class="bass-input" rows="2" placeholder="안내 메모 (선택)" style="width:100%;margin-top:10px;"></textarea>
      <button id="bdSubmit" type="button" class="bass-btn bass-btn--primary" style="width:100%;margin-top:10px;">💬 채팅방에 조건 올리기</button>
    `);
    const depInp = body.querySelector("#bdDeposit");
    const cdepInp = body.querySelector("#bdContractDeposit");
    if (depInp && cdepInp && !isRen) {
      depInp.addEventListener("input", () => {
        cdepInp.value = String(Math.floor(Math.max(0, Number(depInp.value || 0)) / 10));
      });
    }
    body.querySelector("#bdSubmit").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const bundle = {
          deposit: Number(body.querySelector("#bdDeposit").value || 0),
          contract_deposit: Number(body.querySelector("#bdContractDeposit").value || 0),
          monthly_rent: Number(body.querySelector("#bdMonthlyRent").value || 0),
          maintenance_fee: Number(body.querySelector("#bdMaintenance").value || 0),
          cleaning_fee: Number(body.querySelector("#bdCleaning").value || 0),
          move_in_date: body.querySelector("#bdMoveIn").value,
          lease_term_months: Number(body.querySelector("#bdTermMonths").value || 12),
        };
        const note = body.querySelector("#bdNote").value.trim();
        await Bass.api(APP_ID, `/sessions/${s.id}/messages`, {
          method: "POST",
          body: { kind: "bundle", body: note, bundle },
        });
        R3.closeModal();
        Bass.updateAppAlert("success", "계약 조건을 채팅방에 올렸습니다.");
        await refreshInlineChat(true);
        await C.fetchActiveSession(true);
      });
    });
  }

  function renderWsActionBar(s) {
    const bar = document.getElementById("wsActionBar");
    if (!bar) return;
    const step = s.current_step;
    const prog = stepProgress(s, step);
    const t = turnInfo(s);
    const tm = s.terms || {};
    const isRen = s.mode === "RENEWAL";
    const callRow = document.getElementById("cmCall");
    if (callRow) callRow.innerHTML = peerCallHtml(s);

    let extraInfo = "";
    let btns = "";
    if (s.call_status === "CALLING") {
      btns = `<button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-ws-step" data-step="0">📞 호출 응답 / 연결 확인</button>`;
    } else if (step === 1) {
      const myIdDone = currentRole === "lessor" ? Boolean(s.lessor_checked_tenant_id) : Boolean(s.tenant_checked_lessor_id);
      const myRegDone = currentRole === "lessor" ? Boolean(s.step1_lessor_confirmed) : Boolean(s.step1_tenant_locked);
      const tenantRegSubmitted = Boolean(s.tenant_registry && s.tenant_registry.submitted);
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">
        상호 신원 확인: 임대인 ${s.lessor_checked_tenant_id ? "✅" : "⏳"} · 임차인 ${s.tenant_checked_lessor_id ? "✅" : "⏳"} &nbsp;|&nbsp;
        등기부 확인: 임대인 ${s.step1_lessor_confirmed ? "✅" : "⏳"} · 임차인 ${s.step1_tenant_locked ? "✅" : "⏳"}
      </div>`;
      btns = `
        ${!adminProxy && currentRole ? `<button type="button" id="wsBtnIdCheck" class="bass-btn bass-btn--sm ${myIdDone ? "" : "bass-btn--primary"}">${myIdDone ? "✅ 신원 확인 완료" : "🪪 상호 신원 확인 체크"}</button>` : ""}
        <button type="button" class="bass-btn bass-btn--sm ${myRegDone ? "" : "bass-btn--primary"} js-ws-step" data-step="1">${currentRole === "tenant" ? "📄 법원 등기부 열람 · AI 분석" : "📄 등기부 제시 · 확인"}</button>
        ${!adminProxy && currentRole === "tenant" && tenantRegSubmitted && !myRegDone ? `<button type="button" id="wsBtnRegCheck" class="bass-btn bass-btn--sm bass-btn--primary">✅ 등기부 확인 체크</button>` : ""}`;
    } else if (step === 2) {
      const myOptDone = currentRole === "lessor" ? Boolean(s.step2_lessor_confirmed) : Boolean(s.step2_tenant_locked);
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">
        옵션·벽지 상태·사진 기록 확인: 임대인 ${s.step2_lessor_confirmed ? "✅" : "⏳"} · 임차인 ${s.step2_tenant_locked ? "✅" : "⏳"}
      </div>`;
      btns = `<button type="button" class="bass-btn bass-btn--sm ${myOptDone ? "" : "bass-btn--primary"} js-ws-step" data-step="2">🛋️ 옵션 확인 · 벽지 상태 메모 · 📷 사진 촬영</button>`;
    } else if (step === 3) {
      const canAgree = !adminProxy && currentRole && S.chatSummary.open_count === 0;
      const agreedMine = currentRole === "lessor" ? s.step3_lessor_agreed : s.step3_tenant_agreed;
      extraInfo = isRen
        ? `<div class="bass-muted" style="font-size:12px;margin-top:4px;">🔁 연장 계약은 본인·등기부·옵션 확인을 건너뛰고 조건 확인 체크 → 서명 날인으로 바로 종료됩니다.</div>`
        : `<div class="bass-muted" style="font-size:12px;margin-top:4px;">조건 확인 체크: 임대인 ${s.step3_lessor_agreed ? "✅" : "⏳"} · 임차인 ${s.step3_tenant_agreed ? "✅" : "⏳"}</div>`;
      btns = `
        ${!adminProxy && currentRole ? `<button type="button" id="wsBtnAgree3" class="bass-btn bass-btn--sm ${agreedMine ? "" : "bass-btn--primary"}" ${canAgree ? "" : "disabled"}>${agreedMine ? "✅ 조건 확인 완료 (상대 대기)" : (isRen ? "✅ 연장 조건 확인 체크" : "✅ 조건 확인 체크")}</button>` : ""}
        ${!adminProxy && currentRole === "lessor" ? `<button type="button" id="wsBtnBundle" class="bass-btn bass-btn--sm">📋 조건 수정</button>` : ""}
        <button type="button" class="bass-btn bass-btn--sm js-ws-step" data-step="3">✏️ 특약·세부 보기</button>`;
    } else if (step === 4) {
      const mySigDone = currentRole === "lessor" ? Boolean(s.lessor_signature) : Boolean(s.tenant_signature);
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">
        서명 날인: 임대인 ${s.lessor_signature ? "✅" : "⏳"} · 임차인 ${s.tenant_signature ? "✅" : "⏳"}
      </div>`;
      btns = `<button type="button" class="bass-btn bass-btn--sm ${mySigDone ? "" : "bass-btn--primary"} js-ws-step" data-step="4">${mySigDone ? "✅ 서명 완료 (상대 대기)" : "✍️ 서명 날인하기"}</button>`;
    } else {
      extraInfo = `<div style="margin-top:6px;padding:8px 10px;border-radius:10px;background:#fffbeb;border:1px solid #fde68a;font-size:12.5px;line-height:1.6;">
        🏦 <strong>임대인 수납 계좌:</strong> ${acctLabel(s)}<br>
        💸 <strong>계약금(보증금의 10%):</strong> <strong>${fmtWon(tm.contract_deposit)}</strong> ${currentRole === "tenant" ? "— 위 계좌로 송금 후 임대인이 수납을 확인하면 계약이 종료됩니다." : "— 실제 입금 내역을 확인한 뒤 아래 버튼으로 계약을 종료하세요."}
      </div>`;
      btns = currentRole === "lessor"
        ? `<button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-ws-step" data-step="5">💰 계약금 수납 확인 & 계약 종료</button>`
        : `<button type="button" class="bass-btn bass-btn--sm ${s.tenant_recheck_at ? "" : "bass-btn--primary"} js-ws-step" data-step="5">${s.tenant_recheck_at ? "📄 계약금 송금 · 체결 상태 보기" : "🔍 체결 직전 등기부 재열람 확인"}</button>`;
    }
    const cdepText = isRen ? "" : ` · 계약금(10%) <strong>${fmtWon(tm.contract_deposit)}</strong>`;
    const termsSummary = `<div class="bass-muted" style="font-size:12.5px;margin-top:6px;line-height:1.5;background:#f8fafc;padding:6px 10px;border-radius:10px;">
      보증금 <strong>${fmtWon(tm.deposit)}</strong>${cdepText} · 월세 <strong>${fmtWon(tm.monthly_rent)}</strong> · 관리비 <strong>${fmtWon(tm.maintenance_fee)}</strong>${Number(tm.cleaning_fee || 0) > 0 ? ` · 청소비 <strong>${fmtWon(tm.cleaning_fee)}</strong>` : ""} · 입주 ${esc(tm.move_in_date || "-")} (${Number(tm.lease_term_months || 12)}개월)
    </div>`;
    bar.innerHTML = `
      <div class="ws-action-card__head">
        <strong style="font-size:14px;">${esc(prog.label)}</strong>
        <span class="bass-badge ${t.mine ? "bass-badge--warn" : "bass-badge--info"}">${esc(t.text)}</span>
      </div>
      ${termsSummary}
      ${extraInfo}
      <div class="ws-action-card__btns">${btns}</div>`;

    bar.querySelectorAll(".js-ws-step").forEach((b) => {
      b.addEventListener("click", () => {
        const st = Number(b.dataset.step || 0);
        C.openStepModal(st || s.current_step);
      });
    });
    const btnIdChk = document.getElementById("wsBtnIdCheck");
    if (btnIdChk) {
      btnIdChk.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const cur = currentRole === "lessor" ? Boolean(s.lessor_checked_tenant_id) : Boolean(s.tenant_checked_lessor_id);
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/verify-id`, {
            method: "POST",
            body: { role: currentRole, checked: !cur },
          });
          S.activeSession = res.session;
          Bass.updateAppAlert("success", res.message);
          C.renderWorkspace();
        });
      });
    }
    const btnRegChk = document.getElementById("wsBtnRegCheck");
    if (btnRegChk) {
      btnRegChk.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/step1`, {
            method: "POST",
            body: { role: currentRole, checked_counterparty_id: Boolean(s.tenant_checked_lessor_id) },
          });
          S.activeSession = res.session;
          Bass.updateAppAlert("success", res.message);
          C.renderWorkspace();
        });
      });
    }
    const btnBundle = document.getElementById("wsBtnBundle");
    if (btnBundle) btnBundle.addEventListener("click", () => openBundleDialog(s));
    const btnAgree3 = document.getElementById("wsBtnAgree3");
    if (btnAgree3) {
      btnAgree3.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/step3/agree`, {
            method: "POST",
            body: { role: currentRole, expected_revision: s.revision },
          });
          S.activeSession = res.session;
          S.lastRevision = S.activeSession.revision;
          S.viewStep = S.activeSession.current_step;
          Bass.updateAppAlert("success", res.message);
          C.renderWorkspace();
        });
      });
    }
  }

  let wsChatBusy = false;
  async function refreshInlineChat(markRead = true) {
    const list = document.getElementById("wsChatList");
    if (!S.activeSession || !list || wsChatBusy) return;
    wsChatBusy = true;
    try {
      const d = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/messages?mark_read=${markRead ? "true" : "false"}`);
      const note = document.getElementById("wsChatNote");
      const comp = document.getElementById("wsChatComposer");
      if (comp) comp.hidden = d.read_only || adminProxy || !currentRole;
      if (note) note.textContent = d.purged ? d.purged_note : (d.read_only ? "종료된 계약방입니다(읽기 전용)." : "상대방과 채팅하며 아래 카드 또는 상단 버튼으로 단계별 확인·승인을 진행하세요.");
      const sig = JSON.stringify((d.messages || []).map((m) => [m.id, m.status]));
      if (sig !== S.wsChatLastSig) {
        S.wsChatLastSig = sig;
        const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 50;
        list.innerHTML = (d.messages || []).map((m) => renderChatBubble(m, d)).join("") || '<div class="bass-muted" style="font-size:13px;padding:8px;">아직 채팅이 없습니다. 상단 버튼으로 조건을 제시하거나 인사를 남겨 보세요.</div>';
        if (atBottom) list.scrollTop = list.scrollHeight;
        list.querySelectorAll(".cm-act").forEach((btn) => btn.addEventListener("click", () => {
          Bass.withLock(btn, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/messages/${btn.dataset.id}/respond`, { method: "POST", body: { action: btn.dataset.act } });
            Bass.updateAppAlert("success", res.message);
            await refreshInlineChat(true);
            await C.fetchActiveSession(true);
          });
        }));
      }
      S.chatSummary = { unread: d.unread || 0, open_count: d.open_count || 0 };
      updateChatFab();
      updateTurnLine();
    } catch (_) { /* 다음 폴링에서 재시도 */ }
    finally { wsChatBusy = false; }
  }

  const wsSendBtn = document.getElementById("wsChatSend");
  if (wsSendBtn) {
    const sendWsMsg = (text) => {
      if (!S.activeSession || !text) return;
      Bass.withLock(wsSendBtn, async () => {
        await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/messages`, { method: "POST", body: { kind: "question", body: text } });
        const inp = document.getElementById("wsChatMsg");
        if (inp) inp.value = "";
        await refreshInlineChat(true);
      });
    };
    wsSendBtn.addEventListener("click", () => {
      const inp = document.getElementById("wsChatMsg");
      sendWsMsg((inp && inp.value || "").trim());
    });
    document.querySelectorAll(".ws-quick").forEach((b) => b.addEventListener("click", () => sendWsMsg(b.dataset.t)));
  }

  Object.assign(C, {
    stepProgress, blockedMessage, turnInfo, updateTurnLine, updateChatFab,
    renderChatBubble, openChatDrawer, openProposeDialog, openRequestDialog,
    openBundleDialog, renderWsActionBar, refreshInlineChat,
  });
})();
