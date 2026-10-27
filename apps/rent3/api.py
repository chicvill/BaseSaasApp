"""apps/rent3/api.py — rent3 비대면 임대차 및 관리자 API 메인 라우터 (모듈화 완료).

BaseSaasApp 게이트웨이 표준 규격에 따라 마운트됩니다.
도메인별 세부 라우터:
- apps/rent3/routers/public_apps.py    : 공개 매물, 온라인 입주신청서, Co-Signing, 신원확인
- apps/rent3/routers/operations.py     : 임대인 관제, 채팅, 도어락, 주차, 수납/영수증, 퇴실정산, RTMS, 재계약, A/S 대장
- apps/rent3/routers/tax_expenses.py   : 세무 ROI, 연말정산 공식 증명서, 공용 운영비 실결산, 5월 종소세 명세서
- apps/rent3/routers/entities.py       : 8대 표준 엔티티 CRUD, O(1) 인덱스 검색, 16단계 라이프사이클 엔진
"""
from fastapi import APIRouter

from .routers.public_apps import (
    router as public_router,
    ApplicationCreateReq, SignAndPayReq, MaintenanceCreateReq,
    CheckInConfirmReq, ListingCreateReq, CoSignStartReq, CoSignStep1Req,
    CoSignStep2Req, CoSignStep3Req, CoSignSignReq, CoSignPayReq,
    PhoneVerifySendReq, PhoneVerifyConfirmReq, SocialVerifyReq, SignUpReq
)
from .routers.operations import (
    router as operations_router,
    ChatSendReq, ParkingRegisterReq, DoorLockUpdateReq, BillPayReq,
    CheckOutInspectReq, CheckOutAgreeReq, CheckOutCompleteReq,
    RenewalProposeReq, RenewalSignReq, TicketCreateReq, TicketAssignReq,
    TicketCompleteReq
)
from .routers.tax_expenses import (
    router as tax_expenses_router,
    ExpenseCreateReq
)
from .routers.entities import (
    router as entities_router
)

# 메인 라우터 생성 및 도메인 서브 라우터 통합 (기존 API 엔드포인트 100% 하위 호환성 유지)
router = APIRouter(tags=["rent3"])

router.include_router(public_router)
router.include_router(operations_router)
router.include_router(tax_expenses_router)
router.include_router(entities_router)

__all__ = [
    "router",
    "public_router",
    "operations_router",
    "tax_expenses_router",
    "entities_router",
    "ApplicationCreateReq",
    "SignAndPayReq",
    "MaintenanceCreateReq",
    "CheckInConfirmReq",
    "ListingCreateReq",
    "ChatSendReq",
    "CoSignStartReq",
    "CoSignStep1Req",
    "CoSignStep2Req",
    "CoSignStep3Req",
    "CoSignSignReq",
    "CoSignPayReq",
    "PhoneVerifySendReq",
    "PhoneVerifyConfirmReq",
    "SocialVerifyReq",
    "SignUpReq",
    "ParkingRegisterReq",
    "DoorLockUpdateReq",
    "BillPayReq",
    "CheckOutInspectReq",
    "CheckOutAgreeReq",
    "CheckOutCompleteReq",
    "RenewalProposeReq",
    "RenewalSignReq",
    "TicketCreateReq",
    "TicketAssignReq",
    "TicketCompleteReq",
    "ExpenseCreateReq",
]
