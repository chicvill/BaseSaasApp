/* apps/rent3_admin/frontend/core.js — 플랫폼 총괄 관리인 앱 공통 상태·포맷 헬퍼 (window.R3A) */
(function () {
  "use strict";

  const APP_ID = "rent3_admin";
  const elTabContent = document.getElementById("tabContent");

  function fmtWon(n) {
    return Number(n || 0).toLocaleString("ko-KR") + "원";
  }

  function fmtKb(n) {
    return n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
  }

  window.R3A = {
    APP_ID,
    elTabContent,
    state: {
      currentTab: "support",
      ov: null,
      sp: { branch: "", roomNo: "", user: "", cat: "사용법 문의" },
      spTimer: null,
    },
    fmtWon,
    fmtKb,
  };
})();
