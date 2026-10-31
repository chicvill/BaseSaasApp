"""
apps/_rent3_core/pool.py — 실시간 공동 JSON Data Pool 엔진 (`JsonDataPool` & `PoolObj`).

헌법(AGENT.md) 구현 핵심:
1. 불변의 BaseSaasApp 코어(core/)를 단 1줄도 수정하지 않는다.
2. 모든 도메인 구성 요소(Building, Room, Contract, Pay, Outlay, User 등)는 `PoolObj`를 상속한 독립 객체다.
3. 객체 데이터가 생성·수정·삭제될 때마다 `data/rent3_pool/<collection>.json` 파일이
   임시파일 기록 후 원자적 교체(`.tmp` -> `os.replace`)로 즉시 실시간 업데이트된다.
4. 파일 수정 시각(`mtime`) 감지를 통해 통합 게이트웨이 내 5개 분리 앱은 물론,
   독립 프로세스(`--app`)나 외부 JSON 편집까지 실시간으로 동일한 공동 Data Pool을 공유한다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.errors import NotFound
from core.kernel import actor

T = TypeVar("T", bound="PoolObj")

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_POOL_DIR = os.path.join(ROOT_DIR, "data", "rent3_pool")

POOL_COLLECTIONS = (
    "users",
    "buildings",
    "rooms",
    "cosign_sessions",
    "contracts",
    "payments",
    "outlays",
    "ops",
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JsonDataPool:
    """스레드 안전 + mtime 실시간 리로드 + 원자적 파일 쓰기를 지원하는 공동 JSON Data Pool."""

    def __init__(self, pool_dir: Optional[str] = None) -> None:
        self.pool_dir = os.path.abspath(pool_dir or os.getenv("RENT3_POOL_DIR") or DEFAULT_POOL_DIR)
        self._lock = threading.RLock()
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._mtimes: Dict[str, float] = {}
        self._revisions: Dict[str, int] = {}
        self.ensure_dir()

    def ensure_dir(self) -> None:
        os.makedirs(self.pool_dir, exist_ok=True)
        os.makedirs(self.rooms_dir, exist_ok=True)

    def file_path(self, collection: str) -> str:
        return os.path.join(self.pool_dir, f"{collection}.json")

    @property
    def rooms_dir(self) -> str:
        return os.path.join(self.pool_dir, "rooms")

    @property
    def meta_path(self) -> str:
        return os.path.join(self.pool_dir, "_pool_meta.json")

    @property
    def events_path(self) -> str:
        return os.path.join(self.pool_dir, "_events.jsonl")

    def _atomic_write_json(self, target_path: str, payload: Any) -> None:
        self.ensure_dir()
        pid = os.getpid()
        tid = threading.get_ident()
        tmp_path = f"{target_path}.{pid}.{tid}.tmp"
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        with open(tmp_path, "w", encoding="utf-8") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        last_err: Optional[Exception] = None
        for _ in range(8):
            try:
                os.replace(tmp_path, target_path)
                return
            except PermissionError as e:
                last_err = e
                time.sleep(0.015)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        if last_err:
            raise last_err

    def _load_collection_unlocked(self, collection: str) -> List[Dict[str, Any]]:
        path = self.file_path(collection)
        if not os.path.exists(path):
            self._cache[collection] = []
            self._mtimes[collection] = 0.0
            self._revisions.setdefault(collection, 0)
            return []

        mtime = os.path.getmtime(path)
        if collection in self._cache and self._mtimes.get(collection) == mtime:
            return self._cache[collection]

        with open(path, "r", encoding="utf-8") as fp:
            raw = json.load(fp)

        if isinstance(raw, dict):
            items = raw.get("items", [])
            self._revisions[collection] = int(raw.get("revision", 0))
        elif isinstance(raw, list):
            items = raw
            self._revisions[collection] = self._revisions.get(collection, 0) + 1
        else:
            items = []

        self._cache[collection] = list(items)
        self._mtimes[collection] = mtime
        return self._cache[collection]

    def _clean_building_name(self, raw_name: Optional[str], branch: Optional[str] = None) -> str:
        name = (raw_name or "").strip()
        if not name and branch:
            alias = {"main": "시크빌", "sunho": "선호아트빌", "sinbi": "신비주택"}
            name = alias.get(branch.lower(), branch)
        if not name:
            name = "시크빌"
        parts = re.split(r"[\s·/&]+", name)
        clean = parts[0] if parts else name
        clean = re.sub(r"[^\w가-힣0-9]", "", clean)
        return clean or "시크빌"

    def _extract_move_in_date_8digits(self, date_val: Any) -> str:
        s = str(date_val or "").strip()
        nums = re.findall(r"\d+", s)
        if len(nums) >= 3:
            y, m, d = int(nums[0]), int(nums[1]), int(nums[2])
            return f"{y:04d}{m:02d}{d:02d}"
        digits = "".join(nums)
        if len(digits) == 8:
            return digits
        return "vacant"

    def _normalize_billing_ym_int(self, ym_val: Any) -> int:
        """연월 값을 6자리 정수(예: 202601)로 정규화."""
        s = str(ym_val or "").strip()
        nums = re.findall(r"\d+", s)
        if len(nums) >= 2:
            return int(nums[0]) * 100 + int(nums[1])
        digits = "".join(nums)
        if len(digits) >= 6:
            return int(digits[:6])
        return 0

    def _format_paid_date_dots(self, paid_val: Any) -> str:
        """수납일 값을 'YYYY.M.D' 점 표기 형식으로 정규화 (예: '2026.1.12')."""
        s = str(paid_val or "").strip()
        nums = re.findall(r"\d+", s)
        if len(nums) >= 3:
            return f"{int(nums[0])}.{int(nums[1])}.{int(nums[2])}"
        return s or ""

    def format_room_filename(self, room_data: Dict[str, Any]) -> str:
        """건물/호실/전입일 기준 고유 파일명 생성 (예: '시크빌-201-20250101.json')."""
        branch = room_data.get("branch", "main")
        bldg_name = room_data.get("building_name")
        if not bldg_name:
            b_id = room_data.get("id_building")
            if b_id:
                for b in self._load_collection_unlocked("buildings"):
                    if b.get("id") == b_id:
                        bldg_name = b.get("building_name")
                        break
        clean_bldg = self._clean_building_name(bldg_name, branch)
        room_no = str(room_data.get("room_no") or "").strip() or "0"

        move_in = room_data.get("move_in_date")
        if not move_in:
            ct_id = room_data.get("active_contract_id")
            if ct_id:
                for ct in self._load_collection_unlocked("contracts"):
                    if ct.get("id") == ct_id:
                        move_in = ct.get("move_in_date")
                        break
        date_8d = self._extract_move_in_date_8digits(move_in)
        return f"{clean_bldg}-{room_no}-{date_8d}.json"

    def _sync_individual_room_files_unlocked(self, rooms: List[Dict[str, Any]]) -> None:
        """각 호실 객체를 건물/호실/전입일 기준 개별 json 파일로 data/rent3_pool/rooms/ 에 분리 저장.
        또한 각 호실 객체에 [적용수납년월, 수납일, 임대료, 관리비, 부가세] 형식의 수납 이력(payments)을 동기화."""
        os.makedirs(self.rooms_dir, exist_ok=True)
        active_filenames = set()

        # payments.json 의 수납 내역을 id_room 및 (branch, room_no) 기준으로 맵핑
        all_payments = self._load_collection_unlocked("payments")
        payments_by_room: Dict[str, List[List[Any]]] = {}
        for p in all_payments:
            id_room = p.get("id_room") or ""
            branch = p.get("branch") or ""
            room_no = str(p.get("room_no") or "").strip()

            ym = self._normalize_billing_ym_int(p.get("billing_month") or p.get("billing_ym"))
            paid_d = self._format_paid_date_dots(p.get("paid_date"))
            rent = int(p.get("rent_amount") or 0)
            maint = int(p.get("maintenance_amount") or 0)
            vat = int(p.get("vat_amount") or 0)
            rec = [ym, paid_d, rent, maint, vat]

            if id_room:
                payments_by_room.setdefault(f"id:{id_room}", []).append(rec)
            if branch and room_no:
                payments_by_room.setdefault(f"br:{branch}:{room_no}", []).append(rec)

        for r in rooms:
            fn = self.format_room_filename(r)
            active_filenames.add(fn)

            # 불필요한 내부 파일경로 중복 필드 제거
            r.pop("room_file", None)
            r.pop("room_file_path", None)

            # name에서 '201호 (남향 풀옵션)'처럼 status_description과 중복되는 부가 수식어가 있으면 호실 번호 중심 또는 명확한 명칭으로 간소화
            clean_bldg = fn.split("-")[0] if "-" in fn else "시크빌"
            room_no = str(r.get("room_no") or "").strip()
            move_in = r.get("move_in_date") or ""

            # 호실의 기존 payments 목록과 payments.json 의 내역을 합쳐서 최신화
            existing_pmts = list(r.get("payments") or [])
            matched_pool_pmts = (
                payments_by_room.get(f"id:{r.get('id', '')}")
                or payments_by_room.get(f"br:{r.get('branch', '')}:{room_no}")
                or []
            )
            combined_dict: Dict[tuple, List[Any]] = {}
            for item in existing_pmts:
                if isinstance(item, list) and len(item) >= 5:
                    combined_dict[(item[0], str(item[1]))] = [
                        self._normalize_billing_ym_int(item[0]),
                        self._format_paid_date_dots(item[1]),
                        int(item[2]),
                        int(item[3]),
                        int(item[4]),
                    ]
            for item in matched_pool_pmts:
                combined_dict[(item[0], str(item[1]))] = item

            sorted_payments = sorted(combined_dict.values(), key=lambda x: (x[0], x[1]))
            r["payments"] = sorted_payments

            # 파일 내부 data 객체 최적화 (중복 정보 및 무의미한 경로 제거)
            clean_r = dict(r)
            clean_r.pop("room_file", None)
            clean_r.pop("room_file_path", None)

            # name과 status_description 중복 정리: name이 단순히 '201호 (남향 풀옵션)'처럼 status_description의 앞부분과 중복되면 간소화
            raw_name = clean_r.get("name", "")
            stat_desc = clean_r.get("status_description", "")
            if "남향 풀옵션" in raw_name and "남향 풀옵션" in stat_desc:
                # name을 호실명으로 단정화하여 중복 제거
                clean_r["name"] = f"{room_no}호"

            target_path = os.path.join(self.rooms_dir, fn)
            payload = {
                "pool": "rent3_pool",
                "collection": "rooms",
                "file_name": fn,
                "building_name": clean_bldg,
                "room_no": room_no,
                "move_in_date": move_in,
                "is_occupied": bool(r.get("id_tenant") and r.get("active_contract_id")),
                "updated_at": now_iso(),
                "data": clean_r,
            }
            self._atomic_write_json(target_path, payload)

        try:
            for fname in os.listdir(self.rooms_dir):
                if fname.endswith(".json") and fname not in active_filenames:
                    try:
                        os.remove(os.path.join(self.rooms_dir, fname))
                    except OSError:
                        pass
        except OSError:
            pass

    def get_room_file(self, filename: str) -> Optional[Dict[str, Any]]:
        """rooms/*.json 개별 호실 파일을 O(1)로 직접 읽기."""
        if not filename.endswith(".json"):
            filename = f"{filename}.json"
        path = os.path.join(self.rooms_dir, filename)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return None

    def list_room_files(self) -> List[Dict[str, Any]]:
        """rooms/ 디렉터리에 분리 저장된 모든 개별 호실 파일 메타데이터 목록."""
        if not os.path.exists(self.rooms_dir):
            return []
        items = []
        try:
            for fn in sorted(os.listdir(self.rooms_dir)):
                if not fn.endswith(".json"):
                    continue
                fpath = os.path.join(self.rooms_dir, fn)
                try:
                    with open(fpath, "r", encoding="utf-8") as fp:
                        raw = json.load(fp)
                    items.append({
                        "file_name": fn,
                        "file_path": fpath,
                        "building_name": raw.get("building_name", ""),
                        "room_no": raw.get("room_no", ""),
                        "move_in_date": raw.get("move_in_date", ""),
                        "is_occupied": raw.get("is_occupied", False),
                        "size_bytes": os.path.getsize(fpath),
                        "updated_at": raw.get("updated_at", ""),
                    })
                except Exception:
                    pass
        except OSError:
            pass
        return items

    def _save_collection_unlocked(self, collection: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        path = self.file_path(collection)
        rev = self._revisions.get(collection, 0) + 1
        updated = now_iso()
        if collection == "rooms":
            self._sync_individual_room_files_unlocked(items)
        envelope = {
            "pool": "rent3_pool",
            "collection": collection,
            "revision": rev,
            "updated_at": updated,
            "count": len(items),
            "items": items,
        }
        self._atomic_write_json(path, envelope)
        self._cache[collection] = list(items)
        self._revisions[collection] = rev
        self._mtimes[collection] = os.path.getmtime(path) if os.path.exists(path) else time.time()
        self._refresh_meta_unlocked(updated_collection=collection, updated_at=updated)
        return envelope

    def _refresh_meta_unlocked(self, updated_collection: str = "", updated_at: str = "") -> Dict[str, Any]:
        ts = updated_at or now_iso()
        prev_total_rev = 0
        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path, "r", encoding="utf-8") as fp:
                    prev_total_rev = int(json.load(fp).get("total_revision", 0))
            except Exception:
                prev_total_rev = 0

        collections_summary: Dict[str, Any] = {}
        total_objects = 0
        for col in POOL_COLLECTIONS:
            p = self.file_path(col)
            exists = os.path.exists(p)
            items = self._load_collection_unlocked(col) if exists else []
            cnt = len(items)
            total_objects += cnt
            collections_summary[col] = {
                "file": f"{col}.json",
                "exists": exists,
                "count": cnt,
                "revision": self._revisions.get(col, 0),
                "size_bytes": os.path.getsize(p) if exists else 0,
                "updated_at": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")
                if exists else "",
            }

        meta = {
            "pool_name": "rent3_pool",
            "pool_dir": self.pool_dir,
            "total_revision": prev_total_rev + 1,
            "total_objects": total_objects,
            "last_updated_collection": updated_collection,
            "updated_at": ts,
            "collections": collections_summary,
        }
        self._atomic_write_json(self.meta_path, meta)
        return meta

    def _append_event_unlocked(
        self,
        collection: str,
        kind: str,
        obj_id: str,
        action: str,
        summary: str,
        app_source: str = "",
    ) -> None:
        self.ensure_dir()
        ev = {
            "at": now_iso(),
            "actor": actor.get(),
            "app_source": app_source or "rent3_pool",
            "collection": collection,
            "kind": kind,
            "id": obj_id,
            "action": action,
            "summary": summary,
        }
        with open(self.events_path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    # ── 공개 CRUD 인터페이스 ──
    def put(
        self,
        collection: str,
        kind: str,
        obj_id: str,
        data: Dict[str, Any],
        action: str = "saved",
        summary: str = "",
        app_source: str = "",
    ) -> Dict[str, Any]:
        with self._lock:
            items = list(self._load_collection_unlocked(collection))
            record = {**data, "_kind": kind, "updated_at": now_iso()}
            replaced = False
            for idx, existing in enumerate(items):
                if existing.get("id") == obj_id and existing.get("_kind", kind) == kind:
                    items[idx] = record
                    replaced = True
                    break
            if not replaced:
                items.append(record)
            self._save_collection_unlocked(collection, items)
            self._append_event_unlocked(
                collection=collection,
                kind=kind,
                obj_id=obj_id,
                action=action,
                summary=summary or f"{kind}:{obj_id}",
                app_source=app_source,
            )

            # 수납(payments)이 저장될 때, 해당하는 room의 payments 리스트에도 자동 동기화
            if collection == "payments":
                try:
                    self._sync_payment_to_room_unlocked(record)
                except Exception:
                    pass

            return record

    def _sync_payment_to_room_unlocked(self, payment_record: Dict[str, Any]) -> None:
        """새로 저장된 payment_record를 매칭되는 room 객체의 payments 배열에 실시간 반영."""
        id_room = payment_record.get("id_room") or ""
        branch = payment_record.get("branch") or ""
        room_no = str(payment_record.get("room_no") or "").strip()

        rooms = list(self._load_collection_unlocked("rooms"))
        target_idx = None
        for idx, r in enumerate(rooms):
            if id_room and r.get("id") == id_room:
                target_idx = idx
                break
            if branch and room_no and r.get("branch") == branch and str(r.get("room_no", "")).strip() == room_no:
                target_idx = idx
                break

        if target_idx is not None:
            r = rooms[target_idx]
            ym = self._normalize_billing_ym_int(payment_record.get("billing_month") or payment_record.get("billing_ym"))
            paid_d = self._format_paid_date_dots(payment_record.get("paid_date"))
            rent = int(payment_record.get("rent_amount") or 0)
            maint = int(payment_record.get("maintenance_amount") or 0)
            vat = int(payment_record.get("vat_amount") or 0)
            entry = [ym, paid_d, rent, maint, vat]

            current_list = list(r.get("payments") or [])
            filtered = [
                it for it in current_list
                if not (isinstance(it, list) and len(it) >= 2 and it[0] == ym and str(it[1]) == paid_d)
            ]
            filtered.append(entry)
            r["payments"] = sorted(filtered, key=lambda x: (x[0], x[1]))
            # 방 컬렉션 및 개별 파일 갱신
            self._save_collection_unlocked("rooms", rooms)

    def get(self, collection: str, kind: str, obj_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for item in self._load_collection_unlocked(collection):
                if item.get("id") == obj_id and item.get("_kind", kind) == kind:
                    return dict(item)
        return None

    def find(self, collection: str, kind: Optional[str] = None, **where: Any) -> List[Dict[str, Any]]:
        with self._lock:
            items = self._load_collection_unlocked(collection)
            results: List[Dict[str, Any]] = []
            for item in items:
                if kind and item.get("_kind") != kind:
                    continue
                matched = True
                for field, val in where.items():
                    if val is None:
                        continue
                    if item.get(field) != val:
                        matched = False
                        break
                if matched:
                    results.append(dict(item))
            return results

    def delete(
        self,
        collection: str,
        kind: str,
        obj_id: str,
        action: str = "deleted",
        summary: str = "",
        app_source: str = "",
    ) -> bool:
        with self._lock:
            items = list(self._load_collection_unlocked(collection))
            kept = [
                it for it in items
                if not (it.get("id") == obj_id and it.get("_kind", kind) == kind)
            ]
            if len(kept) == len(items):
                return False
            self._save_collection_unlocked(collection, kept)
            self._append_event_unlocked(
                collection=collection,
                kind=kind,
                obj_id=obj_id,
                action=action,
                summary=summary or f"{kind}:{obj_id}",
                app_source=app_source,
            )
            return True

    def raw_collection(self, collection: str) -> Dict[str, Any]:
        """JSON 파일 원문 봉투를 그대로 반환 (통합관리 뷰어용)."""
        if collection not in POOL_COLLECTIONS:
            raise NotFound(f"알 수 없는 Data Pool 컬렉션: '{collection}'", hint=f"허용 목록: {', '.join(POOL_COLLECTIONS)}")
        with self._lock:
            items = self._load_collection_unlocked(collection)
            path = self.file_path(collection)
            return {
                "pool": "rent3_pool",
                "collection": collection,
                "file": f"{collection}.json",
                "revision": self._revisions.get(collection, 0),
                "count": len(items),
                "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
                "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
                if os.path.exists(path) else "",
                "items": items,
            }

    def overview(self) -> Dict[str, Any]:
        """전체 공동 Data Pool 메타 요약."""
        with self._lock:
            return self._refresh_meta_unlocked()

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.events_path):
                return []
            with open(self.events_path, "r", encoding="utf-8") as fp:
                lines = [ln.strip() for ln in fp.readlines() if ln.strip()]
            parsed: List[Dict[str, Any]] = []
            for ln in reversed(lines[-max(1, min(limit, 500)):]):
                try:
                    parsed.append(json.loads(ln))
                except Exception:
                    continue
            return parsed

    def ensure_all_files_initialized(self) -> None:
        """모든 표준 JSON 컬렉션 파일이 디스크에 가시적으로 존재하도록 보장."""
        with self._lock:
            for col in POOL_COLLECTIONS:
                p = self.file_path(col)
                if not os.path.exists(p):
                    self._save_collection_unlocked(col, [])


_pool_instance: Optional[JsonDataPool] = None


def pool() -> JsonDataPool:
    global _pool_instance
    desired = os.path.abspath(os.getenv("RENT3_POOL_DIR") or DEFAULT_POOL_DIR)
    if _pool_instance is None:
        _pool_instance = JsonDataPool(desired)
    return _pool_instance


def use_pool_dir(path: str) -> JsonDataPool:
    """테스트 격리 또는 커스텀 Data Pool 경로 지정용."""
    global _pool_instance
    os.environ["RENT3_POOL_DIR"] = os.path.abspath(path)
    _pool_instance = JsonDataPool(path)
    return _pool_instance


POOL_REGISTRY: Dict[str, Type["PoolObj"]] = {}


class PoolObj(BaseModel):
    """공동 JSON Data Pool에 실시간으로 직렬화·저장되는 모든 도메인 객체의 부모 클래스."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = ""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        POOL_REGISTRY[cls.kind_name()] = cls

    @classmethod
    def kind_name(cls) -> str:
        return cls.__name__.lower()

    def summary(self) -> str:
        return f"{self.kind_name()}:{self.id}"

    def save(self: T, action: str = "saved", app_source: str = "") -> T:
        self.updated_at = now_iso()
        payload = self.model_dump()
        pool().put(
            collection=self.collection,
            kind=self.kind_name(),
            obj_id=self.id,
            data=payload,
            action=action,
            summary=self.summary(),
            app_source=app_source,
        )
        return self

    def delete(self, action: str = "deleted", app_source: str = "") -> bool:
        return pool().delete(
            collection=self.collection,
            kind=self.kind_name(),
            obj_id=self.id,
            action=action,
            summary=self.summary(),
            app_source=app_source,
        )

    @classmethod
    def _clean(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(raw)
        d.pop("_kind", None)
        return d

    @classmethod
    def get(cls: Type[T], obj_id: str) -> Optional[T]:
        raw = pool().get(cls.collection, cls.kind_name(), obj_id)
        return cls.model_validate(cls._clean(raw)) if raw else None

    @classmethod
    def require(cls: Type[T], obj_id: str) -> T:
        obj = cls.get(obj_id)
        if obj is None:
            raise NotFound(
                f"{cls.label or cls.__name__} '{obj_id}' 객체를 공동 Data Pool에서 찾을 수 없습니다.",
                hint="목록을 새로고침한 뒤 다시 선택해 주세요.",
            )
        return obj

    @classmethod
    def find(cls: Type[T], **where: Any) -> List[T]:
        rows = pool().find(cls.collection, cls.kind_name(), **where)
        return [cls.model_validate(cls._clean(r)) for r in rows]

    @classmethod
    def first(cls: Type[T], **where: Any) -> Optional[T]:
        found = cls.find(**where)
        return found[0] if found else None

    @classmethod
    def all(cls: Type[T]) -> List[T]:
        return cls.find()

    @classmethod
    def count(cls, **where: Any) -> int:
        return len(cls.find(**where))

