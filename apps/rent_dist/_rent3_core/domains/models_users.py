"""
apps/_rent3_core/domains/models_users.py — 사용자 객체군(Admin, Lessor, Tenant) 도메인 정의 (`users.json`).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import DomainError
from ..pool import PoolObj, now_iso
from .models_utils import date_today


class Admin(PoolObj):
    """플랫폼 총괄 관리인 객체."""
    collection: ClassVar[str] = "users"
    label: ClassVar[str] = "관리인"

    id: str = Field(default_factory=lambda: f"admin_{uuid4().hex[:6]}")
    username: str = "admin"
    name: str = "본사 총괄 관리인"
    phone: str = "010-3269-3343"
    account_number: str = "국민 123-456-789012"
    business_number: str = "101-86-12345"
    email: str = "admin@rent3.saas.kr"
    role: str = "superadmin"
    password: str = "0202"
    totp_secret: str = ""                    # 2단계 인증(TOTP) 비밀 키 — 디스크에서 암호화, 화면·API 로 내려가지 않는다(mfa.py)
    totp_enabled: bool = False
    totp_last_step: int = 0                  # 마지막으로 쓴 30초 구간(같은 코드 재사용 방지)
    totp_enabled_at: str = ""
    recovery_hashes: List[str] = Field(default_factory=list)   # 복구 코드(해시, 한 번씩만)

    def summary(self) -> str:
        return f"관리인 {self.name} ({self.username})"


class Lessor(PoolObj):
    """건물주 / 임대인 객체 (중개수수료 0원, 월 SaaS 앱 사용료 과금 대상)."""
    collection: ClassVar[str] = "users"
    label: ClassVar[str] = "임대인"

    id: str = Field(default_factory=lambda: f"lessor_{uuid4().hex[:6]}")
    username: str = ""
    name: str = ""
    phone: str = ""
    account_number: str = ""
    business_number: str = ""
    email: str = ""
    branch: str = "main"
    app_fee: int = 33000                     # 월 SaaS 솔루션 이용료 (원)
    app_fee_status: str = "PAID"             # PAID / PENDING / OVERDUE
    last_fee_paid_at: str = ""
    joined_at: str = Field(default_factory=lambda: date_today().isoformat())
    status: str = "ACTIVE"                   # PENDING_APPROVAL(심사 대기) / ACTIVE(가맹) / SUSPENDED(정지) / REJECTED(반려)
    status_note: str = ""                    # 반려·정지 사유(임대인에게 표시)
    room_limit: int = 30                     # 가맹점당 등록 가능 호실 수 상한 (운영 비용 보호)
    ai_monthly_limit: int = 100              # 가맹점당 월 Gemini 등기부 읽기 호출 상한 (운영 비용 보호)
    decided_at: str = ""
    identity: Dict[str, Any] = Field(default_factory=dict)        # 휴대폰 본인인증 결과(실명·생년월일 앞6자리·업체)
    franchise_docs: Dict[str, Any] = Field(default_factory=dict)  # 제출한 등기부(파일 해시)·AI 읽기·소유자 대조 결과
    applied_at: str = ""                     # 가맹 신청 제출 시각 (심사 대기열 진입)
    notice_seen: Dict[str, str] = Field(default_factory=dict)   # 안내성 알림을 확인한 시각(종류별). notifications.py
    acq: Dict[str, Any] = Field(default_factory=dict)   # 유입 귀속: 가입 때 처음 닿은 링크의 표식(ref·campaign·from_tenant). acquisition.py
    mail_opt: Dict[str, Any] = Field(default_factory=dict)   # 입금 메일 자동 정리(선택·기본 꺼짐): 동의 기록·은행·계좌 끝 4자리·수신 토큰. mailpay.py
    franchise_ai_calls: int = 0              # 가맹 신청 중 Gemini 읽기 호출 수 (남용 방지)
    consents: Dict[str, Any] = Field(default_factory=dict)       # 개인정보 동의·관리인 원격 지원 동의 기록(consent.py)
    pin_hash: str = ""                       # 간편 비밀번호(4자리) 해시 — 스마트폰 바로가기 로그인(quicklogin.py, RENT3_LESSOR_PIN 켠 뒤에만 사용)
    quick_devices: List[Dict[str, Any]] = Field(default_factory=list)  # 바로가기 기기 목록(키는 해시만 저장)
    login_devices: List[str] = Field(default_factory=list)  # 비밀번호 로그인이 허용된 기기 해시(devicebind.py, RENT3_DEVICE_BIND 켠 뒤에만 적용)
    invite_hash: str = ""                    # 관리인이 발급한 1회용 가입/암호설정 링크 토큰의 해시(invite.py)
    invite_expires: str = ""
    invite_issued_by: str = ""
    activated_at: str = ""                   # 임대인 본인이 직접 비밀번호를 정한 시각
    password_changed_at: str = ""            # 마지막으로 비밀번호를 설정·변경한 시각(6개월 권유 기준)
    password_remind_sent_at: str = ""        # 관리인이 마지막으로 새 암호 설정 권유를 보낸 시각
    password: str = "1212"

    SECRET_FIELDS: ClassVar[tuple] = ("pin_hash", "quick_devices", "invite_hash", "login_devices", "mail_opt")

    def safe_dump(self) -> Dict[str, Any]:
        """화면·API 로 내려보낼 때 쓰는 model_dump — 간편 비밀번호 해시와 바로가기 키 해시는 뺀다."""
        d = self.model_dump()
        for k in self.SECRET_FIELDS:
            d.pop(k, None)
        return d

    def summary(self) -> str:
        return f"임대인 {self.name} ({self.branch}, 상태={self.status})"

    STATUS_LABELS: ClassVar[Dict[str, str]] = {
        "PENDING_APPROVAL": "가맹 심사 중", "ACTIVE": "가맹 승인", "SUSPENDED": "이용 정지", "REJECTED": "가맹 반려",
    }

    @classmethod
    def for_branch(cls, branch: str) -> Optional["Lessor"]:
        return cls.first(branch=branch)

    @classmethod
    def franchise_open(cls, branch: str) -> bool:
        """이 지점이 매물 공개·직거래 계약을 할 수 있는 승인 가맹점인가. (임대인 정보가 없는 기존 지점은 허용)"""
        lsr = cls.first(branch=branch)
        return lsr is None or lsr.status == "ACTIVE"

    def decide(self, decision: str, note: str = "", app_source: str = "rent3_admin") -> "Lessor":
        """관리자 심사 결과 반영. approve | reject | suspend. 반려·정지 시 해당 지점의 공개 매물은 모두 내린다."""
        mapping = {"approve": "ACTIVE", "reject": "REJECTED", "suspend": "SUSPENDED"}
        if decision not in mapping:
            raise DomainError("decision 은 approve / reject / suspend 중 하나여야 합니다.")
        if decision != "approve" and not note.strip():
            raise DomainError("반려·정지 사유를 입력해 주세요.", hint="임대인에게 그대로 표시됩니다.")
        self.status = mapping[decision]
        self.status_note = "" if decision == "approve" else note.strip()
        self.decided_at = now_iso()
        if decision != "approve":
            from .models_property import Room
            for r in Room.find(branch=self.branch):
                if r.is_published:
                    r.is_published = False
                    r.save(action="room_unpublished_by_franchise_decision", app_source=app_source)
        return self.save(action=f"lessor_{decision}", app_source=app_source)

    def approve(self, app_source: str = "rent3_admin") -> "Lessor":
        return self.decide("approve", app_source=app_source)

    def settle_app_fee(self, paid_date: Optional[str] = None, app_source: str = "rent3_admin") -> "Lessor":
        self.app_fee_status = "PAID"
        self.last_fee_paid_at = paid_date or date_today().isoformat()
        return self.save(action="app_fee_settled", app_source=app_source)


class Tenant(PoolObj):
    """임차인 / 예비 세입자 객체."""
    collection: ClassVar[str] = "users"
    label: ClassVar[str] = "임차인"

    id: str = Field(default_factory=lambda: f"tenant_{uuid4().hex[:6]}")
    name: str = ""
    phone: str = ""
    email: str = ""
    account_number: str = ""
    business_number: str = ""
    company_name: str = ""                   # 사업자 상호/법인명 (세금계산서 공급받는자 상호)
    ceo_name: str = ""                       # 사업자 대표자명 (세금계산서 공급받는자 성명)
    business_address: str = ""               # 사업자등록증상 사업장 주소 (미입력 시 호실 주소 사용)
    biz_submitted_at: str = ""               # 임차인이 사업자등록증을 직접 제출한 시각(임대인이 확인 후 세금계산서 발급)
    biz_ai_calls: int = 0                    # 사업자등록증 AI 읽기 호출 수(남용 방지)
    notice_seen: Dict[str, str] = Field(default_factory=dict)   # 안내성 알림을 확인한 시각(종류별). notifications.py
    lessor_invite_code: str = ""             # 집주인에게 안심계약을 제안할 때 쓰는 소개 코드(추측 불가). lessorinvite.py
    lessor_invite_day: str = ""              # 오늘 보낸 횟수를 센 날짜
    lessor_invite_today: int = 0
    lessor_invite_total: int = 0
    car_number: str = ""
    car_model: str = ""
    doorlock_pwd: str = "1234*"
    branch: str = "main"
    id_building: str = ""
    current_room_id: str = ""
    room_no: str = ""
    checked_in: bool = False
    checked_in_at: str = ""
    checkin_memo: str = ""
    status: str = "ACTIVE"                   # PROSPECT / ACTIVE / MOVED_OUT
    identity: Dict[str, Any] = Field(default_factory=dict)  # 휴대폰 본인인증 결과(실명·생년월일 앞6자리·업체)
    consents: Dict[str, Any] = Field(default_factory=dict)  # 개인정보 동의·관리인 원격 지원 동의 기록(consent.py)
    pin_hash: str = ""                       # 간편 비밀번호(4자리) 해시 — 스마트폰 바로가기 로그인(quicklogin.py)
    quick_devices: List[Dict[str, Any]] = Field(default_factory=list)  # 바로가기 기기 목록(키는 해시만 저장)
    login_devices: List[str] = Field(default_factory=list)  # 비밀번호 로그인이 허용된 기기 해시(devicebind.py, RENT3_DEVICE_BIND 켠 뒤에만 적용)
    invite_hash: str = ""                    # 입주 안내 카드(1회용 링크) 토큰의 해시(invite.py)
    invite_expires: str = ""
    invite_issued_by: str = ""
    activated_at: str = ""                   # 입주자 본인이 비밀번호를 직접 정한 시각(입주 안내 링크·비밀번호 찾기). 비어 있으면 아직 가입 전
    readonly_until: str = ""                 # 퇴실 정산이 끝난 뒤 '조회 전용'으로 로그인할 수 있는 마지막 날(다음 해 2월 말). 지나면 로그인 불가
    password_changed_at: str = ""            # 마지막으로 비밀번호를 설정·변경한 시각(재계약·1년 권유 기준)
    password_remind_sent_at: str = ""        # 임대인이 마지막으로 새 암호 설정 권유를 보낸 시각
    password: str = "1212"

    SECRET_FIELDS: ClassVar[tuple] = ("pin_hash", "quick_devices", "invite_hash", "login_devices")

    def safe_dump(self) -> Dict[str, Any]:
        """화면·API 로 내려보낼 때 쓰는 model_dump — 간편 비밀번호 해시와 바로가기 키 해시는 뺀다(디스크 저장은 model_dump 그대로)."""
        d = self.model_dump()
        for k in self.SECRET_FIELDS:
            d.pop(k, None)
        return d

    def summary(self) -> str:
        return f"임차인 {self.name} ({self.room_no or '미배정'}호)"

    def register_vehicle(self, car_number: str, car_model: str = "", app_source: str = "rent3_tenant") -> "Tenant":
        self.car_number = car_number.strip()
        if car_model:
            self.car_model = car_model.strip()
        return self.save(action="vehicle_registered", app_source=app_source)

    def confirm_checkin(self, memo: str = "", app_source: str = "rent3_tenant") -> "Tenant":
        self.checked_in = True
        self.checked_in_at = now_iso()
        self.checkin_memo = memo.strip()
        return self.save(action="checked_in", app_source=app_source)
