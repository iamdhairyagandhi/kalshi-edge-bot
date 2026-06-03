"""
Kalshi fee model.

Reference: https://kalshi.com/docs/fees

Taker fee per contract, rounded up to the nearest cent, per side:
    fee = ceil(0.07 * N * P * (1 - P) * 100) / 100
where:
    N = number of contracts
    P = trade price in dollars (0.01 - 0.99)

Maker orders that rest in the book and get hit are typically fee-free
(verify your account's current schedule — Kalshi has changed this).

All EV / edge calculations MUST go through this module. Net-of-fee
EV is the only number that matters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# Kalshi's published fee coefficients (verified via research, April 2025+):
#   taker = ceil(0.07   * C * P * (1-P) * 100) / 100   ($/contract)
#   maker = ceil(0.0175 * C * P * (1-P) * 100) / 100   ($/contract) — introduced April 2025
# Some products may still have free maker; set MAKER_FEE_COEFFICIENT = 0.0 to override.
TAKER_FEE_COEFFICIENT = 0.07
MAKER_FEE_COEFFICIENT = 0.0175


def _quantize_ceil(raw: float) -> float:
    """Kalshi rounds fees UP to the next cent. Epsilon avoids FP over-charge."""
    return math.ceil(raw * 100 - 1e-9) / 100


def taker_fee(contracts: int, price: float) -> float:
    """Per-side taker fee, in dollars, rounded up to the nearest cent."""
    if contracts <= 0:
        return 0.0
    price = max(0.01, min(0.99, price))
    raw = TAKER_FEE_COEFFICIENT * contracts * price * (1.0 - price)
    return _quantize_ceil(raw)


def maker_fee(contracts: int, price: float) -> float:
    """Per-side maker fee, in dollars, rounded up to the nearest cent.
    Since July 2025 Kalshi scales maker fees by P*(1-P) just like taker."""
    if contracts <= 0 or MAKER_FEE_COEFFICIENT == 0.0:
        return 0.0
    price = max(0.01, min(0.99, price))
    raw = MAKER_FEE_COEFFICIENT * contracts * price * (1.0 - price)
    return _quantize_ceil(raw)


def round_trip_fee(
    contracts: int,
    entry_price: float,
    exit_price: float,
    entry_is_maker: bool = False,
    exit_is_maker: bool = False,
) -> float:
    """Total fees for opening + closing a position."""
    open_fee = maker_fee(contracts, entry_price) if entry_is_maker else taker_fee(contracts, entry_price)
    close_fee = maker_fee(contracts, exit_price) if exit_is_maker else taker_fee(contracts, exit_price)
    return open_fee + close_fee


@dataclass
class TradeEconomics:
    """Net-of-fee economics for a single contemplated trade."""
    contracts: int
    side: str                  # "YES" or "NO"
    entry_price: float         # what we pay per contract
    true_prob: float           # our estimate of P(win)
    is_maker: bool             # True if resting / maker order

    @property
    def cost(self) -> float:
        """Capital required to open (collateral)."""
        return self.contracts * self.entry_price

    @property
    def open_fee(self) -> float:
        return maker_fee(self.contracts, self.entry_price) if self.is_maker \
            else taker_fee(self.contracts, self.entry_price)

    @property
    def payout_if_win(self) -> float:
        """Gross payout on win (contracts settle at $1)."""
        return float(self.contracts)

    @property
    def gross_profit_if_win(self) -> float:
        return self.payout_if_win - self.cost

    @property
    def net_ev(self) -> float:
        """
        Net-of-fee expected value, assuming we hold to settlement.
        Settlement has no fee. Only the open fee matters here.
        """
        p = self.true_prob
        win = self.gross_profit_if_win
        lose = -self.cost
        return p * win + (1 - p) * lose - self.open_fee

    @property
    def net_edge_per_contract(self) -> float:
        if self.contracts == 0:
            return 0.0
        return self.net_ev / self.contracts


def kelly_fraction(prob_win: float, payout_ratio: float, cap: float = 0.5) -> float:
    """
    Fractional Kelly for a binary bet.

    payout_ratio b = (payoff_if_win) / (stake) = (1 - price) / price for a YES/NO contract.
    Kelly f* = (bp - q) / b, where q = 1 - p.
    Returns max(0, f*) * cap (half-Kelly default).
    Snaps near-zero results to 0 to avoid FP noise propagating downstream.
    """
    if payout_ratio <= 0 or prob_win <= 0 or prob_win >= 1:
        return 0.0
    q = 1.0 - prob_win
    f_star = (payout_ratio * prob_win - q) / payout_ratio
    if f_star < 1e-12:
        return 0.0
    return f_star * cap
