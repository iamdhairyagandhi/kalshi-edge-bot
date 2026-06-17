"""
World Football Elo for international teams.

Reference: eloratings.net method
- Base K = 30 with tournament multiplier (WC final 60, knockout 50, group 40,
  qualifier 30, friendly 20).
- Goal-difference multiplier:
    |GD|=1 -> 1.0
    |GD|=2 -> 1.5
    |GD|>=3 -> (11 + |GD|) / 8
- Home advantage: +100 to home rating before computing expected score.
  WC group games are at neutral venues -> HA forced to 0 unless caller
  overrides.

The ratings produced here feed:
  - Pre-tournament priors for the Dixon-Coles attack/defense parameters
    (`models.dixon_coles.elo_to_strengths`).
  - The state-space updater (`models.state_space`) which performs Kalman
    correction after each in-tournament match.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple


_TOURNAMENT_K: Dict[str, float] = {
    "WC_FINAL": 60.0,
    "WC_KNOCKOUT": 50.0,
    "WC_GROUP": 40.0,
    "QUALIFIER": 30.0,
    "FRIENDLY": 20.0,
    "DEFAULT": 30.0,
}

DEFAULT_HOME_ADVANTAGE = 100.0


@dataclass
class EloRating:
    team_id: str
    rating: float = 1500.0
    n_matches: int = 0


def expected_score(rating_a: float, rating_b: float, home_advantage: float = 0.0) -> float:
    """Probability A wins (with draw split) given Elo difference + HA."""
    return 1.0 / (1.0 + math.pow(10.0, -(rating_a + home_advantage - rating_b) / 400.0))


def goal_difference_multiplier(home_goals: int, away_goals: int) -> float:
    gd = abs(home_goals - away_goals)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


def _outcome_w(home_goals: int, away_goals: int) -> float:
    if home_goals > away_goals:
        return 1.0
    if home_goals < away_goals:
        return 0.0
    return 0.5


def update_match(
    home: EloRating,
    away: EloRating,
    home_goals: int,
    away_goals: int,
    *,
    tournament: str = "DEFAULT",
    neutral_venue: bool = False,
    home_advantage: float = DEFAULT_HOME_ADVANTAGE,
) -> Tuple[EloRating, EloRating]:
    """Return updated (home, away) ratings after one match.

    Pure function — does not mutate inputs.
    """
    k = _TOURNAMENT_K.get(tournament, _TOURNAMENT_K["DEFAULT"])
    g = goal_difference_multiplier(home_goals, away_goals)
    ha = 0.0 if neutral_venue else home_advantage
    we_home = expected_score(home.rating, away.rating, home_advantage=ha)
    w_home = _outcome_w(home_goals, away_goals)
    delta = k * g * (w_home - we_home)
    return (
        EloRating(home.team_id, home.rating + delta, home.n_matches + 1),
        EloRating(away.team_id, away.rating - delta, away.n_matches + 1),
    )


class EloTable:
    """Mutable container for a set of Elo ratings keyed by team_id."""

    def __init__(self, initial: Optional[Mapping[str, float]] = None, *, default: float = 1500.0):
        self._ratings: Dict[str, EloRating] = {}
        self._default = default
        if initial:
            for tid, r in initial.items():
                self._ratings[tid] = EloRating(tid, float(r))

    def get(self, team_id: str) -> EloRating:
        if team_id not in self._ratings:
            self._ratings[team_id] = EloRating(team_id, self._default)
        return self._ratings[team_id]

    def set(self, rating: EloRating) -> None:
        self._ratings[rating.team_id] = rating

    def items(self) -> Iterable[Tuple[str, EloRating]]:
        return self._ratings.items()

    def update(
        self,
        home_id: str,
        away_id: str,
        home_goals: int,
        away_goals: int,
        *,
        tournament: str = "DEFAULT",
        neutral_venue: bool = False,
        home_advantage: float = DEFAULT_HOME_ADVANTAGE,
    ) -> Tuple[EloRating, EloRating]:
        h, a = update_match(
            self.get(home_id),
            self.get(away_id),
            home_goals,
            away_goals,
            tournament=tournament,
            neutral_venue=neutral_venue,
            home_advantage=home_advantage,
        )
        self._ratings[home_id] = h
        self._ratings[away_id] = a
        return h, a

    def fit(
        self,
        matches: Iterable[Mapping[str, object]],
        *,
        tournament_key: str = "competition",
        home_key: str = "home_team_id",
        away_key: str = "away_team_id",
        home_goals_key: str = "home_goals",
        away_goals_key: str = "away_goals",
        neutral_key: str = "neutral_venue",
    ) -> None:
        """Replay a sequence of matches in chronological order to rebuild Elo."""
        for m in matches:
            self.update(
                str(m[home_key]),
                str(m[away_key]),
                int(m[home_goals_key]),
                int(m[away_goals_key]),
                tournament=str(m.get(tournament_key, "DEFAULT")),
                neutral_venue=bool(m.get(neutral_key, False)),
            )

    def to_dict(self) -> Dict[str, float]:
        return {tid: r.rating for tid, r in self._ratings.items()}
