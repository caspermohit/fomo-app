from __future__ import annotations

import os

from flask import Flask, Response, jsonify, render_template, request

from bot.auth import (
    TOTP_ACCOUNT,
    TOTP_ISSUER,
    auth_method,
    clear_fails,
    clear_unlock_cookie,
    client_ip,
    code_matches,
    gate_request,
    mark_totp_enrolled,
    pin_required,
    rate_limited,
    record_fail,
    rotate_totp,
    set_unlock_cookie,
    totp_qr_png,
    totp_secret,
    unlock_status,
)
from bot.engine import BotEngine
from bot.health import make_health_blueprint


def _ui_origin() -> str:
    return os.getenv("UI_ORIGIN", "http://127.0.0.1:3000").rstrip("/")


def create_app(engine: BotEngine) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(make_health_blueprint(engine))

    @app.before_request
    def require_pin():
        blocked = gate_request(request)
        if blocked is not None:
            return blocked
        return None

    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        if not pin_required():
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        origin = request.headers.get("Origin")
        allowed = _ui_origin()
        if origin == allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response

    @app.route("/api/<path:_path>", methods=["OPTIONS"])
    def cors_preflight(_path: str):
        return ("", 204)

    @app.get("/api/unlock")
    def unlock_get():
        return jsonify(unlock_status(request))

    @app.post("/api/unlock")
    def unlock_post():
        if not pin_required():
            return jsonify({"ok": True, "unlocked": True, "required": False})
        ip = client_ip(request)
        if rate_limited(ip):
            return jsonify({"ok": False, "locked": True, "message": "too many tries"}), 429
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or data.get("pin") or "")
        if not code_matches(code):
            record_fail(ip)
            kind = "code" if auth_method() == "totp" else "PIN"
            return jsonify({"ok": False, "locked": True, "message": f"wrong {kind}", "method": auth_method()}), 401
        clear_fails(ip)
        if auth_method() == "totp":
            mark_totp_enrolled()
        response = jsonify(
            {
                "ok": True,
                "unlocked": True,
                "required": True,
                "method": auth_method(),
                "show_qr": False,
            }
        )
        return set_unlock_cookie(response, request)

    @app.post("/api/lock")
    def lock_post():
        response = jsonify({"ok": True, "unlocked": False, "required": pin_required()})
        return clear_unlock_cookie(response)

    @app.get("/api/totp/qr")
    def totp_qr():
        if auth_method() != "totp" or not totp_secret():
            return jsonify({"ok": False, "message": "authenticator is not set"}), 404
        png = totp_qr_png()
        if not png:
            return jsonify({"ok": False, "message": "could not build QR"}), 500
        return Response(png, mimetype="image/png")

    @app.post("/api/totp/rotate")
    def totp_rotate():
        if auth_method() != "totp":
            return jsonify({"ok": False, "message": "authenticator is not set"}), 400
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or data.get("pin") or "")
        if not rotate_totp(code):
            return jsonify({"ok": False, "message": "wrong code — scan stays the same"}), 401
        response = jsonify(
            {
                "ok": True,
                "message": "New QR is ready. Scan it, then the old 6-digit codes stop working.",
                "issuer": TOTP_ISSUER,
                "account": TOTP_ACCOUNT,
            }
        )
        return set_unlock_cookie(response, request)

    @app.get("/")
    def home():
        return render_template("desk.html")

    @app.get("/api/state")
    def state():
        return jsonify(engine.snapshot())

    @app.post("/api/start")
    def start():
        engine.start()
        return jsonify({"ok": True, "message": "bot started"})

    @app.post("/api/stop")
    def stop():
        engine.stop()
        return jsonify({"ok": True, "message": "bot stopping"})

    @app.post("/api/step")
    def step():
        return jsonify(engine.force_step())

    @app.post("/api/buy")
    def buy():
        data = request.get_json(silent=True) or {}
        return jsonify(engine.manual_buy(data.get("symbol")))

    @app.post("/api/sell")
    def sell():
        data = request.get_json(silent=True) or {}
        return jsonify(engine.manual_sell(data.get("symbol")))

    @app.post("/api/report/take")
    def report_take():
        data = request.get_json(silent=True) or {}
        return jsonify(engine.take_htf_report(data.get("symbol")))

    @app.post("/api/close")
    def close():
        data = request.get_json(silent=True) or {}
        return jsonify(engine.manual_close(str(data.get("ticket") or "")))

    @app.post("/api/close-all")
    def close_all():
        return jsonify(engine.manual_close_all())

    @app.post("/api/settings")
    def settings():
        data = request.get_json(silent=True) or {}
        if "focus" in data:
            return jsonify(engine.set_focus(str(data["focus"])))
        if "fill_venue" in data:
            return jsonify(engine.set_fill_venue(str(data["fill_venue"])))
        if "max_positions" in data:
            return jsonify(engine.set_max_positions(int(data["max_positions"])))
        return jsonify({"ok": False, "message": "no settings provided"})

    @app.post("/api/reconnect")
    def reconnect():
        return jsonify(engine.retry_mt5())

    return app
