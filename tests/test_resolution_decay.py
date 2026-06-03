"""Tests for resolution decay strategy."""

from datetime import datetime, timedelta, timezone

import pytest

from src.strategies.resolution_decay import (
    evaluate, should_take_profit,
    NEW_TRADE_BLOCK_MINUTES, WIDEN_WINDOW_MINUTES,
)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def test_far_from_resolution_normal():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    close = now + timedelta(days=7)
    d = evaluate(_iso(close), now=now)
    assert not d.block_new_open
    assert d.widen_spread_multiplier == 1.0


def test_inside_block_window_no_new_opens():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    close = now + timedelta(minutes=2)
    d = evaluate(_iso(close), now=now)
    assert d.block_new_open
    assert d.widen_spread_multiplier > 1.0


def test_widening_ramps_linearly():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    close = now + timedelta(minutes=WIDEN_WINDOW_MINUTES / 2)
    d = evaluate(_iso(close), now=now)
    assert 1.4 < d.widen_spread_multiplier < 1.6  # mid of [1.0, 2.0]


def test_unparseable_close_treated_as_far():
    d = evaluate("not a date")
    assert not d.block_new_open
    assert d.widen_spread_multiplier == 1.0


def test_take_profit_yes():
    # Bought YES at 0.40 expecting 5¢ edge. If mid moves to 0.44
    # (70% of 5¢ = 3.5¢), take it.
    assert should_take_profit(0.40, 0.44, "YES", 0.05)
    assert not should_take_profit(0.40, 0.42, "YES", 0.05)


def test_take_profit_no():
    # Bought NO at 0.60 expecting 5¢ edge. If mid moves to 0.56 (4¢ drop), take it.
    assert should_take_profit(0.60, 0.56, "NO", 0.05)
    assert not should_take_profit(0.60, 0.58, "NO", 0.05)
