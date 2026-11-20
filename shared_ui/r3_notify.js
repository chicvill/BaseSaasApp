/* r3_notify.js — Rent3 알림 센터(관리인·임대인·임차인 공통).
 *
 * 브라우저에서 /shared/ui/r3_notify.js 로 불러온다(디스크 경로: shared_ui/r3_notify.js). bass.js·r3_modal.js 뒤에 둔다.
 *   R3Notify.start({ url, seenUrl, seenBody, openSupport })
 *     url      알림 요약 API 주소(GET). 응답: { total, urgent, items:[{kind,title,count,link,level,action,detail}] }
 *     seenUrl  안내성 알림 '확인함' 기록 주소(POST, 선택). seenBody = 같이 보낼 값(예: {branch}).
 *     openSupport  action==="support" 항목을 눌렀을 때 실행할 함수(관리인 문의 창 열기).
 * 하는 일: 30초마다(화면이 보일 때만) 요약을 불러 🔔 버튼(☰ 와 같은 줄 오른쪽 끝)에만 건수 배지를 달고, 🔔 를 누르면 종류별 알림 목록(건수·[확인하기] 링크)을 연다.
 * 앱을 열지 않은 동안 생긴 일은 서버의 원본 데이터에 남아 있다가 이때 한꺼번에 보인다. 메시지 본문·계좌 같은 개인정보는 요약에 없다.
 */
