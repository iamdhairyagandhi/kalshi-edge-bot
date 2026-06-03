"""Tests for the fee model. These are the most important tests in the repo —
fee math errors compound silently and kill strategies."""

import math
import pytest

from src.utils.fees import (
    taker_fee, maker_fee, round_trip_fee, kelly_fraction,
    TradeEconomics, TAKER_FEE_COEFFICIENT,
)


def test_taker_fee_zero_contracts():
    assert taker_fee(0, 0.5) == 0.0


def test_taker_fee_50c_one_contract():
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> ceil to $0.02
    assert taker_fee(1, 0.50) == 0.02


def test_taker_fee_rounds_up():
    # Ensure rounding is ceiling, not bank-rounding
    raw = TAKER_FEE_COEFFICIENT * 1 * 0.3 * 0.7  # 0.0147
    assert taker_fee(1, 0.30) == 0.02  # ceil(0.0147 * 100)/100 = 0.02
    assert taker_fee(1, 0.30) >= raw


def test_taker_fee_scales_with_contracts():
    # 100 contracts at 50¢: 0.07 * 100 * 0.25 = 1.75 -> $1.75
    assert taker_fee(100, 0.50) == 1.75


def test_taker_fee_edge_prices():
    # At 1¢ or 99¢, fees should be tiny
    assert taker_fee(100, 0.01) == pytest.approx(0.07, abs=0.01)
    assert taker_fee(100, 0.99) == pytest.approx(0.07, abs=0.01)


def test_taker_fee_clamps_invalid_prices():
    # Should not explode on out-of-range
    assert taker_fee(1, 0.0) >= 0
    assert taker_fee(1, 1.0) >= 0


def test_maker_fee_is_quarter_of_taker():
    """Since July 2025: maker = ceil(0.0175 * C * P * (1-P) * 100) / 100.
    At P=0.50, C=100: raw = 0.4375 → ceil to 0.44."""
    assert maker_fee(100, 0.50) == 0.44
    # And maker is ~1/4 of taker (0.0175 / 0.07 = 0.25)
    assert maker_fee(1000, 0.50) < taker_fee(1000, 0.50) / 3


def test_round_trip_taker_taker():
    # Open at 50¢, close at 60¢, both taker, 10 contracts
    expected = taker_fee(10, 0.50) + taker_fee(10, 0.60)
    assert round_trip_fee(10, 0.50, 0.60) == expected


def test_round_trip_maker_taker():
    # Maker leg + taker leg
    expected = maker_fee(10, 0.50) + taker_fee(10, 0.60)
    assert round_trip_fee(10, 0.50, 0.60, entry_is_maker=True) == pytest.approx(expected)


def test_trade_economics_net_ev_positive():
    # True prob 70%, paying 50¢, 10 contracts, taker
    t = TradeEconomics(contracts=10, side="YES", entry_price=0.50, true_prob=0.70, is_maker=False)
    # Gross EV per contract = 0.70 * 0.50 + 0.30 * (-0.50) = 0.35 - 0.15 = 0.20
    # Total gross EV = 2.00, minus open fee of ~0.18
    assert t.net_ev == pytest.approx(2.00 - taker_fee(10, 0.50), abs=0.001)


def test_trade_economics_maker_better_than_taker():
    common = dict(contracts=10, side="NO", entry_price=0.30, true_prob=0.80)
    maker = TradeEconomics(**common, is_maker=True)
    taker = TradeEconomics(**common, is_maker=False)
    assert maker.net_ev > taker.net_ev


def test_kelly_basic():
    # 60% prob, paying 50¢ -> b = 1, p = 0.6, q = 0.4
    # f* = (1*0.6 - 0.4)/1 = 0.2, half-kelly = 0.1
    assert kelly_fraction(0.60, 1.0, cap=0.5) == pytest.approx(0.10, abs=1e-6)


def test_kelly_negative_edge_returns_zero():
    # 40% prob at 50¢ -> negative edge
    assert kelly_fraction(0.40, 1.0) == 0.0


def test_kelly_extreme_probs():
    assert kelly_fraction(0.0, 1.0) == 0.0
    assert kelly_fraction(1.0, 1.0) == 0.0
