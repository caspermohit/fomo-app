import json

import pandas as pd

from bot.analyst import (
    analyze,
    atr_dollar_exits,
    ema_crossover,
    exits_from_atr,
    exits_from_pullback,
    htf_bias,
    m5_to_h1,
    macd_cross,
    macd_lines,
    multi_tf_bias,
    pip_dollar_exits,
)
from bot.pnl import summarize_pnl
from bot.risk import can_add_position, lot_for_budget, pip_value_per_lot, session_drawdown_halt


def test_bill_pairs_are_eurusd_gbpjpy_and_gold():
    from bot.config import BILL_PAIRS, parse_symbols

    assert BILL_PAIRS == ("EURUSD", "GBPJPY", "XAUUSD")
    assert parse_symbols(None) == BILL_PAIRS
    assert parse_symbols("") == BILL_PAIRS
    assert parse_symbols("EURUSD") == ("EURUSD",)
    assert parse_symbols("GBPJPY,EURUSD") == ("GBPJPY", "EURUSD")
    assert parse_symbols("USDCAD,GBPJPY") == ("GBPJPY",)
    assert parse_symbols("EURUSD,GBPJPY,XAUUSD") == ("EURUSD", "GBPJPY", "XAUUSD")
    assert parse_symbols("xauusd") == ("XAUUSD",)
    # "GOLD" on MetaQuotes-Demo is the Barrick Gold share, not spot metal.
    assert parse_symbols("GOLD,XAUUSD") == ("XAUUSD",)


def test_jpy_pip_size():
    from bot.risk import pip_size

    assert pip_size("EURUSD") == 0.0001
    assert pip_size("GBPJPY") == 0.01
    assert pip_size("gbpjpy.a") == 0.01


def test_jpy_pip_value_is_not_ten_dollars():
    eur = pip_value_per_lot("EURUSD")
    jpy = pip_value_per_lot("GBPJPY", usd_jpy=150.0)
    assert abs(eur - 10.0) < 1e-9
    assert abs(jpy - (1000.0 / 150.0)) < 1e-9
    assert 6.5 <= jpy <= 10.0
    # $10/pip must not be hardcoded for GBPJPY.
    assert abs(jpy - 10.0) > 1.0
    take, cut = pip_dollar_exits(1.00, 10.0, 15.0, symbol="GBPJPY", usd_jpy=150.0)
    assert abs(cut - 10.0 * jpy) < 1e-9
    assert abs(take - 15.0 * jpy) < 1e-9
    spec = exits_from_pullback(1.00, 10.0, symbol="GBPJPY", usd_jpy=150.0)
    assert abs(spec.cut_usd - 10.0 * jpy) < 1e-9
    # 20 pip SL on 1.00 lot is ~$133, not $200.
    sized = lot_for_budget(100000, 0.01, 20, 10, symbol="GBPJPY", usd_jpy=150.0)
    eur_sized = lot_for_budget(100000, 0.01, 20, 10, symbol="EURUSD")
    assert sized > eur_sized


def test_lot_is_capped_to_micro_lot():
    assert lot_for_budget(50, 0.02, 20, 0.01) == 0.01


def test_lot_uses_demo_equity():
    assert lot_for_budget(100000, 0.02, 20, 10) == 10.0


def test_lot_cap_tenth_lot_is_above_one_dollar():
    assert lot_for_budget(100000, 0.02, 12, 0.10) == 0.10
    spec = exits_from_atr(0.10, atr_pips=1.5, take_mult=2.0, stop_mult=1.5)
    assert spec.take_usd > 1
    assert spec.take_usd < 10
    assert spec.cut_usd > 1
    assert spec.cut_usd < spec.take_usd


def test_session_loss_stop_closes():
    assert session_drawdown_halt(-12, 10, True) == "CLOSE"
    assert session_drawdown_halt(-12, 10, False) == "HOLD"
    assert session_drawdown_halt(0, 10, True) is None


def test_ema_buy_crossover():
    close = [1.0] * 21 + [1.0, 1.2, 1.4, 1.6]
    bars = pd.DataFrame({"close": close})
    assert ema_crossover(bars) in {"BUY", "HOLD"}


def test_hold_without_enough_bars():
    bars = pd.DataFrame({"close": [1.1, 1.2]})
    assert ema_crossover(bars) == "HOLD"


def test_same_side_winner_may_pyramid():
    assert can_add_position([{"side": "BUY", "profit": 0.20}], "BUY", 3) is None


def test_can_open_first_ticket():
    assert can_add_position([], "BUY", 3) is None


def test_blocks_fourth_ticket():
    open_pos = [
        {"side": "BUY", "profit": 0.20},
        {"side": "BUY", "profit": 0.12},
        {"side": "BUY", "profit": 0.08},
    ]
    assert "already 3/3" in (can_add_position(open_pos, "BUY", 3) or "")


def test_only_adds_to_winners():
    open_pos = [{"side": "BUY", "profit": -0.10}]
    assert "winning" in (can_add_position(open_pos, "BUY", 3) or "")


def test_pyramid_second_winner():
    open_pos = [{"side": "BUY", "profit": 0.20}]
    assert can_add_position(open_pos, "BUY", 3) is None


def test_max_one_blocks_second_ticket():
    winner = [{"side": "BUY", "profit": 0.20}]
    loser = [{"side": "BUY", "profit": -0.10}]
    assert "already 1/1" in (can_add_position(winner, "BUY", 1) or "")
    assert "already 1/1" in (can_add_position(loser, "BUY", 1) or "")


