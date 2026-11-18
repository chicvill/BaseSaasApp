"""
apps/rent_dist/_rent3_core/promo.py — 멀티 채널 원클릭 홍보 생성기

당근마켓, 네이버 피터팬, 에브리타임, 네이버 블로그, 카카오톡/문자 5대 채널에 최적화된
매물 소개글과 직거래 안내 링크를 생성한다.
사실에 기반한 내용만 반영하며, 입력되지 않은 정보는 지어내지 않고 생략한다.
등기부 검증 배지는 roomregistry.public_clean() 검증을 통과한 경우에만 부착한다.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from core.errors import DomainError
from .models import Building, Lessor, Room
from .roomregistry import public_clean


def format_won(amt: int) -> str:
    """원 단위를 '500만 원', '40만 원' 등 한국어 단위로 읽기 쉽게 포맷팅."""
    if amt <= 0:
        return "0원"
    man = amt // 10000
    rem = amt % 10000
    if man > 0 and rem > 0:
        return f"{man:,}만 {rem:,}원"
    if man > 0:
        return f"{man:,}만 원"
    return f"{amt:,}원"


def clean_options(opts: Any) -> List[str]:
    """옵션 리스트에서 문자열 추출."""
    if not opts:
        return []
    res = []
    for o in opts:
        if isinstance(o, str) and o.strip():
            res.append(o.strip())
        elif isinstance(o, dict) and o.get("name"):
            res.append(str(o["name"]).strip())
        elif isinstance(o, dict) and o.get("item"):
            res.append(str(o["item"]).strip())
    return res


def generate_promo(
    room: Room,
    building: Optional[Building] = None,
    lessor: Optional[Lessor] = None,
    base_url: str = "",
) -> Dict[str, Any]:
    """호실 객체를 바탕으로 5개 채널별 최적화 홍보 텍스트 묶음을 산출한다.
    
    규칙:
    1. 입주 중인 호실은 만기 전출 예정(pre_moveout_notice)일 때만 생성 허용(공개 표시가 남아 있어도 입주 중이면 막는다).
    2. 입력되지 않은 정보는 가공/지어내지 않고 해당 항목을 생략한다.
    3. 등기부 확인 문구는 public_clean(room)이 참일 때만 표시한다.
    4. 과장 문구를 지양하고 비대면 직거래 및 수수료 0원 등 사실 기반 표현을 사용한다.
    """
    if room.is_occupied and not room.pre_moveout_notice:
        raise DomainError(
            "입주 중인 호실은 만기 전출 예정일 때만 홍보글을 생성할 수 있습니다.",
            hint="임차인의 만기 전출 의사가 확인되었거나 공실일 때 홍보를 진행해 주세요.",
        )

    bldg_name = (building.building_name if building else room.building_name) or ""
    address = (building.address if building else "") or ""
    room_label = f"{bldg_name} {room.room_no}호" if bldg_name else f"{room.room_no}호"

    dep_str = format_won(int(room.deposit or 0))
    rent_str = format_won(int(room.monthly_rent or 0))
    maint_amt = int(room.maintenance_fee or 0)
    maint_str = format_won(maint_amt) if maint_amt > 0 else "없음"

    # 층·전용면적은 방 모델에 기본값(2층·23㎡)이 있어 '입력한 값'과 구분되지 않는다 → 기본값과 같으면 입력된 것으로 보지 않고 생략한다(홍보 글에서 직접 적을 수 있다)
    _fields = Room.model_fields
    area_sqm = float(room.area_sqm or 0)
    if area_sqm == float(_fields["area_sqm"].default):
        area_sqm = 0.0
    pyeong_str = f"약 {round(area_sqm / 3.3058, 1)}평" if area_sqm > 0 else ""
    area_str = f"전용 {area_sqm}㎡" + (f" ({pyeong_str})" if pyeong_str else "") if area_sqm > 0 else ""

    floor_parts = []
    if room.floor and (room.floor != _fields["floor"].default or (room.total_floors or 0) > 0):
        floor_parts.append(f"{room.floor}층")
    if room.total_floors and room.total_floors > 0:
        floor_parts.append(f"전체 {room.total_floors}층")
    floor_str = " / ".join(floor_parts) if floor_parts else ""

    direction_str = (room.direction or "").strip()
    avail_str = (room.available_from or "").strip()          # 입력하지 않았으면 '즉시입주' 같은 값을 지어내지 않고 생략한다
    category = (room.room_category or "원룸").strip()

    options = clean_options(room.option_list)
    options_str = ", ".join(options) if options else ""

    desc = (room.status_description or "").strip()

    root_url = base_url.rstrip("/") if base_url else ""

    def make_link(ch: str) -> str:
        path = f"/rent3/?room={room.id}&ref={ch}"
        return f"{root_url}{path}" if root_url else path

    # 등기부 상태: roomregistry.public_clean(room)을 엄격히 검증
    is_clean = public_clean(room)
    registry_badge = "🛡️ 등기부 확인 완료 (소유자 일치 · 권리 깨끗함)" if is_clean else ""

    # 1. 🥕 당근마켓
    daangn_link = make_link("daangn")
    daangn_title = f"[수수료0원/집주인직접] {room_label} {category} 직거래"

    d_info = []
    if address:
        d_info.append(f"• 위치: {address}" + (f" ({floor_str})" if floor_str else ""))
    elif floor_str:
        d_info.append(f"• 층: {floor_str}")
    if area_str:
        d_info.append(f"• 구조: {category} ({area_str})")
    else:
        d_info.append(f"• 구조: {category}")
    d_info.append(f"• 금액: 보증금 {dep_str} / 월세 {rent_str}")
    if maint_amt > 0:
        d_info.append(f"• 관리비: {maint_str}")
    if avail_str:
        d_info.append(f"• 입주가능일: {avail_str}")
    if direction_str:
        d_info.append(f"• 방향: {direction_str}")
    if options_str:
        d_info.append(f"• 옵션: {options_str}")

    d_feats = []
    if desc:
        d_feats.append(f"• {desc}")
    d_feats.append("• 공인중개사 중개수수료(복비) 없는 집주인 직거래!")
    if bldg_name:
        d_feats.append(f"• {bldg_name} 임대인이 직접 관리합니다.")
    if registry_badge:
        d_feats.append(f"• {registry_badge}")
        d_feats.append("• 비대면 전자계약으로 진행됩니다.")
    else:
        d_feats.append("• 비대면 직거래 전자계약으로 진행됩니다.")

    daangn_content = f"""[수수료 0원 / 집주인 직접 등록] {room_label} 직거래합니다!

