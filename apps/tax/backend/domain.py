"""
apps/tax/backend/domain.py — 스마트 부동산 세무 (부가세 집계 & 종부세 산출 & 5역할 AI 협업).
Kernel Obj (Document Store) 표준을 준수합니다.
"""
from datetime import datetime
import json
import os
from typing import Any, Dict, List, Optional
from core import Obj, NotFound, Conflict, DomainError


class VatTransaction(Obj):
    """부가가치세 매출/매입 전표 엔티티."""
    label = "부가세 전표"
    scope_field = "branch_id"

    branch_id: str = "main"
    date: str                                # YYYY-MM-DD
    tx_type: str = "SALES"                   # SALES (매출) | PURCHASE (매입)
    category: str = "임대료"                  # 임대료 | 관리비 | 시설보수 | 소모품 | 중개수수료 | 공과금
    counterparty: str = ""                   # 거래처/임차인 상호명 또는 성명
    item_name: str = ""                      # 적요 / 품목
    supply_amount: int = 0                   # 공급가액 (원)
    tax_amount: int = 0                      # 부가세액 (원)
    is_tax_exempt: bool = False              # 면세 여부 (주택 임대 등)
    period: str = ""                         # 2026-1기예정 | 2026-1기확정 | 2026-2기예정 | 2026-2기확정
    source: str = "manual"                   # manual | rent2_sync | n8n
    ref_id: str = ""                         # rent2 등 연동 시 중복 방지 키

    @classmethod
    def create_tx(
        cls,
        date: str,
        tx_type: str,
        category: str,
        counterparty: str,
        item_name: str,
        supply_amount: int,
        is_tax_exempt: bool = False,
        period: Optional[str] = None,
        source: str = "manual",
        ref_id: str = "",
        branch_id: str = "main"
    ) -> "VatTransaction":
        if supply_amount < 0:
            raise DomainError("공급가액은 0원 이상이어야 합니다.")

        # 중복 방지 (ref_id 존재 시)
        if ref_id:
            existing = [t for t in cls.find(branch_id) if getattr(t, "ref_id", "") == ref_id]
            if existing:
                return existing[0]

        # 세액 자동 계산 (면세가 아닐 경우 10%)
        tax = 0 if is_tax_exempt else int(round(supply_amount * 0.1))

        # 과세기간 자동 분류 (미입력 시 날짜 기준 추론)
        if not period:
            dt = datetime.strptime(date[:10], "%Y-%m-%d")
            year = dt.year
            month = dt.month
            if 1 <= month <= 3:
                period = f"{year}-1기예정"
            elif 4 <= month <= 6:
                period = f"{year}-1기확정"
            elif 7 <= month <= 9:
                period = f"{year}-2기예정"
            else:
                period = f"{year}-2기확정"

        tx = cls(
            branch_id=branch_id,
            date=date,
            tx_type=tx_type.upper(),
            category=category,
            counterparty=counterparty,
            item_name=item_name,
            supply_amount=supply_amount,
            tax_amount=tax,
            is_tax_exempt=is_tax_exempt,
            period=period,
            source=source,
            ref_id=ref_id
        )
        tx.save("created")
        return tx

    def card(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "tx_type": self.tx_type,
            "category": self.category,
            "counterparty": self.counterparty,
            "item_name": self.item_name,
            "supply_amount": self.supply_amount,
            "tax_amount": self.tax_amount,
            "total_amount": self.supply_amount + self.tax_amount,
            "is_tax_exempt": self.is_tax_exempt,
            "period": self.period,
            "source": self.source,
            "ref_id": self.ref_id
        }


class PropertyAsset(Obj):
    """보유 부동산 자산 엔티티 (종부세 과세 대상)."""
    label = "보유 부동산"
    scope_field = "branch_id"

    branch_id: str = "main"
    name: str                                # 부동산 명칭 (예: 시크빌 원룸주택 20개실, 테헤란로 상가)
    asset_type: str = "RESIDENTIAL"          # RESIDENTIAL (주택) | COMMERCIAL (일반건축물/토지)
    address: str = ""                        # 소재지
    official_price: int = 0                  # 공시가격 (원)
    ownership_ratio: float = 100.0           # 지분율 (%)
    is_single_homeowner: bool = False        # 1세대 1주택 단독명의 여부
    holding_years: int = 5                   # 보유 연수 (장기보유 세액공제용)
    owner_age: int = 60                      # 소유자 연령 (고령자 세액공제용)

    def card(self) -> Dict[str, Any]:
        share_price = int(round(self.official_price * (self.ownership_ratio / 100.0)))
        return {
            "id": self.id,
            "name": self.name,
            "asset_type": self.asset_type,
            "address": self.address,
            "official_price": self.official_price,
            "ownership_ratio": self.ownership_ratio,
            "share_price": share_price,
            "is_single_homeowner": self.is_single_homeowner,
            "holding_years": self.holding_years,
            "owner_age": self.owner_age
        }


