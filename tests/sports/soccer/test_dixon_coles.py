"""Tests for Dixon-Coles bivariate Poisson."""

from __future__ import annotations

import math

import numpy as np

from src.sports.soccer.models.dixon_coles import (
    DixonColesModel,
    derive_outcome_probs,
    joint_score_log_pmf,
    joint_score_matrix,
)
from src.sports.soccer.ratings.elo import EloTable


def test_score_matrix_normalises_to_unity():
    m = joint_score_matrix(1.5, 1.2, rho=-0.05, max_goals=10)
    assert math.isclose(m.sum(), 1.0, rel_tol=1e-6)


def test_outcome_probs_sum_to_one():
    m = joint_score_matrix(1.4, 1.0, rho=-0.10, max_goals=12)
    p = derive_outcome_probs(m)
    assert math.isclose(p["home_win"] + p["draw"] + p["away_win"], 1.0, rel_tol=1e-6)
    assert math.isclose(p["btts_yes"] + p["btts_no"], 1.0, rel_tol=1e-6)
    assert math.isclose(p["over_2_5"] + p["under_2_5"], 1.0, rel_tol=1e-6)


def test_pmf_at_zero_zero_lower_with_negative_rho():
    """Negative rho should shift mass toward 0-0 and 1-1 (low scores)."""
    p_no_corr = math.exp(joint_score_log_pmf(0, 0, 1.2, 1.0, 0.0))
    p_neg = math.exp(joint_score_log_pmf(0, 0, 1.2, 1.0, -0.10))
    # -ρ on (0,0) means P(0,0) = (1 - λμρ) · Pois(0|λ) · Pois(0|μ).
    # ρ=-0.1 -> factor (1 + 0.12) = 1.12, so P(0,0) goes up.
    assert p_neg > p_no_corr


def test_fit_recovers_correct_ranking_on_synthetic_data():
    rng = np.random.default_rng(0)
    teams = ["STRONG", "MID", "WEAK"]
    # synthesize 200 matches with known strengths
    true_attack = {"STRONG": 0.3, "MID": 0.0, "WEAK": -0.3}
    true_defense = {"STRONG": -0.2, "MID": 0.0, "WEAK": 0.2}
    matches = []
    for _ in range(300):
        h, a = rng.choice(teams, size=2, replace=False)
        lh = math.exp(true_attack[h] + true_defense[a] + 0.2)
        la = math.exp(true_attack[a] + true_defense[h])
        matches.append({
            "home_team_id": h, "away_team_id": a,
            "home_goals": int(rng.poisson(lh)),
            "away_goals": int(rng.poisson(la)),
            "competition": "QUALIFIER",
            "neutral_venue": False,
        })
    fit = DixonColesModel.fit(matches)
    assert fit.params.attack["STRONG"] > fit.params.attack["MID"] > fit.params.attack["WEAK"]
    assert fit.params.defense["STRONG"] < fit.params.defense["MID"] < fit.params.defense["WEAK"]
    # home advantage should be > 0 and < 1
    assert 0.0 < fit.params.home_advantage < 1.0


def test_from_elo_priors_creates_zero_sum_attack():
    elo = EloTable({"A": 2050.0, "B": 1900.0, "C": 1750.0})
    m = DixonColesModel.from_elo_priors(elo)
    s = sum(m.params.attack.values())
    assert abs(s) < 1e-9
    # higher Elo -> higher attack, lower (better) defense
    assert m.params.attack["A"] > m.params.attack["C"]
    assert m.params.defense["A"] < m.params.defense["C"]
