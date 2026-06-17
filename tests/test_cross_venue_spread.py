import os
import sqlite3
import tempfile

import pytest

from src.clients.polymarket import PolymarketMarket, PolymarketOutcome
from src.jobs.cross_venue_runner import CrossVenueSummary, record_run
from src.strategies.cross_venue_spread import (
    MarketMatch,
    build_spread,
    match_markets,
    parse_poly_top_book,
    question_score,
)
from src.strategies.overround_arb import Orderbook


def test_question_score_rewards_equivalent_questions():
    a = "Will Candidate A win the 2026 election?"
    b = "Will Candidate A win the 2026 election?"
    c = "Will Bitcoin hit $100,000 in June?"
    assert question_score(a, b) > 0.95
    assert question_score(a, c) < 0.35


def test_match_markets_selects_binary_yes_like_outcome():
    kalshi = [{"ticker": "KXELECT-1", "title": "Will Candidate A win the 2026 election?"}]
    poly = [
        PolymarketMarket(
            condition_id="0xC",
            question_id=None,
            slug="candidate-a",
            question="Will Candidate A win the 2026 election?",
            closed=False,
            accepting_orders=True,
            outcomes=[
                PolymarketOutcome(0, "Up", "YES_T"),
                PolymarketOutcome(1, "Down", "NO_T"),
            ],
        )
    ]
    matches = match_markets(kalshi, poly, min_score=0.7)
    assert len(matches) == 1
    assert matches[0].polymarket_token_id == "YES_T"
    assert matches[0].polymarket_outcome_label == "Up"


def test_parse_poly_top_book_handles_unsorted_levels():
    book = parse_poly_top_book({
        "bids": [{"price": "0.81", "size": "10"}, {"price": "0.83", "size": "5"}],
        "asks": [{"price": "0.86", "size": "2"}, {"price": "0.84", "size": "7"}],
    })
    assert book is not None
    assert book.bid == pytest.approx(0.83)
    assert book.bid_size == pytest.approx(5)
    assert book.ask == pytest.approx(0.84)
    assert book.ask_size == pytest.approx(7)


def test_build_spread_flags_executable_candidate():
    match = MarketMatch(
        kalshi_ticker="KX-1",
        kalshi_title="Will X happen?",
        polymarket_condition_id="0xC",
        polymarket_question="Will X happen?",
        polymarket_token_id="YES_T",
        polymarket_outcome_index=0,
        polymarket_outcome_label="Yes",
        score=0.95,
    )
    kalshi_book = Orderbook(
        ticker="KX-1",
        yes_best_bid=0.95,
        yes_best_bid_size=10,
        yes_best_ask=0.96,
        yes_best_ask_size=10,
        no_best_bid=0.04,
        no_best_bid_size=10,
        no_best_ask=0.05,
        no_best_ask_size=10,
    )
    poly_book = parse_poly_top_book({
        "bids": [{"price": "0.81", "size": "20"}],
        "asks": [{"price": "0.82", "size": "20"}],
    })
    spread = build_spread(match, kalshi_book, poly_book, min_spread=0.03)
    assert spread.valuation_spread == pytest.approx(0.14)
    assert spread.best_executable_spread == pytest.approx(0.13)
    assert spread.direction == "buy_poly_sell_kalshi"
    assert spread.decision == "candidate"


def test_record_run_persists_latest_scan_rows():
    match = MarketMatch(
        kalshi_ticker="KX-1",
        kalshi_title="Will X happen?",
        polymarket_condition_id="0xC",
        polymarket_question="Will X happen?",
        polymarket_token_id="YES_T",
        polymarket_outcome_index=0,
        polymarket_outcome_label="Yes",
        score=0.95,
    )
    spread = build_spread(
        match,
        Orderbook("KX-1", 0.96, 10, 0.05, 10, 0.95, 10, 0.04, 10),
        parse_poly_top_book({"bids": [{"price": "0.81", "size": "20"}], "asks": [{"price": "0.82", "size": "20"}]}),
        min_spread=0.03,
    )
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "bot.db")
        record_run(
            db,
            CrossVenueSummary("run1", 1, 1, 0, 1, 1, 1, 1, 0.72, 0.03, "candidate spread(s) found"),
            [spread],
        )
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM cross_venue_scan_runs").fetchone()[0] == 1
            row = conn.execute("SELECT direction, decision FROM cross_venue_spreads").fetchone()
        finally:
            conn.close()
        assert row == ("buy_poly_sell_kalshi", "candidate")
