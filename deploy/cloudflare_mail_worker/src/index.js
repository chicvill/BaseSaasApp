/**
 * Rent3 입금 메일 Worker — Cloudflare Email Routing 의 catch-all 규칙에 연결한다.
 *
 * 하는 일: 받은 메일 → (본문·제목·보낸 주소·메시지 ID·수신 주소) 추출 → Cloudflare 가 붙인 최상단 Authentication-Results 에서
 *          DKIM 통과 도메인 추출 → JSON → HMAC-SHA256 서명 → POST WEBHOOK_URL.
 * 하지 않는 일: 메일 저장·전달·답장. 원문은 이 Worker 에 남지 않는다(서버도 원문을 저장하지 않는다).
 *
 * 신뢰 경계: Authentication-Results 는 '가장 위에 있는 것'만 쓴다(보낸 사람이 끼워 넣은 가짜 헤더는 아래쪽에 있다).
 * 서버는 이 Worker 의 서명(RENT3_MAIL_SECRET)을 믿으므로 비밀키가 새면 위조가 가능해진다 — 저장소에 커밋하지 말 것.
 */
import PostalMime from "postal-mime";

const MAX_RAW = 1_000_000;      // 1MB 넘는 메일은 버린다(서버 한도와 같게)
const MAX_TEXT = 20_000;

/** "...; dkim=pass header.d=nonghyup.com; ..." 에서 dkim=pass 인 header.d 도메인들. */
export function dkimPassDomains(authResults) {
  const out = [];
  for (const part of String(authResults || "").split(";")) {
    if (/\bdkim\s*=\s*pass\b/i.test(part)) {
      const m = part.match(/header\.(?:d|i)\s*=\s*@?([A-Za-z0-9.\-]+)/i);
      if (m) out.push(m[1].toLowerCase().replace(/^.*@/, ""));
    }
  }
  return [...new Set(out)];
}

/**
 * 파싱된 헤더 목록(위에서 아래 순서)에서 인증 결과를 고른다.
 *  - AUTHSERV_ID 가 없으면 **어떤 헤더도 믿지 않는다**(value 비움 → 서버는 '발신 확인 불가'로 확인 대기 처리). 로그용으로 맨 위 헤더만 `seen` 에 돌려준다.
 *  - AUTHSERV_ID 가 있으면 **맨 위** Authentication-Results 가 그 서버 것일 때만 믿는다(보낸 사람이 끼워 넣은 가짜는 그 아래에 있다).
 */
export function pickAuthResults(headers, authservId) {
  const list = (headers || []).filter((h) => String(h.key || "").toLowerCase() === "authentication-results");
  if (!list.length) return { value: "", seen: "", warn: "no-authentication-results" };
  const top = String(list[0].value || "");
  if (!authservId) return { value: "", seen: top, warn: "authserv-id-not-configured(신뢰하지 않음)" };
  if (top.trim().toLowerCase().startsWith(authservId.toLowerCase())) return { value: top, seen: top };
  return { value: "", seen: top, warn: "top-header-not-from-authserv-id" };
}

async function hmacHex(secret, body) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export default {
  async email(message, env) {
    if (!env.RENT3_MAIL_SECRET || !env.WEBHOOK_URL) { console.log("설정 누락: RENT3_MAIL_SECRET / WEBHOOK_URL"); return; }
    if (message.rawSize > MAX_RAW) { console.log("메일이 너무 커서 버림", message.rawSize); return; }
    const raw = await new Response(message.raw).arrayBuffer();
    const mail = await PostalMime.parse(raw);
    const auth = pickAuthResults(mail.headers, env.AUTHSERV_ID);
    if (env.DEBUG_HEADERS === "1") {
      console.log("headers:", (mail.headers || []).map((h) => h.key).join(","));
      console.log("auth-results(맨 위):", String(auth.seen || "").slice(0, 300), "| 사용:", auth.value ? "예" : "아니오", auth.warn || "");
    }
    const payload = {
      to: message.to,
      message_id: mail.messageId || "",
      from_addr: message.from,
      dkim_pass_domains: dkimPassDomains(auth.value),
      subject: mail.subject || "",
      text: String(mail.text || mail.html || "").slice(0, MAX_TEXT),
      ts: Date.now() / 1000,
    };
    const body = JSON.stringify(payload);
    const res = await fetch(env.WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Rent3-Signature": await hmacHex(env.RENT3_MAIL_SECRET, body) },
      body,
    });
    console.log("webhook", res.status, (await res.text()).slice(0, 200));
  },
};
