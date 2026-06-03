"""Tests for the calibration store."""

import os
import tempfile

import pytest

from src.utils.calibration import CalibrationStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield CalibrationStore(os.path.join(d, "cal.db"))


def test_log_and_resolve_roundtrip(store):
    pid = store.log_prediction(
        strategy="safe_compounder", ticker="KXTEST-1", side="NO",
        predicted_prob=0.95, price_at_decision=0.88, decided_at="2026-01-01T00:00:00",
    )
    store.resolve(pid, outcome=1, resolved_at="2026-01-02T00:00:00")
    r = store.report("safe_compounder")
    assert r.n_resolved == 1


def test_brier_perfect_predictor(store):
    # Predict 1.0 every time, always win -> Brier = 0
    for i in range(20):
        pid = store.log_prediction(
            strategy="oracle", ticker=f"T{i}", side="YES",
            predicted_prob=1.0, price_at_decision=0.5, decided_at="2026-01-01",
        )
        store.resolve(pid, outcome=1, resolved_at="2026-01-02")
    assert store.report("oracle").brier_score == 0.0


def test_brier_random_predictor(store):
    # Predict 50% on coin flips -> Brier should be 0.25
    for i in range(100):
        pid = store.log_prediction(
            strategy="coin", ticker=f"T{i}", side="YES",
            predicted_prob=0.5, price_at_decision=0.5, decided_at="2026-01-01",
        )
        store.resolve(pid, outcome=i % 2, resolved_at="2026-01-02")
    assert store.report("coin").brier_score == pytest.approx(0.25, abs=1e-9)


def test_resolve_validates_outcome(store):
    pid = store.log_prediction(
        strategy="s", ticker="T", side="YES",
        predicted_prob=0.5, price_at_decision=0.5, decided_at="2026-01-01",
    )
    with pytest.raises(ValueError):
        store.resolve(pid, outcome=2, resolved_at="2026-01-02")


def test_buckets_populated(store):
    # 10 predictions at 0.9, all correct -> bucket [0.9, 1.0) shows realized_rate=1.0
    for i in range(10):
        pid = store.log_prediction(
            strategy="s", ticker=f"T{i}", side="YES",
            predicted_prob=0.9, price_at_decision=0.5, decided_at="2026-01-01",
        )
        store.resolve(pid, outcome=1, resolved_at="2026-01-02")
    r = store.report("s")
    top_bucket = [b for b in r.buckets if b["lo"] <= 0.9 < b["hi"]][0]
    assert top_bucket["realized_rate"] == 1.0
    assert top_bucket["n"] == 10
