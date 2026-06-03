# kalshi-edge-bot

A deterministic, fee-aware trading bot for [Kalshi](https://kalshi.com) **and
[Polymarket](https://polymarket.com)**. Focused on **structural edges** that
don't require an LLM oracle: overround arbitrage on Kalshi and
smart-money consensus copy-trading on Polymarket.

> **No promises.** Prediction markets are competitive. Edges are small.
> This bot will lose money if you run it without understanding the code.
> Paper trade first. Read every module.

## Status: v0.2 — two venues, paper-only

What's built:
- ✅ Kalshi fee model (`src/utils/fees.py`) — the real `0.07·N·P·(1-P)` formula
- ✅ Overround arbitrage scanner (`src/strategies/overround_arb.py`)
- ✅ Pre-trade risk gates (`src/risk/gates.py`)
- ✅ Calibration tracking with Brier scores (`src/utils/calibration.py`)
- ✅ Venue-agnostic paper executor + per-venue fee models
  (`src/utils/fee_models.py`, `src/paper/executor.py`)
- ✅ Polymarket read-only client with offline replay support
  (`src/clients/polymarket.py`)
- ✅ Smart-money cohort selection (`src/signals/smart_money.py`)
- ✅ K-of-N consensus-copy strategy (`src/strategies/consensus_copy.py`)
- ✅ Polymarket copy-runner with idempotent signal log + slippage gates
  (`src/jobs/copy_runner.py`)

What's next:
- [ ] Port signed Kalshi REST + WS client (from ryanfrigo/kalshi-ai-trading-bot, MIT)
- [ ] Port `safe_compounder` strategy, refactored to use new fees module
- [ ] Wire ingest → strategy → gates → execute → calibration pipeline
- [ ] Dashboard panels for the Polymarket cohort + consensus signals + latency
- [ ] Live Polymarket CLOB execution via `polymarket-client` SDK

## Why these strategies

### Overround arbitrage (Kalshi)
On a binary YES/NO market, `YES + NO` must equal $1 at settlement.
When the order book shows `YES_ask + NO_ask < $1 − fees − margin`,
buying both sides locks in a riskless profit. No probability model needed.
The catch: opportunities are small and require both legs to fill.

### Smart-money consensus copy (Polymarket)
Polymarket is on-chain (Polygon, USDC); every wallet's trades are public.
We rank wallets by realized P&L on resolved markets (with eligibility
gates for min trades, min resolved markets, recency, and a stability
proxy for jackpot/blowup wallets), pick the top-N cohort, and only fire
a paper trade when ≥K cohort wallets have ended up **net long** on the
same `(condition_id, outcome_token_id)` within a rolling lookback window.

Key invariants enforced in `src/strategies/consensus_copy.py`:
- One wallet = one vote, regardless of how many partial fills.
- Wallets that opened and closed within the window do NOT vote.
- Signals are keyed by `(strategy, venue, condition_id, outcome_token_id,
  window_start_unix, cohort_version)` — idempotent across restarts.
- Paper fills are marked at the **current CLOB best ask**, NOT the
  copied wallet's price, and rejected if `ask − wallet_avg_entry > max_slippage`.

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
pip install hypothesis    # property-test deps not pinned in requirements yet
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
| Polymarket max slippage vs wallet avg | 2¢ |
| Polymarket cohort size (N) | 10 |
| Polymarket consensus threshold (K) | 5 |
| Polymarket min wallet trades | 50 |
| Polymarket min wallet resolved markets | 20 |

All tunable in `src/risk/gates.py` and `src/config.py`. Don't loosen
them without a backtest.

## CLI

Kalshi commands:
```
cli.py health          Verify API connectivity and credentials
cli.py scan            One scan-and-(paper)-execute pass for overround arb
cli.py paper-run       Continuous Kalshi paper-trading loop
cli.py status          Paper-portfolio state
cli.py calibration     Brier score + reliability diagram
```

Polymarket commands:
```
cli.py polymarket-leaderboard   Re-rank the leaderboard under our criteria + show the cohort
cli.py polymarket-scan          One consensus-copy scan + (paper) execute pass
```

## Dashboard

A read-only Streamlit dashboard ships in `dashboard/`. It shows:
equity curve, open + closed positions, recent fills, fees over time,
per-strategy Brier scores, and the kill-switch state.

```bash
streamlit run dashboard/app.py -- \
    --paper-db data/paper.db \
    --calibration-db data/calibration.db \
    --starting-bankroll 10000
```

Opens at <http://localhost:8501>. Auto-refreshes every 10s.
It opens SQLite in `mode=ro`, so it is safe to run alongside a live
runner without lock contention.

Polymarket-specific panels (cohort table, consensus feed, latency,
attribution) are planned — see plan.md.

## Attribution

Portions of the Kalshi client and position tracker will be ported from
[ryanfrigo/kalshi-ai-trading-bot](https://github.com/ryanfrigo/kalshi-ai-trading-bot)
(MIT). Architecture inspiration from
[OctagonAI/kalshi-trading-bot-cli](https://github.com/OctagonAI/kalshi-trading-bot-cli)
(edge framing, gate model). Polymarket integration uses the official
[polymarket-client](https://pypi.org/project/polymarket-client/) SDK
(beta) for any live-execution work; the read-only data layer is built
directly against Polymarket's public Gamma / Data / CLOB / LB APIs.

## License

MIT
