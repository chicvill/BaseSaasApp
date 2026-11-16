// 임대인 앱 ☰ 메뉴의 '원격 지원 허용 설정' 창과 포털 가입 화면의 동의 항목을 스마트폰 크기로 확인한다.
//   py scripts/serve_pool.py --pool-dir data/private/pool_t --port 9087
//   E2E_BASE=http://127.0.0.1:9087 node scripts/e2e_pair/phone_support.mjs data/private/fresh.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_support");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9087";
const { launch, snap, asUser, loginUser, sleep } = await import("./phone_ui.mjs");
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const ls = cfg.lessors.find((x) => x.username === "chic") || cfg.lessors[0];
const A = await launch("sp", 9615);
let bad = 0;
try {
  const res = await loginUser(ls.username, ls.password);
  await asUser(A, res.user, res.token, "/rent3_lessor/"); await sleep(3500);
  await A.eval(`document.getElementById("btnSupportSetting").click()`); await sleep(1200);
  await snap(A, "1_원격지원설정", "메뉴 ▸ 원격 지원 허용 설정 (처음: 허용 안 함)");
  await A.eval(`document.getElementById("r3SupOn").click()`); await sleep(1000);
  const on = await A.eval(`document.querySelector(".r3-modal__body").innerText.includes("허용 중 (ON)")`);
  console.log(on ? "✅ ON 으로 바뀜" : "❌ ON 이 반영되지 않음"); if (!on) bad++;
  await snap(A, "2_원격지원ON", "허용 ON 후");
  await A.eval(`document.getElementById("r3SupOff").click()`); await sleep(1000);
  const off = await A.eval(`document.querySelector(".r3-modal__body").innerText.includes("허용 안 함 (OFF)")`);
  console.log(off ? "✅ OFF 로 바뀜" : "❌ OFF 가 반영되지 않음"); if (!off) bad++;
  await A.goto(process.env.E2E_BASE + "/rent3/privacy.html"); await sleep(800);
  await snap(A, "3_처리방침", "개인정보 처리방침 초안");
  await A.goto(process.env.E2E_BASE + "/rent3/"); await sleep(1500);
  const hasBoxes = await A.eval(`!!document.getElementById("regAgreePrivacy") && !!document.getElementById("regAgreeSupport")`);
  console.log(hasBoxes ? "✅ 가입 화면에 동의 항목 있음" : "❌ 가입 화면에 동의 항목 없음"); if (!hasBoxes) bad++;
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
