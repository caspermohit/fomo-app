from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from bot.risk import Action, Signal, pip_size

MICRO_LOT = 0.01
TAKE_ATR_MULT = 1.5
STOP_ATR_MULT = 1.5
PIP_VALUE_PER_LOT = 10.0
MIN_OPEN_SCORE = 4


@dataclass(frozen=True)
class ExitSpec:
    """One exit model: ATR multiples → pips → dollars. Not % of EURUSD."""

    sl_pips: float
    tp_pips: float
    take_usd: float
    cut_usd: float


def volume_scale(volume: float) -> float:
    return max(float(volume) / MICRO_LOT, MICRO_LOT)


def pip_dollar_exits(volume: float, sl_pips: float, tp_pips: float) -> tuple[float, float]:
    pip_value = PIP_VALUE_PER_LOT * max(float(volume), 0.0)
    return float(tp_pips) * pip_value, float(sl_pips) * pip_value


def exits_from_atr(
    volume: float,
    atr_pips: float,
    take_mult: float = TAKE_ATR_MULT,
    stop_mult: float = STOP_ATR_MULT,
) -> ExitSpec:
    atr = max(float(atr_pips), 0.1)
    sl_pips = atr * float(stop_mult)
    tp_pips = atr * float(take_mult)
    take_usd, cut_usd = pip_dollar_exits(volume, sl_pips, tp_pips)
    return ExitSpec(sl_pips, tp_pips, take_usd, cut_usd)


def atr_dollar_exits(
    volume: float,
    sl_pips: float,
    tp_pips: float,
    **_ignored,
) -> tuple[float, float]:
    return pip_dollar_exits(volume, sl_pips, tp_pips)


@dataclass
class Analysis:
    action: Action
    bias: Signal
    confidence: int
    sl_pips: float
    tp_pips: float
    trend: str
    rsi: float
    atr_pips: float
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "bias": self.bias,
            "confidence": self.confidence,
            "sl_pips": round(self.sl_pips, 1),
            "tp_pips": round(self.tp_pips, 1),
            "trend": self.trend,
            "rsi": round(self.rsi, 1),
            "atr_pips": round(self.atr_pips, 1),
            "reasons": self.reasons,
            "checks": self.checks,
        }


