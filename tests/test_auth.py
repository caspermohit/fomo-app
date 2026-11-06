from bot.auth import clear_fails, clear_totp_replay
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


def _client(monkeypatch, pin="2468", secret="unit-secret", totp=None):
    if totp:
        monkeypatch.setenv("DESK_TOTP_SECRET", totp)
        monkeypatch.delenv("DESK_PIN", raising=False)
    else:
        monkeypatch.setenv("DESK_PIN", pin)
        monkeypatch.delenv("DESK_TOTP_SECRET", raising=False)
    monkeypatch.setenv("DESK_SECRET", secret)
    monkeypatch.setenv("UI_ORIGIN", "http://127.0.0.1:3000")
    monkeypatch.delenv("DESK_TOTP_ENROLLED", raising=False)
    clear_fails("127.0.0.1")
    clear_totp_replay()
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
    assert status.json["method"] == "pin"
    wrong = client.post("/api/unlock", json={"pin": "0000"})
    assert wrong.status_code == 401
    assert client.get("/api/state").status_code == 401
    ok = client.post("/api/unlock", json={"pin": "2468"})
    assert ok.status_code == 200
    assert ok.json["unlocked"] is True
    assert client.get("/api/state").json["broker"] == "paper"
    assert client.get("/api/health").status_code == 200


def test_totp_unlocks_and_rejects_pin(monkeypatch, tmp_path):
    import pyotp

    secret = "JBSWY3DPEHPK3PXP"
    (tmp_path / ".env").write_text(f"DESK_TOTP_SECRET={secret}\n")
    monkeypatch.setattr("bot.config.ROOT", tmp_path)
    client = _client(monkeypatch, totp=secret)
    assert client.get("/api/unlock").json["method"] == "totp"
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/unlock", json={"pin": "2468"}).status_code == 401
    code = pyotp.TOTP(secret).now()
    ok = client.post("/api/unlock", json={"code": code})
    assert ok.status_code == 200
    assert ok.json["unlocked"] is True
    assert client.get("/api/state").status_code == 200
    client.post("/api/lock")
    replay = client.post("/api/unlock", json={"code": code})
    assert replay.status_code == 401


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
    monkeypatch.delenv("DESK_TOTP_SECRET", raising=False)
    monkeypatch.delenv("DESK_SECRET", raising=False)
    client = create_app(FakeEngine()).test_client()
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/unlock").json["required"] is False
    assert client.get("/api/unlock").json["method"] == "off"


def test_localhost_cookie_not_secure(monkeypatch):
    client = _client(monkeypatch)
    res = client.post("/api/unlock", json={"pin": "2468"})
    cookie = ";".join(res.headers.getlist("Set-Cookie"))
    assert "fomo_desk=" in cookie
    assert "Secure" not in cookie


def test_totp_qr_on_localhost_without_cookie(monkeypatch):
    client = _client(monkeypatch, totp="JBSWY3DPEHPK3PXP")
    status = client.get("/api/unlock")
    assert status.json["show_qr"] is True
    res = client.get("/api/totp/qr")
    assert res.status_code == 200
    assert res.mimetype == "image/png"
    assert res.data.startswith(b"\x89PNG")


def test_totp_qr_hides_after_first_unlock(monkeypatch, tmp_path):
    import pyotp

    secret = "JBSWY3DPEHPK3PXP"
    (tmp_path / ".env").write_text(f"DESK_TOTP_SECRET={secret}\n")
    monkeypatch.setattr("bot.config.ROOT", tmp_path)
    client = _client(monkeypatch, totp=secret)
    assert client.get("/api/totp/qr").status_code == 200
    assert client.post("/api/unlock", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    client.post("/api/lock")
    status = client.get("/api/unlock")
    assert status.json["show_qr"] is False
    assert client.get("/api/totp/qr").status_code == 401


def test_totp_qr_hidden_off_localhost(monkeypatch):
    client = _client(monkeypatch, totp="JBSWY3DPEHPK3PXP")
    res = client.get("/api/totp/qr", headers={"Host": "100.116.239.1:5055"})
    assert res.status_code == 401


def test_totp_rotate_writes_new_secret(monkeypatch, tmp_path):
    import pyotp

    from bot.auth import clear_totp_replay

    secret = "JBSWY3DPEHPK3PXP"
    env = tmp_path / ".env"
    env.write_text(f"DESK_TOTP_SECRET={secret}\n")
    monkeypatch.setattr("bot.config.ROOT", tmp_path)
    client = _client(monkeypatch, totp=secret)
    code = pyotp.TOTP(secret).now()
    assert client.post("/api/unlock", json={"code": code}).status_code == 200
    clear_totp_replay()
    rotated = client.post("/api/totp/rotate", json={"code": pyotp.TOTP(secret).now()})
    assert rotated.status_code == 200
    text = env.read_text()
    new_secret = text.split("DESK_TOTP_SECRET=", 1)[1].splitlines()[0].strip()
    assert new_secret != secret
    assert len(new_secret) >= 16
    assert "DESK_TOTP_ENROLLED=false" in text
    client.post("/api/lock")
    assert client.get("/api/totp/qr").status_code == 200
