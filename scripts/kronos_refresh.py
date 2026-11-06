#!/usr/bin/env python3
"""DEMO-ONLY: keep data/kronos_decisions.json fresh for the Kronos skip/allow gate.

Runs entirely on the box that owns the bot. Bars come from the bot's own cache in
data/runtime.json, so this never opens a second MT5 connection next to the live
bot. Kronos inference runs in its own venv (default ~/kronos).

Never places or closes an order.

    python scripts/kronos_refresh.py            # one refresh
    python scripts/kronos_refresh.py --loop     # refresh forever, every --every seconds
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "data" / "runtime.json"
CSV_DIR = ROOT / "data" / "kronos_m5"
OUT_PATH = ROOT / "data" / "kronos_decisions.json"
# Kronos-mini needs 128 lookback bars; refuse to forecast on less.
MIN_BARS = 128


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {msg}", flush=True)


def read_books(state_path: Path) -> dict[str, list[dict]]:
    """Pull per-symbol M5 bars out of the bot's runtime state."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {state_path}: {exc}") from exc

    books = state.get("books")
    out: dict[str, list[dict]] = {}
    if isinstance(books, dict):
        for sym, book in books.items():
            if isinstance(book, dict) and isinstance(book.get("bars"), list):
                out[str(sym).upper()] = book["bars"]
    if not out and isinstance(state.get("bars"), list):
        sym = str(state.get("symbol") or "EURUSD").upper()
        out[sym] = state["bars"]
    return out


def write_csv(symbol: str, bars: list[dict], csv_dir: Path) -> tuple[Path, int, str]:
    """Write one Kronos CSV. Returns (path, rows, last timestamp)."""
    rows = []
    for b in bars:
        try:
            close = float(b["c"])
            # Volume is optional in older state files; the forecast script treats
            # a flat series as "no volume signal" rather than failing.
            vol = float(b.get("v") or 0.0)
            rows.append(
                {
                    # Kronos wants naive "YYYY-mm-dd HH:MM:SS"; state stores ISO with offset.
                    "timestamps": str(b.get("t", ""))[:19].replace("T", " "),
                    "open": float(b["o"]),
                    "high": float(b["h"]),
                    "low": float(b["l"]),
                    "close": close,
                    "volume": vol if vol > 0 else 1.0,
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    if len(rows) < MIN_BARS:
        raise RuntimeError(f"{symbol}: only {len(rows)} usable bars, need {MIN_BARS}")

    csv_dir.mkdir(parents=True, exist_ok=True)
    path = csv_dir / f"{symbol}_M5.csv"
    lines = ["timestamps,open,high,low,close,volume,amount"]
    for r in rows:
        lines.append(
            f"{r['timestamps']},{r['open']},{r['high']},{r['low']},"
            f"{r['close']},{r['volume']},{r['volume'] * r['close']}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, len(rows), rows[-1]["timestamps"]


def pull(args: argparse.Namespace) -> int:
    """Copy a decisions file produced on another box (the one running Kronos).

    Used by machines that have the gate but no local Kronos install. The forecast
    is per-symbol M5, so it is equally valid on any box trading the same symbols.
    """
    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_suffix(".json.tmp")

    proc = subprocess.run(
        [
            "/usr/bin/scp",
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=20",
            "-i", str(Path(args.key).expanduser()),
            f"{args.pull_from}:{args.remote_out}",
            str(tmp_out),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not tmp_out.is_file():
        log(f"FAIL pull rc={proc.returncode} {(proc.stderr or '').strip()[:200]}")
        tmp_out.unlink(missing_ok=True)
        return proc.returncode or 2

    try:
        payload = json.loads(tmp_out.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"FAIL pulled file is not JSON: {exc}")
        tmp_out.unlink(missing_ok=True)
        return 2

    tmp_out.replace(out_path)
    age = time.time() - float(payload.get("updated_at_unix") or 0)
    sides = " ".join(
        f"{s}={b.get('predicted_side')}"
        for s, b in (payload.get("symbols") or {}).items()
    )
    log(f"OK pulled {out_path.name} age={int(age)}s {sides}")
    return 0


def refresh(args: argparse.Namespace) -> int:
    if args.pull_from:
        return pull(args)

    state_path = Path(args.state).expanduser()
    csv_dir = Path(args.csv_dir).expanduser()
    out_path = Path(args.out).expanduser()

    books = read_books(state_path)
    if not books:
        log(f"FAIL no bars in {state_path} — is the bot running?")
        return 2

    wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    exported = 0
    for sym, bars in sorted(books.items()):
        if wanted and sym not in wanted:
            continue
        try:
            path, n, last = write_csv(sym, bars, csv_dir)
        except RuntimeError as exc:
            log(f"skip {exc}")
            continue
        log(f"wrote {path.name} rows={n} last={last}")
        exported += 1

    if not exported:
        log("FAIL nothing exported")
        return 2

    kronos_dir = Path(args.kronos_dir).expanduser()
    py = kronos_dir / ".venv" / "bin" / "python"
    script = kronos_dir / "demo_filter_forecast.py"
    if not py.is_file() or not script.is_file():
        log(f"FAIL Kronos env missing: {py} / {script}")
        return 2

    # Forecast into a temp file, then swap, so the bot never reads a half-written
    # decisions file and skips every entry because it looks empty.
    tmp_out = out_path.with_suffix(".json.tmp")
    proc = subprocess.run(
        [str(py), str(script), "--data-dir", str(csv_dir), "--out", str(tmp_out)],
        cwd=str(kronos_dir),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not tmp_out.is_file():
        log(f"FAIL forecast rc={proc.returncode}")
        sys.stderr.write((proc.stderr or proc.stdout or "")[-2000:] + "\n")
        return proc.returncode or 2

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out.replace(out_path)

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    sides = " ".join(
        f"{s}={b.get('predicted_side')}({b.get('rel_change_bps')}bps)"
        for s, b in (payload.get("symbols") or {}).items()
    )
    log(f"OK {out_path.name} {sides}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the demo-only Kronos decisions file")
    ap.add_argument("--state", default=str(STATE_PATH))
    ap.add_argument("--csv-dir", default=str(CSV_DIR))
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--kronos-dir", default="~/kronos")
    ap.add_argument("--symbols", default="", help="comma list; default every symbol in state")
    ap.add_argument(
        "--pull-from",
        default="",
        help="user@host to copy a ready-made decisions file from, instead of forecasting locally",
    )
    ap.add_argument("--remote-out", default="~/fomo-app/data/kronos_decisions.json")
    ap.add_argument("--key", default="~/.ssh/id_ed25519")
    ap.add_argument("--loop", action="store_true", help="refresh forever")
    ap.add_argument("--every", type=int, default=600, help="seconds between refreshes in --loop")
    args = ap.parse_args()

    if not args.loop:
        return refresh(args)

    log(f"loop every {args.every}s — demo-only gate, never trades")
    while True:
        try:
            refresh(args)
        except Exception as exc:  # keep the loop alive; a stale file fails safe as skip
            log(f"ERROR {exc}")
        time.sleep(max(60, args.every))


if __name__ == "__main__":
    raise SystemExit(main())
