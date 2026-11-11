// 계약 화면(6단계): 지금 내 차례 한 줄 · 💬 대화창 · 3단계 제안/수락 · 전체 동의 · 조건 다시 열기를 두 사람(임대인·임차인) 휴대폰 크기로 확인한다.
//   py scripts/make_contract_room.py --pool-dir data/private/pool_c --out data/private/party.json
//   py scripts/serve_pool.py --pool-dir data/private/pool_c --port 9093
//   E2E_BASE=http://127.0.0.1:9093 node scripts/e2e_pair/phone_contract.mjs data/private/party.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_contract");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9093";
const BASE = process.env.E2E_BASE;
const { launch, snap, asUser, sleep } = await import("./phone_ui.mjs");
const party = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const sid = party.session_id;
const L = await launch("cl", 9619);
const T = await launch("ct", 9620);
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const ev = (D, code) => D.eval(code);
const turn = (D) => ev(D, `document.getElementById("turnLine").textContent`);
const open = async (D, who, role) => {
  await asUser(D, who.user, who.token, `/rent3_contract/?session=${encodeURIComponent(sid)}&role=${role}`);
  await sleep(4500);
  await ev(D, `window.confirm = () => true; 1`);
};
const api = (who, method, p, body) => fetch(`${BASE}/api/rent3_contract${p}`, { method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${who.token}` }, body: body ? JSON.stringify(body) : undefined }).then((r) => r.json());

try {
  // ── 임대인: 3단계 화면 ──
  await open(L, party.lessor, "lessor");
  await snap(L, "1_임대인_3단계", "임대인: 계약 조건 단계");
  check(/지금 내 차례/.test(await turn(L)), `상태 한 줄: ${await turn(L)}`);
  check(/답할 제안·수정 요청 1건/.test(await turn(L)), "열린 제안 1건이 상태 한 줄에 표시");
  check((await ev(L, `document.querySelectorAll(".btn-propose").length`)) === 6, "6개 항목마다 '제안' 단추");
  check(await ev(L, `!document.getElementById("tDeposit") && !document.getElementById("tRent") && !document.getElementById("tMoveIn")`), "금액·날짜 입력칸 없음(보기만)");
  check(await ev(L, `document.getElementById("btnAgreeStep3").disabled`), "열린 제안이 있어 '전체 동의' 비활성");
  check(await ev(L, `!!document.getElementById("btnReqSpecial")`), "특약 '수정 요청' 단추");
  const unread = await ev(L, `document.querySelector(".chat-fab__n").textContent`);
  check(Number(unread) >= 1 && !(await ev(L, `document.getElementById("chatFab").hidden`)), `💬 단추와 안 읽은 수 배지(${unread}) — 대화창을 열기 전에는 유지`);
  await sleep(6000);   // 2.5초 갱신이 두 번 이상 돌아도 배지가 유지되어야 한다
  check(Number(await ev(L, `document.querySelector(".chat-fab__n").textContent`)) === Number(unread), "폴링 뒤에도 안 읽은 수 배지 유지");

  // ── 임대인: 대화창 열고 제안 수락 ──
  await ev(L, `document.getElementById("chatFab").click()`); await sleep(1800);
  await snap(L, "2_임대인_대화창", "대화창: 문의 말풍선 + 제안 카드");
  check(await ev(L, `document.querySelector(".cm-card .cm-act[data-act=accept]") !== null`), "제안 카드에 수락 단추");
  await ev(L, `document.querySelector(".cm-card .cm-act[data-act=accept]").click()`); await sleep(2500);
  check(/수락됨/.test(await ev(L, `document.getElementById("cmList").innerText`)), "수락됨으로 표시");
  check(/280,000원/.test(await ev(L, `document.getElementById("cmList").innerText`)), "시스템 안내에 새 월세 기록");
  await snap(L, "3_수락후", "제안 수락 후");
  await ev(L, `document.querySelector(".r3-modal__x").click()`); await sleep(2600);
  check(/280,000원/.test(await ev(L, `document.querySelector(".term-grid").innerText`)), "3단계 화면의 월세가 280,000원으로 바뀜");
  check(await ev(L, `!document.getElementById("btnAgreeStep3").disabled`), "열린 제안이 없어 '전체 동의' 활성");

  // ── 임차인: 제안 창 · 열린 제안 ──
  await open(T, party.tenant, "tenant");
  await snap(T, "4_임차인_3단계", "임차인: 계약 조건 단계");
  check(Number(await ev(T, `document.querySelector(".chat-fab__n").textContent`)) >= 1, "임차인에게도 안 읽은 안내 배지(수락 안내)");
  await ev(T, `document.querySelector('.btn-propose[data-field="maintenance_fee"]').click()`); await sleep(800);
  await snap(T, "5_임차인_제안창", "관리비 제안 창");
  await ev(T, `(() => { document.getElementById("pfVal").value = "40000"; document.getElementById("pfSend").click(); return 1; })()`); await sleep(2800);
  check(/답할 제안·수정 요청 1건/.test(await turn(T)), "제안을 보내면 열린 요청 1건");
  check(await ev(T, `document.getElementById("btnAgreeStep3").disabled`), "임차인도 열린 요청이 있으면 동의 비활성");

  // ── 임대인: 거절하고 양쪽 동의 → 단계 ④ 에서 '조건 다시 열기' ──
  await open(L, party.lessor, "lessor");
  await ev(L, `document.getElementById("chatFab").click()`); await sleep(1800);
  await ev(L, `document.querySelector('.cm-card.is-open .cm-act[data-act=decline]').click()`); await sleep(2500);
  await ev(L, `document.querySelector(".r3-modal__x").click()`); await sleep(2600);
  await ev(L, `document.getElementById("btnAgreeStep3").click()`); await sleep(3500);
  check(/상대 차례/.test(await turn(L)), `임대인이 동의하면 상대 차례: ${await turn(L)}`);
  check((await ev(L, `document.querySelectorAll(".dotline .dot").length`)) === 2, "대기창에 점 두 개(배지 줄 대신)");
  await snap(L, "6_임대인_동의후", "임대인 동의 후 대기");
  await open(T, party.tenant, "tenant");
  await ev(T, `document.getElementById("btnAgreeStep3").click()`); await sleep(3500);
  const s4 = await api(party.lessor, "GET", `/sessions/${sid}`);
  check(s4.session.current_step === 4, `양측 동의 뒤 단계 ${s4.session.current_step}`);
  await open(L, party.lessor, "lessor");
  check(await ev(L, `!!document.getElementById("btnReopen")`), "서명 단계에 '조건 다시 열기'");
  await snap(L, "7_서명단계", "단계 ④ — 조건 다시 열기");
  await ev(L, `document.getElementById("btnReopen").click()`); await sleep(3500);
  const s3 = await api(party.lessor, "GET", `/sessions/${sid}`);
  check(s3.session.current_step === 3 && !s3.session.step3_lessor_agreed, "조건 다시 열기 → 3단계로 복귀·동의 초기화");
  console.log("콘솔 오류:", [...L.logs, ...T.logs].length ? [...L.logs, ...T.logs].join("\n") : "없음");
} finally { await L.close(); await T.close(); }
process.exit(bad ? 1 : 0);
