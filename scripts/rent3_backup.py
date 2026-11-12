"""
scripts/rent3_backup.py — Data Pool 백업·검증·복구 (서버에서 직접 실행).

  py scripts/rent3_backup.py --pool-dir <풀 폴더> --run                       # 지금 백업
  py scripts/rent3_backup.py --pool-dir <풀 폴더> --list                      # 백업 목록
  py scripts/rent3_backup.py --verify <백업.tar.gz>                           # 온전한지 확인
  py scripts/rent3_backup.py --restore <백업.tar.gz> --to <빈 폴더>           # 새 폴더에 복구(운영 풀 덮어쓰기 금지)

복구 검증 절차(분기별 1회 권장): --run → --verify → --restore 로 임시 폴더에 풀고, 그 폴더로 서버를 띄워 로그인·수납 내역이 보이는지 확인한다.
암호화 키(MASTER_CRYPTO_KEY / RENT3_CRYPTO_KEY)가 같아야 복구한 값을 읽을 수 있다.
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", default="")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", default="")
    ap.add_argument("--restore", default="")
    ap.add_argument("--to", default="")
    a = ap.parse_args()
    from apps.rent_dist._rent3_core import backup, use_pool_dir
    if a.pool_dir:
        use_pool_dir(os.path.abspath(a.pool_dir))
    if a.verify:
        r = backup.verify(a.verify)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    if a.restore:
        if not a.to:
            print("--to 로 복구할 빈 폴더를 지정하세요.")
            return 2
        print(json.dumps(backup.restore(a.restore, a.to), ensure_ascii=False))
        return 0
    if a.run:
        print(json.dumps(backup.create(), ensure_ascii=False))
        return 0
    if a.list:
        for it in backup.list_backups():
            print(it["name"], it["size"], it["created_at"])
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
