"""
apps/rent_dist/_rent3_core/mailparse.py — 은행 입금 알림 메일 해석기(은행별 어댑터).

 - 어댑터는 은행마다 파일/함수를 나눠 둔다. 읽지 못하면 MailParseError, 입금이 아니면 NotDeposit.
 - ⚠️ 농협 어댑터는 **'가정 형식(미검증)'** 이다. 실제 농협 알림 메일 샘플 없이 가상 문구로 만들었으므로
   `BANKS["nonghyup"]["verified"] = False` 이고, 검증 전에는 '일치하면 자동 처리' 옵션을 서버가 거부한다.
   실제 메일 1건으로 형식을 확인하면 tests 의 고정 입력을 바꾸고 verified 를 True 로 바꾼다.
 - 해석기는 필수 항목(입금 구분·금액·일자·계좌 끝자리, 입금자 또는 호실) 중 하나라도 못 읽으면 처리하지 않는다.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Callable, Dict

from .models import date_today


class MailParseError(Exception):
    """필수 항목을 읽지 못함 — 오수납 대신 '해석 실패'로 남긴다."""


class NotDeposit(Exception):
    """입금 메일이 아님(출금·광고·안내 등) — 무시한다."""


def plain_text(text: str) -> str:
    """메일 본문에서 HTML 태그를 걷어낸 평문."""
    s = text or ""
    if "<" in s and ">" in s:
        s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
        s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
        s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _field(s: str, label: str) -> str:
    m = re.search(rf"{label}\s*[:：]\s*([^\n]*)", s)
    return m.group(1).strip() if m else ""


def parse_nonghyup(subject: str, text: str) -> Dict[str, Any]:
    """NH농협 입금 알림(가정 형식). 예:
        거래일시 : 2026/11/20 10:23:45 / 거래구분 : 입금 / 입금계좌 : 351-****-4567-83
        보낸분 : 이민수 / 입금금액 : 550,000원 / 메모 : 201호 11월 월세
    반환: {amount, paid_date, acct_tail, payer_name, room_no}"""
    s = plain_text(text)
    kind = _field(s, "거래구분")
    if not kind:
        raise MailParseError("거래구분을 읽지 못했습니다.")
    if "입금" not in kind or "출금" in kind:
        raise NotDeposit(kind)
    m_amt = re.search(r"입금금액\s*[:：]\s*(\d{1,3}(?:,\d{3})+|\d+)\s*원", s)
    if not m_amt:
        raise MailParseError("입금금액을 읽지 못했습니다.")
    amount = int(m_amt.group(1).replace(",", ""))
    if amount <= 0:
        raise MailParseError("입금금액이 0원 이하입니다.")
    m_d = re.search(r"거래일시\s*[:：]\s*(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})", s)
    if not m_d:
        raise MailParseError("거래일시를 읽지 못했습니다.")
    paid_date = f"{int(m_d.group(1)):04d}-{int(m_d.group(2)):02d}-{int(m_d.group(3)):02d}"
    acct = re.sub(r"\D", "", _field(s, "입금계좌"))
    if len(acct) < 4:
        raise MailParseError("입금계좌를 읽지 못했습니다.")
    payer = _field(s, "보낸분")
    memo = _field(s, "메모")
    m_room = re.search(r"(?<!\d)([1-9]\d{2})\s*호", memo) or re.search(r"(?<!\d)([1-9]\d{2})\s*호", payer)
    room_no = m_room.group(1) if m_room else ""
    payer_name = re.sub(r"[\d\s호]+$", "", payer).strip()
    if not payer_name and not room_no:
        raise MailParseError("입금자와 호실을 모두 읽지 못했습니다.")
    return {"amount": amount, "paid_date": paid_date, "acct_tail": acct[-4:], "payer_name": payer_name or "입금자", "room_no": room_no}


# 은행 목록. verified=False 인 은행은 '자동 처리'를 켤 수 없다(확인 후 처리만).
BANKS: Dict[str, Dict[str, Any]] = {
    "nonghyup": {"label": "NH농협은행", "domains": ("nonghyup.com", "nhbank.com", "nonghyup.co.kr"), "verified": False, "parser": parse_nonghyup,
                 "note": "가정 형식으로 만든 해석기입니다. 실제 메일로 확인되기 전에는 '확인 후 처리'만 쓸 수 있습니다."},
}


def parser_for(bank: str) -> Callable[[str, str], Dict[str, Any]]:
    return BANKS[bank]["parser"]


def is_recent(paid_date: str, days: int = 3) -> bool:
    try:
        from datetime import date
        d = (date_today() - date.fromisoformat(paid_date)).days
    except Exception:
        return False
    return 0 <= d <= days
