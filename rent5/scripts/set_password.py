"""
rent5/scripts/set_password.py — 운영자가 서버에서 회원 비밀번호를 다시 정해 준다.

    py rent5/scripts/set_password.py <아이디> [--data-dir 폴더]

새 비밀번호는 화면에 보이지 않게 두 번 입력받는다. 바꾸면 그 회원이 이미 로그인해 둔 기기는 모두 로그아웃된다.
이메일 · 문자로 보내는 재설정은 일부러 두지 않았다 (서버 주인만 할 수 있는 가장 단순한 절차).
"""
import argparse
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))     # 저장소 루트 → rent5 패키지

from rent5.security import ApiError, hash_password, validate_password  # noqa: E402
from rent5.store import Store  # noqa: E402


def reset(data_dir: str, username: str, new_password: str) -> None:
    store = Store(data_dir)
    user = store.first("users", username=username.strip().lower())
    if not user:
        raise ApiError(404, f"'{username}' 회원이 없습니다.")
    validate_password(new_password, user["username"])
    salt, hashed = hash_password(new_password)
    store.update("users", user["id"], salt=salt, pw_hash=hashed, pw_ver=user.get("pw_ver", 1) + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rent5 회원 비밀번호 재설정 (운영자용)")
    parser.add_argument("username")
    parser.add_argument("--data-dir", default=os.getenv("RENT5_DATA_DIR") or os.path.join(HERE, "..", "data"))
    args = parser.parse_args()
    first = getpass.getpass("새 비밀번호: ")
    if first != getpass.getpass("한 번 더: "):
        print("두 번 입력한 비밀번호가 다릅니다.")
        return 1
    try:
        reset(args.data_dir, args.username, first)
    except ApiError as err:
        print(err.message, err.hint)
        return 1
    print(f"{args.username} 비밀번호를 바꿨습니다. 이미 로그인해 있던 기기는 모두 로그아웃됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
