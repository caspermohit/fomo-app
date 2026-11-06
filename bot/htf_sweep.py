"""DEMO-ONLY H1 → M15 → M5 liquidity-sweep + order-block report.

Jaime Avalos process (not MACD, not D1 CRT):
1. H1 context — swing high/low, wait for a liquidity sweep, reclaim.
2. M15 confirmation — after the H1 sweep, same-direction swing reaction.
3. M5 precision — wait for the M5 sweep, then the last opposite candle
   (order block) as the execute zone. M5 never decides the trade alone.

A BUY/SELL signal is emitted only when all three stages agree.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from bot.analyst import (
    REWARD_R,
    Analysis,
    atr_pips,
    closed_bars,
    exits_from_pullback,
    rsi,
    _bar_stamp,
    _session_ok,
)
from bot.risk import Action, Signal, pip_size, pip_value_per_lot

SWING_N = 2
H1_LOOKBACK = 16
M15_LOOKBACK = 16
M5_LOOKBACK = 24
STOP_BUFFER_PIPS = 1.0
OB_LOOKBACK = 12
MIN_H1 = 12
MIN_M15 = 10
MIN_M5 = 12


def _stamp_at(frame: pd.DataFrame, i: int) -> str:
    if frame is None or frame.empty or "time" not in frame.columns:
        return ""
    if i < 0 or i >= len(frame):
        return ""
    ts = pd.Timestamp(frame["time"].iloc[i])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_at(frame: pd.DataFrame, i: int):
    if frame is None or frame.empty or "time" not in frame.columns:
        return None
    if i < 0 or i >= len(frame):
        return None
    ts = pd.Timestamp(frame["time"].iloc[i])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def last_fractal(
    frame: pd.DataFrame,
    kind: str,
    n: int = SWING_N,
    before: int | None = None,
) -> tuple[int, float] | None:
    """Last confirmed n-bar fractal strictly before `before` (exclusive)."""
    if frame is None or len(frame) < n * 2 + 1:
        return None
    end = len(frame) - n
    if before is not None:
        end = min(end, int(before))
    start = n
    if end <= start:
        return None
    series = frame["high" if kind == "high" else "low"].astype(float)
    for i in range(end - 1, start - 1, -1):
        v = float(series.iloc[i])
        left = series.iloc[i - n : i]
        right = series.iloc[i + 1 : i + n + 1]
        if left.empty or right.empty:
            continue
        if kind == "high" and v > float(left.max()) and v > float(right.max()):
            return i, v
        if kind == "low" and v < float(left.min()) and v < float(right.min()):
            return i, v
    return None


def find_tf_sweep(
    frame: pd.DataFrame,
    *,
    lookback: int,
    after_ts=None,
) -> tuple[Signal, dict[str, Any]]:
    """Newest swing-high/low liquidity sweep that closed back across the level."""
    empty = {
        "sweep": "none",
        "side": "HOLD",
        "level": None,
        "sweep_i": None,
        "sweep_bar": "",
        "sweep_extreme": None,
        "fractal_i": None,
    }
    if frame is None or len(frame) < SWING_N * 2 + 4:
        return "HOLD", empty
    last_i = len(frame) - 1
    start = max(SWING_N + 1, last_i - int(lookback) + 1)
    price = float(frame["close"].iloc[-1])
    after = pd.Timestamp(after_ts).tz_convert("UTC") if after_ts is not None else None

    for i in range(last_i, start - 1, -1):
        bar_ts = _ts_at(frame, i)
        if after is not None and bar_ts is not None and bar_ts < after:
            continue
        high = float(frame["high"].iloc[i])
        low = float(frame["low"].iloc[i])
        close = float(frame["close"].iloc[i])
        fr_h = last_fractal(frame, "high", before=i)
        fr_l = last_fractal(frame, "low", before=i)
        if fr_h:
            fi, level = fr_h
            if high > level and close < level and price < level:
                return "SELL", {
                    "sweep": "swing_high",
                    "side": "SELL",
                    "level": level,
                    "sweep_i": i,
                    "sweep_bar": _stamp_at(frame, i),
                    "sweep_extreme": high,
                    "fractal_i": fi,
                    "fractal_bar": _stamp_at(frame, fi),
                }
        if fr_l:
            fi, level = fr_l
            if low < level and close > level and price > level:
                return "BUY", {
                    "sweep": "swing_low",
                    "side": "BUY",
                    "level": level,
                    "sweep_i": i,
                    "sweep_bar": _stamp_at(frame, i),
                    "sweep_extreme": low,
                    "fractal_i": fi,
                    "fractal_bar": _stamp_at(frame, fi),
                }
    return "HOLD", empty


def find_order_block(
    frame: pd.DataFrame,
    side: Signal,
    sweep_i: int,
) -> dict[str, Any] | None:
    """Last opposite-colored candle before the sweep impulse (ICT order block)."""
    if frame is None or sweep_i is None or sweep_i < 1:
        return None
    lo = max(0, int(sweep_i) - OB_LOOKBACK)
    for j in range(int(sweep_i) - 1, lo - 1, -1):
        o = float(frame["open"].iloc[j])
        c = float(frame["close"].iloc[j])
        h = float(frame["high"].iloc[j])
        l = float(frame["low"].iloc[j])
        bull = c > o
        if side == "SELL" and bull:
            return {"i": j, "low": l, "high": h, "bar": _stamp_at(frame, j)}
        if side == "BUY" and not bull:
            return {"i": j, "low": l, "high": h, "bar": _stamp_at(frame, j)}
    return None


def _in_zone(price: float, low: float, high: float, pad: float) -> bool:
    return (low - pad) <= price <= (high + pad)


def _stage(state: str, label: str, **extra) -> dict[str, Any]:
    row = {"state": state, "label": label}
    row.update(extra)
    return row


def empty_report(why: str = "waiting for H1 / M15 / M5 bars") -> dict[str, Any]:
    return {
        "ready": False,
        "action": "HOLD",
        "bias": "HOLD",
        "signal": "wait",
        "h1": _stage("wait", "H1 — map swing high/low, wait for a liquidity sweep"),
        "m15": _stage("wait", "M15 — confirmation after H1, not the entry"),
        "m5": _stage("wait", "M5 — timing only; does not decide the trade"),
        "why": why,
        "sl_pips": 20.0,
        "tp_pips": 30.0,
        "cross_bar": "",
    }


def desk_report(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """JSON-safe copy for the desk snapshot (no numpy / extra sweep fields)."""
    src = report or empty_report()

    def stage(key: str) -> dict[str, str]:
        row = src.get(key) or {}
        return {
            "state": str(row.get("state") or "wait"),
            "label": str(row.get("label") or ""),
        }

    action = str(src.get("action") or "HOLD")
    bias = str(src.get("bias") or "HOLD")
    return {
        "ready": bool(src.get("ready")),
        "action": action,
        "bias": bias,
        "signal": str(src.get("signal") or "wait"),
        "h1": stage("h1"),
        "m15": stage("m15"),
        "m5": stage("m5"),
        "why": str(src.get("why") or ""),
        "sl_pips": float(src.get("sl_pips") or 20.0),
        "tp_pips": float(src.get("tp_pips") or 30.0),
        "cross_bar": str(src.get("cross_bar") or ""),
    }


def evaluate_htf(
    h1: pd.DataFrame | None,
    m15: pd.DataFrame | None,
    m5: pd.DataFrame | None,
    symbol: str,
    *,
    now=None,
) -> dict[str, Any]:
    """Build the three-stage report. ready=True only when H1+M15+M5 agree."""
    report = empty_report()
    h1f = closed_bars(h1, now=now, tf_minutes=60) if h1 is not None else None
    m15f = closed_bars(m15, now=now, tf_minutes=15) if m15 is not None else None
    m5f = closed_bars(m5, now=now, tf_minutes=5) if m5 is not None else None

    if h1f is None or len(h1f) < MIN_H1:
        report["why"] = "H1 — not enough closed bars for a swing sweep"
        return report

    h1_side, h1_sw = find_tf_sweep(h1f, lookback=H1_LOOKBACK)
    if h1_side not in {"BUY", "SELL"}:
        report["h1"] = _stage("wait", "H1 — no swing liquidity sweep yet")
        report["why"] = "H1 has not swept a swing. No trade from M15 or M5."
        return report

    level = h1_sw.get("level")
    lvl = f"{float(level):.5f}" if level is not None else "—"
    report["bias"] = h1_side
    report["h1"] = _stage(
        "sweep",
        f"H1 {h1_side} — swept {'swing high' if h1_side == 'SELL' else 'swing low'} {lvl}, closed back",
        side=h1_side,
        level=level,
        bar=h1_sw.get("sweep_bar") or "",
    )

    h1_ts = None
    if h1_sw.get("sweep_i") is not None:
        h1_ts = _ts_at(h1f, int(h1_sw["sweep_i"]))

    if m15f is None or len(m15f) < MIN_M15:
        report["m15"] = _stage("wait", "M15 — waiting for bars after the H1 sweep")
        report["why"] = f"H1 {h1_side} sweep. M15 has not confirmed."
        return report

    m15_side, m15_sw = find_tf_sweep(m15f, lookback=M15_LOOKBACK, after_ts=h1_ts)
    if m15_side != h1_side:
        report["m15"] = _stage("wait", f"M15 — waiting for {h1_side} confirmation after H1")
        report["why"] = f"H1 {h1_side} sweep. M15 has not confirmed that idea."
        return report

    report["m15"] = _stage(
        "ok",
        f"M15 confirmed {h1_side} — same-direction sweep after H1",
        side=m15_side,
        bar=m15_sw.get("sweep_bar") or "",
        level=m15_sw.get("level"),
    )
    m15_ts = None
    if m15_sw.get("sweep_i") is not None:
        m15_ts = _ts_at(m15f, int(m15_sw["sweep_i"]))

    if m5f is None or len(m5f) < MIN_M5:
        report["m5"] = _stage("wait", "M5 — waiting for sweep + order block (timing only)")
        report["why"] = f"H1 {h1_side} + M15 confirmed. M5 has not given the execute bar."
        return report

    m5_side, m5_sw = find_tf_sweep(m5f, lookback=M5_LOOKBACK, after_ts=m15_ts or h1_ts)
    if m5_side != h1_side:
        report["m5"] = _stage("wait", f"M5 — wait for the {h1_side} sweep; M5 does not decide")
        report["why"] = f"H1+M15 are {h1_side}. M5 has not swept yet."
        return report

    sweep_i = m5_sw.get("sweep_i")
    ob = find_order_block(m5f, h1_side, int(sweep_i) if sweep_i is not None else -1)
    if not ob:
        report["m5"] = _stage("wait", "M5 swept, but no opposite candle (order block) before the impulse")
        report["why"] = f"H1+M15 {h1_side}. M5 sweep has no order block yet."
        return report

    price = float(m5f["close"].iloc[-1])
    last_high = float(m5f["high"].iloc[-1])
    last_low = float(m5f["low"].iloc[-1])
    pad = pip_size(symbol)
    touched = _in_zone(price, ob["low"], ob["high"], pad) or (
        last_low <= ob["high"] + pad and last_high >= ob["low"] - pad
    )
    if not touched:
        report["m5"] = _stage(
            "wait",
            "M5 swept — wait for price back into the order block",
            ob_low=ob["low"],
            ob_high=ob["high"],
            bar=m5_sw.get("sweep_bar") or "",
        )
        report["why"] = f"H1+M15 {h1_side}. M5 swept; waiting for order-block mitigation."
        return report

    extreme = m5_sw.get("sweep_extreme")
    pip = pip_size(symbol)
    buf = STOP_BUFFER_PIPS * pip
    if h1_side == "SELL" and extreme is not None:
        sl = max((float(extreme) + buf - price) / pip, 1.0)
    elif h1_side == "BUY" and extreme is not None:
        sl = max((price - (float(extreme) - buf)) / pip, 1.0)
    else:
        sl = 20.0
    tp = sl * REWARD_R
    report["ready"] = True
    report["action"] = h1_side
    report["signal"] = h1_side.lower()
    report["sl_pips"] = float(sl)
    report["tp_pips"] = float(tp)
    report["cross_bar"] = str(m5_sw.get("sweep_bar") or _bar_stamp(m5f))
    report["m5"] = _stage(
        "ok",
        f"M5 {h1_side} — sweep + order block. Execute.",
        side=h1_side,
        bar=m5_sw.get("sweep_bar") or "",
        ob_low=ob["low"],
        ob_high=ob["high"],
        sweep_extreme=extreme,
    )
    report["why"] = (
        f"H1 {h1_side} sweep → M15 confirm → M5 order block. "
        f"SL {sl:.1f} / TP {tp:.1f} pips."
    )
    return report


def analyze_htf_sweep(
    m5: pd.DataFrame,
    h1: pd.DataFrame | None,
    m15: pd.DataFrame | None,
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
    position_sl_pips: float | None = None,
    position_tp_pips: float | None = None,
    **_ignored,
) -> Analysis:
    """Engine analyst for STRATEGY=htf_sweep and HTF_REPORT entries."""
    del peak_profit, open_count
    pv = float(pip_value) if pip_value is not None else pip_value_per_lot(symbol, usd_jpy=usd_jpy)
    exit_kw = {"symbol": symbol, "pip_value": pv, "usd_jpy": usd_jpy}
    m5f = closed_bars(m5, now=now, tf_minutes=5) if m5 is not None else None
    atr = atr_pips(m5f, symbol) if m5f is not None and len(m5f) >= 15 else 20.0
    rsi_now = 50.0
    if m5f is not None and len(m5f) >= 16:
        rsi_now = float(rsi(m5f["close"].astype(float)).iloc[-1] or 50.0)
    in_session = _session_ok(
        m5f if m5f is not None else m5,
        session_start_hour,
        session_end_hour,
        now=now,
        start_minute=session_start_minute,
        end_minute=session_end_minute,
    )
    report = evaluate_htf(h1, m15, m5, symbol, now=now)
    sl = float(report.get("sl_pips") or 20.0)
    tp = float(report.get("tp_pips") or 30.0)
    trend = "htf_" + str(report.get("bias") or "hold").lower()
    checks: dict[str, Any] = {
        "strategy": "htf_sweep",
        "report_ready": str(bool(report.get("ready"))).lower(),
        "h1": str((report.get("h1") or {}).get("state") or "wait"),
        "m15": str((report.get("m15") or {}).get("state") or "wait"),
        "m5": str((report.get("m5") or {}).get("state") or "wait"),
        "session": "in window" if in_session else "outside window",
        "spread": "n/a" if spread_pips is None else f"{spread_pips:.1f} pips",
        "atr": f"{atr:.1f} pips",
        "cross_bar": str(report.get("cross_bar") or ""),
        "pip_value": f"{pv:.2f}",
    }

    if position_side:
        use_sl = float(position_sl_pips) if position_sl_pips else sl
        use_tp = float(position_tp_pips) if position_tp_pips else tp
        rr = (use_tp / use_sl) if use_sl else REWARD_R
        spec = exits_from_pullback(position_volume, use_sl, rr, **exit_kw)
        sl, tp = spec.sl_pips, spec.tp_pips
        pnl_tag = f"${position_profit:+.2f}"
        checks["open_pnl"] = pnl_tag
        checks["verdict"] = "hold"
        action: Action = "HOLD"
        reasons = [f"hold {position_side}: open P&L {pnl_tag} — HTF sweep SL / {REWARD_R:g}R"]
        if position_profit >= spec.take_usd:
            action = "CLOSE"
            checks["verdict"] = "bank"
            reasons = [f"take profit {REWARD_R:g}R: open P&L {pnl_tag}"]
        elif position_profit <= -spec.cut_usd:
            action = "CLOSE"
            checks["verdict"] = "cut"
            reasons = [f"stop loss sweep: open P&L {pnl_tag}"]
        return Analysis(action, "HOLD", 0, sl, tp, trend, rsi_now, atr, reasons, checks)

    reasons = [str(report.get("why") or "H1 → M15 → M5 report")]
    if not in_session:
        reasons.insert(0, "outside session — no new HTF tickets")
        return Analysis("HOLD", report.get("bias") or "HOLD", 0, sl, tp, trend, rsi_now, atr, reasons, checks)
    if spread_pips is not None and float(spread_pips) > float(max_spread_pips):
        reasons.insert(0, f"spread {spread_pips:.1f} above cap {max_spread_pips:.1f}")
        return Analysis("HOLD", report.get("bias") or "HOLD", 0, sl, tp, trend, rsi_now, atr, reasons, checks)

    if report.get("ready") and report.get("action") in {"BUY", "SELL"}:
        side: Signal = report["action"]
        spec = exits_from_pullback(0.01, sl, REWARD_R, **exit_kw)
        conf = 7
        checks["cross_bar"] = str(report.get("cross_bar") or "")
        return Analysis(
            side,
            side,
            conf,
            spec.sl_pips,
            spec.tp_pips,
            trend,
            rsi_now,
            atr,
            reasons,
            checks,
        )
    return Analysis(
        "HOLD",
        report.get("bias") or "HOLD",
        3 if report.get("bias") in {"BUY", "SELL"} else 0,
        sl,
        tp,
        trend,
        rsi_now,
        atr,
        reasons,
        checks,
    )
