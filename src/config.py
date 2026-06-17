"""
Configuration. All secrets come from environment variables / .env.
Never hard-code keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(key, default)
    return v if v not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    v = _env(key)
    return float(v) if v is not None else default


def _env_int(key: str, default: int) -> int:
    v = _env(key)
    return int(v) if v is not None else default


def _env_bool(key: str, default: bool) -> bool:
    v = _env(key)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Kalshi API
    kalshi_api_key: Optional[str] = field(default_factory=lambda: _env("KALSHI_API_KEY"))
    kalshi_private_key_path: str = field(
        default_factory=lambda: _env("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
    )
    kalshi_base_url: str = field(
        default_factory=lambda: _env("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
    )

    # Trading mode
    paper_trading: bool = field(default_factory=lambda: _env_bool("PAPER_TRADING", True))
    starting_bankroll: float = field(default_factory=lambda: _env_float("STARTING_BANKROLL", 1000.0))

    # Runner loop
    scan_interval_seconds: int = field(default_factory=lambda: _env_int("SCAN_INTERVAL_SECONDS", 30))
    max_markets_per_scan: int = field(default_factory=lambda: _env_int("MAX_MARKETS_PER_SCAN", 200))
    kalshi_arb_min_net_edge: float = field(default_factory=lambda: _env_float("KALSHI_ARB_MIN_NET_EDGE", 0.02))
    kalshi_arb_safety_margin: float = field(default_factory=lambda: _env_float("KALSHI_ARB_SAFETY_MARGIN", 0.005))

    # Data paths
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "data/bot.db"))
    calibration_db_path: str = field(default_factory=lambda: _env("CALIBRATION_DB_PATH", "data/calibration.db"))
    log_dir: str = field(default_factory=lambda: _env("LOG_DIR", "logs"))

    # ------------------------------------------------------------------
    # Polymarket (read-only in v1; live execution deferred to a later phase)
    # ------------------------------------------------------------------
    polymarket_gamma_url: str = field(
        default_factory=lambda: _env("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
    )
    polymarket_data_url: str = field(
        default_factory=lambda: _env("POLYMARKET_DATA_URL", "https://data-api.polymarket.com")
    )
    polymarket_clob_url: str = field(
        default_factory=lambda: _env("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
    )
    polymarket_leaderboard_url: str = field(
        default_factory=lambda: _env("POLYMARKET_LEADERBOARD_URL", "https://data-api.polymarket.com")
    )
    polymarket_chain_id: int = field(default_factory=lambda: _env_int("POLYMARKET_CHAIN_ID", 137))

    # Optional offline replay: if set, the Polymarket client serves all
    # responses from JSON fixtures under this directory and never hits the
    # network. Used heavily in tests + corp networks that block egress.
    polymarket_replay_dir: Optional[str] = field(
        default_factory=lambda: _env("POLYMARKET_REPLAY_DIR")
    )

    # Smart-money consensus tunables
    polymarket_top_n: int = field(default_factory=lambda: _env_int("POLYMARKET_TOP_N", 75))
    polymarket_consensus_k: int = field(default_factory=lambda: _env_int("POLYMARKET_CONSENSUS_K", 8))
    polymarket_probe_k: int = field(default_factory=lambda: _env_int("POLYMARKET_PROBE_K", 3))
    polymarket_probe_notional_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_PROBE_NOTIONAL_USD", 2.0))
    polymarket_probe_max_slippage_cents: float = field(default_factory=lambda: _env_float("POLYMARKET_PROBE_MAX_SLIPPAGE_CENTS", 0.015))
    polymarket_pullback_entry_cents: float = field(default_factory=lambda: _env_float("POLYMARKET_PULLBACK_ENTRY_CENTS", 0.02))
    polymarket_pullback_expiry_minutes: int = field(default_factory=lambda: _env_int("POLYMARKET_PULLBACK_EXPIRY_MINUTES", 10))
    polymarket_lookback_hours: int = field(default_factory=lambda: _env_int("POLYMARKET_LOOKBACK_HOURS", 24))
    polymarket_min_wallet_trades: int = field(default_factory=lambda: _env_int("POLYMARKET_MIN_WALLET_TRADES", 20))
    polymarket_min_wallet_resolved: int = field(default_factory=lambda: _env_int("POLYMARKET_MIN_WALLET_RESOLVED", 5))
    polymarket_max_slippage_cents: float = field(default_factory=lambda: _env_float("POLYMARKET_MAX_SLIPPAGE_CENTS", 0.02))
    polymarket_gas_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_GAS_USD", 0.05))
    polymarket_fresh_signal_minutes: int = field(default_factory=lambda: _env_int("POLYMARKET_FRESH_SIGNAL_MINUTES", 15))
    polymarket_momentum_lookback_minutes: int = field(default_factory=lambda: _env_int("POLYMARKET_MOMENTUM_LOOKBACK_MINUTES", 60))
    polymarket_momentum_min_buy_trades: int = field(default_factory=lambda: _env_int("POLYMARKET_MOMENTUM_MIN_BUY_TRADES", 3))
    polymarket_momentum_min_wallets: int = field(default_factory=lambda: _env_int("POLYMARKET_MOMENTUM_MIN_WALLETS", 2))
    polymarket_momentum_min_notional_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_MOMENTUM_MIN_NOTIONAL_USD", 500.0))
    polymarket_momentum_min_price_move: float = field(default_factory=lambda: _env_float("POLYMARKET_MOMENTUM_MIN_PRICE_MOVE", 0.03))
    polymarket_momentum_max_entry_premium: float = field(default_factory=lambda: _env_float("POLYMARKET_MOMENTUM_MAX_ENTRY_PREMIUM", 0.03))
    polymarket_momentum_execute: bool = field(default_factory=lambda: _env_bool("POLYMARKET_MOMENTUM_EXECUTE", False))
    polymarket_momentum_min_resolved_to_execute: int = field(default_factory=lambda: _env_int("POLYMARKET_MOMENTUM_MIN_RESOLVED_TO_EXECUTE", 30))
    polymarket_momentum_min_realized_pnl_to_execute: float = field(default_factory=lambda: _env_float("POLYMARKET_MOMENTUM_MIN_REALIZED_PNL_TO_EXECUTE", 0.0))
    polymarket_momentum_exclude_terms: str = field(default_factory=lambda: _env("POLYMARKET_MOMENTUM_EXCLUDE_TERMS", "vs., v ,spread:,o/u,over/under,ufc,nba,nfl,mlb,nhl,tennis,atp,wta,itf,soccer,football,basketball,baseball,hockey"))
    polymarket_exit_take_profit_cents: float = field(default_factory=lambda: _env_float("POLYMARKET_EXIT_TAKE_PROFIT_CENTS", 0.15))
    polymarket_exit_profit_capture: float = field(default_factory=lambda: _env_float("POLYMARKET_EXIT_PROFIT_CAPTURE", 0.45))
    polymarket_exit_stop_loss_cents: float = field(default_factory=lambda: _env_float("POLYMARKET_EXIT_STOP_LOSS_CENTS", 0.12))
    polymarket_exit_stop_loss_fraction: float = field(default_factory=lambda: _env_float("POLYMARKET_EXIT_STOP_LOSS_FRACTION", 0.50))
    polymarket_exit_min_bid_size: float = field(default_factory=lambda: _env_float("POLYMARKET_EXIT_MIN_BID_SIZE", 1.0))

    # Cross-venue Kalshi/Polymarket spread scanner
    cross_venue_min_match_score: float = field(default_factory=lambda: _env_float("CROSS_VENUE_MIN_MATCH_SCORE", 0.72))
    cross_venue_min_spread: float = field(default_factory=lambda: _env_float("CROSS_VENUE_MIN_SPREAD", 0.03))

    # Weather specialist
    weather_open_meteo_enabled: bool = field(default_factory=lambda: _env_bool("WEATHER_OPEN_METEO_ENABLED", True))
    weather_ai_enabled: bool = field(default_factory=lambda: _env_bool("WEATHER_AI_ENABLED", False))
    weather_ai_model: str = field(default_factory=lambda: _env("WEATHER_AI_MODEL", "gpt-5-mini"))
    weather_ai_max_markets: int = field(default_factory=lambda: _env_int("WEATHER_AI_MAX_MARKETS", 5))
    openai_api_key: Optional[str] = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    weather_min_edge: float = field(default_factory=lambda: _env_float("WEATHER_MIN_EDGE", 0.08))
    weather_safety_buffer: float = field(default_factory=lambda: _env_float("WEATHER_SAFETY_BUFFER", 0.03))
    weather_min_confidence: float = field(default_factory=lambda: _env_float("WEATHER_MIN_CONFIDENCE", 0.70))


settings = Settings()
