"""
rent5/demo.py — 예제 계정 · 예제 매물 · 매물 올리기에서 고르는 예제 목록. 서버를 다시 시작하거나 다시 배포해도 없으면 다시 만든다.

    임대인  아이디 aaaa / 비밀번호 a123456 / 김임대 / 01033336666
    임차인  아이디 bbbb / 비밀번호 a123456 / 김임차 / 0101112222
    매물    아트빌 202호 · 월세 · 보증금 500만원 · 월세 50만원 (임대인 aaaa)

- 이미 있는 계정은 건드리지 않습니다 (비밀번호를 바꿨으면 바꾼 대로 둡니다).
- 예제 매물은 "열려 있거나 예약 중인 것"이 없을 때만 새로 만듭니다 (계약이 끝나면 다시 시작할 때 새 매물이 나옵니다).
- 비밀번호가 널리 알려진 값이므로 **실제 사용자에게 열기 전에는 `RENT5_DEMO=0` 으로 끄세요.**
"""
from __future__ import annotations

import os
from typing import Dict, List

from .security import hash_password
from .store import Store

DEMO_USERS: List[Dict[str, str]] = [
    {"username": "aaaa", "name": "김임대", "phone": "01033336666", "password": "a123456"},
    {"username": "bbbb", "name": "김임차", "phone": "0101112222", "password": "a123456"},
]
DEMO_LISTING = {
    "owner": "aaaa", "address": "아트빌", "room": "202호", "lease_type": "월세", "deposit": 5_000_000, "rent": 500_000, "mgmt": 0,
    "available_from": "2026-12-01", "description": "예제 매물입니다. 계약 신청 → 예약 → 계약방 흐름을 연습해 보세요.",
    "contact_phone": "01033336666", "status": "open", "reserved_by": "", "demo": True,
}


# 매물 올리기 화면에서 고르는 예제 (key 로 중복 없이 채운다). 첫 번째가 예제 매물(아트빌 202호)과 같다.
CATALOG: List[Dict] = [
    {"key": "artvill-202", "address": "아트빌", "room": "202호", "lease_type": "월세", "deposit": 5_000_000, "rent": 500_000, "mgmt": 0,
     "available_from": "2026-12-01", "description": "원룸, 남향, 풀옵션"},
    {"key": "chicvill-303", "address": "인천 서해구 시크빌", "room": "303호", "lease_type": "월세", "deposit": 10_000_000, "rent": 600_000, "mgmt": 50_000,
     "available_from": "2026-12-10", "description": "투룸, 주차 1대, 엘리베이터"},
    {"key": "seohae-101", "address": "서해 오션뷰", "room": "101호", "lease_type": "전세", "deposit": 80_000_000, "rent": 0, "mgmt": 70_000,
     "available_from": "2027-01-05", "description": "바다 조망, 신축"},
    {"key": "hanbit-1203", "address": "한빛아파트", "room": "1203호", "lease_type": "전세", "deposit": 150_000_000, "rent": 0, "mgmt": 120_000,
     "available_from": "2027-02-01", "description": "방 3개, 역세권"},
    {"key": "byeolbit-b01", "address": "별빛주택", "room": "B01호", "lease_type": "월세", "deposit": 3_000_000, "rent": 400_000, "mgmt": 30_000,
     "available_from": "2026-12-20", "description": "반지하 아님, 채광 좋음"},
]


def enabled() -> bool:
    return os.getenv("RENT5_DEMO", "1").strip().lower() not in ("0", "false", "no", "off")


def ensure_demo(store: Store) -> None:
    """없는 것만 채운다. 여러 번 불러도 같은 결과."""
    with store.lock:
        for u in DEMO_USERS:
            if store.first("users", username=u["username"]):
                continue
            salt, hashed = hash_password(u["password"])      # 회원가입 화면의 8자 규칙은 거치지 않는다 (예제 전용)
            store.insert("users", {"username": u["username"], "name": u["name"], "phone": u["phone"],
                                   "salt": salt, "pw_hash": hashed, "pw_ver": 1})
        for item in CATALOG:
            if not store.first("catalog", key=item["key"]):
                store.insert("catalog", dict(item))
        live = [l for l in store.find("listings", owner=DEMO_LISTING["owner"], demo=True) if l["status"] in ("open", "reserved")]
        if not live:
            first = store.first("catalog", key=CATALOG[0]["key"])
            store.insert("listings", dict(DEMO_LISTING, catalog_id=first["id"] if first else ""))   # 같은 예제를 중복으로 올리지 못하게 연결
