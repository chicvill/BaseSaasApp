"""
apps/rent_dist/rent3/routers/_hub_deps.py — OpenRent 포털/Pool Hub 라우터 공통 헬퍼.
"""
from __future__ import annotations

import os
import re
from typing import List

from fastapi import Depends, Request

from core.auth import require_role
from apps.rent_dist._rent3_core import normalize_branch_id
from apps.rent_dist._rent3_core.authz import client_ip, is_local_request, not_revoked

# 원본 Data Pool(개인정보·계정 정보 포함) 열람은 본사 관리자만
ADMIN_ONLY = [Depends(not_revoked), Depends(require_role("superadmin", "admin"))]


def demo_login_allowed(request: Request) -> bool:
    """공통 데모 비밀번호(1212·0202·전화 뒷자리)는 로컬 개발에서만 허용한다.
    환경변수 RENT3_DEMO_LOGIN=1/0 으로 강제 지정할 수 있고, 지정이 없으면 서버 자신(loopback)에서 온 요청만 허용한다."""
    env = os.getenv("RENT3_DEMO_LOGIN", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    return is_local_request(request)


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _client_ip(request: Request) -> str:
    return client_ip(request)


def _is_loopback(request: Request) -> bool:
    return is_local_request(request)


def _build_contract_target_url(
    role_param: str,
    target_hint: str,
    branch: str = "",
    tenant_id: str = "",
) -> str:
    parts = (target_hint or "").split(":")
    q: List[str] = [f"role={role_param}"]
    if role_param == "lessor" and branch:
        q.append(f"branch={branch}")
    if role_param == "tenant" and tenant_id:
        q.append(f"tenant={tenant_id}")
    if len(parts) >= 3 and parts[1] and parts[2]:
        q.append(f"call_branch={normalize_branch_id(parts[1])}")
        q.append(f"call_room={parts[2].strip()}")
    return f"/rent3_contract/?{'&'.join(q)}"
