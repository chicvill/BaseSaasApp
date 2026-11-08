/* apps/rent3_admin/frontend/app.js — 플랫폼 총괄 관리인 관제 클라이언트 */
(function () {
  "use strict";

  const APP_ID = "rent3_admin";
  let currentTab = "lessors";
  let selectedCollection = "contracts";
  let ov = null;

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

  // 가맹 신청 요약: 본인인증·건물·등기부 자동 대조 결과와 제출된 등기부 원본 링크
  function appSummaryHtml(l) {
    const a = l.application || {};
    const s = a.steps || {};
    const chk = (a.registry || {}).check;
    const mark = (ok) => (ok ? "✅" : "⬜");
    const col = chk ? ({ ok: "var(--ok)", caution: "var(--warn)", high: "var(--err)" }[chk.level] || "var(--warn)") : "";
    const idn = a.identity || {};
    return `<div style="font-size:12.5px;margin:4px 0;line-height:1.6;">
      ${mark(s.identity)} 본인인증${idn.verified ? ` (${Bass.escapeHtml(idn.name)}${idn.fake ? " · <b>시연용 가짜 인증</b>" : ""})` : ""}
      · ${mark(s.building)} 건물 주소 · ${mark(s.registry)} 등기부 · ${mark(s.submitted)} 제출
      ${s.registry ? ` · <a href="${Bass.apiBase(APP_ID)}/lessors/${encodeURIComponent(l.id)}/registry-attachment?token=${encodeURIComponent(Bass.token())}" target="_blank" rel="noopener">등기부 원본 보기</a>` : ""}
      ${chk ? `<div style="border-left:4px solid ${col};padding:4px 8px;margin-top:4px;">${chk.auto_pass ? "🟢 자동 대조 일치 (심사 우선)" : "🟠 수동 확인 필요"} — ${Bass.escapeHtml(chk.message)}</div>` : ""}
      ${s.registry ? iirosHtml(l, a) : ""}
    </div>`;
  }

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

    elTabContent.querySelectorAll(".btn-decide").forEach((btn) => {
      btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const d = btn.dataset.d;
          let note = "";
          if (d !== "approve") {
            note = (window.prompt(d === "reject" ? "반려 사유 (임대인에게 표시됩니다)" : "정지 사유 (임대인에게 표시됩니다)") || "").trim();
            if (!note) { Bass.updateAppAlert("warning", "사유를 입력해야 합니다."); return; }
          }
          const cur = (ov.lessors || []).find((x) => x.id === btn.dataset.id) || {};
          const app = cur.application || {};
          let force = false;
          if (d === "approve" && cur.status === "PENDING_APPROVAL" && !(app.steps && app.steps.submitted && app.ready_to_submit)) {
            note = (window.prompt("가맹 신청 서류가 완료되지 않았습니다. 그래도 승인하려면 사유를 입력하세요 (기록으로 남습니다).") || "").trim();
            if (!note) { Bass.updateAppAlert("warning", "사유 없이는 서류 미비 상태로 승인할 수 없습니다."); return; }
            force = true;
          }
          const res = await Bass.api(APP_ID, `/lessors/${btn.dataset.id}/decision`, { method: "POST", body: { decision: d, note, force } });
          Bass.updateAppAlert("success", res.message);
          await refresh();
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
          await refresh();
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
          await refresh();
        });
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

