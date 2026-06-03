# kalshi-edge-bot

A deterministic, fee-aware trading bot for [Kalshi](https://kalshi.com).
Focused on **structural edges** that don't require an LLM oracle:
overround arbitrage and favorite-longshot bias harvesting.

> **No promises.** Prediction markets are competitive. Edges are small.
> This bot will lose money if you run it without understanding the code.
> Paper trade first. Read every module.

## Status: v0.1 — foundations

What's built:
- ✅ Kalshi fee model (`src/utils/fees.py`) — the real `0.07·N·P·(1-P)` formula
- ✅ Overround arbitrage scanner (`src/strategies/overround_arb.py`)
- ✅ Pre-trade risk gates (`src/risk/gates.py`)
- ✅ Calibration tracking with Brier scores (`src/utils/calibration.py`)
- ✅ Test suite for all of the above

What's next:
- [ ] Port signed Kalshi REST + WS client (from ryanfrigo/kalshi-ai-trading-bot, MIT)
- [ ] Port `safe_compounder` strategy, refactored to use new fees module
- [ ] Wire ingest → strategy → gates → execute → calibration pipeline
- [ ] Paper-trading runner with daily report
- [ ] CLI (`run`, `paper`, `status`, `calibration`)

## Why these strategies

### Overround arbitrage
On a binary YES/NO market, `YES + NO` must equal $1 at settlement.
When the order book shows `YES_ask + NO_ask < $1 − fees − margin`,
buying both sides locks in a riskless profit. No probability model needed.
The catch: opportunities are small and require both legs to fill.

### Safe Compounder (favorite-longshot bias)
Retail traders systematically overpay for unlikely positive outcomes
("YES, it will happen"). Selling NO on near-certain tails with maker
orders captures this bias while paying near-zero fees. Math is in
`safe_compounder.py` (port pending).

## What this bot WILL NOT do

- LLM-based price prediction (uncalibrated; burns API credit)
- Scalping (thin books + fees = death)
- Multi-agent debate (cosplay; no proven edge)
- Sentiment trading on news (slow, lossy, regime-dependent)
- Anything that requires a black-box "AI says 72%" oracle

## Setup

```bash
git clone <this repo>
cd kalshi-edge-bot
python -m venv .venv
.venv/Scripts/activate    # or source .venv/bin/activate on Unix
pip install -r requirements.txt
pytest
```

## Risk rails (hardcoded defaults)

| Rail | Default |
|---|---|
| Max per position | 2% of bankroll |
| Max total deployed | 25% of bankroll |
| Max positions per event family | 5 |
| Drawdown kill switch | 10% from 30d peak |
| Min market volume (24h) | $500 |
| Min Kelly fraction to trade | 0.5% |

All tunable in `src/risk/gates.py`. Don't loosen them without a backtest.

## Dashboard

A read-only Streamlit dashboard ships in `dashboard/`. It shows:
equity curve, open + closed positions, recent fills, fees over time,
per-strategy Brier scores, and the kill-switch state.

```bash
pip install -r requirements.txt   # pulls in streamlit + pandas
streamlit run dashboard/app.py -- \
    --paper-db data/paper.db \
    --calibration-db data/calibration.db \
    --starting-bankroll 10000
```

Opens at <http://localhost:8501>. Auto-refreshes every 10s.
It opens SQLite in `mode=ro`, so it is safe to run alongside a live
`arb_runner` without lock contention.

## Attribution

Portions of the Kalshi client and position tracker will be ported from
[ryanfrigo/kalshi-ai-trading-bot](https://github.com/ryanfrigo/kalshi-ai-trading-bot)
(MIT). Architecture inspiration from
[OctagonAI/kalshi-trading-bot-cli](https://github.com/OctagonAI/kalshi-trading-bot-cli)
(edge framing, gate model).

## License

MIT
