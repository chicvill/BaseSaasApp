"""
apps/_rent3_core/domains/models_utils.py — Rent3 공통 상수·지점 정규화·날짜/년월 유틸·사업자/이메일 검증기.
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Any, Dict, Optional, Tuple

BRANCH_ALIASES = {
    "main": "main",
    "chic": "main",
    "chicvill": "main",
    "imsi": "main",
    "시크빌": "main",
    "sunho": "sunho",
    "선호": "sunho",
    "선호아트빌": "sunho",
    "sinbi": "sinbi",
    "신비": "sinbi",
    "신비주택": "sinbi",
}

EXPENSE_CATEGORIES = {
    "depreciation": "건물·기물 감가상각비",
    "electric_common": "공동 전기료",
    "water_common": "공동 수도료",
    "cleaning": "계단·공용부 정기 청소비",
    "elevator": "승강기(엘리베이터) 유지관리",
    "utility_common": "공용 전기·수도·가스료",
    "safety_fire": "소방·전기안전관리 대행료",
    "insurance": "건물 화재보험·배상책임",
    "repair_maint": "공용시설 수선 및 소모품",
    "internet_tv": "건물 인터넷·IPTV 단체선",
    "platform_fee": "플랫폼 가맹비(이용료)",
    "congrat_condol": "경조사비·업무추진비 (종소세 전용·부가세 불공제)",
    "other": "기타 일반 잡비",
}

VAT_NON_DEDUCTIBLE_CATEGORIES = {"insurance", "congrat_condol"}


def normalize_branch_id(raw: Optional[str]) -> str:
    """단일 진입점 지점 ID 정규화 (Single Source of Truth)."""
    if not raw:
        return "main"
    key = raw.strip().lower()
    return BRANCH_ALIASES.get(key, key)


def current_ym() -> str:
    env_today = date_today()
    return f"{env_today.year:04d}-{env_today.month:02d}"


def date_today() -> date:
    v = os.getenv("RENT3_TODAY") or os.getenv("RENT2_TODAY")
    if v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            pass
    return date.today()


def parse_ym(value: str) -> Optional[Tuple[int, int]]:
    s = (value or "").strip()
    m = re.search(r"(\d{4})\s*[-./]\s*(\d{1,2})", s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if 1 <= mo <= 12:
        return (y, mo)
    return None


def ym_index(y: int, m: int) -> int:
    return y * 12 + m


def index_to_ym(idx: int) -> str:
    y = (idx - 1) // 12
    m = (idx - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def add_months_str(start_date: str, months: int) -> str:
    ym = parse_ym(start_date)
    if not ym:
        return start_date
    day = 1
    m_day = re.search(r"\d{4}[-./]\d{1,2}[-./](\d{1,2})", start_date)
    if m_day:
        day = min(int(m_day.group(1)), 28)
    end_idx = ym_index(ym[0], ym[1]) + months
    ey = (end_idx - 1) // 12
    em = (end_idx - 1) % 12 + 1
    return f"{ey:04d}-{em:02d}-{day:02d}"


def validate_kr_business_number(raw: str, *, strict_checksum: bool = False) -> Dict[str, Any]:
    """사업자등록번호(10자리) 형식 및 국세청 가중치 체크섬 검증."""
    cleaned = re.sub(r"\D", "", raw or "")
    if not cleaned:
        return {"valid": False, "digits": "", "checksum_ok": False, "reason": "미입력"}
    if len(cleaned) != 10:
        return {"valid": False, "digits": cleaned, "checksum_ok": False, "reason": "10자리 숫자가 아님"}
    if len(set(cleaned)) == 1 or cleaned.endswith("00000"):
        return {"valid": False, "digits": cleaned, "checksum_ok": False, "reason": "유효하지 않은 번호(가짜/연속 번호)"}
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    total = sum(int(cleaned[i]) * weights[i] for i in range(9))
    total += (int(cleaned[8]) * 5) // 10
    check_digit = (10 - (total % 10)) % 10
    checksum_ok = check_digit == int(cleaned[9])
    if strict_checksum and not checksum_ok:
        return {"valid": False, "digits": cleaned, "checksum_ok": False, "reason": "체크섬 불일치"}
    return {
        "valid": True,
        "digits": cleaned,
        "formatted": f"{cleaned[:3]}-{cleaned[3:5]}-{cleaned[5:]}",
        "checksum_ok": checksum_ok,
        "reason": "정상" if checksum_ok else "형식유효(체크섬주의)",
    }


def validate_email_format(raw: str) -> bool:
    """전자세금계산서 수신 이메일 형식 검증."""
    s = (raw or "").strip()
    if not s:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", s))
