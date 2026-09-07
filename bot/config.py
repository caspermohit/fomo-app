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

# Bill video 1 pairs + XAUUSD (spot gold on MetaQuotes-Demo, path Metals\XAUUSD).
BILL_PAIRS = ("EURUSD", "GBPJPY", "XAUUSD")


def parse_symbols(symbols_env: str | None) -> tuple[str, ...]:
    """Desk pairs. Unset/blank SYMBOLS → EURUSD+GBPJPY+XAUUSD. Explicit list is allowlisted."""
    if symbols_env is None or not str(symbols_env).strip():
        return BILL_PAIRS
    out: list[str] = []
    for raw in str(symbols_env).replace(";", ",").split(","):
        name = raw.strip().upper()
        if name in BILL_PAIRS and name not in out:
            out.append(name)
    return tuple(out) or BILL_PAIRS


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
    max_positions: int = 2
    profit_arm_usd: float = 3.0
    profit_giveback_pct: float = 0.35
    fill_venue: str = "mt5"
    mutex_required: bool = True
    session_start_hour: int = 7
    session_end_hour: int = 20
    session_start_minute: int = 0
    session_end_minute: int = 20
    min_atr_pips: float = 4.0
    min_atr_pips_jpy: float = 8.0
    min_atr_pips_xau: float = 500.0
    min_score: int = 4
    take_atr_mult: float = 1.5
    stop_atr_mult: float = 1.5
    weekend_guard: bool = True
    weekend_flat: bool = True
    stop_cooldown_minutes: int = 5
    min_entry_seconds: int = 300
    max_spread_pips: float = 2.0
    max_spread_pips_jpy: float = 4.0
    max_spread_pips_xau: float = 80.0
    demo_start_balance: float = 100000.0
    history_lookback_days: int = 0
    # Morning MACD Bill went live on this desk (UTC). Used to exclude old Bill closes.
    macd_era_start: str = "2026-09-03T00:00:00+00:00"
    symbols: tuple[str, ...] = ()

    @property
    def is_demo(self) -> bool:
        return self.trade_mode == "demo" and "demo" in self.mt5_server.lower()

    @property
    def pairs(self) -> tuple[str, ...]:
        return self.symbols or (self.symbol,)

    def spread_cap(self, symbol: str) -> float:
        from bot.risk import is_gold

        if is_gold(symbol):
            # Gold pips are $0.01: live MetaQuotes-Demo spread is ~36-49 pips
            # ($0.36-$0.49) in session, so this cap only bites on rollover/news.
            return float(self.max_spread_pips_xau)
        if str(symbol).upper().endswith("JPY"):
            return float(self.max_spread_pips_jpy)
        return float(self.max_spread_pips)

    def atr_floor(self, symbol: str) -> float:
        from bot.risk import is_gold, is_jpy_pair

        if is_gold(symbol):
            # FX floors (4/8 pips) are meaningless on a $0.01 pip. Gold gets its own.
            return float(self.min_atr_pips_xau)
        pair_min = float(self.min_atr_pips_jpy) if is_jpy_pair(symbol) else 4.0
        return max(float(self.min_atr_pips), pair_min)


def _positive_float(name: str, default: str) -> float:
    raw = os.getenv(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} must be a positive number") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be a positive number")
    return value


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
    pairs = parse_symbols(os.getenv("SYMBOLS"))
    wanted = os.getenv("SYMBOL", pairs[0]).strip().upper()
    if wanted in pairs:
        pairs = (wanted,) + tuple(p for p in pairs if p != wanted)
    return Settings(
        trade_mode=mode,
        mt5_login=int(login_raw),
        mt5_password=os.getenv("MT5_PASSWORD", ""),
        mt5_server=server,
        mt5_path=os.getenv(
            "MT5_PATH",
            str(Path.home() / "Applications" / "MetaTrader 5.app"),
        ),
        symbol=pairs[0],
        symbols=pairs,
        risk_pct=float(os.getenv("RISK_PCT", "0.01")),
        max_lot=max(0.01, float(os.getenv("MAX_LOT", "0.10"))),
        sl_pips=float(os.getenv("SL_PIPS", "20")),
        tp_pips=float(os.getenv("TP_PIPS", "40")),
        poll_seconds=max(1, int(os.getenv("POLL_SECONDS", "1"))),
        dashboard_port=int(os.getenv("DASHBOARD_PORT", "5055")),
        max_positions=max(1, min(2, int(os.getenv("MAX_POSITIONS", "2")))),
        profit_arm_usd=_positive_float("PROFIT_ARM_USD", "3.0"),
        profit_giveback_pct=_positive_float("PROFIT_GIVEBACK_PCT", "0.35"),
        fill_venue="mt5" if os.getenv("FILL_VENUE", "mt5").strip().lower() != "paper" else "paper",
        mutex_required=os.getenv("EXECUTION_MUTEX_REQUIRED", "true").strip().lower() not in {"0", "false", "no"},
        session_start_hour=max(0, min(23, int(os.getenv("SESSION_START_HOUR", "7")))),
        session_end_hour=max(0, min(24, int(os.getenv("SESSION_END_HOUR", "20")))),
        session_start_minute=max(0, min(59, int(os.getenv("SESSION_START_MINUTE", "0")))),
        session_end_minute=max(0, min(59, int(os.getenv("SESSION_END_MINUTE", "20")))),
        min_atr_pips=max(0.0, float(os.getenv("MIN_ATR_PIPS", "4.0"))),
        min_atr_pips_jpy=max(0.0, float(os.getenv("MIN_ATR_PIPS_JPY", "8.0"))),
        min_atr_pips_xau=max(0.0, float(os.getenv("MIN_ATR_PIPS_XAU", "500.0"))),
        min_score=max(1, min(8, int(os.getenv("MIN_SCORE", "4")))),
        take_atr_mult=max(0.5, float(os.getenv("TAKE_ATR_MULT", "1.5"))),
        stop_atr_mult=max(0.5, float(os.getenv("STOP_ATR_MULT", "1.5"))),
        weekend_guard=os.getenv("WEEKEND_GUARD", "true").strip().lower() not in {"0", "false", "no"},
        weekend_flat=os.getenv("WEEKEND_FLAT", "true").strip().lower() not in {"0", "false", "no"},
        stop_cooldown_minutes=max(0, int(os.getenv("STOP_COOLDOWN_MINUTES", "5"))),
        min_entry_seconds=max(0, int(os.getenv("MIN_ENTRY_SECONDS", "300"))),
        max_spread_pips=max(0.2, float(os.getenv("MAX_SPREAD_PIPS", "2.0"))),
        max_spread_pips_jpy=max(0.2, float(os.getenv("MAX_SPREAD_PIPS_JPY", "4.0"))),
        max_spread_pips_xau=max(1.0, float(os.getenv("MAX_SPREAD_PIPS_XAU", "80.0"))),
        demo_start_balance=max(0.0, float(os.getenv("DEMO_START_BALANCE", "100000"))),
        history_lookback_days=max(0, int(os.getenv("HISTORY_LOOKBACK_DAYS", "0"))),
        macd_era_start=os.getenv("MACD_ERA_START", "2026-09-03T00:00:00+00:00").strip()
        or "2026-09-03T00:00:00+00:00",
    )
