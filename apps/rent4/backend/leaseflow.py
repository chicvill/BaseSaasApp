"""
apps/rent4/backend/leaseflow.py — 회원 임대차 계약 절차 (현장 점검 → 조건 → 정보 → 등기 → 확정 → 계약금 → 안내 → 입주).

    requested         회원이 매물에 계약 요청. 채팅 · 전화로 현장 점검 약속
    offered           임대인: 점검 뒤 조건 제시(주소 · 보증금 · 계약금 · 임차료 · 관리비 · 기간) → 임차인 계약 링크
                      (채팅창 + 임대인 휴대폰 문자앱 · 카카오톡 보내기 창). 같은 호실의 다른 요청은 마감
    tenant_approved   임차인: 개인정보(생년월일 · 현 주소 · 비상 연락처, 주민번호 없음) · 전입일 · 기간 입력 후 승인
    registry_checked  임대인: 등기부 확인 결과(체크리스트 + 파일) 전송 → 서버가 계약 내용 확정 저장(snapshot + 해시)
    tenant_confirmed  임차인: 확정 계약서 최종 승인 → 계약금 송금
    contracted        임대인: 계약금 입금 확인 = 계약 완료 → 매물 마감 + 입주 안내문 발송
    active            임대인: 입주일 잔금 확인 = 입주 (contracts.confirm_paid) → 회원 아이디로 그 호실 임차인
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import date
from typing import Dict, Optional, Tuple

from core.auth import User
from core.errors import Conflict, Forbidden, Mismatch, NotFound

from . import accounts, chat
from .models import Building, Contract
from .pool import now_iso, pool

REPORT_DEPOSIT, REPORT_RENT = 60_000_000, 300_000      # 주택 임대차 신고 대상: 보증금 6천만 원 초과 또는 월세 30만 원 초과
MAX_FILE = 8 * 1024 * 1024
FILE_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

DEFAULT_GUIDE = """[{building} {room}호 입주 안내]

1. 전기 · 가스 사용자 등록
 - 전기: 한국전력 123 또는 '한전ON' 앱에서 명의 변경 (입주일 기준)
 - 도시가스: 지역 도시가스사 고객센터에 전입 · 사용 신청 (안전 점검 일정 예약)

2. 분리수거
 - 요일 · 장소는 임대인에게 확인해 주세요. 종량제 봉투 · 재활용(종이 · 플라스틱 · 캔 · 유리) · 음식물은 따로 버립니다.

3. 전입신고 (입주 후 14일 이내)
 - 정부24(gov.kr) 온라인 또는 주민센터 방문. 확정일자도 함께 받아 두면 보증금 보호에 도움이 됩니다.

4. 주택 임대차 신고
 - 보증금 6천만 원 초과 또는 월세 30만 원 초과 계약은 계약일로부터 30일 이내 신고합니다.
 - 부동산거래관리시스템(rtms.molit.go.kr) 온라인 또는 주민센터. 전입신고 때 계약서를 내면 함께 처리됩니다.
 - {report_line}

