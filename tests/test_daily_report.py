"""Tests for daily report generator."""

import os
import tempfile

from src.jobs.daily_report import generate_report
from src.paper.executor import PaperExecutor
from src.strategies.overround_arb import ArbOpportunity


def test_empty_report_runs():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "p.db")
        ex = PaperExecutor(db, starting_bankroll=10_000.0)
        r = generate_report(
            paper_db_path=db,
            cash=ex.portfolio.cash,
            open_positions_cost=0.0,
            starting_bankroll=10_000.0,
        )
        assert r.n_trades_today == 0
        assert r.invariant_ok
        assert "KALSHI EDGE BOT" in r.to_text()


def test_report_counts_fills():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "p.db")
        ex = PaperExecutor(db, starting_bankroll=10_000.0)
        # Execute a small arb
        opp = ArbOpportunity(
            ticker="KX-1",
            direction="buy_both",
            contracts=5,
            yes_price=0.45,
            no_price=0.50,
            gross_edge_per_contract=0.05,
            fees_per_contract=0.01,
            net_edge_per_contract=0.04,
            net_profit_total=0.20,
        )
        ex.execute_arb(opp)
        r = generate_report(
            paper_db_path=db,
            cash=ex.portfolio.cash,
            open_positions_cost=sum(p.cost for p in ex.portfolio.positions.values()),
            starting_bankroll=10_000.0,
        )
        assert r.n_trades_today >= 2  # two legs


def test_report_includes_strategy_state():
    with tempfile.TemporaryDirectory() as d:
        paper_db = os.path.join(d, "p.db")
        cal_db = os.path.join(d, "c.db")
        ex = PaperExecutor(paper_db, starting_bankroll=10_000.0)
        from src.risk.strategy_kill import disable
        disable(cal_db, "test-strat", reason="test")

        r = generate_report(
            paper_db_path=paper_db,
            cash=ex.portfolio.cash,
            open_positions_cost=0.0,
            starting_bankroll=10_000.0,
            calibration_db_path=cal_db,
        )
        names = [s["strategy"] for s in r.strategy_states]
        assert "test-strat" in names
        assert "DISABLED" in r.to_text()
