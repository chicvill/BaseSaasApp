"""
apps/rent4/backend/sms.py — 문자 발송. 관리인 화면에서 방식을 고른다.

    dev       개발 모드: 보내지 않고 서버 로그와 outbox 에 남긴다 (키 없이 항상 가능)
    telegram  텔레그램 봇: 솔라피 가입 전 실제 휴대폰으로 받아 보는 테스트용 (무료)
    solapi    솔라피 문자: 운영용

텔레그램 연결
    사용자가 봇에서 '시작' → '📱 전화번호 공유'를 누르면 그 번호와 텔레그램 대화가 연결된다
    (본인 연락처만 받는다). 서버는 getUpdates 롱 폴링으로 봇 메시지를 받아 처리한다 (웹훅·공개 주소 불필요).

환경변수
    TELEGRAM_BOT_TOKEN                  @BotFather 에서 만든 봇 토큰
    SOLAPI_API_KEY, SOLAPI_API_SECRET   솔라피 콘솔 > API Key
    SMS_SENDER                          솔라피에 등록한 발신번호
    RENT4_PUBLIC_URL                    문자에 넣을 접속 주소
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import ClassVar, Dict, List, Optional

from core.errors import DomainError

from .pool import PoolObj

logger = logging.getLogger("rent4.sms")
SOLAPI_URL = "https://api.solapi.com/messages/v4/send"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
outbox: List[Dict[str, str]] = []          # 개발 모드에서 보낸 문자 (테스트·확인용)
PROVIDER_LABELS = {"dev": "개발 모드 (서버 로그)", "telegram": "텔레그램 (테스트)", "solapi": "솔라피 문자"}


class SmsSetting(PoolObj):
    collection: ClassVar[str] = "sms_settings"
    provider: str = ""


class TelegramLink(PoolObj):
    """휴대폰 번호 ↔ 텔레그램 대화. 사용자가 봇에 본인 연락처를 공유하면 생긴다."""
    collection: ClassVar[str] = "telegram_links"
    phone: str
    chat_id: int


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def local_phone(phone: str) -> str:
    """'+82 10-1234-5678' / '821012345678' → '01012345678'."""
    d = digits(phone)
    return "0" + d[2:] if d.startswith("82") else d


TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


def telegram_token() -> str:
    """형식이 맞는 봇 토큰만 쓴다 (예: 123456789:AAH...). 자리표시자 '토큰' 같은 값은 없는 것으로 본다."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    return token if TOKEN_RE.match(token) else ""


def public_url() -> str:
    return os.getenv("RENT4_PUBLIC_URL", "http://localhost:9070/")


