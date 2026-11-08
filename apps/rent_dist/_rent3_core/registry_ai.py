"""
apps/_rent3_core/registry_ai.py — 등기사항증명서(등기부등본)를 Gemini 로 읽어 구조화하고, 위험 신호는 코드로 계산한다.

역할 분담
- Gemini(공용 `shared.ai.gemini_client.GeminiClient`): 글자 읽기와 항목 추출만 한다.
- 이 모듈의 코드: 금액 합산, 소유자 대조, 담보 비율 같은 판단은 전부 직접 계산한다.
  (AI 가 숫자를 틀리게 읽거나 지어낼 수 있으므로 판단을 맡기지 않는다.)

주의: AI 는 서류의 '진위'를 가리지 못한다. 이 결과는 임차인이 인터넷등기소에서 직접 열람한 자료를
읽어 주는 참고용이며, 최종 확인과 책임은 임차인에게 있다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from shared.ai.gemini_client import GeminiClient, clean_json_markdown

from .broker import ALLOWED_MIME_TO_EXT, MAX_PHOTO_BYTES

# 참고 기준: (채권최고액 합계 + 이번 보증금) ÷ 건물 가치
RATIO_HIGH = 0.80
RATIO_CAUTION = 0.60
RECENT_TRANSFER_DAYS = 180

PROMPT = """당신은 한국 등기사항증명서(등기부등본) 문서를 읽어 항목을 추출하는 도구입니다.
첨부된 문서를 읽고 아래 JSON 스키마로만 답하세요. 설명이나 마크다운 없이 순수 JSON만 출력합니다.

규칙:
- 문서에 적힌 내용만 옮기세요. 읽을 수 없거나 없는 값은 추측하지 말고 null 또는 빈 배열로 두세요.
- 이미 말소된 등기(말소기준, 말소됨, 취소선, '말소' 표기)는 "cancelled": true 로 표시하세요.
- 금액은 숫자만(원 단위 정수). 콤마·'금'·'원' 글자는 빼세요.
- 날짜는 YYYY-MM-DD.
- 이 문서가 등기사항증명서가 아니면 "document_type"을 "other"로 하세요.

