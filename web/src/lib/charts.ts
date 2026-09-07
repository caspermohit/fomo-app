import type { BalancePoint } from "@/lib/format";
import type { Bar, ClosedTrade, Position } from "@/lib/types";

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

  const padL = 96;
  const padR = 124;
  const padT = 22;
  const padB = 36;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  if (points.length < 2) {
    ctx.fillStyle = "#6f6d67";
    ctx.font = "22px IBM Plex Mono";
    ctx.fillText("no closed trades yet — curve starts at deposit", padL, h / 2);
    ctx.strokeStyle = "#eceae4";
    ctx.beginPath();
    ctx.moveTo(padL, padT + plotH / 2);
    ctx.lineTo(w - padR, padT + plotH / 2);
    ctx.stroke();
    return;
  }

  const vals = points.map((p) => p.balance);
  vals.push(start);
  const rawMin = Math.min(...vals);
  const rawMax = Math.max(...vals);
  const pad = Math.max((rawMax - rawMin) * 0.12, 8);
  const min = rawMin - pad;
  const max = rawMax + pad;
  const span = max - min || 1;
  const maxN = Math.max(...points.map((p) => p.n), 1);
  const xAt = (n: number) => padL + (n / maxN) * plotW;
  const yAt = (v: number) => padT + ((max - v) / span) * plotH;
  const last = points[points.length - 1];
  const up = last.balance >= start - 0.004;
  const line = up ? "#16a34a" : "#e11d48";
  const startY = yAt(start);

  ctx.strokeStyle = "#eceae4";
  ctx.lineWidth = 1;
  const ticks = [max, start, min];
  const seenY = new Set<number>();
  for (const tick of ticks) {
    const gy = Math.round(yAt(tick));
    if (seenY.has(gy)) continue;
    seenY.add(gy);
    ctx.beginPath();
    ctx.moveTo(padL, gy);
    ctx.lineTo(w - padR, gy);
    ctx.stroke();
    ctx.fillStyle = "#6f6d67";
    ctx.font = "20px IBM Plex Mono";
    ctx.fillText(fmtAxisCash(tick), 10, gy + 6);
  }

  ctx.setLineDash([6, 5]);
  ctx.strokeStyle = "#a8a29e";
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
  realized.forEach((p, i) => {
    if (i === 0) ctx.moveTo(xAt(p.n), yAt(p.balance));
    else ctx.lineTo(xAt(p.n), yAt(p.balance));
  });
  ctx.stroke();

  if (live && lastReal) {
    ctx.setLineDash([7, 5]);
    ctx.strokeStyle = line;
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(xAt(lastReal.n), yAt(lastReal.balance));
    ctx.lineTo(xAt(live.n), yAt(live.balance));
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#fff";
    ctx.strokeStyle = line;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(xAt(live.n), yAt(live.balance), 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  if (realized.length <= 90) {
    ctx.fillStyle = line;
    realized.forEach((p, i) => {
      if (i === 0) return;
      ctx.beginPath();
      ctx.arc(xAt(p.n), yAt(p.balance), 3.4, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  let lastLabelY = yAt(last.balance) + 5;
  let startLabelY = startY + 5;
  if (Math.abs(lastLabelY - startLabelY) < 18) {
    lastLabelY = startLabelY + 20;
  }
  ctx.fillStyle = "#a8a29e";
  ctx.font = "18px IBM Plex Mono";
  ctx.fillText("start " + fmtAxisCash(start), w - padR + 8, startLabelY);
  ctx.fillStyle = line;
  ctx.font = "20px IBM Plex Mono";
  ctx.fillText(fmtAxisCash(last.balance), w - padR + 8, lastLabelY);

  ctx.fillStyle = "#6f6d67";
  ctx.font = "18px IBM Plex Mono";
  const trades = Math.max(realized.length - 1, 0);
  ctx.fillText("0", padL, h - 12);
  if (trades > 1) ctx.fillText(String(Math.round(trades / 2)), xAt(trades / 2) - 8, h - 12);
  ctx.fillText(live ? `${trades} + live` : `${trades} trades`, w - padR - 88, h - 12);
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

function barIndex(bars: Bar[], time?: string): number {
  if (!time) return bars.length - 1;
  const match = String(time).match(/(\d{2}:\d{2})/);
  const needle = match ? match[1] : time.length >= 5 ? time.slice(0, 5) : time;
  for (let i = bars.length - 1; i >= 0; i--) {
    if (String(bars[i].t).includes(needle)) return i;
  }
  return bars.length - 1;
}

function triangle(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  up: boolean,
  color: string,
  label: string,
) {
  const s = 11;
  ctx.fillStyle = color;
  ctx.beginPath();
  if (up) {
    ctx.moveTo(x, y - 2);
    ctx.lineTo(x - s, y + s);
    ctx.lineTo(x + s, y + s);
  } else {
    ctx.moveTo(x, y + 2);
    ctx.lineTo(x - s, y - s);
    ctx.lineTo(x + s, y - s);
  }
  ctx.closePath();
  ctx.fill();
  ctx.font = "11px IBM Plex Mono";
  ctx.fillText(label, x + 8, y + 4);
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
  },
) {
  const sized = size(canvas);
  if (!sized) return;
  const [w, h, ctx] = sized;
  ctx.clearRect(0, 0, w, h);
  const series = liveBars(bars, opts?.last, opts?.bid, opts?.ask);
  if (series.length < 2) {
    ctx.fillStyle = "#6f6d67";
    ctx.font = "16px IBM Plex Mono";
    ctx.fillText("waiting for M5 candles…", 16, h / 2);
    return;
  }

  const padL = 8;
  const padR = 78;
  const padT = 18;
  const padB = 28;
  const prices = series.flatMap((b) => [b.h, b.l]);
  for (const p of opts?.open || []) prices.push(Number(p.price));
  if (opts?.last) prices.push(opts.last);
  if (opts?.bid) prices.push(opts.bid);
  if (opts?.ask) prices.push(opts.ask);
  const max = Math.max(...prices);
  const min = Math.min(...prices);
  const span = max - min || 0.0001;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const cw = plotW / series.length;
  const y = (v: number) => padT + ((max - v) / span) * plotH;
  const xAt = (i: number) => padL + i * cw + cw * 0.5;

  ctx.strokeStyle = "#eceae4";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) {
    const gy = padT + (plotH * i) / 3;
    ctx.beginPath();
    ctx.moveTo(padL, gy);
    ctx.lineTo(w - padR, gy);
    ctx.stroke();
  }

  series.forEach((b, i) => {
    const x = xAt(i);
    const up = b.c >= b.o;
    ctx.strokeStyle = ctx.fillStyle = up ? "#16a34a" : "#e11d48";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(x, y(b.h));
    ctx.lineTo(x, y(b.l));
    ctx.stroke();
    const body = Math.max(Math.abs(y(b.c) - y(b.o)), 3);
    ctx.fillRect(x - Math.max(cw * 0.32, 2), Math.min(y(b.o), y(b.c)), Math.max(cw * 0.64, 4), body);
  });

  const last = opts?.last ?? series[series.length - 1].c;
  ctx.setLineDash([6, 5]);
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.moveTo(padL, y(last));
  ctx.lineTo(w - padR, y(last));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#2563eb";
  ctx.font = "12px IBM Plex Mono";
  ctx.fillText(Number(last).toFixed(5), w - padR + 6, y(last) + 4);

  (opts?.closed || []).slice(0, 24).forEach((t) => {
    const i = barIndex(series, t.time);
    const px = Number(t.price);
    if (!px) return;
    const win = Number(t.profit) >= 0;
    ctx.fillStyle = win ? "#16a34a" : "#e11d48";
    ctx.beginPath();
    ctx.arc(xAt(i), y(px), 5, 0, Math.PI * 2);
    ctx.fill();
  });

  (opts?.open || []).forEach((p) => {
    const px = Number(p.price);
    const buy = p.side === "BUY";
    const color = buy ? "#16a34a" : "#e11d48";
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(padL, y(px));
    ctx.lineTo(w - padR, y(px));
    ctx.stroke();
    ctx.setLineDash([]);
    const label = `${p.side} ${Number(p.profit) >= 0 ? "+" : ""}${Number(p.profit).toFixed(2)}`;
    triangle(ctx, xAt(series.length - 1), y(px), buy, color, label);
  });

  ctx.fillStyle = "#6f6d67";
  ctx.font = "11px IBM Plex Mono";
  ctx.fillText("▲ BUY  ▼ SELL  ● closed  — last", padL, h - 8);
}
