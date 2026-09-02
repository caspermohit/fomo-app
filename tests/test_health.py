from types import SimpleNamespace

from bot.health import build_report, http_status


def _engine(**kwargs):
    settings = SimpleNamespace(
        is_demo=True,
        trade_mode="demo",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/Applications/MetaTrader 5.app",
    )
    engine = SimpleNamespace(
        settings=settings,
        mt5_status={"attached": True},
        _mutex_status=lambda: {"owner": "python", "python_active": True, "ea_active": False},
    )
    engine.__dict__.update(kwargs)
    return engine


def test_health_blocks_live_config(monkeypatch):
    monkeypatch.setattr("bot.health.scan_processes", lambda: {
        "mt5_process_running": True,
        "active_zombies_detected": 0,
        "wine_agent_pids": 1,
    })
    monkeypatch.setattr("bot.health.probe_ipc", lambda: {"ok": True, "latency_ms": 2.0, "error": None})
    monkeypatch.setattr("bot.health.script_paths", lambda _s: {"ok": True, "missing": []})
    engine = _engine()
    engine.settings = SimpleNamespace(is_demo=False, trade_mode="live", mt5_server="Live", mt5_path="x")
    report = build_report(engine)
    assert report["status"] == "CRITICAL"
    assert report["checks"]["trade_mode_safe"] is False
    assert http_status(report) == 403


def test_health_ipc_down_with_terminal_is_degraded(monkeypatch):
    monkeypatch.setattr("bot.health.scan_processes", lambda: {
        "mt5_process_running": True,
        "active_zombies_detected": 0,
        "wine_agent_pids": 0,
    })
    monkeypatch.setattr("bot.health.probe_ipc", lambda: {"ok": False, "latency_ms": 0.0, "error": "Wine backend is not started"})
    monkeypatch.setattr("bot.health.script_paths", lambda _s: {"ok": True, "missing": []})
    report = build_report(_engine(mt5_status={"attached": False}))
    assert report["status"] == "DEGRADED"
    assert report["checks"]["ipc_loop_responsive"] is False
    assert http_status(report) == 200


def test_health_ipc_and_terminal_down_is_critical(monkeypatch):
    monkeypatch.setattr("bot.health.scan_processes", lambda: {
        "mt5_process_running": False,
        "active_zombies_detected": 0,
        "wine_agent_pids": 0,
    })
    monkeypatch.setattr("bot.health.probe_ipc", lambda: {"ok": False, "latency_ms": 0.0, "error": "dead"})
    monkeypatch.setattr("bot.health.script_paths", lambda _s: {"ok": True, "missing": []})
    report = build_report(_engine(mt5_status={"attached": False}))
    assert report["status"] == "CRITICAL"
    assert http_status(report) == 500
