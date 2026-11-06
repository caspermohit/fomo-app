# FOMO current state — improve from here

Snapshot of the repo as of 10 Sep 2026 (day_range strategy mode added). Use this file as the brief for the next iteration. **Demo only.** Do not enable live trading. Do not paste MT5 passwords. `TRADE_MODE` must stay `demo`. Live servers are hard-blocked in Python and the EA.

The root `README.md` is stale (it still describes a $50 dummy bankroll). This file is the source of truth.

**11 Sep 2026:** Demo book cap raised — `MAX_POSITIONS=4` (UI/env clamp 1–4). Still 1 ticket per symbol. Live remains blocked.

---

## What this project is

A local **MetaQuotes-Demo** desk for EURUSD + GBPJPY + **XAUUSD (spot gold)** on M5. **Python is the only bot.**

1. **Python bot** logs into Wine MT5, reads quotes, places tickets, exposes a Flask API.
2. **Next.js dashboard** at `http://127.0.0.1:3000` polls that API and can start/stop/buy/sell/close.
3. **MQL5 indicator** paints the same analyst on the chart. It does **not** trade.
4. **MQL5 Expert** `FOMO_Second_Scalper` is a **display-only** chart helper. It does **not** send orders.

Python poll is **1 second**. Magic `26082601` is owned by Python. While START, Python heartbeats `MQL5/Files/FOMO_PYTHON_ENGINE_ACTIVE` so a leftover *old* trading EA still yields. The current Expert never writes `FOMO_EA_ENGINE_ACTIVE` and never `OrderSend`s. Do not attach a third-party EA on the same magic.

This is not a proven edge. Tiny fast scalps on quiet EURUSD do not make profit likely.

---

## How to run

```bash
# 1. MetaTrader (keep it open)
./scripts/start_mt5.sh

# 2. Python API (no reloader — restart after Python edits)
source .venv/bin/activate && python run.py

# 3. Dashboard
cd web && npm run dev
```

- UI: `http://127.0.0.1:3000`
- Flask: `http://127.0.0.1:5055` (`/api/state`, `/api/health`)
- Credentials: `.env` (gitignored). Server must stay `MetaQuotes-Demo`.
- Algo Trading in MT5 must be green or orders fail.
- Compile/install indicator + EA: `bash scripts/install_mt5_indicator.sh`
  Then Navigator → Refresh. Wine prefix: `$HOME/Library/Application Support/net.metaquotes.wine.metatrader5`.

---

## Layout

```
bot/analyst.py           Bill open rules (H1 trend) + ATR multiples for SL/TP
bot/day_range.py         Optional STRATEGY=day_range CRT prior-day PDH/PDL (demo)
bot/vwap_ema.py          Optional STRATEGY=vwap_ema Austin VWAP+EMA9 (demo)
bot/risk.py              sizing, pyramid, mixed-side, session drawdown only
bot/mutex.py             magic 26082601 heartbeat (Python owns; leftover EA detection)
bot/engine.py            1s loop, bankroll, fill venue, snapshot; never yields to an EA
bot/mt5_broker.py        MT5 fills + quotes
bot/paper_broker.py      paper fills on live quotes
bot/wine_mt5_agent.py    Wine IPC agent
bot/health.py            GET /api/health (demo lock, Wine IPC, processes)
bot/dashboard.py         Flask API
bot/auth.py              DESK_PIN cookie gate
bot/config.py            .env → Settings (live blocked, mutex on)
bot/news_calendar.py     USD CPI/NFP/FOMC skip window (gold default)
bot/pnl.py               closed/floating P&L summary
run.py                   start engine + Flask (no reloader)

web/                     Next.js command center
web/src/components/Dashboard.tsx
web/src/lib/types.ts

mt5/Indicators/FOMO_Analyst.mq5          chart overlay, no orders
mt5/Experts/FOMO_Second_Scalper.mq5      display-only chart helper, no orders

scripts/start_mt5.sh
scripts/install_mt5_indicator.sh         compiles .mq5 → .ex5, copies into Wine MT5

tests/test_bot.py
tests/test_dashboard.py
tests/test_health.py
tests/test_auth.py

cloudflare/             optional Worker in front of the Tunnel hostname
deploy/docker-compose.yml  VPS: Python + Next + cloudflared
```

