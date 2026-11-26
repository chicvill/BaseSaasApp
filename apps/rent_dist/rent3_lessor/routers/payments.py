"""
apps/rent_dist/rent3_lessor/routers/payments.py — FIFO 수납 · 문자 수납 · 납부 정정/무효 · CSV 입출력 · 이체 알림 · 입금 메일 정리.
"""
from datetime import date
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.errors import DomainError, Forbidden
from apps.rent_dist._rent3_core import (
    Contract,
    FifoPaymentEngine,
    Pay,
    Room,
    TaxAndCertificateService,
    ensure_pool_seeded,
    mailpay,
    normalize_branch_id,
)
from apps.rent_dist._rent3_core.authz import claims_from
from apps.rent_dist._rent3_core.billing import SmsDepositParser
from apps.rent_dist._rent3_core.ops import TransferNotice
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import APP_SRC, _my_lessor, _pin_pay_gate, _pin_pay_record

router = APIRouter()


class CollectPaymentReq(BaseModel):
    branch: str = "main"
    room_no: str
    amount: int
    payer_name: str = ""
    paid_date: Optional[str] = None
    method: str = "TRANSFER"
    up_to_ym: Optional[str] = None
    target_ym: Optional[str] = None  # 채울 달(미납으로 표시된 달). 비우면 가장 오래된 미납 달부터
    target_months: Optional[List[str]] = None   # 여러 달을 체크해 수납할 때(체크한 달의 미납 합계 이하만 받는다)


class SmsPasteReq(BaseModel):
    branch: str = "main"
    sms_text: str
    up_to_ym: Optional[str] = None
    force: bool = False   # 장부와 어긋난 문자를 확인한 뒤 '입금자명 기준'으로 그대로 반영
    target_months: Optional[List[str]] = None   # 체크박스로 지정한 귀속월(YYYY-MM) 목록
    pick_if_multi: bool = False                 # 미납 달이 2개 이상이고 일부 입금이면 저장 전 달 선택 요청


class PayEditReq(BaseModel):
    branch: str = "main"
    pay_id: str
    paid_date: str = ""
    rent_amount: int = 0
    maintenance_amount: int = 0
    billing_month: str = ""          # 비우면 그대로, 값이 있으면 그 달로 옮긴다(그 달의 빈 자리가 충분해야 한다)


class PayDeleteReq(BaseModel):
    branch: str = "main"
    pay_id: str
    reason: str = ""                 # 무효 처리 사유(화면은 반드시 묻는다)


class LedgerCsvReq(BaseModel):
    branch: str = "main"
    year: int = 0
    csv_text: str
    replace_diffs: bool = False


class TransferConfirmReq(BaseModel):
    branch: str = "main"
    amount: Optional[int] = None          # 실제 입금액이 다르면 고쳐서 수납
    paid_date: Optional[str] = None


class TransferRejectReq(BaseModel):
    branch: str = "main"
    reason: str = ""


class MailAccount(BaseModel):
    bank: str = ""
    tail: str = ""


class MailEnableReq(BaseModel):
    branch: str = "main"
    consent: bool = False
    bank: str = ""                           # 예전 방식(한 계좌). accounts 가 있으면 무시
    acct_tail: str = ""
    accounts: Optional[List[MailAccount]] = None
    auto: bool = False


class MailAccountsReq(BaseModel):
    branch: str = "main"
    accounts: List[MailAccount]


class MailBranchReq(BaseModel):
    branch: str = "main"


class MailApplyReq(BaseModel):
    branch: str = "main"
    room_no: str = ""
    target_months: Optional[List[str]] = None


def _mail_on() -> None:
    if not mailpay.feature_enabled():
        raise DomainError(
            "입금 메일 자동 정리는 아직 준비 중입니다.",
            hint="준비가 끝나면 안내해 드립니다. 입금 문자 붙여넣기는 그대로 쓰실 수 있습니다.",
        )


def _notice_of(notice_id: str, branch: str):
    n = TransferNotice.get(notice_id)
    if n is None or normalize_branch_id(n.branch) != normalize_branch_id(branch):
        raise DomainError("이체 확인 요청을 찾을 수 없습니다.")
    if n.status != "PENDING":
        raise DomainError("이미 처리한 요청입니다.")
    return n


