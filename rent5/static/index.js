/* rent5/static/index.js — 공개 매물 목록 (로그인 없이).
   [계약 신청] = 임대인에게 전화 걸기 + 옆에 [예약] 버튼 열기. 현장에서 확인한 뒤 [예약]을 누르면(로그인 필요) 계약방이 열린다. */
(function () {
  "use strict";
  const { esc, man, tel } = R5;
  const $ = (id) => document.getElementById(id);
  const KEY = "rent5_called";
  let all = [];

  const readCalled = () => { try { return new Set(JSON.parse(localStorage.getItem(KEY) || "[]")); } catch (_) { return new Set(); } };
  const called = readCalled();
  const saveCalled = () => { try { localStorage.setItem(KEY, JSON.stringify([...called].slice(-50))); } catch (_) { /* 저장 못 해도 동작에는 지장 없음 */ } };
  const pick = new URLSearchParams(location.search).get("l") || "";      // 로그인하고 돌아온 매물
  if (pick) { called.add(pick); saveCalled(); }

  function card(l) {
    const price = l.lease_type === "전세" ? `전세 <b>${man(l.deposit)}</b>` : `보증금 <b>${man(l.deposit)}</b> / 월 <b>${man(l.monthly)}</b>`;
    const fees = l.lease_type === "전세" ? "" : `월세 ${man(l.rent)} + 관리비 ${man(l.mgmt)} · `;
    const id = esc(l.id);
    const actions = l.reserved
      ? `<div class="small muted">다른 분이 예약한 매물입니다. 지금은 고를 수 없습니다.</div>`
      : `<div class="row">
          <button class="btn primary" type="button" data-apply="${id}">📝 계약 신청</button>
          <button class="btn ok" type="button" data-reserve="${id}"${called.has(l.id) ? "" : " hidden"}>🔖 예약</button>
        </div>
        <p class="small muted" data-note="${id}"${called.has(l.id) ? "" : " hidden"}>${l.contact_phone ? `☎ 임대인 ${tel(l.contact_phone)} — 통화하고 현장에서 확인한 뒤 [예약]을 누르세요.` : "문의 번호가 없는 매물입니다. 현장에서 확인한 뒤 [예약]을 누르세요."}</p>`;
    return `<article class="card listing stack${l.reserved ? " dim" : ""}${l.id === pick ? " pick" : ""}" id="l_${id}">
      <div class="row between"><strong>${esc(l.address)} ${esc(l.room)}</strong>${l.reserved ? `<span class="badge warn">예약됨</span>` : `<span class="badge">${esc(l.lease_type)}</span>`}</div>
      <div class="price">${price}</div>
      <div class="small muted">${fees}입주 ${esc(l.available_from || "협의")}</div>
      ${l.description ? `<p class="desc">${esc(l.description)}</p>` : ""}
      ${actions}
    </article>`;
  }

  function render() {
    const key = $("keyword").value.trim().toLowerCase();
    const type = $("leaseType").value;
    const items = all.filter((l) => (!type || l.lease_type === type) && (!key || `${l.address} ${l.room}`.toLowerCase().includes(key)));
    $("count").textContent = all.length ? `매물 ${items.length}건` : "";
    $("listings").innerHTML = items.length ? items.map(card).join("")
      : `<div class="card muted">${all.length ? "조건에 맞는 매물이 없습니다." : "지금 공개된 매물이 없습니다."}</div>`;
    const picked = $(`l_${pick}`);
    if (picked) picked.scrollIntoView({ block: "center" });
  }

  $("listings").addEventListener("click", async (e) => {
    const apply = e.target.closest("[data-apply]");
    if (apply) {                                         // 1) 전화 걸기 + 예약 버튼 열기
      const id = apply.dataset.apply, l = all.find((x) => x.id === id);
      called.add(id); saveCalled();
      const box = $(`l_${id}`);
      box.querySelector("[data-reserve]").hidden = false;
      box.querySelector("[data-note]").hidden = false;
      if (l && l.contact_phone) location.href = `tel:${l.contact_phone.replace(/\D/g, "")}`;
      return;
    }
    const btn = e.target.closest("[data-reserve]");
    if (!btn) return;
    if (btn.dataset.armed !== "1") {                     // 예약은 한 번 더 눌러야 한다 (다른 사람이 못 고르게 되므로)
      btn.dataset.armed = "1";
      btn.textContent = "정말 예약할까요? 한 번 더";
      return;
    }
    const id = btn.dataset.reserve;
    const user = await R5.me();
    if (!user) return R5.go(`login.html?next=${encodeURIComponent(`index.html?l=${id}`)}`);   // 로그인 뒤 이 매물로 돌아온다
    R5.run(btn, async () => {
      const r = await R5.api(`/listings/${encodeURIComponent(id)}/reserve`, { method: "POST" });
      called.delete(id); saveCalled();
      R5.go(`contract.html?id=${encodeURIComponent(r.contract_id)}`);
    }, "예약 중...");
  });

  $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); render(); });
  $("keyword").addEventListener("input", render);
  $("leaseType").addEventListener("change", render);

  R5.nav();
  R5.api("/listings").then((d) => { all = d.listings; render(); }).catch(R5.fail);
})();
