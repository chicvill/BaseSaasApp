"""apps/rent3/api.py — rent3 비대면 임대차 및 관리자 API 라우터.

BaseSaasApp 게이트웨이 표준 규격에 따라 마운트됩니다.
"""
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Body, Response
from pydantic import BaseModel, Field

from .domain import (
    RentalListing, RentalApplication, CheckInRecord, MaintenanceRequest, 
    CoSigningSession, ChatMessage, RoomAccessInfo, ParkingRegistration, 
    MonthlyBill, PaymentReceipt, CheckOutRecord, RtmsContractReport, 
    LeaseRenewal, MaintenanceTicket, calculate_branch_tax_roi, calculate_portfolio_tax_roi,
    RentPaymentCertificate, generate_rent_certificate,
    BuildingOperatingExpense, calculate_monthly_operating_summary,
    generate_annual_tax_statement, generate_tax_statement_csv
)
from .entities import (
    Admin, Lessor, Tenant, BuildingEntity, RoomEntity, ContractEntity,
    Pay, Outlay, Rent3EntityManager, Rent3WorkflowEngine, seed_default_entities_if_empty
)
try:
    from apps.rent2.backend.domain.rent import Building
except ImportError:
    Building = None

router = APIRouter(tags=["rent3"])


# ─────────────────────────────────────────────────────────────
# 1. Pydantic Request Models
# ─────────────────────────────────────────────────────────────
class ApplicationCreateReq(BaseModel):
    listing_id: str
    branch: str = "main"
    room_id: str
    tenant_name: str
    tenant_phone: str
    tenant_email: str = ""
    move_in_date: str
    lease_term_months: int = 12
    deposit: int = 5000000
    monthly_rent: int = 500000
    first_payment_type: str = "deposit_part"
    first_payment_amount: int = 300000

class SignAndPayReq(BaseModel):
    tenant_signature: str
    payment_method: str = "CARD"

class MaintenanceCreateReq(BaseModel):
    branch: str = "main"
    room_id: str
    tenant_name: str
    tenant_phone: str
    category: str = "facility"
    title: str
    description: str
    photos: List[str] = []

class CheckInConfirmReq(BaseModel):
    application_id: str = ""
    branch: str = "main"
    room_id: str
    tenant_name: str
    inspection_notes: str = ""
    photos: List[str] = []

class ListingCreateReq(BaseModel):
    branch: str = "main"
    room_id: str
    title: str
    deposit: int
    monthly_rent: int
    maintenance_fee: int = 50000
    fee_includes: List[str] = ["인터넷", "공용청소", "수도"]
    options: List[str] = ["에어컨", "냉장고", "세탁기", "인덕션", "도어락"]
    available_from: str = ""
    floor: int = 2
    area_sqm: float = 21.0
    images: List[str] = []

class ChatSendReq(BaseModel):
    branch: str = "main"
    room_id: str
    sender_role: str = "tenant"   # tenant or landlord
    sender_name: str = "임차인"
    sender_phone: str = ""
    message: str
    msg_type: str = "text"        # text, tour_request, system_notice, action_link
    is_tour_request: bool = False
    tour_date: str = ""
    quick_action_url: str = ""
    photos: List[str] = Field(default_factory=list) # 첨부된 현장 사진 (Base64 data URL 또는 웹 경로)


# ─────────────────────────────────────────────────────────────
# 2. Public APIs (임차인용 공개 매물 및 비대면 계약)
# ─────────────────────────────────────────────────────────────
@router.get("/public/listings")
def get_public_listings(branch: Optional[str] = None):
    """공개 매물 광고 리스트 조회."""
    listings = RentalListing.all()
    if branch:
        listings = [l for l in listings if getattr(l, "branch", "") == branch]
    active_listings = [l.model_dump() for l in listings if getattr(l, "is_active", True)]
    return {"listings": active_listings, "total": len(active_listings)}


@router.get("/public/listings/{listing_id}")
def get_listing_detail(listing_id: str):
    """매물 상세 정보 조회."""
    l = RentalListing.get(listing_id)
    if not l:
        raise HTTPException(status_code=404, detail="매물을 찾을 수 없습니다.")
    return {"listing": l.model_dump()}


@router.post("/public/apply")
def submit_application(req: ApplicationCreateReq):
    """온라인 입주 신청서 제출."""
    app = RentalApplication(
        listing_id=req.listing_id,
        branch=req.branch,
        room_id=req.room_id,
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        tenant_email=req.tenant_email,
        move_in_date=req.move_in_date,
        lease_term_months=req.lease_term_months,
        deposit=req.deposit,
        monthly_rent=req.monthly_rent,
        first_payment_type=req.first_payment_type,
        first_payment_amount=req.first_payment_amount,
        status="PENDING_REVIEW"
    )
    app.save("tenant_applied_online")
    return {"success": True, "application_id": app.id, "message": "입주 신청이 성공적으로 접수되었습니다. 임대인 검토 후 안내 문자가 발송됩니다."}


@router.get("/public/application/{app_id}")
def get_application(app_id: str):
    """신청서 조회 및 전자계약서 상태 확인."""
    app = RentalApplication.get(app_id)
    if not app:
        target_room = app_id.replace("app_main_", "").replace("_active", "").replace("호", "")
        for a in RentalApplication.all():
            if getattr(a, "room_id", "") == target_room:
                app = a
                break
    if not app:
        target_room = app_id.replace("app_main_", "").replace("_active", "").replace("호", "")
        app = RentalApplication(
            id=f"app_main_{target_room}",
            listing_id=f"lst_{target_room}",
            branch="main",
            room_id=target_room,
            tenant_name="김세입",
            tenant_phone="010-9876-5432",
            move_in_date="2025-03-01",
            lease_term_months=12,
            deposit=5000000,
            monthly_rent=500000,
            status="PAID_CONFIRMED",
            first_payment_type="full_deposit",
            first_payment_amount=5000000,
            tenant_signature="김세입_인",
            is_contract_signed=True,
            is_payment_confirmed=True
        )
        app.save("auto_tenant_lounge_session")

    
    legal_guide = {
        "required": app.deposit > 60000000 or app.monthly_rent > 300000,
        "deadline_days": 30,
        "portal_url": "https://rtms.molit.go.kr",
        "description": "보증금 6천만원 초과 또는 월세 30만원 초과 시, 계약 체결일로부터 30일 이내에 관할 주민센터 또는 부동산거래관리시스템에 주택임대차 계약 신고를 진행해야 합니다."
    }
    
    return {"application": app.model_dump(), "legal_guide": legal_guide}


@router.post("/public/application/{app_id}/sign-and-pay")
def sign_and_pay(app_id: str, req: SignAndPayReq):
    """임차인 전자서명 및 첫 결제 완료 (최종 계약 체결)."""
    app = RentalApplication.get(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="신청서를 찾을 수 없습니다.")
    
    try:
        app.confirm_payment_and_sign(tenant_sig=req.tenant_signature, method=req.payment_method)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"success": True, "status": app.status, "message": "계약 체결 및 결제가 정상 완료되었습니다. 입실 체크인 안내가 발송됩니다."}


# ─────────────────────────────────────────────────────────────
# 3. Tenant Lounge APIs (입실 및 거주 소통)
# ─────────────────────────────────────────────────────────────
@router.get("/public/lounge/{branch}/{room_id}")
def get_tenant_lounge(branch: str, room_id: str):
    """세입자 라운지 정보 (도어락 안내, 수납현황, 수리요청 내역)."""
    checkins = [c for c in CheckInRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    checkin_info = checkins[-1].model_dump() if checkins else {
        "door_lock_code": "입실 당일 오전 10시 자동 안내",
        "wifi_ssid": "Chicvill_Free_WiFi",
        "wifi_pw": "chicvill1212!",
        "tenant_confirmed": False
    }

    reqs = [r.model_dump() for r in MaintenanceRequest.all() if getattr(r, "branch", "") == branch and getattr(r, "room_id", "") == room_id]
    
    return {
        "branch": branch,
        "room_id": room_id,
        "checkin": checkin_info,
        "maintenance_history": reqs,
        "payment_info": {
            "account": "카카오뱅크 3333-01-9876543 (예금주: MQ임대관리)",
            "monthly_rent": 500000,
            "due_day": "매월 25일"
        }
    }


@router.post("/public/lounge/maintenance")
def submit_maintenance_request(req: MaintenanceCreateReq):
    """1:1 민원 및 수리 요청 접수."""
    m = MaintenanceRequest(
        branch=req.branch,
        room_id=req.room_id,
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        category=req.category,
        title=req.title,
        description=req.description,
        photos=req.photos,
        status="SUBMITTED"
    )
    m.save("maintenance_submitted")
    return {"success": True, "request_id": m.id, "message": "민원이 정상 접수되었습니다. 관리자가 신속히 확인 후 답변 드립니다."}


@router.post("/public/lounge/checkin")
def confirm_checkin(req: CheckInConfirmReq):
    """입실 시설 점검표 등록 및 체크인 확인."""
    cr = CheckInRecord(
        application_id=req.application_id,
        branch=req.branch,
        room_id=req.room_id,
        tenant_name=req.tenant_name
    )
    cr.confirm_checkin(notes=req.inspection_notes, photos=req.photos)
    return {"success": True, "message": "입실 시설 점검 및 체크인이 완료되었습니다."}


# ─────────────────────────────────────────────────────────────
# 4. Admin APIs (임대인 관제 & 승인 - rent2 엔진 통합)
# ─────────────────────────────────────────────────────────────
@router.get("/admin/overview")
def get_admin_overview(branch: str = Query("main")):
    """임대인 관제 대시보드 요약 (rent2 호실 현황 + rent3 신규 신청 카운트)."""
    all_apps = [a for a in RentalApplication.all() if getattr(a, "branch", "main") == branch]
    pending_apps = [a for a in all_apps if getattr(a, "status", "") == "PENDING_REVIEW"]
    
    all_maint = [m for m in MaintenanceRequest.all() if getattr(m, "branch", "main") == branch]
    active_maint = [m for m in all_maint if getattr(m, "status", "") != "RESOLVED"]
    
    room_count = 0
    occupied_count = 0
    rooms_summary = []
    
    if Building:
        try:
            b = Building.get(branch)
            if b and hasattr(b, "rooms"):
                rooms_data = b.rooms() if callable(b.rooms) else b.rooms
                if isinstance(rooms_data, dict):
                    room_count = len(rooms_data)
                    for r_id, r in rooms_data.items():
                        tenant = r.get("name") if isinstance(r, dict) else getattr(r, "tenant_name", "")
                        is_occ = bool(tenant and tenant != "공실")
                        if is_occ:
                            occupied_count += 1
                        rooms_summary.append({
                            "room_id": str(r_id),
                            "tenant_name": tenant or "공실",
                            "rent": r.get("rent", 0) if isinstance(r, dict) else getattr(r, "rent", 0),
                            "deposit": r.get("deposit", 0) if isinstance(r, dict) else getattr(r, "deposit", 0),
                            "is_occupied": is_occ
                        })
        except Exception as e:
            print(f"[AdminOverview] rent2 Building 연동 경고: {e}")

    return {
        "branch": branch,
        "kpi": {
            "total_rooms": room_count or 29,
            "occupied_rooms": occupied_count or 25,
            "occupancy_rate": round(((occupied_count or 25) / (room_count or 29)) * 100, 1),
            "pending_applications": len(pending_apps),
            "active_maintenance": len(active_maint)
        },
        "pending_applications": [a.model_dump() for a in pending_apps],
        "active_maintenance": [m.model_dump() for m in active_maint],
        "rooms": rooms_summary[:10]
    }


@router.get("/admin/applications")
def get_applications(branch: str = Query("main"), status: Optional[str] = None):
    """지점별 입주 신청서 목록."""
    apps = [a for a in RentalApplication.all() if getattr(a, "branch", "main") == branch]
    if status:
        apps = [a for a in apps if getattr(a, "status", "") == status]
    return {"applications": [a.model_dump() for a in apps]}


@router.post("/admin/applications/{app_id}/approve")
def approve_application(app_id: str, payload: Dict[str, Any] = Body(default={})):
    """임대인 입주 신청 승인."""
    app = RentalApplication.get(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="신청서를 찾을 수 없습니다.")
    app.approve(landlord_sig=payload.get("signature", "APPROVED_BY_LANDLORD"))
    return {"success": True, "message": f"{app.tenant_name}님의 입주 신청이 승인되었습니다. 결제 및 전자서명 링크가 전송되었습니다."}


@router.post("/admin/applications/{app_id}/reject")
def reject_application(app_id: str, payload: Dict[str, Any] = Body(...)):
    """임대인 입주 신청 반려."""
    app = RentalApplication.get(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="신청서를 찾을 수 없습니다.")
    app.reject(reason=payload.get("reason", "관리자 심사 반려"))
    return {"success": True, "message": "신청서가 반려 처리되었습니다."}


@router.get("/admin/maintenance")
def get_maintenance_list(branch: str = Query("main")):
    """지점별 민원/수리 요청 목록."""
    m_list = [m for m in MaintenanceRequest.all() if getattr(m, "branch", "main") == branch]
    return {"maintenance": [m.model_dump() for m in m_list]}


@router.post("/admin/maintenance/{req_id}/reply")
def reply_maintenance(req_id: str, payload: Dict[str, Any] = Body(...)):
    """민원 답변 및 조치 완료."""
    m = MaintenanceRequest.get(req_id)
    if not m:
        raise HTTPException(status_code=404, detail="민원 내역을 찾을 수 없습니다.")
    m.resolve(reply=payload.get("reply", "조치 완료되었습니다."))
    return {"success": True, "message": "민원 조치 내역이 세입자 라운지에 전달되었습니다."}


@router.post("/admin/listings")
def create_listing(req: ListingCreateReq):
    """공실 매물 광고 등록."""
    l = RentalListing(
        branch=req.branch,
        room_id=req.room_id,
        title=req.title,
        deposit=req.deposit,
        monthly_rent=req.monthly_rent,
        maintenance_fee=req.maintenance_fee,
        fee_includes=req.fee_includes,
        options=req.options,
        available_from=req.available_from,
        floor=req.floor,
        area_sqm=req.area_sqm,
        images=req.images
    )
    l.save("listing_created")
    return {"success": True, "listing_id": l.id, "message": "공실 매물 광고가 등록되었습니다."}


# ─────────────────────────────────────────────────────────────
# 5. Co-Signing Room APIs (현물 동행 실시간 동시 계약)
# ─────────────────────────────────────────────────────────────
class CoSignStartReq(BaseModel):
    branch: str = "main"
    room_id: str
    deposit: int = 5000000
    monthly_rent: int = 520000

class CoSignStep1Req(BaseModel):
    issue_no: str = "2026-0482-9912"
    checked: bool = True

class CoSignStep2Req(BaseModel):
    checks: Dict[str, bool]
    notes: str = ""
    photos: List[str] = []

class CoSignStep3Req(BaseModel):
    deposit: int
    monthly_rent: int
    maintenance_fee: int = 50000
    move_in_date: str
    lease_months: int = 12
    parking_required: bool = False
    car_number: str = ""
    special_terms: str = "실내 금연, 반려동물 사전 협의 필요"
    tenant_name: str
    tenant_phone: str
    tenant_email: str = ""

class CoSignSignReq(BaseModel):
    role: str # "landlord" or "tenant"
    signature: str
    ci: str = ""
    method: str = "PASS"

class CoSignPayReq(BaseModel):
    method: str = "CARD"


@router.post("/cosign/start")
def start_cosign_session(req: CoSignStartReq):
    """임대인이 현장 동행 실시간 계약 세션 생성 (QR코드 생성용)."""
    session = CoSigningSession(
        branch=req.branch,
        room_id=req.room_id,
        deposit=req.deposit,
        monthly_rent=req.monthly_rent,
        status="DRAFT"
    )
    session.save("cosign_session_started")
    return {
        "success": True,
        "session_id": session.id,
        "join_url": f"/rent3/cosign.html?session_id={session.id}",
        "message": f"[{req.room_id}호] 현장 동행 실시간 계약 세션이 시작되었습니다."
    }


@router.get("/cosign/{session_id}")
def get_cosign_session(session_id: str):
    """실시간 계약 세션 데이터 및 현재 단계 조회 (양측 화면 동기화)."""
    session = CoSigningSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="계약 세션을 찾을 수 없습니다.")
    return {"session": session.model_dump()}


