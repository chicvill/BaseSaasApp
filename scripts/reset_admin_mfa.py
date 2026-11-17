"""관리인 2단계 인증 해제(폰·복구 코드를 모두 잃은 경우). 서버에서만 실행한다.

  py scripts/reset_admin_mfa.py --pool-dir data/rent3_pool --username admin
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", required=True)
    ap.add_argument("--username", required=True)
    a = ap.parse_args()
    from apps.rent_dist._rent3_core import mfa, use_pool_dir
    use_pool_dir(os.path.abspath(a.pool_dir))
    adm = mfa.admin_by_username(a.username)
    if adm is None:
        print(f"관리인 '{a.username}' 를 찾을 수 없습니다.")
        return 1
    was = adm.totp_enabled
    mfa.reset(adm, "admin_mfa_reset_by_script")
    print(f"'{a.username}' 의 2단계 인증을 해제했습니다(이전 상태: {'켜짐' if was else '꺼짐'}). 로그인 후 다시 설정하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
