/* rent5/static/index.js — 공개 매물 목록 (로그인 없이) */
(function () {
  "use strict";
  const { esc, man, tel } = R5;
  const $ = (id) => document.getElementById(id);
  let all = [];

  function card(l) {
    const price = l.lease_type === "전세" ? `전세 <b>${man(l.deposit)}</b>` : `보증금 <b>${man(l.deposit)}</b> / 월 <b>${man(l.monthly)}</b>`;
    const fees = l.lease_type === "전세" ? "" : `월세 ${man(l.rent)} + 관리비 ${man(l.mgmt)} · `;
    return `<article class="card listing stack">
      <div class="row between"><strong>${esc(l.address)} ${esc(l.room)}</strong><span class="badge">${esc(l.lease_type)}</span></div>
      <div class="price">${price}</div>
      <div class="small muted">${fees}입주 ${esc(l.available_from || "협의")}</div>
      ${l.description ? `<p class="desc">${esc(l.description)}</p>` : ""}
      ${l.contact_phone ? `<div class="small">☎ 문의 ${tel(l.contact_phone)}</div>` : ""}
      <a class="btn primary" href="apply.html?listing=${encodeURIComponent(l.id)}">📝 계약 신청</a>
    </article>`;
  }

  function render() {
    const key = $("keyword").value.trim().toLowerCase();
    const type = $("leaseType").value;
    const items = all.filter((l) => (!type || l.lease_type === type) && (!key || `${l.address} ${l.room}`.toLowerCase().includes(key)));
    $("count").textContent = all.length ? `공개된 매물 ${items.length}건` : "";
    $("listings").innerHTML = items.length ? items.map(card).join("")
      : `<div class="card muted">${all.length ? "조건에 맞는 매물이 없습니다." : "지금 공개된 매물이 없습니다."}</div>`;
  }

  $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); render(); });
  $("keyword").addEventListener("input", render);
  $("leaseType").addEventListener("change", render);

  R5.nav();
  R5.api("/listings").then((d) => { all = d.listings; render(); }).catch(R5.fail);
})();
