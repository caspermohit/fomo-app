import type { BotState } from "@/lib/types";

export type SideWhy = {
  side: "BUY" | "SELL";
  go: boolean;
  title: string;
  lines: string[];
};

export type Gate = { ok: boolean; label: string; value: string };

export type DeskWhy = {
  now: string;
  h1: string;
  h4: string;
  htf: string;
  buy: SideWhy;
  sell: SideWhy;
  gates: Gate[];
};

function has(blockers: string[], needle: string): string | undefined {
  const n = needle.toLowerCase();
  return blockers.find((b) => b.toLowerCase().includes(n));
}

export function deskWhy(state: BotState | null, focus?: string): DeskWhy {
  const symbol = String(focus || state?.focus || state?.symbol || "EURUSD").toUpperCase();
  const book = state?.books?.[symbol];
  const a = book?.analysis || state?.analysis || {};
  const checks = a.checks || {};
  const h1 = String(book?.h1 || state?.h1 || checks.h1 || "—").toUpperCase();
  const h4 = String(book?.h4 || state?.h4 || checks.h4 || "—").toUpperCase();
  const htf = String(book?.htf || state?.htf || checks.htf || "HOLD").toUpperCase();
  const ema200 = String(checks.ema200 || "—").toLowerCase();
  const cross = String(checks.macd_cross || "HOLD").toUpperCase();
  const zero = String(checks.zero || "—").toLowerCase();
  const blockers = book?.blockers || state?.blockers || [];
  const open = (state?.positions || []).filter((p) => !p.symbol || p.symbol === symbol);
  const allOpen = state?.positions || [];
  const openSide = open[0]?.side?.toUpperCase();
  const maxPos = state?.max_positions ?? 1;
  const startH = state?.session_start_hour ?? 7;
  const endH = state?.session_end_hour ?? 20;
  const spread = book?.spread ?? state?.spread;
  const maxSpread = book?.max_spread_pips ?? (symbol.endsWith("JPY") ? state?.max_spread_pips_jpy ?? 4 : state?.max_spread_pips ?? 2);
  const session = String(checks.session || "");
  const running = Boolean(state?.running);
  const cooldown = has(blockers, "cooldown");
  const pace = has(blockers, "pace");
  const weekend = has(blockers, "weekend");
  const mondayOpen = has(blockers, "monday open");
  const correlated = has(blockers, "correlated");
  const newsHit = book?.news || (state?.news?.active || []).find((e) => String(e.symbol || "").toUpperCase() === symbol);
  const newsBlock = Boolean(state?.news_blackout !== false && newsHit);
  const sessionOut = session.includes("outside") || Boolean(has(blockers, "outside the configured session"));
    const atrNow = a.atr_pips != null ? Number(a.atr_pips) : null;
  const atrFloor = Number(book?.min_atr_pips ?? state?.min_atr_pips ?? 4);
  const atrGate = state?.atr_gate !== false;
  const atrOk = !atrGate || atrNow == null || Number.isNaN(atrNow) || atrNow + 1e-9 >= atrFloor;
    const spreadOk = spread == null || Number(spread) <= maxSpread;
  const room = open.length < maxPos;
  const deskBusy = allOpen.some((p) => p.symbol && p.symbol !== symbol);

  function forSide(side: "BUY" | "SELL"): SideWhy {
    const lines: string[] = [];
    const trendOk = (side === "BUY" && ema200 === "above") || (side === "SELL" && ema200 === "below");
    const crossOk = cross === side;
    const zeroOk = (side === "BUY" && zero === "below") || (side === "SELL" && zero === "above");
    if (trendOk) {
      lines.push(`M5 close is ${ema200} EMA200 — Trading Rush only ${side === "BUY" ? "buys" : "sells"} this way.`);
    } else {
      lines.push(`M5 close is ${ema200} EMA200. ${side} needs close ${side === "BUY" ? "above" : "below"} EMA200.`);
    }
    if (crossOk) {
      lines.push(`Fresh MACD 12/26/9 ${side} cross on the closed bar.`);
    } else {
      lines.push(`No fresh MACD ${side} cross (last cross=${cross}). Entry is the cross-bar close, not a 60s clock.`);
    }
    if (crossOk && !zeroOk) {
      lines.push(side === "BUY" ? "BUY cross must be below the zero line." : "SELL cross must be above the zero line.");
    }
    if (openSide && openSide !== side) {
      lines.push(`Already ${openSide} × ${open.length} — mixing BUY and SELL is blocked.`);
    } else if (openSide === side && open.length) {
      lines.push(`Already ${side} × ${open.length}. Max 1 ticket per pair — no averaging losers.`);
    }
    if (deskBusy) lines.push("Another pair is already open — skip correlated Bill 24h bug.");
    if (!room) lines.push(`Book is full (${open.length}/${maxPos}).`);
    if (cooldown) lines.push(cooldown);
    if (pace) lines.push(pace);
    if (weekend) lines.push(weekend);
    if (mondayOpen) lines.push(mondayOpen);
    if (correlated) lines.push(correlated);
    if (newsBlock) lines.push(newsHit?.reason || `${symbol} news blackout ${newsHit?.name || ""}`.trim());
    if (!atrOk && atrNow != null) lines.push(`ATR ${atrNow.toFixed(1)} below floor ${atrFloor} — skip dead tape.`);
    if (sessionOut) lines.push(`Outside London hours (${startH}–${endH} UTC).`);
    if (!spreadOk) lines.push(`Spread ${spread} pip — cap is ${maxSpread}.`);
    if (!running) lines.push("Bot is stopped — press START.");
    const k = book?.kronos;
    const kronosOk = !k?.enabled || (side === "BUY" ? k.allow_buy !== false : k.allow_sell !== false);
    if (k?.enabled) {
      const pred = String(k.predicted || "n/a").toUpperCase();
      const bps = k.bps != null ? `${Number(k.bps) >= 0 ? "+" : ""}${Number(k.bps).toFixed(1)} bps` : "no slope";
      if (pred === "CHOP") lines.push(`Kronos CHOP (no opinion, ${bps}) — MACD ${side} may still fire.`);
      else if (kronosOk) lines.push(`Kronos ${pred} (${bps}) agrees — allow ${side}.`);
      else lines.push(`Kronos ${pred} (${bps}) — skip ${side}.`);
    }
    const mixOk = !openSide || openSide === side;
    const go =
      trendOk &&
      crossOk &&
      zeroOk &&
      mixOk &&
      room &&
      running &&
      spreadOk &&
      !deskBusy &&
      !cooldown &&
      !pace &&
      !weekend &&
      !mondayOpen &&
      !newsBlock &&
      atrOk &&
      !sessionOut &&
      kronosOk;
    return {
      side,
      go,
      title: go ? `YES — MACD ${side} on this closed bar` : `NOT ${side === "BUY" ? "BUYING" : "SELLING"}`,
      lines,
    };
  }

  const buy = forSide("BUY");
  const sell = forSide("SELL");
  let now = `${symbol}: NOT BUYING · NOT SELLING`;
  if (buy.go) now = `${symbol}: BUYING on this MACD cross`;
  else if (sell.go) now = `${symbol}: SELLING on this MACD cross`;
  else if (openSide === "BUY") now = `${symbol}: HOLDING ${open.length} BUY · not adding`;
  else if (openSide === "SELL") now = `${symbol}: HOLDING ${open.length} SELL · not adding`;

  const nextNews = state?.news?.next;
  const newsValue = newsBlock
    ? String(newsHit?.name || "print")
    : nextNews
      ? `next ${nextNews.name}`
      : "clear";
  const gates: Gate[] = [
    { ok: ema200 === "above" || ema200 === "below", label: "M5 vs EMA200", value: ema200 },
    { ok: cross === "BUY" || cross === "SELL", label: "MACD 12/26/9 cross", value: cross },
    { ok: zero === "below" || zero === "above", label: "MACD vs zero", value: zero },
    {
      ok: !book?.kronos?.enabled || Boolean(book.kronos.allow_buy || book.kronos.allow_sell),
      label: "Kronos",
      value: book?.kronos?.enabled
        ? `${String(book.kronos.predicted || "n/a").toUpperCase()} ${book.kronos.bps != null ? `${Number(book.kronos.bps).toFixed(1)}bps` : "—"}`
        : "off",
    },
    { ok: !sessionOut, label: "London hours", value: sessionOut ? "closed" : `${startH}–${endH} UTC` },
    { ok: spreadOk, label: "Spread", value: spread != null ? `${spread} / ${maxSpread} pip` : "—" },
    { ok: atrOk, label: atrGate ? "ATR" : "ATR (off)", value: atrNow != null ? `${atrNow.toFixed(1)} / ${atrFloor} pip` : "—" },
    { ok: !newsBlock, label: "News", value: state?.news_blackout === false ? "off" : newsValue },
    { ok: !mondayOpen, label: "Monday open", value: mondayOpen ? mondayOpen : "ok" },
    { ok: !cooldown, label: "Stop cooldown", value: cooldown ? cooldown : "clear" },
    { ok: !pace, label: "Fresh cross only", value: pace ? pace : "ready" },
    { ok: true, label: "Same-side only", value: openSide ? `holding ${openSide}` : "flat" },
    { ok: room, label: "Ticket room", value: `${open.length}/${maxPos}` },
    { ok: running, label: "Bot", value: running ? "running" : "stopped" },
    { ok: !weekend, label: "Weekend", value: weekend ? "blocked" : "ok" },
  ];

  return { now, h1, h4, htf, buy, sell, gates };
}

/** Format MACD-era win rate for the open-tickets table. Zero fills → "—". */
export function formatMacdWr(
  winRate: number | null | undefined,
  wins: number | null | undefined,
  fills: number | null | undefined,
  label = "This system",
): string {
  const n = Number(fills || 0);
  if (!n || winRate == null || Number.isNaN(Number(winRate))) return "—";
  const w = Number(wins || 0);
  return `${label} ${Number(winRate).toFixed(0)}% (${w}/${n})`;
}
