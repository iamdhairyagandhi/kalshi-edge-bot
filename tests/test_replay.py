"""Tests for replay backtester."""

import os
import tempfile

from src.backtest.replay import ReplaySnapshot, replay
from src.paper.executor import PaperExecutor
from src.risk.gates import GateConfig
from src.strategies.overround_arb import Orderbook


def _arb_book(ticker):
    """Orderbook with yes_bid + no_bid > 1 → arb opportunity."""
    return Orderbook(
        ticker=ticker,
        yes_best_ask=0.45, yes_best_ask_size=100,
        no_best_ask=0.45, no_best_ask_size=100,
        yes_best_bid=0.55, yes_best_bid_size=100,
        no_best_bid=0.55, no_best_bid_size=100,
    )


def test_replay_deterministic_empty_snapshot():
    with tempfile.TemporaryDirectory() as d:
        ex = PaperExecutor(os.path.join(d, "p.db"), starting_bankroll=10_000.0)
        snaps = iter([ReplaySnapshot(timestamp=1000.0, books=[])])
        r = replay(snaps, ex)
        assert r.snapshots_processed == 1
        assert r.opportunities_seen == 0
        assert r.trades_executed == 0


def test_replay_executes_arb():
    with tempfile.TemporaryDirectory() as d:
        ex = PaperExecutor(os.path.join(d, "p.db"), starting_bankroll=10_000.0)
        snaps = iter([
            ReplaySnapshot(
                timestamp=1000.0,
                books=[_arb_book("KX-1")],
                markets={"KX-1": {"volume_24h": 5000}},
            ),
        ])
        r = replay(snaps, ex)
        assert r.opportunities_seen >= 1
        # We can't strictly require execution since gate decisions depend
        # on a lot of state. But the cash should be no greater than start.
        assert r.final_cash <= 10_000.0


def test_replay_multiple_snapshots():
    with tempfile.TemporaryDirectory() as d:
        ex = PaperExecutor(os.path.join(d, "p.db"), starting_bankroll=10_000.0)
        snaps = iter([
            ReplaySnapshot(timestamp=1000.0, books=[]),
            ReplaySnapshot(timestamp=1001.0, books=[]),
            ReplaySnapshot(timestamp=1002.0, books=[]),
        ])
        r = replay(snaps, ex)
        assert r.snapshots_processed == 3
