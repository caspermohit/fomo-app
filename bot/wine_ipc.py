"""Wine MT5 IPC failsafe. The Mac↔Wine agent is JSON over stdin/stdout, not port 5055.

Port 5055 is the Flask dashboard. Do not socket-connect there, and do not kill
wineserver / terminal64 — that takes down the demo terminal. Only reap hung
wine_mt5_agent.py processes, then re-initialize.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

AGENT_MARKERS = ("wine_mt5_agent.py", "mt5_mac/agent.py", "mt5_mac\\agent.py")
DEAD_SNIPPETS = (
    "not initialized",
    "backend process is not running",
    "empty response from backend",
    "invalid json from backend",
)

_last_recover_ts = 0.0
_SEND_TIMEOUT = 8.0
_RECOVER_COOLDOWN = 20.0
_pool = None


def _executor():
    global _pool
    import concurrent.futures

    if _pool is None:
        _pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="wine-ipc")
    return _pool


def looks_dead(exc: BaseException | str) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in DEAD_SNIPPETS)


def list_agent_pids() -> list[int]:
    try:
        out = subprocess.check_output(["ps", "ax", "-o", "pid=,command="], text=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if not any(marker in line for marker in AGENT_MARKERS):
            continue
        try:
            pids.append(int(line.split(None, 1)[0]))
        except ValueError:
            continue
    return pids


def reap_hung_agents() -> list[int]:
    """Terminate leftover Wine Python agents only. Leaves MetaTrader running."""
    killed: list[int] = []
    me = os.getpid()
    for pid in list_agent_pids():
        if pid == me:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            continue
    if killed:
        time.sleep(0.4)
        for pid in killed:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return killed


def install_send_timeout(seconds: float = _SEND_TIMEOUT) -> None:
    """Stop stdin/stdout readline from blocking the bot forever."""
    try:
        import mt5_mac.backend as backend
    except ImportError:
        return
    if getattr(backend.MT5Backend._send, "_fomo_timeout", False):
        return
    orig = backend.MT5Backend._send

    def _send(self, command: dict):
        fut = _executor().submit(orig, self, command)
        try:
            return fut.result(timeout=seconds)
        except Exception as exc:
            from concurrent.futures import TimeoutError as FuturesTimeout

            if not isinstance(exc, FuturesTimeout):
                raise
            proc = getattr(self, "_proc", None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            self._started = False
            reap_hung_agents()
            raise RuntimeError(f"Wine IPC timed out on {command.get('action')}") from exc

    _send._fomo_timeout = True  # type: ignore[attr-defined]
    backend.MT5Backend._send = _send


def recover(force: bool = False) -> dict:
    """Shutdown the dead backend, reap hung agents, leave the terminal up."""
    global _last_recover_ts
    now = time.time()
    if not force and now - _last_recover_ts < _RECOVER_COOLDOWN:
        return {"ok": False, "reason": "cooldown", "killed": []}
    _last_recover_ts = now
    try:
        import mt5_mac

        mt5_mac.shutdown()
    except Exception:
        pass
    killed = reap_hung_agents()
    return {"ok": True, "killed": killed}


def maybe_restart_terminal() -> None:
    """Opt-in only. Default is off — relaunching MT5 from here creates more zombies."""
    if os.getenv("WINE_IPC_RESTART_TERMINAL", "").strip().lower() not in {"1", "true", "yes"}:
        return
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "start_mt5.sh"
    if script.exists():
        subprocess.Popen(["/bin/bash", str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
