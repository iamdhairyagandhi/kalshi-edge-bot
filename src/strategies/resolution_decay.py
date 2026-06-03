"""
Resolution-day decay strategy.

As a binary market approaches resolution, several things happen:
    - Implied volatility collapses (less time for moves)
    - Spreads widen (market makers pull liquidity to avoid pin risk)
    - Adverse selection grows (anyone trading late may know something)
    - Fee/spread drag becomes a larger % of remaining EV

Our policy at resolution-time proximity:
    1. STOP opening new positions inside `NEW_TRADE_BLOCK_MINUTES`
       (default 5 min). Pin risk and information asymmetry kill EV.
    2. EXIT existing positions if extracted edge has been captured —
       i.e. if mid has moved in our favor by >= TAKE_PROFIT_FRACTION of
       our entry edge, take the win rather than waiting for settlement.
    3. WIDEN any quoting (MM strategies) by RESOLUTION_SPREAD_MULTIPLIER
       inside `WIDEN_WINDOW_MINUTES`.

Provenance: practitioner notes from tyschacht/nanoclaw_passive and
45ck/llm-quant/docs/research/polymarket-replay-backtest-framework.md
("stale threshold tightens and spread multiplier increases near
resolution"). Implementation original.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


NEW_TRADE_BLOCK_MINUTES = 5.0
WIDEN_WINDOW_MINUTES = 60.0
RESOLUTION_SPREAD_MULTIPLIER = 2.0
TAKE_PROFIT_FRACTION = 0.70   # take profit if 70% of expected edge realized


@dataclass
class ResolutionDecision:
    block_new_open: bool
    widen_spread_multiplier: float
    take_profit_threshold: float
    minutes_to_resolution: float


def _parse_close(close_iso: str) -> Optional[datetime]:
    """Tolerant parse of Kalshi `close_time` ISO timestamps."""
    if not close_iso:
        return None
    try:
        # Handle 'Z' suffix
        s = close_iso.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def evaluate(close_time_iso: str, now: Optional[datetime] = None) -> ResolutionDecision:
    """
    Decide trading policy for a market based on time-to-resolution.

    `close_time_iso` is the Kalshi market.close_time (UTC).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    close = _parse_close(close_time_iso)
    if close is None:
        # Unknown close time = treat as far away (conservative: no decay).
        return ResolutionDecision(
            block_new_open=False,
            widen_spread_multiplier=1.0,
            take_profit_threshold=TAKE_PROFIT_FRACTION,
            minutes_to_resolution=float("inf"),
        )

    delta = (close - now).total_seconds() / 60.0

    block = delta <= NEW_TRADE_BLOCK_MINUTES
    if delta <= WIDEN_WINDOW_MINUTES:
        # Linearly ramp from 1.0 → RESOLUTION_SPREAD_MULTIPLIER as we approach
        ratio = max(0.0, 1.0 - (delta / WIDEN_WINDOW_MINUTES))
        multiplier = 1.0 + (RESOLUTION_SPREAD_MULTIPLIER - 1.0) * ratio
    else:
        multiplier = 1.0

    return ResolutionDecision(
        block_new_open=block,
        widen_spread_multiplier=multiplier,
        take_profit_threshold=TAKE_PROFIT_FRACTION,
        minutes_to_resolution=delta,
    )


def should_take_profit(
    entry_price: float,
    current_mid: float,
    side: str,                      # "YES" or "NO"
    expected_edge: float,           # at entry, in dollars per contract
    realized_fraction: float = TAKE_PROFIT_FRACTION,
) -> bool:
    """
    True if our position's mid has moved enough in our favor that the
    remaining EV no longer justifies pin/adverse-selection risk.
    """
    if expected_edge <= 0:
        return False
    if side.upper() == "YES":
        gained = current_mid - entry_price
    else:
        gained = entry_price - current_mid   # NO: profit when mid drops
    return gained >= expected_edge * realized_fraction
