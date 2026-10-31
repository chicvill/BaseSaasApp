"""
apps/rent4/backend/pool.py — Rent4 Knowledge Pool (실시간 JSON 저장소).

- 컬렉션 1개 = data/rent4_pool/<name>.json 파일 1개 (UTF-8, indent=2, 사람이 직접 열람 가능)
- 쓰기는 항상 임시 파일 기록 후 os.replace 로 원자적 교체
- 파일 mtime 을 감지해 다른 프로세스(--app 단독 실행, 수동 편집)의 변경도 즉시 반영
- 저장 위치: 환경변수 RENT4_POOL_DIR > 기본 <프로젝트>/data/rent4_pool
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_POOL_DIR = os.path.join(ROOT_DIR, "data", "rent4_pool")

T = TypeVar("T", bound="PoolObj")


class KnowledgePool:
    def __init__(self, directory: Optional[str] = None):
        self.directory = directory or os.getenv("RENT4_POOL_DIR") or DEFAULT_POOL_DIR
        self._lock = threading.RLock()
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._mtime: Dict[str, float] = {}

    def path(self, name: str) -> str:
        return os.path.join(self.directory, f"{name}.json")

    def read(self, name: str) -> List[Dict[str, Any]]:
        with self._lock:
            path = self.path(name)
            if not os.path.exists(path):
                self._cache[name], self._mtime[name] = [], 0.0
                return []
            mtime = os.path.getmtime(path)
            if self._mtime.get(name) != mtime:
                with open(path, encoding="utf-8") as f:
                    self._cache[name] = json.load(f)
                self._mtime[name] = mtime
            return [dict(row) for row in self._cache[name]]

    def write(self, name: str, rows: List[Dict[str, Any]]) -> None:
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            path = self.path(name)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            self._cache[name] = [dict(row) for row in rows]
            self._mtime[name] = os.path.getmtime(path)


_pool = KnowledgePool()


def pool() -> KnowledgePool:
    return _pool


def use_pool_dir(directory: str) -> KnowledgePool:
    """테스트/별도 지점용으로 저장 위치를 교체한다."""
    global _pool
    _pool = KnowledgePool(directory)
    return _pool


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PoolObj(BaseModel):
    """Knowledge Pool 에 저장되는 도메인 객체의 부모. 스스로 저장·조회·삭제한다."""
    model_config = ConfigDict(extra="ignore")

    collection: ClassVar[str] = ""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = Field(default_factory=now_iso)

    @classmethod
    def all(cls: Type[T]) -> List[T]:
        return [cls(**row) for row in pool().read(cls.collection)]

    @classmethod
    def find(cls: Type[T], **where: Any) -> List[T]:
        return [o for o in cls.all() if all(getattr(o, k) == v for k, v in where.items())]

    @classmethod
    def first(cls: Type[T], **where: Any) -> Optional[T]:
        found = cls.find(**where)
        return found[0] if found else None

    @classmethod
    def replace_all(cls: Type[T], objs: List[T]) -> None:
        pool().write(cls.collection, [o.model_dump() for o in objs])

    def save(self: T) -> T:
        rows = pool().read(self.collection)
        data = self.model_dump()
        for i, row in enumerate(rows):
            if row.get("id") == self.id:
                rows[i] = data
                break
        else:
            rows.append(data)
        pool().write(self.collection, rows)
        return self

    def delete(self) -> None:
        rows = [r for r in pool().read(self.collection) if r.get("id") != self.id]
        pool().write(self.collection, rows)
