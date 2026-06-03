"""
Avellaneda-Stoikov market making, binary-adapted.

Original paper: Avellaneda & Stoikov (2008), "High-frequency trading
in a limit order book", Quantitative Finance 8(3), 217-224.

Standard AS:
    reservation_price r = s − q · γ · σ² · (T − t)
    optimal half-spread = γσ²(T-t)/2 + (1/γ)·ln(1 + γ/κ)

Binary adaptations:
    1. Hard-clip quotes to [0.01, 0.99] — binary contracts can't trade at $0 or $1
    2. Boundary widening: when |mid - 0.50| > 0.40, multiply spread by 1.5
       (intrinsic σ² = P*(1-P) collapses near boundaries; standard AS
       underprices the adverse-selection risk there)
    3. Fee floor: min_spread >= 2 * maker_fee_per_contract / contracts
       (otherwise round-trip is unprofitable even with no inventory risk)
    4. Snap to cent grid: bid/ask quantized to Kalshi's whole-cent ticks

Use case: continuous quoting on liquid Kalshi markets (BTC strikes, daily
political). NOT suitable for sparse or near-resolution markets.

Provenance:
- AS formulas: Avellaneda-Stoikov 2008
- Implementation pattern: zachdaube/kalshi-market-maker/src/quotes.py and
  ryouol/Trade-backend/research/backtest/avellaneda_stoikov.py
- Boundary widening: ryouol/Trade-backend (1.5x at |p - 0.5| > 0.40)
- Fee floor: zachdaube/kalshi-market-maker/src/quotes.py:112-119
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from src.utils.fees import MAKER_FEE_COEFFICIENT


@dataclass
class ASParams:
    mid: float                  # current mid price (in dollars 0-1)
    inventory: int              # current position in contracts (+long YES, -short)
    gamma: float = 0.1          # risk aversion
    sigma: float = 0.05         # volatility (per unit time, same as time_to_resolution)
    time_to_resolution: float = 1.0
    kappa: float = 100.0        # order arrival decay
    boundary_widen: bool = True
    contracts_per_quote: int = 10


@dataclass
class ASQuote:
    bid_price: float            # in dollars
    ask_price: float
    bid_cents: int              # quantized for Kalshi orders
    ask_cents: int
    half_spread: float
    reservation_price: float
    widened: bool
    fee_floor_applied: bool


def reservation_price(p: ASParams) -> float:
    """r = s − q·γ·σ²·(T−t)"""
    return p.mid - p.inventory * p.gamma * (p.sigma ** 2) * p.time_to_resolution


def optimal_half_spread(p: ASParams) -> float:
    """δ* / 2 = γσ²(T-t)/2 + (1/γ)·ln(1+γ/κ)"""
    inventory_risk = 0.5 * p.gamma * (p.sigma ** 2) * p.time_to_resolution
    order_flow = (1.0 / p.gamma) * math.log1p(p.gamma / p.kappa)
    return inventory_risk + order_flow


def fee_floor_half_spread(mid: float) -> float:
    """Minimum half-spread to break even on round-trip maker fees.

    A round-trip = open + close, each costs maker_fee = 0.0175 * mid * (1-mid).
    So total fee per contract round-trip = 2 * 0.0175 * mid * (1-mid).
    The mid-to-quote distance must cover *half* of that (we earn the
    spread on both sides).
    """
    fee_per_contract = MAKER_FEE_COEFFICIENT * mid * (1.0 - mid)
    return fee_per_contract  # half-spread covers one side, both legs each pay half


def quote(p: ASParams) -> ASQuote:
    """Compute binary-adapted AS quotes."""
    r = reservation_price(p)
    delta = optimal_half_spread(p)

    # Boundary widening
    widened = False
    if p.boundary_widen and abs(p.mid - 0.5) > 0.4:
        delta *= 1.5
        widened = True

    # Fee floor: ensure we cover round-trip fees
    fee_floor = fee_floor_half_spread(p.mid)
    fee_floor_applied = False
    if delta < fee_floor:
        delta = fee_floor
        fee_floor_applied = True

    bid = r - delta
    ask = r + delta

    # Clip to legal Kalshi range [0.01, 0.99]
    bid = max(0.01, min(0.99, bid))
    ask = max(0.01, min(0.99, ask))

    # Quantize to whole cents
    bid_cents = int(round(bid * 100))
    ask_cents = int(round(ask * 100))

    # Ensure bid < ask after quantization (could collide on very thin spreads
    # or after clipping both sides to 0.01 or 0.99).
    if ask_cents <= bid_cents:
        if bid_cents >= 99:
            bid_cents = 98
            ask_cents = 99
        elif ask_cents <= 1:
            bid_cents = 1
            ask_cents = 2
        else:
            ask_cents = bid_cents + 1

    return ASQuote(
        bid_price=bid_cents / 100.0,
        ask_price=ask_cents / 100.0,
        bid_cents=bid_cents,
        ask_cents=ask_cents,
        half_spread=delta,
        reservation_price=r,
        widened=widened,
        fee_floor_applied=fee_floor_applied,
    )