@router.post("/payments/collect")
def collect_payment(req: CollectPaymentReq, request: Request):
    ensure_pool_seeded()
    pc = _pin_pay_gate(request, req.amount)
    res = FifoPaymentEngine.collect(
        branch=req.branch,
        room_no=req.room_no,
        amount=req.amount,
        payer_name=req.payer_name,
        paid_date=req.paid_date,
        method=req.method,
        up_to_ym=req.up_to_ym,
        app_source="rent3_lessor_pin" if pc else APP_SRC,
        target_ym=req.target_ym,
        target_months=req.target_months,
    )
    _pin_pay_record(pc, res)
    return {"success": True, **res}


@router.post("/payments/sms-paste")
def paste_sms_payment(req: SmsPasteReq, request: Request):
    ensure_pool_seeded()
    pc = _pin_pay_gate(request, SmsDepositParser.parse(req.sms_text)["amount"]) if (claims_from(request) or {}).get("m") == "pin" else None
    res = FifoPaymentEngine.parse_and_collect_sms(
        branch=req.branch,
        sms_text=req.sms_text,
        up_to_ym=req.up_to_ym,
        app_source="rent3_lessor_pin" if pc else APP_SRC,
        force=req.force,
        target_months=req.target_months,
        pick_if_multi=req.pick_if_multi,
    )
    _pin_pay_record(pc, res)
    return {**res, "success": not res.get("needs_confirm")}


