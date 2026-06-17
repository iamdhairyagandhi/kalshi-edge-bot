/* Shared REST + WebSocket client. All endpoints are relative paths
   that vite proxies to the FastAPI backend in dev. */

export type Portfolio = {
  starting_bankroll: number;
  cash: number;
  open_position_cost: number;
  fees_paid: number;
  realized_pnl: number;
  unrealized_pnl: number;
  bankroll: number;
  n_open_positions: number;
  n_open_kalshi: number;
  n_open_polymarket: number;
};

export type Brier = { strategy: string; n_resolved: number; brier_score: number };

export type StrategyState = {
  strategy: string;
  enabled: boolean;
  last_brier: number | null;
  last_n_samples: number | null;
  last_evaluated_at: string | null;
  disabled_reason: string | null;
};

export type BookLevel = { price: number; size: number };
export type Orderbook = {
  condition_id: string;
  outcome_index: number;
  outcome_label: string;
  token_id: string;
  bids: BookLevel[];
  asks: BookLevel[];
};

export type EquityPoint = { timestamp_unix: number; equity: number; venue?: string | null };

export type Position = {
  id: number; venue: string; ticker: string; side: string;
  condition_id: string | null; market_title: string | null; market_url: string | null; outcome_index: number | null;
  position_status: string; market_status: string;
  contracts: number; avg_price: number; cost: number;
  current_price: number | null; current_value: number | null; unrealized_pnl: number | null;
  potential_payout: number; max_profit: number; opened_at: string;
  closed_at: string | null; realized_pnl: number;
};

export type Fill = {
  id: number; placed_at: string; venue: string; strategy: string;
  ticker: string; side: string; action: string; contracts: number;
  price: number; fees: number; cost: number; is_maker: boolean;
  notes: string | null;
};

export type CohortWallet = {
  wallet: string; rank: number; score: number;
  realized_pnl_usd: number; n_trades: number; n_resolved: number;
  last_trade_unix: number; pnl_stability: number;
  in_cohort_since_unix: number | null;
};

export type Signal = {
  idempotency_key: string; detected_at: string;
  condition_id: string; outcome_token_id: string; outcome_index: number;
  market_question: string | null; cohort_size: number; consensus_k: number;
  agreeing_wallets: string[]; first_trade_unix: number;
  last_trade_unix: number; window_start_unix: number; window_end_unix: number;
  cohort_version: string; avg_wallet_entry_price: number;
  total_wallet_notional_usd: number; decision: string;
  executed_price: number | null; executed_contracts: number | null;
  slippage_cents: number | null; decision_unix: number;
  notes: string | null; latency_seconds: number | null;
};

export type LatencyBucket = { upper_seconds: number; count: number };

export type CrossVenueRun = {
  run_id: string;
  scanned_at_unix: number;
  kalshi_markets: number;
  kalshi_eligible_markets: number;
  kalshi_excluded_mve: number;
  polymarket_markets: number;
  matched_markets: number;
  books_checked: number;
  candidates: number;
  min_match_score: number;
  min_spread: number;
  notes: string | null;
};

export type CrossVenueSpread = {
  id: number;
  run_id: string;
  scanned_at_unix: number;
  kalshi_ticker: string;
  kalshi_title: string;
  polymarket_condition_id: string;
  polymarket_question: string;
  polymarket_token_id: string;
  polymarket_outcome_index: number;
  polymarket_outcome_label: string;
  match_score: number;
  kalshi_yes_bid: number;
  kalshi_yes_ask: number;
  polymarket_yes_bid: number;
  polymarket_yes_ask: number;
  valuation_spread: number;
  best_executable_spread: number;
  direction: string;
  decision: string;
  notes: string | null;
};

export type CrossVenueSnapshot = {
  latest_run: CrossVenueRun | null;
  spreads: CrossVenueSpread[];
};

