"""apps/rent3/routers/tax_expenses.py — 세무·수익률 분석, 공용비 실결산, 5월 종소세 명세서 라우터.

하위 호환성 유지: 모든 URL 경로는 기존 `/api/rent3/...` 스펙을 100% 보존합니다.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Body, Response
from pydantic import BaseModel, Field

from ..domain import (
    calculate_branch_tax_roi, calculate_portfolio_tax_roi,
    RentPaymentCertificate, generate_rent_certificate,
    BuildingOperatingExpense, calculate_monthly_operating_summary,
    generate_annual_tax_statement, generate_tax_statement_csv,
    ChatMessage
)

router = APIRouter(tags=["rent3_tax_expenses"])

# 16. 지점별(시크빌/선호아트빌/신비주택) 통합 세무·수익률 분석 API
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/tax-roi")
def get_tax_roi_dashboard(tax_mode: str = Query("SEPARATE_14", description="SEPARATE_14 (14% 분리과세) | COMPREHENSIVE (종합과세)")):
    """3대 지점 통합 세무(부가세 면세/임대소득세/종부세) 및 수익률(Cap Rate/ROI) 대시보드 지표."""
    try:
        report = calculate_portfolio_tax_roi(tax_mode=tax_mode)
        return {
            "success": True,
            "data": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"세무·수익률 분석 산출 오류: {str(e)}")


@router.get("/analytics/tax-roi/{branch_id}")
def get_branch_tax_roi(branch_id: str, tax_mode: str = Query("SEPARATE_14")):
    """특정 지점 단일 세무·수익률 분석 보고서."""
    if branch_id not in ("main", "sunho", "sinbi"):
        raise HTTPException(status_code=400, detail=f"지원되지 않는 지점입니다: {branch_id}")
    try:
        report = calculate_branch_tax_roi(branch_id=branch_id, tax_mode=tax_mode)
        return {
            "success": True,
            "data": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"지점 세무·수익률 분석 오류: {str(e)}")


# ─────────────────────────────────────────────────────────────

# 17. 세입자 연말정산 월세 세액공제용 납입 증명서 & 현금영수증 API
# ─────────────────────────────────────────────────────────────

@router.get("/certificate/rent-tax/{branch}/{room_id}")
def get_rent_tax_certificate(branch: str, room_id: str, year: int = Query(2026)):
    """연말정산용 월세 납입 증명서 및 국세청 현금영수증 내역 발급."""
    try:
        cert = generate_rent_certificate(branch=branch, room_id=room_id, year=year)
        return {
            "success": True,
            "certificate": cert.model_dump()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"월세 납입 증명서 발급 실패: {str(e)}")


@router.post("/certificate/send-chat/{branch}/{room_id}")
def send_certificate_to_chat(branch: str, room_id: str, year: int = Query(2026)):
    """월세 납입 증명서 발급 완료 통보를 1:1 직통 채팅방에 전송."""
    cert = generate_rent_certificate(branch=branch, room_id=room_id, year=year)

    chat = ChatMessage(
        branch=branch,
        room_id=room_id,
        sender_role="landlord",
        sender_name="임대인(관제센터)",
        message=(
            f"📑 [연말정산 월세 세액공제 공식 납입 증명서 발급 완료]\n"
            f"• 증명서 번호: {cert.cert_no}\n"
            f"• 대상 과세연도: {cert.year}년 (총 {len(cert.items)}개월분)\n"
            f"• 연간 납입 총액: {cert.total_paid_amount:,}원\n"
            f"• 예상 세액환급액: 약 {cert.estimated_tax_refund:,}원 (17% 세액공제 기준)\n"
            f"• 법적 근거: 조세특례제한법 제95조의2\n"
            f"세입자 라운지에서 '월세 납입 증명서'를 원클릭으로 열람 및 PDF/인쇄하실 수 있습니다."
        )
    )
    chat.save("bot_sent_rent_certificate")

    return {
        "success": True,
        "message": f"{room_id}호 임차인 대화방에 연말정산 증명서 발급 안내가 전송되었습니다.",
        "cert_no": cert.cert_no
    }


# ─────────────────────────────────────────────────────────────

# 15. 건물 공용 운영비 실지출 관리 & 월별 실결산 (NOI) APIs
# ─────────────────────────────────────────────────────────────
class ExpenseCreateReq(BaseModel):
    branch: str = "main"
    year: int = 2026
    month: int = 10
    category: str = "cleaning"
    title: str
    amount: int
    vendor_name: str = ""
    vendor_biz_no: str = ""
    payment_date: str = ""
    payment_method: str = "TRANSFER"
    receipt_type: str = "TAX_INVOICE"
    receipt_photo_url: str = ""
    notes: str = ""
    is_tax_deductible: bool = True


def _seed_operating_expenses_if_empty():
    """지점별 현실적인 공용 운영비 초기 시드 데이터 생성."""
    if len(BuildingOperatingExpense.all()) > 0:
        return

    seeds = [
        # main (시크빌 본점 29실)
        ("main", 2026, 10, "cleaning", "10월 계단 및 공용구역 정기 청소비", 150000, "클린토탈", "120-86-12345", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 2회 계단/복도/주차장 물청소"),
        ("main", 2026, 10, "elevator", "10월 승강기 정기 점검 및 유지보수료", 180000, "현대엘리베이터(주)", "107-81-22440", "2026-10-12", "TRANSFER", "TAX_INVOICE", "월 1회 정기점검 완료(양호)"),
        ("main", 2026, 10, "utility_common", "10월 공용 전기요금 (계단등·가압펌프·CCTV)", 95000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "지로 자동납부"),
        ("main", 2026, 10, "utility_common", "10월 공용 수도요금 (청소 및 계단용)", 42000, "서울시상수도사업본부", "104-83-00010", "2026-10-20", "GIRO", "GIRO_RECEIPT", "서울시 전자납부"),
        ("main", 2026, 10, "safety_fire", "10월 소방 및 전기안전관리대행료", 90000, "제일안전기술단", "214-85-33011", "2026-10-15", "TRANSFER", "TAX_INVOICE", "월간 소방점검보고서 첨부"),
        ("main", 2026, 10, "insurance", "10월 건물 화재보험료 및 영업배상책임", 60000, "DB손해보험(주)", "118-81-01234", "2026-10-05", "CARD", "CARD_SLIP", "12개월 분납"),
        ("main", 2026, 10, "internet_tv", "10월 건물 단체 인터넷 및 IPTV 전용선", 220000, "(주)케이티", "102-81-42945", "2026-10-25", "TRANSFER", "TAX_INVOICE", "29개 전 호실 초고속 인터넷 단체선"),
        ("main", 2026, 10, "repair_maint", "3층 복도 센서등 LED 교체 및 배선 보수", 35000, "역삼조명철물", "211-12-89012", "2026-10-22", "CARD", "CARD_SLIP", "자재 실비 교체"),

        # sunho (선호아트빌 15실)
        ("sunho", 2026, 10, "cleaning", "10월 공용구역 정기 청소비", 120000, "관악청소마을", "209-11-45678", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 1회 정기 청소"),
        ("sunho", 2026, 10, "safety_fire", "10월 소방안전관리대행료", 70000, "한결소방엔지니어링", "119-86-55443", "2026-10-15", "TRANSFER", "TAX_INVOICE", "소방시설 점검"),
        ("sunho", 2026, 10, "utility_common", "10월 공용 전기요금", 55000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "공용 전기"),
        ("sunho", 2026, 10, "internet_tv", "10월 건물 단체 인터넷 전용선", 140000, "LG유플러스", "220-81-39938", "2026-10-25", "TRANSFER", "TAX_INVOICE", "15실 단체선"),

        # sinbi (신비주택 20실)
        ("sinbi", 2026, 10, "cleaning", "10월 계단 청소비 및 쓰레기 분리수거 관리", 140000, "당산클린", "107-14-33211", "2026-10-10", "TRANSFER", "TAX_INVOICE", "주 2회 수거 및 계단 청소"),
        ("sinbi", 2026, 10, "utility_common", "10월 공용 전기요금", 75000, "한국전력공사", "116-81-19030", "2026-10-18", "GIRO", "GIRO_RECEIPT", "공용 조명/인터폰"),
        ("sinbi", 2026, 10, "internet_tv", "10월 건물 단체 인터넷선", 180000, "SK브로드밴드", "214-81-37722", "2026-10-25", "TRANSFER", "TAX_INVOICE", "20실 단체선"),
    ]

    for b, y, m, cat, title, amt, vname, vbiz, pdate, pmethod, rtype, note in seeds:
        exp = BuildingOperatingExpense(
            branch=b,
            year=y,
            month=m,
            category=cat,
            title=title,
            amount=amt,
            vendor_name=vname,
            vendor_biz_no=vbiz,
            payment_date=pdate,
            payment_method=pmethod,
            receipt_type=rtype,
            notes=note,
            is_tax_deductible=True
        )
        exp.save(f"seed_expense_{b}_{cat}")


@router.get("/expenses/list")
def get_expenses_list(branch: str = Query("main"), year: int = Query(2026), month: int = Query(10)):
    """지점별, 연월별 공용 운영비 지출 대장 목록 조회."""
    _seed_operating_expenses_if_empty()

    all_expenses = BuildingOperatingExpense.all()
    filtered = [
        e for e in all_expenses
        if getattr(e, "branch", "main") == branch
        and getattr(e, "year", 2026) == year
        and getattr(e, "month", 10) == month
    ]
    # 지출일자 내림차순 정렬
    filtered.sort(key=lambda x: getattr(x, "payment_date", "") or "", reverse=True)

    total_amount = sum(getattr(e, "amount", 0) for e in filtered)
    deductible_amount = sum(getattr(e, "amount", 0) for e in filtered if getattr(e, "is_tax_deductible", True))

    items = []
    for e in filtered:
        cat = getattr(e, "category", "other")
        items.append({
            **e.model_dump(),
            "category_label": BuildingOperatingExpense.get_category_label(cat)
        })

    return {
        "success": True,
        "branch": branch,
        "year": year,
        "month": month,
        "total_count": len(items),
        "total_amount": total_amount,
        "deductible_amount": deductible_amount,
        "expenses": items
    }


@router.post("/expenses/create")
def create_operating_expense(req: ExpenseCreateReq):
    """신규 공용 운영비 지출 등록."""
    pdate = req.payment_date or datetime.now().strftime("%Y-%m-%d")
    exp = BuildingOperatingExpense(
        branch=req.branch,
        year=req.year,
        month=req.month,
        category=req.category,
        title=req.title,
        amount=req.amount,
        vendor_name=req.vendor_name,
        vendor_biz_no=req.vendor_biz_no,
        payment_date=pdate,
        payment_method=req.payment_method,
        receipt_type=req.receipt_type,
        receipt_photo_url=req.receipt_photo_url,
        notes=req.notes,
        is_tax_deductible=req.is_tax_deductible
    )
    exp.save("expense_created")
    return {
        "success": True,
        "message": f"[{exp.title}] {exp.amount:,}원 지출 등록이 완료되었습니다.",
        "expense_id": exp.id
    }


@router.delete("/expenses/{expense_id}")
def delete_operating_expense(expense_id: str):
    """공용 운영비 지출 삭제."""
    exp = BuildingOperatingExpense.get(expense_id)
    if not exp:
        raise HTTPException(status_code=404, detail="지출 항목을 찾을 수 없습니다.")
    exp.delete("expense_deleted")
    return {
        "success": True,
        "message": "지출 항목이 성공적으로 삭제되었습니다."
    }


@router.get("/expenses/monthly-summary")
def get_monthly_operating_summary(branch: str = Query("main"), year: int = Query(2026), month: int = Query(10)):
    """월별 임대차 총 수입 대 실운영비 결산서 (실제 순운영이익 NOI 및 경비율 도출)."""
    _seed_operating_expenses_if_empty()
    summary = calculate_monthly_operating_summary(branch, year, month)
    return {
        "success": True,
        "summary": summary
    }


# ─────────────────────────────────────────────────────────────

# 16. 임대인 5월 종합소득세 홈택스 제출용 연간 임대소득·필요경비 표준 전산 명세서 API
# ─────────────────────────────────────────────────────────────
@router.get("/tax/annual-statement")
def get_annual_tax_statement(branch: str = Query("main"), year: int = Query(2026)):
    """임대인 5월 종합소득세 확정신고용 연간 소득·필요경비 표준 전산 명세서 JSON 조회."""
    _seed_operating_expenses_if_empty()
    statement = generate_annual_tax_statement(branch, year)
    return {
        "success": True,
        "statement": statement
    }


@router.get("/tax/annual-statement/export-csv")
def export_annual_tax_statement_csv(branch: str = Query("main"), year: int = Query(2026)):
    """국세청 홈택스 제출 및 엑셀(MS Excel) 호환 UTF-8 BOM 인코딩 표준 CSV 명세서 다운로드."""
    _seed_operating_expenses_if_empty()
    statement = generate_annual_tax_statement(branch, year)
    csv_content = generate_tax_statement_csv(statement)
    filename = f"tax_statement_{year}_{branch}.csv"
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )



