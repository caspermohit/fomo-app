import type { BalancePoint } from "@/lib/format";
import type { Bar, ClosedTrade, KronosHud, Position } from "@/lib/types";

type TvState = {
  start: number;
  end: number;
  follow: boolean;
  inited: boolean;
  dragging: boolean;
  lastX: number;
};

type ZoomCanvas = HTMLCanvasElement & {
  _tv?: TvState;
  _tvBound?: boolean;
  _cross?: { x: number; y: number } | null;
  _fitAll?: boolean;
};

/** Tightest zoom, in bars. Low enough to inspect the forming candle up close. */
const MIN_SPAN = 8;
/** Extra axis units after the last bar at a normal zoom — shrinks when you zoom in. */
const LIVE_PAD = 8;
const PLOT_PAD_L = 16;
const PLOT_PAD_R = 108;

function size(canvas: HTMLCanvasElement): [number, number, CanvasRenderingContext2D] | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const w = canvas.offsetWidth * 2;
  const h = canvas.offsetHeight * 2;
  if (w < 4 || h < 4) return null;
  canvas.width = w;
  canvas.height = h;
  return [w, h, ctx];
}

function tvState(canvas: HTMLCanvasElement): TvState {
  const c = canvas as ZoomCanvas;
  if (!c._tv) {
    c._tv = { start: 0, end: 0, follow: true, inited: false, dragging: false, lastX: 0 };
  }
  return c._tv;
}

function livePadOf(canvas: HTMLCanvasElement, override?: number): number {
  if (override != null) return Math.max(0, override);
  return Math.max(0, Number(canvas.dataset.livePad || 0));
}

/**
 * Right-side gap after the last bar. Capped so a tight zoom still shows the
 * forming candle instead of an empty strip past it.
 */
function padForSpan(span: number, requested: number): number {
  const minBars = Math.min(5, Math.max(3, span * 0.55));
  const maxPad = Math.max(0.4, span - minBars);
  return Math.max(0.4, Math.min(requested, span * 0.16, maxPad));
}

function followWindow(n: number, span: number, requestedPad: number): [number, number] {
  const pad = padForSpan(span, requestedPad);
  const end = n + pad;
  let start = end - span;
  if (start > n - 1) start = Math.max(0, n - Math.min(span, n));
  if (start < 0) start = 0;
  return [start, end];
}

/** Visible [start, end) in axis units. Defaults to the latest `defaultSpan` like TradingView. */
export function visibleWindow(
  canvas: HTMLCanvasElement,
  n: number,
  defaultSpan = 80,
  livePad?: number,
): [number, number] {
  const tv = tvState(canvas);
  const cap = Math.max(n, 1);
  const requested = livePadOf(canvas, livePad);
  let span = tv.inited && tv.end > tv.start ? Math.max(MIN_SPAN, tv.end - tv.start) : Math.max(MIN_SPAN, defaultSpan);
  const spanWanted = (canvas as ZoomCanvas)._fitAll
    ? cap + padForSpan(cap, requested)
    : Math.max(MIN_SPAN, Math.min(defaultSpan + padForSpan(span, requested), cap + requested));
  if (!tv.inited || tv.end <= tv.start) {
    tv.inited = true;
    tv.follow = true;
    const initSpan = Math.min(spanWanted, cap + requested);
    const w = followWindow(cap, initSpan, requested);
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

export function resetChartZoom(canvas: HTMLCanvasElement, mode: "recent" | "all" = "recent"): void {
  const tv = tvState(canvas);
  tv.inited = false;
  tv.follow = true;
  (canvas as ZoomCanvas)._fitAll = mode === "all";
}

function clampSpan(next: number, n: number, pad: number): number {
  return Math.max(MIN_SPAN, Math.min(n + pad, next));
}

/** Zoom in (`factor` < 1) or out, pinning the live bar when already following it. */
export function zoomChart(canvas: HTMLCanvasElement, factor: number): void {
  const n = Number(canvas.dataset.chartN || 0);
  if (n < 2) return;
  const requested = livePadOf(canvas);
  const tv = tvState(canvas);
  const [a, b] = visibleWindow(canvas, n);
  const next = clampSpan((b - a) * factor, n, requested);
  (canvas as ZoomCanvas)._fitAll = false;
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

/** Jump to the forming candle at a readable zoom. */
export function jumpToLive(canvas: HTMLCanvasElement): void {
  const n = Number(canvas.dataset.chartN || 0);
  const requested = livePadOf(canvas);
  const tv = tvState(canvas);
  tv.follow = true;
  tv.inited = true;
  (canvas as ZoomCanvas)._fitAll = false;
  const span = Math.min(36, Math.max(MIN_SPAN, n * 0.16 || MIN_SPAN));
  const w = followWindow(Math.max(n, 1), span, requested);
  tv.start = w[0];
  tv.end = w[1];
}

export function bindChartZoom(canvas: HTMLCanvasElement, onChange: () => void, onHover?: () => void): void {
  const c = canvas as ZoomCanvas;
  if (c._tvBound) return;
  c._tvBound = true;
  canvas.style.touchAction = "none";
  canvas.style.cursor = "crosshair";

  canvas.addEventListener(
    "wheel",
    (e) => {
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
      (canvas as ZoomCanvas)._fitAll = false;
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
    },
    { passive: false },
  );

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
    const z = canvas as ZoomCanvas;
    z._cross = {
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
    (canvas as ZoomCanvas)._cross = null;
    hover();
  });
  const endDrag = (e: PointerEvent) => {
    tvState(canvas).dragging = false;
    canvas.style.cursor = "crosshair";
    try {
      canvas.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("dblclick", () => {
    resetChartZoom(canvas, "recent");
    onChange();
  });
}

export function drawSpark(canvas: HTMLCanvasElement, data: number[], color: string) {
  const sized = size(canvas);
  if (!sized) return;
  const [w, h, ctx] = sized;
  ctx.clearRect(0, 0, w, h);
  if (data.length < 2) return;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  data.forEach((v, i) => {
    const px = 2 + (i / (data.length - 1)) * (w - 4);
    const py = 2 + (h - 4) - ((v - min) / span) * (h - 4);
    if (i) ctx.lineTo(px, py);
    else ctx.moveTo(px, py);
  });
  ctx.stroke();
}

function fmtAxisCash(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1000) {
    return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  return "$" + n.toFixed(2);
}

/** Bill Excel money graph: trade number on X, running demo balance on Y. */
export function drawMoneyGraph(canvas: HTMLCanvasElement, points: BalancePoint[], start: number) {
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
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.beginPath();
    ctx.moveTo(padL, padT + plotH / 2);
    ctx.lineTo(w - padR, padT + plotH / 2);
    ctx.stroke();
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
  const xAt = (n: number) => padL + ((n - x0) / spanX) * plotW;
  const yAt = (v: number) => padT + ((max - v) / span) * plotH;
  const last = points[points.length - 1];
  const up = last.balance >= start - 0.004;
  const line = up ? "#19e68c" : "#e06a5c";
  const startY = yAt(start);

  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 1;
  const ticks = [start, max, min];
  const seenY: number[] = [];
  for (const tick of ticks) {
    const gy = Math.round(yAt(tick));
    if (seenY.some((prev) => Math.abs(prev - gy) < axisFont * 1.9)) continue;
    seenY.push(gy);
    ctx.beginPath();
    ctx.moveTo(padL, gy);
    ctx.lineTo(w - padR, gy);
    ctx.stroke();
    ctx.fillStyle = "#8b95a3";
    ctx.font = `${axisFont}px IBM Plex Mono`;
    ctx.fillText(fmtAxisCash(tick), 6, gy + Math.round(axisFont * 0.35));
  }

  ctx.setLineDash([6, 5]);
  ctx.strokeStyle = "#3a4452";
  ctx.lineWidth = 1.4;
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
    ctx.strokeStyle = line;
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(xAt(lastReal.n), yAt(lastReal.balance));
    ctx.lineTo(xAt(live.n), yAt(live.balance));
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#0b0e13";
    ctx.strokeStyle = line;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(xAt(live.n), yAt(live.balance), compact ? 3 : 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  const visReal = realized.filter((p) => p.n >= x0 && p.n <= x1);
  if (visReal.length <= 90) {
    ctx.fillStyle = line;
    visReal.forEach((p, i) => {
      if (i === 0 && p.n === 0) return;
      ctx.beginPath();
      ctx.arc(xAt(p.n), yAt(p.balance), 3.4, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  // The start value is already a left-axis tick, so the right gutter only carries the live balance.
  ctx.fillStyle = line;
  ctx.font = `${axisFont}px IBM Plex Mono`;
  ctx.fillText(fmtAxisCash(last.balance), w - padR + 5, yAt(last.balance) + Math.round(axisFont * 0.35));

  ctx.fillStyle = "#8b95a3";
  ctx.font = `${axisFont}px IBM Plex Mono`;
  ctx.fillText(String(Math.round(x0)), padL, h - 5);
  ctx.textAlign = "right";
  ctx.fillText(live && x1 >= maxN - 0.2 ? `${Math.round(x1)} + live` : `${Math.round(x1)} trades`, w - padR, h - 5);
  ctx.textAlign = "left";
}

function liveBars(bars: Bar[], last?: number | null, bid?: number | null, ask?: number | null): Bar[] {
  if (!bars.length) return bars;
  const out = bars.map((b) => ({ ...b }));
  const px = last ?? out[out.length - 1].c;
  const lastBar = out[out.length - 1];
  lastBar.c = px;
  lastBar.h = Math.max(lastBar.h, px, ask ?? px);
  lastBar.l = Math.min(lastBar.l, px, bid ?? px);
  return out;
}

type MacdSeries = { macd: number[]; signal: number[]; hist: number[] };

function emaSeries(values: number[], period: number): number[] {
  const out: number[] = [];
  const k = 2 / (period + 1);
  let prev = values[0] ?? 0;
  values.forEach((v, i) => {
    prev = i === 0 ? v : v * k + prev * (1 - k);
    out.push(prev);
  });
  return out;
}

/** MACD 12/26/9 on closes — the same settings the entry logic trades off. */
function macdSeries(closes: number[], fast = 12, slow = 26, signal = 9): MacdSeries {
  const fastE = emaSeries(closes, fast);
  const slowE = emaSeries(closes, slow);
  const macd = closes.map((_, i) => fastE[i] - slowE[i]);
  const sig = emaSeries(macd, signal);
  return { macd, signal: sig, hist: macd.map((m, i) => m - sig[i]) };
}

/** Same cross rule as bot/analyst.macd_cross: last two MACD vs signal values. */
function macdCrossFlags(closes: number[]): Array<"BUY" | "SELL" | "BUY_HI" | "SELL_LO" | null> {
  const { macd, signal } = macdSeries(closes);
  const flags: Array<"BUY" | "SELL" | "BUY_HI" | "SELL_LO" | null> = closes.map(() => null);
  for (let i = 1; i < closes.length; i++) {
    const prevM = macd[i - 1];
    const lastM = macd[i];
    const prevS = signal[i - 1];
    const lastS = signal[i];
    if (prevM <= prevS && lastM > lastS) flags[i] = lastM < 0 ? "BUY" : "BUY_HI";
    else if (prevM >= prevS && lastM < lastS) flags[i] = lastM > 0 ? "SELL" : "SELL_LO";
  }
  return flags;
}

function drawMacdCrosses(
  ctx: CanvasRenderingContext2D,
  view: Bar[],
  lo: number,
  flags: Array<"BUY" | "SELL" | "BUY_HI" | "SELL_LO" | null>,
  xAt: (i: number) => number,
  y: (v: number) => number,
  upCol: string,
  downCol: string,
) {
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

function drawKronosHud(
  ctx: CanvasRenderingContext2D,
  kronos: KronosHud | undefined,
  left: number,
  top: number,
  axis: string,
  upCol: string,
  downCol: string,
) {
  if (!kronos) return;
  const pred = String(kronos.predicted || "n/a").toUpperCase();
  const bps = kronos.bps == null || Number.isNaN(Number(kronos.bps)) ? "—" : `${Number(kronos.bps) >= 0 ? "+" : ""}${Number(kronos.bps).toFixed(1)}bps`;
  const age =
    kronos.age_sec == null
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

function rsiSeries(closes: number[], period = 14): Array<number | null> {
  const out: Array<number | null> = closes.map(() => null);
  if (closes.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d >= 0) gain += d;
    else loss -= d;
  }
  let avgG = gain / period;
  let avgL = loss / period;
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period;
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period;
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  }
  return out;
}

function bbSeries(closes: number[], period = 20, k = 2) {
  const mid: Array<number | null> = [];
  const up: Array<number | null> = [];
  const lo: Array<number | null> = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      mid.push(null);
      up.push(null);
      lo.push(null);
      continue;
    }
    let s = 0;
    let s2 = 0;
    for (let j = i - period + 1; j <= i; j++) {
      s += closes[j];
      s2 += closes[j] * closes[j];
    }
    const m = s / period;
    const sd = Math.sqrt(Math.max(s2 / period - m * m, 0));
    mid.push(m);
    up.push(m + k * sd);
    lo.push(m - k * sd);
  }
  return { mid, up, lo };
}

function strokeSeries(
  ctx: CanvasRenderingContext2D,
  vals: Array<number | null>,
  xAt: (i: number) => number,
  yAt: (v: number) => number,
  lo: number,
  color: string,
  width: number,
) {
  ctx.beginPath();
  let started = false;
  vals.forEach((v, j) => {
    if (v == null || Number.isNaN(v)) {
      started = false;
      return;
    }
    const x = xAt(lo + j);
    const y = yAt(v);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.stroke();
}

function barIndex(bars: Bar[], time?: string): number {
  if (!time) return bars.length - 1;
  const match = String(time).match(/(\d{2}:\d{2})/);
  const needle = match ? match[1] : time.length >= 5 ? time.slice(0, 5) : time;
  for (let i = bars.length - 1; i >= 0; i--) {
    if (String(bars[i].t).includes(needle)) return i;
  }
  return bars.length - 1;
}

function chartPxDigits(symbol?: string | null): number {
  const s = String(symbol || "").toUpperCase();
  if (s.includes("XAU") || s.includes("GOLD")) return 2;
  if (s.endsWith("JPY")) return 3;
  return 5;
}

function fmtChartPx(symbol: string | null | undefined, n: number): string {
  return Number(n).toFixed(chartPxDigits(symbol));
}

function barTimeLabel(t: string): string {
  const s = String(t || "");
  const m = s.match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (m) {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[4]}:${m[5]}`;
  }
  const hm = s.match(/(\d{2}:\d{2})/);
  return hm ? hm[1] : s.slice(0, 16);
}

function priceTag(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  text: string,
  bg: string,
  plotTop: number,
  plotBot: number,
) {
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

function drawMacdPanel(
  ctx: CanvasRenderingContext2D,
  o: {
    series: Bar[];
    lo: number;
    view: Bar[];
    xAt: (i: number) => number;
    left: number;
    right: number;
    top: number;
    height: number;
    up: string;
    down: string;
    axis: string;
  },
) {
  const { series, lo, view, xAt, left, right, top, height, up, down, axis } = o;
  // Computed over the whole series so the EMAs are warm, then sliced to the view.
  const { macd, signal, hist } = macdSeries(series.map((b) => b.c));
  const slice = <T,>(arr: T[]) => arr.slice(lo, lo + view.length);
  const vMacd = slice(macd);
  const vSig = slice(signal);
  const vHist = slice(hist);

  const peak = Math.max(
    ...vMacd.map(Math.abs),
    ...vSig.map(Math.abs),
    ...vHist.map(Math.abs),
    Number.EPSILON,
  );
  const mid = top + height / 2;
  const my = (v: number) => mid - (v / peak) * (height / 2) * 0.88;

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
    // Fading colour when the histogram is shrinking marks momentum rolling over.
    const shrinking = j > 0 && Math.abs(v) < Math.abs(vHist[j - 1]);
    ctx.fillStyle = v >= 0
      ? shrinking ? "rgba(34,197,94,0.45)" : up
      : shrinking ? "rgba(249,115,22,0.45)" : down;
    const yv = my(v);
    ctx.fillRect(x - barW / 2, Math.min(yv, mid), barW, Math.max(Math.abs(yv - mid), 1));
  });

  const stroke = (vals: number[], color: string, width: number) => {
    ctx.beginPath();
    vals.forEach((v, j) => {
      const x = xAt(lo + j);
      if (j === 0) ctx.moveTo(x, my(v));
      else ctx.lineTo(x, my(v));
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.stroke();
  };
  stroke(vMacd, "#60a5fa", 2.2);
  stroke(vSig, "#f5c451", 2.0);
  ctx.restore();

  const lastM = vMacd[vMacd.length - 1] ?? 0;
  const lastS = vSig[vSig.length - 1] ?? 0;
  ctx.font = "15px IBM Plex Mono";
  ctx.textAlign = "left";
  ctx.fillStyle = axis;
  ctx.fillText("MACD 12/26/9", left + 6, top + 16);
  ctx.fillStyle = "#60a5fa";
  ctx.fillText(lastM.toFixed(5), left + 150, top + 16);
  ctx.fillStyle = "#f5c451";
  ctx.fillText(lastS.toFixed(5), left + 250, top + 16);
  // Above/below zero decides whether a cross is even tradable in the entry rules.
  ctx.fillStyle = lastM >= 0 ? up : down;
  ctx.fillText(lastM >= 0 ? "above zero" : "below zero", left + 350, top + 16);
}

function drawRsiPanel(
  ctx: CanvasRenderingContext2D,
  o: {
    series: Bar[];
    lo: number;
    view: Bar[];
    xAt: (i: number) => number;
    left: number;
    right: number;
    top: number;
    height: number;
    up: string;
    down: string;
    axis: string;
  },
) {
  const { series, lo, view, xAt, left, right, top, height, up, down, axis } = o;
  const rsi = rsiSeries(series.map((b) => b.c));
  const vRsi = rsi.slice(lo, lo + view.length);
  const yR = (v: number) => top + height - ((v - 0) / 100) * height * 0.92 - height * 0.04;

  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, right - left, height);
  ctx.clip();
  ctx.strokeStyle = "rgba(255,255,255,0.14)";
  ctx.lineWidth = 1;
  for (const lvl of [30, 50, 70]) {
    ctx.beginPath();
    ctx.moveTo(left, yR(lvl));
    ctx.lineTo(right, yR(lvl));
    ctx.stroke();
  }
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

export function drawCandles(
  canvas: HTMLCanvasElement,
  bars: Bar[],
  opts?: {
    last?: number | null;
    bid?: number | null;
    ask?: number | null;
    open?: Position[];
    closed?: ClosedTrade[];
    symbol?: string;
    chartType?: "candles" | "line";
    showMacd?: boolean;
    showEma?: boolean;
    showRsi?: boolean;
    showBb?: boolean;
    kronos?: KronosHud;
  },
) {
  const sized = size(canvas);
  if (!sized) return;
  const [w, h, ctx] = sized;
  ctx.fillStyle = "#111317";
  ctx.fillRect(0, 0, w, h);
  const series = liveBars(bars, opts?.last, opts?.bid, opts?.ask);
  if (series.length < 2) {
    ctx.fillStyle = "#8b9099";
    ctx.font = "20px IBM Plex Mono";
    ctx.fillText("waiting for M5 candles…", 24, h / 2);
    return;
  }

  const padL = PLOT_PAD_L;
  const padR = PLOT_PAD_R;
  const padT = 16;
  const padB = 40;
  const [i0, i1] = visibleWindow(canvas, series.length, 80, LIVE_PAD);
  const lo = Math.max(0, Math.floor(i0));
  const hi = Math.min(series.length, Math.ceil(i1));
  const view = series.slice(lo, Math.max(lo + 2, hi));
  const closes = series.map((b) => b.c);
  const ema20 = opts?.showEma ? emaSeries(closes, 20) : [];
  const ema50 = opts?.showEma ? emaSeries(closes, 50) : [];
  const ema200 = opts?.showEma ? emaSeries(closes, 200) : [];
  const bb = opts?.showBb ? bbSeries(closes) : null;
  const prices = view.flatMap((b) => [b.h, b.l]);
  for (const p of opts?.open || []) prices.push(Number(p.price));
  if (opts?.last) prices.push(opts.last);
  if (opts?.bid) prices.push(opts.bid);
  if (opts?.ask) prices.push(opts.ask);
  const takeOverlay = (arr: Array<number | null> | number[]) => {
    for (let j = 0; j < view.length; j++) {
      const v = arr[lo + j];
      if (typeof v === "number" && Number.isFinite(v)) prices.push(v);
    }
  };
  if (opts?.showEma) {
    takeOverlay(ema20);
    takeOverlay(ema50);
    takeOverlay(ema200);
  }
  if (bb) {
    takeOverlay(bb.up);
    takeOverlay(bb.lo);
  }
  const rawMax = Math.max(...prices);
  const rawMin = Math.min(...prices);
  const pad = (rawMax - rawMin) * 0.06 || 0.0001;
  const max = rawMax + pad;
  const min = rawMin - pad;
  const span = max - min || 0.0001;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const macdOn = Boolean(opts?.showMacd);
  const rsiOn = Boolean(opts?.showRsi);
  const paneN = Number(macdOn) + Number(rsiOn);
  const macdGap = paneN ? 14 : 0;
  const paneShare = paneN === 2 ? 0.18 : paneN === 1 ? 0.26 : 0;
  const paneH = paneN ? Math.max(48, Math.round(plotH * paneShare)) : 0;
  const panesH = paneN * paneH + Math.max(0, paneN - 1) * 8 + (paneN ? macdGap : 0);
  const priceH = plotH - panesH;
  const cw = plotW / Math.max(i1 - i0, 1);
  const y = (v: number) => padT + ((max - v) / span) * priceH;
  const xAt = (i: number) => padL + (i - i0) * cw + cw * 0.5;
  const last = opts?.last ?? series[series.length - 1].c;
  const upCol = "#22c55e";
  const downCol = "#f97316";
  const axis = "#8b9099";
  const symbol = opts?.symbol;

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

  if (opts?.chartType === "line") {
    const rising = view[view.length - 1].c >= view[0].c;
    const lineCol = rising ? upCol : downCol;
    ctx.beginPath();
    view.forEach((b, j) => {
      const x = xAt(lo + j);
      if (j === 0) ctx.moveTo(x, y(b.c));
      else ctx.lineTo(x, y(b.c));
    });
    const fill = ctx.createLinearGradient(0, padT, 0, padT + priceH);
    fill.addColorStop(0, rising ? "rgba(34,197,94,0.22)" : "rgba(249,115,22,0.22)");
    fill.addColorStop(1, "rgba(17,19,23,0)");
    ctx.save();
    ctx.lineTo(xAt(lo + view.length - 1), padT + priceH);
    ctx.lineTo(xAt(lo), padT + priceH);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    view.forEach((b, j) => {
      const x = xAt(lo + j);
      if (j === 0) ctx.moveTo(x, y(b.c));
      else ctx.lineTo(x, y(b.c));
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
    const vMid = bb.mid.slice(lo, lo + view.length);
    const vUp = bb.up.slice(lo, lo + view.length);
    const vLo = bb.lo.slice(lo, lo + view.length);
    ctx.beginPath();
    let started = false;
    vUp.forEach((v, j) => {
      if (v == null) {
        started = false;
        return;
      }
      const x = xAt(lo + j);
      if (!started) {
        ctx.moveTo(x, y(v));
        started = true;
      } else ctx.lineTo(x, y(v));
    });
    for (let j = vLo.length - 1; j >= 0; j--) {
      const v = vLo[j];
      if (v == null) continue;
      ctx.lineTo(xAt(lo + j), y(v));
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(148,163,184,0.12)";
    ctx.fill();
    strokeSeries(ctx, vUp, xAt, y, lo, "rgba(148,163,184,0.7)", 1.2);
    strokeSeries(ctx, vLo, xAt, y, lo, "rgba(148,163,184,0.7)", 1.2);
    strokeSeries(ctx, vMid, xAt, y, lo, "rgba(226,232,240,0.55)", 1.2);
  }

  if (opts?.showEma) {
    strokeSeries(ctx, ema20.slice(lo, lo + view.length), xAt, y, lo, "#22d3ee", 1.6);
    strokeSeries(ctx, ema50.slice(lo, lo + view.length), xAt, y, lo, "#a78bfa", 1.6);
    strokeSeries(ctx, ema200.slice(lo, lo + view.length), xAt, y, lo, "#facc15", 2.0);
  }

  if (opts?.showMacd) {
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

  (opts?.closed || []).slice(0, 80).forEach((t) => {
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

  (opts?.open || []).forEach((p) => {
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
    ctx.fillText(
      `${p.side} ${Number(p.profit) >= 0 ? "+" : ""}${Number(p.profit).toFixed(2)}`,
      ox + 12,
      oy + 5,
    );
  });
  ctx.restore();

  priceTag(ctx, w - padR, y(last), fmtChartPx(symbol, last), downCol, padT, padT + priceH);

  ctx.font = "14px IBM Plex Mono";
  ctx.textAlign = "left";
  let lx = padL + 6;
  const ly = padT + 14;
  const legend = (label: string, color: string) => {
    ctx.fillStyle = color;
    ctx.fillRect(lx, ly - 8, 8, 8);
    ctx.fillStyle = axis;
    ctx.fillText(label, lx + 12, ly);
    lx += ctx.measureText(label).width + 24;
  };
  if (opts?.showEma) {
    legend("EMA20", "#22d3ee");
    legend("EMA50", "#a78bfa");
    legend("EMA200", "#facc15");
  }
  if (opts?.showBb) legend("BB 20", "#94a3b8");
  drawKronosHud(ctx, opts?.kronos, padL + 6, padT + 24, axis, upCol, downCol);

  let paneTop = padT + priceH + macdGap;
  if (macdOn) {
    drawMacdPanel(ctx, {
      series,
      lo,
      view,
      xAt,
      left: padL,
      right: w - padR,
      top: paneTop,
      height: paneH,
      up: upCol,
      down: downCol,
      axis,
    });
    paneTop += paneH + 8;
  }
  if (rsiOn) {
    drawRsiPanel(ctx, {
      series,
      lo,
      view,
      xAt,
      left: padL,
      right: w - padR,
      top: paneTop,
      height: paneH,
      up: upCol,
      down: downCol,
      axis,
    });
  }

  ctx.fillStyle = axis;
  ctx.font = "16px IBM Plex Mono";
  ctx.textAlign = "center";
  const labelN = Math.min(5, view.length);
  for (let k = 0; k < labelN; k++) {
    const j = Math.round((k * (view.length - 1)) / Math.max(labelN - 1, 1));
    const text = barTimeLabel(view[j].t);
    // Centered labels at either end would hang off the plot, so keep them inside the gutters.
    const half = ctx.measureText(text).width / 2;
    const cx = Math.min(Math.max(xAt(lo + j), padL + half), w - padR - half);
    ctx.fillText(text, cx, h - 12);
  }
  ctx.textAlign = "left";

  const cross = (canvas as ZoomCanvas)._cross;
  if (
    cross &&
    cross.x >= padL &&
    cross.x <= w - padR &&
    cross.y >= padT &&
    cross.y <= padT + priceH
  ) {
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
