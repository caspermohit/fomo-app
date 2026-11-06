"""Desk unlock cookie. TOTP authenticator when DESK_TOTP_SECRET is set; else optional DESK_PIN."""

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
_USED_TOTP_STEP: dict[str, int] = {}


def totp_secret() -> str:
    return os.getenv("DESK_TOTP_SECRET", "").strip().replace(" ", "").replace("-", "")


def pin_required() -> bool:
    return bool(totp_secret() or os.getenv("DESK_PIN", "").strip())


def auth_method() -> str:
    if totp_secret():
        return "totp"
    if os.getenv("DESK_PIN", "").strip():
        return "pin"
    return "off"


def cookie_secret() -> str:
    explicit = os.getenv("DESK_SECRET", "").strip()
    if explicit:
        return explicit
    pin = os.getenv("DESK_PIN", "").strip()
    login = os.getenv("MT5_LOGIN", "").strip()
    return hashlib.sha256(f"fomo-desk-v2|{totp_secret()}|{pin}|{login}".encode()).hexdigest()


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


def _digits(got: str) -> str:
    return "".join(ch for ch in str(got or "") if ch.isdigit())


def pin_matches(got: str) -> bool:
    expected = os.getenv("DESK_PIN", "").strip()
    if not expected:
        return True
    digest = lambda s: hashlib.sha256(s.encode()).digest()
    return hmac.compare_digest(digest(got.strip()), digest(expected))


def totp_matches(got: str) -> bool:
    secret = totp_secret()
    if not secret:
        return False
    code = _digits(got)
    if len(code) < 6:
        return False
    try:
        import pyotp
    except ImportError:
        return False
    totp = pyotp.TOTP(secret)
    now = time.time()
    for offset in (-1, 0, 1):
        t = now + offset * totp.interval
        if not totp.verify(code, for_time=t, valid_window=0):
            continue
        step = int(t) // totp.interval
        if _USED_TOTP_STEP.get(secret) == step:
            return False
        _USED_TOTP_STEP[secret] = step
        return True
    return False


def code_matches(got: str) -> bool:
    if totp_secret():
        return totp_matches(got)
    return pin_matches(got)


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


def clear_totp_replay() -> None:
    _USED_TOTP_STEP.clear()


def cookie_secure(request: Request) -> bool:
    host = (request.host or "").split(":")[0].lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return False
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


TOTP_ISSUER = "FOMO"
TOTP_ACCOUNT = "desk"


def enroll_qr_allowed(request: Request) -> bool:
    """QR on the lock screen: localhost, or DESK_TOTP_SHOW_QR=true. Never on a public IP by default."""
    flag = os.getenv("DESK_TOTP_SHOW_QR", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    host = (request.host or "").split(":")[0].lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def totp_uri() -> str:
    secret = totp_secret()
    if not secret:
        return ""
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=TOTP_ACCOUNT, issuer_name=TOTP_ISSUER)


def totp_qr_png(uri: str | None = None) -> bytes:
    data = uri or totp_uri()
    if not data:
        return b""
    import io

    import qrcode

    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#050c09", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def totp_enrolled() -> bool:
    return os.getenv("DESK_TOTP_ENROLLED", "").strip().lower() in {"1", "true", "yes"}


def _upsert_env(key: str, value: str) -> None:
    from bot.config import ROOT

    path = ROOT / ".env"
    lines = path.read_text().splitlines() if path.is_file() else []
    out, seen = [], False
    for line in lines:
        raw = line.strip()
        env_key = ""
        if raw and not raw.startswith("#") and "=" in line:
            env_key = line.split("=", 1)[0].strip()
        if env_key == key:
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
    os.environ[key] = value


def write_totp_secret(secret: str) -> None:
    secret = secret.strip().replace(" ", "").replace("-", "")
    _upsert_env("DESK_TOTP_SECRET", secret)
    _upsert_env("DESK_TOTP_ENROLLED", "false")
    clear_totp_replay()


def mark_totp_enrolled() -> None:
    _upsert_env("DESK_TOTP_ENROLLED", "true")


def lock_screen_qr_allowed(request: Request) -> bool:
    """Setup QR on the lock screen until the first successful unlock, unless forced."""
    force = os.getenv("DESK_TOTP_SHOW_QR", "").strip().lower() in {"1", "true", "yes"}
    if totp_enrolled() and not force:
        return False
    return enroll_qr_allowed(request)


def rotate_totp(current_code: str) -> bool:
    if not totp_secret() or not totp_matches(current_code):
        return False
    import pyotp

    write_totp_secret(pyotp.random_base32())
    return True


def unlock_status(request: Request) -> dict[str, Any]:
    needed = pin_required()
    unlocked = (not needed) or token_ok(request.cookies.get(COOKIE))
    method = auth_method()
    return {
        "ok": True,
        "required": needed,
        "unlocked": unlocked,
        "method": method,
        "show_qr": method == "totp" and (unlocked or lock_screen_qr_allowed(request)),
        "issuer": TOTP_ISSUER,
        "account": TOTP_ACCOUNT,
    }


def locked_response() -> tuple[Any, int]:
    if auth_method() == "totp":
        msg = "authenticator code required"
    else:
        msg = "PIN required"
    return jsonify({"ok": False, "locked": True, "message": msg, "method": auth_method()}), 401


def page_open(request: Request) -> bool:
    """HTML desk and its assets load before unlock; JSON APIs stay locked."""
    if request.method != "GET":
        return False
    path = request.path or "/"
    return path == "/" or path.startswith("/static/")


def gate_request(request: Request) -> tuple[Any, int] | None:
    if request.method == "OPTIONS":
        return None
    if not pin_required():
        return None
    if (request.path, request.method) in OPEN_PATHS:
        return None
    if page_open(request):
        return None
    if token_ok(request.cookies.get(COOKIE)):
        return None
    if request.method == "GET" and request.path == "/api/totp/qr" and lock_screen_qr_allowed(request):
        return None
    return locked_response()
