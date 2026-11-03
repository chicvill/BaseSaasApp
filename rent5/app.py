"""
rent5/app.py — 중개사 없이 임대인 ↔ 임차인이 직접 계약하는 가장 단순한 시스템.

  회원은 구분이 없다. 누구나 매물을 올릴 수 있고 (임대인), 다른 사람의 매물에 신청할 수 있다 (임차인).
  /api/...  JSON API        그 밖의 주소  static/ 의 화면
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import flow
import listings
from security import (ApiError, Lockout, check_password, hash_password, load_secret, make_token, read_token,
                      valid_phone, validate_password, validate_username, clean_text)
from store import Store

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = HERE / "data"



async def json_body(request: Request) -> Dict[str, Any]:
    """요청 본문(JSON 객체). 본문이 없으면 빈 값, 깨졌거나 객체가 아니면 400."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(400, "요청 형식이 올바르지 않습니다.", hint="UTF-8 JSON 으로 보내 주세요.")
    if not isinstance(data, dict):
        raise ApiError(400, "요청 형식이 올바르지 않습니다.")
    return data


Payload = Depends(json_body)


def create_app(data_dir: Optional[str] = None) -> FastAPI:
    data_dir = str(data_dir or os.getenv("RENT5_DATA_DIR") or DEFAULT_DATA)
    app = FastAPI(title="Rent5 직거래 계약", docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(data_dir)
    secret = load_secret(data_dir)
    lockout = Lockout()
    dummy = hash_password("not-a-real-password")                 # 없는 아이디에도 같은 횟수의 해시 계산을 하기 위한 값
    app.state.store = store

    # ── 공통 ──
    @app.exception_handler(ApiError)
    async def api_error(_: Request, err: ApiError) -> JSONResponse:
        return JSONResponse(status_code=err.status, content={"success": False, "message": err.message, "hint": err.hint})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        res = await call_next(request)
        res.headers.setdefault("X-Content-Type-Options", "nosniff")
        res.headers.setdefault("X-Frame-Options", "DENY")
        res.headers.setdefault("Referrer-Policy", "same-origin")
        res.headers.setdefault("Content-Security-Policy",
                               "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            res.headers["Cache-Control"] = "no-store"
        return res

    def public_user(u: Dict[str, Any]) -> Dict[str, str]:
        return {"username": u["username"], "name": u["name"], "phone": u["phone"]}

    def current_user(authorization: str = Header("")) -> Dict[str, Any]:
        data = read_token(secret, authorization.removeprefix("Bearer ").strip()) if authorization else None
        user = store.first("users", username=data["u"]) if data else None
        if not user or user.get("pw_ver", 1) != data.get("v"):          # 비밀번호를 바꾸면 이전 토큰은 끝
            raise ApiError(401, "로그인이 필요합니다.", hint="다시 로그인해 주세요.")
        return user

    def session(user: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "token": make_token(secret, user["username"], user.get("pw_ver", 1)), "user": public_user(user)}

    # ── 회원 ──
    @app.post("/api/signup")
    def signup(payload: Dict[str, Any] = Payload) -> Dict[str, Any]:
        username = validate_username(payload.get("username"))
        name = clean_text(payload.get("name"), "이름", 30, required=True)
        phone = valid_phone(payload.get("phone"))
        password = validate_password(payload.get("password"), username)
        with store.lock:
            if store.first("users", username=username):
                raise ApiError(409, "이미 있는 아이디입니다.", hint="다른 아이디를 정해 주세요.")
            salt, hashed = hash_password(password)
            user = store.insert("users", {"username": username, "name": name, "phone": phone,
                                          "salt": salt, "pw_hash": hashed, "pw_ver": 1})
        return session(user)

    @app.post("/api/login")
    def login(request: Request, payload: Dict[str, Any] = Payload) -> Dict[str, Any]:
        username = str(payload.get("username") or "").strip().lower()
        key = f"{request.client.host if request.client else '-'}|{username}"
        lockout.check(key)
        user = store.first("users", username=username)
        # 아이디가 없어도 같은 횟수의 해시 계산을 해서, 걸린 시간으로 아이디 존재 여부를 알 수 없게 한다.
        salt, expected = (user["salt"], user["pw_hash"]) if user else dummy
        if not (check_password(str(payload.get("password") or ""), salt, expected) and user):
            lockout.fail(key)
            raise ApiError(401, "아이디 또는 비밀번호가 맞지 않습니다.")
        lockout.ok(key)
        return session(user)

    @app.get("/api/me")
    def me(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        return {"success": True, "user": public_user(user)}

    @app.post("/api/me/password")
    def change_password(payload: Dict[str, Any] = Payload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if not check_password(str(payload.get("current") or ""), user["salt"], user["pw_hash"]):
            raise ApiError(403, "현재 비밀번호가 맞지 않습니다.")
        password = validate_password(payload.get("new"), user["username"])
        salt, hashed = hash_password(password)
        user = store.update("users", user["id"], salt=salt, pw_hash=hashed, pw_ver=user.get("pw_ver", 1) + 1)
        return session(user)                                   # 다른 기기의 이전 로그인은 끝나고, 지금 기기만 새 토큰

    # ── 매물 ──
    @app.get("/api/listings")
    def public_listings() -> Dict[str, Any]:
        return {"success": True, "listings": listings.public_list(store)}

    @app.get("/api/listings/{listing_id}")
    def public_listing(listing_id: str) -> Dict[str, Any]:
        l = store.first("listings", id=listing_id)
        if not l or l["status"] != "open":
            raise ApiError(404, "마감되었거나 없는 매물입니다.")
        return {"success": True, "listing": listings.public(l)}

    @app.post("/api/listings")
    def create_listing(payload: Dict[str, Any] = Payload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        return {"success": True, "listing": listings.public(listings.create(store, user["username"], payload))}

    @app.get("/api/my/listings")
    def my_listings(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        return {"success": True, "listings": listings.mine(store, user["username"])}

    @app.post("/api/listings/{listing_id}/withdraw")
    def withdraw(listing_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        listings.withdraw(store, user["username"], listing_id)
        return {"success": True}

    @app.post("/api/listings/{listing_id}/apply")
    def apply(listing_id: str, payload: Dict[str, Any] = Payload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        c = flow.apply(store, listing_id, user["username"], payload.get("message", ""))
        return {"success": True, "contract_id": c["id"]}

    # ── 계약 ──
    @app.get("/api/contracts")
    def my_contracts(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        return {"success": True, "contracts": flow.summaries(store, user["username"])}

    @app.get("/api/contracts/{cid}")
    def contract(cid: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        return {"success": True, "contract": flow.get_view(store, cid, user["username"])}

    @app.get("/api/contracts/{cid}/document")
    def contract_document(cid: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        return {"success": True, "document": flow.document(store, cid, user["username"])}

    def step(name: str, fn):
        def handler(cid: str, payload: Dict[str, Any] = Payload, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
            fn(cid, user["username"], payload)
            return {"success": True, "contract": flow.get_view(store, cid, user["username"])}
        handler.__name__ = f"contract_{name}"
        app.post(f"/api/contracts/{{cid}}/{name}")(handler)

    step("offer", lambda cid, u, p: flow.offer(store, cid, u, p))
    step("tenant-sign", lambda cid, u, p: flow.tenant_sign(store, cid, u, p))
    step("landlord-sign", lambda cid, u, p: flow.landlord_sign(store, cid, u, p))
    step("deposit", lambda cid, u, p: flow.confirm_deposit(store, cid, u))
    step("balance", lambda cid, u, p: flow.confirm_balance(store, cid, u))
    step("cancel", lambda cid, u, p: flow.cancel(store, cid, u, p.get("reason", "")))

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"success": True, "users": len(store.all("users")), "listings": len(store.all("listings"))}

    app.mount("/", StaticFiles(directory=str(HERE / "static"), html=True), name="static")
    return app

