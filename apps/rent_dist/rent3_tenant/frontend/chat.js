/* apps/rent3_tenant/frontend/chat.js — 임대인과 1:1 채팅 · 채팅창 퇴실정산 카드 · 초대/사업자 모달 */
(function () {
  "use strict";
  const T = window.R3T;
  if (!T) return;
  const { APP_ID, fmtWon } = T;
  const S = T.state;

  const tnTime = (iso) => (iso && iso.length >= 16 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)} ${iso.slice(11, 16)}` : "");

  // 채팅창 위 '퇴실 정산' 카드: 정산 내역 확인 → 반환받을 본인 명의 계좌·동의 서명 입력 → 송금 대기 → 수취 확인. (임대인이 게시한 정산서만 나온다)
  function drawTenantCheckoutCard(d) {
    const box = document.getElementById("tnMsgCo");
    if (!box) return;
    const co = d.checkout || null;
    const sig = co ? [co.id, co.status, co.tenant_checked_at || "", co.refund_amount, co.account_masked || ""].join("|") : "";
    if (box.dataset.drawn === "1" && sig === S.tnCoSig) return;          // 바뀐 게 없으면 다시 그리지 않는다(입력 중인 계좌번호를 지우지 않게)
    S.tnCoSig = sig;
    box.dataset.drawn = "1";
    if (!co) { box.innerHTML = ""; return; }
    const E = Bass.escapeHtml;
    const W = (n) => fmtWon(Number(n || 0));
    const pin = Boolean(window.R3Shell && R3Shell.isViaPin && R3Shell.isViaPin());
    const detail = `<a href="?tenant=${encodeURIComponent(S.selectedTenantId)}&svc=checkout">📄 정산 내역 자세히 보기</a>`;
    const pinNote = '<div class="bass-muted" style="font-size:12.5px;margin-top:6px;">🔐 간편 비밀번호로 들어온 상태에서는 제출할 수 없습니다. 아이디·비밀번호로 다시 로그인해 주세요.</div>';
    let body = "";
    if (co.status === "REQUESTED") {
      body = "<strong>🚪 퇴실 신청이 접수되었습니다</strong><br>임대인이 정산서를 작성해 게시하면 여기에 정산 카드가 나타납니다.";
    } else if (co.status === "REVISING") {
      body = "<strong>✏️ 정산서를 수정하고 있습니다</strong><br>임대인이 정산서를 수정하는 중이라 내용을 잠시 숨겼습니다. 게시되면 알려 드립니다.";
    } else if (co.status === "CALCULATED" && !co.tenant_checked_at) {
      body = `<strong>🚪 퇴실 정산서가 도착했습니다</strong> · 반환 예정액 <strong>${W(co.refund_amount)}</strong><br>${detail}<br>내용이 맞으면 확인해 주세요. 다르면 <strong>이의 제기</strong>로 협의할 수 있습니다.
      <div class="bass-row" style="gap:8px;margin-top:8px;flex-wrap:wrap;"><button id="tnCoCheck" class="bass-btn bass-btn--primary" type="button" ${pin ? "disabled" : ""} style="min-height:44px;">✅ 정산 내역 확인</button><button id="tnCoDispute" class="bass-btn" type="button" ${pin ? "disabled" : ""} style="min-height:44px;">⚠️ 이의 제기</button></div>${pin ? pinNote : ""}`;
    } else if (co.status === "CALCULATED") {
      body = `<strong>💳 반환 계좌를 입력해 주세요</strong> · 반환 예정액 <strong>${W(co.refund_amount)}</strong><br>정산 내역을 확인하셨습니다. 보증금을 돌려받을 <strong>본인(${E(d.tenant_name || "계약자")}) 명의 계좌</strong>를 입력하고 동의 서명을 하면, 임대인이 그 계좌로 송금합니다. ${detail}
      ${pin ? pinNote : `<div class="field-grid" style="margin-top:8px;">
        <input id="tnCoBank" class="bass-input" placeholder="반환받을 은행 + 계좌번호 (예: 토스 1000-22-334455)" autocomplete="off">
        <input id="tnCoHolder" class="bass-input" placeholder="예금주 (계약자 본인 ${E(d.tenant_name || "")})" autocomplete="off">
        <input id="tnCoSig" class="bass-input" value="${E(d.tenant_name || "")} (동의서명)" placeholder="동의 전자서명">
        <button id="tnCoAgree" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">✍️ 계좌 제출 · 동의 서명</button>
      </div><div class="bass-row" style="margin-top:6px;"><button id="tnCoDispute" class="bass-btn bass-btn--sm" type="button">⚠️ 이의 제기</button></div>`}`;
    } else if (co.status === "AGREED") {
      body = `<strong>✅ 반환 계좌를 제출했습니다</strong>${co.account_masked ? ` (${E(co.account_masked)})` : ""}<br>임대인이 반환액 <strong>${W(co.refund_amount)}</strong>을 송금하면 알려 드립니다. 그동안 ${detail}`;
    } else if (co.status === "PAID") {
      body = `<strong>💸 임대인이 ${W(co.refund_amount)}을 송금했다고 표시했습니다</strong><br>계좌에 입금되었는지 확인해 주세요. 표시 후 7일 안에 이의가 없으면 자동으로 확정됩니다.
      <div class="bass-row" style="gap:8px;margin-top:8px;flex-wrap:wrap;"><button id="tnCoReceived" class="bass-btn bass-btn--primary" type="button" ${pin ? "disabled" : ""} style="min-height:44px;">✅ 받았습니다</button><button id="tnCoDispute" class="bass-btn" type="button" ${pin ? "disabled" : ""} style="min-height:44px;">⚠️ 못 받았습니다</button></div>${pin ? pinNote : ""}`;
    } else if (co.status === "DISPUTED") {
      body = `<strong>⚠️ 이의가 접수되었습니다</strong><br>임대인과 이 채팅·통화로 협의해 주세요. 임대인이 정산서를 고쳐 다시 게시하면 알려 드립니다.`;
    }
    box.innerHTML = body ? `<div class="bass-card" style="padding:12px 14px;font-size:13.5px;line-height:1.7;border-color:var(--warn);">${body}</div>` : "";
    const act = (id, fn) => { const b = box.querySelector("#" + id); if (b) b.addEventListener("click", function () { Bass.withLock(this, fn); }); };
    const done = async (res) => { Bass.updateAppAlert("success", res.message); S.tnCoSig = ""; box.dataset.drawn = ""; await loadTenantMessages(); };
    act("tnCoCheck", async () => done(await Bass.api(APP_ID, `/checkout/${co.id}/confirm-checked`, { method: "POST", body: {} })));
    act("tnCoAgree", async () => done(await Bass.api(APP_ID, `/checkout/${co.id}/agree`, {
      method: "POST",
      body: {
        bank_account: box.querySelector("#tnCoBank").value,
        account_holder: box.querySelector("#tnCoHolder").value,
        signature: box.querySelector("#tnCoSig").value,
      },
    })));
    act("tnCoReceived", async () => done(await Bass.api(APP_ID, `/checkout/${co.id}/confirm-received`, { method: "POST", body: {} })));
    act("tnCoDispute", async () => {
      const note = window.prompt(co.status === "PAID" ? "입금을 받지 못한 이유를 적어 주세요." : "이의 내용을 적어 주세요(예: 청소비가 과하다 / 금액이 다르다).");
      if (!note) return;
      done(await Bass.api(APP_ID, `/checkout/${co.id}/dispute`, { method: "POST", body: { note } }));
    });
  }

  // ── 임대인과 채팅 (내 호실 1:1 대화 + 접어 둔 계약 협상 기록). 관리인 지원 문의·계약방 대화는 섞지 않는다 ──
  async function loadTenantMessages(keepComposer) {
    const box = document.getElementById("tnMsgList");
    if (!box || !S.selectedTenantId) return;
    const d = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(S.selectedTenantId)}/messages`);
    const neg = d.negotiation || { count: 0, items: [] };
    const callEl = document.getElementById("tnMsgCall");
    const lessorDigits = String(d.lessor_phone || "").replace(/\D/g, "");
    if (callEl) callEl.innerHTML = lessorDigits.length >= 9 ? `<a class="bass-btn bass-btn--sm bass-btn--primary" href="tel:${lessorDigits}" style="min-height:40px;">📞 ${Bass.escapeHtml(d.lessor_name || "임대인")}에게 전화 ${Bass.escapeHtml(d.lessor_phone)}</a>` : "";
    drawTenantCheckoutCard(d);
    const bubble = (m) => m.kind === "system"
      ? `<div class="cm-sys">${Bass.escapeHtml(m.body)} <span>${tnTime(m.created_at)}</span></div>`
      : `<div class="cm-b ${m.sender_role === "tenant" ? "is-mine" : ""}"><div><div class="cm-meta">${m.sender_role === "tenant" ? "나" : Bass.escapeHtml(m.sender_name || "임대인")} · ${tnTime(m.created_at)}</div>${Bass.escapeHtml(m.body)}</div></div>`;
    const nextCount = (d.messages || []).length;
    if (!keepComposer || nextCount !== S.tnMsgCount) {
      S.tnMsgCount = nextCount;
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.innerHTML = d.messages.map(bubble).join("") || '<div class="bass-muted" style="font-size:13px;">아직 대화가 없습니다. 궁금한 점을 남겨 보세요.</div>';
      if (atBottom || !keepComposer) box.scrollTop = box.scrollHeight;
    }
    const negBox = document.getElementById("tnMsgNeg");
    if (negBox && !keepComposer) {
      const digestLine = neg.purged ? (neg.purged_note || "보관 기간이 지나 삭제되었습니다.") : neg.digest_ok === true ? "✅ 체결 때 기록한 대화 기록 해시와 일치합니다" : neg.digest_ok === false ? "⚠️ 체결 때 기록한 해시와 달라졌습니다" : "";
      negBox.innerHTML = (neg.count || neg.purged) ? `<details style="margin-top:12px;"><summary class="bass-muted" style="cursor:pointer;font-size:13px;min-height:32px;">계약 협상 기록 ${neg.purged ? `(보관 기간 만료로 삭제됨 · 당시 ${neg.original_count}건)` : `${neg.count}건 보기`}</summary>
        <div class="bass-muted" style="font-size:12px;margin:4px 0;">${Bass.escapeHtml(digestLine)}</div>
        ${neg.items.map((m) => `<div style="font-size:12.5px;padding:4px 0;border-bottom:1px solid var(--border);">[${m.step}단계] ${Bass.escapeHtml(m.sender_role === "lessor" ? "임대인" : m.sender_role === "tenant" ? "나" : "안내")}: ${Bass.escapeHtml(m.body)}</div>`).join("")}</details>` : "";
    }
    clearInterval(S.tnMsgTimer);
    S.tnMsgTimer = setInterval(() => {
      if (S.svcView !== "messages" || !document.getElementById("tnMsgList")) { clearInterval(S.tnMsgTimer); return; }
      if (document.visibilityState === "hidden") return;
      loadTenantMessages(true).catch(() => {});
    }, 2500);
  }

  // 💌 집주인에게 안심계약 제안: 문안을 만들어 임차인의 폰으로 직접 보내게 한다(집주인 번호는 서버로 보내지 않는다).
  function openLessorInviteModal() {
    T.closeDrawer();
    const tid = S.selectedTenantId || (T.savedSession && (T.savedSession.tenant_id || T.savedSession.id));
    if (!tid) { Bass.updateAppAlert("warning", "로그인한 뒤 이용해 주세요."); return; }
    const E = Bass.escapeHtml;
    const body = R3.openModal("💌 집주인에게 안심계약 제안하기", "");
    let cur = null;
    const load = async (label) => {
      cur = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(tid)}/lessor-invite?label=${encodeURIComponent(label || "")}`);
      return cur;
    };
    const draw = () => {
      body.innerHTML = `
        <div style="font-size:13px;line-height:1.7;">
          마음에 드는 집의 집주인에게 <strong>직접 계약(중개수수료 없음)</strong>을 제안하는 문자입니다. 문자는 <strong>내 폰에서 내가 직접</strong> 보내고, 집주인의 번호·이름은 이 서비스로 전송되거나 저장되지 않습니다.
          <label class="r3-fld" style="margin:8px 0;">관심 있는 집 (선택 · 40자)
            <input id="liLabel" class="bass-input" maxlength="40" value="${E(cur.label)}" placeholder="예: 신림동 ○○빌라 201호" style="min-height:44px;">
          </label>
          <label class="r3-fld">보낼 문구 (직접 고칠 수 있어요)
            <textarea id="liMsg" class="bass-input" rows="9" style="font-size:13px;line-height:1.6;">${E(cur.message)}</textarea>
          </label>
          <div class="bass-muted" style="font-size:12.5px;margin-top:6px;">오늘 ${cur.sent_today}/${cur.daily_limit}회 보냄 · 내가 보낸 제안으로 가입한 집주인 <strong>${cur.joined}</strong>명<br>같은 집주인에게 반복해서 보내지 마세요.</div>
          <div class="bass-row" style="gap:8px;margin-top:10px;flex-wrap:wrap;">
            <button id="liSms" class="bass-btn bass-btn--primary" type="button" style="min-height:46px;">📱 문자로 보내기</button>
            <button id="liCopy" class="bass-btn" type="button" style="min-height:46px;">📋 문구 복사 (카톡·당근 채팅에 붙여넣기)</button>
          </div>
        </div>`;
      body.querySelector("#liLabel").addEventListener("change", async function () {
        await load(this.value);
        draw();
      });
      const sent = async () => {
        const r = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(tid)}/lessor-invite/sent`, { method: "POST", body: {} });
        cur.sent_today = r.sent_today;
      };
      body.querySelector("#liCopy").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          await sent();
          await R3.copyText(body.querySelector("#liMsg").value);
          Bass.updateAppAlert("success", "복사했습니다. 집주인에게 보낼 곳(문자·카톡·당근 채팅)에 붙여넣어 보내세요.");
          draw();
        });
      });
      body.querySelector("#liSms").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          await sent();
          const text = body.querySelector("#liMsg").value;
          await R3.copyText(text);
          // 받는 사람은 비워 둔다: 문자 앱에서 집주인 번호를 직접 고른다(번호를 이 서비스가 알 필요가 없다)
          const sep = /iPhone|iPad|iPod/i.test(navigator.userAgent) ? "&" : "?";
          if (/Android|iPhone|iPad/i.test(navigator.userAgent)) {
            const a = document.createElement("a");
            a.href = "sms:" + sep + "body=" + encodeURIComponent(text);
            a.click();
          } else {
            Bass.updateAppAlert("success", "문구를 복사했습니다. 휴대폰 문자 앱에 붙여넣어 집주인 번호로 보내세요.");
          }
          draw();
        });
      });
    };
    load("").then(draw).catch(Bass.showError);
  }

  // 🏢 사업자 임차인: 사업자등록증 촬영·선택(AI가 읽어 채움) + 세금계산서 받을 이메일. 번호가 없으면 세금계산서 대신 현금영수증을 요청한다.
  async function openTenantBizInfoModal() {
    T.closeDrawer();
    const E = Bass.escapeHtml;
    const cur = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(S.selectedTenantId)}/business-registration`);
    const b = cur.business || {};
    const body = R3.openModal("🏢 사업자 정보 · 세금계산서 요청", `
      <div style="font-size:13px;line-height:1.7;margin-bottom:8px;">사업자등록증을 올리면 번호·상호·대표자를 읽어 채웁니다(AI 읽기는 아래 동의 시에만). <strong>사업자등록번호가 없으면</strong> 세금계산서 대신 <strong>현금영수증</strong>을 임대인에게 요청하세요(☰ 임대인과 채팅).</div>
      ${cur.submitted_at ? `<div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">마지막 제출 ${E(String(cur.submitted_at).slice(0, 16).replace("T", " "))}</div>` : ""}
      <div class="bass-row" style="gap:8px;flex-wrap:wrap;margin:6px 0;"><button type="button" id="tbCam" class="bass-btn bass-btn--primary" style="min-height:48px;">📷 촬영하기</button><button type="button" id="tbPic" class="bass-btn" style="min-height:48px;">🖼 사진·PDF 선택</button></div>
      <input id="tbFileCam" type="file" accept="image/*" capture="environment" hidden><input id="tbFile" type="file" accept="image/*,application/pdf" hidden>
      <div id="tbPrev" class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">선택한 파일 없음</div>
      <label class="check-row" style="margin:4px 0;"><span style="font-size:12px;">사업자등록증을 AI로 읽어 채우는 것에 동의합니다.</span><input type="checkbox" id="tbConsent"></label>
      <div class="r3-grid" style="margin-top:6px;">
        <label class="r3-fld">사업자등록번호<input id="tbNo" class="bass-input" placeholder="123-45-67890" value="${E(b.business_number || "")}"></label>
        <label class="r3-fld">상호<input id="tbComp" class="bass-input" value="${E(b.company_name || "")}"></label>
        <label class="r3-fld">대표자<input id="tbCeo" class="bass-input" value="${E(b.ceo_name || "")}"></label>
        <label class="r3-fld">세금계산서 받을 이메일<input id="tbEmail" class="bass-input" type="email" value="${E(b.email || "")}"></label>
      </div>
      <label class="r3-fld" style="margin-top:6px;">사업장 주소<input id="tbAddr" class="bass-input" value="${E(b.business_address || "")}"></label>
      <button id="tbSubmit" class="bass-btn bass-btn--primary" type="button" style="min-height:48px;margin-top:10px;width:100%;">제출</button>`);
    let picked = null;
    const take = (inp) => { const f = inp.files && inp.files[0]; if (!f) return; picked = f; body.querySelector("#tbPrev").textContent = "📎 " + (f.name || "촬영한 사진"); };
    body.querySelector("#tbCam").addEventListener("click", () => body.querySelector("#tbFileCam").click());
    body.querySelector("#tbPic").addEventListener("click", () => body.querySelector("#tbFile").click());
    body.querySelector("#tbFileCam").addEventListener("change", (e) => take(e.target));
    body.querySelector("#tbFile").addEventListener("change", (e) => take(e.target));
    const toUrl = async (file) => {
      const raw = () => new Promise((res, rej) => { const rd = new FileReader(); rd.onload = () => res(rd.result); rd.onerror = rej; rd.readAsDataURL(file); });
      if (!/^image\/(jpeg|png|webp)$/.test(file.type) || !window.createImageBitmap) return raw();
      try {
        const bmp = await createImageBitmap(file);
        const k = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
        const cv = document.createElement("canvas");
        cv.width = Math.max(1, Math.round(bmp.width * k));
        cv.height = Math.max(1, Math.round(bmp.height * k));
        cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
        return cv.toDataURL("image/jpeg", 0.85);
      } catch (_) {
        return raw();
      }
    };
    body.querySelector("#tbSubmit").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const g = (id) => body.querySelector("#" + id).value.trim();
        const res = await Bass.api(APP_ID, `/myhome/${encodeURIComponent(S.selectedTenantId)}/business-registration`, {
          method: "POST",
          body: {
            attachment_data_url: picked ? await toUrl(picked) : "",
            consent: body.querySelector("#tbConsent").checked,
            business_number: g("tbNo"),
            company_name: g("tbComp"),
            ceo_name: g("tbCeo"),
            email: g("tbEmail"),
            business_address: g("tbAddr"),
          },
        });
        Bass.updateAppAlert("success", res.message);
        const bb = res.business || {};
        body.querySelector("#tbNo").value = bb.business_number || "";
        body.querySelector("#tbComp").value = bb.company_name || "";
        body.querySelector("#tbCeo").value = bb.ceo_name || "";
        body.querySelector("#tbAddr").value = bb.business_address || "";
        body.querySelector("#tbEmail").value = bb.email || "";
      });
    });
  }

  Object.assign(T, {
    drawTenantCheckoutCard,
    loadTenantMessages,
    openLessorInviteModal,
    openTenantBizInfoModal,
  });
})();
