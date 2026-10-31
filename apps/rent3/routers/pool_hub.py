"""
apps/rent3/routers/pool_hub.py — 통합관리용 공동 JSON Data Pool 관제 및 OpenRent 직거래 포털·역할별 로그인 라우터.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from core.auth import Unauthorized, User as CoreUser, ensure_default_admins, issue_token
from core.errors import Conflict, DomainError, NotFound
from apps._rent3_core import (
    POOL_COLLECTIONS,
    Admin,
    Building,
    CoSignSession,
    Contract,
    DirectChat,
    Lessor,
    Room,
    Tenant,
    ensure_pool_seeded,
    normalize_branch_id,
    pool,
)

router = APIRouter(prefix="/pool", tags=["rent3-pool-hub"])

DEFAULT_ROOM_IMAGES = (
    "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=800&q=80",
    "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=800&q=80",
    "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=800&q=80",
    "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800&q=80",
)


class PortalLoginReq(BaseModel):
    login_mode: str = "account"          # account | cosign
    identifier: str = ""                 # 아이디 / 휴대폰 / 이메일 / 성명
    password: str = ""
    target_hint: str = ""                # "" | "pool" | "add_listing" | "broker" | "contract" | "contract:{branch}:{room_no}"
    session_code: str = ""
    cosign_role: str = "tenant"          # lessor | tenant


class PortalRegisterReq(BaseModel):
    role: str = "prospect"               # prospect (예비임차인) | lessor (임대인)
    name: str
    phone: str
    email: str = ""
    username: str = ""
    password: str = "1212"
    building_name: str = ""
    address: str = ""
    branch: str = ""
    target_hint: str = ""


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


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


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


@router.get("/overview")
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


@router.get("/files/{collection}")
def pool_file_raw(collection: str) -> Dict[str, Any]:
    ensure_pool_seeded()
    return {
        "success": True,
        "collections": list(POOL_COLLECTIONS),
        **pool().raw_collection(collection),
    }


@router.get("/rooms/files")
def list_room_files_endpoint() -> Dict[str, Any]:
    """분리 저장된 개별 호실 파일 (data/rent3_pool/rooms/*.json) 메타데이터 목록."""
    ensure_pool_seeded()
    files = pool().list_room_files()
    return {
        "success": True,
        "count": len(files),
        "files": files,
    }


@router.get("/rooms/files/{filename}")
def get_room_file_endpoint(filename: str) -> Dict[str, Any]:
    """특정 개별 호실 파일 (예: '시크빌-201-20250101.json') 원본 조회."""
    ensure_pool_seeded()
    doc = pool().get_room_file(filename)
    if not doc:
        raise NotFound(f"호실 파일을 찾을 수 없습니다: {filename}", hint="예: 시크빌-201-20250101.json")
    return {
        "success": True,
        "file_name": filename,
        "data": doc,
    }


@router.get("/landing")
def portal_landing() -> Dict[str, Any]:
    """OpenRent 스타일 직거래 포털 메인 화면용 실시간 Data Pool 집계."""
    ensure_pool_seeded()
    meta = pool().overview()
    buildings = Building.all()
    lessors = Lessor.all()
    rooms = Room.all()
    contracts = [c for c in Contract.all() if c.status == "ACTIVE"]
    sessions = [s for s in CoSignSession.all() if s.status != "CANCELLED"]

    bldg_by_branch: Dict[str, Building] = {b.branch: b for b in buildings}
    lessor_by_branch: Dict[str, Lessor] = {l.branch: l for l in lessors}

    listings: List[Dict[str, Any]] = []
    for idx, r in enumerate(sorted(rooms, key=lambda x: (x.branch, x.room_no))):
        is_public_vacancy = (not r.is_occupied and r.is_published) or r.pre_moveout_notice
        if not is_public_vacancy:
            continue
        b = bldg_by_branch.get(r.branch)
        lsr = lessor_by_branch.get(r.branch)
        reg = r.registry_info or {}
        img_url = r.images[0] if r.images else DEFAULT_ROOM_IMAGES[idx % len(DEFAULT_ROOM_IMAGES)]
        opts = [str(o.get("item", "")) for o in (r.option_list or []) if o.get("item")]
        listings.append({
            "id": r.id,
            "branch": r.branch,
            "building_name": b.building_name if b else r.branch,
            "address": b.address if b else "",
            "room_no": r.room_no,
            "name": r.name or f"{r.room_no}호",
            "room_category": r.room_category,
            "floor": r.floor,
            "area_sqm": r.area_sqm,
            "lease_type": r.lease_type,
            "pay_timing": r.pay_timing,
            "deposit": r.deposit,
            "monthly_rent": r.monthly_rent,
            "maintenance_fee": r.maintenance_fee,
            "vat_applicable": r.vat_applicable,
            "available_from": r.available_from or "즉시입주",
            "pre_moveout_notice": r.pre_moveout_notice,
            "clean_title": bool(reg.get("clean_title", True)),
            "status_description": r.status_description,
            "options": opts,
            "image": img_url,
            "lessor_name": lsr.name if lsr else "건물 소유주",
            "lessor_phone": (lsr.phone if lsr and lsr.phone else (b.manager_phone if b else "010-2222-1111")),
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
        "demo_accounts": demo_accounts,
    }


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


@router.post("/cosign-open")
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


@router.post("/auth/login")
def portal_role_login(req: PortalLoginReq) -> Dict[str, Any]:
    """통합 본인인증 로그인 — 계정 역할(관리인/임대인/입주임차인/예비임차인) 및 공동계약 호출 목적에 맞춰 전용 URL을 반환."""
    ensure_pool_seeded()
    ensure_default_admins()

    # 1. 레거시 호환: 세션 ID/코드 직접 입장
    if req.login_mode == "cosign":
        code = req.session_code.strip()
        if not code:
            raise DomainError("공동계약 세션 ID를 입력해 주세요.")
        sess = CoSignSession.first(session_code=code) or CoSignSession.get(code)
        if not sess:
            raise NotFound(
                f"세션 '{code}'에 해당하는 공동계약을 찾을 수 없습니다.",
                hint="각자 본인 계정으로 로그인한 뒤 공동계약 화면에서 매물을 선택해 상대를 호출·응답해 주세요.",
            )
        role_param = "lessor" if req.cosign_role == "lessor" else "tenant"
        role_ko = "임대인" if role_param == "lessor" else "임차인"
        target_url = f"/rent3_contract/?role={role_param}&session={sess.id}"
        display_name = (
            sess.lessor_name
            if role_param == "lessor"
            else (str((sess.terms or {}).get("tenant_name", "")) or "계약 임차인")
        )
        return {
            "success": True,
            "role": "cosign",
            "role_label": f"공동계약 ({role_ko})",
            "redirect_url": target_url,
            "token": "",
            "user": {
                "id": sess.id,
                "username": sess.session_code,
                "name": display_name,
                "role": "cosign",
                "role_label": f"공동계약 ({role_ko})",
                "branch": sess.branch,
                "room_no": sess.room_no,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{sess.building_name} {sess.room_no}호 공동계약 화면({role_ko})으로 이동합니다.",
        }

    # 2. 일반 통합 본인인증 로그인 (관리인 / 임대인 / 임차인 / 예비임차인)
    ident = req.identifier.strip()
    ident_low = ident.lower()
    ident_num = _digits(ident)
    pw = req.password.strip()
    is_contract_hint = (req.target_hint or "").startswith("contract")

    if not ident or not pw:
        raise DomainError("아이디(또는 휴대폰 번호/성명)와 비밀번호를 모두 입력해 주세요.")

    # (A) 총괄 관리인 (Admin) 매칭
    matched_admin = next(
        (
            a for a in Admin.all()
            if a.username.lower() == ident_low
            or (a.email and a.email.lower() == ident_low)
            or a.name == ident
            or (len(ident_num) >= 8 and _digits(a.phone) == ident_num)
        ),
        None,
    )
    if matched_admin:
        core_u = CoreUser.by_username(matched_admin.username)
        admin_pw = matched_admin.password or "0202"
        valid_pw = (
            pw == "0202"
            or pw == admin_pw
            or (core_u.check_password(pw) if core_u else (pw == "1212"))
        )
        if not valid_pw:
            raise Unauthorized("비밀번호가 올바르지 않습니다.", hint="관리인 계정 비밀번호(0202)를 확인해 주세요.")
        tok = issue_token(core_u) if core_u else ""
        if is_contract_hint:
            target_url = _build_contract_target_url("lessor", req.target_hint, branch="main")
        elif req.target_hint == "pool":
            target_url = "/rent3/pool.html"
        else:
            target_url = "/rent3_admin/"
        role_lbl = "통합 Data Pool 관제" if req.target_hint == "pool" else "플랫폼 총괄 관리인 (rent3_admin)"
        return {
            "success": True,
            "role": "admin",
            "role_label": role_lbl,
            "redirect_url": target_url,
            "token": tok,
            "user": {
                "id": matched_admin.id,
                "username": matched_admin.username,
                "name": matched_admin.name,
                "phone": matched_admin.phone,
                "role": "admin",
                "role_label": role_lbl,
                "branch": "all",
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{matched_admin.name}님 본인인증 로그인 완료! {role_lbl} 페이지로 이동합니다.",
        }

    # (B) 임대인 / 건물주 (Lessor) 매칭
    matched_lessor = next(
        (
            lsr for lsr in Lessor.all()
            if (lsr.username and lsr.username.lower() == ident_low)
            or lsr.id.lower() == ident_low
            or (lsr.email and lsr.email.lower() == ident_low)
            or lsr.name == ident
            or (len(ident_num) >= 8 and _digits(lsr.phone) == ident_num)
        ),
        None,
    )
    if matched_lessor:
        core_u = CoreUser.by_username(matched_lessor.username) if matched_lessor.username else None
        phone_tail = _digits(matched_lessor.phone)[-4:] if len(_digits(matched_lessor.phone)) >= 4 else ""
        custom_pw = matched_lessor.password
        valid_pw = (
            core_u.check_password(pw)
            if core_u
            else (pw == "1212" or (bool(custom_pw) and pw == custom_pw) or (bool(phone_tail) and pw == phone_tail))
        )
        if not valid_pw:
            raise Unauthorized("임대인 비밀번호가 올바르지 않습니다.", hint="기본 비밀번호(1212) 또는 설정한 비밀번호를 확인해 주세요.")
        tok = issue_token(core_u) if core_u else ""
        if is_contract_hint:
            target_url = _build_contract_target_url("lessor", req.target_hint, branch=matched_lessor.branch)
            dest_msg = "공동계약 호출·응답 화면으로 이동합니다."
        else:
            tab_q = ""
            if req.target_hint == "add_listing":
                tab_q = "&tab=rooms"
            elif req.target_hint == "broker":
                tab_q = "&tab=broker"
            target_url = f"/rent3_lessor/?branch={matched_lessor.branch}{tab_q}"
            dest_msg = "지점 임대인 관리 페이지로 이동합니다."
        role_lbl = f"임대인 ({matched_lessor.branch})"
        return {
            "success": True,
            "role": "lessor",
            "role_label": role_lbl,
            "redirect_url": target_url,
            "token": tok,
            "user": {
                "id": matched_lessor.id,
                "username": matched_lessor.username or matched_lessor.id,
                "name": matched_lessor.name,
                "phone": matched_lessor.phone,
                "role": "lessor",
                "role_label": role_lbl,
                "branch": matched_lessor.branch,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{matched_lessor.name}님(본인인증 완료) 환영합니다. {dest_msg}",
        }

    # (C) 임차인 / 예비임차인 (Tenant) 매칭 — 입주 ACTIVE 우선 탐색
    ordered_tenants = sorted(Tenant.all(), key=lambda x: (0 if x.status == "ACTIVE" else 1, x.id))
    matched_tenant = next(
        (
            t for t in ordered_tenants
            if t.id.lower() == ident_low
            or (t.email and t.email.lower() == ident_low)
            or t.name == ident
            or (len(ident_num) >= 8 and _digits(t.phone) == ident_num)
        ),
        None,
    )
    if matched_tenant:
        phone_tail = _digits(matched_tenant.phone)[-4:] if len(_digits(matched_tenant.phone)) >= 4 else ""
        custom_pw = matched_tenant.password
        valid_pw = (
            pw == "1212"
            or (bool(custom_pw) and pw == custom_pw)
            or (bool(phone_tail) and pw == phone_tail)
        )
        if not valid_pw:
            raise Unauthorized(
                "임차인 비밀번호가 올바르지 않습니다.",
                hint="비밀번호(1212) 또는 본인 휴대폰 번호 뒷 4자리를 입력해 주세요.",
            )
        is_active_resident = matched_tenant.status == "ACTIVE" and bool(matched_tenant.current_room_id or matched_tenant.room_no)
        if is_active_resident:
            role_key = "tenant"
            role_lbl = f"입주 임차인 ({matched_tenant.room_no}호)"
            default_url = f"/rent3_tenant/?tenant={matched_tenant.id}"
        else:
            role_key = "prospect"
            role_lbl = "예비임차인"
            default_url = "/rent3_tenant/?view=listings"

        if is_contract_hint:
            target_url = _build_contract_target_url("tenant", req.target_hint, tenant_id=matched_tenant.id)
            dest_msg = "공동계약 호출·응답 화면으로 이동합니다."
        else:
            target_url = default_url
            dest_msg = f"{role_lbl} 전용 페이지로 이동합니다."

        return {
            "success": True,
            "role": role_key,
            "role_label": role_lbl,
            "redirect_url": target_url,
            "token": "",
            "user": {
                "id": matched_tenant.id,
                "username": matched_tenant.id,
                "name": matched_tenant.name,
                "phone": matched_tenant.phone,
                "role": role_key,
                "role_label": role_lbl,
                "branch": matched_tenant.branch,
                "room_no": matched_tenant.room_no,
                "tenant_id": matched_tenant.id,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"{matched_tenant.name}님(본인인증 완료) 환영합니다. {dest_msg}",
        }

    raise Unauthorized(
        "일치하는 사용자 계정을 찾을 수 없습니다.",
        hint="아이디·휴대폰 번호·성명을 다시 확인하거나 '회원가입' 탭에서 간편 본인인증 가입 후 이용해 주세요.",
    )


@router.post("/auth/register")
def portal_register(req: PortalRegisterReq) -> Dict[str, Any]:
    """OpenRent 포털 간편 본인인증 회원가입 — 예비임차인 또는 신규 임대인을 Data Pool에 등록하고 해당 역할 페이지로 즉시 연결."""
    ensure_pool_seeded()
    name = req.name.strip()
    phone = req.phone.strip()
    pw = req.password.strip() or "1212"
    is_contract_hint = (req.target_hint or "").startswith("contract")
    if not name or not phone:
        raise DomainError("성명과 휴대폰 번호는 필수 입력 항목입니다.")

    phone_digits = _digits(phone)
    if len(phone_digits) < 8:
        raise DomainError("올바른 휴대폰 번호 형식을 입력해 주세요.", hint="예: 010-1234-5678")

    if req.role == "lessor":
        uname = (req.username.strip() or f"host_{phone_digits[-4:]}").lower()
        if Lessor.first(username=uname) or Admin.first(username=uname):
            raise Conflict(f"이미 사용 중인 아이디('{uname}')입니다.", hint="다른 아이디를 입력해 주세요.")
        branch_code = normalize_branch_id(req.branch.strip() or uname)
        b_name = req.building_name.strip() or f"{name} 직거래 하우스"
        b_addr = req.address.strip() or "서울특별시 직거래 등록 건물"

        lsr = Lessor(
            username=uname,
            name=name,
            phone=phone,
            email=req.email.strip(),
            branch=branch_code,
            status="ACTIVE",
            password=pw,
        ).save("portal_lessor_registered", app_source="rent3_portal")

        if not Building.first(branch=branch_code):
            Building(
                branch=branch_code,
                building_name=b_name,
                address=b_addr,
                id_lessor=lsr.id,
                manager_phone=phone,
            ).save("portal_building_registered", app_source="rent3_portal")

        target_url = (
            _build_contract_target_url("lessor", req.target_hint, branch=branch_code)
            if is_contract_hint
            else f"/rent3_lessor/?branch={branch_code}&tab=rooms"
        )
        return {
            "success": True,
            "role": "lessor",
            "role_label": f"임대인 ({branch_code})",
            "redirect_url": target_url,
            "token": "",
            "user": {
                "id": lsr.id,
                "username": lsr.username,
                "name": lsr.name,
                "phone": lsr.phone,
                "role": "lessor",
                "role_label": f"임대인 ({branch_code})",
                "branch": branch_code,
                "login_auth_verified": True,
                "redirect_url": target_url,
            },
            "message": f"임대인 본인인증 가입이 완료되었습니다! [{b_name}] 전용 화면으로 이동합니다.",
        }

    # 예비임차인 (prospect) 가입
    existing_t = next((t for t in Tenant.all() if _digits(t.phone) == phone_digits), None)
    if existing_t:
        raise Conflict("이미 등록된 휴대폰 번호입니다.", hint="로그인 탭에서 휴대폰 번호로 바로 로그인해 주세요.")

    new_t = Tenant(
        name=name,
        phone=phone,
        email=req.email.strip(),
        status="PROSPECT",
        password=pw,
    ).save("portal_prospect_registered", app_source="rent3_portal")

    target_url = (
        _build_contract_target_url("tenant", req.target_hint, tenant_id=new_t.id)
        if is_contract_hint
        else "/rent3_tenant/?view=listings"
    )
    return {
        "success": True,
        "role": "prospect",
        "role_label": "예비임차인",
        "redirect_url": target_url,
        "token": "",
        "user": {
            "id": new_t.id,
            "username": new_t.id,
            "name": new_t.name,
            "phone": new_t.phone,
            "role": "prospect",
            "role_label": "예비임차인",
            "branch": "main",
            "room_no": "",
            "tenant_id": new_t.id,
            "login_auth_verified": True,
            "redirect_url": target_url,
        },
        "message": f"{name}님 본인인증 회원가입이 완료되었습니다! 전용 화면으로 이동합니다.",
    }
