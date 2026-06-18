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

Status: next.

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

Status: planned.

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

Status: planned.

Purpose:
- Measure model accuracy by market, not just PnL.

Backend tasks:
- Extend result grading:
  - score-derived markets: 1X2, totals, BTTS, team totals, correct score
  - stats markets when stats feed is available: corners, fouls, cards, shots, SOT
  - player props when lineup/event feed is available
- Add calibration records by:
  - market type
  - league/competition
  - odds bucket
  - confidence bucket
  - AI Guru agreement bucket

Frontend tasks:
- Add calibration dashboard:
  - predicted vs observed
  - Brier score
  - hit rate by confidence bucket
  - EV by market type
  - PnL by market type

Acceptance criteria:
- Settled bets update journal PnL.
- Dashboard shows which markets are profitable or broken.
- Markets with negative paper EV or poor calibration can be auto-blocked.

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

Status: planned.

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

Status: planned.

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

Status: planned.

Purpose:
- Stop the bot from repeating bad behavior.

Rules:
- Block markets with negative calibrated EV.
- Block categories with poor Brier score.
- Block stale odds.
- Block odds below minimum acceptable price.
- Block overexposure to one match/team/market.
- Block player props before confirmed lineup.
- Block books/markets with unreliable settlement or stale pricing.

Acceptance criteria:
- Guardrails run before a bet can be recorded.
- Dashboard explains every block reason.
- User can see market-level allow/block status.

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

1. Build Phase 2: persistent soccer bet journal.
2. Add `RECORD BET` from bettable slips.
3. Add open/settled soccer bets table.
4. Add closing-line snapshot fields.
5. Fix odds provider connectivity or choose a better odds feed.

