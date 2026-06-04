"""Tests for cohort snapshot persistence written by copy_runner."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Dict, List

import pytest

from src.clients.polymarket import PolymarketTrade
from src.jobs.copy_runner import run_once, _record_cohort_snapshot
from src.paper.executor import PaperExecutor
from src.signals.smart_money import WalletScore
from src.utils.fee_models import PolymarketFeeModel

# Reuse the harness from test_copy_runner.
from tests.test_copy_runner import (  # type: ignore
    FakePolyClient, mk_market, _profitable_history, _trade, NOW,
)


def _scores(wallets):
    return [
        WalletScore(
            wallet=w, score=100.0 - i, realized_pnl_usd=1000.0 - i * 10,
            n_trades=80, n_resolved=30, last_trade_unix=NOW - 60,
            pnl_stability=0.8,
        )
        for i, w in enumerate(wallets)
    ]


def test_record_cohort_snapshot_writes_rows():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        # Initialize schema by constructing an executor; that also creates
        # the cohort_snapshots table via copy_runner imports? No — only
        # paper_trades. So we need to ensure cohort table via SIGNAL_LOG_SCHEMA.
        PaperExecutor(db, starting_bankroll=1000.0)
        from src.jobs.copy_runner import SIGNAL_LOG_SCHEMA
        c = sqlite3.connect(db)
        try:
            c.executescript(SIGNAL_LOG_SCHEMA)
            c.commit()
        finally:
            c.close()

        wallets = [f"0xW{i}" for i in range(5)]
        _record_cohort_snapshot(db, "ver-1", _scores(wallets))

        c = sqlite3.connect(db)
        try:
            rows = c.execute(
                "SELECT wallet, rank, score FROM polymarket_cohort_snapshots "
                "ORDER BY rank"
            ).fetchall()
        finally:
            c.close()
        assert len(rows) == 5
        assert rows[0] == ("0xW0", 1, 100.0)
        assert rows[4] == ("0xW4", 5, 96.0)


def test_record_cohort_snapshot_idempotent_per_version():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        PaperExecutor(db, starting_bankroll=1000.0)
        from src.jobs.copy_runner import SIGNAL_LOG_SCHEMA
        c = sqlite3.connect(db)
        try:
            c.executescript(SIGNAL_LOG_SCHEMA)
            c.commit()
        finally:
            c.close()

        wallets = [f"0xW{i}" for i in range(3)]
        _record_cohort_snapshot(db, "ver-A", _scores(wallets))
        _record_cohort_snapshot(db, "ver-A", _scores(wallets))   # dup
        _record_cohort_snapshot(db, "ver-B", _scores(wallets[:2]))

        c = sqlite3.connect(db)
        try:
            total = c.execute("SELECT COUNT(*) FROM polymarket_cohort_snapshots").fetchone()[0]
            versions = c.execute(
                "SELECT cohort_version, COUNT(*) FROM polymarket_cohort_snapshots GROUP BY cohort_version"
            ).fetchall()
        finally:
            c.close()
        assert total == 3 + 2  # version A: 3 wallets (only first write), version B: 2 wallets
        assert dict(versions) == {"ver-A": 3, "ver-B": 2}


def test_run_once_records_snapshot():
    cohort = [f"0xW{i}" for i in range(5)]
    trades_by_wallet: Dict[str, List[PolymarketTrade]] = {}
    for w in cohort:
        history = _profitable_history(w)
        recent = [_trade(w, "BUY", 100, 0.45, NOW - 1000)]
        trades_by_wallet[w.lower()] = history + recent

    resolved = [f"RES_{i}" for i in range(30)]
    markets = {"0xC1": mk_market("0xC1")}

    orderbook = {
        "asks": [{"price": "0.46", "size": "200"}],
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
        run_once(
            client=client, executor=executor, db_path=db,
            candidate_wallets=cohort, resolved_condition_ids=resolved,
            markets=markets, now_unix=NOW,
            top_n=5, consensus_k=3, lookback_hours=24,
            max_slippage_cents=0.05, notional_per_signal_usd=20.0,
        )

        c = sqlite3.connect(db)
        try:
            rows = c.execute(
                "SELECT wallet, rank, realized_pnl_usd, n_trades "
                "FROM polymarket_cohort_snapshots ORDER BY rank"
            ).fetchall()
        finally:
            c.close()
        assert len(rows) == 5
        # Ranks are 1..5
        assert [r[1] for r in rows] == [1, 2, 3, 4, 5]
        # PnL should be > 0 because _profitable_history generates winners.
        assert all(r[2] > 0 for r in rows)
