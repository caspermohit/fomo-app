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

export function sessionPnl(state: { pnl?: { today?: number; total?: number } } | null): number {
  if (!state?.pnl) return 0;
  if (state.pnl.today != null) return Number(state.pnl.today);
  return Number(state.pnl.total ?? 0);
}
