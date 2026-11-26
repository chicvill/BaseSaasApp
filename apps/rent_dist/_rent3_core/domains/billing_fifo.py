"""
apps/rent_dist/_rent3_core/domains/billing_fifo.py — 스마트 은행문자 파서(SmsDepositParser) 및 FIFO 순차 상계 수납 엔진(FifoPaymentEngine).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.errors import Conflict, DomainError, NotFound
from ..models import (
    Contract,
    Pay,
    Room,
    current_ym,
    date_today,
    normalize_branch_id,
)


def _names_of(ct: Any) -> set:
    """이 계약에서 입금자로 인정하는 이름: 임차인명 + 송금자명(다른 이름으로 입금되는 경우)."""
    names = {(ct.tenant_name or "").strip()}
    names.update((x or "").strip() for x in (getattr(ct, "payer_names", None) or []))
    names.discard("")
    return names


class SmsDepositParser:
    """은행 입금 알림 문자에서 [호실번호, 입금자명, 금액, 입금일]을 추출하는 순수 파서 객체."""

    _ACCOUNT_RE = re.compile(
        r"\b(?:\d{2,6}[-*]){1,4}[\d*]{2,8}\b|"
        r"\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b|"
        r"\b\d{10,16}\b"
    )
    _TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
    _MAN_WON_RE = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*원?")

    @staticmethod
    def parse(text: str) -> Dict[str, Any]:
        s = (text or "").strip()
        if not s:
            raise DomainError("입금 문자 내용이 비어 있습니다.", hint="은행 입금 알림 문자를 복사하여 붙여넣어 주세요.")

        # 1) 금액 추출 ("잔액 ..." 이전에서 계좌번호·전화번호·시각을 마스킹한 뒤 입금액 추출)
        before_balance = s.split("잔액")[0]
        amt_zone = SmsDepositParser._TIME_RE.sub(" ", SmsDepositParser._ACCOUNT_RE.sub(" ", before_balance))
        amt_matches = re.findall(r"(\d{1,3}(?:,\d{3})+|\d{5,})\s*원?", amt_zone)
        if amt_matches:
            amount = int(amt_matches[-1].replace(",", ""))
        else:
            man_m = SmsDepositParser._MAN_WON_RE.search(amt_zone)
            if not man_m:
                raise DomainError(
                    "문자에서 입금 금액을 인식하지 못했습니다.",
                    hint="예: '[신한] 10/25 201호 홍길동 550,000원 입금' 형태로 입력해 주세요.",
                )
            amount = int(round(float(man_m.group(1)) * 10000))

        # 2) 날짜 추출 (MM/DD 또는 YYYY-MM-DD)
        today = date_today()
        paid_date = today.isoformat()
        m_full = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
        if m_full:
            paid_date = f"{int(m_full.group(1)):04d}-{int(m_full.group(2)):02d}-{int(m_full.group(3)):02d}"
        else:
            m_md = re.search(r"(?:^|\s)(\d{1,2})/(\d{1,2})(?:\s|$)", s)
            if m_md:
                paid_date = f"{today.year:04d}-{int(m_md.group(1)):02d}-{int(m_md.group(2)):02d}"

        # 3) 호실 번호 추출 (예: 201호 또는 이름 뒤 3자리 호수 — 계좌번호/시각 마스킹 적용)
        room_zone = SmsDepositParser._TIME_RE.sub(" ", SmsDepositParser._ACCOUNT_RE.sub(" ", s))
        room_no = ""
        m_room = re.search(r"\b([1-9]\d{2})\s*호", room_zone)
        if m_room:
            room_no = m_room.group(1)
        else:
            # 이름 바로 뒤 3자리 호수(예: '홍길동201'). 금액(440,000원)의 앞자리나 '입금 440,000' 같은 말을 호실로 읽지 않는다
            for m_room2 in re.finditer(r"([가-힣]{2,4})\s*(?<!\d)([1-9]\d{2})(?![\d,]|\s*원)", room_zone):
                if not m_room2.group(1).endswith(("은행", "뱅크", "금고")) and m_room2.group(1) not in {"입금", "출금", "승인", "체크", "잔액", "신한", "국민", "우리", "하나", "농협", "카카오", "토스", "기업", "발신", "문자", "알림", "일시", "계좌", "이체", "송금", "전자금융"}:
                    room_no = m_room2.group(2)
                    break

        # 4) 입금자 성명 추출 (은행/입금/승인/잔액/단위 키워드 제외 한글 2~4자)
        ignore_words = {"입금", "출금", "승인", "체크", "잔액", "신한", "국민", "우리", "하나", "농협", "카카오", "토스", "기업", "발신", "문자", "알림", "일시", "계좌", "이체", "송금", "전자금융", "만원", "천원", "백원", "월세", "관리비", "수납"}
        kor_tokens = re.findall(r"[가-힣]{2,4}", before_balance)
        # '신한은행'·'카카오뱅크'·'새마을금고' 같은 금융기관 이름은 입금자가 아니다
        is_bank = lambda tok: tok in ignore_words or tok.endswith(("은행", "뱅크", "금고", "체국", "협중앙")) or tok in {"수협", "신협", "케이뱅크", "저축은행"}
        payer_name = next((tok for tok in kor_tokens if not is_bank(tok)), "입금자")

        return {
            "room_no": room_no,
            "payer_name": payer_name,
            "amount": amount,
            "paid_date": paid_date,
            "raw_sms": s,
        }


class FifoPaymentEngine:
    """FIFO(선입선출) 순차 상계 및 중복 입금 원천 차단 수납 엔진."""

    @staticmethod
    def collect(
        branch: str,
        room_no: str,
        amount: int,
        payer_name: str = "",
        paid_date: Optional[str] = None,
        method: str = "TRANSFER",
        raw_sms: str = "",
        up_to_ym: Optional[str] = None,
        app_source: str = "rent3_lessor",
        target_ym: Optional[str] = None,
        target_months: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """입금을 '월 칸'에 채운다. target_ym 이 있으면 그 달의 빈칸(미납)부터, 없으면 가장 오래된 미납 달부터 채우고,
        한 입금이 여러 달에 걸치면 달마다 납부 건을 하나씩 만들어 각 건에 자기 청구월을 적는다(건이 달을 가로질러 숨어 있지 않게).
        미납 합계보다 많은 입금(선수금)은 받지 않고 오류로 처리한다."""
        b_id = normalize_branch_id(branch)
        if amount <= 0:
            raise DomainError("입금 금액은 0원보다 커야 합니다.", hint="정확한 수납 금액을 입력해 주세요.")

        room = Room.first(branch=b_id, room_no=room_no)
        if not room:
            raise NotFound(f"{b_id} 지점에서 '{room_no}호'를 찾을 수 없습니다.", hint="호실 번호를 다시 확인해 주세요.")

        contract = Contract.get(room.active_contract_id) if room.active_contract_id else None
        if not contract:
            contract = Contract.first(id_room=room.id, status="ACTIVE")
        if not contract:
            raise DomainError(
                f"{room.room_no}호는 현재 활성 임대계약이 없는 공실입니다.",
                hint="먼저 공동 임대계약 앱에서 계약을 체결한 뒤 수납을 등록해 주세요.",
            )

        p_date = paid_date or date_today().isoformat()
        p_name = (payer_name or contract.tenant_name or "세입자").strip()
        base_key = f"{room.id}:{p_name}:{amount}:{p_date}:{raw_sms.strip()[:40]}"
        idem_key = base_key

        # 중복 입금 차단: 문자는 '같은 문자 본문'이면 같은 입금이므로 영구 차단한다.
        # 직접 수납(문자 본문 없음)은 같은 호실·같은 금액·같은 날 입금이 실제로 여러 번 있을 수 있으므로(예: 두 달치를 나눠 받음),
        # 방금(30초 안) 같은 요청이 또 온 경우(이중 클릭)만 막고 그 밖에는 새 입금으로 기록한다.
        if raw_sms.strip():
            if any((q.idempotency_key or "") == base_key or (q.idempotency_key or "").startswith(base_key + ":") for q in Pay.find(id_contract=contract.id)):
                raise Conflict(
                    f"이미 등록된 동일 입금 내역입니다 ({room.room_no}호 {p_name} {amount:,}원, {p_date}).",
                    hint="같은 문자가 이미 수납되었습니다. 수납 내역 장부를 확인해 주세요.",
                )
        else:
            now_s = datetime.now()
            for q in Pay.find(id_contract=contract.id):
                if (q.idempotency_key or "").split("#", 1)[0] == base_key and q.created_at:
                    try:
                        if (now_s - datetime.fromisoformat(q.created_at)).total_seconds() < 30:
                            raise Conflict(
                                f"방금 같은 금액({amount:,}원)을 수납했습니다 ({room.room_no}호 {p_name}).",
                                hint="버튼을 두 번 눌렀다면 이미 처리된 것입니다. 수납 내역을 확인해 주세요. (다른 입금이라면 30초 뒤에 다시 수납하세요)",
                            )
                    except ValueError:
                        pass
            idem_key = f"{base_key}#{uuid4().hex[:8]}"

        # 월 칸 채우기 전 원장 조회
        tgt = up_to_ym or current_ym()
        before_ledger = contract.ledger(up_to_ym=tgt)
        unpaid_rows = [r for r in before_ledger["months"] if r["unpaid"] > 0]
        total_unpaid = sum(r["unpaid"] for r in unpaid_rows)
        if not unpaid_rows:
            raise DomainError(
                f"{room.room_no}호는 밀린 청구가 없습니다. {amount:,}원은 받을 수 없습니다(선수금은 받지 않습니다).",
                hint="이미 받은 입금을 또 기록하려는 것은 아닌지 수납 내역(🔍 원장 상태)을 확인해 주세요.",
                code="NO_UNPAID",
            )
        if amount > total_unpaid:
            raise DomainError(
                f"입금액 {amount:,}원이 {room.room_no}호의 밀린 금액 {total_unpaid:,}원보다 많습니다(선수금은 받지 않습니다).",
                hint=f"밀린 금액 {total_unpaid:,}원 이하로 입력해 주세요. 이미 받은 입금이면 중복 기록이 아닌지 확인해 주세요.",
                code="OVER_UNPAID",
            )
        order = list(unpaid_rows)
        if target_months:
            picked_set = {m.strip() for m in target_months if m and m.strip()}
            order = [r for r in unpaid_rows if r["ym"] in picked_set]
            if not order:
                raise DomainError("선택한 달에 밀린 청구가 없습니다.", hint="미납으로 표시된 달을 체크해 주세요.", code="NOT_UNPAID_MONTH")
            picked_unpaid = sum(r["unpaid"] for r in order)
            if amount > picked_unpaid:
                raise DomainError(
                    f"입금액 {amount:,}원이 선택한 달의 미납 합계 {picked_unpaid:,}원보다 많습니다.",
                    hint="입금액에 맞게 달을 더 선택하거나, 금액이 맞는지 확인해 주세요.",
                    code="OVER_UNPAID",
                )
        elif target_ym:
            hit = next((r for r in unpaid_rows if r["ym"] == target_ym), None)
            if hit is None:
                raise DomainError(f"{target_ym}월은 밀린 청구가 아닙니다.", hint="미납으로 표시된 달을 골라 주세요(이미 납부된 달에는 수납할 수 없습니다).", code="NOT_UNPAID_MONTH")
            order = [hit] + [r for r in unpaid_rows if r["ym"] != target_ym]

        remaining = amount
        allocations: List[Dict[str, Any]] = []
        for row in order:
            if remaining <= 0:
                break
            take = min(remaining, row["unpaid"])
            remaining -= take
            allocations.append({"ym": row["ym"], "allocated": take, "remaining_unpaid_in_month": row["unpaid"] - take, "cleared": (row["unpaid"] - take) == 0, "_row": row})

        pay_objs: List[Pay] = []
        multi = len(allocations) > 1
        group_id = f"dep_{uuid4().hex[:8]}"          # 한 입금이 여러 달로 나뉘어도 같은 묶음으로 표시
        for a in allocations:
            row = a["_row"]
            due = row.get("due") or 0
            vat_part = round(a["allocated"] * (row.get("vat") or 0) / due) if due > 0 else 0
            maint_part = round(a["allocated"] * (row.get("maintenance") or 0) / due) if due > 0 else 0
            rent_part = a["allocated"] - vat_part - maint_part
            pay_objs.append(Pay(
                branch=b_id,
                id_building=room.id_building,
                id_room=room.id,
                room_no=room.room_no,
                id_contract=contract.id,
                id_tenant=contract.id_tenant,
                payer_name=p_name,
                billing_month=a["ym"],
                paid_date=p_date,
                rent_amount=rent_part,
                maintenance_amount=maint_part,
                vat_amount=vat_part,
                total_amount=a["allocated"],
                payment_kind="MONTHLY_RENT",
                method=method,
                allocated_months=[{k: v for k, v in a.items() if k != "_row"}],
                idempotency_key=(f"{idem_key}:{a['ym']}" if multi else idem_key),
                deposit_group_id=group_id,
                raw_sms=raw_sms,
            ).save(action="fifo_payment_collected", app_source=app_source))
        for a in allocations:
            a.pop("_row", None)
        pay_obj = pay_objs[0]

        after_ledger = contract.ledger(up_to_ym=tgt)
        from ..ops import RentBillingNotice
        RentBillingNotice.resolve_paid_for_contract(contract.id, after_ledger, app_source=app_source)
        diff = before_ledger["total_unpaid"] - amount
        months_txt = ", ".join(a["ym"] for a in allocations)
        if after_ledger["total_unpaid"] > 0:
            banner_level = "diff"
            banner_msg = f"{room.room_no}호 {amount:,}원을 {months_txt}에 반영했습니다 — 아직 비어 있는 달의 미납 {after_ledger['total_unpaid']:,}원이 남아 있습니다."
        else:
            banner_level = "success"
            banner_msg = f"{room.room_no}호 {p_name}님 {amount:,}원을 {months_txt}에 반영했습니다 — 모든 달이 납부 완료되었습니다."

        return {
            "payment": pay_obj.model_dump(),
            "payments": [x.model_dump() for x in pay_objs],
            "allocations": allocations,
            "ledger": after_ledger,
            "difference": diff,
            "level": banner_level,
            "message": banner_msg,
        }

    @staticmethod
    def _expected_amounts(contract: Any, up_to_ym: Optional[str]) -> Dict[str, Any]:
        """이 계약에서 '맞는 입금액'으로 볼 금액들: 월 청구액(월세+관리비+부가세)의 1~12배, 밀린 달을 앞에서부터 채운 누계, 전체 미납액."""
        monthly = (contract.monthly_rent or 0) + (contract.maintenance_fee or 0) + (contract.compute_vat() or 0)
        led = contract.ledger(up_to_ym=up_to_ym or current_ym())
        ok = set()
        if monthly > 0:
            ok.update(monthly * k for k in range(1, 13))
        acc = 0
        for r in led["months"]:
            if r["unpaid"] > 0:
                acc += r["unpaid"]
                ok.add(acc)
        if led["total_unpaid"] > 0:
            ok.add(led["total_unpaid"])
        return {"ok": ok, "monthly": monthly, "total_unpaid": led["total_unpaid"]}

    @classmethod
    def check_sms(cls, b_id: str, parsed: Dict[str, Any], up_to_ym: Optional[str] = None) -> Dict[str, Any]:
        """문자에서 읽은 호실·입금자명·금액을 장부와 맞춰 본다. 어긋난 곳은 issues, 입금자명 기준으로 반영할 때의 대상 계약은 by_name."""
        room_no, name, amount = parsed["room_no"], parsed["payer_name"], parsed["amount"]
        name_known = name != "입금자"
        by_name_all = [c for c in Contract.all() if c.branch == b_id and c.status == "ACTIVE" and name_known and name in _names_of(c)]
        by_name = None
        if by_name_all:
            by_name = next((c for c in by_name_all if c.room_no == room_no), None) or (by_name_all[0] if len(by_name_all) == 1 else None)
        issues: List[Dict[str, str]] = []
        ct = None
        if room_no:
            room = Room.first(branch=b_id, room_no=room_no)
            if not room:
                issues.append({"field": "room", "msg": f"'{room_no}호'는 이 건물에 없는 호실입니다."})
            else:
                ct = Contract.get(room.active_contract_id) if room.active_contract_id else None
                ct = ct or Contract.first(id_room=room.id, status="ACTIVE")
                if not ct:
                    issues.append({"field": "room", "msg": f"{room_no}호는 현재 계약이 없는 공실입니다."})
        else:
            ct = by_name
            if not ct:
                issues.append({"field": "room", "msg": "문자에서 호실을 찾지 못했고, 입금자명으로도 계약을 찾지 못했습니다."})
        if ct:
            if not name_known:
                issues.append({"field": "name", "msg": f"입금자명을 읽지 못했습니다. ({ct.room_no}호 임차인은 '{ct.tenant_name}')"})
            elif name not in _names_of(ct):
                alias = (", 송금자명 " + "·".join(ct.payer_names)) if getattr(ct, "payer_names", None) else ""
                issues.append({"field": "name", "msg": f"입금자명 '{name}'이(가) {ct.room_no}호 임차인 '{ct.tenant_name}'{alias}과 다릅니다."})
            exp = cls._expected_amounts(ct, up_to_ym)
            if amount not in exp["ok"]:
                extra = f"월 청구액 {exp['monthly']:,}원" + (f", 밀린 금액 {exp['total_unpaid']:,}원" if exp["total_unpaid"] else "")
                issues.append({"field": "amount", "msg": f"입금액 {amount:,}원이 청구액과 맞지 않습니다. ({extra})"})
        return {"issues": issues, "by_name": by_name, "room_contract": ct}

    @classmethod
    def parse_and_collect_sms(
        cls,
        branch: str,
        sms_text: str,
        up_to_ym: Optional[str] = None,
        app_source: str = "rent3_lessor",
        force: bool = False,
        target_months: Optional[List[str]] = None,
        pick_if_multi: bool = False,
    ) -> Dict[str, Any]:
        """문자를 읽어 수납한다. 호실·입금자명·금액 중 장부와 어긋난 것이 있으면 저장하지 않고 확인을 요청하고(needs_confirm),
        pick_if_multi=True 이고 미납 달이 2개 이상이면서 일부 금액만 입금된 경우(target_months 미지정) 어느 달에 넣을지 선택을 요청한다(needs_month_pick)."""
        parsed = SmsDepositParser.parse(sms_text)
        b_id = normalize_branch_id(branch)
        chk = cls.check_sms(b_id, parsed, up_to_ym)
        issues, by_name = chk["issues"], chk["by_name"]
        # 호실·입금자는 맞고 금액만 다르면(예: 3개월분) 문자 수정이 아니라 '수작업 안내'로 보낸다(아래)
        if pick_if_multi and issues and all(i["field"] == "amount" for i in issues) and chk["room_contract"] is not None:
            issues = []
        if issues:
            if not by_name:
                if not parsed["room_no"]:
                    raise DomainError(
                        f"문자에서 호실 번호를 찾을 수 없으며 '{parsed['payer_name']}' 명의의 활성 계약도 없습니다.",
                        hint="문자 내용의 호실 번호를 수정하거나 송금자에게 전화로 확인한 뒤 다시 처리해 주세요.",
                    )
                raise DomainError(
                    " ".join(i["msg"] for i in issues) + f" '{parsed['payer_name']}' 명의의 활성 계약도 찾지 못했습니다.",
                    hint="문자의 호실·입금자명·금액을 확인해 원본을 고치거나 송금자에게 확인한 뒤 다시 붙여넣어 주세요.",
                )
            if not force:
                return {
                    "needs_confirm": True,
                    "level": "warning",
                    "message": "문자 내용이 장부와 다릅니다. 원본 문자를 확인해 주세요.",
                    "issues": issues,
                    "parsed": parsed,
                    "by_name": {"room_no": by_name.room_no, "tenant_name": by_name.tenant_name},
                }
            room_no = by_name.room_no
            target_ct = by_name
        else:
            target_ct = chk["room_contract"]
            room_no = parsed["room_no"] or (target_ct.room_no if target_ct else "")

        # 입금액이 '월 납부액'과 다르면(여러 달분·일부 입금 등) 자동으로 달을 정하지 않고 [직접 수납]에서 미납 월을 골라 처리하도록 안내한다.
        # 초과 입금(밀린 금액보다 많음)·밀린 청구 없음은 처리하지 않는다(선수금 불가).
        if pick_if_multi and not target_months and target_ct:
            led = target_ct.ledger(up_to_ym=up_to_ym or current_ym())
            unpaid_rows = [r for r in led["months"] if r["unpaid"] > 0]
            total_unpaid = sum(r["unpaid"] for r in unpaid_rows)
            monthly = cls._expected_amounts(target_ct, up_to_ym)["monthly"]
            if not unpaid_rows:
                raise DomainError(
                    f"{room_no}호는 밀린 청구가 없습니다. {parsed['amount']:,}원은 받을 수 없습니다(선수금은 받지 않습니다).",
                    hint="이미 받은 입금을 또 기록하려는 것은 아닌지 수납 내역(🔍 원장 상태)을 확인해 주세요.",
                    code="NO_UNPAID",
                )
            if parsed["amount"] > total_unpaid:
                raise DomainError(
                    f"입금액 {parsed['amount']:,}원이 {room_no}호의 밀린 금액 {total_unpaid:,}원보다 많아 처리할 수 없습니다(선수금은 받지 않습니다).",
                    hint=f"밀린 금액 {total_unpaid:,}원 이하인지, 문자 금액이 맞는지 확인해 주세요.",
                    code="OVER_UNPAID",
                )
            if monthly > 0 and parsed["amount"] != monthly:
                return {
                    "needs_confirm": True,
                    "needs_manual": True,
                    "level": "warning",
                    "message": f"입금액 {parsed['amount']:,}원이 월 납부액 {monthly:,}원과 다릅니다. 자동으로 처리하지 않았습니다. [직접 수납]에서 미납 월을 선택해 처리해 주세요.",
                    "parsed": parsed,
                    "room_no": room_no,
                    "tenant_name": target_ct.tenant_name,
                    "monthly": monthly,
                    "total_unpaid": total_unpaid,
                    "unpaid_months": [{"ym": r["ym"], "unpaid": r["unpaid"], "due": r.get("due", 0), "phase": r.get("phase", "OVERDUE")} for r in unpaid_rows],
                }
            overdue_n = sum(1 for r in unpaid_rows if r.get("phase") == "OVERDUE")     # 납부 예정(납입일 전) 달은 '미납 달 수'에 세지 않는다
            if unpaid_rows and parsed["amount"] < total_unpaid and overdue_n >= 2:
                return {
                    "needs_confirm": True,
                    "needs_month_pick": True,
                    "level": "warning",
                    "message": f"{room_no}호에 밀린 달이 {len(unpaid_rows)}개 있습니다. 어느 달 입금으로 처리할지 선택해 주세요.",
                    "parsed": parsed,
                    "room_no": room_no,
                    "tenant_name": target_ct.tenant_name,
                    "unpaid_months": [{"ym": r["ym"], "unpaid": r["unpaid"], "due": r.get("due", 0), "phase": r.get("phase", "OVERDUE")} for r in unpaid_rows],
                }

        result = cls.collect(
            branch=b_id,
            room_no=room_no,
            amount=parsed["amount"],
            payer_name=parsed["payer_name"],
            paid_date=parsed["paid_date"],
            method="BANK_SMS",
            raw_sms=sms_text,
            up_to_ym=up_to_ym,
            app_source=app_source,
            target_months=target_months,
        )
        result["parsed"] = parsed
        if issues:
            result["forced_by_name"] = True
            result["issues"] = issues
        return result
