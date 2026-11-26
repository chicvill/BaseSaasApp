/* apps/rent3/frontend/connect.js — 기존 임차인 '내 페이지 연결 요청' 오버레이 모듈 */
(function () {
  "use strict";

  const P = window.R3P;
  const { APP_ID, $ } = P;

  // ── 기존 임차인 '내 페이지 연결 요청' — 임대인이 보낸 링크(/rent3/?connect=지점코드)를 누르면 메뉴 없이 이 창 하나만 뜬다.
  //    입력(호실·성함·휴대폰) → 4자리 확인번호 + 임대인에게 전화 → 승인되면 자동으로 비밀번호 설정 화면. 통화 중에도 상태가 바로 보인다.
  const CC_KEY = "rent3_connect_req";

  function bindConnect() {
    let timer = null;
    let info = null;   // {branch, building_name, lessor_name, lessor_phone}
    const load = () => { try { return JSON.parse(window.localStorage.getItem(CC_KEY) || "null"); } catch (_) { return null; } };
    const store = (v) => { try { if (v) window.localStorage.setItem(CC_KEY, JSON.stringify(v)); else window.localStorage.removeItem(CC_KEY); } catch (_) { /* 저장이 막혀도 이 화면에서는 계속 진행 */ } };
    const e2 = (s) => Bass.escapeHtml(String(s == null ? "" : s));
    const tel = (p) => "tel:" + String(p || "").replace(/[^0-9+]/g, "");
    const BTN = "display:block;width:100%;box-sizing:border-box;min-height:54px;line-height:1.4;padding:12px 10px;font-size:17px;font-weight:700;border:0;border-radius:12px;text-align:center;text-decoration:none;";
    const INP = "width:100%;box-sizing:border-box;height:52px;font-size:18px;padding:8px 12px;border:1px solid #cbd5e1;border-radius:10px;margin:4px 0 12px;color:#111;background:#fff;";
    function host() {
      let el = document.getElementById("r3Connect");
      if (!el) {
        el = document.createElement("div");
        el.id = "r3Connect";
        el.style.cssText = "position:fixed;inset:0;z-index:99999;background:#fff;color:#111;overflow-y:auto;padding:18px 16px 30px;font-family:inherit;";
        document.body.appendChild(el);
      }
      return el;
    }
    const phoneBtn = (big) => (info && info.lessor_phone)
      ? `<a href="${tel(info.lessor_phone)}" style="${BTN}background:#16a34a;color:#fff;margin:${big ? "10px 0 16px" : "4px 0 14px"};">📞 임대인${info.lessor_name ? " " + e2(info.lessor_name) : ""}에게 전화 · ${e2(info.lessor_phone)}</a>` : "";
    function close() { clearInterval(timer); const el = document.getElementById("r3Connect"); if (el) el.remove(); }
    async function poll() {
      const cur = load();
      const st = document.getElementById("ccStatus");
      if (!cur || !st) return;
      try {
        const r = await Bass.api(APP_ID, "/pool/connect/status", { method: "POST", body: { request_id: cur.id, secret: cur.secret } });
        if (r.invite_url) {
          clearInterval(timer); store(null);
          st.style.color = "#15803d"; st.textContent = "✅ 승인되었습니다! 비밀번호를 정하는 화면으로 이동합니다…";
          setTimeout(() => { window.location.href = r.invite_url; }, 1200);
          return;
        }
        if (r.status === "PENDING") { st.textContent = "⏳ 임대인의 확인을 기다리는 중… (전화로 번호를 불러 주세요)"; return; }
        clearInterval(timer); store(null);
        st.style.color = "#b91c1c"; st.textContent = r.message || "요청이 끝났습니다.";
        setTimeout(draw, 3000);
      } catch (_) { clearInterval(timer); store(null); draw(); }
    }
    function draw() {
      const cur = load();
      clearInterval(timer);
      const head = `<div style="max-width:430px;margin:0 auto;"><div style="font-size:36px;text-align:center;">🏠</div>
        <div style="font-size:20px;font-weight:800;text-align:center;margin:4px 0 6px;">${e2(info ? info.building_name : "입주자")} 입주자 연결</div>`;
      const foot = `<div style="text-align:center;margin-top:12px;"><a href="/rent3/?home=1&find=1" style="font-size:13px;color:#2563eb;display:inline-block;padding:10px;">이미 가입했는데 비밀번호를 잊었어요 (문자 인증)</a><br>
        <a href="#" id="ccClose" style="font-size:13px;color:#64748b;display:inline-block;padding:10px;">닫기</a></div></div>`;
      const el = host();
      if (cur) {
        el.innerHTML = head + `<div style="text-align:center;font-size:15px;line-height:1.6;margin-bottom:4px;"><strong>${e2(cur.room_no)}호</strong> 연결을 요청했습니다.<br>임대인에게 전화해 아래 번호를 불러 주세요.<br><strong>통화를 끊지 마세요.</strong></div>
          <div style="font-size:56px;font-weight:800;letter-spacing:12px;text-align:center;color:#1d4ed8;margin:6px 0;">${e2(cur.code)}</div>
          ${phoneBtn(true)}
          <div id="ccStatus" style="text-align:center;font-size:14.5px;line-height:1.5;color:#334155;min-height:44px;">⏳ 임대인의 확인을 기다리는 중…</div>
          <div style="text-align:center;"><a href="#" id="ccCancel" style="font-size:13px;color:#64748b;display:inline-block;padding:10px;">요청 취소</a></div>` + foot;
        el.querySelector("#ccCancel").addEventListener("click", (e) => { e.preventDefault(); store(null); draw(); });
        poll();
        timer = setInterval(poll, 3000);
      } else {
        el.innerHTML = head + `<div style="font-size:14px;line-height:1.6;color:#334155;text-align:center;margin-bottom:12px;">호실·성함·휴대폰 번호를 입력하면 <strong>4자리 확인번호</strong>가 나옵니다.<br>그 번호를 임대인에게 전화로 알려 주세요.</div>
          ${info ? "" : `<label style="font-size:14px;font-weight:700;">건물명</label><input id="ccBuilding" style="${INP}" placeholder="예: 시크빌">`}
          <label style="font-size:14px;font-weight:700;">호실</label><input id="ccRoom" style="${INP}" placeholder="예: 701" inputmode="numeric" autocomplete="off">
          <label style="font-size:14px;font-weight:700;">성함</label><input id="ccName" style="${INP}" autocomplete="name">
          <label style="font-size:14px;font-weight:700;">휴대폰 번호 <span style="font-weight:400;color:#64748b;">(임대인에게 알려 준 번호)</span></label><input id="ccPhone" style="${INP}" placeholder="010-0000-0000" inputmode="tel" autocomplete="tel">
          <div id="ccMsg" role="alert" style="min-height:22px;font-size:14px;color:#b91c1c;margin-bottom:8px;"></div>
          <button type="button" id="btnCcRequest" style="${BTN}background:#2563eb;color:#fff;cursor:pointer;">연결 요청하기</button>
          <div style="margin-top:14px;">${phoneBtn(false)}</div>` + foot;
        el.querySelector("#btnCcRequest").addEventListener("click", async function () {
          const btn = this, msg = el.querySelector("#ccMsg");
          msg.textContent = ""; btn.disabled = true;
          try {
            const v = (id) => (el.querySelector("#" + id) ? el.querySelector("#" + id).value.trim() : "");
            const r = await Bass.api(APP_ID, "/pool/connect/request", { method: "POST", body: {
              branch: info ? info.branch : "", building_name: v("ccBuilding"), room_no: v("ccRoom"), name: v("ccName"), phone: v("ccPhone") } });
            store({ id: r.request_id, secret: r.secret, code: r.code, room_no: r.room_no, branch: info ? info.branch : "" });
            draw();
          } catch (err) {
            msg.textContent = (err.message || "요청하지 못했습니다.") + (err.hint ? " " + err.hint : "");
          } finally { btn.disabled = false; }
        });
      }
      el.querySelector("#ccClose").addEventListener("click", (e) => { e.preventDefault(); close(); });
    }
    async function open(branch) {
      info = null;
      const cur = load();
      const b = (branch && branch !== "1") ? branch : ((cur && cur.branch) || "");
      if (b) { try { info = await Bass.api(APP_ID, `/pool/connect/info?branch=${encodeURIComponent(b)}`); } catch (_) { info = null; } }
      draw();
    }
    const lc = $("lnkConnect");
    if (lc) lc.addEventListener("click", (e) => { e.preventDefault(); open(""); });
    window.__rent3ConnectOpen = open;
    window.__rent3ConnectPending = () => Boolean(load());
  }

  Object.assign(P, { bindConnect });
})();
