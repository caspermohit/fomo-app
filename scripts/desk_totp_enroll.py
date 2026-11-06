"""Print (or create) the desk TOTP secret and otpauth URI for Authenticator apps.

Does not trade. Writes DESK_TOTP_SECRET into .env if missing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pyotp

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ISSUER = "FOMO"
ACCOUNT = "desk"


def _read_env(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text().splitlines()


def _existing_secret(lines: list[str]) -> str:
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() == "DESK_TOTP_SECRET":
            return val.strip().replace(" ", "").replace("-", "")
    return ""


def _upsert(lines: list[str], key: str, value: str) -> list[str]:
    out = []
    seen = False
    for line in lines:
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in line and line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        if out and out[-1] != "":
            out.append("")
        out.append(f"{key}={value}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll FOMO desk TOTP")
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--rotate", action="store_true", help="replace an existing secret")
    args = parser.parse_args()

    lines = _read_env(args.env)
    secret = _existing_secret(lines)
    created = False
    if args.rotate or not secret:
        secret = pyotp.random_base32()
        created = True
        args.env.parent.mkdir(parents=True, exist_ok=True)
        args.env.write_text("\n".join(_upsert(lines, "DESK_TOTP_SECRET", secret)) + "\n")

    uri = pyotp.TOTP(secret).provisioning_uri(name=ACCOUNT, issuer_name=ISSUER)
    print(f"env: {args.env}")
    print(f"wrote: {created}")
    print(f"issuer: {ISSUER}")
    print(f"account: {ACCOUNT}")
    print("Scan the PNG, or add this URI in Google Authenticator / Authy:")
    print(uri)
    png_path = ROOT / "data" / "desk_totp_qr.png"
    try:
        import qrcode

        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(png_path)
        print(f"qr: {png_path}")
    except Exception:
        print("(install qrcode for a QR: pip install 'qrcode[pil]')")


if __name__ == "__main__":
    main()
