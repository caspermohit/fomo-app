from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from bot.risk import Action, Signal, is_gold, is_jpy_pair, pip_size, pip_value_per_lot

MICRO_LOT = 0.01
# Trading Rush MACD 100-trade test:
# https://tradingrush.net/i-risked-macd-trading-strategy-100-times-heres-what-happened/
# Trend: close vs EMA200. Entry: default MACD 12/26/9 cross on the correct side of zero.
# BUY: cross above signal, below zero, price above EMA200.
# SELL: cross below signal, above zero, price below EMA200.
# Stop beyond the pullback (invalidation). Take 2R. Risk % is sized in the engine.
# Real open gates (not display-only): H1+H4 agree, RSI exhaustion, ATR floor,
# flat EMA200 skip, stretched-move skip.
TAKE_ATR_MULT = 1.5  # kept for older tests / unused as primary SL
STOP_ATR_MULT = 1.5
REWARD_R = 2.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
EMA_TREND = 200
SWING_BARS = 5
STOP_BUFFER_PIPS = 1.0
MIN_BARS = 210
PIP_VALUE_PER_LOT = 10.0  # EURUSD default; JPY uses pip_value_per_lot()
MIN_OPEN_SCORE = 4
EUR_ATR_FLOOR = 4.0
JPY_ATR_FLOOR = 8.0
# XAUUSD pips are $0.01, so an FX-scale floor would wave every dead tape through.
# Measured on 3000 MetaQuotes-Demo M5 bars (19 Aug - 3 Sep 2026), 07:00-16:00 UTC:
# min 247, p25 420, median 551, p75 753, p95 1046 pips ($2.47 / $4.20 / $5.51 ...).
# 500 pips = $5.00 M5 ATR ~ 0.9x the session median. It blocks the dead bottom
# ~40% of the tape while keeping spread (~43 pips) under ~9% of ATR, the same
# spread-to-ATR band EURUSD (4.0) and GBPJPY (8.0) already run at.
GOLD_ATR_FLOOR = 500.0
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
# Flat EMA200: |ΔEMA200| over LOOKBACK M5 bars < FLAT_ATR_FRAC × ATR → skip (no trend).
EMA_FLAT_LOOKBACK = 20
EMA_FLAT_ATR_FRAC = 0.15
# Stretched move: directional run on last 1–2 bars ≥ STRETCH_ATR_FRAC × ATR → late entry.
STRETCH_BARS = 2
STRETCH_ATR_FRAC = 0.70


@dataclass(frozen=True)
class ExitSpec:
    """SL/TP in pips and dollars. Primary path is pullback distance, not ATR."""

    sl_pips: float
    tp_pips: float
    take_usd: float
    cut_usd: float


def volume_scale(volume: float) -> float:
    return max(float(volume) / MICRO_LOT, MICRO_LOT)


def min_atr_floor(symbol: str, configured: float = 1.0) -> float:
    if is_gold(symbol):
        pair_min = GOLD_ATR_FLOOR
    elif is_jpy_pair(symbol):
        pair_min = JPY_ATR_FLOOR
    else:
        pair_min = EUR_ATR_FLOOR
    return max(float(configured), pair_min)


def skip_dead_tape(action: str, atr_pips: float, floor: float, enabled: bool = True) -> str | None:
    """Engine overlay. MACD may still print BUY/SELL; quiet tape does not get a ticket."""
    if not enabled or action not in {"BUY", "SELL"}:
        return None
    if float(atr_pips) + 1e-9 < float(floor):
        return f"ATR {float(atr_pips):.1f} below floor {float(floor):.1f} — skip dead tape"
    return None


def pip_dollar_exits(
    volume: float,
    sl_pips: float,
    tp_pips: float,
    symbol: str = "EURUSD",
    pip_value: float | None = None,
    usd_jpy: float | None = None,
) -> tuple[float, float]:
    pv = float(pip_value) if pip_value is not None else pip_value_per_lot(symbol, usd_jpy=usd_jpy)
    pip_dollars = pv * max(float(volume), 0.0)
    return float(tp_pips) * pip_dollars, float(sl_pips) * pip_dollars


