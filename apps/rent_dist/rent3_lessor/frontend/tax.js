/* apps/rent_dist/rent3_lessor/frontend/tax.js — 세무 허브(종소세·부가세·세금계산서·경비/면세), 대상 호실, 가맹비 증빙, 관리비 산출근거. */
(function () {
  "use strict";
  const L = window.R3L;
  if (!L) return;

  const {
    APP_ID, elTabContent, state: S,
    fmtWon, $m, downloadWithAuth, openModal, closeModal,
  } = L;

  // 세무 허브의 한 화면(예: 세금계산서)으로 곧장 간다. 메뉴·창에서 쓴다.
  function goTaxView(view) {
    S.msgRoom = "";
    S.taxView = view; S.taxMonth = 0;
    S.currentTab = "tax";
    L.renderTab();
    window.scrollTo(0, 0);
  }

  // 💳 가맹비 납부·세금계산서 내역과 종합소득세 증빙(CSV·인쇄). 발행된 세금계산서는 장부 경비로 자동 등록되어 세무 허브에도 들어간다.
  async function showFeeEvidenceModal(year) {
    const yr = year || Number((S.dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const r = await Bass.api(APP_ID, `/franchise-fees?branch=${encodeURIComponent(S.currentBranch)}&year=${yr}`);
    const E = Bass.escapeHtml, ev = r.evidence || {}, t = ev.totals || {}, pl = r.platform || {};
    const row = (f) => `<div style="padding:7px 0;border-bottom:1px solid var(--border);font-size:13px;line-height:1.6;">
        <strong>${E(f.billing_month)}</strong> · ${fmtWon(f.total_amount)} <span class="bass-muted">(공급가 ${fmtWon(f.supply_amount)} + 부가세 ${fmtWon(f.vat_amount)})</span><br>
        <span class="bass-badge ${f.status === "PAID" ? "bass-badge--ok" : "bass-badge--warn"}">${E(f.state_label)}</span>
        <span class="bass-badge ${f.invoice_status === "ISSUED" ? "bass-badge--ok" : ""}">🧾 ${E(f.invoice_label)}</span>
        ${f.status === "NOTICED" ? ` <span class="bass-muted">납부 기한 ${E(f.due_date)}</span>` : ""}${f.paid_at ? ` <span class="bass-muted">입금 ${E(f.paid_at)}</span>` : ""}
        ${f.invoice_nts_no ? `<br><span class="bass-muted">승인번호</span> <code>${E(f.invoice_nts_no)}</code> <span class="bass-muted">작성일 ${E(f.invoice_issue_date)}</span>` : ""}</div>`;
    const body = openModal("💳 가맹비 납부 · 세금계산서 · 종합소득세 증빙", `
      <div style="font-size:13px;line-height:1.7;margin-bottom:8px;">월 가맹비 <strong>${fmtWon(r.monthly_fee)}</strong>(부가세 포함) · 공급자 ${E(pl.name || "")} ${E(pl.business_number || "")}<br>
        <span class="bass-muted">입금이 확인되면 전자세금계산서가 발급되고, 발급된 건은 <strong>장부의 경비(증빙: 세금계산서)</strong>로 자동 등록되어 부가세 매입세액·종합소득세 경비에 들어갑니다.</span></div>
      ${(r.fees || []).map(row).join("") || '<div class="bass-muted">청구 내역이 없습니다.</div>'}
      <div class="bass-card" style="margin-top:12px;padding:10px 12px;">
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;align-items:center;">
          <strong>${yr}년 종합소득세 증빙</strong>
          <select id="feeYear" class="bass-select" style="min-height:44px;">${[yr + 1, yr, yr - 1, yr - 2].map((y) => `<option value="${y}" ${y === yr ? "selected" : ""}>${y}년</option>`).join("")}</select>
        </div>
        <div style="font-size:13.5px;line-height:1.8;margin-top:6px;">세금계산서 발행분 <strong>${t.count_issued || 0}건 · ${fmtWon(t.issued_total)}</strong> (공급가 ${fmtWon(t.issued_supply)} + 부가세 ${fmtWon(t.issued_vat)})<br>
          ${t.count_pending ? `<span style="color:var(--warn);font-weight:700;">발행 대기 ${t.count_pending}건 · ${fmtWon(t.pending_total)}</span> <span class="bass-muted">— 세금계산서가 발급되기 전에는 증빙이 아닙니다.</span>` : `<span class="bass-muted">발행 대기 없음</span>`}</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;margin-top:8px;">
          <button id="feeCsvDl" type="button" class="bass-btn bass-btn--primary" style="min-height:46px;">📥 증빙 CSV 내려받기</button>
          <button id="feePrint" type="button" class="bass-btn" style="min-height:46px;">🖨️ 인쇄 / PDF 저장</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">종합소득세 신고 때 CSV(또는 인쇄본)와 홈택스의 전자세금계산서 조회 내역을 함께 보관하세요. 세무 허브의 경비 자료에도 같은 금액이 들어 있습니다.</div>
      </div>`);
    body.querySelector("#feeYear").addEventListener("change", (e) => showFeeEvidenceModal(Number(e.target.value)).catch(Bass.showError));
    body.querySelector("#feeCsvDl").addEventListener("click", () => downloadWithAuth(`/franchise-fees/export-csv?branch=${encodeURIComponent(S.currentBranch)}&year=${yr}`, `franchise_fee_evidence_${yr}.csv`));
    body.querySelector("#feePrint").addEventListener("click", () => window.print());
  }

  // 🧮 관리비 산출근거: 연간 공용 비용 항목별 합계 ÷ 종합소득세 적용 호실 수 = 호실당 금액
  async function showMgmtFeeModal(year) {
    const yr = Number(year) || Number((S.dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const d = await Bass.api(APP_ID, `/mgmt-fee/basis?branch=${encodeURIComponent(S.currentBranch)}&year=${yr}`);
    const E = Bass.escapeHtml;
    const cats = d.items || [];
    const evDefault = (c) => (c === "depreciation" ? "NONE" : "TAX_INVOICE");
    const rowsHtml = cats.map((i) => `<label class="mf-row"><input type="checkbox" class="mf-chk" data-cat="${E(i.category)}" ${i.included ? "checked" : ""}>
      <span class="mf-lbl">${E(i.label)}</span><span class="mf-cnt">${i.count ? i.count + "건" : "-"}</span><strong class="mf-amt" data-amt="${i.amount}">${fmtWon(i.amount)}</strong></label>`).join("");
    const yrs = [yr - 2, yr - 1, yr, yr + 1].filter((y) => y > 2000);
    const body = openModal(`🧮 ${E(d.year)}년 관리비 산출근거`, `
      <div class="bass-row" style="gap:8px;margin-bottom:6px;"><label class="r3-fld" style="flex:1;">기준 연도<select id="mfYear" class="bass-select">${yrs.map((y) => `<option value="${y}" ${y === d.year ? "selected" : ""}>${y}년</option>`).join("")}</select></label></div>
      <div style="font-size:12.5px;margin-bottom:6px;padding:6px 10px;border-radius:8px;background:#fef9c3;color:#713f12;">📌 이 산출 결과는 호실별 관리비 청구·수납에 반영되지 않습니다(계약서에 적힌 월세·관리비 금액을 그대로 따름). 여기서 등록한 항목은 세무 경비(종소세·부가세)에 자동으로 포함됩니다.</div>
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:4px;">체크한 항목만 합계에 넣습니다. 금액은 세무 허브의 경비(지출) 등록에서 가져옵니다.</div>
      <div>${rowsHtml}</div>
      <div class="mf-sum" id="mfSum"></div>
      <div class="bass-muted" style="font-size:12.5px;">${E(d.note || "")}</div>
      <details style="margin-top:10px;"><summary style="font-weight:700;cursor:pointer;">➕ 항목 금액 추가</summary>
        <div class="r3-grid" style="margin-top:8px;">
          <label class="r3-fld">항목<select id="mfCat" class="bass-select">${cats.map((i) => `<option value="${E(i.category)}">${E(i.label)}</option>`).join("")}</select></label>
          <label class="r3-fld">날짜<input id="mfDate" class="bass-input" type="date" value="${E(S.dash.today || "")}"></label>
          <label class="r3-fld">금액(원)<input id="mfAmt" class="bass-input" type="number" inputmode="numeric" placeholder="예: 1200000"></label>
          <label class="r3-fld">증빙<select id="mfEv" class="bass-select"><option value="TAX_INVOICE">세금계산서</option><option value="CARD">카드·현금영수증</option><option value="NONE">증빙 없음(감가상각 등)</option></select></label>
        </div>
        <label class="r3-fld" style="margin-top:6px;">내용(메모)<input id="mfTitle" class="bass-input" type="text" placeholder="예: 5월 엘리베이터 점검"></label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:13px;"><input id="mfTax" type="checkbox" checked> 세무 경비(종소세·부가세)에 포함 <span class="bass-muted">— 기본 켜짐(감가상각비만 꺼짐) · 끄면 이 화면의 참고용으로만 저장</span></label>
        <button id="mfAdd" class="bass-btn bass-btn--primary" type="button" style="width:100%;min-height:44px;margin-top:8px;">저장</button>
      </details>`);
    const sumEl = body.querySelector("#mfSum");
    const recalc = () => {
      let total = 0;
      body.querySelectorAll(".mf-row").forEach((row) => { if (row.querySelector(".mf-chk").checked) total += Number(row.querySelector(".mf-amt").dataset.amt || 0); });
      const n = d.room_count || 0;
      const py = n ? Math.round(total / n) : 0;
      const pm = Math.round(py / 12);
      sumEl.innerHTML = `포함 항목 합계 <strong>${fmtWon(total)}</strong><br>
        ÷ 종합소득세 적용 호실 <strong>${n}실</strong>${d.tax_rooms_configured ? "" : ` <span class="bass-muted">(호실을 따로 정하지 않아 전체 ${d.all_room_count}실 기준)</span>`}<br>
        = 호실당 연 <span class="mf-big">${fmtWon(py)}</span> · 월 <span class="mf-big">${fmtWon(pm)}</span><br>
        <span class="bass-muted" style="font-size:12.5px;">지금 청구 중인 관리비 평균: 월 ${fmtWon(d.charged_fee_avg)} ${pm > 0 ? (d.charged_fee_avg >= pm ? "(산출액 이상)" : `(산출액보다 ${fmtWon(pm - d.charged_fee_avg)} 적음)`) : ""}</span>`;
    };
    body.querySelectorAll(".mf-chk").forEach((c) => c.addEventListener("change", recalc));
    recalc();
    body.querySelector("#mfYear").addEventListener("change", (e) => showMgmtFeeModal(e.target.value).catch(Bass.showError));
    const syncTaxDefault = () => { const c = body.querySelector("#mfCat").value; body.querySelector("#mfTax").checked = c !== "depreciation"; body.querySelector("#mfEv").value = evDefault(c); };
    body.querySelector("#mfCat").addEventListener("change", syncTaxDefault);
    syncTaxDefault();
    body.querySelector("#mfAdd").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const amount = Number(body.querySelector("#mfAmt").value || 0);
        if (!(amount > 0)) { Bass.updateAppAlert("warning", "금액을 입력해 주세요."); return; }
        const cat = body.querySelector("#mfCat").value;
        const label = (cats.find((i) => i.category === cat) || {}).label || cat;
        const res = await Bass.api(APP_ID, "/outlays", { method: "POST", body: {
          branch: S.currentBranch, category: cat, title: body.querySelector("#mfTitle").value.trim() || label, amount,
          expense_date: body.querySelector("#mfDate").value || undefined, evidence_type: body.querySelector("#mfEv").value, reference_only: !body.querySelector("#mfTax").checked } });
        Bass.updateAppAlert("success", res.message);
        await showMgmtFeeModal(d.year);
      });
    });
  }

  // ── 더보기 ▸ 부가세 · 종소세 · 경비: 연도별 자료, 세금계산서 대장 & 홈택스 CSV, 면세 사업장현황신고, 경비 지출 ──
  async function renderTaxTab(useCache) {
    if (!useCache || !S.taxCache) elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    const yr = S.taxYear || Number((S.dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const moParam = S.taxMonth > 0 ? `&month=${S.taxMonth}` : "";
    let d;
    const cacheKey = `${S.currentBranch}|${yr}|${S.taxMonth}`;
    if (useCache && S.taxCache && S.taxCache.key === cacheKey) {
      d = S.taxCache.d;
    } else {
      try {
        d = await Bass.api(APP_ID, `/tax/summary?branch=${encodeURIComponent(S.currentBranch)}&year=${yr}${moParam}`);
      } catch (e) { Bass.showError(e); return; }
      S.taxCache = { key: cacheKey, d };
    }
    if (S.currentTab !== "tax") return;
    S.taxYear = d.year;
    const fin = d.summary || {};
    const bCfg = d.building_tax_config || {};
    const invReg = d.tax_invoice_register || {};
    const exemptRep = d.exempt_housing_report || {};
    const exemptRooms = exemptRep.rooms || [];
    const exemptTotals = exemptRep.totals || { room_count: 0, deposit_total: 0, annual_rent_total: 0 };
    const vatRoomsList = bCfg.vat_rooms || fin.vat_rooms || [];
    const incRoomsList = bCfg.income_tax_rooms || fin.income_tax_rooms || [];
    const vatRoomBreakdown = fin.vat_room_breakdown || [];
    const vatPayRows = fin.vat_payment_rows || [];
    const incRoomBreakdown = fin.income_tax_room_breakdown || [];
    const incPayRows = fin.income_tax_payment_rows || [];
    const stickyTh = 'style="position:sticky;top:0;background:var(--surface, #1e293b);z-index:1;"';
    const scrollBox5 = 'style="max-height:215px;overflow-y:auto;overflow-x:auto;border:1px solid var(--border, rgba(148,163,184,0.22));border-radius:8px;"';
    const invoices = invReg.invoices || [];
    const invTotals = invReg.totals || { count: 0, supply_amount: 0, tax_amount: 0, total_amount: 0, ready_for_export_count: 0, issued_count: 0, incomplete_count: 0 };
    const supInfo = invReg.supplier || {};
    const cats = d.expense_categories || {};
    const years = [d.year + 1, d.year, d.year - 1, d.year - 2].filter((v, i, a) => a.indexOf(v) === i && v <= Number((S.dash.today || "").slice(0, 4)) + 1);
    const inputVatTotal = fin.input_vat_deductible || 0;
    const rawInputVatTotal = fin.raw_input_vat_total || 0;
    const ratioPct = fin.taxable_supply_ratio_pct ?? 100;
    const disclaimerText = d.tax_disclaimer || fin.tax_disclaimer || "⚠️ 참고용 추정치입니다. 실제 신고 전 반드시 세무사·홈택스에서 확인하세요.";

    const statusBadge = (inv) => {
      if (inv.status === "ISSUED") return `<span class="bass-badge bass-badge--ok">발행완료${inv.nts_confirm_no ? ` (${Bass.escapeHtml(inv.nts_confirm_no)})` : ""}</span>`;
      if (inv.status === "MODIFIED") return `<span class="bass-badge bass-badge--info">수정발행${inv.nts_confirm_no ? ` (${Bass.escapeHtml(inv.nts_confirm_no)})` : ""}</span>`;
      if (inv.status === "CANCELLED") return `<span class="bass-badge bass-badge--err">발행취소</span>`;
      if (!inv.ready_for_hometax) return `<span class="bass-badge bass-badge--warn">⚠️ 미비(내보내기 제외): ${Bass.escapeHtml((inv.missing_fields || []).join(", "))}</span>`;
      return `<span class="bass-badge bass-badge--info">✅ 발급가능(미발행)</span>`;
    };

    const yearRow = `<div class="bass-row" style="gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap;">
        <label class="r3-lbl" for="taxYearSel" style="margin:0;white-space:nowrap;">귀속연도</label>
        <select id="taxYearSel" class="bass-select" style="width:auto;font-size:16px;">${years.map((y) => `<option value="${y}" ${y === d.year ? "selected" : ""}>${y}년</option>`).join("")}</select>
      </div>`;
    const monthRow = `<div class="bass-row" style="gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap;">
        <label class="r3-lbl" for="taxMonthSel" style="margin:0;white-space:nowrap;">발급 대상 월</label>
        <select id="taxMonthSel" class="bass-select" style="width:auto;font-size:16px;">
          <option value="0" ${S.taxMonth === 0 ? "selected" : ""}>전체 월 (연간)</option>
          ${[1,2,3,4,5,6,7,8,9,10,11,12].map((m) => `<option value="${m}" ${m === S.taxMonth ? "selected" : ""}>${m}월</option>`).join("")}
        </select>
        <button id="btnDlHometaxCsv" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 홈택스 세금계산서 일괄발급 CSV</button>
      </div>`;
    const incBtns = `<div class="bass-row" style="gap:8px;margin-bottom:10px;"><button id="btnDlTaxCsv" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 종소세 명세서 CSV</button></div>`;
    const exeBtns = `<div class="bass-row" style="gap:8px;margin-bottom:10px;"><button id="btnDlExemptCsv" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 면세 사업장현황신고 CSV</button></div>`;
    const compAllHub = fin.comprehensive_combined_estimate || {};
    const vatNetHub = (d.vat_periods || []).reduce((a, x) => a + Number(x.net_vat_payable ?? x.vat ?? 0), 0);
    const hubBtn = (v, icon, title, line) => `<button type="button" class="bass-btn tax-hub-btn" data-v="${v}" style="display:block;width:100%;text-align:left;padding:14px;margin-bottom:10px;">
        <div style="font-size:16px;font-weight:800;">${icon} ${title}</div><div class="bass-muted" style="font-size:13px;margin-top:4px;line-height:1.5;">${line}</div></button>`;
    const hub = hubBtn("income", "🧮", "종합소득세", `총수입 ${fmtWon(fin.gross_revenue)} · 예상 종합소득세 ${fmtWon(compAllHub.total_tax_inc_local || 0)}`) +
      hubBtn("vat", "🧾", "부가세", `1기·2기 매출세액 − 매입세액 · 연간 예상 납부세액 ${fmtWon(vatNetHub)}${vatNetHub < 0 ? " (마이너스는 환급 예상)" : ""}`) +
      hubBtn("invoice", "📑", "세금계산서", `전체 ${invTotals.count || 0}건 · 발행완료 ${invTotals.issued_count || 0}건 · 내보내기 가능 ${invTotals.ready_for_export_count || 0}건 · 정보미비 ${invTotals.incomplete_count || 0}건`) +
      hubBtn("expense", "🗂️", "경비 · 면세 신고", `경비 ${(d.outlays || []).length}건 · 면세 주거 호실 ${exemptTotals.room_count || 0}개 · 사업장현황신고 자료`);
    const TAX_TITLES = { income: "종합소득세", vat: "부가세", invoice: "세금계산서", expense: "경비 · 면세 신고" };
    const taxBody = {
      income: incBtns + `      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 6px;">🧮 종합소득세 자료 (${d.year}년 약정 귀속월 기준 · 부가세 제외 공급가액)</h3>
        ${(() => {
          const compAll = fin.comprehensive_combined_estimate || {};
          const compComm = fin.commercial_comprehensive_estimate || {};
          return `<div style="font-size:13.5px;line-height:1.8;">
            • 연간 총수입금액(부가세 제외 공급가액) <strong>${fmtWon(fin.gross_revenue)}</strong>
              <span class="bass-muted" style="font-size:12px;">(부가세 포함 총수납액 ${fmtWon(fin.gross_receipts_inc_vat ?? fin.gross_revenue)} 중 부가세 예수금 ${fmtWon(fin.vat_collected || 0)} 제외 · 수납 ${fin.payment_count || 0}건)</span><br>
            ${fin.carryover_excluded_amount ? `• <span style="color:var(--warn, #f59e0b);font-size:12.5px;">장부이관 과거 누적 이월 수납분(<strong>${fmtWon(fin.carryover_excluded_amount)}</strong>, ${fin.carryover_excluded_count || 0}건)은 연간 수입 왜곡 방지를 위해 당해 수입에서 자동 제외되었습니다.</span><br>` : ""}
            • <strong>① 주택임대 수입 (주거용 면세 호실)</strong>: <strong>${fmtWon(fin.housing_revenue || 0)}</strong>
              ${fin.housing_separate_eligible
                ? `→ 연 2,000만 원 이하 분리과세(15.4%) 선택 가능: 일반(필요경비율 50%·공제 200만) 예상 <strong>${fmtWon(fin.housing_separate_tax_general || 0)}</strong> / 등록임대(60%·400만) 예상 <strong>${fmtWon(fin.housing_separate_tax_registered || 0)}</strong>`
                : `→ 연 2,000만 원 초과로 분리과세 대상 아님 (아래 <strong>종합과세 누진세율 구간</strong>으로 합산 산출)`}<br>
            • <strong>② 상가·사무실 임대 수입 (과세 호실)</strong>: <strong>${fmtWon(fin.commercial_revenue || 0)}</strong>
              → 분리과세 불가 · 실제 필요경비(안분 ${fmtWon(fin.commercial_expense_allocated || 0)}) 차감 후 순소득 <strong>${fmtWon(fin.commercial_noi || 0)}</strong>
              ${fin.housing_separate_eligible && (fin.commercial_noi || 0) > 0
                ? `(상가 단독 종합과세 추정: <strong>${fmtWon(compComm.total_tax_inc_local || 0)}</strong> · ${Bass.escapeHtml(compComm.bracket_label || "")})`
                : ""}<br>
            • 건물 전체 실제 필요경비 합계: <strong>${fmtWon(fin.total_operating_expense)}</strong> · 전체 순임대소득(NOI): <strong style="color:var(--primary-2);">${fmtWon(fin.noi)}</strong><br>
            • <strong>③ 종합소득세 누진세율 추정 (주택+상가 합산 순임대소득 ${fmtWon(compAll.net_income || 0)} · 기본공제 ${fmtWon(compAll.basic_deduction || 0)} 반영 과세표준 ${fmtWon(compAll.taxable_base || 0)})</strong>:<br>
              &nbsp;&nbsp;적용 구간: <strong>${Bass.escapeHtml(compAll.bracket_label || "-")}</strong> →
              산출세액 ${fmtWon(compAll.income_tax || 0)} + 지방소득세(10%) ${fmtWon(compAll.local_income_tax || 0)} =
              예상 종합소득세 합계 <strong style="color:var(--primary-2);">${fmtWon(compAll.total_tax_inc_local || 0)}</strong>
              <span class="bass-muted" style="font-size:12px;">(타 소득 미합산 단독 임대소득 기준 참고용 추정치)</span>
          </div>`;
        })()}
        <div style="font-weight:700;font-size:13px;margin:8px 0 4px;">📊 종합소득세 호실별 수입 산출내역 (5줄 스크롤)</div>
        ${incRoomBreakdown.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>호실</th><th>임차인</th><th>수납건수</th><th>월세 합계</th><th>관리비 합계</th><th>종소세 수입(공급가액)</th><th>총수납액(VAT포함)</th></tr>
            </thead>
            <tbody>
              ${incRoomBreakdown.map((rb) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(rb.room_no)}호</strong></td>
                  <td>${Bass.escapeHtml(rb.tenant_name || "-")}</td>
                  <td>${rb.count}건</td>
                  <td>${fmtWon(rb.rent_amount)}</td>
                  <td>${fmtWon(rb.maintenance_amount)}</td>
                  <td><strong>${fmtWon(rb.supply_amount)}</strong></td>
                  <td>${fmtWon(rb.total_amount)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:12.5px;">종소세 대상 호실 수납 내역이 없습니다.</div>'}
        <details style="margin-top:8px;">
          <summary style="cursor:pointer;font-size:12.5px;font-weight:700;">🔍 호실별 월세·관리비 건별 납부 상세내역 보기 (${incPayRows.length}건)</summary>
          <div ${scrollBox5} style="max-height:215px;overflow-y:auto;overflow-x:auto;margin-top:6px;border:1px solid var(--border, rgba(148,163,184,0.22));border-radius:8px;">
            <table class="bass-table" style="width:100%;font-size:12px;margin:0;">
              <thead ${stickyTh}>
                <tr><th>납부일</th><th>귀속월(약정)</th><th>호실</th><th>입금자</th><th>월세</th><th>관리비</th><th>종소세 수입(공급가액)</th><th>부가세</th><th>총수납액</th></tr>
              </thead>
              <tbody>
                ${incPayRows.map((pr) => `
                  <tr>
                    <td>${Bass.escapeHtml(pr.paid_date)}</td>
                    <td>${Bass.escapeHtml(pr.billing_month)}</td>
                    <td><strong>${Bass.escapeHtml(pr.room_no)}호</strong></td>
                    <td>${Bass.escapeHtml(pr.payer_name || "-")}</td>
                    <td>${fmtWon(pr.rent_amount)}</td>
                    <td>${fmtWon(pr.maintenance_amount)}</td>
                    <td><strong>${fmtWon(pr.supply_amount)}</strong></td>
                    <td>${fmtWon(pr.vat_amount)}</td>
                    <td>${fmtWon(pr.total_amount)}</td>
                  </tr>
                `).join("") || '<tr><td colspan="9" class="bass-muted">내역 없음</td></tr>'}
              </tbody>
            </table>
          </div>
        </details>
      </div>`,
      vat: `      <div class="bass-row" style="justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
        <span class="bass-muted" style="font-size:13px;">부가세 대상 ${vatRoomsList.length}개 호실 · 종소세 대상 ${incRoomsList.length}개 호실</span>
        <button id="btnGoBldgTax" class="bass-btn bass-btn--sm" type="button">📑 대상 호실 설정 →</button>
      </div>` + `      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 6px;">🧾 부가가치세 자료 (${d.year}년 · 매출세액 − 공제 매입세액 · 공통매입 안분 반영)</h3>
        ${(d.vat_periods || []).map((p) => `<div style="font-size:13.5px;line-height:1.8;margin-bottom:4px;">
          <strong>${Bass.escapeHtml(p.label)}</strong> · 과세 공급가액 ${fmtWon(p.supply)} ·
          매출세액(실수납) <strong>${fmtWon(p.output_vat ?? p.vat)}</strong> −
          공제 매입세액(안분후) <strong>${fmtWon(p.input_vat || 0)}</strong> =
          예상 납부세액 <strong style="color:var(--primary-2);">${fmtWon(p.net_vat_payable ?? p.vat)}</strong>
          ${p.non_deductible_expense ? `<span class="bass-muted" style="font-size:12px;">(불공제 경비 ${fmtWon(p.non_deductible_expense)} 제외)</span>` : ""}
        </div>`).join("")}
        <div class="bass-muted" style="font-size:12px;margin-top:4px;margin-bottom:8px;">
          • 과세(상가·사무실)와 면세(주거)가 함께 있는 건물의 <strong>공통경비(엘리베이터·공용전기·청소 등) 매입세액은 과세공급비율(${ratioPct}%)로 안분</strong>하여 공제합니다 (원본 매입세액 ${fmtWon(rawInputVatTotal)} → 공제 가능 ${fmtWon(inputVatTotal)}).<br>
          • <strong>적격증빙(세금계산서·사업용카드·현금영수증) + 일반과세자 거래처</strong> 지출만 매입세액 공제되며, 간이과세자 영수증·면세(화재보험)·경조사비·주거전용 수리비는 매입세액에서 제외(종소세 경비로만 반영)됩니다.<br>
          • 기존 수납 중 부가세가 0원인 건은 임의로 10%를 추정하지 않고 <strong>실제 수납된 부가세만</strong> 집계하여 요약과 표가 100% 일치합니다.
        </div>
        <div style="font-weight:700;font-size:13px;margin:8px 0 4px;">📊 부가세 호실별 산출내역 (과세 호실별 1기·2기·연간 집계 · 5줄 스크롤)</div>
        ${vatRoomBreakdown.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>호실</th><th>임차인</th><th>수납건수</th><th>1기 공급가액(세액)</th><th>2기 공급가액(세액)</th><th>연간 공급가액</th><th>연간 부가세(실수납)</th></tr>
            </thead>
            <tbody>
              ${vatRoomBreakdown.map((rb) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(rb.room_no)}호</strong></td>
                  <td>${Bass.escapeHtml(rb.tenant_name || "-")}</td>
                  <td>${rb.count}건</td>
                  <td>${fmtWon(rb.h1_supply)} (${fmtWon(rb.h1_vat)})</td>
                  <td>${fmtWon(rb.h2_supply)} (${fmtWon(rb.h2_vat)})</td>
                  <td><strong>${fmtWon(rb.supply_amount)}</strong></td>
                  <td><strong>${fmtWon(rb.vat_amount)}</strong></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:12.5px;">과세 호실 수납 내역이 없습니다.</div>'}
        <details style="margin-top:8px;">
          <summary style="cursor:pointer;font-size:12.5px;font-weight:700;">🔍 과세 호실 건별 납부 상세내역 보기 (${vatPayRows.length}건)</summary>
          <div ${scrollBox5} style="max-height:215px;overflow-y:auto;overflow-x:auto;margin-top:6px;border:1px solid var(--border, rgba(148,163,184,0.22));border-radius:8px;">
            <table class="bass-table" style="width:100%;font-size:12px;margin:0;">
              <thead ${stickyTh}>
                <tr><th>납부일</th><th>귀속월(약정)</th><th>호실</th><th>입금자</th><th>월세</th><th>관리비</th><th>공급가액</th><th>부가세</th><th>합계</th></tr>
              </thead>
              <tbody>
                ${vatPayRows.map((pr) => `
                  <tr>
                    <td>${Bass.escapeHtml(pr.paid_date)}</td>
                    <td>${Bass.escapeHtml(pr.billing_month)}</td>
                    <td><strong>${Bass.escapeHtml(pr.room_no)}호</strong></td>
                    <td>${Bass.escapeHtml(pr.payer_name || "-")}</td>
                    <td>${fmtWon(pr.rent_amount)}</td>
                    <td>${fmtWon(pr.maintenance_amount)}</td>
                    <td>${fmtWon(pr.supply_amount)}</td>
                    <td>${fmtWon(pr.vat_amount)}</td>
                    <td><strong>${fmtWon(pr.total_amount)}</strong></td>
                  </tr>
                `).join("") || '<tr><td colspan="9" class="bass-muted">내역 없음</td></tr>'}
              </tbody>
            </table>
          </div>
        </details>
      </div>`,
      invoice: monthRow + `<div class="bass-muted" style="font-size:12.5px;margin:0 0 8px;line-height:1.6;">사업자번호가 없는 과세 호실은 아래에서 <strong>현금영수증</strong>을 기록합니다. 정보미비로 표시된 건은 <strong>수납 현황 ▸ 해당 호실 ▸ 계약내용</strong>에서 사업자등록번호·상호·대표자명·이메일·사업자등록상 주소를 입력하면 발급 가능해집니다 (주거용·기숙사 호실 포함). 사업자등록증 사진이 있으면 <strong>☰ 메뉴 ▸ 사업자등록증 촬영 ▸ 임차인용</strong>에서 올려 자동으로 채울 수 있습니다.</div>` + `      <div class="bass-card" style="margin-bottom:12px;">
        <div class="bass-row" style="justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;">
          <h3 style="margin:0;">📑 세금계산서 발급 리스트 (청구월 약정 지급일 기준 월 1건 · 5줄 스크롤)</h3>
          <button id="btnDlHometaxCsv2" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 홈택스 일괄발급 포맷(CSV)</button>
        </div>
        ${supInfo.supplier_ready === false ? `<div style="font-size:12.5px;color:var(--warn, #f59e0b);margin-bottom:6px;">⚠️ 공급자(임대인) 사업자등록번호(${Bass.escapeHtml(supInfo.business_number || "미입력")})를 확인해 주세요: ${Bass.escapeHtml((supInfo.supplier_issues || []).join(", "))}</div>` : ""}
        <div class="bass-muted" style="font-size:12px;margin-bottom:6px;">
          월세 세금계산서는 입금 건별이 아니라 <strong>청구월(약정 지급일) 기준 호실당 월 1장</strong>으로 집계됩니다.
          필수 정보(사업자번호 10자리·상호·대표자명·이메일)가 빠진 건이나 이미 <strong>발행완료(ISSUED)</strong>로 기록된 건은 이중 발행·오류 방지를 위해 CSV 내보내기에서 자동 제외됩니다.
        </div>
        <div style="font-size:13.5px;margin-bottom:8px;">
          전체 <strong>${invTotals.count || 0}건</strong> (CSV 내보내기 가능 <strong>${invTotals.ready_for_export_count || 0}건</strong> · 발행완료 <strong>${invTotals.issued_count || 0}건</strong> · 정보미비 제외 <strong>${invTotals.incomplete_count || 0}건</strong>) ·
          공급가액 합계 <strong>${fmtWon(invTotals.supply_amount || 0)}</strong> ·
          세액(10%) 합계 <strong>${fmtWon(invTotals.tax_amount || 0)}</strong> ·
          합계금액 <strong>${fmtWon(invTotals.total_amount || 0)}</strong>
        </div>
        ${invoices.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>귀속월(약정일)</th><th>호실 / 품목</th><th>사업자번호</th><th>상호 / 대표자</th><th>이메일</th><th>공급가액 / 세액</th><th>검증·발행상태</th><th>관리</th></tr>
            </thead>
            <tbody>
              ${invoices.map((inv, idx) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(inv.billing_month)}</strong><br><span class="bass-muted" style="font-size:12.5px;">작성일 ${Bass.escapeHtml(inv.write_date)}</span></td>
                  <td><strong>${Bass.escapeHtml(inv.room_no)}호</strong><br><span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(inv.item_name || "")}</span></td>
                  <td>${Bass.escapeHtml(inv.buyer_business_number || "-")}${inv.buyer_biz_checksum_warning ? `<br><span style="color:var(--warn);font-size:12.5px;">${Bass.escapeHtml(inv.buyer_biz_checksum_warning)}</span>` : ""}</td>
                  <td>${Bass.escapeHtml(inv.buyer_company_name || "-")} (${Bass.escapeHtml(inv.buyer_ceo_name || "-")})</td>
                  <td>${Bass.escapeHtml(inv.buyer_email || "-")}</td>
                  <td>${fmtWon(inv.supply_amount)}<br><span class="bass-muted" style="font-size:12.5px;">세액 ${fmtWon(inv.tax_amount)}</span><br><span class="bass-badge ${inv.charge_type === "02" ? "bass-badge--warn" : "bass-badge--ok"}">${Bass.escapeHtml(inv.charge_type_label || (inv.charge_type === "02" ? "청구(02)" : "영수(01)"))}</span></td>
                  <td>${statusBadge(inv)}</td>
                  <td><button type="button" class="bass-btn bass-btn--sm bass-btn--primary btn-inv-card" data-idx="${idx}" style="min-height:44px;">${(inv.buyer_business_number_digits || "").length === 10 && inv.buyer_business_number_checksum_ok ? "📋 발급 정보·홈택스" : "💵 현금영수증 정보·홈택스"}</button><br><button type="button" class="bass-btn bass-btn--sm btn-inv-status" data-idx="${idx}" style="min-height:44px;margin-top:4px;">발행기록</button></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:13px;">해당 기간에 세금계산서 발급 대상 내역이 없습니다.</div>'}
      </div>`,
      expense: exeBtns + `      <div class="bass-card" style="margin-bottom:12px;">
        <div class="bass-row" style="justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;">
          <h3 style="margin:0;">🏠 주택임대(면세) 사업장현황신고 자료 (${d.year}년 귀속 · 매년 2월 10일 마감 · 5줄 스크롤)</h3>
          <button id="btnDlExemptCsv2" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📥 사업장현황신고 CSV</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-bottom:6px;">
          주거용(면세) 호실 임대는 부가가치세 대신 <strong>매년 2월 10일까지 전년도 호실별 보증금·월세 수입금액(수입금액 검토표)</strong>을 국세청에 신고합니다.
          (대상 ${exemptTotals.room_count || 0}개 호실 · 보증금 합계 <strong>${fmtWon(exemptTotals.deposit_total || 0)}</strong> · 연간 월세수입 합계 <strong>${fmtWon(exemptTotals.annual_rent_total || 0)}</strong> · ${Bass.escapeHtml(exemptTotals.deemed_rent_note || "")})
        </div>
        ${exemptRooms.length ? `<div ${scrollBox5}>
          <table class="bass-table" style="width:100%;font-size:12.5px;margin:0;">
            <thead ${stickyTh}>
              <tr><th>호실</th><th>임차인</th><th>임대기간</th><th>보증금</th><th>약정 월세</th><th>연간 월세수입</th><th>수납건수</th></tr>
            </thead>
            <tbody>
              ${exemptRooms.map((er) => `
                <tr>
                  <td><strong>${Bass.escapeHtml(er.room_no)}호</strong></td>
                  <td>${Bass.escapeHtml(er.tenant_name || "-")}</td>
                  <td>${Bass.escapeHtml(er.move_in_date || "-")} ~ ${Bass.escapeHtml(er.expiry_date || "-")}</td>
                  <td>${fmtWon(er.deposit)}</td>
                  <td>${fmtWon(er.monthly_rent)}</td>
                  <td><strong>${fmtWon(er.annual_rent_revenue)}</strong></td>
                  <td>${er.payment_count}건</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>` : '<div class="bass-muted" style="font-size:12.5px;">면세 주거용 호실이 없습니다.</div>'}
      </div>` + `      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 8px;">➕ 경비 지출 등록 (적격증빙·거래처 과세유형·공통매입 안분 구분)</h3>
        <div class="r3-grid">
          <label class="r3-fld">지출일<input id="outDate" class="bass-input" type="date" value="${Bass.escapeHtml(S.dash.today || "")}"></label>
          <label class="r3-fld">분류<select id="outCat" class="bass-select">${Object.entries(cats).map(([k, v]) => `<option value="${k}">${Bass.escapeHtml(v)}</option>`).join("")}</select></label>
          <label class="r3-fld">적요<input id="outTitle" class="bass-input" placeholder="예: 202호 보일러 수리"></label>
          <label class="r3-fld">금액(원)<input id="outAmt" type="number" class="bass-input" value="0"></label>
          <label class="r3-fld">거래처 상호<input id="outVendor" class="bass-input" placeholder="예: 한빛설비"></label>
          <label class="r3-fld">거래처 사업자번호<input id="outVendorBiz" class="bass-input" placeholder="예: 120-81-12345"></label>
          <label class="r3-fld">거래처 과세유형
            <select id="outVendorTax" class="bass-select">
              <option value="GENERAL">일반과세자 (매입공제 가능)</option>
              <option value="SIMPLIFIED">간이과세자 (영수증 불공제)</option>
              <option value="EXEMPT">면세사업자·비사업자 (불공제)</option>
            </select>
          </label>
          <label class="r3-fld">증빙유형
            <select id="outEvid" class="bass-select">
              <option value="TAX_INVOICE">전자세금계산서</option>
              <option value="CARD">사업용 신용카드</option>
              <option value="CASH_RECEIPT">지출증빙 현금영수증</option>
              <option value="SIMPLE_RECEIPT">간이영수증·계좌이체 (불공제)</option>
              <option value="EXEMPT">면세·경조사 청첩장 (불공제)</option>
            </select>
          </label>
          <label class="r3-fld">과세·면세 사용구분
            <select id="outScope" class="bass-select">
              <option value="COMMON">건물 공통경비 (과세비율 안분)</option>
              <option value="TAXABLE_ONLY">상가·과세호실 전용 (100% 공제)</option>
              <option value="EXEMPT_ONLY">주거·면세호실 전용 (부가세 불공제)</option>
            </select>
          </label>
          <label class="r3-fld">메모<input id="outMemo" class="bass-input"></label>
        </div>
        <button id="btnAddOutlay" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin-top:8px;">💾 저장</button>
      </div>` + `      <div class="bass-card">
        <h3 style="margin:0 0 6px;">📋 경비 내역 (${(d.outlays || []).length}건) <span class="bass-muted" style="font-size:12px;">터치하면 고칠 수 있습니다</span></h3>
        ${(fin.expenses_by_category || []).filter((c) => c.amount > 0).map((c) => `<span class="bass-badge bass-badge--info" style="margin:2px;">${Bass.escapeHtml(c.label)} ${fmtWon(c.amount)}</span>`).join("")}
        <div id="outlayList" style="margin-top:8px;">${(d.outlays || []).map((o) => `
          <button type="button" class="outlay-row bass-btn" data-id="${o.id}" style="display:grid;grid-template-columns:78px 1fr auto;gap:6px;width:100%;text-align:left;margin-bottom:4px;font-size:13px;">
            <span>${Bass.escapeHtml(o.expense_date.slice(2).replace(/-/g, "."))}</span>
            <span>${Bass.escapeHtml(o.title)}<br><span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(o.category_label)}${o.vendor ? " · " + Bass.escapeHtml(o.vendor) : ""} · ${o.is_vat_deductible === false ? "부가세 불공제(종소세 경비)" : `공제 매입세액 ${fmtWon(o.effective_input_vat ?? o.input_vat ?? 0)}`}</span></span>
            <strong>${fmtWon(o.amount)}</strong></button>`).join("") || '<div class="bass-muted">등록된 경비가 없습니다.</div>'}</div>
      </div>`,
    };
    const backRow = `<div class="bass-row" style="gap:8px;margin:0 0 10px;"><button id="btnTaxHub" class="bass-btn bass-btn--sm" type="button">← 세무 허브</button><strong style="font-size:15px;">${TAX_TITLES[S.taxView] || ""}</strong></div>`;
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:10px;padding:10px 12px;border-left:4px solid var(--warn, #f59e0b);background:rgba(245,158,11,0.08);font-size:13px;font-weight:700;">
        ${Bass.escapeHtml(disclaimerText)}
      </div>
      ${yearRow}
      ${S.taxView === "hub" || !taxBody[S.taxView] ? hub : backRow + taxBody[S.taxView]}`;

    const on = (id, ev, fn2) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn2); };
    on("taxYearSel", "change", (e) => { S.taxYear = Number(e.target.value); renderTaxTab(); });
    on("taxMonthSel", "change", (e) => { S.taxMonth = Number(e.target.value); renderTaxTab(); });
    on("btnDlTaxCsv", "click", () => {
      downloadWithAuth(`/tax/export-csv?branch=${encodeURIComponent(S.currentBranch)}&year=${d.year}`, `rent3_tax_statement_${S.currentBranch}_${d.year}.csv`);
    });
    const dlHometax = () => {
      const moQ = S.taxMonth > 0 ? `&month=${S.taxMonth}` : "";
      const moSuf = S.taxMonth > 0 ? `_${String(S.taxMonth).padStart(2, "0")}` : "";
      downloadWithAuth(`/tax/export-hometax-csv?branch=${encodeURIComponent(S.currentBranch)}&year=${d.year}${moQ}`, `hometax_tax_invoice_${S.currentBranch}_${d.year}${moSuf}.csv`);
    };
    on("btnDlHometaxCsv", "click", dlHometax);
    on("btnDlHometaxCsv2", "click", dlHometax);
    const dlExempt = () => {
      downloadWithAuth(`/tax/export-exempt-csv?branch=${encodeURIComponent(S.currentBranch)}&year=${d.year}`, `exempt_housing_status_${S.currentBranch}_${d.year}.csv`);
    };
    on("btnDlExemptCsv", "click", dlExempt);
    on("btnDlExemptCsv2", "click", dlExempt);

    elTabContent.querySelectorAll(".tax-hub-btn").forEach((b) => b.addEventListener("click", () => {
      S.taxView = b.dataset.v;
      if (S.taxView !== "invoice") S.taxMonth = 0;
      renderTaxTab(true);
      window.scrollTo(0, 0);
    }));
    on("btnTaxHub", "click", () => { S.taxView = "hub"; S.taxMonth = 0; renderTaxTab(true); window.scrollTo(0, 0); });

    elTabContent.querySelectorAll(".btn-inv-card").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inv = invoices[Number(btn.dataset.idx)];
        if (!inv) return;
        const wd = String(inv.write_date || "").replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
        const miss = [...(inv.missing_fields || [])];
        const bizOk = (inv.buyer_business_number_digits || "").length === 10 && Boolean(inv.buyer_business_number_checksum_ok);
        R3Tax.openCard({
          title: `${inv.billing_month} ${inv.room_no}호 임대료 세금계산서`, writeDate: wd, purpose: inv.charge_type === "01" ? "영수" : "청구", taxType: "과세",
          supplier: { business_number: supInfo.business_number, company_name: supInfo.company_name, ceo_name: supInfo.ceo_name, address: supInfo.address, biz_type: supInfo.biz_type, biz_item: supInfo.biz_item, email: supInfo.email },
          buyer: { business_number: inv.buyer_business_number, company_name: inv.buyer_company_name, ceo_name: inv.buyer_ceo_name, address: inv.buyer_address, email: inv.buyer_email },
          items: [{ name: inv.item_name, supply: inv.supply_amount, tax: inv.tax_amount }], missing: miss,
          taxAvailable: bizOk,
          cash: { kind: bizOk ? "지출증빙용" : "소득공제용", identifier: bizOk ? inv.buyer_business_number_digits : String(inv.tenant_phone || "").replace(/\D/g, ""), identifier_label: bizOk ? "사업자등록번호(지출증빙용)" : "휴대폰 번호(소득공제용)",
                  date: wd, amount: Number(inv.total_amount || 0), item: inv.item_name, email: inv.buyer_email },
          onRecord: (mode) => {
            if (mode !== "cash") { const rec = elTabContent.querySelectorAll(".btn-inv-status")[Number(btn.dataset.idx)]; if (rec) rec.click(); return; }
            const no = (window.prompt("홈택스 현금영수증 발급 후 승인번호 (숫자 6~12자리)") || "").trim();
            if (!no) return;
            Bass.withLock(null, async () => {
              const res = await Bass.api(APP_ID, "/tax/cash-receipt", { method: "POST", body: { branch: S.currentBranch, room_no: inv.room_no, billing_month: inv.billing_month, approval_no: no } });
              Bass.updateAppAlert("success", res.message);
            });
          },
        });
      });
    });
    elTabContent.querySelectorAll(".btn-inv-status").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inv = invoices[Number(btn.dataset.idx)];
        if (inv) showInvoiceStatusModal(inv);
      });
    });

    on("btnGoBldgTax", "click", () => L.switchTab("bldgtax"));
    on("btnAddOutlay", "click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/outlays", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            category: document.getElementById("outCat").value,
            title: document.getElementById("outTitle").value.trim() || "유지보수",
            amount: Number(document.getElementById("outAmt").value || 0),
            vendor: document.getElementById("outVendor").value.trim(),
            vendor_business_number: document.getElementById("outVendorBiz").value.trim(),
            vendor_tax_type: document.getElementById("outVendorTax").value,
            evidence_type: document.getElementById("outEvid").value,
            usage_scope: document.getElementById("outScope").value,
            memo: document.getElementById("outMemo").value.trim(),
            expense_date: document.getElementById("outDate").value || null,
          },
        });
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
      });
    });
    elTabContent.querySelectorAll(".outlay-row").forEach((row) => row.addEventListener("click", () => {
      const o = (d.outlays || []).find((x) => x.id === row.dataset.id);
      if (o) showOutlayModal(o, cats);
    }));
  }

  // 건물 ▸ 부가세 · 소득세 대상 호실 설정 (설정 화면은 이곳 한 곳에만 둔다)
  async function renderBldgTaxTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    let d;
    try {
      d = await Bass.api(APP_ID, `/tax/summary?branch=${encodeURIComponent(S.currentBranch)}&year=${S.taxYear || Number((S.dash.today || "").slice(0, 4))}`);
    } catch (e) { Bass.showError(e); return; }
    if (S.currentTab !== "bldgtax") return;
    const fin = d.summary || {};
    const bCfg = d.building_tax_config || {};
    const vatRoomsList = bCfg.vat_rooms || fin.vat_rooms || [];
    const incRoomsList = bCfg.income_tax_rooms || fin.income_tax_rooms || [];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:12px;">
        <h3 style="margin:0 0 6px;">🏢 건물 부가세 · 종합소득세 대상 호실 설정</h3>
        <div class="r3-grid">
          <label class="r3-fld">부가세 납부 대상 호실 (사무실·상가)
            <input id="txVatRooms" class="bass-input" value="${Bass.escapeHtml(vatRoomsList.join(", "))}" placeholder="예: 201, 202, 203, 301, 302, 303">
          </label>
          <label class="r3-fld">종합소득세 신고 대상 호실
            <input id="txIncRooms" class="bass-input" value="${Bass.escapeHtml(incRoomsList.join(", "))}" placeholder="예: 201, 202, 203, 301, 302, 303, 401~703">
          </label>
        </div>
        <div class="bass-row" style="gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap;">
          <label style="font-size:13px;display:flex;align-items:center;gap:6px;cursor:pointer;">
            <input id="txSyncContracts" type="checkbox"> 현재 입주 계약에도 과세 여부 즉시 동기화
          </label>
          <button id="btnSaveTaxRooms" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 대상 호실 저장</button>
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:6px;">
          현재 설정: 부가세 대상 <strong>${vatRoomsList.length}개 호실</strong> (${Bass.escapeHtml(vatRoomsList.join(", ") || "없음")}) ·
          종소세 대상 <strong>${incRoomsList.length}개 호실</strong>
        </div>
      </div>
    `;
    document.getElementById("btnSaveTaxRooms").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/building/tax-rooms", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            vat_rooms: document.getElementById("txVatRooms").value,
            income_tax_rooms: document.getElementById("txIncRooms").value,
            sync_contracts: Boolean(document.getElementById("txSyncContracts").checked),
          },
        });
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
      });
    });
  }

  function showInvoiceStatusModal(inv) {
    const hist = inv.history || [];
    openModal(`📑 ${Bass.escapeHtml(inv.room_no)}호 (${Bass.escapeHtml(inv.billing_month)}) 세금계산서 발행 상태 기록`, `
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:8px;">
        작성일자(약정일): <strong>${Bass.escapeHtml(inv.write_date)}</strong> · 공급가액 ${fmtWon(inv.supply_amount)} · 세액 ${fmtWon(inv.tax_amount)}<br>
        공급받는자: ${Bass.escapeHtml(inv.buyer_company_name || "-")} (${Bass.escapeHtml(inv.buyer_business_number || "-")})
      </div>
      <div class="r3-grid">
        <label class="r3-fld">발행 상태
          <select id="isStatus" class="bass-select">
            <option value="DRAFT" ${inv.status === "DRAFT" ? "selected" : ""}>미발행 (작성대기 · CSV 포함)</option>
            <option value="ISSUED" ${inv.status === "ISSUED" ? "selected" : ""}>발행완료 (중복발행 방지 CSV 제외)</option>
            <option value="MODIFIED" ${inv.status === "MODIFIED" ? "selected" : ""}>수정발행</option>
            <option value="CANCELLED" ${inv.status === "CANCELLED" ? "selected" : ""}>발행취소 (CSV 제외)</option>
          </select>
        </label>
        <label class="r3-fld">국세청 승인번호 (24자리 등)
          <input id="isNtsNo" class="bass-input" value="${Bass.escapeHtml(inv.nts_confirm_no || "")}" placeholder="예: 20260715-41000000-12345678">
        </label>
      </div>
      <label class="r3-fld" style="margin-top:8px;">비고 / 수정·취소 사유
        <input id="isMemo" class="bass-input" placeholder="예: 홈택스 일괄발급 완료">
      </label>
      ${hist.length ? `<div class="bass-muted" style="font-size:12.5px;margin-top:8px;">
        <strong>상태 변경 이력:</strong><br>
        ${hist.map((h) => `• ${Bass.escapeHtml((h.at || "").slice(0, 16))} : ${Bass.escapeHtml(h.from_status)} → <strong>${Bass.escapeHtml(h.to_status)}</strong> ${h.nts_confirm_no ? `(${Bass.escapeHtml(h.nts_confirm_no)})` : ""} ${Bass.escapeHtml(h.memo || "")}`).join("<br>")}
      </div>` : ""}
      <div class="bass-row" style="gap:6px;margin-top:10px;">
        <button id="isSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 발행 상태 저장</button>
      </div>`);
    $m("isSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/tax/invoices/status", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            room_no: inv.room_no,
            billing_month: inv.billing_month,
            status: $m("isStatus").value,
            nts_confirm_no: $m("isNtsNo").value.trim(),
            memo: $m("isMemo").value.trim(),
            write_date: inv.write_date,
            supply_amount: inv.supply_amount,
            tax_amount: inv.tax_amount,
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await renderTaxTab();
      });
    });
  }

  function showOutlayModal(o, cats) {
    openModal("✏️ 경비 내역 수정", `
      <div class="r3-grid">
        <label class="r3-fld">지출일<input id="oeDate" class="bass-input" type="date" value="${Bass.escapeHtml(o.expense_date)}"></label>
        <label class="r3-fld">분류<select id="oeCat" class="bass-select">${Object.entries(cats).map(([k, v]) => `<option value="${k}" ${k === o.category ? "selected" : ""}>${Bass.escapeHtml(v)}</option>`).join("")}</select></label>
        <label class="r3-fld">적요<input id="oeTitle" class="bass-input" value="${Bass.escapeHtml(o.title)}"></label>
        <label class="r3-fld">금액(원)<input id="oeAmt" type="number" class="bass-input" value="${o.amount}"></label>
        <label class="r3-fld">거래처 상호<input id="oeVendor" class="bass-input" value="${Bass.escapeHtml(o.vendor || "")}"></label>
        <label class="r3-fld">거래처 사업자번호<input id="oeVendorBiz" class="bass-input" value="${Bass.escapeHtml(o.vendor_business_number || "")}"></label>
        <label class="r3-fld">거래처 과세유형
          <select id="oeVendorTax" class="bass-select">
            <option value="GENERAL" ${(o.vendor_tax_type || "GENERAL") === "GENERAL" ? "selected" : ""}>일반과세자</option>
            <option value="SIMPLIFIED" ${o.vendor_tax_type === "SIMPLIFIED" ? "selected" : ""}>간이과세자 (영수증 불공제)</option>
            <option value="EXEMPT" ${o.vendor_tax_type === "EXEMPT" ? "selected" : ""}>면세사업자·비사업자</option>
          </select>
        </label>
        <label class="r3-fld">증빙유형
          <select id="oeEvid" class="bass-select">
            <option value="TAX_INVOICE" ${(o.evidence_type || "TAX_INVOICE") === "TAX_INVOICE" ? "selected" : ""}>전자세금계산서</option>
            <option value="CARD" ${o.evidence_type === "CARD" ? "selected" : ""}>사업용 신용카드</option>
            <option value="CASH_RECEIPT" ${o.evidence_type === "CASH_RECEIPT" ? "selected" : ""}>지출증빙 현금영수증</option>
            <option value="SIMPLE_RECEIPT" ${o.evidence_type === "SIMPLE_RECEIPT" ? "selected" : ""}>간이영수증·계좌이체</option>
            <option value="EXEMPT" ${o.evidence_type === "EXEMPT" ? "selected" : ""}>면세·경조사 청첩장</option>
          </select>
        </label>
        <label class="r3-fld">사용구분
          <select id="oeScope" class="bass-select">
            <option value="COMMON" ${(o.usage_scope || "COMMON") === "COMMON" ? "selected" : ""}>건물 공통경비 (안분)</option>
            <option value="TAXABLE_ONLY" ${o.usage_scope === "TAXABLE_ONLY" ? "selected" : ""}>상가·과세호실 전용</option>
            <option value="EXEMPT_ONLY" ${o.usage_scope === "EXEMPT_ONLY" ? "selected" : ""}>주거·면세호실 전용</option>
          </select>
        </label>
        <label class="r3-fld">메모<input id="oeMemo" class="bass-input" value="${Bass.escapeHtml(o.memo || "")}"></label>
      </div>
      <div class="bass-row" style="gap:6px;margin-top:10px;">
        <button id="oeSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 저장</button>
        <button id="oeDel" class="bass-btn bass-btn--sm bass-btn--danger" type="button">🗑️ 삭제</button>
      </div>`);
    $m("oeSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/outlays/edit", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            outlay_id: o.id,
            category: $m("oeCat").value,
            title: $m("oeTitle").value,
            amount: Number($m("oeAmt").value || 0),
            vendor: $m("oeVendor").value,
            vendor_business_number: $m("oeVendorBiz").value,
            vendor_tax_type: $m("oeVendorTax").value,
            evidence_type: $m("oeEvid").value,
            usage_scope: $m("oeScope").value,
            expense_date: $m("oeDate").value,
            memo: $m("oeMemo").value,
          },
        });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await L.refresh();
      });
    });
    $m("oeDel").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        if (!window.confirm(`'${o.title}' (${fmtWon(o.amount)}) 지출을 지울까요?`)) return;
        const res = await Bass.api(APP_ID, "/outlays/delete", { method: "POST", body: { branch: S.currentBranch, outlay_id: o.id } });
        Bass.updateAppAlert("success", res.message);
        closeModal();
        await L.refresh();
      });
    });
  }

  Object.assign(L, {
    goTaxView, showFeeEvidenceModal, showMgmtFeeModal,
    renderTaxTab, renderBldgTaxTab, showInvoiceStatusModal, showOutlayModal,
  });
})();
