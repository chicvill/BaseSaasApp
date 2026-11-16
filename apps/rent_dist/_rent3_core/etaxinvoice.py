"""
apps/rent_dist/_rent3_core/etaxinvoice.py — 전자세금계산서 발행 API 연계(볼타 Bolta).

가맹비 입금이 등록되면(자동 발행을 켠 경우 즉시) 볼타 정발행 API 로 전자세금계산서를 접수하고, 국세청 승인번호를 받아 `feebilling.finalize_invoice` 로 기록한다.
 - 설정(환경변수): RENT3_ETAX_PROVIDER=bolta · RENT3_BOLTA_API_KEY(test_… 는 샌드박스·국세청 미연동 / live_… 는 실제 발행) · RENT3_BOLTA_WEBHOOK_SECRET(whsec_…)
   공급자(플랫폼): RENT3_PLATFORM_NAME / RENT3_PLATFORM_BIZ_NO / RENT3_PLATFORM_CEO / RENT3_PLATFORM_EMAIL(필수) / RENT3_PLATFORM_ADDRESS / RENT3_PLATFORM_PHONE(010-0000-0000, 선택)
   RENT3_ETAX_AUTO=1 이면 납부 등록 직후 자동 발행을 시도한다(실패해도 납부 등록은 유지, 관리인이 다시 시도).
 - 접수 응답의 issuanceKey 는 '접수'일 뿐이다. 결과는 웹훅(서명 검증)으로 오고, 관리인이 [상태 확인]을 눌러도(또는 목록을 열 때 자동으로) 조회 API 로 확인한다.
   승인번호는 웹훅 본문이 아니라 **조회 API 응답(ntsTransactionId)** 으로만 확정한다.
 - 같은 요청 관리번호(Bolta-Client-Reference-Id)는 다시 쓸 수 없으므로 시도마다 번호를 새로 붙이고, 응답을 못 받은 경우엔 재전송하지 않고 상태 조회로 확인한다.
 - 수정·취소(수정세금계산서)는 연계하지 않았다 — 발행된 건의 수정은 볼타·홈택스에서 하고, 앱의 기록 취소는 앱 장부만 바꾼다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from core.errors import DomainError

BASE_URL = "https://xapi.bolta.io"
TIMEOUT = 15


def provider_name() -> str:
    return os.getenv("RENT3_ETAX_PROVIDER", "").strip().lower()


def enabled() -> bool:
    return provider_name() == "bolta" and bool(os.getenv("RENT3_BOLTA_API_KEY", "").strip())


def auto_on_payment() -> bool:
    return enabled() and os.getenv("RENT3_ETAX_AUTO", "0").strip().lower() in ("1", "true", "yes", "on")


def sandbox() -> bool:
    return os.getenv("RENT3_BOLTA_API_KEY", "").strip().startswith("test_")


def status_info() -> Dict[str, Any]:
    """관리인 화면용 설정 요약(키·비밀은 내보내지 않는다)."""
    miss = []
    if provider_name() == "bolta" and not os.getenv("RENT3_BOLTA_API_KEY", "").strip():
        miss.append("RENT3_BOLTA_API_KEY")
    for k in ("RENT3_PLATFORM_BIZ_NO", "RENT3_PLATFORM_NAME", "RENT3_PLATFORM_CEO", "RENT3_PLATFORM_EMAIL"):
        if not os.getenv(k, "").strip():
            miss.append(k)
    return {"provider": provider_name() or "manual", "enabled": enabled(), "auto": auto_on_payment(), "sandbox": sandbox() if enabled() else False,
            "webhook_configured": bool(os.getenv("RENT3_BOLTA_WEBHOOK_SECRET", "").strip()), "missing": miss if provider_name() == "bolta" else []}


def _http(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """표준 라이브러리 HTTP 호출(시험에서는 이 함수를 대체한다). (상태코드, JSON 본문)을 돌려준다."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:       # noqa: S310 (고정된 https 주소)
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {"message": "응답을 해석하지 못했습니다."}