def exits_from_atr(
    volume: float,
    atr_pips: float,
    take_mult: float = TAKE_ATR_MULT,
    stop_mult: float = STOP_ATR_MULT,
    symbol: str = "EURUSD",
    pip_value: float | None = None,
    usd_jpy: float | None = None,
) -> ExitSpec:
    atr = max(float(atr_pips), 0.1)
    sl_pips = atr * float(stop_mult)
    tp_pips = atr * float(take_mult)
    take_usd, cut_usd = pip_dollar_exits(volume, sl_pips, tp_pips, symbol=symbol, pip_value=pip_value, usd_jpy=usd_jpy)
    return ExitSpec(sl_pips, tp_pips, take_usd, cut_usd)


def atr_dollar_exits(
    volume: float,
    sl_pips: float,
    tp_pips: float,
    symbol: str = "EURUSD",
    pip_value: float | None = None,
    usd_jpy: float | None = None,
    **_ignored,
) -> tuple[float, float]:
    return pip_dollar_exits(volume, sl_pips, tp_pips, symbol=symbol, pip_value=pip_value, usd_jpy=usd_jpy)


def exits_from_pullback(
    volume: float,
    sl_pips: float,
    reward_r: float = REWARD_R,
    symbol: str = "EURUSD",
    pip_value: float | None = None,
    usd_jpy: float | None = None,
) -> ExitSpec:
    sl = max(float(sl_pips), 1.0)
    tp = sl * float(reward_r)
    take_usd, cut_usd = pip_dollar_exits(volume, sl, tp, symbol=symbol, pip_value=pip_value, usd_jpy=usd_jpy)
    return ExitSpec(sl, tp, take_usd, cut_usd)


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


def macd_lines(close: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL) -> tuple[pd.Series, pd.Series]:
    """Standard MACD 12/26/9: MACD line = EMA12−EMA26, signal = EMA9 of MACD."""
    line = ema(close.astype(float), fast) - ema(close.astype(float), slow)
    sig = ema(line, signal)
    return line, sig


def macd_cross(line: pd.Series, signal: pd.Series) -> Signal:
    """Cross on the last two values. HOLD if there is no cross."""
    if line is None or signal is None or len(line) < 2 or len(signal) < 2:
        return "HOLD"
    prev_m, last_m = float(line.iloc[-2]), float(line.iloc[-1])
    prev_s, last_s = float(signal.iloc[-2]), float(signal.iloc[-1])
    if prev_m <= prev_s and last_m > last_s:
        return "BUY"
    if prev_m >= prev_s and last_m < last_s:
        return "SELL"
    return "HOLD"


def closed_bars(bars: pd.DataFrame, now=None, tf_minutes: int = 5) -> pd.DataFrame:
    """Drop the forming M5 bar so entry is on the cross-bar close."""
    if bars is None or bars.empty or "time" not in bars.columns or now is None:
        return bars
    ts = pd.Timestamp(now)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    last = pd.Timestamp(bars["time"].iloc[-1])
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    elif last.tzinfo is not None and ts.tzinfo is not None:
        last = last.tz_convert("UTC")
        ts = ts.tz_convert("UTC")
    if ts - last < pd.Timedelta(minutes=tf_minutes):
        return bars.iloc[:-1].copy()
    return bars


def htf_agrees(side: Signal, higher_tf=None, h1=None, h4=None) -> tuple[bool, str]:
    """Real entry gate: every provided H1/H4 bias must match the MACD side."""
    if side not in {"BUY", "SELL"}:
        return True, ""
    raw: list[tuple[str, str]] = []
    for name, val in (("H1", h1), ("H4", h4)):
        if val in {"BUY", "SELL", "HOLD"}:
            raw.append((name, str(val)))
    if not raw and higher_tf in {"BUY", "SELL", "HOLD"}:
        raw.append(("HTF", str(higher_tf)))
    if not raw:
        return True, ""
    for _name, val in raw:
        if val != side:
            detail = " / ".join(f"{n} {v}" for n, v in raw)
            return False, f"HTF gate: {detail} does not agree with MACD {side}"
    return True, ""


def rsi_exhausted(side: Signal, rsi_now: float) -> str | None:
    if side == "BUY" and rsi_now > RSI_OVERBOUGHT:
        return f"RSI(14) {rsi_now:.0f} > 70 — skip exhausted BUY"
    if side == "SELL" and rsi_now < RSI_OVERSOLD:
        return f"RSI(14) {rsi_now:.0f} < 30 — skip exhausted SELL"
    return None


def htf_bias(bars: pd.DataFrame) -> Signal:
    """Higher-timeframe EMA21 direction. Fed into htf_agrees as a real open gate."""
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
    return resample_ohlc(bars, "1h")


