from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bot.broker import AccountSnapshot, ClosedTrade, OrderResult, Position
from bot.config import Settings
from bot.risk import pip_size

WINDOWS_TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# Official MT5 ENUM_TIMEFRAMES. mt5_mac.TIMEFRAME_H1 is 60, which is not PERIOD_H1.
_MT5_TIMEFRAME = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}


def explain_trade_retcode(retcode, comment: str = "") -> str:
    code = int(retcode) if retcode is not None else -1
    if code == 10027:
        return "Algo Trading is OFF in MetaTrader. Click Algo Trading in the toolbar so it turns green, then try again."
    if code == 10028:
        return "The demo server disabled AutoTrading on this account."
    extra = f" {comment}".rstrip() if comment else ""
    return f"retcode={retcode}{extra}"


def _prepare_macos_bridge(app_path: str) -> str:
    app = Path(app_path)
    if not app.exists():
        raise RuntimeError(f"MetaTrader 5.app not found at {app}")
    wine_dir = app / "Contents" / "SharedSupport" / "wine" / "bin"
    wine = wine_dir / "wine"
    wine64 = wine_dir / "wine64"
    if not wine64.exists() and wine.exists():
        wine64.symlink_to("wine")
    wine_bin = str(wine64 if wine64.exists() else wine)

    import mt5_mac.backend as backend
    import mt5_mac.bootstrap as bootstrap

    backend.MT5_APP = str(app)
    backend.WINE_BIN = wine_bin
    bootstrap.MT5_APP = str(app)
    bootstrap.WINE_BIN = wine_bin
    bootstrap.GET_PIP_URL = "https://bootstrap.pypa.io/pip/3.9/get-pip.py"
    agent = Path(__file__).resolve().parent / "wine_mt5_agent.py"
    backend.AGENT_PATH = str(agent)
    backend.WINE_AGENT_PATH = "Z:" + str(agent).replace("/", "\\")
    from bot.wine_ipc import install_send_timeout

    install_send_timeout()
    return wine_bin


def _load_mt5(app_path: str):
    _prepare_macos_bridge(app_path)
    try:
        import mt5_mac as mt5
    except ImportError as exc:
        raise RuntimeError("mt5-mac is not installed") from exc
    return mt5


