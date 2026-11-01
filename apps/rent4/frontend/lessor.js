/* apps/rent4/frontend/lessor.js — 임대인 화면: 당월 수납현황 · 붙여 넣기(입금 문자) · 신규 계약 · 월별 수납현황 · 임차인 계정 · Excel */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = Bass.escapeHtml;
  const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;
  const man = (n) => (n % 10000 === 0 ? String(n / 10000) : (n / 10000).toFixed(1));
  const STATUS = {
    paid: "입금", unpaid: "미납", due: "대기", vacant: "공실", move_in: "입주", move_out: "전출", prior: "", future: "",
  };
  const DEP_BADGE = {
    ready: ["ok", "반영 준비"], diff: ["warn", "금액 확인"], needs_review: ["err", "호실 확인"],
    duplicate: ["info", "이미 반영됨"], skip: ["info", "입금 아님"],
  };
  let year = new Date().getFullYear();
  let rooms = [];
  let preview = [];
  let boardData = null;
  let branch = "";      // 관리인이 고른 건물 (임대인은 서버가 자기 건물로 고정)

  /** 모든 임대인 API 호출에 건물(branch)을 붙인다. */
  const api = (path, opts) =>
    Bass.api("rent4", `${path}${path.includes("?") ? "&" : "?"}branch=${encodeURIComponent(branch)}`, opts);

  // ── 로그인 확인 (관리인 · 임대인만) ──
  async function boot() {
    const me = await Rent4.guard(["lessor", "admin"]);
    if (me.kind === "admin") {
      branch = new URLSearchParams(location.search).get("branch") || "";
      if (!branch) return Rent4.go("admin.html");
      $("backAdmin").classList.remove("hide");
    } else {
      branch = me.building.branch;
      $("buildingName").textContent = `${me.building.name} 임대인`;
      await Rent4.passwordGate(me, $("pwBox"));
    }
    $("workspace").classList.remove("hide");
    await loadBoard();
    takeHandoff();
    $("buildingName").textContent = `${boardData.building} 임대인`;
    document.title = `Rent4 · ${boardData.building}`;
  }

  // ── 버튼: 누르면 그 화면을 열고, 다시 누르면 닫는다 ──
  document.querySelectorAll(".tabs button").forEach((btn) => btn.addEventListener("click", () => {
    const open = !btn.classList.contains("is-active");
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("is-active", open && b === btn));
    document.querySelectorAll(".panel").forEach((p) => { p.hidden = !open || p.dataset.panel !== btn.dataset.tab; });
    if (!open) return;
    if (btn.dataset.tab === "board") loadBoard().catch(Bass.showError);
    if (btn.dataset.tab === "accounts") loadAccounts().catch(Bass.showError);
    if (btn.dataset.tab === "contracts") loadLeases().catch(Bass.showError);
    if (btn.dataset.tab === "listings") Promise.all([loadListings(), loadGuide()]).catch(Bass.showError);
  }));

  // ── 임대 계약 ──
  const callLease = (path, opts) => api(`/lessor/contracts${path}`, { method: "POST", ...opts });

  async function loadLeases() {
    const { contracts } = await api("/lessor/contracts");
    Rent4.contractTable($("leases"), contracts, { call: callLease, reload: loadLeases, partyLabel: "임차인" });
  }

  function leaseBody() {
    const num = (id) => Number($(id).value || 0);
    return { room: $("lRoom").value.trim(), party_name: $("lName").value.trim(), party_phone: $("lPhone").value.trim(),
      lease_type: $("lType").value, pay_timing: $("lTiming").value,
      deposit: num("lDeposit"), rent: num("lRent"), mgmt: num("lMgmt"),
      start: $("lStart").value || null, end: $("lEnd").value || null };
  }

  $("leaseForm").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock($("btnLease"), async () => {
      const res = await callLease("", { body: leaseBody() });
      Rent4.issuedNotice(res);
      $("leaseForm").reset();
      $("paperNote").classList.add("hide");
      await loadLeases();
    }, "발행 중...");
  });

  // 종이 계약서 등록: 이미 체결 → 바로 입주 + 임차인 계정 (시작은 🔑 비밀번호 초기화 링크로)
  $("btnPaper").addEventListener("click", (e) => {
    if (!$("leaseForm").reportValidity()) return;
    Bass.withLock(e.currentTarget, async () => {
      const res = await callLease("/paper", { body: leaseBody() });
      const c = res.contract;
      Bass.updateAppAlert("success", `${c.room}호 ${c.party_name}님 종이 계약서를 등록했습니다 (체결 완료).`,
        `입주 처리하고 임차인 계정 ${c.username} 을 열었습니다. 임차인 계정 탭의 🔑 비밀번호 초기화로 시작 링크를 보내 주세요.`);
      $("leaseForm").reset();
      $("paperNote").classList.add("hide");
      await Promise.all([loadLeases(), loadBoard()]);
    }, "등록 중...");
  });

  // ── 입주 안내문 · 건물 주소 ──
  let defaultGuide = "";
  async function loadGuide() {
    const g = await api("/lessor/guide");
    defaultGuide = g.default;
    $("gAddr").value = g.address || "";
    $("gText").value = g.guide || g.default;
  }
  $("gDefault").addEventListener("click", () => { $("gText").value = defaultGuide; });
  $("guideForm").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock(e.submitter, async () => {
      const text = $("gText").value.trim() === defaultGuide.trim() ? "" : $("gText").value;   // 기본 문안이면 비워 둔다
      await api("/lessor/guide", { method: "POST", body: { guide: text, address: $("gAddr").value } });
      Bass.updateAppAlert("success", "입주 안내문과 주소를 저장했습니다.");
    }, "저장 중...");
  });

  // ── 매물 공개 ──
  const allRooms = () => (boardData ? boardData.rooms.map((r) => r.room).filter((r) => r.status !== "owner") : []);
  async function loadListings() {
    const { listings } = await api("/lessor/listings");
    const open = new Set(listings.filter((l) => !l.closed_at).map((l) => l.room));
    $("mlRoom").innerHTML = allRooms().map((r) =>
      `<option value="${esc(r.room)}" ${open.has(r.room) ? "disabled" : ""}>${esc(r.room)}호 ${r.status === "occupied" ? "(거주 중)" : "(공실)"}${open.has(r.room) ? " · 공개 중" : ""}</option>`).join("");
    const man = (n) => `${(Number(n || 0) / 10000).toLocaleString("ko-KR")}만`;
    $("listingTable").innerHTML = `<thead><tr><th>호실</th><th>조건</th><th>입주</th><th>상태</th><th></th></tr></thead>
      <tbody>${listings.length ? listings.map((l) => `<tr>
        <td><b>${esc(l.room)}</b></td>
        <td style="white-space:normal;text-align:left">${esc(l.lease_type)} · 보증금 ${man(l.deposit)} · 월 ${man(l.monthly)}</td>
        <td>${esc(l.available_from || "협의")}</td>
        <td>${l.closed_at ? `<span class="bass-badge bass-badge--info">${l.closed_reason === "contracted" ? "계약 완료" : "내림"}</span>` : `<span class="bass-badge bass-badge--ok">공개 중</span>`}</td>
        <td style="text-align:right">${l.closed_at ? "" : `<button class="bass-btn bass-btn--sm bass-btn--ghost" data-close="${esc(l.id)}">내리기</button>`}</td>
      </tr>`).join("") : `<tr><td colspan="5" class="bass-muted">공개한 매물이 없습니다.</td></tr>`}</tbody>`;
  }

  $("mlRoom").addEventListener("change", () => {
    const r = allRooms().find((x) => x.room === $("mlRoom").value);
    if (r) { $("mlDeposit").value = r.deposit || 0; $("mlRent").value = r.rent || 0; $("mlMgmt").value = r.mgmt || 0; }
  });

  $("listingForm").addEventListener("submit", (e) => {
    e.preventDefault();
    Bass.withLock(e.submitter || $("listingForm").querySelector("[type=submit]"), async () => {
      const num = (id) => Number($(id).value || 0);
      await api("/lessor/listings", { method: "POST", body: {
        room: $("mlRoom").value, lease_type: $("mlType").value, pay_timing: $("mlTiming").value,
        deposit: num("mlDeposit"), rent: num("mlRent"), mgmt: num("mlMgmt"),
        available_from: $("mlFrom").value || null, description: $("mlDesc").value } });
      Bass.updateAppAlert("success", `${$("mlRoom").value}호를 매물로 공개했습니다.`, "누구나 보는 매물 목록(첫 화면)에 나옵니다.");
      $("listingForm").reset();
      await loadListings();
    }, "공개 중...");
  });

  $("listingTable").addEventListener("click", (e) => {
    const b = e.target.closest("[data-close]");
    if (!b) return;
    Bass.withLock(b, async () => {
      await api(`/lessor/listings/${b.dataset.close}/close`, { method: "POST" });
      await loadListings();
    });
  });

  // ── 임차인 계정 ──
  async function loadAccounts() {
    const { accounts } = await api("/lessor/accounts");
    $("accounts").innerHTML = `<thead><tr><th>호실</th><th>임차인</th><th>아이디</th><th>상태</th><th></th></tr></thead>
      <tbody>${accounts.map((a) => `<tr>
        <td><b>${esc(a.room)}</b></td><td>${esc(a.tenant_name)}<br><small class="bass-muted">${esc(a.phone)}</small></td>
        <td><code>${esc(a.username)}</code></td>
        <td>${a.activated ? `<span class="bass-badge bass-badge--ok">사용 중</span>`
              : a.link_sent ? `<span class="bass-badge bass-badge--info">링크 보냄</span>`
              : `<span class="bass-badge bass-badge--warn">안내 전</span>`}</td>
        <td style="text-align:right">
          <button class="bass-btn bass-btn--sm bass-btn--primary" data-act="reset" data-room="${esc(a.room)}"
            title="지금 비밀번호를 막고, 한 번만 쓰는 재설정 링크를 만들어 채팅으로 보냅니다">🔑 비밀번호 초기화</button>
          <button class="bass-btn bass-btn--sm bass-btn--ghost" data-act="moveout" data-room="${esc(a.room)}" data-name="${esc(a.tenant_name)}"
            title="공실로 바꾸고 임차인 자격을 끝냅니다 (회원은 일반 회원으로)">🚚 전출</button>
        </td>
      </tr>`).join("")}</tbody>`;
  }

  $("accounts").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const room = encodeURIComponent(btn.dataset.room);
    if (btn.dataset.act === "moveout") {
      const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      const day = window.prompt(`${btn.dataset.room}호 ${btn.dataset.name}님 전출일 (YYYY-MM-DD)\n전출하면 공실이 되고 이 임차인은 호실 · 납입 화면에 더 들어올 수 없습니다.`, today);
      if (!day) return;
      Bass.withLock(btn, async () => {
        const r = await api(`/lessor/rooms/${room}/move-out`, { method: "POST", body: { date: day.trim() } });
        Bass.updateAppAlert("success", `${r.room}호 ${r.tenant_name}님 전출 처리했습니다 (${r.move_out}).`,
          "공실이 되었습니다. 🏷️ 매물 탭에서 다시 공개할 수 있습니다.");
        await Promise.all([loadAccounts(), loadBoard()]);
      }, "처리 중...");
      return;
    }
    if (btn.dataset.act === "reset") Bass.withLock(btn, async () => {
        const r = await api(`/lessor/accounts/${room}/reset`, { method: "POST" });
        if (r.delivered === "chat") {
          Bass.updateAppAlert("success", `${r.room}호 ${r.tenant_name}님 화면의 채팅창으로 링크를 보냈습니다.`,
            "임차인이 채팅창의 '새 비밀번호 정하기'를 누르면 바로 설정하고 로그인합니다. 이전 비밀번호는 이미 막혔습니다.");
          await loadAccounts();
          return;
        }
        Rent4.shareLink({
          title: `${r.room}호 비밀번호를 초기화했습니다`, name: r.tenant_name, phone: r.phone,
          link: new URL(`reset.html?t=${encodeURIComponent(r.token)}`, location.href).href,
          message: `[Rent4] ${r.tenant_name}님, 아래 링크를 열어 새 비밀번호를 정해 주세요. 정하면 바로 로그인되고, 다음부터는 아이디 ${r.username} 또는 휴대폰 번호와 새 비밀번호로 로그인합니다. (${r.expires_hours}시간 안에, 한 번만 사용)`,
          note: "임차인이 지금 접속해 있지 않아 채팅창으로 보내지 못했습니다. 이전 비밀번호는 이미 막혔고, 새 비밀번호를 정하면 이 링크도 바로 만료됩니다.",
        });
        await loadAccounts();
      }, "초기화 중...");
  });

  // ── 수납 현황 ──
  async function loadSummary() {
    $("monthDash").innerHTML = Rent4.monthCard(await api("/lessor/summary"));
    Rent4.startClock($("monthDash"));
  }

  async function loadBoard() {
    loadSummary().catch(Bass.showError);
    boardData = await api(`/lessor/board?year=${year}`);
    rooms = boardData.rooms.map((r) => r.room).filter((r) => r.status === "occupied");
    renderBoard();
  }

  function renderBoard() {
    const d = boardData;
    $("yearLabel").textContent = `${d.year}년`;
    const unpaidRooms = d.rooms.filter((r) => r.unpaid > 0);
    const total = d.totals.reduce((s, t) => s + t.amount, 0);
    $("boardStats").innerHTML = [
      ["호실", d.rooms.filter((r) => r.room.status === "occupied").length + "실"],
      ["연간 입금", man(total) + "만원"],
      ["미납 호실", unpaidRooms.length + "실"],
      ["미납 개월", unpaidRooms.reduce((s, r) => s + r.unpaid, 0) + "개월"],
    ].map(([k, v]) => `<div class="bass-card stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");

    const head = `<thead><tr><th>호실</th>${d.totals.map((t) => `<th>${Number(t.ym.slice(5))}월</th>`).join("")}</tr></thead>`;
    const body = d.rooms.map((row) => {
      const r = row.room;
      if (r.status === "owner") {
        return `<tr><td class="who"><b>${esc(r.room)}</b><small>${esc(r.tenant_name)}</small></td><td colspan="12" class="bass-muted">—</td></tr>`;
      }
      const cells = row.months.map((c, i) => {
        const cls = [`cell`, `s-${c.status}`, c.prior ? "is-prior" : "", c.sources.includes("bank") ? "is-bank" : "",
          c.flags.length ? "has-flag" : ""].join(" ");
        const label = c.status === "paid" ? man(c.amount) : STATUS[c.status];
        const day = c.status === "paid" && c.paid_dates[0] ? `<small>${esc(String(c.paid_dates[0]).slice(5).replace("-", "/"))}</small>` : "";
        return `<td><button class="${cls}" data-room="${esc(r.room)}" data-i="${i}">${esc(label)}${day}</button></td>`;
      }).join("");
      return `<tr><td class="who"><b>${esc(r.room)}</b><small>${esc(r.tenant_name)} · ${man(r.monthly_due)}만</small></td>${cells}</tr>`;
    }).join("");
    const foot = `<tfoot><tr><td class="who">합계</td>${d.totals.map((t) => `<td>${t.amount ? man(t.amount) : ""}</td>`).join("")}</tr></tfoot>`;
    $("board").innerHTML = head + `<tbody>${body}</tbody>` + foot;
  }

  $("board").addEventListener("click", (e) => {
    const btn = e.target.closest("button.cell");
    if (!btn || btn.classList.contains("s-future")) return;
    const row = boardData.rooms.find((r) => r.room.room === btn.dataset.room);
    showDetail(row.room, row.months[Number(btn.dataset.i)]);
  });

  function showDetail(r, c) {
    const dlg = $("detail");
    const pays = c.payments.map((p) => `
      <div class="bass-row" style="justify-content:space-between">
        <span>${esc(p.paid_date || "입금일 미상")} · ${won(p.amount)} · ${p.source === "bank" ? "입금 문자" : "Excel"}</span>
        ${p.source !== "excel" ? `<button class="bass-btn bass-btn--sm bass-btn--danger" data-del="${esc(p.id)}">반영 취소</button>` : ""}
      </div>`).join("");
    dlg.innerHTML = `
      <h3 style="margin:0 0 4px">${esc(r.room)}호 · ${esc(c.ym)}</h3>
      <p class="bass-muted" style="margin:0 0 12px">${esc(r.tenant_name)} · 월 ${won(r.monthly_due)} (임차료 ${won(r.rent)} + 관리비 ${won(r.mgmt)})${c.prior ? " · 현 임차인 입주 전 기록" : ""}</p>
      <p style="margin:0 0 8px"><b>${esc(STATUS[c.status] || "-")}</b> ${c.amount ? `· ${won(c.amount)} (임차료 ${won(c.rent)} / 관리비 ${won(c.mgmt)})` : ""}</p>
      ${c.mark ? `<p class="bass-muted" style="margin:0 0 8px">수납부 표기: ${esc(c.mark)}</p>` : ""}
      <div class="stack" style="gap:8px">${pays}</div>
      ${c.flags.length ? `<p style="color:var(--diff);margin:10px 0 0">⚖️ ${c.flags.map(esc).join("<br>")}</p>` : ""}
      <div class="bass-row" style="justify-content:flex-end;margin-top:16px"><button class="bass-btn bass-btn--sm" data-close>닫기</button></div>`;
    dlg.showModal();
  }

  $("detail").addEventListener("click", (e) => {
    const dlg = $("detail");
    if (e.target === dlg || e.target.closest("[data-close]")) return dlg.close();
    const del = e.target.closest("[data-del]");
    if (del) {
      Bass.withLock(del, async () => {
        await api(`/lessor/payments/${del.dataset.del}`, { method: "DELETE" });
        dlg.close();
        Bass.updateAppAlert("success", "입금 반영을 취소했습니다.", "필요하면 문자를 다시 분석해 반영하세요.");
        await loadBoard();
      });
    }
  });

  $("prevYear").addEventListener("click", () => { year -= 1; loadBoard().catch(Bass.showError); });
  $("nextYear").addEventListener("click", () => { year += 1; loadBoard().catch(Bass.showError); });

  // ── 만능 붙여 넣기: 텍스트 · 구글 시트 주소 · 엑셀 파일 · 사진 → 입금 문자 / Excel / 계약서 ──
  let pasted = null;      // { name, type, data(dataURL) } — 붙여 넣거나 고른 파일 (서버에 저장하지 않음)

  function setFile(file) {
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) { Bass.updateAppAlert("error", "파일이 너무 큽니다 (8MB 까지)."); return; }
    const reader = new FileReader();
    reader.onload = () => {
      pasted = { name: file.name || "붙여 넣은 사진.png", type: file.type, data: reader.result };
      $("pasteFile").innerHTML = `<span class="bass-badge bass-badge--info">📎 ${esc(pasted.name)}</span>
        <button class="bass-btn bass-btn--sm bass-btn--ghost" type="button" data-clear>✕ 빼기</button>`;
      $("pasteFile").classList.remove("hide");
    };
    reader.readAsDataURL(file);
  }
  function clearFile() {
    pasted = null;
    $("pasteInput").value = "";
    $("pasteFile").classList.add("hide");
  }
  $("pasteInput").addEventListener("change", () => setFile($("pasteInput").files[0]));
  $("pasteFile").addEventListener("click", (e) => { if (e.target.closest("[data-clear]")) clearFile(); });
  $("bankText").addEventListener("paste", (e) => {
    const file = [...(e.clipboardData?.files || [])][0];
    if (file) { e.preventDefault(); setFile(file); }
  });
  $("pasteBox").addEventListener("dragover", (e) => { e.preventDefault(); $("pasteBox").classList.add("is-drop"); });
  $("pasteBox").addEventListener("dragleave", () => $("pasteBox").classList.remove("is-drop"));
  $("pasteBox").addEventListener("drop", (e) => {
    $("pasteBox").classList.remove("is-drop");
    const file = e.dataTransfer?.files?.[0];
    if (file) { e.preventDefault(); setFile(file); }
  });

  /** 관리인 화면의 만능 붙여 넣기에서 넘어온 내용이 있으면 바로 분석한다. */
  function takeHandoff() {
    let h = null;
    try { h = JSON.parse(sessionStorage.getItem("rent4.paste") || "null"); sessionStorage.removeItem("rent4.paste"); } catch (_) { h = null; }
    if (!h) return;
    $("bankText").value = h.text || "";
    if (h.file) {
      pasted = h.file;
      $("pasteFile").innerHTML = `<span class="bass-badge bass-badge--info">📎 ${esc(pasted.name)}</span>
        <button class="bass-btn bass-btn--sm bass-btn--ghost" type="button" data-clear>✕ 빼기</button>`;
      $("pasteFile").classList.remove("hide");
    }
    $("btnPreview").click();
  }

  function openTab(tab) {
    const btn = document.querySelector(`.tabs button[data-tab="${tab}"]`);
    if (!btn.classList.contains("is-active")) btn.click();
    document.querySelector(`.panel[data-panel="${tab}"]`).scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function fillLease(c) {
    const set = (id, v) => { if (v !== undefined && v !== null && v !== "" && v !== 0) $(id).value = v; };
    $("leaseForm").reset();
    set("lRoom", c.room); set("lName", c.party_name); set("lPhone", c.party_phone);
    set("lType", c.lease_type); set("lTiming", c.pay_timing);
    set("lDeposit", c.deposit); set("lRent", c.rent); set("lMgmt", c.mgmt);
    set("lStart", c.start); set("lEnd", c.end);
    $("paperNote").classList.remove("hide");
  }

  $("btnPreview").addEventListener("click", (e) => Bass.withLock(e.currentTarget, async () => {
    const body = pasted ? { file: pasted.data, filename: pasted.name, mime: pasted.type } : { text: $("bankText").value };
    const r = await api("/lessor/paste", { method: "POST", body });
    $("previewBox").classList.add("hide");
    if (r.kind === "bank") {
      const res = await api("/lessor/bank/preview", { method: "POST", body: { text: r.text } });
      preview = res.items;
      renderPreview(res.counts);
      if (r.source === "image") Bass.updateAppAlert("success", "사진에서 입금 내역을 읽었습니다.", "금액 · 입금자를 확인한 뒤 반영하세요.");
    } else if (r.kind === "excel") {
      $("excelText").value = r.text;
      openTab("import");
      const res = await api("/lessor/import/preview", { method: "POST", body: { text: r.text } });
      renderImport(res, false);
      $("btnImportCommit").disabled = false;
      Bass.updateAppAlert("success", "엑셀 수납부로 알아봤습니다.", "아래 분석 결과를 확인하고 'Knowledge Pool 에 반영'을 누르세요.");
    } else if (r.kind === "contract") {
      openTab("contracts");
      fillLease(r.contract);
      Bass.updateAppAlert("success", "종이 계약서로 알아봤습니다.", "계약 칸을 확인 · 수정한 뒤 '📄 종이 계약서로 등록'을 누르세요.");
    }
  }, "분석 중... ⏳"));

  function roomOptions(selected) {
    return `<option value="">호실 선택</option>` + rooms.map((r) =>
      `<option value="${esc(r.room)}" ${r.room === selected ? "selected" : ""}>${esc(r.room)} ${esc(r.tenant_name)}</option>`).join("");
  }

  function renderPreview(counts) {
    $("previewBox").classList.remove("hide");
    const parts = Object.entries(counts).map(([k, n]) => `${DEP_BADGE[k] ? DEP_BADGE[k][1] : k} ${n}`);
    $("previewCount").textContent = `문자 ${preview.length}건 · ${parts.join(" · ")}`;
    $("previewList").innerHTML = preview.map((it, i) => {
      const [tone, label] = DEP_BADGE[it.status] || ["info", it.status];
      const usable = ["ready", "diff", "needs_review"].includes(it.status);
      const meta = [it.bank, it.deposit_date, it.deposit_time, it.names.join(", ")].filter(Boolean).map(esc).join(" · ");
      return `<div class="bass-card dep ${usable ? "" : "is-off"}" data-i="${i}">
        <div class="dep__head">
          <label class="bass-row" style="gap:8px"><input type="checkbox" class="pick" ${it.status === "ready" ? "checked" : ""} ${usable ? "" : "disabled"}>
            <strong class="money">${won(it.amount)}</strong></label>
          <span class="bass-badge bass-badge--${tone}">${label}${it.parsed_by === "ai" ? " · AI" : ""}</span>
        </div>
        <div class="bass-muted" style="font-size:13px">${meta}</div>
        ${it.message ? `<div style="font-size:13.5px">${esc(it.message)}</div>` : ""}
        ${usable ? `<div class="dep__edit">
          <select class="bass-select bass-input f-room">${roomOptions(it.room)}</select>
          <input class="bass-input f-months months" value="${esc(it.months.join(", "))}" placeholder="귀속월 예: 2026-10, 2026-11">
          <input class="bass-input f-amount money" type="number" min="1" value="${it.amount || ""}" placeholder="입금액">
        </div>` : ""}
        <details><summary class="bass-muted" style="font-size:12px;cursor:pointer">원문</summary><pre class="dep__raw">${esc(it.raw)}</pre></details>
      </div>`;
    }).join("");
  }

  $("btnCommit").addEventListener("click", (e) => Bass.withLock(e.currentTarget, async () => {
    const items = [];
    document.querySelectorAll("#previewList .dep").forEach((el) => {
      const pick = el.querySelector(".pick");
      if (!pick || !pick.checked) return;
      const it = { ...preview[Number(el.dataset.i)] };
      it.room = el.querySelector(".f-room").value;
      it.months = el.querySelector(".f-months").value.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
      it.amount = Number(el.querySelector(".f-amount").value);
      items.push(it);
    });
    if (!items.length) {
      Bass.updateAppAlert("warning", "반영할 입금을 체크해 주세요.", "금액 확인·호실 확인 건은 내용을 고친 뒤 체크하면 반영됩니다.");
      return;
    }
    const res = await api("/lessor/bank/commit", { method: "POST", body: { items } });
    Bass.updateAppAlert("success", `입금 ${res.saved}건을 수납 기록에 반영했습니다.`, "수납 현황 탭에서 확인할 수 있습니다.");
    $("bankText").value = "";
    clearFile();
    $("previewBox").classList.add("hide");
    await loadBoard();
  }, "반영 중... ⏳"));

  // ── Excel 가져오기 ──
  $("excelText").addEventListener("input", () => { $("btnImportCommit").disabled = true; });

  function renderImport(r, committed) {
    const ok = r.totals_check.filter((t) => t.ok).length;
    const box = $("importResult");
    box.classList.remove("hide");
    box.innerHTML = `
      <strong>${committed ? "✅ 반영 완료" : "🔍 분석 결과"} — ${r.year}년 ${r.months.length}개월</strong>
      <div class="stats">
        <div class="bass-card stat"><b>${r.rooms}</b><span>호실</span></div>
        <div class="bass-card stat"><b>${r.payments}</b><span>수납 기록</span></div>
        <div class="bass-card stat"><b>${ok}/${r.totals_check.length}</b><span>월 합계 대조 일치</span></div>
        <div class="bass-card stat"><b>${r.flagged}</b><span>확인 필요</span></div>
      </div>
      ${r.warnings.length ? `<div>${r.warnings.map((w) => `<div style="color:var(--warn)">⚠️ ${esc(w)}</div>`).join("")}</div>` : ""}
      ${(r.flagged_items || []).map((f) => `<div style="color:var(--diff)">🔎 ${esc(f.room)}호 ${esc(f.ym)} '${esc(f.raw)}': ${f.flags.map(esc).join("; ")}</div>`).join("")}
      ${committed && r.skipped_existing ? `<div class="bass-muted">입금 문자로 이미 반영된 ${r.skipped_existing}건은 Excel 값으로 덮어쓰지 않았습니다.</div>` : ""}`;
  }

  $("btnImportPreview").addEventListener("click", (e) => Bass.withLock(e.currentTarget, async () => {
    const r = await api("/lessor/import/preview", { method: "POST", body: { text: $("excelText").value } });
    renderImport(r, false);
    $("btnImportCommit").disabled = false;
  }, "분석 중... ⏳"));

  $("btnImportCommit").addEventListener("click", (e) => Bass.withLock(e.currentTarget, async () => {
    const r = await api("/lessor/import/commit", { method: "POST", body: { text: $("excelText").value } });
    renderImport(r, true);
    Bass.updateAppAlert("success", `${r.year}년 수납부를 Knowledge Pool 에 반영했습니다.`,
      `임차인 계정: 새로 만듦 ${r.accounts.created} · 초기화 ${r.accounts.reset} · 잠금 ${r.accounts.locked}. 임차인 계정 탭의 🔑 비밀번호 초기화로 시작 링크를 보내세요.`);
    year = r.year;
    await loadBoard();
  }, "반영 중... ⏳"));

  boot().catch(Bass.showError);
})();
