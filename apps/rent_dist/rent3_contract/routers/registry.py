"""
apps/rent_dist/rent3_contract/routers/registry.py — Step 1 등기부 확인 · AI 읽기 · 체결 직전 재열람 · 입주(잔금) 전 재확인.
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.errors import DomainError
from apps.rent_dist._rent3_core import Building, CoSignSession, Contract, Lessor, balancecheck as bc, registry_ai
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import APP_SRC, REGISTRY_AI_MAX_CALLS

router = APIRouter()


class Step1ConfirmReq(BaseModel):
    role: str = "tenant"  # lessor / tenant
    registry_updates: Optional[Dict[str, Any]] = None
    checked_counterparty_id: Optional[bool] = None


class TenantRegistryReq(BaseModel):
    registry: Dict[str, Any] = Field(default_factory=dict)
    attachment_data_url: str = ""
    attachment_name: str = ""


class RegistryAiReq(BaseModel):
    attachment_data_url: str
    consent: bool = False


class RecheckReq(BaseModel):
    changed: bool = False


class BalanceReadReq(BaseModel):
    role: str = "tenant"
    attachment_data_url: str
    consent: bool = False


class BalanceCheckReq(BaseModel):
    role: str = "tenant"
    changed: bool = False
    checked: bool = False          # '변동 없음'일 때: 원본과 대조해 확인했음(체크 없으면 진행 불가)
    issue_number: str = ""
    mortgage_cleared: bool = False


def _balance_contract_of(session_id: str, role: str):
    """입주(잔금) 전 등기부 재확인 대상 계약: 체결이 끝났고 아직 출입 정보를 교부하지 않은 계약. 임차인만."""
    if role != "tenant":
        raise DomainError("입주 전 등기부 재확인은 임차인이 합니다.", hint="임차인 계정으로 진행해 주세요.")
    s = CoSignSession.by_code_or_id(session_id)
    ct = Contract.get(s.created_contract_id) if (s.status == "COMPLETED" and s.created_contract_id) else None
    if ct is None or ct.keys_released is not False:
        raise DomainError("입주 전 등기부 재확인 대상 계약이 아닙니다.", hint="계약이 체결된 뒤, 출입 정보를 받기 전까지만 사용합니다.")
    return s, ct


@router.post("/sessions/{session_id}/step1")
def confirm_step1(session_id: str, req: Step1ConfirmReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.confirm_step1(
        role=req.role,
        registry_updates=req.registry_updates,
        checked_counterparty_id=req.checked_counterparty_id,
        app_source=APP_SRC,
    )
    msg = (
        "상호 신분증 본인 확인 및 등기부등본 권리 확인이 모두 완료되어 Step 2로 이동합니다!"
        if s.is_step1_fully_verified()
        else "등기부등본 권리 확인 상태가 실시간 반영되었습니다. (양측 신분증 확인 체크 및 등기 확인 완료 시 다음 단계로 진행)"
    )
    return {"success": True, "message": msg, "session": s.model_dump()}


@router.post("/sessions/{session_id}/tenant-registry")
def submit_tenant_registry(session_id: str, req: TenantRegistryReq):
    """임차인이 인터넷등기소에서 직접 열람한 결과(+선택 첨부)를 기록한다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.submit_tenant_registry(
        req.registry,
        attachment_data_url=req.attachment_data_url,
        attachment_name=req.attachment_name,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": "직접 열람한 등기부 확인 내용이 기록되었습니다.",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/registry-ai")
