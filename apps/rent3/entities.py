"""apps/rent3/entities.py — 8대 표준 데이터 객체 및 이벤트 라이프사이클 처리 엔진.

표준 객체 매트릭스:
1. Admin (관리인)       : 성명, 전화번호, 계좌번호, 사업자 번호, email
2. Lessor (임대인)      : id_lessor, 성명, 전화번호, 계좌번호, 앱 사용료, 가입일자, 사업자 번호, email
3. Tenant (임차인)      : id_tenant, 성명, 전화번호, 계좌번호, 차량번호, 도어락 비번, 사업자 번호, email
4. Building (건물)      : id_building, 건물명, 주소, id_lessor
5. Room (임대상세)      : id_room, name, id_building, id_tenant, 등기부등본 확인, 상태 설명, 옵션 리스트(품명, 수량, 상태 등)
6. Contract (계약)      : id_contract, id_room, id_tenant, 계약일, 계약금, 전입일, 임차기간, 보증금, 임차료, 관리비, 부가세, 전세/월세, 선불/후불, 계약금 캡춰 화면
7. Pay (수납)           : id_pay, id_room, 적용년월(2026.10), 납부일, 임차료, 관리비, 부가세
8. Outlay (지출)        : id_outlay, id_room, 적요(청소비, 차량유지비, 공동전기 등), 일자, 금액
"""
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pydantic import Field
from core.kernel import Obj


# ─────────────────────────────────────────────────────────────
# 1. Admin (관리인)
# ─────────────────────────────────────────────────────────────
class Admin(Obj):
    """Admin 관리인 엔티티: 성명, 전화번호, 계좌번호, 사업자 번호, email."""
    name: str = "총괄 최고관리자"        # 성명
    phone: str = "010-1212-3434"         # 전화번호
    account_number: str = "국민 123-456-789012" # 계좌번호
    business_number: str = "101-86-12345" # 사업자 번호
    email: str = "admin@rent3.saas.com"  # email
    role: str = "superadmin"

    @property
    def id_admin(self) -> str:
        return self.id


# ─────────────────────────────────────────────────────────────
# 2. Lessor (임대인)
# ─────────────────────────────────────────────────────────────
class Lessor(Obj):
    """Lessor 임대인 엔티티: id_lessor, 성명, 전화번호, 계좌번호, 앱 사용료, 가입일자, 사업자 번호, email."""
    id_lessor: str = Field(default_factory=lambda: f"lessor_{uuid.uuid4().hex[:8]}")
    name: str = ""                       # 성명
    phone: str = ""                      # 전화번호
    account_number: str = ""             # 계좌번호
    app_fee: int = 33000                 # 앱 사용료 / 가맹비 (원)
    joined_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")) # 가입일자
    business_number: str = ""            # 사업자 번호
    email: str = ""                      # email
    branch: str = "main"                 # 소속 지점 (main, sunho, sinbi)
    status: str = "ACTIVE"               # ACTIVE, SUSPENDED


# ─────────────────────────────────────────────────────────────
# 3. Tenant (임차인)
# ─────────────────────────────────────────────────────────────
class Tenant(Obj):
    """Tenant 임차인 엔티티: id_tenant, 성명, 전화번호, 계좌번호, 차량번호, 도어락 비번, 사업자 번호, email."""
    id_tenant: str = Field(default_factory=lambda: f"tenant_{uuid.uuid4().hex[:8]}")
    name: str = ""                       # 성명
    phone: str = ""                      # 전화번호
    account_number: str = ""             # 계좌번호
    car_number: str = ""                 # 차량번호
    doorlock_pwd: str = "1234*"          # 도어락 비번
    business_number: str = ""            # 사업자 번호 (세액공제/소득공제 연계)
    email: str = ""                      # email
    current_room_id: str = ""            # 현재 입주 호실 ID
    status: str = "ACTIVE"               # ACTIVE, MOVED_OUT


