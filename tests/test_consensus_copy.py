"""Tests for the Polymarket consensus-copy strategy."""

from __future__ import annotations

from typing import Dict, List, Sequence

import pytest

from src.clients.polymarket import (
    PolymarketMarket,
    PolymarketOutcome,
    PolymarketTrade,
)
from src.strategies.consensus_copy import (
    ConsensusSignal,
    cohort_version,
    detect_consensus_signals,
)


WIN_START = 1_700_000_000
WIN_END = WIN_START + 24 * 3600


def mk_market(
    cond: str = "0xC",
    question: str = "Q?",
    binary: bool = True,
    closed: bool = False,
    accepting: bool = True,
) -> PolymarketMarket:
    if binary:
        outcomes = [
            PolymarketOutcome(0, "Yes", "YES_T"),
            PolymarketOutcome(1, "No", "NO_T"),
        ]
    else:
        outcomes = [PolymarketOutcome(i, f"O{i}", f"T{i}") for i in range(3)]
    return PolymarketMarket(
        condition_id=cond, question_id=None, slug=None,
        question=question, closed=closed, accepting_orders=accepting,
        outcomes=outcomes,
    )


def mk_trade(
    wallet: str, cond: str, token: str, side: str,
    size: float, price: float, ts: int,
) -> PolymarketTrade:
    outcome_index = 0 if token in ("YES_T", "T0") else (1 if token in ("NO_T", "T1") else 2)
    return PolymarketTrade(
        wallet=wallet.lower(), condition_id=cond, outcome_index=outcome_index,
        outcome_token_id=token, side=side, size_shares=size, price=price,
        timestamp_unix=ts,
    )


# ---------------------------------------------------------------------------
# Basic detection
# ---------------------------------------------------------------------------


def test_emits_signal_when_k_wallets_net_long_same_outcome():
    cohort = ["0xA", "0xB", "0xC", "0xD", "0xE"]
    trades = {
        w: [mk_trade(w, "0xC1", "YES_T", "BUY", 100, 0.40 + i * 0.01,
                     WIN_START + 3600 + i * 60)]
        for i, w in enumerate(cohort)
    }
    markets = {"0xC1": mk_market("0xC1", "Q1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=3,
    )
    assert len(sigs) == 1
    s = sigs[0]
    assert s.condition_id == "0xC1"
    assert s.outcome_token_id == "YES_T"
    assert s.outcome_index == 0
    assert s.cohort_size == 5
    assert s.consensus_k == 3
    assert len(s.agreeing_wallets) == 3
    # avg price across the top-3 most-committed wallets
    assert 0.40 <= s.avg_wallet_entry_price <= 0.45


def test_no_signal_when_under_k():
    cohort = ["0xA", "0xB", "0xC"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 200)],
        "0xC": [],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=3,
    )
    assert sigs == []


def test_wallet_that_buys_then_sells_does_not_vote():
    """The wallet's net position is zero by window end → no vote."""
    cohort = ["0xA", "0xB", "0xC"]
    trades = {
        # 0xA opens then closes — must NOT vote
        "0xA": [
            mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.40, WIN_START + 100),
            mk_trade("0xA", "0xC1", "YES_T", "SELL", 100, 0.45, WIN_START + 200),
        ],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.40, WIN_START + 300)],
        "0xC": [mk_trade("0xC", "0xC1", "YES_T", "BUY", 100, 0.40, WIN_START + 400)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=3,
    )
    assert sigs == []  # only 2 net-long wallets, K=3


def test_multiple_partial_fills_count_as_one_vote():
    """Five fills by one wallet must not satisfy K=2 alone."""
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [
            mk_trade("0xA", "0xC1", "YES_T", "BUY", 20, 0.40, WIN_START + i * 60)
            for i in range(5)
        ],
        "0xB": [],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert sigs == []


def test_trades_outside_window_excluded():
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START - 10)],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_END + 10)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert sigs == []


