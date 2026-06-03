"""
Black-Scholes digital option pricer.

Use case on Kalshi: markets like "Will BTC be above $95,000 on Apr 26?"
are digital options on the underlying spot. If we have a live spot price
and a reasonable volatility estimate, we can compute a theoretical YES
probability and compare it to the market price.

Formula (per Black-Scholes / Merton 1973 for binary-cash-or-nothing):

    P(S_T > K)  =  Φ(d2)
    where:
        d2 = (ln(S_t / K) + (μ - σ²/2) * τ) / (σ * sqrt(τ))
        τ  = time to expiry in years (or any consistent unit)
        σ  = volatility per same time unit

For zero-drift (μ = 0, typical short-horizon assumption for crypto):

    d2 = (ln(S_t / K) - σ²·τ/2) / (σ · sqrt(τ))

Implementation notes:
- We use `math.erf` so there is no scipy dependency. The standard normal
  CDF is `Φ(x) = 0.5 * (1 + erf(x / sqrt(2)))`.
- All inputs must be in consistent time units. Common practice: σ measured
  per minute, τ in minutes.

Provenance: formula is classical (BSM 1973). Implementation pattern adapted
from Dotan-Peleh/poly-trader/models/digital_option.py (MIT).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


SQRT_2 = math.sqrt(2.0)


def standard_normal_cdf(x: float) -> float:
    """Standard normal CDF via erf. No scipy required."""
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


@dataclass
class DigitalOptionQuote:
    spot_price: float
    strike: float
    time_to_expiry: float       # in units consistent with sigma
    sigma: float                # volatility per same unit
    drift: float = 0.0
    implied_yes_prob: Optional[float] = None


@dataclass
class DigitalOptionResult:
    p_yes: float
    edge: Optional[float]
    d2: float


def price_digital(q: DigitalOptionQuote) -> DigitalOptionResult:
    """Compute P(S_T > K) under lognormal BSM dynamics."""
    if q.spot_price <= 0 or q.strike <= 0:
        raise ValueError("spot and strike must be positive")
    if q.time_to_expiry <= 0:
        p = 1.0 if q.spot_price > q.strike else 0.0
        edge = None if q.implied_yes_prob is None else p - q.implied_yes_prob
        return DigitalOptionResult(p_yes=p, edge=edge,
                                    d2=float("inf") if p else float("-inf"))
    if q.sigma <= 0:
        p = 1.0 if q.spot_price > q.strike else 0.0
        edge = None if q.implied_yes_prob is None else p - q.implied_yes_prob
        return DigitalOptionResult(p_yes=p, edge=edge,
                                    d2=float("inf") if p else float("-inf"))

    log_moneyness = math.log(q.spot_price / q.strike)
    d2 = (log_moneyness + (q.drift - 0.5 * q.sigma * q.sigma) * q.time_to_expiry) / (
        q.sigma * math.sqrt(q.time_to_expiry)
    )
    p_yes = standard_normal_cdf(d2)
    edge = None if q.implied_yes_prob is None else p_yes - q.implied_yes_prob
    return DigitalOptionResult(p_yes=p_yes, edge=edge, d2=d2)


def realized_volatility(prices: list[float]) -> float:
    """Realized per-step stdev of log returns from a uniformly-spaced series."""
    if len(prices) < 2:
        return 0.0
    log_returns: list[float] = []
    for prev, curr in zip(prices, prices[1:]):
        if prev <= 0 or curr <= 0:
            continue
        log_returns.append(math.log(curr / prev))
    n = len(log_returns)
    if n < 2:
        return 0.0
    mean = sum(log_returns) / n
    var = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    return math.sqrt(var)
