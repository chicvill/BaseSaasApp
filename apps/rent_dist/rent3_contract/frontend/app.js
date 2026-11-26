/* apps/rent_dist/rent3_contract/frontend/app.js — 계약방 진입·세션 폴링·단계 모달 전환 오케스트레이터. */
(function () {
  "use strict";
  const C = window.R3C;
  if (!C) return;

  const { APP_ID, params, adminProxy, currentRole, ROLE_KO, OTHER_KO, state: S, dom: D, esc } = C;

  // ───────────────────────── 계약방 진입 / 복귀 ─────────────────────────
  async function openSession(id) {
    S.activeSessionId = id;
    S.activeSession = null;
    S.lastRevision = null;
    S.lastCallStatus = null;
    S.modalKey = "";
    S.lastStamp = "";
    S.modalAnimating = false;
    S.wsChatLastSig = "";
    clearTimeout(S.modalTimer);
    D.elCard.classList.remove("is-open", "is-closing");
    D.elHome.hidden = true;
    D.elContract.hidden = false;
    if (window.R3Notify) R3Notify.placeBell("#bellCvSlot");
    window.scrollTo(0, 0);
    await fetchActiveSession(true);
  }

  function goHome() {
    S.activeSessionId = "";
    S.activeSession = null;
    S.modalKey = "";
    S.stepModalOpen = false;
    S.wsChatLastSig = "";
    clearTimeout(S.modalTimer);
    D.elModal.hidden = true;
    D.elContract.hidden = true;
    D.elHome.hidden = false;
    if (window.R3Notify) { R3Notify.placeBell("#bellHomeSlot"); R3Notify.refresh(); }
    C.loadRoleExtras().then(() => C.loadSessionList()).catch(Bass.showError);
  }
  document.getElementById("btnBackHome").addEventListener("click", goHome);

  // 체결 전 계약 취소: 사유를 적으면 상대방 화면에 그대로 전달되고 호실이 다시 열린다
  document.getElementById("btnCancelContract").addEventListener("click", function () {
    Bass.withLock(this, async () => {
      if (!S.activeSession) return;
      const reason = (window.prompt("취소 사유를 입력해 주세요. 상대방 화면에 그대로 표시됩니다.") || "").trim();
      if (!reason) { Bass.updateAppAlert("warning", "사유를 입력해야 취소할 수 있습니다."); return; }
      if (!window.confirm("정말 이 계약을 취소할까요? 취소하면 되돌릴 수 없습니다.")) return;
      const res = await Bass.api(APP_ID, `/sessions/${S.activeSession.id}/cancel`, { method: "POST", body: { role: currentRole, reason } });
      S.activeSession = res.session;
      Bass.updateAppAlert("success", res.message);
      renderWorkspace();
    });
  });

  async function fetchActiveSession(forceRender = false) {
    if (!S.activeSessionId) return;
    const res = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(S.activeSessionId)}`);
    const prevStep = S.activeSession ? S.activeSession.current_step : 0;
    const prevStatus = S.activeSession ? S.activeSession.status : "";
    S.activeSession = res.session;
    S.sealedContract = res.contract;
    S.chatSummary = res.chat || S.chatSummary;
    C.updateChatFab();
    C.updateTurnLine();

    const idx = S.allSessions.findIndex((x) => x.id === S.activeSession.id);
    if (idx >= 0) S.allSessions[idx] = S.activeSession;

    if (forceRender || S.activeSession.current_step !== prevStep) S.viewStep = S.activeSession.current_step;
    if (prevStep > 0 && S.activeSession.current_step !== prevStep) S.stepModalOpen = false;

    if (
      forceRender ||
      S.lastRevision !== S.activeSession.revision ||
      S.lastCallStatus !== S.activeSession.call_status ||
      prevStep !== S.activeSession.current_step ||
      prevStatus !== S.activeSession.status ||
      !S.isEditingForm
    ) {
      S.lastRevision = S.activeSession.revision;
      S.lastCallStatus = S.activeSession.call_status;
      renderWorkspace();
    }
  }

  // ───────────────────────── 단계 모달 라우팅 ─────────────────────────
  function planModal(s) {
    if (s.status === "CANCELLED") {
      return { key: `${s.id}:cancelled`, render: () => C.renderCancelled(s) };
    }
    if (s.call_status === "CALLING") {
      const callee = currentRole !== s.initiated_by_role;
      return { key: `${s.id}:call:${callee ? "respond" : "wait"}`, render: () => C.renderCallHandshake(s) };
    }
    const step = S.viewStep;
    if (step === 5 && s.current_step === 5 && currentRole === "tenant" && !s.tenant_recheck_at) {
      return { key: `${s.id}:s5:recheck`, render: () => { C.renderRecheckTenant(s); C.decoratePanel(s); } };
    }
    if (step === s.current_step) {
      const prog = C.stepProgress(s, step);
      const mine = prog[currentRole];
      const blocked = C.blockedMessage(s, step);
      if (mine || blocked) {
        return { key: `${s.id}:s${step}:wait`, render: () => { renderWaiting(s, step, prog, mine, blocked); C.decoratePanel(s); } };
      }
    }
    return { key: `${s.id}:s${step}:act`, render: () => { renderStepN(s, step); C.decoratePanel(s); } };
  }

  function renderStepN(s, step) {
    if (step === 1) C.renderStep1(s);
    else if (step === 2) C.renderStep2(s);
    else if (step === 3) C.renderStep3(s);
    else if (step === 4) C.renderStep4(s);
    else C.renderStep5(s);
  }

  function renderWaiting(s, step, prog, mine, blocked) {
    const dot = (role, done) => `<span class="dotline__item"><span class="dot ${done ? "dot--ok" : "dot--wait"}"></span>${role === "lessor" ? "임대인" : "임차인"}${role === currentRole ? " (나)" : ""}</span>`;
    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 6px;">${mine ? "✅ 내 차례를 마쳤습니다" : `⏳ ${OTHER_KO}의 차례입니다`}</h3>
      <p class="bass-muted" style="font-size:13px;margin:0 0 6px;">
        ${esc(blocked || `${OTHER_KO}이 [${prog.label}]을 마치면 자동으로 다음 단계가 열립니다. 이 화면을 닫지 말고 기다려 주세요.`)}
      </p>
      <div class="dotline">${dot("lessor", prog.lessor)}${dot("tenant", prog.tenant)}</div>`;
  }

  function positionModal() {
    D.elModal.style.top = "0px";
  }
  window.addEventListener("resize", positionModal);
  window.addEventListener("scroll", positionModal, { passive: true });
  if (window.ResizeObserver) new ResizeObserver(positionModal).observe(document.body);

  function openCardAnim() {
    D.elCard.classList.remove("is-closing", "is-open");
    void D.elCard.offsetWidth;
    D.elCard.classList.add("is-open");
  }

  function openStepModal(stepNum) {
    if (stepNum) S.viewStep = stepNum;
    S.stepModalOpen = true;
    S.modalKey = "";
    renderWorkspace();
  }
  const btnCloseStepModal = document.getElementById("btnCloseStepModal");
  if (btnCloseStepModal) {
    btnCloseStepModal.addEventListener("click", () => {
      S.stepModalOpen = false;
      D.elModal.hidden = true;
    });
  }
  document.querySelectorAll("#stepperBar .step-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      if (!S.activeSession || S.activeSession.status === "COMPLETED") return;
      const n = Number(pill.dataset.step);
      if (n && n <= S.activeSession.current_step) openStepModal(n);
    });
  });

  function switchModal(s) {
    if (S.modalAnimating) return;
    if (!S.stepModalOpen) {
      D.elModal.hidden = true;
      return;
    }
    const plan = planModal(s);
    D.elModal.hidden = false;
    positionModal();
    const chatSummary = S.chatSummary;
    const stamp = `${s.updated_at || ""}:${s.revision}:${S.stepModalOpen}:${chatSummary.open_count}`;
    if (plan.key === S.modalKey) {
      if (stamp !== S.lastStamp) { S.lastStamp = stamp; plan.render(); }
      return;
    }
    S.lastStamp = stamp;
    const first = S.modalKey === "";
    S.modalKey = plan.key;
    if (first) { plan.render(); openCardAnim(); return; }
    S.modalAnimating = true;
    D.elCard.classList.remove("is-open");
    D.elCard.classList.add("is-closing");
    clearTimeout(S.modalTimer);
    S.modalTimer = setTimeout(() => {
      S.modalAnimating = false;
      if (!S.activeSession || !S.stepModalOpen) return;
      planModal(S.activeSession).render();
      openCardAnim();
      D.elModal.scrollTop = 0;
    }, 210);
  }

  function renderWorkspace() {
    if (!S.activeSession) return;
    D.elHome.hidden = true;
    D.elContract.hidden = false;
    if (window.R3Notify) R3Notify.placeBell("#bellCvSlot");
    const s = S.activeSession;
    document.getElementById("wsTitle").textContent = `${s.building_name} ${s.room_no}호`;
    R3Shell.proxyBanner(`${s.building_name} ${s.room_no}호 (${ROLE_KO[currentRole]} 입장 · 계약방)`);
    document.getElementById("wsMeta").textContent =
      `${s.mode === "RENEWAL" ? "재계약" : "신규 계약"} · 나는 ${ROLE_KO[currentRole]}${s.reserve_after ? ` · 📅 퇴실 전 사전 계약 (입주 ${s.reserve_after} 이후)` : ""}`;

    document.getElementById("btnCancelContract").hidden = s.status !== "IN_PROGRESS";
    C.updateTurnLine();
    C.updateChatFab();
    const callBadge = document.getElementById("callStateBadge");
    if (s.status === "COMPLETED") {
      callBadge.className = "bass-badge bass-badge--ok";
      callBadge.textContent = "체결 완료";
    } else if (s.call_status === "CALLING") {
      callBadge.className = "bass-badge bass-badge--warn";
      callBadge.textContent = "응답 대기";
    } else {
      callBadge.className = "bass-badge bass-badge--ok";
      callBadge.textContent = "연결됨";
    }

    const isRenewal = s.mode === "RENEWAL";
    const stepperBar = document.getElementById("stepperBar");
    if (stepperBar) stepperBar.style.gridTemplateColumns = isRenewal ? "repeat(3, 1fr)" : "repeat(5, 1fr)";
    const newLabels = { 1: "① 본인확인", 2: "② 옵션점검", 3: "③ 조건", 4: "④ 서명", 5: "⑤ 체결" };
    const renLabels = { 3: "① 조건(5%)", 4: "② 서명", 5: "③ 체결" };
    document.querySelectorAll(".step-pill").forEach((pill) => {
      const n = Number(pill.dataset.step);
      pill.hidden = isRenewal && n < 3;
      pill.textContent = isRenewal ? (renLabels[n] || "") : (newLabels[n] || "");
      const connected = s.call_status !== "CALLING";
      pill.classList.toggle("is-now", connected && s.status !== "COMPLETED" && n === s.current_step);
      pill.classList.toggle("is-done", connected && (n < s.current_step || s.status === "COMPLETED"));
    });

    if (s.status === "COMPLETED") {
      if (D.elChatWs) D.elChatWs.hidden = true;
      S.stepModalOpen = false;
      D.elModal.hidden = true;
      S.modalKey = "";
      C.renderSealedPaper();
      return;
    }
    if (D.elChatWs) {
      D.elChatWs.hidden = false;
      C.renderWsActionBar(s);
      C.refreshInlineChat(true);
    }
    switchModal(s);
    C.renderSealedPaper();
  }

  Object.assign(C, { openSession, goHome, fetchActiveSession, openStepModal, renderWorkspace });

  // 🔔 알림창: 홈에서는 위 줄 오른쪽, 계약 화면에서는 머리말 오른쪽. 관리인 대리 접속에서는 달지 않는다.
  if (window.R3Notify && !adminProxy) {
    R3Notify.start({ url: "/api/rent3_contract/notifications", seenUrl: "/api/rent3_contract/notifications/seen", bellHost: "#bellHomeSlot", openSupport: () => R3Shell.openSupportChat() });
  }

  (async function init() {
    try {
      await C.loadCandidateRooms();
      await C.loadRoleExtras();
      const list = await Bass.api(APP_ID, "/sessions");
      S.allSessions = list.sessions || [];
      C.renderHome(true);

      let sid = params.get("session");
      if (!sid) {
        try { sid = window.localStorage.getItem("rent3_pending_join") || ""; } catch (_) { sid = ""; }
      }
      try { window.localStorage.removeItem("rent3_pending_join"); } catch (_) {}
      const found = sid ? S.allSessions.find((x) => x.id === sid || x.session_code === sid) : null;
      if (found) {
        await openSession(found.id);
        if (params.get("chat") === "1") C.openChatDrawer();
      } else if (params.get("call_branch") && params.get("call_room")) {
        const val = `${params.get("call_branch")}|${params.get("call_room")}`;
        if (currentRole === "lessor") C.openLessorCallDialog(val);
        else C.openTenantPickerDialog();
      }

      setInterval(async () => {
        if (document.hidden) return;
        try {
          if (!D.elContract.hidden) {
            if (S.activeSessionId && !S.isEditingForm) await fetchActiveSession(false);
          } else if (!D.elDlgHost.firstChild) {
            const l = await Bass.api(APP_ID, "/sessions");
            S.allSessions = l.sessions || [];
            C.renderHome(false);
          }
        } catch (_) { /* 다음 주기에 재시도 */ }
      }, 2500);

      setInterval(async () => {
        if (document.hidden || !D.elContract.hidden) return;
        await C.loadRoleExtras();
        await C.loadCandidateRooms().catch(() => {});
        C.renderHome(false);
      }, 15000);
    } catch (err) {
      Bass.showError(err);
    }
  })();
})();
