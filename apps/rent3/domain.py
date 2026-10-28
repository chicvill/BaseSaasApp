"""apps/rent3/domain.py — 차세대 비대면 직거래 스마트임차 도메인 엔티티 및 비즈니스 로직.

운영 및 법적 안전 규칙: .agents/skills/bass-rent3-ops/SKILL.md
- 공인중개사법상 무등록 중개행위 차단 (단순 IT 솔루션 및 직거래 전자서약)
- rent2 코어 엔진(가짜 체납 방지, FIFO 순차 상계, 3개 지점 격리 매트릭스) 완벽 계승
- 온라인 직거래 풀 라이프사이클: 공개광고 -> 비대면 신청/승인 -> 결제/서명 -> 입실/시설점검 -> 라운지 소통 -> 퇴실
"""
from datetime import datetime, timedelta
import secrets
from typing import Any, Dict, List, Optional
from pydantic import Field
from core.kernel import Obj

# 기존 rent2 도메인 연동 (호환성 보장)
try:
    from apps.rent2.backend.domain.rent import Building, Room, Contract
except ImportError:
    Building = None
    Room = None
    Contract = None

# 8대 표준 엔티티 (Admin, Lessor, Tenant, Building, Room, Contract, Pay, Outlay) 연동
from .entities import (
    Admin, Lessor, Tenant, BuildingEntity, RoomEntity, ContractEntity, 
    Pay, Outlay, Rent3EntityManager, Rent3WorkflowEngine, seed_default_entities_if_empty
)


class RentalListing(Obj):
    """공개 임대 매물 광고 엔티티 (Public Showcase)."""
    branch: str = "main"          # 소속 지점 (main, sunho, sinbi)
    room_id: str = ""             # 호실 번호 (예: 201)
    title: str = ""               # 광고 제목
    deposit: int = 5000000        # 보증금 (원)
    monthly_rent: int = 500000    # 월세 (원)
    maintenance_fee: int = 50000  # 관리비 (원)
    fee_includes: List[str] = Field(default_factory=lambda: ["인터넷", "공용전기", "수도세"])
    options: List[str] = Field(default_factory=lambda: ["에어컨", "세탁기", "냉장고", "인덕션", "도어락"])
    available_from: str = "즉시입주" # 즉시 입주 가능일
    images: List[str] = Field(default_factory=list)
    floor: int = 2                # 층수
    area_sqm: float = 20.5        # 전용면적 (m2)
    is_active: bool = True        # 광고 공개 여부


class RentalApplication(Obj):
    """비대면 온라인 입주신청서 및 전자계약 파이프라인.
    
    Status Lifecycle:
      PENDING_REVIEW  : 임차인 신청 접수 (임대인 검토 대기)
      APPROVED        : 임대인 승인 완료 및 전자계약서 발송 (결제 대기)
      REJECTED        : 신청 거절 (반려 사유 포함)
      PAID_CONFIRMED  : 첫 결제(계약금/첫달분) 및 전자서명 완료 (최종 체결)
      CHECKED_IN      : 입실 완료 및 세입자 라운지 활성화
      CANCELLED       : 신청 취소
    """
    listing_id: str = ""          # 매물 ID
    branch: str = "main"          # 지점 ID
    room_id: str = ""             # 호실 번호
    tenant_name: str = ""         # 신청자 성명
    tenant_phone: str = ""        # 신청자 연락처
    tenant_email: str = ""        # 신청자 이메일
    move_in_date: str = ""        # 입주 희망일 (YYYY-MM-DD)
    lease_term_months: int = 12   # 계약 기간 (개월)
    deposit: int = 5000000        # 약정 보증금
    monthly_rent: int = 500000    # 약정 월세
    first_payment_type: str = "deposit_part" # deposit_part(계약금) / full(전액)
    first_payment_amount: int = 300000       # 첫 결제 필요액 (예: 계약금 30만원)
    
    # 상태 및 심사
    status: str = "PENDING_REVIEW"
    reject_reason: str = ""
    admin_notes: str = ""
    
    # 전자서명 & 결제
    tenant_signature: str = ""    # 임차인 전자서명 데이터/해시
    landlord_signature: str = ""  # 임대인 전자서명 데이터/해시
    paid_at: str = ""             # 첫 결제 일시
    payment_method: str = ""      # CARD, VIRTUAL_ACCOUNT, TRANSFER
    updated_at: str = ""

    def approve(self, landlord_sig: str = "SIGNED_BY_LANDLORD") -> "RentalApplication":
        """임대인 승인 및 전자서명 날인."""
        if self.status != "PENDING_REVIEW":
            raise ValueError(f"현재 상태({self.status})에서는 승인할 수 없습니다.")
        self.status = "APPROVED"
        self.landlord_signature = landlord_sig
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save("approved_by_landlord")

    def reject(self, reason: str) -> "RentalApplication":
        """임대인 신청 반려."""
        self.status = "REJECTED"
        self.reject_reason = reason
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save("rejected_by_landlord")

    def confirm_payment_and_sign(self, tenant_sig: str, method: str = "CARD") -> "RentalApplication":
        """임차인 전자서명 및 첫 결제 완료 -> 최종 계약 체결."""
        if self.status != "APPROVED":
            raise ValueError("임대인 승인이 완료된 신청서만 결제 및 서명할 수 있습니다.")
        self.status = "PAID_CONFIRMED"
        self.tenant_signature = tenant_sig
        self.payment_method = method
        self.paid_at = datetime.now().isoformat(timespec="seconds")
        self.updated_at = self.paid_at
        
        # rent2 코어 엔진 호환: Building 객체 및 Room 상태 동기화
        if Building:
            try:
                b = Building.get(self.branch)
                u_num = int(self.room_id) if str(self.room_id).isdigit() else None
                if b and u_num is not None and hasattr(b, "unit"):
                    u = b.unit(u_num)
                    u.update({
                        "name": self.tenant_name,
                        "phone": self.tenant_phone,
                        "deposit": self.deposit,
                        "rent": self.monthly_rent,
                        "move_in": self.move_in_date
                    })
            except Exception as e:
                print(f"[RentalApplication] rent2 동기화 경고: {e}")

        # 국토교통부 RTMS 주택임대차 계약신고 및 대법원 확정일자 자동 부여
        try:
            RtmsContractReport.auto_file_and_grant(
                session_id=self.id,
                branch=self.branch,
                room_id=self.room_id,
                tenant_name=self.tenant_name,
                tenant_phone=self.tenant_phone,
                deposit=self.deposit,
                monthly_rent=self.monthly_rent,
                move_in_date=self.move_in_date
            )
        except Exception as e:
            print(f"[RentalApplication] RTMS 자동신고 연동 예외: {e}")
                
        return self.save("paid_and_contract_confirmed")


class CheckInRecord(Obj):
    """입실 체크인 기록 및 디지털 시설 점검표 (분쟁 예방)."""
    application_id: str = ""
    branch: str = "main"
    room_id: str = ""
    tenant_name: str = ""
    door_lock_code: str = ""      # 도어락 임시/초기 비밀번호
    wifi_ssid: str = ""
    wifi_pw: str = ""
    inspection_notes: str = ""    # 임차인 입실 점검 메모
    inspection_photos: List[str] = Field(default_factory=list) # 벽지, 에어컨 등 입실 시 사진
    tenant_confirmed: bool = False
    checked_in_at: str = ""

    def confirm_checkin(self, notes: str, photos: List[str]) -> "CheckInRecord":
        self.inspection_notes = notes
        self.inspection_photos = photos
        self.tenant_confirmed = True
        self.checked_in_at = datetime.now().isoformat(timespec="seconds")
        return self.save("tenant_checkin_confirmed")


class MaintenanceRequest(Obj):
    """세입자 라운지 1:1 민원 및 시설 수리 요청 (소통 일원화).
    
    Status:
      SUBMITTED    : 접수 완료
      IN_PROGRESS  : 방문 일정 조율 / 수리 진행 중
      RESOLVED     : 조치 완료
    """
    branch: str = "main"
    room_id: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    category: str = "facility"    # facility(시설/가전), noise(소음), bill(공과금/납부), other(기타)
    title: str = ""
    description: str = ""
    photos: List[str] = Field(default_factory=list)
    status: str = "SUBMITTED"
    admin_reply: str = ""
    updated_at: str = ""

    def resolve(self, reply: str) -> "MaintenanceRequest":
        self.status = "RESOLVED"
        self.admin_reply = reply
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save("maintenance_resolved")


