#!/usr/bin/env python3
"""Export recent M5 OHLCV for Kronos-mini DEMO filter. No trading."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import BILL_PAIRS, load_settings
from bot.mt5_broker import MT5Broker


def bars_to_kronos_csv(df: pd.DataFrame, out: Path) -> int:
    if df is None or df.empty:
        raise RuntimeError("empty bars")
    frame = df.copy()
    if "time" not in frame.columns:
        raise RuntimeError("bars missing time column")
    ts = pd.to_datetime(frame["time"], utc=True)
    out_df = pd.DataFrame(
        {
            "timestamps": ts.dt.strftime("%Y-%m-%d %H:%M:%S"),
            "open": frame["open"].astype(float),
            "high": frame["high"].astype(float),
            "low": frame["low"].astype(float),
            "close": frame["close"].astype(float),
            "volume": frame["tick_volume"].astype(float)
            if "tick_volume" in frame.columns
            else frame.get("volume", 1.0),
        }
    )
    out_df["volume"] = out_df["volume"].fillna(1.0).clip(lower=1.0)
    out_df["amount"] = (out_df["volume"] * out_df["close"]).astype(float)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out, index=False)
    return len(out_df)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export M5 OHLCV CSVs for Kronos demo filter")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "kronos_m5"))
    ap.add_argument("--count", type=int, default=400, help="M5 bars per symbol (need >=128)")
    ap.add_argument("--symbols", default=",".join(BILL_PAIRS))
    args = ap.parse_args()

    settings = load_settings()
    symbols = [s.strip().upper() for s in args.symbols.replace(";", ",").split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    broker = MT5Broker(settings)
    try:
        acct = broker.connect()
        print(f"MT5 demo connected trade_mode={settings.trade_mode} server={settings.mt5_server} equity={getattr(acct, 'equity', '?')}")
        for sym in symbols:
            df = broker.bars(sym, count=max(args.count, 200), timeframe="M5")
            path = out_dir / f"{sym}_M5.csv"
            n = bars_to_kronos_csv(df, path)
            last = float(df["close"].iloc[-1])
            tlast = df["time"].iloc[-1]
            print(f"wrote {path} rows={n} last_close={last} last_time={tlast}")
    finally:
        try:
            broker.disconnect()
        except Exception:
            pass
    print("SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
