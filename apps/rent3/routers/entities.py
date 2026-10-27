"""apps/rent3/routers/entities.py — 8대 표준 엔티티 CRUD, O(1) 인덱스 검색, 16단계 워크플로우 액션 라우터.

하위 호환성 유지: 모든 URL 경로는 기존 `/api/rent3/...` 스펙을 100% 보존합니다.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel

from ..entities import (
    Admin, Lessor, Tenant, BuildingEntity, RoomEntity, ContractEntity,
    Pay, Outlay, Rent3EntityManager, Rent3WorkflowEngine, seed_default_entities_if_empty
)

router = APIRouter(tags=["rent3_entities"])

# 17. 8대 표준 엔티티 (Admin, Lessor, Tenant, Building, Room, Contract, Pay, Outlay) CRUD & 검색 API
# ─────────────────────────────────────────────────────────────
@router.get("/entities/summary/dashboard")
def get_entities_summary_dashboard():
    """8대 표준 엔티티 전체 등록 건수 및 최신 요약 데이터 조회."""
    seed_default_entities_if_empty()
    return {
        "success": True,
        "counts": {
            "admins": len(Admin.all()),
            "lessors": len(Lessor.all()),
            "tenants": len(Tenant.all()),
            "buildings": len(BuildingEntity.all()),
            "rooms": len(RoomEntity.all()),
            "contracts": len(ContractEntity.all()),
            "pays": len(Pay.all()),
            "outlays": len(Outlay.all())
        },
        "workflow_steps_count": 16,
        "system_status": "READY"
    }


@router.get("/entities/{entity_type}")
def list_entities(
    entity_type: str, 
    query: str = Query("", description="검색 키워드 (성명, 전화번호, 호실명, 적요 등)"),
    branch: Optional[str] = Query(None, description="소속 지점 필터")
):
    """지정된 엔티티 타입의 전체 목록 조회 및 키워드 검색."""
    seed_default_entities_if_empty()
    try:
        filters = {}
        if branch:
            filters["branch"] = branch
        items = Rent3EntityManager.search(entity_type, query=query, filters=filters)
        return {
            "success": True,
            "entity_type": entity_type.lower(),
            "count": len(items),
            "items": [item.model_dump() for item in items]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/entities/{entity_type}/{custom_id}")
def get_entity_detail(entity_type: str, custom_id: str):
    """단일 엔티티 고유 ID 또는 시스템 ID 기반 상세 조회."""
    seed_default_entities_if_empty()
    item = Rent3EntityManager.find_by_custom_id(entity_type, custom_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"{entity_type} 객체({custom_id})를 찾을 수 없습니다.")
    return {
        "success": True,
        "entity_type": entity_type.lower(),
        "item": item.model_dump()
    }


@router.post("/entities/{entity_type}")
def create_entity(entity_type: str, payload: Dict[str, Any] = Body(...)):
    """신규 엔티티 객체 생성."""
    seed_default_entities_if_empty()
    try:
        item = Rent3EntityManager.create(entity_type, payload)
        return {
            "success": True,
            "message": f"{entity_type} 객체가 성공적으로 생성되었습니다.",
            "item": item.model_dump()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"객체 생성 실패: {str(e)}")


@router.put("/entities/{entity_type}/{custom_id}")
def update_entity(entity_type: str, custom_id: str, payload: Dict[str, Any] = Body(...)):
    """기존 엔티티 객체 필드 수정/편집."""
    seed_default_entities_if_empty()
    try:
        updated = Rent3EntityManager.update(entity_type, custom_id, payload)
        return {
            "success": True,
            "message": f"{entity_type} 객체({custom_id})가 성공적으로 수정되었습니다.",
            "item": updated.model_dump()
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"객체 수정 실패: {str(e)}")


@router.delete("/entities/{entity_type}/{custom_id}")
def delete_entity(entity_type: str, custom_id: str):
    """엔티티 객체 삭제."""
    seed_default_entities_if_empty()
    deleted = Rent3EntityManager.delete(entity_type, custom_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"{entity_type} 객체({custom_id})를 찾을 수 없거나 삭제에 실패했습니다.")
    return {
        "success": True,
        "message": f"{entity_type} 객체({custom_id})가 삭제되었습니다."
    }


# ─────────────────────────────────────────────────────────────

# 18. 16단계 이벤트 라이프사이클 액션 실행 API
# ─────────────────────────────────────────────────────────────
@router.post("/entities/workflow/{action_name}")
def execute_workflow_action(action_name: str, payload: Dict[str, Any] = Body(default={})):
    """사용자 이벤트 라이프사이클 단계별 액션 처리."""
    seed_default_entities_if_empty()
    try:
        if action_name == "app_publish_info":
            res = Rent3WorkflowEngine.step1_app_published_info()
            return {"success": True, "result": res}
            
        elif action_name == "register_lessor":
            lessor = Rent3WorkflowEngine.step2_register_lessor(
                name=payload.get("name", "신규임대인"),
                phone=payload.get("phone", "010-0000-0000"),
                account_number=payload.get("account_number", "은행 000-00-00000"),
                business_number=payload.get("business_number", "000-00-00000"),
                email=payload.get("email", "lessor@example.com"),
                branch=payload.get("branch", "main")
            )
            return {"success": True, "lessor": lessor.model_dump()}

        elif action_name == "publish_room":
            room = Rent3WorkflowEngine.step3_publish_room_listing(
                id_building=payload.get("id_building", ""),
                name=payload.get("name", "신규호실"),
                deposit=payload.get("deposit", 5000000),
                monthly_rent=payload.get("monthly_rent", 500000),
                maintenance_fee=payload.get("maintenance_fee", 50000),
                status_description=payload.get("status_description", "깨끗한 풀옵션 원룸"),
                options=payload.get("options", None)
            )
            return {"success": True, "room": room.model_dump()}

        elif action_name == "register_tenant":
            tenant = Rent3WorkflowEngine.step4_register_tenant(
                name=payload.get("name", "신규임차인"),
                phone=payload.get("phone", "010-1111-2222"),
                account_number=payload.get("account_number", "계좌 111-222-333"),
                car_number=payload.get("car_number", ""),
                email=payload.get("email", "tenant@example.com"),
                business_number=payload.get("business_number", "")
            )
            return {"success": True, "tenant": tenant.model_dump()}

        elif action_name == "verify_registry":
            check_res = Rent3WorkflowEngine.step5_verify_and_crosscheck_registry(
                id_room=payload.get("id_room", ""),
                id_lessor=payload.get("id_lessor", ""),
                id_tenant=payload.get("id_tenant", ""),
                issue_number=payload.get("issue_number", ""),
                mortgage_amount=payload.get("mortgage_amount", 0)
            )
            return {"success": True, "check_info": check_res}

        elif action_name == "sign_contract":
            ct = Rent3WorkflowEngine.step8_sign_contract(
                id_room=payload.get("id_room", ""),
                id_tenant=payload.get("id_tenant", ""),
                move_in_date=payload.get("move_in_date", datetime.now().strftime("%Y-%m-%d")),
                lease_term=payload.get("lease_term", "12개월"),
                deposit=payload.get("deposit", 5000000),
                monthly_rent=payload.get("monthly_rent", 500000),
                maintenance_fee=payload.get("maintenance_fee", 50000),
                rent_type=payload.get("rent_type", "월세"),
                payment_timing=payload.get("payment_timing", "후불")
            )
            return {"success": True, "contract": ct.model_dump()}

        elif action_name == "remit_deposit":
            dep_res = Rent3WorkflowEngine.step9_remit_contract_deposit(
                id_contract=payload.get("id_contract", ""),
                capture_img_url=payload.get("capture_img_url", "")
            )
            return dep_res

        elif action_name == "checkin":
            cin_res = Rent3WorkflowEngine.step10_process_checkin(
                id_room=payload.get("id_room", ""),
                id_tenant=payload.get("id_tenant", ""),
                doorlock_pwd=payload.get("doorlock_pwd", "2026*")
            )
            return cin_res

        elif action_name == "record_pay":
            pay = Rent3WorkflowEngine.step12_record_monthly_pay(
                id_room=payload.get("id_room", ""),
                billing_month=payload.get("billing_month", "2026.10"),
                monthly_rent=payload.get("monthly_rent", 500000),
                maintenance_fee=payload.get("maintenance_fee", 50000),
                vat=payload.get("vat", 0)
            )
            return {"success": True, "pay": pay.model_dump()}

        elif action_name == "record_outlay":
            outlay = Rent3WorkflowEngine.step13_record_outlay(
                id_room=payload.get("id_room", "COMMON"),
                summary=payload.get("summary", "공용 관리 지출"),
                amount=payload.get("amount", 100000),
                category=payload.get("category", "cleaning"),
                vendor_name=payload.get("vendor_name", "협력업체")
            )
            return {"success": True, "outlay": outlay.model_dump()}

        elif action_name == "generate_tax":
            tax_res = Rent3WorkflowEngine.step14_generate_tax_materials(
                year=payload.get("year", 2026)
            )
            return {"success": True, "tax_materials": tax_res}

        else:
            raise HTTPException(status_code=400, detail=f"알 수 없는 워크플로우 액션: {action_name}")

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"워크플로우 실행 오류: {str(e)}")















