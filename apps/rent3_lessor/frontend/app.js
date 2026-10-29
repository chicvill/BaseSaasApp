/* apps/rent3_lessor/frontend/app.js — 임대인 전용 자산·FIFO 수납·세무·운영 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_lessor";
  const params = new URLSearchParams(window.location.search);
  let currentBranch = params.get("branch") || "main";
  let currentTab = "ledger";
  let dash = null;

  const elBranch = document.getElementById("branchSelect");
  const elTabContent = document.getElementById("tabContent");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  async function refresh() {
    dash = await Bass.api(APP_ID, `/dashboard?branch=${encodeURIComponent(currentBranch)}`);
    document.getElementById("poolRevBadge").textContent = `JSON Pool v${dash.pool_revision}`;

    elBranch.innerHTML = (dash.branches || []).map((b) =>
      `<option value="${b.branch}" ${b.branch === dash.branch ? "selected" : ""}>🏢 ${Bass.escapeHtml(b.building_name)} (${b.branch})</option>`
    ).join("");

    const b = dash.building || {};
    const l = dash.lessor || {};
    document.getElementById("bldgSubtitle").textContent = `${b.building_name || ""} · ${b.address || ""} · 소유자: ${l.name || "-"}`;

    const fin = dash.financial_summary || {};
    document.getElementById("kpiGrid").innerHTML = `
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">가동 호실 / 전체</div><div class="kpi-val">${fin.occupied_rooms || 0} / ${fin.total_rooms || 0}실 (${fin.occupancy_rate || 0}%)</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">지점 미납 합계 (가짜체납 0)</div><div class="kpi-val" style="color:${dash.total_unpaid_branch > 0 ? "var(--err)" : "var(--ok)"};">${fmtWon(dash.total_unpaid_branch)}</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">연간 누적 수납액</div><div class="kpi-val">${fmtWon(fin.gross_revenue)}</div></div>
      <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">순운영이익 (NOI)</div><div class="kpi-val" style="color:var(--primary-2);">${fmtWon(fin.noi)}</div></div>
    `;

    renderTab();
  }

  elBranch.addEventListener("change", () => {
    currentBranch = elBranch.value;
    refresh().catch(Bass.showError);
  });

  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    currentTab = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b === btn));
    renderTab();
  });

  function renderTab() {
    if (!dash) return;
    if (currentTab === "ledger") renderLedgerTab();
    else if (currentTab === "tax") renderTaxTab();
    else if (currentTab === "ops") renderOpsTab();
    else renderRoomsTab();
  }

  function renderLedgerTab() {
    const rooms = dash.rooms || [];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📩 은행 입금 알림문자 붙여넣기 (자동 파싱 & FIFO 순차 상계)</h3>
        <p class="bass-muted" style="font-size:13px;">가장 오래된 미납월부터 순차 상계하며, 동일 입금 재등록은 자동 차단(황색 배너)됩니다.</p>
        <div class="bass-row" style="margin-top:8px;">
          <input id="smsInput" class="bass-input" style="flex:1;" value="[신한은행] 10/28 202호 정서연 570,000원 입금 잔액 15,200,000원">
          <button id="btnPasteSms" class="bass-btn bass-btn--primary" type="button">⚡ 문자 파싱 & 즉시 수납 (payments.json 갱신)</button>
        </div>
      </div>

      ${rooms.map((r) => {
        const lg = r.ledger;
        return `
          <div class="room-card">
            <div class="bass-row" style="justify-content:space-between;margin-bottom:8px;">
              <div>
                <strong style="font-size:17px;">${Bass.escapeHtml(r.room_no)}호</strong>
                <span class="bass-muted" style="font-size:13px;"> · ${Bass.escapeHtml(r.room_category)} · 보 ${fmtWon(r.deposit)} / 월 ${fmtWon(r.monthly_rent)} / 관 ${fmtWon(r.maintenance_fee)} (${Bass.escapeHtml(r.pay_timing)})</span>
              </div>
              <div class="bass-row">
                ${r.is_occupied
                  ? `<span class="bass-badge ${lg && lg.total_unpaid > 0 ? "bass-badge--err" : "bass-badge--ok"}">${lg && lg.total_unpaid > 0 ? `미납 ${fmtWon(lg.total_unpaid)}` : "완납(정상)"}</span>
                     <a class="bass-btn bass-btn--sm" href="/rent3_contract/?role=lessor">🤝 재계약방</a>`
                  : `<span class="bass-badge ${r.is_published ? "bass-badge--info" : "bass-badge--warn"}">${r.is_published ? "공실 매물 공개중" : "공실 비공개"}</span>
                     <button class="bass-btn bass-btn--sm btn-pub-toggle" data-id="${r.id}" data-pub="${!r.is_published}" type="button">${r.is_published ? "비공개 전환" : "직거래 매물 공개"}</button>
                     <a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?role=lessor">🤝 공동 임대계약 시작</a>`
                }
              </div>
            </div>
            ${r.is_occupied && lg ? `
              <div style="font-size:13px;margin-bottom:8px;">
                👤 입주자: <strong>${Bass.escapeHtml(lg.tenant_name)}</strong> (${Bass.escapeHtml((r.contract && r.contract.move_in_date) || "")} 입주 · 만기 D-${lg.days_to_expiry ?? "-"})
                ${r.contract && r.contract.fixed_date_no ? ` · 확정일자 <code>${Bass.escapeHtml(r.contract.fixed_date_no)}</code>` : ""}
              </div>
              <div style="margin-bottom:8px;">
                ${(lg.months || []).map((m) => `<span class="month-chip month-${m.status}">${m.ym}: ${Bass.escapeHtml(m.note)}</span>`).join("")}
              </div>
              <div class="bass-row">
                <input class="bass-input manual-amt" data-room="${r.room_no}" type="number" style="width:160px;" value="${r.monthly_rent + r.maintenance_fee}" placeholder="수납액(원)">
                <button class="bass-btn bass-btn--sm btn-manual-pay" data-room="${r.room_no}" type="button">💰 직접 FIFO 수납</button>
              </div>
            ` : `<div class="bass-muted" style="font-size:13px;">${Bass.escapeHtml(r.status_description)}</div>`}
          </div>
        `;
      }).join("")}
    `;

    document.getElementById("btnPasteSms").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const sms = document.getElementById("smsInput").value.trim();
        const res = await Bass.api(APP_ID, "/payments/sms-paste", {
          method: "POST",
          body: { branch: currentBranch, sms_text: sms },
        });
        Bass.updateAppAlert(res.level || "success", res.message);
        await refresh();
      });
    });

    elTabContent.querySelectorAll(".btn-pub-toggle").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/publish`, {
            method: "POST",
            body: { is_published: btn.dataset.pub === "true" },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-manual-pay").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const roomNo = btn.dataset.room;
          const inp = elTabContent.querySelector(`.manual-amt[data-room="${roomNo}"]`);
          const res = await Bass.api(APP_ID, "/payments/collect", {
            method: "POST",
            body: { branch: currentBranch, room_no: roomNo, amount: Number(inp.value || 0) },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await refresh();
        });
      });
    });
  }

  function renderTaxTab() {
    const fin = dash.financial_summary || {};
    const cats = dash.expense_categories || {};
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;">
          <div>
            <h3>📊 ${fin.year}년 공용 운영비 실결산 & 순운영이익(NOI)</h3>
            <div class="bass-muted" style="font-size:13px;">총수입 ${fmtWon(fin.gross_revenue)} − 필요경비 ${fmtWon(fin.total_operating_expense)} = <strong>순이익 ${fmtWon(fin.noi)}</strong> (15.4% 분리과세 예상세액 ${fmtWon(fin.estimated_separate_tax)})</div>
          </div>
          <a class="bass-btn bass-btn--primary" href="${Bass.apiBase(APP_ID)}/tax/export-csv?branch=${encodeURIComponent(currentBranch)}&year=${fin.year}">📥 5월 종소세 홈택스 CSV 다운로드</a>
        </div>
        <div class="field-grid" style="margin-top:12px;">
          ${(fin.expenses_by_category || []).map((c) => `
            <div class="kpi-box"><div class="bass-muted" style="font-size:12px;">${Bass.escapeHtml(c.label)}</div><div style="font-weight:700;margin-top:4px;">${fmtWon(c.amount)}</div></div>
          `).join("")}
        </div>
      </div>

      <div class="bass-card">
        <h3>🧾 신규 공용 운영비 지출 등재 (outlays.json 실시간 반영)</h3>
        <div class="field-grid" style="margin:10px 0;">
          <div>
            <label class="bass-label">8대 표준 분류</label>
            <select id="outCat" class="bass-select">
              ${Object.entries(cats).map(([k, v]) => `<option value="${k}">${Bass.escapeHtml(v)}</option>`).join("")}
            </select>
          </div>
          <div><label class="bass-label">적요</label><input id="outTitle" class="bass-input" placeholder="예: 복도 센서등 교체"></div>
          <div><label class="bass-label">지출 금액(원)</label><input id="outAmt" type="number" class="bass-input" value="85000"></div>
          <div><label class="bass-label">거래처</label><input id="outVendor" class="bass-input" placeholder="예: 한빛전기"></div>
        </div>
        <button id="btnAddOutlay" class="bass-btn bass-btn--primary" type="button">➕ 공용 운영비 저장</button>
      </div>
    `;

    document.getElementById("btnAddOutlay").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/outlays", {
          method: "POST",
          body: {
            branch: currentBranch,
            category: document.getElementById("outCat").value,
            title: document.getElementById("outTitle").value.trim() || "공용부 유지보수",
            amount: Number(document.getElementById("outAmt").value || 0),
            vendor: document.getElementById("outVendor").value.trim(),
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });
  }

  function renderOpsTab() {
    const tickets = dash.tickets || [];
    const checkouts = dash.checkouts || [];
    const chats = dash.chats || [];
    const occRooms = (dash.rooms || []).filter((r) => r.is_occupied);

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🔧 A/S 하자보수 접수 대장 & 협력업체 원클릭 배정</h3>
        ${tickets.length ? tickets.map((t) => `
          <div class="room-card" style="margin-top:10px;">
            <div class="bass-row" style="justify-content:space-between;">
              <strong>[${Bass.escapeHtml(t.room_no)}호 · ${Bass.escapeHtml(t.category)}] ${Bass.escapeHtml(t.title)}</strong>
              <span class="bass-badge ${t.status === "COMPLETED" ? "bass-badge--ok" : "bass-badge--warn"}">${t.status}</span>
            </div>
            <div class="bass-muted" style="font-size:13px;margin:6px 0;">${Bass.escapeHtml(t.description)} (요청자: ${Bass.escapeHtml(t.tenant_name)})</div>
            ${t.status === "RECEIVED" ? `
              <button class="bass-btn bass-btn--sm bass-btn--primary btn-assign-as" data-id="${t.id}" type="button">👷 협력 설비기사 즉시 배정 (채팅 자동안내)</button>
            ` : t.status === "ASSIGNED" ? `
              <button class="bass-btn bass-btn--sm bass-btn--primary btn-done-as" data-id="${t.id}" type="button">✅ 수리 완료 & 임대인 필요경비(outlays.json) 자동 등재</button>
            ` : `<div style="font-size:12.5px;color:var(--ok);">완료 내역: ${fmtWon(t.repair_cost)} (${t.cost_burden})</div>`}
          </div>
        `).join("") : `<p class="bass-muted">접수된 A/S 티켓이 없습니다.</p>`}
      </div>

      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🚪 퇴실 보증금 공제 정산 & 공실 자동 전환</h3>
        <div class="field-grid" style="margin:10px 0;">
          <div>
            <label class="bass-label">퇴실 대상 호실</label>
            <select id="coRoomSel" class="bass-select">
              ${occRooms.map((r) => `<option value="${r.room_no}">${r.room_no}호 (${Bass.escapeHtml(r.tenant ? r.tenant.name : "")})</option>`).join("")}
            </select>
          </div>
          <div><label class="bass-label">당일 공과금 정산액(원)</label><input id="coUtil" type="number" class="bass-input" value="35000"></div>
          <div><label class="bass-label">약정 입주청소비(원)</label><input id="coClean" type="number" class="bass-input" value="70000"></div>
        </div>
        <button id="btnCalcCo" class="bass-btn bass-btn--primary" type="button">📋 미납월세 자동합산 & 퇴실 정산서 발행</button>

        <div style="margin-top:12px;">
          ${checkouts.map((c) => `
            <div class="room-card">
              <div class="bass-row" style="justify-content:space-between;">
                <strong>${c.room_no}호 (${Bass.escapeHtml(c.tenant_name)}) — 실반환액 ${fmtWon(c.refund_amount)}</strong>
                <span class="bass-badge ${c.status === "COMPLETED" ? "bass-badge--ok" : "bass-badge--info"}">${c.status}</span>
              </div>
              <div class="bass-muted" style="font-size:12.5px;margin:4px 0;">원보증금 ${fmtWon(c.deposit_original)} − 미납월세 ${fmtWon(c.unpaid_rent_deduction)} − 공과금 ${fmtWon(c.utility_settlement)} − 청소비 ${fmtWon(c.cleaning_fee)}</div>
              ${c.status !== "COMPLETED" ? `<button class="bass-btn bass-btn--sm bass-btn--primary btn-complete-co" data-id="${c.id}" type="button">💸 보증금 반환 완료 & 호실 즉시 공실 전환</button>` : ""}
            </div>
          `).join("")}
        </div>
      </div>

      <div class="bass-card">
        <h3>💬 비용 0원 1:1 인앱 직통 메신저</h3>
        <div class="bass-row" style="margin:10px 0;">
          <input id="chatRoomNo" class="bass-input" style="width:100px;" value="203" placeholder="호실">
          <input id="chatMsg" class="bass-input" style="flex:1;" placeholder="세입자/예비임차인에게 보낼 답변 입력">
          <button id="btnSendChat" class="bass-btn bass-btn--primary" type="button">전송</button>
        </div>
        ${chats.slice(0, 8).map((m) => `
          <div style="padding:8px 10px;border-bottom:1px solid var(--border);font-size:13px;">
            <strong>[${Bass.escapeHtml(m.room_no)}호 · ${Bass.escapeHtml(m.sender_name)}]</strong> ${Bass.escapeHtml(m.message)}
          </div>
        `).join("")}
      </div>
    `;

    elTabContent.querySelectorAll(".btn-assign-as").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/tickets/${btn.dataset.id}/assign`, {
            method: "POST",
            body: { vendor_name: "우리설비 김기사", vendor_phone: "010-5050-8282", visit_date: "내일 14:00" },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    elTabContent.querySelectorAll(".btn-done-as").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/tickets/${btn.dataset.id}/complete`, {
            method: "POST",
            body: { repair_cost: 60000, cost_burden: "LANDLORD", resolution_note: "노후 부품 신품 무상 교체 완료" },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    const btnCalc = document.getElementById("btnCalcCo");
    if (btnCalc) {
      btnCalc.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const roomNo = document.getElementById("coRoomSel").value;
          if (!roomNo) {
            Bass.updateAppAlert("warning", "퇴실 정산할 입주 호실이 없습니다.");
            return;
          }
          const res = await Bass.api(APP_ID, "/checkout/calculate", {
            method: "POST",
            body: {
              branch: currentBranch,
              room_no: roomNo,
              utility_settlement: Number(document.getElementById("coUtil").value || 0),
              cleaning_fee: Number(document.getElementById("coClean").value || 0),
            },
          });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    }

    elTabContent.querySelectorAll(".btn-complete-co").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/checkout/${btn.dataset.id}/complete`, { method: "POST" });
          Bass.updateAppAlert("success", res.message);
          await refresh();
        });
      });
    });

    document.getElementById("btnSendChat").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const msg = document.getElementById("chatMsg").value.trim();
        if (!msg) return;
        const res = await Bass.api(APP_ID, "/chats", {
          method: "POST",
          body: { branch: currentBranch, room_no: document.getElementById("chatRoomNo").value.trim(), message: msg },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
      });
    });
  }

  function renderRoomsTab() {
    const rooms = dash.rooms || [];
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>➕ 신규 호실 객체 추가 / 임대 조건 변경 (rooms.json 실시간 반영)</h3>
        <div class="field-grid" style="margin:10px 0;">
          <div><label class="bass-label">호실 번호</label><input id="nrNo" class="bass-input" placeholder="예: 204"></div>
          <div>
            <label class="bass-label">부동산 유형</label>
            <select id="nrCat" class="bass-select">
              <option value="주거용 원룸">주거용 원룸 (면세)</option>
              <option value="오피스텔">오피스텔</option>
              <option value="상가/사무실">상가/사무실 (VAT 10%)</option>
            </select>
          </div>
          <div><label class="bass-label">보증금(원)</label><input id="nrDep" type="number" class="bass-input" value="5000000"></div>
          <div><label class="bass-label">월세(원)</label><input id="nrRent" type="number" class="bass-input" value="500000"></div>
          <div><label class="bass-label">관리비(원)</label><input id="nrMaint" type="number" class="bass-input" value="50000"></div>
        </div>
        <button id="btnCreateRoom" class="bass-btn bass-btn--primary" type="button">🚪 호실 저장 및 직거래 매물 게시</button>
      </div>

      <div class="bass-card">
        <h3>🔑 호실별 디지털 도어락 & 등록 주차 차량 관제</h3>
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
    `;

    document.getElementById("btnCreateRoom").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const roomNo = document.getElementById("nrNo").value.trim();
        if (!roomNo) {
          Bass.updateAppAlert("warning", "호실 번호를 입력해 주세요.");
          return;
        }
        const cat = document.getElementById("nrCat").value;
        const res = await Bass.api(APP_ID, "/rooms", {
          method: "POST",
          body: {
            branch: currentBranch,
            room_no: roomNo,
            room_category: cat,
            vat_applicable: cat === "상가/사무실",
            deposit: Number(document.getElementById("nrDep").value || 0),
            monthly_rent: Number(document.getElementById("nrRent").value || 0),
            maintenance_fee: Number(document.getElementById("nrMaint").value || 0),
            is_published: true,
          },
        });
        Bass.updateAppAlert("success", res.message);
        await refresh();
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
          await refresh();
        });
      });
    });
  }

  refresh().catch(Bass.showError);
})();

