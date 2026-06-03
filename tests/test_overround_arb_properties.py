"""Property-based tests for the overround arbitrage scanner.

Key invariants:
1. If no arb exists in the book, scanner returns None
2. If an arb exists, contracts <= min(book sizes on relevant levels)
3. net_edge_per_contract = gross - fees - safety_margin
4. net_profit_total = net_edge_per_contract * contracts
5. Buy-both and sell-both never both fire on the same book at the same level
   (they're the same opportunity)
6. Scanner is deterministic (same input -> same output)
"""

import pytest
from hypothesis import given, settings, strategies as st, assume

from src.strategies.overround_arb import (
    Orderbook, find_buy_both_arb, find_sell_both_arb, scan_orderbooks,
    MIN_NET_EDGE_PER_CONTRACT, SAFETY_MARGIN,
)


# Generators for valid books
price_cents = st.integers(min_value=1, max_value=99)
size_strat = st.integers(min_value=1, max_value=1000)


@st.composite
def orderbook_st(draw):
    yes_bid = draw(price_cents) / 100.0
    no_bid = draw(price_cents) / 100.0
    yes_size = draw(size_strat)
    no_size = draw(size_strat)
    return Orderbook(
        ticker="K-RAND",
        yes_best_ask=1.0 - no_bid,    # ask derived from opposing bid
        yes_best_ask_size=no_size,
        no_best_ask=1.0 - yes_bid,
        no_best_ask_size=yes_size,
        yes_best_bid=yes_bid,
        yes_best_bid_size=yes_size,
        no_best_bid=no_bid,
        no_best_bid_size=no_size,
    )


@given(orderbook_st())
def test_arb_size_never_exceeds_book(book):
    opp = find_buy_both_arb(book, max_contracts=10_000)
    if opp is not None:
        assert opp.contracts <= min(book.yes_best_ask_size, book.no_best_ask_size)
        assert opp.contracts >= 1


@given(orderbook_st())
def test_arb_size_respects_max_contracts(book):
    opp = find_buy_both_arb(book, max_contracts=10)
    if opp is not None:
        assert opp.contracts <= 10


@given(orderbook_st())
def test_arb_net_edge_consistent_with_fields(book):
    opp = find_buy_both_arb(book)
    if opp is not None:
        # net = gross - fees_per - safety_margin
        expected = opp.gross_edge_per_contract - opp.fees_per_contract - SAFETY_MARGIN
        assert opp.net_edge_per_contract == pytest.approx(expected, abs=1e-9)


@given(orderbook_st())
def test_arb_net_profit_total_equals_per_times_contracts(book):
    opp = find_buy_both_arb(book)
    if opp is not None:
        expected = opp.net_edge_per_contract * opp.contracts
        assert opp.net_profit_total == pytest.approx(expected, abs=1e-9)


@given(orderbook_st())
def test_arb_only_fires_above_threshold(book):
    """If returned, net edge must be at least MIN_NET_EDGE_PER_CONTRACT."""
    opp = find_buy_both_arb(book)
    if opp is not None:
        assert opp.net_edge_per_contract >= MIN_NET_EDGE_PER_CONTRACT


@given(orderbook_st())
def test_no_arb_when_sum_asks_ge_one(book):
    """If sum of asks >= 1, there can never be a buy-both arb."""
    if book.yes_best_ask + book.no_best_ask >= 1.0:
        assert find_buy_both_arb(book) is None


@given(orderbook_st())
def test_no_arb_when_sum_bids_le_one(book):
    """If sum of bids <= 1, there can never be a sell-both arb."""
    if book.yes_best_bid + book.no_best_bid <= 1.0:
        assert find_sell_both_arb(book) is None


@given(orderbook_st())
def test_buy_and_sell_arb_are_equivalent_on_kalshi(book):
    """
    On Kalshi, sum_asks < 1  <=>  sum_bids > 1, because the book is
    derived (asks come from opposing bids). So the two finders should
    EITHER both find or both not find an arb on the same book.
    """
    buy = find_buy_both_arb(book)
    sell = find_sell_both_arb(book)
    assert (buy is None) == (sell is None)


@given(st.lists(orderbook_st(), min_size=0, max_size=20))
def test_scan_dedupes_per_ticker(books):
    """The scanner must return at most one opportunity per ticker."""
    opps = scan_orderbooks(books)
    tickers = [o.ticker for o in opps]
    assert len(tickers) == len(set(tickers))


@given(st.lists(orderbook_st(), min_size=0, max_size=20))
def test_scan_sorted_descending_by_profit(books):
    opps = scan_orderbooks(books)
    profits = [o.net_profit_total for o in opps]
    assert profits == sorted(profits, reverse=True)


@given(orderbook_st())
def test_scan_deterministic(book):
    """Same input -> same output, every time."""
    r1 = scan_orderbooks([book])
    r2 = scan_orderbooks([book])
    assert len(r1) == len(r2)
    if r1:
        assert r1[0].net_profit_total == r2[0].net_profit_total
        assert r1[0].contracts == r2[0].contracts


settings.register_profile("ci", max_examples=200)
settings.load_profile("ci")
