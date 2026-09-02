#!/usr/bin/env python3
import os

from bot.auth import pin_required
from bot.config import load_settings
from bot.dashboard import create_app
from bot.engine import BotEngine


def main() -> None:
    settings = load_settings()
    engine = BotEngine(settings)
    engine.start()
    app = create_app(engine)
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    print(f"API: http://{host}:{settings.dashboard_port}/api/state", flush=True)
    print(f"Health: http://{host}:{settings.dashboard_port}/api/health", flush=True)
    print("UI:  http://127.0.0.1:3000  (cd web && npm run dev)", flush=True)
    print(f"PIN lock: {'ON' if pin_required() else 'off'}", flush=True)
    if host != "127.0.0.1" and not pin_required():
        print("WARNING: API is bound beyond loopback without DESK_PIN.", flush=True)
    app.run(host=host, port=settings.dashboard_port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
