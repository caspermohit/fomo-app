from datetime import datetime, timezone

from bot.analyst import skip_dead_tape
from bot.config import parse_news_pairs
from bot.hours import fx_monday_open
from bot.news_calendar import blackout_for, next_event, news_snapshot


UTC = timezone.utc


def test_parse_news_pairs_defaults_to_gold():
    assert parse_news_pairs(None) == ("XAUUSD",)
    assert parse_news_pairs("") == ("XAUUSD",)
    assert parse_news_pairs("off") == ()
    assert parse_news_pairs("EURUSD,XAUUSD") == ("EURUSD", "XAUUSD")
    assert parse_news_pairs("USDCAD") == ()


def test_cpi_blackout_skips_gold_not_eurusd():
    # Fri Sep 11 2026 08:30 ET = 12:30 UTC (EDT).
    print_time = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
    gold = blackout_for("XAUUSD", print_time, pairs=("XAUUSD",))
    fx = blackout_for("EURUSD", print_time, pairs=("XAUUSD",))
    assert gold is not None
    assert gold["name"] == "CPI"
    assert "XAUUSD news blackout CPI" in gold["reason"]
    assert fx is None


def test_cpi_window_is_30_before_20_after():
    before = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    after = datetime(2026, 9, 11, 12, 50, tzinfo=UTC)
    too_early = datetime(2026, 9, 11, 11, 59, tzinfo=UTC)
    too_late = datetime(2026, 9, 11, 12, 51, tzinfo=UTC)
    assert blackout_for("XAUUSD", before) is not None
    assert blackout_for("XAUUSD", after) is not None
    assert blackout_for("XAUUSD", too_early) is None
    assert blackout_for("XAUUSD", too_late) is None


def test_fomc_keeps_skipping_through_presser():
    # Wed Sep 16 2026 14:00 ET = 18:00 UTC. after_min on the event is 60.
    statement = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)
    presser = datetime(2026, 9, 16, 18, 45, tzinfo=UTC)
    done = datetime(2026, 9, 16, 19, 1, tzinfo=UTC)
    assert blackout_for("XAUUSD", statement)["name"] == "FOMC"
    assert blackout_for("XAUUSD", presser)["name"] == "FOMC"
    assert blackout_for("XAUUSD", done) is None


def test_winter_fomc_uses_est():
    # Wed Dec 9 2026 14:00 ET = 19:00 UTC.
    assert blackout_for("XAUUSD", datetime(2026, 12, 9, 19, 0, tzinfo=UTC))["name"] == "FOMC"
    assert blackout_for("XAUUSD", datetime(2026, 12, 9, 18, 0, tzinfo=UTC)) is None


def test_next_event_after_sep10_is_cpi():
    now = datetime(2026, 9, 10, 13, 37, tzinfo=UTC)
    nxt = next_event(now)
    assert nxt is not None
    assert nxt["name"] == "CPI"
    assert "2026-09-11" in nxt["when_et"]


def test_news_snapshot_lists_active_gold_only():
    snap = news_snapshot(
        ("EURUSD", "GBPJPY", "XAUUSD"),
        datetime(2026, 9, 11, 12, 30, tzinfo=UTC),
        pairs=("XAUUSD",),
    )
    assert snap["enabled"] is True
    assert [row["symbol"] for row in snap["active"]] == ["XAUUSD"]


def test_disabled_blackout_never_fires():
    assert blackout_for("XAUUSD", datetime(2026, 9, 11, 12, 30, tzinfo=UTC), enabled=False) is None


def test_skip_dead_tape_only_on_entries():
    assert skip_dead_tape("BUY", 3.0, 4.0) == "ATR 3.0 below floor 4.0 — skip dead tape"
    assert skip_dead_tape("BUY", 4.0, 4.0) is None
    assert skip_dead_tape("HOLD", 1.0, 4.0) is None
    assert skip_dead_tape("BUY", 1.0, 4.0, enabled=False) is None


def test_monday_open_is_first_half_hour_after_sunday_reopen():
    from pandas import Timestamp

    assert fx_monday_open(Timestamp("2026-08-30 20:59:00", tz="UTC")) is False
    assert fx_monday_open(Timestamp("2026-08-30 21:00:00", tz="UTC")) is True
    assert fx_monday_open(Timestamp("2026-08-30 21:29:00", tz="UTC")) is True
    assert fx_monday_open(Timestamp("2026-08-30 21:30:00", tz="UTC")) is False
    assert fx_monday_open(Timestamp("2026-08-30 21:00:00", tz="UTC"), skip_minutes=0) is False
