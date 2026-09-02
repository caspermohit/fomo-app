from bot.auth import COOKIE, clear_fails
from bot.dashboard import create_app


class FakeEngine:
    def snapshot(self):
        return {"running": True, "broker": "paper"}

    def health(self):
        return {
            "status": "HEALTHY",
            "checks": {"trade_mode_safe": True},
            "metrics": {},
            "warnings": [],
        }

    def start(self):
        return None


def _client(monkeypatch, pin="2468", secret="unit-secret"):
    monkeypatch.setenv("DESK_PIN", pin)
    monkeypatch.setenv("DESK_SECRET", secret)
    monkeypatch.setenv("UI_ORIGIN", "http://127.0.0.1:3000")
    clear_fails("127.0.0.1")
    return create_app(FakeEngine()).test_client()


def test_pin_blocks_state_until_unlock(monkeypatch):
    client = _client(monkeypatch)
    locked = client.get("/api/state")
    assert locked.status_code == 401
    assert locked.json["locked"] is True
    health = client.get("/api/health")
    assert health.status_code == 401
    status = client.get("/api/unlock")
    assert status.json["required"] is True
    assert status.json["unlocked"] is False
    wrong = client.post("/api/unlock", json={"pin": "0000"})
    assert wrong.status_code == 401
    assert client.get("/api/state").status_code == 401
    ok = client.post("/api/unlock", json={"pin": "2468"})
    assert ok.status_code == 200
    assert ok.json["unlocked"] is True
    assert client.get("/api/state").json["broker"] == "paper"
    assert client.get("/api/health").status_code == 200


def test_wrong_pin_rate_limit(monkeypatch):
    client = _client(monkeypatch)
    last = None
    for _ in range(8):
        last = client.post("/api/unlock", json={"pin": "nope"})
        assert last.status_code == 401
    jammed = client.post("/api/unlock", json={"pin": "2468"})
    assert jammed.status_code == 429


def test_lock_clears_cookie(monkeypatch):
    client = _client(monkeypatch)
    client.post("/api/unlock", json={"pin": "2468"})
    assert client.get("/api/state").status_code == 200
    client.post("/api/lock")
    assert client.get("/api/state").status_code == 401


def test_no_pin_stays_open(monkeypatch):
    monkeypatch.delenv("DESK_PIN", raising=False)
    monkeypatch.delenv("DESK_SECRET", raising=False)
    client = create_app(FakeEngine()).test_client()
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/unlock").json["required"] is False
