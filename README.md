# kalshi-edge-bot

A deterministic, fee-aware trading bot for [Kalshi](https://kalshi.com) **and
[Polymarket](https://polymarket.com)**. Focused on **structural edges** that
don't require an LLM oracle: overround arbitrage on Kalshi and
smart-money consensus copy-trading on Polymarket, plus cross-venue
spread diagnostics between likely-equivalent markets.

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
- ✅ Safe Compounder signal scanner, refactored onto the shared Kalshi fee model
  (`src/strategies/safe_compounder.py`)
- ✅ Cross-venue Kalshi/Polymarket spread scanner with persistent diagnostics
  (`src/strategies/cross_venue_spread.py`, `src/jobs/cross_venue_runner.py`)
- ✅ React/FastAPI dashboard v2 with cohort, consensus signal, latency,
  orderbook, cross-venue spread, and kill-switch panels (`dashboard_v2/`)
- ✅ Soccer / FIFA WC bet-builder pipeline: World Football Elo,
  Dixon-Coles bivariate Poisson with τ correction, position-prior
  player-share, minutes/cards models, Kalman state-space updater,
  10k-sim joint Monte Carlo simulator, bet-builder pricing with
  correlation factor + edge + Kelly, isotonic calibration + CLV vs
  Pinnacle, StatsBomb open-data + The Odds API ingest, "Soccer"
  dashboard tab with interactive bet builder
  (`src/sports/soccer/`, `dashboard_v2/api/routes/soccer.py`,
   `dashboard_v2/web/src/panels/soccer/`)

What's next:
- [ ] Add Kalshi WebSocket ingest for lower-latency orderbook updates
- [ ] Wire `safe_compounder` into runner → gates → execute → calibration
- [ ] Wire ingest → strategy → gates → execute → calibration pipeline
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
`safe_compounder.py`; runner/execution wiring is still pending.

### Cross-venue spread diagnostics
`cli.py cross-venue-scan` compares likely-equivalent binary markets across
Kalshi and Polymarket, then records the latest run and matched price rows
to SQLite for the dashboard. It reports both:
- valuation spread: `Kalshi YES ask - Polymarket YES/UP ask`
- executable spread: best of `Kalshi YES bid - Polymarket ask` and
  `Polymarket bid - Kalshi YES ask`

This is scanner-only. It does not execute trades because equivalence must
be checked against market rules before any cross-venue position is safe.

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
| Polymarket max slippage vs wallet avg | 2¢ |
| Polymarket cohort size (N) | 10 |
| Polymarket consensus threshold (K) | 5 |
| Polymarket min wallet trades | 50 |
| Polymarket min wallet resolved markets | 20 |
| Soccer min edge (decimal) to recommend | 3% |
| Soccer Kelly fraction | 0.25 (quarter Kelly) |
| Soccer Kelly cap | 2% of bankroll |
| Soccer simulations per quote | 10,000 |

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
cli.py health --venue all          Check Kalshi + Polymarket connectivity
cli.py health --venue kalshi       Check Kalshi connectivity/auth
cli.py polymarket-health           Check Polymarket public API connectivity
cli.py polymarket-leaderboard   Re-rank the leaderboard under our criteria + show the cohort
cli.py polymarket-scan          One consensus-copy scan + (paper) execute pass
cli.py cross-venue-scan         Compare likely-equivalent Kalshi/Polymarket spreads
```

Soccer / FIFA WC commands:
```
cli.py soccer-ingest   Pull StatsBomb open-data (WC 2018/2022 + Euro 2020/2024) and refit Elo + Dixon-Coles
cli.py soccer-fetch    Cache StatsBomb open-data matches for a single competition/season
cli.py soccer-fit      Fit Dixon-Coles + Elo from cached matches and print diagnostics
cli.py soccer-simulate Run a 10k-sim Monte Carlo simulation for a fixture
```

The Soccer dashboard tab exposes two buttons: **FIT REAL DATA**
(StatsBomb open-data ingest, ~5s, 230 real matches across the last
two World Cups + Euros, Spain/England/France/etc. team strengths
reflecting real tournament form) and **SEED DEMO MODEL** (synthetic
priors only, for offline use).

The Soccer dashboard tab also exposes an interactive bet builder that
hits `/api/soccer/bet-builder` (POST) — pick legs, paste in a
bookmaker's combined SGP price, and the panel returns fair price,
correlation factor, edge, and Kelly stake. The model layer is
documented inline in `src/sports/soccer/`:

| Layer | File | Notes |
|---|---|---|
| Team strength | `models/dixon_coles.py` | Bivariate Poisson + τ correction, MLE with time decay |
| Ratings prior | `ratings/elo.py` | World Football Elo (tournament K, GD multiplier, home advantage) |
| In-tournament | `models/state_space.py` | Per-team Kalman update on attack/defense |
| Player share | `models/player_share.py` | Dirichlet-multinomial; cold-start from positions, refit from history |
| Minutes | `models/minutes.py` | Start prob + sub timing; minute factor for scorer sampling |
| Cards | `models/cards.py` | Per-player Poisson with referee strictness multiplier |
| Simulator | `simulator/match_sim.py` | Joint MC: minutes → goals → scorers → cards |
| Bet builder | `simulator/bet_builder.py` | Predicate library + counting → fair price |
| Calibration | `calibration/isotonic.py` | sklearn isotonic per market type + CLV vs Pinnacle close |
| Pricing | `pricing/edge.py` | Edge, fractional Kelly with cap |

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
orderbook depth, and kill-switch controls) are available in `dashboard_v2/`.

Backend:
```bash
uvicorn dashboard_v2.api.main:app --reload --host 127.0.0.1 --port 8787
```

Frontend:
```bash
cd dashboard_v2/web
npm install
npm run dev
```

## Attribution

Portions of the Kalshi client and position tracker will be ported from
[ryanfrigo/kalshi-ai-trading-bot](https://github.com/ryanfrigo/kalshi-ai-trading-bot)
(MIT). Architecture inspiration from
[OctagonAI/kalshi-trading-bot-cli](https://github.com/OctagonAI/kalshi-trading-bot-cli)
(edge framing, gate model). Polymarket integration uses the official
[polymarket-client](https://pypi.org/project/polymarket-client/) SDK
(beta) for any live-execution work; the read-only data layer is built
directly against Polymarket's public Gamma / Data / CLOB / LB APIs.

The soccer pipeline uses [StatsBomb open data](https://github.com/statsbomb/open-data)
(free for non-commercial use, CC-BY-NC) for historical xG/event data
and [The Odds API](https://the-odds-api.com) (free tier) for leg-level
market odds. CLV benchmarking targets Pinnacle's closing line.

## License

MIT
