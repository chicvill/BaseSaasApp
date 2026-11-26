/* apps/rent_dist/rent3/frontend/listing_modal.js — 매물 사진 클릭 시 열리는 좌우 스크롤 상세·표준 임대조건 PDF 모달 (포털·임차인·계약 앱 공용) */
(function () {
  "use strict";

  const DEFAULT_IMG = "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80";

  function esc(v) {
    if (window.Bass && Bass.escapeHtml) return Bass.escapeHtml(v == null ? "" : String(v));
    return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function fmtWon(amt) {
    const n = Number(amt || 0);
    if (!n) return "0원";
    const man = Math.floor(n / 10000);
    const rem = n % 10000;
    if (man > 0 && rem === 0) return `${man.toLocaleString("ko-KR")}만원`;
    if (man > 0) return `${man.toLocaleString("ko-KR")}만 ${rem.toLocaleString("ko-KR")}원`;
    return `${n.toLocaleString("ko-KR")}원`;
  }

  function ensureStyle() {
    if (document.getElementById("r3ListingModalStyle")) return;
    const style = document.createElement("style");
    style.id = "r3ListingModalStyle";
    style.textContent = `
      .r3-lm-backdrop {
        position: fixed; inset: 0; z-index: 360;
        background: rgba(15, 23, 42, 0.62);
        backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
        display: flex; align-items: center; justify-content: center;
        padding: 12px;
      }
      .r3-lm-card {
        width: 100%; max-width: 680px; max-height: 92vh;
        background: #ffffff; color: #0f172a;
        border-radius: 20px; border: 1px solid #e2e8f0;
        box-shadow: 0 22px 48px rgba(15, 23, 42, 0.28);
        display: flex; flex-direction: column; overflow: hidden;
        font-family: "Pretendard", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      .r3-lm-head {
        display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;
        padding: 14px 18px 10px; border-bottom: 1px solid #e2e8f0; background: #ffffff;
      }
      .r3-lm-title { margin: 0; font-size: 17px; font-weight: 800; color: #0f172a; line-height: 1.3; }
      .r3-lm-sub { font-size: 12.5px; color: #64748b; margin-top: 3px; }
      .r3-lm-close {
        flex: none; width: 36px; height: 36px; border-radius: 9999px;
        border: 1px solid #e2e8f0; background: #f8fafc; color: #334155;
        font-size: 18px; font-weight: 700; cursor: pointer;
        display: inline-flex; align-items: center; justify-content: center;
      }
      .r3-lm-tabs {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
        padding: 8px 14px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
      }
      .r3-lm-tab {
        border: 1px solid #e2e8f0; background: #ffffff; color: #475569;
        border-radius: 9999px; padding: 7px 6px; font-size: 12.5px; font-weight: 700;
        cursor: pointer; white-space: nowrap; transition: all .16s ease;
      }
      .r3-lm-tab.is-active {
        background: #0f172a; color: #ffffff; border-color: #0f172a;
      }
      .r3-lm-stage {
        position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column;
      }
      .r3-lm-track {
        display: flex; flex: 1; min-height: 0;
        overflow-x: auto; overflow-y: hidden;
        scroll-snap-type: x mandatory; scroll-behavior: smooth;
        -webkit-overflow-scrolling: touch;
      }
      .r3-lm-track::-webkit-scrollbar { height: 6px; }
      .r3-lm-track::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 9999px; }
      .r3-lm-slide {
        flex: 0 0 100%; width: 100%; min-width: 100%;
        scroll-snap-align: start; scroll-snap-stop: always;
        overflow-y: auto; padding: 14px 18px 18px; box-sizing: border-box;
      }
      .r3-lm-photo-wrap {
        position: relative; border-radius: 14px; overflow: hidden;
        aspect-ratio: 16 / 9; background: #f1f5f9; margin-bottom: 12px;
      }
      .r3-lm-photo-wrap img {
        width: 100%; height: 100%; object-fit: cover; display: block;
      }
      .r3-lm-photo-pdf {
        position: absolute; right: 10px; bottom: 10px;
        background: rgba(15, 23, 42, 0.88); color: #ffffff;
        padding: 7px 12px; border-radius: 9999px; text-decoration: none;
        font-size: 12.5px; font-weight: 700; display: inline-flex; align-items: center; gap: 5px;
        border: 1px solid rgba(255, 255, 255, 0.22);
      }
      .r3-lm-photo-pdf:hover { background: #1d4ed8; color: #ffffff; }
      .r3-lm-kpi {
        display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 12px;
      }
      .r3-lm-kpi-item {
        background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 9px 11px;
      }
      .r3-lm-kpi-lbl { font-size: 12px; color: #64748b; font-weight: 600; }
      .r3-lm-kpi-val { font-size: 15px; color: #0f172a; font-weight: 800; margin-top: 2px; }
      .r3-lm-table {
        width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px;
      }
      .r3-lm-table th, .r3-lm-table td {
        border: 1px solid #e2e8f0; padding: 7px 10px; text-align: left; vertical-align: top;
      }
      .r3-lm-table th {
        width: 30%; background: #f8fafc; color: #334155; font-weight: 700;
      }
      .r3-lm-fac-grid {
        display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 12px;
      }
      .r3-lm-fac {
        border: 1px solid #e2e8f0; border-radius: 12px; padding: 10px 12px; background: #ffffff;
        display: flex; justify-content: space-between; align-items: center; font-size: 13px;
      }
      .r3-lm-fac strong { color: #0f172a; }
      .r3-lm-pill {
        display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 9999px;
        font-size: 12px; font-weight: 700; border: 1px solid #e2e8f0; background: #f8fafc; color: #1e293b;
      }
      .r3-lm-pill--ok { background: #dcfce7; border-color: #86efac; color: #166534; }
      .r3-lm-pill--warn { background: #fef3c7; border-color: #fde68a; color: #92400e; }
      .r3-lm-pill--no { background: #fee2e2; border-color: #fecaca; color: #991b1b; }
      .r3-lm-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 12px; }
      .r3-lm-sec { font-size: 14px; font-weight: 800; color: #0f172a; margin: 0 0 8px; }
      .r3-lm-clause-list {
        display: grid; gap: 7px; margin-bottom: 12px;
      }
      .r3-lm-clause {
        padding: 9px 11px; border-radius: 10px; background: #fffbeb; border: 1px solid #fde68a;
        font-size: 13px; line-height: 1.5; color: #1e293b;
      }
      .r3-lm-pdf-box {
        background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 14px;
        padding: 12px 14px; margin-top: 8px;
      }
      .r3-lm-pdf-frame {
        width: 100%; height: 360px; border: 1px solid #cbd5e1; border-radius: 10px;
        background: #f8fafc; margin-top: 10px; display: block;
      }
      .r3-lm-pager {
        display: flex; justify-content: space-between; align-items: center; gap: 8px;
        padding: 8px 16px; background: #f8fafc; border-top: 1px solid #e2e8f0;
      }
      .r3-lm-dots { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #475569; font-weight: 700; }
      .r3-lm-dot {
        width: 8px; height: 8px; border-radius: 9999px; background: #cbd5e1; border: none; padding: 0; cursor: pointer;
      }
      .r3-lm-dot.is-active { width: 20px; background: #0f172a; }
      .r3-lm-foot {
        display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center;
        padding: 10px 16px 12px; border-top: 1px solid #e2e8f0; background: #ffffff;
      }
      .r3-lm-btn {
        display: inline-flex; align-items: center; justify-content: center; gap: 5px;
        padding: 9px 14px; border-radius: 9999px; border: 1px solid #cbd5e1;
        background: #ffffff; color: #0f172a; font-size: 13px; font-weight: 700;
        text-decoration: none; cursor: pointer; min-height: 38px;
      }
      .r3-lm-btn:hover { border-color: #0f172a; }
      .r3-lm-btn--primary { background: #1d4ed8; color: #ffffff; border-color: #1d4ed8; }
      .r3-lm-btn--primary:hover { background: #1e40af; color: #ffffff; }
      .r3-lm-btn--dark { background: #0f172a; color: #ffffff; border-color: #0f172a; }
      .r3-lm-btn--dark:hover { background: #000000; color: #ffffff; }
      @media (max-width: 560px) {
        .r3-lm-card { max-height: 94vh; border-radius: 16px; }
        .r3-lm-slide { padding: 12px 14px 14px; }
        .r3-lm-tab { font-size: 12px; padding: 6px 4px; }
        .r3-lm-foot .r3-lm-btn { flex: 1 1 auto; }
      }
    `;
    document.head.appendChild(style);
  }

  function facBadgeClass(val) {
    if (val === "설치됨" || val === "가능") return "r3-lm-pill r3-lm-pill--ok";
    if (val === "미설치" || val === "없음" || val === "불가") return "r3-lm-pill r3-lm-pill--no";
    return "r3-lm-pill r3-lm-pill--warn";
  }

  function open(listing, opts) {
    if (!listing) return;
    ensureStyle();
    close();

    const options = opts || {};
    const spec = listing.listing_spec || {};
    const pdfUrl = listing.terms_pdf_url || `/api/rent3/pool/listings/${encodeURIComponent(listing.id)}/terms.pdf`;
    const downloadUrl = `${pdfUrl}${pdfUrl.includes("?") ? "&" : "?"}download=1`;
    const imgs = Array.isArray(listing.images) && listing.images.length ? listing.images : [listing.image || DEFAULT_IMG];
    const optItems = Array.isArray(spec.options) && spec.options.length ? spec.options : (Array.isArray(listing.options) ? listing.options : []);
    const maintInc = Array.isArray(spec.maintenance_includes) ? spec.maintenance_includes : [];
    const areaSqm = Number(listing.area_sqm || 0);
    const pyeong = areaSqm > 0 ? (areaSqm / 3.3058).toFixed(1) : "";
    const floorStr = `${listing.floor || "-"}층${listing.total_floors ? ` / 전체 ${listing.total_floors}층` : ""}`;
    const dep = Number(listing.deposit || 0);
    const contractDep = Math.floor(dep / 10);
    const cleanFee = Number(listing.cleaning_fee || 0);
    const noPets = spec.no_pets !== false;
    const noSmoking = spec.no_smoking !== false;
    const term2m = spec.terminate_on_2m_arrears !== false;
    const noSublease = spec.no_sublease !== false;
    const termMonths = Number(spec.lease_term_months || 12);

    const wrap = document.createElement("div");
    wrap.id = "r3ListingDetailModal";
    wrap.className = "r3-lm-backdrop";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", `${listing.building_name || ""} ${listing.room_no || ""}호 임대조건 상세`);

    wrap.innerHTML = `
      <div class="r3-lm-card">
        <div class="r3-lm-head">
          <div style="min-width:0;">
            <h3 class="r3-lm-title">🏠 [${esc(listing.building_name || "")}] ${esc(listing.name || `${listing.room_no}호`)}</h3>
            <div class="r3-lm-sub">📍 ${esc(listing.address || "주소 정보 등록됨")} · ${esc(listing.room_category || "주거용 원룸")} · 좌우 스크롤로 전체 임대조건을 확인하세요</div>
          </div>
          <button type="button" class="r3-lm-close" id="r3LmClose" aria-label="닫기">✕</button>
        </div>

        <div class="r3-lm-tabs" role="tablist">
          <button type="button" class="r3-lm-tab is-active" data-slide="0" role="tab" aria-selected="true">① 사진 · 핵심조건</button>
          <button type="button" class="r3-lm-tab" data-slide="1" role="tab" aria-selected="false">② 시설 · 옵션 · 관리비</button>
          <button type="button" class="r3-lm-tab" data-slide="2" role="tab" aria-selected="false">③ 표준 특약 · PDF</button>
        </div>

        <div class="r3-lm-stage">
          <div class="r3-lm-track" id="r3LmTrack">
            <!-- 슬라이드 1: 사진 & 핵심 임대조건 -->
            <section class="r3-lm-slide" data-slide-idx="0">
              <div class="r3-lm-photo-wrap">
                <img src="${esc(imgs[0])}" alt="${esc(listing.building_name || "")} ${esc(listing.room_no || "")}호 매물 사진">
                <a href="${esc(pdfUrl)}" target="_blank" rel="noopener" class="r3-lm-photo-pdf">📄 표준 임대조건서 PDF 열기 ↗</a>
              </div>
              <div class="r3-lm-kpi">
                <div class="r3-lm-kpi-item">
                  <div class="r3-lm-kpi-lbl">임대보증금 (계약금 10% ${fmtWon(contractDep)})</div>
                  <div class="r3-lm-kpi-val">${fmtWon(dep)}</div>
                </div>
                <div class="r3-lm-kpi-item">
                  <div class="r3-lm-kpi-lbl">월세 (${esc(listing.pay_timing || "후불")})</div>
                  <div class="r3-lm-kpi-val">${fmtWon(listing.monthly_rent)}</div>
                </div>
                <div class="r3-lm-kpi-item">
                  <div class="r3-lm-kpi-lbl">월 관리비</div>
                  <div class="r3-lm-kpi-val">${fmtWon(listing.maintenance_fee)}</div>
                </div>
                <div class="r3-lm-kpi-item">
                  <div class="r3-lm-kpi-lbl">퇴실 청소비 / 입주 가능일</div>
                  <div class="r3-lm-kpi-val">${cleanFee > 0 ? fmtWon(cleanFee) : "약정 없음"} · ${esc(listing.available_from || "즉시입주")}</div>
                </div>
              </div>
              <table class="r3-lm-table">
                <tr><th>소재지(주소)</th><td>${esc(listing.address || "-")} (${esc(listing.building_name || "")} ${esc(listing.room_no || "")}호)</td></tr>
                <tr><th>전용면적 · 층/향</th><td>전용 ${areaSqm || "-"}㎡${pyeong ? ` (약 ${pyeong}평)` : ""} · ${esc(floorStr)}${listing.direction ? ` · ${esc(listing.direction)}` : ""}</td></tr>
                <tr><th>임대기간 · 유형</th><td>기본 ${termMonths}개월 · ${esc(listing.room_category || "주거용 원룸")}${listing.vat_applicable ? " (부가세 별도)" : ""}</td></tr>
                <tr><th>등기부 · 임대인</th><td>${listing.clean_title ? "🛡️ 등기부 확인 완료(소유자 일치·클린)" : "계약 시 등기부등본 직접 대조"} · 소유주 ${esc(listing.lessor_name || "건물 소유주")}</td></tr>
              </table>
              <div class="r3-lm-pdf-box" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;">
                <div style="font-size:13px;font-weight:700;color:#1e3a8a;">📄 표준임대차계약서 참조 임대조건 안내서 (PDF)</div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                  <a href="${esc(pdfUrl)}" target="_blank" rel="noopener" class="r3-lm-btn r3-lm-btn--primary">📄 PDF 보기</a>
                  <a href="${esc(downloadUrl)}" download class="r3-lm-btn">⬇️ 저장</a>
                </div>
              </div>
            </section>

            <!-- 슬라이드 2: 옵션 리스트 · 시설 설치 여부 · 관리비 포함 내역 -->
            <section class="r3-lm-slide" data-slide-idx="1">
              <h4 class="r3-lm-sec">🏢 건물 · 호실 설비 설치 여부</h4>
              <div class="r3-lm-fac-grid">
                <div class="r3-lm-fac"><strong>📺 케이블 TV</strong><span class="${facBadgeClass(spec.cable_tv || "미확인")}">${esc(spec.cable_tv || "미확인")}</span></div>
                <div class="r3-lm-fac"><strong>🌐 인터넷 / Wi-Fi</strong><span class="${facBadgeClass(spec.internet || "미확인")}">${esc(spec.internet || "미확인")}</span></div>
                <div class="r3-lm-fac"><strong>🛗 엘리베이터</strong><span class="${facBadgeClass(spec.elevator || "미확인")}">${esc(spec.elevator || "미확인")}</span></div>
                <div class="r3-lm-fac"><strong>🚗 주차 가능</strong><span class="${facBadgeClass(spec.parking || "미확인")}">${esc(spec.parking || "미확인")}</span></div>
              </div>

              <h4 class="r3-lm-sec">🛋️ 내부 옵션 리스트</h4>
              <div class="r3-lm-chips">
                ${optItems.length
                  ? optItems.map((o) => `<span class="r3-lm-pill">✅ ${esc(o)}</span>`).join("")
                  : `<span class="r3-lm-pill">현장 확인 및 계약 시 옵션 체크리스트 확정</span>`}
              </div>

              <h4 class="r3-lm-sec">🧾 관리비 포함 내역 (월 ${fmtWon(listing.maintenance_fee)})</h4>
              <div class="r3-lm-chips">
                ${maintInc.length
                  ? maintInc.map((m) => `<span class="r3-lm-pill r3-lm-pill--ok">✔ ${esc(m)}</span>`).join("")
                  : `<span class="r3-lm-pill">별도 협의 (개별 공과금 별도)</span>`}
              </div>
              ${spec.maintenance_extra
                ? `<div style="font-size:13px;color:#334155;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:9px 11px;margin-bottom:10px;">📌 관리비 비고: ${esc(spec.maintenance_extra)}</div>`
                : ""}
              ${listing.status_description
                ? `<div style="font-size:13px;color:#334155;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:9px 11px;">💬 매물 소개: ${esc(listing.status_description)}</div>`
                : ""}
            </section>

            <!-- 슬라이드 3: 표준 특약 · 계약 해지 사유 & PDF 문서 미리보기 -->
            <section class="r3-lm-slide" data-slide-idx="2">
              <h4 class="r3-lm-sec">⚖️ 표준 임대조건 · 특약 및 계약 해지 사유</h4>
              <div class="r3-lm-clause-list">
                <div class="r3-lm-clause">
                  <strong>🐾 반려동물(동물) 사육:</strong>
                  ${noPets ? "금지 (위반 시 원상복구 및 손해배상 책임 부담)" : "임대인과 사전 협의 가능"}
                </div>
                <div class="r3-lm-clause">
                  <strong>🚭 실내 흡연:</strong>
                  ${noSmoking ? "금지 (호실 내부 및 건물 공용공간 금연)" : "별도 협의"}
                </div>
                <div class="r3-lm-clause">
                  <strong>⚖️ 2개월(2기) 이상 차임 체납 시 계약 해지:</strong>
                  ${term2m ? "적용 — 차임 연체액이 2기(2개월)에 달하는 때에는 임대인이 계약을 해지할 수 있음 (주택임대차보호법 제10조의8 · 민법 제640조)" : "관계 법령 일반 규정에 따름"}
                </div>
                <div class="r3-lm-clause">
                  <strong>🚫 무단 전대 · 양도:</strong>
                  ${noSublease ? "임대인의 서면 동의 없는 무단 전대·양도·용도변경 금지" : "임대인과 별도 서면 합의"}
                </div>
                <div class="r3-lm-clause">
                  <strong>🧹 원상복구 및 퇴실 청소비:</strong>
                  자연 마모에 의한 시설 고장은 임대인이 무상 수리하며 통상 손모는 공제하지 않음.${cleanFee > 0 ? ` (약정 퇴실 청소비 <strong>${fmtWon(cleanFee)}</strong>은 퇴실 시 보증금에서 공제)` : ""}
                </div>
                ${spec.custom_clauses
                  ? `<div class="r3-lm-clause"><strong>📝 임대인 추가 특약:</strong><br>${esc(spec.custom_clauses).replace(/\n/g, "<br>")}</div>`
                  : ""}
              </div>

              <div class="r3-lm-pdf-box">
                <div style="font-size:13.5px;font-weight:800;color:#1e3a8a;margin-bottom:6px;">📄 표준 임대조건 안내서 PDF 파일</div>
                <div style="font-size:12.5px;color:#334155;margin-bottom:10px;">
                  주소·전용면적·보증금·옵션·케이블TV·인터넷·엘리베이터·관리비 포함내역·입주가능일·특약이 담긴 A4 PDF 문서입니다.
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:8px;">
                  <a href="${esc(pdfUrl)}" target="_blank" rel="noopener" class="r3-lm-btn r3-lm-btn--primary">📄 새 창에서 PDF 보기 ↗</a>
                  <a href="${esc(downloadUrl)}" download class="r3-lm-btn">⬇️ PDF 다운로드</a>
                  <button type="button" id="r3LmToggleInlinePdf" class="r3-lm-btn">👁️ 여기서 PDF 미리보기</button>
                </div>
                <div id="r3LmPdfHost" style="display:none;"></div>
              </div>
            </section>
          </div>

          <div class="r3-lm-pager">
            <button type="button" id="r3LmPrev" class="r3-lm-btn" style="padding:5px 12px;min-height:34px;">‹ 이전</button>
            <div class="r3-lm-dots" id="r3LmDots">
              <button type="button" class="r3-lm-dot is-active" data-slide="0" aria-label="1번 슬라이드"></button>
              <button type="button" class="r3-lm-dot" data-slide="1" aria-label="2번 슬라이드"></button>
              <button type="button" class="r3-lm-dot" data-slide="2" aria-label="3번 슬라이드"></button>
              <span id="r3LmPageNum" style="margin-left:4px;">1 / 3</span>
            </div>
            <button type="button" id="r3LmNext" class="r3-lm-btn" style="padding:5px 12px;min-height:34px;">다음 ›</button>
          </div>
        </div>

        <div class="r3-lm-foot">
          <a href="${esc(pdfUrl)}" target="_blank" rel="noopener" class="r3-lm-btn">📄 임대조건 PDF</a>
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            ${options.onInquiry ? `<button type="button" id="r3LmInquiry" class="r3-lm-btn">💬 1:1 방문문의</button>` : ""}
            ${options.onRentNow
              ? `<button type="button" id="r3LmRentNow" class="r3-lm-btn r3-lm-btn--dark" ${listing.contract_in_progress ? "disabled style='opacity:.55;cursor:not-allowed;'" : ""}>${listing.contract_in_progress ? "🔒 계약 진행 중" : esc(options.rentNowLabel || "📞 공동계약 호출")}</button>`
              : ""}
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(wrap);

    const track = wrap.querySelector("#r3LmTrack");
    const tabs = [...wrap.querySelectorAll(".r3-lm-tab")];
    const dots = [...wrap.querySelectorAll(".r3-lm-dot")];
    const pageNum = wrap.querySelector("#r3LmPageNum");
    const btnPrev = wrap.querySelector("#r3LmPrev");
    const btnNext = wrap.querySelector("#r3LmNext");
    let curIdx = 0;

    function syncSlideUI(idx) {
      curIdx = Math.max(0, Math.min(2, idx));
      tabs.forEach((t, i) => {
        const active = i === curIdx;
        t.classList.toggle("is-active", active);
        t.setAttribute("aria-selected", active ? "true" : "false");
      });
      dots.forEach((d, i) => d.classList.toggle("is-active", i === curIdx));
      if (pageNum) pageNum.textContent = `${curIdx + 1} / 3`;
      if (btnPrev) btnPrev.disabled = curIdx === 0;
      if (btnNext) btnNext.disabled = curIdx === 2;
    }

    function goSlide(idx) {
      const target = Math.max(0, Math.min(2, idx));
      syncSlideUI(target);
      if (track) track.scrollTo({ left: target * track.clientWidth, behavior: "smooth" });
    }

    tabs.forEach((t) => t.addEventListener("click", () => goSlide(Number(t.dataset.slide || 0))));
    dots.forEach((d) => d.addEventListener("click", () => goSlide(Number(d.dataset.slide || 0))));
    if (btnPrev) btnPrev.addEventListener("click", () => goSlide(curIdx - 1));
    if (btnNext) btnNext.addEventListener("click", () => goSlide(curIdx + 1));

    let scrollTimer = null;
    if (track) {
      track.addEventListener("scroll", () => {
        if (scrollTimer) clearTimeout(scrollTimer);
        scrollTimer = setTimeout(() => {
          const w = track.clientWidth || 1;
          syncSlideUI(Math.round(track.scrollLeft / w));
        }, 50);
      }, { passive: true });
    }

    const btnInlinePdf = wrap.querySelector("#r3LmToggleInlinePdf");
    const pdfHost = wrap.querySelector("#r3LmPdfHost");
    if (btnInlinePdf && pdfHost) {
      btnInlinePdf.addEventListener("click", () => {
        const shown = pdfHost.style.display !== "none";
        if (shown) {
          pdfHost.style.display = "none";
          btnInlinePdf.textContent = "👁️ 여기서 PDF 미리보기";
          return;
        }
        if (!pdfHost.firstChild) {
          pdfHost.innerHTML = `<iframe class="r3-lm-pdf-frame" src="${esc(pdfUrl)}#view=FitH" title="표준 임대조건 안내서 PDF"></iframe>`;
        }
        pdfHost.style.display = "block";
        btnInlinePdf.textContent = "🙈 미리보기 접기";
      });
    }

    const onKey = (e) => {
      if (!document.getElementById("r3ListingDetailModal")) {
        document.removeEventListener("keydown", onKey);
        return;
      }
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") goSlide(curIdx + 1);
      else if (e.key === "ArrowLeft") goSlide(curIdx - 1);
    };
    document.addEventListener("keydown", onKey);
    wrap._r3KeyHandler = onKey;

    wrap.querySelector("#r3LmClose").addEventListener("click", close);
    wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });

    const btnInq = wrap.querySelector("#r3LmInquiry");
    if (btnInq && options.onInquiry) {
      btnInq.addEventListener("click", () => { close(); options.onInquiry(listing); });
    }
    const btnRn = wrap.querySelector("#r3LmRentNow");
    if (btnRn && options.onRentNow) {
      btnRn.addEventListener("click", () => { close(); options.onRentNow(listing); });
    }

    if (window.R3Hist) R3Hist.open("listingDetail", close);
    syncSlideUI(Number(options.initialSlide || 0));
    if (options.initialSlide) {
      setTimeout(() => goSlide(Number(options.initialSlide)), 20);
    }
  }

  function close() {
    const el = document.getElementById("r3ListingDetailModal");
    if (!el) return;
    if (el._r3KeyHandler) document.removeEventListener("keydown", el._r3KeyHandler);
    el.remove();
    if (window.R3Hist) R3Hist.close("listingDetail");
  }

  window.R3ListingModal = { open, close };
})();
