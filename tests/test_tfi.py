"""Tests for TFI tracker."""

import pytest

from src.signals.tfi import (
    TFITracker, Trade,
    should_veto_no_buy, should_veto_yes_buy,
)


def test_empty_tracker_returns_zero():
    t = TFITracker()
    s = t.sample(now=1000.0)
    assert s.z_score == 0.0
    assert s.short_tfi == 0.0


def test_recent_trades_count_in_short_window():
    t = TFITracker(short_window_seconds=30.0, long_window_seconds=300.0)
    now = 1000.0
    for i in range(20):
        t.add_trade(Trade(timestamp=now - i, signed_size=1.0))
    s = t.sample(now=now)
    # 20 trades in 20s, all within 30s window → short_tfi=20
    assert s.short_tfi == 20.0


def test_old_trades_excluded():
    t = TFITracker(short_window_seconds=30.0, long_window_seconds=300.0)
    now = 1000.0
    # Add older trades
    for i in range(100, 120):
        t.add_trade(Trade(timestamp=now - i, signed_size=1.0))
    # Then add fresh trades
    for i in range(10):
        t.add_trade(Trade(timestamp=now - i, signed_size=1.0))
    s = t.sample(now=now)
    # short_tfi should only count the fresh 10 trades
    assert s.short_tfi == 10.0


def test_veto_logic():
    from src.signals.tfi import TFISample
    bear = TFISample(z_score=-3.0, short_tfi=-100, mean=0, std=10, n_long_samples=100)
    bull = TFISample(z_score=+3.0, short_tfi=+100, mean=0, std=10, n_long_samples=100)
    neutral = TFISample(z_score=0.0, short_tfi=0, mean=0, std=10, n_long_samples=100)

    assert should_veto_yes_buy(bear)    # aggressive selling: don't buy YES
    assert not should_veto_yes_buy(bull)
    assert should_veto_no_buy(bull)     # aggressive buying: don't buy NO
    assert not should_veto_no_buy(bear)
    assert not should_veto_yes_buy(neutral)
