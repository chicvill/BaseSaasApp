"""
scripts/make_contract_room.py — 계약방 화면 확인용 풀을 만든다: 시드 풀 + 3단계(계약 조건)까지 진행된 계약방 하나.
  py scripts/make_contract_room.py --pool-dir data/private/pool_c --out data/private/party.json
  py scripts/serve_pool.py --pool-dir data/private/pool_c --port 9093
  E2E_BASE=http://127.0.0.1:9093 node scripts/e2e_pair/phone_contract.mjs data/private/party.json
출력(JSON): 임대인·임차인(본인인증 완료) 토큰·사용자 정보와 세션 id.
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["RENT3_ADMIN_PROXY_STRICT"] = "0"
os.environ["RENT3_TODAY"] = "2026-10-29"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from fastapi.testclient import TestClient
    from apps.rent_dist._rent3_core import Lessor, Tenant, ensure_pool_seeded, use_pool_dir
    from apps.rent_dist._rent3_core.authz import lessor_token, tenant_token
    from core.gateway import create_gateway

    use_pool_dir(os.path.abspath(a.pool_dir))
    ensure_pool_seeded(force=True)
    c = TestClient(create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:"))
    r = c.post("/api/rent3/pool/auth/login", json={"login_mode": "account", "identifier": "admin", "password": "1212"})
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    base = "/api/rent3_contract"
    c.post("/api/rent3_admin/lessors/lessor_chic/room-limit", json={"room_limit": 500})
    c.post("/api/rent3_lessor/rooms", json={"branch": "main", "room_no": "961", "is_published": True, "deposit": 5000000, "monthly_rent": 300000})
    phone = "010-5555-9961"
    made = c.post(f"{base}/sessions/call", json={"caller_role": "lessor", "branch": "main", "room_no": "961", "mode": "NEW", "tenant_name": "김예비", "tenant_phone": phone})
    sid = made.json()["session"]["id"]
    c.post(f"{base}/sessions/{sid}/respond", json={"responder_role": "tenant", "responder_name": "김예비", "responder_phone": phone})
    c.post(f"{base}/sessions/{sid}/step1", json={"role": "lessor", "checked_counterparty_id": True, "registry_updates": {"owner_name": "김종심", "mortgage_amount": 0}})
    c.post(f"{base}/sessions/{sid}/tenant-registry", json={"registry": {"viewed_directly": True, "viewed_at": "2026-11-08T10:00", "owner_name": "김종심",
                                                                         "mortgage_amount": 0, "seizure_or_trust": "없음", "guarantee_status": "가입 예정"}})
    c.post(f"{base}/sessions/{sid}/step1", json={"role": "tenant", "checked_counterparty_id": True})
    c.post(f"{base}/sessions/{sid}/step2", json={"role": "lessor"})
    c.post(f"{base}/sessions/{sid}/step2", json={"role": "tenant"})
    reg = c.post("/api/rent3/pool/auth/register", headers={"Authorization": ""}, json={"role": "prospect", "name": "김예비", "phone": phone, "password": "Pw123456"})
    tenant = Tenant.get(reg.json()["user"]["id"])
    tenant.identity = {"verified": True}
    tenant.save(action="make_contract_room")
    lessor = Lessor.first(username="chic")
    lh = {"Authorization": f"Bearer {lessor_token(lessor)}"}
    th = {"Authorization": f"Bearer {tenant_token(tenant)}"}
    url = f"{base}/sessions/{sid}/messages"
    c.post(url, headers=th, json={"kind": "question", "body": "관리비에 인터넷이 포함인가요?"})
    c.post(url, headers=lh, json={"kind": "question", "body": "네, 인터넷은 관리비에 포함입니다."})
    c.post(url, headers=th, json={"kind": "proposal", "field": "monthly_rent", "new_value": 280000, "body": "월세를 조금만 낮춰 주실 수 있을까요?"})
    out = {
        "session_id": sid,
        "lessor": {"token": lessor_token(lessor), "user": {"id": lessor.id, "username": lessor.username, "name": lessor.name, "phone": lessor.phone, "role": "lessor", "branch": lessor.branch}},
        "tenant": {"token": tenant_token(tenant), "user": {"id": tenant.id, "tenant_id": tenant.id, "name": tenant.name, "phone": tenant.phone, "role": "prospect", "branch": "main"}},
    }
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    s = c.get(f"{base}/sessions/{sid}").json()["session"]
    print(f"계약방 {sid}: 단계 {s['current_step']}, 열린 제안 {len([1 for m in c.get(url + '?mark_read=false', headers=lh).json()['messages'] if m.get('status') == 'open'])}건")


if __name__ == "__main__":
    main()
