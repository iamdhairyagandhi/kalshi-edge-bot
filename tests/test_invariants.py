"""Tests for runtime invariant checks."""

import pytest

from src.risk.invariants import (
    InvariantViolation,
    check_portfolio_accounting,
    check_order_request,
    reconcile_with_exchange,
    check_orderbook_sanity,
)


# ----------------------- portfolio accounting -----------------------

def test_clean_portfolio_passes():
    r = check_portfolio_accounting(
        starting_bankroll=1000.0, cash=800.0,
        open_positions_cost=200.0, realized_pnl=0.0,
    )
    assert r.passed


def test_negative_cash_violates():
    r = check_portfolio_accounting(
        starting_bankroll=1000.0, cash=-50.0,
        open_positions_cost=200.0, realized_pnl=0.0,
    )
    assert not r.passed
    assert any("cash is negative" in v for v in r.violations)


def test_zero_bankroll_violates():
    r = check_portfolio_accounting(
        starting_bankroll=0.0, cash=0.0,
        open_positions_cost=0.0, realized_pnl=0.0,
    )
    assert not r.passed


def test_impossible_equity_growth_detected():
    """If equity is 1000x what we started with, something's broken."""
    r = check_portfolio_accounting(
        starting_bankroll=1000.0, cash=1_000_000.0,
        open_positions_cost=0.0, realized_pnl=999_000.0,
    )
    assert not r.passed
    assert any("100x" in v for v in r.violations)


def test_raise_if_failed_actually_raises():
    r = check_portfolio_accounting(
        starting_bankroll=1000.0, cash=-50.0,
        open_positions_cost=0.0, realized_pnl=0.0,
    )
    with pytest.raises(InvariantViolation):
        r.raise_if_failed()


# ----------------------- order request -----------------------

def test_clean_order_passes():
    r = check_order_request(ticker="KXBTC-1", side="YES", action="buy",
                            contracts=10, price=0.50)
    assert r.passed


def test_invalid_side_fails():
    r = check_order_request(ticker="K", side="MAYBE", action="buy",
                            contracts=10, price=0.50)
    assert not r.passed


def test_zero_contracts_fails():
    r = check_order_request(ticker="K", side="YES", action="buy",
                            contracts=0, price=0.50)
    assert not r.passed


def test_huge_contracts_fails():
    r = check_order_request(ticker="K", side="YES", action="buy",
                            contracts=999_999, price=0.50)
    assert not r.passed


def test_price_out_of_range_fails():
    r = check_order_request(ticker="K", side="YES", action="buy",
                            contracts=10, price=1.50)
    assert not r.passed


def test_non_cent_price_fails():
    r = check_order_request(ticker="K", side="YES", action="buy",
                            contracts=10, price=0.503)
    assert not r.passed


# ----------------------- exchange reconciliation -----------------------

def test_matching_positions_pass():
    r = reconcile_with_exchange(
        local_positions={"K1:YES": 10, "K2:NO": 5},
        exchange_positions={"K1:YES": 10, "K2:NO": 5},
    )
    assert r.passed


def test_mismatched_quantity_fails():
    r = reconcile_with_exchange(
        local_positions={"K1:YES": 10},
        exchange_positions={"K1:YES": 11},
    )
    assert not r.passed
    assert any("K1:YES" in v for v in r.violations)


def test_missing_on_one_side_fails():
    r = reconcile_with_exchange(
        local_positions={"K1:YES": 10},
        exchange_positions={},
    )
    assert not r.passed


def test_tolerance_respected():
    r = reconcile_with_exchange(
        local_positions={"K1:YES": 10},
        exchange_positions={"K1:YES": 11},
        tolerance_contracts=1,
    )
    assert r.passed


# ----------------------- orderbook sanity -----------------------

def test_normal_book_passes():
    r = check_orderbook_sanity(
        ticker="K1", yes_bid=0.45, yes_ask=0.55,
        no_bid=0.40, no_ask=0.50,
    )
    assert r.passed


def test_crossed_book_allowed_on_kalshi():
    """On Kalshi, yes_bid > yes_ask = arb signal, not corruption."""
    r = check_orderbook_sanity(
        ticker="K1", yes_bid=0.55, yes_ask=0.45,
        no_bid=0.40, no_ask=0.50,
    )
    assert r.passed


def test_out_of_range_price_fails():
    r = check_orderbook_sanity(
        ticker="K1", yes_bid=1.5, yes_ask=0.55,
        no_bid=0.40, no_ask=0.50,
    )
    assert not r.passed


def test_nan_price_fails():
    r = check_orderbook_sanity(
        ticker="K1", yes_bid=float("nan"), yes_ask=0.55,
        no_bid=0.40, no_ask=0.50,
    )
    assert not r.passed
