/* rent5/static/auth.js — 로그인 · 회원가입 화면. 끝나면 ?next= 로 (예: 신청하던 매물), 없으면 내 화면으로. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const nxt = R5.nextPage();
  const suffix = nxt ? `?next=${encodeURIComponent(nxt)}` : "";
  const after = () => R5.go(nxt || "my.html");

  if ($("signupLink")) $("signupLink").href = `signup.html${suffix}`;
  if ($("loginLink")) $("loginLink").href = `login.html${suffix}`;

  R5.me().then((user) => { if (user) after(); });                  // 이미 로그인했으면 바로 넘어간다

  const form = $("loginForm") || $("signupForm");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const submit = form.querySelector("[type=submit]");
    R5.run(submit, async () => {
      let body, path;
      if (form.id === "loginForm") {
        path = "/login";
        body = { username: $("username").value.trim(), password: $("password").value };
      } else {
        if ($("password").value !== $("password2").value) {
          return R5.notify("warn", "비밀번호 두 칸이 서로 다릅니다.", "같은 비밀번호를 두 번 입력해 주세요.");
        }
        path = "/signup";
        body = { username: $("username").value.trim(), name: $("name").value.trim(), phone: $("phone").value.trim(), password: $("password").value };
      }
      const res = await R5.api(path, { method: "POST", body });
      R5.token.set(res.token);
      after();
    }, form.id === "loginForm" ? "로그인 중..." : "가입 중...");
  });
})();
