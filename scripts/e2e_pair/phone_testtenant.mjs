// 관리인 앱 🛟 운영 탭의 '🧪 시험 임차인 등록' 화면을 폰 크기로 확인한다(점검 → 등록 → 재실행).
//   py scripts/serve_pool.py --pool-dir <풀 복사본> --port 9096 --admin-out data/private/admin.json   (RENT3_CONSENT_REQUIRED=0)
//   E2E_BASE=http://127.0.0.1:9096 node scripts/e2e_pair/phone_testtenant.mjs data/private/admin.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_testtenant");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9096";
const BASE = process.env.E2E_BASE;
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const A = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const P = await launch("tt", 9622);
const text = (id) => `document.getElementById("${id}").innerText`;
try {
  await asUser(P, A.user, A.token, "/rent3_admin/?tab=ops"); await sleep(3500);
  await P.eval(`(() => { const b = [...document.querySelectorAll("button, [data-tab]")].find(e => /운영|🛟/.test(e.textContent) && (e.dataset.tab === "ops" || e.tagName === "BUTTON")); if (b) b.click(); })()`); await sleep(2000);
  check(await P.eval(`!!document.getElementById("btnTtCheck")`), "🧪 시험 임차인 등록 카드가 보임");
  await P.eval(`document.getElementById("ttRoom").value = "799"; document.getElementById("ttName").value = "시험박"; document.getElementById("ttPhone").value = "010-1234-0799"`);
  await P.eval(`document.getElementById("btnTtCheck").click()`); await sleep(2000);
  check((await P.eval(text("ttResult"))).includes("점검 결과"), "점검: 아무것도 바꾸지 않고 계획을 보여 줌");
  await snap(P, "1_점검", "시험 임차인 점검 결과");
  await P.eval(`window.confirm = () => true; document.getElementById("btnTtApply").click()`); await sleep(2500);
  const done = await P.eval(text("ttResult"));
  check(done.includes("등록 완료") && /미납 0원/.test(done), "등록: 완료 + 장부 미납 0원 확인");
  await snap(P, "2_등록완료", "등록 완료 결과");
  await P.eval(`document.getElementById("btnTtApply").click()`); await sleep(2000);
  check((await P.eval(text("ttResult"))).includes("이미 있음"), "다시 눌러도 중복 없이 '이미 있음'");
  console.log(bad ? `\n❌ 실패 ${bad}건` : "\n✅ 모두 통과");
} finally { await P.close(); }
process.exit(bad ? 1 : 0);
