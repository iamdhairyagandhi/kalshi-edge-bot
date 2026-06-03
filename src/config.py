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


settings = Settings()
