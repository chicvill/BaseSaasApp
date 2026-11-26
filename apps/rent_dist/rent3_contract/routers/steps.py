"""
apps/rent_dist/rent3_contract/routers/steps.py — Step 2 시설점검/사진 · 취소/사전작성 · Step 3 조건/합의 · Step 4 서명 · Step 5 체결 · 진위확인.
"""
import os
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.errors import Conflict, DomainError
from apps.rent_dist._rent3_core import Building, CoSignSession, Contract, contract_chat, ensure_pool_seeded
from ._deps import APP_SRC

router = APIRouter()

FIELD_KO = {
    "deposit": "보증금",
    "contract_deposit": "계약금",
    "monthly_rent": "월세",
    "maintenance_fee": "관리비",
    "cleaning_fee": "퇴실 청소비",
    "move_in_date": "입주일",
    "lease_term_months": "계약기간",
}


def terms_lock_enabled() -> bool:
    """RENT3_TERMS_LOCK: 기본 켜짐. '0' 이면 예전처럼 /terms 로 모든 조건을 바로 고칠 수 있다(비상·시연용)."""
    return os.environ.get("RENT3_TERMS_LOCK", "1").strip() != "0"


class Step2ConfirmReq(BaseModel):
    role: str = "tenant"
    option_checks: Optional[List[Dict[str, Any]]] = None
    facility_notes: Optional[str] = None


class CancelReq(BaseModel):
    role: str = "tenant"
    reason: str = ""


class PrefillReq(BaseModel):
    role: str = "lessor"
    updates: Dict[str, Any] = Field(default_factory=dict)


class FacilityPhotoReq(BaseModel):
    role: str = "tenant"
    photo_data_url: str
    caption: str = ""


class FacilityPhotoDeleteReq(BaseModel):
    role: str = "tenant"


class ReopenTermsReq(BaseModel):
    role: str = "lessor"
    confirm: bool = False


class TermsUpdateReq(BaseModel):
    role: str = "lessor"
    updates: Dict[str, Any] = Field(default_factory=dict)
    expected_revision: Optional[int] = None      # 화면이 근거로 삼은 리비전. 그 사이 상대가 조건을 바꿨으면 덮어쓰지 않고 거절한다


class Step3AgreeReq(BaseModel):
    role: str = "tenant"
    expected_revision: Optional[int] = None


class Step4SignReq(BaseModel):
    role: str = "tenant"
    signature: str
    expected_revision: Optional[int] = None


class Step5CompleteReq(BaseModel):
    payment_method: str = "TRANSFER"
    own_account_checked: bool = False   # 임대인이 본인 계좌의 실제 거래내역에서 입금을 직접 확인함


