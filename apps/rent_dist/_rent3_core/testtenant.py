"""
apps/rent_dist/_rent3_core/testtenant.py — 시험·실사용 확인용 임차인 호실 하나를 Data Pool 에 안전하게 등록한다 (기본: 시크빌 701호 민흥식, 0원 계약).

관리인 앱(🛟 운영 탭)과 scripts/add_test_tenant.py 가 함께 쓴다.
 - 점검(apply=False)은 아무것도 바꾸지 않고, 등록(apply=True)은 먼저 풀 전체를 자동 백업한다.
 - 같은 호실·임차인이 이미 있으면 아무것도 만들지 않는다(멱등). 다른 임차인이 있거나 계약이 없는 기존 호실은 건드리지 않고 멈춘다.
 - 호실 한도(임대인 room_limit)를 넘으면 raise_limit 을 줘야만 한 칸 올린다. 호실은 공개 매물에 나오지 않게 비공개로 만든다.
 - 0원 계약은 장부에서 ZERO_DUE 로 처리되어 미납이 생기지 않는다(등록 뒤 장부를 다시 계산해 확인한다).
status: DRY(점검만) | EXISTS(이미 있음) | BLOCKED(멈춤) | DONE(등록함)

mark_prospect_verified: 가짜 번호 등으로 문자 인증을 받을 수 없는 '시험용 예비임차인'(예: 홍길동 010-1111-2222)을 관리인이 본인인증 완료로 표시한다.
 - 가입만 한 예비임차인(PROSPECT)만 대상이다. 입주 중인 임차인·이미 인증된 계정은 건드리지 않는다.
 - 점검(apply=False)은 바꾸지 않고, 처리(apply=True)는 먼저 풀 전체를 자동 백업한다.
 - 기록에 provider=admin_test · fake=True · assurance=test 로 남아 실제 인증과 구분되고, 이력(_events)에 'test_identity_marked' 로 남는다.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Dict

from . import backup, franchise
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
           checked_in_at=today.isoformat() + "T12:00:00", status="ACTIVE",
           identity=franchise.invite_identity_record(SimpleNamespace(name=name, phone=phone), "test")).save(action="test_tenant_added", app_source="add_test_tenant")
    Contract(id=cid, branch=branch, id_building=bldg.id, id_room=rid, room_no=room_no, id_lessor=(lessor.id if lessor else ""),
             lessor_name=(lessor.name if lessor else ""), id_tenant=tid, tenant_name=name, tenant_phone=phone, contract_date=today.isoformat(),
             move_in_date=today.isoformat(), expiry_date=(today + timedelta(days=365)).isoformat(), lease_term_months=12, lease_type="월세",
             pay_timing="후불", vat_target=False, deposit=0, monthly_rent=0, maintenance_fee=0, status="ACTIVE").save(action="test_contract_added", app_source="add_test_tenant")
    unpaid = Contract.get(cid).ledger().get("total_unpaid")
    return {"status": "DONE", "head": head, "backup": info.get("name", ""), "unpaid": unpaid,
            "message": f"등록했습니다: {plan}. 등록 전 백업: {info.get('name', '')}. 장부 미납 {unpaid}원(0이어야 정상)."}


def mark_prospect_verified(name: str, phone: str, apply: bool = False) -> Dict[str, Any]:
    name, phone = str(name).strip(), str(phone).strip()
    if not name or len(_digits(phone)) < 10:
        return {"status": "BLOCKED", "message": "성함과 휴대폰 번호(10자리 이상)를 모두 입력해 주세요."}
    found = [t for t in Tenant.all() if _digits(t.phone) == _digits(phone) and t.name.strip() == name]
    if not found:
        return {"status": "BLOCKED", "message": f"가입된 계정이 없습니다: {name} {phone}. 먼저 포털에서 그 이름·번호로 예비임차인 가입을 해 주세요."}
    if len(found) > 1:
        return {"status": "BLOCKED", "message": f"같은 이름·번호의 계정이 {len(found)}개라 멈춥니다. 기존 자료를 보호하기 위해 아무것도 바꾸지 않았습니다."}
    t = found[0]
    if (t.identity or {}).get("verified"):
        return {"status": "EXISTS", "message": f"이미 본인인증이 되어 있습니다({t.name} {t.phone} · {(t.identity or {}).get('provider', '')}). 아무것도 바꾸지 않았습니다."}
    if t.status != "PROSPECT":
        return {"status": "BLOCKED", "message": f"{t.name}님은 예비임차인이 아닙니다(상태 {t.status}). 입주 중인 임차인은 이 도구로 바꾸지 않습니다."}
    plan = f"{t.name} {t.phone} (예비임차인) → 본인인증 완료(시험용 표시)"
    if not apply:
        return {"status": "DRY", "message": f"점검만 했습니다(바뀐 것 없음). 처리하면 → {plan}"}
    info = backup.create()
    t.identity = {"verified": True, "name": t.name, "phone": t.phone, "birth6": "", "provider": "admin_test", "fake": True,
                  "assurance": "test", "verified_at": date.today().isoformat() + "T12:00:00"}
    t.save(action="test_identity_marked", app_source="admin_test_identity")
    return {"status": "DONE", "backup": info.get("name", ""),
            "message": f"처리했습니다: {plan}. 처리 전 백업: {info.get('name', '')}. 이 계정으로 다시 로그인하면 계약을 시작할 수 있습니다."}
