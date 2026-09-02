from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone

from bot.broker import AccountSnapshot, Broker
from bot.analyst import analyze, last_close, m5_to_h1, m5_to_h4, multi_tf_bias
from bot.config import STATE_PATH, Settings, load_settings
from bot.hours import fx_weekend
from bot.mt5_broker import MT5Broker
from bot.mutex import leftover_ea, python_heartbeat, python_release, status as mutex_status
from bot.wine_ipc import looks_dead, recover as recover_wine_ipc
from bot.paper_broker import PaperBroker
from bot.pnl import hole_to_recover, summarize_pnl, utc_day
from bot.risk import can_add_position, lot_for_budget, pip_size, session_drawdown_halt
from bot.state import save_state


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
        self.fill_venue = "paper" if self.settings.fill_venue != "mt5" else "mt5"
        self.feed: Broker | None = None
        self.mt5_status = self._blank_mt5("not attached yet")
        self.yielded_to_ea = False
        self._entry_blocked_until = 0.0
        self._pace_until = 0.0
        self._htf_bars = None
        self._htf_h4 = None
        self._htf_ts = 0.0
        self._htf_warned = False
        self._h1_side = "HOLD"
        self._h4_side = "HOLD"

    def _tf_frame(self, timeframe: str, m5_bars=None, count: int = 80):
        market = self._market()
        if market is not None:
            try:
                frame = market.bars(self.settings.symbol, count, timeframe=timeframe)
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
                    src = market.bars(self.settings.symbol, need)
                except Exception:
                    src = None
        if src is None:
            return None
        return m5_to_h4(src) if timeframe == "H4" else m5_to_h1(src)

    def _spread_pips(self) -> float | None:
        if self.last_bid is None or self.last_ask is None:
            return None
        return (float(self.last_ask) - float(self.last_bid)) / pip_size(self.settings.symbol)

    def _higher_tf(self, m5_bars=None) -> str:
        now = time.time()
        if self._htf_bars is not None and now - self._htf_ts <= 60:
            combined, self._h1_side, self._h4_side = multi_tf_bias(self._htf_bars, self._htf_h4)
            return combined
        h1 = self._tf_frame("H1", m5_bars)
        h4 = self._tf_frame("H4", m5_bars)
        if h1 is None or len(h1) < 25:
            if not getattr(self, "_htf_warned", False):
                self.log("H1/H4 trend unavailable — waiting for enough bars")
                self._htf_warned = True
            return "HOLD"
        if h4 is not None and len(h4) < 25:
            h4 = None
        self._htf_bars = h1
        self._htf_h4 = h4
        self._htf_ts = now
        self._htf_warned = False
        combined, self._h1_side, self._h4_side = multi_tf_bias(h1, h4)
        return combined

    def _analyze(self, bars, **kwargs):
        kwargs.setdefault("higher_tf", self._higher_tf(bars))
        kwargs.setdefault("htf_h1", getattr(self, "_h1_side", None))
        kwargs.setdefault("htf_h4", getattr(self, "_h4_side", None))
        kwargs.setdefault("now", datetime.now(timezone.utc))
        kwargs.setdefault("spread_pips", self._spread_pips())
        kwargs.setdefault("max_spread_pips", getattr(self.settings, "max_spread_pips", 2.0))
        return analyze(
            bars,
            kwargs.pop("symbol", self.settings.symbol),
            session_start_hour=self.settings.session_start_hour,
            session_end_hour=self.settings.session_end_hour,
            min_atr_pips=self.settings.min_atr_pips,
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
        return lot_for_budget(self._bankroll(), self.settings.risk_pct, sl, self.settings.max_lot)

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
                positions = [
                    {
                        "symbol": p.symbol,
                        "side": p.side,
                        "volume": p.volume,
                        "price": p.price,
                        "profit": p.profit,
                        "ticket": p.ticket,
                    }
                    for p in self.broker.positions(self.settings.symbol)
                ]
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
                    }
                    for t in self.broker.closed_trades(self._history_since_ts())
                ]
            except Exception:
                closed = []
        if not closed and self.recorded_closes:
            closed = list(reversed(self.recorded_closes[-2000:]))
        market = self._market()
        if market and not self.last_bars:
            try:
                self._cache_market(market.bars(self.settings.symbol, 80))
            except Exception:
                pass
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
        spread = None
        if self.last_bid is not None and self.last_ask is not None:
            spread = round((self.last_ask - self.last_bid) / pip_size(self.settings.symbol), 1)
        pos0 = positions[0] if positions else None
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
            "symbol": self.settings.symbol,
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
            "spread": spread,
            "hedge_pct": hedge_pct,
            "hedge_dir": hedge_dir,
            "open_count": len(positions),
            "max_positions": self.max_positions,
            "poll_seconds": self.settings.poll_seconds,
            "min_atr_pips": self.settings.min_atr_pips,
            "session_start_hour": self.settings.session_start_hour,
            "session_end_hour": self.settings.session_end_hour,
            "min_score": self.settings.min_score,
            "take_atr_mult": self.settings.take_atr_mult,
            "stop_atr_mult": self.settings.stop_atr_mult,
            "strategy": "bill",
            "min_entry_seconds": self.settings.min_entry_seconds,
            "htf": (self.last_analysis or {}).get("checks", {}).get("htf") if isinstance((self.last_analysis or {}).get("checks"), dict) else None,
            "h1": getattr(self, "_h1_side", None),
            "h4": getattr(self, "_h4_side", None),
            "max_spread_pips": getattr(self.settings, "max_spread_pips", 2.0),
            "mutex": self._mutex_status(),
            "blockers": self._blockers(),
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
            result = closer(self.settings.symbol)
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

    def step(self) -> None:
        assert self.broker is not None
        symbol = self.settings.symbol
        market = self._market()
        assert market is not None
        bars = market.bars(symbol, 200)
        self._cache_market(bars)
        self.last_price = last_close(bars)
        positions = self.broker.positions(symbol)
        live = {p.ticket for p in positions}
        self.peak_by_ticket = {k: v for k, v in self.peak_by_ticket.items() if k in live}
        has_position = bool(positions)
        mutex_block = self._sync_mutex()

        verdict = self._analyze(bars, open_count=len(positions))
        self.last_analysis = verdict.as_dict()
        self.last_signal = verdict.bias
        self.last_reason = verdict.reasons[0] if verdict.reasons else verdict.action
        if mutex_block:
            self.last_reason = mutex_block
            return

        halt = session_drawdown_halt(
            self.session_pnl,
            self._bankroll() * 0.20,
            has_position,
        )
        if halt == "CLOSE":
            before = positions
            result = self.broker.close_all(symbol)
            self.log(f"{result.message} (session loss stop)")
            self._note_closes(before)
            return
        if halt == "HOLD":
            self.last_reason = "session loss stop"
            return

        still_open = []
        for pos in positions:
            peak = max(self.peak_by_ticket.get(pos.ticket, 0.0), float(pos.profit))
            self.peak_by_ticket[pos.ticket] = peak
            one = self._analyze(
                bars,
                position_side=pos.side,
                position_profit=float(pos.profit),
                peak_profit=peak,
                position_volume=float(pos.volume),
            )
            if one.action == "CLOSE":
                result = self.broker.close_one(symbol, pos.ticket)
                why = one.reasons[0] if one.reasons else "exit"
                self.log(f"{result.message} ticket={pos.ticket} ({why})")
                self._note_closes([pos])
                self.peak_by_ticket.pop(pos.ticket, None)
                if "stop loss" in why.lower() or "cut loser" in why.lower():
                    wait = int(self.settings.stop_cooldown_minutes) * 60
                    if wait > 0:
                        self._entry_blocked_until = max(self._entry_blocked_until, time.time() + wait)
                        self.log(f"Stop-out cooldown {self.settings.stop_cooldown_minutes}m — no new tickets")
            else:
                still_open.append(pos)

        if self.settings.weekend_guard and fx_weekend():
            if still_open and self.settings.weekend_flat:
                result = self.broker.close_all(symbol)
                self.log(f"{result.message} (FX weekend flatten — no Sunday gap)")
                self._note_closes(still_open)
                still_open = []
            self.last_reason = "FX weekend — no new tickets (Fri 21:00–Sun 21:00 UTC)"
            return

        if time.time() < self._entry_blocked_until:
            left = int(self._entry_blocked_until - time.time())
            self.last_reason = f"cooldown after stop-out ({left}s left)"
            return
        if time.time() < self._pace_until:
            left = int(self._pace_until - time.time())
            self.last_reason = f"Bill pace — next ticket in {left}s"
            return

        entry = self._analyze(bars, open_count=len(still_open))
        self.last_analysis = entry.as_dict()
        self.last_signal = entry.bias
        self.last_reason = entry.reasons[0] if entry.reasons else entry.action

        blocked = can_add_position(
            still_open,
            entry.action,
            self.max_positions,
            require_pyramid_winners=False,
        )
        if blocked:
            if still_open and blocked != "no entry signal":
                self.last_reason = blocked
            return

        lot = lot_for_budget(
            self._bankroll(),
            self.settings.risk_pct,
            entry.sl_pips,
            self.settings.max_lot,
        )
        result = (
            self.broker.buy(symbol, lot, entry.sl_pips, entry.tp_pips)
            if entry.action == "BUY"
            else self.broker.sell(symbol, lot, entry.sl_pips, entry.tp_pips)
        )
        extra = f"  open {len(still_open)+1}/{self.max_positions}"
        self.log(f"{result.message} ({self.last_reason}){extra}")
        if result.ok:
            pace = int(getattr(self.settings, "min_entry_seconds", 60) or 0)
            if pace > 0:
                self._pace_until = max(self._pace_until, time.time() + pace)

    def _annotate_positions(self, positions: list[dict]) -> list[dict]:
        if not positions:
            return positions
        frame = None
        if self.last_bars and len(self.last_bars) >= 60:
            import pandas as pd

            frame = pd.DataFrame(
                [
                    {"time": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"]}
                    for r in self.last_bars
                ]
            )
        out = []
        for p in positions:
            profit = float(p.get("profit") or 0)
            note = {
                **p,
                "pnl_state": "profit" if profit > 0.004 else "loss" if profit < -0.004 else "flat",
                "verdict": "HOLD",
                "why": f"open P&L ${profit:+.2f}",
            }
            if frame is not None:
                one = self._analyze(
                    frame,
                    position_side=p.get("side"),
                    position_profit=profit,
                    peak_profit=self.peak_by_ticket.get(str(p.get("ticket")), profit),
                    position_volume=float(p.get("volume") or 0.01),
                )
                note["verdict"] = one.action
                note["why"] = one.reasons[0] if one.reasons else note["why"]
            out.append(note)
        return out

    def _cache_market(self, bars) -> None:
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
        self.last_bars = rows
        self.last_price = float(tail["close"].iloc[-1])
        market = self._market()
        if market:
            try:
                self.last_bid, self.last_ask = market.tick(self.settings.symbol)
            except Exception:
                self.last_bid = self.last_ask = self.last_price

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
                }
            )

    def loop(self) -> None:
        try:
            self.connect()
            try:
                self.probe_trade()
            except Exception as exc:
                self.log(f"Probe trade skipped: {exc}")
            while self.running:
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
                    if not self.running:
                        break
                    time.sleep(1)
        finally:
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

    def _blockers(self) -> list[str]:
        out: list[str] = []
        a = self.last_analysis or {}
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
        atr = float(a.get("atr_pips") or 0)
        if atr and atr < self.settings.min_atr_pips:
            out.append(f"auto-entry blocked: ATR {atr} pips (need {self.settings.min_atr_pips:g}+)")
        checks = a.get("checks") if isinstance(a.get("checks"), dict) else {}
        htf = str(checks.get("htf") or "")
        if htf == "HOLD":
            out.append("auto-entry blocked: H1 and H4 not aligned")
        elif htf in {"", "n/a"}:
            conf = int(a.get("confidence") or 0)
            if a and conf < self.settings.min_score:
                out.append(f"auto-entry blocked: score {conf}/8 (need {self.settings.min_score}+)")
        if a.get("action") == "HOLD" and a.get("reasons"):
            out.append(str(a["reasons"][0]))
        if self.settings.weekend_guard and fx_weekend():
            out.append("FX weekend — no new tickets (Fri 21:00–Sun 21:00 UTC)")
        if time.time() < getattr(self, "_entry_blocked_until", 0):
            left = int(self._entry_blocked_until - time.time())
            out.append(f"cooldown after stop-out ({left}s left)")
        if time.time() < getattr(self, "_pace_until", 0):
            left = int(self._pace_until - time.time())
            out.append(f"Bill pace — next ticket in {left}s")
        mutex = self._mutex_status()
        if leftover_ea() or mutex.get("ea_active") or mutex.get("leftover_ea"):
            out.append("old trading EA still on the chart — Refresh Navigator; Python is the only bot")
        return out[:8]

    def set_max_positions(self, n: int) -> dict:
        self.max_positions = max(1, min(8, int(n)))
        self.log(f"UI set max open tickets to {self.max_positions}")
        self.persist()
        return {"ok": True, "max_positions": self.max_positions, "message": f"max tickets {self.max_positions}"}

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

    def _current_positions(self):
        if not self.broker:
            return []
        return self.broker.positions(self.settings.symbol)

    def _manual_fill(self, side: str) -> dict:
        if not self.running:
            return {"ok": False, "message": "bot is stopped — press START"}
        if not self.broker:
            return {"ok": False, "message": "broker offline"}
        with self._lock:
            mutex_block = self._sync_mutex()
            if mutex_block:
                return {"ok": False, "message": mutex_block}
            spots = self._current_positions()
            blocked = can_add_position(
                spots,
                side,
                self.max_positions,
                require_pyramid_winners=False,
            )
            if blocked:
                return {"ok": False, "message": blocked}
            sl = float((self.last_analysis or {}).get("sl_pips") or self.settings.sl_pips)
            tp = float((self.last_analysis or {}).get("tp_pips") or self.settings.tp_pips)
            lot = self._next_lot()
            result = (
                self.broker.buy(self.settings.symbol, lot, sl, tp)
                if side == "BUY"
                else self.broker.sell(self.settings.symbol, lot, sl, tp)
            )
            self.log(f"UI {side} {result.message}")
            self.persist()
            return {"ok": result.ok, "message": result.message, "ticket": result.ticket}

    def manual_buy(self) -> dict:
        return self._manual_fill("BUY")

    def manual_sell(self) -> dict:
        return self._manual_fill("SELL")

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
            before = [p for p in self._current_positions() if p.ticket == ticket]
            result = self.broker.close_one(self.settings.symbol, ticket)
            if result.ok:
                self._note_closes(before)
                self.peak_by_ticket.pop(ticket, None)
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
            before = self._current_positions()
            result = self.broker.close_all(self.settings.symbol)
            if result.ok:
                self._note_closes(before)
                self.peak_by_ticket.clear()
            self.log(f"UI close all: {result.message}")
            self.persist()
            return {"ok": result.ok, "message": result.message}
