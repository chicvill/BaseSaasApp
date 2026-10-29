/* apps/rent3_tenant/frontend/app.js — 임차인 매물 탐색 & 세입자 마이홈 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_tenant";
  const params = new URLSearchParams(window.location.search);

  let mode = params.get("view") === "listings" ? "listings" : "myhome";
  let selectedTenantId = params.get("tenant") || "";
  let tenantList = [];
  let showDoorlock = false;

  const elMyhome = document.getElementById("viewMyhome");
  const elListings = document.getElementById("viewListings");
  const btnMyhome = document.getElementById("btnModeMyhome");
  const btnListings = document.getElementById("btnModeListings");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  function setMode(m) {
    mode = m;
    btnMyhome.classList.toggle("is-active", m === "myhome");
    btnListings.classList.toggle("is-active", m === "listings");
    elMyhome.style.display = m === "myhome" ? "block" : "none";
    elListings.style.display = m === "listings" ? "block" : "none";
    if (m === "myhome") loadMyhome().catch(Bass.showError);
    else loadListings().catch(Bass.showError);
  }

  btnMyhome.addEventListener("click", () => setMode("myhome"));
  btnListings.addEventListener("click", () => setMode("listings"));

  async function loadTenants() {
    const res = await Bass.api(APP_ID, "/tenants");
    tenantList = res.tenants || [];
    if (!selectedTenantId && tenantList.length) {
      selectedTenantId = tenantList[0].id;
    }
  }

  async function loadMyhome() {
    if (!tenantList.length) await loadTenants();
    if (!selectedTenantId) {
      elMyhome.innerHTML = `<div class="bass-card">현재 활성 입주 세입자가 없습니다. 공실 탐색 탭에서 계약을 진행해 보세요.</div>`;
      return;
    }
    const data = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(selectedTenantId)}`);
    document.getElementById("poolBadge").textContent = `JSON Pool v${data.pool_revision}`;

    const t = data.tenant;
    const b = data.building || {};
    const r = data.room || {};
    const ct = data.contract || {};
    const lg = data.ledger || { months: [], total_unpaid: 0 };
    const cert = data.certificate;
    const co = data.pending_checkout;
    const ew = data.expiry_window;
    const ec = data.expiry_check;
    const showExpiryCard = Boolean((ew && ew.in_6_to_2_months_window) || ec);

    elMyhome.innerHTML = `
      <div class="bass-card no-print" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;">
          <div>
            <label class="bass-label">현재 조회 중인 입주 세입자 선택</label>
            <select id="tenantSel" class="bass-select">
              ${tenantList.map((item) => `<option value="${item.id}" ${item.id === t.id ? "selected" : ""}>[${Bass.escapeHtml(item.building_name)} ${Bass.escapeHtml(item.room_no)}호] ${Bass.escapeHtml(item.name)} (${Bass.escapeHtml(item.phone)})</option>`).join("")}
            </select>
          </div>
          <div class="bass-row">
            <span class="bass-badge ${lg.total_unpaid > 0 ? "bass-badge--err" : "bass-badge--ok"}">${lg.total_unpaid > 0 ? `미납 ${fmtWon(lg.total_unpaid)}` : "미납 0원 (완납)"}</span>
            <span class="bass-badge ${ew && ew.in_6_to_2_months_window ? "bass-badge--warn" : "bass-badge--info"}">만기 D-${lg.days_to_expiry ?? "-"}${ew && ew.in_6_to_2_months_window ? " (6~2개월 통지구간)" : ""}</span>
          </div>
        </div>

        <div class="field-grid" style="margin-top:14px;">
          <div class="listing-card" style="margin:0;">
            <div class="bass-muted" style="font-size:12px;">🔑 출입 · 와이파이 (계약 체결자 전용)</div>
            <div style="margin-top:6px;">세대 도어락: <strong>${showDoorlock ? Bass.escapeHtml(r.doorlock_pwd || t.doorlock_pwd) : "••••*"}</strong>
              <button id="btnToggleDl" class="bass-btn bass-btn--sm" style="margin-left:6px;" type="button">${showDoorlock ? "숨기기" : "보기"}</button>
            </div>
            <div style="font-size:12.5px;margin-top:4px;">공동현관: <code>${Bass.escapeHtml(b.common_entrance_pwd || "#1004*")}</code> · Wi-Fi: <code>${Bass.escapeHtml(b.wifi_info || "-")}</code></div>
          </div>

          <div class="listing-card" style="margin:0;">
            <div class="bass-muted" style="font-size:12px;">🚗 내 차량 건물 주차 등록</div>
            <div class="bass-row" style="margin-top:6px;">
              <input id="carNoInput" class="bass-input" style="flex:1;" value="${Bass.escapeHtml(t.car_number || "")}" placeholder="예: 12가 3456">
              <button id="btnSaveCar" class="bass-btn bass-btn--sm bass-btn--primary" type="button">등록</button>
            </div>
          </div>
        </div>
      </div>

      ${showExpiryCard ? `
        <div class="bass-card no-print" style="margin-bottom:14px;border-left:4px solid var(--warn);">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
            <div>
              <h3 style="margin:0;">📅 만기 6~2개월 전 전출계획 확인 & 연장 재계약 예고</h3>
              <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                계약 만기일: <strong>${Bass.escapeHtml(ct.expiry_date || "-")}</strong> (D-${ew ? ew.days_to_expiry : "-"})
                ${ew ? ` · 법정 통지마감(만기 2개월 전): <strong>${Bass.escapeHtml(ew.notice_deadline_date)}</strong>` : ""}
              </div>
            </div>
            <span class="bass-badge ${ec && (ec.status === "RENEWAL_ACKNOWLEDGED" || ec.status === "STAY_CONFIRMED") ? "bass-badge--ok" : "bass-badge--warn"}">
              ${ec ? (
                ec.status === "AWAITING_TENANT" ? "📨 임대인 전출계획 확인 요청 도착" :
                ec.status === "MOVEOUT_PLANNED" ? `📦 만기전출 예정 회신됨 (${Bass.escapeHtml(ec.planned_move_out_date)})` :
                ec.status === "STAY_CONFIRMED" ? "🏠 전출의사 없음 회신 완료" :
                ec.status === "RENEWAL_PREVIEWED" ? "📝 임대인 연장 재계약 예고 도착" :
                ec.status === "RENEWAL_ACKNOWLEDGED" ? "✅ 연장 재계약 예고 조건 동의 완료" : Bass.escapeHtml(ec.status)
              ) : "⚖️ 만기 6~2개월 전 사전확인 구간"}
            </span>
          </div>

          <div class="field-grid" style="margin-top:10px;">
            <div>
              <label class="bass-label">전출(퇴실) 희망 시 예정일 (거주 연장 시 자동 무시)</label>
              <input id="expMoveOutDate" type="date" class="bass-input" value="${Bass.escapeHtml((ec && ec.planned_move_out_date) || ct.expiry_date || "")}">
            </div>
            <div>
              <label class="bass-label">임대인 전달 메모 (선택)</label>
              <input id="expTenantMemo" class="bass-input" value="${Bass.escapeHtml((ec && ec.tenant_memo) || "")}" placeholder="예: 전출 의사 없으며 동일 조건으로 1년 더 거주 희망합니다.">
            </div>
          </div>
          <div class="bass-row" style="margin-top:8px;flex-wrap:wrap;gap:8px;">
            <button id="btnPlanStay" class="bass-btn bass-btn--primary" type="button">🏠 전출의사 없음 (거주 연장 희망 회신)</button>
            <button id="btnPlanMoveOut" class="bass-btn" type="button">📦 만기 전출(퇴실) 예정 회신</button>
          </div>

          ${ec && (ec.status === "RENEWAL_PREVIEWED" || ec.status === "RENEWAL_ACKNOWLEDGED") ? `
            <div class="listing-card" style="margin:12px 0 0;border-color:var(--primary-2);">
              <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
                <strong style="font-size:15px;">📝 임대인 연장 재계약 예고장 (${ec.renewal_type === "SAME_TERMS" ? "종전 동일조건 연장" : "법정 5% 상한 이내 조정"})</strong>
                <span class="bass-badge ${ec.status === "RENEWAL_ACKNOWLEDGED" ? "bass-badge--ok" : "bass-badge--info"}">${ec.status === "RENEWAL_ACKNOWLEDGED" ? "확인·동의 완료" : "임차인 확인 대기"}</span>
              </div>
              <div style="font-size:13.5px;margin-top:8px;line-height:1.6;">
                • 보증금: ${fmtWon(ec.current_deposit)} ➔ <strong>${fmtWon(ec.proposed_deposit)}</strong> (${ec.deposit_increase_pct >= 0 ? "+" : ""}${ec.deposit_increase_pct}%)<br>
                • 월세 / 관리비: ${fmtWon(ec.current_monthly_rent)} ➔ <strong>${fmtWon(ec.proposed_monthly_rent)}</strong> (${ec.rent_increase_pct >= 0 ? "+" : ""}${ec.rent_increase_pct}%) / 관리비 <strong>${fmtWon(ec.proposed_maintenance_fee)}</strong><br>
                • 예정 연장기간: <strong>${ec.proposed_term_months}개월</strong>
                ${ec.renewal_note ? `<br>• 임대인 안내: <span class="bass-muted">${Bass.escapeHtml(ec.renewal_note)}</span>` : ""}
              </div>
              <div class="bass-row" style="margin-top:10px;flex-wrap:wrap;gap:8px;">
                ${ec.status !== "RENEWAL_ACKNOWLEDGED" ? `
                  <button id="btnAckRenewal" class="bass-btn bass-btn--primary" type="button">✅ 연장 재계약 예고 조건 확인·동의</button>
                ` : ""}
                ${ec.cosign_session_id ? `
                  <a class="bass-btn bass-btn--sm ${ec.status === "RENEWAL_ACKNOWLEDGED" ? "bass-btn--primary" : ""}" href="/rent3_contract/?session=${encodeURIComponent(ec.cosign_session_id)}&role=tenant">🤝 공동 재계약방 즉시 입장 (참여코드 ${Bass.escapeHtml(ec.cosign_session_code)})</a>
                ` : ""}
              </div>
            </div>
          ` : ""}
        </div>
      ` : ""}

      ${co ? `
        <div class="bass-card no-print" style="margin-bottom:14px;border-color:var(--warn);">
          <h3>🚪 퇴실 보증금 정산 명세서 도착 (실반환 예정액: ${fmtWon(co.refund_amount)})</h3>
          <p class="bass-muted" style="font-size:13px;">원보증금 ${fmtWon(co.deposit_original)} − 미납월세 ${fmtWon(co.unpaid_rent_deduction)} − 공과금 ${fmtWon(co.utility_settlement)} − 청소비 ${fmtWon(co.cleaning_fee)}</p>
          ${co.status === "CALCULATED" ? `
            <div class="field-grid" style="margin-top:8px;">
              <input id="coBank" class="bass-input" value="${Bass.escapeHtml(t.account_number || "")}" placeholder="반환받을 은행 계좌번호">
              <input id="coSig" class="bass-input" value="${Bass.escapeHtml(t.name)} (동의서명)" placeholder="동의 전자서명">
              <button id="btnAgreeCo" data-id="${co.id}" class="bass-btn bass-btn--primary" type="button">✍️ 정산 내역 확인 및 동의 서명</button>
            </div>
          ` : `<span class="bass-badge bass-badge--ok">세입자 동의 완료 — 임대인 송금 대기 중</span>`}
        </div>
      ` : ""}

      ${ct && ct.contract_photo_file ? `
        <div class="bass-card no-print" style="margin-bottom:14px;">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <h3 style="margin:0;">📸 공인중개사 입회 지면계약서 원본 사본</h3>
              <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">
                중개업소: <strong>${Bass.escapeHtml((ct.broker_info && ct.broker_info.agency_name) || "공인중개사사무소")}</strong>
                ${ct.broker_info && ct.broker_info.agent_name ? ` (대표 ${Bass.escapeHtml(ct.broker_info.agent_name)})` : ""}
                · 특약: ${Bass.escapeHtml(ct.special_terms || "-")}
              </div>
            </div>
            <a class="bass-btn bass-btn--sm bass-btn--primary" href="${Bass.apiBase(APP_ID)}/contracts/${encodeURIComponent(ct.id)}/photo" target="_blank" rel="noopener">🔍 계약서 원본 열기/다운로드</a>
          </div>
          ${String(ct.contract_photo_file).toLowerCase().endsWith(".pdf")
            ? `<div class="bass-muted" style="margin-top:8px;font-size:13px;">PDF 스캔본 계약서가 보관되어 있습니다. 우측 상단 버튼으로 확인하세요.</div>`
            : `<img src="${Bass.apiBase(APP_ID)}/contracts/${encodeURIComponent(ct.id)}/photo" alt="내 호실 임대차계약서 사본" style="max-width:100%;max-height:320px;border-radius:12px;border:1px solid var(--border);margin-top:10px;background:#fff;display:block;">`
          }
        </div>
      ` : ""}

      <div class="bass-card no-print" style="margin-bottom:14px;">
        <h3>💳 월별 납부 현황 & 온라인 간편 납부 (임대인 FIFO 장부 실시간 동기화)</h3>
        <div style="margin:10px 0;">
          ${(lg.months || []).map((m) => `
            <div style="display:flex;justify-content:space-between;padding:7px 10px;border-bottom:1px solid var(--border);font-size:13px;">
              <span><strong>${m.ym}</strong> · 청구 ${fmtWon(m.due)}</span>
              <span style="color:${m.unpaid > 0 ? "var(--err)" : "var(--ok)"};font-weight:700;">${Bass.escapeHtml(m.note)}</span>
            </div>
          `).join("")}
        </div>
        <div class="bass-row">
          <input id="payAmtInput" type="number" class="bass-input" style="width:170px;" value="${lg.total_unpaid > 0 ? lg.total_unpaid : (Number(ct.monthly_rent || 0) + Number(ct.maintenance_fee || 0))}">
          <select id="payMethodSel" class="bass-select" style="width:160px;">
            <option value="KAKAOPAY">카카오페이</option>
            <option value="CARD">신용/체크카드</option>
            <option value="TRANSFER">계좌이체</option>
          </select>
          <button id="btnPayNow" class="bass-btn bass-btn--primary" type="button">⚡ 월세 즉시 납부 (payments.json 반영)</button>
        </div>
      </div>

      <div class="bass-card no-print" style="margin-bottom:14px;">
        <h3>🔧 1:1 시설 수리(A/S) 요청 접수</h3>
        <div class="field-grid" style="margin:10px 0;">
          <select id="asCat" class="bass-select">
            <option value="설비/배관">설비/배관</option>
            <option value="에어컨/냉난방">에어컨/냉난방</option>
            <option value="전기/조명">전기/조명</option>
            <option value="도어락">디지털 도어락</option>
          </select>
          <input id="asTitle" class="bass-input" placeholder="수리 요청 제목 (예: 욕실 샤워기 호스 누수)">
        </div>
        <button id="btnSubmitAs" class="bass-btn" type="button">📩 임대인에게 수리 요청 보내기</button>
        <div style="margin-top:10px;">
          ${(data.tickets || []).map((tk) => `<div style="font-size:12.5px;padding:6px 0;border-bottom:1px solid var(--border);">[${tk.status}] ${Bass.escapeHtml(tk.title)} ${tk.vendor_name ? `— 배정: ${Bass.escapeHtml(tk.vendor_name)} (${Bass.escapeHtml(tk.visit_date)})` : ""}</div>`).join("")}
        </div>
      </div>

      ${cert ? `
        <div class="bass-card no-print" style="margin-bottom:8px;">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <h3 style="margin:0;">🧾 국세청 연말정산 17% 월세 세액공제 납입증명서</h3>
              <div class="bass-muted" style="font-size:12.5px;">예상 환급 세액공제액: <strong>${fmtWon(cert.estimated_tax_credit_17pct)}</strong> (확정일자 ${Bass.escapeHtml(cert.contract.fixed_date_no || "-")})</div>
            </div>
            <button onclick="window.print()" class="bass-btn bass-btn--primary" type="button">🖨️ 납입증명서 A4 인쇄 / PDF</button>
          </div>
        </div>
        <article class="cert-paper">
          <div style="display:flex;justify-content:space-between;border-bottom:2px solid #000;padding-bottom:8px;margin-bottom:12px;">
            <div>
              <h2 style="margin:0;color:#000;">월세액 세액공제 공식 납입증명서 (${cert.tax_year}년 귀속)</h2>
              <div style="font-size:12px;">문서번호: ${Bass.escapeHtml(cert.certificate_no)} · 근거: ${Bass.escapeHtml(cert.statute)}</div>
            </div>
            <div style="border:2px solid #b91c1c;color:#b91c1c;padding:6px 10px;font-weight:800;font-size:12px;">영수필 직인<br>${Bass.escapeHtml(cert.lessor.building_name)}</div>
          </div>
          <table>
            <tr><th>임대인(공급자)</th><td>${Bass.escapeHtml(cert.lessor.name)} (사업자 ${Bass.escapeHtml(cert.lessor.business_number)})</td><th>임차인</th><td>${Bass.escapeHtml(cert.tenant.name)} (${Bass.escapeHtml(cert.tenant.room_no)}호)</td></tr>
            <tr><th>임대 목적물 주소</th><td colspan="3">${Bass.escapeHtml(cert.lessor.address)}</td></tr>
            <tr><th>계약기간 / 확정일자</th><td>${Bass.escapeHtml(cert.contract.move_in_date)} ~ ${Bass.escapeHtml(cert.contract.expiry_date)}</td><th>확정일자 번호</th><td>${Bass.escapeHtml(cert.contract.fixed_date_no)}</td></tr>
            <tr><th>연간 총 납입액</th><td><strong>${fmtWon(cert.total_paid)}</strong></td><th>17% 예상 세액공제</th><td><strong>${fmtWon(cert.estimated_tax_credit_17pct)}</strong></td></tr>
          </table>
          <table>
            <thead><tr><th>귀속년월</th><th>납부일자</th><th>납부수단</th><th>국세청 현금영수증 승인번호</th><th>납부금액</th></tr></thead>
            <tbody>
              ${(cert.payments || []).map((p) => `<tr><td>${Bass.escapeHtml(p.billing_month)}</td><td>${Bass.escapeHtml(p.paid_date)}</td><td>${Bass.escapeHtml(p.method)}</td><td>${Bass.escapeHtml(p.cash_receipt_no)}</td><td>${fmtWon(p.amount)}</td></tr>`).join("")}
            </tbody>
          </table>
        </article>
      ` : ""}
    `;

    document.getElementById("tenantSel").addEventListener("change", (ev) => {
      selectedTenantId = ev.target.value;
      loadMyhome().catch(Bass.showError);
    });

    document.getElementById("btnToggleDl").addEventListener("click", () => {
      showDoorlock = !showDoorlock;
      loadMyhome().catch(Bass.showError);
    });

    document.getElementById("btnSaveCar").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/vehicle`, {
          method: "POST",
          body: { car_number: document.getElementById("carNoInput").value },
        });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });

    document.getElementById("btnPayNow").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/pay`, {
          method: "POST",
          body: {
            amount: Number(document.getElementById("payAmtInput").value || 0),
            method: document.getElementById("payMethodSel").value,
          },
        });
        Bass.updateAppAlert(res.level || "success", res.message);
        await loadMyhome();
      });
    });

    document.getElementById("btnSubmitAs").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const title = document.getElementById("asTitle").value.trim();
        if (!title) {
          Bass.updateAppAlert("warning", "수리 요청 제목을 입력해 주세요.");
          return;
        }
        const res = await Bass.api(APP_ID, `/myhome/${t.id}/tickets`, {
          method: "POST",
          body: { category: document.getElementById("asCat").value, title, description: title },
        });
        Bass.updateAppAlert("success", res.message);
        await loadMyhome();
      });
    });

    const btnAgreeCo = document.getElementById("btnAgreeCo");
    if (btnAgreeCo) {
      btnAgreeCo.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/checkout/${btnAgreeCo.dataset.id}/agree`, {
            method: "POST",
            body: {
              bank_account: document.getElementById("coBank").value,
              signature: document.getElementById("coSig").value,
            },
          });
          Bass.updateAppAlert("success", res.message);
          await loadMyhome();
        });
      });
    }

    const btnPlanStay = document.getElementById("btnPlanStay");
    if (btnPlanStay) {
      btnPlanStay.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const memo = (document.getElementById("expTenantMemo") && document.getElementById("expTenantMemo").value) || "";
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/expiry-plan`, {
            method: "POST",
            body: {
              tenant_plan: "STAY",
              tenant_memo: memo || "전출의사 없음 — 계속 거주 및 연장 희망",
              prelist_vacancy: false,
            },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    }

    const btnPlanMoveOut = document.getElementById("btnPlanMoveOut");
    if (btnPlanMoveOut) {
      btnPlanMoveOut.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const moveDate = (document.getElementById("expMoveOutDate") && document.getElementById("expMoveOutDate").value) || ct.expiry_date || "";
          const memo = (document.getElementById("expTenantMemo") && document.getElementById("expTenantMemo").value) || "";
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/expiry-plan`, {
            method: "POST",
            body: {
              tenant_plan: "MOVE_OUT",
              planned_move_out_date: moveDate,
              tenant_memo: memo || `만기일(${moveDate}) 퇴실 예정`,
              prelist_vacancy: true,
            },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    }

    const btnAckRenewal = document.getElementById("btnAckRenewal");
    if (btnAckRenewal) {
      btnAckRenewal.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const memo = (document.getElementById("expTenantMemo") && document.getElementById("expTenantMemo").value) || "";
          const res = await Bass.api(APP_ID, `/myhome/${t.id}/acknowledge-renewal`, {
            method: "POST",
            body: { tenant_memo: memo },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await loadMyhome();
        });
      });
    }
  }

  async function loadListings() {
    const data = await Bass.api(APP_ID, "/listings");
    document.getElementById("poolBadge").textContent = `JSON Pool v${data.pool_revision}`;
    const list = data.listings || [];

    elListings.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📢 중개수수료(복비) 0원 즉시입주·만기예정 직거래 공실 쇼케이스 (${list.length}실)</h3>
        <p class="bass-muted" style="font-size:13px;">임대인 공실 호실 및 만기 전출 확정 호실(<code>rooms.json</code>)이 실시간으로 표시되며, 계약 체결 즉시 자동으로 목록에서 마감됩니다.</p>
      </div>
      ${list.map((r) => `
        <div class="listing-card">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <strong style="font-size:17px;">[${Bass.escapeHtml(r.building_name)}] ${Bass.escapeHtml(r.name || `${r.room_no}호`)}</strong>
              <div class="bass-muted" style="font-size:13px;">${Bass.escapeHtml(r.address)} · ${r.area_sqm}㎡ · ${Bass.escapeHtml(r.available_from)}</div>
            </div>
            <div class="bass-row" style="gap:6px;">
              ${r.is_pre_moveout_listing ? `<span class="bass-badge bass-badge--warn">📅 만기퇴실 예정 사전공개</span>` : ""}
              <span class="bass-badge bass-badge--ok">등기부 근저당 0원 클린</span>
            </div>
          </div>
          <div style="font-size:16px;font-weight:800;margin:8px 0;color:var(--primary-2);">
            보증금 ${fmtWon(r.deposit)} / 월세 ${fmtWon(r.monthly_rent)} (관리비 ${fmtWon(r.maintenance_fee)}, ${Bass.escapeHtml(r.pay_timing)})
          </div>
          <div class="bass-muted" style="font-size:13px;margin-bottom:10px;">${Bass.escapeHtml(r.status_description)}</div>
          <div class="bass-row">
            <input class="bass-input inq-name" data-room="${r.room_no}" style="width:120px;" placeholder="내 이름">
            <input class="bass-input inq-msg" data-room="${r.room_no}" style="flex:1;" value="이번 주말 현장 방문투어 예약하고 싶습니다!">
            <button class="bass-btn bass-btn--sm btn-send-inq" data-branch="${r.branch}" data-room="${r.room_no}" type="button">💬 1:1 방문문의</button>
            ${r.active_cosign_session
              ? `<a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?session=${r.active_cosign_session.id}&role=tenant">🤝 열린 계약방 입장(코드 ${r.active_cosign_session.session_code})</a>`
              : `<a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?role=tenant">🤝 공동 임대계약 앱 이동</a>`
            }
          </div>
        </div>
      `).join("")}
    `;

    elListings.querySelectorAll(".btn-send-inq").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const roomNo = btn.dataset.room;
          const name = elListings.querySelector(`.inq-name[data-room="${roomNo}"]`).value.trim() || "예비임차인";
          const msg = elListings.querySelector(`.inq-msg[data-room="${roomNo}"]`).value.trim();
          const res = await Bass.api(APP_ID, "/inquiries", {
            method: "POST",
            body: { branch: btn.dataset.branch, room_no: roomNo, sender_name: name, message: msg },
          });
          Bass.updateAppAlert("success", res.message);
        });
      });
    });
  }

  setMode(mode);
})();