def test_manual_still_blocks_mix_and_max():
    # MACD Bill: max 1 per pair, never average losers.
    assert can_add_position([], "BUY", 1) is None
    assert "winning" in (can_add_position([{"side": "BUY", "profit": -0.10}], "BUY", 2) or "")
    assert "mix" in (can_add_position([{"side": "BUY"}], "SELL", 2) or "").lower()
    assert "already 1/1" in (can_add_position([{"side": "BUY"}], "BUY", 1) or "")


def test_exits_are_atr_multiples_not_percent_of_price():
    spec = exits_from_atr(0.10, atr_pips=1.5, take_mult=2.0, stop_mult=1.5)
    assert abs(spec.tp_pips - 3.0) < 1e-9
    assert abs(spec.sl_pips - 2.25) < 1e-9
    assert abs(spec.take_usd - 3.0) < 1e-9
    assert abs(spec.cut_usd - 2.25) < 1e-9


def test_pullback_exits_are_one_point_five_r():
    spec = exits_from_pullback(0.10, sl_pips=10.0)
    assert abs(spec.sl_pips - 10.0) < 1e-9
    assert abs(spec.tp_pips - 15.0) < 1e-9
    assert abs(spec.take_usd - 15.0) < 1e-9
    assert abs(spec.cut_usd - 10.0) < 1e-9


def test_atr_dollar_exits_scale_with_volume():
    take01, cut01 = atr_dollar_exits(0.01, 2.25, 3.0)
    take10, cut10 = atr_dollar_exits(0.10, 2.25, 3.0)
    assert abs(take10 - take01 * 10) < 1e-9
    assert abs(cut10 - cut01 * 10) < 1e-9
    assert abs(take10 - 3.0) < 1e-9
    assert abs(cut10 - 2.25) < 1e-9


def test_micro_lot_is_the_live_cap():
    assert lot_for_budget(100000, 0.02, 20, 0.01) == 0.01


def test_pnl_profit_and_loss():
    profit = summarize_pnl(100000, 100002.5, 0.5)
    assert profit["status"] == "profit"
    assert profit["total"] == 2.5
    assert profit["realized"] == 2.0
    loss = summarize_pnl(100000, 99998.0, -0.4)
    assert loss["status"] == "loss"
    assert loss["total"] == -2.0
    assert loss["recover"] == 2.0
    assert profit["recover"] == 0.0


def test_hole_to_recover_is_zero_when_ahead():
    from bot.pnl import hole_to_recover

    assert hole_to_recover(100000, 99860.79) == 139.21
    assert hole_to_recover(100000, 100010) == 0.0


def test_trades_on_utc_day_keeps_that_day_only():
    from bot.pnl import trades_on_utc_day

    rows = [
        {"time": "2026-08-28 04:02:00", "profit": -0.45},
        {"time": "2026-08-31 15:00:00", "profit": 1.92},
        {"utc_day": "2026-08-31", "time": "15:01:00", "profit": 0.30},
    ]
    today = trades_on_utc_day(rows, "2026-08-31")
    assert len(today) == 2
    assert round(sum(float(t["profit"]) for t in today), 2) == 2.22


def _ohlc(closes, end="2026-08-26 14:00:00"):
    last = pd.Timestamp(end, tz="UTC")
    n = len(closes)
    rows = []
    for i, c in enumerate(closes):
        t = last - pd.Timedelta(minutes=5 * (n - 1 - i))
        rows.append(
            {
                "time": t,
                "open": c - 0.00015,
                "high": c + 0.0004,
                "low": c - 0.0004,
                "close": c,
            }
        )
    return pd.DataFrame(rows)


def _buy_macd_closes():
    """M5 uptrend + pullback + bounce so MACD 12/26/9 crosses above signal below zero."""
    base = [1.05 + i * 0.00015 for i in range(180)]
    dip = [base[-1] - 0.0003 * (i + 1) for i in range(30)]
    bounce = [dip[-1] + 0.0002 * (i + 1) for i in range(4)]
    return base + dip + bounce


def _sell_macd_closes():
    base = [1.20 - i * 0.00015 for i in range(180)]
    rally = [base[-1] + 0.0003 * (i + 1) for i in range(30)]
    fade = [rally[-1] - 0.0002 * (i + 1) for i in range(4)]
    return base + rally + fade


def test_analyst_holds_without_enough_data():
    a = analyze(_ohlc([1.1, 1.11]), "EURUSD")
    assert a.action == "HOLD"


def test_analyst_cuts_open_loser():
    a = analyze(
        _ohlc([1.10 + i * 0.0002 for i in range(80)]),
        "EURUSD",
        position_side="BUY",
        position_profit=-20.0,
        position_sl_pips=20.0,
        position_tp_pips=30.0,
    )
    assert a.action == "CLOSE"


def test_analyst_holds_valid_winner():
    a = analyze(
        _ohlc(_buy_macd_closes()),
        "EURUSD",
        position_side="BUY",
        position_profit=0.20,
        peak_profit=0.20,
        position_sl_pips=20.0,
        position_tp_pips=30.0,
    )
    assert a.action in {"HOLD", "CLOSE"}


def test_chop_does_not_flatten_scratch_trade():
    a = analyze(
        _ohlc([1.10 + i * 0.00004 for i in range(80)]),
        "EURUSD",
        position_side="BUY",
        position_profit=-0.04,
        peak_profit=0.0,
        position_sl_pips=20.0,
        position_tp_pips=30.0,
    )
    assert a.action == "HOLD"
    assert "chopping — exit" not in " ".join(a.reasons)


