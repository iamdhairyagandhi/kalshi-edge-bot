"""Tests for calibration utilities."""

from __future__ import annotations

import math

from src.sports.soccer.calibration.isotonic import (
    CalibrationLayer,
    PredictionRecord,
    brier_score,
    clv_decimal,
    reliability_buckets,
)


def test_brier_perfect_predictions_is_zero():
    recs = [PredictionRecord("m", 1.0, outcome=1), PredictionRecord("m", 0.0, outcome=0)]
    assert brier_score(recs) == 0.0


def test_brier_random_coin_flip_is_quarter():
    recs = [PredictionRecord("m", 0.5, outcome=1)] * 50 + [PredictionRecord("m", 0.5, outcome=0)] * 50
    assert math.isclose(brier_score(recs), 0.25, rel_tol=1e-9)


def test_reliability_buckets_count_observations():
    recs = (
        [PredictionRecord("m", 0.05, outcome=0)] * 10 +
        [PredictionRecord("m", 0.95, outcome=1)] * 10
    )
    bins = reliability_buckets(recs, n_bins=10)
    assert bins[0]["n"] == 10
    assert bins[-1]["n"] == 10
    # observed should match predicted in well-calibrated bins
    assert abs(bins[0]["observed"] - 0.0) < 1e-9
    assert abs(bins[-1]["observed"] - 1.0) < 1e-9


def test_clv_positive_when_we_paid_better_than_close():
    # we got 2.20, market closed 2.00 -> we got better odds -> +CLV
    clv = clv_decimal(2.20, 2.00)
    assert clv > 0
    # explicit: 1/2.0 - 1/2.2 = 0.5 - 0.4545 = 0.0455
    assert math.isclose(clv, 1 / 2.0 - 1 / 2.2, rel_tol=1e-9)


def test_clv_negative_when_we_got_worse_than_close():
    clv = clv_decimal(1.80, 2.00)
    assert clv < 0


def test_clv_handles_invalid_input():
    assert math.isnan(clv_decimal(1.0, 2.0))
    assert math.isnan(clv_decimal(2.0, 0.5))


def test_calibration_layer_is_identity_when_unfit():
    layer = CalibrationLayer()
    for p in (0.05, 0.20, 0.50, 0.80, 0.95):
        assert math.isclose(layer.calibrate("home_win", p), p, abs_tol=1e-9)


def test_calibration_layer_skips_markets_under_min_samples():
    # 29 records is below the 30-sample minimum -> still identity.
    layer = CalibrationLayer()
    recs = [PredictionRecord("home_win", 0.5, outcome=1) for _ in range(29)]
    layer.fit(recs)
    assert math.isclose(layer.calibrate("home_win", 0.5), 0.5, abs_tol=1e-9)


def test_calibration_layer_pulls_overconfident_probs_back_to_observed_rate():
    # Model says 0.80 every time but only 50% actually win — isotonic
    # should pull 0.80 down toward ~0.5.
    layer = CalibrationLayer()
    recs = (
        [PredictionRecord("home_win", 0.80, outcome=1) for _ in range(20)]
        + [PredictionRecord("home_win", 0.80, outcome=0) for _ in range(20)]
    )
    # Add anchor points so the isotonic fit isn't a single x value.
    recs += [PredictionRecord("home_win", 0.10, outcome=0) for _ in range(5)]
    recs += [PredictionRecord("home_win", 0.95, outcome=1) for _ in range(5)]
    layer.fit(recs)
    out = layer.calibrate("home_win", 0.80)
    # At p=0.80 the empirical rate of 1s in the 0.80 bucket is 0.5.
    assert out < 0.70, f"expected calibration to pull 0.80 below 0.70 but got {out}"

