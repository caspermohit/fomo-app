(() => {
  const $ = (id) => document.getElementById(id);

  let state = null;
  let health = null;
  let lastCandleDraw = null;
  let lastClosedKey = "";
  let gate = "checking";
  let pinRequired = false;
  let authMethod = "totp";
  let showQr = false;
  let busy = null;
  let focusOverride = null;
  let maxPosTouched = false;

  function money(n) {
    const v = Number(n || 0);
    return (v >= 0 ? "+" : "-") + "$" + Math.abs(v).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  function tone(n) {
    const v = Number(n || 0);
    if (v > 0.004) return "#16a34a";
    if (v < -0.004) return "#e11d48";
    return "#171717";
  }
  function cash(n) {
    return "$" + Number(n || 0).toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    });
  }
  function pxDigits(symbol) {
    const s = String(symbol || "").toUpperCase();
    if (s.includes("XAU") || s.includes("GOLD")) return 2;
    if (s.endsWith("JPY")) return 3;
    return 5;
  }
  function fmtPx(symbol, n) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    return Number(n).toFixed(pxDigits(symbol));
  }
  function fmtTime(raw) {
    const s = String(raw || "").trim();
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (m) return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
    return s || "—";
  }
  function rangeChange(bars, last) {
    if (!bars?.length || last == null || Number.isNaN(Number(last))) return null;
    const look = Math.min(bars.length - 1, 288);
    const base = Number(bars[bars.length - 1 - look].c);
    if (!base) return null;
    const pct = ((Number(last) - base) / base) * 100;
    return { up: pct >= 0, text: `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(2)}%` };
  }
  function pairList(s) {
    if (s?.symbols?.length) return s.symbols;
    return [s?.symbol || "EURUSD"];
  }
  function nextFocus(s, override) {
    const pairs = pairList(s);
    if (override && pairs.includes(override)) return override;
    if (s?.focus && pairs.includes(s.focus)) return s.focus;
    return pairs[0];
  }
  function sessionPnl(s) {
    if (!s?.pnl) return 0;
    if (s.pnl.today != null) return Number(s.pnl.today);
    return Number(s.pnl.total ?? 0);
  }
  function has(blockers, needle) {
    const n = needle.toLowerCase();
    return (blockers || []).find((b) => String(b).toLowerCase().includes(n));
  }
  function moneyBalanceSeries(trades, start, floating) {
    const ordered = [...(trades || [])].sort((a, b) => {
      const ta = String(a.time || "");
      const tb = String(b.time || "");
      if (ta !== tb) return ta.localeCompare(tb);
      return String(a.ticket || "").localeCompare(String(b.ticket || ""));
    });
    const out = [{ n: 0, balance: start }];
    let bal = start;
    ordered.forEach((t, i) => {
      bal += Number(t.profit || 0);
      out.push({ n: i + 1, balance: Math.round(bal * 100) / 100 });
    });
    if (Math.abs(floating) > 0.004) {
      out.push({
        n: ordered.length + 1,
        balance: Math.round((bal + floating) * 100) / 100,
        live: true,
      });
    }
    return out;
  }

  function deskWhy(s, focus) {
    const symbol = String(focus || s?.focus || s?.symbol || "EURUSD").toUpperCase();
    const book = s?.books?.[symbol];
    const a = book?.analysis || s?.analysis || {};
    const checks = a.checks || {};
    const h1 = String(book?.h1 || s?.h1 || checks.h1 || "—").toUpperCase();
    const h4 = String(book?.h4 || s?.h4 || checks.h4 || "—").toUpperCase();
    const ema200 = String(checks.ema200 || "—").toLowerCase();
    const cross = String(checks.macd_cross || "HOLD").toUpperCase();
    const zero = String(checks.zero || "—").toLowerCase();
    const blockers = book?.blockers || s?.blockers || [];
    const open = (s?.positions || []).filter((p) => !p.symbol || p.symbol === symbol);
    const allOpen = s?.positions || [];
    const openSide = open[0]?.side?.toUpperCase();
    const maxPos = s?.max_positions ?? 1;
    const startH = s?.session_start_hour ?? 7;
    const endH = s?.session_end_hour ?? 20;
    const spread = book?.spread ?? s?.spread;
    const maxSpread = book?.max_spread_pips ?? (symbol.endsWith("JPY") ? s?.max_spread_pips_jpy ?? 4 : s?.max_spread_pips ?? 2);
    const session = String(checks.session || "");
    const running = Boolean(s?.running);
    const cooldown = has(blockers, "cooldown");
    const pace = has(blockers, "pace");
    const weekend = has(blockers, "weekend");
    const mondayOpen = has(blockers, "monday open");
    const newsHit = book?.news || (s?.news?.active || []).find((e) => String(e.symbol || "").toUpperCase() === symbol);
    const newsBlock = Boolean(s?.news_blackout !== false && newsHit);
    const sessionOut = session.includes("outside") || Boolean(has(blockers, "outside the configured session"));
    const atrNow = a.atr_pips != null ? Number(a.atr_pips) : null;
    const atrFloor = Number(book?.min_atr_pips ?? s?.min_atr_pips ?? 4);
    const atrGate = s?.atr_gate !== false;
    const atrOk = !atrGate || atrNow == null || Number.isNaN(atrNow) || atrNow + 1e-9 >= atrFloor;
    const spreadOk = spread == null || Number(spread) <= maxSpread;
    const room = open.length < maxPos;
    const deskBusy = allOpen.some((p) => p.symbol && p.symbol !== symbol);

    const strat = String(s?.strategy || checks.strategy || "macd").toLowerCase();
    const vwapSide = String(checks.price_vs_vwap || "—").toLowerCase();
    const ema9Side = String(checks.ema9_vs_vwap || "—").toLowerCase();
    const vwapCross = String(checks.cross || "HOLD").toUpperCase();

    function forSide(side) {
      const lines = [];
      if (strat === "vwap_ema") {
        const vwapOk = (side === "BUY" && vwapSide === "above") || (side === "SELL" && vwapSide === "below");
        const emaOk = (side === "BUY" && ema9Side === "above") || (side === "SELL" && ema9Side === "below");
        const crossOk = vwapCross === side;
        if (vwapOk) lines.push(`Price is ${vwapSide} session VWAP — Austin only ${side === "BUY" ? "buys" : "sells"} this side.`);
        else lines.push(`Price is ${vwapSide} VWAP. ${side} needs price ${side === "BUY" ? "above" : "below"} VWAP.`);
        if (emaOk) lines.push(`EMA9 is ${ema9Side} VWAP (momentum agrees).`);
        else lines.push(`EMA9 is ${ema9Side} VWAP. ${side} needs EMA9 ${side === "BUY" ? "above" : "below"} VWAP.`);
        if (crossOk) lines.push(`Fresh EMA9 × VWAP ${side} cross on the closed bar.`);
        else lines.push(`No fresh EMA9 × VWAP ${side} cross (last=${vwapCross}).`);
        if (openSide && openSide !== side) lines.push(`Already ${openSide} × ${open.length} — mixing BUY and SELL is blocked.`);
        else if (openSide === side && open.length) lines.push(`Already ${side} × ${open.length}. Max 1 ticket per pair.`);
        if (deskBusy) lines.push("Another pair is already open.");
        if (!room) lines.push(`Book is full (${open.length}/${maxPos}).`);
        if (cooldown) lines.push(cooldown);
        if (pace) lines.push(pace);
        if (weekend) lines.push(weekend);
        if (mondayOpen) lines.push(mondayOpen);
        if (newsBlock) lines.push(newsHit.reason || `${symbol} news blackout ${newsHit.name || ""}`.trim());
        if (!atrOk) lines.push(`ATR ${atrNow.toFixed(1)} below floor ${atrFloor} — skip dead tape.`);
        if (sessionOut) lines.push(`Outside London hours (${startH}–${endH} UTC).`);
        if (!spreadOk) lines.push(`Spread ${spread} pip — cap is ${maxSpread}.`);
        if (!running) lines.push("Bot is stopped — press START.");
        const mixOk = !openSide || openSide === side;
        const go = vwapOk && emaOk && crossOk && mixOk && room && running && spreadOk && atrOk && !deskBusy && !cooldown && !pace && !weekend && !mondayOpen && !newsBlock && !sessionOut;
        return {
          go,
          title: go ? `YES — VWAP+EMA9 ${side} on this closed bar` : `NOT ${side === "BUY" ? "BUYING" : "SELLING"}`,
          lines,
        };
      }
      const trendOk = (side === "BUY" && ema200 === "above") || (side === "SELL" && ema200 === "below");
      const crossOk = cross === side;
      const zeroOk = (side === "BUY" && zero === "below") || (side === "SELL" && zero === "above");
      if (trendOk) lines.push(`M5 close is ${ema200} EMA200 — Trading Rush only ${side === "BUY" ? "buys" : "sells"} this way.`);
      else lines.push(`M5 close is ${ema200} EMA200. ${side} needs close ${side === "BUY" ? "above" : "below"} EMA200.`);
      if (crossOk) lines.push(`Fresh MACD 12/26/9 ${side} cross on the closed bar.`);
      else lines.push(`No fresh MACD ${side} cross (last cross=${cross}).`);
      if (crossOk && !zeroOk) lines.push(side === "BUY" ? "BUY cross must be below the zero line." : "SELL cross must be above the zero line.");
      if (openSide && openSide !== side) lines.push(`Already ${openSide} × ${open.length} — mixing BUY and SELL is blocked.`);
      else if (openSide === side && open.length) lines.push(`Already ${side} × ${open.length}. Max 1 ticket per pair.`);
      if (deskBusy) lines.push("Another pair is already open.");
      if (!room) lines.push(`Book is full (${open.length}/${maxPos}).`);
      if (cooldown) lines.push(cooldown);
      if (pace) lines.push(pace);
      if (weekend) lines.push(weekend);
      if (mondayOpen) lines.push(mondayOpen);
      if (newsBlock) lines.push(newsHit.reason || `${symbol} news blackout ${newsHit.name || ""}`.trim());
      if (!atrOk) lines.push(`ATR ${atrNow.toFixed(1)} below floor ${atrFloor} — skip dead tape.`);
      if (sessionOut) lines.push(`Outside London hours (${startH}–${endH} UTC).`);
      if (!spreadOk) lines.push(`Spread ${spread} pip — cap is ${maxSpread}.`);
      if (!running) lines.push("Bot is stopped — press START.");
      const k = book?.kronos || {};
      const kronosOk = !k.enabled || (side === "BUY" ? k.allow_buy !== false : k.allow_sell !== false);
      if (k.enabled) {
        const pred = String(k.predicted || "n/a").toUpperCase();
        const bps = k.bps != null ? `${Number(k.bps) >= 0 ? "+" : ""}${Number(k.bps).toFixed(1)} bps` : "no slope";
        if (pred === "CHOP") lines.push(`Kronos CHOP (no opinion, ${bps}) — MACD ${side} may still fire.`);
        else if (kronosOk) lines.push(`Kronos ${pred} (${bps}) agrees — allow ${side}.`);
        else lines.push(`Kronos ${pred} (${bps}) — skip ${side}. ${side === "BUY" ? k.reason_buy : k.reason_sell || ""}`.trim());
      }
      const mixOk = !openSide || openSide === side;
      const go = trendOk && crossOk && zeroOk && mixOk && room && running && spreadOk && atrOk && !deskBusy && !cooldown && !pace && !weekend && !mondayOpen && !newsBlock && !sessionOut && kronosOk;
      return {
        go,
        title: go ? `YES — MACD ${side} on this closed bar` : `NOT ${side === "BUY" ? "BUYING" : "SELLING"}`,
        lines,
      };
    }

    const buy = forSide("BUY");
    const sell = forSide("SELL");
    const tag = strat === "vwap_ema" ? "EMA9×VWAP" : "MACD";
    let now = `${symbol}: NOT BUYING · NOT SELLING`;
    if (buy.go) now = `${symbol}: BUYING on this ${tag} cross`;
    else if (sell.go) now = `${symbol}: SELLING on this ${tag} cross`;
    else if (openSide === "BUY") now = `${symbol}: HOLDING ${open.length} BUY · not adding`;
    else if (openSide === "SELL") now = `${symbol}: HOLDING ${open.length} SELL · not adding`;

    const kGate = book?.kronos || {};
    const kPred = String(kGate.predicted || "n/a").toUpperCase();
    const kBps = kGate.bps != null ? `${Number(kGate.bps) >= 0 ? "+" : ""}${Number(kGate.bps).toFixed(1)}bps` : "—";
    const nextNews = s?.news?.next;
    const newsValue = newsBlock
      ? String(newsHit.name || "print")
      : nextNews
        ? `next ${nextNews.name}`
        : "clear";
    const gates = strat === "vwap_ema"
      ? [
          { ok: vwapSide === "above" || vwapSide === "below", label: "Price vs VWAP", value: vwapSide },
          { ok: ema9Side === "above" || ema9Side === "below", label: "EMA9 vs VWAP", value: ema9Side },
          { ok: vwapCross === "BUY" || vwapCross === "SELL", label: "EMA9 × VWAP", value: vwapCross },
        ]
      : [
          { ok: ema200 === "above" || ema200 === "below", label: "M5 vs EMA200", value: ema200 },
          { ok: cross === "BUY" || cross === "SELL", label: "MACD 12/26/9 cross", value: cross },
          { ok: zero === "below" || zero === "above", label: "MACD vs zero", value: zero },
          { ok: !kGate.enabled || Boolean(kGate.allow_buy || kGate.allow_sell), label: "Kronos", value: kGate.enabled ? (kPred === "CHOP" ? `CHOP · MACD ok` : `${kPred} ${kBps}`) : "off" },
        ];
    gates.push(
      { ok: !sessionOut, label: "London hours", value: sessionOut ? "closed" : `${startH}–${endH} UTC` },
      { ok: spreadOk, label: "Spread", value: spread != null ? `${spread} / ${maxSpread} pip` : "—" },
      { ok: atrOk, label: atrGate ? "ATR" : "ATR (off)", value: atrNow != null ? `${atrNow.toFixed(1)} / ${atrFloor} pip` : "—" },
      { ok: !newsBlock, label: "News", value: s?.news_blackout === false ? "off" : newsValue },
      { ok: !mondayOpen, label: "Monday open", value: mondayOpen ? mondayOpen : "ok" },
      { ok: !cooldown, label: "Stop cooldown", value: cooldown ? cooldown : "clear" },
      { ok: room, label: "Ticket room", value: `${open.length}/${maxPos}` },
      { ok: running, label: "Bot", value: running ? "running" : "stopped" },
    ];
    return { now, h1, h4, buy, sell, gates, analysis: a };
  }

  const MIN_SPAN = 8;
  const LIVE_PAD = 8;
  const PLOT_PAD_L = 16;
  const PLOT_PAD_R = 108;
  let chartPrefs = { chartType: "candles", showEma: true, showMacd: true, showRsi: false, showBb: false };

  function size(canvas) {
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    const w = canvas.offsetWidth * 2;
    const h = canvas.offsetHeight * 2;
    if (w < 4 || h < 4) return null;
    canvas.width = w;
    canvas.height = h;
    return [w, h, ctx];
  }
  function tvState(canvas) {
    if (!canvas._tv) canvas._tv = { start: 0, end: 0, follow: true, inited: false, dragging: false, lastX: 0 };
    return canvas._tv;
  }
  function livePadOf(canvas, override) {
    if (override != null) return Math.max(0, override);
    return Math.max(0, Number(canvas.dataset.livePad || 0));
  }
  function padForSpan(span, requested) {
    const minBars = Math.min(5, Math.max(3, span * 0.55));
    const maxPad = Math.max(0.4, span - minBars);
    return Math.max(0.4, Math.min(requested, span * 0.16, maxPad));
  }
  function followWindow(n, span, requestedPad) {
    const pad = padForSpan(span, requestedPad);
    const end = n + pad;
    let start = end - span;
    if (start > n - 1) start = Math.max(0, n - Math.min(span, n));
    if (start < 0) start = 0;
    return [start, end];
  }
  function visibleWindow(canvas, n, defaultSpan, livePad) {
    const tv = tvState(canvas);
    const cap = Math.max(n, 1);
    const requested = livePadOf(canvas, livePad);
    let span = tv.inited && tv.end > tv.start ? Math.max(MIN_SPAN, tv.end - tv.start) : Math.max(MIN_SPAN, defaultSpan || 80);
    const spanWanted = canvas._fitAll
      ? cap + padForSpan(cap, requested)
      : Math.max(MIN_SPAN, Math.min((defaultSpan || 80) + padForSpan(span, requested), cap + requested));
    if (!tv.inited || tv.end <= tv.start) {
      tv.inited = true;
      tv.follow = true;
      const w = followWindow(cap, Math.min(spanWanted, cap + requested), requested);
      tv.start = w[0];
      tv.end = w[1];
    }
    span = Math.max(MIN_SPAN, tv.end - tv.start);
    span = Math.min(span, cap + requested);
    if (tv.follow) {
      const w = followWindow(cap, span, requested);
      tv.start = w[0];
      tv.end = w[1];
    } else {
      const pad = padForSpan(span, requested);
      if (tv.end > cap + pad) {
        tv.end = cap + pad;
        tv.start = Math.max(0, tv.end - span);
      }
      if (tv.start < 0) tv.start = 0;
      if (tv.end - tv.start < MIN_SPAN) tv.end = Math.min(cap + pad, tv.start + MIN_SPAN);
      if (tv.end >= cap - 0.2) tv.follow = true;
    }
    canvas.dataset.chartN = String(cap);
    canvas.dataset.livePad = String(requested);
    return [tv.start, tv.end];
  }
  function resetChartZoom(canvas, mode) {
    const tv = tvState(canvas);
    tv.inited = false;
    tv.follow = true;
    canvas._fitAll = mode === "all";
  }
  function clampSpan(next, n, pad) {
    return Math.max(MIN_SPAN, Math.min(n + pad, next));
  }
  function zoomChart(canvas, factor) {
    const n = Number(canvas.dataset.chartN || 0);
    if (n < 2) return;
    const requested = livePadOf(canvas);
    const tv = tvState(canvas);
    const [a, b] = visibleWindow(canvas, n);
    const next = clampSpan((b - a) * factor, n, requested);
    canvas._fitAll = false;
    if (tv.follow) {
      const w = followWindow(n, next, requested);
      tv.start = w[0];
      tv.end = w[1];
      return;
    }
    const mid = (a + b) / 2;
    tv.start = mid - next / 2;
    tv.end = tv.start + next;
  }
  function jumpToLive(canvas) {
    const n = Number(canvas.dataset.chartN || 0);
    const requested = livePadOf(canvas);
    const tv = tvState(canvas);
    tv.follow = true;
    tv.inited = true;
    canvas._fitAll = false;
    const span = Math.min(36, Math.max(MIN_SPAN, n * 0.16 || MIN_SPAN));
    const w = followWindow(Math.max(n, 1), span, requested);
    tv.start = w[0];
    tv.end = w[1];
  }
  function bindChartZoom(canvas, onChange, onHover) {
    if (!canvas || canvas._tvBound) return;
    canvas._tvBound = true;
    canvas.style.touchAction = "none";
    canvas.style.cursor = "crosshair";
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const n = Number(canvas.dataset.chartN || 0);
      if (n < 2) return;
      const tv = tvState(canvas);
      const requested = livePadOf(canvas);
      const [a, b] = visibleWindow(canvas, n);
      const rect = canvas.getBoundingClientRect();
      const plotL = PLOT_PAD_L / 2;
      const plotR = rect.width - PLOT_PAD_R / 2;
      const frac = Math.min(1, Math.max(0, (e.clientX - rect.left - plotL) / Math.max(plotR - plotL, 1)));
      const span = Math.max(MIN_SPAN, b - a);
      const next = clampSpan(span * (e.deltaY > 0 ? 1.18 : 0.82), n, requested);
      canvas._fitAll = false;
      if (tv.follow || frac > 0.82) {
        tv.follow = true;
        const w = followWindow(n, next, requested);
        tv.start = w[0];
        tv.end = w[1];
      } else {
        const center = a + frac * span;
        tv.follow = false;
        tv.start = center - next * frac;
        tv.end = tv.start + next;
      }
      onChange();
    }, { passive: false });
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      canvas.setPointerCapture(e.pointerId);
      const tv = tvState(canvas);
      tv.dragging = true;
      tv.lastX = e.clientX;
      canvas.style.cursor = "grabbing";
    });
    let hoverRaf = 0;
    const hover = () => {
      if (!onHover) return;
      if (hoverRaf) return;
      hoverRaf = requestAnimationFrame(() => {
        hoverRaf = 0;
        onHover();
      });
    };
    canvas.addEventListener("pointermove", (e) => {
      const rect = canvas.getBoundingClientRect();
      canvas._cross = {
        x: ((e.clientX - rect.left) / Math.max(rect.width, 1)) * canvas.width,
        y: ((e.clientY - rect.top) / Math.max(rect.height, 1)) * canvas.height,
      };
      const tv = tvState(canvas);
      if (!tv.dragging) {
        hover();
        return;
      }
      const n = Number(canvas.dataset.chartN || 0);
      if (n < 2) return;
      const [a, b] = visibleWindow(canvas, n);
      const span = Math.max(MIN_SPAN, b - a);
      const shift = -((e.clientX - tv.lastX) / Math.max(rect.width, 1)) * span;
      tv.lastX = e.clientX;
      tv.follow = false;
      tv.start = a + shift;
      tv.end = tv.start + span;
      const requested = livePadOf(canvas);
      if (tv.start < 0) {
        tv.start = 0;
        tv.end = span;
      }
      const pad = padForSpan(span, requested);
      if (tv.end > n + pad) {
        const w = followWindow(n, span, requested);
        tv.start = w[0];
        tv.end = w[1];
        tv.follow = true;
      }
      onChange();
    });
    canvas.addEventListener("pointerleave", () => {
      canvas._cross = null;
      hover();
    });
    const endDrag = (e) => {
      tvState(canvas).dragging = false;
      canvas.style.cursor = "crosshair";
      try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("dblclick", () => {
      resetChartZoom(canvas, "recent");
      onChange();
    });
  }
  function liveBars(bars, last, bid, ask) {
    if (!bars.length) return bars;
    const out = bars.map((b) => ({ ...b }));
    const px = last ?? out[out.length - 1].c;
    const lastBar = out[out.length - 1];
    lastBar.c = px;
    lastBar.h = Math.max(lastBar.h, px, ask ?? px);
    lastBar.l = Math.min(lastBar.l, px, bid ?? px);
    return out;
  }
  function barIndex(bars, time) {
    if (!time) return bars.length - 1;
    const match = String(time).match(/(\d{2}:\d{2})/);
    const needle = match ? match[1] : time.length >= 5 ? time.slice(0, 5) : time;
    for (let i = bars.length - 1; i >= 0; i--) {
      if (String(bars[i].t).includes(needle)) return i;
    }
    return bars.length - 1;
  }
  function chartPxDigits(symbol) {
    const s = String(symbol || "").toUpperCase();
    if (s.includes("XAU") || s.includes("GOLD")) return 2;
    if (s.endsWith("JPY")) return 3;
    return 5;
  }
  function fmtChartPx(symbol, n) {
    return Number(n).toFixed(chartPxDigits(symbol));
  }
  function barTimeLabel(t) {
    const s = String(t || "");
    const m = s.match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (m) {
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[4]}:${m[5]}`;
    }
    const hm = s.match(/(\d{2}:\d{2})/);
    return hm ? hm[1] : s.slice(0, 16);
  }
  function priceTag(ctx, x, y, text, bg, plotTop, plotBot) {
    ctx.font = "700 20px IBM Plex Mono";
    const tw = ctx.measureText(text).width;
    const padX = 10;
    const bh = 26;
    const cy = Math.min(plotBot - bh / 2 - 2, Math.max(plotTop + bh / 2 + 2, y));
    ctx.fillStyle = bg;
    ctx.beginPath();
    ctx.moveTo(x, cy);
    ctx.lineTo(x + 8, cy - bh / 2);
    ctx.lineTo(x + 8 + tw + padX * 2, cy - bh / 2);
    ctx.lineTo(x + 8 + tw + padX * 2, cy + bh / 2);
    ctx.lineTo(x + 8, cy + bh / 2);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.fillText(text, x + 8 + padX, cy + 7);
  }
  function emaSeries(values, period) {
    const out = [];
    const k = 2 / (period + 1);
    let prev = values[0] || 0;
    values.forEach((v, i) => {
      prev = i === 0 ? v : v * k + prev * (1 - k);
      out.push(prev);
    });
    return out;
  }
  function sessionVwapSeries(bars, startHour, startMin) {
    const out = [];
    let key = null;
    let pv = 0;
    let vol = 0;
    const start = (Number(startHour) || 0) * 60 + (Number(startMin) || 0);
    bars.forEach((b) => {
      const raw = String(b.t || "");
      const iso = raw.includes("T") ? raw : raw.replace(" ", "T") + (raw.endsWith("Z") ? "" : "Z");
      const t = new Date(iso);
      const minutes = t.getUTCHours() * 60 + t.getUTCMinutes();
      const day = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate());
      const sk = Number.isNaN(t.getTime()) ? 0 : minutes >= start ? day : day - 86400000;
      if (key !== sk) {
        key = sk;
        pv = 0;
        vol = 0;
      }
      const typical = (Number(b.h) + Number(b.l) + Number(b.c)) / 3;
      const v = Number(b.v) > 0 ? Number(b.v) : 1;
      pv += typical * v;
      vol += v;
      out.push(vol ? pv / vol : typical);
    });
    return out;
  }
  function macdSeries(closes, fast, slow, signal) {
    fast = fast || 12; slow = slow || 26; signal = signal || 9;
    const fastE = emaSeries(closes, fast);
    const slowE = emaSeries(closes, slow);
    const macd = closes.map((_, i) => fastE[i] - slowE[i]);
    const sig = emaSeries(macd, signal);
    return { macd, signal: sig, hist: macd.map((m, i) => m - sig[i]) };
  }
  function macdCrossFlags(closes) {
    const { macd, signal } = macdSeries(closes);
    const flags = closes.map(() => null);
    for (let i = 1; i < closes.length; i++) {
      const prevM = macd[i - 1], lastM = macd[i];
      const prevS = signal[i - 1], lastS = signal[i];
      if (prevM <= prevS && lastM > lastS) flags[i] = lastM < 0 ? "BUY" : "BUY_HI";
      else if (prevM >= prevS && lastM < lastS) flags[i] = lastM > 0 ? "SELL" : "SELL_LO";
    }
    return flags;
  }
  function drawMacdCrosses(ctx, view, lo, flags, xAt, y, upCol, downCol) {
    view.forEach((b, j) => {
      const i = lo + j;
      const flag = flags[i];
      if (!flag) return;
      const buy = flag === "BUY" || flag === "BUY_HI";
      const valid = flag === "BUY" || flag === "SELL";
      const color = buy ? upCol : downCol;
      const cx = xAt(i);
      const cy = buy ? y(b.l) + 14 : y(b.h) - 14;
      ctx.beginPath();
      if (buy) {
        ctx.moveTo(cx, cy - 7);
        ctx.lineTo(cx - 6, cy + 5);
        ctx.lineTo(cx + 6, cy + 5);
      } else {
        ctx.moveTo(cx, cy + 7);
        ctx.lineTo(cx - 6, cy - 5);
        ctx.lineTo(cx + 6, cy - 5);
      }
      ctx.closePath();
      if (valid) {
        ctx.fillStyle = color;
        ctx.fill();
      } else {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }
    });
  }
  function drawKronosHud(ctx, kronos, left, top, axis, upCol, downCol) {
    if (!kronos) return;
    const pred = String(kronos.predicted || "n/a").toUpperCase();
    const bps = kronos.bps == null || Number.isNaN(Number(kronos.bps))
      ? "—"
      : `${Number(kronos.bps) >= 0 ? "+" : ""}${Number(kronos.bps).toFixed(1)}bps`;
    const age = kronos.age_sec == null
      ? ""
      : kronos.age_sec < 90
        ? `${Math.round(kronos.age_sec)}s`
        : `${Math.round(kronos.age_sec / 60)}m`;
    let allow = "watching";
    if (kronos.enabled) {
      if (kronos.allow_buy && kronos.allow_sell) allow = "neutral";
      else if (kronos.allow_buy && !kronos.allow_sell) allow = "allows BUY";
      else if (kronos.allow_sell && !kronos.allow_buy) allow = "allows SELL";
      else allow = "skip both";
    }
    const text = `KRONOS ${pred} ${bps} · ${allow}${age ? ` · ${age}` : ""}${kronos.enabled ? "" : " · gate off"}`;
    ctx.font = "14px IBM Plex Mono";
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = "rgba(17,19,23,0.82)";
    ctx.fillRect(left, top, tw + 16, 22);
    ctx.fillStyle = pred === "BUY" ? upCol : pred === "SELL" ? downCol : axis;
    ctx.textAlign = "left";
    ctx.fillText(text, left + 8, top + 16);
  }
  function rsiSeries(closes, period) {
    period = period || 14;
    const out = closes.map(() => null);
    if (closes.length <= period) return out;
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    let avgG = gain / period, avgL = loss / period;
    out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period;
      avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period;
      out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
    }
    return out;
  }
  function bbSeries(closes, period, k) {
    period = period || 20; k = k || 2;
    const mid = [], up = [], lo = [];
    for (let i = 0; i < closes.length; i++) {
      if (i < period - 1) { mid.push(null); up.push(null); lo.push(null); continue; }
      let s = 0, s2 = 0;
      for (let j = i - period + 1; j <= i; j++) { s += closes[j]; s2 += closes[j] * closes[j]; }
      const m = s / period;
      const sd = Math.sqrt(Math.max(s2 / period - m * m, 0));
      mid.push(m); up.push(m + k * sd); lo.push(m - k * sd);
    }
    return { mid, up, lo };
  }
  function strokeSeries(ctx, vals, xAt, yAt, lo, color, width) {
    ctx.beginPath();
    let started = false;
    vals.forEach((v, j) => {
      if (v == null || Number.isNaN(v)) { started = false; return; }
      const x = xAt(lo + j), y = yAt(v);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.stroke();
  }
  function drawMacdPanel(ctx, o) {
    const { series, lo, view, xAt, left, right, top, height, up, down, axis } = o;
    const { macd, signal, hist } = macdSeries(series.map((b) => b.c));
    const slice = (arr) => arr.slice(lo, lo + view.length);
    const vMacd = slice(macd), vSig = slice(signal), vHist = slice(hist);
    const peak = Math.max(...vMacd.map(Math.abs), ...vSig.map(Math.abs), ...vHist.map(Math.abs), Number.EPSILON);
    const mid = top + height / 2;
    const my = (v) => mid - (v / peak) * (height / 2) * 0.88;
    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, right - left, height);
    ctx.clip();
    ctx.strokeStyle = "rgba(255,255,255,0.14)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(left, mid);
    ctx.lineTo(right, mid);
    ctx.stroke();
    const cw = (right - left) / Math.max(view.length, 1);
    const barW = Math.max(cw * 0.5, 2);
    vHist.forEach((v, j) => {
      const x = xAt(lo + j);
      const shrinking = j > 0 && Math.abs(v) < Math.abs(vHist[j - 1]);
      ctx.fillStyle = v >= 0 ? (shrinking ? "rgba(34,197,94,0.45)" : up) : (shrinking ? "rgba(249,115,22,0.45)" : down);
      const yv = my(v);
      ctx.fillRect(x - barW / 2, Math.min(yv, mid), barW, Math.max(Math.abs(yv - mid), 1));
    });
    strokeSeries(ctx, vMacd, xAt, my, lo, "#60a5fa", 2.2);
    strokeSeries(ctx, vSig, xAt, my, lo, "#f5c451", 2.0);
    ctx.restore();
    const lastM = vMacd[vMacd.length - 1] || 0;
    const lastS = vSig[vSig.length - 1] || 0;
    ctx.font = "15px IBM Plex Mono";
    ctx.textAlign = "left";
    ctx.fillStyle = axis;
    ctx.fillText("MACD 12/26/9", left + 6, top + 16);
    ctx.fillStyle = "#60a5fa";
    ctx.fillText(lastM.toFixed(5), left + 150, top + 16);
    ctx.fillStyle = "#f5c451";
    ctx.fillText(lastS.toFixed(5), left + 250, top + 16);
    ctx.fillStyle = lastM >= 0 ? up : down;
    ctx.fillText(lastM >= 0 ? "above zero" : "below zero", left + 350, top + 16);
  }
  function drawRsiPanel(ctx, o) {
    const { series, lo, view, xAt, left, right, top, height, up, down, axis } = o;
    const rsi = rsiSeries(series.map((b) => b.c));
    const vRsi = rsi.slice(lo, lo + view.length);
    const yR = (v) => top + height - (v / 100) * height * 0.92 - height * 0.04;
    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, right - left, height);
    ctx.clip();
    ctx.strokeStyle = "rgba(255,255,255,0.14)";
    ctx.lineWidth = 1;
    [30, 50, 70].forEach((lvl) => {
      ctx.beginPath();
      ctx.moveTo(left, yR(lvl));
      ctx.lineTo(right, yR(lvl));
      ctx.stroke();
    });
    strokeSeries(ctx, vRsi, xAt, yR, lo, "#e879f9", 2.0);
    ctx.restore();
    const last = vRsi[vRsi.length - 1];
    ctx.font = "15px IBM Plex Mono";
    ctx.textAlign = "left";
    ctx.fillStyle = axis;
    ctx.fillText("RSI 14", left + 6, top + 16);
    if (last != null) {
      ctx.fillStyle = last >= 70 ? down : last <= 30 ? up : "#e879f9";
      ctx.fillText(last.toFixed(1), left + 90, top + 16);
      ctx.fillText(last >= 70 ? "overbought" : last <= 30 ? "oversold" : "mid", left + 150, top + 16);
    }
  }
  function drawCandles(canvas, bars, opts) {
    const sized = size(canvas);
    if (!sized) return;
    const [w, h, ctx] = sized;
    ctx.fillStyle = "#111317";
    ctx.fillRect(0, 0, w, h);
    opts = opts || {};
    const series = liveBars(bars || [], opts.last, opts.bid, opts.ask);
    if (series.length < 2) {
      ctx.fillStyle = "#8b9099";
      ctx.font = "20px IBM Plex Mono";
      ctx.fillText("waiting for M5 candles…", 24, h / 2);
      return;
    }
    const padL = PLOT_PAD_L, padR = PLOT_PAD_R, padT = 16, padB = 40;
    const [i0, i1] = visibleWindow(canvas, series.length, 80, LIVE_PAD);
    const lo = Math.max(0, Math.floor(i0));
    const hi = Math.min(series.length, Math.ceil(i1));
    const view = series.slice(lo, Math.max(lo + 2, hi));
    const closes = series.map((b) => b.c);
    const ema20 = opts.showEma ? emaSeries(closes, 20) : [];
    const ema50 = opts.showEma ? emaSeries(closes, 50) : [];
    const ema200 = opts.showEma ? emaSeries(closes, 200) : [];
    const vwapOn = String(opts.strategy || "") === "vwap_ema";
    const ema9 = vwapOn ? emaSeries(closes, 9) : [];
    const vwap = vwapOn ? sessionVwapSeries(series, opts.sessionStartHour, opts.sessionStartMinute) : [];
    const bb = opts.showBb ? bbSeries(closes) : null;
    const prices = view.flatMap((b) => [b.h, b.l]);
    for (const p of opts.open || []) prices.push(Number(p.price));
    if (opts.last) prices.push(opts.last);
    if (opts.bid) prices.push(opts.bid);
    if (opts.ask) prices.push(opts.ask);
    const takeOverlay = (arr) => {
      for (let j = 0; j < view.length; j++) {
        const v = arr[lo + j];
        if (typeof v === "number" && Number.isFinite(v)) prices.push(v);
      }
    };
    if (opts.showEma) { takeOverlay(ema20); takeOverlay(ema50); takeOverlay(ema200); }
    if (vwapOn) { takeOverlay(ema9); takeOverlay(vwap); }
    if (bb) { takeOverlay(bb.up); takeOverlay(bb.lo); }
    const rawMax = Math.max(...prices);
    const rawMin = Math.min(...prices);
    const pad = (rawMax - rawMin) * 0.06 || 0.0001;
    const max = rawMax + pad;
    const min = rawMin - pad;
    const span = max - min || 0.0001;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const macdOn = Boolean(opts.showMacd);
    const rsiOn = Boolean(opts.showRsi);
    const paneN = Number(macdOn) + Number(rsiOn);
    const macdGap = paneN ? 14 : 0;
    const paneShare = paneN === 2 ? 0.18 : paneN === 1 ? 0.26 : 0;
    const paneH = paneN ? Math.max(48, Math.round(plotH * paneShare)) : 0;
    const panesH = paneN * paneH + Math.max(0, paneN - 1) * 8 + (paneN ? macdGap : 0);
    const priceH = plotH - panesH;
    const cw = plotW / Math.max(i1 - i0, 1);
    const y = (v) => padT + ((max - v) / span) * priceH;
    const xAt = (i) => padL + (i - i0) * cw + cw * 0.5;
    const last = opts.last ?? series[series.length - 1].c;
    const upCol = "#22c55e";
    const downCol = "#f97316";
    const axis = "#8b9099";
    const symbol = opts.symbol;
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    ctx.font = "18px IBM Plex Mono";
    ctx.fillStyle = axis;
    ctx.textAlign = "left";
    const ticks = 6;
    for (let i = 0; i < ticks; i++) {
      const t = max - (span * i) / (ticks - 1);
      const gy = y(t);
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(w - padR, gy);
      ctx.stroke();
      ctx.fillText(fmtChartPx(symbol, t), w - padR + 10, gy + 6);
    }
    ctx.save();
    ctx.beginPath();
    ctx.rect(padL, padT, plotW, priceH);
    ctx.clip();
    if (opts.chartType === "line") {
      const rising = view[view.length - 1].c >= view[0].c;
      const lineCol = rising ? upCol : downCol;
      ctx.beginPath();
      view.forEach((b, j) => {
        const x = xAt(lo + j);
        if (j === 0) ctx.moveTo(x, y(b.c)); else ctx.lineTo(x, y(b.c));
      });
      ctx.strokeStyle = lineCol;
      ctx.lineWidth = 2.4;
      ctx.lineJoin = "round";
      ctx.stroke();
    } else {
      view.forEach((b, j) => {
        const i = lo + j;
        const x = xAt(i);
        const up = b.c >= b.o;
        ctx.strokeStyle = ctx.fillStyle = up ? upCol : downCol;
        ctx.lineWidth = Math.max(1.2, Math.min(cw * 0.18, 2.2));
        ctx.beginPath();
        ctx.moveTo(x, y(b.h));
        ctx.lineTo(x, y(b.l));
        ctx.stroke();
        const body = Math.max(Math.abs(y(b.c) - y(b.o)), 3);
        ctx.fillRect(x - Math.max(cw * 0.28, 2), Math.min(y(b.o), y(b.c)), Math.max(cw * 0.56, 4), body);
      });
    }
    if (bb) {
      strokeSeries(ctx, bb.up.slice(lo, lo + view.length), xAt, y, lo, "rgba(148,163,184,0.7)", 1.2);
      strokeSeries(ctx, bb.lo.slice(lo, lo + view.length), xAt, y, lo, "rgba(148,163,184,0.7)", 1.2);
      strokeSeries(ctx, bb.mid.slice(lo, lo + view.length), xAt, y, lo, "rgba(226,232,240,0.55)", 1.2);
    }
    if (opts.showEma) {
      strokeSeries(ctx, ema20.slice(lo, lo + view.length), xAt, y, lo, "#22d3ee", 1.6);
      strokeSeries(ctx, ema50.slice(lo, lo + view.length), xAt, y, lo, "#a78bfa", 1.6);
      strokeSeries(ctx, ema200.slice(lo, lo + view.length), xAt, y, lo, "#facc15", 2.0);
    }
    if (vwapOn) {
      strokeSeries(ctx, vwap.slice(lo, lo + view.length), xAt, y, lo, "#fb923c", 2.0);
      strokeSeries(ctx, ema9.slice(lo, lo + view.length), xAt, y, lo, "#38bdf8", 1.8);
    }
    if (opts.showMacd) {
      drawMacdCrosses(ctx, view, lo, macdCrossFlags(closes), xAt, y, upCol, downCol);
    }
    ctx.setLineDash([7, 6]);
    ctx.strokeStyle = downCol;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(padL, y(last));
    ctx.lineTo(w - padR, y(last));
    ctx.stroke();
    ctx.setLineDash([]);
    (opts.closed || []).slice(0, 80).forEach((t) => {
      const i = barIndex(series, t.time);
      if (i < lo || i >= hi) return;
      const px = Number(t.price);
      if (!px) return;
      const win = Number(t.profit) >= 0;
      const cx = xAt(i);
      const cy = y(px);
      ctx.fillStyle = win ? upCol : downCol;
      ctx.beginPath();
      ctx.arc(cx, cy, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#111317";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(cx - 3.2, cy);
      ctx.lineTo(cx + 3.2, cy);
      if (win) {
        ctx.moveTo(cx, cy - 3.2);
        ctx.lineTo(cx, cy + 3.2);
      }
      ctx.stroke();
    });
    (opts.open || []).forEach((p) => {
      const px = Number(p.price);
      const buy = p.side === "BUY";
      const color = buy ? upCol : downCol;
      ctx.setLineDash([5, 5]);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(padL, y(px));
      ctx.lineTo(w - padR, y(px));
      ctx.stroke();
      ctx.setLineDash([]);
      const s = 9;
      const ox = Math.min(w - padR - 18, xAt(series.length - 1));
      const oy = y(px);
      ctx.fillStyle = color;
      ctx.beginPath();
      if (buy) {
        ctx.moveTo(ox, oy - 2);
        ctx.lineTo(ox - s, oy + s);
        ctx.lineTo(ox + s, oy + s);
      } else {
        ctx.moveTo(ox, oy + 2);
        ctx.lineTo(ox - s, oy - s);
        ctx.lineTo(ox + s, oy - s);
      }
      ctx.closePath();
      ctx.fill();
      ctx.font = "16px IBM Plex Mono";
      ctx.fillText(`${p.side} ${Number(p.profit) >= 0 ? "+" : ""}${Number(p.profit).toFixed(2)}`, ox + 12, oy + 5);
    });
    ctx.restore();
    priceTag(ctx, w - padR, y(last), fmtChartPx(symbol, last), downCol, padT, padT + priceH);
    ctx.font = "14px IBM Plex Mono";
    ctx.textAlign = "left";
    let lx = padL + 6;
    const ly = padT + 14;
    const legend = (label, color) => {
      ctx.fillStyle = color;
      ctx.fillRect(lx, ly - 8, 8, 8);
      ctx.fillStyle = axis;
      ctx.fillText(label, lx + 12, ly);
      lx += ctx.measureText(label).width + 24;
    };
    if (opts.showEma) { legend("EMA20", "#22d3ee"); legend("EMA50", "#a78bfa"); legend("EMA200", "#facc15"); }
    if (vwapOn) { legend("VWAP", "#fb923c"); legend("EMA9", "#38bdf8"); }
    if (opts.showBb) legend("BB 20", "#94a3b8");
    drawKronosHud(ctx, opts.kronos, padL + 6, padT + 24, axis, upCol, downCol);
    let paneTop = padT + priceH + macdGap;
    if (macdOn) {
      drawMacdPanel(ctx, { series, lo, view, xAt, left: padL, right: w - padR, top: paneTop, height: paneH, up: upCol, down: downCol, axis });
      paneTop += paneH + 8;
    }
    if (rsiOn) {
      drawRsiPanel(ctx, { series, lo, view, xAt, left: padL, right: w - padR, top: paneTop, height: paneH, up: upCol, down: downCol, axis });
    }
    ctx.fillStyle = axis;
    ctx.font = "16px IBM Plex Mono";
    ctx.textAlign = "center";
    const labelN = Math.min(5, view.length);
    for (let k = 0; k < labelN; k++) {
      const j = Math.round((k * (view.length - 1)) / Math.max(labelN - 1, 1));
      const text = barTimeLabel(view[j].t);
      const half = ctx.measureText(text).width / 2;
      const cx = Math.min(Math.max(xAt(lo + j), padL + half), w - padR - half);
      ctx.fillText(text, cx, h - 12);
    }
    ctx.textAlign = "left";
    const cross = canvas._cross;
    if (cross && cross.x >= padL && cross.x <= w - padR && cross.y >= padT && cross.y <= padT + priceH) {
      ctx.setLineDash([5, 5]);
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(cross.x, padT);
      ctx.lineTo(cross.x, padT + priceH);
      ctx.moveTo(padL, cross.y);
      ctx.lineTo(w - padR, cross.y);
      ctx.stroke();
      ctx.setLineDash([]);
      const price = max - ((cross.y - padT) / priceH) * span;
      const idx = Math.min(view.length - 1, Math.max(0, Math.floor(((cross.x - padL) / plotW) * view.length)));
      const bar = view[idx];
      priceTag(ctx, w - padR, cross.y, fmtChartPx(symbol, price), "#2a2f38", padT, padT + priceH);
      const hud = `${barTimeLabel(bar.t)}   O ${fmtChartPx(symbol, bar.o)}  H ${fmtChartPx(symbol, bar.h)}  L ${fmtChartPx(symbol, bar.l)}  C ${fmtChartPx(symbol, bar.c)}`;
      ctx.font = "16px IBM Plex Mono";
      const hw = ctx.measureText(hud).width;
      ctx.fillStyle = "rgba(17,19,23,0.88)";
      ctx.fillRect(padL, padT + 6, hw + 20, 26);
      ctx.fillStyle = "#e8eaed";
      ctx.fillText(hud, padL + 10, padT + 24);
    }
  }
  function fmtAxisCash(n) {
    const abs = Math.abs(n);
    if (abs >= 1000) return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
    return "$" + n.toFixed(2);
  }
  function drawMoneyGraph(canvas, points, start) {
    const sized = size(canvas);
    if (!sized) return;
    const [w, h, ctx] = sized;
    ctx.clearRect(0, 0, w, h);
    const compact = h < 320 || w < 760;
    const axisFont = compact ? 15 : 20;
    const padL = compact ? 66 : 96;
    const padR = compact ? 88 : 116;
    const padT = compact ? 10 : 22;
    const padB = compact ? 20 : 36;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    if (points.length < 2) {
      ctx.fillStyle = "#8b95a3";
      ctx.font = `${compact ? 12 : 22}px IBM Plex Mono`;
      ctx.fillText("no closed trades yet — curve starts at deposit", padL, h / 2);
      return;
    }
    const maxN = Math.max(...points.map((p) => p.n), 1);
    const [x0, x1] = visibleWindow(canvas, maxN, maxN);
    const spanX = Math.max(x1 - x0, 1e-6);
    const vis = points.filter((p) => p.n >= x0 - 0.01 && p.n <= x1 + 0.01);
    const vals = (vis.length ? vis : points).map((p) => p.balance);
    vals.push(start);
    const rawMin = Math.min(...vals);
    const rawMax = Math.max(...vals);
    const pad = Math.max((rawMax - rawMin) * 0.12, 8);
    const min = rawMin - pad;
    const max = rawMax + pad;
    const span = max - min || 1;
    const xAt = (n) => padL + ((n - x0) / spanX) * plotW;
    const yAt = (v) => padT + ((max - v) / span) * plotH;
    const last = points[points.length - 1];
    const line = last.balance >= start - 0.004 ? "#19e68c" : "#e06a5c";
    const startY = yAt(start);
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    const seenY = [];
    for (const tick of [start, max, min]) {
      const gy = Math.round(yAt(tick));
      if (seenY.some((prev) => Math.abs(prev - gy) < axisFont * 1.9)) continue;
      seenY.push(gy);
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(w - padR, gy);
      ctx.stroke();
      ctx.fillStyle = "#8b95a3";
      ctx.font = axisFont + "px IBM Plex Mono";
      ctx.fillText(fmtAxisCash(tick), 6, gy + Math.round(axisFont * 0.35));
    }
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = "#3a4452";
    ctx.beginPath();
    ctx.moveTo(padL, startY);
    ctx.lineTo(w - padR, startY);
    ctx.stroke();
    ctx.setLineDash([]);
    const realized = points.filter((p) => !p.live);
    const live = points.find((p) => p.live);
    const lastReal = realized[realized.length - 1];
    ctx.strokeStyle = line;
    ctx.lineWidth = 3;
    ctx.beginPath();
    let started = false;
    realized.forEach((p) => {
      if (p.n < x0 - 0.2 || p.n > x1 + 0.2) return;
      if (!started) {
        ctx.moveTo(xAt(p.n), yAt(p.balance));
        started = true;
      } else ctx.lineTo(xAt(p.n), yAt(p.balance));
    });
    ctx.stroke();
    if (live && lastReal && live.n >= x0 && lastReal.n <= x1 + 1) {
      ctx.setLineDash([7, 5]);
      ctx.lineWidth = 2.4;
      ctx.beginPath();
      ctx.moveTo(xAt(lastReal.n), yAt(lastReal.balance));
      ctx.lineTo(xAt(live.n), yAt(live.balance));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#0b0e13";
      ctx.strokeStyle = line;
      ctx.beginPath();
      ctx.arc(xAt(live.n), yAt(live.balance), compact ? 3 : 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    // The start value is already a left-axis tick, so the right gutter only carries the live balance.
    ctx.fillStyle = line;
    ctx.font = axisFont + "px IBM Plex Mono";
    ctx.fillText(fmtAxisCash(last.balance), w - padR + 5, yAt(last.balance) + Math.round(axisFont * 0.35));
    ctx.fillStyle = "#8b95a3";
    ctx.font = axisFont + "px IBM Plex Mono";
    ctx.fillText(String(Math.round(x0)), padL, h - 5);
    ctx.textAlign = "right";
    ctx.fillText(live && x1 >= maxN - 0.2 ? `${Math.round(x1)} + live` : `${Math.round(x1)} trades`, w - padR, h - 5);
    ctx.textAlign = "left";
  }

  function setText(id, text) {
    const n = $(id);
    if (n) n.textContent = text;
  }
  function setChip(el, on, onText, offText) {
    el.textContent = on ? onText : offText;
    el.className = on ? "chip live" : "chip live off";
  }
  function fillList(ul, lines) {
    ul.replaceChildren();
    lines.forEach((line) => {
      const li = document.createElement("li");
      li.textContent = line;
      ul.appendChild(li);
    });
  }
  function emptyRow(cols, text) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols;
    td.className = "empty";
    td.textContent = text;
    tr.appendChild(td);
    return tr;
  }

  function showFlash(ok, text) {
    const box = $("flash");
    box.hidden = false;
    box.className = ok ? "flash ok" : "flash bad";
    $("flash-text").textContent = (ok ? "OK" : "FAIL") + " · " + text;
  }

  async function call(path, body) {
    busy = path;
    renderBusy();
    try {
      const res = await fetch(path, {
        method: "POST",
        credentials: "include",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401) {
        gate = "locked";
        pinRequired = true;
        showFlash(false, authMethod === "totp" ? "authenticator code required" : "PIN required");
        paintGate();
        return;
      }
      const data = await res.json().catch(() => ({}));
      showFlash(Boolean(data.ok ?? res.ok), data.message || (res.ok ? "ok" : `HTTP ${res.status}`));
      await loadState();
    } catch (err) {
      showFlash(false, err instanceof Error ? err.message : "request failed");
    } finally {
      busy = null;
      renderBusy();
    }
  }

  function renderBusy() {
    const disabled = Boolean(busy);
    ["btn-start", "btn-stop", "btn-retry", "btn-apply", "btn-lock", "unlockbtn", "btn-auth", "btn-totp-rotate", "btn-report"].forEach((id) => {
      const n = $(id);
      if (n) n.disabled = disabled;
    });
    if ($("unlockbtn") && !$("pin").value) $("unlockbtn").disabled = true;
    paintTradeButtons();
  }

  function paintReport(s, focus, canTrade) {
    const r = (s?.books?.[focus]?.htf_report || s?.htf_report || {});
    const ready = Boolean(r.ready) && (r.action === "BUY" || r.action === "SELL");
    const bias = $("report-bias");
    if (bias) {
      const shown = ready ? r.action : (r.bias && r.bias !== "HOLD" ? r.bias : "WAIT");
      bias.textContent = shown;
      bias.className = "reportbias" + (
        ready ? ` ${String(r.action).toLowerCase()} ready`
        : r.bias === "BUY" ? " buy"
        : r.bias === "SELL" ? " sell"
        : ""
      );
    }
    [["report-h1", r.h1], ["report-m15", r.m15], ["report-m5", r.m5]].forEach(([id, row]) => {
      const el = $(id);
      if (!el) return;
      const state = String((row && row.state) || "wait");
      el.className = `rstage ${state}`;
      const val = el.querySelector(".rval");
      let label = String((row && row.label) || state);
      label = label.replace(/^(H1|M15|M5)\s+[—–-]\s+/, "");
      if (val) val.textContent = label;
    });
    if ($("report-why")) $("report-why").textContent = r.why || "waiting for H1 / M15 / M5…";
    const btn = $("btn-report");
    if (!btn) return;
    btn.className = ready ? String(r.action).toLowerCase() : "";
    btn.textContent = ready ? `SEND ${r.action} ${focus}` : "SEND WHEN READY";
    btn.disabled = Boolean(busy) || !canTrade || !ready;
  }

  function paintTradeButtons() {
    const focus = nextFocus(state, focusOverride);
    const lot = state?.next_lot != null ? Number(state.next_lot).toFixed(2) : "—";
    const canTrade = Boolean(state?.running && state?.broker && state.broker !== "offline");
    const open = state?.positions || [];
    $("btn-buy").textContent = `BUY ${focus} ${lot}`;
    $("btn-sell").textContent = `SELL ${focus} ${lot}`;
    $("btn-buy").disabled = Boolean(busy) || !canTrade;
    $("btn-sell").disabled = Boolean(busy) || !canTrade;
    $("btn-step").disabled = Boolean(busy) || !canTrade;
    $("btn-close-all").disabled = Boolean(busy) || !open.length;
    $("btn-apply").disabled = Boolean(busy);
    paintReport(state, focus, canTrade);
  }

  function paintGate() {
    const locked = gate === "locked";
    $("lockgate").hidden = !locked;
    $("shell").hidden = locked || gate === "checking";
    const totp = authMethod === "totp";
    if ($("lock-reason")) {
      $("lock-reason").textContent = totp
        ? "Open your authenticator app and enter the 6-digit code. The bot keeps running either way."
        : "Enter your PIN. The bot keeps running on the server either way.";
    }
    if ($("lock-k")) $("lock-k").textContent = totp ? "Authenticator" : "PIN";
    if ($("pin")) $("pin").autocomplete = totp ? "one-time-code" : "off";
    const qr = $("lock-qr");
    const hint = $("lock-qr-hint");
    if (qr) {
      const on = locked && totp && showQr;
      qr.hidden = !on;
      if (hint) hint.hidden = !on;
      if (on) qr.src = `/api/totp/qr?t=${Date.now()}`;
    }
    if ($("lock-reason") && totp && showQr) {
      $("lock-reason").textContent = "Scan the QR in Authenticator, then enter the 6-digit code. The bot keeps running either way.";
    }
  }

  function paint() {
    paintGate();
    if (gate !== "open") return;
    $("shell").hidden = false;
    const s = state;
    const pairs = pairList(s);
    const focus = nextFocus(s, focusOverride);
    const book = s?.books?.[focus];
    const analysis = book?.analysis || s?.analysis || {};
    const trades = s?.closed_trades || [];
    const open = s?.positions || [];
    const maxPos = s?.max_positions || 1;
    const why = deskWhy(s, focus);
    const bid = fmtPx(focus, book?.bid ?? s?.bid);
    const ask = fmtPx(focus, book?.ask ?? s?.ask);
    const px = fmtPx(focus, book?.last_price ?? s?.last_price);
    const mt5 = s?.mt5;
    const mt5On = Boolean(mt5?.attached);
    const fillsInMt5 = Boolean(mt5?.fills_in_terminal);
    const demoBal = mt5On && mt5?.balance != null ? Number(mt5.balance) : null;
    const lot = s?.next_lot != null ? Number(s.next_lot).toFixed(2) : "—";
    const riskPct = s?.risk_pct != null ? Math.round(Number(s.risk_pct) * 100) : 2;
    const equityNow = fillsInMt5 && mt5?.equity != null
      ? Number(mt5.equity)
      : s?.account != null ? Number(s.account.equity) : null;
    const deposit = s?.pnl?.start_deposit != null ? Number(s.pnl.start_deposit) : 100000;
    const recover = s?.pnl?.recover != null
      ? Number(s.pnl.recover)
      : equityNow != null ? Math.max(0, Math.round((deposit - equityNow) * 100) / 100) : null;
    const livePnl = s?.pnl?.floating != null
      ? Number(s.pnl.floating)
      : equityNow != null && demoBal != null ? Math.round((equityNow - demoBal) * 100) / 100 : null;
    const healthStatus = health?.status || (!s ? "CRITICAL" : "");
    const ipcMs = health?.metrics?.ipc_latency_ms;
    const barTone = healthStatus === "HEALTHY" ? "on" : healthStatus === "DEGRADED" ? "warn" : healthStatus === "CRITICAL" ? "off" : mt5On ? "on" : "off";
    const headline = healthStatus === "CRITICAL"
      ? (health?.error_reason || health?.warnings?.[0] || "SYSTEM CRITICAL")
      : healthStatus === "DEGRADED"
        ? (health?.warnings?.[0] || "SYSTEM DEGRADED")
        : mt5On ? "METATRADER CONNECTED" : "METATRADER NOT ATTACHED";

    setChip($("chip-running"), Boolean(s?.running), "RUNNING", "STOPPED");
    setChip($("chip-mt5"), mt5On, "METATRADER ON", "METATRADER OFF");
    setChip($("chip-fills"), fillsInMt5, "FILLS IN MT5 APP", "PAPER FILLS ONLY");
    const hchip = $("chip-health");
    if (healthStatus) {
      hchip.hidden = false;
      hchip.textContent = healthStatus + (ipcMs != null && health?.checks?.ipc_loop_responsive ? ` · IPC ${Number(ipcMs).toFixed(0)}ms` : "");
      hchip.className = healthStatus === "HEALTHY" ? "chip live" : healthStatus === "DEGRADED" ? "chip warn" : "chip live off";
    } else {
      hchip.hidden = true;
    }
    $("chip-open").textContent = `OPEN ${open.length}`;
    $("chip-mode").textContent = s?.trade_mode || "demo";
    $("btn-lock").hidden = !pinRequired;
    if ($("btn-auth")) $("btn-auth").hidden = !pinRequired || authMethod !== "totp";

    const pairBox = $("pairs");
    pairBox.replaceChildren();
    pairs.forEach((pair) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `chip pairtab${pair === focus ? " on" : ""}`;
      btn.disabled = Boolean(busy);
      const extra = s?.books?.[pair]?.open_count ? ` ${s.books[pair].open_count}` : "";
      btn.textContent = pair + extra;
      btn.addEventListener("click", () => {
        focusOverride = pair;
        call("/api/settings", { focus: pair });
      });
      pairBox.appendChild(btn);
    });

    setText("k-last", `${focus} LAST`);
    setText("px-last", px);
    setText("px-bid", bid);
    setText("px-ask", ask);
    setText("px-spread", s?.spread != null ? `${s.spread} pip` : "—");
    setText("px-demo", demoBal != null ? demoBal.toFixed(2) : "—");
    setText("px-lot", lot);
    setText("k-equity", fillsInMt5 ? "MT5 EQUITY" : "PAPER EQUITY");
    setText("px-equity", fillsInMt5 && mt5?.equity != null
      ? Number(mt5.equity).toFixed(2)
      : s?.account ? Number(s.account.equity).toFixed(2) : "—");
    const hole = $("box-hole");
    hole.className = "pxbox " + (recover == null ? "" : recover > 0.004 ? "down" : "up");
    setText("px-hole", recover != null ? `$${recover.toFixed(2)}` : "—");
    const pnlBox = $("box-pnl");
    pnlBox.className = "pxbox " + (livePnl == null ? "" : livePnl > 0.004 ? "up" : livePnl < -0.004 ? "down" : "");
    setText("px-pnl", livePnl != null ? money(livePnl) : "—");

    $("mt5bar").className = `mt5bar ${barTone}`;
    setText("mt5-headline", headline);
    setText("mt5-line", mt5On
      ? fillsInMt5
        ? `Fills hit MetaQuotes-Demo · ${riskPct}% risk · lot ${lot}` + (mt5?.trade_allowed === false ? " · ALGO TRADING OFF" : "")
        : `MT5 quotes only · orders are PAPER · lot ${lot}`
      : "MetaTrader not attached — buys/sells stay PAPER.");
    setText("mt5-meta",
      `${mt5?.login || s?.login || "—"} @ ${mt5?.server || s?.server || "MetaQuotes-Demo"}` +
      (mt5?.trade_allowed === false ? " · ALGO TRADING OFF" : mt5?.error ? ` · ${mt5.error}` : mt5?.company ? ` · ${mt5.company}` : "") +
      (s?.mutex?.ea_active || s?.mutex?.leftover_ea ? " · REMOVE OLD EA FROM CHART" : s?.mutex?.owner === "python" ? " · ONE BOT" : " · ONE BOT IDLE") +
      (health?.warnings?.length ? ` · ${health.warnings.join(" · ")}` : "")
    );

    const today = sessionPnl(s);
    const pnlEl = $("pnl-today");
    pnlEl.textContent = money(today);
    pnlEl.style.color = tone(today);
    const closedEl = $("pnl-closed");
    closedEl.textContent = money(s?.pnl?.closed);
    closedEl.style.color = tone(s?.pnl?.closed);
    const floatEl = $("pnl-float");
    floatEl.textContent = money(s?.pnl?.floating);
    floatEl.style.color = tone(s?.pnl?.floating);
    setText("pnl-wr", `${(s?.win_rate || 0).toFixed(1)}% · ${s?.fills || 0} fills`);
    setText("last-reason", s?.last_reason || (!s ? "waiting for bot API" : "—"));

    setText("chart-k", focus);
    setText("quote-ask", `ASK ${ask}`);
    setText("quote-mid", px);
    setText("quote-bid", `BID ${bid}`);
    setText("quote-spread", `spread ${book?.spread ?? s?.spread ?? "—"}`);
    const chg = rangeChange(book?.bars || s?.bars || [], book?.last_price ?? s?.last_price);
    const chgEl = $("quote-chg");
    if (chgEl) {
      chgEl.textContent = chg ? chg.text : "—";
      chgEl.className = `tvchg ${chg ? (chg.up ? "up" : "down") : ""}`;
    }
    $("tf-m5")?.classList.toggle("on", !$("candles")?._fitAll);
    $("tf-all")?.classList.toggle("on", Boolean($("candles")?._fitAll));

    setText("why-k", `Why ${focus} is buying or selling · Trading Rush MACD · ${pairs.join(" + ")}`);
    setText("why-now", why.now);
    setText("why-head", `${focus} · MACD 12/26/9 + EMA200 + Kronos · H1 ${why.h1} / H4 ${why.h4} · RSI ${analysis.rsi != null ? Number(analysis.rsi).toFixed(1) : "—"} · ATR ${analysis.atr_pips != null ? Number(analysis.atr_pips).toFixed(1) : "—"} pip · ${analysis.trend || "—"} trend`);
    $("buy-card").className = `sidecard ${why.buy.go ? "go" : "stop"}`;
    $("sell-card").className = `sidecard ${why.sell.go ? "go" : "stop"}`;
    const buyTitle = $("buy-title");
    buyTitle.textContent = why.buy.title;
    buyTitle.className = why.buy.go ? "bid" : "ask";
    const sellTitle = $("sell-title");
    sellTitle.textContent = why.sell.title;
    sellTitle.className = why.sell.go ? "ask" : "";
    fillList($("buy-lines"), why.buy.lines);
    fillList($("sell-lines"), why.sell.lines);
    const gates = $("gates");
    gates.replaceChildren();
    why.gates.forEach((g) => {
      const div = document.createElement("div");
      div.className = `gate ${g.ok ? "ok" : "no"}`;
      const span = document.createElement("span");
      span.textContent = g.label;
      const b = document.createElement("b");
      b.textContent = g.value;
      div.append(span, b);
      gates.appendChild(div);
    });

    setText("money-k", `Money graph · demo balance${s?.pnl?.history_count != null ? ` · ${s.pnl.history_count} closed` : ""}`);

    setText("closed-k", `Closed trades · ${fillsInMt5 ? "MT5 history" : "MT5 + paper"}${s?.pnl?.history_count != null ? ` · ${s.pnl.history_count}` : trades.length ? ` · ${trades.length}` : ""}${s?.pnl?.history_net != null ? ` · net ${money(s.pnl.history_net)}` : ""}`);
    const closedKey = `${trades.length}:${trades[0]?.ticket || ""}:${trades[0]?.profit || ""}:${trades[trades.length - 1]?.ticket || ""}`;
    const closedBody = $("closed-body");
    if (closedKey !== lastClosedKey) {
      lastClosedKey = closedKey;
      closedBody.replaceChildren();
      if (!trades.length) {
        closedBody.appendChild(emptyRow(7, "No closed trades in history"));
      } else {
        trades.forEach((t) => {
          const pnl = Number(t.profit || 0);
          const tr = document.createElement("tr");
          tr.className = pnl > 0.004 ? "gain" : pnl < -0.004 ? "loss" : "";
          [fmtTime(t.time), t.symbol || "—", t.side, t.volume, t.price != null ? fmtPx(t.symbol, t.price) : "—", money(t.profit), t.ticket].forEach((val, i) => {
            const td = document.createElement("td");
            td.textContent = String(val ?? "—");
            if (i === 5) td.className = "cash";
            tr.appendChild(td);
          });
          closedBody.appendChild(tr);
        });
      }
    }

    const candleOpts = {
      last: book?.last_price ?? s?.last_price,
      bid: book?.bid ?? s?.bid,
      ask: book?.ask ?? s?.ask,
      open: open.filter((p) => !p.symbol || p.symbol === focus),
      closed: trades.filter((t) => !t.symbol || t.symbol === focus).slice(0, 80),
      symbol: focus,
      chartType: chartPrefs.chartType,
      showEma: chartPrefs.showEma,
      showMacd: chartPrefs.showMacd,
      showRsi: chartPrefs.showRsi,
      showBb: chartPrefs.showBb,
      kronos: book?.kronos,
      strategy: s?.strategy,
      sessionStartHour: s?.session_start_hour,
      sessionStartMinute: s?.session_start_minute,
    };
    lastCandleDraw = { bars: book?.bars || s?.bars || [], opts: candleOpts };
    try {
      drawCandles($("candles"), lastCandleDraw.bars, lastCandleDraw.opts);
    } catch (err) {
      console.warn("candles", err);
    }
    try {
      drawMoneyGraph($("money"), moneyBalanceSeries(trades, deposit, Number(s?.pnl?.floating ?? 0)), deposit);
    } catch (err) {
      console.warn("money", err);
    }

    const openBody = $("open-body");
    openBody.replaceChildren();
    if (!open.length) {
      const canTrade = Boolean(s?.running && s?.broker && s.broker !== "offline");
      const msg = canTrade
        ? `No open tickets. Manual BUY/SELL is for ${focus}. Cap ${maxPos} total; max 1 per pair.`
        : s?.running
          ? "Connecting broker… BUY/SELL unlocks after MT5 or paper is ready."
          : "Press START so the bot connects a broker, then BUY/SELL will fill.";
      openBody.appendChild(emptyRow(8, msg));
    } else {
      open.forEach((p) => {
        const tr = document.createElement("tr");
        const cells = [
          p.symbol || focus,
          p.ticket,
          p.side,
          p.volume,
          fmtPx(p.symbol || focus, p.price),
          money(p.profit),
          p.why || p.verdict || "—",
        ];
        cells.forEach((val, i) => {
          const td = document.createElement("td");
          td.textContent = String(val);
          if (i === 2) td.className = p.side === "BUY" ? "bid" : "ask";
          if (i === 5) td.style.color = tone(p.profit);
          if (i === 6) td.className = "why";
          tr.appendChild(td);
        });
        const td = document.createElement("td");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "stop";
        btn.textContent = "CLOSE";
        const readOnly = p.venue === "mt5" && !fillsInMt5;
        btn.disabled = Boolean(busy) || readOnly;
        btn.title = readOnly ? "This ticket is on the MT5 filler — close it there" : "";
        btn.addEventListener("click", () => call("/api/close", { ticket: p.ticket }));
        td.appendChild(btn);
        tr.appendChild(td);
        openBody.appendChild(tr);
      });
    }

    if (!maxPosTouched && typeof s?.max_positions === "number") {
      $("max-pos").value = String(s.max_positions);
    }
    $("btn-paper").className = fillsInMt5 ? "" : "buy";
    $("btn-paper").disabled = Boolean(busy) || !fillsInMt5;
    $("btn-mt5fills").disabled = Boolean(busy) || fillsInMt5 || !mt5On;
    $("meta").textContent =
      `poll ${s?.poll_seconds || "—"}s · lot ${lot} · ${riskPct}% of demo · SL ${analysis.sl_pips ?? "—"} / TP ${analysis.tp_pips ?? "—"} pips · strategy ${s?.strategy || "macd"}\n` +
      `Kotegawa: ${riskPct}% per ticket · hard SL at structure · TP ${Number(s?.reward_r ?? 2)}R · no size-up after a loss · halt if today ≤ -${Math.round(Number(s?.daily_loss_pct ?? 0.02) * 100)}% equity. Broker SL/TP only — no trail.\n` +
      (fillsInMt5 ? "BUY/SELL hits the MetaQuotes demo account." : "BUY/SELL stays on paper even if MetaTrader is connected.") +
      (busy ? ` · busy ${busy}` : "");

    const log = $("log");
    log.replaceChildren();
    const lines = (s?.logs || []).slice(0, 40);
    if (!lines.length) {
      const li = document.createElement("li");
      li.textContent = "waiting for bot feed…";
      log.appendChild(li);
    } else {
      lines.forEach((line) => {
        const li = document.createElement("li");
        li.textContent = line;
        log.appendChild(li);
      });
    }
    setText("foot-feed", (book?.blockers || s?.blockers || [])[0] || s?.last_reason || "ready");
    setText("foot-account", s?.account ? `${s.account.login} @ ${s.account.server}` : `${s?.broker || "offline"}`);
    $("clock-local").classList.toggle("on", open.length > 0);
    paintTradeButtons();
  }

  async function loadState() {
    try {
      const res = await fetch("/api/state", { credentials: "include", cache: "no-store" });
      if (res.status === 401) {
        gate = "locked";
        pinRequired = true;
        paintGate();
        return;
      }
      if (!res.ok) return;
      state = await res.json();
      paint();
    } catch {
      // keep last paint
    }
  }
  async function loadHealth() {
    try {
      const res = await fetch("/api/health", { credentials: "include" });
      if (res.status === 401) {
        gate = "locked";
        pinRequired = true;
        paintGate();
        return;
      }
      health = await res.json();
      paint();
    } catch {
      health = {
        status: "CRITICAL",
        checks: {},
        metrics: { ipc_latency_ms: 0 },
        warnings: ["Flask /api/health is unreachable"],
        error_reason: "API unreachable",
      };
      paint();
    }
  }

  function tickClocks() {
    const now = new Date();
    $("clock-utc").textContent = now.toISOString().slice(11, 19) + " UTC";
    $("clock-local").textContent = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    }).format(now);
  }

  $("lockform").addEventListener("submit", async (event) => {
    event.preventDefault();
    busy = "unlock";
    $("pinerror").hidden = true;
    renderBusy();
    try {
      const res = await fetch("/api/unlock", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: $("pin").value, pin: $("pin").value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        $("pinerror").hidden = false;
        $("pinerror").textContent = data.message || (authMethod === "totp" ? "wrong code" : "wrong PIN");
        return;
      }
      $("pin").value = "";
      pinRequired = Boolean(data.required ?? true);
      showQr = Boolean(data.show_qr);
      gate = "open";
      paintGate();
      await loadState();
      await loadHealth();
      startPolling();
    } catch {
      $("pinerror").hidden = false;
      $("pinerror").textContent = "could not reach the desk";
    } finally {
      busy = null;
      renderBusy();
    }
  });
  $("pin").addEventListener("input", () => {
    $("unlockbtn").disabled = Boolean(busy) || !$("pin").value;
  });
  $("btn-lock").addEventListener("click", async () => {
    await call("/api/lock");
    gate = "locked";
    pinRequired = true;
    state = null;
    health = null;
    try {
      const res = await fetch("/api/unlock", { credentials: "include" });
      const next = await res.json();
      showQr = Boolean(next.show_qr);
      if (next.method) authMethod = next.method;
    } catch {
      /* keep last */
    }
    paintGate();
  });
  function openAuth() {
    const panel = $("authgate");
    const img = $("auth-qr");
    if (!panel || !img) return;
    img.src = `/api/totp/qr?t=${Date.now()}`;
    if ($("auth-msg")) $("auth-msg").textContent = "";
    if ($("auth-code")) $("auth-code").value = "";
    panel.hidden = false;
  }
  $("btn-auth")?.addEventListener("click", openAuth);
  $("btn-auth-close")?.addEventListener("click", () => {
    if ($("authgate")) $("authgate").hidden = true;
  });
  $("btn-totp-rotate")?.addEventListener("click", async () => {
    const code = $("auth-code")?.value || "";
    if ($("auth-msg")) $("auth-msg").textContent = "";
    busy = "totp-rotate";
    renderBusy();
    try {
      const res = await fetch("/api/totp/rotate", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        if ($("auth-msg")) $("auth-msg").textContent = data.message || "wrong code";
        return;
      }
      if ($("auth-qr")) $("auth-qr").src = `/api/totp/qr?t=${Date.now()}`;
      if ($("auth-code")) $("auth-code").value = "";
      if ($("auth-msg")) $("auth-msg").textContent = data.message || "Scan the new QR. Old codes no longer work.";
    } catch {
      if ($("auth-msg")) $("auth-msg").textContent = "could not reach the desk";
    } finally {
      busy = null;
      renderBusy();
    }
  });
  $("btn-start").addEventListener("click", () => call("/api/start"));
  $("btn-stop").addEventListener("click", () => call("/api/stop"));
  $("btn-retry").addEventListener("click", () => call("/api/reconnect"));
  $("btn-buy").addEventListener("click", () => call("/api/buy", { symbol: nextFocus(state, focusOverride) }));
  $("btn-sell").addEventListener("click", () => call("/api/sell", { symbol: nextFocus(state, focusOverride) }));
  $("btn-report")?.addEventListener("click", () => call("/api/report/take", { symbol: nextFocus(state, focusOverride) }));
  $("btn-step").addEventListener("click", () => call("/api/step"));
  $("btn-close-all").addEventListener("click", () => call("/api/close-all"));
  $("btn-apply").addEventListener("click", () => call("/api/settings", { max_positions: Number($("max-pos").value) }));
  $("max-pos").addEventListener("input", () => { maxPosTouched = true; });
  $("btn-paper").addEventListener("click", () => call("/api/settings", { fill_venue: "paper" }));
  $("btn-mt5fills").addEventListener("click", () => call("/api/settings", { fill_venue: "mt5" }));
  $("flash-dismiss").addEventListener("click", () => { $("flash").hidden = true; });

  const ro = new ResizeObserver(() => { if (gate === "open" && state) paint(); });
  ro.observe($("candles"));
  ro.observe($("money"));
  bindChartZoom($("candles"), () => { if (gate === "open" && state) paint(); }, () => {
    if (lastCandleDraw) drawCandles($("candles"), lastCandleDraw.bars, lastCandleDraw.opts);
  });
  bindChartZoom($("money"), () => { if (gate === "open" && state) paint(); });
  $("tf-m5")?.addEventListener("click", () => {
    resetChartZoom($("candles"), "recent");
    if (gate === "open" && state) paint();
  });
  $("tf-all")?.addEventListener("click", () => {
    resetChartZoom($("candles"), "all");
    if (gate === "open" && state) paint();
  });
  $("tf-zin")?.addEventListener("click", () => {
    zoomChart($("candles"), 0.7);
    if (lastCandleDraw) drawCandles($("candles"), lastCandleDraw.bars, lastCandleDraw.opts);
    $("tf-m5")?.classList.toggle("on", true);
    $("tf-all")?.classList.toggle("on", false);
  });
  $("tf-zout")?.addEventListener("click", () => {
    zoomChart($("candles"), 1.4);
    if (lastCandleDraw) drawCandles($("candles"), lastCandleDraw.bars, lastCandleDraw.opts);
  });
  $("tf-live")?.addEventListener("click", () => {
    jumpToLive($("candles"));
    if (lastCandleDraw) drawCandles($("candles"), lastCandleDraw.bars, lastCandleDraw.opts);
    $("tf-m5")?.classList.toggle("on", true);
    $("tf-all")?.classList.toggle("on", false);
  });
  function syncIndButtons() {
    $("tf-line")?.classList.toggle("on", chartPrefs.chartType === "line");
    $("tf-ema")?.classList.toggle("on", chartPrefs.showEma);
    $("tf-macd")?.classList.toggle("on", chartPrefs.showMacd);
    $("tf-rsi")?.classList.toggle("on", chartPrefs.showRsi);
    $("tf-bb")?.classList.toggle("on", chartPrefs.showBb);
  }
  function togglePref(key) {
    if (key === "chartType") chartPrefs.chartType = chartPrefs.chartType === "line" ? "candles" : "line";
    else chartPrefs[key] = !chartPrefs[key];
    syncIndButtons();
    if (lastCandleDraw) {
      lastCandleDraw.opts = { ...lastCandleDraw.opts, ...chartPrefs };
      drawCandles($("candles"), lastCandleDraw.bars, lastCandleDraw.opts);
    }
  }
  $("tf-line")?.addEventListener("click", () => togglePref("chartType"));
  $("tf-ema")?.addEventListener("click", () => togglePref("showEma"));
  $("tf-macd")?.addEventListener("click", () => togglePref("showMacd"));
  $("tf-rsi")?.addEventListener("click", () => togglePref("showRsi"));
  $("tf-bb")?.addEventListener("click", () => togglePref("showBb"));
  syncIndButtons();

  tickClocks();
  setInterval(tickClocks, 1000);

  let polling = false;
  function startPolling() {
    if (polling) return;
    polling = true;
    setInterval(loadState, 1000);
    setInterval(loadHealth, 5000);
  }

  (async () => {
    try {
      const res = await fetch("/api/unlock", { credentials: "include" });
      const next = await res.json();
      pinRequired = Boolean(next.required);
      if (next.method) authMethod = next.method;
      showQr = Boolean(next.show_qr);
      gate = next.required && !next.unlocked ? "locked" : "open";
    } catch {
      gate = "open";
    }
    paintGate();
    if (gate === "open") {
      await loadState();
      await loadHealth();
      startPolling();
    }
  })();
})();
