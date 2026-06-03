"""Tests for the Black-Scholes digital option pricer."""

import math

import pytest

from src.models.digital_option import (
    DigitalOptionQuote,
    price_digital,
    realized_volatility,
    standard_normal_cdf,
)


def test_normal_cdf_basic():
    assert standard_normal_cdf(0.0) == pytest.approx(0.5)
    assert standard_normal_cdf(1.0) == pytest.approx(0.8413, abs=1e-3)
    assert standard_normal_cdf(-1.0) == pytest.approx(0.1587, abs=1e-3)
    # Tails
    assert standard_normal_cdf(5.0) > 0.999_999
    assert standard_normal_cdf(-5.0) < 0.000_001


def test_atm_p_yes_about_half():
    """When spot == strike and there's some vol left, P(YES) ≈ 0.5."""
    q = DigitalOptionQuote(spot_price=100.0, strike=100.0,
                            time_to_expiry=1.0, sigma=0.20)
    r = price_digital(q)
    # With drift=0 and σ²t/2 in d2, ATM goes slightly below 0.5
    assert 0.40 < r.p_yes < 0.50


def test_deep_itm_high_prob():
    """Spot >> strike with low vol → P(YES) ≈ 1."""
    q = DigitalOptionQuote(spot_price=200.0, strike=100.0,
                            time_to_expiry=0.01, sigma=0.10)
    r = price_digital(q)
    assert r.p_yes > 0.99


def test_deep_otm_low_prob():
    q = DigitalOptionQuote(spot_price=50.0, strike=100.0,
                            time_to_expiry=0.01, sigma=0.10)
    r = price_digital(q)
    assert r.p_yes < 0.01


def test_expired_returns_deterministic():
    q1 = DigitalOptionQuote(spot_price=110.0, strike=100.0,
                             time_to_expiry=0.0, sigma=0.20)
    assert price_digital(q1).p_yes == 1.0
    q2 = DigitalOptionQuote(spot_price=90.0, strike=100.0,
                             time_to_expiry=0.0, sigma=0.20)
    assert price_digital(q2).p_yes == 0.0


def test_zero_vol_returns_deterministic():
    q = DigitalOptionQuote(spot_price=110.0, strike=100.0,
                            time_to_expiry=1.0, sigma=0.0)
    assert price_digital(q).p_yes == 1.0


def test_edge_calc_when_implied_provided():
    q = DigitalOptionQuote(spot_price=100.0, strike=100.0,
                            time_to_expiry=1.0, sigma=0.20,
                            implied_yes_prob=0.30)
    r = price_digital(q)
    assert r.edge is not None
    assert r.edge == pytest.approx(r.p_yes - 0.30)


def test_negative_inputs_raise():
    with pytest.raises(ValueError):
        price_digital(DigitalOptionQuote(spot_price=-1, strike=100,
                                          time_to_expiry=1, sigma=0.2))
    with pytest.raises(ValueError):
        price_digital(DigitalOptionQuote(spot_price=100, strike=-1,
                                          time_to_expiry=1, sigma=0.2))


def test_realized_vol_basic():
    # Constant price → 0 vol
    assert realized_volatility([100.0] * 10) == 0.0
    # Need >= 2 returns for sample stdev
    assert realized_volatility([100.0]) == 0.0
    assert realized_volatility([100.0, 101.0]) == 0.0
    # Computable for >= 3 points
    v = realized_volatility([100.0, 101.0, 100.5, 102.0, 101.5])
    assert v > 0


def test_more_vol_means_atm_prob_closer_to_half_above():
    """For ATM, more vol pushes Φ(d2) further below 0.5 (since d2 = -σ²t/2 / (σ√t) = -σ√t/2)."""
    q_low = DigitalOptionQuote(spot_price=100.0, strike=100.0,
                                time_to_expiry=1.0, sigma=0.05)
    q_high = DigitalOptionQuote(spot_price=100.0, strike=100.0,
                                 time_to_expiry=1.0, sigma=0.50)
    assert price_digital(q_low).p_yes > price_digital(q_high).p_yes
