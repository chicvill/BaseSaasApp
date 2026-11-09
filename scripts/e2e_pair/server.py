"""
scripts/e2e_pair/server.py — 두 역할(임대인·임차인) 공동 계약 시험용 로컬 임시 서버.

    py scripts/e2e_pair/server.py              # 포트 9070, 시험 데이터는 e2e_runtime/pool (없으면 새로 시드)
    py scripts/e2e_pair/server.py --fresh      # 시험 데이터를 지우고 처음부터
    py scripts/e2e_pair/server.py --port 9071

이 서버는 이 PC 안에서만 접속되고(127.0.0.1), 시연용 공통 비밀번호(1212)와 가짜 본인인증(인증번호 000000)이 허용된다.
운영 데이터와 완전히 분리된 폴더를 쓰므로 시험이 끝나면 e2e_runtime/ 을 지우면 된다. 배포 서버에서는 쓰지 말 것.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

RUNTIME = os.environ.get("E2E_RUNTIME") or os.path.join(ROOT, "e2e_runtime")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9070)
    ap.add_argument("--fresh", action="store_true", help="시험 데이터를 지우고 새로 시작")
    args = ap.parse_args()

    # 운영 설정이 섞이지 않게: 시연 모드 변수는 로컬 판정에 맡기고, 날짜 고정 변수는 제거
    for k in ("RENT3_DEMO_LOGIN", "RENT3_FAKE_IDENTITY", "RENT3_IDENTITY_PROVIDER", "RENT3_TODAY", "RENT2_TODAY", "RENT3_POOL_DIR"):
        os.environ.pop(k, None)

    pool_dir = os.path.join(RUNTIME, "pool")
    if args.fresh and os.path.isdir(pool_dir):
        shutil.rmtree(pool_dir)
    os.makedirs(pool_dir, exist_ok=True)

    from apps.rent_dist._rent3_core import ensure_pool_seeded, use_pool_dir
    from core.gateway import create_gateway
    import uvicorn

    use_pool_dir(pool_dir)
    ensure_pool_seeded()
    app = create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:")
    with open(os.path.join(RUNTIME, "server.json"), "w", encoding="utf-8") as fp:
        json.dump({"base": f"http://127.0.0.1:{args.port}", "pool": pool_dir}, fp)
    print(f"[e2e] 시험 서버: http://127.0.0.1:{args.port}  (데이터: {pool_dir})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
