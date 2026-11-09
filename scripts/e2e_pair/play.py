"""
scripts/e2e_pair/play.py — 임대인·임차인 두 역할이 번갈아 진행하는 공동 계약 시험 도구.

    py scripts/e2e_pair/play.py status                 # 지금 누구 차례이고 무엇을 해야 하는지
    py scripts/e2e_pair/play.py lessor <단계>          # 임대인 역할로 한 단계 실행 (내 차례일 때만)
    py scripts/e2e_pair/play.py tenant <단계>          # 임차인 역할로 한 단계 실행 (내 차례일 때만)
    py scripts/e2e_pair/play.py auto                   # 두 역할을 한 번에 끝까지 (사람·AI 없이 전체 흐름 검증)
    py scripts/e2e_pair/play.py attack                 # 보안 장치가 막는지 확인하는 공격 시험
    py scripts/e2e_pair/play.py reset                  # 진행 상태 초기화 (서버 데이터는 그대로)

공유 상태 파일: e2e_runtime/state.json (git 제외). 시험용 비밀번호만 들어 있다.
서버 주소: 환경변수 E2E_BASE, 없으면 e2e_runtime/server.json (scripts/e2e_pair/server.py 가 기록).
이 도구는 로컬 시험 서버(시연 로그인·가짜 본인인증 허용)용이다. 운영 서버에는 쓰지 말 것.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNTIME = os.environ.get("E2E_RUNTIME") or os.path.join(ROOT, "e2e_runtime")
STATE_PATH = os.environ.get("E2E_STATE") or os.path.join(RUNTIME, "state.json")

LESSOR_ID, LESSOR_PW = os.environ.get("E2E_LESSOR_ID", "chic"), os.environ.get("E2E_LESSOR_PW", "1212")
TENANT_PW = "Gemini#Test9"           # 시험용 임차인 비밀번호 (로컬 시험 서버 전용)
BRANCH = "main"
OWNER = "김종심"

# (역할, 단계) — 이 순서대로만 진행된다
STEPS: List[Tuple[str, str, str]] = [
    ("lessor", "prepare", "로그인하고 시험 호실을 만들어 공개한다"),
    ("tenant", "signup", "임차인 가입 + 시험용 본인인증(000000) + 로그인 확인"),
    ("lessor", "call", "시험 호실로 임차인에게 계약을 호출하고 입주일 등을 사전 작성한다"),
    ("tenant", "respond", "이메일을 사전 작성하고 호출에 응답한다"),
    ("lessor", "step1", "① 등기부 정보를 제시하고 임차인 신분증을 확인한다"),
    ("tenant", "step1", "① 등기부를 직접 열람한 값(시험값)을 제출하고 임대인 신분증을 확인한다"),
    ("lessor", "photo", "② 시설 점검 사진을 올린다"),
    ("tenant", "photo", "② 시설 점검 사진을 올린다"),
    ("lessor", "step2", "② 시설 점검 확인"),
    ("tenant", "step2", "② 시설 점검 확인(잠금) → ③단계로"),
    ("lessor", "agree", "③ 현재 조건에 합의한다"),
    ("tenant", "agree", "③ 현재 조건에 합의한다"),
    ("lessor", "sign", "④ 전자서명"),
    ("tenant", "sign", "④ 전자서명"),
    ("tenant", "recheck", "체결 직전 등기부 재열람: 변동 없음"),
    ("lessor", "complete", "⑤ 계약금 입금을 내 계좌에서 확인하고 체결한다"),
    ("tenant", "balance_check", "잔금일 등기부 재열람: 변동 없음"),
    ("lessor", "release_keys", "잔금 입금을 확인하고 도어락·공동현관을 교부한다"),
    ("tenant", "verify_keys", "도어락이 열렸는지, 계약 진위 확인이 일치하는지 확인한다"),
]


# ───────────────────────── 서버 호출 ─────────────────────────
def base_url() -> str:
    if os.environ.get("E2E_BASE"):
        return os.environ["E2E_BASE"].rstrip("/")
    p = os.path.join(RUNTIME, "server.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))["base"]
    return "http://127.0.0.1:9070"


def call(method: str, path: str, body: Optional[dict] = None, token: str = "") -> Tuple[int, Dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw.decode("utf-8", "replace")[:200]}
    except urllib.error.URLError as e:
        sys.exit(f"[오류] 시험 서버에 연결할 수 없습니다 ({base_url()}): {e.reason}\n  → 먼저 `py scripts/e2e_pair/server.py` 를 다른 터미널에서 실행하세요.")


class Fail(Exception):
    pass


def ok(status: int, res: Dict[str, Any], what: str) -> Dict[str, Any]:
    if status != 200 or res.get("success") is False:
        raise Fail(f"{what} 실패 (HTTP {status}): {res.get('message') or res.get('detail') or res}")
    return res


def login(ident: str, pw: str) -> str:
    st, res = call("POST", "/api/rent3/pool/auth/login", {"login_mode": "account", "identifier": ident, "password": pw})
    return ok(st, res, f"{ident} 로그인")["token"]


# ───────────────────────── 상태 파일 ─────────────────────────
def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return {"next": 0, "log": []}


def save_state(s: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(s, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# ───────────────────────── 한 단계씩 ─────────────────────────
class Ctx:
    def __init__(self, state: Dict[str, Any]):
        self.s = state

    def lessor_token(self) -> str:
        return login(LESSOR_ID, LESSOR_PW)

    def tenant_token(self) -> str:
        return login(self.s["tenant_phone"], TENANT_PW)

    @property
    def sid(self) -> str:
        return self.s["session_id"]

    def session(self, token: str) -> Dict[str, Any]:
        st, res = call("GET", f"/api/rent3_contract/sessions/{self.sid}", token=token)
        return ok(st, res, "계약 조회")["session"]


_phone_seq = [0]


def fresh_phone() -> str:
    """시험마다 겹치지 않는 휴대폰 번호 (010-9xxx-xxxx)."""
    _phone_seq[0] += 1
    n = time.time_ns() // 1000 + _phone_seq[0]
    return f"010-9{n % 1000:03d}-{(n // 1000) % 10000:04d}"


def fresh_birth() -> str:
    """같은 사람(이름+생년월일)은 계정 1개만 인증되므로, 시험 계정마다 다른 생년월일(YYMMDD)을 쓴다."""
    n = time.time_ns() // 1000 + _phone_seq[0] * 7919
    return f"{50 + n % 45:02d}{(n // 45) % 12 + 1:02d}{(n // 540) % 28 + 1:02d}"


def do_prepare(c: Ctx) -> str:
    tk = c.lessor_token()
    room_no = "9" + str(int(time.time()) % 1000).zfill(3)
    st, res = call("POST", "/api/rent3_lessor/rooms", {
        "branch": BRANCH, "room_no": room_no, "is_published": True, "deposit": 7000000, "monthly_rent": 450000,
        "maintenance_fee": 50000, "area_sqm": 21.5, "floor": 9, "status_description": "공동 계약 시험 호실"}, tk)
    room = ok(st, res, "시험 호실 등록")["room"]
    c.s.update(room_no=room_no, room_id=room["id"])
    return f"시험 호실 {room_no}호 공개 (보증금 700만 / 월세 45만)"


def do_signup(c: Ctx) -> str:
    phone = fresh_phone()
    st, res = call("POST", "/api/rent3/pool/auth/register", {
        "role": "prospect", "name": "제미니임차", "phone": phone, "password": TENANT_PW})
    reg = ok(st, res, "임차인 가입")
    c.s.update(tenant_phone=phone, tenant_id=reg["user"]["id"], tenant_name="제미니임차")
    who = {"name": "제미니임차", "phone": phone, "birth": fresh_birth()}
    st, res = call("POST", "/api/rent3_contract/identity/start", who, reg["token"])
    ok(st, res, "본인인증 시작")
    st, res = call("POST", "/api/rent3_contract/identity/confirm", {**who, "code": "000000"}, reg["token"])
    ok(st, res, "본인인증 확인")
    c.tenant_token()  # 비밀번호 로그인이 되는지 확인
    return f"임차인 {phone} 가입·본인인증(시험용)·로그인 확인"


def do_call(c: Ctx) -> str:
    tk = c.lessor_token()
    st, res = call("POST", "/api/rent3_contract/sessions/call", {
        "caller_role": "lessor", "branch": BRANCH, "room_no": c.s["room_no"], "mode": "NEW",
        "tenant_name": c.s["tenant_name"], "tenant_phone": c.s["tenant_phone"], "id_tenant": c.s["tenant_id"]}, tk)
    sess = ok(st, res, "계약 호출")["session"]
    c.s["session_id"] = sess["id"]
    move_in = (date.today() + timedelta(days=14)).isoformat()
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/prefill", {
        "role": "lessor", "updates": {"move_in_date": move_in, "contract_deposit": 500000, "special_terms": "반려동물 불가"}}, tk)
    ok(st, res, "사전 작성")
    c.s["move_in"] = move_in
    return f"계약 {sess['id']} 호출(참여코드 {sess.get('session_code')}), 입주일 {move_in} 사전 작성"


def do_respond(c: Ctx) -> str:
    tk = c.tenant_token()
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/prefill", {
        "role": "tenant", "updates": {"tenant_email": "gemini@example.test", "tenant_car_number": "12가3456"}}, tk)
    ok(st, res, "임차인 사전 작성")
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/respond", {
        "responder_role": "tenant", "responder_name": c.s["tenant_name"], "responder_phone": c.s["tenant_phone"],
        "id_tenant": c.s["tenant_id"]}, tk)
    ok(st, res, "호출 응답")
    return "호출 응답 완료 (양쪽 연결)"


def do_step1_lessor(c: Ctx) -> str:
    tk = c.lessor_token()
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/step1", {
        "role": "lessor", "checked_counterparty_id": True,
        "registry_updates": {"owner_name": OWNER, "mortgage_amount": 0}}, tk)
    ok(st, res, "① 임대인 확인")
    return f"등기부 제시(소유자 {OWNER}, 근저당 0원) + 임차인 신분증 확인"


def do_step1_tenant(c: Ctx) -> str:
    tk = c.tenant_token()
    rd = c.session(tk).get("registry_data") or {}
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/tenant-registry", {"registry": {
        "viewed_directly": True, "viewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "owner_name": rd.get("owner_name") or OWNER, "mortgage_amount": int(rd.get("mortgage_amount") or 0),
        "seizure_or_trust": "없음", "guarantee_status": "가입 예정"}}, tk)
    ok(st, res, "임차인 등기부 직접 열람 제출")
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/step1", {"role": "tenant", "checked_counterparty_id": True}, tk)
    ok(st, res, "① 임차인 확인")
    return "등기부 직접 열람(시험값=임대인 제시값) 제출 + 임대인 신분증 확인"


PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def do_photo(role: str):
    def run(c: Ctx) -> str:
        tk = c.lessor_token() if role == "lessor" else c.tenant_token()
        cap = "싱크대 상판" if role == "lessor" else "욕실 천장"
        st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/facility-photos", {"role": role, "photo_data_url": PNG, "caption": cap}, tk)
        ok(st, res, "점검 사진 업로드")
        return f"점검 사진 '{cap}' 기록 (해시 {res['photo']['sha256'][:10]}…)"
    return run


def do_step2(role: str):
    def run(c: Ctx) -> str:
        tk = c.lessor_token() if role == "lessor" else c.tenant_token()
        st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/step2", {"role": role}, tk)
        ok(st, res, "② 점검 확인")
        return "시설 점검 확인" + (" → ③단계" if res["session"]["current_step"] >= 3 else "")
    return run


def do_agree(role: str):
    def run(c: Ctx) -> str:
        tk = c.lessor_token() if role == "lessor" else c.tenant_token()
        rev = c.session(tk)["revision"]
        st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/step3/agree", {"role": role, "expected_revision": rev}, tk)
        ok(st, res, "③ 조건 합의")
        return f"조건(v{rev}) 합의"
    return run


def do_sign(role: str):
    def run(c: Ctx) -> str:
        tk = c.lessor_token() if role == "lessor" else c.tenant_token()
        rev = c.session(tk)["revision"]
        st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/step4/sign", {
            "role": role, "signature": f"{'김종심' if role == 'lessor' else '제미니임차'}(전자서명)", "expected_revision": rev}, tk)
        ok(st, res, "④ 전자서명")
        return "전자서명 완료" + (" (양측 서명 → 무결성 해시 생성)" if res["session"].get("integrity_hash") else "")
    return run


def do_recheck(c: Ctx) -> str:
    tk = c.tenant_token()
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/recheck", {"changed": False}, tk)
    ok(st, res, "체결 직전 재열람")
    return "체결 직전 등기부 재열람: 변동 없음"


def do_complete(c: Ctx) -> str:
    tk = c.lessor_token()
    st, res = call("POST", f"/api/rent3_contract/sessions/{c.sid}/step5/complete", {"payment_method": "TRANSFER", "own_account_checked": True}, tk)
    r = ok(st, res, "⑤ 체결")
    ct = r["contract"]
    c.s.update(contract_id=ct["id"], integrity_hash=ct.get("integrity_hash") or r["session"].get("integrity_hash", ""))
    return f"계약 체결 {ct['id']} (접수번호 {ct.get('fixed_date_no')} — 법적 확정일자 아님)"


def do_balance_check(c: Ctx) -> str:
    tk = c.tenant_token()
    st, res = call("POST", f"/api/rent3_tenant/myhome/{c.s['tenant_id']}/balance-registry-check", {"changed": False, "issue_number": "1101-2026-E2E"}, tk)
    ok(st, res, "잔금일 등기부 확인")
    return "잔금일 등기부 재열람: 변동 없음"


def do_release(c: Ctx) -> str:
    tk = c.lessor_token()
    st, res = call("POST", f"/api/rent3_lessor/rooms/{c.s['room_id']}/release-keys", {"balance_checked": True}, tk)
    ok(st, res, "도어락 교부")
    return "잔금 입금 확인 → 도어락·공동현관 교부"


def do_verify_keys(c: Ctx) -> str:
    tk = c.tenant_token()
    st, res = call("GET", f"/api/rent3_tenant/myhome/{c.s['tenant_id']}", token=tk)
    home = ok(st, res, "마이홈 조회")
    if home["keys"]["locked"] or "잔금" in str(home["room"].get("doorlock_pwd", "")):
        raise Fail("교부 후에도 도어락이 잠겨 있습니다")
    st, res = call("GET", f"/api/rent3_contract/verify?hash={c.s['integrity_hash']}&no={c.s['contract_id']}")
    verdict = res.get("result")
    if verdict != "INTACT":
        raise Fail(f"계약 진위 확인이 INTACT 가 아닙니다: {verdict}")
    return f"도어락 공개 확인, 계약 진위 확인 {verdict}"


IMPL = {
    ("lessor", "prepare"): do_prepare, ("tenant", "signup"): do_signup, ("lessor", "call"): do_call,
    ("tenant", "respond"): do_respond, ("lessor", "step1"): do_step1_lessor, ("tenant", "step1"): do_step1_tenant,
    ("lessor", "photo"): do_photo("lessor"), ("tenant", "photo"): do_photo("tenant"),
    ("lessor", "step2"): do_step2("lessor"), ("tenant", "step2"): do_step2("tenant"),
    ("lessor", "agree"): do_agree("lessor"), ("tenant", "agree"): do_agree("tenant"),
    ("lessor", "sign"): do_sign("lessor"), ("tenant", "sign"): do_sign("tenant"),
    ("tenant", "recheck"): do_recheck, ("lessor", "complete"): do_complete,
    ("tenant", "balance_check"): do_balance_check, ("lessor", "release_keys"): do_release,
    ("tenant", "verify_keys"): do_verify_keys,
}


# ───────────────────────── 명령 ─────────────────────────
def describe_next(s: Dict[str, Any]) -> str:
    i = s["next"]
    if i >= len(STEPS):
        return "✅ 모든 단계가 끝났습니다. (다시 하려면 `reset`)"
    role, name, desc = STEPS[i]
    return f"다음 차례: [{role}] `play.py {role} {name}` — {desc}"


def cmd_status() -> int:
    s = load_state()
    print(f"서버: {base_url()}")
    print(f"진행: {min(s['next'], len(STEPS))}/{len(STEPS)}단계 완료")
    if s.get("room_no"):
        print(f"시험 호실: {s['room_no']}호 · 계약: {s.get('session_id', '-')}")
    for e in s["log"][-4:]:
        print(f"  ✔ [{e['role']}] {e['step']}: {e['result']}")
    print(describe_next(s))
    return 0


def run_step(role: str, name: str, force_state: Optional[Dict[str, Any]] = None) -> int:
    s = force_state if force_state is not None else load_state()
    i = s["next"]
    if i >= len(STEPS):
        print("이미 모든 단계가 끝났습니다. 다시 하려면 `play.py reset`")
        return 0
    want_role, want_name, _ = STEPS[i]
    if (role, name) != (want_role, want_name):
        known = [n for r, n, _d in STEPS if r == role]
        if name not in known:
            print(f"[거부] '{role}' 역할에는 '{name}' 단계가 없습니다. 가능한 단계: {', '.join(known)}")
        else:
            print(f"[거부] 지금은 {role} 차례가 아닙니다 (또는 다른 단계 차례입니다).")
        print(describe_next(s))
        return 2
    try:
        result = IMPL[(role, name)](Ctx(s))
    except Fail as e:
        print(f"[실패] [{role}] {name}: {e}")
        print("상태는 그대로입니다. 원인을 고친 뒤 같은 명령을 다시 실행하세요.")
        return 1
    s["log"].append({"at": datetime.now().isoformat(timespec="seconds"), "role": role, "step": name, "result": result})
    s["next"] = i + 1
    if force_state is None:
        save_state(s)
    print(f"[성공] [{role}] {name}: {result}")
    print(describe_next(s))
    return 0


def cmd_auto() -> int:
    s = {"next": 0, "log": []}
    for role, name, _d in STEPS:
        rc = run_step(role, name, force_state=s)
        if rc:
            return rc
    save_state(s)
    print(f"\n✅ 자동 통과: 전체 {len(STEPS)}단계 성공 — 계약 {s.get('contract_id')} (상태 파일에 저장됨)")
    return 0


# ───────────────────────── 공격 시험 ─────────────────────────
def drive_to(index: int) -> Dict[str, Any]:
    s = {"next": 0, "log": []}
    for role, name, _d in STEPS[:index]:
        IMPL[(role, name)](Ctx(s))
        s["next"] += 1
        time.sleep(0.02)
    return s


def idx(role: str, name: str) -> int:
    return next(i for i, (r, n, _d) in enumerate(STEPS) if (r, n) == (role, name))


def cmd_attack() -> int:
    results: List[Tuple[bool, str]] = []

    def expect(label: str, status: int, allowed: Tuple[int, ...], note: str = "") -> None:
        good = status in allowed
        results.append((good, label))
        print(f"  {'✅ 막힘' if good else '❌ 뚫림'}  {label}  (HTTP {status}{', ' + note if note else ''})")

    print("공격 시험 — 모두 '막힘'이어야 합니다\n")
    other_lessor = login("sunho", LESSOR_PW)

    # ── 계약 호출 전후 ──
    s = drive_to(idx("lessor", "call"))                       # 임차인 인증·호실 준비까지
    c = Ctx(s)
    lt, tt = c.lessor_token(), c.tenant_token()
    st, made = call("POST", "/api/rent3_contract/sessions/call", {
        "caller_role": "lessor", "branch": BRANCH, "room_no": s["room_no"], "mode": "NEW",
        "tenant_name": s["tenant_name"], "tenant_phone": s["tenant_phone"], "id_tenant": s["tenant_id"]}, lt)
    sid = made.get("session", {}).get("id", "")
    if not sid:
        sys.exit(f"[준비 실패] 공격 시험용 계약을 만들지 못했습니다: {made}")
    # 본인인증 없는 임차인
    st2, reg = call("POST", "/api/rent3/pool/auth/register", {"role": "prospect", "name": "미인증", "phone": fresh_phone(), "password": TENANT_PW})
    st3, _r = call("POST", "/api/rent3_contract/sessions/call", {
        "caller_role": "tenant", "branch": BRANCH, "room_no": s["room_no"], "mode": "NEW",
        "tenant_name": "미인증", "tenant_phone": reg.get("user", {}).get("phone", "")}, reg.get("token", ""))
    expect("본인인증 안 한 임차인이 계약을 시작", st3, (403,))
    # 다른 임차인이 같은 호실 진행 중에 끼어들기
    st4, other = call("POST", "/api/rent3/pool/auth/register", {"role": "prospect", "name": "끼어들기", "phone": fresh_phone(), "password": TENANT_PW})
    who = {"name": "끼어들기", "phone": other["user"]["phone"], "birth": fresh_birth()}
    call("POST", "/api/rent3_contract/identity/start", who, other["token"])
    call("POST", "/api/rent3_contract/identity/confirm", {**who, "code": "000000"}, other["token"])
    st5, _r = call("POST", "/api/rent3_contract/sessions/call", {
        "caller_role": "tenant", "branch": BRANCH, "room_no": s["room_no"], "mode": "NEW",
        "tenant_name": "끼어들기", "tenant_phone": other["user"]["phone"], "id_tenant": other["user"]["id"]}, other["token"])
    expect("진행 중인 호실에 다른 임차인이 끼어들기", st5, (400, 403, 409))
    st6, _r = call("GET", f"/api/rent3_contract/sessions/{sid}", token=other["token"])
    expect("다른 임차인이 남의 계약 내용 열람", st6, (403,))
    st7, _r = call("GET", f"/api/rent3_contract/sessions/{sid}", token=other_lessor)
    expect("다른 지점 임대인이 계약 내용 열람", st7, (403,))
    st8, _r = call("GET", f"/api/rent3_contract/sessions/{sid}")
    expect("로그인 없이 계약 내용 열람", st8, (401,))
    st9, _r = call("POST", f"/api/rent3_contract/sessions/{sid}/respond", {"responder_role": "lessor", "responder_name": "x"}, tt)
    expect("임차인 토큰으로 임대인 역할 수행", st9, (403,))
    # 사전 작성 규칙
    st10, _r = call("POST", f"/api/rent3_contract/sessions/{sid}/prefill", {"role": "tenant", "updates": {"deposit": 1}}, tt)
    expect("임차인이 보증금을 사전 작성으로 바꾸기", st10, (400,))
    call("POST", f"/api/rent3_contract/sessions/{sid}/prefill", {"role": "lessor", "updates": {"move_in_date": (date.today() + timedelta(days=7)).isoformat()}}, lt)
    call("POST", f"/api/rent3_contract/sessions/{sid}/respond", {"responder_role": "tenant", "responder_name": s["tenant_name"], "responder_phone": s["tenant_phone"], "id_tenant": s["tenant_id"]}, tt)
    st11, _r = call("POST", f"/api/rent3_contract/sessions/{sid}/prefill", {"role": "lessor", "updates": {"move_in_date": "2030-01-01"}}, lt)
    expect("연결 뒤에 사전 작성으로 조건 변경", st11, (400,))

    # ── 등기부 불일치 ──
    call("POST", f"/api/rent3_contract/sessions/{sid}/step1", {"role": "lessor", "checked_counterparty_id": True, "registry_updates": {"owner_name": OWNER, "mortgage_amount": 0}}, lt)
    bad = {"viewed_directly": True, "viewed_at": datetime.now().strftime("%Y-%m-%dT%H:%M"), "owner_name": "박사기", "mortgage_amount": 900000000,
           "seizure_or_trust": "있음", "guarantee_status": "가입 예정"}
    st12, _r = call("POST", f"/api/rent3_contract/sessions/{sid}/tenant-registry", {"registry": bad}, tt)
    expect("임대인 제시와 다른 등기부 값을 확인 없이 제출", st12, (400,))
    st13, _r = call("POST", f"/api/rent3_contract/sessions/{sid}/tenant-registry", {"registry": {**bad, "viewed_directly": False}}, tt)
    expect("직접 열람하지 않고 등기부 제출", st13, (400,))
    st14, _r = call("POST", f"/api/rent3_contract/sessions/{sid}/step1", {"role": "tenant", "checked_counterparty_id": True}, tt)
    expect("등기부 직접 열람 없이 1단계 통과", st14, (400,))

    # ── 체결·교부 순서 ──
    s2 = drive_to(idx("lessor", "complete"))                    # 재열람까지 끝난 새 계약
    c2 = Ctx(s2)
    lt2, tt2 = c2.lessor_token(), c2.tenant_token()
    st15, _r = call("POST", f"/api/rent3_contract/sessions/{c2.sid}/step5/complete", {"payment_method": "TRANSFER", "own_account_checked": False}, lt2)
    expect("입금 확인 표시 없이 체결", st15, (400,))
    s3 = drive_to(idx("tenant", "recheck"))                     # 서명까지 끝났지만 재열람 전
    st16, _r = call("POST", f"/api/rent3_contract/sessions/{Ctx(s3).sid}/step5/complete", {"payment_method": "TRANSFER", "own_account_checked": True}, Ctx(s3).lessor_token())
    expect("체결 직전 재열람 없이 체결", st16, (400,))
    st17, _r = call("POST", f"/api/rent3_contract/sessions/{c2.sid}/step5/complete", {"payment_method": "TRANSFER", "own_account_checked": True}, lt2)
    contract_ok = st17 == 200
    results.append((contract_ok, "(준비) 정상 체결"))
    rid = s2["room_id"]
    home = call("GET", f"/api/rent3_tenant/myhome/{s2['tenant_id']}", token=tt2)[1]
    masked = home.get("keys", {}).get("locked") and "잔금" in str(home.get("room", {}).get("doorlock_pwd", ""))
    results.append((bool(masked), "체결 직후 도어락이 서버에서 가려져 있음"))
    print(f"  {'✅ 막힘' if masked else '❌ 뚫림'}  체결 직후 임차인 화면의 도어락이 마스킹됨")
    st18, _r = call("POST", f"/api/rent3_lessor/rooms/{rid}/release-keys", {"balance_checked": True}, lt2)
    expect("잔금일 등기부 확인 전에 도어락 교부", st18, (400,))
    st19, _r = call("POST", f"/api/rent3_lessor/rooms/{rid}/release-keys", {"balance_checked": True}, tt2)
    expect("임차인이 직접 도어락 교부", st19, (403,))
    st20, _r = call("POST", f"/api/rent3_lessor/rooms/{rid}/release-keys", {"balance_checked": True}, other_lessor)
    expect("다른 지점 임대인이 도어락 교부", st20, (403,))
    call("POST", f"/api/rent3_tenant/myhome/{s2['tenant_id']}/balance-registry-check", {"changed": True, "issue_number": "x"}, tt2)
    st21, _r = call("POST", f"/api/rent3_lessor/rooms/{rid}/release-keys", {"balance_checked": True}, lt2)
    expect("잔금일 등기부 변동 신고 중에 도어락 교부", st21, (400,))
    st22, _r = call("POST", f"/api/rent3_tenant/myhome/{s2['tenant_id']}/balance-registry-check", {"changed": False}, lt2)
    expect("임대인이 임차인 대신 잔금일 등기부 확인", st22, (403,))
    # 점검 사진은 점검 단계에서만
    st23, _r = call("POST", f"/api/rent3_contract/sessions/{c2.sid}/facility-photos", {"role": "lessor", "photo_data_url": PNG}, lt2)
    expect("체결 뒤 시설 점검 사진 추가", st23, (400, 409))

    good = sum(1 for g, _l in results if g)
    print(f"\n결과: {good}/{len(results)} 항목이 기대대로 막혔습니다.")
    for g, label in results:
        if not g:
            print(f"  ❌ 뚫림: {label}")
    return 0 if good == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="임대인·임차인 번갈아 공동 계약 시험")
    ap.add_argument("role", help="status | auto | attack | reset | lessor | tenant")
    ap.add_argument("step", nargs="?", default="")
    a = ap.parse_args()
    if a.role == "status":
        return cmd_status()
    if a.role == "auto":
        return cmd_auto()
    if a.role == "attack":
        return cmd_attack()
    if a.role == "reset":
        save_state({"next": 0, "log": []})
        print("진행 상태를 초기화했습니다.")
        return 0
    if a.role in ("lessor", "tenant"):
        if not a.step:
            print(f"단계 이름이 필요합니다. 예: play.py {a.role} {next(n for r, n, _d in STEPS if r == a.role)}")
            return 2
        return run_step(a.role, a.step)
    print("알 수 없는 명령입니다. status | auto | attack | reset | lessor <단계> | tenant <단계>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
