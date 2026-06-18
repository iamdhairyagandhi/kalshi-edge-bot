# Kalshi Edge Bot Implementation Plan

This plan prioritizes building a defensible betting workflow: verified prices, measured edge, controlled exposure, and post-bet proof through CLV/calibration. The goal is not to create "sure shots"; it is to stop weak bets, surface real +EV candidates, and make the model auditable.

## Phase 0: Current Baseline

Status: partially complete.

Implemented:
- Kalshi authenticated health check.
- Polymarket public data checks.
- Soccer tab with fixtures, model probabilities, bet builder, scout betslips, AI Guru comments, rating window, export/print.
- Bet365/manual odds paste check on scout slips.
- Bet Qualification Gate on every soccer slip.
- Soccer exposure summary for generated slips.
- Weather tab and weather specialist groundwork.
- Cross-venue Kalshi/Polymarket spread scanner groundwork.

Known blockers:
- The Odds API currently returns non-JSON/503 responses in this environment, so many soccer slips remain `SCOUT`.
- Bet365 direct odds are not available through an official integration.
- No persistent soccer bet journal yet.
- No closing-line value tracking yet.
- No lineup/injury/news feed yet.

## Phase 1: Soccer Bet Qualification Gate

Status: complete.

Purpose:
- Clearly separate model ideas from actual bettable opportunities.

Features:
- Status states: `BETTABLE`, `REVIEW`, `NEEDS PRICE`, `NO BET`.
- Gate checks:
  - real or pasted odds present
  - edge clears threshold
  - odds exceed minimum acceptable price
  - risk level acceptable
  - model trust acceptable
  - lineup/player-prop risk
  - parlay/correlation risk
  - stake exposure within cap
- Plain-English decision strip.
- Match-level exposure summary.

Acceptance criteria:
- A scout slip without odds cannot be marked bettable.
- A pasted Bet365 price below minimum acceptable odds is rejected.
- Bettable stake never exceeds exposure cap.
- User can see exactly why a slip passed or failed.

## Phase 2: Persistent Soccer Bet Journal

Status: complete.

Purpose:
- Track what was actually placed, at what price, and why.

Backend tasks:
- Add `soccer_bets` table.
- Store:
  - bet id
  - created/placed timestamp
  - fixture id(s)
  - match label
  - slip id
  - slip type
  - legs JSON
  - model probability
  - fair odds
  - placed odds
  - stake
  - expected value
  - qualification status at placement
  - source: live odds, pasted Bet365, manual
  - notes
  - status: open, won, lost, pushed, void, cashed out
- Add API routes:
  - `POST /api/soccer/bets`
  - `GET /api/soccer/bets`
  - `PATCH /api/soccer/bets/{id}`

Frontend tasks:
- Add `RECORD BET` button only for `BETTABLE` or user-confirmed `REVIEW`.
- Add Soccer Bet Journal panel.
- Show open, settled, voided, and cashed-out bets.

Acceptance criteria:
- User can record a Bet365/manual bet from a qualified slip.
- Recorded bet persists after dashboard refresh.
- Bet journal can show current open exposure.

## Phase 3: Closing-Line Value Tracking

Status: complete.

Purpose:
- Prove whether the model beats the market over time.

Backend tasks:
- Add closing odds fields:
  - closing odds
  - closing source
  - closing timestamp
  - CLV percent
- Add `snapshot closing line` command/API for soccer bets.
- Store closing odds per leg if available.
- For parlays, store combined closing odds when available or leg-implied approximation.

Frontend tasks:
- Add CLV columns in Bet Journal:
  - placed odds
  - closing odds
  - CLV
  - result
- Add aggregate CLV panel:
  - average CLV
  - CLV by market
  - CLV by bet type
  - CLV by model rating bucket

Acceptance criteria:
- Every recorded bet can have a closing price attached.
- Dashboard shows whether bets are beating closing lines.
- Negative CLV markets can be flagged or blocked.

## Phase 4: Result Grading and Calibration

Status: complete (calibration dashboard + scoring layer landed; stats /
player-prop grading still gated on those feeds being wired up).

Purpose:
- Measure model accuracy by market, not just PnL.

Backend tasks (delivered):
- Score-derived market grading already wired in the resolve loop and
  `resolve_open_bets` (auto-grades match_result, totals, BTTS, team
  totals on `POST /api/soccer/fixtures/{id}/resolve`).
- New calibration aggregator in `src/sports/soccer/data/store.py`
  (`soccer_bets_calibration_summary`, `_calibration_bucket_stats`,
  `_reliability_curve`, `_odds_bucket_for`,
  `_build_calibration_summary`). Filters to settled won/lost bets and
  reports Brier, log-loss, hit rate, average predicted probability,
  model EV per $1, total stake, net PnL, ROI — overall plus bucket
  breakdowns by market, model rating, odds band, slip type, and
  qualification status.
- Reliability curve: 10 predicted-probability deciles with predicted
  mean, observed hit rate and gap per bucket.
- New `GET /api/soccer/bets/calibration-summary` endpoint and an
  embedded `calibration_summary` block on `GET /api/soccer/bets`.

