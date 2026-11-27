"""
apps/rent_dist/rent3/routers/hub_public.py — OpenRent 공개 랜딩·매물·문의·공동계약 열기 및 관리자 Data Pool 파일 조회.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.errors import DomainError, NotFound
from apps.rent_dist._rent3_core import (
    POOL_COLLECTIONS,
    Building,
    CoSignSession,
    Contract,
    DirectChat,
    Lessor,
    Room,
    Tenant,
    ensure_pool_seeded,
    listing_pdf,
    normalize_branch_id,
    pool,
    roomregistry as room_registry,
)
from apps.rent_dist._rent3_core.authz import LESSOR_ROLES, claims_from, contract_scope, is_admin
from apps.rent_dist._rent3_core.ops import ExpiryRenewalCheck
from ._hub_deps import ADMIN_ONLY, demo_login_allowed

router = APIRouter()

DEFAULT_ROOM_IMAGES = (
    "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
    "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80",
    "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
    "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800&q=80",
)


class PortalInquiryReq(BaseModel):
    branch: str
    room_no: str
    sender_name: str
    sender_phone: str = ""
    visit_date: str = ""
    message: str


class PortalRentNowReq(BaseModel):
    caller_role: str = "tenant"          # lessor | tenant
    branch: str
    room_no: str
    tenant_name: str = ""
    tenant_phone: str = ""
    id_tenant: str = ""


@router.get("/overview", dependencies=ADMIN_ONLY)
def pool_overview() -> Dict[str, Any]:
    ensure_pool_seeded()
    p = pool()
    return {
        "success": True,
        "meta": p.overview(),
        "apps": [
            {"app_id": "rent3", "name": "직거래 포털 홈 (OpenRent)", "icon": "🏡", "url": "/rent3/"},
            {"app_id": "rent3_pool", "name": "통합관리 Data Pool 허브", "icon": "🌐", "url": "/rent3/pool.html"},
            {"app_id": "rent3_admin", "name": "관리인 전용 앱", "icon": "👑", "url": "/rent3_admin/"},
            {"app_id": "rent3_lessor", "name": "임대인 전용 앱", "icon": "🏢", "url": "/rent3_lessor/"},
            {"app_id": "rent3_tenant", "name": "임차인 전용 앱", "icon": "🔑", "url": "/rent3_tenant/"},
            {"app_id": "rent3_contract", "name": "공동 임대계약 전용 앱", "icon": "🤝", "url": "/rent3_contract/"},
        ],
        "recent_events": p.recent_events(limit=30),
    }


@router.get("/files/{collection}", dependencies=ADMIN_ONLY)
def pool_file_raw(collection: str) -> Dict[str, Any]:
    ensure_pool_seeded()
    from apps.rent_dist._rent3_core import poolsearch as _ps
    env = pool().raw_collection(collection)
    env["items"] = [_ps.redact_item(it) for it in env["items"]]       # 비밀번호 해시·기기 목록 같은 보호 필드는 관리인 화면에도 내려보내지 않는다
    return {
        "success": True,
        "collections": list(POOL_COLLECTIONS),
        **env,
    }


@router.get("/lessor-offer")
def lessor_offer() -> Dict[str, Any]:
    """임대인용 서비스 안내(공개): 월 가맹비·무료 기간·세무 자료 범위. 값은 청구 로직과 같은 곳에서 읽는다."""
    from apps.rent_dist._rent3_core import offer
    return offer.terms()


@router.get("/lessor-offer/sample.csv")
def lessor_offer_sample():
    """가상 자료로 만든 납부현황 샘플 CSV(공개)."""
    from apps.rent_dist._rent3_core import offer
    return Response(
        content=offer.sample_csv().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{offer.SAMPLE_CSV_NAME}"'},
    )


@router.get("/landing")
def portal_landing(request: Request) -> Dict[str, Any]:
    """OpenRent 스타일 직거래 포털 메인 화면용 실시간 Data Pool 집계."""
    ensure_pool_seeded()
    ExpiryRenewalCheck.activate_due()   # 공개일(퇴실 기준일 1개월 전)이 된 사전 매물을 올린다 — 스케줄러가 없어 조회·로그인 때 확인
    CoSignSession.expire_idle()         # 24시간/7일 기한이 지난 세션을 먼저 해제해 만료된 잠금 표시를 없앤다
    meta = pool().overview()
    buildings = Building.all()
    lessors = Lessor.all()
    rooms = Room.all()
    contracts = [c for c in Contract.all() if c.status == "ACTIVE"]
    sessions = [s for s in CoSignSession.all() if s.status != "CANCELLED"]

    bldg_by_branch: Dict[str, Building] = {b.branch: b for b in buildings}
    lessor_by_branch: Dict[str, Lessor] = {l.branch: l for l in lessors}

    franchise_ok = {l.branch: l.status == "ACTIVE" for l in lessors}
    listings: List[Dict[str, Any]] = []
    for idx, r in enumerate(sorted(rooms, key=lambda x: (x.branch, x.room_no))):
        is_public_vacancy = (not r.is_occupied and r.is_published) or r.pre_moveout_notice
        if not is_public_vacancy or not franchise_ok.get(r.branch, True):
            continue
        r = r.as_listing()          # 입주 중 사전 매물은 '다음 세입자용 조건'으로 보여 준다(현재 계약 값은 그대로)
        b = bldg_by_branch.get(r.branch)
        lsr = lessor_by_branch.get(r.branch)
        img_url = r.images[0] if r.images else DEFAULT_ROOM_IMAGES[idx % len(DEFAULT_ROOM_IMAGES)]
        spec = listing_pdf.normalize_listing_spec(r.listing_spec, r.option_list)
        opts = spec["options"] or [str(o.get("item", "")) for o in (r.option_list or []) if o.get("item")]
        listings.append({
            "id": r.id,
            "branch": r.branch,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "room_no": r.room_no,
            "name": r.name or f"{r.room_no}호",
            "room_category": r.room_category,
            "floor": r.floor,
            "total_floors": r.total_floors,
            "direction": r.direction,
            "area_sqm": r.area_sqm,
            "lease_type": r.lease_type,
            "pay_timing": r.pay_timing,
            "deposit": r.deposit,
            "monthly_rent": r.monthly_rent,
            "maintenance_fee": r.maintenance_fee,
            "cleaning_fee": r.cleaning_fee,
            "vat_applicable": r.vat_applicable,
            "available_from": r.available_from or "즉시입주",
            "pre_moveout_notice": r.pre_moveout_notice,
            "clean_title": room_registry.public_clean(r),       # 임대인이 확정한 최근 등기부가 있고 코드가 깨끗하다고 계산할 때만 '등기클린'
            "status_description": r.status_description,
            "options": opts,
            "listing_spec": spec,
            "terms_pdf_url": listing_pdf.listing_pdf_url(r.id),
            "image": img_url,
            "images": list(r.images) if r.images else [img_url],
            "contract_in_progress": CoSignSession.live_for_room(r.branch, r.room_no) is not None,
            "lessor_name": lsr.name if lsr else "건물 소유주",
            "lessor_phone": (lsr.phone if lsr and lsr.phone else (b.manager_phone if b else "")),
        })

    open_sessions = [
        {
            "id": s.id,
            "session_code": s.session_code,
            "mode": s.mode,
            "status": s.status,
            "call_status": s.call_status,
            "initiated_by_role": s.initiated_by_role,
            "branch": s.branch,
            "building_name": s.building_name,
            "room_no": s.room_no,
            "lessor_name": s.lessor_name,
            "current_step": s.current_step,
            "tenant_name": str((s.terms or {}).get("tenant_name", "")),
        }
        for s in sessions
        if s.status == "IN_PROGRESS"
    ]

    demo_accounts: List[Dict[str, Any]] = [
        {
            "group": "관리인",
            "badge": "👑 총괄 관리인 전용 앱",
            "label": "본사 총괄 관리인 (admin / 0202)",
            "identifier": "admin",
            "password": "0202",
            "target_desc": "/rent3_admin/",
        },
        {
            "group": "통합허브",
            "badge": "🌐 Data Pool",
            "label": "통합 Data Pool 관제 (admin / 0202)",
            "identifier": "admin",
            "password": "0202",
            "target_hint": "pool",
            "target_desc": "/rent3/pool.html",
        },
    ]
    for lsr in lessors[:4]:
        demo_accounts.append({
            "group": "임대인",
            "badge": f"🏢 임대인({lsr.branch})",
            "label": f"{lsr.name}",
            "identifier": lsr.username or lsr.phone or lsr.id,
            "password": "1212",
            "target_desc": f"/rent3_lessor/?branch={lsr.branch}",
        })
    active_tenants = [t for t in Tenant.all() if t.status == "ACTIVE" and t.room_no]
    for t in active_tenants[:4]:
        demo_accounts.append({
            "group": "임차인",
            "badge": f"🔑 임차인({t.room_no}호)",
            "label": f"{t.name} ({t.phone})",
            "identifier": t.phone or t.name,
            "password": "1212",
            "target_desc": f"/rent3_tenant/?tenant={t.id}",
        })

    occupied_count = sum(1 for r in rooms if r.is_occupied)
    return {
        "success": True,
        "stats": {
            "total_buildings": len(buildings),
            "total_rooms": len(rooms),
            "occupied_rooms": occupied_count,
            "vacant_published_count": len(listings),
            "active_contracts": len(contracts),
            "open_cosign_count": len(open_sessions),
            "pool_revision": meta.get("total_revision", 0),
        },
        "branches": [
            {
                "branch": b.branch,
                "building_name": b.building_name,
                "address": b.address,
                "manager_phone": b.manager_phone,
            }
            for b in buildings
        ],
        "listings": listings,
        "open_sessions": open_sessions,
        "demo_accounts": demo_accounts if demo_login_allowed(request) else [],  # 공통 비밀번호는 로컬에서만 안내
    }


@router.get("/listings/{room_id}/terms.pdf")
def get_public_listing_terms_pdf(room_id: str, request: Request, download: int = 0):
    """공개 매물(공실 공개 또는 전출예정 사전공개)의 표준 임대조건 안내서 PDF 를 반환한다."""
    ensure_pool_seeded()
    room = Room.get(room_id)
    if not room:
        raise NotFound("매물을 찾을 수 없습니다.")
    is_public = (
        ((not room.is_occupied and room.is_published) or room.pre_moveout_notice)
        and Lessor.franchise_open(room.branch)
    )
    if not is_public:
        c = claims_from(request) or {}
        can_staff = is_admin(c) or (
            c.get("r") in LESSOR_ROLES
            and normalize_branch_id(str(c.get("b") or "")) == room.branch
        )
        if not can_staff:
            raise NotFound("공개된 매물이 아닙니다.")
    bldg = Building.get(room.id_building) if room.id_building else Building.first(branch=room.branch)
    lsr = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=room.branch)
    pdf_bytes = listing_pdf.build_listing_terms_pdf(room, building=bldg, lessor=lsr)
    disp = "attachment" if download else "inline"
    fname_ascii = f"rent3_terms_{room.room_no}.pdf"
    fname_utf8 = quote(f"{(bldg.building_name if bldg else room.branch)}_{room.room_no}호_표준임대조건서.pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disp}; filename="{fname_ascii}"; filename*=UTF-8\'\'{fname_utf8}',
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.post("/inquiries")
def create_portal_inquiry(req: PortalInquiryReq) -> Dict[str, Any]:
    """OpenRent 포털에서 예비임차인이 임대인에게 직접 1:1 방문투어/문의 전송 (ops.json 실시간 동기화)."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    sender = req.sender_name.strip()
    msg_body = req.message.strip()
    if not sender or not msg_body:
        raise DomainError("성명과 문의 내용을 모두 입력해 주세요.")

    room = Room.first(branch=b_id, room_no=req.room_no.strip())
    prefix_parts: List[str] = []
    if req.visit_date.strip():
        prefix_parts.append(f"[방문투어 희망: {req.visit_date.strip()}]")
    if req.sender_phone.strip():
        prefix_parts.append(f"(연락처: {req.sender_phone.strip()})")
    composed = f"{' '.join(prefix_parts)} {msg_body}".strip()

    chat = DirectChat(
        branch=b_id,
        id_room=room.id if room else "",
        room_no=req.room_no.strip(),
        sender_role="tenant",
        sender_name=sender,
        message=composed,
    ).save("portal_inquiry_sent", app_source="rent3_portal")

    return {
        "success": True,
        "message": f"{req.room_no}호 임대인에게 1:1 직거래 방문문의가 실시간 전달되었습니다.",
        "chat": chat.model_dump(),
    }