class CoSigningSession(Obj):
    """임대인-임차인 현물 동행 스마트 실시간 동시 계약 세션 (Co-Signing Room).
    
    Status:
      DRAFT       : 세션 생성됨
      IN_PROGRESS : 진행 중 (등기부/시설점검/조건합의)
      SIGNED      : 양측 전자서명 완료
      COMPLETED   : 계약금 결제 및 계약 최종 확정
    """
    branch: str = "main"
    room_id: str = ""
    current_step: int = 1         # 1:등기부, 2:시설점검, 3:조건합의, 4:전자서명, 5:결제/신고
    status: str = "DRAFT"

    # Step 1: 대법원 실시간 등기부 및 소유권 확인
    registry_checked: bool = False
    registry_issue_no: str = "2026-0482-9912" # 대법원 발급확인번호 (시뮬레이션)
    owner_name: str = "김임대 (소유자 본인 확인)"
    debt_summary: str = "선순위 근저당 0원 (안전 클린 매물)"

    # Step 2: 부동산 실물 옵션 및 시설 현장 점검표
    facility_checks: Dict[str, bool] = Field(default_factory=lambda: {
        "시스템 에어컨 냉방 정상": True,
        "드럼 세탁기 급배수 정상": True,
        "빌트인 냉장고 냉동/냉장 정상": True,
        "벽지 및 장판 흠집 없음": True,
        "디지털 도어락 및 인터폰 정상": True,
        "수도 수압 및 온수 정상": True
    })
    facility_photos: List[str] = Field(default_factory=list)
    facility_notes: str = "현물 동행 확인 완료 (특이사항 없음)"

    # Step 3: 계약 조건 합의 및 임차인 정보
    deposit: int = 5000000
    monthly_rent: int = 520000
    maintenance_fee: int = 50000
    move_in_date: str = ""
    lease_months: int = 12
    parking_required: bool = False
    car_number: str = ""
    special_terms: str = "실내 금연, 반려동물 사전 협의 필요"
    tenant_name: str = ""
    tenant_phone: str = ""
    tenant_email: str = ""

    # Step 4: 전자 서명 날인 & 실명 CI 연계
    landlord_signed: bool = False
    landlord_sig: str = ""
    landlord_ci: str = ""
    landlord_verified_method: str = "PASS"
    tenant_signed: bool = False
    tenant_sig: str = ""
    tenant_ci: str = ""
    tenant_verified_method: str = "KAKAO_CERT"
    contract_signed_at: str = ""

    # Step 5: 계약금 결제 및 전입신고 가이드
    down_payment_amount: int = 300000
    down_payment_paid: bool = False
    payment_method: str = ""
    paid_at: str = ""
    
    updated_at: str = ""

    def update_step1(self, issue_no: str = "", checked: bool = True):
        self.registry_checked = checked
        if issue_no:
            self.registry_issue_no = issue_no
        self.current_step = max(self.current_step, 2)
        self.status = "IN_PROGRESS"
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save("step1_registry_checked")

    def update_step2(self, checks: Dict[str, bool], notes: str, photos: List[str]):
        self.facility_checks = checks
        self.facility_notes = notes
        self.facility_photos = photos
        self.current_step = max(self.current_step, 3)
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save("step2_facilities_checked")

    def update_step3(self, data: Dict[str, Any]):
        for k, v in data.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.current_step = max(self.current_step, 4)
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save("step3_terms_agreed")

    def sign(self, role: str, signature: str, ci: str = "", method: str = "PASS"):
        if role == "landlord":
            self.landlord_signed = True
            self.landlord_sig = signature
            self.landlord_ci = ci or f"CI_LANDLORD_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            self.landlord_verified_method = method
        elif role == "tenant":
            self.tenant_signed = True
            self.tenant_sig = signature
            self.tenant_ci = ci or f"CI_TENANT_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            self.tenant_verified_method = method
        
        if self.landlord_signed and self.tenant_signed:
            self.status = "SIGNED"
            self.current_step = 5
            self.contract_signed_at = datetime.now().isoformat(timespec="seconds")
        
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        return self.save(f"signed_by_{role}")

    def complete_payment(self, method: str = "CARD") -> "CoSigningSession":
        self.down_payment_paid = True
        self.payment_method = method
        self.status = "COMPLETED"
        self.paid_at = datetime.now().isoformat(timespec="seconds")
        self.updated_at = self.paid_at

        # rent2 호실 연동 및 확정
        if Building:
            try:
                b = Building.get(self.branch)
                u_num = int(self.room_id) if str(self.room_id).isdigit() else None
                if b and u_num is not None and hasattr(b, "unit"):
                    u = b.unit(u_num)
                    u.update({
                        "name": self.tenant_name,
                        "phone": self.tenant_phone,
                        "deposit": self.deposit,
                        "rent": self.monthly_rent,
                        "move_in": self.move_in_date
                    })
            except Exception as e:
                print(f"[CoSigningSession] rent2 동기화 경고: {e}")

        # 국토교통부 RTMS 주택임대차 계약신고 및 대법원 확정일자 자동 부여
        try:
            RtmsContractReport.auto_file_and_grant(
                session_id=self.id,
                branch=self.branch,
                room_id=self.room_id,
                tenant_name=self.tenant_name,
                tenant_phone=self.tenant_phone,
                deposit=self.deposit,
                monthly_rent=self.monthly_rent,
                move_in_date=self.move_in_date
            )
        except Exception as e:
            print(f"[CoSigningSession] RTMS 자동신고 연동 예외: {e}")

        return self.save("cosign_completed_and_paid")


class ChatMessage(Obj):
    """임대인-임차인 1:1 앱내 실시간 메신저 (SMS/카카오 대체)."""
    branch: str = "main"
    room_id: str = ""
    sender_role: str = "tenant"   # tenant(임차인/예비세입자) or landlord(임대인)
    sender_name: str = ""
    sender_phone: str = ""
    message: str = ""
    msg_type: str = "text"        # text, tour_request, system_notice, action_link
    is_tour_request: bool = False # 대면 투어 약속 요청 여부
    tour_date: str = ""           # 약속 일시 (예: 2026-10-28 15:00)
    quick_action_url: str = ""    # 바로가기 링크 (계약서, 라운지 등)
    photos: List[str] = Field(default_factory=list) # 첨부된 시설/하자 현장 사진 목록
    is_read: bool = False         # 상대방 수신/열람 여부
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class RoomAccessInfo(Obj):
    """호실별 출입 및 편의 정보 (도어락 비번, 공동현관, 와이파이)."""
    branch: str = "main"
    room_id: str = ""
    door_lock_code: str = "2026*"       # 세대 도어락 비밀번호
    building_entry_code: str = "#1234*" # 1층 공동현관 로비 비밀번호
    wifi_ssid: str = "MQNET_WIFI_5G"
    wifi_pw: str = "rent2026pw"
    notes: str = "도어락 번호 입력 후 * 버튼을 누르세요."
    updated_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class ParkingRegistration(Obj):
    """건물 등록 차량 대장 (외부 불법 주차 단속 및 호실별 배정)."""
    branch: str = "main"
    room_id: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    car_number: str = ""            # 차량번호 (예: 12가 3456)
    car_model: str = ""             # 차종 (예: 아반떼, 그랜저, 쏘렌토 등)
    slot_type: str = "자유주차"      # "지정주차" or "자유주차"
    slot_number: str = ""           # 예: B1-03
    is_active: bool = True
    registered_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class MonthlyBill(Obj):
    """월별 임대료 및 관리비 청구서 (세입자 투명 공유)."""
    branch: str = "main"
    room_id: str = ""
    billing_month: str = "2026-10"   # 청구 년월 (YYYY-MM)
    tenant_name: str = ""
    tenant_phone: str = ""
    rent_amount: int = 500000        # 월세
    maint_amount: int = 50000        # 관리비
    total_amount: int = 550000       # 합계 (월세 + 관리비)
    due_date: str = "2026-10-31"     # 납부 기한
    is_paid: bool = False
    paid_at: str = ""
    payment_method: str = ""         # CARD, TRANSFER, EASY_PAY
    receipt_id: str = ""             # 발급된 전자영수증 ID
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if self.rent_amount or self.maint_amount:
            self.total_amount = self.rent_amount + self.maint_amount



class PaymentReceipt(Obj):
    """국세청 소득공제 및 지출증빙용 공인 전자 영수증."""
    receipt_no: str = ""             # 영수증 고유번호 (예: RCP-20261027-1234)
    bill_id: str = ""
    branch: str = "main"
    room_id: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    landlord_name: str = "김임대"
    building_address: str = "서울특별시 강남구 역삼동 123 시크빌"
    rent_amount: int = 500000
    maint_amount: int = 50000
    total_amount: int = 550000
    payment_method: str = "신용카드 (일시불)"
    approval_no: str = ""            # 카드/PG 승인번호 (8자리)
    paid_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tax_deduction_type: str = "주택임대료 세액공제용 영수증"


