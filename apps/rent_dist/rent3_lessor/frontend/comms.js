/* apps/rent_dist/rent3_lessor/frontend/comms.js — 호실 채팅·정산 카드, A/S 접수, 입금 메일, 임차인 앱 연결 안내. */
(function () {
  "use strict";
  const L = window.R3L;
  if (!L) return;

  const {
    APP_ID, ME_ADMIN, VIA_PIN, elTabContent, state: S,
    fmtWon, dotDate, copyText, openModal, closeModal,
  } = L;

  // ── 입금 메일 자동 정리(선택, 기본 꺼짐) ──
  async function renderMailTab() {
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    const q = `?branch=${encodeURIComponent(S.currentBranch)}`;
    let st, it;
    try {
      st = await Bass.api(APP_ID, `/mail/settings${q}`);
      it = st.active ? await Bass.api(APP_ID, `/mail/intakes${q}`) : { items: [] };
    } catch (e) { Bass.showError(e); return; }
    if (S.currentTab !== "mail") return;
    const E = Bass.escapeHtml;
    const post = (path, body) => Bass.api(APP_ID, path, { method: "POST", body: { branch: S.currentBranch, ...(body || {}) } });
    const card = (inner) => `<div class="bass-card" style="margin-bottom:12px;">${inner}</div>`;
    let html = st.test_mode ? '<div role="status" style="margin-bottom:10px;padding:8px 12px;border-radius:10px;background:#fef3c7;color:#78350f;font-size:13px;font-weight:700;">🧪 테스트 모드 — 테스트 도메인에서 온 메일은 자동 수납하지 않고 [확인 대기]에만 둡니다.</div>' : "";
    if (!st.active) {
      html += card(`
        <h3 style="margin:0 0 6px;">📧 입금 메일 자동 정리 <span class="bass-badge">꺼짐</span></h3>
        <div class="bass-muted" style="font-size:13px;line-height:1.6;margin-bottom:10px;"><strong>지메일(Gmail) 전용입니다.</strong> 은행이 보내는 입금 알림 메일을 지메일에서 전용 주소로 자동 전달해 두면, 조건이 맞는 입금만 골라 수납 처리하고 결과를 알림으로 알려 드립니다.
          맞지 않는 메일은 처리하지 않고 [확인 대기]에 둡니다. 쓰지 않아도 다른 기능은 그대로입니다.</div>
        <label style="display:flex;gap:8px;align-items:flex-start;min-height:44px;font-size:13px;line-height:1.55;margin-bottom:10px;"><input id="mlConsent" type="checkbox" style="margin-top:4px;"><span><strong>수신 동의</strong> — ${E(st.consent_text)}</span></label>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;margin-bottom:8px;">
          <select id="mlBank" class="bass-select" style="min-height:44px;" aria-label="은행">${st.banks.map((b) => `<option value="${E(b.id)}">${E(b.label)}</option>`).join("")}</select>
          <input id="mlTail" class="bass-input" inputmode="numeric" maxlength="4" placeholder="입금 계좌 끝 4자리" style="width:170px;" aria-label="입금 계좌 끝 4자리">
        </div>
        <div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">계좌를 더 추가하려면 먼저 켠 뒤 이 화면에서 추가합니다. 은행은 메일의 보낸 주소로 자동 구분됩니다.</div>
        <div class="bass-muted" style="font-size:12.5px;margin-bottom:8px;">${st.banks.map((b) => (b.verified ? "" : `${E(b.label)}: ${E(b.note)}`)).join("")}</div>
        <label style="display:flex;gap:8px;align-items:center;min-height:44px;font-size:13px;"><input id="mlAuto" type="checkbox" disabled><span>일치하면 자동 처리 <span class="bass-muted">(검증된 은행에서만 선택할 수 있습니다. 기본은 '확인 후 처리')</span></span></label>
        <button id="mlEnable" class="bass-btn bass-btn--primary" type="button" style="min-height:46px;margin-top:8px;">켜기</button>`);
    } else {
      html += card(`
        <h3 style="margin:0 0 6px;">📧 입금 메일 자동 정리 <span class="bass-badge bass-badge--ok">켜짐</span></h3>
        <div style="font-size:13px;line-height:1.7;">처리 방식 <strong>${st.auto ? "일치하면 자동 처리" : "확인 후 처리"}</strong> · 은행은 메일의 보낸 주소로 자동 구분합니다.</div>
        <div style="margin:8px 0;">
          <div style="font-size:13px;font-weight:700;margin-bottom:4px;">입금 계좌 (${st.accounts.length}/${st.max_accounts})</div>
          ${st.accounts.map((a, i) => `<div class="bass-row" style="justify-content:space-between;gap:8px;min-height:40px;border-top:1px solid var(--border);font-size:13px;"><span><strong>${E(a.bank_label)}</strong> · 끝 4자리 <strong>${E(a.tail)}</strong>${a.verified ? "" : ' <span class="bass-badge" style="font-size:12px;">형식 미검증</span>'}</span>
            <button class="bass-btn bass-btn--sm ml-acc-del" data-i="${i}" type="button" style="min-height:40px;" ${st.accounts.length <= 1 ? "disabled" : ""}>삭제</button></div>`).join("")}
          ${st.accounts.length < st.max_accounts ? `<div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-top:6px;">
            <select id="mlNewBank" class="bass-select" style="min-height:44px;" aria-label="은행">${st.banks.map((b) => `<option value="${E(b.id)}">${E(b.label)}</option>`).join("")}</select>
            <input id="mlNewTail" class="bass-input" inputmode="numeric" maxlength="4" placeholder="끝 4자리" style="width:110px;" aria-label="입금 계좌 끝 4자리">
            <button id="mlAccAdd" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">계좌 추가</button></div>` : ""}
        </div>
        <div style="margin:10px 0;padding:10px;border-radius:10px;background:var(--surface-2,#f1f5f9);">
          <div style="font-size:12.5px;" class="bass-muted">내 전용 수신 주소</div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;"><code id="mlAddr" style="font-size:13px;word-break:break-all;">${st.address ? E(st.address) : "(수신 도메인이 아직 설정되지 않았습니다 — 관리인과 채팅)"}</code>
            ${st.address ? '<button id="mlCopy" class="bass-btn bass-btn--sm" type="button" style="min-height:40px;">복사</button>' : ""}</div>
        </div>
        ${st.forward_code ? `<div style="margin:0 0 10px;padding:10px;border-radius:10px;border:2px solid var(--primary,#2563eb);"><div style="font-size:13px;">📮 지메일 전달 확인 코드 <strong style="font-size:20px;letter-spacing:2px;">${E(st.forward_code)}</strong></div>
          <div class="bass-muted" style="font-size:12.5px;margin:4px 0 6px;">지메일의 '전달' 설정 화면에 이 코드를 입력하세요. 1시간 뒤 저절로 사라집니다.</div>
          <button id="mlCodeDone" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">입력했어요 (코드 지우기)</button></div>` : ""}
        ${st.forward_error ? '<div role="alert" style="margin:0 0 10px;padding:10px;border-radius:10px;background:#fff1f2;font-size:13px;">⚠️ 지메일 확인 메일이 도착했으나 코드를 읽지 못했습니다. 지메일 전달 설정 화면의 안내에 따라 다시 시도하거나 관리인과 채팅해 주세요.</div>' : ""}
        <ol style="margin:0 0 10px;padding-left:20px;font-size:13px;line-height:1.7;"><li>지메일 <strong>설정 → 전달 및 POP/IMAP → 전달 주소 추가</strong>에 위 주소를 입력합니다. 구글이 보낸 <strong>확인 코드</strong>는 이 화면에 잠시 나타납니다.</li>
          <li>지메일 <strong>설정 → 필터 및 차단된 주소 → 새 필터 만들기</strong>에서 <strong>보낸사람</strong>에 ${E([...new Set(st.accounts.flatMap((a) => a.domains))].join(" 또는 "))} 를 넣고, <strong>다음 주소로 전달</strong>에서 위 주소를 고릅니다.</li>
          <li>은행에서 <strong>입금 알림을 이메일로</strong> 받도록 설정되어 있어야 합니다(은행 앱/인터넷뱅킹의 알림 설정).</li></ol>
        <div class="bass-row" style="gap:8px;flex-wrap:wrap;">
          <button id="mlRotate" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">🔄 주소 새로 받기</button>
          <button id="mlDisable" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;border-color:var(--err);color:var(--err);">동의 철회 · 끄기 (보관 자료 삭제)</button>
        </div>`);
      const pend = it.items.filter((x) => x.status === "PENDING");
      const done = it.items.filter((x) => x.status !== "PENDING");
      const money = (x) => fmtWon(x.amount);
      html += card(`<h3 style="margin:0 0 8px;">확인 대기 ${pend.length}건</h3>` + (pend.length ? pend.map((x) => `
        <div class="ml-row" data-id="${E(x.id)}" style="padding:10px 0;border-top:1px solid var(--border);">
          <div><strong>${E(x.room_no ? x.room_no + "호 " : "호실 미확인 ")}${E(x.payer_name)}</strong> · ${money(x)} · <span class="bass-muted">${E(dotDate(x.paid_date))}</span></div>
          ${x.reason ? `<div class="bass-muted" style="font-size:12.5px;margin:2px 0 6px;">⚠️ ${E(x.reason)}</div>` : ""}
          <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
            ${x.room_no ? "" : `<input class="bass-input ml-room" placeholder="호실" style="width:90px;" aria-label="호실">`}
            <button class="bass-btn bass-btn--sm bass-btn--primary ml-apply" type="button" style="min-height:44px;">✅ 처리</button>
            <button class="bass-btn bass-btn--sm ml-dismiss" type="button" style="min-height:44px;">무시</button></div>
        </div>`).join("") : '<div class="bass-muted" style="font-size:13px;">확인할 메일이 없습니다.</div>'));
      html += card(`<h3 style="margin:0 0 8px;">처리 내역 <span class="bass-muted" style="font-size:12.5px;font-weight:500;">(${st.retention_days}일 보관)</span></h3>` + (done.length ? done.slice(0, 30).map((x) => `
        <div style="padding:6px 0;border-top:1px solid var(--border);font-size:13px;"><span class="bass-badge ${x.status === "AUTO_APPLIED" || x.status === "USER_APPLIED" ? "bass-badge--ok" : ""}" style="font-size:12px;">${E(x.status_ko)}</span>
          ${x.amount ? ` ${E(x.room_no ? x.room_no + "호 " : "")}${E(x.payer_name)} · ${money(x)}` : ""}<span class="bass-muted"> ${x.reason ? "· " + E(x.reason) : ""}</span></div>`).join("") : '<div class="bass-muted" style="font-size:13px;">아직 없습니다.</div>'));
    }
    elTabContent.innerHTML = html;
    const done = async (fn) => { try { await fn(); } catch (e) { Bass.showError(e); } };
    const on = (id, fn) => { const el = document.getElementById(id); if (el) el.addEventListener("click", function () { Bass.withLock(this, () => done(fn)); }); };
    on("mlEnable", async () => {
      await post("/mail/enable", { consent: document.getElementById("mlConsent").checked, bank: document.getElementById("mlBank").value, acct_tail: document.getElementById("mlTail").value, auto: false });
      Bass.updateAppAlert("ok", "입금 메일 자동 정리를 켰습니다. 안내에 따라 자동 전달을 설정해 주세요."); renderMailTab();
    });
    on("mlCopy", async () => { R3.copyText(st.address); });
    const accNow = () => st.accounts.map((a) => ({ bank: a.bank, tail: a.tail }));
    on("mlAccAdd", async () => {
      await post("/mail/accounts", { accounts: [...accNow(), { bank: document.getElementById("mlNewBank").value, tail: document.getElementById("mlNewTail").value }] });
      renderMailTab();
    });
    elTabContent.querySelectorAll(".ml-acc-del").forEach((b) => b.addEventListener("click", function () {
      const i = Number(this.dataset.i);
      Bass.withLock(this, () => done(async () => { await post("/mail/accounts", { accounts: accNow().filter((_, k) => k !== i) }); renderMailTab(); }));
    }));
    on("mlCodeDone", async () => { await post("/mail/forward-code/clear"); renderMailTab(); });
    clearTimeout(window.__mlRefresh);
    if (st.active) window.__mlRefresh = setTimeout(() => { const ae = document.activeElement; if (S.currentTab === "mail" && !document.hidden && !(ae && ae.tagName === "INPUT")) renderMailTab(); }, 15000);
    on("mlRotate", async () => { if (!window.confirm("새 주소를 받으면 지금 주소로 오는 메일은 더 이상 처리되지 않습니다. 계속할까요?")) return; await post("/mail/rotate-token"); renderMailTab(); });
    on("mlDisable", async () => {
      if (!window.confirm("동의를 철회하면 수신 주소가 폐기되고, 보관된 입금 메일 처리 기록이 모두 삭제됩니다. 계속할까요?")) return;
      const r = await post("/mail/disable"); Bass.updateAppAlert("ok", `철회했습니다. 보관 자료 ${r.deleted}건을 삭제했습니다.`); renderMailTab();
    });
    elTabContent.querySelectorAll(".ml-row").forEach((row) => {
      const id = row.dataset.id;
      const apply = async (targets) => {
        const room = row.querySelector(".ml-room");
        const r = await post(`/mail/${id}/apply`, { room_no: room ? room.value.trim() : "", target_months: targets || null });
        if (r.needs_month_pick) {
          const body = R3.openModal("어느 달 입금으로 처리할까요?", "");
          body.innerHTML = `<div class="bass-muted" style="font-size:13px;margin-bottom:8px;">${E(r.room_no)}호 — 밀린 달이 여러 개입니다. 반영할 달을 체크해 주세요.</div>` +
            r.unpaid_months.map((m) => `<label style="display:flex;gap:8px;align-items:center;min-height:42px;"><input type="checkbox" class="ml-ym" value="${E(m.ym)}"> <span>${E(m.ym)}분 · ${m.phase === "DUE_SOON" ? "납부 예정" : "미납"} ${fmtWon(m.unpaid)}</span></label>`).join("") +
            '<button id="mlPickGo" class="bass-btn bass-btn--primary" type="button" style="min-height:46px;margin-top:8px;width:100%;">✅ 선택한 달로 처리</button>';
          body.querySelector("#mlPickGo").addEventListener("click", function () {
            const picked = Array.from(body.querySelectorAll(".ml-ym:checked")).map((el) => el.value);
            if (!picked.length) { Bass.updateAppAlert("warning", "반영할 달을 하나 이상 체크해 주세요."); return; }
            Bass.withLock(this, () => done(async () => { await apply(picked); R3.closeModal(); }));
          });
          return;
        }
        Bass.updateAppAlert("ok", "수납 처리했습니다."); renderMailTab();
      };
      row.querySelector(".ml-apply").addEventListener("click", function () { Bass.withLock(this, () => done(() => apply(null))); });
      row.querySelector(".ml-dismiss").addEventListener("click", function () { Bass.withLock(this, () => done(async () => { await post(`/mail/${id}/dismiss`); renderMailTab(); })); });
    });
  }

  // ── 운영 ▸ 채팅: 호실별 1:1 대화(임차인과) + 접어 둔 계약 협상 기록 ──
  const fmtMsgTime = (iso) => (iso && iso.length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");
  const callBtn = (phone, label) => {
    const dg = String(phone || "").replace(/\D/g, "");
    if (dg.length < 9) return "";
    const pretty = dg.length === 11 ? `${dg.slice(0, 3)}-${dg.slice(3, 7)}-${dg.slice(7)}` : dg.length === 10 ? `${dg.slice(0, 3)}-${dg.slice(3, 6)}-${dg.slice(6)}` : dg;
    return `<a class="bass-btn bass-btn--sm bass-btn--primary" href="tel:${dg}" style="min-height:40px;white-space:nowrap;">📞 ${label} ${pretty}</a>`;
  };

  async function renderMessagesTab() {
    clearInterval(S.msgTimer);
    if (S.msgRoom) { await renderMessageThread(); return; }
    elTabContent.innerHTML = '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>';
    let d;
    try { d = await Bass.api(APP_ID, `/messages/rooms?branch=${encodeURIComponent(S.currentBranch)}`); } catch (e) { Bass.showError(e); return; }
    if (S.currentTab !== "messages") return;
    const rows = d.rooms.filter((r) => r.count > 0 || r.is_occupied).sort((a, b) => (b.unread - a.unread) || String(b.last_at).localeCompare(String(a.last_at)) || String(a.room_no).localeCompare(String(b.room_no), "ko", { numeric: true }));
    elTabContent.innerHTML = `
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:10px;">호실을 누르면 입주자와의 대화가 열립니다. 수납·미납 안내문은 [수납 현황]에서 문자로 보내고, 이곳은 앱 안 대화입니다.</div>
      ${rows.map((r) => `
        <button type="button" class="bass-btn msg-room" data-room="${Bass.escapeHtml(r.room_no)}" style="display:block;width:100%;text-align:left;margin-bottom:6px;padding:10px 12px;">
          <div class="bass-row" style="justify-content:space-between;gap:8px;">
            <strong>${Bass.escapeHtml(r.room_no)}호 ${Bass.escapeHtml(r.tenant_name || "공실")}</strong>
            ${r.unread > 0 ? `<span class="bass-badge bass-badge--err">안 읽음 ${r.unread}</span>` : ""}
          </div>
          <div class="bass-muted" style="font-size:12.5px;margin-top:2px;">${r.count ? `${Bass.escapeHtml(r.last_role === "lessor" ? "나: " : "")}${Bass.escapeHtml(r.last_body)} · ${fmtMsgTime(r.last_at)}` : "대화 없음"}</div>
        </button>`).join("") || '<div class="bass-muted">대화할 호실이 없습니다.</div>'}`;
    elTabContent.querySelectorAll(".msg-room").forEach((b) => b.addEventListener("click", () => { S.msgRoom = b.dataset.room; renderMessagesTab(); }));
  }

  // 채팅창 위 '퇴실 정산' 카드: 임차인이 채팅창에서 제출한 반환 계좌를 보고 송금한 뒤 [송금 완료 표시]
  function drawLessorCheckoutCard(room, co) {
    const box = document.getElementById("msgCo");
    if (!box) return;
    const sig = co ? [co.id, co.status, co.tenant_checked_at || "", co.refund_amount, co.refund_bank_account || ""].join("|") : "";
    if (box.dataset.drawn === "1" && sig === S.msgCoSig) return;
    S.msgCoSig = sig; box.dataset.drawn = "1";
    if (!co) { box.innerHTML = ""; return; }
    const E = Bass.escapeHtml, W = (n) => fmtWon(Number(n || 0));
    const open = `<button id="msgCoOpen" class="bass-btn bass-btn--sm" type="button" style="min-height:40px;">📦 전출 정산 창 열기</button>`;
    let body = "";
    if (co.status === "REQUESTED") body = `<strong>🚪 임차인이 퇴실을 신청했습니다</strong>${co.desired_date ? ` (희망일 ${E(co.desired_date)})` : ""}<br>점검 후 정산서를 작성해 게시해 주세요. ${open}`;
    else if (co.status === "DRAFT") body = `<strong>💾 정산서 작성 중</strong> · 반환 예정액 ${W(co.refund_amount)}<br>임차인에게는 아직 보이지 않습니다. 마치면 '임차인에게 게시'에 체크해 보내세요. ${open}`;
    else if (co.status === "CALCULATED") body = `<strong>📤 정산서를 게시했습니다</strong> · 반환 예정액 ${W(co.refund_amount)}<br>${co.tenant_checked_at ? "✅ 임차인이 정산 내역을 확인했습니다. 반환 계좌 입력을 기다리는 중입니다." : "임차인이 확인하고 반환 계좌를 입력하기를 기다리는 중입니다."}`;
    else if (co.status === "AGREED") body = `<strong>💳 임차인이 반환 계좌를 보냈습니다</strong><br>예금주 <strong>${E(co.account_holder || "")}</strong> · 계좌 <strong id="msgCoAcct">${E(co.refund_bank_account || "")}</strong> <button id="msgCoCopy" class="bass-btn bass-btn--sm" type="button" style="min-height:36px;">📋 계좌 복사</button><br>반환액 <strong>${W(co.refund_amount)}</strong> — 내 은행 앱에서 이 계좌로 송금한 뒤 아래를 눌러 주세요.
      <div class="bass-row" style="margin-top:8px;"><button id="msgCoPaid" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">💸 송금 완료 표시</button></div>`;
    else if (co.status === "PAID") body = `<strong>💸 송금 완료로 표시했습니다</strong> · ${W(co.refund_amount)}<br>임차인이 받았다고 확인하면(또는 7일 안에 이의가 없으면) 퇴실이 확정되고 공실 매물로 복귀합니다.`;
    else if (co.status === "DISPUTED") body = `<strong>⚠️ 임차인이 이의를 제기했습니다</strong><br>채팅·통화로 협의한 뒤 정산서를 고쳐 다시 게시해 주세요. ${open}`;
    box.innerHTML = body ? `<div class="bass-card" style="padding:12px 14px;font-size:13.5px;line-height:1.7;border-color:var(--warn);">${body}</div>` : "";
    const o = box.querySelector("#msgCoOpen");
    if (o) o.addEventListener("click", () => L.showMoveOutModal(room));
    const cp = box.querySelector("#msgCoCopy");
    if (cp) cp.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(co.refund_bank_account || ""); Bass.updateAppAlert("success", "계좌번호를 복사했습니다."); }
      catch (_) { Bass.updateAppAlert("warning", "복사하지 못했습니다. 계좌번호를 직접 선택해 복사해 주세요."); }
    });
    const pd = box.querySelector("#msgCoPaid");
    if (pd) pd.addEventListener("click", function () {
      if (!window.confirm(`${co.account_holder || ""} 님 계좌로 ${fmtWon(co.refund_amount)}을 실제로 송금한 뒤 눌러 주세요. 표시하면 임차인에게 수취 확인 요청이 갑니다.`)) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/checkout/${co.id}/mark-paid`, { method: "POST", body: { branch: S.currentBranch } });
        Bass.updateAppAlert("success", res.message);
        S.msgCoSig = ""; box.dataset.drawn = "";
        await L.refresh();
        await renderMessageThread();
      });
    });
  }

  async function renderMessageThread() {
    const room = S.msgRoom;
    let d;
    try { d = await Bass.api(APP_ID, `/messages/rooms/${encodeURIComponent(room)}?branch=${encodeURIComponent(S.currentBranch)}`); } catch (e) { Bass.showError(e); S.msgRoom = ""; return; }
    if (S.currentTab !== "messages" || S.msgRoom !== room) return;
    const neg = d.negotiation || { count: 0, items: [] };
    const digestLine = neg.purged ? (neg.purged_note || "보관 기간이 지나 삭제되었습니다.") : neg.digest_ok === true ? "✅ 체결 때 기록한 대화 기록 해시와 일치합니다"
      : neg.digest_ok === false ? "⚠️ 체결 때 기록한 해시와 달라졌습니다 — 기록이 바뀌었을 수 있습니다"
      : "체결 전 기록입니다";
    const bubble = (m) => m.kind === "system"
      ? `<div class="cm-sys">${Bass.escapeHtml(m.body)} <span>${fmtMsgTime(m.created_at)}</span></div>`
      : `<div class="cm-b ${m.sender_role === "lessor" ? "is-mine" : ""}"><div><div class="cm-meta">${Bass.escapeHtml(m.sender_role === "lessor" ? "나" : m.sender_name || "임차인")} · ${fmtMsgTime(m.created_at)}</div>${Bass.escapeHtml(m.body)}</div></div>`;
    elTabContent.innerHTML = `
      <div class="r3-chat-peer">
        <div class="bass-row" style="gap:8px;"><button id="btnMsgBack" class="bass-btn bass-btn--sm" type="button">← 목록</button><strong>👤 ${Bass.escapeHtml(room)}호 ${Bass.escapeHtml(d.tenant_name || "공실")}</strong></div>
        ${callBtn(d.tenant_phone, "임차인 전화")}
      </div>
      <div id="msgCo" style="margin:0 0 10px;"></div>
      <div id="msgList" class="cm-list" style="max-height:52vh;">${d.messages.map(bubble).join("") || '<div class="bass-muted" style="font-size:13px;">아직 대화가 없습니다.</div>'}</div>
      <div class="r3-chat-compose">
        <textarea id="msgInput" class="bass-input" rows="2" placeholder="입주자에게 보낼 채팅"></textarea>
        <button id="btnMsgSend" class="bass-btn bass-btn--primary bass-btn--sm" type="button">보내기</button>
      </div>
      <details style="margin-top:14px;"><summary class="bass-muted" style="cursor:pointer;font-size:13px;min-height:32px;">계약 협상 기록 ${neg.purged ? `(보관 기간 만료로 삭제됨 · 당시 ${neg.original_count}건)` : `${neg.count}건 ${neg.count ? "보기" : ""}`}</summary>
        <div class="bass-muted" style="font-size:12px;margin:4px 0;">${Bass.escapeHtml(digestLine)}</div>
        ${neg.items.map((m) => `<div style="font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--border);">[${m.step}단계] ${Bass.escapeHtml(m.sender_role === "lessor" ? "임대인" : m.sender_role === "tenant" ? "임차인" : "안내")}: ${Bass.escapeHtml(m.body)}${m.status ? ` <span class="bass-badge">${Bass.escapeHtml(({ open: "답 기다림", accepted: "수락됨", declined: "거절됨", withdrawn: "철회됨" })[m.status] || m.status)}</span>` : ""}</div>`).join("")}
      </details>`;
    drawLessorCheckoutCard(room, d.checkout);
    const list = document.getElementById("msgList");
    list.scrollTop = list.scrollHeight;
    document.getElementById("btnMsgBack").addEventListener("click", () => { S.msgRoom = ""; renderMessagesTab(); });
    document.getElementById("btnMsgSend").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const text = document.getElementById("msgInput").value.trim();
        if (!text) { Bass.updateAppAlert("warning", "내용을 입력해 주세요."); return; }
        await Bass.api(APP_ID, "/chats", { method: "POST", body: { branch: S.currentBranch, room_no: room, message: text } });
        await renderMessageThread();
      });
    });
    S.msgTimer = setInterval(async () => {
      if (S.currentTab !== "messages" || S.msgRoom !== room || !document.getElementById("msgList")) { clearInterval(S.msgTimer); return; }
      if (document.visibilityState === "hidden") return;
      try {
        const nd = await Bass.api(APP_ID, `/messages/rooms/${encodeURIComponent(room)}?branch=${encodeURIComponent(S.currentBranch)}`);
        drawLessorCheckoutCard(room, nd.checkout);
        const box = document.getElementById("msgList");
        if (box && nd.messages.length !== d.messages.length) {
          d = nd;
          const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
          box.innerHTML = nd.messages.map(bubble).join("");
          if (atBottom) box.scrollTop = box.scrollHeight;
        }
      } catch (_) {}
    }, 2500);
  }

  function renderOpsTab() {
    const tickets = S.dash.tickets || [];
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
              <button class="bass-btn bass-btn--sm bass-btn--primary btn-done-as" data-id="${t.id}" type="button">✅ 수리 완료 & 필요경비 자동 등록</button>
            ` : `<div style="font-size:12.5px;color:var(--ok);">완료 내역: ${fmtWon(t.repair_cost)} (${t.cost_burden})</div>`}
          </div>
        `).join("") : `<p class="bass-muted">접수된 A/S 티켓이 없습니다.</p>`}
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
          await L.refresh();
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
          await L.refresh();
        });
      });
    });
  }

  // 📨 기존 임차인 안내: 입주자마다 '본인 전용 링크'(7일·1회)를 만들어 문자 창을 연다
  async function showInviteSender() {
    const E = Bass.escapeHtml;
    const key = `rent3_inv_${S.currentBranch}`;
    const loadB = () => { try { const v = JSON.parse(window.localStorage.getItem(key) || "null"); return v && (Date.now() - v.at) < 6 * 86400000 ? v : null; } catch (_) { return null; } };
    const saveB = (v) => { try { window.localStorage.setItem(key, JSON.stringify(v)); } catch (_) {} };
    const b = S.dash.building || {}, l = S.dash.lessor || {};
    let batch = loadB();
    const body = openModal("📨 기존 임차인 앱 연결 안내", '<div class="bass-muted" style="padding:14px;">불러오는 중...</div>');
    async function draw() {
      const st = await Bass.api(APP_ID, `/tenant-invites?branch=${encodeURIComponent(S.currentBranch)}`);
      const rows = st.tenants;
      const todo = rows.filter((p) => !p.activated);
      const link = (p) => (batch && batch.links[p.room_no]) ? window.location.origin + batch.links[p.room_no] : "";
      const sent = (p) => batch && batch.sent && batch.sent[p.room_no];
      const msgFor = (p) => `[${b.building_name || "건물"} 안내] ${p.tenant_name}님, 안녕하세요. ${b.building_name || "건물"} 관리자입니다.\n월세 납부 내역 확인, 수리(A/S) 요청, 연말정산 납부확인서 발행, 임대인과의 채팅 등 생활 편이를 위해 임차관리 앱을 제공합니다.\n아래 본인 전용 링크를 누르고 본인 확인 및 비밀번호만 정하시면 바로 사용하실 수 있습니다. (7일 안에 한 번만 사용 가능, 다른 분께 전달하지 마세요).\n👉 ${link(p)}\n문의: ${l.phone || ""}`;
      const sendable = todo.filter((p) => p.phone && link(p) && !sent(p));
      const done = rows.length - todo.length;
      const list = rows.map((p, i) => {
        const chip = p.activated ? '<span style="color:var(--ok);font-weight:700;">✅ 가입 완료</span>' : (sent(p) ? '<span style="color:var(--warn);font-weight:700;">📩 보냄</span>' : '<span class="bass-muted">미발송</span>');
        return `<div class="iv-row" data-i="${i}" style="display:flex;align-items:center;gap:6px;padding:6px 4px;border-bottom:1px solid var(--border);">
          <span style="flex:1;min-width:0;font-size:14px;line-height:1.4;"><strong>${E(p.room_no)}호 ${E(p.tenant_name)}</strong> ${chip}<br><span class="bass-muted">${E(p.phone || "번호 없음")}</span></span>
          ${p.activated ? `<button type="button" class="bass-btn bass-btn--sm iv-rereg" style="min-height:44px;">🔁 재등록 문자</button><button type="button" class="bass-btn bass-btn--sm iv-cancel" style="min-height:44px;">↩ 가입 취소</button>`
            : `${!p.phone ? "" : (link(p) ? `<button type="button" class="bass-btn bass-btn--sm iv-one" style="min-height:44px;">📩 문자</button>` : `<button type="button" class="bass-btn bass-btn--sm iv-make1" style="min-height:44px;">🔗 링크 만들기</button>`)}<button type="button" class="bass-btn bass-btn--sm iv-cancel" style="min-height:44px;">${(p.invite_pending || sent(p)) ? "✖ 링크 취소" : "↩ 등록 취소"}</button>`}</div>`;
      }).join("");
      body.innerHTML = `
        <div style="font-size:13px;line-height:1.65;margin-bottom:8px;">입주자마다 <strong>본인 전용 링크</strong>(7일·1회)를 앱이 만들어 줍니다. <strong>링크를 문자로 받은 사람만</strong> 가입할 수 있어서 임대인 승인이나 전화 확인이 필요 없어요. 아래 큰 단추를 누르면 <strong>다음 사람의 문자 창</strong>이 받는 사람·문구가 입력된 채 열리니 <strong>보내기</strong>만 누르고 돌아오세요. <span class="bass-muted">(문구가 비어 있으면 자동 복사된 문구를 붙여넣으세요. PC에서는 복사만 됩니다.)</span><br><span class="bass-muted">사람마다 링크가 달라서 단체문자·그룹채팅에는 넣을 수 없습니다(다른 사람 링크가 보입니다).</span></div>
        <div style="font-weight:800;margin:6px 0;">가입 ${done}/${rows.length}명 · 미발송 ${sendable.length}명</div>
        ${batch ? "" : '<div class="bass-muted" style="margin-bottom:6px;">아직 링크를 만들지 않았습니다.</div>'}
        <button id="ivMake" class="bass-btn ${batch ? "" : "bass-btn--primary"}" type="button" style="width:100%;min-height:48px;margin-bottom:8px;">${batch ? "🔁 링크 다시 만들기 (이전 링크는 못 씀)" : "🔗 입주자별 링크 만들기"}</button>
        <button id="ivNext" class="bass-btn bass-btn--primary" type="button" style="width:100%;min-height:56px;font-size:17px;margin-bottom:10px;" ${sendable.length ? "" : "disabled"}>📩 다음 사람에게 문자 보내기 (${sendable.length}명 남음)</button>
        <div id="ivList" style="max-height:34vh;overflow-y:auto;">${list || '<div class="bass-muted" style="padding:10px;">입주 임차인이 없습니다.</div>'}</div>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.6;margin-top:8px;">📞 문자를 못 받았다는 분은 공통 주소 <code>${E(window.location.origin)}/rent3/?connect=${E(S.currentBranch)}</code> 로 연결을 요청하게 하세요(이때는 임대인 승인 창이 뜹니다).</div>`;
      const smsTo = (phone, text) => {
        copyText(text);
        const d = String(phone || "").replace(/\D/g, "");
        if (d && /Android|iPhone|iPad/i.test(navigator.userAgent)) {
          const a = document.createElement("a");
          a.href = `sms:${d}${/iPhone|iPad/i.test(navigator.userAgent) ? "&" : "?"}body=${encodeURIComponent(text)}`;
          a.click();
        } else { Bass.updateAppAlert("success", "문구를 복사했습니다 — 문자 창에 붙여넣어 보내세요."); }
      };
      const send = (p) => { smsTo(p.phone, msgFor(p)); batch.sent = batch.sent || {}; batch.sent[p.room_no] = true; saveB(batch); setTimeout(draw, 400); };
      const issueOne = async (p) => {
        const r = await Bass.api(APP_ID, "/tenant-invites/bulk", { method: "POST", body: { branch: S.currentBranch, room_no: p.room_no } });
        batch = batch || { at: Date.now(), links: {}, sent: {} };
        batch.links = batch.links || {};
        r.links.forEach((x) => { batch.links[x.room_no] = x.path; if (batch.sent) delete batch.sent[x.room_no]; });
        saveB(batch);
        return r;
      };
      body.querySelectorAll(".iv-row .iv-make1").forEach((btn) => btn.addEventListener("click", function () {
        const p = rows[Number(this.closest(".iv-row").dataset.i)];
        Bass.withLock(this, async () => { await issueOne(p); Bass.updateAppAlert("success", `${p.room_no}호 ${p.tenant_name}님의 새 링크를 만들었습니다. 📩 문자를 눌러 보내세요.`); await draw(); });
      }));
      body.querySelector("#ivMake").addEventListener("click", function () {
        if (batch && !window.confirm("링크를 다시 만들면 이미 보낸 링크는 쓸 수 없게 됩니다. 계속할까요?")) return;
        Bass.withLock(this, async () => {
          const r = await Bass.api(APP_ID, "/tenant-invites/bulk", { method: "POST", body: { branch: S.currentBranch } });
          batch = { at: Date.now(), links: Object.fromEntries(r.links.map((x) => [x.room_no, x.path])), sent: {} };
          saveB(batch);
          Bass.updateAppAlert("success", r.message);
          await draw();
        });
      });
      body.querySelector("#ivNext").addEventListener("click", () => { if (sendable.length) send(sendable[0]); });
      body.querySelectorAll(".iv-row").forEach((row) => {
        const p = rows[Number(row.dataset.i)];
        const one = row.querySelector(".iv-one");
        if (one) one.addEventListener("click", () => send(p));
        const rereg = row.querySelector(".iv-rereg");
        if (rereg) rereg.addEventListener("click", function () {
          if (!window.confirm(`${p.room_no}호 ${p.tenant_name}님께 전화로 본인 확인을 하셨나요?\n\n확인되면 기존 비밀번호·간편 비밀번호·등록된 폰이 모두 지워지고, 장부에 적힌 번호(${p.phone})로 새 가입 링크 문자 창이 열립니다.\n(호실·계약·납부는 그대로, 임차인이 새 비밀번호를 직접 정합니다)`)) return;
          Bass.withLock(this, async () => {
            await Bass.api(APP_ID, "/tenant-invites/cancel", { method: "POST", body: { branch: S.currentBranch, room_no: p.room_no } });
            await issueOne(p);
            const np = { ...p, activated: false };
            smsTo(np.phone, msgFor(np));
            batch.sent = batch.sent || {}; batch.sent[p.room_no] = true; saveB(batch);
            Bass.updateAppAlert("success", `${p.room_no}호 ${p.tenant_name}님의 재등록 링크 문자 창을 열었습니다. 보내기를 눌러 주세요.`);
            setTimeout(draw, 400);
          });
        });
        const cancel = row.querySelector(".iv-cancel");
        if (cancel) cancel.addEventListener("click", function () {
          const msg = p.activated
            ? `${p.room_no}호 ${p.tenant_name}님의 가입을 취소할까요?\n비밀번호·간편 비밀번호·바로가기가 지워져 가입 전 상태로 돌아갑니다. (호실·계약·납부는 그대로)\n다시 쓰려면 안내 링크를 새로 보내야 합니다.`
            : (p.invite_pending || sent(p))
              ? `${p.room_no}호 ${p.tenant_name}님께 보낸 안내 링크를 취소할까요?\n받은 링크는 더 이상 쓸 수 없습니다.`
              : `${p.room_no}호 ${p.tenant_name}님의 이전 등록 기록(비밀번호·동의 기록 등)을 초기화할까요?\n호실·계약·납부는 그대로입니다.`;
          if (!window.confirm(msg)) return;
          Bass.withLock(this, async () => {
            const r = await Bass.api(APP_ID, "/tenant-invites/cancel", { method: "POST", body: { branch: S.currentBranch, room_no: p.room_no } });
            if (batch && batch.links) { delete batch.links[p.room_no]; if (batch.sent) delete batch.sent[p.room_no]; saveB(batch); }
            if (r.kind === "reset") await issueOne(p);
            Bass.updateAppAlert("success", r.message);
            await draw();
          });
        });
      });
    }
    await draw();
  }

  // 🔗 기존 임차인 연결 요청 실시간 감시
  function watchConnect() {
    if (VIA_PIN || ME_ADMIN) return;
    const shown = new Set();
    const telOf = (p) => "tel:" + String(p || "").replace(/[^0-9+]/g, "");
    function renderBar(recent) {
      let bar = document.getElementById("connBar");
      if (!recent.length) { if (bar) bar.remove(); return; }
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "connBar";
        bar.style.cssText = "margin:8px 0;padding:10px 12px;border-radius:12px;border:1px solid var(--ok);background:rgba(34,197,94,.12);font-size:13.5px;line-height:1.6;";
        elTabContent.parentNode.insertBefore(bar, elTabContent);
      }
      const icon = { DONE: "✅", SETTING: "⏳", APPROVED: "📲" };
      bar.innerHTML = "<strong>🔗 임차인 연결 진행</strong><br>" + recent.map((n) => `${icon[n.state] || ""} ${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)} — ${Bass.escapeHtml(n.label)}`).join("<br>");
    }
    function showModal(n) {
      shown.add(n.id);
      const body = openModal(`🔗 ${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)}님 연결 요청`, `
        <div style="font-size:14.5px;line-height:1.7;margin-bottom:4px;">임차인이 전화를 걸어 <strong>4자리 확인번호</strong>를 불러 줍니다. 그 번호를 아래에 입력하세요.</div>
        <a href="${Bass.escapeHtml(telOf(n.tenant_phone))}" style="display:block;font-size:18px;font-weight:800;margin:6px 0 10px;">📞 임차인 ${Bass.escapeHtml(n.tenant_phone || "")} (눌러서 전화)</a>
        <input id="cmCode" class="bass-input" inputmode="numeric" maxlength="4" autocomplete="off" placeholder="확인번호" aria-label="확인번호" style="font-size:30px;letter-spacing:12px;text-align:center;min-height:60px;">
        <div style="display:flex;gap:8px;margin-top:12px;">
          <button id="cmOk" class="bass-btn bass-btn--primary" type="button" style="flex:1;min-height:54px;font-size:17px;">✅ 승인</button>
          <button id="cmNo" class="bass-btn" type="button" style="min-height:54px;">거절</button></div>`);
      const code = body.querySelector("#cmCode");
      setTimeout(() => code.focus(), 50);
      body.querySelector("#cmOk").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(n.id)}/approve`, { method: "POST", body: { branch: S.currentBranch, code: code.value.trim() } });
          closeModal();
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
          poll();
        });
      });
      body.querySelector("#cmNo").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(n.id)}/reject`, { method: "POST", body: { branch: S.currentBranch } });
          closeModal();
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        });
      });
    }
    async function poll() {
      if (document.visibilityState === "hidden") return;
      let d;
      try { d = await Bass.api(APP_ID, `/connect-requests?branch=${encodeURIComponent(S.currentBranch)}`); } catch (_) { return; }
      renderBar(d.recent || []);
      const next = (d.pending || []).find((n) => !shown.has(n.id));
      if (next && !document.getElementById("r3Modal")) showModal(next);
    }
    setTimeout(poll, 1500);
    setInterval(poll, 6000);
  }

  Object.assign(L, {
    renderMailTab, fmtMsgTime, callBtn,
    renderMessagesTab, drawLessorCheckoutCard, renderMessageThread,
    renderOpsTab, showInviteSender, watchConnect,
  });
})();
