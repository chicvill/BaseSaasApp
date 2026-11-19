/* hamburger.js — Rent3 공용 ☰ 전체 메뉴 (네이버 스타일 기능별 아이콘 모달).
 *
 * 핵심 화면만 남기고 부가 기능(매뉴얼, Data Pool, 세무·A/S 등)을 이 모달에 모은다.
 * 사용법:
 *   <button type="button" data-r3-burger>☰ 더보기</button>
 *   <aside class="r3-drawer" data-r3-drawer> ...메뉴 항목(a/button)... </aside>
 * 메뉴 안의 항목(원본 DOM)을 그대로 그리드 타일로 배치하므로 기존 id·data-tab·href 이벤트가 그대로 동작한다.
 */
(function () {
  "use strict";

  var css = [
    "[hidden]{display:none!important}",
    ".r3-burger{display:inline-flex;align-items:center;gap:6px;cursor:pointer;font:inherit;font-weight:700;",
    "padding:8px 14px;border-radius:10px;border:1px solid var(--border,#cbd5e1);background:transparent;color:inherit}",
    ".r3-burger:hover{filter:brightness(1.08)}",
    ".r3-overlay{position:fixed;inset:0;background:rgba(15,23,42,.55);backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);z-index:9998;display:none}",
    ".r3-overlay.is-open{display:block}",
    /* 제목 앞에 놓는 아이콘형 메뉴 단추: 글자 없이 ☰ 만, 눈에 띄는 색, 누르기 쉬운 크기 */
    ".r3-burger--icon{flex:none;justify-content:center;width:48px;height:48px;min-height:48px;padding:0;border:0;border-radius:14px;",
    "color:#fff;background:linear-gradient(135deg,var(--primary,#2563eb),var(--primary-2,#0ea5e9));box-shadow:0 6px 16px -6px rgba(37,99,235,.7)}",
    ".r3-burger--icon svg{width:26px;height:26px;display:block}",
    ".r3-burger--icon:active{transform:scale(.95)}",

    /* 네이버 스타일 중앙 기능별 아이콘 모달 */
    ".r3-drawer{position:fixed;top:50%;left:50%;width:min(680px,94vw);max-height:86vh;z-index:9999;overflow-y:auto;",
    "background:#ffffff!important;color:#0f172a!important;border:1px solid #e2e8f0;border-radius:20px;",
    "box-shadow:0 24px 60px rgba(15,23,42,.32);padding:0 18px 24px;",
    "transform:translate(-50%,-46%) scale(.96);opacity:0;pointer-events:none;",
    "transition:transform .18s ease,opacity .18s ease;display:block!important}",
    ".r3-drawer.is-open{transform:translate(-50%,-50%) scale(1);opacity:1;pointer-events:auto}",

    ".r3-drawer__head{position:sticky;top:0;z-index:2;background:#ffffff;display:flex;justify-content:space-between;align-items:center;",
    "font-weight:800;font-size:17px;padding:16px 2px 12px;border-bottom:1px solid #f1f5f9;margin:0 0 12px}",
    ".r3-drawer__close{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;border-radius:12px;",
    "background:#f1f5f9;border:none;font-size:18px;font-weight:700;cursor:pointer;color:#334155}",
    ".r3-drawer__close:hover{background:#e2e8f0}",

    ".r3-menu-search{width:100%;box-sizing:border-box;min-height:44px;padding:10px 14px;margin-bottom:14px;",
    "border:1px solid #cbd5e1;border-radius:12px;background:#f8fafc;color:#0f172a;font:inherit;font-size:14px;outline:none}",
    ".r3-menu-search:focus{border-color:var(--primary,#2563eb);background:#ffffff;box-shadow:0 0 0 3px rgba(37,99,235,.12)}",
    ".r3-menu-empty{text-align:center;padding:28px 12px;color:#64748b;font-size:14px;display:none}",

    ".r3-drawer__section{margin-bottom:16px}",
    ".r3-drawer__group{font-size:13px;color:#475569;margin:4px 2px 10px;font-weight:800;display:flex;align-items:center;gap:6px}",
    ".r3-drawer__grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}",
    "@media (max-width:540px){.r3-drawer{padding:0 12px 20px;border-radius:18px}.r3-drawer__grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}}",

    /* 개별 아이콘 타일 카드 */
    ".r3-drawer a,.r3-drawer button.r3-item,.r3-drawer .tab-btn{",
    "display:flex!important;flex-direction:column;align-items:center;justify-content:flex-start;gap:8px;",
    "width:100%;min-height:92px;padding:12px 6px 10px;border-radius:14px;text-align:center;",
    "text-decoration:none;color:#1e293b;font:inherit;font-size:12.5px;font-weight:700;line-height:1.35;",
    "border:1px solid #e2e8f0;background:#ffffff;cursor:pointer;white-space:normal;word-break:keep-all;",
    "transition:transform .12s ease,background .12s ease,border-color .12s ease,box-shadow .12s ease}",
    ".r3-drawer a:hover,.r3-drawer button.r3-item:hover,.r3-drawer .tab-btn:hover{",
    "background:#f8fafc;border-color:#93c5fd;transform:translateY(-2px);box-shadow:0 6px 14px -6px rgba(37,99,235,.22)}",
    ".r3-drawer a:active,.r3-drawer button.r3-item:active,.r3-drawer .tab-btn:active{transform:scale(.97)}",
    ".r3-drawer .tab-btn.is-active{border:2px solid var(--primary,#2563eb);background:#eff6ff;color:#1d4ed8}",

    ".r3-tile-ico{display:inline-flex;align-items:center;justify-content:center;width:46px;height:46px;border-radius:14px;",
    "font-size:23px;line-height:1;background:#f1f5f9;flex:none}",
    ".r3-drawer .tab-btn.is-active .r3-tile-ico{background:#dbeafe}",
    ".r3-tile-txt{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:12.5px;line-height:1.3}",
    ".r3-drawer .r3-note{font-size:12px;color:#64748b;padding:4px 2px;margin-top:6px;display:block}"
  ].join("");

  var PALETTE = [
    "#eff6ff", "#ecfdf5", "#fef3c7", "#fce7f3", "#f3e8ff", "#e0f2fe", "#ffedd5", "#f1f5f9"
  ];

  function splitIconLabel(raw) {
    var s = String(raw || "").trim();
    if (!s) return { ico: "📌", txt: "" };
    var idx = s.indexOf(" ");
    if (idx > 0) {
      var head = s.slice(0, idx).trim();
      var rest = s.slice(idx + 1).trim();
      // 첫 토큰이 한글·영숫자가 아니면(이모지·기호) 아이콘으로 분리한다
      if (head && !/[A-Za-z0-9가-힣]/.test(head)) {
        return { ico: head, txt: rest };
      }
    }
    return { ico: "📌", txt: s };
  }

  function decorateItem(el, colorIdx) {
    if (!el || el.querySelector(".r3-tile-ico")) return;
    var parsed = splitIconLabel(el.textContent);
    var bg = PALETTE[colorIdx % PALETTE.length];
    el.setAttribute("data-r3-label", (parsed.ico + " " + parsed.txt).toLowerCase());
    el.textContent = "";
    var ico = document.createElement("span");
    ico.className = "r3-tile-ico";
    ico.style.background = bg;
    ico.setAttribute("aria-hidden", "true");
    ico.textContent = parsed.ico;
    var txt = document.createElement("span");
    txt.className = "r3-tile-txt";
    txt.textContent = parsed.txt;
    el.appendChild(ico);
    el.appendChild(txt);
  }

  function layoutGrid(drawer, head, searchWrap, emptyMsg) {
    if (!drawer) return;
    // 이미 section으로 묶여 있고 바깥에 새로 추가된 아이템이 없으면 각 아이템 아이콘만 확인한다
    var loose = Array.prototype.slice.call(drawer.children).filter(function (c) {
      return c !== head && c !== searchWrap && c !== emptyMsg && !c.classList.contains("r3-drawer__section");
    });

    if (loose.length) {
      var curSection = null;
      var curGrid = null;
      var colorIdx = 0;

      function ensureSection(titleEl) {
        curSection = document.createElement("div");
        curSection.className = "r3-drawer__section";
        if (titleEl) {
          curSection.appendChild(titleEl);
        } else {
          var defTitle = document.createElement("div");
          defTitle.className = "r3-drawer__group";
          defTitle.textContent = "주요 메뉴";
          curSection.appendChild(defTitle);
        }
        curGrid = document.createElement("div");
        curGrid.className = "r3-drawer__grid";
        curSection.appendChild(curGrid);
        drawer.insertBefore(curSection, emptyMsg);
      }

      loose.forEach(function (node) {
        if (node.classList.contains("r3-drawer__group")) {
          ensureSection(node);
        } else if (node.matches("a, button.r3-item, .tab-btn")) {
          if (!curGrid) ensureSection(null);
          decorateItem(node, colorIdx++);
          curGrid.appendChild(node);
        } else {
          if (!curSection) ensureSection(null);
          node.classList.add("r3-note");
          curSection.appendChild(node);
        }
      });
    } else {
      var tiles = drawer.querySelectorAll("a, button.r3-item, .tab-btn");
      Array.prototype.forEach.call(tiles, function (el, i) { decorateItem(el, i); });
    }
  }

  function init() {
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    var drawers = document.querySelectorAll("[data-r3-drawer]");
    if (!drawers.length) return;
    var drawer = drawers[0];
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-label", "전체 메뉴");

    var head = document.createElement("div");
    head.className = "r3-drawer__head";
    head.innerHTML = "<span>📱 전체 기능 메뉴</span>";
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "r3-drawer__close";
    closeBtn.setAttribute("aria-label", "메뉴 닫기");
    closeBtn.textContent = "✕";
    head.appendChild(closeBtn);
    drawer.insertBefore(head, drawer.firstChild);

    var searchInp = document.createElement("input");
    searchInp.type = "search";
    searchInp.className = "r3-menu-search";
    searchInp.placeholder = "🔍 기능 검색 (예: 계약, 세무, 등기부, 도어락, 정산)";
    searchInp.setAttribute("aria-label", "메뉴 기능 검색");
    drawer.insertBefore(searchInp, head.nextSibling);

    var emptyMsg = document.createElement("div");
    emptyMsg.className = "r3-menu-empty";
    emptyMsg.textContent = "검색어와 일치하는 메뉴가 없습니다.";
    drawer.appendChild(emptyMsg);

    var overlay = document.createElement("div");
    overlay.className = "r3-overlay";
    document.body.appendChild(overlay);

    function applyFilter() {
      var q = searchInp.value.trim().toLowerCase();
      var sections = drawer.querySelectorAll(".r3-drawer__section");
      var visibleTotal = 0;
      Array.prototype.forEach.call(sections, function (sec) {
        var tiles = sec.querySelectorAll("a, button.r3-item, .tab-btn");
        var secVisible = 0;
        Array.prototype.forEach.call(tiles, function (t) {
          var txt = (t.getAttribute("data-r3-label") || t.textContent || "").toLowerCase();
          var hit = !q || txt.indexOf(q) !== -1;
          t.style.display = hit ? "" : "none";
          if (hit) { secVisible++; visibleTotal++; }
        });
        sec.style.display = secVisible > 0 ? "" : "none";
      });
      emptyMsg.style.display = visibleTotal === 0 && q ? "block" : "none";
    }
    searchInp.addEventListener("input", applyFilter);

    layoutGrid(drawer, head, searchInp, emptyMsg);

    function setOpen(open) {
      if (open) {
        layoutGrid(drawer, head, searchInp, emptyMsg);
        searchInp.value = "";
        applyFilter();
        drawer.scrollTop = 0;
      }
      drawer.classList.toggle("is-open", open);
      overlay.classList.toggle("is-open", open);
      document.querySelectorAll("[data-r3-burger]").forEach(function (b) {
        b.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }

    // 앱 코드에서 drawer.classList.remove("is-open") 을 직접 호출해도 overlay가 함께 닫히도록 동기화
    if (window.MutationObserver) {
      new MutationObserver(function () {
        var isOpen = drawer.classList.contains("is-open");
        overlay.classList.toggle("is-open", isOpen);
      }).observe(drawer, { attributes: true, attributeFilter: ["class"] });
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
      b.setAttribute("aria-haspopup", "dialog");
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
