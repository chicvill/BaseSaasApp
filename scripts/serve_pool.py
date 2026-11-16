"""
scripts/serve_pool.py — 지정한 Data Pool 로 로컬 서버를 띄워 화면을 확인한다 (서버 적용 전 점검용).

  py scripts/serve_pool.py --pool-dir data/private/pool_new --port 9080 [--tokens-out 파일]

--tokens-out: 지정한 임차인 id 목록(--tenants a,b)의 화면 확인용 로그인 정보(JSON)를 적는다. 시연 로그인은 켜지 않는다.
"""
import argparse
import json
import os
import sys

os.environ["RENT3_DEMO_SEED"] = "0"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-dir", required=True)
    ap.add_argument("--port", type=int, default=9080)
    ap.add_argument("--tokens-out", default="")
    ap.add_argument("--tenants", default="")
    ap.add_argument("--lessor-out", default="", help="화면 확인용 임대인(지점 첫 임대인) 로그인 정보(JSON)를 적는다")
    ap.add_argument("--admin-out", default="", help="화면 확인용 관리인 로그인 정보(JSON)를 적는다")
    a = ap.parse_args()
    for k in ("RENT3_DEMO_LOGIN", "RENT3_FAKE_IDENTITY", "RENT3_TODAY", "RENT2_TODAY", "RENT3_POOL_DIR"):
        os.environ.pop(k, None)
    from apps.rent_dist._rent3_core import Tenant, use_pool_dir
    from apps.rent_dist._rent3_core.authz import tenant_token
    from core.gateway import create_gateway
    import uvicorn

    use_pool_dir(os.path.abspath(a.pool_dir))
    app = create_gateway(apps_root=os.path.join(ROOT, "apps"), db_path=":memory:")
    if a.tokens_out:
        out = {}
        for tid in [x for x in a.tenants.split(",") if x]:
            t = Tenant.get(tid)
            if t:
                out[tid] = {"token": tenant_token(t), "user": {"id": t.id, "tenant_id": t.id, "name": t.name, "phone": t.phone,
                                                               "role": "tenant", "branch": t.branch}}
        json.dump(out, open(a.tokens_out, "w", encoding="utf-8"), ensure_ascii=False)
    if a.lessor_out:
        from apps.rent_dist._rent3_core import Lessor
        from apps.rent_dist._rent3_core.authz import lessor_token
        l = Lessor.first(branch="main")
        json.dump({"id": l.id, "token": lessor_token(l), "user": {"id": l.id, "username": l.username or l.id, "name": l.name, "phone": l.phone,
                                                               "role": "lessor", "branch": l.branch}},
                  open(a.lessor_out, "w", encoding="utf-8"), ensure_ascii=False)
    if a.admin_out:
        from core.auth import User as CoreUser, ensure_default_admins, issue_token
        ensure_default_admins()
        u = CoreUser.by_username("admin")
        json.dump({"token": issue_token(u), "user": {"id": "admin", "username": "admin", "name": "관리인", "role": "admin", "branch": "all"}},
                  open(a.admin_out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[pool] http://127.0.0.1:{a.port}  (데이터: {a.pool_dir})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
