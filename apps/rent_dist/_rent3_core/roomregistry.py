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
from typing import Any, Dict, List, Optional, Tuple

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


_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def doc_path(room: Room, *, allow_pending: bool = False) -> Optional[Tuple[str, str, str]]:
    """임대인이 올린 등기부등본 파일의 (절대경로, MIME, 다운로드 파일명)을 반환한다. 없거나 안전하지 않으면 None."""
    info = room.registry_info or {}
    if not allow_pending and not room.registry_checked:
        return None
    rel = str(info.get("file") or "").strip().replace("\\", "/")
    if not rel or not rel.startswith("uploads/room_registry/"):
        return None
    base_dir = os.path.realpath(os.path.join(pool().pool_dir, "uploads", "room_registry"))
    target = os.path.realpath(os.path.join(pool().pool_dir, rel))
    if not (target == base_dir or target.startswith(base_dir + os.sep)) or not os.path.isfile(target):
        return None
    ext = os.path.splitext(target)[1].lower()
    mime = _EXT_TO_MIME.get(ext, "application/octet-stream")
    fname = f"registry_{room.room_no}{ext or '.pdf'}"
    return target, mime, fname


def public_summary(room: Room) -> Optional[Dict[str, Any]]:
    """공개 매물 카드·상세의 [등기부 확인] 버튼에 제시할 안전한 임대인 확인본 요약(민감 경로/해시 제외)."""
    info = room.registry_info or {}
    if not room.registry_checked or not info:
        return None
    doc = doc_path(room)
    return {
        "verified_at": str(info.get("verified_at") or ""),
        "owner_name": str(info.get("owner_name") or ""),
        "mortgage_amount": int(info.get("mortgage_amount") or 0),
        "mortgage_count": int(info.get("mortgage_count") or 0),
        "seizure_or_trust": str(info.get("seizure_or_trust") or "없음"),
        "clean_title": public_clean(room),
        "fresh": is_fresh(info),
        "source": str(info.get("source") or "lessor"),
        "has_file": doc is not None,
        "doc_mime": doc[1] if doc else "",
        "doc_url": f"/api/rent3/pool/listings/{room.id}/registry-doc" if doc else "",
    }


def overwrite_from_balance(room: Room, ct: Any, br_read: Dict[str, Any], *, app_source: str = "rent3_contract") -> bool:
    """잔금 처리 시 임차인이 직접 열람·등재한 최신 등기부등본으로 호실 등기부를 덮어씌워 최신화한다."""
    rd = br_read or {}
    prev = dict(room.registry_info or {})
    saved_file = str(prev.get("file") or "")
    saved_sha = str(prev.get("sha256") or "")

    # 1) 잔금 시 서버 보관 파일이 있으면 호실 등기부 폴더로 원자적 복사(덮어쓰기)
    rel_src = str(rd.get("file") or "").strip().replace("\\", "/")
    if rel_src.startswith("uploads/balance_registry/"):
        bal_base = os.path.realpath(os.path.join(pool().pool_dir, "uploads", "balance_registry"))
        src_abs = os.path.realpath(os.path.join(pool().pool_dir, rel_src))
        if (src_abs == bal_base or src_abs.startswith(bal_base + os.sep)) and os.path.isfile(src_abs):
            ext = os.path.splitext(src_abs)[1].lower()
            mime = _EXT_TO_MIME.get(ext, "")
            if mime:
                with open(src_abs, "rb") as fp:
                    binary = fp.read()
                saved = _save_file(room.id, binary, mime)
                saved_file = saved["file"]
                saved_sha = saved["sha256"]

    if rd.get("status") != "ok" and not saved_file:
        return False

    today = date_today().isoformat()
    if rd.get("status") == "ok":
        owner_name = str(rd.get("owner_name") or prev.get("owner_name") or "")
        mort_amt = int(rd.get("mortgage_total") or 0)
        mort_cnt = int(rd.get("mortgage_count") if rd.get("mortgage_count") is not None else (1 if mort_amt > 0 else 0))
        seize = str(rd.get("seizure_or_trust") or "없음")
        issue_no = str(rd.get("issue_number") or prev.get("issue_number") or "")
        names = {registry_ai._norm_name(n) for n in _lessor_names(room)}
        owner_matches = (not names) or (registry_ai._norm_name(owner_name) in names) or bool(prev.get("owner_matches", True))
        clean = mort_amt == 0 and seize == "없음"
        info = {
            **prev,
            "status": "confirmed",
            "source": "tenant_balance",
            "updated_from_contract_id": str(getattr(ct, "id", "") or ""),
            "issue_number": issue_no,
            "owner_name": owner_name,
            "mortgage_amount": mort_amt,
            "mortgage_count": mort_cnt,
            "seizure_or_trust": seize,
            "clean_title": bool(clean),
            "owner_matches": bool(owner_matches),
            "readable": True,
            "viewed_at": str(rd.get("viewed_at") or ""),
            "verified_at": today,
            "read_at": now_iso(),
            "file": saved_file,
            "sha256": saved_sha or str(rd.get("sha256") or ""),
        }
    else:
        info = {
            **prev,
            "status": "confirmed",
            "source": "tenant_balance",
            "updated_from_contract_id": str(getattr(ct, "id", "") or ""),
            "verified_at": today,
            "read_at": now_iso(),
            "file": saved_file,
            "sha256": saved_sha or str(rd.get("sha256") or ""),
        }
    room.registry_info = info
    room.registry_checked = True
    room.save(action="room_registry_overwritten_from_balance", app_source=app_source)
    return True
