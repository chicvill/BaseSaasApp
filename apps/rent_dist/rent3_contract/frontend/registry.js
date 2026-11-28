/* apps/rent_dist/rent3_contract/frontend/registry.js — 등기부 직접 열람·AI 분석·위험 차단·1단계 및 입주 전 재확인. */
(function () {
  "use strict";
  const C = window.R3C;
  if (!C) return;

  const { APP_ID, currentRole, state: S, dom: D, esc, fmtWon } = C;

  // ───────────── 등기부 직접 확인 (임차인) ─────────────
  const IROS_URL = "https://www.iros.go.kr";
  function irosTargetUrl() {
    return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "")
      ? "https://m.iros.go.kr"
      : IROS_URL;
  }

  // 인터넷등기소 열기: 열기 전에 검색할 주소를 클립보드에 복사한다.
  function searchAddress(s) {
    const rn = String((s && s.room_no) || "").trim();
    const unit = rn ? (/호$/.test(rn) ? rn : `${rn}호`) : "";
    return `${(s && s.building_address) || ""} ${unit}`.trim();
  }

  function pdfGuideStepsHtml() {
    return `
      <ol style="margin:6px 0 0 18px;padding:0;font-size:12.5px;line-height:1.65;">
        <li>등기소 로그인(비회원 가능) → <strong>부동산 열람 (700원)</strong> → 복사한 주소 <strong>붙여넣기</strong></li>
        <li><strong>현재 유효사항 + 주요 등기사항 요약 포함</strong> 선택 후 결제 (최초 열람 후 1시간 무료 재열람)</li>
        <li><strong>💻 PC (권장):</strong> 열람창 <strong>[인쇄] → PDF로 저장</strong>(또는 Microsoft Print to PDF)</li>
        <li><strong>📱 스마트폰:</strong> 공식 <strong>'인터넷등기소' 앱은 보안상 캡처·PDF 저장이 차단</strong>됩니다. 모바일 웹(<code>m.iros.go.kr</code>) 인쇄→PDF 저장 또는 <strong>PC PDF 저장</strong>(현장에서는 타 기기로 화면 촬영)을 이용해 주세요.</li>
      </ol>`;
  }

  function irosBoxHtml(addr) {
    return `
      <div style="font-size:12.5px;margin-top:8px;">① 검색할 주소를 복사하고, ② 법원등기소에서 붙여넣어 열람·PDF 저장 후 아래에 올려 AI 분석을 받으세요.</div>
      <div class="bass-row" style="gap:6px;margin-top:6px;">
        <input id="irosAddr" class="bass-input" style="flex:1;min-width:0;" readonly value="${esc(addr)}">
        <button id="btnCopyAddr" class="bass-btn bass-btn--sm" type="button">📋 복사</button>
      </div>
      <button id="btnOpenIros" class="bass-btn bass-btn--primary" style="margin-top:8px;width:100%;" type="button">🏛️ 주소 복사하고 법원등기소 열기 ↗</button>
      <div id="irosMsg" class="bass-muted" style="font-size:12px;margin-top:6px;min-height:16px;">
        새 창이 열리지 않으면 주소창에 <strong>iros.go.kr</strong> 을 직접 입력하세요.
      </div>
      <details style="margin-top:6px;font-size:12.5px;">
        <summary style="cursor:pointer;font-weight:700;color:#1e3a8a;">📄 PDF 저장 방법 보기 (PC · 스마트폰)</summary>
        ${pdfGuideStepsHtml()}
      </details>`;
  }

  // 복사는 사용자의 터치 동작 안에서 '동기'로 시작해야 한다 (일부 휴대폰 브라우저는 await 뒤에 새 창·복사를 막는다).
  function copyNow(input) {
    try {
      input.focus();
      input.select();
      input.setSelectionRange(0, input.value.length);
      if (document.execCommand("copy")) return true;
    } catch (_) { /* 아래 방법으로 */ }
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(input.value).then(() => true, () => false);
    }
    return false;
  }

  function bindIrosBox(root = document) {
    const q = (id) => (root && root.querySelector ? root.querySelector("#" + id) : document.getElementById(id));
    const msg = q("irosMsg");
    const input = q("irosAddr");
    const say = (t) => { if (msg) msg.innerHTML = t; };
    const btnCopy = q("btnCopyAddr");
    const btnOpen = q("btnOpenIros");

    if (btnCopy && input) {
      btnCopy.addEventListener("click", () => {
        Promise.resolve(copyNow(input)).then((ok) => say(
          ok ? "✅ 주소가 복사되었습니다. 등기소 검색창에 붙여 넣으세요."
             : "복사하지 못했습니다. 위 주소를 길게 눌러 직접 복사해 주세요."));
      });
    }

    if (btnOpen && input) {
      btnOpen.addEventListener("click", () => {
        const copied = copyNow(input);                    // 1) 먼저 복사 (동기)
        const w = window.open(irosTargetUrl(), "_blank"); // 2) 같은 터치 안에서 바로 열기
        if (w) { try { w.opener = null; } catch (_) { /* 무시 */ } }
        Promise.resolve(copied).then((ok) => {
          if (!w) {
            say("새 창이 차단되었습니다. 주소창에 <strong>iros.go.kr</strong> 을 직접 입력해 접속하세요." + (ok ? " (주소는 복사되어 있습니다)" : ""));
          } else {
            say(ok ? "✅ 주소가 복사되었습니다. 열린 등기소 창에서 '부동산 열람(700원)'을 고르고 붙여 넣으세요."
                   : "등기소를 열었습니다. 위 주소를 직접 복사해 입력하세요.");
          }
        });
      });
    }
  }

  // ── 계약 전 등기부등본 발급 받기 · AI 분석 원스톱 안내 모달 ──
  function openIrosGuideModal(s) {
    if (!s) return;
    const addr = searchAddress(s);
    const body = R3.openModal("📄 등기부등본 발급 받기 · AI 분석 안내", `
      <div style="font-size:13px;line-height:1.65;">
        <div style="padding:9px 11px;border-radius:10px;background:#fffbeb;border:1px solid #f59e0b;color:#78350f;font-size:12.5px;margin-bottom:10px;">
          <strong>⚠️ 임대인이 보낸 등기부는 위·변조 우려가 있습니다.</strong><br>
          반드시 <strong>임차인 본인이 직접 법원등기소에서 열람</strong>해 PDF(또는 촬영 사진)를 올리고 AI 분석과 원본을 함께 확인하세요.
        </div>
        <div style="padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:#f8fafc;margin-bottom:10px;">
          <div style="font-weight:800;margin-bottom:4px;">① 주소 복사 & 대법원 인터넷등기소 열기</div>
          <div class="bass-row" style="gap:6px;margin-top:6px;">
            <input id="irosAddr" class="bass-input" style="flex:1;min-width:0;" readonly value="${esc(addr)}">
            <button id="btnCopyAddr" class="bass-btn bass-btn--sm" type="button">📋 복사</button>
          </div>
          <button id="btnOpenIros" class="bass-btn bass-btn--primary" style="margin-top:8px;width:100%;min-height:44px;" type="button">🏛️ 주소 복사하고 법원등기소 열기 ↗</button>
          <div id="irosMsg" class="bass-muted" style="font-size:12px;margin-top:6px;min-height:16px;">열린 등기소 검색창에 복사된 주소를 붙여넣기 하세요.</div>
        </div>
        <div style="padding:10px 12px;border-radius:10px;border:1px solid var(--border);margin-bottom:10px;">
          <div style="font-weight:800;margin-bottom:4px;">② PDF 파일 저장 방법 (700원 열람 권장)</div>
          ${pdfGuideStepsHtml()}
        </div>
        ${currentRole === "tenant" ? `
          <div style="padding:10px 12px;border-radius:10px;border:1px solid #93c5fd;background:#eff6ff;">
            <div style="font-weight:800;color:#1e3a8a;margin-bottom:6px;">③ 저장한 등기부 PDF·사진 바로 AI 분석</div>
            <input type="file" id="gmRegFile" accept="application/pdf,image/*" class="bass-input" style="margin-bottom:6px;">
            <label style="display:flex;gap:6px;align-items:center;font-size:12.5px;margin-bottom:8px;cursor:pointer;">
              <input type="checkbox" id="gmRegConsent" checked> AI(Google Gemini) 등기부 분석 전송에 동의합니다 (참고용)
            </label>
            <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
              <button type="button" id="gmRegAnalyze" class="bass-btn bass-btn--primary" style="flex:1;min-height:42px;">🤖 올려서 AI 분석하기</button>
              <button type="button" id="gmOpenStep1" class="bass-btn" style="min-height:42px;">🔍 상세 대조 · 승인 모달</button>
            </div>
            <div id="gmRegOut" style="margin-top:8px;"></div>
          </div>` : `
          <div class="bass-muted" style="font-size:12.5px;">임차인이 위 절차로 직접 열람·AI 분석을 진행하면 실시간으로 확인 상태가 반영됩니다.</div>`}
      </div>
    `);
    bindIrosBox(body);
    const btnStep1 = body.querySelector("#gmOpenStep1");
    if (btnStep1) btnStep1.addEventListener("click", () => { R3.closeModal(); C.openStepModal(1, { forceDoc: true }); });
    const btnAn = body.querySelector("#gmRegAnalyze");
    if (btnAn) {
      btnAn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const f = body.querySelector("#gmRegFile").files[0];
          if (!f) { Bass.updateAppAlert("warning", "등기부 PDF 또는 사진 파일을 먼저 골라 주세요."); return; }
          if (f.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다."); return; }
          const dataUrl = await new Promise((resolve, reject) => {
            const fr = new FileReader();
            fr.onload = () => resolve(fr.result);
            fr.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
            fr.readAsDataURL(f);
          });
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/registry-ai`, {
            method: "POST",
            body: { attachment_data_url: dataUrl, consent: body.querySelector("#gmRegConsent").checked },
          });
          if (res.session) S.activeSession = res.session;
          const out = body.querySelector("#gmRegOut");
          if (out) {
            out.innerHTML = aiResultHtml(res.result) || `<div class="bass-muted" style="font-size:12.5px;">${esc(res.message)}</div>`;
          }
          Bass.updateAppAlert(res.level === "success" ? "success" : "warning", res.message);
          C.renderWorkspace();
        });
      });
    }
  }

  function isJeonseSession(s) {
    const t = s.terms || {};
    return t.lease_type === "전세" || Number(t.monthly_rent || 0) === 0;
  }

  // ── 등기부 위험 정책 (서버 cosign.py 와 같은 기준. 화면은 미리 알려 주는 용도이고 최종 판단은 서버) ──
  // ① ② 단계의 진행 표시도 대기창·③과 같이 '점 두 개'(임대인·임차인, 초록=완료·주황=대기)로 통일한다
  function pairDots(title, lessorDone, tenantDone) {
    const d = (role, done) => `<span class="dotline__item"><span class="dot ${done ? "dot--ok" : "dot--wait"}"></span>${role === "lessor" ? "임대인" : "임차인"}${role === currentRole ? " (나)" : ""}</span>`;
    return `<div style="margin:6px 0;"><div class="bass-muted" style="font-size:12px;">${title}</div><div class="dotline" style="margin:2px 0 0;">${d("lessor", lessorDone)}${d("tenant", tenantDone)}</div></div>`;
  }

  const HIGH_DEPOSIT = 50000000;   // 초과: 선순위 근저당이 있으면 보증보험 가입 완료 또는 말소 특약 필요
  const LOW_DEPOSIT = 20000000;    // 이하 월세: 근저당이 있으면 강한 위험 확인 필요
  const normName = (v) => String(v || "").replace(/\([^)]*\)/g, "").replace(/\s+/g, "");
  function needsGuarantee(s) { return isJeonseSession(s) || Number((s.terms || {}).deposit || 0) > HIGH_DEPOSIT; }
  function isLowDepositMonthly(s) { return !isJeonseSession(s) && Number((s.terms || {}).deposit || 0) <= LOW_DEPOSIT; }
  function mortgageExposure(s, d) {
    const mine = d && d.mortgage_amount !== "" && d.mortgage_amount != null ? d.mortgage_amount : (s.tenant_registry || {}).mortgage_amount;
    return Math.max(Number((s.registry_data || {}).mortgage_amount || 0), Number(mine || 0));
  }
  function hardBlocks(s, tr) {   // 확인 체크로도 넘어갈 수 없는 항목 → 제출하면 계약이 즉시 중단된다
    const out = [];
    const lr = s.registry_data || {};
    if (lr.owner_name && tr.owner_name && normName(lr.owner_name) !== normName(tr.owner_name)) out.push(`등기부 소유자(${tr.owner_name})가 임대인이 제시한 소유자(${lr.owner_name})와 다릅니다`);
    if (tr.seizure_or_trust === "있음") out.push("압류·가압류·가처분·신탁·경매개시결정·임차권등기가 등기부에 있습니다");
    return out;
  }
  function riskBlock(s) {        // 서명 전 진행 차단 사유 (전세·고액 + 선순위 근저당 + 보증보험도 말소 특약도 없음)
    const tr = s.tenant_registry || {};
    if (!tr.submitted || !needsGuarantee(s) || mortgageExposure(s) <= 0) return "";
    const insured = tr.guarantee_status === "가입 완료" || tr.guarantee_status === "가입 예정";
    if (insured || (s.terms || {}).mortgage_cleanup_clause) return "";
    return "선순위 근저당이 있는 전세·고액 보증금(5천만 원 초과) 계약은 보증보험 '가입 완료·가입 예정' 또는 '잔금 시 근저당 말소 특약'이 있어야 진행할 수 있습니다. ('가입 안 함'으로는 진행할 수 없습니다)";
  }

  function nowLocalInput() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  }

  function registryMismatches(s, tr) {
    const lr = s.registry_data || {};
    const out = [];
    const norm = (v) => String(v || "").replace(/\([^)]*\)/g, "").replace(/\s+/g, "");
    if (lr.owner_name && tr.owner_name && norm(lr.owner_name) !== norm(tr.owner_name)) {
      out.push(`소유자가 다릅니다 — 임대인 제시: ${lr.owner_name} / 내가 열람: ${tr.owner_name}`);
    }
    if (tr.mortgage_amount !== "" && Number(lr.mortgage_amount || 0) !== Number(tr.mortgage_amount || 0)) {
      out.push(`근저당 채권최고액이 다릅니다 — 임대인 제시: ${fmtWon(lr.mortgage_amount)} / 내가 열람: ${fmtWon(tr.mortgage_amount)}`);
    }
    if (lr.issue_number && tr.issue_number && norm(lr.issue_number) !== norm(tr.issue_number)) {
      out.push("발급확인번호가 다릅니다");
    }
    if (tr.seizure_or_trust === "있음") out.push("압류·가압류·가처분·신탁등기가 있습니다");
    return out;
  }

  const RISK_STYLE = {
    high: { icon: "🚨", color: "var(--err)" },
    caution: { icon: "⚠️", color: "var(--warn)" },
    info: { icon: "ℹ️", color: "var(--ok)" },
  };

  function aiResultHtml(res) {
    if (!res || res.status !== "ok" || !res.extracted) return "";
    const e = res.extracted;
    return `
      <div style="margin-top:10px;">
        ${res.plain_summary ? `<div class="cg-ai-summary" style="margin:0 0 8px;white-space:pre-line;"><strong>🤖 AI 쉬운 요약</strong>\n${esc(res.plain_summary)}</div>` : ""}
        <div style="font-size:12.5px;line-height:1.7;">
          <strong>AI가 읽은 값</strong> (틀릴 수 있어요)<br>
          소유자: ${esc(e.owner_name || "-")}${e.owners.length > 1 ? ` (공동 ${e.owners.length}명)` : ""}<br>
          유효한 근저당: ${e.mortgage_count}건 · 채권최고액 합계 ${fmtWon(e.mortgage_total)}<br>
          압류·가압류·가처분·신탁: ${esc(e.seizure_or_trust)}<br>
          발급확인번호: ${esc(e.issue_number || "-")}
        </div>
        <div style="margin-top:8px;display:grid;gap:6px;">
          ${(res.risks || []).map((r) => {
            const st = RISK_STYLE[r.level] || RISK_STYLE.info;
            return `<div style="border-left:4px solid ${st.color};padding:6px 10px;font-size:12.5px;line-height:1.55;background:#f8fafc;color:#1e293b;border-radius:8px;">${st.icon} ${esc(r.text)}</div>`;
          }).join("")}
        </div>
        ${e.notes ? `<div class="bass-muted" style="font-size:12px;margin-top:6px;">AI 메모: ${esc(e.notes)}</div>` : ""}
        <div class="bass-muted" style="font-size:12.5px;margin-top:6px;">※ 위험 신호의 기준(담보 비율 등)은 참고용입니다. AI는 서류의 진위를 가리지 못하니, 인터넷등기소 원본과 직접 대조하세요.</div>
      </div>`;
  }

  function buildRegistryComparisonSheetHtml(s) {
    const lr = s.registry_data || {};
    const tr = s.tenant_registry || {};
    const addr = searchAddress(s);
    const ownerTarget = lr.owner_name || s.lessor_name || "-";
    const ownerMatch = Boolean(tr.submitted && tr.owner_name && normName(tr.owner_name) === normName(ownerTarget));
    return `
      <div class="cm-doc-scroll-box" role="region" aria-label="등기부등본 권리관계 대조표 (좌우상하 스크롤)">
        <div class="cm-std-sheet">
          <div class="cm-std-sheet__head">
            <div>
              <div class="cm-std-sheet__title">🏛️ 부동산 등기부등본 권리관계 확인표</div>
              <div class="cm-std-sheet__sub">소재지: ${esc(addr)} · 임대인 ${esc(s.lessor_name || "-")} / 임차인 ${esc((s.terms && s.terms.tenant_name) || "-")}</div>
            </div>
            <span class="bass-badge ${s.step1_tenant_locked ? "bass-badge--ok" : "bass-badge--warn"}">${s.step1_tenant_locked ? "임차인 열람 확인 완료 ✓" : "열람·대조 필요"}</span>
          </div>
          <table class="cm-std-table">
            <tr>
              <th>확인 항목</th>
              <th>임대인 제시 등기 정보</th>
              <th>임차인 법원등기소 직접 열람</th>
              <th>확인 결과</th>
            </tr>
            <tr>
              <th>1. 갑구 소유자<br>(등기인 = 임대인 명의)</th>
              <td><strong>${esc(ownerTarget)}</strong></td>
              <td>${tr.submitted ? `<strong>${esc(tr.owner_name || "-")}</strong>` : "입력 대기"}</td>
              <td>${ownerMatch ? "✅ 명의 일치" : (tr.submitted ? "⚠️ 확인 필요" : "⏳ 대기")}</td>
            </tr>
            <tr>
              <th>2. 을구 근저당<br>(채권최고액)</th>
              <td><strong>${fmtWon(lr.mortgage_amount)}</strong></td>
              <td>${tr.submitted ? `<strong>${fmtWon(tr.mortgage_amount)}</strong>` : "입력 대기"}</td>
              <td>${tr.submitted ? "✅ 열람 확인" : "⏳ 대기"}</td>
            </tr>
            <tr>
              <th>3. 압류·가압류·신탁<br>(위험 권리 여부)</th>
              <td>${esc(lr.seizure_or_trust || "없음")}</td>
              <td>${tr.submitted ? `<strong>${esc(tr.seizure_or_trust || "-")}</strong>` : "입력 대기"}</td>
              <td>${tr.submitted ? (tr.seizure_or_trust === "없음" ? "✅ 없음 확인" : "🛑 위험 권리 있음") : "⏳ 대기"}</td>
            </tr>
            <tr>
              <th>발급확인번호 / 열람일시</th>
              <td>${esc(lr.issue_number || "-")}</td>
              <td>${tr.submitted ? `${esc(tr.issue_number || "-")} · ${esc(String(tr.viewed_at || "-").replace("T", " "))}` : "입력 대기"}</td>
              <td>${tr.submitted ? "✅ 기록됨" : "⏳ 대기"}</td>
            </tr>
          </table>
        </div>
      </div>`;
  }

  function renderStep1Tenant(s) {
    const lr = s.registry_data || {};
    const done = s.tenant_registry || {};
    const aiRes = s.registry_ai_result || {};
    const aiEx = (!done.submitted && aiRes.extracted) ? aiRes.extracted : null;
    const jeonse = needsGuarantee(s);
    const ownerTarget = lr.owner_name || s.lessor_name || "";

    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 8px;">① 신분증 확인 · 법원등기소 등기부등본 직접 확인</h3>
      ${s.step1_tenant_locked && s.tenant_checked_lessor_id ? `<div class="bass-card" style="padding:10px 12px;margin-bottom:10px;border-color:var(--ok);background:#f0fdf4;font-size:13px;">✅ 임차인 신분 확인 및 등기부등본 열람 확인을 완료했습니다.</div>` : ""}

      ${buildRegistryComparisonSheetHtml(s)}

      <div class="call-banner" style="margin-bottom:12px;">
        <div style="font-weight:800;margin-bottom:4px;">⚠️ 임대인이 보여준 사진·PDF만 믿지 마세요</div>
        <div style="font-size:12.5px;line-height:1.55;">
          서류는 위조되거나, 진본이어도 발급 뒤에 바뀌었을 수 있습니다.
          <strong>인터넷등기소(법원등기소)에서 직접</strong> 아래 주소로 열람한 결과를 입력하고 1~3 항목을 체크해 주세요. 열람은 소유자 동의 없이 누구나 할 수 있습니다.
        </div>
        ${irosBoxHtml(searchAddress(s))}
      </div>

      <div class="id-card-box ${s.tenant_checked_lessor_id ? "is-verified" : ""}" style="margin-bottom:12px;">
        <strong>🪪 임대인 신분증 확인</strong>
        <div style="font-size:13px;margin:4px 0 8px;">성명: <strong>${esc(s.lessor_name)}</strong> · 연락처: ${esc(s.lessor_phone || "-")}</div>
        <label class="check-row" style="margin-bottom:0;">
          <span style="font-size:12.5px;">임차인이 임대인 신분 확인 (실물 신분증을 보고 본인임을 확인함)</span>
          <input type="checkbox" id="chkTenantVerifiedLessorId" ${s.tenant_checked_lessor_id ? "checked" : ""}>
        </label>
      </div>

      <div class="bass-card" style="padding:12px;margin-bottom:12px;">
        <div style="font-weight:800;margin-bottom:6px;">🏢 임대인이 제시한 등기 정보 <span class="bass-muted" style="font-weight:400;">(참고용)</span></div>
        <div style="font-size:12.5px;line-height:1.7;">
          발급확인번호: ${esc(lr.issue_number || "-")}<br>
          갑구 소유자: ${esc(lr.owner_name || "-")}<br>
          을구 채권최고액: ${fmtWon(lr.mortgage_amount)}
        </div>
      </div>

      <div style="font-weight:800;margin-bottom:6px;">🔎 내가 직접 열람한 결과 (1~3 체크)</div>
      <div class="field-grid" style="margin:8px 0;">
        <div><label class="bass-label">열람 일시 <span class="bass-muted" style="font-weight:400;">(3일 이내 열람본만 인정${aiEx && aiEx.viewed_at ? " · 올린 파일: " + esc(aiEx.viewed_at.replace("T", " ")) : ""})</span></label><input id="trAt" type="datetime-local" class="bass-input" value="${esc(done.viewed_at || (aiEx && aiEx.viewed_at) || nowLocalInput())}"></div>
        <div><label class="bass-label">1. 갑구 소유자 성명 (등기인 = 임대인 명의)</label><input id="trOwner" class="bass-input" value="${esc(done.owner_name || (aiEx ? aiEx.owner_name : ""))}" placeholder="등기부에 적힌 이름"></div>
        <div><label class="bass-label">2. 을구 근저당 채권최고액(원)</label><input id="trMort" type="number" min="0" class="bass-input" value="${done.submitted ? Number(done.mortgage_amount || 0) : (aiEx ? aiEx.mortgage_total : "")}" placeholder="없으면 0"></div>
        <div><label class="bass-label">3. 압류·가압류·가처분·신탁·경매개시·임차권등기</label>
          <select id="trSeize" class="bass-select">
            <option value="">선택</option>
            <option value="없음" ${(done.seizure_or_trust || (aiEx && aiEx.seizure_or_trust)) === "없음" ? "selected" : ""}>없음</option>
            <option value="있음" ${(done.seizure_or_trust || (aiEx && aiEx.seizure_or_trust)) === "있음" ? "selected" : ""}>있음</option>
          </select>
        </div>
      </div>
      <div class="field-grid" style="margin:8px 0;">
        <div><label class="bass-label">발급확인번호 (선택)</label><input id="trIssue" class="bass-input" value="${esc(done.issue_number || (aiEx ? aiEx.issue_number : ""))}" placeholder="등기부 하단 번호"></div>
      </div>
      <label class="check-row">
        <span style="font-size:12.5px;">임대인이 준 서류의 발급확인번호를 인터넷등기소 '발급확인'으로 대조해 일치함 (선택)</span>
        <input type="checkbox" id="trIssueOk" ${done.issue_number_verified ? "checked" : ""}>
      </label>

      <div id="trMismatchBox"></div>

      <div style="margin:10px 0;">
        <label class="bass-label">열람 결과 첨부 — PDF 또는 캡처 (선택, 10MB 이하)${s.registry_attachment_file ? " · 이미 첨부됨" : ""}</label>
        <input id="trFile" type="file" accept="image/*,application/pdf" class="bass-input">
      </div>

      <div class="bass-card" style="padding:12px;margin:10px 0;">
        <div style="font-weight:800;">🤖 AI로 등기부 읽기 <span class="bass-muted" style="font-weight:400;">(선택 · 참고용)</span></div>
        <div class="bass-muted" style="font-size:12.5px;margin:4px 0 8px;line-height:1.55;">
          인터넷등기소에서 <strong>직접 받은</strong> PDF나 캡처를 위에서 고른 뒤 누르면, AI(Google Gemini)가 소유자·근저당·압류 여부를 읽어 아래 칸을 채우고 위험 신호를 알려 줍니다.
        </div>
        <label class="check-row">
          <span style="font-size:12.5px;">등기부 내용(소유자 이름 등)이 Google Gemini로 전송되는 것에 동의합니다. 전송한 파일은 분석에만 쓰고 서버에 저장하지 않습니다.</span>
          <input type="checkbox" id="aiConsent">
        </label>
        <button id="btnAiRead" class="bass-btn" style="width:100%;" type="button">🤖 위에서 고른 파일을 AI로 읽기</button>
        <div id="aiResult">${aiResultHtml(aiRes)}</div>
      </div>

      ${jeonse ? `
        <div class="bass-card" style="padding:12px;margin-bottom:10px;border-color:var(--warn);">
          <div style="font-weight:800;margin-bottom:4px;">🛡️ 전세·고액 보증금 · 보증금 반환보증</div>
          <div style="font-size:12.5px;line-height:1.6;margin-bottom:6px;color:var(--err);">선순위 근저당이 있으면 <strong>'가입 완료'·'가입 예정'</strong>이거나 임대인이 <strong>'잔금 시 근저당 말소 특약'</strong>을 넣어야 계약을 진행할 수 있습니다. <strong>'가입 예정'</strong>을 고르면 <strong>"가입이 거절되면 계약 무효·계약금 전액 반환"</strong> 특약이 계약서에 자동으로 들어갑니다. ('가입 안 함'은 진행할 수 없습니다)</div>
          <div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">HUG·SGI 등 보증보험 가입이 가능한지 계약 전에 확인하세요. 가입이 안 되는 집은 보증금을 돌려받지 못할 위험이 큽니다.</div>
          <select id="trGuar" class="bass-select">
            <option value="">가입 여부 선택</option>
            ${["가입 완료", "가입 예정", "가입 안 함"].map((v) => `<option value="${v}" ${done.guarantee_status === v ? "selected" : ""}>${v}</option>`).join("")}
          </select>
        </div>` : ""}

      <div class="bass-card" style="padding:12px;margin:10px 0;border-color:#93c5fd;background:#eff6ff;">
        <div style="font-weight:800;margin-bottom:6px;color:#1e3a8a;">☑️ 등기부등본 필수 확인 체크</div>
        <label class="check-row" style="background:#ffffff;">
          <span style="font-size:12.5px;"><strong>1. 등기인 = 임대인 명의(${esc(ownerTarget || "-")}) 일치</strong>를 확인했습니다</span>
          <input type="checkbox" id="trChk1" ${done.submitted ? "checked" : ""}>
        </label>
        <label class="check-row" style="background:#ffffff;">
          <span style="font-size:12.5px;"><strong>2. 근저당 / 채권최고액</strong>을 확인했습니다</span>
          <input type="checkbox" id="trChk2" ${done.submitted ? "checked" : ""}>
        </label>
        <label class="check-row" style="background:#ffffff;">
          <span style="font-size:12.5px;"><strong>3. 압류·가압류·신탁 없음</strong>을 확인했습니다</span>
          <input type="checkbox" id="trChk3" ${done.submitted ? "checked" : ""}>
        </label>
        <label class="check-row" style="margin-bottom:0;background:#ecfdf5;border-color:#86efac;">
          <span style="font-size:13px;"><strong>임차인이 위 사실을 확인했음 (인터넷등기소에서 직접 열람)</strong></span>
          <input type="checkbox" id="trViewed" ${done.viewed_directly ? "checked" : ""}>
        </label>
      </div>

      <button id="btnStep1Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ✅ 임차인이 위 사실을 확인했음 · 승인 및 채팅으로 돌아가기
      </button>
    `;

    bindIrosBox();
    const $ = (id) => document.getElementById(id);
    D.elStepPanel.querySelectorAll("input, select").forEach((el) => {
      el.addEventListener("focus", () => { S.isEditingForm = true; });
      el.addEventListener("blur", () => { setTimeout(() => { S.isEditingForm = false; }, 400); });
    });

    function collect() {
      return {
        viewed_directly: $("trViewed").checked,
        viewed_at: $("trAt").value,
        owner_name: $("trOwner").value.trim(),
        mortgage_amount: $("trMort").value === "" ? "" : Number($("trMort").value),
        seizure_or_trust: $("trSeize").value,
        issue_number: $("trIssue").value.trim(),
        issue_number_verified: $("trIssueOk").checked,
        guarantee_status: jeonse ? $("trGuar").value : "",
      };
    }

    let ackChecked = false;
    let lowAck = false;
    function refreshMismatch() {
      const d = collect();
      const hard = hardBlocks(s, d);
      const box = $("trMismatchBox");
      if (hard.length) {
        ackChecked = false; lowAck = false;
        box.innerHTML = `
          <div class="call-banner" style="border:2px solid var(--err);background:hsl(0 80% 50% / .18);margin-bottom:10px;">
            <div style="font-weight:800;margin-bottom:4px;">🛑 이 계약은 진행할 수 없습니다</div>
            <ul style="margin:0 0 6px 18px;padding:0;font-size:12.5px;line-height:1.6;">${hard.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
            <div style="font-size:12.5px;line-height:1.6;">소유자 불일치, 압류·가압류·신탁·경매개시·임차권등기는 <strong>확인 체크로 넘어갈 수 없고, 제출하면 계약이 즉시 중단</strong>됩니다. 입력을 잘못한 것이 아니라면 제출하지 마세요.</div>
          </div>`;
        return;
      }
      const list = registryMismatches(s, d);
      const lowRisk = isLowDepositMonthly(s) && mortgageExposure(s, d) > 0;
      const lowHtml = lowRisk ? `
        <div class="call-banner" style="border:2px solid var(--err);background:hsl(0 80% 50% / .18);margin-bottom:10px;">
          <div style="font-weight:800;margin-bottom:4px;">🚨 강한 위험 경고 — 소액 보증금에 선순위 근저당이 있습니다</div>
          <div style="font-size:12.5px;line-height:1.6;margin-bottom:8px;">경매가 되면 근저당권자가 먼저 배당받아 <strong>보증금을 한 푼도 돌려받지 못할 수 있습니다.</strong> 위험을 이해한 경우에만 계속하세요.</div>
          <label class="check-row" style="margin-bottom:0;">
            <span style="font-size:12.5px;"><strong>소액 보증금 위험을 확인했고, 그래도 계속합니다</strong></span>
            <input type="checkbox" id="trLowAck" ${lowAck ? "checked" : ""}>
          </label>
        </div>` : "";
      if (!list.length) {
        ackChecked = false;
        if (!lowRisk) lowAck = false;
        box.innerHTML = lowHtml;
        const lc = $("trLowAck"); if (lc) lc.addEventListener("change", (e) => { lowAck = e.target.checked; });
        return;
      }
      box.innerHTML = lowHtml + `
        <div class="call-banner" style="border-color:var(--err);background:hsl(0 80% 50% / .12);margin-bottom:10px;">
          <div style="font-weight:800;margin-bottom:4px;">🚨 확인이 필요한 항목</div>
          <ul style="margin:0 0 8px 18px;padding:0;font-size:12.5px;line-height:1.6;">${list.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
          <label class="check-row" style="margin-bottom:0;">
            <span style="font-size:12.5px;"><strong>위 내용을 확인했고, 그래도 계약을 계속합니다</strong></span>
            <input type="checkbox" id="trAck" ${ackChecked ? "checked" : ""}>
          </label>
        </div>`;
      $("trAck").addEventListener("change", (e) => { ackChecked = e.target.checked; });
      const lc2 = $("trLowAck"); if (lc2) lc2.addEventListener("change", (e) => { lowAck = e.target.checked; });
    }
    ["trOwner", "trMort", "trSeize", "trIssue"].forEach((id) => {
      $(id).addEventListener("input", refreshMismatch);
      $(id).addEventListener("change", refreshMismatch);
    });
    refreshMismatch();

    function readFile(file) {
      return new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(fr.result);
        fr.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
        fr.readAsDataURL(file);
      });
    }

    $("btnAiRead").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const f = $("trFile").files[0];
        if (!f) { Bass.updateAppAlert("warning", "먼저 위의 '열람 결과 첨부'에서 PDF나 캡처 파일을 골라 주세요."); return; }
        if (f.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다."); return; }
        if (!$("aiConsent").checked) { Bass.updateAppAlert("warning", "Gemini 전송에 동의해야 AI로 읽을 수 있습니다."); return; }
        const dataUrl = await readFile(f);
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/registry-ai`, {
          method: "POST",
          body: { attachment_data_url: dataUrl, consent: true },
        });
        // 이 호출로 세션이 갱신되어도 입력 중인 폼이 지워지지 않게, 최신 상태로 맞춰 둔다.
        if (res.session) {
          S.activeSession = res.session;
          S.lastStamp = `${res.session.updated_at || ""}:${res.session.revision}:${S.stepModalOpen}:${S.chatSummary.open_count}`;
        }
        $("aiResult").innerHTML = aiResultHtml(res.result) ||
          `<div class="bass-muted" style="font-size:12.5px;margin-top:8px;">${esc(res.message)}</div>`;
        const ex = res.result && res.result.extracted;
        if (ex) {
          $("trOwner").value = ex.owner_name || "";
          $("trMort").value = ex.mortgage_total;
          $("trSeize").value = ex.seizure_or_trust;
          if (ex.issue_number) $("trIssue").value = ex.issue_number;
          ["trOwner", "trMort", "trSeize", "trIssue"].forEach((id) => $(id).dispatchEvent(new Event("input", { bubbles: true })));
        }
        Bass.updateAppAlert(res.level === "success" ? "success" : "warning", res.message,
          ex ? `AI 읽기 남은 횟수: ${res.calls_left}회` : "");
      });
    });

    $("btnStep1Confirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        if (!$("chkTenantVerifiedLessorId").checked) {
          Bass.updateAppAlert("warning", "임대인의 신분증을 확인한 뒤 체크해 주세요.");
          return;
        }
        if (!$("trChk1").checked || !$("trChk2").checked || !$("trChk3").checked) {
          Bass.updateAppAlert("warning", "등기부등본 1~3 확인 항목(소유자 일치·근저당·압류/신탁 없음)을 모두 체크해 주세요.");
          return;
        }
        const reg = collect();
        reg.mismatch_ack = ackChecked;
        reg.low_deposit_risk_ack = lowAck;
        const hardNow = hardBlocks(s, reg);
        if (hardNow.length && !window.confirm("🛑 " + hardNow.join("\n") + "\n\n제출하면 이 계약은 즉시 중단되고 되돌릴 수 없습니다. 정말 제출할까요?")) return;
        let dataUrl = "";
        const f = $("trFile").files[0];
        if (f) {
          if (f.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "첨부 파일은 10MB 이하만 가능합니다."); return; }
          dataUrl = await readFile(f);
        }
        await Bass.api(APP_ID, `/sessions/${s.id}/tenant-registry`, {
          method: "POST",
          body: { registry: reg, attachment_data_url: dataUrl, attachment_name: f ? f.name : "" },
        });
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step1`, {
          method: "POST",
          body: { role: "tenant", checked_counterparty_id: true },
        });
        S.activeSession = res.session;
        S.viewStep = S.activeSession.current_step;
        S.isEditingForm = false;
        if (C.closeStepModal) C.closeStepModal();
        Bass.updateAppAlert("success", res.message);
        C.renderWorkspace();
      });
    });
  }

  // ───────────── 체결 직전 재열람 (임차인) ─────────────
  function renderRecheckTenant(s) {
    const tr = s.tenant_registry || {};
    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 8px;">⑤ 체결 직전 마지막 확인 — 등기부 다시 열람</h3>
      <div class="call-banner" style="margin-bottom:12px;">
        <div style="font-size:12.5px;line-height:1.6;">
          처음 열람한 뒤 <strong>근저당이 새로 잡히거나 소유자가 바뀌는 사고</strong>가 가장 흔합니다.
          체결 전에 인터넷등기소에서 한 번 더 열람해 처음 확인한 내용과 같은지 보세요.
        </div>
        <div style="font-size:12.5px;margin-top:4px;">처음 확인한 내용: 소유자 ${esc(tr.owner_name || "-")} · 채권최고액 ${fmtWon(tr.mortgage_amount)} · 압류/신탁 ${esc(tr.seizure_or_trust || "-")}</div>
        ${irosBoxHtml(searchAddress(s))}
      </div>
      <button id="btnRecheckOk" class="bass-btn bass-btn--primary" style="width:100%;margin-bottom:8px;" type="button">✅ 다시 열람했고, 변동 없음을 확인했습니다</button>
      <button id="btnRecheckChanged" class="bass-btn" style="width:100%;border-color:var(--err);color:var(--err);" type="button">🚨 변동이 있습니다 — 계약 중단</button>
    `;
    bindIrosBox();
    const send = (btn, changed) => Bass.withLock(btn, async () => {
      if (changed && !window.confirm("등기부에 변동이 있어 계약을 중단합니다. 되돌릴 수 없습니다. 계속할까요?")) return;
      const res = await Bass.api(APP_ID, `/sessions/${s.id}/recheck`, { method: "POST", body: { changed } });
      S.activeSession = res.session;
      Bass.updateAppAlert(changed ? "warning" : "success", res.message);
      C.renderWorkspace();
    });
    document.getElementById("btnRecheckOk").addEventListener("click", function () { send(this, false); });
    document.getElementById("btnRecheckChanged").addEventListener("click", function () { send(this, true); });
  }

  function renderCancelled(s) {
    const why = s.cancel_reason || ((s.tenant_registry || {}).recheck_changed
      ? "임차인이 체결 직전 등기부에서 변동을 확인해 계약이 중단되었습니다."
      : "이 계약은 중단되었습니다.");
    D.elStepPanel.innerHTML = `
      <h3 style="margin:0 0 6px;">⛔ 계약이 중단되었습니다</h3>
      <p class="bass-muted" style="font-size:13px;margin:0 0 12px;">${esc(why)} 필요하면 홈에서 새로 계약을 시작할 수 있습니다.</p>
      <button id="btnCancelledHome" class="bass-btn bass-btn--primary" style="width:100%;" type="button">← 홈으로</button>`;
    document.getElementById("btnCancelledHome").addEventListener("click", C.goHome);
  }

  function renderStep1(s) {
    if (currentRole === "tenant") { renderStep1Tenant(s); return; }
    const r = s.registry_data || {};
    const step1AllDone =
      s.lessor_login_verified &&
      s.tenant_login_verified &&
      s.lessor_checked_tenant_id &&
      s.tenant_checked_lessor_id &&
      s.step1_lessor_confirmed &&
      s.step1_tenant_locked;

    D.elStepPanel.innerHTML = `
      <h3>① 로그인 본인인증 · 상호 신분증 확인 체크 & 등기부등본 확인</h3>
      ${buildRegistryComparisonSheetHtml(s)}
      <div class="bass-card" style="padding:10px 12px;margin:8px 0;font-size:12.5px;border-color:var(--warn);">🔎 임차인이 <strong>인터넷등기소에서 등기부를 직접 열람</strong>해 아래 내용과 대조합니다. 실제 등기와 다르게 입력하면 계약이 진행되지 않을 수 있으니 열람한 그대로 입력해 주세요.</div>
      <p class="bass-muted" style="font-size:13px;">
        임대인과 임차인은 로그인 본인인증을 완료했으며, <strong>서로 상대방의 신분증을 확인하여 본인 확인 체크</strong>를 완료하고 <strong>등기부등본</strong>을 확인해야 서명 자격이 부여됩니다.
      </p>

      <div style="margin-top:12px; font-weight:800; font-size:14px;">🪪 1-A. 당사자 로그인 본인인증 및 상호 신분증 대조 확인 체크</div>
      <div class="id-verify-grid">
        <div class="id-card-box ${s.tenant_checked_lessor_id ? "is-verified" : ""}">
          <div class="bass-row" style="justify-content:space-between; margin-bottom:6px;">
            <strong>🏢 임대인(갑) 신분 정보</strong>
            <span class="bass-badge ${s.lessor_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.lessor_login_verified ? "🛡️ 로그인 본인인증 완료" : "미인증"}
            </span>
          </div>
          <div style="font-size:13px; margin-bottom:8px;">
            성명: <strong>${esc(s.lessor_name)}</strong> · 연락처: ${esc(s.lessor_phone || "-")}
            <div class="bass-muted" style="font-size:12.5px;">등기부 소유자 대조: ${esc(r.owner_name || s.lessor_name)} (일치)</div>
          </div>
          <label class="check-row" style="margin-bottom:0; cursor:${currentRole === "tenant" ? "pointer" : "default"};">
            <span style="font-size:12.5px;">
              <strong>[임차인이 체크]</strong> 임대인의 신분증을 확인하여 본인임을 확인함
            </span>
            <input type="checkbox" id="chkTenantVerifiedLessorId"
              ${s.tenant_checked_lessor_id ? "checked" : ""}
              ${currentRole !== "tenant" ? "disabled" : ""}>
          </label>
        </div>

        <div class="id-card-box ${s.lessor_checked_tenant_id ? "is-verified" : ""}">
          <div class="bass-row" style="justify-content:space-between; margin-bottom:6px;">
            <strong>🔑 임차인(을) 신분 정보</strong>
            <span class="bass-badge ${s.tenant_login_verified ? "bass-badge--ok" : "bass-badge--warn"}">
              ${s.tenant_login_verified ? "🛡️ 로그인 본인인증 완료" : "미인증"}
            </span>
          </div>
          <div style="font-size:13px; margin-bottom:8px;">
            성명: <strong>${esc((s.terms && s.terms.tenant_name) || "-")}</strong> · 연락처: ${esc((s.terms && s.terms.tenant_phone) || "-")}
            <div class="bass-muted" style="font-size:12.5px;">로그인 본인인증 휴대폰 명의 일치 확인됨</div>
          </div>
          <label class="check-row" style="margin-bottom:0; cursor:${currentRole === "lessor" ? "pointer" : "default"};">
            <span style="font-size:12.5px;">
              <strong>[임대인이 체크]</strong> 임차인의 신분증을 확인하여 본인임을 확인함
            </span>
            <input type="checkbox" id="chkLessorVerifiedTenantId"
              ${s.lessor_checked_tenant_id ? "checked" : ""}
              ${currentRole !== "lessor" ? "disabled" : ""}>
          </label>
        </div>
      </div>

      <div style="margin-top:14px; font-weight:800; font-size:14px;">🏛️ 1-B. 대법원 등기부등본 권리관계 확인</div>
      <div class="field-grid" style="margin:10px 0;">
        <div>
          <label class="bass-label">등기부 발급확인번호</label>
          <input id="regIssue" class="bass-input" value="${esc(r.issue_number || "")}" ${currentRole !== "lessor" ? "readonly" : ""}>
        </div>
        <div>
          <label class="bass-label">갑구 소유자 성명</label>
          <input id="regOwner" class="bass-input" value="${esc(r.owner_name || "")}" ${currentRole !== "lessor" ? "readonly" : ""}>
        </div>
        <div>
          <label class="bass-label">을구 선순위 채권최고액(원)</label>
          <input id="regMortgage" type="number" class="bass-input" value="${Number(r.mortgage_amount || 0)}" ${currentRole !== "lessor" ? "readonly" : ""}>
        </div>
      </div>
      <div class="bass-muted" style="font-size:12px; margin-bottom:10px;">
        압류·가압류·신탁등기 여부: <strong>${esc(r.seizure_or_trust || "없음")}</strong>
      </div>

      <div class="bass-row" style="margin-bottom:12px; flex-wrap:wrap; gap:6px;">
        ${pairDots("서로 신분증 확인 (상대방 신분증을 확인했는지)", s.lessor_checked_tenant_id, s.tenant_checked_lessor_id)}
        ${pairDots("등기부 확인 (임대인 제시 · 임차인 직접 열람)", s.step1_lessor_confirmed, s.step1_tenant_locked)}
      </div>

      ${step1AllDone ? `
        <div class="bass-card" style="padding:10px 12px; margin-bottom:10px; border-color:var(--ok); font-size:12.5px;">
          ✅ 양측 로그인 본인인증 + 서로 신분증 확인 체크 + 등기부등본 권리 확인이 모두 완료되었습니다!
        </div>
      ` : ""}

      <button id="btnStep1Confirm" class="bass-btn bass-btn--primary" style="width:100%;" type="button">
        ${currentRole === "lessor"
          ? "🏢 임차인 신분증 확인 체크 & 등기부등본 제시 저장"
          : "🔑 임대인 신분증 확인 체크 & 등기부등본 이상 없음 확인 잠금"}
      </button>
    `;

    const chkTenant = document.getElementById("chkTenantVerifiedLessorId");
    if (chkTenant && currentRole === "tenant") {
      chkTenant.addEventListener("change", async () => {
        try {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/verify-id`, {
            method: "POST",
            body: { role: "tenant", checked: chkTenant.checked },
          });
          S.activeSession = res.session;
          S.viewStep = S.activeSession.current_step;
          Bass.updateAppAlert("success", res.message);
          C.renderWorkspace();
        } catch (err) {
          Bass.showError(err);
        }
      });
    }

    const chkLessor = document.getElementById("chkLessorVerifiedTenantId");
    if (chkLessor && currentRole === "lessor") {
      chkLessor.addEventListener("change", async () => {
        try {
          const res = await Bass.api(APP_ID, `/sessions/${s.id}/verify-id`, {
            method: "POST",
            body: { role: "lessor", checked: chkLessor.checked },
          });
          S.activeSession = res.session;
          S.viewStep = S.activeSession.current_step;
          Bass.updateAppAlert("success", res.message);
          C.renderWorkspace();
        } catch (err) {
          Bass.showError(err);
        }
      });
    }

    document.getElementById("btnStep1Confirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const myIdChk = currentRole === "lessor"
          ? document.getElementById("chkLessorVerifiedTenantId").checked
          : document.getElementById("chkTenantVerifiedLessorId").checked;
        const body = {
          role: currentRole,
          checked_counterparty_id: myIdChk,
        };
        if (currentRole === "lessor") {
          body.registry_updates = {
            issue_number: document.getElementById("regIssue").value.trim(),
            owner_name: document.getElementById("regOwner").value.trim(),
            mortgage_amount: Number(document.getElementById("regMortgage").value || 0),
          };
        }
        const res = await Bass.api(APP_ID, `/sessions/${s.id}/step1`, { method: "POST", body });
        S.activeSession = res.session;
        S.viewStep = S.activeSession.current_step;
        if (C.closeStepModal) C.closeStepModal();
        Bass.updateAppAlert("success", res.message);
        C.renderWorkspace();
      });
    });
  }

  // ── 입주(잔금) 전 등기부 재확인: 계약 체결 뒤 출입 정보를 받기 전까지, 임차인이 모달에서 ①등기소 열기 ②올리기 ③AI 비교 ④확인했음 체크 ──
  function balRegState() {
    const ct = S.sealedContract || {};
    const br = ct.balance_registry || {};
    return {
      show: currentRole === "tenant" && ct.keys_released === false,
      br,
      read: br.read || {},
      done: !!br.checked_at && !br.changed,
      changed: !!br.checked_at && !!br.changed,
    };
  }

  function balRegCardHtml() {
    const st = balRegState();
    if (!st.show) return "";
    const line = st.done ? `<span style="color:var(--ok);font-weight:700;">✅ 입주 전 등기부 확인 완료 (${esc(String(st.br.checked_at).slice(0, 16).replace("T", " "))})</span>`
      : st.changed ? `<span style="color:var(--err);font-weight:700;">⚠️ 변동을 신고했습니다. 해소되기 전에는 잔금을 보내지 마세요.</span>`
      : `<span style="color:var(--err);font-weight:700;">아직 확인하지 않았습니다.</span>`;
    return `<div class="bass-card" style="padding:12px 14px;margin:12px 0;border-left:4px solid var(--primary);font-size:13px;line-height:1.7;">
      <strong>🏛 입주 전 등기부 재확인</strong><br>
      계약 뒤 입주(잔금)까지 사이에 대출(근저당)이 새로 생긴 사례가 있습니다. <strong>잔금을 보내기 직전에</strong> 인터넷등기소에서 다시 열람해 확인하세요. 확인하지 않으면 출입 정보를 받을 수 없습니다.<br>
      ${line}
      <div style="margin-top:8px;"><button id="btnBalReg" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🏛 ${st.done ? "다시 확인하기" : "등기부 재확인 시작"}</button></div>
    </div>`;
  }

  async function openBalanceRegistryFile(sessionId) {
    const headers = {};
    const tk = Bass.token();
    if (tk) headers["Authorization"] = "Bearer " + tk;
    const resp = await fetch(`${Bass.apiBase(APP_ID)}/sessions/${encodeURIComponent(sessionId)}/balance-registry/attachment`, { headers });
    if (!resp.ok) throw new Error("서버에 보관된 등기부등본 파일을 불러올 수 없습니다.");
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function openBalRegModal(s) {
    const body = R3.openModal("🏛 입주 전 등기부 재확인", "");
    const E = Bass.escapeHtml;
    const addr = searchAddress(s);
    const draw = () => {
      const st = balRegState(), rd = st.read || {};
      const lb = rd.lessor_baseline || {};
      const allDiffs = [...new Set([...(rd.differences || []), ...(rd.lessor_differences || [])])];
      const savedHtml = rd.file
        ? `<div style="margin-top:8px;padding:7px 9px;border-radius:8px;background:#f0fdf4;border:1px solid #86efac;display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px;">
             <span>💾 <strong>서버 증빙 보관 완료</strong> (${E(String(rd.saved_at || rd.read_at || "").slice(0, 16).replace("T", " "))})</span>
             <button type="button" id="balViewSaved" class="bass-btn bass-btn--sm" style="min-height:32px;">📄 보관 원본 보기 ↗</button>
           </div>`
        : "";
      const cmpTableHtml = rd.status === "ok"
        ? `<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;">
             <thead>
               <tr style="background:#f8fafc;">
                 <th style="border:1px solid var(--border);padding:5px 6px;text-align:left;">항목</th>
                 <th style="border:1px solid var(--border);padding:5px 6px;text-align:left;">임대인 확인본 (계약 시)</th>
                 <th style="border:1px solid var(--border);padding:5px 6px;text-align:left;">잔금 시 임차인 직접 열람</th>
               </tr>
             </thead>
             <tbody>
               <tr>
                 <td style="border:1px solid var(--border);padding:5px 6px;font-weight:700;">갑구 소유자</td>
                 <td style="border:1px solid var(--border);padding:5px 6px;">${E(lb.owner_name || s.lessor_name || "-")}</td>
                 <td style="border:1px solid var(--border);padding:5px 6px;"><strong>${E(rd.owner_name || "-")}</strong></td>
               </tr>
               <tr>
                 <td style="border:1px solid var(--border);padding:5px 6px;font-weight:700;">근저당 합계</td>
                 <td style="border:1px solid var(--border);padding:5px 6px;">${fmtWon(lb.mortgage_amount || 0)}</td>
                 <td style="border:1px solid var(--border);padding:5px 6px;"><strong>${fmtWon(rd.mortgage_total || 0)}</strong></td>
               </tr>
               <tr>
                 <td style="border:1px solid var(--border);padding:5px 6px;font-weight:700;">압류 · 신탁</td>
                 <td style="border:1px solid var(--border);padding:5px 6px;">${E(lb.seizure_or_trust || "없음")}</td>
                 <td style="border:1px solid var(--border);padding:5px 6px;"><strong>${E(rd.seizure_or_trust || "없음")}</strong></td>
               </tr>
             </tbody>
           </table>`
        : "";
      const readHtml = !rd.status ? "" : rd.status === "unavailable"
        ? `<div style="margin:8px 0;font-size:12.5px;">⚠️ AI 를 쓸 수 없어 직접 확인으로 진행합니다. 인터넷등기소 원본을 직접 보고 아래에 체크해 주세요.${savedHtml}</div>`
        : `<div style="margin:8px 0;padding:10px 12px;border:1px solid var(--border);border-radius:10px;font-size:12.5px;line-height:1.7;">
            <strong>🤖 AI 분석 · 임대인 확인본 비교 결과</strong> — 열람 일시 <strong>${E((rd.viewed_at || "읽지 못함").replace("T", " "))}</strong> ${rd.recent ? "✅ 최근" : "<span style='color:var(--err);font-weight:700;'>⚠️ 오래됨·확인 불가 — 오늘 다시 열람하세요</span>"}
            ${cmpTableHtml}
            ${rd.plain_summary ? `<div class="bass-muted" style="margin-top:6px;white-space:pre-line;font-size:12px;">${E(rd.plain_summary)}</div>` : ""}
            ${allDiffs.length ? `<div style="color:var(--err);font-weight:700;margin-top:6px;">⚠️ 임대인 확인본·계약 때와 달라진 점:<br>${allDiffs.map((x) => "· " + E(x)).join("<br>")}<br>잔금을 보내지 말고 [변동 있음 신고]를 눌러 주세요.</div>` : `<div style="color:var(--ok);font-weight:700;margin-top:6px;">✅ 임대인 확인본 및 계약 시점과 달라진 점이 없습니다 (AI 분석 범위).</div>`}
            ${savedHtml}
          </div>`;
      body.innerHTML = `
        <div style="font-size:13px;line-height:1.7;">
          <div style="padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:#f8fafc;">
            <strong>① 인터넷등기소에서 오늘 다시 열람</strong>해 PDF로 저장하세요.
            <div class="bass-row" style="gap:6px;margin-top:6px;">
              <input id="irosAddr" class="bass-input" style="flex:1;min-width:0;" readonly value="${E(addr)}">
              <button id="btnCopyAddr" class="bass-btn bass-btn--sm" type="button">📋 복사</button>
            </div>
            <button id="btnOpenIros" class="bass-btn bass-btn--primary" type="button" style="margin-top:6px;width:100%;min-height:44px;">🏛️ 주소 복사하고 법원등기소 열기 ↗</button>
            <div id="irosMsg" class="bass-muted" style="font-size:12px;margin-top:4px;">열리지 않으면 <a href="https://www.iros.go.kr" target="_blank" rel="noopener">iros.go.kr</a> 로 직접 접속하세요.</div>
            <details style="margin-top:6px;font-size:12.5px;">
              <summary style="cursor:pointer;font-weight:700;color:#1e3a8a;">📄 PDF 저장 방법 보기 (PC · 스마트폰)</summary>
              ${pdfGuideStepsHtml()}
            </details>
          </div>
          <div style="margin-top:10px;"><strong>② 열람한 파일 올려 AI 분석 · 임대인 확인본과 비교 · 서버 저장</strong>
            <input type="file" id="balFile" accept="application/pdf,image/*" style="display:block;margin:6px 0;max-width:100%;">
            <label style="display:flex;gap:6px;align-items:center;font-size:12.5px;"><input type="checkbox" id="balConsent" checked> AI 분석(Google Gemini), 임대인 확인본 자동 비교 및 잔금 증빙 서버 보관에 동의합니다</label>
            <button id="balRead" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="min-height:44px;margin-top:4px;">📤 올려서 AI 분석 · 비교 · 서버 저장</button>
          </div>
          ${readHtml}
          <div style="margin-top:10px;"><strong>③ 원본과 직접 대조</strong>
            <label style="display:flex;gap:6px;align-items:center;min-height:40px;font-size:12.5px;"><input type="checkbox" id="balChecked"> <strong>인터넷등기소 원본과 직접 대조했고, 변동이 없음을 확인했음</strong></label>
            <div class="bass-muted" style="font-size:12px;">'확인했음'에 체크하지 않으면 다음 단계(잔금·출입 정보 받기)로 진행할 수 없습니다.</div>
          </div>
          <div class="bass-row" style="gap:8px;margin-top:10px;flex-wrap:wrap;">
            <button id="balOk" class="bass-btn bass-btn--primary" type="button" style="min-height:46px;">변동 없음 확인</button>
            <button id="balChg" class="bass-btn" type="button" style="min-height:46px;">변동 있음 신고</button>
          </div>
        </div>`;
      bindIrosBox(body);
      const viewBtn = body.querySelector("#balViewSaved");
      if (viewBtn) {
        viewBtn.addEventListener("click", function () {
          Bass.withLock(this, () => openBalanceRegistryFile(s.id), "불러오는 중...");
        });
      }
      const post = (path, payload) => Bass.api(APP_ID, `/sessions/${s.id}/balance-registry/${path}`, { method: "POST", body: { role: "tenant", ...payload } });
      body.querySelector("#balRead").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const file = body.querySelector("#balFile").files[0];
          if (!file) { Bass.updateAppAlert("warning", "인터넷등기소에서 열람한 등기부 파일(PDF·사진)을 먼저 골라 주세요."); return; }
          if (file.size > 10 * 1024 * 1024) { Bass.updateAppAlert("warning", "파일은 10MB 이하만 가능합니다."); return; }
          const dataUrl = await new Promise((res, rej) => { const rd2 = new FileReader(); rd2.onload = () => res(rd2.result); rd2.onerror = rej; rd2.readAsDataURL(file); });
          const res = await post("read", { attachment_data_url: dataUrl, consent: body.querySelector("#balConsent").checked });
          Bass.updateAppAlert(res.level || "warning", res.message);
          await C.fetchActiveSession(); draw();
        });
      });
      const record = (changed) => Bass.withLock(body.querySelector(changed ? "#balChg" : "#balOk"), async () => {
        if (!changed && !body.querySelector("#balChecked").checked) { Bass.updateAppAlert("warning", "원본과 대조하고 '확인했음'에 체크해 주세요."); return; }
        const res = await post("check", { changed, checked: !changed });
        S.sealedContract = res.contract;
        Bass.updateAppAlert(changed ? "warning" : "success", res.message);
        R3.closeModal(); C.renderWorkspace();
      });
      body.querySelector("#balOk").addEventListener("click", () => record(false));
      body.querySelector("#balChg").addEventListener("click", () => record(true));
    };
    draw();
  }

  Object.assign(C, {
    IROS_URL, irosTargetUrl, searchAddress, irosBoxHtml, copyNow, bindIrosBox, openIrosGuideModal,
    isJeonseSession, pairDots, needsGuarantee, isLowDepositMonthly,
    mortgageExposure, hardBlocks, riskBlock, nowLocalInput,
    registryMismatches, aiResultHtml, renderStep1Tenant, renderRecheckTenant,
    renderCancelled, renderStep1, balRegState, balRegCardHtml, openBalRegModal, openBalanceRegistryFile,
  });
})();
