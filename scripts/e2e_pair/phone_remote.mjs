// 관리인 원격 지원 흐름을 스마트폰 크기로 확인한다: 관리인 앱 원격 지원 카드 → 전화 접수 → 입장(띠) → 보기 전용 → 사용자 쪽 알림.
//   py scripts/serve_pool.py --pool-dir data/private/pool_t --port 9089 --admin-out data/private/admin.json
//   E2E_BASE=http://127.0.0.1:9089 node scripts/e2e_pair/phone_remote.mjs data/private/fresh.json data/private/admin.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_remote");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9089";
const BASE = process.env.E2E_BASE;
const { launch, snap, asUser, loginUser, sleep } = await import("./phone_ui.mjs");
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const admin = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
const ls = cfg.lessors.find((x) => x.username === "chic") || cfg.lessors[0];
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };

// 사용자(임대인): 원격 지원 허용 ON
const me = await loginUser(ls.username, ls.password);
const hdr = { "Content-Type": "application/json", Authorization: `Bearer ${me.token}` };
const on = await fetch(`${BASE}/api/rent3/pool/auth/consents/support`, { method: "POST", headers: hdr, body: JSON.stringify({ allow: true }) }).then((r) => r.json());
check(on.support_access === true, "사용자가 원격 지원 허용 ON");

const A = await launch("rm", 9616);
try {
  await asUser(A, admin.user, admin.token, "/rent3_admin/"); await sleep(3500);
  await snap(A, "1_원격지원카드", "관리인 앱 첫 화면: 원격 지원 카드");
  await A.eval(`(() => { const s = document.getElementById("spBranch"); s.value = "${me.user.branch}"; s.dispatchEvent(new Event("change", { bubbles: true })); return 1; })()`); await sleep(1800);
  await A.eval(`document.querySelector(".btn-sp-open").click()`); await sleep(1500);
  check(await A.eval(`!!document.getElementById("btnSpPhone")`), "임대인 카드의 지원 채팅 열림");
  check(await A.eval(`document.querySelector('.btn-sp-enter[data-view="lessor"]').disabled`), "채팅이 없으면 입장 단추가 비활성");
  await A.eval(`(() => { document.getElementById("spMemo").value = "수납 입력 방법을 모르심"; document.getElementById("btnSpPhone").click(); return 1; })()`); await sleep(2200);
  await snap(A, "2_전화접수후", "전화 접수를 남긴 뒤 (입장 가능)");
  check(await A.eval(`!document.querySelector('.btn-sp-enter[data-view="lessor"]').disabled`), "전화 접수 후 입장 단추 활성");
  await A.eval(`document.querySelector('.btn-sp-enter[data-view="lessor"]').click()`); await sleep(4500);
  check(await A.eval(`location.pathname.startsWith("/rent3_lessor")`), "임대인 앱으로 이동");
  check(await A.eval(`!!document.getElementById("r3ProxyBar")`), "관리인 대리 접속 띠 표시");
  check(await A.eval(`!document.getElementById("btnSupportChat")`), "관리인 화면에는 '관리인에게 문의' 메뉴 없음");
  await snap(A, "3_대리접속_임대인앱", "임대인 앱에 관리인이 들어온 모습(위 띠)");
  // 보기 전용: 수납 입력은 서버가 막는다
  await sleep(1000);   // 수납 내용은 호실 카드 아래에 이미 펼쳐져 있다
  await A.eval(`document.getElementById("pmDirect").click()`); await sleep(1500);
  const msg = await A.eval(`(document.querySelector(".bass-alert__msg") || {}).innerText || ""`);
  check(/보기 전용/.test(msg), `수납 입력 시도는 막힘: ${msg}`);
  await snap(A, "4_보기전용_막힘", "대리 접속 중 수납 입력 시도");
  // 사용자 쪽: 접속 알림과 대화
  await asUser(A, me.user, me.token, "/rent3_lessor/"); await sleep(4000);
  check(await A.eval(`!!document.getElementById("r3AdminViewing")`), "사용자 화면에 '관리인이 접속 중' 알림");
  await A.eval(`document.getElementById("btnSupportChat").click()`); await sleep(1500);
  const chat = await A.eval(`document.querySelector(".r3-modal__body").innerText`);
  check(/전화 요청 접수/.test(chat) && /관리인이 임대인 입장으로 접속/.test(chat), "사용자 지원 채팅에 전화 접수·접속 안내가 남음");
  await snap(A, "5_사용자_지원채팅", "사용자 쪽 지원 채팅");
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
