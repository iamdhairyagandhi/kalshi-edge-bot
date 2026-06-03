"""Property-based tests for fees.py using Hypothesis.

These tests auto-generate thousands of inputs and check INVARIANTS that
must hold regardless of inputs. They catch edge cases human-written tests
miss.

Key invariants for fees:
1. Fees are always non-negative
2. Fees are always quantized to whole cents (* 100 is an integer)
3. Fees never decrease when contracts increase
4. Fees are symmetric around price=0.5 (since P*(1-P) is)
5. Maker fees <= taker fees (always)
6. net_ev(p=1) = +full payout - cost - fees
7. net_ev(p=0) = -cost - fees
8. Kelly fraction is always in [0, cap]
9. Kelly fraction is 0 when prob_win <= price (no edge)
"""

import math

import pytest
from hypothesis import given, settings, strategies as st, assume

from src.utils.fees import (
    taker_fee, maker_fee, round_trip_fee,
    TradeEconomics, kelly_fraction,
)


# Strategies for generating valid inputs
contracts_strat = st.integers(min_value=1, max_value=10_000)
price_strat = st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
prob_strat = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


# -------------------------------------------------------------------
# Fee invariants
# -------------------------------------------------------------------

@given(contracts_strat, price_strat)
def test_taker_fee_non_negative(n, p):
    assert taker_fee(n, p) >= 0


@given(contracts_strat, price_strat)
def test_taker_fee_quantized_to_cents(n, p):
    fee = taker_fee(n, p)
    # fee * 100 should be very close to an integer
    cents = fee * 100
    assert abs(cents - round(cents)) < 1e-6, f"fee {fee} not cent-quantized for ({n}, {p})"


@given(contracts_strat, contracts_strat, price_strat)
def test_taker_fee_monotonic_in_contracts(n1, n2, p):
    assume(n1 < n2)
    assert taker_fee(n1, p) <= taker_fee(n2, p)


@given(contracts_strat, price_strat)
def test_taker_fee_symmetric_around_half(n, p):
    """fee(p) == fee(1-p) because P*(1-P) is symmetric."""
    assume(0.01 <= p <= 0.99 and 0.01 <= 1 - p <= 0.99)
    assert taker_fee(n, p) == taker_fee(n, 1 - p)


@given(contracts_strat, price_strat)
def test_maker_fee_le_taker_fee(n, p):
    """Maker fee can never be worse than taker fee."""
    assert maker_fee(n, p) <= taker_fee(n, p)


@given(contracts_strat, price_strat)
def test_taker_fee_upper_bound(n, p):
    """Fee is at most 7% of notional, plus one cent of rounding."""
    notional = n * p * (1 - p)
    assert taker_fee(n, p) <= 0.07 * notional + 0.01 + 1e-9


@given(contracts_strat, price_strat, price_strat)
def test_round_trip_equals_sum_of_legs(n, p1, p2):
    """Round trip fee = sum of two legs (basic accounting)."""
    rt = round_trip_fee(n, p1, p2, entry_is_maker=False, exit_is_maker=False)
    assert rt == taker_fee(n, p1) + taker_fee(n, p2)


# -------------------------------------------------------------------
# TradeEconomics invariants
# -------------------------------------------------------------------

@given(contracts_strat, price_strat)
def test_net_ev_at_prob_one_equals_max_profit(n, p):
    """If we're certain to win, EV = (payout - cost) - fees."""
    t = TradeEconomics(contracts=n, side="YES", entry_price=p, true_prob=1.0, is_maker=False)
    expected = n * (1 - p) - t.open_fee
    assert math.isclose(t.net_ev, expected, abs_tol=1e-9)


@given(contracts_strat, price_strat)
def test_net_ev_at_prob_zero_equals_full_loss(n, p):
    """If we're certain to lose, EV = -cost - fees."""
    t = TradeEconomics(contracts=n, side="YES", entry_price=p, true_prob=0.0, is_maker=False)
    expected = -(n * p) - t.open_fee
    assert math.isclose(t.net_ev, expected, abs_tol=1e-9)


@given(contracts_strat, price_strat, prob_strat)
def test_net_ev_monotonic_in_true_prob(n, p, prob1):
    """Higher true probability -> higher EV for a YES position."""
    prob2 = min(1.0, prob1 + 0.1)
    assume(prob2 != prob1)
    t1 = TradeEconomics(contracts=n, side="YES", entry_price=p, true_prob=prob1, is_maker=False)
    t2 = TradeEconomics(contracts=n, side="YES", entry_price=p, true_prob=prob2, is_maker=False)
    assert t2.net_ev > t1.net_ev


@given(contracts_strat, price_strat, prob_strat)
def test_maker_ev_dominates_taker_ev(n, p, prob):
    """Maker order always has weakly higher EV than taker (no fee)."""
    common = dict(contracts=n, side="YES", entry_price=p, true_prob=prob)
    assert TradeEconomics(**common, is_maker=True).net_ev >= TradeEconomics(**common, is_maker=False).net_ev


# -------------------------------------------------------------------
# Kelly invariants
# -------------------------------------------------------------------

@given(prob_strat, st.floats(min_value=0.01, max_value=100.0))
def test_kelly_always_in_unit_interval(prob, payout):
    """Kelly fraction is always in [0, 1]."""
    k = kelly_fraction(prob, payout, cap=1.0)
    assert 0 <= k <= 1


@given(prob_strat, st.floats(min_value=0.01, max_value=100.0))
def test_kelly_cap_respected(prob, payout):
    """Fractional Kelly never exceeds the cap."""
    cap = 0.5
    k = kelly_fraction(prob, payout, cap=cap)
    assert k <= cap + 1e-9


@given(price_strat)
def test_kelly_zero_when_no_edge(p):
    """If true_prob equals market price, edge is zero -> Kelly is zero."""
    payout = (1 - p) / p
    assert kelly_fraction(p, payout) == 0.0


@given(price_strat, prob_strat)
def test_kelly_zero_when_negative_edge(p, prob):
    """If true_prob < market-implied prob, no Kelly bet."""
    assume(prob < p)
    payout = (1 - p) / p
    assert kelly_fraction(prob, payout) == 0.0


# -------------------------------------------------------------------
# Run with extra effort
# -------------------------------------------------------------------

# Speed up CI but still cover a lot of cases
settings.register_profile("ci", max_examples=200)
settings.load_profile("ci")
