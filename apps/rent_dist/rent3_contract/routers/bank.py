"""
apps/rent_dist/rent3_contract/routers/bank.py — 계좌번호 등록(임차료 수납·보증금 환불) 및 상호 1원 송수금 확인(난수 4자리 코드) API.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from apps.rent_dist._rent3_core import CoSignSession, ensure_pool_seeded
from ._deps import APP_SRC, _session_view

router = APIRouter()


class BankAccountReq(BaseModel):
    role: str = "tenant"
    bank_name: str
    account_no: str


class OneWonSentReq(BaseModel):
    role: str = "tenant"
    holder_name_checked: bool = True


class OneWonConfirmReq(BaseModel):
    role: str = "tenant"
    code: str


class OneWonReissueReq(BaseModel):
    role: str = "tenant"


class OneWonSkipReq(BaseModel):
    role: str = "tenant"
    agree: bool = True
    reason: str = ""


@router.post("/sessions/{session_id}/bank-account")
def register_bank_account_route(session_id: str, req: BankAccountReq, request: Request):
    """임대인 수납 계좌 또는 임차인 보증금 환불 계좌를 등록하고 상호 1원 난수 4자리 코드를 발급한다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.register_bank_account(
        role=req.role,
        bank_name=req.bank_name,
        account_no=req.account_no,
        app_source=APP_SRC,
    )
    msg = (
        "임차료·보증금 수납 계좌를 등록했습니다. 임차인이 1원 송금 후 통장에 찍힌 4자리 코드를 입력해 주세요."
        if req.role == "lessor"
        else "보증금 환불 계좌를 등록했습니다. 임대인이 1원 송금 후 통장에 찍힌 4자리 코드를 입력해 주세요."
    )
    return {"success": True, "message": msg, "session": _session_view(s, request, req.role)}


@router.post("/sessions/{session_id}/bank-verify/sent")
def mark_one_won_sent_route(session_id: str, req: OneWonSentReq, request: Request):
    """송금인이 은행 앱에서 상대방 예금주(실명)를 확인하고 난수 4자리 코드와 함께 1원을 보냈음을 기록한다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.mark_one_won_sent(
        role=req.role,
        holder_name_checked=req.holder_name_checked,
        app_source=APP_SRC,
    )
    msg = (
        "임대인 예금주 확인 및 1원 송금 표시를 완료했습니다. 임대인이 통장의 4자리 코드를 입력하면 검증됩니다."
        if req.role == "tenant"
        else "임차인 예금주 확인 및 1원 송금 표시를 완료했습니다. 임차인이 통장의 4자리 코드를 입력하면 검증됩니다."
    )
    return {"success": True, "message": msg, "session": _session_view(s, request, req.role)}


@router.post("/sessions/{session_id}/bank-verify/confirm")
def confirm_one_won_code_route(session_id: str, req: OneWonConfirmReq, request: Request):
    """수신자가 본인 통장 1원 입금 내역의 난수 4자리 코드를 입력해 계좌 실명·점유를 확정한다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.confirm_one_won_code(
        role=req.role,
        code=req.code,
        app_source=APP_SRC,
    )
    done = s.is_bank_verify_done()
    msg = (
        "상호 1원 송수금 확인이 모두 완료되었습니다! 이제 등기부등본으로 임대인 소유권을 재확인해 주세요."
        if done
        else "1원 입금 4자리 코드 확인이 완료되었습니다! 상대방 확인을 기다리는 중입니다."
    )
    return {"success": True, "message": msg, "session": _session_view(s, request, req.role)}


@router.post("/sessions/{session_id}/bank-verify/reissue")
def reissue_one_won_code_route(session_id: str, req: OneWonReissueReq, request: Request):
    """송금인이 새 난수 4자리 코드를 재발급한다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.reissue_one_won_code(role=req.role, app_source=APP_SRC)
    return {
        "success": True,
        "message": "새 1원 확인 난수 4자리 코드를 발급했습니다.",
        "session": _session_view(s, request, req.role),
    }


@router.post("/sessions/{session_id}/bank-verify/skip")
def toggle_bank_verify_skip_route(session_id: str, req: OneWonSkipReq, request: Request):
    """예외 상황(외국인·타행이체 제한 계좌 등)에서 양측 합의로 1원 확인을 생략한다."""
    ensure_pool_seeded()
    s = CoSignSession.by_code_or_id(session_id)
    s.toggle_bank_verify_skip(
        role=req.role,
        agree=req.agree,
        reason=req.reason,
        app_source=APP_SRC,
    )
    both = s.bank_verify_mode == "SKIPPED_MUTUAL"
    msg = (
        "양측 합의로 1원 송수금 확인을 생략했습니다 (대면 실물 신분증 확인으로 대체)."
        if both
        else ("1원 확인 생략 동의를 기록했습니다. 상대방도 동의하면 생략됩니다." if req.agree else "1원 확인 생략 동의를 취소했습니다.")
    )
    return {"success": True, "message": msg, "session": _session_view(s, request, req.role)}
