"""
shared/core/vault.py
MQnet 시크릿 암호화 및 런타임 자동 복호화 모듈.
- 알고리즘: AES-256-GCM (표준 라이브러리/경량 fallback 지원)
- 목적: .env.shared의 민감한 DB/API 키를 .env.encrypted로 암호화하여 Git에 안전하게 포함.
- 런타임: 마스터 키로 메모리 상에서 복호화하여 os.environ에 자동 주입.
- 마스터 키는 저장소 밖에만 둔다: 서버 = 환경변수 MASTER_CRYPTO_KEY, 내 PC = 루트의 .env.key (git 제외).
  코드에 기본 키를 두지 않는다 — 키가 없으면 복호화 · 암호화를 하지 않고 경고만 남긴다.

CLI: python shared/core/vault.py [newkey | encrypt | sync | decrypt | hook]
    newkey   새 마스터 키를 .env.key 에 저장 (화면에 출력하지 않음, 있으면 --force 필요)
    encrypt  .env.shared → .env.encrypted
    decrypt  복호화가 되는지 확인 (키 이름만 출력, 값은 출력하지 않음)
"""
import os
import sys
import hmac
import base64
import hashlib
import logging
from typing import Optional

logger = logging.getLogger("mqnet.vault")

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KEY_FILE = os.path.join(_BASE_DIR, ".env.key")


def get_master_key() -> Optional[str]:
    """MASTER_CRYPTO_KEY 환경변수 → 루트 .env.key 파일 순. 없으면 None."""
    key = os.getenv("MASTER_CRYPTO_KEY", "").strip()
    if not key and os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
    return key or None


def _require_key(master_key: Optional[str]) -> str:
    key = master_key or get_master_key()
    if not key:
        raise ValueError("마스터 키가 없습니다 (MASTER_CRYPTO_KEY 또는 .env.key).")
    return key


def _get_derived_key(master_key: str) -> bytes:
    """마스터 키 문자열로부터 32바이트 AES 키 도출"""
    return hashlib.sha256(master_key.encode("utf-8")).digest()


def encrypt_text(plaintext: str, master_key: Optional[str] = None) -> str:
    """
    텍스트를 AES-GCM(또는 ChaCha20/HMAC-CTR)로 암호화하여 Base64 문자열 반환.
    cryptography 라이브러리가 있으면 AES-GCM 사용, 없으면 표준 라이브러리 HMAC-CTR 사용.
    """
    key = _get_derived_key(_require_key(master_key))
    raw_bytes = plaintext.encode("utf-8")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, raw_bytes, None)
        # format: b"GCM:" + nonce + ciphertext
        payload = b"GCM:" + nonce + ciphertext
        return base64.b64encode(payload).decode("ascii")
    except Exception:
        # Fallback: 표준 라이브러리(hashlib, hmac, os.urandom) 기반 CTR 스트림 암호화 + HMAC-SHA256 인증
        nonce = os.urandom(16)
        out = bytearray()
        block_idx = 0
        while len(out) < len(raw_bytes):
            counter_bytes = block_idx.to_bytes(4, "big")
            keystream_block = hashlib.sha256(key + nonce + counter_bytes).digest()
            for b in keystream_block:
                if len(out) < len(raw_bytes):
                    out.append(raw_bytes[len(out)] ^ b)
                else:
                    break
            block_idx += 1
        tag = hmac.new(key, nonce + bytes(out), hashlib.sha256).digest()
        payload = b"CTR:" + nonce + tag + bytes(out)
        return base64.b64encode(payload).decode("ascii")


