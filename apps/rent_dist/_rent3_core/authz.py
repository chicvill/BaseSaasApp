"""
apps/rent_dist/_rent3_core/authz.py — rent3 앱 공통 접근 통제.

로그인(포털)이 서명 토큰을 내주고, 각 API 는 토큰의 (역할, 지점/계정)과 요청 대상이 맞는지 서버에서 확인한다.
 - 본사 관리자(superadmin/admin): 모두 허용
 - 임대인(lessor/branch_admin): 자기 지점(branch)의 자료만
 - 임차인(tenant): 자기 계정과 자기가 당사자인 계약만
토큰은 `Authorization: Bearer ...` 헤더, 또는 링크로 열어야 하는 파일(이미지·CSV)용 `?token=` 쿼리로 받는다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from fastapi import Request, Response

from core.auth import TOKEN_TTL_SECONDS, _b64, _secret, verify_token
from core.errors import Forbidden, NotFound
from core.auth import Unauthorized

ADMIN_ROLES = ("superadmin", "admin")
LESSOR_ROLES = ("lessor", "branch_admin")


# ── 토큰 발급 (포털 로그인/가입에서 호출) ──
# 플랫폼 코어(core/)는 수정하지 않는다. 코어와 같은 서명 방식(HMAC-SHA256)으로 임대인·임차인 토큰을 만든다.
def issue_claims_token(username: str, role: str, branch: Optional[str], ttl: int = TOKEN_TTL_SECONDS) -> str:
    payload = _b64(json.dumps({"u": username, "r": role, "b": branch, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def lessor_token(lessor: Any) -> str:
    return issue_claims_token(f"lessor:{lessor.id}", "lessor", lessor.branch)


def tenant_token(tenant: Any) -> str:
    return issue_claims_token(f"tenant:{tenant.id}", "tenant", getattr(tenant, "branch", None))


# ── 토큰 읽기 ──
# ── 토큰 폐기(로그아웃) ──
# 로그아웃한 토큰은 만료 전에도 쓸 수 없게 서명 부분의 해시를 풀 폴더의 파일에 보관한다(만료된 항목은 정리).
_rev_lock = threading.Lock()


def _rev_path() -> str:
    from .pool import pool
    return os.path.join(pool().pool_dir, "_revoked_tokens.json")


def _tok_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _load_revoked() -> Dict[str, int]:
    try:
        with open(_rev_path(), encoding="utf-8") as fp:
            data = json.load(fp)
        now = time.time()
        return {k: v for k, v in data.items() if v > now}
    except (OSError, ValueError):
        return {}


def revoke_token(raw: str) -> None:
    data = verify_token(raw) if raw else None
    if not data:
        return
    with _rev_lock:
        cur = _load_revoked()
        cur[_tok_key(raw)] = int(data.get("exp", 0))
        tmp = _rev_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(cur, fp)
        os.replace(tmp, _rev_path())


def unrevoke_token(raw: str) -> None:
    """같은 초에 다시 로그인하면 같은 토큰이 발급될 수 있으므로, 새 로그인이 발급한 토큰은 폐기 목록에서 뺀다."""
    if not raw:
        return
    with _rev_lock:
        cur = _load_revoked()
        if cur.pop(_tok_key(raw), None) is not None:
            with open(_rev_path() + ".tmp", "w", encoding="utf-8") as fp:
                json.dump(cur, fp)
            os.replace(_rev_path() + ".tmp", _rev_path())


def is_revoked(raw: str) -> bool:
    return bool(raw) and _tok_key(raw) in _load_revoked()


def raw_token(request: Request) -> str:
    raw = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return raw or (request.query_params.get("token") or "").strip()


def not_revoked(request: Request) -> None:
    """관리자 API 등 코어 인증(require_role)에 덧붙여, 로그아웃한 토큰을 거부한다."""
    if is_revoked(raw_token(request)):
        raise Unauthorized("로그아웃된 로그인입니다. 다시 로그인해 주세요.")


def api_headers(response: Response) -> None:
    """API 응답에 보안 헤더: 개인정보가 캐시에 남지 않게 하고, 형식 추측·프레임 삽입을 막는다."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"


def claims_from(request: Request) -> Optional[Dict[str, Any]]:
    raw = raw_token(request)
    if not raw or is_revoked(raw):
        return None
    return verify_token(raw)


def need_claims(request: Request) -> Dict[str, Any]:
    c = claims_from(request)
    if c is None:
        raise Unauthorized("로그인이 필요합니다.", hint="포털에서 로그인한 뒤 다시 시도해 주세요.")
    return c


