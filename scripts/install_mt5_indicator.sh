#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WINEPREFIX="${WINEPREFIX:-$HOME/Library/Application Support/net.metaquotes.wine.metatrader5}"
WINE="${WINE:-$HOME/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine}"
MT5_MQL5="$WINEPREFIX/drive_c/Program Files/MetaTrader 5/MQL5"

compile_one() {
  local src="$1"
  local dest_dir="$2"
  local name
  name="$(basename "$src" .mq5)"
  mkdir -p "$dest_dir"
  if [[ -x "$WINE" ]]; then
    export WINEPREFIX WINEDEBUG="-all"
    # Compile via Z: (mac path). Do not pass /include — Wine splits "Program Files".
    "$WINE" "C:\\Program Files\\MetaTrader 5\\metaeditor64.exe" \
      "/compile:Z:${src//\//\\}" \
      "/log:Z:${src//\//\\}.log" || true
  fi
  if [[ -f "${src%.mq5}.ex5" ]]; then
    cp "$src" "$dest_dir/${name}.mq5"
    cp "${src%.mq5}.ex5" "$dest_dir/${name}.ex5"
    echo "installed ${name}.ex5 -> $dest_dir"
  else
    cp "$src" "$dest_dir/${name}.mq5"
    echo "copied ${name}.mq5 only — compile in MetaEditor (F7)"
    if [[ -f "${src}.log" ]]; then
      cat "${src}.log"
    fi
  fi
  rm -f "${src}.log"
}

compile_one "$ROOT/mt5/Indicators/FOMO_Analyst.mq5" "$MT5_MQL5/Indicators"
compile_one "$ROOT/mt5/Experts/FOMO_Second_Scalper.mq5" "$MT5_MQL5/Experts"
echo "Navigator → Refresh. Python run.py is the only bot. Indicator FOMO_Analyst paints arrows. Expert FOMO_Second_Scalper is display-only (no orders). Remove any old trading EA from the EURUSD M5 chart, then Refresh. Algo Trading must be green for Python fills."
