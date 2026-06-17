"""Tests for edge / Kelly utilities."""

from __future__ import annotations

import math

from src.sports.soccer.pricing.edge import (
    decimal_to_implied_prob,
    edge_decimal,
    fractional_kelly,
    implied_prob_to_decimal,
    overround,
    report_edge,
)


def test_decimal_implied_round_trip():
    assert math.isclose(decimal_to_implied_prob(2.0), 0.5)
    assert math.isclose(implied_prob_to_decimal(0.25), 4.0)


def test_overround_binary_book_typical_5_pct():
    # 1.92/1.92 implies 0.5208/0.5208 = 1.0416 -> 4.16% overround
    o = overround([1.92, 1.92])
    assert 0.04 < o < 0.05


def test_edge_positive_when_p_beats_implied():
    p = 0.55
    book = 2.0  # implied 0.5
    e = edge_decimal(p, book)
    assert e > 0
    assert math.isclose(e, 0.55 * 2.0 - 1.0)


def test_kelly_zero_when_no_edge():
    assert fractional_kelly(0.5, 2.0) == 0.0


def test_kelly_caps_at_provided_limit():
    f = fractional_kelly(0.99, 50.0, fraction=1.0, cap=0.05)
    assert f == 0.05


def test_report_edge_recommends_bet_with_clear_positive_edge():
    rep = report_edge(0.55, 2.10, min_edge=0.02)
    assert rep.recommendation == "bet"
    assert rep.edge is not None and rep.edge > 0
    assert rep.kelly_fraction > 0


def test_report_edge_no_book_quote():
    rep = report_edge(0.30, None)
    assert rep.recommendation == "no_book_quote"
    assert rep.book_decimal_odds is None
