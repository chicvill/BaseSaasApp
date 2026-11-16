// 임대인 앱: 첫 화면 → ☰ 메뉴 → 각 화면으로 들어갔다 돌아오기를 스마트폰 크기로 확인한다.
//   py scripts/serve_pool.py --pool-dir data/private/pool_t --port 9086
//   E2E_BASE=http://127.0.0.1:9086 node scripts/e2e_pair/phone_menu.mjs data/private/fresh.json
// 결과: e2e_runtime/phone_menu/*.png (저장소에 올리지 않는다)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_menu");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9086";
const { launch, snap, asUser, loginUser, sleep } = await import("./phone_ui.mjs");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const ls = cfg.lessors.find((x) => x.username === "chic") || cfg.lessors[0];
const A = await launch("mn", 9614);
const TABS = [["rooms", "임대매물등록"], ["broker", "중개계약서"], ["tax", "세무경비"], ["ops", "AS메시지"], ["notice", "일괄통보"], ["bldgtax", "대상호실설정"]];
let bad = 0;
try {
  const res = await loginUser(ls.username, ls.password);
  await asUser(A, res.user, res.token, "/rent3_lessor/"); await sleep(3500);
  await snap(A, "1_첫화면", "수납 현황(첫 화면)");
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  await snap(A, "2_메뉴", "☰ 메뉴 열기");
  await A.eval(`document.querySelector(".r3-overlay") && document.querySelector(".r3-overlay").click()`); await sleep(300);
  for (const [tab, name] of TABS) {
    const ok = await A.eval(`(() => { const b = document.querySelector('.r3-drawer .tab-btn[data-tab="${tab}"]'); if (!b) return false; b.click(); return true; })()`);
    if (!ok) { console.log(`❌ 메뉴에 ${tab} 없음`); bad++; continue; }
    await sleep(2200);
    const back = await A.eval(`!document.getElementById("backBar").hidden && document.getElementById("backTitle").textContent`);
    await snap(A, `3_${name}`, `메뉴 ▸ ${tab} (뒤로가기 줄: ${back})`);
    if (!back) { console.log(`❌ ${tab}: 뒤로가기 줄이 없음`); bad++; }
  }
  await A.eval(`document.getElementById("btnBackLedger").click()`); await sleep(800);
  const home = await A.eval(`!!document.getElementById("roomPick") && document.getElementById("backBar").hidden`);
  console.log(home ? "✅ ← 수납 현황으로 돌아옴" : "❌ 수납 현황으로 돌아오지 못함");
  if (!home) bad++;
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally {
  await A.close();
}
process.exit(bad ? 1 : 0);
