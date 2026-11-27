/* apps/rent3/frontend/auth.js — 본인인증 로그인·MFA·동의·회원가입·비밀번호 재설정 모듈 */
(function () {
  "use strict";

  const P = window.R3P;
  const { APP_ID, state, $, esc, saveSession, readAcq } = P;

  function switchAuthTab(tabName) {
    document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.authTab === tabName);
    });
    $("formAccountLogin").style.display = tabName === "login" ? "block" : "none";
    $("formRegister").style.display = tabName === "register" ? "block" : "none";
    const fr = $("formReset"); if (fr) fr.style.display = "none";
    const alertBox = $("authModalAlert");
    if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }

    // 로그인에 입력한 번호가 있으면 회원가입 탭 진입 시 자동 연동
    const ident = ($("loginIdentifier").value || "").trim();
    const digits = ident.replace(/[^0-9]/g, "");
    if (tabName === "register" && !$("regPhone").value && digits.length >= 8) {
      $("regPhone").value = digits.length === 11 ? `${digits.slice(0,3)}-${digits.slice(3,7)}-${digits.slice(7)}` : ident;
    }
  }

  function openAuthModal({ tab = "login", preset = "", targetHint = "" } = {}) {
    const alertBox = $("authModalAlert");
    if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }
    switchAuthTab(tab === "register" ? "register" : "login");
    $("loginTargetHint").value = targetHint || "";
    $("chkAdminPoolHub").checked = targetHint === "pool";

    const sub = $("authModalSub");
    if ((targetHint || "").startsWith("contract")) {
      sub.textContent = "임대인 또는 임차인으로 본인인증 로그인(또는 회원가입)하면 즉시 스마트 계약 호출·응답 화면으로 이동합니다.";
    } else if (targetHint === "add_listing") {
      sub.textContent = "임대인으로 본인인증 로그인(또는 신규 가입)하면 보유 지점의 호실·매물 등록 탭으로 바로 이동합니다.";
    } else if (targetHint === "broker") {
      sub.textContent = "임대인으로 로그인하면 공인중개사 지면계약서 사진 등록 탭으로 바로 이동합니다.";
    } else if (preset === "tenant") {
      sub.textContent = "입주 세입자는 내 호실 마이홈으로, 예비임차인은 공실 탐색 화면으로 자동 이동합니다.";
      const ex = state.demoAccounts.find((d) => d.group === "임차인");
      if (ex) $("loginIdentifier").value = ex.identifier;
    } else if (preset === "lessor") {
      sub.textContent = "임대인 계정으로 로그인하면 본인 소유 건물 지점 장부 페이지로 자동 이동합니다.";
      const ex = state.demoAccounts.find((d) => d.group === "임대인");
      if (ex) $("loginIdentifier").value = ex.identifier;
    } else if (preset === "admin") {
      sub.textContent = "총괄 관리인 계정으로 로그인하면 관리인 전용 앱(/rent3_admin/)으로 즉시 이동합니다.";
      $("loginIdentifier").value = "admin";
      if (state.demoAccounts.length) $("loginPassword").value = "0202"; // 시연(로컬) 환경에서만 미리 채움
    } else {
      sub.textContent = "본인인증 로그인 즉시 계정의 역할에 맞는 전용 페이지로 자동 이동합니다.";
    }

    $("authModal").classList.add("is-open");
  }

  function closeAuthModal() {
    $("authModal").classList.remove("is-open");
  }

  function wantKeepLoggedIn() {
    const chk = $("chkKeepLoggedIn");
    return chk ? Boolean(chk.checked) : true;
  }

  // ── 관리인 2단계 인증: 비밀번호를 통과하면 '코드 필요' 응답이 온다 → 인증 앱 6자리(또는 복구 코드)를 입력해 정식 로그인 응답을 받는다 ──
  async function resolveMfa(res, keepLoggedIn = true) {
    if (!res || !res.mfa_required) return res;
    let last = "";
    for (let i = 0; i < 3; i += 1) {
      const code = window.prompt(`${last ? last + "\n\n" : ""}🔐 2단계 인증\n인증 앱(Google Authenticator 등)의 6자리 숫자를 입력하세요.\n폰을 잃었다면 복구 코드를 입력하세요.`, "");
      if (code === null) throw new Error("2단계 인증을 취소했습니다.");
      try {
        return await Bass.api(APP_ID, "/pool/auth/mfa-verify", {
          method: "POST",
          body: { ticket: res.mfa_ticket, code: code.trim(), device_id: Bass.deviceId(), keep_logged_in: keepLoggedIn },
        });
      } catch (err) {
        if (/시도 횟수|처음부터 다시/.test(err.message || "")) throw err;           // 잠겼거나 대기표가 만료되면 더 묻지 않는다
        last = `❌ ${err.message || "코드가 맞지 않습니다."}`;
      }
    }
    throw new Error("2단계 인증 코드를 3번 틀렸습니다. 잠시 뒤 다시 로그인해 주세요.");
  }

  // ── 로그인 결과가 '동의 필요'이면 동의 화면을 거쳐 토큰을 받는다 ──
  function resolveConsent(res, keepLoggedIn = true) {
    if (!res || !res.needs_consent) return Promise.resolve(res);
    return new Promise((resolve, reject) => {
      const bg = document.createElement("div");
      bg.style.cssText = "position:fixed;inset:0;z-index:100000;background:rgba(15,23,42,.6);display:flex;align-items:center;justify-content:center;padding:14px;overflow-y:auto;";
      bg.innerHTML = `<div style="background:#fff;color:#0f172a;border-radius:16px;max-width:460px;width:100%;padding:18px 16px;line-height:1.6;">
        <div style="font-weight:800;font-size:17px;margin-bottom:6px;">${esc(res.user.name)}님, 동의가 필요합니다</div>
        <div style="font-size:13px;margin-bottom:10px;">서비스를 이용하려면 아래 내용을 확인하고 동의해 주세요.</div>
        <label style="display:flex;gap:8px;align-items:flex-start;font-size:13px;">
          <input type="checkbox" id="csPrivacy" style="margin-top:3px;">
          <span><strong>(필수)</strong> 개인정보 수집·이용에 동의합니다. <a href="privacy.html" target="_blank" rel="noopener">전문 보기</a></span>
        </label>
        <label style="display:flex;gap:8px;align-items:flex-start;font-size:13px;margin-top:8px;">
          <input type="checkbox" id="csSupport" style="margin-top:3px;">
          <span><strong>(선택)</strong> 관리인 원격 지원 접속에 동의합니다. 도움을 요청하신 때에 한해 관리인이 <strong>보기만</strong> 하며 금전·계약 기록은 고칠 수 없고, 접속 기록이 남습니다. 메뉴에서 언제든 끌 수 있습니다.</span>
        </label>
        <div id="csWarn" style="display:none;margin-top:10px;color:#b91c1c;font-size:12.5px;font-weight:700;">⚠️ 개인정보 수집·이용(필수)에 체크해야 계속할 수 있습니다.</div>
        <div style="display:flex;gap:8px;margin-top:14px;">
          <button type="button" id="csOk" class="or-btn or-btn--green" style="flex:1;padding:11px;">동의하고 계속</button>
          <button type="button" id="csNo" class="or-btn" style="padding:11px;">취소</button>
        </div></div>`;
      document.body.appendChild(bg);
      const close = () => bg.remove();
      bg.querySelector("#csNo").addEventListener("click", () => { close(); reject(new Error("동의하지 않아 로그인을 취소했습니다.")); });
      bg.querySelector("#csOk").addEventListener("click", async function () {
        if (!bg.querySelector("#csPrivacy").checked) {
          bg.querySelector("#csWarn").style.display = "block";   // 팝업 안에 바로 보이게 한다(알림창은 팝업 뒤에 가려질 수 있음)
          return;
        }
        bg.querySelector("#csWarn").style.display = "none";
        try {
          const done = await Bass.api(APP_ID, "/pool/auth/consent", {
            method: "POST",
            body: {
              ticket: res.consent_ticket,
              agree_privacy: true,
              agree_support: bg.querySelector("#csSupport").checked,
              device_id: Bass.deviceId(),
              keep_logged_in: keepLoggedIn,
            },
          });
          close();
          resolve(done);
        } catch (e) { close(); reject(e); }
      });
    });
  }

  function renderDemoAccounts() {
    const box = $("demoAccountGrid");
    box.innerHTML = state.demoAccounts.map((acc, idx) => `
      <button type="button" class="or-demo-btn" data-demo-idx="${idx}">
        <div style="font-weight:800; color:var(--or-blue);">${esc(acc.badge)}</div>
        <div style="font-weight:600; margin-top:2px;">${esc(acc.label)}</div>
        <div style="font-size:12px; color:var(--or-muted);">이동: <code>${esc(acc.target_desc)}</code></div>
      </button>
    `).join("");

    box.querySelectorAll("[data-demo-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const acc = state.demoAccounts[Number(btn.dataset.demoIdx)];
        if (!acc) return;
        Bass.withLock(btn, async () => {
          const currentHint = $("loginTargetHint").value || "";
          const effectiveHint = currentHint.startsWith("contract")
            ? currentHint
            : (acc.target_hint || currentHint);
          const keep = wantKeepLoggedIn();
          const res = await Bass.api(APP_ID, "/pool/auth/login", {
            method: "POST",
            body: {
              login_mode: "account",
              identifier: acc.identifier,
              password: acc.password,
              target_hint: effectiveHint,
              device_id: Bass.deviceId(),
              keep_logged_in: keep,
            },
          });
          const done = await resolveConsent(await resolveMfa(res, keep), keep);
          saveSession(done.user, done.token, done.refresh_token || "");
          Bass.updateAppAlert("success", done.message, `이동 경로: ${done.redirect_url}`);
          window.location.href = done.redirect_url;
        }, "본인인증 확인 및 이동 중... ⏳");
      });
    });
  }

  // ── 비밀번호 재설정 (본인인증 기반) ──
  function bindPasswordReset() {
    const link = $("lnkForgot");
    if (!link) return;
    link.addEventListener("click", (e) => {
      e.preventDefault();
      $("formAccountLogin").style.display = "none";
      $("formRegister").style.display = "none";
      $("formReset").style.display = "block";
      if (!$("rsIdent").value) $("rsIdent").value = ($("loginIdentifier").value || "").trim();
    });
    $("lnkBackLogin").addEventListener("click", (e) => { e.preventDefault(); switchAuthTab("login"); });
    const body = () => ({
      identifier: $("rsIdent").value.trim(), name: $("rsName").value.trim(),
      phone: $("rsPhone").value.trim(), birth: $("rsBirth").value.trim(), proof: ($("rsProof") ? $("rsProof").value.trim() : ""),
    });
    // 메인 화면의 '비밀번호를 잊으셨나요? 내 페이지 찾기' 링크: 로그인 창을 열고 곧바로 재설정 화면을 보인다
    document.querySelectorAll(".js-forgot").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      openAuthModal({ tab: "login" });
      link.click();
    }));
    $("btnRsStart").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const r = await Bass.api(APP_ID, "/pool/auth/password-reset/start", { method: "POST", body: body() });
        Bass.updateAppAlert("success", r.message, r.hint || "");
      });
    });
    $("btnRsConfirm").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const newPw = $("rsNew").value;
        const r = await Bass.api(APP_ID, "/pool/auth/password-reset/confirm", {
          method: "POST", body: { ...body(), code: $("rsCode").value.trim(), new_password: newPw } });
        Bass.updateAppAlert("success", r.message);
        const ident = $("rsIdent").value.trim();
        $("rsNew").value = ""; $("rsCode").value = ""; if ($("rsProof")) $("rsProof").value = "";
        $("loginIdentifier").value = ident;
        $("loginPassword").value = "";
        // 본인 확인을 방금 마쳤으므로 새 비밀번호로 곧바로 내 페이지에 들어간다(실패하면 로그인 화면으로)
        try {
          const res = await Bass.api(APP_ID, "/pool/auth/login", { method: "POST", body: { login_mode: "account", identifier: ident, password: newPw, target_hint: "", device_id: Bass.deviceId(), keep_logged_in: true } });
          const done = await resolveConsent(await resolveMfa(res, true), true);
          saveSession(done.user, done.token, done.refresh_token || "");
          closeAuthModal();
          window.location.href = done.redirect_url;
          return;
        } catch (_) { /* 자동 입장이 안 되면 아래처럼 로그인 화면에서 직접 들어간다 */ }
        switchAuthTab("login");
      });
    });
  }

  function bindAuthEvents() {
    $("authModalTabs").addEventListener("click", (e) => {
      const tabBtn = e.target.closest("[data-auth-tab]");
      if (!tabBtn) return;
      switchAuthTab(tabBtn.dataset.authTab);
    });

    $("regRole").addEventListener("change", () => {
      $("regLessorFields").style.display = $("regRole").value === "lessor" ? "block" : "none";
    });

    // 통합 계정 본인인증 로그인 제출 -> 역할 판별 후 자동 리다이렉트
    $("formAccountLogin").addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = $("btnSubmitLogin");
      const alertBox = $("authModalAlert");
      if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }

      const rawIdent = ($("loginIdentifier").value || "").trim();
      const rawDigits = rawIdent.replace(/[^0-9]/g, "");
      const formattedPhone = rawDigits.length === 11
        ? `${rawDigits.slice(0,3)}-${rawDigits.slice(3,7)}-${rawDigits.slice(7)}`
        : rawIdent;

      Bass.withLock(btn, async () => {
        let hint = $("loginTargetHint").value || "";
        if ($("chkAdminPoolHub").checked && !hint) hint = "pool";

        try {
          const keep = wantKeepLoggedIn();
          const res = await Bass.api(APP_ID, "/pool/auth/login", {
            method: "POST",
            body: {
              login_mode: "account",
              identifier: rawIdent,
              password: $("loginPassword").value,
              device_id: Bass.deviceId(),
              target_hint: hint,
              keep_logged_in: keep,
            },
          });
          const done = await resolveConsent(await resolveMfa(res, keep), keep);
          saveSession(done.user, done.token, done.refresh_token || "");
          closeAuthModal();
          Bass.updateAppAlert("success", done.message, `자동 이동: ${done.redirect_url}`);
          window.location.href = done.redirect_url;
        } catch (err) {
          if (alertBox) {
            const isNotFound = (err.message || "").includes("찾을 수 없습니다");
            let quickBtnHtml = "";
            if (isNotFound && rawDigits.length >= 8) {          // 번호를 넣었을 때만 번호 간편가입을 권한다(이름만 넣은 경우는 제외)
              quickBtnHtml = `
                <div style="margin-top:10px; padding-top:8px; border-top:1px dashed #fca5a5;">
                  <div style="font-size:12px; color:#991b1b; margin-bottom:6px; font-weight:600;">
                    💡 아직 등록되지 않은 번호입니다. 간편 본인인증으로 즉시 가입하시겠습니까?
                  </div>
                  <button type="button" id="btnSwitchToRegisterWithPhone" class="or-btn or-btn--green or-btn--sm" style="width:100%; font-size:13px; font-weight:700;">
                    ✨ '${formattedPhone}' 번호로 3초 간편가입 진행 ➔
                  </button>
                </div>`;
            }

            alertBox.style.display = "block";
            alertBox.innerHTML = `
              <div style="background:#fef2f2; border:1.5px solid #f87171; border-radius:10px; padding:12px 14px; color:#7f1d1d;">
                <div style="font-weight:800; font-size:14px; display:flex; align-items:center; gap:6px;">
                  <span>❌</span> <span>${Bass.escapeHtml(err.message || "로그인에 실패했습니다.")}</span>
                </div>
                ${err.hint ? `<div style="font-size:12.5px; color:#991b1b; margin-top:4px; font-weight:600; line-height:1.4;">${Bass.escapeHtml(err.hint)}</div>` : ""}
                ${quickBtnHtml}
              </div>`;

            const quickBtn = $("btnSwitchToRegisterWithPhone");
            if (quickBtn) {
              quickBtn.addEventListener("click", () => {
                alertBox.style.display = "none";
                switchAuthTab("register");
                $("regPhone").value = formattedPhone;
                $("regName").focus();
              });
            }
          }
          throw err;
        }
      }, "본인인증 확인 및 이동 중... ⏳");
    });

    // 간편 본인인증 회원가입 -> Data Pool 등록 후 역할/공동계약 페이지로 자동 이동
    $("formRegister").addEventListener("submit", (e) => {
      e.preventDefault();
      const alertBox = $("authModalAlert");
      if (alertBox) { alertBox.style.display = "none"; alertBox.innerHTML = ""; }

      if (!$("regAgreePrivacy").checked) {
        Bass.updateAppAlert("warning", "개인정보 수집·이용(필수)에 동의해 주세요.", "동의하지 않으면 가입할 수 없습니다.");
        return;
      }
      Bass.withLock($("btnSubmitRegister"), async () => {
        try {
          const res = await Bass.api(APP_ID, "/pool/auth/register", {
            method: "POST",
            body: {
              role: $("regRole").value,
              name: $("regName").value,
              phone: $("regPhone").value,
              username: $("regUsername").value,
              building_name: $("regBuildingName").value,
              address: $("regAddress").value,
              password: $("regPassword").value,
              target_hint: $("loginTargetHint").value || "",
              agree_privacy: $("regAgreePrivacy").checked,
              agree_support: $("regAgreeSupport").checked,
              acq: $("regRole").value === "lessor" ? (readAcq() || {}) : {},
              device_id: Bass.deviceId(),
              keep_logged_in: true,
            },
          });
          saveSession(res.user, res.token, res.refresh_token || "");
          closeAuthModal();
          Bass.updateAppAlert("success", res.message);
          window.location.href = res.redirect_url;
        } catch (err) {
          if (alertBox) {
            alertBox.style.display = "block";
            alertBox.innerHTML = `
              <div style="background:#fef2f2; border:1.5px solid #f87171; border-radius:10px; padding:12px 14px; color:#7f1d1d;">
                <div style="font-weight:800; font-size:14px; display:flex; align-items:center; gap:6px;">
                  <span>❌</span> <span>${Bass.escapeHtml(err.message || "회원가입에 실패했습니다.")}</span>
                </div>
                ${err.hint ? `<div style="font-size:12.5px; color:#991b1b; margin-top:4px; font-weight:600;">${Bass.escapeHtml(err.hint)}</div>` : ""}
              </div>`;
          }
          throw err;
        }
      }, "본인인증 가입 및 이동 중... ⏳");
    });
  }

  Object.assign(P, {
    switchAuthTab,
    openAuthModal,
    closeAuthModal,
    resolveMfa,
    resolveConsent,
    renderDemoAccounts,
    bindPasswordReset,
    bindAuthEvents,
  });
})();
