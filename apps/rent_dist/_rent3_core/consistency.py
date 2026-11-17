"""
apps/rent_dist/_rent3_core/consistency.py — Data Pool 데이터 유일성(한 사실은 한 곳에만) 점검, 읽기 전용.

'같은 사실'이 여러 곳에 복사돼 있으면 한쪽만 고쳐졌을 때 서로 다른 값이 생긴다. 이 모듈은 알려진 복사본을 원본과 대조해 어긋난 곳을 찾는다.
원본(진짜 값)은 한 곳이다: 임대인 이름=Lessor.name, 건물명·주소=Building, 임차인 이름·연락처=Tenant, 월세·보증금(계약 기준)=Contract, 호실 번호=Room.
점검만 하고 아무것도 바꾸지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List

from .models import Admin, Building, Contract, Lessor, Pay, Room, Tenant


def _nm(s: Any) -> str:
    return re.sub(r"\s+", "", re.sub(r"\([^)]*\)", "", str(s or "")))


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def scan(limit: int = 8) -> Dict[str, Any]:
    rooms, bldgs = Room.all(), {b.id: b for b in Building.all()}
    lessors, tenants, contracts = {x.id: x for x in Lessor.all()}, {x.id: x for x in Tenant.all()}, Contract.all()
    room_by_id = {r.id: r for r in rooms}
    ct_by_id = {c.id: c for c in contracts}
    res: List[Dict[str, Any]] = []

    def add(code: str, title: str, kind: str, where: str, rows: List[str], total: int) -> None:
        res.append({"code": code, "title": title, "kind": kind, "where": where, "count": len(rows), "checked": total, "examples": rows[:limit]})

    # ── 복사본이 원본과 다른 경우 ──
    rows = []
    for r in rooms:
        b = bldgs.get(r.id_building)
        if b and r.building_name and r.building_name != b.building_name:
            rows.append(f"{r.branch} {r.room_no}호: 방에 '{r.building_name}' / 건물 '{b.building_name}'")
    add("ROOM_BLDG_NAME", "방에 적힌 건물명이 건물과 다름", "복사본", "rooms.building_name ↔ buildings.building_name", rows, len(rooms))

    rows = []
    blank = 0
    for r in rooms:
        if not r.building_name:
            blank += 1
    add("ROOM_BLDG_NAME_BLANK", "방에 건물명이 비어 있음(복사본이 있는데 채워지지 않음)", "복사본", "rooms.building_name", [f"{blank}개 호실" ] if blank else [], len(rooms))

    rows = []
    for r in rooms:
        b = bldgs.get(r.id_building)
        lsr = lessors.get(b.id_lessor) if b and b.id_lessor else None
        own = (r.registry_info or {}).get("owner_name")
        if lsr and own and _nm(own) != _nm(lsr.name):
            rows.append(f"{r.branch} {r.room_no}호: 등기부 소유자 '{own}' / 임대인 '{lsr.name}'")
    add("ROOM_OWNER_NAME", "방의 등기부 소유자 이름이 임대인 이름과 다름", "복사본(등기부 스냅샷)", "rooms.registry_info.owner_name ↔ users.Lessor.name", rows, len(rooms))

    rows = []
    for c in contracts:
        lsr = lessors.get(c.id_lessor)
        if lsr and c.lessor_name and _nm(c.lessor_name) != _nm(lsr.name):
            rows.append(f"{c.branch} {c.room_no}호 계약 {c.id}: 계약에 '{c.lessor_name}' / 임대인 '{lsr.name}'")
    add("CT_LESSOR_NAME", "계약에 적힌 임대인 이름이 임대인과 다름", "복사본", "contracts.lessor_name ↔ users.Lessor.name", rows, len(contracts))

    rows, rows_p = [], []
    for c in contracts:
        t = tenants.get(c.id_tenant)
        if t and c.status == "ACTIVE":
            if c.tenant_name and _nm(c.tenant_name) != _nm(t.name):
                rows.append(f"{c.branch} {c.room_no}호: 계약 '{c.tenant_name}' / 임차인 '{t.name}'")
            if c.tenant_phone and _digits(c.tenant_phone) != _digits(t.phone):
                rows_p.append(f"{c.branch} {c.room_no}호: 계약 {c.tenant_phone} / 임차인 {t.phone}")
    add("CT_TENANT_NAME", "계약의 임차인 이름이 임차인과 다름", "복사본", "contracts.tenant_name ↔ users.Tenant.name", rows, len(contracts))
    add("CT_TENANT_PHONE", "계약의 임차인 전화번호가 임차인과 다름(로그인·장부 대조에 쓰임)", "복사본", "contracts.tenant_phone ↔ users.Tenant.phone", rows_p, len(contracts))

    rows = []
    for c in contracts:
        r = room_by_id.get(c.id_room)
        if r and c.status == "ACTIVE" and r.active_contract_id == c.id:
            diff = [f"{k} 계약 {getattr(c, k):,}/방 {getattr(r, k):,}" for k in ("monthly_rent", "maintenance_fee", "deposit") if getattr(c, k) != getattr(r, k)]
            if diff:
                rows.append(f"{c.branch} {c.room_no}호: " + ", ".join(diff))
    add("ROOM_VS_CT_MONEY", "방의 월세·관리비·보증금이 활성 계약과 다름(방 값은 공실 표시용 복사본)", "복사본", "rooms.monthly_rent… ↔ contracts.monthly_rent…", rows, len(contracts))

    rows = []
    for c in contracts:
        t = tenants.get(c.id_tenant)
        if t and c.status == "ACTIVE" and c.tenant_business_number and t.business_number and _digits(c.tenant_business_number) != _digits(t.business_number):
            rows.append(f"{c.branch} {c.room_no}호: 계약 {c.tenant_business_number} / 임차인 {t.business_number}")
    add("CT_TENANT_BIZNO", "계약의 임차인 사업자번호가 임차인과 다름(세금계산서)", "복사본", "contracts.tenant_business_number ↔ users.Tenant.business_number", rows, len(contracts))

    # ── 참조(연결)가 서로 맞지 않는 경우 ──
    rows = []
    for t in tenants.values():
        if t.status == "ACTIVE" and t.current_room_id:
            r = room_by_id.get(t.current_room_id)
            if r is None:
                rows.append(f"{t.name}({t.id}): 입주 호실 {t.current_room_id} 가 없음")
            elif r.id_tenant != t.id or (t.room_no and t.room_no != r.room_no):
                rows.append(f"{t.name}({t.id}): 임차인 {t.room_no}호 / 방 {r.room_no}호의 임차인 {r.id_tenant}")
    add("TENANT_ROOM_LINK", "임차인↔방 연결이 서로 다름", "연결", "users.Tenant.current_room_id/room_no ↔ rooms.id_tenant", rows, len(tenants))

    rows = []
    for r in rooms:
        if r.active_contract_id:
            c = ct_by_id.get(r.active_contract_id)
            if c is None:
                rows.append(f"{r.branch} {r.room_no}호: 활성 계약 {r.active_contract_id} 가 없음")
            elif c.status != "ACTIVE" or c.id_room != r.id or c.id_tenant != r.id_tenant:
                rows.append(f"{r.branch} {r.room_no}호: 방의 계약 {c.id}({c.status}) 가 방·임차인과 맞지 않음")
    add("ROOM_CT_LINK", "방↔활성 계약 연결이 맞지 않음", "연결", "rooms.active_contract_id ↔ contracts", rows, len(rooms))

    # ── 하나뿐이어야 하는 것이 둘 이상인 경우 ──
    for code, title, items, keyf in (
        ("DUP_ACTIVE_CT", "같은 호실에 활성 계약이 둘 이상", [c for c in contracts if c.status == "ACTIVE"], lambda c: (c.branch, c.room_no)),
        ("DUP_ROOM", "같은 지점·호실번호의 방이 둘 이상", rooms, lambda r: (r.branch, r.room_no)),
        ("DUP_ACTIVE_TENANT", "같은 호실에 입주 중 임차인이 둘 이상", [t for t in tenants.values() if t.status == "ACTIVE" and t.room_no], lambda t: (t.branch, t.room_no)),
    ):
        g = defaultdict(list)
        for it in items:
            g[keyf(it)].append(getattr(it, "tenant_name", None) or getattr(it, "name", "") or it.id)
        add(code, title, "중복", code, [f"{k[0]} {k[1]}호: {', '.join(map(str, v))}" for k, v in g.items() if len(v) > 1], len(items))

    # 로그인 식별자(전화번호·아이디·이메일)가 둘 이상의 계정에 겹침 — 로그인이 엉뚱한 계정에 걸릴 수 있다
    g = defaultdict(list)
    for t in tenants.values():
        if t.status == "ACTIVE" and len(_digits(t.phone)) >= 8:
            g[_digits(t.phone)].append(f"{t.name}({t.room_no}호)")
    for lsr in lessors.values():
        if len(_digits(lsr.phone)) >= 8:
            g[_digits(lsr.phone)].append(f"임대인 {lsr.name}")
    for a in Admin.all():
        if len(_digits(a.phone)) >= 8:
            g[_digits(a.phone)].append(f"관리인 {a.name}")
    add("DUP_LOGIN_PHONE", "같은 전화번호가 둘 이상의 계정에 있음(전화번호 로그인이 엉뚱한 계정에 걸릴 수 있음)", "중복", "users.*.phone", [f"{k}: {', '.join(v)}" for k, v in g.items() if len(v) > 1], len(g))
    em = defaultdict(list)
    for who, e in [(f"임대인 {x.name}", x.email) for x in lessors.values()] + [(f"관리인 {x.name}", x.email) for x in Admin.all()] + [(f"임차인 {x.name}", x.email) for x in tenants.values()]:
        if e:
            em[e.lower()].append(who)
    add("DUP_LOGIN_EMAIL", "같은 이메일이 둘 이상의 계정에 있음", "중복", "users.*.email", [f"{k}: {', '.join(v)}" for k, v in em.items() if len(v) > 1], len(em))

    g = defaultdict(list)
    for b in bldgs.values():
        g[b.branch].append(b.building_name)
    add("DUP_BRANCH_BLDG", "같은 지점 코드에 건물이 둘 이상", "중복", "buildings.branch", [f"{k}: {', '.join(v)}" for k, v in g.items() if len(v) > 1], len(bldgs))
    g = defaultdict(list)
    for lsr in lessors.values():
        g[lsr.branch].append(lsr.name)
    add("DUP_BRANCH_LESSOR", "같은 지점 코드에 임대인이 둘 이상(건물↔임대인 참조가 모호해짐)", "중복", "users.Lessor.branch", [f"{k}: {', '.join(v)}" for k, v in g.items() if len(v) > 1], len(lessors))

    # 납부 ↔ 계약
    rows = []
    for p in Pay.all():
        c = ct_by_id.get(p.id_contract)
        if c and (p.room_no != c.room_no or (p.id_tenant and p.id_tenant != c.id_tenant)):
            rows.append(f"{p.id}: 납부 {p.room_no}호/{p.id_tenant} ↔ 계약 {c.room_no}호/{c.id_tenant}")
    add("PAY_CT_LINK", "납부의 호실·임차인이 그 계약과 다름", "연결", "payments ↔ contracts", rows, len(Pay.all()))

    bad = [x for x in res if x["count"] > 0]
    return {"checks": res, "problem_checks": len(bad), "problems": sum(x["count"] for x in bad)}
