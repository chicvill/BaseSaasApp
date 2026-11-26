"""
apps/rent_dist/rent3_admin/routers/fees.py — 가맹비 청구·납부 등록·전자세금계산서 API(볼타)/웹훅·현금영수증·홈택스 CSV.
"""
import json
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import Lessor, ensure_pool_seeded, etaxinvoice as et, feebilling as fb
from ._deps import ADMIN_ONLY, APP_SRC, _admin_label

router = APIRouter()


class FeePlanReq(BaseModel):
    app_fee: int                  # 월 가맹비(부가세 포함 금액, 원). 0 이면 청구하지 않는다


class FeePaidReq(BaseModel):
    amount: int
    paid_date: str = ""
    method: str = "계좌이체"
    memo: str = ""


class FeeInvoiceReq(BaseModel):
    nts_confirm_no: str
    issue_date: str = ""


class FeeInvoiceCancelReq(BaseModel):
    memo: str = ""


class FeeGenerateReq(BaseModel):
    month: str = ""


class FeeCashReq(BaseModel):
    approval_no: str
    issue_date: str = ""


@router.get("/fees", dependencies=ADMIN_ONLY)
def list_franchise_fees(month: str = ""):
    """가맹비 청구 현황(자동 통보 포함). month 를 주면 그 청구월만."""
    ensure_pool_seeded()
    fb.ensure_notices()
    fb.refresh_pending()          # API 로 발행 중인 건의 결과를 확인
    rows = [f for f in fb.FranchiseFee.all() if f.status != "CANCELLED" and (not month or f.billing_month == month)]
    rows.sort(key=lambda f: (f.billing_month, f.branch), reverse=True)
    tot = lambda xs: sum(int(f.total_amount) for f in xs)
    paid = [f for f in rows if f.status == "PAID"]
    biz = {l.id: l.business_number for l in Lessor.all()}
    return {
        "success": True,
        "platform": fb.platform_info(),
        "month": month or fb.month_key(),
        "auto": fb.auto_enabled(),
        "etax": et.status_info(),
        "fees": [{**fb.public(f), "lessor_business_number": biz.get(f.id_lessor, "")} for f in rows],
        "summary": {
            "billed": tot(rows),
            "paid": tot(paid),
            "unpaid": tot([f for f in rows if f.status == "NOTICED"]),
            "overdue": len([f for f in rows if f.overdue]),
            "invoice_waiting": len([f for f in paid if f.invoice_status != "ISSUED"]),
        },
        "lessors": [
            {"id": l.id, "name": l.name, "branch": l.branch, "app_fee": l.app_fee, "status": l.status, "business_number": l.business_number}
            for l in Lessor.all()
        ],
    }


@router.post("/fees/generate", dependencies=ADMIN_ONLY)
def generate_franchise_fees(req: FeeGenerateReq):
    ensure_pool_seeded()
    made = fb.ensure_notices(req.month, force=True)
    return {"success": True, "message": f"{req.month or fb.month_key()} 가맹비 {len(made)}건을 새로 청구·통보했습니다(이미 있는 청구는 건너뜀).", "created": len(made)}


@router.post("/lessors/{lessor_id}/fee-plan", dependencies=ADMIN_ONLY)
def set_fee_plan(lessor_id: str, req: FeePlanReq):
    lessor = Lessor.require(lessor_id)
    if req.app_fee < 0 or req.app_fee > 10_000_000:
        raise DomainError("월 가맹비는 0원 이상 1,000만 원 이하로 입력해 주세요.")
    lessor.app_fee = int(req.app_fee)
    lessor.save(action="franchise_fee_plan_changed", app_source=APP_SRC)
    return {"success": True, "message": f"{lessor.name} 임대인의 월 가맹비를 {int(req.app_fee):,}원(부가세 포함)으로 정했습니다. 다음 청구부터 적용됩니다."}


@router.post("/fees/{fee_id}/paid", dependencies=ADMIN_ONLY)
def register_fee_paid(fee_id: str, req: FeePaidReq, request: Request):
    fee = fb.register_payment(fb.FranchiseFee.require(fee_id), req.amount, req.paid_date, req.method, req.memo, _admin_label(request))
    msg = f"{fee.billing_month} 가맹비 납부를 등록했습니다. 홈택스에서 전자세금계산서를 발행한 뒤 승인번호를 기록해 주세요."
    if et.auto_on_payment():        # 즉시 발급: 입금 등록과 함께 API 로 접수(실패해도 납부 등록은 유지)
        try:
            fee = fb.submit_invoice_api(fee, _admin_label(request))
            msg = f"{fee.billing_month} 가맹비 납부를 등록하고 전자세금계산서를 API 로 접수했습니다. 국세청 전송 결과는 곧 자동으로 반영됩니다."
        except DomainError as exc:
            msg = f"납부는 등록했지만 세금계산서 자동 발행은 되지 않았습니다: {exc.message if hasattr(exc, 'message') else exc} — [🧾 API로 발행] 또는 홈택스 발행 후 승인번호 기록으로 처리해 주세요."
            fee = fb.FranchiseFee.require(fee_id)
    return {"success": True, "message": msg, "fee": fb.public(fee)}