🏠 매물 정보
{chr(10).join(d_info)}

✨ 특징 및 안내
{chr(10).join(d_feats)}

👇 매물 상세 확인 & 계약 안내 링크
{daangn_link}

💬 편하게 채팅으로 문의주세요! (방 보실 분 환영)"""

    # 2. ☕ 네이버 피터팬 (지어낸 난방방식/포함항목 제거)
    peter_link = make_link("peterpan")
    peter_title = f"[직거래] {room_label} / 보증금 {dep_str} 월세 {rent_str}" + (f" / {avail_str}" if avail_str else "") + " / 수수료 0원"

    p_items = [f"1. 매물종류: {category}"]
    if address:
        p_items.append(f"2. 매물위치: {address}")
    if floor_str:
        p_items.append(f"3. 해당층/건물층: {floor_str}")
    if area_str:
        p_items.append(f"4. 전용면적: {area_str}")
    p_items.append(f"5. 거래금액: 보증금 {dep_str} / 월세 {rent_str}")
    if maint_amt > 0:
        p_items.append(f"6. 관리비: {maint_str}")
    if avail_str:
        p_items.append(f"7. 입주가능일: {avail_str}")
    if options_str:
        p_items.append(f"8. 옵션사항: {options_str}")

    p_descs = []
    if desc:
        p_descs.append(desc)
    p_descs.append("- 집주인 직접 계약으로 중개수수료(복비)가 전혀 없습니다.")
    if registry_badge:
        p_descs.append(f"- {registry_badge}")

    peter_content = f"""■ 매물 정보 (피터팬 직거래 표준 양식)
{chr(10).join(p_items)}

