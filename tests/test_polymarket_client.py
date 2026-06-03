"""Tests for the Polymarket client (offline / replay mode only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.clients.polymarket import (
    PolymarketAPIError,
    PolymarketClient,
    PolymarketMarket,
    PolymarketTrade,
    _fixture_filename,
)


@pytest.fixture
def replay_dir(tmp_path):
    """Build a minimal replay tree the tests can point the client at."""
    d = tmp_path / "fixtures"
    d.mkdir()

    # /markets?active=true&closed=false&limit=2&offset=0
    markets_path = d / _fixture_filename(
        "/markets", {"active": "true", "closed": "false", "limit": 2, "offset": 0}
    )
    markets_path.write_text(json.dumps([
        {
            "conditionId": "0xCOND_A",
            "questionId": "0xQ_A",
            "slug": "will-x-happen",
            "question": "Will X happen?",
            "closed": False,
            "acceptingOrders": True,
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["111", "222"]),
            "volume24hr": 5000.0,
            "liquidity": 12000.0,
            "endDate": "2026-12-31T00:00:00Z",
        },
        {
            "conditionId": "0xCOND_B",
            "questionId": None,
            "slug": "multi-outcome",
            "question": "Who wins?",
            "closed": False,
            "acceptingOrders": True,
            "outcomes": ["Alice", "Bob", "Carol"],
            "clobTokenIds": ["1", "2", "3"],
        },
    ]))

    # /markets?condition_ids=0xCOND_A
    market_one_path = d / _fixture_filename("/markets", {"condition_ids": "0xCOND_A"})
    market_one_path.write_text(json.dumps([{
        "conditionId": "0xCOND_A",
        "questionId": "0xQ_A",
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "closed": False,
        "acceptingOrders": True,
        "outcomes": ["Yes", "No"],
        "clobTokenIds": ["111", "222"],
    }]))

    # /trades?user=0xWALLET&limit=500
    trades_path = d / _fixture_filename("/trades", {"user": "0xWALLET", "limit": 500})
    trades_path.write_text(json.dumps([
        {
            "proxyWallet": "0xWALLET",
            "conditionId": "0xCOND_A",
            "outcomeIndex": 0,
            "asset": "111",
            "side": "BUY",
            "size": 100.0,
            "price": 0.45,
            "timestamp": 1717000000,
            "transactionHash": "0xTX1",
        },
        {
            "proxyWallet": "0xWALLET",
            "conditionId": "0xCOND_A",
            "outcomeIndex": 1,
            "asset": "222",
            "side": "BUY",
            "size": 50.0,
            "price": 0.55,
            "timestamp": 1717001000,
        },
    ]))

    # /leaderboard
    lb_path = d / _fixture_filename(
        "/leaderboard", {"window": "month", "metric": "profit", "limit": 50}
    )
    lb_path.write_text(json.dumps([
        {"wallet": "0xA", "profit": 50000, "volume": 1_000_000},
        {"wallet": "0xB", "profit": 30000, "volume":   500_000},
    ]))

    # /book?token_id=111
    book_path = d / _fixture_filename("/book", {"token_id": "111"})
    book_path.write_text(json.dumps({
        "bids": [{"price": "0.44", "size": "1000"}, {"price": "0.43", "size": "5000"}],
        "asks": [{"price": "0.46", "size": "800"},  {"price": "0.47", "size": "4000"}],
    }))

    return d


def test_get_markets_parses_normalized(replay_dir):
    c = PolymarketClient(replay_dir=str(replay_dir))
    mkts = c.get_markets(active=True, limit=2, offset=0)
    assert len(mkts) == 2
    assert all(isinstance(m, PolymarketMarket) for m in mkts)
    a = mkts[0]
    assert a.condition_id == "0xCOND_A"
    assert a.is_binary
    assert a.outcomes[0].label == "Yes"
    assert a.outcomes[0].token_id == "111"
    assert a.outcomes[1].token_id == "222"
    assert a.volume_24h_usd == 5000.0
    assert a.liquidity_usd == 12000.0
    # Multi-outcome support — must NOT be flagged as binary.
    b = mkts[1]
    assert not b.is_binary
    assert len(b.outcomes) == 3


def test_get_market_by_condition_id(replay_dir):
    c = PolymarketClient(replay_dir=str(replay_dir))
    m = c.get_market("0xCOND_A")
    assert m.condition_id == "0xCOND_A"
    assert m.question == "Will X happen?"


def test_get_wallet_trades_normalizes(replay_dir):
    c = PolymarketClient(replay_dir=str(replay_dir))
    trades = c.get_wallet_trades("0xWALLET", limit=500)
    assert len(trades) == 2
    assert all(isinstance(t, PolymarketTrade) for t in trades)
    t0 = trades[0]
    assert t0.wallet == "0xwallet"  # normalized to lowercase
    assert t0.condition_id == "0xCOND_A"
    assert t0.outcome_index == 0
    assert t0.outcome_token_id == "111"
    assert t0.side == "BUY"
    assert t0.size_shares == 100.0
    assert t0.price == 0.45
    assert t0.notional_usd == pytest.approx(45.0)
    assert t0.tx_hash == "0xTX1"


def test_get_leaderboard_passthrough(replay_dir):
    c = PolymarketClient(replay_dir=str(replay_dir))
    rows = c.get_leaderboard(window="month", metric="profit", limit=50)
    assert len(rows) == 2
    assert rows[0]["wallet"] == "0xA"


def test_get_orderbook_passthrough(replay_dir):
    c = PolymarketClient(replay_dir=str(replay_dir))
    book = c.get_orderbook("111")
    assert "bids" in book and "asks" in book
    assert book["asks"][0]["price"] == "0.46"


def test_replay_missing_fixture_raises_clearly(replay_dir):
    c = PolymarketClient(replay_dir=str(replay_dir))
    with pytest.raises(PolymarketAPIError, match="Replay fixture missing"):
        c.get_market("0xDOES_NOT_EXIST")


def test_fixture_filename_is_deterministic():
    a = _fixture_filename("/markets", {"limit": 10, "offset": 0})
    b = _fixture_filename("/markets", {"offset": 0, "limit": 10})  # different key order
    assert a == b  # sort_keys=True
    assert _fixture_filename("/markets", None) == "markets.json"
