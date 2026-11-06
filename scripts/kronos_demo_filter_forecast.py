#!/usr/bin/env python3
"""DEMO-ONLY Kronos-mini per-symbol allow/skip forecast from M5 CSVs. No trading."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import torch

ROOT = Path(__file__).resolve().parent
# When uploaded to ~/kronos/demo_filter_forecast.py, ROOT is ~/kronos
sys.path.insert(0, str(ROOT))

from model import Kronos, KronosPredictor, KronosTokenizer


def decide(pred_close: float, last_close: float, chop_bps: float) -> dict:
    slope = float(pred_close) - float(last_close)
    rel_bps = (slope / float(last_close)) * 10000.0 if last_close else 0.0
    chop = abs(rel_bps) < float(chop_bps)
    if chop:
        side = "CHOP"
    elif slope > 0:
        side = "BUY"
    else:
        side = "SELL"
    return {
        "predicted_side": side,
        "slope": round(slope, 8),
        "rel_change_bps": round(rel_bps, 4),
        "chop": chop,
        "last_close": float(last_close),
        "pred_close": float(pred_close),
    }


def forecast_one(
    csv_path: Path,
    predictor: KronosPredictor,
    lookback: int,
    pred_len: int,
    chop_bps: float,
    sample_count: int = 5,
) -> dict:
    df = pd.read_csv(csv_path)
    if "timestamps" not in df.columns:
        raise RuntimeError(f"{csv_path} missing timestamps")
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    need = lookback + pred_len
    if len(df) < lookback:
        raise RuntimeError(f"{csv_path} has {len(df)} rows; need lookback={lookback}")

    # Use the most recent lookback bars; synthesize future timestamps for pred_len.
    hist = df.iloc[-lookback:].copy().reset_index(drop=True)
    cols = ["open", "high", "low", "close", "volume", "amount"]
    for c in cols:
        if c not in hist.columns:
            if c == "amount":
                hist[c] = hist["volume"].astype(float) * hist["close"].astype(float)
            elif c == "volume":
                hist[c] = 1.0
            else:
                raise RuntimeError(f"missing column {c}")
    x_df = hist[cols].copy()
    x_timestamp = hist["timestamps"]
    last_ts = pd.Timestamp(hist["timestamps"].iloc[-1])
    # Assume M5
    y_timestamp = pd.Series(
        [last_ts + pd.Timedelta(minutes=5 * (i + 1)) for i in range(pred_len)]
    )

    pred_df = predictor.predict(
        df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=pred_len,
        T=1.0,
        top_p=0.9,
        # Averaged internally. One sample makes the verdict flip run to run on the
        # same bars, which flips the gate; several samples smooth that out.
        sample_count=sample_count,
        verbose=False,
    )
    last_close = float(x_df["close"].iloc[-1])
    # Use mean of predicted closes as horizon view (smoother than last step only).
    pred_close = float(pred_df["close"].astype(float).mean())
    body = decide(pred_close, last_close, chop_bps)
    body.update(
        {
            "lookback": lookback,
            "pred_len": pred_len,
            "bars_end": str(last_ts),
            "source": "kronos-mini",
            "csv": csv_path.name,
            "rows": int(len(df)),
            "chop_bps": float(chop_bps),
            "sample_count": int(sample_count),
            "pred_close_last": float(pred_df["close"].astype(float).iloc[-1]),
            "pred_close_mean": pred_close,
        }
    )
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "out" / "kronos_decisions.json"))
    ap.add_argument("--lookback", type=int, default=128)
    ap.add_argument("--pred-len", type=int, default=8)
    ap.add_argument("--chop-bps", type=float, default=2.0, help="|rel bps| below this → CHOP")
    ap.add_argument("--chop-bps-xau", type=float, default=5.0)
    # Measured on identical bars, spread of rel_change_bps across three runs:
    #   1 sample  -> EURUSD 4.96, GBPJPY 4.12, XAUUSD 15.06 (flipped BUY to SELL)
    #   20        -> 0.19, 0.81, 4.49
    #   40        -> 0.23, 0.98, 1.76
    # XAUUSD needs the higher count to stay clear of its 5 bps chop band.
    ap.add_argument(
        "--sample-count",
        type=int,
        default=40,
        help="Kronos samples averaged per forecast; low values make the verdict unstable",
    )
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    csvs = sorted(data_dir.glob("*_M5.csv"))
    if not csvs:
        print(f"No *_M5.csv in {data_dir}", file=sys.stderr)
        return 2

    print("=== Kronos-mini DEMO filter forecast ===")
    print(f"torch={torch.__version__} device=cpu")
    t0 = time.time()
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-2k")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-mini")
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=2048)
    print(f"load_seconds={time.time() - t0:.1f}")

    symbols: dict = {}
    for csv in csvs:
        sym = csv.name.split("_")[0].upper()
        chop = args.chop_bps_xau if sym.startswith("XAU") else args.chop_bps
        print(f"forecast {sym} from {csv} chop_bps={chop}")
        t1 = time.time()
        try:
            symbols[sym] = forecast_one(
                csv, predictor, args.lookback, args.pred_len, chop, args.sample_count
            )
            symbols[sym]["infer_seconds"] = round(time.time() - t1, 2)
            print(
                f"  {sym}: side={symbols[sym]['predicted_side']} "
                f"bps={symbols[sym]['rel_change_bps']} "
                f"infer={symbols[sym]['infer_seconds']}s"
            )
        except Exception as exc:
            symbols[sym] = {
                "predicted_side": "CHOP",
                "chop": True,
                "error": str(exc),
                "source": "kronos-mini",
            }
            print(f"  {sym}: ERROR → CHOP ({exc})")

    now = datetime.now(timezone.utc)
    payload = {
        "demo_only": True,
        "hard_block_live": True,
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at_unix": int(now.timestamp()),
        "model": "NeoQuasar/Kronos-mini",
        "sample_count": int(args.sample_count),
        "rule": "allow when predicted_side matches MACD side; CHOP/opposite/stale → skip",
        "symbols": symbols,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("wrote", out_path)
    print("SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
