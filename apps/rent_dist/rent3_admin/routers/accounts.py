"""
apps/rent_dist/rent3_admin/routers/accounts.py — 임대인·임차인 계정 관리(임시비번·초대·권유·시험도구) · 관리인 2단계 인증(TOTP MFA).
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.errors import DomainError
from apps.rent_dist._rent3_core import (
    Building,
    Lessor,
    Tenant,
    connect as cn,
    ensure_pool_seeded,
    invite as inv,
    mfa,
    normalize_branch_id,
    passwords as pwd,
    quicklogin as ql,
    support as sup,
    testtenant as tt,
)
from apps.rent_dist._rent3_core.authz import claims_from, issue_claims_token
from apps.rent_dist._rent3_core.pool import now_iso
from ._deps import ADMIN_ONLY, APP_SRC, _admin_name, _require_lessor_proof

router = APIRouter()


class AccountResetReq(BaseModel):
    kind: str        # lessor | tenant
    id: str


class InviteIssueReq(BaseModel):
    id: str


class TestTenantReq(BaseModel):
    branch: str = "main"
    room_no: str = "701"
    name: str = "민흥식"
    phone: str = "010-3269-3343"
    apply: bool = False
    raise_limit: bool = False


class TestIdentityReq(BaseModel):
    name: str = ""
    phone: str = ""
    apply: bool = False
    new_password: str = ""             # 선택: 적으면 이 예비임차인의 비밀번호도 새로 정한다(8자 이상·영문+숫자)


class TestVacateReq(BaseModel):
    branch: str = "main"
    room_no: str = "701"
    apply: bool = False
    deposit: int = 5_000_000
    monthly_rent: int = 300_000


class MfaCodeReq(BaseModel):
    code: str = ""


class MfaResetReq(BaseModel):
    username: str


@router.post("/accounts/reset-password", dependencies=ADMIN_ONLY)
def reset_account_password(req: AccountResetReq):
    """본인인증 이력이 없어 직접 재설정할 수 없는 계정에 임시 비밀번호를 발급한다. 임시 비밀번호는 이 응답에서 한 번만 보인다(해시로 저장)."""
    obj = Lessor.get(req.id) if req.kind == "lessor" else Tenant.get(req.id) if req.kind == "tenant" else None
    if obj is None:
        raise DomainError("계정을 찾을 수 없습니다. kind 는 lessor 또는 tenant 여야 합니다.")
    temp = pwd.temp_password()
    obj.password = pwd.hash_password(temp)
    obj.save(action="password_reset_by_admin", app_source=APP_SRC)
    return {
        "success": True,
        "message": f"'{obj.name}' 님의 임시 비밀번호를 발급했습니다. 본인에게 안전하게 전달하고 바로 바꾸게 하세요.",
        "temporary_password": temp,
    }


@router.get("/accounts/tenants", dependencies=ADMIN_ONLY)
def list_resident_tenants():
    """입주 안내 링크·임시 비밀번호 발급 화면용: 입주 중인 임차인 목록."""
    ensure_pool_seeded()
    items = [
        {
            "id": t.id,
            "name": t.name,
            "room_no": t.room_no,
            "branch": t.branch,
            "invite": inv.status(t)["pending"],
            "has_pin": bool(t.pin_hash),
            "devices": len(ql.devices_of(t)),
        }
        for t in Tenant.all()
        if ql.eligible(t)
    ]
    return {"success": True, "tenants": sorted(items, key=lambda x: (x["branch"], x["room_no"]))}


@router.get("/accounts/lessors", dependencies=ADMIN_ONLY)
def list_lessor_accounts():
    """임대인 암호 설정 상태·6개월(180일) 변경 권유 대상 조회(해시·원문은 제외)."""
    ensure_pool_seeded()
    items = []
    for lsr in sorted(Lessor.all(), key=lambda x: x.branch):
        b = Building.first(branch=normalize_branch_id(lsr.branch))
        age = inv.password_age_days(lsr)
        items.append({
            "id": lsr.id,
            "name": lsr.name,
            "username": lsr.username,
            "phone": lsr.phone,
            "branch": lsr.branch,
            "building_name": b.building_name if b else "",
            "status": lsr.status,
            "activated": cn.activated(lsr),
            "password_changed_at": lsr.password_changed_at or lsr.activated_at,
            "days_since_change": age,
            "due_6m": lsr.status == "ACTIVE" and age is not None and age >= inv.LESSOR_REMIND_DAYS,
            "invite_pending": inv.status(lsr)["pending"],
            "password_remind_sent_at": lsr.password_remind_sent_at,
        })
    return {"success": True, "lessors": items}


@router.post("/accounts/lessor-invite", dependencies=ADMIN_ONLY)
def issue_lessor_invite(req: InviteIssueReq, request: Request):
    """관리인이 전화로 본인 확인 후 임대인에게 1회용 비밀번호 설정 링크(7일 유효)를 발급한다. 새 비밀번호를 정하기 전까지 기존 비밀번호는 유지된다."""
    lsr = Lessor.get(req.id)
    if lsr is None:
        raise DomainError("임대인을 찾을 수 없습니다.")
    if lsr.status != "ACTIVE":
        raise DomainError("가맹 승인된 임대인에게만 발급할 수 있습니다.")
    _require_lessor_proof(lsr)
    tok = inv.issue(lsr, _admin_name(request))
    b = Building.first(branch=normalize_branch_id(lsr.branch))
    bname = b.building_name if b else lsr.branch
    url = f"/rent3_lessor/?invite={tok}"
    sms_text = (
        f"[{bname}] {lsr.name} 임대인님, 관리인이 비밀번호 설정 링크를 보내 드립니다.\n"
        f"아래 링크를 열어 본인 확인(건물명·이름·전화번호·사업자/계좌 끝 4자리) 후 새 비밀번호를 정해 주세요(7일 안에 1회 사용).\n"
        f"{url}"
    )
    return {
        "success": True,
        "url": url,
        "expires": lsr.invite_expires,
        "lessor_name": lsr.name,
        "building_name": bname,
        "sms_text": sms_text,
        "message": f"{lsr.name} 임대인용 비밀번호 설정 링크를 만들었습니다(7일 안에 1회 사용).",
    }


@router.post("/accounts/lessors/{lessor_id}/remind-password", dependencies=ADMIN_ONLY)
def remind_lessor_password(lessor_id: str, request: Request):
    """관리인 → 임대인 6개월(180일) 주기 새 비밀번호 설정 권유 채팅·링크 발송. 강제 만료·차단은 하지 않으며 기존 비밀번호와 등록 기기는 유지된다."""
    lsr = Lessor.get(lessor_id)
    if lsr is None:
        raise DomainError("임대인을 찾을 수 없습니다.")
    if lsr.status != "ACTIVE":
        raise DomainError("가맹 승인된 임대인에게만 발송할 수 있습니다.")
    _require_lessor_proof(lsr)
    tok = inv.issue(lsr, _admin_name(request))
    lsr.password_remind_sent_at = now_iso()
    lsr.save(action="lessor_password_remind_sent", app_source=APP_SRC)
    b = Building.first(branch=normalize_branch_id(lsr.branch))
    bname = b.building_name if b else lsr.branch
    age = inv.password_age_days(lsr)
    url = f"/rent3_lessor/?invite={tok}"
    body = (
        f"[🔒 6개월 정기 비밀번호 변경 권유] {bname} {lsr.name} 임대인님, 안전한 임대 장부 보호를 위해 "
        f"6개월마다 새로운 비밀번호로 변경하시기를 권유드립니다. 아래 1회용 링크(7일 유효)에서 본인 확인 후 새 비밀번호를 설정해 주세요. "
        f"(새 비밀번호를 설정하기 전까지는 기존 비밀번호를 그대로 쓰실 수 있습니다.)\n{url}"
    )
    sup.post_admin_message(lsr, _admin_name(request), body)
    return {
        "success": True,
        "url": url,
        "expires": lsr.invite_expires,
        "lessor_name": lsr.name,
        "building_name": bname,
        "days_since_change": age,
        "sms_text": body,
        "message": f"{lsr.name} 임대인에게 6개월 새 비밀번호 설정 권유 채팅와 1회용 링크를 보냈습니다.",
    }


@router.post("/accounts/invite", dependencies=ADMIN_ONLY)
def issue_invite(req: InviteIssueReq, request: Request):
    """입주 임차인용 1회용 입주 안내 링크 발급(임대인이 못 하는 경우의 대행). 링크 원문은 이 응답에서만 보인다."""
    t = Tenant.get(req.id)
    if t is None:
        raise DomainError("임차인을 찾을 수 없습니다.")
    if not ql.eligible(t):
        raise DomainError("입주 중인 호실이 있는 임차인에게만 발급할 수 있습니다.")
    tok = inv.issue(t, _admin_name(request))
    return {
        "success": True,
        "url": f"/rent3_tenant/?invite={tok}",
        "expires": t.invite_expires,
        "tenant_name": t.name,
        "room_no": t.room_no,
        "message": "입주 안내 링크를 만들었습니다(7일 안에 1회 사용).",
    }


@router.post("/accounts/revoke-shortcuts", dependencies=ADMIN_ONLY)
def revoke_shortcuts(req: InviteIssueReq):
    """분실 폰 대응(전화 요청): 임차인의 간편 비밀번호·바로가기를 모두 해제한다. 접근을 줄이는 조치라 입장 절차 없이 가능하다."""
    t = Tenant.get(req.id)
    if t is None:
        raise DomainError("임차인을 찾을 수 없습니다.")
    n = ql.revoke_all(t)
    return {"success": True, "message": f"{t.name}님의 바로가기 {n}대와 간편 비밀번호를 해제했습니다."}


@router.post("/accounts/reset-activation", dependencies=ADMIN_ONLY)
def reset_activation(req: InviteIssueReq):
    """임차인 가입 상태 초기화(재시험·잘못 연결된 계정 풀기): 비밀번호·간편 비밀번호·바로가기·입주 안내 링크·동의 기록·연결 요청을 지운다. 호실·계약·납부는 그대로."""
    t = Tenant.get(req.id)
    if t is None:
        raise DomainError("임차인을 찾을 수 없습니다.")
    out = cn.reset_activation(t)
    return {
        "success": True,
        "message": f"{t.room_no}호 {t.name}님의 가입 상태를 처음으로 되돌렸습니다(연결 요청 {out['requests_closed']}건 종료). 호실·계약·납부 내역은 그대로입니다.",
        "hint": "이미 열려 있는 로그인 화면은 로그아웃해야 처음부터 시험할 수 있습니다.",
    }


@router.post("/accounts/test-tenant", dependencies=ADMIN_ONLY)
def test_tenant(req: TestTenantReq):
    """시험·실사용 확인용 임차인 호실 등록(기본 시크빌 701호 민흥식, 0원 계약). apply=False 는 점검만, True 는 등록(등록 전 자동 백업, 중복 없음)."""
    ensure_pool_seeded()
    out = tt.register(req.branch.strip() or "main", req.room_no, req.name, req.phone, apply=req.apply, raise_limit=req.raise_limit)
    return {"success": out["status"] != "BLOCKED", "level": "warning" if out["status"] == "BLOCKED" else "success", **out}


@router.post("/accounts/test-identity", dependencies=ADMIN_ONLY)
def test_identity(req: TestIdentityReq):
    """시험용 예비임차인(가짜 번호라 문자 인증을 받을 수 없는 계정)을 본인인증 완료로 표시한다. apply=False 는 점검만, True 는 처리(처리 전 자동 백업)."""
    ensure_pool_seeded()
    out = tt.mark_prospect_verified(req.name, req.phone, apply=req.apply, new_password=req.new_password)
    return {"success": out["status"] != "BLOCKED", "level": "warning" if out["status"] == "BLOCKED" else "success", **out}


@router.post("/accounts/test-vacate", dependencies=ADMIN_ONLY)
def test_vacate(req: TestVacateReq):
    """시험 임차인(0원 계약·시험용 인증)이 든 호실을 퇴실 확정한 것처럼 비워 공실 매물로 다시 공개한다. apply=False 는 점검만, True 는 처리(처리 전 자동 백업)."""
    ensure_pool_seeded()
    out = tt.vacate_test_room(req.branch, req.room_no, apply=req.apply, deposit=req.deposit, monthly_rent=req.monthly_rent)
    return {"success": out["status"] != "BLOCKED", "level": "warning" if out["status"] == "BLOCKED" else "success", **out}


def _me_admin(request: Request):
    c = claims_from(request) or {}
    a = mfa.admin_by_username(c.get("u", ""))
    if a is None:
        raise DomainError("이 계정은 관리인 목록에 없어 2단계 인증을 설정할 수 없습니다.", hint="관리인 계정(Data Pool 관리인)으로 로그인해 주세요.")
    return a, c


@router.get("/mfa/status", dependencies=ADMIN_ONLY)
def mfa_status(request: Request):
    a, c = _me_admin(request)
    return {"success": True, "username": a.username, "this_session_verified": bool(c.get("mfa")), **mfa.status(a)}


@router.post("/mfa/enroll/start", dependencies=ADMIN_ONLY)
def mfa_enroll_start(request: Request):
    """2단계 인증 설정 시작: 비밀 키와 인증 앱 등록용 주소를 한 번 보여 준다(확인 코드를 맞춰야 켜진다)."""
    a, _c = _me_admin(request)
    return {"success": True, **mfa.enroll_start(a)}


@router.post("/mfa/enroll/confirm", dependencies=ADMIN_ONLY)
def mfa_enroll_confirm(req: MfaCodeReq, request: Request):
    """인증 앱의 6자리 코드를 맞추면 켜진다. 복구 코드 8개를 한 번만 보여 주고, 지금 세션이 끊기지 않게 2단계를 마친 새 토큰을 준다."""
    a, c = _me_admin(request)
    res = mfa.enroll_confirm(a, req.code)
    return {
        "success": True,
        "message": "2단계 인증이 켜졌습니다. 복구 코드를 안전한 곳에 보관하세요.",
        **res,
        "token": issue_claims_token(c.get("u", ""), c.get("r", "admin"), c.get("b"), extra={"mfa": 1}),
    }


@router.post("/mfa/disable", dependencies=ADMIN_ONLY)
def mfa_disable(req: MfaCodeReq, request: Request):
    a, _c = _me_admin(request)
    mfa.disable(a, req.code)
    return {"success": True, "message": "2단계 인증을 껐습니다."}


@router.post("/mfa/reset", dependencies=ADMIN_ONLY)
def mfa_reset_other(req: MfaResetReq, request: Request):
    """폰을 잃고 복구 코드도 없는 다른 관리인의 2단계 인증을 최고관리자가 해제한다(본인 것은 불가 — 본인은 코드로 끈다)."""
    me, c = _me_admin(request)
    if c.get("r") != "superadmin":
        raise DomainError("최고관리자(superadmin)만 다른 관리인의 2단계 인증을 해제할 수 있습니다.")
    target = mfa.admin_by_username(req.username)
    if target is None:
        raise DomainError("그 관리인을 찾을 수 없습니다.")
    if target.id == me.id:
        raise DomainError("본인의 2단계 인증은 코드를 입력해 직접 끄세요.")
    mfa.reset(target, "admin_mfa_reset_by_superadmin")
    return {"success": True, "message": f"{target.username} 관리인의 2단계 인증을 해제했습니다. 다시 로그인해 새로 설정하도록 안내하세요."}
