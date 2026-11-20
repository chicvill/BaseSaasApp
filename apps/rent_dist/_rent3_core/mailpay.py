"""
apps/rent_dist/_rent3_core/mailpay.py — 은행 입금 알림 메일 자동 정리(임대인 선택 기능, 기본 꺼짐).

흐름: 임대인이 동의하고 은행·계좌 끝 4자리를 입력 → 전용 수신 주소(pay-<토큰>@수신도메인) 발급 → 은행 알림 메일을 그 주소로 자동 전달.
수신 서비스(Cloudflare Email Worker 등)가 서명한 웹훅으로 이 모듈의 ingest() 를 부른다.

판정(원문은 저장하지 않는다 — 입금액·입금자명·일자·호실·메일 해시·처리 결과만 90일 보관):
  토큰·동의 확인 → 중복(메일 해시) → 발신 확인(원본 DKIM 도메인 = 은행 도메인; 자동 전달이라 SPF 는 보지 않는다)
  → 해석(mailparse 어댑터) → 입금 여부 → 계좌 끝자리 → 장부 대조(check_sms 와 같은 규칙) → 자동 조건이면 수납, 아니면 확인 대기.
수납 엔진(billing.py)은 그대로 쓴다: collect(method="BANK_MAIL", raw_sms="mail:<해시>") — 같은 메일은 영구 중복 차단.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError, NotFound
from . import mailparse as mp
from .billing import FifoPaymentEngine
from .models import Contract, Lessor, current_ym, date_today
from .pool import PoolObj, now_iso

CONSENT_VER = "2026-11-20"
CONSENT_TEXT = (
    "입금 알림 메일에서 입금액·입금자명·입금일(과 메모에 적힌 호실)만 읽어 수납 처리에 사용합니다. "
    "메일 원문은 저장하지 않고, 처리 결과(입금액·입금자명·일자·호실)만 90일 보관한 뒤 지웁니다. "
    "언제든 이 기능을 끄면 수신 주소가 폐기되고 보관 자료가 즉시 삭제됩니다. 입금자명은 임차인의 개인정보이므로 수납 확인 목적 외에는 쓰지 않습니다."
)
RETENTION_DAYS = 90
DAILY_CAP = 30          # 임대인별 하루 자동 수납 상한
RECENT_DAYS = 3         # 자동 수납은 입금일이 이 일수 이내일 때만
SIG_MAX_AGE_SEC = 600   # 웹훅 서명 시각 허용 오차

FORWARD_CODE_MIN = 60   # 지메일 전달 확인 코드 보관 분(1시간)
GMAIL_FORWARD_SENDER = "forwarding-noreply@google.com"
STATUS_KO = {"AUTO_APPLIED": "자동 수납", "USER_APPLIED": "확인 후 수납", "PENDING": "확인 대기", "IGNORED": "무시됨", "FAILED": "해석 실패"}


class MailIntake(PoolObj):
    """수신한 입금 메일 1통의 처리 기록. 원문은 없다."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "입금 메일"

    id: str = Field(default_factory=lambda: f"mi_{uuid4().hex[:8]}")
    lessor_id: str = ""
    branch: str = "main"
    msg_hash: str = ""
    received_at: str = Field(default_factory=now_iso)
    from_domain: str = ""
    auth_ok: bool = False                # 원본 DKIM 도메인이 은행 도메인과 일치하고 통과
    is_test: bool = False                # 테스트 도메인으로 받은 메일(테스트 지점 전용) — 자동 수납 금지
    status: str = "PENDING"              # AUTO_APPLIED | USER_APPLIED | PENDING | IGNORED | FAILED
    reason: str = ""                     # 확인 대기·무시·실패 사유(개인정보 없음)
    amount: int = 0
    payer_name: str = ""
    paid_date: str = ""
    room_no: str = ""
    pay_id: str = ""
    purge_at: str = ""


def _today_iso() -> str:
    return date_today().isoformat()


def _purge_at() -> str:
    return (date_today() + timedelta(days=RETENTION_DAYS)).isoformat()


def purge_due() -> int:
    """보관 기간이 지난 처리 기록을 지운다(스케줄러가 없어 설정 조회·수신 때 부른다)."""
    n = 0
    for x in MailIntake.all():
        if x.purge_at and x.purge_at < _today_iso():
            x.delete(action="mail_intake_purged", app_source="rent3_mail")
            n += 1
    return n


