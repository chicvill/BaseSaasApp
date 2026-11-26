/* apps/rent_dist/rent3_contract/frontend/steps.js — 호출 응답·사전작성·2~5단계 패널·전자계약서 인쇄본 렌더링. */
(function () {
  "use strict";
  const C = window.R3C;
  if (!C) return;

  const {
    APP_ID, adminProxy, currentRole, state: S, dom: D,
    esc, acctLabel, autoPayGuideHtml, fmtWon,
    pairDots, needsGuarantee, mortgageExposure, riskBlock,
  } = C;

  // 서명 이후 조건 변경은 '조건 다시 열기' 하나로만 한다
  function decoratePanel(s) {
    if (adminProxy || s.status !== "IN_PROGRESS" || s.call_status === "CALLING" || s.current_step < 4) return;
    const box = document.createElement("div");
    box.className = "reopen-box";
    box.innerHTML = `<button id="btnReopen" class="bass-btn bass-btn--sm" type="button">↩️ 조건 다시 열기</button>
      <div class="bass-muted" style="font-size:12px;margin-top:4px;">조건을 바꿔야 할 때만 누르세요. 양측 서명과 3단계 동의가 초기화되고 3단계로 돌아갑니다.</div>`;
    D.elStepPanel.appendChild(box);
    box.querySelector("#btnReopen").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        if (!window.confirm("조건을 다시 열까요?\n양측 서명과 3단계 동의가 초기화되고 3단계로 돌아갑니다.")) return;
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/reopen-terms`, { method: "POST", body: { role: currentRole, confirm: true } });
        S.activeSession = res.session;
        S.viewStep = S.activeSession.current_step;
        S.lastRevision = S.activeSession.revision;
        Bass.updateAppAlert(res.level || "diff", res.message);
        C.renderWorkspace();
        await C.fetchActiveSession(true);
      });
    });
  }

  function renderCallHandshake(s) {
    const callerIsLessor = s.initiated_by_role === "lessor";
    const callerKo = callerIsLessor ? `임대인(${s.lessor_name})` : `임차인(${(s.terms && s.terms.tenant_name) || "예비임차인"})`;
    const calleeKo = callerIsLessor ? `임차인(${(s.terms && s.terms.tenant_name) || "예비임차인"})` : `임대인(${s.lessor_name})`;
    const isCalleeView = currentRole !== s.initiated_by_role;

    D.elStepPanel.innerHTML = `
      <h3>📞 상대방 연결</h3>
      <p class="bass-muted" style="font-size:13px;">먼저 입장한 쪽이 호출하고, 상대가 로그인해 응답하면 시작됩니다.</p>

      <div class="id-verify-grid">
        <div class="id-card-box ${s.lessor_login_verified ? "is-verified" : ""}">
          <div style="font-weight:800; margin-bottom:4px;">🏢 임대인(갑): ${esc(s.lessor_name)}</div>
          <div class="bass-muted" style="font-size:12px;">연락처: ${esc(s.lessor_phone || "-")}</div>
          <div style="margin-top:8px;">
            <span class="bass-badge ${s.lessor_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.lessor_login_verified ? "🛡️ 로그인 본인인증 완료 (호출자)" : "⏳ 로그인 호출 응답 대기"}
            </span>
          </div>
        </div>

        <div class="id-card-box ${s.tenant_login_verified ? "is-verified" : ""}">
          <div style="font-weight:800; margin-bottom:4px;">🔑 임차인(을): ${esc((s.terms && s.terms.tenant_name) || "응답 시 자동입력")}</div>
          <div class="bass-muted" style="font-size:12px;">연락처: ${esc((s.terms && s.terms.tenant_phone) || "-")}</div>
          <div style="margin-top:8px;">
            <span class="bass-badge ${s.tenant_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.tenant_login_verified ? "🛡️ 로그인 본인인증 완료 (호출자)" : "⏳ 로그인 호출 응답 대기"}
            </span>
          </div>
        </div>
      </div>

      ${isCalleeView ? `
        <div class="call-banner" style="margin-top:12px; margin-bottom:0;">
          <div style="font-weight:800; margin-bottom:6px;">
            🔔 ${esc(callerKo)}님이 [${esc(s.building_name)} ${esc(s.room_no)}호] 공동계약을 호출했습니다!
          </div>
          <div class="bass-muted" style="font-size:12.5px; margin-bottom:10px;">
            응답하면 ① 신분증·등기부 확인 단계가 시작됩니다.
          </div>
          <button id="btnRespondMain" type="button" class="bass-btn bass-btn--primary" style="width:100%;">
            ✅ 호출에 응답하고 공동계약 시작하기 (${currentRole === "lessor" ? "임대인" : "임차인"} 본인인증 연결)
          </button>
        </div>
      ` : `
        <div class="bass-card" style="padding:14px; margin-top:12px; border:1px dashed var(--border-strong);">
          <div style="font-weight:800; margin-bottom:4px;">📡 ${esc(calleeKo)}님의 호출 응답을 기다리고 있습니다...</div>
          <div class="bass-muted" style="font-size:12.5px;">
            상대가 응답하면 자동으로 다음 화면이 열립니다.
          </div>
        </div>
      `}
    `;

    renderPrefill(s);
    if (!isCalleeView) renderJoinQr(s, calleeKo);

    const btnResp = document.getElementById("btnRespondMain");
    if (btnResp) {
      btnResp.addEventListener("click", function () {
        C.respondToIncomingCall(this, s.id);
      });
    }
  }

  // ───────────── 대면 전 사전 작성 (상대가 연결하기 전) ─────────────
  function renderPrefill(s) {
    const t = s.terms || {};
    const box = document.createElement("div");
    box.className = "bass-card";
    box.style.cssText = "padding:14px;margin-top:12px;";
    const head = `<div style="font-weight:800;margin-bottom:4px;">📝 대면 전 미리 작성</div>
      <div class="bass-muted" style="font-size:12.5px;line-height:1.55;margin-bottom:8px;">미리 적어 두면 만나서는 <strong>입주일 정도만 확인</strong>하면 됩니다. 여기 적은 내용은 초안이며, 만난 뒤 ③단계에서 양쪽이 조건을 다시 보고 동의해야 계약이 진행됩니다. (상대가 연결하기 전까지만 고칠 수 있고, 작성 후 72시간 안에 만나지 않으면 자동으로 해제됩니다)</div>`;
    const taxReq = Boolean(t.tax_invoice_requested || t.tenant_business_number);
    if (currentRole === "lessor") {
      box.innerHTML = head + `
        <div class="field-grid">
          <div><label class="bass-label">입주 예정일(잔금일)</label><input id="pfMoveIn" type="date" class="bass-input"${s.reserve_after ? ` min="${esc(s.reserve_after)}"` : ""} value="${esc(t.move_in_date || "")}"></div>
          <div><label class="bass-label">계약 기간(개월)</label><input id="pfTerm" type="number" class="bass-input" value="${Number(t.lease_term_months || 12)}"></div>
          <div><label class="bass-label">보증금(원)</label><input id="pfDeposit" type="number" class="bass-input" value="${Number(t.deposit || 0)}"></div>
          <div><label class="bass-label">계약금(원, 보증금 안)</label><input id="pfConDep" type="number" class="bass-input" value="${Number(t.contract_deposit || 0)}"></div>
          <div><label class="bass-label">월세(원)</label><input id="pfRent" type="number" class="bass-input" value="${Number(t.monthly_rent || 0)}"></div>
          <div><label class="bass-label">관리비(원)</label><input id="pfFee" type="number" class="bass-input" value="${Number(t.maintenance_fee || 0)}"></div>
          <div><label class="bass-label">퇴실 청소비(원, 0 = 약정 없음)</label><input id="pfClean" type="number" min="0" class="bass-input" value="${Number(t.cleaning_fee || 0)}"></div>
          <div><label class="bass-label">월세 납부</label><select id="pfPay" class="bass-select"><option value="후불" ${t.pay_timing === "후불" ? "selected" : ""}>후불</option><option value="선불" ${t.pay_timing === "선불" ? "selected" : ""}>선불</option></select></div>
        </div>
        <div class="bass-card" style="padding:10px;margin-top:8px;">
          <label class="check-row" style="margin-bottom:0;cursor:pointer;">
            <span style="font-size:13px;font-weight:700;">🧾 세입자가 세금계산서 발행을 요청함 (사무실·법인 기숙사 등 모든 호실 가능)</span>
            <input type="checkbox" id="pfTaxInvReq" ${taxReq ? "checked" : ""}>
          </label>
          <div id="pfTaxBizWrap" class="field-grid" style="display:${taxReq ? "grid" : "none"};margin-top:8px;">
            <div><label class="bass-label">사업자등록번호</label><input id="pfBizNo" class="bass-input" value="${esc(t.tenant_business_number || "")}" placeholder="예: 214-86-12345"></div>
            <div><label class="bass-label">상호(법인명)</label><input id="pfCompName" class="bass-input" value="${esc(t.tenant_company_name || "")}" placeholder="예: (주)시크디자인"></div>
            <div><label class="bass-label">대표자명</label><input id="pfCeoName" class="bass-input" value="${esc(t.tenant_ceo_name || "")}" placeholder="예: 홍길동"></div>
            <div><label class="bass-label">세금계산서 수신 이메일</label><input id="pfBizEmail" type="email" class="bass-input" value="${esc(t.tenant_email || "")}" placeholder="예: tax@company.co.kr"></div>
          </div>
        </div>
        <label class="bass-label" style="margin-top:8px;">특약사항</label>
        <textarea id="pfSpecial" class="bass-input" rows="3">${esc(t.special_terms || "")}</textarea>
        <button id="btnPrefillSave" class="bass-btn bass-btn--primary" style="width:100%;margin-top:8px;" type="button">💾 사전 작성 저장</button>`;
    } else {
      box.innerHTML = head + `
        <div class="field-grid">
          <div><label class="bass-label">이메일</label><input id="pfEmail" type="email" class="bass-input" value="${esc(t.tenant_email || "")}"></div>
          <div><label class="bass-label">차량번호(선택)</label><input id="pfCar" class="bass-input" value="${esc(t.tenant_car_number || "")}"></div>
        </div>
        <div class="bass-card" style="padding:10px;margin-top:8px;">
          <label class="check-row" style="margin-bottom:0;cursor:pointer;">
            <span style="font-size:13px;font-weight:700;">🧾 세금계산서 발행을 요청합니다 (사무실·법인 기숙사 등)</span>
            <input type="checkbox" id="pfTaxInvReq" ${taxReq ? "checked" : ""}>
          </label>
          <div id="pfTaxBizWrap" class="field-grid" style="display:${taxReq ? "grid" : "none"};margin-top:8px;">
            <div><label class="bass-label">사업자등록번호</label><input id="pfBizNo" class="bass-input" value="${esc(t.tenant_business_number || "")}" placeholder="예: 214-86-12345"></div>
            <div><label class="bass-label">상호(법인명)</label><input id="pfCompName" class="bass-input" value="${esc(t.tenant_company_name || "")}" placeholder="예: (주)시크디자인"></div>
            <div><label class="bass-label">대표자명</label><input id="pfCeoName" class="bass-input" value="${esc(t.tenant_ceo_name || "")}" placeholder="예: 홍길동"></div>
          </div>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">이름·연락처는 본인인증된 값으로 고정됩니다. 금액과 입주일은 임대인이 정하고, 만나서 함께 확인합니다.</div>
        <button id="btnPrefillSave" class="bass-btn bass-btn--primary" style="width:100%;margin-top:8px;" type="button">💾 사전 작성 저장</button>`;
    }
    D.elStepPanel.appendChild(box);
    const pfChk = document.getElementById("pfTaxInvReq");
    if (pfChk) {
      pfChk.addEventListener("change", (ev) => {
        const w = document.getElementById("pfTaxBizWrap");
        if (w) w.style.display = ev.target.checked ? "grid" : "none";
      });
    }
    document.getElementById("btnPrefillSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const v = (id) => (document.getElementById(id) && document.getElementById(id).value) || "";
        const reqInv = Boolean(document.getElementById("pfTaxInvReq") && document.getElementById("pfTaxInvReq").checked);
        const updates = currentRole === "lessor"
          ? { move_in_date: v("pfMoveIn"), lease_term_months: Number(v("pfTerm")), deposit: Number(v("pfDeposit")), contract_deposit: Number(v("pfConDep")),
              monthly_rent: Number(v("pfRent")), maintenance_fee: Number(v("pfFee")), cleaning_fee: Number(v("pfClean") || 0), pay_timing: v("pfPay"), special_terms: v("pfSpecial"),
              tax_invoice_requested: reqInv, vat_target: reqInv,
              tenant_business_number: reqInv ? v("pfBizNo").trim() : "",
              tenant_company_name: reqInv ? v("pfCompName").trim() : "",
              tenant_ceo_name: reqInv ? v("pfCeoName").trim() : "",
              tenant_email: reqInv ? v("pfBizEmail").trim() : "" }
          : { tenant_email: v("pfEmail").trim(), tenant_car_number: v("pfCar").trim(),
              tax_invoice_requested: reqInv, vat_target: reqInv,
              tenant_business_number: reqInv ? v("pfBizNo").trim() : "",
              tenant_company_name: reqInv ? v("pfCompName").trim() : "",
              tenant_ceo_name: reqInv ? v("pfCeoName").trim() : "" };
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/prefill`, { method: "POST", body: { role: currentRole, updates } });
        S.activeSession = res.session;
        Bass.updateAppAlert("success", res.message);
      });
    });
  }

  // ───────────── 대면 참여 QR (호출한 쪽 대기 화면) ─────────────
  function renderJoinQr(s, calleeKo) {
    const joinUrl = `${window.location.origin}/rent3_contract/?session=${encodeURIComponent(s.id)}`;
    let svg = "";
    try {
      const qr = qrcode(0, "M");
      qr.addData(joinUrl);
      qr.make();
      svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
    } catch (_) { svg = ""; }
    const invite = `[${s.building_name} ${s.room_no}호] 공동 임대계약에 초대합니다. 아래 링크로 들어와 본인 계정으로 로그인한 뒤 '응답'을 눌러 주세요.\n${joinUrl}\n참여코드 ${s.session_code || ""}`;
    const box = document.createElement("div");
    box.className = "bass-card";
    box.style.cssText = "padding:14px;margin-top:12px;text-align:center;";
    box.innerHTML = `
      <div style="font-weight:800;margin-bottom:6px;">${esc(calleeKo)}님 초대</div>
      <button id="btnCopyInvite" type="button" class="bass-btn bass-btn--primary" style="width:100%;">📋 초대 문자 복사</button>
      <div class="bass-muted" style="font-size:12px;margin:6px 0 10px;">복사해서 상대에게 문자로 보내세요. 옆에 있다면 아래 QR을 찍게 해도 됩니다.</div>
      ${svg ? `<div style="width:170px;max-width:60%;margin:0 auto;background:#fff;padding:6px;border-radius:10px;">${svg}</div>` : ""}
      <div style="margin-top:8px;font-size:13px;">참여코드 <strong style="letter-spacing:2px;">${esc(s.session_code || "")}</strong></div>
      <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">링크·QR·코드만으로는 입장할 수 없습니다. 계약 당사자 계정으로 로그인해야 합니다.</div>`;
    D.elStepPanel.appendChild(box);
    box.querySelector("#btnCopyInvite").addEventListener("click", () => R3.copyText(invite).then((ok) => Bass.updateAppAlert(ok ? "success" : "warning", ok ? "초대 문자를 복사했습니다. 상대에게 붙여넣어 보내세요." : "복사에 실패했습니다.")));
  }

  // ───────────── 2단계: 옵션·시설 점검 + 현장 사진 ─────────────
  function shrinkPhoto(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        const k = Math.min(1, 1600 / Math.max(img.width, img.height));
        const cv = document.createElement("canvas");
        cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k);
        cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
        URL.revokeObjectURL(url);
        resolve(cv.toDataURL("image/jpeg", 0.8));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("사진을 읽지 못했습니다.")); };
      img.src = url;
    });
  }

  function facilityPhotosHtml(s) {
    const photos = s.facility_photos || [];
    const base = `${Bass.apiBase(APP_ID)}/sessions/${encodeURIComponent(s.id)}/facility-photos`;
    return `<div class="bass-card" style="padding:12px;margin:12px 0;">
      <div style="font-weight:800;">📷 현장 사진 <span class="bass-muted" style="font-weight:400;font-size:12px;">(${photos.length}/12 · 합의 후에는 바꿀 수 없고 퇴실 정산 때 비교 자료가 됩니다)</span></div>
      <input id="facPhotoCaption" class="bass-input" placeholder="사진 설명 (예: 욕실 천장 곰팡이, 싱크대 상판 흠집)" style="margin:8px 0 6px;">
      <input id="facPhotoInput" type="file" accept="image/*" capture="environment" multiple class="bass-input">
      <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">휴대폰에서는 카메라가 바로 열립니다. 사진을 올리거나 지우면 상대방 화면에도 바로 보이고, 양쪽 점검 확인을 다시 받습니다.</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px;margin-top:10px;">
        ${photos.map((p) => `<div style="font-size:12.5px;">
          <a href="#" class="js-open-fac-photo" data-photo-url="${base}/${encodeURIComponent(p.id)}" target="_blank" rel="noopener">
            <img class="js-auth-fac-img" data-photo-url="${base}/${encodeURIComponent(p.id)}" alt="${esc(p.caption || "점검 사진")}" loading="lazy"
                 style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;border:1px solid var(--border);"></a>
          <div>${p.by_role === "lessor" ? "🏢" : "🔑"} ${esc(p.caption || "-")}</div>
          ${p.by_role === currentRole ? `<button class="bass-btn bass-btn--sm js-del-photo" data-pid="${esc(p.id)}" type="button">삭제</button>` : ""}
        </div>`).join("") || `<div class="bass-muted" style="font-size:12.5px;grid-column:1/-1;">아직 올린 사진이 없습니다.</div>`}
      </div>
    </div>`;
  }

  function bindFacilityPhotos(s) {
    const input = document.getElementById("facPhotoInput");
    if (!input) return;
    const tok = Bass.token();
    const hdrs = tok ? { Authorization: "Bearer " + tok } : {};
    document.querySelectorAll(".js-auth-fac-img").forEach(async (img) => {
      const u = img.dataset.photoUrl;
      if (!u) return;
      try {
        const resp = await fetch(u, { headers: hdrs });
        if (!resp.ok) return;
        const blobUrl = URL.createObjectURL(await resp.blob());
        img.src = blobUrl;
        const a = img.closest(".js-open-fac-photo");
        if (a) a.href = blobUrl;
      } catch (_) {}
    });
    const notes = document.getElementById("facNotes");
    if (notes) notes.addEventListener("input", () => { S.pendingFacNotes = notes.value; });
    input.addEventListener("change", async () => {
      const files = Array.from(input.files || []);
      if (!files.length) return;
      const caption = document.getElementById("facPhotoCaption").value.trim();
      input.disabled = true;
      try {
        let last = null;
        for (const f of files) {
          const dataUrl = await shrinkPhoto(f);
          last = await Bass.api(APP_ID, `/sessions/${s.id}/facility-photos`, {
            method: "POST", body: { role: currentRole, photo_data_url: dataUrl, caption } });
        }
        if (last) { S.activeSession = last.session; Bass.updateAppAlert("success", `사진 ${files.length}장을 기록했습니다.`); C.renderWorkspace(); }
      } catch (e) { Bass.showError(e); }
      finally { input.disabled = false; }
    });
    document.querySelectorAll(".js-del-photo").forEach((b) => b.addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/facility-photos/${encodeURIComponent(b.dataset.pid)}/delete`, {
          method: "POST", body: { role: currentRole } });
        S.activeSession = res.session;
        C.renderWorkspace();
      });
    }));
  }

  // ───────────── 좌우·상하 스크롤 표준계약서 전문 미리보기 ─────────────
  function estimateExpiryDate(moveIn, months) {
    if (!moveIn || !/^\d{4}-\d{2}-\d{2}$/.test(moveIn)) return "-";
    const parts = moveIn.split("-").map(Number);
    const dt = new Date(parts[0], parts[1] - 1 + Number(months || 12), parts[2] - 1);
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, "0");
    const d = String(dt.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function buildStandardContractSheetHtml(s) {
    const t = s.terms || {};
    const ri = S.roomInfo || {};
    const spec = ri.listing_spec || {};
    const isRen = s.mode === "RENEWAL";
    const dep = Number(t.deposit || 0);
    const cdep = Number(t.contract_deposit || 0);
    const bal = Math.max(dep - cdep, 0);
    const months = Number(t.lease_term_months || 12);
    const expiryEst = estimateExpiryDate(t.move_in_date || "", months);
    const areaSqm = Number(ri.area_sqm || 0);
    const areaText = areaSqm > 0 ? `${areaSqm}㎡ (약 ${(areaSqm / 3.3058).toFixed(1)}평)` : "-";
    const floorText = ri.floor ? `${ri.floor}층${ri.total_floors ? ` / 총 ${ri.total_floors}층` : ""}` : "-";
    const opts = s.option_checks || [];
    const optListText = opts.length
      ? opts.map((o) => `${o.checked ? "☑" : "☐"} ${o.item}(${o.quantity}개 · ${o.condition})`).join(", ")
      : ((spec.options && spec.options.length) ? spec.options.join(", ") : "기본 옵션");
    const maintInc = (spec.maintenance_includes && spec.maintenance_includes.length)
      ? spec.maintenance_includes.join(" · ")
      : "별도 약정";
    const facNotes = S.pendingFacNotes !== null ? S.pendingFacNotes : (s.facility_notes || "");
    const photosCount = (s.facility_photos || []).length;
    const todayDot = (() => {
      const now = new Date();
      return `${now.getFullYear()}. ${String(now.getMonth() + 1).padStart(2, "0")}. ${String(now.getDate()).padStart(2, "0")}.`;
    })();
    return `
      <div class="cm-doc-scroll-box" role="region" aria-label="주택임대차 표준계약서 미리보기 (좌우상하 스크롤)">
        <div class="cm-std-sheet">
          <div class="cm-std-sheet__head">
            <div>
              <div class="cm-std-sheet__title">📄 주택임대차 표준계약서 (${isRen ? "연장 재계약" : esc(t.lease_type || "월세")} · Revision v${s.revision})</div>
              <div class="cm-std-sheet__sub">${esc(s.building_address || "")} (${esc(s.building_name)} ${esc(s.room_no)}호)</div>
            </div>
            ${ri.terms_pdf_url ? `<a class="bass-btn bass-btn--sm" href="${esc(ri.terms_pdf_url)}" target="_blank" rel="noopener">📑 매물조건 PDF ↗</a>` : ""}
          </div>
          <table class="cm-std-table">
            <tr>
              <th>목적물 소재지</th>
              <td colspan="3"><strong>${esc(s.building_address || "-")}</strong> (${esc(s.building_name)} ${esc(s.room_no)}호)</td>
            </tr>
            <tr>
              <th>유형 · 층수</th>
              <td>${esc(ri.room_category || "주거용")} · ${esc(floorText)}</td>
              <th>전용면적 · 방향</th>
              <td>${esc(areaText)}${ri.direction ? ` · ${esc(ri.direction)}` : ""}</td>
            </tr>
            <tr>
              <th>보증금</th>
              <td><strong>${fmtWon(dep)}</strong></td>
              <th>계약금(10%) / 잔금</th>
              <td><strong>${fmtWon(cdep)}</strong> / ${fmtWon(bal)}</td>
            </tr>
            <tr>
              <th>월 임차료</th>
              <td><strong>${fmtWon(t.monthly_rent)}</strong> (${esc(t.pay_timing || "후불")})</td>
              <th>관리비 / 퇴실 청소비</th>
              <td><strong>${fmtWon(t.maintenance_fee)}</strong> / ${Number(t.cleaning_fee || 0) > 0 ? fmtWon(t.cleaning_fee) : "약정 없음"}</td>
            </tr>
            <tr>
              <th>임대차 기간</th>
              <td colspan="3"><strong>${esc(t.move_in_date || "-")} ~ ${esc(expiryEst)}</strong> (${months}개월)</td>
            </tr>
            <tr>
              <th>시설 · 설비</th>
              <td colspan="3">케이블 TV: ${esc(spec.cable_tv || "확인")} · 인터넷: ${esc(spec.internet || "확인")} · 엘리베이터: ${esc(spec.elevator || "확인")} · 주차: ${esc(spec.parking || "확인")} · 관리비 포함: ${esc(maintInc)}</td>
            </tr>
            <tr>
              <th>옵션 내역</th>
              <td colspan="3">${esc(optListText)}</td>
            </tr>
            <tr>
              <th>도배상태 등<br>세부내역 메모</th>
              <td colspan="3" id="stdSheetFacNotes">${esc(facNotes || "기록 없음")} <span class="bass-muted">(현장 사진 ${photosCount}장)</span></td>
            </tr>
            <tr>
              <th>임차료 수납 계좌</th>
              <td colspan="3">${acctLabel(s)}</td>
            </tr>
            <tr>
              <th>특약사항</th>
              <td colspan="3" style="white-space:pre-line;">${esc(t.special_terms || "-")}</td>
            </tr>
            <tr>
              <th>임대인(갑)</th>
              <td><strong>${esc(s.lessor_name || "-")}</strong> (${esc(s.lessor_phone || "-")})</td>
              <th>임차인(을)</th>
              <td><strong>${esc(t.tenant_name || "-")}</strong> (${esc(t.tenant_phone || "-")})</td>
            </tr>
          </table>
          <div class="cm-std-sheet__foot">
            <div><strong>상기 내용에 동의합니다.</strong> &nbsp; (${todayDot})</div>
            <div>
              임대인 <strong>${esc(s.lessor_name || "-")}</strong> (${s.step3_lessor_agreed ? "동의 완료 ✓" : "동의 대기"}${s.lessor_signature ? " · 서명 완료 ✓" : ""})
              &nbsp;|&nbsp;
              임차인 <strong>${esc(t.tenant_name || "-")}</strong> (${s.step3_tenant_agreed ? "동의 완료 ✓" : "동의 대기"}${s.tenant_signature ? " · 서명 완료 ✓" : ""})
            </div>
          </div>
        </div>
      </div>`;
  }

  function renderStep2(s) {
    const opts = s.option_checks || [];
    const myDone = currentRole === "lessor" ? s.step2_lessor_confirmed : s.step2_tenant_locked;
    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 6px;">② 표준계약서 내용 확인 · 옵션 및 도배상태 세부내역 메모</h3>
      ${myDone ? `<div class="bass-card" style="padding:10px 12px;margin-bottom:8px;border-color:var(--ok);background:#f0fdf4;font-size:13px;">✅ 내 옵션·도배상태 메모 확인이 완료되었습니다.</div>` : ""}
      ${buildStandardContractSheetHtml(s)}
      <p class="bass-muted" style="font-size:13px;">입실 시 함께 확인한 옵션과 도배·벽지 상태 메모가 표준계약서 세부내역 및 퇴실 정산 비교 원본으로 박제됩니다.</p>
      <div style="margin:12px 0;">
        ${opts.map((o, idx) => `
          <label class="check-row">
            <span><strong>${esc(o.item)}</strong> (${o.quantity}개) — <span class="bass-muted">${esc(o.condition)}</span></span>
            <input type="checkbox" class="opt-chk" data-idx="${idx}" ${o.checked ? "checked" : ""}>
          </label>
        `).join("")}
      </div>
      <div>
        <label class="bass-label">벽지 상태 등 시설 기록사항 메모 (옵션, 도배상태 등 세부내역 메모)</label>
        <input id="facNotes" class="bass-input" placeholder="예: 벽지 오염·찢어짐, 바닥 흠집 등 상태 기록" value="${esc(S.pendingFacNotes !== null ? S.pendingFacNotes : (s.facility_notes || ""))}">
      </div>
      ${facilityPhotosHtml(s)}
      <div class="bass-row" style="margin:12px 0;">
        ${pairDots("시설·옵션 점검 확인", s.step2_lessor_confirmed, s.step2_tenant_locked)}
      </div>
      <button id="btnStep2Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ${currentRole === "lessor" ? "🏢 옵션·도배상태 메모 확인 완료" : "🔑 옵션·도배상태 메모 확인 완료 (표준계약서 동의로 이동)"}
      </button>
    `;
    bindFacilityPhotos(s);
    const facInp = document.getElementById("facNotes");
    if (facInp) {
      facInp.addEventListener("input", () => {
        const cell = document.getElementById("stdSheetFacNotes");
        if (cell) cell.innerHTML = `${esc(facInp.value.trim() || "기록 없음")} <span class="bass-muted">(현장 사진 ${(s.facility_photos || []).length}장)</span>`;
      });
    }
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
        S.pendingFacNotes = null;
        S.activeSession = res.session;
        S.viewStep = S.activeSession.current_step;
        if (S.activeSession.current_step >= 3) {
          S.modalKey = "";
          S.modalForceDoc = true;
        } else if (C.closeStepModal) {
          C.closeStepModal();
        }
        Bass.updateAppAlert("success", res.message);
        C.renderWorkspace();
      });
    });
  }

  // ───────────── 3단계: 계약 조건 및 특약 (표준계약서 검토·동의) ─────────────
  function renderStep3(s) {
    S.reviewedStdContractRev = s.revision;
    const t = s.terms || {};
    const rc = s.mode === "RENEWAL" ? S.renewalContext : null;
    const agreedMine = currentRole === "lessor" ? s.step3_lessor_agreed : s.step3_tenant_agreed;
    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 6px;">${s.mode === "RENEWAL" ? `③ 비대면 재계약 표준계약서 내용 확인 및 동의 (Revision v${s.revision})` : `③ 표준계약서 내용 확인 및 조건·특약 동의 (Revision v${s.revision})`}</h3>
      ${agreedMine ? `<div class="bass-card" style="padding:10px 12px;margin-bottom:8px;border-color:var(--ok);background:#f0fdf4;font-size:13px;">✅ 나는 표준계약서 내용에 동의했습니다. 상대방이 동의하면 전자서명이 열립니다.</div>` : ""}
      ${buildStandardContractSheetHtml(s)}
      ${s.mode === "RENEWAL" ? `<div class="call-banner" style="margin:0 0 10px;border-color:var(--primary-2);background:#f0f9ff;color:#0c4a6e;">
        <strong>🔄 비대면 재계약</strong> — 기존 계약 당사자이므로 본인 확인(신분증·등기부) 없이 바로 <strong>재계약 승인 → 전자서명</strong>으로 체결됩니다.
        ${rc ? `<div style="font-size:12.5px;margin-top:4px;">기존 계약: 보증금 ${fmtWon(rc.prev_deposit)} · 월세 ${fmtWon(rc.prev_monthly_rent)} · 관리비 ${fmtWon(rc.prev_maintenance_fee)} (5% 상한: 보증금 ${fmtWon(rc.max_deposit)} · 월세 ${fmtWon(rc.max_monthly_rent)})${rc.notice_sent ? `<br>문자·예고 조건: 보증금 ${fmtWon(rc.notice_deposit)} · 월세 ${fmtWon(rc.notice_monthly_rent)} · 관리비 ${fmtWon(rc.notice_maintenance_fee)} (${Number(rc.notice_term_months || 12)}개월)` : ""}</div>` : ""}
      </div>` : ""}
      ${!adminProxy && currentRole === "lessor" && s.current_step <= 3 ? `<div style="margin:0 0 10px;"><button type="button" id="btnStep3Bundle" class="bass-btn bass-btn--sm" style="width:100%;">📋 조건 일괄 수정 (통화·협의 후 채팅방 제시)</button></div>` : ""}
      <p class="bass-muted" style="font-size:13px;">보증금·계약금·월세·관리비·<strong>퇴실 청소비(약정하면 퇴실 때 보증금에서 공제)</strong>·입주일·계약기간은 상대에게 <strong>제안</strong>하고 <strong>수락</strong>을 받아야 바뀝니다. 인적사항·납부 시점·세금계산서 정보·특약은 직접 고칠 수 있습니다.</p>
      <div class="term-grid">
        ${[["deposit", "보증금", fmtWon(t.deposit)], ["contract_deposit", "계약금", fmtWon(t.contract_deposit)], ["monthly_rent", "월세", fmtWon(t.monthly_rent)],
           ["maintenance_fee", "관리비", fmtWon(t.maintenance_fee)], ["cleaning_fee", "퇴실 청소비 (퇴실 때 보증금에서 공제)", Number(t.cleaning_fee || 0) > 0 ? fmtWon(t.cleaning_fee) : "약정 없음"], ["move_in_date", "입주일", esc(t.move_in_date || "-")],
           ["lease_term_months", "계약기간", `${Number(t.lease_term_months || 0)}개월`]].map(([f, label, val]) => `
          <div class="term-row"><span class="bass-muted">${label}</span><b>${val}</b>
            <button type="button" class="bass-btn bass-btn--sm btn-propose" data-field="${f}"${currentRole && !adminProxy ? "" : " disabled"}>제안</button></div>`).join("")}
      </div>
      <div class="field-grid" style="margin:12px 0;">
        <div><label class="bass-label">임차인 성명 *</label><input id="tName" class="bass-input term-input" value="${esc(t.tenant_name || "")}"></div>
        <div><label class="bass-label">임차인 연락처 *</label><input id="tPhone" class="bass-input term-input" value="${esc(t.tenant_phone || "")}"></div>
        <div><label class="bass-label">차량 번호 (주차 등록용)</label><input id="tCar" class="bass-input term-input" value="${esc(t.tenant_car_number || "")}" placeholder="예: 12가 3456"></div>
        <div>
          <label class="bass-label">임대 구분 / 납부 시점</label>
          <select id="tPayTiming" class="bass-select term-input">
            <option value="후불" ${t.pay_timing === "후불" ? "selected" : ""}>월세 후불 (입주월 비과세 · 익월 청구)</option>
            <option value="선불" ${t.pay_timing === "선불" ? "selected" : ""}>월세 선불 (입주월부터 당월 청구)</option>
          </select>
        </div>
      </div>
      <div class="bass-card" style="padding:10px 12px;margin:10px 0;">
        <label class="check-row" style="margin-bottom:0;cursor:pointer;">
          <span style="font-size:13px;font-weight:700;">🧾 세입자가 세금계산서 발행을 요청함 (사무실·법인 기숙사 등 모든 호실 가능)</span>
          <input type="checkbox" id="tTaxInvReq" class="term-input" ${(t.tax_invoice_requested || t.tenant_business_number) ? "checked" : ""}>
        </label>
        <div id="tTaxBizWrap" class="field-grid" style="display:${(t.tax_invoice_requested || t.tenant_business_number) ? "grid" : "none"};margin-top:8px;">
          <div><label class="bass-label">사업자등록번호</label><input id="tBizNo" class="bass-input term-input" value="${esc(t.tenant_business_number || "")}" placeholder="예: 214-86-12345"></div>
          <div><label class="bass-label">상호(법인명)</label><input id="tCompName" class="bass-input term-input" value="${esc(t.tenant_company_name || "")}" placeholder="예: (주)시크디자인"></div>
          <div><label class="bass-label">대표자명</label><input id="tCeoName" class="bass-input term-input" value="${esc(t.tenant_ceo_name || "")}" placeholder="예: 홍길동"></div>
          <div><label class="bass-label">세금계산서 수신 이메일</label><input id="tBizEmail" type="email" class="bass-input term-input" value="${esc(t.tenant_email || "")}" placeholder="예: tax@company.co.kr"></div>
        </div>
      </div>
      ${(() => {
        const need = needsGuarantee(s) && (s.tenant_registry || {}).submitted && mortgageExposure(s) > 0;
        if (!need) return "";
        const gs = (s.tenant_registry || {}).guarantee_status;
        const insured = gs === "가입 완료" || gs === "가입 예정";
        const blocked = riskBlock(s);
        return `<div class="call-banner" style="border:2px solid ${blocked ? "var(--err)" : "var(--ok)"};margin:10px 0;">
          <div style="font-weight:800;margin-bottom:4px;">${blocked ? "🛑 선순위 근저당이 있어 이대로는 진행할 수 없습니다" : "✅ 선순위 근저당 위험 조치가 있습니다"}</div>
          <div style="font-size:12.5px;line-height:1.6;margin-bottom:6px;">근저당 채권최고액 ${fmtWon(mortgageExposure(s))} · 보증금 ${fmtWon(t.deposit)} · 보증보험 ${insured ? esc(gs) + (gs === "가입 예정" ? " (거절 시 계약 무효 특약 자동 삽입)" : "") : "미가입(" + esc(gs || "-") + ")"}</div>
          <label class="check-row" style="margin-bottom:0;">
            <span style="font-size:12.5px;"><strong>잔금 시 선순위 근저당 전액 말소 특약</strong>을 넣는다 (임대인이 체크 · 잔금일에 말소가 확인돼야 도어락이 교부됩니다)</span>
            <input type="checkbox" id="tCleanup" class="term-input" ${t.mortgage_cleanup_clause ? "checked" : ""} ${currentRole === "lessor" ? "" : "disabled"}>
          </label>
          ${blocked ? `<div style="font-size:12px;margin-top:6px;">${esc(blocked)} 임차인은 보증보험 가입을 완료하거나, 임대인이 위 특약을 넣어야 합의할 수 있습니다.</div>` : ""}
        </div>`;
      })()}
      <div>
        <label class="bass-label">상호 합의 특약사항</label>
        <textarea id="tSpecial" rows="4" class="bass-input term-input">${esc(t.special_terms || "")}</textarea>
      </div>
      <div class="bass-row" style="margin:6px 0 0;"><button id="btnReqSpecial" type="button" class="bass-btn bass-btn--sm"${adminProxy ? " disabled" : ""}>✏️ 상대에게 특약 수정 요청</button></div>
      <div class="bass-row" style="margin:12px 0;justify-content:space-between;align-items:center;">
        <div class="dotline" style="margin:0;"><span class="dotline__item"><span class="dot ${s.step3_lessor_agreed ? "dot--ok" : "dot--wait"}"></span>임대인${currentRole === "lessor" ? " (나)" : ""}</span><span class="dotline__item"><span class="dot ${s.step3_tenant_agreed ? "dot--ok" : "dot--wait"}"></span>임차인${currentRole === "tenant" ? " (나)" : ""}</span></div>
        <button id="btnSaveTerms" class="bass-btn bass-btn--sm" type="button">✏️ 인적사항·특약 등 저장</button>
      </div>
      ${S.chatSummary.open_count > 0 ? `<div class="call-banner" style="margin:8px 0;"><div style="font-weight:800;margin-bottom:4px;">답하지 않은 제안·수정 요청이 ${S.chatSummary.open_count}건 있습니다</div><div style="font-size:12.5px;margin-bottom:8px;">모두 처리(수락·거절·철회)해야 최종 동의할 수 있습니다.</div><button id="btnOpenChat3" type="button" class="bass-btn bass-btn--sm bass-btn--primary">💬 채팅 열기</button></div>` : ""}
      <button id="btnAgreeStep3" class="bass-btn bass-btn--primary" style="width:100%;" type="button"${S.chatSummary.open_count > 0 ? " disabled" : ""}>
        ${currentRole === "lessor" ? "✅ 임대인은 표준계약서 내용에 동의" : "✅ 임차인은 표준계약서 내용에 동의"} · ${s.mode === "RENEWAL" ? "재계약 승인" : "계약 조건 전체 동의"} (v${s.revision})
      </button>
    `;

    D.elStepPanel.querySelectorAll(".term-input").forEach((inp) => {
      inp.addEventListener("focus", () => { S.isEditingForm = true; });
      inp.addEventListener("blur", () => { setTimeout(() => { S.isEditingForm = false; }, 400); });
    });

    const chkTaxInv = document.getElementById("tTaxInvReq");
    if (chkTaxInv) {
      chkTaxInv.addEventListener("change", (ev) => {
        const w = document.getElementById("tTaxBizWrap");
        if (w) w.style.display = ev.target.checked ? "grid" : "none";
      });
    }

    const collectUpdates = () => {
      const reqInv = Boolean(document.getElementById("tTaxInvReq") && document.getElementById("tTaxInvReq").checked);
      return {
        tenant_name: document.getElementById("tName").value.trim(),
        tenant_phone: document.getElementById("tPhone").value.trim(),
        tenant_car_number: document.getElementById("tCar").value.trim(),
        tax_invoice_requested: reqInv,
        vat_target: reqInv,
        tenant_business_number: reqInv ? ((document.getElementById("tBizNo") && document.getElementById("tBizNo").value.trim()) || "") : "",
        tenant_company_name: reqInv ? ((document.getElementById("tCompName") && document.getElementById("tCompName").value.trim()) || "") : "",
        tenant_ceo_name: reqInv ? ((document.getElementById("tCeoName") && document.getElementById("tCeoName").value.trim()) || "") : "",
        tenant_email: reqInv ? ((document.getElementById("tBizEmail") && document.getElementById("tBizEmail").value.trim()) || "") : "",
        pay_timing: document.getElementById("tPayTiming").value,
        special_terms: document.getElementById("tSpecial").value,
        ...(document.getElementById("tCleanup") && !document.getElementById("tCleanup").disabled ? { mortgage_cleanup_clause: document.getElementById("tCleanup").checked } : {}),
      };
    };

    D.elStepPanel.querySelectorAll(".btn-propose").forEach((b) => b.addEventListener("click", () => C.openProposeDialog(s, b.dataset.field)));
    const btnStep3Bundle = document.getElementById("btnStep3Bundle");
    if (btnStep3Bundle) {
      btnStep3Bundle.addEventListener("click", () => {
        S.stepModalOpen = false;
        D.elModal.hidden = true;
        C.openBundleDialog(s);
      });
    }
    const btnReqSp = document.getElementById("btnReqSpecial");
    if (btnReqSp) btnReqSp.addEventListener("click", () => C.openRequestDialog(s));
    const btnChat3 = document.getElementById("btnOpenChat3");
    if (btnChat3) btnChat3.addEventListener("click", () => C.openChatDrawer());

    // 내가 실제로 고친 항목만 보낸다(고치지 않은 항목까지 보내 상대가 방금 바꾼 값을 되돌리는 일을 막는다).
    const norm = (v) => String(v === undefined || v === null ? "" : v).trim();
    const changedUpdates = () => {
      const ups = collectUpdates();
      const out = {};
      Object.keys(ups).forEach((k) => { if (norm(ups[k]) !== norm((s.terms || {})[k])) out[k] = ups[k]; });
      return out;
    };
    const postTerms = async () => {
      const updates = changedUpdates();
      if (!Object.keys(updates).length) return { session: S.activeSession, unchanged: true };
      try {
        return await Bass.api(APP_ID, `/sessions/${s.id}/terms`, { method: "POST", body: { role: currentRole, updates, expected_revision: s.revision } });
      } catch (err) {
        if (err && err.status === 409 && /상대방이 계약 조건을 바꿨/.test(err.message || "")) { S.isEditingForm = false; await C.fetchActiveSession(true); }
        throw err;
      }
    };

    document.getElementById("btnSaveTerms").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await postTerms();
        S.activeSession = res.session;
        S.lastRevision = S.activeSession.revision;
        S.isEditingForm = false;
        Bass.updateAppAlert(res.level || "success", res.message);
        C.renderWorkspace();
      });
    });

    document.getElementById("btnAgreeStep3").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const editRes = await postTerms();
        S.activeSession = editRes.session;
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step3/agree`, {
          method: "POST",
          body: { role: currentRole, expected_revision: S.activeSession.revision },
        });
        S.activeSession = res.session;
        S.lastRevision = S.activeSession.revision;
        S.viewStep = S.activeSession.current_step;
        S.isEditingForm = false;
        if (C.closeStepModal) C.closeStepModal();
        Bass.updateAppAlert("success", res.message);
        C.renderWorkspace();
      });
    });
  }

  // ───────────── 4단계: 전자서명 ─────────────
  let sigEndHandler = null;

  function renderStep4(s) {
    const mySig = currentRole === "lessor" ? s.lessor_signature : s.tenant_signature;
    const defaultName = currentRole === "lessor" ? s.lessor_name : ((s.terms && s.terms.tenant_name) || "임차인");

    const okLogin = Boolean(s.lessor_login_verified && s.tenant_login_verified);
    const okMutualId = Boolean(s.lessor_checked_tenant_id && s.tenant_checked_lessor_id);
    const okRegistry = Boolean(s.step1_lessor_confirmed && s.step1_tenant_locked);
    const okStep3 = Boolean(s.step3_lessor_agreed && s.step3_tenant_agreed);
    const canSignNow = okLogin && okMutualId && okRegistry && okStep3;

    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 6px;">${s.mode === "RENEWAL" ? "④ 재계약 전자서명 (양측 서명 완료 시 즉시 체결)" : "④ 표준계약서 최종 확인 및 자필 전자서명"}</h3>
      ${buildStandardContractSheetHtml(s)}
      <p class="bass-muted" style="font-size:13px;">
        로그인 본인인증, 서로 신분증 확인 체크, 등기부등본 열람 확인, 계약조건 합의가 모두 완료되어야 서명이 날인되며 SHA-256 해시로 봉인됩니다.
      </p>

      <div class="gate-list">
        <div class="gate-item">
          <span>1. 임대인·임차인 각자 로그인 본인인증 완료</span>
          <span class="bass-badge ${okLogin ? "bass-badge--ok" : "bass-badge--warn"}">${okLogin ? "확인 완료 ✓" : "미완료"}</span>
        </div>
        <div class="gate-item">
          <span>2. 서로 신분증 대조 본인 확인 체크 (임대인 ${s.lessor_checked_tenant_id ? "✓" : "✗"} · 임차인 ${s.tenant_checked_lessor_id ? "✓" : "✗"})</span>
          <span class="bass-badge ${okMutualId ? "bass-badge--ok" : "bass-badge--warn"}">${okMutualId ? "양측 완료 ✓" : "Step 1 체크 필요"}</span>
        </div>
        <div class="gate-item">
          <span>3. 대법원 등기부등본 권리관계 확인 잠금</span>
          <span class="bass-badge ${okRegistry ? "bass-badge--ok" : "bass-badge--warn"}">${okRegistry ? "확인 완료 ✓" : "Step 1 확인 필요"}</span>
        </div>
        <div class="gate-item">
          <span>4. 계약 조건(Revision v${s.revision}) 양측 최종 합의 잠금</span>
          <span class="bass-badge ${okStep3 ? "bass-badge--ok" : "bass-badge--warn"}">${okStep3 ? "합의 완료 ✓" : "Step 3 합의 필요"}</span>
        </div>
      </div>

      ${!canSignNow ? `
        <div class="call-banner" style="margin-bottom:12px;">
          <div style="font-weight:800; font-size:13px; margin-bottom:6px;">⚠️ 전자서명 전 필수 확인 항목이 남아 있습니다.</div>
          <div class="bass-row">
            ${(!okMutualId || !okRegistry) ? `<button type="button" id="btnJumpStep1" class="bass-btn bass-btn--sm">① 신분증 체크 · 등기부등본 확인하러 가기 →</button>` : ""}
            ${!okStep3 ? `<button type="button" id="btnJumpStep3" class="bass-btn bass-btn--sm">③ 계약조건 합의 잠금하러 가기 →</button>` : ""}
          </div>
        </div>
      ` : ""}

      <div class="bass-row" style="margin:10px 0;">
        <span class="bass-badge ${s.lessor_signature ? "bass-badge--ok" : "bass-badge--warn"}">임대인 서명: ${s.lessor_signature ? "날인 완료 ✓" : "대기"}</span>
        <span class="bass-badge ${s.tenant_signature ? "bass-badge--ok" : "bass-badge--warn"}">임차인 서명: ${s.tenant_signature ? "날인 완료 ✓" : "대기"}</span>
      </div>
      ${s.integrity_hash ? `<div class="bass-card" style="padding:12px;margin-bottom:12px;font-size:12px;">🔐 <strong>SHA-256 무결성 봉인 해시:</strong> <code>${esc(s.integrity_hash)}</code></div>` : ""}
      ${S.sealedContract && S.sealedContract.chat_digest ? `<div class="bass-card" style="padding:12px;margin-bottom:12px;font-size:12px;">💬 <strong>협상 채팅 기록 ${Number(S.sealedContract.chat_digest_count || 0)}건 해시:</strong> <code>${esc(S.sealedContract.chat_digest)}</code></div>` : ""}

      <label class="bass-label">${currentRole === "lessor" ? "🏢 임대인(갑)" : "🔑 임차인(을)"} 자필 서명 패드 (마우스/터치 드로잉)</label>
      <canvas id="sigCanvas" class="sig-pad" width="600" height="110"></canvas>
      <div class="field-grid" style="margin-top:10px;">
        <div>
          <label class="bass-label">정자 서명 성명 확인</label>
          <input id="sigText" class="bass-input" value="${esc(mySig && !mySig.startsWith("data:") ? mySig : `${defaultName} (자필전자서명)`)}">
        </div>
        <div class="bass-row" style="align-items:flex-end;">
          <button id="btnClearCanvas" class="bass-btn" type="button">지우기</button>
          <button id="btnSubmitSign" class="bass-btn bass-btn--primary" style="flex:1;" type="button" ${!canSignNow ? "disabled" : ""}>
            ✍️ 전자서명 날인 완료
          </button>
        </div>
      </div>
    `;

    const jump1 = document.getElementById("btnJumpStep1");
    if (jump1) jump1.addEventListener("click", () => { S.viewStep = 1; C.renderWorkspace(); });
    const jump3 = document.getElementById("btnJumpStep3");
    if (jump3) jump3.addEventListener("click", () => { S.viewStep = 3; C.renderWorkspace(); });

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
      S.isEditingForm = true;
      if (canvas.setPointerCapture) {
        try { canvas.setPointerCapture(ev.pointerId); } catch (_) {}
      }
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
    if (sigEndHandler) {
      window.removeEventListener("pointerup", sigEndHandler);
      window.removeEventListener("pointercancel", sigEndHandler);
    }
    sigEndHandler = () => { drawing = false; };
    window.addEventListener("pointerup", sigEndHandler);
    window.addEventListener("pointercancel", sigEndHandler);

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
        S.activeSession = res.session;
        if (res.contract) S.sealedContract = res.contract;
        S.isEditingForm = false;
        S.viewStep = S.activeSession.current_step;
        if (C.closeStepModal) C.closeStepModal();
        Bass.updateAppAlert("success", res.message);
        if (S.activeSession.status === "COMPLETED" && !S.sealedContract) await C.fetchActiveSession(true);
        C.renderWorkspace();
      });
    });
  }

  // ───────────── 5단계: 계약금 확인·체결 및 A4 전자계약서 ─────────────
  function reservedBannerHtml() {
    const ct = S.sealedContract || {};
    if (ct.status !== "SCHEDULED") return "";
    if (ct.renews_contract_id) {
      return `<div class="bass-card" style="padding:12px 14px;margin:12px 0;border-left:4px solid var(--warn);font-size:13px;line-height:1.7;">
      <strong>🔁 연장 재계약이 체결되었습니다</strong><br>
      <strong>${esc(ct.move_in_date || "-")}</strong>(현재 계약 만기일)부터 새 조건으로 청구됩니다. 그날까지는 <strong>현재 계약 조건이 그대로 적용</strong>되며, 그때까지 새 계약은 예약 상태입니다.
    </div>`;
    }
    return `<div class="bass-card" style="padding:12px 14px;margin:12px 0;border-left:4px solid var(--warn);font-size:13px;line-height:1.7;">
      <strong>📅 입주 예약 계약입니다</strong><br>
      현재 입주자가 퇴실 확정되면 입주 계약이 시작됩니다(입주 예정일 <strong>${esc(ct.move_in_date || "-")}</strong>). 월세 청구와 납부 내역은 입주가 시작된 뒤부터 계산됩니다.
      도어락 정보는 입주가 시작되고 임대인이 잔금 입금을 확인한 뒤에 안내됩니다. 예약을 취소하려면 임대인에게 요청해 주세요.
    </div>`;
  }

  function renderStep5(s) {
    const t = s.terms || {};
    const done = s.status === "COMPLETED";
    D.elStepPanel.innerHTML = `
      <h3>⑤ 계약금 입금 확인 & 공동 Data Pool 원장 일괄 생성</h3>
      <p class="bass-muted" style="font-size:13px;">체결이 끝나면 계약·호실·입주자·수납 정보가 한꺼번에 반영됩니다.</p>
      <div class="bass-card" style="padding:14px;margin:12px 0;">
        <div><strong>입금 계좌:</strong> ${acctLabel(s)}</div>
        <div><strong>당일 계약금:</strong> ${fmtWon(t.contract_deposit)} / <strong>보증금 총액:</strong> ${fmtWon(t.deposit)} / <strong>월세+관리비:</strong> ${fmtWon(Number(t.monthly_rent || 0) + Number(t.maintenance_fee || 0))} (${esc(t.pay_timing)})</div>
        ${s.fixed_date_no ? `<div style="margin-top:6px;color:var(--ok);font-weight:700;">📄 계약 접수번호: ${esc(s.fixed_date_no)}<div class="bass-muted" style="font-weight:400;font-size:12px;">이 번호는 이 서비스의 접수번호이며 <strong>법적 확정일자가 아닙니다.</strong> 입주일에 전입신고와 함께 주민센터(또는 등기소)에서 확정일자를 받아야 보증금이 보호됩니다.</div></div>` : ""}
      </div>
      ${done ? `<div class="bass-card" style="padding:12px 14px;margin:12px 0;border-left:4px solid var(--warn);font-size:13px;line-height:1.7;">
        <strong>💰 잔금 안내</strong><br>
        잔금 <strong>${fmtWon(Math.max(Number(t.deposit || 0) - Number(t.contract_deposit || 0), 0))}</strong> (보증금 ${fmtWon(t.deposit)} − 계약금 ${fmtWon(t.contract_deposit)}) ·
        납부일(입주일) <strong>${esc(t.move_in_date || "-")}</strong><br>
        입금 계좌: ${acctLabel(s)}<br>
        <span id="autoPayGuide">${autoPayGuideHtml()}</span><br>
        🔒 <strong>도어락·공동현관 번호는 입주 당일 잔금 입금을 임대인이 확인한 뒤 임차인 마이홈에 공개됩니다.</strong>
      </div>` : ""}
      ${done ? reservedBannerHtml() : ""}
      ${done ? C.balRegCardHtml() : ""}
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
        ${currentRole === "lessor" ? `<label class="check-row" style="margin-bottom:10px;border-color:var(--warn);">
          <span style="font-size:12.5px;line-height:1.55;">⚠️ <strong>내 은행 앱의 실제 입금 내역</strong>에서 계약금이 들어온 것을 직접 확인했습니다. (임차인이 보여 주는 이체 완료 화면·캡처는 위조될 수 있습니다)</span>
          <input type="checkbox" id="chkOwnAccount"></label>` : ""}
        ${s.tenant_recheck_at ? "" : `<div class="bass-card" style="padding:10px 12px;margin-bottom:10px;font-size:12.5px;border-color:var(--warn);">
          ⏳ 임차인이 체결 직전 등기부 재열람을 확인하면 체결 버튼이 열립니다.</div>`}
        <button id="btnCompleteSeal" class="bass-btn bass-btn--primary" style="width:100%;" type="button" ${s.tenant_recheck_at ? "" : "disabled"}>
          🎉 계약금 입금 확인 & 임대차 계약 최종 체결 봉인
        </button>
      `}
    `;
    const balBtn = document.getElementById("btnBalReg");
    if (balBtn) balBtn.addEventListener("click", () => C.openBalRegModal(s));
    const btn = document.getElementById("btnCompleteSeal");
    if (btn) {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const method = document.getElementById("payMethodSel").value;
          const own = document.getElementById("chkOwnAccount");
          if (!own || !own.checked) { Bass.updateAppAlert("warning", "내 계좌의 실제 입금 내역을 확인했다는 항목에 체크해 주세요."); return; }
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/step5/complete`, {
            method: "POST",
            body: { payment_method: method, own_account_checked: true },
          });
          S.activeSession = res.session;
          S.sealedContract = res.contract;
          Bass.updateAppAlert("success", res.message, "아래 생성된 표준 전자계약서를 인쇄하거나 임대인/임차인 앱에서 즉시 동기화된 장부를 확인하세요.");
          await C.loadSessionList();
          C.renderWorkspace();
        });
      });
    }
  }

  function renderSigCell(sig) {
    if (!sig) return `(미서명)`;
    if (sig.startsWith("data:image")) {
      return `<img src="${sig}" alt="전자서명" style="max-height:48px;vertical-align:middle;">`;
    }
    return `<strong>${esc(sig)}</strong>`;
  }

  function renderSealedPaper() {
    if (!S.activeSession || S.activeSession.status !== "COMPLETED" || !S.sealedContract) {
      D.elSealedView.style.display = "none";
      D.elSealedView.innerHTML = "";
      return;
    }
    const c = S.sealedContract;
    const regSnap = c.registry_snapshot || {};
    const tdr = regSnap.tenant_direct_registry || {};
    const verifyUrl = `${window.location.origin}/rent3_contract/verify.html?no=${encodeURIComponent(c.id)}&hash=${c.integrity_hash}`;
    let qrSvg = "";
    try {
      const qr = qrcode(0, "M");
      qr.addData(verifyUrl);
      qr.make();
      qrSvg = qr.createSvgTag({ cellSize: 3, margin: 0, scalable: true });
    } catch (_) { qrSvg = ""; }
    D.elSealedView.style.display = "block";
    D.elSealedView.innerHTML = `
      <div class="no-print" style="margin-top:14px;">
        ${reservedBannerHtml()}
        <div class="bass-card" style="padding:14px;">
          <div style="font-weight:800;margin-bottom:4px;">🎉 계약이 체결되었습니다</div>
          <div class="bass-muted" style="font-size:12.5px;margin-bottom:10px;">
            행정복지센터(주민센터) 확정일자·전입신고 제출용으로 인쇄하거나 PDF로 저장하세요.
            인쇄 창에서 "PDF로 저장"을 고르면 파일로 보관됩니다. 계약서 하단 QR로 언제든 진위를 확인할 수 있습니다.<br>            <strong>잔금을 보내기 전과 입주 당일에도 인터넷등기소에서 한 번 더 열람</strong>하고, 전입신고·확정일자는 입주 당일 바로 받으세요.
          </div>
          <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
            <button id="btnPrintContract" class="bass-btn bass-btn--primary" style="flex:2;min-width:180px;" type="button">🖨️ 계약서 인쇄 / PDF 저장 (A4)</button>
            <button id="btnSealedHome" class="bass-btn" style="flex:1;" type="button">← 홈으로</button>
          </div>
        </div>
      </div>
      <article class="contract-paper">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;border-bottom:2px solid #000;padding-bottom:10px;margin-bottom:14px;">
          <div>
            <h2 style="margin:0;">표준 부동산 임대차 전자계약서 (${esc(c.lease_type)})</h2>
            <div style="font-size:12px;">계약번호: ${esc(c.id)} · 체결일: ${esc(c.contract_date)}</div>
          </div>
          <div style="border:2px solid #b91c1c;color:#b91c1c;padding:6px 10px;font-weight:800;font-size:12px;text-align:center;">
            계약 접수번호<br>${esc(c.fixed_date_no)}<br><span style="font-size:10px;font-weight:600;">(확정일자 아님)</span>
          </div>
        </div>

        <table>
          <tr><th>목적물 소재지</th><td colspan="3">${esc(S.activeSession.building_address)} (${esc(S.activeSession.building_name)} ${esc(c.room_no)}호)</td></tr>
          <tr><th>보증금</th><td>${fmtWon(c.deposit)}</td><th>계약금(보증금 내)</th><td>${fmtWon(c.contract_deposit)} (영수함)</td></tr>
          <tr><th>월 임차료</th><td>${fmtWon(c.monthly_rent)} (${esc(c.pay_timing)})</td><th>월 관리비 / 부가세</th><td>${fmtWon(c.maintenance_fee)} / ${fmtWon(c.vat)}</td></tr>
          <tr><th>퇴실 청소비</th><td colspan="3">${Number(c.cleaning_fee || 0) > 0 ? `${fmtWon(c.cleaning_fee)} — 퇴실할 때 임차인이 부담하며 전출 정산에서 보증금에서 공제함` : "약정 없음(임차인이 청소비를 부담하지 않음)"}</td></tr>
          <tr><th>임차료 수납 계좌</th><td colspan="3">${acctLabel(S.activeSession)}<br><span style="font-size:11.5px;">임차료·관리비는 매월 납입일(입주일)에 위 계좌로 납부합니다. ${autoPayGuideHtml()}</span></td></tr>
          <tr><th>임대차 기간</th><td colspan="3">${esc(c.move_in_date)} ~ ${esc(c.expiry_date)} (${c.lease_term_months}개월)</td></tr>
          <tr><th>본인인증 및 신분증·등기 확인</th><td colspan="3">양측 로그인 본인인증 완료 · 임대인↔임차인 상호 신분증 대조 본인 확인 체크 완료 · 등기부등본(${esc(regSnap.issue_number || "-")}, 소유자 ${esc(regSnap.owner_name || c.lessor_name)}) 열람 확인 필</td></tr>
          ${tdr.submitted ? `<tr><th>임차인 직접 열람 확인</th><td colspan="3">인터넷등기소 직접 열람 (${esc(tdr.viewed_at || "-")}) · 소유자 ${esc(tdr.owner_name || "-")} · 근저당 채권최고액 ${fmtWon(tdr.mortgage_amount)} · 압류/신탁 ${esc(tdr.seizure_or_trust || "-")}${tdr.guarantee_status ? ` · 보증금 반환보증 ${esc(tdr.guarantee_status)}` : ""}${regSnap.tenant_recheck_at ? ` · 체결 직전 재열람 확인 ${esc(String(regSnap.tenant_recheck_at).slice(0, 16).replace("T", " "))}` : ""}${regSnap.registry_attachment_sha256 ? `<br><span style="font-family:monospace;font-size:10.5px;">첨부 SHA-256 ${esc(regSnap.registry_attachment_sha256)}</span>` : ""}</td></tr>` : ""}
          <tr><th>특약사항</th><td colspan="3" style="white-space:pre-line;">${esc(c.special_terms)}</td></tr>
          <tr><th>임대인(갑) 서명</th><td>${esc(c.lessor_name)} &nbsp; ${renderSigCell(c.lessor_signature)}</td><th>임차인(을) 서명</th><td>${esc(c.tenant_name)} (${esc(c.tenant_phone)}) &nbsp; ${renderSigCell(c.tenant_signature)}</td></tr>
          <tr><th>진위 확인</th><td colspan="3"><div style="display:flex;gap:12px;align-items:center;">${qrSvg ? `<div style="width:92px;height:92px;flex:none;">${qrSvg}</div>` : ""}<div style="font-size:11.5px;line-height:1.5;">QR을 찍거나 아래 주소로 접속하면 이 계약서가 서버 기록과 같은지 확인할 수 있습니다.<br><span style="font-family:monospace;font-size:10px;word-break:break-all;">${esc(verifyUrl)}</span></div></div></td></tr>
          <tr><th>무결성 해시(SHA-256)</th><td colspan="3" style="font-family:monospace;font-size:11px;word-break:break-all;">${esc(c.integrity_hash)}</td></tr>
        </table>
      </article>
    `;
    document.getElementById("btnPrintContract").addEventListener("click", () => window.print());
    document.getElementById("btnSealedHome").addEventListener("click", C.goHome);
  }

  Object.assign(C, {
    decoratePanel, renderCallHandshake, renderPrefill, renderJoinQr,
    shrinkPhoto, facilityPhotosHtml, bindFacilityPhotos,
    buildStandardContractSheetHtml, renderStep2, renderStep3, renderStep4,
    reservedBannerHtml, renderStep5, renderSigCell, renderSealedPaper,
  });
})();