def test_session_24_7_allows_overnight():
    bars = _ohlc(_buy_macd_closes())
    bars.loc[bars.index[-1], "time"] = pd.Timestamp("2026-08-27 03:00:00", tz="UTC")
    a = analyze(bars, "EURUSD", session_start_hour=0, session_end_hour=24)
    assert "outside the configured session window" not in " ".join(a.reasons)
    night = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=16)
    assert "outside the configured session window" in " ".join(night.reasons)


def test_htf_bias_follows_ema21():
    up = pd.DataFrame({"close": [1.0 + 0.01 * i for i in range(40)]})
    down = pd.DataFrame({"close": [1.4 - 0.01 * i for i in range(40)]})
    assert htf_bias(up) == "BUY"
    assert htf_bias(down) == "SELL"


def test_macd_holds_without_cross():
    closes = [1.05 + i * 0.00015 for i in range(220)]
    a = analyze(_ohlc(closes), "EURUSD", session_start_hour=7, session_end_hour=16)
    assert a.action == "HOLD"
    assert "macd" in " ".join(a.reasons).lower()


def test_macd_holds_cross_on_wrong_side_of_zero():
    base = [1.08 + i * 0.0002 for i in range(220)]
    pause = [base[-1] - 0.00008 * (i + 1) for i in range(2)]
    bounce = [pause[-1] + 0.0006 * (i + 1) for i in range(3)]
    a = analyze(_ohlc(base + pause + bounce), "EURUSD", session_start_hour=7, session_end_hour=16)
    assert a.action == "HOLD"
    assert "zero" in " ".join(a.reasons).lower()


def test_macd_holds_buy_against_ema200():
    closes = _buy_macd_closes()
    for i in range(80):
        closes[i] += 0.02
    a = analyze(_ohlc(closes), "EURUSD", session_start_hour=7, session_end_hour=16)
    assert a.action == "HOLD"
    assert "ema200" in " ".join(a.reasons).lower()


def test_macd_buys_cross_below_zero_above_ema200():
    a = analyze(_ohlc(_buy_macd_closes()), "EURUSD", session_start_hour=7, session_end_hour=16)
    assert a.action == "BUY"
    assert a.checks["ema200"] == "above"
    assert a.checks["zero"] == "below"
    assert abs(a.tp_pips / a.sl_pips - 1.5) < 1e-9


def test_macd_sells_cross_above_zero_below_ema200():
    a = analyze(_ohlc(_sell_macd_closes()), "EURUSD", session_start_hour=7, session_end_hour=16)
    assert a.action == "SELL"
    assert a.checks["ema200"] == "below"
    assert a.checks["zero"] == "above"
    assert abs(a.tp_pips / a.sl_pips - 1.5) < 1e-9


def test_macd_blocks_wide_spread():
    a = analyze(
        _ohlc(_buy_macd_closes()),
        "EURUSD",
        session_start_hour=7,
        session_end_hour=16,
        spread_pips=3.5,
        max_spread_pips=2.0,
    )
    assert a.action == "HOLD"
    assert "spread" in " ".join(a.reasons).lower()


def test_multi_tf_bias_requires_agreement():
    up = pd.DataFrame({"close": [1.0 + 0.01 * i for i in range(40)]})
    down = pd.DataFrame({"close": [1.4 - 0.01 * i for i in range(40)]})
    combined, h1, h4 = multi_tf_bias(up, up)
    assert combined == "BUY" and h1 == "BUY" and h4 == "BUY"
    combined, _, _ = multi_tf_bias(up, down)
    assert combined == "HOLD"


def test_htf_disagree_does_not_block_macd():
    bars = _ohlc(_buy_macd_closes())
    open_ok = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=16, htf_h1="BUY", htf_h4="BUY")
    assert open_ok.action == "BUY"
    disagree = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=16, htf_h1="BUY", htf_h4="SELL")
    assert disagree.action == "BUY"
    assert disagree.checks.get("htf_gate") == "ignored"
    hold = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=16, htf_h1="HOLD", htf_h4="BUY")
    assert hold.action == "BUY"
    assert hold.checks.get("htf_gate") == "ignored"


def test_rsi_exhaustion_does_not_block_macd(monkeypatch):
    bars = _ohlc(_buy_macd_closes())
    monkeypatch.setattr(
        "bot.analyst.rsi",
        lambda close, period=14: pd.Series([72.0] * len(close)),
    )
    tired_buy = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=16)
    assert tired_buy.action == "BUY"
    assert tired_buy.checks.get("rsi_gate") == "exhaustion"

    sell_bars = _ohlc(_sell_macd_closes())
    monkeypatch.setattr(
        "bot.analyst.rsi",
        lambda close, period=14: pd.Series([25.0] * len(close)),
    )
    tired_sell = analyze(sell_bars, "EURUSD", session_start_hour=7, session_end_hour=16)
    assert tired_sell.action == "SELL"
    assert tired_sell.checks.get("rsi_gate") == "exhaustion"


def test_macd_respects_london_hours():
    bars = _ohlc(_buy_macd_closes())
    day = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=16)
    assert day.action == "BUY"
    night = analyze(
        bars,
        "EURUSD",
        session_start_hour=7,
        session_end_hour=16,
        now=pd.Timestamp("2026-08-27 03:00:00", tz="UTC"),
    )
    assert night.action == "HOLD"


def test_m5_to_h1_resamples():
    bars = _ohlc([1.10 + i * 0.0001 for i in range(80)])
    h1 = m5_to_h1(bars)
    assert len(h1) >= 6
    assert {"open", "high", "low", "close"} <= set(h1.columns)