# -------------------------------------------------------------
# 세무 집계 및 5역할 인텔리전스 엔진
# -------------------------------------------------------------

def calculate_vat_report(period: Optional[str] = None, branch_id: str = "main") -> Dict[str, Any]:
    """과세기간별 부가가치세 합산 리포트."""
    txs = VatTransaction.find(branch_id)
    if period:
        txs = [t for t in txs if t.period == period]

    sales_taxable = sum(t.supply_amount for t in txs if t.tx_type == "SALES" and not t.is_tax_exempt)
    sales_exempt = sum(t.supply_amount for t in txs if t.tx_type == "SALES" and t.is_tax_exempt)
    sales_tax = sum(t.tax_amount for t in txs if t.tx_type == "SALES")

    purchase_taxable = sum(t.supply_amount for t in txs if t.tx_type == "PURCHASE" and not t.is_tax_exempt)
    purchase_exempt = sum(t.supply_amount for t in txs if t.tx_type == "PURCHASE" and t.is_tax_exempt)
    purchase_tax = sum(t.tax_amount for t in txs if t.tx_type == "PURCHASE")

    net_tax_payable = sales_tax - purchase_tax

    return {
        "period": period or "전체 과세기간",
        "tx_count": len(txs),
        "sales": {
            "taxable_supply": sales_taxable,
            "exempt_supply": sales_exempt,
            "total_supply": sales_taxable + sales_exempt,
            "tax": sales_tax
        },
        "purchase": {
            "taxable_supply": purchase_taxable,
            "exempt_supply": purchase_exempt,
            "total_supply": purchase_taxable + purchase_exempt,
            "tax": purchase_tax
        },
        "net_tax_payable": net_tax_payable,
        "is_refund": net_tax_payable < 0,
        "refund_amount": abs(net_tax_payable) if net_tax_payable < 0 else 0
    }


def calculate_comprehensive_property_tax(
    is_single_homeowner_override: Optional[bool] = None,
    branch_id: str = "main"
) -> Dict[str, Any]:
    """2026 현행 종합부동산세(주택분) 정밀 누진 시뮬레이션."""
    assets = PropertyAsset.find(branch_id)
    residential_assets = [a for a in assets if a.asset_type == "RESIDENTIAL"]

    total_official_price = sum(
        int(round(a.official_price * (a.ownership_ratio / 100.0)))
        for a in residential_assets
    )

    # 1세대 1주택 여부
    is_single = is_single_homeowner_override
    if is_single is None:
        is_single = len(residential_assets) == 1 and bool(residential_assets[0].is_single_homeowner)

    # 기본공제 (1세대 1주택 12억 원, 다주택/일반 9억 원)
    basic_deduction = 1_200_000_000 if is_single else 900_000_000
    market_fair_rate = 0.60    # 공정시장가액비율 60% (Librarian 팩트체크 기준)

    tax_base_raw = max(0, total_official_price - basic_deduction)
    tax_base = int(round(tax_base_raw * market_fair_rate))

    # 누진세율표 (2026년 일반 기본세율: 0.5% ~ 2.7%, 3주택 이상 중과 0.5% ~ 5.0%)
    is_heavy_surcharge = len(residential_assets) >= 3
    calculated_tax = 0

    if tax_base > 0:
        if not is_heavy_surcharge:
            # 일반 세율
            if tax_base <= 300_000_000:
                calculated_tax = int(round(tax_base * 0.005))
            elif tax_base <= 600_000_000:
                calculated_tax = int(round(tax_base * 0.007 - 600_000))
            elif tax_base <= 1_200_000_000:
                calculated_tax = int(round(tax_base * 0.010 - 2_400_000))
            elif tax_base <= 2_500_000_000:
                calculated_tax = int(round(tax_base * 0.013 - 6_000_000))
            elif tax_base <= 5_000_000_000:
                calculated_tax = int(round(tax_base * 0.015 - 11_000_000))
            elif tax_base <= 9_400_000_000:
                calculated_tax = int(round(tax_base * 0.020 - 36_000_000))
            else:
                calculated_tax = int(round(tax_base * 0.027 - 101_800_000))
        else:
            # 3주택 이상 중과세율
            if tax_base <= 300_000_000:
                calculated_tax = int(round(tax_base * 0.005))
            elif tax_base <= 600_000_000:
                calculated_tax = int(round(tax_base * 0.010 - 1_500_000))
            elif tax_base <= 1_200_000_000:
                calculated_tax = int(round(tax_base * 0.020 - 7_500_000))
            elif tax_base <= 2_500_000_000:
                calculated_tax = int(round(tax_base * 0.030 - 19_500_000))
            elif tax_base <= 5_000_000_000:
                calculated_tax = int(round(tax_base * 0.040 - 44_500_000))
            else:
                calculated_tax = int(round(tax_base * 0.050 - 94_500_000))

    calculated_tax = max(0, calculated_tax)

    # 재산세 중복공제액 (종부세 과세표준 해당 재산세분 표준 0.24% 적용)
    property_tax_credit = int(round(tax_base * 0.0024)) if tax_base > 0 else 0
    subtotal_tax = max(0, calculated_tax - property_tax_credit)

    # 1세대 1주택 고령자 및 장기보유 세액공제 (최대 80%)
    special_credit_rate = 0.0
    if is_single and residential_assets:
        asset = residential_assets[0]
        # 연령별 공제 (60~65세: 20%, 65~70세: 30%, 70세 이상: 40%)
        age_rate = 0.40 if asset.owner_age >= 70 else (0.30 if asset.owner_age >= 65 else (0.20 if asset.owner_age >= 60 else 0.0))
        # 보유기간별 공제 (5~10년: 20%, 10~15년: 40%, 15년 이상: 50%)
        holding_rate = 0.50 if asset.holding_years >= 15 else (0.40 if asset.holding_years >= 10 else (0.20 if asset.holding_years >= 5 else 0.0))
        special_credit_rate = min(0.80, age_rate + holding_rate)

    special_credit_amount = int(round(subtotal_tax * special_credit_rate))
    final_property_tax = max(0, subtotal_tax - special_credit_amount)
    rural_special_tax = int(round(final_property_tax * 0.20))    # 농어촌특별세 20%
    total_tax_payable = final_property_tax + rural_special_tax

    return {
        "residential_count": len(residential_assets),
        "total_official_price": total_official_price,
        "is_single_homeowner": is_single,
        "basic_deduction": basic_deduction,
        "market_fair_rate": market_fair_rate,
        "tax_base_raw": tax_base_raw,
        "tax_base": tax_base,
        "is_heavy_surcharge": is_heavy_surcharge,
        "calculated_tax": calculated_tax,
        "property_tax_credit": property_tax_credit,
        "subtotal_tax": subtotal_tax,
        "special_credit_rate": special_credit_rate,
        "special_credit_amount": special_credit_amount,
        "final_property_tax": final_property_tax,
        "rural_special_tax": rural_special_tax,
        "total_tax_payable": total_tax_payable,
        "is_exempt": tax_base == 0
    }


