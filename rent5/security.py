"""
rent5/security.py — 비밀번호 해시 · 로그인 토큰 · 로그인 잠금 · 입력 검사.

- 비밀번호: PBKDF2-SHA256 (salt 별도), 원문은 어디에도 저장하지 않는다.
- 토큰: 서버 비밀키로 서명한 12시간짜리 값. 비밀번호를 바꾸면 이전 토큰은 즉시 무효 (pw_ver).
- 로그인 실패: (접속 IP + 아이디) 별로 5번 틀리면 10분 잠금. 아이디가 있는지 없는지는 알려 주지 않는다.
- 주민등록번호 모양은 어떤 입력에서도 받지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

TOKEN_TTL = 12 * 3600
PBKDF2_ROUNDS = 200_000
MAX_FAILS, LOCK_SECONDS = 5, 600

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,19}$")
RRN_RE = re.compile(r"\d{6}\s*[-–]?\s*[1-4]\d{6}")


class ApiError(Exception):
    """화면에 그대로 보여 줄 수 있는 오류."""

    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


# ── 입력 검사 ──
def digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def clean_text(value, label: str, max_len: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ApiError(422, f"{label}을(를) 입력해 주세요.")
    if len(text) > max_len:
        raise ApiError(422, f"{label}은(는) {max_len}자까지 쓸 수 있습니다.")
    if RRN_RE.search(text):
        raise ApiError(422, "주민등록번호는 입력하지 않습니다.", hint=f"{label} 칸에서 주민등록번호를 지워 주세요.")
    return text


def valid_phone(value, label: str = "휴대폰 번호", required: bool = True) -> str:
    text = str(value or "").strip()
    if not text and not required:
        return ""
    if not 9 <= len(digits(text)) <= 11 or not re.fullmatch(r"[\d\-\s+()]+", text):
        raise ApiError(422, f"{label}를 정확히 입력해 주세요.", hint="예: 010-1234-5678")
    return text


def validate_username(username: str) -> str:
    username = str(username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ApiError(422, "아이디는 영문 소문자로 시작하는 영문 소문자 · 숫자 · _ 4~20자입니다.")
    return username


def validate_password(raw: str, username: str = "") -> str:
    raw = str(raw or "")
    if len(raw) < 8:
        raise ApiError(422, "비밀번호는 8자 이상이어야 합니다.")
    if len(set(raw)) == 1 or raw.lower() == username.lower():
        raise ApiError(422, "너무 쉬운 비밀번호입니다.", hint="같은 글자 반복이나 아이디와 같은 비밀번호는 쓸 수 없습니다.")
    return raw


# ── 비밀번호 ──
def hash_password(raw: str, salt: Optional[str] = None) -> Tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return salt, digest.hex()


def check_password(raw: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(hash_password(raw, salt)[1], expected)


# ── 토큰 ──
def load_secret(data_dir: str) -> bytes:
    """RENT5_SECRET 환경변수, 없으면 data/secret.key (처음 한 번 무작위로 만든다)."""
    env = os.getenv("RENT5_SECRET")
    if env:
        return env.encode()
    path = Path(data_dir) / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="ascii")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text(encoding="ascii").strip().encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(secret: bytes, username: str, pw_ver: int, ttl: int = TOKEN_TTL) -> str:
    body = _b64(json.dumps({"u": username, "v": pw_ver, "exp": int(time.time()) + ttl}).encode())
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_token(secret: bytes, token: str) -> Optional[Dict]:
    try:
        body, sig = token.split(".", 1)
        want = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if data.get("exp", 0) > time.time() else None
    except Exception:
        return None


# ── 로그인 잠금 ──
class Lockout:
    def __init__(self) -> None:
        self._fails: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            count, until = self._fails.get(key, (0, 0.0))
            if count >= MAX_FAILS and until > time.time():
                raise ApiError(429, "로그인 시도가 너무 많습니다.", hint="10분 뒤에 다시 시도해 주세요.")
            if until <= time.time():
                self._fails.pop(key, None)

    def fail(self, key: str) -> None:
        with self._lock:
            count, _ = self._fails.get(key, (0, 0.0))
            self._fails[key] = (count + 1, time.time() + LOCK_SECONDS)

    def ok(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
