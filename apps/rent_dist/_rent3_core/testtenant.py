"""
apps/rent_dist/_rent3_core/testtenant.py — 시험·실사용 확인용 임차인 호실 하나를 Data Pool 에 안전하게 등록한다 (기본: 시크빌 701호 민흥식, 0원 계약).

관리인 앱(🛟 운영 탭)과 scripts/add_test_tenant.py 가 함께 쓴다.
 - 점검(apply=False)은 아무것도 바꾸지 않고, 등록(apply=True)은 먼저 풀 전체를 자동 백업한다.
 - 같은 호실·임차인이 이미 있으면 아무것도 만들지 않는다(멱등). 다른 임차인이 있거나 계약이 없는 기존 호실은 건드리지 않고 멈춘다.
 - 호실 한도(임대인 room_limit)를 넘으면 raise_limit 을 줘야만 한 칸 올린다. 호실은 공개 매물에 나오지 않게 비공개로 만든다.
 - 0원 계약은 장부에서 ZERO_DUE 로 처리되어 미납이 생기지 않는다(등록 뒤 장부를 다시 계산해 확인한다).
status: DRY(점검만) | EXISTS(이미 있음) | BLOCKED(멈춤) | DONE(등록함)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

from . import backup
from .models import Building, Contract, Lessor, Room, Tenant


def _digits(s: Any) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def register(branch: str = "main", room_no: str = "701", name: str = "민흥식", phone: str = "010-3269-3343",
             floor: int = 7, apply: bool = False, raise_limit: bool = False) -> Dict[str, Any]:
    room_no, name, phone = str(room_no).strip(), str(name).strip(), str(phone).strip()
    if not room_no or not name or len(_digits(phone)) < 10:
        return {"status": "BLOCKED", "message": "호실·성함·휴대폰 번호(10자리 이상)를 모두 입력해 주세요."}
    bldg = Building.first(branch=branch)
    if bldg is None:
        return {"status": "BLOCKED", "message": f"'{branch}' 지점의 건물을 찾을 수 없습니다. 지점 코드를 확인해 주세요."}
    lessor = Lessor.get(bldg.id_lessor) if bldg.id_lessor else Lessor.first(branch=branch)
    rooms = Room.find(branch=branch)
    limit = lessor.room_limit if lessor else 0
    head = f"{bldg.building_name}({branch}) · 현재 {len(rooms)}호실 · 한도 {limit if lessor else '없음'}"
    room = next((r for r in rooms if r.room_no == room_no), None)
    tenant = next((t for t in Tenant.find(branch=branch) if t.room_no == room_no and _digits(t.phone) == _digits(phone)), None)
    if room is not None and tenant is not None and room.active_contract_id:
        ct = Contract.get(room.active_contract_id)
        unpaid = ct.ledger().get("total_unpaid") if ct else None
        return {"status": "EXISTS", "message": f"이미 등록되어 있습니다: {room_no}호 {tenant.name} ({tenant.phone}) · 미납 {unpaid}원. 아무것도 바꾸지 않았습니다.", "head": head}
    if room is not None:
        return {"status": "BLOCKED", "message": f"{room_no}호가 이미 있지만 이 임차인({name})과 연결되어 있지 않습니다(다른 임차인이거나 계약이 없음). 기존 자료를 보호하기 위해 멈춥니다.", "head": head}
    over = bool(lessor) and len(rooms) + 1 > limit
    if over and not raise_limit:
        return {"status": "BLOCKED", "need_raise_limit": True, "head": head,
                "message": f"호실 한도({limit}실)를 넘습니다(현재 {len(rooms)}실). 한도를 한 칸 올려도 되면 '한도 올리기'를 체크하세요."}
    today = date.today()
    plan = f"{room_no}호({floor}층, 비공개) · 임차인 {name} {phone} · 보증금 0 · 월세 0 · 관리비 0 · 입주 {today.isoformat()}"
    if not apply:
        return {"status": "DRY", "message": f"점검만 했습니다(바뀐 것 없음). 등록하면 → {plan}", "head": head, "need_raise_limit": over}
    info = backup.create()
    if over:
        lessor.room_limit = len(rooms) + 1
        lessor.save(action="room_limit_raised_for_test_tenant", app_source="add_test_tenant")
    rid, tid, cid = f"room_{branch}_{room_no}", f"tenant_{branch}_{room_no}", f"ct_{branch}_{room_no}"
    Room(id=rid, branch=branch, id_building=bldg.id, building_name=bldg.building_name, room_no=room_no, name=f"{room_no}호", floor=floor,
         deposit=0, monthly_rent=0, maintenance_fee=0, is_published=False, id_tenant=tid, active_contract_id=cid,
         move_in_date=today.isoformat()).save(action="test_room_added", app_source="add_test_tenant")
    Tenant(id=tid, name=name, phone=phone, branch=branch, id_building=bldg.id, current_room_id=rid, room_no=room_no, checked_in=True,
           checked_in_at=today.isoformat() + "T12:00:00", status="ACTIVE").save(action="test_tenant_added", app_source="add_test_tenant")
    Contract(id=cid, branch=branch, id_building=bldg.id, id_room=rid, room_no=room_no, id_lessor=(lessor.id if lessor else ""),
             lessor_name=(lessor.name if lessor else ""), id_tenant=tid, tenant_name=name, tenant_phone=phone, contract_date=today.isoformat(),
             move_in_date=today.isoformat(), expiry_date=(today + timedelta(days=365)).isoformat(), lease_term_months=12, lease_type="월세",
             pay_timing="후불", vat_target=False, deposit=0, monthly_rent=0, maintenance_fee=0, status="ACTIVE").save(action="test_contract_added", app_source="add_test_tenant")
    unpaid = Contract.get(cid).ledger().get("total_unpaid")
    return {"status": "DONE", "head": head, "backup": info.get("name", ""), "unpaid": unpaid,
            "message": f"등록했습니다: {plan}. 등록 전 백업: {info.get('name', '')}. 장부 미납 {unpaid}원(0이어야 정상)."}
