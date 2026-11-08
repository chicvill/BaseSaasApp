"""
apps/rent_dist/_rent3_core/fieldcrypto.py — 디스크에 저장되는 민감 필드(도어락·공동현관 비밀번호·계좌번호) 암호화.

- AES-256-GCM, 필드마다 새 nonce, 필드 이름을 AAD 로 묶어 다른 필드로 옮겨 붙인 값은 복호화되지 않는다.
- 키: 환경변수 RENT3_CRYPTO_KEY, 없으면 플랫폼이 이미 쓰는 서버 마스터 키 MASTER_CRYPTO_KEY(Coolify 에 .env 암호화용으로 설정된 값)를
  쓴다. 마스터 키는 용도 분리를 위해 라벨을 섞어 도출한 별도 키로 쓰므로 .env.encrypted 용 키와 같은 바이트가 아니다.
  (내 PC 의 .env.key 파일은 일부러 읽지 않는다 — 로컬 개발은 평문.) RENT3_CRYPTO_KEY 는 임의의 긴 문자열이면 되고
  (SHA-256 으로 32바이트 도출), 32바이트 base64 도 그대로 쓴다. 교체할 때는 새 키를 RENT3_CRYPTO_KEY, 옛 키를 RENT3_CRYPTO_KEY_PREVIOUS(쉼표로 여러 개)에
  두고 `scripts/encrypt_fields.py` 로 다시 저장한다.
- 키가 없으면(로컬 개발) 평문으로 저장하고, 이미 암호화된 값을 읽으려 하면 명확한 오류를 낸다.
- **키를 잃으면 암호화된 값은 복구할 수 없다** — Coolify 환경변수와 별도로 안전한 곳에 백업할 것.
- 화면·API 에서 값을 내려주는 규칙(누구에게 보이는가)은 그대로이며, 이 모듈은 '디스크에 남는 모습'만 보호한다.
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Any, Dict, List, Optional

from core.errors import DomainError

PREFIX = "enc:v1:"
SENSITIVE_FIELDS = ("doorlock_pwd", "common_entrance_pwd", "account_number", "lessor_account")


def _to_key(raw: str) -> bytes:
    raw = raw.strip()
    try:
        b = base64.b64decode(raw, validate=True)
        if len(b) == 32:
            return b
    except Exception:  # noqa: BLE001 — base64 가 아니면 문자열로 간주
        pass
    return hashlib.sha256(raw.encode()).digest()


def _master_derived() -> Optional[bytes]:
    m = os.getenv("MASTER_CRYPTO_KEY", "").strip()
    return hashlib.sha256(("rent3-fields-v1:" + m).encode()).digest() if m else None


def _keys() -> List[bytes]:
    """[0] = 새로 쓸 때의 키. 나머지는 읽기용(옛 키, 마스터 키 도출값)."""
    cur = os.getenv("RENT3_CRYPTO_KEY", "").strip()
    prev = [k for k in os.getenv("RENT3_CRYPTO_KEY_PREVIOUS", "").split(",") if k.strip()]
    keys = [_to_key(k) for k in ([cur] if cur else []) + prev]
    master = _master_derived()
    if master is not None:
        keys = keys + [master] if cur else [master] + keys
    return keys


def enabled() -> bool:
    return bool(os.getenv("RENT3_CRYPTO_KEY", "").strip() or os.getenv("MASTER_CRYPTO_KEY", "").strip())


def is_encrypted(v: Any) -> bool:
    return isinstance(v, str) and v.startswith(PREFIX)


def encrypt_value(field: str, value: str) -> str:
    if not enabled() or not isinstance(value, str) or not value or is_encrypted(value):
        return value
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(_keys()[0]).encrypt(nonce, value.encode(), field.encode())
    return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_value(field: str, value: str) -> str:
    if not is_encrypted(value):
        return value
    keys = _keys()
    if not keys:
        raise DomainError("암호화된 데이터를 읽을 수 없습니다.", hint="서버에 RENT3_CRYPTO_KEY 또는 MASTER_CRYPTO_KEY 환경변수가 필요합니다.", status_code=500)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = base64.urlsafe_b64decode(value[len(PREFIX):])
    nonce, ct = blob[:12], blob[12:]
    for k in keys:
        try:
            return AESGCM(k).decrypt(nonce, ct, field.encode()).decode()
        except Exception:  # noqa: BLE001 — 다른 키로 다시 시도
            continue
    raise DomainError("암호화된 데이터를 복호화하지 못했습니다.", hint="RENT3_CRYPTO_KEY(또는 _PREVIOUS)가 저장할 때의 키와 다릅니다.", status_code=500)


def seal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """저장 직전: 민감 필드 암호화."""
    if not enabled():
        return payload
    out = dict(payload)
    for f in SENSITIVE_FIELDS:
        if isinstance(out.get(f), str):
            out[f] = encrypt_value(f, out[f])
    return out


def unseal(raw: Dict[str, Any]) -> Dict[str, Any]:
    """읽은 직후: 민감 필드 복호화."""
    if not any(is_encrypted(raw.get(f)) for f in SENSITIVE_FIELDS):
        return raw
    out = dict(raw)
    for f in SENSITIVE_FIELDS:
        if is_encrypted(out.get(f)):
            out[f] = decrypt_value(f, out[f])
    return out
