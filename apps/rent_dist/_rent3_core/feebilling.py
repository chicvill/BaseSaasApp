"""
apps/rent_dist/_rent3_core/feebilling.py — 가맹비(월 플랫폼 이용료) 징수 통보 · 납부 등록 · 세금계산서 발행 기록 · 임대인 종합소득세 증빙.

흐름
 1) 자동 통보: 매월(청구월 1일부터) 가맹 승인(ACTIVE)·이용료(`Lessor.app_fee`, 부가세 포함 금액)가 있는 임대인마다 청구 1건을 만들고 지원 채팅으로 알린다(이미 있으면 다시 만들지 않는다).
    조회 때 함께 실행한다(`ensure_notices`) — 별도 스케줄러가 없어도 관리인·임대인 화면을 열면 처리된다.
 2) 납부 등록: 관리인이 입금을 확인해 등록한다(금액은 청구액과 같아야 한다). 등록하면 임대인에게 '납부 확인 · 세금계산서 발행 예정'이 간다.
 3) 세금계산서: **앱이 국세청에 직접 발행하지는 않는다.** 관리인이 홈택스(또는 연계 업체)에서 전자세금계산서를 발행한 뒤 국세청 승인번호(24자리)를 기록한다.
    발행 기록이 생기면 임대인 장부의 지출(경비)로 자동 등록되어(증빙: 세금계산서) 부가세 매입세액·종합소득세 경비 자료에 들어간다.
 4) 임대인은 자기 가맹비 청구·납부·세금계산서 내역을 보고, 연도별 증빙(CSV·인쇄)을 종합소득세 신고 자료로 낼 수 있다.
공급자(플랫폼) 정보는 환경변수 RENT3_PLATFORM_NAME / RENT3_PLATFORM_BIZ_NO / RENT3_PLATFORM_CEO 로 둔다.
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError
from . import support as sup
from .models import Building, Lessor, Outlay, date_today
from .pool import PoolObj, now_iso

DUE_DAY = 10                 # 납부 기한: 청구월 10일
EXPENSE_CATEGORY = "platform_fee"


class FranchiseFee(PoolObj):
    """임대인(가맹점) 월 가맹비 청구·납부·세금계산서 기록."""
    collection: ClassVar[str] = "ops"
    label: ClassVar[str] = "가맹비 청구"

    id: str = Field(default_factory=lambda: f"fee_{uuid4().hex[:8]}")
    branch: str = "main"
    id_lessor: str = ""
    lessor_name: str = ""
    billing_month: str = ""                  # YYYY-MM
    due_date: str = ""
    supply_amount: int = 0                   # 공급가액
    vat_amount: int = 0                      # 부가세
    total_amount: int = 0                    # 합계(청구액)
    status: str = "NOTICED"                  # NOTICED(통보·미납) / PAID(납부 등록) / CANCELLED(청구 취소)
    noticed_at: str = ""
    paid_at: str = ""                        # 입금일
    paid_amount: int = 0
    pay_method: str = ""
    pay_memo: str = ""
    registered_by: str = ""
    invoice_status: str = "NONE"             # NONE(미발행) / PENDING(API 접수·발행 중) / ISSUED(발행 완료) / FAILED(API 발행 실패)
    invoice_via: str = ""                    # manual(홈택스 발행 후 승인번호 기록) / bolta(API 연계)
    invoice_key: str = ""                    # API 접수번호(issuanceKey)
    invoice_client_ref: str = ""             # 요청 관리번호(시도마다 새로)
    invoice_attempts: int = 0
    invoice_error: str = ""
    invoice_requested_at: str = ""
    last_webhook_id: str = ""
    invoice_nts_no: str = ""                 # 국세청 전자세금계산서 승인번호 24자리
    invoice_issue_date: str = ""             # 작성(발행)일
    invoice_issued_at: str = ""              # 기록 시각
    invoice_by: str = ""
    outlay_id: str = ""                      # 임대인 장부 지출로 자동 등록한 건

    def summary(self) -> str:
        return f"가맹비 [{self.branch} {self.billing_month}] {self.status}/{self.invoice_status} {self.total_amount:,}원"

    @property
    def overdue(self) -> bool:
        return self.status == "NOTICED" and bool(self.due_date) and date_today().isoformat() > self.due_date


def platform_info() -> Dict[str, str]:
    return {"name": os.getenv("RENT3_PLATFORM_NAME", "Rent3 플랫폼"), "business_number": os.getenv("RENT3_PLATFORM_BIZ_NO", ""),
            "ceo": os.getenv("RENT3_PLATFORM_CEO", "")}


def split_vat(total: int) -> Dict[str, int]:
    """부가세 포함 합계 → 공급가액·부가세(10%)."""
    total = int(total)
    supply = round(total / 1.1)
    return {"supply": supply, "vat": total - supply, "total": total}


def month_key(today: Optional[date] = None) -> str:
    t = today or date_today()
    return f"{t.year}-{t.month:02d}"


def _fmt_won(n: int) -> str:
    return f"{int(n):,}원"


def _thread_notice(lsr: Lessor, body: str, category: str) -> None:
    sup.post_admin_message(lsr, "관리인", body, kind="message", category=category, sender_role="system")


def auto_enabled() -> bool:
    """화면을 열 때 저절로 청구·통보하는 기능은 환경변수 RENT3_FEE_AUTO=1 로 켠다(기본 꺼짐 — 월 가맹비 금액·공급자 사업자번호를 확인한 뒤 켠다).
    꺼져 있어도 관리인의 [이번 달 청구·통보 만들기]로 직접 만들 수 있다."""
    return os.getenv("RENT3_FEE_AUTO", "0").strip().lower() in ("1", "true", "yes", "on")


def ensure_notices(month: str = "", branch: str = "", force: bool = False) -> List[FranchiseFee]:
    """해당 월(기본: 이번 달)의 가맹비 청구를 아직 안 만든 임대인에게 만들고 통보한다. 이미 있으면 건너뛴다(여러 번 불러도 안전). 새로 만든 건만 돌려준다.
    force 가 아니면 자동 기능(RENT3_FEE_AUTO)이 켜져 있을 때만 만든다."""
    if not force and not auto_enabled():
        _mark_overdue()
        return []
    mk = month or month_key()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mk):
        raise DomainError("청구월은 YYYY-MM 형식이어야 합니다.")
    made: List[FranchiseFee] = []
    existing = {(f.id_lessor, f.billing_month) for f in FranchiseFee.all() if f.status != "CANCELLED"}
    for lsr in Lessor.all():
        if branch and lsr.branch != branch:
            continue
        if lsr.status != "ACTIVE" or int(lsr.app_fee or 0) <= 0 or (lsr.id, mk) in existing:
            continue
        sp = split_vat(lsr.app_fee)
        due = f"{mk}-{DUE_DAY:02d}"
        fee = FranchiseFee(branch=lsr.branch, id_lessor=lsr.id, lessor_name=lsr.name, billing_month=mk, due_date=due,
                           supply_amount=sp["supply"], vat_amount=sp["vat"], total_amount=sp["total"], noticed_at=now_iso()
                           ).save(action="franchise_fee_noticed", app_source="rent3_fee")
        _thread_notice(lsr, f"[💳 가맹비 청구 안내] {mk} 가맹비 {_fmt_won(sp['total'])}(공급가액 {_fmt_won(sp['supply'])} + 부가세 {_fmt_won(sp['vat'])})을 "
                       f"{due}까지 납부해 주세요. 입금이 확인되면 전자세금계산서를 발급해 드리며, 발급 내역은 ☰ 💳 가맹비 납부·세금계산서에서 확인하고 종합소득세 증빙으로 내실 수 있습니다.",
                       "가맹비 징수 통보")
        lsr.app_fee_status = "PENDING"
        lsr.save(action="franchise_fee_pending", app_source="rent3_fee")
        made.append(fee)
    _mark_overdue()
    return made


def _mark_overdue() -> None:
    """기한이 지난 미납은 상태 표시를 연체로."""
    for f in FranchiseFee.find(status="NOTICED"):
        if f.overdue:
            lsr = Lessor.get(f.id_lessor)
            if lsr is not None and lsr.app_fee_status != "OVERDUE":
                lsr.app_fee_status = "OVERDUE"
                lsr.save(action="franchise_fee_overdue", app_source="rent3_fee")


def register_payment(fee: FranchiseFee, amount: int, paid_date: str = "", method: str = "계좌이체", memo: str = "", by: str = "관리인") -> FranchiseFee:
    if fee.status == "CANCELLED":
        raise DomainError("취소된 청구입니다.")
    if fee.status == "PAID":
        raise DomainError("이미 납부가 등록된 청구입니다.")
    if int(amount) != int(fee.total_amount):
        raise DomainError(f"입금액({_fmt_won(amount)})이 청구액({_fmt_won(fee.total_amount)})과 다릅니다.", hint="청구액과 같은 금액만 등록할 수 있습니다. 차액이 있으면 먼저 확인해 주세요.")
    pd = (paid_date or date_today().isoformat()).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pd):
        raise DomainError("입금일은 YYYY-MM-DD 형식이어야 합니다.")
    fee.status, fee.paid_at, fee.paid_amount, fee.pay_method, fee.pay_memo, fee.registered_by = "PAID", pd, int(amount), (method or "").strip(), (memo or "").strip(), by
    fee.save(action="franchise_fee_paid", app_source="rent3_fee")
    lsr = Lessor.get(fee.id_lessor)
    if lsr is not None:
        lsr.app_fee_status, lsr.last_fee_paid_at = "PAID", pd
        lsr.save(action="franchise_fee_paid", app_source="rent3_fee")
        _thread_notice(lsr, f"[✅ 가맹비 납부 확인] {fee.billing_month} 가맹비 {_fmt_won(fee.total_amount)} 입금({pd})을 확인했습니다. 전자세금계산서를 발급하는 대로 다시 알려 드립니다.", "가맹비 납부 확인")
    return fee


def _norm_nts(no: str) -> str:
    d = re.sub(r"\D", "", str(no or ""))
    if len(d) != 24:
        raise DomainError("국세청 승인번호는 숫자 24자리여야 합니다.", hint="홈택스 전자세금계산서 조회 화면의 승인번호(예: 20261116-41000000-12345678)를 그대로 입력해 주세요.")
    return f"{d[:8]}-{d[8:16]}-{d[16:]}"


def record_invoice(fee: FranchiseFee, nts_no: str, issue_date: str = "", by: str = "관리인") -> FranchiseFee:
    """홈택스에서 발행한 전자세금계산서의 승인번호를 기록하고, 임대인 장부의 지출(경비)로 자동 등록한다."""
    if fee.status != "PAID":
        raise DomainError("입금이 등록된 가맹비만 세금계산서를 기록할 수 있습니다.")
    if fee.invoice_status == "ISSUED":
        raise DomainError("이미 세금계산서가 기록된 청구입니다.", hint="잘못 기록했다면 [발행 취소 기록] 후 다시 입력해 주세요.")
    if fee.invoice_status == "PENDING":
        raise DomainError("API로 발행 중인 건입니다.", hint="결과를 기다리거나 [발행 상태 확인]을 눌러 주세요. 직접 기록하면 이중 발행이 될 수 있습니다.")
    nts = _norm_nts(nts_no)
    idate = (issue_date or date_today().isoformat()).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", idate):
        raise DomainError("작성일은 YYYY-MM-DD 형식이어야 합니다.")
    return finalize_invoice(fee, nts, idate, by, "manual")


def finalize_invoice(fee: FranchiseFee, nts: str, idate: str, by: str, via: str) -> FranchiseFee:
    """승인번호가 확정된 발행 건을 기록하고 임대인 장부 경비로 등록한다(수동 기록·API 완료 공통)."""
    if any(f.id != fee.id and f.invoice_status == "ISSUED" and f.invoice_nts_no == nts for f in FranchiseFee.all()):
        raise DomainError("이미 다른 청구에 기록된 승인번호입니다.")
    lsr = Lessor.get(fee.id_lessor)
    bldg = Building.first(branch=fee.branch)
    p = platform_info()
    out = Outlay(branch=fee.branch, id_building=(bldg.id if bldg else ""), category=EXPENSE_CATEGORY, title=f"가맹비 {fee.billing_month}", expense_date=idate,
                 amount=fee.total_amount, vendor=p["name"], vendor_business_number=p["business_number"], vendor_tax_type="GENERAL",
                 memo=f"전자세금계산서 승인번호 {nts}", evidence_type="TAX_INVOICE", usage_scope="COMMON").save(action="franchise_fee_outlay", app_source="rent3_fee")
    fee.invoice_status, fee.invoice_nts_no, fee.invoice_issue_date, fee.invoice_issued_at, fee.invoice_by, fee.outlay_id = "ISSUED", nts, idate, now_iso(), by, out.id
    fee.invoice_via, fee.invoice_error = via, ''
    fee.save(action="franchise_fee_invoice_recorded", app_source="rent3_fee")
    if lsr is not None:
        _thread_notice(lsr, f"[🧾 세금계산서 발급 완료] {fee.billing_month} 가맹비 {_fmt_won(fee.total_amount)} 전자세금계산서(승인번호 {nts}, 작성일 {idate})를 발급했습니다. "
                       "장부의 경비로 자동 등록되었고, ☰ 💳 가맹비 납부·세금계산서에서 종합소득세 증빙을 받을 수 있습니다.", "가맹비 세금계산서")
    return fee


# ───────── 전자세금계산서 API(볼타) 연계 ─────────
def submit_invoice_api(fee: FranchiseFee, by: str = "관리인") -> FranchiseFee:
    """입금이 등록된 건의 전자세금계산서를 API 로 접수한다. 접수되면 '발행 중'(PENDING)이고, 결과는 웹훅 또는 상태 확인으로 확정한다."""
    from . import etaxinvoice as et
    if fee.status != "PAID":
        raise DomainError("입금이 등록된 가맹비만 발행할 수 있습니다.")
    if fee.invoice_status in ("ISSUED", "PENDING"):
        raise DomainError("이미 발행했거나 발행 중인 청구입니다.", hint="[발행 상태 확인]으로 결과를 확인해 주세요." if fee.invoice_status == "PENDING" else "")
    lsr = Lessor.get(fee.id_lessor)
    if lsr is None:
        raise DomainError("임대인을 찾을 수 없습니다.")
    bldg = Building.first(branch=fee.branch)
    try:
        res = et.issue(fee, lsr, bldg.building_name if bldg else "")
    except et._NetworkUncertain as exc:
        fee.invoice_status, fee.invoice_client_ref, fee.invoice_requested_at = "PENDING", exc.client_ref, now_iso()
        fee.invoice_error = "접수 여부 불확실 — 발행 상태 확인으로 확인하세요"
        fee.save(action="franchise_fee_invoice_uncertain", app_source="rent3_fee")
        raise
    except DomainError as exc:
        fee.invoice_status, fee.invoice_error = "FAILED", str(exc.message if hasattr(exc, "message") else exc)[:300]
        fee.save(action="franchise_fee_invoice_failed", app_source="rent3_fee")
        raise
    fee.invoice_status, fee.invoice_via, fee.invoice_key, fee.invoice_client_ref = "PENDING", "bolta", res["issuance_key"], res["client_ref"]
    fee.invoice_error, fee.invoice_requested_at, fee.invoice_by = "", now_iso(), by
    fee.save(action="franchise_fee_invoice_submitted", app_source="rent3_fee")
    return fee


def refresh_invoice(fee: FranchiseFee) -> FranchiseFee:
    """발행 중인 건의 결과를 조회 API 로 확인한다. 승인번호가 확인되면 확정(장부 경비 등록·임대인 알림), 아직이면 그대로 둔다."""
    from . import etaxinvoice as et
    if fee.invoice_status != "PENDING" or not fee.invoice_key:
        return fee
    got = et.retrieve(fee.invoice_key)
    if got is None:
        return fee
    nts = got["nts_no"]
    d = re.sub(r"\D", "", nts)
    nts = f"{d[:8]}-{d[8:16]}-{d[16:]}" if len(d) == 24 else nts
    return finalize_invoice(fee, nts, (got["issued_at"] or fee.paid_at or date_today().isoformat())[:10], fee.invoice_by or "API", "bolta")


def refresh_pending() -> int:
    """발행 중인 모든 건을 확인한다(관리인 목록을 열 때). 확정된 건수를 돌려준다."""
    from . import etaxinvoice as et
    if not et.enabled():
        return 0
    n = 0
    for f in FranchiseFee.find(invoice_status="PENDING"):
        if not f.invoice_key:
            continue
        try:
            if refresh_invoice(f).invoice_status == "ISSUED":
                n += 1
        except Exception:      # 일시적 오류는 다음 조회 때 다시
            continue
    return n


def handle_webhook(event: Dict[str, Any], webhook_id: str) -> str:
    """검증을 마친 웹훅 이벤트 처리. 성공 이벤트는 승인번호를 조회 API 로 다시 확인해 확정하고, 실패 이벤트는 실패로 기록한다. 같은 webhook-id 는 한 번만."""
    data = (event or {}).get("data") or {}
    key = str(data.get("issuanceKey") or "")
    fee = next((f for f in FranchiseFee.all() if key and f.invoice_key == key), None)
    if fee is None:
        return "unknown"
    if webhook_id and fee.last_webhook_id == webhook_id:
        return "duplicate"
    fee.last_webhook_id = webhook_id
    et = (event or {}).get("eventType", "")
    if et == "TAX_INVOICE_ISSUANCE_SUCCESS":
        fee.save(action="franchise_fee_webhook", app_source="rent3_fee")
        refresh_invoice(fee)
        return "issued"
    if et == "TAX_INVOICE_ISSUANCE_FAILURE":
        cause = data.get("cause") or {}
        fee.invoice_status, fee.invoice_error = "FAILED", f"{cause.get('code', '')} {cause.get('message', '')}".strip()[:300] or "발행 실패"
        fee.save(action="franchise_fee_invoice_failed", app_source="rent3_fee")
        lsr = Lessor.get(fee.id_lessor)
        if lsr is not None:
            _thread_notice(lsr, f"[⚠️ 세금계산서 발행 실패] {fee.billing_month} 가맹비 세금계산서 발행이 실패했습니다. 관리인이 확인 후 다시 발행합니다.", "가맹비 세금계산서")
        return "failed"
    fee.save(action="franchise_fee_webhook", app_source="rent3_fee")
    return "ignored"


def cancel_invoice(fee: FranchiseFee, memo: str = "", by: str = "관리인") -> FranchiseFee:
    """발행 기록 취소(잘못 기록했거나 홈택스에서 취소·수정 발행한 경우): 자동 등록한 지출도 지운다."""
    if fee.invoice_status != "ISSUED":
        raise DomainError("취소할 세금계산서 기록이 없습니다.")
    if not (memo or "").strip():
        raise DomainError("취소 사유를 적어 주세요.")
    if fee.outlay_id:
        out = Outlay.get(fee.outlay_id)
        if out is not None:
            out.delete(action="franchise_fee_outlay_removed", app_source="rent3_fee")
    fee.pay_memo = (fee.pay_memo + f" | 세금계산서 {fee.invoice_nts_no} 취소({by}): {memo.strip()}").strip(" |")
    fee.invoice_status, fee.invoice_nts_no, fee.invoice_issue_date, fee.outlay_id = "NONE", "", "", ""
    fee.invoice_key, fee.invoice_via, fee.invoice_error = "", "", ""
    fee.save(action="franchise_fee_invoice_cancelled", app_source="rent3_fee")
    return fee


def invoice_card(fee: FranchiseFee) -> Dict[str, Any]:
    """홈택스 수동 발급용 '발급 정보 카드' 값: 공급자(플랫폼) → 공급받는자(가맹 임대인), 품목·금액·작성일. 비어 있는 필수값은 missing 에 모은다."""
    from .billing import validate_kr_business_number
    lsr = Lessor.get(fee.id_lessor)
    bldg = Building.first(branch=fee.branch)
    p = platform_info()
    sup = {"business_number": p["business_number"], "company_name": p["name"], "ceo_name": p["ceo"], "address": os.getenv("RENT3_PLATFORM_ADDRESS", "").strip(),
           "biz_type": os.getenv("RENT3_PLATFORM_BIZ_TYPE", "").strip(), "biz_item": os.getenv("RENT3_PLATFORM_BIZ_ITEM", "").strip(), "email": os.getenv("RENT3_PLATFORM_EMAIL", "").strip()}
    buy = {"business_number": (lsr.business_number if lsr else ""), "company_name": ((bldg.biz_company_name or bldg.building_name) if bldg else (lsr.name if lsr else "")),
           "ceo_name": (lsr.name if lsr else fee.lessor_name), "address": (bldg.address if bldg else ""), "biz_type": (bldg.biz_type if bldg else ""),
           "biz_item": (bldg.biz_item if bldg else ""), "email": (lsr.email if lsr else "")}
    missing = []
    if not validate_kr_business_number(sup["business_number"], strict_checksum=True)["valid"]:
        missing.append("공급자 사업자번호(서버 RENT3_PLATFORM_BIZ_NO)")
    if not sup["company_name"] or not sup["ceo_name"]:
        missing.append("공급자 상호·대표자")
    if not validate_kr_business_number(buy["business_number"], strict_checksum=True)["valid"]:
        missing.append("공급받는자 사업자번호(임대인 사업자등록증 승인 필요)")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", buy["email"] or ""):
        missing.append("공급받는자 이메일(임대인 정보)")
    wd = fee.paid_at or f"{fee.billing_month}-01"
    return {"title": f"가맹비 {fee.billing_month} 세금계산서 ({fee.lessor_name})", "write_date": wd, "purpose": "영수", "tax_type": "과세",
            "supplier": sup, "buyer": buy, "items": [{"name": f"{fee.billing_month} 플랫폼 가맹비", "supply": int(fee.supply_amount), "tax": int(fee.vat_amount)}],
            "note": "", "missing": missing, "total": int(fee.total_amount)}


def public(f: FranchiseFee) -> Dict[str, Any]:
    d = f.model_dump()
    d["overdue"] = f.overdue
    d["state_label"] = ("취소" if f.status == "CANCELLED" else ("납부 완료" if f.status == "PAID" else ("연체" if f.overdue else "납부 대기")))
    d["invoice_label"] = {"NONE": ("발행 대기" if f.status == "PAID" else "-"), "PENDING": "발행 중(국세청 전송 대기)", "ISSUED": "발행 완료", "FAILED": "발행 실패", "CANCELLED": "취소"}.get(f.invoice_status, f.invoice_status)
    return d


def evidence(lessor_id: str, year: int) -> Dict[str, Any]:
    """임대인의 연도별 가맹비 증빙: 세금계산서가 발행된 건(경비 증빙)과 납부했지만 아직 발행 전인 건을 나눠 합계를 낸다."""
    rows = sorted((f for f in FranchiseFee.find(id_lessor=lessor_id) if f.status == "PAID" and (f.paid_at or "").startswith(str(year))), key=lambda f: f.billing_month)
    issued = [public(f) for f in rows if f.invoice_status == "ISSUED"]
    pending = [public(f) for f in rows if f.invoice_status != "ISSUED"]
    s = lambda xs, k: sum(int(x[k]) for x in xs)
    return {"year": year, "platform": platform_info(), "issued": issued, "pending": pending,
            "totals": {"issued_total": s(issued, "total_amount"), "issued_supply": s(issued, "supply_amount"), "issued_vat": s(issued, "vat_amount"),
                       "pending_total": s(pending, "total_amount"), "count_issued": len(issued), "count_pending": len(pending)}}


def evidence_csv(lessor: Lessor, year: int) -> str:
    ev = evidence(lessor.id, year)
    p = ev["platform"]
    lines = ["﻿구분,청구월,입금일,작성일,공급가액,부가세,합계,국세청 승인번호,공급자 상호,공급자 사업자번호,공급받는자,공급받는자 사업자번호"]
    for kind, rows in (("세금계산서 발행", ev["issued"]), ("발행 대기(증빙 아님)", ev["pending"])):
        for r in rows:
            lines.append(",".join(str(x) for x in [kind, r["billing_month"], r["paid_at"], r["invoice_issue_date"], r["supply_amount"], r["vat_amount"], r["total_amount"],
                                                  r["invoice_nts_no"], p["name"], p["business_number"], lessor.name, lessor.business_number]))
    t = ev["totals"]
    lines.append(f"합계(발행분),,,,{t['issued_supply']},{t['issued_vat']},{t['issued_total']},,,,,")
    return "\n".join(lines) + "\n"


def hometax_csv(month: str) -> str:
    """입금은 확인됐지만 세금계산서가 아직 없는 건의 일괄 발행용 목록(공급받는자는 승인된 사업자번호가 있어야 한다)."""
    p = platform_info()
    lines = ["﻿작성일자,공급자 사업자번호,공급받는자 사업자번호,공급받는자 상호,공급받는자 대표자,공급가액,세액,합계,품목,비고"]
    for f in sorted(FranchiseFee.find(status="PAID"), key=lambda x: (x.billing_month, x.branch)):
        if f.invoice_status in ("ISSUED", "PENDING") or (month and f.billing_month != month):
            continue
        lsr = Lessor.get(f.id_lessor)
        biz = (lsr.business_number if lsr else "") or "(사업자번호 미승인)"
        lines.append(",".join(str(x) for x in [f.paid_at, p["business_number"], biz, lsr.name if lsr else f.lessor_name, lsr.name if lsr else "",
                                              f.supply_amount, f.vat_amount, f.total_amount, f"{f.billing_month} 플랫폼 가맹비", f.id]))
    return "\n".join(lines) + "\n"
