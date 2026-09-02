from __future__ import annotations

from datetime import datetime, timezone


def summarize_pnl(
    start_equity: float,
    equity: float,
    floating: float,
) -> dict[str, float | str]:
    total = round(equity - start_equity, 2)
    realized = round(total - floating, 2)
    if total > 0.004:
        status = "profit"
    elif total < -0.004:
        status = "loss"
    else:
        status = "flat"
    return {
        "start_equity": round(start_equity, 2),
        "equity": round(equity, 2),
        "floating": round(floating, 2),
        "realized": realized,
        "total": total,
        "status": status,
        "recover": hole_to_recover(start_equity, equity),
    }


def hole_to_recover(start: float, equity: float) -> float:
    """Dollars still needed to get back to start. Zero if already there or ahead."""
    return round(max(0.0, float(start) - float(equity)), 2)


def utc_day(ts: datetime | None = None) -> str:
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")


def trades_on_utc_day(trades: list, day: str | None = None) -> list:
    needle = day or utc_day()
    out = []
    for trade in trades or []:
        if isinstance(trade, dict):
            stamp = str(trade.get("utc_day") or trade.get("time") or "")
        else:
            stamp = str(getattr(trade, "utc_day", "") or getattr(trade, "time", "") or "")
        if stamp.startswith(needle):
            out.append(trade)
    return out


def utc_day(ts: datetime | None = None) -> str:
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")


def trades_on_utc_day(trades: list, day: str | None = None) -> list:
    needle = day or utc_day()
    out = []
    for trade in trades or []:
        stamp = ""
        if isinstance(trade, dict):
            stamp = str(trade.get("utc_day") or trade.get("time") or "")
        else:
            stamp = str(getattr(trade, "utc_day", "") or getattr(trade, "time", "") or "")
        if stamp.startswith(needle):
            out.append(trade)
    return out
