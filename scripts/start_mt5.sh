#!/bin/bash
set -euo pipefail
export WINEPREFIX="${HOME}/Library/Application Support/net.metaquotes.wine.metatrader5"
WINE="${HOME}/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine"
exec "$WINE" "C:\\Program Files\\MetaTrader 5\\terminal64.exe" "/config:C:\\mt5_start.ini"