# ─────────────────────────────────────────────────────────────
# 4. Building (건물)
# ─────────────────────────────────────────────────────────────
class BuildingEntity(Obj):
    """Building 건물 엔티티: id_building, 건물명, 주소, id_lessor."""
    id_building: str = Field(default_factory=lambda: f"bldg_{uuid.uuid4().hex[:8]}")
    building_name: str = ""              # 건물명
    address: str = ""                    # 주소
    id_lessor: str = ""                  # id_lessor (소유 임대인)
    branch: str = "main"                 # 지점 키 (main, sunho, sinbi)
    total_rooms: int = 20                # 총 호실 수
    app_fee_monthly: int = 33000         # 월 앱 사용료 (원)
    app_fee_status: str = "PAID"         # PAID (완납), OVERDUE (연체), PENDING (청구중)
    app_fee_last_paid: str = "2026-10-15"# 최근 사용료 납부일
    default_maintenance_fee: int = 50000 # 기본 관리비 (원)
    manager_phone: str = "010-1212-3434" # 건물 관리 담당자 연락처

    @property
    def lessor(self) -> Optional["Lessor"]:
        """건물 소유 임대인 객체 탐색."""
        if not self.id_lessor:
            return None
        return Rent3EntityManager.find_by_custom_id("lessor", self.id_lessor)


# ─────────────────────────────────────────────────────────────
# 5. Room (임대상세)
# ─────────────────────────────────────────────────────────────
class RoomEntity(Obj):
    """Room 임대상세 엔티티: id_room, name, id_building, id_tenant, 등기부등본 확인, 상태 설명, 옵션 리스트(품명, 수량, 상태 등)."""
    id_room: str = Field(default_factory=lambda: f"room_{uuid.uuid4().hex[:8]}")
    name: str = ""                       # 호실명 (예: 201호)
    id_building: str = ""                # id_building
    id_tenant: str = ""                  # id_tenant (공실 시 빈 문자열)
    registry_checked: bool = False       # 등기부등본 확인 여부
    registry_check_info: Dict[str, Any] = Field(default_factory=lambda: {
        "verified": False,
        "checked_at": "",
        "issue_number": "",
        "mortgage_amount": 0,
        "clean_title": True
    })                                   # 등기부등본 확인 세부내역
    status_description: str = "남향 풀옵션, 신규 도배장판 완료" # 상태 설명
    option_list: List[Dict[str, Any]] = Field(default_factory=lambda: [
        {"item": "벽걸이 에어컨", "quantity": 1, "condition": "최상 (2025년형 인버터)"},
        {"item": "드럼 세탁기", "quantity": 1, "condition": "양호 (9kg)"},
        {"item": "투도어 냉장고", "quantity": 1, "condition": "최상 (180L)"},
        {"item": "디지털 도어락", "quantity": 1, "condition": "신규 설치"},
        {"item": "인덕션 하이라이트", "quantity": 1, "condition": "정상 작동"}
    ])                                   # 옵션 리스트(품명, 수량, 상태 등)
    deposit: int = 5000000               # 보증금
    monthly_rent: int = 500000           # 임차료
    maintenance_fee: int = 50000         # 관리비
    is_published: bool = True            # 매물 홈페이지 게시 여부

    @property
    def building(self) -> Optional[BuildingEntity]:
        """소속 건물 객체 탐색."""
        if not self.id_building:
            return None
        return Rent3EntityManager.find_by_custom_id("building", self.id_building)

    @property
    def tenant(self) -> Optional["Tenant"]:
        """현재 입주 임차인 객체 탐색."""
        if not self.id_tenant:
            return None
        return Rent3EntityManager.find_by_custom_id("tenant", self.id_tenant)


# ─────────────────────────────────────────────────────────────
# 6. Contract (계약)
# ─────────────────────────────────────────────────────────────
class ContractEntity(Obj):
    """Contract 계약 엔티티: id_contract, id_room, id_tenant, 계약일, 계약금, 전입일, 임차기간, 보증금, 임차료, 관리비, 부가세, 전세/월세, 선불/후불, 계약금 캡춰 화면."""
    id_contract: str = Field(default_factory=lambda: f"ct_{uuid.uuid4().hex[:8]}")
    id_room: str = ""                    # id_room
    id_tenant: str = ""                  # id_tenant
    contract_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")) # 계약일
    contract_deposit: int = 300000       # 계약금 (원)
    move_in_date: str = ""               # 전입일 (YYYY-MM-DD)
    lease_term: str = "12개월"           # 임차기간 (예: 12개월, 24개월)
    deposit: int = 5000000               # 보증금
    monthly_rent: int = 500000           # 임차료
    maintenance_fee: int = 50000         # 관리비
    vat: int = 0                         # 부가세 (주택임대용역 부가가치세법 제26조 면세: 0원)
    rent_type: str = "월세"              # 전세/월세
    payment_timing: str = "후불"         # 선불/후불
    deposit_capture_img: str = ""        # 계약금 캡춰 화면 (영수증 이미지 URL 또는 Base64)
    status: str = "CONFIRMED"            # DRAFT, CONFIRMED, ACTIVE, TERMINATED

    @property
    def room(self) -> Optional[RoomEntity]:
        """계약 호실 객체 탐색."""
        if not self.id_room:
            return None
        return Rent3EntityManager.find_by_custom_id("room", self.id_room)

    @property
    def tenant(self) -> Optional["Tenant"]:
        """계약 임차인 객체 탐색."""
        if not self.id_tenant:
            return None
        return Rent3EntityManager.find_by_custom_id("tenant", self.id_tenant)


