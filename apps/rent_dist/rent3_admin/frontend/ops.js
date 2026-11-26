/* apps/rent3_admin/frontend/ops.js — 운영 안전 · 백업 · 2단계 인증 · 계정·시험 도구 · Data Pool 탐색기 */
(function () {
  "use strict";
  const A = window.R3A;
  const { APP_ID, elTabContent, fmtKb } = A;
  const S = A.state;

  async function downloadBackup(name) {
    const r = await fetch(`/api/rent3_admin/backups/${encodeURIComponent(name)}/download`, { headers: { Authorization: `Bearer ${Bass.token()}` } });
    if (!r.ok) { Bass.updateAppAlert("error", "내려받지 못했습니다."); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  async function loadMfa() {
    const E = Bass.escapeHtml;
    const box = document.getElementById("mfaBody");
    if (!box) return;
    let st;
    try { st = await Bass.api(APP_ID, "/mfa/status"); } catch (e) { box.innerHTML = `<span class="bass-muted">${E(e.message || "")}</span>`; return; }
    if (st.enabled) {
      box.innerHTML = `<div style="font-size:13.5px;line-height:1.8;">🟢 <strong>켜짐</strong> (${E(String(st.enabled_at || "").slice(0, 10))}) · 남은 복구 코드 ${st.recovery_left}개${st.this_session_verified ? "" : " · ⚠️ 이 로그인은 2단계 인증을 거치지 않았습니다(다시 로그인하세요)"}</div>
        <div class="bass-row" style="gap:8px;margin-top:8px;flex-wrap:wrap;"><input id="mfaOffCode" class="bass-input" inputmode="numeric" placeholder="인증 앱 6자리 또는 복구 코드" style="max-width:240px;min-height:44px;"><button id="mfaOff" class="bass-btn" type="button" style="min-height:44px;">끄기</button></div>`;
      document.getElementById("mfaOff").addEventListener("click", function () {
        if (!window.confirm("2단계 인증을 끄면 비밀번호만으로 로그인됩니다. 끌까요?")) return;
        Bass.withLock(this, async () => {
          const r = await Bass.api(APP_ID, "/mfa/disable", { method: "POST", body: { code: document.getElementById("mfaOffCode").value } });
          Bass.updateAppAlert("success", r.message);
          await loadMfa();
        });
      });
    } else {
      box.innerHTML = `<div style="font-size:13.5px;">⚪ <strong>꺼짐</strong>${st.pending ? " (설정하던 중)" : ""}</div><button id="mfaStart" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;margin-top:8px;">🔐 설정 시작</button><div id="mfaSetup" style="margin-top:10px;"></div>`;
      document.getElementById("mfaStart").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const r = await Bass.api(APP_ID, "/mfa/enroll/start", { method: "POST", body: {} });
          const setupEl = document.getElementById("mfaSetup");
          setupEl.innerHTML = `<ol style="margin:0 0 8px 18px;padding:0;font-size:13px;line-height:1.8;"><li>폰에서 인증 앱을 열고 <strong>QR을 스캔</strong>하거나 <strong>키를 직접 입력</strong>합니다.</li><li>앱에 나온 6자리 숫자를 아래에 입력하고 [확인하고 켜기]를 누릅니다.</li></ol>
            <div id="mfaQr" style="background:#fff;padding:8px;display:inline-block;border-radius:8px;"></div>
            <div style="margin:6px 0;font-size:12.5px;">키: <code style="user-select:all;word-break:break-all;">${E(r.secret)}</code></div>
            <div class="bass-row" style="gap:8px;flex-wrap:wrap;"><input id="mfaCode" class="bass-input" inputmode="numeric" maxlength="8" placeholder="6자리 숫자" style="max-width:160px;min-height:44px;"><button id="mfaOn" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">확인하고 켜기</button></div>`;
          try { if (window.QRCode) new window.QRCode(document.getElementById("mfaQr"), { text: r.otpauth_uri, width: 180, height: 180 }); } catch (_) { document.getElementById("mfaQr").remove(); }
          document.getElementById("mfaOn").addEventListener("click", function () {
            Bass.withLock(this, async () => {
              const c = await Bass.api(APP_ID, "/mfa/enroll/confirm", { method: "POST", body: { code: document.getElementById("mfaCode").value } });
              if (c.token) Bass.setToken(c.token);              // 지금 세션이 끊기지 않게 2단계를 마친 토큰으로 교체
              setupEl.innerHTML = `<div style="border:2px solid var(--warn);border-radius:10px;padding:10px;"><strong>🔑 복구 코드 (지금 한 번만 보입니다 — 안전한 곳에 보관)</strong><div style="font-family:monospace;font-size:15px;line-height:1.9;margin:6px 0;user-select:all;">${c.recovery_codes.map(E).join("<br>")}</div>
                <button id="mfaCopy" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">📋 복사</button></div>`;
              document.getElementById("mfaCopy").addEventListener("click", () => { try { navigator.clipboard.writeText(c.recovery_codes.join("\n")); Bass.updateAppAlert("success", "복구 코드를 복사했습니다."); } catch (_) { Bass.updateAppAlert("warning", "복사하지 못했습니다. 직접 선택해 복사하세요."); } });
              Bass.updateAppAlert("success", c.message);
            });
          });
        });
      });
    }
  }

  function bindTestTools() {
    const ttRun = (apply) => async function () {
      Bass.withLock(this, async () => {
        const body = {
          branch: document.getElementById("ttBranch").value.trim(),
          room_no: document.getElementById("ttRoom").value.trim(),
          name: document.getElementById("ttName").value.trim(),
          phone: document.getElementById("ttPhone").value.trim(),
          apply,
          raise_limit: document.getElementById("ttRaise").checked,
        };
        if (apply && !window.confirm(`${body.room_no}호 ${body.name}님(0원 계약)을 등록합니다. 등록 전에 자동 백업합니다. 계속할까요?`)) return;
        const r = await Bass.api(APP_ID, "/accounts/test-tenant", { method: "POST", body });
        const color = r.status === "BLOCKED" ? "var(--warn)" : "var(--ok)";
        document.getElementById("ttResult").innerHTML = `<span style="color:${color};font-weight:800;">${{ DRY: "점검 결과", EXISTS: "이미 있음", BLOCKED: "멈춤", DONE: "등록 완료" }[r.status] || ""}</span><br>${Bass.escapeHtml(r.head || "")}<br>${Bass.escapeHtml(r.message)}${r.status === "DONE" ? "<br><strong>서버는 등록한 풀을 바로 사용합니다.</strong> 이제 포털 '연결 요청'에서 이 정보로 접수할 수 있습니다." : ""}`;
        if (r.status === "DONE") Bass.updateAppAlert("success", r.message);
      });
    };
    document.getElementById("btnTtCheck").addEventListener("click", ttRun(false));
    document.getElementById("btnTtApply").addEventListener("click", ttRun(true));

    const tiRun = (apply) => async function () {
      Bass.withLock(this, async () => {
        const body = {
          name: document.getElementById("tiName").value.trim(),
          phone: document.getElementById("tiPhone").value.trim(),
          apply,
          new_password: document.getElementById("tiPw").value.trim(),
        };
        if (apply && !window.confirm(`${body.name}님(${body.phone})을 처리합니다${body.new_password ? " — 본인인증 표시와 새 비밀번호 설정" : " — 시험용 본인인증 완료 표시"}. 처리 전에 자동 백업합니다. 계속할까요?`)) return;
        const r = await Bass.api(APP_ID, "/accounts/test-identity", { method: "POST", body });
        const color = r.status === "BLOCKED" ? "var(--warn)" : "var(--ok)";
        document.getElementById("tiResult").innerHTML = `<span style="color:${color};font-weight:800;">${{ DRY: "점검 결과", EXISTS: "이미 인증됨", BLOCKED: "멈춤", DONE: "처리 완료" }[r.status] || ""}</span><br>${Bass.escapeHtml(r.message)}`;
        if (r.status === "DONE") { document.getElementById("tiPw").value = ""; Bass.updateAppAlert("success", r.message); }
      });
    };
    document.getElementById("btnTiCheck").addEventListener("click", tiRun(false));
    document.getElementById("btnTiApply").addEventListener("click", tiRun(true));

    const tvRun = (apply) => async function () {
      Bass.withLock(this, async () => {
        const body = {
          branch: document.getElementById("tvBranch").value.trim(),
          room_no: document.getElementById("tvRoom").value.trim(),
          apply,
          deposit: Number(document.getElementById("tvDep").value || 0),
          monthly_rent: Number(document.getElementById("tvRent").value || 0),
        };
        if (apply && !window.confirm(`${body.room_no}호의 시험 임차인을 퇴실 처리하고 공실 매물로 공개합니다. 처리 전에 자동 백업합니다. 계속할까요?`)) return;
        const r = await Bass.api(APP_ID, "/accounts/test-vacate", { method: "POST", body });
        const color = r.status === "BLOCKED" ? "var(--warn)" : "var(--ok)";
        document.getElementById("tvResult").innerHTML = `<span style="color:${color};font-weight:800;">${{ DRY: "점검 결과", EXISTS: "이미 비어 있음", BLOCKED: "멈춤", DONE: "처리 완료" }[r.status] || ""}</span><br>${Bass.escapeHtml(r.message)}`;
        if (r.status === "DONE") Bass.updateAppAlert("success", r.message);
      });
    };
    document.getElementById("btnTvCheck").addEventListener("click", tvRun(false));
    document.getElementById("btnTvApply").addEventListener("click", tvRun(true));

    // ☰ '🧪 시험 계정 도구' 바로가기(?tab=ops&focus=testtools): 처음 한 번만 시험 계정 카드 위치로 내려간다
    if (!window.__r3FocusDone && new URLSearchParams(location.search).get("focus") === "testtools") {
      window.__r3FocusDone = true;
      const el = document.getElementById("ttCard");
      if (el) el.scrollIntoView({ block: "start" });
    }
  }

  function bindMaintenanceAndAccountTools() {
    document.getElementById("btnBkRun").addEventListener("click", function () {
      Bass.withLock(this, async () => { const r = await Bass.api(APP_ID, "/backups/run", { method: "POST", body: {} }); Bass.updateAppAlert(r.verify.ok ? "success" : "warning", r.message); await renderOpsTab(); });
    });
    elTabContent.querySelectorAll(".btn-bk-dl").forEach((b) => b.addEventListener("click", () => downloadBackup(b.dataset.name)));
    loadMfa();

    document.getElementById("btnLf").addEventListener("click", () => {
      const sel = document.getElementById("lfRegion");
      const label = sel.options[sel.selectedIndex].text;
      window.open(`/rent3/leaflet.html?region=${encodeURIComponent(sel.value)}&label=${encodeURIComponent(label)}&campaign=leaflet1`, "_blank", "noopener");
    });
    document.getElementById("btnLp").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const E = Bass.escapeHtml;
        const r = await Bass.api(APP_ID, `/landlord-promo?channel=${document.getElementById("lpChannel").value}&season=${document.getElementById("lpSeason").value}`);
        const p = r.post;
        document.getElementById("lpResult").innerHTML = `
          <div style="font-weight:700;margin-bottom:4px;">제목</div><input id="lpTitle" class="bass-input" style="width:100%;min-height:44px;" value="${E(p.title)}">
          <div style="font-weight:700;margin:8px 0 4px;">본문 (직접 고칠 수 있어요)</div><textarea id="lpBody" class="bass-input" rows="16" style="width:100%;font-size:13px;line-height:1.6;">${E(p.content)}</textarea>
          ${p.tags ? `<div class="bass-muted" style="font-size:12.5px;">태그: ${p.tags.map((x) => "#" + E(x)).join(" ")}</div>` : ""}
          <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">유입 추적 링크: <code>${E(p.link)}</code></div>
          <div class="bass-row" style="gap:8px;margin-top:8px;"><button id="lpCopy" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">📋 제목+본문 복사</button></div>
          <div style="margin-top:10px;font-weight:700;">올리기 전 확인</div>
          <ul style="margin:4px 0 0 18px;font-size:12.5px;line-height:1.7;">${p.checklist.map((x) => `<li>${E(x)}</li>`).join("")}</ul>`;
        document.getElementById("lpCopy").addEventListener("click", async () => {
          await R3.copyText(document.getElementById("lpTitle").value + "\n\n" + document.getElementById("lpBody").value);
          Bass.updateAppAlert("success", "복사했습니다. 광고 표시는 지우지 말고 올려 주세요.");
        });
      });
    });
    document.getElementById("btnAcq").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/acquisition/summary");
        const E = Bass.escapeHtml;
        const tbl = (title, rows) => `<div style="font-weight:700;margin:10px 0 4px;">${title}</div><table class="bass-table" style="width:100%;font-size:12.5px;"><thead><tr><th>구분</th><th>가입</th><th>가맹 승인</th><th>첫 방 등록</th><th>첫 수납</th></tr></thead><tbody>${rows.map((x) => `<tr><td>${E(x.key)}</td><td>${x.signups}</td><td>${x.approved}</td><td>${x.with_room}</td><td>${x.with_payment}</td></tr>`).join("") || `<tr><td colspan="5">자료 없음</td></tr>`}</tbody></table>`;
        document.getElementById("acqResult").innerHTML = `전체 임대인 ${r.total_lessors}명 중 유입 표식이 있는 가입 <strong>${r.tracked}</strong>명` + tbl("채널(ref)별", r.by_ref) + tbl("캠페인별", r.by_campaign) + (r.by_from.length ? tbl("임차인 소개 코드별(상위 20)", r.by_from) : "") + `<div class="bass-muted" style="font-size:12.5px;margin-top:6px;">임차인이 '집주인에게 제안'을 보냈다고 기록한 횟수: ${r.invites_sent}회</div>`;
      });
    });
    document.getElementById("btnLgPrev").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const out = document.getElementById("lgResult");
        const ap = document.getElementById("btnLgApply");
        const r = await Bass.api(APP_ID, "/legacy-registry/preview");
        out.innerHTML = `계약 ${r.contracts_total}건 중 기록 대상 <strong>${r.to_mark}</strong>건(전자계약이 아닌 진행 중 계약)`;
        ap.disabled = r.to_mark === 0;
        ap.onclick = function () {
          if (!window.confirm(`${r.to_mark}건의 기존 입주자에게 '입주 전 등기부 확인 완료(이전 입주자)'를 기록합니다. 풀 전체를 자동 백업한 뒤 진행하며 출입 잠금·수납은 바꾸지 않습니다. 계속할까요?`)) return;
          Bass.withLock(ap, async () => {
            const res = await Bass.api(APP_ID, "/legacy-registry/apply", { method: "POST", body: { confirm: true } });
            Bass.updateAppAlert("success", res.message);
            out.innerHTML = `<strong>${Bass.escapeHtml(res.message)}</strong>`;
            ap.disabled = true;
          });
        };
      });
    });
    document.getElementById("btnRdPrev").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const out = document.getElementById("rdResult");
        const ap = document.getElementById("btnRdApply");
        const r = await Bass.api(APP_ID, "/room-defaults/preview");
        out.innerHTML = `호실 ${r.rooms_total}개 중 정리 대상 <strong>${r.rooms_to_clean}</strong>개 — 샘플 등기부 ${r.counts.registry} · 가전 옵션 ${r.counts.options} · 상태 설명 ${r.counts.status}${r.public_now ? ` · <span style="color:var(--err);font-weight:700;">현재 공개 매물 ${r.public_now}개 포함</span>` : ""}
          ${r.counts.door ? `<div class="bass-muted" style="font-size:12.5px;">도어락 기본값('2580*')인 방 ${r.counts.door}개는 실제로 쓰고 있을 수 있어 자동으로 바꾸지 않습니다(임대인이 직접 확인).</div>` : ""}`;
        ap.disabled = r.rooms_to_clean === 0;
        ap.onclick = function () {
          if (!window.confirm(`${r.rooms_to_clean}개 방의 샘플 값(등기부·옵션·상태 설명)을 비웁니다. 풀 전체를 자동 백업한 뒤 진행하며, 직접 고친 값은 건드리지 않습니다. 계속할까요?`)) return;
          Bass.withLock(ap, async () => {
            const res = await Bass.api(APP_ID, "/room-defaults/apply", { method: "POST", body: { confirm: true } });
            Bass.updateAppAlert("success", res.message);
            out.innerHTML = `<strong>${Bass.escapeHtml(res.message)}</strong>`;
            ap.disabled = true;
          });
        };
      });
    });
    document.getElementById("btnCarryPrev").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const E = Bass.escapeHtml;
        const out = document.getElementById("carryResult");
        const r = await Bass.api(APP_ID, "/carryover-split/preview");
        out.innerHTML = `<div>이월 합산 건 <strong>${r.total}</strong>개 — 분해 가능 <strong>${r.ok}</strong>개(새 납부 건 ${r.new_records}개 예정) · 검토 필요 <strong style="color:${r.review.length ? "var(--err)" : "var(--ok)"};">${r.review.length}</strong>개<br>
          금액 합계: 원본 ${Number(r.sum_ok_amount).toLocaleString("ko-KR")}원 → 분해 후 ${Number(r.sum_planned_amount).toLocaleString("ko-KR")}원 ${r.sum_ok_amount === r.sum_planned_amount ? "✅ 일치" : "❌ 불일치"}</div>
          ${(r.rows || []).slice(0, 12).map((x) => `<div class="bass-muted" style="font-size:12.5px;">${E(x.branch)} ${E(x.room_no)}호 ${E(x.tenant)} · ${Number(x.total).toLocaleString("ko-KR")}원 → ${x.months}개월 (${E(x.from)}~${E(x.to)}) ${x.status !== "OK" ? `<strong style="color:var(--err);">검토 필요: ${E(x.note)}</strong>` : ""}</div>`).join("")}
          ${r.rows.length > 12 ? `<div class="bass-muted" style="font-size:12.5px;">… 외 ${r.rows.length - 12}건</div>` : ""}`;
        const ap = document.getElementById("btnCarryApply");
        ap.disabled = !(r.ok > 0 && r.sum_ok_amount === r.sum_planned_amount);
        ap.onclick = function () {
          if (!window.confirm(`이월 합산 ${r.ok}건을 달별 ${r.new_records}개 건으로 분해합니다.\n풀 전체를 자동 백업한 뒤 진행하고, 계약마다 합계가 다르면 그 건만 되돌립니다.\n원본은 무효로 보관됩니다.\n\n계속할까요?`)) return;
          Bass.withLock(ap, async () => {
            const res = await Bass.api(APP_ID, "/carryover-split/apply", { method: "POST", body: { confirm: true } });
            Bass.updateAppAlert(res.identical_totals && !(res.rolled_back || []).length ? "success" : "warning", res.message);
            out.innerHTML = `<strong>${E(res.message)}</strong>`;
            ap.disabled = true;
          });
        };
      });
    });
    document.getElementById("btnDupScan").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const E = Bass.escapeHtml;
        const out = document.getElementById("dupResult");
        const r = await Bass.api(APP_ID, "/pool-duplicates");
        const ex = r.exact_duplicate_groups || [];
        const line = (p) => `${E(p.branch)} ${E(p.room_no)}호 ${E(p.billing_month)} · ${Number(p.amount).toLocaleString("ko-KR")}원 · 납부일 ${E(p.paid_date)} · <code>${E(p.id)}</code>`;
        out.innerHTML = `<div>납부 ${r.payments_total}건 점검 — <strong style="color:${r.removable_count ? "var(--err)" : "var(--ok)"};">내용이 같은 중복 ${r.removable_count}건</strong> · 같은 달 여러 건(참고) ${(r.same_month_multiple || []).length}건 · 같은 호실 계약 중복 ${(r.duplicate_contracts || []).length}건 · 임차인 중복 ${(r.duplicate_tenants || []).length}건 · 호실 중복 ${(r.duplicate_rooms || []).length}건</div>
          ${ex.slice(0, 20).map((g) => `<div style="padding:6px 0;border-bottom:1px solid var(--border);">남김: ${line(g.keep)}<br>${g.duplicates.map((d) => `<span style="color:var(--err);">지움 대상: ${line(d)}</span>`).join("<br>")}</div>`).join("")}
          ${(r.same_month_multiple || []).slice(0, 10).map((m) => `<div class="bass-muted" style="padding:4px 0;">참고(나눠 낸 정상 납부일 수 있음) ${E(m.contract)} ${E(m.billing_month)}: ${m.payments.map((p) => `${Number(p.amount).toLocaleString("ko-KR")}원(${E(p.paid_date)})`).join(" + ")}</div>`).join("")}
          ${r.removable_count ? `<button id="btnDupRemove" class="bass-btn" type="button" style="min-height:44px;margin-top:8px;">🧹 중복 ${r.removable_count}건 정리 (자동 백업 후)</button>` : ""}`;
        const rm = document.getElementById("btnDupRemove");
        if (rm) rm.addEventListener("click", function () {
          if (!window.confirm(`내용이 완전히 같은 중복 납부 ${r.removable_count}건을 지웁니다(가장 먼저 만든 건은 남김). 지우기 전에 풀 전체를 백업합니다. 계속할까요?`)) return;
          Bass.withLock(this, async () => {
            const res = await Bass.api(APP_ID, "/pool-duplicates/remove", { method: "POST", body: { apply: true } });
            Bass.updateAppAlert("success", res.message);
            out.innerHTML = `<strong>${E(res.message)}</strong>`;
          });
        });
      });
    });

    const lsrOut = document.getElementById("opsLessorResult");
    document.getElementById("btnLessorInvite").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/lessor-invite", { method: "POST", body: { id: document.getElementById("opsLessor").value } });
        const link = window.location.origin + r.url;
        lsrOut.innerHTML = `<strong>${Bass.escapeHtml(r.lessor_name)}님 (${Bass.escapeHtml(r.building_name)})</strong> — 아래 링크를 전달해 주세요(7일·1회).<br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
      });
    });
    document.getElementById("btnLessorRemind").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const lid = document.getElementById("opsLessor").value;
        const r = await Bass.api(APP_ID, `/accounts/lessors/${encodeURIComponent(lid)}/remind-password`, { method: "POST", body: {} });
        const link = window.location.origin + r.url;
        lsrOut.innerHTML = `<strong>${Bass.escapeHtml(r.message)}</strong><br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
        Bass.updateAppAlert("success", r.message);
      });
    });

    const out = document.getElementById("opsResult");
    document.getElementById("btnOpsInvite").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/invite", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        const link = window.location.origin + r.url;
        out.innerHTML = `<strong>${Bass.escapeHtml(r.tenant_name)}님 (${Bass.escapeHtml(r.room_no)}호)</strong> — 아래 링크를 문자로 보내거나 QR 로 보여 주세요. 지금만 보입니다.<br><input class="bass-input" readonly value="${Bass.escapeHtml(link)}" style="width:100%;margin-top:6px;" onclick="this.select()">`;
      });
    });
    document.getElementById("btnOpsRevoke").addEventListener("click", function () {
      if (!window.confirm("이 임차인의 간편 비밀번호와 바로가기 기기를 모두 해제합니다. 계속할까요?")) return;
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/revoke-shortcuts", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        out.textContent = r.message;
        await renderOpsTab();
      });
    });
    document.getElementById("btnOpsResetAct").addEventListener("click", function () {
      const label = document.getElementById("opsTenant").selectedOptions[0].textContent.trim();
      if (!window.confirm(`[${label}]\n비밀번호·간편 비밀번호·바로가기·입주 안내 링크·동의 기록·연결 요청을 모두 지우고 '가입 전' 처음 상태로 되돌립니다. (호실·계약·납부 내역은 그대로)\n연결·가입 과정을 다시 시험할 때 쓰세요. 계속할까요?`)) return;
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/reset-activation", { method: "POST", body: { id: document.getElementById("opsTenant").value } });
        out.innerHTML = `${Bass.escapeHtml(r.message)}<br><span class="bass-muted">${Bass.escapeHtml(r.hint || "")}</span>`;
        await renderOpsTab();
      });
    });
    document.getElementById("btnOpsTemp").addEventListener("click", function () {
      if (!window.confirm("임시 비밀번호를 발급하면 기존 비밀번호는 못 씁니다. 계속할까요?")) return;
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/accounts/reset-password", { method: "POST", body: { kind: "tenant", id: document.getElementById("opsTenant").value } });
        out.innerHTML = `${Bass.escapeHtml(r.message)}<br>임시 비밀번호: <code style="font-size:16px;">${Bass.escapeHtml(r.temporary_password)}</code>`;
      });
    });
  }

  async function renderOpsTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    const [bk, tn, ls] = await Promise.all([Bass.api(APP_ID, "/backups"), Bass.api(APP_ID, "/accounts/tenants"), Bass.api(APP_ID, "/accounts/lessors")]);
    const rows = bk.backups.map((b) => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">
      <span><code>${Bass.escapeHtml(b.name)}</code><br><span class="bass-muted" style="font-size:12px;">${Bass.escapeHtml(b.created_at.replace("T", " "))} · ${fmtKb(b.size)}</span></span>
      <button class="bass-btn bass-btn--sm btn-bk-dl" data-name="${Bass.escapeHtml(b.name)}" type="button" style="min-height:44px;">내려받기</button></div>`).join("") || '<div class="bass-muted" style="font-size:13px;">아직 백업이 없습니다.</div>';
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🛟 Data Pool 백업</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">풀 전체(JSON·감사 로그·업로드 파일)를 하루 한 번 자동으로 압축해 최근 ${bk.keep}개를 남깁니다${bk.daily ? "" : " (지금은 자동 실행이 꺼져 있습니다)"}.
          <strong>같은 디스크에만 두면 디스크 고장 때 함께 사라집니다 — 가끔 내려받아 다른 곳에 보관하세요.</strong> 백업에는 개인정보가 들어 있습니다.</div>
        <button id="btnBkRun" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">지금 백업 만들기</button>
        <div style="margin-top:8px;">${rows}</div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;" id="mfaCard">
        <h3>🔐 내 계정 2단계 인증 (인증 앱)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">비밀번호가 유출돼도 <strong>내 폰의 인증 앱 코드가 없으면 로그인할 수 없게</strong> 합니다(Google Authenticator·Microsoft Authenticator·Authy 등, 문자 요금 없음). 켜면 복구 코드 8개를 <strong>한 번만</strong> 보여 주니 안전한 곳에 보관하세요.</div>
        <div id="mfaBody" class="bass-muted">불러오는 중...</div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📈 임대인 유입 현황 (채널별 가입 → 승인 → 첫 방 → 첫 수납)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">홍보 링크의 <code>?ref=채널&amp;campaign=캠페인</code> 값으로 어느 채널에서 가입했는지 셉니다. 방문 기록·연락처는 저장하지 않고, 임대인 가입 때 짧은 표식만 남깁니다. 표식 없이 가입한 임대인은 '(출처 없음)'입니다.</div>
        <button id="btnAcq" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">📈 현황 보기</button>
        <div id="acqResult" style="margin-top:10px;overflow-x:auto;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🖨️ 오프라인 QR 리플릿 (인천 시범)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">구·군을 고르면 그 지역 표식(<code>ref=offline-incheon-…</code>)이 든 QR 리플릿(A4)을 엽니다. 인쇄하거나 PDF로 저장해 열쇠점·도배점 등 <strong>허락받은 곳에 비치</strong>하세요. 우편함 투입은 건물 관리 규정을 확인하고, 이름·연락처를 수집하지 않습니다. 효과는 위 '유입 현황'에서 지역별 가입·승인 수로 봅니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <select id="lfRegion" class="bass-select" style="width:auto;min-height:44px;">
            ${[["incheon","인천 전체"],["incheon-namdong","인천 남동구"],["incheon-bupyeong","인천 부평구"],["incheon-michuhol","인천 미추홀구"],["incheon-yeonsu","인천 연수구"],["incheon-gyeyang","인천 계양구"],["incheon-seo","인천 서구"],["incheon-jung","인천 중구"],["incheon-dong","인천 동구"]].map((x) => `<option value="${x[0]}">${x[1]}</option>`).join("")}
          </select>
          <button id="btnLf" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🖨️ 리플릿 열기</button>
        </div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📣 임대인 대상 홍보 글 (카페·밴드·블로그)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">운영자가 직접 올리는 글 초안입니다. 맨 앞 <strong>[광고] 표시</strong>를 지우거나 후기처럼 바꾸지 마세요. 요금은 실제 조건에서 읽어 옵니다. 올리기 전 아래 규정 확인 목록을 지켜 주세요.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <select id="lpChannel" class="bass-select" style="width:auto;min-height:44px;"><option value="cafe">☕ 네이버 카페</option><option value="band">👥 네이버 밴드</option><option value="blog">📝 네이버 블로그</option></select>
          <select id="lpSeason" class="bass-select" style="width:auto;min-height:44px;"><option value="none">상시</option><option value="vat_jan">1월 부가세철</option><option value="tax_may">5월 종소세철</option><option value="vat_jul">7월 부가세철</option></select>
          <button id="btnLp" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✍️ 글 만들기</button>
        </div>
        <div id="lpResult" style="margin-top:10px;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📜 기존 입주자 등기부 확인 완료 처리</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">입주 전 등기부 확인은 <strong>공동 전자계약으로 새로 체결한 계약</strong>에서 임차인이 합니다. 전자계약이 아닌 <strong>기존 입주자</strong>는 이미 살고 있어 다시 할 대상이 아니므로 '확인 완료(이전 입주자)'로 기록합니다. 출입 정보 잠금·수납·계약 조건은 바꾸지 않습니다. [미리보기]는 아무것도 바꾸지 않고, [백업 후 기록]은 자동 백업 뒤에 진행합니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;"><button id="btnLgPrev" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 미리보기</button><button id="btnLgApply" class="bass-btn" type="button" style="min-height:44px;" disabled>📜 백업 후 기록</button></div>
        <div id="lgResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🧹 방의 샘플 값 정리 (가짜 등기부·옵션)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">예전에는 모든 방에 <strong>샘플 등기부(소유자 '김임대'·권리 깨끗함)·가전 옵션 5개·상태 설명</strong>이 자동으로 들어갔습니다. 실제 정보가 아닌데 공개 매물에 '등기클린'으로 나올 수 있어, <strong>샘플과 정확히 같은 값만</strong> 비웁니다(직접 고친 값·실제 등기부 값은 그대로). [미리보기]는 아무것도 바꾸지 않고, [백업 후 정리]는 자동 백업 뒤에 진행합니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;"><button id="btnRdPrev" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 미리보기</button><button id="btnRdApply" class="bass-btn" type="button" style="min-height:44px;" disabled>🧹 백업 후 정리</button></div>
        <div id="rdResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🧮 이관 '이전 입금 합산' 건 달별 분해</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">장부 이관 때 여러 달의 입금을 한 건으로 합쳐 넣은 납부를 <strong>달마다 한 건</strong>으로 풀어, 달별 옮기기·무효 처리가 가능하게 합니다. 합계는 바뀌지 않습니다. <strong>[미리보기]는 아무것도 바꾸지 않고</strong>, [백업 후 반영]은 풀 전체를 <strong>자동 백업</strong>한 뒤 진행하며 계약마다 입금·미납·선수금 합계가 다르면 그 건만 즉시 되돌립니다. 원본은 지우지 않고 보관합니다.</div>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <button id="btnCarryPrev" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 미리보기</button>
          <button id="btnCarryApply" class="bass-btn" type="button" style="min-height:44px;" disabled>🧮 백업 후 반영</button>
        </div>
        <div id="carryResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🔎 Data Pool 중복 점검</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">엑셀·장부를 여러 번 읽어 들였거나 같은 입금이 두 번 기록됐는지 점검합니다. <strong>점검만으로는 아무것도 바뀌지 않습니다.</strong> 내용이 완전히 같은 납부만 [중복 정리]로 지울 수 있고(가장 먼저 만든 한 건을 남김), 지우기 전에 자동 백업합니다.</div>
        <button id="btnDupScan" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔎 중복 점검</button>
        <div id="dupResult" style="margin-top:10px;font-size:13px;line-height:1.7;"></div>
      </div>
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>🏢 임대인 암호 설정 · 6개월 변경 권유</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">전화로 본인 확인을 마친 임대인에게 <strong>1회용 비밀번호 설정 링크</strong>(7일 유효, 4칸 대조 필수)를 보내거나, 6개월(180일)이 지난 임대인에게 <strong>새 암호 설정 권유 채팅·링크</strong>를 보냅니다(새 비밀번호를 정하기 전까지 기존 비밀번호와 등록 기기는 유지됩니다).</div>
        <select id="opsLessor" class="bass-select" style="width:100%;min-height:44px;margin-bottom:8px;">
          ${(ls.lessors || []).map((l) => `<option value="${Bass.escapeHtml(l.id)}">${Bass.escapeHtml(l.building_name || l.branch)} · ${Bass.escapeHtml(l.name)}${l.activated ? " (가입됨" + (l.days_since_change != null ? ` · ${l.days_since_change}일 경과` : "") + ")" : " (미설정)"}${l.due_6m ? " · ⚠️ 6개월 경과 권유 대상" : ""}${l.invite_pending ? " · 링크 대기" : ""}</option>`).join("")}
        </select>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnLessorInvite" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">🔑 임대인 암호 설정 링크 발급</button>
          <button id="btnLessorRemind" class="bass-btn" type="button" style="min-height:44px;">🔒 6개월 새 암호 설정 권유 발송</button>
        </div>
        <div id="opsLessorResult" style="margin-top:10px;font-size:13px;word-break:break-all;"></div>
      </div>
      <div class="bass-card">
        <h3>🔑 입주 임차인 계정</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">가져온 임차인은 쓸 수 있는 비밀번호가 없습니다. <strong>입주 안내 링크</strong>(7일·1회)를 만들어 전해 주거나, 임대인이 호실 화면의 [입주 안내 카드]로 직접 만들 수도 있습니다. 링크를 못 쓰는 사정이면 <strong>임시 비밀번호</strong>를 발급하세요(본인에게만 전달).</div>
        <select id="opsTenant" class="bass-select" style="width:100%;min-height:44px;margin-bottom:8px;">
          ${tn.tenants.map((t) => `<option value="${Bass.escapeHtml(t.id)}">${Bass.escapeHtml(t.branch)} ${Bass.escapeHtml(t.room_no)}호 ${Bass.escapeHtml(t.name)}${t.invite ? " · 안내 링크 대기" : ""}${t.devices ? ` · 바로가기 ${t.devices}대` : ""}</option>`).join("")}
        </select>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnOpsInvite" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">입주 안내 링크 만들기</button>
          <button id="btnOpsTemp" class="bass-btn" type="button" style="min-height:44px;">임시 비밀번호 발급</button>
          <button id="btnOpsRevoke" class="bass-btn" type="button" style="min-height:44px;">📵 분실 폰 바로가기 전체 해제</button>
          <button id="btnOpsResetAct" class="bass-btn" type="button" style="min-height:44px;">↩ 가입 상태 초기화 (재시험)</button></div>
        <div id="opsResult" style="margin-top:10px;font-size:13px;word-break:break-all;"></div>
      </div>
      <div class="bass-card" id="ttCard" style="margin-top:14px;">
        <h3>🧪 시험 임차인 등록</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">실사용 확인용으로 호실 하나와 <strong>0원 계약</strong> 임차인을 등록합니다(공개 매물에는 나오지 않음). 먼저 <strong>점검</strong>으로 확인한 뒤 <strong>등록</strong>하세요 — 등록 전에 풀 전체를 자동 백업하고, 같은 호실·임차인이 있으면 아무것도 만들지 않습니다.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <input id="ttBranch" class="bass-input" value="main" placeholder="지점 코드 (예: main)" aria-label="지점 코드" style="min-height:44px;">
          <input id="ttRoom" class="bass-input" value="701" placeholder="호실" aria-label="호실" inputmode="numeric" style="min-height:44px;">
          <input id="ttName" class="bass-input" value="민흥식" placeholder="성함" aria-label="성함" style="min-height:44px;">
          <input id="ttPhone" class="bass-input" value="010-3269-3343" placeholder="휴대폰" aria-label="휴대폰" inputmode="tel" style="min-height:44px;">
        </div>
        <label style="display:block;min-height:44px;padding:10px 0;font-size:13.5px;"><input id="ttRaise" type="checkbox"> 호실 한도가 모자라면 한 칸 올리기</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="btnTtCheck" class="bass-btn" type="button" style="min-height:44px;">🔎 점검(변경 없음)</button>
          <button id="btnTtApply" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✅ 등록</button></div>
        <div id="ttResult" style="margin-top:10px;font-size:13px;line-height:1.6;"></div>
      </div>
      <div class="bass-card" id="tiCard" style="margin-top:14px;">
        <h3>🧪 시험 예비임차인 본인인증 처리</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">가짜 번호라 문자 인증번호를 받을 수 없는 <strong>시험용 예비임차인</strong>(포털에서 가입만 한 계정)을 본인인증 완료로 표시합니다. 입주 중인 임차인·이미 인증된 계정은 바꾸지 않고, 기록에는 <strong>시험용 인증</strong>으로 남습니다. 먼저 <strong>점검</strong>으로 확인한 뒤 <strong>처리</strong>하세요 — 처리 전에 풀 전체를 자동 백업합니다.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <input id="tiName" class="bass-input" value="홍길동" placeholder="성함" aria-label="성함" style="min-height:44px;">
          <input id="tiPhone" class="bass-input" value="010-1111-2222" placeholder="휴대폰" aria-label="휴대폰" inputmode="tel" style="min-height:44px;">
        </div>
        <input id="tiPw" class="bass-input" type="text" autocomplete="off" placeholder="새 비밀번호 (선택 — 적으면 비밀번호도 새로 정합니다)" aria-label="새 비밀번호" style="min-height:44px;width:100%;margin-top:8px;">
        <div class="bass-muted" style="font-size:12px;margin-top:4px;">비밀번호는 8자 이상, 영문과 숫자를 모두 포함해야 합니다(예: Test1234). 새로 정하면 그 계정의 로그인 잠금과 간편 비밀번호도 풀립니다.</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
          <button id="btnTiCheck" class="bass-btn" type="button" style="min-height:44px;">🔎 점검(변경 없음)</button>
          <button id="btnTiApply" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✅ 처리 (본인인증 · 비밀번호)</button></div>
        <div id="tiResult" style="margin-top:10px;font-size:13px;line-height:1.6;"></div>
      </div>
      <div class="bass-card" id="tvCard" style="margin-top:14px;">
        <h3>🧪 시험 호실 비우기 (공실 매물로 다시 공개)</h3>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin-bottom:8px;">'시험 임차인 등록'으로 만든 <strong>0원 계약</strong> 호실(예: 701호)을 퇴실 확정한 것처럼 비웁니다. 시험 임차인은 퇴실 처리, 계약은 종료되고 호실은 <strong>공실 매물</strong>로 공개되어 임차인 화면 '계약할 집 고르기'에 나옵니다. 실제 입주자 호실은 바꾸지 않습니다. 먼저 <strong>점검</strong>, 처리 전에는 풀 전체를 자동 백업합니다.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <input id="tvBranch" class="bass-input" value="main" placeholder="지점 코드" aria-label="지점 코드" style="min-height:44px;">
          <input id="tvRoom" class="bass-input" value="701" placeholder="호실" aria-label="호실" inputmode="numeric" style="min-height:44px;">
          <input id="tvDep" class="bass-input" type="number" value="5000000" placeholder="보증금(원)" aria-label="보증금" style="min-height:44px;">
          <input id="tvRent" class="bass-input" type="number" value="300000" placeholder="월세(원)" aria-label="월세" style="min-height:44px;">
        </div>
        <div class="bass-muted" style="font-size:12px;margin-top:4px;">보증금·월세는 호실에 값이 없을 때만 채웁니다(전출 예정 매물등록 때 입력한 조건이 있으면 그 값을 씁니다).</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
          <button id="btnTvCheck" class="bass-btn" type="button" style="min-height:44px;">🔎 점검(변경 없음)</button>
          <button id="btnTvApply" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✅ 비우고 매물 공개</button></div>
        <div id="tvResult" style="margin-top:10px;font-size:13px;line-height:1.6;"></div>
      </div>`;

    bindTestTools();
    bindMaintenanceAndAccountTools();
  }

  async function renderPoolTab() {
    const events = (S.ov && S.ov.recent_events) || [];

    // 원문 보기·검색·수정은 '공동 JSON Data Pool 허브' 한 화면으로 합쳤다(파일 카드 선택 → 검색 → 원문/JSON 수정)
    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📂 공동 JSON Data Pool — 원문 보기 · 검색 · 수정</h3>
        <div class="bass-muted" style="font-size:13px;line-height:1.7;margin:6px 0 10px;">파일별 현황 카드를 눌러 원문을 보고, 글자로 검색하고, 찾은 건을 JSON 으로 고칠 수 있습니다(수정은 사유 필수·전후 기록). 다른 앱에 로그인하지 않아도 됩니다.</div>
        <a class="bass-btn bass-btn--primary" href="/rent3/pool.html" style="min-height:46px;display:inline-flex;align-items:center;">📂 Data Pool 열기</a>
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
  }

  Object.assign(A, { renderOpsTab, renderPoolTab });
})();
