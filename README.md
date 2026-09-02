# FOMO demo trader

Python bot that logs into **MetaQuotes-Demo**, places a tiny probe trade, then follows a 9/21 EMA crossover on EURUSD M5.

- Demo only. Live servers are refused.
- Risk is sized from a **$50** budget, even if the demo account shows a larger fake balance.
- Max size is **0.01 lot**, stop 20 pips, take 40 pips, one position at a time.
- Session stop at 20% of the $50 budget.

This will not turn $50 into $10,000. It is a connection and strategy test.

# FOMO demo trader

Python bot that logs into **MetaQuotes-Demo**, places a tiny probe trade, then follows a 9/21 EMA crossover on EURUSD M5.

- Demo only. Live servers are refused.
- Risk is sized from a **$50** budget, even if the demo account shows a larger fake balance.
- Max size is **0.01 lot**, stop 20 pips, take 40 pips, one position at a time.
- Session stop at 20% of the $50 budget.

This will not turn $50 into $10,000. It is a connection and strategy test.

## Run

1. Start MetaTrader 5 (keep it open):

```bash
./scripts/start_mt5.sh
```

2. Start the bot API:

```bash
cd /Users/mohitshah/fomo-app
source .venv/bin/activate
python run.py
```

3. Start the Next.js dashboard (separate terminal):

```bash
cd /Users/mohitshah/fomo-app/web
npm run dev
```

4. Open [http://127.0.0.1:3000](http://127.0.0.1:3000). The UI proxies `/api/*` to the Python bot on port 5055.

If orders fail with AutoTrading disabled, click **Algo Trading** in the MetaTrader toolbar so it is green.

Credentials stay in `.env` (not committed).
