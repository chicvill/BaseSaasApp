"""
apps/_rent3_core/domains/cosign_steps.py — 공동계약 사전작성·시설사진·3단계 합의·4단계 전자서명·5단계 체결 봉인 믹스인.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from core.errors import Conflict, DomainError, NotFound
from ..broker import EXT_TO_MIME, MAX_PHOTO_BYTES
from ..models import (
    Contract,
    Pay,
    Room,
    Tenant,
    add_months_str,
    date_today,
)
from ..ops import DirectChat, RtmsReport
from ..pool import now_iso, pool
from .cosign_registry import re_is_date

if TYPE_CHECKING:
    from ..cosign import CoSignSession


def prefill(self: CoSignSession, role: str, updates: Dict[str, Any], app_source: str = "rent3_contract") -> CoSignSession:
    """대면 전 사전 작성. 상대가 아직 연결하기 전(CALLING)에만 가능하다.
    임대인은 계약 조건(입주일·금액·특약)을, 임차인은 본인 연락 정보(이메일·차량번호)를 미리 적는다.
    여기서 적은 값은 '초안'일 뿐이다 — 만난 뒤 ③단계에서 양쪽이 조건을 다시 보고 동의해야 효력이 생긴다."""
    self._ensure_in_progress()
    if self.call_status != "CALLING":
        raise DomainError("이미 양쪽이 연결되어 사전 작성을 할 수 없습니다.", hint="연결 뒤의 조건 변경은 ③단계에서 하며 양쪽 동의를 다시 받습니다.")
    allowed = self.PREFILL_LESSOR_KEYS if role == "lessor" else self.PREFILL_TENANT_KEYS
    bad = [k for k in updates if k not in allowed]
    if bad:
        raise DomainError(f"이 역할이 미리 적을 수 없는 항목입니다: {', '.join(bad)}",
                          hint="임대인은 계약 조건, 임차인은 이메일·차량번호만 사전 작성할 수 있습니다.")
    merged = {**self.terms}
    int_fields = {"contract_deposit", "deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "lease_term_months"}
    for k, v in updates.items():
        if v is None:
            continue
        if k in int_fields:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise DomainError(f"{k} 는 숫자여야 합니다.")
            if v < 0:
                raise DomainError("금액·기간은 0 이상이어야 합니다.")
        elif isinstance(v, str):
            v = v.strip()
        if k == "move_in_date" and not re_is_date(str(v)):
            raise DomainError("입주일은 YYYY-MM-DD 형식이어야 합니다.")
        if k == "pay_timing" and v not in ("후불", "선불"):
            raise DomainError("월세 납부 시기는 후불 또는 선불이어야 합니다.")
        if k == "lease_type" and v not in ("월세", "전세"):
            raise DomainError("임대 유형은 월세 또는 전세여야 합니다.")
        merged[k] = v
    merged = self._sync_cleanup_clause(merged)
    if int(merged.get("contract_deposit", 0)) > int(merged.get("deposit", 0)):
        raise DomainError("계약금은 보증금보다 클 수 없습니다.")
    if self.mode == "RENEWAL" and self.prev_contract_id and role == "lessor":
        prev_ct = Contract.get(self.prev_contract_id)
        if prev_ct:
            prev_ct.validate_renewal_cap(new_deposit=int(merged.get("deposit", 0)), new_rent=int(merged.get("monthly_rent", 0)))
    self.terms = merged
    self.draft_prefilled_at = now_iso()
    return self.save(action=f"prefilled_by_{role}", app_source=app_source)

def add_facility_photo(self: CoSignSession, role: str, data_url: str, caption: str = "", app_source: str = "rent3_contract") -> Dict[str, Any]:
    """시설 점검 사진을 올린다(점검 단계에서만). 파일은 서버에 저장하고 SHA-256 을 남겨, 퇴실 정산 때 입주 당시 상태의 증거가 된다."""
    self._ensure_ready_for_steps()
    if self.current_step > 2:
        raise DomainError("시설 점검 단계가 끝나 사진을 더 올릴 수 없습니다.", hint="점검 사진은 합의된 뒤 바뀌지 않도록 고정됩니다.")
    if len(self.facility_photos) >= self.MAX_FACILITY_PHOTOS:
        raise DomainError(f"점검 사진은 최대 {self.MAX_FACILITY_PHOTOS}장까지 올릴 수 있습니다.")
    m = re.match(r"^data:([a-zA-Z0-9.+/-]+);base64,(.+)$", (data_url or "").strip(), re.DOTALL)
    ext = self.FACILITY_MIME.get(m.group(1).lower()) if m else None
    if not m or not ext:
        raise DomainError("사진 형식이 올바르지 않습니다.", hint="허용 형식: JPG, PNG, WEBP")
    try:
        binary = base64.b64decode(m.group(2), validate=False)
    except Exception:
        raise DomainError("사진을 읽지 못했습니다. 다시 선택해 주세요.")
    if not binary:
        raise DomainError("비어 있는 사진입니다.")
    if len(binary) > MAX_PHOTO_BYTES:
        raise DomainError("사진은 한 장당 10MB 이하만 허용됩니다.")
    pid = uuid4().hex[:10]
    abs_dir = os.path.join(pool().pool_dir, "uploads", "facility", self.id)
    os.makedirs(abs_dir, exist_ok=True)
    target = os.path.join(abs_dir, f"{pid}.{ext}")
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fp:
        fp.write(binary)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, target)
    rec = {"id": pid, "file": f"uploads/facility/{self.id}/{pid}.{ext}", "sha256": hashlib.sha256(binary).hexdigest(),
           "caption": (caption or "").strip()[:80], "by_role": "lessor" if role == "lessor" else "tenant",
           "uploaded_at": now_iso()}
    self.facility_photos = [*self.facility_photos, rec]
    # 점검 내용이 바뀌었으니 양쪽 확인을 모두 다시 받는다(상대가 보지 못한 사진에 동의한 것이 되지 않게)
    self.step2_tenant_locked = False
    self.step2_lessor_confirmed = False
    self.save(action=f"facility_photo_added_by_{role}", app_source=app_source)
    return rec

def remove_facility_photo(self: CoSignSession, role: str, photo_id: str, app_source: str = "rent3_contract") -> None:
    self._ensure_ready_for_steps()
    if self.current_step > 2:
        raise DomainError("시설 점검 단계가 끝나 사진을 지울 수 없습니다.")
    rec = next((p for p in self.facility_photos if p["id"] == photo_id), None)
    if rec is None:
        raise NotFound("사진을 찾을 수 없습니다.")
    if rec["by_role"] != ("lessor" if role == "lessor" else "tenant"):
        raise DomainError("본인이 올린 사진만 지울 수 있습니다.")
    self.facility_photos = [p for p in self.facility_photos if p["id"] != photo_id]
    self.step2_tenant_locked = False
    self.step2_lessor_confirmed = False
    self.save(action=f"facility_photo_removed_by_{role}", app_source=app_source)

def facility_photo_path(self: CoSignSession, photo_id: str):
    rec = next((p for p in self.facility_photos if p["id"] == photo_id), None)
    if rec is None:
        raise NotFound("사진을 찾을 수 없습니다.")
    root = os.path.abspath(os.path.join(pool().pool_dir, "uploads", "facility"))
    cand = os.path.abspath(os.path.join(pool().pool_dir, rec["file"]))
    if not cand.startswith(root + os.sep) or not os.path.isfile(cand):
        raise NotFound("사진 파일을 찾을 수 없습니다.")
    return cand, EXT_TO_MIME.get(os.path.splitext(cand.lower())[1], "application/octet-stream")

def terms_changes(self: CoSignSession, updates: Dict[str, Any]) -> List[str]:
    """update_terms 와 같은 값 정리를 거쳐, 지금 값과 달라지는 항목 이름 목록을 돌려준다(저장하지 않는다)."""
    int_fields = {"contract_deposit", "deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "lease_term_months"}
    bool_fields = {"vat_target", "vat_separate", "mortgage_cleanup_clause", "tax_invoice_requested"}
    out: List[str] = []
    for k, v in (updates or {}).items():
        if v is None:
            continue
        try:
            if k in int_fields:
                v = int(v)
            elif k in bool_fields:
                v = bool(v)
            elif isinstance(v, str):
                v = v.strip()
        except (TypeError, ValueError):
            out.append(k)
            continue
        if (self.terms or {}).get(k) != v:
            out.append(k)
    return out

def reopen_terms(self: CoSignSession, role: str, app_source: str = "rent3_contract") -> CoSignSession:
    """서명(4단계) 이후 조건을 바꿔야 할 때의 유일한 길: 양측 서명·3단계 동의를 지우고 3단계로 돌아간다(리비전 +1)."""
    self._ensure_ready_for_steps()
    if self.current_step < 4:
        raise DomainError("이미 계약 조건 단계(③)입니다.", hint="조건은 제안·수락으로 바꿀 수 있습니다.")
    self.step3_lessor_agreed = False
    self.step3_tenant_agreed = False
    self.lessor_signature = ""
    self.tenant_signature = ""
    self.integrity_hash = ""
    self.tenant_recheck_at = ""            # 다시 서명하면 체결 직전 등기부 재확인도 다시 한다
    self.current_step = 3
    self.revision += 1
    return self.save(action=f"terms_reopened_by_{role}_rev_{self.revision}", app_source=app_source)

def update_terms(
    self: CoSignSession,
    role: str,
    updates: Dict[str, Any],
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """Step 3 계약 조건 수정 — 단 1개 필드라도 값이 달라지면 revision 증가 + 양측 동의 및 서명 즉시 초기화!"""
    self._ensure_ready_for_steps()
    merged = {**self.terms}
    changed = False
    int_fields = {"contract_deposit", "deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "lease_term_months"}
    bool_fields = {"vat_target", "vat_separate", "mortgage_cleanup_clause", "tax_invoice_requested"}

    for k, v in updates.items():
        if v is None:
            continue
        if k in int_fields:
            v = int(v)
        elif k in bool_fields:
            v = bool(v)
        elif isinstance(v, str):
            v = v.strip()
        if merged.get(k) != v:
            merged[k] = v
            changed = True

    merged = self._sync_cleanup_clause(merged)
    self._check_pre_contract_move_in(merged)
    if merged != self.terms:
        changed = True

    # 재계약(RENEWAL) 시 법정 5% 상한 실시간 검증
    if self.mode == "RENEWAL" and self.prev_contract_id:
        prev_ct = Contract.get(self.prev_contract_id)
        if prev_ct:
            prev_ct.validate_renewal_cap(
                new_deposit=int(merged.get("deposit", 0)),
                new_rent=int(merged.get("monthly_rent", 0)),
            )

    if changed:
        self.terms = merged
        self.revision += 1
        self.step3_lessor_agreed = False
        self.step3_tenant_agreed = False
        self.lessor_signature = ""
        self.tenant_signature = ""
        self.integrity_hash = ""
        if self.current_step > 3:
            self.current_step = 3
        return self.save(action=f"terms_edited_by_{role}_rev_{self.revision}", app_source=app_source)

    return self

def pre_contract_min_move_in(self: CoSignSession) -> str:
    """사전 계약(입주 중인 전출 예정 호실)이면 새 입주일의 가장 빠른 날, 아니면 ''. 현재 입주자가 나가기 전에는 들어올 수 없다."""
    if self.mode == "RENEWAL":      # 연장 재계약: 새 조건은 이전 계약 만기일부터 적용(그 전까지는 이전 조건)
        prev = Contract.get(self.prev_contract_id) if self.prev_contract_id else None
        return (prev.expiry_date or "") if prev else ""
    room = Room.get(self.id_room)
    if room is None or not room.is_occupied:
        return ""
    return room.expected_vacate_date()

def _check_pre_contract_move_in(self: CoSignSession, terms: Dict[str, Any]) -> None:
    floor = self.pre_contract_min_move_in()
    mv = str(terms.get("move_in_date") or "")
    if floor and re_is_date(mv) and mv < floor:
        if self.mode == "RENEWAL":
            raise DomainError(f"연장 계약의 시작일은 현재 계약 만기일({floor}) 이후여야 합니다.",
                              hint="만기 전까지는 이전 계약 조건이 적용되고, 새 조건은 만기일부터 적용됩니다.")
        raise DomainError(f"입주일은 현재 입주자의 퇴실 예정일({floor}) 이후여야 합니다.",
                          hint="전입일과 전출일은 같은 날까지 가능합니다. 퇴실 예정일 이후 날짜를 입력해 주세요.")

def agree_step3(self: CoSignSession, role: str, expected_revision: Optional[int] = None, app_source: str = "rent3_contract") -> CoSignSession:
    self._ensure_ready_for_steps()
    self._ensure_risk_ok()
    self._check_pre_contract_move_in(self.terms)
    from ..contract_chat import ensure_no_open_requests   # 답하지 않은 제안·수정 요청이 있으면 최종 동의 불가
    ensure_no_open_requests(self.id)
    if expected_revision is not None and expected_revision != self.revision:
        raise Conflict(
            f"계약 조건이 방금 수정되었습니다 (현재 리비전 v{self.revision}).",
            hint="변경된 최신 계약 조건을 다시 확인한 뒤 동의 버튼을 눌러 주세요.",
        )
    if not self.terms.get("tenant_name") or not self.terms.get("tenant_phone"):
        raise DomainError("임차인 성명과 연락처는 필수 입력 항목입니다.", hint="Step 3에서 임차인 인적사항을 먼저 입력해 주세요.")

    if role == "lessor":
        self.step3_lessor_agreed = True
    else:
        self.step3_tenant_agreed = True

    if self.step3_lessor_agreed and self.step3_tenant_agreed and self.current_step < 4:
        self.current_step = 4
    return self.save(action=f"step3_agreed_by_{role}_rev_{self.revision}", app_source=app_source)

def sign_step4(
    self: CoSignSession,
    role: str,
    signature: str,
    expected_revision: Optional[int] = None,
    app_source: str = "rent3_contract",
) -> CoSignSession:
    """Step 4 자필 전자서명 — 본인인증·상호 신분증 확인·등기부등본 확인·계약조건 합의가 모두 완료된 뒤에만 허용!"""
    self._ensure_ready_for_steps()
    self._ensure_risk_ok()
    if not (self.lessor_login_verified and self.tenant_login_verified):
        raise DomainError(
            "임대인과 임차인 모두 로그인 본인인증이 완료되어야 전자서명할 수 있습니다.",
            hint="양측 모두 로그인 및 호출 응답을 완료해 주세요.",
        )
    if not (self.lessor_checked_tenant_id and self.tenant_checked_lessor_id):
        raise DomainError(
            "서로 상대방의 신분증을 확인하여 본인 확인 체크를 완료해야 전자서명할 수 있습니다.",
            hint="Step 1에서 임대인·임차인 모두 상대방 신분증 대조 확인에 체크해 주세요.",
        )
    if not (self.step1_lessor_confirmed and self.step1_tenant_locked):
        raise DomainError(
            "등기부등본을 확인한 뒤에만 전자서명할 수 있습니다.",
            hint="Step 1에서 등기부등본 권리관계 제시 및 임차인 열람 확인을 완료해 주세요.",
        )
    if not (self.step3_lessor_agreed and self.step3_tenant_agreed):
        raise DomainError("양측 모두 Step 3 계약 조건 합의 잠금을 완료해야 전자서명할 수 있습니다.")
    if expected_revision is not None and expected_revision != self.revision:
        raise Conflict("계약 리비전이 변경되어 서명을 진행할 수 없습니다. 조건을 다시 확인해 주세요.")
    sig = (signature or "").strip()
    if not sig:
        raise DomainError("전자서명 데이터가 비어 있습니다.", hint="서명 패드에 자필 서명을 입력해 주세요.")

    if role == "lessor":
        self.lessor_verified = True
        self.lessor_signature = sig
        self.lessor_signed_at = now_iso()
    else:
        self.tenant_verified = True
        self.tenant_signature = sig
        self.tenant_signed_at = now_iso()

    if self.lessor_signature and self.tenant_signature:
        self.integrity_hash = self.compute_integrity_hash()
        if self.current_step < 5:
            self.current_step = 5
        if self.mode == "RENEWAL":
            self.save(action=f"step4_signed_by_{role}", app_source=app_source)
            self.complete_step5_and_seal(payment_method="RENEWAL_CARRYOVER", app_source=app_source)
            return self

    return self.save(action=f"step4_signed_by_{role}", app_source=app_source)

def complete_step5_and_seal(
    self: CoSignSession,
    payment_method: str = "TRANSFER",
    app_source: str = "rent3_contract",
) -> Dict[str, Any]:
    """계약금 입금 확인 -> 공동 JSON Data Pool의 모든 연관 객체 실시간 원자적 업데이트!"""
    if self.status == "COMPLETED" and self.created_contract_id:
        ct = Contract.require(self.created_contract_id)
        return {"session": self.model_dump(), "contract": ct.model_dump(), "already_completed": True}

    self._ensure_ready_for_steps()
    self._ensure_risk_ok()
    if not (self.lessor_signature and self.tenant_signature):
        raise DomainError("임대인과 임차인 양측 전자서명이 모두 완료되어야 최종 체결할 수 있습니다.")
    if not self.tenant_recheck_at and self.mode != "RENEWAL":
        raise DomainError(
            "임차인의 체결 직전 등기부 재열람 확인이 필요합니다.",
            hint="계약 후 잔금 전에 근저당·소유자가 바뀌는 사고를 막기 위해, 임차인이 인터넷등기소에서 다시 열람한 뒤 확인해야 체결할 수 있습니다.",
        )

    room = Room.require(self.id_room)
    t_info = self.terms

    # 사전 계약: 입주 중인 전출 예정 호실에 미리 체결하는 경우 현재 입주자 기록을 건드리지 않고 '예약 계약'(SCHEDULED)으로 만든다.
    # 퇴실이 확정되어 방이 비는 순간(Room.vacate) 이 계약이 ACTIVE 가 되고 임차인이 입주한다.
    reserve = (self.mode != "RENEWAL") and room.is_occupied
    if reserve:
        if room.reservation() is not None:
            raise Conflict(f"{room.room_no}호는 이미 다음 입주자와 예약 계약이 체결되었습니다.", hint="예약이 취소된 뒤에 다시 시도해 주세요.")
        if not room.pre_moveout_notice:
            raise DomainError(f"{room.room_no}호는 입주 중이며 전출 예정으로 공개되지 않아 미리 계약할 수 없습니다.")
        self._check_pre_contract_move_in(t_info)

    # 연장 재계약을 만기 전에 미리 체결하는 경우: 새 계약은 '예약'(SCHEDULED)으로 두고 이전 계약·호실 값은 그대로 둔다.
    # 새 조건의 시작일(이전 계약 만기일)이 되면 이전 계약이 RENEWED, 새 계약이 ACTIVE 로 바뀌고 호실 조건이 새 값으로 바뀐다(ExpiryRenewalCheck.activate_due).
    renewal_later = False
    prev_for_renewal = Contract.get(self.prev_contract_id) if (self.mode == "RENEWAL" and self.prev_contract_id) else None
    if prev_for_renewal is not None:
        self._check_pre_contract_move_in(t_info)
        start0 = str(t_info.get("move_in_date") or prev_for_renewal.expiry_date or "")
        renewal_later = prev_for_renewal.status == "ACTIVE" and bool(start0) and start0 > date_today().isoformat()
        if renewal_later and room.reservation() is not None:
            raise Conflict(f"{room.room_no}호는 이미 연장 재계약이 체결되었습니다.", hint="체결된 재계약을 취소한 뒤에 다시 시도해 주세요.")

    # 1) 임차인 객체 생성 또는 갱신 -> users.json 즉시 업데이트
    tenant = (
        (Tenant.get(self.id_tenant) if self.id_tenant else None)
        or (Tenant.first(phone=t_info["tenant_phone"]) if t_info.get("tenant_phone") else None)
    )
    if not tenant:
        tenant = Tenant(
            name=t_info["tenant_name"],
            phone=t_info["tenant_phone"],
            email=t_info.get("tenant_email", ""),
            business_number=t_info.get("tenant_business_number", ""),
            company_name=t_info.get("tenant_company_name", ""),
            ceo_name=t_info.get("tenant_ceo_name", ""),
            car_number=t_info.get("tenant_car_number", ""),
            doorlock_pwd="" if reserve else room.doorlock_pwd,
            branch=self.branch,
            id_building=self.id_building,
            current_room_id="" if reserve else room.id,
            room_no="" if reserve else room.room_no,
            status="PROSPECT" if reserve else "ACTIVE",
        )
    else:
        tenant.name = t_info["tenant_name"]
        tenant.email = t_info.get("tenant_email", tenant.email)
        if t_info.get("tenant_business_number") is not None and str(t_info.get("tenant_business_number")).strip():
            tenant.business_number = str(t_info["tenant_business_number"]).strip()
        if t_info.get("tenant_company_name") is not None and str(t_info.get("tenant_company_name")).strip():
            tenant.company_name = str(t_info["tenant_company_name"]).strip()
        if t_info.get("tenant_ceo_name") is not None and str(t_info.get("tenant_ceo_name")).strip():
            tenant.ceo_name = str(t_info["tenant_ceo_name"]).strip()
        if t_info.get("tenant_car_number"):
            tenant.car_number = t_info["tenant_car_number"]
        if not reserve:
            tenant.doorlock_pwd = room.doorlock_pwd
            tenant.branch = self.branch
            tenant.id_building = self.id_building
            tenant.current_room_id = room.id
            tenant.room_no = room.room_no
            tenant.status = "ACTIVE"
    tenant.save(action="tenant_reserved_by_cosign" if reserve else "tenant_activated_by_cosign", app_source=app_source)
    self.id_tenant = tenant.id

    # 재계약인 경우 이전 계약 RENEWED 마감(미리 체결한 연장은 시작일에 마감한다)
    if self.mode == "RENEWAL" and self.prev_contract_id and not renewal_later:
        old_ct = Contract.get(self.prev_contract_id)
        if old_ct:
            old_ct.status = "RENEWED"
            old_ct.ended_at = date_today().isoformat()
            old_ct.save(action="contract_renewed_replaced", app_source=app_source)

    # 2) Contract 원장 객체 생성 -> contracts.json 즉시 업데이트
    move_in = t_info.get("move_in_date") or date_today().isoformat()
    months = int(t_info.get("lease_term_months", 12))
    expiry = add_months_str(move_in, months)

    from .. import contract_chat
    contract_chat.system_note(self, "계약이 체결되었습니다. 이 대화는 이제 읽기 전용으로 보관됩니다.", step=5)
    chat_digest, chat_digest_count = contract_chat.compute_digest(self.id)   # 안내 글까지 포함해 체결 시점의 협상 기록을 봉인
    ct = Contract(
        branch=self.branch,
        id_building=self.id_building,
        id_room=room.id,
        room_no=room.room_no,
        id_lessor=self.id_lessor,
        lessor_name=self.lessor_name,
        id_tenant=tenant.id,
        tenant_name=tenant.name,
        tenant_phone=tenant.phone,
        tax_invoice_requested=bool(t_info.get("tax_invoice_requested") or t_info.get("tenant_business_number")),
        tenant_business_number=tenant.business_number,
        tenant_company_name=tenant.company_name,
        tenant_ceo_name=tenant.ceo_name,
        tenant_email=tenant.email,
        contract_date=date_today().isoformat(),
        move_in_date=move_in,
        expiry_date=expiry,
        lease_term_months=months,
        lease_type=t_info.get("lease_type", "월세"),
        pay_timing=t_info.get("pay_timing", "후불"),
        vat_target=bool(t_info.get("vat_target") or t_info.get("tax_invoice_requested") or t_info.get("tenant_business_number")),
        vat_separate=bool(t_info.get("vat_separate", True)),
        contract_deposit=int(t_info.get("contract_deposit", 0 if self.mode == "RENEWAL" else (int(t_info.get("deposit", room.deposit) or 0) // 10))),
        keys_released=(self.mode == "RENEWAL"),  # 재계약은 기존 입주자 도어락 유지, 신규는 잔금 입금 확인 후 교부
        insurance_proof_required=bool(self.needs_insurance_refusal_clause(t_info) and (self.tenant_registry or {}).get("guarantee_status") == "가입 예정"),
        mortgage_cleanup_required=bool(t_info.get("mortgage_cleanup_clause")),  # 잔금 시 근저당 말소 특약 — 잔금일에 말소 확인이 있어야 교부
        deposit=int(t_info.get("deposit", room.deposit)),
        monthly_rent=int(t_info.get("monthly_rent", room.monthly_rent)),
        maintenance_fee=int(t_info.get("maintenance_fee", room.maintenance_fee)),
        cleaning_fee=int(t_info.get("cleaning_fee", 0) or 0),
        special_terms=t_info.get("special_terms", ""),
        option_snapshot=self.option_checks,
        registry_snapshot={
            **self.registry_data,
            "lessor_login_verified": self.lessor_login_verified,
            "tenant_login_verified": self.tenant_login_verified,
            "lessor_checked_tenant_id": self.lessor_checked_tenant_id,
            "lessor_id_checked_at": self.lessor_id_checked_at,
            "tenant_checked_lessor_id": self.tenant_checked_lessor_id,
            "tenant_id_checked_at": self.tenant_id_checked_at,
            "tenant_direct_registry": self.tenant_registry,
            "tenant_identity": self._tenant_identity_evidence(),
            "tenant_recheck_at": self.tenant_recheck_at,
            "registry_attachment_sha256": self.registry_attachment_sha256,
            "facility_photos": [{"id": p["id"], "sha256": p["sha256"], "caption": p["caption"], "by_role": p["by_role"],
                                 "uploaded_at": p["uploaded_at"]} for p in self.facility_photos],
        },
        lessor_signature=self.lessor_signature,
        tenant_signature=self.tenant_signature,
        integrity_hash=self.integrity_hash,
        cosign_session_id=self.id,
        chat_digest=chat_digest,
        chat_digest_count=chat_digest_count,
        status="SCHEDULED" if (reserve or renewal_later) else "ACTIVE",
        renews_contract_id=self.prev_contract_id if (self.mode == "RENEWAL" and self.prev_contract_id) else "",
    )
    ct.vat = ct.compute_vat()
    ct.save(action="contract_sealed_from_cosign", app_source=app_source)

    # 3) Room 호실 상태 입주 전환 (공실 광고 자동 비노출) -> rooms.json 즉시 업데이트
    if reserve or renewal_later:
        room.reserved_contract_id = ct.id   # 현재 입주자·계약은 그대로 두고 예약만 건다(새 입주자 예약은 광고를 내린다, 연장 예약은 호실 값 그대로)
        if reserve:
            room.is_published = False
        room.save(action="room_reserved_by_cosign" if reserve else "room_renewal_reserved_by_cosign", app_source=app_source)
    else:
        room.id_tenant = tenant.id
        room.active_contract_id = ct.id
        room.deposit = ct.deposit
        room.monthly_rent = ct.monthly_rent
        room.maintenance_fee = ct.maintenance_fee
        room.cleaning_fee = ct.cleaning_fee or 0
        room.lease_type = ct.lease_type
        room.pay_timing = ct.pay_timing
        room.pre_moveout_notice = False
        room.next_listing = {}                  # 매물 등록 때의 임시 조건은 계약 값으로 덮였으니 삭제
        room.next_terms = {}                    # 임대인이 저장해 둔 다음 계약 조건도 이 계약으로 쓰였으니 비운다
        room.available_from = "즉시입주"
        room.is_published = False
        room.save(action="room_assigned_by_cosign", app_source=app_source)

    # 4) 계약금 수납 기록 생성 -> payments.json 즉시 업데이트
    c_dep = ct.contract_deposit
    if c_dep > 0:
        Pay(
            branch=self.branch,
            id_building=self.id_building,
            id_room=room.id,
            room_no=room.room_no,
            id_contract=ct.id,
            id_tenant=tenant.id,
            payer_name=tenant.name,
            billing_month=move_in[:7],
            paid_date=date_today().isoformat(),
            rent_amount=0,
            maintenance_amount=0,
            vat_amount=0,
            total_amount=c_dep,
            payment_kind="CONTRACT_DEPOSIT",
            method=payment_method,
            idempotency_key=f"cosign_dep:{self.id}",
        ).save(action="contract_deposit_paid", app_source=app_source)

    # 5) 국토부 RTMS 전월세 신고 & 대법원 확정일자 즉시 부여 -> ops.json & contracts.json 업데이트
    rtms = RtmsReport.auto_file_for_contract(ct, app_source=app_source)

    # 6) 세션 완료 봉인 -> cosign_sessions.json 업데이트
    self.deposit_paid_confirmed = True
    self.payment_method = payment_method
    self.created_contract_id = ct.id
    self.fixed_date_no = rtms.fixed_date_no
    self.status = "COMPLETED"
    self.completed_at = now_iso()
    self.save(action="cosign_completed_and_sealed", app_source=app_source)

    if not reserve:        # 예약 계약의 체결 알림은 현재 입주자도 보는 호실 채팅에 남기지 않는다
        DirectChat(
            branch=self.branch,
            id_room=room.id,
            room_no=room.room_no,
            sender_role="system",
            sender_name="전자계약 체결 알림",
            message=(
                f"[🎉 임대차 계약 최종 체결 완료] {room.room_no}호 계약원장({ct.id})이 봉인되었습니다. "
                f"계약 접수번호는 {rtms.fixed_date_no} 입니다. (법적 확정일자는 아닙니다 — 주민센터·등기소에서 별도로 받으세요)"
                + (f" 연장 계약의 새 조건은 {ct.move_in_date}부터 적용되며, 그때까지는 현재 계약 조건이 적용됩니다." if renewal_later else "")
            ),
        ).save(action="chat_auto_sent", app_source=app_source)

    return {
        "session": self.model_dump(),
        "contract": ct.model_dump(),
        "tenant": tenant.model_dump(),
        "room": room.model_dump(),
        "rtms": rtms.model_dump(),
        "reserved": reserve,
        "renewal_scheduled": renewal_later,
    }

def _ensure_in_progress(self: CoSignSession) -> None:
    if self.status != "IN_PROGRESS":
        raise Conflict(
            f"이미 종료되었거나 체결 완료된 계약 세션입니다 (상태: {self.status}).",
            hint="체결된 계약서를 인쇄하거나 새 매물을 선택해 상대를 호출해 주세요.",
        )

def _ensure_ready_for_steps(self: CoSignSession) -> None:
    self._ensure_in_progress()
    if self.call_status == "CALLING":
        callee_ko = "임차인" if self.initiated_by_role == "lessor" else "임대인"
        raise DomainError(
            f"상대방({callee_ko})이 아직 공동계약 호출에 응답하지 않았습니다.",
            hint=f"{callee_ko}이(가) 로그인 후 호출에 응답하면 계약 단계 진행이 시작됩니다.",
        )