@router.post("/cosign/{session_id}/step1")
def update_cosign_step1(session_id: str, req: CoSignStep1Req):
    """Step 1: 대법원 실시간 등기부 및 소유권 확인 완료."""
    session = CoSigningSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="계약 세션을 찾을 수 없습니다.")
    session.update_step1(issue_no=req.issue_no, checked=req.checked)
    return {"success": True, "step": session.current_step, "session": session.model_dump()}


@router.post("/cosign/{session_id}/step2")
def update_cosign_step2(session_id: str, req: CoSignStep2Req):
    """Step 2: 부동산 실물 옵션 및 시설 현장 점검표 등록."""
    session = CoSigningSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="계약 세션을 찾을 수 없습니다.")
    session.update_step2(checks=req.checks, notes=req.notes, photos=req.photos)
    return {"success": True, "step": session.current_step, "session": session.model_dump()}


@router.post("/cosign/{session_id}/step3")
def update_cosign_step3(session_id: str, req: CoSignStep3Req):
    """Step 3: 계약 조건 합의 및 임차인 정보 등록."""
    session = CoSigningSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="계약 세션을 찾을 수 없습니다.")
    session.update_step3(req.model_dump())
    return {"success": True, "step": session.current_step, "session": session.model_dump()}


@router.post("/cosign/{session_id}/sign")
def sign_cosign(session_id: str, req: CoSignSignReq):
    """Step 4: 임대인/임차인 모바일 전자서명 날인."""
    session = CoSigningSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="계약 세션을 찾을 수 없습니다.")
    session.sign(role=req.role, signature=req.signature, ci=req.ci, method=req.method)
    return {
        "success": True,
        "landlord_signed": session.landlord_signed,
        "tenant_signed": session.tenant_signed,
        "status": session.status,
        "session": session.model_dump()
    }


@router.post("/cosign/{session_id}/pay")
def pay_cosign(session_id: str, req: CoSignPayReq):
    """Step 5: 계약금 결제 완료 및 계약 최종 확정 -> 전입신고/확정일자 가이드 제공."""
    session = CoSigningSession.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="계약 세션을 찾을 수 없습니다.")
    session.complete_payment(method=req.method)
    
    legal_guide = {
        "move_in_report_url": "https://www.gov.kr/portal/main",
        "rtms_report_url": "https://rtms.molit.go.kr",
        "deadline_days": 30,
        "message": "계약 체결일로부터 30일 이내에 국토교통부 거래관리시스템(RTMS)에 전월세 계약 신고 및 입주 시 정부24 전입신고를 완료해주세요."
    }
    
    return {
        "success": True,
        "status": session.status,
        "legal_guide": legal_guide,
        "session": session.model_dump()
    }


# ─────────────────────────────────────────────────────────────
# 6. Identity Verification & Social SignUp APIs (신원확인 & 가입)
# ─────────────────────────────────────────────────────────────
class PhoneVerifySendReq(BaseModel):
    name: str
    phone: str
    birth_date: str = ""

class PhoneVerifyConfirmReq(BaseModel):
    phone: str
    code: str

class SocialVerifyReq(BaseModel):
    provider: str # "kakao" or "google"
    access_token: str = "mock_token"
    name: str = ""
    email: str = ""
    phone: str = ""

class SignUpReq(BaseModel):
    role: str # "landlord" or "tenant"
    username: str
    password: str
    name: str
    phone: str
    email: str = ""
    branch: Optional[str] = None
    verified_by: str = "PHONE"


# 메모리 인증코드 보관소 (휴대폰 번호: 6자리 코드)
_phone_verify_codes: Dict[str, str] = {}


@router.post("/auth/verify-phone/send")
def send_phone_verification(req: PhoneVerifySendReq):
    """휴대폰 본인인증 6자리 SMS 발송 (시뮬레이션)."""
    # 6자리 인증번호 생성 (시뮬레이션 기본: 123456)
    code = "123456"
    _phone_verify_codes[req.phone] = code
    return {
        "success": True,
        "message": f"[{req.name}]님의 휴대폰({req.phone})으로 본인확인 인증번호가 발송되었습니다.",
        "simulated_code": code # 개발/테스트 편의 제공
    }


@router.post("/auth/verify-phone/confirm")
def confirm_phone_verification(req: PhoneVerifyConfirmReq):
    """휴대폰 본인인증 6자리 확인 -> 본인확인 토큰 발급."""
    stored_code = _phone_verify_codes.get(req.phone, "123456")
    if req.code != stored_code and req.code != "123456":
        raise HTTPException(status_code=400, detail="인증번호가 일치하지 않습니다. 다시 입력해주세요.")
    
    return {
        "success": True,
        "is_verified": True,
        "method": "PHONE_PASS",
        "ci": f"CI_VERIFIED_{hash(req.phone)}",
        "message": "휴대폰 본인확인이 정상적으로 완료되었습니다."
    }


@router.post("/auth/social-verify")
def social_verification(req: SocialVerifyReq):
    """카카오 / 구글 간편 신원인증 연동."""
    # 소셜 프로바이더 프로필 확인 시뮬레이션
    profile = {
        "provider": req.provider.upper(),
        "is_verified": True,
        "name": req.name or ("카카오인증회원" if req.provider == "kakao" else "구글인증회원"),
        "email": req.email or f"{req.provider}_user@mqnet.co.kr",
        "phone": req.phone or "010-8899-1234",
        "ci": f"CI_{req.provider.upper()}_{secrets.token_hex(8)}"
    }
    return {
        "success": True,
        "profile": profile,
        "message": f"{req.provider.upper()} 간편 신원확인이 완료되었습니다."
    }


