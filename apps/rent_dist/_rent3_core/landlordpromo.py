"""
apps/rent_dist/_rent3_core/landlordpromo.py — 임대인(집주인) 대상 홍보 글 템플릿 (유치 P3). 운영자가 카페·밴드·블로그에 직접 올리는 글을 만든다.

원칙
- **광고임을 맨 앞에 밝힌다**('[광고] 운영자 작성'). 이용 후기·경험담처럼 꾸미지 않는다.
- 요금은 offer.terms() 의 실제 값, 무료 기간은 설정돼 있을 때만 쓴다. 세무는 '홈택스 신고용 입금 증빙 자료(CSV) 정리'까지만 말한다.
- 효과·절감액·과태료 면제 같은 단정·보장 표현을 쓰지 않는다(시험으로 고정). 올리기 전 채널 규정 확인 목록을 함께 준다.
- 링크는 `/rent3/lessor-intro.html?ref=<채널>&campaign=<캠페인>` 이라 P0 유입 귀속에 잡힌다.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from core.errors import DomainError

from . import offer

CHANNELS = {
    "cafe": {"name": "네이버 카페", "icon": "☕"},
    "band": {"name": "네이버 밴드", "icon": "👥"},
    "blog": {"name": "네이버 블로그", "icon": "📝"},
}
SEASONS = {
    "none": "상시",
    "vat_jan": "1월 부가세 신고철",
    "tax_may": "5월 종합소득세 신고철",
    "vat_jul": "7월 부가세 신고철",
}
AD_LABEL = "[광고] 운영자가 직접 작성한 서비스 소개글입니다."
CHECKLIST = [
    "카페·밴드의 홍보·광고 글 허용 여부와 허용 게시판을 가입 규정에서 확인했는가",
    "운영진 사전 승인이 필요한 곳이면 승인을 받았는가",
    "글 맨 앞의 '[광고]' 표시를 지우지 않았는가 (후기·경험담으로 바꿔 쓰지 않기)",
    "요금·무료 기간이 지금 실제 조건과 같은가 (바뀌었다면 글도 고치기)",
    "같은 글을 여러 게시판에 반복해서 올리지 않았는가",
]
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _season_line(season: str) -> str:
    return {
        "vat_jan": "1월은 부가세 신고철입니다. 월세 입금 내역을 한곳에 모아 두면 자료를 찾기 편합니다.",
        "tax_may": "5월은 종합소득세 신고철입니다. 월세 입금 내역을 홈택스 신고에 참고할 자료(CSV)로 정리해 둘 수 있습니다.",
        "vat_jul": "7월은 부가세 신고철입니다. 월세 입금 내역을 한곳에 모아 두면 자료를 찾기 편합니다.",
    }.get(season, "")


def generate(channel: str, season: str = "none", campaign: str = "", base_url: str = "") -> Dict[str, Any]:
    if channel not in CHANNELS:
        raise DomainError("지원하지 않는 채널입니다.", hint="cafe · band · blog 중에서 골라 주세요.")
    if season not in SEASONS:
        raise DomainError("지원하지 않는 시기입니다.", hint="none · vat_jan · tax_may · vat_jul 중에서 골라 주세요.")
    camp = campaign.strip() or f"{channel}-{season}"
    if not _TOKEN.match(camp):
        raise DomainError("캠페인 이름은 영문·숫자·-·_ 만, 32자 이하로 써 주세요.", hint="예: cafe-spring26")
    t = offer.terms()
    link = f"{base_url.rstrip('/')}/rent3/lessor-intro.html?ref={channel}&campaign={camp}"
    fee = f"월 {t['monthly_fee']:,}원(부가세 포함, 건물당 정액)"
    free = f"\n- {t['free_text']}" if t["free_text"] else ""
    sl = _season_line(season)
    body_lines = [
        AD_LABEL, "",
        "원룸·다가구 임대하시는 집주인분들께 도움이 될 수 있는 도구를 소개합니다.", "",
        "■ 할 수 있는 것",
        "- 세입자와 직접 계약(중개수수료 없음) — 등기부 확인·서명은 비대면 전자계약으로 진행",
        "- 은행 입금 문자를 붙여넣어 호실별 수납 장부에 기록, 미납 월·금액 자동 계산",
        "- 장부를 홈택스 신고용 입금 증빙 자료(CSV)로 정리" + (f"\n  ({sl})" if sl else ""),
        "", "■ 이용 조건 (숨기지 않고 적습니다)",
        f"- 가맹비 {fee}" + free,
        f"- {t['after_approval_only']}",
        f"- {t['tax_scope']}",
        f"- {t['individual_note']}",
        "", "■ 참고",
        "- 직접 입력해 비교해 볼 수 있는 계산기와 샘플 장부(가상 자료)가 안내 페이지에 있습니다.",
        f"- 서비스 소개·요금 보기: {link}",
        "", "운영자에게 질문은 댓글로 남겨 주세요. 맞지 않으면 이용하지 않으셔도 됩니다.",
    ]
    content = "\n".join(body_lines)
    title = {"cafe": "[광고] 원룸 집주인용 월세 장부·직접 계약 도구 소개",
             "band": "[광고] 집주인분들께 월세 장부 정리 도구 소개드립니다",
             "blog": "[광고] 원룸 월세 장부와 직접 계약, 요금까지 숨김없이 소개합니다"}[channel]
    if sl:
        title = title.replace("[광고] ", f"[광고] {SEASONS[season].split(' ')[0]} ", 1)
    out = {"channel": channel, "channel_name": CHANNELS[channel]["name"], "icon": CHANNELS[channel]["icon"], "season": season,
           "campaign": camp, "title": title, "content": content, "link": link, "checklist": CHECKLIST}
    if channel == "blog":
        out["tags"] = ["원룸임대", "월세장부", "임대인", "직접계약", "홈택스자료정리", "광고"]
    return out
