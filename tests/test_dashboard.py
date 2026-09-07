from bot.dashboard import create_app


class FakeEngine:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {"running": True, "broker": "paper", "blockers": []}

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def force_step(self):
        self.calls.append("step")
        return {"ok": True, "message": "stepped"}

    def manual_buy(self, symbol=None):
        self.calls.append("buy")
        return {"ok": True, "message": "paper BUY", "ticket": "PAPER-1"}

    def manual_sell(self, symbol=None):
        self.calls.append("sell")
        return {"ok": True, "message": "paper SELL", "ticket": "PAPER-2"}

    def manual_close(self, ticket: str):
        self.calls.append(("close", ticket))
        return {"ok": True, "message": f"closed {ticket}"}

    def manual_close_all(self):
        self.calls.append("close-all")
        return {"ok": True, "message": "flat"}

    def set_max_positions(self, n: int):
        self.calls.append(("max", n))
        return {"ok": True, "max_positions": n}

    def set_fill_venue(self, venue: str):
        self.calls.append(("fill_venue", venue))
        return {"ok": True, "fill_venue": venue, "message": "fills are PAPER"}

    def set_focus(self, symbol: str):
        self.calls.append(("focus", symbol))
        return {"ok": True, "focus": symbol}

    def retry_mt5(self):
        self.calls.append("reconnect")
        return {"ok": False, "message": "Could not attach to MetaTrader 5"}

    def health(self):
        return {
            "status": "HEALTHY",
            "checks": {
                "trade_mode_safe": True,
                "mt5_process_running": True,
                "ipc_loop_responsive": True,
                "mutex_lock_clear": True,
                "script_paths_ok": True,
            },
            "metrics": {"ipc_latency_ms": 1.5, "active_zombies_detected": 0, "wine_agent_pids": 1},
            "warnings": [],
            "error_reason": None,
        }


def _client(monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.delenv("DESK_PIN", raising=False)
        monkeypatch.delenv("DESK_SECRET", raising=False)
    engine = FakeEngine()
    return create_app(engine).test_client(), engine


def test_state_and_trade_routes_hit_engine(monkeypatch):
    client, engine = _client(monkeypatch)
    assert client.get("/api/state").json["broker"] == "paper"
    assert client.post("/api/start").json["ok"] is True
    assert client.post("/api/buy").json["ticket"] == "PAPER-1"
    assert client.post("/api/sell").json["ok"] is True
    assert client.post("/api/close", json={"ticket": "PAPER-1"}).json["ok"] is True
    assert client.post("/api/close-all").json["ok"] is True
    assert client.post("/api/step").json["message"] == "stepped"
    assert client.post("/api/settings", json={"max_positions": 2}).json["max_positions"] == 2
    assert client.post("/api/settings", json={"fill_venue": "paper"}).json["fill_venue"] == "paper"
    assert client.post("/api/reconnect").json["ok"] is False
    assert engine.calls == [
        "start",
        "buy",
        "sell",
        ("close", "PAPER-1"),
        "close-all",
        "step",
        ("max", 2),
        ("fill_venue", "paper"),
        "reconnect",
    ]


def test_health_route(monkeypatch):
    client, _engine = _client(monkeypatch)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json
    assert body["status"] == "HEALTHY"
    assert body["checks"]["trade_mode_safe"] is True
    assert body["metrics"]["ipc_latency_ms"] == 1.5
