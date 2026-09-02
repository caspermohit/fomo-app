# FOMO current state — improve from here

Snapshot of the repo as of 28 Aug 2026. Use this file as the brief for the next iteration. **Demo only.** Do not enable live trading. Do not paste MT5 passwords. `TRADE_MODE` must stay `demo`. Live servers are hard-blocked in Python and the EA.

The root `README.md` is stale (it still describes a $50 dummy bankroll). This file is the source of truth.

---

## What this project is

A local **MetaQuotes-Demo** desk for EURUSD M5. **Python is the only bot.**

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

Python (`bot/analyst.py`) and the chart indicator follow Trading Rush [Bill the Bot](https://www.youtube.com/watch?v=BIGVu71IT1w) ([writeup](https://tradingrush.net/i-built-a-trading-bot-and-it-actually-made-1000-in-2hrs/)): **money management + multi-timeframe trend**, not a 4/8 score gate and not a candle-color trigger. The video is a demo/YouTube experiment (news blew ~$1000 mid-run). This desk does **not** promise $1000 in 2 hours. Still EURUSD only. Still 0.10 lot cap.

| Check | Rule |
|---|---|
| Trend | H1 **and** H4 close vs EMA21 must agree. If H4 history is missing, H1 alone is used until H4 fills. |
| Entry | Clock: one market ticket every **60s** at last price. No M5 candle signal. |
| Spread | Block if spread **> 2.0 pips** (his London/NY reason). |
| ATR | Open blocked if ATR **< 1 pip**. |
| Session | **London + New York** (`07–21 UTC` wall clock). |
| Size | **≤1%** of demo equity vs SL pips, capped by `MAX_LOT` **0.10**. |
| Scale-in | Up to **8** same-side tickets. Averaging is allowed. |
| Exits | **1:1** — TP **1.5×ATR** and SL **1.5×ATR** (his “good distance”, ~50% WR math). |

While a Python position is open, the analyst closes on **1:1 ATR multiples**: take-profit **1.5×ATR**, stop-loss **1.5×ATR**. On quiet EURUSD M5 (~1.5 pip ATR) that is about **2.25 / 2.25 pips** (~$2.25 / $2.25 on 0.10 lot). Broker SL/TP on new tickets use those pips.

Exits live only in Python: **1.5×ATR TP / 1.5×ATR SL**. Not percent of EURUSD. The 4/8 M5 score is still computed for the log but is **not** the open gate.

---

## Python bot

- Poll: `POLL_SECONDS` default **1**. Not an async tick stream — the engine thread sleeps 1s between `step()` calls.
- Fills: `FILL_VENUE=mt5` (tickets appear in the terminal) or `paper` (quotes from MT5, fills simulated).
- Bankroll: **MT5 demo equity** (often ~$100k). `$50` dummy is gone.
- Size: `1%` of equity vs analyst SL pips, capped by `MAX_LOT` (default **0.10**). On ~$99,999 equity that is **0.10 lots** (~$1/pip). Not 10 lots.
- Session loss stop: **20% of demo equity**.
- Max concurrent tickets: `MAX_POSITIONS` default **8**. No mixed BUY+SELL. Bill may average into losers. Pace 60s between new tickets.
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

Shows: METATRADER ON/OFF, RETRY MT5, START/STOP, BUY/SELL at next lot, close per ticket, close all, paper vs MT5 fills, max positions, analyst score/RSI/ATR, equity, blockers, closed history.

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

```
TRADE_MODE=demo
MT5_SERVER=MetaQuotes-Demo
SYMBOL=EURUSD
RISK_PCT=0.01
MAX_LOT=0.10
SL_PIPS=20
TP_PIPS=40
POLL_SECONDS=1
MAX_POSITIONS=8
FILL_VENUE=mt5
HARD_BLOCK_LIVE=true
EXECUTION_MUTEX_REQUIRED=true
SESSION_START_HOUR=7
SESSION_END_HOUR=21
MIN_ATR_PIPS=1
MIN_SCORE=4
TAKE_ATR_MULT=1.5
STOP_ATR_MULT=1.5
STOP_COOLDOWN_MINUTES=5
MIN_ENTRY_SECONDS=60
MAX_SPREAD_PIPS=2.0
DESK_PIN=
```

SL/TP env pip values are unused for new tickets. Broker stops are **1.5×ATR / 1.5×ATR** in pips.

---

## What is actually true in the market

- FX London/NY hours only (`7–21 UTC`). Auto-open needs **H1 and H4 aligned**, **spread ≤ 2 pips**, and **ATR ≥ 1**. No candle-color trigger.
- Typical quiet EURUSD ATR on M5 is **~1.2 pips** → ATR gate can pass; H1/H4 can still disagree.
- Fast scalp size is **0.10 lot**. Exits are **1.5×ATR TP / 1.5×ATR SL** (~2.25 / 2.25 pips on a 1.5-pip M5 ATR, ~$2.25 / $2.25). **No new tickets Fri 21:00–Sun 21:00 UTC**; open tickets flatten at the weekend. After a stop, **5 minutes** with no new entry. At most one new ticket per **60 seconds**.
- Closed history can mix old `PAPER-*` tickets, old 10-lot tickets, and current 0.01 tickets.

---

## Known gaps (improve these)

### Strategy (highest leverage)

- [ ] Analyst is Bill’s H1-with-trend money-management experiment, not a tested edge. No walk-forward, no spread/commission model, no hold-out. The source video is not a live audited result.
- [x] Python poll aligned to 1s (thread sleep, not a native tick stream).
- [x] Python owns 1.5×ATR TP / 1.5×ATR SL (1:1). The Expert does not trade.
- [x] Session defaults to London/NY (`SESSION_START_HOUR=7`, `SESSION_END_HOUR=21`). No news filter, no rollover, no Monday open.
- [x] ATR floor is 1 pip (was 6). Quiet tape can still fail if H1 is flat.
- [x] Open gate is H1+H4 trend + 60s clock, not score 4/8 and not M5 candle color.
- [x] Size cap is 0.10 lot, up to 8 tickets. Bigger than a $1 micro scalp, not 10-lot stacking.
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
4. **Bill the Bot.** Done on demo from [the 1-hour video](https://www.youtube.com/watch?v=BIGVu71IT1w): multi-TF trend, clock entries, 1:1 ATR, London/NY, scale-in. Not a promise of the video’s $1000/2h.
4. **Same-score test.** Fixture of M5 bars → expected score; assert Python and a documented MQL5 port match.
5. **Refresh README** from this file so the runbook is not lying.
6. **Only then** consider a different symbol/timeframe or a real backtest — not more lot size.

When you subscribe to a live account, say so explicitly. Until then, everything here is demo cash.
