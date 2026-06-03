"""Tests for smart-money cohort ranking + selection."""

from __future__ import annotations

from typing import List

import pytest

from src.clients.polymarket import PolymarketTrade
from src.signals.smart_money import (
    compute_wallet_stats,
    rank_wallets,
    score_wallet,
    select_cohort,
)


def mk_trade(
    wallet: str = "0xW",
    cond: str = "0xC",
    outcome_index: int = 0,
    token: str = "T0",
    side: str = "BUY",
    size: float = 100.0,
    price: float = 0.50,
    ts: int = 1_700_000_000,
) -> PolymarketTrade:
    return PolymarketTrade(
        wallet=wallet.lower(),
        condition_id=cond,
        outcome_index=outcome_index,
        outcome_token_id=token,
        side=side,
        size_shares=size,
        price=price,
        timestamp_unix=ts,
    )


# ---------------------------------------------------------------------------
# wallet stats
# ---------------------------------------------------------------------------


def test_compute_stats_basic():
    trades: List[PolymarketTrade] = [
        mk_trade(cond="A", token="A0", side="BUY", size=100, price=0.4, ts=1_700_000_000),
        mk_trade(cond="A", token="A0", side="SELL", size=100, price=0.7, ts=1_700_086_400),
        mk_trade(cond="B", token="B0", side="BUY", size=50, price=0.3, ts=1_700_172_800),
    ]
    stats = compute_wallet_stats("0xW", trades, resolved_condition_ids=["A"])
    assert stats.n_trades == 3
    assert stats.n_markets == 2
    assert stats.n_resolved_markets == 1
    # Realized on A only: sold 100*0.7 (+70) - bought 100*0.4 (-40) = +30
    assert stats.realized_pnl_usd == pytest.approx(30.0)
    assert stats.total_volume_usd == pytest.approx(40 + 70 + 15)
    assert stats.first_trade_unix == 1_700_000_000
    assert stats.last_trade_unix == 1_700_172_800


def test_compute_stats_no_resolved_means_zero_pnl():
    trades = [mk_trade(cond="A", side="BUY", size=100, price=0.4)]
    stats = compute_wallet_stats("0xW", trades, resolved_condition_ids=[])
    assert stats.n_resolved_markets == 0
    assert stats.realized_pnl_usd == 0.0


# ---------------------------------------------------------------------------
# score_wallet eligibility gates
# ---------------------------------------------------------------------------


def test_score_excludes_too_few_trades():
    trades = [mk_trade(ts=1_700_000_000 + i * 86400) for i in range(5)]
    s = score_wallet(
        "0xW", trades, resolved_condition_ids=[],
        now_unix=1_700_500_000, min_trades=50, min_resolved=20,
    )
    assert not s.eligible
    assert any("n_trades" in r for r in s.reasons_excluded)


def test_score_excludes_too_few_resolved():
    trades = [mk_trade(cond=f"C{i}", ts=1_700_000_000 + i * 3600) for i in range(60)]
    s = score_wallet(
        "0xW", trades, resolved_condition_ids=["C0"],
        now_unix=1_700_500_000, min_trades=50, min_resolved=20,
    )
    assert not s.eligible
    assert any("n_resolved" in r for r in s.reasons_excluded)


def test_score_excludes_unprofitable_wallet():
    # 60 trades across 25 resolved markets but all losing
    trades: List[PolymarketTrade] = []
    resolved = []
    for i in range(60):
        cond = f"C{i % 30}"
        resolved.append(cond)
        trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="BUY", size=100,
                               price=0.5, ts=1_700_000_000 + i * 3600))
        # Sold lower → loss
        trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="SELL", size=100,
                               price=0.3, ts=1_700_000_000 + i * 3600 + 60))
    s = score_wallet("0xW", trades, resolved_condition_ids=resolved,
                     now_unix=1_700_500_000, min_trades=50, min_resolved=20)
    assert not s.eligible
    assert "realized_pnl<=0" in s.reasons_excluded


def test_score_eligible_profitable_wallet():
    trades: List[PolymarketTrade] = []
    resolved = []
    for i in range(60):
        cond = f"C{i % 30}"
        resolved.append(cond)
        ts = 1_700_000_000 + i * 3600
        trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="BUY", size=100,
                               price=0.4, ts=ts))
        trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="SELL", size=100,
                               price=0.7, ts=ts + 60))
    s = score_wallet(
        "0xW", trades, resolved_condition_ids=resolved,
        now_unix=1_700_500_000, min_trades=50, min_resolved=20,
    )
    assert s.eligible
    assert s.score > 0
    assert s.realized_pnl_usd > 0


