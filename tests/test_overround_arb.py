"""Tests for the overround arbitrage scanner."""

import pytest

from src.strategies.overround_arb import (
    Orderbook, find_buy_both_arb, find_sell_both_arb, scan_orderbooks,
    MIN_NET_EDGE_PER_CONTRACT, SAFETY_MARGIN,
)


def make_book(ya, yas, na, nas, yb=0, ybs=0, nb=0, nbs=0, ticker="KXTEST-1"):
    return Orderbook(
        ticker=ticker,
        yes_best_ask=ya, yes_best_ask_size=yas,
        no_best_ask=na, no_best_ask_size=nas,
        yes_best_bid=yb, yes_best_bid_size=ybs,
        no_best_bid=nb, no_best_bid_size=nbs,
    )


def test_no_arb_when_sum_asks_equals_one():
    book = make_book(0.50, 100, 0.50, 100)
    assert find_buy_both_arb(book) is None


def test_no_arb_when_sum_asks_above_one():
    book = make_book(0.55, 100, 0.50, 100)
    assert find_buy_both_arb(book) is None


def test_clear_buy_both_arb():
    # Asks sum to 0.90, gross edge 10¢/contract. Even after fees + margin, plenty.
    book = make_book(0.45, 50, 0.45, 50)
    opp = find_buy_both_arb(book)
    assert opp is not None
    assert opp.direction == "buy_both"
    assert opp.contracts == 50
    assert opp.gross_edge_per_contract == pytest.approx(0.10, abs=1e-9)
    assert opp.net_edge_per_contract >= MIN_NET_EDGE_PER_CONTRACT


def test_arb_rejected_when_edge_too_thin():
    # Asks sum to 0.98 — 2¢ gross edge, but fees + safety margin eat it
    book = make_book(0.49, 100, 0.49, 100)
    assert find_buy_both_arb(book) is None


def test_arb_sized_to_min_book_depth():
    # YES has 5, NO has 100 — should size to 5
    book = make_book(0.40, 5, 0.40, 100)
    opp = find_buy_both_arb(book)
    assert opp is not None
    assert opp.contracts == 5


def test_arb_respects_max_contracts():
    book = make_book(0.40, 1000, 0.40, 1000)
    opp = find_buy_both_arb(book, max_contracts=25)
    assert opp is not None
    assert opp.contracts == 25


def test_sell_both_arb_basic():
    # Bids sum to 1.10 — pay both bids worth of NO/YES, lock 10¢ overround
    book = make_book(0.0, 0, 0.0, 0, yb=0.55, ybs=20, nb=0.55, nbs=20)
    opp = find_sell_both_arb(book)
    assert opp is not None
    assert opp.direction == "sell_both"
    assert opp.gross_edge_per_contract == pytest.approx(0.10, abs=1e-9)


def test_scan_sorts_by_profit():
    books = [
        make_book(0.49, 100, 0.49, 100, ticker="A"),   # filtered (too thin)
        make_book(0.30, 100, 0.30, 100, ticker="B"),   # big arb
        make_book(0.45, 5,   0.45, 5,   ticker="C"),   # small arb, small size
    ]
    opps = scan_orderbooks(books)
    assert len(opps) >= 1
    # Sorted descending by net profit
    assert opps == sorted(opps, key=lambda o: o.net_profit_total, reverse=True)


def test_net_profit_accounting_consistent():
    book = make_book(0.30, 10, 0.30, 10)
    opp = find_buy_both_arb(book)
    assert opp is not None
    assert opp.net_profit_total == pytest.approx(
        opp.net_edge_per_contract * opp.contracts, abs=1e-9
    )
