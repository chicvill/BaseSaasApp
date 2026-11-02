/* apps/rent3_admin/frontend/app.js — 플랫폼 총괄 관리인 관제 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_admin";
  let currentTab = "lessors";
  let selectedCollection = "contracts";
  let ov = null;

  const elTabContent = document.getElementById("tabContent");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  async function refresh() {
    ov = await Bass.api(APP_ID, "/overview");
    const meta = ov.pool_meta || {};
    const kpi = ov.kpi || {};
    document.getElementById("poolBadge").textContent = `JSON Pool v${meta.total_revision} (${meta.total_objects} 객체)`;

    document.getElementById("kpiGrid").innerHTML = `
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">가맹 건물 / 전체 호실</div><div class="kpi-val">${kpi.total_buildings}채 / ${kpi.occupied_rooms}/${kpi.total_rooms}실</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">플랫폼 월 SaaS MRR (복비 0)</div><div class="kpi-val" style="color:var(--ok);">${fmtWon(kpi.monthly_saas_mrr)}</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">전사 연간 총 임대수입</div><div class="kpi-val">${fmtWon(kpi.portfolio_gross_revenue)}</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">전사 순운영이익 (NOI)</div><div class="kpi-val" style="color:var(--primary-2);">${fmtWon(kpi.portfolio_noi)}</div></div>
    `;

    renderTab();
  }

  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    currentTab = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    renderTab();
  });

  function renderTab() {
    if (!ov) return;
    if (currentTab === "lessors") renderLessorsTab();
    else if (currentTab === "portfolio") renderPortfolioTab();
    else renderPoolTab();
  }

  function renderLessorsTab() {
    const lessors = ov.lessors || [];
    elTabContent.innerHTML = `
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
        ${lessors.map((l) => `
          <div class="item-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <div>
                <strong>${Bass.escapeHtml(l.name)}</strong> (지점: <code>${Bass.escapeHtml(l.branch)}</code> · 연락처: ${Bass.escapeHtml(l.phone)})
                <div class="bass-muted" style="font-size:12.5px;">사업자: ${Bass.escapeHtml(l.business_number || "-")} · 계좌: ${Bass.escapeHtml(l.account_number || "-")} · 월 사용료: ${fmtWon(l.app_fee)}</div>
              </div>
              <div class="bass-row">
                <span class="bass-badge ${l.app_fee_status === "PAID" ? "bass-badge--ok" : "bass-badge--warn"}">사용료 ${l.app_fee_status}</span>
                ${l.app_fee_status !== "PAID" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-settle-fee" data-id="${l.id}" type="button">💰 사용료 완납 정산</button>` : ""}
                <a class="bass-btn bass-btn--sm" href="/rent3_lessor/?branch=${encodeURIComponent(l.branch)}">지점 장부 열기 →</a>
              </div>
            </div>
          </div>
        `).join("")}
      </div>
    `;

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
        await refresh();
      });
    });

    elTabContent.querySelectorAll(".btn-settle-fee").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/settle-fee`, { method: "POST" });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });
  }

  function renderPortfolioTab() {
    const branches = ov.branches || [];
    const rtms = ov.rtms_reports || [];
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

  async function renderPoolTab() {
    const res = await Bass.api(APP_ID, `/pool/${encodeURIComponent(selectedCollection)}`);
    const cols = res.collections || [];
    const events = ov.recent_events || [];

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;">
          <div>
            <h3>📂 공동 실시간 JSON Data Pool 원문 탐색기 (<code>data/rent3_pool/${Bass.escapeHtml(res.file)}</code>)</h3>
            <div class="bass-muted" style="font-size:12.5px;">파일 크기: ${res.size_bytes.toLocaleString()} bytes · 리비전 v${res.revision} · 객체 ${res.count}건 · 최종 갱신: ${Bass.escapeHtml(res.updated_at)}</div>
          </div>
          <select id="colSelect" class="bass-select" style="width:auto;">
            ${cols.map((c) => `<option value="${c}" ${c === selectedCollection ? "selected" : ""}>📄 ${c}.json</option>`).join("")}
          </select>
        </div>
        <pre class="json-view">${Bass.escapeHtml(JSON.stringify(res.items, null, 2))}</pre>
      </div>

      <div class="bass-card">
        <h3>⚡ 실시간 객체 업데이트 감사 스트림 (<code>_events.jsonl</code> 최근 20건)</h3>
        ${events.slice(0, 20).map((e) => `
          <div style="padding:7px 8px;border-bottom:1px solid var(--border);font-size:12.5px;">
            <code>${Bass.escapeHtml(e.at)}</code> · <span class="bass-badge">${Bass.escapeHtml(e.app_source)}</span>
            <strong>${Bass.escapeHtml(e.collection)}.json</strong> [${Bass.escapeHtml(e.action)}] — ${Bass.escapeHtml(e.summary)}
          </div>
        `).join("")}
      </div>
    `;

    document.getElementById("colSelect").addEventListener("change", (ev) => {
      selectedCollection = ev.target.value;
      renderPoolTab().catch(Bass.showError);
    });
  }

  refresh().catch(Bass.showError);
})();