# ─────────────────────────────────────────────────────────────
# 7. Pay (수납)
# ─────────────────────────────────────────────────────────────
class Pay(Obj):
    """Pay 수납 엔티티: id_pay, id_room, 적용년월(2026.10), 납부일, 임차료, 관리비, 부가세."""
    id_pay: str = Field(default_factory=lambda: f"pay_{uuid.uuid4().hex[:8]}")
    id_room: str = ""                    # id_room
    billing_month: str = "2026.10"       # 적용년월(예: 2026.10)
    paid_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")) # 납부일
    monthly_rent: int = 500000           # 임차료
    maintenance_fee: int = 50000         # 관리비
    vat: int = 0                         # 부가세
    total_amount: int = 550000           # 총 납부액 (임차료 + 관리비 + 부가세)
    receipt_number: str = ""             # 전자 영수증 승인번호
    payment_method: str = "계좌이체"     # 계좌이체, 신용카드, 카카오페이

    @property
    def room(self) -> Optional[RoomEntity]:
        """수납 대상 호실 객체 탐색."""
        if not self.id_room:
            return None
        return Rent3EntityManager.find_by_custom_id("room", self.id_room)


# ─────────────────────────────────────────────────────────────
# 8. Outlay (지출)
# ─────────────────────────────────────────────────────────────
class Outlay(Obj):
    """Outlay 지출 엔티티: id_outlay, id_room, 적요(청소비, 차량유지비, 공동전기 등), 일자, 금액."""
    id_outlay: str = Field(default_factory=lambda: f"outlay_{uuid.uuid4().hex[:8]}")
    id_room: str = ""                    # id_room (특정 호실 수선비 또는 "COMMON" 공용부)
    summary_desc: str = ""               # 적요 (청소비, 차량유지비, 공동전기 등)
    expense_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")) # 일자
    amount: int = 0                      # 금액
    category: str = "cleaning"           # cleaning, elevator, utility_common, safety_fire, repair_maint 등
    vendor_name: str = ""                # 거래처/공급업체

    def __init__(self, **data: Any):
        if "summary" in data and "summary_desc" not in data:
            data["summary_desc"] = data.pop("summary")
        super().__init__(**data)

    def summary(self) -> str:
        """Obj 규격 타임라인 요약 메서드."""
        return f"{self.summary_desc or self.category} ({self.amount:,}원)"

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        d = super().model_dump(*args, **kwargs)
        d["summary"] = self.summary_desc
        return d

    @property
    def room(self) -> Optional[RoomEntity]:
        """지출 발생 호실 (공용부일 경우 None)."""
        if not self.id_room or self.id_room == "COMMON":
            return None
        return Rent3EntityManager.find_by_custom_id("room", self.id_room)


# ─────────────────────────────────────────────────────────────
# 데이터 통합 CRUD 및 검색/편집 서비스 매니저 (O(1) 인덱스 캐시 최적화)
# ─────────────────────────────────────────────────────────────
ENTITY_CLS_MAP = {
    "admin": Admin,
    "lessor": Lessor,
    "tenant": Tenant,
    "building": BuildingEntity,
    "room": RoomEntity,
    "contract": ContractEntity,
    "pay": Pay,
    "outlay": Outlay
}

ID_FIELD_MAP = {
    "admin": "id",
    "lessor": "id_lessor",
    "tenant": "id_tenant",
    "building": "id_building",
    "room": "id_room",
    "contract": "id_contract",
    "pay": "id_pay",
    "outlay": "id_outlay"
}

# O(1) 메모리 인덱스 캐시
_ENTITY_INDEX_CACHE: Dict[str, Dict[str, Obj]] = {k: {} for k in ENTITY_CLS_MAP}


