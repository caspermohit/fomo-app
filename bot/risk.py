"""Execution gates only. Strategy lives in bot.analyst — not here."""

from __future__ import annotations

from typing import Literal

Signal = Literal["BUY", "SELL", "HOLD"]
Action = Literal["BUY", "SELL", "CLOSE", "HOLD"]

MAGIC = 26082601


def pip_size(symbol: str) -> float:
    return 0.01 if symbol.endswith("JPY") else 0.0001


def lot_for_budget(bankroll: float, risk_pct: float, sl_pips: float, max_lot: float) -> float:
    """Size from demo equity. 1.00 lot EURUSD ≈ $10/pip. sl_pips comes from the analyst."""
    risk_dollars = max(0.0, bankroll * risk_pct)
    pip_value_per_lot = 10.0
    raw = risk_dollars / (sl_pips * pip_value_per_lot) if sl_pips else 0.0
    lot = min(max_lot, max(0.01, round(raw, 2)))
    if risk_dollars < sl_pips * 0.10:
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
    ) -> float:
        return lot_for_budget(equity, risk_pct, sl_pips, max_lot)

    def session_halt(self, session_pnl: float, max_session_loss: float, has_position: bool) -> Action | None:
        return session_drawdown_halt(session_pnl, max_session_loss, has_position)
