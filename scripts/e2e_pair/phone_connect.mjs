// 기존 임차인 '내 페이지 연결 요청'(문자 인증 없이 임대인 승인): 공통 링크 → 연결 요청 → 확인번호 → 임대인 승인 → 비밀번호 설정 화면까지 폰 크기로 확인
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
const B = await launch("connl", 9621);  // 임대인 폰
const setVal = (id, v) => `(() => { const e = document.getElementById("${id}"); e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event("input", {bubbles:true})); })()`;
try {
  await A.goto(BASE + "/rent3/?home=1&find=1"); await sleep(3000);
  check(await A.eval(`getComputedStyle(document.getElementById("formReset")).display !== "none"`), "공통 링크(?find=1): 내 페이지 찾기가 바로 열림");
  check(await A.eval(`!!document.getElementById("lnkConnect")`), "연결 요청 링크가 보임");
  await snap(A, "1_내페이지찾기", "공통 링크로 열린 내 페이지 찾기(문자 인증) + 연결 요청 링크");
  await A.eval(`document.getElementById("lnkConnect").click()`); await sleep(500);
  await A.eval(setVal("ccBuilding", T.building)); await A.eval(setVal("ccRoom", T.room + "호"));
  await A.eval(setVal("ccName", T.name)); await A.eval(setVal("ccPhone", T.phone));
  await snap(A, "2_연결요청입력", "연결 요청 입력");
  await A.eval(`document.getElementById("btnCcRequest").click()`); await sleep(2000);
  check(await A.eval(`getComputedStyle(document.getElementById("ccWait")).display !== "none"`), "요청 접수: 대기 화면");
  const code = await A.eval(`document.getElementById("ccCode").textContent`);
  check(/^\d{4}$/.test(code), "4자리 확인번호 표시: " + code);
  await snap(A, "3_확인번호", "확인번호와 대기 화면");
  // 임대인 폰: 첫 화면에 요청 카드
  await asUser(B, L.user, L.token, "/rent3_lessor/?branch=main"); await sleep(3500);
  check(await B.eval(`!!document.querySelector("[data-conn]")`), "임대인 첫 화면에 연결 요청 카드");
  await snap(B, "4_임대인_요청카드", "임대인 첫 화면의 연결 요청 카드");
  await B.eval(`document.querySelector(".cn-code").value = "0000"; document.querySelector(".cn-ok").click()`); await sleep(1500);
  check(await B.eval(`!!document.querySelector("[data-conn]")`), "틀린 확인번호: 승인되지 않고 카드 유지");
  await B.eval(`document.querySelector(".cn-code").value = ${JSON.stringify(code)}; document.querySelector(".cn-ok").click()`); await sleep(2500);
  check(await B.eval(`!document.querySelector("[data-conn]")`), "맞는 확인번호: 승인 후 카드 사라짐");
  // 임차인 폰: 자동으로 비밀번호 설정(입주 안내) 화면으로
  await sleep(6000);
  check(await A.eval(`location.search.includes("invite=") || !!document.getElementById("r3InviteSetup")`), "승인 뒤 임차인 화면이 비밀번호 설정 화면으로 이동");
  await snap(A, "5_비밀번호설정", "승인 후 비밀번호·간편 비밀번호 설정 화면");
  // 임대인 메뉴: 안내 문자 만들기
  await B.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  await B.eval(`document.getElementById("btnConnectMsg").click()`); await sleep(1200);
  check(await B.eval(`document.getElementById("cmMsg").value.includes("find=1")`), "안내 문자에 공통 링크(find=1) 포함");
  await snap(B, "6_안내문자", "기존 임차인 앱 연결 안내 문자");
  console.log(bad ? `\n❌ 실패 ${bad}건` : "\n✅ 모두 통과");
} finally { await A.close(); await B.close(); }
process.exit(bad ? 1 : 0);
