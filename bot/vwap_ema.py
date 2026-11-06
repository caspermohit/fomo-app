"""DEMO-ONLY Austin McConnell path: session VWAP + EMA9.

VWAP = session direction and the invalidation area.
EMA9 = momentum / timing. Entry is a fresh EMA9 cross of VWAP on the closed
M5 bar, with price on the same side of VWAP as the cross.

Kronos does not apply. Kotegawa RM still owns size / 2R / daily halt.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from bot.analyst import (
    REWARD_R,
    Analysis,
    atr_pips,
    closed_bars,
    ema,
    exits_from_pullback,
    min_atr_floor,
    pullback_stop_pips,
    skip_dead_tape,
    _bar_stamp,
    _session_ok,
)
from bot.risk import Action, Signal, pip_value_per_lot

EMA_SPAN = 9
MIN_BARS = 30


def session_vwap(
    bars: pd.DataFrame,
    session_start_hour: int = 7,
    session_start_minute: int = 0,
) -> pd.Series:
    """Volume-weighted typical price, reset at the desk session open (UTC).

    Missing/zero volume falls back to equal weight so FX tick bars still work.
    """
    if bars is None or bars.empty or not {"high", "low", "close"} <= set(bars.columns):
        return pd.Series(dtype=float)
    typical = (
        bars["high"].astype(float) + bars["low"].astype(float) + bars["close"].astype(float)
    ) / 3.0
    if "tick_volume" in bars.columns:
        vol = bars["tick_volume"].astype(float)
    elif "volume" in bars.columns:
        vol = bars["volume"].astype(float)
    else:
        vol = pd.Series(1.0, index=bars.index)
    vol = vol.clip(lower=0.0)
    if float(vol.sum()) <= 0:
        vol = pd.Series(1.0, index=bars.index)

    if "time" in bars.columns:
        times = pd.to_datetime(bars["time"], utc=True)
        minutes = times.dt.hour * 60 + times.dt.minute
        start_m = int(session_start_hour) * 60 + int(session_start_minute)
        day = times.dt.floor("D")
        session_key = day.where(minutes >= start_m, day - pd.Timedelta(days=1))
    else:
        session_key = pd.Series(0, index=bars.index)

    cum_pv = (typical * vol).groupby(session_key).cumsum()
    cum_vol = vol.groupby(session_key).cumsum().replace(0, pd.NA)
    return (cum_pv / cum_vol).astype(float)


def ema9_vwap_cross(ema9: pd.Series, vwap: pd.Series) -> Signal:
    """Fresh EMA9 cross of VWAP on the last two closed bars."""
    if ema9 is None or vwap is None or len(ema9) < 2 or len(vwap) < 2:
        return "HOLD"
    prev_e, last_e = float(ema9.iloc[-2]), float(ema9.iloc[-1])
    prev_v, last_v = float(vwap.iloc[-2]), float(vwap.iloc[-1])
    if prev_e <= prev_v and last_e > last_v:
        return "BUY"
    if prev_e >= prev_v and last_e < last_v:
        return "SELL"
    return "HOLD"


def analyze_vwap_ema(
    bars: pd.DataFrame,
    symbol: str,
    *,
    position_side: Signal | None = None,
    position_profit: float = 0.0,
    peak_profit: float = 0.0,
    position_volume: float = 0.01,
    open_count: int = 0,
    session_start_hour: int = 7,
    session_end_hour: int = 20,
    session_start_minute: int = 0,
    session_end_minute: int = 20,
    now=None,
    spread_pips: float | None = None,
    max_spread_pips: float = 2.0,
    usd_jpy: float | None = None,
    pip_value: float | None = None,
    min_atr_pips: float = 1.0,
    position_sl_pips: float | None = None,
    position_tp_pips: float | None = None,
    atr_gate: bool = True,
    **_ignored,
) -> Analysis:
    """VWAP bias + EMA9 timing. Demo-only path used when STRATEGY=vwap_ema."""
    del peak_profit, open_count
    empty = Analysis(
        "HOLD",
        "HOLD",
        0,
        20,
        40,
        "unknown",
        50.0,
        20.0,
        ["vwap_ema: not enough bars"],
        {"strategy": "vwap_ema"},
    )
    pv = float(pip_value) if pip_value is not None else pip_value_per_lot(symbol, usd_jpy=usd_jpy)
    exit_kw = {"symbol": symbol, "pip_value": pv, "usd_jpy": usd_jpy}
    frame = closed_bars(bars, now=now) if bars is not None else bars
    if frame is None or len(frame) < MIN_BARS or not {"open", "high", "low", "close"} <= set(frame.columns):
        if position_side and position_sl_pips and position_tp_pips:
            rr = (position_tp_pips / position_sl_pips) if position_sl_pips else REWARD_R
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
                    [f"take profit {REWARD_R:g}R: open P&L {pnl_tag}"],
                    {"verdict": "bank", "strategy": "vwap_ema"},
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
                    [f"stop loss VWAP/pullback: open P&L {pnl_tag}"],
                    {"verdict": "cut", "strategy": "vwap_ema"},
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
                [f"hold {position_side}: open P&L {pnl_tag} — {REWARD_R:g}R vs VWAP SL"],
                {"verdict": "hold", "strategy": "vwap_ema"},
            )
        return empty

    close = frame["close"].astype(float)
    vwap = session_vwap(frame, session_start_hour, session_start_minute)
    line = ema(close, EMA_SPAN)
    price = float(close.iloc[-1])
    vwap_now = float(vwap.iloc[-1])
    ema_now = float(line.iloc[-1])
    cross = ema9_vwap_cross(line, vwap)
    atr = atr_pips(frame, symbol)
    in_session = _session_ok(
        frame,
        session_start_hour,
        session_end_hour,
        now=now,
        start_minute=session_start_minute,
        end_minute=session_end_minute,
    )
    bias: Signal = "BUY" if price > vwap_now else "SELL" if price < vwap_now else "HOLD"
    trend = "above_vwap" if bias == "BUY" else "below_vwap" if bias == "SELL" else "at_vwap"
    need_atr = min_atr_floor(symbol, min_atr_pips)

    sl, tp = 20.0, 40.0
    if bias in {"BUY", "SELL"}:
        spec_pb = exits_from_pullback(
            position_volume,
            pullback_stop_pips(frame, symbol, bias, price, vwap_now),
            **exit_kw,
        )
        sl, tp = spec_pb.sl_pips, spec_pb.tp_pips

    checks: dict[str, Any] = {
        "strategy": "vwap_ema",
        "vwap": f"{vwap_now:.5f}",
        "ema9": f"{ema_now:.5f}",
        "price_vs_vwap": "above" if price > vwap_now else "below" if price < vwap_now else "flat",
        "ema9_vs_vwap": "above" if ema_now > vwap_now else "below" if ema_now < vwap_now else "flat",
        "cross": cross,
        "cross_bar": _bar_stamp(frame),
        "session": "in window" if in_session else "outside window",
        "spread": "n/a" if spread_pips is None else f"{spread_pips:.1f} pips",
        "atr": f"{atr:.1f} pips",
        "atr_floor": f"{need_atr:.1f}",
        "tp": f"{REWARD_R:g}R",
        "sl": "pullback / VWAP",
        "pip_value": f"{pv:.2f}",
    }
    reasons: list[str] = [
        f"VWAP {vwap_now:.5f} · EMA9 {ema_now:.5f} · price {price:.5f}",
    ]

    if position_side:
        use_sl = float(position_sl_pips) if position_sl_pips else sl
        use_tp = float(position_tp_pips) if position_tp_pips else tp
        spec = exits_from_pullback(
            position_volume,
            use_sl,
            use_tp / use_sl if use_sl else REWARD_R,
            **exit_kw,
        )
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
            reasons.insert(0, f"take profit {REWARD_R:g}R: open P&L {pnl_tag}")
        elif position_profit <= -spec.cut_usd:
            action = "CLOSE"
            checks["verdict"] = "cut"
            reasons.insert(0, f"stop loss VWAP/pullback: open P&L {pnl_tag}")
        else:
            reasons.insert(0, f"hold {position_side}: open P&L {pnl_tag} — {REWARD_R:g}R vs VWAP SL")
        hold_bias: Signal = position_side if position_side in {"BUY", "SELL"} else "HOLD"
        return Analysis(action, hold_bias, 6, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if not in_session:
        reasons.append("outside the configured session window")
        return Analysis("HOLD", bias, 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if spread_pips is not None and spread_pips > max_spread_pips:
        reasons.append(f"spread {spread_pips:.1f} pips — skip bad-spread hours")
        return Analysis("HOLD", bias, 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    tape = skip_dead_tape("BUY" if bias in {"BUY", "SELL"} else "HOLD", atr, need_atr, atr_gate)
    if tape:
        reasons.insert(0, tape)
        return Analysis("HOLD", bias, 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if cross not in {"BUY", "SELL"}:
        reasons.insert(0, "waiting for EMA9 to cross VWAP on the closed bar")
        return Analysis("HOLD", bias, 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    if cross == "BUY" and price <= vwap_now:
        reasons.insert(0, "EMA9 crossed up but price is not above VWAP")
        return Analysis("HOLD", bias, 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)
    if cross == "SELL" and price >= vwap_now:
        reasons.insert(0, "EMA9 crossed down but price is not below VWAP")
        return Analysis("HOLD", bias, 0, sl, tp, trend, 50.0, atr, reasons[:8], checks)

    spec = exits_from_pullback(
        position_volume,
        pullback_stop_pips(frame, symbol, cross, price, vwap_now),
        **exit_kw,
    )
    sl, tp = spec.sl_pips, spec.tp_pips
    reasons.insert(
        0,
        f"open {cross}: EMA9 crossed VWAP with price {checks['price_vs_vwap']} ({REWARD_R:g}R vs pullback/VWAP SL)",
    )
    return Analysis(cross, cross, 8, sl, tp, trend, 50.0, atr, reasons[:8], checks)
