from __future__ import annotations

import os

from flask import Flask, jsonify, request

from bot.auth import (
    clear_fails,
    clear_unlock_cookie,
    client_ip,
    gate_request,
    pin_matches,
    pin_required,
    rate_limited,
    record_fail,
    set_unlock_cookie,
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
            return jsonify({"ok": False, "locked": True, "message": "too many PIN tries"}), 429
        data = request.get_json(silent=True) or {}
        pin = str(data.get("pin") or "")
        if not pin_matches(pin):
            record_fail(ip)
            return jsonify({"ok": False, "locked": True, "message": "wrong PIN"}), 401
        clear_fails(ip)
        response = jsonify({"ok": True, "unlocked": True, "required": True})
        return set_unlock_cookie(response, request)

    @app.post("/api/lock")
    def lock_post():
        response = jsonify({"ok": True, "unlocked": False, "required": pin_required()})
        return clear_unlock_cookie(response)

    @app.get("/")
    def home():
        return jsonify({"ui": _ui_origin(), "state": "/api/state"})

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
