/* r3_shell.js — Rent3 공용 화면 틀: 머리글 시계, ☰ 메뉴 구성.
 *
 * 브라우저에서 /shared/ui/r3_shell.js 로 불러온다(디스크 경로: shared_ui/r3_shell.js).
 * ☰ 메뉴 서랍 자체의 열고 닫기는 /rent3/hamburger.js 가 맡고, 이 파일은 서랍 안의 항목을 만든다.
 *
 *   R3Shell.renderMenu(서랍요소, 그룹목록)
 *     그룹: { title: "계약 · 매물", items: [ 항목, ... ] }   (title 이 없으면 제목 줄 없이 항목만)
 *     항목: { label, tab }      → 화면 전환 버튼(data-tab). 앱의 탭 클릭 처리에 맡긴다.
 *           { label, href }     → 링크
 *           { label, id }       → 단추(앱이 id 로 이벤트를 붙인다)
 *   R3Shell.startClock(제목요소, () => "건물이름[아이디]") → "🏢 이름 2026.11.10 17:05" 를 30초마다 갱신, 갱신 함수를 돌려준다
 *   R3Shell.menuTabs(그룹목록) → 메뉴에 있는 tab 이름 목록(테스트·점검용)
 */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function itemHtml(it) {
    if (it.tab) return '<button type="button" class="bass-btn tab-btn" data-tab="' + esc(it.tab) + '">' + esc(it.label) + "</button>";
    if (it.href) return '<a href="' + esc(it.href) + '">' + esc(it.label) + "</a>";
    return '<button type="button" class="bass-btn r3-item"' + (it.id ? ' id="' + esc(it.id) + '"' : "") + ">" + esc(it.label) + "</button>";
  }

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var st = document.createElement("style");
    // 서랍 뒤 화면이 비쳐 글이 겹쳐 보이지 않도록 바탕을 꽉 채운다
    st.textContent = ".r3-drawer{background:#17172e!important;color:#eceaf7!important}";
    document.head.appendChild(st);
  }

  function renderMenu(drawer, groups) {
    if (!drawer) return;
    ensureStyle();
    var keep = drawer.querySelector(".r3-drawer__head");   // hamburger.js 가 이미 만든 머리줄은 남긴다
    Array.prototype.slice.call(drawer.children).forEach(function (c) { if (c !== keep) c.remove(); });
    var html = "";
    groups.forEach(function (g) {
      if (g.title) html += '<div class="r3-drawer__group">' + esc(g.title) + "</div>";
      g.items.forEach(function (it) { html += itemHtml(it); });
    });
    drawer.insertAdjacentHTML("beforeend", html);
  }

  function menuTabs(groups) {
    var out = [];
    groups.forEach(function (g) { g.items.forEach(function (it) { if (it.tab) out.push(it.tab); }); });
    return out;
  }

  function startClock(el, nameFn) {
    function tick() {
      var n = new Date();
      var p = function (v) { return String(v).padStart(2, "0"); };
      var t = n.getFullYear() + "." + p(n.getMonth() + 1) + "." + p(n.getDate()) + " " + p(n.getHours()) + ":" + p(n.getMinutes());
      var name = nameFn();
      if (el && name) el.textContent = "🏢 " + name + " " + t;
    }
    setInterval(tick, 30000);
    return tick;
  }

  global.R3Shell = { renderMenu: renderMenu, menuTabs: menuTabs, startClock: startClock };
})(window);
