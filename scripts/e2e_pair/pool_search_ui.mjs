// 관리인 Data Pool 허브의 검색·수정 구역이 실제 브라우저에서 컬럼 체크 목록과 검색 결과를 보이는지 확인한다.
//   py scripts/serve_pool.py --pool-dir <풀 복사본> --port 9163 --admin-out e2e_runtime/admin_tok.json
//   E2E_BASE=http://127.0.0.1:9163 node scripts/e2e_pair/pool_search_ui.mjs e2e_runtime/admin_tok.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "pool_search_ui");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9163";
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const L = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const P = await launch("psearch", 9624);
try {
  await asUser(P, L.user, L.token, "/rent3/pool.html"); await sleep(3500);
  console.log("컬렉션 목록:", await P.eval(`[...document.querySelectorAll("#psCol option")].map(o=>o.value).join(",")`));
  console.log("건수 표시:", await P.eval(`document.getElementById("psCount").textContent`));
  const n0 = await P.eval(`document.querySelectorAll(".ps-c").length`);
  console.log("첫 컬렉션의 체크 목록 수:", n0);
  await P.eval(`(() => { const s = document.getElementById("psCol"); s.value = "payments"; s.dispatchEvent(new Event("change")); return 1; })()`); await sleep(2000);
  const n = await P.eval(`document.querySelectorAll(".ps-c").length`);
  check(n > 5, `payments 컬럼 체크 목록 (${n}개)`);
  console.log("체크됨:", await P.eval(`[...document.querySelectorAll(".ps-c:checked")].map(e=>e.value).join(", ")`));
  await P.eval(`(() => { document.getElementById("psVal").value = "안동기 2026"; document.getElementById("psGo").click(); return 1; })()`); await sleep(2000);
  const rows = await P.eval(`document.querySelectorAll("#psRes tbody tr").length`);
  check(rows > 0, `검색 결과 행 (${rows}행)`);
  await snap(P, "1_검색", "검색·수정 구역");
  console.log("콘솔 오류:", P.logs.length ? P.logs.join("\n") : "없음");
  check(P.logs.length === 0, "화면 오류 없음");
} finally { await P.close(); }
process.exit(bad ? 1 : 0);