def read_registry_with_ai(session_id: str, req: RegistryAiReq):
    """임차인이 올린 등기사항증명서를 Gemini 로 읽어 항목을 추출하고, 위험 신호는 코드로 계산한다.

    - 전송 동의가 있어야 한다. 올린 파일은 분석에 쓰고 서버에 저장하지 않는다.
    - 결과는 참고용이다. AI 는 서류의 진위를 가리지 못한다.
    """
    s = CoSignSession.by_code_or_id(session_id)
    s._ensure_ready_for_steps()
    if not req.consent:
        raise DomainError(
            "등기부 내용이 Google Gemini 로 전송되는 것에 동의해야 합니다.",
            hint="동의하지 않으면 AI 읽기 없이 직접 입력해 주세요.",
        )
    if s.registry_ai_calls >= REGISTRY_AI_MAX_CALLS:
        raise DomainError(
            f"이 계약의 AI 읽기 횟수({REGISTRY_AI_MAX_CALLS}회)를 모두 썼습니다.",
            hint="직접 입력해 주세요.",
        )
    lsr = Lessor.for_branch(s.branch)
    if lsr and CoSignSession.ai_calls_this_month(s.branch) >= lsr.ai_monthly_limit:
        raise DomainError(
            f"이 건물의 이번 달 AI 읽기 한도({lsr.ai_monthly_limit}회)를 모두 썼습니다.",
            hint="직접 입력해 주세요. 한도는 본사 관리자에게 문의할 수 있습니다.",
        )
    binary, mime = registry_ai.decode_data_url(req.attachment_data_url)
    bldg = Building.get(s.id_building)
    t = s.terms or {}
    result = registry_ai.analyze(
        binary,
        mime,
        lessor_names=[n for n in (s.lessor_name, (s.registry_data or {}).get("owner_name")) if n],
        deposit=int(t.get("deposit") or 0),
        property_value=int(getattr(bldg, "property_value", 0) or 0) if bldg else 0,
    )
    if result["status"] == "ok":
        s.registry_ai_calls += 1
        s.registry_ai_times = [*s.registry_ai_times, now_iso()]
        s.registry_ai_result = result
        s.save(action="registry_ai_read", app_source=APP_SRC)
    return {
        "success": result["status"] == "ok",
        "level": "success" if result["status"] == "ok" else "warning",
        "message": (
            "AI가 등기부를 읽었습니다. 참고용이니 원본과 꼭 대조하세요."
            if result["status"] == "ok"
            else result["message"]
        ),
        "available": result["status"] != "unavailable",
        "result": result,
        "calls_left": max(REGISTRY_AI_MAX_CALLS - s.registry_ai_calls, 0),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/recheck")
def recheck_registry(session_id: str, req: RecheckReq):
    """체결 직전 임차인의 등기부 재열람 결과. 변동이 있으면 계약을 중단한다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.recheck_registry(changed=req.changed, app_source=APP_SRC)
    return {
        "success": True,
        "message": (
            "등기부에 변동이 확인되어 계약이 중단되었습니다."
            if req.changed
            else "체결 직전 재열람 확인이 기록되었습니다."
        ),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/balance-registry/read")
def balance_registry_read(session_id: str, req: BalanceReadReq):
    """입주(잔금) 전에 임차인이 인터넷등기소에서 다시 열람한 등기부를 올리면 AI 가 읽고 계약 때 값과 코드로 비교한다."""
    _s, ct = _balance_contract_of(session_id, req.role)
    res = bc.read(ct, req.attachment_data_url, req.consent, app_source=APP_SRC)
    if res["status"] == "unavailable":
        return {
            "success": False,
            "available": False,
            "message": res["message"] + " 인터넷등기소에서 직접 확인한 뒤 '확인했음'에 체크해 기록하세요.",
            "read": res["read"],
        }
    rd = res["read"]
    msg = (
        "계약 때와 달라진 점이 있습니다. 잔금을 보내지 말고 확인하세요."
        if rd["differences"]
        else "열람 일시가 오래되었거나 읽지 못했습니다. 잔금 직전에 새로 열람해 다시 올려 주세요."
        if not rd["recent"]
        else "AI 가 읽은 범위에서 계약 때와 달라진 점이 없습니다. 원본과 직접 대조한 뒤 '확인했음'에 체크해 주세요."
    )
    return {
        "success": True,
        "available": True,
        "level": "warning" if (rd["differences"] or not rd["recent"]) else "success",
        "message": msg,
        "read": rd,
    }


@router.post("/sessions/{session_id}/balance-registry/check")
def balance_registry_check(session_id: str, req: BalanceCheckReq):
    _s, ct = _balance_contract_of(session_id, req.role)
    br = bc.record(ct, req.changed, req.checked, req.issue_number, req.mortgage_cleared, app_source=APP_SRC)
    return {
        "success": True,
        "balance_registry": br,
        "contract": ct.model_dump(),
        "message": (
            "등기부 변동을 신고했습니다. 임대인·본사 확인 전까지 잔금 납부를 보류하세요."
            if req.changed
            else "입주 전 등기부 확인을 기록했습니다. 잔금 입금 후 임대인이 확인하면 출입 정보가 열립니다."
        ),
    }


@router.get("/sessions/{session_id}/registry-attachment")
def get_registry_attachment(session_id: str):
    s = CoSignSession.by_code_or_id(session_id)
    path, mime, name = s.registry_attachment_path()
    return FileResponse(path, media_type=mime, filename=name)
