"""
apps/rent_dist/_rent3_core/connect.py — 기존 임차인의 '내 페이지 연결'(임대인 승인 방식).

문자 인증을 쓰지 않아도 되는 대안이다. 건물명·호실·성명·휴대폰 번호는 이웃도 알 수 있는 정보라 이것만으로는 본인 확인이 되지 않으므로,
  1) 임차인이 4가지를 입력하면 장부와 대조해 요청을 접수하고 화면에 4자리 확인번호를 보여 준다(요청한 브라우저만 아는 비밀값 secret 도 함께 받는다).
  2) 임대인이 장부의 휴대폰으로 임차인에게 직접 전화해 확인번호를 듣고, 첫 화면에서 그 번호를 입력해 승인한다(5번 틀리면 요청 폐기).
  3) 승인 뒤 요청한 브라우저(secret 보유)가 확인하러 오면 그때 1회용 입주 안내 링크(invite.py)를 만들어 준다 — 비밀번호·PIN·동의는 기존 입주 안내 화면이 처리한다.
이미 비밀번호나 간편 비밀번호가 있는 호실은 이 방법으로 덮어쓸 수 없다(문자 인증 '내 페이지 찾기' 또는 임대인의 입주 안내 카드를 쓴다).
링크 원문은 어디에도 저장하지 않는다(요청한 브라우저에 전달되는 순간 만들어진다).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.errors import DomainError

from . import franchise as fr, invite as inv, passwords as pwd, quicklogin as ql
from .models import Building, Lessor, Tenant, normalize_branch_id
from .ops import ConnectRequest

WAIT_HOURS = 24          # 임대인이 승인할 때까지 기다리는 시간
DELIVER_MINUTES = 30     # 승인 뒤 요청한 쪽이 링크를 받아 가야 하는 시간
MAX_CODE_FAILS = 5


def _h(s: str) -> str:
    return hashlib.sha256(str(s).encode()).hexdigest()


def _digits(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _room(s: Any) -> str:
    return _digits(s)


def _bname(s: Any) -> str:
    return re.sub(r"[\s·\-_()\[\]]", "", str(s or "")).lower()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def activated(t: Any) -> bool:
    """입주자 본인이 이미 가입(비밀번호를 직접 정함)한 계정인가(그러면 연결 요청으로 덮어쓰지 못한다).
    가져온(이관) 임차인에게는 아무도 모르는 무작위 비밀번호가 해시로 들어 있으므로 '해시 여부'로 판단하지 않는다 — 가입 시각(activated_at)이나 간편 비밀번호가 있어야 한다."""
    return bool(getattr(t, "activated_at", "") or t.pin_hash or t.quick_devices)


def public_info(branch: str) -> Optional[Dict[str, Any]]:
    """연결 화면 머리에 보일 건물명·임대인 연락처(임대인이 보낸 링크의 지점 코드로 찾는다). 없는 지점이면 None."""
    b = Building.first(branch=normalize_branch_id(branch))
    if b is None:
        return None
    lsr = Lessor.get(b.id_lessor) if b.id_lessor else Lessor.first(branch=b.branch)
    return {"branch": b.branch, "building_name": b.building_name, "lessor_name": (lsr.name if lsr else ""), "lessor_phone": (lsr.phone if lsr else "")}


def find_tenant(building_name: str, room_no: str, name: str, phone: str, branch: str = "") -> Optional[Any]:
    """호실·성명·휴대폰이 장부와 같고, 지점 코드(링크에 들어 있음) 또는 건물명이 같은 입주 임차인(아니면 None)."""
    room, ph, nm, bn = _room(room_no), _digits(phone), fr._norm_name(name), _bname(building_name)
    br = normalize_branch_id(branch) if branch else ""
    if not room or len(ph) < 10 or not nm or (not br and len(bn) < 2):
        return None
    for t in Tenant.all():
        if not ql.eligible(t) or _room(t.room_no) != room or _digits(t.phone) != ph or fr._norm_name(t.name) != nm:
            continue
        if br:
            if normalize_branch_id(t.branch) == br:
                return t
            continue
        b = Building.get(t.id_building) if t.id_building else Building.first(branch=t.branch)
        bname = _bname(b.building_name) if b else ""
        if bname and (bn in bname or bname in bn):
            return t
    return None


def create(t: Any) -> Tuple[ConnectRequest, str, str]:
    """요청 접수. 이 임차인의 이전 요청은 폐기하고, (요청, secret, 확인번호)를 돌려준다. secret·확인번호 원문은 이 응답에서만 볼 수 있다."""
    for old in ConnectRequest.find(tenant_id=t.id):
        if old.status in ("PENDING", "APPROVED"):
            old.status = "EXPIRED"
            old.save(action="connect_replaced", app_source="rent3_connect")
    secret = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(9000) + 1000}"
    req = ConnectRequest(branch=normalize_branch_id(t.branch), room_no=t.room_no, tenant_id=t.id, tenant_name=t.name, secret_hash=_h(secret),
                         expires_at=(datetime.now() + timedelta(hours=WAIT_HOURS)).isoformat(timespec="seconds"))
    req.code_hash = _h(f"{req.id}:{code}")
    req.save(action="connect_requested", app_source="rent3_connect")
    return req, secret, code


def _live(req: ConnectRequest) -> bool:
    return req.status == "PENDING" and req.expires_at >= _now()


def pending_for_branch(branch: str) -> List[Dict[str, Any]]:
    """임대인 첫 화면에 보일 승인 대기 요청(휴대폰은 장부의 번호 — 이 번호로 전화해 확인번호를 듣는다)."""
    out = []
    for r in sorted(ConnectRequest.find(branch=normalize_branch_id(branch)), key=lambda x: x.created_at):
        if not _live(r):
            continue
        t = Tenant.get(r.tenant_id)
        out.append({"id": r.id, "room_no": r.room_no, "tenant_name": r.tenant_name, "tenant_phone": (t.phone if t else ""), "created_at": r.created_at})
    return out


def recent_for_branch(branch: str, minutes: int = 90) -> List[Dict[str, Any]]:
    """최근 승인한 요청의 진행 상태 — 통화를 끊지 않은 채 임차인이 어디까지 왔는지 임대인이 본다."""
    cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    out = []
    for r in ConnectRequest.find(branch=normalize_branch_id(branch)):
        if r.status not in ("APPROVED", "DELIVERED") or (r.approved_at or "") < cutoff:
            continue
        t = Tenant.get(r.tenant_id)
        if r.status == "DELIVERED" and t is not None and activated(t):
            state, label = "DONE", "연결 완료 (비밀번호 설정을 마쳤습니다)"
        elif r.status == "DELIVERED":
            state, label = "SETTING", "임차인이 비밀번호를 정하는 중"
        else:
            state, label = "APPROVED", "승인함 — 임차인 화면이 넘어가는 중"
        out.append({"id": r.id, "room_no": r.room_no, "tenant_name": r.tenant_name, "state": state, "label": label, "approved_at": r.approved_at})
    return sorted(out, key=lambda x: x["approved_at"], reverse=True)


def _of(req_id: str, branch: str) -> ConnectRequest:
    r = ConnectRequest.get(req_id)
    if r is None or normalize_branch_id(r.branch) != normalize_branch_id(branch):
        raise DomainError("연결 요청을 찾을 수 없습니다.")
    if not _live(r):
        raise DomainError("이미 처리했거나 기한이 지난 요청입니다.")
    return r


def approve(req_id: str, branch: str, code: str, by: str) -> ConnectRequest:
    r = _of(req_id, branch)
    if not hmac.compare_digest(r.code_hash, _h(f"{r.id}:{str(code or '').strip()}")):
        r.code_fails += 1
        if r.code_fails >= MAX_CODE_FAILS:
            r.status = "REJECTED"
            r.handled_by = by
            r.save(action="connect_code_locked", app_source="rent3_connect")
            raise DomainError("확인번호를 5번 틀려 이 요청을 폐기했습니다.", hint="임차인이 연결 요청을 다시 보내게 해 주세요.")
        r.save(action="connect_code_failed", app_source="rent3_connect")
        raise DomainError(f"확인번호가 맞지 않습니다. (남은 시도 {MAX_CODE_FAILS - r.code_fails}회)", hint="임차인에게 전화해 화면에 보이는 4자리 번호를 정확히 들어 주세요.")
    r.status, r.approved_at, r.handled_by = "APPROVED", _now(), by
    r.save(action="connect_approved", app_source="rent3_connect")
    return r


def reject(req_id: str, branch: str, by: str) -> ConnectRequest:
    r = _of(req_id, branch)
    r.status, r.handled_by = "REJECTED", by
    r.save(action="connect_rejected", app_source="rent3_connect")
    return r


def check_status(req_id: str, secret: str) -> Dict[str, Any]:
    """요청한 브라우저(secret 보유)가 묻는다. 승인되었으면 그 자리에서 1회용 입주 안내 링크를 만들어 돌려준다(한 번만)."""
    r = ConnectRequest.get(str(req_id or ""))
    if r is None or not hmac.compare_digest(r.secret_hash, _h(secret or "")):
        raise DomainError("연결 요청을 찾을 수 없습니다.", hint="처음부터 다시 요청해 주세요.")
    now = _now()
    if r.status == "PENDING":
        if r.expires_at < now:
            return {"status": "EXPIRED", "message": "기한(24시간)이 지났습니다. 다시 요청해 주세요."}
        return {"status": "PENDING", "message": "임대인의 확인을 기다리고 있습니다."}
    if r.status == "REJECTED":
        return {"status": "REJECTED", "message": "임대인이 승인하지 않았습니다. 임대인에게 문의해 주세요."}
    if r.status == "APPROVED":
        t = Tenant.get(r.tenant_id)
        if t is None or not ql.eligible(t) or activated(t):
            r.status = "EXPIRED"
            r.save(action="connect_void", app_source="rent3_connect")
            return {"status": "EXPIRED", "message": "이미 가입이 끝났거나 입주 중인 호실이 아닙니다."}
        if (datetime.fromisoformat(r.approved_at) + timedelta(minutes=DELIVER_MINUTES)).isoformat(timespec="seconds") < now:
            r.status = "EXPIRED"
            r.save(action="connect_window_closed", app_source="rent3_connect")
            return {"status": "EXPIRED", "message": "승인 후 30분이 지났습니다. 다시 요청해 주세요."}
        tok = inv.issue(t, f"connect:{r.handled_by}")
        r.status = "DELIVERED"
        r.save(action="connect_delivered", app_source="rent3_connect")
        return {"status": "APPROVED", "message": "임대인이 승인했습니다. 비밀번호를 정해 주세요.", "invite_url": f"/rent3_tenant/?invite={tok}"}
    return {"status": "EXPIRED", "message": "이미 처리된 요청입니다."}


def reset_activation(t: Any) -> Dict[str, Any]:
    """관리인이 한 임차인을 '아직 가입하지 않은 처음 상태'로 되돌린다(연결·가입 과정을 다시 시험하거나, 잘못 연결된 계정을 풀 때).
    비밀번호(→ 쓸 수 없는 초기값), 간편 비밀번호·바로가기 기기, 입주 안내 링크, 개인정보 동의 기록, 진행 중인 연결 요청을 모두 지운다.
    호실·계약·납부 내역은 건드리지 않는다. 이미 열려 있는 로그인 화면은 만료(최대 12시간)될 때까지 남을 수 있다."""
    n_req = 0
    for r in ConnectRequest.find(tenant_id=t.id):
        if r.status in ("PENDING", "APPROVED", "DELIVERED"):
            r.status = "EXPIRED"
            r.save(action="connect_reset_by_admin", app_source="rent3_admin")
            n_req += 1
    t.password = "1212"
    t.pin_hash, t.quick_devices = "", []
    t.invite_hash, t.invite_expires, t.invite_issued_by = "", "", ""
    t.consents = {}
    t.activated_at = ""
    t.save(action="activation_reset", app_source="rent3_admin")
    return {"requests_closed": n_req}


def cancel_invite(t: Any) -> bool:
    """아직 쓰지 않은 개별 안내 링크를 취소한다(받은 링크는 바로 못 쓰게 된다). 취소할 링크가 없으면 False."""
    if not (t.invite_hash and t.invite_expires >= _now()):
        return False
    t.invite_hash, t.invite_expires, t.invite_issued_by = "", "", ""
    t.save(action="invite_cancelled", app_source="rent3_invite")
    return True
