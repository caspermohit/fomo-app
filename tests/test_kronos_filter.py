
"""Unit tests for DEMO-ONLY Kronos agree gate."""

import json
import time
from pathlib import Path

from bot.kronos_filter import kronos_allows


def test_kronos_off_always_allows(tmp_path: Path):
    ok, why, checks = kronos_allows("EURUSD", "BUY", enabled=False)
    assert ok is True
    assert checks["kronos_gate"] == "off"


def test_kronos_agrees_allows(tmp_path: Path):
    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time(),
                "symbols": {
                    "EURUSD": {
                        "predicted_side": "BUY",
                        "slope": 0.0002,
                        "rel_change_bps": 2.5,
                        "chop": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    ok, why, checks = kronos_allows("EURUSD", "BUY", enabled=True, path=p)
    assert ok is True
    assert checks["kronos_decision"] == "allow"
    assert "agrees" in why


def test_kronos_opposite_skips(tmp_path: Path):
    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time(),
                "symbols": {"EURUSD": {"predicted_side": "SELL", "chop": False, "slope": -0.1}},
            }
        ),
        encoding="utf-8",
    )
    ok, why, checks = kronos_allows("EURUSD", "BUY", enabled=True, path=p)
    assert ok is False
    assert checks["kronos_decision"] == "skip"
    assert "SELL" in why


def test_kronos_chop_allows_macd_by_default(tmp_path: Path):
    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time(),
                "symbols": {"XAUUSD": {"predicted_side": "CHOP", "chop": True, "slope": 0.0}},
            }
        ),
        encoding="utf-8",
    )
    ok, why, checks = kronos_allows("XAUUSD", "SELL", enabled=True, path=p)
    assert ok is True
    assert checks["kronos_decision"] == "neutral"
    assert "chop" in why.lower()


def test_kronos_chop_skips_when_policy_skip(tmp_path: Path):
    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time(),
                "symbols": {"XAUUSD": {"predicted_side": "CHOP", "chop": True, "slope": 0.0}},
            }
        ),
        encoding="utf-8",
    )
    ok, why, _ = kronos_allows("XAUUSD", "SELL", enabled=True, path=p, chop_policy="skip")
    assert ok is False
    assert "chop" in why.lower()


def test_kronos_stale_skips(tmp_path: Path):
    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time() - 10_000,
                "symbols": {"GBPJPY": {"predicted_side": "BUY", "chop": False, "slope": 0.01}},
            }
        ),
        encoding="utf-8",
    )
    ok, why, _ = kronos_allows("GBPJPY", "BUY", enabled=True, path=p, max_age_sec=1800)
    assert ok is False
    assert "stale" in why.lower()


def test_symbol_status_exposes_allow_sides(tmp_path: Path):
    from bot.kronos_filter import symbol_status

    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time(),
                "updated_at": "now",
                "symbols": {
                    "XAUUSD": {
                        "predicted_side": "SELL",
                        "slope": -0.4,
                        "rel_change_bps": -3.2,
                        "chop": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    blob = symbol_status("XAUUSD", enabled=True, path=p)
    assert blob["predicted"] == "SELL"
    assert blob["allow_sell"] is True
    assert blob["allow_buy"] is False
    assert blob["enabled"] is True
    off = symbol_status("XAUUSD", enabled=False, path=p)
    assert off["allow_buy"] is True
    assert off["predicted"] == "SELL"
