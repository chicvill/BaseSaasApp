/* apps/rent_dist/rent3/frontend/sw.js — Rent3 Network-First PWA Service Worker.
 *
 * 설계 원칙:
 * 1) /api/* 요청과 GET 이외의 모든 요청은 절대 가로채거나 캐시하지 않는다.
 * 2) HTML·JS·CSS는 항상 서버 최신본(Network-First)을 우선 사용해 코드 배포 즉시 반영된다.
 * 3) 완전 오프라인 상태에서 페이지를 열 때만 간단한 재시도 안내 화면을 표시한다.
 */
const CACHE_NAME = "rent3-pwa-icons-v1";
const ICON_ASSETS = [
  "/rent3_tenant/icons/icon-192.png?v=1",
  "/rent3_tenant/icons/icon-512.png?v=1",
  "/rent3_tenant/icons/icon-maskable-512.png?v=1",
  "/rent3_tenant/icons/apple-touch-icon.png",
];

const OFFLINE_HTML = `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Rent3 — 오프라인 안내</title>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #0f172a;
      display: flex; min-height: 100vh; align-items: center; justify-content: center; padding: 20px; box-sizing: border-box; }
    .box { max-width: 420px; width: 100%; background: #fff; border: 1px solid #e2e8f0; border-radius: 18px;
      padding: 24px 20px; text-align: center; box-shadow: 0 12px 30px rgba(15,23,42,.08); }
    h1 { margin: 0 0 8px; font-size: 18px; }
    p { margin: 0 0 16px; font-size: 14px; line-height: 1.6; color: #475569; }
    button { width: 100%; padding: 12px 16px; border: 0; border-radius: 12px; background: #2563eb;
      color: #fff; font-size: 15px; font-weight: 800; cursor: pointer; }
  </style>
</head>
<body>
  <div class="box">
    <h1>📡 인터넷 연결을 확인해 주세요</h1>
    <p>Rent3는 실시간 계약·수납 데이터를 안전하게 동기화하기 위해 인터넷 연결이 필요합니다.<br>네트워크가 복구되면 아래 버튼을 눌러 다시 열어 주세요.</p>
    <button type="button" onclick="location.reload()">🔄 다시 시도</button>
  </div>
</body>
</html>`;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(ICON_ASSETS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (!req || req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch (_) {
    return;
  }

  // 타 도메인 및 API 요청은 서비스워커가 일절 개입하지 않는다.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  // 아이콘 리소스만 네트워크 우선 + 캐시 폴백
  if (url.pathname.startsWith("/rent3_tenant/icons/")) {
    event.respondWith(
      fetch(req).then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
        }
        return resp;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // 페이지 이동(navigate)은 항상 네트워크 우선(Network-First), 완전 오프라인일 때만 안내 페이지 반환
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => new Response(OFFLINE_HTML, {
        status: 503,
        headers: { "Content-Type": "text/html; charset=utf-8" },
      }))
    );
  }
});
