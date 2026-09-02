"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { drawCandles, drawSpark } from "@/lib/charts";
import { money, push, sessionPnl, tone } from "@/lib/format";
import { emptyHistory, type ApiResult, type BotState, type HealthReport, type UnlockStatus } from "@/lib/types";

export default function Dashboard() {
  const [mounted, setMounted] = useState(false);
  const [state, setState] = useState<BotState | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [clock, setClock] = useState("00:00:00 UTC");
  const [flash, setFlash] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [maxPosDraft, setMaxPosDraft] = useState("8");
  const [gate, setGate] = useState<"checking" | "locked" | "open">("checking");
  const [pinRequired, setPinRequired] = useState(false);
  const [pin, setPin] = useState("");
  const [pinError, setPinError] = useState("");
  const hist = useRef(emptyHistory());
  const candlesRef = useRef<HTMLCanvasElement>(null);
  const sparkRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/state", { credentials: "include" });
      if (res.status === 401) {
        setGate("locked");
        setPinRequired(true);
        return;
      }
      if (!res.ok) return;
      const next = (await res.json()) as BotState;
      const a = next.analysis || {};
      if (next.last_price) push(hist.current.px, next.last_price);
      if (a.rsi != null) push(hist.current.rsi, a.rsi);
      if (a.atr_pips != null) push(hist.current.atr, a.atr_pips);
      setClock(new Date().toISOString().slice(11, 19) + " UTC");
      setState(next);
    } catch {
      // bot API is down; keep last paint
    }
  }, []);

  const loadHealth = useCallback(async () => {
    try {
      const res = await fetch("/api/health", { credentials: "include" });
      if (res.status === 401) {
        setGate("locked");
        setPinRequired(true);
        return;
      }
      const next = (await res.json()) as HealthReport;
      setHealth(next);
    } catch {
      setHealth({
        status: "CRITICAL",
        checks: {
          trade_mode_safe: false,
          mt5_process_running: false,
          ipc_loop_responsive: false,
          mutex_lock_clear: false,
        },
        metrics: { ipc_latency_ms: 0, active_zombies_detected: 0 },
        warnings: ["Flask /api/health is unreachable"],
        error_reason: "API unreachable",
      });
    }
  }, []);

  useEffect(() => {
    let stop = false;
    (async () => {
      try {
        const res = await fetch("/api/unlock", { credentials: "include" });
        const next = (await res.json()) as UnlockStatus;
        if (stop) return;
        setPinRequired(Boolean(next.required));
        setGate(next.required && !next.unlocked ? "locked" : "open");
      } catch {
        if (!stop) setGate("open");
      }
    })();
    return () => {
      stop = true;
    };
  }, []);

  useEffect(() => {
    if (gate !== "open") return;
    load();
    loadHealth();
    const id = setInterval(load, 1000);
    const hid = setInterval(loadHealth, 5000);
    return () => {
      clearInterval(id);
      clearInterval(hid);
    };
  }, [gate, load, loadHealth]);

  useEffect(() => {
    if (!state) return;
    if (typeof state.max_positions === "number") {
      setMaxPosDraft(String(state.max_positions));
    }
    if (candlesRef.current) {
      drawCandles(candlesRef.current, state.bars || [], {
        last: state.last_price,
        bid: state.bid,
        ask: state.ask,
        open: state.positions || [],
        closed: (state.closed_trades || []).slice(0, 24),
      });
    }
    if (sparkRef.current) {
      drawSpark(sparkRef.current, hist.current.px, tone(sessionPnl(state)));
    }
  }, [state]);

  async function call(path: string, body?: Record<string, unknown>, label = path) {
    setBusy(label);
    try {
      const res = await fetch(path, {
        method: "POST",
        credentials: "include",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401) {
        setGate("locked");
        setPinRequired(true);
        setFlash({ ok: false, text: "PIN required" });
        return;
      }
      const data = (await res.json().catch(() => ({}))) as ApiResult;
      const text = data.message || (res.ok ? "ok" : `HTTP ${res.status}`);
      setFlash({ ok: Boolean(data.ok ?? res.ok), text });
      await load();
    } catch (err) {
      setFlash({ ok: false, text: err instanceof Error ? err.message : "request failed" });
    } finally {
      setBusy(null);
    }
  }

  async function unlockDesk(event: FormEvent) {
    event.preventDefault();
    setBusy("unlock");
    setPinError("");
    try {
      const res = await fetch("/api/unlock", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      const data = (await res.json().catch(() => ({}))) as UnlockStatus & ApiResult;
      if (!res.ok || data.ok === false) {
        setPinError(data.message || "wrong PIN");
        return;
      }
      setPin("");
      setPinRequired(Boolean(data.required ?? true));
      setGate("open");
    } catch {
      setPinError("could not reach the desk");
    } finally {
      setBusy(null);
    }
  }

  async function lockDesk() {
    await call("/api/lock", undefined, "lock");
    setGate("locked");
    setPinRequired(true);
    setState(null);
    setHealth(null);
  }

  if (!mounted || gate === "checking") {
    return <div className="shell" suppressHydrationWarning />;
  }

  if (gate === "locked") {
    return (
      <div className="lockgate">
        <form className="lockcard" onSubmit={unlockDesk}>
          <div className="brand">fomo-app // LOCKED</div>
          <p className="reason">Enter your PIN. The bot keeps running on the server either way.</p>
          <label className="field">
            <span className="k">PIN</span>
            <input
              type="password"
              inputMode="numeric"
              autoComplete="off"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              autoFocus
            />
          </label>
          {pinError ? <div className="flash bad">{pinError}</div> : null}
          <button type="submit" disabled={!!busy || !pin}>
            UNLOCK
          </button>
        </form>
      </div>
    );
  }

  const today = sessionPnl(state);
  const analysis = state?.analysis || {};
  const trades = state?.closed_trades || [];
  const open = state?.positions || [];
  const maxPos = state?.max_positions || 3;
  const blockers = state?.blockers || [];
  const checks = analysis.checks || {};
  const bid = state?.bid != null ? Number(state.bid).toFixed(5) : "—";
  const ask = state?.ask != null ? Number(state.ask).toFixed(5) : "—";
  const px = state?.last_price != null ? Number(state.last_price).toFixed(5) : "—";
  const offline = !state;
  const mt5 = state?.mt5;
  const mt5On = Boolean(mt5?.attached);
  const fillsInMt5 = Boolean(mt5?.fills_in_terminal);
  const demoBal = mt5On && mt5?.balance != null ? Number(mt5.balance) : null;
  const demoCredit = mt5On && mt5?.credit != null ? Number(mt5.credit) : null;
  const canTrade = Boolean(state?.running && state?.broker && state.broker !== "offline");
  const lot = state?.next_lot != null ? Number(state.next_lot).toFixed(2) : "—";
  const riskPct = state?.risk_pct != null ? Math.round(Number(state.risk_pct) * 100) : 2;
  const equityNow =
    fillsInMt5 && mt5?.equity != null
      ? Number(mt5.equity)
      : state?.account != null
        ? Number(state.account.equity)
        : null;
  const deposit = state?.pnl?.start_deposit != null ? Number(state.pnl.start_deposit) : 100000;
  const recover =
    state?.pnl?.recover != null
      ? Number(state.pnl.recover)
      : equityNow != null
        ? Math.max(0, Math.round((deposit - equityNow) * 100) / 100)
        : null;
  const livePnl =
    state?.pnl?.floating != null
      ? Number(state.pnl.floating)
      : equityNow != null && demoBal != null
        ? Math.round((equityNow - demoBal) * 100) / 100
        : null;
  const healthStatus = health?.status || (offline ? "CRITICAL" : "");
  const healthChip =
    healthStatus === "HEALTHY" ? "live" : healthStatus === "DEGRADED" ? "warn" : healthStatus ? "live off" : "";
  const barTone =
    healthStatus === "HEALTHY" ? "on" : healthStatus === "DEGRADED" ? "warn" : healthStatus === "CRITICAL" ? "off" : mt5On ? "on" : "off";
  const ipcMs = health?.metrics?.ipc_latency_ms;
  const healthHeadline =
    healthStatus === "CRITICAL"
      ? (health?.error_reason || health?.warnings?.[0] || "SYSTEM CRITICAL")
      : healthStatus === "DEGRADED"
        ? (health?.warnings?.[0] || "SYSTEM DEGRADED")
        : mt5On
          ? "METATRADER CONNECTED"
          : "METATRADER NOT ATTACHED";

  return (
    <div className="shell">
      <div className="headstack">
      <header>
        <div className="headrow">
          <div className="brand">fomo-app // DEMO DESK</div>
          <div className={`chip live ${state?.running ? "" : "off"}`}>
            {state?.running ? "RUNNING" : "STOPPED"}
          </div>
          <div className={`chip ${mt5On ? "live" : "live off"}`}>
            {mt5On ? "METATRADER ON" : "METATRADER OFF"}
          </div>
          <div className={`chip ${fillsInMt5 ? "live" : "live off"}`}>
            {fillsInMt5 ? "FILLS IN MT5 APP" : "PAPER FILLS ONLY"}
          </div>
          {healthStatus ? (
            <div className={`chip ${healthChip}`}>
              {healthStatus}
              {ipcMs != null && health?.checks?.ipc_loop_responsive ? ` · IPC ${Number(ipcMs).toFixed(0)}ms` : ""}
            </div>
          ) : null}
          <div className="chip hedge">
            OPEN {open.length}/{maxPos}
          </div>
          <div className="chip">{state?.trade_mode || "demo"}</div>
          <div className="grow" />
          <div className="clock">{clock}</div>
          {pinRequired ? (
            <button type="button" disabled={!!busy} onClick={() => void lockDesk()}>
              LOCK
            </button>
          ) : null}
          <button type="button" disabled={!!busy} onClick={() => call("/api/start", undefined, "start")}>
            START
          </button>
          <button
            type="button"
            className="stop"
            disabled={!!busy}
            onClick={() => call("/api/stop", undefined, "stop")}
          >
            STOP
          </button>
        </div>
        <div className="headrow">
          <div className="pxbox">
            <span className="k">{state?.symbol || "EURUSD"} LAST</span>
            <b>{px}</b>
          </div>
          <div className="pxbox bidbox">
            <span className="k">BID</span>
            <b>{bid}</b>
          </div>
          <div className="pxbox askbox">
            <span className="k">ASK</span>
            <b>{ask}</b>
          </div>
          <div className="pxbox">
            <span className="k">SPREAD</span>
            <b>{state?.spread != null ? `${state.spread} pip` : "—"}</b>
          </div>
          <div className="pxbox">
            <span className="k">MT5 DEMO $</span>
            <b>{demoBal != null ? demoBal.toFixed(2) : "—"}</b>
          </div>
          <div className="pxbox">
            <span className="k">LOT</span>
            <b>{lot}</b>
          </div>
          <div className="pxbox">
            <span className="k">{fillsInMt5 ? "MT5 EQUITY" : "PAPER EQUITY"}</span>
            <b>
              {fillsInMt5 && mt5?.equity != null
                ? Number(mt5.equity).toFixed(2)
                : state?.account
                  ? Number(state.account.equity).toFixed(2)
                  : "—"}
            </b>
          </div>
          <div className={`pxbox ${recover == null ? "" : recover > 0.004 ? "down" : "up"}`}>
            <span className="k">HOLE TO $100k</span>
            <b>{recover != null ? `$${recover.toFixed(2)}` : "—"}</b>
          </div>
          <div className={`pxbox ${livePnl == null ? "" : livePnl > 0.004 ? "up" : livePnl < -0.004 ? "down" : ""}`}>
            <span className="k">P&L</span>
            <b>{livePnl != null ? money(livePnl) : "—"}</b>
          </div>
        </div>
      </header>
      <div className={`mt5bar ${barTone}`}>
        <span className="mt5dot" />
        <div className="mt5copy">
          <b>{healthHeadline}</b>
          <div>
            {mt5On
              ? fillsInMt5
                ? `Fills go into the MetaTrader 5 app. Demo money ${demoBal != null ? demoBal.toFixed(2) : "—"} ${mt5?.currency || ""}` +
                  (demoCredit ? ` · credit ${demoCredit.toFixed(2)}` : "") +
                  ` · size is ${riskPct}% of demo equity, lot ${lot}.` +
                  (mt5?.trade_allowed === false ? " · ALGO TRADING IS OFF — click Algo Trading in MetaTrader so it is green." : "")
                : `MT5 is quotes + demo balance only. Orders are PAPER and will not hit the demo account. Demo money ${demoBal != null ? demoBal.toFixed(2) : "—"} ${mt5?.currency || ""}` +
                  (demoCredit ? ` · credit ${demoCredit.toFixed(2)}` : "") +
                  ` · size is ${riskPct}% of demo equity.`
              : "The MT5 window can be open and Python still fails to attach. Buys/sells on this desk are PAPER — they will not show in the MetaTrader app. Demo deposit is unknown until attach works."}
          </div>
          <div className="k">
            {(mt5?.login || state?.login || "—") + " @ " + (mt5?.server || state?.server || "MetaQuotes-Demo")}
            {mt5?.trade_allowed === false ? " · ALGO TRADING OFF" : mt5?.error ? ` · ${mt5.error}` : mt5?.company ? ` · ${mt5.company}` : ""}
            {state?.mutex?.ea_active || state?.mutex?.leftover_ea
              ? " · REMOVE OLD EA FROM CHART"
              : state?.mutex?.owner === "python"
                ? " · ONE BOT"
                : " · ONE BOT IDLE"}
            {health?.warnings?.length ? ` · ${health.warnings.join(" · ")}` : ""}
          </div>
        </div>
        <button type="button" disabled={!!busy} onClick={() => call("/api/reconnect", undefined, "reconnect")}>
          RETRY MT5
        </button>
      </div>
      {flash ? (
        <div className={`flash ${flash.ok ? "ok" : "bad"}`} role="status">
          {flash.ok ? "OK" : "FAIL"} · {flash.text}
          <button type="button" className="ghost" onClick={() => setFlash(null)}>
            DISMISS
          </button>
        </div>
      ) : null}
      </div>

      <section className="top">
        <div className="card pad">
          <div className="k">Session PnL · MetaQuotes demo equity · not live money</div>
          <div className="pnl" style={{ color: tone(today) }}>
            {money(today)}
          </div>
          <div className="metrics">
            <div>
              <span className="k">Closed</span>
              <b style={{ color: tone(state?.pnl?.closed) }}>{money(state?.pnl?.closed)}</b>
            </div>
            <div>
              <span className="k">Floating</span>
              <b style={{ color: tone(state?.pnl?.floating) }}>{money(state?.pnl?.floating)}</b>
            </div>
            <div>
              <span className="k">Win rate</span>
              <b>
                {(state?.win_rate || 0).toFixed(1)}% · {state?.fills || 0} fills
              </b>
            </div>
          </div>
          <div className="k">Last price (from /api/state)</div>
          <canvas ref={sparkRef} className="spark" />
          <div className="reason">{state?.last_reason || (offline ? "waiting for bot API on :5055" : "—")}</div>
        </div>

        <div className="card pad chartcard">
          <div className="k">
            {(state?.symbol || "EURUSD")} M5 · live last + buy/sell marks
          </div>
          <div className="chartwrap">
            <canvas ref={candlesRef} />
            <div className="quote">
              <div className="ask">ASK {ask}</div>
              <div className="mid">{px}</div>
              <div className="bid">BID {bid}</div>
              <div className="k">spread {state?.spread != null ? `${state.spread}` : "—"}</div>
              {open.map((p) => (
                <div key={p.ticket} className={p.side === "BUY" ? "bid" : "ask"}>
                  {p.side} {money(p.profit)}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card pad">
          <div className="k">
            Bill · H1 {state?.h1 || analysis.checks?.h1 || "—"} + H4 {state?.h4 || analysis.checks?.h4 || "—"}
            {" "}→ {state?.htf || analysis.checks?.htf || "HOLD"} · clock 60s · 1:1 ATR · London–NY{" "}
            {state?.session_start_hour ?? 7}–{state?.session_end_hour ?? 21} UTC · risk{" "}
            {((state?.risk_pct ?? 0.01) * 100).toFixed(0)}% · up to {state?.max_positions ?? 8} tickets
            · TP {state?.take_atr_mult ?? 1.5}×ATR · SL {state?.stop_atr_mult ?? 1.5}×ATR · lot{" "}
            {state?.next_lot ?? 0.1}
          </div>
          <div className="scoreline">
            <b>
              {analysis.action || "HOLD"} · {state?.htf || analysis.checks?.htf || analysis.bias || "FLAT"}
            </b>
            <span>
              RSI {analysis.rsi != null ? Number(analysis.rsi).toFixed(1) : "—"} · ATR{" "}
              {analysis.atr_pips != null ? Number(analysis.atr_pips).toFixed(1) : "—"} pip · trend{" "}
              {analysis.trend || "—"}
            </span>
          </div>
          <div className="k">Why auto is blocked</div>
          <ul className="blockers">
            {blockers.length ? (
              blockers.map((b) => <li key={b}>{b}</li>)
            ) : (
              <li>no blockers — auto may open on next step</li>
            )}
          </ul>
          <div className="k">Checks</div>
          <div className="checks">
            {Object.keys(checks).length
              ? Object.entries(checks).map(([k, v]) => (
                  <div key={k}>
                    <span>{k}</span>
                    <b>{v}</b>
                  </div>
                ))
              : "—"}
          </div>
          {(analysis.reasons || []).length ? (
            <>
              <div className="k">Reasons</div>
              <ul className="blockers">
                {analysis.reasons!.slice(0, 6).map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </section>

      <section className="mid">
        <div className="card pad">
          <div className="toolbar">
            <div className="k">Open tickets · Close hits /api/close</div>
            <div className="actions">
              <button
                type="button"
                className="buy"
                disabled={!!busy || !canTrade}
                onClick={() => call("/api/buy", undefined, "buy")}
              >
                BUY {lot}
              </button>
              <button
                type="button"
                className="sell"
                disabled={!!busy || !canTrade}
                onClick={() => call("/api/sell", undefined, "sell")}
              >
                SELL {lot}
              </button>
              <button type="button" disabled={!!busy || !canTrade} onClick={() => call("/api/step", undefined, "step")}>
                STEP NOW
              </button>
              <button
                type="button"
                className="stop"
                disabled={!!busy || !open.length}
                onClick={() => call("/api/close-all", undefined, "close-all")}
              >
                CLOSE ALL
              </button>
            </div>
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Ticket</th>
                  <th>Side</th>
                  <th>Vol</th>
                  <th>Entry</th>
                  <th>PnL</th>
                  <th>Verdict</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {open.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="empty">
                      {canTrade
                        ? `No open tickets. Manual BUY/SELL ignores analyst HOLD. Cap ${maxPos}. Mix BUY/SELL blocked.`
                        : state?.running
                          ? "Connecting broker… BUY/SELL unlocks after MT5 or paper is ready."
                          : "Press START so the bot connects a broker, then BUY/SELL will fill."}
                    </td>
                  </tr>
                ) : (
                  open.map((p) => (
                    <tr key={p.ticket}>
                      <td>{p.ticket}</td>
                      <td className={p.side === "BUY" ? "bid" : "ask"}>{p.side}</td>
                      <td>{p.volume}</td>
                      <td>{Number(p.price).toFixed(5)}</td>
                      <td style={{ color: tone(p.profit) }}>{money(p.profit)}</td>
                      <td className="why">{p.why || p.verdict || "—"}</td>
                      <td>
                        <button
                          type="button"
                          className="stop"
                          disabled={!!busy}
                          onClick={() => call("/api/close", { ticket: p.ticket }, "close")}
                        >
                          CLOSE
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card pad">
          <div className="k">
            Closed trades · all MT5 history
            {state?.pnl?.history_count != null ? ` · ${state.pnl.history_count}` : trades.length ? ` · ${trades.length}` : ""}
            {state?.pnl?.history_net != null ? ` · net ${money(state.pnl.history_net)}` : ""}
          </div>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Time UTC</th>
                  <th>Side</th>
                  <th>Vol</th>
                  <th>Price</th>
                  <th>PnL</th>
                  <th>Ticket</th>
                </tr>
              </thead>
              <tbody>
                {trades.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="empty">
                      No closed trades in history
                    </td>
                  </tr>
                ) : (
                  trades.map((t, i) => {
                    const pnl = Number(t.profit || 0);
                    const row = pnl > 0.004 ? "gain" : pnl < -0.004 ? "loss" : "";
                    return (
                      <tr key={t.ticket || i} className={row}>
                        <td>{t.time || "—"}</td>
                        <td>{t.side}</td>
                        <td>{t.volume}</td>
                        <td>{t.price != null ? Number(t.price).toFixed(5) : "—"}</td>
                        <td className="cash">{money(t.profit)}</td>
                        <td>{t.ticket}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="bot">
        <div className="card pad">
          <div className="k">Settings · POST /api/settings</div>
          <label className="field">
            Max open tickets
            <input
              type="number"
              min={1}
              max={8}
              value={maxPosDraft}
              onChange={(e) => setMaxPosDraft(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={!!busy}
            onClick={() => call("/api/settings", { max_positions: Number(maxPosDraft) }, "settings")}
          >
            APPLY
          </button>
          <div className="k">Order fills</div>
          <div className="actions">
            <button
              type="button"
              className={fillsInMt5 ? "" : "buy"}
              disabled={!!busy || !fillsInMt5}
              onClick={() => call("/api/settings", { fill_venue: "paper" }, "paper-fills")}
            >
              PAPER FILLS
            </button>
            <button
              type="button"
              className="stop"
              disabled={!!busy || fillsInMt5 || !mt5On}
              onClick={() => call("/api/settings", { fill_venue: "mt5" }, "mt5-fills")}
            >
              SEND FILLS TO DEMO
            </button>
          </div>
          <div className="meta">
            poll {state?.poll_seconds || "—"}s · lot {lot} · {riskPct}% of demo · SL {analysis.sl_pips ?? "—"} / TP{" "}
            {analysis.tp_pips ?? "—"} pips
            <br />
            {fillsInMt5
              ? "BUY/SELL hits the MetaQuotes demo account."
              : "BUY/SELL stays on paper even if MetaTrader is connected."}
            <br />
            MT5 chart: Navigator → Indicators → FOMO_Analyst → drag onto EURUSD M5
            <br />
            {state?.account
              ? `${state.account.login} @ ${state.account.server}`
              : "no account yet"}
            {busy ? ` · busy ${busy}` : ""}
          </div>
        </div>
        <div className="card pad">
          <div className="k">Bot log · newest first</div>
          <ul className="log">
            {(state?.logs || []).slice(0, 40).map((line, i) => (
              <li key={`${i}-${line}`}>{line}</li>
            ))}
            {!state?.logs?.length ? <li>waiting for bot feed…</li> : null}
          </ul>
        </div>
      </section>

      <footer>
        <div>FOMO DEMO · UI DRIVES /api/* · TREND+RSI+ATR</div>
        <div className="feed">{blockers[0] || state?.last_reason || "ready"}</div>
        <div>
          {state?.account ? `${state.account.login} @ ${state.account.server}` : "offline"} · {state?.broker || "down"}
        </div>
      </footer>
    </div>
  );
}
