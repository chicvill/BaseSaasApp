"""
apps/rent_dist/rent3_contract/routers/_deps.py — 공동 임대계약 라우터 공통 상수·헬퍼.
"""
import re
from typing import Any, Dict, Optional
from fastapi import Request

from core.errors import DomainError
from apps.rent_dist._rent3_core import Lessor, Tenant, chatsessions as chat_sessions
from apps.rent_dist._rent3_core.authz import LESSOR_ROLES, claims_from, is_admin, is_local_request, need_claims
from apps.rent_dist._rent3_core.domains.cosign_bank import sanitize_session_dict

APP_SRC = "rent3_contract"
REGISTRY_AI_MAX_CALLS = 5  # 계약 1건당 Gemini 호출 상한 (비용 보호)


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _loopback(request: Request) -> bool:
    return is_local_request(request)


def _tenant_of(request: Request) -> Tenant:
    c = claims_from(request) or {}
    if c.get("r") != "tenant":
        raise DomainError("임차인 계정으로 로그인했을 때만 쓸 수 있습니다.", hint="임대인은 가맹 신청 단계에서 본인인증을 합니다.")
    t = Tenant.get(str(c.get("u", "")).removeprefix("tenant:"))
    if t is None:
        raise DomainError("임차인 정보를 찾을 수 없습니다. 다시 로그인해 주세요.")
    return t


def _viewer_role(request: Optional[Request], fallback_role: str = "") -> str:
    """요청 토큰에서 현재 조회자 역할('lessor' | 'tenant' | '')을 결정한다."""
    if request is not None:
        c = claims_from(request) or {}
        if is_admin(c):
            return ""
        if c.get("r") in LESSOR_ROLES:
            return "lessor"
        if c.get("r") == "tenant":
            return "tenant"
    return fallback_role if fallback_role in ("lessor", "tenant") else ""


def _session_view(s: Any, request: Optional[Request] = None, role: str = "") -> Dict[str, Any]:
    """세션 응답 직전 역할에 맞게 1원 난수 4자리 코드를 안전하게 필터링한다."""
    return sanitize_session_dict(s.model_dump(), _viewer_role(request, role))


def _chat_actor(request: Request, s) -> Dict[str, str]:
    """토큰으로 이 대화의 발화자(역할·이름)를 정한다. 본문에 적은 역할은 믿지 않는다. 관리인은 읽기만 가능(쓰기는 authz 가 막는다)."""
    c = need_claims(request)
    if is_admin(c):
        return {"role": "", "name": "관리인"}
    if c.get("r") in LESSOR_ROLES:
        return {"role": "lessor", "name": s.lessor_name or "임대인"}
    return {"role": "tenant", "name": str((s.terms or {}).get("tenant_name") or "임차인")}


def _touch_chat_session(s, role: str, request: Request) -> None:
    try:
        if role == "lessor":
            me = Lessor.for_branch(s.branch)
            viewer = f"lessor:{me.id}" if me else ""
        else:
            c = claims_from(request) or {}
            tid = str(c.get("u", "")).removeprefix("tenant:")
            tid = tid if Tenant.get(tid) else (s.id_tenant or "")
            viewer = f"tenant:{tid}" if tid else ""
        if viewer:
            chat_sessions.touch(viewer, "contract", s.id, f"{s.room_no}호 계약방", s.branch)
    except Exception:  # 목록 기록이 실패해도 채팅은 계속 된다
        pass