def test_paper_rebases_old_fifty_dollar_book(tmp_path):
    from bot.config import Settings
    from bot.paper_broker import PaperBroker

    store = tmp_path / "paper.json"
    store.write_text('{"balance": 49.28, "positions": [], "trades": [], "closed": []}')
    settings = Settings(
        trade_mode="demo",
        mt5_login=1,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.01,
        max_lot=0.10,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=1,
        dashboard_port=5055,
    )
    paper = PaperBroker(settings, store, seed_balance=99855.06)
    assert paper.refresh_account().balance == 99855.06
    assert json.loads(store.read_text())["balance"] == 99855.06
    assert "winning" in (can_add_position([{"side": "BUY", "profit": -0.10}], "BUY", 4) or "")


def test_fx_weekend_is_friday_night_through_sunday_open():
    from bot.hours import fx_weekend

    assert fx_weekend(pd.Timestamp("2026-08-28 20:59:00", tz="UTC")) is False
    assert fx_weekend(pd.Timestamp("2026-08-28 21:00:00", tz="UTC")) is True
    assert fx_weekend(pd.Timestamp("2026-08-29 12:00:00", tz="UTC")) is True
    assert fx_weekend(pd.Timestamp("2026-08-30 20:59:00", tz="UTC")) is True
    assert fx_weekend(pd.Timestamp("2026-08-30 21:00:00", tz="UTC")) is False


def test_take_profit_sells_winner():
    a = analyze(
        _ohlc(_buy_macd_closes()),
        "EURUSD",
        position_side="BUY",
        position_profit=50.0,
        peak_profit=50.0,
        position_sl_pips=20.0,
        position_tp_pips=30.0,
    )
    assert a.action == "CLOSE"
    assert "take profit" in a.reasons[0].lower()


def test_mutex_python_owns_even_if_old_ea_lock_is_fresh(tmp_path, monkeypatch):
    import bot.mutex as mx

    monkeypatch.setattr(mx, "mql5_files_dir", lambda: tmp_path)
    (tmp_path / mx.EA_LOCK).write_text("9999999999\n")
    assert mx.leftover_ea() is True
    mx.python_heartbeat()
    info = mx.status()
    assert info["owner"] == "python"
    assert info["one_bot"] is True
    assert info["yielded_to_ea"] is False
    assert info["leftover_ea"] is True
    mx.python_release()
    assert (tmp_path / mx.PYTHON_LOCK).exists() is False


def test_engine_does_not_yield_to_ea_lock(tmp_path, monkeypatch):
    from bot.config import Settings
    from bot.engine import BotEngine
    import bot.mutex as mx

    monkeypatch.setattr(mx, "mql5_files_dir", lambda: tmp_path)
    (tmp_path / mx.EA_LOCK).write_text("9999999999\n")
    settings = Settings(
        trade_mode="demo",
        mt5_login=1,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.02,
        max_lot=0.01,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=1,
        dashboard_port=5055,
        mutex_required=True,
    )
    engine = BotEngine(settings)
    assert engine._sync_mutex() is None
    assert engine.yielded_to_ea is False
    assert (tmp_path / mx.PYTHON_LOCK).exists()


def test_mt5_attach_keeps_paper_fills(monkeypatch, tmp_path):
    from bot.broker import AccountSnapshot, OrderResult
    from bot.config import Settings
    from bot.engine import BotEngine
    import bot.engine as engine_mod

    monkeypatch.setattr(engine_mod, "STATE_PATH", tmp_path / "runtime.json")
    acct = AccountSnapshot(
        login="5055047552",
        server="MetaQuotes-Demo",
        broker="MetaQuotes",
        connected=True,
        trade_mode="demo",
        currency="USD",
        balance=99999.04,
        equity=99999.04,
        credit=0,
    )

    class FakeMT5:
        name = "mt5-demo"

        def __init__(self, settings):
            self.settings = settings

        def connect(self):
            return acct

        def refresh_account(self):
            return acct

        def disconnect(self):
            pass

        def positions(self, symbol):
            return []

        def closed_trades(self, since_ts):
            return []

        def close_bot_positions(self, symbol):
            return OrderResult(True, "no bot MT5 positions")

        def close_all(self, symbol):
            return OrderResult(True, "no MT5 positions")

    monkeypatch.setattr(engine_mod, "MT5Broker", FakeMT5)
    settings = Settings(
        trade_mode="demo",
        mt5_login=5055047552,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.02,
        max_lot=0.01,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=15,
        dashboard_port=5055,
        fill_venue="paper",
    )
    bot = BotEngine(settings)
    bot.connect()
    assert bot.broker_name == "paper"
    assert bot.feed is not None
    assert bot.feed.name == "mt5-demo"
    snap = bot.snapshot()
    assert snap["mt5"]["attached"] is True
    assert snap["mt5"]["fills_in_terminal"] is False
    assert snap["fill_venue"] == "paper"
    assert snap["account"]["balance"] == 99999.04
    assert bot.set_fill_venue("mt5")["ok"] is True
    assert bot.broker_name == "mt5-demo"
    assert bot.mt5_status["fills_in_terminal"] is True
    assert bot.set_fill_venue("paper")["ok"] is True
    assert bot.broker_name == "paper"
    assert bot.mt5_status["fills_in_terminal"] is False


def test_risk_pyramid_guard():
    from bot.risk import RiskManager

    risk = RiskManager()
    underwater = [{"type": "buy", "profit": -12.50}]
    assert risk.validate_execution("buy", underwater) is not None
    assert "winning" in (risk.validate_execution("BUY", underwater, 3) or "")
    assert risk.validate_execution("BUY", [{"side": "BUY", "profit": 1.20}], 3) is None
    assert "already 1/1" in (risk.validate_execution("BUY", [{"side": "BUY", "profit": 1.20}], 1) or "")


