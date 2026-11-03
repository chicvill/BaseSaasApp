/* rent5/static/contract.js — 계약 한 건: 진행 단계 · 조건 · 지금 내가 할 일 (임대인/임차인에 따라 다르다) */
(async function () {
  "use strict";
  const { esc, won, when, tel } = R5;
  const $ = (id) => document.getElementById(id);
  const id = new URLSearchParams(location.search).get("id") || "";

  const user = await R5.requireLogin();
  R5.nav(user);
  if (!id) return R5.notify("warn", "계약을 골라 주세요.", "내 화면의 계약 목록에서 열어 주세요.");

  const STEP_LABEL = ["신청", "조건 제시", "임차인 서명", "계약 성립", "계약금", "잔금 · 입주"];
  const EVENT_LABEL = { apply: "신청", offer: "임대인이 조건 제시", tenant_sign: "임차인 서명", landlord_sign: "임대인 서명 — 계약 성립",
    deposit: "계약금 입금 확인", balance: "잔금 입금 확인 — 입주", cancel: "취소" };
  const REPORT_NOTE = "보증금 6천만 원 또는 월세 30만 원을 넘는 계약은 계약일부터 30일 안에 주택 임대차 계약 신고를 해야 합니다(주민센터 또는 부동산거래관리시스템).";
  let c = null;

  const dl = (rows) => `<dl class="kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const field = (fid, label, attrs = "", value = "") => `<div><label for="${fid}">${esc(label)}</label><input id="${fid}" value="${esc(value)}" ${attrs}></div>`;

  // ── 정보 영역 ──
  function renderInfo() {
    $("title").textContent = c.terms ? c.terms.address : `${c.listing.address} ${c.listing.room || ""}`.trim();
    $("roleBadge").textContent = c.role === "landlord" ? "나는 임대인" : "나는 임차인";
    $("statusLine").textContent = c.status_label;
    const idx = c.steps.indexOf(c.status);
    $("progress").innerHTML = c.status === "cancelled" ? "" : STEP_LABEL.map((label, i) =>
      `<li class="${i < idx || c.status === "active" ? "done" : i === idx ? "now" : ""}">${esc(label)}</li>`).join("");

    $("parties").innerHTML = `<h2>당사자</h2>` + dl([
      ["임대인", `${esc(c.landlord.name)} ${tel(c.landlord.phone)}`],
      ["임차인", `${esc(c.tenant.name)} ${tel(c.tenant.phone)}`],
      c.message ? ["신청 메시지", esc(c.message)] : null,
      c.status === "cancelled" ? ["취소 사유", esc(c.cancel_reason || "")] : null,
    ]);
    $("parties").hidden = false;

    const t = c.terms, r = c.registry, i = c.tenant_info;
    $("terms").innerHTML = t ? `<h2>계약 조건</h2>` + dl([
      ["주소", esc(t.address)], ["구분", esc(t.lease_type)], ["보증금", won(t.deposit)], ["계약금", won(t.down_payment)],
      t.lease_type === "전세" ? null : ["월세 · 관리비", `${won(t.rent)} + ${won(t.mgmt)}`],
      ["기간", `${esc(t.start)} ~ ${esc(t.end)} (${t.months}개월)`],
    ]) + `<h3>등기부 확인 (임대인이 입력)</h3>` + dl([
      ["소유자", "등기부상 소유자가 임대인 본인임을 확인"], ["압류 · 가압류", "없음"], ["근저당 채권최고액", won(r.mortgage)],
      r.note ? ["메모", esc(r.note)] : null, ["확인일", esc(r.checked_on)],
    ]) + `<p class="small muted">임차인도 서명 전에 인터넷등기소(iros.go.kr)에서 등기부등본을 직접 확인하세요.</p>`
      + (i ? `<h3>임차인 정보</h3>` + dl([["생년월일", esc(i.birth)], ["현재 주소", esc(i.address)], ["비상 연락처", esc(i.emergency)]]) : "")
      + (c.report_required ? `<div class="alert warn">${esc(REPORT_NOTE)}</div>` : "") : "";
    $("terms").hidden = !t;

    $("history").innerHTML = `<h3>진행 기록</h3><ul class="timeline">${c.events.map((e) =>
      `<li>${esc(when(e.at))} · ${esc(EVENT_LABEL[e.what] || e.what)} (${esc(e.by === user.username ? "나" : "상대")})</li>`).join("")}</ul>`;
    $("history").hidden = false;
  }

  // ── 지금 할 일 ──
  const offerForm = () => {
    const t = c.terms || {}, l = c.listing;
    const deposit = t.deposit ?? l.deposit, down = t.down_payment ?? Math.round(l.deposit / 10 / 10000) * 10000;
    return `<form class="card stack" data-form="offer">
      <h2>${c.status === "offered" ? "조건 다시 제시" : "조건 제시"}</h2>
      <p class="small muted">${c.status === "offered" ? "임차인이 서명하기 전에는 조건을 고쳐 다시 제시할 수 있습니다." : "신청자에게 보여 줄 계약 조건과 등기부 확인 결과를 입력하세요."} 같은 매물에 다른 신청자가 있으면 자동으로 마감됩니다.</p>
      <div class="grid">
        ${field("f_address", "주소", 'maxlength="120" required', t.address ?? `${l.address} ${l.room || ""}`.trim())}
        ${field("f_deposit", "보증금 (원)", 'type="number" min="0" required', deposit)}
        ${field("f_down", "계약금 (원)", 'type="number" min="0" required', down)}
        ${l.lease_type === "전세" ? "" : field("f_rent", "월세 (원)", 'type="number" min="0" required', t.rent ?? l.rent)}
        ${field("f_mgmt", "관리비 (원)", 'type="number" min="0"', t.mgmt ?? l.mgmt)}
        ${field("f_start", "입주일", 'type="date" required', t.start ?? l.available_from ?? "")}
        ${field("f_months", "기간 (개월)", 'type="number" min="1" max="120" required', t.months ?? 12)}
      </div>
      <h3>등기부 확인</h3>
      <label class="check"><input type="checkbox" id="f_owner"> 등기부등본상 소유자가 본인임을 확인했습니다.</label>
      <label class="check"><input type="checkbox" id="f_clean"> 압류 · 가압류가 없음을 확인했습니다.</label>
      ${field("f_mortgage", "근저당 채권최고액 (원, 없으면 0)", 'type="number" min="0"', c.registry ? c.registry.mortgage : 0)}
      <div><label for="f_note">메모 (선택)</label><textarea id="f_note" maxlength="500">${esc(c.registry ? c.registry.note : "")}</textarea></div>
      <button class="btn primary" type="submit">${c.status === "offered" ? "조건 다시 제시" : "조건 제시하기"}</button>
    </form>`;
  };

  const tenantSignForm = () => `<form class="card stack" data-form="tenant-sign">
      <h2>내용 확인 후 서명</h2>
      <p class="small muted">위 계약 조건과 등기부 확인 결과를 확인하세요. 서명하면 임대인이 같은 내용에 서명해 계약이 성립합니다. 주민등록번호는 입력하지 않습니다.</p>
      ${field("f_birth", "생년월일", 'type="date" required')}
      ${field("f_taddress", "현재 주소", 'maxlength="120" required')}
      ${field("f_emergency", "비상 연락처 (관계 · 번호)", 'maxlength="60" placeholder="예: 부모 010-0000-0000" required')}
      <label class="check"><input type="checkbox" id="f_agree"> 위 계약 조건에 동의하고 전자 서명합니다.</label>
      <button class="btn primary" type="submit">서명하기</button>
    </form>`;

  const simple = (action, title, text, button) => `<div class="card stack" data-simple="${action}">
      <h2>${esc(title)}</h2><p>${text}</p>
      <button class="btn primary" type="button" data-action="${action}">${esc(button)}</button></div>`;

  function renderActions() {
    const a = c.actions, t = c.terms || {}, out = [];
    if (a.includes("offer")) out.push(offerForm());
    if (a.includes("tenant_sign")) out.push(tenantSignForm());
    if (a.includes("landlord_sign")) {
      out.push(simple("landlord-sign", "서명하고 계약 성립", `임차인이 서명했습니다. 위 임차인 정보와 조건을 확인한 뒤 서명하면 계약이 성립하고 매물이 마감됩니다.<br><span class="hash">내용 지문(SHA-256): ${esc(c.final_hash)}</span>`, "서명하고 계약 성립"));
    }
    if (a.includes("deposit")) out.push(simple("deposit", "계약금 입금 확인", `임차인에게서 계약금 <b>${won(t.down_payment)}</b>을(를) 직접 받았다면 확인해 주세요.`, "계약금 받았음"));
    if (a.includes("balance")) out.push(simple("balance", "잔금 입금 확인 · 입주", `잔금 <b>${won(t.deposit - t.down_payment)}</b>을(를) 받았다면 확인하세요. 확인하면 입주 처리됩니다.`, "잔금 받았음 · 입주 처리"));

    const wait = {
      "requested:tenant": `임대인이 조건을 제시하기를 기다리고 있습니다. 임대인 ${tel(c.landlord.phone)}`,
      "offered:landlord": "임차인이 서명하기를 기다리고 있습니다.",
      "tenant_signed:tenant": "서명했습니다. 임대인이 서명하면 계약이 성립합니다.",
      "signed:tenant": `계약이 성립했습니다. 계약금 <b>${won(t.down_payment)}</b>을(를) 임대인에게 직접 보내세요. 임대인이 받았음을 확인하면 다음 단계로 넘어갑니다.`,
      "deposit_paid:tenant": `계약금이 확인되었습니다. 입주일에 잔금 <b>${won((t.deposit || 0) - (t.down_payment || 0))}</b>을(를) 임대인에게 직접 보내세요.`,
      "active:tenant": "잔금까지 확인되어 입주 처리되었습니다.",
      "active:landlord": "잔금까지 확인되어 입주 처리되었습니다.",
    }[`${c.status}:${c.role}`];
    if (wait) out.push(`<div class="alert">${wait}</div>`);
    if (a.includes("document")) out.push(`<a class="btn primary" href="doc.html?id=${encodeURIComponent(c.id)}">📄 계약서 보기 · 인쇄</a>`);
    if (a.includes("cancel")) {
      out.push(`<div class="card stack"><h3>계약 취소</h3>
        <div><label for="f_reason">사유 (선택)</label><input id="f_reason" maxlength="200"></div>
        <button class="btn danger" type="button" data-action="cancel">계약 취소</button>
        <p class="small muted">계약이 성립한 뒤에는 이 화면에서 취소할 수 없습니다. 두 사람이 직접 합의해 해지하세요.</p></div>`);
    }
    $("actions").innerHTML = out.join("");
  }

  const val = (fid) => $(fid).value;
  const bodies = {
    "offer": () => ({ address: val("f_address"), deposit: val("f_deposit"), down_payment: val("f_down"),
      rent: $("f_rent") ? val("f_rent") : 0, mgmt: val("f_mgmt"), start: val("f_start"), months: val("f_months"),
      owner_match: $("f_owner").checked, seizure: !$("f_clean").checked, mortgage: val("f_mortgage"), note: val("f_note") }),
    "tenant-sign": () => ({ birth: val("f_birth"), address: val("f_taddress"), emergency: val("f_emergency"),
      agree: $("f_agree").checked, offer_hash: c.offer_hash }),
    "landlord-sign": () => ({ final_hash: c.final_hash }),
    "deposit": () => ({}), "balance": () => ({}),
    "cancel": () => ({ reason: val("f_reason") }),
  };

  async function act(name, btn) {
    await R5.run(btn, async () => {
      c = (await R5.api(`/contracts/${encodeURIComponent(id)}/${name}`, { method: "POST", body: bodies[name]() })).contract;
      R5.notify("ok", "처리했습니다.", c.status_label);
      render();
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
    if (btn.dataset.action === "cancel" && btn.dataset.armed !== "1") {     // 취소는 한 번 더 눌러야 한다
      btn.dataset.armed = "1";
      btn.textContent = "정말 취소할까요? 한 번 더 누르세요";
      return;
    }
    act(btn.dataset.action, btn);
  });

  function render() { renderInfo(); renderActions(); }
  try {
    c = (await R5.api(`/contracts/${encodeURIComponent(id)}`)).contract;
    render();
  } catch (err) {
    $("statusLine").textContent = "";
    R5.fail(err);
  }
})();
