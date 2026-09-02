from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd


@dataclass
class AccountSnapshot:
    login: str
    server: str
    broker: str
    connected: bool
    trade_mode: str
    currency: str
    balance: float
    equity: float
    credit: float = 0.0
    note: str = ""
    virtual_bankroll: float = 0.0
    trade_allowed: bool = True
    trade_expert: bool = True


@dataclass
class Position:
    symbol: str
    side: str
    volume: float
    price: float
    profit: float
    ticket: str = ""


@dataclass
class OrderResult:
    ok: bool
    message: str
    ticket: str = ""
    price: float = 0.0


@dataclass
class BrokerStatus:
    name: str
    connected: bool
    detail: str
    extras: dict = field(default_factory=dict)


@dataclass
class ClosedTrade:
    time: str
    symbol: str
    side: str
    volume: float
    price: float
    profit: float
    ticket: str = ""
    utc_day: str = ""


class Broker(Protocol):
    name: str

    def connect(self) -> AccountSnapshot: ...
    def disconnect(self) -> None: ...
    def refresh_account(self) -> AccountSnapshot: ...
    def bars(self, symbol: str, count: int = 200, timeframe: str = "M5") -> pd.DataFrame: ...
    def tick(self, symbol: str) -> tuple[float, float]: ...
    def positions(self, symbol: str) -> list[Position]: ...
    def closed_trades(self, since_ts: int) -> list[ClosedTrade]: ...
    def buy(self, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult: ...
    def sell(self, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult: ...
    def close_all(self, symbol: str) -> OrderResult: ...
    def close_one(self, symbol: str, ticket: str) -> OrderResult: ...
