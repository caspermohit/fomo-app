from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from bot.broker import AccountSnapshot, ClosedTrade, OrderResult, Position
from bot.config import Settings
from bot.risk import is_gold, pip_size, pip_value_per_lot

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class PaperBroker:
    name = "paper"

    def __init__(self, settings: Settings, store: Path, note: str | None = None, seed_balance: float | None = None):
        self.settings = settings
        self.store = store
        self._note = note or (
            "MT5 terminal was not reachable, so this session uses paper fills on live EURUSD prices."
        )
        self._data = {
            "balance": 0.0,
            "positions": [],
            "trades": [],
            "closed": [],
        }
        if store.exists():
            self._data.update(json.loads(store.read_text()))
        stored = float(self._data.get("balance") or 0)
        # Old $50 dummy book. Paper should track demo-sized cash, not that leftover.
        if seed_balance is not None and stored < 1000:
            self._data["balance"] = round(float(seed_balance), 2)
            self._save()

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(self._data, indent=2))

    def connect(self) -> AccountSnapshot:
        return self.refresh_account()

    def refresh_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            login=str(self.settings.mt5_login),
            server=self.settings.mt5_server,
            broker="paper (live FX prices, simulated fills)",
            connected=True,
            trade_mode="demo",
            currency="USD",
            balance=float(self._data["balance"]),
            equity=self._equity(),
            note=self._note,
        )

    def disconnect(self) -> None:
        self._save()

    def _yahoo_chart(self, symbol: str, interval: str, range_: str) -> dict:
        ticker = f"{symbol}=X"
        r = requests.get(
            YAHOO_CHART.format(ticker=ticker),
            params={"interval": interval, "range": range_},
            headers={"User-Agent": "Mozilla/5.0 fomo-demo-bot"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def bars(self, symbol: str, count: int = 200, timeframe: str = "M5") -> pd.DataFrame:
        tf_name = (timeframe or "M5").upper()
        if tf_name == "H1":
            payload = self._yahoo_chart(symbol, "60m", "60d")
        elif tf_name == "H4":
            payload = self._yahoo_chart(symbol, "60m", "60d")
        else:
            payload = self._yahoo_chart(symbol, "5m", "5d")
        result = payload["chart"]["result"][0]
        quotes = result["indicators"]["quote"][0]
        rows = []
        for ts, o, h, low, c in zip(
            result["timestamp"],
            quotes["open"],
            quotes["high"],
            quotes["low"],
            quotes["close"],
        ):
            if None in (o, h, low, c):
                continue
            rows.append(
                {
                    "time": datetime.fromtimestamp(ts, timezone.utc),
                    "open": float(o),
                    "high": float(h),
                    "low": float(low),
                    "close": float(c),
                }
            )
        df = pd.DataFrame(rows)
        if (timeframe or "M5").upper() == "H4":
            from bot.analyst import m5_to_h4

            df = m5_to_h4(df)
        return df.tail(count).reset_index(drop=True)

    def tick(self, symbol: str) -> tuple[float, float]:
        payload = self._yahoo_chart(symbol, "1m", "1d")
        meta = payload["chart"]["result"][0]["meta"]
        price = float(meta.get("regularMarketPrice") or meta.get("previousClose"))
        # Gold really trades ~40 pips ($0.40) wide; FX ~1.2 pips.
        spread = pip_size(symbol) * (40.0 if is_gold(symbol) else 1.2)
        return price - spread / 2, price + spread / 2

    def _equity(self) -> float:
        equity = float(self._data["balance"])
        if not self._data["positions"]:
            return equity
        for pos in self._data["positions"]:
            bid, ask = self.tick(pos["symbol"])
            price = bid if pos["side"] == "BUY" else ask
            equity += self._pnl(pos, price)
        return round(equity, 2)

    def _pnl(self, pos: dict, price: float) -> float:
        direction = 1 if pos["side"] == "BUY" else -1
        pair = pos.get("symbol") or self.settings.symbol
        pips = (price - pos["price"]) / pip_size(pair) * direction
        return pips * pos["volume"] * pip_value_per_lot(pair)

    def positions(self, symbol: str) -> list[Position]:
        out = []
        for pos in self._data["positions"]:
            if pos.get("symbol") != symbol:
                continue
            bid, ask = self.tick(symbol)
            price = bid if pos["side"] == "BUY" else ask
            out.append(
                Position(
                    symbol=pos["symbol"],
                    side=pos["side"],
                    volume=pos["volume"],
                    price=pos["price"],
                    profit=round(self._pnl(pos, price), 2),
                    ticket=pos["ticket"],
                    magic=int(pos.get("magic", self.settings.magic)),
                )
            )
        return out

    def closed_trades(self, since_ts: int) -> list[ClosedTrade]:
        out = []
        for row in reversed(self._data.get("closed", [])):
            magic_raw = row.get("magic", self.settings.magic)
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
            )
        return out

    def _fill(self, side: str, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult:
        bid, ask = self.tick(symbol)
        price = ask if side == "BUY" else bid
        ticket = f"PAPER-{time.time_ns()}"
        self._data["positions"].append(
            {
                "symbol": symbol,
                "side": side,
                "volume": lot,
                "price": price,
                "ticket": ticket,
                "magic": int(self.settings.magic),
                "sl_pips": sl_pips,
                "tp_pips": tp_pips,
            }
        )
        self._data["trades"].append(
            {"time": datetime.now(timezone.utc).isoformat(), "side": side, "price": price, "lot": lot, "ticket": ticket}
        )
        self._save()
        return OrderResult(True, f"paper {side} {lot} {symbol} @ {price:.5f}", ticket, price)

    def buy(self, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult:
        return self._fill("BUY", symbol, lot, sl_pips, tp_pips)

    def sell(self, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult:
        return self._fill("SELL", symbol, lot, sl_pips, tp_pips)

    def close_one(self, symbol: str, ticket: str) -> OrderResult:
        keep = []
        closed_pos = None
        for pos in self._data["positions"]:
            if pos.get("ticket") == ticket:
                closed_pos = pos
            else:
                keep.append(pos)
        if closed_pos is None:
            return OrderResult(False, f"paper ticket {ticket} not found")
        bid, ask = self.tick(symbol)
        price = bid if closed_pos["side"] == "BUY" else ask
        profit = self._pnl(closed_pos, price)
        now = datetime.now(timezone.utc)
        self._data.setdefault("closed", []).append(
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
        )
        self._data["balance"] = round(float(self._data["balance"]) + profit, 2)
        self._data["positions"] = keep
        self._save()
        return OrderResult(True, f"closed paper {closed_pos['side']} {ticket} pnl {profit:+.2f}")

    def close_all(self, symbol: str) -> OrderResult:
        keep = []
        to_close = []
        for pos in self._data["positions"]:
            if pos.get("symbol") == symbol:
                to_close.append(pos)
            else:
                keep.append(pos)
        if not to_close:
            return OrderResult(True, f"no paper {symbol} positions")
        closed = 0.0
        for pos in to_close:
            bid, ask = self.tick(pos["symbol"])
            price = bid if pos["side"] == "BUY" else ask
            profit = self._pnl(pos, price)
            closed += profit
            now = datetime.now(timezone.utc)
            self._data.setdefault("closed", []).append(
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
            )
        self._data["balance"] = round(float(self._data["balance"]) + closed, 2)
        self._data["positions"] = keep
        self._save()
        return OrderResult(True, f"closed paper {symbol} positions, pnl {closed:+.2f}")