def _env_list(name: str) -> List[str]:
    return [x.strip().lower() for x in (os.getenv(name) or "").split(",") if x.strip()]


def test_domains_for(lsr: Lessor) -> List[str]:
    """테스트 전용 허용 도메인(RENT3_MAIL_EXTRA_DOMAINS) — RENT3_MAIL_TEST_BRANCHES 에 든 지점에만 적용. 운영에서는 둘 다 비워 둔다."""
    if not _env_list("RENT3_MAIL_EXTRA_DOMAINS"):
        return []
    return _env_list("RENT3_MAIL_EXTRA_DOMAINS") if (lsr.branch or "").lower() in _env_list("RENT3_MAIL_TEST_BRANCHES") else []


def inbound_domain() -> str:
    return (os.getenv("RENT3_MAIL_DOMAIN") or "").strip().lower()


def address_for(token: str) -> str:
    d = inbound_domain()
    return f"pay-{token}@{d}" if (token and d) else ""


def _opt(lsr: Lessor) -> Dict[str, Any]:
    return dict(lsr.mail_opt or {})


def _fresh(o: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    v = o.get(key)
    if isinstance(v, dict) and (v.get("expires_at") or "") > datetime.now().isoformat(timespec="seconds"):
        return v
    return None


def settings_view(lsr: Lessor) -> Dict[str, Any]:
    purge_due()
    o = _opt(lsr)
    bank = o.get("bank") or ""
    fc, fe = _fresh(o, "forward_code"), _fresh(o, "forward_code_error")
    return {
        "test_mode": bool(test_domains_for(lsr)),
        "forward_code": fc["code"] if fc else "", "forward_error": bool(fe),
        "active": bool(o.get("active")), "bank": bank, "bank_label": (mp.BANKS.get(bank) or {}).get("label", ""),
        "acct_tail": o.get("acct_tail", ""), "auto": bool(o.get("auto")),
        "address": address_for(o.get("token", "")) if o.get("active") else "", "domain_ready": bool(inbound_domain()),
        "consented_at": o.get("consented_at", ""), "consent_ver": o.get("consent_ver", ""),
        "consent_text": CONSENT_TEXT, "retention_days": RETENTION_DAYS,
        "banks": [{"id": k, "label": v["label"], "verified": v["verified"], "note": v["note"]} for k, v in mp.BANKS.items()],
    }


def enable(lsr: Lessor, consent: bool, bank: str, acct_tail: str, auto: bool) -> Lessor:
    if not consent:
        raise DomainError("메일 수신 동의가 필요합니다.", hint="동의 문구를 확인하고 체크해 주세요. 동의하지 않아도 다른 기능은 그대로 쓸 수 있습니다.")
    if bank not in mp.BANKS:
        raise DomainError("지원하지 않는 은행입니다.", hint="목록에서 은행을 골라 주세요.")
    tail = "".join(ch for ch in (acct_tail or "") if ch.isdigit())
    if len(tail) != 4:
        raise DomainError("입금 계좌 끝 4자리를 숫자로 입력해 주세요.", hint="예: 계좌번호가 351-0123-4567-83 이면 6783")
    if auto and not mp.BANKS[bank]["verified"]:
        raise DomainError("이 은행은 아직 '일치하면 자동 처리'를 쓸 수 없습니다.", hint="실제 메일로 형식이 확인되기 전에는 '확인 후 처리'만 선택할 수 있습니다.")
    o = _opt(lsr)
    o.update({"active": True, "bank": bank, "acct_tail": tail, "auto": bool(auto), "consent_ver": CONSENT_VER})
    if not o.get("consented_at") or o.get("revoked_at"):
        o["consented_at"] = now_iso()
    o.pop("revoked_at", None)
    if not o.get("token"):
        o["token"] = secrets.token_hex(10)
    lsr.mail_opt = o
    lsr.save(action="mail_opt_enabled", app_source="rent3_mail")
    return lsr


def rotate_token(lsr: Lessor) -> Lessor:
    o = _opt(lsr)
    if not o.get("active"):
        raise DomainError("입금 메일 연동이 꺼져 있습니다.")
    o["token"] = secrets.token_hex(10)
    lsr.mail_opt = o
    lsr.save(action="mail_token_rotated", app_source="rent3_mail")
    return lsr


def disable(lsr: Lessor) -> int:
    """철회: 수신 주소 폐기 + 이 임대인의 보관 자료 즉시 삭제. 지운 건수를 돌려준다."""
    n = 0
    for x in MailIntake.find(lessor_id=lsr.id):
        x.delete(action="mail_intake_revoked", app_source="rent3_mail")
        n += 1
    lsr.mail_opt = {"active": False, "revoked_at": now_iso(), "consent_ver": (lsr.mail_opt or {}).get("consent_ver", "")}
    lsr.save(action="mail_opt_revoked", app_source="rent3_mail")
    return n


# ───────────────────────── 수신 ─────────────────────────
def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    import hmac
    if not secret or not signature:
        return False
    want = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.strip().lower())


