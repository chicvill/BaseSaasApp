"""
scripts/admin_link.py — 관리인 설정 링크 발급 (서버 콘솔 전용).

임대인이 관리인에게서 받은 계약 링크와 임시 비밀번호로 처음 들어와 비밀번호를 바꾸듯,
관리인도 서버에서 발급한 설정 링크로 임시 비밀번호를 확인하고 첫 로그인 때 바꾼다.
처음 시작할 때, 그리고 관리인 비밀번호를 잊었을 때 쓴다. 이전 링크와 비밀번호는 즉시 막힌다.

    py -3.14 scripts/admin_link.py                                  # 단독 실행 (http://localhost:9070)
    py -3.14 scripts/admin_link.py --base http://localhost:8080/rent4/   # 통합 게이트웨이
"""
import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.auth import ensure_default_admins
from core.kernel import use_store
from apps.rent4.backend import accounts


def main():
    parser = argparse.ArgumentParser(description="Rent4 관리인 설정 링크 발급")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--base", default="http://localhost:9070/", help="rent4 화면 주소 (끝에 / 포함)")
    parser.add_argument("--db", default=os.path.join(ROOT, "data", "platform.db"))
    args = parser.parse_args()

    use_store(args.db)
    ensure_default_admins()
    _, token = accounts.issue_admin_link(args.user)
    base = args.base if args.base.endswith("/") else args.base + "/"
    print(f"'{args.user}' 관리인 설정 링크 (이전 링크·비밀번호는 막혔습니다):")
    print(f"  {base}setup.html?t={token}")
    print("링크를 열어 임시 비밀번호를 확인하고 로그인한 뒤, 새 비밀번호로 바꾸세요.")


if __name__ == "__main__":
    main()
