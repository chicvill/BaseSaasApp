/**
 * apps/rent3/frontend/js/tax_expenses.js — 세무·수익률 분석, 공용 운영비 실결산, RTMS 주택임대차 신고, 5월 종소세 전산 명세서 모듈
 */

let currentExpenseSummaryCache = null;
let currentExpenseListCache = [];
let expenseReceiptDataUrl = "";

// ─────────────────────────────────────────────────────────────
// 1. RTMS 주택임대차 계약 전자신고 & 확정일자 대장
// ─────────────────────────────────────────────────────────────
async function openAdminRtmsModal() {
  const branch = document.getElementById("branchSelect")?.value || "main";
  const modal = document.getElementById("adminRtmsModal");
  if (!modal) return;
  modal.style.display = "flex";

  try {
    const res = await fetch(`/api/rent3/admin/rtms/pending?branch=${branch}`);
    const data = await res.json();
    const reports = data.reports || [];

    const submittedCount = reports.filter(r => r.status === 'SUBMITTED').length;
    const pendingCount = reports.filter(r => r.status !== 'SUBMITTED').length;

    if (document.getElementById("adminRtmsTotalCount")) document.getElementById("adminRtmsTotalCount").innerText = `${reports.length || 0}건`;
    if (document.getElementById("adminRtmsSubmittedCount")) document.getElementById("adminRtmsSubmittedCount").innerText = `${submittedCount}건`;
    if (document.getElementById("adminRtmsPendingCount")) document.getElementById("adminRtmsPendingCount").innerText = `${pendingCount}건`;

    const tbody = document.getElementById("adminRtmsTbody");
    if (!tbody) return;

    if (reports.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:var(--text-muted);">현재 관리 지점의 주택임대차 신고 내역이 없습니다.</td></tr>`;
      return;
    }

    tbody.innerHTML = reports.map(r => {
      const isSubmitted = (r.status === 'SUBMITTED');
      const daysLeft = r.days_left_to_deadline !== undefined ? r.days_left_to_deadline : 30;
      return `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06);">
          <td style="padding:10px 8px; font-weight:700;">${r.room_id}호</td>
          <td style="padding:10px 8px;">${r.tenant_name} (${r.tenant_phone ? r.tenant_phone.slice(-4) : '****'})</td>
          <td style="padding:10px 8px;">${(r.deposit||0).toLocaleString()} / ${(r.monthly_rent||0).toLocaleString()}</td>
          <td style="padding:10px 8px; color:var(--text-muted); font-size:12px;">${r.contract_date || '-'}</td>
          <td style="padding:10px 8px;">
            <span class="badge ${isSubmitted ? 'badge-confirmed' : (daysLeft <= 7 ? 'badge-rejected' : 'badge-pending')}" style="font-size:11px;">
              ${isSubmitted ? '안심 준수' : `D-${daysLeft}일 남음`}
            </span>
          </td>
          <td style="padding:10px 8px;">
            ${isSubmitted ? `
              <div style="font-size:12px; font-weight:700; color:#10b981;">✅ 확정일자 부여완료</div>
              <div style="font-size:11px; color:#38bdf8; font-family:monospace;">${r.fixed_date_number || '-'}</div>
            ` : `
              <span style="font-size:12px; color:var(--warning); font-weight:600;">⏳ 전자신고 대기</span>
            `}
          </td>
          <td style="padding:10px 8px; text-align:right;">
            <div style="display:inline-flex; gap:6px;">
              ${!isSubmitted ? `
                <button class="btn btn-primary" style="padding:4px 10px; font-size:12px;" onclick="submitAdminRtmsOne('${r.room_id}', this)">
                  ⚡ 즉시 대리 신고
                </button>
              ` : `
                <button class="btn btn-secondary" style="padding:4px 10px; font-size:12px; background:rgba(56,189,248,0.15); color:var(--primary); border:1px solid rgba(56,189,248,0.3);" onclick="viewAdminRtmsReceipt('${r.room_id}')">
                  📄 필증 확인
                </button>
              `}
            </div>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    const tbody = document.getElementById("adminRtmsTbody");
    if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="color:var(--error); text-align:center; padding:20px;">신고 대장을 불러오지 못했습니다: ${err.message}</td></tr>`;
  }
}

function closeAdminRtmsModal() {
  const modal = document.getElementById("adminRtmsModal");
  if (modal) modal.style.display = "none";
}

async function submitAdminRtmsOne(roomId, btn) {
  const branch = document.getElementById("branchSelect")?.value || "main";
  if (typeof Bass !== 'undefined' && Bass.withLock) {
    await Bass.withLock(btn, async () => {
      const res = await fetch(`/api/rent3/rtms/${roomId}/submit?branch=${branch}`, { method: "POST" });
      const data = await res.json();
      if (data.success) {
        Bass.updateAppAlert(data.message, "success");
        openAdminRtmsModal();
      } else {
        Bass.updateAppAlert(data.detail || "신고 처리 실패", "error");
      }
    });
  }
}

function viewAdminRtmsReceipt(roomId) {
  window.open(`index.html?view=tenant&room=${roomId}`, '_blank');
}


// ─────────────────────────────────────────────────────────────
// 2. 세입자 연말정산 월세 세액공제 공식 납입 증명서
// ─────────────────────────────────────────────────────────────
async function openAdminRentCertModal() {
  const branch = document.getElementById("branchSelect")?.value || "main";
  const roomId = document.getElementById("certTargetRoomSelect")?.value || "201";

  try {
    const res = await fetch(`/api/rent3/certificate/rent-tax/${branch}/${roomId}?year=2026`);
    const data = await res.json();
    if (!data.success || !data.certificate) {
      alert("증명서를 불러오지 못했습니다.");
      return;
    }

    const c = data.certificate;
    if (document.getElementById("adminCertNoText")) document.getElementById("adminCertNoText").innerText = c.cert_no;
    if (document.getElementById("adminCertYearTitle")) document.getElementById("adminCertYearTitle").innerText = c.year;
    if (document.getElementById("adminCertTenantName")) document.getElementById("adminCertTenantName").innerText = c.tenant_name;
    if (document.getElementById("adminCertTenantSsn")) document.getElementById("adminCertTenantSsn").innerText = c.tenant_ssn_masked || "950101-1******";
    if (document.getElementById("adminCertAddress")) document.getElementById("adminCertAddress").innerText = c.address;
    if (document.getElementById("adminCertDeposit")) document.getElementById("adminCertDeposit").innerText = (c.deposit || 0).toLocaleString() + "원";
    if (document.getElementById("adminCertMonthlyRent")) document.getElementById("adminCertMonthlyRent").innerText = (c.monthly_rent || 0).toLocaleString() + "원";
    if (document.getElementById("adminCertPeriod")) document.getElementById("adminCertPeriod").innerText = c.lease_period;
    if (document.getElementById("adminCertLandlordName")) document.getElementById("adminCertLandlordName").innerText = c.landlord_name;
    if (document.getElementById("adminCertSealLandlordName")) document.getElementById("adminCertSealLandlordName").innerText = c.landlord_name;
    if (document.getElementById("adminCertLandlordRegNo")) document.getElementById("adminCertLandlordRegNo").innerText = c.landlord_reg_no;
    if (document.getElementById("adminCertIssuedAt")) document.getElementById("adminCertIssuedAt").innerText = c.issued_at;
    if (document.getElementById("adminCertTotalPaidAmount")) document.getElementById("adminCertTotalPaidAmount").innerText = (c.total_paid_amount || 0).toLocaleString() + "원";
    if (document.getElementById("adminCertEstimatedRefund")) document.getElementById("adminCertEstimatedRefund").innerText = (c.estimated_tax_refund || 0).toLocaleString() + "원";

    const tbody = document.getElementById("adminCertTableBody");
    if (tbody) {
      if (c.items && c.items.length > 0) {
        tbody.innerHTML = c.items.map(it => `
          <tr>
            <td style="border:1px solid #cbd5e1; padding:6px; text-align:center;">${it.month}회</td>
            <td style="border:1px solid #cbd5e1; padding:6px;">${it.period}</td>
            <td style="border:1px solid #cbd5e1; padding:6px; text-align:center;">${it.paid_date}</td>
            <td style="border:1px solid #cbd5e1; padding:6px; text-align:right; font-weight:700;">${(it.amount || 0).toLocaleString()}원</td>
            <td style="border:1px solid #cbd5e1; padding:6px; text-align:center; font-size:11px; color:#475569;">${it.payment_method}</td>
            <td style="border:1px solid #cbd5e1; padding:6px; text-align:center; font-family:monospace; font-size:11px; color:#0284c7; font-weight:700;">${it.cash_receipt_no}</td>
          </tr>
        `).join("");
      } else {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:16px;">납부 내역이 없습니다.</td></tr>`;
      }
    }

    const modal = document.getElementById("adminRentCertModal");
    if (modal) modal.style.display = "flex";
  } catch (err) {
    alert("증명서 조회 오류: " + err.message);
  }
}

function closeAdminRentCertModal() {
  const modal = document.getElementById("adminRentCertModal");
  if (modal) modal.style.display = "none";
}

function printAdminRentCert() {
  window.print();
}

async function sendCertToTenantChat(btn) {
  const branch = document.getElementById("branchSelect")?.value || "main";
  const roomId = document.getElementById("certTargetRoomSelect")?.value || "201";

  if (typeof Bass !== 'undefined' && Bass.withLock) {
    await Bass.withLock(btn, async () => {
      const res = await fetch(`/api/rent3/certificate/send-chat/${branch}/${roomId}?year=2026`, {
        method: "POST"
      });
      const data = await res.json();
      if (data.success) {
        Bass.updateAppAlert(`📑 [${roomId}호] 연말정산 월세 납입 증명서 발급 안내가 1:1 대화방에 성공적으로 전송되었습니다!`, "success");
      } else {
        Bass.updateAppAlert(data.detail || "전송에 실패했습니다.", "error");
      }
    });
  }
}


// ─────────────────────────────────────────────────────────────
// 3. 지점별 통합 세무·수익률 분석 (ROI / Cap Rate)
// ─────────────────────────────────────────────────────────────
async function loadTaxRoiDashboard() {
  const mode = document.getElementById("taxModeSelect")?.value || "SEPARATE_14";

  try {
    const res = await fetch(`/api/rent3/analytics/tax-roi?tax_mode=${mode}`);
    const data = await res.json();
    const p = data.portfolio;

    // 1. 포트폴리오 4대 핵심 지표 갱신
    if (document.getElementById("roiAvgOcc")) {
      document.getElementById("roiAvgOcc").innerText = `${p.portfolio_occupancy_rate || 0}%`;
      document.getElementById("roiOccRooms").innerText = `${p.portfolio_occupied_rooms || 0} / ${p.portfolio_total_rooms || 64}실`;
      document.getElementById("roiGrossRev").innerText = `${((p.portfolio_gross_revenue || 0) / 10000).toLocaleString()} 만원`;
      document.getElementById("roiTotalDep").innerText = `${((p.portfolio_total_deposit || 0) / 10000).toLocaleString()} 만원`;
      document.getElementById("roiVacLoss").innerText = `-${((p.portfolio_vacancy_loss || 0) / 10000).toLocaleString()} 만원`;
      document.getElementById("roiVacRooms").innerText = `공실 ${p.portfolio_vacant_rooms || 0}실 발생 손실`;
      document.getElementById("roiPortfolioNoi").innerText = `${((p.portfolio_net_operating_income || 0) / 10000).toLocaleString()} 만원`;
      document.getElementById("roiTotalExp").innerText = `운영비+보수 -${((p.portfolio_total_operating_cost || 0) / 10000).toLocaleString()}만원`;
    }

    // 2. 3대 지점별 테이블 렌더링
    const tbody = document.getElementById("taxRoiBranchTbody");
    if (tbody && data.branches) {
      tbody.innerHTML = data.branches.map(b => {
        return `
          <tr>
            <td>
              <strong>${b.branch_name}</strong><br>
              <span style="font-size:11px; color:var(--text-muted);">${b.address || ''}</span>
            </td>
            <td><strong>${b.occupied_rooms} / ${b.total_rooms}실</strong> (${b.occupancy_rate}%)</td>
            <td>${(b.total_deposit / 10000).toLocaleString()}만원</td>
            <td><strong>${(b.annual_revenue / 10000).toLocaleString()}만원</strong></td>
            <td style="color:#f87171;">-${(b.vacancy_loss / 10000).toLocaleString()}만원</td>
            <td style="color:#38bdf8; font-weight:700;">${(b.net_operating_income / 10000).toLocaleString()}만원</td>
            <td><strong style="color:#10b981;">${b.net_cap_rate}%</strong></td>
            <td style="font-size:12px; color:#cbd5e1;">${b.estimated_holding_tax ? (b.estimated_holding_tax/10000).toFixed(0)+'만원' : '자동산출'}</td>
            <td>
              <button class="btn btn-secondary" style="padding:4px 8px; font-size:11px;" onclick="openAnnualTaxStatementModal('${b.branch}')">📑 연간 명세서</button>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    console.error("loadTaxRoiDashboard error:", err);
  }
}


// ─────────────────────────────────────────────────────────────
// 4. 건물 공용 운영비 실지출 관리 & 결산
// ─────────────────────────────────────────────────────────────
async function loadOperatingExpenses() {
  const branch = document.getElementById("branchSelect")?.value || "main";
  const year = document.getElementById("expenseYearSelect")?.value || "2026";
  const month = document.getElementById("expenseMonthSelect")?.value || "10";

  try {
    const summaryRes = await fetch(`/api/rent3/expenses/monthly-summary?branch=${branch}&year=${year}&month=${month}`);
    const summaryData = await summaryRes.json();
    const s = summaryData.summary;
    currentExpenseSummaryCache = s;

    if (s && document.getElementById("kpiExpTotal")) {
      document.getElementById("kpiExpTotal").innerText = `${(s.total_expense || s.total_operating_expense || 0).toLocaleString()} 원`;
      document.getElementById("kpiExpCount").innerText = s.total_receipt_count || 0;
      document.getElementById("kpiExpIncome").innerText = `${(s.total_revenue || s.total_gross_income || 0).toLocaleString()} 원`;
      document.getElementById("kpiExpNoi").innerText = `${(s.noi || s.net_operating_income || 0).toLocaleString()} 원`;
      document.getElementById("kpiExpMargin").innerText = s.net_margin_rate || s.noi_margin || '0.0';
      document.getElementById("kpiExpDeductible").innerText = `${(s.tax_deductible_expense || 0).toLocaleString()} 원`;
      if (document.getElementById("kpiExpRatioText")) {
        document.getElementById("kpiExpRatioText").innerText = `경비율 ${s.expense_ratio || 0}%`;
      }
    }

    const listRes = await fetch(`/api/rent3/expenses/list?branch=${branch}&year=${year}&month=${month}`);
    const listData = await listRes.json();
    const items = listData.items || [];
    currentExpenseListCache = items;

    const tbody = document.getElementById("expenseTbody");
    if (tbody) {
      if (items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:var(--text-muted);">등록된 운영비 지출 내역이 없습니다.</td></tr>`;
      } else {
        tbody.innerHTML = items.map(item => `
          <tr>
            <td>${item.payment_date || ''}</td>
            <td><span class="badge badge-info">${item.category_label || item.category}</span></td>
            <td><strong>${item.title || ''}</strong></td>
            <td>${item.vendor_name || '-'} <span style="font-size:11px; color:var(--text-muted);">(${item.vendor_biz_no || ''})</span></td>
            <td style="color:#f87171; font-weight:700;">-${(item.amount || 0).toLocaleString()}원</td>
            <td><span class="badge badge-success">${item.receipt_type_label || item.receipt_type}</span></td>
            <td>
              <div style="display:inline-flex; gap:4px;">
                <button class="btn btn-secondary" style="padding:2px 8px; font-size:11px;" onclick="alert('영수증 번호: ${item.receipt_no}\\n적요: ${item.title}\\n공급자: ${item.vendor_name || '-'}')">증빙</button>
                <button class="btn btn-secondary" style="padding:2px 6px; font-size:11px; color:var(--error);" onclick="deleteExpenseItem('${item.id}')">삭제</button>
              </div>
            </td>
          </tr>
        `).join("");
      }
    }
  } catch (err) {
    console.error("loadOperatingExpenses error:", err);
  }
}

function openNewExpenseModal() {
  const modal = document.getElementById("newExpenseModal");
  if (modal) {
    const branchSel = document.getElementById("branchSelect");
    if (branchSel && document.getElementById("expModalBranch")) {
      document.getElementById("expModalBranch").value = branchSel.value;
    }
    expenseReceiptDataUrl = "";
    if (document.getElementById("expModalFileStatus")) document.getElementById("expModalFileStatus").innerText = "선택된 파일 없음";
    if (document.getElementById("expModalPreviewArea")) document.getElementById("expModalPreviewArea").style.display = "none";
    modal.style.display = "flex";
  }
}

function closeNewExpenseModal() {
  const modal = document.getElementById("newExpenseModal");
  if (modal) modal.style.display = "none";
}

function handleExpenseReceiptSelect(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    expenseReceiptDataUrl = e.target.result;
    if (document.getElementById("expModalFileStatus")) document.getElementById("expModalFileStatus").innerText = `첨부됨 (${(file.size/1024).toFixed(1)} KB)`;
    if (document.getElementById("expModalPreviewImg")) document.getElementById("expModalPreviewImg").src = expenseReceiptDataUrl;
    if (document.getElementById("expModalPreviewArea")) document.getElementById("expModalPreviewArea").style.display = "block";
  };
  reader.readAsDataURL(file);
}

async function submitNewExpense(btn) {
  const branch = document.getElementById("expModalBranch").value;
  const year = parseInt(document.getElementById("expModalYear").value);
  const month = parseInt(document.getElementById("expModalMonth").value);
  const category = document.getElementById("expModalCategory").value;
  const payment_date = document.getElementById("expModalDate").value;
  const title = document.getElementById("expModalTitle").value.trim();
  const amount = parseInt(document.getElementById("expModalAmount").value);
  const vendor_name = document.getElementById("expModalVendorName").value.trim();
  const vendor_biz_no = document.getElementById("expModalVendorBiz").value.trim();
  const payment_method = document.getElementById("expModalPaymentMethod").value;
  const receipt_type = document.getElementById("expModalReceiptType").value;
  const notes = document.getElementById("expModalNotes").value.trim();

  if (!title || !amount || isNaN(amount) || amount <= 0) {
    if (typeof Bass !== 'undefined' && Bass.updateAppAlert) Bass.updateAppAlert("적요와 올바른 지출 금액을 입력해주세요.", "warning");
    return;
  }

  if (typeof Bass !== 'undefined' && Bass.withLock) {
    await Bass.withLock(btn, async () => {
      const payload = {
        branch,
        year,
        month,
        category,
        title,
        amount,
        vendor_name,
        vendor_biz_no,
        payment_date,
        payment_method,
        receipt_type,
        receipt_photo_url: expenseReceiptDataUrl,
        notes,
        is_tax_deductible: true
      };

      const res = await fetch("/api/rent3/expenses/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.success) {
        Bass.updateAppAlert(data.message, "success");
        closeNewExpenseModal();
        const yearSel = document.getElementById("expenseYearSelect");
        const monthSel = document.getElementById("expenseMonthSelect");
        if (yearSel) yearSel.value = String(year);
        if (monthSel) monthSel.value = String(month);
        loadOperatingExpenses();
        loadTaxRoiDashboard();
      } else {
        Bass.updateAppAlert(data.detail || "지출 등록 실패", "error");
      }
    });
  }
}

async function deleteExpenseItem(expenseId) {
  if (!confirm("해당 지출 내역을 삭제하시겠습니까? 지출 결산과 순운영이익(NOI)에서 제외됩니다.")) return;
  try {
    const res = await fetch(`/api/rent3/expenses/${expenseId}`, { method: "DELETE" });
    const data = await res.json();
    if (data.success) {
      if (typeof Bass !== 'undefined' && Bass.updateAppAlert) Bass.updateAppAlert(data.message, "success");
      loadOperatingExpenses();
      loadTaxRoiDashboard();
    } else {
      if (typeof Bass !== 'undefined' && Bass.updateAppAlert) Bass.updateAppAlert(data.detail || "삭제 실패", "error");
    }
  } catch (err) {
    if (typeof Bass !== 'undefined' && Bass.updateAppAlert) Bass.updateAppAlert(`삭제 오류: ${err.message}`, "error");
  }
}

function openMonthlyExpenseReportModal() {
  const modal = document.getElementById("monthlyExpenseReportModal");
  if (modal) modal.style.display = "flex";
}

function closeMonthlyExpenseReportModal() {
  const modal = document.getElementById("monthlyExpenseReportModal");
  if (modal) modal.style.display = "none";
}

function printMonthlyExpenseReport() {
  window.print();
}


// ─────────────────────────────────────────────────────────────
// 5. 5월 종합소득세 홈택스 전산 명세서 (연간 결산)
// ─────────────────────────────────────────────────────────────
async function openAnnualTaxStatementModal(targetBranch) {
  const branchSel = document.getElementById("taxStmtBranchSelect");
  if (branchSel && targetBranch) {
    branchSel.value = targetBranch;
  }
  const modal = document.getElementById("annualTaxStatementModal");
  if (modal) {
    modal.style.display = "flex";
    await loadAnnualTaxStatementData();
  }
}

function closeAnnualTaxStatementModal() {
  const modal = document.getElementById("annualTaxStatementModal");
  if (modal) modal.style.display = "none";
}

async function loadAnnualTaxStatementData() {
  const branch = document.getElementById("taxStmtBranchSelect")?.value || "all";
  const year = parseInt(document.getElementById("taxStmtYearSelect")?.value || "2026");

  try {
    const res = await fetch(`/api/rent3/tax/annual-statement?branch=${branch}&year=${year}`);
    const data = await res.json();
    const s = data.statement;

    if (!s) return;
    if (document.getElementById("stmtTaxYear")) document.getElementById("stmtTaxYear").innerText = s.tax_year;
    if (document.getElementById("stmtDocNo")) document.getElementById("stmtDocNo").innerText = s.document_no;
    if (document.getElementById("stmtOwnerName")) document.getElementById("stmtOwnerName").innerText = s.business_info?.lessor_name || "김임대";
    if (document.getElementById("stmtBizNo")) document.getElementById("stmtBizNo").innerText = s.business_info?.business_registration_no || "214-85-12345";
    if (document.getElementById("stmtBuildingAddress")) document.getElementById("stmtBuildingAddress").innerText = s.business_info?.building_address || "서울특별시 관악구 봉천동 888-12";
    if (document.getElementById("stmtTotalRoomsCount")) document.getElementById("stmtTotalRoomsCount").innerText = `${s.business_info?.total_rooms || 29}실 (3대 지점 통합 64실)`;

    const rev = s.revenue_summary;
    if (rev && document.getElementById("stmtGrossRev")) {
      document.getElementById("stmtGrossRev").innerText = `${(rev.annual_gross_revenue || 0).toLocaleString()}원`;
      document.getElementById("stmtTotalRentIncome").innerText = `${(rev.total_collected_rent || 0).toLocaleString()}원`;
      document.getElementById("stmtTotalMaintIncome").innerText = `${(rev.total_collected_maintenance || 0).toLocaleString()}원`;
    }

    const exp = s.expense_summary;
    if (exp && document.getElementById("stmtTotalExpDeductible")) {
      document.getElementById("stmtTotalExpDeductible").innerText = `${(exp.grand_deductible_expense || 0).toLocaleString()}원`;
      document.getElementById("stmtReceiptCount").innerText = `${exp.total_receipt_count || 0}건`;
      document.getElementById("stmtExpenseRatioText").innerText = `${exp.expense_ratio || 0}%`;
    }

    const resResult = s.taxable_result;
    if (resResult && document.getElementById("stmtResGrossRev")) {
      document.getElementById("stmtResGrossRev").innerText = `${(rev.annual_gross_revenue || 0).toLocaleString()} 원`;
      document.getElementById("stmtResTotalExp").innerText = `${(exp.grand_deductible_expense || 0).toLocaleString()} 원`;
      document.getElementById("stmtResTaxableIncome").innerText = `${(resResult.taxable_rental_income || 0).toLocaleString()} 원`;
      document.getElementById("stmtResSeparateTax").innerText = `${(resResult.separate_tax_estimate_14 || 0).toLocaleString()} 원`;
      if (document.getElementById("stmtIssuedDate")) document.getElementById("stmtIssuedDate").innerText = s.issued_date;
      if (document.getElementById("stmtSealCenterName")) document.getElementById("stmtSealCenterName").innerText = `${s.branch_title} 임대관리센터`;
    }
  } catch (err) {
    alert("명세서 조회 오류: " + err.message);
  }
}

function downloadAnnualTaxStatementCsv() {
  const branch = document.getElementById("taxStmtBranchSelect")?.value || "all";
  const year = parseInt(document.getElementById("taxStmtYearSelect")?.value || "2026");
  window.location.href = `/api/rent3/tax/annual-statement/export-csv?branch=${branch}&year=${year}`;
}

function printAnnualTaxStatement() {
  window.print();
}