def _token_of(to_addr: str) -> str:
    local = (to_addr or "").split("@", 1)[0].strip().lower()
    return local[4:] if local.startswith("pay-") else ""


def lessor_by_token(token: str) -> Optional[Lessor]:
    if not token:
        return None
    for l in Lessor.all():
        o = l.mail_opt or {}
        if o.get("active") and o.get("token") and secrets.compare_digest(str(o["token"]), token):
            return l
    return None


def _domain(addr: str) -> str:
    a = (addr or "").strip().lower()
    if "<" in a and ">" in a:
        a = a[a.rfind("<") + 1:a.rfind(">")]
    return a.rsplit("@", 1)[-1] if "@" in a else ""


def _in_domains(d: str, allowed) -> bool:
    d = (d or "").lower()
    return any(d == a or d.endswith("." + a) for a in allowed)


def clear_forward_code(lsr: Lessor) -> None:
    o = _opt(lsr)
    o.pop("forward_code", None)
    o.pop("forward_code_error", None)
    lsr.mail_opt = o
    lsr.save(action="mail_forward_code_cleared", app_source="rent3_mail")


def _forward_confirmation(lsr: Lessor, payload: Dict[str, Any], from_dom: str, dkim: List[str]) -> Optional[Dict[str, Any]]:
    """지메일 '전달 확인' 메일이면 숫자 코드만 뽑아 1시간 보관한다(링크·원문은 보관 안 함). 아니면 None."""
    frm = str(payload.get("from_addr") or "").lower()
    if GMAIL_FORWARD_SENDER not in frm:
        return None
    if not any(_in_domains(d, ("google.com",)) for d in dkim):
        return {"status": "IGNORED"}                      # 구글 서명이 아니면 위조 가능 — 버린다
    subj, body = str(payload.get("subject") or ""), mp.plain_text(str(payload.get("text") or payload.get("html") or ""))
    m = re.search(r"\(#\s*(\d{6,9})\s*\)", subj) or re.search(r"(?<!\d)(\d{6,9})(?!\d)", subj + chr(10) + body)
    o = _opt(lsr)
    exp = (datetime.now() + timedelta(minutes=FORWARD_CODE_MIN)).isoformat(timespec="seconds")
    if m:
        o["forward_code"] = {"code": m.group(1), "expires_at": exp}
        o.pop("forward_code_error", None)
        status = "FORWARD_CODE"
    else:
        o["forward_code_error"] = {"expires_at": exp}
        status = "FORWARD_CODE_ERROR"
    lsr.mail_opt = o
    lsr.save(action="mail_forward_code", app_source="rent3_mail")
    return {"status": status}


def _month_pick(ct: Contract, amount: int):
    """미납 달이 여러 개이고 일부만 입금되면 어느 달에 넣을지 사람이 정해야 한다(문자 수납과 같은 규칙)."""
    led = ct.ledger(up_to_ym=current_ym())
    rows = [r for r in led["months"] if r["unpaid"] > 0]
    total = sum(r["unpaid"] for r in rows)
    overdue_n = sum(1 for r in rows if r.get("phase") == "OVERDUE")
    if rows and amount < total and overdue_n >= 2:
        return [{"ym": r["ym"], "unpaid": r["unpaid"], "phase": r.get("phase", "OVERDUE")} for r in rows]
    return []


