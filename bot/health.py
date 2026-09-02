"""Desk health: demo lock, Wine/MT5 processes, IPC ping, mutex, script paths.

Uses the existing mt5_mac backend. Do not construct a second Wine agent — that
calls initialize() and steals the live IPC singleton from the trading engine.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from flask import Blueprint, jsonify

from bot.mutex import mql5_files_dir
from bot.wine_ipc import list_agent_pids

AGENT_PATH = Path(__file__).resolve().parent / "wine_mt5_agent.py"
_IPC_CACHE: tuple[float, dict] | None = None
_IPC_CACHE_SEC = 2.0


def scan_processes() -> dict:
    mt5_running = False
    zombies = 0
    try:
        out = subprocess.check_output(
            ["ps", "ax", "-o", "pid=,stat=,command="],
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return {
            "mt5_process_running": False,
            "active_zombies_detected": 0,
            "wine_agent_pids": len(list_agent_pids()),
        }
    for line in out.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(None, 2)
        if len(parts) < 3:
            continue
        stat, command = parts[1], parts[2]
        low = command.lower()
        if "terminal64" in low or "terminal.exe" in low:
            mt5_running = True
        wineish = any(
            token in low
            for token in ("wineserver", "terminal64", "terminal.exe", "wine64", "wine_mt5_agent")
        )
        if wineish and stat.upper().startswith("Z"):
            zombies += 1
    return {
        "mt5_process_running": mt5_running,
        "active_zombies_detected": zombies,
        "wine_agent_pids": len(list_agent_pids()),
    }


def script_paths(settings) -> dict:
    missing: list[str] = []
    mt5_path = Path(getattr(settings, "mt5_path", "") or "")
    if not mt5_path.exists():
        missing.append(str(mt5_path) or "MT5_PATH")
    if not AGENT_PATH.exists():
        missing.append(str(AGENT_PATH))
    files = mql5_files_dir()
    if not files.exists():
        missing.append(str(files))
    return {"ok": not missing, "missing": missing}


def probe_ipc() -> dict:
    """Ping the live Wine backend. Cached so a 1s UI poll cannot stampede IPC."""
    global _IPC_CACHE
    now = time.monotonic()
    if _IPC_CACHE and now - _IPC_CACHE[0] < _IPC_CACHE_SEC:
        return dict(_IPC_CACHE[1])
    t0 = time.perf_counter()
    try:
        import mt5_mac

        backend = getattr(mt5_mac, "_backend", None)
        if backend is None or not getattr(backend, "_started", False):
            result = {"ok": False, "latency_ms": 0.0, "error": "Wine backend is not started"}
        else:
            got = backend._send({"action": "ping"}) or {}
            latency = round((time.perf_counter() - t0) * 1000, 2)
            ok = bool(got.get("success") or got.get("connected"))
            result = {
                "ok": ok,
                "latency_ms": latency,
                "error": None if ok else str(got.get("error") or got or "ping failed"),
            }
    except Exception as exc:
        result = {
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "error": str(exc),
        }
    _IPC_CACHE = (now, result)
    return dict(result)


def http_status(report: dict) -> int:
    checks = report.get("checks") or {}
    if not checks.get("trade_mode_safe"):
        return 403
    if report.get("status") == "CRITICAL":
        return 500
    return 200


def build_report(engine) -> dict:
    settings = engine.settings
    demo_ok = bool(getattr(settings, "is_demo", False))
    procs = scan_processes()
    paths = script_paths(settings)
    ipc = probe_ipc()
    mutex = {}
    try:
        mutex = engine._mutex_status() if hasattr(engine, "_mutex_status") else {}
    except Exception:
        mutex = {"owner": "unknown"}
    mutex_clear = not bool(mutex.get("ea_active") or mutex.get("leftover_ea"))

    checks = {
        "trade_mode_safe": demo_ok,
        "mt5_process_running": bool(procs["mt5_process_running"]),
        "ipc_loop_responsive": bool(ipc["ok"]),
        "mutex_lock_clear": mutex_clear,
        "script_paths_ok": bool(paths["ok"]),
    }
    attached = bool((getattr(engine, "mt5_status", None) or {}).get("attached"))
    warnings: list[str] = []
    status = "HEALTHY"
    error_reason = None

    if not demo_ok:
        status = "CRITICAL"
        error_reason = "Live trading configurations detected. Process hard-blocked."
        return {
            "status": status,
            "timestamp": time.time(),
            "checks": checks,
            "metrics": {
                "ipc_latency_ms": float(ipc.get("latency_ms") or 0.0),
                "active_zombies_detected": int(procs["active_zombies_detected"]),
                "wine_agent_pids": int(procs["wine_agent_pids"]),
            },
            "mutex": mutex,
            "warnings": warnings,
            "error_reason": error_reason,
            "paths": paths,
        }

    if not paths["ok"]:
        warnings.append("missing paths: " + ", ".join(paths["missing"]))
        status = "DEGRADED"
    if not mutex_clear:
        warnings.append("Old trading EA still on the chart — Refresh Navigator. Python is the only bot.")
        status = "DEGRADED"
    if procs["active_zombies_detected"] > 0:
        warnings.append(f"{procs['active_zombies_detected']} Wine/MT5 zombie process(es)")
        status = "DEGRADED"
    if not procs["mt5_process_running"]:
        warnings.append("terminal64 is not in the process list")
        status = "DEGRADED"
    if not ipc["ok"]:
        warnings.append(ipc.get("error") or "Wine IPC ping failed")
        status = "CRITICAL" if not procs["mt5_process_running"] else "DEGRADED"
    elif not attached:
        warnings.append("IPC answered but the engine is not attached")
        if status == "HEALTHY":
            status = "DEGRADED"

    if ipc["ok"] and float(ipc.get("latency_ms") or 0) >= 2000:
        warnings.append(f"IPC latency {ipc['latency_ms']} ms")
        if status == "HEALTHY":
            status = "DEGRADED"

    return {
        "status": status,
        "timestamp": time.time(),
        "checks": checks,
        "metrics": {
            "ipc_latency_ms": float(ipc.get("latency_ms") or 0.0),
            "active_zombies_detected": int(procs["active_zombies_detected"]),
            "wine_agent_pids": int(procs["wine_agent_pids"]),
        },
        "mutex": mutex,
        "warnings": warnings,
        "error_reason": error_reason,
        "paths": paths,
    }


def make_health_blueprint(engine) -> Blueprint:
    bp = Blueprint("health", __name__)

    @bp.get("/api/health")
    def system_health_check():
        report = engine.health()
        return jsonify(report), http_status(report)

    return bp
