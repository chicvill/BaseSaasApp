/* apps/rent_dist/rent3_contract/frontend/chat.js — 채팅 중심 계약 워크스페이스, 차례 안내, 조건 일괄·개별 제안. */
(function () {
  "use strict";
  const C = window.R3C;
  if (!C) return;

  const {
    APP_ID, adminProxy, currentRole, OTHER_KO, state: S, dom: D,
    esc, digits, acctLabel, refundAcctLabel, fmtWon, FIELD_LABEL, fmtField, hhmm, STATUS_KO,
  } = C;

  function splitBankAcct(raw) {
    const t = String(raw || "").trim().replace(/\s+/g, " ");
    if (!t) return { bank: "", no: "" };
    const sp = t.indexOf(" ");
    if (sp > 0 && /\d/.test(t.slice(sp + 1))) {
      return { bank: t.slice(0, sp).trim(), no: t.slice(sp + 1).trim() };
    }
    const m = t.match(/^([^\d]+?)\s*([\d\- ]{6,})$/);
    if (m) return { bank: m[1].trim(), no: m[2].trim() };
    return { bank: "", no: t };
  }

  function isBankDone(s) {
    if (!s || s.mode === "RENEWAL") return true;
    const mode = s.bank_verify_mode || "REQUIRED";
    if (mode === "NOT_REQUIRED" || mode === "SKIPPED_MUTUAL") return true;
    const bv = s.bank_verify || {};
    return Boolean(bv.to_lessor_verified && bv.to_tenant_verified);
  }

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
    if (s.status === "PAUSED") return { mine: true, text: "⏸️ 보류 중 — [▶️ 재개하기]로 이어서 진행" };
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
        <div class="cm-bundle__grid">${items.map((it) => {
          const oldTxt = it.old_text || fmtField(it.field, it.old);
          const newTxt = it.new_text || fmtField(it.field, it.new);
          const diff = it.old !== undefined && it.old !== null && String(it.old) !== String(it.new);
          return `<div class="cm-bundle__row"><span class="bass-muted">${esc(it.label)}</span><span>${diff ? `<span class="bass-muted" style="text-decoration:line-through;margin-right:4px;">${esc(oldTxt)}</span>` : ""}<strong>${esc(newTxt)}</strong></span></div>`;
        }).join("")}</div>
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
    return `<div class="cm-msg-card ${m.status === "open" ? "is-open" : ""}">
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
      S.latestChatMessages = d.messages || [];
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

  function getEditableTerms(s) {
    const base = { ...(s.terms || {}) };
    const msgs = S.latestChatMessages || [];
    let fromOpenProposal = false;
    msgs.forEach((m) => {
      if (m.status !== "open") return;
      if (m.kind === "bundle" && m.proposal && Array.isArray(m.proposal.items)) {
        m.proposal.items.forEach((it) => {
          if (it && it.field && it.new !== undefined && it.new !== null) {
            base[it.field] = it.new;
            fromOpenProposal = true;
          }
        });
      } else if (m.kind === "proposal" && m.proposal && m.proposal.field && m.proposal.new !== undefined && m.proposal.new !== null) {
        base[m.proposal.field] = m.proposal.new;
        fromOpenProposal = true;
      }
    });
    return { terms: base, fromOpenProposal };
  }

  function openProposeDialog(s, field) {
    const label = FIELD_LABEL[field];
    const { terms: curTerms } = getEditableTerms(s);
    const cur = curTerms[field];
    const isDate = field === "move_in_date";
    const rc = s.mode === "RENEWAL" ? S.renewalContext : null;
    const capHint = rc && field === "deposit" && rc.prev_deposit > 0
      ? `<div class="bass-muted" style="font-size:12px;margin-bottom:6px;">기존 보증금 ${fmtWon(rc.prev_deposit)} · 법정 5% 상한 <strong>최대 ${fmtWon(rc.max_deposit)}</strong></div>`
      : (rc && field === "monthly_rent" && rc.prev_monthly_rent > 0
        ? `<div class="bass-muted" style="font-size:12px;margin-bottom:6px;">기존 월세 ${fmtWon(rc.prev_monthly_rent)} · 법정 5% 상한 <strong>최대 ${fmtWon(rc.max_monthly_rent)}</strong></div>`
        : "");
    const body = R3.openModal(`💡 ${label} 제안`, `
      <div style="font-size:14px;margin-bottom:6px;">현재 ${label}: <strong>${esc(fmtField(field, cur))}</strong></div>
      ${capHint}
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
    const { terms: t, fromOpenProposal } = getEditableTerms(s);
    const isRen = s.mode === "RENEWAL";
    const rc = isRen ? S.renewalContext : null;
    const depVal = Number(t.deposit || 0);
    const cdepVal = isRen ? 0 : (t.contract_deposit != null ? Number(t.contract_deposit) : Math.floor(depVal / 10));
    const renSummary = rc ? `
      <div class="bass-card" style="padding:8px 10px;margin:0 0 10px;font-size:12.5px;line-height:1.6;background:#f0f9ff;border-color:var(--primary-2);">
        <div><strong>기존 계약</strong>: 보증금 ${fmtWon(rc.prev_deposit)} · 월세 ${fmtWon(rc.prev_monthly_rent)} · 관리비 ${fmtWon(rc.prev_maintenance_fee)}</div>
        ${rc.notice_sent ? `<div><strong>문자·예고 조건</strong>: 보증금 ${fmtWon(rc.notice_deposit)} · 월세 ${fmtWon(rc.notice_monthly_rent)} · 관리비 ${fmtWon(rc.notice_maintenance_fee)} (${Number(rc.notice_term_months || 12)}개월)</div>` : ""}
        <div class="bass-muted" style="font-size:12px;">법정 5% 상한: 보증금 최대 ${fmtWon(rc.max_deposit)} · 월세 최대 ${fmtWon(rc.max_monthly_rent)}</div>
        ${fromOpenProposal ? `<div style="font-size:12px;color:var(--primary);margin-top:2px;">💬 방금 채팅방에 올린 최신 제안 조건을 불러왔습니다. 통화 협의 내용대로 이어서 수정하세요.</div>` : ""}
      </div>` : (fromOpenProposal ? `<div class="bass-muted" style="font-size:12px;margin:0 0 8px;color:var(--primary);">💬 채팅방에 올려 둔 최신 제안 조건을 불러왔습니다.</div>` : "");
    const body = R3.openModal(isRen ? "📋 재계약 조건 수정 · 일괄 반영" : "📋 계약 조건 수정 · 일괄 반영", `
      ${renSummary}
      <p class="bass-muted" style="font-size:13px;margin:0 0 10px;">${isRen
        ? "문자로 안내한 재계약 조건을 기준으로 임차인과 통화·채팅 협의 후 서명 전 조건을 조정해 채팅방에 올립니다. 상대가 [전체 수락]하면 계약 조건에 즉시 반영됩니다."
        : "보증금·계약금(보증금의 10%)·월세·관리비·청소비·입주일·계약기간을 한 번에 조정해 채팅방에 올립니다. 상대가 채팅방에서 확인하면 한꺼번에 반영됩니다."}</p>
      <div class="field-grid">
        <div><label class="bass-label">보증금 (원)${rc && rc.prev_deposit > 0 ? ` · 최대 ${fmtWon(rc.max_deposit)}` : ""}</label><input id="bdDeposit" type="number" class="bass-input" value="${depVal}"></div>
        <div><label class="bass-label">${isRen ? "계약금 (재계약 0원)" : "계약금 (보증금의 10%)"}</label><input id="bdContractDeposit" type="number" class="bass-input" value="${cdepVal}" ${isRen ? "readonly" : ""}></div>
        <div><label class="bass-label">월세 (원)${rc && rc.prev_monthly_rent > 0 ? ` · 최대 ${fmtWon(rc.max_monthly_rent)}` : ""}</label><input id="bdMonthlyRent" type="number" class="bass-input" value="${Number(t.monthly_rent || 0)}"></div>
        <div><label class="bass-label">관리비 (원)</label><input id="bdMaintenance" type="number" class="bass-input" value="${Number(t.maintenance_fee || 0)}"></div>
        <div><label class="bass-label">퇴실 청소비 (원)</label><input id="bdCleaning" type="number" class="bass-input" value="${Number(t.cleaning_fee || 0)}"></div>
        <div><label class="bass-label">${isRen ? "연장 시작일" : "입주일"}</label><input id="bdMoveIn" type="date" class="bass-input" value="${esc(t.move_in_date || "")}"></div>
        <div><label class="bass-label">계약기간 (개월)</label><input id="bdTermMonths" type="number" class="bass-input" value="${Number(t.lease_term_months || 12)}"></div>
      </div>
      <textarea id="bdNote" class="bass-input" rows="2" placeholder="${isRen ? "통화·협의 메모 (예: 임차인과 통화 협의하여 월세 300,000원으로 확정)" : "안내 메모 (선택)"}" style="width:100%;margin-top:10px;"></textarea>
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
      const depN = Number(body.querySelector("#bdDeposit").value || 0);
      const rentN = Number(body.querySelector("#bdMonthlyRent").value || 0);
      if (rc && rc.prev_deposit > 0 && depN > rc.max_deposit) {
        Bass.updateAppAlert("warning", `보증금은 법정 5% 상한(${fmtWon(rc.max_deposit)}) 이내로 입력해 주세요.`);
        return;
      }
      if (rc && rc.prev_monthly_rent > 0 && rentN > rc.max_monthly_rent) {
        Bass.updateAppAlert("warning", `월세는 법정 5% 상한(${fmtWon(rc.max_monthly_rent)}) 이내로 입력해 주세요.`);
        return;
      }
      Bass.withLock(this, async () => {
        const bundle = {
          deposit: depN,
          contract_deposit: isRen ? 0 : Number(body.querySelector("#bdContractDeposit").value || 0),
          monthly_rent: rentN,
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

  function formatKoreanDateDot() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}. ${m}. ${day}.`;
  }

  function normSimpleName(v) {
    return String(v || "").replace(/\([^)]*\)/g, "").replace(/\s+/g, "");
  }

  function nowLocalInputStr() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  }

  function readFileDataUrl(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result || ""));
      fr.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
      fr.readAsDataURL(file);
    });
  }

  function renderPartyHeader(s) {
    const el = document.getElementById("wsPartyHeader");
    if (!el) return;
    const tm = s.terms || {};
    el.innerHTML = `
      <div class="cg-party-header__title">💬 계약 채팅 <span class="bass-muted" style="font-weight:600;font-size:12.5px;">${esc(s.building_name)} ${esc(s.room_no)}호</span></div>
      <div class="cg-party-header__names">
        <span>임대인: <strong>${esc(s.lessor_name || "-")}</strong></span>
        <span>임차인: <strong>${esc(tm.tenant_name || "응답 대기")}</strong></span>
      </div>`;
  }

  function buildSmartChatTimelineHtml(s, msgs, d) {
    const tm = s.terms || {};
    const lr = s.registry_data || {};
    const tr = s.tenant_registry || {};
    const aiRes = s.registry_ai_result || {};
    const isRen = s.mode === "RENEWAL";
    const active = s.status === "IN_PROGRESS";
    const paused = s.status === "PAUSED";
    const canAct = !adminProxy && Boolean(currentRole) && active;
    const renderMsg = (m) => renderChatBubble(m, d);
    const sigHtml = (sig) => (C.renderSigCell ? C.renderSigCell(sig) : esc(sig));

    const pauseBannerHtml = paused ? `
      <div class="cg-pause-banner">
        <div style="font-weight:800;color:#92400e;margin-bottom:4px;">⏸️ 스마트 계약이 잠시 보류 중입니다 (${s.current_step}단계 보관 중)</div>
        <div style="font-size:12.5px;color:#78350f;line-height:1.55;">
          ${s.pause_reason ? `보류 사유: <strong>${esc(s.pause_reason)}</strong><br>` : ""}
          보관 기한: <strong>${esc(String(s.pause_expires_at || "").slice(0, 16).replace("T", " ") || "최대 7일")}</strong> · 보류 중에도 아래 채팅으로 자유롭게 대화할 수 있습니다.
        </div>
        ${!adminProxy && currentRole ? `<div style="margin-top:8px;"><button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-cg-resume">▶️ 스마트 계약 재개하기 (${s.current_step}단계 이어서)</button></div>` : ""}
      </div>` : "";

    if (s.call_status === "CALLING") {
      const callee = currentRole !== s.initiated_by_role;
      const callCard = `
        <div class="cg-card cg-card--active">
          <div class="cg-card__top">
            <span class="cg-card__tag">💬 1:1 방문 협의 채팅 · 대면 전</span>
            <span class="bass-badge bass-badge--warn">${callee ? "임대인 알림 도착 · 채팅 가능" : "임대인 알림 전송 완료"}</span>
          </div>
          <div class="cg-card__msg">
            ${callee
              ? `<strong>${esc(s.building_name)} ${esc(s.room_no)}호</strong> 방문 협의 · 스마트 계약 요청이 도착했습니다.<br>먼저 아래 채팅창으로 <strong>방문 일정·문의사항을 자유롭게 협의</strong>하시고, 현장에서 <strong>대면</strong>을 마치면 아래 버튼으로 스마트 계약을 개시해 주세요.`
              : `<strong>${esc(OTHER_KO)}</strong>에게 실시간 알림이 전송되었습니다.<br>아래 채팅창에서 <strong>1:1 방문 일정·조건을 자유롭게 협의</strong>하세요. 현장 <strong>대면</strong> 후 상대방이 <strong>[🤝 대면 완료 · 스마트 계약 개시]</strong>를 누르면 ① 신분증 확인부터 바로 시작됩니다.`}
          </div>
          <div class="cg-link-row">
            ${callee && !adminProxy
              ? `<button type="button" class="cg-link-btn cg-link-btn--primary js-cg-respond-now">🤝 대면 완료 · 스마트 계약 개시하기</button>
                 <button type="button" class="cg-link-btn js-cg-open-step" data-step="0">📋 상세 연결 정보</button>`
              : `<button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="0">📋 초대 QR · 사전 작성 열기 ↗</button>`}
          </div>
        </div>`;
      return pauseBannerHtml + callCard + msgs.map(renderMsg).join("");
    }

    const parts = [];
    if (pauseBannerHtml) parts.push(pauseBannerHtml);
    if (s.status === "CANCELLED") {
      parts.push(`
        <div class="cg-card" style="border-color:var(--err);border-left-color:var(--err);background:#fef2f2;">
          <div class="cg-card__top"><span class="cg-card__tag" style="background:#fee2e2;color:#b91c1c;">⛔ 계약 중단</span></div>
          <div class="cg-card__msg">${esc(s.cancel_reason || "이 계약은 중단되었습니다.")}</div>
        </div>`);
    }

    const msgs1 = msgs.filter((m) => (m.step || 1) <= 1);
    const msgs23 = msgs.filter((m) => m.step === 2 || m.step === 3);
    const msgs4 = msgs.filter((m) => m.step === 4);
    const msgs5 = msgs.filter((m) => (m.step || 0) >= 5);

    if (!isRen) {
      // 앱 개입 카드 ①: 대면 · 실물 신분증 확인
      const idDone = Boolean(s.lessor_checked_tenant_id && s.tenant_checked_lessor_id);
      const canLessorId = canAct && currentRole === "lessor";
      const canTenantId = canAct && currentRole === "tenant";
      parts.push(`
        <div id="cgCardId" class="cg-card ${idDone ? "cg-card--done" : "cg-card--active"}">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ① 대면 · 신분증 확인</span>
            <span class="bass-badge ${idDone ? "bass-badge--ok" : "bass-badge--warn"}">${idDone ? "양측 완료 ✓" : "진행 중"}</span>
          </div>
          <div class="cg-card__msg">
            <strong>지금부터 스마트 계약을 시작합니다.</strong><br>
            대면 상태에서 임대인과 임차인은 서로 신분증을 제시해 주세요.
          </div>
          <label class="cg-check ${s.lessor_checked_tenant_id ? "is-checked" : (canLessorId ? "is-actionable" : "")}">
            <span class="cg-check__label">
              <span><strong>임대인이 임차인 신분 확인</strong> (${esc(tm.tenant_name || "임차인")})</span>
              <span class="cg-check__hint">← 임대인이 체크${s.lessor_checked_tenant_id ? " · 확인 완료" : ""}</span>
            </span>
            <input type="checkbox" class="js-cg-id-chk" data-role="lessor" ${s.lessor_checked_tenant_id ? "checked" : ""} ${canLessorId ? "" : "disabled"}>
          </label>
          <label class="cg-check ${s.tenant_checked_lessor_id ? "is-checked" : (canTenantId ? "is-actionable" : "")}">
            <span class="cg-check__label">
              <span><strong>임차인이 임대인 신분 확인</strong> (${esc(s.lessor_name || "임대인")})</span>
              <span class="cg-check__hint">← 임차인이 체크${s.tenant_checked_lessor_id ? " · 확인 완료" : ""}</span>
            </span>
            <input type="checkbox" class="js-cg-id-chk" data-role="tenant" ${s.tenant_checked_lessor_id ? "checked" : ""} ${canTenantId ? "" : "disabled"}>
          </label>
          <div class="cg-id-links">
            <span>🔍 무료 공공 신분증 진위확인:</span>
            <a class="bass-btn bass-btn--sm" href="tel:1382">☎️ 주민증 1382</a>
            <a class="bass-btn bass-btn--sm" href="https://www.gov.kr" target="_blank" rel="noopener">🏛️ 정부24 진위확인 ↗</a>
          </div>
        </div>`);

      // 앱 개입 카드 ②: 임차료 송금 · 보증금 환불용 계좌번호 등록
      const bv = s.bank_verify || {};
      const lAcctParts = splitBankAcct(s.lessor_account);
      const tAcctParts = splitBankAcct(s.tenant_refund_account);
      const lBankVal = bv.lessor_bank || lAcctParts.bank || "";
      const lNoVal = bv.lessor_account_no || lAcctParts.no || "";
      const tBankVal = bv.tenant_bank || tAcctParts.bank || "";
      const tNoVal = bv.tenant_account_no || tAcctParts.no || "";
      const lAcctReady = Boolean(s.lessor_account && lNoVal);
      const tAcctReady = Boolean(s.tenant_refund_account && tNoVal);
      const acctBothReady = lAcctReady && tAcctReady;
      const canEditBank = canAct && !s.lessor_signature && !s.tenant_signature;
      parts.push(`
        <div id="cgCardAccount" class="cg-card ${acctBothReady ? "cg-card--done" : (idDone ? "cg-card--active" : "")}">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ② 계좌번호 등록 (수납·환불용)</span>
            <span class="bass-badge ${acctBothReady ? "bass-badge--ok" : "bass-badge--warn"}">${acctBothReady ? "양측 등록 완료 ✓" : "계좌 등록 필요"}</span>
          </div>
          <div class="cg-card__msg">
            <strong>임차료·보증금 수납 계좌</strong>(임대인)와 <strong>퇴실 시 보증금 환불 계좌</strong>(임차인)를 본인 명의 계좌로 등록해 주세요.
          </div>
          <div class="cg-bank-grid">
            <div class="cg-bank-box ${lAcctReady ? "is-ok" : (currentRole === "lessor" ? "is-mine" : "")}">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
                <strong>🏢 임대인 수납 계좌 (임차료·보증금)</strong>
                <span class="bass-badge ${lAcctReady ? "bass-badge--ok" : "bass-badge--warn"}">${lAcctReady ? "등록 완료 ✓" : "등록 대기"}</span>
              </div>
              <div style="margin-top:4px;">${acctLabel(s)}</div>
              ${lAcctReady && currentRole === "tenant" ? `<div class="cg-inline-row"><button type="button" class="bass-btn bass-btn--sm js-cg-copy-txt" data-copy="${esc(s.lessor_account)}" data-label="임대인 계좌번호">📋 임대인 계좌 복사</button></div>` : ""}
              ${canEditBank && currentRole === "lessor" ? `
                <div class="cg-inline-row">
                  <input id="cgLessorBank" class="bass-input js-cg-bank-input" style="flex:1;min-width:100px;" placeholder="은행명 (예: 국민은행)" value="${esc(lBankVal)}">
                  <input id="cgLessorAccNo" class="bass-input js-cg-bank-input" style="flex:2;min-width:140px;" placeholder="계좌번호 (예: 123-456-789012)" value="${esc(lNoVal)}">
                  <button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-cg-save-acct" data-role="lessor">💾 수납 계좌 저장</button>
                </div>` : ""}
            </div>
            <div class="cg-bank-box ${tAcctReady ? "is-ok" : (currentRole === "tenant" ? "is-mine" : "")}">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
                <strong>🔑 임차인 보증금 환불 계좌 (퇴실 시 반환용)</strong>
                <span class="bass-badge ${tAcctReady ? "bass-badge--ok" : "bass-badge--warn"}">${tAcctReady ? "등록 완료 ✓" : "등록 대기"}</span>
              </div>
              <div style="margin-top:4px;">${refundAcctLabel(s)}</div>
              ${tAcctReady && currentRole === "lessor" ? `<div class="cg-inline-row"><button type="button" class="bass-btn bass-btn--sm js-cg-copy-txt" data-copy="${esc(s.tenant_refund_account)}" data-label="임차인 환불 계좌번호">📋 임차인 계좌 복사</button></div>` : ""}
              ${canEditBank && currentRole === "tenant" ? `
                <div class="cg-inline-row">
                  <input id="cgTenantBank" class="bass-input js-cg-bank-input" style="flex:1;min-width:100px;" placeholder="은행명 (예: 카카오뱅크)" value="${esc(tBankVal)}">
                  <input id="cgTenantAccNo" class="bass-input js-cg-bank-input" style="flex:2;min-width:140px;" placeholder="계좌번호 (예: 3333-01-1234567)" value="${esc(tNoVal)}">
                  <button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-cg-save-acct" data-role="tenant">💾 환불 계좌 저장</button>
                </div>` : ""}
            </div>
          </div>
        </div>`);

      // 앱 개입 카드 ③: 상호 1원 송수금 확인 (난수 4자리 코드)
      const bankSkipped = s.bank_verify_mode === "SKIPPED_MUTUAL";
      const bankDone = isBankDone(s);
      const sendToName = currentRole === "tenant" ? (s.lessor_name || "임대인") : (tm.tenant_name || "임차인");
      const sendToAcct = currentRole === "tenant" ? (s.lessor_account || "") : (s.tenant_refund_account || "");
      const sendCode = currentRole === "tenant" ? (bv.to_lessor_code || "") : (bv.to_tenant_code || "");
      const sendDoneByOther = currentRole === "tenant" ? Boolean(bv.to_lessor_verified) : Boolean(bv.to_tenant_verified);
      const sendMarked = currentRole === "tenant" ? Boolean(bv.to_lessor_sent_at) : Boolean(bv.to_tenant_sent_at);
      const recvDone = currentRole === "tenant" ? Boolean(bv.to_tenant_verified) : Boolean(bv.to_lessor_verified);
      const recvPeerSent = currentRole === "tenant" ? Boolean(bv.to_tenant_sent_at) : Boolean(bv.to_lessor_sent_at);
      const recvCodeIssued = currentRole === "tenant" ? Boolean(bv.to_tenant_code_issued) : Boolean(bv.to_lessor_code_issued);
      const skipLessor = Boolean(bv.skip_lessor_agreed);
      const skipTenant = Boolean(bv.skip_tenant_agreed);
      parts.push(`
        <div id="cgCardOneWon" class="cg-card ${bankDone ? "cg-card--done" : (acctBothReady ? "cg-card--active" : "")}">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ③ 1원 송수금 확인 (난수 4자리)</span>
            <span class="bass-badge ${bankDone ? "bass-badge--ok" : "bass-badge--warn"}">${bankSkipped ? "양측 합의로 생략 ✓" : (bankDone ? "상호 1원 확인 완료 ✓" : "상호 확인 필요")}</span>
          </div>
          ${bankSkipped ? `
            <div class="cg-ai-summary" style="background:#f0fdf4;border-color:#86efac;">
              ✅ <strong>임대인·임차인 양측 합의로 1원 송수금 확인을 생략했습니다.</strong><br>
              사유: ${esc(bv.skip_reason || "대면 실물 신분증 확인으로 대체")}
            </div>
          ` : `
            <div class="cg-card__msg">
              <strong>송금할 때 은행 앱의 예금주(실명)</strong>를 확인하고, <strong>받는 통장 표시(입금자명)에 난수 4자리</strong>를 적어 1원씩 주고받아 실명과 계좌 주인을 입증합니다.
            </div>
            <div class="cg-bank-grid">
              ${currentRole && !adminProxy ? `
                <div class="cg-bank-box ${sendDoneByOther ? "is-ok" : "is-mine"}">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
                    <strong>1) 내가 ${esc(sendToName)} 계좌로 1원 보내기</strong>
                    <span class="bass-badge ${sendDoneByOther ? "bass-badge--ok" : (sendMarked ? "bass-badge--info" : "bass-badge--warn")}">${sendDoneByOther ? "상대 코드 일치 ✓" : (sendMarked ? "송금 완료 · 상대 입력 대기" : "송금 대기")}</span>
                  </div>
                  ${sendToAcct && sendCode ? `
                    <div style="margin-top:6px;">
                      받는 계좌: <strong>${esc(sendToAcct)}</strong> (예금주: <strong>${esc(sendToName)}</strong>)
                    </div>
                    <div class="cg-inline-row">
                      <span>입금자명(받는 통장 표시) 난수 4자리:</span>
                      <span class="cg-code-badge">${esc(sendCode)}</span>
                      <button type="button" class="bass-btn bass-btn--sm js-cg-copy-txt" data-copy="${esc(sendToAcct)}" data-label="받는 계좌번호">📋 계좌 복사</button>
                      <button type="button" class="bass-btn bass-btn--sm js-cg-copy-txt" data-copy="${esc(sendCode)}" data-label="난수 4자리 코드">📋 코드 복사</button>
                      ${canEditBank && !sendDoneByOther ? `<button type="button" class="bass-btn bass-btn--sm js-cg-1won-reissue">🔄 새 코드 재발급</button>` : ""}
                    </div>
                    <div class="bass-muted" style="font-size:12px;margin-top:4px;">
                      ※ 이 4자리 코드는 <strong>내 화면에만</strong> 표시됩니다. 은행 앱에서 예금주가 <strong>${esc(sendToName)}</strong>인지 확인한 뒤, 받는 통장 표시에 <strong>${esc(sendCode)}</strong>를 적어 1원을 보내 주세요.
                    </div>
                    ${canEditBank && !sendDoneByOther ? `
                      <label class="cg-check is-actionable" style="margin-top:6px;">
                        <span class="cg-check__label">
                          <span>은행 앱 송금 화면에서 <strong>예금주명(${esc(sendToName)}) 일치</strong>를 직접 확인했습니다.</span>
                        </span>
                        <input type="checkbox" id="cgHolderNameChk" ${sendMarked ? "checked" : ""}>
                      </label>
                      <div class="cg-inline-row">
                        <button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-cg-1won-sent">💸 예금주 확인 · 1원 송금 완료 알림</button>
                      </div>` : ""}
                  ` : `<div class="bass-muted" style="font-size:12.5px;margin-top:4px;">위 ②에서 ${esc(sendToName)}님의 계좌가 등록되면 송금할 난수 4자리 코드가 자동 발급됩니다.</div>`}
                </div>

                <div class="cg-bank-box ${recvDone ? "is-ok" : "is-mine"}">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
                    <strong>2) 내 통장에 들어온 1원의 4자리 코드 입력</strong>
                    <span class="bass-badge ${recvDone ? "bass-badge--ok" : (recvPeerSent ? "bass-badge--warn" : "")}">${recvDone ? "내 계좌 확인 완료 ✓" : (recvPeerSent ? "1원 입금 도착 · 입력 필요" : "상대 송금 대기")}</span>
                  </div>
                  ${recvDone ? `
                    <div style="margin-top:6px;color:#065f46;font-weight:700;">
                      ✅ 본인 통장의 4자리 코드가 일치하여 계좌 실명·점유 확인이 완료되었습니다.${currentRole === "tenant" ? " (임차인 본인인증 자동 확정 ✓)" : ""}
                    </div>
                  ` : (recvCodeIssued ? `
                    <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                      상대방이 보낸 1원 입금 내역의 <strong>입금자명 숫자 4자리</strong>를 본인 은행 앱에서 확인해 입력해 주세요. (5회 오입력 시 재발급 필요)
                    </div>
                    ${canEditBank ? `
                      <div class="cg-inline-row">
                        <input id="cgOneWonCodeInput" class="bass-input js-cg-bank-input" style="max-width:180px;font-weight:800;letter-spacing:2px;" inputmode="numeric" maxlength="8" placeholder="숫자 4자리 (예: 4821)">
                        <button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-cg-1won-confirm">✅ 4자리 코드 확인</button>
                      </div>` : ""}
                  ` : `<div class="bass-muted" style="font-size:12.5px;margin-top:4px;">위 ②에서 내 계좌를 먼저 저장해 주세요.</div>`)}
                </div>
              ` : `
                <div class="cg-bank-box">
                  임대인 계좌 확인: ${bv.to_lessor_verified ? "완료 ✓" : "대기"} · 임차인 계좌 확인: ${bv.to_tenant_verified ? "완료 ✓" : "대기"}
                </div>
              `}
            </div>
          `}
          <div class="bass-muted" style="font-size:12px;margin-top:6px;">
            실시간 확인 현황: 임대인 수납 계좌 ${bv.to_lessor_verified ? "✅ 완료" : (bankSkipped ? "생략 합의" : "⏳ 대기")} · 임차인 환불 계좌 ${bv.to_tenant_verified ? "✅ 완료" : (bankSkipped ? "생략 합의" : "⏳ 대기")}
          </div>
          ${canEditBank && !(bv.to_lessor_verified && bv.to_tenant_verified) ? `
            <details style="margin-top:8px;font-size:12.5px;">
              <summary style="cursor:pointer;color:#475569;font-weight:700;">예외: 타행이체 제한·외국인 계좌 등으로 1원 확인이 어려운 경우 (양측 합의 생략)</summary>
              <div style="margin-top:6px;padding:8px 10px;border-radius:10px;background:#f8fafc;border:1px solid #e2e8f0;">
                <input id="cgBankSkipReason" class="bass-input js-cg-bank-input" placeholder="생략 사유 (예: 외국인 계좌 · 대면 실물 신분증 확인 완료)" value="${esc(bv.skip_reason || "")}" style="margin-bottom:6px;">
                <label class="cg-check ${skipLessor ? "is-checked" : (currentRole === "lessor" ? "is-actionable" : "")}" style="margin-top:4px;">
                  <span class="cg-check__label"><span>임대인: 대면 실물 신분증 확인으로 1원 확인 생략에 동의</span></span>
                  <input type="checkbox" class="js-cg-bank-skip" data-role="lessor" ${skipLessor ? "checked" : ""} ${currentRole === "lessor" ? "" : "disabled"}>
                </label>
                <label class="cg-check ${skipTenant ? "is-checked" : (currentRole === "tenant" ? "is-actionable" : "")}" style="margin-top:4px;">
                  <span class="cg-check__label"><span>임차인: 대면 실물 신분증 확인으로 1원 확인 생략에 동의</span></span>
                  <input type="checkbox" class="js-cg-bank-skip" data-role="tenant" ${skipTenant ? "checked" : ""} ${currentRole === "tenant" ? "" : "disabled"}>
                </label>
              </div>
            </details>` : ""}
        </div>`);

      // 앱 개입 카드 ④: 법원등기소 등기부등본 열람 · 캡처 AI OCR 검토 (임대인 신분·소유권 재확인)
      const regSkipped = s.registry_check_mode === "SKIPPED_BY_TENANT";
      const regEnabled = !regSkipped;
      const regDone = Boolean(s.step1_lessor_confirmed && s.step1_tenant_locked);
      const trSub = Boolean(tr.submitted);
      const ownerTarget = lr.owner_name || s.lessor_name || "";
      const ownerOk = trSub && Boolean(tr.owner_name) && (!ownerTarget || normSimpleName(tr.owner_name) === normSimpleName(ownerTarget));
      const mortOk = trSub && tr.mortgage_amount !== undefined && tr.mortgage_amount !== "";
      const seizeOk = trSub && tr.seizure_or_trust === "없음";
      const canTenantRegChk = canAct && currentRole === "tenant" && !s.step1_tenant_locked && regEnabled;
      const canToggleReg = canAct && currentRole === "tenant" && s.current_step <= 3 && !s.lessor_signature && !s.tenant_signature;
      const regBadgeText = regSkipped ? "임차인 선택으로 생략 ✓" : (regDone ? "열람·확인 완료 ✓" : "임차인 열람 선택");
      parts.push(`
        <div id="cgCardRegistry" class="cg-card ${regDone || regSkipped ? "cg-card--done" : (s.current_step === 1 && bankDone ? "cg-card--active" : "")}">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ④ 등기부등본 확인 (임대인 신분 재확인)</span>
            <span class="bass-badge ${regDone || regSkipped ? "bass-badge--ok" : "bass-badge--warn"}">${regBadgeText}</span>
          </div>
          <div class="cg-reg-opt-box">
            <label class="cg-check ${regEnabled ? "is-checked" : ""} ${canToggleReg ? "is-actionable" : ""}" style="margin:0;">
              <span class="cg-check__label">
                <span><strong>☑️ 등기부등본 캡처 · AI 권리관계 검토 진행 (임차인 선택)</strong></span>
                <span class="cg-check__hint">${regSkipped ? "현재 생략 선택됨 — 서명 전까지 체크를 켜면 언제든 등기부 캡처·AI 검토를 진행할 수 있습니다" : "임차인이 원할 때만 체크해서 진행합니다 (체크를 풀면 등기부 확인 단계를 생략할 수 있어요)"}</span>
              </span>
              <input type="checkbox" class="js-cg-reg-opt" ${regEnabled ? "checked" : ""} ${canToggleReg ? "" : "disabled"}>
            </label>
          </div>
          ${regSkipped ? `
            <div class="cg-ai-summary" style="background:#f0fdf4;border-color:#86efac;">
              ✅ <strong>임차인 선택으로 등기부등본 확인을 생략했습니다.</strong><br>
              임대인 신분·계좌 확인이 끝나면 다음 단계로 바로 진행되며, 서명 전까지 위 체크를 다시 켜서 언제든 캡처·AI 검토를 할 수 있습니다.
            </div>
          ` : `
            <div class="cg-card__msg">
              임차인은 <strong>[ 법원등기소 ]</strong> 에 접속하여 등기부등본을 열람·캡처하고, <strong>등기부 갑구 소유자가 방금 신분증·1원 계좌로 확인한 임대인(${esc(ownerTarget || "-")})과 같은지 재확인</strong>해 주세요.
            </div>
            <div class="cg-link-row">
              <button type="button" class="cg-link-btn js-cg-iros">📄 등기부등본 발급 받기 · AI 안내</button>
              <button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="1">
                ${currentRole === "tenant" ? "📄 [ 등기부등본 확인 · 승인 모달 ] ↗" : "📄 [ 등기부 제시 · 확인 모달 ] ↗"}
              </button>
            </div>
            ${currentRole === "tenant" && canAct && !s.step1_tenant_locked ? `
              <div class="cg-ocr-box">
                <div style="font-weight:800;font-size:13px;color:#1e3a8a;margin-bottom:4px;">📸 등기부등본 캡처 · PDF 바로 올려 AI OCR 검토</div>
                <div class="bass-muted" style="font-size:12px;margin-bottom:6px;line-height:1.5;">
                  법원등기소 화면을 캡처해 올리거나 <strong>Ctrl+V 붙여넣기</strong>하면 AI가 소유자·근저당·압류 여부를 쉬운 말로 요약해 드립니다.
                </div>
                <label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:6px;cursor:pointer;">
                  <input type="checkbox" class="js-cg-reg-ai-consent" checked>
                  <span>AI(Google Gemini) 등기부 OCR 분석 전송 동의 (분석 전용, 서버 미보관)</span>
                </label>
                <input type="file" class="bass-input js-cg-reg-file" accept="image/*,application/pdf">
              </div>` : ""}
            ${aiRes && aiRes.status === "ok" && aiRes.extracted ? `
              <div class="cg-ai-summary">
                <div style="font-weight:800;color:#1e3a8a;margin-bottom:4px;">🤖 AI 등기부등본 쉬운 요약 (참고용)</div>
                <div style="white-space:pre-line;">${esc(aiRes.plain_summary || `소유자: ${aiRes.extracted.owner_name || "-"} · 근저당 ${fmtWon(aiRes.extracted.mortgage_total)} · 압류/신탁 ${aiRes.extracted.seizure_or_trust || "없음"}`)}</div>
                ${currentRole === "tenant" && canAct && !s.step1_tenant_locked ? `
                  <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
                    <button type="button" class="bass-btn bass-btn--sm bass-btn--primary js-cg-reg-ai-apply">✅ AI OCR 결과로 등기부 확인 바로 완료</button>
                    <button type="button" class="bass-btn bass-btn--sm js-cg-open-step" data-step="1">🔍 상세 대조·수정 모달</button>
                  </div>` : ""}
              </div>` : ""}
            <div class="cg-reg-list">
              <div class="cg-reg-item">
                <span>1. 등기인 = 임대인 명의(${esc(ownerTarget || "-")}) 일치?</span>
                <span class="bass-badge ${ownerOk ? "bass-badge--ok" : "bass-badge--warn"}">${ownerOk ? `${esc(tr.owner_name)} 일치 ✓` : "열람 대기"}</span>
              </div>
              <div class="cg-reg-item">
                <span>2. 근저당 / 채권최고액 확인</span>
                <span class="bass-badge ${mortOk ? "bass-badge--ok" : "bass-badge--warn"}">${mortOk ? `${fmtWon(tr.mortgage_amount)} ✓` : "열람 대기"}</span>
              </div>
              <div class="cg-reg-item">
                <span>3. 압류·가압류·신탁 없음 확인</span>
                <span class="bass-badge ${seizeOk ? "bass-badge--ok" : "bass-badge--warn"}">${seizeOk ? "없음 ✓" : (trSub && tr.seizure_or_trust ? esc(tr.seizure_or_trust) : "열람 대기")}</span>
              </div>
            </div>
            <label class="cg-check ${s.step1_tenant_locked ? "is-checked" : (canTenantRegChk ? "is-actionable" : "")}">
              <span class="cg-check__label">
                <span><strong>임차인이 위 사실을 확인했음</strong></span>
                <span class="cg-check__hint">${s.step1_tenant_locked ? "임차인 직접 열람·확인 완료" : "캡처 AI OCR 승인 또는 모달에서 확인하면 체크됩니다"}</span>
              </span>
              <input type="checkbox" class="js-cg-reg-confirm" ${s.step1_tenant_locked ? "checked" : ""} ${canTenantRegChk ? "" : "disabled"}>
            </label>
          `}
          <div class="bass-muted" style="font-size:12px;margin-top:6px;">
            임대인 등기 제시: <strong>${s.step1_lessor_confirmed ? "제시 완료 ✓" : "대기 중"}</strong> (소유자 ${esc(lr.owner_name || s.lessor_name || "-")} · 채권최고액 ${fmtWon(lr.mortgage_amount)})
          </div>
        </div>`);
      parts.push(...msgs1.map(renderMsg));
    }

    // 앱 개입 카드 ③: 표준계약서 내용 확인 (옵션, 도배상태 등 세부내역 메모 포함) & 양측 동의
    if (s.current_step >= 2 || isRen) {
      const stdDone = Boolean(s.step3_lessor_agreed && s.step3_tenant_agreed);
      const optCount = (s.option_checks || []).length;
      const photoCount = (s.facility_photos || []).length;
      const stdStepTarget = (!isRen && s.current_step === 2) ? 2 : 3;
      const canLessorAgree = canAct && currentRole === "lessor" && !s.step3_lessor_agreed;
      const canTenantAgree = canAct && currentRole === "tenant" && !s.step3_tenant_agreed;
      parts.push(`
        <div class="cg-card ${stdDone ? "cg-card--done" : ((s.current_step === 2 || s.current_step === 3) ? "cg-card--active" : "")}">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ${isRen ? "① 표준계약서 확인" : "⑤ 표준계약서 내용 확인"}</span>
            <span class="bass-badge ${stdDone ? "bass-badge--ok" : "bass-badge--warn"}">${stdDone ? "양측 동의 완료 ✓" : `Revision v${s.revision}`}</span>
          </div>
          <div class="cg-card__msg">
            ${isRen
              ? "<strong>비대면 연장 재계약을 시작합니다.</strong> 아래 링크에서 표준계약서 내용을 좌우·상하로 스크롤해 확인한 뒤 동의해 주세요."
              : "아래 <strong>[ 표준계약서 내용 확인 ]</strong> 링크를 눌러 좌우·상하 스크롤 모달에서 계약서 전문과 <strong>옵션·도배상태 등 세부내역 메모</strong>를 확인하고 동의해 주세요."}
          </div>
          <div class="cg-link-row">
            <button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="${stdStepTarget}">
              📄 [ 표준계약서 내용 확인 ] ↗
            </button>
            ${!isRen ? `<button type="button" class="cg-link-btn js-cg-open-step" data-step="2">🛋️ 옵션·도배상태 메모 (${optCount}개·사진 ${photoCount}장) ↗</button>` : ""}
            ${canAct && currentRole === "lessor" && s.current_step <= 3 ? `<button type="button" class="cg-link-btn js-cg-bundle">📋 조건 일괄 수정</button>` : ""}
          </div>
          ${!isRen ? `
            <div class="cg-memo-chip">
              <strong>📝 옵션 · 도배상태 등 세부내역 메모:</strong> ${esc(s.facility_notes || "기록 없음")}
              <div style="margin-top:3px;font-size:12px;">
                세부 점검 확인: 임대인 ${s.step2_lessor_confirmed ? "완료 ✓" : "대기"} · 임차인 ${s.step2_tenant_locked ? "완료 ✓" : "대기"}
              </div>
            </div>` : ""}
          <div class="bass-muted" style="font-size:12.5px;margin:4px 0 6px;line-height:1.5;">
            보증금 <strong>${fmtWon(tm.deposit)}</strong> · 월세 <strong>${fmtWon(tm.monthly_rent)}</strong> · 관리비 <strong>${fmtWon(tm.maintenance_fee)}</strong>${Number(tm.cleaning_fee || 0) > 0 ? ` · 청소비 <strong>${fmtWon(tm.cleaning_fee)}</strong>` : ""} · ${isRen ? "연장 시작" : "입주"} ${esc(tm.move_in_date || "-")} (${Number(tm.lease_term_months || 12)}개월)
          </div>
          <label class="cg-check ${s.step3_lessor_agreed ? "is-checked" : (canLessorAgree ? "is-actionable" : "")}">
            <span class="cg-check__label">
              <span><strong>임대인은 표준계약서 내용에 동의</strong></span>
              <span class="cg-check__hint">${s.step3_lessor_agreed ? "임대인 동의 완료" : "모달에서 표준계약서를 확인 후 동의해 주세요"}</span>
            </span>
            <input type="checkbox" class="js-cg-std-agree" data-role="lessor" ${s.step3_lessor_agreed ? "checked" : ""} ${canLessorAgree ? "" : "disabled"}>
          </label>
          <label class="cg-check ${s.step3_tenant_agreed ? "is-checked" : (canTenantAgree ? "is-actionable" : "")}">
            <span class="cg-check__label">
              <span><strong>임차인은 표준계약서 내용에 동의</strong></span>
              <span class="cg-check__hint">${s.step3_tenant_agreed ? "임차인 동의 완료" : "모달에서 표준계약서를 확인 후 동의해 주세요"}</span>
            </span>
            <input type="checkbox" class="js-cg-std-agree" data-role="tenant" ${s.step3_tenant_agreed ? "checked" : ""} ${canTenantAgree ? "" : "disabled"}>
          </label>
        </div>`);
      parts.push(...msgs23.map(renderMsg));
    }

    // 앱 개입 카드 ④: 상기 내용에 동의합니다 + 날짜 + 임대인/임차인 서명
    if (s.current_step >= 4) {
      const sigDone = Boolean(s.lessor_signature && s.tenant_signature);
      parts.push(`
        <div class="cg-card ${sigDone ? "cg-card--done" : (s.current_step === 4 ? "cg-card--active" : "")}">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ${isRen ? "② 전자서명" : "⑥ 전자서명"}</span>
            <span class="bass-badge ${sigDone ? "bass-badge--ok" : "bass-badge--warn"}">${sigDone ? "양측 서명 완료 ✓" : "서명 진행 중"}</span>
          </div>
          <div class="cg-sign-paper">
            <div class="cg-sign-paper__title">상기 내용에 동의합니다.</div>
            <div class="cg-sign-paper__date">${formatKoreanDateDot()}</div>
            <div class="cg-sign-row">
              <span class="cg-sign-row__who"><span class="bass-muted">임대인</span> <strong>${esc(s.lessor_name || "-")}</strong></span>
              <span class="cg-sign-row__sig">
                ${s.lessor_signature
                  ? `${sigHtml(s.lessor_signature)} <span class="bass-badge bass-badge--ok">서명 완료 ✓</span>`
                  : (canAct && currentRole === "lessor"
                    ? `<button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="4">✍️ 임대인 서명하기 ↗</button>`
                    : `<span class="bass-muted">서명 대기</span>`)}
              </span>
            </div>
            <div class="cg-sign-row">
              <span class="cg-sign-row__who"><span class="bass-muted">임차인</span> <strong>${esc(tm.tenant_name || "임차인")}</strong></span>
              <span class="cg-sign-row__sig">
                ${s.tenant_signature
                  ? `${sigHtml(s.tenant_signature)} <span class="bass-badge bass-badge--ok">서명 완료 ✓</span>`
                  : (canAct && currentRole === "tenant"
                    ? `<button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="4">✍️ 임차인 서명하기 ↗</button>`
                    : `<span class="bass-muted">서명 대기</span>`)}
              </span>
            </div>
          </div>
          <div class="cg-link-row" style="margin-top:8px;">
            <button type="button" class="cg-link-btn js-cg-open-step" data-step="3">📄 표준계약서 다시 보기 ↗</button>
          </div>
        </div>`);
      parts.push(...msgs4.map(renderMsg));
    }

    // 앱 개입 카드 ⑤: 계약금 입금 확인 & 최종 체결
    if (s.current_step >= 5 && active) {
      parts.push(`
        <div class="cg-card cg-card--active">
          <div class="cg-card__top">
            <span class="cg-card__tag">🤖 스마트 계약 안내 · ⑦ 계약금 확인 및 최종 체결</span>
            <span class="bass-badge bass-badge--warn">${s.tenant_recheck_at ? "임대인 입금 확인 대기" : "임차인 등기부 재열람 필요"}</span>
          </div>
          <div class="cg-card__msg">
            🏦 <strong>임대인 수납 계좌:</strong> ${acctLabel(s)}<br>
            💸 <strong>계약금(보증금의 10%):</strong> <strong>${fmtWon(tm.contract_deposit)}</strong>
          </div>
          <div class="cg-link-row">
            ${currentRole === "lessor"
              ? `<button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="5">💰 계약금 수납 확인 & 계약 종료 ↗</button>`
              : `<button type="button" class="cg-link-btn cg-link-btn--primary js-cg-open-step" data-step="5">${s.tenant_recheck_at ? "📄 계약금 송금 · 체결 상태 보기 ↗" : "🔍 체결 직전 등기부 재열람 확인 모달 ↗"}</button>`}
          </div>
        </div>`);
      parts.push(...msgs5.map(renderMsg));
    }

    return parts.join("");
  }

  function renderWsActionBar(s) {
    renderPartyHeader(s);
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
    if (s.status === "PAUSED") {
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">⏸️ 보류 상태(${s.current_step}단계 보관) · 채팅 대화는 계속 가능하며 [▶️ 재개하기]를 누르면 이어서 진행합니다.</div>`;
      btns = !adminProxy && currentRole
        ? `<button type="button" id="wsBtnResume" class="bass-btn bass-btn--sm bass-btn--primary">▶️ 스마트 계약 재개하기</button>`
        : "";
    } else if (s.call_status === "CALLING") {
      const callee = currentRole !== s.initiated_by_role;
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">💬 1:1 방문 협의 채팅 진행 중 — 아래 채팅으로 방문 일정을 먼저 협의한 뒤, 현장에서 대면하면 스마트 계약을 개시하세요.</div>`;
      btns = callee && !adminProxy
        ? `<button type="button" id="wsBtnRespondNow" class="bass-btn bass-btn--sm bass-btn--primary">🤝 대면 완료 · 스마트 계약 개시</button>
           <button type="button" class="bass-btn bass-btn--sm js-ws-step" data-step="0">📋 상세 연결 정보</button>`
        : `<button type="button" class="bass-btn bass-btn--sm js-ws-step" data-step="0">📋 초대 QR · 사전 작성</button>`;
    } else if (step === 1) {
      const myIdDone = currentRole === "lessor" ? Boolean(s.lessor_checked_tenant_id) : Boolean(s.tenant_checked_lessor_id);
      const myRegDone = currentRole === "lessor" ? Boolean(s.step1_lessor_confirmed) : Boolean(s.step1_tenant_locked);
      const tenantRegSubmitted = Boolean(s.tenant_registry && s.tenant_registry.submitted);
      const bvBar = s.bank_verify || {};
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">
        ① 신분증: ${s.lessor_checked_tenant_id && s.tenant_checked_lessor_id ? "✅" : "⏳"} &nbsp;|&nbsp;
        ②~③ 계좌·1원: ${isBankDone(s) ? "✅" : (bvBar.to_lessor_verified || bvBar.to_tenant_verified ? "1/2" : "⏳")} &nbsp;|&nbsp;
        ④ 등기부: 임대인 ${s.step1_lessor_confirmed ? "✅" : "⏳"} · 임차인 ${s.step1_tenant_locked ? "✅" : "⏳"}
      </div>`;
      btns = `
        ${!adminProxy && currentRole ? `<button type="button" id="wsBtnIdCheck" class="bass-btn bass-btn--sm ${myIdDone ? "" : "bass-btn--primary"}">${myIdDone ? "✅ 신원 확인 완료" : "🪪 상호 신원 확인 체크"}</button>` : ""}
        <button type="button" class="bass-btn bass-btn--sm ${myRegDone ? "" : "bass-btn--primary"} js-ws-step" data-step="1">${currentRole === "tenant" ? "📄 법원 등기부 열람 · 승인 모달" : "📄 등기부 제시 · 확인 모달"}</button>
        ${!adminProxy && currentRole === "tenant" && tenantRegSubmitted && !myRegDone ? `<button type="button" id="wsBtnRegCheck" class="bass-btn bass-btn--sm bass-btn--primary">✅ 등기부 확인 체크</button>` : ""}`;
    } else if (step === 2) {
      const myOptDone = currentRole === "lessor" ? Boolean(s.step2_lessor_confirmed) : Boolean(s.step2_tenant_locked);
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">
        표준계약서 · 옵션·벽지 상태 메모 확인: 임대인 ${s.step2_lessor_confirmed ? "✅" : "⏳"} · 임차인 ${s.step2_tenant_locked ? "✅" : "⏳"}
      </div>`;
      btns = `<button type="button" class="bass-btn bass-btn--sm ${myOptDone ? "" : "bass-btn--primary"} js-ws-step" data-step="2">📄 표준계약서 · 옵션·벽지 메모 확인 모달</button>`;
    } else if (step === 3) {
      const canAgree = !adminProxy && currentRole && S.chatSummary.open_count === 0;
      const agreedMine = currentRole === "lessor" ? s.step3_lessor_agreed : s.step3_tenant_agreed;
      extraInfo = isRen
        ? `<div class="bass-muted" style="font-size:12px;margin-top:4px;">🔁 연장 계약은 표준계약서 확인 동의 → 서명 날인으로 바로 종료됩니다.</div>`
        : `<div class="bass-muted" style="font-size:12px;margin-top:4px;">표준계약서 동의: 임대인 ${s.step3_lessor_agreed ? "✅" : "⏳"} · 임차인 ${s.step3_tenant_agreed ? "✅" : "⏳"}</div>`;
      btns = `
        <button type="button" class="bass-btn bass-btn--sm ${agreedMine ? "" : "bass-btn--primary"} js-ws-step" data-step="3">📄 표준계약서 내용 확인 모달</button>
        ${!adminProxy && currentRole ? `<button type="button" id="wsBtnAgree3" class="bass-btn bass-btn--sm" ${canAgree ? "" : "disabled"}>${agreedMine ? "✅ 동의 완료 (상대 대기)" : (isRen ? "✅ 연장 조건 동의" : "✅ 표준계약서 동의")}</button>` : ""}
        ${!adminProxy && currentRole === "lessor" ? `<button type="button" id="wsBtnBundle" class="bass-btn bass-btn--sm">📋 조건 수정</button>` : ""}`;
    } else if (step === 4) {
      const mySigDone = currentRole === "lessor" ? Boolean(s.lessor_signature) : Boolean(s.tenant_signature);
      extraInfo = `<div class="bass-muted" style="font-size:12px;margin-top:4px;">
        서명 날인: 임대인 ${s.lessor_signature ? "✅" : "⏳"} · 임차인 ${s.tenant_signature ? "✅" : "⏳"}
      </div>`;
      btns = `
        <button type="button" class="bass-btn bass-btn--sm ${mySigDone ? "" : "bass-btn--primary"} js-ws-step" data-step="4">${mySigDone ? "✅ 서명 완료 (상대 대기)" : "✍️ 서명 날인하기"}</button>
        ${!adminProxy && currentRole && s.status === "IN_PROGRESS" ? `<button type="button" id="wsBtnReopen" class="bass-btn bass-btn--sm">📋 서명 전 조건 수정</button>` : ""}`;
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
    const rcBar = isRen ? S.renewalContext : null;
    const rcLine = rcBar ? `<div class="bass-muted" style="font-size:12px;margin-top:3px;">기존 계약: 보 ${fmtWon(rcBar.prev_deposit)} · 월 ${fmtWon(rcBar.prev_monthly_rent)} · 관 ${fmtWon(rcBar.prev_maintenance_fee)}${rcBar.notice_sent ? " · <strong>문자 예고 조건 반영</strong>" : ""} (5% 상한 월 ${fmtWon(rcBar.max_monthly_rent)})</div>` : "";
    const termsSummary = `<div class="bass-muted" style="font-size:12.5px;margin-top:5px;line-height:1.5;background:#f8fafc;padding:5px 9px;border-radius:9px;">
      보증금 <strong>${fmtWon(tm.deposit)}</strong>${cdepText} · 월세 <strong>${fmtWon(tm.monthly_rent)}</strong> · 관리비 <strong>${fmtWon(tm.maintenance_fee)}</strong>${Number(tm.cleaning_fee || 0) > 0 ? ` · 청소비 <strong>${fmtWon(tm.cleaning_fee)}</strong>` : ""} · ${isRen ? "연장 시작" : "입주"} ${esc(tm.move_in_date || "-")} (${Number(tm.lease_term_months || 12)}개월)${rcLine}
    </div>`;
    bar.innerHTML = `
      <div class="ws-action-card__head">
        <strong style="font-size:13.5px;">${esc(prog.label)}</strong>
        <span class="bass-badge ${t.mine ? "bass-badge--warn" : "bass-badge--info"}">${esc(t.text)}</span>
      </div>
      ${termsSummary}
      ${extraInfo}
      <div class="ws-action-card__btns">${btns}</div>`;

    bar.querySelectorAll(".js-ws-step").forEach((b) => {
      b.addEventListener("click", () => {
        const st = Number(b.dataset.step || 0);
        C.openStepModal(st || s.current_step, { forceDoc: true });
      });
    });
    const btnWsRespond = document.getElementById("wsBtnRespondNow");
    if (btnWsRespond && C.respondToIncomingCall) {
      btnWsRespond.addEventListener("click", function () { C.respondToIncomingCall(this, s.id); });
    }
    const btnWsResume = document.getElementById("wsBtnResume");
    if (btnWsResume) {
      btnWsResume.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/resume`, { method: "POST", body: { role: currentRole } });
          S.activeSession = res.session;
          Bass.updateAppAlert("success", res.message);
          C.renderWorkspace();
        });
      });
    }
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
    const btnReopenWs = document.getElementById("wsBtnReopen");
    if (btnReopenWs) {
      btnReopenWs.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          if (!window.confirm("서명 전 계약 조건을 수정할까요?\n양측의 조건 확인(및 기존 서명)이 초기화되고 조건 수정 단계로 돌아갑니다.")) return;
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/reopen-terms`, {
            method: "POST",
            body: { role: currentRole, confirm: true },
          });
          S.activeSession = res.session;
          S.lastRevision = S.activeSession.revision;
          S.viewStep = S.activeSession.current_step;
          Bass.updateAppAlert(res.level || "diff", res.message);
          C.renderWorkspace();
          if (currentRole === "lessor") openBundleDialog(S.activeSession);
        });
      });
    }
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
      S.latestChatMessages = d.messages || [];
      const note = document.getElementById("wsChatNote");
      const comp = document.getElementById("wsChatComposer");
      if (comp) comp.hidden = d.read_only || adminProxy || !currentRole;
      if (note) note.textContent = d.purged ? d.purged_note : (d.read_only ? "종료된 계약방입니다(읽기 전용)." : "채팅창 안에서 상대방과 대화하며 앱이 제시하는 링크(등기부등본·표준계약서 모달)를 눌러 확인·승인하세요.");
      const s = S.activeSession;
      const tr = s.tenant_registry || {};
      const tm = s.terms || {};
      const bvSig = s.bank_verify || {};
      const sessionSig = [
        s.id, s.status, s.call_status, s.current_step, s.revision,
        s.paused_at || "", s.registry_check_mode || "", (s.registry_ai_result && s.registry_ai_result.analyzed_at) || "",
        s.lessor_account || "", s.tenant_refund_account || "", s.bank_verify_mode || "",
        bvSig.to_lessor_code || "", bvSig.to_tenant_code || "",
        Boolean(bvSig.to_lessor_code_issued), Boolean(bvSig.to_tenant_code_issued),
        bvSig.to_lessor_sent_at || "", bvSig.to_tenant_sent_at || "",
        Boolean(bvSig.to_lessor_verified), Boolean(bvSig.to_tenant_verified),
        Boolean(bvSig.skip_lessor_agreed), Boolean(bvSig.skip_tenant_agreed), bvSig.skip_reason || "",
        s.lessor_checked_tenant_id, s.tenant_checked_lessor_id,
        s.step1_lessor_confirmed, s.step1_tenant_locked,
        Boolean(tr.submitted), tr.owner_name || "", tr.mortgage_amount ?? "", tr.seizure_or_trust || "",
        s.step2_lessor_confirmed, s.step2_tenant_locked,
        s.facility_notes || "", (s.facility_photos || []).length,
        s.step3_lessor_agreed, s.step3_tenant_agreed,
        Boolean(s.lessor_signature), Boolean(s.tenant_signature),
        s.tenant_recheck_at || "",
        tm.deposit || 0, tm.monthly_rent || 0, tm.maintenance_fee || 0, tm.cleaning_fee || 0, tm.move_in_date || "", tm.lease_term_months || 12,
      ].join("|");
      const sig = sessionSig + "::" + JSON.stringify((d.messages || []).map((m) => [m.id, m.status]));
      const activeInBankInput = Boolean(document.activeElement && list.contains(document.activeElement) && document.activeElement.classList.contains("js-cg-bank-input"));
      if (sig !== S.wsChatLastSig && !activeInBankInput) {
        const firstRender = !S.wsChatLastSig;
        S.wsChatLastSig = sig;
        const atBottom = firstRender || (list.scrollHeight - list.scrollTop - list.clientHeight < 60);
        list.innerHTML = buildSmartChatTimelineHtml(s, d.messages || [], d);
        if (atBottom) list.scrollTop = list.scrollHeight;

        list.querySelectorAll(".cm-act").forEach((btn) => btn.addEventListener("click", () => {
          Bass.withLock(btn, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/messages/${btn.dataset.id}/respond`, { method: "POST", body: { action: btn.dataset.act } });
            Bass.updateAppAlert("success", res.message);
            await refreshInlineChat(true);
            await C.fetchActiveSession(true);
          });
        }));

        list.querySelectorAll(".js-cg-open-step").forEach((btn) => btn.addEventListener("click", () => {
          const st = Number(btn.dataset.step || 0);
          C.openStepModal(st || S.activeSession.current_step, { forceDoc: true });
        }));

        list.querySelectorAll(".js-cg-respond-now").forEach((btn) => btn.addEventListener("click", function () {
          if (C.respondToIncomingCall && S.activeSession) {
            C.respondToIncomingCall(this, S.activeSession.id);
          }
        }));

        list.querySelectorAll(".js-cg-iros").forEach((btn) => btn.addEventListener("click", () => {
          if (C.openIrosGuideModal) {
            C.openIrosGuideModal(S.activeSession);
            return;
          }
          const curS = S.activeSession;
          const addr = `${curS.building_address || ""} ${curS.room_no}호`.trim();
          const w = window.open("https://www.iros.go.kr", "_blank");
          if (w) { try { w.opener = null; } catch (_) {} }
          if (window.R3 && R3.copyText) {
            R3.copyText(addr).then((ok) => {
              Bass.updateAppAlert("success", ok ? `주소(${addr})를 복사하고 법원등기소(iros.go.kr)를 열었습니다.` : "법원등기소(iros.go.kr)를 열었습니다.");
            });
          }
        }));

        list.querySelectorAll(".js-cg-id-chk").forEach((chk) => chk.addEventListener("change", () => {
          const role = chk.dataset.role;
          if (!role || role !== currentRole) return;
          const nextChecked = chk.checked;
          chk.disabled = true;
          Bass.api(APP_ID, `/sessions/${S.activeSession.id}/verify-id`, {
            method: "POST",
            body: { role: currentRole, checked: nextChecked },
          }).then((res) => {
            S.activeSession = res.session;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          }).catch((err) => {
            chk.checked = !nextChecked;
            chk.disabled = false;
            Bass.showError(err);
          });
        }));

        list.querySelectorAll(".js-cg-reg-confirm").forEach((chk) => chk.addEventListener("click", (ev) => {
          ev.preventDefault();
          const curS = S.activeSession;
          if (!curS || currentRole !== "tenant" || curS.step1_tenant_locked) return;
          if (!(curS.tenant_registry && curS.tenant_registry.submitted)) {
            Bass.updateAppAlert("info", "등기부등본 확인 모달에서 열람 내용을 입력하고 승인해 주세요.");
            C.openStepModal(1, { forceDoc: true });
            return;
          }
          chk.disabled = true;
          Bass.api(APP_ID, `/sessions/${curS.id}/step1`, {
            method: "POST",
            body: { role: "tenant", checked_counterparty_id: Boolean(curS.tenant_checked_lessor_id) },
          }).then((res) => {
            S.activeSession = res.session;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          }).catch((err) => {
            chk.disabled = false;
            Bass.showError(err);
          });
        }));

        list.querySelectorAll(".js-cg-std-agree").forEach((chk) => chk.addEventListener("click", (ev) => {
          ev.preventDefault();
          const curS = S.activeSession;
          const role = chk.dataset.role;
          if (!curS || role !== currentRole) return;
          if (curS.mode !== "RENEWAL" && curS.current_step === 2) {
            Bass.updateAppAlert("info", "표준계약서 모달에서 옵션·도배상태 메모를 먼저 확인 완료해 주세요.");
            C.openStepModal(2, { forceDoc: true });
            return;
          }
          if (S.chatSummary.open_count > 0) {
            Bass.updateAppAlert("warning", "답하지 않은 계약 조건 제안이 있습니다. 채팅에서 먼저 수락·거절해 주세요.");
            return;
          }
          if (S.reviewedStdContractRev !== curS.revision) {
            Bass.updateAppAlert("info", "좌우·상하로 스크롤해 표준계약서 전문을 확인한 뒤 하단에서 동의해 주세요.");
            C.openStepModal(3, { forceDoc: true });
            return;
          }
          chk.disabled = true;
          Bass.api(APP_ID, `/sessions/${curS.id}/step3/agree`, {
            method: "POST",
            body: { role: currentRole, expected_revision: curS.revision },
          }).then((res) => {
            S.activeSession = res.session;
            S.lastRevision = S.activeSession.revision;
            S.viewStep = S.activeSession.current_step;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          }).catch((err) => {
            chk.disabled = false;
            Bass.showError(err);
          });
        }));

        list.querySelectorAll(".js-cg-bundle").forEach((btn) => btn.addEventListener("click", () => {
          openBundleDialog(S.activeSession);
        }));

        list.querySelectorAll(".js-cg-resume").forEach((btn) => btn.addEventListener("click", function () {
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/resume`, {
              method: "POST",
              body: { role: currentRole },
            });
            S.activeSession = res.session;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          });
        }));

        list.querySelectorAll(".js-cg-reg-opt").forEach((chk) => chk.addEventListener("change", () => {
          const curS = S.activeSession;
          if (!curS || currentRole !== "tenant") return;
          const want = chk.checked;
          if (!want) {
            const ok = window.confirm("등기부등본 캡처·AI 검토를 생략하고 진행할까요?\n생략해도 전자서명 전까지 체크박스를 다시 켜서 언제든 검토할 수 있습니다.");
            if (!ok) { chk.checked = true; return; }
          }
          chk.disabled = true;
          Bass.api(APP_ID, `/sessions/${curS.id}/registry-preference`, {
            method: "POST",
            body: { role: "tenant", want_check: want, skip_acknowledged: !want },
          }).then((res) => {
            S.activeSession = res.session;
            S.viewStep = S.activeSession.current_step;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          }).catch((err) => {
            chk.checked = !want;
            chk.disabled = false;
            Bass.showError(err);
          });
        }));

        list.querySelectorAll(".js-cg-reg-file").forEach((inp) => inp.addEventListener("change", () => {
          const f = inp.files && inp.files[0];
          if (!f) return;
          const box = inp.closest(".cg-ocr-box");
          const consentChk = box && box.querySelector(".js-cg-reg-ai-consent");
          runInlineRegistryOcr(f, !consentChk || consentChk.checked).finally(() => { inp.value = ""; });
        }));

        list.querySelectorAll(".js-cg-reg-ai-apply").forEach((btn) => btn.addEventListener("click", function () {
          const curS = S.activeSession;
          const ex = curS && curS.registry_ai_result && curS.registry_ai_result.extracted;
          if (!curS || !ex) return;
          const lrNow = curS.registry_data || {};
          const tmNow = curS.terms || {};
          const ownerTarget = lrNow.owner_name || curS.lessor_name || "";
          const ownerDiff = Boolean(ownerTarget && ex.owner_name && normSimpleName(ownerTarget) !== normSimpleName(ex.owner_name));
          const isJeonse = tmNow.lease_type === "전세" || Number(tmNow.monthly_rent || 0) === 0;
          const highDep = isJeonse || Number(tmNow.deposit || 0) > 50000000;
          const lowDepRisk = !isJeonse && Number(tmNow.deposit || 0) <= 20000000 && Number(ex.mortgage_total || 0) > 0;
          if (ownerDiff || ex.seizure_or_trust === "있음" || highDep || lowDepRisk) {
            Bass.updateAppAlert("warning", "권리관계 확인 또는 보증보험 선택이 필요해 상세 대조 모달을 열었습니다.");
            C.openStepModal(1, { forceDoc: true });
            return;
          }
          Bass.withLock(this, async () => {
            const regPayload = {
              viewed_directly: true,
              viewed_at: ex.viewed_at || nowLocalInputStr(),
              owner_name: ex.owner_name || ownerTarget,
              mortgage_amount: Number(ex.mortgage_total || 0),
              seizure_or_trust: ex.seizure_or_trust || "없음",
              issue_number: ex.issue_number || "",
              mismatch_ack: true,
            };
            await Bass.api(APP_ID, `/sessions/${curS.id}/tenant-registry`, {
              method: "POST",
              body: { registry: regPayload },
            });
            const res = await Bass.api(APP_ID, `/sessions/${curS.id}/step1`, {
              method: "POST",
              body: { role: "tenant", checked_counterparty_id: Boolean(curS.tenant_checked_lessor_id) },
            });
            S.activeSession = res.session;
            S.viewStep = S.activeSession.current_step;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          });
        }));

        // 계좌번호 등록 · 상호 1원 송수금(난수 4자리 코드) 인라인 이벤트 바인딩
        list.querySelectorAll(".js-cg-bank-input").forEach((inp) => {
          inp.addEventListener("focus", () => { S.isEditingForm = true; });
          inp.addEventListener("blur", () => { setTimeout(() => { S.isEditingForm = false; }, 250); });
        });

        list.querySelectorAll(".js-cg-copy-txt").forEach((btn) => btn.addEventListener("click", () => {
          const val = btn.dataset.copy || "";
          const lbl = btn.dataset.label || "내용";
          if (window.R3 && R3.copyText) {
            R3.copyText(val).then((ok) => Bass.updateAppAlert(ok ? "success" : "warning", ok ? `${lbl}(${val})을(를) 복사했습니다.` : "복사에 실패했습니다."));
          }
        }));

        list.querySelectorAll(".js-cg-save-acct").forEach((btn) => btn.addEventListener("click", function () {
          const role = btn.dataset.role;
          if (!role || role !== currentRole) return;
          const bankEl = document.getElementById(role === "lessor" ? "cgLessorBank" : "cgTenantBank");
          const noEl = document.getElementById(role === "lessor" ? "cgLessorAccNo" : "cgTenantAccNo");
          const bank_name = (bankEl && bankEl.value || "").trim();
          const account_no = (noEl && noEl.value || "").trim();
          if (!bank_name || !account_no) {
            Bass.updateAppAlert("warning", "은행명과 계좌번호를 모두 입력해 주세요.");
            return;
          }
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/bank-account`, {
              method: "POST",
              body: { role: currentRole, bank_name, account_no },
            });
            S.activeSession = res.session;
            S.isEditingForm = false;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          });
        }));

        list.querySelectorAll(".js-cg-1won-sent").forEach((btn) => btn.addEventListener("click", function () {
          const chk = document.getElementById("cgHolderNameChk");
          if (!chk || !chk.checked) {
            Bass.updateAppAlert("warning", "먼저 은행 앱 송금 화면에서 상대방 예금주(실명) 일치를 확인해 체크해 주세요.");
            return;
          }
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/bank-verify/sent`, {
              method: "POST",
              body: { role: currentRole, holder_name_checked: true },
            });
            S.activeSession = res.session;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          });
        }));

        list.querySelectorAll(".js-cg-1won-confirm").forEach((btn) => btn.addEventListener("click", function () {
          const inp = document.getElementById("cgOneWonCodeInput");
          const code = (inp && inp.value || "").trim();
          if (!code) {
            Bass.updateAppAlert("warning", "본인 통장 1원 입금 내역에 찍힌 숫자 4자리를 입력해 주세요.");
            return;
          }
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/bank-verify/confirm`, {
              method: "POST",
              body: { role: currentRole, code },
            });
            S.activeSession = res.session;
            S.isEditingForm = false;
            if (currentRole === "tenant") await C.loadIdentity();
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          });
        }));

        list.querySelectorAll(".js-cg-1won-reissue").forEach((btn) => btn.addEventListener("click", function () {
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/bank-verify/reissue`, {
              method: "POST",
              body: { role: currentRole },
            });
            S.activeSession = res.session;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          });
        }));

        list.querySelectorAll(".js-cg-bank-skip").forEach((chk) => chk.addEventListener("change", () => {
          const role = chk.dataset.role;
          if (!role || role !== currentRole) return;
          const agree = chk.checked;
          const reasonEl = document.getElementById("cgBankSkipReason");
          const reason = (reasonEl && reasonEl.value || "").trim();
          chk.disabled = true;
          Bass.api(APP_ID, `/sessions/${S.activeSession.id}/bank-verify/skip`, {
            method: "POST",
            body: { role: currentRole, agree, reason },
          }).then((res) => {
            S.activeSession = res.session;
            S.isEditingForm = false;
            Bass.updateAppAlert("success", res.message);
            C.renderWorkspace();
          }).catch((err) => {
            chk.checked = !agree;
            chk.disabled = false;
            Bass.showError(err);
          });
        }));
      }
      S.chatSummary = { unread: d.unread || 0, open_count: d.open_count || 0 };
      updateChatFab();
      updateTurnLine();
    } catch (_) { /* 다음 폴링에서 재시도 */ }
    finally { wsChatBusy = false; }
  }

  async function runInlineRegistryOcr(file, consent) {
    const curS = S.activeSession;
    if (!curS) return;
    if (file.size > 10 * 1024 * 1024) {
      Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다.");
      return;
    }
    if (!consent) {
      Bass.updateAppAlert("warning", "AI 등기부 OCR 분석 전송 동의 체크 후 올려 주세요.");
      return;
    }
    Bass.updateAppAlert("info", "🤖 AI가 등기부등본 캡처를 읽고 있습니다...");
    try {
      const dataUrl = await readFileDataUrl(file);
      const res = await Bass.api(APP_ID, `/sessions/${curS.id}/registry-ai`, {
        method: "POST",
        body: { attachment_data_url: dataUrl, consent: true },
      });
      if (res.session) S.activeSession = res.session;
      Bass.updateAppAlert(res.level === "success" ? "success" : "warning", res.message);
      C.renderWorkspace();
    } catch (err) {
      Bass.showError(err);
    }
  }

  // 채팅창에서 Ctrl+V 로 등기부 캡처 이미지를 바로 붙여넣어 AI OCR 검토
  document.addEventListener("paste", (ev) => {
    const curS = S.activeSession;
    if (!curS || D.elContract.hidden || S.stepModalOpen) return;
    if (curS.status !== "IN_PROGRESS" || curS.mode === "RENEWAL" || curS.current_step !== 1) return;
    if (currentRole !== "tenant" || curS.step1_tenant_locked || curS.registry_check_mode === "SKIPPED_BY_TENANT") return;
    const items = (ev.clipboardData && ev.clipboardData.items) || [];
    for (const it of items) {
      if (it.type && it.type.startsWith("image/")) {
        const f = it.getAsFile();
        if (f) {
          ev.preventDefault();
          const consentChk = document.querySelector(".js-cg-reg-ai-consent");
          runInlineRegistryOcr(f, !consentChk || consentChk.checked);
          return;
        }
      }
    }
  });

  function openAiExplainModal() {
    const curS = S.activeSession;
    if (!curS) return;
    const body = R3.openModal("🤖 AI 쉬운 스마트 계약 요약", `<div class="bass-muted" style="font-size:13px;padding:12px 0;">AI가 현재 계약 조건·특약·권리관계를 쉬운 말로 정리하고 있습니다...</div>`);
    Bass.api(APP_ID, `/sessions/${curS.id}/ai-assist`, {
      method: "POST",
      body: { action: "explain_contract" },
    }).then((res) => {
      const r = res.result || {};
      const bullets = (r.bullets || []).map((b) => `<li style="margin:4px 0;">${esc(b)}</li>`).join("");
      const cautions = (r.cautions || []).map((c) => `<li style="margin:4px 0;color:#92400e;">${esc(c)}</li>`).join("");
      body.innerHTML = `
        <div class="cg-ai-summary" style="margin-top:0;">
          <div style="font-weight:800;color:#1e3a8a;margin-bottom:4px;">📋 한눈에 보는 계약 요약 <span class="bass-badge bass-badge--info" style="margin-left:4px;">${r.ai_enhanced ? "Gemini AI" : "표준 분석"}</span></div>
          <div>${esc(r.summary || "")}</div>
        </div>
        ${bullets ? `<div style="margin-top:10px;"><strong style="font-size:13px;">핵심 조건 체크</strong><ul style="margin:6px 0 0 18px;padding:0;font-size:13px;line-height:1.55;">${bullets}</ul></div>` : ""}
        ${cautions ? `<div style="margin-top:10px;padding:10px 12px;border-radius:10px;background:#fffbeb;border:1px solid #fde68a;"><strong style="font-size:13px;color:#92400e;">⚠️ 서명 전 꼭 확인할 사항</strong><ul style="margin:6px 0 0 18px;padding:0;font-size:12.5px;line-height:1.55;">${cautions}</ul></div>` : ""}
        <div class="bass-muted" style="font-size:12px;margin-top:10px;">※ AI 요약은 이해를 돕기 위한 참고용이며, 법적 효력은 표준계약서 본문에 따릅니다.</div>`;
    }).catch((err) => {
      body.innerHTML = `<div class="bass-muted" style="font-size:13px;color:var(--err);">${esc(err.message || "요약을 불러오지 못했습니다.")}</div>`;
    });
  }

  function openAiClauseModal() {
    const curS = S.activeSession;
    if (!curS) return;
    const body = R3.openModal("✨ AI 특약 다듬기 (문해력 도우미)", `
      <p class="bass-muted" style="font-size:12.5px;margin:0 0 8px;">평소 말투로 합의 내용을 적으면 분쟁 없는 공정한 표준 임대차 특약 문구로 다듬어 드립니다.</p>
      <textarea id="aiClauseIn" class="bass-input" rows="3" placeholder="예: 고양이 1마리 키우는데 벽지 긁으면 원상복구하고 퇴실 청소비 10만원 낼게요"></textarea>
      <button id="aiClauseRun" type="button" class="bass-btn bass-btn--primary" style="width:100%;margin-top:8px;">✨ 표준 특약으로 다듬기</button>
      <div id="aiClauseOut" style="margin-top:10px;"></div>`);
    body.querySelector("#aiClauseRun").addEventListener("click", function () {
      const txt = body.querySelector("#aiClauseIn").value.trim();
      if (!txt) { Bass.updateAppAlert("warning", "특약으로 정리할 내용을 입력해 주세요."); return; }
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/sessions/${curS.id}/ai-assist`, {
          method: "POST",
          body: { action: "draft_clause", text: txt },
        });
        const r = res.result || {};
        const out = body.querySelector("#aiClauseOut");
        out.innerHTML = `
          <div class="cg-ai-summary">
            <div style="font-weight:800;color:#1e3a8a;margin-bottom:4px;">📝 추천 표준 특약 문구</div>
            <div id="aiClauseText" style="white-space:pre-line;font-weight:600;">${esc(r.clause || "")}</div>
            ${r.explanation ? `<div class="bass-muted" style="font-size:12px;margin-top:6px;">💡 ${esc(r.explanation)}</div>` : ""}
          </div>
          <div class="bass-row" style="gap:6px;margin-top:8px;flex-wrap:wrap;">
            <button type="button" id="aiClauseCopy" class="bass-btn bass-btn--sm">📋 복사</button>
            <button type="button" id="aiClauseSend" class="bass-btn bass-btn--sm bass-btn--primary">💬 채팅에 특약 제안 보내기</button>
          </div>`;
        out.querySelector("#aiClauseCopy").addEventListener("click", () => {
          R3.copyText(r.clause || "").then((ok) => Bass.updateAppAlert(ok ? "success" : "warning", ok ? "특약 문구를 복사했습니다." : "복사에 실패했습니다."));
        });
        out.querySelector("#aiClauseSend").addEventListener("click", function () {
          Bass.withLock(this, async () => {
            const canReq = curS.status === "IN_PROGRESS" && curS.current_step <= 3;
            await Bass.api(APP_ID, `/sessions/${curS.id}/messages`, {
              method: "POST",
              body: canReq
                ? { kind: "change_request", field: "special_terms", body: r.clause || txt }
                : { kind: "question", body: `[AI 특약 초안] ${r.clause || txt}` },
            });
            R3.closeModal();
            Bass.updateAppAlert("success", "다듬은 특약 문구를 계약 채팅방에 보냈습니다.");
            await refreshInlineChat(true);
            await C.fetchActiveSession(true);
          });
        });
      });
    });
  }

  function openAiOcrMemoModal() {
    const curS = S.activeSession;
    if (!curS) return;
    const body = R3.openModal("📷 메모 · 기존 계약서 사진 OCR", `
      <p class="bass-muted" style="font-size:12.5px;margin:0 0 8px;">손글씨 메모, 중개사 안내지, 기존 계약서 사진을 올리면 AI OCR이 보증금·월세·관리비·청소비·입주일·특약을 자동으로 읽어 계약 조건으로 채워 드립니다.</p>
      <input id="aiOcrFile" type="file" accept="image/*" class="bass-input">
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;margin:8px 0;cursor:pointer;">
        <input id="aiOcrConsent" type="checkbox" checked>
        <span>선택한 사진이 AI(Google Gemini) OCR 분석에 전송되는 것에 동의합니다 (분석 전용, 서버 미보관)</span>
      </label>
      <button id="aiOcrRun" type="button" class="bass-btn bass-btn--primary" style="width:100%;">🔍 AI로 조건 읽어오기</button>
      <div id="aiOcrOut" style="margin-top:10px;"></div>`);
    body.querySelector("#aiOcrRun").addEventListener("click", function () {
      const inp = body.querySelector("#aiOcrFile");
      const f = inp.files && inp.files[0];
      if (!f) { Bass.updateAppAlert("warning", "사진 파일을 먼저 선택해 주세요."); return; }
      if (f.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "사진은 10MB 이하만 가능합니다."); return; }
      if (!body.querySelector("#aiOcrConsent").checked) { Bass.updateAppAlert("warning", "AI OCR 전송 동의에 체크해 주세요."); return; }
      Bass.withLock(this, async () => {
        const dataUrl = await readFileDataUrl(f);
        const res = await Bass.api(APP_ID, `/sessions/${curS.id}/ai-assist`, {
          method: "POST",
          body: { action: "ocr_memo", image_data_url: dataUrl, consent: true },
        });
        const r = res.result || {};
        const ex = r.extracted || {};
        const out = body.querySelector("#aiOcrOut");
        if (r.status !== "ok") {
          out.innerHTML = `<div class="bass-muted" style="font-size:12.5px;color:#b45309;">⚠️ ${esc(r.message || "읽지 못했습니다.")}</div>`;
          return;
        }
        const rows = [];
        if (ex.deposit != null) rows.push(`보증금 ${fmtWon(ex.deposit)}`);
        if (ex.monthly_rent != null) rows.push(`월세 ${fmtWon(ex.monthly_rent)}`);
        if (ex.maintenance_fee != null) rows.push(`관리비 ${fmtWon(ex.maintenance_fee)}`);
        if (ex.cleaning_fee != null) rows.push(`퇴실 청소비 ${fmtWon(ex.cleaning_fee)}`);
        if (ex.move_in_date) rows.push(`입주일 ${ex.move_in_date}`);
        if (ex.lease_term_months) rows.push(`계약기간 ${ex.lease_term_months}개월`);
        const canBundle = currentRole === "lessor" && curS.status === "IN_PROGRESS" && curS.current_step <= 3 && rows.length > 0;
        out.innerHTML = `
          <div class="cg-ai-summary">
            <div style="font-weight:800;color:#1e3a8a;margin-bottom:4px;">✅ AI OCR 추출 결과</div>
            <div>${rows.length ? esc(rows.join(" · ")) : "금액·날짜 항목 없음"}</div>
            ${ex.special_terms ? `<div style="margin-top:4px;">특약: ${esc(ex.special_terms)}</div>` : ""}
            ${ex.facility_notes ? `<div style="margin-top:4px;">시설 메모: ${esc(ex.facility_notes)}</div>` : ""}
            ${ex.summary ? `<div class="bass-muted" style="font-size:12px;margin-top:4px;">${esc(ex.summary)}</div>` : ""}
          </div>
          <div class="bass-row" style="gap:6px;margin-top:8px;flex-wrap:wrap;">
            ${canBundle ? `<button type="button" id="aiOcrApplyBundle" class="bass-btn bass-btn--sm bass-btn--primary">📋 채팅방에 조건 일괄 제안 올리기</button>` : ""}
            <button type="button" id="aiOcrShareChat" class="bass-btn bass-btn--sm">💬 OCR 요약 채팅에 공유</button>
          </div>`;
        const btnB = out.querySelector("#aiOcrApplyBundle");
        if (btnB) {
          btnB.addEventListener("click", function () {
            Bass.withLock(this, async () => {
              const tNow = curS.terms || {};
              const isRen = curS.mode === "RENEWAL";
              const dep = ex.deposit != null ? Number(ex.deposit) : Number(tNow.deposit || 0);
              const bundle = {
                deposit: dep,
                contract_deposit: isRen ? 0 : Math.floor(dep / 10),
                monthly_rent: ex.monthly_rent != null ? Number(ex.monthly_rent) : Number(tNow.monthly_rent || 0),
                maintenance_fee: ex.maintenance_fee != null ? Number(ex.maintenance_fee) : Number(tNow.maintenance_fee || 0),
                cleaning_fee: ex.cleaning_fee != null ? Number(ex.cleaning_fee) : Number(tNow.cleaning_fee || 0),
                move_in_date: ex.move_in_date || tNow.move_in_date || "",
                lease_term_months: ex.lease_term_months || Number(tNow.lease_term_months || 12),
              };
              await Bass.api(APP_ID, `/sessions/${curS.id}/messages`, {
                method: "POST",
                body: { kind: "bundle", body: "📷 AI OCR로 읽은 계약 조건입니다. 확인 후 수락해 주세요.", bundle },
              });
              R3.closeModal();
              Bass.updateAppAlert("success", "OCR로 읽은 조건을 채팅방에 일괄 제안했습니다.");
              await refreshInlineChat(true);
              await C.fetchActiveSession(true);
            });
          });
        }
        out.querySelector("#aiOcrShareChat").addEventListener("click", function () {
          Bass.withLock(this, async () => {
            const shareText = `[📷 AI OCR 메모 정리] ${rows.join(" / ")}${ex.special_terms ? ` · 특약: ${ex.special_terms}` : ""}${ex.facility_notes ? ` · 시설: ${ex.facility_notes}` : ""}`;
            await Bass.api(APP_ID, `/sessions/${curS.id}/messages`, {
              method: "POST",
              body: { kind: "question", body: shareText },
            });
            R3.closeModal();
            Bass.updateAppAlert("success", "OCR 요약 내용을 채팅방에 공유했습니다.");
            await refreshInlineChat(true);
          });
        });
      });
    });
  }

  const btnAiExplain = document.getElementById("wsAiExplain");
  if (btnAiExplain) btnAiExplain.addEventListener("click", openAiExplainModal);
  const btnAiClause = document.getElementById("wsAiClause");
  if (btnAiClause) btnAiClause.addEventListener("click", openAiClauseModal);
  const btnAiOcr = document.getElementById("wsAiOcr");
  if (btnAiOcr) btnAiOcr.addEventListener("click", openAiOcrMemoModal);

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