def _save(lsr: Lessor, **kw: Any) -> MailIntake:
    it = MailIntake(lessor_id=lsr.id, branch=lsr.branch, purge_at=_purge_at(), **kw)
    it.save(action="mail_intake", app_source="rent3_mail")
    return it


def _apply(lsr: Lessor, it: MailIntake, room_no: str, target_months: Optional[List[str]], status: str) -> Dict[str, Any]:
    res = FifoPaymentEngine.collect(
        branch=lsr.branch, room_no=room_no, amount=it.amount, payer_name=it.payer_name, paid_date=it.paid_date,
        method="BANK_MAIL", raw_sms=f"mail:{it.msg_hash}", app_source="rent3_mail", target_months=target_months,
    )
    it.room_no = room_no
    it.status = status
    it.reason = ""
    it.pay_id = ((res or {}).get("payment") or {}).get("id", "")
    it.save(action="mail_intake_applied", app_source="rent3_mail")
    return res


def ingest(payload: Dict[str, Any]) -> Dict[str, Any]:
    """수신 서비스가 보낸 메일 1통을 판정한다. 반환: {status, reason?} — 호출자에게 개인정보는 돌려주지 않는다."""
    purge_due()
    to = payload.get("to") or ""
    if isinstance(to, list):
        to = to[0] if to else ""
    lsr = lessor_by_token(_token_of(str(to)))
    if lsr is None:
        return {"status": "REJECTED"}                 # 토큰 없음·철회·꺼짐: 기록하지 않고 폐기
    o = lsr.mail_opt or {}
    bank = o.get("bank") or ""
    spec = mp.BANKS.get(bank)
    if not spec:
        return {"status": "REJECTED"}
    mid = str(payload.get("message_id") or "").strip()
    body = str(payload.get("text") or payload.get("html") or "")
    msg_hash = hashlib.sha256(f"{lsr.id}|{mid or body}".encode("utf-8")).hexdigest()[:32]
    if any(x.msg_hash == msg_hash for x in MailIntake.find(lessor_id=lsr.id)):
        return {"status": "DUPLICATE"}
    from_dom = _domain(str(payload.get("from_addr") or ""))
    dkim = [str(d).lower() for d in (payload.get("dkim_pass_domains") or [])]
    fwd = _forward_confirmation(lsr, payload, from_dom, dkim)
    if fwd is not None:
        return fwd
    allowed = spec["domains"]
    auth_ok = any(_in_domains(d, allowed) for d in dkim)
    tdoms = test_domains_for(lsr)
    is_test = (not auth_ok and not _in_domains(from_dom, allowed)) and bool(tdoms) and (any(_in_domains(d, tdoms) for d in dkim) or _in_domains(from_dom, tdoms))
    base = {"msg_hash": msg_hash, "from_domain": from_dom, "auth_ok": auth_ok, "is_test": is_test}
    if not auth_ok and not is_test and not _in_domains(from_dom, allowed):
        _save(lsr, status="IGNORED", reason="허용된 은행 발신이 아닙니다", **base)
        return {"status": "IGNORED"}
    try:
        p = mp.parser_for(bank)(str(payload.get("subject") or ""), body)
    except mp.NotDeposit:
        _save(lsr, status="IGNORED", reason="입금 메일이 아닙니다", **base)
        return {"status": "IGNORED"}
    except mp.MailParseError as e:
        _save(lsr, status="FAILED", reason=f"메일 형식을 읽지 못했습니다: {e}", **base)
        return {"status": "FAILED"}
    if p["acct_tail"] != o.get("acct_tail"):
        _save(lsr, status="IGNORED", reason="내 입금 계좌가 아닙니다", **base)
        return {"status": "IGNORED"}
    fields = {"amount": p["amount"], "payer_name": p["payer_name"], "paid_date": p["paid_date"], "room_no": p["room_no"]}
    parsed = {**fields, "raw_sms": ""}
    chk = FifoPaymentEngine.check_sms(lsr.branch, parsed)
    ct = chk["room_contract"]
    issues = chk["issues"]
    room_no = fields["room_no"] or (ct.room_no if ct else "")
    reasons: List[str] = []
    if is_test:
        reasons.append("[테스트] 테스트 도메인으로 받은 메일 — 자동 수납하지 않습니다")
    elif not auth_ok:
        reasons.append("발신 확인 불가(원본 서명을 확인하지 못했습니다)")
    reasons += [i["msg"] for i in issues]
    pick = _month_pick(ct, p["amount"]) if (ct and not issues) else []
    if pick:
        reasons.append("밀린 달이 여러 개라 어느 달에 넣을지 선택이 필요합니다")
    if not mp.is_recent(p["paid_date"], RECENT_DAYS):
        reasons.append("입금일이 최근 3일 이내가 아닙니다")
    today_auto = sum(1 for x in MailIntake.find(lessor_id=lsr.id, status="AUTO_APPLIED") if (x.received_at or "")[:10] == _today_iso())
    if today_auto >= DAILY_CAP:
        reasons.append("하루 자동 수납 상한을 넘었습니다")
    want_auto = bool(o.get("auto")) and spec["verified"] and not is_test
    it = _save(lsr, status="PENDING", reason=" · ".join(reasons), **{**fields, "room_no": room_no}, **base)
    if want_auto and not reasons and ct is not None:
        try:
            _apply(lsr, it, room_no, None, "AUTO_APPLIED")
            return {"status": "AUTO_APPLIED"}
        except Exception as e:                           # 수납 엔진이 거부하면 사람이 확인하게 둔다
            it.reason = f"자동 수납에 실패했습니다: {getattr(e, 'message', None) or e}"
            it.save(action="mail_intake_failed", app_source="rent3_mail")
    elif not reasons:
        it.reason = "확인 후 처리로 설정되어 있습니다"
        it.save(action="mail_intake_pending", app_source="rent3_mail")
    return {"status": "PENDING"}


