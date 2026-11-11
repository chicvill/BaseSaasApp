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
 *   R3Shell.openSupportSettings() → '관리인 원격 지원 허용 ON/OFF' 창 (임대인·임차인 앱 메뉴에서 연다). r3_modal.js 가 먼저 있어야 한다.
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

  var CONSENT_API = "/api/rent3/pool/auth/consents";

  function authHeaders(json) {
    var h = { Authorization: "Bearer " + (global.Bass ? global.Bass.token() : "") };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  function notify(level, msg) {
    if (global.Bass && global.Bass.updateAppAlert) global.Bass.updateAppAlert(level, msg);
  }

  function openSupportSettings() {
    fetch(CONSENT_API, { headers: authHeaders(false) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) { notify("error", res.d.message || res.d.detail || "상태를 불러오지 못했습니다."); return; }
        var d = res.d;
        if (d.applicable === false) { notify("warning", d.message || "해당하지 않습니다."); return; }
        function render() {
          var on = Boolean(d.support_access);
          var body = global.R3.openModal("🛡️ 관리인 원격 지원 허용",
            '<p style="font-size:14px;line-height:1.7;margin:0 0 8px;">사용 중 도움이 필요해 관리인에게 요청하면, 허용한 경우에 한해 관리인이 <strong>내 화면을 보기만</strong> 하며 도와드립니다.</p>' +
            '<ul style="font-size:13px;line-height:1.7;margin:0 0 10px;padding-left:18px;">' +
            "<li>본인인증·동의·서명을 대신하지 않고, 수납·계약 같은 금전·계약 기록은 고치지 않습니다.</li>" +
            "<li>접속할 때마다 기록이 남고, 접속 중이면 화면에 알려 드립니다.</li>" +
            "<li>언제든 여기서 끌 수 있습니다.</li></ul>" +
            '<div style="font-size:15px;margin-bottom:10px;">현재: <strong style="color:' + (on ? "var(--ok,#16a34a)" : "var(--err,#dc2626)") + ';">' + (on ? "허용 중 (ON)" : "허용 안 함 (OFF)") + "</strong></div>" +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
            '<button type="button" id="r3SupOn" class="bass-btn ' + (on ? "bass-btn--primary" : "") + '">허용 ON</button>' +
            '<button type="button" id="r3SupOff" class="bass-btn ' + (on ? "" : "bass-btn--primary") + '">허용 안 함 OFF</button></div>' +
            '<p style="font-size:12px;margin:10px 0 0;"><a href="/rent3/privacy.html" target="_blank" rel="noopener">개인정보 처리방침 보기</a></p>');
          function set(allow) {
            fetch(CONSENT_API + "/support", { method: "POST", headers: authHeaders(true), body: JSON.stringify({ allow: allow }) })
              .then(function (r) { return r.json().then(function (x) { return { ok: r.ok, x: x }; }); })
              .then(function (r2) {
                if (!r2.ok) { notify("error", r2.x.message || r2.x.detail || "바꾸지 못했습니다."); return; }
                d = r2.x;
                notify("success", d.message);
                render();
              });
          }
          body.querySelector("#r3SupOn").addEventListener("click", function () { set(true); });
          body.querySelector("#r3SupOff").addEventListener("click", function () { set(false); });
        }
        render();
      });
  }

  global.R3Shell = { renderMenu: renderMenu, menuTabs: menuTabs, startClock: startClock, openSupportSettings: openSupportSettings };
})(window);
