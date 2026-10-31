"""
scripts/rent4_import_excel.py — 연간 수납 Excel 표(TSV)를 Rent4 Knowledge Pool 로 가져온다.

Excel 에서 표 전체를 복사해 .tsv(탭 구분) 파일로 저장한 뒤, 오래된 연도부터 순서대로 넣는다:
    py -3.14 scripts/rent4_import_excel.py 2025.tsv 2026.tsv [--dry-run] [--building 시크빌] [--branch main]
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from apps.rent4.backend import excel_import
from apps.rent4.backend.pool import pool


def main():
    parser = argparse.ArgumentParser(description="Rent4 Excel 수납부 가져오기")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--building", default="시크빌")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true", help="분석 결과만 출력하고 저장하지 않음")
    args = parser.parse_args()

    print(f"Knowledge Pool: {pool().directory}")
    for path in args.files:
        with open(path, encoding="utf-8-sig") as f:
            result = excel_import.parse_sheet(f.read(), building=args.building, branch=args.branch)
        report = result.summary() if args.dry_run else excel_import.apply_sheet(result, args.branch)
        ok = sum(1 for t in report["totals_check"] if t["ok"])
        print(f"\n== {os.path.basename(path)}: {report['year']}년 {len(report['months'])}개월, "
              f"호실 {report['rooms']}, 수납 {report['payments']}건 (확인 필요 {report['flagged']}건), "
              f"합계 대조 {ok}/{len(report['totals_check'])} 일치")
        for w in report["warnings"]:
            print(f"  ⚠️ {w}")
        for p in result.payments:
            if p.flags:
                print(f"  🔎 {p.room}호 {p.ym}: {'; '.join(p.flags)}")
        if args.dry_run:
            print(json.dumps(report["totals_check"][:2], ensure_ascii=False))


if __name__ == "__main__":
    main()
