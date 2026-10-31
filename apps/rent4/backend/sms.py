"""
apps/rent4/backend/sms.py — 문자 발송 (솔라피).

솔라피를 고른 이유: 개인도 가입 가능, 발신번호는 휴대폰 본인인증으로 바로 등록, 서버 IP 등록이 필요 없다
(Cloud Run 처럼 IP 가 바뀌는 환경에 맞다). 인증은 API 키 + HMAC 서명.

환경변수
    SOLAPI_API_KEY, SOLAPI_API_SECRET   솔라피 콘솔 > API Key
    SMS_SENDER                          솔라피에 등록한 발신번호 (예: 01012345678)
    RENT4_PUBLIC_URL                    문자에 넣을 접속 주소 (예: https://rent.example.com/rent4/)

세 값이 없으면 '개발 모드': 실제로 보내지 않고 서버 로그와 메모리(outbox)에 남긴다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from core.errors import DomainError

logger = logging.getLogger("rent4.sms")
SOLAPI_URL = "https://api.solapi.com/messages/v4/send"
outbox: List[Dict[str, str]] = []          # 개발 모드에서 보낸 문자 (테스트·확인용)


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def configured() -> bool:
    return all(os.getenv(k) for k in ("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "SMS_SENDER"))


def public_url() -> str:
    return os.getenv("RENT4_PUBLIC_URL", "http://localhost:9070/")


def send(to: str, text: str) -> Dict[str, str]:
    to = digits(to)
    if len(to) < 10:
        raise DomainError("휴대폰 번호가 올바르지 않습니다.")
    if not configured():
        outbox.append({"to": to, "text": text})
        del outbox[:-50]
        logger.warning("[SMS 개발 모드] %s ← %s", to, text)
        return {"mode": "dev"}
    key, secret = os.environ["SOLAPI_API_KEY"], os.environ["SOLAPI_API_SECRET"]
    date = datetime.now(timezone.utc).isoformat(timespec="seconds")
    salt = secrets.token_hex(16)
    signature = hmac.new(secret.encode(), (date + salt).encode(), hashlib.sha256).hexdigest()
    body = json.dumps({"message": {"to": to, "from": digits(os.environ["SMS_SENDER"]), "text": text}}).encode()
    req = urllib.request.Request(SOLAPI_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"HMAC-SHA256 apiKey={key}, date={date}, salt={salt}, signature={signature}",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            res.read()
    except Exception as e:  # 발송 실패는 사용자에게 알린다 (조용히 넘기지 않음)
        logger.exception("문자 발송 실패")
        raise DomainError("문자를 보내지 못했습니다.", hint="잠시 뒤 다시 시도하거나 관리자에게 문의하세요.", level="error") from e
    return {"mode": "solapi"}
