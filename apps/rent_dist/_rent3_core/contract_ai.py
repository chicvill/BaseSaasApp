"""
apps/rent_dist/_rent3_core/contract_ai.py — 스마트 계약 AI OCR · 문해력(쉬운 요약 · 표준 특약 다듬기 · 메모 OCR) 도우미.

설계 원칙:
  - 금액·비율·법정 5% 상한·차이 계산은 Python 코드가 정확히 계산하고,
    Gemini AI는 OCR(이미지 읽기)과 자연어 문해력(쉬운 말 풀이·표준 특약 정리)을 보조한다.
  - GEMINI_API_KEY가 없거나 일시 오류일 때도 결정론적(규칙 기반) 쉬운 요약·특약 초안을 반환해
    사용자가 막히지 않도록 한다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any, Dict, List

from shared.ai.gemini_client import GeminiClient, clean_json_markdown

from . import registry_ai


def _client() -> GeminiClient:
    return GeminiClient()


def _won(n: Any) -> str:
    try:
        return f"{int(n or 0):,}원"
    except (TypeError, ValueError):
        return f"{n}원"


def plain_registry_summary(
    ex: Dict[str, Any],
    risks: List[Dict[str, str]],
    *,
    deposit: int = 0,
    lessor_names: List[str] | None = None,
) -> str:
    """등기부 OCR 추출 결과를 임차인이 3초 만에 이해할 수 있는 쉬운 한국어로 요약한다."""
    if ex.get("document_type") != "registry":
        return "등기부등본이 아닌 문서로 보입니다. 인터넷등기소에서 열람한 등기부등본 화면 캡처나 PDF를 다시 올려 주세요."
    owner = ex.get("owner_name") or "확인 불가"
    mort_cnt = int(ex.get("mortgage_count") or 0)
    mort_tot = int(ex.get("mortgage_total") or 0)
    seize = ex.get("seizure_or_trust") or "없음"
    high_codes = {r.get("code") for r in (risks or []) if r.get("level") == "high"}
    caution_codes = {r.get("code") for r in (risks or []) if r.get("level") == "caution"}

    lines: List[str] = []
    if "RESTRICTION" in high_codes:
        lines.append("🛑 압류·가압류·신탁 등 위험 권리가 발견되었습니다. 계약을 진행하지 마세요.")
    elif "OWNER_MISMATCH" in high_codes:
        lines.append(f"🛑 등기부 소유자({owner})가 임대인과 다릅니다. 대리 계약 위임장·인감증명서 없이는 진행하면 안 됩니다.")
    else:
        lines.append(f"✅ 등기부 소유자는 '{owner}'이며 압류·가압류·신탁은 {seize} 상태입니다.")

    if mort_tot > 0:
        lines.append(f"⚠️ 유효한 근저당 {mort_cnt}건(채권최고액 합계 {_won(mort_tot)})이 잡혀 있습니다. 보증금({_won(deposit)}) 보호를 위해 보증보험 가입 가능 여부나 말소 특약을 꼭 확인하세요.")
    else:
        lines.append("💚 설정된 근저당(빚)이 없는 깨끗한 권리 상태입니다.")

    if "LTV_HIGH" in high_codes:
        lines.append("🚨 담보 비율이 높아 경매 시 보증금 회수 위험이 큽니다.")
    elif "RECENT_TRANSFER" in caution_codes:
        lines.append("ℹ️ 최근 6개월 내 소유권 변동 이력이 있어 매매 경위를 함께 확인하면 좋습니다.")
    elif not high_codes and not caution_codes:
        lines.append("👍 AI 검토 기준 눈에 띄는 위험 신호가 없습니다. 원본과 소유자·금액만 한 번 더 대조 후 승인하세요.")
    return " ".join(lines)


def _rule_explain_contract(session: Any) -> Dict[str, Any]:
    """API 키 없이도 항상 동작하는 결정론적 계약서 쉬운 풀이 + 체크포인트."""
    tm = session.terms or {}
    is_ren = session.mode == "RENEWAL"
    dep = int(tm.get("deposit") or 0)
    cdep = 0 if is_ren else int(tm.get("contract_deposit") or 0)
    bal = max(0, dep - cdep)
    rent = int(tm.get("monthly_rent") or 0)
    fee = int(tm.get("maintenance_fee") or 0)
    clean = int(tm.get("cleaning_fee") or 0)
    pay_timing = str(tm.get("pay_timing") or "후불")
    move_in = str(tm.get("move_in_date") or "-")
    months = int(tm.get("lease_term_months") or 12)
    entry = str(getattr(session, "entry_channel", "") or "")

    entry_ko = {
        "LESSOR_RENEWAL": "임대인 요청 연장 스마트 계약",
        "EXISTING_TENANT_MOVE": f"기존 임차인({getattr(session, 'moving_from_room_no', '')}호) 이사 신규 스마트 계약",
        "PORTAL_NEW": "포털 신규 가입자 스마트 계약",
        "LESSOR_NEW": "임대인 호출 신규 스마트 계약",
    }.get(entry, "연장 스마트 계약" if is_ren else "신규 스마트 계약")

    bullets: List[str] = [
        f"📌 계약 유형: {entry_ko} ({session.building_name} {session.room_no}호 · {months}개월 · {move_in} 시작)",
        (
            f"💰 보증금 {_won(dep)} (기존 보증금 승계, 계약금 0원)"
            if is_ren
            else f"💰 보증금 {_won(dep)} = 계약금 {_won(cdep)} + 입주 잔금 {_won(bal)}"
        ),
        f"📆 월 납부 합계: {_won(rent + fee)} (월세 {_won(rent)} + 관리비 {_won(fee)} · {pay_timing}"
        + (" — 입주 첫 달은 비과세, 익월부터 전월분 납부)" if pay_timing == "후불" else ")"),
    ]
    if clean > 0:
        bullets.append(f"🧹 퇴실 청소비 약정: {_won(clean)} (퇴실 정산 시 보증금에서 약정액 이내 공제)")
    else:
        bullets.append("🧹 퇴실 청소비: 약정 없음(0원) — 별도 약정 없으면 퇴실 시 청소비 공제 불가")

    reg_mode = str(getattr(session, "registry_check_mode", "REQUIRED") or "REQUIRED")
    tr = session.tenant_registry or {}
    if is_ren:
        bullets.append("🔁 기존 입주자 연장 계약으로 신원확인·옵션점검을 생략하고 법정 5% 상한 내에서 체결합니다.")
    elif reg_mode == "SKIPPED_BY_TENANT":
        bullets.append("⚠️ 임차인 선택으로 등기부등본 확인이 생략되었습니다. 원하시면 서명 전 언제든 체크를 켜서 AI 검토를 진행할 수 있습니다.")
    elif tr.get("submitted"):
        bullets.append(f"🏛️ 등기부 확인됨: 소유자 {tr.get('owner_name') or '-'} · 근저당 {_won(tr.get('mortgage_amount'))} · 압류/신탁 {tr.get('seizure_or_trust') or '-'}")
    else:
        bullets.append("🏛️ 등기부등본 검토 대기 중: 원하시면 캡처 이미지를 올려 AI로 3초 만에 검토할 수 있습니다.")

    cautions: List[str] = []
    if not is_ren and not (session.facility_photos or []):
        cautions.append("입주 전 하자 분쟁 예방을 위해 도배·옵션 현장 사진 1~2장을 남겨 두는 것을 권장합니다.")
    if not (session.lessor_account or "").strip() and not is_ren:
        cautions.append("임대인 수납 계좌번호가 아직 등록되지 않았습니다. 계약금 송금 전 계좌를 확인하세요.")
    if tm.get("tax_invoice_requested"):
        cautions.append("세금계산서 발행 요청 호실입니다. 사업자등록번호와 수신 이메일이 정확한지 확인하세요.")
    special = str(tm.get("special_terms") or "").strip()
    if special:
        n_lines = len([ln for ln in special.splitlines() if ln.strip()])
        bullets.append(f"📝 특약사항 {n_lines}개 조항이 포함되어 있습니다.")

    return {
        "summary": f"{session.building_name} {session.room_no}호 {entry_ko}입니다. 월 {_won(rent + fee)}({pay_timing}), 보증금 {_won(dep)} 조건입니다.",
        "bullets": bullets,
        "cautions": cautions,
        "ai_enhanced": False,
    }


def explain_contract(session: Any) -> Dict[str, Any]:
    """현재 스마트 계약서 조건과 특약을 누구나 이해하기 쉽게 요약·점검한다."""
    base = _rule_explain_contract(session)
    client = _client()
    if not client.is_available:
        return base
    tm = session.terms or {}
    prompt = (
        "당신은 한국 주택임대차 스마트 계약서의 문해력 도우미입니다.\n"
        "아래 계약 정보를 읽고 임대인과 임차인이 오해 없이 합의할 수 있도록 아주 쉬운 한국어로 요약하세요.\n"
        "반드시 순수 JSON만 출력하세요: {\"summary\": \"한 문장 핵심 요약\", \"plain_special_terms\": [\"특약 쉬운 풀이...\"], \"cautions\": [\"확인할 점...\"]}\n"
        "규칙: 숫자를 바꾸거나 지어내지 말고 제공된 값만 쓰세요. 각 항목은 짧고 친절하게 1~2문장으로 쓰세요.\n"
        f"계약 정보: {json.dumps({'mode': session.mode, 'room': f'{session.building_name} {session.room_no}호', 'deposit': tm.get('deposit'), 'contract_deposit': tm.get('contract_deposit'), 'monthly_rent': tm.get('monthly_rent'), 'maintenance_fee': tm.get('maintenance_fee'), 'cleaning_fee': tm.get('cleaning_fee'), 'pay_timing': tm.get('pay_timing'), 'move_in_date': tm.get('move_in_date'), 'months': tm.get('lease_term_months'), 'special_terms': tm.get('special_terms', ''), 'facility_notes': session.facility_notes}, ensure_ascii=False)}"
    )
    try:
        text = asyncio.run(client.generate_text(prompt))
        raw = json.loads(clean_json_markdown(text))
        if isinstance(raw, dict) and not raw.get("mock"):
            if raw.get("summary"):
                base["summary"] = str(raw["summary"]).strip()
            extra_sp = [str(x).strip() for x in (raw.get("plain_special_terms") or []) if str(x).strip()][:5]
            if extra_sp:
                base["bullets"] = [*base["bullets"], *[f"💡 특약 풀이: {x}" for x in extra_sp]]
            extra_c = [str(x).strip() for x in (raw.get("cautions") or []) if str(x).strip()][:4]
            if extra_c:
                base["cautions"] = [*base["cautions"], *[x for x in extra_c if x not in base["cautions"]]]
            base["ai_enhanced"] = True
    except Exception:
        pass
    return base


def draft_special_clause(raw_text: str, session: Any) -> Dict[str, Any]:
    """일상 한국어 요청을 공정하고 명확한 주택임대차 표준 특약 문구로 다듬는다."""
    src = (raw_text or "").strip()
    if len(src) < 2:
        from core.errors import DomainError
        raise DomainError("특약으로 정리할 내용을 입력해 주세요.", hint="예: 소형 반려견 1마리 허용, 벽지 훼손 시 실비 원상복구")

    # 결정론적 기본 폴백
    clean_src = src.rstrip(". ")
    fallback_clause = f"임대인과 임차인은 상호 합의에 따라 '{clean_src}' 사항을 이행하며, 통상적인 사용에 따른 자연 마모는 원상복구 대상에서 제외한다."
    out: Dict[str, Any] = {
        "clause": fallback_clause,
        "explanation": "입력하신 합의 내용을 분쟁 소지가 적도록 표준 특약 형식으로 정리했습니다. 양측 확인 후 특약에 반영하세요.",
        "caution": "주택임대차보호법상 임차인에게 일방적으로 불리한 약정은 효력이 없을 수 있으므로 상호 공정한 문구로 합의하세요.",
        "ai_enhanced": False,
    }
    client = _client()
    if not client.is_available:
        return out
    prompt = (
        "당신은 한국 주택임대차 표준계약서 특약 작성 도우미입니다.\n"
        "사용자가 일상어로 적은 합의 사항을 임대인과 임차인 모두에게 공정하고 명확한 1~2문장의 표준 특약 문구로 다듬어 주세요.\n"
        "반드시 순수 JSON만 출력하세요: {\"clause\": \"표준 특약 문구\", \"explanation\": \"쉬운 설명 1문장\", \"caution\": \"법적 주의점 1문장(없으면 빈 문자열)\"}\n"
        f"호실: {session.building_name} {session.room_no}호 ({session.mode})\n"
        f"사용자 입력: {src}"
    )
    try:
        text = asyncio.run(client.generate_text(prompt))
        raw = json.loads(clean_json_markdown(text))
        if isinstance(raw, dict) and not raw.get("mock") and str(raw.get("clause") or "").strip():
            out["clause"] = str(raw["clause"]).strip()
            if str(raw.get("explanation") or "").strip():
                out["explanation"] = str(raw["explanation"]).strip()
            if raw.get("caution") is not None:
                out["caution"] = str(raw.get("caution") or "").strip()
            out["ai_enhanced"] = True
    except Exception:
        pass
    return out


MEMO_OCR_PROMPT = """당신은 임대차 계약 메모, 기존 계약서 사진, 또는 시설 점검 메모 이미지를 읽어 핵심 항목을 추출하는 OCR 도구입니다.
첨부된 이미지를 읽고 아래 JSON 스키마로만 답하세요. 설명이나 마크다운 없이 순수 JSON만 출력하세요.
읽을 수 없거나 이미지에 없는 값은 null로 두세요. 금액은 원 단위 정수(숫자만)로 변환하세요(예: 보증금 500만원 -> 5000000, 월세 35만 -> 350000).

