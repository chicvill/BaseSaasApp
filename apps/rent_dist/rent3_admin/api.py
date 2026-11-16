"""
apps/rent3_admin/api.py — 플랫폼 총괄 관리인 전용 API 라우터.
"""
from typing import Any, Dict, List, Optional
import re
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.auth import require_role

from core.errors import DomainError
from apps.rent_dist._rent3_core.pool import now_iso
from apps.rent_dist._rent3_core import franchise as fr, consent as cs, support as sup
from apps.rent_dist._rent3_core.authz import api_headers, not_revoked
from apps.rent_dist._rent3_core import (
    Admin,
    Building,
    Contract,
    Lessor,
    POOL_COLLECTIONS,
    Pay,
    Room,
    RtmsReport,
    TaxAndCertificateService,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter(dependencies=[Depends(api_headers)])
APP_SRC = "rent3_admin"
# 가맹 심사·정산은 본사 관리자만 (토큰 필요). 지점 관리자(branch_admin)는 자기 지점 가맹을 스스로 승인할 수 없다.
ADMIN_ONLY = [Depends(not_revoked), Depends(require_role("superadmin", "admin"))]


class DecisionReq(BaseModel):
    decision: str                 # approve | reject | suspend
    note: str = ""
    force: bool = False           # 신청 서류가 미비해도 관리자 책임으로 승인(사유 필수·기록됨)


class RegistryVerifyReq(BaseModel):
    verified: bool = True
    issue_number: str = ""      # 관리자가 인터넷등기소에서 실제로 조회한 발급확인번호
    note: str = ""


class BizRegVerifyReq(BaseModel):
    verified: bool = True
    business_number: str = ""   # 관리자가 국세청 홈택스에서 조회·확인한 10자리 사업자등록번호
    hometax_status: str = "계속사업자"   # 계속사업자(일반/면세/간이) | 휴업자 | 폐업자
    note: str = ""


class RoomLimitReq(BaseModel):
    room_limit: int


class AccountResetReq(BaseModel):
    kind: str        # lessor | tenant
    id: str


class AiLimitReq(BaseModel):
    ai_monthly_limit: int


class LessorRegisterReq(BaseModel):
    username: str
    name: str
    phone: str
    account_number: str = ""
    business_number: str = ""
    email: str = ""
    branch: str
    building_name: str
    address: str = ""
    app_fee: int = 33000


@router.get("/status")
def get_status():
    ensure_pool_seeded()
    meta = pool().overview()
    return {
        "success": True,
        "app_id": "rent3_admin",
        "status": "HEALTHY",
        "pool_revision": meta["total_revision"],
        "total_objects": meta["total_objects"],
        "lessors": Lessor.count(),
        "buildings": Building.count(),
        "rooms": Room.count(),
    }


@router.get("/overview", dependencies=ADMIN_ONLY)
def get_admin_overview():
    ensure_pool_seeded()
    p = pool()
    meta = p.overview()

    lessors = Lessor.all()
    buildings = Building.all()
    rooms = Room.all()
    contracts = Contract.find(status="ACTIVE")
    rtms_list = RtmsReport.all()

    branch_summaries: List[Dict[str, Any]] = []
    portfolio_gross = 0
    portfolio_expense = 0
    portfolio_noi = 0

    for b in buildings:
        fin = TaxAndCertificateService.branch_financial_summary(b.branch)
        portfolio_gross += fin["gross_revenue"]
        portfolio_expense += fin["total_operating_expense"]
        portfolio_noi += fin["noi"]
        branch_summaries.append({
            **b.model_dump(),
            **fin,
        })

    monthly_saas_mrr = sum(l.app_fee for l in lessors if l.status == "ACTIVE")
    pending_fee_lessors = [l for l in lessors if l.app_fee_status != "PAID"]

    return {
        "success": True,
        "pool_meta": meta,
        "admins": [a.model_dump() for a in Admin.all()],
        "kpi": {
            "total_buildings": len(buildings),
            "total_rooms": len(rooms),
            "occupied_rooms": sum(1 for r in rooms if r.is_occupied),
            "active_contracts": len(contracts),
            "active_tenants": Tenant.count(status="ACTIVE"),
            "monthly_saas_mrr": monthly_saas_mrr,
            "pending_fee_count": len(pending_fee_lessors),
            "portfolio_gross_revenue": portfolio_gross,
            "portfolio_operating_expense": portfolio_expense,
            "portfolio_noi": portfolio_noi,
        },
        "lessors": [
            {**{k: v for k, v in l.safe_dump().items() if k not in ("identity", "franchise_docs", "password")},
             "application": fr.application_summary(l, Building.first(branch=l.branch)),
             "room_count": Room.count(branch=l.branch),
             "published_count": len([r for r in Room.find(branch=l.branch) if r.is_published])}
            for l in lessors
        ],
        "branches": branch_summaries,
        "rtms_reports": [
            {**r.model_dump(), "days_left": r.days_left()}
            for r in sorted(rtms_list, key=lambda x: x.updated_at, reverse=True)
        ],
        "recent_events": p.recent_events(limit=40),
    }


@router.post("/lessors", dependencies=ADMIN_ONLY)
def register_lessor_and_building(req: LessorRegisterReq):
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    lessor = Lessor(
        username=req.username.strip(),
        name=req.name.strip(),
        phone=req.phone.strip(),
        account_number=req.account_number.strip(),
        business_number=req.business_number.strip(),
        email=req.email.strip(),
        branch=b_id,
        app_fee=req.app_fee,
        app_fee_status="PENDING",
        status="ACTIVE",
    ).save(action="lessor_onboarded", app_source=APP_SRC)

    bldg = Building.first(branch=b_id)
    if not bldg:
        bldg = Building(
            id=f"bldg_{b_id}",
            branch=b_id,
            building_name=req.building_name.strip(),
            address=req.address.strip(),
            id_lessor=lessor.id,
        ).save(action="building_onboarded", app_source=APP_SRC)

    return {
        "success": True,
        "message": f"임대인 '{lessor.name}' 및 건물 '{bldg.building_name}'이 공동 JSON Data Pool(users.json, buildings.json)에 등재되었습니다.",
        "lessor": lessor.safe_dump(),
        "building": bldg.model_dump(),
    }


@router.post("/lessors/{lessor_id}/approve", dependencies=ADMIN_ONLY)
def approve_lessor(lessor_id: str):
    lessor = Lessor.require(lessor_id)
    lessor.approve(app_source=APP_SRC)
    return {"success": True, "message": f"임대인 '{lessor.name}' 가맹 승인이 완료되었습니다.", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/decision", dependencies=ADMIN_ONLY)
def decide_lessor(lessor_id: str, req: DecisionReq):
    """가맹 심사 결과: 승인 / 반려 / 정지. 반려·정지는 사유 필수, 해당 지점의 공개 매물이 모두 내려간다."""
    lessor = Lessor.require(lessor_id)
    if req.decision == "approve" and lessor.status == "PENDING_APPROVAL":
        summ = fr.application_summary(lessor, Building.first(branch=lessor.branch))
        ver = (summ["registry"] or {}).get("verification") or {}
        if not (summ["steps"]["submitted"] and summ["ready_to_submit"] and ver.get("verified")):
            if not req.force:
                raise DomainError(
                    "가맹 신청이 완료되지 않았거나 등기부 원본 대조(발급확인번호)가 기록되지 않았습니다.",
                    hint="인터넷등기소에서 발급확인번호를 조회해 '원본 대조 완료'를 기록하세요. 건너뛰려면 '강제 승인'과 사유가 필요합니다(기록됨).",
                )
            if not req.note.strip():
                raise DomainError("서류 미비 상태로 승인하려면 사유를 입력해 주세요.")
    lessor.decide(req.decision, req.note, app_source=APP_SRC)
    label = Lessor.STATUS_LABELS.get(lessor.status, lessor.status)
    return {"success": True, "message": f"임대인 '{lessor.name}' → {label}", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/registry-verify")
def verify_franchise_registry(lessor_id: str, req: RegistryVerifyReq, admin=Depends(require_role("superadmin", "admin")), _nr=Depends(not_revoked)):
    """관리자가 인터넷등기소에서 발급확인번호로 원본을 대조했음을 기록한다 (누가·언제·어떤 번호로). 승인의 근거가 된다."""
    lessor = Lessor.require(lessor_id)
    docs = lessor.franchise_docs or {}
    if not docs.get("registry_file"):
        raise DomainError("제출된 등기부가 없습니다.")
    num = req.issue_number.strip()
    if req.verified and not num:
        raise DomainError("조회한 발급확인번호를 입력해 주세요.", hint="서류에 적힌 번호를 인터넷등기소에서 조회한 뒤 기록합니다.")
    if req.verified and not req.note.strip():
        raise DomainError("대조 결과 메모를 남겨 주세요.", hint="예: 소유자·소재지·발급일시 모두 제출 서류와 일치")
    ai_num = ((docs.get("ai") or {}).get("extracted") or {}).get("issue_number") or ""
    lessor.franchise_docs = {**docs, "admin_verification": {
        "verified": bool(req.verified),
        "issue_number": num,
        "matches_ai_number": bool(ai_num) and re.sub(r"\D", "", ai_num) == re.sub(r"\D", "", num),
        "note": req.note.strip(),
        "verified_by": getattr(admin, "username", ""),
        "verified_at": now_iso(),
        "file_sha256": docs.get("registry_sha256", ""),
    }}
    lessor.save(action="franchise_registry_verified" if req.verified else "franchise_registry_rejected", app_source=APP_SRC)
    return {"success": True, "message": "등기부 원본 대조 결과를 기록했습니다." if req.verified else "원본과 다름으로 기록했습니다.",
            "application": fr.application_summary(lessor, Building.first(branch=lessor.branch))}


@router.get("/lessors/{lessor_id}/registry-attachment", dependencies=ADMIN_ONLY)
def get_franchise_registry(lessor_id: str):
    """가맹 신청 때 제출된 등기부 원본 (본사 관리자만)."""
    path, mime = fr.registry_file_path(Lessor.require(lessor_id))
    return FileResponse(path, media_type=mime)


@router.post("/lessors/{lessor_id}/bizreg-verify")
def verify_franchise_bizreg(lessor_id: str, req: BizRegVerifyReq, admin=Depends(require_role("superadmin", "admin")), _nr=Depends(not_revoked)):
    """관리자가 국세청 홈택스에서 임대인 사업자등록증(사업자상태·대표자·과세유형)을 확인한 뒤 승인 또는 반려를 기록한다.
    승인(verified=True)되면 임대인 장부의 business_number 에 공식 반영된다."""
    from apps.rent_dist._rent3_core.billing import validate_kr_business_number
    from apps.rent_dist._rent3_core.support import SupportMessage
    lessor = Lessor.require(lessor_id)
    docs = dict(lessor.franchise_docs or {})
    biz = dict(docs.get("bizreg") or {})
    if not biz.get("file"):
        raise DomainError("제출된 사업자등록증이 없습니다.")
    chk = biz.get("check") or {}
    raw_no = (req.business_number or chk.get("business_number") or "").strip()
    ht_status = (req.hometax_status or "계속사업자").strip()
    note = (req.note or "").strip()
    if not note:
        raise DomainError("홈택스 확인 결과 메모를 남겨 주세요.", hint="예: 홈택스 사업자상태 계속사업자(면세/일반) 및 대표자 일치 확인")
    fmt_no = raw_no
    if req.verified:
        if not raw_no:
            raise DomainError("홈택스에서 확인한 사업자등록번호 10자리를 입력해 주세요.")
        val = validate_kr_business_number(raw_no, strict_checksum=True)
        if not val["valid"]:
            raise DomainError(f"사업자등록번호({raw_no})가 유효하지 않아 승인할 수 없습니다: {val['reason']}")
        if any(bad in ht_status for bad in ("폐업", "휴업", "미등록")):
            raise DomainError(f"홈택스 조회 상태가 '{ht_status}'인 사업자등록증은 승인할 수 없습니다.",
                              hint="계속사업자 상태인 사업자등록증만 승인할 수 있습니다.")
        d = val["digits"]
        fmt_no = f"{d[:3]}-{d[3:5]}-{d[5:10]}"
        dup = next((o for o in Lessor.all() if o.id != lessor.id and re.sub(r"\D", "", o.business_number or "") == d), None)
        if dup is not None:      # 같은 사업자번호를 다른 임대인이 이미 쓰고 있으면 승인하지 않는다(남의 등록증 도용 방지)
            raise DomainError(f"이 사업자등록번호({fmt_no})는 이미 다른 임대인({dup.name})에게 승인되어 있습니다.", hint="동일 사업자의 다른 지점이라면 관리인이 직접 확인한 뒤 한쪽을 정리해 주세요.")
        lessor.business_number = fmt_no

    ext_no = str(chk.get("business_number") or "")
    biz["admin_verification"] = {
        "verified": bool(req.verified),
        "business_number": fmt_no,
        "hometax_status": ht_status,
        "matches_extracted_number": bool(ext_no) and re.sub(r"\D", "", ext_no) == re.sub(r"\D", "", fmt_no),
        "note": note,
        "verified_by": getattr(admin, "username", "") or "admin",
        "verified_at": now_iso(),
        "file_sha256": biz.get("sha256", ""),
    }
    docs["bizreg"] = biz
    lessor.franchise_docs = docs
    lessor.save(action="franchise_bizreg_verified" if req.verified else "franchise_bizreg_rejected", app_source=APP_SRC)
    sup.post_admin_message(
        lessor, getattr(admin, "username", "") or "관리인",
        (f"[✅ 사업자등록증 홈택스 확인·승인 완료] 사업자등록번호 {fmt_no} ({ht_status}) 확인이 완료되어 장부에 반영되었습니다. 메모: {note}" if req.verified
         else f"[⚠️ 사업자등록증 반려] 홈택스 확인 결과 보완이 필요합니다. 사유: {note}"),
        category="사업자등록증 확인 결과")
    return {
        "success": True,
        "message": f"홈택스 확인 완료: 사업자등록번호({fmt_no})를 승인하고 장부에 반영했습니다." if req.verified else "사업자등록증 반려 사유를 기록했습니다.",
        "application": fr.application_summary(lessor, Building.first(branch=lessor.branch)),
    }


@router.get("/lessors/{lessor_id}/bizreg-attachment", dependencies=ADMIN_ONLY)
def get_franchise_bizreg(lessor_id: str):
    """임대인이 촬영·제출한 사업자등록증 원본 (본사 관리자만)."""
    path, mime = fr.bizreg_file_path(Lessor.require(lessor_id))
    return FileResponse(path, media_type=mime)


@router.post("/lessors/{lessor_id}/room-limit", dependencies=ADMIN_ONLY)
def set_room_limit(lessor_id: str, req: RoomLimitReq):
    """가맹점당 등록 가능 호실 수 상한 변경 (가맹 상태는 바꾸지 않는다)."""
    if req.room_limit < 1:
        raise DomainError("호실 한도는 1 이상이어야 합니다.")
    lessor = Lessor.require(lessor_id)
    lessor.room_limit = req.room_limit
    lessor.save(action="lessor_room_limit_changed", app_source=APP_SRC)
    return {"success": True, "message": f"호실 한도를 {req.room_limit}실로 바꿨습니다.", "lessor": lessor.safe_dump()}


@router.post("/accounts/reset-password", dependencies=ADMIN_ONLY)
def reset_account_password(req: AccountResetReq):
    """본인인증 이력이 없어 직접 재설정할 수 없는 계정에 임시 비밀번호를 발급한다. 임시 비밀번호는 이 응답에서 한 번만 보인다(해시로 저장)."""
    from apps.rent_dist._rent3_core import passwords as pwd
    obj = Lessor.get(req.id) if req.kind == "lessor" else Tenant.get(req.id) if req.kind == "tenant" else None
    if obj is None:
        raise DomainError("계정을 찾을 수 없습니다. kind 는 lessor 또는 tenant 여야 합니다.")
    temp = pwd.temp_password()
    obj.password = pwd.hash_password(temp)
    obj.save(action="password_reset_by_admin", app_source=APP_SRC)
    return {"success": True, "message": f"'{obj.name}' 님의 임시 비밀번호를 발급했습니다. 본인에게 안전하게 전달하고 바로 바꾸게 하세요.",
            "temporary_password": temp}


class InviteIssueReq(BaseModel):
    id: str


@router.get("/accounts/tenants", dependencies=ADMIN_ONLY)
def list_resident_tenants():
    """입주 안내 링크·임시 비밀번호 발급 화면용: 입주 중인 임차인 목록."""
    from apps.rent_dist._rent3_core import invite as inv, quicklogin as ql
    ensure_pool_seeded()
    items = [{"id": t.id, "name": t.name, "room_no": t.room_no, "branch": t.branch, "invite": inv.status(t)["pending"], "has_pin": bool(t.pin_hash),
              "devices": len(ql.devices_of(t))} for t in Tenant.all() if ql.eligible(t)]
    return {"success": True, "tenants": sorted(items, key=lambda x: (x["branch"], x["room_no"]))}


def _require_lessor_proof(lsr) -> None:
    """임대인 링크의 본인 확인 4번째 칸(사업자번호·계좌번호 끝 4자리)에 쓸 증빙이 장부에 있어야 발급한다(없으면 건물명·이름·전화 3가지만으로 통과해 약하다)."""
    import re as _re
    if not any(len(_re.sub(r"\D", "", str(v or ""))) >= 4 for v in (lsr.business_number, lsr.account_number)):
        raise DomainError(f"{lsr.name} 임대인의 장부에 사업자번호·계좌번호가 없어 본인 확인 증빙이 없습니다.",
                          hint="가맹 신청 정보에서 사업자번호 또는 계좌번호를 먼저 등록한 뒤 링크를 발급해 주세요.")


# ───────── 가맹비 징수 통보 · 납부 등록 · 세금계산서 발행 기록 ─────────
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


def _admin_label(request: Request) -> str:
    from apps.rent_dist._rent3_core.authz import claims_from
    return str((claims_from(request) or {}).get("u") or "관리인")


@router.get("/fees", dependencies=ADMIN_ONLY)
def list_franchise_fees(month: str = ""):
    """가맹비 청구 현황(자동 통보 포함). month 를 주면 그 청구월만."""
    from apps.rent_dist._rent3_core import feebilling as fb, etaxinvoice as et
    ensure_pool_seeded()
    fb.ensure_notices()
    fb.refresh_pending()          # API 로 발행 중인 건의 결과를 확인
    rows = [f for f in fb.FranchiseFee.all() if f.status != "CANCELLED" and (not month or f.billing_month == month)]
    rows.sort(key=lambda f: (f.billing_month, f.branch), reverse=True)
    tot = lambda xs: sum(int(f.total_amount) for f in xs)
    paid = [f for f in rows if f.status == "PAID"]
    biz = {l.id: l.business_number for l in Lessor.all()}
    return {"success": True, "platform": fb.platform_info(), "month": month or fb.month_key(), "auto": fb.auto_enabled(), "etax": et.status_info(),
            "fees": [{**fb.public(f), "lessor_business_number": biz.get(f.id_lessor, "")} for f in rows],
            "summary": {"billed": tot(rows), "paid": tot(paid), "unpaid": tot([f for f in rows if f.status == "NOTICED"]),
                        "overdue": len([f for f in rows if f.overdue]), "invoice_waiting": len([f for f in paid if f.invoice_status != "ISSUED"])},
            "lessors": [{"id": l.id, "name": l.name, "branch": l.branch, "app_fee": l.app_fee, "status": l.status, "business_number": l.business_number} for l in Lessor.all()]}


@router.post("/fees/generate", dependencies=ADMIN_ONLY)
def generate_franchise_fees(req: FeeGenerateReq):
    from apps.rent_dist._rent3_core import feebilling as fb
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
    from apps.rent_dist._rent3_core import feebilling as fb
    from apps.rent_dist._rent3_core import etaxinvoice as et
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
    from apps.rent_dist._rent3_core import feebilling as fb
    fee = fb.submit_invoice_api(fb.FranchiseFee.require(fee_id), _admin_label(request))
    return {"success": True, "message": "전자세금계산서 발행을 접수했습니다. 국세청 전송 결과는 잠시 뒤 자동 반영됩니다(발행 상태 확인으로 바로 확인할 수도 있습니다).", "fee": fb.public(fee)}


@router.post("/fees/{fee_id}/invoice-refresh", dependencies=ADMIN_ONLY)
def refresh_fee_invoice(fee_id: str):
    from apps.rent_dist._rent3_core import feebilling as fb
    fee = fb.refresh_invoice(fb.FranchiseFee.require(fee_id))
    done = fee.invoice_status == "ISSUED"
    return {"success": True, "message": ("발행이 완료되어 승인번호를 기록했습니다." if done else "아직 국세청 전송 결과가 없습니다(테스트 키는 10~30초, 운영 키는 약 10분 걸립니다)."), "fee": fb.public(fee)}


@router.post("/webhooks/etax")
async def etax_webhook(request: Request):
    """볼타 웹훅 수신(서명 검증 필수, 인증 토큰 없음). 성공 이벤트는 조회 API 로 승인번호를 다시 확인해 확정한다."""
    import json as _json
    from apps.rent_dist._rent3_core import feebilling as fb, etaxinvoice as et
    raw = await request.body()
    et.verify_webhook(dict(request.headers), raw)
    try:
        event = _json.loads(raw.decode("utf-8"))
    except ValueError:
        raise DomainError("웹훅 본문을 읽지 못했습니다.")
    result = fb.handle_webhook(event, request.headers.get("webhook-id", ""))
    return {"success": True, "result": result}


@router.get("/fees/{fee_id}/invoice-card", dependencies=ADMIN_ONLY)
def fee_invoice_card(fee_id: str):
    """홈택스에서 직접 발급할 때 입력할 값(공급자·공급받는자·품목·금액)과 비어 있는 필수 정보."""
    from apps.rent_dist._rent3_core import feebilling as fb
    return {"success": True, "card": fb.invoice_card(fb.FranchiseFee.require(fee_id))}


class FeeCashReq(BaseModel):
    approval_no: str
    issue_date: str = ""


@router.post("/fees/{fee_id}/cash-receipt", dependencies=ADMIN_ONLY)
def record_fee_cash_receipt(fee_id: str, req: FeeCashReq, request: Request):
    """홈택스에서 발급한 현금영수증 승인번호를 기록한다(사업자번호가 없는 임대인의 가맹비 등)."""
    from apps.rent_dist._rent3_core import feebilling as fb
    fee = fb.record_cash_receipt(fb.FranchiseFee.require(fee_id), req.approval_no, req.issue_date, _admin_label(request))
    return {"success": True, "message": f"{fee.billing_month} 가맹비 현금영수증(승인번호 {fee.invoice_nts_no})을 기록했고 임대인 장부의 경비로 등록했습니다.", "fee": fb.public(fee)}


@router.post("/fees/{fee_id}/invoice", dependencies=ADMIN_ONLY)
def record_fee_invoice(fee_id: str, req: FeeInvoiceReq, request: Request):
    from apps.rent_dist._rent3_core import feebilling as fb
    fee = fb.record_invoice(fb.FranchiseFee.require(fee_id), req.nts_confirm_no, req.issue_date, _admin_label(request))
    return {"success": True, "message": f"{fee.billing_month} 가맹비 세금계산서(승인번호 {fee.invoice_nts_no})를 기록했고 임대인 장부의 경비로 등록했습니다.", "fee": fb.public(fee)}


@router.post("/fees/{fee_id}/invoice-cancel", dependencies=ADMIN_ONLY)
def cancel_fee_invoice(fee_id: str, req: FeeInvoiceCancelReq, request: Request):
    from apps.rent_dist._rent3_core import feebilling as fb
    fee = fb.cancel_invoice(fb.FranchiseFee.require(fee_id), req.memo, _admin_label(request))
    return {"success": True, "message": "세금계산서 기록을 취소하고 장부에 등록했던 경비도 지웠습니다. 홈택스에서 수정·재발행한 뒤 다시 기록해 주세요.", "fee": fb.public(fee)}


@router.get("/fees/export-hometax-csv", dependencies=ADMIN_ONLY)
def export_fee_hometax_csv(month: str = ""):
    """입금 확인 후 세금계산서 발행 전인 가맹비 목록(홈택스 일괄 발급용 참고 CSV)."""
    from fastapi.responses import Response
    from apps.rent_dist._rent3_core import feebilling as fb
    return Response(content=fb.hometax_csv(month).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="franchise_fee_hometax_{month or "all"}.csv"'})


@router.get("/accounts/lessors", dependencies=ADMIN_ONLY)
def list_lessor_accounts():
    """임대인 암호 설정 상태·6개월(180일) 변경 권유 대상 조회(해시·원문은 제외)."""
    from apps.rent_dist._rent3_core import connect as cn, invite as inv
    ensure_pool_seeded()
    items = []
    for lsr in sorted(Lessor.all(), key=lambda x: x.branch):
        b = Building.first(branch=normalize_branch_id(lsr.branch))
        age = inv.password_age_days(lsr)
        items.append({
            "id": lsr.id,
            "name": lsr.name,
            "username": lsr.username,
            "phone": lsr.phone,
            "branch": lsr.branch,
            "building_name": b.building_name if b else "",
            "status": lsr.status,
            "activated": cn.activated(lsr),
            "password_changed_at": lsr.password_changed_at or lsr.activated_at,
            "days_since_change": age,
            "due_6m": bool(lsr.status == "ACTIVE" and age is not None and age >= inv.LESSOR_REMIND_DAYS),
            "invite_pending": inv.status(lsr)["pending"],
            "password_remind_sent_at": lsr.password_remind_sent_at,
        })
    return {"success": True, "lessors": items}


@router.post("/accounts/lessor-invite", dependencies=ADMIN_ONLY)
def issue_lessor_invite(req: InviteIssueReq, request: Request):
    """관리인이 전화로 본인 확인 후 임대인에게 1회용 비밀번호 설정 링크(7일 유효)를 발급한다. 새 비밀번호를 정하기 전까지 기존 비밀번호는 유지된다."""
    from apps.rent_dist._rent3_core import invite as inv
    lsr = Lessor.get(req.id)
    if lsr is None:
        raise DomainError("임대인을 찾을 수 없습니다.")
    if lsr.status != "ACTIVE":
        raise DomainError("가맹 승인된 임대인에게만 발급할 수 있습니다.")
    _require_lessor_proof(lsr)
    tok = inv.issue(lsr, _admin_name(request))
    b = Building.first(branch=normalize_branch_id(lsr.branch))
    bname = b.building_name if b else lsr.branch
    url = f"/rent3_lessor/?invite={tok}"
    sms_text = (
        f"[{bname}] {lsr.name} 임대인님, 관리인이 비밀번호 설정 링크를 보내 드립니다.\n"
        f"아래 링크를 열어 본인 확인(건물명·이름·전화번호·사업자/계좌 끝 4자리) 후 새 비밀번호를 정해 주세요(7일 안에 1회 사용).\n"
        f"{url}"
    )
    return {
        "success": True,
        "url": url,
        "expires": lsr.invite_expires,
        "lessor_name": lsr.name,
        "building_name": bname,
        "sms_text": sms_text,
        "message": f"{lsr.name} 임대인용 비밀번호 설정 링크를 만들었습니다(7일 안에 1회 사용).",
    }


@router.post("/accounts/lessors/{lessor_id}/remind-password", dependencies=ADMIN_ONLY)
def remind_lessor_password(lessor_id: str, request: Request):
    """관리인 → 임대인 6개월(180일) 주기 새 비밀번호 설정 권유 메시지·링크 발송. 강제 만료·차단은 하지 않으며 기존 비밀번호와 등록 기기는 유지된다."""
    from apps.rent_dist._rent3_core import invite as inv
    lsr = Lessor.get(lessor_id)
    if lsr is None:
        raise DomainError("임대인을 찾을 수 없습니다.")
    if lsr.status != "ACTIVE":
        raise DomainError("가맹 승인된 임대인에게만 발송할 수 있습니다.")
    _require_lessor_proof(lsr)
    tok = inv.issue(lsr, _admin_name(request))
    lsr.password_remind_sent_at = now_iso()
    lsr.save(action="lessor_password_remind_sent", app_source=APP_SRC)
    b = Building.first(branch=normalize_branch_id(lsr.branch))
    bname = b.building_name if b else lsr.branch
    age = inv.password_age_days(lsr)
    url = f"/rent3_lessor/?invite={tok}"
    body = (
        f"[🔒 6개월 정기 비밀번호 변경 권유] {bname} {lsr.name} 임대인님, 안전한 임대 장부 보호를 위해 "
        f"6개월마다 새로운 비밀번호로 변경하시기를 권유드립니다. 아래 1회용 링크(7일 유효)에서 본인 확인 후 새 비밀번호를 설정해 주세요. "
        f"(새 비밀번호를 설정하기 전까지는 기존 비밀번호를 그대로 쓰실 수 있습니다.)\n{url}"
    )
    sup.post_admin_message(lsr, _admin_name(request), body)
    return {
        "success": True,
        "url": url,
        "expires": lsr.invite_expires,
        "lessor_name": lsr.name,
        "building_name": bname,
        "days_since_change": age,
        "sms_text": body,
        "message": f"{lsr.name} 임대인에게 6개월 새 비밀번호 설정 권유 메시지와 1회용 링크를 보냈습니다.",
    }


@router.post("/accounts/invite", dependencies=ADMIN_ONLY)
def issue_invite(req: InviteIssueReq, request: Request):
    """입주 임차인용 1회용 입주 안내 링크 발급(임대인이 못 하는 경우의 대행). 링크 원문은 이 응답에서만 보인다."""
    from apps.rent_dist._rent3_core import invite as inv, quicklogin as ql
    t = Tenant.get(req.id)
    if t is None:
        raise DomainError("임차인을 찾을 수 없습니다.")
    if not ql.eligible(t):
        raise DomainError("입주 중인 호실이 있는 임차인에게만 발급할 수 있습니다.")
    tok = inv.issue(t, _admin_name(request))
    return {"success": True, "url": f"/rent3_tenant/?invite={tok}", "expires": t.invite_expires, "tenant_name": t.name, "room_no": t.room_no,
            "message": "입주 안내 링크를 만들었습니다(7일 안에 1회 사용)."}


@router.post("/accounts/revoke-shortcuts", dependencies=ADMIN_ONLY)
def revoke_shortcuts(req: InviteIssueReq):
    """분실 폰 대응(전화 요청): 임차인의 간편 비밀번호·바로가기를 모두 해제한다. 접근을 줄이는 조치라 입장 절차 없이 가능하다."""
    from apps.rent_dist._rent3_core import quicklogin as ql
    t = Tenant.get(req.id)
    if t is None:
        raise DomainError("임차인을 찾을 수 없습니다.")
    n = ql.revoke_all(t)
    return {"success": True, "message": f"{t.name}님의 바로가기 {n}대와 간편 비밀번호를 해제했습니다."}


class TestTenantReq(BaseModel):
    branch: str = "main"
    room_no: str = "701"
    name: str = "민흥식"
    phone: str = "010-3269-3343"
    apply: bool = False
    raise_limit: bool = False


@router.post("/accounts/reset-activation", dependencies=ADMIN_ONLY)
def reset_activation(req: InviteIssueReq):
    """임차인 가입 상태 초기화(재시험·잘못 연결된 계정 풀기): 비밀번호·간편 비밀번호·바로가기·입주 안내 링크·동의 기록·연결 요청을 지운다. 호실·계약·납부는 그대로."""
    from apps.rent_dist._rent3_core import connect as cn
    t = Tenant.get(req.id)
    if t is None:
        raise DomainError("임차인을 찾을 수 없습니다.")
    out = cn.reset_activation(t)
    return {"success": True, "message": f"{t.room_no}호 {t.name}님의 가입 상태를 처음으로 되돌렸습니다(연결 요청 {out['requests_closed']}건 종료). 호실·계약·납부 내역은 그대로입니다.",
            "hint": "이미 열려 있는 로그인 화면은 로그아웃해야 처음부터 시험할 수 있습니다."}


@router.post("/accounts/test-tenant", dependencies=ADMIN_ONLY)
def test_tenant(req: TestTenantReq):
    """시험·실사용 확인용 임차인 호실 등록(기본 시크빌 701호 민흥식, 0원 계약). apply=False 는 점검만, True 는 등록(등록 전 자동 백업, 중복 없음)."""
    from apps.rent_dist._rent3_core import testtenant as tt
    ensure_pool_seeded()
    out = tt.register(req.branch.strip() or "main", req.room_no, req.name, req.phone, apply=req.apply, raise_limit=req.raise_limit)
    return {"success": out["status"] != "BLOCKED", "level": "warning" if out["status"] == "BLOCKED" else "success", **out}


@router.get("/backups", dependencies=ADMIN_ONLY)
def list_backups():
    from apps.rent_dist._rent3_core import backup as bk
    return {"success": True, "backups": bk.list_backups(), "keep": bk.keep_count(), "daily": __import__("os").getenv("RENT3_BACKUP_DAILY", "1") != "0"}


@router.post("/backups/run", dependencies=ADMIN_ONLY)
def run_backup():
    from apps.rent_dist._rent3_core import backup as bk
    info = bk.create()
    v = bk.verify(bk.path_for(info["name"]))
    return {"success": True, "message": f"백업을 만들었습니다({info['name']}). 검사: {'이상 없음' if v['ok'] else '문제 있음'}", "backup": info, "verify": v}


@router.get("/backups/{name}/download", dependencies=ADMIN_ONLY)
def download_backup(name: str):
    """백업 내려받기 — 개인정보가 들어 있으므로 안전하게 보관할 것."""
    from apps.rent_dist._rent3_core import backup as bk
    try:
        p = bk.path_for(name)
    except (ValueError, FileNotFoundError):
        raise DomainError("백업 파일을 찾을 수 없습니다.")
    return FileResponse(p, media_type="application/gzip", filename=name)


@router.post("/lessors/{lessor_id}/ai-limit", dependencies=ADMIN_ONLY)
def set_ai_limit(lessor_id: str, req: AiLimitReq):
    """가맹점당 월 AI(Gemini) 등기부 읽기 호출 상한 변경."""
    if req.ai_monthly_limit < 0:
        raise DomainError("AI 호출 한도는 0 이상이어야 합니다.")
    lessor = Lessor.require(lessor_id)
    lessor.ai_monthly_limit = req.ai_monthly_limit
    lessor.save(action="lessor_ai_limit_changed", app_source=APP_SRC)
    return {"success": True, "message": f"월 AI 읽기 한도를 {req.ai_monthly_limit}회로 바꿨습니다.", "lessor": lessor.safe_dump()}


@router.post("/lessors/{lessor_id}/settle-fee", dependencies=ADMIN_ONLY)
def settle_lessor_fee(lessor_id: str):
    lessor = Lessor.require(lessor_id)
    lessor.settle_app_fee(app_source=APP_SRC)
    return {
        "success": True,
        "message": f"임대인 '{lessor.name}' 월 SaaS 이용료({lessor.app_fee:,}원) 완납 정산이 users.json에 반영되었습니다.",
        "lessor": lessor.safe_dump(),
    }


class DupRemoveReq(BaseModel):
    apply: bool = False


@router.get("/pool-duplicates", dependencies=ADMIN_ONLY)
def pool_duplicates():
    """Data Pool 중복 점검(읽기 전용): 내용이 같은 납부(자동 정리 가능)·같은 달 여러 건·같은 호실의 계약/임차인 중복."""
    from apps.rent_dist._rent3_core import dupcheck
    ensure_pool_seeded()
    return {"success": True, **dupcheck.scan()}


@router.post("/pool-duplicates/remove", dependencies=ADMIN_ONLY)
def remove_pool_duplicates(req: DupRemoveReq):
    """내용이 완전히 같은 중복 납부만 정리한다(가장 먼저 만든 한 건을 남김). apply=false 면 점검만, true 면 풀 전체를 먼저 백업한 뒤 지운다."""
    from apps.rent_dist._rent3_core import dupcheck
    ensure_pool_seeded()
    res = dupcheck.remove_exact(apply=req.apply)
    msg = (f"중복 납부 {res['removed']}건을 지웠습니다(백업 {res['backup']})." if res["applied"] else f"지울 수 있는 중복 납부 {res['would_remove']}건을 찾았습니다(아직 지우지 않았습니다).")
    return {"success": True, "message": msg, **res}


@router.get("/pool/{collection}", dependencies=ADMIN_ONLY)
def inspect_pool_collection(collection: str, q: str = ""):
    ensure_pool_seeded()
    envelope = pool().raw_collection(collection)
    if q.strip():
        kw = q.strip().lower()
        envelope["items"] = [
            it for it in envelope["items"]
            if kw in str(it).lower()
        ]
        envelope["filtered_count"] = len(envelope["items"])
    return {"success": True, "collections": list(POOL_COLLECTIONS), **envelope}


# ───────── 원격 지원 · 대리 접속 (4A) ─────────
# 관리인이 사용법을 모르는 임대인·임차인을 돕는다. 사용자가 원격 지원을 허용했고 지원 채팅이 있어야 입장할 수 있으며,
# 입장하면 보기 전용이다(authz.admin_proxy_guard). 접속 사유는 지원 채팅에 남고, 접속 기록(1년)이 따로 남는다.
class SupportMsgReq(BaseModel):
    user: str                      # 'lessor:<id>' | 'tenant:<id>'
    body: str


class PhoneIntakeReq(BaseModel):
    user: str
    category: str
    memo: str = ""


class SupportEnterReq(BaseModel):
    user: str
    view: str                      # lessor | tenant | contract
    session_id: str = ""
    room_no: str = ""
    category: str = ""


def _admin_name(request: Request) -> str:
    from apps.rent_dist._rent3_core.authz import need_claims
    return str(need_claims(request).get("u") or "admin")


def _user_card(user: Any) -> Dict[str, Any]:
    key = sup.thread_key(user)
    msgs = sup.thread_messages(key)
    return {
        "id": user.id, "name": user.name, "user": key, "phone_tail": "".join(ch for ch in (user.phone or "") if ch.isdigit())[-4:],
        "support_allowed": cs.support_allowed(user),
        "has_thread": bool(msgs), "unread": sup.unread_count(key, "admin"),
        "last_at": msgs[-1].created_at if msgs else "",
    }


@router.get("/support/directory", dependencies=ADMIN_ONLY)
def support_directory(branch: str = ""):
    """원격 지원 카드: 건물 목록, 선택한 건물의 호실별 임대인·현 임차인·진행 중 계약방과 원격 지원 허용 여부."""
    ensure_pool_seeded()
    sup.purge_expired()
    buildings = Building.all()
    out: Dict[str, Any] = {"success": True, "branches": [{"branch": b.branch, "building_name": b.building_name} for b in buildings],
                           "categories": list(sup.CATEGORIES)}
    if not branch:
        return out
    b_id = normalize_branch_id(branch)
    bldg = Building.first(branch=b_id)
    if not bldg:
        raise DomainError(f"'{branch}' 건물을 찾을 수 없습니다.")
    lessor = Lessor.get(bldg.id_lessor) if bldg.id_lessor else Lessor.first(branch=b_id)
    from apps.rent_dist._rent3_core.cosign import CoSignSession
    sessions = [x for x in CoSignSession.find(branch=b_id) if getattr(x, "status", "") == "IN_PROGRESS"]
    rooms = []
    for r in sorted(Room.find(branch=b_id), key=lambda x: x.room_no):
        tenant = Tenant.get(r.id_tenant) if r.id_tenant else None
        rooms.append({
            "room_no": r.room_no, "is_occupied": r.is_occupied,
            "tenant": _user_card(tenant) if tenant else None,
            "sessions": [{"id": x.id, "step": x.current_step, "call_status": x.call_status,
                          "tenant_name": str((x.terms or {}).get("tenant_name", ""))}
                         for x in sessions if x.room_no == r.room_no],
        })
    out.update({"branch": b_id, "building_name": bldg.building_name, "lessor": _user_card(lessor) if lessor else None, "rooms": rooms})
    return out


@router.get("/support/thread", dependencies=ADMIN_ONLY)
def support_thread(user: str):
    sup.purge_expired()
    u = sup.user_by_thread(user)
    sup.mark_read(user, "admin")
    return {"success": True, "user": _user_card(u), "messages": [sup.public_message(m) for m in sup.thread_messages(user)],
            "retention_days": sup.SUPPORT_CHAT_DAYS}


@router.post("/support/messages", dependencies=ADMIN_ONLY)
def support_post(req: SupportMsgReq, request: Request):
    u = sup.user_by_thread(req.user)
    m = sup.post_admin_message(u, _admin_name(request), req.body)
    return {"success": True, "message": "사용자에게 보냈습니다.", "item": sup.public_message(m)}


@router.post("/support/phone-intake", dependencies=ADMIN_ONLY)
def support_phone_intake(req: PhoneIntakeReq, request: Request):
    """전화로 요청받은 경우: 사유 분류와 접수 메모를 지원 채팅에 '[전화 요청 접수]'로 남긴다."""
    u = sup.user_by_thread(req.user)
    m = sup.phone_intake(u, _admin_name(request), req.category, req.memo)
    return {"success": True, "message": "전화 요청 접수를 지원 채팅에 남겼습니다. 이제 입장할 수 있습니다.", "item": sup.public_message(m)}


@router.post("/support/enter", dependencies=ADMIN_ONLY)
def support_enter(req: SupportEnterReq, request: Request):
    u = sup.user_by_thread(req.user)
    res = sup.enter(_admin_name(request), u, req.view, session_id=req.session_id, category=req.category, room_no=req.room_no)
    return {"success": True, "message": "접속 기록을 남기고 보기 전용으로 입장합니다.", **res}
