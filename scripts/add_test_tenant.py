"""
scripts/add_test_tenant.py — 시험·실사용 확인용 임차인 호실 하나를 Data Pool 에 안전하게 등록한다 (기본: 시크빌 701호 민흥식, 보증금·월세·관리비 0원).
같은 기능이 관리인 앱의 🛟 운영 탭 '🧪 시험 임차인 등록'에도 있다(서버 터미널 없이 사용). 로직은 apps/rent_dist/_rent3_core/testtenant.py.

  py scripts/add_test_tenant.py --pool-dir <풀 폴더>            # 점검만(아무것도 바꾸지 않는다)
  py scripts/add_test_tenant.py --pool-dir <풀 폴더> --apply    # 실제 등록(등록 전에 풀 전체를 자동 백업)

같은 호실·임차인이 있으면 아무것도 만들지 않고(멱등), 다른 임차인이 있는 호실이면 멈추며, 호실 한도를 넘으면 --raise-limit 이 있어야 한 칸 올린다.
서버가 떠 있는 풀에 직접 쓰면 서버를 다시 시작해야 반영된다(서버는 시작할 때 풀을 읽는다). 관리인 앱에서 하면 재시작이 필요 없다.
"""
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("RENT3_DEMO_SEED", "0")   # 빈 폴더에 시연 데이터를 채우지 않는다
for _s in (sys.stdout, sys.stderr):   # 윈도 콘솔(cp949)에서도 한글이 깨지거나 멈추지 않게
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
    from apps.rent_dist._rent3_core import testtenant, use_pool_dir

    use_pool_dir(os.path.abspath(a.pool_dir))
    out = testtenant.register(a.branch, a.room, a.name, a.phone, floor=a.floor, apply=a.apply, raise_limit=a.raise_limit)
    if out.get("head"):
        print(out["head"])
    print(f"[{out['status']}] {out['message']}")
    return {"DRY": 0, "EXISTS": 0, "DONE": 0 if out.get("unpaid", 0) == 0 else 5, "BLOCKED": 3}.get(out["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
