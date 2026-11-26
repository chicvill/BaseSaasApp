/* apps/rent_dist/rent3/frontend/pwa.js — Rent3 PWA 등록 · 홈 화면 설치 프롬프트 · 포털 ☰ PWA/TWA 안내 모달. */
(function (global) {
  "use strict";

  let deferredPrompt = null;

  function isStandalone() {
    try {
      return Boolean(
        (global.matchMedia && global.matchMedia("(display-mode: standalone)").matches) ||
        global.navigator.standalone === true
      );
    } catch (_) {
      return false;
    }
  }

  function isIOS() {
    return /iPhone|iPad|iPod/i.test((global.navigator && global.navigator.userAgent) || "");
  }

  function canInstall() {
    return Boolean(deferredPrompt) && !isStandalone();
  }

  async function promptInstall() {
    if (!deferredPrompt) return { outcome: "unavailable" };
    const ev = deferredPrompt;
    deferredPrompt = null;
    try {
      await ev.prompt();
      const choice = await ev.userChoice;
      return choice || { outcome: "dismissed" };
    } catch (_) {
      return { outcome: "dismissed" };
    }
  }

  global.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
  });

  // HTTPS 또는 localhost 환경에서만 Network-First Service Worker 등록
  (function registerServiceWorker() {
    if (!("serviceWorker" in global.navigator)) return;
    const loc = global.location;
    const okHost = loc.protocol === "https:" || loc.hostname === "localhost" || loc.hostname === "127.0.0.1";
    if (!okHost) return;
    global.addEventListener("load", function () {
      global.navigator.serviceWorker.register("/rent3/sw.js", { scope: "/rent3/" }).catch(function () {});
    });
  })();

  function openGuideModal() {
    if (!global.R3 || typeof global.R3.openModal !== "function") return;
    const standalone = isStandalone();
    const ios = isIOS();
    const origin = global.location ? global.location.origin : "https://<도메인>";
    const statusBanner = standalone
      ? `<div style="padding:10px 12px;border-radius:12px;background:#ecfdf5;border:1px solid #86efac;color:#065f46;font-size:13.5px;font-weight:700;margin-bottom:12px;">
           ✅ 현재 Rent3 앱 모드(PWA/TWA 전체화면)로 실행 중입니다.
         </div>`
      : (canInstall()
        ? `<div style="padding:12px;border-radius:12px;background:#eff6ff;border:1.5px solid #93c5fd;margin-bottom:12px;">
             <div style="font-size:13.5px;font-weight:800;color:#1e3a8a;margin-bottom:8px;">📲 이 기기에서 바로 앱으로 설치할 수 있습니다</div>
             <button type="button" id="btnPwaInstallNow" class="bass-btn bass-btn--primary" style="width:100%;min-height:44px;">📲 지금 바로 홈 화면에 Rent3 앱 설치하기</button>
           </div>`
        : `<div style="padding:10px 12px;border-radius:12px;background:#f8fafc;border:1px solid #cbd5e1;color:#334155;font-size:13px;line-height:1.6;margin-bottom:12px;">
             ${ios
               ? "🍎 <strong>아이폰(Safari)</strong>: 화면 아래 <strong>공유(□↑) → 홈 화면에 추가 → 추가</strong>를 누르면 앱처럼 바로 설치됩니다."
               : "🤖 <strong>안드로이드(Chrome · 삼성인터넷)</strong>: 브라우저 우측 상단 메뉴(<strong>⋮</strong>) → <strong>앱 설치</strong> 또는 <strong>홈 화면에 추가</strong>를 눌러 설치하세요."}
           </div>`);

    const html = `
      <div style="font-size:13.5px;line-height:1.68;color:#1e293b;">
        ${statusBanner}

        <div style="font-weight:800;font-size:14.5px;color:#0f172a;margin:0 0 6px;">1. PWA · TWA란 무엇인가요?</div>
        <div style="padding:10px 12px;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;margin-bottom:12px;">
          <div style="margin-bottom:6px;"><strong>• PWA (Progressive Web App)</strong>: 별도 앱스토어 다운로드 없이 웹사이트를 스마트폰 홈 화면에 설치해, 주소창 없는 전체화면 앱처럼 실행하는 웹 표준 기술입니다.</div>
          <div><strong>• TWA (Trusted Web Activity)</strong>: 완성된 PWA를 Google 공식 안드로이드 패키지(<code>.aab</code>)로 감싸 <strong>Google Play 스토어에 정식 안드로이드 앱으로 등록</strong>하는 기술입니다.</div>
        </div>

        <div style="font-weight:800;font-size:14.5px;color:#0f172a;margin:0 0 6px;">2. 기기별 설치 방법 (10초 완료)</div>
        <ul style="margin:0 0 12px;padding-left:18px;font-size:13px;line-height:1.7;">
          <li><strong>안드로이드 (Chrome / 삼성인터넷)</strong>: 브라우저 메뉴(<strong>⋮</strong>) → <strong>앱 설치</strong> 또는 <strong>홈 화면에 추가</strong></li>
          <li><strong>아이폰 · 아이패드 (Safari)</strong>: 하단 <strong>공유(□↑)</strong> 버튼 → <strong>홈 화면에 추가</strong> → 우측 상단 <strong>추가</strong></li>
          <li><strong>PC (Chrome / Edge)</strong>: 주소창 우측 끝의 <strong>앱 설치(⊕)</strong> 아이콘 클릭</li>
        </ul>

        <div style="font-weight:800;font-size:14.5px;color:#0f172a;margin:0 0 6px;">3. 기존 웹 사용과 무엇이 같고 달라지나요?</div>
        <ul style="margin:0 0 12px;padding-left:18px;font-size:13px;line-height:1.7;">
          <li><strong>코드 수정 시 자동 최신화</strong>: <code>Network-First</code> 방식(API 캐시 제외)이 적용되어, 서버에 새 기능을 배포하면 <strong>스토어 재심사·앱 업데이트 없이</strong> 실행 즉시 최신 화면이 반영됩니다.</li>
          <li><strong>기존 웹과 100% 동일 데이터</strong>: 일반 브라우저 접속과 앱 설치 사용 모두 동일한 계정·계약·수납 데이터를 실시간 공유합니다.</li>
          <li><strong>주소창 없는 전체화면</strong>: 앱 모드에서는 상단 주소창이 사라져 화면이 넓어지며, 법원등기소(<code>iros.go.kr</code>)·전화 걸기·PDF 보기는 새 창·외부 앱으로 안전하게 열립니다.</li>
          <li><strong>스토어 재등록이 필요한 예외</strong>: 앱 이름·바탕화면 아이콘·도메인·시작 주소를 바꿀 때만 안드로이드 패키지(<code>.aab</code>)를 다시 올려 주면 됩니다.</li>
        </ul>

        <details style="padding:10px 12px;border-radius:12px;background:#f8fafc;border:1px solid #cbd5e1;font-size:12.5px;line-height:1.65;">
          <summary style="font-weight:800;color:#0f172a;cursor:pointer;font-size:13px;">🛠️ 운영자 · Google Play (TWA) 패키징 정보 보기</summary>
          <div style="margin-top:8px;">
            <div>• <strong>Web App Manifest</strong>: <a href="/rent3/manifest.json" target="_blank" rel="noopener">/rent3/manifest.json</a></div>
            <div>• <strong>Service Worker</strong>: <code>/rent3/sw.js</code> (Network-First · <code>/api/*</code> 캐시 제외)</div>
            <div>• <strong>Digital Asset Links</strong>: <a href="/api/rent3/pool/pwa/assetlinks.json" target="_blank" rel="noopener">/api/rent3/pool/pwa/assetlinks.json</a><br>
              <span style="color:#475569;">(환경변수 <code>RENT3_TWA_PACKAGE_NAME</code>, <code>RENT3_TWA_SHA256_CERT</code> 설정 후 리버스 프록시에서 <code>/.well-known/assetlinks.json</code> 연결)</span></div>
            <div style="margin-top:6px;">• <strong>Bubblewrap CLI 빌드 명령</strong>:</div>
            <pre style="margin:4px 0 0;padding:8px 10px;border-radius:8px;background:#0f172a;color:#e2e8f0;font-size:12px;overflow-x:auto;">npx @bubblewrap/cli init --manifest ${origin}/rent3/manifest.json</pre>
          </div>
        </details>
      </div>`;

    const body = global.R3.openModal("📲 앱 설치 · PWA / TWA 안내", html);
    const btnNow = body && body.querySelector("#btnPwaInstallNow");
    if (btnNow) {
      btnNow.addEventListener("click", function () {
        promptInstall().then(function (res) {
          if (res && res.outcome === "accepted") {
            if (global.Bass && global.Bass.updateAppAlert) {
              global.Bass.updateAppAlert("success", "Rent3 앱 설치가 완료되었습니다! 홈 화면 아이콘으로 바로 실행할 수 있습니다.");
            }
            if (global.R3 && global.R3.closeModal) global.R3.closeModal();
          }
        });
      });
    }
  }

  function bindTriggers() {
    const btn = document.getElementById("btnPwaGuide");
    if (btn && !btn.dataset.pwaBound) {
      btn.dataset.pwaBound = "1";
      btn.addEventListener("click", openGuideModal);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindTriggers);
  } else {
    bindTriggers();
  }

  global.R3PWA = {
    isStandalone: isStandalone,
    canInstall: canInstall,
    promptInstall: promptInstall,
    openGuideModal: openGuideModal,
  };
})(window);