class Rent3EntityManager:
    """8대 표준 객체의 생성, 검색, 편집, 삭제를 처리하는 통합 매니저 (O(1) 인덱스 최적화)."""

    @classmethod
    def get_class(cls, entity_type: str):
        key = entity_type.lower()
        if key not in ENTITY_CLS_MAP:
            raise ValueError(f"지원하지 않는 엔티티 타입입니다: {entity_type}")
        return ENTITY_CLS_MAP[key]

    @classmethod
    def create(cls, entity_type: str, data: Dict[str, Any]) -> Any:
        """신규 객체 생성 및 인덱스 등록."""
        key = entity_type.lower()
        model_cls = cls.get_class(key)
        obj = model_cls(**data)
        obj.save(f"create_{key}")
        
        # O(1) 인덱스 등록
        id_field = ID_FIELD_MAP.get(key, "id")
        custom_id = getattr(obj, id_field, None)
        if custom_id:
            _ENTITY_INDEX_CACHE[key][custom_id] = obj
        if hasattr(obj, "id"):
            _ENTITY_INDEX_CACHE[key][obj.id] = obj
        return obj

    @classmethod
    def find_by_custom_id(cls, entity_type: str, custom_id: str) -> Optional[Any]:
        """고유 ID(예: id_lessor, id_room) 또는 시스템 id 로 단일 객체 초고속 조회 (O(1) 캐시)."""
        key = entity_type.lower()
        model_cls = cls.get_class(key)
        id_field = ID_FIELD_MAP.get(key, "id")
        
        # 0차: O(1) 인덱스 캐시 조회
        cached = _ENTITY_INDEX_CACHE.get(key, {}).get(custom_id)
        if cached:
            return cached

        # 1차: 시스템 id로 조회
        try:
            item = model_cls.get(custom_id)
            if item:
                _ENTITY_INDEX_CACHE[key][custom_id] = item
                c_id = getattr(item, id_field, None)
                if c_id:
                    _ENTITY_INDEX_CACHE[key][c_id] = item
                return item
        except Exception:
            pass

        # 2차: 커스텀 고유키(id_lessor 등)로 검색
        all_items = model_cls.all()
        for item in all_items:
            c_id = getattr(item, id_field, None)
            sys_id = getattr(item, "id", None)
            if c_id:
                _ENTITY_INDEX_CACHE[key][c_id] = item
            if sys_id:
                _ENTITY_INDEX_CACHE[key][sys_id] = item
            if c_id == custom_id or sys_id == custom_id:
                return item
        return None

    @classmethod
    def update(cls, entity_type: str, custom_id: str, updates: Dict[str, Any]) -> Any:
        """기존 객체 필드 편집 및 인덱스 갱신."""
        key = entity_type.lower()
        item = cls.find_by_custom_id(key, custom_id)
        if not item:
            raise KeyError(f"{entity_type} 객체를 찾을 수 없습니다: {custom_id}")
        
        for k, v in updates.items():
            if hasattr(item, k):
                setattr(item, k, v)
        item.save(f"update_{key}_{custom_id}")

        # 캐시 갱신
        id_field = ID_FIELD_MAP.get(key, "id")
        c_id = getattr(item, id_field, None)
        if c_id:
            _ENTITY_INDEX_CACHE[key][c_id] = item
        if hasattr(item, "id"):
            _ENTITY_INDEX_CACHE[key][item.id] = item
        return item

    @classmethod
    def delete(cls, entity_type: str, custom_id: str) -> bool:
        """객체 삭제 및 인덱스 제거."""
        key = entity_type.lower()
        item = cls.find_by_custom_id(key, custom_id)
        if not item:
            return False
        
        # 캐시 제거
        id_field = ID_FIELD_MAP.get(key, "id")
        c_id = getattr(item, id_field, None)
        if c_id and c_id in _ENTITY_INDEX_CACHE[key]:
            del _ENTITY_INDEX_CACHE[key][c_id]
        if hasattr(item, "id") and item.id in _ENTITY_INDEX_CACHE[key]:
            del _ENTITY_INDEX_CACHE[key][item.id]

        item.delete(f"delete_{key}_{custom_id}")
        return True

    @classmethod
    def search(cls, entity_type: str, query: str = "", filters: Optional[Dict[str, Any]] = None) -> List[Any]:
        """객체 검색 (키워드 매칭 및 필드 필터)."""
        model_cls = cls.get_class(entity_type)
        all_items = model_cls.all()
        results = []
        q = (query or "").strip().lower()

        for item in all_items:
            # 1. 필터 검사
            match = True
            if filters:
                for k, v in filters.items():
                    val = getattr(item, k, None)
                    if val is not None and str(val) != str(v):
                        match = False
                        break
            if not match:
                continue

            # 2. 쿼리 키워드 검사 (전체 필드 대상)
            if q:
                item_dict = item.model_dump()
                found = False
                for field_val in item_dict.values():
                    if q in str(field_val).lower():
                        found = True
                        break
                if not found:
                    continue

            results.append(item)
        return results



