/* apps/rent_dist/rent3_lessor/frontend/rooms.js — 매물 등록·홍보 생성기·등기부 확인·건물 정보·가맹 신청·사업자등록증. */
(function () {
  "use strict";
  const L = window.R3L;
  if (!L) return;

  const {
    APP_ID, REG_FRESH_DAYS, CHECK_STYLE, elTabContent, state: S,
    $m, copyText, openModal, closeModal,
  } = L;

  // 사업자등록증 올리기: [📷 촬영하기] · [🖼 사진·PDF 선택]. 사진은 긴 변 2000px JPEG 로 줄여 올린다.
  function bizPickerHtml(p) {
    return `<div class="bass-row" style="gap:8px;flex-wrap:wrap;margin:6px 0;">
        <button type="button" id="${p}Cam" class="bass-btn bass-btn--primary" style="min-height:48px;">📷 촬영하기</button>
        <button type="button" id="${p}Pic" class="bass-btn" style="min-height:48px;">🖼 사진·PDF 선택</button>
      </div>
      <input id="${p}FileCam" type="file" accept="image/*" capture="environment" hidden>
      <input id="${p}File" type="file" accept="image/*,application/pdf" hidden>
      <div id="${p}Prev" class="bass-muted" style="font-size:12.5px;">선택한 파일 없음 — 등록증 전체가 반듯하게 보이도록 밝은 곳에서 찍어 주세요.</div>`;
  }

  function bindBizPicker(p) {
    delete S.bizPicked[p];
    const g = (id) => document.getElementById(p + id);
    const take = (inp) => {
      const file = inp.files && inp.files[0];
      if (!file) return;
      if (file.size > 10 * 1024 * 1024 && !/^image\//.test(file.type)) { Bass.updateAppAlert("warning", "PDF 는 10MB 이하만 가능합니다."); return; }
      S.bizPicked[p] = file;
      const prev = g("Prev");
      if (/^image\//.test(file.type)) {
        prev.innerHTML = `<img alt="선택한 사업자등록증" src="${URL.createObjectURL(file)}" style="max-width:100%;max-height:180px;border-radius:8px;border:1px solid var(--border);display:block;margin-bottom:4px;">${Bass.escapeHtml(file.name || "촬영한 사진")}`;
      } else {
        prev.textContent = "📄 " + file.name;
      }
    };
    g("Cam").addEventListener("click", () => g("FileCam").click());
    g("Pic").addEventListener("click", () => g("File").click());
    g("FileCam").addEventListener("change", (e) => take(e.target));
    g("File").addEventListener("change", (e) => take(e.target));
  }

  async function bizDataUrl(p) {
    const file = S.bizPicked[p];
    if (!file) return "";
    const raw = () => new Promise((res, rej) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.onerror = rej; rd.readAsDataURL(file); });
    if (!/^image\/(jpeg|png|webp)$/.test(file.type) || !window.createImageBitmap) return raw();
    try {
      const bmp = await createImageBitmap(file);
      const k = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
      const cv = document.createElement("canvas");
      cv.width = Math.max(1, Math.round(bmp.width * k)); cv.height = Math.max(1, Math.round(bmp.height * k));
      cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
      return cv.toDataURL("image/jpeg", 0.85);
    } catch (_) { return raw(); }
  }

  // ── 가맹 신청 패널: 본인인증 → 건물 정보 → 등기부 대조 → 사업자등록증 → 제출 ──
  async function renderApplication(box, f) {
    const data = await Bass.api(APP_ID, `/franchise/application?branch=${encodeURIComponent(S.currentBranch)}`);
    const a = data.application, st = a.steps, idn = a.identity, chk = a.registry.check;
    const biz = a.bizreg || {}, bchk = biz.check, bver = biz.verification;
    const done = (ok) => (ok ? "✅" : "⬜");
    const esc = Bass.escapeHtml;
    box.innerHTML = `
      <div class="bass-card" style="margin-top:10px;border-left:4px solid var(--warn);padding:12px;font-size:13px;line-height:1.6;">
        <strong>${esc(f.label || "")}</strong> — 매물 공개와 직거래 계약은 가맹 승인 후에 가능합니다. 아래 단계를 마치고 제출해 주세요.
        ${f.note ? `<div>사유: ${esc(f.note)}</div>` : ""}
        <div style="margin-top:8px;">
          <div><strong>${done(st.identity)} 1. 휴대폰 본인인증</strong>${idn.verified ? ` — ${esc(idn.name)} · ${esc(idn.phone)} ${idn.fake ? "<span class='bass-badge bass-badge--warn'>시연용 가짜 인증</span>" : ""}` : ""}</div>
          ${idn.verified ? "" : `
          <div class="field-grid" style="margin:6px 0;">
            <input id="fiName" class="bass-input" placeholder="실명">
            <input id="fiPhone" class="bass-input" placeholder="휴대폰 010-0000-0000">
            <input id="fiBirth" class="bass-input" placeholder="생년월일 6자리(650312) 또는 8자리">
          </div>
          <button id="btnIdStart" class="bass-btn bass-btn--sm" type="button">인증번호 받기</button>
          <input id="fiCode" class="bass-input" style="width:140px;display:inline-block;" placeholder="인증번호">
          <button id="btnIdConfirm" class="bass-btn bass-btn--sm bass-btn--primary" type="button">인증 확인</button>`}
        </div>
        <div style="margin-top:8px;">
          <div><strong>${done(st.building)} 2. 건물 정보</strong> — 등기부 소재지와 대조하는 기준입니다 (번지 포함).</div>
          <div class="field-grid" style="margin:6px 0;">
            <input id="fbName" class="bass-input" placeholder="건물명" value="${esc(a.building.building_name)}">
            <input id="fbAddr" class="bass-input" placeholder="실제 주소 (예: 인천 서해구 석남동 507-34)" value="${esc(a.building.address)}">
            <input id="fbValue" type="number" class="bass-input" placeholder="건물 가치(원, 선택)" value="${a.building.property_value || ""}">
          </div>
          <button id="btnBldgSave" class="bass-btn bass-btn--sm" type="button">건물 정보 저장</button>
        </div>
        <div style="margin-top:8px;">
          <div><strong>${done(st.registry)} 3. 등기부 대조</strong> — 인터넷등기소에서 받은 PDF·캡처를 올리세요.</div>
          <input id="frFile" type="file" accept="image/*,application/pdf" class="bass-input" ${st.identity ? "" : "disabled"}>
          <label class="check-row" style="margin:4px 0;"><span style="font-size:12px;">등기부 내용이 Google Gemini로 전송되어 자동 대조되는 것에 동의합니다 (미동의 시 관리자가 직접 확인).</span><input type="checkbox" id="frConsent"></label>
          <button id="btnRegUpload" class="bass-btn bass-btn--sm bass-btn--primary" type="button" ${st.identity ? "" : "disabled"}>올리고 대조하기</button>
          ${chk ? `<div style="margin-top:6px;border-left:4px solid ${CHECK_STYLE[chk.level] || "var(--warn)"};padding:6px 10px;background:#f8fafc;color:#1e293b;border-radius:8px;">${esc(chk.message)}${chk.auto_pass ? "" : "<br><span class='bass-muted'>관리자가 원본을 직접 확인한 뒤 결정합니다.</span>"}</div>` : ""}
        </div>
        <div style="margin-top:8px;">
          <div><strong>${done(st.bizreg)} 3-2. 📷 사업자등록증 촬영·제출 (홈택스 확인 후 승인)</strong></div>
          <div class="field-grid" style="margin:6px 0;">
            <input id="fbizNo" class="bass-input" placeholder="사업자등록번호 10자리 (예: 123-45-67890)" value="${esc(biz.business_number || "")}">
            <input id="fbizRep" class="bass-input" placeholder="대표자 성명 (선택)" value="${esc(biz.representative_name || idn.name || "")}">
            <input id="fbizComp" class="bass-input" placeholder="상호 (선택)" value="${esc(biz.company_name || "")}">
            <input id="fbizEmail" class="bass-input" type="email" placeholder="세금계산서 받을 이메일" value="${esc(biz.email || "")}">
            <input id="fbizType" class="bass-input" placeholder="업태 (예: 부동산업)" value="${esc(biz.business_type || "")}">
            <input id="fbizItem" class="bass-input" placeholder="종목 (예: 비주거용 건물 임대업)" value="${esc(biz.business_item || "")}">
          </div>
          ${bizPickerHtml("fbiz")}
          <label class="check-row" style="margin:4px 0;"><span style="font-size:12px;">사업자등록증을 AI로 읽어 사업자번호·대표자를 대조하는 것에 동의합니다.</span><input type="checkbox" id="fbizConsent"></label>
          <button id="btnBizUpload" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📷 사업자등록증 올리고 홈택스 확인 요청</button>
          ${bchk ? `<div style="margin-top:6px;border-left:4px solid ${CHECK_STYLE[bchk.level] || "var(--warn)"};padding:6px 10px;background:#f8fafc;color:#1e293b;border-radius:8px;">${esc(bchk.message)}</div>` : ""}
          ${bver ? `<div style="margin-top:4px;font-size:12.5px;">${bver.verified ? `✅ 홈택스 확인·승인 완료 (${esc(bver.business_number)} · ${esc(bver.hometax_status)})` : `⚠️ 반려됨: ${esc(bver.note || "")}`}</div>` : ""}
        </div>
        <div style="margin-top:10px;">
          <div><strong>${done(st.submitted)} 4. 가맹 신청 제출</strong>${a.applied_at ? ` — ${esc(String(a.applied_at).slice(0, 16).replace("T", " "))} 접수, 심사 대기 중` : ""}</div>
          <button id="btnApply" class="bass-btn bass-btn--primary" type="button" ${a.ready_to_submit ? "" : "disabled"} style="margin-top:4px;">${a.applied_at ? "다시 제출" : "가맹 신청 제출"}</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">호실 ${f.room_count}/${f.room_limit}실 등록 · 호실 정보는 승인 전에도 미리 준비할 수 있습니다.</div>
      </div>`;
    const $q = (id) => document.getElementById(id);
    const who = () => ({ branch: S.currentBranch, name: $q("fiName").value.trim(), phone: $q("fiPhone").value.trim(), birth: $q("fiBirth").value.trim() });
    const act = (id, fn, rerender = true) => {
      const el = $q(id);
      if (el) el.addEventListener("click", function () { Bass.withLock(this, async () => { await fn(); if (rerender) await renderFranchiseBanner(); }); });
    };
    act("btnIdStart", async () => {
      const r = await Bass.api(APP_ID, "/franchise/identity/start", { method: "POST", body: who() });
      Bass.updateAppAlert("success", r.message, r.hint || "");
    }, false);
    act("btnIdConfirm", async () => {
      const r = await Bass.api(APP_ID, "/franchise/identity/confirm", { method: "POST", body: { ...who(), code: $q("fiCode").value.trim() } });
      Bass.updateAppAlert("success", r.message);
    });
    act("btnBldgSave", async () => {
      const r = await Bass.api(APP_ID, "/franchise/building", { method: "POST", body: {
        branch: S.currentBranch, building_name: $q("fbName").value.trim(), address: $q("fbAddr").value.trim(), property_value: Number($q("fbValue").value || 0) } });
      Bass.updateAppAlert("success", r.message);
    });
    act("btnRegUpload", async () => {
      const file = $q("frFile").files[0];
      if (!file) { Bass.updateAppAlert("warning", "등기부 파일(PDF·캡처)을 먼저 골라 주세요."); return; }
      if (file.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다."); return; }
      const dataUrl = await new Promise((res, rej) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.onerror = rej; rd.readAsDataURL(file); });
      const r = await Bass.api(APP_ID, "/franchise/registry", { method: "POST", body: { branch: S.currentBranch, attachment_data_url: dataUrl, consent: $q("frConsent").checked } });
      Bass.updateAppAlert(r.level === "success" ? "success" : "warning", r.message);
    });
    bindBizPicker("fbiz");
    act("btnBizUpload", async () => {
      const dataUrl = await bizDataUrl("fbiz");
      if (!dataUrl) { Bass.updateAppAlert("warning", "사업자등록증 사진(또는 PDF)을 먼저 촬영하거나 골라 주세요."); return; }
      const r = await Bass.api(APP_ID, "/franchise/bizreg", {
        method: "POST",
        body: {
          branch: S.currentBranch,
          attachment_data_url: dataUrl,
          business_number: $q("fbizNo").value.trim(),
          representative_name: $q("fbizRep").value.trim(),
          company_name: $q("fbizComp").value.trim(),
          email: $q("fbizEmail").value.trim(), business_type: $q("fbizType").value.trim(), business_item: $q("fbizItem").value.trim(),
          consent: $q("fbizConsent").checked,
        },
      });
      Bass.updateAppAlert(r.level === "success" ? "success" : "warning", r.message);
    });
    act("btnApply", async () => {
      const r = await Bass.api(APP_ID, "/franchise/submit", { method: "POST", body: { branch: S.currentBranch } });
      Bass.updateAppAlert("success", r.message);
    });
  }

  async function renderFranchiseBanner() {
    const box = document.getElementById("franchiseBanner");
    if (!box) return;
    try {
      const st = await Bass.api(APP_ID, `/status?branch=${encodeURIComponent(S.currentBranch)}`);
      const f = st.franchise || {};
      if (f.open) { box.innerHTML = ""; return; }
      if (f.status === "PENDING_APPROVAL" || f.status === "REJECTED") { await renderApplication(box, f); return; }
      const msg = {
        PENDING_APPROVAL: "가맹 심사 중입니다. 승인되기 전에는 호실 정보를 미리 준비할 수 있고, 매물 공개와 직거래 계약은 승인 후에 가능합니다.",
        SUSPENDED: "이용이 정지되었습니다. 매물이 모두 비공개로 바뀌었고 새 계약을 시작할 수 없습니다.",
        REJECTED: "가맹 심사가 반려되었습니다.",
      }[f.status] || "가맹 승인 후에 매물을 공개할 수 있습니다.";
      box.innerHTML = `<div class="bass-card" style="margin-top:10px;border-left:4px solid var(--warn);padding:10px 12px;font-size:13px;line-height:1.6;">
        <strong>${Bass.escapeHtml(f.label || "")}</strong> — ${msg}${f.note ? `<br>사유: ${Bass.escapeHtml(f.note)}` : ""}
        <br><span class="bass-muted">호실 ${f.room_count}/${f.room_limit}실 등록</span></div>`;
    } catch (_) { box.innerHTML = ""; }
  }

  // 📢 멀티채널 원클릭 홍보 생성기
  async function showPromoModal(roomId, roomNo) {
    const E = Bass.escapeHtml;
    try {
      const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(roomId)}/promo`);
      const promo = res.promo;
      const channels = promo.channels || {};
      const channelKeys = Object.keys(channels);
      let curKey = "daangn";

      const body = openModal(`📢 ${E(roomNo)}호 멀티채널 원클릭 홍보 생성기`, `
        <div style="font-size:13.5px;color:var(--muted);margin-bottom:12px;line-height:1.6;">
          원클릭으로 <strong>당근마켓, 네이버 피터팬, 에브리타임, 네이버 블로그, 카톡</strong> 맞춤형 홍보글과 안심 직거래 링크를 복사하여 공실을 빠르게 채우세요!
        </div>
        <div class="promo-tabs" style="display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;border-bottom:1px solid var(--border);margin-bottom:12px;">
          ${channelKeys.map((k) => `
            <button type="button" class="bass-btn bass-btn--sm promo-tab-btn ${k === curKey ? "bass-btn--primary" : ""}" data-ch="${k}" style="min-height:36px;white-space:nowrap;padding:4px 10px;font-size:13px;">
              ${E(channels[k].icon)} ${E(channels[k].name)}
            </button>
          `).join("")}
        </div>
        <div id="promoContentWrap">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <span style="font-weight:600;font-size:13.5px;" id="promoChannelTitle"></span>
            <span class="bass-badge bass-badge--info" style="font-size:12px;">내용 직접 수정 가능</span>
          </div>
          <textarea id="promoTextarea" class="bass-input" rows="12" style="width:100%;font-size:13px;line-height:1.6;font-family:inherit;padding:10px;border-radius:8px;resize:vertical;"></textarea>
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;justify-content:flex-end;">
            <button type="button" id="btnOpenTermsPdfPromo" class="bass-btn bass-btn--sm" style="min-height:40px;">📄 표준 임대조건서 PDF 보기</button>
            <button type="button" id="btnCopyLinkOnly" class="bass-btn bass-btn--sm" style="min-height:40px;">🔗 안심 링크만 복사</button>
            <button type="button" id="btnOpenDaangnApp" class="bass-btn bass-btn--sm" style="min-height:40px;background:hsla(28,95%,55%,.15);border-color:hsla(28,95%,50%,.4);display:none;">🥕 당근 앱 열기</button>
            <button type="button" id="btnCopyAllPromo" class="bass-btn bass-btn--sm bass-btn--primary" style="min-height:40px;font-weight:600;">📋 홍보글 전체 복사</button>
          </div>
        </div>
      `);

      function updateChannelView(k) {
        curKey = k;
        body.querySelectorAll(".promo-tab-btn").forEach((b) => {
          b.classList.toggle("bass-btn--primary", b.dataset.ch === k);
        });
        const ch = channels[k] || {};
        const titleEl = body.querySelector("#promoChannelTitle");
        const areaEl = body.querySelector("#promoTextarea");
        const daangnBtn = body.querySelector("#btnOpenDaangnApp");
        if (titleEl) titleEl.textContent = `${ch.icon || ""} ${ch.name || ""} 맞춤형 서식`;
        if (areaEl) areaEl.value = ch.content || "";
        if (daangnBtn) daangnBtn.style.display = k === "daangn" ? "inline-flex" : "none";
      }

      body.querySelectorAll(".promo-tab-btn").forEach((b) => {
        b.addEventListener("click", () => updateChannelView(b.dataset.ch));
      });

      body.querySelector("#btnCopyAllPromo").addEventListener("click", async () => {
        const text = body.querySelector("#promoTextarea").value;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
          } else {
            const ta = body.querySelector("#promoTextarea");
            ta.select();
            document.execCommand("copy");
          }
          const chName = (channels[curKey] && channels[curKey].name) || "홍보글";
          Bass.updateAppAlert("success", `${chName} 홍보글이 클립보드에 복사되었습니다! 원하는 앱이나 웹사이트에 붙여넣으세요.`);
        } catch (_) {
          copyText(text);
          Bass.updateAppAlert("success", "클립보드에 복사되었습니다.");
        }
      });

      body.querySelector("#btnOpenTermsPdfPromo").addEventListener("click", () => {
        openLessorTermsPdf(roomId).catch(Bass.showError);
      });

      body.querySelector("#btnCopyLinkOnly").addEventListener("click", async () => {
        const link = (channels[curKey] && channels[curKey].link) || `https://${window.location.host}/rent3/?room=${encodeURIComponent(roomId)}`;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(link);
          } else {
            copyText(link);
          }
          Bass.updateAppAlert("success", "안심 직거래 링크가 복사되었습니다!");
        } catch (_) {
          window.prompt("아래 링크를 복사하세요:", link);
        }
      });

      body.querySelector("#btnOpenDaangnApp").addEventListener("click", () => {
        const scheme = "karrot://";
        const t0 = Date.now();
        window.location.href = scheme;
        setTimeout(() => {
          if (Date.now() - t0 < 1500) window.open("https://www.daangn.com", "_blank");
        }, 1000);
      });

      updateChannelView("daangn");
    } catch (err) {
      Bass.showError(err);
    }
  }

  // 더보기 ▸ 건물 정보 수정
  function showBuildingModal() {
    const b = (S.dash && S.dash.building) || {};
    const vatStr = (b.vat_rooms || []).join(", ");
    const incStr = (b.income_tax_rooms || []).join(", ");
    openModal("🏢 건물 정보 및 과세·종소세 대상 호실 수정", `
      <label class="r3-fld">건물 이름<input id="bdName" class="bass-input" value="${Bass.escapeHtml(b.building_name || "")}"></label>
      <label class="r3-fld" style="margin-top:8px;">주소<input id="bdAddr" class="bass-input" value="${Bass.escapeHtml(b.address || "")}"></label>
      <label class="r3-fld" style="margin-top:8px;">부가가치세 납부 대상 호실 (사무실·상가, 쉼표 구분)
        <input id="bdVatRooms" class="bass-input" value="${Bass.escapeHtml(vatStr)}" placeholder="예: 201, 202, 203, 301, 302, 303">
      </label>
      <label class="r3-fld" style="margin-top:8px;">종합소득세 신고 대상 호실 (쉼표 구분)
        <input id="bdIncRooms" class="bass-input" value="${Bass.escapeHtml(incStr)}" placeholder="예: 201, 202, 203, 301, 302, 303, 401, 402, 403...">
      </label>
      <button id="bdSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin-top:10px;">💾 저장</button>`);
    $m("bdSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/building/edit", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            building_name: $m("bdName").value,
            address: $m("bdAddr").value,
            vat_rooms: $m("bdVatRooms").value,
            income_tax_rooms: $m("bdIncRooms").value,
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await L.refresh();
      });
    });
  }

  async function showBizRegModal(mode) {
    if (mode === "tenant") return showTenantBizRegModal();
    const data = await Bass.api(APP_ID, `/franchise/application?branch=${encodeURIComponent(S.currentBranch)}`);
    const a = data.application || {}, biz = a.bizreg || {}, bchk = biz.check, bver = biz.verification;
    const esc = Bass.escapeHtml;
    openModal("📷 사업자등록증 촬영 · 국세청 홈택스 확인 승인", `
      <div class="bass-row" style="gap:6px;margin-bottom:8px;"><button type="button" class="bass-btn bass-btn--sm bass-btn--primary" disabled>임대인용 (내 사업자)</button><button type="button" id="mbizToTenant" class="bass-btn bass-btn--sm">임차인용 (세금계산서 발급)</button></div>
      <div style="font-size:13px;line-height:1.6;">
        <div class="bass-muted">사업자등록증(또는 임대사업자등록증)을 촬영해 올리시면 10자리 유효성·대표자 대조 후 관리인이 <strong>국세청 홈택스</strong>에서 사업자 상태를 확인해 승인합니다.</div>
        <div style="margin-top:8px;">현재 승인된 사업자번호: <code>${esc(biz.approved_business_number || "미등록")}</code></div>
        ${bver ? `<div style="margin-top:6px;padding:6px 10px;border-radius:8px;background:#f8fafc;color:#1e293b;border:1px solid #cbd5e1;">
          ${bver.verified ? `✅ <strong>홈택스 확인·승인 완료</strong> — <code>${esc(bver.business_number)}</code> (${esc(bver.hometax_status)}) · ${esc(String(bver.verified_at || "").slice(0, 16).replace("T", " "))}`
            : `⚠️ <strong>반려됨</strong> — 사유: ${esc(bver.note || "")}`}
        </div>` : biz.uploaded ? `<div style="margin-top:6px;"><span class="bass-badge bass-badge--warn">⏳ 관리자 홈택스 확인·승인 대기 중</span></div>` : ""}
        ${bchk ? `<div style="margin-top:6px;border-left:4px solid ${CHECK_STYLE[bchk.level] || "var(--warn)"};padding:6px 10px;">${esc(bchk.message)}</div>` : ""}
        <label class="r3-fld" style="margin-top:10px;">사업자등록번호 10자리 (선택 입력 · 사진에서도 자동 추출)
          <input id="mbizNo" class="bass-input" placeholder="예: 123-45-67890" value="${esc(biz.business_number || "")}">
        </label>
        <div class="r3-grid" style="margin-top:6px;">
          <label class="r3-fld">대표자 성명<input id="mbizRep" class="bass-input" value="${esc(biz.representative_name || "")}"></label>
          <label class="r3-fld">상호 (선택)<input id="mbizComp" class="bass-input" value="${esc(biz.company_name || "")}"></label>
          <label class="r3-fld">세금계산서 받을 이메일<input id="mbizEmail" class="bass-input" type="email" value="${esc(biz.email || "")}"></label>
          <label class="r3-fld">업태<input id="mbizType" class="bass-input" placeholder="예: 부동산업" value="${esc(biz.business_type || "")}"></label>
          <label class="r3-fld">종목<input id="mbizItem" class="bass-input" placeholder="예: 비주거용 건물 임대업" value="${esc(biz.business_item || "")}"></label>
        </div>
        <label class="r3-fld" style="margin-top:8px;">📷 사업자등록증 사진 촬영 또는 파일(JPG·PNG·PDF) 선택
          ${bizPickerHtml("mbiz")}
        </label>
        <label class="check-row" style="margin:6px 0;"><span style="font-size:12px;">사업자등록증을 AI로 읽어 사업자번호·대표자를 대조하는 것에 동의합니다.</span><input type="checkbox" id="mbizConsent"></label>
        <button id="mbizSubmit" class="bass-btn bass-btn--primary" type="button" style="margin-top:8px;">📷 사업자등록증 제출 (홈택스 확인 요청)</button>
      </div>`);
    bindBizPicker("mbiz");
    $m("mbizToTenant").addEventListener("click", () => showTenantBizRegModal().catch(Bass.showError));
    $m("mbizSubmit").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const dataUrl = await bizDataUrl("mbiz");
        if (!dataUrl) { Bass.updateAppAlert("warning", "사업자등록증 사진(또는 PDF)을 먼저 촬영하거나 골라 주세요."); return; }
        const r = await Bass.api(APP_ID, "/franchise/bizreg", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            attachment_data_url: dataUrl,
            business_number: $m("mbizNo").value.trim(),
            representative_name: $m("mbizRep").value.trim(),
            company_name: $m("mbizComp").value.trim(),
            email: $m("mbizEmail").value.trim(), business_type: $m("mbizType").value.trim(), business_item: $m("mbizItem").value.trim(),
            consent: $m("mbizConsent").checked,
          },
        });
        Bass.updateAppAlert(r.level === "success" ? "success" : "warning", r.message);
        await showBizRegModal();
        await L.refresh();
      });
    });
  }

  // 📷 임차인용 사업자등록증: 법인·사업자 임차인의 사업자등록증을 촬영해 그 호실 계약의 세금계산서 발급 정보에 반영한다.
  async function showTenantBizRegModal() {
    const E = Bass.escapeHtml;
    const occ = (S.dash.rooms || []).filter((r) => r.is_occupied && r.contract);
    const opt = (r) => `<option value="${E(r.room_no)}">${E(r.room_no)}호 ${E(r.contract.tenant_name || "")}${r.contract.tenant_business_number ? " · 등록됨" : ""}</option>`;
    openModal("📷 임차인 사업자등록증 (세금계산서 발급용)", `
      <div class="bass-row" style="gap:6px;margin-bottom:8px;"><button type="button" id="tbizToLessor" class="bass-btn bass-btn--sm">임대인용 (내 사업자)</button><button type="button" class="bass-btn bass-btn--sm bass-btn--primary" disabled>임차인용 (세금계산서 발급)</button></div>
      <div style="font-size:13px;line-height:1.6;">
        <div class="bass-muted">회사(법인·사업자)가 임차한 호실의 사업자등록증을 올리면 사업자등록번호·상호·대표자·주소를 읽어 <strong>그 호실 계약의 세금계산서 발급 정보</strong>에 저장합니다. 사업자가 아닌 임차인은 아무것도 저장되지 않으며, 번호가 없으면 현금영수증을 발행합니다.</div>
        <label class="r3-fld" style="margin-top:8px;">호실<select id="tbizRoom" class="bass-select">${occ.map(opt).join("") || '<option value="">입주 중인 호실이 없습니다</option>'}</select></label>
        <label class="r3-fld" style="margin-top:8px;">📷 사업자등록증 사진 촬영 또는 파일(JPG·PNG·PDF) 선택${bizPickerHtml("tbiz")}</label>
        <label class="check-row" style="margin:6px 0;"><span style="font-size:12px;">사업자등록증을 AI로 읽어 아래 칸을 채우는 것에 동의합니다. (사진 없이 직접 입력해도 됩니다)</span><input id="tbizConsent" type="checkbox" checked></label>
        <div class="r3-grid">
          <label class="r3-fld">사업자등록번호<input id="tbizNo" class="bass-input" placeholder="예: 123-45-67890"></label>
          <label class="r3-fld">상호<input id="tbizComp" class="bass-input"></label>
          <label class="r3-fld">대표자<input id="tbizCeo" class="bass-input"></label>
          <label class="r3-fld">세금계산서 받을 이메일<input id="tbizEmail" class="bass-input" type="email"></label>
        </div>
        <label class="r3-fld" style="margin-top:6px;">사업장 주소 (비우면 호실 주소)<input id="tbizAddr" class="bass-input"></label>
        <button id="tbizSubmit" class="bass-btn bass-btn--primary" type="button" style="width:100%;margin-top:10px;min-height:46px;">저장 (세금계산서 발급 정보에 반영)</button>
        <div id="tbizDone"></div>
      </div>`);
    bindBizPicker("tbiz");
    $m("tbizToLessor").addEventListener("click", () => showBizRegModal().catch(Bass.showError));
    $m("tbizSubmit").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const room_no = $m("tbizRoom").value;
        if (!room_no) { Bass.updateAppAlert("warning", "호실을 골라 주세요."); return; }
        const dataUrl = await bizDataUrl("tbiz");
        const r = await Bass.api(APP_ID, "/contracts/tenant-bizreg", { method: "POST", body: {
          branch: S.currentBranch, room_no, attachment_data_url: dataUrl || "", consent: $m("tbizConsent").checked && !!dataUrl,
          business_number: $m("tbizNo").value.trim(), company_name: $m("tbizComp").value.trim(), ceo_name: $m("tbizCeo").value.trim(),
          business_address: $m("tbizAddr").value.trim(), email: $m("tbizEmail").value.trim() } });
        Bass.updateAppAlert("success", r.message);
        const b = r.business || {};
        $m("tbizNo").value = b.business_number || ""; $m("tbizComp").value = b.company_name || ""; $m("tbizCeo").value = b.ceo_name || "";
        $m("tbizAddr").value = b.business_address || ""; $m("tbizEmail").value = b.email || "";
        $m("tbizDone").innerHTML = `<div style="margin-top:10px;padding:8px 10px;border-radius:10px;background:#dcfce7;color:#14532d;font-size:13px;">✅ ${E(r.room_no)}호 사업자 정보 저장 완료 — ${E(b.company_name || "")} ${E(b.business_number || "")}</div>
          <button id="tbizGoInv" class="bass-btn bass-btn--sm" type="button" style="margin-top:8px;width:100%;min-height:44px;">🧾 세금계산서 발행 화면으로</button>`;
        $m("tbizGoInv").addEventListener("click", () => { closeModal(); L.goTaxView("invoice"); });
        await L.refresh();
      });
    });
  }

  async function openLessorTermsPdf(roomId) {
    if (!roomId) return;
    const headers = {};
    const tk = Bass.token();
    if (tk) headers["Authorization"] = "Bearer " + tk;
    const resp = await fetch(`${Bass.apiBase(APP_ID)}/rooms/${encodeURIComponent(roomId)}/terms.pdf`, { headers });
    if (!resp.ok) throw new Error("표준 임대조건서 PDF를 불러올 수 없습니다.");
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function renderRoomsTab() {
    const rooms = S.dash.rooms || [];
    const bldg = (S.dash && S.dash.building) || {};
    const sortedRooms = rooms.slice().sort((a, b) => String(a.room_no).localeCompare(String(b.room_no), "ko", { numeric: true }));
    const DIRECTIONS = ["", "남향", "남동향", "남서향", "동향", "서향", "북향", "북동향", "북서향"];
    const OPTION_PRESETS = ["시스템 에어컨", "드럼 세탁기", "빌트인 냉장고", "인덕션", "디지털 도어락", "옷장", "침대", "책상", "전자레인지", "신발장"];
    const MAINT_PRESETS = ["수도료", "인터넷", "케이블TV", "공용전기", "건물청소", "승강기유지비"];
    const E = Bass.escapeHtml;
    const listingAlerts = S.dash.listing_alerts || [];
    const alertBannerHtml = listingAlerts.length ? `
      <div class="bass-card" style="margin-bottom:12px;padding:10px 12px;border-left:4px solid var(--primary,#2563eb);background:rgba(37,99,235,.06);">
        <div style="font-weight:800;font-size:13.5px;margin-bottom:6px;">🔔 PDF 작성 · 매물 등록 대기 호실 (${listingAlerts.length}실)</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;">
          ${listingAlerts.map((a) => `<button type="button" class="bass-btn bass-btn--sm bass-btn--primary nr-alert-pick" data-room="${E(a.room_no)}">📄 ${E(a.room_no)}호 선택 (${a.reason === "EARLY_MOVEOUT" ? "중도 퇴실" : "만기 30일 전"} · ${E(a.move_out_date || "예정")})</button>`).join("")}
        </div>
      </div>` : "";
    elTabContent.innerHTML = `
      ${alertBannerHtml}
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🏷️ 임대매물 등록 · 표준 임대조건서(PDF) 생성</h3>
        <p class="bass-muted" style="font-size:13px;margin:4px 0 10px;">표준임대차계약서를 참조해 주소·전용면적·보증금·옵션·설비·관리비 포함내역·특약을 입력하고 등록하면 <strong>표준 임대조건 안내서 PDF</strong>가 자동 생성되어 예비 임차인이 매물 사진에서 바로 열람할 수 있습니다.</p>
        <div style="font-size:13px;background:var(--surface-2,#f8fafc);border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin-bottom:10px;">
          📍 건물 소재지: <strong>${E(bldg.address || "주소 미등록 (더보기 ▸ 건물 정보 수정에서 입력)")}</strong> · ${E(bldg.building_name || S.currentBranch)}
        </div>
        <div class="field-grid" style="margin:10px 0;">
          <div>
            <label class="bass-label">호실</label>
            <select id="nrRoom" class="bass-select">
              <option value="">호실을 선택하세요</option>
              ${sortedRooms.map((r) => {
                const st = r.is_occupied
                  ? (r.pre_moveout_notice ? "입주중 · 전출예정 공개중" : "입주중 (전출예정 사전등록)")
                  : (r.is_published ? "공실 · 매물 공개중" : "공실");
                return `<option value="${E(r.id)}">${E(r.room_no)}호 — ${st}</option>`;
              }).join("")}
              <option value="__new__">➕ 목록에 없는 새 호실 추가…</option>
            </select>
          </div>
          <div id="nrNewWrap" style="display:none;"><label class="bass-label">새 호실 번호</label><input id="nrNo" class="bass-input" placeholder="예: 704"></div>
          <div>
            <label class="bass-label">부동산 유형</label>
            <select id="nrCat" class="bass-select">
              <option value="주거용 원룸">주거용 원룸 (면세)</option>
              <option value="오피스텔">오피스텔</option>
              <option value="상가/사무실">상가/사무실 (VAT 10%)</option>
            </select>
          </div>
          <div><label class="bass-label">입주가능일</label>
            <input id="nrAvail" type="date" class="bass-input">
            <label style="font-size:12.5px;display:flex;align-items:center;gap:6px;margin-top:6px;cursor:pointer;"><input id="nrAvailNow" type="checkbox" checked> 즉시입주</label>
          </div>
          <div><label class="bass-label">보증금(원)</label><input id="nrDep" type="number" class="bass-input" value="5000000"></div>
          <div><label class="bass-label">월세(원)</label><input id="nrRent" type="number" class="bass-input" value="500000"></div>
          <div><label class="bass-label">관리비(원)</label><input id="nrMaint" type="number" class="bass-input" value="50000"></div>
          <div><label class="bass-label">퇴실 청소비(원, 약정 시)</label><input id="nrClean" type="number" class="bass-input" value="0" min="0" step="10000"></div>
          <div>
            <label class="bass-label">월세 납부 시기</label>
            <select id="nrPayTiming" class="bass-select">
              <option value="후불">후불 (입주월 비과세 · 익월 납부)</option>
              <option value="선불">선불 (매월 선납)</option>
            </select>
          </div>
          <div><label class="bass-label">임대기간(개월)</label><input id="nrTermMonths" type="number" class="bass-input" value="12" min="1" max="60"></div>
          <div><label class="bass-label">전용면적(㎡)</label><input id="nrArea" type="number" step="0.1" class="bass-input" value="23"></div>
          <div><label class="bass-label">향</label>
            <select id="nrDir" class="bass-select">${DIRECTIONS.map((d) => `<option value="${d}">${d || "선택 안 함"}</option>`).join("")}</select>
          </div>
          <div><label class="bass-label">층</label><input id="nrFloor" type="number" class="bass-input" value="2"></div>
          <div><label class="bass-label">전체 층수</label><input id="nrTotalFloors" type="number" class="bass-input" value="0" placeholder="예: 7"></div>
        </div>
        <div style="margin-bottom:10px;">
          <label class="bass-label">매물 소개 (선택)</label>
          <input id="nrDesc" class="bass-input" placeholder="예: 남향 풀옵션, 도배·장판 새로 시공">
        </div>

        <details open style="border:1px solid var(--border);border-radius:12px;padding:12px;margin:0 0 12px;background:var(--surface-2,#f8fafc);">
          <summary style="cursor:pointer;font-weight:800;font-size:14px;">📄 표준 임대조건서(PDF) 상세 항목 (옵션 · 설비 · 관리비 포함 · 표준 특약)</summary>
          <div style="margin-top:10px;">
            <div class="bass-label" style="margin-bottom:6px;">🛋️ 옵션 리스트 (해당 항목 체크)</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px 12px;margin-bottom:8px;">
              ${OPTION_PRESETS.map((opt) => `<label style="display:inline-flex;align-items:center;gap:5px;font-size:13px;cursor:pointer;min-height:30px;"><input type="checkbox" class="nr-opt-chk" value="${E(opt)}"> ${E(opt)}</label>`).join("")}
            </div>
            <input id="nrOptExtra" class="bass-input" placeholder="추가 옵션 직접 입력 (쉼표 구분, 예: TV, 식탁, 블라인드)" style="margin-bottom:10px;">

            <div class="bass-label" style="margin-bottom:6px;">🏢 건물 · 호실 설비 설치 여부</div>
            <div class="field-grid" style="margin-bottom:10px;">
              <div>
                <label class="bass-label">📺 케이블 TV</label>
                <select id="nrCableTv" class="bass-select">
                  <option value="설치됨">설치됨</option>
                  <option value="미설치">미설치</option>
                  <option value="별도신청">별도신청</option>
                  <option value="미확인">미확인</option>
                </select>
              </div>
              <div>
                <label class="bass-label">🌐 인터넷 / Wi-Fi</label>
                <select id="nrInternet" class="bass-select">
                  <option value="설치됨">설치됨</option>
                  <option value="미설치">미설치</option>
                  <option value="별도신청">별도신청</option>
                  <option value="미확인">미확인</option>
                </select>
              </div>
              <div>
                <label class="bass-label">🛗 엘리베이터</label>
                <select id="nrElevator" class="bass-select">
                  <option value="설치됨">설치됨</option>
                  <option value="없음">없음</option>
                  <option value="미확인">미확인</option>
                </select>
              </div>
              <div>
                <label class="bass-label">🚗 주차 가능 여부</label>
                <select id="nrParking" class="bass-select">
                  <option value="가능">가능</option>
                  <option value="불가">불가</option>
                  <option value="협의">협의</option>
                  <option value="미확인">미확인</option>
                </select>
              </div>
            </div>

            <div class="bass-label" style="margin-bottom:6px;">🧾 관리비 포함 내역</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px 12px;margin-bottom:8px;">
              ${MAINT_PRESETS.map((it) => `<label style="display:inline-flex;align-items:center;gap:5px;font-size:13px;cursor:pointer;min-height:30px;"><input type="checkbox" class="nr-maint-chk" value="${E(it)}"> ${E(it)}</label>`).join("")}
            </div>
            <input id="nrMaintExtra" class="bass-input" placeholder="관리비 비고 (예: 전기·가스 개별계량 별도 부담)" style="margin-bottom:10px;">

            <div class="bass-label" style="margin-bottom:6px;">⚖️ 표준 임대조건 · 특약 및 계약 해지 사유</div>
            <div style="display:grid;gap:4px;margin-bottom:8px;">
              <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;min-height:30px;"><input id="nrNoPets" type="checkbox" checked> 🐾 반려동물(동물) 사육 금지</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;min-height:30px;"><input id="nrNoSmoking" type="checkbox" checked> 🚭 실내 흡연 금지 (호실 내부 및 공용부)</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;min-height:30px;"><input id="nrTerm2m" type="checkbox" checked> ⚖️ 2개월(2기) 이상 차임 체납 시 계약 해지 (주택임대차보호법 제10조의8 · 민법 제640조)</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;min-height:30px;"><input id="nrNoSublease" type="checkbox" checked> 🚫 무단 전대 · 양도 및 용도변경 금지</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;min-height:30px;"><input id="nrRestore" type="checkbox" checked> 🧹 퇴실 시 원상복구(자연 마모 제외) 및 약정 퇴실 청소비 공제</label>
            </div>
            <textarea id="nrCustomClauses" class="bass-input" rows="2" placeholder="추가 특약 사항 (선택, 줄바꿈으로 구분 · 예: 퇴실 1개월 전 통보, 벽면 못질 금지)"></textarea>
          </div>
        </details>

        <div id="nrMoveoutNoticeWrap" class="bass-card" style="display:none;margin:0 0 10px;padding:10px;background:var(--surface-2,#f8fafc);border-color:var(--warn,#d97706);">
          <div style="font-size:13px;font-weight:700;margin-bottom:4px;">📦 입주 중인 호실 — 중도 퇴실 · 만기 전출 사전 매물 등록</div>
          <div id="nrCurTermsHint" style="font-size:12.5px;margin-bottom:6px;line-height:1.55;"></div>
          <div class="bass-muted" style="font-size:12.5px;margin-bottom:8px;">현재 세입자가 입주 중입니다. <strong>중도 퇴실 확인 즉시</strong> 또는 <strong>만기 전출 예정(만기 30일 전 알림 수신 시)</strong> 아래 표준 임대조건을 작성해 PDF와 함께 사전 매물로 등록해 주세요. 위 금액은 다음 새 임차인용 조건으로 별도 저장되며 현재 계약에는 영향을 주지 않습니다.</div>
          <label style="display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600;cursor:pointer;min-height:36px;">
            <input id="nrPreMoveout" type="checkbox"> 현재 세입자의 중도 퇴실 또는 만기 전출이 확인되어 표준 임대조건서(PDF)와 함께 사전 매물로 공개합니다
          </label>
        </div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <button id="btnCreateRoom" class="bass-btn bass-btn--primary" type="button">📢 임대매물 등록 (PDF 자동 생성)</button>
          <button id="btnPreviewListingPdf" class="bass-btn" type="button" style="display:none;">📄 표준 임대조건서 PDF 보기</button>
          <button id="btnUnpublishRoom" class="bass-btn" type="button" style="display:none;">매물 내리기</button>
        </div>
      </div>

      <details class="bass-card" id="regCard" ${S.regOpen ? "open" : ""}>
        <summary style="cursor:pointer;font-weight:800;">📜 호실별 등기부 확인 (등기클린 표시)</summary>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin:8px 0;">인터넷등기소에서 받은 <strong>등기사항증명서(PDF 또는 사진)</strong>를 올리면 AI 가 소유자·근저당·압류 여부를 읽습니다. <strong>원본과 대조해 [확인 완료]를 눌러야</strong> 기록되고, 소유자가 임대인과 같고 근저당·압류가 없을 때만 임차인에게 '등기클린'이 표시됩니다(확정 후 ${REG_FRESH_DAYS}일 동안). AI 는 서류의 진위를 가리지 못하므로 참고용입니다.</div>
        ${rooms.map((r) => {
          const ri = r.registry_info || {}, st = ri.status || (r.registry_checked ? "confirmed" : "");
          const badge = st === "confirmed" && r.registry_checked ? `<span class="bass-badge bass-badge--ok">✅ 확인됨 ${Bass.escapeHtml(ri.verified_at || "")}</span>` : st === "pending" ? `<span class="bass-badge bass-badge--warn">⏳ 확인 대기</span>` : `<span class="bass-badge">미확인</span>`;
          const risks = (ri.risks || []).map((x) => `<li style="color:${x.level === "high" ? "var(--err)" : x.level === "caution" ? "var(--warn, #b45309)" : "inherit"};">${Bass.escapeHtml(x.text)}</li>`).join("");
          const pend = st === "pending" ? `
            <div style="border:1px solid var(--primary);border-radius:10px;padding:10px;margin-top:8px;font-size:13px;line-height:1.7;">
              <div>소유자 <strong>${Bass.escapeHtml(ri.owner_name || "읽지 못함")}</strong> ${ri.owner_matches ? "✅ 임대인과 같음" : "<span style='color:var(--err);font-weight:700;'>⚠️ 임대인과 다름 — 확정할 수 없습니다</span>"}</div>
              <div>유효 근저당 ${ri.mortgage_count || 0}건 · 채권최고액 합계 ${Number(ri.mortgage_amount || 0).toLocaleString()}원 · 압류·신탁 ${Bass.escapeHtml(ri.seizure_or_trust || "없음")} · 발급번호 ${Bass.escapeHtml(ri.issue_number || "읽지 못함")}</div>
              ${risks ? `<ul style="margin:6px 0 6px 18px;padding:0;">${risks}</ul>` : ""}
              <label style="display:flex;gap:6px;align-items:center;min-height:36px;"><input type="checkbox" class="reg-orig" data-id="${r.id}"> 인터넷등기소 원본과 대조했습니다</label>
              <label style="display:flex;gap:6px;align-items:center;min-height:36px;"><input type="checkbox" class="reg-all" data-id="${r.id}"> 같은 건물의 모든 호실에 적용</label>
              <button class="bass-btn bass-btn--primary btn-reg-confirm" data-id="${r.id}" type="button" style="min-height:44px;" ${ri.owner_matches ? "" : "disabled"}>✅ 확인 완료</button>
            </div>` : "";
          return `<div class="room-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;gap:8px;flex-wrap:wrap;"><strong>${r.room_no}호</strong>${badge}</div>
            <div class="bass-row" style="gap:8px;flex-wrap:wrap;margin-top:6px;align-items:center;">
              <input type="file" class="reg-file" data-id="${r.id}" accept="application/pdf,image/*" style="max-width:100%;">
              <label style="display:flex;gap:5px;align-items:center;font-size:12.5px;"><input type="checkbox" class="reg-consent" data-id="${r.id}"> AI 읽기에 동의(파일이 AI 서비스로 전송됩니다)</label>
              <button class="bass-btn bass-btn--sm btn-reg-read" data-id="${r.id}" type="button" style="min-height:44px;">📤 올려서 읽기</button>
            </div>${pend}
          </div>`;
        }).join("")}
      </details>

      <details class="bass-card">
        <summary style="cursor:pointer;font-weight:800;">🔑 호실별 도어락 · 등록 차량 관리</summary>
        <div style="margin-top:10px;">
        ${rooms.map((r) => `
          <div class="room-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <div>
                <strong>${r.room_no}호</strong> · 현재 도어락: <code>${Bass.escapeHtml(r.doorlock_pwd)}</code>
                · 등록차량: <strong>${Bass.escapeHtml((r.tenant && r.tenant.car_number) || "없음")}</strong>
              </div>
              <div class="bass-row">
                <input class="bass-input dl-inp" data-id="${r.id}" style="width:110px;" value="${Bass.escapeHtml(r.doorlock_pwd)}">
                <button class="bass-btn bass-btn--sm btn-dl-save" data-id="${r.id}" type="button">비번 변경</button>
              </div>
            </div>
          </div>
        `).join("")}
        </div>
      </details>
    `;

    const $nr = (id) => document.getElementById(id);
    const roomById = (id) => rooms.find((x) => x.id === id);

    function syncAvailInputs() {
      $nr("nrAvail").disabled = $nr("nrAvailNow").checked;
    }
    $nr("nrAvailNow").addEventListener("change", syncAvailInputs);
    syncAvailInputs();

    function applyListingSpecToForm(lm) {
      const spec = (lm && lm.listing_spec) || {};
      const optNames = Array.isArray(spec.options) && spec.options.length
        ? spec.options
        : ((lm && Array.isArray(lm.option_list)) ? lm.option_list.map((o) => (typeof o === "string" ? o : (o && (o.item || o.name)) || "")).filter(Boolean) : []);
      const presetSet = new Set(OPTION_PRESETS);
      elTabContent.querySelectorAll(".nr-opt-chk").forEach((el) => { el.checked = optNames.includes(el.value); });
      $nr("nrOptExtra").value = optNames.filter((x) => !presetSet.has(x)).join(", ");
      $nr("nrCableTv").value = spec.cable_tv || "설치됨";
      $nr("nrInternet").value = spec.internet || "설치됨";
      $nr("nrElevator").value = spec.elevator || "설치됨";
      $nr("nrParking").value = spec.parking || "가능";
      const maintInc = Array.isArray(spec.maintenance_includes) ? spec.maintenance_includes : [];
      elTabContent.querySelectorAll(".nr-maint-chk").forEach((el) => { el.checked = maintInc.includes(el.value); });
      $nr("nrMaintExtra").value = spec.maintenance_extra || "";
      $nr("nrTermMonths").value = Number(spec.lease_term_months || 12);
      $nr("nrNoPets").checked = spec.no_pets !== false;
      $nr("nrNoSmoking").checked = spec.no_smoking !== false;
      $nr("nrTerm2m").checked = spec.terminate_on_2m_arrears !== false;
      $nr("nrNoSublease").checked = spec.no_sublease !== false;
      $nr("nrRestore").checked = spec.restore_clause !== false;
      $nr("nrCustomClauses").value = spec.custom_clauses || "";
    }

    function collectListingSpecFromForm() {
      const checkedOpts = [...elTabContent.querySelectorAll(".nr-opt-chk:checked")].map((el) => el.value);
      const extraOpts = ($nr("nrOptExtra").value || "").split(/[,/\n]+/).map((s) => s.trim()).filter(Boolean);
      const options = [...new Set([...checkedOpts, ...extraOpts])];
      const maintenance_includes = [...elTabContent.querySelectorAll(".nr-maint-chk:checked")].map((el) => el.value);
      return {
        options,
        cable_tv: $nr("nrCableTv").value,
        internet: $nr("nrInternet").value,
        elevator: $nr("nrElevator").value,
        parking: $nr("nrParking").value,
        maintenance_includes,
        maintenance_extra: ($nr("nrMaintExtra").value || "").trim(),
        lease_term_months: Math.max(1, Number($nr("nrTermMonths").value || 12)),
        no_pets: $nr("nrNoPets").checked,
        no_smoking: $nr("nrNoSmoking").checked,
        terminate_on_2m_arrears: $nr("nrTerm2m").checked,
        no_sublease: $nr("nrNoSublease").checked,
        restore_clause: $nr("nrRestore").checked,
        custom_clauses: ($nr("nrCustomClauses").value || "").trim(),
      };
    }

    $nr("nrRoom").addEventListener("change", () => {
      const v = $nr("nrRoom").value;
      $nr("nrNewWrap").style.display = v === "__new__" ? "block" : "none";
      const r = roomById(v);
      const isOcc = Boolean(r && r.is_occupied);
      const isLive = Boolean(r && (r.is_published || r.pre_moveout_notice));
      $nr("nrMoveoutNoticeWrap").style.display = isOcc ? "block" : "none";
      $nr("nrPreMoveout").checked = Boolean(r && r.pre_moveout_notice);
      $nr("btnUnpublishRoom").style.display = isLive ? "inline-flex" : "none";
      $nr("btnPreviewListingPdf").style.display = r ? "inline-flex" : "none";
      $nr("btnCreateRoom").textContent = isLive ? "📢 변경 내용으로 다시 등록 (PDF 갱신)" : (isOcc ? "📢 전출예정 사전 매물 등록 (PDF 생성)" : "📢 임대매물 등록 (PDF 자동 생성)");
      if (!r) {
        $nr("nrAvailNow").disabled = false;
        $nr("nrAvailNow").checked = true;
        $nr("nrAvail").value = "";
        $nr("nrClean").value = 0;
        $nr("nrPayTiming").value = "후불";
        applyListingSpecToForm(null);
        syncAvailInputs();
        return;
      }
      if (isOcc && r.contract) {
        const c = r.contract;
        const maxDep5 = Math.round(Number(c.deposit || 0) * 1.05);
        const maxRent5 = Math.round(Number(c.monthly_rent || 0) * 1.05);
        $nr("nrCurTermsHint").innerHTML =
          `현재 계약: 보증금 <strong>${Number(c.deposit || 0).toLocaleString()}원</strong> / 월세 <strong>${Number(c.monthly_rent || 0).toLocaleString()}원</strong> / 관리비 ${Number(c.maintenance_fee || 0).toLocaleString()}원 / 청소비 ${Number(c.cleaning_fee || 0).toLocaleString()}원<br>` +
          `<span class="bass-muted">참고(+5% 기준): 보증금 ${maxDep5.toLocaleString()}원 · 월세 ${maxRent5.toLocaleString()}원 (신규 임차인용 매물 조건은 자유롭게 입력 가능)</span>`;
      } else {
        $nr("nrCurTermsHint").innerHTML = "";
      }
      const lm = isOcc ? { ...r, ...(r.next_terms || {}), ...(r.next_listing || {}) } : r;
      $nr("nrCat").value = lm.room_category || "주거용 원룸";
      $nr("nrDep").value = lm.deposit;
      $nr("nrRent").value = lm.monthly_rent;
      $nr("nrMaint").value = lm.maintenance_fee;
      $nr("nrClean").value = Number(lm.cleaning_fee || 0);
      $nr("nrPayTiming").value = lm.pay_timing === "선불" ? "선불" : "후불";
      $nr("nrArea").value = lm.area_sqm;
      $nr("nrDir").value = lm.direction || "";
      $nr("nrFloor").value = lm.floor;
      $nr("nrTotalFloors").value = lm.total_floors || 0;
      $nr("nrDesc").value = lm.status_description || "";
      applyListingSpecToForm(lm);
      const af = lm.available_from || "";
      const mDate = af.match(/\d{4}-\d{2}-\d{2}/);
      const alertHit = listingAlerts.find((a) => a.room_no === r.room_no);
      const plannedDate = (alertHit && /^\d{4}-\d{2}-\d{2}$/.test(alertHit.move_out_date || ""))
        ? alertHit.move_out_date
        : (r.expiry_check && /^\d{4}-\d{2}-\d{2}$/.test(r.expiry_check.planned_move_out_date || "") ? r.expiry_check.planned_move_out_date : "");
      const expDate = r.contract && /^\d{4}-\d{2}-\d{2}$/.test(r.contract.expiry_date || "") ? r.contract.expiry_date : "";
      if (isOcc) {
        $nr("nrAvailNow").checked = false;
        $nr("nrAvailNow").disabled = true;
        $nr("nrAvail").value = mDate ? mDate[0] : (plannedDate || expDate);
        if (alertHit || (r.expiry_check && r.expiry_check.tenant_plan === "MOVE_OUT")) {
          $nr("nrPreMoveout").checked = true;
        }
      } else {
        $nr("nrAvailNow").disabled = false;
        const isDate = Boolean(mDate);
        $nr("nrAvailNow").checked = !isDate;
        $nr("nrAvail").value = isDate ? mDate[0] : "";
      }
      syncAvailInputs();
    });

    const pickRoomByNo = (roomNo) => {
      const target = rooms.find((x) => x.room_no === roomNo);
      if (!target) return;
      $nr("nrRoom").value = target.id;
      $nr("nrRoom").dispatchEvent(new Event("change"));
      $nr("nrRoom").scrollIntoView({ behavior: "smooth", block: "center" });
    };
    elTabContent.querySelectorAll(".nr-alert-pick").forEach((btn) => {
      btn.addEventListener("click", () => pickRoomByNo(btn.dataset.room));
    });
    if (S.listingTargetRoomNo) {
      const targetNo = S.listingTargetRoomNo;
      S.listingTargetRoomNo = "";
      pickRoomByNo(targetNo);
    }

    $nr("btnPreviewListingPdf").addEventListener("click", function () {
      const r = roomById($nr("nrRoom").value);
      if (!r) return;
      Bass.withLock(this, () => openLessorTermsPdf(r.id), "PDF 생성 중...");
    });

    $nr("btnUnpublishRoom").addEventListener("click", function () {
      const r = roomById($nr("nrRoom").value);
      if (!r) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/publish`, {
          method: "POST",
          body: { is_published: false },
        });
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
      });
    });

    $nr("btnCreateRoom").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const sel = $nr("nrRoom").value;
        const picked = roomById(sel);
        const roomNo = sel === "__new__" ? $nr("nrNo").value.trim() : (picked ? picked.room_no : "");
        if (!roomNo) {
          Bass.updateAppAlert("warning", sel === "__new__" ? "새 호실 번호를 입력해 주세요." : "등록할 호실을 선택해 주세요.");
          return;
        }
        const isOcc = Boolean(picked && picked.is_occupied);
        if (isOcc && !$nr("nrPreMoveout").checked) {
          Bass.updateAppAlert("warning", "입주 중인 호실은 세입자의 전출 계획 확정 확인을 체크해 주세요.");
          return;
        }
        const avail = $nr("nrAvailNow").checked ? "즉시입주" : $nr("nrAvail").value;
        if (!avail || (isOcc && avail === "즉시입주")) {
          Bass.updateAppAlert("warning", isOcc ? "입주 중인 호실은 전출 후 입주가능일을 지정해 주세요." : "입주가능일을 고르거나 '즉시입주'를 체크해 주세요.");
          return;
        }
        const cat = $nr("nrCat").value;
        const listing_spec = collectListingSpecFromForm();
        const res = await Bass.api(APP_ID, "/rooms", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            room_no: roomNo,
            room_category: cat,
            vat_applicable: cat === "상가/사무실",
            deposit: Number($nr("nrDep").value || 0),
            monthly_rent: Number($nr("nrRent").value || 0),
            maintenance_fee: Number($nr("nrMaint").value || 0),
            cleaning_fee: Number($nr("nrClean").value || 0),
            pay_timing: $nr("nrPayTiming").value || "후불",
            area_sqm: Number($nr("nrArea").value || 0),
            direction: $nr("nrDir").value,
            floor: Number($nr("nrFloor").value || 0),
            total_floors: Number($nr("nrTotalFloors").value || 0),
            available_from: avail,
            status_description: $nr("nrDesc").value.trim() || "신규 등록 호실",
            listing_spec,
            is_published: true,
            pre_moveout_notice: isOcc ? Boolean($nr("nrPreMoveout").checked) : null,
          },
        });
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
        if (res.room && res.room.id) {
          showPromoModal(res.room.id, res.room.room_no);
        }
      });
    });

    elTabContent.querySelectorAll(".btn-dl-save").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const inp = elTabContent.querySelector(`.dl-inp[data-id="${btn.dataset.id}"]`);
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/doorlock`, {
            method: "POST",
            body: { new_pwd: inp.value.trim() },
          });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        });
      });
    });

    const regVal = (cls, id) => elTabContent.querySelector(`.${cls}[data-id="${id}"]`);
    elTabContent.querySelectorAll(".btn-reg-read").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const file = regVal("reg-file", btn.dataset.id).files[0];
          if (!file) { Bass.updateAppAlert("warning", "등기사항증명서 파일(PDF·사진)을 먼저 골라 주세요."); return; }
          if (file.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다."); return; }
          const dataUrl = await new Promise((res, rej) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.onerror = rej; rd.readAsDataURL(file); });
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/registry/read`, { method: "POST", body: { attachment_data_url: dataUrl, consent: regVal("reg-consent", btn.dataset.id).checked } });
          Bass.updateAppAlert("success", res.message);
          S.regOpen = true; await L.refresh();
        });
      });
    });
    elTabContent.querySelectorAll(".btn-reg-confirm").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/registry/confirm`, { method: "POST", body: { checked_original: regVal("reg-orig", btn.dataset.id).checked, all_rooms: regVal("reg-all", btn.dataset.id).checked } });
          Bass.updateAppAlert("success", res.message);
          S.regOpen = true; await L.refresh();
        });
      });
    });
  }

  Object.assign(L, {
    bizPickerHtml, bindBizPicker, bizDataUrl,
    renderApplication, renderFranchiseBanner,
    showPromoModal, showBuildingModal, showBizRegModal, showTenantBizRegModal,
    renderRoomsTab,
  });
})();
