// 임차인 앱: 첫 화면(내 집) → ☰ 메뉴 → 각 화면(서비스 구역)으로 들어갔다 돌아오기를 스마트폰 크기로 확인한다.
//   py scripts/serve_pool.py --pool-dir data/private/pool_t --port 9090 --tokens-out data/private/tokens.json --tenants tenant_main_202,tenant_sinbi_103
//   E2E_BASE=http://127.0.0.1:9090 node scripts/e2e_pair/phone_tenant.mjs data/private/tokens.json tenant_main_202
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_tenant");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9090";
const { launch, snap, asUser, sleep } = await import("./phone_ui.mjs");
const tokens = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const tid = process.argv[3] || Object.keys(tokens)[0];
const t = tokens[tid];
const A = await launch("tn", 9617);
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const VIEWS = [["btnSvcCert", "cert", "납부확인서"], ["btnSvcPaper", "paper", "중개계약서"], ["btnSvcExpiry", "expiry", "만기안내"],
               ["btnSvcCheckout", "checkout", "퇴실정산"], ["btnSvcAs", "as", "AS요청"], ["btnSvcDoor", "door", "도어락차량"]];
try {
  await asUser(A, t.user, t.token, "/rent3_tenant/?tenant=" + tid); await sleep(4000);
  await snap(A, "1_첫화면", `${t.user.name} 내 집`);
  const visible = () => A.eval(`Array.from(document.querySelectorAll("section.svc")).filter((s) => s.style.display !== "none").map((s) => s.dataset.svc).join(",")`);
  check((await visible()) === "home", "첫 화면에는 '내 집' 구역만 보임");
  check(await A.eval(`!document.querySelector('[data-r3-drawer] a[href="/rent3_lessor/"]') && !document.querySelector('[data-r3-drawer] a[href="/rent3_admin/"]')`), "일반 임차인 메뉴에 임대인·관리인 앱 링크 없음");
  check(await A.eval(`!!document.getElementById("btnSupportChat") && !!document.getElementById("btnSupportSetting")`), "메뉴에 관리인 문의·원격 지원 설정 있음");
  await A.eval(`document.querySelector("[data-r3-burger]").click()`); await sleep(500);
  await snap(A, "2_메뉴", "☰ 메뉴");
  await A.eval(`document.querySelector(".r3-overlay") && document.querySelector(".r3-overlay").click()`); await sleep(300);
  for (const [id, view, name] of VIEWS) {
    await A.eval(`document.getElementById("${id}").click()`); await sleep(500);
    const v = await visible();
    const back = await A.eval(`document.getElementById("svcBack").style.display !== "none"`);
    check(v === view && back, `메뉴 ▸ ${name}: ${v} (뒤로가기 줄 ${back ? "있음" : "없음"})`);
    await snap(A, `3_${name}`, name);
  }
  await A.eval(`document.getElementById("btnSvcBack").click()`); await sleep(400);
  check((await visible()) === "home", "← 내 집으로 돌아옴");
  await A.eval(`document.getElementById("btnMenuListings").click()`); await sleep(1800);
  check(await A.eval(`document.getElementById("viewListings").style.display !== "none" && document.getElementById("viewMyhome").style.display === "none"`), "빈 집 찾기 화면");
  await snap(A, "4_빈집찾기", "빈 집 찾기");
  await A.eval(`document.getElementById("btnBackHome").click()`); await sleep(2000);
  check((await visible()) === "home", "빈 집 찾기에서 ← 내 집");
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
