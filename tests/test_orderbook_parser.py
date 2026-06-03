"""Tests for orderbook parser (Kalshi quirk: bids only on both sides)."""

import pytest

from src.utils.orderbook_parser import parse_orderbook


def test_parses_typical_book():
    payload = {"orderbook": {
        "yes": [[45, 100], [44, 50]],   # YES bids: 45¢ x100, 44¢ x50
        "no":  [[52, 80],  [51, 30]],   # NO bids:  52¢ x80,  51¢ x30
    }}
    ob = parse_orderbook("KXTEST-1", payload)
    assert ob is not None
    assert ob.ticker == "KXTEST-1"
    # YES bid taken directly
    assert ob.yes_best_bid == pytest.approx(0.45)
    assert ob.yes_best_bid_size == 100
    # NO bid taken directly
    assert ob.no_best_bid == pytest.approx(0.52)
    assert ob.no_best_bid_size == 80
    # YES ask = 1 - NO bid
    assert ob.yes_best_ask == pytest.approx(0.48)
    assert ob.yes_best_ask_size == 80
    # NO ask = 1 - YES bid
    assert ob.no_best_ask == pytest.approx(0.55)
    assert ob.no_best_ask_size == 100


def test_returns_none_for_empty_book():
    payload = {"orderbook": {"yes": [], "no": []}}
    assert parse_orderbook("X", payload) is None


def test_handles_one_sided_book():
    # Only YES bids exist; NO side empty -> NO ask defaults to $1
    payload = {"orderbook": {"yes": [[40, 10]], "no": []}}
    ob = parse_orderbook("X", payload)
    assert ob is not None
    assert ob.yes_best_bid == pytest.approx(0.40)
    assert ob.no_best_ask == pytest.approx(0.60)  # 1 - 0.40
    assert ob.yes_best_ask == 1.0                  # no NO bids -> default
    assert ob.yes_best_ask_size == 0