def test_non_cohort_wallets_ignored_even_if_present_in_trades():
    """Stray trades by non-cohort wallets must NOT vote."""
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [],
        "0xSTRANGER": [mk_trade("0xSTRANGER", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 200)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert sigs == []


def test_closed_market_does_not_emit():
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 200)],
    }
    markets = {"0xC1": mk_market("0xC1", closed=True)}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert sigs == []


def test_unknown_token_id_skipped():
    """If trades reference a token_id that the market doesn't list,
    skip safely (don't crash)."""
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "BAD_TOKEN", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC1", "BAD_TOKEN", "BUY", 100, 0.4, WIN_START + 200)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert sigs == []


def test_yes_and_no_are_separate_signals():
    """A cohort split — some long YES, others long NO — should emit at most
    one signal per side that has K."""
    cohort = ["0xA", "0xB", "0xC", "0xD"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 200)],
        "0xC": [mk_trade("0xC", "0xC1", "NO_T",  "BUY", 100, 0.6, WIN_START + 300)],
        "0xD": [mk_trade("0xD", "0xC1", "NO_T",  "BUY", 100, 0.6, WIN_START + 400)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert {s.outcome_token_id for s in sigs} == {"YES_T", "NO_T"}
    assert len(sigs) == 2


def test_multi_outcome_market_carries_outcome_index():
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC2", "T2", "BUY", 100, 0.30, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC2", "T2", "BUY", 100, 0.30, WIN_START + 200)],
    }
    markets = {"0xC2": mk_market("0xC2", binary=False)}
    sigs = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )
    assert len(sigs) == 1
    assert sigs[0].outcome_index == 2


# ---------------------------------------------------------------------------
# Idempotency + cohort versioning
# ---------------------------------------------------------------------------


def test_idempotency_key_stable_across_runs():
    cohort = ["0xA", "0xB"]
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 200)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    s1 = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )[0]
    s2 = detect_consensus_signals(
        cohort_wallets=cohort, cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END,
        consensus_k=2,
    )[0]
    assert s1.idempotency_key == s2.idempotency_key


def test_cohort_version_independent_of_order():
    assert cohort_version(["0xA", "0xB", "0xC"]) == cohort_version(["0xc", "0xb", "0xa"])


def test_cohort_version_changes_when_membership_changes():
    a = cohort_version(["0xA", "0xB"])
    b = cohort_version(["0xA", "0xC"])
    assert a != b


def test_idempotency_key_changes_with_cohort_membership():
    """If the cohort changes, the same window/market produces a NEW
    idempotency key so the runner re-evaluates."""
    trades = {
        "0xA": [mk_trade("0xA", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 100)],
        "0xB": [mk_trade("0xB", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 200)],
    }
    markets = {"0xC1": mk_market("0xC1")}
    sig_v1 = detect_consensus_signals(
        cohort_wallets=["0xA", "0xB"], cohort_trades=trades, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END, consensus_k=2,
    )[0]
    # Same window, same market, but add 0xC to cohort.
    trades_v2 = {**trades, "0xC": [
        mk_trade("0xC", "0xC1", "YES_T", "BUY", 100, 0.4, WIN_START + 300)
    ]}
    sig_v2 = detect_consensus_signals(
        cohort_wallets=["0xA", "0xB", "0xC"], cohort_trades=trades_v2, markets=markets,
        window_start_unix=WIN_START, window_end_unix=WIN_END, consensus_k=2,
    )[0]
    assert sig_v1.idempotency_key != sig_v2.idempotency_key


def test_k_must_be_at_least_2():
    with pytest.raises(ValueError):
        detect_consensus_signals(
            cohort_wallets=["0xA"], cohort_trades={"0xA": []},
            markets={}, window_start_unix=WIN_START,
            window_end_unix=WIN_END, consensus_k=1,
        )


def test_k_cannot_exceed_cohort_size():
    with pytest.raises(ValueError):
        detect_consensus_signals(
            cohort_wallets=["0xA", "0xB"], cohort_trades={},
            markets={}, window_start_unix=WIN_START,
            window_end_unix=WIN_END, consensus_k=3,
        )
