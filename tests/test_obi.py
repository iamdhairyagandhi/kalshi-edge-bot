"""Tests for OBI."""

from src.signals.obi import compute_obi, should_skip_post_yes_bid
from src.strategies.overround_arb import Orderbook


def _book(yes_bid_size, yes_ask_size, no_bid_size=10, no_ask_size=10):
    return Orderbook(
        ticker="K1",
        yes_best_ask=0.55, yes_best_ask_size=yes_ask_size,
        no_best_ask=0.45, no_best_ask_size=no_ask_size,
        yes_best_bid=0.45, yes_best_bid_size=yes_bid_size,
        no_best_bid=0.40, no_best_bid_size=no_bid_size,
    )


def test_balanced_book_obi_zero():
    obi = compute_obi(_book(yes_bid_size=10, yes_ask_size=10))
    assert obi.yes_obi == 0.0


def test_bid_heavy_obi_positive():
    obi = compute_obi(_book(yes_bid_size=100, yes_ask_size=10))
    assert obi.yes_obi > 0.5


def test_ask_heavy_obi_negative():
    obi = compute_obi(_book(yes_bid_size=10, yes_ask_size=100))
    assert obi.yes_obi < -0.5


def test_skip_post_when_book_ask_heavy():
    assert should_skip_post_yes_bid(_book(yes_bid_size=5, yes_ask_size=100))
    assert not should_skip_post_yes_bid(_book(yes_bid_size=50, yes_ask_size=50))
