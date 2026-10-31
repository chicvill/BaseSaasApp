/* apps/rent4/frontend/rent4.js — Rent4 공통: 로그인 세션 · 역할별 페이지 가드 · 상단 사용자 표시
 *
 *   const me = await Rent4.guard(["lessor", "admin"]);   // 로그인 안 했으면 index.html, 다른 역할이면 그 역할의 첫 페이지로
 *   Rent4.logout();
 */
(function (global) {
  "use strict";

  async function me() {
    return Bass.api("rent4", "/me");
  }

  function go(page) {
    global.location.replace(page);
  }

  async function guard(kinds) {
    if (!Bass.token()) { go("index.html"); return new Promise(() => {}); }
    let info;
    try {
      info = await me();
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        Bass.setToken("");
        go("index.html");
        return new Promise(() => {});
      }
      throw err;
    }
    if (!kinds.includes(info.kind)) { go(info.home); return new Promise(() => {}); }
    renderWho(info);
    return info;
  }

  function renderWho(info) {
    const el = document.getElementById("who");
    if (!el) return;
    const where = info.kind === "tenant" ? `${info.building.name} ${info.room}호`
      : info.kind === "lessor" ? info.building.name : "전체 건물";
    el.textContent = `${info.label} · ${info.name || info.username} · ${where}`;
    const out = document.getElementById("btnLogout");
    if (out) {
      out.classList.remove("hide");
      out.addEventListener("click", logout);
    }
    const pw = document.getElementById("btnPassword");
    if (pw && !info.must_change_password) {
      pw.classList.remove("hide");
      pw.addEventListener("click", () => openPasswordDialog(info));
    }
  }

  function logout() {
    Bass.setToken("");
    go("index.html");
  }

  /** 비밀번호 변경 폼 (첫 로그인 카드와 평소 변경 창이 같이 쓴다). 바꾸고 나면 onDone 을 부른다. */
  function passwordForm({ title, intro, currentLabel, submitLabel, minLen, firstTime }, onDone) {
    const esc = Bass.escapeHtml;
    const form = document.createElement("form");
    form.className = "stack";
    form.innerHTML = `
      <h2 style="margin:0;font-size:19px">${esc(title)}</h2>
      ${intro ? `<p class="bass-muted" style="margin:0;font-size:13.5px">${esc(intro)}</p>` : ""}
      ${firstTime ? "" : `<div><label class="bass-label">${esc(currentLabel)}
        <input class="bass-input" name="current" type="password" autocomplete="current-password" required></label></div>`}
      <div><label class="bass-label">${firstTime ? "비밀번호" : "새 비밀번호"} (${minLen}자 이상)
        <input class="bass-input" name="new" type="password" autocomplete="new-password" minlength="${minLen}" required></label></div>
      <div><label class="bass-label">${firstTime ? "비밀번호" : "새 비밀번호"} 확인
        <input class="bass-input" name="new2" type="password" autocomplete="new-password" minlength="${minLen}" required></label></div>
      <div class="bass-row"><button class="bass-btn bass-btn--primary" type="submit">${esc(submitLabel)}</button></div>`;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = (n) => (form.elements[n] ? form.elements[n].value : "");
      if (v("new") !== v("new2")) {
        Bass.updateAppAlert("diff", "비밀번호 두 칸이 서로 다릅니다.", "같은 비밀번호를 두 번 입력해 주세요.");
        return;
      }
      Bass.withLock(form.querySelector("button[type=submit]"), async () => {
        await Bass.api("rent4", "/account/password", { method: "POST", body: { current: v("current"), new: v("new") } });
        Bass.updateAppAlert("success", firstTime ? "비밀번호를 설정했습니다." : "비밀번호를 바꿨습니다.",
          "다음부터는 아이디와 이 비밀번호로 로그인하세요.");
        form.reset();
        onDone();
      }, "저장 중...");
    });
    return form;
  }

  /** 링크로 처음 들어온 사용자면 비밀번호 설정 카드를 보여 주고, 정한 뒤에 resolve 된다. */
  function passwordGate(info, mount) {
    if (!info.must_change_password) return Promise.resolve();
    return new Promise((resolve) => {
      const card = document.createElement("div");
      card.className = "bass-card";
      card.style.maxWidth = "460px";
      card.append(passwordForm({
        title: "환영합니다 — 비밀번호를 설정해 주세요",
        intro: `아이디는 ${info.username} 입니다. 앞으로 로그인할 때 쓸 비밀번호를 정하면 ${info.label} 화면이 열립니다.`,
        currentLabel: "", submitLabel: "비밀번호 설정하고 시작", firstTime: true,
        minLen: info.kind === "admin" ? 8 : 6,
      }, () => {
        mount.replaceChildren();
        mount.classList.add("hide");
        const pw = document.getElementById("btnPassword");
        if (pw) { pw.classList.remove("hide"); pw.addEventListener("click", () => openPasswordDialog(info)); }
        resolve();
      }));
      mount.replaceChildren(card);
      mount.classList.remove("hide");
    });
  }

  /** 평소 비밀번호 변경 창 (상단 🔒 비밀번호 버튼). */
  function openPasswordDialog(info) {
    let dlg = document.getElementById("pwDialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "pwDialog";
      dlg.className = "detail";
      document.body.append(dlg);
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    }
    const form = passwordForm({
      title: "비밀번호 변경", intro: "", currentLabel: "현재 비밀번호", submitLabel: "바꾸기",
      minLen: info.kind === "admin" ? 8 : 6,
    }, () => dlg.close());
    const close = document.createElement("button");
    close.type = "button";
    close.className = "bass-btn bass-btn--ghost";
    close.textContent = "닫기";
    close.addEventListener("click", () => dlg.close());
    form.querySelector(".bass-row").append(close);
    dlg.replaceChildren(form);
    dlg.showModal();
  }

  /** 계약 링크 (상대방에게 보낼 주소). */
  function contractLink(token) {
    return new URL(`contract.html?t=${encodeURIComponent(token)}`, global.location.href).href;
  }

  /**
   * 클립보드 복사. 클릭 직후 동기 방식(숨은 입력칸 선택 + execCommand)을 먼저 쓰고,
   * 안 되면 클립보드 API 를 쓴다. 클립보드 API 는 권한 대기로 응답이 늦거나 막히는 브라우저가 있다.
   */
  async function copy(text) {
    const host = document.querySelector("dialog[open]") || document.body;   // 모달이 열려 있으면 그 안에서만 선택 가능
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0";
    host.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    ta.remove();
    if (ok) return true;
    try {
      await Promise.race([global.navigator.clipboard.writeText(text),
        new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 1500))]);
      return true;
    } catch (_) {
      return false;
    }
  }

  const STATUS_TONE = { sent: "warn", signed: "info", active: "ok", cancelled: "err" };

  /** 계약 목록 표 + [링크 · 납부 확인 · 취소] 버튼. apiPath 는 "/admin/contracts" 또는 "/lessor/contracts". */
  function contractTable(table, items, { call, reload, partyLabel }) {
    const esc = Bass.escapeHtml;
    const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;
    table.innerHTML = `<thead><tr><th>대상</th><th>${esc(partyLabel)}</th><th>조건</th><th>상태</th><th></th></tr></thead>
      <tbody>${items.length ? items.map((c) => {
        const target = c.kind === "membership" ? `${c.building_name} (${c.building_code})` : `${c.room}호`;
        const terms = c.kind === "membership" ? `가맹비 ${won(c.fee)}` : `보증금 ${won(c.deposit)} · 월 ${won(c.rent + c.mgmt)} · ${c.start || ""}`;
        const acts = [
          c.status === "signed" ? `<button class="bass-btn bass-btn--sm bass-btn--primary" data-act="paid" data-id="${esc(c.id)}">💰 ${c.kind === "membership" ? "가맹비" : "잔금"} 확인</button>` : "",
          ["sent", "signed"].includes(c.status) ? `<button class="bass-btn bass-btn--sm bass-btn--ghost" data-act="cancel" data-id="${esc(c.id)}">취소</button>` : "",
          c.status === "active" ? `<button class="bass-btn bass-btn--sm" data-act="reset" data-id="${esc(c.id)}" title="요청·분실·계약 정리·처음 안내 등: 지금 비밀번호를 막고 새 링크로 다시 설정">🔑 비밀번호 초기화</button>` : "",
        ].join(" ");
        return `<tr><td><b>${esc(target)}</b></td><td>${esc(c.party_name)}<br><small class="bass-muted">${esc(c.party_phone)}</small></td>
          <td style="white-space:normal;text-align:left">${esc(terms)}</td>
          <td><span class="bass-badge bass-badge--${STATUS_TONE[c.status] || "info"}">${esc(c.status_label)}</span>${c.username ? `<br><small><code>${esc(c.username)}</code></small>` : ""}</td>
          <td style="text-align:right">${acts}</td></tr>`;
      }).join("") : `<tr><td colspan="5" class="bass-muted">아직 발행한 계약이 없습니다.</td></tr>`}</tbody>`;

    table._items = Object.fromEntries(items.map((c) => [c.id, c]));
    if (table.dataset.bound) return;
    table.dataset.bound = "1";
    table.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const c = table._items[btn.dataset.id];
      Bass.withLock(btn, async () => {
        const res = await call(`/${btn.dataset.id}/${btn.dataset.act}`);
        if (btn.dataset.act === "paid") sendLink({
          title: "계약이 체결되었습니다", name: c.party_name, phone: c.party_phone,
          message: `[Rent4] ${c.party_name}님, ${contractTitle(c)}이 체결되었습니다. 로그인 페이지에서 휴대폰 번호와 계약 때 정한 비밀번호로 로그인한 뒤 '시작하기'를 누르세요.`,
        });
        if (btn.dataset.act === "cancel") Bass.updateAppAlert("warning", "계약을 취소했습니다.", "취소된 링크는 열리지 않습니다.");
        if (btn.dataset.act === "reset") sendLink({
          title: `${res.contract.username} 비밀번호를 초기화했습니다`, name: c.party_name, phone: c.party_phone,
          link: contractLink(res.token),
          message: `[Rent4] ${c.party_name}님, 비밀번호가 초기화되었습니다. 아래 링크를 열고 '시작하기'를 누르면 바로 로그인되어 비밀번호를 다시 설정할 수 있습니다.`,
          note: "이전 비밀번호와 이전 링크는 이미 막혔습니다.",
        });
        await reload();
      });
    });
  }

  function contractTitle(c) {
    return c.kind === "membership" ? `${c.building_name} 가맹 계약` : `${c.building_name} ${c.room}호 임대차 계약`;
  }

  /** 계약 발행 · 링크 재발급 뒤 상대방에게 계약 링크 보내기. */
  function sendContract(c, token, title) {
    sendLink({
      title: title || `${contractTitle(c)}을 발행했습니다`, name: c.party_name, phone: c.party_phone,
      link: contractLink(token),
      message: `[Rent4] ${c.party_name}님, ${contractTitle(c)}서입니다. 아래 링크를 열어 먼저 비밀번호를 정한 뒤 계약 내용을 확인하고 동의해 주세요. 링크를 잃어버려도 로그인 페이지에서 휴대폰 번호와 그 비밀번호로 이어서 진행할 수 있습니다.`,
    });
  }

  /** 임차인 안내 링크 (Excel 로 등록된 기존 임차인). */
  function inviteLink(token) {
    return new URL(`invite.html?t=${encodeURIComponent(token)}`, global.location.href).href;
  }

  /**
   * 링크 보내기 창: 안내 문구 + [📤 카카오톡 등으로 공유] [💬 문자 보내기] [📋 복사].
   * 휴대폰에서는 공유 버튼이 카카오톡·메시지 앱 선택 창을 연다. 문구는 열자마자 클립보드에도 복사한다.
   */
  async function sendLink({ title, name, phone, link, message, note }) {
    const esc = Bass.escapeHtml;
    const text = link ? `${message}\n${link}` : message;
    const copied = await copy(text);
    let dlg = document.getElementById("sendDialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "sendDialog";
      dlg.className = "detail";
      document.body.append(dlg);
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    }
    const digits = String(phone || "").replace(/\D/g, "");
    const canShare = typeof global.navigator.share === "function";
    dlg.innerHTML = `
      <div class="stack">
        <h2 style="margin:0;font-size:18px">📨 ${esc(title)}</h2>
        <div class="bass-muted" style="font-size:13px">${esc(name)}님${digits ? ` (${esc(phone)})` : ""}에게 아래 내용을 보내 주세요.${copied ? " 이미 복사해 두었습니다." : ""}</div>
        <textarea class="bass-input" readonly style="min-height:120px;font-size:13px">${esc(text)}</textarea>
        ${link ? `<div>
          <label class="bass-label">링크만 (브라우저 주소창에는 이 링크만 붙여 넣으세요)</label>
          <div class="bass-row" style="flex-wrap:nowrap">
            <input class="bass-input" readonly value="${esc(link)}" style="font-size:12.5px;min-width:0" onfocus="this.select()">
            <button class="bass-btn bass-btn--sm" data-send="copy-link" style="white-space:nowrap">🔗 링크만 복사</button>
            <a class="bass-btn bass-btn--sm" href="${esc(link)}" target="_blank" rel="noopener" style="white-space:nowrap">↗ 열기</a>
          </div></div>` : ""}
        ${note ? `<div style="font-size:12.5px;color:var(--warn)">${esc(note)}</div>` : ""}
        <div class="bass-row">
          ${canShare ? `<button class="bass-btn bass-btn--primary" data-send="share">📤 카카오톡 등으로 공유</button>` : ""}
          ${digits ? `<a class="bass-btn ${canShare ? "" : "bass-btn--primary"}" href="sms:${digits}?&body=${encodeURIComponent(text)}">💬 문자 보내기</a>` : ""}
          <button class="bass-btn" data-send="copy">📋 문구 전체 복사</button>
          <button class="bass-btn bass-btn--ghost" data-send="close">닫기</button>
        </div>
        ${canShare ? "" : `<div class="bass-muted" style="font-size:12px">문구 전체는 카카오톡 PC·문자 앱에 붙여 넣으세요 (받는 쪽에서 링크가 눌립니다). 휴대폰에서 열면 카카오톡 공유 버튼이 나타납니다.</div>`}
      </div>`;
    const flash = (btn, ok) => {
      const original = btn.textContent;
      btn.textContent = ok ? "✓ 복사됨" : "복사 실패";
      setTimeout(() => { btn.textContent = original; }, 1500);
    };
    dlg.onclick = async (e) => {
      const btn = e.target.closest("[data-send]");
      const act = btn?.dataset.send;
      if (act === "close") dlg.close();
      if (act === "copy") flash(btn, await copy(text));
      if (act === "copy-link") flash(btn, await copy(link));
      if (act === "share") {
        try { await global.navigator.share({ title: "Rent4", text }); dlg.close(); } catch (_) { /* 사용자가 취소 */ }
      }
    };
    if (!dlg.open) dlg.showModal();
  }

  /** 비밀번호 칸마다 [보기/숨기기] 버튼을 붙인다. 나중에 생기는 칸(비밀번호 변경 창 등)도 자동으로. */
  function addRevealToggle(input) {
    if (input.dataset.reveal) return;
    input.dataset.reveal = "1";
    const wrap = document.createElement("span");
    wrap.className = "pw-wrap";
    input.replaceWith(wrap);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    btn.textContent = "보기";
    btn.setAttribute("aria-label", "비밀번호 보기");
    btn.setAttribute("aria-pressed", "false");
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.textContent = show ? "숨기기" : "보기";
      btn.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 보기");
      btn.setAttribute("aria-pressed", String(show));
      input.focus();
    });
    wrap.append(input, btn);
  }

  function enhancePasswords(root) {
    root.querySelectorAll?.('input[type="password"]').forEach(addRevealToggle);
  }

  function watchPasswords() {
    enhancePasswords(document);
    new MutationObserver((records) => records.forEach((r) => r.addedNodes.forEach((n) => {
      if (n.nodeType !== 1) return;
      if (n.matches('input[type="password"]')) addRevealToggle(n);
      else enhancePasswords(n);
    }))).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchPasswords);
  else watchPasswords();

  global.Rent4 = { me, guard, logout, go, passwordGate, contractLink, inviteLink, copy, contractTable, sendContract, sendLink };
})(window);
