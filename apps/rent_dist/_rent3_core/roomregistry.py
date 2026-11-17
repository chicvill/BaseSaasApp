"""
apps/rent_dist/_rent3_core/roomregistry.py — 호실의 등기부 확인: 임대인이 등기사항증명서(PDF·사진)를 올리면 AI 가 읽고,
임대인이 원본과 대조해 확정해야만 방에 '확인됨'으로 기록된다.

원칙
- 값은 AI 가 읽은 것을 그대로 옮기되, '깨끗함'(clean_title)·합계·소유자 일치는 AI 의 말이 아니라 코드로 계산한다.
- 읽기만으로는 확인됨이 아니다(pending). 임대인이 원본과 대조했다고 확정(confirm)해야 registry_checked=True 가 된다.
- 소유자가 임대인과 다르면 확정할 수 없다(대리 계약은 관리인에게 문의).
- 확정 후 FRESH_DAYS 가 지나면 공개 매물의 '등기클린' 표시는 꺼진다(등기부는 시간이 지나면 달라질 수 있다).
- 이 결과는 참고용이며 AI 는 서류의 진위를 가리지 못한다.
"""
from __future__ import annotations

import hashlib
import os
from datetime import date, datetime
from typing import Any, Dict, List

from core.errors import DomainError

from . import registry_ai
from .models import Building, Lessor, Room, date_today
from .pool import now_iso, pool

MAX_AI_CALLS = 5          # 호실 하나당 AI 읽기 상한(남용·비용 방지)
FRESH_DAYS = 90           # 확정 후 이 기간까지만 공개 매물에 '등기클린'을 표시


def _lessor_names(room: Room) -> List[str]:
    lsr = Lessor.for_branch(room.branch)
    if lsr is None:
        return []
    return [n for n in (lsr.name, (lsr.identity or {}).get("name", "")) if n]


def _save_file(room_id: str, binary: bytes, mime: str) -> Dict[str, str]:
    from .broker import ALLOWED_MIME_TO_EXT
    ext = ALLOWED_MIME_TO_EXT[mime][0]
    abs_dir = os.path.join(pool().pool_dir, "uploads", "room_registry")
    os.makedirs(abs_dir, exist_ok=True)
    filename = f"{room_id}.{ext}"
    target = os.path.join(abs_dir, filename)
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fp:
        fp.write(binary)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, target)
    return {"file": f"uploads/room_registry/{filename}", "sha256": hashlib.sha256(binary).hexdigest()}


def read(room: Room, data_url: str, consent: bool, app_source: str = "rent3_lessor") -> Dict[str, Any]:
    """등기부 파일을 보관하고 AI 로 읽어 '확인 대기(pending)'로 저장한다. 확정은 confirm()."""
    if not consent:
        raise DomainError("AI 읽기에 동의해 주세요.", hint="올린 등기부가 AI(Gemini) 서비스로 전송되어 글자를 읽는 데 쓰입니다.")
    prev = room.registry_info or {}
    if int(prev.get("ai_calls") or 0) >= MAX_AI_CALLS:
        raise DomainError(f"이 호실의 AI 읽기 횟수({MAX_AI_CALLS}회)를 모두 썼습니다.", hint="관리인에게 문의해 주세요.")
    binary, mime = registry_ai.decode_data_url(data_url)
    bldg = Building.first(branch=room.branch)
    res = registry_ai.analyze(binary, mime, lessor_names=_lessor_names(room), deposit=int(room.deposit or 0),
                              property_value=int(getattr(bldg, "property_value", 0) or 0))
    if res["status"] != "ok":
        raise DomainError(res.get("message") or "등기부를 읽지 못했습니다.", hint="잠시 뒤 다시 시도하거나, 더 선명한 파일(인터넷등기소 PDF)을 올려 주세요.")
    ex = res["extracted"]
    if ex["document_type"] != "registry":
        raise DomainError("등기사항증명서가 아닌 문서로 보입니다.", hint="인터넷등기소에서 받은 등기사항증명서(PDF)를 올려 주세요.")
    saved = _save_file(room.id, binary, mime)
    names = {registry_ai._norm_name(n) for n in _lessor_names(room)}
    owner_matches = bool(names) and any(registry_ai._norm_name(o["name"]) in names for o in ex["owners"])
    # '깨끗함'은 코드로 판단: 유효한 근저당·압류·신탁 등이 하나도 없을 때만
    clean = ex["mortgage_count"] == 0 and ex["seizure_or_trust"] == "없음" and ex["readable"]
    room.registry_info = {
        "status": "pending", "issue_number": ex["issue_number"], "owner_name": ex["owner_name"],
        "property_address": ex["property_address"], "mortgage_amount": ex["mortgage_total"],
        "mortgage_count": ex["mortgage_count"], "seizure_or_trust": ex["seizure_or_trust"],
        "clean_title": bool(clean), "owner_matches": bool(owner_matches), "readable": bool(ex["readable"]),
        "risks": res["risks"], "file": saved["file"], "sha256": saved["sha256"],
        "ai_calls": int(prev.get("ai_calls") or 0) + 1, "read_at": now_iso(), "verified_at": "",
    }
    room.registry_checked = False
    room.save(action="room_registry_read", app_source=app_source)
    return room.registry_info


def confirm(room: Room, all_rooms: bool = False, app_source: str = "rent3_lessor") -> Dict[str, Any]:
    """임대인이 원본과 대조했다고 확정 → registry_checked=True. 소유자가 임대인과 다르면 확정 불가."""
    info = dict(room.registry_info or {})
    if info.get("status") != "pending":
        raise DomainError("확정할 등기부 읽기 결과가 없습니다.", hint="먼저 등기부를 올려 읽어 주세요.")
    if not info.get("owner_matches"):
        raise DomainError("등기부의 소유자가 임대인과 달라 확정할 수 없습니다.",
                          hint=f"등기부 소유자: {info.get('owner_name') or '읽지 못함'}. 대리 계약·공동 소유라면 관리인에게 문의해 주세요.")
    today = date_today().isoformat()
    info.update({"status": "confirmed", "verified_at": today})
    room.registry_info, room.registry_checked = info, True
    room.save(action="room_registry_confirmed", app_source=app_source)
    shared = 0
    if all_rooms:                      # 한 건물(다가구)은 등기부가 하나이므로 같은 지점의 다른 호실에도 적용
        copy = {k: v for k, v in info.items() if k not in ("risks", "ai_calls", "read_at")}
        copy["shared_from"] = room.id
        for o in Room.find(branch=room.branch):
            if o.id != room.id:
                o.registry_info, o.registry_checked = dict(copy), True
                o.save(action="room_registry_shared", app_source=app_source)
                shared += 1
    return {"verified_at": today, "shared": shared}


def is_fresh(info: Dict[str, Any]) -> bool:
    try:
        return (date.today() - datetime.strptime(str(info.get("verified_at"))[:10], "%Y-%m-%d").date()).days <= FRESH_DAYS
    except ValueError:
        return False


def public_clean(room: Room) -> bool:
    """공개 매물의 '등기클린' 표시: 임대인이 확정했고, 최근이고, 코드가 계산한 깨끗함일 때만."""
    info = room.registry_info or {}
    return bool(room.registry_checked and info.get("clean_title") and info.get("owner_matches", True) and is_fresh(info))
