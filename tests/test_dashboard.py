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

    def take_htf_report(self, symbol=None):
        self.calls.append(("report", symbol))
        return {"ok": False, "message": "H1 has not swept a swing"}

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
        monkeypatch.delenv("DESK_TOTP_SECRET", raising=False)
        monkeypatch.delenv("DESK_TOTP_ENROLLED", raising=False)
        monkeypatch.delenv("DESK_SECRET", raising=False)
    engine = FakeEngine()
    return create_app(engine).test_client(), engine


def test_state_and_trade_routes_hit_engine(monkeypatch):
    client, engine = _client(monkeypatch)
    assert client.get("/api/state").json["broker"] == "paper"
    assert client.post("/api/start").json["ok"] is True
    assert client.post("/api/buy").json["ticket"] == "PAPER-1"
    assert client.post("/api/sell").json["ok"] is True
    assert client.post("/api/report/take", json={"symbol": "EURUSD"}).json["ok"] is False
    assert client.post("/api/close", json={"ticket": "PAPER-1"}).json["ok"] is True
    assert client.post("/api/close-all").json["ok"] is True
    assert client.post("/api/step").json["message"] == "stepped"
    assert client.post("/api/settings", json={"max_positions": 4}).json["max_positions"] == 4
    assert client.post("/api/settings", json={"fill_venue": "paper"}).json["fill_venue"] == "paper"
    assert client.post("/api/reconnect").json["ok"] is False
    assert engine.calls == [
        "start",
        "buy",
        "sell",
        ("report", "EURUSD"),
        ("close", "PAPER-1"),
        "close-all",
        "step",
        ("max", 4),
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


def test_home_serves_graphical_desk(monkeypatch):
    client, _engine = _client(monkeypatch)
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    assert b"Demo desk" in res.data
    assert b"/static/desk.js" in res.data
    assert b'max="4"' in res.data
    assert b"btn-report" in res.data
    assert b"Report" in res.data
    js = client.get("/static/desk.js")
    assert js.status_code == 200
    assert b"drawCandles" in js.data
    assert b"drawMacdCrosses" in js.data
    assert b"KRONOS" in js.data
    assert b'label: "News"' in js.data
    assert b"/api/report/take" in js.data
    css = client.get("/static/desk.css")
    assert css.status_code == 200
    assert b"historycard th:nth-child(1)" not in css.data
    assert b".lockcard .reason[hidden]" in css.data
    assert b".workbody > *" in css.data
    assert b".chartbar" in css.data
    assert b"flex-shrink: 0" in css.data
    assert b"fmtTime" in js.data
