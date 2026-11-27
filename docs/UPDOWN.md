# BTC 5m / 15m Up/Down + the Telegram assistant

Polymarket runs a Bitcoin "Up or Down" market every 5 and every 15 minutes. Up wins when Chainlink's 60-second
average BTC price at the end is at least the one at the start. This part of the app watches both markets, learns
them, pings you on Telegram, keeps your bankroll and sends a daily plan.

## How it works

| Piece | Where | What it does |
|---|---|---|
| Live loop | `app/updown/live.py`, service `updown` | Every 10 s: BTC price (Coinbase) + both order books (Polymarket) → features → model → snapshot; pings on a signal |
| Model | `app/updown/model.py` | Logistic regression and a small neural network (32-16, ReLU). Trained every 3 hours (Celery `updown_train`) on the last 30 days, judged on the newest 20% of windows |
| Features | `app/updown/features.py` | Distance to the price to beat in remaining-volatility units (`z`), momentum, volatility, the market's own price, time of day, and Claude's daily lean + news sentiment |
| Daily plan | `app/updown/daily.py` | Each morning: the "Bitcoin above ___" ladder against the 1-day forecast → best bet or no bet |
| Assistant | `app/assistant.py` | Telegram: pings, "in"/"skip", your balance, the savings rule, open bets, questions answered by Claude |
| Bankroll | `app/bitcoin/bankroll.py` | One ledger for the website and Telegram |

**Prices: Chainlink, not an exchange.** The markets settle on Chainlink's BTC/USD, which differs from any single
exchange by tens of dollars, enough to decide a 5-minute window. The live loop reads Chainlink directly from
Polymarket's real-time socket (`app/bitcoin/chainlink.py`, `wss://ws-live-data.polymarket.com`) for "price now" and
the price to beat, and falls back to Coinbase corrected by the measured gap if the socket is down. Polymarket publishes
the official `priceToBeat` / `finalPrice` only after a window ends; those official numbers replace the live estimate
in the stored history, so the model always trains on what the market really settled on.

**First results on real data (3 days, 1,152 windows, Nov 2026).** On the newest 231 windows: the model was right 72.6%
of the time, the market's own odds 73.2%; log loss 0.500 vs the market's 0.495. When it was at least 90% sure it was
right 96.9% of the time, and so was the market. The alert rule fired 7 times and won 7, far too few bets to mean
anything. Verdict: NOT PROVEN; pings are PAPER. The market is hard to beat. More data (it keeps collecting) and the
live Chainlink price are what can change that, and the 3-hourly retraining will say so if it happens.

**Proven vs paper.** A model is *proven* only when, on the newest windows it never trained on, it beats the market's
own odds (log loss) **and** replaying the alert rule made money on at least 20 bets. Until then every ping says
PAPER SIGNAL: practice only. The market is hard to beat; most of the time the honest answer is "no edge".

**The alert rule.** The model is at least 90% sure of one side, that beats the share price plus the fee by at least 3
points, and there are at least 45 seconds left.

**The savings rule.** When a win lifts your balance above its previous best, half of the new part goes to the vault
and is never bet again. Example: best $10, you now have $14 → set aside $2. A win that only recovers a loss saves nothing.

## The service block

Add this under `services:` in the server's `docker-compose.prod.yml`, next to `worker` (same image and `.env`):

```yaml
  updown:
    build: ./backend
    env_file: .env
    restart: unless-stopped
    command: python -m app.cli updown-live
    depends_on: [api]
```

## First start (on the server)

```bash
docker exec -i mlearning-db-1 psql -U mlesprint -d mlesprint -1 -v ON_ERROR_STOP=1 < supabase/migrations/20261127000014_updown.sql
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api python -m app.cli updown-backfill --days 7
docker compose -f docker-compose.prod.yml exec api python -m app.cli updown-train
```

Telegram: create a bot with @BotFather, put `TELEGRAM_BOT_TOKEN` in `.env`, restart `updown`, message the bot (it
replies with your chat id), put `TELEGRAM_CHAT_ID` in `.env`, restart `updown` again. Test with
`python -m app.cli telegram-test`.
