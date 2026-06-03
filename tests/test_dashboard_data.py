"""Tests for the dashboard data layer."""

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from dashboard import data as dd


def _make_paper_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placed_at TEXT NOT NULL,
            strategy TEXT, ticker TEXT, side TEXT, action TEXT,
            contracts INTEGER, price REAL, is_maker INTEGER,
            fees REAL, cost REAL, notes TEXT
        );
        CREATE TABLE paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, side TEXT, contracts INTEGER, avg_price REAL,
            opened_at TEXT, closed_at TEXT, realized_pnl REAL, notes TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO paper_trades(placed_at,strategy,ticker,side,action,contracts,price,is_maker,fees,cost) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("2026-06-02T12:00:00Z", "arb", "KX-1", "YES", "buy",  10, 0.45, 0, 0.05, 4.50),
            ("2026-06-02T12:00:00Z", "arb", "KX-1", "NO",  "buy",  10, 0.50, 0, 0.05, 5.00),
            ("2026-06-02T13:00:00Z", "arb", "KX-1", "YES", "sell", 10, 0.55, 0, 0.05, 5.50),
        ],
    )
    conn.execute(
        "INSERT INTO paper_positions(ticker,side,contracts,avg_price,opened_at,closed_at,realized_pnl) "
        "VALUES (?,?,?,?,?,?,?)",
        ("KX-2", "YES", 5, 0.40, "2026-06-02T11:00:00Z", None, 0.0),
    )
    conn.commit()
    conn.close()


def _make_calibration_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT, ticker TEXT, side TEXT,
            predicted_prob REAL, price_at_decision REAL,
            decided_at TEXT, resolved_at TEXT, outcome INTEGER, notes TEXT
        );
        CREATE TABLE strategy_state (
            strategy TEXT PRIMARY KEY, enabled INTEGER, last_brier REAL,
            last_n_samples INTEGER, last_evaluated_at TEXT, disabled_reason TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO predictions(strategy,ticker,side,predicted_prob,price_at_decision,decided_at,resolved_at,outcome) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            ("arb", "K1", "YES", 0.9, 0.5, "2026-06-01", "2026-06-02", 1),
            ("arb", "K2", "YES", 0.8, 0.5, "2026-06-01", "2026-06-02", 1),
            ("bad", "K3", "YES", 0.9, 0.5, "2026-06-01", "2026-06-02", 0),
            ("bad", "K4", "YES", 0.9, 0.5, "2026-06-01", None, None),
        ],
    )
    conn.execute(
        "INSERT INTO strategy_state(strategy,enabled,last_brier,last_n_samples,last_evaluated_at,disabled_reason) "
        "VALUES (?,?,?,?,?,?)",
        ("bad", 0, 0.81, 30, "2026-06-02", "Brier > 0.25"),
    )
    conn.commit()
    conn.close()


def test_load_trades_and_positions(tmp_path):
    db = tmp_path / "paper.db"
    _make_paper_db(db)
    trades = dd.load_trades(str(db))
    positions = dd.load_positions(str(db))
    assert len(trades) == 3
    assert len(positions) == 1
    assert pd.api.types.is_datetime64_any_dtype(trades["placed_at"])


def test_load_missing_db_is_empty(tmp_path):
    assert dd.load_trades(str(tmp_path / "nope.db")).empty
    assert dd.load_positions(str(tmp_path / "nope.db")).empty
    assert dd.load_predictions(str(tmp_path / "nope.db")).empty
    assert dd.load_strategy_state(str(tmp_path / "nope.db")).empty


def test_equity_curve_math(tmp_path):
    db = tmp_path / "paper.db"
    _make_paper_db(db)
    trades = dd.load_trades(str(db))
    eq = dd.compute_equity_curve(trades, starting_bankroll=10_000.0)
    assert not eq.empty
    # buys: -4.50-.05 -5.00-.05 = -9.60; sell: +5.50-.05 = +5.45
    expected_final = 10_000.0 - 4.50 - 0.05 - 5.00 - 0.05 + 5.50 - 0.05
    assert abs(eq["cash"].iloc[-1] - expected_final) < 1e-6


def test_equity_curve_empty():
    out = dd.compute_equity_curve(pd.DataFrame(), starting_bankroll=10_000.0)
    assert out.empty


def test_brier_by_strategy(tmp_path):
    db = tmp_path / "cal.db"
    _make_calibration_db(db)
    preds = dd.load_predictions(str(db))
    brier = dd.brier_by_strategy(preds)
    assert set(brier["strategy"]) == {"arb", "bad"}
    arb = brier[brier["strategy"] == "arb"].iloc[0]
    bad = brier[brier["strategy"] == "bad"].iloc[0]
    # arb: (.9-1)^2=.01, (.8-1)^2=.04 → mean .025
    assert abs(arb["brier"] - 0.025) < 1e-6
    # bad: only resolved row is (.9-0)^2=.81; unresolved row excluded
    assert abs(bad["brier"] - 0.81) < 1e-6
    assert bad["n_resolved"] == 1


def test_brier_empty_inputs():
    assert dd.brier_by_strategy(pd.DataFrame()).empty
    only_unresolved = pd.DataFrame({
        "strategy": ["x"], "id": [1], "predicted_prob": [0.5], "outcome": [None],
    })
    assert dd.brier_by_strategy(only_unresolved).empty


def test_load_strategy_state(tmp_path):
    db = tmp_path / "cal.db"
    _make_calibration_db(db)
    ss = dd.load_strategy_state(str(db))
    assert len(ss) == 1
    assert ss.iloc[0]["enabled"] == 0
    assert ss.iloc[0]["strategy"] == "bad"
