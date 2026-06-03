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

    # Data paths
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "data/bot.db"))
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
        default_factory=lambda: _env("POLYMARKET_LEADERBOARD_URL", "https://lb-api.polymarket.com")
    )
    polymarket_chain_id: int = field(default_factory=lambda: _env_int("POLYMARKET_CHAIN_ID", 137))

    # Optional offline replay: if set, the Polymarket client serves all
    # responses from JSON fixtures under this directory and never hits the
    # network. Used heavily in tests + corp networks that block egress.
    polymarket_replay_dir: Optional[str] = field(
        default_factory=lambda: _env("POLYMARKET_REPLAY_DIR")
    )

    # Smart-money consensus tunables
    polymarket_top_n: int = field(default_factory=lambda: _env_int("POLYMARKET_TOP_N", 10))
    polymarket_consensus_k: int = field(default_factory=lambda: _env_int("POLYMARKET_CONSENSUS_K", 5))
    polymarket_lookback_hours: int = field(default_factory=lambda: _env_int("POLYMARKET_LOOKBACK_HOURS", 24))
    polymarket_min_wallet_trades: int = field(default_factory=lambda: _env_int("POLYMARKET_MIN_WALLET_TRADES", 50))
    polymarket_min_wallet_resolved: int = field(default_factory=lambda: _env_int("POLYMARKET_MIN_WALLET_RESOLVED", 20))
    polymarket_max_slippage_cents: float = field(default_factory=lambda: _env_float("POLYMARKET_MAX_SLIPPAGE_CENTS", 0.02))
    polymarket_gas_usd: float = field(default_factory=lambda: _env_float("POLYMARKET_GAS_USD", 0.05))


settings = Settings()
