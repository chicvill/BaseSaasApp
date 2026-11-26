"""
MQnet / BaseSaasApp Automated Verification Script
.agents/skills/mqnet-system-navigator/scripts/verify_system.py

Runs 5 verification stages across the Rent3 codebase (`apps/rent_dist`):
1. Python AST & Syntax Verification
2. Node.js Frontend JS Syntax Verification
3. Core Backend Module Import Integrity
4. Platform Unit Tests (`tests.test_platform`)
5. FastAPI TestClient Rent3 API & SMS/FIFO Smoke Simulation
"""
import ast
import os
import subprocess
import sys

_reconf_out = getattr(sys.stdout, "reconfigure", None)
if callable(_reconf_out):
    try:
        _reconf_out(encoding="utf-8")
    except Exception:
        pass

_reconf_err = getattr(sys.stderr, "reconfigure", None)
if callable(_reconf_err):
    try:
        _reconf_err(encoding="utf-8")
    except Exception:
        pass

cur = os.path.abspath(os.path.dirname(__file__))
ROOT = cur
while cur and os.path.dirname(cur) != cur:
    if os.path.isdir(os.path.join(cur, "apps")):
        ROOT = cur
        break
    cur = os.path.dirname(cur)

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def print_header(title):
    print(f"\n{'='*60}\n>> {title}\n{'='*60}")


def verify_ast():
    print_header("Stage 1: Python AST & Syntax Check (apps/rent_dist)")
    checked = 0
    errors = 0
    for root, _, files in os.walk(os.path.join(ROOT, "apps", "rent_dist")):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                checked += 1
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        ast.parse(fp.read(), filename=path)
                except Exception as e:
                    print(f"  [FAIL] {path}: {e}")
                    errors += 1
    if errors == 0:
        print(f"  [PASS] All {checked} Python files parsed successfully (0 syntax errors).")
        return True
    return False


def verify_frontend_js():
    print_header("Stage 2: Frontend JS Syntax Check (node -c)")
    js_files = []
    for root, _, files in os.walk(os.path.join(ROOT, "apps", "rent_dist")):
        for f in files:
            if f.endswith(".js"):
                js_files.append(os.path.join(root, f))
    if not js_files:
        print("  [SKIP] No JS files found.")
        return True
    errors = 0
    for p in js_files:
        res = subprocess.run(["node", "-c", p], capture_output=True, text=True)
        rel = os.path.relpath(p, ROOT)
        if res.returncode == 0:
            print(f"  [PASS] {rel}")
        else:
            print(f"  [FAIL] {rel}: {res.stderr.strip()}")
            errors += 1
    return errors == 0


def verify_modules():
    print_header("Stage 3: Core Module Import Integrity")
    modules = [
        "apps.rent_dist._rent3_core",
        "apps.rent_dist._rent3_core.models",
        "apps.rent_dist._rent3_core.billing",
        "apps.rent_dist._rent3_core.cosign",
        "apps.rent_dist._rent3_core.ops",
        "apps.rent_dist.rent3.app",
        "apps.rent_dist.rent3_admin.app",
        "apps.rent_dist.rent3_lessor.app",
        "apps.rent_dist.rent3_tenant.app",
        "apps.rent_dist.rent3_contract.app",
    ]
    import importlib
    errors = 0
    for m in modules:
        try:
            importlib.import_module(m)
            print(f"  [PASS] {m}")
        except Exception as e:
            print(f"  [FAIL] {m}: {e}")
            errors += 1
    return errors == 0


def verify_unit_tests():
    print_header("Stage 4: Platform Unit Tests (tests.test_platform)")
    import unittest
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_platform")
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    if result.wasSuccessful():
        print(f"  [PASS] All {result.testsRun} platform tests passed.")
        return True
    print(f"  [FAIL] Failures: {len(result.failures)}, Errors: {len(result.errors)}")
    return False


def verify_api_simulation():
    print_header("Stage 5: FastAPI TestClient & Rent3 Smoke Simulation")
    try:
        from fastapi.testclient import TestClient
        from core.gateway import create_gateway
        from apps.rent_dist._rent3_core.billing import SmsDepositParser

        app = create_gateway(db_path=":memory:")
        client = TestClient(app)

        r_apps = client.get("/api/apps")
        assert r_apps.status_code == 200
        ids = [a["app_id"] for a in r_apps.json()["apps"]]
        assert "rent3" in ids and "rent3_lessor" in ids and "rent2" not in ids

        r_land = client.get("/api/rent3/pool/landing")
        assert r_land.status_code == 200

        parsed = SmsDepositParser.parse("[신한] 11/15 110-482-998877 201호 홍길동 50만원 입금 잔액 12,000,000원")
        assert (parsed["room_no"], parsed["payer_name"], parsed["amount"]) == ("201", "홍길동", 500000), parsed
        print("  [PASS] Gateway catalog, /api/rent3/pool/landing, and SmsDepositParser verified.")
        return True
    except Exception as e:
        print(f"  [FAIL] API simulation error: {e}")
        return False


def main():
    stages = [
        ("Python AST Syntax", verify_ast),
        ("Frontend JS Syntax", verify_frontend_js),
        ("Module Integrity", verify_modules),
        ("Platform Unit Tests", verify_unit_tests),
        ("FastAPI Rent3 Smoke", verify_api_simulation),
    ]

    results = []
    for name, fn in stages:
        results.append((name, fn()))

    print_header("FINAL VERIFICATION SUMMARY")
    all_pass = True
    for name, ok in results:
        status = "PASSED [OK]" if ok else "FAILED [X]"
        if not ok:
            all_pass = False
        print(f"  * {name:<25}: {status}")

    print("\n" + ("=" * 60))
    if all_pass:
        print(">>> ALL VERIFICATION CHECKS COMPLETED SUCCESSFULLY! <<<")
        print("=" * 60 + "\n")
        sys.exit(0)
    else:
        print(">>> SOME VERIFICATION CHECKS FAILED! PLEASE REVIEW LOGS. <<<")
        print("=" * 60 + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
