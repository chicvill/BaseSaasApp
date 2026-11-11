// 임대인 앱 세무 허브(종합소득세 / 부가세 / 세금계산서 / 경비·면세 신고)를 스마트폰 크기로 확인한다.
//   py scripts/serve_pool.py --pool-dir <시드 풀> --port 9092 --admin-out data/private/admin.json
//   E2E_BASE=http://127.0.0.1:9092 node scripts/e2e_pair/phone_taxhub.mjs data/private/admin.json main
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_taxhub");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9092";
const { launch, snap, asUser, sleep } = await import("./phone_ui.mjs");
const admin = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const branch = process.argv[3] || "main";
const A = await launch("th", 9618);
let bad = 0;
const check = (ok, msg) => { console.log(`${ok ? "✅" : "❌"} ${msg}`); if (!ok) bad++; };
const has = (sel) => A.eval(`!!document.querySelector(${JSON.stringify(sel)})`);
const txt = (s) => A.eval(`document.getElementById("tabContent").innerText.includes(${JSON.stringify(s)})`);
try {
  await asUser(A, admin.user, admin.token, `/rent3_lessor/?branch=${branch}&tab=tax`); await sleep(4500);
  await snap(A, "1_세무허브", "세무 허브");
  check((await A.eval(`document.querySelectorAll(".tax-hub-btn").length`)) === 4, "허브에 큰 버튼 4개");
  check(await txt("참고용"), "참고용 추정치 안내 표시");
  const steps = [
    ["income", "2_종합소득세", [["#btnDlTaxCsv", "연간 세무 CSV 버튼"]], ["종합소득세 자료", "분리과세", "누진세율"]],
    ["vat", "3_부가세", [["#btnGoBldgTax", "대상 호실 설정 바로가기"]], ["부가가치세 자료", "매입세액"]],
    ["invoice", "4_세금계산서", [["#taxMonthSel", "발급 월 선택"], ["#btnDlHometaxCsv", "홈택스 CSV 버튼"], ["#btnDlHometaxCsv2", "카드 안 홈택스 CSV 버튼"]], ["세금계산서 발급 리스트", "청구월(약정 지급일)"]],
    ["expense", "5_경비면세", [["#btnDlExemptCsv", "면세 사업장현황신고 CSV"], ["#btnAddOutlay", "경비 등록 저장"], ["#outEvid", "증빙유형"], ["#outScope", "과세·면세 사용구분"]], ["사업장현황신고", "경비 내역"]],
  ];
  for (const [view, name, sels, texts] of steps) {
    await A.eval(`document.querySelector('.tax-hub-btn[data-v="${view}"]').click()`); await sleep(900);
    for (const [sel, label] of sels) check(await has(sel), `${view}: ${label}`);
    for (const t of texts) check(await txt(t), `${view}: '${t}' 표시`);
    await snap(A, name, name);
    await A.eval(`document.getElementById("btnTaxHub").click()`); await sleep(500);
    check((await A.eval(`document.querySelectorAll(".tax-hub-btn").length`)) === 4, `${view}: ← 세무 허브로 돌아옴`);
  }
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
process.exit(bad ? 1 : 0);
