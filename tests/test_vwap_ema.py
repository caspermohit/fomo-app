"""Unit tests for DEMO-ONLY VWAP + EMA9 (Austin) strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from bot.analyst import REWARD_R, ema
from bot.config import parse_strategy
from bot.vwap_ema import MIN_BARS, analyze_vwap_ema, ema9_vwap_cross, session_vwap


def _m5(closes, start="2026-11-05 08:00:00", wick=0.0012, vol=1000.0) -> pd.DataFrame:
    t0 = pd.Timestamp(start, tz="UTC")
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi = max(prev, c) + wick
        lo = min(prev, c) - wick
        rows.append(
            {
                "time": t0 + timedelta(minutes=5 * i),
                "open": prev,
                "high": hi,
                "low": lo,
                "close": c,
                "tick_volume": vol,
            }
        )
        prev = c
    return pd.DataFrame(rows)


def _now_after(bars) -> datetime:
    last = pd.Timestamp(bars["time"].iloc[-1])
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    return (last + timedelta(minutes=6)).to_pydatetime()


def test_parse_strategy_aliases():
    assert parse_strategy("macd") == "macd"
    assert parse_strategy("day_range") == "day_range"
    assert parse_strategy("vwap_ema9") == "vwap_ema"
    assert parse_strategy("austin") == "vwap_ema"
    assert parse_strategy("VWAP") == "vwap_ema"


def test_session_vwap_equal_weight():
    t0 = pd.Timestamp("2026-11-05 08:00:00", tz="UTC")
    rows = []
    for i, c in enumerate([1.0, 2.0, 3.0]):
        rows.append(
            {
                "time": t0 + timedelta(minutes=5 * i),
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "tick_volume": 10.0,
            }
        )
    vwap = session_vwap(pd.DataFrame(rows), session_start_hour=0)
    assert abs(float(vwap.iloc[-1]) - 2.0) < 1e-9


def test_ema9_vwap_cross_detects_both_sides():
    ema9 = pd.Series([1.0, 1.1])
    vwap = pd.Series([1.05, 1.05])
    assert ema9_vwap_cross(ema9, vwap) == "BUY"
    assert ema9_vwap_cross(pd.Series([1.1, 1.0]), vwap) == "SELL"
    assert ema9_vwap_cross(pd.Series([1.1, 1.2]), vwap) == "HOLD"


def _buy_closes():
    return [1.1000] * 40 + [1.0920] * 16 + [1.0935, 1.0960, 1.0990, 1.1030, 1.1080, 1.1120]


def _sell_closes():
    return [1.1000] * 40 + [1.1080] * 16 + [1.1065, 1.1040, 1.1010, 1.0970, 1.0920, 1.0880]


def _bars_on_cross(closes, want: str) -> pd.DataFrame:
    full = _m5(closes)
    for n in range(MIN_BARS, len(full) + 1):
        chunk = full.iloc[:n].copy()
        vwap = session_vwap(chunk, 0, 0)
        line = ema(chunk["close"].astype(float), 9)
        if ema9_vwap_cross(line, vwap) == want:
            return chunk
    raise AssertionError(f"no {want} EMA9/VWAP cross in fixture")


def test_buy_on_ema9_cross_above_vwap():
    bars = _bars_on_cross(_buy_closes(), "BUY")
    now = _now_after(bars)
    frame_vwap = session_vwap(bars, 0, 0)
    frame_ema = ema(bars["close"].astype(float), 9)
    assert ema9_vwap_cross(frame_ema, frame_vwap) == "BUY"
    a = analyze_vwap_ema(
        bars,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=now,
        min_atr_pips=0.1,
        atr_gate=False,
    )
    assert a.action == "BUY", (a.action, a.reasons, a.checks)
    assert a.checks.get("strategy") == "vwap_ema"
    assert a.checks.get("cross") == "BUY"
    assert abs(a.tp_pips / a.sl_pips - REWARD_R) < 1e-9


def test_sell_on_ema9_cross_below_vwap():
    bars = _bars_on_cross(_sell_closes(), "SELL")
    now = _now_after(bars)
    a = analyze_vwap_ema(
        bars,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=now,
        min_atr_pips=0.1,
        atr_gate=False,
    )
    assert a.action == "SELL", (a.action, a.reasons, a.checks)
    assert a.checks.get("cross") == "SELL"


def test_no_cross_is_hold():
    bars = _m5([1.1000 + i * 0.00001 for i in range(50)])
    a = analyze_vwap_ema(
        bars,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=_now_after(bars),
        min_atr_pips=0.1,
        atr_gate=False,
    )
    assert a.action == "HOLD"
    assert "waiting for EMA9" in " ".join(a.reasons)


def test_open_ticket_banks_at_two_r():
    bars = _m5(_buy_closes())
    held = analyze_vwap_ema(
        bars,
        "EURUSD",
        position_side="BUY",
        position_profit=10.0,
        position_volume=0.10,
        position_sl_pips=10.0,
        position_tp_pips=20.0,
        session_start_hour=0,
        session_end_hour=24,
        now=_now_after(bars),
        atr_gate=False,
    )
    assert held.action == "HOLD"
    assert held.checks["take_usd"] == "20.00"
    banked = analyze_vwap_ema(
        bars,
        "EURUSD",
        position_side="BUY",
        position_profit=21.0,
        position_volume=0.10,
        position_sl_pips=10.0,
        position_tp_pips=20.0,
        session_start_hour=0,
        session_end_hour=24,
        now=_now_after(bars),
        atr_gate=False,
    )
    assert banked.action == "CLOSE"
    assert "take profit" in banked.reasons[0].lower()
