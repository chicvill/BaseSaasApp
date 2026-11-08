"""
scripts/encrypt_fields.py — 저장된 Data Pool 의 민감 필드(도어락·공동현관 비밀번호·계좌번호)를 현재 RENT3_CRYPTO_KEY 로 (다시) 암호화한다.

- 처음 켤 때: 키를 설정한 뒤 1회 실행하면 평문으로 남은 값이 모두 암호화된다.
- 키를 교체할 때: 새 키는 RENT3_CRYPTO_KEY, 옛 키는 RENT3_CRYPTO_KEY_PREVIOUS 에 두고 실행하면 새 키로 다시 저장된다.
- 여러 번 실행해도 안전하다. 실행 전에 풀 폴더를 백업할 것.

사용:  py scripts/encrypt_fields.py [--pool-dir PATH]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apps.rent_dist._rent3_core import POOL_COLLECTIONS, ensure_pool_seeded, fieldcrypto, use_pool_dir  # noqa: E402
from apps.rent_dist._rent3_core.pool import POOL_REGISTRY  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", default="")
    args = ap.parse_args()
    if not fieldcrypto.enabled():
        sys.exit("RENT3_CRYPTO_KEY 환경변수가 없습니다. 키를 설정한 뒤 다시 실행하세요.")
    if args.pool_dir:
        use_pool_dir(args.pool_dir)
    ensure_pool_seeded()
    n = 0
    for cls in POOL_REGISTRY.values():
        if cls.collection not in POOL_COLLECTIONS:
            continue
        for obj in cls.all():
            obj.save(action="fields_encrypted", app_source="scripts.encrypt_fields")
            n += 1
    print(f"{n}개 객체를 현재 키로 다시 저장했습니다.")


if __name__ == "__main__":
    main()
