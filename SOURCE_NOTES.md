# Source Notes

This file credits the open-source repos and academic sources that informed
each component of kalshi-edge-bot. We did not vendor any of these as-is —
every implementation here is original code, but the *ideas* and *formulas*
come from prior art. Stand on shoulders.

---

## Phase 0 — Core Plumbing

### `src/clients/kalshi.py` — REST client
- **Skeleton** loosely inspired by `ryanfrigo/kalshi-ai-trading-bot/src/clients/kalshi_client.py`
  (MIT). Restructured: removed websocket helpers we don't use yet, added
  retry/backoff and rate-limit throttle.
- **RSA-PSS signing** verified bit-for-bit against the canonical
  `Kalshi/kalshi-starter-code-python` (`clients.py`, SHA `ee0ba2da`). Cross-
  verified with TypeScript (`linq-team/kalshi-agent`) and Rust
  (`ruvnet/RuVector`) reimplementations. Path-prefix validation added based
  on the consistent "#1 gotcha" warning from those repos.

### `src/utils/orderbook_parser.py`
- **Bids-only quirk** documented by `jonDomino/nba_scanner` and confirmed in
  `TweedBeetle/prediction-market-interface` docs scrape of the official
  Kalshi docs.
- **Ascending-order bug** (best bid is `levels[-1]`, not `[0]`) flagged by
  the same research. We use a defensive `max(levels, key=price)` so we don't
  depend on the documented order being honored.
- **Cent → dollar migration** (Jan/Mar 2026): support both `int cents` and
  `str dollars` representations — pattern from
  `Fincept-Corporation/FinceptTerminal/fincept-qt/scripts/prediction_kalshi.py`.

### `src/utils/fees.py`
- **Taker formula** `ceil(0.07 * C * P * (1-P) * 100) / 100` published by
  Kalshi; verified in production code at
  `zachdaube/kalshi-market-maker/src/fees.py`.
- **Maker formula** `ceil(0.0175 * C * P * (1-P) * 100) / 100` (April 2025
  change; July 2025 P*(1-P) scaling). Verified at same source.
- **FP-safe ceiling** (`ceil(raw*100 - 1e-9)/100`) is original — caught by
  our own unit tests on a `0.07*100*0.5*0.5 = 1.7500000000000002` edge case.
- **Kelly (binary)** `f* = (p - ask) / (1 - ask)` from Kelly 1956; in code
  matches `Dotan-Peleh/poly-trader/risk/position_sizer.py:27-30`. We snap
  near-zero values to 0 (also caught by our Hypothesis tests).

---

## Phase 1 — Hardening

### `src/risk/gates.py` — pre-trade gates
- Inspired by Octagon's risk-controls posture (no usable code visible), plus
  conservative defaults from `Dotan-Peleh/poly-trader/risk/portfolio.py`.
- 7-gate model is original: drawdown kill, per-position cap, total cap,
  per-family cap, liquidity, cash, min-Kelly.
- Drawdown threshold (10% from 30d peak) and per-position cap (2%) inspired
  by `KaustubhPatange/polymarket-trade-engine`'s `MAX_SESSION_LOSS`.

### `src/risk/invariants.py` — runtime invariants
- Original. Pattern (assertions over production state with hard kill on
  violation) is standard belt-and-suspenders defensive programming.

