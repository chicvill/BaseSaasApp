"""
scripts/set_password.py — 플랫폼 계정 비밀번호를 서버 콘솔에서 직접 정한다 (관리인 계정 복구용).

시드 비밀번호는 코드에 없다 (BASS_SEED_PASSWORD 또는 첫 생성 때 로그에 한 번). 그 값을 잃어버렸으면:
    py -3.14 scripts/set_password.py admin
입력한 비밀번호는 화면에 보이지 않고, 해시로만 data/platform.db 에 저장된다.
"""
import argparse
import getpass
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.auth import User
from core.kernel import use_store


def main():
    parser = argparse.ArgumentParser(description="플랫폼 계정 비밀번호 설정")
    parser.add_argument("username")
    parser.add_argument("--db", default=os.path.join(ROOT, "data", "platform.db"))
    args = parser.parse_args()

    use_store(args.db)
    user = User.by_username(args.username)
    if user is None:
        sys.exit(f"'{args.username}' 계정이 없습니다. 서버를 한 번 실행하면 기본 계정이 만들어집니다.")
    first = getpass.getpass("새 비밀번호 (8자 이상): ")
    if len(first) < 8 or first != getpass.getpass("한 번 더: "):
        sys.exit("비밀번호가 8자 미만이거나 두 번 입력한 값이 다릅니다. 바꾸지 않았습니다.")
    user.set_password(first).save("password_set_by_console")
    print(f"'{user.username}' ({user.role}) 비밀번호를 바꿨습니다.")


if __name__ == "__main__":
    main()
