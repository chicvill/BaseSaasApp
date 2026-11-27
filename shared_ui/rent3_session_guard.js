/* Rent3 앱용: 로그인이 만료됐거나 서버 재시작으로 토큰이 무효가 되면(401)
   장기 로그인 갱신 토큰(rent3_refresh_token)으로 조용히 재발급해 원래 요청을 자동 재시도한다.
   갱신 토큰이 없거나 만료·폐기된 경우에만 저장된 로그인 정보를 지우고 포털 로그인으로 보낸다.
   로그인·가입·비밀번호 재설정·갱신 요청의 401 은 건드리지 않는다. */
(function () {
  "use strict";
  if (window.__rent3GuardPatched) return;
  // Rent3: 오류·경고 알림은 자동으로 닫히지 않고 [확인]을 눌러야 닫힌다(bass.js).
  window.__rent3GuardPatched = true;
  var REFRESH_KEY = "rent3_refresh_token";
  var orig = window.fetch.bind(window);
  var redirecting = false;
  var refreshPromise = null;

  function isViaPinToken(tok) {
    try {
      if (!tok) return false;
      var p = JSON.parse(atob(String(tok).split(".")[0].replace(/-/g, "+").replace(/_/g, "/")));
      return p && p.m === "pin";
    } catch (e) {
      return false;
    }
  }

  function getDeviceId() {
    try {
      if (window.Bass && typeof window.Bass.deviceId === "function") return window.Bass.deviceId();
      return window.localStorage.getItem("r3_dev") || "";
    } catch (e) {
      return "";
    }
  }

  function withBearer(input, init, tok) {
    var nextInit = Object.assign({}, init || {}, { __r3Retried: true });
    var h = new Headers((init && init.headers) || (typeof input !== "string" && input && input.headers) || {});
    h.set("Authorization", "Bearer " + tok);
    nextInit.headers = h;
    return nextInit;
  }

  function refreshSession() {
    if (refreshPromise) return refreshPromise;
    var rt = "";
    try { rt = window.localStorage.getItem(REFRESH_KEY) || ""; } catch (e) { rt = ""; }
    if (!rt) return Promise.resolve({ ok: false, fatal: true });
    refreshPromise = orig("/api/rent3/pool/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ refresh_token: rt, device_id: getDeviceId() }),
    })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (d) {
          if (r.ok && d && d.token) {
            try {
              window.localStorage.setItem("bass_token", d.token);
              if (d.user) window.localStorage.setItem("rent3_session", JSON.stringify(d.user));
              if (d.refresh_token) window.localStorage.setItem(REFRESH_KEY, d.refresh_token);
            } catch (e) { /* 저장소 쓰기 실패 무시 */ }
            return { ok: true, token: d.token };
          }
          return { ok: false, fatal: r.status === 401 || r.status === 403 };
        });
      })
      .catch(function () {
        return { ok: false, fatal: false };
      })
      .then(function (out) {
        refreshPromise = null;
        return out;
      });
    return refreshPromise;
  }

  function expireAndRedirect() {
    if (redirecting) return;
    var hadLogin =
      window.localStorage.getItem("bass_token") ||
      window.localStorage.getItem("rent3_session") ||
      window.localStorage.getItem(REFRESH_KEY);
    if (!hadLogin) return;
    redirecting = true;
    window.localStorage.removeItem("bass_token");
    window.localStorage.removeItem("rent3_session");
    window.localStorage.removeItem(REFRESH_KEY);
    if (/[?&]qk=/.test(window.location.search)) {
      window.location.reload();   // 스마트폰 바로가기로 연 화면: 포털로 보내지 않고 간편 비밀번호 화면을 다시 띄운다
    } else if (window.location.pathname.indexOf("/rent3/") !== 0) {
      window.location.replace("/rent3/?login=1&expired=1");
    }
  }

  window.fetch = function (input, init) {
    var sentTok = "";
    try { sentTok = window.localStorage.getItem("bass_token") || ""; } catch (e) { sentTok = ""; }
    return orig(input, init).then(function (res) {
      try {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var isRent3Api = url.indexOf("/api/rent3") !== -1;
        var isAuthCall = url.indexOf("/auth/") !== -1 || url.indexOf("/verify") !== -1;
        if (res.status === 403 && isRent3Api && window.R3 && window.R3.openModal) {
          res.clone().json().then(function (d) {
            if (d && /간편 비밀번호로 들어온/.test(d.message || "") && !document.getElementById("r3PinBlock")) {
              window.R3.openModal("🔐 비밀번호 로그인이 필요합니다",
                '<div id="r3PinBlock" style="font-size:14px;line-height:1.7;">' + String(d.message || "").replace(/[<>&]/g, "") +
                '<div style="margin-top:12px;"><a class="bass-btn bass-btn--primary" style="display:inline-block;min-height:44px;line-height:44px;padding:0 16px;" href="/rent3/?logout=1">아이디·비밀번호로 다시 로그인</a></div></div>');
            }
          }).catch(function () {});
        }
        if (res.status === 401 && isRent3Api && !isAuthCall && !redirecting) {
          var alreadyRetried = Boolean(init && init.__r3Retried);
          if (!alreadyRetried && !isViaPinToken(sentTok)) {
            var curTok = window.localStorage.getItem("bass_token") || "";
            if (curTok && curTok !== sentTok) {
              return orig(input, withBearer(input, init, curTok));
            }
            if (window.localStorage.getItem(REFRESH_KEY)) {
              return refreshSession().then(function (rf) {
                if (rf && rf.ok && rf.token) {
                  return orig(input, withBearer(input, init, rf.token));
                }
                if (rf && rf.fatal) expireAndRedirect();
                return res;
              });
            }
          }
          expireAndRedirect();
        }
      } catch (e) { /* 안내가 실패해도 원래 응답은 그대로 돌려준다 */ }
      return res;
    });
  };
})();
