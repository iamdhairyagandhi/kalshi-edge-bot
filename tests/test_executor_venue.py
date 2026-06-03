"""Tests for the venue-aware executor migration + cross-venue isolation."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from src.paper.executor import PaperExecutor, position_key
from src.paper.migrations import add_venue_columns
from src.utils.fee_models import PolymarketFeeModel


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_migration_adds_venue_column_to_legacy_db():
    """A pre-venue DB (no venue column, rows already present) must upgrade
    cleanly and default existing rows to 'kalshi'."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "legacy.db")
        # Build the *old* schema by hand and insert a legacy row.
        conn = sqlite3.connect(db)
        try:
            conn.executescript(
                """
                CREATE TABLE paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    placed_at TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    action TEXT NOT NULL,
                    contracts INTEGER NOT NULL,
                    price REAL NOT NULL,
                    is_maker INTEGER NOT NULL,
                    fees REAL NOT NULL,
                    cost REAL NOT NULL,
                    notes TEXT
                );
                CREATE TABLE paper_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    contracts INTEGER NOT NULL,
                    avg_price REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    realized_pnl REAL DEFAULT 0,
                    notes TEXT
                );
                INSERT INTO paper_trades(placed_at,strategy,ticker,side,action,contracts,price,is_maker,fees,cost)
                    VALUES('2024-01-01T00:00:00Z','arb','KXOLD','YES','buy',10,0.5,0,0.18,5.0);
                """
            )
            conn.commit()
        finally:
            conn.close()
        # Construct the executor — this should run migrations.
        ex = PaperExecutor(db, starting_bankroll=1000.0)
        conn = sqlite3.connect(db)
        try:
            assert "venue" in _columns(conn, "paper_trades")
            assert "venue" in _columns(conn, "paper_positions")
            (venue,) = conn.execute(
                "SELECT venue FROM paper_trades WHERE ticker='KXOLD'"
            ).fetchone()
            assert venue == "kalshi"
        finally:
            conn.close()
        # And we can still write a new row.
        ex.execute_leg(
            strategy="t", ticker="KX1", side="YES", action="buy",
            contracts=1, price=0.5, is_maker=True,
        )


def test_migration_is_idempotent():
    """Running migrations twice must not double-add columns or fail."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "x.db")
        PaperExecutor(db, starting_bankroll=100.0)
        conn = sqlite3.connect(db)
        try:
            add_venue_columns(conn)
            add_venue_columns(conn)
            assert "venue" in _columns(conn, "paper_trades")
        finally:
            conn.close()


def test_cross_venue_positions_do_not_collide():
    """A position with the same ticker/side on two venues must be tracked
    independently (the rubber-duck blocker on the executor)."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "x.db")
        ex = PaperExecutor(db, starting_bankroll=1000.0,
                           fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)})
        ex.execute_leg(strategy="t", ticker="X", side="YES", action="buy",
                       contracts=10, price=0.4, is_maker=True, venue="kalshi")
        ex.execute_leg(strategy="t", ticker="X", side="YES", action="buy",
                       contracts=10, price=0.6, is_maker=True, venue="polymarket")
        assert "kalshi:X:YES" in ex.portfolio.positions
        assert "polymarket:X:YES" in ex.portfolio.positions
        assert ex.portfolio.positions["kalshi:X:YES"].avg_price == pytest.approx(0.4)
        assert ex.portfolio.positions["polymarket:X:YES"].avg_price == pytest.approx(0.6)


def test_polymarket_fee_model_charges_flat_gas():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "x.db")
        ex = PaperExecutor(
            db, starting_bankroll=100.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.25)},
        )
        fill = ex.execute_leg(
            strategy="copy", ticker="0xCOND", side="YES", action="buy",
            contracts=10, price=0.5, is_maker=False, venue="polymarket",
        )
        assert fill["fees"] == pytest.approx(0.25)
        # cash deducted = 10*0.5 + 0.25
        assert ex.portfolio.cash == pytest.approx(100.0 - 5.25)


def test_position_key_helper():
    assert position_key("kalshi", "K1", "YES") == "kalshi:K1:YES"
    assert position_key("polymarket", "0xabc", "NO") == "polymarket:0xabc:NO"


def test_kalshi_default_path_unchanged():
    """Smoke test: an executor constructed the legacy way (no fee_models, no
    venue arg on execute_leg) must behave exactly like before — same fees,
    same cash, just venue-prefixed keys."""
    with tempfile.TemporaryDirectory() as d:
        ex = PaperExecutor(os.path.join(d, "x.db"), starting_bankroll=1000.0)
        fill = ex.execute_leg(
            strategy="t", ticker="K1", side="YES", action="buy",
            contracts=10, price=0.5, is_maker=False,
        )
        # Legacy fee was ceil(0.07*10*0.5*0.5*100)/100 = 0.18
        assert fill["fees"] == pytest.approx(0.18)
        assert "kalshi:K1:YES" in ex.portfolio.positions
