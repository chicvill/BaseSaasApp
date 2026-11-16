"""
scripts/deploy_check.py — 배포 서버(또는 로컬)의 Rent3 배포 전 점검. 서버 터미널에서 실행한다.

    py scripts/deploy_check.py            # 현재 환경변수·데이터 폴더를 점검
    py scripts/deploy_check.py --strict   # 경고(WARN)도 실패로 취급

점검 항목: 필수 환경변수, 시연용 설정이 꺼져 있는지, 운영 데이터 폴더(영구 볼륨)·암호화·비밀번호 해시 상태, 본인인증·문자 발송 설정.
비밀 값은 출력하지 않는다(설정 여부만). 실패(FAIL)가 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []  # (level, name, detail)


def add(level: str, name: str, detail: str = "") -> None:
    results.append((level, name, detail))


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def check_env() -> None:
    if env("BASS_SECRET"):
        add("OK", "BASS_SECRET", "토큰 서명 키 설정됨")
    else:
        add("FAIL", "BASS_SECRET", "없으면 서버를 다시 켤 때마다 모든 로그인이 풀리고, 서버가 여러 대면 서로 토큰을 인정하지 못합니다")
    if env("BASS_SEED_PASSWORD"):
        add("OK", "BASS_SEED_PASSWORD", "관리자 비밀번호 지정됨")
    else:
        add("WARN", "BASS_SEED_PASSWORD", "미지정이면 무작위 비밀번호가 서버 로그에 한 번만 찍힙니다(놓치면 관리자 로그인 불가)")
    if env("RENT3_DEMO_LOGIN") in ("0", "false", "no"):
        add("OK", "RENT3_DEMO_LOGIN", "공통 데모 비밀번호 차단")
    elif env("RENT3_DEMO_LOGIN") in ("1", "true", "yes"):
        add("FAIL", "RENT3_DEMO_LOGIN", "1 이면 누구나 1212 로 로그인할 수 있습니다. 0 으로 바꾸세요")
    else:
        add("FAIL", "RENT3_DEMO_LOGIN", "0 으로 명시하세요(미지정이면 접속 위치로 판정하는데 프록시 설정에 따라 흐려질 수 있음)")
    if env("RENT3_FAKE_IDENTITY") in ("1", "true", "yes"):
        add("FAIL", "RENT3_FAKE_IDENTITY", "가짜 본인인증이 켜져 있습니다(인증번호 000000). 끄세요")
    else:
        add("OK", "RENT3_FAKE_IDENTITY", "가짜 본인인증 꺼짐")
    for d in ("RENT3_TODAY", "RENT2_TODAY"):
        if env(d):
            add("FAIL", d, "오늘 날짜를 고정하는 시험용 변수입니다. 운영에서는 지우세요(연체·만기 계산이 틀어집니다)")
    if env("MASTER_CRYPTO_KEY") or env("RENT3_CRYPTO_KEY"):
        add("OK", "암호화 키", "도어락·계좌 암호화 키 설정됨 (" + ("RENT3_CRYPTO_KEY" if env("RENT3_CRYPTO_KEY") else "MASTER_CRYPTO_KEY") + ")")
    else:
        add("FAIL", "암호화 키", "MASTER_CRYPTO_KEY 또는 RENT3_CRYPTO_KEY 가 없어 도어락·계좌번호가 평문으로 저장됩니다")
    if env("GEMINI_API_KEY"):
        add("OK", "GEMINI_API_KEY", "등기부 AI 읽기 가능")
    else:
        add("WARN", "GEMINI_API_KEY", "없으면 등기부 AI 읽기가 꺼지고 직접 입력만 가능합니다(오류는 아님)")


def check_identity() -> None:
    prov = env("RENT3_IDENTITY_PROVIDER").lower()
    if prov == "sms":
        missing = [k for k in ("RENT3_SMS_SENDER", "RENT3_SMS_API_KEY", "RENT3_SMS_API_SECRET", "RENT3_SMS_FROM") if not env(k)]
        if missing:
            add("FAIL", "문자 인증", "RENT3_IDENTITY_PROVIDER=sms 인데 설정이 없습니다: " + ", ".join(missing))
        else:
            add("WARN", "문자 인증", "설정됨 — 단 발송 코드는 실서버로 시험된 적이 없습니다. 본인 번호로 먼저 시험하세요. 문자 인증은 휴대폰 소유만 확인합니다(실명 미확인)")
    elif prov:
        add("FAIL", "RENT3_IDENTITY_PROVIDER", f"알 수 없는 제공자 '{prov}'")
    else:
        add("FAIL", "본인인증", "RENT3_IDENTITY_PROVIDER 가 없어 가맹 신청·임차인 계약이 막힙니다(배포 환경은 가짜 인증을 쓸 수 없음)")
    hops = env("RENT3_TRUSTED_PROXY_HOPS")
    if hops.isdigit() and int(hops) >= 1:
        add("OK", "RENT3_TRUSTED_PROXY_HOPS", f"로그인 제한이 프록시 {hops}단 뒤의 실제 접속 주소를 씁니다")
    else:
        add("WARN", "RENT3_TRUSTED_PROXY_HOPS", "프록시(Traefik) 뒤라면 1 로 설정하세요. 없으면 모든 사용자가 프록시 주소 하나로 보여 30회 실패 시 전원이 잠깁니다")


def check_data() -> None:
    from apps.rent_dist._rent3_core import fieldcrypto, passwords
    from apps.rent_dist._rent3_core.pool import DEFAULT_POOL_DIR

    pool_dir = os.path.abspath(env("RENT3_POOL_DIR") or DEFAULT_POOL_DIR)
    if not env("RENT3_POOL_DIR"):
        add("FAIL", "RENT3_POOL_DIR", "미지정 — 데이터가 컨테이너 안에 쌓여 재배포하면 사라집니다. 영구 볼륨 경로로 지정하세요")
    elif pool_dir.startswith(ROOT + os.sep) and not env("RENT3_ALLOW_POOL_IN_APP"):
        add("WARN", "RENT3_POOL_DIR", f"{pool_dir} 는 앱 폴더 안입니다. 이 경로가 영구 볼륨으로 마운트돼 있는지 Coolify 에서 확인하세요")
    else:
        add("OK", "RENT3_POOL_DIR", f"{pool_dir}")
    if not os.path.isdir(pool_dir):
        add("WARN", "데이터 폴더", "아직 없음(첫 기동 때 만들어집니다)")
        return
    try:
        probe = os.path.join(pool_dir, ".write_test")
        with open(probe, "w") as fp:
            fp.write("ok")
        os.remove(probe)
        add("OK", "쓰기 권한", "데이터 폴더에 쓸 수 있음")
    except OSError as exc:
        add("FAIL", "쓰기 권한", f"데이터 폴더에 쓸 수 없습니다: {exc}")
        return

    weak = plain = unenc = 0
    for f in glob.glob(os.path.join(pool_dir, "*.json")) + glob.glob(os.path.join(pool_dir, "rooms", "*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                doc = json.load(fp)
        except (OSError, ValueError):
            continue
        items = doc.get("items") if isinstance(doc, dict) and "items" in doc else [doc.get("data", doc)] if isinstance(doc, dict) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            pw = it.get("password")
            if isinstance(pw, str) and pw:
                if not passwords.is_hashed(pw):
                    if pw.lower() in passwords.WEAK:
                        weak += 1
                    else:
                        plain += 1
            for k in fieldcrypto.SENSITIVE_FIELDS:
                v = it.get(k)
                if isinstance(v, str) and v and not fieldcrypto.is_encrypted(v):
                    unenc += 1
    if weak or plain:
        add("FAIL", "평문 비밀번호", f"약한 값 {weak}건 · 평문 {plain}건 — py scripts/hash_passwords.py --pool-dir {pool_dir}")
    else:
        add("OK", "비밀번호 저장", "모두 해시이거나 비어 있음")
    if unenc and fieldcrypto.enabled():
        add("FAIL", "암호화되지 않은 민감 필드", f"{unenc}건 — py scripts/encrypt_fields.py --pool-dir {pool_dir} (먼저 폴더 백업)")
    elif unenc:
        add("WARN", "민감 필드", f"{unenc}건이 평문입니다(암호화 키가 없어 암호화되지 않음)")
    else:
        add("OK", "민감 필드", "도어락·계좌 값이 모두 암호화되었거나 비어 있음")
    if os.path.isdir(os.path.join(pool_dir, "uploads")):
        add("OK", "uploads", "등기부·점검 사진은 데이터 폴더 안(uploads/)에 저장되어 같은 볼륨으로 보존됩니다")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="WARN 도 실패로 취급")
    args = ap.parse_args()
    check_env()
    check_identity()
    check_data()
    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    for level, name, detail in results:
        print(f"{icon[level]} [{level:4}] {name}" + (f" — {detail}" if detail else ""))
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    print(f"\n결과: 실패 {fails} · 경고 {warns} · 정상 {len(results) - fails - warns}")
    print("이 점검이 확인하지 못하는 것: 프록시 보안 헤더(CSP·HSTS)·CORS, 영구 볼륨의 실제 마운트, 백업, 문자 실발송, 가맹 심사 운영 절차")
    return 1 if fails or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