def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    key = os.getenv("RENT3_BOLTA_API_KEY", "").strip()
    h = {"Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode(), "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _valid_email(s: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", s or "")) and len(s) <= 40


def build_payload(fee: Any, lessor: Any, building_name: str) -> Dict[str, Any]:
    """볼타 정발행 요청 본문. 공급자는 플랫폼, 공급받는자는 가맹 임대인(승인된 사업자번호 필수). 입금 후 발행하므로 영수(RECEIPT)."""
    from . import feebilling as fb
    from .billing import validate_kr_business_number
    sup = fb.platform_info()
    sup_no = _digits(sup["business_number"])
    problems = []
    if len(sup_no) != 10 or not validate_kr_business_number(sup_no, strict_checksum=True)["valid"]:
        problems.append("공급자(플랫폼) 사업자번호(RENT3_PLATFORM_BIZ_NO)가 없거나 올바르지 않습니다.")
    sup_email = os.getenv("RENT3_PLATFORM_EMAIL", "").strip()
    if not _valid_email(sup_email):
        problems.append("공급자 담당자 이메일(RENT3_PLATFORM_EMAIL)이 없거나 올바르지 않습니다.")
    if not sup["name"] or not sup["ceo"]:
        problems.append("공급자 상호·대표자(RENT3_PLATFORM_NAME/CEO)가 필요합니다.")
    buy_no = _digits(lessor.business_number)
    if len(buy_no) != 10 or not validate_kr_business_number(buy_no, strict_checksum=True)["valid"]:
        problems.append(f"{lessor.name} 임대인의 승인된 사업자번호가 없습니다(사업자등록증 홈택스 승인을 먼저).")
    if not _valid_email(lessor.email or ""):
        problems.append(f"{lessor.name} 임대인의 이메일이 없거나 올바르지 않습니다(세금계산서 수신용).")
    if problems:
        raise DomainError("전자세금계산서를 발행할 수 없습니다: " + " ".join(problems), hint="정보를 채운 뒤 다시 시도하거나, 홈택스에서 직접 발행하고 승인번호를 기록해 주세요.")
    issue_date = fee.paid_at or fee.billing_month + "-01"
    supplier: Dict[str, Any] = {"identificationNumber": sup_no, "organizationName": sup["name"][:70], "representativeName": sup["ceo"][:30],
                                "manager": {"email": sup_email}}
    if os.getenv("RENT3_PLATFORM_ADDRESS", "").strip():
        supplier["address"] = os.getenv("RENT3_PLATFORM_ADDRESS").strip()[:150]
    if re.fullmatch(r"010-\d{4}-\d{4}", os.getenv("RENT3_PLATFORM_PHONE", "").strip()):
        supplier["manager"]["telephone"] = os.getenv("RENT3_PLATFORM_PHONE").strip()
    return {
        "date": issue_date, "purpose": "RECEIPT", "taxType": "TAXABLE",
        "supplier": supplier,
        "supplied": {"identificationNumber": buy_no, "organizationName": (building_name or lessor.name)[:70], "representativeName": lessor.name[:30],
                     "managers": [{"email": lessor.email.strip(), "name": lessor.name[:30]}]},
        "items": [{"date": issue_date, "name": f"{fee.billing_month} 플랫폼 가맹비", "supplyCost": int(fee.supply_amount), "tax": int(fee.vat_amount)}],
        "description": f"가맹비 {fee.billing_month} ({fee.id})"[:150],
    }


class _NetworkUncertain(DomainError):
    """요청이 접수됐는지 모르는 상태(재전송 금지, 상태 조회로 확인)."""
    def __init__(self, ref: str, exc: Exception):
        super().__init__("볼타 응답을 받지 못해 접수 여부가 불확실합니다.", hint="같은 요청을 다시 보내지 말고 잠시 뒤 [발행 상태 확인]을 눌러 확인해 주세요.")
        self.client_ref = ref


def issue(fee: Any, lessor: Any, building_name: str) -> Dict[str, Any]:
    """접수 요청. 성공하면 {issuance_key, client_ref}. 응답을 받지 못했거나 오류면 DomainError(재전송 금지 안내 포함)."""
    if not enabled():
        raise DomainError("전자세금계산서 API 연계가 켜져 있지 않습니다.", hint="서버 환경변수 RENT3_ETAX_PROVIDER=bolta, RENT3_BOLTA_API_KEY 를 설정해 주세요.")
    payload = build_payload(fee, lessor, building_name)
    fee.invoice_attempts = int(getattr(fee, 'invoice_attempts', 0) or 0) + 1      # 요청 관리번호는 한 번 쓰면 재사용할 수 없다 — 실제로 호출하는 시도마다 올린다(정보 부족으로 호출 전에 막힌 경우는 올리지 않는다)
    ref = f"{fee.id}-{fee.invoice_attempts}"
    try:
        code, body = _http("POST", f"{BASE_URL}/v1/taxInvoices/issue", _auth_headers({"Bolta-Client-Reference-Id": ref}), payload)
    except Exception as exc:      # 네트워크 오류: 접수됐는지 모르므로 상태 조회로 확인하라고 알린다
        raise _NetworkUncertain(ref, exc)
    if code == 200 and body.get("issuanceKey"):
        return {"issuance_key": str(body["issuanceKey"]), "client_ref": ref}
    msg = str(body.get("message") or body.get("code") or f"HTTP {code}")
    if code >= 500:
        raise _NetworkUncertain(ref, RuntimeError(msg))
    raise DomainError(f"볼타가 발행 요청을 거절했습니다: {msg}", hint="입력 정보(사업자번호·이메일·금액)를 확인하고 다시 시도하거나 홈택스에서 직접 발행해 주세요.")


def retrieve(issuance_key: str) -> Optional[Dict[str, Any]]:
    """발행 완료 건 조회. 완료되어 승인번호가 있으면 {nts_no, issued_at}, 아직이면 None."""
    code, body = _http("GET", f"{BASE_URL}/v1/taxInvoices/{issuance_key}", _auth_headers())
    nts = str(body.get("ntsTransactionId") or "") if code == 200 else ""
    return {"nts_no": nts, "issued_at": str(body.get("issuedAt") or "")} if nts else None


def verify_webhook(headers: Dict[str, str], raw_body: bytes, now: Optional[float] = None) -> None:
    """Standard Webhooks 서명 검증(HMAC-SHA256, 시각 ±5분). 실패하면 DomainError."""
    secret = os.getenv("RENT3_BOLTA_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise DomainError("웹훅 서명 키가 설정되지 않았습니다.")
    h = {k.lower(): v for k, v in headers.items()}
    wid, ts, sig = h.get("webhook-id", ""), h.get("webhook-timestamp", ""), h.get("webhook-signature", "")
    if not (wid and ts and sig):
        raise DomainError("웹훅 헤더가 없습니다.")
    try:
        if abs((now or time.time()) - int(ts)) > 300:
            raise DomainError("웹훅 시각이 허용 범위를 벗어났습니다.")
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except (ValueError, TypeError):
        raise DomainError("웹훅 헤더나 키 형식이 올바르지 않습니다.")
    want = base64.b64encode(hmac.new(key, f"{wid}.{ts}.".encode() + raw_body, hashlib.sha256).digest()).decode()
    got = [p.split(",", 1)[1] for p in sig.split() if p.startswith("v1,")]
    if not any(hmac.compare_digest(want, g) for g in got):
        raise DomainError("웹훅 서명이 올바르지 않습니다.")
