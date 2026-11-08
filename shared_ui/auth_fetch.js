/* 레거시 화면(fetch 를 직접 쓰는 페이지)용: 같은 서버의 /api/ 호출에 로그인 토큰을 자동으로 붙인다. */
(function () {
  "use strict";
  if (window.__authFetchPatched) return;
  window.__authFetchPatched = true;
  var orig = window.fetch.bind(window);
  window.fetch = function (input, init) {
    try {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var sameOriginApi = url.indexOf("/api/") === 0 || url.indexOf(location.origin + "/api/") === 0;
      var tok = window.localStorage.getItem("bass_token");
      if (sameOriginApi && tok) {
        init = init || {};
        var h = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
        if (!h.has("Authorization")) h.set("Authorization", "Bearer " + tok);
        init.headers = h;
      }
    } catch (e) { /* 토큰을 못 붙여도 원래 요청은 그대로 보낸다 */ }
    return orig(input, init);
  };
})();
