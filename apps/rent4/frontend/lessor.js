/* apps/rent4/frontend/lessor.js — 임대인 화면: 입금 문자 반영 · 수납 현황 · Excel 가져오기 */
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
    }
    $("workspace").classList.remove("hide");
    await loadBoard();
    $("buildingName").textContent = `${boardData.building} 임대인`;
    document.title = `Rent4 · ${boardData.building}`;
  }

  // ── 탭 ──
  document.querySelectorAll(".tabs button").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.dataset.panel !== btn.dataset.tab; });
    if (btn.dataset.tab === "board") loadBoard().catch(Bass.showError);
    if (btn.dataset.tab === "accounts") loadAccounts().catch(Bass.showError);
  }));

  // ── 임차인 계정 ──
  async function loadAccounts() {
    const { accounts } = await api("/lessor/accounts");
    $("accounts").innerHTML = `<thead><tr><th>호실</th><th>임차인</th><th>아이디</th><th>첫 비밀번호 코드</th><th>상태</th><th></th></tr></thead>
      <tbody>${accounts.map((a) => `<tr>
        <td><b>${esc(a.room)}</b></td><td>${esc(a.tenant_name)}</td>
        <td><code>${esc(a.username)}</code></td>
        <td>${a.initial_code ? `<code style="font-size:14px">${esc(a.initial_code)}</code>` : "—"}</td>
        <td>${a.activated ? `<span class="bass-badge bass-badge--ok">사용 중</span>` : `<span class="bass-badge bass-badge--warn">첫 로그인 전</span>`}</td>
        <td><button class="bass-btn bass-btn--sm" data-reset="${esc(a.room)}">코드 재발급</button></td>
      </tr>`).join("")}</tbody>`;
  }

  $("accounts").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-reset]");
    if (!btn) return;
    Bass.withLock(btn, async () => {
      const r = await api(`/lessor/accounts/${encodeURIComponent(btn.dataset.reset)}/reset`, { method: "POST" });
      Bass.updateAppAlert("success", `${btn.dataset.reset}호 새 코드: ${r.initial_code}`,
        `아이디 ${r.username} 와 이 코드를 임차인에게 전달하세요. 이전 비밀번호로는 더 이상 로그인할 수 없습니다.`);
      await loadAccounts();
    });
  });

  // ── 수납 현황 ──
  async function loadBoard() {
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

  // ── 입금 문자 ──
  $("btnPreview").addEventListener("click", (e) => Bass.withLock(e.currentTarget, async () => {
    const res = await api("/lessor/bank/preview", { method: "POST", body: { text: $("bankText").value } });
    preview = res.items;
    renderPreview(res.counts);
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
      `임차인 계정: 새로 만듦 ${r.accounts.created} · 초기화 ${r.accounts.reset} · 잠금 ${r.accounts.locked}. 임차인 계정 탭에서 코드를 확인하세요.`);
    year = r.year;
    await loadBoard();
  }, "반영 중... ⏳"));

  boot().catch(Bass.showError);
})();
