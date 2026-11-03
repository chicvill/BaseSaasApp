"""rent5/run.py — 단독 실행: py rent5/run.py [--port 8050] [--host 127.0.0.1]"""
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Rent5 직거래 계약")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8050")))
    args = parser.parse_args()
    uvicorn.run("rent5.app:create_app_with_demo", factory=True, host=args.host, port=args.port)
