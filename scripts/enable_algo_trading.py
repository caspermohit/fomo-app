# Runs inside Wine. Posts WM_COMMAND 32851 to MetaTrader 5 to turn Algo Trading on.
# This is a toggle: only run when AutoTrading is off (retcode 10027).

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
WM_COMMAND = 0x0111
ID_ALGO_TRADING = 32851

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible
PostMessageW = user32.PostMessageW

found: list[tuple[int, str]] = []


def _foreach(hwnd, _lparam):
    if not IsWindowVisible(hwnd):
        return True
    n = GetWindowTextLengthW(hwnd)
    if n <= 0:
        return True
    buf = ctypes.create_unicode_buffer(n + 1)
    GetWindowTextW(hwnd, buf, n + 1)
    title = buf.value or ""
    low = title.lower()
    if any(token in low for token in ("metatrader", "metaquotes", "eurusd", "demo account")):
        found.append((int(hwnd), title))
    return True


EnumWindows(EnumWindowsProc(_foreach), 0)
if not found:
    print("NO_WINDOW")
    raise SystemExit(1)

posted = 0
for hwnd, title in found:
    if PostMessageW(hwnd, WM_COMMAND, ID_ALGO_TRADING, 0):
        posted += 1
        print("POSTED")

if not posted:
    print("POST_FAILED")
    raise SystemExit(2)
print("OK", posted)
