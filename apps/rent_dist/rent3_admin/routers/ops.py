"""
apps/rent_dist/rent3_admin/routers/ops.py — 백업 · 홍보/유입/알림 · 이관/정리 도구 · Data Pool 검색/수정/열람.
"""
import os
from typing import Any, Dict, List
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    POOL_COLLECTIONS,
    acquisition,
    backup as bk,
    carryover_split as cs_split,
    dupcheck,
    ensure_pool_seeded,
    landlordpromo as lp,
    legacyregistry as lg,
    notifications as nf,
    pool,
    poolsearch as ps,
    roomdefaults as rd,
)
from ._deps import ADMIN_ONLY, _admin_label

router = APIRouter()


class CarrySplitReq(BaseModel):
    confirm: bool = False


class DupRemoveReq(BaseModel):
    apply: bool = False


class PoolQueryReq(BaseModel):
    collection: str
    columns: List[str] = []
    value: str = ""
    limit: int = 100


class PoolUpdateReq(BaseModel):
    collection: str
    id: str
    changes: Dict[str, Any] = {}
    reason: str = ""


class PoolTextReq(BaseModel):
    collection: str
    value: str = ""
    limit: int = 30


class PoolJsonUpdateReq(BaseModel):
    collection: str
    id: str
    record: Dict[str, Any]
    reason: str = ""


# ── 백업 ──
@router.get("/backups", dependencies=ADMIN_ONLY)
def list_backups():
    return {"success": True, "backups": bk.list_backups(), "keep": bk.keep_count(), "daily": os.getenv("RENT3_BACKUP_DAILY", "1") != "0"}


@router.post("/backups/run", dependencies=ADMIN_ONLY)
def run_backup():
    info = bk.create()
    v = bk.verify(bk.path_for(info["name"]))
    return {"success": True, "message": f"백업을 만들었습니다({info['name']}). 검사: {'이상 없음' if v['ok'] else '문제 있음'}", "backup": info, "verify": v}


@router.get("/backups/{name}/download", dependencies=ADMIN_ONLY)
def download_backup(name: str):
    """백업 내려받기 — 개인정보가 들어 있으므로 안전하게 보관할 것."""
    try:
        p = bk.path_for(name)
    except (ValueError, FileNotFoundError):
        raise DomainError("백업 파일을 찾을 수 없습니다.")
    return FileResponse(p, media_type="application/gzip", filename=name)