def is_admin(c: Dict[str, Any]) -> bool:
    return c.get("r") in ADMIN_ROLES


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _norm_branch(b: Optional[str]) -> str:
    from .models import normalize_branch_id  # 지연 import (순환 방지)
    return normalize_branch_id(b or "main")


async def _body(request: Request) -> Dict[str, Any]:
    if request.method not in ("POST", "PUT", "PATCH"):
        return {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 — 본문이 JSON 이 아니면 지점 힌트 없음
        return {}
    return data if isinstance(data, dict) else {}


def _branch_of_path(params: Dict[str, Any]) -> Optional[str]:
    from .models import Contract, Room
    from .ops import CheckOutSettlement, MaintenanceTicket
    for key, cls in (("room_id", Room), ("ticket_id", MaintenanceTicket),
                     ("settlement_id", CheckOutSettlement), ("contract_id", Contract)):
        if key in params:
            o = cls.get(params[key])
            return getattr(o, "branch", None) if o else None
    return None


# ── FastAPI 의존성 ──
async def lessor_scope(request: Request) -> Dict[str, Any]:
    """임대인 API: 본사 관리자 또는 '자기 지점' 임대인만. 지점은 쿼리 → 경로 대상 → 본문 → 기본(main) 순으로 정한다."""
    if request.url.path.rstrip("/").endswith("/status"):
        return {}  # 상태 확인은 공개 (민감 정보 없음)
    c = need_claims(request)
    if is_admin(c):
        return c
    if c.get("r") not in LESSOR_ROLES:
        raise Forbidden("임대인 계정으로 로그인해야 합니다.", hint="임차인 계정으로는 임대인 기능을 쓸 수 없습니다.")
    q = request.query_params.get("branch")
    branch = q or _branch_of_path(request.path_params)
    if branch is None:
        branch = (await _body(request)).get("branch")
    if branch is None and any(k in request.path_params for k in ("room_id", "ticket_id", "settlement_id", "contract_id")):
        return c  # 대상이 없는 경로 — 엔드포인트가 404 로 답한다
    if _norm_branch(branch) != _norm_branch(c.get("b")):
        raise Forbidden("다른 지점의 자료에는 접근할 수 없습니다.", hint="본인 건물(지점)의 자료만 열람·수정할 수 있습니다.")
    return c


def tenant_scope(request: Request) -> Dict[str, Any]:
    """임차인 마이홈 API: 본인(tenant_id 일치) 또는 본사 관리자만."""
    c = need_claims(request)
    if is_admin(c):
        return c
    tid = request.path_params.get("tenant_id")
    if c.get("r") == "tenant" and tid and c.get("u") == f"tenant:{tid}":
        return c
    raise Forbidden("본인 계정의 정보만 볼 수 있습니다.", hint="다른 사람의 마이홈에는 접근할 수 없습니다.")


def any_user(request: Request) -> Dict[str, Any]:
    """로그인한 누구나(임대인·임차인·관리자)."""
    return need_claims(request)


def staff_only(request: Request) -> Dict[str, Any]:
    """임대인 또는 본사 관리자 (임차인 제외)."""
    c = need_claims(request)
    if is_admin(c) or c.get("r") in LESSOR_ROLES:
        return c
    raise Forbidden("임대인 또는 관리자만 사용할 수 있습니다.")


def session_party_ok(c: Dict[str, Any], session: Any) -> bool:
    """이 토큰의 주인이 해당 계약 세션의 당사자인가."""
    if is_admin(c):
        return True
    role = c.get("r")
    if role in LESSOR_ROLES:
        return _norm_branch(session.branch) == _norm_branch(c.get("b"))
    if role == "tenant":
        from .models import Tenant
        tid = str(c.get("u", "")).removeprefix("tenant:")
        if session.id_tenant and session.id_tenant == tid:
            return True
        t = Tenant.get(tid)
        mine = _digits(t.phone) if t else ""
        theirs = _digits(str((session.terms or {}).get("tenant_phone") or ""))
        return bool(mine and theirs and mine == theirs)
    return False


# 공개·타인에게 내려보내면 안 되는 호실 필드 (도어락 비밀번호, 등기 정보, 입주자·계약 연결)
ROOM_PRIVATE_FIELDS = ("doorlock_pwd", "registry_info", "id_tenant", "active_contract_id")


def public_room(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k not in ROOM_PRIVATE_FIELDS}


def admin_only(request: Request) -> Dict[str, Any]:
    """본사 관리자 전용 (토큰의 역할만 본다)."""
    c = need_claims(request)
    if not is_admin(c):
        raise Forbidden("본사 관리자만 사용할 수 있습니다.", hint="관리자 계정으로 로그인해 주세요.")
    return c


def _req_branch(request: Request, body: Dict[str, Any]) -> Optional[str]:
    p = request.path_params
    return (p.get("branch") or p.get("branch_id") or request.query_params.get("branch")
            or request.query_params.get("branch_id") or _branch_of_path(p) or body.get("branch") or body.get("branch_id"))


async def staff_branch_scope(request: Request) -> Dict[str, Any]:
    """임대인 관리 기능(세무·경비 등): 관리자, 또는 '지점이 요청에 명시된' 자기 지점 임대인만."""
    c = need_claims(request)
    if is_admin(c):
        return c
    if c.get("r") not in LESSOR_ROLES:
        raise Forbidden("임대인 또는 관리자만 사용할 수 있습니다.")
    branch = _req_branch(request, await _body(request))
    if not branch:
        raise Forbidden("지점을 지정해야 합니다.", hint="전체 지점 자료는 본사 관리자만 볼 수 있습니다.")
    if _norm_branch(branch) != _norm_branch(c.get("b")):
        raise Forbidden("다른 지점의 자료에는 접근할 수 없습니다.")
    return c


# 임차인이 쓸 수 있는 레거시 운영 API (그 밖의 운영 API 는 임대인·관리자 전용)
import re as _re  # noqa: E402

_TENANT_OK = tuple(_re.compile(p) for p in (
    r"/tenant/", r"/chat/[^/]+/[^/]+$", r"/chat/[^/]+/[^/]+/(send|read)$",
    r"/maintenance/tickets$", r"/checkout/[^/]+/[^/]+$", r"/checkout/[^/]+/[^/]+/(tenant-agree|receipt)$",
    r"/renewals$", r"/renewals/[^/]+/(sign|tacit-confirm)$",
))


async def legacy_ops_scope(request: Request) -> Dict[str, Any]:
    """레거시 운영 API: 로그인 필수. /admin/* 은 본사 관리자 전용, 임대인은 자기 지점만, 임차인은 허용된 경로에서 자기 호실만."""
    c = need_claims(request)
    if is_admin(c):
        return c
    path = request.url.path.rstrip("/")
    if "/admin/" in path + "/":
        raise Forbidden("본사 관리자만 사용할 수 있습니다.", hint="관리자 계정으로 로그인해 주세요.")
    body = await _body(request)
    branch = _req_branch(request, body)
    role = c.get("r")
    if role in LESSOR_ROLES:
        if not branch:
            # 지점을 알 수 없는 조회·작업은 막는다 (다른 지점 자료가 섞여 나갈 수 있음)
            raise Forbidden("지점을 지정해야 합니다.", hint="요청에 지점(branch)을 포함해 주세요.")
        if _norm_branch(branch) != _norm_branch(c.get("b")):
            raise Forbidden("다른 지점의 자료에는 접근할 수 없습니다.")
        return c
    if role == "tenant":
        from .models import Tenant
        t = Tenant.get(str(c.get("u", "")).removeprefix("tenant:"))
        if t is None or not any(rx.search(path) for rx in _TENANT_OK):
            raise Forbidden("이 기능은 임대인 또는 관리자만 사용할 수 있습니다.")
        room = request.path_params.get("room_id") or body.get("room_id") or body.get("room_no") or request.query_params.get("room_id")
        # 임차인은 지점·호실이 요청에 명시되고 본인 것과 같을 때만 (신청서·영수증·재계약 id 만으로 조회하는 경로는 막는다)
        if not branch or not room:
            raise Forbidden("본인 호실을 지정한 요청만 사용할 수 있습니다.", hint="새 임차인 앱(마이홈)을 이용해 주세요.")
        if _norm_branch(branch) != _norm_branch(t.branch) or str(room) != str(t.room_no):
            raise Forbidden("본인 호실의 정보만 볼 수 있습니다.")
        return c
    raise Forbidden("권한이 없습니다.")


async def legacy_public_scope(request: Request):
    """레거시 공개 앱: 매물 목록 조회는 공개, 입주신청은 로그인, 라운지는 본인 호실·본인 지점만,
    그 밖(신청서 id 조회·구 계약방·서명결제)은 본사 관리자만 — id 만 알면 남의 신청서·계약 세션을 읽거나 바꿀 수 있기 때문이다."""
    path = request.url.path
    if request.method == "GET" and "/public/listings" in path:
        return None
    if "/auth/" in path:
        return None  # 각 핸들러가 직접 막는다(_require_fake_identity)
    c = need_claims(request)
    if is_admin(c):
        return c
    if path.endswith("/public/apply"):
        return c
    if "/public/lounge/" in path and request.method == "GET":
        branch, room = request.path_params.get("branch"), request.path_params.get("room_id")
        if c.get("r") in LESSOR_ROLES and branch and _norm_branch(branch) == _norm_branch(c.get("b")):
            return c
        if c.get("r") == "tenant":
            from .models import Tenant
            t = Tenant.get(str(c.get("u", "")).removeprefix("tenant:"))
            if t and branch and room and _norm_branch(branch) == _norm_branch(t.branch) and str(room) == str(t.room_no):
                return c
    raise Forbidden("이 기능은 더 이상 일반 계정에 열려 있지 않습니다.", hint="새 앱(임대인 장부·임차인 마이홈·계약 앱)을 이용해 주세요.")


PUBLIC_CONTRACT_PATHS = ("/verify", "/status")
IDENTITY_GATED_SUFFIXES = ("/sessions/call", "/respond", "/step4/sign")


async def contract_scope(request: Request) -> Optional[Dict[str, Any]]:
    """공동계약 API: 공개 진위 확인(/verify)·상태를 뺀 모든 요청에 로그인 필수.
    세션이 정해지는 요청은 당사자(임대인=해당 지점, 임차인=본인)만, 본문의 역할이 토큰 역할과 다르면 거부한다."""
    path = request.url.path
    if path.rstrip("/").endswith(PUBLIC_CONTRACT_PATHS):
        return None
    c = need_claims(request)
    if is_admin(c):
        return c
    body = await _body(request)

    # 토큰 역할과 다른 역할로 행동 금지 (임차인 토큰으로 임대인 서명 등)
    claimed = {"lessor"} if c.get("r") in LESSOR_ROLES else {"tenant"}
    for key in ("role", "caller_role", "responder_role"):
        v = str(body.get(key) or "").lower()
        if v in ("lessor", "tenant") and v not in claimed:
            raise Forbidden("로그인한 계정의 역할과 다른 역할로 진행할 수 없습니다.",
                            hint="임대인은 임대인, 임차인은 임차인 계정으로 각각 로그인해 주세요.")

    # 임차인은 휴대폰 본인인증을 마쳐야 계약을 시작·응답·서명할 수 있다 (명의 도용 방어)
    if c.get("r") == "tenant" and request.method == "POST" and path.rstrip("/").endswith(IDENTITY_GATED_SUFFIXES):
        from .models import Tenant
        t = Tenant.get(str(c.get("u", "")).removeprefix("tenant:"))
        if not (t and (t.identity or {}).get("verified")):
            raise Forbidden("휴대폰 본인인증이 필요합니다.",
                            hint="계약 화면 첫 페이지에서 본인인증을 마친 뒤 다시 시도해 주세요.")

    sid = request.path_params.get("session_id") or request.path_params.get("token")
    if sid:
        from .cosign import CoSignSession
        s = CoSignSession.first(session_code=sid) or CoSignSession.get(sid)
        if s is not None and not session_party_ok(c, s):
            raise Forbidden("이 계약의 당사자가 아닙니다.", hint="계약 당사자(해당 건물 임대인, 계약 임차인)만 접근할 수 있습니다.")
    elif path.endswith("/sessions/call"):
        if c.get("r") in LESSOR_ROLES and _norm_branch(body.get("branch")) != _norm_branch(c.get("b")):
            raise Forbidden("다른 지점의 호실로 계약을 시작할 수 없습니다.")
        if c.get("r") == "tenant":
            from .models import Tenant
            tid = str(c.get("u", "")).removeprefix("tenant:")
            t = Tenant.get(tid)
            if str(body.get("id_tenant") or "") and str(body["id_tenant"]) != tid:
                raise Forbidden("본인 계정으로만 계약을 시작할 수 있습니다.")
            ph = _digits(str(body.get("tenant_phone") or ""))
            if ph and t and _digits(t.phone) != ph:
                raise Forbidden("본인 휴대폰 번호로만 계약을 시작할 수 있습니다.")
    return c
