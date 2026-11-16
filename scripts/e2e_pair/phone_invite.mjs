// 입주 안내 링크(가입 화면)를 스마트폰 크기로 확인한다.
//   py scripts/serve_pool.py --pool-dir data/private/pool_inv --port 9099
//   E2E_BASE=http://127.0.0.1:9099 node scripts/e2e_pair/phone_invite.mjs <입주 안내 링크 경로(/rent3_tenant/?invite=...)>
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_invite");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9099";
const { launch, snap, sleep } = await import("./phone_ui.mjs");
const A = await launch("inv", 9622);
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
try {
  await A.goto(process.env.E2E_BASE + process.argv[2]); await sleep(2500);
  await snap(A, "1_가입화면", "입주 안내 링크로 연 가입 화면");
  check(await A.eval(`!document.getElementById("r3InvForm").hidden`), "입력 양식이 보임");
  await A.eval(`document.getElementById("r3InvPw").value="Invite2026x"; document.getElementById("r3InvPw2").value="Invite2026x"; document.getElementById("r3InvPin").value="2846"; document.getElementById("r3InvAgree").click(); document.getElementById("r3InvGo").click()`);
  await sleep(4500);
  await snap(A, "2_가입후", "가입 직후(홈 화면 추가 안내)");
  check(await A.eval(`!document.getElementById("r3InviteSetup")`), "가입 화면이 사라지고 앱이 열림");
  check(await A.eval(`document.body.innerText.includes("홈 화면에 추가")`), "홈 화면 추가 안내 창");
  check(await A.eval(`location.search.includes("qk=") && !location.search.includes("welcome")`), "주소에 qk 유지, welcome 제거");
  await A.eval(`document.querySelector(".r3-modal__x") && document.querySelector(".r3-modal__x").click()`);
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  check(await A.eval(`!!document.querySelector('[data-r3-drawer] a[href="/rent3/?logout=1"]')`), "서랍에 로그아웃");
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
