/* Rent3 앱용: 로그인이 만료됐거나 무효가 되면(401) 저장된 로그인 정보를 지우고 포털 로그인으로 보낸다.
   (그렇지 않으면 화면이 401 을 받으면서 계속 같은 요청을 반복한다.) 로그인·가입·비밀번호 재설정 요청의 401 은 건드리지 않는다. */
(function () {
  "use strict";
  if (window.__rent3GuardPatched) return;
  window.BASS_ERROR_TIMEOUT = 2000; // Rent3: 오류·경고 알림은 2초 뒤 자동으로 닫힌다(마우스를 올리거나 터치하는 동안은 유지)
  window.__rent3GuardPatched = true;
  var orig = window.fetch.bind(window);
  var redirecting = false;
  window.fetch = function (input, init) {
    return orig(input, init).then(function (res) {
      try {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var isRent3Api = url.indexOf("/api/rent3") !== -1;
        var isAuthCall = url.indexOf("/auth/") !== -1 || url.indexOf("/verify") !== -1;
        if (res.status === 401 && isRent3Api && !isAuthCall && !redirecting) {
          var hadLogin = window.localStorage.getItem("bass_token") || window.localStorage.getItem("rent3_session");
          if (hadLogin) {
            redirecting = true;
            window.localStorage.removeItem("bass_token");
            window.localStorage.removeItem("rent3_session");
            if (window.location.pathname.indexOf("/rent3/") !== 0) {
              window.location.replace("/rent3/?login=1&expired=1");
            }
          }
        }
      } catch (e) { /* 안내가 실패해도 원래 응답은 그대로 돌려준다 */ }
      return res;
    });
  };
})();
