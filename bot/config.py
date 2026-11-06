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

# Magic this desk has always traded under. Override with MAGIC per machine when
# more than one bot shares an MT5 login; positions opened under another magic
# are left alone.
DEFAULT_MAGIC = 26082601


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


def parse_strategy(raw: str | None) -> str:
    """macd (default) | day_range | vwap_ema (Austin VWAP+EMA9)."""
    text = (raw or "macd").strip().lower().replace("-", "_").replace(" ", "")
    if text in {"day_range", "dayrange", "crt", "pdh_pdl"}:
        return "day_range"
    if text in {"vwap_ema", "vwap", "ema9", "vwap_ema9", "austin"}:
        return "vwap_ema"
    return "macd"


def parse_news_pairs(raw: str | None) -> tuple[str, ...]:
    """Pairs that skip around USD prints. Unset → gold only. off/none → nobody."""
    if raw is None or not str(raw).strip():
        return ("XAUUSD",)
    text = str(raw).strip().lower()
    if text in {"0", "false", "no", "off", "none", "-"}:
        return ()
    out: list[str] = []
    for part in str(raw).replace(";", ",").split(","):
        name = part.strip().upper()
        if name in BILL_PAIRS and name not in out:
            out.append(name)
    return tuple(out)


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
    magic: int = DEFAULT_MAGIC
    max_positions: int = 4
    # Kotegawa/BNF: halt the session at ~2% equity, not a 20% circuit breaker.
    daily_loss_pct: float = 0.02
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
    # DEMO-ONLY optional Kronos-mini agree gate (default off until verified).
    kronos_filter: bool = False
    kronos_decisions_path: str = ""
    kronos_max_age_sec: int = 1800
    # CHOP = no Kronos opinion. "allow" lets MACD fire; "skip" blocks (old behavior).
    kronos_chop: str = "allow"
    max_lot_xau: float = 0.02
    news_blackout: bool = True
    news_before_min: int = 30
    news_after_min: int = 20
    news_pairs: tuple[str, ...] = ("XAUUSD",)
    atr_gate: bool = True
    monday_open_skip_min: int = 30
    # macd (default) | day_range | vwap_ema. Kronos only for macd.
    strategy: str = "macd"
    day_range_mid_band_pct: float = 0.05
    day_range_timeframe: str = "M15"
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

    def lot_cap(self, symbol: str) -> float:
        from bot.risk import is_gold

        cap = float(self.max_lot)
        if is_gold(symbol):
            xau = float(self.max_lot_xau or 0)
            if xau > 0:
                cap = min(cap, xau)
        return max(0.01, cap)


def _unit_pct(name: str, default: str) -> float:
    raw = os.getenv(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{name} must be a fraction between 0 and 1") from exc
    if value <= 0 or value >= 1:
        raise SystemExit(f"{name} must be a fraction between 0 and 1")
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
        # Each bot must own a distinct magic when several run on one MT5 login,
        # otherwise they claim and manage each other's positions.
        magic=int(os.getenv("MAGIC", str(DEFAULT_MAGIC)) or DEFAULT_MAGIC),
        max_positions=max(1, min(4, int(os.getenv("MAX_POSITIONS", "4")))),
        daily_loss_pct=_unit_pct("DAILY_LOSS_PCT", "0.02"),
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
        kronos_filter=os.getenv("KRONOS_FILTER", "false").strip().lower() in {"1", "true", "yes", "on"},
        kronos_decisions_path=os.getenv("KRONOS_DECISIONS_PATH", "").strip(),
        kronos_max_age_sec=max(60, int(os.getenv("KRONOS_MAX_AGE_SEC", "1800"))),
        kronos_chop="skip" if os.getenv("KRONOS_CHOP", "allow").strip().lower() == "skip" else "allow",
        max_lot_xau=max(0.01, float(os.getenv("MAX_LOT_XAU", "0.02"))),
        news_blackout=os.getenv("NEWS_BLACKOUT", "true").strip().lower() not in {"0", "false", "no", "off"},
        news_before_min=max(0, min(180, int(os.getenv("NEWS_BEFORE_MIN", "30")))),
        news_after_min=max(0, min(180, int(os.getenv("NEWS_AFTER_MIN", "20")))),
        news_pairs=parse_news_pairs(os.getenv("NEWS_PAIRS")),
        atr_gate=os.getenv("ATR_GATE", "true").strip().lower() not in {"0", "false", "no", "off"},
        monday_open_skip_min=max(0, min(180, int(os.getenv("MONDAY_OPEN_SKIP_MIN", "30")))),
        strategy=parse_strategy(os.getenv("STRATEGY", "macd")),
        day_range_mid_band_pct=max(
            0.01,
            min(0.25, float(os.getenv("DAY_RANGE_MID_BAND_PCT", "0.05"))),
        ),
        day_range_timeframe=(
            "M5"
            if os.getenv("DAY_RANGE_TIMEFRAME", "M15").strip().upper() == "M5"
            else "M15"
        ),
    )
