"""Simulator + bet builder tests."""

from __future__ import annotations

import math

import numpy as np

from src.sports.soccer.models.cards import CardsModel
from src.sports.soccer.models.dixon_coles import DixonColesModel
from src.sports.soccer.models.minutes import MinutesModel
from src.sports.soccer.models.player_share import PlayerShareModel
from src.sports.soccer.ratings.elo import EloTable
from src.sports.soccer.simulator.bet_builder import price_bet_builder
from src.sports.soccer.simulator.match_sim import MatchSimulator, SimulationConfig
from src.sports.soccer.types import BetLeg


def _make_simulator(seed: int = 0) -> MatchSimulator:
    elo = EloTable({"H": 2000.0, "A": 1900.0})
    score = DixonColesModel.from_elo_priors(elo, league_avg_goals=1.4)
    squads = {
        "H": [
            {"player_id": "h-fw", "position": "FW"},
            {"player_id": "h-mf", "position": "MF"},
            {"player_id": "h-df", "position": "DF"},
            {"player_id": "h-gk", "position": "GK"},
        ],
        "A": [
            {"player_id": "a-fw", "position": "FW"},
            {"player_id": "a-mf", "position": "MF"},
            {"player_id": "a-df", "position": "DF"},
            {"player_id": "a-gk", "position": "GK"},
        ],
    }
    share = PlayerShareModel.from_position_priors(squads)
    pids = [p["player_id"] for s in squads.values() for p in s]
    minutes = MinutesModel.from_starting_priors({pid: {"start_prob": 0.9, "minutes_avg": 80} for pid in pids})
    cards = CardsModel.from_priors({pid: {"yellow_per90": 0.2, "red_per90": 0.005} for pid in pids})
    sim = MatchSimulator(
        score_model=score, player_share=share, minutes=minutes, cards=cards,
        squads={tid: [p["player_id"] for p in players] for tid, players in squads.items()},
    )
    return sim


def test_simulator_marginals_match_score_matrix():
    sim = _make_simulator()
    sims = sim.simulate("H", "A", neutral_venue=True,
                         config=SimulationConfig(n_sims=15000, seed=42))
    # closed-form
    p = sim.score_model.outcome_probs("H", "A", neutral=True, max_goals=10)
    home_win = sum(1 for s in sims if s.result == "H") / len(sims)
    away_win = sum(1 for s in sims if s.result == "A") / len(sims)
    btts = sum(1 for s in sims if s.btts) / len(sims)
    # match within ~2% absolute
    assert abs(home_win - p["home_win"]) < 0.02
    assert abs(away_win - p["away_win"]) < 0.02
    assert abs(btts - p["btts_yes"]) < 0.02


def test_bet_builder_joint_le_min_marginals():
    sim = _make_simulator()
    sims = sim.simulate("H", "A", neutral_venue=True,
                         config=SimulationConfig(n_sims=8000, seed=1))
    legs = [
        BetLeg("match_result", {"side": "H"}),
        BetLeg("btts", {"side": "yes"}),
    ]
    q = price_bet_builder(sims, legs)
    # joint <= each marginal
    assert q.fair_probability <= min(q.leg_probabilities) + 1e-9
    # 1/p == fair odds
    assert math.isclose(q.fair_decimal_odds, 1.0 / q.fair_probability, rel_tol=1e-9)


def test_bet_builder_with_book_odds_computes_edge_and_kelly():
    sim = _make_simulator()
    sims = sim.simulate("H", "A", neutral_venue=True,
                         config=SimulationConfig(n_sims=5000, seed=2))
    legs = [BetLeg("match_result", {"side": "H"})]
    q = price_bet_builder(sims, legs, book_decimal_odds=3.0, min_edge=0.0)
    assert q.book_decimal_odds == 3.0
    assert q.edge is not None
    # edge = book*p - 1; sanity check
    assert math.isclose(q.edge, 3.0 * q.fair_probability - 1.0, rel_tol=1e-9)
    if q.edge > 0:
        assert q.kelly_fraction is not None and q.kelly_fraction > 0
        assert q.recommendation in ("bet", "thin_edge")
    else:
        assert q.recommendation == "no_bet"


def test_bet_builder_handles_empty_legs():
    sim = _make_simulator()
    sims = sim.simulate("H", "A", neutral_venue=True,
                         config=SimulationConfig(n_sims=200, seed=3))
    q = price_bet_builder(sims, [])
    assert q.fair_probability == 1.0
    assert q.fair_decimal_odds == 1.0


def test_correlated_legs_have_correlation_factor_above_one():
    """Match result + BTTS + Over 2.5 are positively correlated."""
    sim = _make_simulator()
    sims = sim.simulate("H", "A", neutral_venue=True,
                         config=SimulationConfig(n_sims=8000, seed=5))
    legs = [
        BetLeg("match_result", {"side": "H"}),
        BetLeg("btts", {"side": "yes"}),
        BetLeg("total_goals", {"line": 2.5, "side": "over"}),
    ]
    q = price_bet_builder(sims, legs)
    # joint probability should exceed product of marginals
    assert q.correlation_factor > 1.0
