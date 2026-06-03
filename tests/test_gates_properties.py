"""Property-based tests for risk gates.

Key invariants:
1. If ALL inputs are within limits, gate passes
2. If ANY single hard limit is breached, gate fails
3. Multiple failures all reported (not just first)
4. Gate result is deterministic
5. derive_event_family is idempotent (running twice on output = same)
"""

import pytest
from hypothesis import given, settings, strategies as st, assume

from src.risk.gates import (
    PortfolioState, GateConfig, evaluate_gates, derive_event_family,
)


def _portfolio(bankroll=10000.0, cash=10000.0, deployed=None, families=None, peak=None, equity=None):
    return PortfolioState(
        bankroll=bankroll, cash=cash,
        open_positions=deployed or {},
        open_positions_by_family=families or {},
        rolling_30d_peak=peak if peak is not None else bankroll,
        current_equity=equity if equity is not None else bankroll,
    )


# -------------------------------------------------------------------
# Single-violation tests
# -------------------------------------------------------------------

@given(st.floats(min_value=0.11, max_value=0.99))
def test_drawdown_above_threshold_always_fails(dd_pct):
    """Any drawdown >= 10% should fail the gate."""
    p = _portfolio(peak=10000.0, equity=10000.0 * (1 - dd_pct))
    r = evaluate_gates(
        portfolio=p, ticker="K1", event_family="K1",
        proposed_cost=10.0, market_volume_24h=1000.0,
        kelly_fraction_value=0.05,
    )
    assert not r.passed
    assert any("drawdown" in f for f in r.failures)


@given(st.floats(min_value=0.025, max_value=1.0))
def test_oversized_position_always_fails(pos_pct):
    """Any position > 2% of bankroll fails."""
    cost = 10000.0 * pos_pct
    p = _portfolio()
    r = evaluate_gates(
        portfolio=p, ticker="K1", event_family="K1",
        proposed_cost=cost, market_volume_24h=1000.0,
        kelly_fraction_value=0.05,
    )
    assert not r.passed
    assert any("position size" in f for f in r.failures)


@given(st.integers(min_value=5, max_value=100))
def test_too_many_in_family_always_fails(count):
    """5 or more positions in a family is a hard fail."""
    p = _portfolio(families={"KXBTC": count})
    r = evaluate_gates(
        portfolio=p, ticker="KXBTC-X", event_family="KXBTC",
        proposed_cost=10.0, market_volume_24h=1000.0,
        kelly_fraction_value=0.05,
    )
    assert not r.passed
    assert any("event family" in f for f in r.failures)


@given(st.floats(min_value=0.0, max_value=499.0))
def test_low_volume_always_fails(vol):
    p = _portfolio()
    r = evaluate_gates(
        portfolio=p, ticker="K1", event_family="K1",
        proposed_cost=10.0, market_volume_24h=vol,
        kelly_fraction_value=0.05,
    )
    assert not r.passed
    assert any("volume" in f for f in r.failures)


# -------------------------------------------------------------------
# Compound invariants
# -------------------------------------------------------------------

@given(
    st.floats(min_value=1.0, max_value=200.0),       # cost (under 2% of $10k)
    st.floats(min_value=500.0, max_value=1_000_000.0),  # volume
    st.floats(min_value=0.005, max_value=1.0),       # kelly
)
def test_clean_trade_always_passes(cost, vol, kelly):
    """If everything is within limits, gate must pass."""
    p = _portfolio()
    r = evaluate_gates(
        portfolio=p, ticker="K1", event_family="K1",
        proposed_cost=cost, market_volume_24h=vol,
        kelly_fraction_value=kelly,
    )
    assert r.passed, r.failures


@given(
    st.floats(min_value=1.0, max_value=200.0),
    st.floats(min_value=500.0, max_value=1_000_000.0),
    st.floats(min_value=0.005, max_value=1.0),
)
def test_gate_deterministic(cost, vol, kelly):
    p = _portfolio()
    kw = dict(portfolio=p, ticker="K1", event_family="K1",
              proposed_cost=cost, market_volume_24h=vol,
              kelly_fraction_value=kelly)
    r1 = evaluate_gates(**kw)
    r2 = evaluate_gates(**kw)
    assert r1.passed == r2.passed
    assert r1.failures == r2.failures


# -------------------------------------------------------------------
# Event family
# -------------------------------------------------------------------

@given(st.text(alphabet=st.characters(whitelist_categories=["L", "N"]), min_size=1, max_size=20))
def test_derive_event_family_idempotent(ticker):
    """family(family(x)) == family(x) for tickers without hyphens."""
    f1 = derive_event_family(ticker)
    f2 = derive_event_family(f1)
    assert f1 == f2


settings.register_profile("ci", max_examples=200)
settings.load_profile("ci")
