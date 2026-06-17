import os
import sqlite3
import tempfile

import pytest

from src.clients.polymarket import PolymarketMarket, PolymarketOutcome, PolymarketTrade
from src.jobs.momentum_runner import run_once
from src.paper.executor import PaperExecutor
from src.strategies.polymarket_momentum import detect_momentum_signals
from src.utils.fee_models import PolymarketFeeModel


NOW = 1_800_000_000


class FakePolyClient:
    def __init__(self, orderbooks):
        self.orderbooks = orderbooks

    def get_orderbook(self, token_id):
        return self.orderbooks[token_id]


def trade(wallet, price, ts, size=100.0, side="BUY"):
    return PolymarketTrade(
        wallet=wallet.lower(),
        condition_id="0xMOM",
        outcome_index=0,
        outcome_token_id="YES_T",
        side=side,
        size_shares=size,
        price=price,
        timestamp_unix=ts,
        market_title="Momentum market",
        outcome_label="Yes",
    )


def sports_trade(wallet, price, ts, size=100.0, side="BUY"):
    t = trade(wallet, price, ts, size=size, side=side)
    return PolymarketTrade(
        wallet=t.wallet,
        condition_id=t.condition_id,
        outcome_index=t.outcome_index,
        outcome_token_id=t.outcome_token_id,
        side=t.side,
        size_shares=t.size_shares,
        price=t.price,
        timestamp_unix=t.timestamp_unix,
        market_title="Libema Open: Player A vs Player B",
        outcome_label=t.outcome_label,
    )


def market():
    return PolymarketMarket(
        condition_id="0xMOM",
        question_id=None,
        slug=None,
        question="Momentum market",
        closed=False,
        accepting_orders=True,
        outcomes=[PolymarketOutcome(0, "Yes", "YES_T"), PolymarketOutcome(1, "No", "NO_T")],
    )


def test_detect_momentum_requires_late_vwap_move():
    trades = {
        "0xa": [trade("0xa", 0.40, NOW - 50 * 60), trade("0xa", 0.47, NOW - 5 * 60)],
        "0xb": [trade("0xb", 0.41, NOW - 45 * 60), trade("0xb", 0.48, NOW - 4 * 60)],
    }
    signals = detect_momentum_signals(
        trades_by_wallet=trades,
        markets={"0xMOM": market()},
        now_unix=NOW,
        lookback_minutes=60,
        min_buy_trades=3,
        min_unique_wallets=2,
        min_notional_usd=100.0,
        min_price_move=0.03,
    )
    assert len(signals) == 1
    assert signals[0].price_move == pytest.approx(0.07)
    assert signals[0].unique_wallets == 2


def test_momentum_runner_fills_when_live_ask_near_vwap():
    trades = {
        "0xa": [trade("0xa", 0.40, NOW - 50 * 60), trade("0xa", 0.47, NOW - 5 * 60)],
        "0xb": [trade("0xb", 0.41, NOW - 45 * 60), trade("0xb", 0.48, NOW - 4 * 60)],
    }
    client = FakePolyClient({"YES_T": {"asks": [{"price": "0.455", "size": "100"}]}})
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db,
            starting_bankroll=1000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client,
            executor=executor,
            db_path=db,
            candidate_wallets=list(trades),
            candidate_trades=trades,
            now_unix=NOW,
            lookback_minutes=60,
            min_buy_trades=3,
            min_unique_wallets=2,
            min_notional_usd=100.0,
            min_price_move=0.03,
            max_entry_premium=0.03,
            notional_per_signal_usd=10.0,
            execute=True,
            exclude_terms="",
            require_positive_history=False,
        )
        assert summary.signals_filled == 1
        assert "polymarket:YES_T:YES" in executor.portfolio.positions


def test_momentum_runner_rejects_overpriced_live_ask():
    trades = {
        "0xa": [trade("0xa", 0.40, NOW - 50 * 60), trade("0xa", 0.47, NOW - 5 * 60)],
        "0xb": [trade("0xb", 0.41, NOW - 45 * 60), trade("0xb", 0.48, NOW - 4 * 60)],
    }
    client = FakePolyClient({"YES_T": {"asks": [{"price": "0.60", "size": "100"}]}})
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db,
            starting_bankroll=1000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client,
            executor=executor,
            db_path=db,
            candidate_wallets=list(trades),
            candidate_trades=trades,
            now_unix=NOW,
            lookback_minutes=60,
            min_buy_trades=3,
            min_unique_wallets=2,
            min_notional_usd=100.0,
            min_price_move=0.03,
            max_entry_premium=0.03,
            notional_per_signal_usd=10.0,
            execute=True,
            exclude_terms="",
            require_positive_history=False,
        )
        assert summary.signals_rejected == 1
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT reason, metric_value, threshold_value FROM trade_diagnostics"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "premium"
        assert row[1] > row[2]


def test_momentum_runner_observes_by_default_instead_of_filling():
    trades = {
        "0xa": [trade("0xa", 0.40, NOW - 50 * 60), trade("0xa", 0.47, NOW - 5 * 60)],
        "0xb": [trade("0xb", 0.41, NOW - 45 * 60), trade("0xb", 0.48, NOW - 4 * 60)],
    }
    client = FakePolyClient({"YES_T": {"asks": [{"price": "0.455", "size": "100"}]}})
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db,
            starting_bankroll=1000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client,
            executor=executor,
            db_path=db,
            candidate_wallets=list(trades),
            candidate_trades=trades,
            now_unix=NOW,
            lookback_minutes=60,
            min_buy_trades=3,
            min_unique_wallets=2,
            min_notional_usd=100.0,
            min_price_move=0.03,
            max_entry_premium=0.03,
            notional_per_signal_usd=10.0,
        )
        assert summary.signals_observed == 1
        assert executor.portfolio.positions == {}


def test_momentum_runner_blocks_sports_universe():
    trades = {
        "0xa": [sports_trade("0xa", 0.40, NOW - 50 * 60), sports_trade("0xa", 0.47, NOW - 5 * 60)],
        "0xb": [sports_trade("0xb", 0.41, NOW - 45 * 60), sports_trade("0xb", 0.48, NOW - 4 * 60)],
    }
    client = FakePolyClient({"YES_T": {"asks": [{"price": "0.455", "size": "100"}]}})
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "x.db")
        executor = PaperExecutor(
            db,
            starting_bankroll=1000.0,
            fee_models={"polymarket": PolymarketFeeModel(gas_usd=0.0)},
        )
        summary = run_once(
            client=client,
            executor=executor,
            db_path=db,
            candidate_wallets=list(trades),
            candidate_trades=trades,
            now_unix=NOW,
            lookback_minutes=60,
            min_buy_trades=3,
            min_unique_wallets=2,
            min_notional_usd=100.0,
            min_price_move=0.03,
            max_entry_premium=0.03,
            notional_per_signal_usd=10.0,
            execute=True,
            exclude_terms="",
            require_positive_history=False,
        )
        assert summary.signals_rejected == 1
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT reason, details FROM trade_diagnostics"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "category_sports"
        assert "category=sports" in row[1]
