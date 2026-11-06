"""DEMO-ONLY Kronos-mini skip/allow gate beside MACD Bill.

Reads a decisions JSON produced by scripts/run_kronos_demo_filter.py (server
Kronos-mini forecast). Does not place orders. When KRONOS_FILTER is false
(default), every check is a no-op allow.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.config import DATA_DIR, ROOT

DEFAULT_DECISIONS_PATH = DATA_DIR / "kronos_decisions.json"
# Stale forecasts should not silently allow forever when the gate is on.
DEFAULT_MAX_AGE_SEC = 30 * 60


def decisions_path(override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    return DEFAULT_DECISIONS_PATH


def load_decisions(path: str | Path | None = None) -> dict[str, Any]:
    p = decisions_path(path)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _symbol_blob(payload: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        return None
    blob = symbols.get(str(symbol).upper())
    return blob if isinstance(blob, dict) else None


def predicted_side(blob: dict[str, Any]) -> str:
    side = str(blob.get("predicted_side") or "").upper()
    if side in {"BUY", "SELL", "CHOP"}:
        return side
    if blob.get("chop"):
        return "CHOP"
    slope = blob.get("slope")
    try:
        s = float(slope)
    except (TypeError, ValueError):
        return "CHOP"
    if s > 0:
        return "BUY"
    if s < 0:
        return "SELL"
    return "CHOP"


def kronos_allows(
    symbol: str,
    macd_side: str,
    *,
    enabled: bool = False,
    path: str | Path | None = None,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
    payload: dict[str, Any] | None = None,
    chop_policy: str = "allow",
) -> tuple[bool, str, dict[str, Any]]:
    """Return (ok, reason, checks). When disabled, always ok.

    Skip opposite / missing / stale. CHOP is no opinion: default allow MACD
    (`chop_policy=allow`). `chop_policy=skip` restores the old block-on-chop gate.
    Never opens a trade on its own.
    """
    checks: dict[str, Any] = {
        "kronos_gate": "off",
        "kronos_predicted": "n/a",
        "kronos_decision": "n/a",
    }
    if not enabled:
        return True, "kronos filter off", checks

    side = str(macd_side or "").upper()
    if side not in {"BUY", "SELL"}:
        checks["kronos_gate"] = "n/a"
        return True, "no MACD entry side", checks

    data = payload if payload is not None else load_decisions(path)
    checks["kronos_gate"] = "on"
    if not data:
        checks["kronos_decision"] = "skip"
        return False, f"Kronos skip {symbol}: no decisions file", checks

    updated = data.get("updated_at_unix")
    try:
        age = time.time() - float(updated)
    except (TypeError, ValueError):
        age = None
    if age is not None and max_age_sec > 0 and age > max_age_sec:
        checks["kronos_decision"] = "skip"
        checks["kronos_age_sec"] = int(age)
        return False, f"Kronos skip {symbol}: forecast stale ({int(age)}s)", checks

    blob = _symbol_blob(data, symbol)
    if not blob:
        checks["kronos_decision"] = "skip"
        return False, f"Kronos skip {symbol}: no forecast row", checks

    pred = predicted_side(blob)
    checks["kronos_predicted"] = pred
    checks["kronos_slope"] = blob.get("slope")
    checks["kronos_rel_bps"] = blob.get("rel_change_bps")
    checks["kronos_bars_end"] = blob.get("bars_end")
    chop = pred == "CHOP" or bool(blob.get("chop"))
    policy = "skip" if str(chop_policy or "").strip().lower() == "skip" else "allow"

    if chop:
        if policy == "skip":
            checks["kronos_decision"] = "skip"
            return False, f"Kronos skip {symbol}: predicted chop", checks
        checks["kronos_decision"] = "neutral"
        return True, f"Kronos {symbol}: chop — MACD {side} may fire", checks
    if pred != side:
        checks["kronos_decision"] = "skip"
        return False, f"Kronos skip {symbol}: forecast {pred} vs MACD {side}", checks

    checks["kronos_decision"] = "allow"
    return True, f"Kronos allow {symbol}: forecast {pred} agrees with MACD", checks


def symbol_status(
    symbol: str,
    *,
    enabled: bool = False,
    path: str | Path | None = None,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
    payload: dict[str, Any] | None = None,
    chop_policy: str = "allow",
) -> dict[str, Any]:
    """UI blob: what Kronos predicts and whether each side would pass the gate."""
    data = payload if payload is not None else load_decisions(path)
    blob = _symbol_blob(data, symbol) or {}
    pred = predicted_side(blob) if blob else "n/a"
    buy_ok, buy_why, buy_chk = kronos_allows(
        symbol,
        "BUY",
        enabled=enabled,
        path=path,
        max_age_sec=max_age_sec,
        payload=data,
        chop_policy=chop_policy,
    )
    sell_ok, sell_why, sell_chk = kronos_allows(
        symbol,
        "SELL",
        enabled=enabled,
        path=path,
        max_age_sec=max_age_sec,
        payload=data,
        chop_policy=chop_policy,
    )
    updated = data.get("updated_at_unix")
    try:
        age = int(time.time() - float(updated)) if updated is not None else None
    except (TypeError, ValueError):
        age = None
    return {
        "enabled": bool(enabled),
        "predicted": pred,
        "bps": blob.get("rel_change_bps"),
        "chop": bool(blob.get("chop")) if blob else None,
        "age_sec": age,
        "updated_at": data.get("updated_at"),
        "allow_buy": buy_ok,
        "allow_sell": sell_ok,
        "reason_buy": buy_why,
        "reason_sell": sell_why,
        "gate": buy_chk.get("kronos_gate") or sell_chk.get("kronos_gate") or ("on" if enabled else "off"),
    }


def summarize_decisions(path: str | Path | None = None) -> str:
    data = load_decisions(path)
    if not data:
        return "no kronos_decisions.json"
    lines = [f"updated={data.get('updated_at')} demo_only={data.get('demo_only', True)}"]
    symbols = data.get("symbols") or {}
    for sym, blob in symbols.items():
        if not isinstance(blob, dict):
            continue
        lines.append(
            f"{sym}: side={predicted_side(blob)} slope={blob.get('slope')} "
            f"bps={blob.get('rel_change_bps')} chop={blob.get('chop')}"
        )
    return "\n".join(lines)