def test_recency_decay_drops_inactive_wallet_score():
    """Two wallets with identical trade histories — the older one should
    score lower because of the recency decay."""
    base_trades: List[PolymarketTrade] = []
    for i in range(60):
        cond = f"C{i % 30}"
        base_trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="BUY",
                                     size=100, price=0.4, ts=0))  # ts overridden below
    resolved = [f"C{i}" for i in range(30)]

    def shift(trades, offset):
        return [
            PolymarketTrade(
                wallet=t.wallet, condition_id=t.condition_id, outcome_index=t.outcome_index,
                outcome_token_id=t.outcome_token_id, side=t.side, size_shares=t.size_shares,
                price=t.price, timestamp_unix=1_700_000_000 + offset + i * 3600,
            )
            for i, t in enumerate(trades)
        ]

    recent = shift(base_trades, 0)
    # Plus exits for realized PnL
    recent += [mk_trade(cond=f"C{i % 30}", token=f"C{i % 30}_T",
                        side="SELL", size=100, price=0.7,
                        ts=1_700_000_000 + i * 3600 + 60) for i in range(60)]
    old = shift(base_trades, -60 * 86400)  # 60 days earlier
    old += [mk_trade(cond=f"C{i % 30}", token=f"C{i % 30}_T",
                     side="SELL", size=100, price=0.7,
                     ts=1_700_000_000 - 60 * 86400 + i * 3600 + 60) for i in range(60)]

    now = 1_700_000_000 + 60 * 3600 + 1000
    s_recent = score_wallet("0xR", recent, resolved, now_unix=now,
                            min_trades=50, min_resolved=20)
    s_old = score_wallet("0xO", old, resolved, now_unix=now,
                         min_trades=50, min_resolved=20)
    assert s_recent.eligible
    assert s_old.eligible  # still profitable, just decayed
    assert s_recent.score > s_old.score


# ---------------------------------------------------------------------------
# rank_wallets + cohort selection
# ---------------------------------------------------------------------------


def _profitable_wallet_trades(seed_pnl: float, n: int = 60, base_ts: int = 1_700_000_000):
    trades = []
    for i in range(n):
        cond = f"C{i % 30}"
        ts = base_ts + i * 3600
        # entry/exit chosen so per-pair realized = (exit - entry) * size
        entry = 0.4
        exit_ = 0.4 + seed_pnl / (100 * 30)  # scale so realized totals seed_pnl
        trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="BUY",
                               size=100, price=entry, ts=ts))
        trades.append(mk_trade(cond=cond, token=f"{cond}_T", side="SELL",
                               size=100, price=exit_, ts=ts + 60))
    return trades


def test_rank_orders_eligible_above_ineligible():
    pool = {
        "0xLow": _profitable_wallet_trades(seed_pnl=100, n=60),
        "0xHigh": _profitable_wallet_trades(seed_pnl=10000, n=60),
        "0xTiny": _profitable_wallet_trades(seed_pnl=50, n=4),   # too few trades
    }
    resolved = [f"C{i}" for i in range(30)]
    ranked = rank_wallets(pool, resolved, now_unix=1_700_500_000)
    # Eligible first
    assert ranked[0].eligible
    assert ranked[0].wallet == "0xhigh"
    assert ranked[1].wallet == "0xlow"
    assert not ranked[-1].eligible


def test_select_cohort_drops_ineligible():
    pool = {
        "0xA": _profitable_wallet_trades(seed_pnl=10000, n=60),
        "0xB": _profitable_wallet_trades(seed_pnl=5000, n=60),
        "0xC": _profitable_wallet_trades(seed_pnl=50, n=4),  # ineligible
    }
    resolved = [f"C{i}" for i in range(30)]
    ranked = rank_wallets(pool, resolved, now_unix=1_700_500_000)
    cohort = select_cohort(ranked, top_n=10)
    assert len(cohort) == 2  # ineligible dropped, only 2 left
    assert {c.wallet for c in cohort} == {"0xa", "0xb"}


def test_select_cohort_is_deterministic():
    pool = {
        f"0xW{i}": _profitable_wallet_trades(seed_pnl=10000 - i, n=60)
        for i in range(20)
    }
    resolved = [f"C{i}" for i in range(30)]
    ranked1 = rank_wallets(pool, resolved, now_unix=1_700_500_000)
    ranked2 = rank_wallets(pool, resolved, now_unix=1_700_500_000)
    assert [s.wallet for s in ranked1] == [s.wallet for s in ranked2]
    assert [s.score for s in ranked1] == [s.score for s in ranked2]