class MT5Broker:
    name = "mt5-demo"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.mt5 = _load_mt5(settings.mt5_path)
        self._connected = False

    def connect(self) -> AccountSnapshot:
        mt5 = self.mt5
        os.environ["MT5_TIMEOUT"] = "120000"
        os.environ["MT5_LOGIN"] = str(self.settings.mt5_login)
        os.environ["MT5_PASSWORD"] = self.settings.mt5_password
        os.environ["MT5_SERVER"] = self.settings.mt5_server
        os.environ["MT5_TERMINAL_PATH"] = WINDOWS_TERMINAL

        def _err() -> str:
            try:
                import mt5_mac

                backend = getattr(mt5_mac, "_backend", None)
                if backend is None:
                    return "no backend"
                got = backend._send({"action": "last_error"}) or {}
                return str(got.get("error") or got)
            except Exception as exc:
                return str(exc)

        try:
            ok = mt5.initialize(
                path=WINDOWS_TERMINAL,
                login=self.settings.mt5_login,
                password=self.settings.mt5_password,
                server=self.settings.mt5_server,
                timeout=120000,
            )
            if not ok:
                detail = _err()
                mt5.shutdown()
                raise RuntimeError(
                    "Could not attach to MetaTrader 5 "
                    f"({detail}). Keep one demo terminal open, Algo Trading green, then RETRY MT5."
                )
            info = mt5.account_info()
            if info is None or int(info.login) != int(self.settings.mt5_login):
                ok = mt5.login(
                    self.settings.mt5_login,
                    self.settings.mt5_password,
                    self.settings.mt5_server,
                )
                if not ok:
                    detail = _err()
                    mt5.shutdown()
                    raise RuntimeError(
                        f"MT5 login failed ({detail}). Is the demo password in .env correct?"
                    )

            info = mt5.account_info()
            if info is None:
                mt5.shutdown()
                raise RuntimeError("MT5 connected but account_info() is empty")
            trade_mode = getattr(info, "trade_mode", 0)
            if trade_mode not in (0, "ACCOUNT_TRADE_MODE_DEMO", None) and "demo" not in self.settings.mt5_server.lower():
                mt5.shutdown()
                raise RuntimeError("Refusing to trade a non-demo MT5 account")

            self._connected = True
            try:
                import mt5_mac

                backend = getattr(mt5_mac, "_backend", None)
                if backend is not None:
                    backend._fomo_owner = self
            except Exception:
                pass
            mt5.symbol_select(self.settings.symbol, True)
            return self.refresh_account()
        except Exception:
            try:
                mt5.shutdown()
            except Exception:
                pass
            raise

    def refresh_account(self) -> AccountSnapshot:
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 account_info() is empty")
        return AccountSnapshot(
            login=str(info.login),
            server=info.server,
            broker=getattr(info, "company", "MetaQuotes"),
            connected=True,
            trade_mode="demo",
            currency=info.currency,
            balance=float(info.balance),
            equity=float(info.equity),
            credit=float(getattr(info, "credit", 0) or 0),
            note="Connected to MetaQuotes demo. Position size uses this demo equity.",
            trade_allowed=bool(getattr(info, "trade_allowed", True)),
            trade_expert=bool(getattr(info, "trade_expert", True)),
        )

    def closed_trades(self, since_ts: int) -> list[ClosedTrade]:
        now = int(datetime.now(timezone.utc).timestamp())
        deals = self.mt5.history_deals_get(since_ts, now) or []
        out = []
        for deal in deals:
            if getattr(deal, "entry", 0) not in (1, 2):
                continue
            symbol = getattr(deal, "symbol", self.settings.symbol)
            if symbol and symbol != self.settings.symbol:
                continue
            profit = float(getattr(deal, "profit", 0) or 0)
            swap = float(getattr(deal, "swap", 0) or 0)
            commission = float(getattr(deal, "commission", 0) or 0)
            side = "SELL" if getattr(deal, "type", 0) == 1 else "BUY"
            when = datetime.fromtimestamp(int(deal.time), timezone.utc)
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
                )
            )
        return list(reversed(out[-2000:]))

    def disconnect(self) -> None:
        # mt5_mac uses one global backend. Do not shutdown if a newer broker replaced us.
        if not self._connected:
            return
        self._connected = False
        try:
            import mt5_mac

            backend = getattr(mt5_mac, "_backend", None)
            if backend is not None and getattr(backend, "_fomo_owner", None) is self:
                self.mt5.shutdown()
        except Exception:
            pass

    def bars(self, symbol: str, count: int = 200, timeframe: str = "M5") -> pd.DataFrame:
        from bot.analyst import m5_to_h1

        mt5 = self.mt5
        try:
            mt5.symbol_select(symbol, True)
        except Exception:
            pass
        tf_name = (timeframe or "M5").upper()
        tf = _MT5_TIMEFRAME.get(tf_name, 5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(list(rates))
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            return df
        if tf_name == "H1":
            m5 = mt5.copy_rates_from_pos(symbol, _MT5_TIMEFRAME["M5"], 0, max(count * 12, 400))
            if m5 is not None and len(m5) > 0:
                df = pd.DataFrame(list(m5))
                df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                hourly = m5_to_h1(df)
                if len(hourly) >= 25:
                    return hourly.tail(count).reset_index(drop=True)
        raise RuntimeError(f"No MT5 bars for {symbol} {timeframe}")

    def tick(self, symbol: str) -> tuple[float, float]:
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {symbol}")
        return float(tick.bid), float(tick.ask)

    def positions(self, symbol: str) -> list[Position]:
        raw = self.mt5.positions_get(symbol=symbol) or []
        out = []
        for pos in raw:
            side = "BUY" if pos.type == self.mt5.ORDER_TYPE_BUY else "SELL"
            out.append(
                Position(
                    symbol=pos.symbol,
                    side=side,
                    volume=float(pos.volume),
                    price=float(pos.price_open),
                    profit=float(pos.profit),
                    ticket=str(pos.ticket),
                )
            )
        return out

    def _filling_modes(self):
        mt5 = self.mt5
        modes = []
        for name in ("ORDER_FILLING_RETURN", "ORDER_FILLING_FOK", "ORDER_FILLING_IOC"):
            if hasattr(mt5, name):
                modes.append(getattr(mt5, name))
        return modes or [1, 0, 2]

    def _send(self, side: str, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult:
        mt5 = self.mt5
        info = mt5.symbol_info(symbol)
        if info is None:
            return OrderResult(False, f"{symbol} not in Market Watch")
        if not info.visible:
            mt5.symbol_select(symbol, True)

        bid, ask = self.tick(symbol)
        price = ask if side == "BUY" else bid
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        point = pip_size(symbol)
        sl = price - sl_pips * point if side == "BUY" else price + sl_pips * point
        tp = price + tp_pips * point if side == "BUY" else price - tp_pips * point

        last_error = "order_send failed"
        for filling in self._filling_modes():
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(lot),
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": self.settings.magic,
                "comment": "fomo-demo-bot",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            result = self._order_send(request)
            if result.ok:
                return OrderResult(True, f"MT5 {side} {lot} {symbol} @ {result.price}", result.ticket, result.price)
            last_error = result.message
        return OrderResult(False, last_error)

    def _explain_retcode(self, retcode, comment: str) -> str:
        return explain_trade_retcode(retcode, comment)

    def _order_send(self, request: dict) -> OrderResult:
        mt5 = self.mt5
        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, str(mt5.last_error()))
        retcode = getattr(result, "retcode", None)
        ok_codes = {getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "RES_E_SUCCESS", 0), 10009, 10008}
        if retcode in ok_codes:
            price = float(getattr(result, "price", 0) or 0)
            ticket = str(getattr(result, "order", "") or getattr(result, "deal", ""))
            return OrderResult(True, "ok", ticket, price)
        return OrderResult(False, self._explain_retcode(retcode, str(getattr(result, "comment", "") or "")))

    def buy(self, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult:
        return self._send("BUY", symbol, lot, sl_pips, tp_pips)

    def sell(self, symbol: str, lot: float, sl_pips: float, tp_pips: float) -> OrderResult:
        return self._send("SELL", symbol, lot, sl_pips, tp_pips)

    def close_one(self, symbol: str, ticket: str) -> OrderResult:
        mt5 = self.mt5
        positions = mt5.positions_get(symbol=symbol) or []
        match = next((p for p in positions if str(p.ticket) == str(ticket)), None)
        if match is None:
            return OrderResult(False, f"MT5 ticket {ticket} not found")
        return self._close_position(symbol, match)

    def close_all(self, symbol: str) -> OrderResult:
        return self._close_many(symbol, None)

    def close_bot_positions(self, symbol: str) -> OrderResult:
        return self._close_many(symbol, int(self.settings.magic))

    def _close_many(self, symbol: str, magic: int | None) -> OrderResult:
        mt5 = self.mt5
        positions = mt5.positions_get(symbol=symbol) or []
        if magic is not None:
            positions = [p for p in positions if int(getattr(p, "magic", 0) or 0) == magic]
        if not positions:
            return OrderResult(True, "no MT5 positions" if magic is None else "no bot MT5 positions")
        errors = []
        closed = 0
        for pos in positions:
            result = self._close_position(symbol, pos)
            if result.ok:
                closed += 1
            else:
                errors.append(result.message)
        if errors:
            return OrderResult(False, f"close errors: {errors}")
        return OrderResult(True, f"closed {closed} MT5 position(s)")

    def _close_position(self, symbol: str, pos) -> OrderResult:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(symbol)
        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type, price = mt5.ORDER_TYPE_SELL, tick.bid
        else:
            close_type, price = mt5.ORDER_TYPE_BUY, tick.ask
        last = "close failed"
        for filling in self._filling_modes():
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(pos.volume),
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": self.settings.magic,
                "comment": "fomo-demo-close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            }
            result = self._order_send(request)
            if result.ok:
                return OrderResult(True, f"closed MT5 {symbol} {pos.ticket}")
            last = result.message
        return OrderResult(False, last)
