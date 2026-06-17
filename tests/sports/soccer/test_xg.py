"""Tests for the shot-level xG model and its helpers."""

from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.sports.soccer.models.xg import (
    FEATURE_NAMES,
    XgModel,
    aggregate_match_xg,
    extract_shot_features,
)


# ----------------------------------------------------------------------
# Feature extraction
# ----------------------------------------------------------------------
def test_feature_names_stable():
    assert FEATURE_NAMES[0] == "distance_to_goal"
    assert "angle_to_goal" in FEATURE_NAMES
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))


def test_close_central_shot_has_small_distance_large_angle():
    shot = {"x": 115.0, "y": 40.0, "shot_type": "Open Play", "body_part": "Right Foot"}
    f = extract_shot_features(shot)
    assert f["distance_to_goal"] == pytest.approx(5.0, abs=0.01)
    # Wide goal mouth seen from very close = large angle (>1 rad)
    assert f["angle_to_goal"] > 0.5
    assert f["is_open_play"] == 1.0
    assert f["is_right_foot"] == 1.0


def test_far_wide_shot_has_large_distance_small_angle():
    shot = {"x": 80.0, "y": 5.0, "shot_type": "Open Play", "body_part": "Left Foot"}
    f = extract_shot_features(shot)
    assert f["distance_to_goal"] > 40.0
    # Narrow strip of goal mouth = small angle
    assert f["angle_to_goal"] < 0.2
    assert f["is_left_foot"] == 1.0


def test_header_volley_and_set_piece_flags():
    shot = {
        "x": 110.0, "y": 40.0,
        "body_part": "Head",
        "technique": "Volley",
        "shot_type": "Free Kick",
        "first_time": True,
        "under_pressure": True,
        "one_on_one": False,
    }
    f = extract_shot_features(shot)
    assert f["is_header"] == 1.0
    assert f["is_volley"] == 1.0
    assert f["is_free_kick"] == 1.0
    assert f["is_open_play"] == 0.0
    assert f["is_first_time"] == 1.0
    assert f["is_under_pressure"] == 1.0


def test_penalty_flag():
    shot = {"x": 108.0, "y": 40.0, "shot_type": "Penalty"}
    f = extract_shot_features(shot)
    assert f["is_penalty"] == 1.0
    assert f["is_open_play"] == 0.0


# ----------------------------------------------------------------------
# Model fit + predict
# ----------------------------------------------------------------------
def _synthetic_shots(n: int = 600, seed: int = 7) -> list[dict]:
    """Generate a synthetic shot dataset where goal probability is a
    decreasing function of distance to goal. The model should learn this
    and rank close shots above far shots."""
    rng = random.Random(seed)
    shots: list[dict] = []
    for i in range(n):
        x = rng.uniform(70.0, 119.0)
        y = rng.uniform(20.0, 60.0)
        dist = math.hypot(120.0 - x, 40.0 - y)
        # Sigmoid in distance: close-in ~0.4, far ~0.02
        p_goal = 1.0 / (1.0 + math.exp(0.18 * (dist - 12.0)))
        is_goal = 1 if rng.random() < p_goal else 0
        shots.append({
            "match_id": f"m{i // 30}",
            "team_id": "home" if i % 2 == 0 else "away",
            "player_id": f"p{i % 20}",
            "x": x, "y": y,
            "body_part": "Right Foot",
            "technique": "Normal",
            "shot_type": "Open Play",
            "first_time": False,
            "under_pressure": False,
            "one_on_one": False,
            "open_goal": False,
            "is_goal": is_goal,
            "minute": rng.randint(1, 90),
        })
    return shots


def test_xg_fit_predict_returns_probabilities():
    shots = _synthetic_shots()
    model = XgModel.fit(shots, backend="sklearn")
    preds = model.predict_xgs(shots)
    assert preds.shape == (len(shots),)
    assert float(preds.min()) > 0.0
    assert float(preds.max()) < 1.0
    # Base rate sanity check
    obs_rate = float(np.mean([s["is_goal"] for s in shots]))
    assert abs(float(preds.mean()) - obs_rate) < 0.05


def test_xg_ranks_close_above_far():
    shots = _synthetic_shots()
    model = XgModel.fit(shots, backend="sklearn")
    close_shot = {
        "x": 116.0, "y": 40.0, "body_part": "Right Foot",
        "technique": "Normal", "shot_type": "Open Play",
    }
    far_shot = {
        "x": 75.0, "y": 15.0, "body_part": "Right Foot",
        "technique": "Normal", "shot_type": "Open Play",
    }
    assert model.predict_xg(close_shot) > model.predict_xg(far_shot)


def test_xg_constant_backend_when_no_goals():
    # All misses -> constant predictor at base rate (0.0)
    shots = [
        {"x": 80.0, "y": 40.0, "shot_type": "Open Play", "is_goal": 0}
        for _ in range(20)
    ]
    model = XgModel.fit(shots)
    assert model.backend == "constant"
    assert model.predict_xg({"x": 115.0, "y": 40.0}) == pytest.approx(0.0)


def test_xg_empty_input_raises():
    with pytest.raises(ValueError):
        XgModel.fit([])


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------
def test_match_xg_sums_per_side():
    shots = _synthetic_shots(n=120)
    model = XgModel.fit(shots, backend="sklearn")
    match_shots = [s for s in shots if s["match_id"] == "m0"]
    h_xg, a_xg = model.match_xg(
        match_shots, home_team_id="home", away_team_id="away",
    )
    assert h_xg >= 0.0
    assert a_xg >= 0.0
    # Sum equals total predicted xG over those shots
    total = float(model.predict_xgs(match_shots).sum())
    assert h_xg + a_xg == pytest.approx(total, rel=1e-6)


def test_aggregate_match_xg_keyed_by_match():
    shots = _synthetic_shots(n=300)
    model = XgModel.fit(shots, backend="sklearn")
    mids = sorted({s["match_id"] for s in shots})
    mapping = {mid: ("home", "away") for mid in mids}
    per_match = aggregate_match_xg(shots, model, match_home_away=mapping)
    assert set(per_match.keys()) == set(mids)
    for mid, (h, a) in per_match.items():
        assert h >= 0.0 and a >= 0.0


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------
def test_xg_model_save_load_roundtrip():
    shots = _synthetic_shots(n=200)
    model = XgModel.fit(shots, backend="sklearn")
    test_shot = {
        "x": 110.0, "y": 38.0, "body_part": "Right Foot",
        "technique": "Normal", "shot_type": "Open Play",
    }
    before = model.predict_xg(test_shot)
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "xg.pkl")
        model.save(path)
        loaded = XgModel.load(path)
    after = loaded.predict_xg(test_shot)
    assert after == pytest.approx(before, rel=1e-9, abs=1e-9)
    assert loaded.backend == model.backend
    assert loaded.n_shots_trained == model.n_shots_trained
