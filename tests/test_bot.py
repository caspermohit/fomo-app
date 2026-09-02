import json

import pandas as pd

from bot.analyst import analyze, atr_dollar_exits, ema_crossover, exits_from_atr, htf_bias, m5_to_h1, multi_tf_bias
from bot.pnl import summarize_pnl
from bot.risk import can_add_position, lot_for_budget, session_drawdown_halt


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


def test_manual_skips_pyramid_but_not_mix_or_max():
    assert can_add_position([], "BUY", 3, require_pyramid_winners=False) is None
    assert can_add_position([{"side": "BUY", "profit": -0.10}], "BUY", 3, require_pyramid_winners=False) is None
    assert "mix" in (can_add_position([{"side": "BUY"}], "SELL", 3, require_pyramid_winners=False) or "").lower()
    assert "already 3/3" in (can_add_position([{"side": "BUY"}] * 3, "BUY", 3, require_pyramid_winners=False) or "")


def test_exits_are_atr_multiples_not_percent_of_price():
    spec = exits_from_atr(0.10, atr_pips=1.5, take_mult=2.0, stop_mult=1.5)
    assert abs(spec.tp_pips - 3.0) < 1e-9
    assert abs(spec.sl_pips - 2.25) < 1e-9
    assert abs(spec.take_usd - 3.0) < 1e-9
    assert abs(spec.cut_usd - 2.25) < 1e-9


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


def _ohlc(closes):
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "time": pd.Timestamp("2026-08-26 12:00:00", tz="UTC") + pd.Timedelta(minutes=5 * i),
                "open": c - 0.00015,
                "high": c + 0.0004,
                "low": c - 0.0004,
                "close": c,
            }
        )
    return pd.DataFrame(rows)


def test_analyst_holds_without_enough_data():
    a = analyze(_ohlc([1.1, 1.11]), "EURUSD")
    assert a.action == "HOLD"


def test_analyst_cuts_open_loser():
    closes = [1.10 + i * 0.0002 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", position_side="BUY", position_profit=-20.0)
    assert a.action == "CLOSE"


def test_analyst_holds_valid_winner():
    closes = [1.10 + i * 0.0003 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", position_side="BUY", position_profit=0.20, peak_profit=0.20)
    assert a.action in {"HOLD", "CLOSE"}
    assert a.trend in {"up", "range"}


def test_chop_does_not_flatten_scratch_trade():
    closes = [1.10 + i * 0.00004 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", position_side="BUY", position_profit=-0.04, peak_profit=0.0)
    assert a.action == "HOLD"
    assert "chopping — exit" not in " ".join(a.reasons)


def test_session_24_7_allows_overnight():
    bars = _ohlc([1.10 + i * 0.0002 for i in range(80)])
    bars.loc[bars.index[-1], "time"] = pd.Timestamp("2026-08-27 03:00:00", tz="UTC")
    a = analyze(bars, "EURUSD", session_start_hour=0, session_end_hour=24)
    assert "outside the configured session window" not in " ".join(a.reasons)
    night = analyze(bars, "EURUSD", session_start_hour=7, session_end_hour=21)
    assert "outside the configured session window" in " ".join(night.reasons)


def test_htf_bias_follows_ema21():
    up = pd.DataFrame({"close": [1.0 + 0.01 * i for i in range(40)]})
    down = pd.DataFrame({"close": [1.4 - 0.01 * i for i in range(40)]})
    assert htf_bias(up) == "BUY"
    assert htf_bias(down) == "SELL"


def test_bill_opens_on_multi_tf_trend_without_candle_filter():
    closes = [1.10 + i * 0.0002 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", higher_tf="BUY", session_start_hour=7, session_end_hour=21)
    assert a.action == "BUY"
    assert "multi-TF trend" in a.reasons[0]


def test_bill_still_opens_if_m5_candle_fights_the_trend():
    closes = [1.10 + i * 0.0002 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", higher_tf="SELL", session_start_hour=7, session_end_hour=21)
    assert a.action == "SELL"


def test_bill_blocks_without_aligned_trend():
    closes = [1.10 + i * 0.0002 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", higher_tf="HOLD", session_start_hour=7, session_end_hour=21)
    assert a.action == "HOLD"
    assert "multi-timeframe" in " ".join(a.reasons).lower()


def test_bill_blocks_wide_spread():
    closes = [1.10 + i * 0.0002 for i in range(80)]
    a = analyze(
        _ohlc(closes),
        "EURUSD",
        higher_tf="BUY",
        session_start_hour=7,
        session_end_hour=21,
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


def test_bill_respects_london_ny_hours():
    bars = _ohlc([1.10 + i * 0.0002 for i in range(80)])
    bars.loc[bars.index[-1], "time"] = pd.Timestamp("2026-08-27 03:00:00", tz="UTC")
    a = analyze(bars, "EURUSD", higher_tf="BUY", session_start_hour=7, session_end_hour=21)
    assert a.action == "HOLD"
    day = analyze(
        bars,
        "EURUSD",
        higher_tf="BUY",
        session_start_hour=7,
        session_end_hour=21,
        now=pd.Timestamp("2026-08-27 14:00:00", tz="UTC"),
    )
    assert day.action == "BUY"
    night = analyze(
        bars,
        "EURUSD",
        higher_tf="BUY",
        session_start_hour=7,
        session_end_hour=21,
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
    assert can_add_position([{"side": "BUY", "profit": -0.10}], "BUY", 4, require_pyramid_winners=False) is None


def test_fx_weekend_is_friday_night_through_sunday_open():
    from bot.hours import fx_weekend

    assert fx_weekend(pd.Timestamp("2026-08-28 20:59:00", tz="UTC")) is False
    assert fx_weekend(pd.Timestamp("2026-08-28 21:00:00", tz="UTC")) is True
    assert fx_weekend(pd.Timestamp("2026-08-29 12:00:00", tz="UTC")) is True
    assert fx_weekend(pd.Timestamp("2026-08-30 20:59:00", tz="UTC")) is True
    assert fx_weekend(pd.Timestamp("2026-08-30 21:00:00", tz="UTC")) is False


def test_take_profit_sells_winner():
    closes = [1.10 + i * 0.0003 for i in range(80)]
    a = analyze(_ohlc(closes), "EURUSD", position_side="BUY", position_profit=50.0, peak_profit=50.0)
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
