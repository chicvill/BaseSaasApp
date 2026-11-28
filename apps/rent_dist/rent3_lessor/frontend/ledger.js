/* apps/rent_dist/rent3_lessor/frontend/ledger.js — 수납 현황, 은행 문자 자동수납, 호실 수납창, 원장 상태, 납부 CSV. */
(function () {
  "use strict";
  const L = window.R3L;
  if (!L) return;

  const {
    APP_ID, VIA_PIN, elTabContent, state: S,
    fmtWon, expiryStatusLabel, pinLimit,
    roomOf, shortYm, dotDate, $m, dueSoonOf, unpaidCount,
    receiptText, unpaidText, sendReceiptSms, downloadWithAuth,
    copyAndSms, openModal, closeModal,
  } = L;

  // 🔍 원장 상태: 저장된 납부 건(출처·적힌 청구월)과 상계 결과·선수금·이상 징후를 보여 주고, 잘못 들어간 건은 여기서 삭제한다
  async function showLedgerStateModal(no) {
    const E = Bass.escapeHtml;
    const r = await Bass.api(APP_ID, `/payments/ledger-state?branch=${encodeURIComponent(S.currentBranch)}&room_no=${encodeURIComponent(no)}`);
    const body = openModal(`🔍 ${E(no)}호 ${E(r.tenant_name || "")} · 수납 원장 상태`, `
      <div style="font-size:13px;line-height:1.7;margin-bottom:8px;">청구 합계 <strong>${fmtWon(r.total_due)}</strong> · 받은 돈 <strong>${fmtWon(r.total_paid)}</strong> · 미납 <strong style="color:${r.total_unpaid ? "var(--err)" : "var(--ok)"};">${fmtWon(r.total_unpaid)}</strong> · 선수금 <strong>${fmtWon(r.advance_credit)}</strong></div>
      ${(r.anomalies || []).map((a) => `<div style="border-left:4px solid var(--warn);padding:6px 10px;margin-bottom:6px;font-size:13px;line-height:1.6;">⚠️ ${E(a.msg)}</div>`).join("") || '<div class="bass-muted" style="font-size:13px;margin-bottom:8px;">✅ 이상 징후 없음</div>'}
      <div class="r3-lbl">저장된 납부 건 (적힌 청구월 · 출처)</div>
      ${(r.payments || []).slice().reverse().slice(0, 60).map((p) => `<div data-pay="${E(p.id)}" style="display:flex;gap:6px;align-items:center;padding:5px 0;border-bottom:1px solid var(--border);font-size:12.5px;">
        <span style="flex:1;min-width:0;"><strong>${E(p.billing_month || "-")}</strong> · ${fmtWon(p.total)} · 입금 ${E(p.paid_date)}<br><span class="bass-muted">${E(p.source)} · 기록 ${E(String(p.created_at || "").slice(0, 16).replace("T", " "))}${(p.allocated_months || []).length > 1 ? ` · 상계 ${E(p.allocated_months.join(", "))}` : ""}</span></span>
        <button type="button" class="bass-btn bass-btn--sm ls-del" style="min-height:40px;">삭제</button></div>`).join("")}
      ${(r.voided || []).length ? `<div class="r3-lbl" style="margin-top:10px;">무효 처리된 납부 (계산에서 제외 · 기록만 보관)</div>${(r.voided || []).map((v) => `<div class="bass-muted" style="font-size:12px;padding:3px 0;">${E(v.billing_month)} · ${fmtWon(v.total)} · 입금 ${E(v.paid_date)} — 사유: ${E(v.reason)} (${E(String(v.voided_at || "").slice(0, 16).replace("T", " "))})</div>`).join("")}` : ""}
      <div class="r3-lbl" style="margin-top:10px;">달별 상계 결과 (밀린 달부터 표시)</div>
      ${(r.months || []).filter((m) => m.due > 0).slice().reverse().slice(0, 36).map((m) => `<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12.5px;${m.unpaid ? (m.phase === "DUE_SOON" ? "color:var(--warn);" : "color:var(--err);") : ""}"><span>${E(m.ym)}</span><span>청구 ${fmtWon(m.due)} · 상계 ${fmtWon(m.paid)}${m.unpaid ? ` · <strong>${m.phase === "DUE_SOON" ? "납부 예정" : "미납"} ${fmtWon(m.unpaid)}</strong>` : ""}${m.labelled_total && m.labelled_total !== m.due ? ` · 이 달로 적힌 납부 ${fmtWon(m.labelled_total)}` : ""}</span></div>`).join("")}`);
    body.querySelectorAll(".ls-del").forEach((b) => b.addEventListener("click", function () {
      const id = this.closest("[data-pay]").dataset.pay;
      const reason = window.prompt("이 납부 건을 무효 처리합니다(기록은 남고 그 달은 미납으로 돌아갑니다). 사유를 적어 주세요:", "오입력");
      if (reason === null) return;
      if (!reason.trim()) { Bass.updateAppAlert("warning", "무효 처리 사유를 적어야 합니다."); return; }
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/payments/delete", { method: "POST", body: { branch: S.currentBranch, pay_id: id, reason: reason.trim() } });
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
        await showLedgerStateModal(no);
      });
    }));
  }

  // ① 수납내역(호실 카드 아래에 항상 펼쳐 둔다): 직접 수납(현금 등) · 월별 납부 내역(날짜·금액 고치기) · 수납확인 문자
  function renderPayInline(no) {
    const r = roomOf(no);
    if (!r || !r.ledger) return;
    const lg = r.ledger;
    const ct = r.contract || {};
    const curY = Number((S.dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const moveYm = (ct.move_in_date || "").slice(0, 7);
    const floorYm = `${curY - 1}-01`;
    const startYm = moveYm > floorYm ? moveYm : floorYm;
    const byYm = {};
    (r.pays || []).forEach((p) => { (byYm[p.billing_month] = byYm[p.billing_month] || []).push(p); });
    const rows = [];
    [...(lg.months || [])].reverse().forEach((m) => {
      if (m.ym < startYm) return;
      if (m.unpaid > 0) {
        rows.push(`<div class="pr pr--un"${m.phase === "DUE_SOON" ? ' style="opacity:.85;"' : ""}><span class="pr__ym">${shortYm(m.ym)}</span><span class="pr__un">${m.phase === "DUE_SOON" ? "🕒 납부 예정" : "미납"} ${fmtWon(m.unpaid)}${m.status === "PARTIAL" ? " (일부 납부)" : ""}${m.phase === "DUE_SOON" && m.note ? ` <span class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(String(m.note).replace(/^납부 예정 /, ""))}</span>` : ""}</span></div>`);
      }
      const ps = byYm[m.ym] || [];
      ps.forEach((p) => rows.push(`<div class="pr" data-pay="${p.id}" data-total="${p.total}" data-rent="${p.rent}" data-fee="${p.fee}"><span class="pr__ym">${shortYm(m.ym)}</span>
        <input type="date" class="bass-input pr-date" value="${Bass.escapeHtml(p.paid_date)}" aria-label="납부일">
        <input type="number" class="bass-input pr-total" value="${p.total}" aria-label="월임차료(입금액)">
        <span style="display:flex;flex-direction:column;gap:2px;"><button type="button" class="bass-btn bass-btn--sm pr-del" data-grpn="${p.grp_n || 1}" aria-label="수납 내역 무효 처리">삭제</button></span></div>`));
      if (!ps.length && !(m.unpaid > 0)) {
        rows.push(`<div class="pr pr--ok"><span class="pr__ym">${shortYm(m.ym)}</span><span class="bass-muted">${m.status === "EXEMPT_POSTPAID" ? "입주월(후불) — 익월부터 청구" : "완납"}</span></div>`);
      }
    });
    const unpaidMonths = (lg.months || []).filter((m) => m.unpaid > 0 && m.ym >= startYm);
    const notices = (S.dash.transfer_notices || []).filter((n) => n.room_no === no);
    const noticeHtml = notices.map((n) => `<div class="bass-card" style="padding:10px;margin-bottom:10px;border-color:var(--warn);" data-notice="${Bass.escapeHtml(n.id)}">
        <div style="font-size:13.5px;font-weight:700;">📨 임차인이 ${fmtWon(n.amount)} 이체했다고 알렸습니다</div>
        <div class="bass-muted" style="font-size:12px;margin:2px 0 6px;">통장에서 입금을 확인한 뒤 수납하세요.${n.memo ? ` 메모: ${Bass.escapeHtml(n.memo)}` : ""}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;"><input type="number" class="bass-input nt-amt" value="${n.amount}" style="width:120px;" aria-label="실제 입금액">
        <button class="bass-btn bass-btn--sm bass-btn--primary nt-ok" type="button" style="min-height:44px;">✅ 입금 확인·수납</button>
        <button class="bass-btn bass-btn--sm nt-no" type="button" style="min-height:44px;">반려</button></div></div>`).join("");
    const host = document.getElementById("payInline");
    if (!host) return;
    const flashHtml = S.payFlash && S.payFlash.no === no ? `<div id="payFlashBox" role="status" style="padding:10px 12px;margin-bottom:10px;border-radius:12px;background:rgba(34,197,94,.16);border:1px solid var(--ok);font-size:14px;line-height:1.5;cursor:pointer;">✅ ${Bass.escapeHtml(S.payFlash.text)}<br><span class="bass-muted" style="font-size:13px;">아래 월별 내역을 확인해 주세요.</span></div>` : "";
    if (flashHtml) {
      const flashRef = S.payFlash;
      const dismiss = () => { if (S.payFlash !== flashRef) return; S.payFlash = null; const el = document.getElementById("payFlashBox"); if (el) el.remove(); };
      setTimeout(() => document.addEventListener("pointerdown", dismiss, { once: true }), 0);
      setTimeout(dismiss, 2000);
    }
    host.innerHTML = `
      <div style="font-weight:800;font-size:16px;margin-bottom:8px;">💰 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 수납내역</div>
      ${flashHtml}
      ${noticeHtml}
      <div class="r3-subbox r3-subbox--direct">
        <label class="r3-lbl" style="margin:0 0 6px;">직접 수납 (현금·여러 달분 입금 등) <span class="bass-muted">— 처리할 미납 달을 체크하세요(여러 달 가능). 금액은 체크한 달의 미납 합계 이하만 받습니다.</span></label>
        ${unpaidMonths.length ? `<div id="pmYm" role="group" aria-label="수납할 달" style="display:grid;gap:4px;margin-bottom:8px;">${unpaidMonths.map((m, i) => `<label class="check-row" style="display:flex;align-items:center;gap:8px;min-height:44px;padding:6px 8px;border-radius:8px;border:1px solid var(--border,#e2e8f0);background:#fff;cursor:pointer;"><input type="checkbox" class="pm-chk" value="${m.ym}" data-unpaid="${m.unpaid}" ${i === 0 ? "checked" : ""}><span>${shortYm(m.ym)} ${m.phase === "DUE_SOON" ? "납부 예정" : "미납"} ${fmtWon(m.unpaid)}</span></label>`).join("")}</div>
        <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
          <input id="pmAmt" class="bass-input" type="number" style="width:140px;" value="${unpaidMonths[0].unpaid}" aria-label="직접 수납액">
          <button id="pmDirect" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="min-height:44px;">💰 직접 수납</button>
        </div>` : `<div class="bass-muted" style="font-size:13px;">✅ 비어 있는 달이 없습니다(밀린 청구 없음). 선수금은 받지 않습니다.</div>`}
      </div>
      ${Number(lg.advance_credit || 0) > 0 ? `<div style="padding:8px 10px;margin-bottom:8px;border-radius:10px;background:rgba(239,68,68,.14);border:1px solid var(--err);font-size:13px;">⚠️ 오류: 청구액보다 ${fmtWon(lg.advance_credit)}을 더 받은 것으로 기록되어 있습니다(선수금은 받지 않습니다). 중복 수납일 수 있으니 <button id="pmState2" type="button" class="bass-btn bass-btn--sm" style="min-height:40px;">🔍 원장 상태</button>에서 확인하고 잘못 들어간 건을 삭제하세요.</div>` : ""}
      <div class="r3-lbl" style="display:flex;justify-content:space-between;align-items:center;gap:8px;">월별 납부 내역 <span class="bass-muted">(날짜·금액을 고치면 바로 저장)</span> <button id="pmState" type="button" class="bass-btn bass-btn--sm" style="min-height:40px;">🔍 원장 상태</button></div>
      <div class="pr pr--head"><span class="pr__ym">월</span><span>납부일</span><span>월임차료</span><span></span></div>
      <div class="pr-list">${rows.join("") || '<div class="bass-muted" style="padding:10px;">내역이 없습니다.</div>'}</div>`;
    const body = host;
    pinLimit(host);

    const after = async (res) => {
      Bass.updateAppAlert(res.level || "success", res.message);
      const pay0 = res.payment || {};
      const pay = (res.payments || []).length > 1 ? { ...pay0, total_amount: res.payments.reduce((a, x) => a + Number(x.total_amount || 0), 0) } : pay0;
      const rn = pay.room_no || no;
      const remain = (res.ledger && res.ledger.total_unpaid) || 0;
      S.receiptMsg[rn] = receiptText(pay, remain);
      S.payFlash = { no: rn, text: `${rn}호 ${fmtWon(pay.total_amount || 0)} 수납 처리 완료 (${dotDate(pay.paid_date || "")})${(res.allocations || []).length ? ` — ${(res.allocations || []).map((x) => shortYm(x.ym)).join(", ")}` : ""}` };
      S.selRoomNo = rn;
      await L.refresh();
      await sendReceiptSms(rn, pay, remain);
    };
    body.querySelectorAll("[data-notice]").forEach((card) => {
      const id = card.dataset.notice;
      card.querySelector(".nt-ok").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/transfer-notices/${encodeURIComponent(id)}/confirm`, { method: "POST", body: { branch: S.currentBranch, amount: Number(card.querySelector(".nt-amt").value || 0) } });
          await after(res);
        });
      });
      card.querySelector(".nt-no").addEventListener("click", function () {
        const why = window.prompt("반려 사유(임차인에게 보입니다)", "입금 내역이 확인되지 않습니다");
        if (why === null) return;
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/transfer-notices/${encodeURIComponent(id)}/reject`, { method: "POST", body: { branch: S.currentBranch, reason: why } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        });
      });
    });
    if ($m("pmYm")) {
      const pmPicked = () => Array.from(body.querySelectorAll(".pm-chk:checked"));
      body.querySelectorAll(".pm-chk").forEach((el) => el.addEventListener("change", () => { $m("pmAmt").value = pmPicked().reduce((a, x) => a + Number(x.dataset.unpaid || 0), 0); }));
      $m("pmDirect").addEventListener("click", function () {
        const months = pmPicked().map((x) => x.value);
        if (!months.length) { Bass.updateAppAlert("warning", "처리할 미납 달을 하나 이상 체크해 주세요."); return; }
        Bass.withLock(this, async () => {
          const amount = Number($m("pmAmt").value || 0);
          const hint = S.smsManualHint && S.smsManualHint.room_no === no && Number(S.smsManualHint.amount) === amount ? S.smsManualHint : null;
          const res = await Bass.api(APP_ID, "/payments/collect", { method: "POST", body: { branch: S.currentBranch, room_no: no, amount,
            ...(months.length === 1 ? { target_ym: months[0] } : { target_months: months }), ...(hint ? { paid_date: hint.paid_date, payer_name: hint.payer_name } : {}) } });
          if (hint) S.smsManualHint = null;
          await after(res);
        });
      });
    }
    const openState = () => showLedgerStateModal(no).catch(Bass.showError);
    if ($m("pmState")) $m("pmState").addEventListener("click", openState);
    if ($m("pmState2")) $m("pmState2").addEventListener("click", openState);
    body.querySelectorAll(".pr[data-pay]").forEach((row) => {
      const delBtn = row.querySelector(".pr-del");
      if (delBtn) {
        delBtn.addEventListener("click", () => {
          const dt = row.querySelector(".pr-date").value;
          const tot = Number(row.dataset.total || row.querySelector(".pr-total").value || 0);
          const grpN = Number(delBtn.dataset.grpn || 1);
          const reason = window.prompt(`${no}호 ${dt} (${fmtWon(tot)}) 수납 내역을 무효 처리합니다.\n기록은 사유와 함께 남고, 그 달은 미납으로 돌아갑니다.${grpN > 1 ? `\n(같은 입금에서 나뉜 다른 달 ${grpN - 1}건은 그대로 남습니다)` : ""}\n\n사유를 적어 주세요:`, "오입력");
          if (reason === null) return;
          if (!reason.trim()) { Bass.updateAppAlert("warning", "무효 처리 사유를 적어야 합니다."); return; }
          Bass.withLock(delBtn, async () => {
            const res = await Bass.api(APP_ID, "/payments/delete", {
              method: "POST",
              body: { branch: S.currentBranch, pay_id: row.dataset.pay, reason: reason.trim() },
            });
            Bass.updateAppAlert("success", res.message);
            delete S.receiptMsg[no];
            await L.refresh();
          });
        });
      }
      // 월임차료 = 월세 + 관리비(은행에 입금되는 금액). 고친 금액은 계약서에 적힌 월세:관리비 비율대로 나눠 저장한다(부가세는 그대로).
      const splitTotal = () => {
        const t0 = Number(row.dataset.total || 0);
        const cR = Number(ct.monthly_rent || 0), cF = Number(ct.maintenance_fee || 0);
        const hasC = cR + cF > 0;
        const r0 = hasC ? cR : Number(row.dataset.rent || 0), f0 = hasC ? cF : Number(row.dataset.fee || 0);
        const vat0 = Math.max(0, t0 - Number(row.dataset.rent || 0) - Number(row.dataset.fee || 0));
        const base = Math.max(0, Number(row.querySelector(".pr-total").value || 0) - vat0);
        const rent = r0 + f0 > 0 ? Math.round((base * r0) / (r0 + f0)) : base;
        return { rent, fee: base - rent };
      };
      row.querySelectorAll("input").forEach((inp) => inp.addEventListener("change", () => {
        Bass.withLock(inp, async () => {
          const sp = splitTotal();
          const res = await Bass.api(APP_ID, "/payments/edit", {
            method: "POST",
            body: {
              branch: S.currentBranch, pay_id: row.dataset.pay,
              paid_date: row.querySelector(".pr-date").value,
              rent_amount: sp.rent,
              maintenance_amount: sp.fee,
            },
          });
          Bass.updateAppAlert("success", res.message);
          delete S.receiptMsg[no];
          await L.refresh();
        }).catch(() => { L.refresh().catch(() => {}); });
      }));
    });
  }

  // 입주 중인 호실에 미리 체결한 '예약 계약'(퇴실 확정 때 새 입주자로 바뀐다): 요약과 취소 단추
  function reservationCardHtml(r) {
    const rv = r.reservation;
    if (!rv) return "";
    if (rv.kind === "RENEWAL") {
      const ct = r.contract || {};
      return `<div class="room-card r3-zone" style="margin-top:10px;border-color:var(--warn);">
      <div style="font-weight:800;">🔁 연장 재계약 예약 · ${Bass.escapeHtml(rv.tenant_name || "")}님</div>
      <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin:4px 0 8px;">새 조건 적용 시작일 <strong>${Bass.escapeHtml(rv.move_in_date || "-")}</strong><br>
        <strong>그날까지는 현재 계약 조건</strong>(보증금 ${fmtWon(ct.deposit)} · 월세 ${fmtWon(ct.monthly_rent)} · 관리비 ${fmtWon(ct.maintenance_fee)})이 그대로 적용되고, 그날부터 <strong>새 조건</strong>(보증금 ${fmtWon(rv.deposit)} · 월세 ${fmtWon(rv.monthly_rent)} · 관리비 ${fmtWon(rv.maintenance_fee)})으로 청구됩니다.</div>
      <button id="btnCancelReserve" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">연장 예약 취소</button>
    </div>`;
    }
    return `<div class="room-card r3-zone" style="margin-top:10px;border-color:var(--warn);">
      <div style="font-weight:800;">📅 다음 입주 예약 · ${Bass.escapeHtml(rv.tenant_name || "")}님</div>
      <div class="bass-muted" style="font-size:12.5px;line-height:1.7;margin:4px 0 8px;">입주 예정일 <strong>${Bass.escapeHtml(rv.move_in_date || "-")}</strong> · 보증금 ${fmtWon(rv.deposit)} · 월세 ${fmtWon(rv.monthly_rent)} · 관리비 ${fmtWon(rv.maintenance_fee)}<br>
        현재 입주자가 퇴실 확정(정산 완료)되면 이 계약으로 입주가 시작됩니다. 그때까지 현재 계약·청구는 그대로입니다.</div>
      <button id="btnCancelReserve" class="bass-btn bass-btn--sm" type="button" style="min-height:44px;">예약 계약 취소</button>
    </div>`;
  }

  // 호실 수납 창: 호실 단추·이체 확인 알림·문자 수납 결과를 누르면 그 호실의 계약 요약 + 수납내역이 창으로 열린다
  function openRoomModal(no) {
    S.selRoomNo = String(no);
    const r = roomOf(S.selRoomNo);
    const who = r && r.is_occupied && r.contract ? ` ${r.contract.tenant_name || ""}` : " 공실";
    const body = openModal(`🏠 ${Bass.escapeHtml(S.selRoomNo)}호${Bass.escapeHtml(who)}`, '<div id="roomPanel"></div><button type="button" id="roomModalClose" class="bass-btn" style="width:100%;min-height:48px;margin-top:12px;font-weight:800;">닫기</button>');
    const box = body.closest(".r3-modal__box");
    if (box) { box.classList.add("r3-modal__box--wide"); const xb = box.querySelector(".r3-modal__x"); if (xb) xb.textContent = "✕ 닫기"; }
    body.querySelector("#roomModalClose").addEventListener("click", closeModal);
    renderRoomPanel();
    return body;
  }

  function renderRoomPanel() {
    const box = document.getElementById("roomPanel");
    const r = roomOf(S.selRoomNo);
    if (!box) return;
    if (!r) { box.innerHTML = ""; return; }
    const ct = r.contract;
    const lg = r.ledger;
    if (!(r.is_occupied && lg && ct)) {
      box.innerHTML = `<div class="room-card">
        <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
          <div><strong style="font-size:17px;">${Bass.escapeHtml(r.room_no)}호</strong> <span class="bass-badge ${r.is_published ? "bass-badge--info" : "bass-badge--warn"}">${r.is_published ? "공실 · 매물 공개중" : "공실 · 비공개"}</span></div>
          <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
            <button class="bass-btn bass-btn--sm btn-pub-toggle" data-id="${r.id}" data-pub="${!r.is_published}" type="button">${r.is_published ? "비공개 전환" : "직거래 매물 공개"}</button>
            <button class="bass-btn bass-btn--sm btn-open-promo" data-id="${r.id}" data-room="${r.room_no}" type="button" style="border-color:hsla(28,95%,50%,.4);background:hsla(28,95%,55%,.1);font-weight:600;">📢 홍보글 복사</button>
            <button class="bass-btn bass-btn--sm btn-go-broker" data-room="${r.room_no}" type="button">📸 중개계약 사진등록</button>
            <a class="bass-btn bass-btn--sm" href="/rent3_contract/?role=lessor">🤝 서식 계약</a>
          </div>
        </div>
        <div class="bass-muted" style="font-size:13px;margin-top:6px;">보증금 ${fmtWon(r.deposit)} · 월세 ${fmtWon(r.monthly_rent)} · 관리비 ${fmtWon(r.maintenance_fee)}</div>
      </div>`;
      box.querySelectorAll(".btn-pub-toggle").forEach((btn) => btn.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/rooms/${btn.dataset.id}/publish`, { method: "POST", body: { is_published: btn.dataset.pub === "true" } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        });
      }));
      box.querySelectorAll(".btn-open-promo").forEach((btn) => btn.addEventListener("click", () => L.showPromoModal(btn.dataset.id, btn.dataset.room)));
      box.querySelectorAll(".btn-go-broker").forEach((btn) => btn.addEventListener("click", () => { S.brokerTargetRoomNo = btn.dataset.room || ""; L.switchTab("broker"); }));
      return;
    }
    const ew = r.expiry_window;
    const ec = r.expiry_check;
    const inWindow = Boolean(ew && ew.in_6_to_2_months_window);
    const need = inWindow && !ec;
    const n = unpaidCount(r);
    const bs = r.billing_schedule;
    box.innerHTML = `<div class="room-card r3-zone r3-zone--room" style="${need ? "border-color:var(--warn);" : ""}">
      <div class="rc-btns">
        <button class="bass-btn bass-btn--sm" data-act="contract" type="button">계약내용</button>
        <button class="bass-btn bass-btn--sm ${need ? "rc-warn" : ""}" data-act="expiry" type="button">만기통보</button>
        <button class="bass-btn bass-btn--sm" data-act="moveout" type="button">전출 정산</button>
      </div>
      <div class="rc-subbtns"><button class="bass-btn bass-btn--sm" data-act="invite" type="button" style="min-height:44px;border-color:hsla(199,80%,60%,.4);">📲 입주 안내 카드 (QR)</button>
        <button class="bass-btn bass-btn--sm" data-act="lostphone" type="button" style="min-height:44px;border-color:hsla(356,75%,62%,.4);">📵 분실 폰 해제</button>
        ${r.pre_moveout_notice ? `<button class="bass-btn bass-btn--sm btn-open-promo" data-id="${r.id}" data-room="${r.room_no}" type="button" style="min-height:44px;border-color:hsla(28,95%,50%,.4);">📢 홍보글 복사</button>` : ""}
        ${n > 0 ? '<button class="bass-btn bass-btn--sm" data-act="unpaidmsg" type="button" style="min-height:44px;border-color:hsla(356,75%,62%,.4);">📨 미납 안내 문자</button>' : ""}</div>
      <div class="rc-info">
        <span class="rc-unpaid ${n > 0 ? "is-late" : ""}">${n > 0 ? `미납:${n}` : (dueSoonOf(r) > 0 ? `<span class="bass-badge bass-badge--warn" style="font-size:12px;">🕒 납부 예정 ${fmtWon(dueSoonOf(r))}</span>` : "완납")}</span><br>
        보증금 ${fmtWon(ct.deposit)} · 월세 ${fmtWon(ct.monthly_rent)} · 관리비 ${fmtWon(ct.maintenance_fee)}<br>
        만기일 ${Bass.escapeHtml(dotDate(ct.expiry_date))}${lg.days_to_expiry != null ? ` (D-${lg.days_to_expiry})` : ""}${bs ? ` · 납부일 매월 ${bs.payment_day}일` : ""}${ec ? ` · ${Bass.escapeHtml(expiryStatusLabel(ec))}` : ""}
      </div></div>
      ${reservationCardHtml(r)}
      <div id="payInline" class="room-card r3-zone r3-zone--pay" style="margin-top:10px;"></div>`;
    const cancelBtn = box.querySelector("#btnCancelReserve");
    if (cancelBtn) cancelBtn.addEventListener("click", function () {
      const isRen = Boolean(r.reservation && r.reservation.kind === "RENEWAL");
      const why = window.prompt(isRen
        ? `${r.room_no}호 연장 재계약 예약(${(r.reservation && r.reservation.tenant_name) || ""}님)을 취소합니다. 취소 사유를 적어 주세요(선택).\n취소해도 만기까지는 현재 계약 조건이 그대로 적용됩니다.`
        : `${r.room_no}호 예약 계약(${(r.reservation && r.reservation.tenant_name) || ""}님)을 취소합니다. 취소 사유를 적어 주세요(선택).\n취소하면 이 호실은 전출 예정 사전 매물로 다시 공개됩니다.`, "");
      if (why === null) return;
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/reservation/cancel`, { method: "POST", body: { reason: why } });
        Bass.updateAppAlert("success", res.message, res.hint || "");
        await L.refresh();
      });
    });
    const acts = {
      contract: (rn) => L.showContractModal(rn),
      expiry: (rn) => L.showExpiryModal(rn),
      moveout: (rn) => L.showMoveOutModal(rn),
      invite: (rn) => L.showInviteModal(rn),
      lostphone: (rn) => L.revokeShortcuts(rn),
      unpaidmsg: (rn) => { const rm = roomOf(rn); if (!rm || !rm.ledger) return; copyAndSms(unpaidText(rm), (rm.contract && rm.contract.tenant_phone) || (rm.tenant && rm.tenant.phone) || ""); },
    };
    box.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => acts[b.dataset.act](r.room_no)));
    box.querySelectorAll(".btn-open-promo").forEach((btn) => btn.addEventListener("click", () => L.showPromoModal(btn.dataset.id, btn.dataset.room)));
    renderPayInline(r.room_no);
  }

  let _ctxCleanup = null;

  function closeRoomContextMenu() {
    if (_ctxCleanup) {
      const fn = _ctxCleanup;
      _ctxCleanup = null;
      fn();
    }
    document.querySelectorAll(".rl-ctx-menu, .rl-ctx-backdrop").forEach((el) => el.remove());
    document.querySelectorAll(".rl-more[aria-expanded='true']").forEach((el) => el.setAttribute("aria-expanded", "false"));
  }

  function smartContractHrefForRoom(r) {
    const live = ((S.dash && S.dash.cosign_sessions) || []).find((s) =>
      s.room_no === r.room_no && (s.status === "IN_PROGRESS" || s.status === "PAUSED")
    );
    if (live && live.id) {
      return `/rent3_contract/?session=${encodeURIComponent(live.id)}&role=lessor`;
    }
    return `/rent3_contract/?role=lessor&call_branch=${encodeURIComponent(S.currentBranch)}&call_room=${encodeURIComponent(r.room_no)}`;
  }

  function openRoomContextMenu(no, anchorEl, clientPoint) {
    const wasSame = anchorEl && anchorEl.getAttribute("aria-expanded") === "true";
    closeRoomContextMenu();
    if (wasSame) return;

    const r = roomOf(String(no));
    if (!r) return;
    const E = Bass.escapeHtml;
    const isOcc = Boolean(r.is_occupied && r.contract);
    const ct = r.contract || {};
    const who = isOcc ? (ct.tenant_name || "") : "공실";
    const nUnpaid = isOcc ? unpaidCount(r) : 0;
    const needExpiry = Boolean(isOcc && r.expiry_window && r.expiry_window.in_6_to_2_months_window && !r.expiry_check);
    const liveSess = ((S.dash && S.dash.cosign_sessions) || []).find((s) =>
      s.room_no === r.room_no && (s.status === "IN_PROGRESS" || s.status === "PAUSED")
    );
    const ri = r.registry_info || {};
    const regRegistered = Boolean(r.registry_checked);
    const regFromBalance = ri.source === "tenant_balance";
    const regBadge = !regRegistered
      ? (ri.status === "pending" ? "확정 대기" : "미등록")
      : (regFromBalance ? "잔금 최신" : (ri.verified_at ? ri.verified_at.slice(5) : "등재됨"));
    const carNo = (r.tenant && r.tenant.car_number) ? String(r.tenant.car_number).trim() : "";

    const items = [];
    if (isOcc) {
      items.push({ key: "ledger", label: "💰 수납내역" });
      if (!VIA_PIN) {
        items.push({ key: "contract", label: "📄 계약내용" });
        items.push({ key: "expiry", label: "⚖️ 만기통보", badge: needExpiry ? "통보 필요" : "" });
        items.push({ key: "moveout", label: "📦 전출정산" });
        items.push({ key: "smartcontract", label: "🤝 스마트 계약", badge: liveSess ? "진행 중" : "" });
      }
      items.push({ key: "chat", label: "💬 임차인과 채팅" });
      if (!VIA_PIN) {
        items.push({ sep: true });
        items.push({ key: "registry", label: "📜 등기부등본 등재", pink: !regRegistered, badge: regBadge, badgeCls: !regRegistered ? "rl-ctx-badge--pink" : "bass-badge--ok" });
        items.push({ key: "doorlock", label: "🔑 도어락 · 주차", badge: carNo, badgeCls: "bass-badge--info" });
        items.push({ key: "invite", label: "📲 입주안내 카드" });
        items.push({ key: "lostphone", label: "📵 분실폰 해제", danger: true });
        if (nUnpaid > 0) items.push({ key: "unpaidmsg", label: "📨 미납 안내 문자", badge: `미납 ${nUnpaid}` });
        if (r.pre_moveout_notice) items.push({ key: "promo", label: "📢 홍보글 복사" });
        items.push({ key: "listing", label: "🏷️ 임대매물 · PDF 등록" });
        items.push({ key: "broker", label: "📸 중개계약서 사진 등록" });
      }
    } else {
      items.push({ key: "ledger", label: "🏠 호실 상세 · 매물 상태" });
      if (!VIA_PIN) {
        items.push({ key: "registry", label: "📜 등기부등본 등재", pink: !regRegistered, badge: regBadge, badgeCls: !regRegistered ? "rl-ctx-badge--pink" : "bass-badge--ok" });
        items.push({ key: "doorlock", label: "🔑 도어락 · 주차" });
        items.push({ key: "listing", label: "🏷️ 임대매물 · PDF 등록" });
        items.push({ key: "pubtoggle", label: r.is_published ? "🙈 매물 비공개 전환" : "👁️ 직거래 매물 공개" });
        items.push({ key: "promo", label: "📢 홍보글 복사" });
        items.push({ key: "smartcontract", label: "🤝 스마트 계약", badge: liveSess ? "진행 중" : "신규" });
        items.push({ key: "broker", label: "📸 중개계약 사진등록" });
      }
    }

    const backdrop = document.createElement("div");
    backdrop.className = "rl-ctx-backdrop";
    const menu = document.createElement("div");
    menu.className = "rl-ctx-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", `${r.room_no}호 빠른 메뉴`);
    menu.innerHTML = `
      <div class="rl-ctx-head">
        <span>🏠 ${E(r.room_no)}호 ${E(who)}</span>
        <span class="bass-badge ${isOcc ? (nUnpaid > 0 ? "bass-badge--err" : "bass-badge--ok") : "bass-badge--warn"}" style="font-size:12px;">${isOcc ? (nUnpaid > 0 ? `미납 ${nUnpaid}` : (dueSoonOf(r) > 0 ? "대기" : "완납")) : "공실"}</span>
      </div>
      ${items.map((it) => it.sep
        ? '<div class="rl-ctx-sep" role="separator"></div>'
        : `<button type="button" class="rl-ctx-item ${it.danger ? "rl-ctx-item--danger" : ""} ${it.pink ? "rl-ctx-item--pink" : ""}" role="menuitem" data-rmact="${E(it.key)}">
            <span>${E(it.label)}</span>
            ${it.badge ? `<span class="bass-badge ${it.badgeCls || "bass-badge--warn"}" style="font-size:12px;">${E(it.badge)}</span>` : ""}
          </button>`).join("")}
      ${VIA_PIN ? '<div class="bass-muted" style="font-size:12px;padding:6px 10px 4px;">※ 계약·만기·설정은 아이디 로그인 후 이용할 수 있습니다.</div>' : ""}`;

    document.body.appendChild(backdrop);
    document.body.appendChild(menu);
    if (anchorEl) anchorEl.setAttribute("aria-expanded", "true");

    // 위치 계산: 모바일·데스크톱 모두 화면 밖으로 잘리지 않게 자동 상하/좌우 보정
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const mw = menu.offsetWidth || 236;
    const mh = menu.offsetHeight || 280;
    const bottomReserve = 68;
    let left, top;
    if (clientPoint) {
      left = clientPoint.x;
      top = clientPoint.y;
    } else if (anchorEl) {
      const rect = anchorEl.getBoundingClientRect();
      left = rect.right - mw;
      top = rect.bottom + 4;
      if (top + mh > vh - bottomReserve && rect.top - mh - 4 >= 8) {
        top = rect.top - mh - 4;
      }
    } else {
      left = (vw - mw) / 2;
      top = 120;
    }
    left = Math.max(10, Math.min(left, vw - mw - 10));
    top = Math.max(10, Math.min(top, vh - mh - bottomReserve));
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;

    const onKey = (ev) => { if (ev.key === "Escape") closeRoomContextMenu(); };
    const onResize = () => closeRoomContextMenu();
    backdrop.addEventListener("click", closeRoomContextMenu);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    _ctxCleanup = () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };

    const runAction = (key) => {
      closeRoomContextMenu();
      S.selRoomNo = String(r.room_no);
      if (key === "ledger") { S.payFlash = null; openRoomModal(r.room_no); }
      else if (key === "contract") L.showContractModal(r.room_no);
      else if (key === "expiry") L.showExpiryModal(r.room_no);
      else if (key === "moveout") L.showMoveOutModal(r.room_no);
      else if (key === "smartcontract") window.location.href = smartContractHrefForRoom(r);
      else if (key === "chat") { S.msgRoom = r.room_no; L.switchTab("messages"); }
      else if (key === "registry") L.openRoomRegistryModal(r.room_no);
      else if (key === "doorlock") L.openRoomDoorlockParkingModal(r.room_no);
      else if (key === "invite") L.showInviteModal(r.room_no);
      else if (key === "lostphone") L.revokeShortcuts(r.room_no);
      else if (key === "unpaidmsg") {
        const rm = roomOf(r.room_no);
        if (!rm || !rm.ledger) return;
        copyAndSms(unpaidText(rm), (rm.contract && rm.contract.tenant_phone) || (rm.tenant && rm.tenant.phone) || "");
      }
      else if (key === "promo") L.showPromoModal(r.id, r.room_no);
      else if (key === "listing") { S.listingTargetRoomNo = r.room_no; L.switchTab("rooms"); }
      else if (key === "broker") { S.brokerTargetRoomNo = r.room_no; L.switchTab("broker"); }
      else if (key === "pubtoggle") {
        Bass.withLock(null, async () => {
          const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/publish`, { method: "POST", body: { is_published: !r.is_published } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        }).catch(Bass.showError);
      }
    };

    menu.querySelectorAll("[data-rmact]").forEach((btn) => {
      btn.addEventListener("click", () => runAction(btn.dataset.rmact));
    });
  }

  function renderLedgerTab() {
    closeRoomContextMenu();
    const rooms = S.dash.rooms || [];
    const roomState = (x) => {
      if (!(x.is_occupied && x.contract)) return { cls: "vacant", label: "공실" };
      const n = unpaidCount(x);
      if (n > 0) return { cls: "late", label: `미납 ${n}` };
      if (dueSoonOf(x) > 0) return { cls: "wait", label: "대기" };
      return { cls: "ok", label: "완납" };
    };
    const roomRow = (x) => {
      const st = roomState(x);
      const day = x.billing_schedule && x.billing_schedule.payment_day;
      const who = x.is_occupied && x.contract ? ` ${x.contract.tenant_name || ""}` : "";
      const rn = Bass.escapeHtml(x.room_no);
      return `<div class="rl-wrap rl-wrap--${st.cls}" data-room="${rn}">
        <button type="button" class="bass-btn rl-row rl-row--${st.cls}" data-room="${rn}" aria-label="${rn}호${Bass.escapeHtml(who)} ${Bass.escapeHtml(st.label)}${day ? ` 매월 ${day}일` : ""}">
          <span class="rl-no">${rn}<small>호</small></span>
          <span class="rl-who">${Bass.escapeHtml((who || " 공실").trim())}</span>
          <span class="rl-st">${Bass.escapeHtml(st.label)}</span>
          <span class="rl-day">${day ? `${day}일` : "-"}</span>
        </button>
        <button type="button" class="rl-more" data-room="${rn}" aria-label="${rn}호 빠른 메뉴" aria-haspopup="menu" aria-expanded="false">⋯</button>
      </div>`;
    };
    const conn = S.dash.connect_requests || [];
    const incomingCalls = ((S.dash && S.dash.cosign_sessions) || []).filter((s) =>
      s.status === "IN_PROGRESS" && s.call_status === "CALLING" && s.initiated_by_role === "tenant"
    );
    const incomingCallsHtml = incomingCalls.length ? `
      <div class="bass-card" style="padding:10px 12px;margin-bottom:12px;border:2px solid var(--primary,#2563eb);background:rgba(37,99,235,.07);">
        <div style="font-weight:800;font-size:14px;color:#1e3a8a;margin-bottom:6px;">💬 예비 임차인 1:1 방문 협의 · 스마트 계약 요청 ${incomingCalls.length}건</div>
        ${incomingCalls.map((s) => `
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--border);font-size:13px;line-height:1.5;">
            <div>
              <strong>${Bass.escapeHtml(s.room_no)}호 ${Bass.escapeHtml((s.terms && s.terms.tenant_name) || "예비 임차인")}님</strong>
              <div class="bass-muted" style="font-size:12.5px;">채팅으로 방문 일정을 협의한 뒤, 현장에서 대면하면 스마트 계약을 개시할 수 있습니다.</div>
            </div>
            <a class="bass-btn bass-btn--sm bass-btn--primary" href="/rent3_contract/?session=${encodeURIComponent(s.id)}&role=lessor&chat=1" style="min-height:42px;display:inline-flex;align-items:center;white-space:nowrap;">💬 채팅 개시 · 확인 →</a>
          </div>
        `).join("")}
      </div>` : "";
    const listingAlerts = S.dash.listing_alerts || [];
    const listingAlertsHtml = listingAlerts.length ? `
      <div class="bass-card" style="padding:10px 12px;margin-bottom:12px;border-left:4px solid var(--primary,#2563eb);background:rgba(37,99,235,.06);">
        <div style="font-weight:800;font-size:14px;margin-bottom:6px;">📄 매물 등록(표준 임대조건 PDF 작성) 알림 ${listingAlerts.length}건</div>
        ${listingAlerts.map((a) => `
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--border);font-size:13px;line-height:1.5;">
            <div><strong>${Bass.escapeHtml(a.title || `${a.room_no}호 매물 등록 필요`)}</strong><div class="bass-muted" style="font-size:12.5px;">${Bass.escapeHtml(a.detail || "")}</div></div>
            <button type="button" class="bass-btn bass-btn--sm bass-btn--primary btn-go-listing-pdf" data-room="${Bass.escapeHtml(a.room_no)}" style="min-height:40px;white-space:nowrap;">📄 PDF 작성 · 매물 등록 →</button>
          </div>
        `).join("")}
      </div>` : "";
    const coAlerts = (S.dash.checkouts || []).filter((x) => ["DISPUTED", "REQUESTED", "AGREED"].includes(x.status));
    const coHtml = coAlerts.length ? `
      <div class="bass-card" style="padding:10px 12px;margin-bottom:12px;border-color:var(--warn);">
        <div style="font-weight:800;font-size:14px;margin-bottom:4px;">🚪 퇴실 정산 확인 필요 ${coAlerts.length}건</div>
        ${coAlerts.map((x) => `<div style="font-size:13px;line-height:1.6;">${Bass.escapeHtml(x.room_no)}호 ${Bass.escapeHtml(x.tenant_name || "")} — ${x.status === "DISPUTED" ? `<strong>⚠️ 정산 이의/미수취 확인 필요</strong>: ${Bass.escapeHtml(x.dispute_note || "")}` : x.status === "AGREED" ? "✍️ 동의 완료 — 송금 후 표시하세요" : "🚪 퇴실 신청 — 정산서를 보내세요"} <button type="button" class="bass-btn bass-btn--sm co-open" data-room="${Bass.escapeHtml(x.room_no)}" style="min-height:40px;">열기</button> <button type="button" class="bass-btn bass-btn--sm btn-go-listing-pdf" data-room="${Bass.escapeHtml(x.room_no)}" style="min-height:40px;">📄 PDF · 매물 등록</button></div>`).join("")}
      </div>` : "";
    const connHtml = conn.length ? `
      <div class="bass-card" style="padding:10px 12px;margin-bottom:12px;border-color:var(--warn);">
        <div style="font-weight:800;font-size:14px;margin-bottom:4px;">🔗 내 페이지 연결 요청 ${conn.length}건</div>
        <div class="bass-muted" style="font-size:12.5px;line-height:1.5;margin-bottom:8px;">임차인의 <strong>장부 휴대폰으로 전화</strong>해, 임차인 화면에 보이는 <strong>4자리 확인번호</strong>를 들은 뒤 입력하세요. 임차인이 요청한 적이 없다고 하면 거절하세요.</div>
        ${conn.map((n) => `<div data-conn="${Bass.escapeHtml(n.id)}" style="padding:8px 0;border-top:1px solid var(--border);">
          <div style="font-size:14px;margin-bottom:6px;"><strong>${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)}</strong> · <a href="tel:${Bass.escapeHtml(n.tenant_phone)}">📞 ${Bass.escapeHtml(n.tenant_phone)}</a></div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;"><input class="bass-input cn-code" inputmode="numeric" maxlength="4" placeholder="확인번호 4자리" aria-label="확인번호" style="width:140px;min-height:44px;">
          <button class="bass-btn bass-btn--sm bass-btn--primary cn-ok" type="button" style="min-height:44px;">✅ 승인</button>
          <button class="bass-btn bass-btn--sm cn-no" type="button" style="min-height:44px;">거절</button></div></div>`).join("")}
      </div>` : "";
    const pend = S.dash.transfer_notices || [];
    const pendHtml = pend.length ? `
      <div class="bass-card r3-zone r3-zone--notice" style="padding:10px 12px;margin-bottom:12px;">
        <div style="font-weight:800;font-size:14px;margin-bottom:6px;">📨 이체 확인 요청 ${pend.length}건</div>
        ${pend.map((n) => `<button type="button" class="bass-btn todo-row" data-room="${Bass.escapeHtml(n.room_no)}" style="width:100%;min-height:48px;text-align:left;margin-bottom:6px;border-color:var(--warn);">${Bass.escapeHtml(n.room_no)}호 ${Bass.escapeHtml(n.tenant_name)} · ${fmtWon(n.amount)} 이체했다고 알림 → 확인하기</button>`).join("")}
      </div>` : "";
    elTabContent.innerHTML = `
      <div class="bass-card r3-zone r3-zone--sms" style="padding:8px 10px;margin-bottom:8px;">
        <label class="r3-lbl" for="bankSms" style="margin:0 0 6px;font-weight:800;">🏦 은행 입금 문자 붙여넣기 <span class="bass-muted" style="font-weight:500;">— 호실·입금자 이름으로 호실을 자동으로 찾습니다</span></label>
        <textarea id="bankSms" class="bass-input" rows="3" placeholder="[은행] 11/13 201호 홍길동 440,000원 입금"></textarea>
        <button id="bankSmsGo" class="bass-btn bass-btn--primary" type="button" style="width:100%;min-height:40px;margin-top:6px;">⚡ 문자 읽고 수납 처리</button>
        <div id="smsWarn"></div>
      </div>
      ${incomingCallsHtml}
      ${listingAlertsHtml}
      ${coHtml}
      ${connHtml}
      ${pendHtml}
      <div class="r3-lbl" style="margin:4px 0 8px;font-weight:800;">🏠 호실 <span class="bass-muted" style="font-weight:500;">— 호실 터치: 수납내역 · ⋯ 터치: 호실 메뉴</span></div>
      <div class="rl-list" id="roomList">${rooms.map(roomRow).join("") || '<div class="bass-muted">등록된 호실이 없습니다.</div>'}</div>
    `;
    const goRoom = (no) => { closeRoomContextMenu(); S.payFlash = null; openRoomModal(no); };
    elTabContent.querySelectorAll(".rl-row").forEach((b) => b.addEventListener("click", () => goRoom(b.dataset.room)));
    elTabContent.querySelectorAll(".rl-more").forEach((b) => b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openRoomContextMenu(b.dataset.room, b);
    }));
    elTabContent.querySelectorAll(".rl-wrap").forEach((w) => w.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      openRoomContextMenu(w.dataset.room, w.querySelector(".rl-more"), { x: ev.clientX, y: ev.clientY });
    }));
    elTabContent.querySelectorAll(".todo-row").forEach((b) => b.addEventListener("click", () => goRoom(b.dataset.room)));
    elTabContent.querySelectorAll(".co-open").forEach((b) => b.addEventListener("click", () => L.showMoveOutModal(b.dataset.room)));
    elTabContent.querySelectorAll(".btn-go-listing-pdf").forEach((b) => b.addEventListener("click", () => {
      S.listingTargetRoomNo = b.dataset.room;
      L.switchTab("rooms");
    }));
    elTabContent.querySelectorAll("[data-conn]").forEach((card) => {
      const id = card.dataset.conn;
      card.querySelector(".cn-ok").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(id)}/approve`, { method: "POST", body: { branch: S.currentBranch, code: card.querySelector(".cn-code").value.trim() } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        });
      });
      card.querySelector(".cn-no").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/connect-requests/${encodeURIComponent(id)}/reject`, { method: "POST", body: { branch: S.currentBranch } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
        });
      });
    });

    const smsWarn = document.getElementById("smsWarn");
    async function runSms(force, targetMonths) {
      const t = document.getElementById("bankSms").value.trim();
      if (!t) { Bass.updateAppAlert("warning", "붙여넣은 입금 문자가 없습니다."); return; }
      smsWarn.innerHTML = "";
      const res = await Bass.api(APP_ID, "/payments/sms-paste", {
        method: "POST",
        body: {
          branch: S.currentBranch,
          sms_text: t,
          force: !!force,
          pick_if_multi: true,
          target_months: targetMonths || null,
        },
      });
      if (res.needs_manual) {
        const p = res.parsed || {};
        smsWarn.innerHTML = `
          <div role="alert" style="margin-top:10px;padding:12px;border-radius:12px;border:1px solid var(--warn,#f59e0b);background:rgba(245,158,11,.12);font-size:14px;line-height:1.6;">
            <div style="font-weight:800;margin-bottom:4px;">✋ ${Bass.escapeHtml(res.room_no)}호 ${Bass.escapeHtml(res.tenant_name || "")} — 수작업이 필요합니다</div>
            <div>입금액 <strong>${fmtWon(p.amount || 0)}</strong> (${Bass.escapeHtml(dotDate(p.paid_date || ""))})이 월 납부액 <strong>${fmtWon(res.monthly)}</strong>과 다릅니다. 여러 달분이거나 일부 입금일 수 있어 <strong>자동으로 처리하지 않았습니다.</strong></div>
            <div class="bass-muted" style="font-size:13px;margin:4px 0 8px;">아래 <strong>[직접 수납]</strong>에서 처리할 미납 달을 체크하고 금액을 확인한 뒤 수납하세요. (밀린 금액 ${fmtWon(res.total_unpaid)}보다 많은 입금은 받을 수 없습니다.)</div>
            <button id="smsGoDirect" class="bass-btn bass-btn--primary" type="button" style="min-height:46px;width:100%;">💰 직접 수납에서 월 선택하기</button>
          </div>`;
        Bass.updateAppAlert("warning", res.message);
        document.getElementById("smsGoDirect").addEventListener("click", async () => {
          S.smsManualHint = { amount: Number(p.amount || 0), paid_date: p.paid_date || "", payer_name: p.payer_name || "", room_no: String(res.room_no) };
          S.payFlash = null;
          openRoomModal(res.room_no);
          let left = S.smsManualHint.amount;
          Array.from(document.querySelectorAll(".pm-chk")).forEach((el) => { el.checked = left > 0; if (left > 0) left -= Math.min(left, Number(el.dataset.unpaid || 0)); });
          const amt = document.getElementById("pmAmt"); if (amt) amt.value = S.smsManualHint.amount;
          const box = document.querySelector(".r3-subbox--direct"); if (box) box.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        return;
      }
      if (res.needs_month_pick) {
        const p = res.parsed || {};
        const ms = res.unpaid_months || [];
        const nowD = new Date(), curYm = `${nowD.getFullYear()}-${String(nowD.getMonth() + 1).padStart(2, "0")}`;
        smsWarn.innerHTML = `
          <div role="alert" style="margin-top:10px;padding:12px;border-radius:12px;border:1px solid var(--primary,#2563eb);background:rgba(37,99,235,.07);font-size:14px;line-height:1.55;">
            <div style="font-weight:800;margin-bottom:4px;">📅 ${Bass.escapeHtml(res.room_no)}호 ${Bass.escapeHtml(res.tenant_name || "")} — 어느 달 입금으로 처리할까요?</div>
            <div class="bass-muted" style="font-size:13px;margin-bottom:8px;">
              입금액 <strong>${fmtWon(p.amount || 0)}</strong> (${Bass.escapeHtml(dotDate(p.paid_date || ""))}) · 밀린 달이 여러 개이므로 반영할 달을 체크해 주세요.
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;margin-bottom:10px;">
              ${ms.map((m) => `
                <label class="check-row" style="display:flex;align-items:center;gap:8px;min-height:42px;padding:6px 8px;border-radius:8px;background:#fff;border:1px solid var(--border,#e2e8f0);cursor:pointer;">
                  <input type="checkbox" class="sms-ym-chk" value="${Bass.escapeHtml(m.ym)}">
                  <span><strong>${Bass.escapeHtml(m.ym)}분</strong>${m.ym === curYm ? ' <span class="bass-badge bass-badge--info" style="font-size:12px;">이달 분</span>' : ""} · ${m.phase === "DUE_SOON" ? "납부 예정" : "미납"} ${fmtWon(m.unpaid)}</span>
                </label>
              `).join("")}
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <button id="smsMonthFix" class="bass-btn" type="button" style="flex:1;min-width:120px;min-height:46px;">✏️ 문자 수정</button>
              <button id="smsMonthGo" class="bass-btn bass-btn--primary" type="button" style="flex:1;min-width:160px;min-height:46px;">✅ 선택한 달로 입금 처리</button>
            </div>
          </div>`;
        Bass.updateAppAlert("warning", res.message);
        document.getElementById("smsMonthFix").addEventListener("click", () => { smsWarn.innerHTML = ""; document.getElementById("bankSms").focus(); });
        document.getElementById("smsMonthGo").addEventListener("click", function () {
          const picked = Array.from(smsWarn.querySelectorAll(".sms-ym-chk:checked")).map((el) => el.value);
          if (!picked.length) { Bass.updateAppAlert("warning", "반영할 달을 하나 이상 체크해 주세요."); return; }
          Bass.withLock(this, () => runSms(!!force, picked));
        });
        return;
      }
      if (res.needs_confirm) {
        const p = res.parsed || {}, bn = res.by_name || {};
        smsWarn.innerHTML = `
          <div role="alert" style="margin-top:10px;padding:10px 12px;border-radius:12px;border:1px solid var(--warn);background:rgba(234,179,8,.14);font-size:14px;line-height:1.55;">
            <div style="font-weight:800;margin-bottom:4px;">⚠️ 문자 내용이 장부와 다릅니다</div>
            ${res.issues.map((i) => `<div>· ${Bass.escapeHtml(i.msg)}</div>`).join("")}
            <div class="bass-muted" style="font-size:13px;margin:6px 0;">읽은 값: ${p.room_no ? Bass.escapeHtml(p.room_no) + "호 " : "호실 없음 "}${Bass.escapeHtml(p.payer_name || "")} ${fmtWon(p.amount || 0)}<br>문자에서 호실·금액·이름이 잘못 읽혔다면 원본 문자를 수정하거나 송금자와 통화해 확인한 뒤 다시 처리하세요. 그대로 진행하면 <strong>입금자명 기준</strong>으로 ${Bass.escapeHtml(bn.room_no || "")}호 ${Bass.escapeHtml(bn.tenant_name || "")}님 계약에 반영합니다.</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <button id="smsFix" class="bass-btn" type="button" style="flex:1;min-width:120px;min-height:46px;">✏️ 원본 문자 고치기</button>
              <button id="smsForce" class="bass-btn bass-btn--primary" type="button" style="flex:1;min-width:160px;min-height:46px;">그대로 진행 (${Bass.escapeHtml(bn.room_no || "")}호 ${Bass.escapeHtml(bn.tenant_name || "")})</button>
            </div>
          </div>`;
        Bass.updateAppAlert("warning", res.message);
        document.getElementById("smsFix").addEventListener("click", () => { smsWarn.innerHTML = ""; const ta = document.getElementById("bankSms"); ta.focus(); });
        document.getElementById("smsForce").addEventListener("click", function () { Bass.withLock(this, () => runSms(true)); });
        return;
      }
      await doneSms(res);
    }
    document.getElementById("bankSmsGo").addEventListener("click", function () {
      Bass.withLock(this, () => runSms(false));
    });
    async function doneSms(res) {
      Bass.updateAppAlert(res.level || "success", res.message);
      const pay = res.payment || {};
      const rn = pay.room_no || S.selRoomNo;
      const remain = (res.ledger && res.ledger.total_unpaid) || 0;
      S.receiptMsg[rn] = receiptText(pay, remain);
      const note = res.forced_by_name ? " — 입금자명 기준으로 반영" : "";
      S.payFlash = { no: rn, text: `${rn}호 ${fmtWon(pay.total_amount || 0)} 수납 처리 완료 (${dotDate(pay.paid_date || "")})${note}` };
      S.selRoomNo = rn;
      await L.refresh();
      openRoomModal(rn);
      await sendReceiptSms(rn, pay, remain);
    }
    renderRoomPanel();
    if (S.autoRoom) { const a0 = S.autoRoom; S.autoRoom = ""; if (roomOf(a0)) openRoomModal(a0); }
  }

  // 📥 납부현황 엑셀(CSV): 내려받기(백업·엑셀 편집) / 올리기(미리보기 → 확인 → 반영). 반영 전에 서버가 자동 백업한다.
  function showLedgerCsvModal() {
    const yr = Number((S.dash.today || "").slice(0, 4)) || new Date().getFullYear();
    const body = openModal("📥 납부현황 엑셀(CSV)", `
      <div class="bass-muted" style="font-size:13px;line-height:1.6;margin-bottom:8px;">호실×월 납부일·금액 표를 엑셀로 받거나, 엑셀 장부를 올려 한꺼번에 기록합니다. 파일에는 <strong>입주자 이름</strong>이 들어 있으니 안전하게 보관하세요.</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;">
        <select id="lcYear" class="bass-select" style="width:110px;min-height:44px;">${[yr + 1, yr, yr - 1, yr - 2].map((y) => `<option value="${y}" ${y === yr ? "selected" : ""}>${y}년</option>`).join("")}</select>
        <button id="lcDown" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">⬇️ 내려받기</button>
      </div>
      <div class="r3-lbl">엑셀(CSV) 올리기</div>
      <input id="lcFile" type="file" accept=".csv,text/csv" class="bass-input" style="min-height:44px;">
      <div id="lcOut" style="margin-top:10px;"></div>`);
    const $ = (id) => body.querySelector("#" + id);
    let csvText = "";
    $("lcDown").addEventListener("click", () => downloadWithAuth(`/ledger/export-csv?branch=${encodeURIComponent(S.currentBranch)}&year=${$("lcYear").value}`, `납부현황_${S.currentBranch}_${$("lcYear").value}.csv`));
    const statusKo = { new: "새로 기록", same: "이미 같음", diff: "다름", skip: "건너뜀" };
    const statusColor = { new: "var(--ok)", same: "var(--muted)", diff: "var(--warn)", skip: "var(--err)" };
    async function readText(file) {
      const buf = await file.arrayBuffer();
      let t = new TextDecoder("utf-8").decode(buf);
      if (t.includes("\uFFFD")) { try { t = new TextDecoder("euc-kr").decode(buf); } catch (_) {} }
      return t;
    }
    async function preview() {
      const year = Number($("lcYear").value);
      const d = await Bass.api(APP_ID, "/ledger/import/preview", { method: "POST", body: { branch: S.currentBranch, year, csv_text: csvText } });
      const c = d.counts;
      const attention = d.items.filter((i) => i.status === "diff" || i.status === "skip" || i.notes.length);
      const rows = attention.map((i) => `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:13.5px;"><strong>${Bass.escapeHtml(i.room_no)}호 ${Number(i.ym.slice(5))}월</strong> ${Bass.escapeHtml(i.date.slice(5).replace("-", "."))} ${fmtWon(i.amount)} <span style="color:${statusColor[i.status]};font-weight:700;">${statusKo[i.status]}</span>${i.reason ? ` · ${Bass.escapeHtml(i.reason)}` : ""}${i.existing ? `<br><span class="bass-muted">지금 앱: ${Bass.escapeHtml(i.existing.date.slice(5).replace("-", "."))} ${fmtWon(i.existing.amount)}</span>` : ""}${i.notes.map((n) => `<br><span class="bass-muted">· ${Bass.escapeHtml(n)}</span>`).join("")}</div>`).join("");
      const unread = d.issues.map((i) => `<div style="padding:5px 0;font-size:13px;color:var(--muted);">· ${Bass.escapeHtml(i.msg)}</div>`).join("");
      $("lcOut").innerHTML = `
        <div class="bass-card" style="padding:10px 12px;">
          <div style="font-weight:800;font-size:15px;margin-bottom:6px;">미리보기 — ${year}년 ${d.rows}호실</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:14px;"><span style="color:var(--ok);font-weight:800;">새로 기록 ${c.new}건</span><span>이미 같음 ${c.same}건</span><span style="color:var(--warn);font-weight:800;">다름 ${c.diff}건</span><span style="color:var(--err);">건너뜀 ${c.skip}건</span>${c.former ? `<span>이전 입주자 시기 ${c.former}건 포함</span>` : ""}<span>읽지 못한 칸 ${d.issues.length}곳</span></div>
        </div>
        ${rows || unread ? `<details style="margin-top:8px;" ${attention.length + d.issues.length <= 12 ? "open" : ""}><summary style="cursor:pointer;min-height:40px;font-weight:700;">확인이 필요한 칸 ${attention.length + d.issues.length}건 보기</summary><div style="max-height:34vh;overflow-y:auto;">${rows}${unread}</div></details>` : ""}
        ${c.diff ? `<label style="margin-top:8px;"><input id="lcReplace" type="checkbox"> '다름' ${c.diff}건은 <strong>파일 내용으로 바꾸기</strong> (끄면 지금 앱의 값을 그대로 둡니다)</label>` : ""}
        <div style="margin-top:10px;"><button id="lcApply" class="bass-btn bass-btn--primary" type="button" style="min-height:48px;width:100%;" ${c.new + c.diff === 0 ? "disabled" : ""}>✅ 확인했습니다 — 반영하기</button>
        <div class="bass-muted" style="font-size:12.5px;margin-top:6px;">반영하기 전에 전체 자료를 자동으로 백업합니다. 같은 파일을 다시 올려도 중복되지 않습니다.</div></div>`;
      const ap = $("lcApply");
      if (ap) ap.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const rep = $("lcReplace") ? $("lcReplace").checked : false;
          const r = await Bass.api(APP_ID, "/ledger/import/apply", { method: "POST", body: { branch: S.currentBranch, year, csv_text: csvText, replace_diffs: rep } });
          Bass.updateAppAlert("success", r.message);
          await L.refresh();
          closeModal();
        });
      });
    }
    $("lcFile").addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      if (f.size > 1000000) { Bass.updateAppAlert("warning", "파일이 너무 큽니다(1MB 이하)."); return; }
      csvText = await readText(f);
      $("lcOut").innerHTML = '<div class="bass-muted">읽는 중...</div>';
      try { await preview(); } catch (err) { $("lcOut").innerHTML = ""; Bass.showError(err); }
    });
    $("lcYear").addEventListener("change", () => { if (csvText) preview().catch(Bass.showError); });
  }

  Object.assign(L, {
    showLedgerStateModal, renderPayInline, reservationCardHtml,
    openRoomModal, renderRoomPanel, openRoomContextMenu, closeRoomContextMenu,
    renderLedgerTab, showLedgerCsvModal,
  });
})();
