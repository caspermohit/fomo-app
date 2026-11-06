"""Unit tests for DEMO-ONLY day_range (prior-day PDH/PDL sweep) strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from bot.day_range import analyze_day_range, prior_day_levels, sl_tp_pips


def _daily(pdh: float, pdl: float, day: str = "2026-09-09") -> pd.DataFrame:
    mid = (pdh + pdl) / 2.0
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp(f"{day} 00:00:00", tz="UTC"),
                "open": mid,
                "high": pdh,
                "low": pdl,
                "close": mid,
            }
        ]
    )


def _m15_rows(ohlc: list[tuple[float, float, float, float]], start: str = "2026-09-10 10:00:00") -> pd.DataFrame:
    t0 = pd.Timestamp(start, tz="UTC")
    rows = []
    for i, (o, h, l, c) in enumerate(ohlc):
        rows.append(
            {
                "time": t0 + timedelta(minutes=15 * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )
    return pd.DataFrame(rows)


def _pad_above(n: int = 6) -> list[tuple[float, float, float, float]]:
    # Quiet bars above mid, inside range — no sweep yet.
    return [(1.0960, 1.0968, 1.0956, 1.0962)] * n


def _pad_below(n: int = 6) -> list[tuple[float, float, float, float]]:
    return [(1.0940, 1.0945, 1.0932, 1.0938)] * n


def test_prior_day_levels_mid():
    daily = _daily(1.1000, 1.0900)
    levels = prior_day_levels(daily)
    assert levels is not None
    assert abs(levels.mid - 1.0950) < 1e-9
    assert abs(levels.day_range - 0.0100) < 1e-9


def test_sweep_sell_after_pdh_reclaim():
    daily = _daily(1.1000, 1.0900)
    bars = _m15_rows(
        _pad_above(6)
        + [
            (1.0965, 1.1010, 1.0960, 1.0975),  # sweep PDH, close back inside, above mid
            (1.0975, 1.0985, 1.0968, 1.0970),
        ]
    )
    # Last bar starts 10:00 + 7*15 = 11:45; use now well after close so both stay.
    now = datetime(2026, 9, 10, 12, 5, tzinfo=timezone.utc)
    a = analyze_day_range(
        bars,
        daily,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=now,
        mid_band_pct=0.05,
    )
    assert a.action == "SELL", (a.action, a.reasons, a.checks)
    assert a.checks.get("strategy") == "day_range"
    assert a.checks.get("sweep") == "PDH"
    assert a.sl_pips > 0 and a.tp_pips > 0


def test_sweep_buy_after_pdl_reclaim():
    daily = _daily(1.1000, 1.0900)
    bars = _m15_rows(
        _pad_below(6)
        + [
            (1.0935, 1.0940, 1.0885, 1.0920),  # sweep PDL, close back inside, below mid
            (1.0920, 1.0928, 1.0915, 1.0922),
        ]
    )
    now = datetime(2026, 9, 10, 12, 5, tzinfo=timezone.utc)
    a = analyze_day_range(
        bars,
        daily,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=now,
        mid_band_pct=0.05,
    )
    assert a.action == "BUY", (a.action, a.reasons, a.checks)
    assert a.checks.get("sweep") == "PDL"


def test_mid_chop_skips():
    daily = _daily(1.1000, 1.0900)  # mid 1.0950; 5% band = ±0.0005 → 1.0945-1.0955
    bars = _m15_rows(
        [(1.0950, 1.0952, 1.0948, 1.0950)] * 6
        + [
            (1.0950, 1.1012, 1.0948, 1.0950),  # swept PDH but closed at mid
            (1.0950, 1.0952, 1.0949, 1.0951),  # still near mid
        ]
    )
    now = datetime(2026, 9, 10, 12, 5, tzinfo=timezone.utc)
    a = analyze_day_range(
        bars,
        daily,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=now,
        mid_band_pct=0.05,
    )
    assert a.action == "HOLD", (a.action, a.reasons, a.checks)
    assert "mid" in " ".join(a.reasons).lower() or a.checks.get("mid_gate") == "chop"


def test_outside_range_no_trade():
    daily = _daily(1.1000, 1.0900)
    bars = _m15_rows([(1.1010, 1.1020, 1.1005, 1.1015)] * 8)
    now = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    a = analyze_day_range(
        bars,
        daily,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=now,
    )
    assert a.action == "HOLD"


def test_sl_beyond_sweep_tp_at_mid():
    from bot.day_range import PriorDayLevels

    levels = PriorDayLevels(1.1000, 1.0900, 1.0950, 0.01, "2026-09-09")
    sl, tp = sl_tp_pips("EURUSD", "SELL", 1.0970, levels, 1.1010)
    assert abs(sl - 41.0) < 0.1
    assert abs(tp - 20.0) < 0.1
    sl_b, tp_b = sl_tp_pips("EURUSD", "BUY", 1.0930, levels, 1.0890)
    assert abs(sl_b - 41.0) < 0.1
    assert abs(tp_b - 20.0) < 0.1
