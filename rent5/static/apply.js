/* rent5/static/apply.js — 매물 계약 신청. 비회원이면 가입(또는 로그인) 뒤 이 화면으로 돌아온다. */
(async function () {
  "use strict";
  const { esc, won, tel } = R5;
  const $ = (id) => document.getElementById(id);
  const id = new URLSearchParams(location.search).get("listing") || "";

  if (!id) {
    R5.notify("warn", "신청할 매물을 골라 주세요.", "매물 목록에서 [계약 신청]을 누르세요.");
    R5.nav();
    return;
  }
  const user = await R5.me();
  if (!user) return R5.go(`signup.html?next=${encodeURIComponent(R5.here())}`);   // 비회원 → 가입 → 이 화면
  R5.nav(user);

  try {
    const { listing: l } = await R5.api(`/listings/${encodeURIComponent(id)}`);
    $("listingCard").innerHTML = `
      <strong>${esc(l.address)} ${esc(l.room)}</strong>
      <dl class="kv">
        <dt>구분</dt><dd>${esc(l.lease_type)}</dd>
        <dt>보증금</dt><dd>${won(l.deposit)}</dd>
        ${l.lease_type === "전세" ? "" : `<dt>월세 · 관리비</dt><dd>${won(l.rent)} + ${won(l.mgmt)} = <b>${won(l.monthly)}</b></dd>`}
        <dt>입주 가능</dt><dd>${esc(l.available_from || "협의")}</dd>
      </dl>
      ${l.description ? `<p class="desc">${esc(l.description)}</p>` : ""}`;
    $("callTip").innerHTML = l.contact_phone
      ? `신청 전에 임대인과 통화로 확인하셨나요? ☎ ${tel(l.contact_phone)}`
      : "신청 후 임대인이 연락해 조건을 정합니다.";
    $("listingCard").hidden = false;
    $("applyForm").hidden = false;
  } catch (err) {
    R5.fail(err);
    return;
  }

  $("applyForm").addEventListener("submit", (e) => {
    e.preventDefault();
    R5.run($("btnApply"), async () => {
      const r = await R5.api(`/listings/${encodeURIComponent(id)}/apply`, { method: "POST", body: { message: $("message").value } });
      R5.go(`contract.html?id=${encodeURIComponent(r.contract_id)}`);
    }, "신청 중...");
  });
})();
