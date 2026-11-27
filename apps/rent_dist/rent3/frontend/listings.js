/* apps/rent3/frontend/listings.js — 직거래 공실 탐색·필터·예산 계산기·문의/호출 모달 모듈 */
(function () {
  "use strict";

  const P = window.R3P;
  const { APP_ID, state, $, esc, fmtMan, getSession } = P;

  function updateAffordPreview() {
    const incMan = Number($("calcIncomeMan").value || 0);
    const recMan = Math.max(20, Math.round(incMan * 0.3));
    $("calcRecommendedText").textContent = `월 ${recMan.toLocaleString("ko-KR")}만원 이하`;
    return recMan * 10000;
  }

  function filteredListings() {
    const kw = ($("searchKeyword").value || "").trim().toLowerCase();
    const br = $("searchBranch").value;
    const cat = $("searchCategory").value;

    return state.listings.filter((item) => {
      if (br && item.branch !== br) return false;
      if (cat && item.room_category !== cat) return false;

      if (state.quickFilter === "instant" && item.pre_moveout_notice) return false;
      if (state.quickFilter === "pre_moveout" && !item.pre_moveout_notice) return false;
      if (state.quickFilter === "clean_title" && !item.clean_title) return false;
      if (state.quickFilter === "commercial" && item.room_category === "주거용 원룸") return false;

      if (state.budgetActive) {
        const monthlyTotal = Number(item.monthly_rent || 0) + Number(item.maintenance_fee || 0);
        if (state.maxMonthlyWon > 0 && monthlyTotal > state.maxMonthlyWon) return false;
        if (state.maxDepositWon > 0 && Number(item.deposit || 0) > state.maxDepositWon) return false;
      }

      if (kw) {
        const hay = [
          item.building_name,
          item.address,
          item.room_no,
          item.name,
          item.room_category,
          item.status_description,
          ...(item.options || []),
        ].join(" ").toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }

  function renderListings() {
    const list = filteredListings();
    $("listingCountSub").innerHTML =
      `총 <strong>${list.length}건</strong>의 직거래 매물이 조건과 일치합니다 (전체 공개 ${state.listings.length}건 · 중개수수료 0원).`;

    const grid = $("listingsGrid");
    if (!list.length) {
      grid.innerHTML = `
        <div class="or-pillar" style="grid-column:1/-1; text-align:center; padding:36px;">
          <div style="font-size:16px; font-weight:800; color:var(--or-navy); margin-bottom:6px;">조건에 맞는 공개 매물이 없습니다.</div>
          <div style="font-size:13.5px; color:var(--or-muted); margin-bottom:14px;">검색어나 예산 필터를 초기화하면 전체 직거래 공실을 다시 확인할 수 있습니다.</div>
          <button type="button" id="btnEmptyReset" class="or-btn or-btn--outline or-btn--sm">필터 전체 초기화</button>
        </div>
      `;
      const resetBtn = $("btnEmptyReset");
      if (resetBtn) {
        resetBtn.addEventListener("click", () => {
          $("searchKeyword").value = "";
          $("searchBranch").value = "";
          $("searchCategory").value = "";
          state.quickFilter = "all";
          state.budgetActive = false;
          document.querySelectorAll("#quickFilterBar [data-filter]").forEach((b) => {
            b.classList.toggle("is-active", b.dataset.filter === "all");
          });
          renderListings();
        });
      }
      return;
    }

    grid.innerHTML = list.map((l) => {
      const pdfUrl = l.terms_pdf_url || `/api/rent3/pool/listings/${encodeURIComponent(l.id)}/terms.pdf`;
      return `
      <article class="or-prop-card" data-room-id="${esc(l.id)}" data-room-no="${esc(l.room_no)}">
        <div class="or-prop-main">
          <div class="or-prop-media js-open-detail" data-room-id="${esc(l.id)}" role="button" tabindex="0" aria-label="${esc(l.building_name)} ${esc(l.room_no)}호 표준 임대조건 상세 보기">
            <img src="${esc(l.image)}" alt="${esc(l.building_name)} ${esc(l.room_no)}호">
            <div class="or-prop-badges">
              <span class="or-pill or-pill--green">복비 0원</span>
              ${l.pre_moveout_notice
                ? `<span class="or-pill or-pill--amber">📦 만기전출 사전공개 (${esc(l.available_from)})</span>`
                : `<span class="or-pill or-pill--navy">⚡ ${esc(l.available_from || "즉시입주")}</span>`}
              ${l.clean_title ? `<span class="or-pill or-pill--navy">🛡️ 등기클린</span>` : ""}
            </div>
            <div class="or-prop-photo-bar">
              <a href="${esc(pdfUrl)}" class="or-photo-pdf-link js-open-detail" data-room-id="${esc(l.id)}" title="표준 임대조건 상세 및 PDF 열기">📄 임대조건 PDF · 상세보기</a>
            </div>
          </div>
          <div class="or-prop-body">
            <div>
              <div style="font-size:12px; font-weight:700; color:var(--or-muted);">
                ${esc(l.building_name)} · ${esc(l.room_category)} · ${l.floor}${l.total_floors ? `/${l.total_floors}` : ""}층 · 전용 ${l.area_sqm}㎡${l.direction ? ` · ${esc(l.direction)}` : ""}
              </div>
              <h3 style="margin:4px 0 2px; font-size:17px; color:var(--or-navy);">
                [${esc(l.building_name)}] ${esc(l.name)}
              </h3>
              <div class="or-prop-price">
                보증금 ${fmtMan(l.deposit)} / 월 ${fmtMan(l.monthly_rent)}
                <span style="font-size:12.5px; font-weight:600; color:var(--or-muted);"> (관리비 ${fmtMan(l.maintenance_fee)})</span>
              </div>
              <div class="or-prop-meta">
                📍 ${esc(l.address || "주소 정보 등록됨")} · ${esc(l.pay_timing)} · 소유주 ${esc(l.lessor_name)}
              </div>
              <div class="or-tag-list">
                ${(l.options || []).slice(0, 5).map((opt) => `<span class="or-tag">${esc(opt)}</span>`).join("")}
              </div>
            </div>
          </div>
        </div>
        <div class="or-prop-actions" style="display:grid; grid-template-columns:1fr 1fr; gap:6px; padding:0 20px 18px;">
          <button type="button" class="or-btn or-btn--outline or-btn--sm js-open-inquiry"
            data-branch="${esc(l.branch)}" data-room="${esc(l.room_no)}" data-bldg="${esc(l.building_name)}">
            💬 1:1 방문문의
          </button>
          <button type="button" class="or-btn or-btn--green or-btn--sm js-open-rentnow"
            data-branch="${esc(l.branch)}" data-room="${esc(l.room_no)}" data-bldg="${esc(l.building_name)}"
            ${l.contract_in_progress ? 'disabled aria-disabled="true" title="다른 분과 계약이 진행 중입니다" style="opacity:.55;cursor:not-allowed;background:#94a3b8;border-color:#94a3b8;"' : ""}>
            ${l.contract_in_progress ? "🔒 계약 진행 중" : "🤝 스마트 계약 요청"}
          </button>
        </div>
      </article>
    `;
    }).join("");

    function openInquiryFor(l) {
      $("inqBranch").value = l.branch;
      $("inqRoomNo").value = l.room_no;
      $("inqModalTitle").textContent = `💬 [${l.building_name} ${l.room_no}호] 집주인 1:1 방문문의`;
      const u = getSession();
      if (u && u.name) $("inqSenderName").value = u.name;
      if (u && u.phone) $("inqSenderPhone").value = u.phone;
      $("inquiryModal").classList.add("is-open");
    }

    function openRentNowFor(l) {
      const u = getSession();
      if (!u || !u.name) {
        // 비로그인 시: 본인인증 로그인 후 해당 매물 호출 화면으로 즉시 이동!
        P.openAuthModal({
          tab: "login",
          preset: "cosign",
          targetHint: `contract:${l.branch}:${l.room_no}`,
        });
        return;
      }
      $("rnBranch").value = l.branch;
      $("rnRoomNo").value = l.room_no;
      const isLessor = u.role === "lessor" || u.role === "admin";
      $("rnCallerRole").value = isLessor ? "lessor" : "tenant";
      $("rnModalTitle").textContent = `📞 [${l.building_name} ${l.room_no}호] 상대방 호출 및 스마트 계약 시작`;
      $("rnVerifiedNotice").innerHTML =
        `🛡️ <strong>${esc(u.name)}</strong>님(본인인증 완료) 계정으로 <strong>${isLessor ? "임차인" : "임대인"}</strong>을 호출합니다.`;
      if (!isLessor) {
        $("rnTenantName").value = u.name || "";
        $("rnTenantPhone").value = u.phone || "";
      }
      $("rentNowModal").classList.add("is-open");
    }

    grid.querySelectorAll(".js-open-detail").forEach((el) => {
      const openIt = (e) => {
        if (e && (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1)) return;
        if (e) {
          e.preventDefault();
          e.stopPropagation();
        }
        const item = list.find((x) => x.id === el.dataset.roomId);
        if (item && window.R3ListingModal) {
          window.R3ListingModal.open(item, {
            onInquiry: openInquiryFor,
            onRentNow: openRentNowFor,
          });
        }
      };
      el.addEventListener("click", openIt);
      if (el.tagName !== "A") {
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") openIt(e);
        });
      }
    });

    grid.querySelectorAll(".js-open-inquiry").forEach((btn) => {
      btn.addEventListener("click", () => {
        openInquiryFor({
          branch: btn.dataset.branch,
          room_no: btn.dataset.room,
          building_name: btn.dataset.bldg,
        });
      });
    });

    grid.querySelectorAll(".js-open-rentnow").forEach((btn) => {
      btn.addEventListener("click", () => {
        openRentNowFor({
          branch: btn.dataset.branch,
          room_no: btn.dataset.room,
          building_name: btn.dataset.bldg,
        });
      });
    });
  }

  // Affordability Calculator
  // ☰ 더보기 > 계산기: 모달로 연다(예전엔 페이지 위쪽 패널을 펼쳐서, 메뉴가 닫힌 뒤 보고 있던 위치에서는 아무 일도 없어 보였다).
  // 폰의 뒤로가기( < )는 R3Hist 로 모달만 닫는다.
  function setAffordOpen(open) {
    const m = $("affordModal");
    if (open === m.classList.contains("is-open")) return;
    m.classList.toggle("is-open", open);
    $("btnToggleAfford").classList.toggle("is-active", open);
    if (window.R3Hist) {
      if (open) R3Hist.open("afford", () => { m.classList.remove("is-open"); $("btnToggleAfford").classList.remove("is-active"); });
      else R3Hist.close("afford");
    }
    if (open) updateAffordPreview();
  }

  function bindListingEvents() {
    // 검색 & 필터 바인딩
    const navFind = $("navFindListings");
    if (navFind) {
      navFind.addEventListener("click", (e) => {
        e.preventDefault();
        $("heroSearchForm").scrollIntoView({ behavior: "smooth", block: "start" });
        $("searchKeyword").focus({ preventScroll: true });
      });
    }
    $("heroSearchForm").addEventListener("submit", (e) => {
      e.preventDefault();
      renderListings();
      $("heroSearchForm").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("searchBranch").addEventListener("change", renderListings);
    $("searchCategory").addEventListener("change", renderListings);
    $("btnRefreshListings").addEventListener("click", function () {
      Bass.withLock(this, P.loadPortalData, "갱신 중...");
    });

    $("quickFilterBar").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-filter]");
      if (!chip) return;
      state.quickFilter = chip.dataset.filter;
      document.querySelectorAll("#quickFilterBar [data-filter]").forEach((b) => {
        b.classList.toggle("is-active", b === chip);
      });
      renderListings();
    });

    $("btnToggleAfford").addEventListener("click", () => setAffordOpen(true));
    $("btnCloseAfford").addEventListener("click", () => setAffordOpen(false));
    $("calcIncomeMan").addEventListener("input", updateAffordPreview);
    $("btnApplyBudget").addEventListener("click", () => {
      state.maxMonthlyWon = updateAffordPreview();
      state.maxDepositWon = Number($("calcDepositMan").value || 0) * 10000;
      state.budgetActive = true;
      renderListings();
      Bass.updateAppAlert(
        "success",
        `월세+관리비 ${fmtMan(state.maxMonthlyWon)} 이하 · 보증금 ${fmtMan(state.maxDepositWon)} 이하 조건으로 필터링했습니다.`
      );
      setAffordOpen(false);
      $("heroSearchForm").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("btnResetBudget").addEventListener("click", () => {
      state.budgetActive = false;
      renderListings();
      setAffordOpen(false);
      Bass.updateAppAlert("warning", "월세 예산 필터가 해제되었습니다.");
    });

    // 1:1 방문투어 문의 모달
    $("btnCloseInquiryModal").addEventListener("click", () => $("inquiryModal").classList.remove("is-open"));
    $("formInquiry").addEventListener("submit", (e) => {
      e.preventDefault();
      Bass.withLock($("btnSubmitInquiry"), async () => {
        const res = await Bass.api(APP_ID, "/pool/inquiries", {
          method: "POST",
          body: {
            branch: $("inqBranch").value,
            room_no: $("inqRoomNo").value,
            sender_name: $("inqSenderName").value,
            sender_phone: $("inqSenderPhone").value,
            visit_date: $("inqVisitDate").value,
            message: $("inqMessage").value,
          },
        });
        $("inquiryModal").classList.remove("is-open");
        $("inqMessage").value = "";
        Bass.updateAppAlert("success", res.message, "임대인 콘솔(rent3_lessor) 1:1 채팅함에 즉시 기록되었습니다.");
      }, "전송 중... ⏳");
    });

    // Rent Now 상대방 호출 모달
    $("btnCloseRentNowModal").addEventListener("click", () => $("rentNowModal").classList.remove("is-open"));
    $("formRentNow").addEventListener("submit", (e) => {
      e.preventDefault();
      const u = getSession();
      Bass.withLock($("btnSubmitRentNow"), async () => {
        const res = await Bass.api(APP_ID, "/pool/cosign-open", {
          method: "POST",
          body: {
            caller_role: $("rnCallerRole").value || "tenant",
            branch: $("rnBranch").value,
            room_no: $("rnRoomNo").value,
            tenant_name: $("rnTenantName").value,
            tenant_phone: $("rnTenantPhone").value,
            id_tenant: u && (u.tenant_id || (u.role !== "lessor" ? u.id : "")) ? (u.tenant_id || u.id) : "",
          },
        });
        $("rentNowModal").classList.remove("is-open");
        Bass.updateAppAlert("success", res.message);
        window.location.href = res.redirect_url;
      }, "상대방 호출 발송 중... ⏳");
    });

    $("affordModal").addEventListener("click", (e) => { if (e.target === $("affordModal")) setAffordOpen(false); });
  }

  Object.assign(P, {
    updateAffordPreview,
    filteredListings,
    renderListings,
    setAffordOpen,
    bindListingEvents,
  });
})();
