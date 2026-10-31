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
  }

  function logout() {
    Bass.setToken("");
    go("index.html");
  }

  /** 첫 로그인(임시 비밀번호)이면 비밀번호 변경 카드를 보여 주고, 바꾼 뒤에 resolve 된다. */
  function passwordGate(info, mount) {
    if (!info.must_change_password) return Promise.resolve();
    const esc = Bass.escapeHtml;
    mount.innerHTML = `
      <form class="bass-card stack" id="pwForm" style="max-width:460px">
        <h2 style="margin:0">처음 오셨네요 — 비밀번호를 바꿔 주세요</h2>
        <p class="bass-muted" style="margin:0;font-size:13.5px">계약 링크에서 받은 임시 비밀번호는 첫 로그인에만 쓸 수 있습니다.
          앞으로 쓸 비밀번호를 정하면 ${esc(info.label)} 화면을 쓸 수 있습니다.</p>
        <div><label class="bass-label" for="pwCurrent">임시 비밀번호 (현재 비밀번호)</label>
          <input class="bass-input" id="pwCurrent" type="password" autocomplete="current-password" required></div>
        <div><label class="bass-label" for="pwNew">새 비밀번호 (6자 이상)</label>
          <input class="bass-input" id="pwNew" type="password" autocomplete="new-password" minlength="6" required></div>
        <div><label class="bass-label" for="pwNew2">새 비밀번호 확인</label>
          <input class="bass-input" id="pwNew2" type="password" autocomplete="new-password" minlength="6" required></div>
        <button class="bass-btn bass-btn--primary" id="btnPw" type="submit">비밀번호 바꾸고 계속</button>
      </form>`;
    mount.classList.remove("hide");
    return new Promise((resolve) => {
      const form = mount.querySelector("#pwForm");
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const val = (id) => form.querySelector(id).value;
        if (val("#pwNew") !== val("#pwNew2")) {
          Bass.updateAppAlert("diff", "새 비밀번호 두 칸이 서로 다릅니다.", "같은 비밀번호를 두 번 입력해 주세요.");
          return;
        }
        Bass.withLock(form.querySelector("#btnPw"), async () => {
          await Bass.api("rent4", "/account/password", { method: "POST", body: { current: val("#pwCurrent"), new: val("#pwNew") } });
          Bass.updateAppAlert("success", "비밀번호를 바꿨습니다.", "다음부터는 새 비밀번호로 로그인하세요.");
          mount.innerHTML = "";
          mount.classList.add("hide");
          resolve();
        }, "저장 중...");
      });
    });
  }

  /** 계약 링크 (상대방에게 보낼 주소). */
  function contractLink(token) {
    return new URL(`contract.html?t=${encodeURIComponent(token)}`, global.location.href).href;
  }

  async function copy(text) {
    try { await global.navigator.clipboard.writeText(text); return true; } catch (_) { return false; }
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
          c.status !== "cancelled" ? `<button class="bass-btn bass-btn--sm" data-act="relink" data-id="${esc(c.id)}">🔗 링크</button>` : "",
          c.status === "signed" ? `<button class="bass-btn bass-btn--sm bass-btn--primary" data-act="paid" data-id="${esc(c.id)}">💰 ${c.kind === "membership" ? "가맹비" : "잔금"} 확인</button>` : "",
          ["sent", "signed"].includes(c.status) ? `<button class="bass-btn bass-btn--sm bass-btn--ghost" data-act="cancel" data-id="${esc(c.id)}">취소</button>` : "",
        ].join(" ");
        return `<tr><td><b>${esc(target)}</b></td><td>${esc(c.party_name)}<br><small class="bass-muted">${esc(c.party_phone)}</small></td>
          <td style="white-space:normal;text-align:left">${esc(terms)}</td>
          <td><span class="bass-badge bass-badge--${STATUS_TONE[c.status] || "info"}">${esc(c.status_label)}</span>${c.username ? `<br><small><code>${esc(c.username)}</code></small>` : ""}</td>
          <td style="text-align:right">${acts}</td></tr>`;
      }).join("") : `<tr><td colspan="5" class="bass-muted">아직 발행한 계약이 없습니다.</td></tr>`}</tbody>`;

    if (table.dataset.bound) return;
    table.dataset.bound = "1";
    table.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      Bass.withLock(btn, async () => {
        const res = await call(`/${btn.dataset.id}/${btn.dataset.act}`);
        if (btn.dataset.act === "relink") await announceLink(res.token, "링크를 새로 만들었습니다. 이전 링크는 더 이상 열리지 않습니다.");
        if (btn.dataset.act === "paid") Bass.updateAppAlert("success", "납부를 확인해 계약이 체결되었습니다.",
          `상대방이 계약 링크를 다시 열면 아이디(${res.contract.username})와 임시 비밀번호가 보입니다.`);
        if (btn.dataset.act === "cancel") Bass.updateAppAlert("warning", "계약을 취소했습니다.", "취소된 링크는 열리지 않습니다.");
        await reload();
      });
    });
  }

  async function announceLink(token, lead) {
    const link = contractLink(token);
    const copied = await copy(link);
    Bass.updateAppAlert("success", lead || "계약 링크를 만들었습니다.",
      `${copied ? "링크를 복사했습니다. " : ""}문자·카카오톡 등으로 상대방에게 보내세요: ${link}`, { timeout: 0 });
  }

  global.Rent4 = { me, guard, logout, go, passwordGate, contractLink, copy, contractTable, announceLink };
})(window);
