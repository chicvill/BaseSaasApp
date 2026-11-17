"""
apps/rent_dist/_rent3_core/balancecheck.py — 잔금 전 등기부 재확인: 임차인이 인터넷등기소에서 직접 열람한 등기부를 올리면 AI 가 읽고,
계약 때 본 값과 코드로 비교해 달라진 점을 알려 준다. 임차인이 원본과 대조했다고 '확인했음'에 체크해야만 '변동 없음'을 기록할 수 있다.

원칙
- 계약 때 값(계약 시 직접 열람 기록)과의 비교·열람 날짜 판정은 AI 가 아니라 코드가 한다.
- AI 가 달라진 점(근저당 증가·압류·소유자 변경)을 찾으면 '변동 없음'으로는 기록할 수 없다(변동 신고만 가능).
- 열람 일시가 오래됐거나(RECENT_DAYS 초과) 읽지 못하면 '변동 없음'을 기록할 수 없다 — 잔금 직전에 새로 열람해야 한다.
- AI 를 쓸 수 없을 때(키 없음·쿼터)에는 직접 열람 후 체크만으로 기록할 수 있다(AI 없이 했다는 표시를 남긴다).
- 올린 파일은 읽는 데만 쓰고 서버에 저장하지 않는다(해시만 남긴다).
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Dict, List

from core.errors import DomainError

from . import registry_ai
from .models import Contract
from .pool import now_iso

RECENT_DAYS = 3          # 잔금 직전이라고 인정하는 열람 일시의 허용 범위(일)
MAX_AI_CALLS = 5


def _norm(v: Any) -> str:
    return registry_ai._norm_name(v)


def baseline(ct: Contract) -> Dict[str, Any]:
    """계약 때 임차인이 직접 열람해 기록한 값(없으면 계약 때 임대인이 제시한 값)."""
    snap = ct.registry_snapshot or {}
    t = snap.get("tenant_direct_registry") or {}
    return {
        "owner_name": t.get("owner_name") or snap.get("owner_name") or "",
        "mortgage_amount": int(t.get("mortgage_amount") if t.get("mortgage_amount") is not None else snap.get("mortgage_amount") or 0),
        "seizure_or_trust": t.get("seizure_or_trust") or "없음",
    }


def differences(base: Dict[str, Any], ex: Dict[str, Any]) -> List[str]:
    """계약 때와 달라진 점(코드 계산)."""
    out: List[str] = []
    if base["owner_name"] and ex["owner_name"] and _norm(base["owner_name"]) != _norm(ex["owner_name"]) and not any(
            _norm(o["name"]) == _norm(base["owner_name"]) for o in ex["owners"]):
        out.append(f"소유자가 달라졌습니다: 계약 때 {base['owner_name']} → 지금 {ex['owner_name']}")
    if int(ex["mortgage_total"]) > int(base["mortgage_amount"]):
        out.append(f"근저당 채권최고액이 늘었습니다: 계약 때 {int(base['mortgage_amount']):,}원 → 지금 {int(ex['mortgage_total']):,}원")
    if ex["seizure_or_trust"] == "있음" and base["seizure_or_trust"] != "있음":
        out.append("압류·가압류·가처분·신탁 등 새 등기가 있습니다")
    return out


def is_recent(viewed_at: str, today: date | None = None) -> bool:
    today = today or date.today()
    try:
        d = datetime.fromisoformat(str(viewed_at).strip()[:16]).date()
    except ValueError:
        return False
    return -1 <= (today - d).days <= RECENT_DAYS


def read(ct: Contract, data_url: str, consent: bool, app_source: str = "rent3_tenant") -> Dict[str, Any]:
    if not consent:
        raise DomainError("AI 읽기에 동의해 주세요.", hint="올린 등기부가 AI(Gemini) 서비스로 전송되어 글자를 읽는 데 쓰입니다. 파일은 서버에 저장하지 않습니다.")
    br = dict(ct.balance_registry or {})
    calls = int((br.get("read") or {}).get("calls") or 0)
    if calls >= MAX_AI_CALLS:
        raise DomainError(f"AI 읽기 횟수({MAX_AI_CALLS}회)를 모두 썼습니다.", hint="인터넷등기소 원본을 직접 보고 변동 여부를 확인해 주세요.")
    binary, mime = registry_ai.decode_data_url(data_url)
    owner = (ct.registry_snapshot or {}).get("owner_name") or ct.lessor_name
    res = registry_ai.analyze(binary, mime, lessor_names=[n for n in (ct.lessor_name, owner) if n],
                              deposit=int(ct.deposit or 0), property_value=0)
    if res["status"] == "unavailable":
        br["read"] = {"status": "unavailable", "calls": calls, "read_at": now_iso()}
        ct.balance_registry = br
        ct.save(action="balance_registry_ai_unavailable", app_source=app_source)
        return {"status": "unavailable", "message": res["message"], "read": br["read"]}
    if res["status"] != "ok":
        raise DomainError(res.get("message") or "등기부를 읽지 못했습니다.", hint="더 선명한 파일(인터넷등기소 PDF)로 다시 올려 주세요.")
    ex = res["extracted"]
    if ex["document_type"] != "registry":
        raise DomainError("등기사항증명서가 아닌 문서로 보입니다.", hint="인터넷등기소에서 방금 열람한 등기사항증명서(PDF)를 올려 주세요.")
    diffs = differences(baseline(ct), ex)
    recent = is_recent(ex.get("viewed_at", ""))
    br["read"] = {
        "status": "ok", "calls": calls + 1, "read_at": now_iso(), "sha256": hashlib.sha256(binary).hexdigest(),
        "viewed_at": ex.get("viewed_at", ""), "recent": recent, "issue_number": ex["issue_number"], "owner_name": ex["owner_name"],
        "mortgage_total": ex["mortgage_total"], "seizure_or_trust": ex["seizure_or_trust"], "differences": diffs,
        "risks": [r for r in res["risks"] if r["level"] != "info"],
    }
    ct.balance_registry = br
    ct.save(action="balance_registry_ai_read", app_source=app_source)
    return {"status": "ok", "message": "", "read": br["read"]}


def check_no_change(ct: Contract, checked: bool) -> Dict[str, Any]:
    """'변동 없음' 기록의 조건을 검사하고, 통과하면 기록에 남길 근거(read 요약)를 돌려준다."""
    if not checked:
        raise DomainError("원본과 대조하고 '확인했음'에 체크해 주세요.",
                          hint="체크하지 않으면 다음 단계(잔금·출입 정보 교부)로 진행할 수 없습니다. 인터넷등기소에서 직접 열람해 확인한 뒤 체크해 주세요.")
    rd = (ct.balance_registry or {}).get("read") or {}
    if not rd:
        raise DomainError("먼저 방금 열람한 등기부를 올려 AI 로 확인해 주세요.", hint="인터넷등기소에서 열람한 PDF를 [올려서 확인]으로 올려 주세요.")
    if rd.get("status") == "ok":
        if rd.get("differences"):
            raise DomainError("계약 때와 달라진 점이 있어 '변동 없음'으로 기록할 수 없습니다.",
                              hint=" / ".join(rd["differences"]) + " — 잔금을 보내지 말고 [변동 있음 신고]를 눌러 주세요.")
        if not rd.get("recent"):
            raise DomainError("열람 일시가 오래되었거나 읽을 수 없습니다.",
                              hint=f"잔금 직전({RECENT_DAYS}일 이내)에 인터넷등기소에서 새로 열람한 등기부를 올려 주세요. (읽힌 열람 일시: {rd.get('viewed_at') or '없음'})")
    return {"ai": rd.get("status") == "ok", "viewed_at": rd.get("viewed_at", ""), "read_sha256": rd.get("sha256", "")}


def record(ct: Contract, changed: bool, checked: bool, issue_number: str = "", mortgage_cleared: bool = False,
           app_source: str = "rent3_contract") -> Dict[str, Any]:
    """임차인의 '변동 없음 확인' 또는 '변동 있음 신고'를 계약에 기록한다(계약 화면·마이홈 공통 규칙).
    변동 없음은 확인했음 체크·AI 대조·열람 일시 조건을 모두 통과해야 하고, 변동 신고는 언제나 가능하다."""
    if ct.mortgage_cleanup_required and not changed and not mortgage_cleared:
        raise DomainError(
            "이 계약은 '잔금 시 근저당 말소 특약'이 있습니다. 등기부에서 근저당이 말소된 것을 확인해야 합니다.",
            hint="아직 말소되지 않았다면 '변동 있음'이 아니라 잔금을 보내지 말고 임대인에게 말소를 요구하세요. 말소된 등기부를 확인한 뒤 다시 확인해 주세요.",
        )
    prior = (ct.balance_registry or {}).get("read") or {}
    evidence = {} if changed else check_no_change(ct, checked)
    ct.balance_registry = {"checked_at": now_iso(), "changed": bool(changed), "issue_number": (issue_number or "").strip() or prior.get("issue_number", ""),
                           "mortgage_cleared": bool(mortgage_cleared), "read": prior, "evidence": evidence}
    ct.save(action="balance_registry_changed_reported" if changed else "balance_registry_checked", app_source=app_source)
    return ct.balance_registry
