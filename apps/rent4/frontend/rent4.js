/* apps/rent4/frontend/rent4.js — Rent4 공통: 로그인 세션 · 역할별 페이지 가드 · 상단 사용자 표시
 *
 *   const me = await Rent4.guard(["lessor", "admin"]);   // 로그인 안 했으면 login.html, 다른 역할이면 그 역할의 첫 페이지로
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

  /** 로그인이 필요한 파일(등기부 등)은 토큰을 붙여 받아 새 창으로 연다. */
  async function openFile(path) {
    const win = global.open("", "_blank");                 // 클릭 직후 열어야 팝업 차단을 피한다
    const res = await fetch(Bass.apiBase("rent4") + path, { headers: { Authorization: `Bearer ${Bass.token()}` } });
    if (!res.ok) {
      if (win) win.close();
      throw new Error("파일을 열 수 없습니다.");
    }
    const url = URL.createObjectURL(await res.blob());
    if (win) win.location.href = url;
    else global.location.href = url;
  }

  async function guard(kinds) {
    if (!Bass.token()) { go("login.html"); return new Promise(() => {}); }
    let info;
    try {
      info = await me();
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        Bass.setToken("");
        go("login.html");
        return new Promise(() => {});
      }
      throw err;
    }
    if (!kinds.includes(info.kind)) { go(info.home); return new Promise(() => {}); }
    // 임대인은 자기 건물로 고정 — 주소에 다른 건물(?branch=)을 넣어도 지운다
    if (info.kind !== "admin" && new URLSearchParams(location.search).has("branch")) {
      history.replaceState(null, "", location.pathname);
    }
    renderWho(info);
    startChat();
    return info;
  }

  /** 역할과 상관없이 로그인만 확인 (설명서 등). 안 했으면 로그인 화면으로. */
  async function requireLogin() {
    if (!Bass.token()) { go("login.html"); return new Promise(() => {}); }
    try {
      return await me();
    } catch (err) {
      Bass.setToken("");
      go("login.html");
      return new Promise(() => {});
    }
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
    if (pw && ["tenant", "lessor"].includes(info.kind)) googleLink(pw);
  }

  /** 내 정보 → 구글 연결. 연결해 두면 로그인 화면의 아이디·비밀번호 찾기에 쓴다 (연결한 사람만 찾을 수 있다). */
  async function googleLink(beforeEl) {
    const result = (location.hash.match(/oauth=(\w+)/) || [])[1];
    if (result) {
      history.replaceState(null, "", location.pathname + location.search);
      if (result === "linked") Bass.updateAppAlert("success", "구글 계정을 연결했습니다.", "아이디·비밀번호를 잊으면 로그인 화면의 '찾기'에서 이 구글 계정으로 본인 확인을 합니다.");
      if (result === "used") Bass.updateAppAlert("error", "이미 다른 사람이 연결한 구글 계정입니다.", "본인 구글 계정으로 다시 연결해 주세요.");
    }
    let res;
    try { res = await Bass.api("rent4", "/oauth/providers"); } catch (_) { return; }
    if (!res.providers.some((p) => p.id === "google")) return;
    const done = res.linked.find((l) => l.provider === "google");
    const btn = document.createElement("button");
    btn.className = "bass-btn bass-btn--sm bass-btn--ghost";
    btn.textContent = done ? "✅ 구글 연결됨" : "🔗 구글 연결";
    btn.title = done ? `연결된 구글 계정: ${done.name || ""} (다시 누르면 바꿉니다)` : "아이디·비밀번호 찾기에 쓸 구글 계정을 연결합니다";
    btn.addEventListener("click", () => Bass.withLock(btn, async () => {
      const r = await Bass.api("rent4", "/oauth/google/start", { method: "POST", body: { purpose: "link", back: "account" } });
      location.href = r.url;
    }, "이동 중..."));
    beforeEl.before(btn);
  }

  function logout() {
    Bass.setToken("");
    go("login.html");
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



  const STATUS_TONE = { requested: "warn", offered: "info", tenant_approved: "warn", registry_checked: "info",
    tenant_confirmed: "warn", contracted: "ok", sent: "warn", signed: "info", active: "ok", cancelled: "err" };
  const OPEN = ["requested", "offered", "tenant_approved", "registry_checked", "tenant_confirmed", "sent", "signed"];

  /** 계약 목록 표 + 단계별 버튼. call(path, opts) 는 "/admin/contracts" 또는 "/lessor/contracts" 아래로 POST. */
  function contractTable(table, items, { call, reload, partyLabel }) {
    const esc = Bass.escapeHtml;
    const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;
    const btn = (act, id, label, primary, title) =>
      `<button class="bass-btn bass-btn--sm ${primary ? "bass-btn--primary" : ""}" data-act="${act}" data-id="${esc(id)}"${title ? ` title="${esc(title)}"` : ""}>${label}</button>`;
    table.innerHTML = `<thead><tr><th>대상</th><th>${esc(partyLabel)}</th><th>조건</th><th>상태</th><th></th></tr></thead>
      <tbody>${items.length ? items.map((c) => {
        const target = c.kind === "membership" ? `${c.building_name} (${c.building_code})` : `${c.room}호`;
        const terms = c.kind === "membership" ? `가맹비 ${won(c.fee)}`
          : `보증금 ${won(c.deposit)}${c.down_payment ? ` (계약금 ${won(c.down_payment)})` : ""} · 월 ${won(c.rent + c.mgmt)} · ${c.start || ""}${c.end ? ` ~ ${c.end}` : ""}`;
        const acts = [
          c.status === "requested" ? btn("offer", c.id, "📝 조건 제시", true, "현장 점검 뒤 계약 조건을 정해 임차인에게 보냅니다 (같은 호실 다른 요청은 마감)") : "",
          c.status === "offered" ? btn("offer", c.id, "📝 조건 수정 · 다시 보내기") : "",
          c.status === "tenant_approved" ? btn("registry", c.id, "📑 등기 확인 결과 보내기", true) : "",
          c.status === "tenant_confirmed" ? btn("downpay", c.id, `💰 계약금 ${won(c.down_payment)} 확인`, true, "입금을 확인하면 계약 완료 + 입주 안내문 발송") : "",
          c.status === "contracted" ? btn("paid", c.id, `🏠 잔금 ${won(c.deposit - c.down_payment)} 확인 · 입주`, true) : "",
          c.status === "signed" ? btn("paid", c.id, `💰 ${c.kind === "membership" ? "가맹비" : "잔금"} 확인`, true) : "",
          ["sent", "signed"].includes(c.status) && !c.member ? btn("link", c.id, "🔗 링크", false, "계약 시작 링크를 새로 만듭니다 (이전 링크 무효)") : "",
          c.finalized_at ? `<a class="bass-btn bass-btn--sm" href="contract_doc.html?id=${encodeURIComponent(c.id)}">📄 계약서</a>` : "",
          OPEN.includes(c.status) ? `<button class="bass-btn bass-btn--sm bass-btn--ghost" data-act="cancel" data-id="${esc(c.id)}">${c.status === "requested" ? "거절" : "취소"}</button>` : "",
        ].join(" ");
        const info = c.tenant_birth ? `<br><small class="bass-muted">생년월일 ${esc(c.tenant_birth)} · ${esc(c.tenant_address)}${c.emergency_contact ? ` · 비상 ${esc(c.emergency_contact)}` : ""}</small>` : "";
        return `<tr><td><b>${esc(target)}</b></td><td>${esc(c.party_name)}${c.member ? ` <span class="bass-badge bass-badge--info">회원</span>` : ""}<br><small class="bass-muted">${esc(c.party_phone)}</small>${info}</td>
          <td style="white-space:normal;text-align:left">${esc(terms)}${c.request_note ? `<br><small class="bass-muted">💬 ${esc(c.request_note)}</small>` : ""}</td>
          <td><span class="bass-badge bass-badge--${STATUS_TONE[c.status] || "info"}">${esc(c.status_label)}</span>${c.username ? `<br><small><code>${esc(c.username)}</code></small>` : ""}</td>
          <td style="text-align:right">${acts}</td></tr>`;
      }).join("") : `<tr><td colspan="5" class="bass-muted">아직 계약이 없습니다.</td></tr>`}</tbody>`;

    table._items = Object.fromEntries(items.map((c) => [c.id, c]));
    if (table.dataset.bound) return;
    table.dataset.bound = "1";
    table.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const c = table._items[b.dataset.id];
      const act = b.dataset.act;
      if (act === "offer") return offerDialog(c, call, reload);
      if (act === "registry") return registryDialog(c, call, reload);
      if (act === "cancel" && !global.confirm(`${c.party_name}님 계약을 ${c.status === "requested" ? "거절" : "취소"}할까요?`)) return;
      Bass.withLock(b, async () => {
        const res = await call(`/${b.dataset.id}/${act}`);
        if (act === "link") { issuedNotice(res); return; }
        if (act === "downpay") Bass.updateAppAlert("success", "계약금을 확인했습니다 — 계약 완료.",
          `${c.party_name}님에게 입주 안내문(전기 · 가스, 분리수거, 전입신고, 임대차 신고)을 채팅으로 보냈습니다. 매물은 내려갔습니다.`);
        if (act === "paid") Bass.updateAppAlert("success", c.member ? "잔금을 확인해 입주 처리했습니다." : "납부를 확인해 계약이 체결되었습니다.",
          c.member ? `${c.party_name}님 아이디로 내 계약 · 납입 화면이 열렸습니다.` : `${c.party_name}님은 휴대폰 번호와 계약 때 정한 비밀번호로 로그인하면 바로 시작됩니다.`);
        if (act === "cancel") Bass.updateAppAlert("warning", "계약을 취소했습니다.", c.member ? "임차인 채팅창에 안내가 갔습니다." : "취소된 링크는 열리지 않습니다.");
        await reload();
      });
    });
  }

  function dialog(html) {
    let dlg = document.getElementById("flowDialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "flowDialog";
      dlg.className = "detail";
      dlg.style.maxWidth = "min(520px, 94vw)";
      document.body.append(dlg);
    }
    dlg.innerHTML = html;
    dlg.querySelector("[data-close]")?.addEventListener("click", () => dlg.close());
    dlg.showModal();
    return dlg;
  }

  /** 현장 점검 뒤 조건 제시 → 임차인 채팅 + 임대인 휴대폰 문자앱 · 카카오톡 보내기 창. */
  function offerDialog(c, call, reload) {
    const esc = Bass.escapeHtml;
    const f = (id, label, value, type = "number", extra = "") =>
      `<div><label class="bass-label" for="${id}">${label}</label><input class="bass-input" id="${id}" type="${type}" value="${esc(value ?? "")}" ${extra}></div>`;
    const dlg = dialog(`
      <form class="stack" id="offerForm" style="gap:10px">
        <h3 style="margin:0">📝 ${esc(c.room)}호 계약 조건 — ${esc(c.party_name)}님</h3>
        <p class="bass-muted" style="margin:0;font-size:13px">현장 점검 뒤 확정한 조건을 적어 주세요. 임차인 채팅창으로 가고, 휴대폰으로도 보낼 수 있습니다.</p>
        ${f("oAddr", "주소", c.address, "text", "required")}
        <div class="bass-grid" style="grid-template-columns:repeat(auto-fit,minmax(min(140px,100%),1fr));gap:10px">
          ${f("oDeposit", "보증금 (원)", c.deposit, "number", 'min="0"')}
          ${f("oDown", "계약금 (원)", c.down_payment || Math.round((c.deposit || 0) / 10 / 10000) * 10000, "number", 'min="0"')}
          ${f("oRent", "월 임차료 (원)", c.rent, "number", 'min="0"')}
          ${f("oMgmt", "월 관리비 (원)", c.mgmt, "number", 'min="0"')}
          ${f("oStart", "입주일", c.start, "date")}
          ${f("oPeriod", "기간 (개월)", c.period_months || 12, "number", 'min="1" max="60"')}
        </div>
        <div class="bass-row" style="justify-content:flex-end">
          <button class="bass-btn bass-btn--ghost" type="button" data-close>닫기</button>
          <button class="bass-btn bass-btn--primary" type="submit">조건 보내기</button>
        </div>
      </form>`);
    dlg.querySelector("#offerForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = (id) => dlg.querySelector(`#${id}`).value;
      Bass.withLock(e.submitter, async () => {
        const res = await call(`/${c.id}/offer`, { body: {
          address: v("oAddr").trim(), deposit: Number(v("oDeposit") || 0), down_payment: Number(v("oDown") || 0),
          rent: Number(v("oRent") || 0), mgmt: Number(v("oMgmt") || 0), start: v("oStart") || null, period_months: Number(v("oPeriod") || 12) } });
        dlg.close();
        shareLink({ title: `${c.room}호 계약 조건을 보냈습니다`, name: c.party_name, phone: res.share.phone,
          link: new URL(res.share.link, location.href).href, message: res.share.message,
          note: "임차인 채팅창에는 이미 보냈습니다. 휴대폰 문자 · 카카오톡으로도 보내려면 아래 버튼을 쓰세요." });
        await reload();
      }, "보내는 중...");
    });
  }

  /** 등기부 확인 결과: 체크리스트 + 파일(선택) → 서버가 계약 내용을 확정 저장. */
  function registryDialog(c, call, reload) {
    const esc = Bass.escapeHtml;
    const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const dlg = dialog(`
      <form class="stack" id="regForm" style="gap:10px">
        <h3 style="margin:0">📑 ${esc(c.room)}호 등기부 확인 결과</h3>
        <p class="bass-muted" style="margin:0;font-size:13px">인터넷등기소(iros.go.kr)에서 오늘 열람한 등기부등본을 보고 적어 주세요. 보내면 계약 내용이 확정 저장되고 임차인이 최종 승인합니다.</p>
        <div><label class="bass-label" for="rOwner">등기부상 소유자</label><input class="bass-input" id="rOwner" required></div>
        <label class="bass-row" style="gap:8px"><input type="checkbox" id="rMatch" checked> 소유자 = 임대인 (일치)</label>
        <div class="bass-grid" style="grid-template-columns:1fr 1fr;gap:10px">
          <div><label class="bass-label" for="rMort">근저당 등 채권최고액 합계 (원, 없으면 0)</label><input class="bass-input" id="rMort" type="number" min="0" value="0"></div>
          <div><label class="bass-label" for="rDate">열람일</label><input class="bass-input" id="rDate" type="date" value="${today}"></div>
        </div>
        <label class="bass-row" style="gap:8px"><input type="checkbox" id="rSeize"> 압류 · 가압류 · 경매 기입 있음</label>
        <div><label class="bass-label" for="rNote">메모 (선택)</label><textarea class="bass-input" id="rNote" style="min-height:60px;font-family:var(--font)"></textarea></div>
        <div><label class="bass-label" for="rFile">등기부 파일 (PDF · 사진, 선택 — 양쪽 당사자만 열람)</label><input class="bass-input" id="rFile" type="file" accept="application/pdf,image/*"></div>
        <div class="bass-row" style="justify-content:flex-end">
          <button class="bass-btn bass-btn--ghost" type="button" data-close>닫기</button>
          <button class="bass-btn bass-btn--primary" type="submit">결과 보내기 · 계약 확정</button>
        </div>
      </form>`);
    dlg.querySelector("#regForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = (id) => dlg.querySelector(`#${id}`);
      Bass.withLock(e.submitter, async () => {
        const body = { owner_name: q("rOwner").value.trim(), owner_match: q("rMatch").checked, mortgage: Number(q("rMort").value || 0),
          seizure: q("rSeize").checked, note: q("rNote").value, viewed_on: q("rDate").value };
        const file = q("rFile").files[0];
        if (file) {
          body.file = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(file); });
          body.mime = file.type;
        }
        await call(`/${c.id}/registry`, { body });
        dlg.close();
        Bass.updateAppAlert("success", "등기 확인 결과를 보내고 계약 내용을 확정 저장했습니다.", "임차인이 최종 승인하고 계약금을 보내면 '계약금 확인'을 누르세요.");
        await reload();
      }, "보내는 중...");
    });
  }

  /** 계약 발행 · 링크 다시 만들기 뒤: 계약 시작 링크 보내기 창 (문자 대신 대면 · 카카오톡 등). */
  function issuedNotice(res) {
    const c = res.contract;
    const what = c.kind === "membership" ? `${c.building_name} 가맹 계약` : `${c.building_name} ${c.room}호 임대차 계약`;
    shareLink({
      title: `${what} 시작 링크`, name: c.party_name, phone: c.party_phone,
      link: new URL(res.link, location.href).href,
      message: `[Rent4] ${c.party_name}님, ${what}서입니다. 아래 링크에서 비밀번호를 정한 뒤 계약 내용을 확인하고 동의해 주세요. 다음부터는 휴대폰 번호와 그 비밀번호로 로그인합니다. (${res.expires_hours}시간 안에, 한 번만 사용)`,
      note: "링크는 비밀번호를 정하는 순간 만료됩니다. 잃어버리면 계약 목록의 '🔗 링크'로 다시 만드세요 (이전 링크는 무효).",
    });
  }

  /** 클립보드 복사: 클릭 직후 동기 방식 먼저, 안 되면 클립보드 API. */
  async function copyText(text) {
    const host = document.querySelector("dialog[open]") || document.body;
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
    try { await global.navigator.clipboard.writeText(text); return true; } catch (_) { return false; }
  }

  /** 링크 보내기 창: 채팅(카카오톡 등)으로 보낼 문구 + 링크. */
  function shareLink({ title, name, phone, link, message, note }) {
    const esc = Bass.escapeHtml;
    const text = `${message}\n${link}`;
    let dlg = document.getElementById("shareDialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "shareDialog";
      dlg.className = "detail";
      document.body.append(dlg);
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    }
    const digits = String(phone || "").replace(/\D/g, "");
    const canShare = typeof global.navigator.share === "function";
    dlg.innerHTML = `
      <div class="stack">
        <h2 style="margin:0;font-size:18px">🔑 ${esc(title)}</h2>
        <div class="bass-muted" style="font-size:13px">${esc(name)}님에게 채팅(카카오톡 등)으로 아래 내용을 보내 주세요.</div>
        <textarea class="bass-input" readonly style="min-height:110px;font-size:13px">${esc(text)}</textarea>
        <div class="bass-row" style="flex-wrap:nowrap">
          <input class="bass-input" readonly value="${esc(link)}" style="font-size:12.5px;min-width:0" onfocus="this.select()">
          <button class="bass-btn bass-btn--sm" data-share="copy-link" style="white-space:nowrap">🔗 링크만 복사</button>
        </div>
        ${note ? `<div style="font-size:12.5px;color:var(--warn)">${esc(note)}</div>` : ""}
        <div class="bass-row">
          ${canShare ? `<button class="bass-btn bass-btn--primary" data-share="share">📤 카카오톡 등으로 공유</button>` : ""}
          <button class="bass-btn ${canShare ? "" : "bass-btn--primary"}" data-share="copy">📋 문구 전체 복사</button>
          ${digits ? `<a class="bass-btn" href="sms:${digits}?&body=${encodeURIComponent(text)}">💬 문자 앱</a>` : ""}
          <button class="bass-btn bass-btn--ghost" data-share="close">닫기</button>
        </div>
      </div>`;
    const flash = (btn, ok) => {
      const original = btn.textContent;
      btn.textContent = ok ? "✓ 복사됨" : "복사 실패";
      setTimeout(() => { btn.textContent = original; }, 1500);
    };
    dlg.onclick = async (e) => {
      const btn = e.target.closest("[data-share]");
      const act = btn && btn.dataset.share;
      if (act === "close") dlg.close();
      if (act === "copy") flash(btn, await copyText(text));
      if (act === "copy-link") flash(btn, await copyText(link));
      if (act === "share") {
        try { await global.navigator.share({ title: "Rent4", text }); dlg.close(); } catch (_) { /* 취소 */ }
      }
    };
    if (!dlg.open) dlg.showModal();
  }

  /** 채팅창: 로그인한 화면 오른쪽 아래. 대화 상대 목록 → 대화 (분류: 고장 · 문의 · 안내).
   *  5초마다 받은 메시지를 확인한다 (= 서버에 '접속 중' 표시). 새 메시지가 오면 창이 열린다. */
  function startChat() {
    if (document.getElementById("chatFab")) return;
    const esc = Bass.escapeHtml;
    const CAT_TONE = { 고장: "err", 문의: "info", 안내: "ok" };
    const fab = document.createElement("button");
    fab.id = "chatFab";
    fab.className = "chat-fab";
    fab.type = "button";
    fab.setAttribute("aria-label", "채팅");
    fab.textContent = "💬";
    const panel = document.createElement("div");
    panel.id = "chatPanel";
    panel.className = "chat-panel hide";
    panel.innerHTML = `
      <div class="chat-head">
        <span class="bass-row" style="gap:6px"><button type="button" class="bass-btn bass-btn--sm bass-btn--ghost hide" data-chat="back">←</button>
          <b id="chatTitle">💬 채팅</b></span>
        <button type="button" class="bass-btn bass-btn--sm bass-btn--ghost" data-chat="close">닫기</button>
      </div>
      <div class="chat-list" id="chatList"></div>
      <form class="chat-form hide" id="chatForm">
        <div class="chat-cats" id="chatCats"></div>
        <div class="bass-row" style="flex-wrap:nowrap;gap:6px">
          <textarea class="bass-input" id="chatText" rows="2" maxlength="1000" placeholder="메시지 입력 (Enter 보내기, Shift+Enter 줄바꿈)"></textarea>
          <button class="bass-btn bass-btn--primary" type="submit">보내기</button>
        </div>
      </form>`;
    document.body.append(fab, panel);
    const $c = (id) => document.getElementById(id);
    let other = null;            // 지금 열린 대화 상대 {username, label}
    let category = "문의";
    let seen = new Set();
    let first = true;

    const time = (iso) => iso.slice(5, 16).replace("T", " ").replace("-", "/");

    async function showContacts() {
      other = null;
      $c("chatTitle").textContent = "💬 채팅";
      panel.querySelector("[data-chat=back]").classList.add("hide");
      $c("chatForm").classList.add("hide");
      const { contacts, categories } = await Bass.api("rent4", "/chat/contacts");
      $c("chatCats").innerHTML = categories.map((c) =>
        `<button type="button" class="chat-cat ${c === category ? "is-on" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("");
      if (other) return;
      $c("chatList").innerHTML = contacts.length ? contacts.map((p) => `
        <button type="button" class="chat-contact" data-to="${esc(p.username)}" data-label="${esc(p.label)}">
          <span><i class="chat-dot ${p.online ? "is-on" : ""}" title="${p.online ? "접속 중" : "접속 안 함"}"></i>${esc(p.label)}</span>
          ${p.unread ? `<span class="bass-badge bass-badge--err">${p.unread}</span>` : ""}
        </button>`).join("") : `<div class="bass-muted" style="font-size:13px">대화할 수 있는 상대가 없습니다.</div>`;
    }

    async function showThread(keepScroll) {
      const list = $c("chatList");
      const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
      const { messages } = await Bass.api("rent4", `/chat/thread/${encodeURIComponent(other.username)}`);
      if (!other) return;
      list.innerHTML = messages.length ? messages.map((m) => `
        <div class="chat-msg ${m.mine ? "is-mine" : ""}">
          <div class="chat-meta">${m.category ? `<span class="bass-badge bass-badge--${CAT_TONE[m.category] || "info"}">${esc(m.category)}</span> ` : ""}${esc(m.mine ? "나" : (m.sender_label || m.sender))} · ${esc(time(m.created_at))}${m.mine && m.read_at ? " · 읽음" : ""}</div>
          <div style="white-space:pre-wrap">${esc(m.text)}</div>
          ${m.link ? `<a class="bass-btn bass-btn--sm bass-btn--primary" href="${esc(m.link)}" style="margin-top:6px">${esc(m.link_label || "열기")}</a>` : ""}
        </div>`).join("") : `<div class="bass-muted" style="font-size:13px">아직 대화가 없습니다. 분류를 고르고 메시지를 보내 보세요.</div>`;
      if (!keepScroll || atBottom) list.scrollTop = list.scrollHeight;
    }

    function openThread(username, label) {
      other = { username, label };
      $c("chatTitle").textContent = label;
      panel.querySelector("[data-chat=back]").classList.remove("hide");
      $c("chatForm").classList.remove("hide");
      showThread(false).then(poll).catch(Bass.showError);
      $c("chatText").focus();
    }

    async function poll() {
      try {
        const { messages } = await Bass.api("rent4", "/chat/inbox");
        const unread = messages.filter((m) => !m.read_at);
        fab.textContent = unread.length ? `💬 ${unread.length}` : "💬";
        fab.classList.toggle("has-new", unread.length > 0);
        const fresh = messages.filter((m) => !seen.has(m.id) && !m.read_at);
        messages.forEach((m) => seen.add(m.id));
        if (!first && fresh.length) {           // 새 메시지가 오면 바로 연다
          panel.classList.remove("hide");
          if (!other) openThread(fresh[fresh.length - 1].sender, fresh[fresh.length - 1].sender_label || fresh[fresh.length - 1].sender);
        }
        first = false;
        if (panel.classList.contains("hide")) return;
        if (other) await showThread(true);
        else await showContacts();
      } catch (_) { /* 로그아웃 등 — 조용히 다음 차례 */ }
    }

    fab.addEventListener("click", () => {
      panel.classList.toggle("hide");
      if (!panel.classList.contains("hide")) (other ? showThread(false) : showContacts()).catch(Bass.showError);
    });
    panel.addEventListener("click", (e) => {
      if (e.target.closest("[data-chat=close]")) panel.classList.add("hide");
      if (e.target.closest("[data-chat=back]")) showContacts().catch(Bass.showError);
      const c = e.target.closest("[data-to]");
      if (c) openThread(c.dataset.to, c.dataset.label);
      const cat = e.target.closest("[data-cat]");
      if (cat) {
        category = cat.dataset.cat;
        panel.querySelectorAll("[data-cat]").forEach((b) => b.classList.toggle("is-on", b === cat));
      }
    });
    $c("chatForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = $c("chatText").value.trim();
      if (!text || !other) return;
      try {
        await Bass.api("rent4", "/chat/send", { method: "POST", body: { to: other.username, text, category } });
        $c("chatText").value = "";
        await showThread(false);
      } catch (err) { Bass.showError(err); }
    });
    $c("chatText").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        $c("chatForm").requestSubmit();
      }
    });
    showContacts().catch(() => {});
    poll();
    setInterval(poll, 5000);
  }

  /** 당월 수납현황 카드 (임대인 첫 화면 · 관리인 가맹점 펼쳐보기). */
  function monthCard(m) {
    const esc = Bass.escapeHtml;
    const man = (n) => `${(Number(n || 0) / 10000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만원`;
    const [y, mo] = m.ym.split("-");
    return `
      <div class="bass-row" style="justify-content:space-between;align-items:baseline">
        <strong style="font-size:16px">${Number(y)}년 ${Number(mo)}월 수납현황</strong>
        <span class="bass-muted" data-clock style="font-size:13px;font-variant-numeric:tabular-nums"></span>
      </div>
      <div class="dash-amount"><b>${man(m.collected)}</b> <span class="bass-muted">/ 예정 ${man(m.expected)}</span>
        <span class="dash-rate">${m.rate}%</span></div>
      <div class="dash-bar"><i style="width:${Math.min(100, m.rate)}%"></i></div>
      <div class="dash-counts">
        <span><b>${m.paid_rooms}</b>실 완납</span>
        <span class="is-err" title="${esc(m.unpaid_list.map((r) => r + "호").join(", "))}"><b>${m.unpaid_rooms}</b>실 미납</span>
        <span class="is-muted"><b>${m.vacant_rooms}</b>실 공실</span>
      </div>`;
  }

  /** [data-clock] 칸에 현재 년월일시 (1초마다). */
  function startClock(root) {
    const DAYS = "일월화수목금토";
    const tick = () => {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      const text = `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${DAYS[d.getDay()]}) ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
      (root || document).querySelectorAll("[data-clock]").forEach((el) => { el.textContent = text; });
    };
    tick();
    if (!startClock.timer) startClock.timer = setInterval(tick, 1000);
  }

  global.Rent4 = { me, guard, requireLogin, logout, openFile, go, passwordGate, contractTable, issuedNotice, shareLink, startChat, monthCard, startClock };
})(window);