# ─────────────────────────────────────────────────────────────
# 9. 16단계 이벤트 발생 과정 비즈니스 파이프라인
# ─────────────────────────────────────────────────────────────
class Rent3WorkflowEngine:
    """사용자가 정의한 이벤트 발생 과정 16단계를 엔티티 간 상호작용으로 완결 처리하는 워크플로우 엔진."""

    @classmethod
    def step1_app_published_info(cls) -> Dict[str, Any]:
        """이벤트 1: 앱 게시 — 홈페이지 접속 링크 및 모바일 안내."""
        return {
            "app_name": "Smart Rent3 (차세대 비대면 직거래 & 올인원 건물관리)",
            "google_play_link": "https://play.google.com/store/apps/details?id=com.basesaas.rent3",
            "home_url": "http://localhost:8000/rent3/index.html",
            "admin_url": "http://localhost:8000/rent3/public.html?role=admin",
            "status": "PUBLISHED_ACTIVE"
        }

    @classmethod
    def step2_register_lessor(cls, name: str, phone: str, account_number: str, business_number: str, email: str, branch: str = "main") -> Lessor:
        """이벤트 2: 임대인 회원 가입 — 본인 확인 후 id_lessor 발급 및 등록."""
        lessor = Lessor(
            name=name,
            phone=phone,
            account_number=account_number,
            business_number=business_number,
            email=email,
            branch=branch,
            app_fee=33000
        )
        lessor.save("register_lessor")
        return lessor

    @classmethod
    def step3_publish_room_listing(cls, id_building: str, name: str, deposit: int, monthly_rent: int, maintenance_fee: int, status_description: str, options: Optional[List[Dict[str, Any]]] = None) -> RoomEntity:
        """이벤트 3: 임대 매물 게시 — 매물정보 등록(id_room) 및 홈페이지 게시."""
        room = RoomEntity(
            id_building=id_building,
            name=name,
            deposit=deposit,
            monthly_rent=monthly_rent,
            maintenance_fee=maintenance_fee,
            status_description=status_description,
            option_list=options or [
                {"item": "벽걸이 에어컨", "quantity": 1, "condition": "최상"},
                {"item": "드럼 세탁기", "quantity": 1, "condition": "양호"},
                {"item": "냉장고", "quantity": 1, "condition": "최상"},
                {"item": "디지털 도어락", "quantity": 1, "condition": "정상"}
            ],
            is_published=True
        )
        room.save("publish_room_listing")
        return room

    @classmethod
    def step4_register_tenant(cls, name: str, phone: str, account_number: str, car_number: str, email: str, business_number: str = "") -> Tenant:
        """이벤트 4: 임차인 회원 가입 — 본인 확인 후 id_tenant 발급 및 등록."""
        tenant = Tenant(
            name=name,
            phone=phone,
            account_number=account_number,
            car_number=car_number,
            email=email,
            business_number=business_number
        )
        tenant.save("register_tenant")
        return tenant

    @classmethod
    def step5_verify_and_crosscheck_registry(cls, id_room: str, id_lessor: str, id_tenant: str, issue_number: str, mortgage_amount: int = 0) -> Dict[str, Any]:
        """이벤트 5, 6, 7: 등기부등본 열람 및 임차인 크로스 체크 DB 등록."""
        room = Rent3EntityManager.find_by_custom_id("room", id_room)
        if not room:
            raise KeyError(f"호실을 찾을 수 없습니다: {id_room}")
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean = (mortgage_amount == 0)
        
        check_info = {
            "verified": True,
            "checked_at": now_str,
            "issue_number": issue_number or f"REG-2026-{uuid.uuid4().hex[:6].upper()}",
            "mortgage_amount": mortgage_amount,
            "clean_title": clean,
            "verified_by_lessor": id_lessor,
            "crosschecked_by_tenant": id_tenant
        }
        room.registry_checked = True
        room.registry_check_info = check_info
        room.save("verify_registry_crosscheck")
        return check_info

    @classmethod
    def step8_sign_contract(cls, id_room: str, id_tenant: str, move_in_date: str, lease_term: str = "12개월", deposit: int = 5000000, monthly_rent: int = 500000, maintenance_fee: int = 50000, rent_type: str = "월세", payment_timing: str = "후불") -> ContractEntity:
        """이벤트 8: 계약 체결 — 현물/옵션/상태 확인 및 id_contract 등록."""
        room = Rent3EntityManager.find_by_custom_id("room", id_room)
        if not room:
            raise KeyError(f"호실을 찾을 수 없습니다: {id_room}")
        
        contract = ContractEntity(
            id_room=room.id_room,
            id_tenant=id_tenant,
            contract_date=datetime.now().strftime("%Y-%m-%d"),
            contract_deposit=300000,
            move_in_date=move_in_date,
            lease_term=lease_term,
            deposit=deposit,
            monthly_rent=monthly_rent,
            maintenance_fee=maintenance_fee,
            vat=0,
            rent_type=rent_type,
            payment_timing=payment_timing,
            status="CONFIRMED"
        )
        contract.save("contract_created")

        # 호실에 임차인 배정
        room.id_tenant = id_tenant
        room.save("room_tenant_assigned")
        return contract

    @classmethod
    def step9_remit_contract_deposit(cls, id_contract: str, capture_img_url: str = "") -> Dict[str, Any]:
        """이벤트 9: 계약금 송금 — 계약금 캡춰 화면 등록 및 전자 영수증 발행."""
        ct = Rent3EntityManager.find_by_custom_id("contract", id_contract)
        if not ct:
            raise KeyError(f"계약서를 찾을 수 없습니다: {id_contract}")
        
        ct.deposit_capture_img = capture_img_url or "https://via.placeholder.com/600x400/2563eb/ffffff?text=Deposit+Receipt+CONFIRMED"
        ct.save("deposit_remitted")

        receipt_no = f"REC-DEP-2026-{uuid.uuid4().hex[:6].upper()}"
        return {
            "success": True,
            "id_contract": ct.id_contract,
            "contract_deposit": ct.contract_deposit,
            "receipt_number": receipt_no,
            "issued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "DEPOSIT_CONFIRMED"
        }

    @classmethod
    def step10_process_checkin(cls, id_room: str, id_tenant: str, doorlock_pwd: str = "5829*") -> Dict[str, Any]:
        """이벤트 10, 11: 전입 및 입주 — 출입문 비번 전달, 전기/가스 정산, 입주 완료."""
        tenant = Rent3EntityManager.find_by_custom_id("tenant", id_tenant)
        room = Rent3EntityManager.find_by_custom_id("room", id_room)
        if tenant:
            tenant.doorlock_pwd = doorlock_pwd
            tenant.current_room_id = id_room
            tenant.save("tenant_checkin")

        return {
            "success": True,
            "id_room": id_room,
            "id_tenant": id_tenant,
            "doorlock_pwd": doorlock_pwd,
            "common_gate_pwd": "종소리 2580*",
            "wifi_ssid": f"Rent3_{room.name if room else 'Room'}_5G",
            "guide": "전기/가스 명의변경 안내 및 쓰레기 종량제 분리배출 요일(화/목) 안내 완료",
            "status": "CHECKIN_COMPLETED"
        }

    @classmethod
    def step12_record_monthly_pay(cls, id_room: str, billing_month: str, monthly_rent: int, maintenance_fee: int, vat: int = 0) -> Pay:
        """이벤트 12: 1개월 후 임차료 송금 & 임대인 수납 등록(id_pay) — 임차인 화면 즉시 확인."""
        receipt_no = f"PAY-REC-{billing_month.replace('.', '')}-{uuid.uuid4().hex[:6].upper()}"
        pay = Pay(
            id_room=id_room,
            billing_month=billing_month,
            paid_date=datetime.now().strftime("%Y-%m-%d"),
            monthly_rent=monthly_rent,
            maintenance_fee=maintenance_fee,
            vat=vat,
            total_amount=monthly_rent + maintenance_fee + vat,
            receipt_number=receipt_no,
            payment_method="계좌이체"
        )
        pay.save("record_monthly_pay")
        return pay

    @classmethod
    def step13_record_outlay(cls, id_room: str, summary: str, amount: int, category: str = "cleaning", vendor_name: str = "") -> Outlay:
        """이벤트 14: 경비 지출 등록 — 청소비, 차량유지비, 공동전기수도 등 등록."""
        outlay = Outlay(
            id_room=id_room,
            summary=summary,
            expense_date=datetime.now().strftime("%Y-%m-%d"),
            amount=amount,
            category=category,
            vendor_name=vendor_name
        )
        outlay.save("record_outlay")
        return outlay

    @classmethod
    def step14_generate_tax_materials(cls, year: int = 2026) -> Dict[str, Any]:
        """이벤트 15: 부가세/종부세/소득세 결산 자료 자동 생성."""
        pays = Pay.all()
        outlays = Outlay.all()

        total_income = sum(p.total_amount for p in pays)
        total_rent = sum(p.monthly_rent for p in pays)
        total_maint = sum(p.maintenance_fee for p in pays)
        total_vat = sum(p.vat for p in pays)
        total_expense = sum(o.amount for o in outlays)
        net_income = total_income - total_expense

        return {
            "year": year,
            "total_income": total_income,
            "total_rent": total_rent,
            "total_maintenance_fee": total_maint,
            "total_vat": total_vat,
            "total_expense": total_expense,
            "net_rental_income": net_income,
            "separate_tax_estimate_14": int(net_income * 0.154) if net_income > 0 else 0,
            "comprehensive_holding_tax_estimate": 450000,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }


