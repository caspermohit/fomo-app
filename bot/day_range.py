"""DEMO-ONLY prior-day range (CRT-style) strategy.

Uses the previous completed daily candle (PDH / PDL / mid) and M15 (or M5)
sweep-and-reclaim setups. Kronos does not apply to this mode — engine skips it
when STRATEGY=day_range.

v1 exits: SL beyond the sweep extreme (+1 pip buffer), TP at mid (TP1).
TP2 at the opposite extreme is deferred to a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from bot.analyst import (
    Analysis,
    atr_pips,
    closed_bars,
    exits_from_pullback,
    _bar_stamp,
    _session_ok,
)
from bot.risk import Action, Signal, pip_size, pip_value_per_lot

# Fraction of (PDH-PDL) around mid where we refuse entries (chop).
DEFAULT_MID_BAND_PCT = 0.05
STOP_BUFFER_PIPS = 1.0
# Look back this many closed intraday bars for a PDH/PDL sweep.
SWEEP_LOOKBACK = 12
MIN_INTRADAY = 3
MIN_DAILY = 2


@dataclass(frozen=True)
class PriorDayLevels:
    pdh: float
    pdl: float
    mid: float
    day_range: float
    stamp: str


def prior_day_levels(daily: pd.DataFrame, now=None) -> PriorDayLevels | None:
    """PDH/PDL/mid from the last *completed* daily candle."""
    if daily is None or daily.empty or not {"high", "low", "close"} <= set(daily.columns):
        return None
    frame = closed_bars(daily, now=now, tf_minutes=1440) if now is not None else daily
    if frame is None or len(frame) < 1:
        return None
    # Prefer the prior completed day: if we still have today's forming bar in the
    # raw series, closed_bars already dropped it when `now` is set. With only
    # completed bars, the last row is yesterday.
    row = frame.iloc[-1]
    pdh = float(row["high"])
    pdl = float(row["low"])
    if pdh <= pdl:
        return None
    mid = (pdh + pdl) / 2.0
    stamp = ""
    if "time" in frame.columns:
        ts = pd.Timestamp(frame["time"].iloc[-1])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        stamp = ts.tz_convert("UTC").strftime("%Y-%m-%d")
    return PriorDayLevels(pdh, pdl, mid, pdh - pdl, stamp)


def mid_band(levels: PriorDayLevels, band_pct: float = DEFAULT_MID_BAND_PCT) -> tuple[float, float]:
    half = float(band_pct) * float(levels.day_range)
    return levels.mid - half, levels.mid + half


def _inside_range(price: float, levels: PriorDayLevels) -> bool:
    return levels.pdl <= price <= levels.pdh


def find_sweep(
    bars: pd.DataFrame,
    levels: PriorDayLevels,
    *,
    lookback: int = SWEEP_LOOKBACK,
) -> tuple[Signal, dict[str, Any]]:
    """Detect a recent PDH/PDL liquidity sweep that closed back inside the range.

    SELL: bar high > PDH, close back inside [PDL, PDH], and current price above mid.
    BUY:  bar low  < PDL, close back inside, and current price below mid.
    Near-mid chop is decided by the caller.
    """
    detail: dict[str, Any] = {"sweep": "none", "sweep_extreme": None, "sweep_bar": ""}
    if bars is None or len(bars) < 2:
        return "HOLD", detail
    window = bars.tail(max(2, int(lookback)))
    price = float(window["close"].iloc[-1])
    if not _inside_range(price, levels):
        detail["sweep"] = "outside_range"
        return "HOLD", detail

    # Scan newest-first for the most recent reclaim bar.
    for i in range(len(window) - 1, -1, -1):
        high = float(window["high"].iloc[i])
        low = float(window["low"].iloc[i])
        close = float(window["close"].iloc[i])
        if not _inside_range(close, levels):
            continue
        stamp = ""
        if "time" in window.columns:
            ts = pd.Timestamp(window["time"].iloc[i])
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            stamp = ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

        if high > levels.pdh and price > levels.mid:
            detail.update(
                {
                    "sweep": "PDH",
                    "sweep_extreme": high,
                    "sweep_bar": stamp,
                    "side": "SELL",
                }
            )
            return "SELL", detail
        if low < levels.pdl and price < levels.mid:
            detail.update(
                {
                    "sweep": "PDL",
                    "sweep_extreme": low,
                    "sweep_bar": stamp,
                    "side": "BUY",
                }
            )
            return "BUY", detail
    return "HOLD", detail


def sl_tp_pips(
    symbol: str,
    side: Signal,
    entry: float,
    levels: PriorDayLevels,
    sweep_extreme: float,
    *,
    buffer_pips: float = STOP_BUFFER_PIPS,
) -> tuple[float, float]:
    """v1: SL beyond sweep extreme, TP at mid (TP1)."""
    pip = pip_size(symbol)
    buf = float(buffer_pips) * pip
    if side == "SELL":
        stop = max(float(sweep_extreme), levels.pdh) + buf
        sl = max((stop - entry) / pip, 1.0)
        tp = max((entry - levels.mid) / pip, 1.0)
    elif side == "BUY":
        stop = min(float(sweep_extreme), levels.pdl) - buf
        sl = max((entry - stop) / pip, 1.0)
        tp = max((levels.mid - entry) / pip, 1.0)
    else:
        return 20.0, 30.0
    return float(sl), float(tp)


def analyze_day_range(
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    symbol: str,
    *,
    position_side: Signal | None = None,
    position_profit: float = 0.0,
    peak_profit: float = 0.0,
    position_volume: float = 0.01,
    open_count: int = 0,
    session_start_hour: int = 0,
    session_end_hour: int = 24,
    session_start_minute: int = 0,
    session_end_minute: int = 0,
    now=None,
    spread_pips: float | None = None,
    max_spread_pips: float = 2.0,
    usd_jpy: float | None = None,
    pip_value: float | None = None,
    mid_band_pct: float = DEFAULT_MID_BAND_PCT,
    position_sl_pips: float | None = None,
    position_tp_pips: float | None = None,
    timeframe: str = "M15",
    **_ignored,
) -> Analysis:
    """CRT-style prior-day sweep/reclaim. Demo-only path used when STRATEGY=day_range."""
    del peak_profit, open_count  # reserved for engine parity / future filters
    tf_min = 15 if str(timeframe).upper() == "M15" else 5
    empty = Analysis(
        "HOLD",
        "HOLD",
        0,
        20,
        30,
        "unknown",
        50.0,
        20.0,
        ["day_range: not enough bars"],
        {"strategy": "day_range"},
    )
    pv = float(pip_value) if pip_value is not None else pip_value_per_lot(symbol, usd_jpy=usd_jpy)
    exit_kw = {"symbol": symbol, "pip_value": pv, "usd_jpy": usd_jpy}

    levels = prior_day_levels(daily, now=now)
    frame = closed_bars(intraday, now=now, tf_minutes=tf_min) if intraday is not None else None
    if levels is None or frame is None or len(frame) < MIN_INTRADAY:
        if position_side and position_sl_pips and position_tp_pips:
            rr = (position_tp_pips / position_sl_pips) if position_sl_pips else 1.0
            spec = exits_from_pullback(position_volume, position_sl_pips, rr, **exit_kw)
            pnl_tag = f"${position_profit:+.2f}"
            if position_profit >= spec.take_usd:
                return Analysis(
                    "CLOSE",
                    "HOLD",
                    0,
                    spec.sl_pips,
                    spec.tp_pips,
                    "unknown",
                    50.0,
                    20.0,
                    [f"take profit mid (TP1): open P&L {pnl_tag}"],
                    {"verdict": "bank", "strategy": "day_range"},
                )
            if position_profit <= -spec.cut_usd:
                return Analysis(
                    "CLOSE",
                    "HOLD",
                    0,
                    spec.sl_pips,
                    spec.tp_pips,
                    "unknown",
                    50.0,
                    20.0,
                    [f"stop loss sweep: open P&L {pnl_tag}"],
                    {"verdict": "cut", "strategy": "day_range"},
                )
            return Analysis(
                "HOLD",
                "HOLD",
                0,
                spec.sl_pips,
                spec.tp_pips,
                "unknown",
                50.0,
                20.0,
                [f"hold {position_side}: open P&L {pnl_tag} — TP1 mid vs sweep SL"],
                {"verdict": "hold", "strategy": "day_range"},
            )
        return empty

    price = float(frame["close"].iloc[-1])
    atr = atr_pips(frame, symbol)
    in_session = _session_ok(
        frame,
        session_start_hour,
        session_end_hour,
        now=now,
        start_minute=session_start_minute,
        end_minute=session_end_minute,
    )
    lo, hi = mid_band(levels, mid_band_pct)
    side, sweep = find_sweep(frame, levels)
    trend = "above_mid" if price > levels.mid else "below_mid" if price < levels.mid else "mid"

    sl, tp = 20.0, 30.0
    checks: dict[str, Any] = {
        "strategy": "day_range",
        "pdh": f"{levels.pdh:.5f}",
        "pdl": f"{levels.pdl:.5f}",
        "mid": f"{levels.mid:.5f}",
        "prior_day": levels.stamp or "n/a",
        "timeframe": timeframe,
        "session": "in window" if in_session else "outside window",
        "spread": "n/a" if spread_pips is None else f"{spread_pips:.1f} pips",
        "atr": f"{atr:.1f} pips",
        "price_vs_mid": trend,
        "mid_band": f"{lo:.5f}-{hi:.5f}",
        "sweep": sweep.get("sweep"),
        "sweep_bar": sweep.get("sweep_bar") or "",
        "cross_bar": sweep.get("sweep_bar") or _bar_stamp(frame),
        "tp": "mid (TP1)",
        "sl": "beyond sweep",
        "pip_value": f"{pv:.2f}",
        "inside_range": str(_inside_range(price, levels)).lower(),
    }
    reasons: list[str] = [
        f"prior day {levels.stamp or '?'}: PDH {levels.pdh:.5f} / mid {levels.mid:.5f} / PDL {levels.pdl:.5f}"
    ]

    if position_side:
        use_sl = float(position_sl_pips) if position_sl_pips else sl
        use_tp = float(position_tp_pips) if position_tp_pips else tp
        rr = (use_tp / use_sl) if use_sl else 1.0
        spec = exits_from_pullback(position_volume, use_sl, rr, **exit_kw)
        sl, tp = spec.sl_pips, spec.tp_pips
        pnl_tag = f"${position_profit:+.2f}"
        checks["open_pnl"] = pnl_tag
        checks["take_usd"] = f"{spec.take_usd:.2f}"
        checks["cut_usd"] = f"{spec.cut_usd:.2f}"
        checks["verdict"] = "hold"
        action: Action = "HOLD"
        if position_profit >= spec.take_usd:
            action = "CLOSE"
            checks["verdict"] = "bank"
            reasons.insert(0, f"take profit mid (TP1): open P&L {pnl_tag}")
        elif position_profit <= -spec.cut_usd:
            action = "CLOSE"
            checks["verdict"] = "cut"
            reasons.insert(0, f"stop loss sweep: open P&L {pnl_tag}")
        else:
            reasons.insert(0, f"hold {position_side}: open P&L {pnl_tag} — TP1 mid vs sweep SL")
        bias: Signal = position_side if position_side in {"BUY", "SELL"} else "HOLD"
        return Analysis(action, bias, 6, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if not in_session:
        reasons.append("outside the configured session window")
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if spread_pips is not None and spread_pips > max_spread_pips:
        reasons.append(f"spread {spread_pips:.1f} pips — skip bad-spread hours")
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if not _inside_range(price, levels):
        reasons.insert(0, "price outside prior day range — wait for reclaim")
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if lo <= price <= hi:
        reasons.insert(0, f"price near mid (±{mid_band_pct:.0%} of day range) — skip chop")
        checks["mid_gate"] = "chop"
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)
    checks["mid_gate"] = "ok"

    if side not in {"BUY", "SELL"}:
        reasons.insert(0, "waiting for PDH/PDL sweep then close back inside range")
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    # Side must match mid location (already enforced in find_sweep, re-check).
    if side == "SELL" and price <= levels.mid:
        reasons.insert(0, "SELL sweep but price not above mid")
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)
    if side == "BUY" and price >= levels.mid:
        reasons.insert(0, "BUY sweep but price not below mid")
        return Analysis("HOLD", "HOLD", 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    extreme = float(sweep.get("sweep_extreme") or (levels.pdh if side == "SELL" else levels.pdl))
    sl, tp = sl_tp_pips(symbol, side, price, levels, extreme)
    reasons.insert(
        0,
        f"open {side}: {sweep.get('sweep')} sweep reclaim inside prior day — TP1 mid, SL beyond sweep",
    )
    return Analysis(side, side, 8, sl, tp, trend, 50.0, atr, reasons[:8], checks)
