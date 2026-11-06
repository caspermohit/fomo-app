"""Tests for the DEMO-ONLY Kronos refresher that feeds the skip/allow gate."""

import importlib.util
import json
import time
from pathlib import Path

import pytest

from bot.kronos_filter import kronos_allows

ROOT = Path(__file__).resolve().parent.parent


def _load_refresh():
    spec = importlib.util.spec_from_file_location(
        "kronos_refresh", ROOT / "scripts" / "kronos_refresh.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


refresh_mod = _load_refresh()


def _state(bars_per_symbol: int = 200, with_volume: bool = True) -> dict:
    bars = []
    for i in range(bars_per_symbol):
        bar = {
            "t": f"2026-09-09 {10 + i // 60:02d}:{i % 60:02d}:00+00:00",
            "o": 1.1000 + i * 1e-5,
            "h": 1.1002 + i * 1e-5,
            "l": 1.0998 + i * 1e-5,
            "c": 1.1001 + i * 1e-5,
        }
        if with_volume:
            bar["v"] = 100.0 + i
        bars.append(bar)
    return {"books": {"EURUSD": {"bars": bars}, "GBPJPY": {"bars": list(bars)}}}


def test_read_books_pulls_every_symbol(tmp_path: Path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps(_state()), encoding="utf-8")
    books = refresh_mod.read_books(p)
    assert set(books) == {"EURUSD", "GBPJPY"}
    assert len(books["EURUSD"]) == 200


def test_read_books_missing_file_raises(tmp_path: Path):
    with pytest.raises(RuntimeError):
        refresh_mod.read_books(tmp_path / "nope.json")


def test_write_csv_has_kronos_columns_and_real_volume(tmp_path: Path):
    books = refresh_mod.read_books(_write_state(tmp_path))
    path, rows, last = refresh_mod.write_csv("EURUSD", books["EURUSD"], tmp_path / "csv")

    assert rows == 200
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "timestamps,open,high,low,close,volume,amount"

    first = lines[1].split(",")
    # Timestamp must be naive "YYYY-mm-dd HH:MM:SS" with the +00:00 offset stripped.
    assert first[0] == "2026-09-09 10:00:00"
    assert float(first[5]) == 100.0
    # amount == volume * close
    assert float(first[6]) == pytest.approx(float(first[5]) * float(first[4]))


def test_write_csv_falls_back_when_volume_absent(tmp_path: Path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps(_state(with_volume=False)), encoding="utf-8")
    books = refresh_mod.read_books(p)
    path, _, _ = refresh_mod.write_csv("EURUSD", books["EURUSD"], tmp_path / "csv")
    volumes = {line.split(",")[5] for line in path.read_text().strip().splitlines()[1:]}
    assert volumes == {"1.0"}


def test_write_csv_refuses_short_history(tmp_path: Path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps(_state(bars_per_symbol=50)), encoding="utf-8")
    books = refresh_mod.read_books(p)
    with pytest.raises(RuntimeError, match="need 128"):
        refresh_mod.write_csv("EURUSD", books["EURUSD"], tmp_path / "csv")


def test_gate_skips_when_decisions_file_missing(tmp_path: Path):
    """A refresher that never ran must fail safe to skip, not allow."""
    ok, why, checks = kronos_allows(
        "EURUSD", "BUY", enabled=True, path=tmp_path / "absent.json"
    )
    assert ok is False
    assert checks["kronos_decision"] == "skip"
    assert "no decisions file" in why


def test_gate_ignores_non_entry_sides(tmp_path: Path):
    p = tmp_path / "kronos_decisions.json"
    p.write_text(
        json.dumps(
            {
                "updated_at_unix": time.time(),
                "symbols": {"EURUSD": {"predicted_side": "CHOP", "chop": True}},
            }
        ),
        encoding="utf-8",
    )
    ok, _, checks = kronos_allows("EURUSD", "FLAT", enabled=True, path=p)
    assert ok is True
    assert checks["kronos_gate"] == "n/a"


def _write_state(tmp_path: Path) -> Path:
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps(_state()), encoding="utf-8")
    return p
