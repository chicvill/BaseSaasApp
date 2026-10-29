/* apps/rent3_contract/frontend/app.js — 실시간 양방향 공동 임대계약 작성 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_contract";
  const params = new URLSearchParams(window.location.search);

  let currentRole = params.get("role") === "tenant" ? "tenant" : "lessor";
  let activeSessionId = params.get("session") || "";
  let activeSession = null;
  let sealedContract = null;
  let viewStep = 1;
  let lastRevision = null;
  let isEditingForm = false;

  const elRoleLessor = document.getElementById("btnRoleLessor");
  const elRoleTenant = document.getElementById("btnRoleTenant");
  const elSessionSelect = document.getElementById("sessionSelect");
  const elWorkspace = document.getElementById("sessionWorkspace");
  const elStepPanel = document.getElementById("stepPanel");
  const elSealedView = document.getElementById("sealedContractView");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  function setRole(role) {
    currentRole = role;
    elRoleLessor.classList.toggle("is-active", role === "lessor");
    elRoleTenant.classList.toggle("is-active", role === "tenant");
    if (activeSession) renderWorkspace();
  }

  elRoleLessor.addEventListener("click", () => setRole("lessor"));
  elRoleTenant.addEventListener("click", () => setRole("tenant"));
  setRole(currentRole);

  async function loadCandidateRooms() {
    const data = await Bass.api(APP_ID, "/rooms");
    const sel = document.getElementById("newRoomSelect");
    sel.innerHTML = data.rooms.map((r) => {
      const tag = r.is_occupied ? "입주중(재계약용)" : "공실(즉시계약)";
      return `<option value="${r.branch}|${r.room_no}">[${Bass.escapeHtml(r.building_name)}] ${Bass.escapeHtml(r.room_no)}호 — ${tag} (보 ${fmtWon(r.deposit)} / 월 ${fmtWon(r.monthly_rent)})</option>`;
    }).join("");
  }

  async function loadSessionList() {
    const data = await Bass.api(APP_ID, "/sessions");
    document.getElementById("poolBadge").textContent = `JSON Pool v${data.pool_revision}`;
    const list = data.sessions || [];
    if (!list.length) {
      elSessionSelect.innerHTML = `<option value="">개설된 공동 계약 세션 없음</option>`;
      return;
    }
    elSessionSelect.innerHTML = list.map((s) =>
      `<option value="${s.id}">[${s.status === "COMPLETED" ? "체결완료" : `Step ${s.current_step}/5`}] ${Bass.escapeHtml(s.building_name)} ${Bass.escapeHtml(s.room_no)}호 — 코드 ${s.session_code} (${Bass.escapeHtml(s.terms.tenant_name || "미정")})</option>`
    ).join("");

    if (!activeSessionId || !list.some((s) => s.id === activeSessionId)) {
      activeSessionId = list[0].id;
    }
    elSessionSelect.value = activeSessionId;
    await fetchActiveSession(true);
  }

  elSessionSelect.addEventListener("change", async () => {
    activeSessionId = elSessionSelect.value;
    lastRevision = null;
    await fetchActiveSession(true);
  });

  document.getElementById("btnJoinCode").addEventListener("click", function () {
    Bass.withLock(this, async () => {
      const code = document.getElementById("joinCodeInput").value.trim();
      if (!code) {
        Bass.updateAppAlert("warning", "6자리 참여코드를 입력해 주세요.");
        return;
      }
      const res = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(code)}`);
      activeSessionId = res.session.id;
      lastRevision = null;
      await loadSessionList();
      Bass.updateAppAlert("success", `${res.session.room_no}호 공동 계약방에 합류했습니다.`);
    });
  });

  document.getElementById("btnOpenSession").addEventListener("click", function () {
    Bass.withLock(this, async () => {
      const val = document.getElementById("newRoomSelect").value;
      const [branch, roomNo] = val.split("|");
      const mode = document.getElementById("newModeSelect").value;
      const tenantName = document.getElementById("newTenantName").value.trim();
      const tenantPhone = document.getElementById("newTenantPhone").value.trim();
      const res = await Bass.api(APP_ID, "/sessions", {
        method: "POST",
        body: { branch, room_no: roomNo, mode, tenant_name: tenantName, tenant_phone: tenantPhone },
      });
      activeSessionId = res.session.id;
      viewStep = 1;
      lastRevision = null;
      await loadSessionList();
      Bass.updateAppAlert("success", res.message, "임차인에게 6자리 참여코드를 알려주거나 한 화면에서 시점을 전환하며 함께 작성하세요.");
    });
  });

  document.getElementById("stepperBar").addEventListener("click", (e) => {
    const pill = e.target.closest(".step-pill");
    if (!pill || !activeSession) return;
    viewStep = Number(pill.dataset.step);
    renderWorkspace();
  });

  async function fetchActiveSession(forceRender = false) {
    if (!activeSessionId) return;
    const res = await Bass.api(APP_ID, `/sessions/${encodeURIComponent(activeSessionId)}`);
    document.getElementById("poolBadge").textContent = `JSON Pool v${res.pool_revision}`;
    const prevStep = activeSession ? activeSession.current_step : 1;
    const prevStatus = activeSession ? activeSession.status : "";
    activeSession = res.session;
    sealedContract = res.contract;

    if (forceRender) {
      viewStep = activeSession.current_step;
    } else if (activeSession.current_step !== prevStep) {
      viewStep = activeSession.current_step;
    }

    if (
      forceRender ||
      lastRevision !== activeSession.revision ||
      prevStep !== activeSession.current_step ||
      prevStatus !== activeSession.status ||
      !isEditingForm
    ) {
      lastRevision = activeSession.revision;
      renderWorkspace();
    }
  }

  function renderWorkspace() {
    if (!activeSession) return;
    elWorkspace.style.display = "block";
    const s = activeSession;
    document.getElementById("wsTitle").textContent = `${s.building_name} ${s.room_no}호 (${s.mode === "RENEWAL" ? "재계약" : "신규 직거래 계약"})`;
    document.getElementById("wsMeta").textContent = `소재지: ${s.building_address} · 임대인: ${s.lessor_name} · 현재 보는 시점: ${currentRole === "lessor" ? "🏢 임대인(갑)" : "🔑 임차인(을)"}`;
    document.getElementById("revBadge").textContent = `Revision v${s.revision}`;
    document.getElementById("codeBadge").textContent = `참여코드 ${s.session_code}`;

    document.querySelectorAll(".step-pill").forEach((pill) => {
      const n = Number(pill.dataset.step);
      pill.classList.toggle("is-now", n === viewStep);
      pill.classList.toggle("is-done", n < s.current_step || s.status === "COMPLETED");
    });

    if (viewStep === 1) renderStep1(s);
    else if (viewStep === 2) renderStep2(s);
    else if (viewStep === 3) renderStep3(s);
    else if (viewStep === 4) renderStep4(s);
    else renderStep5(s);

    renderSealedPaper();
  }

  function renderStep1(s) {
    const r = s.registry_data || {};
    elStepPanel.innerHTML = `
      <h3>① 대법원 등기부등본 권리관계 공동 확인</h3>
      <p class="bass-muted" style="font-size:13px;">임대인이 제시한 등기부 정보를 임차인이 직접 확인하고 잠금 승인해야 다음 단계로 이동합니다.</p>
      <div class="field-grid" style="margin:12px 0;">
        <div><label class="bass-label">발급확인번호</label><input id="regIssue" class="bass-input" value="${Bass.escapeHtml(r.issue_number || "")}" ${currentRole !== "lessor" ? "readonly" : ""}></div>
        <div><label class="bass-label">갑구 소유자 성명</label><input id="regOwner" class="bass-input" value="${Bass.escapeHtml(r.owner_name || "")}" ${currentRole !== "lessor" ? "readonly" : ""}></div>
        <div><label class="bass-label">을구 선순위 채권최고액(원)</label><input id="regMortgage" type="number" class="bass-input" value="${Number(r.mortgage_amount || 0)}" ${currentRole !== "lessor" ? "readonly" : ""}></div>
      </div>
      <div class="bass-row" style="margin-bottom:12px;">
        <span class="bass-badge ${s.step1_lessor_confirmed ? "bass-badge--ok" : "bass-badge--warn"}">임대인 등기 제시: ${s.step1_lessor_confirmed ? "완료" : "대기"}</span>
        <span class="bass-badge ${s.step1_tenant_locked ? "bass-badge--ok" : "bass-badge--warn"}">임차인 권리 확인 잠금: ${s.step1_tenant_locked ? "승인 잠금됨" : "미확인"}</span>
      </div>
      <button id="btnStep1Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ${currentRole === "lessor" ? "🏢 등기부 정보 제시/갱신 저장" : "🔑 등기부 소유권·근저당 이상 없음 확인 잠금"}
      </button>
    `;
    document.getElementById("btnStep1Confirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const body = { role: currentRole };
        if (currentRole === "lessor") {
          body.registry_updates = {
            issue_number: document.getElementById("regIssue").value.trim(),
            owner_name: document.getElementById("regOwner").value.trim(),
            mortgage_amount: Number(document.getElementById("regMortgage").value || 0),
          };
        }
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step1`, { method: "POST", body });
        activeSession = res.session;
        viewStep = activeSession.current_step;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep2(s) {
    const opts = s.option_checks || [];
    elStepPanel.innerHTML = `
      <h3>② 호실 상태 및 옵션 실물 공동 점검표</h3>
      <p class="bass-muted" style="font-size:13px;">입실 시 함께 확인한 시설 상태가 퇴실 정산 비교 원본으로 박제되어 억울한 원상복구 분쟁을 차단합니다.</p>
      <div style="margin:12px 0;">
        ${opts.map((o, idx) => `
          <label class="check-row">
            <span><strong>${Bass.escapeHtml(o.item)}</strong> (${o.quantity}개) — <span class="bass-muted">${Bass.escapeHtml(o.condition)}</span></span>
            <input type="checkbox" class="opt-chk" data-idx="${idx}" ${o.checked ? "checked" : ""}>
          </label>
        `).join("")}
      </div>
      <div>
        <label class="bass-label">현장 시설 특기사항 메모</label>
        <input id="facNotes" class="bass-input" value="${Bass.escapeHtml(s.facility_notes || "")}">
      </div>
      <div class="bass-row" style="margin:12px 0;">
        <span class="bass-badge ${s.step2_lessor_confirmed ? "bass-badge--ok" : "bass-badge--warn"}">임대인 확인: ${s.step2_lessor_confirmed ? "완료" : "대기"}</span>
        <span class="bass-badge ${s.step2_tenant_locked ? "bass-badge--ok" : "bass-badge--warn"}">임차인 확인 잠금: ${s.step2_tenant_locked ? "완료" : "대기"}</span>
      </div>
      <button id="btnStep2Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ${currentRole === "lessor" ? "🏢 시설 점검표 확인 완료" : "🔑 실물 옵션 이상 없음 상호 동의 잠금"}
      </button>
    `;
    document.getElementById("btnStep2Confirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const updatedOpts = opts.map((o, i) => ({
          ...o,
          checked: document.querySelector(`.opt-chk[data-idx="${i}"]`).checked,
        }));
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step2`, {
          method: "POST",
          body: {
            role: currentRole,
            option_checks: updatedOpts,
            facility_notes: document.getElementById("facNotes").value.trim(),
          },
        });
        activeSession = res.session;
        viewStep = activeSession.current_step;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep3(s) {
    const t = s.terms || {};
    elStepPanel.innerHTML = `
      <h3>③ 계약 조건 및 특약사항 공동 작성 (Revision v${s.revision})</h3>
      <p class="bass-muted" style="font-size:13px;">어느 한쪽이라도 금액·날짜·특약을 1글자라도 수정하면 리비전이 올라가고 기존 동의가 자동 해제됩니다(위변조 원천 차단).</p>
      <div class="field-grid" style="margin:12px 0;">
        <div><label class="bass-label">임차인 성명 *</label><input id="tName" class="bass-input term-input" value="${Bass.escapeHtml(t.tenant_name || "")}"></div>
        <div><label class="bass-label">임차인 연락처 *</label><input id="tPhone" class="bass-input term-input" value="${Bass.escapeHtml(t.tenant_phone || "")}"></div>
        <div><label class="bass-label">차량 번호 (주차 등록용)</label><input id="tCar" class="bass-input term-input" value="${Bass.escapeHtml(t.tenant_car_number || "")}" placeholder="예: 12가 3456"></div>
        <div><label class="bass-label">입주(전입)일</label><input id="tMoveIn" type="date" class="bass-input term-input" value="${Bass.escapeHtml(t.move_in_date || "")}"></div>
        <div>
          <label class="bass-label">임대 구분 / 납부 시점 (rent2 엔진 직결)</label>
          <select id="tPayTiming" class="bass-select term-input">
            <option value="후불" ${t.pay_timing === "후불" ? "selected" : ""}>월세 후불 (입주월 비과세 · 익월 청구)</option>
            <option value="선불" ${t.pay_timing === "선불" ? "selected" : ""}>월세 선불 (입주월부터 당월 청구)</option>
          </select>
        </div>
        <div><label class="bass-label">계약 기간(개월)</label><input id="tMonths" type="number" class="bass-input term-input" value="${Number(t.lease_term_months || 12)}"></div>
        <div><label class="bass-label">보증금(원)</label><input id="tDeposit" type="number" class="bass-input term-input" value="${Number(t.deposit || 0)}"></div>
        <div><label class="bass-label">월 임차료(원)</label><input id="tRent" type="number" class="bass-input term-input" value="${Number(t.monthly_rent || 0)}"></div>
        <div><label class="bass-label">월 관리비(원)</label><input id="tMaint" type="number" class="bass-input term-input" value="${Number(t.maintenance_fee || 0)}"></div>
        <div><label class="bass-label">당일 계약금(원)</label><input id="tConDep" type="number" class="bass-input term-input" value="${Number(t.contract_deposit || 0)}"></div>
      </div>
      <div>
        <label class="bass-label">상호 합의 특약사항</label>
        <textarea id="tSpecial" rows="4" class="bass-input term-input">${Bass.escapeHtml(t.special_terms || "")}</textarea>
      </div>
      <div class="bass-row" style="margin:12px 0;justify-content:space-between;">
        <div class="bass-row">
          <span class="bass-badge ${s.step3_lessor_agreed ? "bass-badge--ok" : "bass-badge--warn"}">임대인 합의: ${s.step3_lessor_agreed ? "잠금 완료" : "미동의"}</span>
          <span class="bass-badge ${s.step3_tenant_agreed ? "bass-badge--ok" : "bass-badge--warn"}">임차인 합의: ${s.step3_tenant_agreed ? "잠금 완료" : "미동의"}</span>
        </div>
        <button id="btnSaveTerms" class="bass-btn" type="button">✏️ 조건 수정 반영 (공동 화면 갱신)</button>
      </div>
      <button id="btnAgreeStep3" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        🔒 현재 조건(v${s.revision})에 ${currentRole === "lessor" ? "임대인(갑)" : "임차인(을)"} 최종 합의 잠금
      </button>
    `;

    elStepPanel.querySelectorAll(".term-input").forEach((inp) => {
      inp.addEventListener("focus", () => { isEditingForm = true; });
      inp.addEventListener("blur", () => { setTimeout(() => { isEditingForm = false; }, 400); });
    });

    const collectUpdates = () => ({
      tenant_name: document.getElementById("tName").value.trim(),
      tenant_phone: document.getElementById("tPhone").value.trim(),
      tenant_car_number: document.getElementById("tCar").value.trim(),
      move_in_date: document.getElementById("tMoveIn").value,
      pay_timing: document.getElementById("tPayTiming").value,
      lease_term_months: Number(document.getElementById("tMonths").value || 12),
      deposit: Number(document.getElementById("tDeposit").value || 0),
      monthly_rent: Number(document.getElementById("tRent").value || 0),
      maintenance_fee: Number(document.getElementById("tMaint").value || 0),
      contract_deposit: Number(document.getElementById("tConDep").value || 0),
      special_terms: document.getElementById("tSpecial").value,
    });

    document.getElementById("btnSaveTerms").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/terms`, {
          method: "POST",
          body: { role: currentRole, updates: collectUpdates() },
        });
        activeSession = res.session;
        lastRevision = activeSession.revision;
        isEditingForm = false;
        Bass.updateAppAlert(res.level || "success", res.message);
        renderWorkspace();
      });
    });

    document.getElementById("btnAgreeStep3").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        // 입력 중 수정값이 있으면 먼저 반영 검사
        const editRes = await Bass.api(APP_ID, `/sessions/${s.id}/terms`, {
          method: "POST",
          body: { role: currentRole, updates: collectUpdates() },
        });
        activeSession = editRes.session;
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step3/agree`, {
          method: "POST",
          body: { role: currentRole, expected_revision: activeSession.revision },
        });
        activeSession = res.session;
        lastRevision = activeSession.revision;
        viewStep = activeSession.current_step;
        isEditingForm = false;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep4(s) {
    const mySig = currentRole === "lessor" ? s.lessor_signature : s.tenant_signature;
    const defaultName = currentRole === "lessor" ? s.lessor_name : (s.terms.tenant_name || "임차인");
    elStepPanel.innerHTML = `
      <h3>④ 양측 본인확인 및 모바일 자필 전자서명</h3>
      <p class="bass-muted" style="font-size:13px;">양측 전자서명이 모두 완료되면 계약서 전문이 SHA-256 암호화 해시로 봉인됩니다.</p>
      <div class="bass-row" style="margin:10px 0;">
        <span class="bass-badge ${s.lessor_signature ? "bass-badge--ok" : "bass-badge--warn"}">임대인 서명: ${s.lessor_signature ? "날인 완료" : "대기"}</span>
        <span class="bass-badge ${s.tenant_signature ? "bass-badge--ok" : "bass-badge--warn"}">임차인 서명: ${s.tenant_signature ? "날인 완료" : "대기"}</span>
      </div>
      ${s.integrity_hash ? `<div class="bass-card" style="padding:12px;margin-bottom:12px;font-size:12px;">🔐 <strong>SHA-256 무결성 봉인 해시:</strong> <code>${Bass.escapeHtml(s.integrity_hash)}</code></div>` : ""}
      <label class="bass-label">${currentRole === "lessor" ? "🏢 임대인(갑)" : "🔑 임차인(을)"} 자필 서명 패드 (마우스/터치 드로잉)</label>
      <canvas id="sigCanvas" class="sig-pad" width="600" height="110"></canvas>
      <div class="field-grid" style="margin-top:10px;">
        <div>
          <label class="bass-label">정자 서명 성명 확인</label>
          <input id="sigText" class="bass-input" value="${Bass.escapeHtml(mySig && !mySig.startsWith("data:") ? mySig : `${defaultName} (자필전자서명)`)}">
        </div>
        <div class="bass-row" style="align-items:flex-end;">
          <button id="btnClearCanvas" class="bass-btn" type="button">지우기</button>
          <button id="btnSubmitSign" class="bass-btn bass-btn--primary" style="flex:1;" type="button">✍️ 전자서명 날인 완료</button>
        </div>
      </div>
    `;

    const canvas = document.getElementById("sigCanvas");
    const ctx = canvas.getContext("2d");
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#0f172a";
    let drawing = false;
    let hasStroke = false;

    const pos = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const pt = ev.touches ? ev.touches[0] : ev;
      return {
        x: ((pt.clientX - rect.left) / rect.width) * canvas.width,
        y: ((pt.clientY - rect.top) / rect.height) * canvas.height,
      };
    };
    canvas.addEventListener("pointerdown", (ev) => {
      drawing = true;
      hasStroke = true;
      isEditingForm = true;
      const p = pos(ev);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
    });
    canvas.addEventListener("pointermove", (ev) => {
      if (!drawing) return;
      const p = pos(ev);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    });
    window.addEventListener("pointerup", () => { drawing = false; });

    document.getElementById("btnClearCanvas").addEventListener("click", () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      hasStroke = false;
    });

    document.getElementById("btnSubmitSign").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const textSig = document.getElementById("sigText").value.trim();
        const sigPayload = hasStroke ? canvas.toDataURL("image/png") : textSig;
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step4/sign`, {
          method: "POST",
          body: { role: currentRole, signature: sigPayload, expected_revision: s.revision },
        });
        activeSession = res.session;
        isEditingForm = false;
        viewStep = activeSession.current_step;
        Bass.updateAppAlert("success", res.message);
        renderWorkspace();
      });
    });
  }

  function renderStep5(s) {
    const t = s.terms || {};
    const done = s.status === "COMPLETED";
    elStepPanel.innerHTML = `
      <h3>⑤ 계약금 입금 확인 & 공동 Data Pool 원장 일괄 생성</h3>
      <p class="bass-muted" style="font-size:13px;">체결 완료 즉시 <code>contracts.json</code>, <code>rooms.json</code>, <code>users.json</code>, <code>payments.json</code>, <code>ops.json(RTMS 확정일자)</code>가 실시간 업데이트됩니다.</p>
      <div class="bass-card" style="padding:14px;margin:12px 0;">
        <div><strong>입금 계좌:</strong> ${Bass.escapeHtml(s.lessor_account)} (예금주: ${Bass.escapeHtml(s.lessor_name)})</div>
        <div><strong>당일 계약금:</strong> ${fmtWon(t.contract_deposit)} / <strong>보증금 총액:</strong> ${fmtWon(t.deposit)} / <strong>월세+관리비:</strong> ${fmtWon(Number(t.monthly_rent || 0) + Number(t.maintenance_fee || 0))} (${Bass.escapeHtml(t.pay_timing)})</div>
        ${s.fixed_date_no ? `<div style="margin-top:6px;color:var(--ok);font-weight:700;">🏛️ 대법원 확정일자 자동 부여 완료: ${Bass.escapeHtml(s.fixed_date_no)}</div>` : ""}
      </div>
      ${done ? `
        <div class="bass-row">
          <button onclick="window.print()" class="bass-btn bass-btn--primary" type="button">🖨️ 표준 임대차 전자계약서 A4 인쇄 / PDF 저장</button>
          <a class="bass-btn" href="/rent3_lessor/">🏢 임대인 수납장부로 이동</a>
          <a class="bass-btn" href="/rent3_tenant/">🔑 임차인 마이홈으로 이동</a>
        </div>
      ` : `
        <div class="field-grid" style="margin-bottom:12px;">
          <div>
            <label class="bass-label">계약금 납부 수단</label>
            <select id="payMethodSel" class="bass-select">
              <option value="TRANSFER">계좌이체 실시간 입금</option>
              <option value="KAKAOPAY">카카오페이 간편송금</option>
              <option value="CARD">신용/체크카드 결제</option>
            </select>
          </div>
        </div>
        <button id="btnCompleteSeal" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
          🎉 계약금 입금 확인 & 임대차 계약 최종 체결 봉인
        </button>
      `}
    `;
    const btn = document.getElementById("btnCompleteSeal");
    if (btn) {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const method = document.getElementById("payMethodSel").value;
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/step5/complete`, {
            method: "POST",
            body: { payment_method: method },
          });
          activeSession = res.session;
          sealedContract = res.contract;
          Bass.updateAppAlert("success", res.message, "아래 생성된 표준 전자계약서를 인쇄하거나 임대인/임차인 앱에서 즉시 동기화된 장부를 확인하세요.");
          await loadSessionList();
          renderWorkspace();
        });
      });
    }
  }

  function renderSigCell(sig, fallbackName) {
    if (!sig) return `(미서명)`;
    if (sig.startsWith("data:image")) {
      return `<img src="${sig}" alt="전자서명" style="max-height:48px;vertical-align:middle;">`;
    }
    return `<strong>${Bass.escapeHtml(sig)}</strong>`;
  }

  function renderSealedPaper() {
    if (!activeSession || activeSession.status !== "COMPLETED" || !sealedContract) {
      elSealedView.style.display = "none";
      elSealedView.innerHTML = "";
      return;
    }
    const c = sealedContract;
    elSealedView.style.display = "block";
    elSealedView.innerHTML = `
      <article class="contract-paper">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #000;padding-bottom:10px;margin-bottom:14px;">
          <div>
            <h2 style="margin:0;">표준 부동산 임대차 전자계약서 (${Bass.escapeHtml(c.lease_type)})</h2>
            <div style="font-size:12px;">계약번호: ${Bass.escapeHtml(c.id)} · 체결일: ${Bass.escapeHtml(c.contract_date)}</div>
          </div>
          <div style="border:2px solid #b91c1c;color:#b91c1c;padding:6px 10px;font-weight:800;font-size:12px;text-align:center;">
            확정일자 부여필<br>${Bass.escapeHtml(c.fixed_date_no)}
          </div>
        </div>

        <table>
          <tr><th>목적물 소재지</th><td colspan="3">${Bass.escapeHtml(activeSession.building_address)} (${Bass.escapeHtml(activeSession.building_name)} ${Bass.escapeHtml(c.room_no)}호)</td></tr>
          <tr><th>보증금</th><td>${fmtWon(c.deposit)}</td><th>계약금(보증금 내)</th><td>${fmtWon(c.contract_deposit)} (영수함)</td></tr>
          <tr><th>월 임차료</th><td>${fmtWon(c.monthly_rent)} (${Bass.escapeHtml(c.pay_timing)})</td><th>월 관리비 / 부가세</th><td>${fmtWon(c.maintenance_fee)} / ${fmtWon(c.vat)}</td></tr>
          <tr><th>임대차 기간</th><td colspan="3">${Bass.escapeHtml(c.move_in_date)} ~ ${Bass.escapeHtml(c.expiry_date)} (${c.lease_term_months}개월)</td></tr>
          <tr><th>특약사항</th><td colspan="3" style="white-space:pre-line;">${Bass.escapeHtml(c.special_terms)}</td></tr>
          <tr><th>임대인(갑) 서명</th><td>${Bass.escapeHtml(c.lessor_name)} &nbsp; ${renderSigCell(c.lessor_signature, c.lessor_name)}</td><th>임차인(을) 서명</th><td>${Bass.escapeHtml(c.tenant_name)} (${Bass.escapeHtml(c.tenant_phone)}) &nbsp; ${renderSigCell(c.tenant_signature, c.tenant_name)}</td></tr>
          <tr><th>무결성 해시(SHA-256)</th><td colspan="3" style="font-family:monospace;font-size:11px;word-break:break-all;">${Bass.escapeHtml(c.integrity_hash)}</td></tr>
        </table>
      </article>
    `;
  }

  (async function init() {
    try {
      await loadCandidateRooms();
      await loadSessionList();
      setInterval(() => {
        if (!document.hidden && activeSessionId && !isEditingForm) {
          fetchActiveSession(false).catch(() => {});
        }
      }, 2500);
    } catch (err) {
      Bass.showError(err);
    }
  })();
})();