■ 매물 상세설명
{chr(10).join(p_descs)}

■ 비대면 직거래 전자계약 링크
{peter_link}
(※ 비대면으로 매물 정보 확인 및 전자계약 체결이 가능합니다)"""

    # 3. 🎓 에브리타임 (과장 표현 제거)
    eta_link = make_link("everytime")
    eta_title = f"[복비0원/직거래] {room_label} 보{dep_str}/월{rent_str} 직거래"

    e_items = []
    if address:
        e_items.append(f"📍 위치: {address}")
    e_items.append(f"💰 금액: 보증금 {dep_str} / 월세 {rent_str}" + (f" (관리비 {maint_str})" if maint_amt > 0 else ""))
    if avail_str or floor_str:
        e_items.append("🚪 " + (f"입주: {avail_str}" if avail_str else "층") + (f" ({floor_str})" if floor_str and avail_str else (f" {floor_str}" if floor_str else "")))
    if options_str:
        e_items.append(f"🛋️ 옵션: {options_str}")

    e_feats = []
    if desc:
        e_feats.append(f"• {desc}")
    e_feats.append("• 중개수수료 0원 집주인 직거래")
    if registry_badge:
        e_feats.append(f"• {registry_badge}")
    e_feats.append("• 비대면 직거래 전자계약 지원")

    eta_content = f"""선후배님들 중개수수료 없이 직거래하실 수 있는 방입니다. 🙌
집주인 직접 직거래 매물입니다.

{chr(10).join(e_items)}

💡 특징:
{chr(10).join(e_feats)}

👇 매물 상세 확인 & 직거래 계약 안내:
{eta_link}

궁금한 점 있으시면 편하게 연락주세요!"""

    # 4. 📝 네이버 블로그 (지어낸 역명 제거, 사실 기반 구조화)
    blog_link = make_link("blog")
    blog_titles = []
    if bldg_name and address:
        blog_titles.append(f"[{bldg_name}] {address} {category} 월세 직거래 (보증금 {dep_str} / 월세 {rent_str}) 수수료 0원")
    elif bldg_name:
        blog_titles.append(f"[{bldg_name}] {room.room_no}호 {category} 월세 직거래 (보증금 {dep_str} / 월세 {rent_str})")
    else:
        blog_titles.append(f"{room_label} {category} 월세 직거래 (보증금 {dep_str} / 월세 {rent_str}) 수수료 0원")

    blog_titles.append(f"{room_label} {category} 직거래 매물 안내" + (f" - {avail_str}" if avail_str else ""))
    blog_titles.append(f"복비 아끼는 부동산 직거래 - {room_label} 비대면 전자계약")

    blog_tags = ["원룸직거래", "부동산직거래", "중개수수료무료", "복비0원", "월세직거래", "비대면전자계약", "자취방구하기", "OpenRent"]
    if bldg_name:
        blog_tags.append(bldg_name)

    b_sec1 = []
    if address:
        b_sec1.append(f"* **소재지**: {address}")
    if floor_str or area_str:
        sp = [p for p in (floor_str, area_str) if p]
        b_sec1.append(f"* **층수/면적**: {' / '.join(sp)}")
    b_sec1.append(f"* **임대조건**: **보증금 {dep_str} / 월세 {rent_str}**")
    if maint_amt > 0:
        b_sec1.append(f"* **관리비**: 매월 {maint_str}")
    if avail_str:
        b_sec1.append(f"* **입주시기**: {avail_str}")
    if direction_str:
        b_sec1.append(f"* **방향**: {direction_str}")

    b_sec2 = []
    if options_str:
        b_sec2.append(f"* **옵션 품목**: {options_str}")
    else:
        b_sec2.append("* **옵션**: 호실 세부 정보 참조")

    b_sec3 = []
    if desc:
        b_sec3.append(desc)
    if bldg_name:
        b_sec3.append(f"{bldg_name} 임대인이 직접 관리합니다.")
    else:
        b_sec3.append("임대인이 직접 관리합니다.")

    b_sec4 = []
    if registry_badge:
        b_sec4.append(f"* {registry_badge}")
    b_sec4.append("* OpenRent 비대면 전자계약으로 계약을 진행합니다.")

    blog_content = f"""# {blog_titles[0]}

