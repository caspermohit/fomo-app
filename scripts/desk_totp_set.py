"""Read a TOTP secret from stdin and write DESK_TOTP_SECRET into .env. No stdout of the secret."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def main() -> None:
    secret = sys.stdin.read().strip().replace(" ", "").replace("-", "")
    if len(secret) < 16:
        raise SystemExit("secret too short")
    path = ENV_PATH
    lines = path.read_text().splitlines() if path.is_file() else []
    out, seen = [], False
    for line in lines:
        raw = line.strip()
        key = ""
        if raw and not raw.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
        if key == "DESK_TOTP_SECRET":
            out.append(f"DESK_TOTP_SECRET={secret}")
            seen = True
        else:
            out.append(line)
    if not seen:
        if out and out[-1] != "":
            out.append("")
        out.append(f"DESK_TOTP_SECRET={secret}")
    path.write_text("\n".join(out) + "\n")
    print("totp-secret-upserted")


if __name__ == "__main__":
    main()
