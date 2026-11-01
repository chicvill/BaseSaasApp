"""
apps/rent4/backend/paste.py — 만능 붙여 넣기: 무엇을 넣었는지 알아내서 알맞은 흐름으로 보낸다.

    텍스트      은행 입금 문자 → bank   (입금 문자 분석·반영)
                Excel 표 복사 → excel  (수납부 가져오기)
                구글 시트 주소 → 공개 시트를 TSV 로 읽어 excel
    파일        .xlsx         → 시트를 TSV 로 바꿔 excel
                사진          → Gemini 로 읽기: 입금 캡처면 bank, 종이 계약서면 contract(초안)
결과는 미리보기용일 뿐 저장하지 않는다. 저장은 각 흐름의 '반영' · '등록' 버튼이 한다.
사진·파일 원본은 저장하지 않는다.
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.request
from datetime import date, datetime
from typing import Dict

from core.errors import DomainError, Mismatch

from . import bank_parser
from .excel_import import INFO_COLS

MAX_BYTES = 8 * 1024 * 1024
SHEETS_RE = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/[^?#]*)?(?:[?#].*?gid=(\d+))?")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
XLSX_TYPES = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

IMAGE_PROMPT = """이 사진은 한국 임대 관리용 자료입니다. 아래 JSON 하나만 답하세요.
{"kind": "bank" | "contract" | "other",
 "bank_text": "입금 알림/거래내역이면 입금 건마다 '은행 날짜 시각 입금 금액 입금자' 를 한 줄씩, 건 사이는 빈 줄",
 "contract": {"room": "호실 숫자", "party_name": "임차인 이름", "party_phone": "010-0000-0000",
              "deposit": 보증금 정수(원), "rent": 월 차임 정수(원), "mgmt": 월 관리비 정수(원),
              "start": "YYYY-MM-DD 입주일/계약 시작", "end": "YYYY-MM-DD 만기", "lease_type": "월세|전세",
              "pay_timing": "후불|선불"}}
주민등록번호는 절대 옮겨 적지 마세요. 읽을 수 없는 값은 빈 문자열 또는 0."""


def detect_text(text: str) -> str:
    t = text.strip()
    if SHEETS_RE.match(t):
        return "url"
    if re.match(r"^https?://", t):
        raise Mismatch("구글 시트 주소만 읽을 수 있습니다.", hint="엑셀 파일은 '📎 파일'로 올리거나, 표를 복사해 붙여 넣어 주세요.")
    lines = [ln for ln in t.splitlines() if ln.strip()]
    tabbed = sum(1 for ln in lines if ln.count("\t") >= 5)
    if any(ln.split("\t")[0].strip() == "호실" for ln in lines) or (lines and tabbed >= len(lines) * 0.6):
        return "excel"
    return "bank"


def _cell(v, col: int) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return f"{v.month}/{v.day}" if col >= INFO_COLS else f"{v.year}. {v.month}. {v.day}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def xlsx_to_text(data: bytes) -> str:
    """수납부 시트(A1 이 'YYYY년도')를 골라 TSV 로. 여러 개면 가장 늦은 연도."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as e:
        raise Mismatch("엑셀 파일을 열 수 없습니다.", hint=".xlsx 파일인지 확인해 주세요.") from e
    best, best_year = None, -1
    for ws in wb.worksheets:
        first = next(ws.iter_rows(min_row=1, max_row=1, max_col=1, values_only=True), (None,))[0]
        m = re.match(r"^\s*(\d{4})\s*년도", str(first or ""))
        year = int(m[1]) if m else -1
        if best is None or year > best_year:
            best, best_year = ws, year
    rows = ["\t".join(_cell(v, i) for i, v in enumerate(r)).rstrip("\t") for r in best.iter_rows(values_only=True)]
    return "\n".join(rows)


def fetch_sheet(url: str) -> str:
    """공개(링크가 있는 모든 사용자) 구글 시트를 TSV 로 읽는다. 구글 시트 주소만 허용."""
    m = SHEETS_RE.match(url.strip())
    if not m:
        raise Mismatch("구글 시트 주소가 아닙니다.")
    export = f"https://docs.google.com/spreadsheets/d/{m[1]}/export?format=tsv" + (f"&gid={m[2]}" if m[2] else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(export, headers={"User-Agent": "Rent4"}), timeout=15) as res:
            body = res.read(MAX_BYTES + 1)
            ctype = res.headers.get("Content-Type", "")
    except Exception as e:
        raise DomainError("구글 시트를 읽지 못했습니다.", hint="시트 공유를 '링크가 있는 모든 사용자 — 뷰어'로 바꿔 주세요.") from e
    if "html" in ctype or len(body) > MAX_BYTES:
        raise DomainError("구글 시트를 읽지 못했습니다.", hint="시트 공유를 '링크가 있는 모든 사용자 — 뷰어'로 바꿔 주세요.")
    return body.decode("utf-8", "replace")


