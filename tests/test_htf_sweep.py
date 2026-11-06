"""Unit tests for DEMO-ONLY H1 → M15 → M5 sweep + order-block report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from bot.htf_sweep import analyze_htf_sweep, evaluate_htf, find_tf_sweep


def _frame(ohlc: list[tuple[float, float, float, float]], start: str, minutes: int) -> pd.DataFrame:
    t0 = pd.Timestamp(start, tz="UTC")
    rows = []
    for i, (o, h, l, c) in enumerate(ohlc):
        rows.append(
            {
                "time": t0 + timedelta(minutes=minutes * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )
    return pd.DataFrame(rows)


def _sell_sweep_ohlc() -> list[tuple[float, float, float, float]]:
    # Fractal high at index 4 (1.1000), sweep+reclaim at index 8.
    return [
        (1.0898, 1.0905, 1.0890, 1.0902),
        (1.0902, 1.0910, 1.0896, 1.0906),
        (1.0906, 1.0915, 1.0900, 1.0910),
        (1.0910, 1.0912, 1.0902, 1.0908),
        (1.0908, 1.1000, 1.0905, 1.0930),  # swing high
        (1.0930, 1.0935, 1.0908, 1.0912),
        (1.0912, 1.0918, 1.0900, 1.0906),
        (1.0906, 1.0908, 1.0898, 1.0902),
        (1.0902, 1.1012, 1.0895, 1.0960),  # sweep high, close back
        (1.0960, 1.0970, 1.0948, 1.0955),
        (1.0955, 1.0965, 1.0940, 1.0950),
        (1.0950, 1.0960, 1.0938, 1.0948),
        (1.0948, 1.0955, 1.0935, 1.0945),
        (1.0945, 1.0950, 1.0932, 1.0942),
    ]


def _buy_sweep_ohlc() -> list[tuple[float, float, float, float]]:
    return [
        (1.1102, 1.1110, 1.1095, 1.1098),
        (1.1098, 1.1104, 1.1090, 1.1094),
        (1.1094, 1.1100, 1.1085, 1.1090),
        (1.1090, 1.1098, 1.1088, 1.1092),
        (1.1092, 1.1095, 1.1000, 1.1070),  # swing low
        (1.1070, 1.1088, 1.1068, 1.1080),
        (1.1080, 1.1090, 1.1075, 1.1084),
        (1.1084, 1.1092, 1.1078, 1.1086),
        (1.1086, 1.1105, 1.0988, 1.1040),  # sweep low, close back
        (1.1040, 1.1055, 1.1032, 1.1048),
        (1.1048, 1.1060, 1.1038, 1.1052),
        (1.1052, 1.1065, 1.1042, 1.1055),
        (1.1055, 1.1068, 1.1048, 1.1058),
        (1.1058, 1.1070, 1.1050, 1.1062),
    ]


def _m5_sell_with_ob() -> list[tuple[float, float, float, float]]:
    # Same H1-style sweep, plus a bullish candle at 4 used as the order block.
    # Last bar closes back inside that bullish candle's range.
    rows = _sell_sweep_ohlc()
    # Force last bar into the OB at index 4 [low 1.0905, high 1.1000] — too wide.
    # Insert a tighter bullish OB immediately before the sweep (index 7).
    rows[7] = (1.0900, 1.0958, 1.0896, 1.0952)  # bullish OB
    rows[-1] = (1.0948, 1.0956, 1.0938, 1.0949)  # mitigate into OB
    return rows


def _m5_buy_with_ob() -> list[tuple[float, float, float, float]]:
    rows = _buy_sweep_ohlc()
    rows[7] = (1.1090, 1.1096, 1.1030, 1.1036)  # bearish OB
    rows[-1] = (1.1050, 1.1068, 1.1042, 1.1055)  # mitigate into OB
    return rows


NOW = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)


def test_h1_sell_sweep_without_m15_is_not_ready():
    h1 = _frame(_sell_sweep_ohlc(), "2026-09-10 00:00:00", 60)
    side, detail = find_tf_sweep(h1, lookback=16)
    assert side == "SELL", (side, detail)
    report = evaluate_htf(h1, None, None, "EURUSD", now=NOW)
    assert report["bias"] == "SELL"
    assert report["ready"] is False
    assert report["action"] == "HOLD"
    assert report["h1"]["state"] == "sweep"
    assert report["m15"]["state"] == "wait"


def test_m5_alone_does_not_fire():
    m5 = _frame(_m5_sell_with_ob(), "2026-09-10 10:00:00", 5)
    report = evaluate_htf(None, None, m5, "EURUSD", now=NOW)
    assert report["ready"] is False
    assert report["action"] == "HOLD"


def test_full_stack_sell_ready():
    h1 = _frame(_sell_sweep_ohlc(), "2026-09-10 00:00:00", 60)
    # H1 sweep bar is index 8 → 08:00. M15 starts there so confirmation is after.
    m15 = _frame(_sell_sweep_ohlc(), "2026-09-10 08:00:00", 15)
    m5 = _frame(_m5_sell_with_ob(), "2026-09-10 10:00:00", 5)
    report = evaluate_htf(h1, m15, m5, "EURUSD", now=NOW)
    assert report["h1"]["state"] == "sweep"
    assert report["m15"]["state"] == "ok"
    assert report["ready"] is True, report
    assert report["action"] == "SELL"
    a = analyze_htf_sweep(
        m5,
        h1,
        m15,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=NOW,
    )
    assert a.action == "SELL", (a.action, a.reasons, a.checks)
    assert a.checks.get("strategy") == "htf_sweep"
    assert a.sl_pips >= 1.0
    assert abs(a.tp_pips / a.sl_pips - 2.0) < 1e-6


def test_full_stack_buy_ready():
    h1 = _frame(_buy_sweep_ohlc(), "2026-09-10 00:00:00", 60)
    m15 = _frame(_buy_sweep_ohlc(), "2026-09-10 08:00:00", 15)
    m5 = _frame(_m5_buy_with_ob(), "2026-09-10 10:00:00", 5)
    report = evaluate_htf(h1, m15, m5, "EURUSD", now=NOW)
    assert report["ready"] is True, report
    assert report["action"] == "BUY"
    a = analyze_htf_sweep(
        m5,
        h1,
        m15,
        "EURUSD",
        session_start_hour=0,
        session_end_hour=24,
        now=NOW,
    )
    assert a.action == "BUY", (a.action, a.reasons, a.checks)


def test_desk_report_is_json_safe():
    from bot.htf_sweep import desk_report, empty_report

    blank = desk_report()
    assert blank["ready"] is False
    assert blank["action"] == "HOLD"
    assert blank["h1"]["state"] == "wait"
    filled = desk_report(
        {
            **empty_report(),
            "ready": True,
            "action": "SELL",
            "bias": "SELL",
            "signal": "sell",
            "why": "go",
            "sl_pips": 12.5,
            "tp_pips": 25.0,
            "cross_bar": "bar",
        }
    )
    assert filled["ready"] is True
    assert filled["sl_pips"] == 12.5


def test_m5_to_m15_resamples_three_bars():
    from bot.analyst import m5_to_m15

    rows = []
    t0 = pd.Timestamp("2026-09-10 10:00:00", tz="UTC")
    for i in range(6):
        rows.append(
            {
                "time": t0 + timedelta(minutes=5 * i),
                "open": 1.1,
                "high": 1.11,
                "low": 1.09,
                "close": 1.105,
            }
        )
    out = m5_to_m15(pd.DataFrame(rows))
    assert len(out) >= 1
    assert {"open", "high", "low", "close"} <= set(out.columns)