Backend tasks (deferred — feed-gated):
- Stats markets grading (corners, fouls, cards, shots, SOT) — needs the
  stats feed from Phase 5.
- Player-prop grading — needs the lineup/event feed from Phase 7.
- League/competition bucket — fixture metadata exists; can be added when
  bets cover multiple leagues.
- AI Guru agreement bucket — depends on the guru workflow.

Frontend tasks (delivered):
- Calibration dashboard in `BetJournalPanel.tsx`
  (`CalibrationSummaryStrip`, `CalibrationBucketRow`,
  `ReliabilityCurveRow`):
  - Header cells: settled W-L, hit rate, Brier, model EV/$1, ROI.
  - Bucket strips: BY MARKET / BY RATING / BY ODDS BAND / BY SLIP, each
    chip showing observed hit rate vs predicted (or ROI for slip type)
    and tooltip with brier + ROI + sample count.
  - Reliability curve: 10 decile boxes with observed hit rate, sample
    count, and a gap-magnitude colour.
- Types + `api.soccerCalibrationSummary` wrapper in `client.ts`.

Tests:
- `tests/sports/soccer/test_calibration_summary.py` (11 tests) — pure
  helper math (Brier, log-loss, EV per $1, ROI, odds buckets, decile
  grouping) plus store integration.
- `tests/sports/soccer/test_api_routes.py` — three new API tests cover
  the empty case, settled bets producing buckets + reliability points,
  and the embedded calibration summary on the list endpoint.

Acceptance criteria (met):
- Settled bets update journal PnL (already in place).
- Dashboard shows which markets are profitable or broken (BY MARKET +
  BY ODDS BAND chips, reliability curve).
- Markets with negative paper EV / poor calibration can be auto-blocked
  via the Phase 10 `negative_clv_market` rule (now complemented by the
  Phase 4 calibration tracking that lets us reason about why).

## Phase 5: Odds Feed Reliability and Line Shopping

Status: planned.

Purpose:
- Move from scout slips to real price-validated bets.

Tasks:
- Fix current Odds API connectivity issue:
  - verify API key
  - inspect 503 HTML response
  - test from terminal and browser
  - restore SSL verification if possible
- Add provider abstraction:
  - The Odds API
  - OddsJam or other paid odds feed
  - Betfair/Pinnacle where available
  - manual Bet365 paste
- Add line shopping table:
  - book
  - decimal odds
  - implied probability
  - edge vs model
  - best book
  - stale price warning
- Add odds freshness checks.

Acceptance criteria:
- Live odds return JSON reliably.
- Dashboard shows best available price by market.
- Bet qualification uses the actual best price or pasted Bet365 price.
- Stale or missing prices cannot qualify as live bets.

## Phase 6: Bet365 Paste Workflow

Status: complete.

Purpose:
- Make manual Bet365 usage fast and less error-prone.

Tasks:
- Add paste modal for full Bet365 slip text.
- Parse:
  - legs
  - teams
  - markets
  - decimal odds
  - stake, if included
- Match parsed legs to model legs.
- Show mismatches clearly.
- Reprice the pasted slip:
  - fair odds
  - edge
  - correlation tax
  - qualification gate status
- Export/print final reviewed ticket.

Acceptance criteria:
- User can paste a Bet365 slip and get a pass/fail decision.
- The app warns when a pasted leg does not match the model leg.
- The app refuses to qualify unmatched or ambiguous slips.

## Phase 7: Lineups, Injuries, News, and Referee Context

Status: planned.

Purpose:
- Reduce avoidable betting mistakes, especially for player props and cards/fouls.

Data needed:
- Starting XI
- Bench
- injuries
- suspensions
- expected minutes
- referee tendencies
- weather/venue
- travel/rest

Features:
- Lineup status badge:
  - unconfirmed
  - projected
  - confirmed
- Player-prop lock:
  - block before lineup confirmation
  - regrade after lineup confirmation
- News risk badge.
- Referee factor for cards/fouls.

Acceptance criteria:
- Player props cannot be marked `BETTABLE` until lineup status is acceptable.
- Cards/fouls models include referee context when available.
- A lineup change triggers requalification.

## Phase 8: Market Expansion

Status: planned.

Purpose:
- Cover more of the sportsbook board without treating all markets equally.

Markets:
- 1X2
- goal totals
- alternate totals
- BTTS
- team totals
- correct score
- double chance
- draw no bet
- Asian handicap
- corners
- team corners
- cards
- team cards
- fouls committed
- fouls won
- shots
- shots on target
- player anytime scorer
- first/last scorer
- player cards
- keeper saves
- offsides

Tasks:
- For each market:
  - define model probability source
  - define required data feed
  - define grading method
  - define variance/risk rating
  - define block rules
- Add market-specific calibration.
- Add market-specific min edge.

Acceptance criteria:
- Each market has a documented model and grading path.
- Model-only markets are labeled `SCOUT`.
- Markets without grading data cannot affect real PnL claims.

## Phase 9: Smart Bet Builder and Correlation Engine

Status: complete.

Purpose:
- Build parlays intelligently instead of stacking random legs.

