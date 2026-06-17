"""Tests for World Football Elo."""

from __future__ import annotations

import math

from src.sports.soccer.ratings.elo import (
    EloRating,
    EloTable,
    expected_score,
    goal_difference_multiplier,
    update_match,
)


def test_expected_score_symmetry_at_equal_ratings_is_50_pct():
    p = expected_score(1500, 1500)
    assert abs(p - 0.5) < 1e-12


def test_home_advantage_pushes_expected_score_above_half():
    p = expected_score(1500, 1500, home_advantage=100.0)
    assert p > 0.5
    # 100 Elo HA -> ~64% expectation
    assert 0.6 < p < 0.7


def test_goal_difference_multiplier_matches_eloratings_method():
    assert goal_difference_multiplier(2, 2) == 1.0  # GD=0
    assert goal_difference_multiplier(1, 0) == 1.0
    assert goal_difference_multiplier(2, 0) == 1.5
    assert math.isclose(goal_difference_multiplier(4, 0), (11 + 4) / 8)
    assert math.isclose(goal_difference_multiplier(5, 0), (11 + 5) / 8)


def test_update_match_winner_gains_loser_loses_equal_amount():
    h = EloRating("HOME", 1500.0)
    a = EloRating("AWAY", 1600.0)
    h2, a2 = update_match(h, a, 2, 1, tournament="WC_GROUP", neutral_venue=True)
    # Underdog won at home -> rating goes up
    assert h2.rating > h.rating
    assert a2.rating < a.rating
    # zero-sum
    assert math.isclose((h2.rating - h.rating) + (a2.rating - a.rating), 0.0, abs_tol=1e-9)


def test_table_fit_replays_in_order_and_is_deterministic():
    matches = [
        {"home_team_id": "A", "away_team_id": "B", "home_goals": 2, "away_goals": 1, "competition": "QUALIFIER", "neutral_venue": False},
        {"home_team_id": "B", "away_team_id": "A", "home_goals": 0, "away_goals": 0, "competition": "QUALIFIER", "neutral_venue": False},
        {"home_team_id": "A", "away_team_id": "C", "home_goals": 3, "away_goals": 1, "competition": "QUALIFIER", "neutral_venue": False},
    ]
    t1 = EloTable({"A": 1500.0, "B": 1500.0, "C": 1500.0})
    t1.fit(matches)
    t2 = EloTable({"A": 1500.0, "B": 1500.0, "C": 1500.0})
    t2.fit(matches)
    assert t1.to_dict() == t2.to_dict()
    # A should be highest after winning twice and drawing once
    rs = t1.to_dict()
    assert rs["A"] > rs["B"] > rs["C"]
