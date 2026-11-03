"""rent5/run.py — 실행: py rent5/run.py [--port 8050] [--host 127.0.0.1]"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Rent5 직거래 계약")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8050")))
    args = parser.parse_args()
    uvicorn.run("app:create_app", factory=True, host=args.host, port=args.port, app_dir=HERE)
