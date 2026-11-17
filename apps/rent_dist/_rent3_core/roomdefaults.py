"""
apps/rent_dist/_rent3_core/roomdefaults.py — 방(Room)에 자동으로 들어갔던 '샘플 기본값' 정리.

예전 Room 모델은 방을 만들 때마다 샘플 등기부(소유자 '김임대', 번호 1101-2026-884920, 권리 깨끗함, 확인 날짜), 가전 옵션 5개, 상태 설명을 자동으로 채웠다.
실제 등기부 정보가 아닌데 공개 매물에 '등기클린'·옵션으로 나올 수 있어, 모델 기본값은 비우고 이미 저장된 방에서는 이 도구로 샘플과 **정확히 같은** 값만 지운다.
 - 사람이 직접 고친 값·실제 등기부에서 읽은 값은 샘플과 다르므로 건드리지 않는다.
 - 도어락 비밀번호 기본값('2580*')은 실제로 쓰고 있을 수 있어 자동으로 지우지 않고 건수만 알린다.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import backup as bk
from .models import Room

SAMPLE_ISSUE_NUMBER = "1101-2026-884920"
SAMPLE_STATUS = "남향 풀옵션, 도배·장판 클린 완료"
SAMPLE_OPTIONS = [
    {"item": "시스템 에어컨", "quantity": 1, "condition": "정상(최상)"},
    {"item": "드럼 세탁기", "quantity": 1, "condition": "정상(양호)"},
    {"item": "빌트인 냉장고", "quantity": 1, "condition": "정상(최상)"},
    {"item": "인덕션 쿡탑", "quantity": 1, "condition": "정상"},
    {"item": "디지털 도어락", "quantity": 1, "condition": "신규 교체"},
]
SAMPLE_DOOR = "2580*"


def _findings(r: Room) -> Dict[str, bool]:
    reg = r.registry_info or {}
    return {
        "registry": reg.get("issue_number") == SAMPLE_ISSUE_NUMBER,
        "options": list(r.option_list or []) == SAMPLE_OPTIONS,
        "status": (r.status_description or "") == SAMPLE_STATUS,
        "door": (r.doorlock_pwd or "") == SAMPLE_DOOR,
    }


def plan() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    counts = {"registry": 0, "options": 0, "status": 0, "door": 0}
    for r in Room.all():
        f = _findings(r)
        for k, v in f.items():
            counts[k] += int(v)
        if f["registry"] or f["options"] or f["status"]:
            rows.append({"id": r.id, "branch": r.branch, "room_no": r.room_no, "published": bool(r.is_published and not r.is_occupied), **f})
    return {"rooms_total": len(Room.all()), "rooms_to_clean": len(rows), "counts": counts, "public_now": sum(1 for x in rows if x["published"]), "rows": rows}


def apply() -> Dict[str, Any]:
    """샘플과 정확히 같은 등기부·옵션·상태 설명만 비운다(풀 전체를 먼저 자동 백업)."""
    p = plan()
    if not p["rows"]:
        return {"cleaned": 0, "backup": "", "fields": 0}
    info = bk.create()
    cleaned = fields = 0
    for r in Room.all():
        f = _findings(r)
        if not (f["registry"] or f["options"] or f["status"]):
            continue
        if f["registry"]:
            r.registry_info, r.registry_checked = {}, False
            fields += 1
        if f["options"]:
            r.option_list = []
            fields += 1
        if f["status"]:
            r.status_description = ""
            fields += 1
        r.save(action="room_sample_defaults_cleared", app_source="rent3_roomdefaults")
        cleaned += 1
    return {"cleaned": cleaned, "fields": fields, "backup": info.get("name", ""), "door_defaults_left": p["counts"]["door"]}