Magic number everywhere: **26082601**.

---

## Shared analyst (open rules)

Python (`bot/analyst.py`) follows the [Trading Rush MACD 100-trade test](https://tradingrush.net/i-risked-macd-trading-strategy-100-times-heres-what-happened/). Not a proven live edge. Still 0.10 lot cap.

| Check | Rule |
|---|---|
| Trend | Close vs **EMA200**. Long only if close > EMA200. Short only if close < EMA200. |
| HTF | **Real gate:** H1 + H4 EMA21 must both agree with the MACD side (HOLD/disagree → skip). |
| RSI | **Real gate:** no BUY if RSI(14) > 70; no SELL if RSI(14) < 30. |
| ATR | **Real gate:** M5 ATR below pair floor (EUR 4 / JPY 8 / XAU 500) → skip dead tape. |
| Flat EMA200 | **Skip** if |ΔEMA200| over last **20** M5 bars < **0.15×ATR** (near-flat slope). |
| Stretched | **Skip** if signal candle / last **1–2** bars already ran ≥ **0.70×ATR** in trade direction (close-path). |
| Entry | Default **MACD 12/26/9**. BUY: MACD crosses **above** signal **below zero**. SELL: MACD crosses **below** signal **above zero**. Entry on the **cross-bar close**. |
| Stop | Beyond the pullback (last-5 M5 swing and/or EMA200, plus 1 pip). |
| Take | **2R** vs the pullback stop (Kotegawa package; not the old 1.5R Trading Rush clip). |
| Spread | Block if spread above the per-symbol cap (broker hygiene). |
| Session | **07:00–20:20 UTC**. Weekend guard on. |
| Size | **1%** of demo equity vs SL pips, capped by `MAX_LOT` **0.10**. Same size after a loss — no revenge scale-up. |
| Daily halt | Flatten / no new tickets when **today's P&L ≤ −2%** of equity (`DAILY_LOSS_PCT`). |
| Scale-in | **Max 1** ticket per symbol, **max 4 open tickets total** (demo). No averaging losers. |
| Fills | Buys at **ask**, sells at **bid**. |

Open tickets exit on the frozen pullback SL / 2R TP from entry. Broker SL/TP only — **no** profit-arm / giveback trail / % loss-cut overlay.

---

## Optional STRATEGY modes (demo only)

`STRATEGY` env selects the analyst path. Default **`macd`** — current desk behavior unchanged.

| Value | Behavior |
|---|---|
| `macd` (default) | Existing Trading Rush MACD 12/26/9 path. Optional Kronos agree-gate applies only here. |
| `vwap_ema` | Austin McConnell demo path: session **VWAP** (direction / invalidation) + **EMA9** (timing). Entry is a fresh EMA9 cross of VWAP on the closed M5 bar with price on the same side of VWAP. Kotegawa 1% / 2R / 2% daily halt. Kronos skipped. |
| `day_range` | DEMO-ONLY CRT-style prior-day range. Uses previous completed D1 candle: **PDH / PDL / mid=(PDH+PDL)/2**. On **M15** (fallback **M5**): after a liquidity sweep of PDH then close back inside the prior-day range while price is **above mid** → **SELL**; sweep of PDL then reclaim while **below mid** → **BUY**. Within `DAY_RANGE_MID_BAND_PCT` (default 5% of day range) of mid → no trade. Must reclaim **inside** [PDL, PDH]. |

**day_range v1 exits:** SL beyond the sweep extreme (+1 pip buffer); **TP at mid (TP1)**. TP2 at the opposite extreme is phase 2 — not implemented. Still one ticket per symbol, `MAX_POSITIONS`, session window, weekend guard, `HARD_BLOCK_LIVE`, demo only.

Enable on this Mac (restart `python run.py` after):

```bash
# in .env
STRATEGY=day_range
DAY_RANGE_MID_BAND_PCT=0.05
DAY_RANGE_TIMEFRAME=M15
```

Leave `STRATEGY=macd` (or unset) to go back to MACD. Do not enable live. Kronos is skipped when `STRATEGY=day_range` or `STRATEGY=vwap_ema`.

Layout: `bot/day_range.py`, `bot/vwap_ema.py`, tests in `tests/test_day_range.py` and `tests/test_vwap_ema.py`.

## Python bot

- Poll: `POLL_SECONDS` default **1**. Not an async tick stream — the engine thread sleeps 1s between `step()` calls.
- Fills: `FILL_VENUE=mt5` (tickets appear in the terminal) or `paper` (quotes from MT5, fills simulated).
- Bankroll: **MT5 demo equity** (often ~$100k). `$50` dummy is gone.
- Size: `1%` of equity vs analyst SL pips, capped by `MAX_LOT` (default **0.10**). On ~$99,999 equity that is **0.10 lots** (~$1/pip). Not 10 lots.
- Session loss stop: **20% of demo equity**.
- Max concurrent tickets: `MAX_POSITIONS` **4** on this demo desk (UI/env clamp **1–4**). Still **1 ticket per symbol**. No mixed BUY+SELL. Engine passes `require_pyramid_winners=True`. Entry only on a fresh MACD cross bar.
- Mutex: `EXECUTION_MUTEX_REQUIRED=true`. Python heartbeats `FOMO_PYTHON_ENGINE_ACTIVE`. Python never yields. A leftover trading EA on the chart is a health warning, not a second bot.
- Feed vs broker: `self.feed` is quotes; `self.broker` is fills. If MT5 attach fails, engine can fall back to paper fills.

Flask (PIN-locked when `DESK_PIN` is set; CORS is open only while the PIN is empty):

| Method | Path | Role |
|---|---|---|
| GET | `/api/health` | Wine IPC, demo lock, MT5 process, mutex |
| GET | `/api/state` | full snapshot (1s poll from UI) |
| GET/POST | `/api/unlock` | PIN status / unlock (HttpOnly cookie) |
| POST | `/api/lock` | clear unlock cookie |
| POST | `/api/start` `/api/stop` | loop |
| POST | `/api/step` | one cycle |
| POST | `/api/buy` `/api/sell` | manual |
| POST | `/api/close` `/api/close-all` | exits |
| POST | `/api/settings` | `fill_venue` or `max_positions` |
| POST | `/api/reconnect` | retry Wine MT5 |

Restart `python run.py` after Python edits. Flask has no reloader.

---

## 24/7 on Cloudflare (Mac can be off)

Cloudflare is the **edge**: HTTPS URL, Tunnel, optional Worker PIN check. It cannot run Wine MetaTrader or the 1s Python loop. A VPS (or a Mac mini that stays powered) is the always-on computer. **Demo only.** Do not set `TRADE_MODE=live`.

1. Put a **PIN** in `.env` on the server (`DESK_PIN=...`). Leave it empty on this Mac if you want localhost unlocked.
2. Set `UI_ORIGIN=https://your-hostname` and `DESK_COOKIE_SECURE=true` on the server.
3. Optional: same `DESK_SECRET` in `.env` and `cd cloudflare && npx wrangler secret put DESK_SECRET`, then attach the Worker as a route on that hostname.
4. Create a Cloudflare Tunnel (Zero Trust → Networks → Tunnels). Public hostname → `http://localhost:3000`. Copy the token into `deploy/.env` as `TUNNEL_TOKEN`.
5. On a Linux VPS: `cd deploy && docker compose --env-file .env up -d --build`. That stack is **paper/Yahoo** (no Wine MT5). Tickets in the MetaTrader app still need a Mac or Windows box that stays on.

Optional extra lock: Cloudflare Access on the hostname, policy **only your email**, “One-time PIN”. That is email OTP, on top of the desk PIN.

---

## Dashboard

Next.js app in `web/`. Proxies `/api/*` to Flask `:5055`. Polls `/api/state` every 1s and `/api/health` every 5s.

Shows: METATRADER ON/OFF, RETRY MT5, START/STOP, BUY/SELL at next lot, close per ticket, close all, paper vs MT5 fills, max 4 total tickets (demo), Kotegawa RM (1% / 2R / 2% daily halt, no trail), analyst score/RSI/ATR, equity, blockers, closed history.

Header wraps so UTC / START-STOP do not clip. Client swallows Urban VPN `chrome-extension://` noise (`instrumentation-client.ts`).

---

## MQL5 pieces (in the Wine terminal)

Wine MQL5 tree:

`$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/`

### Indicator — `FOMO_Analyst`

Drag onto EURUSD M5. EMA lines + buy/sell arrows. Same open rules. **Does not place orders.**

### Expert — `FOMO_Second_Scalper` (display only)

Version **2.00**. Chart comment only. **No `OrderSend`.** Refuses non-demo. Deletes any leftover `FOMO_EA_ENGINE_ACTIVE` lock. Prefer the `FOMO_Analyst` indicator for arrows; you can leave this Expert off the chart.

Compile note: `scripts/install_mt5_indicator.sh` compiles via `Z:` path and **must not** pass `/include:C:\Program Files\...` (Wine splits on the space). Copy `.mq5` + `.ex5` into `MQL5/Experts` and `MQL5/Indicators`. After install: Navigator → Refresh, remove any old trading EA from EURUSD M5.

---

## Risk / env (current)

Kotegawa / BNF (Takashi Kotegawa) money management on demo: **1% per ticket**, hard SL at structure, **2R take**, **2% daily halt**, never size up after a loss. Trail / giveback / % loss-cut overlays are gone.

```
TRADE_MODE=demo
MT5_SERVER=MetaQuotes-Demo
SYMBOL=EURUSD
SYMBOLS=EURUSD,GBPJPY,XAUUSD
RISK_PCT=0.01
MAX_LOT=0.10
SL_PIPS=20
TP_PIPS=40
POLL_SECONDS=1
MAX_POSITIONS=4
DAILY_LOSS_PCT=0.02
FILL_VENUE=mt5
HARD_BLOCK_LIVE=true
EXECUTION_MUTEX_REQUIRED=true
SESSION_START_HOUR=7
SESSION_END_HOUR=20 SESSION_END_MINUTE=20
MIN_ATR_PIPS=4.0
MIN_ATR_PIPS_JPY=8.0
MIN_ATR_PIPS_XAU=500.0
MIN_SCORE=4
TAKE_ATR_MULT=1.5
STOP_ATR_MULT=1.5
STOP_COOLDOWN_MINUTES=5
MIN_ENTRY_SECONDS=300
MAX_SPREAD_PIPS=2.0
MAX_SPREAD_PIPS_JPY=4.0
MAX_SPREAD_PIPS_XAU=80.0
DESK_PIN=
```

SL/TP env pip values are unused for new tickets. Broker stops come from the analyst pullback distance; take is **2R**. Daily halt is **2%** of equity. No profit-trail overlay.

---

## Gold (XAUUSD) — third symbol, same rules

Added 3 Sep 2026. **Same strategy, no rule changes**: MACD 12/26/9 fresh cross, M5 EMA200 agreement,
H1+H4 bias agreement, RSI(14) exhaustion filter, pullback SL, 2R TP, 07:00-20:20 UTC session,
1% risk sizing, magic 26082601, max 1 ticket per symbol and max 4 tickets total (demo). Hard SL/TP only.

Verified on this demo account (read-only probe, no orders): `mt5.symbol_info("XAUUSD")` resolves to
`Metals\XAUUSD` "Gold vs US Dollar", `digits=2`, `point=0.01`, `volume_min/step=0.01`, `volume_max=100`,
profit currency USD, `trade_mode=4` (full). `symbol_select("XAUUSD", True)` succeeds and quotes stream.
**Do not use the ticker `GOLD` on this server — that is `Nasdaq\Stock\GOLD`, the Barrick Gold *share*,
step 1.0.** Other aliases (`XAUUSD.`, `XAUUSDm`, `XAUUSD.m`, `GOLDUSD`, ...) do not exist here.

What is gold-specific:

| Item | Value | Why |
|---|---|---|
| Pip | **$0.01** (`point`, digits 2) | not the 0.0001 FX pip |
| Pip value | **$1.00 per pip per 1.00 lot** (100 oz x $0.01) | broker `tick_value`/`contract_size` first; this Wine bridge returns them as `None`, so the 100 oz convention is the fallback. Never $10/pip, never 1000/USDJPY |
| `MIN_ATR_PIPS_XAU` | **500** pips = $5.00 M5 ATR | 3000 M5 bars (19 Aug - 3 Sep 2026), 07:00-20:20 UTC: min 247, p25 420, median 551, p75 753, p95 1046 pips. 500 ~ 0.9x session median: dead tape blocked, spread stays ~9% of ATR like the FX pairs |
| `MAX_SPREAD_PIPS_XAU` | **80** pips = $0.80 | live demo spread sampled 36-49 pips ($0.36-$0.49) in NY hours; ~1.8x that leaves room for normal widening but blocks rollover/news blowouts |
| Lot | still `MAX_LOT` **0.10** | 1% of ~$99.6k = ~$996 risk / (SL pips x $1) is well above 0.10 on any realistic gold stop, so the 0.10 cap binds (e.g. 700-pip SL -> 0.10 lot = ~$70 risk, 0.07% of equity) |

---

## What is actually true in the market

- Session **07:00–20:20 UTC**. Auto-open needs **M5 close vs EMA200**, **fresh MACD 12/26/9 cross on the correct side of zero**, **H1+H4 agree**, RSI not exhausted, ATR above floor, EMA200 not flat, not stretched, and **spread ≤ cap**. Entry is the cross-bar close.
- Size cap is **0.10 lot**. Stop is **beyond the pullback**; take is **2R**. Daily halt **−2%** equity. **No new tickets Fri 21:00–Sun 21:00 UTC**; open tickets flatten at the weekend. After a stop, **5 minutes** with no new entry. Max **1** ticket per pair; max **4** open tickets total on demo.
- Closed history can mix old `PAPER-*` tickets, old 10-lot tickets, and current 0.01 tickets.

---

## Known gaps (improve these)

### Strategy (highest leverage)

- [ ] Analyst is Bill’s H1-with-trend money-management experiment, not a tested edge. No walk-forward, no spread/commission model, no hold-out. The source video is not a live audited result.
- [x] Python poll aligned to 1s (thread sleep, not a native tick stream).
- [x] Python owns 0.5×ATR TP / 2.8×ATR SL (high-hit scalp). The Expert does not trade.
- [x] Session defaults to London/NY (`SESSION_START_HOUR=7`, `SESSION_END_HOUR=20 SESSION_END_MINUTE=20`). USD news blackout on gold (`NEWS_BLACKOUT`, default XAUUSD). Monday-open skip 30m after Sunday 21:00 UTC.
- [x] ATR floor + HTF + RSI + flat EMA200 + stretched-move are **real** `analyze()` blockers (engine still overlays `ATR_GATE`). Demo only; `HARD_BLOCK_LIVE=true`.
- [x] Open gate is H1+H4 trend + M5 EMA21 pullback, not a 60s clock and not score 4/8.
- [x] Size cap is 0.10 lot, max 1 ticket per pair (UI clamp 1–2). No averaging into losers.
- [x] `decide()` and `bot/strategy.py` removed. Risk is gates only; analyst owns signal.

### Execution

- [x] One bot. Python owns magic 26082601. Expert is display-only.
- [ ] Wine IPC timeouts / zombie agents still happen. Attach is fragile.
- [ ] Filling mode IOC/FOK/RETURN is guessed; no retry/backoff policy.
- [ ] Python may send SL/TP depending on broker path — confirm they actually land on MT5 tickets.
- [x] Size default is 0.10 lot.
- [x] `MAX_LOT=0.10` on ~$100k demo. ~$1/pip, not 10-lot tickets.

### Product / UX

- [ ] README is stale ($50 dummy bankroll).
- [ ] Dashboard cannot edit analyst thresholds, session hours, ATR floor, or dollar TP.
- [ ] No backtest page, no equity curve of **only this magic**, no spread overlay.
- [ ] Manual BUY/SELL ignore the analyst. Easy to fight the bot.
- [ ] Paper vs MT5 toggle can confuse “am I seeing terminal tickets?”

### Engineering

- [x] PIN lock (`DESK_PIN`) + Cloudflare Tunnel so the UI can stay up on a VPS when this Mac is off. Cloudflare does **not** run Wine/MT5.
- [ ] Flask + Next.js for a local desk is two processes. Fine, but Python changes need a manual restart.
- [ ] Tests cover sizing/analyst/dashboard JSON, not Wine/MT5.
- [ ] Indicator/Python scoring can drift; no golden-vector test that both score the same bars the same way.
- [ ] `mt5_start.ini` (if present) can hold a plaintext password — never commit.

---

## Kronos-mini DEMO filter (optional gate)

Added 9 Sep 2026. **Demo only.** Does not replace MACD Bill. When `KRONOS_FILTER=false`
(default), behavior is unchanged. When `true`, a fresh MACD entry also needs Kronos-mini
forecast slope/direction to agree (CHOP / opposite / missing / stale → skip).

- `bot/kronos_filter.py` — reads `data/kronos_decisions.json`
- `scripts/export_m5_for_kronos.py` — MT5 M5 OHLCV → CSV for EURUSD/GBPJPY/XAUUSD
- `scripts/run_kronos_demo_filter.py` — scp → casperserver `~/kronos` → forecast → pull JSON
- `scripts/kronos_demo_filter_forecast.py` — server-side Kronos-mini inference
- Keep `HARD_BLOCK_LIVE=true`. Never enable live. Refresh decisions before relying on the gate.

Toggle: `KRONOS_FILTER=true` in `.env`, then restart `python run.py`.

---

## USD news blackout (gold only by default)

Added 10 Sep 2026. **Not an entry engine.** APFX-style gold vs USD data (CPI / NFP / unemployment / FOMC) is encoded as a **skip** around the print, not as a BUY/SELL. Kronos and MACD still own direction.

- `bot/news_calendar.py` — 2026 CPI / NFP / FOMC / PCE / GDP in America/New_York (DST-aware)
- Default window: 30 minutes before, 20 after (FOMC keeps 60 after for the presser)
- Default pair: `XAUUSD` only (`NEWS_PAIRS`). EURUSD/GBPJPY still trade through the print
- Does **not** flatten open gold. New tickets only.
- Desk Why shows the gate and the next print

Also: `ATR_GATE` skips quiet tape; `MONDAY_OPEN_SKIP_MIN` skips the first half-hour after Sunday 21:00 UTC.

---

## Hard rules for the next change

1. Stay on **demo**. Do not set live. Do not dump `.env` secrets.
2. Do not restore the prompt’s “buy if ask > previous close” entry. It spams.
3. Do not restore the **$50 dummy** bankroll unless explicitly asked.
4. Do not put a second trading EA on magic 26082601. Python is the only bot.
5. If asked for both a strategy change and live enablement: do the strategy on demo only.
6. Verify UI in the browser when the dashboard changes.
7. After Python edits, restart `python run.py`.
8. After MQL5 edits, re-run `scripts/install_mt5_indicator.sh` and Refresh Navigator.

---

## Suggested next iterations (pick one)

1. **One desk.** Done — Python is the only bot. Expert is display-only.
2. **Unify exits.** Done — ATR multiples (2.0× TP / 1.5× SL), not % of price.
3. **Honest size.** Cap is **0.10**. Do not silently restore 10 lots.
4. **MACD Bill on demo.** M5 EMA200 + MACD 12/26/9, pullback SL, 2R TP, max 1 ticket. Not a proven edge.
4. **Same-score test.** Fixture of M5 bars → expected score; assert Python and a documented MQL5 port match.
5. **Refresh README** from this file so the runbook is not lying.
6. **Only then** consider a different symbol/timeframe or a real backtest — not more lot size.

When you subscribe to a live account, say so explicitly. Until then, everything here is demo cash.