@router.post("/fees/{fee_id}/invoice-auto", dependencies=ADMIN_ONLY)
def issue_fee_invoice_api(fee_id: str, request: Request):
    """전자세금계산서를 API(볼타)로 발행 접수한다."""
    fee = fb.submit_invoice_api(fb.FranchiseFee.require(fee_id), _admin_label(request))
    return {
        "success": True,
        "message": "전자세금계산서 발행을 접수했습니다. 국세청 전송 결과는 잠시 뒤 자동 반영됩니다(발행 상태 확인으로 바로 확인할 수도 있습니다).",
        "fee": fb.public(fee),
    }


@router.post("/fees/{fee_id}/invoice-refresh", dependencies=ADMIN_ONLY)
def refresh_fee_invoice(fee_id: str):
    fee = fb.refresh_invoice(fb.FranchiseFee.require(fee_id))
    done = fee.invoice_status == "ISSUED"
    return {
        "success": True,
        "message": (
            "발행이 완료되어 승인번호를 기록했습니다."
            if done
            else "아직 국세청 전송 결과가 없습니다(테스트 키는 10~30초, 운영 키는 약 10분 걸립니다)."
        ),
        "fee": fb.public(fee),
    }


@router.post("/webhooks/etax")
async def etax_webhook(request: Request):
    """볼타 웹훅 수신(서명 검증 필수, 인증 토큰 없음). 성공 이벤트는 조회 API 로 승인번호를 다시 확인해 확정한다."""
    raw = await request.body()
    et.verify_webhook(dict(request.headers), raw)
    try:
        event = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise DomainError("웹훅 본문을 읽지 못했습니다.")
    result = fb.handle_webhook(event, request.headers.get("webhook-id", ""))
    return {"success": True, "result": result}


@router.get("/fees/{fee_id}/invoice-card", dependencies=ADMIN_ONLY)
def fee_invoice_card(fee_id: str):
    """홈택스에서 직접 발급할 때 입력할 값(공급자·공급받는자·품목·금액)과 비어 있는 필수 정보."""
    return {"success": True, "card": fb.invoice_card(fb.FranchiseFee.require(fee_id))}


@router.post("/fees/{fee_id}/cash-receipt", dependencies=ADMIN_ONLY)
def record_fee_cash_receipt(fee_id: str, req: FeeCashReq, request: Request):
    """홈택스에서 발급한 현금영수증 승인번호를 기록한다(사업자번호가 없는 임대인의 가맹비 등)."""
    fee = fb.record_cash_receipt(fb.FranchiseFee.require(fee_id), req.approval_no, req.issue_date, _admin_label(request))
    return {"success": True, "message": f"{fee.billing_month} 가맹비 현금영수증(승인번호 {fee.invoice_nts_no})을 기록했고 임대인 장부의 경비로 등록했습니다.", "fee": fb.public(fee)}


@router.post("/fees/{fee_id}/invoice", dependencies=ADMIN_ONLY)
def record_fee_invoice(fee_id: str, req: FeeInvoiceReq, request: Request):
    fee = fb.record_invoice(fb.FranchiseFee.require(fee_id), req.nts_confirm_no, req.issue_date, _admin_label(request))
    return {"success": True, "message": f"{fee.billing_month} 가맹비 세금계산서(승인번호 {fee.invoice_nts_no})를 기록했고 임대인 장부의 경비로 등록했습니다.", "fee": fb.public(fee)}


@router.post("/fees/{fee_id}/invoice-cancel", dependencies=ADMIN_ONLY)
def cancel_fee_invoice(fee_id: str, req: FeeInvoiceCancelReq, request: Request):
    fee = fb.cancel_invoice(fb.FranchiseFee.require(fee_id), req.memo, _admin_label(request))
    return {"success": True, "message": "세금계산서 기록을 취소하고 장부에 등록했던 경비도 지웠습니다. 홈택스에서 수정·재발행한 뒤 다시 기록해 주세요.", "fee": fb.public(fee)}


@router.get("/fees/export-hometax-csv", dependencies=ADMIN_ONLY)
def export_fee_hometax_csv(month: str = ""):
    """입금 확인 후 세금계산서 발행 전인 가맹비 목록(홈택스 일괄 발급용 참고 CSV)."""
    return Response(
        content=fb.hometax_csv(month).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="franchise_fee_hometax_{month or "all"}.csv"'},
    )
