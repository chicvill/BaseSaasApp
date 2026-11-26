"""
apps/_rent3_core/domains/cosign_registry.py — 공동계약 등기부 권리 검증·위험 차단 정책·특약 동기화·첨부 믹스인.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.errors import DomainError, NotFound
from ..broker import ALLOWED_MIME_TO_EXT, EXT_TO_MIME, MAX_PHOTO_BYTES
from ..models import Contract
from ..pool import now_iso, pool

if TYPE_CHECKING:
    from ..cosign import CoSignSession

# ── 등기부 위험 차단 정책 ──────────────────────────────────────────────
HIGH_DEPOSIT = 50_000_000            # 고액 보증금 기준(초과): 선순위 근저당이 있으면 보증보험 또는 말소 특약이 필요
LOW_DEPOSIT = 20_000_000             # 소액 월세 기준(이하): 근저당이 있으면 강한 위험 확인이 필요
INSURED_STATUS = ("가입 완료", "가입 예정")   # 보증보험으로 인정하는 상태. 보증보험은 계약서 체결 뒤에야 발급되므로 '가입 예정'도 인정하되 아래 거절 특약이 자동으로 필수 삽입된다
INSURANCE_CLAUSE = ("임차인이 보증금 반환보증(보증보험)에 가입하지 못하거나 가입이 거절되면 이 계약은 무효로 하고, "
                    "임대인은 받은 계약금 전액을 즉시 반환한다.")
CLEANUP_CLAUSE = ("임대인은 잔금일까지 선순위 근저당권을 전액 말소하고, 말소된 등기사항증명서를 임차인에게 제시한다. "
                  "말소되지 않으면 임차인은 잔금 지급을 거절하고 계약을 해제할 수 있으며, 임대인은 받은 계약금을 즉시 반환한다.")


class RegistryBlocked(DomainError):
    """등기부 확인 결과 계약을 더는 진행할 수 없는 경우(소유자 불일치·압류 등). 체크로 넘어갈 수 없고 계약이 중단된다."""
    status_code = 409
    level = "error"


def re_is_date(v: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", (v or "").strip()))


def summary(self: CoSignSession) -> str:
    c_tag = "호출중" if self.call_status == "CALLING" else f"Step {self.current_step}/5"
    return f"공동계약 [{self.room_no}호] {c_tag} ({self.status}, rev={self.revision})"

def compute_integrity_hash(self: CoSignSession) -> str:
    """현재 세션 데이터로 계산한 무결성 해시. 서명 시점에 저장한 값과 비교해 변조 여부를 확인한다."""
    return Contract.compute_integrity_hash({
        "session_id": self.id,
        "revision": self.revision,
        "identity_verification": {
            "lessor_login_verified": self.lessor_login_verified,
            "tenant_login_verified": self.tenant_login_verified,
            "lessor_checked_tenant_id": self.lessor_checked_tenant_id,
            "tenant_checked_lessor_id": self.tenant_checked_lessor_id,
        },
        "registry_data": self.registry_data,
        "option_checks": self.option_checks,
        "terms": self.terms,
        "lessor_signature": self.lessor_signature,
        "tenant_signature": self.tenant_signature,
    })

def is_jeonse(self: CoSignSession) -> bool:
    t = self.terms or {}
    return t.get("lease_type") == "전세" or int(t.get("monthly_rent") or 0) == 0

def _norm_name(v: Any) -> str:
    # 괄호 안 직함(예: "김임대 (시크빌 대표)")과 공백은 비교에서 제외
    return re.sub(r"\s+", "", re.sub(r"\([^)]*\)", "", str(v or "")))

def needs_insurance_refusal_clause(self: CoSignSession, terms: Optional[Dict[str, Any]] = None) -> bool:
    """전세·고액 보증금 계약에서 임차인이 보증보험을 '가입 예정'이라고 했으면 '가입 거절 시 계약 무효·계약금 전액 반환' 특약이 필수다."""
    t = terms if terms is not None else (self.terms or {})
    jeonse = t.get("lease_type") == "전세" or int(t.get("monthly_rent") or 0) == 0
    needs = jeonse or int(t.get("deposit") or 0) > HIGH_DEPOSIT
    return needs and (self.tenant_registry or {}).get("guarantee_status") == "가입 예정"

def _sync_cleanup_clause(self: CoSignSession, terms: Dict[str, Any]) -> Dict[str, Any]:
    """자동 특약을 특약사항 본문과 맞춘다.
    ① '잔금 시 근저당 말소 특약': 임대인이 체크하면 표준 문구가 들어가고 풀면 빠진다.
    ② '보증보험 가입 거절 시 계약 무효·계약금 반환': 임차인이 '가입 예정'이면 **자동 필수 삽입** — 사용자가 본문에서 지워도 다시 들어간다."""
    out = dict(terms)
    text = str(out.get("special_terms") or "")
    for clause, wanted in ((CLEANUP_CLAUSE, bool(out.get("mortgage_cleanup_clause"))),
                           (INSURANCE_CLAUSE, self.needs_insurance_refusal_clause(out))):
        has = clause in text
        if wanted and not has:
            n = len([ln for ln in text.splitlines() if ln.strip()]) + 1
            text = (text.rstrip() + "\n" if text.strip() else "") + f"{n}. " + clause
        elif not wanted and has:
            text = text.replace(clause, "")
            text = "\n".join(ln for ln in text.splitlines() if ln.strip() not in {f"{i}." for i in range(1, 30)}).rstrip()
    out["special_terms"] = text
    return out

def deposit_amount(self: CoSignSession) -> int:
    return int((self.terms or {}).get("deposit") or 0)

def needs_guarantee_info(self: CoSignSession) -> bool:
    """전세이거나 보증금이 고액(5천만 원 초과)이면 보증보험 가입 여부를 확인해야 한다."""
    return self.is_jeonse() or self.deposit_amount() > HIGH_DEPOSIT

def is_low_deposit_monthly(self: CoSignSession) -> bool:
    return (not self.is_jeonse()) and self.deposit_amount() <= LOW_DEPOSIT

def mortgage_exposure(self: CoSignSession, tenant_rec: Optional[Dict[str, Any]] = None) -> int:
    """임대인이 제시한 값과 임차인이 직접 본 값(저장된 값 또는 방금 제출한 값) 중 큰 근저당 채권최고액."""
    mine = tenant_rec if tenant_rec is not None else (self.tenant_registry or {})
    return max(int((self.registry_data or {}).get("mortgage_amount") or 0), int(mine.get("mortgage_amount") or 0))

def hard_block_reasons(self: CoSignSession, rec: Dict[str, Any]) -> List[str]:
    """확인 체크로도 넘어갈 수 없는 절대 차단 사유: 소유자 불일치, 압류·가압류·신탁·경매개시·임차권등기."""
    out: List[str] = []
    lr = self.registry_data or {}
    if lr.get("owner_name") and self._norm_name(lr["owner_name"]) != self._norm_name(rec.get("owner_name")):
        out.append(f"등기부 소유자({rec.get('owner_name')})가 임대인이 제시한 소유자({lr['owner_name']})와 다릅니다")
    if rec.get("seizure_or_trust") == "있음":
        out.append("압류·가압류·가처분·신탁·경매개시결정·임차권등기가 등기부에 있습니다")
    return out

def risk_blockers(self: CoSignSession) -> List[str]:
    """진행 차단 사유(조건을 갖추면 풀린다): 선순위 근저당이 있는 전세·고액 보증금에 보증보험도 말소 특약도 없는 경우."""
    out: List[str] = []
    if not (self.tenant_registry or {}).get("submitted"):
        return out
    if self.needs_guarantee_info() and self.mortgage_exposure() > 0:
        status = (self.tenant_registry or {}).get("guarantee_status")
        insured = status in INSURED_STATUS and (status != "가입 예정" or INSURANCE_CLAUSE in str((self.terms or {}).get("special_terms") or ""))
        clause = bool((self.terms or {}).get("mortgage_cleanup_clause"))
        if not (insured or clause):
            out.append("선순위 근저당이 있는 전세·고액 보증금(5천만 원 초과) 계약은 보증보험 '가입 완료·가입 예정(거절 시 계약 무효 특약 포함)' 또는 '잔금 시 근저당 말소 특약'이 있어야 진행할 수 있습니다")
    return out

def _ensure_risk_ok(self: CoSignSession) -> None:
    blockers = self.risk_blockers()
    if blockers:
        raise DomainError(blockers[0], hint="임대인이 계약 조건에서 '잔금 시 근저당 말소 특약'을 넣거나, 임차인이 보증보험 가입을 완료한 뒤 등기부 확인 값을 다시 제출해 주세요.")

def registry_mismatches(self: CoSignSession) -> List[str]:
    """임대인이 제시한 등기 정보와 임차인이 직접 열람한 결과의 불일치·위험 항목."""
    tr = self.tenant_registry or {}
    if not tr.get("submitted"):
        return []
    lr = self.registry_data or {}
    out: List[str] = []

    norm = self._norm_name

    if lr.get("owner_name") and norm(lr.get("owner_name")) != norm(tr.get("owner_name")):
        out.append(f"소유자가 다릅니다 (임대인 제시: {lr.get('owner_name')} / 직접 열람: {tr.get('owner_name')})")
    if int(lr.get("mortgage_amount") or 0) != int(tr.get("mortgage_amount") or 0):
        out.append(
            f"근저당 채권최고액이 다릅니다 (임대인 제시: {int(lr.get('mortgage_amount') or 0):,}원 / "
            f"직접 열람: {int(tr.get('mortgage_amount') or 0):,}원)"
        )
    if lr.get("issue_number") and tr.get("issue_number") and norm(lr["issue_number"]) != norm(tr["issue_number"]):
        out.append("발급확인번호가 다릅니다")
    if tr.get("seizure_or_trust") == "있음":
        out.append("압류·가압류·가처분·신탁등기가 있다고 확인되었습니다")
    return out

def submit_tenant_registry(
    self: CoSignSession,
    data: Dict[str, Any],
    attachment_data_url: str = "",
    attachment_name: str = "",
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """임차인이 인터넷등기소에서 직접 열람한 결과를 기록한다 (필수 항목·불일치 확인 검증)."""
    self._ensure_ready_for_steps()
    if not self.step1_lessor_confirmed:
        raise DomainError("임대인이 등기부 정보를 먼저 제시해야 대조할 수 있습니다.")
    if not data.get("viewed_directly"):
        raise DomainError(
            "인터넷등기소에서 직접 열람했다는 확인이 필요합니다.",
            hint="임대인이 준 사진·PDF가 아니라, 본인이 인터넷등기소(iros.go.kr)에서 직접 열람한 결과를 입력해 주세요.",
        )
    if not str(data.get("viewed_at", "")).strip():
        raise DomainError("열람 일시를 입력해 주세요.")
    # 열람 일시는 최근(입주 전 재확인과 같은 기준)이어야 하고, AI 로 읽은 파일이 있으면 그 파일의 열람 일시와 맞아야 한다(오래된 서류·다른 서류 방지)
    from .. import balancecheck as _bc
    if not _bc.is_recent(str(data["viewed_at"])):
        raise DomainError("열람 일시가 오래되었거나 형식이 올바르지 않습니다.",
                          hint=f"인터넷등기소에서 {_bc.RECENT_DAYS}일 이내에 열람한 등기부만 인정합니다. 오늘 다시 열람해 주세요.")
    ai_view = ((self.registry_ai_result or {}).get("extracted") or {}).get("viewed_at", "")
    if ai_view:
        try:
            gap = abs((datetime.fromisoformat(str(data["viewed_at"]).strip()[:16]).date() - datetime.fromisoformat(ai_view[:16]).date()).days)
        except ValueError:
            gap = 0
        if gap > 1:
            raise DomainError("입력한 열람 일시가 올린 등기부 파일의 열람 일시와 다릅니다.",
                              hint=f"파일에서 읽힌 열람 일시: {ai_view.replace('T', ' ')}. 방금 열람한 파일을 올리거나 열람 일시를 바르게 고쳐 주세요.")
    if not str(data.get("owner_name", "")).strip():
        raise DomainError("직접 열람한 등기부의 갑구 소유자 성명을 입력해 주세요.")
    try:
        raw_m = data.get("mortgage_amount")
        if raw_m is None:
            raise ValueError
        mortgage = int(raw_m)
        if mortgage < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise DomainError("을구 근저당 채권최고액을 숫자로 입력해 주세요 (없으면 0).")
    if data.get("seizure_or_trust") not in ("없음", "있음"):
        raise DomainError("압류·가압류·가처분·신탁등기 여부를 선택해 주세요.")
    if self.needs_guarantee_info() and data.get("guarantee_status") not in ("가입 완료", "가입 예정", "가입 안 함"):
        raise DomainError(
            "전세 또는 고액 보증금(5천만 원 초과) 계약은 보증금 반환보증 가입 여부를 선택해야 합니다.",
            hint="HUG·SGI 등 보증 가입 가능 여부를 계약 전에 확인해 주세요.",
        )

    rec: Dict[str, Any] = {
        "submitted": True,
        "viewed_directly": True,
        "viewed_at": str(data["viewed_at"]).strip(),
        "issue_number": str(data.get("issue_number", "")).strip(),
        "issue_number_verified": bool(data.get("issue_number_verified")),
        "owner_name": str(data["owner_name"]).strip(),
        "mortgage_amount": mortgage,
        "seizure_or_trust": data["seizure_or_trust"],
        "guarantee_status": str(data.get("guarantee_status", "")).strip(),
        "mismatch_ack": bool(data.get("mismatch_ack")),
        "low_deposit_risk_ack": bool(data.get("low_deposit_risk_ack")),
        "ai_assisted": bool(self.registry_ai_result),
        "submitted_at": now_iso(),
    }
    hard = self.hard_block_reasons(rec)
    if hard:
        # 절대 차단: 확인 체크로 넘어갈 수 없다. 근거(임차인이 본 값)를 남기고 계약을 즉시 중단한다.
        self.tenant_registry = {**rec, "blocked": hard}
        self.status = "CANCELLED"
        self.cancel_reason = "등기부 확인 결과 계약을 진행할 수 없어 중단되었습니다: " + " / ".join(hard)
        self.cancelled_by, self.cancelled_at = "system", now_iso()
        self.save(action="cosign_blocked_by_registry_policy", app_source=app_source)
        raise RegistryBlocked(
            "등기부 확인 결과 계약을 더는 진행할 수 없어 중단했습니다: " + " / ".join(hard),
            hint="임차인 보호를 위해 이 항목은 확인 체크로 넘어갈 수 없습니다. 문제가 해소된 뒤 임대인이 새로 계약을 호출해 주세요.",
        )
    if self.is_low_deposit_monthly() and self.mortgage_exposure(rec) > 0 and not rec["low_deposit_risk_ack"]:
        self.tenant_registry = {}
        raise DomainError(
            "⚠️ 위험: 소액 보증금에 선순위 근저당이 있습니다. 경매가 되면 보증금을 돌려받지 못할 수 있습니다.",
            hint="위험을 이해했다면 '소액 보증금 위험을 확인하고 계속' 항목에 체크해 다시 제출해 주세요.",
        )
    self.tenant_registry = rec
    synced = self._sync_cleanup_clause(self.terms or {})   # 보증보험 '가입 예정'이면 거절 특약을 자동으로 넣는다
    if synced != self.terms:
        self.terms = synced
    mism = self.registry_mismatches()
    if mism and not rec["mismatch_ack"]:
        self.tenant_registry = {}
        raise DomainError(
            "임대인이 제시한 내용과 직접 열람한 내용이 다르거나 위험 항목이 있습니다.",
            hint=" / ".join(mism) + " — 내용을 확인했다면 '차이를 확인하고 계속' 항목에 체크해 주세요.",
        )

    if attachment_data_url:
        self.registry_attachment_file, self.registry_attachment_sha256 = self._save_registry_attachment(
            self.id, attachment_data_url
        )
        self.registry_attachment_name = (attachment_name or "").strip()[:120]
    return self.save(action="tenant_registry_submitted", app_source=app_source)

def _save_registry_attachment(session_id: str, data_url: str):
    m = re.match(r"^data:([a-zA-Z0-9.+/-]+);base64,(.+)$", data_url.strip(), re.DOTALL)
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
    abs_dir = os.path.join(pool().pool_dir, "uploads", "registry")
    os.makedirs(abs_dir, exist_ok=True)
    filename = f"{session_id}.{ext}"
    target = os.path.join(abs_dir, filename)
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fp:
        fp.write(binary)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, target)
    return f"uploads/registry/{filename}", hashlib.sha256(binary).hexdigest()

def registry_attachment_path(self: CoSignSession):
    if not self.registry_attachment_file:
        raise NotFound("첨부된 등기부 열람 자료가 없습니다.")
    root = os.path.abspath(pool().pool_dir)
    cand = os.path.abspath(os.path.join(root, self.registry_attachment_file))
    if not cand.startswith(root + os.sep) or not os.path.isfile(cand):
        raise NotFound("첨부 파일을 찾을 수 없습니다.")
    mime = EXT_TO_MIME.get(os.path.splitext(cand.lower())[1], "application/octet-stream")
    return cand, mime, self.registry_attachment_name or os.path.basename(cand)

def cancel(self: CoSignSession, role: str, reason: str, app_source: str = "rent3_contract") -> CoSignSession:
    """체결 전에 당사자가 계약을 취소한다. 사유는 필수이며 상대방 화면에 그대로 보인다. 호실은 즉시 다시 열린다."""
    self._ensure_in_progress()
    why = (reason or "").strip()
    if len(why) < 2:
        raise DomainError("취소 사유를 입력해 주세요.", hint="상대방 화면에 그대로 표시됩니다.")
    who = "임대인" if role == "lessor" else "임차인"
    self.status = "CANCELLED"
    self.cancel_reason = f"{who}이 계약을 취소했습니다: {why[:200]}"
    self.cancelled_by, self.cancelled_at = ("lessor" if role == "lessor" else "tenant"), now_iso()
    return self.save(action=f"cosign_cancelled_by_{self.cancelled_by}", app_source=app_source)

def recheck_registry(self: CoSignSession, changed: bool, app_source: str = "rent3_contract") -> CoSignSession:
    """체결 직전 임차인이 등기부를 다시 열람한 결과. 변동이 있으면 계약을 중단한다."""
    self._ensure_in_progress()
    if self.current_step < 4:
        raise DomainError("서명 단계 이후에 체결 직전 재열람을 확인할 수 있습니다.")
    if changed:
        self.status = "CANCELLED"
        self.cancel_reason = "임차인이 체결 직전 등기부에서 변동(근저당·압류 등)을 확인해 계약이 중단되었습니다."
        self.cancelled_by, self.cancelled_at = "tenant", now_iso()
        self.tenant_registry = {**self.tenant_registry, "recheck_changed": True}
        return self.save(action="cosign_cancelled_registry_changed", app_source=app_source)
    self.tenant_recheck_at = now_iso()
    return self.save(action="tenant_registry_rechecked", app_source=app_source)

def is_step1_fully_verified(self: CoSignSession) -> bool:
    return (
        self.lessor_checked_tenant_id
        and self.tenant_checked_lessor_id
        and self.step1_lessor_confirmed
        and self.step1_tenant_locked
    )

def ensure_renewal_ready(self: CoSignSession) -> CoSignSession:
    """재계약 세션은 본인 확인·옵션 점검을 생략하고 바로 3단계(조건 확인·승인)부터 시작한다."""
    if self.mode != "RENEWAL" or self.status != "IN_PROGRESS":
        return self
    if (
        self.lessor_checked_tenant_id
        and self.tenant_checked_lessor_id
        and self.step1_lessor_confirmed
        and self.step1_tenant_locked
        and self.step2_lessor_confirmed
        and self.step2_tenant_locked
        and self.current_step >= 3
    ):
        return self
    self.lessor_checked_tenant_id = True
    self.tenant_checked_lessor_id = True
    self.step1_lessor_confirmed = True
    self.step1_tenant_locked = True
    self.step2_lessor_confirmed = True
    self.step2_tenant_locked = True
    if not self.tenant_recheck_at:
        self.tenant_recheck_at = now_iso()
    if self.current_step < 3:
        self.current_step = 3
    return self.save(action="renewal_auto_skipped_id_check", app_source="rent3_contract")
