"""
apps/rent_dist/_rent3_core/listing_pdf.py — 표준임대차계약서 참조 매물 임대조건 안내서 PDF 생성기.

임대인이 매물 등록 시 입력한 주소·전용면적·보증금·월세·관리비·관리비 포함 내역·퇴실 청소비·
입주 가능일·옵션 리스트·케이블 TV·인터넷·엘리베이터 설치 여부·표준 특약(동물 사육 금지,
실내 금연, 2개월 이상 체납 시 계약 해지 등)을 A4 PDF 로 생성·저장한다.
"""
from __future__ import annotations

import io
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

from .models import Building, Lessor, Room, date_today
from .pool import pool
from .roomregistry import public_clean

DEFAULT_OPTION_PRESETS: Tuple[str, ...] = (
    "시스템 에어컨",
    "드럼 세탁기",
    "빌트인 냉장고",
    "인덕션",
    "디지털 도어락",
    "옷장",
    "침대",
    "책상",
    "전자레인지",
    "신발장",
)

DEFAULT_MAINTENANCE_ITEMS: Tuple[str, ...] = (
    "수도료",
    "인터넷",
    "케이블TV",
    "공용전기",
    "건물청소",
    "승강기유지비",
)

_FONT_LOCK = threading.Lock()
_REGISTERED_FONTS: Optional[Tuple[str, str]] = None


def _ensure_korean_fonts() -> Tuple[str, str]:
    """Windows(맑은 고딕) · Linux(나눔고딕/Noto) · 무의존 CID(HYSMyeongJo) 순으로 한글 폰트를 등록한다."""
    global _REGISTERED_FONTS
    if _REGISTERED_FONTS is not None:
        return _REGISTERED_FONTS
    with _FONT_LOCK:
        if _REGISTERED_FONTS is not None:
            return _REGISTERED_FONTS
        from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore[import-untyped]
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]

        candidates: List[Tuple[str, str, str, str]] = [
            ("R3Malgun", r"C:\Windows\Fonts\malgun.ttf", "R3MalgunBd", r"C:\Windows\Fonts\malgunbd.ttf"),
            (
                "R3Nanum",
                "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                "R3NanumBd",
                "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
            ),
            (
                "R3Noto",
                "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
                "R3NotoBd",
                "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
            ),
        ]
        for reg_name, reg_path, bold_name, bold_path in candidates:
            if os.path.exists(reg_path):
                try:
                    pdfmetrics.registerFont(TTFont(reg_name, reg_path))
                    if os.path.exists(bold_path):
                        pdfmetrics.registerFont(TTFont(bold_name, bold_path))
                        _REGISTERED_FONTS = (reg_name, bold_name)
                    else:
                        _REGISTERED_FONTS = (reg_name, reg_name)
                    return _REGISTERED_FONTS
                except Exception:
                    continue

        cid_name = "HYSMyeongJo-Medium"
        pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
        _REGISTERED_FONTS = (cid_name, cid_name)
        return _REGISTERED_FONTS


def _clean_str_list(val: Any, max_items: int = 30) -> List[str]:
    if not val:
        return []
    raw_list: List[Any]
    if isinstance(val, str):
        raw_list = [x.strip() for x in re.split(r"[,/\n]+", val) if x.strip()]
    elif isinstance(val, (list, tuple)):
        raw_list = list(val)
    else:
        return []
    out: List[str] = []
    for item in raw_list:
        if isinstance(item, dict):
            tok = str(item.get("item") or item.get("name") or "").strip()
        else:
            tok = str(item or "").strip()
        if tok and tok not in out:
            out.append(tok[:40])
            if len(out) >= max_items:
                break
    return out


def _norm_choice(val: Any, allowed: Tuple[str, ...], default: str = "미확인") -> str:
    s = str(val or "").strip()
    if s in allowed:
        return s
    return default