export type TradeDiagnosticRow = {
  id: number;
  recorded_unix: number;
  strategy: string;
  venue: string;
  market_id: string;
  market_title: string | null;
  side: string | null;
  decision: string;
  reason: string;
  metric_name: string | null;
  metric_value: number | null;
  threshold_value: number | null;
  observed_price: number | null;
  reference_price: number | null;
  details: string | null;
};

export type TradeDiagnosticSummary = {
  strategy: string;
  venue: string;
  reason: string;
  count: number;
};

export type TradeDiagnosticSnapshot = {
  summary: TradeDiagnosticSummary[];
  rows: TradeDiagnosticRow[];
};

export type WeatherEstimate = {
  id: number;
  run_id: string;
  recorded_unix: number;
  venue: string;
  market_id: string;
  title: string | null;
  city: string | null;
  kind: string | null;
  threshold: number | null;
  comparator: string | null;
  forecast_value: number | null;
  sigma: number | null;
  p_yes: number | null;
  yes_bid: number | null;
  yes_ask: number | null;
  edge_yes: number | null;
  edge_no: number | null;
  recommendation: string;
  confidence: number | null;
  ai_used: boolean;
  notes: string | null;
};

export type WeatherRecommendationSummary = {
  recommendation: string;
  count: number;
};

export type WeatherSnapshot = {
  latest_run_id: string | null;
  latest_recorded_unix: number | null;
  summary: WeatherRecommendationSummary[];
  rows: WeatherEstimate[];
};

/* ---------- Soccer (FIFA WC bet builder) ---------- */

export type SoccerTeam = {
  team_id: string; name: string; country: string | null;
  elo: number; attack: number; defense: number;
};

export type SoccerFixture = {
  fixture_id: string;
  home_team_id: string; home_team_name: string | null;
  away_team_id: string; away_team_name: string | null;
  kickoff_unix: number;
  competition: string | null;
  neutral_venue: boolean;
  referee_id: string | null;
  home_win: number | null;
  draw: number | null;
  away_win: number | null;
  over_2_5: number | null;
  btts_yes: number | null;
};

export type SoccerTopScorer = { player_id: string; name: string; anytime: number };

export type SoccerMatchSummary = {
  fixture: SoccerFixture;
  score_matrix: number[][];
  outcome: Record<string, number>;
  top_scorer_probs: SoccerTopScorer[];
  cards_distribution: Record<string, number>;
};

export type SoccerBetLeg = {
  kind: string;
  params: Record<string, unknown>;
  book_decimal_odds?: number | null;
  label?: string | null;
};

export type SoccerBetBuilderQuote = {
  fair_probability: number;
  fair_decimal_odds: number;
  n_sims: number;
  leg_probabilities: number[];
  independent_product: number;
  correlation_factor: number;
  book_decimal_odds: number | null;
  edge: number | null;
  kelly_fraction: number | null;
  recommendation: string;
  notes: string | null;
  safety_score: number;
  risk_level: string;
  risk_flags: string[];
  ai_review: string | null;
};

export type SoccerOddsLeg = {
  market_key: string;
  selection: string;
  best_decimal: number | null;
  pinnacle_decimal: number | null;
  book_count: number;
};

export type SoccerOddsForFixture = {
  fixture_id: string;
  legs: SoccerOddsLeg[];
  fetched_at_unix: number;
};

export type SoccerBookPrice = {
  book: string;
  decimal: number;
};

export type SoccerMarketEdge = {
  market_group: string;
  market_key: string;
  selection: string;
  label: string;
  model_probability: number;
  fair_decimal_odds: number;
  best_decimal: number | null;
  best_book: string | null;
  implied_probability: number | null;
  edge: number | null;
  kelly_fraction: number;
  recommendation: string;
  book_count: number;
  prices: SoccerBookPrice[];
  no_vig_market_prob: number | null;
  edge_vs_market: number | null;
};

