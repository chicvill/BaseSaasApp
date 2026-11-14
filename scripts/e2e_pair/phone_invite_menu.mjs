// 임대인 ☰ 메뉴의 '기존 임차인 앱 연결 안내 문자'를 진짜 터치로 눌러 창이 열리는지 확인한다(가져온 실제 데이터 풀로).
//   py scripts/serve_pool.py --pool-dir <풀 복사본> --port 9097 --lessor-out data/private/lessor_pin.json   (RENT3_CONSENT_REQUIRED=0)
//   E2E_BASE=http://127.0.0.1:9097 node scripts/e2e_pair/phone_invite_menu.mjs data/private/lessor_pin.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_invite_menu");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9097";
const BASE = process.env.E2E_BASE;
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const L = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const P = await launch("invm", 9623);
try {
  await asUser(P, L.user, L.token, "/rent3_lessor/?branch=main"); await sleep(4000);
  await P.tap("[data-r3-burger]"); await sleep(700);
  check(await P.eval(`!!document.getElementById("btnConnectMsg")`), "메뉴에 '기존 임차인 앱 연결 안내' 항목이 있음");
  check(await P.tap("#btnConnectMsg"), "진짜 터치로 누름");
  await sleep(2500);
  const shown = await P.eval(`!!document.getElementById("r3Modal")`);
  check(shown, "창이 열림");
  check(await P.eval(`!!document.getElementById("ivMake") || /불러오는 중/.test(document.getElementById("r3Modal")?.innerText || "")`), "창 안에 내용(만들기 단추 또는 불러오는 중)");
  const txt = await P.eval(`(document.getElementById("r3Modal")?.innerText || "").slice(0, 300)`);
  console.log("창 내용:", String(txt).replace(/\n/g, " | "));
  console.log("콘솔 오류:", P.logs.length ? P.logs.join("\n") : "없음");
  await snap(P, "1_창", "메뉴 항목을 눌렀을 때");
  check(P.logs.length === 0, "화면 오류(예외) 없음");
  console.log(bad ? `\n❌ 실패 ${bad}건` : "\n✅ 모두 통과");
} finally { await P.close(); }
process.exit(bad ? 1 : 0);
