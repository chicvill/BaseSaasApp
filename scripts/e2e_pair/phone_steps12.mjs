// 계약 화면 ①② 단계의 진행 표시(점 두 개)를 스마트폰 크기로 확인한다. 3단계까지 진행된 방(make_contract_room.py)을 ①·② 상태로 되돌려서 본다.
//   py scripts/make_contract_room.py --pool-dir data/private/pool_s12 --out data/private/party_s12.json
//   (세션을 ①·② 상태로 되돌리는 건 풀 파일을 직접 고치는 작은 파이썬으로 따로 한다 — 시험용 풀에서만)
//   ROLE=lessor|tenant 로 어느 쪽 화면을 볼지 고른다(기본 tenant)
//   py scripts/serve_pool.py --pool-dir data/private/pool_s12 --port 9095
//   E2E_BASE=http://127.0.0.1:9095 node scripts/e2e_pair/phone_steps12.mjs data/private/party_s12.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_steps12");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9095";
const BASE = process.env.E2E_BASE;
const { launch, snap, asUser, sleep } = await import("./phone_ui.mjs");
const party = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const sid = party.session_id;
const L = await launch("s12", 9621);
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
try {
  const ROLE = process.env.ROLE || "tenant";
  await asUser(L, party[ROLE].user, party[ROLE].token, `/rent3_contract/?session=${encodeURIComponent(sid)}&role=${ROLE}`);
  await sleep(4500);
  await snap(L, `1_${ROLE}_현재단계`, `${ROLE} 화면의 현재 단계`);
  console.log("점 줄 수:", await L.eval(`document.querySelectorAll(".dotline").length`));
  check(await L.eval(`!document.body.innerText.includes("미체크") && !document.body.innerText.includes("미확인")`), "옛 '미체크·미확인' 문구가 없음");
  check(await L.eval(`document.querySelectorAll(".dotline").length >= 1`), "점 두 개 줄이 있음(① 신분증·등기부 2줄, ② 시설 점검 1줄)");
  console.log("콘솔 오류:", L.logs.length ? L.logs.join("\n") : "없음");
} finally { await L.close(); }
process.exit(bad ? 1 : 0);
