"""Tests for the pre-trade risk gates."""

import pytest

from src.risk.gates import (
    PortfolioState, GateConfig, evaluate_gates, derive_event_family,
)


def make_portfolio(
    bankroll=10000.0, cash=8000.0, open_positions=None,
    families=None, peak=10000.0, equity=10000.0,
):
    return PortfolioState(
        bankroll=bankroll,
        cash=cash,
        open_positions=open_positions or {},
        open_positions_by_family=families or {},
        rolling_30d_peak=peak,
        current_equity=equity,
    )


def call(p=None, **kw):
    defaults = dict(
        portfolio=p or make_portfolio(),
        ticker="KXBTC-26APR-B95000",
        event_family="KXBTC",
        proposed_cost=100.0,
        market_volume_24h=1000.0,
        kelly_fraction_value=0.05,
    )
    defaults.update(kw)
    return evaluate_gates(**defaults)


def test_clean_trade_passes_all_gates():
    r = call()
    assert r.passed, r.failures


def test_drawdown_kill_switch_triggers():
    p = make_portfolio(peak=10000.0, equity=8900.0)  # 11% dd
    r = call(p=p)
    assert not r.passed
    assert any("drawdown" in f for f in r.failures)


def test_per_position_cap_enforced():
    # 2% of $10k = $200; propose $500
    r = call(proposed_cost=500.0)
    assert not r.passed
    assert any("position size" in f for f in r.failures)


def test_total_deployment_cap_enforced():
    # Already $2400 deployed (24%), proposing $200 (2%) -> 26%, over cap
    p = make_portfolio(open_positions={"X": 2400.0})
    r = call(p=p, proposed_cost=200.0)
    assert not r.passed
    assert any("total deployment" in f for f in r.failures)


def test_family_concentration_cap_enforced():
    p = make_portfolio(families={"KXBTC": 5})
    r = call(p=p)
    assert not r.passed
    assert any("event family" in f for f in r.failures)


def test_low_liquidity_rejected():
    r = call(market_volume_24h=100.0)
    assert not r.passed
    assert any("volume" in f for f in r.failures)


def test_insufficient_cash_rejected():
    p = make_portfolio(cash=50.0)
    r = call(p=p, proposed_cost=100.0)
    assert not r.passed
    assert any("cash" in f for f in r.failures)


def test_tiny_kelly_rejected():
    r = call(kelly_fraction_value=0.0001)
    assert not r.passed
    assert any("kelly" in f for f in r.failures)


def test_multiple_failures_reported_together():
    p = make_portfolio(cash=10.0, peak=10000.0, equity=8000.0)
    r = call(p=p, proposed_cost=500.0, market_volume_24h=10.0)
    assert not r.passed
    assert len(r.failures) >= 3


def test_derive_event_family_kx_prefix():
    assert derive_event_family("KXBTC-26APR-B95000") == "KXBTC"
    assert derive_event_family("KXNFLGAME-25DEC25-DAL") == "KXNFLGAME"
    assert derive_event_family("SINGLETOKEN") == "SINGLETOKEN"
