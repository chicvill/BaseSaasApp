// 스마트폰 바로가기 + 간편 비밀번호(PIN): 홈 화면 아이콘으로 연 것처럼 qk 주소를 열어 PIN 화면 → 로그인 → 메뉴 창 → 차단을 스마트폰 크기로 확인한다.
//   py scripts/serve_pool.py --pool-dir data/private/pool_pin --port 9093 --tokens-out data/private/tokens_pin.json --tenants tenant_main_202
//   E2E_BASE=http://127.0.0.1:9093 node scripts/e2e_pair/phone_pin.mjs data/private/tokens_pin.json tenant_main_202
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_pin");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9093";
const BASE = process.env.E2E_BASE;
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const tokens = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const tid = process.argv[3] || Object.keys(tokens)[0];
const full = { Authorization: `Bearer ${tokens[tid].token}`, "Content-Type": "application/json" };
const api = async (p, body) => { const r = await fetch(BASE + "/api/rent3/pool" + p, { method: body ? "POST" : "GET", headers: full, body: body ? JSON.stringify(body) : undefined }); return { ok: r.ok, d: await r.json() }; };
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const st0 = await api("/shortcut/status");
if (!st0.d.has_pin) { const r = await api("/shortcut/pin", { pin: "2846" }); check(r.ok, "PIN 설정(2846)"); }
const dev = await api("/shortcut/devices", { label: "화면확인폰" });
check(dev.ok && dev.d.qk, "바로가기 기기 발급");
const A = await launch("pin", 9618);
const pressPin = async (pin) => { for (const k of pin) { await A.eval(`document.querySelector('#r3PinPad [data-k="${k}"]').click()`); await sleep(120); } await sleep(1200); };
try {
  await A.goto(BASE + dev.d.start_url); await sleep(2500);
  await snap(A, "1_PIN화면", "홈 화면 아이콘으로 열었을 때");
  const who = await A.eval(`document.getElementById("r3PinWho").textContent`);
  console.log("표시:", who);
  check(/\*/.test(who) && /202호/.test(who), "이름 가림 + 호실 표시");
  check(await A.eval(`!!document.querySelector('link[rel="manifest"]') && document.querySelector('link[rel="manifest"]').href.includes("qk=")`), "매니페스트 링크가 이 기기 키를 가리킴");
  await pressPin("9999");
  const msg = await A.eval(`document.getElementById("r3PinMsg").textContent`);
  check(/남은 시도 4회/.test(msg), "틀리면 남은 횟수 안내: " + msg);
  await snap(A, "2_PIN틀림", "틀린 PIN");
  await pressPin("2846"); await sleep(3500);
  check(await A.eval(`!document.getElementById("r3PinGate")`), "맞으면 PIN 화면이 사라지고 앱이 열림");
  check(await A.eval(`(JSON.parse(localStorage.getItem("rent3_session")||"{}")).role === "tenant"`), "세션이 입주 임차인으로 저장됨");
  await snap(A, "3_PIN로그인후", "PIN 로그인 뒤 내 집");
  const claim = await A.eval(`JSON.parse(atob(localStorage.getItem("bass_token").split(".")[0].replace(/-/g,"+").replace(/_/g,"/"))).m`);
  check(claim === "pin", "토큰에 m=pin 표시");
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  check(await A.eval(`!!document.getElementById("btnShortcut")`), "메뉴에 '바로가기 · 간편 비밀번호'");
  await A.eval(`document.getElementById("btnShortcut").click()`); await sleep(1500);
  await snap(A, "4_바로가기창_PIN상태", "PIN 로그인 상태에서 연 설정 창(변경 불가 안내)");
  check(await A.eval(`document.body.innerText.includes("간편 비밀번호로 들어온 상태")`), "PIN 상태에서는 설정 변경이 막혔다는 안내");
  const blocked = await A.eval(`fetch("/api/rent3_tenant/checkout/x/agree",{method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+localStorage.getItem("bass_token")},body:"{}"}).then(r=>r.status)`);
  check(blocked === 403, "퇴실 정산 동의는 PIN 상태에서 403");
  // 만료 흉내: 토큰을 지우고 새로고침하면 다시 PIN 화면(포털로 쫓겨나지 않음)
  await A.eval(`localStorage.removeItem("bass_token"); localStorage.removeItem("rent3_session"); location.reload()`); await sleep(2500);
  check(await A.eval(`!!document.getElementById("r3PinGate") && location.pathname.startsWith("/rent3_tenant")`), "로그인이 없으면 포털로 가지 않고 PIN 화면");
  for (let i = 0; i < 5; i++) await pressPin("1111");
  await sleep(500);
  await snap(A, "5_잠김", "5번 틀려 잠긴 화면");
  check(await A.eval(`document.getElementById("r3PinMsg").textContent.includes("잠겼")`), "5번 틀리면 잠금 안내");
  // 아이디·비밀번호로 로그인한 상태: 설정 창에서 새 기기 바로가기 만들기·안내·해제
  await asUser(A, tokens[tid].user, tokens[tid].token, "/rent3_tenant/?tenant=" + tid); await sleep(3500);
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  await A.eval(`document.getElementById("btnShortcut").click()`); await sleep(1500);
  await snap(A, "6_바로가기창_일반로그인", "일반 로그인 상태의 설정 창(기기 목록·잠긴 기기 표시)");
  check(await A.eval(`!!document.getElementById("r3DevAdd") && !document.getElementById("r3DevAdd").disabled`), "바로가기 만들기 단추 사용 가능");
  check(await A.eval(`document.body.innerText.includes("잠김")`), "5번 틀린 기기는 '잠김'으로 표시");
  await A.eval(`document.getElementById("r3DevAdd").click()`); await sleep(2000);
  await snap(A, "7_바로가기_설치안내", "만든 직후 설치 안내(안드로이드·아이폰)");
  check(await A.eval(`location.search.includes("qk=")`), "만든 뒤 주소에 qk 가 들어감(아이폰 홈 화면 추가용)");
  check(await A.eval(`document.body.innerText.includes("홈 화면에 추가")`), "홈 화면 추가 안내");
  const before = await A.eval(`document.querySelectorAll("[data-rev]").length`);
  await A.eval(`document.querySelector("[data-rev]").click()`); await sleep(1500);
  check(await A.eval(`document.querySelectorAll("[data-rev]").length`) === before - 1, `기기 해제(${before}대→${before - 1}대)`);
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
