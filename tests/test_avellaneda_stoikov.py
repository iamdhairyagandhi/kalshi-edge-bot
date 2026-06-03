"""Tests for Avellaneda-Stoikov MM quoter."""

import pytest

from src.strategies.avellaneda_stoikov import (
    ASParams, quote, reservation_price, optimal_half_spread,
    fee_floor_half_spread,
)


def test_zero_inventory_quotes_symmetric_around_mid():
    p = ASParams(mid=0.50, inventory=0, sigma=0.05, time_to_resolution=1.0)
    q = quote(p)
    # bid and ask should be roughly symmetric around 0.50 (cent quantization)
    assert abs((q.bid_price + q.ask_price) / 2 - 0.50) < 0.02
    assert q.bid_cents < q.ask_cents


def test_long_inventory_shifts_quotes_down():
    """When we're long YES, we lower both bid and ask (sell more, buy less)."""
    flat = quote(ASParams(mid=0.50, inventory=0, sigma=0.05, time_to_resolution=1.0))
    long = quote(ASParams(mid=0.50, inventory=100, sigma=0.05, time_to_resolution=1.0))
    assert long.reservation_price < flat.reservation_price
    assert long.bid_cents <= flat.bid_cents
    assert long.ask_cents <= flat.ask_cents


def test_boundary_widening_kicks_in():
    flat = quote(ASParams(mid=0.50, inventory=0, sigma=0.05, time_to_resolution=1.0))
    extreme = quote(ASParams(mid=0.05, inventory=0, sigma=0.05, time_to_resolution=1.0))
    assert extreme.widened
    assert not flat.widened


def test_quotes_clipped_to_legal_range():
    q = quote(ASParams(mid=0.99, inventory=-1000, sigma=0.5, time_to_resolution=10.0))
    assert 0.01 <= q.bid_price <= 0.99
    assert 0.01 <= q.ask_price <= 0.99
    assert q.bid_cents < q.ask_cents


def test_fee_floor_kicks_in_when_spread_too_tight():
    # Very low risk-aversion + high kappa = nearly 0 spread from AS
    q = quote(ASParams(mid=0.50, inventory=0, gamma=0.001,
                       sigma=0.001, time_to_resolution=0.001, kappa=10000))
    assert q.fee_floor_applied


def test_fee_floor_zero_at_boundary():
    # At p=0.01, the fee P*(1-P) is tiny
    f = fee_floor_half_spread(0.01)
    assert f < fee_floor_half_spread(0.50)
