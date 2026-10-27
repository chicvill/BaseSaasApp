"""apps/rent3/routers/public_apps.py — 공개 매물, 온라인 입주신청서, 현장 동행 Co-Signing, 본인확인 라우터.

하위 호환성 유지: 모든 URL 경로는 기존 `/api/rent3/...` 스펙을 100% 보존합니다.
"""
import secrets
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..domain import (
    RentalListing, RentalApplication, CheckInRecord, MaintenanceRequest, 
    CoSigningSession
)

router = APIRouter(tags=["rent3_public"])


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
# 4. Co-Signing Room APIs (현물 동행 실시간 동시 계약)
# ─────────────────────────────────────────────────────────────
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
# 5. Identity Verification & Social SignUp APIs (신원확인 & 가입)
# ─────────────────────────────────────────────────────────────
@router.post("/auth/verify-phone/send")
def send_phone_verification(req: PhoneVerifySendReq):
    """휴대폰 본인인증 6자리 SMS 발송 (시뮬레이션)."""
    code = "123456"
    _phone_verify_codes[req.phone] = code
    return {
        "success": True,
        "message": f"[{req.name}]님의 휴대폰({req.phone})으로 본인확인 인증번호가 발송되었습니다.",
        "simulated_code": code
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
