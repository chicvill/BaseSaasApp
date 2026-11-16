// 임대인 호실 카드(풀다운 + 수납내역·계약내용·만기통보·전출 모달)를 스마트폰 크기로 찍는다 — 화면 확인용.
//   py scripts/serve_pool.py --pool-dir data/private/pool_t --port 9080
//   node scripts/e2e_pair/phone_roomcard.mjs data/private/fresh.json [시크빌호실=402]
// 결과: e2e_runtime/phone_roomcard/*.png (저장소에 올리지 않는다)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_roomcard");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9080";
const { launch, snap, asUser, loginUser, sleep } = await import("./phone_ui.mjs");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const room = process.argv[3] || "402";
const ls = cfg.lessors.find((x) => x.username === "chic") || cfg.lessors[0];
const A = await launch("rc", 9612);
const click = (sel, text) => A.eval(`(() => { const els = Array.from(document.querySelectorAll(${JSON.stringify(sel)})); const b = els.find((x) => !${JSON.stringify(text || "")} || x.innerText.includes(${JSON.stringify(text || "")})); if (b) b.click(); return !!b; })()`);
try {
  const res = await loginUser(ls.username, ls.password);
  await asUser(A, res.user, res.token, "/rent3_lessor/"); await sleep(3500);
  await A.eval(`(() => { const s = document.getElementById("roomPick"); s.value = ${JSON.stringify(room)}; s.dispatchEvent(new Event("change", { bubbles: true })); return 1; })()`);
  await sleep(600);
  await snap(A, "1_호실카드", `${room}호 선택`);
  for (const [act, name] of [["contract", "3_계약내용"], ["expiry", "4_만기통보"], ["moveout", "5_전출"]]) {
    await click(`[data-act="${act}"]`); await sleep(900);
    await snap(A, name, act);
    await A.eval(`document.querySelector(".r3-modal__x") && document.querySelector(".r3-modal__x").click()`); await sleep(300);
  }
  console.log("\n콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally {
  await A.close();
}
