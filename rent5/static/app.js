/* rent5/static/app.js — 모든 화면이 쓰는 공통: API 호출 · 로그인 세션 · 안내 · 이동할 곳(next) 검사 */
(function (g) {
  "use strict";

  const KEY = "rent5.token";
  // 단독 실행은 /api, 게이트웨이(/rent5/ 아래)에서는 /api/rent5
  const API = location.pathname.startsWith("/rent5/") ? "/api/rent5" : "/api";
  const store = {
    get() { try { return localStorage.getItem(KEY) || ""; } catch (_) { return ""; } },
    set(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (_) { /* 저장 못 해도 이번 화면은 동작 */ } },
  };

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
  const won = (n) => `${Number(n || 0).toLocaleString("ko-KR")}원`;
  const man = (n) => `${(Number(n || 0) / 10000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만`;
  const when = (iso) => (iso ? String(iso).replace("T", " ").slice(0, 16) : "");
  const tel = (p) => (p ? `<a href="tel:${esc(String(p).replace(/[^\d+]/g, ""))}">${esc(p)}</a>` : "");

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (store.get()) headers.Authorization = `Bearer ${store.get()}`;
    let res;
    try {
      res = await fetch(`${API}${path}`, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    } catch (_) {
      throw Object.assign(new Error("서버에 연결할 수 없습니다."), { status: 0, hint: "네트워크를 확인하고 다시 시도해 주세요." });
    }
    let data = {};
    try { data = await res.json(); } catch (_) { /* 본문 없음 */ }
    if (!res.ok) {
      const message = data.message || (Array.isArray(data.detail) ? "입력한 내용을 확인해 주세요." : "요청을 처리하지 못했습니다.");
      throw Object.assign(new Error(message), { status: res.status, hint: data.hint || "" });
    }
    return data;
  }

  /** 화면 위쪽 안내 (kind: ok | warn | bad | info). 내용은 textContent 로만 넣는다. */
  function notify(kind, message, hint) {
    const box = document.getElementById("alert");
    if (!box) return;
    box.replaceChildren();
    if (!message) return;
    const div = document.createElement("div");
    div.className = `alert ${kind === "info" ? "" : kind}`.trim();
    div.setAttribute("role", "alert");
    div.textContent = message;
    if (hint) {
      const h = document.createElement("span");
      h.className = "hint";
      h.textContent = hint;
      div.appendChild(h);
    }
    box.appendChild(div);
    g.scrollTo({ top: 0, behavior: "smooth" });
  }
  const fail = (err) => notify("bad", err.message, err.hint);

  /** 버튼을 잠가 두 번 눌림을 막고, 실패하면 안내를 띄운다. */
  async function run(btn, work, busy) {
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; if (busy) btn.textContent = busy; }
    try { return await work(); } catch (err) { fail(err); } finally { if (btn) { btn.disabled = false; btn.textContent = label; } }
  }

  /** 로그인 뒤 돌아갈 곳: 같은 폴더의 .html 상대 경로만. 바깥 주소는 버린다. */
  function nextPage(raw) {
    const v = raw ?? new URLSearchParams(location.search).get("next") ?? "";
    return /^[\w-]+\.html(\?[^\s#]*)?$/.test(v) && !v.includes("..") ? v : "";
  }
  const go = (page) => location.replace(page);
  const here = () => location.pathname.split("/").pop() + location.search;

  async function me() {
    if (!store.get()) return null;
    try { return (await api("/me")).user; } catch (err) { if (err.status === 401) store.set(""); return null; }
  }

  /** 로그인이 필요한 화면: 안 했으면 로그인으로 보내고, 로그인하면 이 화면으로 돌아온다. */
  async function requireLogin() {
    const user = await me();
    if (!user) { go(`login.html?next=${encodeURIComponent(here())}`); return new Promise(() => {}); }
    return user;
  }

  function logout() { store.set(""); go("index.html"); }

  async function nav(userArg) {
    const box = document.getElementById("nav");
    if (!box) return userArg || null;
    const user = userArg === undefined ? await me() : userArg;
    box.innerHTML = user
      ? `<span class="who">${esc(user.name)}님</span><a class="btn sm primary" href="my.html">내 화면</a><button class="btn sm" id="btnLogout" type="button">로그아웃</button>`
      : `<a class="btn sm primary" href="login.html">로그인</a><a class="btn sm" href="signup.html">회원가입</a>`;
    const out = document.getElementById("btnLogout");
    if (out) out.addEventListener("click", logout);
    return user;
  }

  g.R5 = { api, esc, won, man, when, tel, notify, fail, run, nextPage, go, here, me, requireLogin, nav, logout, token: store };
})(window);