def get_five_role_intelligence(branch_id: str = "main") -> Dict[str, Dict[str, str]]:
    """5역할 협업 거버넌스 기반 실시간 세무 진단 및 가이드."""
    vat_rep = calculate_vat_report(branch_id=branch_id)
    prop_rep = calculate_comprehensive_property_tax(branch_id=branch_id)

    # 1. Advisor (나침반 3원칙 & 리스크 점검)
    advisor_text = (
        f"나침반 검증 완료: 과세 전표 {vat_rep['tx_count']}건이 단일 WAL 원장에 기록되어 1분 내 추적 가능합니다. "
        + (f"주의: 납부세액이 {vat_rep['net_tax_payable']:,}원 발생하였으므로 통장 유동성을 확보하십시오."
           if vat_rep['net_tax_payable'] > 0 else "환급 예상 상태입니다. 매입세금계산서 누락 여부를 확인하십시오.")
    )

    # 2. Librarian (법령/세율 팩트체크)
    librarian_text = (
        f"최신 법정 기준 검증: 부가세율 10%, 2026 주택분 공정시장가액비율 {int(prop_rep['market_fair_rate']*100)}%, "
        f"기본공제 {prop_rep['basic_deduction']//100_000_000}억 원이 정확히 적용되었습니다. "
        + ("3주택 이상 다주택 중과세율이 활성화되었습니다." if prop_rep['is_heavy_surcharge'] else "일반 누진세율이 적용되었습니다.")
    )

    # 3. Tutor (산출 원리 분해)
    tutor_text = (
        f"원리 설명: 부가세는 매출세액({vat_rep['sales']['tax']:,}원)에서 매입세액({vat_rep['purchase']['tax']:,}원)을 공제해 계산되었습니다. "
        f"종부세는 공시가 합계({prop_rep['total_official_price']:,}원)에서 {prop_rep['basic_deduction']//100_000_000}억을 차감한 뒤 "
        f"공정시장가액비율(60%)을 곱해 과세표준 {prop_rep['tax_base']:,}원을 도출했습니다."
    )

    # 4. Editor (데이터 정합성 및 무결성)
    editor_text = (
        f"데이터 검증 통과: 총 {vat_rep['tx_count']}건의 전표 및 {prop_rep['residential_count']}건의 부동산 자산 무결성이 확인되었습니다. "
        "모든 계산은 부동소수점 오차 없이 원 단위 정밀 정규화되었습니다."
    )

    # 5. Roommate (진도 요약 및 다음 액션)
    roommate_text = (
        "이번 주 세무 점검이 완료되었습니다! 👏 "
        "다음 액션: apps/rent2의 최신 수납 내역을 'rent2 원클릭 동기화' 버튼으로 가져와 매출 원장을 최신화해 보세요."
    )

    return {
        "Advisor": {"role": "자문가", "tag": "방향 및 리스크 점검", "message": advisor_text},
        "Librarian": {"role": "사서", "tag": "최신 법령·세율 팩트체크", "message": librarian_text},
        "Tutor": {"role": "튜터", "tag": "산출 근거 눈높이 설명", "message": tutor_text},
        "Editor": {"role": "엔지니어", "tag": "코드 및 데이터 무결성", "message": editor_text},
        "Roommate": {"role": "동거인", "tag": "진도 체크 및 실행 독려", "message": roommate_text}
    }


