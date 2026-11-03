"""
rent5/store.py — 컬렉션 하나 = data/<이름>.json 파일 하나 (UTF-8, 사람이 열어 볼 수 있다).

- 쓰기는 임시 파일에 쓴 뒤 os.replace 로 한 번에 바꾼다 (쓰다 꺼져도 파일이 깨지지 않는다).
- 한 프로세스 안에서는 RLock 으로 묶는다. 여러 프로세스가 같은 폴더를 동시에 쓰는 용도는 아니다.
- 파일이 밖에서 바뀌면(운영자 스크립트, 손으로 고침) 다음에 읽을 때 다시 읽는다 (파일 수정 시각 확인).
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

Doc = Dict[str, Any]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._cache: Dict[str, List[Doc]] = {}
        self._seen: Dict[str, int] = {}                    # 컬렉션 → 마지막으로 읽거나 쓴 파일의 수정 시각

    def _path(self, collection: str) -> Path:
        return self.directory / f"{collection}.json"

    def _stamp(self, collection: str) -> int:
        path = self._path(collection)
        return path.stat().st_mtime_ns if path.exists() else 0

    def _load(self, collection: str) -> List[Doc]:
        if collection not in self._cache or self._seen.get(collection) != self._stamp(collection):
            path = self._path(collection)
            self._cache[collection] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            self._seen[collection] = self._stamp(collection)
        return self._cache[collection]

    def _save(self, collection: str) -> None:
        path = self._path(collection)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._cache[collection], ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._seen[collection] = self._stamp(collection)

    def all(self, collection: str) -> List[Doc]:
        with self.lock:
            return [dict(d) for d in self._load(collection)]

    def find(self, collection: str, **where: Any) -> List[Doc]:
        return [d for d in self.all(collection) if all(d.get(k) == v for k, v in where.items())]

    def first(self, collection: str, **where: Any) -> Optional[Doc]:
        found = self.find(collection, **where)
        return found[0] if found else None

    def insert(self, collection: str, doc: Doc) -> Doc:
        with self.lock:
            doc = {"id": secrets.token_hex(6), "created_at": now_iso(), **doc}
            self._load(collection).append(doc)
            self._save(collection)
            return dict(doc)

    def update(self, collection: str, doc_id: str, **changes: Any) -> Doc:
        with self.lock:
            for doc in self._load(collection):
                if doc["id"] == doc_id:
                    doc.update(changes)
                    doc["updated_at"] = now_iso()
                    self._save(collection)
                    return dict(doc)
        raise KeyError(doc_id)