안녕하세요! 집주인이 직접 등록한 **{room_label}** 임대 매물을 소개합니다.
공인중개사 중개수수료(복비) 없이 직거래로 입주하실 수 있는 매물입니다.

---

## 1. 기본 정보 및 가격
{chr(10).join(b_sec1)}

---

## 2. 내부 시설 및 옵션 안내
{chr(10).join(b_sec2)}

---

## 3. 매물 특징 및 추천 이유
{chr(10).join(b_sec3)}

---

## 4. 비대면 직거래 안내
{chr(10).join(b_sec4)}

👉 **매물 확인 및 계약 안내 링크**:
{blog_link}

---
태그: #{' #'.join(blog_tags)}"""

    # 5. 💬 카카오톡 / 문자 요약본
    kakao_link = make_link("kakao")
    k_items = [f"🏡 {room_label} ({category})"]
    if address:
        k_items.append(f"• 위치: {address}" + (f" ({floor_str})" if floor_str else ""))
    elif floor_str:
        k_items.append(f"• 층: {floor_str}")
    k_items.append(f"• 조건: 보증금 {dep_str} / 월세 {rent_str}" + (f" (관리비 {maint_str})" if maint_amt > 0 else ""))
    if avail_str or direction_str:
        k_items.append("• 입주: " + (avail_str or "") + (f" ({direction_str})" if direction_str and avail_str else (direction_str or "")))
    if options_str:
        k_items.append(f"• 옵션: {options_str}")
    if registry_badge:
        k_items.append(f"• {registry_badge}")
    k_items.append("• 특징: 중개수수료 0원 집주인 직거래")

    kakao_content = f"""[집주인 직거래 매물 안내]
{chr(10).join(k_items)}

👇 매물 상세 및 계약 안내 링크
{kakao_link}"""

    return {
        "room_no": room.room_no,
        "room_id": room.id,
        "building_name": bldg_name,
        "deposit_formatted": dep_str,
        "rent_formatted": rent_str,
        "clean_title": is_clean,
        "channels": {
            "daangn": {
                "name": "당근마켓",
                "icon": "🥕",
                "title": daangn_title,
                "content": daangn_content,
                "link": daangn_link,
                "scheme": f"karrot://search?q={room.room_no}호",
            },
            "peterpan": {
                "name": "네이버 피터팬",
                "icon": "☕",
                "title": peter_title,
                "content": peter_content,
                "link": peter_link,
            },
            "everytime": {
                "name": "에브리타임",
                "icon": "🎓",
                "title": eta_title,
                "content": eta_content,
                "link": eta_link,
            },
            "blog": {
                "name": "네이버 블로그",
                "icon": "📝",
                "title": blog_titles[0],
                "recommended_titles": blog_titles,
                "content": blog_content,
                "link": blog_link,
                "tags": blog_tags,
            },
            "kakao": {
                "name": "카카오톡/문자",
                "icon": "💬",
                "title": f"{room_label} 직거래 안내",
                "content": kakao_content,
                "link": kakao_link,
            },
        },
    }