# ───────────────────────── 임대인 화면용 ─────────────────────────
def _row(x: MailIntake) -> Dict[str, Any]:
    return {"id": x.id, "status": x.status, "status_ko": STATUS_KO.get(x.status, x.status), "reason": x.reason, "amount": x.amount,
            "payer_name": x.payer_name, "paid_date": x.paid_date, "room_no": x.room_no, "received_at": x.received_at, "auth_ok": x.auth_ok, "is_test": x.is_test}


def list_intakes(lsr: Lessor, limit: int = 60) -> List[Dict[str, Any]]:
    purge_due()
    rows = sorted(MailIntake.find(lessor_id=lsr.id), key=lambda x: (x.received_at, x.id), reverse=True)
    return [_row(x) for x in rows[:limit]]


def _mine(lsr: Lessor, intake_id: str) -> MailIntake:
    it = MailIntake.get(intake_id)
    if it is None or it.lessor_id != lsr.id:
        raise NotFound("입금 메일 기록을 찾을 수 없습니다.")
    return it


def apply_pending(lsr: Lessor, intake_id: str, room_no: str = "", target_months: Optional[List[str]] = None) -> Dict[str, Any]:
    """임대인이 [처리]를 눌렀을 때: 저장된 구조화 값으로 수납한다(원문 불필요)."""
    it = _mine(lsr, intake_id)
    if it.status != "PENDING":
        raise DomainError("이미 처리된 메일입니다.")
    rn = (room_no or it.room_no or "").strip()
    if not rn:
        raise DomainError("호실을 알 수 없습니다.", hint="호실을 직접 입력해 주세요.")
    parsed = {"amount": it.amount, "payer_name": it.payer_name, "paid_date": it.paid_date, "room_no": rn, "raw_sms": ""}
    chk = FifoPaymentEngine.check_sms(lsr.branch, parsed)
    ct = chk["room_contract"]
    if ct is None:
        raise DomainError(f"{rn}호에 활성 계약이 없습니다.", hint="호실을 확인해 주세요.")
    if not target_months:
        pick = _month_pick(ct, it.amount)
        if pick:
            return {"needs_month_pick": True, "unpaid_months": pick, "room_no": rn, "success": False}
    res = _apply(lsr, it, rn, target_months, "USER_APPLIED")
    return {**res, "success": True}


def dismiss(lsr: Lessor, intake_id: str) -> MailIntake:
    it = _mine(lsr, intake_id)
    if it.status != "PENDING":
        raise DomainError("이미 처리된 메일입니다.")
    it.status = "IGNORED"
    it.reason = "임대인이 무시했습니다"
    it.save(action="mail_intake_dismissed", app_source="rent3_mail")
    return it
