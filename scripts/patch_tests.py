#!/usr/bin/env python3
"""Keep tests on bot.analyst / bot.risk. Do not invent Strategy or AnalystManager."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_PATH = ROOT / "tests" / "test_bot.py"

PYRAMID = '''
def test_risk_pyramid_guard():
    from bot.risk import RiskManager

    risk = RiskManager()
    underwater = [{"type": "buy", "profit": -12.50}]
    assert risk.validate_execution("buy", underwater) is not None
    assert "winning" in (risk.validate_execution("BUY", underwater, 3) or "")
    assert risk.validate_execution("BUY", [{"side": "BUY", "profit": 1.20}], 3) is None
'''


def patch_test_suite() -> int:
    if not TEST_PATH.exists():
        print(f"[-] Error: {TEST_PATH} not found.")
        return 1
    content = TEST_PATH.read_text()
    if "from bot.strategy" in content or "import bot.strategy" in content:
        print("[-] tests/test_bot.py still imports bot.strategy — remove it. Use bot.analyst.")
        return 1
    if "AnalystManager" in content or "strategy = Strategy(" in content:
        print("[-] Refusing to patch in AnalystManager/Strategy. Those classes do not exist.")
        return 1
    if "test_risk_pyramid_guard" not in content:
        TEST_PATH.write_text(content.rstrip() + "\n" + PYRAMID)
        print("[+] Added test_risk_pyramid_guard.")
    else:
        print("[+] tests already use bot.analyst; pyramid guard is present.")
    print("[+] Run: pytest tests/test_bot.py")
    return 0


if __name__ == "__main__":
    sys.exit(patch_test_suite())