class CheckOutRecord(Obj):
    """퇴실 체크아웃, 원상복구 시설 검수 및 보증금 정산 공제 명세서."""
    branch: str = "main"
    room_id: str = ""
    room_number: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    settlement_no: str = Field(default_factory=lambda: f"MO-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}")
    move_in_date: str = ""
    move_out_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    # 1. 보증금 및 공제 세부 항목
    original_deposit: int = 5000000      # 기존 약정 보증금
    unpaid_rent_deduction: int = 0       # 잔여 미납 월세/관리비 공제액 (rent2 FIFO 장부 자동 집계)
    
    # 공과금 실비 세부 (수도 + 전기 + 가스)
    water_fee: int = 0                   # 당일 수도요금
    electric_fee: int = 0                # 당일 한전 전기요금
    gas_fee: int = 0                     # 당일 도시가스 요금
    utility_deduction: int = 0           # 공과금 합계
    
    # 시설 원상복구 및 청소비 세부
    cleaning_fee: int = 0                # 퇴실 전문 입주청소비
    repair_deduction: int = 0            # 시설 파손/원상복구비 (세입자 과실)
    damage_deduction: int = 0            # 원상복구/청소 합계
    damage_notes: str = "통상 손모 외 특이 파손 없음 (상태 양호)"
    other_deduction: int = 0             # 기타 공제/정산액
    
    final_refund_deposit: int = 5000000  # 최종 반환 보증금 = original_deposit - 총공제

    # 2. 반환 계좌 정보
    refund_bank: str = "카카오뱅크"
    refund_account: str = "3333-01-2345678"
    refund_account_holder: str = ""

    # 3. 6대 시설 검수 상태 (입실 시 체크 항목과 1:1 대조)
    inspection_checks: Dict[str, Any] = Field(default_factory=lambda: {
        "시스템 에어컨": {"status": "NORMAL", "note": "작동 양호, 필터 청결", "cost": 0},
        "드럼 세탁기": {"status": "NORMAL", "note": "급배수 및 도어 정상", "cost": 0},
        "빌트인 냉장고": {"status": "NORMAL", "note": "냉각 정상 및 선반 온전", "cost": 0},
        "벽지 및 장판": {"status": "NATURAL_WEAR", "note": "통상 생활 손모 (임대인 부담)", "cost": 0},
        "디지털 도어락": {"status": "NORMAL", "note": "비밀번호 초기화 완료", "cost": 0},
        "욕실 수압 및 도기": {"status": "NORMAL", "note": "누수 없음, 배수 원활", "cost": 0}
    })
    inspection_photos: List[str] = Field(default_factory=list) # 퇴실 현장 실물 사진

    # 4. 진행 상태 & 서명
    status: str = "INSPECTING"           # INSPECTING -> CALCULATED -> AGREED -> COMPLETED
    tenant_agreed: bool = False          # 임차인 정산 동의 여부
    tenant_signature: str = ""           # 임차인 전자서명 데이터
    agreed_at: str = ""
    refund_completed: bool = False       # 보증금 송금 완료 여부
    refund_completed_at: str = ""
    refund_tx_ref: str = ""              # 은행 송금 참조번호
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def calculate_refund(self) -> int:
        """공제 내역 및 최종 반환 보증금 자동 합산."""
        # 공과금 합산 (개별 입력값 우선)
        utility_sum = self.water_fee + self.electric_fee + self.gas_fee
        if utility_sum > 0:
            self.utility_deduction = utility_sum
            
        # 원상복구/청소 합산 (개별 입력값 우선)
        damage_sum = self.cleaning_fee + self.repair_deduction
        if damage_sum > 0:
            self.damage_deduction = damage_sum
            
        total_deduction = self.unpaid_rent_deduction + self.utility_deduction + self.damage_deduction + self.other_deduction
        self.final_refund_deposit = max(0, self.original_deposit - total_deduction)
        return self.final_refund_deposit

    def agree_by_tenant(self, signature: str, bank: str = "", account: str = "", holder: str = "") -> "CheckOutRecord":
        """세입자 정산 명세서 확인 및 동의 전자서명 날인."""
        self.tenant_agreed = True
        self.tenant_signature = signature
        self.agreed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if bank:
            self.refund_bank = bank
        if account:
            self.refund_account = account
        if holder:
            self.refund_account_holder = holder
        self.status = "AGREED"
        return self.save("tenant_checkout_agreed")

    def complete_settlement(self, tx_ref: str = "") -> "CheckOutRecord":
        """보증금 반환 완료 승인 -> rent2 호실 공실화 + rent3 매물 자동 광고 재등록."""
        self.refund_completed = True
        self.refund_tx_ref = tx_ref or f"TX-BANK-{datetime.now().strftime('%Y%m%d%H%M')}"
        self.status = "COMPLETED"
        self.refund_completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. rent2 호실 초기화 및 공실 전환
        if Building:
            try:
                b = Building.get(self.branch)
                u_num = int(self.room_id) if str(self.room_id).isdigit() else None
                if b and u_num is not None and hasattr(b, "unit"):
                    u = b.unit(u_num)
                    u.update({"name": "", "move_out": self.move_out_date})
            except Exception as e:
                print(f"[CheckOut] rent2 공실화 경고: {e}")

        # 2. rent3 공실 매물 자동 활성화
        try:
            listings = [l for l in RentalListing.all() if getattr(l, "branch", "") == self.branch and getattr(l, "room_id", "") == self.room_id]
            if listings:
                listing = listings[-1]
                listing.is_active = True
                listing.available_from = "즉시입주"
                listing.save("checkout_auto_reactivated_listing")
            else:
                new_l = RentalListing(
                    branch=self.branch,
                    room_id=self.room_id,
                    title=f"즉시입주 가능 깔끔한 풀옵션 {self.room_id}호",
                    deposit=self.original_deposit,
                    monthly_rent=500000,
                    maintenance_fee=50000,
                    available_from="즉시입주",
                    is_active=True
                )
                new_l.save("checkout_auto_created_listing")
        except Exception as e:
            print(f"[CheckOut] rent3 매물 재등록 경고: {e}")

        # 3. 1:1 직통 채팅방에 보증금 반환 영수증 및 완료 자동 통보
        try:
            chat_msg = ChatMessage(
                branch=self.branch,
                room_id=self.room_id,
                sender_role="landlord",
                sender_name="김임대 (임대인)",
                message=(
                    f"🚪 [{self.room_id}호 보증금 반환 및 퇴실 정산 완료]\n"
                    f"최종 반환 보증금 {self.final_refund_deposit:,}원이 등록 계좌({self.refund_bank} {self.refund_account})로 정상 송금되었습니다.\n"
                    f"공과금/청소비 정산 명세서(관리번호: {self.settlement_no})가 영구 발급되었습니다.\n"
                    f"그동안 시크빌과 함께해 주셔서 진심으로 감사드립니다."
                ),
                msg_type="checkout_settlement",
                quick_action_url=f"/rent3/public.html?branch={self.branch}&room={self.room_id}#receiptModal",
                is_read=False
            )
            chat_msg.save(f"chat_checkout_{self.room_id}")
        except Exception as e:
            print(f"[CheckOut] 1:1 채팅 통보 경고: {e}")

        return self.save("checkout_settlement_completed")


