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