@router.post("/cosign-open", dependencies=[Depends(contract_scope)])
def open_or_join_rent_now(req: PortalRentNowReq) -> Dict[str, Any]:
    """OpenRent 'Rent Now' 버튼 — 로그인한 당사자가 매물을 선택하여 상대방을 호출(CALLING)하고 공동계약 앱 URL을 반환."""
    ensure_pool_seeded()
    b_id = normalize_branch_id(req.branch)
    r_no = req.room_no.strip()
    role = "lessor" if req.caller_role == "lessor" else "tenant"
    t_name = req.tenant_name.strip()
    t_phone = req.tenant_phone.strip()
    if role == "tenant" and (not t_name or not t_phone):
        raise DomainError("계약자 성명과 휴대폰 번호를 입력해 주세요.")

    sess = CoSignSession.call_counterparty(
        caller_role=role,
        branch=b_id,
        room_no=r_no,
        tenant_name=t_name,
        tenant_phone=t_phone,
        id_tenant=req.id_tenant,
        app_source="rent3_portal",
    )

    redirect_url = f"/rent3_contract/?role={role}&session={sess.id}"
    callee_ko = "임차인" if role == "lessor" else "임대인"
    return {
        "success": True,
        "session_id": sess.id,
        "session_code": sess.session_code,
        "call_status": sess.call_status,
        "redirect_url": redirect_url,
        "message": f"[{sess.building_name} {sess.room_no}호] {callee_ko}에게 공동계약 호출을 발송했습니다. 공동계약 화면으로 이동합니다.",
    }


