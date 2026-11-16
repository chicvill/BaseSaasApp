"""
apps/rent_dist/_rent3_core/ratelimit.py — 로그인·재설정 무차별 대입 방어 (메모리 기반).

계정(아이디)별로 10분 안에 5번, 접속 주소(IP)별로 10분 안에 30번 실패하면 15분 동안 잠근다.
주의: 서버 프로세스 메모리에 둔다 — 서버를 여러 개 띄우면 서버마다 따로 센다(공유 저장소가 필요하면 교체).
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Iterable, List

from core.errors import DomainError

WINDOW = 600          # 실패를 세는 구간(초)
LOCK_SECONDS = 900    # 잠금 시간(초)
LIMITS = {"u": 5, "ip": 30}


class TooManyAttempts(DomainError):
    status_code = 429
    level = "warning"


_lock = threading.Lock()
_fails: Dict[str, List[float]] = {}
_locked: Dict[str, float] = {}


def _limit_for(key: str) -> int:
    return LIMITS["ip"] if key.split(":", 2)[1] == "ip" else LIMITS["u"]


def check(scope: str, keys: Iterable[str]) -> None:
    now = time.time()
    with _lock:
        for k in keys:
            until = _locked.get(f"{scope}:{k}", 0)
            if until > now:
                mins = int((until - now) // 60) + 1
                raise TooManyAttempts(
                    f"시도 횟수를 초과했습니다. {mins}분 뒤에 다시 시도해 주세요.",
                    hint="비밀번호가 기억나지 않으면 '비밀번호 재설정'을 이용해 주세요.",
                )


def fail(scope: str, keys: Iterable[str]) -> None:
    now = time.time()
    with _lock:
        for k in keys:
            full = f"{scope}:{k}"
            arr = [t for t in _fails.get(full, []) if now - t < WINDOW] + [now]
            _fails[full] = arr
            if len(arr) >= _limit_for(full):
                _locked[full] = now + LOCK_SECONDS
                _fails[full] = []


def clear(scope: str, keys: Iterable[str]) -> None:
    with _lock:
        for k in keys:
            full = f"{scope}:{k}"
            _fails.pop(full, None)
            _locked.pop(full, None)


def reset_all() -> None:
    """테스트·운영 도구용: 모든 기록 초기화."""
    with _lock:
        _fails.clear()
        _locked.clear()
