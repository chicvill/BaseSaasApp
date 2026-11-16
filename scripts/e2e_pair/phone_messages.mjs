// 메시지(7단계): 임대인 ▸ 메시지(호실 목록·대화) ↔ 임차인 ▸ 임대인과 메시지, 계약방·지원 채팅과 섞이지 않는지 스마트폰 크기로 확인한다.
//   BASS_SECRET=x py scripts/make_contract_room.py --pool-dir data/private/pool_c --out data/private/party.json
//   BASS_SECRET=x py scripts/serve_pool.py --pool-dir data/private/pool_c --port 9096 --tokens-out data/private/tokens.json --tenants tenant_201
//   E2E_BASE=http://127.0.0.1:9096 node scripts/e2e_pair/phone_messages.mjs data/private/party.json data/private/tokens.json
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_messages");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9096";
const { launch, snap, asUser, sleep } = await import("./phone_ui.mjs");
const party = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const toks = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
const tid = Object.keys(toks)[0];
const L = await launch("ml", 9621);
const T = await launch("mt", 9622);
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const ev = (D, c) => D.eval(c);
try {
  await asUser(L, party.lessor.user, party.lessor.token, "/rent3_lessor/?tab=messages"); await sleep(4000);
  await snap(L, "1_임대인_메시지목록", "임대인 ▸ 메시지 목록");
  check((await ev(L, `document.querySelectorAll(".msg-room").length`)) >= 1, "호실 목록이 보임");
  check(await ev(L, `!!document.querySelector('.r3-drawer .tab-btn[data-tab="messages"]')`), "메뉴에 '메시지'");
  check(await ev(L, `!document.getElementById("btnSendChat")`), "A/S 화면에서 옛 메신저는 사라짐");
  await ev(L, `document.querySelector('.msg-room[data-room="201"]').click()`); await sleep(2000);
  await ev(L, `(() => { document.getElementById("msgInput").value = "이번 달 관리비 안내드립니다"; document.getElementById("btnMsgSend").click(); return 1; })()`); await sleep(2500);
  check(/이번 달 관리비 안내드립니다/.test(await ev(L, `document.getElementById("msgList").innerText`)), "임대인이 보낸 글이 대화에 보임");
  await snap(L, "2_임대인_대화", "임대인 ▸ 201호 대화");
  await ev(L, `document.getElementById("btnMsgBack").click()`); await sleep(1500);   // 대화를 열어 둔 채로 두면 새 글이 자동으로 읽음 처리된다

  await asUser(T, toks[tid].user, toks[tid].token, "/rent3_tenant/?tenant=" + tid); await sleep(4500);
  check(/새 메시지 1/.test(await ev(T, `document.body.innerText`)), "임차인 첫 화면에 '새 메시지 1' 배지");
  check(/임대인과 메시지 \(1\)/.test(await ev(T, `document.getElementById("btnSvcMsg").textContent`)), "메뉴 항목에도 안 읽은 수");
  await snap(T, "3_임차인_첫화면", "임차인 첫 화면(할 일)");
  await ev(T, `document.getElementById("btnSvcMsg").click()`); await sleep(2500);
  const txt = await ev(T, `document.getElementById("tnMsgList").innerText`);
  check(/이번 달 관리비 안내드립니다/.test(txt), "임차인이 임대인 글을 봄");
  check(!/지원 채팅|관리인이 임차인 입장/.test(txt), "관리인 지원 채팅은 섞이지 않음");
  await ev(T, `(() => { document.getElementById("tnMsgInput").value = "네 확인했습니다"; document.getElementById("btnTnMsgSend").click(); return 1; })()`); await sleep(2500);
  await snap(T, "4_임차인_대화", "임차인 ▸ 임대인과 메시지");
  check(/네 확인했습니다/.test(await ev(T, `document.getElementById("tnMsgList").innerText`)), "임차인이 보낸 글이 대화에 보임");
  await asUser(L, party.lessor.user, party.lessor.token, "/rent3_lessor/?tab=messages"); await sleep(3500);
  check(/안 읽음/.test(await ev(L, `document.querySelector('.msg-room[data-room="201"]').innerText`)), "임대인 목록에 안 읽음 표시");
  console.log("콘솔 오류:", [...L.logs, ...T.logs].length ? [...L.logs, ...T.logs].join("\n") : "없음");
} finally { await L.close(); await T.close(); }
process.exit(bad ? 1 : 0);
