"""
scripts/seed_chicvill_2026.py — 2026년 시크빌 임대차 장부(엑셀)를 Rent3 Data Pool 에 반영한다.

반영하는 것: 호실별 보증금 · 월 임대료 · 관리비 · 선불/후불, 예비임차인 계정 `imcha`.
반영하지 않는 것: 세입자 성명 · 주민번호 · 전화번호 등 개인정보 (장부에는 있으나 시드에 넣지 않는다).

이미 계약·입주가 있는 호실(id_tenant / active_contract_id 가 있는 호실)은 건드리지 않는다.
여러 번 실행해도 결과가 같다(멱등).

사용: py scripts/seed_chicvill_2026.py [--pool-dir 경로]
"""
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from apps.rent_dist._rent3_core import Building, Room, Tenant, ensure_pool_seeded, use_pool_dir  # noqa: E402

BRANCH = "main"
SRC = "seed_chicvill_2026"

# 호실: (보증금, 월 임대료, 관리비, 납부 시기)  — 2026년 장부의 최근 월 기준
LEDGER = {
    "101": (2_000_000, 150_000, 100_000, "후불"),
    "201": (5_000_000, 290_000, 150_000, "후불"),
    "202": (3_000_000, 290_000, 150_000, "선불"),
    "203": (5_000_000, 290_000, 150_000, "후불"),
    "204": (5_000_000, 290_000, 150_000, "후불"),
    "205": (5_000_000, 250_000, 150_000, "후불"),
    "301": (5_000_000, 290_000, 150_000, "후불"),
    "302": (40_000_000, 0, 150_000, "후불"),          # 전세
    "303": (5_000_000, 290_000, 150_000, "후불"),
    "304": (10_000_000, 340_000, 50_000, "후불"),
    "305": (3_000_000, 290_000, 150_000, "후불"),
    "401": (5_000_000, 290_000, 150_000, "후불"),
    "402": (3_000_000, 290_000, 150_000, "선불"),
    "403": (5_000_000, 290_000, 150_000, "후불"),
    "404": (5_000_000, 290_000, 150_000, "후불"),
    "405": (5_000_000, 290_000, 150_000, "후불"),
    "501": (5_000_000, 290_000, 150_000, "후불"),
    "502": (5_000_000, 290_000, 150_000, "후불"),
    "503": (5_000_000, 290_000, 150_000, "후불"),
    "504": (5_000_000, 290_000, 150_000, "후불"),
    "505": (5_000_000, 250_000, 150_000, "선불"),
    "601": (5_000_000, 350_000, 90_000, "후불"),
    "602": (5_000_000, 290_000, 150_000, "후불"),
    "603": (5_000_000, 290_000, 150_000, "후불"),
    "604": (5_000_000, 290_000, 150_000, "후불"),
    "605": (5_000_000, 290_000, 100_000, "후불"),
    # 701 은 주인세대(임대 대상 아님)
    "702": (6_000_000, 290_000, 250_000, "후불"),
    "703": (5_000_000, 290_000, 150_000, "후불"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", default="")
    args = ap.parse_args()
    if args.pool_dir:
        use_pool_dir(args.pool_dir)
    ensure_pool_seeded()

    bldg = Building.first(branch=BRANCH)
    if not bldg:
        sys.exit("시크빌(main) 건물이 Data Pool 에 없습니다.")

    created = updated = skipped = 0
    for room_no, (deposit, rent, fee, timing) in LEDGER.items():
        room = Room.first(branch=BRANCH, room_no=room_no)
        if room and (room.id_tenant or room.active_contract_id):
            skipped += 1
            continue
        if room is None:
            Room(
                id=f"room_{BRANCH}_{room_no}",
                branch=BRANCH,
                id_building=bldg.id,
                room_no=room_no,
                name=f"{room_no}호 (주거용 원룸)",
                floor=int(room_no[0]),
                room_category="주거용 원룸",
                deposit=deposit,
                monthly_rent=rent,
                maintenance_fee=fee,
                pay_timing=timing,
                available_from="즉시입주",
                is_published=False,
            ).save("seeded", app_source=SRC)
            created += 1
        else:
            room.deposit, room.monthly_rent = deposit, rent
            room.maintenance_fee, room.pay_timing = fee, timing
            room.save("room_updated", app_source=SRC)
            updated += 1

    if not Tenant.get("imcha"):
        Tenant(
            id="imcha",
            name="임차인 (imcha)",
            phone="010-0000-1212",
            branch=BRANCH,
            status="PROSPECT",
            password="1212",
        ).save("seeded", app_source=SRC)
        print("예비임차인 imcha 생성")

    print(f"호실 신규 {created} · 갱신 {updated} · 입주중 건너뜀 {skipped}")


if __name__ == "__main__":
    main()
