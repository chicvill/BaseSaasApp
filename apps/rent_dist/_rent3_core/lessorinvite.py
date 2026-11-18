"""
apps/rent_dist/_rent3_core/lessorinvite.py — 임차인이 집주인에게 보내는 '안심계약 제안' (임대인 유치 P1).

임차인이 마음에 든 집(특히 당근·직방 같은 외부 매물)의 집주인에게 직거래 전자계약을 제안하는 문자를 보낸다.
 - 문자는 **임차인의 폰에서** 임차인이 직접 보낸다. 서버는 집주인의 번호·이름을 받지도 저장하지도 않는다(스팸·개인정보 부담 없음).
 - 서버가 하는 일: 임차인별 추측 불가한 소개 코드 발급, 문안 생성(사실만), 하루 발송 횟수 제한, 이 코드로 가입한 집주인 수 집계.
 - 링크: `/rent3/?invite=lessor&ref=tenant&from=<코드>` → 포털이 임대인 가입 화면을 열고, 가입 때 `Lessor.acq` 에 귀속된다(acquisition.py).
문안은 임차인이 보내는 글이므로 과장 없이 사실만 쓴다: 중개 없는 직거래라 중개수수료가 들지 않는다는 점, 가입 후 가맹 심사가 있다는 점, 이용 조건은 링크에서 확인한다는 점.
"""
from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Any, Dict

from core.errors import DomainError

from .models import Lessor, Tenant

DAILY_LIMIT = 10          # 임차인 1명이 하루에 '보냈다'고 기록할 수 있는 횟수
LABEL_MAX = 40


def clean_label(raw: Any) -> str:
    """집 이름·호실 표시(임차인이 직접 적는 짧은 글). 제어문자·줄바꿈·링크 모양을 빼고 40자로 자른다."""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(raw or ""))
    s = re.sub(r"https?://\S+|www\.\S+", "", s)
    return re.sub(r"\s+", " ", s).strip()[:LABEL_MAX]


def ensure_code(t: Tenant) -> str:
    """임차인별 소개 코드(추측 불가, 영숫자 11자). 없으면 만들어 저장한다."""
    if not t.lessor_invite_code:
        t.lessor_invite_code = f"tn_{secrets.token_hex(4)}"
        t.save(action="lessor_invite_code_issued", app_source="rent3_tenant")
    return t.lessor_invite_code


def link_path(code: str) -> str:
    return f"/rent3/?invite=lessor&ref=tenant&from={code}"


def message(label: str, link: str) -> str:
    target = f"'{label}'에 관심이 있는 세입자입니다." if label else "집을 알아보고 있는 세입자입니다."
    return (f"안녕하세요, {target}\n"
            "계약을 진행하고 싶은데, 집주인님과 직접 계약하는 방식(OpenRent 직거래 전자계약)으로 하면 중개수수료가 들지 않고 "
            "등기부를 확인하며 비대면으로 계약할 수 있어서 제안드립니다.\n"
            "가입 후 가맹 심사가 있고, 이용 조건은 아래 링크에서 확인하실 수 있습니다. 편하신 대로 검토해 주세요.\n"
            f"{link}")


def joined_count(code: str) -> int:
    return sum(1 for l in Lessor.all() if (l.acq or {}).get("from_tenant") == code)


def info(t: Tenant, label: str = "", base_url: str = "") -> Dict[str, Any]:
    """코드·문안·오늘 남은 횟수·이 코드로 가입한 집주인 수(횟수는 올리지 않는다)."""
    code = ensure_code(t)
    today = date.today().isoformat()
    used = int(t.lessor_invite_today or 0) if t.lessor_invite_day == today else 0
    lab = clean_label(label)
    link = f"{base_url.rstrip('/')}{link_path(code)}"
    return {"code": code, "link": link, "label": lab, "message": message(lab, link),
            "sent_today": used, "left_today": max(DAILY_LIMIT - used, 0), "daily_limit": DAILY_LIMIT,
            "sent_total": int(t.lessor_invite_total or 0), "joined": joined_count(code)}


def record_sent(t: Tenant) -> Dict[str, Any]:
    """임차인이 [문자로 보내기]·[복사]를 눌렀을 때 호출: 하루 한도를 넘으면 거부하고, 아니면 횟수를 올린다."""
    today = date.today().isoformat()
    used = int(t.lessor_invite_today or 0) if t.lessor_invite_day == today else 0
    if used >= DAILY_LIMIT:
        raise DomainError(f"오늘은 제안 문자를 {DAILY_LIMIT}번까지 보낼 수 있습니다.", hint="내일 다시 보낼 수 있어요. 같은 집주인에게 반복해서 보내지 않도록 해 주세요.")
    t.lessor_invite_day, t.lessor_invite_today = today, used + 1
    t.lessor_invite_total = int(t.lessor_invite_total or 0) + 1
    t.save(action="lessor_invite_sent", app_source="rent3_tenant")
    return {"sent_today": t.lessor_invite_today, "left_today": max(DAILY_LIMIT - t.lessor_invite_today, 0)}
