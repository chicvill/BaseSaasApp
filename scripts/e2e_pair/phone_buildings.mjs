// 세 건물(시크빌·신비주택·선호아트빌)을 스마트폰 크기(360x800)로 열어 스크린샷을 찍는다 — 서버 적용 전 화면 확인용.
//   py scripts/serve_pool.py --pool-dir data/private/pool_new --port 9080 --tokens-out data/private/tokens.json --tenants tenant_sinbi_103,tenant_sunho_404
//   node scripts/e2e_pair/phone_buildings.mjs data/private/fresh.json data/private/tokens.json
// 결과: e2e_runtime/phone_buildings/*.png (저장소에 올리지 않는다)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_buildings");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9080";
const { launch, snap, asUser, loginUser, sleep } = await import("./phone_ui.mjs");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const tokens = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
const BASE = process.env.E2E_BASE.replace(/\/$/, "");
const A = await launch("bld", 9611);
try {
  for (const ls of cfg.lessors) {
    const res = await loginUser(ls.username, ls.password);
    if (!res.token) { console.log(`❌ ${ls.username} 로그인 실패`); continue; }
    const tag = ls.building.name;
    await asUser(A, res.user, res.token, "/rent3_lessor/"); await sleep(3500);
    await snap(A, `${tag}_임대인_수납현황`, `${ls.username} 로그인 후 첫 화면`);
    // 호실·수납 목록 화면(두 번째 탭)
    await A.eval(`(() => { const b = Array.from(document.querySelectorAll("button, a")).find((x) => /임대매물/.test(x.innerText)); if (b) b.click(); return 1; })()`);
    await sleep(2000);
    await snap(A, `${tag}_임대인_매물`, "임대매물등록 탭");
  }
  for (const [tid, t] of Object.entries(tokens)) {
    await asUser(A, t.user, t.token, "/rent3_tenant/?tenant=" + tid); await sleep(3500);
    await snap(A, `${tid}_임차인_마이홈`, `${t.user.name} 마이홈`);
  }
  console.log("\n콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally {
  await A.close();
}