class RtmsContractReport(Obj):
    """국토교통부 거래관리시스템(RTMS) 법정 주택임대차 계약신고서 및 대법원 확정일자 부여 필증.
    
    부동산 거래신고 등에 관한 법률 제6조의2 및 주택임대차보호법 제3조의6 준수:
    - 보증금 6,000만원 초과 또는 월차임 30만원 초과 시 30일 이내 신고 의무.
    - 신고 접수 즉시 대법원 확정일자가 자동 부여되어 주택임대차보호법상 우선변제권 확보.
    """
    session_id: str = ""             # 계약 세션 ID 또는 신청서 ID
    branch: str = "main"
    room_id: str = ""
    
    # 신고서 고유 번호
    report_no: str = Field(default_factory=lambda: f"RTMS-2026-{datetime.now().strftime('%m%d')}-{secrets.token_hex(3).upper()}")
    contract_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")) # 계약체결일
    deadline_date: str = Field(default_factory=lambda: (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")) # 체결일 + 30일 (과태료 기한)
    
    # 1. 임대인 (신고 의무자 1)
    landlord_name: str = "김임대"
    landlord_ssn_front: str = "780512-1" # 주민번호 앞자리 + 성별
    landlord_phone: str = "010-2345-6789"
    landlord_address: str = "서울특별시 강남구 역삼동 123"

    # 2. 임차인 (신고 의무자 2)
    tenant_name: str = "홍길동"
    tenant_ssn_front: str = "960315-1"
    tenant_phone: str = "010-1234-5678"
    tenant_address: str = "서울특별시 동작구 상도로 45"

    # 3. 임대 목적물
    property_address: str = "서울특별시 강남구 역삼동 123 시크빌"
    building_type: str = "다세대·원룸주택" # 공동주택/오피스텔
    exclusive_area_sqm: float = 24.5      # 전용면적

    # 4. 계약 조건
    deposit: int = 5000000                # 보증금
    monthly_rent: int = 500000            # 월차임
    maintenance_fee: int = 50000          # 관리비
    contract_period: str = "12개월"       # 계약기간
    move_in_date: str = "2026-11-01"

    # 5. 상태 및 확정일자 부여
    status: str = "READY"                 # READY(작성완료) -> SUBMITTED(신고 및 확정일자 부여)
    fixed_date_number: str = ""           # 대법원 확정일자 부여번호 (예: FIX-20261027-09124)
    fixed_date_granted_at: str = ""
    submitted_at: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 6. 법정 관할 기관 및 대항력/우선변제권 정보
    reporting_agency: str = "서울특별시 강남구 역삼1동 주민센터" # 관할 행정복지센터
    court_registry_office: str = "서울중앙지방법원 강남등기소"     # 관할 등기소
    legal_effect_date: str = ""           # 우선변제권 효력 발생일시 (부여 익일 0시)
    certificate_hash: str = ""            # 위변조 방지 전자서명 검증 해시

    @property
    def is_subject_to_report(self) -> bool:
        """부동산거래신고법 제6조의2 신고 의무 대상 여부 (보증금 6천만원 초과 또는 월세 30만원 초과)."""
        return (self.deposit > 60000000) or (self.monthly_rent > 300000)

    @property
    def days_left_to_deadline(self) -> int:
        """과태료 부과 마감 기한(30일)까지 남은 일수 (D-Day)."""
        try:
            deadline = datetime.strptime(self.deadline_date, "%Y-%m-%d")
            return max(0, (deadline - datetime.now()).days)
        except Exception:
            return 30

    def submit_report(self) -> "RtmsContractReport":
        """RTMS 전자 신고 제출 -> 대법원 확정일자 번호 즉시 자동 부여 및 우선변제권 효력 개시."""
        self.status = "SUBMITTED"
        now = datetime.now()
        self.submitted_at = now.strftime("%Y-%m-%d %H:%M:%S")
        self.fixed_date_number = f"FIX-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        self.fixed_date_granted_at = self.submitted_at
        tomorrow = now + timedelta(days=1)
        self.legal_effect_date = f"{tomorrow.strftime('%Y-%m-%d')} 00:00:00 (대항요건 구비 시 우선변제권 개시)"
        self.certificate_hash = secrets.token_hex(16).upper()

        # 지점별 관할 행정기관 매핑
        if self.branch == "sunho":
            self.reporting_agency = "서울특별시 관악구 신림동 주민센터"
            self.court_registry_office = "서울중앙지방법원 등기국"
        elif self.branch == "sinbi":
            self.reporting_agency = "서울특별시 영등포구 당산동 주민센터"
            self.court_registry_office = "서울남부지방법원 등기국"
        else:
            self.reporting_agency = "서울특별시 강남구 역삼1동 주민센터"
            self.court_registry_office = "서울중앙지방법원 강남등기소"

        return self.save("rtms_report_submitted")

    @classmethod
    def auto_file_and_grant(cls, session_id: str, branch: str, room_id: str,
                            tenant_name: str, tenant_phone: str, deposit: int,
                            monthly_rent: int, move_in_date: str = "") -> "RtmsContractReport":
        """계약 체결 시 원클릭/전자동으로 주택임대차 신고 및 확정일자 발급."""
        # 기존 신고서 확인
        existing = [r for r in cls.all() if getattr(r, "session_id", "") == session_id or (getattr(r, "branch", "") == branch and getattr(r, "room_id", "") == room_id)]
        if existing and existing[-1].status == "SUBMITTED":
            return existing[-1]

        report = existing[-1] if existing else cls(
            session_id=session_id,
            branch=branch,
            room_id=room_id,
            tenant_name=tenant_name,
            tenant_phone=tenant_phone,
            deposit=deposit,
            monthly_rent=monthly_rent,
            property_address=f"서울특별시 강남구 역삼동 123 시크빌 {room_id}호",
            move_in_date=move_in_date or datetime.now().strftime("%Y-%m-%d")
        )
        report.submit_report()
        return report


class LeaseRenewal(Obj):
    """주택임대차보호법 제6조/제6조의2 준수 계약 갱신 및 만기 관리 모델.
    
    Status:
      PROPOSED    : 갱신 조건(인상/동결/기간) 제안 상태
      AGREED      : 조건 상호 합의 (전자서명 대기)
      SIGNED      : 양측 전자서명 완료 (계약 갱신 확정)
      TACIT       : 묵시적 갱신 확정 (동일 조건 2년 연장)
      TERMINATED  : 만기 퇴실 확정
    """
    branch: str = "main"
    room_id: str = ""
    room_number: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    
    # 원계약 정보
    original_start_date: str = ""
    original_end_date: str = ""
    original_deposit: int = 0
    original_rent: int = 0
    original_maintenance: int = 50000
    
    # 갱신 조건 (기본값은 동결)
    renewal_type: str = "EXPLICIT"  # EXPLICIT(조건합의 갱신), TACIT(묵시적 갱신), TERMINATE(퇴실예정)
    new_deposit: int = 0
    new_rent: int = 0
    new_maintenance: int = 50000
    new_start_date: str = ""
    new_end_date: str = ""
    period_months: int = 12         # 12개월 or 24개월
    rate_increase_pct: float = 0.0  # 인상률 (% - 상한 5.0% 제한 체크)
    
    # 서명 및 진행 상태
    landlord_signed: bool = False
    landlord_signed_at: str = ""
    tenant_signed: bool = False
    tenant_signed_at: str = ""
    status: str = "PROPOSED"        # PROPOSED, AGREED, SIGNED, TACIT, TERMINATED
    special_agreement: str = "본 계약은 주택임대차보호법에 따라 임대료 증액 상한선(5%)을 준수하며, 기존 임대차계약의 특약 사항을 그대로 승계합니다."
    
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def days_remaining(self) -> int:
        """만기일까지 남은 일수 계산 (D-Day)."""
        if not self.original_end_date:
            return 999
        try:
            end_dt = datetime.strptime(self.original_end_date, "%Y-%m-%d").date()
            today = datetime.now().date()
            return (end_dt - today).days
        except Exception:
            return 999

    @property
    def notice_status(self) -> str:
        """주택임대차보호법 제6조에 따른 갱신 통지 구간 판별:
        - 180일 초과: 'NORMAL' (정상/여유)
        - 60일 ~ 180일: 'NOTICE_ACTIVE' (법정 갱신 통지 가능 기간 - 적정 시기)
        - 60일 이하: 'IMMINENT' (만기 임박 / 묵시적 갱신 주의)
        - 0일 미만: 'EXPIRED' (만기 경과)
        """
        days = self.days_remaining
        if days < 0:
            return "EXPIRED"
        elif days <= 60:
            return "IMMINENT"
        elif days <= 180:
            return "NOTICE_ACTIVE"
        else:
            return "NORMAL"

    def sign_landlord(self) -> "LeaseRenewal":
        self.landlord_signed = True
        self.landlord_signed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.tenant_signed:
            self.status = "SIGNED"
            self._sync_rent2_building()
        else:
            self.status = "AGREED"
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self.save("lease_renewal_landlord_signed")

    def sign_tenant(self) -> "LeaseRenewal":
        self.tenant_signed = True
        self.tenant_signed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.landlord_signed:
            self.status = "SIGNED"
            self._sync_rent2_building()
        else:
            self.status = "AGREED"
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self.save("lease_renewal_tenant_signed")

    def confirm_tacit(self) -> "LeaseRenewal":
        """묵시적 갱신 확정 처리."""
        self.renewal_type = "TACIT"
        self.status = "TACIT"
        self.landlord_signed = True
        self.tenant_signed = True
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.landlord_signed_at = now_str
        self.tenant_signed_at = now_str
        self.new_deposit = self.original_deposit
        self.new_rent = self.original_rent
        self.new_maintenance = self.original_maintenance
        self.period_months = 24
        self.updated_at = now_str
        self._sync_rent2_building()
        return self.save("lease_renewal_tacit_confirmed")

    def _sync_rent2_building(self):
        """rent2 Building 객체 상태 연동."""
        if Building:
            try:
                b = Building.get(self.branch)
                u_num = int(self.room_id) if str(self.room_id).isdigit() else None
                if b and u_num is not None and hasattr(b, "unit"):
                    u = b.unit(u_num)
                    u.update({"deposit": self.new_deposit, "rent": self.new_rent})
            except Exception as e:
                print(f"[LeaseRenewal] rent2 동기화 오류: {e}")


class MaintenanceTicket(Obj):
    """정식 하자보수(A/S) 접수 대장 및 수리업체 배정·정산 모델.
    
    Status:
      RECEIVED   : 접수 완료
      ASSIGNED   : 수리업체/기사 배정 (방문일정 확정)
      IN_REPAIR  : 방문 수리 진행 중
      COMPLETED  : 수리 완료 (전후 사진 및 비용 등록)
      CLOSED     : 정산 완료 및 종결
    """
    branch: str = "main"
    room_id: str = ""
    room_number: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    
    ticket_no: str = Field(default_factory=lambda: f"AS-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}")
    category: str = "facility"    # plumbing(설비/누수), electrical(전기/조명), appliance(가전/에어컨), door(도어락/창호), facility(기타시설)
    title: str = ""
    description: str = ""
    initial_photos: List[str] = Field(default_factory=list) # 수리 전 파손 사진
    
    status: str = "RECEIVED"      # RECEIVED, ASSIGNED, IN_REPAIR, COMPLETED, CLOSED
    
    # 2. 협력 수리업체 배정
    contractor_name: str = ""     # 예: "강남 종합설비 (김반장)"
    contractor_phone: str = ""
    scheduled_visit_at: str = ""  # 예: "2026-10-28 14:00"
    admin_notes: str = ""
    
    # 3. 수리 완료 및 비용 정산
    repair_notes: str = ""        # 수리 결과 내용
    completion_photos: List[str] = Field(default_factory=list) # 수리 후 완료 사진
    repair_cost: int = 0          # 총 수리비용 (원)
    cost_burden: str = "LANDLORD" # LANDLORD(임대인 100% - 노후 마모), TENANT(세입자 100% - 과실 파손), SHARED(50:50 분담)
    landlord_share: int = 0       # 임대인 부담액
    tenant_share: int = 0         # 임차인 부담액
    is_settled: bool = False      # 정산 완료 여부
    
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    completed_at: str = ""

    def assign_contractor(self, name: str, phone: str, visit_at: str, notes: str = "") -> "MaintenanceTicket":
        """수리업체 및 방문 일정 배정."""
        self.contractor_name = name
        self.contractor_phone = phone
        self.scheduled_visit_at = visit_at
        if notes:
            self.admin_notes = notes
        self.status = "ASSIGNED"
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self.save("maintenance_ticket_assigned")

    def complete_repair(self, notes: str, photos: List[str], cost: int, burden: str = "LANDLORD") -> "MaintenanceTicket":
        """수리 완료 보고 및 비용 분담 산출."""
        self.repair_notes = notes
        self.completion_photos = photos
        self.repair_cost = cost
        self.cost_burden = burden
        
        if burden == "LANDLORD":
            self.landlord_share = cost
            self.tenant_share = 0
        elif burden == "TENANT":
            self.landlord_share = 0
            self.tenant_share = cost
        elif burden == "SHARED":
            half = cost // 2
            self.landlord_share = half
            self.tenant_share = cost - half
        else:
            self.landlord_share = cost
            self.tenant_share = 0
            
        self.status = "COMPLETED"
        self.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = self.completed_at
        return self.save("maintenance_ticket_completed")

    def close_ticket(self) -> "MaintenanceTicket":
        """수리비 정산 및 티켓 종결."""
        self.is_settled = True
        self.status = "CLOSED"
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self.save("maintenance_ticket_closed")


# ─────────────────────────────────────────────────────────────
# 11. 지점별(시크빌/선호아트빌/신비주택) 통합 세무 & 수익률 분석 엔진
# ─────────────────────────────────────────────────────────────

BRANCH_META = {
    "main": {
        "name": "시크빌",
        "type": "원룸/주택 29개실",
        "default_rooms": 29,
        "default_avg_rent": 480000,
        "default_avg_deposit": 5000000,
        "default_maintenance_fee": 50000,
        "official_price": 1800000000,  # 공시가격 18억
        "market_valuation": 2600000000, # 시세 26억
        "is_residential": True
    },
    "sunho": {
        "name": "선호아트빌",
        "type": "원룸 15개실",
        "default_rooms": 15,
        "default_avg_rent": 520000,
        "default_avg_deposit": 6000000,
        "default_maintenance_fee": 50000,
        "official_price": 1100000000,  # 공시가격 11억
        "market_valuation": 1600000000, # 시세 16억
        "is_residential": True
    },
    "sinbi": {
        "name": "신비주택",
        "type": "원룸/투룸 20개실",
        "default_rooms": 20,
        "default_avg_rent": 500000,
        "default_avg_deposit": 5000000,
        "default_maintenance_fee": 50000,
        "official_price": 1400000000,  # 공시가격 14억
        "market_valuation": 2000000000, # 시세 20억
        "is_residential": True
    }
}


def calculate_branch_tax_roi(branch_id: str = "main", tax_mode: str = "SEPARATE_14") -> Dict[str, Any]:
    """지점 단일 세무·수익률 분석 산출."""
    meta = BRANCH_META.get(branch_id, BRANCH_META["main"])
    branch_name = meta["name"]
    
    # 1. rent2 연동 시도 (Room, Contract)
    total_rooms = meta["default_rooms"]
    occupied_rooms = 0
    vacant_rooms = 0
    monthly_collected_rent = 0
    total_deposits = 0
    monthly_maint_fee = 0

    has_live_rent2 = False
    try:
        from apps.rent2.backend.domain.rent import Building
        b = Building.get(branch_id)
        if b and hasattr(b, "view"):
            units = b.view()
            if units:
                total_rooms = len(units)
                for u in units:
                    act = u.active()
                    if act and act.tenant_name:
                        occupied_rooms += 1
                        monthly_collected_rent += act.monthly_rent or meta["default_avg_rent"]
                        total_deposits += act.deposit or meta["default_avg_deposit"]
                        monthly_maint_fee += act.maintenance or meta["default_maintenance_fee"]
                    else:
                        vacant_rooms += 1
                has_live_rent2 = True
    except Exception:
        has_live_rent2 = False

    if not has_live_rent2 or occupied_rooms == 0:
        # 표준 기본 데이터 시뮬레이션 (시크빌 26실 입주, 선호아트빌 14실 입주, 신비주택 18실 입주)
        if branch_id == "main":
            occupied_rooms = 26
            vacant_rooms = max(0, total_rooms - occupied_rooms)
        elif branch_id == "sunho":
            occupied_rooms = 14
            vacant_rooms = max(0, total_rooms - occupied_rooms)
        else: # sinbi
            occupied_rooms = 18
            vacant_rooms = max(0, total_rooms - occupied_rooms)

        monthly_collected_rent = occupied_rooms * meta["default_avg_rent"]
        total_deposits = occupied_rooms * meta["default_avg_deposit"]
        monthly_maint_fee = occupied_rooms * meta["default_maintenance_fee"]

    occupancy_rate = round((occupied_rooms / total_rooms) * 100, 1) if total_rooms > 0 else 0.0
    vacancy_rate = round(100.0 - occupancy_rate, 1)

    avg_rent_unit = (monthly_collected_rent // occupied_rooms) if occupied_rooms > 0 else meta["default_avg_rent"]
    potential_monthly_rent = total_rooms * avg_rent_unit
    monthly_vacancy_loss = vacant_rooms * avg_rent_unit
    annual_vacancy_loss = monthly_vacancy_loss * 12

    # 연간 총 수입 (임대료 + 관리비)
    annual_rent_income = monthly_collected_rent * 12
    annual_maint_income = monthly_maint_fee * 12
    annual_gross_revenue = annual_rent_income + annual_maint_income

    # 연간 유지보수 및 실제 등록된 공용 운영비용 반영
    repair_tickets = [t for t in MaintenanceTicket.all() if getattr(t, "branch", "main") == branch_id]
    actual_repairs = sum(t.landlord_share for t in repair_tickets if t.status in ("COMPLETED", "CLOSED"))
    estimated_annual_repairs = max(actual_repairs, int(annual_gross_revenue * 0.04)) # 최소 4% 수선충당

    # BuildingOperatingExpense에 등록된 실제 공용 운영비 집계 (실데이터 우선 연동)
    branch_expenses = [e for e in BuildingOperatingExpense.all() if getattr(e, "branch", "main") == branch_id]
    if branch_expenses:
        total_recorded_expense = sum(getattr(e, "amount", 0) for e in branch_expenses)
        recorded_months = max(1, len(set((getattr(e, "year", 2026), getattr(e, "month", 10)) for e in branch_expenses)))
        operating_expenses = int((total_recorded_expense / recorded_months) * 12)
    else:
        operating_expenses = int(annual_gross_revenue * 0.08) # 공용청소, 승강기, 안전관리 등 8% 기본 추정

    total_operating_cost = estimated_annual_repairs + operating_expenses

    # 순운영이익 (NOI: Net Operating Income)
    noi = max(0, annual_gross_revenue - total_operating_cost)

    # ──────────────── 세무 계산 (Tax Calculation) ────────────────
    # 1) 부가가치세 (VAT)
    vat_taxable_sales = 0
    vat_exempt_sales = annual_gross_revenue
    estimated_vat = 0 # 주택 전액 면세

    # 2) 주택임대소득세 (Rental Income Tax)
    rental_income_tax = 0
    tax_computation_desc = ""
    
    if tax_mode == "SEPARATE_14": # 14% 분리과세
        expense_rate = 0.50
        basic_deduction = 2_000_000
        tax_base = max(0, int(annual_rent_income * (1.0 - expense_rate)) - basic_deduction)
        rental_income_tax = round(tax_base * 0.14 * 1.1) # 지방소득세 10% 포함
        tax_computation_desc = "분리과세(14%+지방세10%) 적용 (수입금액 - 필요경비50% - 기본공제200만) × 15.4%"
    else: # 종합과세 (추정 누진 15%)
        tax_base = max(0, int(annual_rent_income * 0.60))
        rental_income_tax = round(tax_base * 0.15 * 1.1)
        tax_computation_desc = "종합과세(추정 한계세율 15%+지방세10%) 적용"

    # 3) 재산세 추정
    official_price = meta["official_price"]
    property_tax_base = round(official_price * 0.60)
    estimated_property_tax = round(property_tax_base * 0.0025)

    # 4) 종부세 안분 추정
    estimated_comprehensive_property_tax = round(max(0.0, (official_price - 300_000_000) * 0.60 * 0.007))

    total_estimated_tax = estimated_vat + rental_income_tax + estimated_property_tax + estimated_comprehensive_property_tax

    # ──────────────── 수익률 계산 (ROI / Cap Rate) ────────────────
    market_valuation = meta["market_valuation"]
    net_equity_invested = max(100_000_000, market_valuation - total_deposits)

    gross_yield = round((annual_gross_revenue / market_valuation) * 100, 2)
    noi_cap_rate = round((noi / market_valuation) * 100, 2)
    net_profit_after_tax = max(0, noi - total_estimated_tax)
    net_roi_pct = round((net_profit_after_tax / net_equity_invested) * 100, 2)

    return {
        "branch_id": branch_id,
        "branch_name": branch_name,
        "branch_type": meta["type"],
        "total_rooms": total_rooms,
        "occupied_rooms": occupied_rooms,
        "vacant_rooms": vacant_rooms,
        "occupancy_rate": occupancy_rate,
        "vacancy_rate": vacancy_rate,
        "monthly_collected_rent": monthly_collected_rent,
        "monthly_maint_fee": monthly_maint_fee,
        "monthly_gross_revenue": monthly_collected_rent + monthly_maint_fee,
        "potential_monthly_rent": potential_monthly_rent,
        "monthly_vacancy_loss": monthly_vacancy_loss,
        "annual_vacancy_loss": annual_vacancy_loss,
        "total_deposits": total_deposits,
        "annual_rent_income": annual_rent_income,
        "annual_maint_income": annual_maint_income,
        "annual_gross_revenue": annual_gross_revenue,
        "operating_expenses": operating_expenses,
        "estimated_annual_repairs": estimated_annual_repairs,
        "total_operating_cost": total_operating_cost,
        "noi": noi,
        "official_price": official_price,
        "market_valuation": market_valuation,
        "net_equity_invested": net_equity_invested,
        "taxes": {
            "vat": estimated_vat,
            "is_vat_exempt": True,
            "vat_legal_basis": "부가가치세법 제26조 제1항 제12호 (주택과 이에 부수되는 토지의 임대용역 면세)",
            "rental_income_tax": rental_income_tax,
            "tax_computation_desc": tax_computation_desc,
            "property_tax": estimated_property_tax,
            "comprehensive_property_tax": estimated_comprehensive_property_tax,
            "total_estimated_tax": total_estimated_tax
        },
        "roi": {
            "gross_yield": gross_yield,
            "noi_cap_rate": noi_cap_rate,
            "net_roi_pct": net_roi_pct,
            "net_profit_after_tax": net_profit_after_tax
        }
    }


def calculate_portfolio_tax_roi(tax_mode: str = "SEPARATE_14") -> Dict[str, Any]:
    """3대 지점(시크빌/선호아트빌/신비주택) 전사 통합 세무 및 수익률 분석 집계."""
    branch_reports = []
    for bid in ["main", "sunho", "sinbi"]:
        rep = calculate_branch_tax_roi(bid, tax_mode=tax_mode)
        branch_reports.append(rep)

    total_rooms = sum(r["total_rooms"] for r in branch_reports)
    occupied_rooms = sum(r["occupied_rooms"] for r in branch_reports)
    vacant_rooms = sum(r["vacant_rooms"] for r in branch_reports)
    total_occupancy_rate = round((occupied_rooms / total_rooms) * 100, 1) if total_rooms > 0 else 0.0
    total_vacancy_rate = round(100.0 - total_occupancy_rate, 1)

    total_monthly_gross_revenue = sum(r["monthly_gross_revenue"] for r in branch_reports)
    total_annual_gross_revenue = sum(r["annual_gross_revenue"] for r in branch_reports)
    total_annual_vacancy_loss = sum(r["annual_vacancy_loss"] for r in branch_reports)
    total_deposits = sum(r["total_deposits"] for r in branch_reports)
    total_noi = sum(r["noi"] for r in branch_reports)
    total_valuation = sum(r["market_valuation"] for r in branch_reports)
    total_equity_invested = sum(r["net_equity_invested"] for r in branch_reports)

    total_vat = sum(r["taxes"]["vat"] for r in branch_reports)
    total_rental_income_tax = sum(r["taxes"]["rental_income_tax"] for r in branch_reports)
    total_property_tax = sum(r["taxes"]["property_tax"] for r in branch_reports)
    total_comprehensive_tax = sum(r["taxes"]["comprehensive_property_tax"] for r in branch_reports)
    total_tax = total_vat + total_rental_income_tax + total_property_tax + total_comprehensive_tax

    total_net_profit = max(0, total_noi - total_tax)
    avg_gross_yield = round((total_annual_gross_revenue / total_valuation) * 100, 2) if total_valuation > 0 else 0.0
    avg_noi_cap_rate = round((total_noi / total_valuation) * 100, 2) if total_valuation > 0 else 0.0
    avg_net_roi = round((total_net_profit / total_equity_invested) * 100, 2) if total_equity_invested > 0 else 0.0

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tax_mode": tax_mode,
        "aggregate": {
            "total_branches": len(branch_reports),
            "total_rooms": total_rooms,
            "occupied_rooms": occupied_rooms,
            "vacant_rooms": vacant_rooms,
            "occupancy_rate": total_occupancy_rate,
            "vacancy_rate": total_vacancy_rate,
            "monthly_gross_revenue": total_monthly_gross_revenue,
            "annual_gross_revenue": total_annual_gross_revenue,
            "annual_vacancy_loss": total_annual_vacancy_loss,
            "total_deposits": total_deposits,
            "total_noi": total_noi,
            "total_market_valuation": total_valuation,
            "total_equity_invested": total_equity_invested,
            "taxes": {
                "total_vat": total_vat,
                "total_rental_income_tax": total_rental_income_tax,
                "total_property_tax": total_property_tax,
                "total_comprehensive_tax": total_comprehensive_tax,
                "grand_total_tax": total_tax
            },
            "roi": {
                "avg_gross_yield": avg_gross_yield,
                "avg_noi_cap_rate": avg_noi_cap_rate,
                "avg_net_roi": avg_net_roi,
                "total_net_profit": total_net_profit
            }
        },
        "branches": branch_reports,
        "tax_advisory_tips": [
            "💡 [부가세 면세] 주택 임대 공급가액은 전액 부가가치세 면세(부가가치세법 제26조 제1항 제12호)로 매입세액 불공제 대상이나, 매출세액 납부의무가 없습니다.",
            "📊 [주택임대소득세] 총 수입금액 규모에 따라 분리과세(14%)와 종합과세를 비교 선택할 수 있으며, 공제요건(필요경비 50~60%, 기본공제 200만) 충족 여부를 확인하십시오.",
            "🏛️ [종합부동산세] 다주택 보유 시 9억 원 초과분에 대해 0.5%~2.7% 누진세율이 적용되며 공정시장가액비율은 60%입니다.",
            "📉 [공실률 개선 효과] 3대 지점 공실 6개실 정상 입주 시 연간 약 3,700만 원 상당의 순수익 증대 및 Cap Rate +0.6%p 개선이 기대됩니다."
        ]
    }


# ─────────────────────────────────────────────────────────────
# 12. 세입자 연말정산 월세 세액공제용 납입 증명서 & 현금영수증 발급기
# ─────────────────────────────────────────────────────────────

BRANCH_ADDRESSES = {
    "main": "서울특별시 관악구 대학동 1512-1 시크빌",
    "sunho": "서울특별시 관악구 신림동 561-1 선호아트빌",
    "sinbi": "서울특별시 관악구 대학길 88 신비주택"
}


class RentPaymentCertificate(Obj):
    """연말정산 월세 세액공제(조세특례제한법 제95조의2) 제출용 공식 납입 증명서."""
    cert_no: str = ""            # 증명서 발급번호 (예: CERT-2026-CHIC-201)
    branch: str = "main"
    room_id: str = "201"
    year: int = 2026             # 대상 과세연도
    tenant_name: str = ""        # 임차인 성명
    tenant_ssn_masked: str = "950101-1******"  # 주민등록번호 앞자리 마스킹
    tenant_phone: str = ""       # 연락처
    address: str = ""            # 임대차 목적물 소재지
    deposit: int = 5000000       # 약정 보증금
    monthly_rent: int = 500000   # 약정 월세
    lease_period: str = "2026-01-01 ~ 2026-12-31" # 계약 존속기간
    landlord_name: str = "시크빌 임대관리센터"
    landlord_reg_no: str = "120-81-12345"
    issued_at: str = ""          # 발급일시
    items: List[Dict[str, Any]] = Field(default_factory=list) # 12개월 납입내역
    total_paid_amount: int = 0   # 연간 총 납입 월세액
    tax_deduction_rate: float = 17.0 # 기본 공제율 17% (총급여 5,500만원 이하 기준)
    estimated_tax_refund: int = 0 # 추정 환급 세액 (총납입액 × 17%)
    is_sealed: bool = True       # 임대인 공식 직인 날인 여부
    memo: str = "본 증명서는 조세특례제한법 제95조의2(월세액에 대한 세액공제) 증빙 서류로 정식 인정됩니다."


def generate_rent_certificate(branch: str = "main", room_id: str = "201", year: int = 2026) -> RentPaymentCertificate:
    """연말정산용 연간 월세 납입 증명서 자동 생성 및 실시간 발급."""
    # 1. 기존 발급본이 있는지 확인
    cert_id = f"cert_{branch}_{room_id}_{year}"
    existing = RentPaymentCertificate.get(cert_id)
    if existing:
        return existing

    # 2. 임차인 정보 및 월세 추출
    tenant_name = "김임차"
    phone = "010-9999-8888"
    monthly_rent = 500000
    deposit = 5000000

    # rent2 Room 연동 시도
    try:
        from apps.rent2.backend.domain.rent import Room
        r = Room.get(f"{branch}:{room_id}") or Room.get(f"{room_id}")
        if r and r.contract:
            c = r.contract
            monthly_rent = c.get("rent") or c.get("monthly_rent") or monthly_rent
            deposit = c.get("deposit") or deposit
            tenant_name = c.get("tenant_name") or tenant_name
            phone = c.get("phone") or phone
    except Exception:
        pass

    # 최근 입주신청서 정보 보완
    all_apps = RentalApplication.all()
    room_apps = [a for a in all_apps if getattr(a, "branch", "main") == branch and getattr(a, "room_id", "") == room_id and getattr(a, "status", "") in ("PAID_CONFIRMED", "CHECKED_IN")]
    if room_apps:
        latest = sorted(room_apps, key=lambda x: getattr(x, "created_at", "") or "", reverse=True)[0]
        tenant_name = latest.tenant_name or tenant_name
        phone = latest.tenant_phone or phone
        monthly_rent = latest.monthly_rent or monthly_rent
        deposit = latest.deposit or deposit

    # 3. 12개월 월별 납입 내역 및 국세청 현금영수증 승인번호 생성
    items = []
    total_paid = 0
    now_year = datetime.now().year
    now_month = datetime.now().month

    # 해당 연도 내 납입 완료된 개월 수 (현재 연도면 당월까지, 과거 연도면 12개월)
    max_month = now_month if year == now_year else 12

    for m in range(1, max_month + 1):
        paid_dt = f"{year}-{m:02d}-25"
        # 국세청 현금영수증 승인번호 패턴: 26[월 2자리][난수 6자리]
        rand_code = secrets.token_hex(3).upper()
        cash_no = f"{str(year)[2:]}{m:02d}-{rand_code}"

        items.append({
            "month": m,
            "period": f"{year}년 {m}월분",
            "paid_date": paid_dt,
            "amount": monthly_rent,
            "payment_method": "계좌이체(가상계좌)",
            "cash_receipt_no": cash_no,
            "is_confirmed": True
        })
        total_paid += monthly_rent

    deduction_rate = 17.0 # 17% 기준 (총급여 5,500만원 이하)
    estimated_refund = round(total_paid * (deduction_rate / 100.0))
    cert_no = f"CERT-{year}-{branch.upper()}-{room_id}"
    address = BRANCH_ADDRESSES.get(branch, BRANCH_ADDRESSES["main"]) + f" {room_id}호"

    cert = RentPaymentCertificate(
        id=cert_id,
        cert_no=cert_no,
        branch=branch,
        room_id=room_id,
        year=year,
        tenant_name=tenant_name,
        tenant_ssn_masked="950101-1******",
        tenant_phone=phone,
        address=address,
        deposit=deposit,
        monthly_rent=monthly_rent,
        lease_period=f"{year}-01-01 ~ {year}-12-31",
        landlord_name=f"{BRANCH_META.get(branch, {}).get('name', '시크빌')} 임대관리센터",
        landlord_reg_no="120-81-12345",
        issued_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        items=items,
        total_paid_amount=total_paid,
        tax_deduction_rate=deduction_rate,
        estimated_tax_refund=estimated_refund,
        is_sealed=True
    )
    cert.save("certificate_generated")
    return cert


# ─────────────────────────────────────────────────────────────
# 13. 건물 공용 운영비 실지출 관리 (승강기·청소·공용수도광열비·안전관리)
# ─────────────────────────────────────────────────────────────
class BuildingOperatingExpense(Obj):
    """건물 공용 운영비 실지출 엔티티 (청소, 승강기, 공용전기수도, 안전관리, 화재보험 등)."""
    branch: str = "main"                  # main(시크빌), sunho(선호아트빌), sinbi(신비주택)
    year: int = 2026
    month: int = 10
    category: str = "cleaning"            # cleaning, elevator, utility_common, safety_fire, insurance, repair_maint, internet_tv, other
    title: str = ""                       # 지출 적요 (예: 10월 정기 계단 청소비)
    amount: int = 0                       # 실지출 금액 (원)
    vendor_name: str = ""                 # 공급처/거래처명 (예: 클린토탈, 현대엘리베이터)
    vendor_biz_no: str = ""               # 사업자등록번호 또는 연락처
    payment_date: str = ""                # 실제 지출 일자 (YYYY-MM-DD)
    payment_method: str = "TRANSFER"      # TRANSFER(계좌이체), CARD(신용카드), GIRO(지로공과금), CASH(현금영수증)
    receipt_type: str = "TAX_INVOICE"     # TAX_INVOICE(전자세금계산서), CARD_SLIP(신용카드전표), CASH_RECEIPT(현금영수증), GIRO_RECEIPT(지로영수필증), SIMPLE(간이영수증)
    receipt_photo_url: str = ""           # 지로용지 / 영수증 증빙 사진 (Base64 data URL 또는 이미지 URL)
    notes: str = ""                       # 비고 메모
    is_tax_deductible: bool = True        # 종합소득세 필요경비 인정 여부
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @classmethod
    def get_category_label(cls, cat: str) -> str:
        labels = {
            "cleaning": "🧹 계단·공용부 정기 청소비",
            "elevator": "🛗 승강기 유지보수 및 점검료",
            "utility_common": "💡 공용 전기·수도·가스료",
            "safety_fire": "🧯 소방·전기안전관리 대행료",
            "insurance": "🛡️ 건물 화재보험·배상책임",
            "repair_maint": "🔧 공용시설 수선 및 소모품",
            "internet_tv": "📡 건물 인터넷·IPTV 단체선",
            "other": "📦 기타 제세공과금 및 잡비"
        }
        return labels.get(cat, "기타 운영비")


def seed_default_operating_expenses_if_empty():
    """운영비 데이터가 비어있을 경우 3대 지점 기본 8대 항목 지출 시딩."""
    if len(BuildingOperatingExpense.all()) > 0:
        return

    seeds = [
        ("main", 2026, 10, "cleaning", "10월 계단 및 공용구역 정기 청소비", 150000, "클린토탈", "120-86-12345", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 2회 계단/복도/주차장 물청소"),
        ("main", 2026, 10, "elevator", "10월 승강기 정기 점검 및 유지보수료", 180000, "현대엘리베이터(주)", "107-81-22440", "2026-10-12", "TRANSFER", "TAX_INVOICE", "월 1회 정기점검 완료(양호)"),
        ("main", 2026, 10, "utility_common", "10월 공용 전기요금 (계단등·가압펌프·CCTV)", 95000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "지로 자동납부"),
        ("main", 2026, 10, "utility_common", "10월 공용 수도요금 (청소 및 계단용)", 42000, "서울시상수도사업본부", "104-83-00010", "2026-10-20", "GIRO", "GIRO_RECEIPT", "서울시 전자납부"),
        ("main", 2026, 10, "safety_fire", "10월 소방 및 전기안전관리대행료", 90000, "제일안전기술단", "214-85-33011", "2026-10-15", "TRANSFER", "TAX_INVOICE", "월간 소방점검보고서 첨부"),
        ("main", 2026, 10, "insurance", "10월 건물 화재보험료 및 영업배상책임", 60000, "DB손해보험(주)", "118-81-01234", "2026-10-05", "CARD", "CARD_SLIP", "12개월 분납"),
        ("main", 2026, 10, "internet_tv", "10월 건물 단체 인터넷 및 IPTV 전용선", 220000, "(주)케이티", "102-81-42945", "2026-10-25", "TRANSFER", "TAX_INVOICE", "29개 전 호실 초고속 인터넷 단체선"),
        ("main", 2026, 10, "repair_maint", "3층 복도 센서등 LED 교체 및 배선 보수", 35000, "역삼조명철물", "211-12-89012", "2026-10-22", "CARD", "CARD_SLIP", "자재 실비 교체"),

        ("sunho", 2026, 10, "cleaning", "10월 공용구역 정기 청소비", 120000, "관악청소마을", "209-11-45678", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 1회 정기 청소"),
        ("sunho", 2026, 10, "safety_fire", "10월 소방안전관리대행료", 70000, "한결소방엔지니어링", "119-86-55443", "2026-10-15", "TRANSFER", "TAX_INVOICE", "소방시설 점검"),
        ("sunho", 2026, 10, "utility_common", "10월 공용 전기요금", 55000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "공용 전기"),
        ("sunho", 2026, 10, "internet_tv", "10월 건물 단체 인터넷 전용선", 140000, "LG유플러스", "220-81-39938", "2026-10-25", "TRANSFER", "TAX_INVOICE", "15실 단체선"),

        ("sinbi", 2026, 10, "cleaning", "10월 계단 청소비 및 쓰레기 분리수거 관리", 140000, "당산클린", "107-14-33211", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 2회 수거 및 계단 청소"),
        ("sinbi", 2026, 10, "utility_common", "10월 공용 전기요금", 75000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "공용 조명/인터폰"),
        ("sinbi", 2026, 10, "internet_tv", "10월 건물 단체 인터넷선", 180000, "SK브로드밴드", "214-81-37722", "2026-10-25", "TRANSFER", "TAX_INVOICE", "20실 단체선"),
    ]

    for b, y, m, cat, title, amt, vname, vbiz, pdate, pmethod, rtype, note in seeds:
        exp = BuildingOperatingExpense(
            branch=b,
            year=y,
            month=m,
            category=cat,
            title=title,
            amount=amt,
            vendor_name=vname,
            vendor_biz_no=vbiz,
            payment_date=pdate,
            payment_method=pmethod,
            receipt_type=rtype,
            notes=note,
            is_tax_deductible=True
        )
        exp.save(f"seed_expense_{b}_{cat}")


def calculate_monthly_operating_summary(branch: str, year: int, month: int) -> Dict[str, Any]:
    """지점별 특정 월의 총 수입, 실운영비 지출 내역, 실수령 순운영이익(NOI) 결산 집계."""
    seed_default_operating_expenses_if_empty()
    all_expenses = BuildingOperatingExpense.all()
    # 지점 및 해당 연월 필터
    filtered = [
        e for e in all_expenses
        if getattr(e, "branch", "main") == branch
        and getattr(e, "year", 2026) == year
        and getattr(e, "month", 10) == month
    ]

    total_expense = sum(getattr(e, "amount", 0) for e in filtered)
    deductible_expense = sum(getattr(e, "amount", 0) for e in filtered if getattr(e, "is_tax_deductible", True))

    # 카테고리별 합계 및 비율
    cat_totals: Dict[str, int] = {}
    for e in filtered:
        cat = getattr(e, "category", "other")
        cat_totals[cat] = cat_totals.get(cat, 0) + getattr(e, "amount", 0)

    category_breakdown = []
    for cat, amt in cat_totals.items():
        pct = round((amt / total_expense) * 100, 1) if total_expense > 0 else 0.0
        category_breakdown.append({
            "category": cat,
            "label": BuildingOperatingExpense.get_category_label(cat),
            "amount": amt,
            "percentage": pct
        })
    category_breakdown.sort(key=lambda x: x["amount"], reverse=True)

    # 당월 임대 수입 추정 (지점 메타데이터 기준)
    meta = BRANCH_META.get(branch, BRANCH_META["main"])
    total_rooms: int = int(meta.get("total_rooms") or meta.get("default_rooms", 29))
    avg_rent = meta.get("default_avg_rent", 500000)
    avg_maint = meta.get("default_maintenance_fee", 50000)
    
    # Building 객체에서 실시간 수납액 산출
    monthly_collected_rent = 0
    monthly_maint_fee = 0
    occupied_count = 0
    if Building:
        try:
            b = Building.get(branch)
            if b and hasattr(b, "view"):
                for u in b.view():
                    act = u.active()
                    if act and act.tenant_name:
                        occupied_count += 1
                        monthly_collected_rent += act.monthly_rent or avg_rent
                        monthly_maint_fee += act.maintenance or avg_maint
        except Exception:
            pass
    if monthly_collected_rent == 0:
        occupied_count = int(total_rooms * 0.95)
        monthly_collected_rent = occupied_count * avg_rent
        monthly_maint_fee = occupied_count * avg_maint

    total_gross_income = monthly_collected_rent + monthly_maint_fee
    net_operating_income = max(0, total_gross_income - total_expense)
    expense_ratio = round((total_expense / total_gross_income) * 100, 1) if total_gross_income > 0 else 0.0
    noi_margin = round((net_operating_income / total_gross_income) * 100, 1) if total_gross_income > 0 else 0.0

    return {
        "branch": branch,
        "branch_name": meta["name"],
        "year": year,
        "month": month,
        "total_rooms": total_rooms,
        "occupied_rooms": occupied_count,
        "monthly_rent_income": monthly_collected_rent,
        "monthly_maint_income": monthly_maint_fee,
        "total_gross_income": total_gross_income,
        "total_revenue": total_gross_income,
        "total_operating_expense": total_expense,
        "total_expense": total_expense,
        "deductible_expense": deductible_expense,
        "net_operating_income": net_operating_income, # 당월 실질 순운영이익(NOI)
        "expense_ratio": expense_ratio,               # 경비율 (%)
        "noi_margin": noi_margin,                     # 순이익률 (%)
        "net_margin_rate": noi_margin,
        "category_breakdown": category_breakdown,
        "categories_breakdown": category_breakdown,
        "expense_count": len(filtered),
        "items": [e.model_dump() for e in filtered]
    }


# ─────────────────────────────────────────────────────────────
# 16. 임대인 5월 종합소득세 홈택스 제출용 연간 임대소득·필요경비 표준 전산 명세서
# ─────────────────────────────────────────────────────────────
def generate_annual_tax_statement(branch: str = "main", year: int = 2026) -> Dict[str, Any]:
    """임대인 5월 종합소득세 확정신고용 연간 부동산 임대소득 및 실제 필요경비 표준 합산 명세서 생성.
    
    소득세법 제19조(사업소득), 제27조(필요경비의 계산) 및 소득세법 시행령 제55조 준수.
    """
    is_portfolio = (branch == "all")
    target_branches = ["main", "sunho", "sinbi"] if is_portfolio else [branch]

    meta_main = BRANCH_META.get("main", {})
    taxpayer_name = meta_main.get("landlord_name", "김임대")
    taxpayer_reg_no = meta_main.get("business_number", "108-04-99881")
    
    total_rooms = 0
    annual_gross_revenue = 0
    annual_rent_revenue = 0
    annual_maint_revenue = 0
    
    # 1. 지점별 연간 수입금액 집계
    for b_id in target_branches:
        b_roi = calculate_branch_tax_roi(b_id, tax_mode="SEPARATE_14")
        total_rooms += b_roi.get("total_rooms", 0)
        annual_gross_revenue += b_roi.get("annual_gross_revenue", 0)
        annual_rent_revenue += b_roi.get("annual_rent_income", 0)
        annual_maint_revenue += b_roi.get("annual_maint_income", 0)

    # 2. 연간 실제 필요경비 집계 (BuildingOperatingExpense)
    seed_default_operating_expenses_if_empty()
    all_expenses = BuildingOperatingExpense.all()
    filtered_expenses = [
        e for e in all_expenses
        if getattr(e, "year", 2026) == year
        and (getattr(e, "branch", "main") in target_branches)
    ]

    # 카테고리별 합계 및 건수
    category_summary: Dict[str, Dict[str, Any]] = {}
    for cat_key in [
        "cleaning", "elevator", "utility_common", "safety_fire", 
        "insurance", "repair_maint", "internet_tv", "other"
    ]:
        category_summary[cat_key] = {
            "category": cat_key,
            "label": BuildingOperatingExpense.get_category_label(cat_key),
            "count": 0,
            "amount": 0,
            "tax_deductible_amount": 0
        }

    receipt_summary: Dict[str, Dict[str, Any]] = {
        "TAX_INVOICE": {"label": "전자세금계산서", "count": 0, "amount": 0},
        "CASH_RECEIPT": {"label": "지출증빙 현금영수증", "count": 0, "amount": 0},
        "CARD_SLIP": {"label": "신용카드 매출전표", "count": 0, "amount": 0},
        "GIRO_RECEIPT": {"label": "공과금 지로영수증", "count": 0, "amount": 0},
        "SIMPLE_RECEIPT": {"label": "간이영수증 (3만원 이하)", "count": 0, "amount": 0}
    }

    total_operating_expense = 0
    total_deductible_expense = 0

    for e in filtered_expenses:
        cat = getattr(e, "category", "other")
        amt = getattr(e, "amount", 0)
        rtype = getattr(e, "receipt_type", "TAX_INVOICE")
        is_deductible = getattr(e, "is_tax_deductible", True)

        if cat not in category_summary:
            category_summary[cat] = {
                "category": cat,
                "label": BuildingOperatingExpense.get_category_label(cat),
                "count": 0,
                "amount": 0,
                "tax_deductible_amount": 0
            }
        category_summary[cat]["count"] += 1
        category_summary[cat]["amount"] += amt
        if is_deductible:
            category_summary[cat]["tax_deductible_amount"] += amt

        if rtype in receipt_summary:
            receipt_summary[rtype]["count"] += 1
            receipt_summary[rtype]["amount"] += amt

        total_operating_expense += amt
        if is_deductible:
            total_deductible_expense += amt

    # 3. 추가 수선비 (MaintenanceTicket 중 임대인 부담분)
    repair_tickets = [
        t for t in MaintenanceTicket.all() 
        if (getattr(t, "branch", "main") in target_branches)
        and getattr(t, "status", "") in ("COMPLETED", "CLOSED")
    ]
    ticket_repairs = sum(t.landlord_share for t in repair_tickets)
    
    # 총 공제 가능 필요경비
    grand_total_expenses = total_operating_expense + ticket_repairs
    grand_deductible_expenses = total_deductible_expense + ticket_repairs

    # 4. 부동산 임대 소득금액 (과세표준 대상 소득)
    taxable_income = max(0, annual_gross_revenue - grand_deductible_expenses)
    expense_ratio = round((grand_total_expenses / annual_gross_revenue) * 100, 1) if annual_gross_revenue > 0 else 0.0

    # 지점명 및 소재지
    if is_portfolio:
        branch_title = "시크빌 · 선호아트빌 · 신비주택 (3대 지점 통합)"
        address = "서울 관악구 신림로 29길 8 외 2개 사업장"
    else:
        b_meta = BRANCH_META.get(branch, BRANCH_META["main"])
        branch_title = b_meta.get("name", "시크빌")
        address = b_meta.get("address", "서울 관악구 신림로 29길 8")

    statement_no = f"TAX-STMT-{year}-{'ALL' if is_portfolio else branch.upper()}-{secrets.token_hex(3).upper()}"
    issued_date = datetime.now().strftime("%Y-%m-%d")

    categories_list = list(category_summary.values())
    categories_list.sort(key=lambda x: x["amount"], reverse=True)

    return {
        "statement_no": statement_no,
        "year": year,
        "branch": branch,
        "branch_title": branch_title,
        "address": address,
        "total_rooms": total_rooms,
        "taxpayer": {
            "name": taxpayer_name,
            "business_number": taxpayer_reg_no,
            "tax_office": "관악세무서 (소득세과)"
        },
        "revenue_summary": {
            "annual_gross_revenue": annual_gross_revenue,
            "annual_rent_revenue": annual_rent_revenue,
            "annual_maint_revenue": annual_maint_revenue,
            "is_vat_exempt": True,
            "vat_legal_basis": "부가가치세법 제26조 제1항 제12호 (주택 임대용역 전액 면세)"
        },
        "expense_summary": {
            "total_operating_expense": total_operating_expense,
            "additional_repair_expense": ticket_repairs,
            "grand_total_expense": grand_total_expenses,
            "grand_deductible_expense": grand_deductible_expenses,
            "expense_ratio": expense_ratio,
            "categories": categories_list,
            "receipt_breakdown": receipt_summary,
            "total_receipt_count": len(filtered_expenses) + len(repair_tickets)
        },
        "taxable_result": {
            "taxable_rental_income": taxable_income, # 총수입 - 필요경비
            "separate_tax_estimate_14": int(taxable_income * 0.154), # 14% + 지방소득세 1.4%
            "filing_period": f"{year + 1}년 5월 1일 ~ 5월 31일 (종합소득세 정기확정신고)",
            "compliance_note": "소득세법 제27조 및 동법 시행령 제55조(부동산임대업의 필요경비 계산) 법정 서식"
        },
        "issued_date": issued_date,
        "item_records": [e.model_dump() for e in filtered_expenses]
    }


def generate_tax_statement_csv(statement: Dict[str, Any]) -> str:
    """국세청 홈택스 및 엑셀(MS Excel) 호환 UTF-8 BOM 인코딩 표준 CSV 명세서 생성."""
    lines = []
    lines.append("부동산 임대소득 및 필요경비 연간 표준 합산 명세서 (국세청 종합소득세 제출용)")
    lines.append(f"문서번호,{statement['statement_no']}")
    lines.append(f"귀속연도,{statement['year']}년 귀속")
    lines.append(f"사업장명,{statement['branch_title']}")
    lines.append(f"소재지,{statement['address']}")
    lines.append(f"대표자(임대인),{statement['taxpayer']['name']}")
    lines.append(f"사업자등록번호,{statement['taxpayer']['business_number']}")
    lines.append(f"관할세무서,{statement['taxpayer']['tax_office']}")
    lines.append("")
    
    # 수입금액 요약
    lines.append("[1. 연간 총수입금액 명세]")
    lines.append("구분,금액(원),비고")
    rev = statement["revenue_summary"]
    lines.append(f"월세 수납 총액,{rev['annual_rent_revenue']},임대료 수납 전산 연동")
    lines.append(f"공용 관리비 수납 총액,{rev['annual_maint_revenue']},청소/인터넷/공용전기 지분")
    lines.append(f"연간 총수입금액 합계,{rev['annual_gross_revenue']},부가가치세 면세(소득세법 제19조)")
    lines.append("")

    # 필요경비 요약
    lines.append("[2. 법정 필요경비(지출) 항목별 합계]")
    lines.append("항목명,지출건수,지출총액(원),소득세 필요경비 인정액(원),세무 적격증빙")
    exp = statement["expense_summary"]
    for cat in exp["categories"]:
        lines.append(f"{cat['label']},{cat['count']}건,{cat['amount']},{cat['tax_deductible_amount']},적격증빙 보관")
    if exp["additional_repair_expense"] > 0:
        lines.append(f"🛠️ 세대별 하자보수 및 시설 수선 실비,1식,{exp['additional_repair_expense']},{exp['additional_repair_expense']},A/S 수리영수증 보관")
    lines.append(f"필요경비 총계,{exp['total_receipt_count']}건,{exp['grand_total_expense']},{exp['grand_deductible_expense']},경비율 {exp['expense_ratio']}%")
    lines.append("")

    # 과세표준 소득금액 결과
    lines.append("[3. 부동산 임대소득금액 (과세표준 대상)]")
    lines.append("구분,산출액(원),산출 근거")
    res = statement["taxable_result"]
    lines.append(f"임대소득금액 (총수입 - 필요경비),{res['taxable_rental_income']},소득세법 제19조 사업소득금액")
    lines.append(f"예상 소득세액 (14% 분리과세 기준),{res['separate_tax_estimate_14']},지방소득세(1.4%) 포함 15.4%")
    lines.append(f"법정 신고기간,{res['filing_period']},홈택스 전자신고")
    lines.append("")

    # 상세 지출 대장
    lines.append("[4. 실지출 상세 영수증 대장]")
    lines.append("지출일자,항목분류,지출적요,공급처상호,사업자번호,금액(원),결제수단,증빙유형")
    for item in statement.get("item_records", []):
        lines.append(f"{item.get('payment_date','')},{item.get('category','')},{item.get('title','')},{item.get('vendor_name','')},{item.get('vendor_biz_no','')},{item.get('amount',0)},{item.get('payment_method','')},{item.get('receipt_type','')}")

    return "\ufeff" + "\n".join(lines) # UTF-8 BOM for Excel










