"""FX session clock. Demo desk only."""

from __future__ import annotations

from datetime import datetime, timezone


def _utc(ts: datetime | None = None) -> datetime:
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def fx_weekend(ts: datetime | None = None) -> bool:
    """True from Friday 21:00 UTC through Sunday 21:00 UTC (typical FX close)."""
    utc = _utc(ts)
    weekday = utc.weekday()
    hour = utc.hour
    if weekday == 4 and hour >= 21:
        return True
    if weekday == 5:
        return True
    if weekday == 6 and hour < 21:
        return True
    return False