def _int(v) -> int:
    try:
        return max(0, int(str(v or 0).replace(",", "").replace("원", "").strip() or 0))
    except ValueError:
        return 0


def _draft(c: Dict) -> Dict:
    room = re.sub(r"\D", "", str(c.get("room", "")))
    dates = {}
    for k in ("start", "end"):
        v = str(c.get(k) or "")
        dates[k] = v if re.match(r"^\d{4}-\d{2}-\d{2}$", v) else ""
    return {"room": room, "party_name": str(c.get("party_name") or "").strip(),
            "party_phone": re.sub(r"[^\d-]", "", str(c.get("party_phone") or "")),
            "deposit": _int(c.get("deposit")), "rent": _int(c.get("rent")), "mgmt": _int(c.get("mgmt")),
            "lease_type": c.get("lease_type") if c.get("lease_type") in ("월세", "전세") else "월세",
            "pay_timing": c.get("pay_timing") if c.get("pay_timing") in ("후불", "선불", "반선불") else "후불", **dates}


async def read_image(data: bytes, mime: str) -> Dict:
    from shared.ai.gemini_client import GeminiClient, clean_json_markdown
    client = GeminiClient()
    if not client.is_available:
        raise DomainError("사진 읽기는 AI(Gemini) 키가 있어야 합니다.", hint="서버에 GEMINI_API_KEY 를 등록하거나, 내용을 글자로 붙여 넣어 주세요.")
    try:
        reply = await client.generate_with_image(IMAGE_PROMPT, base64.b64encode(data).decode(), mime_type=mime)
        out = json.loads(clean_json_markdown(reply))
    except Exception as e:
        raise DomainError("사진을 읽지 못했습니다.", hint="더 밝고 똑바로 찍은 사진으로 다시 시도해 주세요.") from e
    if out.get("kind") == "bank" and str(out.get("bank_text") or "").strip():
        return {"kind": "bank", "text": str(out["bank_text"]).strip(), "source": "image"}
    if out.get("kind") == "contract" and isinstance(out.get("contract"), dict):
        return {"kind": "contract", "contract": _draft(out["contract"]), "source": "image"}
    raise Mismatch("사진에서 입금 내역이나 계약서를 찾지 못했습니다.")


async def analyze(text: str = "", file_b64: str = "", filename: str = "", mime: str = "") -> Dict:
    """무엇인지 알아내 다음 흐름에 넘길 내용을 돌려준다 (저장하지 않음)."""
    if file_b64:
        try:
            data = base64.b64decode(file_b64.split(",", 1)[-1], validate=False)
        except Exception as e:
            raise Mismatch("파일을 읽을 수 없습니다.") from e
        if len(data) > MAX_BYTES:
            raise Mismatch("파일이 너무 큽니다 (8MB 까지).")
        name = filename.lower()
        if mime in XLSX_TYPES or name.endswith(".xlsx"):
            return {"kind": "excel", "text": xlsx_to_text(data), "source": "xlsx"}
        if mime in IMAGE_TYPES or re.search(r"\.(jpe?g|png|webp|heic|heif)$", name):
            return await read_image(data, mime if mime in IMAGE_TYPES else "image/jpeg")
        if name.endswith((".txt", ".tsv")):
            text = data.decode("utf-8", "replace")
        else:
            raise Mismatch("엑셀(.xlsx) · 사진 · 텍스트 파일만 넣을 수 있습니다.")
    text = (text or "").strip()
    if not text:
        raise Mismatch("붙여 넣은 내용이 없습니다.", hint="입금 문자, 엑셀 표, 구글 시트 주소, 사진 · 엑셀 파일을 넣어 주세요.")
    kind = detect_text(text)
    if kind == "url":
        return {"kind": "excel", "text": fetch_sheet(text), "source": "url"}
    if kind == "bank" and not bank_parser.split_messages(text):
        raise Mismatch("무엇인지 알아보지 못했습니다.", hint="입금 문자, 엑셀 표, 구글 시트 주소 중 하나를 넣어 주세요.")
    return {"kind": kind, "text": text, "source": "text"}

