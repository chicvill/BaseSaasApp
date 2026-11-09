"""
scripts/fresh_pool.py — Data Pool 을 비우고 '실제 정보만' 담아 새로 만든다 (시험·시연용 시드 없이).

왜 필요한가: 기본 시드(chic/1212, 김임대·박선호·최신비, 샘플 임차인·수납)가 운영 데이터에 섞여 있으면
불필요한 계정·장부가 남는다. 이 도구는 설정 파일에 적은 관리자·임대인·건물·호실만 만든다.

사용:
  py scripts/fresh_pool.py --config 경로/fresh.json --pool-dir 새_풀_폴더            # 새 폴더에 만든다 (기본, 안전)
  py scripts/fresh_pool.py --config 경로/fresh.json --pool-dir 기존_풀_폴더 --wipe   # 기존 폴더를 옆으로 백업하고 새로 만든다

- 기존 폴더는 지우지 않고 `<폴더>.bak-날짜시각` 으로 옮겨 둔다(되돌릴 수 있다). 백업은 사람이 확인한 뒤에 지운다.
- 설정 파일에는 비밀번호·실명·전화번호가 들어가므로 **저장소에 올리지 않는다**(`data/` 아래나 저장소 밖에 둔다).
  형식은 scripts/fresh_pool.example.json 참고.
- 서버에서는 환경변수 RENT3_DEMO_SEED=0 이 켜져 있어야 앱이 다시 시드를 채우지 않는다.
- 관리자(admin) 로그인 계정은 Data Pool 이 아니라 플랫폼 DB(BASS_SEED_PASSWORD)에 있어 이 도구가 건드리지 않는다.
"""
import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime

os.environ["RENT3_DEMO_SEED"] = "0"   # 기본 시드를 끈다 (import 전에 설정)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from apps.rent_dist._rent3_core import Admin, Building, Lessor, Room, ensure_pool_seeded, use_pool_dir  # noqa: E402
from apps.rent_dist._rent3_core import passwords as pwd  # noqa: E402

SRC = "fresh_pool"


def _rooms(spec: dict, base_dir: str) -> list:
    rows = list(spec.get("rooms") or [])
    if spec.get("rooms_csv"):
        path = spec["rooms_csv"] if os.path.isabs(spec["rooms_csv"]) else os.path.join(base_dir, spec["rooms_csv"])
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows += list(csv.DictReader(f))
    return rows


def build(cfg: dict, base_dir: str = ".") -> dict:
    """설정대로 Data Pool 을 채운다 (이미 use_pool_dir 로 대상 폴더가 정해진 상태)."""
    ensure_pool_seeded()   # RENT3_DEMO_SEED=0 이면 빈 파일만 준비한다
    adm = cfg.get("admin") or {}
    Admin(id="admin_master", username=adm.get("username", "admin"), name=adm.get("name", "본사 총괄 관리인"),
          phone=adm.get("phone", ""), account_number=adm.get("account_number", ""),
          business_number=adm.get("business_number", ""), email=adm.get("email", "")).save("seeded", app_source=SRC)
    n_lessor = n_bldg = n_room = n_tenant = n_pay = 0
    for ls in cfg.get("lessors") or []:
        branch = ls.get("branch") or ls["username"]
        pw = ls["password"]
        if len(pw) < 8:
            raise SystemExit(f"임대인 '{ls['username']}' 비밀번호는 8자 이상이어야 합니다.")
        lessor = Lessor(
            id=ls.get("id") or f"lessor_{branch}", username=ls["username"], name=ls.get("name", ""),
            phone=ls.get("phone", ""), account_number=ls.get("account_number", ""),
            business_number=ls.get("business_number", ""), email=ls.get("email", ""), branch=branch,
            status=ls.get("status", "ACTIVE"), room_limit=int(ls.get("room_limit", 100)),
            password=pwd.hash_password(pw),
        ).save("seeded", app_source=SRC)
        n_lessor += 1
        b = ls.get("building")
        if not b:
            continue
        bldg = Building(
            id=f"bldg_{branch}", branch=branch, building_name=b["name"], address=b.get("address", ""),
            id_lessor=lessor.id, default_maintenance_fee=int(b.get("default_maintenance_fee", 0)),
            common_entrance_pwd=b.get("common_entrance_pwd", ""), manager_phone=ls.get("phone", ""),
        ).save("seeded", app_source=SRC)
        n_bldg += 1
        for r in _rooms(b, base_dir):
            no = str(r["room_no"]).strip()
            Room(
                id=f"room_{branch}_{no}", branch=branch, id_building=bldg.id, room_no=no,
                name=r.get("name") or f"{no}호", floor=int(r.get("floor") or (no[:-2] or 1)),
                room_category=r.get("room_category") or "주거용 원룸",
                deposit=int(r.get("deposit") or 0), monthly_rent=int(r.get("monthly_rent") or 0),
                maintenance_fee=int(r.get("maintenance_fee") or bldg.default_maintenance_fee or 0),
                pay_timing=r.get("pay_timing") or "후불", available_from="즉시입주", is_published=False,
                doorlock_pwd=r.get("doorlock_pwd", ""),
            ).save("seeded", app_source=SRC)
            n_room += 1
        if b.get("ledger"):   # 종이 장부(수납부) CSV → 세입자·계약·월별 수납
            import ledger_import
            res = ledger_import.import_ledger(lessor, bldg, b["ledger"], base_dir)
            n_tenant += res["tenants"]; n_pay += res["payments"]
    return {"lessors": n_lessor, "buildings": n_bldg, "rooms": n_room, "tenants": n_tenant, "payments": n_pay}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--pool-dir", required=True)
    ap.add_argument("--wipe", action="store_true", help="대상 폴더에 데이터가 있으면 옆으로 백업하고 새로 만든다")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8-sig"))
    d = os.path.abspath(a.pool_dir)
    if os.path.isdir(d) and any(os.scandir(d)):
        if not a.wipe:
            sys.exit(f"'{d}' 에 이미 데이터가 있습니다. 기존 데이터를 백업하고 새로 만들려면 --wipe 를 붙이세요.")
        bak = f"{d}.bak-{datetime.now():%Y%m%d%H%M%S}"
        try:
            shutil.move(d, bak)
        except OSError:   # 폴더 자체가 저장소(볼륨) 마운트 지점이면 이름을 바꿀 수 없다 — 복사해 두고 안의 내용만 비운다
            shutil.copytree(d, bak)
            for entry in os.scandir(d):
                shutil.rmtree(entry.path) if entry.is_dir(follow_symlinks=False) else os.remove(entry.path)
        print(f"기존 데이터를 백업했습니다: {bak}")
    os.makedirs(d, exist_ok=True)
    use_pool_dir(d)
    res = build(cfg, os.path.dirname(os.path.abspath(a.config)))
    print(f"새 Data Pool 생성 완료: 임대인 {res['lessors']} · 건물 {res['buildings']} · 호실 {res['rooms']} · 입주 세입자 {res['tenants']} · 수납 기록 {res['payments']}  ({d})")


if __name__ == "__main__":
    main()
