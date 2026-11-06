#!/bin/bash
set -euo pipefail
export DISPLAY="${DISPLAY:-:99}"
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine-mt5}"
export WINEARCH="${WINEARCH:-win64}"
TERM64="$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"
if [[ ! -f "$TERM64" ]]; then
  echo "MetaTrader 5 not found at $TERM64" >&2
  exit 1
fi
exec wine "C:\\Program Files\\MetaTrader 5\\terminal64.exe" /portable /skipupdate
