"""
apps/rent_dist/_rent3_core/poolsearch.py — 관리인용 Data Pool 검색·수정(통합관리 허브 pool.html).

흐름: 컬렉션 선택 → 컬럼 목록(체크) → 검색 값 → 결과 → 한 건 열어 고치기(사유 필수) → 저장/취소.
안전 규칙
 - 비밀번호·간편 비밀번호·초대/연결 비밀값·기기 목록 같은 **보호 필드는 화면에 내려보내지 않고 고칠 수도 없다**(`is_hidden`).
 - 디스크에서 암호화되는 필드(도어락·공동현관·계좌번호)는 값을 보여 주지도 검색하지도 고치지도 않는다(🔒). 그 값은 임대인·임차인 화면의 정식 경로로만 바뀐다.
 - id·생성/수정 시각, 목록·사전 형태의 필드는 읽기 전용. 나머지 문자·숫자·참/거짓 필드만 고칠 수 있다.
 - 저장은 모델 검증(타입)을 통과해야 하고, 바뀐 필드의 전/후 값(비밀 제외)을 PoolEditLog 에 사유·관리인과 함께 남긴다(이벤트 로그에도 기록).
 - 수납은 합계·청구월 검증이 있는 수납 화면의 수정 기능을 쓰는 것이 안전하다(여기서는 원본 값을 그대로 고친다).
"""
from __future__ import annotations

import json
import re
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError, NotFound
from . import fieldcrypto
from .pool import POOL_COLLECTIONS, POOL_REGISTRY, PoolObj, now_iso, pool

HIDDEN_RE = re.compile(r"(password|passwd|secret|hash|token|quick_devices|login_devices)", re.I)
READONLY = {"id", "created_at", "updated_at", "_kind"}
SHOW_FIRST = ("name", "tenant_name", "room_no", "billing_month", "payer_name", "branch", "status", "title", "username")
MASK = "•••(보호됨)"