def test_set_max_positions_clamps_to_two_total():
    from bot.config import Settings
    from bot.engine import BotEngine

    settings = Settings(
        trade_mode="demo",
        mt5_login=1,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.01,
        max_lot=0.10,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=1,
        dashboard_port=5055,
        max_positions=1,
    )
    engine = BotEngine(settings)
    assert engine.set_max_positions(8)["max_positions"] == 2
    assert engine.set_max_positions(0)["max_positions"] == 1
    assert engine.set_max_positions(2)["max_positions"] == 2


def test_wine_ipc_detects_dead_agent():
    from bot.wine_ipc import looks_dead

    assert looks_dead("MT5 not initialized. Call mt5_mac.initialize() first.")
    assert looks_dead(RuntimeError("Backend process is not running"))
    assert not looks_dead("No MT5 bars for EURUSD")


def test_wine_ipc_reap_only_matches_agent(monkeypatch):
    from bot import wine_ipc

    monkeypatch.setattr(
        wine_ipc.subprocess,
        "check_output",
        lambda *a, **k: " 111 wine C:\\Python39\\python.exe Z:\\Users\\x\\wine_mt5_agent.py\n 222 wineserver\n",
    )
    killed = []
    monkeypatch.setattr(wine_ipc.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(wine_ipc.time, "sleep", lambda _s: None)
    assert wine_ipc.list_agent_pids() == [111]
    wine_ipc.reap_hung_agents()
    assert 111 in killed
    assert 222 not in killed


def test_algo_trading_off_retcode_is_human():
    from bot.mt5_broker import explain_trade_retcode

    msg = explain_trade_retcode(10027, "AutoTrading disabled by client")
    assert "Algo Trading is OFF" in msg
    assert "green" in msg


def test_paper_buy_uses_ask_sell_uses_bid(tmp_path, monkeypatch):
    from bot.config import Settings
    from bot.paper_broker import PaperBroker

    settings = Settings(
        trade_mode="demo",
        mt5_login=1,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.01,
        max_lot=0.10,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=1,
        dashboard_port=5055,
    )
    paper = PaperBroker(settings, tmp_path / "paper.json", seed_balance=100000)
    monkeypatch.setattr(paper, "tick", lambda symbol: (1.10000, 1.10020))
    buy = paper.buy("EURUSD", 0.01, 10.0, 15.0)
    sell = paper.sell("EURUSD", 0.01, 10.0, 15.0)
    assert buy.ok and abs(buy.price - 1.10020) < 1e-9
    assert sell.ok and abs(sell.price - 1.10000) < 1e-9


def test_profit_peak_giveback_exit_is_armed_and_magic_scoped():
    from bot.broker import OrderResult, Position
    from bot.config import Settings
    from bot.engine import BotEngine
    class FakeBroker:
        def __init__(self): self.closed = []
        def close_one(self, symbol, ticket): self.closed.append(ticket); return OrderResult(True, "closed")
    settings = Settings(trade_mode="demo", mt5_login=1, mt5_password="x", mt5_server="MetaQuotes-Demo", mt5_path="/tmp", symbol="EURUSD", risk_pct=.01, max_lot=.1, sl_pips=20, tp_pips=40, poll_seconds=1, dashboard_port=5055)
    engine = BotEngine(settings); engine.broker = FakeBroker()
    def pos(ticket, profit, magic): return Position("EURUSD", "BUY", .01, 1.1, profit, ticket, magic)
    engine._apply_profit_lock([pos("t", 6, settings.magic)])
    assert engine.broker.closed == []
    assert engine._apply_profit_lock([pos("t", 4.5, settings.magic)])
    assert engine.broker.closed == []
    assert engine._apply_profit_lock([pos("t", 3.8, settings.magic)]) == []
    assert engine.broker.closed == ["t"]
    engine._apply_profit_lock([pos("manual", 10, 999)])
    assert engine.broker.closed == ["t"]


# ---------------------------------------------------------------------------
# XAUUSD (spot gold) — third desk symbol, identical rules, gold-scale numbers.
# ---------------------------------------------------------------------------


def _desk_settings(**over):
    from bot.config import Settings

    base = dict(
        trade_mode="demo",
        mt5_login=1,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.01,
        max_lot=0.10,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=1,
        dashboard_port=5055,
        symbols=("EURUSD", "GBPJPY", "XAUUSD"),
    )
    base.update(over)
    return Settings(**base)


def _gold_ohlc(closes, end="2026-09-03 14:00:00", wick=3.0):
    """Gold M5 bars. wick=3.0 -> ~600 pip true range, i.e. a live-ish $6 ATR."""
    last = pd.Timestamp(end, tz="UTC")
    n = len(closes)
    rows = []
    for i, c in enumerate(closes):
        t = last - pd.Timedelta(minutes=5 * (n - 1 - i))
        rows.append(
            {
                "time": t,
                "open": c - 0.30,
                "high": c + wick,
                "low": c - wick,
                "close": c,
            }
        )
    return pd.DataFrame(rows)


def _gold_buy_closes():
    """Uptrend + pullback + bounce so MACD 12/26/9 crosses up below zero."""
    base = [4300.0 + i * 1.5 for i in range(180)]
    dip = [base[-1] - 3.0 * (i + 1) for i in range(30)]
    bounce = [dip[-1] + 2.0 * (i + 1) for i in range(4)]
    return base + dip + bounce


def _gold_sell_closes():
    base = [4700.0 - i * 1.5 for i in range(180)]
    rally = [base[-1] + 3.0 * (i + 1) for i in range(30)]
    fade = [rally[-1] - 2.0 * (i + 1) for i in range(4)]
    return base + rally + fade


def test_gold_pip_size_and_value_are_not_forex():
    from bot.risk import (
        GOLD_CONTRACT_SIZE,
        GOLD_USD_PER_PIP_PER_LOT,
        is_gold,
        is_jpy_pair,
        pip_size,
        pip_value_per_lot,
    )

    assert is_gold("XAUUSD")
    assert is_gold("xauusd.m")
    assert not is_gold("EURUSD")
    assert not is_gold("GBPJPY")
    # MetaQuotes-Demo ticker "GOLD" is Nasdaq\Stock\GOLD (Barrick share).
    assert not is_gold("GOLD")
    assert not is_jpy_pair("XAUUSD")

    assert pip_size("XAUUSD") == 0.01
    assert GOLD_CONTRACT_SIZE == 100.0
    gold = pip_value_per_lot("XAUUSD", usd_jpy=150.0)
    assert abs(gold - 1.0) < 1e-9
    assert abs(gold - GOLD_USD_PER_PIP_PER_LOT) < 1e-9
    # not the $10 FX pip value
    assert abs(gold - 10.0) > 8.0
    # not the JPY 1000/USDJPY formula either
    assert abs(gold - (1000.0 / 150.0)) > 5.0
    assert abs(pip_value_per_lot("EURUSD") - 10.0) < 1e-9


def test_gold_pip_value_prefers_live_broker_numbers():
    from bot.risk import pip_value_per_lot

    # tick value straight from MT5 (tick 0.01 == 1 pip on XAUUSD)
    assert abs(pip_value_per_lot("XAUUSD", tick_value=1.0, tick_size=0.01) - 1.0) < 1e-9
    # a broker quoting per-cent ticks still scales to the pip
    assert abs(pip_value_per_lot("XAUUSD", tick_value=0.1, tick_size=0.001) - 1.0) < 1e-9
    # contract size path: 100 oz x $0.01
    assert abs(pip_value_per_lot("XAUUSD", contract_size=100.0) - 1.0) < 1e-9
    # same path is still right for FX
    assert abs(pip_value_per_lot("EURUSD", contract_size=100_000.0) - 10.0) < 1e-9
    # JPY must never go through contract size (that would return yen, not USD)
    assert abs(pip_value_per_lot("GBPJPY", contract_size=100_000.0, usd_jpy=150.0) - (1000.0 / 150.0)) < 1e-9


def test_gold_dollar_exits_use_one_dollar_pip():
    take, cut = pip_dollar_exits(0.10, 500.0, 750.0, symbol="XAUUSD")
    assert abs(cut - 50.0) < 1e-9
    assert abs(take - 75.0) < 1e-9
    spec = exits_from_pullback(0.10, 500.0, symbol="XAUUSD")
    assert abs(spec.sl_pips - 500.0) < 1e-9
    assert abs(spec.tp_pips - 750.0) < 1e-9
    assert abs(spec.cut_usd - 50.0) < 1e-9
    # the $10 FX pip would have claimed $500 of risk on the same ticket
    fx_take, fx_cut = pip_dollar_exits(0.10, 500.0, 750.0, symbol="EURUSD")
    assert abs(fx_cut - 500.0) < 1e-9
    assert cut * 10 == fx_cut


def test_gold_lot_sizing_is_one_percent_capped_at_max_lot():
    # 1% of 99,594 demo equity = $995.94 risk, 700 pip ($7.00) stop, $1.00/pip/lot.
    risk_lots = lot_for_budget(99594.0, 0.01, 700.0, 10.0, symbol="XAUUSD")
    assert abs(risk_lots - round(995.94 / 700.0, 2)) < 1e-9  # 1.42 lots raw
    assert risk_lots > 1.0
    # MAX_LOT 0.10 still binds, same ceiling logic as EURUSD/GBPJPY
    assert lot_for_budget(99594.0, 0.01, 700.0, 0.10, symbol="XAUUSD") == 0.10
    # a $10/pip assumption would have sized 10x smaller
    fx_lots = lot_for_budget(99594.0, 0.01, 700.0, 10.0, symbol="EURUSD")
    assert fx_lots < 0.2
    assert risk_lots > fx_lots * 5
    # actual risk taken at the cap: 700 pips x $1 x 0.10 lot = $70
    assert abs(700.0 * pip_value_per_lot("XAUUSD") * 0.10 - 70.0) < 1e-9


def test_gold_atr_floor_and_spread_cap_are_gold_scale():
    settings = _desk_settings()
    assert settings.atr_floor("XAUUSD") == 500.0
    assert settings.spread_cap("XAUUSD") == 80.0
    # FX untouched
    assert settings.atr_floor("EURUSD") == 4.0
    assert settings.atr_floor("GBPJPY") == 8.0
    assert settings.spread_cap("EURUSD") == 2.0
    assert settings.spread_cap("GBPJPY") == 4.0
    assert settings.pairs == ("EURUSD", "GBPJPY", "XAUUSD")

    from bot.analyst import min_atr_floor

    assert min_atr_floor("XAUUSD", 4.0) == 500.0
    assert min_atr_floor("XAUUSD", 700.0) == 700.0
    assert min_atr_floor("EURUSD", 1.0) == 4.0


def test_gold_macd_entry_passes_every_gate():
    a = analyze(
        _gold_ohlc(_gold_buy_closes()),
        "XAUUSD",
        session_start_hour=7,
        session_end_hour=16,
        min_atr_pips=500.0,
        htf_h1="BUY",
        htf_h4="BUY",
        spread_pips=45.0,
        max_spread_pips=80.0,
    )
    assert a.action == "BUY"
    assert a.checks["ema200"] == "above"
    assert a.checks["zero"] == "below"
    assert a.checks["macd_cross"] == "BUY"
    assert a.checks["htf_gate"] == "ok"
    assert a.checks["rsi_gate"] == "ok"
    assert a.checks["pip_value"] == "1.00"
    assert abs(a.tp_pips / a.sl_pips - 1.5) < 1e-9

    s = analyze(
        _gold_ohlc(_gold_sell_closes()),
        "XAUUSD",
        session_start_hour=7,
        session_end_hour=16,
        min_atr_pips=500.0,
        htf_h1="SELL",
        htf_h4="SELL",
        spread_pips=45.0,
        max_spread_pips=80.0,
    )
    assert s.action == "SELL"
    assert s.checks["ema200"] == "below"
    assert s.checks["zero"] == "above"


def test_gold_htf_and_rsi_do_not_block_macd(monkeypatch):
    bars = _gold_ohlc(_gold_buy_closes())
    kw = dict(session_start_hour=7, session_end_hour=16, min_atr_pips=500.0, max_spread_pips=80.0)
    disagree = analyze(bars, "XAUUSD", htf_h1="BUY", htf_h4="SELL", **kw)
    assert disagree.action == "BUY"
    assert disagree.checks.get("htf_gate") == "ignored"
    monkeypatch.setattr("bot.analyst.rsi", lambda close, period=14: pd.Series([73.0] * len(close)))
    tired_buy = analyze(_gold_ohlc(_gold_buy_closes()), "XAUUSD", htf_h1="BUY", htf_h4="BUY", **kw)
    assert tired_buy.action == "BUY"
    assert tired_buy.checks.get("rsi_gate") == "exhaustion"


def test_gold_entry_needs_ema200_and_a_fresh_cross():
    kw = dict(session_start_hour=7, session_end_hour=16, min_atr_pips=500.0, max_spread_pips=80.0)
    # price dragged under EMA200 -> BUY cross is refused
    closes = _gold_buy_closes()
    for i in range(80):
        closes[i] += 200.0
    against = analyze(_gold_ohlc(closes), "XAUUSD", htf_h1="BUY", htf_h4="BUY", **kw)
    assert against.action == "HOLD"
    assert "ema200" in " ".join(against.reasons).lower()

    # clean trend with no fresh cross
    steady = analyze(
        _gold_ohlc([4300.0 + i * 1.5 for i in range(220)]),
        "XAUUSD",
        htf_h1="BUY",
        htf_h4="BUY",
        **kw,
    )
    assert steady.action == "HOLD"
    assert "macd" in " ".join(steady.reasons).lower()


def test_gold_wide_spread_is_blocked():
    kw = dict(session_start_hour=7, session_end_hour=16, htf_h1="BUY", htf_h4="BUY")
    wide = analyze(
        _gold_ohlc(_gold_buy_closes()),
        "XAUUSD",
        min_atr_pips=500.0,
        spread_pips=95.0,
        max_spread_pips=80.0,
        **kw,
    )
    assert wide.action == "HOLD"
    assert "spread" in " ".join(wide.reasons).lower()


def test_gold_open_ticket_uses_dollar_pip_for_exits():
    a = analyze(
        _gold_ohlc(_gold_buy_closes()),
        "XAUUSD",
        position_side="BUY",
        position_profit=20.0,
        peak_profit=20.0,
        position_volume=0.10,
        position_sl_pips=500.0,
        position_tp_pips=750.0,
        session_start_hour=7,
        session_end_hour=16,
        min_atr_pips=500.0,
    )
    # 1.5R take on 0.10 lot of gold is $75, not $750
    assert a.checks["take_usd"] == "75.00"
    assert a.checks["cut_usd"] == "50.00"
    assert a.action == "HOLD"
    banked = analyze(
        _gold_ohlc(_gold_buy_closes()),
        "XAUUSD",
        position_side="BUY",
        position_profit=80.0,
        peak_profit=80.0,
        position_volume=0.10,
        position_sl_pips=500.0,
        position_tp_pips=750.0,
        session_start_hour=7,
        session_end_hour=16,
        min_atr_pips=500.0,
    )
    assert banked.action == "CLOSE"
    assert "take profit" in banked.reasons[0].lower()


def test_engine_pip_value_uses_broker_specs_for_gold():
    from bot.engine import BotEngine

    class FakeSpecMarket:
        name = "fake"

        def __init__(self, specs):
            self._specs = specs

        def symbol_specs(self, symbol):
            return self._specs.get(symbol, {})

        def tick(self, symbol):
            raise RuntimeError("no tick in this fake")

    settings = _desk_settings()

    live = BotEngine(settings)
    live.feed = FakeSpecMarket({"XAUUSD": {"tick_value": 1.0, "tick_size": 0.01, "contract_size": 100.0}})
    assert abs(live._pip_value("XAUUSD") - 1.0) < 1e-9

    # Wine bridge that answers None for every spec -> 100 oz convention, never $10
    blind = BotEngine(settings)
    blind.feed = FakeSpecMarket({})
    assert abs(blind._pip_value("XAUUSD") - 1.0) < 1e-9
    assert abs(blind._pip_value("EURUSD") - 10.0) < 1e-9
    assert blind._pip_value("GBPJPY") < 10.0


def test_two_open_tickets_total_block_a_third_on_gold():
    open_pos = [
        {"symbol": "EURUSD", "side": "BUY", "profit": 1.20},
        {"symbol": "GBPJPY", "side": "BUY", "profit": 0.80},
    ]
    assert "already 2/2" in (can_add_position(open_pos, "BUY", 2) or "")
    assert can_add_position([], "BUY", 1) is None


def test_macd_win_rate_filters_era_closes():
    """MACD WR uses magic+comment and excludes pre-rewrite Bill closes; last-20 WR stays mixed."""
    from bot.config import Settings
    from bot.engine import BotEngine, is_macd_strategy_close, macd_win_stats

    settings = Settings(
        trade_mode="demo",
        mt5_login=1,
        mt5_password="x",
        mt5_server="MetaQuotes-Demo",
        mt5_path="/tmp",
        symbol="EURUSD",
        risk_pct=0.01,
        max_lot=0.10,
        sl_pips=20,
        tp_pips=40,
        poll_seconds=1,
        dashboard_port=5055,
        macd_era_start="2026-09-03T00:00:00+00:00",
    )
    magic = int(settings.magic)
    era = 1756857600.0  # 2026-09-03 00:00:00 UTC approx; helper parses from settings too
    from bot.engine import _macd_era_ts

    era_ts = _macd_era_ts(settings)
    closes = [
        # Old Bill era — same magic but before rewrite morning
        {"time": "2026-09-02 15:00:00", "symbol": "EURUSD", "profit": 10.0, "magic": magic, "comment": "fomo-demo-bot"},
        {"time": "2026-09-02 16:00:00", "symbol": "EURUSD", "profit": -5.0, "magic": magic, "comment": "fomo-demo-close"},
        # MACD era
        {"time": "2026-09-03 12:00:00", "symbol": "EURUSD", "profit": 8.0, "magic": magic, "comment": "fomo-demo-bot"},
        {"time": "2026-09-03 13:00:00", "symbol": "GBPJPY", "profit": -3.0, "magic": magic, "comment": "fomo-demo-close"},
        {"time": "2026-09-03 14:00:00", "symbol": "EURUSD", "profit": 4.0, "magic": magic, "comment": "fomo-demo-bot"},
        {"time": "2026-09-03 15:00:00", "symbol": "XAUUSD", "profit": -2.0, "magic": 999, "comment": "manual"},
        # Post-cutoff, no magic/comment in history — still counted
        {"time": "2026-09-03 16:00:00", "symbol": "EURUSD", "profit": 1.0},
    ]
    assert is_macd_strategy_close(closes[0], magic=magic, era_start_ts=era_ts) is False
    assert is_macd_strategy_close(closes[2], magic=magic, era_start_ts=era_ts) is True
    assert is_macd_strategy_close(closes[5], magic=magic, era_start_ts=era_ts) is False

    stats = macd_win_stats(closes, magic=magic, era_start_ts=era_ts)
    # EURUSD win, GBPJPY loss, EURUSD win, EURUSD blank-meta win = 3 wins / 4 fills
    assert stats["macd_fills"] == 4
    assert stats["macd_wins"] == 3
    assert stats["macd_win_rate"] == 75.0
    assert stats["macd_net"] == round(8.0 - 3.0 + 4.0 + 1.0, 2)

    eurusd = macd_win_stats(closes, magic=magic, era_start_ts=era_ts, symbol="EURUSD")
    assert eurusd["macd_fills"] == 3
    assert eurusd["macd_wins"] == 3
    assert eurusd["macd_win_rate"] == 100.0

    empty = macd_win_stats([], magic=magic, era_start_ts=era_ts)
    assert empty["macd_fills"] == 0
    assert empty["macd_win_rate"] is None

    # Snapshot keeps mixed last-20 win_rate and exposes macd_* fields
    engine = BotEngine(settings)
    engine.recorded_closes = list(reversed(closes))  # newest last in recorded; snapshot reverses

    class _Feed:
        name = "paper"

        def refresh_account(self):
            from bot.broker import AccountSnapshot
            return AccountSnapshot("1", "MetaQuotes-Demo", "MQ", True, "demo", "USD", 100000, 100000)

        def positions(self, symbol):
            return []

        def closed_trades(self, since_ts):
            from bot.broker import ClosedTrade
            out = []
            for row in closes:
                out.append(
                    ClosedTrade(
                        time=row["time"],
                        symbol=row.get("symbol", "EURUSD"),
                        side="BUY",
                        volume=0.01,
                        price=1.1,
                        profit=float(row["profit"]),
                        ticket=f"T-{row['time']}",
                        utc_day=row["time"][:10],
                        magic=row.get("magic"),
                        comment=row.get("comment", ""),
                    )
                )
            return list(reversed(out))

    engine.broker = _Feed()
    engine.broker_name = "paper"
    engine.account = engine.broker.refresh_account()
    snap = engine.snapshot()
    assert snap["fills"] == 7  # last-20 includes old Bill
    assert snap["wins"] == 4
    assert snap["macd_fills"] == 4
    assert snap["macd_wins"] == 3
    assert snap["macd_win_rate"] == 75.0
    assert snap["macd_by_symbol"]["EURUSD"]["macd_win_rate"] == 100.0


def test_session_until_420_et():
    bars = _ohlc(_buy_macd_closes())
    still = analyze(
        bars,
        "EURUSD",
        session_start_hour=7,
        session_end_hour=20,
        session_end_minute=20,
        now=pd.Timestamp("2026-09-03 20:20:00", tz="UTC"),
    )
    assert "outside the configured session window" not in " ".join(still.reasons)
    after = analyze(
        bars,
        "EURUSD",
        session_start_hour=7,
        session_end_hour=20,
        session_end_minute=20,
        now=pd.Timestamp("2026-09-03 20:21:00", tz="UTC"),
    )
    assert "outside the configured session window" in " ".join(after.reasons)