궁금한 점은 화면 오른쪽 아래 💬 채팅으로 임대인에게 문의해 주세요."""


def _money(data: Dict, key: str) -> int:
    try:
        v = int(str(data.get(key) or 0).replace(",", ""))
    except ValueError:
        raise Mismatch(f"{key} 금액이 숫자가 아닙니다.")
    if v < 0:
        raise Mismatch("금액은 0 이상이어야 합니다.")
    return v


def _date(text, label: str) -> Optional[str]:
    if not text:
        return None
    try:
        return date.fromisoformat(str(text)).isoformat()
    except ValueError:
        raise Mismatch(f"{label} 형식이 올바르지 않습니다.", hint="YYYY-MM-DD")


def _add_months(d: str, months: int) -> str:
    y, m, day = map(int, d.split("-"))
    m0 = m - 1 + months
    y, m = y + m0 // 12, m0 % 12 + 1
    for dd in (day, 30, 29, 28):
        try:
            end = date(y, m, dd)
            break
        except ValueError:
            continue
    return date.fromordinal(end.toordinal() - 1).isoformat()          # 1년 계약: 2026-12-05 ~ 2027-12-04


def _need(c: Contract, status: str, what: str) -> None:
    if c.status != status:
        raise Conflict(f"지금은 {what} 단계가 아닙니다.", hint=f"현재 상태: {c.status}")


def _to_party(c: Contract, text: str, category: str = "안내", link: str = "contract.html", label: str = "계약 보기") -> None:
    from .contracts import _lessor_username
    m = chat.post(c.party_username, _lessor_username(c.branch) or c.issued_by or "admin",
                  f"임대인 {c.building_name}", f"[{c.building_name} {c.room}호] {text}", link=link, link_label=label)
    m.category = category
    m.save()


def _to_lessor(c: Contract, text: str, link: str = "임대인.html", label: str = "계약 보기") -> None:
    from .contracts import _lessor_username
    lessor = _lessor_username(c.branch)
    if lessor:
        m = chat.post(lessor, c.party_username, f"신청자 {c.party_name}", f"[{c.building_name} {c.room}호] {text}",
                      link=link, link_label=label)
        m.category = "문의"
        m.save()


# ── 2. 임대인: 조건 제시 ──
def offer(c: Contract, data: Dict) -> Contract:
    if c.status not in ("requested", "offered"):
        raise Conflict("요청 상태의 계약에만 조건을 제시할 수 있습니다.")
    b = Building.first(branch=c.branch)
    c.address = str(data.get("address") or (b.address if b else "") or "").strip()
    if not c.address:
        raise Mismatch("주소를 입력해 주세요.", hint="계약서와 안내문에 들어갑니다.")
    if b is not None and not b.address:
        b.address = c.address
        b.save()
    c.deposit, c.down_payment = _money(data, "deposit"), _money(data, "down_payment")
    c.rent, c.mgmt = _money(data, "rent"), _money(data, "mgmt")
    if c.down_payment > c.deposit:
        raise Mismatch("계약금은 보증금보다 클 수 없습니다.")
    c.start = _date(data.get("start"), "입주일") or c.start
    c.period_months = int(data.get("period_months") or 12)
    if not 1 <= c.period_months <= 60:
        raise Mismatch("계약 기간은 1~60개월입니다.")
    c.end = _add_months(c.start, c.period_months) if c.start else None
    c.lease_type = str(data.get("lease_type") or c.lease_type)
    c.pay_timing = str(data.get("pay_timing") or c.pay_timing)
    first = c.status == "requested"
    c.status, c.offered_at = "offered", now_iso()
    c.save()
    if first:                                 # 같은 호실의 다른 요청은 마감
        for other in Contract.find(branch=c.branch, room=c.room, status="requested"):
            if other.id != c.id:
                other.status = "cancelled"
                other.save()
                _to_party(other, "다른 분과 계약이 진행되어 요청이 마감되었습니다.", link="index.html", label="다른 매물 보기")
    _to_party(c, f"현장 점검 감사합니다. 계약 조건을 보내 드립니다 — 보증금 {c.deposit:,}원(계약금 {c.down_payment:,}원), "
                 f"월 {c.rent:,}원 + 관리비 {c.mgmt:,}원, {c.start or '입주일 협의'}부터 {c.period_months}개월. "
                 f"계약 페이지에서 확인하고 정보를 입력해 주세요.", link="contract.html", label="📝 계약 페이지 열기")
    return c


def offer_message(c: Contract) -> Dict:
    """임대인 휴대폰 문자앱 · 카카오톡으로 보낼 문구 (서버는 문자를 보내지 않는다)."""
    text = (f"[Rent4] {c.party_name}님, {c.building_name} {c.room}호 계약 조건입니다.\n"
            f"주소: {c.address}\n보증금 {c.deposit:,}원 (계약금 {c.down_payment:,}원)\n"
            f"월 임차료 {c.rent:,}원 + 관리비 {c.mgmt:,}원\n입주 {c.start or '협의'} · {c.period_months}개월\n"
            f"아래 계약 페이지에서 확인하고 정보를 입력해 주세요.")
    return {"message": text, "link": "contract.html", "phone": c.party_phone, "name": c.party_name}


# ── 3. 임차인: 정보 입력 · 승인 ──
def approve(c: Contract, data: Dict) -> Contract:
    _need(c, "offered", "정보 입력")
    if not data.get("agree"):
        raise Mismatch("계약 조건에 동의해야 승인됩니다.")
    birth = _date(data.get("birth"), "생년월일")
    addr = str(data.get("address") or "").strip()
    if not birth or not addr:
        raise Mismatch("생년월일과 현 주소를 입력해 주세요.")
    if re.search(r"\d{6}\s*-?\s*[1-4]\d{6}", json.dumps(data, ensure_ascii=False)):
        raise Mismatch("주민등록번호는 입력하지 않습니다.", hint="생년월일만 적어 주세요.")
    c.tenant_birth, c.tenant_address = birth, addr[:200]
    c.emergency_contact = str(data.get("emergency") or "").strip()[:100]
    c.start = _date(data.get("start"), "전입일") or c.start
    c.period_months = int(data.get("period_months") or c.period_months or 12)
    if not 1 <= c.period_months <= 60:
        raise Mismatch("계약 기간은 1~60개월입니다.")
    if not c.start:
        raise Mismatch("전입일(입주일)을 정해 주세요.")
    c.end = _add_months(c.start, c.period_months)
    c.status, c.tenant_approved_at = "tenant_approved", now_iso()
    c.save()
    _to_lessor(c, f"임차인이 정보를 입력하고 승인했습니다 (전입 {c.start}, {c.period_months}개월). 등기부를 확인해 결과를 보내 주세요.")
    return c


# ── 4. 임대인: 등기 확인 → 계약 내용 확정 저장 ──
def _registry_dir() -> str:
    d = os.path.join(pool().directory, "registry")
    os.makedirs(d, exist_ok=True)
    return d


def registry(c: Contract, data: Dict) -> Contract:
    _need(c, "tenant_approved", "등기 확인")
    owner_match = bool(data.get("owner_match"))
    check = {"owner_match": owner_match, "owner_name": str(data.get("owner_name") or "").strip()[:50],
             "mortgage": _money(data, "mortgage"), "seizure": bool(data.get("seizure")),
             "note": str(data.get("note") or "").strip()[:1000], "viewed_on": _date(data.get("viewed_on"), "열람일") or date.today().isoformat()}
    if not check["owner_name"]:
        raise Mismatch("등기부상 소유자 이름을 적어 주세요.")
    if data.get("file"):
        mime = str(data.get("mime") or "")
        ext = FILE_TYPES.get(mime)
        if not ext:
            raise Mismatch("등기부 파일은 PDF 또는 사진만 올릴 수 있습니다.")
        raw = base64.b64decode(str(data["file"]).split(",", 1)[-1])
        if len(raw) > MAX_FILE:
            raise Mismatch("파일이 너무 큽니다 (8MB 까지).")
        name = f"{c.id}{ext}"
        with open(os.path.join(_registry_dir(), name), "wb") as f:
            f.write(raw)
        c.registry_file = name
    c.registry, c.registry_at = check, now_iso()
    c.status = "registry_checked"
    _finalize(c)
    c.save()
    warn = [] if owner_match else ["소유자 불일치"]
    if check["seizure"]:
        warn.append("압류 · 가압류 있음")
    _to_party(c, "임대인이 등기부 확인 결과를 보냈고, 계약 내용이 확정 저장되었습니다. 확인 후 최종 승인해 주세요."
                 + (f" ⚠️ {', '.join(warn)}" if warn else ""), category="안내")
    return c


def _finalize(c: Contract) -> None:
    """확정 계약서: 이후 바뀌지 않는 내용과 해시 (위변조 확인용)."""
    b = Building.first(branch=c.branch)
    c.report_required = c.deposit > REPORT_DEPOSIT or c.rent > REPORT_RENT
    c.snapshot = {
        "contract_id": c.id, "building": c.building_name, "address": c.address or (b.address if b else ""), "room": c.room,
        "lease_type": c.lease_type, "pay_timing": c.pay_timing, "deposit": c.deposit, "down_payment": c.down_payment,
        "balance": c.deposit - c.down_payment, "rent": c.rent, "mgmt": c.mgmt, "start": c.start, "end": c.end,
        "period_months": c.period_months,
        "lessor": {"username": _lessor_name(c)},
        "tenant": {"name": c.party_name, "phone": c.party_phone, "birth": c.tenant_birth, "address": c.tenant_address,
                   "emergency": c.emergency_contact},
        "registry": c.registry, "registry_file": bool(c.registry_file),
        "report_required": c.report_required,
        "offered_at": c.offered_at, "tenant_approved_at": c.tenant_approved_at, "registry_at": c.registry_at,
    }
    c.finalized_at = now_iso()
    c.snapshot["finalized_at"] = c.finalized_at
    c.snapshot_hash = hashlib.sha256(json.dumps(c.snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _lessor_name(c: Contract) -> str:
    from .contracts import _lessor_username
    u = User.by_username(_lessor_username(c.branch) or "")
    return (u.name or u.username) if u else c.building_name


def registry_file(c: Contract) -> Tuple[str, str]:
    if not c.registry_file:
        raise NotFound("올린 등기부 파일이 없습니다.")
    path = os.path.join(_registry_dir(), c.registry_file)
    if not os.path.exists(path):
        raise NotFound("등기부 파일을 찾을 수 없습니다.")
    mime = next((m for m, e in FILE_TYPES.items() if c.registry_file.endswith(e)), "application/octet-stream")
    return path, mime


# ── 5. 임차인: 최종 승인 ──
def confirm(c: Contract, data: Dict) -> Contract:
    _need(c, "registry_checked", "최종 승인")
    if not data.get("agree"):
        raise Mismatch("확정 계약서에 동의해야 승인됩니다.")
    if data.get("hash") and data["hash"] != c.snapshot_hash:
        raise Conflict("계약 내용이 바뀌었습니다. 새로 고친 뒤 다시 확인해 주세요.")
    c.status, c.confirmed_at = "tenant_confirmed", now_iso()
    c.save()
    _to_lessor(c, f"임차인이 확정 계약서를 최종 승인했습니다. 계약금 {c.down_payment:,}원 입금을 확인해 주세요.")
    return c


# ── 6. 임대인: 계약금 확인 = 계약 완료 → 안내문 ──
def guide_for(c: Contract) -> str:
    b = Building.first(branch=c.branch)
    template = (b.guide if b and b.guide.strip() else DEFAULT_GUIDE)
    report_line = ("✅ 이 계약은 신고 대상입니다 (보증금 6천만 원 초과 또는 월세 30만 원 초과). 계약일로부터 30일 안에 신고해 주세요."
                   if c.report_required else "이 계약은 금액 기준으로는 신고 대상이 아닙니다.")
    try:
        return template.format(building=c.building_name, room=c.room, address=c.address, start=c.start or "",
                               report_line=report_line)
    except (KeyError, IndexError, ValueError):                         # 임대인이 고친 문안에 다른 {} 가 있으면 그대로
        return template + "\n\n" + report_line


def down_paid(c: Contract) -> Contract:
    _need(c, "tenant_confirmed", "계약금 확인")
    from . import listings
    c.down_paid_at = now_iso()
    c.status = "contracted"
    c.guide_text = guide_for(c)
    c.save()
    l = listings.open_listing(c.branch, c.room)
    if l:
        listings.close(c.branch, l.id, "contracted")
    _to_party(c, f"계약금 입금이 확인되어 계약이 완료되었습니다 🎉 입주일({c.start}) 잔금 {c.deposit - c.down_payment:,}원 확인 후 "
                 f"입주 처리됩니다.\n\n{c.guide_text}", link=f"contract_doc.html?id={c.id}", label="📄 계약서 보기")
    return c


# ── 계약서 보기 (양쪽 당사자 · 관리인) ──
def can_view(user: User, c: Contract) -> bool:
    if user.role in accounts.ADMIN_ROLES:
        return True
    if user.role in accounts.LESSOR_ROLES:
        return user.branch == c.branch
    return user.username in (c.party_username, c.username)


def document(user: User, contract_id: str) -> Dict:
    c = Contract.first(id=contract_id)
    if c is None or not can_view(user, c):
        raise Forbidden("볼 수 없는 계약서입니다.")
    if not c.snapshot:
        raise NotFound("아직 확정되지 않은 계약입니다.", hint="임대인이 등기 확인 결과를 보내면 확정됩니다.")
    return {"snapshot": c.snapshot, "hash": c.snapshot_hash, "status": c.status, "confirmed_at": c.confirmed_at,
            "down_paid_at": c.down_paid_at, "paid_at": c.paid_at, "guide": c.guide_text}
