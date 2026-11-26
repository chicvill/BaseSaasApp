/* apps/rent3_admin/frontend/lessors.js — 가맹 임대인 심사 · 월 가맹비 수납/세금계산서 · 전 지점 건물 현황 */
(function () {
  "use strict";
  const A = window.R3A;
  const { APP_ID, elTabContent, fmtWon } = A;
  const S = A.state;

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

  async function openAuthAttachment(path, errLabel) {
    const tk = Bass.token();
    const resp = await fetch(`${Bass.apiBase(APP_ID)}${path}`, { headers: tk ? { Authorization: "Bearer " + tk } : {} });
    if (!resp.ok) throw new Error(`${errLabel}을 불러오지 못했습니다 (${resp.status})`);
    const blobUrl = URL.createObjectURL(await resp.blob());
    window.open(blobUrl, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
  }

  // 💳 가맹비 징수 통보·납부 등록·세금계산서 기록 (앱은 국세청에 직접 발행하지 않는다 — 홈택스에서 발행한 뒤 승인번호를 기록)
  async function loadFeeBox() {
    const box = document.getElementById("feeBox");
    if (!box) return;
    const E = Bass.escapeHtml;
    const won = fmtWon;
    const r = await Bass.api(APP_ID, "/fees");
    const sm = r.summary || {};
    const pl = r.platform || {};
    const et = r.etax || {};
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
        R3Tax.openCard({
          title: card.title, writeDate: card.write_date, purpose: card.purpose, taxType: card.tax_type,
          supplier: card.supplier, buyer: card.buyer, items: card.items, note: card.note,
          missing: card.missing, cash: card.cash, taxAvailable: card.tax_available,
          onRecord: (mode) => (mode === "cash" ? recordCash(f) : recordFee(f)),
        });
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
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await resp.blob());
      a.download = "franchise_fee_hometax.csv";
      a.click();
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
    const lessors = (S.ov && S.ov.lessors) || [];
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
        await A.refresh();
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
          const cur = (S.ov.lessors || []).find((x) => x.id === btn.dataset.id) || {};
          const app = cur.application || {};
          let force = false;
          if (d === "approve" && cur.status === "PENDING_APPROVAL" && !(app.steps && app.steps.submitted && app.ready_to_submit)) {
            note = (window.prompt("가맹 신청 서류가 완료되지 않았습니다. 그래도 승인하려면 사유를 입력하세요 (기록으로 남습니다).") || "").trim();
            if (!note) { Bass.updateAppAlert("warning", "사유 없이는 서류 미비 상태로 승인할 수 없습니다."); return; }
            force = true;
          }
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/decision`, { method: "POST", body: { decision: d, note, force } });
          Bass.updateAppAlert("success", res.message);
          await A.refresh();
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
        Bass.withLock(this, () => openAuthAttachment(`/lessors/${encodeURIComponent(btn.dataset.id)}/registry-attachment`, "등기부 원본"));
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
          await A.refresh();
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
        Bass.withLock(this, () => openAuthAttachment(`/lessors/${encodeURIComponent(btn.dataset.id)}/bizreg-attachment`, "사업자등록증 원본"));
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
          await A.refresh();
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
          await A.refresh();
        });
      });
    });
  }

  function renderPortfolioTab() {
    const branches = (S.ov && S.ov.branches) || [];
    const rtms = (S.ov && S.ov.rtms_reports) || [];
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

  Object.assign(A, { loadFeeBox, renderLessorsTab, renderPortfolioTab });
})();