@router.get("/pwa/assetlinks.json")
def get_pwa_assetlinks():
    """Google Play TWA(Trusted Web Activity) 디지털 자산 연결(Digital Asset Links) JSON.

    환경변수 RENT3_TWA_PACKAGE_NAME(기본 io.mqnet.rent3), RENT3_TWA_SHA256_CERT(쉼표 구분 지문 목록)을
    설정하고 리버스 프록시(Coolify/Traefik)에서 /.well-known/assetlinks.json 으로 연결하면
    안드로이드 앱 실행 시 브라우저 주소창이 완전히 제거된다.
    """
    pkg = (os.environ.get("RENT3_TWA_PACKAGE_NAME") or "io.mqnet.rent3").strip()
    raw_certs = os.environ.get("RENT3_TWA_SHA256_CERT") or ""
    fingerprints = [c.strip() for c in raw_certs.split(",") if c.strip()]
    payload = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": pkg,
                "sha256_cert_fingerprints": fingerprints,
            },
        }
    ]
    return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json")


@router.get("/rooms/files", dependencies=ADMIN_ONLY)
def list_room_partition_files() -> Dict[str, Any]:
    """분리 보관된 개별 호실 JSON 파일 목록 조회."""
    p = pool()
    rooms_dir = p.rooms_dir
    files = []
    if os.path.exists(rooms_dir):
        for fname in sorted(os.listdir(rooms_dir)):
            if fname.endswith(".json"):
                fpath = os.path.join(rooms_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fp:
                        doc = json.load(fp)
                    files.append({
                        "file_name": fname,
                        "building_name": doc.get("building_name", ""),
                        "room_no": doc.get("room_no", ""),
                        "move_in_date": doc.get("move_in_date", ""),
                        "updated_at": doc.get("updated_at", ""),
                    })
                except Exception:
                    files.append({"file_name": fname})
    return {"count": len(files), "files": files}


@router.get("/rooms/files/{file_name}", dependencies=ADMIN_ONLY)
def get_room_partition_file(file_name: str) -> Dict[str, Any]:
    """개별 호실 분리 JSON 파일 단건 직접 조회 (O(1))."""
    p = pool()
    fpath = os.path.join(p.rooms_dir, file_name)
    if not os.path.exists(fpath):
        raise NotFound(f"호실 파일 '{file_name}'을 찾을 수 없습니다.")
    with open(fpath, "r", encoding="utf-8") as fp:
        return json.load(fp)