export type SoccerParlayBlueprint = {
  label: string;
  legs: SoccerBetLeg[];
  fair_probability: number;
  fair_decimal_odds: number;
  correlation_factor: number;
  safety_score: number;
  risk_level: string;
  risk_flags: string[];
};

export type SoccerEdgeBoard = {
  fixture_id: string;
  generated_at_unix: number;
  model_ready: boolean;
  odds_available: boolean;
  expected_home_goals: number | null;
  expected_away_goals: number | null;
  market_edges: SoccerMarketEdge[];
  parlay_blueprints: SoccerParlayBlueprint[];
  notes: string[];
};

export type SoccerBetslipLeg = {
  kind: string;
  label: string;
  params: Record<string, unknown>;
  model_probability: number | null;
  fair_decimal_odds: number | null;
  best_decimal: number | null;
  best_book: string | null;
  edge: number | null;
};

export type SoccerBetslip = {
  slip_id: string;
  fixture_id: string;
  match_label: string;
  kickoff_unix: number;
  slip_type: string;
  title: string;
  legs: SoccerBetslipLeg[];
  fair_probability: number;
  fair_decimal_odds: number;
  book_decimal_odds: number | null;
  minimum_acceptable_decimal: number;
  edge: number | null;
  kelly_fraction: number;
  stake_usd: number;
  safety_score: number;
  risk_level: string;
  risk_flags: string[];
  confidence: number;
  reasons: string[];
  warnings: string[];
  scout_only: boolean;
};

export type SoccerBetslipBatch = {
  generated_at_unix: number;
  bankroll: number;
  max_slips: number;
  total_suggested_stake_usd: number;
  slips: SoccerBetslip[];
  notes: string[];
};

export type SoccerCalibrationRow = {
  market_type: string;
  n_resolved: number;
  brier: number;
  reliability: { bin_lo: number; bin_hi: number; n: number; predicted: number; observed: number }[];
};

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

