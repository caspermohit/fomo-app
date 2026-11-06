"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { bindChartZoom, drawCandles, drawMoneyGraph, jumpToLive, resetChartZoom, zoomChart } from "@/lib/charts";
import { fmtPx, fmtTime, money, moneyBalanceSeries, push, sessionPnl, tone } from "@/lib/format";
import { emptyHistory, type ApiResult, type BotState, type HealthReport, type HtfReport, type UnlockStatus } from "@/lib/types";
import { deskWhy, formatMacdWr } from "@/lib/verdict";

function pairList(state: BotState | null): string[] {
  if (state?.symbols?.length) return state.symbols;
  return [state?.symbol || "EURUSD"];
}

function nextFocus(state: BotState | null, override?: string | null): string {
  const pairs = pairList(state);
  if (override && pairs.includes(override)) return override;
  if (state?.focus && pairs.includes(state.focus)) return state.focus;
  return pairs[0];
}

function stageCopy(row?: { state?: string; label?: string } | null): { state: string; label: string } {
  const state = String(row?.state || "wait");
  let label = String(row?.label || state);
  label = label.replace(/^(H1|M15|M5)\s+[—–-]\s+/, "");
  return { state, label };
}

function utcClock(now: Date): string {
  return now.toISOString().slice(11, 19) + " UTC";
}

function localClock(now: Date): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(now);
}