def m5_to_m15(bars: pd.DataFrame) -> pd.DataFrame:
    return resample_ohlc(bars, "15min")


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


def pullback_stop_pips(bars: pd.DataFrame, symbol: str, side: Signal, entry: float, ema200: float) -> float:
    """Stop beyond the pullback: last-5 swing and EMA200, whichever is further, plus 1 pip."""
    pip = pip_size(symbol)
    window = bars.tail(SWING_BARS)
    buf = STOP_BUFFER_PIPS * pip
    if side == "BUY":
        swing = float(window["low"].min())
        stop = min(swing, float(ema200)) - buf
        dist = float(entry) - stop
    else:
        swing = float(window["high"].max())
        stop = max(swing, float(ema200)) + buf
        dist = stop - float(entry)
    return max(dist / pip, 1.0)


def _session_ok(
    bars: pd.DataFrame,
    start_hour: int = 0,
    end_hour: int = 24,
    now=None,
    start_minute: int = 0,
    end_minute: int = 0,
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
    utc = ts.tz_convert("UTC")
    now_m = int(utc.hour) * 60 + int(utc.minute)
    start_m = int(start_hour) * 60 + max(0, min(59, int(start_minute)))
    end_m = int(end_hour) * 60 + max(0, min(59, int(end_minute)))
    if start_m == end_m:
        return True
    if start_m < end_m:
        return start_m <= now_m <= end_m
    return now_m >= start_m or now_m <= end_m


def _bar_stamp(bars: pd.DataFrame) -> str:
    if bars is None or bars.empty or "time" not in bars.columns:
        return ""
    ts = pd.Timestamp(bars["time"].iloc[-1])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")



def ema200_flat(e200: pd.Series, atr_price: float, lookback: int = EMA_FLAT_LOOKBACK, frac: float = EMA_FLAT_ATR_FRAC) -> str | None:
    """Skip when EMA200 slope is near-flat vs recent ATR (chop / no trend)."""
    if e200 is None or len(e200) <= lookback or atr_price <= 0:
        return None
    now = float(e200.iloc[-1])
    then = float(e200.iloc[-(lookback + 1)])
    if pd.isna(now) or pd.isna(then):
        return None
    move = abs(now - then)
    limit = float(frac) * float(atr_price)
    if move + 1e-12 < limit:
        return (
            f"EMA200 flat over {lookback} bars "
            f"(|Δ| {move:.6f} < {frac:.2f}×ATR {limit:.6f}) — skip"
        )
    return None


def stretched_move(
    bars: pd.DataFrame,
    side: Signal,
    atr_price: float,
    n_bars: int = STRETCH_BARS,
    frac: float = STRETCH_ATR_FRAC,
) -> str | None:
    """Skip late entries: signal candle / last 1–2 bars already ran a large ATR fraction.

    Uses close-path distance (body / net close), not wick extremes — wicks are noise.
    """
    if side not in {"BUY", "SELL"} or bars is None or len(bars) < 1 or atr_price <= 0:
        return None
    n = max(1, min(int(n_bars), len(bars)))
    window = bars.tail(n)
    open0 = float(window["open"].iloc[0])
    close1 = float(window["close"].iloc[-1])
    last_open = float(window["open"].iloc[-1])
    last_close = float(window["close"].iloc[-1])
    if side == "BUY":
        body = max(last_close - last_open, 0.0)
        net = max(close1 - open0, 0.0)
    else:
        body = max(last_open - last_close, 0.0)
        net = max(open0 - close1, 0.0)
    run = max(body, net)
    limit = float(frac) * float(atr_price)
    if run + 1e-12 >= limit:
        return (
            f"stretched {side}: last {n} bar(s) already ran {run:.6f} "
            f"(≥ {frac:.2f}×ATR {limit:.6f}) — skip late entry"
        )
    return None


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
    session_start_minute: int = 0,
    session_end_minute: int = 0,
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
    position_sl_pips: float | None = None,
    position_tp_pips: float | None = None,
    usd_jpy: float | None = None,
    pip_value: float | None = None,
) -> Analysis:
    empty = Analysis("HOLD", "HOLD", 0, 20, 30, "unknown", 50.0, 20.0, ["not enough bars"], {})
    frame = closed_bars(bars, now=now) if bars is not None else bars
    pv = float(pip_value) if pip_value is not None else pip_value_per_lot(symbol, usd_jpy=usd_jpy)
    need_atr = min_atr_floor(symbol, min_atr_pips)
    exit_kw = {"symbol": symbol, "pip_value": pv, "usd_jpy": usd_jpy}
    if frame is None or len(frame) < MIN_BARS or not {"open", "high", "low", "close"} <= set(frame.columns):
        if position_side and position_sl_pips and position_tp_pips:
            spec = exits_from_pullback(position_volume, position_sl_pips, position_tp_pips / position_sl_pips if position_sl_pips else REWARD_R, **exit_kw)
            pnl_tag = f"${position_profit:+.2f}"
            if position_profit >= spec.take_usd:
                return Analysis("CLOSE", "HOLD", 0, spec.sl_pips, spec.tp_pips, "unknown", 50.0, 20.0, [f"take profit {REWARD_R:g}R: open P&L {pnl_tag}"], {"verdict": "bank"})
            if position_profit <= -spec.cut_usd:
                return Analysis("CLOSE", "HOLD", 0, spec.sl_pips, spec.tp_pips, "unknown", 50.0, 20.0, [f"stop loss pullback: open P&L {pnl_tag}"], {"verdict": "cut"})
            return Analysis("HOLD", "HOLD", 0, spec.sl_pips, spec.tp_pips, "unknown", 50.0, 20.0, [f"hold {position_side}: open P&L {pnl_tag} — {REWARD_R:g}R vs pullback SL"], {"verdict": "hold"})
        return empty

    close = frame["close"].astype(float)
    open_ = frame["open"].astype(float)
    e9, e21, e50 = ema(close, 9), ema(close, 21), ema(close, 50)
    e200 = ema(close, EMA_TREND)
    rsi_val = rsi(close).iloc[-1]
    rsi_now = float(rsi_val) if pd.notna(rsi_val) else 50.0
    atr = atr_pips(frame, symbol)
    price = float(close.iloc[-1])
    ema200 = float(e200.iloc[-1])
    last_open = float(open_.iloc[-1])
    bullish = price > last_open
    in_session = _session_ok(frame, session_start_hour, session_end_hour, now=now, start_minute=session_start_minute, end_minute=session_end_minute)
    line, sig = macd_lines(close)
    last_macd = float(line.iloc[-1])
    last_sig = float(sig.iloc[-1])
    cross = macd_cross(line, sig)
    trend_side: Signal = "BUY" if price > ema200 else "SELL" if price < ema200 else "HOLD"
    trend = "up" if trend_side == "BUY" else "down" if trend_side == "SELL" else "range"

    sl = 20.0
    tp = 30.0
    if trend_side in {"BUY", "SELL"}:
        spec_pb = exits_from_pullback(position_volume, pullback_stop_pips(frame, symbol, trend_side, price, ema200), **exit_kw)
        sl, tp = spec_pb.sl_pips, spec_pb.tp_pips

    checks = {
        "trend": trend,
        "rsi": f"{rsi_now:.0f}",
        "atr": f"{atr:.1f} pips",
        "session": "24/7" if session_start_hour <= 0 and session_end_hour >= 24 else (
            "in window" if in_session else "outside window"
        ),
        "candle": "bullish" if bullish else "bearish",
        "tp": f"{REWARD_R:g}R",
        "sl": "pullback",
        "htf": higher_tf or "n/a",
        "h1": htf_h1 or "n/a",
        "h4": htf_h4 or "n/a",
        "spread": "n/a" if spread_pips is None else f"{spread_pips:.1f} pips",
        "ema200": "above" if price > ema200 else "below" if price < ema200 else "flat",
        "macd": f"{last_macd:.6f}",
        "macd_signal": f"{last_sig:.6f}",
        "macd_cross": cross,
        "zero": "below" if last_macd < 0 else "above" if last_macd > 0 else "zero",
        "cross_bar": _bar_stamp(frame),
        "strategy": "macd",
        "pip_value": f"{pv:.2f}",
        "atr_floor": f"{need_atr:.1f}",
    }
    reasons: list[str] = []
    buy = 0
    sell = 0

    if trend == "up":
        buy += 2
        reasons.append("price above M5 EMA200")
    elif trend == "down":
        sell += 2
        reasons.append("price below M5 EMA200")
    else:
        reasons.append("price on EMA200 — no trend")

    if last_macd > last_sig:
        buy += 1
    else:
        sell += 1

    if atr < need_atr:
        checks["volatility"] = "low"
    else:
        checks["volatility"] = "ok"

    if not in_session:
        reasons.append("outside the configured session window")

    if cross == "BUY":
        reasons.append("MACD crossed above signal")
    elif cross == "SELL":
        reasons.append("MACD crossed below signal")

    bias: Signal = trend_side if trend_side in {"BUY", "SELL"} else "HOLD"
    action: Action = "HOLD"
    confidence = 8 if cross in {"BUY", "SELL"} and cross == trend_side else max(buy, sell)

    if position_side:
        use_sl = float(position_sl_pips) if position_sl_pips else sl
        use_tp = float(position_tp_pips) if position_tp_pips else tp
        spec = exits_from_pullback(position_volume, use_sl, use_tp / use_sl if use_sl else REWARD_R, **exit_kw)
        sl, tp = spec.sl_pips, spec.tp_pips
        pnl_tag = f"${position_profit:+.2f}"
        checks["open_pnl"] = pnl_tag
        checks["take_usd"] = f"{spec.take_usd:.2f}"
        checks["cut_usd"] = f"{spec.cut_usd:.2f}"
        checks["verdict"] = "hold"
        if position_profit >= spec.take_usd:
            action = "CLOSE"
            checks["verdict"] = "bank"
            reasons.insert(0, f"take profit {REWARD_R:g}R: open P&L {pnl_tag}")
        elif position_profit <= -spec.cut_usd:
            action = "CLOSE"
            checks["verdict"] = "cut"
            reasons.insert(0, f"stop loss pullback: open P&L {pnl_tag}")
        else:
            action = "HOLD"
            reasons.insert(0, f"hold {position_side}: open P&L {pnl_tag} — {REWARD_R:g}R vs pullback SL")
        return Analysis(action, bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if not in_session:
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if spread_pips is not None and spread_pips > max_spread_pips:
        reasons.append(f"spread {spread_pips:.1f} pips — skip bad-spread hours")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if cross == "HOLD":
        reasons.insert(0, "waiting for a fresh MACD 12/26/9 cross")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if cross == "BUY" and last_macd >= 0:
        reasons.insert(0, "MACD BUY cross is not below the zero line")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
    if cross == "SELL" and last_macd <= 0:
        reasons.insert(0, "MACD SELL cross is not above the zero line")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    if cross == "BUY" and price <= ema200:
        reasons.insert(0, "MACD BUY but close is not above EMA200")
        return Analysis("HOLD", "HOLD", confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
    if cross == "SELL" and price >= ema200:
        reasons.insert(0, "MACD SELL but close is not below EMA200")
        return Analysis("HOLD", "HOLD", confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    # ATR floor — block dead tape inside analyze (engine also overlays when ATR_GATE).
    atr_price = atr * pip_size(symbol)
    if atr + 1e-9 < need_atr:
        checks["volatility"] = "low"
        checks["atr_gate"] = "blocked"
        reasons.insert(0, f"ATR {atr:.1f} below floor {need_atr:.1f} — skip dead tape")
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
    checks["atr_gate"] = "ok"

    ok_htf, htf_reason = htf_agrees(cross, higher_tf=higher_tf, h1=htf_h1, h4=htf_h4)
    checks["htf_gate"] = "ok" if ok_htf else "blocked"
    if not ok_htf:
        reasons.insert(0, htf_reason)
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    tired = rsi_exhausted(cross, rsi_now)
    checks["rsi_gate"] = "exhaustion" if tired else "ok"
    if tired:
        reasons.insert(0, tired)
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    flat = ema200_flat(e200, atr_price)
    checks["ema200_slope"] = "flat" if flat else "ok"
    if flat:
        reasons.insert(0, flat)
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    late = stretched_move(frame, cross, atr_price)
    checks["stretch_gate"] = "blocked" if late else "ok"
    if late:
        reasons.insert(0, late)
        return Analysis("HOLD", bias, confidence, sl, tp, trend, rsi_now, atr, reasons[:8], checks)

    spec = exits_from_pullback(position_volume, pullback_stop_pips(frame, symbol, cross, price, ema200), **exit_kw)
    sl, tp = spec.sl_pips, spec.tp_pips
    reasons.insert(0, f"open {cross}: MACD cross + EMA200 ({REWARD_R:g}R vs pullback SL)")
    return Analysis(cross, cross, 8, sl, tp, trend, rsi_now, atr, reasons[:8], checks)
