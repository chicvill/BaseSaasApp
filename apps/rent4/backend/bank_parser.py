"""
apps/rent4/backend/bank_parser.py — 은행 입금 문자 분석기.

1차: 규칙 기반 (은행 문자 공통 패턴: 날짜, 시각, '입금 금액', 잔액, 입금자명)
2차: 규칙으로 금액·입금자를 못 찾은 문자만 Gemini 에 JSON 추출을 요청 (GEMINI_API_KEY 가 있을 때만)

여러 건을 한 번에 붙여 넣어도 된다:
    [Web발신]
    신한 10/25 14:32
    110-***-123456
    입금 290,000
    잔액 3,456,789
    김수현

    [KB]10/26 09:10 123456**789 정민준 입금 440,000 잔액1,234,567
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import List, Optional

BANKS = ("카카오뱅크", "케이뱅크", "토스뱅크", "토스", "새마을", "우체국", "신한", "국민", "KB", "우리", "하나",
         "농협", "NH", "기업", "IBK", "SC제일", "제일", "씨티", "부산", "대구", "경남", "광주", "전북", "수협", "신협")
STOPWORDS = {"Web발신", "웹발신", "입금", "출금", "잔액", "원", "님", "계좌", "알림", "체크", "카드", "이체", "통장",
             "보통예금", "저축예금", "입출금", "받은", "보낸", "분", "일", "은행", "뱅크"}
AMOUNT_AFTER_DEPOSIT = re.compile(r"입금\s*[:：]?\s*([\d,]+)\s*원?")
MONEY = re.compile(r"(?<![\d*\-])(\d{1,3}(?:,\d{3})+|\d{4,})(?:\s*원)?(?![\d*\-])")
FULL_DATE = re.compile(r"(20\d{2})[./\-](\d{1,2})[./\-](\d{1,2})")
SHORT_DATE = re.compile(r"(?<![\d*\-])(\d{1,2})[/.](\d{1,2})(?![\d*])")
TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
ACCOUNT = re.compile(r"[\d*]+(?:-[\d*]+)+|\d*\*+\d*")


@dataclass
class Deposit:
    raw: str
    kind: str = "deposit"            # deposit | withdrawal | unknown
    bank: str = ""
    deposit_date: Optional[str] = None
    deposit_time: str = ""
    amount: int = 0
    names: List[str] = field(default_factory=list)   # 입금자 후보 (앞쪽일수록 유력)
    parsed_by: str = "rule"
    issues: List[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return "|".join([self.deposit_date or "", self.deposit_time, str(self.amount), ",".join(self.names)])

    def to_dict(self) -> dict:
        return {**asdict(self), "fingerprint": self.fingerprint}


def split_messages(text: str) -> List[str]:
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = [p for chunk in re.split(r"\n\s*\n", text)            # 빈 줄 구분
             for p in re.split(r"(?=\[Web발신\])", chunk) if p.strip()]   # 빈 줄 없이 이어 붙인 [Web발신] 문자
    out: List[str] = []
    for p in parts:
        lines = [ln for ln in p.split("\n") if ln.strip()]
        deposit_lines = [ln for ln in lines if "입금" in ln and MONEY.search(ln)]
        if len(deposit_lines) > 1 and len(deposit_lines) == len(lines):
            out.extend(deposit_lines)        # 한 줄짜리 문자를 여러 줄 붙여 넣은 경우
        else:
            out.append(p.strip())
    return out


def _year_for(month: int, day: int, today: date) -> int:
    y = today.year
    try:
        return y - 1 if date(y, month, day) > today else y
    except ValueError:
        return y


def parse_message(raw: str, today: Optional[date] = None) -> Deposit:
    today = today or date.today()
    dep = Deposit(raw=raw)
    text = raw.replace("\n", " ")

    if "입금" not in text:
        dep.kind = "withdrawal" if ("출금" in text or "지급" in text) else "unknown"
        dep.issues.append("입금 문자가 아닙니다." if dep.kind == "withdrawal" else "'입금' 표기를 찾지 못했습니다.")

    dep.bank = next((b for b in BANKS if b in text), "")

    m = AMOUNT_AFTER_DEPOSIT.search(text)
    if m:
        dep.amount = int(m[1].replace(",", ""))
    else:
        balance_at = text.find("잔액")
        for mm in MONEY.finditer(text):
            if balance_at != -1 and 0 <= mm.start() - balance_at <= 4:
                continue
            dep.amount = int(mm[1].replace(",", ""))
            break
    if not dep.amount:
        dep.issues.append("입금액을 찾지 못했습니다.")

    fd = FULL_DATE.search(text)
    sd = None if fd else SHORT_DATE.search(text)
    try:
        if fd:
            dep.deposit_date = date(int(fd[1]), int(fd[2]), int(fd[3])).isoformat()
        elif sd:
            mo, d = int(sd[1]), int(sd[2])
            dep.deposit_date = date(_year_for(mo, d, today), mo, d).isoformat()
    except ValueError:
        pass
    if not dep.deposit_date:
        dep.issues.append("입금일을 찾지 못해 오늘 날짜로 봅니다.")
        dep.deposit_date = today.isoformat()

    t = TIME.search(text)
    if t:
        dep.deposit_time = f"{int(t[1]):02d}:{t[2]}"

    dep.names = _name_candidates(text)
    if not dep.names:
        dep.issues.append("입금자명을 찾지 못했습니다.")
    return dep


def _name_candidates(text: str) -> List[str]:
    """입금자 후보: 입금액 바로 앞뒤 단어 → 나머지 순."""
    cleaned = text
    for pat in (FULL_DATE, TIME, ACCOUNT, SHORT_DATE):
        cleaned = pat.sub(" ", cleaned)
    cleaned = re.sub(r"잔액\s*[\d,]+\s*원?", " ", cleaned)
    cleaned = re.sub(r"\d[\d,]{3,}\s*원?", " \x00 ", cleaned)      # 금액 자리 표시
    cleaned = re.sub(r"[\[\]()<>{}:：|/·,]", " ", cleaned)
    tokens = cleaned.split()

    def is_name(tk: str) -> bool:
        if tk == "\x00" or tk in STOPWORDS or any(tk == b or tk == b + "은행" for b in BANKS):
            return False
        if tk.startswith("입금") or tk.startswith("잔액") or tk.startswith("출금"):
            return False
        return bool(re.search(r"[가-힣A-Za-z]", tk)) and 2 <= len(tk) <= 20

    names: List[str] = []
    amt_pos = [i for i, tk in enumerate(tokens) if tk == "\x00"]
    for i in amt_pos:
        for j in (i + 1, i - 1, i - 2):
            if 0 <= j < len(tokens) and is_name(tokens[j]) and tokens[j] not in names:
                names.append(tokens[j])
    for tk in tokens:
        if is_name(tk) and tk not in names:
            names.append(tk)
    # 은행명이 붙은 토큰(예: '신한입금')·접미사 정리
    return [re.sub(r"(님|씨)$", "", n) for n in names]


AI_PROMPT = """다음은 한국 은행의 입출금 알림 문자입니다. 입금 1건의 정보를 JSON 으로만 답하세요.
형식: {"kind": "deposit" 또는 "withdrawal", "bank": "은행명", "date": "YYYY-MM-DD 또는 null",
"time": "HH:MM 또는 빈 문자열", "amount": 정수(원), "depositor": "입금자명"}
잔액은 입금액이 아닙니다. 오늘은 %s 입니다.
문자:
%s"""


async def refine_with_ai(dep: Deposit, today: Optional[date] = None) -> Deposit:
    """규칙 분석이 불완전한 문자만 Gemini 로 보완한다. 키가 없거나 실패하면 원래 결과를 그대로 돌려준다."""
    if dep.amount and dep.names:
        return dep
    try:
        from shared.ai.gemini_client import GeminiClient, clean_json_markdown
        client = GeminiClient()
        if not client.is_available:
            return dep
        reply = await client.generate_text(AI_PROMPT % ((today or date.today()).isoformat(), dep.raw))
        data = json.loads(clean_json_markdown(reply))
    except Exception:
        return dep
    if data.get("kind") == "withdrawal":
        dep.kind = "withdrawal"
    if not dep.amount and isinstance(data.get("amount"), int) and data["amount"] > 0:
        dep.amount = data["amount"]
    if not dep.names and data.get("depositor"):
        dep.names = [str(data["depositor"]).strip()]
    if data.get("date") and re.match(r"^\d{4}-\d{2}-\d{2}$", str(data["date"])):
        dep.deposit_date = data["date"]
    dep.bank = dep.bank or str(data.get("bank") or "")
    dep.parsed_by = "ai"
    dep.issues = [i for i in dep.issues if not (("입금액" in i and dep.amount) or ("입금자명" in i and dep.names))]
    return dep