export const api = {
  portfolio: (venue?: string) => getJSON<Portfolio>(`/api/portfolio${venue ? `?venue=${venue}` : ""}`),
  equity:    (venue?: string) => getJSON<EquityPoint[]>(`/api/equity${venue ? `?venue=${venue}` : ""}`),
  positions: (venue?: string) => getJSON<Position[]>(`/api/positions${venue ? `?venue=${venue}` : ""}`),
  fills:     (venue?: string) => getJSON<Fill[]>(`/api/fills${venue ? `?venue=${venue}` : ""}`),
  signals:   () => getJSON<Signal[]>("/api/signals?limit=200"),
  cohort:    () => getJSON<CohortWallet[]>("/api/cohort"),
  latency:   () => getJSON<LatencyBucket[]>("/api/latency"),
  crossVenue:() => getJSON<CrossVenueSnapshot>("/api/cross-venue?limit=50"),
  diagnostics:() => getJSON<TradeDiagnosticSnapshot>("/api/diagnostics?limit=100"),
  weather:   () => getJSON<WeatherSnapshot>("/api/weather?limit=100"),
  brier:     () => getJSON<Brier[]>("/api/calibration"),
  killswitch:() => getJSON<StrategyState[]>("/api/killswitch"),
  toggleKill:(strategy: string, enabled: boolean, reason?: string) =>
              postJSON<StrategyState>("/api/killswitch", { strategy, enabled, reason }),
  book:      (conditionId: string, outcomeIndex = 0) =>
              getJSON<Orderbook>(`/api/markets/polymarket/${conditionId}/book?outcome_index=${outcomeIndex}`),
  health:    () => getJSON<{ ok: boolean }>("/api/health"),
  // Soccer
  soccerSeedDemo: () => postJSON<{ ok: boolean }>("/api/soccer/seed-demo", {}),
  soccerFitStatsBomb: (body?: {
                competitions?: { competition_id: number; season_id: number; neutral?: boolean }[];
                decay_per_day?: number;
              }) => postJSON<{
                ok: boolean;
                fit_source: string;
                competitions: string[];
                matches_used: number;
                teams: number;
                decay_per_day: number;
              }>("/api/soccer/fit-statsbomb", body || {}),
  soccerIngestOddsFixtures: () => postJSON<{
                ok: boolean;
                sport_key: string;
                events_seen: number;
                fixtures_added: number;
                unmatched: number;
                odds_api_key_configured: boolean;
              }>("/api/soccer/ingest-odds-fixtures", {}),
  soccerTeams:    () => getJSON<SoccerTeam[]>("/api/soccer/teams"),
  soccerFixtures: () => getJSON<SoccerFixture[]>("/api/soccer/fixtures"),
  soccerMatch:    (fixtureId: string, nSims = 5000) =>
              getJSON<SoccerMatchSummary>(`/api/soccer/match/${encodeURIComponent(fixtureId)}?n_sims=${nSims}`),
  soccerBetBuilder: (payload: {
                fixture_id: string;
                legs: SoccerBetLeg[];
                book_decimal_odds?: number | null;
                n_sims?: number;
                persist?: boolean;
              }) => postJSON<SoccerBetBuilderQuote>("/api/soccer/bet-builder", payload),
  soccerOdds:     (fixtureId: string, legs?: string) =>
              getJSON<SoccerOddsForFixture>(
                `/api/soccer/odds/${encodeURIComponent(fixtureId)}${legs ? `?legs=${encodeURIComponent(legs)}` : ""}`,
              ),
  soccerEdgeBoard:(fixtureId: string, nSims = 8000) =>
              getJSON<SoccerEdgeBoard>(
                `/api/soccer/edge-board/${encodeURIComponent(fixtureId)}?n_sims=${nSims}`,
              ),
  soccerBetslips:(maxSlips = 12, bankroll?: number, minEdge?: number, horizonHours?: number) => {
              const qs = new URLSearchParams({ max_slips: String(maxSlips) });
              if (bankroll != null) qs.set("bankroll", String(bankroll));
              if (minEdge != null) qs.set("min_edge", String(minEdge));
              if (horizonHours != null) qs.set("horizon_hours", String(Math.max(1, Math.round(horizonHours))));
              return getJSON<SoccerBetslipBatch>(`/api/soccer/betslips?${qs.toString()}`);
            },
  soccerCalibration: () => getJSON<SoccerCalibrationRow[]>("/api/soccer/calibration"),
};

/* ----- WebSocket with auto-reconnect ----- */

export type StreamEvent =
  | { type: "fill"; payload: Fill; ts_unix: number }
  | { type: "signal"; payload: Signal; ts_unix: number }
  | { type: "heartbeat"; ts_unix: number };

export class StreamClient {
  private ws: WebSocket | null = null;
  private retry = 0;
  private dead = false;
  private watchdog: number | null = null;
  constructor(private onEvent: (e: StreamEvent) => void, private onStatus: (alive: boolean) => void) {}
  connect() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.markAlive();
    };
    ws.onmessage = (m) => {
      this.markAlive();
      try { this.onEvent(JSON.parse(m.data)); } catch {}
    };
    ws.onclose = () => {
      if (this.dead) return;
      this.clearWatchdog();
      this.onStatus(false);
      this.retry = Math.min(this.retry + 1, 6);
      setTimeout(() => this.connect(), 500 * 2 ** this.retry);
    };
    ws.onerror = () => ws.close();
  }
  close() {
    this.dead = true;
    this.clearWatchdog();
    this.ws?.close();
  }
  private markAlive() {
    if (this.dead) return;
    this.onStatus(true);
    this.clearWatchdog();
    this.watchdog = window.setTimeout(() => {
      if (!this.dead) this.onStatus(false);
    }, 35000);
  }
  private clearWatchdog() {
    if (this.watchdog != null) {
      window.clearTimeout(this.watchdog);
      this.watchdog = null;
    }
  }
}
