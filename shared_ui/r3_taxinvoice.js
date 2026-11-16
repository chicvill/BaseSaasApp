/* r3_taxinvoice.js — 전자세금계산서 '발급 정보 카드'(관리인·임대인 공용).
 *
 * 홈택스에서 직접 발급할 때 입력할 값을 항목별로 보여 주고, 항목마다 [복사]·[전체 복사]·[홈택스 열기]를 제공한다.
 * 앱은 국세청에 발행하지 않는다 — 공급자 본인이 홈택스(공동·금융인증서 로그인)에서 발급하고, 끝나면 승인번호를 앱에 기록한다.
 *
 *   R3Tax.openCard({
 *     title: "가맹비 2026-11 세금계산서",
 *     writeDate: "2026-11-16", purpose: "영수"|"청구", taxType: "과세",
 *     supplier: { business_number, company_name, ceo_name, address, biz_type, biz_item, email },
 *     buyer:    { …같은 항목… },
 *     items: [{ name, supply, tax }], note: "비고(선택)",
 *     missing: ["공급받는자 이메일", …],          // 비어 있는 필수값(경고로 표시)
 *     recordLabel: "✍️ 발급했어요 — 승인번호 기록", onRecord: function () {…}   // 선택
 *   })
 */
(function (global) {
  "use strict";

  var HOMETAX = "https://www.hometax.go.kr/";
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }
  function won(n) { return Number(n || 0).toLocaleString("ko-KR") + "원"; }
  function fmtBiz(v) { var d = String(v || "").replace(/\D/g, ""); return d.length === 10 ? d.slice(0, 3) + "-" + d.slice(3, 5) + "-" + d.slice(5) : String(v || ""); }

  function copy(text) {
    if (global.navigator && global.navigator.clipboard && global.isSecureContext) {
      return global.navigator.clipboard.writeText(text).then(function () { return true; }, function () { return legacy(text); });
    }
    return Promise.resolve(legacy(text));
  }
  function legacy(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
      document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
      var ok = document.execCommand("copy"); ta.remove(); return ok;
    } catch (e) { return false; }
  }

  // 현금영수증 값(식별번호·금액·구분)을 항목별 복사 행으로
  function cashRows(c, o) {
    var r = [];
    function add(l, v) { if (v !== "" && v != null) r.push({ g: "현금영수증", l: l, v: String(v) }); }
    add("발급 구분", c.kind); add(c.identifier_label || "식별번호", c.identifier); add("거래일자", c.date); add("금액", Number(c.amount || 0));
    add("품목", c.item || (o.items && o.items[0] && o.items[0].name)); add("이메일(선택)", c.email);
    return r;
  }

  function openCard(o) {
    if (o.cash && o.taxAvailable !== false && !o.cashOnly) return openTabs(o);
    return render(o, o.cash && (o.taxAvailable === false || o.cashOnly) ? "cash" : "tax");
  }
  function openTabs(o) { return render(o, "tax"); }

  function render(o, mode) {
    var sup = o.supplier || {}, buy = o.buyer || {}, items = o.items || [];
    var supply = items.reduce(function (a, x) { return a + Number(x.supply || 0); }, 0);
    var tax = items.reduce(function (a, x) { return a + Number(x.tax || 0); }, 0);
    var rows = []; // {group, label, value}
    function add(group, label, value) { if (value !== "" && value != null) rows.push({ g: group, l: label, v: String(value) }); }
    add("기본", "작성일자", o.writeDate); add("기본", "종류", o.taxType || "과세"); add("기본", "영수/청구", o.purpose || "영수");
    add("공급자", "사업자등록번호", fmtBiz(sup.business_number)); add("공급자", "상호", sup.company_name); add("공급자", "대표자", sup.ceo_name);
    add("공급자", "사업장 주소", sup.address); add("공급자", "업태", sup.biz_type); add("공급자", "종목", sup.biz_item); add("공급자", "이메일", sup.email);
    add("공급받는자", "사업자등록번호", fmtBiz(buy.business_number)); add("공급받는자", "상호", buy.company_name); add("공급받는자", "대표자", buy.ceo_name);
    add("공급받는자", "사업장 주소", buy.address); add("공급받는자", "업태", buy.biz_type); add("공급받는자", "종목", buy.biz_item); add("공급받는자", "이메일(수신)", buy.email);
    items.forEach(function (x, i) {
      var p = items.length > 1 ? "품목 " + (i + 1) + " " : "품목 ";
      add("품목", p + "이름", x.name); add("품목", p + "공급가액", Number(x.supply || 0)); add("품목", p + "세액", Number(x.tax || 0));
    });
    add("품목", "합계금액", supply + tax); add("비고", "비고", o.note);
    if (mode === "cash") rows = cashRows(o.cash, o);

    var groups = []; rows.forEach(function (r) { if (groups.indexOf(r.g) < 0) groups.push(r.g); });
    var plain = o.title + "\n" + groups.map(function (g) {
      return "[" + g + "]\n" + rows.filter(function (r) { return r.g === g; }).map(function (r) { return r.l + ": " + r.v; }).join("\n");
    }).join("\n");

    var ov = document.createElement("div");
    ov.setAttribute("data-r3tax", "1");
    ov.style.cssText = "position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;justify-content:center;";
    var missing = (o.missing || []).length
      ? '<div style="border-left:4px solid #f59e0b;background:rgba(245,158,11,.12);padding:8px 10px;border-radius:8px;margin-bottom:10px;font-size:13px;line-height:1.6;">⚠️ <strong>비어 있는 필수 정보</strong>: ' + esc(o.missing.join(", ")) + "<br>홈택스에서 발급하려면 먼저 채워 주세요.</div>" : "";
    ov.innerHTML = '<div style="width:100%;max-width:600px;max-height:92vh;overflow-y:auto;background:#17172e;color:#eceaf7;border:1px solid #334;border-radius:16px 16px 0 0;padding:14px 14px 22px;">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;"><strong style="font-size:15px;">🧾 ' + esc(o.title) + '</strong><button type="button" data-x style="min-width:44px;min-height:44px;font-size:18px;">✕</button></div>' +
      (o.cash ? '<div style="display:flex;gap:6px;margin-bottom:8px;"><button type="button" data-mode="tax"' + (o.taxAvailable === false ? " disabled" : "") + ' style="flex:1;min-height:44px;font-weight:' + (mode === "tax" ? "800" : "400") + ';border:' + (mode === "tax" ? "2px solid #6366f1" : "1px solid #334") + ';">🧾 세금계산서</button><button type="button" data-mode="cash" style="flex:1;min-height:44px;font-weight:' + (mode === "cash" ? "800" : "400") + ';border:' + (mode === "cash" ? "2px solid #6366f1" : "1px solid #334") + ';">💵 현금영수증</button></div>' : "") +
      (mode === "cash" && o.taxAvailable === false ? '<div style="border-left:4px solid #6366f1;background:rgba(99,102,241,.12);padding:8px 10px;border-radius:8px;margin-bottom:10px;font-size:13px;line-height:1.6;">ℹ️ <strong>사업자등록번호가 없어 세금계산서는 발급할 수 없습니다.</strong> 현금영수증(소득공제용)으로 발급합니다.</div>' : "") +
      (mode === "cash"
        ? '<ol style="margin:0 0 10px 18px;padding:0;font-size:13px;line-height:1.7;"><li>아래 값을 항목별로 복사하거나 전체 복사합니다.</li><li><strong>홈택스 열기</strong> → 공급자 본인 인증서로 로그인 → <em>현금영수증 발급</em> 메뉴에 식별번호·금액을 입력합니다.</li><li>발급이 끝나면 <strong>현금영수증 승인번호</strong>를 앱에 기록합니다.</li></ol>'
        : '<ol style="margin:0 0 10px 18px;padding:0;font-size:13px;line-height:1.7;"><li>아래 값을 <strong>항목별로 복사</strong>하거나 <strong>전체 복사</strong>합니다.</li><li><strong>홈택스 열기</strong> → 공급자 본인 인증서로 로그인 → <em>전자(세금)계산서 발급(건별)</em> 메뉴에 붙여 넣습니다.</li><li>발급이 끝나면 <strong>국세청 승인번호</strong>를 앱에 기록합니다.</li></ol>') +
      (mode === "cash" ? "" : missing) +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;"><button type="button" data-all style="min-height:46px;padding:0 14px;font-weight:700;">📋 전체 복사</button>' +
      '<a href="' + HOMETAX + '" target="_blank" rel="noopener" data-ht style="display:inline-flex;align-items:center;min-height:46px;padding:0 14px;font-weight:700;border:1px solid #6366f1;border-radius:8px;color:#c7d2fe;text-decoration:none;">🏛️ 홈택스 열기 ↗</a>' +
      (o.onRecord ? '<button type="button" data-rec style="min-height:46px;padding:0 14px;font-weight:700;">' + esc(mode === "cash" ? "✍️ 현금영수증 발급했어요 — 승인번호 기록" : (o.recordLabel || "✍️ 발급했어요 — 승인번호 기록")) + "</button>" : "") + "</div>" +
      '<div data-msg style="min-height:18px;font-size:12.5px;color:#86efac;margin-bottom:4px;"></div>' +
      groups.map(function (g) {
        return '<div style="font-weight:800;margin:10px 0 4px;font-size:13px;opacity:.85;">' + esc(g) + "</div>" + rows.filter(function (r) { return r.g === g; }).map(function (r, i) {
          return '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.2);"><span style="flex:0 0 34%;font-size:12.5px;opacity:.75;">' + esc(r.l) + '</span><span style="flex:1;min-width:0;font-size:14px;word-break:break-all;"><strong>' + esc(/공급가액|세액|합계/.test(r.l) ? won(r.v) : r.v) + '</strong></span><button type="button" data-copy="' + esc(/공급가액|세액|합계금액/.test(r.l) ? r.v : r.v) + '" style="min-width:56px;min-height:40px;">복사</button></div>';
        }).join("");
      }).join("") +
      '<div style="font-size:12px;opacity:.7;margin-top:10px;line-height:1.6;">숫자 항목은 쉼표 없이 복사됩니다. 앱은 국세청에 직접 발행하지 않으며, 발급 책임은 공급자 본인에게 있습니다.</div></div>';
    document.body.appendChild(ov);
    var msg = ov.querySelector("[data-msg]");
    function say(t) { msg.textContent = t; setTimeout(function () { if (msg.textContent === t) msg.textContent = ""; }, 2500); }
    function close() { ov.remove(); }
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.closest("[data-x]")) { close(); return; }
      var md = e.target.closest("[data-mode]");
      if (md && !md.disabled) { close(); render(o, md.getAttribute("data-mode")); return; }
      var one = e.target.closest("[data-copy]");
      if (one) { copy(one.getAttribute("data-copy")).then(function (ok) { say(ok ? "복사했습니다" : "복사하지 못했습니다 — 값을 길게 눌러 직접 복사해 주세요"); }); return; }
      if (e.target.closest("[data-all]")) { copy(plain).then(function (ok) { say(ok ? "전체를 복사했습니다" : "복사하지 못했습니다"); }); return; }
      if (e.target.closest("[data-rec]")) { close(); o.onRecord(mode); }
    });
    return ov;
  }

  global.R3Tax = { openCard: openCard, HOMETAX: HOMETAX };
})(window);
