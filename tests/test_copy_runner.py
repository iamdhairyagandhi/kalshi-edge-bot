"""Integration tests for the Polymarket consensus-copy runner."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Dict, List, Sequence

import pytest

from src.clients.polymarket import (
    PolymarketMarket, PolymarketOutcome, PolymarketTrade,
)
from src.jobs.copy_runner import run_once
from src.paper.executor import PaperExecutor
from src.utils.fee_models import PolymarketFeeModel


NOW = 1_700_500_000
WIN_LOOKBACK_H = 24


# ---------------------------------------------------------------------------
# Fake Polymarket client — drives the runner without HTTP.
# ---------------------------------------------------------------------------


class FakePolyClient:
    def __init__(self, *, trades_by_wallet=None, orderbooks=None):
        self.trades_by_wallet: Dict[str, List[PolymarketTrade]] = trades_by_wallet or {}
        self.orderbooks: Dict[str, dict] = orderbooks or {}
        self.book_calls: List[str] = []

    def get_wallet_trades(self, wallet, *, limit=500, since_unix=None):
        trades = self.trades_by_wallet.get(wallet.lower(), [])
        if since_unix is not None:
            trades = [t for t in trades if t.timestamp_unix >= since_unix]
        return trades[:limit]

    def get_orderbook(self, token_id):
        self.book_calls.append(token_id)
        if token_id not in self.orderbooks:
            raise RuntimeError(f"no book for {token_id}")
        return self.orderbooks[token_id]


def mk_market(cond="0xC1") -> PolymarketMarket:
    return PolymarketMarket(
        condition_id=cond, question_id=None, slug="x", question="Will X?",
        closed=False, accepting_orders=True,
        outcomes=[PolymarketOutcome(0, "Yes", "YES_T"), PolymarketOutcome(1, "No", "NO_T")],
    )


def _trade(wallet, side, size, price, ts, cond="0xC1", token="YES_T", idx=0):
    return PolymarketTrade(
        wallet=wallet.lower(), condition_id=cond, outcome_index=idx,
        outcome_token_id=token, side=side, size_shares=size, price=price,
        timestamp_unix=ts,
    )


def _profitable_history(wallet, n_pairs=30, base_ts=NOW - 30 * 86400):
    """Build a wallet history with enough trades + resolved markets to be
    cohort-eligible, with positive realized PnL."""
    trades = []
    for i in range(n_pairs):
        cond = f"RES_{i}"
        ts = base_ts + i * 3600
        trades.append(_trade(wallet, "BUY", 100, 0.40, ts, cond=cond, token=f"{cond}_T"))
        trades.append(_trade(wallet, "SELL", 100, 0.70, ts + 60, cond=cond, token=f"{cond}_T"))
    return trades


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


def test_runner_paper_fills_when_consensus_and_book_ok():
    cohort = [f"0xW{i}" for i in range(5)]

    # Each cohort wallet has a profitable history AND a recent BUY on 0xC1/YES_T
    trades_by_wallet: Dict[str, List[PolymarketTrade]] = {}
    for w in cohort:
        history = _profitable_history(w)
        recent = [_trade(w, "BUY", 100, 0.45, NOW - 1000)]
        trades_by_wallet[w.lower()] = history + recent

    # Resolved markets = the closed RES_* ids from the profitable history
    resolved = [f"RES_{i}" for i in range(30)]
    markets = {"0xC1": mk_market("0xC1")}

    orderbook = {
        "asks": [{"price": "0.46", "size": "200"}, {"price": "0.47", "size": "500"}],
        "bids": [{"price": "0.44", "size": "200"}],
    }
    client = FakePolyClient(
        trades_by_wallet=trades_by_wallet,
        orderbooks={"YES_T": orderbook},
    )

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db, starting_bankroll=10000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client, executor=executor, db_path=db,
            candidate_wallets=cohort, resolved_condition_ids=resolved,
            markets=markets, now_unix=NOW,
            top_n=5, consensus_k=3, lookback_hours=24,
            max_slippage_cents=0.05, notional_per_signal_usd=20.0,
        )
        assert summary.cohort_size == 5
        assert summary.signals_detected == 1
        assert summary.signals_new == 1
        assert summary.signals_filled == 1
        assert "polymarket:YES_T:YES" in executor.portfolio.positions

        # The fill MUST have been at the live ask price, not the wallet's price.
        pos = executor.portfolio.positions["polymarket:YES_T:YES"]
        assert pos.avg_price == pytest.approx(0.46)
        assert pos.contracts > 0

        # Decision row persisted with slippage measured.
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT decision, executed_price, slippage_cents "
                "FROM polymarket_consensus_signals"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "filled"
        assert row[1] == pytest.approx(0.46)
        # avg wallet entry was 0.45 → slippage 0.01
        assert row[2] == pytest.approx(0.01, abs=1e-6)


def test_runner_rejects_on_excessive_slippage():
    cohort = [f"0xW{i}" for i in range(5)]
    trades_by_wallet = {}
    for w in cohort:
        trades_by_wallet[w.lower()] = (
            _profitable_history(w) +
            [_trade(w, "BUY", 100, 0.40, NOW - 1000)]
        )
    resolved = [f"RES_{i}" for i in range(30)]
    markets = {"0xC1": mk_market()}
    # Ask is 10 cents above the wallets' avg entry — way over slippage cap.
    orderbook = {"asks": [{"price": "0.50", "size": "1000"}], "bids": []}
    client = FakePolyClient(trades_by_wallet=trades_by_wallet,
                            orderbooks={"YES_T": orderbook})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db, starting_bankroll=1000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client, executor=executor, db_path=db,
            candidate_wallets=cohort, resolved_condition_ids=resolved,
            markets=markets, now_unix=NOW,
            top_n=5, consensus_k=3, lookback_hours=24,
            max_slippage_cents=0.02, notional_per_signal_usd=20.0,
        )
        assert summary.signals_filled == 0
        assert summary.signals_rejected == 1
        assert executor.portfolio.positions == {}
        conn = sqlite3.connect(db)
        try:
            (decision,) = conn.execute(
                "SELECT decision FROM polymarket_consensus_signals"
            ).fetchone()
        finally:
            conn.close()
        assert decision == "rejected_slippage"


def test_runner_is_idempotent_across_two_runs():
    """A signal already in the decision log must NOT be re-evaluated or
    cause a second paper fill."""
    cohort = [f"0xW{i}" for i in range(5)]
    trades_by_wallet = {}
    for w in cohort:
        trades_by_wallet[w.lower()] = (
            _profitable_history(w) +
            [_trade(w, "BUY", 100, 0.45, NOW - 1000)]
        )
    resolved = [f"RES_{i}" for i in range(30)]
    markets = {"0xC1": mk_market()}
    orderbook = {"asks": [{"price": "0.46", "size": "200"}], "bids": []}
    client = FakePolyClient(trades_by_wallet=trades_by_wallet,
                            orderbooks={"YES_T": orderbook})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db, starting_bankroll=10000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        s1 = run_once(
            client=client, executor=executor, db_path=db,
            candidate_wallets=cohort, resolved_condition_ids=resolved,
            markets=markets, now_unix=NOW,
            top_n=5, consensus_k=3, lookback_hours=24,
            max_slippage_cents=0.05, notional_per_signal_usd=20.0,
        )
        s2 = run_once(
            client=client, executor=executor, db_path=db,
            candidate_wallets=cohort, resolved_condition_ids=resolved,
            markets=markets, now_unix=NOW,
            top_n=5, consensus_k=3, lookback_hours=24,
            max_slippage_cents=0.05, notional_per_signal_usd=20.0,
        )
        assert s1.signals_filled == 1
        assert s2.signals_filled == 0
        assert s2.signals_new == 0
        # Only one position with the original contracts.
        pos = executor.portfolio.positions["polymarket:YES_T:YES"]
        assert pos.contracts > 0
        conn = sqlite3.connect(db)
        try:
            (n,) = conn.execute(
                "SELECT COUNT(*) FROM polymarket_consensus_signals"
            ).fetchone()
        finally:
            conn.close()
        assert n == 1


def test_runner_handles_missing_orderbook_gracefully():
    cohort = [f"0xW{i}" for i in range(5)]
    trades_by_wallet = {}
    for w in cohort:
        trades_by_wallet[w.lower()] = (
            _profitable_history(w) +
            [_trade(w, "BUY", 100, 0.45, NOW - 1000)]
        )
    resolved = [f"RES_{i}" for i in range(30)]
    markets = {"0xC1": mk_market()}
    # No orderbook registered — the client will raise.
    client = FakePolyClient(trades_by_wallet=trades_by_wallet, orderbooks={})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db, starting_bankroll=1000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client, executor=executor, db_path=db,
            candidate_wallets=cohort, resolved_condition_ids=resolved,
            markets=markets, now_unix=NOW,
            top_n=5, consensus_k=3, lookback_hours=24,
            max_slippage_cents=0.05, notional_per_signal_usd=20.0,
        )
        assert summary.signals_filled == 0
        assert summary.signals_rejected == 1
        conn = sqlite3.connect(db)
        try:
            (decision,) = conn.execute(
                "SELECT decision FROM polymarket_consensus_signals"
            ).fetchone()
        finally:
            conn.close()
        assert decision == "rejected_no_book"
