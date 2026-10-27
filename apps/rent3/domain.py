"""apps/rent3/domain.py — 차세대 비대면 직거래 스마트임차 도메인 엔티티 및 비즈니스 로직.

운영 및 법적 안전 규칙: .agents/skills/bass-rent3-ops/SKILL.md
- 공인중개사법상 무등록 중개행위 차단 (단순 IT 솔루션 및 직거래 전자서약)
- rent2 코어 엔진(가짜 체납 방지, FIFO 순차 상계, 3개 지점 격리 매트릭스) 완벽 계승
- 온라인 직거래 풀 라이프사이클: 공개광고 -> 비대면 신청/승인 -> 결제/서명 -> 입실/시설점검 -> 라운지 소통 -> 퇴실
"""
from datetime import datetime
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
                if b and hasattr(b, "rooms") and self.room_id in b.rooms:
                    room = b.rooms[self.room_id]
                    room.tenant_name = self.tenant_name
                    room.tenant_phone = self.tenant_phone
                    room.deposit = self.deposit
                    room.rent = self.monthly_rent
                    room.move_in = self.move_in_date
                    b.save(f"rent3_online_contract_{self.room_id}")
            except Exception as e:
                print(f"[RentalApplication] rent2 동기화 경고: {e}")
                
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
                if b and hasattr(b, "rooms") and self.room_id in b.rooms:
                    room = b.rooms[self.room_id]
                    room.tenant_name = self.tenant_name
                    room.tenant_phone = self.tenant_phone
                    room.deposit = self.deposit
                    room.rent = self.monthly_rent
                    room.move_in = self.move_in_date
                    b.save(f"cosign_contract_{self.room_id}")
            except Exception as e:
                print(f"[CoSigningSession] rent2 동기화 경고: {e}")

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
    tenant_name: str = ""
    tenant_phone: str = ""
    move_out_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    # 1. 보증금 및 공제 항목
    original_deposit: int = 5000000      # 기존 보증금
    unpaid_rent_deduction: int = 0       # 잔여 미납 월세 공제액
    utility_deduction: int = 45000       # 공과금(수도/가스/전기) 실비 공제액
    damage_deduction: int = 0            # 시설 파손/원상복구 공제액
    damage_notes: str = "통상 손모 외 특이 파손 없음 (상태 양호)"
    final_refund_deposit: int = 4955000  # 최종 반환 보증금 = original_deposit - 총공제

    # 2. 시설 검수 상태 (입실 시 체크 항목과 대조)
    inspection_checks: Dict[str, bool] = Field(default_factory=lambda: {
        "시스템 에어컨 작동 및 외관": True,
        "드럼 세탁기 급배수 및 도어": True,
        "빌트인 냉장고 청결 및 냉동/냉장": True,
        "벽지 및 장판 훼손/오염 없음": True,
        "디지털 도어락 건전지 및 작동": True,
        "욕실 수압 및 도기 파손 없음": True
    })
    inspection_photos: List[str] = Field(default_factory=list)

    # 3. 진행 상태
    status: str = "INSPECTING"           # INSPECTING -> AGREED -> COMPLETED
    tenant_agreed: bool = True           # 임차인 정산 동의 여부
    refund_completed: bool = False       # 보증금 송금 완료 여부
    refund_completed_at: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def calculate_refund(self) -> int:
        total_deduction = self.unpaid_rent_deduction + self.utility_deduction + self.damage_deduction
        self.final_refund_deposit = max(0, self.original_deposit - total_deduction)
        return self.final_refund_deposit

    def complete_settlement(self) -> "CheckOutRecord":
        """보증금 반환 완료 승인 -> rent2 호실 공실화 + rent3 매물 자동 광고 재등록."""
        self.refund_completed = True
        self.status = "COMPLETED"
        self.refund_completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. rent2 호실 초기화 및 공실 전환
        if Building:
            try:
                b = Building.get(self.branch)
                if b and hasattr(b, "rooms") and self.room_id in b.rooms:
                    room = b.rooms[self.room_id]
                    room.tenant_name = ""
                    room.tenant_phone = ""
                    room.is_occupied = False
                    room.status = "공실"
                    b.save(f"checkout_vacancy_{self.room_id}")
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

        return self.save("checkout_settlement_completed")


class RtmsContractReport(Obj):
    """국토교통부 거래관리시스템(RTMS) 법정 주택임대차 계약신고서 및 확정일자 필증."""
    session_id: str = ""             # 계약 세션 ID 또는 신청서 ID
    branch: str = "main"
    room_id: str = ""
    
    # 신고서 고유 번호
    report_no: str = Field(default_factory=lambda: f"RTMS-2026-{datetime.now().strftime('%m%d')}-{secrets.token_hex(3).upper()}")
    contract_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")) # 계약체결일
    deadline_date: str = "2026-11-26"# 체결일 + 30일 (과태료 기한)
    
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
    building_type: str = "다세대·원룸주택" # 또는 공동주택/오피스텔
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

    def submit_report(self) -> "RtmsContractReport":
        """RTMS 전자 신고 제출 -> 확정일자 자동 부여."""
        self.status = "SUBMITTED"
        self.submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.fixed_date_number = f"FIX-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        self.fixed_date_granted_at = self.submitted_at
        return self.save("rtms_report_submitted")


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
                if b and hasattr(b, "rooms") and self.room_id in b.rooms:
                    room = b.rooms[self.room_id]
                    room.deposit = self.new_deposit
                    room.rent = self.new_rent
                    b.save(f"rent3_renewal_sync_{self.room_id}")
            except Exception as e:
                print(f"[LeaseRenewal] rent2 동기화 오류: {e}")





