"""Tests for strategy kill switch."""

import os
import tempfile

from src.risk.strategy_kill import (
    enable, disable, is_enabled, get_status, evaluate_and_maybe_kill,
    MIN_SAMPLES_BEFORE_EVALUATING,
)
from src.utils.calibration import CalibrationStore


def test_default_enabled():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "k.db")
        assert is_enabled(db, "any-strategy")


def test_disable_then_check():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "k.db")
        disable(db, "bad-strat", reason="manual test")
        assert not is_enabled(db, "bad-strat")
        s = get_status(db, "bad-strat")
        assert s.disabled_reason == "manual test"


def test_enable_re_enables():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "k.db")
        disable(db, "s", reason="x")
        enable(db, "s")
        assert is_enabled(db, "s")


def test_evaluate_not_enough_samples_does_nothing():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "k.db")
        store = CalibrationStore(db)
        for i in range(5):
            pid = store.log_prediction(
                strategy="s", ticker="K1", side="YES",
                predicted_prob=0.5, price_at_decision=0.5,
                decided_at="2026-01-01",
            )
            store.resolve(pid, outcome=1, resolved_at="2026-01-02")
        result = evaluate_and_maybe_kill(db, "s")
        assert result.enabled
        assert result.last_brier is None


def test_evaluate_kills_bad_strategy():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "k.db")
        store = CalibrationStore(db)
        # Predict 0.99 over and over but the outcome is always 0 → Brier ≈ 0.98
        for i in range(MIN_SAMPLES_BEFORE_EVALUATING + 5):
            pid = store.log_prediction(
                strategy="bad", ticker=f"K{i}", side="YES",
                predicted_prob=0.99, price_at_decision=0.99,
                decided_at="2026-01-01",
            )
            store.resolve(pid, outcome=0, resolved_at="2026-01-02")
        result = evaluate_and_maybe_kill(db, "bad")
        assert not result.enabled
        assert result.last_brier > 0.25
        assert "Brier" in (result.disabled_reason or "")


def test_evaluate_keeps_good_strategy():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "k.db")
        store = CalibrationStore(db)
        # Perfect predictions: 1.0 when outcome=1, 0.0 when outcome=0
        for i in range(MIN_SAMPLES_BEFORE_EVALUATING + 5):
            outcome = i % 2
            pid = store.log_prediction(
                strategy="good", ticker=f"K{i}", side="YES",
                predicted_prob=float(outcome), price_at_decision=0.5,
                decided_at="2026-01-01",
            )
            store.resolve(pid, outcome=outcome, resolved_at="2026-01-02")
        result = evaluate_and_maybe_kill(db, "good")
        assert result.enabled
        assert result.last_brier < 0.01
