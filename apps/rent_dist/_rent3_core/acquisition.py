"""
apps/rent_dist/_rent3_core/acquisition.py — 임대인 유입 귀속(어느 채널·캠페인·소개로 가입했는가).

홍보 링크(`?ref=daangn&campaign=…&from=…`)로 들어온 사람이 임대인으로 가입하면, 그 값을 `Lessor.acq` 에 남겨 채널별로 가입 → 가맹 승인 → 첫 방 등록 → 첫 수납 기록까지의
전환을 비교할 수 있게 한다.

원칙
- 짧은 영숫자(`-`·`_` 허용, 32자 이하) 표식만 저장한다. 그 밖의 값은 오류 없이 버린다(가입을 막지 않는다). 방문 기록·이름·연락처는 저장하지 않는다.
- 값은 '처음 닿은 링크'(first touch) 기준이다. 브라우저가 30일 안의 첫 값을 보관했다가 가입 때 보낸다(만료는 화면 쪽에서 판단, 서버는 형식만 검증).
- 서버에서 가입 시 한 번만 기록하고 이후 바꾸지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List

from .models import Lessor, Pay, Room
from .pool import now_iso

TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KEYS = ("ref", "campaign", "from_tenant")
NONE_LABEL = "(출처 없음)"


def _tok(v: Any) -> str:
    s = str(v or "").strip()
    return s if TOKEN.match(s) else ""


def sanitize(raw: Any) -> Dict[str, str]:
    """화면이 보낸 값에서 허용되는 표식만 남긴다. 아무것도 없으면 빈 사전."""
    if not isinstance(raw, dict):
        return {}
    out = {"ref": _tok(raw.get("ref")), "campaign": _tok(raw.get("campaign")), "from_tenant": _tok(raw.get("from_tenant") or raw.get("from"))}
    out = {k: v for k, v in out.items() if v}
    if out:
        fs = str(raw.get("first_seen") or "").strip()[:10]
        out["first_seen"] = fs if re.match(r"^\d{4}-\d{2}-\d{2}$", fs) else ""
    return out


def record(raw: Any) -> Dict[str, str]:
    """가입 시 저장할 값: 표식 + 가입 시각. 표식이 없으면 빈 사전(= 출처 없음)."""
    clean = sanitize(raw)
    if not clean:
        return {}
    return {**clean, "registered_at": now_iso()}


def summary() -> Dict[str, Any]:
    """채널(ref)별·캠페인별 임대인 전환 단계 수. 읽기 전용."""
    pay_branches = {p.branch for p in Pay.all()}
    room_branches = defaultdict(int)
    for r in Room.all():
        room_branches[r.branch] += 1

    def blank() -> Dict[str, int]:
        return {"signups": 0, "approved": 0, "with_room": 0, "with_payment": 0}

    by_ref: Dict[str, Dict[str, int]] = defaultdict(blank)
    by_campaign: Dict[str, Dict[str, int]] = defaultdict(blank)
    lessors = Lessor.all()
    for l in lessors:
        acq = l.acq or {}
        step = {"signups": 1, "approved": int(l.status == "ACTIVE"), "with_room": int(room_branches.get(l.branch, 0) > 0), "with_payment": int(l.branch in pay_branches)}
        for bucket, key in ((by_ref, acq.get("ref") or NONE_LABEL), (by_campaign, acq.get("campaign") or NONE_LABEL)):
            for k, v in step.items():
                bucket[key][k] += v
    by_from: Dict[str, Dict[str, int]] = defaultdict(blank)
    for l in lessors:
        code = (l.acq or {}).get("from_tenant")
        if code:
            for k, v in {"signups": 1, "approved": int(l.status == "ACTIVE"), "with_room": int(room_branches.get(l.branch, 0) > 0), "with_payment": int(l.branch in pay_branches)}.items():
                by_from[code][k] += v
    rows = lambda b: sorted(({"key": k, **v} for k, v in b.items()), key=lambda x: (-x["signups"], x["key"]))
    tracked = sum(1 for l in lessors if (l.acq or {}).get("ref") or (l.acq or {}).get("campaign") or (l.acq or {}).get("from_tenant"))
    from .models import Tenant
    sent = sum(int(t.lessor_invite_total or 0) for t in Tenant.all())
    return {"total_lessors": len(lessors), "tracked": tracked, "invites_sent": sent, "by_ref": rows(by_ref), "by_campaign": rows(by_campaign), "by_from": rows(by_from)[:20]}