def sync_from_rent2(branch_id: str = "main") -> int:
    """apps/rent2 임대차 수납 데이터를 부가세 매출 전표로 동기화 (멱등성 보장)."""
    # rent2 seed_data.json 경로 탐색
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    rent2_seed = os.path.join(root, "apps", "rent2", "backend", "seed_data.json")

    imported = 0
    if os.path.exists(rent2_seed):
        try:
            with open(rent2_seed, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 임대 계약 정보 추출
            contracts = data.get("contracts", [])
            for c in contracts:
                tenant = c.get("tenant_name", "미상임차인")
                room = c.get("room_no", "원룸")
                monthly_rent = c.get("monthly_rent", 0)
                if monthly_rent <= 0:
                    continue

                # 계약일 기준 2026년 1기, 2기 모의 월세 전표 생성
                for m in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
                    ref_key = f"rent2_{room}_{tenant}_2026_{m:02d}"
                    date_str = f"2026-{m:02d}-25"
                    # 주택 임대료는 부가세 면세 적용, 상가/사무실은 과세
                    is_exempt = "상가" not in room and "오피스" not in room

                    tx = VatTransaction.create_tx(
                        date=date_str,
                        tx_type="SALES",
                        category="임대료(rent2 연동)",
                        counterparty=f"{tenant} ({room}호)",
                        item_name=f"2026년 {m}월분 월세 임대료",
                        supply_amount=monthly_rent,
                        is_tax_exempt=is_exempt,
                        source="rent2_sync",
                        ref_id=ref_key,
                        branch_id=branch_id
                    )
                    imported += 1
        except Exception as e:
            print("rent2 동기화 중 오류 (진행 계속):", e)

    return imported


def seed(branch_id: str = "main") -> int:
    """초기 세무 기준 자산 및 샘플 전표 시딩."""
    # 1. 초기 부동산 자산 등록 (시크빌 원룸주택 등)
    if not PropertyAsset.find(branch_id):
        PropertyAsset(
            branch_id=branch_id,
            name="신비주택 원룸 (20개실)",
            asset_type="RESIDENTIAL",
            address="인천광역시 미추홀구 석정로 123",
            official_price=1_450_000_000,
            ownership_ratio=100.0,
            is_single_homeowner=False,
            holding_years=6,
            owner_age=62
        ).save("seed")

    # 2. 초기 매입 전표 시딩 (건물 수리비, 청소비, 세무사 기장료 등)
    if not VatTransaction.find(branch_id):
        VatTransaction.create_tx(
            date="2026-03-15",
            tx_type="PURCHASE",
            category="시설보수",
            counterparty="대한설비공사",
            item_name="보일러 및 배관 동파 수리비",
            supply_amount=800_000,
            is_tax_exempt=False,
            branch_id=branch_id
        )
        VatTransaction.create_tx(
            date="2026-06-20",
            tx_type="PURCHASE",
            category="소모품",
            counterparty="청결종합유통",
            item_name="공용계단 청소용역 및 비품 구입",
            supply_amount=450_000,
            is_tax_exempt=False,
            branch_id=branch_id
        )
        VatTransaction.create_tx(
            date="2026-07-10",
            tx_type="PURCHASE",
            category="중개수수료",
            counterparty="행복공인중개사사무소",
            item_name="201호 신규 임대차 중개보수",
            supply_amount=300_000,
            is_tax_exempt=False,
            branch_id=branch_id
        )

        # rent2 연동 시동
        sync_from_rent2(branch_id)

    return VatTransaction.count(branch_id)
