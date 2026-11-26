"""
apps/_rent3_core/domains/models_contract.py — 임대차 계약 원장 객체(Contract) 도메인 정의 (`contracts.json`).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import Field

from core.errors import Mismatch
from ..pool import PoolObj
from .models_finance import Pay
from .models_utils import current_ym, date_today, index_to_ym, parse_ym, ym_index

if TYPE_CHECKING:
    from .models_property import Room


class Contract(PoolObj):
    normalize_phones = False  # 장부 대조(진위 확인) 대상: 원문 보존
    """임대차 계약 원장 객체 — rent2의 후불 비과세·가짜체납 방지·FIFO 청구 계산 규칙 내장."""
    collection: ClassVar[str] = "contracts"
    label: ClassVar[str] = "임대계약"

    id: str = Field(default_factory=lambda: f"ct_{uuid4().hex[:8]}")
    branch: str = "main"
    id_building: str = ""
    id_room: str = ""
    room_no: str = ""
    id_lessor: str = ""
    lessor_name: str = ""
    id_tenant: str = ""
    tenant_name: str = ""
    tenant_phone: str = ""
    payer_names: List[str] = Field(default_factory=list)   # 송금자명(임차인 이름과 다른 이름으로 입금되는 경우, 최대 3개): 문자 수납 때 임차인명과 똑같이 인정
    tax_invoice_requested: bool = False      # 세입자가 세금계산서 발행을 요청한 계약인지 여부 (사무실·기숙사 등 모든 호실 적용 가능)
    tenant_business_number: str = ""         # 임차인 사업자등록번호 (세금계산서 발행 요청 시 입력)
    tenant_company_name: str = ""            # 임차인 상호/법인명
    tenant_ceo_name: str = ""                # 임차인 대표자명
    tenant_email: str = ""                   # 임차인 세금계산서 수신 이메일
    tenant_business_address: str = ""        # 임차인 사업자등록증상 사업장 주소 (미입력 시 해당 호실 주소 사용)
    contract_date: str = Field(default_factory=lambda: date_today().isoformat())
    move_in_date: str = ""
    expiry_date: str = ""
    move_out_date: str = ""
    lease_term_months: int = 12
    lease_type: str = "월세"                 # 월세 / 전세
    pay_timing: str = "후불"                 # 후불 / 선불
    vat_target: bool = False                 # 주거용 원룸 False(면세), 상가/업무용 True(과세)
    vat_separate: bool = True                # 과세 시 10% 별도 청구 여부
    contract_deposit: int = 300000           # 계약금
    deposit: int = 5000000                   # 보증금
    # 출입 정보(도어락·공동현관) 교부 여부: None=예전 계약(공개), False=잠금(잔금 확인 전), True=임대인이 잔금 입금을 확인하고 교부
    keys_released: Optional[bool] = None
    mortgage_cleanup_required: bool = False   # 잔금 시 근저당 말소 특약이 있는 계약 — 잔금일 등기부에서 말소가 확인돼야 교부
    # 잔금일 등기부 재확인(임차인이 직접 열람): {checked_at, changed, issue_number} — 변동 없음으로 확인돼야 임대인이 출입 정보를 교부할 수 있다
    balance_registry: Dict[str, Any] = Field(default_factory=dict)
    # '보증보험 가입 예정'으로 진행한 계약: 잔금일 전에 임차인이 보험 증권 정보를 제출해야 교부 가능 {issuer, policy_no, submitted_at}
    insurance_proof_required: bool = False
    insurance_proof: Dict[str, Any] = Field(default_factory=dict)
    keys_released_at: str = ""
    keys_released_by: str = ""
    monthly_rent: int = 500000               # 월 임차료
    maintenance_fee: int = 50000             # 월 관리비
    vat: int = 0                             # 월 부가세
    special_terms: str = ""
    lessor_memo: str = ""                    # 임대인 메모(연락처·특이사항)
    option_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    registry_snapshot: Dict[str, Any] = Field(default_factory=dict)
    lessor_signature: str = ""
    tenant_signature: str = ""
    integrity_hash: str = ""
    cosign_session_id: str = ""
    chat_digest: str = ""                    # 체결 때 계약방 협상 기록(불변 필드만)의 SHA-256 — 열람해도 바뀌지 않는다
    chat_digest_count: int = 0
    chat_purged_at: str = ""                # 보관 기간(계약 종료 후 5년) 만료로 협상 대화를 지운 시각 — 있으면 '해시 변조'가 아니라 '기간 만료 삭제'
    ended_at: str = ""                       # 계약 종료(퇴실·해지·재계약 마감) 확정일 — 대화 보관 5년의 기준
    contract_channel: str = "DIRECT"         # DIRECT (직거래 공동계약) / BROKER_PAPER (공인중개사 지면계약 사진등록)
    contract_photo_file: str = ""            # data/rent3_pool/ 기준 상대 경로 (예: uploads/contracts/ct_xxx.jpg)
    contract_photo_name: str = ""            # 원본 업로드 파일명
    broker_info: Dict[str, Any] = Field(default_factory=dict)  # 중개사무소 상호, 공인중개사명, 등록번호, 연락처 등
    fixed_date_no: str = ""                  # 대법원 확정일자 번호 (FIX-YYYYMMDD-XXXXXX)
    cleaning_fee: int = 0                    # 퇴실 청소비 약정액: 계약서에 '퇴실할 때 임차인이 청소비를 부담'하기로 한 금액. 전출 정산 때 보증금에서 이 금액만 공제한다(0 = 약정 없음)
    renews_contract_id: str = ""             # 연장(재계약) 계약이면 이어받는 이전 계약 id. 만기 전에 미리 체결하면 SCHEDULED 로 두었다가 시작일에 이전 계약을 RENEWED 로 닫고 ACTIVE 가 된다
    status: str = "ACTIVE"                   # ACTIVE / TERMINATED / RENEWED / SCHEDULED(미리 체결한 예약 계약: 새 입주자 또는 연장) / CANCELLED(예약 취소)

    def summary(self) -> str:
        ch = "중개계약" if self.contract_channel == "BROKER_PAPER" else "직거래"
        return f"계약[{ch}] {self.room_no}호 {self.tenant_name} ({self.lease_type}/{self.pay_timing})"

    def compute_vat(self) -> int:
        if not self.vat_target or self.lease_type == "전세":
            return 0
        base = self.monthly_rent + self.maintenance_fee
        if self.vat_separate:
            return round(base * 0.1)
        return round(base - (base / 1.1))

    def days_to_expiry(self) -> Optional[int]:
        if not self.expiry_date:
            return None
        try:
            exp = date.fromisoformat(self.expiry_date[:10])
            return (exp - date_today()).days
        except ValueError:
            return None

    def expiry_window_status(self) -> Dict[str, Any]:
        """주택임대차보호법 제6조 만기 6개월(180일)~2개월(60일) 전 전출확인·재계약예고 구간 판별."""
        days = self.days_to_expiry()
        if days is None or not self.expiry_date:
            return {
                "days_to_expiry": None,
                "months_to_expiry": None,
                "window_state": "UNKNOWN",
                "in_6_to_2_months_window": False,
                "notice_open_date": "",
                "notice_deadline_date": "",
                "days_to_notice_deadline": None,
            }
        try:
            exp = date.fromisoformat(self.expiry_date[:10])
        except ValueError:
            return {
                "days_to_expiry": days,
                "months_to_expiry": None,
                "window_state": "UNKNOWN",
                "in_6_to_2_months_window": False,
                "notice_open_date": "",
                "notice_deadline_date": "",
                "days_to_notice_deadline": None,
            }

        open_dt = exp - timedelta(days=180)
        deadline_dt = exp - timedelta(days=60)
        days_to_deadline = (deadline_dt - date_today()).days
        if days < 0:
            w_state = "EXPIRED"
        elif days < 60:
            w_state = "TACIT_LOCKED"
        elif days <= 180:
            w_state = "NOTICE_WINDOW"
        else:
            w_state = "EARLY"

        return {
            "days_to_expiry": days,
            "months_to_expiry": round(days / 30.0, 1),
            "window_state": w_state,
            "in_6_to_2_months_window": (60 <= days <= 180),
            "notice_open_date": open_dt.isoformat(),
            "notice_deadline_date": deadline_dt.isoformat(),
            "days_to_notice_deadline": days_to_deadline,
        }

    def payment_day(self) -> int:
        """매월 약정 임대료 납입일(= 입주일 move_in_date의 일자, 1~31)."""
        m = re.search(r"\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*(\d{1,2})", self.move_in_date or "")
        if m:
            d = int(m.group(1))
            if 1 <= d <= 31:
                return d
        return 1

    def due_date_for_ym(self, ym: str) -> Optional[date]:
        """특정 귀속년월(YYYY-MM)의 실제 납입기한일 산출 (단월 말일 자동 클램핑)."""
        parsed = parse_ym(ym)
        if not parsed:
            return None
        y, mo = parsed
        last_day = calendar.monthrange(y, mo)[1]
        return date(y, mo, min(self.payment_day(), last_day))

    def billing_notice_schedule(self, as_of: Optional[date] = None) -> Dict[str, Any]:
        """기준일(as_of) 대비 ① 납입일 3일 전 사전통보(PRE_DUE_3D) 및 ② 납입일 1일 후 미납통보(OVERDUE_1D) 판정."""
        ref = as_of or date_today()
        pay_day = self.payment_day()
        this_ym = f"{ref.year:04d}-{ref.month:02d}"
        this_due = self.due_date_for_ym(this_ym) or date(ref.year, ref.month, 1)

        # 1) 차기(또는 당월 미도래) 납입기한 산출
        if this_due >= ref:
            upcoming_ym = this_ym
            upcoming_due = this_due
        else:
            next_idx = ym_index(ref.year, ref.month) + 1
            upcoming_ym = index_to_ym(next_idx)
            upcoming_due = self.due_date_for_ym(upcoming_ym) or ref

        up_spec = self.due_for_month(upcoming_ym)
        if up_spec["rule_state"] == "POSTPAID_MOVE_IN_EXEMPT":
            up_parsed = parse_ym(upcoming_ym)
            if up_parsed:
                upcoming_ym = index_to_ym(ym_index(*up_parsed) + 1)
                upcoming_due = self.due_date_for_ym(upcoming_ym) or upcoming_due
                up_spec = self.due_for_month(upcoming_ym)

        lg_up = self.ledger(up_to_ym=upcoming_ym)
        row_up = next((m for m in lg_up["months"] if m["ym"] == upcoming_ym), None)
        upcoming_unpaid = int(row_up["unpaid"]) if row_up else 0
        days_until_due = (upcoming_due - ref).days
        is_pre_due_3d = (
            self.status == "ACTIVE"
            and days_until_due == 3
            and up_spec["due"] > 0
            and upcoming_unpaid > 0
        )

        # 2) 이미 납입일이 최소 1일 이상 경과한 최근 귀속월의 미납 여부 산출 (납입일 당일은 미납 아님)
        if this_due < ref:
            past_ym = this_ym
            past_due = this_due
        else:
            prev_idx = ym_index(ref.year, ref.month) - 1
            past_ym = index_to_ym(prev_idx)
            past_due = self.due_date_for_ym(past_ym) or (ref - timedelta(days=1))

        lg_past = self.ledger(up_to_ym=past_ym)
        past_total_unpaid = int(lg_past["total_unpaid"])
        unpaid_months = [str(m["ym"]) for m in lg_past["months"] if m["unpaid"] > 0]
        days_past_due = (ref - past_due).days
        is_overdue_1d_exact = (
            self.status == "ACTIVE"
            and days_past_due == 1
            and past_total_unpaid > 0
        )
        is_overdue_eligible = (
            self.status == "ACTIVE"
            and days_past_due >= 1
            and past_total_unpaid > 0
        )

        return {
            "as_of_date": ref.isoformat(),
            "payment_day": pay_day,
            "upcoming_billing_ym": upcoming_ym,
            "upcoming_due_date": upcoming_due.isoformat(),
            "days_until_due": days_until_due,
            "upcoming_due_amount": int(up_spec["due"]),
            "upcoming_unpaid_amount": upcoming_unpaid,
            "is_pre_due_3d": is_pre_due_3d,
            "past_billing_ym": past_ym,
            "past_due_date": past_due.isoformat(),
            "days_past_due": days_past_due,
            "past_unpaid_amount": past_total_unpaid,
            "unpaid_months": unpaid_months,
            "is_overdue_1d_exact": is_overdue_1d_exact,
            "is_overdue_eligible": is_overdue_eligible,
        }

    def due_for_month(self, ym: str) -> Dict[str, Any]:
        """핵심 수납 불변식:
        - 전입일 이전 월은 절대 청구하지 않는다 (가짜 체납 Ghost Overdue 원천 차단).
        - 월세가 '후불'인 경우 입주월 당월은 청구액 0원(POSTPAID_EXEMPT)이며, 익월부터 청구한다.
        - 퇴실월 이후는 청구하지 않는다.
        """
        target = parse_ym(ym)
        move_in = parse_ym(self.move_in_date)
        if not target or not move_in:
            return {"ym": ym, "due": 0, "rent": 0, "maintenance": 0, "vat": 0, "rule_state": "INVALID_DATE"}

        t_idx = ym_index(*target)
        in_idx = ym_index(*move_in)

        if t_idx < in_idx:
            return {"ym": ym, "due": 0, "rent": 0, "maintenance": 0, "vat": 0, "rule_state": "BEFORE_MOVE_IN"}

        if self.move_out_date:
            out_ym = parse_ym(self.move_out_date)
            if out_ym and t_idx > ym_index(*out_ym):
                return {"ym": ym, "due": 0, "rent": 0, "maintenance": 0, "vat": 0, "rule_state": "AFTER_MOVE_OUT"}

        if self.pay_timing == "후불" and t_idx == in_idx:
            return {
                "ym": ym,
                "due": 0,
                "rent": 0,
                "maintenance": 0,
                "vat": 0,
                "rule_state": "POSTPAID_MOVE_IN_EXEMPT",
            }

        rent_amt = 0 if self.lease_type == "전세" else self.monthly_rent
        maint_amt = self.maintenance_fee
        vat_amt = self.compute_vat() if (self.vat_target and self.vat_separate) else 0
        total_due = rent_amt + maint_amt + vat_amt
        return {
            "ym": ym,
            "due": total_due,
            "rent": rent_amt,
            "maintenance": maint_amt,
            "vat": vat_amt,
            "rule_state": "BILLABLE",
        }

    def ledger(self, up_to_ym: Optional[str] = None) -> Dict[str, Any]:
        """계약 시작월부터 기준월까지 FIFO 수납 상계가 반영된 실시간 원장 산출."""
        end_ym = up_to_ym or current_ym()
        move_in = parse_ym(self.move_in_date)
        target_end = parse_ym(end_ym)
        if not move_in or not target_end:
            return {"months": [], "total_due": 0, "total_paid": 0, "total_unpaid": 0, "oldest_unpaid_ym": None}

        start_idx = ym_index(*move_in)
        end_idx = max(start_idx, ym_index(*target_end))

        pays = [
            p for p in Pay.find(id_contract=self.id)
            if p.payment_kind == "MONTHLY_RENT"
        ]
        total_pool_paid = sum(p.total_amount for p in pays)

        # 수납 상계 순서: ① 각 납부 건은 먼저 '그 건에 적힌 청구월'의 청구액을 채우고(달 단위로 바로 보이게),
        # ② 남는 금액(청구액을 넘는 납부·청구월이 없거나 범위 밖인 납부·이월 합산 납부)은 가장 오래된 미납 달부터 채운다(FIFO).
        # 이렇게 해야 '납부 내역이 있는 달이 미납으로 나오는' 일이 없고, 부족분이 실제로 비어 있는 달에 표시된다. 총 미납액은 예전 방식과 같다.
        labelled: Dict[str, int] = {}
        for p in pays:
            ym0 = (p.billing_month or "")[:7]
            if len(ym0) == 7 and ym0[:4].isdigit() and ym0[4] == "-" and ym0[5:7].isdigit():
                labelled[ym0] = labelled.get(ym0, 0) + p.total_amount

        specs: List[Dict[str, Any]] = []
        for idx in range(start_idx, end_idx + 1):
            ym_str = index_to_ym(idx)
            spec = self.due_for_month(ym_str)
            specs.append(spec)
        first_alloc: Dict[str, int] = {}
        for spec in specs:
            if spec["rule_state"] == "POSTPAID_MOVE_IN_EXEMPT" or spec["due"] == 0:
                continue
            first_alloc[spec["ym"]] = min(spec["due"], labelled.get(spec["ym"], 0))
        remaining_credit = total_pool_paid - sum(first_alloc.values())

        rows: List[Dict[str, Any]] = []
        total_due = 0
        for spec in specs:
            ym_str = spec["ym"]
            due = spec["due"]
            total_due += due

            if spec["rule_state"] == "POSTPAID_MOVE_IN_EXEMPT":
                rows.append({
                    **spec,
                    "paid": 0,
                    "unpaid": 0,
                    "status": "EXEMPT_POSTPAID",
                    "note": "후불 입주월 비과세 (익월 청구)",
                })
                continue

            if due == 0:
                rows.append({
                    **spec,
                    "paid": 0,
                    "unpaid": 0,
                    "status": "ZERO_DUE",
                    "note": "청구 없음",
                })
                continue

            direct = first_alloc.get(ym_str, 0)
            extra = min(due - direct, remaining_credit)
            remaining_credit -= extra
            allocated = direct + extra
            unpaid = due - allocated
            if unpaid == 0:
                st = "PAID"
                note = "완납"
            elif allocated > 0:
                st = "PARTIAL"
                note = f"부분납부 (잔여 {unpaid:,}원)"
            else:
                st = "UNPAID"
                note = "미납"

            # 3단계 구분: 완납(PAID) / 납부 예정(DUE_SOON: 납입일 당일까지 아직 안 냄) / 연체(OVERDUE: 납입일 다음 날부터)
            if unpaid == 0:
                phase = "PAID"
            else:
                dd = self.due_date_for_ym(ym_str)
                phase = "DUE_SOON" if (dd is None or date_today() <= dd) else "OVERDUE"
                if phase == "DUE_SOON" and st == "UNPAID" and dd is not None:
                    note = f"납부 예정 ({dd.month}/{dd.day}까지)"
            rows.append({
                **spec,
                "paid": allocated,
                "unpaid": unpaid,
                "status": st,
                "phase": phase,
                "note": note,
            })

        total_unpaid = sum(r["unpaid"] for r in rows)
        overdue_unpaid = sum(r["unpaid"] for r in rows if r.get("phase") == "OVERDUE")
        oldest = next((r["ym"] for r in rows if r["unpaid"] > 0), None)
        return {
            "contract_id": self.id,
            "room_no": self.room_no,
            "tenant_name": self.tenant_name,
            "pay_timing": self.pay_timing,
            "months": rows,
            "total_due": total_due,
            "total_paid": total_pool_paid,
            "advance_credit": remaining_credit,
            "total_unpaid": total_unpaid,
            "overdue_unpaid": overdue_unpaid,       # 납입일이 지난 달의 미입금 합계(진짜 미납)
            "oldest_unpaid_ym": oldest,
            "days_to_expiry": self.days_to_expiry(),
        }

    @staticmethod
    def _cycle_anchor(start: date, k: int) -> date:
        """k번째 달 임차료 기간의 시작일. 1개월분은 매달 전입일부터 다음 달 전입일 전날까지이므로 전입일의 '일'을 그대로 쓴다(그 달에 없는 날이면 말일)."""
        idx = start.year * 12 + (start.month - 1) + k
        y, m0 = divmod(idx, 12)
        return date(y, m0 + 1, min(start.day, calendar.monthrange(y, m0 + 1)[1]))

    def checkout_liability(self, room: Optional["Room"] = None) -> Dict[str, Any]:
        """조기 전출(만기 전 퇴실) 때 이전 임차인이 부담하는 월 임차료.
        규칙: 만기 전에 나가도 '만기일 또는 새 임차인이 입주할 때까지(먼저 오는 날)' 이전 임차인이 임차료를 부담하고, 아직 내지 않은 금액은 전출 정산에서 보증금에서 공제한다.
        - 부담이 끝나는 마지막 날: 만기일, 단 새 임차인이 미리 계약해 둔(예약 계약, SCHEDULED) 입주일이 만기 이내면 입주일의 전날(전입일·전출일이 같은 날이면 그날부터 새 임차인).
        - 1개월분은 매달 전입일부터 다음 달 전입일 전날까지다. 그 마지막 날까지 끝난 달들은 달 임차료 전액, 마지막 날이 한 달 기간의 중간이면
          그 기간은 (경과 일수 × 월 임차료) ÷ 30 만 추가로 징수한다(월 임차료 = 월세 + 관리비 + 부가세).
        - unpaid_now: 지금 달까지의 미납(부담 범위 안), early_amount: 지금 달 이후 ~ 마지막 날까지 아직 안 낸 금액(일할 포함, 선납분은 제외)."""
        today_ym = current_ym()
        monthly = (0 if self.lease_type == "전세" else int(self.monthly_rent or 0)) + int(self.maintenance_fee or 0) \
            + (int(self.compute_vat()) if (self.vat_target and self.vat_separate) else 0)
        out: Dict[str, Any] = {"end_date": self.expiry_date or "", "last_liable_date": self.expiry_date or "", "reason": "EXPIRY", "new_tenant_move_in": "",
                               "unpaid_now": int(self.ledger()["total_unpaid"]), "early_amount": 0, "months": [], "full_cycles": 0,
                               "partial_from": "", "partial_days": 0, "partial_amount": 0, "monthly_total": monthly}
        try:
            mi = date.fromisoformat((self.move_in_date or "")[:10])
            exp = date.fromisoformat((self.expiry_date or "")[:10])
        except ValueError:
            return out
        last, reason, new_in = exp, "EXPIRY", ""
        if room is not None:
            res = room.reservation()
            if res is not None and not res.renews_contract_id:
                try:
                    n_in = date.fromisoformat((res.move_in_date or "")[:10])
                except ValueError:
                    n_in = None
                if n_in is not None and n_in <= exp:
                    last, reason, new_in = n_in - timedelta(days=1), "NEW_TENANT", res.move_in_date
        # 마지막 날까지 끝난 달 수(n_full)와 그 다음 기간에서 마지막 날까지 지난 일수(partial_days)
        n_full = 0
        while n_full < 600 and self._cycle_anchor(mi, n_full + 1) - timedelta(days=1) <= last:
            n_full += 1
        part_from = self._cycle_anchor(mi, n_full)
        partial_days = min(30, (last - part_from).days + 1) if last >= part_from else 0
        partial_amount = min(monthly, round(partial_days * monthly / 30)) if partial_days > 0 else 0
        unpaid_now, future, credit = 0, [], 0
        if n_full > 0:
            # 끝난 달들에 해당하는 원장 달: 후불은 그 기간이 끝나는 달에, 선불은 그 기간이 시작하는 달에 청구된다
            anchor = self._cycle_anchor(mi, n_full) if self.pay_timing == "후불" else self._cycle_anchor(mi, n_full - 1)
            led = self.ledger(up_to_ym=f"{anchor.year:04d}-{anchor.month:02d}")
            rows = led["months"]
            unpaid_now = sum(int(r["unpaid"]) for r in rows if r["ym"] <= today_ym)
            future = [r for r in rows if r["ym"] > today_ym and int(r["unpaid"]) > 0]
            credit = int(led.get("advance_credit", 0))
        else:
            credit = sum(p.total_amount for p in Pay.find(id_contract=self.id) if p.payment_kind == "MONTHLY_RENT")
        partial_net = max(0, partial_amount - credit)                 # 남는 선납액이 있으면 일할분에서 먼저 뺀다
        out.update({
            "end_date": last.isoformat(), "last_liable_date": last.isoformat(), "reason": reason, "new_tenant_move_in": new_in,
            "unpaid_now": unpaid_now, "full_cycles": n_full,
            "early_amount": sum(int(r["unpaid"]) for r in future) + partial_net,
            "months": [{"ym": r["ym"], "due": int(r["due"]), "unpaid": int(r["unpaid"])} for r in future],
            "partial_from": part_from.isoformat() if partial_days > 0 else "", "partial_days": partial_days, "partial_amount": partial_net,
        })
        return out

    def validate_renewal_cap(self, new_deposit: int, new_rent: int) -> Dict[str, Any]:
        """주택임대차보호법 제7조 5% 임대료 증액 상한선 검증."""
        old_equiv = self.monthly_rent
        max_rent = round(old_equiv * 1.05)
        if old_equiv > 0 and new_rent > max_rent:
            inc_pct = round(((new_rent - old_equiv) / old_equiv) * 100, 2)
            raise Mismatch(
                f"재계약 월세 증액률({inc_pct}%)이 법정 상한선(5.0%)을 초과했습니다.",
                hint=f"종전 월세 {old_equiv:,}원 기준 최대 {max_rent:,}원까지만 설정할 수 있습니다.",
                expected=max_rent,
                actual=new_rent,
            )
        max_dep = round(self.deposit * 1.05)
        if self.deposit > 0 and new_deposit > max_dep:
            inc_pct = round(((new_deposit - self.deposit) / self.deposit) * 100, 2)
            raise Mismatch(
                f"재계약 보증금 증액률({inc_pct}%)이 법정 상한선(5.0%)을 초과했습니다.",
                hint=f"종전 보증금 {self.deposit:,}원 기준 최대 {max_dep:,}원까지만 설정할 수 있습니다.",
                expected=max_dep,
                actual=new_deposit,
            )
        return {"valid": True, "max_rent": max_rent, "max_deposit": max_dep}

    def terminate(self, move_out_date: Optional[str] = None, app_source: str = "rent3_lessor") -> "Contract":
        self.move_out_date = move_out_date or date_today().isoformat()
        self.ended_at = self.move_out_date
        self.status = "TERMINATED"
        return self.save(action="contract_terminated", app_source=app_source)

    @staticmethod
    def compute_integrity_hash(payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