def last_close(bars: pd.DataFrame) -> float | None:
    if bars is None or bars.empty:
        return None
    return float(bars["close"].iloc[-1])


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def atr_pips(bars: pd.DataFrame, symbol: str, period: int = 14) -> float:
    high, low, close = bars["high"].astype(float), bars["low"].astype(float), bars["close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(span=period, adjust=False).mean().iloc[-1])
    return max(atr / pip_size(symbol), 1.0)


def htf_bias(bars: pd.DataFrame) -> Signal:
    """Higher-timeframe direction. Bill only trades this way, not against it."""
    if bars is None or len(bars) < 25 or "close" not in bars.columns:
        return "HOLD"
    close = bars["close"].astype(float)
    mid = float(ema(close, 21).iloc[-1])
    price = float(close.iloc[-1])
    if price > mid:
        return "BUY"
    if price < mid:
        return "SELL"
    return "HOLD"


def multi_tf_bias(h1: pd.DataFrame, h4: pd.DataFrame | None = None) -> tuple[Signal, Signal, Signal]:
    """Bill needs the trend on more than one timeframe. Return (combined, H1, H4)."""
    one = htf_bias(h1)
    two = htf_bias(h4) if h4 is not None else one
    if one == two and one in {"BUY", "SELL"}:
        return one, one, two
    return "HOLD", one, two


def resample_ohlc(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    if bars is None or bars.empty or not {"open", "high", "low", "close"} <= set(bars.columns):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    if "time" not in bars.columns:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    frame = bars.copy()
    frame = frame.set_index(pd.to_datetime(frame["time"], utc=True))
    hourly = frame.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    hourly.index.name = "time"
    return hourly.reset_index()


def m5_to_h1(bars: pd.DataFrame) -> pd.DataFrame:
    """Build H1 OHLC from M5 when the terminal has no H1 history."""
    return resample_ohlc(bars, "1h")


def m5_to_h4(bars: pd.DataFrame) -> pd.DataFrame:
    return resample_ohlc(bars, "4h")


def ema_crossover(bars: pd.DataFrame, fast: int = 9, slow: int = 21) -> Signal:
    if bars is None or len(bars) < slow + 2:
        return "HOLD"
    close = bars["close"].astype(float)
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    prev_fast, last_fast = float(fast_ema.iloc[-2]), float(fast_ema.iloc[-1])
    prev_slow, last_slow = float(slow_ema.iloc[-2]), float(slow_ema.iloc[-1])
    if prev_fast <= prev_slow and last_fast > last_slow:
        return "BUY"
    if prev_fast >= prev_slow and last_fast < last_slow:
        return "SELL"
    return "HOLD"


def _session_ok(
    bars: pd.DataFrame,
    start_hour: int = 0,
    end_hour: int = 24,
    now=None,
) -> bool:
    if start_hour <= 0 and end_hour >= 24:
        return True
    if now is not None:
        ts = pd.Timestamp(now)
    elif "time" not in bars.columns or bars.empty:
        return True
    else:
        ts = pd.Timestamp(bars["time"].iloc[-1])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    hour = int(ts.tz_convert("UTC").hour)
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def analyze(
    bars: pd.DataFrame,
    symbol: str,
    position_side: Signal | None = None,
    position_profit: float = 0.0,
    peak_profit: float = 0.0,
    position_volume: float = 0.01,
    open_count: int = 0,
    session_start_hour: int = 0,
    session_end_hour: int = 24,
    min_atr_pips: float = 1.0,
    min_score: int = MIN_OPEN_SCORE,
    take_atr_mult: float = TAKE_ATR_MULT,
    stop_atr_mult: float = STOP_ATR_MULT,
    higher_tf: Signal | None = None,
    now=None,
    spread_pips: float | None = None,
    max_spread_pips: float = 2.0,
    htf_h1: Signal | None = None,
    htf_h4: Signal | None = None,
) -> Analysis:
    empty = Analysis("HOLD", "HOLD", 0, 20, 30, "unknown", 50.0, 20.0, ["not enough bars"], {})
    if bars is None or len(bars) < 60 or not {"open", "high", "low", "close"} <= set(bars.columns):
        return empty

    close = bars["close"].astype(float)
    open_ = bars["open"].astype(float)
    e9, e21, e50 = ema(close, 9), ema(close, 21), ema(close, 50)
    rsi_val = rsi(close).iloc[-1]
    rsi_now = float(rsi_val) if pd.notna(rsi_val) else 50.0
    atr = atr_pips(bars, symbol)
    price = float(close.iloc[-1])
    spec = exits_from_atr(position_volume, atr, take_atr_mult, stop_atr_mult)
    sl, tp = spec.sl_pips, spec.tp_pips
    last_open = float(open_.iloc[-1])
    bullish = price > last_open
    in_session = _session_ok(bars, session_start_hour, session_end_hour, now=now)

    if price > float(e50.iloc[-1]) and float(e9.iloc[-1]) > float(e21.iloc[-1]):
        trend = "up"
    elif price < float(e50.iloc[-1]) and float(e9.iloc[-1]) < float(e21.iloc[-1]):
        trend = "down"
    else:
        trend = "range"

    checks = {
        "trend": trend,
        "rsi": f"{rsi_now:.0f}",
        "atr": f"{atr:.1f} pips",
        "session": "24/7" if session_start_hour <= 0 and session_end_hour >= 24 else (
            "in window" if in_session else "outside window"
        ),
        "candle": "bullish" if bullish else "bearish",
        "tp": f"{take_atr_mult:g}×ATR",
        "sl": f"{stop_atr_mult:g}×ATR",
        "htf": higher_tf or "n/a",
        "h1": htf_h1 or "n/a",
        "h4": htf_h4 or "n/a",
        "spread": "n/a" if spread_pips is None else f"{spread_pips:.1f} pips",
    }
    reasons: list[str] = []
    buy = 0
    sell = 0

    if trend == "up":
        buy += 2
        reasons.append("uptrend vs EMA50")
    elif trend == "down":
        sell += 2
        reasons.append("downtrend vs EMA50")
    else:
        reasons.append("no clear trend — wait")

    if float(e9.iloc[-1]) > float(e21.iloc[-1]):
        buy += 1
    else:
        sell += 1

    if 42 <= rsi_now <= 62:
        buy += 1
        sell += 1
        reasons.append("RSI is mid-trend, not exhausted")
    elif rsi_now >= 70:
        sell += 2
        buy -= 2
        reasons.append("RSI overbought — prefer sell or wait")
    elif rsi_now <= 30:
        buy += 2
        sell -= 2
        reasons.append("RSI oversold — prefer buy or wait")

    if bullish:
        buy += 1
    else:
        sell += 1

    if price > float(e9.iloc[-1]):
        buy += 1
    else:
        sell += 1

    if atr < min_atr_pips:
        buy -= 2
        sell -= 2
        reasons.append("ATR too small, market is chopping")
        checks["volatility"] = "low"
    else:
        checks["volatility"] = "ok"

    if not in_session:
        buy -= 2
        sell -= 2
        reasons.append("outside the configured session window")

    cross = ema_crossover(bars)
    if cross == "BUY":
        buy += 2
        reasons.append("EMA 9 crossed above 21")
    elif cross == "SELL":
        sell += 2
        reasons.append("EMA 9 crossed below 21")

    bias: Signal = "HOLD"
    if buy >= sell + 2 and buy >= 4:
        bias = "BUY"
    elif sell >= buy + 2 and sell >= 4:
        bias = "SELL"

    action: Action = "HOLD"
    confidence = max(buy, sell)

    if position_side:
        take_usd, cut_usd = spec.take_usd, spec.cut_usd
        pnl_tag = f"${position_profit:+.2f}"
        checks["open_pnl"] = pnl_tag
        checks["take_usd"] = f"{take_usd:.2f}"
        checks["cut_usd"] = f"{cut_usd:.2f}"
        checks["verdict"] = "hold"
        if position_profit >= take_usd:
            action = "CLOSE"
            checks["verdict"] = "bank"
            reasons.insert(0, f"take profit {take_atr_mult:g}×ATR: open P&L {pnl_tag}")
        elif position_profit <= -cut_usd:
            action = "CLOSE"
            checks["verdict"] = "cut"
            reasons.insert(0, f"stop loss {stop_atr_mult:g}×ATR: open P&L {pnl_tag}")
        else:
            action = "HOLD"
            reasons.insert(0, f"hold {position_side}: open P&L {pnl_tag} — TP {take_atr_mult:g}×ATR / SL {stop_atr_mult:g}×ATR")
        return Analysis(action, bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if higher_tf is not None:
        checks["htf"] = higher_tf
        if htf_h1:
            checks["h1"] = htf_h1
        if htf_h4:
            checks["h4"] = htf_h4
        if not in_session or atr < min_atr_pips:
            return Analysis(
                "HOLD",
                higher_tf if higher_tf in {"BUY", "SELL"} else "HOLD",
                confidence,
                sl,
                tp,
                trend,
                rsi_now,
                atr,
                reasons[:8],
                checks,
            )
        if spread_pips is not None and spread_pips > max_spread_pips:
            reasons.append(f"spread {spread_pips:.1f} pips — Bill only trades when London/NY spreads are tight")
            return Analysis("HOLD", higher_tf if higher_tf in {"BUY", "SELL"} else "HOLD", confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
        if higher_tf not in {"BUY", "SELL"}:
            reasons.append("H1 and H4 disagree or are flat — Bill only trades a multi-timeframe trend")
            return Analysis("HOLD", "HOLD", confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
        reasons.insert(0, f"open {higher_tf}: multi-TF trend (Bill) — clock entry, no candle signal")
        return Analysis(higher_tf, higher_tf, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if bias == "HOLD" or not in_session or atr < min_atr_pips:
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if confidence < min_score:
        reasons.append("score too weak to open a trade")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if open_count > 0 and confidence < 6:
        reasons.append("score too weak to add another ticket")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    reasons.insert(0, f"open {bias}: score {confidence}/8")
    return Analysis(bias, bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
