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
 *   R3Shell.isAdminSession() / isAdminProxy() / proxyBanner(라벨) → 관리인 대리 접속 중 위쪽 띠(보기 전용 안내 + 관리인 페이지로 돌아가기)
 *   R3Shell.openSupportChat() → '관리인에게 문의' 창(지원 채팅). R3Shell.watchSupport() → 안 읽은 글 수·관리인 접속 알림 확인
 *   R3Shell.openSupportSettings() → '관리인 원격 지원 허용 ON/OFF' 창 (임대인·임차인 앱 메뉴에서 연다). r3_modal.js 가 먼저 있어야 한다.
 *   R3Shell.openShortcutSettings() → '바로가기 · 간편 비밀번호' 창 (입주 임차인 앱 메뉴): PIN 정하기, 이 기기 바로가기 만들기, 기기별 해제.
 *   R3Shell.pinPending() → 주소에 qk(바로가기 키)가 있고 아직 로그인 전이거나, 주소에 invite(입주 안내 링크)가 있으면 true.
 *     이때 이 파일이 PIN 입력 화면 / 가입(비밀번호 정하기) 화면을 덮어 띄우므로 앱은 시작하지 않는다.
 *   R3Shell.installHelp() → '홈 화면에 추가' 안내 창(아이폰·안드로이드).
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
    // 서랍 맨 아래: 어느 앱에서나 포털 홈으로 가거나 로그아웃할 수 있게 한다(공유 휴대폰에서 꼭 필요)
    html += '<div class="r3-drawer__group">계정</div><a href="/rent3/">🌐 포털 홈</a><a href="/rent3/?logout=1">🚪 로그아웃</a>';
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


  // ── 스마트폰 바로가기 + 간편 비밀번호(PIN) ──
  var SHORTCUT_API = "/api/rent3/pool/shortcut";
  var urlParams = new URLSearchParams(global.location.search);
  var QK = urlParams.get("qk") || "";

  function readJson(key) { try { return JSON.parse(global.localStorage.getItem(key) || "null"); } catch (e) { return null; } }
  function tokenAlive(tok) {
    try {
      var p = JSON.parse(atob(String(tok).split(".")[0].replace(/-/g, "+").replace(/_/g, "/")));
      return p.exp * 1000 > Date.now() + 30000;
    } catch (e) { return false; }
  }
  function needsPin() {
    if (!QK) return false;
    var tok = global.localStorage.getItem("bass_token") || "";
    var sess = readJson("rent3_session");
    var tid = urlParams.get("tenant") || "";
    var same = sess && sess.role === "tenant" && (!tid || sess.tenant_id === tid || sess.id === tid);
    return !(tok && tokenAlive(tok) && same);
  }
  var INVITE = urlParams.get("invite") || "";
  var PIN_PENDING = needsPin() || Boolean(INVITE);
  function pinPending() { return PIN_PENDING; }

  // 바로가기로 열린 화면: 홈 화면 이름·아이콘·매니페스트를 이 기기 것으로 맞춘다
  function applyShortcutHead(qk, label) {
    function link(rel, href, extra) {
      var el = document.querySelector('link[rel="' + rel + '"]');
      if (!el) { el = document.createElement("link"); el.rel = rel; document.head.appendChild(el); }
      el.href = href;
      if (extra) el.setAttribute("sizes", extra);
    }
    link("manifest", SHORTCUT_API + "/manifest.json?qk=" + encodeURIComponent(qk));
    link("apple-touch-icon", "/rent3_tenant/icons/apple-touch-icon.png?v=1", "180x180");
    function meta(name, val) {
      var el = document.querySelector('meta[name="' + name + '"]');
      if (!el) { el = document.createElement("meta"); el.name = name; document.head.appendChild(el); }
      el.content = val;
    }
    meta("apple-mobile-web-app-capable", "yes");
    meta("mobile-web-app-capable", "yes");
    meta("theme-color", "#2563eb");
    if (label) { meta("apple-mobile-web-app-title", label); document.title = label; }
  }

  function showPinGate() {
    var ov = document.createElement("div");
    ov.id = "r3PinGate";
    ov.style.cssText = "position:fixed;inset:0;z-index:99999;background:#fff;color:#111;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:16px;font-family:inherit;";
    ov.innerHTML =
      '<div style="font-size:40px;line-height:1;">🏠</div>' +
      '<div id="r3PinWho" style="font-size:17px;font-weight:700;margin:10px 0 2px;text-align:center;">확인하는 중…</div>' +
      '<div style="font-size:13px;color:#555;margin-bottom:14px;">간편 비밀번호 4자리를 눌러 주세요</div>' +
      '<div id="r3PinDots" style="display:flex;gap:14px;margin-bottom:10px;"></div>' +
      '<div id="r3PinMsg" style="min-height:36px;font-size:13px;color:#dc2626;text-align:center;max-width:300px;"></div>' +
      '<div id="r3PinPad" style="display:grid;grid-template-columns:repeat(3,72px);gap:12px;margin-top:6px;"></div>' +
      '<a href="/rent3/?login=1" style="margin-top:10px;padding:12px 18px;font-size:14px;color:#2563eb;display:inline-block;">아이디·비밀번호로 로그인</a>';
    document.body.appendChild(ov);
    var dots = ov.querySelector("#r3PinDots"), msg = ov.querySelector("#r3PinMsg"), pad = ov.querySelector("#r3PinPad");
    var pin = "", busy = false, dead = false;
    function drawDots() {
      dots.innerHTML = [0, 1, 2, 3].map(function (i) {
        return '<span style="width:14px;height:14px;border-radius:50%;border:2px solid #2563eb;background:' + (i < pin.length ? "#2563eb" : "transparent") + ';display:inline-block;"></span>';
      }).join("");
    }
    var keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];
    pad.innerHTML = keys.map(function (k) {
      return k === "" ? "<span></span>" : '<button type="button" data-k="' + k + '" style="height:60px;border-radius:30px;border:1px solid #d1d5db;background:#f3f4f6;font-size:22px;color:#111;">' + k + "</button>";
    }).join("");
    function lock(text) { dead = true; msg.textContent = text; pad.style.opacity = "0.35"; }
    function submit() {
      busy = true;
      fetch("/api/rent3/pool/auth/pin-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ qk: QK, pin: pin }) })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (res.ok && res.d.token) {
            global.localStorage.setItem("bass_token", res.d.token);
            global.localStorage.setItem("rent3_session", JSON.stringify(res.d.user));
            global.location.reload();
            return;
          }
          var text = (res.d.message || res.d.detail || "로그인하지 못했습니다.") + (res.d.hint ? " " + res.d.hint : "");
          pin = ""; drawDots(); busy = false;
          if (/잠겼|해제|등록되지|입주 중인|동의/.test(text)) lock(text); else msg.textContent = text;
        })
        .catch(function () { pin = ""; drawDots(); busy = false; msg.textContent = "서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요."; });
    }
    pad.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-k]");
      if (!b || busy || dead) return;
      msg.textContent = "";
      if (b.dataset.k === "⌫") pin = pin.slice(0, -1);
      else if (pin.length < 4) pin += b.dataset.k;
      drawDots();
      if (pin.length === 4) submit();
    });
    drawDots();
    fetch(SHORTCUT_API + "/identify?qk=" + encodeURIComponent(QK))
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var who = ov.querySelector("#r3PinWho");
        if (!res.ok) { who.textContent = "바로가기를 쓸 수 없습니다"; lock(res.d.message || res.d.detail || "등록되지 않았거나 해제된 바로가기입니다."); return; }
        var d = res.d;
        who.textContent = ((d.building_name ? d.building_name + " " : "") + (d.room_no ? d.room_no + "호 · " : "") + d.masked_name + "님").trim();
        applyShortcutHead(QK, ((d.building_name || "내 집") + " " + d.room_no + "호").trim());
        if (!d.eligible) lock("현재 입주 중인 계정이 아니어서 바로가기를 쓸 수 없습니다.");
        else if (d.locked) lock("간편 비밀번호를 5번 틀려 잠겼습니다. 아이디·비밀번호로 한 번 로그인하면 다시 쓸 수 있습니다.");
        else if (!d.has_pin) lock("간편 비밀번호가 설정되어 있지 않습니다. 아이디·비밀번호로 로그인해 주세요.");
      })
      .catch(function () { msg.textContent = "서버에 연결하지 못했습니다."; });
  }
  function showInviteSetup() {
    var ov = document.createElement("div");
    ov.id = "r3InviteSetup";
    ov.style.cssText = "position:fixed;inset:0;z-index:99999;background:#fff;color:#111;overflow-y:auto;padding:20px 16px;font-family:inherit;";
    var inp = "width:100%;box-sizing:border-box;height:46px;font-size:16px;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;margin:4px 0 10px;color:#111;background:#fff;";
    ov.innerHTML = '<div style="max-width:380px;margin:0 auto;">' +
      '<div style="font-size:40px;line-height:1;text-align:center;">🏠</div>' +
      '<div id="r3InvWho" style="font-size:17px;font-weight:700;margin:10px 0 2px;text-align:center;">확인하는 중…</div>' +
      '<div style="font-size:13px;color:#555;margin-bottom:14px;text-align:center;">비밀번호를 정하면 바로 시작합니다.</div>' +
      '<div id="r3InvForm" hidden>' +
      '<label style="font-size:13px;font-weight:700;">비밀번호 (8자 이상, 영문+숫자)</label><input id="r3InvPw" type="password" autocomplete="new-password" style="' + inp + '">' +
      '<label style="font-size:13px;font-weight:700;">비밀번호 다시 입력</label><input id="r3InvPw2" type="password" autocomplete="new-password" style="' + inp + '">' +
      '<label style="font-size:13px;font-weight:700;">간편 비밀번호 숫자 4자리 <span style="font-weight:400;color:#666;">(선택 · 홈 화면 아이콘으로 바로 들어갈 때 씁니다)</span></label>' +
      '<input id="r3InvPin" type="password" inputmode="numeric" maxlength="4" autocomplete="off" placeholder="비워 두면 나중에 정해도 됩니다" style="' + inp + '">' +
      '<label style="display:flex;gap:10px;align-items:flex-start;font-size:13px;line-height:1.5;margin:6px 0 12px;min-height:44px;"><input id="r3InvAgree" type="checkbox" style="width:22px;height:22px;flex:none;margin-top:2px;"><span>개인정보 수집·이용에 동의합니다. <a href="/rent3/privacy.html" target="_blank" rel="noopener" style="color:#2563eb;display:inline-block;padding:12px 4px;margin:-12px 0;">처리방침 보기</a></span></label>' +
      '<div id="r3InvMsg" style="min-height:20px;font-size:13px;color:#dc2626;margin-bottom:8px;"></div>' +
      '<button id="r3InvGo" type="button" style="width:100%;height:50px;font-size:17px;font-weight:700;border:0;border-radius:10px;background:#2563eb;color:#fff;">시작하기</button>' +
      '</div>' +
      '<div style="text-align:center;"><a href="/rent3/?login=1" style="display:inline-block;margin-top:14px;padding:12px 18px;font-size:14px;color:#2563eb;">이미 비밀번호가 있으면 로그인</a></div></div>';
    document.body.appendChild(ov);
    var $ = function (id) { return ov.querySelector("#" + id); };
    var msg = $("r3InvMsg");
    fetch(SHORTCUT_API.replace("/shortcut", "/invite") + "/identify?token=" + encodeURIComponent(INVITE))
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) { $("r3InvWho").textContent = "사용할 수 없는 안내 링크입니다"; msg.textContent = ""; var m = document.createElement("div"); m.style.cssText = "font-size:13px;color:#dc2626;text-align:center;"; m.textContent = (res.d.message || "") + " " + (res.d.hint || ""); $("r3InvWho").after(m); return; }
        var d = res.d;
        $("r3InvWho").textContent = ((d.building_name ? d.building_name + " " : "") + d.room_no + "호 · " + d.masked_name + "님").trim();
        $("r3InvForm").hidden = false;
      })
      .catch(function () { $("r3InvWho").textContent = "서버에 연결하지 못했습니다"; });
    $("r3InvGo").addEventListener("click", function () {
      var pw = $("r3InvPw").value, pin = $("r3InvPin").value.trim();
      msg.textContent = "";
      if (pw !== $("r3InvPw2").value) { msg.textContent = "비밀번호가 서로 다릅니다."; return; }
      if (pin && !/^\d{4}$/.test(pin)) { msg.textContent = "간편 비밀번호는 숫자 4자리입니다."; return; }
      if (!$("r3InvAgree").checked) { msg.textContent = "개인정보 수집·이용에 동의해 주세요."; return; }
      var btn = this; btn.disabled = true;
      fetch("/api/rent3/pool/auth/invite-accept", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: INVITE, password: pw, pin: pin, agree_privacy: true }) })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          btn.disabled = false;
          if (!res.ok || !res.d.token) { msg.textContent = (res.d.message || res.d.detail || "가입하지 못했습니다.") + (res.d.hint ? " " + res.d.hint : ""); return; }
          global.localStorage.setItem("bass_token", res.d.token);
          global.localStorage.setItem("rent3_session", JSON.stringify(res.d.user));
          global.location.replace(res.d.start_url || res.d.redirect_url);
        })
        .catch(function () { btn.disabled = false; msg.textContent = "서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요."; });
    });
  }

  function installHelp() {
    var ua = global.navigator.userAgent || "";
    var ios = /iPhone|iPad|iPod/.test(ua);
    global.R3.openModal("📲 홈 화면에 추가", '<div style="font-size:14px;line-height:1.8;">' +
      (ios ? "<strong>아이폰</strong>: Safari 아래 <strong>공유(□↑) → 홈 화면에 추가</strong>를 누르세요."
           : "<strong>안드로이드</strong>: 브라우저 메뉴(⋮) → <strong>홈 화면에 추가</strong>(또는 앱 설치)를 누르세요.") +
      "<br>추가한 아이콘을 누르면 <strong>간편 비밀번호 4자리</strong>만으로 내 집 화면이 열립니다.</div>");
  }

  if (INVITE) {
    if (document.body) showInviteSetup(); else document.addEventListener("DOMContentLoaded", showInviteSetup);
  } else if (PIN_PENDING) {
    if (document.body) showPinGate(); else document.addEventListener("DOMContentLoaded", showPinGate);
  } else if (QK) {
    applyShortcutHead(QK, "");   // 이미 로그인된 상태로 열려도 매니페스트는 이 기기 것으로
  }

  var installPrompt = null;
  global.addEventListener("beforeinstallprompt", function (e) { e.preventDefault(); installPrompt = e; });

  function openShortcutSettings() {
    function call(path, opts) {
      return fetch(SHORTCUT_API + path, Object.assign({ headers: authHeaders(Boolean(opts && opts.body)) }, opts || {}))
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
    }
    call("/status").then(function (res) {
      if (!res.ok) { notify("error", res.d.message || res.d.detail || "상태를 불러오지 못했습니다."); return; }
      var st = res.d;
      if (!st.applicable) { notify("warning", "입주한 임차인만 쓸 수 있습니다."); return; }
      var fresh = null;   // 방금 만든 기기의 안내
      function render() {
        var ua = global.navigator.userAgent || "";
        var ios = /iPhone|iPad|iPod/.test(ua);
        var html =
          '<p style="font-size:13.5px;line-height:1.7;margin:0 0 8px;">홈 화면 아이콘을 누르면 <strong>4자리 숫자</strong>만으로 내 집 화면이 열립니다. 이 기기에서만 쓰이고, 언제든 해제할 수 있습니다.</p>' +
          '<ul style="font-size:12.5px;line-height:1.6;margin:0 0 10px;padding-left:18px;">' +
          "<li>열람·납부·메시지·A/S 요청 같은 일상 업무만 됩니다.</li>" +
          "<li>계약 동의·서명, 퇴실 정산 동의, 설정 변경은 아이디·비밀번호로 로그인해야 합니다.</li>" +
          "<li>5번 틀리면 그 기기는 잠기고, 아이디·비밀번호로 로그인하면 풀립니다.</li></ul>";
        if (st.via_pin) {
          html += '<div style="font-size:13px;padding:8px;background:rgba(245,158,11,.22);border-radius:6px;margin-bottom:8px;">간편 비밀번호로 들어온 상태입니다. 설정을 바꾸려면 아이디·비밀번호로 다시 로그인해 주세요.</div>';
        } else {
          html += '<div style="font-weight:700;font-size:14px;margin:6px 0;">① 간편 비밀번호 ' + (st.has_pin ? "(설정됨)" : "(아직 없음)") + "</div>" +
            '<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;"><input id="r3PinNew" type="password" inputmode="numeric" maxlength="4" autocomplete="off" placeholder="숫자 4자리" style="width:120px;height:44px;box-sizing:border-box;font-size:16px;padding:8px;border:1px solid #d1d5db;border-radius:6px;color:#111;background:#fff;">' +
            '<button type="button" id="r3PinSave" class="bass-btn bass-btn--primary">' + (st.has_pin ? "바꾸기" : "정하기") + "</button></div>";
        }
        html += '<div style="font-weight:700;font-size:14px;margin:6px 0;">② 이 기기 바로가기</div>';
        if (!st.via_pin) {
          html += '<div style="margin-bottom:8px;"><button type="button" id="r3DevAdd" class="bass-btn"' + (st.has_pin ? "" : " disabled") + ">📲 이 기기에 바로가기 만들기</button>" +
            (st.has_pin ? "" : '<span style="font-size:12px;opacity:.75;margin-left:6px;">먼저 간편 비밀번호를 정해 주세요.</span>') + "</div>";
        }
        if (fresh) {
          html += '<div style="font-size:13px;line-height:1.7;padding:10px;background:rgba(99,102,241,.22);border-radius:6px;margin-bottom:8px;">' +
            (ios ? "<strong>아이폰</strong>: Safari 아래 <strong>공유(□↑) → 홈 화면에 추가</strong>를 누르세요."
                 : '<strong>안드로이드</strong>: ' + (installPrompt ? '<button type="button" id="r3DevInstall" class="bass-btn bass-btn--sm bass-btn--primary">홈 화면에 설치</button> 를 누르거나, ' : "") + "브라우저 메뉴(⋮) → <strong>홈 화면에 추가</strong>를 누르세요.") +
            "<br>추가한 아이콘을 누르면 간편 비밀번호 화면이 나옵니다.</div>";
        }
        html += st.devices.length ? st.devices.map(function (d) {
          return '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.35);font-size:13px;">' +
            "<span>📱 " + esc(d.label) + (d.locked ? ' <b style="color:#dc2626;">잠김</b>' : "") + '<br><span style="opacity:.7;font-size:11.5px;">' + esc((d.created_at || "").slice(0, 10)) + " 등록" + (d.last_used_at ? " · 최근 " + esc(d.last_used_at.slice(0, 16).replace("T", " ")) : "") + "</span></span>" +
            (st.via_pin ? "" : '<button type="button" class="bass-btn bass-btn--sm" data-rev="' + esc(d.id) + '">해제</button>') + "</div>";
        }).join("") : '<div style="font-size:12.5px;opacity:.7;">등록된 기기가 없습니다.</div>';
        var body = global.R3.openModal("📲 바로가기 · 간편 비밀번호", html);
        var save = body.querySelector("#r3PinSave");
        if (save) save.addEventListener("click", function () {
          call("/pin", { method: "POST", body: JSON.stringify({ pin: body.querySelector("#r3PinNew").value }) }).then(function (r) {
            if (!r.ok) { notify("error", r.d.message || r.d.detail || "저장하지 못했습니다."); return; }
            notify("success", r.d.message);
            st.has_pin = true; render();
          });
        });
        var add = body.querySelector("#r3DevAdd");
        if (add) add.addEventListener("click", function () {
          var label = ios ? "내 아이폰" : "내 휴대폰";
          call("/devices", { method: "POST", body: JSON.stringify({ label: label }) }).then(function (r) {
            if (!r.ok) { notify("error", r.d.message || r.d.detail || "만들지 못했습니다."); return; }
            applyShortcutHead(r.d.qk, "");
            try { global.history.replaceState(null, "", r.d.start_url); } catch (e) { /* 주소 바꾸기가 막혀도 안드로이드 설치는 매니페스트를 쓴다 */ }
            fresh = r.d; call("/status").then(function (r2) { if (r2.ok) st = r2.d; render(); });
          });
        });
        var inst = body.querySelector("#r3DevInstall");
        if (inst) inst.addEventListener("click", function () { if (installPrompt) { installPrompt.prompt(); installPrompt = null; } });
        Array.prototype.forEach.call(body.querySelectorAll("[data-rev]"), function (b) {
          b.addEventListener("click", function () {
            call("/devices/" + encodeURIComponent(b.getAttribute("data-rev")) + "/revoke", { method: "POST", body: "{}" }).then(function (r) {
              if (!r.ok) { notify("error", r.d.message || r.d.detail || "해제하지 못했습니다."); return; }
              notify("success", r.d.message); st.devices = r.d.devices; render();
            });
          });
        });
      }
      render();
    });
  }


  // ── 관리인 대리 접속 띠 (admin 세션이고 주소에 proxy= 가 있을 때만) ──
  function sessionRole() {
    try { return (JSON.parse(global.localStorage.getItem("rent3_session") || "null") || {}).role || ""; } catch (e) { return ""; }
  }
  function isAdminSession() { var r = sessionRole(); return r === "admin" || r === "superadmin"; }
  function isAdminProxy() { return isAdminSession() && /[?&]proxy=/.test(global.location.search); }

  function proxyBanner(label) {
    if (!isAdminProxy()) return;
    var old = document.getElementById("r3ProxyBar");
    if (old) old.remove();
    var bar = document.createElement("div");
    bar.id = "r3ProxyBar";
    bar.style.cssText = "position:sticky;top:0;z-index:99990;background:#f59e0b;color:#1f1500;padding:8px 12px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap;font-size:13px;font-weight:700;";
    bar.innerHTML = "<span>🛡️ 관리인 대리 접속 중 (보기 전용): " + esc(label) + "</span>" +
      '<a href="/rent3_admin/" style="color:#1f1500;background:#fff7e0;border:1px solid #b45309;border-radius:8px;padding:5px 10px;text-decoration:none;">← 관리인 페이지로</a>';
    document.body.insertBefore(bar, document.body.firstChild);
  }

  // ── 지원 채팅 (사용자 ↔ 관리인) ──
  var SUPPORT_API = "/api/rent3/pool/support";

  function fmtMd(iso) {
    var t = String(iso || "");
    return t.length >= 16 ? t.slice(5, 7) + "." + t.slice(8, 10) + " " + t.slice(11, 16) : "";
  }

  function openSupportChat() {
    function load() {
      fetch(SUPPORT_API + "/thread", { headers: authHeaders(false) })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) { notify("error", res.d.message || res.d.detail || "대화를 불러오지 못했습니다."); return; }
          render(res.d);
        });
    }
    function render(d) {
      var rows = (d.messages || []).map(function (m) {
        var who = m.sender_role === "user" ? "나" : (m.sender_role === "admin" ? "관리인" : "안내");
        var mine = m.sender_role === "user";
        return '<div style="margin:6px 0;text-align:' + (mine ? "right" : "left") + ';">' +
          '<div style="display:inline-block;max-width:88%;text-align:left;padding:8px 10px;border-radius:12px;font-size:13.5px;line-height:1.5;background:' +
          (m.sender_role === "system" ? "rgba(245,158,11,.18)" : (mine ? "rgba(99,102,241,.35)" : "rgba(148,163,184,.22)")) + ';">' +
          '<div style="font-size:11px;opacity:.7;margin-bottom:2px;">' + esc(who) + " · " + esc(fmtMd(m.created_at)) + "</div>" + esc(m.body) + "</div></div>";
      }).join("") || '<div style="opacity:.7;font-size:13px;padding:8px 0;">아직 대화가 없습니다. 도움이 필요한 내용을 남겨 주세요.</div>';
      var body = global.R3.openModal("💬 관리인에게 문의",
        '<div id="r3SupList" style="max-height:44vh;overflow-y:auto;padding:2px 2px 6px;">' + rows + "</div>" +
        '<textarea id="r3SupMsg" class="bass-input" rows="3" placeholder="도움이 필요한 내용을 적어 주세요" style="width:100%;margin-top:6px;"></textarea>' +
        '<div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap;">' +
        '<button type="button" id="r3SupSend" class="bass-btn bass-btn--primary">보내기</button>' +
        '<span style="font-size:12px;opacity:.75;">대화는 ' + (d.retention_days || 10) + '일 뒤 삭제됩니다. 전화로 요청하셔도 관리인이 접수 내용을 여기에 남깁니다.</span></div>' +
        (d.support_access ? '<p style="font-size:12px;margin:8px 0 0;color:var(--ok,#16a34a);">원격 지원 허용 중 — 관리인이 보기 전용으로 접속해 도와드릴 수 있습니다.</p>'
          : '<p style="font-size:12px;margin:8px 0 0;opacity:.8;">관리인이 화면을 보며 도우려면 메뉴의 <strong>원격 지원 허용 설정</strong>을 켜 주세요.</p>'));
      var list = body.querySelector("#r3SupList");
      list.scrollTop = list.scrollHeight;
      body.querySelector("#r3SupSend").addEventListener("click", function () {
        var ta = body.querySelector("#r3SupMsg");
        var text = ta.value.trim();
        if (!text) { notify("warning", "내용을 입력해 주세요."); return; }
        fetch(SUPPORT_API + "/messages", { method: "POST", headers: authHeaders(true), body: JSON.stringify({ body: text }) })
          .then(function (r) { return r.json().then(function (x) { return { ok: r.ok, x: x }; }); })
          .then(function (r2) {
            if (!r2.ok) { notify("error", r2.x.message || r2.x.detail || "보내지 못했습니다."); return; }
            load();
          });
      });
    }
    load();
  }

  // 안 읽은 관리인 글 수(메뉴 단추 옆)와 '관리인이 접속 중' 알림을 30초마다 확인한다. 관리인 세션에서는 하지 않는다.
  function watchSupport() {
    if (isAdminSession() || !(global.Bass && global.Bass.token())) return;
    function poll() {
      fetch(SUPPORT_API + "/summary", { headers: authHeaders(false) })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          var btn = document.getElementById("btnSupportChat");
          if (btn) btn.textContent = "💬 관리인에게 문의" + (d.unread > 0 ? " (" + d.unread + ")" : "");
          var bar = document.getElementById("r3AdminViewing");
          if (d.admin_viewing && !bar) {
            bar = document.createElement("div");
            bar.id = "r3AdminViewing";
            bar.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:99991;background:#f59e0b;color:#1f1500;padding:8px 12px;text-align:center;font-size:13px;font-weight:700;";
            bar.textContent = "🛡️ 관리인이 내 화면을 보고 있습니다 (보기 전용)";
            document.body.appendChild(bar);
          } else if (!d.admin_viewing && bar) {
            bar.remove();
          }
        })
        .catch(function () {});
    }
    poll();
    setInterval(poll, 30000);
  }

  global.R3Shell = {
    renderMenu: renderMenu, menuTabs: menuTabs, startClock: startClock, openSupportSettings: openSupportSettings, openShortcutSettings: openShortcutSettings, pinPending: pinPending, installHelp: installHelp,
    isAdminSession: isAdminSession, isAdminProxy: isAdminProxy, proxyBanner: proxyBanner, openSupportChat: openSupportChat, watchSupport: watchSupport
  };
})(window);
