"""Execution gates only. Strategy lives in bot.analyst — not here."""

from __future__ import annotations

from typing import Literal

Signal = Literal["BUY", "SELL", "HOLD"]
Action = Literal["BUY", "SELL", "CLOSE", "HOLD"]

MAGIC = 26082601


STANDARD_LOT = 100_000.0
USD_PIP_VALUE_PER_LOT = 10.0  # 0.0001 * 100000 for EURUSD-like USD quotes
JPY_YEN_PER_PIP_PER_LOT = 1000.0  # 0.01 * 100000
DEFAULT_USDJPY = 150.0

# Gold is a metal CFD, not forex. MetaQuotes-Demo quotes XAUUSD with digits=2 /
# point=0.01 on the standard 100 oz contract, and settles in USD. So 1 pip is a
# $0.01 move and 1.00 lot is 100 oz -> $1.00 per pip. Never the $10 FX pip value
# and never the JPY 1000/USDJPY formula.
GOLD_PIP = 0.01
GOLD_CONTRACT_SIZE = 100.0  # troy ounces in 1.00 lot
GOLD_USD_PER_PIP_PER_LOT = GOLD_CONTRACT_SIZE * GOLD_PIP  # $1.00


def _letters(symbol: str) -> str:
    return "".join(ch for ch in str(symbol).upper() if ch.isalpha())


def is_gold(symbol: str) -> bool:
    """XAUUSD plus broker suffix variants (XAUUSD.m, XAUUSDpro...).

    Deliberately does NOT match the bare name "GOLD": on MetaQuotes-Demo that
    ticker is the Barrick Gold share (Nasdaq\\Stock\\GOLD), not spot metal.
    """
    return _letters(symbol).startswith("XAUUSD")


def is_jpy_pair(symbol: str) -> bool:
    if is_gold(symbol):
        return False
    letters = _letters(symbol)
    if letters.endswith("JPY"):
        return True
    return len(letters) >= 6 and letters[:6].endswith("JPY")


def pip_size(symbol: str) -> float:
    """Gold pip is 0.01 ($0.01/oz); JPY pip is 0.01; others 0.0001.

    Not the MT5 point (0.001 on JPY, 0.01 on XAUUSD here).
    """
    if is_gold(symbol):
        return GOLD_PIP
    return 0.01 if is_jpy_pair(symbol) else 0.0001


def pip_value_per_lot(
    symbol: str = "EURUSD",
    usd_jpy: float | None = None,
    tick_value: float | None = None,
    tick_size: float | None = None,
    contract_size: float | None = None,
) -> float:
    """USD value of 1 pip for 1.00 lot.

    Preference order:
    1. live MT5 tick value scaled from tick_size to a pip (real broker number),
    2. the symbol's contract size x pip for USD-settled symbols
       (XAUUSD: 100 oz x $0.01 = $1.00; EURUSD: 100000 x 0.0001 = $10),
    3. per-class fallback: gold $1.00, JPY pairs 1000 JPY / USDJPY, else $10.
    """
    if tick_value is not None and tick_size:
        tv, ts = float(tick_value), float(tick_size)
        if tv > 0 and ts > 0:
            return tv * (pip_size(symbol) / ts)
    if contract_size and not is_jpy_pair(symbol):
        # Profit currency is USD here, so contract size x pip is already dollars.
        size = float(contract_size)
        if size > 0:
            return size * pip_size(symbol)
    if is_gold(symbol):
        return GOLD_USD_PER_PIP_PER_LOT
    if is_jpy_pair(symbol):
        rate = float(usd_jpy) if usd_jpy and float(usd_jpy) > 0 else DEFAULT_USDJPY
        return JPY_YEN_PER_PIP_PER_LOT / rate
    return USD_PIP_VALUE_PER_LOT


def lot_for_budget(
    bankroll: float,
    risk_pct: float,
    sl_pips: float,
    max_lot: float,
    symbol: str = "EURUSD",
    pip_value: float | None = None,
    usd_jpy: float | None = None,
) -> float:
    """Size from demo equity vs analyst SL pips. Do not hardcode $10/pip on JPY."""
    risk_dollars = max(0.0, bankroll * risk_pct)
    pv = float(pip_value) if pip_value is not None else pip_value_per_lot(symbol, usd_jpy=usd_jpy)
    pv = max(pv, 0.01)
    raw = risk_dollars / (sl_pips * pv) if sl_pips else 0.0
    lot = min(max_lot, max(0.01, round(raw, 2)))
    if risk_dollars < sl_pips * pv * 0.01:
        return min(max_lot, 0.01)
    return lot


def session_drawdown_halt(session_pnl: float, max_session_loss: float, has_position: bool) -> Action | None:
    """Account-level circuit breaker. None means the session may continue."""
    if session_pnl <= -abs(max_session_loss):
        return "CLOSE" if has_position else "HOLD"
    return None


def _normalize_side(raw) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if text in {"BUY", "0", "POSITION_TYPE_BUY"}:
        return "BUY"
    if text in {"SELL", "1", "POSITION_TYPE_SELL"}:
        return "SELL"
    return text if text in {"BUY", "SELL"} else None


def _side_of(pos) -> str | None:
    if isinstance(pos, dict):
        return _normalize_side(pos.get("side") or pos.get("type"))
    return _normalize_side(getattr(pos, "side", None) or getattr(pos, "type", None))


def _profit_of(pos) -> float:
    if isinstance(pos, dict):
        return float(pos.get("profit") or 0)
    return float(getattr(pos, "profit", 0) or 0)


def can_add_position(
    open_positions,
    action: str,
    max_positions: int,
    require_pyramid_winners: bool = True,
) -> str | None:
    """Return a block reason, or None if another ticket is allowed.

    No trend, RSI, or score checks. Those belong in analyst.py.
    """
    if action not in {"BUY", "SELL"}:
        action = _normalize_side(action) or ""
    if action not in {"BUY", "SELL"}:
        return "no entry signal"
    spots = list(open_positions or [])
    if len(spots) >= max_positions:
        return f"already {len(spots)}/{max_positions} open trades"
    sides = [_side_of(p) for p in spots]
    if spots and any(side != action for side in sides):
        return "do not mix BUY and SELL"
    if spots and require_pyramid_winners:
        if any(_profit_of(p) <= 0.05 for p in spots):
            return "only add to winning trades"
    return None


class RiskManager:
    """Sizing, pyramid caps, mixed-side block, session drawdown. No strategy."""

    magic_number = MAGIC

    def validate_execution(
        self,
        target_side: str,
        current_positions: list,
        max_positions: int = 3,
        require_pyramid_winners: bool = True,
    ) -> str | None:
        return can_add_position(
            current_positions,
            target_side,
            max_positions,
            require_pyramid_winners=require_pyramid_winners,
        )

    def calculate_lot_size(
        self,
        equity: float,
        sl_pips: float,
        risk_pct: float,
        max_lot: float,
        symbol: str = "EURUSD",
        pip_value: float | None = None,
    ) -> float:
        return lot_for_budget(equity, risk_pct, sl_pips, max_lot, symbol=symbol, pip_value=pip_value)

    def session_halt(self, session_pnl: float, max_session_loss: float, has_position: bool) -> Action | None:
        return session_drawdown_halt(session_pnl, max_session_loss, has_position)
