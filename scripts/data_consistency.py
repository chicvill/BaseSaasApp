"""Data Pool 데이터 유일성(한 사실은 한 곳에만) 점검 — 읽기 전용.   py scripts/data_consistency.py --pool-dir data/private/pool_csv_c"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("RENT3_DEMO_SEED", "0")

ap = argparse.ArgumentParser()
ap.add_argument("--pool-dir", required=True)
a = ap.parse_args()
from apps.rent_dist._rent3_core import consistency, use_pool_dir

use_pool_dir(os.path.abspath(a.pool_dir))
r = consistency.scan()
print(f"풀: {a.pool_dir}\n점검 {len(r['checks'])}개 중 문제 있는 항목 {r['problem_checks']}개 · 어긋난 건 {r['problems']}건\n")
for c in r["checks"]:
    mark = "✅" if c["count"] == 0 else "⚠️ "
    print(f"{mark} [{c['kind']}] {c['title']}  — {c['count']}건 / 점검 {c['checked']}   ({c['where']})")
    for e in c["examples"][:5]:
        print("      ", e)
