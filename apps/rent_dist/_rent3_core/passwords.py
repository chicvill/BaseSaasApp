"""
apps/rent_dist/_rent3_core/passwords.py — 비밀번호 해시 저장·검증, 새 비밀번호 규칙.

저장 형식: pbkdf2_sha256$<반복>$<salt hex>$<hash hex>
예전에 평문으로 저장된 값도 검증은 되지만(`verify`), 로그인에 성공하면 해시로 바꿔 저장한다(`is_hashed` 로 구분).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from core.errors import DomainError

ITERATIONS = 120_000
PREFIX = "pbkdf2_sha256"
WEAK = {"1212", "0202", "123456", "1234567", "12345678", "00000000", "password", "qwer1234", "12341234"}


def hash_password(raw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), ITERATIONS).hex()
    return f"{PREFIX}${ITERATIONS}${salt}${h}"


def is_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX + "$") and stored.count("$") == 3


def verify(stored: str, raw: str) -> bool:
    """해시 또는 (예전) 평문 저장값과 입력을 비교한다."""
    if not stored or raw is None:
        return False
    if not is_hashed(stored):
        return hmac.compare_digest(stored.encode(), raw.encode())
    _, iters, salt, expected = stored.split("$")
    h = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), int(iters)).hex()
    return hmac.compare_digest(h, expected)


def validate_new_password(raw: str) -> None:
    """새로 정하는 비밀번호 규칙: 8자 이상, 영문+숫자 포함, 흔한 값 금지."""
    pw = (raw or "").strip()
    if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw) or pw.lower() in WEAK:
        raise DomainError("비밀번호는 8자 이상이며 영문과 숫자를 모두 포함해야 합니다.",
                          hint="1212·12345678 같은 흔한 값은 쓸 수 없습니다.")


def temp_password() -> str:
    """관리자가 임시로 발급하는 비밀번호 (영문+숫자 보장)."""
    return "Tp" + secrets.token_urlsafe(7).replace("-", "x").replace("_", "y") + "7"
