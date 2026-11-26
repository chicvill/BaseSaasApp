"""
apps/rent_dist/_rent3_core/billing.py — 스마트 은행문자 파서, FIFO 수납 엔진, 세무/증명서 서비스 파사드 (`domains/billing_*.py` 통합 재노출).
"""
from __future__ import annotations

from .domains.billing_fifo import FifoPaymentEngine, SmsDepositParser, _names_of
from .domains.billing_invoices import TaxInvoiceAndCertMixin
from .models import validate_email_format, validate_kr_business_number


class TaxAndCertificateService(TaxInvoiceAndCertMixin):
    """공용 운영비 NOI 결산, 5월 종소세·2월 사업장현황신고·부가세 홈택스 CSV, 세입자 월세 세액공제 납부확인서 생성기."""


__all__ = [
    "_names_of",
    "SmsDepositParser",
    "FifoPaymentEngine",
    "TaxAndCertificateService",
    "validate_kr_business_number",
    "validate_email_format",
]
