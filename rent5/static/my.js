/* rent5/static/my.js — 내 화면: 내 계약 · 내 매물 올리기/내리기 · 내 정보 */
(async function () {
  "use strict";
  const { esc, won, man, when, tel } = R5;
  const $ = (id) => document.getElementById(id);

  const user = await R5.requireLogin();
  R5.nav(user);
  $("profile").innerHTML = `<dt>이름</dt><dd>${esc(user.name)}</dd><dt>아이디</dt><dd>${esc(user.username)}</dd><dt>휴대폰</dt><dd>${esc(user.phone)}</dd>`;

  const STATE_BADGE = { cancelled: "bad", active: "ok", signed: "ok", deposit_paid: "ok" };
  const LISTING_LABEL = { open: ["예약 받는 중", "ok"], reserved: ["예약됨 · 계약 진행 중", "warn"], closed: ["마감", ""] };

  async function loadContracts() {
    const { contracts } = await R5.api("/contracts");
    $("contracts").innerHTML = contracts.length ? contracts.map((c) => `
      <a class="card stack" href="contract.html?id=${encodeURIComponent(c.id)}">
        <div class="row between">
          <strong>${esc(c.address)}</strong>
          <span class="badge ${STATE_BADGE[c.status] || ""}">${esc(c.role === "landlord" ? "임대인" : "임차인")}</span>
        </div>
        <div class="small">${esc(c.status_label)}</div>
        <div class="small muted">상대: ${esc(c.other_name)} ${esc(c.other_phone)} · ${esc(when(c.updated_at))}</div>
      </a>`).join("") : `<span class="muted">진행 중인 계약이 없습니다. <a href="./">매물을 찾아보세요.</a></span>`;
  }

  // ── 예제 매물 선택: 고르면 내용이 채워지고, 입주 가능일 · 보증금 · 월세만 고칠 수 있다 ──
  let catalog = [], listedIds = new Set();
  const FIXED = ["address", "room", "leaseType", "mgmt", "description"];
  const optionLabel = (c) => `${c.address} ${c.room} · ${c.lease_type} ${R5.man(c.deposit)}${c.lease_type === "전세" ? "" : ` / 월 ${R5.man(c.rent)}`}${listedIds.has(c.id) ? " (공개 중)" : ""}`;

  function applyCatalog() {
    const c = catalog.find((x) => x.id === $("catalog").value);
    FIXED.forEach((f) => { $(f).disabled = !!c; });                 // 예제를 고르면 고정, 직접 입력이면 모두 열린다
    if (c) {
      $("address").value = c.address; $("room").value = c.room; $("leaseType").value = c.lease_type;
      $("deposit").value = c.deposit; $("rent").value = c.rent; $("mgmt").value = c.mgmt;
      $("availableFrom").value = c.available_from || ""; $("description").value = c.description || "";
    }
    $("rentBox").hidden = $("leaseType").value === "전세";
    $("btnListing").disabled = !!c && listedIds.has(c.id);
    $("catalogHint").textContent = c
      ? (listedIds.has(c.id) ? "이미 공개 중인 매물입니다. 다른 매물을 고르세요." : "✏️ 표시된 입주 가능일 · 보증금 · 월세만 고칠 수 있습니다.")
      : "직접 입력: 모든 칸을 적어 새 매물을 올립니다.";
  }

  function fillCatalog() {
    const keep = $("catalog").value;
    $("catalog").innerHTML = catalog.map((c) => `<option value="${esc(c.id)}">${esc(optionLabel(c))}</option>`).join("")
      + `<option value="">✏️ 직접 입력 (새 매물)</option>`;
    $("catalog").value = catalog.some((c) => c.id === keep) || keep === "" && $("catalog").dataset.ready ? keep : (catalog.find((c) => !listedIds.has(c.id)) || catalog[0] || {}).id || "";
    $("catalog").dataset.ready = "1";
    applyCatalog();
  }
  $("catalog").addEventListener("change", applyCatalog);

  async function loadListings() {
    const { listings } = await R5.api("/my/listings");
    listedIds = new Set(listings.filter((l) => l.catalog_id && ["open", "reserved"].includes(l.status)).map((l) => l.catalog_id));
    if (catalog.length) fillCatalog();
    $("myListings").innerHTML = listings.length ? listings.map((l) => {
      const [label, tone] = LISTING_LABEL[l.status] || [l.status, ""];
      return `<div class="card stack">
        <div class="row between"><strong>${esc(l.address)} ${esc(l.room)}</strong><span class="badge ${tone}">${esc(label)}</span></div>
        <div class="small">${esc(l.lease_type)} · 보증금 ${man(l.deposit)}${l.lease_type === "전세" ? "" : ` / 월 ${man(l.monthly)}`} · 입주 ${esc(l.available_from || "협의")}</div>
        <div class="small muted">진행 중인 예약 ${l.applications}건${l.contact_phone ? ` · 문의 번호 ${tel(l.contact_phone)}` : ""}</div>
        ${l.status === "open" ? `<button class="btn sm danger" type="button" data-withdraw="${esc(l.id)}">매물 내리기</button>` : ""}
      </div>`;
    }).join("") : `<span class="muted">올린 매물이 없습니다.</span>`;
  }

  $("myListings").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-withdraw]");
    if (!btn) return;
    if (btn.dataset.armed !== "1") {                      // 한 번 더 눌러야 내려간다 (브라우저 기본 팝업 대신)
      btn.dataset.armed = "1";
      btn.textContent = "정말 내릴까요? 한 번 더 누르세요";
      return;
    }
    R5.run(btn, async () => {
      await R5.api(`/listings/${encodeURIComponent(btn.dataset.withdraw)}/withdraw`, { method: "POST" });
      R5.notify("ok", "매물을 내렸습니다.", "대기 중이던 신청도 함께 마감했습니다.");
      await Promise.all([loadListings(), loadContracts()]);
    });
  });

  $("contactPhone").value = user.phone;                  // 내 번호가 기본값 (바꾸거나 비울 수 있다)
  $("leaseType").addEventListener("change", () => { $("rentBox").hidden = $("leaseType").value === "전세"; });
  R5.api("/catalog").then((d) => { catalog = d.catalog; fillCatalog(); }).catch(R5.fail);

  $("listingForm").addEventListener("submit", (e) => {
    e.preventDefault();
    R5.run($("btnListing"), async () => {
      await R5.api("/listings", { method: "POST", body: {
        address: $("address").value, room: $("room").value, lease_type: $("leaseType").value,
        deposit: $("deposit").value, rent: $("rent").value, mgmt: $("mgmt").value,
        available_from: $("availableFrom").value, description: $("description").value, contact_phone: $("contactPhone").value,
        catalog_id: $("catalog").value } });
      $("contactPhone").value = user.phone;
      R5.notify("ok", "매물을 공개했습니다.", "누구나 보는 첫 화면 목록에 나옵니다.");
      await loadListings();
    }, "공개 중...");
  });

  $("pwForm").addEventListener("submit", (e) => {
    e.preventDefault();
    R5.run($("btnPw"), async () => {
      const res = await R5.api("/me/password", { method: "POST", body: { current: $("current").value, new: $("newPw").value } });
      R5.token.set(res.token);
      $("pwForm").reset();
      R5.notify("ok", "비밀번호를 바꿨습니다.", "다른 기기의 로그인은 끝났습니다.");
    }, "변경 중...");
  });

  await Promise.all([loadContracts(), loadListings()]).catch(R5.fail);
})();
