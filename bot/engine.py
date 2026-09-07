from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone

from bot.broker import AccountSnapshot, Broker
from bot.analyst import analyze, m5_to_h1, m5_to_h4, multi_tf_bias
from bot.config import BILL_PAIRS, STATE_PATH, Settings, load_settings
from bot.hours import fx_weekend
from bot.mt5_broker import MT5Broker
from bot.mutex import leftover_ea, python_heartbeat, python_release, status as mutex_status
from bot.wine_ipc import looks_dead, recover as recover_wine_ipc
from bot.paper_broker import PaperBroker
from bot.pnl import hole_to_recover, summarize_pnl, utc_day
from bot.risk import can_add_position, lot_for_budget, pip_size, pip_value_per_lot, session_drawdown_halt
from bot.state import save_state


# MACD Bill rewrite morning (UTC). Env MACD_ERA_START overrides via Settings.macd_era_start.
_MACD_COMMENT_MARKERS = ("fomo-demo",)


def _parse_close_ts(trade: dict) -> float:
    """UTC epoch for a closed-trade dict. Missing/unparseable -> 0."""
    raw = str(trade.get("time") or "").strip()
    if not raw:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(raw.replace("Z", "+00:00") if fmt.endswith("%z") else raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _macd_era_ts(settings: Settings) -> float:
    raw = str(getattr(settings, "macd_era_start", "") or "2026-09-03T00:00:00+00:00").strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return datetime(2026, 9, 3, tzinfo=timezone.utc).timestamp()


def is_macd_strategy_close(trade: dict, *, magic: int, era_start_ts: float) -> bool:
    """True for closes belonging to this MACD bot era (not mixed old Bill history).

    Prefer magic 26082601 + comment containing fomo-demo (open/close markers).
    Always require close time >= MACD era start so pre-rewrite same-magic Bill
    tickets are excluded even when comment is missing from history.
    """
    if _parse_close_ts(trade) < float(era_start_ts):
        return False
    comment = str(trade.get("comment") or "").lower()
    comment_ok = any(m in comment for m in _MACD_COMMENT_MARKERS)
    magic_raw = trade.get("magic")
    magic_ok = False
    if magic_raw is not None and str(magic_raw).strip() != "":
        try:
            magic_ok = int(magic_raw) == int(magic)
        except (TypeError, ValueError):
            magic_ok = False
    if magic_ok and comment_ok:
        return True
    if magic_ok:
        return True
    if comment_ok:
        return True
    # History without magic/comment: post-cutoff closes on desk pairs still count.
    if magic_raw is None and not comment:
        return True
    return False


def macd_win_stats(closes: list[dict], *, magic: int, era_start_ts: float, symbol: str | None = None) -> dict:
    """Win rate over MACD-era closes. win_rate is None when fills==0 (UI shows n/a)."""
    rows = [t for t in closes if is_macd_strategy_close(t, magic=magic, era_start_ts=era_start_ts)]
    if symbol:
        sym = str(symbol).upper()
        rows = [t for t in rows if str(t.get("symbol") or "").upper() == sym]
    fills = len(rows)
    wins = sum(1 for t in rows if float(t.get("profit") or 0) > 0)
    net = round(sum(float(t.get("profit") or 0) for t in rows), 2)
    return {
        "macd_fills": fills,
        "macd_wins": wins,
        "macd_win_rate": round(100.0 * wins / fills, 1) if fills else None,
        "macd_net": net if fills else None,
    }


class BotEngine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.broker: Broker | None = None
        self.account: AccountSnapshot | None = None
        self.running = False
        self.probe_done = True
        self.session_pnl = 0.0
        self.logs: deque[str] = deque(maxlen=200)
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_signal = "HOLD"
        self.last_reason = "starting"
        self.last_price = None
        self.last_analysis: dict = {}
        self.peak_by_ticket: dict[str, float] = {}
        self.broker_name = "offline"
        self.start_equity = 0.0
        self.session_started_ts = int(datetime.now(timezone.utc).timestamp())
        self.recorded_closes: list[dict] = []
        self.last_bars: list[dict] = []
        self.last_bid = None
        self.last_ask = None
        self.max_positions = int(self.settings.max_positions)
        self.profit_arm_usd = float(self.settings.profit_arm_usd)
        self.profit_giveback_pct = float(self.settings.profit_giveback_pct)
        self.fill_venue = "paper" if self.settings.fill_venue != "mt5" else "mt5"
        self.feed: Broker | None = None
        self.mt5_status = self._blank_mt5("not attached yet")
        self.yielded_to_ea = False
        self._entry_blocked_until = 0.0
        self._pace_until = 0.0
        self._books: dict[str, dict] = {}
        self.focus_symbol = self.settings.pairs[0]
        self._htf_bars = None
        self._htf_h4 = None
        self._htf_ts = 0.0
        self._htf_warned = False
        self._h1_side = "HOLD"
        self._h4_side = "HOLD"
        self._exits_by_ticket: dict[str, tuple[float, float]] = {}
        self._usd_jpy_cache: tuple[float, float] | None = None
        self._pip_value_cache: dict[str, tuple[float, float]] = {}
        self._macd_by_symbol: dict[str, dict] = {}

    def _pairs(self) -> tuple[str, ...]:
        return self.settings.pairs

    def _book(self, symbol: str) -> dict:
        slot = self._books.get(symbol)
        if slot is None:
            slot = {
                "pace_until": 0.0,
                "blocked_until": 0.0,
                "h1": "HOLD",
                "h4": "HOLD",
                "htf_bars": None,
                "htf_h4": None,
                "htf_ts": 0.0,
                "htf_warned": False,
                "bars": [],
                "bid": None,
                "ask": None,
                "analysis": {},
                "reason": "starting",
                "signal": "HOLD",
                "price": None,
                "last_cross_bar": "",
            }
            self._books[symbol] = slot
        return slot

    def _publish_focus(self) -> None:
        book = self._book(self.focus_symbol)
        self.last_bars = list(book.get("bars") or [])
        self.last_bid = book.get("bid")
        self.last_ask = book.get("ask")
        self.last_price = book.get("price")
        self.last_analysis = dict(book.get("analysis") or {})
        self.last_reason = str(book.get("reason") or "")
        self.last_signal = str(book.get("signal") or "HOLD")
        self._h1_side = book.get("h1") or "HOLD"
        self._h4_side = book.get("h4") or "HOLD"

    def _all_positions(self) -> list:
        if not self.broker:
            return []
        out = []
        for pair in self._pairs():
            try:
                out.extend(self.broker.positions(pair))
            except Exception:
                pass
        return out

    def _tf_frame(self, timeframe: str, m5_bars=None, count: int = 80, symbol: str | None = None):
        pair = symbol or self.focus_symbol
        market = self._market()
        if market is not None:
            try:
                frame = market.bars(pair, count, timeframe=timeframe)
                if frame is not None and len(frame) >= 25:
                    return frame
            except Exception:
                pass
        src = m5_bars
        need = 1200 if timeframe == "H4" else 400
        if src is None or len(src) < 60:
            src = None
            if market is not None:
                try:
                    src = market.bars(pair, need)
                except Exception:
                    src = None
        if src is None:
            return None
        return m5_to_h4(src) if timeframe == "H4" else m5_to_h1(src)

    def _spread_pips(self, symbol: str | None = None) -> float | None:
        pair = symbol or self.focus_symbol
        book = self._book(pair)
        bid, ask = book.get("bid"), book.get("ask")
        if bid is None or ask is None:
            if pair == self.focus_symbol and self.last_bid is not None and self.last_ask is not None:
                bid, ask = self.last_bid, self.last_ask
            else:
                return None
        return (float(ask) - float(bid)) / pip_size(pair)

    def _higher_tf(self, m5_bars=None, symbol: str | None = None) -> str:
        pair = symbol or self.focus_symbol
        book = self._book(pair)
        now = time.time()
        if book["htf_bars"] is not None and now - book["htf_ts"] <= 60:
            combined, book["h1"], book["h4"] = multi_tf_bias(book["htf_bars"], book["htf_h4"])
            return combined
        h1 = self._tf_frame("H1", m5_bars, symbol=pair)
        h4 = self._tf_frame("H4", m5_bars, symbol=pair)
        if h1 is None or len(h1) < 25:
            if not book["htf_warned"]:
                self.log(f"{pair} H1/H4 trend unavailable — waiting for enough bars")
                book["htf_warned"] = True
            return "HOLD"
        if h4 is not None and len(h4) < 25:
            h4 = None
        book["htf_bars"] = h1
        book["htf_h4"] = h4
        book["htf_ts"] = now
        book["htf_warned"] = False
        combined, book["h1"], book["h4"] = multi_tf_bias(h1, h4)
        return combined

    def _usd_jpy(self) -> float | None:
        now = time.time()
        if self._usd_jpy_cache and now - self._usd_jpy_cache[0] <= 60:
            return self._usd_jpy_cache[1]
        market = self._market()
        if market is None:
            return None
        try:
            bid, ask = market.tick("USDJPY")
            mid = (float(bid) + float(ask)) / 2.0
            if 50 < mid < 250:
                self._usd_jpy_cache = (now, mid)
                return mid
        except Exception:
            pass
        return self._usd_jpy_cache[1] if self._usd_jpy_cache else None

    def _symbol_specs(self, symbol: str) -> dict:
        """Contract spec through the broker layer (tick value/size, contract size)."""
        market = self._market()
        if market is None:
            return {}
        getter = getattr(market, "symbol_specs", None)
        if getter is not None:
            try:
                return dict(getter(symbol) or {})
            except Exception:
                return {}
        mt5 = getattr(market, "mt5", None)
        if mt5 is None:
            return {}
        try:
            info = mt5.symbol_info(symbol)
        except Exception:
            return {}
        if info is None:
            return {}

        def pick(*names):
            for name in names:
                raw = info.get(name) if isinstance(info, dict) else getattr(info, name, None)
                try:
                    num = float(raw)
                except (TypeError, ValueError):
                    continue
                if num > 0:
                    return num
            return None

        return {
            "tick_value": pick("trade_tick_value", "tick_value"),
            "tick_size": pick("trade_tick_size", "tick_size", "point"),
            "contract_size": pick("trade_contract_size", "contract_size"),
        }

    def _pip_value(self, symbol: str) -> float:
        """USD per pip per 1.00 lot. Real broker numbers first, convention second.

        XAUUSD is not forex: 100 oz contract x $0.01 pip = $1.00 per lot, so the
        $10 FX pip value and the JPY formula must never be used for it.
        """
        now = time.time()
        hit = self._pip_value_cache.get(symbol)
        if hit and now - hit[0] <= 60:
            return hit[1]
        specs = self._symbol_specs(symbol)
        val = pip_value_per_lot(
            symbol,
            usd_jpy=self._usd_jpy(),
            tick_value=specs.get("tick_value"),
            tick_size=specs.get("tick_size"),
            contract_size=specs.get("contract_size"),
        )
        self._pip_value_cache[symbol] = (now, val)
        return val

    def _analyze(self, bars, **kwargs):
        pair = kwargs.pop("symbol", None) or self.focus_symbol
        kwargs.setdefault("higher_tf", self._higher_tf(bars, symbol=pair))
        book = self._book(pair)
        kwargs.setdefault("htf_h1", book.get("h1"))
        kwargs.setdefault("htf_h4", book.get("h4"))
        kwargs.setdefault("now", datetime.now(timezone.utc))
        kwargs.setdefault("spread_pips", self._spread_pips(pair))
        kwargs.setdefault("max_spread_pips", self.settings.spread_cap(pair))
        kwargs.setdefault("usd_jpy", self._usd_jpy())
        kwargs.setdefault("pip_value", self._pip_value(pair))
        return analyze(
            bars,
            pair,
            session_start_hour=self.settings.session_start_hour,
            session_end_hour=self.settings.session_end_hour,
            session_start_minute=self.settings.session_start_minute,
            session_end_minute=self.settings.session_end_minute,
            min_atr_pips=self.settings.atr_floor(pair),
            min_score=self.settings.min_score,
            take_atr_mult=self.settings.take_atr_mult,
            stop_atr_mult=self.settings.stop_atr_mult,
            **kwargs,
        )

    def _market(self) -> Broker | None:
        return self.feed or self.broker

    def _bankroll(self) -> float:
        eq = self.mt5_status.get("equity")
        if eq:
            return float(eq)
        if self.account:
            return float(self.account.equity)
        return 0.0

    def _next_lot(self) -> float:
        sl = float((self.last_analysis or {}).get("sl_pips") or self.settings.sl_pips)
        pair = self.focus_symbol
        return lot_for_budget(
            self._bankroll(),
            self.settings.risk_pct,
            sl,
            self.settings.max_lot,
            symbol=pair,
            pip_value=self._pip_value(pair),
        )

    def _history_since_ts(self) -> int:
        days = int(getattr(self.settings, "history_lookback_days", 0) or 0)
        if days <= 0:
            return 0
        return int(time.time()) - days * 86400

    def log(self, message: str) -> None:
        line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')}  {message}"
        self.logs.appendleft(line)
        print(line, flush=True)

    def snapshot(self) -> dict:
        positions = []
        closed = []
        if self.feed and self.feed is not self.broker:
            try:
                mt5_acct = self.feed.refresh_account()
                self._remember_mt5(mt5_acct)
            except Exception:
                pass
        if self.broker:
            try:
                self.account = self.broker.refresh_account()
                if self.broker_name.startswith("mt5") and self.account:
                    self._remember_mt5(self.account)
            except Exception:
                pass
            try:
                positions = []
                for pair in self._pairs():
                    positions.extend(
                        {
                            "symbol": p.symbol,
                            "side": p.side,
                            "volume": p.volume,
                            "price": p.price,
                            "profit": p.profit,
                            "ticket": p.ticket,
                            "magic": getattr(p, "magic", None),
                        }
                        for p in self.broker.positions(pair)
                    )
                positions = self._annotate_positions(positions)
            except Exception:
                positions = []
            try:
                closed = [
                    {
                        "time": t.time,
                        "utc_day": getattr(t, "utc_day", "") or str(t.time)[:10],
                        "symbol": t.symbol,
                        "side": t.side,
                        "volume": t.volume,
                        "price": t.price,
                        "profit": t.profit,
                        "ticket": t.ticket,
                        "magic": getattr(t, "magic", None),
                        "comment": getattr(t, "comment", "") or "",
                    }
                    for t in self.broker.closed_trades(self._history_since_ts())
                ]
            except Exception:
                closed = []
        if not closed and self.recorded_closes:
            closed = list(reversed(self.recorded_closes[-2000:]))
        market = self._market()
        if market:
            for pair in self._pairs():
                if not self._book(pair).get("bars"):
                    try:
                        self._cache_market(market.bars(pair, 250), pair)
                    except Exception:
                        pass
        self._publish_focus()
        account = self.account
        floating = round(sum(float(p["profit"]) for p in positions), 2)
        recent = closed[:20]
        closed_sum = round(sum(float(t["profit"]) for t in recent), 2)
        history_net = round(sum(float(t["profit"]) for t in closed), 2)
        equity = float(account.equity) if account else 0.0
        start = self.start_equity or equity
        pnl = summarize_pnl(start, equity, floating)
        deposit = float(self.settings.demo_start_balance)
        pnl["start_deposit"] = round(deposit, 2)
        hole_eq = float(self.mt5_status.get("equity") or 0) or equity
        pnl["recover"] = hole_to_recover(deposit, hole_eq)
        pnl["closed"] = closed_sum
        pnl["today"] = round(closed_sum + floating, 2)
        pnl["history_net"] = history_net
        pnl["history_count"] = len(closed)
        if float(pnl["today"]) > 0.004:
            pnl["status"] = "profit"
        elif float(pnl["today"]) < -0.004:
            pnl["status"] = "loss"
        else:
            pnl["status"] = "flat"
        self.session_pnl = float(pnl["today"])
        wins = sum(1 for t in recent if float(t.get("profit", 0)) > 0)
        fills = len(recent)
        era_ts = _macd_era_ts(self.settings)
        macd_stats = macd_win_stats(
            closed,
            magic=int(self.settings.magic),
            era_start_ts=era_ts,
        )
        macd_by_symbol = {
            pair: macd_win_stats(
                closed,
                magic=int(self.settings.magic),
                era_start_ts=era_ts,
                symbol=pair,
            )
            for pair in self._pairs()
        }
        self._macd_by_symbol = macd_by_symbol
        spread = None
        focus_book = self._book(self.focus_symbol)
        if focus_book.get("bid") is not None and focus_book.get("ask") is not None:
            spread = round((focus_book["ask"] - focus_book["bid"]) / pip_size(self.focus_symbol), 1)
        elif self.last_bid is not None and self.last_ask is not None:
            spread = round((self.last_ask - self.last_bid) / pip_size(self.focus_symbol), 1)
        pos0 = next((p for p in positions if p.get("symbol") == self.focus_symbol), None) or (positions[0] if positions else None)
        buy_vol = sum(float(p.get("volume") or 0) for p in positions if p.get("side") == "BUY")
        sell_vol = sum(float(p.get("volume") or 0) for p in positions if p.get("side") == "SELL")
        net = buy_vol - sell_vol
        hedge_pct = 0
        hedge_dir = "FLAT"
        if pos0:
            total_vol = max(buy_vol + sell_vol, 0.0001)
            hedge_pct = int(round(100 * abs(net) / total_vol))
            hedge_dir = "UP" if net > 0 else "DOWN" if net < 0 else "FLAT"
        elif self.last_analysis.get("bias") in {"BUY", "SELL"}:
            hedge_pct = int(round(100 * int(self.last_analysis.get("confidence") or 0) / 8))
            hedge_dir = "UP" if self.last_analysis.get("bias") == "BUY" else "DOWN"
        return {
            "running": self.running,
            "broker": self.broker_name,
            "trade_mode": self.settings.trade_mode,
            "symbol": self.focus_symbol,
            "symbols": list(self._pairs()),
            "focus": self.focus_symbol,
            "books": self._books_public(),
            "bankroll": self._bankroll(),
            "next_lot": self._next_lot(),
            "risk_pct": self.settings.risk_pct,
            "max_lot": self.settings.max_lot,
            "login": self.settings.mt5_login,
            "server": self.settings.mt5_server,
            "connected": bool(self.mt5_status.get("attached")),
            "mt5": dict(self.mt5_status),
            "venue": self.broker_name,
            "fill_venue": self.fill_venue,
            "account": None
            if account is None
            else {
                "login": account.login,
                "server": account.server,
                "broker": account.broker,
                "currency": account.currency,
                "balance": account.balance,
                "equity": account.equity,
                "credit": getattr(account, "credit", 0),
                "note": account.note,
            },
            "last_signal": self.last_signal,
            "last_reason": self.last_reason,
            "last_price": self.last_price,
            "analysis": self.last_analysis,
            "session_pnl": self.session_pnl,
            "pnl": pnl,
            "closed_trades": closed,
            "positions": positions,
            "bars": self.last_bars[-80:],
            "bid": self.last_bid,
            "ask": self.last_ask,
            "fills": fills,
            "wins": wins,
            "win_rate": round(100.0 * wins / fills, 1) if fills else 0.0,
            "net_edge": round(closed_sum / fills, 2) if fills else 0.0,
            "macd_fills": macd_stats["macd_fills"],
            "macd_wins": macd_stats["macd_wins"],
            "macd_win_rate": macd_stats["macd_win_rate"],
            "macd_net": macd_stats["macd_net"],
            "macd_by_symbol": macd_by_symbol,
            "spread": spread,
            "hedge_pct": hedge_pct,
            "hedge_dir": hedge_dir,
            "open_count": len(positions),
            "max_positions": self.max_positions,
            "max_positions_total": self.max_positions,
            "profit_arm_usd": self.profit_arm_usd,
            "profit_giveback_pct": self.profit_giveback_pct,
            "poll_seconds": self.settings.poll_seconds,
            "min_atr_pips": self.settings.atr_floor(self.focus_symbol),
            "min_atr_pips_xau": self.settings.min_atr_pips_xau,
            "max_spread_pips_xau": self.settings.max_spread_pips_xau,
            "pip_value": self._pip_value(self.focus_symbol),
            "session_start_hour": self.settings.session_start_hour,
            "session_end_hour": self.settings.session_end_hour,
            "session_start_minute": self.settings.session_start_minute,
            "session_end_minute": self.settings.session_end_minute,
            "min_score": self.settings.min_score,
            "take_atr_mult": self.settings.take_atr_mult,
            "stop_atr_mult": self.settings.stop_atr_mult,
            "strategy": "macd",
            "min_entry_seconds": self.settings.min_entry_seconds,
            "htf": (focus_book.get("analysis") or {}).get("checks", {}).get("htf") if isinstance((focus_book.get("analysis") or {}).get("checks"), dict) else None,
            "h1": focus_book.get("h1"),
            "h4": focus_book.get("h4"),
            "max_spread_pips": self.settings.spread_cap(self.focus_symbol),
            "max_spread_pips_jpy": self.settings.max_spread_pips_jpy,
            "mutex": self._mutex_status(),
            "blockers": self._blockers(self.focus_symbol),
            "logs": list(self.logs),
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _mutex_status(self) -> dict:
        info = mutex_status()
        info["required"] = bool(self.settings.mutex_required)
        info["yielded_to_ea"] = bool(self.yielded_to_ea)
        return info

    def _sync_mutex(self) -> str | None:
        self.yielded_to_ea = False
        if self.settings.mutex_required:
            python_heartbeat()
        return None

    def persist(self) -> None:
        save_state(STATE_PATH, self.snapshot())

    def _paper_broker(self, mt5_attached: bool) -> PaperBroker:
        note = (
            "MT5 is quotes + demo balance only. Fills are PAPER and will not hit the demo account."
            if mt5_attached
            else "MT5 terminal was not reachable, so this session uses paper fills on live EURUSD prices."
        )
        seed = self._bankroll() or float(self.settings.demo_start_balance)
        if seed < 1000:
            seed = float(self.settings.demo_start_balance)
        return PaperBroker(self.settings, STATE_PATH.parent / "paper.json", note=note, seed_balance=seed)

    def _drop_venues(self) -> None:
        seen: list[Broker] = []
        for venue in (self.broker, self.feed):
            if venue is None or venue in seen:
                continue
            seen.append(venue)
            try:
                venue.disconnect()
            except Exception:
                pass
        self.broker = None
        self.feed = None
        self.broker_name = "offline"

    def _flatten_bot_mt5(self) -> None:
        feed = self.feed
        if feed is None or self.fill_venue != "paper":
            return
        closer = getattr(feed, "close_bot_positions", None) or getattr(feed, "close_all", None)
        if closer is None:
            return
        try:
            for pair in self._pairs():
                result = closer(pair)
                if result and "no " not in (result.message or "").lower():
                    self.log(result.message)
        except Exception as exc:
            self.log(f"Could not flatten leftover MT5 demo tickets: {exc}")

    def connect(self) -> None:
        self.log("Connecting to MetaQuotes-Demo (demo equity, not live money)...")
        self._drop_venues()
        mt5_acct = None
        try:
            feed: Broker = MT5Broker(self.settings)
            mt5_acct = feed.connect()
            self.feed = feed
            self._remember_mt5(mt5_acct)
            self.log(
                f"MT5 attached  login={mt5_acct.login}  "
                f"demo_balance={mt5_acct.balance:.2f} {mt5_acct.currency}  "
                f"credit={mt5_acct.credit:.2f}"
            )
        except Exception as exc:
            self.feed = None
            self.mt5_status = self._blank_mt5(str(exc))
            self.log(f"MT5 connect failed: {exc}")

        mt5_ok = self.feed is not None and mt5_acct is not None
        if self.fill_venue == "mt5" and mt5_ok and self.feed is not None:
            self.broker = self.feed
            self.broker_name = self.feed.name
            self.account = mt5_acct
            self._remember_mt5(mt5_acct)
            self.log(
                f"Fills will hit the MetaQuotes demo account. "
                f"Sizing uses demo equity {mt5_acct.equity:.2f} {mt5_acct.currency} at {self.settings.risk_pct:.0%} risk."
            )
        else:
            if self.fill_venue == "mt5" and not mt5_ok:
                self.fill_venue = "paper"
                self.log("Cannot send fills to MT5 — using paper.")
            self._flatten_bot_mt5()
            paper = self._paper_broker(mt5_attached=mt5_ok)
            self.account = paper.connect()
            self.broker = paper
            self.broker_name = paper.name
            if mt5_ok:
                self.log(
                    "Orders are PAPER. They will NOT hit the MetaTrader demo account. "
                    "MT5 is quotes + demo balance only."
                )
            else:
                self.log(
                    "Falling back to paper broker with live EURUSD prices. "
                    "These fills will NOT show in the MetaTrader app."
                )
            self.log(f"Paper broker ready  virtual ${self.account.balance:.2f}")
            if mt5_acct is not None:
                self._remember_mt5(mt5_acct)
        if self.account:
            self.start_equity = float(self.account.equity)
            now = datetime.now(timezone.utc)
            self.session_started_ts = int(
                datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()
            )
        self.persist()

    def probe_trade(self) -> None:
        return

    def _is_bot_position(self, position) -> bool:
        magic = getattr(position, "magic", None)
        if magic is not None:
            return int(magic or 0) == int(self.settings.magic)
        return str(getattr(position, "ticket", "")) in {str(k) for k in self._exits_by_ticket}

    def _apply_profit_lock(self, positions) -> list:
        remaining = []
        for pos in positions:
            ticket = str(pos.ticket)
            profit = float(pos.profit)
            if self._is_bot_position(pos):
                peak = max(float(self.peak_by_ticket.get(ticket, profit)), profit)
                self.peak_by_ticket[ticket] = peak
                armed = peak >= self.profit_arm_usd
                giveback = armed and profit <= peak * (1.0 - self.profit_giveback_pct)
                if giveback:
                    result = self.broker.close_one(pos.symbol, pos.ticket)
                    if result.ok:
                        self.log(f"{pos.symbol} ticket={pos.ticket} profit peak giveback {profit:+.2f} from {peak:+.2f}: {result.message}")
                        self._note_closes([pos])
                        self.peak_by_ticket.pop(ticket, None)
                        self._exits_by_ticket.pop(ticket, None)
                        continue
                    self.log(f"{pos.symbol} ticket={pos.ticket} profit giveback close failed: {result.message}")
            remaining.append(pos)
        return remaining

    def step(self) -> None:
        assert self.broker is not None
        market = self._market()
        assert market is not None
        pairs = self._pairs()
        all_pos = self._all_positions()
        live = {p.ticket for p in all_pos}
        self.peak_by_ticket = {k: v for k, v in self.peak_by_ticket.items() if k in live}
        mutex_block = self._sync_mutex()
        if mutex_block:
            for pair in pairs:
                self._book(pair)["reason"] = mutex_block
            self._publish_focus()
            return

        all_pos = self._apply_profit_lock(all_pos)

        halt = session_drawdown_halt(
            self.session_pnl,
            self._bankroll() * 0.20,
            bool(all_pos),
        )
        if halt == "CLOSE":
            for pair in pairs:
                before = self.broker.positions(pair)
                result = self.broker.close_all(pair)
                self.log(f"{result.message} (session loss stop)")
                self._note_closes(before)
            return
        if halt == "HOLD":
            for pair in pairs:
                self._book(pair)["reason"] = "session loss stop"
            self._publish_focus()
            return

        if self.settings.weekend_guard and fx_weekend():
            for pair in pairs:
                still = self.broker.positions(pair)
                if still and self.settings.weekend_flat:
                    result = self.broker.close_all(pair)
                    self.log(f"{result.message} (FX weekend flatten — no Sunday gap)")
                    self._note_closes(still)
                self._book(pair)["reason"] = "FX weekend — no new tickets (Fri 21:00–Sun 21:00 UTC)"
            self._publish_focus()
            return

        for pair in pairs:
            self._step_symbol(pair)
        self._publish_focus()

    def _step_symbol(self, symbol: str) -> None:
        assert self.broker is not None
        market = self._market()
        assert market is not None
        book = self._book(symbol)
        bars = market.bars(symbol, 250)
        self._cache_market(bars, symbol)
        positions = self.broker.positions(symbol)
        verdict = self._analyze(bars, open_count=len(positions), symbol=symbol)
        book["analysis"] = verdict.as_dict()
        book["signal"] = verdict.bias
        book["reason"] = verdict.reasons[0] if verdict.reasons else verdict.action

        still_open = []
        for pos in positions:
            peak = max(self.peak_by_ticket.get(pos.ticket, 0.0), float(pos.profit))
            self.peak_by_ticket[pos.ticket] = peak
            frozen = self._exits_by_ticket.get(pos.ticket)
            one = self._analyze(
                bars,
                symbol=symbol,
                position_side=pos.side,
                position_profit=float(pos.profit),
                peak_profit=peak,
                position_volume=float(pos.volume),
                position_sl_pips=frozen[0] if frozen else None,
                position_tp_pips=frozen[1] if frozen else None,
            )
            if one.action == "CLOSE":
                result = self.broker.close_one(symbol, pos.ticket)
                why = one.reasons[0] if one.reasons else "exit"
                self.log(f"{result.message} ticket={pos.ticket} ({why})")
                self._note_closes([pos])
                self.peak_by_ticket.pop(pos.ticket, None)
                self._exits_by_ticket.pop(pos.ticket, None)
                if "stop loss" in why.lower() or "cut loser" in why.lower():
                    wait = int(self.settings.stop_cooldown_minutes) * 60
                    if wait > 0:
                        book["blocked_until"] = max(float(book["blocked_until"]), time.time() + wait)
                        self.log(f"{symbol} stop-out cooldown {self.settings.stop_cooldown_minutes}m — no new tickets")
            else:
                still_open.append(pos)

        if time.time() < float(book["blocked_until"]):
            left = int(book["blocked_until"] - time.time())
            book["reason"] = f"{symbol} cooldown after stop-out ({left}s left)"
            return
        if time.time() < float(book["pace_until"]):
            left = int(book["pace_until"] - time.time())
            book["reason"] = f"{symbol} MACD pace — next ticket in {left}s"
            return

        entry = self._analyze(bars, open_count=len(still_open), symbol=symbol)
        book["analysis"] = entry.as_dict()
        book["signal"] = entry.bias
        book["reason"] = entry.reasons[0] if entry.reasons else entry.action

        if len(self._all_positions()) >= self.max_positions:
            book["reason"] = f"{symbol} already at {self.max_positions} total open tickets"
            return

        blocked = can_add_position(
            still_open,
            entry.action,
            1,
            require_pyramid_winners=True,
        )
        if blocked:
            if still_open and blocked != "no entry signal":
                book["reason"] = f"{symbol} {blocked}"
            return

        cross_bar = ""
        if isinstance(entry.checks, dict):
            cross_bar = str(entry.checks.get("cross_bar") or "")
        if cross_bar and book.get("last_cross_bar") == cross_bar:
            book["reason"] = f"{symbol} already used MACD cross on {cross_bar}"
            return

        lot = lot_for_budget(
            self._bankroll(),
            self.settings.risk_pct,
            entry.sl_pips,
            self.settings.max_lot,
            symbol=symbol,
            pip_value=self._pip_value(symbol),
        )
        result = (
            self.broker.buy(symbol, lot, entry.sl_pips, entry.tp_pips)
            if entry.action == "BUY"
            else self.broker.sell(symbol, lot, entry.sl_pips, entry.tp_pips)
        )
        extra = f"  open {len(still_open)+1}/{self.max_positions}"
        self.log(f"{result.message} ({book['reason']}){extra}")
        if result.ok:
            if result.ticket:
                self._exits_by_ticket[result.ticket] = (float(entry.sl_pips), float(entry.tp_pips))
            if cross_bar:
                book["last_cross_bar"] = cross_bar
            pace = int(getattr(self.settings, "min_entry_seconds", 300) or 0)
            if pace > 0:
                book["pace_until"] = max(float(book["pace_until"]), time.time() + pace)

    def _annotate_positions(self, positions: list[dict]) -> list[dict]:
        if not positions:
            return positions
        frames: dict[str, object] = {}
        out = []
        for p in positions:
            pair = str(p.get("symbol") or self.focus_symbol)
            profit = float(p.get("profit") or 0)
            note = {
                **p,
                "pnl_state": "profit" if profit > 0.004 else "loss" if profit < -0.004 else "flat",
                "verdict": "HOLD",
                "why": f"open P&L ${profit:+.2f}",
            }
            if pair not in frames:
                rows = self._book(pair).get("bars") or []
                if len(rows) >= 60:
                    import pandas as pd

                    frames[pair] = pd.DataFrame(
                        [
                            {"time": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"]}
                            for r in rows
                        ]
                    )
                else:
                    frames[pair] = None
            frame = frames[pair]
            if frame is not None:
                one = self._analyze(
                    frame,
                    symbol=pair,
                    position_side=p.get("side"),
                    position_profit=profit,
                    peak_profit=self.peak_by_ticket.get(str(p.get("ticket")), profit),
                    position_volume=float(p.get("volume") or 0.01),
                )
                note["verdict"] = one.action
                note["why"] = one.reasons[0] if one.reasons else note["why"]
            out.append(note)
        return out

    def _cache_market(self, bars, symbol: str | None = None) -> None:
        pair = symbol or self.focus_symbol
        if bars is None or bars.empty:
            return
        tail = bars.tail(80)
        rows = []
        for rec in tail.to_dict("records"):
            rows.append(
                {
                    "t": str(rec.get("time", "")),
                    "o": float(rec["open"]),
                    "h": float(rec["high"]),
                    "l": float(rec["low"]),
                    "c": float(rec["close"]),
                }
            )
        book = self._book(pair)
        book["bars"] = rows
        book["price"] = float(tail["close"].iloc[-1])
        market = self._market()
        if market:
            try:
                book["bid"], book["ask"] = market.tick(pair)
            except Exception:
                book["bid"] = book["ask"] = book["price"]
        if pair == self.focus_symbol:
            self.last_bars = rows
            self.last_price = book["price"]
            self.last_bid = book["bid"]
            self.last_ask = book["ask"]

    def _books_public(self) -> dict:
        out = {}
        for pair in self._pairs():
            book = self._book(pair)
            spread = None
            if book.get("bid") is not None and book.get("ask") is not None:
                spread = round((float(book["ask"]) - float(book["bid"])) / pip_size(pair), 1)
            open_n = 0
            if self.broker:
                try:
                    open_n = len(self.broker.positions(pair))
                except Exception:
                    pass
            analysis = book.get("analysis") or {}
            checks = analysis.get("checks") if isinstance(analysis.get("checks"), dict) else {}
            pair_macd = (getattr(self, "_macd_by_symbol", {}) or {}).get(pair) or {}
            out[pair] = {
                "h1": book.get("h1"),
                "h4": book.get("h4"),
                "htf": checks.get("htf"),
                "analysis": analysis,
                "bid": book.get("bid"),
                "ask": book.get("ask"),
                "spread": spread,
                "last_price": book.get("price"),
                "last_reason": book.get("reason"),
                "last_signal": book.get("signal"),
                "bars": (book.get("bars") or [])[-80:],
                "open_count": open_n,
                "blockers": self._blockers(pair),
                "max_spread_pips": self.settings.spread_cap(pair),
                "min_atr_pips": self.settings.atr_floor(pair),
                "pip_value": self._pip_value(pair),
                "macd_fills": pair_macd.get("macd_fills", 0),
                "macd_wins": pair_macd.get("macd_wins", 0),
                "macd_win_rate": pair_macd.get("macd_win_rate"),
                "macd_net": pair_macd.get("macd_net"),
            }
        return out

    def _note_closes(self, positions) -> None:
        now = datetime.now(timezone.utc)
        for p in positions or []:
            self.recorded_closes.append(
                {
                    "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "utc_day": utc_day(now),
                    "symbol": p.symbol,
                    "side": p.side,
                    "volume": p.volume,
                    "price": p.price,
                    "profit": round(float(p.profit), 2),
                    "ticket": p.ticket,
                    "magic": int(getattr(p, "magic", None) or self.settings.magic),
                    "comment": "fomo-demo-bot",
                }
            )

    def loop(self) -> None:
        me = threading.current_thread()
        try:
            self.connect()
            try:
                self.probe_trade()
            except Exception as exc:
                self.log(f"Probe trade skipped: {exc}")
            while self.running and me is self.thread:
                try:
                    with self._lock:
                        self.step()
                except Exception as exc:
                    self.log(f"step error: {exc}")
                    self.log(traceback.format_exc().splitlines()[-1])
                    if looks_dead(exc):
                        self.log("Wine IPC dead — reaping hung agents, then reconnecting")
                        recover_wine_ipc()
                        recovered = self.retry_mt5()
                        if not recovered.get("ok"):
                            self.log(f"Wine recover failed: {recovered.get('message')}")
                self.persist()
                for _ in range(self.settings.poll_seconds):
                    if not self.running or me is not self.thread:
                        break
                    time.sleep(1)
        finally:
            if me is self.thread:
                python_release()
                self.yielded_to_ea = False
                self._drop_venues()
                self.running = False
                self.persist()
                self.log("Bot stopped.")

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self.running = True
            if self.settings.mutex_required:
                python_heartbeat()
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.running = False
        python_release()
        self.yielded_to_ea = False
        self.log("Stop requested from UI")

    def health(self) -> dict:
        from bot.health import build_report

        return build_report(self)

    def _blank_mt5(self, error: str = "") -> dict:
        return {
            "attached": False,
            "login": self.settings.mt5_login,
            "server": self.settings.mt5_server,
            "company": None,
            "currency": None,
            "balance": None,
            "equity": None,
            "credit": None,
            "error": error,
            "fills_in_terminal": False,
            "trade_allowed": None,
            "trade_expert": None,
        }

    def _remember_mt5(self, account) -> None:
        fills = self.fill_venue == "mt5" and self.broker_name.startswith("mt5")
        self.mt5_status = {
            "attached": True,
            "login": account.login,
            "server": account.server,
            "company": account.broker,
            "currency": account.currency,
            "balance": float(account.balance),
            "equity": float(account.equity),
            "credit": float(getattr(account, "credit", 0) or 0),
            "error": None,
            "fills_in_terminal": fills,
            "trade_allowed": bool(getattr(account, "trade_allowed", True)),
            "trade_expert": bool(getattr(account, "trade_expert", True)),
        }

    def retry_mt5(self) -> dict:
        if not self.running:
            return {"ok": False, "message": "bot is stopped — press START"}
        self.log("Retry MetaTrader attach...")
        try:
            with self._lock:
                recover_wine_ipc(force=True)
                feed: Broker = MT5Broker(self.settings)
                acct = feed.connect()
                old_feed = self.feed
                self.feed = feed
                if old_feed is not None and old_feed is not feed:
                    old_feed._connected = False
                if self.fill_venue == "mt5":
                    old = self.broker
                    self.broker = feed
                    self.broker_name = feed.name
                    self.account = acct
                    if old is not None and old is not feed:
                        old._connected = False
                    self._remember_mt5(acct)
                    msg = (
                        f"MT5 connected · fills go to demo {acct.balance:.2f} {acct.currency}"
                        + (f" · credit {acct.credit:.2f}" if acct.credit else "")
                    )
                else:
                    self._flatten_bot_mt5()
                    self._remember_mt5(acct)
                    msg = (
                        f"MT5 connected for prices · demo {acct.balance:.2f} {acct.currency}"
                        + (f" · credit {acct.credit:.2f}" if acct.credit else "")
                        + " · orders stay PAPER and will not hit the demo account"
                    )
                self.log(msg)
                self.persist()
                return {"ok": True, "message": msg}
        except Exception as exc:
            self.mt5_status = self._blank_mt5(str(exc))
            self.log(f"MT5 retry failed: {exc}")
            return {"ok": False, "message": str(exc)}

    def _blockers(self, symbol: str | None = None) -> list[str]:
        pair = symbol or self.focus_symbol
        out: list[str] = []
        book = self._book(pair)
        a = book.get("analysis") or self.last_analysis or {}
        if not self.running:
            out.append("bot is stopped — press START")
        if self.broker is None:
            out.append("broker is not connected yet")
        elif self.broker_name == "paper" and self.mt5_status.get("attached"):
            out.append("orders are PAPER — they will not hit the MetaTrader demo account")
        elif self.broker_name == "paper":
            out.append("MT5 did not attach — paper fills only")
        if self.mt5_status.get("attached") and self.mt5_status.get("fills_in_terminal"):
            if self.mt5_status.get("trade_allowed") is False:
                out.append("Algo Trading is OFF in MetaTrader — click Algo Trading so it is green")
        checks = a.get("checks") if isinstance(a.get("checks"), dict) else {}
        ema200 = str(checks.get("ema200") or "")
        cross = str(checks.get("macd_cross") or "")
        if ema200 in {"flat", ""}:
            out.append(f"{pair} auto-entry blocked: waiting for EMA200 trend")
        if cross in {"HOLD", ""}:
            out.append(f"{pair} auto-entry blocked: no fresh MACD 12/26/9 cross")
        if a.get("action") == "HOLD" and a.get("reasons"):
            out.append(f"{pair}: {a['reasons'][0]}")
        if self.settings.weekend_guard and fx_weekend():
            out.append("FX weekend — no new tickets (Fri 21:00–Sun 21:00 UTC)")
        if time.time() < float(book.get("blocked_until") or 0):
            left = int(book["blocked_until"] - time.time())
            out.append(f"{pair} cooldown after stop-out ({left}s left)")
        if time.time() < float(book.get("pace_until") or 0):
            left = int(book["pace_until"] - time.time())
            out.append(f"{pair} MACD pace — next ticket in {left}s")
        mutex = self._mutex_status()
        if leftover_ea() or mutex.get("ea_active") or mutex.get("leftover_ea"):
            out.append("old trading EA still on the chart — Refresh Navigator; Python is the only bot")
        return out[:8]

    def set_max_positions(self, n: int) -> dict:
        self.max_positions = max(1, min(2, int(n)))
        self.log(f"UI set max open tickets total to {self.max_positions}")
        self.persist()
        return {"ok": True, "max_positions": self.max_positions, "message": f"max tickets {self.max_positions} total"}

    def set_focus(self, symbol: str) -> dict:
        pair = str(symbol or "").strip().upper()
        if pair not in self._pairs():
            return {"ok": False, "message": f"desk only trades {', '.join(self._pairs())}"}
        self.focus_symbol = pair
        self._publish_focus()
        self.persist()
        return {"ok": True, "focus": pair, "message": f"desk focus {pair}"}

    def set_fill_venue(self, venue: str) -> dict:
        wanted = "mt5" if str(venue).strip().lower() == "mt5" else "paper"
        with self._lock:
            if wanted == "mt5":
                if not self.mt5_status.get("attached") or self.feed is None:
                    return {"ok": False, "message": "attach MetaTrader first — RETRY MT5"}
                old = self.broker
                self.fill_venue = "mt5"
                self.broker = self.feed
                self.broker_name = self.feed.name
                try:
                    self.account = self.feed.refresh_account()
                except Exception:
                    pass
                if self.account:
                    self.start_equity = float(self.account.equity)
                    self._remember_mt5(self.account)
                if old is not None and old is not self.feed:
                    try:
                        old.disconnect()
                    except Exception:
                        pass
                self.log("UI switched fills to the MetaQuotes demo account.")
                self.persist()
                return {
                    "ok": True,
                    "fill_venue": "mt5",
                    "message": "fills now hit the MT5 demo account",
                }

            self.fill_venue = "paper"
            self._flatten_bot_mt5()
            paper = self._paper_broker(mt5_attached=bool(self.mt5_status.get("attached")))
            acct = paper.connect()
            old = self.broker
            self.broker = paper
            self.broker_name = paper.name
            self.account = acct
            self.start_equity = float(acct.equity)
            if self.feed is not None:
                try:
                    self._remember_mt5(self.feed.refresh_account())
                except Exception:
                    self.mt5_status["fills_in_terminal"] = False
            if old is not None and old is not self.feed:
                try:
                    old.disconnect()
                except Exception:
                    pass
            self.log("UI switched fills to PAPER. Orders will not hit the MetaTrader demo account.")
            self.persist()
            return {
                "ok": True,
                "fill_venue": "paper",
                "message": "fills are PAPER — they will not hit the demo account",
            }

    def force_step(self) -> dict:
        if not self.running:
            return {"ok": False, "message": "bot is stopped — press START"}
        if not self.broker:
            return {"ok": False, "message": "broker offline"}
        with self._lock:
            try:
                self.step()
                self.persist()
                return {"ok": True, "message": self.last_reason, "analysis": self.last_analysis}
            except Exception as exc:
                return {"ok": False, "message": str(exc)}

    def _resolve_pair(self, symbol: str | None = None) -> str:
        pair = str(symbol or self.focus_symbol or self.settings.symbol).strip().upper()
        if pair not in self._pairs():
            return self.focus_symbol
        return pair

    def _current_positions(self, symbol: str | None = None):
        if not self.broker:
            return []
        return self.broker.positions(self._resolve_pair(symbol))

    def _manual_fill(self, side: str, symbol: str | None = None) -> dict:
        if not self.running:
            return {"ok": False, "message": "bot is stopped — press START"}
        if not self.broker:
            return {"ok": False, "message": "broker offline"}
        pair = self._resolve_pair(symbol)
        with self._lock:
            mutex_block = self._sync_mutex()
            if mutex_block:
                return {"ok": False, "message": mutex_block}
            spots = self._current_positions(pair)
            if len(self._all_positions()) >= self.max_positions:
                return {"ok": False, "message": f"already {self.max_positions}/{self.max_positions} total open trades"}
            blocked = can_add_position(
                spots,
                side,
                self.max_positions,
                require_pyramid_winners=True,
            )
            if blocked:
                return {"ok": False, "message": f"{pair} {blocked}"}
            analysis = self._book(pair).get("analysis") or self.last_analysis or {}
            sl = float(analysis.get("sl_pips") or self.settings.sl_pips)
            tp = float(analysis.get("tp_pips") or self.settings.tp_pips)
            lot = lot_for_budget(
                self._bankroll(),
                self.settings.risk_pct,
                sl,
                self.settings.max_lot,
                symbol=pair,
                pip_value=self._pip_value(pair),
            )
            result = (
                self.broker.buy(pair, lot, sl, tp)
                if side == "BUY"
                else self.broker.sell(pair, lot, sl, tp)
            )
            if result.ok and result.ticket:
                self._exits_by_ticket[result.ticket] = (sl, tp)
            self.log(f"UI {side} {result.message}")
            self.persist()
            return {"ok": result.ok, "message": result.message, "ticket": result.ticket}

    def manual_buy(self, symbol: str | None = None) -> dict:
        return self._manual_fill("BUY", symbol)

    def manual_sell(self, symbol: str | None = None) -> dict:
        return self._manual_fill("SELL", symbol)

    def manual_close(self, ticket: str) -> dict:
        if not self.running:
            return {"ok": False, "message": "bot is stopped — press START"}
        if not self.broker:
            return {"ok": False, "message": "broker offline"}
        if not ticket:
            return {"ok": False, "message": "ticket required"}
        with self._lock:
            mutex_block = self._sync_mutex()
            if mutex_block:
                return {"ok": False, "message": mutex_block}
            found = next((p for p in self._all_positions() if p.ticket == ticket), None)
            pair = found.symbol if found is not None else self.focus_symbol
            before = [found] if found is not None else []
            result = self.broker.close_one(pair, ticket)
            if result.ok:
                self._note_closes(before)
                self.peak_by_ticket.pop(ticket, None)
                self._exits_by_ticket.pop(ticket, None)
            self.log(f"UI close {ticket}: {result.message}")
            self.persist()
            return {"ok": result.ok, "message": result.message}

    def manual_close_all(self) -> dict:
        if not self.running:
            return {"ok": False, "message": "bot is stopped — press START"}
        if not self.broker:
            return {"ok": False, "message": "broker offline"}
        with self._lock:
            mutex_block = self._sync_mutex()
            if mutex_block:
                return {"ok": False, "message": mutex_block}
            pairs = tuple(dict.fromkeys((*self._pairs(), *BILL_PAIRS)))
            before = []
            for pair in pairs:
                try:
                    before.extend(self.broker.positions(pair))
                except Exception:
                    pass
            messages = []
            ok = True
            for pair in pairs:
                result = self.broker.close_all(pair)
                ok = ok and bool(result.ok)
                messages.append(result.message)
            if ok:
                self._note_closes(before)
                self.peak_by_ticket.clear()
                self._exits_by_ticket.clear()
            text = " · ".join(messages)
            self.log(f"UI close all: {text}")
            self.persist()
            return {"ok": ok, "message": text}
