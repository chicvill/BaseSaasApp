/* hamburger.js — Rent3 공용 ☰ 더보기 메뉴.
 *
 * 핵심 화면만 남기고 부가 기능(매뉴얼, Data Pool, 세무·A/S 등)을 이 메뉴에 모은다.
 * 사용법:
 *   <button type="button" data-r3-burger>☰ 더보기</button>
 *   <aside class="r3-drawer" data-r3-drawer> ...메뉴 항목(a/button)... </aside>
 * 메뉴 안의 항목을 누르면 메뉴가 닫힌다. 항목은 기존 id·data-tab 을 그대로 가질 수 있어
 * 각 화면의 기존 스크립트가 그대로 동작한다.
 */
(function () {
  "use strict";

  var css = [
    "[hidden]{display:none!important}",
    ".r3-burger{display:inline-flex;align-items:center;gap:6px;cursor:pointer;font:inherit;font-weight:700;",
    "padding:8px 14px;border-radius:10px;border:1px solid var(--border,#cbd5e1);background:transparent;color:inherit}",
    ".r3-burger:hover{filter:brightness(1.08)}",
    ".r3-overlay{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:9998;display:none}",
    ".r3-overlay.is-open{display:block}",
    /* 제목 앞에 놓는 아이콘형 메뉴 단추: 글자 없이 ☰ 만, 눈에 띄는 색, 누르기 쉬운 크기 */
    ".r3-burger--icon{flex:none;justify-content:center;width:48px;height:48px;min-height:48px;padding:0;border:0;border-radius:14px;",
    "color:#fff;background:linear-gradient(135deg,var(--primary,#2563eb),var(--primary-2,#0ea5e9));box-shadow:0 6px 16px -6px rgba(37,99,235,.7)}",
    ".r3-burger--icon svg{width:26px;height:26px;display:block}",
    ".r3-burger--icon:active{transform:scale(.95)}",
    ".r3-drawer{position:fixed;top:0;left:0;bottom:0;width:min(320px,86vw);z-index:9999;overflow-y:auto;",
    "background:var(--surface,#fff);color:var(--text,#0f172a);border-right:1px solid var(--border,#e2e8f0);",
    "box-shadow:8px 0 24px rgba(0,0,0,.25);padding:16px 14px 28px;transform:translateX(-105%);",
    "transition:transform .2s ease;display:flex;flex-direction:column;gap:6px}",
    ".r3-drawer.is-open{transform:none}",
    ".r3-drawer__head{display:flex;justify-content:space-between;align-items:center;font-weight:800;",
    "font-size:16px;margin-bottom:8px}",
    ".r3-drawer__close{background:none;border:none;font-size:20px;cursor:pointer;color:inherit}",
    ".r3-drawer__group{font-size:11.5px;opacity:.65;margin:12px 4px 2px;font-weight:700}",
    ".r3-drawer a,.r3-drawer button.r3-item,.r3-drawer .tab-btn{display:block;width:100%;text-align:left;",
    "text-decoration:none;color:inherit;font:inherit;font-size:14px;padding:11px 12px;border-radius:10px;",
    "border:1px solid var(--border,#e2e8f0);background:transparent;cursor:pointer}",
    ".r3-drawer a:hover,.r3-drawer button.r3-item:hover,.r3-drawer .tab-btn:hover{background:rgba(100,116,139,.14)}",
    ".r3-drawer .tab-btn.is-active{border-color:var(--primary,#2563eb);font-weight:800}"
  ].join("");

  function init() {
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    var drawers = document.querySelectorAll("[data-r3-drawer]");
    if (!drawers.length) return;
    var drawer = drawers[0];

    var head = document.createElement("div");
    head.className = "r3-drawer__head";
    head.innerHTML = "<span>메뉴</span>";
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "r3-drawer__close";
    closeBtn.setAttribute("aria-label", "메뉴 닫기");
    closeBtn.textContent = "✕";
    head.appendChild(closeBtn);
    drawer.insertBefore(head, drawer.firstChild);

    var overlay = document.createElement("div");
    overlay.className = "r3-overlay";
    document.body.appendChild(overlay);
    // drawer 는 제자리에 둔다(position:fixed). #tabBar 안에 두면 기존 탭 클릭 위임이 그대로 동작한다.

    function setOpen(open) {
      drawer.classList.toggle("is-open", open);
      overlay.classList.toggle("is-open", open);
      document.querySelectorAll("[data-r3-burger]").forEach(function (b) {
        b.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }

    var ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M4 6.5h16M4 12h16M4 17.5h16"/></svg>';
    document.querySelectorAll("[data-r3-burger]").forEach(function (b) {
      b.classList.add("r3-burger");
      var hdr = b.closest("header.bass-card");
      var row = hdr && hdr.querySelector(":scope > .bass-row");
      if (row) {                       // 앱 머리줄: ☰ 를 제목 앞으로 옮기고 '더보기' 글자는 없앤다
        row.insertBefore(b, row.firstChild);
        row.style.cssText += ";justify-content:flex-start;flex-wrap:nowrap;align-items:center;gap:10px";
        Array.prototype.forEach.call(row.children, function (c) { if (c !== b && c.tagName === "DIV") { c.style.flex = "1 1 auto"; c.style.minWidth = "0"; } });
        b.classList.add("r3-burger--icon");
        b.innerHTML = ICON;
        b.setAttribute("aria-label", "메뉴 열기");
      }
      b.setAttribute("aria-haspopup", "true");
      b.addEventListener("click", function () { setOpen(!drawer.classList.contains("is-open")); });
    });
    closeBtn.addEventListener("click", function () { setOpen(false); });
    overlay.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    drawer.addEventListener("click", function (e) {
      if (e.target.closest("a, button.r3-item, .tab-btn")) setOpen(false);
    });
    window.Rent3Menu = { open: function () { setOpen(true); }, close: function () { setOpen(false); } };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
