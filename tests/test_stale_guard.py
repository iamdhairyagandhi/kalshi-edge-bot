"""Tests for stale_guard."""

import time

import pytest

from src.risk.stale_guard import (
    check_arb_sanity,
    freshness_score,
    MAX_ARB_PROFIT_FRACTION,
)


def test_fresh_quote_scores_high():
    now = 1_000_000.0
    assert freshness_score(now, now=now) == pytest.approx(1.0)


def test_old_quote_scores_zero():
    now = 1_000_000.0
    assert freshness_score(now - 120, now=now) == 0.0


def test_freshness_linearly_decays():
    now = 1_000_000.0
    s = freshness_score(now - 30, now=now)   # halfway through 60s window
    assert s == pytest.approx(0.5, abs=0.02)


def test_clean_arb_accepted():
    now = time.time()
    r = check_arb_sanity(
        yes_price=0.40, no_price=0.55, profit_per_contract=0.03,
        observed_at_epoch=now,
    )
    assert r.accepted
    assert r.rejections == []


def test_too_good_to_be_true_rejected():
    now = time.time()
    r = check_arb_sanity(
        yes_price=0.40, no_price=0.40, profit_per_contract=0.20,
        observed_at_epoch=now,
    )
    assert not r.accepted
    assert any("too-good-to-be-true" in m for m in r.rejections)


def test_extreme_leg_rejected():
    now = time.time()
    r = check_arb_sanity(
        yes_price=0.005, no_price=0.95, profit_per_contract=0.02,
        observed_at_epoch=now,
    )
    assert not r.accepted
    assert any("extreme" in m for m in r.rejections)


def test_stale_quote_rejected():
    now = 1_000_000.0
    r = check_arb_sanity(
        yes_price=0.40, no_price=0.55, profit_per_contract=0.02,
        observed_at_epoch=now - 120, now=now,
    )
    assert not r.accepted
    assert any("stale" in m for m in r.rejections)


def test_boundary_exactly_at_cap_rejected():
    # > cap, not >=
    now = time.time()
    r = check_arb_sanity(
        yes_price=0.40, no_price=0.55,
        profit_per_contract=MAX_ARB_PROFIT_FRACTION + 0.001,
        observed_at_epoch=now,
    )
    assert not r.accepted