# ─────────────────────────────────────────────────────────────
# 10. 초기 기본 데이터 시딩 (시크빌, 선호아트빌, 신비주택)
# ─────────────────────────────────────────────────────────────
def seed_default_entities_if_empty():
    """8대 표준 엔티티 기본 데이터가 비어있을 경우 시크빌/선호아트빌/신비주택 기준 시딩."""
    if Lessor.all():
        return # 이미 데이터 존재

    # 1. Admin 관리인
    Admin(
        name="총괄 최고관리자",
        phone="010-1212-3434",
        account_number="국민 123-456-789012",
        business_number="101-86-12345",
        email="admin@rent3.saas.com"
    ).save("seed_admin")

    # 2. Lessors 임대인 (3명)
    lessor_chic = Lessor(
        name="김시크",
        phone="010-5555-1111",
        account_number="신한 110-382-998811",
        app_fee=33000,
        joined_at="2026-01-15",
        business_number="214-85-67890",
        email="chic@rent3.com",
        branch="main"
    )
    lessor_chic.save("seed_lessor_chic")

    lessor_sunho = Lessor(
        name="이선호",
        phone="010-5555-2222",
        account_number="우리 1002-345-678901",
        app_fee=33000,
        joined_at="2026-02-01",
        business_number="128-81-23456",
        email="sunho@rent3.com",
        branch="sunho"
    )
    lessor_sunho.save("seed_lessor_sunho")

    lessor_sinbi = Lessor(
        name="박신비",
        phone="010-5555-3333",
        account_number="하나 354-910-123456",
        app_fee=33000,
        joined_at="2026-03-10",
        business_number="305-82-34567",
        email="sinbi@rent3.com",
        branch="sinbi"
    )
    lessor_sinbi.save("seed_lessor_sinbi")

    # 3. Buildings 건물 (3동)
    bldg_chic = BuildingEntity(
        building_name="시크빌 본점",
        address="서울특별시 관악구 봉천동 888-12",
        id_lessor=lessor_chic.id_lessor,
        branch="main",
        total_rooms=29
    )
    bldg_chic.save("seed_bldg_chic")

    bldg_sunho = BuildingEntity(
        building_name="선호아트빌",
        address="서울특별시 동작구 사당동 450-7",
        id_lessor=lessor_sunho.id_lessor,
        branch="sunho",
        total_rooms=15
    )
    bldg_sunho.save("seed_bldg_sunho")

    bldg_sinbi = BuildingEntity(
        building_name="신비주택",
        address="서울특별시 서초구 방배동 120-4",
        id_lessor=lessor_sinbi.id_lessor,
        branch="sinbi",
        total_rooms=20
    )
    bldg_sinbi.save("seed_bldg_sinbi")

    # 4. Tenants 임차인 (샘플 3명)
    tenant1 = Tenant(
        name="홍길동",
        phone="010-9876-5432",
        account_number="카카오뱅크 3333-01-2345678",
        car_number="12가 3456",
        doorlock_pwd="2011*",
        business_number="",
        email="hong@tenant.com",
        current_room_id=""
    )
    tenant1.save("seed_tenant1")

    tenant2 = Tenant(
        name="이지은",
        phone="010-4321-8765",
        account_number="토스뱅크 1000-22-334455",
        car_number="34나 7890",
        doorlock_pwd="3022*",
        business_number="105-02-99881",
        email="jieun@tenant.com",
        current_room_id=""
    )
    tenant2.save("seed_tenant2")

    tenant3 = Tenant(
        name="강하늘",
        phone="010-7777-8888",
        account_number="국민 456-78-901234",
        car_number="",
        doorlock_pwd="1013*",
        business_number="",
        email="sky@tenant.com",
        current_room_id=""
    )
    tenant3.save("seed_tenant3")

    # 5. Rooms 임대상세 (샘플 호실들)
    room201 = RoomEntity(
        name="201호",
        id_building=bldg_chic.id_building,
        id_tenant=tenant1.id_tenant,
        registry_checked=True,
        registry_check_info={
            "verified": True,
            "checked_at": "2026-10-25 10:30:00",
            "issue_number": "REG-2026-CHIC201",
            "mortgage_amount": 0,
            "clean_title": True
        },
        status_description="남향 풀옵션, 신규 인버터 에어컨 시공 완료",
        deposit=5000000,
        monthly_rent=500000,
        maintenance_fee=50000
    )
    room201.save("seed_room201")
    tenant1.current_room_id = room201.id_room
    tenant1.save("update_tenant1_room")

    room202 = RoomEntity(
        name="202호",
        id_building=bldg_chic.id_building,
        id_tenant="",
        registry_checked=True,
        registry_check_info={"verified": True, "checked_at": "2026-10-20", "issue_number": "REG-2026-CHIC202", "clean_title": True},
        status_description="즉시입주 가능, 남향 채광 우수",
        deposit=5000000,
        monthly_rent=480000,
        maintenance_fee=50000
    )
    room202.save("seed_room202")

    room301 = RoomEntity(
        name="301호",
        id_building=bldg_sunho.id_building,
        id_tenant=tenant2.id_tenant,
        registry_checked=True,
        status_description="서향 햇살 가득, 붙박이장 완비",
        deposit=10000000,
        monthly_rent=600000,
        maintenance_fee=60000
    )
    room301.save("seed_room301")
    tenant2.current_room_id = room301.id_room
    tenant2.save("update_tenant2_room")

    # 6. Contracts 계약 (샘플 체결 건)
    ct1 = ContractEntity(
        id_room=room201.id_room,
        id_tenant=tenant1.id_tenant,
        contract_date="2026-10-01",
        contract_deposit=300000,
        move_in_date="2026-10-15",
        lease_term="12개월",
        deposit=5000000,
        monthly_rent=500000,
        maintenance_fee=50000,
        vat=0,
        rent_type="월세",
        payment_timing="후불",
        deposit_capture_img="https://images.unsplash.com/photo-1554224155-8d04cb21cd6c?w=500",
        status="CONFIRMED"
    )
    ct1.save("seed_ct1")

    # 7. Pays 수납 (2026.10 수납 내역)
    pay1 = Pay(
        id_room=room201.id_room,
        billing_month="2026.10",
        paid_date="2026-10-25",
        monthly_rent=500000,
        maintenance_fee=50000,
        vat=0,
        total_amount=550000,
        receipt_number="PAY-REC-202610-001"
    )
    pay1.save("seed_pay1")

    # 8. Outlays 지출 (경비 내역)
    out1 = Outlay(
        id_room=room201.id_room,
        summary="계단 정기 청소 및 분리수거 관리 대행",
        expense_date="2026-10-10",
        amount=150000,
        category="cleaning",
        vendor_name="깔끔환경크린"
    )
    out1.save("seed_out1")

    out2 = Outlay(
        id_room="COMMON",
        summary="승강기 정기 점검 및 유지보수료",
        expense_date="2026-10-15",
        amount=180000,
        category="elevator",
        vendor_name="현대엘리베이터케어"
    )
    out2.save("seed_out2")
