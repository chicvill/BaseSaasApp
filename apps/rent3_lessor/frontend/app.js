/* apps/rent3_lessor/frontend/app.js — 임대인 전용 자산·FIFO 수납·세무·운영 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_lessor";
  const params = new URLSearchParams(window.location.search);
  let currentBranch = params.get("branch") || "main";
  let currentTab = params.get("tab") || "ledger";
  let dash = null;
  let brokerTargetRoomNo = "";
  let brokerPhotoDataUrl = "";
  let brokerPhotoFilename = "";
  const openPhotoRooms = new Set();

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

  function switchTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === tabName));
    renderTab();
  }

  document.getElementById("tabBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-btn");
    if (!btn) return;
    switchTab(btn.dataset.tab);
  });

  function renderTab() {
    if (!dash) return;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === currentTab));
    if (currentTab === "ledger") renderLedgerTab();
    else if (currentTab === "broker") renderBrokerTab();
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
        const ct = r.contract;
        const hasPhoto = Boolean(ct && ct.contract_photo_file);
        const photoUrl = hasPhoto ? `${Bass.apiBase(APP_ID)}/contracts/${encodeURIComponent(ct.id)}/photo` : "";
        const isPhotoOpen = openPhotoRooms.has(r.room_no);
        const isPdfPhoto = hasPhoto && String(ct.contract_photo_file).toLowerCase().endsWith(".pdf");
        const brokerAgency = (ct && ct.broker_info && ct.broker_info.agency_name) || "공인중개사사무소";
        return `
          <div class="room-card">
            <div class="bass-row" style="justify-content:space-between;margin-bottom:8px;">
              <div>
                <strong style="font-size:17px;">${Bass.escapeHtml(r.room_no)}호</strong>
                <span class="bass-muted" style="font-size:13px;"> · ${Bass.escapeHtml(r.room_category)} · 보 ${fmtWon(r.deposit)} / 월 ${fmtWon(r.monthly_rent)} / 관 ${fmtWon(r.maintenance_fee)} (${Bass.escapeHtml(r.pay_timing)})</span>
                ${hasPhoto ? ` <span class="bass-badge bass-badge--info">📸 중개 지면계약 (${Bass.escapeHtml(brokerAgency)})</span>` : ""}
              </div>
              <div class="bass-row">
                ${r.is_occupied
                  ? `<span class="bass-badge ${lg && lg.total_unpaid > 0 ? "bass-badge--err" : "bass-badge--ok"}">${lg && lg.total_unpaid > 0 ? `미납 ${fmtWon(lg.total_unpaid)}` : "완납(정상)"}</span>
                     ${hasPhoto ? `<button class="bass-btn bass-btn--sm btn-toggle-photo" data-room="${r.room_no}" type="button">${isPhotoOpen ? "🖼️ 계약서 닫기" : "📸 첨부 계약서 보기"}</button>` : ""}
                     <button class="bass-btn bass-btn--sm btn-go-broker" data-room="${r.room_no}" type="button">📸 중개 재계약 등록</button>
                     <a class="bass-btn bass-btn--sm" href="/rent3_contract/?role=lessor">🤝 공동 재계약방</a>`
                  : `<span class="bass-badge ${r.is_published ? "bass-badge--info" : "bass-badge--warn"}">${r.is_published ? "공실 매물 공개중" : "공실 비공개"}</span>
                     <button class="bass-btn bass-btn--sm btn-pub-toggle" data-id="${r.id}" data-pub="${!r.is_published}" type="button">${r.is_published ? "비공개 전환" : "직거래 매물 공개"}</button>
                     <button class="bass-btn bass-btn--sm btn-go-broker" data-room="${r.room_no}" type="button">📸 중개계약 사진등록</button>
                     <a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?role=lessor">🤝 공동 임대계약 시작</a>`
                }
              </div>
            </div>
            ${r.is_occupied && lg ? `
              <div style="font-size:13px;margin-bottom:8px;">
                👤 입주자: <strong>${Bass.escapeHtml(lg.tenant_name)}</strong> (${Bass.escapeHtml((ct && ct.move_in_date) || "")} 입주 · 만기 D-${lg.days_to_expiry ?? "-"})
                ${ct && ct.fixed_date_no ? ` · 확정일자 <code>${Bass.escapeHtml(ct.fixed_date_no)}</code>` : ` · <span style="color:var(--warn);">전월세신고 확인필요</span>`}
              </div>
              ${hasPhoto && isPhotoOpen ? `
                <div class="bass-card" style="margin:8px 0 10px;padding:12px;">
                  <div class="bass-row" style="justify-content:space-between;">
                    <div style="font-size:12.5px;">
                      <strong>📸 첨부된 공인중개사 임대차계약서 사본</strong> · 중개업소: ${Bass.escapeHtml(brokerAgency)}
                      ${ct.broker_info && ct.broker_info.agent_name ? ` (대표: ${Bass.escapeHtml(ct.broker_info.agent_name)})` : ""}
                      · 무결성 SHA-256: <code>${Bass.escapeHtml((ct.integrity_hash || "").slice(0, 14))}...</code>
                    </div>
                    <a class="bass-btn bass-btn--sm" href="${photoUrl}" target="_blank" rel="noopener">🔍 원본 새창 열기</a>
                  </div>
                  ${isPdfPhoto
                    ? `<div class="bass-muted" style="margin-top:8px;font-size:13px;">PDF 계약서 스캔본이 보관되어 있습니다. 우측 상단 [원본 새창 열기]로 열람하세요.</div>`
                    : `<img class="contract-photo-preview" src="${photoUrl}" alt="${Bass.escapeHtml(r.room_no)}호 공인중개사 계약서 사본">`
                  }
                </div>
              ` : ""}
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

    elTabContent.querySelectorAll(".btn-go-broker").forEach((btn) => {
      btn.addEventListener("click", () => {
        brokerTargetRoomNo = btn.dataset.room || "";
        switchTab("broker");
      });
    });

    elTabContent.querySelectorAll(".btn-toggle-photo").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rm = btn.dataset.room;
        if (openPhotoRooms.has(rm)) openPhotoRooms.delete(rm);
        else openPhotoRooms.add(rm);
        renderLedgerTab();
      });
    });
  }

  function buildSampleBrokerPaperDataUrl(roomObj) {
    const c = document.createElement("canvas");
    c.width = 760;
    c.height = 500;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fcfbf7";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 3;
    ctx.strokeRect(18, 18, c.width - 36, c.height - 36);

    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 24px sans-serif";
    ctx.fillText("주택임대차표준계약서 (공인중개사 입회 원본 촬영본)", 95, 62);

    ctx.lineWidth = 1;
    ctx.strokeStyle = "#94a3b8";
    ctx.beginPath();
    ctx.moveTo(40, 82);
    ctx.lineTo(720, 82);
    ctx.stroke();

    const bldgName = (dash && dash.building && dash.building.building_name) || "시크빌";
    const addr = (dash && dash.building && dash.building.address) || "서울특별시";
    const rNo = (roomObj && roomObj.room_no) || "203";
    const dep = fmtWon((roomObj && roomObj.deposit) || 10000000);
    const rent = fmtWon((roomObj && roomObj.monthly_rent) || 520000);
    const maint = fmtWon((roomObj && roomObj.maintenance_fee) || 50000);

    ctx.font = "16px sans-serif";
    ctx.fillStyle = "#1e293b";
    const lines = [
      `1. 부동산의 표시 : ${addr} (${bldgName} ${rNo}호)`,
      `2. 보증금 : 금 ${dep}정  /  계약금 : 금 500,000원정 (계약시 영수함)`,
      `3. 차임(월세) : 금 ${rent}정 (후불)  /  관리비 : 금 ${maint}정`,
      `4. 임대차기간 : 인도일(2026-10-29)로부터 12개월간`,
      `5. 특약사항 : 현 시설물 상태 임대차 계약이며, 퇴실 시 기본 입주청소비 약정함.`,
      `6. 임대인(갑) : ${(dash && dash.lessor && dash.lessor.name) || "임대인"} (인)`,
      `7. 임차인(을) : 최중개  (연락처: 010-7788-9900)  (인)`,
      `8. 개업공인중개사 : 한강공인중개사사무소 대표 박공인 (등록 11440-2024-00112)`,
    ];
    lines.forEach((ln, idx) => {
      ctx.fillText(ln, 46, 122 + idx * 42);
    });

    ctx.strokeStyle = "#dc2626";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(650, 405, 36, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#dc2626";
    ctx.font = "bold 14px sans-serif";
    ctx.fillText("공인중개사", 616, 401);
    ctx.fillText("직인날인", 622, 420);

    return c.toDataURL("image/png");
  }

  function renderBrokerTab() {
    const rooms = dash.rooms || [];
    const vacantFirst = rooms.find((r) => !r.is_occupied) || rooms[0];
    const selectedNo = brokerTargetRoomNo || (vacantFirst ? vacantFirst.room_no : "");
    const selRoom = rooms.find((r) => r.room_no === selectedNo) || vacantFirst || {};
    const todayIso = new Date().toISOString().slice(0, 10);

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📸 부동산중개사 개입 지면계약서 사진 첨부 & 계약 원장 일괄 적용</h3>
        <p class="bass-muted" style="font-size:13px;">
          공인중개사 사무소에서 종이 계약서로 계약이 체결된 경우, 촬영한 계약서 사진을 첨부하고 계약 조건을 적용하면
          <code>uploads/contracts/</code> 이미지 보관과 함께 <code>contracts.json</code> · <code>rooms.json</code>(공실 자동마감) · <code>users.json</code>(임차인 마이홈 개통) · <code>payments.json</code> · <code>ops.json</code>(RTMS 신고관제)이 원클릭으로 동기화됩니다.
        </p>

        <div class="room-card" style="margin-top:12px;">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
            <div>
              <label class="bass-label">1단계 · 종이 임대차계약서 촬영 사진 / 스캔본 첨부 (필수, JPG/PNG/WEBP/PDF)</label>
              <input id="brkPhotoFile" type="file" accept="image/*,application/pdf" class="bass-input" style="max-width:360px;">
            </div>
            <button id="btnGenSamplePhoto" class="bass-btn bass-btn--sm" type="button">🧪 샘플 중개계약서 촬영본 자동 생성</button>
          </div>
          <div id="brkPhotoStatus" class="bass-muted" style="font-size:12.5px;margin-top:6px;">
            ${brokerPhotoDataUrl ? `✅ 첨부 완료: ${Bass.escapeHtml(brokerPhotoFilename || "contract_photo.png")}` : "아직 첨부된 계약서 사진이 없습니다. 파일을 선택하거나 우측 샘플 생성 버튼을 눌러보세요."}
          </div>
          <img id="brkPhotoPreview" class="contract-photo-preview" style="display:${brokerPhotoDataUrl && !brokerPhotoDataUrl.startsWith("data:application/pdf") ? "block" : "none"};" src="${brokerPhotoDataUrl || ""}" alt="첨부 계약서 미리보기">
        </div>

        <div class="room-card">
          <label class="bass-label">💡 선택 기능 · 스마트폰 카메라 텍스트 복사(OCR) 붙여넣기 자동 입력</label>
          <div class="bass-row" style="margin-top:6px;">
            <input id="brkOcrText" class="bass-input" style="flex:1;" placeholder="예: ${Bass.escapeHtml(selRoom.room_no || "203")}호 보증금 1,000만원 월세 52만원 관리비 5만원 후불 입주일 ${todayIso} 임차인 최중개 010-7788-9900 한강공인중개사사무소 대표 박공인">
            <button id="btnParseOcr" class="bass-btn bass-btn--sm" type="button">✨ 문구에서 계약조건 자동 채우기</button>
          </div>
        </div>

        <div class="room-card">
          <h4 style="margin:0 0 10px;">2단계 · 호실 · 임차인 · 계약 금액 및 기간 확인</h4>
          <div class="field-grid">
            <div>
              <label class="bass-label">대상 호실</label>
              <select id="brkRoomNo" class="bass-select">
                ${rooms.map((r) => `<option value="${Bass.escapeHtml(r.room_no)}" ${r.room_no === selectedNo ? "selected" : ""}>${Bass.escapeHtml(r.room_no)}호 (${r.is_occupied ? "입주중-교체/재계약" : "공실"})</option>`).join("")}
              </select>
            </div>
            <div>
              <label class="bass-label">임대 유형</label>
              <select id="brkLeaseType" class="bass-select">
                <option value="월세" ${selRoom.lease_type !== "전세" ? "selected" : ""}>월세</option>
                <option value="전세" ${selRoom.lease_type === "전세" ? "selected" : ""}>전세</option>
              </select>
            </div>
            <div>
              <label class="bass-label">납부 방식 (후불 입주월 비과세 자동적용)</label>
              <select id="brkPayTiming" class="bass-select">
                <option value="후불" ${selRoom.pay_timing !== "선불" ? "selected" : ""}>후불</option>
                <option value="선불" ${selRoom.pay_timing === "선불" ? "selected" : ""}>선불</option>
              </select>
            </div>
            <div><label class="bass-label">보증금(원)</label><input id="brkDeposit" type="number" class="bass-input" value="${Number(selRoom.deposit || 10000000)}"></div>
            <div><label class="bass-label">월세(원)</label><input id="brkRent" type="number" class="bass-input" value="${Number(selRoom.monthly_rent || 500000)}"></div>
            <div><label class="bass-label">관리비(원)</label><input id="brkMaint" type="number" class="bass-input" value="${Number(selRoom.maintenance_fee || 50000)}"></div>
            <div><label class="bass-label">영수 계약금(원)</label><input id="brkContractDep" type="number" class="bass-input" value="500000"></div>
            <div><label class="bass-label">계약 체결일</label><input id="brkContractDate" type="date" class="bass-input" value="${todayIso}"></div>
            <div><label class="bass-label">입주(잔금)일</label><input id="brkMoveInDate" type="date" class="bass-input" value="${todayIso}"></div>
            <div><label class="bass-label">계약 기간(개월)</label><input id="brkMonths" type="number" class="bass-input" value="12"></div>
            <div><label class="bass-label">임차인 성명 (필수)</label><input id="brkTenantName" class="bass-input" placeholder="예: 최중개"></div>
            <div><label class="bass-label">임차인 연락처 (필수)</label><input id="brkTenantPhone" class="bass-input" placeholder="010-7788-9900"></div>
            <div><label class="bass-label">임차인 차량번호 (선택)</label><input id="brkCarNo" class="bass-input" placeholder="예: 34나 7890"></div>
          </div>
          <div style="margin-top:8px;">
            <label style="font-size:13px;cursor:pointer;">
              <input id="brkDepReceived" type="checkbox" checked> 계약금 수령 완료 (체크 시 <code>payments.json</code>에 CONTRACT_DEPOSIT 즉시 등재)
            </label>
          </div>
        </div>

        <div class="room-card">
          <h4 style="margin:0 0 10px;">3단계 · 개업공인중개사 정보 및 전월세 신고제(RTMS) 확인</h4>
          <div class="field-grid">
            <div><label class="bass-label">중개사무소 상호</label><input id="brkAgency" class="bass-input" placeholder="예: 한강공인중개사사무소"></div>
            <div><label class="bass-label">대표 공인중개사 성명</label><input id="brkAgent" class="bass-input" placeholder="예: 박공인"></div>
            <div><label class="bass-label">중개업소 연락처</label><input id="brkBrokerPhone" class="bass-input" placeholder="02-555-1234"></div>
            <div><label class="bass-label">확정일자 번호 (부여 시 입력)</label><input id="brkFixedNo" class="bass-input" placeholder="미입력 시 중개신고 기준 자동채번"></div>
          </div>
          <div style="margin-top:10px;">
            <label class="bass-label">지면 계약서 주요 특약사항 메모</label>
            <input id="brkSpecial" class="bass-input" placeholder="예: 애완동물 사육 금지, 퇴실 시 입주청소비 7만원 정산 약정">
          </div>
          <div style="margin-top:10px;">
            <label style="font-size:13px;cursor:pointer;">
              <input id="brkRtmsFiled" type="checkbox" checked> 공인중개사가 주택임대차(전월세) 신고서 대행 접수 완료 (해제 시 30일 마감 알림 <code>PENDING</code>으로 관리인·임대인 관제)
            </label>
          </div>
        </div>

        <div class="bass-row" style="justify-content:flex-end;margin-top:10px;">
          <button id="btnSubmitBrokerContract" class="bass-btn bass-btn--primary" type="button">📸 계약서 사진 보관 및 계약내용 즉시 적용</button>
        </div>
      </div>
    `;

    document.getElementById("brkRoomNo").addEventListener("change", (ev) => {
      brokerTargetRoomNo = ev.target.value;
      const picked = rooms.find((x) => x.room_no === brokerTargetRoomNo);
      if (picked) {
        document.getElementById("brkDeposit").value = Number(picked.deposit || 0);
        document.getElementById("brkRent").value = Number(picked.monthly_rent || 0);
        document.getElementById("brkMaint").value = Number(picked.maintenance_fee || 0);
        document.getElementById("brkLeaseType").value = picked.lease_type || "월세";
        document.getElementById("brkPayTiming").value = picked.pay_timing || "후불";
      }
    });

    document.getElementById("brkPhotoFile").addEventListener("change", (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        brokerPhotoDataUrl = String(reader.result || "");
        brokerPhotoFilename = file.name;
        document.getElementById("brkPhotoStatus").textContent = `✅ 첨부 완료: ${file.name} (${Math.round(file.size / 1024)} KB)`;
        const prev = document.getElementById("brkPhotoPreview");
        if (brokerPhotoDataUrl.startsWith("data:image/")) {
          prev.src = brokerPhotoDataUrl;
          prev.style.display = "block";
        } else {
          prev.style.display = "none";
        }
      };
      reader.readAsDataURL(file);
    });

    document.getElementById("btnGenSamplePhoto").addEventListener("click", () => {
      const curRoomNo = document.getElementById("brkRoomNo").value;
      const picked = rooms.find((x) => x.room_no === curRoomNo) || selRoom;
      brokerPhotoDataUrl = buildSampleBrokerPaperDataUrl(picked);
      brokerPhotoFilename = `broker_contract_${curRoomNo || "sample"}.png`;
      document.getElementById("brkPhotoStatus").textContent = `✅ 샘플 촬영 이미지 생성 완료: ${brokerPhotoFilename}`;
      const prev = document.getElementById("brkPhotoPreview");
      prev.src = brokerPhotoDataUrl;
      prev.style.display = "block";
      if (!document.getElementById("brkTenantName").value.trim()) {
        document.getElementById("brkTenantName").value = "최중개";
        document.getElementById("brkTenantPhone").value = "010-7788-9900";
        document.getElementById("brkAgency").value = "한강공인중개사사무소";
        document.getElementById("brkAgent").value = "박공인";
      }
      Bass.updateAppAlert("info", "샘플 공인중개사 표준계약서 촬영 이미지가 첨부되었습니다. 내용을 확인한 뒤 하단 적용 버튼을 눌러주세요.");
    });

    document.getElementById("btnParseOcr").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const raw = document.getElementById("brkOcrText").value.trim()
          || document.getElementById("brkOcrText").getAttribute("placeholder").replace(/^예:\s*/, "");
        const res = await Bass.api(APP_ID, "/contracts/parse-broker-text", {
          method: "POST",
          body: { text: raw },
        });
        const p = res.parsed || {};
        if (p.room_no && rooms.some((r) => r.room_no === p.room_no)) {
          document.getElementById("brkRoomNo").value = p.room_no;
          brokerTargetRoomNo = p.room_no;
        }
        if (p.deposit !== undefined) document.getElementById("brkDeposit").value = p.deposit;
        if (p.monthly_rent !== undefined) document.getElementById("brkRent").value = p.monthly_rent;
        if (p.maintenance_fee !== undefined) document.getElementById("brkMaint").value = p.maintenance_fee;
        if (p.contract_deposit !== undefined) document.getElementById("brkContractDep").value = p.contract_deposit;
        if (p.lease_type) document.getElementById("brkLeaseType").value = p.lease_type;
        if (p.pay_timing) document.getElementById("brkPayTiming").value = p.pay_timing;
        if (p.move_in_date) document.getElementById("brkMoveInDate").value = p.move_in_date;
        if (p.tenant_name) document.getElementById("brkTenantName").value = p.tenant_name;
        if (p.tenant_phone) document.getElementById("brkTenantPhone").value = p.tenant_phone;
        if (p.broker_agency_name) document.getElementById("brkAgency").value = p.broker_agency_name;
        if (p.broker_agent_name) document.getElementById("brkAgent").value = p.broker_agent_name;
        if (p.broker_phone) document.getElementById("brkBrokerPhone").value = p.broker_phone;
        Bass.updateAppAlert("info", res.message);
      });
    });

    document.getElementById("btnSubmitBrokerContract").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const roomNo = document.getElementById("brkRoomNo").value;
        const res = await Bass.api(APP_ID, "/contracts/broker-photo", {
          method: "POST",
          body: {
            branch: currentBranch,
            room_no: roomNo,
            photo_data_url: brokerPhotoDataUrl,
            photo_filename: brokerPhotoFilename,
            tenant_name: document.getElementById("brkTenantName").value.trim(),
            tenant_phone: document.getElementById("brkTenantPhone").value.trim(),
            tenant_car_number: document.getElementById("brkCarNo").value.trim(),
            lease_type: document.getElementById("brkLeaseType").value,
            pay_timing: document.getElementById("brkPayTiming").value,
            deposit: Number(document.getElementById("brkDeposit").value || 0),
            monthly_rent: Number(document.getElementById("brkRent").value || 0),
            maintenance_fee: Number(document.getElementById("brkMaint").value || 0),
            contract_deposit: Number(document.getElementById("brkContractDep").value || 0),
            deposit_received: document.getElementById("brkDepReceived").checked,
            contract_date: document.getElementById("brkContractDate").value,
            move_in_date: document.getElementById("brkMoveInDate").value,
            lease_term_months: Number(document.getElementById("brkMonths").value || 12),
            special_terms: document.getElementById("brkSpecial").value.trim(),
            broker_agency_name: document.getElementById("brkAgency").value.trim(),
            broker_agent_name: document.getElementById("brkAgent").value.trim(),
            broker_phone: document.getElementById("brkBrokerPhone").value.trim(),
            rtms_filed_by_broker: document.getElementById("brkRtmsFiled").checked,
            fixed_date_no: document.getElementById("brkFixedNo").value.trim(),
          },
        });
        brokerPhotoDataUrl = "";
        brokerPhotoFilename = "";
        openPhotoRooms.add(roomNo);
        currentTab = "ledger";
        Bass.updateAppAlert(res.level || "success", res.message, res.hint);
        await refresh();
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