(function (global) {
  "use strict";

  var state = { data: { total: 0, urgent: 0, items: [] }, opts: null, timer: null, bell: null };
  var CSS = [
    ".r3-nbadge{position:absolute;top:-6px;right:-6px;min-width:20px;height:20px;padding:0 5px;border-radius:10px;background:#e11d48;color:#fff;",
    "font-size:12px;font-weight:800;line-height:20px;text-align:center;box-sizing:border-box;pointer-events:none}",
    ".r3-nbadge--soft{background:#f59e0b}",
    ".r3-bell{position:relative;flex:none;display:inline-flex;align-items:center;justify-content:center;width:48px;height:48px;min-height:48px;border-radius:14px;",
    "border:1px solid var(--border,#e2e8f0);background:var(--surface,#fff);font-size:22px;cursor:pointer;padding:0}",
    ".r3-bell--float{position:fixed;top:10px;right:10px;z-index:9990}",
    "[data-r3-burger]{position:relative}",
    ".r3-nlist{display:grid;gap:8px}",
    ".r3-nitem{display:flex;align-items:center;gap:10px;min-height:52px;padding:10px 12px;border-radius:12px;border:1px solid #e2e8f0;background:#fff;color:#1e293b;",
    "text-decoration:none;font:inherit;font-size:14px;text-align:left;cursor:pointer;width:100%}",
    ".r3-nitem--urgent{border-color:#fecdd3;background:#fff1f2}",
    ".r3-nitem__n{flex:none;min-width:30px;height:30px;border-radius:15px;background:#e11d48;color:#fff;font-weight:800;font-size:13px;line-height:30px;text-align:center}",
    ".r3-nitem--normal .r3-nitem__n{background:#2563eb}.r3-nitem--info .r3-nitem__n{background:#64748b}",
    ".r3-nitem__t{flex:1 1 auto;min-width:0;font-weight:700}.r3-nitem__d{display:block;font-weight:400;font-size:12.5px;color:#64748b}",
    ".r3-nitem__go{flex:none;font-size:12.5px;color:#475569}"
  ].join("");

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function ensureStyle() {
    if (document.getElementById("r3NotifyStyle")) return;
    var st = document.createElement("style"); st.id = "r3NotifyStyle"; st.textContent = CSS; document.head.appendChild(st);
  }
  function badgeText(n) { return n > 99 ? "99+" : String(n); }

  function setBadge(host, n, urgent) {
    var b = host.querySelector(":scope > .r3-nbadge");
    if (n <= 0) { if (b) b.remove(); host.removeAttribute("data-r3-notify"); return; }
    if (!b) { b = document.createElement("span"); b.className = "r3-nbadge"; host.appendChild(b); }
    b.classList.toggle("r3-nbadge--soft", !urgent);
    b.textContent = badgeText(n);
    host.setAttribute("data-r3-notify", String(n));
  }

  function ensureBell() {
    if (state.bell) return state.bell;
    var bell = document.createElement("button");
    bell.type = "button"; bell.className = "r3-bell"; bell.setAttribute("aria-label", "알림"); bell.textContent = "🔔";
    bell.addEventListener("click", openPanel);
    var burger = document.querySelector("[data-r3-burger]");
    if (burger && burger.parentNode) { bell.style.marginLeft = "auto"; burger.parentNode.appendChild(bell); }   // ☰ 와 같은 줄의 오른쪽 끝(건수는 🔔 에만 단다)
    else { bell.classList.add("r3-bell--float"); document.body.appendChild(bell); }
    state.bell = bell;
    return bell;
  }

  function render() {
    var d = state.data, urgent = d.urgent > 0;
    ensureBell();
    setBadge(state.bell, d.total, urgent);
    state.bell.setAttribute("aria-label", d.total > 0 ? "알림 " + d.total + "건" : "알림 없음");
  }

  function acked(it) { return it.detail === "확인하면 사라집니다" && state.opts && state.opts.seenUrl; }

  function openPanel() {
    var d = state.data;
    if (!global.R3 || !global.R3.openModal) { return; }
    var body = global.R3.openModal("🔔 알림 " + (d.total > 0 ? d.total + "건" : "없음"), "");
    if (!d.items.length) {
      body.innerHTML = '<div style="padding:24px 8px;text-align:center;color:#64748b;font-size:14px;">지금 처리할 알림이 없습니다. 👍</div>';
      return;
    }
    body.innerHTML = '<div class="r3-nlist">' + d.items.map(function (it, i) {
      var inner = '<span class="r3-nitem__n">' + esc(badgeText(it.count)) + '</span><span class="r3-nitem__t">' + esc(it.title) +
        (it.detail ? '<span class="r3-nitem__d">' + esc(it.detail) + '</span>' : "") + '</span><span class="r3-nitem__go">확인하기 ›</span>';
      var cls = "r3-nitem r3-nitem--" + esc(it.level);
      if (it.link && !it.action) return '<a class="' + cls + '" data-i="' + i + '" href="' + esc(it.link) + '">' + inner + "</a>";
      return '<button type="button" class="' + cls + '" data-i="' + i + '">' + inner + "</button>";
    }).join("") + '</div><div style="margin-top:10px;font-size:12.5px;color:#64748b;line-height:1.6;">처리를 마치면 알림은 저절로 사라집니다. 앱을 열지 않은 동안 생긴 일도 여기에 모여 있습니다.</div>';
    body.addEventListener("click", function (e) {
      var el = e.target.closest("[data-i]");
      if (!el) return;
      var it = d.items[Number(el.getAttribute("data-i"))];
      if (!it) return;
      if (acked(it)) { markSeen(it.kind); }
      if (it.action === "support" && state.opts && state.opts.openSupport) { global.R3.closeModal(); state.opts.openSupport(); }
      else if (it.link && el.tagName !== "A") { location.href = it.link; }
    });
  }

  function markSeen(kind) {
    var o = state.opts;
    if (!o || !o.seenUrl || !global.Bass) return;
    global.Bass.request(o.seenUrl, { method: "POST", body: Object.assign({}, o.seenBody || {}, { kind: kind }) }).then(function (r) {
      if (r && r.items) { state.data = r; render(); }
    }).catch(function () {});
  }

  function poll() {
    if (document.hidden || !global.Bass || !global.Bass.token() || !state.opts) return;
    global.Bass.request(state.opts.url).then(function (r) {
      if (r && Array.isArray(r.items)) { state.data = r; render(); }
    }).catch(function () { /* 알림을 못 불러와도 화면 사용은 막지 않는다 */ });
  }

  function start(opts) {
    if (!opts || !opts.url) return;
    ensureStyle();
    state.opts = opts;
    if (state.timer) clearInterval(state.timer);
    poll();
    state.timer = setInterval(poll, 30000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  }

  global.R3Notify = { start: start, refresh: poll, open: openPanel };
})(window);
