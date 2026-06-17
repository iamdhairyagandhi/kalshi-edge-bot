"""
Edge calculation + Kelly staking.

All numbers are in decimal-odds convention:
  decimal_odds = 1 + payout_per_unit_stake
  implied_prob = 1 / decimal_odds
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence


def decimal_to_implied_prob(d: float) -> float:
    if d <= 1.0:
        return 1.0
    return 1.0 / d


def implied_prob_to_decimal(p: float) -> float:
    if p <= 0.0:
        return float("inf")
    return 1.0 / p


def fair_decimal_from_prob(p: float) -> float:
    return implied_prob_to_decimal(p)


def overround(decimal_odds: Iterable[float]) -> float:
    """Sum of implied probabilities across mutually exclusive outcomes - 1.

    e.g. 1X2 with overround 0.05 means books are charging a 5% margin.
    """
    return sum(decimal_to_implied_prob(d) for d in decimal_odds) - 1.0


def no_vig_probs(decimal_odds: Sequence[float]) -> List[float]:
    """De-vig a mutually-exclusive market.

    Given decimal odds for every selection in a market (e.g. [H_odds,
    D_odds, A_odds] for 1X2, or [over_odds, under_odds] for a total),
    returns the proportional no-vig fair probabilities that sum to 1.

    Uses the multiplicative (proportional) method, which is the standard
    pro approach for 2- and 3-way markets where the vig is approximately
    symmetric across outcomes. For asymmetric vig (heavy favourites)
    Shin's method is more accurate, but proportional is the workhorse.
    """
    valid = [d for d in decimal_odds if d and d > 1.0]
    if len(valid) < 2:
        return []
    raw = [1.0 / d for d in valid]
    s = sum(raw)
    if s <= 0:
        return []
    return [p / s for p in raw]


def no_vig_prob(
    target_decimal: float,
    other_decimals: Sequence[float],
) -> Optional[float]:
    """No-vig probability of one selection given the other selections in
    the same market. Returns None if the market is malformed."""
    if target_decimal is None or target_decimal <= 1.0:
        return None
    probs = no_vig_probs([target_decimal, *other_decimals])
    if not probs:
        return None
    return probs[0]


def edge_decimal(p_true: float, book_decimal: float) -> float:
    """Expected return per unit stake at the given decimal odds.

    EV = p · (book - 1) - (1 - p) = p · book - 1
    """
    return p_true * book_decimal - 1.0


def fractional_kelly(
    p_true: float,
    book_decimal: float,
    *,
    fraction: float = 0.5,
    cap: float = 0.05,
) -> float:
    """Fractional Kelly stake fraction of bankroll.

    Full Kelly: f* = (b·p - q) / b where b = book - 1, q = 1 - p.
    Returns max(0, fraction · f*) capped at `cap`.
    """
    if book_decimal <= 1.0 or not (0.0 < p_true < 1.0):
        return 0.0
    b = book_decimal - 1.0
    q = 1.0 - p_true
    f_full = (b * p_true - q) / b
    if f_full <= 0:
        return 0.0
    return min(fraction * f_full, cap)


@dataclass
class EdgeReport:
    fair_probability: float
    fair_decimal_odds: float
    book_decimal_odds: Optional[float]
    edge: Optional[float]
    kelly_fraction: float
    recommendation: str
    notes: Optional[str] = None
    # New: market-consensus de-vigged probability of the same selection,
    # derived from the full set of decimal odds on the same market across
    # its mutually-exclusive outcomes (e.g., H/D/A for 1X2).
    no_vig_market_prob: Optional[float] = None
    # Model probability minus market consensus. Positive = model thinks
    # the market is wrong in our favour. Pros require this to be positive
    # AND meaningfully above zero before betting, not just `edge > 0`.
    edge_vs_market: Optional[float] = None


def report_edge(
    fair_probability: float,
    book_decimal_odds: Optional[float],
    *,
    min_edge: float = 0.02,
    kelly_fraction: float = 0.5,
    kelly_cap: float = 0.05,
    market_decimal_odds: Optional[Sequence[float]] = None,
) -> EdgeReport:
    """Report the edge of `fair_probability` vs `book_decimal_odds`.

    If `market_decimal_odds` is provided (the decimal odds of every
    mutually-exclusive outcome on the same market, including the target
    selection itself), we additionally compute the no-vig market
    probability for the target selection. We only flag `bet` when the
    model probability beats both the raw book price (positive EV) AND the
    no-vig market consensus by at least `min_edge`. Beating only the raw
    book is usually just consuming the book's vig, not real edge.
    """
    fair_d = fair_decimal_from_prob(fair_probability)
    if book_decimal_odds is None:
        return EdgeReport(
            fair_probability=fair_probability,
            fair_decimal_odds=fair_d,
            book_decimal_odds=None,
            edge=None,
            kelly_fraction=0.0,
            recommendation="no_book_quote",
        )
    edge = edge_decimal(fair_probability, book_decimal_odds)
    kelly = fractional_kelly(
        fair_probability,
        book_decimal_odds,
        fraction=kelly_fraction,
        cap=kelly_cap,
    )
    no_vig_p: Optional[float] = None
    edge_vs_market: Optional[float] = None
    if market_decimal_odds:
        probs = no_vig_probs(list(market_decimal_odds))
        # Target selection is identified by `book_decimal_odds` — find
        # the matching slot. If the market vector wasn't passed with the
        # target first, we still need to identify it.
        try:
            idx = next(
                i for i, d in enumerate(market_decimal_odds)
                if d is not None and abs(float(d) - float(book_decimal_odds)) < 1e-9
            )
        except StopIteration:
            idx = None
        if probs and idx is not None and idx < len(probs):
            no_vig_p = probs[idx]
            edge_vs_market = fair_probability - no_vig_p

    if edge > min_edge and kelly > 0:
        if edge_vs_market is None or edge_vs_market > min_edge:
            rec = "bet"
        else:
            rec = "thin_edge"
    elif edge > 0:
        rec = "thin_edge"
    else:
        rec = "no_bet"
    return EdgeReport(
        fair_probability=fair_probability,
        fair_decimal_odds=fair_d,
        book_decimal_odds=book_decimal_odds,
        edge=edge,
        kelly_fraction=kelly,
        recommendation=rec,
        no_vig_market_prob=no_vig_p,
        edge_vs_market=edge_vs_market,
    )