### Property-based tests (`tests/test_*_properties.py`)
- Hypothesis (https://hypothesis.works/) standard usage. Profiles registered
  per-file. No external repo influence.

---

## Phase 3 — Strategies

### `src/strategies/overround_arb.py` — pure structural arb
- **Edge formula** `yes_ask + no_ask < 1 - fees - safety` is textbook
  no-arbitrage; appears in every binary-options paper.
- **Implementation pattern** mirrors
  `ArDangerUS/-Prediction-Market-Arbitrage-Bot/scanner.py:82-88` (the
  two-cost min approach).
- **"BoneReaper" insight**: `braedonsaunders/homerun` notes that pure
  two-sided buying-at-the-bid (`yes_bid + no_bid > 1`) is the same arb
  expressed from the opposite side. Our scanner now dedupes per ticker so
  we don't double-count.
- **Maker-only execution** preference inspired by
  `Dotan-Peleh/poly-trader`'s smart-money playbook.

### `src/models/digital_option.py` — Black-Scholes digital pricer
- **BSM digital formula** from Black & Scholes 1973 / Merton 1973.
- **Implementation** patterned after
  `Dotan-Peleh/poly-trader/models/digital_option.py:38-58` — uses
  `math.erf` directly, no scipy dependency. Reusable for any time/strike
  binary market (crypto strikes, sports late-game etc.).

### `src/strategies/safe_compounder.py` — NO-side longshot
- **Strategy idea** from
  `ryanfrigo/kalshi-ai-trading-bot/src/strategies/safe_compounder.py`.
  Sells NO on markets where YES is heavily favored (favorite-longshot bias:
  market overprices the unlikely YES win, so NO is cheap).
- **Fee-aware EV** computation reworked to use our `TradeEconomics` so the
  edge is net-of-Kalshi-fees end-to-end. Original had a hand-rolled
  approximation.
- **Calibration logging** original: every NO-sell is logged with the
  predicted "no-win" probability so we can compute Brier after resolution.

### `src/risk/stale_guard.py` — anti-stale-quote sanity
- **Hard profit cap** `MAX_EDGE_FRACTION = 0.05` (anything >5% on a binary
  market is almost certainly bad data) — from
  `JacobJ215/sharpedge/.../arbitrage_scanner.py:39-41` (sports books cap
  6%, we use 5% for binaries which are tighter).
- **Side-implied guardrails** (reject if any side outside [0.02, 0.98])
  from same source, adapted to binary ranges.
- **Freshness scoring** decays linearly over a 60s window — pattern from
  `sharpedge/.../arbitrage_scanner.py:167-171`.

### `src/signals/tfi.py` — trade-flow imbalance
- **Concept** from Silantyev 2019 (`Order flow imbalance and price discovery
  in BitMEX`) and Cont-Kukanov-Stoikov 2014. Empirical evidence on
  prediction markets from
  `DannyChee1/prediction-market-bot/tasks/findings/order_flow_imbalance_research_2026-04-11.md`.
- **TFI z-score with z>2 veto** is the practical-overlay form — used as an
  execution filter, not an alpha source (per the research, OFI alpha alone
  is too weak after fees on Kalshi-depth books).

### `src/signals/obi.py` — top-of-book imbalance
- Standard limit-order-book microstructure signal (Cao-Hansch-Wang 2009).
- Used as a post-only gating signal: if the ask side is N× the bid stack,
  posting a bid is likely to get adversely selected — skip.

### `src/strategies/resolution_decay.py` — near-resolution policy
- **Block-new / widen-spread / take-profit** pattern from
  `zachdaube/kalshi-market-maker` and `ryouol/Trade-backend`. Both reduce
  market-making activity in the final minutes before resolution because
  jump risk dominates fair-value uncertainty.
- Constants (5-min block, 60-min widening, 70% take-profit) tuned to
  Kalshi event-market cadence, not the original sports cadence.

### `src/strategies/avellaneda_stoikov.py` — binary-adapted MM
- **AS formulas** from Avellaneda & Stoikov 2008. Implementation patterned
  after `zachdaube/kalshi-market-maker/src/quotes.py` and
  `ryouol/Trade-backend/research/backtest/avellaneda_stoikov.py`.
- **Boundary widening** (1.5× when |mid-0.5|>0.4) is a binary-specific
  adaptation — standard AS underprices adverse selection near 0/1 where
  intrinsic σ²=P(1-P) collapses.
- **Fee floor** from `zachdaube/kalshi-market-maker/src/quotes.py:112-119`:
  half-spread ≥ maker_fee_per_contract or the round-trip is structurally
  unprofitable.

### `src/risk/strategy_kill.py` — Brier-based auto-disable
- Concept: if a strategy's Brier score on resolved predictions exceeds
  0.25 (worse than always-predict-0.5), kill it until manually re-enabled.
- Threshold from forecasting literature (e.g., Tetlock 2015,
  *Superforecasting*) — 0.25 is the random-baseline Brier on binary events.
- Implementation: SQLite `strategy_state` table next to the calibration
  store; gates check `is_enabled()` before opening any position.

### `src/jobs/daily_report.py` — EOD reconciliation
- Pattern from `JacobJ215/sharpedge/.../daily_report.py` (which logs P&L
  attribution by strategy). Adapted to our `paper_trades` / `paper_positions`
  schema and includes strategy-kill status snapshots.

### `src/backtest/replay.py` — deterministic replay backtester
- Snapshot-iterator pattern from
  `ryouol/Trade-backend/research/backtest/` — feeds the *same* gate /
  executor stack used in live so behavior is bit-identical between
  backtest and production.
- JSONL snapshot format is original; chosen for streaming-friendly
  recording from the live arb runner without holding the full history
  in memory.

  doesn't overcome the fee drag on Kalshi/Polymarket).

---

## Sources NOT used (and why)

- **OctagonAI/kalshi-trading-bot-cli** — vendor lock-in funnel for paid
  Octagon AI API. No tradeable code under MIT.
- **yllvar/Kalshi-Quant-TeleBot** — marketing-heavy README, code does not
  match claims. Risk-management bullet points are aspirational.
- **LLM-driven directional bots** (zostaff/poly-trading-bot,
  Cortex-Trading-Systems, ryanfrigo's `src/agents/` debate stack) — no
  evidence that LLM directional alpha is positive after fees. Excluded
  from v1.
- **Multi-agent debate frameworks** — excluded as cargo-cult complexity for
  a market this thin and binary.

---

## Licenses

This project is MIT-licensed. We do not vendor any GPL/AGPL code. Where we
draw an idea from another MIT/Apache repo, the credit is above and a short
provenance comment lives near the code itself.
