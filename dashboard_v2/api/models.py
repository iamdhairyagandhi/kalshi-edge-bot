"""Pydantic response models for the dashboard API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class PortfolioSnapshot(BaseModel):
    starting_bankroll: float
    cash: float
    open_position_cost: float
    realized_pnl: float
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
    side: str
    contracts: int
    avg_price: float
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


class KillSwitch(BaseModel):
    active: bool
    reason: Optional[str]
    triggered_at_unix: Optional[int]


class StreamEvent(BaseModel):
    type: str   # "fill" | "signal" | "equity" | "cohort_update" | "book" | "heartbeat"
    payload: dict
    ts_unix: int
