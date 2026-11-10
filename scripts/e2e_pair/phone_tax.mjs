// 더보기 ▸ 부가세·종소세·경비 화면을 스마트폰 크기로 찍는다.
//   node scripts/e2e_pair/phone_tax.mjs data/private/fresh.json   (serve_pool 로 9080 에 서버를 띄운 뒤)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.PHONE_OUT = process.env.PHONE_OUT || path.join(ROOT, "e2e_runtime", "phone_tax");
process.env.E2E_BASE = process.env.E2E_BASE || "http://127.0.0.1:9080";
const { launch, snap, asUser, loginUser, sleep } = await import("./phone_ui.mjs");
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const ls = cfg.lessors.find((x) => x.username === "chic") || cfg.lessors[0];
const A = await launch("tx", 9613);
try {
  const res = await loginUser(ls.username, ls.password);
  await asUser(A, res.user, res.token, "/rent3_lessor/?tab=tax"); await sleep(4000);
  console.log(await A.eval(`Bass.api("rent3_lessor","/tax/summary?branch=main&year=2026").then((d)=>"ok "+Object.keys(d).join(",")).catch((e)=>"ERR "+e.message)`)); await snap(A, "1_세금탭", "부가세·종소세·경비");
  await A.eval(`(() => { const b = document.querySelector(".outlay-row"); if (b) b.click(); return 1; })()`); await sleep(700);
  await snap(A, "2_경비수정", "경비 수정 창");
  console.log("콘솔 오류:", A.logs.length ? A.logs.join("\n") : "없음");
} finally { await A.close(); }
