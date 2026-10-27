(() => {
  "use strict";
  const APP = "tax";
  const $ = (id) => document.getElementById(id);
  const esc = Bass.escapeHtml;

  async function load() {
    try {
      const res = await Bass.api(APP, "/items");
      $("item-grid").innerHTML = res.items.map((it) => `
        <article class="bass-card item-card bass-reveal" data-code="${esc(it.code)}">
          <div>
            <b>${esc(it.code)}</b> · ${esc(it.name)}<br>
            <span class="bass-badge bass-badge--${it.status === 'ACTIVE' ? 'info' : it.status === 'COMPLETED' ? 'ok' : 'warn'}">${esc(it.status)}</span>
          </div>
          <div>
            ${it.status === 'READY'
              ? `<button class="bass-btn bass-btn--primary bass-btn--sm" data-act="activate">활성화</button>`
              : it.status === 'ACTIVE'
              ? `<button class="bass-btn bass-btn--sm" data-act="complete">완료</button>`
              : `<span class="bass-muted" style="font-size:13px">종료됨</span>`}
          </div>
        </article>`).join("") || "<p class='bass-muted'>항목이 없습니다.</p>";
    } catch (err) {
      Bass.showError(err);
    }
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const code = btn.closest("[data-code]").dataset.code;
    const act = btn.dataset.act;
    await Bass.withLock(btn, async () => {
      const res = await Bass.api(APP, `/items/${code}/${act}`, { method: "POST" });
      Bass.updateAppAlert("success", res.message);
      await load();
    });
  });

  $("refresh-btn").addEventListener("click", () => Bass.withLock($("refresh-btn"), load));
  load();
})();
