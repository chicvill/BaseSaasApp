"""
scripts/add_test_tenant.py — 시험·실사용 확인용 임차인 호실 하나를 Data Pool 에 안전하게 등록한다 (기본: 시크빌 701호 민흥식, 보증금·월세·관리비 0원).

  py scripts/add_test_tenant.py --pool-dir <풀 폴더>            # 점검만(아무것도 바꾸지 않고 무엇을 할지 보여 준다)
  py scripts/add_test_tenant.py --pool-dir <풀 폴더> --apply    # 실제 등록(등록 전에 풀 전체를 자동 백업)

안전 장치
 - 이미 같은 호실·임차인이 있으면 아무것도 만들지 않는다(여러 번 실행해도 같은 결과, 중복 생성 없음). 기존 호실·계약은 건드리지 않는다.
 - 호실 한도(임대인 room_limit)를 넘으면 멈춘다(--raise-limit 을 줘야 한도를 한 칸 올린다).
 - 공개 매물 목록에는 나오지 않게 비공개(is_published=False)로 만든다.
 - 0원 계약은 장부 계산에서 ZERO_DUE 로 처리되어 미납이 생기지 않는다(등록 뒤 장부를 다시 계산해 확인한다).
 - 서버를 끈 상태에서 하거나, 서버가 떠 있으면 등록 뒤 서버를 다시 시작한다(서버는 시작할 때 풀을 읽는다).
"""
import argparse
import os
import sys
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("RENT3_DEMO_SEED", "0")   # 빈 폴더에 시연 데이터를 채우지 않는다
for _s in (sys.stdout, sys.stderr):   # 윈도 콘솔(cp949)에서도 이모지·한글이 깨지거나 멈추지 않게
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--room", default="701")
    ap.add_argument("--name", default="민흥식")
    ap.add_argument("--phone", default="010-3269-3343")
    ap.add_argument("--floor", type=int, default=7)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--raise-limit", action="store_true", help="호실 한도가 모자라면 한 칸 올린다")
    a = ap.parse_args()

    from apps.rent_dist._rent3_core import Building, Contract, Lessor, Room, Tenant, backup, use_pool_dir

    use_pool_dir(os.path.abspath(a.pool_dir))
    bldg = Building.first(branch=a.branch)
    if bldg is None:
        print(f"❌ '{a.branch}' 지점의 건물을 찾을 수 없습니다. 풀 폴더({a.pool_dir})를 확인하세요.")
        return 2
    lessor = Lessor.get(bldg.id_lessor) if bldg.id_lessor else Lessor.first(branch=a.branch)
    rooms = Room.find(branch=a.branch)
    room = next((r for r in rooms if r.room_no == a.room), None)
    digits = "".join(ch for ch in a.phone if ch.isdigit())
    tenant = next((t for t in Tenant.find(branch=a.branch) if t.room_no == a.room and "".join(c for c in t.phone if c.isdigit()) == digits), None)
    print(f"건물: {bldg.building_name} ({a.branch}) · 호실 {len(rooms)}개 · 한도 {lessor.room_limit if lessor else '없음'}")

    if room is not None and tenant is not None and room.active_contract_id:
        ct = Contract.get(room.active_contract_id)
        led = ct.ledger() if ct else {}
        print(f"✅ 이미 등록되어 있습니다: {a.room}호 {tenant.name} ({tenant.phone}) · 계약 {ct.id if ct else '-'} · 미납 {led.get('total_unpaid', '-')}원. 아무것도 바꾸지 않았습니다.")
        return 0
    if room is not None:
        print(f"❌ {a.room}호가 이미 있지만 이 임차인({a.name})과 연결되어 있지 않습니다(다른 임차인이거나 계약이 없음). 기존 자료를 보호하기 위해 멈춥니다.")
        return 3
    if lessor is not None and len(rooms) + 1 > lessor.room_limit and not a.raise_limit:
        print(f"❌ 호실 한도({lessor.room_limit}실)를 넘습니다(현재 {len(rooms)}실). 한도를 올려도 되면 --raise-limit 을 붙이세요.")
        return 4

    today = date.today()
    plan = f"{a.room}호({a.floor}층, 비공개) · 임차인 {a.name} {a.phone} · 보증금 0 · 월세 0 · 관리비 0 · 입주 {today.isoformat()}"
    if not a.apply:
        print(f"📝 점검만 했습니다. --apply 를 붙이면 등록합니다 → {plan}")
        return 0

    info = backup.create()
    print(f"🛟 등록 전 백업: {info.get('name') or info}")
    if lessor is not None and len(rooms) + 1 > lessor.room_limit:
        lessor.room_limit = len(rooms) + 1
        lessor.save(action="room_limit_raised_for_test_tenant", app_source="add_test_tenant")
        print(f"   호실 한도를 {lessor.room_limit}실로 올렸습니다.")
    rid, tid, cid = f"room_{a.branch}_{a.room}", f"tenant_{a.branch}_{a.room}", f"ct_{a.branch}_{a.room}"
    Room(id=rid, branch=a.branch, id_building=bldg.id, building_name=bldg.building_name, room_no=a.room, name=f"{a.room}호", floor=a.floor,
         deposit=0, monthly_rent=0, maintenance_fee=0, is_published=False, id_tenant=tid, active_contract_id=cid,
         move_in_date=today.isoformat()).save(action="test_room_added", app_source="add_test_tenant")
    Tenant(id=tid, name=a.name, phone=a.phone, branch=a.branch, id_building=bldg.id, current_room_id=rid, room_no=a.room,
           checked_in=True, checked_in_at=today.isoformat() + "T12:00:00", status="ACTIVE").save(action="test_tenant_added", app_source="add_test_tenant")
    Contract(id=cid, branch=a.branch, id_building=bldg.id, id_room=rid, room_no=a.room, id_lessor=(lessor.id if lessor else ""),
             lessor_name=(lessor.name if lessor else ""), id_tenant=tid, tenant_name=a.name, tenant_phone=a.phone,
             contract_date=today.isoformat(), move_in_date=today.isoformat(), expiry_date=(today + timedelta(days=365)).isoformat(),
             lease_term_months=12, lease_type="월세", pay_timing="후불", vat_target=False, deposit=0, monthly_rent=0, maintenance_fee=0,
             status="ACTIVE").save(action="test_contract_added", app_source="add_test_tenant")
    led = Contract.get(cid).ledger()
    print(f"✅ 등록했습니다: {plan}")
    print(f"   장부 확인: 미납 {led.get('total_unpaid')}원 (0이어야 정상)")
    return 0 if led.get("total_unpaid", 0) == 0 else 5


if __name__ == "__main__":
    sys.exit(main())