class PoolEditLog(PoolObj):
    """관리인이 Data Pool 검색·수정 화면에서 고친 기록(전/후 값, 사유). 비밀·암호화 필드는 값을 남기지 않는다."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "Data Pool 수정 기록"

    id: str = Field(default_factory=lambda: f"pedit_{uuid4().hex[:8]}")
    admin: str = ""
    target_collection: str = ""
    target_kind: str = ""
    target_id: str = ""
    reason: str = ""
    changes: List[Dict[str, Any]] = Field(default_factory=list)

    def summary(self) -> str:
        return f"Data Pool 수정 [{self.target_collection}:{self.target_id}] {', '.join(c['field'] for c in self.changes)}"


def is_hidden(name: str) -> bool:
    return bool(HIDDEN_RE.search(name or ""))


def _check_collection(collection: str) -> None:
    if collection not in POOL_COLLECTIONS:
        raise NotFound(f"알 수 없는 Data Pool 컬렉션: '{collection}'", hint=f"허용 목록: {', '.join(POOL_COLLECTIONS)}")


def _kind_classes(collection: str) -> Dict[str, Any]:
    return {k: c for k, c in POOL_REGISTRY.items() if getattr(c, "collection", "") == collection and k != "pooleditlog"}


def _type_of(ann: Any) -> str:
    s = str(ann)
    if "bool" in s:
        return "bool"
    if "int" in s and "str" not in s:
        return "int"
    if "float" in s and "str" not in s:
        return "float"
    if "str" in s and "List" not in s and "Dict" not in s:
        return "str"
    return "json"


def schema(collection: str) -> Dict[str, Any]:
    _check_collection(collection)
    fields: Dict[str, Dict[str, Any]] = {}
    for kind, cls in _kind_classes(collection).items():
        for name, f in cls.model_fields.items():
            if is_hidden(name) or name in fields:
                continue
            t = _type_of(f.annotation)
            sealed = name in fieldcrypto.SENSITIVE_FIELDS
            fields[name] = {"name": name, "type": t, "sealed": sealed, "editable": (t != "json" and not sealed and name not in READONLY),
                            "searchable": not sealed, "kinds": [kind]}
    names = sorted(fields, key=lambda n: (SHOW_FIRST.index(n) if n in SHOW_FIRST else 99, n))
    items = pool().find(collection)
    return {"collection": collection, "count": len(items), "kinds": sorted(_kind_classes(collection)), "fields": [fields[n] for n in names]}


def _disp(v: Any, limit: int = 60) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def query(collection: str, columns: List[str], value: str, limit: int = 100) -> Dict[str, Any]:
    """공백으로 나눈 단어가 모두 체크한 컬럼 어딘가에 (대소문자 무시) 포함되면 일치. 암호화·보호 컬럼은 검색하지 않는다."""
    _check_collection(collection)
    sc = {f["name"]: f for f in schema(collection)["fields"]}
    cols = [c for c in columns if c in sc and sc[c]["searchable"]]
    if not cols:
        raise DomainError("검색할 컬럼을 하나 이상 체크해 주세요.", hint="🔒 표시된 컬럼(암호화)과 보호된 컬럼은 검색할 수 없습니다.")
    terms = [t for t in (value or "").strip().lower().split() if t]
    if not terms:
        raise DomainError("검색 값을 입력해 주세요.")
    show = [n for n in SHOW_FIRST if n in sc][:3]
    show_cols = list(dict.fromkeys(cols + show))
    rows, total = [], 0
    for it in pool().find(collection):
        # 여러 단어는 모두(AND) 포함돼야 하고, 각 단어는 체크한 컬럼 중 어느 하나에 들어 있으면 된다
        if all(any(t in str(it.get(c, "")).lower() for c in cols) for t in terms):
            total += 1
            if len(rows) < limit:
                rows.append({"id": it.get("id", ""), "kind": it.get("_kind", ""), "cells": {c: _disp(it.get(c, "")) for c in show_cols}})
    return {"collection": collection, "columns": show_cols, "total": total, "shown": len(rows), "rows": rows, "truncated": total > len(rows)}


def _load(collection: str, obj_id: str):
    _check_collection(collection)
    for kind, cls in _kind_classes(collection).items():
        raw = pool().get(collection, kind, obj_id)
        if raw:
            return cls, cls.model_validate(cls._clean(raw))
    raise NotFound(f"'{obj_id}' 객체를 {collection} 에서 찾을 수 없습니다.")


def get_item(collection: str, obj_id: str) -> Dict[str, Any]:
    cls, obj = _load(collection, obj_id)
    sc = {f["name"]: f for f in schema(collection)["fields"]}
    dump = obj.model_dump()
    out = []
    for name in cls.model_fields:
        if is_hidden(name):
            continue
        f = sc.get(name) or {"type": "json", "sealed": False, "editable": False}
        v = dump.get(name)
        out.append({"name": name, "type": f["type"], "sealed": f["sealed"], "editable": f["editable"],
                    "value": ("🔒 암호화됨(수정 불가)" if f["sealed"] and v else ("" if f["sealed"] else (_disp(v, 4000) if f["type"] == "json" else v)))})
    out.sort(key=lambda x: (SHOW_FIRST.index(x["name"]) if x["name"] in SHOW_FIRST else 99, x["name"]))
    hidden = [n for n in cls.model_fields if is_hidden(n)]
    return {"collection": collection, "id": obj_id, "kind": cls.kind_name(), "fields": out, "protected_fields": hidden}


def update(collection: str, obj_id: str, changes: Dict[str, Any], reason: str, admin: str = "관리인") -> Dict[str, Any]:
    cls, obj = _load(collection, obj_id)
    reason = (reason or "").strip()
    if len(reason) < 2:
        raise DomainError("수정 사유를 적어 주세요.", hint="누가 왜 고쳤는지 기록으로 남습니다(예: 이름 오타 정정).")
    sc = {f["name"]: f for f in schema(collection)["fields"]}
    clean: Dict[str, Any] = {}
    for k, v in (changes or {}).items():
        f = sc.get(k)
        if f is None or is_hidden(k):
            raise DomainError(f"'{k}' 항목은 고칠 수 없습니다(보호된 항목이거나 없는 항목).")
        if not f["editable"]:
            raise DomainError(f"'{k}' 항목은 고칠 수 없습니다.", hint="id·시각·목록 형태·🔒 암호화 항목은 읽기 전용입니다.")
        clean[k] = v
    if not clean:
        raise DomainError("바뀐 내용이 없습니다.")
    base = obj.model_dump()
    try:
        new = cls.model_validate({**base, **clean})
    except Exception as exc:       # pydantic ValidationError — 값 형식이 맞지 않음
        raise DomainError("값 형식이 맞지 않습니다.", hint=str(exc).splitlines()[0][:200])
    diff = [{"field": k, "before": base.get(k), "after": getattr(new, k)} for k in clean if str(base.get(k)) != str(getattr(new, k))]
    if not diff:
        raise DomainError("바뀐 내용이 없습니다.")
    new.save(action="admin_pool_edit", app_source="rent3_admin_pool")
    PoolEditLog(admin=admin, target_collection=collection, target_kind=cls.kind_name(), target_id=obj_id, reason=reason, changes=diff).save(action="admin_pool_edit_logged", app_source="rent3_admin_pool")
    return {"id": obj_id, "changed": [d["field"] for d in diff], "changes": diff}


def recent_edits(limit: int = 15) -> List[Dict[str, Any]]:
    rows = sorted(PoolEditLog.all(), key=lambda x: x.created_at, reverse=True)[:limit]
    return [{"at": r.created_at, "admin": r.admin, "collection": r.target_collection, "id": r.target_id, "reason": r.reason, "changes": r.changes} for r in rows]


def redact_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """기존 원문 뷰어용: 보호 필드 값을 가린다(관리인 화면에도 해시·기기 목록을 내려보내지 않는다)."""
    return {k: (MASK if is_hidden(k) and v not in ("", None, [], {}) else v) for k, v in it.items()}
