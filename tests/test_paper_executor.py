"""Tests for the paper executor."""

import os
import tempfile

import pytest

from src.paper.executor import PaperExecutor
from src.strategies.overround_arb import ArbOpportunity


@pytest.fixture
def executor():
    with tempfile.TemporaryDirectory() as d:
        yield PaperExecutor(os.path.join(d, "paper.db"), starting_bankroll=1000.0)


def test_initial_state(executor):
    assert executor.portfolio.cash == 1000.0
    assert executor.portfolio.bankroll == 1000.0
    assert executor.portfolio.positions == {}


def test_buy_leg_deducts_cash_and_fees(executor):
    executor.execute_leg(
        strategy="test", ticker="KXTEST-1", side="YES",
        action="buy", contracts=10, price=0.50, is_maker=False,
    )
    # cost $5 + taker fee ceil(0.07*10*0.25) = $0.18 -> cash 994.82
    assert executor.portfolio.cash == pytest.approx(994.82, abs=0.01)
    assert "kalshi:KXTEST-1:YES" in executor.portfolio.positions


def test_maker_order_no_fees(executor):
    executor.execute_leg(
        strategy="test", ticker="K1", side="NO",
        action="buy", contracts=10, price=0.30, is_maker=True,
    )
    # cost $3, no fee -> cash 997.00
    assert executor.portfolio.cash == pytest.approx(997.0, abs=1e-9)


def test_insufficient_cash_raises(executor):
    with pytest.raises(ValueError, match="Insufficient"):
        executor.execute_leg(
            strategy="test", ticker="K1", side="YES",
            action="buy", contracts=5000, price=0.50,
        )


def test_buy_then_sell_realizes_pnl(executor):
    executor.execute_leg(strategy="t", ticker="K1", side="YES",
                         action="buy", contracts=10, price=0.40, is_maker=True)
    executor.execute_leg(strategy="t", ticker="K1", side="YES",
                         action="sell", contracts=10, price=0.60, is_maker=True)
    # Realized = (0.60 - 0.40) * 10 = $2.00, no fees (maker)
    assert executor.portfolio.realized_pnl == pytest.approx(2.0, abs=1e-9)
    assert "kalshi:K1:YES" not in executor.portfolio.positions


def test_buy_more_blends_avg_price(executor):
    executor.execute_leg(strategy="t", ticker="K1", side="YES",
                         action="buy", contracts=10, price=0.40, is_maker=True)
    executor.execute_leg(strategy="t", ticker="K1", side="YES",
                         action="buy", contracts=10, price=0.60, is_maker=True)
    pos = executor.portfolio.positions["kalshi:K1:YES"]
    assert pos.contracts == 20
    assert pos.avg_price == pytest.approx(0.50, abs=1e-9)


def test_execute_arb_buy_both(executor):
    opp = ArbOpportunity(
        ticker="K1", direction="buy_both", contracts=10,
        yes_price=0.40, no_price=0.40,
        gross_edge_per_contract=0.20, fees_per_contract=0.04,
        net_edge_per_contract=0.15, net_profit_total=1.50,
    )
    fills = executor.execute_arb(opp)
    assert len(fills) == 2
    assert "kalshi:K1:YES" in executor.portfolio.positions
    assert "kalshi:K1:NO" in executor.portfolio.positions
    # We hold both sides — guaranteed $1/contract at settlement
    yes_pos = executor.portfolio.positions["kalshi:K1:YES"]
    no_pos = executor.portfolio.positions["kalshi:K1:NO"]
    assert yes_pos.contracts == 10
    assert no_pos.contracts == 10


def test_sell_more_than_held_raises(executor):
    executor.execute_leg(strategy="t", ticker="K1", side="YES",
                         action="buy", contracts=5, price=0.50, is_maker=True)
    with pytest.raises(ValueError, match="Selling more"):
        executor.execute_leg(strategy="t", ticker="K1", side="YES",
                             action="sell", contracts=10, price=0.60)
