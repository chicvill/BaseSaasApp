"""
apps/_rent3_core/seed.py — 공동 JSON Data Pool 초기 시드 보장기.

저장소 경로(`pool_dir`)당 1회만 확인하며, 이미 JSON 파일에 건물 데이터가 있으면 덮어쓰지 않는다.
"""
from __future__ import annotations

from typing import Set

from .cosign import CoSignSession
from .models import Admin, Building, Contract, Lessor, Outlay, Pay, Room, Tenant
from .ops import DirectChat, MaintenanceTicket, RtmsReport
from .pool import pool

_seeded_dirs: Set[str] = set()


def ensure_pool_seeded(force: bool = False) -> None:
    p = pool()
    p.ensure_all_files_initialized()

    if not force and p.pool_dir in _seeded_dirs:
        return
    if not force and Building.count() > 0:
        _seeded_dirs.add(p.pool_dir)
        return

    src = "seed_bootstrap"

    # 1. Admin & Lessors (users.json)
    Admin(
        id="admin_master",
        username="admin",
        name="본사 총괄 관리인",
        phone="010-1212-3434",
        account_number="국민 123-456-789012",
        business_number="101-86-12345",
        email="admin@rent3.saas.kr",
    ).save("seeded", app_source=src)

    Lessor(
        id="lessor_chic",
        username="chic",
        name="김임대 (시크빌 대표)",
        phone="010-2222-1111",
        account_number="신한 110-482-998877",
        business_number="214-19-88412",
        email="chic@rent3.saas.kr",
        branch="main",
        app_fee=33000,
        app_fee_status="PAID",
        last_fee_paid_at="2026-10-05",
        joined_at="2025-03-01",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    Lessor(
        id="lessor_sunho",
        username="sunho",
        name="박선호 (선호아트빌 대표)",
        phone="010-3333-2222",
        account_number="국민 412-09-112233",
        business_number="128-31-44910",
        email="sunho@rent3.saas.kr",
        branch="sunho",
        app_fee=33000,
        app_fee_status="PAID",
        last_fee_paid_at="2026-10-10",
        joined_at="2025-06-15",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    Lessor(
        id="lessor_sinbi",
        username="sinbi",
        name="최신비 (신비주택 대표)",
        phone="010-4444-3333",
        account_number="우리 1002-849-221100",
        business_number="305-22-91820",
        email="sinbi@rent3.saas.kr",
        branch="sinbi",
        app_fee=33000,
        app_fee_status="PENDING",
        joined_at="2026-01-10",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    # 2. Buildings (buildings.json)
    Building(
        id="bldg_main",
        branch="main",
        building_name="시크빌 본점",
        address="서울특별시 관악구 남부순환로 1820-12",
        id_lessor="lessor_chic",
        default_maintenance_fee=50000,
        common_entrance_pwd="#2580*",
        wifi_info="ChicVill_5G / chic2026!",
        property_value=1850000000,
        manager_phone="010-2222-1111",
    ).save("seeded", app_source=src)

    Building(
        id="bldg_sunho",
        branch="sunho",
        building_name="선호아트빌",
        address="서울특별시 동작구 상도로 245-8",
        id_lessor="lessor_sunho",
        default_maintenance_fee=40000,
        common_entrance_pwd="#1357*",
        wifi_info="Sunho_Giga / sunho1004",
        property_value=1150000000,
        manager_phone="010-3333-2222",
    ).save("seeded", app_source=src)

    Building(
        id="bldg_sinbi",
        branch="sinbi",
        building_name="신비주택 & 상가",
        address="서울특별시 마포구 월드컵북로 64-5",
        id_lessor="lessor_sinbi",
        default_maintenance_fee=60000,
        common_entrance_pwd="#9900*",
        wifi_info="Sinbi_Fiber / sinbi7788",
        property_value=1450000000,
        manager_phone="010-4444-3333",
    ).save("seeded", app_source=src)

    # 3. Tenants (users.json)
    Tenant(
        id="tenant_201",
        name="이민수",
        phone="010-8888-2010",
        email="minsu@example.com",
        account_number="카카오뱅크 3333-01-998877",
        car_number="12가 3456",
        car_model="아반떼 CN7",
        doorlock_pwd="4860*",
        branch="main",
        id_building="bldg_main",
        current_room_id="room_main_201",
        room_no="201",
        checked_in=True,
        checked_in_at="2026-07-01T14:00:00",
        checkin_memo="에어컨 및 도어락 정상 작동 확인 완료",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    Tenant(
        id="tenant_202",
        name="정서연",
        phone="010-7777-2020",
        email="seoyeon@example.com",
        account_number="토스뱅크 1000-22-334455",
        car_number="",
        doorlock_pwd="7942*",
        branch="main",
        id_building="bldg_main",
        current_room_id="room_main_202",
        room_no="202",
        checked_in=True,
        checked_in_at="2026-08-01T11:30:00",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    Tenant(
        id="tenant_sunho_301",
        name="한지훈",
        phone="010-6666-3010",
        email="jihun@example.com",
        account_number="신한 110-332-445566",
        car_number="58나 9012",
        car_model="쏘렌토",
        doorlock_pwd="3690*",
        branch="sunho",
        id_building="bldg_sunho",
        current_room_id="room_sunho_301",
        room_no="301",
        checked_in=True,
        status="ACTIVE",
    ).save("seeded", app_source=src)

    Tenant(
        id="tenant_sinbi_101",
        name="디자인스튜디오 봄",
        phone="010-5555-1010",
        email="bomstudio@example.com",
        business_number="220-88-77123",
        doorlock_pwd="1122*",
        branch="sinbi",
        id_building="bldg_sinbi",
        current_room_id="room_sinbi_101",
        room_no="101",
        checked_in=True,
        status="ACTIVE",
    ).save("seeded", app_source=src)

    # 4. Rooms (rooms.json — 입주 호실 + 즉시입주 공실 매물)
    Room(
        id="room_main_201",
        branch="main",
        id_building="bldg_main",
        room_no="201",
        name="201호 (남향 풀옵션)",
        floor=2,
        area_sqm=24.2,
        room_category="주거용 원룸",
        vat_applicable=False,
        id_tenant="tenant_201",
        active_contract_id="ct_main_201",
        doorlock_pwd="4860*",
        deposit=10000000,
        monthly_rent=500000,
        maintenance_fee=50000,
        pay_timing="후불",
        is_published=False,
    ).save("seeded", app_source=src)

    Room(
        id="room_main_202",
        branch="main",
        id_building="bldg_main",
        room_no="202",
        name="202호 (분리형 원룸)",
        floor=2,
        area_sqm=25.0,
        room_category="주거용 원룸",
        vat_applicable=False,
        id_tenant="tenant_202",
        active_contract_id="ct_main_202",
        doorlock_pwd="7942*",
        deposit=5000000,
        monthly_rent=520000,
        maintenance_fee=50000,
        pay_timing="후불",
        is_published=False,
    ).save("seeded", app_source=src)

    Room(
        id="room_main_203",
        branch="main",
        id_building="bldg_main",
        room_no="203",
        name="203호 (채광 최상 남향 즉시입주)",
        floor=2,
        area_sqm=23.8,
        room_category="주거용 원룸",
        vat_applicable=False,
        doorlock_pwd="2030*",
        status_description="신규 천장형 에어컨·빌트인 냉장고 완비, 복비 0원 직거래 즉시입주",
        deposit=5000000,
        monthly_rent=500000,
        maintenance_fee=50000,
        pay_timing="후불",
        available_from="즉시입주",
        is_published=True,
    ).save("seeded", app_source=src)

    Room(
        id="room_sunho_301",
        branch="sunho",
        id_building="bldg_sunho",
        room_no="301",
        name="301호 (역세권 정남향)",
        floor=3,
        area_sqm=21.5,
        room_category="주거용 원룸",
        vat_applicable=False,
        id_tenant="tenant_sunho_301",
        active_contract_id="ct_sunho_301",
        doorlock_pwd="3690*",
        deposit=5000000,
        monthly_rent=460000,
        maintenance_fee=40000,
        pay_timing="후불",
        is_published=False,
    ).save("seeded", app_source=src)

    Room(
        id="room_sunho_302",
        branch="sunho",
        id_building="bldg_sunho",
        room_no="302",
        name="302호 (조용한 주택가 풀옵션 공실)",
        floor=3,
        area_sqm=20.4,
        room_category="주거용 원룸",
        vat_applicable=False,
        doorlock_pwd="3020*",
        status_description="도배·장판 신규 시공 완료, 지하철 도보 4분",
        deposit=3000000,
        monthly_rent=450000,
        maintenance_fee=40000,
        pay_timing="후불",
        available_from="즉시입주",
        is_published=True,
    ).save("seeded", app_source=src)

    Room(
        id="room_sinbi_101",
        branch="sinbi",
        id_building="bldg_sinbi",
        room_no="101",
        name="101호 (1층 로드형 상가/사무실)",
        floor=1,
        area_sqm=39.6,
        room_category="상가/사무실",
        vat_applicable=True,
        id_tenant="tenant_sinbi_101",
        active_contract_id="ct_sinbi_101",
        doorlock_pwd="1122*",
        deposit=20000000,
        monthly_rent=900000,
        maintenance_fee=100000,
        pay_timing="선불",
        is_published=False,
    ).save("seeded", app_source=src)

    Room(
        id="room_sinbi_202",
        branch="sinbi",
        id_building="bldg_sinbi",
        room_no="202",
        name="202호 (복층형 오피스텔 즉시입주)",
        floor=2,
        area_sqm=29.0,
        room_category="오피스텔",
        vat_applicable=False,
        doorlock_pwd="2029*",
        status_description="홍대입구 인접 층고 높은 복층 구조, 채광 우수",
        deposit=10000000,
        monthly_rent=620000,
        maintenance_fee=60000,
        pay_timing="후불",
        available_from="즉시입주",
        is_published=True,
    ).save("seeded", app_source=src)

    # 5. Contracts (contracts.json)
    # 201호: 2026-07 입주(후불 -> 7월 비과세, 8·9·10월 완납 상태)
    ct201 = Contract(
        id="ct_main_201",
        branch="main",
        id_building="bldg_main",
        id_room="room_main_201",
        room_no="201",
        id_lessor="lessor_chic",
        lessor_name="김임대 (시크빌 대표)",
        id_tenant="tenant_201",
        tenant_name="이민수",
        tenant_phone="010-8888-2010",
        contract_date="2026-06-25",
        move_in_date="2026-07-01",
        expiry_date="2027-07-01",
        lease_term_months=12,
        lease_type="월세",
        pay_timing="후불",
        vat_target=False,
        deposit=10000000,
        monthly_rent=500000,
        maintenance_fee=50000,
        lessor_signature="김임대(인)",
        tenant_signature="이민수(서명)",
        fixed_date_no="FIX-20260625-201AAA",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    # 202호: 2026-08 입주(후불 -> 8월 비과세, 9월·10월 미납 상태로 두어 FIFO 수납 테스트 즉시 가능!)
    ct202 = Contract(
        id="ct_main_202",
        branch="main",
        id_building="bldg_main",
        id_room="room_main_202",
        room_no="202",
        id_lessor="lessor_chic",
        lessor_name="김임대 (시크빌 대표)",
        id_tenant="tenant_202",
        tenant_name="정서연",
        tenant_phone="010-7777-2020",
        contract_date="2026-07-28",
        move_in_date="2026-08-01",
        expiry_date="2027-08-01",
        lease_term_months=12,
        lease_type="월세",
        pay_timing="후불",
        vat_target=False,
        deposit=5000000,
        monthly_rent=520000,
        maintenance_fee=50000,
        lessor_signature="김임대(인)",
        tenant_signature="정서연(서명)",
        fixed_date_no="FIX-20260728-202BBB",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    ct301 = Contract(
        id="ct_sunho_301",
        branch="sunho",
        id_building="bldg_sunho",
        id_room="room_sunho_301",
        room_no="301",
        id_lessor="lessor_sunho",
        lessor_name="박선호 (선호아트빌 대표)",
        id_tenant="tenant_sunho_301",
        tenant_name="한지훈",
        tenant_phone="010-6666-3010",
        contract_date="2026-08-20",
        move_in_date="2026-09-01",
        expiry_date="2027-09-01",
        lease_term_months=12,
        lease_type="월세",
        pay_timing="후불",
        vat_target=False,
        deposit=5000000,
        monthly_rent=460000,
        maintenance_fee=40000,
        lessor_signature="박선호(인)",
        tenant_signature="한지훈(서명)",
        fixed_date_no="FIX-20260820-301CCC",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    ct101 = Contract(
        id="ct_sinbi_101",
        branch="sinbi",
        id_building="bldg_sinbi",
        id_room="room_sinbi_101",
        room_no="101",
        id_lessor="lessor_sinbi",
        lessor_name="최신비 (신비주택 대표)",
        id_tenant="tenant_sinbi_101",
        tenant_name="디자인스튜디오 봄",
        tenant_phone="010-5555-1010",
        contract_date="2026-09-25",
        move_in_date="2026-10-01",
        expiry_date="2028-10-01",
        lease_term_months=24,
        lease_type="월세",
        pay_timing="선불",
        vat_target=True,
        vat_separate=True,
        deposit=20000000,
        monthly_rent=900000,
        maintenance_fee=100000,
        vat=100000,
        lessor_signature="최신비(인)",
        tenant_signature="디자인스튜디오봄(인)",
        fixed_date_no="FIX-20260925-101DDD",
        status="ACTIVE",
    ).save("seeded", app_source=src)

    # 6. Payments (payments.json — 201호 8,9,10월분 완납 & 301호 10월분 완납 & 101호 상가 10월분 부가세포함 완납)
    for ym, pdate in [("2026-08", "2026-08-05"), ("2026-09", "2026-09-05"), ("2026-10", "2026-10-05")]:
        Pay(
            branch="main",
            id_building="bldg_main",
            id_room="room_main_201",
            room_no="201",
            id_contract=ct201.id,
            id_tenant="tenant_201",
            payer_name="이민수",
            billing_month=ym,
            paid_date=pdate,
            rent_amount=500000,
            maintenance_amount=50000,
            vat_amount=0,
            total_amount=550000,
            payment_kind="MONTHLY_RENT",
            method="TRANSFER",
            idempotency_key=f"seed:201:{ym}",
        ).save("seeded", app_source=src)

    Pay(
        branch="sunho",
        id_building="bldg_sunho",
        id_room="room_sunho_301",
        room_no="301",
        id_contract=ct301.id,
        id_tenant="tenant_sunho_301",
        payer_name="한지훈",
        billing_month="2026-10",
        paid_date="2026-10-03",
        rent_amount=460000,
        maintenance_amount=40000,
        vat_amount=0,
        total_amount=500000,
        payment_kind="MONTHLY_RENT",
        method="KAKAOPAY",
        idempotency_key="seed:301:2026-10",
    ).save("seeded", app_source=src)

    Pay(
        branch="sinbi",
        id_building="bldg_sinbi",
        id_room="room_sinbi_101",
        room_no="101",
        id_contract=ct101.id,
        id_tenant="tenant_sinbi_101",
        payer_name="디자인스튜디오 봄",
        billing_month="2026-10",
        paid_date="2026-10-01",
        rent_amount=900000,
        maintenance_amount=100000,
        vat_amount=100000,
        total_amount=1100000,
        payment_kind="MONTHLY_RENT",
        method="TRANSFER",
        idempotency_key="seed:101:2026-10",
    ).save("seeded", app_source=src)

    # 7. Outlays (outlays.json — 공용 운영비 샘플)
    Outlay(
        branch="main",
        id_building="bldg_main",
        category="cleaning",
        title="10월 계단·복도 정기 미화 대행",
        expense_date="2026-10-10",
        amount=180000,
        vendor="클린케어환경",
    ).save("seeded", app_source=src)

    Outlay(
        branch="main",
        id_building="bldg_main",
        category="elevator",
        title="10월 승강기 법정 정기점검 수수료",
        expense_date="2026-10-15",
        amount=110000,
        vendor="현대엘리베이터서비스",
    ).save("seeded", app_source=src)

    Outlay(
        branch="main",
        id_building="bldg_main",
        category="internet_tv",
        title="전 세대 기가인터넷 단체회선료",
        expense_date="2026-10-20",
        amount=165000,
        vendor="KT기업통신",
    ).save("seeded", app_source=src)

    # 8. Ops (ops.json — RTMS 신고, A/S 티켓, 1:1 채팅 샘플)
    for ct, fix in [(ct201, ct201.fixed_date_no), (ct202, ct202.fixed_date_no), (ct301, ct301.fixed_date_no)]:
        RtmsReport(
            branch=ct.branch,
            id_contract=ct.id,
            id_room=ct.id_room,
            room_no=ct.room_no,
            lessor_name=ct.lessor_name,
            tenant_name=ct.tenant_name,
            deposit=ct.deposit,
            monthly_rent=ct.monthly_rent,
            contract_date=ct.contract_date,
            deadline_date="2026-11-25",
            mandatory=True,
            status="FILED",
            fixed_date_no=fix,
            filed_at="2026-10-01T10:00:00",
        ).save("seeded", app_source=src)

    MaintenanceTicket(
        id="as_sample_201",
        branch="main",
        id_building="bldg_main",
        id_room="room_main_201",
        room_no="201",
        id_tenant="tenant_201",
        tenant_name="이민수",
        category="설비/배관",
        title="세면대 팝업 마개 배수 느림 점검 요청",
        description="세면대 물이 조금 천천히 내려가서 점검 부탁드립니다.",
        status="RECEIVED",
    ).save("seeded", app_source=src)

    DirectChat(
        branch="main",
        id_room="room_main_203",
        room_no="203",
        sender_role="tenant",
        sender_name="김예비(방문희망)",
        message="안녕하세요! 시크빌 203호 공실 이번 주 토요일 오후 2시에 실물 투어 가능할까요?",
    ).save("seeded", app_source=src)

    # 샘플 진행중 공동계약 세션 1건 (시크빌 203호)
    CoSignSession.open_session(
        branch="main",
        room_no="203",
        tenant_name="김예비",
        tenant_phone="010-9999-2030",
        app_source=src,
    )

    _seeded_dirs.add(p.pool_dir)

