/* 전화번호 표기 통일: 01012345678 / 010 1234 5678 → 010-1234-5678 (화면 표시용, 데이터는 건드리지 않음) */
(function () {
  "use strict";
  var RE = /(^|[^0-9A-Za-z])(01[016789])[-\s.]?(\d{3,4})[-\s.]?(\d{4})(?![0-9A-Za-z])/g;
  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, INPUT: 1, CODE: 1 };
  function fmt(s) { return s.replace(RE, function (_, p, a, b, c) { return p + a + "-" + b + "-" + c; }); }
  function walk(node) {
    if (node.nodeType === 3) {
      var v = node.nodeValue;
      if (v && v.length > 9) { var n = fmt(v); if (n !== v) node.nodeValue = n; }
      return;
    }
    if (node.nodeType !== 1 || SKIP[node.tagName]) return;
    for (var c = node.firstChild; c; c = c.nextSibling) walk(c);
  }
  function isPhoneInput(el) {
    return el && el.tagName === "INPUT" && (el.type === "tel" || /phone|tel/i.test((el.id || "") + (el.name || "")));
  }
  function fmtInput(el) { var n = fmt(" " + el.value).slice(1); if (n !== el.value) el.value = n; }
  function run() {
    walk(document.body);
    document.querySelectorAll("input").forEach(function (el) { if (isPhoneInput(el) && el.value) fmtInput(el); });
  }
  var busy = false, timer = 0;
  function later() { clearTimeout(timer); timer = setTimeout(function () { if (busy) return; busy = true; try { run(); } finally { busy = false; } }, 60); }
  document.addEventListener("focusout", function (e) { if (isPhoneInput(e.target)) fmtInput(e.target); }, true);
  function start() { run(); new MutationObserver(later).observe(document.body, { childList: true, subtree: true, characterData: true }); }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
