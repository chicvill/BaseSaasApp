"""
scripts/hash_passwords.py — Rent3 Data Pool 에 평문으로 저장된 비밀번호를 해시로 바꾼다 (1회 실행, 여러 번 실행해도 안전).

- 평문이지만 약한 값(1212, 0202 등 흔한 값): 해시하지 않고 **비운다**. 그대로 해시하면 약한 비밀번호가 영구히 유효해진다.
  비운 계정은 본인인증 기반 '비밀번호 재설정' 또는 관리자 임시 비밀번호로만 다시 쓸 수 있다.
  (로컬 시연은 공통 비밀번호가 따로 허용되므로 영향이 없다.)
- 평문이고 약하지 않은 값: pbkdf2 해시로 바꾼다.
- 이미 해시인 값: 그대로 둔다.

사용:  py scripts/hash_passwords.py [--pool-dir PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps.rent_dist._rent3_core import Admin, Lessor, Tenant, ensure_pool_seeded, use_pool_dir  # noqa: E402
from apps.rent_dist._rent3_core import passwords as pwd  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.pool_dir:
        use_pool_dir(args.pool_dir)
    ensure_pool_seeded()

    hashed = cleared = kept = 0
    for cls in (Admin, Lessor, Tenant):
        for obj in cls.all():
            pw = getattr(obj, "password", "") or ""
            if not pw or pwd.is_hashed(pw):
                kept += 1
                continue
            if pw.lower() in pwd.WEAK:
                new, cleared = "", cleared + 1
            else:
                new, hashed = pwd.hash_password(pw), hashed + 1
            if not args.dry_run:
                obj.password = new
                obj.save(action="password_migrated", app_source="scripts.hash_passwords")
    print(f"해시로 전환 {hashed} · 약한 비밀번호 비움 {cleared} · 변경 없음 {kept}" + (" (dry-run: 저장 안 함)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
