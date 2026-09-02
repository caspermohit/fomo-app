"""Demo-only MetaTrader bot settings. Live trading is hard-blocked."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "runtime.json"
LOG_PATH = DATA_DIR / "bot.log"


@dataclass(frozen=True)
class Settings:
    trade_mode: str
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_path: str
    symbol: str
    risk_pct: float
    max_lot: float
    sl_pips: float
    tp_pips: float
    poll_seconds: int
    dashboard_port: int
    magic: int = 26082601
    max_positions: int = 8
    fill_venue: str = "mt5"
    mutex_required: bool = True
    session_start_hour: int = 7
    session_end_hour: int = 21
    min_atr_pips: float = 1.0
    min_score: int = 4
    take_atr_mult: float = 1.5
    stop_atr_mult: float = 1.5
    weekend_guard: bool = True
    weekend_flat: bool = True
    stop_cooldown_minutes: int = 5
    min_entry_seconds: int = 60
    max_spread_pips: float = 2.0
    demo_start_balance: float = 100000.0
    history_lookback_days: int = 0

    @property
    def is_demo(self) -> bool:
        return self.trade_mode == "demo" and "demo" in self.mt5_server.lower()


def load_settings() -> Settings:
    mode = os.getenv("TRADE_MODE", "demo").strip().lower()
    server = os.getenv("MT5_SERVER", "MetaQuotes-Demo").strip()
    if mode != "demo":
        raise SystemExit("Live trading is disabled. TRADE_MODE must be demo.")
    hard_block = os.getenv("HARD_BLOCK_LIVE", "true").strip().lower() not in {"0", "false", "no"}
    if not hard_block:
        raise SystemExit("HARD_BLOCK_LIVE cannot be turned off. Demo only.")
    if "demo" not in server.lower():
        raise SystemExit(f"Refusing non-demo server: {server}")

    login_raw = os.getenv("MT5_LOGIN", "").strip()
    if not login_raw:
        raise SystemExit("MT5_LOGIN is missing from .env")

    DATA_DIR.mkdir(exist_ok=True)
    return Settings(
        trade_mode=mode,
        mt5_login=int(login_raw),
        mt5_password=os.getenv("MT5_PASSWORD", ""),
        mt5_server=server,
        mt5_path=os.getenv(
            "MT5_PATH",
            str(Path.home() / "Applications" / "MetaTrader 5.app"),
        ),
        symbol=os.getenv("SYMBOL", "EURUSD").upper(),
        risk_pct=float(os.getenv("RISK_PCT", "0.01")),
        max_lot=max(0.01, float(os.getenv("MAX_LOT", "0.10"))),
        sl_pips=float(os.getenv("SL_PIPS", "20")),
        tp_pips=float(os.getenv("TP_PIPS", "40")),
        poll_seconds=max(1, int(os.getenv("POLL_SECONDS", "1"))),
        dashboard_port=int(os.getenv("DASHBOARD_PORT", "5055")),
        max_positions=max(1, int(os.getenv("MAX_POSITIONS", "8"))),
        fill_venue="mt5" if os.getenv("FILL_VENUE", "mt5").strip().lower() != "paper" else "paper",
        mutex_required=os.getenv("EXECUTION_MUTEX_REQUIRED", "true").strip().lower() not in {"0", "false", "no"},
        session_start_hour=max(0, min(23, int(os.getenv("SESSION_START_HOUR", "7")))),
        session_end_hour=max(0, min(24, int(os.getenv("SESSION_END_HOUR", "21")))),
        min_atr_pips=max(0.0, float(os.getenv("MIN_ATR_PIPS", "1"))),
        min_score=max(1, min(8, int(os.getenv("MIN_SCORE", "4")))),
        take_atr_mult=max(0.5, float(os.getenv("TAKE_ATR_MULT", "1.5"))),
        stop_atr_mult=max(0.5, float(os.getenv("STOP_ATR_MULT", "1.5"))),
        weekend_guard=os.getenv("WEEKEND_GUARD", "true").strip().lower() not in {"0", "false", "no"},
        weekend_flat=os.getenv("WEEKEND_FLAT", "true").strip().lower() not in {"0", "false", "no"},
        stop_cooldown_minutes=max(0, int(os.getenv("STOP_COOLDOWN_MINUTES", "5"))),
        min_entry_seconds=max(0, int(os.getenv("MIN_ENTRY_SECONDS", "60"))),
        max_spread_pips=max(0.2, float(os.getenv("MAX_SPREAD_PIPS", "2.0"))),
        demo_start_balance=max(0.0, float(os.getenv("DEMO_START_BALANCE", "100000"))),
        history_lookback_days=max(0, int(os.getenv("HISTORY_LOOKBACK_DAYS", "0"))),
    )
