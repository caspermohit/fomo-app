"""Python owns magic 26082601. Heartbeat so a leftover trading EA yields.

The old FOMO_Second_Scalper Expert used to trade. It now is display-only.
Heartbeating FOMO_PYTHON_ENGINE_ACTIVE still blocks any leftover compiled
trading EA that checks that file. Python never yields to an EA lock.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MAGIC = 26082601
PYTHON_LOCK = "FOMO_PYTHON_ENGINE_ACTIVE"
EA_LOCK = "FOMO_EA_ENGINE_ACTIVE"
STALE_SEC = 8.0


def mql5_files_dir() -> Path:
    prefix = os.environ.get("WINEPREFIX")
    if not prefix:
        if sys.platform == "darwin":
            prefix = str(Path.home() / "Library" / "Application Support" / "net.metaquotes.wine.metatrader5")
        else:
            prefix = str(Path.home() / ".wine-mt5")
    return Path(prefix) / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files"


def _lock_path(name: str) -> Path:
    return mql5_files_dir() / name


def is_fresh(name: str) -> bool:
    path = _lock_path(name)
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip().split()[0]
        ts = float(raw)
    except (OSError, ValueError, IndexError):
        return True
    return (time.time() - ts) <= STALE_SEC


def write_lock(name: str) -> None:
    folder = mql5_files_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(f"{int(time.time())}\n{os.getpid()}\n", encoding="utf-8")
    except OSError:
        pass


def clear_lock(name: str) -> None:
    try:
        _lock_path(name).unlink(missing_ok=True)
    except OSError:
        pass


def leftover_ea() -> bool:
    return is_fresh(EA_LOCK)


def python_heartbeat() -> None:
    write_lock(PYTHON_LOCK)


def python_release() -> None:
    clear_lock(PYTHON_LOCK)


def status() -> dict:
    py = is_fresh(PYTHON_LOCK)
    ea = is_fresh(EA_LOCK)
    if py:
        owner = "python"
    elif ea:
        owner = "leftover_ea"
    else:
        owner = "none"
    return {
        "required": True,
        "magic": MAGIC,
        "python_active": py,
        "ea_active": ea,
        "leftover_ea": ea,
        "owner": owner,
        "one_bot": True,
        "yielded_to_ea": False,
    }
