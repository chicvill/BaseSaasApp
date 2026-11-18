"""
apps/rent_dist/_rent3_core/offer.py — 임대인용 서비스 안내(P2)에 보여 줄 '정직한 조건': 월 가맹비·무료 기간·세무 자료 범위.

원칙
- 금액은 코드의 실제 값(`Lessor.app_fee` 기본값, 부가세 포함)에서 읽는다. 안내문에 따로 적은 숫자를 두지 않는다.
- 무료 기간은 환경변수 `RENT3_FEE_FREE_UNTIL=YYYY-MM`(그 달까지 청구 안 함)을 설정했을 때만 안내하고, 같은 값을 청구 로직(feebilling)이 지켜 안내와 청구가 어긋나지 않게 한다.
- 세무는 '홈택스 신고용 입금 증빙 자료(CSV) 정리'까지만 말한다. 신고서 작성·제출·세액 계산·절세 보장은 말하지 않는다.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict

from .models import Lessor

SAMPLE_CSV_NAME = "rent3_sample_ledger.csv"


def free_until() -> str:
    """무료 기간의 마지막 달(YYYY-MM). 설정이 없거나 형식이 틀리면 빈 문자열(= 무료 안내 없음)."""
    v = os.getenv("RENT3_FEE_FREE_UNTIL", "").strip()
    return v if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v) else ""


def is_free_month(month: str) -> bool:
    fu = free_until()
    return bool(fu) and month <= fu


def default_fee() -> int:
    return int(Lessor.model_fields["app_fee"].default)


def terms() -> Dict[str, Any]:
    fee, fu = default_fee(), free_until()
    supply = round(fee / 1.1)
    return {
        "monthly_fee": fee, "fee_supply": supply, "fee_vat": fee - supply, "vat_included": True,
        "fee_basis": "임대인(건물)당 월 정액",
        "free_until": fu,
        "free_text": (f"{fu[:4]}년 {int(fu[5:])}월분까지 가맹비를 청구하지 않습니다." if fu else ""),
        "after_approval_only": "가입 후 가맹 심사를 통과해야 매물 공개·직거래가 가능합니다.",
        "tax_scope": "홈택스 신고용 입금 증빙 자료(CSV) 자동 정리. 신고서를 대신 작성·제출하지 않으며 세액을 계산해 드리지 않습니다.",
        "individual_note": "개별 조건이 다를 수 있으며, 실제 청구 금액은 가맹 승인 시 안내됩니다.",
    }


def sample_csv() -> str:
    """가짜 값으로 만든 납부현황 샘플(실제 장부와 같은 모양). 첫 줄에 샘플임을 밝힌다."""
    rows = [
        "※ 샘플입니다 — 가상의 호실·이름·금액이며 실제 자료가 아닙니다,,,,,,",
        "호실,성명,선/후,1월,,2월,",
        ",,,월일,금액,월일,금액",
        "101,홍길동(샘플),후불,1. 5,450000,2. 4,450000",
        "102,김철수(샘플),후불,1. 7,500000,2. 6,500000",
        "201,이영희(샘플),후불,1. 10,480000,,",
    ]
    return "﻿" + "\r\n".join(rows) + "\r\n"
