"""USD high-impact calendar. Skip new tickets — never a third entry engine.

Gold is priced in dollars. CPI / NFP / FOMC / PCE routinely reprice USD and
gold in the same minute; they are not MACD signals. Default blackout is
XAUUSD only so EURUSD/GBPJPY can still fire.

Times are America/New_York wall-clock (EDT/EST via zoneinfo). Sources:
BLS release calendars, Fed FOMC calendars, TradersQuant 2026 table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass(frozen=True)
class NewsEvent:
    name: str
    when_et: datetime
    after_min: int

    @property
    def when_utc(self) -> datetime:
        return self.when_et.astimezone(UTC)


def _et(day: str, hhmm: str) -> datetime:
    y, m, d = (int(part) for part in day.split("-"))
    hh, mm = (int(part) for part in hhmm.split(":"))
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def _rows() -> tuple[NewsEvent, ...]:
    # (name, YYYY-MM-DD, HH:MM ET, minutes after print to keep skipping)
    raw = (
        ("FOMC", "2026-01-28", "14:00", 60),
        ("NFP", "2026-02-11", "08:30", 20),
        ("FOMC", "2026-03-18", "14:00", 60),
        ("NFP", "2026-04-03", "08:30", 20),
        ("CPI", "2026-04-10", "08:30", 20),
        ("FOMC", "2026-04-29", "14:00", 60),
        ("FOMC", "2026-06-17", "14:00", 60),
        ("FOMC", "2026-07-29", "14:00", 60),
        ("NFP", "2026-09-04", "08:30", 20),
        ("CPI", "2026-09-11", "08:30", 20),
        ("FOMC", "2026-09-16", "14:00", 60),
        ("PCE", "2026-09-25", "08:30", 20),
        ("NFP", "2026-10-02", "08:30", 20),
        ("CPI", "2026-10-14", "08:30", 20),
        ("FOMC", "2026-10-28", "14:00", 60),
        ("GDP", "2026-10-29", "08:30", 20),
        ("PCE", "2026-10-30", "08:30", 20),
        ("NFP", "2026-11-06", "08:30", 20),
        ("CPI", "2026-11-10", "08:30", 20),
        ("PCE", "2026-11-25", "08:30", 20),
        ("NFP", "2026-12-04", "08:30", 20),
        ("FOMC", "2026-12-09", "14:00", 60),
        ("CPI", "2026-12-10", "08:30", 20),
        ("PCE", "2026-12-23", "08:30", 20),
    )
    return tuple(NewsEvent(name, _et(day, hhmm), after) for name, day, hhmm, after in raw)


EVENTS: tuple[NewsEvent, ...] = _rows()


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    ts = now
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def pair_in_news(symbol: str, pairs: tuple[str, ...] | list[str] | None) -> bool:
    wanted = {str(p).strip().upper() for p in (pairs or ()) if str(p).strip()}
    return str(symbol or "").strip().upper() in wanted


def blackout_for(
    symbol: str,
    now: datetime | None = None,
    *,
    enabled: bool = True,
    before_min: int = 30,
    after_min: int = 20,
    pairs: tuple[str, ...] | list[str] | None = ("XAUUSD",),
) -> dict | None:
    """Active USD print window for this symbol, or None."""
    if not enabled or not pair_in_news(symbol, pairs):
        return None
    utc = _as_utc(now)
    before = max(0, int(before_min))
    after_floor = max(0, int(after_min))
    pair = str(symbol).strip().upper()
    for event in EVENTS:
        start = event.when_utc - timedelta(minutes=before)
        end = event.when_utc + timedelta(minutes=max(after_floor, int(event.after_min)))
        if start <= utc <= end:
            until_et = end.astimezone(ET).strftime("%H:%M ET")
            return {
                "name": event.name,
                "symbol": pair,
                "when": event.when_utc.isoformat(timespec="seconds"),
                "until": end.isoformat(timespec="seconds"),
                "until_et": until_et,
                "reason": f"{pair} news blackout {event.name} until {until_et}",
            }
    return None


def next_event(now: datetime | None = None) -> dict | None:
    utc = _as_utc(now)
    upcoming = [ev for ev in EVENTS if ev.when_utc > utc]
    if not upcoming:
        return None
    event = min(upcoming, key=lambda ev: ev.when_utc)
    minutes = int((event.when_utc - utc).total_seconds() // 60)
    et = event.when_et.strftime("%Y-%m-%d %H:%M ET")
    return {
        "name": event.name,
        "when": event.when_utc.isoformat(timespec="seconds"),
        "when_et": et,
        "minutes": minutes,
        "label": f"{event.name} {et}",
    }


def news_snapshot(
    symbols: tuple[str, ...] | list[str],
    now: datetime | None = None,
    *,
    enabled: bool = True,
    before_min: int = 30,
    after_min: int = 20,
    pairs: tuple[str, ...] | list[str] | None = ("XAUUSD",),
) -> dict:
    utc = _as_utc(now)
    active: list[dict] = []
    if enabled:
        for symbol in symbols:
            hit = blackout_for(
                symbol,
                utc,
                enabled=True,
                before_min=before_min,
                after_min=after_min,
                pairs=pairs,
            )
            if hit:
                active.append(hit)
    nxt = next_event(utc)
    return {
        "enabled": bool(enabled),
        "pairs": [str(p).upper() for p in (pairs or ())],
        "active": active,
        "next": nxt,
    }