스키마:
{
  "document_type": "registry" 또는 "other",
  "readable": true 또는 false,
  "issue_number": "발급확인번호(없으면 null)",
  "property_address": "부동산 소재지",
  "owners": [{"name": "소유자 성명", "share": "지분(없으면 null)", "id_front": "소유자 주민등록번호 앞 6자리(YYMMDD)만. 뒷자리는 절대 옮기지 마세요. 없으면 null"}],
  "mortgages": [{"creditor": "근저당권자", "amount": 채권최고액 숫자, "registered_date": "접수일", "cancelled": true/false}],
  "restrictions": [{"type": "압류|가압류|가처분|신탁|경매개시결정|임차권등기|기타", "detail": "요약", "cancelled": true/false}],
  "ownership_transfers": [{"date": "접수일", "cause": "원인(매매, 상속 등)"}],
  "notes": "읽기 어려웠던 부분이나 특이사항(없으면 null)"
}"""


def _client() -> GeminiClient:
    """테스트에서 가짜 클라이언트로 바꿔 끼울 수 있게 분리했다."""
    return GeminiClient()


def _to_int(v: Any) -> int:
    try:
        return int(re.sub(r"[^\d-]", "", str(v))) if v not in (None, "") else 0
    except ValueError:
        return 0


def _norm_name(v: Any) -> str:
    return re.sub(r"\s+", "", re.sub(r"\([^)]*\)", "", str(v or "")))


def _parse_date(v: Any) -> Optional[date]:
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", str(v or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def decode_data_url(data_url: str):
    m = re.match(r"^data:([a-zA-Z0-9.+/-]+);base64,(.+)$", (data_url or "").strip(), re.DOTALL)
    if not m or m.group(1).lower() not in ALLOWED_MIME_TO_EXT:
        from core.errors import DomainError
        raise DomainError("파일 형식을 읽을 수 없습니다.", hint="PDF 또는 이미지(JPG, PNG, WEBP)를 올려 주세요.")
    try:
        binary = base64.b64decode(m.group(2), validate=False)
    except Exception:
        from core.errors import DomainError
        raise DomainError("파일을 읽지 못했습니다. 다시 선택해 주세요.")
    if not binary:
        from core.errors import DomainError
        raise DomainError("비어 있는 파일입니다.")
    if len(binary) > MAX_PHOTO_BYTES:
        from core.errors import DomainError
        raise DomainError("파일은 최대 10MB 이하만 가능합니다.")
    return binary, m.group(1).lower()


def read_registry(binary: bytes, mime: str) -> Dict[str, Any]:
    """Gemini 에 문서를 보내 항목을 추출한다. 반환: {"status": ok|unavailable|failed, "raw": dict|None, "message": str}"""
    client = _client()
    if not client.is_available:
        return {"status": "unavailable", "raw": None,
                "message": "서버에 GEMINI_API_KEY가 설정되어 있지 않아 AI 읽기를 쓸 수 없습니다. 직접 입력해 주세요."}
    b64 = base64.b64encode(binary).decode("ascii")
    try:
        # 이 함수는 동기 라우트(스레드풀)에서 호출되므로 실행 중인 이벤트 루프가 없다.
        text = asyncio.run(client.generate_with_image(PROMPT, b64, mime_type=mime))
    except Exception as exc:  # 네트워크·쿼터 등
        return {"status": "failed", "raw": None, "message": f"AI 호출에 실패했습니다: {type(exc).__name__}"}
    try:
        raw = json.loads(clean_json_markdown(text))
    except Exception:
        return {"status": "failed", "raw": None, "message": "AI 응답을 해석하지 못했습니다. 직접 입력해 주세요."}
    if not isinstance(raw, dict) or raw.get("mock"):
        return {"status": "unavailable", "raw": None,
                "message": "AI 서비스를 쓸 수 없는 상태입니다(키 또는 쿼터). 직접 입력해 주세요."}
    return {"status": "ok", "raw": raw, "message": ""}


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """AI 가 준 값을 형식을 맞춰 정리하고, 합계·여부는 AI 의 말이 아니라 목록에서 코드로 계산한다."""
    owners = [
        {"name": str(o.get("name") or "").strip(), "share": (str(o.get("share")).strip() if o.get("share") else ""),
         "id_front": "".join(ch for ch in str(o.get("id_front") or "") if ch.isdigit())[:6]}
        for o in (raw.get("owners") or []) if isinstance(o, dict) and str(o.get("name") or "").strip()
    ]
    mortgages = []
    for m in raw.get("mortgages") or []:
        if not isinstance(m, dict):
            continue
        mortgages.append({
            "creditor": str(m.get("creditor") or "").strip(),
            "amount": _to_int(m.get("amount")),
            "registered_date": str(m.get("registered_date") or "").strip(),
            "cancelled": bool(m.get("cancelled")),
        })
    restrictions = []
    for r in raw.get("restrictions") or []:
        if not isinstance(r, dict):
            continue
        restrictions.append({
            "type": str(r.get("type") or "기타").strip(),
            "detail": str(r.get("detail") or "").strip(),
            "cancelled": bool(r.get("cancelled")),
        })
    transfers = [
        {"date": str(t.get("date") or "").strip(), "cause": str(t.get("cause") or "").strip()}
        for t in (raw.get("ownership_transfers") or []) if isinstance(t, dict)
    ]
    active_m = [m for m in mortgages if not m["cancelled"]]
    active_r = [r for r in restrictions if not r["cancelled"]]
    return {
        "document_type": "registry" if raw.get("document_type") == "registry" else "other",
        "readable": bool(raw.get("readable", True)),
        "issue_number": str(raw.get("issue_number") or "").strip(),
        "property_address": str(raw.get("property_address") or "").strip(),
        "owners": owners,
        "owner_name": ", ".join(o["name"] for o in owners),
        "mortgages": mortgages,
        "mortgage_count": len(active_m),
        "mortgage_total": sum(m["amount"] for m in active_m),
        "restrictions": restrictions,
        "seizure_or_trust": "있음" if active_r else "없음",
        "ownership_transfers": transfers,
        "notes": str(raw.get("notes") or "").strip(),
    }


def assess_risks(ex: Dict[str, Any], *, lessor_names: List[str], deposit: int, property_value: int,
                 today: Optional[date] = None) -> List[Dict[str, str]]:
    """코드로 계산하는 위험 신호. level: high | caution | info"""
    today = today or date.today()
    risks: List[Dict[str, str]] = []

    if ex["document_type"] != "registry":
        risks.append({"level": "high", "code": "NOT_REGISTRY",
                      "text": "등기사항증명서가 아닌 문서로 보입니다. 인터넷등기소에서 받은 PDF를 올려 주세요."})
        return risks
    if not ex["readable"]:
        risks.append({"level": "caution", "code": "UNREADABLE",
                      "text": "글자가 흐려 일부를 읽지 못했습니다. 아래 값을 원본과 꼼꼼히 대조해 주세요."})

    active = [r for r in ex["restrictions"] if not r["cancelled"]]
    for r in active:
        risks.append({"level": "high", "code": "RESTRICTION",
                      "text": f"{r['type']} 등기가 있습니다{(' — ' + r['detail']) if r['detail'] else ''}. 계약 전 반드시 전문가와 확인하세요."})

    names = {_norm_name(n) for n in lessor_names if n}
    if ex["owner_name"] and names and not any(_norm_name(o["name"]) in names for o in ex["owners"]):
        risks.append({"level": "high", "code": "OWNER_MISMATCH",
                      "text": f"등기부의 소유자({ex['owner_name']})가 임대인과 다릅니다. 대리 계약이면 위임장·인감증명서를 확인하세요."})
    elif len(ex["owners"]) > 1:
        risks.append({"level": "caution", "code": "MULTI_OWNER",
                      "text": "공동 소유입니다. 모든 소유자의 동의(또는 위임)가 있는지 확인하세요."})

    if property_value > 0:
        ratio = (ex["mortgage_total"] + max(deposit, 0)) / property_value
        pct = f"{ratio * 100:.0f}%"
        base = (f"채권최고액 합계 {ex['mortgage_total']:,}원 + 보증금 {deposit:,}원 = 건물 가치 "
                f"{property_value:,}원의 {pct}")
        if ratio >= RATIO_HIGH:
            risks.append({"level": "high", "code": "LTV_HIGH",
                          "text": f"{base}. 경매가 되면 보증금을 돌려받지 못할 위험이 큽니다(참고 기준 {int(RATIO_HIGH * 100)}%)."})
        elif ratio >= RATIO_CAUTION:
            risks.append({"level": "caution", "code": "LTV_CAUTION",
                          "text": f"{base}. 여유가 크지 않습니다(참고 기준 {int(RATIO_CAUTION * 100)}% 이상)."})
        else:
            risks.append({"level": "info", "code": "LTV_OK", "text": f"{base}."})
    elif ex["mortgage_total"] > 0:
        risks.append({"level": "caution", "code": "NO_VALUE",
                      "text": f"채권최고액 합계는 {ex['mortgage_total']:,}원입니다. 건물 가치 정보가 없어 비율은 계산하지 못했습니다."})

    dates = [d for d in (_parse_date(t["date"]) for t in ex["ownership_transfers"]) if d]
    if dates:
        latest = max(dates)
        if (today - latest).days <= RECENT_TRANSFER_DAYS:
            risks.append({"level": "caution", "code": "RECENT_TRANSFER",
                          "text": f"최근 {RECENT_TRANSFER_DAYS}일 안({latest.isoformat()})에 소유권이 바뀌었습니다. 매매 경위를 확인하세요."})

    if not any(r["level"] in ("high", "caution") for r in risks):
        risks.append({"level": "info", "code": "NO_FLAG",
                      "text": "AI가 읽은 범위에서 눈에 띄는 위험 신호는 없습니다. 그래도 인터넷등기소 원본과 직접 대조하세요."})
    return risks


def analyze(binary: bytes, mime: str, *, lessor_names: List[str], deposit: int, property_value: int) -> Dict[str, Any]:
    res = read_registry(binary, mime)
    if res["status"] != "ok":
        return {"status": res["status"], "message": res["message"], "extracted": None, "risks": []}
    ex = normalize(res["raw"])
    risks = assess_risks(ex, lessor_names=lessor_names, deposit=deposit, property_value=property_value)
    return {"status": "ok", "message": "", "extracted": ex, "risks": risks,
            "analyzed_at": datetime.now().isoformat(timespec="seconds")}
