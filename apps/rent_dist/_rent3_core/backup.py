"""
apps/rent_dist/_rent3_core/backup.py — Data Pool 일일 백업 · 검증 · 복구.

 - 풀 폴더 전체(JSON, `_events.jsonl`, `uploads/`, 폐기 토큰 목록)를 `.tar.gz` 하나로 묶는다. 백업 폴더 자체는 제외.
 - 저장 위치: `RENT3_BACKUP_DIR`(기본 풀 폴더 안 `_backups`). **풀과 다른 디스크(볼륨)에 두는 것을 권장**한다.
 - 하루 한 번(로그인 때 지연 실행, 스케줄러 없음), 최근 `RENT3_BACKUP_KEEP`(기본 14)개만 남긴다. `RENT3_BACKUP_DAILY=0` 이면 자동 실행을 끈다.
 - 필드 암호화(`MASTER_CRYPTO_KEY`/`RENT3_CRYPTO_KEY`)가 켜져 있으면 백업 안의 파일도 암호화된 그대로다 — **복구하려면 같은 키가 필요하다**.
 - 백업에는 개인정보가 들어 있다. 내려받기는 관리인만, 받은 파일은 안전하게 보관한다.
"""
from __future__ import annotations

import json
import os
import re
import tarfile
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .pool import pool

NAME_RE = re.compile(r"^rent3_pool_\d{8}_\d{6}\.tar\.gz$")
_last_check = 0.0
_lock = threading.Lock()


def keep_count() -> int:
    try:
        return max(1, int(os.getenv("RENT3_BACKUP_KEEP", "14") or 14))
    except ValueError:
        return 14


def backup_dir() -> str:
    d = os.getenv("RENT3_BACKUP_DIR", "").strip() or os.path.join(pool().pool_dir, "_backups")
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(d)


def _info(path: str) -> Dict[str, Any]:
    st = os.stat(path)
    return {"name": os.path.basename(path), "size": st.st_size, "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def list_backups() -> List[Dict[str, Any]]:
    d = backup_dir()
    out = [_info(os.path.join(d, n)) for n in os.listdir(d) if NAME_RE.match(n)]
    return sorted(out, key=lambda x: x["name"], reverse=True)


def path_for(name: str) -> str:
    """내려받기·복구용: 규칙에 맞는 이름만 허용(경로 조작 차단)."""
    if not NAME_RE.match(name or ""):
        raise ValueError("백업 파일 이름이 올바르지 않습니다.")
    p = os.path.join(backup_dir(), name)
    if not os.path.isfile(p):
        raise FileNotFoundError(name)
    return p


def create() -> Dict[str, Any]:
    root, dest = pool().pool_dir, backup_dir()
    name = f"rent3_pool_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    final, tmp = os.path.join(dest, name), os.path.join(dest, name + ".tmp")
    n_files = 0
    with tarfile.open(tmp, "w:gz") as tf:
        for cur, dirs, files in os.walk(root):
            if os.path.abspath(cur) == dest or os.path.abspath(cur).startswith(dest + os.sep):
                dirs[:] = []
                continue
            dirs[:] = [x for x in dirs if os.path.abspath(os.path.join(cur, x)) != dest]
            for f in files:
                if f.endswith(".tmp"):
                    continue
                full = os.path.join(cur, f)
                tf.add(full, arcname=os.path.relpath(full, root).replace(os.sep, "/"))
                n_files += 1
    os.replace(tmp, final)
    pruned = prune()
    return {**_info(final), "files": n_files, "pruned": pruned}


def prune() -> int:
    items = list_backups()
    gone = items[keep_count():]
    for it in gone:
        try:
            os.remove(os.path.join(backup_dir(), it["name"]))
        except OSError:
            pass
    return len(gone)


def verify(path: str) -> Dict[str, Any]:
    """백업이 온전한지 확인: 압축이 열리고, 경로가 안전하고, JSON·JSONL 이 읽힌다(암호화된 값은 문자열로 읽히면 된다)."""
    problems: List[str] = []
    n = 0
    try:
        with tarfile.open(path, "r:gz") as tf:
            for m in tf.getmembers():
                n += 1
                if m.name.startswith("/") or ".." in m.name.split("/") or not (m.isfile() or m.isdir()):
                    problems.append(f"안전하지 않은 항목: {m.name}")
                    continue
                if m.isfile() and m.name.endswith(".json"):
                    try:
                        json.loads(tf.extractfile(m).read().decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        problems.append(f"JSON 을 읽을 수 없음: {m.name}")
                elif m.isfile() and m.name.endswith(".jsonl"):
                    for i, line in enumerate(tf.extractfile(m).read().decode("utf-8").splitlines(), 1):
                        if line.strip():
                            try:
                                json.loads(line)
                            except Exception:  # noqa: BLE001
                                problems.append(f"{m.name} {i}번째 줄을 읽을 수 없음")
                                break
    except Exception as e:  # noqa: BLE001
        problems.append(f"압축을 열 수 없음: {e}")
    return {"ok": not problems and n > 0, "files": n, "problems": problems[:20]}


def restore(path: str, target_dir: str) -> Dict[str, Any]:
    """비어 있는(또는 없는) 폴더에만 복구한다 — 운영 풀을 덮어쓰지 않도록."""
    if os.path.isdir(target_dir) and os.listdir(target_dir):
        raise ValueError("복구 대상 폴더가 비어 있지 않습니다. 새 폴더를 지정하세요.")
    v = verify(path)
    if not v["ok"]:
        raise ValueError("백업이 온전하지 않아 복구하지 않습니다: " + "; ".join(v["problems"]))
    os.makedirs(target_dir, exist_ok=True)
    base = os.path.abspath(target_dir)
    with tarfile.open(path, "r:gz") as tf:
        for m in tf.getmembers():
            dest = os.path.abspath(os.path.join(base, m.name))
            if not dest.startswith(base + os.sep) and dest != base:
                raise ValueError(f"안전하지 않은 경로: {m.name}")
            if m.isdir():
                os.makedirs(dest, exist_ok=True)
            elif m.isfile():
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as out:
                    out.write(tf.extractfile(m).read())
    return {"restored_to": base, "files": v["files"]}


def daily(force_check: bool = False) -> Optional[str]:
    """하루 한 번 자동 백업(로그인 때 호출). 느려지지 않게 별도 스레드, 실패해도 로그인에는 영향 없음. 시작했으면 'started'."""
    global _last_check
    if os.getenv("RENT3_BACKUP_DAILY", "1").strip() == "0":
        return None
    now = time.time()
    with _lock:
        if not force_check and now - _last_check < 600:
            return None
        _last_check = now
    try:
        items = list_backups()
        if items and time.time() - os.path.getmtime(os.path.join(backup_dir(), items[0]["name"])) < 20 * 3600:
            return None
    except OSError:
        return None

    def run() -> None:
        try:
            create()
        except Exception:  # noqa: BLE001 — 백업 실패가 서비스를 막지 않는다(관리인 화면에서 직접 실행·확인)
            pass
    threading.Thread(target=run, daemon=True).start()
    return "started"
