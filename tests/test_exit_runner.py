import os
import tempfile

from src.jobs.exit_runner import run_once
from src.paper.executor import PaperExecutor
from src.utils.fee_models import PolymarketFeeModel


class FakePolyClient:
    def __init__(self, bids):
        self.bids = bids

    def get_orderbook(self, token_id):
        return {"bids": self.bids[token_id], "asks": []}


def _executor(db):
    return PaperExecutor(
        db,
        starting_bankroll=1000.0,
        fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
    )


def test_exit_runner_takes_profit():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        ex = _executor(db)
        ex.execute_leg(
            strategy="polymarket_momentum", venue="polymarket",
            ticker="YES_T", side="YES", action="buy",
            contracts=10, price=0.70,
        )
        client = FakePolyClient({"YES_T": [{"price": "0.88", "size": "10"}]})
        summary = run_once(
            client=client, executor=ex, db_path=db,
            take_profit_cents=0.15, profit_capture=0.9,
            stop_loss_cents=0.12, stop_loss_fraction=0.5,
            min_bid_size=1,
        )
        assert summary.exited == 1
        assert ex.portfolio.positions == {}


def test_exit_runner_stops_loss():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        ex = _executor(db)
        ex.execute_leg(
            strategy="polymarket_momentum", venue="polymarket",
            ticker="YES_T", side="YES", action="buy",
            contracts=10, price=0.70,
        )
        client = FakePolyClient({"YES_T": [{"price": "0.55", "size": "10"}]})
        summary = run_once(
            client=client, executor=ex, db_path=db,
            take_profit_cents=0.15, profit_capture=0.9,
            stop_loss_cents=0.12, stop_loss_fraction=0.5,
            min_bid_size=1,
        )
        assert summary.exited == 1
        assert ex.portfolio.positions == {}


def test_exit_runner_holds_when_no_rule_fires():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        ex = _executor(db)
        ex.execute_leg(
            strategy="polymarket_momentum", venue="polymarket",
            ticker="YES_T", side="YES", action="buy",
            contracts=10, price=0.70,
        )
        client = FakePolyClient({"YES_T": [{"price": "0.76", "size": "10"}]})
        summary = run_once(
            client=client, executor=ex, db_path=db,
            take_profit_cents=0.15, profit_capture=0.9,
            stop_loss_cents=0.12, stop_loss_fraction=0.5,
            min_bid_size=1,
        )
        assert summary.exited == 0
        assert summary.skipped == 1
        assert "polymarket:YES_T:YES" in ex.portfolio.positions