@router.post("/auth/signup")
def register_user(req: SignUpReq):
    """신원확인이 완료된 사용자 회원가입 (임대인 또는 임차인)."""
    try:
        from core.auth import User
        # 기존 중복 확인
        existing = [u for u in User.all() if getattr(u, "username", "") == req.username]
        if existing:
            raise HTTPException(status_code=400, detail="이미 사용 중인 아이디입니다.")
        
        user = User(
            username=req.username,
            name=req.name,
            role="branch_admin" if req.role == "landlord" else "user",
            branch=req.branch if req.role == "landlord" else None
        )
        user.set_password(req.password)
        user.save("user_registered_with_identity_verification")
        
        return {
            "success": True,
            "username": user.username,
            "role": req.role,
            "name": user.name,
            "verified_by": req.verified_by,
            "message": f"[{user.name}]님의 회원가입 및 신원확인이 완료되었습니다."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────
# 7. In-App Direct 1:1 Messenger APIs (카카오톡·문자 대체 인앱 실시간 채팅)
# ─────────────────────────────────────────────────────────────
@router.get("/chat/{branch}/{room_id}")
def get_room_chat_messages(branch: str, room_id: str):
    """특정 호실의 1:1 직거래 메시지 및 대면 약속 내역 조회 (시간순)."""
    msgs = [m for m in ChatMessage.all() if getattr(m, "branch", "") == branch and getattr(m, "room_id", "") == room_id]
    
    # 기본 초기 환영 메시지 생성 (채팅이 처음일 때)
    if not msgs:
        welcome = ChatMessage(
            branch=branch,
            room_id=room_id,
            sender_role="landlord",
            sender_name="김임대 (임대인)",
            message=f"안녕하세요! {room_id}호 임대인 김임대입니다. 실물 방 상태, 옵션 문의나 대면 방문 투어 희망 일정을 편하게 남겨주세요.",
            msg_type="text",
            is_read=True
        )
        welcome.save("seeded_chat_welcome")
        msgs = [welcome]

    sorted_msgs = sorted(msgs, key=lambda x: getattr(x, "created_at", ""))
    contact_info = {
        "landlord_phone": "010-2345-6789",
        "landlord_name": "김임대",
        "building_address": "서울특별시 강남구 역삼동 123 시크빌"
    }
    
    return {
        "branch": branch,
        "room_id": room_id,
        "contact_info": contact_info,
        "total": len(sorted_msgs),
        "messages": [m.model_dump() for m in sorted_msgs]
    }


@router.post("/chat/{branch}/{room_id}/send")
def send_chat_message(branch: str, room_id: str, req: ChatSendReq):
    """1:1 직거래 메시지 또는 대면 약속 전송."""
    msg = ChatMessage(
        branch=branch,
        room_id=room_id,
        sender_role=req.sender_role,
        sender_name=req.sender_name or ("김임대 (임대인)" if req.sender_role == "landlord" else "임차인"),
        sender_phone=req.sender_phone,
        message=req.message,
        msg_type=req.msg_type,
        is_tour_request=req.is_tour_request,
        tour_date=req.tour_date,
        quick_action_url=req.quick_action_url,
        photos=req.photos,
        is_read=False
    )
    msg.save(f"chat_{room_id}_{req.sender_role}")
    return {"success": True, "message_id": msg.id, "chat": msg.model_dump()}


@router.post("/chat/{branch}/{room_id}/read")
def mark_chat_read(branch: str, room_id: str, role: str = Query("landlord")):
    """상대방이 보낸 메시지 모두 읽음 처리."""
    target_role = "tenant" if role == "landlord" else "landlord"
    msgs = [m for m in ChatMessage.all() if getattr(m, "branch", "") == branch and getattr(m, "room_id", "") == room_id and getattr(m, "sender_role", "") == target_role and not getattr(m, "is_read", False)]
    for m in msgs:
        m.is_read = True
        m.save("chat_marked_read")
    return {"success": True, "read_count": len(msgs)}


@router.post("/chat/{branch}/{room_id}/quick-reply")
def send_quick_reply(branch: str, room_id: str, template: str = Query(...)):
    """임대인 원클릭 인앱 퀵 답변 템플릿 전송 (SMS/카카오톡 발송 비용 0원 인앱 완결)."""
    templates = {
        "tour_accept": {
            "message": "✅ 신청하신 일시에 실물 방문 투어가 확정되었습니다. 약속 시간에 맞춰 건물 1층 공동현관 앞에서 뵙겠습니다!",
            "msg_type": "tour_accept"
        },
        "cosign_ready": {
            "message": "📜 대법원 실시간 등기부 검증 및 현장 실물 동행 전자 계약 준비가 완료되었습니다. 아래 링크를 통해 5단계 실시간 동시 계약을 체결해주세요.",
            "msg_type": "action_link",
            "quick_action_url": f"/rent3/cosign.html?branch={branch}&room={room_id}"
        },
        "access_info": {
            "message": "🔑 입주를 환영합니다! 세대 디지털 도어락 비밀번호 및 차량 주차 등록은 세입자 전용 라운지에서 확인하실 수 있습니다.",
            "msg_type": "action_link",
            "quick_action_url": f"/rent3/public.html?room={room_id}#lounge"
        },
        "payment_reminder": {
            "message": "💳 이번 달 정기 임대료 및 관리비 청구서가 발행되었습니다. 세입자 라운지에서 간편결제(신용카드/카카오페이)로 납부하실 수 있습니다.",
            "msg_type": "action_link",
            "quick_action_url": f"/rent3/public.html?room={room_id}#lounge"
        }
    }
    tpl = templates.get(template, {
        "message": "문의해주셔서 감사합니다. 확인 후 신속하게 처리해 드리겠습니다.",
        "msg_type": "text"
    })
    msg = ChatMessage(
        branch=branch,
        room_id=room_id,
        sender_role="landlord",
        sender_name="김임대 (임대인)",
        message=tpl["message"],
        msg_type=tpl["msg_type"],
        quick_action_url=tpl.get("quick_action_url", ""),
        is_read=False
    )
    msg.save("quick_reply_sent")
    return {"success": True, "chat": msg.model_dump()}


@router.get("/admin/chats")
def get_admin_active_chats(branch: str = Query("main")):
    """임대인 관제용 활성 문의 채팅방 목록 및 실시간 통계."""
    all_msgs = [m for m in ChatMessage.all() if getattr(m, "branch", "main") == branch]
    
    if not all_msgs:
        m1 = ChatMessage(branch=branch, room_id="201", sender_role="tenant", sender_name="홍길동 (예비세입자)", message="안녕하세요! 이번 주 토요일 오후 3시에 방 보러 갈 수 있을까요?", is_tour_request=True, tour_date="2026-10-31 15:00", is_read=False)
        m1.save("seed_chat_1")
        m2 = ChatMessage(branch=branch, room_id="203", sender_role="tenant", sender_name="박직객 (세입자)", message="임대인님, 주차 등록 완료했습니다. 도어락 비밀번호도 잘 작동하네요!", msg_type="text", is_read=True)
        m2.save("seed_chat_2")
        m3 = ChatMessage(branch=branch, room_id="203", sender_role="landlord", sender_name="김임대 (임대인)", message="네 확인 감사합니다. 편안하게 지내시고 문제 생기면 언제든 라운지 1:1 채팅 주세요.", msg_type="text", is_read=True)
        m3.save("seed_chat_3")
        all_msgs = [m1, m2, m3]

    room_groups = {}
    for m in all_msgs:
        r_id = getattr(m, "room_id", "")
        if r_id not in room_groups:
            room_groups[r_id] = []
        room_groups[r_id].append(m)

    chat_rooms = []
    for r_id, m_list in room_groups.items():
        sorted_m = sorted(m_list, key=lambda x: getattr(x, "created_at", ""))
        last_m = sorted_m[-1]
        unread = sum(1 for m in sorted_m if getattr(m, "sender_role", "") == "tenant" and not getattr(m, "is_read", False))
        tenant_name = next((getattr(m, "sender_name", "") for m in sorted_m if getattr(m, "sender_role", "") == "tenant"), f"{r_id}호 문의자")
        chat_rooms.append({
            "room_id": r_id,
            "branch": branch,
            "tenant_name": tenant_name,
            "last_message": getattr(last_m, "message", ""),
            "last_sender": getattr(last_m, "sender_name", ""),
            "last_time": getattr(last_m, "created_at", ""),
            "unread_count": unread,
            "is_tour_request": getattr(last_m, "is_tour_request", False),
            "tour_date": getattr(last_m, "tour_date", ""),
            "messages": [m.model_dump() for m in sorted_m]
        })

    chat_rooms.sort(key=lambda x: x["last_time"], reverse=True)
    return {
        "branch": branch,
        "total_rooms": len(chat_rooms),
        "total_unread": sum(r["unread_count"] for r in chat_rooms),
        "chat_rooms": chat_rooms,
        "rooms": chat_rooms
    }



# ─────────────────────────────────────────────────────────────
# 8. Check-In & Parking Management APIs (도어락 발급 & 주차 등록)
# ─────────────────────────────────────────────────────────────
class ParkingRegisterReq(BaseModel):
    branch: str = "main"
    room_id: str
    tenant_name: str
    tenant_phone: str
    car_number: str
    car_model: str = ""
    slot_type: str = "자유주차"
    slot_number: str = ""

class CheckInConfirmReq(BaseModel):
    application_id: str
    inspection_notes: str = ""
    photos: List[str] = Field(default_factory=list)

class DoorLockUpdateReq(BaseModel):
    branch: str = "main"
    room_id: str
    door_lock_code: str
    building_entry_code: str = "#1234*"
    wifi_ssid: str = "MQNET_WIFI_5G"
    wifi_pw: str = "rent2026pw"
    notes: str = ""


@router.get("/tenant/checkin-info/{application_id}")
def get_tenant_checkin_info(application_id: str):
    """입주 당일 도어락 비밀번호, 공동현관, 와이파이, 주차 등록 상태 조회."""
    app = RentalApplication.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail="신청서를 찾을 수 없습니다.")

    branch = getattr(app, "branch", "main")
    room_id = getattr(app, "room_id", "")
    is_paid = getattr(app, "status", "") == "PAID_CONFIRMED"

    # 1. 도어락 및 출입 정보 조회 (없으면 기본값 생성)
    access_infos = [a for a in RoomAccessInfo.all() if getattr(a, "branch", "") == branch and getattr(a, "room_id", "") == room_id]
    if access_infos:
        access_info = access_infos[-1]
    else:
        access_info = RoomAccessInfo(
            branch=branch,
            room_id=room_id,
            door_lock_code=f"{int(room_id) * 11 % 9000 + 1000}*", # 호실별 고유 번호 생성
            building_entry_code="#1234*",
            wifi_ssid="MQNET_DIRECT_5G",
            wifi_pw="rent2026pw"
        )
        access_info.save("seed_room_access")

    # 2. 주차 등록 내역 조회
    parkings = [p for p in ParkingRegistration.all() if getattr(p, "branch", "") == branch and getattr(p, "room_id", "") == room_id and getattr(p, "is_active", True)]
    parking_record = parkings[-1].model_dump() if parkings else None

    # 3. 입실 체크인 기록 조회
    checkins = [c for c in CheckInRecord.all() if getattr(c, "application_id", "") == application_id]
    checkin_record = checkins[-1].model_dump() if checkins else None

    # 잔금/계약 미체결 시 도어락 마스킹 보호
    door_code_display = access_info.door_lock_code if is_paid else "**** (계약금 결제 완료 후 입주일 당일 노출)"
    building_code_display = access_info.building_entry_code if is_paid else "****"

    return {
        "application_id": application_id,
        "is_paid": is_paid,
        "branch": branch,
        "room_id": room_id,
        "move_in_date": app.move_in_date,
        "tenant_name": app.tenant_name,
        "access": {
            "door_lock_code": door_code_display,
            "building_entry_code": building_code_display,
            "wifi_ssid": access_info.wifi_ssid,
            "wifi_pw": access_info.wifi_pw,
            "notes": access_info.notes
        },
        "parking": parking_record,
        "checkin": checkin_record
    }


@router.post("/tenant/checkin-confirm")
def confirm_tenant_checkin(req: CheckInConfirmReq):
    """세입자 당일 첫 입실 확인 및 입실 점검 메모 제출."""
    app = RentalApplication.get(req.application_id)
    if not app:
        raise HTTPException(status_code=404, detail="신청서를 찾을 수 없습니다.")

    branch = getattr(app, "branch", "main")
    room_id = getattr(app, "room_id", "")

    # CheckInRecord 생성 또는 갱신
    checkins = [c for c in CheckInRecord.all() if getattr(c, "application_id", "") == req.application_id]
    if checkins:
        rec = checkins[-1]
    else:
        rec = CheckInRecord(
            application_id=req.application_id,
            branch=branch,
            room_id=room_id,
            tenant_name=app.tenant_name
        )
    rec.confirm_checkin(notes=req.inspection_notes, photos=req.photos)
    return {
        "success": True,
        "message": f"[{room_id}호] 입실 체크인이 완료되었습니다. 편안한 거주 되시길 바랍니다!",
        "checkin": rec.model_dump()
    }


@router.post("/tenant/parking/register")
def register_parking_vehicle(req: ParkingRegisterReq):
    """세입자 차량 번호 및 차종 등록."""
    # 기존 등록 비활성화 후 신규 등록
    for p in ParkingRegistration.all():
        if getattr(p, "branch", "") == req.branch and getattr(p, "room_id", "") == req.room_id:
            p.is_active = False
            p.save("parking_superseded")

    parking = ParkingRegistration(
        branch=req.branch,
        room_id=req.room_id,
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        car_number=req.car_number,
        car_model=req.car_model,
        slot_type=req.slot_type,
        slot_number=req.slot_number
    )
    parking.save("parking_registered")
    return {
        "success": True,
        "message": f"[{req.room_id}호] 차량({req.car_number}) 주차 등록이 완료되었습니다.",
        "parking": parking.model_dump()
    }


@router.get("/admin/parking")
def get_admin_parking_records(branch: str = Query("main")):
    """임대인용 지점별 등록 차량 대장 (외부 불법 주차 단속용)."""
    parkings = [p for p in ParkingRegistration.all() if getattr(p, "branch", "main") == branch and getattr(p, "is_active", True)]
    return {
        "branch": branch,
        "total_vehicles": len(parkings),
        "vehicles": [p.model_dump() for p in parkings]
    }


@router.post("/admin/doorlock/update")
def update_room_doorlock(req: DoorLockUpdateReq):
    """임대인용 호실별 도어락 비밀번호 변경/설정."""
    infos = [a for a in RoomAccessInfo.all() if getattr(a, "branch", "") == req.branch and getattr(a, "room_id", "") == req.room_id]
    if infos:
        info = infos[-1]
    else:
        info = RoomAccessInfo(branch=req.branch, room_id=req.room_id)
    
    info.door_lock_code = req.door_lock_code
    info.building_entry_code = req.building_entry_code
    info.wifi_ssid = req.wifi_ssid
    info.wifi_pw = req.wifi_pw
    info.notes = req.notes
    info.save("doorlock_updated_by_admin")

    return {
        "success": True,
        "message": f"[{req.room_id}호] 도어락 및 출입 정보가 성공적으로 변경되었습니다.",
        "access": info.model_dump()
    }


# ─────────────────────────────────────────────────────────────
# 9. Tenant Billing, Payment & Electronic Receipt APIs (수납 & 영수증)
# ─────────────────────────────────────────────────────────────
class BillPayReq(BaseModel):
    payment_method: str = "CARD" # CARD, TRANSFER, KAKAO_PAY
    tenant_name: str = "홍길동"


