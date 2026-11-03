/* rent5/static/contract.js — 계약방: 위는 임대인 · 임차인이 함께 쓰는 대화창, 아래는 지금 내가 할 일.
   단계마다 말풍선이 남고, 상대방이 하면 몇 초 안에 이 화면에 나타난다. */
(async function () {
  "use strict";
  const { esc, won, when, tel } = R5;
  const $ = (id) => document.getElementById(id);
  const id = new URLSearchParams(location.search).get("id") || "";

  const user = await R5.requireLogin();
  R5.nav(user);
  if (!id) return R5.notify("warn", "계약방을 골라 주세요.", "내 화면의 계약 목록에서 열어 주세요.");

  const ROLE_LABEL = { landlord: "임대인", tenant: "임차인" };
  const REPORT_NOTE = "보증금 6천만 원 또는 월세 30만 원을 넘는 계약은 계약일부터 30일 안에 주택 임대차 계약 신고를 해야 합니다(주민센터 또는 부동산거래관리시스템).";
  const LEGACY_NOTICE = { apply: "임차인이 계약을 신청했습니다", offer: "임대인이 조건을 제시했습니다", tenant_sign: "임차인이 서명했습니다",
    landlord_sign: "임대인이 서명 — 계약이 성립했습니다", deposit: "계약금 입금이 확인되었습니다", balance: "잔금 입금이 확인되었습니다 — 입주 처리", cancel: "계약이 취소되었습니다" };
  const PATH = { confirm_landlord_info: "confirm-landlord-info", confirm_tenant_info: "confirm-tenant-info", show_registry: "registry",
    confirm_registry: "confirm-registry", send_account: "account", pay: "pay", cancel: "cancel" };
  const SILENT = ["cancel", "document"];            // 차례를 뜻하지 않는 동작
  let c = null;

  const other = () => (c.role === "landlord" ? "tenant" : "landlord");
  const isLegacy = () => c.steps[0] === "requested";
  const myTurn = () => c.actions.some((a) => !SILENT.includes(a));
  const live = () => c.actions.includes("cancel");           // 아직 진행 중
  const dl = (rows) => `<dl class="kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const field = (fid, label, attrs = "", value = "") => `<div><label for="${fid}">${esc(label)}</label><input id="${fid}" value="${esc(value)}" ${attrs}></div>`;
  const mark = (at, who) => at ? `<span class="ok-line">✔ ${who} 확인 · ${esc(when(at))}</span>` : `<span class="wait-line">⏳ ${who}의 확인을 기다리는 중</span>`;

  // ── 대화창: 말풍선(대화 · 단계)과 자동으로 뜨는 정보 카드를 시간순으로 ──
  function infoCards(at) {
    const t = c.terms, l = c.landlord, n = c.tenant;
    const landlordCard = `<strong>임대인 · 매물 정보</strong>` + dl([
      ["임대인", `${esc(l.name)} ${tel(l.phone)}`], ["매물", esc(t.address)], ["구분", esc(t.lease_type)], ["보증금", won(t.deposit)],
      t.lease_type === "전세" ? null : ["월세 · 관리비", `${won(t.rent)} + ${won(t.mgmt)}`],
      ["입주 가능", esc(t.start || "협의")], ["계약금(10%)", won(t.down_payment)]]) + mark(c.landlord_info_ok_at, "임차인");
    const tenantCard = `<strong>임차인 정보 (회원가입 정보)</strong>` + dl([
      ["이름", esc(n.name)], ["휴대폰", tel(n.phone)]]) + mark(c.tenant_info_ok_at, "임대인");
    return [{ at, kind: "card", role: "landlord", mine: c.role === "landlord", html: landlordCard },
            { at, kind: "card", role: "tenant", mine: c.role === "tenant", html: tenantCard }];
  }

  function timeline() {
    const items = [];
    c.events.forEach((e) => {
      if (e.text) items.push({ at: e.at, kind: "step", role: e.role, mine: e.mine, text: e.text });
      else items.push({ at: e.at, kind: "notice", text: LEGACY_NOTICE[e.what] || e.what });
      if (e.what === "reserve") infoCards(e.at).forEach((card) => items.push(card));
    });
    if (c.message) {
      const apply = c.events.find((e) => e.what === "apply");
      items.push({ at: apply ? apply.at : c.created_at, kind: "msg", role: "tenant", mine: c.role === "tenant", text: c.message });
    }
    (c.chat || []).forEach((m) => items.push({ at: m.at, kind: "msg", role: m.role, mine: m.mine, text: m.text }));
    return items.sort((a, b) => String(a.at).localeCompare(String(b.at)));
  }

  function chatHtml() {
    let lastDay = "";
    return timeline().map((it) => {
      const day = String(it.at).slice(0, 10);
      const sep = day !== lastDay ? `<div class="day">${esc(day)}</div>` : "";
      lastDay = day;
      if (it.kind === "notice") return `${sep}<div class="notice">${esc(it.text)}</div>`;
      const body = it.kind === "card" ? `<div class="bubble cardb">${it.html}</div>`
        : `<div class="bubble${it.kind === "step" ? " stepb" : ""}">${esc(it.text)}</div>`;
      return `${sep}<div class="msg ${it.mine ? "mine" : "theirs"} ${it.role}">
        ${it.mine ? "" : `<span class="name">${ROLE_LABEL[it.role]} ${esc(c[it.role].name)}</span>`}
        ${body}<time>${esc(String(it.at).slice(11, 16))}</time></div>`;
    }).join("");
  }

  function renderChat() {
    const box = $("chatScroll"), log = $("chatLog");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const first = !log.dataset.ready;
    log.innerHTML = chatHtml();
    log.dataset.ready = "1";
    if (first || nearBottom) box.scrollTop = box.scrollHeight;
  }

  let flashed = false;
  function renderInfo() {
    $("title").textContent = c.terms ? c.terms.address : `${c.listing.address} ${c.listing.room || ""}`.trim();
    $("roleBadge").textContent = c.role === "landlord" ? "나는 임대인" : "나는 임차인";
    $("statusLine").textContent = c.status_label;
    const idx = c.steps.indexOf(c.status);
    $("progress").innerHTML = c.status === "cancelled" ? "" : c.step_labels.map((label, i) =>
      `<li class="${i < idx || ["completed", "active"].includes(c.status) ? "done" : i === idx ? "now" : ""}">${esc(label)}</li>`).join("");

    const o = other();
    $("chatroom").className = `chatroom ${o}${flashed ? " flash" : ""}`;
    $("actions").className = `stack inline-actions ${c.role}`;
    flashed = false;
    $("chatHead").innerHTML = `<h2>${ROLE_LABEL[o]} · ${esc(c[o].name)} ${tel(c[o].phone)}</h2>`
      + `<span class="who-tag">${myTurn() ? "내 차례" : live() ? "⏳ 응답 대기" : "상대방"}</span>`;
    renderChat();
    $("split").hidden = false;
  }

  // ── 지금 할 일 (내 칸) ──
  const confirmCard = (action, title, text, button) => `<div class="card stack" data-simple="${action}">
      <h3>${esc(title)}</h3><p class="small">${text}</p>
      <button class="btn primary" type="button" data-action="${action}">${esc(button)}</button></div>`;

  const registryForm = () => `<form class="card stack" data-form="show_registry">
      <h3>등기부등본 열람 · 선순위 조건</h3>
      <p class="small muted">① 아래 버튼으로 내 스마트폰에서 등기부등본을 열람하고, ② 임차인에게 화면을 직접 보여 주세요. ③ 확인한 선순위 조건을 올립니다.</p>
      <a class="btn" href="https://www.iros.go.kr" target="_blank" rel="noopener noreferrer">🔎 등기부등본 열람 (인터넷등기소)</a>
      <label class="check"><input type="checkbox" id="f_owner"> 등기부등본상 소유자가 본인임을 확인했습니다.</label>
      <label class="check"><input type="checkbox" id="f_clean"> 압류 · 가압류가 없음을 확인했습니다.</label>
      ${field("f_mortgage", "선순위 근저당 채권최고액 (원, 없으면 0)", 'type="number" inputmode="numeric" min="0"', 0)}
      <div><label for="f_note">선순위 메모 (선택)</label><textarea id="f_note" maxlength="300" placeholder="예: 전입세대 열람 결과 없음"></textarea></div>
      <button class="btn primary" type="submit">임차인에게 보여 줌 · 조건 올리기</button></form>`;

  const accountForm = () => `<form class="card stack" data-form="send_account">
      <h3>계약금 송금 계좌 알림</h3>
      <p class="small">계약금은 보증금의 10%인 <b>${won(c.terms.down_payment)}</b>입니다. 예금주는 본인 이름(<b>${esc(c.landlord.name)}</b>)이어야 임차인이 안심합니다.</p>
      ${field("f_bank", "은행", 'maxlength="20" required placeholder="예: 국민은행"')}
      ${field("f_number", "계좌번호", 'inputmode="numeric" maxlength="30" required placeholder="숫자와 -"')}
      ${field("f_holder", "예금주", 'maxlength="20" required', c.landlord.name)}
      <button class="btn primary" type="submit">임차인에게 계좌 알리기</button></form>`;

  function waitText() {
    if (!live()) return "";
    if (isLegacy()) return "이전 방식(서명)으로 진행하던 계약입니다. 새 방식으로 진행하려면 이 계약을 취소하고 매물에서 다시 예약하세요.";
    if (myTurn()) return "";
    const s = c.status, tenant = c.role === "tenant";
    if (s === "reserved") return tenant ? "임대인이 임차인 정보를 확인하기를 기다리고 있습니다." : "임차인이 임대인 · 매물 정보를 확인하기를 기다리고 있습니다.";
    return { registry_shown: "임차인이 등기부등본을 확인하기를 기다리고 있습니다.", registry_ok: "임대인이 계약금 계좌를 알리기를 기다리고 있습니다.",
      account_sent: "임차인이 계약금을 송금하기를 기다리고 있습니다.", info_ok: "임대인이 등기부등본을 열람해 보여 주기를 기다리고 있습니다." }[s] || "";
  }

  function renderActions() {
    const a = c.actions, out = [];
    if (a.includes("confirm_landlord_info")) out.push(confirmCard("confirm_landlord_info", "임대인 · 매물 정보 확인", "대화창의 임대인 · 매물 정보가 현장에서 직접 본 내용과 같은지 확인하세요. 이 확인은 임차인만 할 수 있습니다.", "✔ 임대인 정보 확인했음"));
    if (a.includes("confirm_tenant_info")) out.push(confirmCard("confirm_tenant_info", "임차인 정보 확인", "대화창의 임차인 정보(회원가입 정보)가 지금 만나고 있는 분과 같은지 확인하세요. 이 확인은 임대인만 할 수 있습니다.", "✔ 임차인 정보 확인했음"));
    if (a.includes("show_registry")) out.push(registryForm());
    if (a.includes("confirm_registry")) out.push(confirmCard("confirm_registry", "등기부등본 확인", "임대인이 보여 준 등기부등본과 위 선순위 조건(근저당 · 압류)이 같은지 확인하세요. 필요하면 인터넷등기소(iros.go.kr)에서 직접 열람해도 됩니다. 이 확인은 임차인만 할 수 있습니다.", "✔ 등기부등본 확인했음"));
    if (a.includes("send_account")) out.push(accountForm());
    if (a.includes("pay")) {
      const ac = c.account;
      out.push(`<div class="card stack" data-simple="pay"><h3>계약금 송금</h3>
        <p>${esc(ac.bank)} <b>${esc(ac.number)}</b> · 예금주 ${esc(ac.holder)}<br>보낼 금액 <b>${won(ac.amount)}</b> (보증금의 10%)</p>
        ${ac.holder_match ? "" : `<div class="alert warn">예금주가 임대인 이름과 다릅니다. 송금 전에 임대인에게 꼭 확인하세요.</div>`}
        <p class="small muted">은행 앱에서 직접 송금한 뒤 아래 버튼을 누르면 계약이 종료됩니다.</p>
        <button class="btn primary" type="button" data-action="pay">💸 송금했습니다 — 계약 종료</button></div>`);
    }
    const wait = waitText();
    if (wait) out.push(`<div class="alert">${esc(wait)}</div>`);
    if (c.status === "completed") out.push(`<div class="alert ok">계약금 송금까지 끝났습니다. 계약이 종료되었습니다.</div>${c.report_required ? `<div class="alert warn">${esc(REPORT_NOTE)}</div>` : ""}`);
    if (a.includes("document")) out.push(`<a class="btn primary" href="doc.html?id=${encodeURIComponent(c.id)}">📄 계약 확인서 보기 · 인쇄</a>`);
    if (a.includes("cancel")) {
      out.push(`<details class="card"><summary>예약 취소</summary><div class="stack">
        <div><label for="f_reason">사유 (선택)</label><input id="f_reason" maxlength="200"></div>
        <button class="btn danger" type="button" data-action="cancel">예약 취소</button>
        <p class="small muted">취소하면 매물이 다시 열려 다른 분이 예약할 수 있습니다.</p></div></details>`);
    }
    $("actions").innerHTML = out.join("");
  }

  const val = (fid) => $(fid).value;
  const bodies = {
    show_registry: () => ({ owner_match: $("f_owner").checked, seizure: !$("f_clean").checked, mortgage: val("f_mortgage"), note: val("f_note") }),
    send_account: () => ({ bank: val("f_bank"), number: val("f_number"), holder: val("f_holder") }),
    cancel: () => ({ reason: val("f_reason") }),
  };

  async function act(name, btn) {
    await R5.run(btn, async () => {
      c = (await R5.api(`/contracts/${encodeURIComponent(id)}/${PATH[name]}`, { method: "POST", body: (bodies[name] || (() => ({})))() })).contract;
      R5.notify("ok", "처리했습니다.", c.status_label);
      render();
      $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
    }, "처리 중...");
  }

  $("actions").addEventListener("submit", (e) => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    act(form.dataset.form, form.querySelector("[type=submit]"));
  });
  $("actions").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (["cancel", "pay"].includes(btn.dataset.action) && btn.dataset.armed !== "1") {     // 되돌릴 수 없는 동작은 한 번 더
      btn.dataset.armed = "1";
      btn.textContent = btn.dataset.action === "pay" ? "정말 송금하셨나요? 한 번 더 누르세요" : "정말 취소할까요? 한 번 더 누르세요";
      return;
    }
    act(btn.dataset.action, btn);
  });

  const toBottom = () => { $("chatScroll").scrollTop = $("chatScroll").scrollHeight; };
  function render() { renderInfo(); renderActions(); toBottom(); }

  // 상대방의 응답 · 메시지는 몇 초 안에 반영된다 (이 화면을 열어 둔 동안만 확인한다).
  const stamp = (x) => `${x.status}|${x.events.length}|${(x.chat || []).length}|${x.landlord_info_ok_at}|${x.tenant_info_ok_at}`;
  async function poll() {
    if (document.hidden || !c) return;
    try {
      const next = (await R5.api(`/contracts/${encodeURIComponent(id)}`)).contract;
      if (stamp(next) === stamp(c)) return;
      const stepChanged = next.status !== c.status || next.events.length !== c.events.length;
      const newChat = (next.chat || []).length > (c.chat || []).length;
      c = next;
      flashed = stepChanged;
      renderInfo();
      if (stepChanged) { renderActions(); toBottom(); R5.notify("ok", "상대방이 응답했습니다.", c.status_label); }
      else if (newChat) $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
    } catch (err) { /* 일시적인 오류는 다음 확인 때 다시 시도 */ }
  }

  $("chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("chatText").value.trim();
    if (!text) return;
    R5.run($("chatSend"), async () => {
      c = (await R5.api(`/contracts/${encodeURIComponent(id)}/messages`, { method: "POST", body: { text } })).contract;
      $("chatText").value = "";
      renderInfo();
      $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
      $("chatText").focus();
    }, "...");
  });

  try {
    c = (await R5.api(`/contracts/${encodeURIComponent(id)}`)).contract;
    render();
    setInterval(poll, 3000);
  } catch (err) {
    $("statusLine").textContent = "";
    R5.fail(err);
  }
})();
