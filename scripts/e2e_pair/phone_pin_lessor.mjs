// 임대인 간편 비밀번호(PIN): 홈 화면 아이콘으로 연 것처럼 qk 주소를 열어 PIN 화면 → 로그인 → 제한된 메뉴·안내 띠 → 설정 창(수납 허용) 확인
//   RENT3_LESSOR_PIN=1 py scripts/serve_pool.py --pool-dir data/private/pool_pin --port 9094 --lessor-out data/private/lessor_pin.json
//   E2E_BASE=http://127.0.0.1:9094 node scripts/e2e_pair/phone_pin_lessor.mjs data/private/lessor_pin.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_pin_lessor");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9094";
const BASE = process.env.E2E_BASE;
const { launch, snap, sleep, asUser } = await import("./phone_ui.mjs");
const L = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const full = { Authorization: `Bearer ${L.token}`, "Content-Type": "application/json" };
const api = async (p, body) => { const r = await fetch(BASE + "/api/rent3/pool" + p, { method: body ? "POST" : "GET", headers: full, body: body ? JSON.stringify(body) : undefined }); return { ok: r.ok, d: await r.json() }; };
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const st0 = await api("/shortcut/status");
check(st0.ok && st0.d.applicable, "임대인 바로가기 사용 가능(스위치 켜짐)");
if (!st0.d.has_pin) check((await api("/shortcut/pin", { pin: "2846" })).ok, "PIN 설정(2846)");
for (const d of st0.d.devices || []) await api(`/shortcut/devices/${d.id}/revoke`, {});
const dev = await api("/shortcut/devices", { label: "화면확인폰" });
check(dev.ok && dev.d.qk && dev.d.start_url.startsWith("/rent3_lessor/?qk="), "바로가기 기기 발급");
const A = await launch("pinl", 9619);
const pressPin = async (pin) => { for (const k of pin) { await A.eval(`document.querySelector('#r3PinPad [data-k="${k}"]').click()`); await sleep(120); } await sleep(1300); };
try {
  // 비밀번호 로그인이 남아 있어도 바로가기로 열면 PIN 을 먼저 묻는다(PIN 우선)
  await asUser(A, L.user, L.token, "/rent3_lessor/?branch=main"); await sleep(3000);
  check(await A.eval(`!document.getElementById("r3PinGate")`), "일반 주소: 비밀번호 로그인 상태면 PIN 없이 열림");
  await A.goto(BASE + dev.d.start_url); await sleep(3000);
  check(await A.eval(`!!document.getElementById("r3PinGate")`), "바로가기 주소: 비밀번호 로그인이 남아 있어도 PIN 화면이 뜸(PIN 우선)");
  const who = await A.eval(`document.getElementById("r3PinWho").textContent`);
  console.log("표시:", who);
  check(/\*/.test(who) && !/호/.test(who), "이름 가림, 건물·호실은 숨김");
  await snap(A, "1_PIN화면", "임대인 PIN 화면");
  await pressPin("9999");
  check(/남은 시도 4회/.test(await A.eval(`document.getElementById("r3PinMsg").textContent`)), "틀리면 남은 횟수 안내");
  await pressPin("2846"); await sleep(4000);
  check(await A.eval(`!document.getElementById("r3PinGate")`), "맞으면 PIN 화면이 사라지고 앱이 열림");
  check(await A.eval(`JSON.parse(atob(localStorage.getItem("bass_token").split(".")[0].replace(/-/g,"+").replace(/_/g,"/"))).m === "pin"`), "토큰이 PIN 토큰으로 바뀜(비밀번호 토큰은 내려감)");
  check(await A.eval(`!!document.getElementById("pinBar")`), "상단 안내 띠(간편 비밀번호 접속 중)");
  check(await A.eval(`document.getElementById("pinBar").innerText.includes("수납 불가")`), "수납 허용 전: 수납 불가 안내");
  check(await A.eval(`document.getElementById("bankSmsGo").disabled === true`), "수납 허용 전: 문자 수납 단추 비활성");
  check(await A.eval(`!document.querySelector('[data-act="contract"]') || document.querySelector('[data-act="contract"]').style.display === "none"`), "계약·만기·전출 단추 숨김");
  await snap(A, "2_PIN로그인후", "PIN 로그인 뒤 첫 화면(수납 불가 상태)");
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(600);
  const labels = await A.eval(`[...document.querySelectorAll("[data-r3-drawer] button, [data-r3-drawer] a")].map(e => e.textContent.trim()).join(" | ")`);
  console.log("메뉴:", labels);
  check(!/세무|엑셀|건물 정보|계약|매물|일괄/.test(labels), "허용되지 않는 메뉴(세무·CSV·건물·계약·매물·일괄통보)는 보이지 않음");
  check(/바로가기/.test(labels) && /아이디·비밀번호로 로그인/.test(labels), "메뉴에 '바로가기 설정'과 '비밀번호로 로그인'");
  await snap(A, "3_메뉴", "PIN 상태 메뉴");
  await A.eval(`document.getElementById("btnShortcut").click()`); await sleep(1500);
  check(await A.eval(`document.body.innerText.includes("간편 비밀번호로 들어온 상태")`), "PIN 상태에서는 설정 변경 불가 안내");
  await snap(A, "4_설정창_PIN상태", "PIN 상태의 설정 창");
  // 일반 로그인으로 수납 허용 켜기
  await asUser(A, L.user, L.token, "/rent3_lessor/?branch=main"); await sleep(3500);
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(600);
  await A.eval(`document.getElementById("btnShortcut").click()`); await sleep(1500);
  await snap(A, "5_설정창_일반로그인", "일반 로그인 상태의 설정 창(수납 허용 켜기 단추)");
  check(await A.eval(`!!document.querySelector("button[data-pay][data-on]")`), "기기별 '수납 허용 켜기' 단추");
  await A.eval(`document.querySelector("button[data-pay][data-on]").click()`); await sleep(600);
  check(await A.eval(`!!document.querySelector("[data-paypw]")`), "켜기: 비밀번호 확인 입력칸");
  await snap(A, "6_수납허용_비밀번호", "수납 허용 켜기 — 비밀번호 확인");
  console.log(bad ? `\n❌ 실패 ${bad}건` : "\n✅ 모두 통과");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