@router.get("/payments/ledger-state")
def payment_ledger_state(branch: str = "main", room_no: str = ""):
    """한 호실의 수납 원장 상태(저장된 납부 건·적힌 청구월·상계 결과·선수금)와 이상 징후(같은 달 여러 건·선수금·미납인데 납부 있는 달)를 보여 준다."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    room = Room.first(branch=b_id, room_no=room_no)
    ct = Contract.get(room.active_contract_id) if (room and room.active_contract_id) else None
    if ct is None:
        raise DomainError(f"{room_no}호에 활성 계약이 없습니다.")
    lg = ct.ledger()
    pays = sorted(
        (x for x in Pay.find(id_contract=ct.id) if x.payment_kind == "MONTHLY_RENT"),
        key=lambda x: (x.billing_month, x.paid_date, x.created_at),
    )

    def source(p):
        k = p.idempotency_key or ""
        if k.startswith("import:") or TaxAndCertificateService.is_carryover_payment(p):
            return "장부 이관"
        if k.startswith("csv:"):
            return "엑셀 올리기"
        if k.startswith("seed:"):
            return "시험 데이터"
        return "문자 수납" if p.method == "BANK_SMS" else "앱에서 수납"

    by_ym: Dict[str, List[Any]] = {}
    for p in pays:
        by_ym.setdefault((p.billing_month or "")[:7], []).append(p)
    months = []
    for m in lg["months"]:
        ps = by_ym.get(m["ym"], [])
        months.append({
            "ym": m["ym"],
            "due": m["due"],
            "paid": m["paid"],
            "unpaid": m["unpaid"],
            "status": m["status"],
            "labelled_total": sum(x.total_amount for x in ps),
            "pay_ids": [x.id for x in ps],
        })
    anomalies = []
    for ym, ps in by_ym.items():
        due = next((m["due"] for m in lg["months"] if m["ym"] == ym), 0)
        if len(ps) > 1 and sum(x.total_amount for x in ps) > due > 0:
            anomalies.append({
                "kind": "SAME_MONTH_OVER",
                "ym": ym,
                "msg": f"{ym}월에 납부가 {len(ps)}건(합계 {sum(x.total_amount for x in ps):,}원)으로 청구액 {due:,}원보다 많습니다. 중복 수납이면 한 건을 삭제하세요.",
                "pay_ids": [x.id for x in ps],
            })
    if lg["advance_credit"] > 0:
        anomalies.append({
            "kind": "ADVANCE_CREDIT",
            "ym": "",
            "msg": f"선수금 {lg['advance_credit']:,}원이 쌓여 있습니다(밀린 청구보다 많이 받은 금액). 중복 수납이 아닌지 확인하세요.",
            "pay_ids": [],
        })
    voided = sorted(
        (x for x in Pay.find(id_contract=ct.id, include_voided=True) if x.status == "VOIDED" and x.payment_kind == "MONTHLY_RENT"),
        key=lambda x: x.voided_at,
        reverse=True,
    )[:30]
    return {
        "success": True,
        "room_no": room.room_no,
        "tenant_name": ct.tenant_name,
        "total_due": lg["total_due"],
        "voided": [
            {
                "id": x.id,
                "billing_month": x.billing_month,
                "paid_date": x.paid_date,
                "total": x.total_amount,
                "reason": x.void_reason,
                "voided_at": x.voided_at,
                "voided_by": x.voided_by,
            }
            for x in voided
        ],
        "total_paid": lg["total_paid"],
        "total_unpaid": lg["total_unpaid"],
        "advance_credit": lg["advance_credit"],
        "months": months,
        "anomalies": anomalies,
        "payments": [
            {
                "id": p.id,
                "billing_month": p.billing_month,
                "paid_date": p.paid_date,
                "total": p.total_amount,
                "source": source(p),
                "created_at": p.created_at,
                "allocated_months": [a.get("ym") for a in (p.allocated_months or [])],
                "group": p.deposit_group_id,
            }
            for p in pays
        ],
    }


@router.post("/payments/edit")
def edit_payment(req: PayEditReq):
    """수납 내역 한 건의 납부일·임차료·관리비를 고친다 (장부 이관 오류 정정용)."""
    ensure_pool_seeded()
    pay = Pay.require(req.pay_id)
    if normalize_branch_id(pay.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 수납 내역은 고칠 수 없습니다.")
    try:
        d = date.fromisoformat(req.paid_date)
    except ValueError:
        raise DomainError("납부일 형식이 올바르지 않습니다 (예: 2026-03-15).")
    if req.rent_amount < 0 or req.maintenance_amount < 0 or req.rent_amount + req.maintenance_amount <= 0:
        raise DomainError("금액은 0원보다 커야 합니다.")
    new_total = req.rent_amount + req.maintenance_amount + pay.vat_amount
    old_ym = (pay.billing_month or "")[:7]
    new_ym = (req.billing_month or old_ym).strip()[:7]
    moving = bool(req.billing_month) and new_ym != old_ym
    ct = Contract.get(pay.id_contract) if pay.id_contract else None
    legacy = TaxAndCertificateService.is_carryover_payment(pay)
    if moving and (legacy or ct is None):
        raise DomainError(
            "이 납부는 여러 달분을 합친 이월 건이라 달을 옮길 수 없습니다.",
            hint="이월 합산 건은 관리자가 달별로 나누는 정리를 거친 뒤에 옮길 수 있습니다.",
        )
    # 선수금 방지: 그 달 청구액을 넘게 고치거나, 자리가 부족한 달로 옮길 수 없다(날짜만 고치거나 금액을 줄이는 것은 허용)
    if ct is not None and not legacy and (moving or new_total > pay.total_amount):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", new_ym):
            raise DomainError("옮길 달은 YYYY-MM 형식이어야 합니다 (예: 2026-09).")
        row = next((m for m in ct.ledger()["months"] if m["ym"] == new_ym), None)
        if row is None or int(row["due"]) <= 0:
            raise DomainError(
                f"{new_ym}월은 청구 대상 달이 아닙니다.",
                hint="청구가 있는 달(미납으로 표시될 수 있는 달)만 고를 수 있습니다.",
                code="NOT_DUE_MONTH",
            )
        others = sum(
            p.total_amount
            for p in Pay.find(id_contract=ct.id)
            if p.id != pay.id
            and p.payment_kind == "MONTHLY_RENT"
            and (p.billing_month or "")[:7] == new_ym
            and not TaxAndCertificateService.is_carryover_payment(p)
        )
        room_left = int(row["due"]) - others
        if new_total > room_left:
            raise DomainError(
                f"{new_ym}월에는 {max(room_left, 0):,}원까지만 넣을 수 있습니다(청구액 {int(row['due']):,}원, 이미 낸 {others:,}원). 선수금은 받지 않습니다.",
                hint="금액을 줄이거나 비어 있는 다른 달을 골라 주세요.",
                code="OVER_MONTH",
            )
    pay.paid_date = d.isoformat()
    pay.rent_amount = req.rent_amount
    pay.maintenance_amount = req.maintenance_amount
    pay.total_amount = new_total
    if moving:
        pay.billing_month = new_ym
    pay.allocated_months = [{"ym": (pay.billing_month or "")[:7], "allocated": new_total, "cleared": False}] if not legacy else pay.allocated_months
    pay.save(action="payment_month_moved" if moving else "payment_edited", app_source=APP_SRC)
    msg = (
        f"{pay.room_no}호 수납 내역을 고쳤습니다 ({pay.paid_date}, {pay.total_amount:,}원)."
        if not moving
        else f"{pay.room_no}호 납부를 {old_ym}월에서 {new_ym}월로 옮겼습니다 ({pay.total_amount:,}원)."
    )
    return {"success": True, "message": msg, "payment": pay.model_dump()}


@router.post("/payments/delete")
def delete_payment(req: PayDeleteReq, request: Request):
    """수납 내역 한 건을 **무효 처리**한다(오입력·중복·테스트 정정용). 기록은 사유·시각과 함께 남지만 원장·세금 계산·중복 점검에서는 빠지므로,
    그 달은 원래대로 미납이 되고 선수금이 쌓이지 않는다. 같은 입금에서 나뉜 다른 달 건은 건드리지 않는다."""
    ensure_pool_seeded()
    pay = Pay.require(req.pay_id)
    if normalize_branch_id(pay.branch) != normalize_branch_id(req.branch):
        raise Forbidden("다른 지점의 수납 내역은 지울 수 없습니다.")
    room_no, p_date, amt, ym = pay.room_no, pay.paid_date, pay.total_amount, pay.billing_month
    siblings = [x for x in Pay.find(id_contract=pay.id_contract) if pay.deposit_group_id and x.deposit_group_id == pay.deposit_group_id and x.id != pay.id]
    pay.status, pay.void_reason = "VOIDED", (req.reason or "").strip() or "사유 미기재"
    pay.voided_at, pay.voided_by = now_iso(), str((claims_from(request) or {}).get("u") or "")
    pay.save(action="payment_voided", app_source=APP_SRC)
    extra = f" 같은 입금에서 나뉜 다른 달 {len(siblings)}건은 그대로입니다." if siblings else ""
    return {
        "success": True,
        "message": f"{room_no}호 {ym}월 수납 내역({p_date}, {amt:,}원)을 무효 처리했습니다(사유: {pay.void_reason}). 그 달은 미납으로 돌아갑니다.{extra}",
    }


# ── 납부현황 엑셀(CSV) 내려받기 · 올리기 (미리보기 → 확인 → 반영) ──
@router.get("/ledger/export-csv")
def export_ledger_csv(branch: str = "main", year: Optional[int] = None):
    """호실×월 납부 현황을 엑셀로 열리는 CSV 로 내려받는다(백업·엑셀 편집용). 입주자 이름이 들어 있다."""
    from apps.rent_dist._rent3_core import ledger_csv
    ensure_pool_seeded()
    b_id = normalize_branch_id(branch)
    yr = year or date.today().year
    return Response(
        content=ledger_csv.export_csv(b_id, yr).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=ledger_{b_id}_{yr}.csv; filename*=UTF-8''{quote(f'납부현황_{b_id}_{yr}.csv')}"},
    )


@router.post("/ledger/import/preview")
def preview_ledger_csv(req: LedgerCsvReq):
    """올린 CSV 를 읽어 '이렇게 기록하겠습니다'를 보여 준다(저장하지 않음)."""
    from apps.rent_dist._rent3_core import ledger_csv
    ensure_pool_seeded()
    try:
        return ledger_csv.plan(req.branch, req.year or date.today().year, req.csv_text)
    except ValueError as e:
        raise DomainError(str(e))


@router.post("/ledger/import/apply")
def apply_ledger_csv(req: LedgerCsvReq):
    """미리보기에서 확인한 내용을 반영한다. 반영 전에 풀 전체를 자동 백업한다. 같은 파일을 다시 올려도 중복되지 않는다."""
    from apps.rent_dist._rent3_core import ledger_csv
    ensure_pool_seeded()
    try:
        return ledger_csv.apply(req.branch, req.year or date.today().year, req.csv_text, req.replace_diffs)
    except ValueError as e:
        raise DomainError(str(e))


@router.post("/transfer-notices/{notice_id}/confirm")
def confirm_transfer_notice(notice_id: str, req: TransferConfirmReq, request: Request):
    """임대인이 통장에서 입금을 확인한 뒤 수납으로 반영한다(선입선출 수납)."""
    ensure_pool_seeded()
    n = _notice_of(notice_id, req.branch)
    pc = _pin_pay_gate(request, int(req.amount or n.amount))
    res = FifoPaymentEngine.collect(
        branch=n.branch,
        room_no=n.room_no,
        amount=int(req.amount or n.amount),
        payer_name=n.tenant_name,
        paid_date=req.paid_date,
        method="TRANSFER",
        app_source="rent3_lessor_pin" if pc else APP_SRC,
    )
    _pin_pay_record(pc, res)
    n.status, n.handled_at, n.handled_by = "CONFIRMED", now_iso(), str((claims_from(request) or {}).get("u") or "lessor")
    n.save(action="transfer_notice_confirmed", app_source=APP_SRC)
    return {"success": True, **res}


@router.post("/transfer-notices/{notice_id}/reject")
def reject_transfer_notice(notice_id: str, req: TransferRejectReq, request: Request):
    ensure_pool_seeded()
    n = _notice_of(notice_id, req.branch)
    n.status, n.handled_at, n.reject_reason = "REJECTED", now_iso(), (req.reason or "").strip()[:100]
    n.handled_by = str((claims_from(request) or {}).get("u") or "lessor")
    n.save(action="transfer_notice_rejected", app_source=APP_SRC)
    return {"success": True, "message": "입금 확인이 되지 않아 반려했습니다. 임차인 화면에 표시됩니다."}


# ── 입금 메일 자동 정리(선택, 기본 꺼짐): 동의·설정·확인 대기 처리. 수신은 /api/rent3/inbound/mail (서명 웹훅) ──
@router.get("/mail/settings")
def mail_settings(branch: str = "main"):
    return {"success": True, **mailpay.settings_view(_my_lessor(branch))}


@router.post("/mail/enable")
def mail_enable(req: MailEnableReq):
    _mail_on()
    lsr = mailpay.enable(
        _my_lessor(req.branch),
        req.consent,
        req.bank,
        req.acct_tail,
        req.auto,
        accounts=[a.model_dump() for a in req.accounts] if req.accounts else None,
    )
    return {"success": True, **mailpay.settings_view(lsr)}


@router.post("/mail/accounts")
def mail_accounts(req: MailAccountsReq):
    _mail_on()
    lsr = mailpay.set_accounts(_my_lessor(req.branch), [a.model_dump() for a in req.accounts])
    return {"success": True, **mailpay.settings_view(lsr)}


@router.post("/mail/rotate-token")
def mail_rotate(req: MailBranchReq):
    _mail_on()
    lsr = mailpay.rotate_token(_my_lessor(req.branch))
    return {"success": True, **mailpay.settings_view(lsr)}


@router.post("/mail/disable")
def mail_disable(req: MailBranchReq):
    lsr = _my_lessor(req.branch)
    n = mailpay.disable(lsr)
    return {"success": True, "deleted": n, **mailpay.settings_view(lsr)}


@router.get("/mail/intakes")
def mail_intakes(branch: str = "main"):
    _mail_on()
    return {"success": True, "items": mailpay.list_intakes(_my_lessor(branch))}


@router.post("/mail/forward-code/clear")
def mail_forward_code_clear(req: MailBranchReq):
    _mail_on()
    lsr = _my_lessor(req.branch)
    mailpay.clear_forward_code(lsr)
    return {"success": True, **mailpay.settings_view(lsr)}


@router.post("/mail/{intake_id}/apply")
def mail_apply(intake_id: str, req: MailApplyReq):
    _mail_on()
    return mailpay.apply_pending(_my_lessor(req.branch), intake_id, req.room_no, req.target_months)


@router.post("/mail/{intake_id}/dismiss")
def mail_dismiss(intake_id: str, req: MailBranchReq):
    _mail_on()
    mailpay.dismiss(_my_lessor(req.branch), intake_id)
    return {"success": True}
