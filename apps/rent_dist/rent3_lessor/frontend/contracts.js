/* apps/rent_dist/rent3_lessor/frontend/contracts.js — 계약내용·신규/연장·만기통보·전출정산·입주안내·중개계약서. */
(function () {
  "use strict";
  const L = window.R3L;
  if (!L) return;

  const {
    APP_ID, elTabContent, state: S,
    fmtWon, expiryStatusLabel, roomOf, dotDate, bldgName, $m,
    downloadWithAuth, copyText, copyAndSms, openModal, closeModal,
  } = L;

  // 📵 분실 폰: 그 임차인의 간편 비밀번호·바로가기를 모두 해제
  function revokeShortcuts(no) {
    if (!window.confirm(`${no}호 임차인의 간편 비밀번호와 바로가기를 모두 해제할까요? (비밀번호 로그인은 그대로 됩니다)`)) return;
    Bass.withLock(null, async () => {
      const r = await Bass.api(APP_ID, "/tenant-shortcuts/revoke-all", { method: "POST", body: { branch: S.currentBranch, room_no: no } });
      Bass.updateAppAlert("success", r.message);
    });
  }

  // 📲 입주 안내 카드: 1회용 링크(QR). 임차인이 비밀번호·간편 비밀번호를 정하고 홈 화면에 추가하도록 안내한다.
  function showInviteModal(no) {
    Bass.withLock(null, async () => {
      const d = await Bass.api(APP_ID, "/tenant-invite", { method: "POST", body: { branch: S.currentBranch, room_no: no } });
      const link = window.location.origin + d.url;
      let svg = "";
      try { const qr = qrcode(0, "M"); qr.addData(link); qr.make(); svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); } catch (_) { svg = ""; }
      const msg = `[${d.building_name} ${d.room_no}호] ${d.tenant_name}님, 입주를 환영합니다. 아래 링크에서 비밀번호를 정하면 바로 시작할 수 있습니다(7일 안, 1회).\n${link}`;
      const body = openModal(`📲 ${Bass.escapeHtml(no)}호 입주 안내 카드`, `
        <div style="text-align:center;"><div style="max-width:220px;margin:0 auto;background:#fff;padding:6px;border-radius:8px;">${svg || "QR 을 만들지 못했습니다. 아래 링크를 보내 주세요."}</div></div>
        <div class="bass-muted" style="font-size:12.5px;margin:8px 0;">${Bass.escapeHtml(d.tenant_name)}님에게 QR 을 보여 주거나 문자로 보내세요. <strong>7일 안에 한 번</strong> 쓸 수 있고, 다시 만들면 이전 링크는 못 씁니다.</div>
        <textarea id="invMsg" class="bass-input r3-msg" rows="5" readonly>${Bass.escapeHtml(msg)}</textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">
          <button id="invCopy" class="bass-btn bass-btn--primary" type="button" style="min-height:44px;">문구 복사</button>
          <button id="invPrint" class="bass-btn" type="button" style="min-height:44px;">🖨 인쇄</button></div>`);
      body.querySelector("#invCopy").addEventListener("click", () => copyText(msg));
      body.querySelector("#invPrint").addEventListener("click", () => {
        const w = window.open("", "_blank");
        if (!w) return;
        w.document.write(`<title>입주 안내 카드</title><body style="font-family:sans-serif;text-align:center;padding:30px;"><h2>${Bass.escapeHtml(d.building_name)} ${Bass.escapeHtml(d.room_no)}호 입주 안내</h2><p>${Bass.escapeHtml(d.tenant_name)}님, 입주를 환영합니다.</p><div style="width:260px;margin:0 auto;">${svg}</div><p>QR 을 카메라로 찍어 비밀번호를 정해 주세요.<br>(7일 안, 1회 사용)</p><p style="font-size:12px;word-break:break-all;">${Bass.escapeHtml(link)}</p></body>`);
        w.document.close(); w.focus(); w.print();
      });
    }).catch(Bass.showError);
  }

  // ② 계약내용: 보기·고치기 + 계약서 사진 + 신규/연장 계약(다음 계약 조건 저장) + 도어락 교부
  function showContractModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct) return;
    const hasPhoto = Boolean(ct.contract_photo_file);
    const bizNo = ct.tenant_business_number || (r.tenant && r.tenant.business_number) || "";
    const compName = ct.tenant_company_name || (r.tenant && r.tenant.company_name) || "";
    const ceoName = ct.tenant_ceo_name || (r.tenant && r.tenant.ceo_name) || "";
    const bizEmail = ct.tenant_email || (r.tenant && r.tenant.email) || "";
    const bizAddr = ct.tenant_business_address || (r.tenant && r.tenant.business_address) || "";
    const taxReq = Boolean(ct.tax_invoice_requested || bizNo || compName);
    const f = (id, label, val, type, ph) => `<label class="r3-fld">${label}<input id="${id}" class="bass-input" type="${type || "text"}" value="${Bass.escapeHtml(String(val ?? ""))}" ${ph ? `placeholder="${Bass.escapeHtml(ph)}"` : ""}></label>`;
    openModal(`📄 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 계약내용`, `
      <div class="r3-grid">
        ${f("cmIn", "전입일", ct.move_in_date, "date")}
        ${f("cmEx", "만기일", ct.expiry_date, "date")}
        ${f("cmDep", "보증금(원)", ct.deposit, "number")}
        ${f("cmRent", "월세(원)", ct.monthly_rent, "number")}
        ${f("cmFee", "관리비(원)", ct.maintenance_fee, "number")}
        ${f("cmClean", "퇴실 청소비 약정(원 · 0 = 약정 없음)", ct.cleaning_fee || 0, "number")}
        ${f("cmTel", "임차인 전화번호", ct.tenant_phone || (r.tenant && r.tenant.phone) || "", "tel")}
        ${f("cmPayers", "송금자명 (임차인 이름과 다르게 입금될 때 · 쉼표로 구분, 최대 3개)", (ct.payer_names || []).join(", "), "text", "예: 홍길순, (주)시크디자인")}
      </div>
      <div class="bass-muted" style="font-size:12.5px;margin-top:4px;">입금 문자의 이름이 임차인 이름 <strong>또는 송금자명</strong>과 같으면 이 호실로 수납됩니다. 보증금 반환은 계약자(임차인) 명의로 합니다.</div>
      <div class="bass-card" style="margin-top:10px;padding:10px;background:var(--surface-2, #f8fafc);">
        <label style="display:flex;align-items:center;gap:6px;font-weight:700;font-size:13.5px;cursor:pointer;">
          <input id="cmTaxInvReq" type="checkbox" ${taxReq ? "checked" : ""}>
          🧾 세입자가 세금계산서 발행을 요청함 (사무실·법인 기숙사 등 모든 호실 가능)
        </label>
        <div id="cmTaxBizWrap" class="r3-grid" style="display:${taxReq ? "grid" : "none"};margin-top:8px;">
          ${f("cmBizNo", "사업자등록번호", bizNo, "text", "예: 214-86-12345")}
          ${f("cmComp", "상호(법인명)", compName, "text", "예: (주)시크디자인")}
          ${f("cmCeo", "대표자명 (필수·임차인명 자동대체 안함)", ceoName, "text", "예: 홍길동")}
          ${f("cmEmail", "세금계산서 수신 이메일", bizEmail, "email", "예: tax@company.co.kr")}
          ${f("cmBizAddr", "사업자등록상 주소 (미입력 시 호실 주소)", bizAddr, "text", "법인 본점 등 주소가 다를 때 입력")}
        </div>
      </div>
      <label class="r3-fld" style="margin-top:8px;">메모<textarea id="cmMemo" class="bass-input" rows="3">${Bass.escapeHtml(ct.lessor_memo || "")}</textarea></label>
      <div class="bass-muted" style="font-size:12.5px;margin:8px 0;">
        계약일 ${Bass.escapeHtml(ct.contract_date || "-")} · ${Bass.escapeHtml(ct.lease_type || "")} · ${Bass.escapeHtml(ct.pay_timing || "")}
        ${ct.fixed_date_no ? ` · 접수번호 <code>${Bass.escapeHtml(ct.fixed_date_no)}</code>` : " · 전월세신고 확인 필요"}
      </div>
      ${r.next_terms && Object.keys(r.next_terms).length && !r.reservation ? `<div class="bass-card" style="padding:8px 10px;margin:8px 0;font-size:12.5px;border-color:var(--primary);background:rgba(37,99,235,.06);">
        <strong>📝 저장된 다음 계약 조건</strong>: 보증금 ${fmtWon(r.next_terms.deposit != null ? r.next_terms.deposit : ct.deposit)} · 월세 ${fmtWon(r.next_terms.monthly_rent != null ? r.next_terms.monthly_rent : ct.monthly_rent)} · 관리비 ${fmtWon(r.next_terms.maintenance_fee != null ? r.next_terms.maintenance_fee : ct.maintenance_fee)} · 청소비 ${fmtWon(r.next_terms.cleaning_fee != null ? r.next_terms.cleaning_fee : (ct.cleaning_fee || 0))} · ${Bass.escapeHtml(r.next_terms.pay_timing || ct.pay_timing || "후불")}
        <div class="bass-muted" style="font-size:12px;margin-top:2px;">신규 임차인 입주 또는 연장 적용 순간 현재 계약 조건을 대체합니다.</div>
      </div>` : ""}
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;">
        <button id="cmSave" class="bass-btn bass-btn--sm bass-btn--primary" type="button">💾 저장</button>
        ${hasPhoto ? `<button id="cmViewPhoto" class="bass-btn bass-btn--sm" type="button">📸 계약서 사진 보기</button>` : ""}
        <button id="cmPhoto" class="bass-btn bass-btn--sm" type="button">📸 계약서 사진 ${hasPhoto ? "다시 " : ""}등록</button>
        <button id="cmNewContract" class="bass-btn bass-btn--sm" type="button">🤝 신규 계약</button>
        <button id="cmRenew" class="bass-btn bass-btn--sm" type="button">🔁 연장 계약</button>
      </div>
      ${ct.keys_released === false ? `<div class="bass-card" style="margin-top:12px;padding:10px;">
        <div style="font-size:13px;margin-bottom:6px;">🔑 잔금 입금 확인 전입니다. 내 은행 앱에서 직접 확인한 뒤 도어락 정보를 열어 주세요.</div>
        <button id="cmKeys" class="bass-btn bass-btn--sm bass-btn--primary" type="button">🔑 잔금 확인 & 도어락 교부</button></div>` : ""}`);

    $m("cmTaxInvReq").addEventListener("change", (ev) => {
      $m("cmTaxBizWrap").style.display = ev.target.checked ? "grid" : "none";
    });
    const vp = $m("cmViewPhoto");
    if (vp) {
      vp.addEventListener("click", () => {
        downloadWithAuth(`/contracts/${encodeURIComponent(ct.id)}/photo`, `contract_${ct.room_no}.png`, true);
      });
    }
    const saveContractEdits = async () => {
      const reqInv = Boolean($m("cmTaxInvReq").checked);
      return await Bass.api(APP_ID, "/contracts/edit", {
        method: "POST",
        body: {
          branch: S.currentBranch, contract_id: ct.id,
          deposit: Number($m("cmDep").value || 0), monthly_rent: Number($m("cmRent").value || 0), maintenance_fee: Number($m("cmFee").value || 0), cleaning_fee: Number($m("cmClean").value || 0),
          move_in_date: $m("cmIn").value, expiry_date: $m("cmEx").value, tenant_phone: $m("cmTel").value, payer_names: $m("cmPayers").value.split(/[,，、]/).map((x) => x.trim()).filter(Boolean), memo: $m("cmMemo").value,
          tax_invoice_requested: reqInv,
          vat_target: reqInv,
          tenant_business_number: reqInv ? $m("cmBizNo").value.trim() : "",
          tenant_company_name: reqInv ? $m("cmComp").value.trim() : "",
          tenant_ceo_name: reqInv ? $m("cmCeo").value.trim() : "",
          tenant_email: reqInv ? $m("cmEmail").value.trim() : "",
          tenant_business_address: reqInv && $m("cmBizAddr") ? $m("cmBizAddr").value.trim() : "",
        },
      });
    };
    $m("cmSave").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await saveContractEdits();
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
        showContractModal(no);
      });
    });
    $m("cmPhoto").addEventListener("click", () => { closeModal(); S.brokerTargetRoomNo = no; L.switchTab("broker"); });

    const curDep = Number(ct.deposit || 0);
    const curRent = Number(ct.monthly_rent || 0);
    const curFee = Number(ct.maintenance_fee || 0);
    const curClean = Number(ct.cleaning_fee || 0);
    const curPay = ct.pay_timing || r.pay_timing || "후불";
    const resolveNextDefaults = () => {
      const nt = r.next_terms || {};
      const ec0 = r.expiry_check || null;
      const ecPrev = Boolean(ec0 && /RENEWAL_/.test(ec0.status || ""));
      const edDep = Number($m("cmDep").value || 0);
      const edRent = Number($m("cmRent").value || 0);
      const edFee = Number($m("cmFee").value || 0);
      const edClean = Number($m("cmClean").value || 0);
      return {
        hasNt: Object.keys(nt).length > 0 || ecPrev,
        dep0: edDep !== curDep ? edDep : (nt.deposit != null ? Number(nt.deposit) : (ecPrev && ec0.proposed_deposit != null ? Number(ec0.proposed_deposit) : curDep)),
        rent0: edRent !== curRent ? edRent : (nt.monthly_rent != null ? Number(nt.monthly_rent) : (ecPrev && ec0.proposed_monthly_rent != null ? Number(ec0.proposed_monthly_rent) : curRent)),
        fee0: edFee !== curFee ? edFee : (nt.maintenance_fee != null ? Number(nt.maintenance_fee) : (ecPrev && ec0.proposed_maintenance_fee != null ? Number(ec0.proposed_maintenance_fee) : curFee)),
        clean0: edClean !== curClean ? edClean : (nt.cleaning_fee != null ? Number(nt.cleaning_fee) : curClean),
        pay0: nt.pay_timing || curPay,
        term0: Number((nt.lease_term_months != null ? nt.lease_term_months : (ecPrev && ec0.proposed_term_months)) || 12),
      };
    };
    const currentTermsSummaryHtml = (hasNt) => `
      <div class="bass-card" style="padding:8px 10px;margin-bottom:10px;font-size:12.5px;background:var(--surface-2, #f8fafc);">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
          <strong>현재 계약 조건</strong>
          ${hasNt ? '<span class="bass-badge bass-badge--info" style="font-size:12px;">다음 계약 조건 저장됨</span>' : '<span class="bass-muted" style="font-size:12px;">현재 조건 기준</span>'}
        </div>
        <div class="bass-muted" style="margin-top:3px;">보증금 ${fmtWon(curDep)} · 월세 ${fmtWon(curRent)} · 관리비 ${fmtWon(curFee)} · 청소비 ${fmtWon(curClean)} · ${Bass.escapeHtml(curPay)}</div>
      </div>`;

    const btnNewCt = $m("cmNewContract");
    if (btnNewCt) {
      btnNewCt.addEventListener("click", () => {
        const { hasNt, dep0, rent0, fee0, clean0, pay0 } = resolveNextDefaults();
        const hasRes = Boolean(r.reservation);
        const canOpenNew = (!r.is_occupied || Boolean(r.pre_moveout_notice)) && !hasRes;
        openModal(`🤝 ${Bass.escapeHtml(no)}호 신규 계약 (다음 계약 조건)`, `
          ${currentTermsSummaryHtml(hasNt)}
          <div class="bass-muted" style="font-size:13px;margin-bottom:10px;line-height:1.6;">
            ${r.is_occupied
              ? `현재 계약 조건이 먼저 표시됩니다. 아래에서 수정 후 저장하면 <strong>다음 계약 조건</strong>으로 별도 저장되며, <strong>신규 임차인이 입주하는 순간 현재 계약 조건을 대체</strong>해 적용됩니다. (전출 전까지 현재 계약은 그대로 유지)`
              : `신규 입주자용 계약 조건입니다. 계약금은 보증금의 10%로 자동 설정됩니다.`}
          </div>
          <div class="r3-grid">
            <label class="r3-fld">보증금(원)<input id="ncDep" class="bass-input" type="number" min="0" value="${dep0}"></label>
            <label class="r3-fld">월세(원)<input id="ncRent" class="bass-input" type="number" min="0" value="${rent0}"></label>
            <label class="r3-fld">관리비(원)<input id="ncFee" class="bass-input" type="number" min="0" value="${fee0}"></label>
            <label class="r3-fld">퇴실 청소비(원)<input id="ncClean" class="bass-input" type="number" min="0" value="${clean0}"></label>
            <label class="r3-fld">월세 납부<select id="ncPay" class="bass-input"><option value="후불" ${pay0 === "후불" ? "selected" : ""}>후불</option><option value="선불" ${pay0 === "선불" ? "selected" : ""}>선불</option></select></label>
            ${canOpenNew ? `<label class="r3-fld">새 임차인 성명(선택)<input id="ncName" class="bass-input" placeholder="예: 홍길동"></label>
            <label class="r3-fld">새 임차인 휴대폰(선택)<input id="ncTel" class="bass-input" placeholder="010-0000-0000"></label>` : ""}
          </div>
          ${hasRes ? `<div class="bass-card" style="margin-top:10px;padding:10px;font-size:12.5px;border-color:var(--warn);">
            ⚠️ 이미 다음 계약(예약 또는 연장)이 체결되어 있어 다음 계약 조건을 변경할 수 없습니다. 변경하려면 예약을 먼저 취소해 주세요.
          </div>` : (!canOpenNew ? `<div class="bass-card" style="margin-top:10px;padding:10px;font-size:12.5px;border-color:var(--warn);">
            ⏳ 현재 입주 중이며 전출 예정 사전 매물로 공개되지 않아 신규 계약방을 바로 열 수 없습니다. 먼저 <strong>다음 계약 조건 저장</strong>을 해 두면 사전 매물 공개 및 신규 입주 시 자동 적용됩니다.
          </div>` : "")}
          <div class="bass-row" style="gap:8px;margin-top:12px;flex-wrap:wrap;">
            ${r.is_occupied && !hasRes ? `<button id="ncSaveNext" class="bass-btn ${canOpenNew ? "" : "bass-btn--primary"}" type="button" style="flex:1;">💾 다음 계약 조건 저장</button>` : ""}
            ${r.is_occupied && !hasRes && hasNt ? `<button id="ncClearNext" class="bass-btn" type="button">초기화</button>` : ""}
            ${canOpenNew ? `<button id="ncOpen" class="bass-btn bass-btn--primary" type="button" style="flex:1;">🤝 신규 계약 채팅방 열기</button>` : ""}
            <button id="ncBack" class="bass-btn" type="button">← 돌아가기</button>
          </div>`);
        $m("ncBack").addEventListener("click", () => showContractModal(no));
        const saveNextOnly = async () => {
          if (!r.is_occupied) return;
          return await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/next-terms`, {
            method: "POST",
            body: {
              deposit: Number($m("ncDep").value || 0),
              monthly_rent: Number($m("ncRent").value || 0),
              maintenance_fee: Number($m("ncFee").value || 0),
              cleaning_fee: Number($m("ncClean").value || 0),
              pay_timing: $m("ncPay").value || "후불",
            },
          });
        };
        const btnSaveNext = $m("ncSaveNext");
        if (btnSaveNext) {
          btnSaveNext.addEventListener("click", function () {
            Bass.withLock(this, async () => {
              const res = await saveNextOnly();
              Bass.updateAppAlert("success", (res && res.message) || `${no}호 다음 계약 조건을 저장했습니다. 신규 임차인 입주 시 현재 계약 조건을 대체합니다.`);
              await L.refresh();
              showContractModal(no);
            });
          });
        }
        const btnClearNext = $m("ncClearNext");
        if (btnClearNext) {
          btnClearNext.addEventListener("click", function () {
            if (!window.confirm("저장해 둔 다음 계약 조건을 지우고 현재 계약 조건으로 되돌릴까요?")) return;
            Bass.withLock(this, async () => {
              const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/next-terms`, { method: "POST", body: { clear: true } });
              Bass.updateAppAlert("success", res.message);
              await L.refresh();
              showContractModal(no);
            });
          });
        }
        const btnNcOpen = $m("ncOpen");
        if (btnNcOpen) {
          btnNcOpen.addEventListener("click", function () {
            Bass.withLock(this, async () => {
              await saveNextOnly();
              const created = await Bass.api("rent3_contract", "/sessions", {
                method: "POST",
                body: {
                  branch: S.currentBranch,
                  room_no: no,
                  tenant_name: ($m("ncName") && $m("ncName").value || "").trim(),
                  tenant_phone: ($m("ncTel") && $m("ncTel").value || "").trim(),
                  mode: "NEW",
                  initiated_by_role: "lessor",
                },
              });
              const sess = created.session;
              if (sess && sess.id) {
                window.location.href = `/rent3_contract/?session=${encodeURIComponent(sess.id)}&role=lessor&chat=1`;
              }
            });
          });
        }
      });
    }

    const btnRenew = $m("cmRenew");
    if (btnRenew) {
      btnRenew.addEventListener("click", () => {
        const { hasNt, dep0, rent0, fee0, clean0, pay0, term0 } = resolveNextDefaults();
        const hasRes = Boolean(r.reservation);
        const maxDep = Math.round(curDep * 1.05);
        const maxRent = Math.round(curRent * 1.05);
        openModal(`🔁 ${Bass.escapeHtml(no)}호 연장 계약 (5% 상한 범위)`, `
          ${currentTermsSummaryHtml(hasNt)}
          <div class="bass-muted" style="font-size:13px;margin-bottom:10px;line-height:1.6;">
            현재 계약 조건이 먼저 표시됩니다. <strong>법정 5% 상한 이내에서 직접 수정</strong>해 <strong>다음 계약 조건 저장</strong>하거나 재계약 채팅방을 여세요.<br>
            만기 전까지는 현재 조건이 유지되고 연장 시작일부터 새 조건으로 대체 적용됩니다. (본인·등기부·옵션 확인 생략 → 조건 확인 체크 → 서명 날인)
          </div>
          <div class="r3-grid">
            <label class="r3-fld">보증금(원) <span class="bass-muted">5% 상한 ${fmtWon(maxDep)}</span><input id="rnDep" class="bass-input" type="number" min="0" value="${dep0}"></label>
            <label class="r3-fld">월세(원) <span class="bass-muted">5% 상한 ${fmtWon(maxRent)}</span><input id="rnRent" class="bass-input" type="number" min="0" value="${rent0}"></label>
            <label class="r3-fld">관리비(원)<input id="rnFee" class="bass-input" type="number" min="0" value="${fee0}"></label>
            <label class="r3-fld">퇴실 청소비(원)<input id="rnClean" class="bass-input" type="number" min="0" value="${clean0}"></label>
            <label class="r3-fld">월세 납부<select id="rnPay" class="bass-input"><option value="후불" ${pay0 === "후불" ? "selected" : ""}>후불</option><option value="선불" ${pay0 === "선불" ? "selected" : ""}>선불</option></select></label>
            <label class="r3-fld">연장 개월<input id="rnTerm" class="bass-input" type="number" min="1" value="${term0 || 12}"></label>
          </div>
          ${hasRes ? `<div class="bass-card" style="margin-top:10px;padding:10px;font-size:12.5px;border-color:var(--warn);">
            ⚠️ 이미 다음 계약(예약 또는 연장)이 체결되어 있어 조건을 변경하거나 다시 열 수 없습니다. 예약을 먼저 취소해 주세요.
          </div>` : ""}
          <div class="bass-row" style="gap:8px;margin-top:12px;flex-wrap:wrap;">
            ${!hasRes ? `<button id="rnSaveNext" class="bass-btn" type="button" style="flex:1;">💾 다음 계약 조건 저장</button>` : ""}
            ${!hasRes && hasNt ? `<button id="rnClearNext" class="bass-btn" type="button">초기화</button>` : ""}
            ${!hasRes ? `<button id="rnGo" class="bass-btn bass-btn--primary" type="button" style="flex:1;">🔁 재계약 채팅방 열기 (5% 이내)</button>` : ""}
            <button id="rnBack" class="bass-btn" type="button">← 돌아가기</button>
          </div>`);
        $m("rnBack").addEventListener("click", () => showContractModal(no));
        const readRenewalInputs = () => {
          const pDep = Number($m("rnDep").value || 0);
          const pRent = Number($m("rnRent").value || 0);
          const pFee = Number($m("rnFee").value || 0);
          const pClean = Number($m("rnClean").value || 0);
          const pPay = $m("rnPay").value || "후불";
          const pTerm = Math.max(1, Number($m("rnTerm").value || 12));
          if (curDep > 0 && pDep > maxDep) {
            Bass.updateAppAlert("warning", `보증금은 법정 5% 상한(${fmtWon(maxDep)}) 이내로 입력해 주세요.`);
            return null;
          }
          if (curRent > 0 && pRent > maxRent) {
            Bass.updateAppAlert("warning", `월세는 법정 5% 상한(${fmtWon(maxRent)}) 이내로 입력해 주세요.`);
            return null;
          }
          return { pDep, pRent, pFee, pClean, pPay, pTerm };
        };
        const btnRnSave = $m("rnSaveNext");
        if (btnRnSave) {
          btnRnSave.addEventListener("click", function () {
            const v = readRenewalInputs();
            if (!v) return;
            Bass.withLock(this, async () => {
              const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/next-terms`, {
                method: "POST",
                body: {
                  deposit: v.pDep, monthly_rent: v.pRent, maintenance_fee: v.pFee,
                  cleaning_fee: v.pClean, pay_timing: v.pPay, enforce_renewal_cap: true,
                },
              });
              Bass.updateAppAlert("success", (res && res.message) || `${no}호 다음 계약 조건(5% 이내)을 저장했습니다.`);
              await L.refresh();
              showContractModal(no);
            });
          });
        }
        const btnRnClear = $m("rnClearNext");
        if (btnRnClear) {
          btnRnClear.addEventListener("click", function () {
            if (!window.confirm("저장해 둔 다음 계약 조건을 지우고 현재 계약 조건으로 되돌릴까요?")) return;
            Bass.withLock(this, async () => {
              const res = await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/next-terms`, { method: "POST", body: { clear: true } });
              Bass.updateAppAlert("success", res.message);
              await L.refresh();
              showContractModal(no);
            });
          });
        }
        const btnRnGo = $m("rnGo");
        if (btnRnGo) {
          btnRnGo.addEventListener("click", function () {
            const v = readRenewalInputs();
            if (!v) return;
            Bass.withLock(this, async () => {
              if (r.is_occupied && !r.reservation) {
                await Bass.api(APP_ID, `/rooms/${encodeURIComponent(r.id)}/next-terms`, {
                  method: "POST",
                  body: {
                    deposit: v.pDep, monthly_rent: v.pRent, maintenance_fee: v.pFee,
                    cleaning_fee: v.pClean, pay_timing: v.pPay, enforce_renewal_cap: true,
                  },
                });
              }
              const res = await Bass.api(APP_ID, "/expiry/preview-renewal", {
                method: "POST",
                body: {
                  branch: S.currentBranch,
                  room_no: no,
                  proposed_deposit: v.pDep,
                  proposed_monthly_rent: v.pRent,
                  proposed_maintenance_fee: v.pFee,
                  proposed_cleaning_fee: v.pClean,
                  proposed_term_months: v.pTerm,
                  open_cosign_session: true,
                },
              });
              Bass.updateAppAlert("success", res.message || "연장 재계약방이 열렸습니다.");
              const sid = res.check && res.check.cosign_session_id;
              await L.refresh();
              if (sid) window.location.href = `/rent3_contract/?session=${encodeURIComponent(sid)}&role=lessor&chat=1`;
              else closeModal();
            });
          });
        }
      });
    }

    const kb = $m("cmKeys");
    if (kb) {
      kb.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const bal = Math.max(Number(ct.deposit || 0) - Number(ct.contract_deposit || 0), 0);
          const ok = window.confirm(`${no}호 입주일(${ct.move_in_date || "-"})의 잔금 ${fmtWon(bal)}이 내 은행 앱의 실제 입금 내역에 들어온 것을 직접 확인했습니까?\n\n확인하면 도어락·공동현관 정보가 임차인 화면에 바로 열립니다. (임차인이 보여 주는 이체 완료 화면은 위조될 수 있습니다)`);
          if (!ok) return;
          const res = await Bass.api(APP_ID, `/rooms/${r.id}/release-keys`, { method: "POST", body: { balance_checked: true } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
          showContractModal(no);
        });
      });
    }
  }

  // ③ 만기통보: 전출계획 확인 문자 → 결과 기록 → (전출 의사 없으면) 5% 이내 갱신 안내 문자
  function showExpiryModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct) return;
    const ew = r.expiry_window;
    const ec = r.expiry_check;
    const phone = ct.tenant_phone || (r.tenant && r.tenant.phone) || "";
    const deadline = ew && ew.notice_deadline_date ? `${dotDate(ew.notice_deadline_date)}까지 ` : "";
    const msg1 = `[${bldgName()}] ${no}호 ${ct.tenant_name}님 안녕하세요, 임대인입니다.\n계약 만기가 ${dotDate(ct.expiry_date)}입니다. 만기 후 이사(전출) 계획이 있으신지 ${deadline}알려 주시기 바랍니다.\n연장(전출의사 없음) 시에는 월세 5% 범위 내에서 인상된 조건으로 재계약이 진행됩니다. 자세한 조건은 따로 안내드리겠습니다.`;
    const stay = Boolean(ec && (ec.tenant_plan === "STAY" || /RENEWAL/.test(ec.status || "")));
    const moveOut = Boolean(ec && ec.tenant_plan === "MOVE_OUT");
    const nt0 = r.next_terms || {};
    const previewed = Boolean(ec && /RENEWAL_/.test(ec.status || ""));
    const dep0 = previewed ? ((ec && ec.proposed_deposit) || r.deposit) : (nt0.deposit != null ? nt0.deposit : ((ec && ec.proposed_deposit) || r.deposit));
    const rent0 = (previewed && ec.proposed_monthly_rent) ? ec.proposed_monthly_rent : (nt0.monthly_rent != null ? nt0.monthly_rent : Number(r.monthly_rent || 0));
    const fee0 = (previewed && ec.proposed_maintenance_fee != null) ? ec.proposed_maintenance_fee : (nt0.maintenance_fee != null ? nt0.maintenance_fee : r.maintenance_fee);
    const maxDep = Math.round(Number(r.deposit || 0) * 1.05);
    const maxRent = Math.round(Number(r.monthly_rent || 0) * 1.05);
    openModal(`📅 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 만기통보`, `
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;">만기 ${Bass.escapeHtml(dotDate(ct.expiry_date))}${ew ? ` · 법정 통지기간 ${Bass.escapeHtml(dotDate(ew.notice_open_date))} ~ ${Bass.escapeHtml(dotDate(ew.notice_deadline_date))}` : ""}
        ${ec ? `<br>현재 상태: <strong>${Bass.escapeHtml(expiryStatusLabel(ec))}</strong>` : "<br>통보 실적이 없습니다."}</div>
      <div class="r3-lbl">① 전출계획 확인 문자</div>
      <button id="exCopy1" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="margin:6px 0 12px;">📋 복사 → 임차인 문자창</button>
      <div class="r3-lbl">② 임차인 답변</div>
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-bottom:6px;">
        <button id="exStay" class="bass-btn bass-btn--sm" type="button">🏠 전출 의사 없음</button>
        <input id="exOutDate" class="bass-input" type="date" style="width:150px;" value="${Bass.escapeHtml(ct.expiry_date || "")}" aria-label="전출 예정일">
        <button id="exOut" class="bass-btn bass-btn--sm" type="button">📦 전출 예정</button>
      </div>
      ${moveOut ? `<div class="bass-card" style="padding:8px 10px;margin:6px 0;font-size:12.5px;border-color:var(--primary,#2563eb);background:rgba(37,99,235,.06);">
        📦 전출 예정(${Bass.escapeHtml(ec.planned_move_out_date || "")})으로 기록되어 있습니다.${ec.prelist_open_date ? ` (알림 기준일: ${Bass.escapeHtml(ec.prelist_open_date)})` : ""}
        <div style="margin-top:6px;"><button id="exGoListingPdf" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📄 표준 임대조건 PDF 작성 · 매물 등록 →</button></div>
      </div>` : ""}
      ${stay ? `
        <div class="r3-lbl" style="margin-top:12px;">③ 재계약 안내 (현재 조건 기준 · 법정 5% 상한 이내 직접 조정)</div>
        <div class="r3-grid">
          <label class="r3-fld">보증금(원) <span class="bass-muted">최대 ${fmtWon(maxDep)}</span><input id="exDep" class="bass-input" type="number" value="${dep0}"></label>
          <label class="r3-fld">월세(원) <span class="bass-muted">최대 ${fmtWon(maxRent)}</span><input id="exRent" class="bass-input" type="number" value="${rent0}"></label>
          <label class="r3-fld">관리비(원)<input id="exFee" class="bass-input" type="number" value="${fee0}"></label>
          <label class="r3-fld">연장 개월<input id="exTerm" class="bass-input" type="number" value="${(ec && ec.proposed_term_months) || 12}"></label>
        </div>
        <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-top:8px;">
          <button id="exCopy2" class="bass-btn bass-btn--sm bass-btn--primary" type="button">📞 재계약 창 호출 문자 (만기 3~5일 전)</button>
          <button id="exSend" class="bass-btn bass-btn--sm" type="button">📝 갱신 예고 기록·앱 전달</button>
          ${ec && ec.cosign_session_id ? `<a class="bass-btn bass-btn--sm" href="/rent3_contract/?session=${Bass.escapeHtml(ec.cosign_session_id)}&role=lessor">🤝 재계약 창 열기 (승인·서명)</a>` : ""}
          <button id="exPwRemind" class="bass-btn bass-btn--sm" type="button">🔒 재계약 새 암호 설정 안내·링크 복사</button>
        </div>` : ""}`);

    $m("exCopy1").addEventListener("click", async () => {
      await copyAndSms(msg1, phone);
      if (!ec) {
        try {
          await Bass.api(APP_ID, "/expiry/request-check", { method: "POST", body: { branch: S.currentBranch, room_no: no } });
          await L.refresh();
          showExpiryModal(no);
        } catch (e) { Bass.showError(e); }
      }
    });
    const plan = (p) => function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/expiry/record-plan", {
          method: "POST",
          body: {
            branch: S.currentBranch, room_no: no, tenant_plan: p,
            planned_move_out_date: p === "MOVE_OUT" ? ($m("exOutDate").value || ct.expiry_date || "") : "",
            tenant_memo: p === "MOVE_OUT" ? "임차인 문자 답변 — 만기 퇴실 예정" : "임차인 문자 답변 — 전출의사 없음(거주 연장 희망)",
            prelist_vacancy: true,
          },
        });
        Bass.updateAppAlert(res.level || "success", res.message, res.hint);
        await L.refresh();
        showExpiryModal(no);
      });
    };
    $m("exStay").addEventListener("click", plan("STAY"));
    $m("exOut").addEventListener("click", plan("MOVE_OUT"));
    if ($m("exGoListingPdf")) {
      $m("exGoListingPdf").addEventListener("click", () => {
        closeModal();
        S.listingTargetRoomNo = no;
        L.switchTab("rooms");
      });
    }
    if (stay) {
      const readExpiryInputs = () => {
        const d = Number($m("exDep").value || 0);
        const rt = Number($m("exRent").value || 0);
        const fe = Number($m("exFee").value || 0);
        const tm = Math.max(1, Number($m("exTerm").value || 12));
        if (Number(r.deposit || 0) > 0 && d > maxDep) {
          Bass.updateAppAlert("warning", `보증금은 법정 5% 상한(${fmtWon(maxDep)}) 이내로 입력해 주세요.`);
          return null;
        }
        if (Number(r.monthly_rent || 0) > 0 && rt > maxRent) {
          Bass.updateAppAlert("warning", `월세는 법정 5% 상한(${fmtWon(maxRent)}) 이내로 입력해 주세요.`);
          return null;
        }
        return { d, rt, fe, tm };
      };
      const openOrSyncRenewal = async (v) => {
        return await Bass.api(APP_ID, "/expiry/preview-renewal", {
          method: "POST",
          body: {
            branch: S.currentBranch, room_no: no,
            proposed_deposit: v.d, proposed_monthly_rent: v.rt,
            proposed_maintenance_fee: v.fe, proposed_term_months: v.tm,
            renewal_note: "전출의사가 없으셔서 연장 재계약 조건을 사전 예고드립니다.", open_cosign_session: true,
          },
        });
      };
      $m("exCopy2").addEventListener("click", function () {
        const v = readExpiryInputs();
        if (!v) return;
        Bass.withLock(this, async () => {
          // 기존 세션 유무와 무관하게 입력한 재계약 조건(인상예정액)을 예고장·다음계약조건·재계약 창에 항상 동기화한다
          const res = await openOrSyncRenewal(v);
          const chk = (res && res.check) || {};
          const sid = chk.cosign_session_id || (ec && ec.cosign_session_id) || "";
          const d = chk.proposed_deposit != null ? Number(chk.proposed_deposit) : v.d;
          const rt = chk.proposed_monthly_rent != null ? Number(chk.proposed_monthly_rent) : v.rt;
          const fe = chk.proposed_maintenance_fee != null ? Number(chk.proposed_maintenance_fee) : v.fe;
          const tm = chk.proposed_term_months != null ? Number(chk.proposed_term_months) : v.tm;
          await L.refresh();
          const url = `${window.location.origin}/rent3_contract/?session=${encodeURIComponent(sid)}&role=tenant`;
          const callMsg = `[${bldgName()}] ${no}호 ${ct.tenant_name}님, 계약 만기(${dotDate(ct.expiry_date)})를 앞두고 비대면 재계약 창으로 호출합니다.\n보증금 ${fmtWon(d)} / 월세 ${fmtWon(rt)} / 관리비 ${fmtWon(fe)} (${tm}개월 연장)\n본인 확인 없이 아래 링크에서 재계약 조건 승인 후 전자서명만 하시면 비대면 재계약이 완료됩니다.\n${url}`;
          await copyAndSms(callMsg, phone);
          Bass.updateAppAlert("success", `${no}호 재계약 조건(보증금 ${fmtWon(d)} / 월세 ${fmtWon(rt)})을 재계약 창에 반영하고 호출 문자를 준비했습니다.`);
          showExpiryModal(no);
        });
      });
      $m("exPwRemind").addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, "/expiry/remind-password", {
            method: "POST",
            body: { branch: S.currentBranch, room_no: no },
          });
          const fullText = (res.sms_text || "").replace(res.url, window.location.origin + res.url);
          await copyAndSms(fullText, phone);
          Bass.updateAppAlert("success", res.message);
        });
      });
      $m("exSend").addEventListener("click", function () {
        const v = readExpiryInputs();
        if (!v) return;
        Bass.withLock(this, async () => {
          const res = await openOrSyncRenewal(v);
          const sid = res && res.check && res.check.cosign_session_id;
          Bass.updateAppAlert(res.level || "success", res.message, res.hint);
          if (sid) {
            window.location.href = `/rent3_contract/?session=${encodeURIComponent(sid)}&role=lessor`;
            return;
          }
          await L.refresh();
          showExpiryModal(no);
        });
      });
    }
  }

  // ④ 전출 정산: 임차인 정보 · 공제 후 환불액 문자 요약 → 복사 → 정산서 게시/임시저장
  function showMoveOutModal(no) {
    const r = roomOf(no);
    const ct = r && r.contract;
    if (!ct || !r.ledger) return;
    const lb = r.checkout_liability || {};
    const early = Number(lb.early_amount || 0);
    const partialAuto = Number(lb.partial_amount || 0);
    const unpaidAuto = Number(lb.unpaid_now || 0) + Math.max(0, early - partialAuto);
    const earlyWho = lb.reason === "NEW_TENANT" ? "새 임차인 입주 전날" : "만기일";
    const dep = Number(ct.deposit || 0);
    const cleanAgreed = Number(ct.cleaning_fee || 0);
    const phone = ct.tenant_phone || (r.tenant && r.tenant.phone) || "";
    const num = (id) => Number(($m(id) && $m(id).value) || 0);
    const prev = ((S.dash && S.dash.checkouts) || []).find((x) => x.room_no === no && ["DRAFT", "CALCULATED", "DISPUTED"].includes(x.status) && (Number(x.rent_unpaid_auto || 0) + Number(x.rent_prorated_auto || 0) + Number(x.rent_unpaid || 0) > 0 || Number(x.refund_amount || 0) > 0 && "rent_unpaid_auto" in x));
    const pv = (k, auto) => (prev && k in prev ? Number(prev[k] || 0) : auto);
    const published = Boolean(prev && prev.status !== "DRAFT");
    openModal(`📦 ${Bass.escapeHtml(no)}호 ${Bass.escapeHtml(ct.tenant_name || "")} · 전출 정산`, `
      <div style="font-size:13px;line-height:1.7;margin-bottom:8px;">
        👤 <strong>${Bass.escapeHtml(ct.tenant_name || "")}</strong> ${phone ? `· ${Bass.escapeHtml(phone)}` : ""}<br>
        전입 ${Bass.escapeHtml(dotDate(ct.move_in_date))} · 만기 ${Bass.escapeHtml(dotDate(ct.expiry_date))}
      </div>
      <div class="bass-muted" style="font-size:12.5px;margin-bottom:6px;"><strong>전출 정산 = 임차보증금 − 미납 임차료 − 일할 계산 임차료 − 청소비 − 미납 공과금(전기·수도·가스 등)</strong>. 자동 계산된 값을 임대인이 고칠 수 있고, 보낸 정산서는 임차인이 확인합니다.</div>
      <div class="r3-grid">
        <label class="r3-fld">전출일<input id="moDate" class="bass-input" type="date" value="${Bass.escapeHtml(S.dash.today || "")}"></label>
        <label class="r3-fld">임차보증금<input class="bass-input" type="text" value="${fmtWon(dep)}" readonly></label>
        <label class="r3-fld">미납 임차료(원) <span class="bass-muted">(자동 ${fmtWon(unpaidAuto)})</span><input id="moUnpaid" class="bass-input mo-in" type="number" min="0" value="${pv("rent_unpaid", unpaidAuto)}"></label>
        <label class="r3-fld">일할 계산 임차료(원) <span class="bass-muted">(자동 ${fmtWon(partialAuto)}${Number(lb.partial_days || 0) > 0 ? ` · ${Number(lb.partial_days)}일분` : ""})</span><input id="moProrated" class="bass-input mo-in" type="number" min="0" value="${pv("rent_prorated", partialAuto)}"></label>
        <label class="r3-fld">청소비(원) <span class="bass-muted">${cleanAgreed > 0 ? `(계약 약정 ${fmtWon(cleanAgreed)} 이내)` : "(계약에 약정 없음)"}</span><input id="moClean" class="bass-input mo-in" type="number" min="0" max="${cleanAgreed}" value="${prev ? Math.min(pv("cleaning_fee", cleanAgreed), cleanAgreed) : cleanAgreed}" ${cleanAgreed > 0 ? "" : "readonly"}></label>
        <label class="r3-fld">미납 전기요금(원)<input id="moElec" class="bass-input mo-in" type="number" min="0" value="${pv("utility_electric", 0)}"></label>
        <label class="r3-fld">미납 수도요금(원)<input id="moWater" class="bass-input mo-in" type="number" min="0" value="${pv("utility_water", 0)}"></label>
        <label class="r3-fld">미납 가스요금(원)<input id="moGas" class="bass-input mo-in" type="number" min="0" value="${pv("utility_gas", 0)}"></label>
        <label class="r3-fld">기타 공과금(원)<input id="moUtilOther" class="bass-input mo-in" type="number" min="0" value="${pv("utility_other", 0)}"></label>
        <label class="r3-fld">파손 복구비(원)<input id="moDmg" class="bass-input mo-in" type="number" min="0" value="${pv("damage_restoration_fee", 0)}"></label>
      </div>
      ${early > 0 ? `<div class="bass-muted" style="font-size:12.5px;line-height:1.6;margin-top:6px;">만기 전 퇴실이라 <strong>${Bass.escapeHtml(dotDate(lb.end_date))}(${earlyWho})</strong>까지 월 임차료를 부담합니다. 1개월분은 매달 전입일부터 다음 달 전입일 전날까지이며, 한 달 기간 중간에 끝나면 <strong>(경과 일수 × 월 임차료 ${fmtWon(lb.monthly_total)}) ÷ 30</strong>만 징수합니다${Number(lb.partial_days || 0) > 0 ? ` — 이번에는 ${Number(lb.partial_days)}일분 ${fmtWon(lb.partial_amount)}이 일할 계산 임차료입니다` : ""}.</div>` : ""}
      <div id="moFormula" class="bass-card" style="margin-top:8px;padding:10px;font-size:13px;line-height:1.7;"></div>
      <label class="bass-card" style="display:flex;align-items:flex-start;gap:8px;margin-top:8px;padding:10px;cursor:pointer;font-size:13.5px;font-weight:700;border-color:var(--warn);">
        <input id="moPublish" type="checkbox" style="margin-top:3px;width:20px;height:20px;" ${published ? "checked" : ""}>
        <span>정산을 마쳤습니다 — <strong>임차인에게 게시</strong><br><span class="bass-muted" style="font-weight:400;font-size:12.5px;">체크하고 보내야 임차인이 정산서를 볼 수 있습니다. 체크하지 않으면 <strong>임시 저장</strong>만 되어 임차인에게 보이지 않고 알림도 가지 않습니다(수정하는 중에 임차인이 보고 이의 제기를 하지 않도록).</span></span>
      </label>
      <div class="bass-row" style="gap:6px;flex-wrap:wrap;margin-top:8px;">
        <button id="moCopy" class="bass-btn bass-btn--sm" type="button">📋 복사 → 임차인 문자창</button>
        <button id="moDone" class="bass-btn bass-btn--sm bass-btn--primary" type="button">${published ? "📤 정산서 게시" : "💾 임시 저장 (임차인에게 안 보임)"}</button>
        ${!r.is_published ? `<button id="moGoListingPdf" class="bass-btn bass-btn--sm" type="button">📄 표준 임대조건 PDF · 매물 등록</button>` : ""}
      </div>
      <div id="moState" style="margin-top:10px;"></div>
      <div class="bass-muted" style="font-size:12px;margin-top:6px;">정산서를 보내면 임차인 앱·채팅에 도착하고, 임차인이 <strong>[정산 내역 확인]</strong>을 누르면 여기에 확인 표시가 뜹니다. 이어서 임차인이 <strong>본인 명의 계좌</strong>를 입력하고 동의하면 <strong>송금 완료 표시</strong>를 합니다. 임차인이 받았다고 확인하거나(또는 7일 안에 이의가 없으면) 퇴실이 확정되어 공실 매물로 공개됩니다. 이의가 오면 채팅·통화로 협의한 뒤 금액을 고쳐 다시 보내세요.</div>`);

    const clean = () => Math.min(Math.max(0, num("moClean")), cleanAgreed);
    const utilTotal = () => num("moElec") + num("moWater") + num("moGas") + num("moUtilOther");
    const deductTotal = () => num("moUnpaid") + num("moProrated") + clean() + utilTotal() + num("moDmg");
    const refund = () => Math.max(0, dep - deductTotal());
    const drawFormula = () => {
      const short = Math.max(0, deductTotal() - dep);
      $m("moFormula").innerHTML = `<strong>정산 내역</strong><br>임차보증금 ${fmtWon(dep)} − 미납 임차료 ${fmtWon(num("moUnpaid"))} − 일할 계산 임차료 ${fmtWon(num("moProrated"))} − 청소비 ${fmtWon(clean())} − 미납 공과금 ${fmtWon(utilTotal())}(전기 ${fmtWon(num("moElec"))} · 수도 ${fmtWon(num("moWater"))} · 가스 ${fmtWon(num("moGas"))} · 기타 ${fmtWon(num("moUtilOther"))})${num("moDmg") > 0 ? ` − 파손 복구비 ${fmtWon(num("moDmg"))}` : ""}<br><strong>= 반환 예정액 ${fmtWon(refund())}</strong>${short > 0 ? `<br><span style="color:var(--err);">공제액이 보증금보다 ${fmtWon(short)} 많습니다 — 부족분은 따로 정산해야 합니다.</span>` : ""}${(num("moUnpaid") !== unpaidAuto || num("moProrated") !== partialAuto) ? `<br><span class="bass-muted">※ 자동 계산값(미납 임차료 ${fmtWon(unpaidAuto)} · 일할 임차료 ${fmtWon(partialAuto)})에서 수정했습니다. 임차인에게도 수정 사실이 표시됩니다.</span>` : ""}`;
    };
    document.querySelectorAll(".mo-in").forEach((el) => el.addEventListener("input", drawFormula));
    $m("moPublish").addEventListener("change", () => { $m("moDone").textContent = $m("moPublish").checked ? "📤 정산서 게시" : "💾 임시 저장 (임차인에게 안 보임)"; });
    drawFormula();
    const compose = () => {
      const lines = [`[${bldgName()}] ${no}호 ${ct.tenant_name}님 전출 정산 안내 (전출일 ${dotDate($m("moDate").value)})`, `임차보증금 ${fmtWon(dep)}`];
      [["미납 임차료", num("moUnpaid")], [`일할 계산 임차료${early > 0 ? `(${dotDate(lb.end_date)} ${earlyWho}까지)` : ""}`, num("moProrated")], ["청소비(계약 약정)", clean()],
       ["미납 전기요금", num("moElec")], ["미납 수도요금", num("moWater")], ["미납 가스요금", num("moGas")], ["기타 공과금", num("moUtilOther")], ["파손 복구비", num("moDmg")]]
        .forEach(([k, v]) => { if (v > 0) lines.push(`- ${k} ${fmtWon(v)}`); });
      lines.push(`반환 예정액 ${fmtWon(refund())}`, "앱에서 [정산 내역 확인] 후 반환받을 계좌를 입력해 주세요.");
      return lines.join("\n");
    };
    $m("moCopy").addEventListener("click", () => copyAndSms(compose(), phone));
    if ($m("moGoListingPdf")) {
      $m("moGoListingPdf").addEventListener("click", () => {
        closeModal();
        S.listingTargetRoomNo = no;
        L.switchTab("rooms");
      });
    }
    const stateOf = () => ((S.dash && S.dash.checkouts) || []).find((x) => x.room_no === no && x.status !== "COMPLETED");
    const drawState = () => {
      const co = stateOf(), box = $m("moState");
      if (!co) { box.innerHTML = ""; return; }
      const label = { DRAFT: "💾 임시 저장됨 — 임차인에게 보이지 않습니다. 정산을 마치면 '임차인에게 게시'에 체크하고 보내세요.", REQUESTED: "🚪 임차인이 퇴실을 신청했습니다(희망일 " + (co.desired_date || "미정") + "). 금액을 입력해 정산서를 보내세요.", CALCULATED: (co.tenant_checked_at ? `✅ 임차인이 정산 내역을 확인했습니다(${String(co.tenant_checked_at).slice(5, 16).replace("T", " ")}) — 계좌 입력·동의 대기` : "📤 정산서를 보냈습니다 — 임차인 확인·동의 대기"), AGREED: "✍️ 임차인이 동의하고 계좌를 입력했습니다 — 송금 후 아래 단추를 누르세요", PAID: "💸 송금 표시 완료 — 임차인 수취 확인 대기(7일 안에 이의 없으면 자동 확정)", DISPUTED: "⚠️ 임차인 이의: " + (co.dispute_note || "") + " — 채팅·통화로 협의한 뒤 금액을 고쳐 다시 보내세요" }[co.status] || co.status;
      box.innerHTML = `<div class="bass-card" style="padding:10px;font-size:13px;line-height:1.7;"><strong>${Bass.escapeHtml(label)}</strong>
        ${co.status === "AGREED" ? `<br>예금주 <strong>${Bass.escapeHtml(co.account_holder || "")}</strong> · 계좌 <strong>${Bass.escapeHtml(co.refund_bank_account || "")}</strong> · 반환액 <strong>${fmtWon(co.refund_amount)}</strong><br><button id="moPaid" class="bass-btn bass-btn--sm bass-btn--primary" type="button" style="min-height:44px;margin-top:6px;">💸 송금 완료 표시</button>` : ""}</div>`;
      const paid = $m("moPaid");
      if (paid) paid.addEventListener("click", function () {
        if (!window.confirm(`${co.account_holder || ""} 님 계좌로 ${fmtWon(co.refund_amount)}을 실제로 송금한 뒤 눌러 주세요. 표시하면 임차인에게 수취 확인 요청이 갑니다.`)) return;
        Bass.withLock(this, async () => {
          const res = await Bass.api(APP_ID, `/checkout/${co.id}/mark-paid`, { method: "POST", body: { branch: S.currentBranch } });
          Bass.updateAppAlert("success", res.message);
          await L.refresh();
          drawState();
        });
      });
    };
    drawState();
    $m("moDone").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const res = await Bass.api(APP_ID, "/checkout/calculate", {
          method: "POST",
          body: { branch: S.currentBranch, room_no: no, unpaid_rent: num("moUnpaid"), prorated_rent: num("moProrated"), cleaning_fee: clean(),
                  utility_electric: num("moElec"), utility_water: num("moWater"), utility_gas: num("moGas"), utility_other: num("moUtilOther"), damage_restoration_fee: num("moDmg"), publish: Boolean($m("moPublish").checked) },
        });
        Bass.updateAppAlert("success", res.message);
        await L.refresh();
        drawState();
      });
    });
  }

  // 운영 ▸ 만기 통보: 만기 6~2개월 법정 통보 대상 목록 + 전체 입주 호실 만기 임박 순 현황
  function renderExpiryTab() {
    const noticeRooms = [...(S.dash.expiry_notice_rooms || [])]
      .sort((a, b) => (a.days_to_expiry ?? 9999) - (b.days_to_expiry ?? 9999));
    const stLabel = (st) => ({
      UNREQUESTED: "미통보",
      AWAITING_TENANT: "회신 대기",
      MOVEOUT_PLANNED: "전출 예정",
      STAY_CONFIRMED: "연장 희망",
      RENEWAL_PREVIEWED: "예고 발송",
      RENEWAL_ACKNOWLEDGED: "동의 완료",
    }[st] || st || "미통보");
    const stBadge = (st) => {
      if (st === "STAY_CONFIRMED" || st === "RENEWAL_PREVIEWED" || st === "RENEWAL_ACKNOWLEDGED") return "bass-badge--ok";
      if (st === "AWAITING_TENANT" || st === "MOVEOUT_PLANNED") return "bass-badge--info";
      return "bass-badge--warn";
    };
    const allOccupied = (S.dash.rooms || [])
      .filter((r) => r.is_occupied && r.contract)
      .sort((a, b) => ((a.ledger && a.ledger.days_to_expiry) ?? 99999) - ((b.ledger && b.ledger.days_to_expiry) ?? 99999));

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;${noticeRooms.length ? "border-left:4px solid var(--warn);" : ""}">
        <div class="bass-row" style="justify-content:space-between;gap:8px;flex-wrap:wrap;">
          <div>
            <h3 style="margin:0;">⚖️ 만기 6~2개월 법정 통보 대상 (${noticeRooms.length}실)</h3>
            <p class="bass-muted" style="font-size:13px;margin:4px 0 0;">
              만기 <strong>6개월 ~ 2개월 전</strong> 호실입니다. 눌러서 전출계획 확인이나 연장 예고를 보내세요.
            </p>
          </div>
          ${noticeRooms.length ? `<span class="bass-badge bass-badge--warn">법정 통지구간</span>` : ""}
        </div>
        <div style="margin-top:10px;">
          ${noticeRooms.length ? noticeRooms.map((nr) => `
            <button type="button" class="bass-btn exp-room-btn" data-room="${Bass.escapeHtml(nr.room_no)}"
              style="width:100%;min-height:52px;text-align:left;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 12px;">
              <div>
                <strong>${Bass.escapeHtml(nr.room_no)}호 ${Bass.escapeHtml(nr.tenant_name || "")}</strong>
                <div class="bass-muted" style="font-size:12.5px;margin-top:2px;">만기 ${Bass.escapeHtml(dotDate(nr.expiry_date))}${nr.days_to_expiry != null ? ` (D-${nr.days_to_expiry})` : ""} · 마감 ${Bass.escapeHtml(dotDate(nr.notice_deadline_date))}</div>
              </div>
              <span class="bass-badge ${stBadge(nr.check_status)}" style="white-space:nowrap;">${Bass.escapeHtml(stLabel(nr.check_status))}</span>
            </button>
          `).join("") : `<div class="bass-muted" style="font-size:13px;">현재 만기 6~2개월 전 구간에 해당하는 호실이 없습니다.</div>`}
        </div>
      </div>

      <div class="bass-card">
        <h3 style="margin:0 0 8px;">📅 전체 입주 호실 만기 현황 (${allOccupied.length}실 · 임박 순)</h3>
        <div style="max-height:42vh;overflow-y:auto;">
          ${allOccupied.map((r) => {
            const ct = r.contract;
            const days = r.ledger && r.ledger.days_to_expiry;
            const inWin = Boolean(r.expiry_window && r.expiry_window.in_6_to_2_months_window);
            const ec = r.expiry_check;
            return `
              <button type="button" class="bass-btn exp-room-btn" data-room="${Bass.escapeHtml(r.room_no)}"
                style="width:100%;min-height:48px;text-align:left;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px;${inWin ? "border-color:var(--warn);" : ""}">
                <span><strong>${Bass.escapeHtml(r.room_no)}호</strong> ${Bass.escapeHtml(ct.tenant_name || "")} · ${Bass.escapeHtml(dotDate(ct.expiry_date))}</span>
                <span class="bass-badge ${inWin ? "bass-badge--warn" : ""}" style="white-space:nowrap;">${days != null ? (days >= 0 ? `D-${days}` : `만기+${Math.abs(days)}일`) : "-"}${ec ? ` · ${Bass.escapeHtml(stLabel(ec.status))}` : ""}</span>
              </button>`;
          }).join("") || `<div class="bass-muted" style="font-size:13px;">입주 중인 호실이 없습니다.</div>`}
        </div>
      </div>
    `;
    elTabContent.querySelectorAll(".exp-room-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        S.selRoomNo = btn.dataset.room;
        showExpiryModal(btn.dataset.room);
      });
    });
  }

  // 운영 ▸ 일괄 납입 통보: 만기 6~2개월 구간 호실 현황 + D-3 사전 통보 · D+1 미납 통보 일괄 실행
  function renderNoticeTab() {
    const noticeRooms = S.dash.expiry_notice_rooms || [];
    elTabContent.innerHTML = `
      <div style="margin-top:12px;">
      ${noticeRooms.length > 0 ? `
        <div class="bass-card" style="margin-bottom:14px;border-left:4px solid var(--warn);">
          <div class="bass-row" style="justify-content:space-between;">
            <div>
              <h3 style="margin:0;">⚖️ 만기 6~2개월 전 전출계획 확인 · 연장 재계약 예고 대상 (${noticeRooms.length}실)</h3>
              <p class="bass-muted" style="font-size:13px;margin:4px 0 0;">
                주택임대차보호법 제6조에 따라 만기 <strong>6개월 ~ 2개월 전</strong>에 전출계획을 확인하고, 전출의사가 없을 때 조건 변경(5% 상한) 또는 동일조건 연장 재계약을 예고해야 묵시적 갱신 분쟁을 예방합니다.
              </p>
            </div>
            <span class="bass-badge bass-badge--warn">법정 통지구간 도래</span>
          </div>
          <div class="bass-row" style="margin-top:8px;flex-wrap:wrap;gap:6px;">
            ${noticeRooms.map((nr) => `
              <span class="bass-badge ${nr.check_status === "STAY_CONFIRMED" || nr.check_status === "RENEWAL_PREVIEWED" || nr.check_status === "RENEWAL_ACKNOWLEDGED" ? "bass-badge--ok" : "bass-badge--warn"}">
                ${Bass.escapeHtml(nr.room_no)}호 ${Bass.escapeHtml(nr.tenant_name)} · 만기 ${Bass.escapeHtml(nr.expiry_date)} (D-${nr.days_to_expiry} · 통지마감 ${Bass.escapeHtml(nr.notice_deadline_date)})
              </span>
            `).join("")}
          </div>
        </div>
      ` : ""}

      <div class="bass-card" style="margin-bottom:14px;">
        <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
          <div>
            <h3 style="margin:0;">🔔 매월 임대료 납입일(=입주일) 3일 전 통보 · 납입일 1일 후 미납 통보</h3>
            <p class="bass-muted" style="font-size:13px;margin:4px 0 0;">
              입주일 일자를 매월 납입일로 자동 산정하여 <strong>납입일 3일 전(D-3)</strong> 사전 안내 및 <strong>납입일 1일 후(D+1)</strong> 미납 호실 독촉 통보를 발송합니다 (완납 호실 오발송 원천 차단).
            </p>
          </div>
          <div class="bass-row" style="flex-wrap:wrap;gap:8px;">
            <label style="font-size:12px;">기준일
              <input id="billingRefDate" type="date" class="bass-input" style="width:145px;" value="${Bass.escapeHtml(S.dash.as_of_date || S.dash.today || "")}">
            </label>
            <label style="font-size:12.5px;display:flex;align-items:center;gap:5px;cursor:pointer;">
              <input id="chkCatchupOverdue" type="checkbox" checked>
              납입일+1일 이후 미통보 미납 포함
            </label>
            <button id="btnAutoDispatchBilling" class="bass-btn bass-btn--primary" type="button">⚡ D-3 납입통보 · D+1 미납통보 일괄 실행</button>
          </div>
        </div>
        ${(S.dash.recent_billing_notices || []).length > 0 ? `
          <div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);display:flex;flex-wrap:wrap;gap:6px;">
            ${(S.dash.recent_billing_notices || []).slice(0, 6).map((bn) => `
              <span class="bass-badge ${bn.status === "RESOLVED_PAID" ? "bass-badge--ok" : (bn.notice_type === "OVERDUE_1D" ? "bass-badge--err" : "bass-badge--info")}">
                ${bn.notice_type === "OVERDUE_1D" ? "🚨 미납통보" : "🔔 3일전통보"} · ${Bass.escapeHtml(bn.room_no)}호 ${Bass.escapeHtml(bn.tenant_name)} (${Bass.escapeHtml(bn.billing_ym)} · ${fmtWon(bn.amount_due)} · ${bn.status === "RESOLVED_PAID" ? "완납해소" : (bn.status === "ACKNOWLEDGED" ? "세입자확인" : "발송됨")})
              </span>
            `).join("")}
          </div>
        ` : ""}
      </div>
      </div>
    `;

    const elRefDate = document.getElementById("billingRefDate");
    if (elRefDate) {
      elRefDate.addEventListener("change", () => {
        S.selectedAsOfDate = elRefDate.value || "";
        L.refresh().catch(Bass.showError);
      });
    }

    const btnAutoDispatch = document.getElementById("btnAutoDispatchBilling");
    if (btnAutoDispatch) {
      btnAutoDispatch.addEventListener("click", function () {
        Bass.withLock(this, async () => {
          const refDate = document.getElementById("billingRefDate").value;
          S.selectedAsOfDate = refDate || "";
          const catchup = document.getElementById("chkCatchupOverdue").checked;
          const res = await Bass.api(APP_ID, "/billing-notices/auto-dispatch", {
            method: "POST",
            body: { branch: S.currentBranch, as_of_date: refDate || null, include_catchup_overdue: catchup },
          });
          Bass.updateAppAlert(res.level || "success", res.message);
          await L.refresh();
        });
      });
    }
  }

  function buildSampleBrokerPaperDataUrl(roomObj) {
    const c = document.createElement("canvas");
    c.width = 760;
    c.height = 500;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fcfbf7";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 3;
    ctx.strokeRect(18, 18, c.width - 36, c.height - 36);

    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 24px sans-serif";
    ctx.fillText("주택임대차표준계약서 (공인중개사 입회 원본 촬영본)", 95, 62);

    ctx.lineWidth = 1;
    ctx.strokeStyle = "#94a3b8";
    ctx.beginPath();
    ctx.moveTo(40, 82);
    ctx.lineTo(720, 82);
    ctx.stroke();

    const bn = (S.dash && S.dash.building && S.dash.building.building_name) || "시크빌";
    const addr = (S.dash && S.dash.building && S.dash.building.address) || "서울특별시";
    const rNo = (roomObj && roomObj.room_no) || "203";
    const dep = fmtWon((roomObj && roomObj.deposit) || 10000000);
    const rent = fmtWon((roomObj && roomObj.monthly_rent) || 520000);
    const maint = fmtWon((roomObj && roomObj.maintenance_fee) || 50000);

    ctx.font = "16px sans-serif";
    ctx.fillStyle = "#1e293b";
    const lines = [
      `1. 부동산의 표시 : ${addr} (${bn} ${rNo}호)`,
      `2. 보증금 : 금 ${dep}정  /  계약금 : 금 500,000원정 (계약시 영수함)`,
      `3. 차임(월세) : 금 ${rent}정 (후불)  /  관리비 : 금 ${maint}정`,
      `4. 임대차기간 : 인도일(2026-10-29)로부터 12개월간`,
      `5. 특약사항 : 현 시설물 상태 임대차 계약이며, 퇴실 시 기본 입주청소비 약정함.`,
      `6. 임대인(갑) : ${(S.dash && S.dash.lessor && S.dash.lessor.name) || "임대인"} (인)`,
      `7. 임차인(을) : 최중개  (연락처: 010-7788-9900)  (인)`,
      `8. 개업공인중개사 : 한강공인중개사사무소 대표 박공인 (등록 11440-2024-00112)`,
    ];
    lines.forEach((ln, idx) => { ctx.fillText(ln, 46, 122 + idx * 42); });

    ctx.strokeStyle = "#dc2626";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(650, 405, 36, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#dc2626";
    ctx.font = "bold 14px sans-serif";
    ctx.fillText("공인중개사", 616, 401);
    ctx.fillText("직인날인", 622, 420);

    return c.toDataURL("image/png");
  }

  function renderBrokerTab() {
    const rooms = S.dash.rooms || [];
    const vacantFirst = rooms.find((r) => !r.is_occupied) || rooms[0];
    const selectedNo = S.brokerTargetRoomNo || (vacantFirst ? vacantFirst.room_no : "");
    const selRoom = rooms.find((r) => r.room_no === selectedNo) || vacantFirst || {};
    const selTerms = { ...selRoom, ...(selRoom.next_terms || {}) };
    const todayIso = new Date().toISOString().slice(0, 10);

    elTabContent.innerHTML = `
      <div class="bass-card" style="margin-bottom:14px;">
        <h3>📸 부동산중개사 개입 지면계약서 사진 첨부 & 계약 원장 일괄 적용</h3>
        <p class="bass-muted" style="font-size:13px;">
          공인중개사 사무소에서 종이 계약서로 계약이 체결된 경우, 촬영한 계약서 사진을 첨부하고 계약 조건을 적용하면
          <code>uploads/contracts/</code> 이미지 보관과 함께 <code>contracts.json</code> · <code>rooms.json</code>(공실 자동마감) · <code>users.json</code>(임차인 마이홈 개통) · <code>payments.json</code> · <code>ops.json</code>(RTMS 신고관제)이 원클릭으로 동기화됩니다.
        </p>

        <div class="room-card" style="margin-top:12px;">
          <div class="bass-row" style="justify-content:space-between;flex-wrap:wrap;gap:8px;">
            <div>
              <label class="bass-label">1단계 · 종이 임대차계약서 촬영 사진 / 스캔본 첨부 (필수, JPG/PNG/WEBP/PDF)</label>
              <input id="brkPhotoFile" type="file" accept="image/*,application/pdf" class="bass-input" style="max-width:360px;">
            </div>
            <button id="btnGenSamplePhoto" class="bass-btn bass-btn--sm" type="button">🧪 샘플 중개계약서 촬영본 자동 생성</button>
          </div>
          <div id="brkPhotoStatus" class="bass-muted" style="font-size:12.5px;margin-top:6px;">
            ${S.brokerPhotoDataUrl ? `✅ 첨부 완료: ${Bass.escapeHtml(S.brokerPhotoFilename || "contract_photo.png")}` : "아직 첨부된 계약서 사진이 없습니다. 파일을 선택하거나 우측 샘플 생성 버튼을 눌러보세요."}
          </div>
          <img id="brkPhotoPreview" class="contract-photo-preview" style="display:${S.brokerPhotoDataUrl && !S.brokerPhotoDataUrl.startsWith("data:application/pdf") ? "block" : "none"};" src="${S.brokerPhotoDataUrl || ""}" alt="첨부 계약서 미리보기">
        </div>

        <div class="room-card">
          <label class="bass-label">💡 선택 기능 · 스마트폰 카메라 텍스트 복사(OCR) 붙여넣기 자동 입력</label>
          <div class="bass-row" style="margin-top:6px;">
            <input id="brkOcrText" class="bass-input" style="flex:1 1 180px;min-width:180px;" placeholder="예: ${Bass.escapeHtml(selRoom.room_no || "203")}호 보증금 1,000만원 월세 52만원 관리비 5만원 후불 입주일 ${todayIso} 임차인 최중개 010-7788-9900 한강공인중개사사무소 대표 박공인">
            <button id="btnParseOcr" class="bass-btn bass-btn--sm" type="button">✨ 문구에서 계약조건 자동 채우기</button>
          </div>
        </div>

        <div class="room-card">
          <h4 style="margin:0 0 10px;">2단계 · 호실 · 임차인 · 계약 금액 및 기간 확인</h4>
          <div class="field-grid">
            <div>
              <label class="bass-label">대상 호실</label>
              <select id="brkRoomNo" class="bass-select">
                ${rooms.map((r) => `<option value="${Bass.escapeHtml(r.room_no)}" ${r.room_no === selectedNo ? "selected" : ""}>${Bass.escapeHtml(r.room_no)}호 (${r.is_occupied ? "입주중-교체/재계약" : "공실"})</option>`).join("")}
              </select>
            </div>
            <div>
              <label class="bass-label">임대 유형</label>
              <select id="brkLeaseType" class="bass-select">
                <option value="월세" ${selRoom.lease_type !== "전세" ? "selected" : ""}>월세</option>
                <option value="전세" ${selRoom.lease_type === "전세" ? "selected" : ""}>전세</option>
              </select>
            </div>
            <div>
              <label class="bass-label">납부 방식 (후불 입주월 비과세 자동적용)</label>
              <select id="brkPayTiming" class="bass-select">
                <option value="후불" ${selTerms.pay_timing !== "선불" ? "selected" : ""}>후불</option>
                <option value="선불" ${selTerms.pay_timing === "선불" ? "selected" : ""}>선불</option>
              </select>
            </div>
            <div><label class="bass-label">보증금(원)</label><input id="brkDeposit" type="number" class="bass-input" value="${Number(selTerms.deposit ?? 10000000)}"></div>
            <div><label class="bass-label">월세(원)</label><input id="brkRent" type="number" class="bass-input" value="${Number(selTerms.monthly_rent ?? 500000)}"></div>
            <div><label class="bass-label">관리비(원)</label><input id="brkMaint" type="number" class="bass-input" value="${Number(selTerms.maintenance_fee ?? 50000)}"></div>
            <div><label class="bass-label">영수 계약금(원)</label><input id="brkContractDep" type="number" class="bass-input" value="500000"></div>
            <div><label class="bass-label">계약 체결일</label><input id="brkContractDate" type="date" class="bass-input" value="${todayIso}"></div>
            <div><label class="bass-label">입주(잔금)일</label><input id="brkMoveInDate" type="date" class="bass-input" value="${todayIso}"></div>
            <div><label class="bass-label">계약 기간(개월)</label><input id="brkMonths" type="number" class="bass-input" value="12"></div>
            <div><label class="bass-label">임차인 성명 (필수)</label><input id="brkTenantName" class="bass-input" placeholder="예: 최중개"></div>
            <div><label class="bass-label">임차인 연락처 (필수)</label><input id="brkTenantPhone" class="bass-input" placeholder="010-7788-9900"></div>
            <div><label class="bass-label">임차인 차량번호 (선택)</label><input id="brkCarNo" class="bass-input" placeholder="예: 34나 7890"></div>
          </div>
          <div style="margin-top:8px;">
            <label style="font-size:13px;cursor:pointer;">
              <input id="brkDepReceived" type="checkbox" checked> 계약금 수령 완료 (체크하면 계약금 수납으로 바로 기록됩니다)
            </label>
          </div>
          <div class="bass-card" style="margin-top:10px;padding:10px;">
            <label style="font-size:13.5px;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:6px;">
              <input id="brkTaxInvReq" type="checkbox"> 🧾 세입자가 세금계산서 발행을 요청함 (사무실·법인 기숙사 등 모든 호실 가능)
            </label>
            <div id="brkTaxBizWrap" class="field-grid" style="display:none;margin-top:8px;">
              <div><label class="bass-label">사업자등록번호</label><input id="brkBizNo" class="bass-input" placeholder="예: 214-86-12345"></div>
              <div><label class="bass-label">상호(법인명)</label><input id="brkCompName" class="bass-input" placeholder="예: (주)시크디자인"></div>
              <div><label class="bass-label">대표자명</label><input id="brkCeoName" class="bass-input" placeholder="예: 홍길동"></div>
              <div><label class="bass-label">세금계산서 수신 이메일</label><input id="brkBizEmail" type="email" class="bass-input" placeholder="예: tax@company.co.kr"></div>
            </div>
          </div>
        </div>

        <div class="room-card">
          <h4 style="margin:0 0 10px;">3단계 · 개업공인중개사 정보 및 전월세 신고제(RTMS) 확인</h4>
          <div class="field-grid">
            <div><label class="bass-label">중개사무소 상호</label><input id="brkAgency" class="bass-input" placeholder="예: 한강공인중개사사무소"></div>
            <div><label class="bass-label">대표 공인중개사 성명</label><input id="brkAgent" class="bass-input" placeholder="예: 박공인"></div>
            <div><label class="bass-label">중개업소 연락처</label><input id="brkBrokerPhone" class="bass-input" placeholder="02-555-1234"></div>
            <div><label class="bass-label">확정일자 번호 (부여 시 입력)</label><input id="brkFixedNo" class="bass-input" placeholder="미입력 시 중개신고 기준 자동채번"></div>
          </div>
          <div style="margin-top:10px;">
            <label class="bass-label">지면 계약서 주요 특약사항 메모</label>
            <input id="brkSpecial" class="bass-input" placeholder="예: 애완동물 사육 금지, 퇴실 시 입주청소비 7만원 정산 약정">
          </div>
          <div style="margin-top:10px;">
            <label style="font-size:13px;cursor:pointer;">
              <input id="brkRtmsFiled" type="checkbox" checked> 공인중개사가 주택임대차(전월세) 신고서 대행 접수 완료 (해제 시 30일 마감 알림 <code>PENDING</code>으로 관리인·임대인 관제)
            </label>
          </div>
        </div>

        <div class="bass-row" style="justify-content:flex-end;margin-top:10px;">
          <button id="btnSubmitBrokerContract" class="bass-btn bass-btn--primary" type="button">📸 계약서 사진 보관 및 계약내용 즉시 적용</button>
        </div>
      </div>
    `;

    document.getElementById("brkRoomNo").addEventListener("change", (ev) => {
      S.brokerTargetRoomNo = ev.target.value;
      const picked = rooms.find((x) => x.room_no === S.brokerTargetRoomNo);
      if (picked) {
        const pt = { ...picked, ...(picked.next_terms || {}) };
        document.getElementById("brkDeposit").value = Number(pt.deposit || 0);
        document.getElementById("brkRent").value = Number(pt.monthly_rent || 0);
        document.getElementById("brkMaint").value = Number(pt.maintenance_fee || 0);
        document.getElementById("brkLeaseType").value = pt.lease_type || "월세";
        document.getElementById("brkPayTiming").value = pt.pay_timing || "후불";
      }
    });

    document.getElementById("brkPhotoFile").addEventListener("change", (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        S.brokerPhotoDataUrl = String(reader.result || "");
        S.brokerPhotoFilename = file.name;
        document.getElementById("brkPhotoStatus").textContent = `✅ 첨부 완료: ${file.name} (${Math.round(file.size / 1024)} KB)`;
        const prev = document.getElementById("brkPhotoPreview");
        if (S.brokerPhotoDataUrl.startsWith("data:image/")) {
          prev.src = S.brokerPhotoDataUrl;
          prev.style.display = "block";
        } else {
          prev.style.display = "none";
        }
      };
      reader.readAsDataURL(file);
    });

    document.getElementById("btnGenSamplePhoto").addEventListener("click", () => {
      const curRoomNo = document.getElementById("brkRoomNo").value;
      const picked = rooms.find((x) => x.room_no === curRoomNo) || selRoom;
      S.brokerPhotoDataUrl = buildSampleBrokerPaperDataUrl(picked);
      S.brokerPhotoFilename = `broker_contract_${curRoomNo || "sample"}.png`;
      document.getElementById("brkPhotoStatus").textContent = `✅ 샘플 촬영 이미지 생성 완료: ${S.brokerPhotoFilename}`;
      const prev = document.getElementById("brkPhotoPreview");
      prev.src = S.brokerPhotoDataUrl;
      prev.style.display = "block";
      if (!document.getElementById("brkTenantName").value.trim()) {
        document.getElementById("brkTenantName").value = "최중개";
        document.getElementById("brkTenantPhone").value = "010-7788-9900";
        document.getElementById("brkAgency").value = "한강공인중개사사무소";
        document.getElementById("brkAgent").value = "박공인";
      }
      Bass.updateAppAlert("info", "샘플 공인중개사 표준계약서 촬영 이미지가 첨부되었습니다. 내용을 확인한 뒤 하단 적용 버튼을 눌러주세요.");
    });

    document.getElementById("btnParseOcr").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const raw = document.getElementById("brkOcrText").value.trim()
          || document.getElementById("brkOcrText").getAttribute("placeholder").replace(/^예:\s*/, "");
        const res = await Bass.api(APP_ID, "/contracts/parse-broker-text", {
          method: "POST",
          body: { text: raw },
        });
        const p = res.parsed || {};
        if (p.room_no && rooms.some((r) => r.room_no === p.room_no)) {
          document.getElementById("brkRoomNo").value = p.room_no;
          S.brokerTargetRoomNo = p.room_no;
        }
        if (p.deposit !== undefined) document.getElementById("brkDeposit").value = p.deposit;
        if (p.monthly_rent !== undefined) document.getElementById("brkRent").value = p.monthly_rent;
        if (p.maintenance_fee !== undefined) document.getElementById("brkMaint").value = p.maintenance_fee;
        if (p.contract_deposit !== undefined) document.getElementById("brkContractDep").value = p.contract_deposit;
        if (p.lease_type) document.getElementById("brkLeaseType").value = p.lease_type;
        if (p.pay_timing) document.getElementById("brkPayTiming").value = p.pay_timing;
        if (p.move_in_date) document.getElementById("brkMoveInDate").value = p.move_in_date;
        if (p.tenant_name) document.getElementById("brkTenantName").value = p.tenant_name;
        if (p.tenant_phone) document.getElementById("brkTenantPhone").value = p.tenant_phone;
        if (p.broker_agency_name) document.getElementById("brkAgency").value = p.broker_agency_name;
        if (p.broker_agent_name) document.getElementById("brkAgent").value = p.broker_agent_name;
        if (p.broker_phone) document.getElementById("brkBrokerPhone").value = p.broker_phone;
        Bass.updateAppAlert("info", res.message);
      });
    });

    document.getElementById("brkTaxInvReq").addEventListener("change", (ev) => {
      document.getElementById("brkTaxBizWrap").style.display = ev.target.checked ? "grid" : "none";
    });

    document.getElementById("btnSubmitBrokerContract").addEventListener("click", function () {
      Bass.withLock(this, async () => {
        const roomNo = document.getElementById("brkRoomNo").value;
        const reqInv = Boolean(document.getElementById("brkTaxInvReq").checked);
        const res = await Bass.api(APP_ID, "/contracts/broker-photo", {
          method: "POST",
          body: {
            branch: S.currentBranch,
            room_no: roomNo,
            photo_data_url: S.brokerPhotoDataUrl,
            photo_filename: S.brokerPhotoFilename,
            tenant_name: document.getElementById("brkTenantName").value.trim(),
            tenant_phone: document.getElementById("brkTenantPhone").value.trim(),
            tenant_car_number: document.getElementById("brkCarNo").value.trim(),
            lease_type: document.getElementById("brkLeaseType").value,
            pay_timing: document.getElementById("brkPayTiming").value,
            deposit: Number(document.getElementById("brkDeposit").value || 0),
            monthly_rent: Number(document.getElementById("brkRent").value || 0),
            maintenance_fee: Number(document.getElementById("brkMaint").value || 0),
            contract_deposit: Number(document.getElementById("brkContractDep").value || 0),
            deposit_received: document.getElementById("brkDepReceived").checked,
            contract_date: document.getElementById("brkContractDate").value,
            move_in_date: document.getElementById("brkMoveInDate").value,
            lease_term_months: Number(document.getElementById("brkMonths").value || 12),
            special_terms: document.getElementById("brkSpecial").value.trim(),
            broker_agency_name: document.getElementById("brkAgency").value.trim(),
            broker_agent_name: document.getElementById("brkAgent").value.trim(),
            broker_phone: document.getElementById("brkBrokerPhone").value.trim(),
            rtms_filed_by_broker: document.getElementById("brkRtmsFiled").checked,
            fixed_date_no: document.getElementById("brkFixedNo").value.trim(),
            tax_invoice_requested: reqInv,
            tenant_business_number: reqInv ? document.getElementById("brkBizNo").value.trim() : "",
            tenant_company_name: reqInv ? document.getElementById("brkCompName").value.trim() : "",
            tenant_ceo_name: reqInv ? document.getElementById("brkCeoName").value.trim() : "",
            tenant_email: reqInv ? document.getElementById("brkBizEmail").value.trim() : "",
          },
        });
        S.brokerPhotoDataUrl = "";
        S.brokerPhotoFilename = "";
        S.currentTab = "ledger";
        Bass.updateAppAlert(res.level || "success", res.message, res.hint);
        await L.refresh();
      });
    });
  }

  Object.assign(L, {
    revokeShortcuts, showInviteModal, showContractModal, showExpiryModal,
    showMoveOutModal, renderExpiryTab, renderNoticeTab,
    buildSampleBrokerPaperDataUrl, renderBrokerTab,
  });
})();
