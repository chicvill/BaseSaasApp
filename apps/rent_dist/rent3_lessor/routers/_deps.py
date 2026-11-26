"""
apps/rent_dist/rent3_lessor/routers/_deps.py — 임대인 라우터 공통 상수·헬퍼.
"""
import re
import sys
from typing import Any, Dict, List, Optional
from fastapi import Request

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Lessor,
    Room,
    Tenant,
    normalize_branch_id,
)
from apps.rent_dist._rent3_core.authz import claims_from, is_local_request

APP_SRC = "rent3_lessor"
FRANCHISE_AI_MAX_CALLS = 3  # 신청 1건당 등기부 AI 읽기 상한 (남용·비용 방지)


def get_franchise_ai_max_calls() -> int:
    """테스트 등에서 apps.rent_dist.rent3_lessor.api.FRANCHISE_AI_MAX_CALLS 를 동적으로 조정해도 반영된다."""
    api_mod = sys.modules.get("apps.rent_dist.rent3_lessor.api")
    if api_mod is not None and hasattr(api_mod, "FRANCHISE_AI_MAX_CALLS"):
        return int(getattr(api_mod, "FRANCHISE_AI_MAX_CALLS"))
    return FRANCHISE_AI_MAX_CALLS


def _franchise_info(branch: str) -> Dict[str, Any]:
    lsr = Lessor.for_branch(branch)
    if lsr is None:
        return {
            "status": "ACTIVE",
            "label": "가맹 승인",
            "open": True,
            "note": "",
            "room_limit": 0,
            "room_count": Room.count(branch=branch),
        }
    return {
        "status": lsr.status,
        "label": Lessor.STATUS_LABELS.get(lsr.status, lsr.status),
        "open": lsr.status == "ACTIVE",
        "note": lsr.status_note,
        "room_limit": lsr.room_limit,
        "room_count": Room.count(branch=branch),
    }


def _my_lessor(branch: str) -> Lessor:
    lsr = Lessor.for_branch(normalize_branch_id(branch))
    if lsr is None:
        raise DomainError("이 지점의 임대인 정보를 찾을 수 없습니다.")
    return lsr


def _is_loopback(request: Request) -> bool:
    return is_local_request(request)


def _reservation_summary(room: Room) -> Optional[Dict[str, Any]]:
    """입주 중인 호실에 미리 체결한 예약 계약의 요약(없으면 None). 주민번호·서명 같은 상세는 싣지 않는다."""
    ct = room.reservation()
    if ct is None:
        return None
    return {
        "contract_id": ct.id,
        "kind": "RENEWAL" if ct.renews_contract_id else "NEW",
        "tenant_name": ct.tenant_name,
        "tenant_phone": ct.tenant_phone,
        "move_in_date": ct.move_in_date,
        "deposit": ct.deposit,
        "monthly_rent": ct.monthly_rent,
        "maintenance_fee": ct.maintenance_fee,
        "contract_date": ct.contract_date,
    }


def _pin_info(request: Request) -> Dict[str, Any]:
    """간편 비밀번호로 들어온 화면이면 수납 허용 여부와 오늘 남은 한도를 알려 준다(화면이 막힌 버튼을 미리 꺼 두는 용도)."""
    from apps.rent_dist._rent3_core import quicklogin as ql
    c = claims_from(request) or {}
    if c.get("m") != "pin":
        return {"via_pin": False}
    t = ql.load_account(c)
    return {"via_pin": True, **(ql.pin_pay_state(t, str(c.get("d", ""))) if t is not None else {"pay_ok": False})}


def _pin_pay_gate(request: Request, amount: int) -> Optional[Dict[str, Any]]:
    """PIN 토큰이면 수납 허용 기기·하루 한도를 확인하고 그 토큰 정보를 돌려준다(일반 로그인이면 None)."""
    from apps.rent_dist._rent3_core import quicklogin as ql
    c = claims_from(request) or {}
    if c.get("m") != "pin":
        return None
    ql.pin_pay_gate(c, int(amount))
    return c


def _pin_pay_record(c: Optional[Dict[str, Any]], res: Dict[str, Any]) -> None:
    if c and not res.get("needs_confirm"):
        from apps.rent_dist._rent3_core import quicklogin as ql
        ql.pin_pay_record(c, int((res.get("payment") or {}).get("total_amount") or 0))


def _parse_room_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        tokens = [re.sub(r"[\"'`\[\]\s]|호$", "", str(x)).strip() for x in raw]
    else:
        tokens = [re.sub(r"[\"'`\[\]\s]|호$", "", t).strip() for t in re.split(r"[,\s;/]+", str(raw)) if t.strip()]
    seen: List[str] = []
    for tok in tokens:
        if tok and tok not in seen:
            seen.append(tok)
    return seen


def _branch_tenants(branch: str) -> List[Tenant]:
    from apps.rent_dist._rent3_core import quicklogin as ql
    b_id = normalize_branch_id(branch)
    ts = [t for t in Tenant.all() if normalize_branch_id(t.branch) == b_id and ql.eligible(t)]
    return sorted(ts, key=lambda t: (t.room_no or ""))