# ── 방식 선택 ──
def available() -> List[Dict[str, str]]:
    out = [{"id": "dev", "label": PROVIDER_LABELS["dev"]}]
    if telegram_token():
        out.append({"id": "telegram", "label": PROVIDER_LABELS["telegram"]})
    if all(os.getenv(k) for k in ("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "SMS_SENDER")):
        out.append({"id": "solapi", "label": PROVIDER_LABELS["solapi"]})
    return out


def provider() -> str:
    ids = [p["id"] for p in available()]
    chosen = (SmsSetting.first() or SmsSetting()).provider
    if chosen in ids:
        return chosen
    return "solapi" if "solapi" in ids else "telegram" if "telegram" in ids else "dev"


def set_provider(name: str) -> str:
    if name not in [p["id"] for p in available()]:
        raise DomainError(f"'{PROVIDER_LABELS.get(name, name)}' 는 지금 쓸 수 없습니다.", hint="서버에 해당 키가 설정되어야 합니다.")
    s = SmsSetting.first() or SmsSetting()
    s.provider = name
    s.save()
    return name


def info() -> Dict:
    p = provider()
    return {"provider": p, "label": PROVIDER_LABELS[p], "available": available(),
            "telegram_bot": telegram_bot_url() if p == "telegram" else ""}


def send(to: str, text: str) -> Dict[str, str]:
    to = digits(to)
    if len(to) < 10:
        raise DomainError("휴대폰 번호가 올바르지 않습니다.")
    p = provider()
    if p == "telegram":
        return _send_telegram(to, text)
    if p == "solapi":
        return _send_solapi(to, text)
    outbox.append({"to": to, "text": text})
    del outbox[:-50]
    logger.warning("[문자 개발 모드] %s ← %s", to, text)
    return {"mode": "dev"}


# ── 솔라피 ──
def _send_solapi(to: str, text: str) -> Dict[str, str]:
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


# ── 텔레그램 ──
_bot_username: Optional[str] = None
_update_offset = 0
_lock = threading.Lock()


def _telegram(method: str, params: Optional[Dict] = None, timeout: int = 10) -> Dict:
    url = TELEGRAM_API.format(token=telegram_token(), method=method)
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.loads(res.read() or b"{}")
    if not data.get("ok"):
        raise DomainError("텔레그램 요청이 거절되었습니다.", hint=str(data.get("description", "")), level="error")
    return data.get("result")


def telegram_bot_url() -> str:
    global _bot_username
    if _bot_username is None:
        try:
            _bot_username = _telegram("getMe")["username"]
        except Exception:
            logger.exception("텔레그램 봇 정보를 가져오지 못했습니다")
            return ""
    return f"https://t.me/{_bot_username}"


def process_updates(timeout: int = 0) -> int:
    """봇에 온 메시지 처리: /start → 전화번호 공유 버튼, 본인 연락처 → 번호 연결. 처리한 개수."""
    global _update_offset
    with _lock:
        updates = _telegram("getUpdates", {"offset": _update_offset, "timeout": timeout,
                                           "allowed_updates": ["message"]}, timeout=timeout + 10) or []
        for u in updates:
            _update_offset = max(_update_offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            contact = msg.get("contact")
            if chat_id is None:
                continue
            if contact and contact.get("user_id") == (msg.get("from") or {}).get("id"):
                phone = local_phone(contact.get("phone_number", ""))
                link = TelegramLink.first(phone=phone) or TelegramLink(phone=phone, chat_id=chat_id)
                link.chat_id = chat_id
                link.save()
                _telegram("sendMessage", {"chat_id": chat_id, "reply_markup": {"remove_keyboard": True},
                                          "text": f"✅ {phone} 번호가 연결되었습니다. 이제 Rent4 화면에서 인증번호를 받으세요."})
            elif contact:
                _telegram("sendMessage", {"chat_id": chat_id, "text": "본인 연락처만 연결할 수 있습니다. 아래 버튼을 눌러 주세요."})
            else:
                _telegram("sendMessage", {"chat_id": chat_id, "text": "Rent4 인증번호를 받으려면 아래 버튼으로 전화번호를 공유해 주세요.",
                                          "reply_markup": {"keyboard": [[{"text": "📱 전화번호 공유", "request_contact": True}]],
                                                           "one_time_keyboard": True, "resize_keyboard": True}})
        return len(updates)


def _send_telegram(to: str, text: str) -> Dict[str, str]:
    link = TelegramLink.first(phone=to)
    if link is None:
        try:
            process_updates()                # 방금 연락처를 공유했을 수 있다
        except Exception:
            logger.exception("텔레그램 메시지 확인 실패")
        link = TelegramLink.first(phone=to)
    if link is None:
        bot = telegram_bot_url()
        raise DomainError("텔레그램이 아직 연결되지 않은 번호입니다.",
                          hint=f"텔레그램에서 {bot or 'Rent4 봇'} 을 열어 '시작' → '📱 전화번호 공유'를 누른 뒤 다시 시도하세요.")
    try:
        _telegram("sendMessage", {"chat_id": link.chat_id, "text": text})
    except Exception as e:
        logger.exception("텔레그램 발송 실패")
        raise DomainError("텔레그램으로 보내지 못했습니다.", hint="잠시 뒤 다시 시도해 주세요.", level="error") from e
    return {"mode": "telegram"}


def start_polling() -> None:
    """텔레그램을 쓸 수 있으면 봇 메시지를 계속 받는다 (데몬 스레드, 공개 주소 불필요)."""
    if os.getenv("RENT4_TELEGRAM_POLLING") == "0":
        return
    if not telegram_token():
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            logger.error("TELEGRAM_BOT_TOKEN 형식이 올바르지 않습니다 (예: 123456789:AAH...). @BotFather 가 준 토큰 전체를 넣으세요. 텔레그램은 꺼 둡니다.")
        return

    def loop():
        while True:
            try:
                process_updates(timeout=25)
            except Exception:
                logger.exception("텔레그램 폴링 오류")
                threading.Event().wait(5)

    threading.Thread(target=loop, name="rent4-telegram", daemon=True).start()
