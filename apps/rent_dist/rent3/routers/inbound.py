"""apps/rent_dist/rent3/routers/inbound.py — 외부 수신 서비스(Cloudflare Email Worker 등)가 은행 입금 메일을 넘기는 서명 웹훅.

로그인 토큰이 아니라 HMAC 서명(환경변수 RENT3_MAIL_SECRET)으로만 연다. 비밀키가 없으면 기능 자체가 꺼진 것으로 본다(503).
요청 본문(JSON): {to, message_id, from_addr, dkim_pass_domains[], subject, text|html, ts}  헤더: X-Rent3-Signature = HMAC-SHA256(secret, 원본 본문) 16진수.
응답에는 처리 상태만 담고 개인정보는 돌려주지 않는다.
"""
import json
import os
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from apps.rent_dist._rent3_core.authz import api_headers

router = APIRouter(prefix="/inbound", tags=["rent3-inbound"], dependencies=[Depends(api_headers)])


@router.post("/mail")
async def inbound_mail(request: Request):
    from apps.rent_dist._rent3_core import mailpay
    secret = os.getenv("RENT3_MAIL_SECRET", "")
    if not secret:
        return JSONResponse({"success": False, "message": "입금 메일 수신이 설정되어 있지 않습니다."}, status_code=503)
    raw = await request.body()
    if len(raw) > 1_000_000 or not mailpay.verify_signature(secret, raw, request.headers.get("x-rent3-signature", "")):
        return JSONResponse({"success": False, "message": "서명이 올바르지 않습니다."}, status_code=401)
    try:
        payload = json.loads(raw.decode("utf-8"))
        ts = float(payload.get("ts") or 0)
    except Exception:
        return JSONResponse({"success": False, "message": "형식이 올바르지 않습니다."}, status_code=400)
    if abs(time.time() - ts) > mailpay.SIG_MAX_AGE_SEC:
        return JSONResponse({"success": False, "message": "요청 시각이 맞지 않습니다."}, status_code=401)
    res = mailpay.ingest(payload)
    return {"success": True, "status": res.get("status", "")}
