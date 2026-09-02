"""PIN unlock for the desk API. Local stays open when DESK_PIN is empty."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from flask import Request, Response, jsonify

COOKIE = "fomo_desk"
COOKIE_TTL = 7 * 24 * 3600
FAIL_WINDOW = 15 * 60
MAX_FAILS = 8
OPEN_PATHS = {("/api/unlock", "GET"), ("/api/unlock", "POST")}

_FAILS: dict[str, list[float]] = {}


def pin_required() -> bool:
    return bool(os.getenv("DESK_PIN", "").strip())


def cookie_secret() -> str:
    explicit = os.getenv("DESK_SECRET", "").strip()
    if explicit:
        return explicit
    pin = os.getenv("DESK_PIN", "").strip()
    login = os.getenv("MT5_LOGIN", "").strip()
    return hashlib.sha256(f"fomo-desk-v1|{pin}|{login}".encode()).hexdigest()


def mint_token(now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + COOKIE_TTL)
    msg = str(exp)
    sig = hmac.new(cookie_secret().encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(value: str | None, now: float | None = None) -> bool:
    if not value or "." not in value:
        return False
    exp_s, sig = value.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(now if now is not None else time.time()):
        return False
    expected = hmac.new(cookie_secret().encode(), exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def pin_matches(got: str) -> bool:
    expected = os.getenv("DESK_PIN", "").strip()
    if not expected:
        return True
    digest = lambda s: hashlib.sha256(s.encode()).digest()
    return hmac.compare_digest(digest(got.strip()), digest(expected))


def client_ip(request: Request) -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or (request.remote_addr or "unknown")
    )


def rate_limited(ip: str, now: float | None = None) -> bool:
    ts = now if now is not None else time.time()
    hits = [t for t in _FAILS.get(ip, []) if ts - t < FAIL_WINDOW]
    _FAILS[ip] = hits
    return len(hits) >= MAX_FAILS


def record_fail(ip: str, now: float | None = None) -> None:
    _FAILS.setdefault(ip, []).append(now if now is not None else time.time())


def clear_fails(ip: str) -> None:
    _FAILS.pop(ip, None)


def cookie_secure(request: Request) -> bool:
    flag = os.getenv("DESK_COOKIE_SECURE", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    return request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https" or bool(
        request.is_secure
    )


def set_unlock_cookie(response: Response, request: Request) -> Response:
    response.set_cookie(
        COOKIE,
        mint_token(),
        max_age=COOKIE_TTL,
        httponly=True,
        samesite="Lax",
        secure=cookie_secure(request),
        path="/",
    )
    return response


def clear_unlock_cookie(response: Response) -> Response:
    response.delete_cookie(COOKIE, path="/")
    return response


def unlock_status(request: Request) -> dict[str, Any]:
    needed = pin_required()
    return {
        "ok": True,
        "required": needed,
        "unlocked": (not needed) or token_ok(request.cookies.get(COOKIE)),
    }


def locked_response() -> tuple[Any, int]:
    return jsonify({"ok": False, "locked": True, "message": "PIN required"}), 401


def gate_request(request: Request) -> tuple[Any, int] | None:
    if request.method == "OPTIONS":
        return None
    if not pin_required():
        return None
    if (request.path, request.method) in OPEN_PATHS:
        return None
    if token_ok(request.cookies.get(COOKIE)):
        return None
    return locked_response()
