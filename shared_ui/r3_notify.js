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
    ".r3-nitem--unread{border-color:#fbcfe8;background:#fdf2f8}",       // 읽지 않은(아직 처리하지 않은) 알림: 엷은 분홍
    ".r3-nitem__n{flex:none;min-width:30px;height:30px;border-radius:15px;background:#e11d48;color:#fff;font-weight:800;font-size:13px;line-height:30px;text-align:center}",
    ".r3-nitem--normal .r3-nitem__n{background:#2563eb}.r3-nitem--info .r3-nitem__n{background:#64748b}",
    ".r3-nitem__t{flex:1 1 auto;min-width:0;font-weight:700}.r3-nitem__d{display:block;font-weight:400;font-size:12.5px;color:#64748b}",
    ".r3-nitem__go{flex:none;font-size:12.5px;color:#475569}",
    ".r3-today{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0;padding:10px 12px;border-radius:12px;background:#fff7ed;border:1px solid #fed7aa;color:#7c2d12;font-size:14px;line-height:1.5}",
    ".r3-today__t{flex:1 1 200px;min-width:0;font-weight:700}",
    ".r3-today button{flex:none;min-height:44px;padding:8px 14px;border-radius:10px;border:1px solid #fb923c;background:#fff;color:#9a3412;font:inherit;font-size:14px;font-weight:700;cursor:pointer}"
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
    bell.addEventListener("click", openFresh);
    var burger = document.querySelector("[data-r3-burger]");
    if (burger && burger.parentNode) { bell.style.marginLeft = "auto"; burger.parentNode.appendChild(bell); }   // ☰ 와 같은 줄의 오른쪽 끝(건수는 🔔 에만 단다)
    else { bell.classList.add("r3-bell--float"); document.body.appendChild(bell); }
    state.bell = bell;
    return bell;
  }

  // 첫 화면 '오늘 할 일' 한 줄 띠(opts.todayAfter 선택자 요소 바로 아래): 처리할 일이 있을 때만 보이고 0건이면 감춘다. 안내성(info)은 제외.
  function renderToday() {
    var o = state.opts;
    if (!o || !o.todayAfter) return;
    var host = document.querySelector(o.todayAfter);
    if (!host) return;
    var strip = document.getElementById("r3Today");
    var todo = (state.data.items || []).filter(function (i) { return i.level !== "info"; });
    if (!todo.length) { if (strip) strip.remove(); return; }
    if (!strip) {
      strip = document.createElement("div");
      strip.id = "r3Today"; strip.className = "r3-today"; strip.setAttribute("role", "status");
      host.insertAdjacentElement("afterend", strip);
      strip.addEventListener("click", function (e) { if (e.target.closest("button")) openPanel(); });
    }
    var shown = todo.slice(0, 3).map(function (i) { return esc(i.title) + " " + i.count + "건"; }).join(" · ");
    var more = todo.length > 3 ? " 외 " + (todo.length - 3) + "종" : "";
    strip.innerHTML = '<span class="r3-today__t">🔔 오늘 할 일: ' + shown + more + '</span><button type="button">확인하기</button>';
  }

  function render() {
    renderToday();
    var d = state.data, urgent = d.urgent > 0;
    ensureBell();
    setBadge(state.bell, d.total, urgent);
    state.bell.setAttribute("aria-label", d.total > 0 ? "알림 " + d.total + "건" : "알림 없음");
  }

  function acked(it) { return it.detail === "확인하면 사라집니다" && state.opts && state.opts.seenUrl; }

  // 🔔 을 누르면 최신 상태를 한 번 읽어 와서 연다(방금 연 채팅 기록 등도 바로 보이게). 1.5초 안에 못 읽으면 지금 가진 것으로 연다.
  function openFresh() {
    var done = false;
    function go() { if (done) return; done = true; openPanel(); }
    var t = setTimeout(go, 1500);
    if (!state.opts || !global.Bass || !global.Bass.token()) { clearTimeout(t); go(); return; }
    global.Bass.request(state.opts.url).then(function (r) {
      if (r && Array.isArray(r.items)) { state.data = r; render(); }
    }).catch(function () {}).then(function () { clearTimeout(t); go(); });
  }

  function openPanel() {
    var d = state.data;
    if (!global.R3 || !global.R3.openModal) { return; }
    var body = global.R3.openModal("🔔 알림 " + (d.total > 0 ? d.total + "건" : "없음"), "");
    var recent = (d.recent_chats || []);
    // 새 알림이 없어도 이전에 대화하던 창으로 돌아갈 수 있게 '최근 대화' 목록을 늘 함께 보여 준다
    var recentHtml = recent.length
      ? '<div style="margin:14px 0 6px;font-weight:800;font-size:14px;">💬 최근 대화</div><div class="r3-nlist">' + recent.map(function (c, i) {
          var inner = '<span class="r3-nitem__n" style="' + (c.unread > 0 ? "" : "background:#e2e8f0;color:#475569;") + '">' + (c.unread > 0 ? esc(badgeText(c.unread)) : "💬") + '</span><span class="r3-nitem__t">' + esc(c.title) +
            (c.preview ? '<span class="r3-nitem__d">' + esc(c.preview) + "</span>" : "") + '</span><span class="r3-nitem__go">열기 ›</span>';   // 제목에 상대·시작 시각이 들어 있다(대화 내용은 알림에 싣지 않는다)
          var cls = "r3-nitem r3-nitem--normal" + (c.unread > 0 ? " r3-nitem--unread" : "");
          if (c.link && !c.action) return '<a class="' + cls + '" data-c="' + i + '" href="' + esc(c.link) + '">' + inner + "</a>";
          return '<button type="button" class="' + cls + '" data-c="' + i + '">' + inner + "</button>";
        }).join("") + "</div>"
      : "";
    function bindChats() {
      body.addEventListener("click", function (e) {
        var el = e.target.closest("[data-c]");
        if (!el) return;
        var c = recent[Number(el.getAttribute("data-c"))];
        if (!c) return;
        if (c.action === "support" && state.opts && state.opts.openSupport) { global.R3.closeModal(); state.opts.openSupport(); }
        else if (c.link && el.tagName !== "A") { location.href = c.link; }
      });
    }
    if (!d.items.length) {
      body.innerHTML = '<div style="padding:18px 8px;text-align:center;color:#64748b;font-size:14px;">지금 처리할 알림이 없습니다. 👍</div>' + recentHtml;
      bindChats();
      return;
    }
    body.innerHTML = '<div class="r3-nlist">' + d.items.map(function (it, i) {
      var inner = '<span class="r3-nitem__n">' + esc(badgeText(it.count)) + '</span><span class="r3-nitem__t">' + esc(it.title) +
        (it.detail ? '<span class="r3-nitem__d">' + esc(it.detail) + '</span>' : "") + '</span><span class="r3-nitem__go">확인하기 ›</span>';
      var cls = "r3-nitem r3-nitem--" + esc(it.level) + " r3-nitem--unread";      // 지금 처리할 알림은 모두 읽지 않은 것: 처리하면 목록에서 사라진다(이력 없음)
      if (it.link && !it.action) return '<a class="' + cls + '" data-i="' + i + '" href="' + esc(it.link) + '">' + inner + "</a>";
      return '<button type="button" class="' + cls + '" data-i="' + i + '">' + inner + "</button>";
    }).join("") + '</div><div style="margin-top:10px;font-size:12.5px;color:#64748b;line-height:1.6;">처리를 마치면 알림은 저절로 사라집니다(미납 같은 일회성 알림은 이력을 남기지 않습니다). 앱을 열지 않은 동안 생긴 일도 여기에 모여 있습니다.</div>' + recentHtml;
    bindChats();
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
