#!/usr/bin/env python3
"""DEMO-ONLY: export M5 bars → scp to casperserver Kronos → forecast JSON → pull back.

Does not trade. Requires Tailscale SSH to caspermohit@100.116.239.1.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_HOST = "caspermohit@100.116.239.1"
DEFAULT_KEY = str(Path.home() / ".ssh" / "id_ed25519")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def ssh_base(host: str, key: str) -> list[str]:
    return [
        "/usr/bin/ssh",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        host,
    ]


def scp_base(key: str) -> list[str]:
    return [
        "/usr/bin/scp",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.getenv("KRONOS_SSH_HOST", DEFAULT_HOST))
    ap.add_argument("--key", default=os.getenv("KRONOS_SSH_KEY", DEFAULT_KEY))
    ap.add_argument("--remote-dir", default="~/kronos")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--count", type=int, default=400)
    args = ap.parse_args()

    local_csv = ROOT / "data" / "kronos_m5"
    local_out = ROOT / "data" / "kronos_decisions.json"
    local_csv.mkdir(parents=True, exist_ok=True)

    if not args.skip_export:
        py = ROOT / ".venv" / "bin" / "python"
        if not py.is_file():
            py = Path(sys.executable)
        run([str(py), str(ROOT / "scripts" / "export_m5_for_kronos.py"), "--out-dir", str(local_csv), "--count", str(args.count)])

    csvs = sorted(local_csv.glob("*_M5.csv"))
    if not csvs:
        print("No CSVs in", local_csv, file=sys.stderr)
        return 2

    remote = args.remote_dir
    # Expand ~ on remote via ssh shell
    run(ssh_base(args.host, args.key) + [f"mkdir -p {remote}/data {remote}/out"])

    for csv in csvs:
        run(scp_base(args.key) + [str(csv), f"{args.host}:{remote}/data/{csv.name}"])

    # Ensure forecast script exists (idempotent upload)
    local_forecast = ROOT / "scripts" / "kronos_demo_filter_forecast.py"
    run(scp_base(args.key) + [str(local_forecast), f"{args.host}:{remote}/demo_filter_forecast.py"])

    remote_cmd = (
        f"cd {remote} && .venv/bin/python demo_filter_forecast.py "
        f"--data-dir data --out out/kronos_decisions.json"
    )
    run(ssh_base(args.host, args.key) + [remote_cmd])
    run(scp_base(args.key) + [f"{args.host}:{remote}/out/kronos_decisions.json", str(local_out)])

    print("=== local decisions ===")
    print(local_out.read_text(encoding="utf-8")[:4000])
    print("Wrote", local_out)
    print("Toggle: KRONOS_FILTER=true in .env (demo only). Default false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