Tasks:
- Show:
  - independent probability
  - simulated joint probability
  - correlation factor
  - book implied probability
  - correlation tax
- Add parlay rules:
  - max legs by risk tier
  - no high-variance leg unless large edge
  - no player prop before lineup
  - no same-game parlay without pasted/live combined odds
  - no duplicate exposure to same outcome
- Add "why this parlay could lose" section.

Acceptance criteria:
- Same-game parlays cannot qualify without a real combined book price.
- The app shows whether the book is underpaying correlation.
- Low-probability lottery parlays are blocked or heavily warned.

## Phase 10: Strategy Guardrails and Auto-Block Rules

Status: complete.

Purpose:
- Stop the bot from repeating bad behavior.

Rules implemented in `src/sports/soccer/risk/guardrails.py`:
- `stake_positive` (hard) — non-positive stake blocks.
- `min_decimal_odds` (hard) — odds below configurable floor (default 1.30) block.
- `fixture_exposure_cap` (hard) — current open stake on the same fixture plus
  this bet's stake must stay within `fixture_exposure_cap_usd` (default $100;
  set to 0 to disable).
- `player_prop_lineup` (hard) — any leg in `PLAYER_PROP_LEGS` requires
  `lineup_confirmed=true`.
- `same_game_needs_price` (hard) — multi-leg same-game parlays must include
  the book price (`source in {live,pasted}` plus `book_decimal_odds`).
- `negative_clv_market` (warn) — market+rating buckets with at least
  `min_bets_for_clv_block` samples and average CLV below
  `negative_clv_threshold` are flagged.
- `parlay.*` — failing rules from Phase 9 `parlay_rule_check` (correlation
  tax, low joint probability, max legs, high-variance edge floor) are merged
  in for multi-leg bets; single-leg bets never produce a `parlay.*` check.

Backend wiring (`dashboard_v2/api/routes/soccer.py`):
- `POST /api/soccer/guardrails/preview` — dry-run a slip without recording.
- `POST /api/soccer/bets` — runs guardrails, returns `422` with
  `{ok:false, error:"guardrails_blocked", guardrails: GuardrailReportOut}`
  on any hard fail unless the payload sets `force=true`. The guardrail
  checks are merged into `qualification_checks_json` regardless.
- Helper `soccer_bets_open_stake_by_fixture()` in `data/store.py` powers the
  fixture-exposure rule.

Frontend wiring (`dashboard_v2/web/src/api/client.ts` +
`panels/soccer/BetslipCreatorPanel.tsx`):
- `recordSoccerBetWithGuardrails` catches the 422 and throws
  `SoccerGuardrailsBlockedError` carrying the structured report.
- `RecordBetWidget` renders a red BLOCK strip listing each failing rule,
  exposes a "Confirm starting XI is locked" toggle when the player-prop
  rule fires, and a "FORCE RECORD ANYWAY" checkbox that opts out of the
  block on the next click.

Tests:
- `tests/sports/soccer/test_guardrails.py` (16) — every rule path.
- `tests/sports/soccer/test_api_routes.py` (Phase 10 block at the end) —
  block-without-force, allow-with-force, player-prop lineup unlock, SGP
  price requirement, fixture exposure cap, preview shape on clean and
  dirty slips, audit-trail merge into `qualification_checks_json`.

Acceptance criteria (met):
- Guardrails run before a bet can be recorded.
- Dashboard explains every block reason inline on the betslip card.
- User can see and override (force) block status per bet.

## Phase 11: Kalshi/Polymarket Roadmap

Status: parallel track.

Tasks:
- Continue cross-venue spread scanner.
- Improve market matching.
- Add stale/unresolvable market filters.
- Add live position mark-to-market.
- Add exit logic:
  - take profit
  - stop loss
  - risk-free sell when available
- Add category-level performance tracking.
- Add paper/live mode separation.

Acceptance criteria:
- Positions show current value and potential payout.
- Scanners can run continuously.
- Strategies can be disabled based on negative paper EV.

## Phase 12: Deployment and 24/7 Operations

Status: planned.

Purpose:
- Stop relying on a Mac laptop staying awake.

Tasks:
- Add process manager:
  - systemd
  - Docker Compose
  - or cloud VM
- Add health checks.
- Add logging/alerting.
- Add safe shutdown.
- Add daily report:
  - bets placed
  - bets skipped
  - open exposure
  - CLV
  - PnL
  - model calibration

Acceptance criteria:
- Bot can run unattended.
- Dashboard shows scanner health.
- Failures are visible and actionable.

## Immediate Next Steps

1. Build Phase 8: market expansion + market-specific calibration (extends Phase 4 calibration dashboard with new market types).
2. Fix odds provider connectivity or choose a better odds feed (Phase 5) — needs network/keys.
3. Wire Phase 7 lineup feed once a provider/keys are available (player-prop rule in Phase 9/10 already reads `lineup_confirmed`; Phase 4 player-prop grading deferred until then).
4. Optional follow-up to Phase 10: debounce `/api/soccer/guardrails/preview` behind each slip card so blocks surface before the user clicks RECORD BET (today the UI relies on the 422 round-trip).

