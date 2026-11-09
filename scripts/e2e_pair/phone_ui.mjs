// scripts/e2e_pair/phone_ui.mjs — 스마트폰 크기 크롬 두 대(임대인·임차인)로 공동 계약 화면을 단계별로 열어 보고,
// 스크린샷과 레이아웃 점검(가로 넘침·작은 터치 영역·작은 글자)을 남긴다.
//
//   py scripts/e2e_pair/server.py --fresh                 # 먼저 시험 서버를 켠다 (다른 터미널)
//   node scripts/e2e_pair/phone_ui.mjs [--policy]         # 결과: e2e_runtime/phone/*.png, report.json
//
// 환경변수: CHROME(크롬 경로), PHONE_W/PHONE_H/PHONE_DPR(기본 360x800, 배율 2), E2E_BASE, PHONE_OUT
// 이 도구는 로컬 시험 서버(시연 로그인·가짜 본인인증 허용)에서만 쓴다.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const CHROME = process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const W = Number(process.env.PHONE_W || 360), H = Number(process.env.PHONE_H || 800), DPR = Number(process.env.PHONE_DPR || 2);
const BASE = (process.env.E2E_BASE || "http://127.0.0.1:9070").replace(/\/$/, "");
const OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone");
const UA = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

// ───────── 스마트폰 모양 크롬 ─────────
async function launch(name, port) {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), `phone_${name}_`));
  const proc = spawn(CHROME, [`--remote-debugging-port=${port}`, `--user-data-dir=${ud}`, "--headless=new", "--disable-gpu",
    "--no-first-run", "--no-default-browser-check", `--window-size=${W},${H}`, "about:blank"], { stdio: "ignore" });
  let targets = null;
  for (let i = 0; i < 150; i++) {            // 컴퓨터가 바쁠 때를 대비해 최대 30초 기다린다
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (targets.length) break; } catch (_) { /* 아직 시작 중 */ }
    await sleep(200);
  }
  if (!targets) { proc.kill(); throw new Error(`크롬(${CHROME})이 30초 안에 시작되지 않았습니다.`); }
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
    else if (d.method === "Runtime.exceptionThrown") logs.push("EXC " + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
    else if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") logs.push("ERR " + d.params.args.map((a) => a.value || a.description).join(" "));
  };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Runtime.enable"); await send("Page.enable");
  await send("Emulation.setUserAgentOverride", { userAgent: UA, platform: "Android" });
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DPR, mobile: true });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  const api = {
    name, logs,
    async goto(url) { await send("Page.navigate", { url }); await sleep(1800); },
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result.exceptionDetails) return "EVAL_ERR " + (r.result.exceptionDetails.exception?.description || "");
      return r.result.result.value;
    },
    async shot(file) { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(file, Buffer.from(r.result.data, "base64")); },
    async tap(selector) {            // 진짜 터치로 누른다
      const pos = await api.eval(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}); if(!e) return null; e.scrollIntoView({block:"center"}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
      if (!pos) return false;
      await sleep(250);
      await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: pos.x, y: pos.y }] });
      await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      return true;
    },
    async draw(selector) {           // 손가락으로 서명 패드에 선을 긋는다
      const box = await api.eval(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}); if(!e) return null; e.scrollIntoView({block:"center"}); const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}})()`);
      if (!box) return false;
      await sleep(250);
      const pts = []; for (let i = 0; i <= 24; i++) pts.push({ x: box.x + box.w * (0.1 + 0.8 * i / 24), y: box.y + box.h * (0.5 + 0.28 * Math.sin(i / 2.2)) });
      await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [pts[0]] });
      for (const p of pts.slice(1)) await send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [p] });
      await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      return true;
    },
    async close() { try { ws.close(); } catch (_) { /* 이미 닫힘 */ } proc.kill(); try { fs.rmSync(ud, { recursive: true, force: true }); } catch (_) { /* 정리 실패는 무시 */ } },
  };
  return api;
}

// ───────── 레이아웃 점검 (페이지 안에서 실행) ─────────
const AUDIT = `(() => {
  const vw = window.innerWidth;
  const vis = (e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && r.bottom > 0; };
  const label = (e) => (e.id ? "#" + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === "string" ? "." + e.className.trim().split(/\\s+/)[0] : "")) + ":" + (e.innerText || e.value || e.placeholder || "").trim().slice(0, 18);
  const out = { vw, docScrollW: document.documentElement.scrollWidth, overflowX: document.documentElement.scrollWidth > vw + 1, clipped: [], smallTaps: [], tinyText: 0, tinyTextSamples: [] };
  const all = Array.from(document.querySelectorAll("body *")).filter(vis);
  for (const e of all) {
    const r = e.getBoundingClientRect();
    if (r.right > vw + 1 && !e.closest("[data-allow-x], .r3-drawer") && !/^(SCRIPT|STYLE|BR)$/.test(e.tagName)) {
      let p = e.parentElement, scrollable = false;
      while (p) { const o = getComputedStyle(p).overflowX; if ((o === "auto" || o === "scroll") && p.scrollWidth > p.clientWidth) { scrollable = true; break; } p = p.parentElement; }
      if (!scrollable) out.clipped.push(label(e) + " right=" + Math.round(r.right));
    }
  }
  for (const e of all.filter((x) => x.matches("button, a.bass-btn, a[href], input:not([type=hidden]), select, textarea, [role=button], label.check-row"))) {
    const r = e.getBoundingClientRect();
    if (e.matches("input[type=checkbox], input[type=radio]") && e.closest("label") && e.closest("label").getBoundingClientRect().height >= 40) continue;
    if (r.height < 40 || r.width < 40) out.smallTaps.push(label(e) + " " + Math.round(r.width) + "x" + Math.round(r.height));
  }
  for (const e of all) {
    if (!e.childNodes.length || !Array.from(e.childNodes).some((n) => n.nodeType === 3 && n.nodeValue.trim())) continue;
    const fs = parseFloat(getComputedStyle(e).fontSize);
    if (fs < 12) { out.tinyText++; if (out.tinyTextSamples.length < 4) out.tinyTextSamples.push(label(e) + " " + fs + "px"); }
  }
  out.clipped = out.clipped.slice(0, 6); out.smallTaps = out.smallTaps.slice(0, 8);
  return out;
})()`;

const report = [];
async function snap(dev, name, note = "") {
  await sleep(700);
  const audit = await dev.eval(AUDIT);
  const scroller = await dev.eval(`(() => { const c = [document.querySelector("#stepCard"), document.querySelector("#stepModal"), document.querySelector(".dlg"), document.scrollingElement].filter(Boolean); const s = c.find((e) => e.scrollHeight > e.clientHeight + 40); return s ? { sel: s.id ? "#" + s.id : (s.className ? "." + String(s.className).split(" ")[0] : "html"), sh: s.scrollHeight, ch: s.clientHeight } : null })()`);
  const files = [];
  const f1 = path.join(OUT, `${name}.png`);
  await dev.shot(f1); files.push(f1);
  if (scroller && scroller.sh > scroller.ch + 40) {          // 한 화면보다 길면 아래쪽도 찍는다
    const pages = Math.min(3, Math.ceil(scroller.sh / scroller.ch) - 1);
    for (let i = 1; i <= pages; i++) {
      await dev.eval(`(() => { const s = ${scroller.sel === "html" ? "document.scrollingElement" : `document.querySelector(${JSON.stringify(scroller.sel)})`}; s.scrollTop = ${i} * (s.clientHeight - 60); })()`);
      await sleep(350);
      const f = path.join(OUT, `${name}-${i + 1}.png`);
      await dev.shot(f); files.push(f);
    }
    await dev.eval(`(() => { const s = ${scroller.sel === "html" ? "document.scrollingElement" : `document.querySelector(${JSON.stringify(scroller.sel)})`}; s.scrollTop = 0; })()`);
  }
  const bad = [];
  if (audit.overflowX) bad.push(`가로 스크롤 생김(문서 폭 ${audit.docScrollW} > 화면 ${audit.vw})`);
  if (audit.clipped && audit.clipped.length) bad.push(`화면 밖으로 나간 요소 ${audit.clipped.length}개: ${audit.clipped.slice(0, 3).join(" | ")}`);
  if (audit.smallTaps && audit.smallTaps.length) bad.push(`터치 영역 40px 미만 ${audit.smallTaps.length}개: ${audit.smallTaps.slice(0, 3).join(" | ")}`);
  if (audit.tinyText) bad.push(`12px 미만 글자 ${audit.tinyText}곳: ${audit.tinyTextSamples.slice(0, 2).join(" | ")}`);
  report.push({ name, note, files: files.map((f) => path.basename(f)), audit, scroller, issues: bad });
  console.log(`${bad.length ? "⚠️ " : "✅"} ${name}${note ? " — " + note : ""}${scroller ? ` (내용 ${scroller.sh}px / 화면 ${scroller.ch}px → ${files.length}장)` : ""}`);
  for (const b of bad) console.log("     · " + b);
}

// ───────── 서버·계정 도우미 ─────────
const PY = process.env.PYTHON || "py";
function play(...args) {
  const r = spawnSync(PY, [path.join(HERE, "play.py"), ...args], { cwd: ROOT, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1", E2E_BASE: BASE } });
  if (r.status !== 0) { console.log(r.stdout + r.stderr); throw new Error(`play.py ${args.join(" ")} 실패(${r.status})`); }
  return r.stdout;
}
const readState = () => JSON.parse(fs.readFileSync(path.join(ROOT, "e2e_runtime", "state.json"), "utf-8"));
async function api(method, url, body, token) {
  const r = await fetch(BASE + url, { method, headers: { "content-type": "application/json", ...(token ? { authorization: "Bearer " + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, json: await r.json().catch(() => ({})) };
}
async function loginUser(ident, pw) {
  const { json } = await api("POST", "/api/rent3/pool/auth/login", { login_mode: "account", identifier: ident, password: pw });
  return json;
}
async function asUser(dev, user, token, url) {
  await dev.goto(BASE + "/rent3/manual.html");
  await dev.eval(`localStorage.setItem("rent3_session", ${JSON.stringify(JSON.stringify(user))}); localStorage.setItem("bass_token", ${JSON.stringify(token)}); 1`);
  await dev.goto(BASE + url);
}

// ───────── 시나리오 ─────────
async function main() {
  const policy = process.argv.includes("--policy");
  const health = await fetch(BASE + "/api/health").then((r) => r.status).catch(() => 0);
  if (health !== 200) { console.error(`시험 서버(${BASE})에 연결할 수 없습니다. 먼저: py scripts/e2e_pair/server.py --fresh`); process.exit(2); }
  console.log(`화면 ${W}x${H} (배율 ${DPR}) · 크롬 ${CHROME}\n`);
  const A = await launch("lessor", 9451);      // 임대인 폰
  const B = await launch("tenant", 9452);      // 임차인 폰
  try {
    if (!policy) await normalFlow(A, B); else await policyFlow(A, B);
  } finally {
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify({ viewport: { W, H, DPR }, screens: report, consoleErrors: { lessor: A.logs, tenant: B.logs } }, null, 2));
    const issues = report.filter((r) => r.issues.length).length;
    console.log(`\n화면 ${report.length}개 점검 · 문제 있는 화면 ${issues}개 · 콘솔 오류 ${A.logs.length + B.logs.length}건 → ${OUT}`);
    await A.close(); await B.close();
  }
}

async function normalFlow(A, B) {
  play("reset");
  play("lessor", "prepare");
  const lessor = await loginUser("chic", "1212");
  await asUser(A, lessor.user, lessor.token, "/rent3_lessor/?branch=main");
  await snap(A, "01_임대인_장부홈", "임대인 폰: 호실 현황");
  play("tenant", "signup");
  const st = readState();
  const tenant = await loginUser(st.tenant_phone, "Gemini#Test9");
  await asUser(B, { ...tenant.user, role: "prospect" }, tenant.token, "/rent3/");
  await snap(B, "02_임차인_포털홈", "임차인 폰: 포털");
  await B.goto(BASE + "/rent3_contract/");
  await snap(B, "03_임차인_계약홈", "본인인증 완료 후 계약 홈");

  play("lessor", "call");
  const sid = readState().session_id;
  await asUser(A, lessor.user, lessor.token, "/rent3_contract/?session=" + sid);
  await snap(A, "04_임대인_호출대기_QR", "대기 화면: 사전 작성 + 대면 참여 QR");
  await B.goto(BASE + "/rent3_contract/"); await sleep(2500);
  await snap(B, "05_임차인_호출도착", "임차인 폰 홈: 계약 요청 배너");

  play("tenant", "respond");
  await B.goto(BASE + "/rent3_contract/?session=" + sid); await sleep(2500);
  await A.goto(BASE + "/rent3_contract/?session=" + sid); await sleep(2500);
  await snap(A, "06_임대인_1단계", "① 임대인 등기부 제시");
  await snap(B, "07_임차인_1단계_대기", "① 임차인은 임대인 차례를 기다림");

  play("lessor", "step1");
  await sleep(3500);
  await snap(B, "08_임차인_1단계_등기부", "① 임차인 직접 열람 입력 (가장 긴 화면)");
  play("tenant", "step1");
  await sleep(3500);
  await snap(A, "09_임대인_2단계_점검", "② 시설 점검 + 사진");
  play("lessor", "photo"); play("tenant", "photo");
  await A.goto(BASE + "/rent3_contract/?session=" + sid); await sleep(2500);
  await snap(A, "10_임대인_점검사진", "② 사진이 올라간 뒤");
  play("lessor", "step2"); play("tenant", "step2");
  await sleep(3500);
  await snap(A, "11_임대인_3단계_조건", "③ 계약 조건");
  await snap(B, "12_임차인_3단계_조건", "③ 임차인 화면");
  play("lessor", "agree"); play("tenant", "agree");
  await sleep(3500);
  await snap(A, "13_임대인_4단계_서명", "④ 서명 패드");
  const drew = await A.draw("canvas");
  await sleep(500);
  await snap(A, "14_임대인_서명후", drew ? "손가락으로 서명한 뒤" : "서명 패드를 찾지 못함");
  play("lessor", "sign"); play("tenant", "sign");
  await sleep(3500);
  await snap(B, "15_임차인_체결직전_재열람", "체결 직전 등기부 재열람");
  play("tenant", "recheck");
  await sleep(3500);
  await snap(A, "16_임대인_5단계_체결", "⑤ 입금 확인 + 체결");
  play("lessor", "complete");
  await A.goto(BASE + "/rent3_contract/?session=" + sid); await sleep(3000);
  await snap(A, "17_임대인_체결완료_계약서", "체결 완료: 인쇄용 계약서");
  await asUser(B, { ...tenant.user, role: "tenant", tenant_id: tenant.user.id }, tenant.token, "/rent3_tenant/?tenant=" + tenant.user.id);
  await sleep(2500);
  await snap(B, "18_임차인_마이홈_도어락잠금", "잔금 확인 전: 도어락 잠금 안내");
  play("tenant", "balance_check"); play("lessor", "release_keys");
  await B.goto(BASE + "/rent3_tenant/?tenant=" + tenant.user.id); await sleep(2500);
  await snap(B, "19_임차인_마이홈_도어락공개", "교부 후: 도어락 공개");
}

async function policyFlow(A, B) {
  const lessor = await loginUser("chic", "1212");
  async function setup(init, upTo) {                       // 새 호실·새 임차인으로 upTo 직전까지 진행
    fs.writeFileSync(path.join(ROOT, "e2e_runtime", "state.json"), JSON.stringify({ next: 0, log: [], ...init }));
    const order = ["lessor prepare", "tenant signup", "lessor call", "tenant respond", "lessor step1"];
    for (const o of order) { if (o === upTo) break; play(...o.split(" ")); }
    const st = readState();
    const tenant = await loginUser(st.tenant_phone, "Gemini#Test9");
    return { st, tenant };
  }
  // 소액 월세 + 근저당: 강한 적색 경고
  let { st, tenant } = await setup({ deposit: 10000000, mortgage: 500000000 }, "end");
  await asUser(B, { ...tenant.user, role: "prospect" }, tenant.token, "/rent3_contract/?session=" + st.session_id); await sleep(3000);
  await snap(B, "20_정책_소액월세_위험경고", "소액 월세 + 선순위 근저당: 강한 경고와 위험 확인 체크");
  // 소유자 불일치 입력 → 절대 차단 안내
  await B.eval(`(() => { const o = document.getElementById("trOwner"); if (o) { o.value = "박사기"; o.dispatchEvent(new Event("input", { bubbles: true })); } return 1; })()`);
  await sleep(600);
  await snap(B, "21_정책_소유자불일치_차단", "소유자 불일치를 입력하면 진행 불가 안내");
  await B.eval(`(() => { const o = document.getElementById("trOwner"); o.value = "김종심"; o.dispatchEvent(new Event("input", { bubbles: true })); const z = document.getElementById("trSeize"); z.value = "있음"; z.dispatchEvent(new Event("change", { bubbles: true })); return 1; })()`);
  await sleep(600);
  await snap(B, "22_정책_압류_차단", "압류·신탁 있음을 고르면 진행 불가 안내");
  // 전세 + 근저당 + 보증보험 가입 안 함 → 합의 차단 배너
  ({ st, tenant } = await setup({ deposit: 100000000, monthly_rent: 0, mortgage: 300000000, guarantee: "가입 안 함" }, "lessor step1"));
  for (const step of ["lessor step1", "tenant step1", "lessor photo", "tenant photo", "lessor step2", "tenant step2"]) play(...step.split(" "));
  await asUser(A, lessor.user, lessor.token, "/rent3_contract/?session=" + st.session_id); await sleep(3000);
  await snap(A, "23_정책_전세_근저당_합의차단", "전세 + 근저당 + 가입 안 함: 말소 특약 체크 안내");
  await A.tap("#tCleanup"); await sleep(300);
  await A.tap("#btnSaveTerms"); await sleep(2500);
  await snap(A, "24_정책_말소특약_적용", "말소 특약을 넣은 뒤");
  // 전세 + 근저당 + 가입 예정 → 거절 특약 자동 삽입
  ({ st, tenant } = await setup({ deposit: 100000000, monthly_rent: 0, mortgage: 300000000, guarantee: "가입 예정" }, "lessor step1"));
  for (const step of ["lessor step1", "tenant step1", "lessor photo", "tenant photo", "lessor step2", "tenant step2"]) play(...step.split(" "));
  await asUser(B, { ...tenant.user, role: "prospect" }, tenant.token, "/rent3_contract/?session=" + st.session_id); await sleep(3000);
  await snap(B, "25_정책_보증보험예정_자동특약", "가입 예정: 거절 시 계약 무효 특약이 특약사항에 자동으로 들어감");
}

export { launch, snap, asUser, loginUser, sleep };
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