def normalize_listing_spec(
    raw: Optional[Dict[str, Any]] = None,
    option_list: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """표준 임대조건 세부 스펙을 안전하게 정규화한다."""
    d = dict(raw or {})
    opts = _clean_str_list(d.get("options"))
    if not opts and option_list:
        opts = _clean_str_list(option_list)
    maint_inc = _clean_str_list(d.get("maintenance_includes"))
    try:
        term_m = int(d.get("lease_term_months") or 12)
    except (TypeError, ValueError):
        term_m = 12
    term_m = max(1, min(60, term_m))

    def _flag(key: str, default: bool = True) -> bool:
        if key not in d or d[key] is None:
            return default
        return bool(d[key])

    return {
        "configured": bool(d),
        "options": opts,
        "cable_tv": _norm_choice(d.get("cable_tv"), ("설치됨", "미설치", "별도신청", "미확인"), "미확인"),
        "internet": _norm_choice(d.get("internet"), ("설치됨", "미설치", "별도신청", "미확인"), "미확인"),
        "elevator": _norm_choice(d.get("elevator"), ("설치됨", "없음", "미확인"), "미확인"),
        "parking": _norm_choice(d.get("parking"), ("가능", "불가", "협의", "미확인"), "미확인"),
        "maintenance_includes": maint_inc,
        "maintenance_extra": str(d.get("maintenance_extra") or "").strip()[:200],
        "lease_term_months": term_m,
        "no_pets": _flag("no_pets", True),
        "no_smoking": _flag("no_smoking", True),
        "terminate_on_2m_arrears": _flag("terminate_on_2m_arrears", True),
        "no_sublease": _flag("no_sublease", True),
        "restore_clause": _flag("restore_clause", True),
        "custom_clauses": str(d.get("custom_clauses") or "").strip()[:1000],
    }


def options_to_room_list(options: List[str]) -> List[Dict[str, Any]]:
    """문자열 옵션 리스트를 Room.option_list 규격으로 변환한다."""
    return [{"item": name, "quantity": 1, "condition": "정상"} for name in _clean_str_list(options)]


def build_special_terms_from_spec(spec: Optional[Dict[str, Any]], cleaning_fee: int = 0) -> List[str]:
    """매물 표준 임대조건(listing_spec)을 전자계약 특약 조항 문구 목록으로 변환한다."""
    if not spec:
        return []
    norm = normalize_listing_spec(spec)
    lines: List[str] = []
    if norm["maintenance_includes"] or norm["maintenance_extra"]:
        parts = list(norm["maintenance_includes"])
        if norm["maintenance_extra"]:
            parts.append(norm["maintenance_extra"])
        lines.append(f"관리비 포함 내역: {', '.join(parts)} (포함 항목 외 개별 공과금은 임차인 부담).")
    if norm["no_pets"]:
        lines.append("반려동물(동물) 사육을 금지함 (위반 시 원상복구 및 손해배상 책임 부담).")
    if norm["no_smoking"]:
        lines.append("호실 내부 및 건물 공용 공간에서의 실내 흡연을 금지함.")
    if norm["terminate_on_2m_arrears"]:
        lines.append(
            "임차인의 차임(월세) 연체액이 2기(2개월)의 차임액에 달하는 때에는 "
            "임대인은 본 임대차계약을 해지할 수 있음(주택임대차보호법 제10조의8 · 민법 제640조)."
        )
    if norm["no_sublease"]:
        lines.append("임대인의 사전 서면 동의 없이 임차권을 양도하거나 목적물을 무단 전대·용도변경할 수 없음.")
    if norm["restore_clause"] and int(cleaning_fee or 0) > 0:
        lines.append(f"퇴실 시 약정 퇴실 청소비 {int(cleaning_fee):,}원은 보증금 반환 정산 시 공제함(통상 자연마모 수선은 임대인 부담).")
    if norm["custom_clauses"]:
        for raw_line in norm["custom_clauses"].splitlines():
            clean = raw_line.strip().lstrip("-•· ").strip()
            if clean:
                lines.append(clean)
    return lines


def _fmt_won(amt: Any) -> str:
    n = int(amt or 0)
    if n <= 0:
        return "0원 (없음)"
    man = n // 10000
    rem = n % 10000
    if man > 0 and rem == 0:
        return f"{n:,}원 ({man:,}만원)"
    if man > 0:
        return f"{n:,}원 ({man:,}만 {rem:,}원)"
    return f"{n:,}원"


def listing_pdf_url(room_id: str) -> str:
    return f"/api/rent3/pool/listings/{room_id}/terms.pdf"


def listing_pdf_path(room_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(room_id or "room"))
    out_dir = os.path.join(pool().pool_dir, "uploads", "listing_pdfs")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"{safe}.pdf")


