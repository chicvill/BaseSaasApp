"""
apps/tax/backend/api.py — 스마트 세무 REST API 라우터.
모든 엔드포인트는 /api/tax 아래로 자동 마운트됩니다.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core import NotFound, DomainError
from . import domain

router = APIRouter(tags=["Tax"])


class TxCreateReq(BaseModel):
    date: str
    tx_type: str = "SALES"
    category: str = "임대료"
    counterparty: str
    item_name: str
    supply_amount: int
    is_tax_exempt: bool = False
    period: Optional[str] = None
    branch_id: str = "main"


class AssetCreateReq(BaseModel):
    name: str
    asset_type: str = "RESIDENTIAL"
    address: str = ""
    official_price: int
    ownership_ratio: float = 100.0
    is_single_homeowner: bool = False
    holding_years: int = 5
    owner_age: int = 60
    branch_id: str = "main"


@router.get("/summary")
def get_tax_summary(
    period: Optional[str] = None,
    branch: str = "main"
) -> Dict[str, Any]:
    """부가세, 종부세, 5역할 인텔리전스를 통합 제공하는 마스터 대시보드 API."""
    vat = domain.calculate_vat_report(period=period, branch_id=branch)
    prop = domain.calculate_comprehensive_property_tax(branch_id=branch)
    roles = domain.get_five_role_intelligence(branch_id=branch)
    return {
        "ok": True,
        "vat": vat,
        "property_tax": prop,
        "five_roles": roles
    }


@router.get("/vat/transactions")
def list_transactions(
    period: Optional[str] = None,
    tx_type: Optional[str] = None,
    branch: str = "main"
) -> Dict[str, Any]:
    """부가세 전표 목록 조회."""
    txs = domain.VatTransaction.find(branch)
    if period:
        txs = [t for t in txs if t.period == period]
    if tx_type:
        txs = [t for t in txs if t.tx_type == tx_type.upper()]
    # 최신 날짜순 정렬
    txs = sorted(txs, key=lambda x: x.date, reverse=True)
    return {
        "ok": True,
        "count": len(txs),
        "items": [t.card() for t in txs]
    }


@router.post("/vat/transactions")
def create_transaction(req: TxCreateReq) -> Dict[str, Any]:
    """신규 전표 등록."""
    try:
        tx = domain.VatTransaction.create_tx(
            date=req.date,
            tx_type=req.tx_type,
            category=req.category,
            counterparty=req.counterparty,
            item_name=req.item_name,
            supply_amount=req.supply_amount,
            is_tax_exempt=req.is_tax_exempt,
            period=req.period,
            branch_id=req.branch_id
        )
        return {"ok": True, "item": tx.card()}
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/vat/transactions/{tx_id}")
def delete_transaction(tx_id: str) -> Dict[str, Any]:
    """전표 삭제."""
    try:
        tx = domain.VatTransaction.get(tx_id)
        tx.delete()
        return {"ok": True, "deleted_id": tx_id}
    except NotFound:
        raise HTTPException(status_code=404, detail="전표를 찾을 수 없습니다.")


@router.get("/property/assets")
def list_property_assets(branch: str = "main") -> Dict[str, Any]:
    """보유 부동산 목록 조회."""
    assets = domain.PropertyAsset.find(branch)
    return {
        "ok": True,
        "count": len(assets),
        "items": [a.card() for a in assets]
    }


@router.post("/property/assets")
def create_property_asset(req: AssetCreateReq) -> Dict[str, Any]:
    """부동산 자산 등록."""
    asset = domain.PropertyAsset(
        branch_id=req.branch_id,
        name=req.name,
        asset_type=req.asset_type,
        address=req.address,
        official_price=req.official_price,
        ownership_ratio=req.ownership_ratio,
        is_single_homeowner=req.is_single_homeowner,
        holding_years=req.holding_years,
        owner_age=req.owner_age
    )
    asset.save("created")
    return {"ok": True, "item": asset.card()}


@router.delete("/property/assets/{asset_id}")
def delete_property_asset(asset_id: str) -> Dict[str, Any]:
    """부동산 자산 삭제."""
    try:
        asset = domain.PropertyAsset.get(asset_id)
        asset.delete()
        return {"ok": True, "deleted_id": asset_id}
    except NotFound:
        raise HTTPException(status_code=404, detail="부동산 자산을 찾을 수 없습니다.")


@router.post("/sync/rent2")
def sync_rent2_data(branch: str = "main") -> Dict[str, Any]:
    """apps/rent2 임차 수납 데이터를 부가세 매출 전표로 원클릭 동기화."""
    imported_count = domain.sync_from_rent2(branch_id=branch)
    return {
        "ok": True,
        "imported_count": imported_count,
        "message": f"rent2 수납 원장 {imported_count}건이 세무 전표로 성공적으로 동기화되었습니다."
    }


@router.get("/advisor/feedback")
def get_advisor_feedback(branch: str = "main") -> Dict[str, Any]:
    """5역할 협업 실시간 AI 진단 피드백."""
    roles = domain.get_five_role_intelligence(branch_id=branch)
    return {"ok": True, "roles": roles}
