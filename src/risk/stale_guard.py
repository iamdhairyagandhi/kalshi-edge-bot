"""
Stale-quote sanity gate.

A guaranteed-profit arbitrage above ~5% on a binary market is almost
always a data artifact (one leg priced off the wrong side of the book,
stale quote, halted market, etc.). Real arbs on Kalshi are tiny —
0.5%-3% net is typical when they exist at all.

We layer multiple cheap checks before trusting any arb opportunity:

    1. Hard profit-fraction cap (above this = data lying to us)
    2. Side-price guardrails (no leg too close to 0 or 1)
    3. Quote freshness (decays linearly over a window)
    4. Optional last-trade-time vs now

Provenance:
- Profit-cap idea: JacobJ215/sharpedge/apps/bot/.../arbitrage_scanner.py:39-41
  (sportsbook arbs capped at 6%; we use 5% for binaries)
- Side-price guardrails: same repo, lines 94-109
- Freshness decay: same repo, lines 167-171
- Architecture: original — apply as a "do we even trust this opp?" gate
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List


# Above this fraction of profit on a $1 binary arb, the quote is almost
# certainly wrong. Real Kalshi arbs are < 3% net; 5% is a generous cap.
MAX_ARB_PROFIT_FRACTION = 0.05

# Any leg priced this close to the boundary is suspect (or settling out)
MIN_LEG_PRICE = 0.02
MAX_LEG_PRICE = 0.98

# Freshness window: quotes older than this are scored 0
FRESHNESS_WINDOW_SECONDS = 60.0

# Below this freshness score, reject the opportunity outright
MIN_FRESHNESS_SCORE = 0.30


@dataclass
class StaleGuardResult:
    accepted: bool
    rejections: List[str]
    freshness_score: float


def freshness_score(observed_at_epoch: float, now: float | None = None) -> float:
    """Linearly decays from 1.0 (fresh) to 0.0 over FRESHNESS_WINDOW_SECONDS."""
    if now is None:
        now = time.time()
    age = max(0.0, now - observed_at_epoch)
    if age >= FRESHNESS_WINDOW_SECONDS:
        return 0.0
    return 1.0 - (age / FRESHNESS_WINDOW_SECONDS)


def check_arb_sanity(
    *,
    yes_price: float,
    no_price: float,
    profit_per_contract: float,
    observed_at_epoch: float,
    now: float | None = None,
) -> StaleGuardResult:
    """
    Reject an arbitrage opportunity if any data-quality signal is bad.

    `profit_per_contract` is the EXPECTED profit per $1 contract pair
    (e.g. 0.02 = 2 cents per pair).
    """
    rejections: List[str] = []

    if profit_per_contract > MAX_ARB_PROFIT_FRACTION:
        rejections.append(
            f"profit {profit_per_contract:.3f} > {MAX_ARB_PROFIT_FRACTION:.3f} "
            "(too-good-to-be-true; likely stale)"
        )

    for name, p in (("yes", yes_price), ("no", no_price)):
        if p < MIN_LEG_PRICE:
            rejections.append(f"{name} leg {p:.3f} < {MIN_LEG_PRICE} (extreme)")
        if p > MAX_LEG_PRICE:
            rejections.append(f"{name} leg {p:.3f} > {MAX_LEG_PRICE} (extreme)")

    fresh = freshness_score(observed_at_epoch, now=now)
    if fresh < MIN_FRESHNESS_SCORE:
        rejections.append(f"freshness {fresh:.2f} < {MIN_FRESHNESS_SCORE} (stale)")

    return StaleGuardResult(
        accepted=not rejections,
        rejections=rejections,
        freshness_score=fresh,
    )
