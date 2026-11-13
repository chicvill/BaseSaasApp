// 기존 임차인 '내 페이지 연결'(문자 인증 없이 임대인 승인): 임대인이 보낸 링크 → 입력 창 하나 → 확인번호·임대인 전화 → 임대인 폰에 승인 창이 자동으로 뜸 → 승인 → 임차인 화면이 비밀번호 설정으로 이동
//   py scripts/serve_pool.py --pool-dir data/private/pool_pin --port 9095 --lessor-out data/private/lessor_pin.json   (RENT3_CONSENT_REQUIRED=0)
//   E2E_BASE=http://127.0.0.1:9095 node scripts/e2e_pair/phone_connect.mjs data/private/lessor_pin.json data/private/connect_pick.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_connect");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9095";
const BASE = process.env.E2E_BASE;
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const L = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const T = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const A = await launch("conn", 9620);   // 임차인 폰
const B = await launch("connl", 9621);  // 임대인 폰(이미 로그인해 있음)
const fill = (id, v) => `(() => { const e = document.getElementById("${id}"); e.value = ${JSON.stringify(v)}; })()`;
try {
  // 임대인은 먼저 로그인해 있다 — 요청이 오면 창이 자동으로 뜨는지 본다
  await asUser(B, L.user, L.token, "/rent3_lessor/?branch=main"); await sleep(3500);
  check(await B.eval(`!document.getElementById("r3Modal")`), "임대인 화면: 요청 전에는 창이 없음");
  // 임차인: 링크를 누르면 연결 창 하나만
  await A.goto(BASE + "/rent3/?connect=main"); await sleep(3000);
  check(await A.eval(`!!document.getElementById("r3Connect") && !!document.getElementById("btnCcRequest")`), "링크: 메뉴 없이 연결 창만 열림");
  check(await A.eval(`!!document.querySelector('#r3Connect a[href^="tel:"]')`), "임대인 전화번호(눌러서 전화)가 보임");
  check(await A.eval(`!document.getElementById("ccBuilding")`), "건물명 입력 없음(링크가 건물을 정함)");
  await snap(A, "1_링크로열린창", "임대인이 보낸 링크로 열린 입력 창");
  await A.eval(fill("ccRoom", T.room + "호")); await A.eval(fill("ccName", T.name)); await A.eval(fill("ccPhone", T.phone));
  await A.eval(`document.getElementById("btnCcRequest").click()`); await sleep(2500);
  const code = await A.eval(`document.getElementById("ccStatus") ? [...document.querySelectorAll("#r3Connect div")].map(d => d.textContent.trim()).find(t => /^\\d{4}$/.test(t)) : ""`);
  check(/^\d{4}$/.test(code || ""), "확인번호 표시: " + code);
  check(await A.eval(`!!document.querySelector('#r3Connect a[href^="tel:"]')`), "대기 화면에도 임대인 전화 단추");
  await snap(A, "2_확인번호와전화", "확인번호 + 임대인에게 전화 + 상태");
  // 임대인 폰: 로그인 상태에서 승인 창이 자동으로 뜸
  await sleep(8000);
  check(await B.eval(`!!document.getElementById("cmCode")`), "임대인 화면: 로그인 상태에서 승인 창이 자동으로 뜸");
  check(await B.eval(`!!document.querySelector('#r3Modal a[href^="tel:"]')`), "승인 창에 임차인 전화(눌러서 전화)");
  await snap(B, "3_임대인_승인창", "임대인 화면에 자동으로 뜬 승인 창");
  await B.eval(`document.getElementById("cmCode").value = "0000"; document.getElementById("cmOk").click()`); await sleep(1500);
  check(await B.eval(`!!document.getElementById("cmCode")`), "틀린 확인번호: 창 유지(승인 안 됨)");
  await B.eval(`document.getElementById("cmCode").value = ${JSON.stringify(code)}; document.getElementById("cmOk").click()`); await sleep(2500);
  check(await B.eval(`!document.getElementById("r3Modal")`), "맞는 확인번호: 승인되고 창이 닫힘");
  check(await B.eval(`!!document.getElementById("connBar")`), "임대인 화면 위에 임차인 진행 상태 띠");
  // 임차인 폰: 통화 중 화면에서 승인이 바로 보이고 비밀번호 설정으로 넘어감
  await sleep(6000);
  check(await A.eval(`location.search.includes("invite=") || !!document.getElementById("r3InviteSetup")`), "임차인 화면: 승인되자 비밀번호 설정 화면으로 이동");
  await snap(A, "4_비밀번호설정", "승인 후 비밀번호·간편 비밀번호 설정");
  await sleep(7000);
  await snap(B, "5_임대인_진행띠", "임대인 화면의 임차인 연결 진행 상태");
  check(await B.eval(`document.getElementById("connBar") && /비밀번호/.test(document.getElementById("connBar").textContent)`), "진행 띠에 '비밀번호 설정' 상태가 보임");
  // 임대인 메뉴: 안내 문자 예시
  await B.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  await B.eval(`document.getElementById("btnConnectMsg").click()`); await sleep(1200);
  const msg = await B.eval(`document.getElementById("cmMsg").value`);
  check(/\?connect=main/.test(msg) && /전화/.test(msg), "안내 문자: 링크(?connect=지점코드) + 전화 안내 포함");
  console.log("----- 안내 문자 예시 -----\n" + msg + "\n--------------------------");
  await snap(B, "6_안내문자", "기존 임차인 앱 연결 안내 문자");
  console.log(bad ? `\n❌ 실패 ${bad}건` : "\n✅ 모두 통과");
} finally { await A.close(); await B.close(); }
process.exit(bad ? 1 : 0);
