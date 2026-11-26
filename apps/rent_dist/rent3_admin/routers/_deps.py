"""
apps/rent_dist/rent3_admin/routers/_deps.py — 관리인 라우터 공통 상수·권한 가드·헬퍼.
"""
import re
from fastapi import Depends, Request

from core.auth import require_role
from core.errors import DomainError
from apps.rent_dist._rent3_core.authz import claims_from, need_claims, not_revoked

APP_SRC = "rent3_admin"
# 가맹 심사·정산은 본사 관리자만 (토큰 필요). 지점 관리자(branch_admin)는 자기 지점 가맹을 스스로 승인할 수 없다.
ADMIN_ONLY = [Depends(not_revoked), Depends(require_role("superadmin", "admin"))]


def _admin_label(request: Request) -> str:
    return str((claims_from(request) or {}).get("u") or "관리인")


def _admin_name(request: Request) -> str:
    return str(need_claims(request).get("u") or "admin")


def _require_lessor_proof(lsr) -> None:
    """임대인 링크의 본인 확인 4번째 칸(사업자번호·계좌번호 끝 4자리)에 쓸 증빙이 장부에 있어야 발급한다(없으면 건물명·이름·전화 3가지만으로 통과해 약하다)."""
    if not any(len(re.sub(r"\D", "", str(v or ""))) >= 4 for v in (lsr.business_number, lsr.account_number)):
        raise DomainError(
            f"{lsr.name} 임대인의 장부에 사업자번호·계좌번호가 없어 본인 확인 증빙이 없습니다.",
            hint="가맹 신청 정보에서 사업자번호 또는 계좌번호를 먼저 등록한 뒤 링크를 발급해 주세요.",
        )
