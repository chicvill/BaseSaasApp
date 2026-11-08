"""
apps/rent_dist/_rent3_core/franchise.py — 가맹 신청: 휴대폰 본인인증 + 등기부 소유자 대조.

흐름
  1) 임대인 본인인증(휴대폰) → 실명·생년월일 확보
  2) 건물 기본정보(실제 주소) 입력
  3) 등기부 업로드 → (Gemini 로 읽기) → 본인인증 값과 코드로 대조
  4) 신청 제출 → 관리자 심사 (자동 대조는 '심사 우선' 표시일 뿐, 최종 승인은 관리자)

본인인증 업체:
  - `fake`: 개발·시연용. 인증번호 000000. 서버 자신(loopback) 또는 RENT3_FAKE_IDENTITY=1 에서만 허용.
  - 실제 업체(PASS/NICE/카카오 등)는 `IdentityProvider` 를 구현해 PROVIDERS 에 등록하고
    환경변수 RENT3_IDENTITY_PROVIDER 로 고른다. 지금은 연동 전이라 'none' 이면 인증이 막힌다.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.errors import DomainError

from .broker import ALLOWED_MIME_TO_EXT, MAX_PHOTO_BYTES
from .pool import pool

FAKE_CODE = "000000"
CORP_RE = re.compile(r"(주식회사|\(주\)|㈜|유한회사|합자회사|법인|재단|사단|조합|협회|공사|공단)")


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def normalize_birth(raw: str) -> str:
    """생년월일 → 앞 6자리(YYMMDD). 8자리(YYYYMMDD)도 받는다."""
    d = _digits(raw)
    if len(d) == 8:
        return d[2:]
    if len(d) == 6:
        return d
    raise DomainError("생년월일을 6자리(예: 650312) 또는 8자리(예: 19650312)로 입력해 주세요.")


# ── 본인인증 업체 ──
class IdentityProvider:
    name = "none"

    def start(self, name: str, phone: str, birth6: str) -> Dict[str, Any]:
        raise DomainError("본인인증 업체가 아직 연동되지 않았습니다.", hint="본사에 문의해 주세요.")

    def confirm(self, name: str, phone: str, birth6: str, code: str) -> Dict[str, Any]:
        raise DomainError("본인인증 업체가 아직 연동되지 않았습니다.")


class FakeProvider(IdentityProvider):
    """개발·시연용 가짜 인증. 실제 본인 확인이 아니다."""
    name = "fake"

    def start(self, name: str, phone: str, birth6: str) -> Dict[str, Any]:
        return {"sent": True, "fake": True, "hint": f"시연용 인증번호는 {FAKE_CODE} 입니다."}

    def confirm(self, name: str, phone: str, birth6: str, code: str) -> Dict[str, Any]:
        if code.strip() != FAKE_CODE:
            raise DomainError("인증번호가 올바르지 않습니다.", hint=f"시연용 인증번호는 {FAKE_CODE} 입니다.")
        return {"name": name, "phone": phone, "birth6": birth6, "provider": "fake", "fake": True}


PROVIDERS: Dict[str, IdentityProvider] = {"none": IdentityProvider(), "fake": FakeProvider()}


def fake_allowed(is_loopback: bool) -> bool:
    env = os.getenv("RENT3_FAKE_IDENTITY", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    return is_loopback


def provider(is_loopback: bool) -> IdentityProvider:
    chosen = os.getenv("RENT3_IDENTITY_PROVIDER", "").strip().lower()
    if chosen and chosen in PROVIDERS:
        p = PROVIDERS[chosen]
    else:
        p = PROVIDERS["fake"] if fake_allowed(is_loopback) else PROVIDERS["none"]
    if p.name == "fake" and not fake_allowed(is_loopback):
        return PROVIDERS["none"]  # 배포 환경에서 가짜 인증이 켜지는 일을 막는다
    return p


def verify_identity_record(res: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "verified": True,
        "name": res["name"],
        "phone": res["phone"],
        "birth6": res["birth6"],
        "provider": res["provider"],
        "fake": bool(res.get("fake")),
        "verified_at": datetime.now().isoformat(timespec="seconds"),
    }


def mask_identity(idn: Dict[str, Any]) -> Dict[str, Any]:
    """임대인 화면에 돌려줄 값. 생년월일은 가린다."""
    if not idn or not idn.get("verified"):
        return {"verified": False}
    n = idn.get("name", "")
    return {
        "verified": True,
        "name": n,
        "phone": idn.get("phone", ""),
        "birth6": (idn.get("birth6", "")[:2] + "****") if idn.get("birth6") else "",
        "provider": idn.get("provider", ""),
        "fake": bool(idn.get("fake")),
        "verified_at": idn.get("verified_at", ""),
    }


# ── 등기부 ↔ 본인 대조 ──
def _norm_name(v: Any) -> str:
    return re.sub(r"[\s]+", "", re.sub(r"\([^)]*\)", "", str(v or "")))


def _address_tokens(addr: str) -> List[str]:
    return re.findall(r"\d+(?:-\d+)*", addr or "")


def address_matches(building_address: str, registry_address: str) -> Optional[bool]:
    """건물 주소의 번지(숫자) 표기가 등기부 소재지에 모두 있는가. 번지가 없으면 판단 불가(None)."""
    want = _address_tokens(building_address)
    if not want:
        return None
    have = set(_address_tokens(registry_address))
    return all(t in have for t in want)


def check_ownership(identity: Dict[str, Any], extracted: Optional[Dict[str, Any]], building_address: str) -> Dict[str, Any]:
    """본인인증 값(실명·생년월일)과 등기부 소유자·소재지를 코드로 대조한다. 자동 통과는 '심사 우선'일 뿐 승인이 아니다."""
    def out(code: str, msg: str, level: str, **kw: Any) -> Dict[str, Any]:
        return {"code": code, "message": msg, "level": level, "auto_pass": code == "AUTO_MATCH", **kw}

    if not identity or not identity.get("verified"):
        return out("NO_IDENTITY", "본인인증을 먼저 해 주세요.", "high")
    if not extracted:
        return out("NO_REGISTRY", "등기부를 읽지 못했습니다. 관리자가 원본을 직접 확인합니다.", "caution")
    if extracted.get("document_type") != "registry" or not extracted.get("readable", True):
        return out("NOT_REGISTRY", "등기사항증명서로 읽히지 않았습니다. 관리자가 원본을 직접 확인합니다.", "high")

    me = _norm_name(identity.get("name"))
    birth6 = identity.get("birth6", "")
    owners = extracted.get("owners") or []
    addr_ok = address_matches(building_address, extracted.get("property_address", ""))

    matched = [o for o in owners if _norm_name(o.get("name")) == me]
    person_owner_present = any(not CORP_RE.search(str(o.get("name") or "")) for o in owners)
    if not matched:
        if owners and not person_owner_present:
            return out("CORPORATE", "소유자가 법인입니다. 사업자등록증과 대표자 확인이 필요합니다.", "caution", address_ok=addr_ok)
        return out("NOT_OWNER", "본인 이름이 등기부 소유자에 없습니다. 위임장 등 대리 권한 확인이 필요합니다.", "high", address_ok=addr_ok)

    m = matched[0]
    owner_birth = _digits(m.get("id_front"))[:6]
    birth_state = "unknown" if len(owner_birth) < 6 else ("ok" if owner_birth == birth6 else "mismatch")
    shared = len(owners) > 1
    info = {"address_ok": addr_ok, "birth_check": birth_state, "owner_count": len(owners), "share": m.get("share", "")}

    if birth_state == "mismatch":
        return out("BIRTH_MISMATCH", "이름은 같지만 생년월일이 등기부와 다릅니다(동명이인 가능). 관리자가 확인합니다.", "high", **info)
    if shared:
        return out("SHARED_OWNER", "공동 소유입니다. 다른 공유자의 위임·동의 확인이 필요합니다.", "caution", **info)
    if addr_ok is False:
        return out("ADDRESS_MISMATCH", "등기부 소재지가 입력한 건물 주소와 다릅니다.", "high", **info)
    if addr_ok is None:
        return out("ADDRESS_UNKNOWN", "건물 주소에 번지가 없어 등기부와 대조하지 못했습니다. 실제 주소를 입력해 주세요.", "caution", **info)
    if birth_state == "unknown":
        return out("NAME_ONLY", "이름·주소는 맞지만 생년월일을 확인하지 못했습니다. 관리자가 확인합니다.", "caution", **info)
    return out("AUTO_MATCH", "본인(이름·생년월일)이 단독 소유자이고 소재지가 일치합니다. 관리자 심사 우선 대상입니다.", "ok", **info)


# ── 서류 파일 ──
def save_registry_file(lessor_id: str, data_url: str) -> Dict[str, str]:
    m = re.match(r"^data:([a-zA-Z0-9.+/-]+);base64,(.+)$", (data_url or "").strip(), re.DOTALL)
    if not m or m.group(1).lower() not in ALLOWED_MIME_TO_EXT:
        raise DomainError("첨부 형식이 올바르지 않습니다.", hint="허용 형식: JPG, PNG, WEBP, GIF, PDF")
    try:
        binary = base64.b64decode(m.group(2), validate=False)
    except Exception:
        raise DomainError("첨부 파일을 읽지 못했습니다. 다시 선택해 주세요.")
    if not binary:
        raise DomainError("비어 있는 첨부 파일입니다.")
    if len(binary) > MAX_PHOTO_BYTES:
        raise DomainError("첨부 파일은 최대 10MB 이하만 허용됩니다.")
    ext = ALLOWED_MIME_TO_EXT[m.group(1).lower()][0]
    abs_dir = os.path.join(pool().pool_dir, "uploads", "franchise")
    os.makedirs(abs_dir, exist_ok=True)
    filename = f"{lessor_id}.{ext}"
    target = os.path.join(abs_dir, filename)
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fp:
        fp.write(binary)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, target)
    return {"file": f"uploads/franchise/{filename}", "sha256": hashlib.sha256(binary).hexdigest(), "mime": m.group(1).lower(), "binary_len": str(len(binary))}


def registry_file_path(lessor: Any):
    from .broker import EXT_TO_MIME
    rel = (lessor.franchise_docs or {}).get("registry_file", "")
    if not rel:
        raise DomainError("제출된 등기부가 없습니다.")
    root = os.path.abspath(os.path.join(pool().pool_dir, "uploads", "franchise"))
    cand = os.path.abspath(os.path.join(pool().pool_dir, rel))
    if not cand.startswith(root + os.sep) or not os.path.isfile(cand):
        raise DomainError("제출된 등기부 파일을 찾을 수 없습니다.")
    return cand, EXT_TO_MIME.get(os.path.splitext(cand.lower())[1], "application/octet-stream")


def application_summary(lessor: Any, building: Any) -> Dict[str, Any]:
    """임대인·관리자 화면에 보여 줄 가맹 신청 진행 상태 (개인정보는 가린다)."""
    docs = lessor.franchise_docs or {}
    idn = lessor.identity or {}
    addr = getattr(building, "address", "") if building else ""
    has_real_address = bool(_address_tokens(addr))
    steps = {
        "identity": bool(idn.get("verified")),
        "building": has_real_address,
        "registry": bool(docs.get("registry_file")),
        "submitted": bool(lessor.applied_at),
    }
    return {
        "steps": steps,
        "identity": mask_identity(idn),
        "building": {
            "building_name": getattr(building, "building_name", "") if building else "",
            "address": addr,
            "property_value": getattr(building, "property_value", 0) if building else 0,
            "address_has_lot_number": has_real_address,
        },
        "registry": {
            "uploaded": steps["registry"],
            "sha256": docs.get("registry_sha256", ""),
            "ai_status": (docs.get("ai") or {}).get("status", ""),
            "issue_number": ((docs.get("ai") or {}).get("extracted") or {}).get("issue_number") or "",
            "verification": docs.get("admin_verification") or None,
            "check": docs.get("check") or None,
            "uploaded_at": docs.get("uploaded_at", ""),
        },
        "applied_at": lessor.applied_at,
        "ready_to_submit": steps["identity"] and steps["building"] and steps["registry"],
    }
