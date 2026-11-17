// 카드 선택 → 검색줄 → 결과 JSON → 수정 → 저장 흐름을 실제 브라우저로 확인한다(풀 복사본에서만 실행할 것 — 한 건을 실제로 고친다).
//   py scripts/serve_pool.py --pool-dir <풀 복사본> --port 9164 --admin-out e2e_runtime/admin_tok.json
//   E2E_BASE=http://127.0.0.1:9164 node scripts/e2e_pair/pool_card_search_ui.mjs e2e_runtime/admin_tok.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "pool_card_search_ui");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9164";
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const L = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const P = await launch("pcard", 9625);
try {
  await asUser(P, L.user, L.token, "/rent3/pool.html"); await sleep(3500);
  await P.eval(`document.querySelector('.file-card[data-col="payments"]').click(); 1`); await sleep(2500);
  check(await P.eval(`document.getElementById("rawBarTitle").textContent.includes("payments")`), "카드를 고르면 검색줄 제목이 payments.json 으로 바뀜");
  await P.eval(`(() => { document.getElementById("rawQ").value = "202 2026-09"; document.getElementById("rawGo").click(); return 1; })()`); await sleep(2000);
  const n = await P.eval(`document.querySelectorAll("#rawHits .rh").length`);
  check(n >= 1, `포함된 건이 카드 안에 JSON 으로 나타남 (${n}건)`);
  check(await P.eval(`document.getElementById("rawJsonBox").hidden === true`), "검색 중에는 전체 원문 상자를 숨김");
  await sleep(4500);                                                                                   // 3초 자동 갱신이 지나도 결과가 유지돼야 한다
  check(await P.eval(`document.querySelectorAll("#rawHits .rh").length`) === n, "3초 자동 갱신이 검색 결과를 덮어쓰지 않음");
  await P.eval(`document.querySelector("#rawHits .rh-edit").click(); 1`); await sleep(600);
  await P.eval(`(() => { const ta = document.querySelector("#rawHits .rh-ta"); const o = JSON.parse(ta.value); o.payer_name = "카드수정시험" + Math.floor(Math.random() * 1000); ta.value = JSON.stringify(o, null, 2); document.querySelector("#rawHits .rh-why").value = "화면 시험"; window.confirm = () => true; document.querySelector("#rawHits .rh-save").click(); return 1; })()`); await sleep(2500);
  const got = await P.eval(`document.getElementById("rawHits").innerText.includes("카드수정시험") && document.getElementById("rawHits").innerText.includes("저장됨")`);
  check(got, "저장 뒤 결과가 다시 그려짐");
  await P.eval(`document.getElementById("rawAll").click(); 1`); await sleep(500);
  check(await P.eval(`document.getElementById("rawJsonBox").hidden === false`), "[전체 보기]로 원문 상자로 돌아옴");
  await snap(P, "1_카드검색", "카드 검색");
  console.log("콘솔 오류:", P.logs.length ? P.logs.join("\n") : "없음");
  check(P.logs.length === 0, "화면 오류 없음");
} finally { await P.close(); }
process.exit(bad ? 1 : 0);
