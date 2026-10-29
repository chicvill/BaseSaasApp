"""
apps/_rent3_core/broker.py — 부동산중개사 개입 지면계약서 사진 첨부 및 공동 Data Pool 원장 일괄 적용 객체.

역할:
  1. `BrokerContractTextParser`: 스마트폰 카메라 OCR(라이브 텍스트)로 복사한 지면계약서 문구에서
     보증금·월세·관리비·임차인명·연락처·입주일·중개사무소명을 자동 추출하여 임대인의 수기 입력 부담을 줄인다.
  2. `BrokerContractOnboarding`: 첨부된 계약서 사진(Data URL / 바이너리)을 검증한 뒤
     `data/rent3_pool/uploads/contracts/{contract_id}.{ext}`에 원자적으로 저장하고,
     `users.json`, `contracts.json`, `rooms.json`, `payments.json`, `ops.json(RTMS)`를 즉시 일괄 동기화한다.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from core.errors import DomainError, NotFound
from .models import (
    Building,
    Contract,
    Lessor,
    Pay,
    Room,
    Tenant,
    add_months_str,
    date_today,
    normalize_branch_id,
)
from .ops import DirectChat, RtmsReport
from .pool import now_iso, pool

ALLOWED_MIME_TO_EXT = {
    "image/jpeg": ("jpg", "image/jpeg"),
    "image/jpg": ("jpg", "image/jpeg"),
    "image/png": ("png", "image/png"),
    "image/webp": ("webp", "image/webp"),
    "image/gif": ("gif", "image/gif"),
    "application/pdf": ("pdf", "application/pdf"),
}
EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB


def _parse_korean_money(fragment: str) -> Optional[int]:
    """'1,000만원', '500만', '520,000원', '1억 2,000만원' 등을 원 단위 정수로 변환."""
    s = (fragment or "").replace(",", "").strip()
    if not s:
        return None
    total = 0
    matched = False
    m_eok = re.search(r"(\d+(?:\.\d+)?)\s*억", s)
    if m_eok:
        total += int(round(float(m_eok.group(1)) * 100_000_000))
        matched = True
    m_cheon = re.search(r"(\d+(?:\.\d+)?)\s*천만", s)
    if m_cheon:
        total += int(round(float(m_cheon.group(1)) * 10_000_000))
        matched = True
    else:
        m_man = re.search(r"(\d+(?:\.\d+)?)\s*만", s)
        if m_man:
            total += int(round(float(m_man.group(1)) * 10_000))
            matched = True
    if matched:
        return total
    m_raw = re.search(r"(\d{4,})", s)
    return int(m_raw.group(1)) if m_raw else None


class BrokerContractTextParser:
    """스마트폰 카메라 라이브텍스트(OCR)로 복사한 종이 임대차계약서 문구 자동 파서."""

    @staticmethod
    def parse(text: str) -> Dict[str, Any]:
        s = (text or "").strip()
        if not s:
            raise DomainError("추출할 계약서 문구가 비어 있습니다.", hint="계약서 사진에서 복사한 텍스트를 붙여넣어 주세요.")

        out: Dict[str, Any] = {}

        # 호실 번호 (예: 203호)
        m_room = re.search(r"\b([1-9]\d{2})\s*호", s)
        if m_room:
            out["room_no"] = m_room.group(1)

        # 보증금
        m_dep = re.search(r"보증금[^\d\n]{0,12}([0-9,.\s억천만원]+)", s)
        if m_dep:
            val = _parse_korean_money(m_dep.group(1))
            if val is not None:
                out["deposit"] = val

        # 월세 / 차임
        m_rent = re.search(r"(?:월세|차임)[^\d\n]{0,12}([0-9,.\s억천만원]+)", s)
        if m_rent:
            val = _parse_korean_money(m_rent.group(1))
            if val is not None:
                out["monthly_rent"] = val

        # 관리비
        m_maint = re.search(r"관리비[^\d\n]{0,12}([0-9,.\s억천만원]+)", s)
        if m_maint:
            val = _parse_korean_money(m_maint.group(1))
            if val is not None:
                out["maintenance_fee"] = val

        # 계약금
        m_cdep = re.search(r"계약금[^\d\n]{0,12}([0-9,.\s억천만원]+)", s)
        if m_cdep:
            val = _parse_korean_money(m_cdep.group(1))
            if val is not None:
                out["contract_deposit"] = val

        # 선불 / 후불, 전세 / 월세
        if "선불" in s:
            out["pay_timing"] = "선불"
        elif "후불" in s:
            out["pay_timing"] = "후불"

        if "전세" in s and "월세" not in s:
            out["lease_type"] = "전세"
        else:
            out["lease_type"] = "월세"

        # 입주일 / 잔금일 / 인도일
        m_date = re.search(r"(?:입주일|인도일|잔금일|전입일)?[^\d\n]{0,10}(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", s)
        if m_date:
            out["move_in_date"] = f"{int(m_date.group(1)):04d}-{int(m_date.group(2)):02d}-{int(m_date.group(3)):02d}"

        # 연락처 (010-xxxx-xxxx)
        phones = re.findall(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}", s)
        if phones:
            out["tenant_phone"] = re.sub(r"\s+", "-", phones[0])
            if len(phones) >= 2:
                out["broker_phone"] = re.sub(r"\s+", "-", phones[1])

        # 임차인 성명
        m_tname = re.search(r"임차인(?:\s*\(을\))?\s*[:：]?\s*([가-힣]{2,5})", s)
        if m_tname:
            out["tenant_name"] = m_tname.group(1)

        # 공인중개사 사무소명
        m_agency = re.search(r"([가-힣A-Za-z0-9]{2,15}(?:공인중개사사무소|공인중개사|부동산중개법인|부동산))", s)
        if m_agency:
            out["broker_agency_name"] = m_agency.group(1)

        m_agent = re.search(
            r"(?:대표공인중개사|개업공인중개사|대표중개사|대표|중개사)(?!사무소)\s*(?:[:：]\s*|\s+)([가-힣]{2,4})",
            s,
        )
        if m_agent:
            out["broker_agent_name"] = m_agent.group(1)

        return out


class BrokerContractOnboarding:
    """공인중개사 개입 지면계약서 사진 저장 및 Data Pool 원장 일괄 적용 서비스 객체."""

    @staticmethod
    def _save_photo_file(contract_id: str, photo_data_url: str, original_name: str = "") -> Tuple[str, str]:
        raw_url = (photo_data_url or "").strip()
        if not raw_url:
            raise DomainError(
                "공인중개사 계약서 사진 파일이 첨부되지 않았습니다.",
                hint="촬영하거나 스캔한 임대차계약서 이미지(JPG/PNG/WEBP/PDF)를 먼저 첨부해 주세요.",
            )

        m = re.match(r"^data:([a-zA-Z0-9.+/-]+);base64,(.+)$", raw_url, re.DOTALL)
        if not m:
            raise DomainError(
                "첨부된 계약서 파일 형식이 올바르지 않습니다.",
                hint="이미지 파일(JPG, PNG, WEBP) 또는 PDF 스캔본을 선택해 주세요.",
            )

        mime = m.group(1).lower()
        if mime not in ALLOWED_MIME_TO_EXT:
            raise DomainError(
                f"지원하지 않는 파일 형식입니다 ({mime}).",
                hint="허용 형식: JPG, PNG, WEBP, GIF, PDF",
            )

        try:
            binary = base64.b64decode(m.group(2), validate=False)
        except Exception:
            raise DomainError("계약서 이미지 디코딩에 실패했습니다.", hint="파일을 다시 선택해 주세요.")

        if len(binary) == 0:
            raise DomainError("비어 있는 이미지 파일입니다.")
        if len(binary) > MAX_PHOTO_BYTES:
            raise DomainError("계약서 이미지 용량은 최대 10MB 이하만 허용됩니다.")

        ext, _ = ALLOWED_MIME_TO_EXT[mime]
        sha256_hex = hashlib.sha256(binary).hexdigest()

        rel_dir = os.path.join("uploads", "contracts")
        abs_dir = os.path.join(pool().pool_dir, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        filename = f"{contract_id}.{ext}"
        abs_path = os.path.join(abs_dir, filename)
        tmp_path = f"{abs_path}.{os.getpid()}.tmp"
        with open(tmp_path, "wb") as fp:
            fp.write(binary)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, abs_path)

        rel_posix = f"uploads/contracts/{filename}"
        return rel_posix, sha256_hex

    @staticmethod
    def resolve_photo_file(contract_id: str) -> Tuple[str, str, str]:
        """저장된 계약서 사진의 (절대경로, MIME, 다운로드파일명)을 안전하게 반환."""
        ct = Contract.require(contract_id)
        if not ct.contract_photo_file:
            raise NotFound(f"계약 '{contract_id}'에는 첨부된 지면계약서 사진이 없습니다.")

        pool_root = os.path.abspath(pool().pool_dir)
        candidate = os.path.abspath(os.path.join(pool_root, ct.contract_photo_file))
        if not candidate.startswith(pool_root + os.sep) or not os.path.isfile(candidate):
            raise NotFound("계약서 원본 사진 파일을 디스크에서 찾을 수 없습니다.")

        _, ext = os.path.splitext(candidate.lower())
        mime = EXT_TO_MIME.get(ext, "application/octet-stream")
        dl_name = ct.contract_photo_name or os.path.basename(candidate)
        return candidate, mime, dl_name

    @classmethod
    def register(
        cls,
        *,
        branch: str,
        room_no: str,
        photo_data_url: str,
        photo_filename: str = "",
        tenant_name: str,
        tenant_phone: str,
        tenant_car_number: str = "",
        lease_type: str = "월세",
        pay_timing: str = "후불",
        deposit: int = 5000000,
        monthly_rent: int = 500000,
        maintenance_fee: int = 50000,
        contract_deposit: int = 300000,
        deposit_received: bool = True,
        contract_date: Optional[str] = None,
        move_in_date: Optional[str] = None,
        lease_term_months: int = 12,
        special_terms: str = "",
        broker_agency_name: str = "",
        broker_agent_name: str = "",
        broker_reg_no: str = "",
        broker_phone: str = "",
        rtms_filed_by_broker: bool = True,
        fixed_date_no: str = "",
        app_source: str = "rent3_lessor",
    ) -> Dict[str, Any]:
        b_id = normalize_branch_id(branch)
        if not (tenant_name or "").strip() or not (tenant_phone or "").strip():
            raise DomainError("임차인 성명과 연락처는 필수 입력 항목입니다.")

        room = Room.first(branch=b_id, room_no=str(room_no))
        if not room:
            raise NotFound(f"{b_id} 지점에서 '{room_no}호'를 찾을 수 없습니다.")

        bldg = Building.get(room.id_building) or Building.first(branch=b_id)
        lessor = Lessor.get(bldg.id_lessor) if (bldg and bldg.id_lessor) else Lessor.first(branch=b_id)

        ct_id = f"ct_brk_{uuid4().hex[:7]}"
        rel_photo, photo_hash = cls._save_photo_file(ct_id, photo_data_url, photo_filename)

        # 기존 활성 계약이 있는 호실(중개 재계약/교체)인 경우 기존 계약 마감 처리
        if room.active_contract_id:
            old_ct = Contract.get(room.active_contract_id)
            if old_ct and old_ct.status == "ACTIVE":
                old_ct.status = "RENEWED"
                old_ct.save(action="contract_replaced_by_broker_paper", app_source=app_source)

        # 1) Tenant 생성 또는 활성화 -> users.json 즉시 업데이트
        t_phone = tenant_phone.strip()
        tenant = Tenant.first(phone=t_phone)
        if not tenant:
            tenant = Tenant(
                name=tenant_name.strip(),
                phone=t_phone,
                car_number=tenant_car_number.strip(),
                doorlock_pwd=room.doorlock_pwd,
                branch=b_id,
                id_building=bldg.id if bldg else "",
                current_room_id=room.id,
                room_no=room.room_no,
                status="ACTIVE",
            )
        else:
            tenant.name = tenant_name.strip()
            if tenant_car_number.strip():
                tenant.car_number = tenant_car_number.strip()
            tenant.doorlock_pwd = room.doorlock_pwd
            tenant.branch = b_id
            tenant.id_building = bldg.id if bldg else tenant.id_building
            tenant.current_room_id = room.id
            tenant.room_no = room.room_no
            tenant.status = "ACTIVE"
        tenant.save(action="tenant_activated_by_broker_contract", app_source=app_source)

        # 2) Contract 원장 생성 -> contracts.json 즉시 업데이트
        c_date = (contract_date or "").strip() or date_today().isoformat()
        m_in = (move_in_date or "").strip() or c_date
        months = max(1, int(lease_term_months or 12))
        expiry = add_months_str(m_in, months)

        broker_meta = {
            "agency_name": broker_agency_name.strip() or "개업공인중개사사무소",
            "agent_name": broker_agent_name.strip(),
            "reg_no": broker_reg_no.strip(),
            "phone": broker_phone.strip(),
            "rtms_filed_by_broker": bool(rtms_filed_by_broker),
            "photo_sha256": photo_hash,
        }

        try:
            c_dt_obj = date.fromisoformat(c_date[:10])
        except ValueError:
            c_dt_obj = date_today()
        resolved_fix_no = (
            fixed_date_no.strip()
            or (f"FIX-BRK-{c_dt_obj.strftime('%Y%m%d')}-{uuid4().hex[:5].upper()}" if rtms_filed_by_broker else "")
        )

        ct = Contract(
            id=ct_id,
            branch=b_id,
            id_building=bldg.id if bldg else "",
            id_room=room.id,
            room_no=room.room_no,
            id_lessor=lessor.id if lessor else "",
            lessor_name=lessor.name if lessor else "임대인",
            id_tenant=tenant.id,
            tenant_name=tenant.name,
            tenant_phone=tenant.phone,
            contract_date=c_date,
            move_in_date=m_in,
            expiry_date=expiry,
            lease_term_months=months,
            lease_type=lease_type,
            pay_timing=pay_timing,
            vat_target=bool(room.vat_applicable),
            vat_separate=True,
            contract_deposit=int(contract_deposit or 0),
            deposit=int(deposit or 0),
            monthly_rent=0 if lease_type == "전세" else int(monthly_rent or 0),
            maintenance_fee=int(maintenance_fee or 0),
            special_terms=special_terms.strip() or f"[공인중개사 중개 계약] {broker_meta['agency_name']} 입회 하 지면 체결 원본 첨부",
            option_snapshot=room.option_list,
            registry_snapshot=room.registry_info,
            lessor_signature=f"지면계약날인({lessor.name if lessor else '임대인'})",
            tenant_signature=f"지면계약날인({tenant.name})",
            integrity_hash=photo_hash,
            contract_channel="BROKER_PAPER",
            contract_photo_file=rel_photo,
            contract_photo_name=photo_filename or os.path.basename(rel_photo),
            broker_info=broker_meta,
            fixed_date_no=resolved_fix_no,
            status="ACTIVE",
        )
        ct.vat = ct.compute_vat()
        ct.save(action="broker_paper_contract_registered", app_source=app_source)

        # 3) Room 호실 상태 입주 배정 및 직거래 공실 광고 자동 비노출 -> rooms.json 즉시 업데이트
        room.id_tenant = tenant.id
        room.active_contract_id = ct.id
        room.deposit = ct.deposit
        room.monthly_rent = ct.monthly_rent
        room.maintenance_fee = ct.maintenance_fee
        room.lease_type = ct.lease_type
        room.pay_timing = ct.pay_timing
        room.is_published = False
        room.save(action="room_assigned_by_broker_contract", app_source=app_source)

        # 4) 계약금 수령 기록 -> payments.json 즉시 업데이트
        if deposit_received and ct.contract_deposit > 0:
            Pay(
                branch=b_id,
                id_building=ct.id_building,
                id_room=room.id,
                room_no=room.room_no,
                id_contract=ct.id,
                id_tenant=tenant.id,
                payer_name=tenant.name,
                billing_month=m_in[:7],
                paid_date=c_date,
                total_amount=ct.contract_deposit,
                payment_kind="CONTRACT_DEPOSIT",
                method="TRANSFER",
                idempotency_key=f"broker_dep:{ct.id}",
            ).save(action="broker_contract_deposit_recorded", app_source=app_source)

        # 5) 국토부 RTMS 전월세 신고 상태 기록 -> ops.json 즉시 업데이트
        deadline = (c_dt_obj + timedelta(days=30)).isoformat()
        mandatory = (ct.deposit > 60_000_000) or (ct.monthly_rent > 300_000)
        rtms = RtmsReport(
            branch=b_id,
            id_contract=ct.id,
            id_room=room.id,
            room_no=room.room_no,
            lessor_name=ct.lessor_name,
            tenant_name=ct.tenant_name,
            deposit=ct.deposit,
            monthly_rent=ct.monthly_rent,
            contract_date=ct.contract_date,
            deadline_date=deadline,
            mandatory=mandatory,
            status="FILED" if rtms_filed_by_broker else "PENDING",
            fixed_date_no=resolved_fix_no,
            filed_at=now_iso() if rtms_filed_by_broker else "",
        ).save(action="rtms_recorded_for_broker_contract", app_source=app_source)

        # 6) 세입자 안내 채팅 등록 -> ops.json
        DirectChat(
            branch=b_id,
            id_room=room.id,
            room_no=room.room_no,
            sender_role="system",
            sender_name="계약원장 등재 안내",
            message=(
                f"[📸 공인중개사 계약서 등록 완료] {broker_meta['agency_name']} 통해 체결된 {room.room_no}호 계약서 사본 및 조건이 "
                f"장부에 반영되었습니다. 임차인 앱 마이홈에서 계약서 사본·도어락·납부현황을 확인하실 수 있습니다."
            ),
        ).save(action="chat_auto_sent", app_source=app_source)

        return {
            "contract": ct.model_dump(),
            "tenant": tenant.model_dump(),
            "room": room.model_dump(),
            "rtms": rtms.model_dump(),
        }
