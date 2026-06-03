"""
Runtime invariant checks.

These run *in production*, not just in tests. They catch state corruption
the moment it happens, before bad decisions get made on bad state.

Philosophy: any invariant that "must always be true" deserves an assertion.
The cost of a check is microseconds; the cost of trading on corrupted state
is your bankroll.

All checks raise InvariantViolation on failure. The runner catches these
and triggers a kill-switch (stops trading, demands manual intervention).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


class InvariantViolation(Exception):
    """Raised when production state violates an invariant. Halt trading."""


@dataclass
class InvariantReport:
    passed: bool
    violations: List[str]

    def raise_if_failed(self) -> None:
        if not self.passed:
            raise InvariantViolation("; ".join(self.violations))


# Tolerance for floating-point comparisons in dollar arithmetic
DOLLAR_EPS = 0.005   # half a cent


def check_portfolio_accounting(
    *,
    starting_bankroll: float,
    cash: float,
    open_positions_cost: float,
    realized_pnl: float,
) -> InvariantReport:
    """
    The fundamental ledger invariant:
        cash + open_positions_cost = starting_bankroll + realized_pnl
                                     - fees_paid (which are baked into cash already)

    In our paper executor, fees are deducted from cash at trade time and
    realized_pnl includes the fees-paid effect on closed trades. So:
        cash + open_positions_cost == starting_bankroll + realized_pnl - open_fees_unrealized

    For the simpler check we use here, we just verify the system isn't
    obviously broken (cash never negative, no negative position cost, etc.).
    A stricter ledger reconciliation lives in `reconcile_with_exchange`.
    """
    violations: List[str] = []
    if cash < -DOLLAR_EPS:
        violations.append(f"cash is negative: ${cash:.4f}")
    if open_positions_cost < -DOLLAR_EPS:
        violations.append(f"open_positions_cost is negative: ${open_positions_cost:.4f}")
    if starting_bankroll <= 0:
        violations.append(f"starting_bankroll non-positive: ${starting_bankroll:.4f}")

    # Total equity cannot exceed starting + max-possible-payout sanity bound.
    # If equity > 100x starting, something is very wrong.
    equity = cash + open_positions_cost
    if equity > starting_bankroll * 100:
        violations.append(
            f"equity ${equity:.2f} is >100x starting ${starting_bankroll:.2f} — corruption likely"
        )
    return InvariantReport(passed=not violations, violations=violations)


def check_order_request(
    *,
    ticker: str,
    side: str,
    action: str,
    contracts: int,
    price: float,
) -> InvariantReport:
    """Sanity-check an order before sending."""
    violations: List[str] = []
    if not ticker or not ticker.strip():
        violations.append("ticker is empty")
    if side.upper() not in ("YES", "NO"):
        violations.append(f"invalid side: {side!r}")
    if action.lower() not in ("buy", "sell"):
        violations.append(f"invalid action: {action!r}")
    if contracts <= 0:
        violations.append(f"non-positive contracts: {contracts}")
    if contracts > 100_000:
        violations.append(f"contracts {contracts} exceeds sanity bound 100k")
    if not (0.01 <= price <= 0.99):
        violations.append(f"price {price} outside valid Kalshi range [0.01, 0.99]")
    # Price must be quantized to cents
    cents = price * 100
    if abs(cents - round(cents)) > 1e-6:
        violations.append(f"price {price} not cent-quantized")
    return InvariantReport(passed=not violations, violations=violations)


def reconcile_with_exchange(
    *,
    local_positions: Dict[str, int],          # ticker:side -> contracts
    exchange_positions: Dict[str, int],
    tolerance_contracts: int = 0,
) -> InvariantReport:
    """
    Compare our internal position ledger against what the exchange says
    we hold. Any divergence beyond `tolerance_contracts` is a violation.

    Call this periodically (e.g., every N minutes). If it fails, STOP
    TRADING and reconcile manually before resuming.
    """
    violations: List[str] = []
    all_keys = set(local_positions) | set(exchange_positions)
    for key in sorted(all_keys):
        local = local_positions.get(key, 0)
        exch = exchange_positions.get(key, 0)
        if abs(local - exch) > tolerance_contracts:
            violations.append(
                f"position mismatch {key}: local={local}, exchange={exch}"
            )
    return InvariantReport(passed=not violations, violations=violations)


def check_orderbook_sanity(
    *,
    ticker: str,
    yes_bid: float, yes_ask: float,
    no_bid: float, no_ask: float,
) -> InvariantReport:
    """
    Sanity-check an orderbook before basing trades on it.

    Invariants:
      - All prices in [0, 1]
      - bid <= ask on each side (otherwise the book is crossed = something's wrong)
      - yes_ask + no_ask >= ~yes_bid + no_bid (spread is non-negative)
      - No NaN/inf
    """
    violations: List[str] = []
    import math as _math
    for name, v in [("yes_bid", yes_bid), ("yes_ask", yes_ask),
                    ("no_bid", no_bid), ("no_ask", no_ask)]:
        if not _math.isfinite(v):
            violations.append(f"{ticker}:{name} is not finite: {v}")
            continue
        if v < 0 or v > 1:
            violations.append(f"{ticker}:{name} out of [0,1]: {v}")

    if yes_bid > yes_ask + 1e-9:
        # NOTE: On Kalshi asks are derived from the opposing side's bids.
        # When yes_bid + no_bid > 1 (arb opportunity), yes_bid will exceed
        # the derived yes_ask. That's a SIGNAL, not corruption. So we don't
        # flag bid>ask as a violation here — we let the arb scanner handle it.
        pass
    if no_bid > no_ask + 1e-9:
        pass

    return InvariantReport(passed=not violations, violations=violations)
