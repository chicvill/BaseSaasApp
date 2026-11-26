"""
apps/_rent3_core/cosign.py — 공동 임대계약 객체(`CoSignSession`) 스키마 및 파사드 (`domains/cosign_*.py` 조합).

저장 파일: `data/rent3_pool/cosign_sessions.json`
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from .domains.cosign_registry import (
    CLEANUP_CLAUSE,
    HIGH_DEPOSIT,
    INSURANCE_CLAUSE,
    INSURED_STATUS,
    LOW_DEPOSIT,
    RegistryBlocked,
    _CoSignRegistryMixin,
    re_is_date,
)
from .domains.cosign_session import MAX_LIVE_PER_TENANT, _CoSignSessionMixin
from .domains.cosign_steps import _CoSignStepsMixin
from .pool import PoolObj, now_iso

# 진행 중인 계약이 이 시간 동안 아무 변화가 없으면 자동 해제(취소)한다. (테스트·E2E 하네스에서 동적 변경 가능)
IDLE_EXPIRE = timedelta(hours=24)
IDLE_EXPIRE_DRAFT = timedelta(hours=72)  # 대면 전에 미리 작성한 계약은 약속 시간을 감안해 더 오래 보존


class CoSignSession(_CoSignRegistryMixin, _CoSignSessionMixin, _CoSignStepsMixin, PoolObj):
    normalize_phones = False  # 무결성 해시 대상: 입력 원문 보존
    """임대인·임차인 실시간 호출·응답 및 상호 신분·등기 확인 기반 공동 임대계약 세션 객체."""
    collection: ClassVar[str] = "cosign_sessions"
    label: ClassVar[str] = "공동 임대계약 세션"

    PREFILL_LESSOR_KEYS: ClassVar[tuple] = (
        "move_in_date", "lease_term_months", "deposit", "monthly_rent", "maintenance_fee", "cleaning_fee",
        "contract_deposit", "pay_timing", "lease_type", "special_terms", "mortgage_cleanup_clause",
        "vat_target", "vat_separate", "tax_invoice_requested",
        "tenant_business_number", "tenant_company_name", "tenant_ceo_name", "tenant_email",
    )
    PREFILL_TENANT_KEYS: ClassVar[tuple] = (
        "tenant_email", "tenant_car_number", "tax_invoice_requested",
        "tenant_business_number", "tenant_company_name", "tenant_ceo_name",
    )
    MAX_FACILITY_PHOTOS: ClassVar[int] = 12
    FACILITY_MIME: ClassVar[Dict[str, str]] = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp"}
    # 3단계에서 '제안 → 상대 수락'으로만 바뀌는 항목 (POST /terms 로는 직접 못 바꾼다 — contract_chat.respond 가 update_terms 를 부른다)
    TERMS_LOCKED_FIELDS: ClassVar[tuple] = ("deposit", "contract_deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "move_in_date", "lease_term_months")

    id: str = Field(default_factory=lambda: f"cs_{uuid4().hex[:8]}")
    session_code: str = Field(default_factory=lambda: f"{random.randint(100000, 999999)}")
    mode: str = "NEW"                        # NEW (신규계약) / RENEWAL (조건변경 재계약)
    prev_contract_id: str = ""
    reserve_after: str = ""                  # 사전 계약(입주 중인 전출 예정 호실): 새 입주일의 가장 빠른 날 = 현재 입주자의 예상 퇴실일
    branch: str = "main"
    id_building: str = ""
    building_name: str = ""
    building_address: str = ""
    id_room: str = ""
    room_no: str = ""
    id_lessor: str = ""
    lessor_name: str = ""
    lessor_phone: str = ""
    lessor_account: str = ""
    id_tenant: str = ""

    # 호출·응답 핸드셰이크 및 로그인 본인인증 상태
    initiated_by_role: str = "lessor"        # lessor / tenant (먼저 매물을 선택해 호출한 주체)
    call_status: str = "CONNECTED"           # CALLING (상대방 응답 대기) / CONNECTED (양측 응답·접속 완료)
    called_at: str = Field(default_factory=now_iso)
    responded_at: str = ""
    lessor_login_verified: bool = True       # 임대인 로그인 본인인증 완료 여부
    tenant_login_verified: bool = True       # 임차인 로그인 본인인증 완료 여부

    current_step: int = 1                    # 1..5
    status: str = "IN_PROGRESS"              # IN_PROGRESS / COMPLETED / CANCELLED
    revision: int = 1                        # 조건 변경 시마다 +1 증가 (위변조 원천 차단)

    # Step 1: 상호 신분증 확인 체크 & 대법원 등기부등본 권리 분석
    lessor_checked_tenant_id: bool = False   # 임대인이 임차인 신분증을 대조·본인확인 체크했는지 여부
    lessor_id_checked_at: str = ""
    tenant_checked_lessor_id: bool = False   # 임차인이 임대인 신분증을 대조·본인확인 체크했는지 여부
    tenant_id_checked_at: str = ""

    registry_data: Dict[str, Any] = Field(default_factory=dict)
    step1_lessor_confirmed: bool = False     # 임대인 등기부등본 제시/확인
    step1_tenant_locked: bool = False        # 임차인 등기부등본 열람/확인 잠금
    # 임차인이 인터넷등기소에서 '직접' 열람해 확인한 내용 (임대인이 제시한 서류와는 별개로 검증)
    tenant_registry: Dict[str, Any] = Field(default_factory=dict)
    registry_attachment_file: str = ""       # uploads/registry/<session>.<ext>
    registry_attachment_name: str = ""
    registry_attachment_sha256: str = ""
    tenant_recheck_at: str = ""              # 체결 직전 재열람(변동 없음) 확인 시각
    cancel_reason: str = ""                  # 취소 사유(당사자 취소·재열람 변동·자동 해제) — 상대방 화면에 그대로 보인다
    cancelled_by: str = ""                   # lessor / tenant / system
    cancelled_at: str = ""
    chat_purged_at: str = ""                 # 취소 후 90일(보관 기간) 만료로 계약방 대화를 지운 시각
    registry_ai_times: List[str] = Field(default_factory=list)  # AI 읽기 호출 시각(월별 집계용)
    registry_ai_calls: int = 0               # Gemini 등기부 읽기 호출 횟수 (비용 상한용)
    registry_ai_result: Dict[str, Any] = Field(default_factory=dict)  # 마지막 AI 읽기 결과(참고용)

    # Step 2: 호실 옵션 및 시설 공동 점검표
    option_checks: List[Dict[str, Any]] = Field(default_factory=list)
    facility_notes: str = "도배·장판·수압·배수·도어락 현장 동반 확인 이상 없음"
    draft_prefilled_at: str = ""            # 대면 전 사전 작성 시각(있으면 자동 해제 기한이 72시간)
    facility_photos: List[Dict[str, Any]] = Field(default_factory=list)  # 시설 점검 사진(당사자가 현장에서 촬영): id·file·sha256·caption·by_role·uploaded_at
    step2_lessor_confirmed: bool = False
    step2_tenant_locked: bool = False

    # Step 3: 계약 조건 및 특약 공동 작성
    terms: Dict[str, Any] = Field(default_factory=dict)
    step3_lessor_agreed: bool = False
    step3_tenant_agreed: bool = False

    # Step 4: 양측 본인확인 및 자필 전자서명
    lessor_verified: bool = True
    tenant_verified: bool = False
    lessor_signature: str = ""
    lessor_signed_at: str = ""
    tenant_signature: str = ""
    tenant_signed_at: str = ""
    integrity_hash: str = ""

    # Step 5: 계약금 결제 확인 및 최종 계약 원장 생성 결과
    deposit_paid_confirmed: bool = False
    payment_method: str = "TRANSFER"
    created_contract_id: str = ""
    fixed_date_no: str = ""
    completed_at: str = ""

    def is_idle_expired(self, now: Optional[datetime] = None) -> bool:
        """진행 중인 세션이 IDLE_EXPIRE 동안 무응답(변화 없음)이면 True."""
        if self.status != "IN_PROGRESS":
            return False
        try:
            last = datetime.fromisoformat(self.updated_at)
        except (TypeError, ValueError):
            return False
        window = IDLE_EXPIRE_DRAFT if (self.draft_prefilled_at and self.call_status == "CALLING") else IDLE_EXPIRE
        return (now or datetime.now()) - last >= max(window, IDLE_EXPIRE)


__all__ = [
    "IDLE_EXPIRE",
    "IDLE_EXPIRE_DRAFT",
    "HIGH_DEPOSIT",
    "LOW_DEPOSIT",
    "INSURED_STATUS",
    "INSURANCE_CLAUSE",
    "CLEANUP_CLAUSE",
    "MAX_LIVE_PER_TENANT",
    "RegistryBlocked",
    "CoSignSession",
    "re_is_date",
]
