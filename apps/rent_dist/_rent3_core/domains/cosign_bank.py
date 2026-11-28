"""
apps/_rent3_core/domains/cosign_bank.py — 스마트 계약 계좌번호 등록(임차료 수납·보증금 환불) 및 상호 1원 송수금 확인(난수 4자리 코드) 믹스인.

보안 원칙:
 - 실명 확인은 '송금하는 쪽' 은행 앱의 금융결제원 예금주명 조회로 확인한다.
 - 계좌 실점유 확인은 '받는 쪽'이 본인 통장 입금 내역에 찍힌 난수 4자리 코드를 입력해 증명한다.
 - 난수 4자리 코드는 API 응답 직전 `sanitize_session_dict()`에서 수신자(계좌 주인) 화면에는 절대 내려보내지 않는다.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
from typing import TYPE_CHECKING, Any, Dict, Tuple

from core.errors import DomainError
from ..models import Lessor, Tenant
from ..pool import now_iso

if TYPE_CHECKING:
    from ..cosign import CoSignSession

MAX_ONE_WON_TRIES = 5


def gen_code4() -> str:
    """1000~9999 범위의 암호학적 난수 4자리 코드를 생성한다."""
    return f"{secrets.randbelow(9000) + 1000}"


def split_account(raw: str) -> Tuple[str, str]:
    """'국민은행 123-456-789012' 형식 문자열을 (은행명, 계좌번호)로 분리한다."""
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text:
        return "", ""
    parts = text.split(" ", 1)
    if len(parts) == 2 and re.search(r"\d", parts[1]):
        return parts[0].strip(), parts[1].strip()
    m = re.match(r"^([^\d]+?)\s*([\d\- ]{6,})$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", text


def format_account(bank_name: str, account_no: str) -> Tuple[str, str, str]:
    """은행명과 계좌번호를 검증·정규화하여 (은행명, 계좌번호, 표시문자열)을 돌려준다."""
    bank = re.sub(r"\s+", " ", (bank_name or "").strip())[:30]
    acc = re.sub(r"\s+", "", (account_no or "").strip())[:32]
    if len(bank) < 2:
        raise DomainError("은행명을 정확히 입력해 주세요.", hint="예: 국민은행, 신한은행, 카카오뱅크, 토스뱅크")
    if not re.match(r"^[\d\-]+$", acc):
        raise DomainError("계좌번호는 숫자와 하이픈(-)만 입력할 수 있습니다.")
    digits = re.sub(r"\D", "", acc)
    if len(digits) < 8 or len(digits) > 20:
        raise DomainError("계좌번호 자리수를 확인해 주세요.", hint="보통 10~14자리 숫자입니다.")
    return bank, acc, f"{bank} {acc}"


def mask_account_no(acc_no: str) -> str:
    """공개 스냅샷·감사 기록용 계좌번호 마스킹(끝 4자리만 노출)."""
    digits = re.sub(r"\D", "", str(acc_no or ""))
    if len(digits) < 4:
        return ""
    return f"****-{digits[-4:]}"


def _ensure_bank_dict(self: CoSignSession) -> Dict[str, Any]:
    bv = dict(self.bank_verify or {})
    if self.lessor_account and not bv.get("lessor_account_no"):
        b, n = split_account(self.lessor_account)
        if b:
            bv["lessor_bank"] = b
        if n:
            bv["lessor_account_no"] = n
    if self.tenant_refund_account and not bv.get("tenant_account_no"):
        b, n = split_account(self.tenant_refund_account)
        if b:
            bv["tenant_bank"] = b
        if n:
            bv["tenant_account_no"] = n
    return bv


def is_bank_verify_done(self: CoSignSession) -> bool:
    """상호 1원 송수금 확인이 완료되었거나(양측 코드 일치) 생략 합의·연장 계약 상태인지 반환한다."""
    if self.mode == "RENEWAL" or self.bank_verify_mode in ("NOT_REQUIRED", "SKIPPED_MUTUAL"):
        return True
    bv = self.bank_verify or {}
    return bool(bv.get("to_lessor_verified") and bv.get("to_tenant_verified"))


def is_bank_verify_enforced(self: CoSignSession) -> bool:
    """이 세션의 전자서명(Step 4)에서 상호 1원 확인을 필수 조건으로 검사할지 판정한다."""
    if self.mode == "RENEWAL" or self.bank_verify_mode == "NOT_REQUIRED":
        return False
    bv = self.bank_verify or {}
    if bv.get("initiated"):
        return True
    return os.getenv("RENT3_BANK_1WON_REQUIRED", "").strip().lower() in ("1", "true", "yes")


def bank_verify_public_summary(self: CoSignSession) -> Dict[str, Any]:
    """계약서 스냅샷 및 무결성 해시에 봉인할 계좌·1원 확인 공개 요약 (난수 코드는 절대 포함하지 않는다)."""
    bv = _ensure_bank_dict(self)
    return {
        "mode": self.bank_verify_mode,
        "initiated": bool(bv.get("initiated")),
        "lessor_bank": bv.get("lessor_bank", ""),
        "lessor_account_last4": mask_account_no(bv.get("lessor_account_no", "")),
        "tenant_bank": bv.get("tenant_bank", ""),
        "tenant_account_last4": mask_account_no(bv.get("tenant_account_no", "")),
        "to_lessor_verified": bool(bv.get("to_lessor_verified")),
        "to_lessor_verified_at": bv.get("to_lessor_verified_at", ""),
        "to_tenant_verified": bool(bv.get("to_tenant_verified")),
        "to_tenant_verified_at": bv.get("to_tenant_verified_at", ""),
        "skip_lessor_agreed": bool(bv.get("skip_lessor_agreed")),
        "skip_tenant_agreed": bool(bv.get("skip_tenant_agreed")),
        "skip_reason": bv.get("skip_reason", ""),
        "done": self.is_bank_verify_done(),
    }


def sanitize_session_dict(d: Dict[str, Any], viewer_role: str) -> Dict[str, Any]:
    """API 응답 직전에 난수 4자리 코드를 역할별로 필터링한다.

    - 임차인(송금인)은 임대인 계좌로 보낼 `to_lessor_code`만 볼 수 있고, 본인이 통장에서 읽어 입력해야 하는 `to_tenant_code`는 볼 수 없다.
    - 임대인(송금인)은 임차인 계좌로 보낼 `to_tenant_code`만 볼 수 있고, 본인이 통장에서 읽어 입력해야 하는 `to_lessor_code`는 볼 수 없다.
    - 관리자(보기 전용)는 양측 난수 코드를 모두 볼 수 없다.
    """
    out = dict(d)
    raw_bv = out.get("bank_verify")
    bv = dict(raw_bv) if isinstance(raw_bv, dict) else {}
    has_to_lessor = bool(bv.get("to_lessor_code"))
    has_to_tenant = bool(bv.get("to_tenant_code"))
    if viewer_role == "lessor":
        bv["to_lessor_code"] = ""
    elif viewer_role == "tenant":
        bv["to_tenant_code"] = ""
    else:
        bv["to_lessor_code"] = ""
        bv["to_tenant_code"] = ""
    bv["to_lessor_code_issued"] = has_to_lessor
    bv["to_tenant_code_issued"] = has_to_tenant
    mode = out.get("bank_verify_mode") or "REQUIRED"
    bv["done"] = (
        out.get("mode") == "RENEWAL"
        or mode in ("NOT_REQUIRED", "SKIPPED_MUTUAL")
        or bool(bv.get("to_lessor_verified") and bv.get("to_tenant_verified"))
    )
    out["bank_verify"] = bv
    return out


def register_bank_account(
    self: CoSignSession,
    role: str,
    bank_name: str,
    account_no: str,
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """임대인(임차료·보증금 수납 계좌) 또는 임차인(퇴실 보증금 환불 계좌)의 계좌번호를 등록하고 1원 난수 코드를 발급한다."""
    self._ensure_ready_for_steps()
    if self.mode == "RENEWAL":
        return self
    if self.lessor_signature or self.tenant_signature:
        raise DomainError(
            "전자서명이 시작된 뒤에는 계좌번호를 바꿀 수 없습니다.",
            hint="계좌를 변경하려면 '조건 다시 열기' 후 수정해 주세요.",
        )

    bank, acc, formatted = format_account(bank_name, account_no)
    bv = _ensure_bank_dict(self)
    ts = now_iso()
    bv["initiated"] = True

    if self.bank_verify_mode == "SKIPPED_MUTUAL":
        self.bank_verify_mode = "REQUIRED"
        bv["skip_lessor_agreed"] = False
        bv["skip_lessor_at"] = ""
        bv["skip_tenant_agreed"] = False
        bv["skip_tenant_at"] = ""
        bv["skip_reason"] = ""

    from .. import contract_chat
    if role == "lessor":
        changed = (self.lessor_account.strip() != formatted)
        self.lessor_account = formatted
        bv["lessor_bank"] = bank
        bv["lessor_account_no"] = acc
        bv["lessor_account_registered_at"] = ts
        if changed or not bv.get("to_lessor_code"):
            bv["to_lessor_code"] = gen_code4()
            bv["to_lessor_issued_at"] = ts
            bv["to_lessor_sent_at"] = ""
            bv["to_lessor_holder_checked"] = False
            bv["to_lessor_tries"] = 0
            bv["to_lessor_verified"] = False
            bv["to_lessor_verified_at"] = ""
        lsr = Lessor.get(self.id_lessor) if self.id_lessor else Lessor.for_branch(self.branch)
        if lsr is not None and not (lsr.account_number or "").strip():
            lsr.account_number = formatted
            lsr.save(action="lessor_account_seeded_from_cosign", app_source=app_source)
        note = f"🏦 임대인이 임차료·보증금 수납 계좌({bank} {mask_account_no(acc)})를 등록했습니다."
    else:
        changed = (self.tenant_refund_account.strip() != formatted)
        self.tenant_refund_account = formatted
        bv["tenant_bank"] = bank
        bv["tenant_account_no"] = acc
        bv["tenant_account_registered_at"] = ts
        if changed or not bv.get("to_tenant_code"):
            bv["to_tenant_code"] = gen_code4()
            bv["to_tenant_issued_at"] = ts
            bv["to_tenant_sent_at"] = ""
            bv["to_tenant_holder_checked"] = False
            bv["to_tenant_tries"] = 0
            bv["to_tenant_verified"] = False
            bv["to_tenant_verified_at"] = ""
        if self.lessor_account and not bv.get("to_lessor_code"):
            bv["to_lessor_code"] = gen_code4()
            bv["to_lessor_issued_at"] = ts
            bv.setdefault("lessor_account_registered_at", ts)
        note = f"🏦 임차인이 보증금 환불 계좌({bank} {mask_account_no(acc)})를 등록했습니다."

    self.bank_verify = bv
    saved = self.save(action=f"bank_account_registered_by_{role}", app_source=app_source)
    contract_chat.system_note(saved, note, step=saved.current_step)
    return saved


def mark_one_won_sent(
    self: CoSignSession,
    role: str,
    holder_name_checked: bool = True,
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """송금인이 은행 앱에서 상대방 예금주(실명)를 확인하고 난수 4자리 코드와 함께 1원을 보냈음을 기록한다."""
    self._ensure_ready_for_steps()
    if self.mode == "RENEWAL":
        return self
    if not holder_name_checked:
        raise DomainError(
            "은행 앱 송금 화면에서 상대방 예금주(실명) 일치를 확인해 체크해 주세요.",
            hint="받는 분 통장 표시(입금자명)는 송금인이 바꿀 수 있으므로, 송금 전 예금주 실명 확인이 핵심입니다.",
        )

    bv = _ensure_bank_dict(self)
    ts = now_iso()
    bv["initiated"] = True
    from .. import contract_chat

    if role == "tenant":
        # 임차인 → 임대인 계좌로 1원 송금
        if not self.lessor_account.strip() or not bv.get("lessor_account_no"):
            raise DomainError("임대인의 수납 계좌가 먼저 등록되어야 합니다.", hint="② 계좌번호 등록에서 임대인이 계좌를 저장해 주세요.")
        if not bv.get("to_lessor_code"):
            bv["to_lessor_code"] = gen_code4()
            bv["to_lessor_issued_at"] = ts
        bv["to_lessor_holder_checked"] = True
        bv["to_lessor_sent_at"] = ts
        note = "💸 임차인이 임대인 계좌 예금주(실명)를 확인하고 1원을 송금했습니다. 임대인은 본인 통장에 찍힌 4자리 코드를 입력해 주세요."
    else:
        # 임대인 → 임차인 계좌로 1원 송금
        if not self.tenant_refund_account.strip() or not bv.get("tenant_account_no"):
            raise DomainError("임차인의 보증금 환불 계좌가 먼저 등록되어야 합니다.", hint="② 계좌번호 등록에서 임차인이 계좌를 저장해 주세요.")
        if not bv.get("to_tenant_code"):
            bv["to_tenant_code"] = gen_code4()
            bv["to_tenant_issued_at"] = ts
        bv["to_tenant_holder_checked"] = True
        bv["to_tenant_sent_at"] = ts
        note = "💸 임대인이 임차인 계좌 예금주(실명)를 확인하고 1원을 송금했습니다. 임차인은 본인 통장에 찍힌 4자리 코드를 입력해 주세요."

    self.bank_verify = bv
    saved = self.save(action=f"bank_1won_sent_by_{role}", app_source=app_source)
    contract_chat.system_note(saved, note, step=saved.current_step)
    return saved


def _promote_tenant_identity_from_bank(self: CoSignSession, bv: Dict[str, Any], app_source: str) -> None:
    """임차인이 본인 환불 계좌로 받은 1원 난수 4자리 코드를 검증 통과하면 임차인 계정의 본인확인 기록을 자동 확정한다."""
    t = Tenant.get(self.id_tenant) if self.id_tenant else None
    if t is None:
        ph = re.sub(r"\D", "", str((self.terms or {}).get("tenant_phone") or ""))
        t = next((x for x in Tenant.all() if ph and re.sub(r"\D", "", x.phone) == ph), None)
    if t is None:
        return
    if self.tenant_refund_account.strip():
        t.account_number = self.tenant_refund_account.strip()
    cur = dict(t.identity or {})
    # 기존 공인/문자 인증이 없는 경우(또는 fake/invite만 있는 경우) bank_1won 본인확인으로 확정
    if not cur.get("verified") or cur.get("fake") or cur.get("provider") in ("", "invite"):
        t.identity = {
            "verified": True,
            "name": t.name or str((self.terms or {}).get("tenant_name") or ""),
            "phone": t.phone or str((self.terms or {}).get("tenant_phone") or ""),
            "birth6": cur.get("birth6", ""),
            "provider": "bank_1won",
            "fake": False,
            "assurance": "bank_account_1won",
            "bank": bv.get("tenant_bank", ""),
            "account_last4": mask_account_no(bv.get("tenant_account_no", "")),
            "verified_at": now_iso(),
        }
    t.save(action="tenant_identity_verified_by_bank_1won", app_source=app_source)


def confirm_one_won_code(
    self: CoSignSession,
    role: str,
    code: str,
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """수신자(계좌 주인)가 본인 통장 입금 내역에 찍힌 난수 4자리 코드를 입력해 계좌 실점유와 실명을 검증한다."""
    self._ensure_ready_for_steps()
    if self.mode == "RENEWAL":
        return self
    digits = re.sub(r"\D", "", (code or "").strip())
    if len(digits) != 4:
        raise DomainError("통장 입금 내역에 찍힌 숫자 4자리 코드를 입력해 주세요.", hint="예: 4821")

    bv = _ensure_bank_dict(self)
    bv["initiated"] = True
    ts = now_iso()
    from .. import contract_chat

    if role == "lessor":
        # 임대인이 임차인으로부터 받은 1원의 4자리 코드 입력
        expected = str(bv.get("to_lessor_code") or "")
        if not expected:
            raise DomainError("발급된 1원 확인 코드가 없습니다.", hint="먼저 계좌 등록 및 임차인의 1원 송금을 진행해 주세요.")
        tries = int(bv.get("to_lessor_tries") or 0)
        if tries >= MAX_ONE_WON_TRIES:
            raise DomainError(
                "4자리 코드를 5회 이상 틀려 잠겼습니다.",
                hint="임차인 화면에서 [새 코드 재발급]을 눌러 다시 1원을 송금받아 주세요.",
            )
        bv["to_lessor_tries"] = tries + 1
        if not hmac.compare_digest(digits, expected):
            self.bank_verify = bv
            self.save(action="bank_1won_confirm_failed_lessor", app_source=app_source)
            raise DomainError(
                f"입금 내역의 4자리 코드가 일치하지 않습니다 ({bv['to_lessor_tries']}/{MAX_ONE_WON_TRIES}회).",
                hint="본인 은행 앱의 최근 입금 내역(1원)에 표시된 숫자 4자리를 정확히 입력해 주세요.",
            )
        bv["to_lessor_verified"] = True
        bv["to_lessor_verified_at"] = ts
        bv["to_lessor_tries"] = 0
        who_msg = "✅ 임대인이 1원 입금 코드(4자리) 확인을 완료했습니다 — 임대인 수납 계좌 실명·점유 입증 완료."
    else:
        # 임차인이 임대인으로부터 받은 1원의 4자리 코드 입력
        expected = str(bv.get("to_tenant_code") or "")
        if not expected:
            raise DomainError("발급된 1원 확인 코드가 없습니다.", hint="먼저 계좌 등록 및 임대인의 1원 송금을 진행해 주세요.")
        tries = int(bv.get("to_tenant_tries") or 0)
        if tries >= MAX_ONE_WON_TRIES:
            raise DomainError(
                "4자리 코드를 5회 이상 틀려 잠겼습니다.",
                hint="임대인 화면에서 [새 코드 재발급]을 눌러 다시 1원을 송금받아 주세요.",
            )
        bv["to_tenant_tries"] = tries + 1
        if not hmac.compare_digest(digits, expected):
            self.bank_verify = bv
            self.save(action="bank_1won_confirm_failed_tenant", app_source=app_source)
            raise DomainError(
                f"입금 내역의 4자리 코드가 일치하지 않습니다 ({bv['to_tenant_tries']}/{MAX_ONE_WON_TRIES}회).",
                hint="본인 은행 앱의 최근 입금 내역(1원)에 표시된 숫자 4자리를 정확히 입력해 주세요.",
            )
        bv["to_tenant_verified"] = True
        bv["to_tenant_verified_at"] = ts
        bv["to_tenant_tries"] = 0
        _promote_tenant_identity_from_bank(self, bv, app_source=app_source)
        who_msg = "✅ 임차인이 1원 입금 코드(4자리) 확인을 완료했습니다 — 임차인 환불 계좌 실명·점유 및 본인확인 완료."

    self.bank_verify = bv
    saved = self.save(action=f"bank_1won_confirmed_by_{role}", app_source=app_source)
    if bv.get("to_lessor_verified") and bv.get("to_tenant_verified"):
        contract_chat.system_note(
            saved,
            "🎉 임대인·임차인 상호 1원 송수금(난수 4자리 코드) 확인이 모두 완료되었습니다! 이제 등기부등본으로 임대인 소유권을 재확인해 주세요.",
            step=saved.current_step,
        )
    else:
        contract_chat.system_note(saved, who_msg, step=saved.current_step)
    return saved


def reissue_one_won_code(
    self: CoSignSession,
    role: str,
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """송금인이 상대방 계좌로 보낼 새 난수 4자리 코드를 재발급한다."""
    self._ensure_ready_for_steps()
    if self.mode == "RENEWAL":
        return self
    bv = _ensure_bank_dict(self)
    ts = now_iso()
    bv["initiated"] = True
    if role == "tenant":
        bv["to_lessor_code"] = gen_code4()
        bv["to_lessor_issued_at"] = ts
        bv["to_lessor_sent_at"] = ""
        bv["to_lessor_tries"] = 0
        bv["to_lessor_verified"] = False
        bv["to_lessor_verified_at"] = ""
    else:
        bv["to_tenant_code"] = gen_code4()
        bv["to_tenant_issued_at"] = ts
        bv["to_tenant_sent_at"] = ""
        bv["to_tenant_tries"] = 0
        bv["to_tenant_verified"] = False
        bv["to_tenant_verified_at"] = ""
    self.bank_verify = bv
    return self.save(action=f"bank_1won_reissued_by_{role}", app_source=app_source)


def toggle_bank_verify_skip(
    self: CoSignSession,
    role: str,
    agree: bool = True,
    reason: str = "",
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """예외 상황(외국인·타행이체 제한 계좌 등)에서 양측 모두 합의 시 대면 실물 신분증 확인으로 1원 확인을 생략한다."""
    self._ensure_ready_for_steps()
    if self.mode == "RENEWAL":
        return self
    if self.lessor_signature or self.tenant_signature:
        raise DomainError("전자서명이 시작된 뒤에는 1원 확인 생략 설정을 바꿀 수 없습니다.")

    bv = _ensure_bank_dict(self)
    bv["initiated"] = True
    ts = now_iso()
    clean_reason = (reason or "").strip()[:120]
    if role == "lessor":
        bv["skip_lessor_agreed"] = bool(agree)
        bv["skip_lessor_at"] = ts if agree else ""
    else:
        bv["skip_tenant_agreed"] = bool(agree)
        bv["skip_tenant_at"] = ts if agree else ""
    if clean_reason:
        bv["skip_reason"] = clean_reason
    elif agree and not bv.get("skip_reason"):
        bv["skip_reason"] = "대면 실물 신분증 확인으로 1원 확인 생략 합의"

    both = bool(bv.get("skip_lessor_agreed") and bv.get("skip_tenant_agreed"))
    self.bank_verify_mode = "SKIPPED_MUTUAL" if both else "REQUIRED"
    self.bank_verify = bv
    saved = self.save(action=f"bank_1won_skip_{'agreed' if agree else 'cancelled'}_by_{role}", app_source=app_source)
    from .. import contract_chat
    if both:
        contract_chat.system_note(
            saved,
            f"ℹ️ 임대인·임차인 양측 합의로 1원 송수금 확인을 생략했습니다 ({bv.get('skip_reason')}). 대면 실물 신분증 확인은 필수입니다.",
            step=saved.current_step,
        )
    return saved
