"""
apps/rent_dist/_rent3_core/legacyregistry.py — 전자계약 이전부터 살던 임차인의 '입주 전 등기부 확인'을 완료로 기록.

입주 전 등기부 확인은 공동 전자계약으로 새로 체결한 계약에서 임차인이 하는 절차다. 전자계약이 아닌 기존 계약(엑셀 이관·중개 계약서 등록)은
이미 입주해 살고 있어 다시 할 대상이 아니므로, '확인 완료(이전 입주자)'로 표시해 둔다.
 - 대상: 전자계약 세션 없이 만들어진(cosign_session_id 가 비어 있는) 진행 중 계약 중, 아직 확인 기록이 없는 것
 - 출입 정보 잠금(keys_released)·수납·계약 조건은 건드리지 않는다. 기록은 balance_registry 에 legacy=True 로 남는다.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import backup as bk
from .models import Contract
from .pool import now_iso


def _targets() -> List[Contract]:
    return [c for c in Contract.all()
            if not c.cosign_session_id and c.status == "ACTIVE" and not (c.balance_registry or {}).get("checked_at")]


def plan() -> Dict[str, Any]:
    t = _targets()
    return {"contracts_total": len(Contract.all()), "to_mark": len(t),
            "rows": [{"id": c.id, "branch": c.branch, "room_no": c.room_no, "tenant_name": c.tenant_name} for c in t]}


def apply() -> Dict[str, Any]:
    t = _targets()
    if not t:
        return {"marked": 0, "backup": ""}
    info = bk.create()
    for c in t:
        c.balance_registry = {"checked_at": now_iso(), "changed": False, "legacy": True, "issue_number": "", "mortgage_cleared": False, "read": {},
                              "evidence": {"legacy": "전자계약 이전 입주자 — 등기부 확인 완료로 기록"}}
        c.save(action="balance_registry_legacy_marked", app_source="rent3_legacyregistry")
    return {"marked": len(t), "backup": info.get("name", "")}