def build_listing_terms_pdf(
    room: Room,
    building: Optional[Building] = None,
    lessor: Optional[Lessor] = None,
) -> bytes:
    """표준임대차계약서 주요 항목을 정리한 A4 PDF 바이트를 생성한다."""
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
    from reportlab.lib.units import mm  # type: ignore[import-untyped]
    from reportlab.platypus import (  # type: ignore[import-untyped]
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    reg_font, bold_font = _ensure_korean_fonts()
    shown = room.as_listing()
    spec = normalize_listing_spec(shown.listing_spec, shown.option_list)

    bldg_name = (building.building_name if building else shown.building_name) or shown.branch
    address = (building.address if building else "") or "임대인 건물 소재지(계약 시 등기부 대조)"
    lessor_name = (lessor.name if lessor else "") or "건물 소유주"
    lessor_phone = (lessor.phone if (lessor and lessor.phone) else (building.manager_phone if building else "")) or "-"

    dep = int(shown.deposit or 0)
    contract_dep = dep // 10 if dep > 0 else 0
    balance_dep = max(dep - contract_dep, 0)
    rent = int(shown.monthly_rent or 0)
    maint = int(shown.maintenance_fee or 0)
    clean_fee = int(shown.cleaning_fee or 0)
    area_sqm = float(shown.area_sqm or 0.0)
    pyeong = round(area_sqm / 3.3058, 1) if area_sqm > 0 else 0.0
    floor_text = f"{shown.floor}층" + (f" / 전체 {shown.total_floors}층" if shown.total_floors else "")
    avail_text = (shown.available_from or "즉시입주").strip()
    is_clean = public_clean(shown)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
        title=f"{bldg_name} {shown.room_no}호 표준 임대조건 안내서",
        author="Rent3 OpenRent",
    )

    navy = colors.HexColor("#0F172A")
    slate = colors.HexColor("#475569")
    accent = colors.HexColor("#1D4ED8")
    light_bg = colors.HexColor("#F8FAFC")
    head_bg = colors.HexColor("#EFF6FF")
    border_c = colors.HexColor("#CBD5E1")
    ok_bg = colors.HexColor("#F0FDF4")
    warn_bg = colors.HexColor("#FFFBEB")

    st_title = ParagraphStyle("Title", fontName=bold_font, fontSize=16, leading=21, textColor=colors.white)
    st_sub = ParagraphStyle("Sub", fontName=reg_font, fontSize=9.5, leading=13, textColor=colors.HexColor("#DBEAFE"))
    st_sec = ParagraphStyle("Sec", fontName=bold_font, fontSize=11, leading=15, textColor=navy, spaceBefore=8, spaceAfter=4)
    st_th = ParagraphStyle("Th", fontName=bold_font, fontSize=9, leading=13, textColor=navy)
    st_td = ParagraphStyle("Td", fontName=reg_font, fontSize=9, leading=13.5, textColor=colors.HexColor("#1E293B"))
    st_td_b = ParagraphStyle("TdB", fontName=bold_font, fontSize=9.5, leading=13.5, textColor=navy)
    st_clause = ParagraphStyle("Clause", fontName=reg_font, fontSize=9, leading=14, textColor=colors.HexColor("#1E293B"))
    st_foot = ParagraphStyle("Foot", fontName=reg_font, fontSize=8.5, leading=12.5, textColor=slate)

    def p(text: str, style: ParagraphStyle = st_td) -> Paragraph:
        return Paragraph(xml_escape(str(text or "-")).replace("\n", "<br/>"), style)

    story: List[Any] = []
    usable_w = A4[0] - 28 * mm

    # 상단 배너
    banner = Table(
        [
            [
                Paragraph(xml_escape(f"표준 임대조건 안내서 — {bldg_name} {shown.room_no}호"), st_title),
            ],
            [
                Paragraph(
                    xml_escape(
                        f"주택임대차표준계약서 참조 · 중개수수료 0원 소유주 직거래 · 작성일 {date_today().isoformat()}"
                    ),
                    st_sub,
                ),
            ],
        ],
        colWidths=[usable_w],
    )
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), navy),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (0, 0), 10),
                ("BOTTOMPADDING", (0, 0), (0, 0), 2),
                ("TOPPADDING", (0, 1), (0, 1), 0),
                ("BOTTOMPADDING", (0, 1), (0, 1), 9),
            ]
        )
    )
    story.append(banner)
    story.append(Spacer(1, 5 * mm))

    # 핵심 금액 요약 4열 카드
    kpi = Table(
        [
            [p("보증금", st_th), p("월 차임(월세)", st_th), p("월 관리비", st_th), p("입주 가능일", st_th)],
            [p(_fmt_won(dep), st_td_b), p(_fmt_won(rent), st_td_b), p(_fmt_won(maint), st_td_b), p(avail_text, st_td_b)],
        ],
        colWidths=[usable_w / 4] * 4,
    )
    kpi.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                ("BACKGROUND", (0, 1), (-1, 1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.6, border_c),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(kpi)

    # 1. 임차주택의 표시
    story.append(Paragraph("1. 임차목적물의 표시 (표준임대차계약서 제1조 기준)", st_sec))
    cw4 = [usable_w * 0.18, usable_w * 0.32, usable_w * 0.18, usable_w * 0.32]
    t1 = Table(
        [
            [p("소재지 (주소)", st_th), p(address), "", ""],
            [
                p("건물명 / 호실", st_th),
                p(f"{bldg_name} {shown.room_no}호"),
                p("부동산 유형", st_th),
                p(shown.room_category + (" (부가세 별도)" if shown.vat_applicable else " (면세)")),
            ],
            [
                p("전용면적", st_th),
                p(f"{area_sqm}㎡ (약 {pyeong}평)" if area_sqm > 0 else "계약 시 확인"),
                p("해당층 / 방향", st_th),
                p(f"{floor_text}" + (f" · {shown.direction}" if shown.direction else "")),
            ],
            [
                p("입주 가능일", st_th),
                p(avail_text + (" (만기 전출예정 사전매물)" if shown.pre_moveout_notice else "")),
                p("등기부 상태", st_th),
                p("확인 완료 (소유자 일치·권리 깨끗함)" if is_clean else "계약 단계에서 등기부등본 직접 대조"),
            ],
        ],
        colWidths=cw4,
    )
    t1.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 0), (3, 0)),
                ("BACKGROUND", (0, 0), (0, -1), light_bg),
                ("BACKGROUND", (2, 1), (2, -1), light_bg),
                ("GRID", (0, 0), (-1, -1), 0.5, border_c),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(t1)

    # 2. 보증금·차임·관리비 조건
    story.append(Paragraph("2. 보증금 · 월세 · 관리비 및 납부 조건", st_sec))
    maint_items_txt = ", ".join(spec["maintenance_includes"]) if spec["maintenance_includes"] else "별도 협의 (개별 공과금 별도)"
    if spec["maintenance_extra"]:
        maint_items_txt = f"{maint_items_txt} / {spec['maintenance_extra']}" if spec["maintenance_includes"] else spec["maintenance_extra"]
    t2 = Table(
        [
            [
                p("임대보증금", st_th),
                p(_fmt_won(dep), st_td_b),
                p("계약금 / 잔금", st_th),
                p(f"계약금 {_fmt_won(contract_dep)} (10%) / 잔금 {_fmt_won(balance_dep)}"),
            ],
            [
                p("월 차임(월세)", st_th),
                p(_fmt_won(rent), st_td_b),
                p("납부 시기", st_th),
                p(f"{shown.pay_timing} (" + ("입주월 비과세 · 익월 납부" if shown.pay_timing == "후불" else "매월 선납") + ")"),
            ],
            [
                p("월 관리비", st_th),
                p(_fmt_won(maint)),
                p("임대 기간", st_th),
                p(f"기본 {spec['lease_term_months']}개월 (협의 가능)"),
            ],
            [p("관리비 포함 내역", st_th), p(maint_items_txt), "", ""],
            [
                p("퇴실 청소비", st_th),
                p(f"{_fmt_won(clean_fee)} (약정 시 퇴실 보증금 공제)" if clean_fee > 0 else "별도 약정 없음 (협의)"),
                p("중개수수료", st_th),
                p("0원 (소유주 직거래 전자계약)", st_td_b),
            ],
        ],
        colWidths=cw4,
    )
    t2.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 3), (3, 3)),
                ("BACKGROUND", (0, 0), (0, -1), light_bg),
                ("BACKGROUND", (2, 0), (2, 2), light_bg),
                ("BACKGROUND", (2, 4), (2, 4), light_bg),
                ("GRID", (0, 0), (-1, -1), 0.5, border_c),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(t2)

    # 3. 옵션 리스트 및 시설 설치 여부
    story.append(Paragraph("3. 내부 옵션 리스트 및 건물 시설 설치 여부", st_sec))
    opt_text = ", ".join(spec["options"]) if spec["options"] else "현장 확인 및 계약 시 옵션 체크리스트 확정"
    t3 = Table(
        [
            [p("옵션 리스트", st_th), p(opt_text), "", ""],
            [
                p("케이블 TV", st_th),
                p(spec["cable_tv"]),
                p("인터넷 / Wi-Fi", st_th),
                p(spec["internet"]),
            ],
            [
                p("엘리베이터", st_th),
                p(spec["elevator"]),
                p("주차 가능 여부", st_th),
                p(spec["parking"]),
            ],
        ],
        colWidths=cw4,
    )
    t3.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 0), (3, 0)),
                ("BACKGROUND", (0, 0), (0, -1), light_bg),
                ("BACKGROUND", (2, 1), (2, -1), light_bg),
                ("GRID", (0, 0), (-1, -1), 0.5, border_c),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(t3)

    # 4. 표준 임대조건 · 특약 및 계약 해지 사유
    story.append(Paragraph("4. 주요 임대조건 · 표준 특약 및 계약 해지 기준", st_sec))
    clauses: List[str] = [
        f"• 반려동물(동물) 사육: {'금지 (위반 시 원상복구 및 손해배상 책임)' if spec['no_pets'] else '임대인과 사전 협의 가능'}",
        f"• 실내 흡연: {'금지 (호실 내부 및 공용 공간 금연)' if spec['no_smoking'] else '별도 협의'}",
        (
            "• 차임 연체 시 계약 해지: 2개월(2기) 이상 차임(월세) 체납 시 임대인은 계약을 해지할 수 있음 "
            "(주택임대차보호법 제10조의8 · 민법 제640조)"
            if spec["terminate_on_2m_arrears"]
            else "• 차임 연체 시 계약 해지: 주택임대차보호법 및 민법 일반 규정에 따름"
        ),
        (
            "• 전대 및 양도: 임대인의 서면 동의 없는 무단 전대·양도·용도변경을 금지함"
            if spec["no_sublease"]
            else "• 전대 및 양도: 임대인과 별도 서면 합의에 따름"
        ),
        (
            "• 수선 및 원상복구: 자연 마모에 의한 시설 고장은 임대인이 수리하며, 퇴실 시 통상 손모는 공제하지 않음"
            + (f" (약정 퇴실 청소비 {_fmt_won(clean_fee)} 별도 공제)" if clean_fee > 0 else "")
        ),
    ]
    if shown.status_description:
        clauses.append(f"• 매물 소개: {shown.status_description}")
    if spec["custom_clauses"]:
        for ln in spec["custom_clauses"].splitlines():
            clean_ln = ln.strip().lstrip("-•· ").strip()
            if clean_ln:
                clauses.append(f"• 추가 특약: {clean_ln}")

    t4 = Table([[p("\n".join(clauses), st_clause)]], colWidths=[usable_w])
    t4.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), warn_bg),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#F59E0B")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(t4)
    story.append(Spacer(1, 4 * mm))

    # 5. 임대인 및 직거래 안내
    t5 = Table(
        [
            [
                p(
                    f"임대인: {lessor_name}   |   연락처: {lessor_phone}   |   건물: {bldg_name} {shown.room_no}호\n"
                    "※ 본 문서는 임대인이 매물 등록 시 표준임대차계약서 주요 항목을 사전 고지하기 위해 작성한 임대조건 안내서입니다. "
                    "실제 계약 체결 전 당사자 신분 확인·등기부등본 열람·시설 점검 후 공동 전자계약서 서명으로 최종 확정됩니다.",
                    st_foot,
                )
            ]
        ],
        colWidths=[usable_w],
    )
    t5.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), ok_bg),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#86EFAC")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(t5)

    doc.build(story)
    return buf.getvalue()


def save_listing_pdf(
    room: Room,
    building: Optional[Building] = None,
    lessor: Optional[Lessor] = None,
) -> str:
    """매물 임대조건 안내서 PDF를 생성하여 pool uploads 폴더에 원자적으로 저장한다."""
    pdf_bytes = build_listing_terms_pdf(room, building=building, lessor=lessor)
    path = listing_pdf_path(room.id)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fp:
        fp.write(pdf_bytes)
    os.replace(tmp, path)
    return path
