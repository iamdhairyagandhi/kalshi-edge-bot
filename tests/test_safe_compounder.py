"""Tests for safe_compounder."""

import pytest

from src.strategies.overround_arb import Orderbook
from src.strategies.safe_compounder import (
    estimate_no_win_prob,
    evaluate_book,
    scan_books,
    MAX_NO_ASK,
    MIN_LIQUIDITY_24H_USD,
    MIN_YES_PRICE,
)


def _book(ticker, yes_bid, no_ask, yes_size=100, no_size=100):
    """Create a book where YES is favored, NO is cheap."""
    return Orderbook(
        ticker=ticker,
        yes_best_ask=yes_bid + 0.02,
        yes_best_ask_size=yes_size,
        no_best_ask=no_ask,
        no_best_ask_size=no_size,
        yes_best_bid=yes_bid,
        yes_best_bid_size=yes_size,
        no_best_bid=no_ask - 0.02,
        no_best_bid_size=no_size,
    )


def test_estimate_no_win_prob_caps_premium():
    # At yes=0.85: implied_no=0.15, premium scales linearly to 0.05 cap.
    # extremity = (0.85-0.80)/0.20 = 0.25 → premium = 0.05*0.25 = 0.0125
    # p_no ≈ 0.1625
    p = estimate_no_win_prob(0.85)
    assert 0.15 < p < 0.18
    # At yes=1.00, premium is at cap
    assert estimate_no_win_prob(1.00) == pytest.approx(0.05)
    # Below threshold: no premium
    assert estimate_no_win_prob(0.50) == pytest.approx(0.50)


def test_skip_low_volume():
    book = _book("KX-1", yes_bid=0.85, no_ask=0.12)
    sig = evaluate_book(book, volume_24h_usd=100.0, bankroll=10_000.0)
    assert sig is None


def test_skip_when_yes_not_favored():
    book = _book("KX-1", yes_bid=0.50, no_ask=0.48)
    sig = evaluate_book(book, volume_24h_usd=5_000.0, bankroll=10_000.0)
    assert sig is None


def test_skip_when_no_too_expensive():
    book = _book("KX-1", yes_bid=0.78, no_ask=0.25)
    sig = evaluate_book(book, volume_24h_usd=5_000.0, bankroll=10_000.0)
    assert sig is None


def test_qualifying_market_produces_signal():
    # yes_bid=0.85 → implied_no=0.15, estimated_p_no ≈ 0.18
    # no_ask = 0.12 → net edge ≈ 0.18-0.12 - small fee = > 3¢
    book = _book("KX-1", yes_bid=0.85, no_ask=0.12, no_size=100)
    sig = evaluate_book(book, volume_24h_usd=5_000.0, bankroll=10_000.0)
    assert sig is not None
    assert sig.side == "NO"
    assert sig.action == "buy"
    assert sig.contracts_suggested > 0
    assert sig.net_edge_per_contract > 0.03


def test_position_capped_by_max_pct():
    book = _book("KX-1", yes_bid=0.95, no_ask=0.04, no_size=100_000)
    sig = evaluate_book(book, volume_24h_usd=50_000.0,
                         bankroll=10_000.0, max_position_pct=0.02)
    assert sig is not None
    # Max stake = $200, at $0.04 = 5000 contracts cap
    assert sig.contracts_suggested * sig.no_ask <= 200.01


def test_signal_size_clipped_to_book_depth():
    book = _book("KX-1", yes_bid=0.85, no_ask=0.12, no_size=10)
    sig = evaluate_book(book, volume_24h_usd=5_000.0, bankroll=10_000.0)
    assert sig is not None
    assert sig.contracts_suggested <= 10


def test_scan_books_filters():
    books = [
        _book("KX-good", yes_bid=0.85, no_ask=0.12),
        _book("KX-bad-yes", yes_bid=0.40, no_ask=0.55),
        _book("KX-bad-noprice", yes_bid=0.85, no_ask=0.50),
    ]
    sigs = scan_books(
        books,
        volumes_24h={"KX-good": 5000, "KX-bad-yes": 5000, "KX-bad-noprice": 5000},
        bankroll=10_000.0,
    )
    assert len(sigs) == 1
    assert sigs[0].ticker == "KX-good"
