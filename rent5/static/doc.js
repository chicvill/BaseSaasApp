/* rent5/static/doc.js — 두 사람이 서명한 계약서 (인쇄 · PDF 저장). 서명한 그 내용 그대로 보여 준다. */
(async function () {
  "use strict";
  const { esc, won, when } = R5;
  const $ = (id) => document.getElementById(id);
  const id = new URLSearchParams(location.search).get("id") || "";

  const user = await R5.requireLogin();
  R5.nav(user);
  $("backLink").href = `contract.html?id=${encodeURIComponent(id)}`;
  $("btnPrint").addEventListener("click", () => window.print());
  if (!id) return R5.notify("warn", "계약을 골라 주세요.");

  try {
    const { document: d } = await R5.api(`/contracts/${encodeURIComponent(id)}/document`);
    const s = d.snapshot, t = s.terms, r = s.registry, i = s.tenant_info;
    if (s.version === 2) {
      const tm = s.times, a = s.account;
      $("doc").innerHTML = `
      <h1>주택 임대차 계약금 송금 확인서 (직거래)</h1>
      <table>
        <tr><th>임대인</th><td>${esc(s.landlord.name)} · ${esc(s.landlord.phone)}</td></tr>
        <tr><th>임차인</th><td>${esc(s.tenant.name)} · ${esc(s.tenant.phone)}</td></tr>
      </table>
      <table>
        <tr><th>목적물 주소</th><td>${esc(t.address)}</td></tr>
        <tr><th>구분</th><td>${esc(t.lease_type)}</td></tr>
        <tr><th>보증금</th><td>${won(t.deposit)} (계약금 ${won(t.down_payment)} · 잔금 ${won(t.deposit - t.down_payment)})</td></tr>
        ${t.lease_type === "전세" ? "" : `<tr><th>월세 · 관리비</th><td>${won(t.rent)} + ${won(t.mgmt)}</td></tr>`}
        <tr><th>입주 · 기간</th><td>${t.start ? `${esc(t.start)} ~ ${esc(t.end)} (${t.months}개월)` : "협의"}</td></tr>
      </table>
      <table>
        <tr><th>등기부 열람</th><td>${esc(r.checked_on)} 임대인이 임차인에게 대면으로 보여 줌 — 소유자 본인 일치, 압류·가압류 없음, 선순위 근저당 채권최고액 ${won(r.mortgage)}${r.note ? `<br>${esc(r.note)}` : ""}</td></tr>
        <tr><th>계약금 계좌</th><td>${esc(a.bank)} ${esc(a.number)} · 예금주 ${esc(a.holder)} · ${won(a.amount)}</td></tr>
      </table>
      <table>
        <tr><th>정보 확인</th><td>임차인이 임대인 · 매물 정보 확인 ${esc(when(tm.landlord_info_ok_at))}<br>임대인이 임차인 정보 확인 ${esc(when(tm.tenant_info_ok_at))}</td></tr>
        <tr><th>등기부 확인</th><td>열람 ${esc(when(tm.registry_shown_at))} → 임차인 확인 ${esc(when(tm.registry_ok_at))}</td></tr>
        <tr><th>계약금</th><td>계좌 안내 ${esc(when(tm.account_sent_at))} → 임차인 송금 완료 ${esc(when(tm.paid_at))}</td></tr>
      </table>
      <p class="hash">문서 지문(SHA-256): ${esc(d.final_hash)}<br>이 기록으로 계산한 값입니다. 내용이 한 글자라도 바뀌면 달라집니다.</p>
      ${d.report_required ? `<p>※ 보증금 6천만 원 또는 월세 30만 원을 넘는 계약입니다. 계약일부터 30일 안에 주택 임대차 계약 신고를 해야 합니다.</p>` : ""}
      <p class="small">이 문서는 두 사람이 온라인에서 확인한 순서의 기록입니다. 송금은 임차인이 알린 내용이며 은행 거래 내역이 아닙니다. 중개사가 관여하지 않았고, 법률 자문이 아닙니다.</p>`;
      $("doc").hidden = false;
      return;
    }
    $("doc").innerHTML = `
      <h1>주택 임대차 계약서 (직거래)</h1>
      <table>
        <tr><th>임대인</th><td>${esc(s.landlord.name)} · ${esc(s.landlord.phone)}</td></tr>
        <tr><th>임차인</th><td>${esc(s.tenant.name)} · ${esc(s.tenant.phone)}<br>생년월일 ${esc(i.birth)} · 현재 주소 ${esc(i.address)}<br>비상 연락처 ${esc(i.emergency)}</td></tr>
      </table>
      <table>
        <tr><th>목적물 주소</th><td>${esc(t.address)}</td></tr>
        <tr><th>구분</th><td>${esc(t.lease_type)}</td></tr>
        <tr><th>보증금</th><td>${won(t.deposit)} (계약금 ${won(t.down_payment)} · 잔금 ${won(t.deposit - t.down_payment)})</td></tr>
        ${t.lease_type === "전세" ? "" : `<tr><th>월세 · 관리비</th><td>${won(t.rent)} + ${won(t.mgmt)}</td></tr>`}
        <tr><th>계약 기간</th><td>${esc(t.start)} ~ ${esc(t.end)} (${t.months}개월)</td></tr>
      </table>
      <table>
        <tr><th>등기부 확인</th><td>${esc(r.checked_on)} 임대인이 확인 — 소유자 일치, 압류·가압류 없음, 근저당 채권최고액 ${won(r.mortgage)}${r.note ? `<br>${esc(r.note)}` : ""}</td></tr>
        <tr><th>특약</th><td>없음 (이 화면에 적힌 조건이 전부입니다)</td></tr>
      </table>
      <div class="sign">
        <div><strong>임차인 ${esc(s.tenant.name)}</strong><br>전자 서명 ${esc(when(d.tenant_signed_at))}</div>
        <div><strong>임대인 ${esc(s.landlord.name)}</strong><br>전자 서명 ${esc(when(d.signed_at))}</div>
      </div>
      <p>${d.deposit_confirmed_at ? `계약금 입금 확인 ${esc(when(d.deposit_confirmed_at))}` : "계약금 입금 확인 전"}
        · ${d.moved_in_at ? `잔금 입금 확인 · 입주 ${esc(when(d.moved_in_at))}` : "잔금 입금 확인 전"}</p>
      <p class="hash">문서 지문(SHA-256): ${esc(d.final_hash)}<br>두 사람이 서명한 내용으로 계산한 값입니다. 내용이 한 글자라도 바뀌면 달라집니다.</p>
      ${d.report_required ? `<p>※ 보증금 6천만 원 또는 월세 30만 원을 넘는 계약입니다. 계약일부터 30일 안에 주택 임대차 계약 신고를 해야 합니다.</p>` : ""}
      <p class="small">이 문서는 두 당사자가 온라인에서 합의한 내용의 기록입니다. 중개사가 관여하지 않았고, 법률 자문이 아닙니다.</p>`;
    $("doc").hidden = false;
  } catch (err) {
    R5.fail(err);
  }
})();