def decrypt_text(encrypted_b64: str, master_key: Optional[str] = None) -> str:
    """Base64 암호화 문자열을 복호화하여 평문 텍스트 반환"""
    key = _get_derived_key(_require_key(master_key))
    try:
        payload = base64.b64decode(encrypted_b64.encode("ascii"))
    except Exception as e:
        raise ValueError(f"잘못된 Base64 데이터: {e}")

    if payload.startswith(b"GCM:"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        nonce = payload[4:16]
        ciphertext = payload[16:]
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

    elif payload.startswith(b"CTR:"):
        nonce = payload[4:20]
        tag = payload[20:52]
        ciphertext = payload[52:]
        expected_tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise ValueError("암호문 무결성 검증 실패 (HMAC 불일치 또는 마스터 키 오류)")

        out = bytearray()
        block_idx = 0
        while len(out) < len(ciphertext):
            counter_bytes = block_idx.to_bytes(4, "big")
            keystream_block = hashlib.sha256(key + nonce + counter_bytes).digest()
            for b in keystream_block:
                if len(out) < len(ciphertext):
                    out.append(ciphertext[len(out)] ^ b)
                else:
                    break
            block_idx += 1
        return bytes(out).decode("utf-8")
    else:
        raise ValueError("알 수 없는 암호화 포맷입니다.")


def load_encrypted_env(file_path: Optional[str] = None, master_key: Optional[str] = None) -> bool:
    """
    .env.encrypted 파일을 복호화하여 현재 프로세스의 os.environ에 주입합니다.
    기존에 이미 주입된 환경변수가 있다면 덮어쓰지 않습니다. 마스터 키가 없으면 읽지 않습니다.
    """
    if file_path is None:
        file_path = os.path.join(_BASE_DIR, ".env.encrypted")

    if not os.path.exists(file_path):
        return False

    key = master_key or get_master_key()
    if not key:
        logger.warning("⚠️ Vault: 마스터 키(MASTER_CRYPTO_KEY 또는 .env.key)가 없어 .env.encrypted 를 읽지 않습니다.")
        return False

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            encrypted_content = f.read().strip()

        decrypted_env = decrypt_text(encrypted_content, key)
        loaded_count = 0
        for line in decrypted_env.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                # 기존에 명시적으로 설정된 변수가 없으면 주입
                if k and not os.environ.get(k):
                    os.environ[k] = v
                    loaded_count += 1
        logger.info(f"🔒 Vault: .env.encrypted에서 {loaded_count}개의 환경변수를 성공적으로 복호화하여 로드했습니다.")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Vault: .env.encrypted 복호화 중 오류 발생 (마스터 키가 다르거나 파일이 손상됨): {type(e).__name__}")
        return False


def auto_sync_vault(base_dir: Optional[str] = None, force: bool = False) -> bool:
    """
    로컬 환경에서 .env.shared의 변경을 감지하여 .env.encrypted를 자동 갱신합니다.
    - .env.shared가 존재하고 .env.encrypted보다 최신 수정 시간이면 자동 암호화 실행 (마스터 키가 있을 때만).
    - Git 저장소인 경우 .git/hooks/pre-commit 훅도 자동 설치하여 커밋 시 누락을 원천 차단합니다.
    """
    if base_dir is None:
        base_dir = _BASE_DIR

    src_file = os.path.join(base_dir, ".env.shared")
    enc_file = os.path.join(base_dir, ".env.encrypted")

    if not os.path.exists(src_file):
        return False

    needs_sync = force or not os.path.exists(enc_file) or os.path.getmtime(src_file) > os.path.getmtime(enc_file)
    if not needs_sync:
        return False
    if not get_master_key():
        logger.warning("⚠️ Vault: 마스터 키가 없어 .env.shared 를 암호화하지 않았습니다 (python shared/core/vault.py newkey).")
        return False

    try:
        with open(src_file, "r", encoding="utf-8") as f:
            src_data = f.read()
        encrypted = encrypt_text(src_data)
        with open(enc_file, "w", encoding="utf-8") as f:
            f.write(encrypted)
        logger.info(f"🔄 Vault: .env.shared 변경 감지 -> .env.encrypted 자동 암호화 갱신 완료 ({len(encrypted)} bytes)")
        # Git 훅 자동 점검 및 설치
        ensure_git_hook(base_dir)
        return True
    except Exception as e:
        logger.warning(f"⚠️ Vault: 자동 암호화 동기화 중 오류: {e}")
        return False


def ensure_git_hook(base_dir: Optional[str] = None) -> bool:
    """Git pre-commit 훅을 확인하고 없으면 자동 설치합니다."""
    if base_dir is None:
        base_dir = _BASE_DIR

    hooks_dir = os.path.join(base_dir, ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        return False

    hook_file = os.path.join(hooks_dir, "pre-commit")
    hook_script = (
        "#!/bin/sh\n"
        "# MQnet Vault Pre-Commit Hook: .env.shared 변경 시 자동 암호화 및 스테이징\n"
        "python shared/core/vault.py sync\n"
        "if git status --porcelain | grep -q \"\\.env\\.encrypted\"; then\n"
        "    git add .env.encrypted\n"
        "    echo \"🔒 [Vault Hook] .env.shared 변경 감지 -> .env.encrypted가 자동 암호화되어 커밋에 포함되었습니다.\"\n"
        "fi\n"
    )

    if not os.path.exists(hook_file):
        try:
            with open(hook_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(hook_script)
            return True
        except Exception:
            return False
    return False


if __name__ == "__main__":
    src_file = os.path.join(_BASE_DIR, ".env.shared")
    enc_file = os.path.join(_BASE_DIR, ".env.encrypted")

    mode = sys.argv[1] if len(sys.argv) > 1 else "encrypt"

    if mode == "newkey":
        if os.path.exists(KEY_FILE) and "--force" not in sys.argv:
            print(f"[INFO] {KEY_FILE} 가 이미 있습니다. 바꾸려면 --force (그 뒤 .env.encrypted 를 다시 암호화해야 합니다).")
            sys.exit(1)
        import secrets
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(32))
        print(f"[SUCCESS] 새 마스터 키를 {KEY_FILE} 에 저장했습니다 (화면에 출력하지 않음).")
        print("[INFO] 서버(Coolify)에는 이 파일 내용을 환경변수 MASTER_CRYPTO_KEY 로 한 번 등록하세요.")
    elif mode == "decrypt":
        if not os.path.exists(enc_file):
            print(f"Error: {enc_file} does not exist.")
            sys.exit(1)
        with open(enc_file, "r", encoding="utf-8") as f:
            enc_data = f.read().strip()
        try:
            dec = decrypt_text(enc_data)
        except Exception as e:
            print(f"[ERROR] 복호화 실패 (마스터 키가 다르거나 없음): {type(e).__name__}")
            sys.exit(1)
        names = [ln.split("=", 1)[0].strip() for ln in dec.splitlines() if "=" in ln and not ln.strip().startswith("#")]
        print(f"[SUCCESS] 복호화 확인 — 키 {len(names)}개: {', '.join(names)} (값은 출력하지 않음)")
    elif mode == "sync":
        synced = auto_sync_vault(_BASE_DIR)
        if synced:
            print(f"[SUCCESS] Synced .env.shared -> {enc_file}")
        else:
            print("[INFO] .env.encrypted is already up to date (또는 마스터 키 없음).")
    elif mode == "hook":
        installed = ensure_git_hook(_BASE_DIR)
        if installed:
            print("[SUCCESS] Installed Git pre-commit vault hook.")
        else:
            print("[INFO] Git pre-commit hook is already present or not a git repo.")
    else:
        if not os.path.exists(src_file):
            print(f"Error: {src_file} does not exist.")
            sys.exit(1)
        if not get_master_key():
            print("Error: 마스터 키가 없습니다. 먼저 python shared/core/vault.py newkey")
            sys.exit(1)
        with open(src_file, "r", encoding="utf-8") as f:
            src_data = f.read()
        encrypted = encrypt_text(src_data)
        with open(enc_file, "w", encoding="utf-8") as f:
            f.write(encrypted)
        ensure_git_hook(_BASE_DIR)
        print(f"[SUCCESS] Encryption complete: {src_file} -> {enc_file}")
        print(f"[INFO] Ciphertext size: {len(encrypted)} bytes")