@router.post("/sessions/{session_id}/step2")
def confirm_step2(session_id: str, req: Step2ConfirmReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.confirm_step2(
        role=req.role,
        option_checks=req.option_checks,
        facility_notes=req.facility_notes,
        app_source=APP_SRC,
    )
    return {
        "success": True,
        "message": "Step 2 호실 옵션 및 시설 점검표 상태가 실시간 반영되었습니다.",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: str, req: CancelReq):
    """체결 전 계약 취소(당사자만). 사유가 상대방 화면에 그대로 전달되고 호실이 다시 열린다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.cancel(req.role, req.reason, app_source=APP_SRC)
    return {"success": True, "message": "계약을 취소했습니다. 호실이 다시 열렸습니다.", "session": s.model_dump()}


@router.post("/sessions/{session_id}/prefill")
def prefill_session(session_id: str, req: PrefillReq):
    """대면 전 사전 작성: 임대인은 계약 조건, 임차인은 이메일·차량번호. 상대가 연결하기 전에만 가능하며 만난 뒤 ③단계에서 다시 합의한다."""
    s = CoSignSession.by_code_or_id(session_id)
    s.prefill(req.role, req.updates, app_source=APP_SRC)
    return {"success": True, "message": "사전 작성 내용을 저장했습니다. 만나서 ③단계에서 양쪽이 다시 확인합니다.", "session": s.model_dump()}


@router.post("/sessions/{session_id}/facility-photos")
def add_facility_photo(session_id: str, req: FacilityPhotoReq):
    """시설 점검 사진 업로드(모바일 카메라). 당사자만, 점검 단계에서만."""
    s = CoSignSession.by_code_or_id(session_id)
    rec = s.add_facility_photo(req.role, req.photo_data_url, req.caption, app_source=APP_SRC)
    return {"success": True, "message": "점검 사진이 기록되었습니다.", "photo": rec, "session": s.model_dump()}


@router.get("/sessions/{session_id}/facility-photos/{photo_id}")
def get_facility_photo(session_id: str, photo_id: str):
    s = CoSignSession.by_code_or_id(session_id)
    path, mime = s.facility_photo_path(photo_id)
    return FileResponse(path, media_type=mime)


@router.post("/sessions/{session_id}/facility-photos/{photo_id}/delete")
def delete_facility_photo(session_id: str, photo_id: str, req: FacilityPhotoDeleteReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.remove_facility_photo(req.role, photo_id, app_source=APP_SRC)
    return {"success": True, "message": "사진을 지웠습니다.", "session": s.model_dump()}


@router.post("/sessions/{session_id}/reopen-terms")
def reopen_terms(session_id: str, req: ReopenTermsReq):
    """서명 이후 조건 변경: 경고를 확인(confirm)하면 양측 서명·동의가 지워지고 3단계로 돌아간다."""
    s = CoSignSession.by_code_or_id(session_id)
    if not req.confirm:
        raise DomainError(
            "조건을 다시 열면 양측 서명과 3단계 동의가 초기화되고 3단계로 돌아갑니다.",
            hint="그래도 진행하려면 확인 후 다시 요청해 주세요(confirm=true).",
        )
    s.reopen_terms(role=req.role, app_source=APP_SRC)
    who = "임대인" if req.role == "lessor" else "임차인"
    contract_chat.system_note(s, f"{who}이(가) 조건을 다시 열었습니다. 양측 서명·동의가 초기화되고 3단계로 돌아갔습니다 (리비전 v{s.revision}).", step=3)
    return {
        "success": True,
        "level": "diff",
        "message": f"조건을 다시 열었습니다. 서명이 초기화되고 3단계로 돌아갑니다 (리비전 v{s.revision}).",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/terms")
def update_terms(session_id: str, req: TermsUpdateReq):
    s = CoSignSession.by_code_or_id(session_id)
    if req.expected_revision is not None and req.expected_revision != s.revision and s.terms_changes(req.updates):
        raise Conflict(
            "그 사이 상대방이 계약 조건을 바꿨습니다. 화면을 새로 불러왔으니 확인한 뒤 다시 진행해 주세요.",
            hint="내가 고치던 값이 있다면 최신 조건과 비교해서 다시 입력하세요.",
        )
    if terms_lock_enabled() and s.call_status != "CALLING" and s.status == "IN_PROGRESS":
        changed_keys = s.terms_changes(req.updates)      # 지금 값과 같게 넘어온 항목은 그대로 통과한다
        if changed_keys and s.current_step >= 4:
            raise Conflict(
                "서명 단계 이후에는 조건을 바로 고칠 수 없습니다.",
                hint="'조건 다시 열기'를 쓰세요. 양측 서명과 3단계 동의가 초기화되고 3단계로 돌아갑니다.",
            )
        locked = [FIELD_KO[k] for k in changed_keys if k in CoSignSession.TERMS_LOCKED_FIELDS]
        if locked:
            raise Conflict(
                f"{', '.join(locked)}은(는) 직접 고칠 수 없습니다. 상대에게 제안하고 수락을 받아야 바뀝니다.",
                hint="계약방 채팅(💬)에서 '제안'을 보내세요. 인적사항·납부 시점·세금계산서 정보·특약은 직접 고칠 수 있습니다.",
            )
    prev_rev = s.revision
    s.update_terms(role=req.role, updates=req.updates, app_source=APP_SRC)
    changed = s.revision != prev_rev
    return {
        "success": True,
        "changed": changed,
        "level": "diff" if changed else "success",
        "message": (
            f"계약 조건이 수정되어 리비전(v{s.revision})이 갱신되었으며, 양측 동의 잠금이 재확인 대기로 초기화되었습니다."
            if changed
            else "변경된 조건이 없습니다."
        ),
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/step3/agree")
def agree_step3(session_id: str, req: Step3AgreeReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.agree_step3(role=req.role, expected_revision=req.expected_revision, app_source=APP_SRC)
    return {
        "success": True,
        "message": f"{'임대인' if req.role == 'lessor' else '임차인'} 계약 조건 합의 잠금이 완료되었습니다 (v{s.revision}).",
        "session": s.model_dump(),
    }


@router.post("/sessions/{session_id}/step4/sign")
def sign_step4(session_id: str, req: Step4SignReq):
    s = CoSignSession.by_code_or_id(session_id)
    s.sign_step4(
        role=req.role,
        signature=req.signature,
        expected_revision=req.expected_revision,
        app_source=APP_SRC,
    )
    ct_dump = None
    if s.status == "COMPLETED" and s.created_contract_id:
        ct_obj = Contract.get(s.created_contract_id)
        if ct_obj:
            ct_dump = ct_obj.model_dump()
    return {
        "success": True,
        "message": (
            f"🎉 비대면 재계약 체결 완료! 계약서({s.created_contract_id})가 봉인되었습니다."
            if s.status == "COMPLETED"
            else (
                "상호 신분증·등기 확인 기반 양측 전자서명 및 SHA-256 계약원본 무결성 봉인이 완료되었습니다!"
                if s.integrity_hash
                else f"{'임대인' if req.role == 'lessor' else '임차인'} 전자서명이 날인되었습니다."
            )
        ),
        "session": s.model_dump(),
        **({"contract": ct_dump} if ct_dump is not None else {}),
    }


@router.post("/sessions/{session_id}/step5/complete")
def complete_step5(session_id: str, req: Step5CompleteReq):
    s = CoSignSession.by_code_or_id(session_id)
    if not req.own_account_checked:
        raise DomainError(
            "본인 계좌의 실제 거래내역에서 입금을 직접 확인해야 체결할 수 있습니다.",
            hint="임차인이 보여 주는 이체 완료 화면·캡처는 위조될 수 있습니다. 은행 앱의 입금 내역을 확인해 주세요.",
        )
    result = s.complete_step5_and_seal(payment_method=req.payment_method, app_source=APP_SRC)
    return {
        "success": True,
        "message": (
            f"계약 체결 완료! 계약·호실·입주자·수납 정보가 "
            f"즉시 업데이트되었습니다. 계약 접수번호는 {result['contract']['fixed_date_no']} 입니다 (법적 확정일자는 주민센터·등기소에서 별도로 받아야 합니다)."
        ),
        **result,
    }


def _ledger_diffs(ct: Contract, sess: CoSignSession) -> List[Dict[str, Any]]:
    """계약서에 표시되는 원장(contracts.json)이 서명 당시 세션 내용과 같은지 비교한다.

    서명 해시는 세션 데이터로만 계산되므로, 원장만 따로 고치면 해시 검사로는 걸리지 않는다.
    """
    t = sess.terms or {}
    checks = [
        ("deposit", "보증금", int(t.get("deposit", ct.deposit)), ct.deposit),
        ("monthly_rent", "월 임차료", int(t.get("monthly_rent", ct.monthly_rent)), ct.monthly_rent),
        ("maintenance_fee", "월 관리비", int(t.get("maintenance_fee", ct.maintenance_fee)), ct.maintenance_fee),
        ("contract_deposit", "계약금", int(t.get("contract_deposit", ct.contract_deposit)), ct.contract_deposit),
        ("lease_term_months", "임대차 개월 수", int(t.get("lease_term_months", ct.lease_term_months)), ct.lease_term_months),
        ("lease_type", "임대 유형", t.get("lease_type", ct.lease_type), ct.lease_type),
        ("pay_timing", "납부 시기", t.get("pay_timing", ct.pay_timing), ct.pay_timing),
        ("move_in_date", "입주일", t.get("move_in_date") or ct.move_in_date, ct.move_in_date),
        ("special_terms", "특약사항", t.get("special_terms", ct.special_terms), ct.special_terms),
        ("tenant_name", "임차인 성명", t.get("tenant_name", ct.tenant_name), ct.tenant_name),
        ("tenant_phone", "임차인 연락처", t.get("tenant_phone", ct.tenant_phone), ct.tenant_phone),
        ("room_no", "호실", sess.room_no, ct.room_no),
        ("lessor_signature", "임대인 서명", sess.lessor_signature, ct.lessor_signature),
        ("tenant_signature", "임차인 서명", sess.tenant_signature, ct.tenant_signature),
        ("integrity_hash", "무결성 해시", sess.integrity_hash, ct.integrity_hash),
    ]
    diffs: List[Dict[str, Any]] = []
    for key, label, signed, ledger in checks:
        if str(signed) != str(ledger):
            # 서명·해시·개인정보는 값을 그대로 보이지 않고 '다름'만 알린다.
            hide = key in ("lessor_signature", "tenant_signature", "integrity_hash", "tenant_name", "tenant_phone", "special_terms")
            diffs.append({
                "field": key,
                "label": label,
                "signed": "" if hide else signed,
                "ledger": "" if hide else ledger,
            })
    return diffs


@router.get("/verify")
def verify_contract_integrity(hash: str = "", no: str = ""):
    """계약서 진위 확인 (공개). 인쇄본의 무결성 해시(SHA-256)와 계약번호로 서버 기록과 대조한다.

    개인정보(성명·연락처)는 응답에 넣지 않고, 확인에 필요한 최소 정보만 돌려준다.
    """
    ensure_pool_seeded()
    h = re.sub(r"[^0-9a-fA-F]", "", hash or "").lower()
    contract_no = (no or "").strip()
    if len(h) != 64:
        return {
            "success": True,
            "result": "INVALID_INPUT",
            "message": "무결성 해시는 64자리(0-9, a-f)입니다. 계약서에 적힌 값을 그대로 입력해 주세요.",
        }

    ct = next((c for c in Contract.all() if (c.integrity_hash or "").lower() == h), None)
    if ct is None and contract_no:
        by_no = Contract.get(contract_no)
        if by_no:
            return {
                "success": True,
                "result": "MISMATCH",
                "message": "입력한 계약번호는 존재하지만 서버에 기록된 해시와 다릅니다. 인쇄본이 변조되었거나 다른 계약서일 수 있습니다.",
                "contract_no": by_no.id,
            }
    if ct is None:
        return {
            "success": True,
            "result": "NOT_FOUND",
            "message": "이 해시로 체결된 계약이 서버에 없습니다. 인쇄본이 변조되었거나 이 시스템에서 만든 계약서가 아닐 수 있습니다.",
        }
    if contract_no and contract_no != ct.id:
        return {
            "success": True,
            "result": "MISMATCH",
            "message": "해시는 서버에 있지만 입력한 계약번호와 다른 계약의 것입니다. 인쇄본의 계약번호가 바뀌었을 수 있습니다.",
        }

    bldg_obj = Building.get(ct.id_building) if ct.id_building else None
    summary = {
        "contract_no": ct.id,
        "building_name": bldg_obj.building_name if bldg_obj else "",
        "room_no": ct.room_no,
        "contract_date": ct.contract_date,
        "lease_type": ct.lease_type,
        "fixed_date_no": getattr(ct, "fixed_date_no", "") or "",
        "status": ct.status,
    }
    sess = CoSignSession.get(ct.cosign_session_id) if ct.cosign_session_id else None
    if sess is None:
        return {
            "success": True,
            "result": "MATCHED_NO_SOURCE",
            "message": "서버에 기록된 해시와 일치합니다. 다만 원본 계약 데이터가 없어 내용 변경 여부까지는 다시 계산할 수 없습니다.",
            "summary": summary,
        }
    recomputed = sess.compute_integrity_hash()
    if recomputed != (ct.integrity_hash or "").lower():
        return {
            "success": True,
            "result": "ALTERED",
            "message": "서버에 저장된 계약 데이터가 서명 이후 바뀌었습니다. 이 계약서는 신뢰할 수 없습니다. 관리자에게 문의하세요.",
            "summary": summary,
        }
    diffs = _ledger_diffs(ct, sess)
    if diffs:
        return {
            "success": True,
            "result": "LEDGER_MISMATCH",
            "message": "서명 당시 내용은 그대로지만, 계약서에 표시되는 계약 원장이 서명 내용과 다릅니다. 원장이 서명 뒤에 수정되었을 수 있어 이 계약서는 신뢰할 수 없습니다.",
            "summary": summary,
            "diffs": diffs,
        }
    return {
        "success": True,
        "result": "INTACT",
        "message": "인쇄본의 해시가 서버 기록과 일치하고, 서명 이후 계약 내용과 계약 원장이 바뀌지 않았습니다.",
        "summary": summary,
    }


@router.get("/contracts/{contract_id}")
def get_sealed_contract(contract_id: str):
    ensure_pool_seeded()
    ct = Contract.require(contract_id)
    bldg = Building.get(ct.id_building)
    return {
        "success": True,
        "contract": ct.model_dump(),
        "building": bldg.model_dump() if bldg else None,
    }