@router.get("/tenant/bills/{branch}/{room_id}")
def get_tenant_bills(branch: str, room_id: str):
    """해당 호실의 월별 청구서, 당월 미납 청구 내역 및 납부 히스토리 조회 (rent2 실시간 장부 연동)."""
    bills = [b for b in MonthlyBill.all() if getattr(b, "branch", "") == branch and getattr(b, "room_id", "") == room_id]

    # 기본 청구서가 없으면 9월(완납) 및 10월(당월 미납) 시드 자동 생성
    if not bills:
        # 9월 완납 청구서
        paid_bill = MonthlyBill(
            branch=branch,
            room_id=room_id,
            billing_month="2026-09",
            tenant_name="홍길동",
            tenant_phone="010-1234-5678",
            rent_amount=500000,
            maint_amount=50000,
            due_date="2026-09-30",
            is_paid=True,
            paid_at="2026-09-25 10:30:15",
            payment_method="신용카드 (일시불)",
            receipt_id="RCP-20260925-0912"
        )
        paid_bill.save("seed_bill_sep")

        # 9월 영수증 시드
        r_sep = PaymentReceipt(
            id="RCP-20260925-0912",
            receipt_no="RCP-20260925-0912",
            bill_id=paid_bill.id,
            branch=branch,
            room_id=room_id,
            tenant_name="홍길동",
            tenant_phone="010-1234-5678",
            rent_amount=500000,
            maint_amount=50000,
            total_amount=550000,
            payment_method="신용카드 (일시불)",
            approval_no="84920134",
            paid_at="2026-09-25 10:30:15"
        )
        r_sep.save("seed_receipt_sep")

        # 10월 당월 미납 청구서
        curr_bill = MonthlyBill(
            branch=branch,
            room_id=room_id,
            billing_month="2026-10",
            tenant_name="홍길동",
            tenant_phone="010-1234-5678",
            rent_amount=500000,
            maint_amount=50000,
            due_date="2026-10-31",
            is_paid=False
        )
        curr_bill.save("seed_bill_oct")
        bills = [curr_bill, paid_bill]

    # 정렬: 최신 월 순
    bills_sorted = sorted(bills, key=lambda x: getattr(x, "billing_month", ""), reverse=True)
    unpaid_bills = [b for b in bills_sorted if not getattr(b, "is_paid", False)]
    paid_bills = [b for b in bills_sorted if getattr(b, "is_paid", False)]

    # rent2 코어 장부와 상계 상태 동기화 확인
    rent2_status = "정상"
    if Building:
        try:
            b_obj = Building.get(branch)
            if b_obj and hasattr(b_obj, "rooms") and room_id in b_obj.rooms:
                room = b_obj.rooms[room_id]
                rent2_status = getattr(room, "status", "정상")
        except Exception:
            pass

    return {
        "branch": branch,
        "room_id": room_id,
        "current_unpaid": unpaid_bills[0].model_dump() if unpaid_bills else None,
        "total_unpaid_amount": sum(b.total_amount for b in unpaid_bills),
        "bills": [b.model_dump() for b in bills_sorted],
        "rent2_status": rent2_status
    }


@router.post("/tenant/bills/{branch}/{room_id}/pay")
def pay_tenant_bill(branch: str, room_id: str, req: BillPayReq):
    """당월 미납 월세 및 관리비 결제 처리 -> rent2 FIFO 상계 및 전자 영수증 발급."""
    bills = [b for b in MonthlyBill.all() if getattr(b, "branch", "") == branch and getattr(b, "room_id", "") == room_id and not getattr(b, "is_paid", False)]
    if not bills:
        raise HTTPException(status_code=400, detail="납부할 미납 청구서가 없습니다. 이미 완납되었습니다.")

    target_bill = bills[0]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt_no = f"RCP-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"
    approval_no = str(secrets.randbelow(89999999) + 10000000)

    # 1. 전자 영수증 생성 (국세청 증빙 규격)
    receipt = PaymentReceipt(
        receipt_no=receipt_no,
        bill_id=target_bill.id,
        branch=branch,
        room_id=room_id,
        tenant_name=req.tenant_name or target_bill.tenant_name,
        tenant_phone=target_bill.tenant_phone or "010-1234-5678",
        landlord_name="김임대",
        building_address="서울특별시 강남구 역삼동 123 시크빌",
        rent_amount=target_bill.rent_amount,
        maint_amount=target_bill.maint_amount,
        total_amount=target_bill.total_amount,
        payment_method="신용카드 (일시불)" if req.payment_method == "CARD" else ("간편결제 (카카오페이)" if req.payment_method == "KAKAO_PAY" else "가상계좌 이체"),
        approval_no=approval_no,
        paid_at=now_str
    )
    receipt.save("payment_receipt_issued")

    # 2. 청구서 완납 처리
    target_bill.is_paid = True
    target_bill.paid_at = now_str
    target_bill.payment_method = receipt.payment_method
    target_bill.receipt_id = receipt.id
    target_bill.save("bill_paid_confirmed")

    # 3. rent2 코어 엔진 호환 (FIFO 장부 반영)
    if Building:
        try:
            b_obj = Building.get(branch)
            if b_obj and hasattr(b_obj, "rooms") and room_id in b_obj.rooms:
                room = b_obj.rooms[room_id]
                room.status = "정상"
                b_obj.save(f"rent3_payment_fifo_{room_id}")
        except Exception as e:
            print(f"[Billing] rent2 FIFO 장부 동기화 경고: {e}")

    return {
        "success": True,
        "message": f"[{target_bill.billing_month}월] 월세 및 관리비({target_bill.total_amount:,}원) 납부가 정상 완료되었습니다.",
        "receipt": receipt.model_dump(),
        "bill": target_bill.model_dump()
    }


@router.get("/tenant/receipt/{receipt_id}")
def get_payment_receipt(receipt_id: str):
    """국세청 소득공제 및 지출증빙용 공인 전자 영수증 상세 반환."""
    receipt = PaymentReceipt.get(receipt_id)
    if not receipt:
        # 혹시 receipt_no로 검색
        all_r = [r for r in PaymentReceipt.all() if getattr(r, "receipt_no", "") == receipt_id]
        if all_r:
            receipt = all_r[-1]
    if not receipt:
        raise HTTPException(status_code=404, detail="해당 전자 영수증을 찾을 수 없습니다.")

    return {
        "success": True,
        "receipt": receipt.model_dump()
    }


# ─────────────────────────────────────────────────────────────
# 10. Check-Out & Deposit Settlement APIs (퇴실 검수 & 보증금 정산)
# ─────────────────────────────────────────────────────────────
class CheckOutInspectReq(BaseModel):
    unpaid_rent_deduction: int = 0
    water_fee: int = 15000
    electric_fee: int = 20000
    gas_fee: int = 10000
    utility_deduction: int = 45000
    cleaning_fee: int = 70000
    repair_deduction: int = 0
    damage_deduction: int = 70000
    damage_notes: str = "통상 손모 외 특이 파손 없음 (상태 양호)"
    other_deduction: int = 0
    checks: Dict[str, Any] = Field(default_factory=dict)
    photos: List[str] = Field(default_factory=list)
    refund_bank: str = "카카오뱅크"
    refund_account: str = "3333-01-2345678"
    refund_account_holder: str = ""


class CheckOutAgreeReq(BaseModel):
    signature: str
    refund_bank: str = ""
    refund_account: str = ""
    refund_account_holder: str = ""


class CheckOutCompleteReq(BaseModel):
    tx_ref: str = ""


@router.get("/checkout/list")
def list_checkouts(branch: str = Query("main")):
    """지점 전체 퇴실 정산 목록 및 KPI 통계 조회."""
    checkouts = [c for c in CheckOutRecord.all() if getattr(c, "branch", "") == branch]
    
    # 지점 내 관리 대상 호실들 추출 (신규 퇴실 점검 착수용)
    room_options = []
    if Building:
        try:
            b_obj = Building.get(branch)
            if b_obj and hasattr(b_obj, "rooms"):
                raw_rooms = b_obj.rooms() if callable(b_obj.rooms) else b_obj.rooms
                if raw_rooms:
                    for r_id, r in raw_rooms.items():
                        t_name = getattr(r, "tenant_name", "") or ""
                        dep = getattr(r, "deposit", 5000000) or 5000000
                        room_options.append({
                            "room_id": str(r_id),
                            "tenant_name": t_name or "(공실)",
                            "deposit": dep,
                            "is_occupied": bool(getattr(r, "is_occupied", False) or t_name)
                        })
        except Exception:
            pass

    # 기본 데모 데이터 시딩 (목록이 비어있을 경우)
    if not checkouts:
        c1 = CheckOutRecord(
            branch=branch,
            room_id="201",
            room_number="201호",
            tenant_name="이지은",
            tenant_phone="010-2345-6789",
            move_in_date="2025-10-28",
            move_out_date="2026-10-27",
            original_deposit=5000000,
            unpaid_rent_deduction=0,
            water_fee=14000,
            electric_fee=23000,
            gas_fee=8000,
            cleaning_fee=70000,
            repair_deduction=0,
            damage_notes="벽지 미세 생활 변색(자연마모 인정 - 임대인 무상 부담), 옵션 정상",
            refund_bank="카카오뱅크",
            refund_account="3333-02-9988776",
            refund_account_holder="이지은",
            status="AGREED",
            tenant_agreed=True,
            tenant_signature="SIG_IEUN_2026"
        )
        c1.calculate_refund()
        c1.save("seed_checkout_c1")

        c2 = CheckOutRecord(
            branch=branch,
            room_id="204",
            room_number="204호",
            tenant_name="강태양",
            tenant_phone="010-5544-3322",
            move_in_date="2024-10-15",
            move_out_date="2026-10-15",
            original_deposit=5000000,
            unpaid_rent_deduction=0,
            water_fee=16000,
            electric_fee=19000,
            gas_fee=12000,
            cleaning_fee=70000,
            repair_deduction=0,
            damage_notes="옵션 양호 및 청소 완료",
            refund_bank="토스뱅크",
            refund_account="1000-8877-6655",
            refund_account_holder="강태양",
            status="COMPLETED",
            tenant_agreed=True,
            refund_completed=True,
            refund_completed_at="2026-10-15 15:30:00",
            refund_tx_ref="TX-BANK-20261015-098"
        )
        c2.calculate_refund()
        c2.save("seed_checkout_c2")

        checkouts = [c1, c2]

    # KPI 계산
    inspecting_count = len([c for c in checkouts if c.status == "INSPECTING" or c.status == "CALCULATED"])
    agreed_count = len([c for c in checkouts if c.status == "AGREED"])
    completed_count = len([c for c in checkouts if c.status == "COMPLETED"])
    total_refund = sum(c.final_refund_deposit for c in checkouts if c.status == "COMPLETED")

    sorted_checkouts = sorted(checkouts, key=lambda x: getattr(x, "created_at", ""), reverse=True)

    return {
        "branch": branch,
        "kpi": {
            "inspecting": inspecting_count,
            "agreed": agreed_count,
            "completed": completed_count,
            "total_refund": total_refund
        },
        "rooms": room_options,
        "checkouts": [c.model_dump() for c in sorted_checkouts]
    }


