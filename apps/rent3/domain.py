"""apps/rent3/domain.py — 차세대 비대면 직거래 스마트임차 도메인 엔티티 및 비즈니스 로직.

운영 및 법적 안전 규칙: .agents/skills/bass-rent3-ops/SKILL.md
- 공인중개사법상 무등록 중개행위 차단 (단순 IT 솔루션 및 직거래 전자서약)
- rent2 코어 엔진(가짜 체납 방지, FIFO 순차 상계, 3개 지점 격리 매트릭스) 완벽 계승
- 온라인 직거래 풀 라이프사이클: 공개광고 -> 비대면 신청/승인 -> 결제/서명 -> 입실/시설점검 -> 라운지 소통 -> 퇴실
"""
from datetime import datetime
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
    """임대인-임차인 1:1 직거래 메시지 및 대면 방문투어 약속."""
    branch: str = "main"
    room_id: str = ""
    sender_role: str = "tenant"   # tenant(임차인) or landlord(임대인)
    sender_name: str = ""
    message: str = ""
    is_tour_request: bool = False # 대면 투어 약속 요청 여부
    tour_date: str = ""           # 약속 일시 (예: 2026-10-28 15:00)
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