# ── 홍보 · 알림 · 유입 ──
@router.get("/landlord-promo", dependencies=ADMIN_ONLY)
def landlord_promo(request: Request, channel: str = "cafe", season: str = "none", campaign: str = ""):
    """임대인 대상 홍보 글 초안(카페·밴드·블로그). '[광고]' 표시·실제 요금·채널 규정 확인 목록이 들어 있다. 운영자가 직접 올린다."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return {"success": True, "seasons": lp.SEASONS, "post": lp.generate(channel, season, campaign, f"{proto}://{host}" if host else "")}


@router.get("/notifications", dependencies=ADMIN_ONLY)
def admin_notifications():
    """관리인 알림 일괄 요약: 문의 안 읽음·가맹 심사 대기·홈택스 확인 대기·가맹비 연체·세금계산서 발행 필요(건수·이동 주소만)."""
    ensure_pool_seeded()
    return {"success": True, **nf.summary("admin", None, "admin")}


@router.get("/acquisition/summary", dependencies=ADMIN_ONLY)
def acquisition_summary():
    """임대인 유입 현황(읽기 전용): 채널·캠페인별 가입 → 가맹 승인 → 첫 방 등록 → 첫 수납 기록 수."""
    ensure_pool_seeded()
    return {"success": True, **acquisition.summary()}


# ── 데이터 정비 도구 (미리보기 → 백업 후 반영) ──
@router.get("/legacy-registry/preview", dependencies=ADMIN_ONLY)
def legacy_registry_preview():
    """전자계약이 아닌 기존 입주자 중 '입주 전 등기부 확인'을 완료로 기록할 대상(읽기 전용)."""
    ensure_pool_seeded()
    p = lg.plan()
    return {"success": True, "contracts_total": p["contracts_total"], "to_mark": p["to_mark"], "rows": p["rows"][:30]}


@router.post("/legacy-registry/apply", dependencies=ADMIN_ONLY)
def legacy_registry_apply(req: CarrySplitReq):
    """풀 전체를 자동 백업한 뒤, 기존 입주자 계약에 '등기부 확인 완료(이전 입주자)'를 기록한다(출입 잠금·수납은 그대로)."""
    ensure_pool_seeded()
    if not req.confirm:
        raise DomainError("확인(confirm)이 필요합니다.", hint="미리보기를 확인한 뒤 확인 창에서 진행해 주세요.")
    res = lg.apply()
    return {
        "success": True,
        "message": (f"기존 입주자 {res['marked']}건을 등기부 확인 완료로 기록했습니다(백업 {res['backup']})." if res["marked"] else "기록할 대상이 없습니다."),
        **res,
    }


@router.get("/room-defaults/preview", dependencies=ADMIN_ONLY)
def room_defaults_preview():
    """방에 자동으로 들어갔던 샘플 등기부·가전 옵션·상태 설명을 정리할 계획(읽기 전용)."""
    ensure_pool_seeded()
    p = rd.plan()
    return {"success": True, **{k: p[k] for k in ("rooms_total", "rooms_to_clean", "counts", "public_now")}, "rows": p["rows"][:30]}


@router.post("/room-defaults/apply", dependencies=ADMIN_ONLY)
def room_defaults_apply(req: CarrySplitReq):
    """풀 전체를 자동 백업한 뒤, 샘플과 정확히 같은 값만 비운다(직접 고친 값·실제 등기부 값은 그대로)."""
    ensure_pool_seeded()
    if not req.confirm:
        raise DomainError("확인(confirm)이 필요합니다.", hint="미리보기를 확인한 뒤 확인 창에서 진행해 주세요.")
    res = rd.apply()
    return {
        "success": True,
        "message": (f"샘플 값을 비운 방 {res['cleaned']}개(항목 {res['fields']}개)를 정리했습니다(백업 {res['backup']})." if res["cleaned"] else "정리할 샘플 값이 없습니다."),
        **res,
    }


@router.get("/carryover-split/preview", dependencies=ADMIN_ONLY)
def carryover_split_preview():
    """이관 '이전 입금 합산' 건을 달별 건으로 나눌 계획(읽기 전용): 어느 호실이 몇 개월로 풀리는지, 검토가 필요한 건은 무엇인지."""
    ensure_pool_seeded()
    rows = cs_split.plan()
    ok = [r for r in rows if r["status"] == "OK"]
    return {
        "success": True,
        "total": len(rows),
        "ok": len(ok),
        "review": [r for r in rows if r["status"] != "OK"],
        "new_records": sum(len(r["months"]) for r in ok),
        "sum_ok_amount": sum(r["total"] for r in ok),
        "sum_planned_amount": sum(m["amount"] for r in ok for m in r["months"]),
        "rows": [
            {
                "branch": r["branch"],
                "room_no": r["room_no"],
                "tenant": r["tenant"],
                "total": r["total"],
                "months": len(r["months"]),
                "from": (r["months"][0]["ym"] if r["months"] else ""),
                "to": (r["months"][-1]["ym"] if r["months"] else ""),
                "status": r["status"],
                "note": r["note"],
            }
            for r in rows
        ],
    }


@router.post("/carryover-split/apply", dependencies=ADMIN_ONLY)
def carryover_split_apply(req: CarrySplitReq):
    """풀 전체를 자동 백업한 뒤 분해한다. 건마다 그 계약의 입금·미납·선수금 합계와 미납 달 분포를 전후로 비교해 다르면 그 건만 되돌린다. 원본은 무효로 보관."""
    ensure_pool_seeded()
    if not req.confirm:
        raise DomainError("확인(confirm)이 필요합니다.", hint="미리보기를 확인한 뒤 확인 창에서 진행해 주세요.")
    rows = cs_split.plan()
    if not any(r["status"] == "OK" for r in rows):
        return {"success": True, "message": "분해할 이월 합산 건이 없습니다.", "split": 0, "created": 0, "rolled_back": [], "backup": ""}
    snap0 = cs_split.snapshot()
    info = bk.create()
    res = cs_split.apply(rows)
    cmp = cs_split.compare(snap0, cs_split.snapshot())
    ok = cmp["identical_totals"] and not res["rolled_back"]
    return {
        "success": True,
        "backup": info.get("name", ""),
        **res,
        "identical_totals": cmp["identical_totals"],
        "mismatches": cmp["mismatches"],
        "month_distribution_changed": len(cmp["month_distribution_changed"]),
        "message": (
            f"분해 {res['split']}건 · 새 납부 건 {res['created']}개를 만들었습니다. 계약별 입금·미납·선수금 합계가 모두 그대로입니다(백업 {info.get('name', '')})."
            if ok
            else f"분해 {res['split']}건 완료, 되돌린 건 {len(res['rolled_back'])}건, 합계 불일치 {len(cmp['mismatches'])}건 — 확인이 필요합니다(백업 {info.get('name', '')})."
        ),
    }


@router.get("/pool-duplicates", dependencies=ADMIN_ONLY)
def pool_duplicates():
    """Data Pool 중복 점검(읽기 전용): 내용이 같은 납부(자동 정리 가능)·같은 달 여러 건·같은 호실의 계약/임차인 중복."""
    ensure_pool_seeded()
    return {"success": True, **dupcheck.scan()}


@router.post("/pool-duplicates/remove", dependencies=ADMIN_ONLY)
def remove_pool_duplicates(req: DupRemoveReq):
    """내용이 완전히 같은 중복 납부만 정리한다(가장 먼저 만든 한 건을 남김). apply=false 면 점검만, true 면 풀 전체를 먼저 백업한 뒤 지운다."""
    ensure_pool_seeded()
    res = dupcheck.remove_exact(apply=req.apply)
    msg = (
        f"중복 납부 {res['removed']}건을 지웠습니다(백업 {res['backup']})."
        if res["applied"]
        else f"지울 수 있는 중복 납부 {res['would_remove']}건을 찾았습니다(아직 지우지 않았습니다)."
    )
    return {"success": True, "message": msg, **res}


# ── Data Pool 검색·수정·열람 ──
@router.get("/pool-search/schema", dependencies=ADMIN_ONLY)
def pool_search_schema(collection: str):
    """검색·수정 화면용: 컬렉션의 컬럼 목록(타입·수정 가능 여부·🔒 암호화 여부). 보호 필드(비밀번호 해시 등)는 목록에 없다."""
    ensure_pool_seeded()
    return {"success": True, **ps.schema(collection)}


@router.post("/pool-search/query", dependencies=ADMIN_ONLY)
def pool_search_query(req: PoolQueryReq):
    ensure_pool_seeded()
    return {"success": True, **ps.query(req.collection, req.columns, req.value, max(1, min(req.limit, 300)))}


@router.post("/pool-search/text", dependencies=ADMIN_ONLY)
def pool_search_text(req: PoolTextReq):
    """카드 선택 후 간단 검색: 글자가 들어 있는 한 건씩의 JSON(보호 항목은 가림)을 돌려준다."""
    ensure_pool_seeded()
    return {"success": True, **ps.text_search(req.collection, req.value, req.limit)}


@router.post("/pool-search/update-json", dependencies=ADMIN_ONLY)
def pool_search_update_json(req: PoolJsonUpdateReq, request: Request):
    """카드의 JSON 을 고쳐 저장(사유 필수). 바뀐 항목만 안전한 수정 경로로 반영하고, 보호·잠금·읽기 전용 항목을 바꾸면 거절한다."""
    ensure_pool_seeded()
    res = ps.update_from_json(req.collection, req.id, req.record, req.reason, _admin_label(request))
    return {"success": True, "message": f"{', '.join(res['changed'])} 항목을 고쳤습니다(사유 기록됨).", **res}


@router.get("/pool-search/item", dependencies=ADMIN_ONLY)
def pool_search_item(collection: str, id: str):
    ensure_pool_seeded()
    return {"success": True, **ps.get_item(collection, id)}


@router.post("/pool-search/update", dependencies=ADMIN_ONLY)
def pool_search_update(req: PoolUpdateReq, request: Request):
    """한 건의 문자·숫자·참거짓 필드를 고친다(사유 필수, 전/후 값 기록). 보호·암호화·목록 필드는 고칠 수 없다."""
    ensure_pool_seeded()
    res = ps.update(req.collection, req.id, req.changes, req.reason, _admin_label(request))
    return {"success": True, "message": f"{', '.join(res['changed'])} 항목을 고쳤습니다(사유 기록됨).", **res}


@router.get("/pool-search/edits", dependencies=ADMIN_ONLY)
def pool_search_edits():
    ensure_pool_seeded()
    return {"success": True, "edits": ps.recent_edits()}


@router.get("/pool/{collection}", dependencies=ADMIN_ONLY)
def inspect_pool_collection(collection: str, q: str = ""):
    ensure_pool_seeded()
    envelope = pool().raw_collection(collection)
    envelope["items"] = [ps.redact_item(it) for it in envelope["items"]]
    if q.strip():
        kw = q.strip().lower()
        envelope["items"] = [it for it in envelope["items"] if kw in str(it).lower()]
        envelope["filtered_count"] = len(envelope["items"])
    return {"success": True, "collections": list(POOL_COLLECTIONS), **envelope}
