export function money(n: number | null | undefined): string {
  const v = Number(n || 0);
  return (
    (v >= 0 ? "+" : "-") +
    "$" +
    Math.abs(v).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

export function tone(n: number | null | undefined): string {
  const v = Number(n || 0);
  if (v > 0.004) return "#16a34a";
  if (v < -0.004) return "#e11d48";
  return "#171717";
}

export function push(arr: number[], v: number | null | undefined, max = 48): void {
  if (v == null || Number.isNaN(Number(v))) return;
  arr.push(Number(v));
  if (arr.length > max) arr.shift();
}

export function pxDigits(symbol: string | null | undefined): number {
  return String(symbol || "").toUpperCase().endsWith("JPY") ? 3 : 5;
}

export function fmtPx(symbol: string | null | undefined, n: number | null | undefined): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toFixed(pxDigits(symbol));
}

export function sessionPnl(state: { pnl?: { today?: number; total?: number } } | null): number {
  if (!state?.pnl) return 0;
  if (state.pnl.today != null) return Number(state.pnl.today);
  return Number(state.pnl.total ?? 0);
}

export type BalancePoint = { n: number; balance: number; live?: boolean };

/** Bill Excel curve: start deposit, then running balance after each closed ticket (oldest → newest). */
export function moneyBalanceSeries(
  trades: { time?: string; ticket?: string; profit?: number }[],
  start: number,
  floating = 0,
): BalancePoint[] {
  const ordered = [...trades].sort((a, b) => {
    const ta = String(a.time || "");
    const tb = String(b.time || "");
    if (ta !== tb) return ta.localeCompare(tb);
    return String(a.ticket || "").localeCompare(String(b.ticket || ""));
  });
  const out: BalancePoint[] = [{ n: 0, balance: start }];
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

export function cash(n: number | null | undefined): string {
  return "$" + Number(n || 0).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}
