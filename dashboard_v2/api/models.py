"""Pydantic response models for the dashboard API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class PortfolioSnapshot(BaseModel):
    starting_bankroll: float
    cash: float
    open_position_cost: float
    fees_paid: float = 0.0
    realized_pnl: float
    unrealized_pnl: float = 0.0
    bankroll: float
    n_open_positions: int
    n_open_kalshi: int
    n_open_polymarket: int


class EquityPoint(BaseModel):
    timestamp_unix: int
    equity: float
    venue: Optional[str] = None  # None = "All"


class Position(BaseModel):
    id: int
    venue: str
    ticker: str
    condition_id: Optional[str] = None
    market_title: Optional[str] = None
    market_url: Optional[str] = None
    outcome_index: Optional[int] = None
    position_status: str = "open"
    market_status: str = "unknown"
    side: str
    contracts: int
    avg_price: float
    cost: float
    current_price: Optional[float] = None
    current_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    potential_payout: float
    max_profit: float
    opened_at: str
    closed_at: Optional[str]
    realized_pnl: float


class Fill(BaseModel):
    id: int
    placed_at: str
    venue: str
    strategy: str
    ticker: str
    side: str
    action: str
    contracts: int
    price: float
    fees: float
    cost: float
    is_maker: bool
    notes: Optional[str]


class CohortWallet(BaseModel):
    wallet: str
    rank: int
    score: float
    realized_pnl_usd: float
    n_trades: int
    n_resolved: int
    last_trade_unix: int
    pnl_stability: float
    in_cohort_since_unix: Optional[int] = None


class ConsensusSignalRow(BaseModel):
    idempotency_key: str
    detected_at: str
    condition_id: str
    outcome_token_id: str
    outcome_index: int
    market_question: Optional[str]
    cohort_size: int
    consensus_k: int
    agreeing_wallets: List[str]
    first_trade_unix: int
    last_trade_unix: int
    window_start_unix: int
    window_end_unix: int
    cohort_version: str
    avg_wallet_entry_price: float
    total_wallet_notional_usd: float
    decision: str
    executed_price: Optional[float]
    executed_contracts: Optional[int]
    slippage_cents: Optional[float]
    decision_unix: int
    notes: Optional[str]
    latency_seconds: Optional[int] = None  # decision_unix - first_trade_unix


class LatencyBucket(BaseModel):
    upper_seconds: int
    count: int


class BrierReport(BaseModel):
    strategy: str
    n_resolved: int
    brier_score: float


class StrategyState(BaseModel):
    strategy: str
    enabled: bool
    last_brier: Optional[float] = None
    last_n_samples: Optional[int] = None
    last_evaluated_at: Optional[str] = None
    disabled_reason: Optional[str] = None


class OrderbookLevel(BaseModel):
    price: float
    size: float


class OrderbookSnapshot(BaseModel):
    condition_id: str
    outcome_index: int
    outcome_label: str
    token_id: str
    bids: List[OrderbookLevel]
    asks: List[OrderbookLevel]


class CrossVenueRun(BaseModel):
    run_id: str
    scanned_at_unix: int
    kalshi_markets: int
    kalshi_eligible_markets: int
    kalshi_excluded_mve: int
    polymarket_markets: int
    matched_markets: int
    books_checked: int
    candidates: int
    min_match_score: float
    min_spread: float
    notes: Optional[str]


class CrossVenueSpreadRow(BaseModel):
    id: int
    run_id: str
    scanned_at_unix: int
    kalshi_ticker: str
    kalshi_title: str
    polymarket_condition_id: str
    polymarket_question: str
    polymarket_token_id: str
    polymarket_outcome_index: int
    polymarket_outcome_label: str
    match_score: float
    kalshi_yes_bid: float
    kalshi_yes_ask: float
    polymarket_yes_bid: float
    polymarket_yes_ask: float
    valuation_spread: float
    best_executable_spread: float
    direction: str
    decision: str
    notes: Optional[str]


class CrossVenueSnapshot(BaseModel):
    latest_run: Optional[CrossVenueRun]
    spreads: List[CrossVenueSpreadRow]


class TradeDiagnosticRow(BaseModel):
    id: int
    recorded_unix: int
    strategy: str
    venue: str
    market_id: str
    market_title: Optional[str]
    side: Optional[str]
    decision: str
    reason: str
    metric_name: Optional[str]
    metric_value: Optional[float]
    threshold_value: Optional[float]
    observed_price: Optional[float]
    reference_price: Optional[float]
    details: Optional[str]


class TradeDiagnosticSummary(BaseModel):
    strategy: str
    venue: str
    reason: str
    count: int


class TradeDiagnosticSnapshot(BaseModel):
    summary: List[TradeDiagnosticSummary]
    rows: List[TradeDiagnosticRow]


class WeatherEstimateRow(BaseModel):
    id: int
    run_id: str
    recorded_unix: int
    venue: str
    market_id: str
    title: Optional[str]
    city: Optional[str]
    kind: Optional[str]
    threshold: Optional[float]
    comparator: Optional[str]
    forecast_value: Optional[float]
    sigma: Optional[float]
    p_yes: Optional[float]
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    edge_yes: Optional[float]
    edge_no: Optional[float]
    recommendation: str
    confidence: Optional[float]
    ai_used: bool
    notes: Optional[str]


class WeatherRecommendationSummary(BaseModel):
    recommendation: str
    count: int


class WeatherSnapshot(BaseModel):
    latest_run_id: Optional[str]
    latest_recorded_unix: Optional[int]
    summary: List[WeatherRecommendationSummary]
    rows: List[WeatherEstimateRow]


class KillSwitch(BaseModel):
    active: bool
    reason: Optional[str]
    triggered_at_unix: Optional[int]


class StreamEvent(BaseModel):
    type: str   # "fill" | "signal" | "equity" | "cohort_update" | "book" | "heartbeat"
    payload: dict
    ts_unix: int
