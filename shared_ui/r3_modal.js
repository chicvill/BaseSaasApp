/* r3_modal.js — Rent3 공용 창(모달)·복사 도구.
 *
 * 브라우저에서 /shared/ui/r3_modal.js 로 불러온다(디스크 경로: shared_ui/r3_modal.js).
 *   R3.openModal(제목HTML, 본문HTML) → 본문 요소를 돌려준다. 바깥을 누르거나 ✕ 를 누르면 닫힌다.
 *   R3.closeModal()
 *   R3.copyText(글) → 성공 여부
 *   R3.copyAndSms(글, 전화번호) → 복사한 뒤 휴대폰이면 문자창을 연다(붙여넣기만 하면 됨)
 * 스타일은 이 파일이 직접 넣는다(index.html 이 캐시돼도 화면이 깨지지 않게).
 */
(function (global) {
  "use strict";

  var CSS = [
    ".r3-lbl{display:block;font-size:13px;font-weight:700;margin:2px 0 4px}",
    ".r3-fld{display:grid;gap:3px;font-size:12.5px}",
    ".r3-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}",
    ".r3-msg{width:100%;font-size:13px;line-height:1.5}",
    ".r3-modal{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.55);display:flex;align-items:flex-end;justify-content:center}",
    ".r3-modal__box{width:100%;max-width:560px;max-height:92vh;overflow-y:auto;background:#17172e;color:#eceaf7;",
    "border:1px solid var(--border,#334);border-radius:16px 16px 0 0;padding:14px 14px 22px}",
    "@media (min-width:640px){.r3-modal{align-items:center}.r3-modal__box{border-radius:16px}}",
    ".r3-modal__head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px;font-size:15px}",
    ".r3-modal__x{min-width:40px;min-height:40px;font-size:18px}",
    // 대화 말풍선(계약방 대화·메시지 화면 공용)
    ".cm-list{max-height:46vh;overflow-y:auto;padding:2px 2px 6px}",
    ".cm-b{margin:6px 0;display:flex}.cm-b.is-mine{justify-content:flex-end}",
    ".cm-b>div{max-width:88%;padding:8px 10px;border-radius:12px;font-size:13.5px;line-height:1.5;background:rgba(148,163,184,.22)}",
    ".cm-b.is-mine>div{background:rgba(99,102,241,.35)}",
    ".cm-meta{font-size:11px;opacity:.7;margin-bottom:2px}",
    ".cm-sys{margin:8px 0;text-align:center;font-size:12px;opacity:.8}"
  ].join("");

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function closeModal() {
    var m = document.getElementById("r3Modal");
    if (m) m.remove();
  }

  function openModal(title, html) {
    ensureStyle();
    closeModal();
    var bg = document.createElement("div");
    bg.className = "r3-modal";
    bg.id = "r3Modal";
    bg.innerHTML = '<div class="r3-modal__box" role="dialog" aria-modal="true">' +
      '<div class="r3-modal__head"><strong>' + title + '</strong>' +
      '<button type="button" class="bass-btn r3-modal__x" aria-label="닫기">✕</button></div>' +
      '<div class="r3-modal__body">' + html + '</div></div>';
    bg.addEventListener("mousedown", function (e) { if (e.target === bg) closeModal(); });
    bg.querySelector(".r3-modal__x").addEventListener("click", closeModal);
    document.body.appendChild(bg);
    return bg.querySelector(".r3-modal__body");
  }

  function copyText(text) {
    var fallback = function () {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;opacity:0;";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      ta.remove();
      return ok;
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallback(); });
    }
    return Promise.resolve(fallback());
  }

  function copyAndSms(text, phone) {
    return copyText(text).then(function (ok) {
      if (global.Bass && global.Bass.updateAppAlert) {
        global.Bass.updateAppAlert(ok ? "success" : "warning",
          ok ? "복사했습니다 — 임차인 문자창에 붙여넣어 보내세요." : "복사에 실패했습니다. 글을 직접 길게 눌러 복사해 주세요.");
      }
      var d = String(phone || "").replace(/\D/g, "");
      if (ok && d && /Android|iPhone|iPad/i.test(navigator.userAgent)) {
        var a = document.createElement("a");
        a.href = "sms:" + d;
        a.click();
      }
      return ok;
    });
  }

  ensureStyle();   // 창을 열지 않는 화면(메시지 말풍선 등)도 같은 스타일을 쓴다
  global.R3 = Object.assign(global.R3 || {}, {
    openModal: openModal, closeModal: closeModal, copyText: copyText, copyAndSms: copyAndSms
  });
})(window);