export default function Dashboard() {
  const [mounted, setMounted] = useState(false);
  const [state, setState] = useState<BotState | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [clock, setClock] = useState(() => {
    const now = new Date();
    return { utc: utcClock(now), local: utcClock(now) };
  });
  const [flash, setFlash] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [maxPosDraft, setMaxPosDraft] = useState("4");
  const [focusOverride, setFocusOverride] = useState<string | null>(null);
  const [gate, setGate] = useState<"checking" | "locked" | "open">("checking");
  const [pinRequired, setPinRequired] = useState(false);
  const [authMethod, setAuthMethod] = useState<"totp" | "pin" | "off">("totp");
  const [pin, setPin] = useState("");
  const [pinError, setPinError] = useState("");
  const [showQr, setShowQr] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [authCode, setAuthCode] = useState("");
  const [authMsg, setAuthMsg] = useState("");
  const [qrTick, setQrTick] = useState(0);
  const [zoomTick, setZoomTick] = useState(0);
  const [candleFit, setCandleFit] = useState<"recent" | "all">("recent");
  const [chartOpts, setChartOpts] = useState({
    chartType: "candles" as "candles" | "line",
    showEma: true,
    showMacd: true,
    showRsi: false,
    showBb: false,
  });
  const hist = useRef(emptyHistory());
  const candlesRef = useRef<HTMLCanvasElement>(null);
  const moneyRef = useRef<HTMLCanvasElement>(null);
  const lastStateRef = useRef<BotState | null>(null);
  const lastFocusRef = useRef<string | null>(null);
  const chartOptsRef = useRef(chartOpts);
  chartOptsRef.current = chartOpts;

  useEffect(() => {
    setMounted(true);
    const tick = () => {
      const now = new Date();
      setClock({ utc: utcClock(now), local: localClock(now) });
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/state", { credentials: "include", cache: "no-store" });
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
        if (next.method) setAuthMethod(next.method);
        setShowQr(Boolean(next.show_qr));
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

  const redrawCandles = useCallback(() => {
    const s = lastStateRef.current;
    const canvas = candlesRef.current;
    if (!s || !canvas) return;
    const f = nextFocus(s, lastFocusRef.current);
    const book = s.books?.[f];
    drawCandles(canvas, book?.bars || s.bars || [], {
      last: book?.last_price ?? s.last_price,
      bid: book?.bid ?? s.bid,
      ask: book?.ask ?? s.ask,
      open: (s.positions || []).filter((p) => !p.symbol || p.symbol === f),
      closed: (s.closed_trades || []).filter((t) => !t.symbol || t.symbol === f).slice(0, 80),
      symbol: f,
      kronos: book?.kronos,
      ...chartOptsRef.current,
    });
  }, []);

  useEffect(() => {
    if (gate !== "open") return;
    const bump = () => setZoomTick((n) => n + 1);
    if (candlesRef.current) {
      bindChartZoom(candlesRef.current, () => {
        const c = candlesRef.current as (HTMLCanvasElement & { _fitAll?: boolean }) | null;
        setCandleFit(c?._fitAll ? "all" : "recent");
        bump();
      }, redrawCandles);
    }
    if (moneyRef.current) bindChartZoom(moneyRef.current, bump);
  }, [gate, redrawCandles]);

  useEffect(() => {
    if (!state) return;
    lastStateRef.current = state;
    lastFocusRef.current = focusOverride;
    if (typeof state.max_positions === "number") {
      setMaxPosDraft(String(state.max_positions));
    }
    if (candlesRef.current) {
      const focus = nextFocus(state, focusOverride);
      const book = state.books?.[focus];
      drawCandles(candlesRef.current, book?.bars || state.bars || [], {
        last: book?.last_price ?? state.last_price,
        bid: book?.bid ?? state.bid,
        ask: book?.ask ?? state.ask,
        open: (state.positions || []).filter((p) => !p.symbol || p.symbol === focus),
        closed: (state.closed_trades || []).filter((t) => !t.symbol || t.symbol === focus).slice(0, 80),
        symbol: focus,
        kronos: book?.kronos,
        ...chartOpts,
      });
    }
    if (moneyRef.current) {
      const start = Number(state.pnl?.start_deposit ?? 100000);
      const series = moneyBalanceSeries(
        state.closed_trades || [],
        start,
        Number(state.pnl?.floating ?? 0),
      );
      drawMoneyGraph(moneyRef.current, series, start);
    }
  }, [state, focusOverride, zoomTick, chartOpts]);

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
        setFlash({ ok: false, text: authMethod === "totp" ? "authenticator code required" : "PIN required" });
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
        body: JSON.stringify({ code: pin, pin }),
      });
      const data = (await res.json().catch(() => ({}))) as UnlockStatus & ApiResult;
      if (!res.ok || data.ok === false) {
        setPinError(data.message || (authMethod === "totp" ? "wrong code" : "wrong PIN"));
        if (data.method) setAuthMethod(data.method);
        return;
      }
      setPin("");
      setPinRequired(Boolean(data.required ?? true));
      setShowQr(Boolean(data.show_qr));
      if (data.method) setAuthMethod(data.method);
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
    try {
      const res = await fetch("/api/unlock", { credentials: "include" });
      const next = (await res.json()) as UnlockStatus;
      setShowQr(Boolean(next.show_qr));
      if (next.method) setAuthMethod(next.method);
    } catch {
      /* keep last */
    }
  }

  if (!mounted || gate === "checking") {
    return (
      <div className="lockgate" suppressHydrationWarning>
        <div className="lockcard">
          <div className="brand">FOMO</div>
          <p className="reason">Connecting to the bot…</p>
        </div>
      </div>
    );
  }

  if (gate === "locked") {
    return (
      <div className="lockgate">
        <form className="lockcard" onSubmit={unlockDesk}>
          <div className="brand">FOMO</div>
          <p className="reason">
            {authMethod === "pin"
              ? "Enter your PIN. The bot keeps running on the server either way."
              : showQr
                ? "Scan the QR in Authenticator, then enter the 6-digit code. The bot keeps running either way."
                : "Open your authenticator app and enter the 6-digit code. The bot keeps running either way."}
          </p>
          {authMethod === "totp" && showQr ? (
            <>
              <img className="lockqr" alt="Scan to add FOMO desk" src={`/api/totp/qr?t=${qrTick}`} />
              <p className="reason">Google Authenticator / Authy → add account → scan QR. Then type the code below.</p>
            </>
          ) : null}
          <label className="field">
            <span className="k">{authMethod === "pin" ? "PIN" : "Authenticator"}</span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              autoFocus
            />
          </label>
          {pinError ? <div className="flash bad">{pinError}</div> : null}
          <button type="submit" className="primary" disabled={!!busy || !pin}>
            UNLOCK
          </button>
        </form>
      </div>
    );
  }

  const today = sessionPnl(state);
  const pairs = pairList(state);
  const focus = nextFocus(state, focusOverride);
  const book = state?.books?.[focus];
  const analysis = book?.analysis || state?.analysis || {};
  const trades = state?.closed_trades || [];
  const open = state?.positions || [];
  const maxPos = state?.max_positions || 1;
  const blockers = book?.blockers || state?.blockers || [];
  const why = deskWhy(state, focus);
  const bid = fmtPx(focus, book?.bid ?? state?.bid);
  const ask = fmtPx(focus, book?.ask ?? state?.ask);
  const px = fmtPx(focus, book?.last_price ?? state?.last_price);
  const bars = book?.bars || state?.bars || [];
  const lastPx = book?.last_price ?? state?.last_price;
  const chg = (() => {
    if (!bars.length || lastPx == null) return null;
    const look = Math.min(bars.length - 1, 288);
    const base = Number(bars[bars.length - 1 - look].c);
    if (!base) return null;
    const pct = ((Number(lastPx) - base) / base) * 100;
    return { up: pct >= 0, text: `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(2)}%` };
  })();
  const offline = !state;
  const mt5 = state?.mt5;
  const mt5On = Boolean(mt5?.attached);
  const fillsInMt5 = Boolean(mt5?.fills_in_terminal);
  const demoBal = mt5On && mt5?.balance != null ? Number(mt5.balance) : null;
  const canTrade = Boolean(state?.running && state?.broker && state.broker !== "offline");
  const report: HtfReport = book?.htf_report || state?.htf_report || {};
  const reportReady = Boolean(report.ready) && (report.action === "BUY" || report.action === "SELL");
  const reportBias = reportReady ? String(report.action) : report.bias && report.bias !== "HOLD" ? String(report.bias) : "WAIT";
  const h1s = stageCopy(report.h1);
  const m15s = stageCopy(report.m15);
  const m5s = stageCopy(report.m5);
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
  const ipcMs = health?.metrics?.ipc_latency_ms;
  const healthHeadline =
    healthStatus === "CRITICAL"
      ? (health?.error_reason || health?.warnings?.[0] || "SYSTEM CRITICAL")
      : healthStatus === "DEGRADED"
        ? (health?.warnings?.[0] || "SYSTEM DEGRADED")
        : mt5On
          ? "MT5 CONNECTED"
          : "MT5 NOT ATTACHED";
  // Everything the old MT5 banner said, kept as hover text so the header stays one line tall.
  const mt5Detail = [
    (mt5?.login || state?.login || "—") + " @ " + (mt5?.server || state?.server || "MetaQuotes-Demo"),
    mt5On
      ? fillsInMt5
        ? `Fills hit MetaQuotes-Demo · ${riskPct}% risk · lot ${lot}`
        : `MT5 quotes only · orders are PAPER · lot ${lot}`
      : "MetaTrader not attached — buys/sells stay PAPER.",
    mt5?.trade_allowed === false ? "ALGO TRADING OFF" : "",
    mt5?.error || mt5?.company || "",
    state?.mutex?.ea_active || state?.mutex?.leftover_ea
      ? "REMOVE OLD EA FROM CHART"
      : state?.mutex?.owner === "python"
        ? "ONE BOT"
        : "ONE BOT IDLE",
    ...(health?.warnings || []),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">FOMO</div>
        <div className="hello">
          <h1>Demo desk</h1>
          <div className="chip">{state?.trade_mode || "demo"}</div>
        </div>
        <div className="navmark">Trade</div>
        <div className="k">Symbols</div>
        <div className="pairs">
          {pairs.map((pair) => (
            <button
              key={pair}
              type="button"
              className={`chip pairtab ${pair === focus ? "on" : ""}`}
              disabled={!!busy}
              onClick={() => {
                setFocusOverride(pair);
                void call("/api/settings", { focus: pair }, "focus");
              }}
            >
              {pair}
              {state?.books?.[pair]?.open_count ? ` ${state.books[pair].open_count}` : ""}
            </button>
          ))}
        </div>
        <div className="grow" />
        <div className="statusline">
          <div className={`chip live ${state?.running ? "" : "off"}`}>
            {state?.running ? "RUNNING" : "STOPPED"}
          </div>
          <div
            className={`mt5bar ${healthStatus === "DEGRADED" ? "warn" : mt5On ? "on" : "off"}`}
            title={mt5Detail}
          >
            <span className="mt5dot" />
            <div className="mt5copy">
              <b>{healthHeadline}</b>
            </div>
          </div>
          <div className={`chip ${fillsInMt5 ? "live" : "live off"}`}>
            {fillsInMt5 ? "FILLS IN MT5" : "PAPER FILLS"}
          </div>
          {healthStatus ? (
            <div className={`chip ${healthChip}`} title={health?.warnings?.join(" · ") || ""}>
              {healthStatus}
              {ipcMs != null && health?.checks?.ipc_loop_responsive ? ` · IPC ${Number(ipcMs).toFixed(0)}ms` : ""}
            </div>
          ) : null}
          <div className="chip hedge">OPEN {open.length}</div>
        </div>
        <div className="head-actions">
          <button id="btn-retry" type="button" disabled={!!busy} onClick={() => call("/api/reconnect", undefined, "reconnect")}>
            RETRY MT5
          </button>
          {pinRequired ? (
            <button id="btn-lock" type="button" disabled={!!busy} onClick={() => void lockDesk()}>
              LOCK
            </button>
          ) : null}
          {pinRequired && authMethod === "totp" ? (
            <button
              type="button"
              disabled={!!busy}
              onClick={() => {
                setAuthMsg("");
                setAuthCode("");
                setQrTick((n) => n + 1);
                setAuthOpen(true);
              }}
            >
              AUTHENTICATOR
            </button>
          ) : null}
          <button type="button" className="primary" disabled={!!busy} onClick={() => call("/api/start", undefined, "start")}>
            START
          </button>
          <button type="button" className="stop" disabled={!!busy} onClick={() => call("/api/stop", undefined, "stop")}>
            STOP
          </button>
        </div>
      </aside>

      <div className="workspace">
        <div className="apptop">
          <div className="crumb">
            Home <span>/</span> Trade
          </div>
          <div className="ticker">
            <div className="pxbox">
              <span className="k">{focus}</span>
              <b>{px}</b>
            </div>
            <div className="pxbox">
              <span className="k">BID/ASK</span>
              <b className="bid">{bid}</b>
              <b className="ask">{ask}</b>
            </div>
            <div className="pxbox">
              <span className="k">SPREAD</span>
              <b>{state?.spread != null ? `${state.spread}p` : "—"}</b>
            </div>
            <div className="pxbox">
              <span className="k">LOT</span>
              <b>{lot}</b>
            </div>
            <div className="pxbox" title={fillsInMt5 ? "MetaQuotes demo equity" : "Paper equity"}>
              <span className="k">EQUITY</span>
              <b>
                {fillsInMt5 && mt5?.equity != null
                  ? Number(mt5.equity).toFixed(2)
                  : state?.account
                    ? Number(state.account.equity).toFixed(2)
                    : "—"}
              </b>
            </div>
            <div className="pxbox">
              <span className="k">DEMO $</span>
              <b>{demoBal != null ? demoBal.toFixed(2) : "—"}</b>
            </div>
            <div className={`pxbox ${recover == null ? "" : recover > 0.004 ? "down" : "up"}`}>
              <span className="k">HOLE TO 100k</span>
              <b>{recover != null ? `$${recover.toFixed(2)}` : "—"}</b>
            </div>
            <div className={`pxbox ${livePnl == null ? "" : livePnl > 0.004 ? "up" : livePnl < -0.004 ? "down" : ""}`}>
              <span className="k">P&L</span>
              <b>{livePnl != null ? money(livePnl) : "—"}</b>
            </div>
            <div className="pxbox" title="Session PnL on MetaQuotes demo equity — not live money">
              <span className="k">SESSION</span>
              <b style={{ color: tone(today) }}>{money(today)}</b>
            </div>
            <div className="pxbox">
              <span className="k">CLOSED/FLOAT</span>
              <b style={{ color: tone(state?.pnl?.closed) }}>{money(state?.pnl?.closed)}</b>
              <b style={{ color: tone(state?.pnl?.floating ?? livePnl) }}>{money(state?.pnl?.floating ?? livePnl)}</b>
            </div>
            <div className="pxbox">
              <span className="k">WIN RATE</span>
              <b>
                {(state?.win_rate || 0).toFixed(0)}% · {state?.fills || 0}
              </b>
            </div>
          </div>
          <div className="wallet">
            <div className="clocks">
              <div className="clock">{clock.utc}</div>
              <div className={`clock local ${open.length ? "on" : ""}`} title={open.length ? "Ticket open" : "No open ticket"}>
                {clock.local}
              </div>
            </div>
          </div>
        </div>

        <div className="workbody">
          <div className="chartscol">
            <div className="card pad chartcard">
              <div className="k">Balance</div>
              <canvas ref={moneyRef} className="moneygraph" />
            </div>
            <div className="card tvchart">
              <div className="tvhead">
                <div>
                  <div className="tvpair">{focus}</div>
                  <div className="tvsub">M5 · live last · buy / sell marks</div>
                </div>
                <div className="tvright">
                  <div className="tvlast">{px}</div>
                  <div className={`tvchg ${chg?.up ? "up" : chg ? "down" : ""}`}>{chg?.text || "—"}</div>
                  <div className="tvquotes">
                    <span className="ask">ASK {ask}</span>
                    <span className="bid">BID {bid}</span>
                    <span>spread {book?.spread ?? state?.spread ?? "—"}</span>
                  </div>
                </div>
              </div>
              <div className="chartwrap">
                <canvas ref={candlesRef} />
              </div>
              <div className="chartbar">
                <button
                  type="button"
                  className={`tf ${candleFit === "recent" ? "on" : ""}`}
                  onClick={() => {
                    if (!candlesRef.current) return;
                    resetChartZoom(candlesRef.current, "recent");
                    setCandleFit("recent");
                    setZoomTick((n) => n + 1);
                  }}
                >
                  M5
                </button>
                <button
                  type="button"
                  className={`tf ${candleFit === "all" ? "on" : ""}`}
                  onClick={() => {
                    if (!candlesRef.current) return;
                    resetChartZoom(candlesRef.current, "all");
                    setCandleFit("all");
                    setZoomTick((n) => n + 1);
                  }}
                >
                  ALL
                </button>
                <button
                  type="button"
                  className="tf"
                  title="Zoom in"
                  onClick={() => {
                    if (!candlesRef.current) return;
                    zoomChart(candlesRef.current, 0.7);
                    setCandleFit("recent");
                    setZoomTick((n) => n + 1);
                  }}
                >
                  +
                </button>
                <button
                  type="button"
                  className="tf"
                  title="Zoom out"
                  onClick={() => {
                    if (!candlesRef.current) return;
                    zoomChart(candlesRef.current, 1.4);
                    setZoomTick((n) => n + 1);
                  }}
                >
                  −
                </button>
                <button
                  type="button"
                  className="tf"
                  title="Jump to live bar"
                  onClick={() => {
                    if (!candlesRef.current) return;
                    jumpToLive(candlesRef.current);
                    setCandleFit("recent");
                    setZoomTick((n) => n + 1);
                  }}
                >
                  LIVE
                </button>
                <span className="tfsep" />
                <button
                  type="button"
                  className={`tf ${chartOpts.chartType === "line" ? "on" : ""}`}
                  onClick={() => setChartOpts((o) => ({ ...o, chartType: o.chartType === "line" ? "candles" : "line" }))}
                >
                  LINE
                </button>
                <button
                  type="button"
                  className={`tf ${chartOpts.showEma ? "on" : ""}`}
                  onClick={() => setChartOpts((o) => ({ ...o, showEma: !o.showEma }))}
                >
                  EMA
                </button>
                <button
                  type="button"
                  className={`tf ${chartOpts.showMacd ? "on" : ""}`}
                  onClick={() => setChartOpts((o) => ({ ...o, showMacd: !o.showMacd }))}
                >
                  MACD
                </button>
                <button
                  type="button"
                  className={`tf ${chartOpts.showRsi ? "on" : ""}`}
                  onClick={() => setChartOpts((o) => ({ ...o, showRsi: !o.showRsi }))}
                >
                  RSI
                </button>
                <button
                  type="button"
                  className={`tf ${chartOpts.showBb ? "on" : ""}`}
                  onClick={() => setChartOpts((o) => ({ ...o, showBb: !o.showBb }))}
                >
                  BB
                </button>
              </div>
            </div>
          </div>

          <div className="card pad ticket">
            <div className="tickhead">
              <div>
                <div className="k">Symbol</div>
                <div className="whynow">{why.now}</div>
              </div>
            </div>
            <div className="report">
              <div className="reporthead">
                <div className="k">Report · H1 → M15 → M5</div>
                <b
                  className={`reportbias${
                    reportReady
                      ? ` ${String(report.action).toLowerCase()} ready`
                      : report.bias === "BUY"
                        ? " buy"
                        : report.bias === "SELL"
                          ? " sell"
                          : ""
                  }`}
                >
                  {reportBias}
                </b>
              </div>
              <ol className="rstages">
                <li className={`rstage ${h1s.state}`}>
                  <i />
                  <span>H1</span>
                  <b className="rval">{h1s.label}</b>
                </li>
                <li className={`rstage ${m15s.state}`}>
                  <i />
                  <span>M15</span>
                  <b className="rval">{m15s.label}</b>
                </li>
                <li className={`rstage ${m5s.state}`}>
                  <i />
                  <span>M5</span>
                  <b className="rval">{m5s.label}</b>
                </li>
              </ol>
              <p className="reason">{report.why || "waiting for H1 / M15 / M5…"}</p>
              <button
                type="button"
                className={`reportbtn${reportReady ? ` ${String(report.action).toLowerCase()}` : ""}`}
                disabled={!!busy || !canTrade || !reportReady}
                onClick={() => call("/api/report/take", { symbol: focus }, "report")}
              >
                {reportReady ? `SEND ${report.action} ${focus}` : "SEND WHEN READY"}
              </button>
            </div>
            <div className="orderpair">
              <button
                type="button"
                className="sell"
                disabled={!!busy || !canTrade}
                onClick={() => call("/api/sell", { symbol: focus }, "sell")}
              >
                SELL {focus} {lot}
              </button>
              <button
                type="button"
                className="buy"
                disabled={!!busy || !canTrade}
                onClick={() => call("/api/buy", { symbol: focus }, "buy")}
              >
                BUY {focus} {lot}
              </button>
            </div>
            <div className="ticketmeta">
              <label className="field">
                Max tickets
                <input
                  type="number"
                  min={1}
                  max={4}
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
            </div>
            <div className="actions ticketacts">
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
            <div className="actions ticketacts">
              <button
                type="button"
                className={fillsInMt5 ? "" : "buy"}
                disabled={!!busy || !fillsInMt5}
                onClick={() => call("/api/settings", { fill_venue: "paper" }, "paper-fills")}
              >
                PAPER
              </button>
              <button
                type="button"
                className="stop"
                disabled={!!busy || fillsInMt5 || !mt5On}
                onClick={() => call("/api/settings", { fill_venue: "mt5" }, "mt5-fills")}
              >
                DEMO FILLS
              </button>
            </div>
            <div className="gates">
              {why.gates.map((g) => (
                <div key={g.label} className={`gate ${g.ok ? "ok" : "no"}`}>
                  <span>{g.label}</span>
                  <b>{g.value}</b>
                </div>
              ))}
            </div>
            <div className="reason">{state?.last_reason || blockers[0] || (offline ? "waiting for bot feed…" : "ready")}</div>
            <div className="meta">
              poll {state?.poll_seconds || "—"}s · lot {lot} · {riskPct}% of demo · SL {analysis.sl_pips ?? "—"} / TP{" "}
              {analysis.tp_pips ?? "—"} pips · {state?.strategy || "macd"} · Kotegawa {riskPct}% / {Number(state?.reward_r ?? 2)}R · daily halt{" "}
              {Math.round(Number(state?.daily_loss_pct ?? 0.02) * 100)}%
              {busy ? ` · busy ${busy}` : ""}
            </div>
          </div>
        </div>

        <section className="deskrow">
          <div className="card pad historycard">
            <div className="k">
              Closed trades · {fillsInMt5 ? "MT5 history" : "MT5 + paper"}
              {state?.pnl?.history_count != null ? ` · ${state.pnl.history_count}` : trades.length ? ` · ${trades.length}` : ""}
              {state?.pnl?.history_net != null ? ` · net ${money(state.pnl.history_net)}` : ""}
            </div>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Time UTC</th>
                    <th>Pair</th>
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
                      <td colSpan={7} className="empty">
                        No closed trades in history
                      </td>
                    </tr>
                  ) : (
                    trades.map((t, i) => {
                      const pnl = Number(t.profit || 0);
                      const row = pnl > 0.004 ? "gain" : pnl < -0.004 ? "loss" : "";
                      return (
                        <tr key={t.ticket || i} className={row}>
                          <td>{fmtTime(t.time)}</td>
                          <td>{t.symbol || "—"}</td>
                          <td>{t.side}</td>
                          <td>{t.volume}</td>
                          <td>{t.price != null ? fmtPx(t.symbol, t.price) : "—"}</td>
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
          <div className="card pad ticketcard">
            <div className="toolbar">
              <div className="k">Open tickets</div>
            </div>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Pair</th>
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
                      <td colSpan={8} className="empty">
                        {canTrade
                          ? `No open tickets. Manual BUY/SELL is for ${focus}. Cap ${maxPos} total; max 1 per pair.`
                          : state?.running
                            ? "Connecting broker… BUY/SELL unlocks after MT5 or paper is ready."
                            : "Press START so the bot connects a broker, then BUY/SELL will fill."}
                      </td>
                    </tr>
                  ) : (
                    open.map((p) => (
                      <tr key={p.ticket}>
                        <td>{p.symbol || focus}</td>
                        <td>{p.ticket}</td>
                        <td className={p.side === "BUY" ? "bid" : "ask"}>{p.side}</td>
                        <td>{p.volume}</td>
                        <td>{fmtPx(p.symbol || focus, p.price)}</td>
                        <td style={{ color: tone(p.profit) }}>{money(p.profit)}</td>
                        <td className="why">
                          <div>{p.why || p.verdict || "—"}</div>
                          <div className="k">
                            {(() => {
                              const sym = String(p.symbol || focus || "").toUpperCase();
                              const pairBook = state?.books?.[sym];
                              const overall = formatMacdWr(state?.macd_win_rate, state?.macd_wins, state?.macd_fills, "This system");
                              const pairFills = pairBook?.macd_fills ?? state?.macd_by_symbol?.[sym]?.macd_fills ?? 0;
                              if (pairFills > 0) {
                                return `${overall} · ${formatMacdWr(
                                  pairBook?.macd_win_rate ?? state?.macd_by_symbol?.[sym]?.macd_win_rate,
                                  pairBook?.macd_wins ?? state?.macd_by_symbol?.[sym]?.macd_wins,
                                  pairFills,
                                  sym,
                                )}`;
                              }
                              return overall;
                            })()}
                          </div>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="stop"
                            disabled={!!busy || (p.venue === "mt5" && !fillsInMt5)}
                            title={p.venue === "mt5" && !fillsInMt5 ? "This ticket is on the MT5 filler — close it there" : undefined}
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
          <div className="card pad whypane">
            <div className="k">Why {focus} is buying or selling</div>
            <div className="whyhead">
              {focus} · MACD 12/26/9 + EMA200 + Kronos
              {" · H1 "}
              {why.h1} / H4 {why.h4}
              {" · RSI "}
              {analysis.rsi != null ? Number(analysis.rsi).toFixed(1) : "—"}
              {" · ATR "}
              {analysis.atr_pips != null ? Number(analysis.atr_pips).toFixed(1) : "—"} pip
              {" · "}
              {analysis.trend || "—"} trend
            </div>
            <div className="sides">
              <div className={`sidecard ${why.buy.go ? "go" : "stop"}`}>
                <div className="k">Buy</div>
                <b className={why.buy.go ? "bid" : "ask"}>{why.buy.title}</b>
                <ul>
                  {why.buy.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
              <div className={`sidecard ${why.sell.go ? "go" : "stop"}`}>
                <div className="k">Sell</div>
                <b className={why.sell.go ? "ask" : ""}>{why.sell.title}</b>
                <ul>
                  {why.sell.lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </section>

        <div className="card pad logcard">
          <ul className="log">
            {(state?.logs || []).slice(0, 40).map((line, i) => (
              <li key={`${i}-${line}`}>{line}</li>
            ))}
            {!state?.logs?.length ? <li>waiting for bot feed…</li> : null}
          </ul>
        </div>
      </div>

      {flash ? (
        <div className={`flash ${flash.ok ? "ok" : "bad"}`} role="status">
          {flash.ok ? "OK" : "FAIL"} · {flash.text}
          <button type="button" className="ghost" onClick={() => setFlash(null)}>
            DISMISS
          </button>
        </div>
      ) : null}

      {authOpen ? (
        <div className="lockgate authpanel">
          <div className="lockcard">
            <div className="brand">Authenticator</div>
            <p className="reason">Scan this QR to add FOMO desk. To change it later, type the current 6-digit code and make a new QR.</p>
            <img className="lockqr" alt="FOMO authenticator QR" src={`/api/totp/qr?t=${qrTick}`} />
            <label className="field">
              <span className="k">Current code</span>
              <input
                type="text"
                inputMode="numeric"
                maxLength={8}
                autoComplete="one-time-code"
                value={authCode}
                onChange={(e) => setAuthCode(e.target.value)}
              />
            </label>
            {authMsg ? <div className="reason">{authMsg}</div> : null}
            <button
              type="button"
              className="primary"
              disabled={!!busy || !authCode}
              onClick={async () => {
                setBusy("totp-rotate");
                setAuthMsg("");
                try {
                  const res = await fetch("/api/totp/rotate", {
                    method: "POST",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ code: authCode }),
                  });
                  const data = (await res.json().catch(() => ({}))) as ApiResult;
                  if (!res.ok || data.ok === false) {
                    setAuthMsg(data.message || "wrong code");
                    return;
                  }
                  setAuthCode("");
                  setQrTick((n) => n + 1);
                  setAuthMsg(data.message || "Scan the new QR. Old codes no longer work.");
                } catch {
                  setAuthMsg("could not reach the desk");
                } finally {
                  setBusy(null);
                }
              }}
            >
              MAKE NEW QR
            </button>
            <button type="button" onClick={() => setAuthOpen(false)}>
              CLOSE
            </button>
          </div>
        </div>
      ) : null}

      <footer>
        <div>FOMO DEMO · DEMO ONLY</div>
        <div className="feed">
          {state?.last_reason || blockers[0] || (offline ? "waiting for bot API on :5055" : "ready")}
        </div>
        <div>
          {state?.account ? `${state.account.login} @ ${state.account.server}` : "offline"} · {state?.broker || "down"}
        </div>
      </footer>
    </div>
  );
}
