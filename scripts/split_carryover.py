"""이관 '이전 입금 합산(:carry)' 납부 건을 달별 건으로 분해 — 미리보기(기본)와 반영.

  py scripts/split_carryover.py --pool-dir data/private/pool_csv_c            # 미리보기: 풀 복사본에서 분해해 보고 전후를 비교만 한다(실제 풀은 바뀌지 않는다)
  py scripts/split_carryover.py --pool-dir <운영 풀> --apply                   # 반영: 복사본 검증이 모두 통과해야만, 자동 백업 뒤에 실제 풀에 적용한다

검증 기준: 계약별 입금 합계·미납 합계·선수금이 분해 전후에 1원도 다르지 않아야 한다(미납 '달'의 분포는 바뀔 수 있고 그 변화를 보여 준다).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("RENT3_DEMO_SEED", "0")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", required=True)
    ap.add_argument("--apply", action="store_true", help="검증 통과 시 실제 풀에 반영(자동 백업)")
    ap.add_argument("--json", default="", help="결과를 JSON 파일로도 저장")
    a = ap.parse_args()
    real = os.path.abspath(a.pool_dir)
    from apps.rent_dist._rent3_core import carryover_split as cs, use_pool_dir

    use_pool_dir(real)
    rows = cs.plan()
    ok_rows = [r for r in rows if r["status"] == "OK"]
    review = [r for r in rows if r["status"] != "OK"]
    before = cs.snapshot()
    print(f"풀: {real}")
    print(f"이월 합산 건 {len(rows)}개 — 분해 가능 {len(ok_rows)}개 · 검토 필요 {len(review)}개")
    for r in rows[:40]:
        mt = ", ".join(f"{m['ym']}:{m['amount']:,}" for m in r["months"][:4]) + (f" … 외 {len(r['months']) - 4}개월" if len(r["months"]) > 4 else "")
        print(f"  [{r['status']}] {r['branch']} {r['room_no']}호 {r['tenant']} 합계 {r['total']:,}원 → {len(r['months'])}개월 ({mt}) {r['note']}")

    tmp = tempfile.mkdtemp(prefix="split_carry_")
    try:
        copy = os.path.join(tmp, "pool")
        shutil.copytree(real, copy, ignore=shutil.ignore_patterns("_backups"))
        use_pool_dir(copy)
        res = cs.apply(cs.plan())
        after = cs.snapshot()
    finally:
        use_pool_dir(real)
        shutil.rmtree(tmp, ignore_errors=True)
    cmp = cs.compare(before, after)
    print(f"\n[복사본 검증] 분해 {res['split']}건 · 새 납부 건 {res['created']}개 · 건너뜀 {res['skipped']}건")
    print(f"계약별 입금·미납·선수금 합계 일치: {'✅ 모두 일치(1원도 다르지 않음)' if cmp['identical_totals'] else '❌ 불일치 ' + str(len(cmp['mismatches'])) + '건'}")
    for m in cmp["mismatches"][:20]:
        print("  ", m)
    print(f"미납 달 분포가 달라진 계약: {len(cmp['month_distribution_changed'])}개")
    for m in cmp["month_distribution_changed"][:15]:
        print(f"  {m['room_no']}호 {m['tenant']}: 전 {m['before'][:6]}{'…' if len(m['before']) > 6 else ''} → 후 {m['after'][:6]}{'…' if len(m['after']) > 6 else ''}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"plan": rows, "result": res, "compare": cmp}, f, ensure_ascii=False, indent=1)
    if not a.apply:
        print("\n(미리보기입니다. 실제 풀은 바뀌지 않았습니다. 반영하려면 --apply)")
        return 0 if cmp["identical_totals"] else 2
    if not cmp["identical_totals"]:
        print("\n❌ 합계가 일치하지 않아 반영하지 않습니다.")
        return 2
    from apps.rent_dist._rent3_core import backup as bk
    info = bk.create()
    print(f"\n백업: {info.get('name')}")
    use_pool_dir(real)
    res2 = cs.apply(cs.plan())
    cmp2 = cs.compare(before, cs.snapshot())
    print(f"반영: 분해 {res2['split']}건 · 새 납부 건 {res2['created']}개 · 사후 검증 {'✅ 일치' if cmp2['identical_totals'] else '❌ 불일치 — 백업으로 복구하세요'}")
    return 0 if cmp2["identical_totals"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
