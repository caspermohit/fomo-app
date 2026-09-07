from pathlib import Path

root = Path('/Users/mohitshah/fomo-app')

# --- 1) ClosedTrade: add magic + comment ---
broker = root / 'bot/broker.py'
text = broker.read_text()
old = '''@dataclass
class ClosedTrade:
    time: str
    symbol: str
    side: str
    volume: float
    price: float
    profit: float
    ticket: str = ""
    utc_day: str = ""
'''
new = '''@dataclass
class ClosedTrade:
    time: str
    symbol: str
    side: str
    volume: float
    price: float
    profit: float
    ticket: str = ""
    utc_day: str = ""
    magic: int | None = None
    comment: str = ""
'''
assert old in text, 'ClosedTrade block missing'
broker.write_text(text.replace(old, new, 1))
print('broker.py: ClosedTrade extended')

# --- 2) mt5_broker closed_trades: capture magic/comment ---
mt5 = root / 'bot/mt5_broker.py'
text = mt5.read_text()
old = '''            out.append(
                ClosedTrade(
                    time=when.strftime("%Y-%m-%d %H:%M:%S"),
                    utc_day=when.strftime("%Y-%m-%d"),
                    symbol=symbol,
                    side=side,
                    volume=float(getattr(deal, "volume", 0) or 0),
                    price=float(getattr(deal, "price", 0) or 0),
                    profit=round(profit + swap + commission, 2),
                    ticket=str(getattr(deal, "ticket", "")),
                )
            )'''
new = '''            magic_raw = getattr(deal, "magic", None)
            try:
                magic = int(magic_raw) if magic_raw is not None else None
            except (TypeError, ValueError):
                magic = None
            out.append(
                ClosedTrade(
                    time=when.strftime("%Y-%m-%d %H:%M:%S"),
                    utc_day=when.strftime("%Y-%m-%d"),
                    symbol=symbol,
                    side=side,
                    volume=float(getattr(deal, "volume", 0) or 0),
                    price=float(getattr(deal, "price", 0) or 0),
                    profit=round(profit + swap + commission, 2),
                    ticket=str(getattr(deal, "ticket", "")),
                    magic=magic,
                    comment=str(getattr(deal, "comment", "") or ""),
                )
            )'''
assert old in text, 'mt5 closed_trades block missing'
mt5.write_text(text.replace(old, new, 1))
print('mt5_broker.py: magic/comment on closes')

# --- 3) paper_broker ---
paper = root / 'bot/paper_broker.py'
text = paper.read_text()
old = '''            out.append(
                ClosedTrade(
                    time=str(row.get("time", "")),
                    utc_day=str(row.get("utc_day") or str(row.get("time", ""))[:10]),
                    symbol=row.get("symbol", self.settings.symbol),
                    side=row.get("side", ""),
                    volume=float(row.get("volume", 0)),
                    price=float(row.get("price", 0)),
                    profit=float(row.get("profit", 0)),
                    ticket=str(row.get("ticket", "")),
                )
            )'''
new = '''            magic_raw = row.get("magic", self.settings.magic)
            try:
                magic = int(magic_raw) if magic_raw is not None else None
            except (TypeError, ValueError):
                magic = None
            out.append(
                ClosedTrade(
                    time=str(row.get("time", "")),
                    utc_day=str(row.get("utc_day") or str(row.get("time", ""))[:10]),
                    symbol=row.get("symbol", self.settings.symbol),
                    side=row.get("side", ""),
                    volume=float(row.get("volume", 0)),
                    price=float(row.get("price", 0)),
                    profit=float(row.get("profit", 0)),
                    ticket=str(row.get("ticket", "")),
                    magic=magic,
                    comment=str(row.get("comment") or "fomo-demo-bot"),
                )
            )'''
assert old in text, 'paper closed_trades missing'
text = text.replace(old, new, 1)

old1 = '''        self._data.setdefault("closed", []).append(
            {
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "utc_day": now.strftime("%Y-%m-%d"),
                "symbol": closed_pos["symbol"],
                "side": closed_pos["side"],
                "volume": closed_pos["volume"],
                "price": price,
                "profit": round(profit, 2),
                "ticket": ticket,
            }
        )'''
new1 = '''        self._data.setdefault("closed", []).append(
            {
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "utc_day": now.strftime("%Y-%m-%d"),
                "symbol": closed_pos["symbol"],
                "side": closed_pos["side"],
                "volume": closed_pos["volume"],
                "price": price,
                "profit": round(profit, 2),
                "ticket": ticket,
                "magic": int(closed_pos.get("magic", self.settings.magic) or self.settings.magic),
                "comment": "fomo-demo-bot",
            }
        )'''
assert old1 in text, 'paper close_one missing'
text = text.replace(old1, new1, 1)

old2 = '''            self._data.setdefault("closed", []).append(
                {
                    "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "utc_day": now.strftime("%Y-%m-%d"),
                    "symbol": pos["symbol"],
                    "side": pos["side"],
                    "volume": pos["volume"],
                    "price": price,
                    "profit": round(profit, 2),
                    "ticket": pos["ticket"],
                }
            )'''
new2 = '''            self._data.setdefault("closed", []).append(
                {
                    "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "utc_day": now.strftime("%Y-%m-%d"),
                    "symbol": pos["symbol"],
                    "side": pos["side"],
                    "volume": pos["volume"],
                    "price": price,
                    "profit": round(profit, 2),
                    "ticket": pos["ticket"],
                    "magic": int(pos.get("magic", self.settings.magic) or self.settings.magic),
                    "comment": "fomo-demo-bot",
                }
            )'''
assert old2 in text, 'paper close_all missing'
paper.write_text(text.replace(old2, new2, 1))
print('paper_broker.py: magic/comment stamped')

# --- 4) config ---
cfg = root / 'bot/config.py'
text = cfg.read_text()
if 'macd_era_start' not in text:
    text = text.replace(
        '    history_lookback_days: int = 0\n    symbols: tuple[str, ...] = ()\n',
        '    history_lookback_days: int = 0\n'
        '    # Morning MACD Bill went live on this desk (UTC). Used to exclude old Bill closes.\n'
        '    macd_era_start: str = "2026-09-03T00:00:00+00:00"\n'
        '    symbols: tuple[str, ...] = ()\n',
        1,
    )
    text = text.replace(
        '        history_lookback_days=max(0, int(os.getenv("HISTORY_LOOKBACK_DAYS", "0"))),\n    )\n',
        '        history_lookback_days=max(0, int(os.getenv("HISTORY_LOOKBACK_DAYS", "0"))),\n'
        '        macd_era_start=os.getenv("MACD_ERA_START", "2026-09-03T00:00:00+00:00").strip()\n'
        '        or "2026-09-03T00:00:00+00:00",\n'
        '    )\n',
        1,
    )
    cfg.write_text(text)
    print('config.py: macd_era_start added')
else:
    print('config.py: already has macd_era_start')

envex = root / '.env.example'
etext = envex.read_text()
if 'MACD_ERA_START' not in etext:
    envex.write_text(etext.replace(
        'HISTORY_LOOKBACK_DAYS=0\n',
        'HISTORY_LOOKBACK_DAYS=0\n'
        '# Only count MACD-strategy closes after this UTC instant (exclude old Bill era).\n'
        'MACD_ERA_START=2026-09-03T00:00:00+00:00\n',
        1,
    ))
    print('.env.example updated')

print('phase1 done')