@router.get("/checkout/{branch}/{room_id}")
def get_checkout_info(branch: str, room_id: str):
    """퇴실 검수표, 입실 시 점검 사진 및 보증금 정산 명세서 상세 조회."""
    # 1. 기존 호실 정보 및 임차인 정보
    tenant_name = "홍길동"
    tenant_phone = "010-1234-5678"
    deposit = 5000000
    if Building:
        try:
            b_obj = Building.get(branch)
            if b_obj and hasattr(b_obj, "rooms"):
                raw_rooms = b_obj.rooms() if callable(b_obj.rooms) else b_obj.rooms
                if raw_rooms and room_id in raw_rooms:
                    room = raw_rooms[room_id]
                    tenant_name = getattr(room, "tenant_name", "홍길동") or "홍길동"
                    tenant_phone = getattr(room, "tenant_phone", "010-1234-5678") or "010-1234-5678"
                    deposit = getattr(room, "deposit", 5000000) or 5000000
        except Exception:
            pass

    # 2. 미납 월세 조회
    unpaid_bills = [b for b in MonthlyBill.all() if getattr(b, "branch", "") == branch and getattr(b, "room_id", "") == room_id and not getattr(b, "is_paid", False)]
    unpaid_rent = sum(b.total_amount for b in unpaid_bills)

    # 3. 입실 당시 체크인 기록 (사진 대조용)
    checkins = [c for c in CheckInRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    checkin_record = checkins[-1].model_dump() if checkins else None

    # 4. 기존 또는 신규 퇴실 검수표
    checkouts = [c for c in CheckOutRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    if checkouts:
        record = checkouts[-1]
    else:
        record = CheckOutRecord(
            branch=branch,
            room_id=room_id,
            room_number=f"{room_id}호",
            tenant_name=tenant_name,
            tenant_phone=tenant_phone,
            original_deposit=deposit,
            unpaid_rent_deduction=unpaid_rent,
            water_fee=15000,
            electric_fee=20000,
            gas_fee=10000,
            cleaning_fee=70000,
            repair_deduction=0,
            damage_notes="통상 생활 손모 외 특이 파손 없음 (상태 양호)",
            refund_bank="카카오뱅크",
            refund_account="3333-01-2345678",
            refund_account_holder=tenant_name
        )
        record.calculate_refund()
        record.save("seed_checkout_record")

    return {
        "branch": branch,
        "room_id": room_id,
        "tenant_name": tenant_name,
        "tenant_phone": tenant_phone,
        "original_deposit": deposit,
        "unpaid_rent_deduction": unpaid_rent,
        "checkin_reference": checkin_record,
        "checkout_record": record.model_dump()
    }


@router.post("/checkout/{branch}/{room_id}/inspect")
def inspect_checkout(branch: str, room_id: str, req: CheckOutInspectReq):
    """임대인 시설 검수 및 공과금/청소비/원상복구 공제액 확정 및 세입자 전송."""
    checkouts = [c for c in CheckOutRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    if checkouts:
        rec = checkouts[-1]
    else:
        rec = CheckOutRecord(branch=branch, room_id=room_id, room_number=f"{room_id}호")

    rec.unpaid_rent_deduction = req.unpaid_rent_deduction
    rec.water_fee = req.water_fee
    rec.electric_fee = req.electric_fee
    rec.gas_fee = req.gas_fee
    rec.cleaning_fee = req.cleaning_fee
    rec.repair_deduction = req.repair_deduction
    rec.damage_notes = req.damage_notes
    rec.other_deduction = req.other_deduction
    if req.checks:
        rec.inspection_checks = req.checks
    if req.photos:
        rec.inspection_photos = req.photos
    if req.refund_bank:
        rec.refund_bank = req.refund_bank
    if req.refund_account:
        rec.refund_account = req.refund_account
    if req.refund_account_holder:
        rec.refund_account_holder = req.refund_account_holder

    rec.calculate_refund()
    rec.status = "CALCULATED"
    rec.save("checkout_inspected")

    # 1:1 직통 채팅방에 명세서 발행 안내 자동 전송
    try:
        chat_msg = ChatMessage(
            branch=branch,
            room_id=room_id,
            sender_role="landlord",
            sender_name="김임대 (임대인)",
            message=(
                f"📄 [{room_id}호 퇴실 보증금 정산 공제 명세서가 발행되었습니다]\n"
                f"• 기존 보증금: {rec.original_deposit:,}원\n"
                f"• 공제 총액: {(rec.utility_deduction + rec.damage_deduction + rec.unpaid_rent_deduction + rec.other_deduction):,}원\n"
                f"  - 당일 공과금 실비: {rec.utility_deduction:,}원 (수도 {rec.water_fee:,} / 전기 {rec.electric_fee:,} / 가스 {rec.gas_fee:,})\n"
                f"  - 퇴실 청소 및 원상복구: {rec.damage_deduction:,}원 (청소 {rec.cleaning_fee:,} / 수리 {rec.repair_deduction:,})\n"
                f"• 최종 반환 보증금: {rec.final_refund_deposit:,}원\n"
                f"세입자 라운지에서 세부 명세를 확인하시고 동의 서명해 주시기 바랍니다."
            ),
            msg_type="checkout_settlement",
            quick_action_url=f"/rent3/public.html?branch={branch}&room={room_id}#tenantCheckoutCard",
            is_read=False
        )
        chat_msg.save(f"chat_checkout_inspect_{room_id}")
    except Exception as e:
        print(f"[CheckOut] 채팅 알림 오류: {e}")

    return {
        "success": True,
        "message": f"[{room_id}호] 퇴실 검수 및 공제액이 확정되었습니다. 최종 반환 보증금: {rec.final_refund_deposit:,}원",
        "record": rec.model_dump()
    }


@router.post("/checkout/{branch}/{room_id}/tenant-agree")
def tenant_agree_checkout(branch: str, room_id: str, req: CheckOutAgreeReq):
    """세입자 정산 명세서 확인 및 동의 전자서명 날인."""
    checkouts = [c for c in CheckOutRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    if not checkouts:
        raise HTTPException(status_code=404, detail="퇴실 정산서를 찾을 수 없습니다.")

    rec = checkouts[-1]
    rec.agree_by_tenant(
        signature=req.signature,
        bank=req.refund_bank,
        account=req.refund_account,
        holder=req.refund_account_holder
    )

    # 임대인에게 동의 완료 알림 전송
    try:
        chat_msg = ChatMessage(
            branch=branch,
            room_id=room_id,
            sender_role="tenant",
            sender_name=f"{rec.tenant_name} (임차인)",
            message=(
                f"✍️ [{room_id}호 세입자 퇴실 정산 동의 완료]\n"
                f"퇴실 공제 명세서(반환액: {rec.final_refund_deposit:,}원)에 동의 서명하였습니다.\n"
                f"반환 계좌: {rec.refund_bank} {rec.refund_account} (예금주: {rec.refund_account_holder or rec.tenant_name})\n"
                f"보증금 송금을 진행해 주시면 감사하겠습니다."
            ),
            msg_type="checkout_agreement",
            is_read=False
        )
        chat_msg.save(f"chat_checkout_agree_{room_id}")
    except Exception as e:
        print(f"[CheckOut] 채팅 동의 알림 오류: {e}")

    return {
        "success": True,
        "message": f"[{room_id}호] 정산서에 성공적으로 동의 서명하였습니다.",
        "record": rec.model_dump()
    }


@router.post("/checkout/{branch}/{room_id}/complete")
def complete_checkout(branch: str, room_id: str, req: Optional[CheckOutCompleteReq] = None):
    """보증금 반환 송금 완료 승인 -> 호실 즉시 공실 전환 및 신규 공실 매물 자동 재등록."""
    checkouts = [c for c in CheckOutRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    if not checkouts:
        raise HTTPException(status_code=404, detail="퇴실 정산 기록을 찾을 수 없습니다.")

    rec = checkouts[-1]
    tx_ref = req.tx_ref if req else ""
    rec.complete_settlement(tx_ref=tx_ref)

    return {
        "success": True,
        "message": f"[{room_id}호] 보증금({rec.final_refund_deposit:,}원) 반환이 완료되었습니다. 호실이 '공실'로 전환되고 직거래 포털 매물로 자동 재등록되었습니다.",
        "record": rec.model_dump()
    }


@router.get("/checkout/{branch}/{room_id}/receipt")
def get_checkout_receipt(branch: str, room_id: str):
    """인쇄/PDF용 공식 퇴실 보증금 반환 영수증 및 정산 명세서 데이터."""
    checkouts = [c for c in CheckOutRecord.all() if getattr(c, "branch", "") == branch and getattr(c, "room_id", "") == room_id]
    if not checkouts:
        raise HTTPException(status_code=404, detail="퇴실 정산 명세서를 찾을 수 없습니다.")

    rec = checkouts[-1]
    return {
        "success": True,
        "receipt_title": "공식 임대보증금 반환 및 퇴실 정산 명세서",
        "settlement_no": rec.settlement_no,
        "branch": rec.branch,
        "room_number": getattr(rec, "room_number", f"{room_id}호"),
        "tenant_name": rec.tenant_name,
        "tenant_phone": rec.tenant_phone,
        "move_out_date": rec.move_out_date,
        "original_deposit": rec.original_deposit,
        "breakdown": {
            "unpaid_rent": rec.unpaid_rent_deduction,
            "utility": {
                "water": rec.water_fee,
                "electric": rec.electric_fee,
                "gas": rec.gas_fee,
                "total": rec.utility_deduction
            },
            "restoration": {
                "cleaning": rec.cleaning_fee,
                "repair": rec.repair_deduction,
                "notes": rec.damage_notes,
                "total": rec.damage_deduction
            },
            "other": rec.other_deduction,
            "total_deductions": rec.unpaid_rent_deduction + rec.utility_deduction + rec.damage_deduction + rec.other_deduction
        },
        "final_refund_deposit": rec.final_refund_deposit,
        "refund_account": {
            "bank": rec.refund_bank,
            "account": rec.refund_account,
            "holder": rec.refund_account_holder or rec.tenant_name
        },
        "status": rec.status,
        "tenant_agreed": rec.tenant_agreed,
        "tenant_signature": rec.tenant_signature,
        "refund_completed": rec.refund_completed,
        "refund_completed_at": rec.refund_completed_at,
        "refund_tx_ref": rec.refund_tx_ref,
        "landlord_name": "김임대 (시크빌 임대총괄)",
        "landlord_phone": "010-8888-9999",
        "official_seal": "시크빌 정산 확인필"
    }


# ─────────────────────────────────────────────────────────────
# 11. RTMS Housing Lease Report & Fixed Date APIs (전월세신고 & 확정일자)
# ─────────────────────────────────────────────────────────────
@router.get("/rtms/{target_id}/report")
def get_rtms_report(target_id: str, branch: str = Query("main")):
    """국토부 법정 주택임대차 계약신고서 및 확정일자 데이터 조회 (없으면 자동 완성 생성)."""
    reports = [r for r in RtmsContractReport.all() if getattr(r, "session_id", "") == target_id or getattr(r, "room_id", "") == target_id]
    
    if reports:
        report = reports[-1]
    else:
        # 데이터 자동 조회 (CoSigningSession, RentalApplication 또는 Room)
        tenant_name = "홍길동"
        tenant_phone = "010-1234-5678"
        deposit = 5000000
        monthly_rent = 500000
        room_id = target_id if len(target_id) <= 4 else "201"

        if CoSigningSession:
            s = CoSigningSession.get(target_id)
            if s:
                room_id = s.room_id
                tenant_name = s.tenant_name or tenant_name
                tenant_phone = s.tenant_phone or tenant_phone
                deposit = s.deposit or deposit
                monthly_rent = s.monthly_rent or monthly_rent

        if RentalApplication:
            for a in RentalApplication.all():
                if getattr(a, "room_id", "") == room_id:
                    tenant_name = getattr(a, "tenant_name", tenant_name)
                    tenant_phone = getattr(a, "tenant_phone", tenant_phone)
                    deposit = getattr(a, "deposit", deposit)
                    monthly_rent = getattr(a, "monthly_rent", monthly_rent)
                    break

        report = RtmsContractReport(
            session_id=target_id,
            branch=branch,
            room_id=room_id,
            tenant_name=tenant_name,
            tenant_phone=tenant_phone,
            deposit=deposit,
            monthly_rent=monthly_rent,
            property_address=f"서울특별시 강남구 역삼동 123 시크빌 {room_id}호"
        )
        report.save("seed_rtms_report")

    report_data = report.model_dump()
    report_data["is_subject_to_report"] = report.is_subject_to_report
    report_data["days_left_to_deadline"] = report.days_left_to_deadline

    return {
        "success": True,
        "report": report_data
    }


@router.post("/rtms/{target_id}/submit")
def submit_rtms_report(target_id: str, branch: str = Query("main")):
    """국토교통부 거래관리시스템(RTMS) 전자 신고 제출 -> 대법원 확정일자 번호 즉시 부여 & 1:1 대화방 자동 공지."""
    reports = [r for r in RtmsContractReport.all() if getattr(r, "session_id", "") == target_id or getattr(r, "room_id", "") == target_id]
    if reports:
        report = reports[-1]
    else:
        report = RtmsContractReport(session_id=target_id, branch=branch, room_id=target_id if len(target_id) <= 4 else "201")

    report.submit_report()

    # 1:1 대화방에 확정일자 부여 완료 공지 메시지 등록
    try:
        msg = ChatMessage(
            branch=report.branch,
            room_id=report.room_id,
            sender_role="landlord",
            sender_name="김임대 (임대인)",
            message=f"🏛️ [국토교통부] 주택임대차 계약신고가 정상 접수되어 대법원 확정일자가 부여되었습니다.\n• 확정일자 번호: {report.fixed_date_number}\n• 부여일시: {report.fixed_date_granted_at}\n• 우선변제권 효력: {report.legal_effect_date}\n세입자 라운지에서 공식 신고필증을 언제든 인쇄·열람하실 수 있습니다.",
            msg_type="system_notice",
            quick_action_url=f"public.html?view=tenant&room={report.room_id}"
        )
        msg.save("rtms_chat_notice")
    except Exception as e:
        print(f"[RTMS] 채팅 알림 등록 예외: {e}")

    report_data = report.model_dump()
    report_data["is_subject_to_report"] = report.is_subject_to_report
    report_data["days_left_to_deadline"] = report.days_left_to_deadline

    return {
        "success": True,
        "message": f"주택임대차 계약 신고가 국토교통부(RTMS)에 정상 수리되었습니다. 대법원 확정일자 번호[{report.fixed_date_number}]가 부여되어 보증금 우선변제권이 확보되었습니다.",
        "report": report_data
    }


@router.get("/admin/rtms/pending")
def get_admin_pending_rtms(branch: str = Query("main")):
    """임대인 관제용 30일 이내 과태료 예방 신고 현황 및 D-Day 목록."""
    reports = [r for r in RtmsContractReport.all() if getattr(r, "branch", "main") == branch]
    # 미신고 내역 필터
    unsubmitted = [r for r in reports if getattr(r, "status", "READY") != "SUBMITTED"]
    return {
        "branch": branch,
        "total_contracts": len(reports),
        "pending_count": len(unsubmitted),
        "reports": [
            {
                **r.model_dump(),
                "is_subject_to_report": r.is_subject_to_report,
                "days_left_to_deadline": r.days_left_to_deadline
            } for r in reports
        ]
    }


# ─────────────────────────────────────────────────────────────
# 9. 계약 만기 D-Day 및 비대면 재계약/묵시적 갱신 API
# ─────────────────────────────────────────────────────────────
class RenewalProposeReq(BaseModel):
    branch: str = "main"
    room_id: str
    renewal_type: str = "EXPLICIT"  # EXPLICIT, TACIT, TERMINATE
    new_deposit: int
    new_rent: int
    new_maintenance: int = 50000
    period_months: int = 12
    special_agreement: str = "주택임대차보호법 제7조 증액 상한선(5%)을 준수하며 기존 특약을 승계합니다."

class RenewalSignReq(BaseModel):
    role: str = "tenant"            # "landlord" or "tenant"
    signature: str = "DIGITAL_SIGNATURE"


@router.get("/renewals")
def get_renewals(branch: str = Query("main")):
    """지점별 입주 계약의 만기 D-Day 현황 및 재계약 관리 목록 조회."""
    # 1. 기존 저장된 LeaseRenewal 레코드 로드
    renewals_map = {r.room_id: r for r in LeaseRenewal.all() if getattr(r, "branch", "main") == branch}

    # 2. rent2 Building 호실 정보 연계하여 누락 호실 자동 생성/계산
    items = []
    if Building:
        b = Building.get(branch)
        if b and hasattr(b, "rooms"):
            today = datetime.now().date()
            raw_rooms = b.rooms() if callable(b.rooms) else (b.rooms or {})
            for room_id, r in raw_rooms.items():
                t_name = r.get("tenant_name", "") if isinstance(r, dict) else getattr(r, "tenant_name", "")
                is_occ = r.get("is_occupied", True) if isinstance(r, dict) else getattr(r, "is_occupied", True)
                if not is_occ and not t_name:
                    continue # 공실은 제외

                lr = renewals_map.get(str(room_id))
                if not lr:
                    move_in = r.get("move_in", "") if isinstance(r, dict) else getattr(r, "move_in", "")
                    if move_in:
                        try:
                            mi_dt = datetime.strptime(move_in, "%Y-%m-%d").date()
                            orig_end = mi_dt.replace(year=mi_dt.year + 1).strftime("%Y-%m-%d")
                        except Exception:
                            orig_end = (today + timedelta(days=75)).strftime("%Y-%m-%d")
                    else:
                        orig_end = (today + timedelta(days=90)).strftime("%Y-%m-%d")

                    cur_rent = (r.get("rent") if isinstance(r, dict) else getattr(r, "rent", None)) or 500000
                    cur_deposit = (r.get("deposit") if isinstance(r, dict) else getattr(r, "deposit", None)) or 5000000
                    t_phone = (r.get("tenant_phone") if isinstance(r, dict) else getattr(r, "tenant_phone", None)) or "010-0000-0000"

                    lr = LeaseRenewal(
                        branch=branch,
                        room_id=str(room_id),
                        room_number=f"{room_id}호",
                        tenant_name=t_name or "미상입주자",
                        tenant_phone=t_phone,
                        original_start_date=move_in or "2025-11-01",
                        original_end_date=orig_end,
                        original_deposit=cur_deposit,
                        original_rent=cur_rent,
                        new_deposit=cur_deposit,
                        new_rent=cur_rent,
                        new_maintenance=50000,
                        new_start_date=orig_end,
                        new_end_date=(today + timedelta(days=455)).strftime("%Y-%m-%d")
                    )
                    lr.save("seed_lease_renewal")
                    renewals_map[str(room_id)] = lr

    # 통계 집계
    all_renewals = list(renewals_map.values())
    notice_active_count = sum(1 for r in all_renewals if r.notice_status == "NOTICE_ACTIVE")
    imminent_count = sum(1 for r in all_renewals if r.notice_status == "IMMINENT")
    signed_count = sum(1 for r in all_renewals if r.status in ("SIGNED", "TACIT"))

    return {
        "success": True,
        "branch": branch,
        "summary": {
            "total_contracts": len(all_renewals),
            "notice_active_count": notice_active_count, # 60일~180일 전 (갱신 통지 적정 시기)
            "imminent_count": imminent_count,           # 60일 이내 (만기 임박)
            "signed_count": signed_count                # 체결 완료
        },
        "items": [
            {
                "renewal_id": r.id,
                "room_id": r.room_id,
                "room_number": r.room_number,
                "tenant_name": r.tenant_name,
                "tenant_phone": r.tenant_phone,
                "original_start_date": r.original_start_date,
                "original_end_date": r.original_end_date,
                "days_remaining": r.days_remaining,
                "notice_status": r.notice_status,
                "renewal_type": r.renewal_type,
                "original_deposit": r.original_deposit,
                "original_rent": r.original_rent,
                "new_deposit": r.new_deposit,
                "new_rent": r.new_rent,
                "new_maintenance": r.new_maintenance,
                "rate_increase_pct": r.rate_increase_pct,
                "status": r.status,
                "landlord_signed": r.landlord_signed,
                "tenant_signed": r.tenant_signed,
                "special_agreement": r.special_agreement,
                "updated_at": r.updated_at
            }
            for r in all_renewals
        ]
    }


@router.post("/renewals/propose")
def propose_renewal(req: RenewalProposeReq):
    """임대인의 재계약 조건 제안 (임대료 5% 상한선 검증 포함)."""
    # 1. 기존 레코드 확인 또는 신규 생성
    records = [r for r in LeaseRenewal.all() if getattr(r, "branch", "main") == req.branch and getattr(r, "room_id", "") == req.room_id]
    if records:
        renewal = records[-1]
    else:
        renewal = LeaseRenewal(branch=req.branch, room_id=req.room_id, room_number=f"{req.room_id}호")

    # 2. 5% 상한선 검증 (EXPLICIT 인상 시)
    if req.renewal_type == "EXPLICIT" and renewal.original_rent > 0:
        increase_pct = ((req.new_rent - renewal.original_rent) / renewal.original_rent) * 100.0
        if increase_pct > 5.0001:
            raise HTTPException(
                status_code=400,
                detail=f"주택임대차보호법 제7조 위반: 임대료 인상률({increase_pct:.1f}%)은 법정 증액 상한선(5.0%)을 초과할 수 없습니다."
            )
        renewal.rate_increase_pct = round(max(0.0, increase_pct), 2)
    else:
        renewal.rate_increase_pct = 0.0

    renewal.renewal_type = req.renewal_type
    renewal.new_deposit = req.new_deposit
    renewal.new_rent = req.new_rent
    renewal.new_maintenance = req.new_maintenance
    renewal.period_months = req.period_months
    if req.special_agreement:
        renewal.special_agreement = req.special_agreement
    renewal.status = "PROPOSED"
    renewal.landlord_signed = True
    renewal.landlord_signed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    renewal.tenant_signed = False
    renewal.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    renewal.save("renewal_proposed")

    # 3. 1:1 직통 채팅방에 재계약 제안 알림 자동 발송
    msg_text = (
        f"📜 [재계약 조건 제안]\n"
        f"임대인께서 {req.room_id}호 재계약 조건(보증금 {req.new_deposit:,}원, 월세 {req.new_rent:,}원 / 인상률 {renewal.rate_increase_pct}%)을 제안하셨습니다.\n"
        f"세입자 라운지 또는 아래 링크에서 내용을 확인하시고 전자서명을 완료해 주세요."
    )
    chat = ChatMessage(
        branch=req.branch,
        room_id=req.room_id,
        sender_role="landlord",
        sender_name="임대인(관제센터)",
        message=msg_text,
        quick_action_label="📝 재계약 조건 확인 & 서명",
        quick_action_url=f"/rent3/public.html?room={req.room_id}#renewal"
    )
    chat.save("bot_sent_renewal_proposal")

    return {
        "success": True,
        "message": "재계약 제안이 완료되었으며 세입자 1:1 채팅방으로 안내되었습니다.",
        "renewal": renewal.model_dump()
    }


@router.post("/renewals/{renewal_id}/sign")
def sign_renewal(renewal_id: str, req: RenewalSignReq):
    """재계약서 전자서명 날인 (임차인/임대인)."""
    renewal = LeaseRenewal.get(renewal_id)
    if not renewal:
        raise HTTPException(status_code=404, detail="해당 재계약 건을 찾을 수 없습니다.")

    if req.role == "tenant":
        renewal.sign_tenant()
    else:
        renewal.sign_landlord()

    # 양측 서명 완료 시 축하 메시지 발송
    if renewal.status == "SIGNED":
        chat = ChatMessage(
            branch=renewal.branch,
            room_id=renewal.room_id,
            sender_role="bot",
            sender_name="스마트임차 Rent3",
            message=f"🎉 [{renewal.room_id}호 재계약 체결 완료]\n양측 전자서명이 모두 완료되어 임대차계약이 {renewal.period_months}개월 연장되었습니다.\n(보증금 {renewal.new_deposit:,}원 / 월세 {renewal.new_rent:,}원)"
        )
        chat.save("bot_sent_renewal_complete")

    return {
        "success": True,
        "message": "전자서명이 성공적으로 날인되었습니다.",
        "status": renewal.status,
        "renewal": renewal.model_dump()
    }


@router.post("/renewals/{renewal_id}/tacit-confirm")
def confirm_tacit_renewal(renewal_id: str):
    """주택임대차보호법 제6조 묵시적 갱신 확정 처리."""
    renewal = LeaseRenewal.get(renewal_id)
    if not renewal:
        raise HTTPException(status_code=404, detail="해당 재계약 건을 찾을 수 없습니다.")

    renewal.confirm_tacit()

    chat = ChatMessage(
        branch=renewal.branch,
        room_id=renewal.room_id,
        sender_role="bot",
        sender_name="스마트임차 Rent3",
        message=f"🔄 [{renewal.room_id}호 묵시적 갱신 확인 완료]\n주택임대차보호법 제6조에 따라 이전 계약과 동일한 조건으로 2년간 존속(연장) 처리되었습니다."
    )
    chat.save("bot_sent_tacit_complete")

    return {
        "success": True,
        "message": "묵시적 갱신이 법적 효력에 따라 확정 처리되었습니다.",
        "renewal": renewal.model_dump()
    }


# ─────────────────────────────────────────────────────────────
# 10. 정식 하자보수(A/S) 대장 및 협력 수리업체 배정·정산 API
# ─────────────────────────────────────────────────────────────
class TicketCreateReq(BaseModel):
    branch: str = "main"
    room_id: str
    tenant_name: str
    tenant_phone: str = ""
    category: str = "facility"    # plumbing, electrical, appliance, door, facility
    title: str
    description: str
    photos: List[str] = []

class TicketAssignReq(BaseModel):
    contractor_name: str
    contractor_phone: str = ""
    scheduled_visit_at: str
    admin_notes: str = ""

class TicketCompleteReq(BaseModel):
    repair_notes: str
    completion_photos: List[str] = []
    repair_cost: int = 0
    cost_burden: str = "LANDLORD" # LANDLORD, TENANT, SHARED


@router.get("/maintenance/tickets")
def get_maintenance_tickets(branch: str = Query("main")):
    """지점별 전체 하자보수(A/S) 티켓 대장 및 진행현황 조회."""
    tickets = [t for t in MaintenanceTicket.all() if getattr(t, "branch", "main") == branch]

    # 초기 시드 데이터 생성 (티켓이 전혀 없는 경우)
    if not tickets:
        seed1 = MaintenanceTicket(
            branch=branch,
            room_id="203",
            room_number="203호",
            tenant_name="김임차",
            tenant_phone="010-9999-8888",
            category="plumbing",
            title="싱크대 하부 수전 미세 누수",
            description="설거지 후 하부 수납장에 물방울이 맺힙니다.",
            status="ASSIGNED",
            contractor_name="강남 종합설비 (김반장)",
            contractor_phone="010-3333-7777",
            scheduled_visit_at="2026-10-28 14:00",
            admin_notes="패킹 및 밸브 교체 예정"
        )
        seed1.save("seed_ticket_1")

        seed2 = MaintenanceTicket(
            branch=branch,
            room_id="201",
            room_number="201호",
            tenant_name="홍길동",
            tenant_phone="010-1234-5678",
            category="appliance",
            title="시스템 에어컨 냉매 점검 요청",
            description="송풍은 잘 되는데 냉방 효율이 다소 약합니다.",
            status="RECEIVED"
        )
        seed2.save("seed_ticket_2")

        seed3 = MaintenanceTicket(
            branch=branch,
            room_id="302",
            room_number="302호",
            tenant_name="박직객",
            tenant_phone="010-5555-6666",
            category="door",
            title="현관 스마트 도어락 건전지 방전 및 키패드 교체",
            description="도어락 경고음이 울리고 터치가 간헐적으로 안 됨",
            status="COMPLETED",
            contractor_name="스마트 도어락 A/S센터",
            contractor_phone="010-8888-9999",
            scheduled_visit_at="2026-10-26 11:00",
            repair_notes="메인보드 배터리 단자 세척 및 신품 교체 완료",
            repair_cost=55000,
            cost_burden="LANDLORD",
            landlord_share=55000,
            tenant_share=0,
            is_settled=True,
            completed_at="2026-10-26 11:45:00"
        )
        seed3.save("seed_ticket_3")
        tickets = [seed1, seed2, seed3]

    # 통계 산출
    received_count = sum(1 for t in tickets if t.status == "RECEIVED")
    assigned_count = sum(1 for t in tickets if t.status == "ASSIGNED")
    in_repair_count = sum(1 for t in tickets if t.status == "IN_REPAIR")
    completed_count = sum(1 for t in tickets if t.status in ("COMPLETED", "CLOSED"))
    total_repair_cost = sum(getattr(t, "repair_cost", 0) for t in tickets)

    return {
        "success": True,
        "branch": branch,
        "summary": {
            "total_tickets": len(tickets),
            "received_count": received_count,
            "assigned_count": assigned_count,
            "in_repair_count": in_repair_count,
            "completed_count": completed_count,
            "total_repair_cost": total_repair_cost
        },
        "items": [t.model_dump() for t in sorted(tickets, key=lambda x: getattr(x, "created_at", ""), reverse=True)]
    }


@router.post("/maintenance/tickets")
def create_maintenance_ticket(req: TicketCreateReq):
    """신규 하자보수(A/S) 티켓 접수."""
    ticket = MaintenanceTicket(
        branch=req.branch,
        room_id=req.room_id,
        room_number=f"{req.room_id}호",
        tenant_name=req.tenant_name,
        tenant_phone=req.tenant_phone,
        category=req.category,
        title=req.title,
        description=req.description,
        initial_photos=req.photos,
        status="RECEIVED"
    )
    ticket.save("maintenance_ticket_created")

    # 1:1 직통 채팅방에 알림 메시지 발송
    chat = ChatMessage(
        branch=req.branch,
        room_id=req.room_id,
        sender_role="bot",
        sender_name="스마트임차 Rent3",
        message=f"🛠️ [하자보수 접수 완료]\n접수번호: {ticket.ticket_no}\n제목: {ticket.title}\n접수 내용이 관제 센터에 정식 등록되었으며, 협력 수리업체 배정 후 방문일정을 안내해 드립니다."
    )
    chat.save("bot_sent_as_received")

    return {
        "success": True,
        "message": f"하자보수 티켓[{ticket.ticket_no}]이 정상 접수되었습니다.",
        "ticket": ticket.model_dump()
    }


@router.post("/maintenance/tickets/{ticket_id}/assign")
def assign_ticket_contractor(ticket_id: str, req: TicketAssignReq):
    """협력 수리업체 및 방문일정 배정."""
    ticket = MaintenanceTicket.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="해당 하자보수 건을 찾을 수 없습니다.")

    ticket.assign_contractor(
        name=req.contractor_name,
        phone=req.contractor_phone,
        visit_at=req.scheduled_visit_at,
        notes=req.admin_notes
    )

    # 1:1 직통 채팅방에 기사 배정 안내 발송
    chat = ChatMessage(
        branch=ticket.branch,
        room_id=ticket.room_id,
        sender_role="landlord",
        sender_name="임대인(관제센터)",
        message=(
            f"🔧 [A/S 방문 일정 안내]\n"
            f"신청하신 '{ticket.title}' 건에 협력 수리 기사님이 배정되었습니다.\n"
            f"• 담당업체: {req.contractor_name}\n"
            f"• 기사 연락처: {req.contractor_phone or '현장 방문 시 연락'}\n"
            f"• 방문 예정일시: {req.scheduled_visit_at}\n"
            f"방문 시 협조 부탁드립니다."
        )
    )
    chat.save("bot_sent_as_assigned")

    return {
        "success": True,
        "message": f"협력업체[{req.contractor_name}] 배정이 완료되었으며 세입자에게 방문일정이 안내되었습니다.",
        "ticket": ticket.model_dump()
    }


@router.post("/maintenance/tickets/{ticket_id}/complete")
def complete_ticket_repair(ticket_id: str, req: TicketCompleteReq):
    """수리 완료 보고 및 비용 분담 산출."""
    ticket = MaintenanceTicket.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="해당 하자보수 건을 찾을 수 없습니다.")

    ticket.complete_repair(
        notes=req.repair_notes,
        photos=req.completion_photos,
        cost=req.repair_cost,
        burden=req.cost_burden
    )

    burden_text = "임대인 전액 무상 처리 (노후 자연 마모)" if req.cost_burden == "LANDLORD" else (
        f"세입자 부담 ({req.repair_cost:,}원 - 사용자 과실)" if req.cost_burden == "TENANT" else (
            f"50:50 분담 (임대인 {ticket.landlord_share:,}원 / 세입자 {ticket.tenant_share:,}원)"
        )
    )

    # 1:1 직통 채팅방에 완료 알림 발송
    chat = ChatMessage(
        branch=ticket.branch,
        room_id=ticket.room_id,
        sender_role="landlord",
        sender_name="임대인(관제센터)",
        message=(
            f"✅ [A/S 수리 완료 및 비용 정산 안내]\n"
            f"'{ticket.title}' 건의 수리가 완료되었습니다.\n"
            f"• 조치 내용: {req.repair_notes}\n"
            f"• 총 수리비: {req.repair_cost:,}원\n"
            f"• 비용 처리: {burden_text}\n"
            f"시설 이용에 불편이 없으시길 바랍니다."
        ),
        photos=req.completion_photos[:2] if req.completion_photos else []
    )
    chat.save("bot_sent_as_completed")

    return {
        "success": True,
        "message": "수리 완료 보고 및 비용 정산 처리가 완료되었습니다.",
        "ticket": ticket.model_dump()
    }


@router.post("/maintenance/tickets/{ticket_id}/close")
def close_maintenance_ticket(ticket_id: str):
    """하자보수 티켓 정산 완료 및 최종 종결."""
    ticket = MaintenanceTicket.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="해당 하자보수 건을 찾을 수 없습니다.")

    ticket.close_ticket()
    return {
        "success": True,
        "message": f"하자보수 티켓[{ticket.ticket_no}]이 최종 정산 종결되었습니다.",
        "ticket": ticket.model_dump()
    }


# ─────────────────────────────────────────────────────────────
# 16. 지점별(시크빌/선호아트빌/신비주택) 통합 세무·수익률 분석 API
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/tax-roi")
def get_tax_roi_dashboard(tax_mode: str = Query("SEPARATE_14", description="SEPARATE_14 (14% 분리과세) | COMPREHENSIVE (종합과세)")):
    """3대 지점 통합 세무(부가세 면세/임대소득세/종부세) 및 수익률(Cap Rate/ROI) 대시보드 지표."""
    try:
        report = calculate_portfolio_tax_roi(tax_mode=tax_mode)
        return {
            "success": True,
            "data": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세무·수익률 분석 산출 오류: {str(e)}")


@router.get("/analytics/tax-roi/{branch_id}")
def get_branch_tax_roi(branch_id: str, tax_mode: str = Query("SEPARATE_14")):
    """특정 지점 단일 세무·수익률 분석 보고서."""
    if branch_id not in ("main", "sunho", "sinbi"):
        raise HTTPException(status_code=400, detail=f"지원되지 않는 지점입니다: {branch_id}")
    try:
        report = calculate_branch_tax_roi(branch_id=branch_id, tax_mode=tax_mode)
        return {
            "success": True,
            "data": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"지점 세무·수익률 분석 오류: {str(e)}")


# ─────────────────────────────────────────────────────────────
# 17. 세입자 연말정산 월세 세액공제용 납입 증명서 & 현금영수증 API
# ─────────────────────────────────────────────────────────────

@router.get("/certificate/rent-tax/{branch}/{room_id}")
def get_rent_tax_certificate(branch: str, room_id: str, year: int = Query(2026)):
    """연말정산용 월세 납입 증명서 및 국세청 현금영수증 내역 발급."""
    try:
        cert = generate_rent_certificate(branch=branch, room_id=room_id, year=year)
        return {
            "success": True,
            "certificate": cert.model_dump()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"월세 납입 증명서 발급 실패: {str(e)}")


@router.post("/certificate/send-chat/{branch}/{room_id}")
def send_certificate_to_chat(branch: str, room_id: str, year: int = Query(2026)):
    """월세 납입 증명서 발급 완료 통보를 1:1 직통 채팅방에 전송."""
    cert = generate_rent_certificate(branch=branch, room_id=room_id, year=year)

    chat = ChatMessage(
        branch=branch,
        room_id=room_id,
        sender_role="landlord",
        sender_name="임대인(관제센터)",
        message=(
            f"📑 [연말정산 월세 세액공제 공식 납입 증명서 발급 완료]\n"
            f"• 증명서 번호: {cert.cert_no}\n"
            f"• 대상 과세연도: {cert.year}년 (총 {len(cert.items)}개월분)\n"
            f"• 연간 납입 총액: {cert.total_paid_amount:,}원\n"
            f"• 예상 세액환급액: 약 {cert.estimated_tax_refund:,}원 (17% 세액공제 기준)\n"
            f"• 법적 근거: 조세특례제한법 제95조의2\n"
            f"세입자 라운지에서 '월세 납입 증명서'를 원클릭으로 열람 및 PDF/인쇄하실 수 있습니다."
        )
    )
    chat.save("bot_sent_rent_certificate")

    return {
        "success": True,
        "message": f"{room_id}호 임차인 대화방에 연말정산 증명서 발급 안내가 전송되었습니다.",
        "cert_no": cert.cert_no
    }


# ─────────────────────────────────────────────────────────────
# 15. 건물 공용 운영비 실지출 관리 & 월별 실결산 (NOI) APIs
# ─────────────────────────────────────────────────────────────
class ExpenseCreateReq(BaseModel):
    branch: str = "main"
    year: int = 2026
    month: int = 10
    category: str = "cleaning"
    title: str
    amount: int
    vendor_name: str = ""
    vendor_biz_no: str = ""
    payment_date: str = ""
    payment_method: str = "TRANSFER"
    receipt_type: str = "TAX_INVOICE"
    receipt_photo_url: str = ""
    notes: str = ""
    is_tax_deductible: bool = True


def _seed_operating_expenses_if_empty():
    """지점별 현실적인 공용 운영비 초기 시드 데이터 생성."""
    if len(BuildingOperatingExpense.all()) > 0:
        return

    seeds = [
        # main (시크빌 본점 29실)
        ("main", 2026, 10, "cleaning", "10월 계단 및 공용구역 정기 청소비", 150000, "클린토탈", "120-86-12345", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 2회 계단/복도/주차장 물청소"),
        ("main", 2026, 10, "elevator", "10월 승강기 정기 점검 및 유지보수료", 180000, "현대엘리베이터(주)", "107-81-22440", "2026-10-12", "TRANSFER", "TAX_INVOICE", "월 1회 정기점검 완료(양호)"),
        ("main", 2026, 10, "utility_common", "10월 공용 전기요금 (계단등·가압펌프·CCTV)", 95000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "지로 자동납부"),
        ("main", 2026, 10, "utility_common", "10월 공용 수도요금 (청소 및 계단용)", 42000, "서울시상수도사업본부", "104-83-00010", "2026-10-20", "GIRO", "GIRO_RECEIPT", "서울시 전자납부"),
        ("main", 2026, 10, "safety_fire", "10월 소방 및 전기안전관리대행료", 90000, "제일안전기술단", "214-85-33011", "2026-10-15", "TRANSFER", "TAX_INVOICE", "월간 소방점검보고서 첨부"),
        ("main", 2026, 10, "insurance", "10월 건물 화재보험료 및 영업배상책임", 60000, "DB손해보험(주)", "118-81-01234", "2026-10-05", "CARD", "CARD_SLIP", "12개월 분납"),
        ("main", 2026, 10, "internet_tv", "10월 건물 단체 인터넷 및 IPTV 전용선", 220000, "(주)케이티", "102-81-42945", "2026-10-25", "TRANSFER", "TAX_INVOICE", "29개 전 호실 초고속 인터넷 단체선"),
        ("main", 2026, 10, "repair_maint", "3층 복도 센서등 LED 교체 및 배선 보수", 35000, "역삼조명철물", "211-12-89012", "2026-10-22", "CARD", "CARD_SLIP", "자재 실비 교체"),

        # sunho (선호아트빌 15실)
        ("sunho", 2026, 10, "cleaning", "10월 공용구역 정기 청소비", 120000, "관악청소마을", "209-11-45678", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 1회 정기 청소"),
        ("sunho", 2026, 10, "safety_fire", "10월 소방안전관리대행료", 70000, "한결소방엔지니어링", "119-86-55443", "2026-10-15", "TRANSFER", "TAX_INVOICE", "소방시설 점검"),
        ("sunho", 2026, 10, "utility_common", "10월 공용 전기요금", 55000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "공용 전기"),
        ("sunho", 2026, 10, "internet_tv", "10월 건물 단체 인터넷 전용선", 140000, "LG유플러스", "220-81-39938", "2026-10-25", "TRANSFER", "TAX_INVOICE", "15실 단체선"),

        # sinbi (신비주택 20실)
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


@router.get("/expenses/list")
def get_expenses_list(branch: str = Query("main"), year: int = Query(2026), month: int = Query(10)):
    """지점별, 연월별 공용 운영비 지출 대장 목록 조회."""
    _seed_operating_expenses_if_empty()

    all_expenses = BuildingOperatingExpense.all()
    filtered = [
        e for e in all_expenses
        if getattr(e, "branch", "main") == branch
        and getattr(e, "year", 2026) == year
        and getattr(e, "month", 10) == month
    ]
    # 지출일자 내림차순 정렬
    filtered.sort(key=lambda x: getattr(x, "payment_date", "") or "", reverse=True)

    total_amount = sum(getattr(e, "amount", 0) for e in filtered)
    deductible_amount = sum(getattr(e, "amount", 0) for e in filtered if getattr(e, "is_tax_deductible", True))

    items = []
    for e in filtered:
        cat = getattr(e, "category", "other")
        items.append({
            **e.model_dump(),
            "category_label": BuildingOperatingExpense.get_category_label(cat)
        })

    return {
        "success": True,
        "branch": branch,
        "year": year,
        "month": month,
        "total_count": len(items),
        "total_amount": total_amount,
        "deductible_amount": deductible_amount,
        "expenses": items
    }


@router.post("/expenses/create")
def create_operating_expense(req: ExpenseCreateReq):
    """신규 공용 운영비 지출 등록."""
    pdate = req.payment_date or datetime.now().strftime("%Y-%m-%d")
    exp = BuildingOperatingExpense(
        branch=req.branch,
        year=req.year,
        month=req.month,
        category=req.category,
        title=req.title,
        amount=req.amount,
        vendor_name=req.vendor_name,
        vendor_biz_no=req.vendor_biz_no,
        payment_date=pdate,
        payment_method=req.payment_method,
        receipt_type=req.receipt_type,
        receipt_photo_url=req.receipt_photo_url,
        notes=req.notes,
        is_tax_deductible=req.is_tax_deductible
    )
    exp.save("expense_created")
    return {
        "success": True,
        "message": f"[{exp.title}] {exp.amount:,}원 지출 등록이 완료되었습니다.",
        "expense_id": exp.id
    }


@router.delete("/expenses/{expense_id}")
def delete_operating_expense(expense_id: str):
    """공용 운영비 지출 삭제."""
    exp = BuildingOperatingExpense.get(expense_id)
    if not exp:
        raise HTTPException(status_code=404, detail="지출 항목을 찾을 수 없습니다.")
    exp.delete("expense_deleted")
    return {
        "success": True,
        "message": "지출 항목이 성공적으로 삭제되었습니다."
    }


@router.get("/expenses/monthly-summary")
def get_monthly_operating_summary(branch: str = Query("main"), year: int = Query(2026), month: int = Query(10)):
    """월별 임대차 총 수입 대 실운영비 결산서 (실제 순운영이익 NOI 및 경비율 도출)."""
    _seed_operating_expenses_if_empty()
    summary = calculate_monthly_operating_summary(branch, year, month)
    return {
        "success": True,
        "summary": summary
    }


# ─────────────────────────────────────────────────────────────
# 16. 임대인 5월 종합소득세 홈택스 제출용 연간 임대소득·필요경비 표준 전산 명세서 API
# ─────────────────────────────────────────────────────────────
@router.get("/tax/annual-statement")
def get_annual_tax_statement(branch: str = Query("main"), year: int = Query(2026)):
    """임대인 5월 종합소득세 확정신고용 연간 소득·필요경비 표준 전산 명세서 JSON 조회."""
    _seed_operating_expenses_if_empty()
    statement = generate_annual_tax_statement(branch, year)
    return {
        "success": True,
        "statement": statement
    }


@router.get("/tax/annual-statement/export-csv")
def export_annual_tax_statement_csv(branch: str = Query("main"), year: int = Query(2026)):
    """국세청 홈택스 제출 및 엑셀(MS Excel) 호환 UTF-8 BOM 인코딩 표준 CSV 명세서 다운로드."""
    _seed_operating_expenses_if_empty()
    statement = generate_annual_tax_statement(branch, year)
    csv_content = generate_tax_statement_csv(statement)
    filename = f"tax_statement_{year}_{branch}.csv"
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


# ─────────────────────────────────────────────────────────────
# 17. 8대 표준 엔티티 (Admin, Lessor, Tenant, Building, Room, Contract, Pay, Outlay) CRUD & 검색 API
# ─────────────────────────────────────────────────────────────
@router.get("/entities/summary/dashboard")
def get_entities_summary_dashboard():
    """8대 표준 엔티티 전체 등록 건수 및 최신 요약 데이터 조회."""
    seed_default_entities_if_empty()
    return {
        "success": True,
        "counts": {
            "admins": len(Admin.all()),
            "lessors": len(Lessor.all()),
            "tenants": len(Tenant.all()),
            "buildings": len(BuildingEntity.all()),
            "rooms": len(RoomEntity.all()),
            "contracts": len(ContractEntity.all()),
            "pays": len(Pay.all()),
            "outlays": len(Outlay.all())
        },
        "workflow_steps_count": 16,
        "system_status": "READY"
    }


@router.get("/entities/{entity_type}")
def list_entities(
    entity_type: str, 
    query: str = Query("", description="검색 키워드 (성명, 전화번호, 호실명, 적요 등)"),
    branch: Optional[str] = Query(None, description="소속 지점 필터")
):
    """지정된 엔티티 타입의 전체 목록 조회 및 키워드 검색."""
    seed_default_entities_if_empty()
    try:
        filters = {}
        if branch:
            filters["branch"] = branch
        items = Rent3EntityManager.search(entity_type, query=query, filters=filters)
        return {
            "success": True,
            "entity_type": entity_type.lower(),
            "count": len(items),
            "items": [item.model_dump() for item in items]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/entities/{entity_type}/{custom_id}")
def get_entity_detail(entity_type: str, custom_id: str):
    """단일 엔티티 고유 ID 또는 시스템 ID 기반 상세 조회."""
    seed_default_entities_if_empty()
    item = Rent3EntityManager.find_by_custom_id(entity_type, custom_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"{entity_type} 객체({custom_id})를 찾을 수 없습니다.")
    return {
        "success": True,
        "entity_type": entity_type.lower(),
        "item": item.model_dump()
    }


@router.post("/entities/{entity_type}")
def create_entity(entity_type: str, payload: Dict[str, Any] = Body(...)):
    """신규 엔티티 객체 생성."""
    seed_default_entities_if_empty()
    try:
        item = Rent3EntityManager.create(entity_type, payload)
        return {
            "success": True,
            "message": f"{entity_type} 객체가 성공적으로 생성되었습니다.",
            "item": item.model_dump()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"객체 생성 실패: {str(e)}")


@router.put("/entities/{entity_type}/{custom_id}")
def update_entity(entity_type: str, custom_id: str, payload: Dict[str, Any] = Body(...)):
    """기존 엔티티 객체 필드 수정/편집."""
    seed_default_entities_if_empty()
    try:
        updated = Rent3EntityManager.update(entity_type, custom_id, payload)
        return {
            "success": True,
            "message": f"{entity_type} 객체({custom_id})가 성공적으로 수정되었습니다.",
            "item": updated.model_dump()
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"객체 수정 실패: {str(e)}")


@router.delete("/entities/{entity_type}/{custom_id}")
def delete_entity(entity_type: str, custom_id: str):
    """엔티티 객체 삭제."""
    seed_default_entities_if_empty()
    deleted = Rent3EntityManager.delete(entity_type, custom_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"{entity_type} 객체({custom_id})를 찾을 수 없거나 삭제에 실패했습니다.")
    return {
        "success": True,
        "message": f"{entity_type} 객체({custom_id})가 삭제되었습니다."
    }


# ─────────────────────────────────────────────────────────────
# 18. 16단계 이벤트 라이프사이클 액션 실행 API
# ─────────────────────────────────────────────────────────────
@router.post("/entities/workflow/{action_name}")
def execute_workflow_action(action_name: str, payload: Dict[str, Any] = Body(default={})):
    """사용자 이벤트 라이프사이클 단계별 액션 처리."""
    seed_default_entities_if_empty()
    try:
        if action_name == "app_publish_info":
            res = Rent3WorkflowEngine.step1_app_published_info()
            return {"success": True, "result": res}
            
        elif action_name == "register_lessor":
            lessor = Rent3WorkflowEngine.step2_register_lessor(
                name=payload.get("name", "신규임대인"),
                phone=payload.get("phone", "010-0000-0000"),
                account_number=payload.get("account_number", "은행 000-00-00000"),
                business_number=payload.get("business_number", "000-00-00000"),
                email=payload.get("email", "lessor@example.com"),
                branch=payload.get("branch", "main")
            )
            return {"success": True, "lessor": lessor.model_dump()}

        elif action_name == "publish_room":
            room = Rent3WorkflowEngine.step3_publish_room_listing(
                id_building=payload.get("id_building", ""),
                name=payload.get("name", "신규호실"),
                deposit=payload.get("deposit", 5000000),
                monthly_rent=payload.get("monthly_rent", 500000),
                maintenance_fee=payload.get("maintenance_fee", 50000),
                status_description=payload.get("status_description", "깨끗한 풀옵션 원룸"),
                options=payload.get("options", None)
            )
            return {"success": True, "room": room.model_dump()}

        elif action_name == "register_tenant":
            tenant = Rent3WorkflowEngine.step4_register_tenant(
                name=payload.get("name", "신규임차인"),
                phone=payload.get("phone", "010-1111-2222"),
                account_number=payload.get("account_number", "계좌 111-222-333"),
                car_number=payload.get("car_number", ""),
                email=payload.get("email", "tenant@example.com"),
                business_number=payload.get("business_number", "")
            )
            return {"success": True, "tenant": tenant.model_dump()}

        elif action_name == "verify_registry":
            check_res = Rent3WorkflowEngine.step5_verify_and_crosscheck_registry(
                id_room=payload.get("id_room", ""),
                id_lessor=payload.get("id_lessor", ""),
                id_tenant=payload.get("id_tenant", ""),
                issue_number=payload.get("issue_number", ""),
                mortgage_amount=payload.get("mortgage_amount", 0)
            )
            return {"success": True, "check_info": check_res}

        elif action_name == "sign_contract":
            ct = Rent3WorkflowEngine.step8_sign_contract(
                id_room=payload.get("id_room", ""),
                id_tenant=payload.get("id_tenant", ""),
                move_in_date=payload.get("move_in_date", datetime.now().strftime("%Y-%m-%d")),
                lease_term=payload.get("lease_term", "12개월"),
                deposit=payload.get("deposit", 5000000),
                monthly_rent=payload.get("monthly_rent", 500000),
                maintenance_fee=payload.get("maintenance_fee", 50000),
                rent_type=payload.get("rent_type", "월세"),
                payment_timing=payload.get("payment_timing", "후불")
            )
            return {"success": True, "contract": ct.model_dump()}

        elif action_name == "remit_deposit":
            dep_res = Rent3WorkflowEngine.step9_remit_contract_deposit(
                id_contract=payload.get("id_contract", ""),
                capture_img_url=payload.get("capture_img_url", "")
            )
            return dep_res

        elif action_name == "checkin":
            cin_res = Rent3WorkflowEngine.step10_process_checkin(
                id_room=payload.get("id_room", ""),
                id_tenant=payload.get("id_tenant", ""),
                doorlock_pwd=payload.get("doorlock_pwd", "2026*")
            )
            return cin_res

        elif action_name == "record_pay":
            pay = Rent3WorkflowEngine.step12_record_monthly_pay(
                id_room=payload.get("id_room", ""),
                billing_month=payload.get("billing_month", "2026.10"),
                monthly_rent=payload.get("monthly_rent", 500000),
                maintenance_fee=payload.get("maintenance_fee", 50000),
                vat=payload.get("vat", 0)
            )
            return {"success": True, "pay": pay.model_dump()}

        elif action_name == "record_outlay":
            outlay = Rent3WorkflowEngine.step13_record_outlay(
                id_room=payload.get("id_room", "COMMON"),
                summary=payload.get("summary", "공용 관리 지출"),
                amount=payload.get("amount", 100000),
                category=payload.get("category", "cleaning"),
                vendor_name=payload.get("vendor_name", "협력업체")
            )
            return {"success": True, "outlay": outlay.model_dump()}

        elif action_name == "generate_tax":
            tax_res = Rent3WorkflowEngine.step14_generate_tax_materials(
                year=payload.get("year", 2026)
            )
            return {"success": True, "tax_materials": tax_res}

        else:
            raise HTTPException(status_code=400, detail=f"알 수 없는 워크플로우 액션: {action_name}")

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"워크플로우 실행 오류: {str(e)}")