{
  "deposit": 숫자 또는 null,
  "contract_deposit": 숫자 또는 null,
  "monthly_rent": 숫자 또는 null,
  "maintenance_fee": 숫자 또는 null,
  "cleaning_fee": 숫자 또는 null,
  "move_in_date": "YYYY-MM-DD 또는 null",
  "lease_term_months": 숫자 또는 null,
  "special_terms": "추출된 특약 문구 또는 null",
  "facility_notes": "추출된 옵션/도배/시설 상태 메모 또는 null",
  "summary": "이미지에서 읽은 내용을 1~2문장으로 쉽게 요약"
}"""


def ocr_memo_image(data_url: str) -> Dict[str, Any]:
    """계약 메모·기존 계약서·시설 상태 메모 사진을 AI OCR로 읽어 구조화한다."""
    binary, mime = registry_ai.decode_data_url(data_url)
    client = _client()
    if not client.is_available:
        return {
            "status": "unavailable",
            "message": "서버에 GEMINI_API_KEY가 설정되어 있지 않아 사진 OCR을 쓸 수 없습니다. 조건을 직접 입력해 주세요.",
            "extracted": {},
        }
    b64 = base64.b64encode(binary).decode("ascii")
    try:
        text = asyncio.run(client.generate_with_image(MEMO_OCR_PROMPT, b64, mime_type=mime))
        raw = json.loads(clean_json_markdown(text))
    except Exception as exc:
        return {
            "status": "failed",
            "message": f"사진 OCR 분석에 실패했습니다({type(exc).__name__}). 선명한 사진으로 다시 시도해 주세요.",
            "extracted": {},
        }
    if not isinstance(raw, dict) or raw.get("mock"):
        return {
            "status": "unavailable",
            "message": "AI OCR 서비스를 현재 사용할 수 없습니다. 직접 입력해 주세요.",
            "extracted": {},
        }
    extracted: Dict[str, Any] = {}
    for k in ("deposit", "contract_deposit", "monthly_rent", "maintenance_fee", "cleaning_fee", "lease_term_months"):
        if raw.get(k) not in (None, ""):
            try:
                val = int(re.sub(r"[^\d]", "", str(raw[k])))
                if val >= 0:
                    extracted[k] = val
            except ValueError:
                pass
    mv = str(raw.get("move_in_date") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", mv):
        extracted["move_in_date"] = mv
    for k in ("special_terms", "facility_notes", "summary"):
        v = str(raw.get(k) or "").strip()
        if v:
            extracted[k] = v
    return {
        "status": "ok",
        "message": "AI가 사진에서 계약 조건·메모를 읽었습니다. 값을 확인한 뒤 채팅 제안이나 메모에 반영하세요.",
        "extracted": extracted,
    }
