"""
Player goal/shot share model.

Given a team's expected goals (lambda from Dixon-Coles), distribute
those goals across the on-pitch players. We use a Dirichlet prior over
player goal shares within a team and update from history.

Cold start (no observed shots): use a position-based prior:
    forwards   : 0.55 of team goal share
    midfield   : 0.25
    defenders  : 0.10
    keepers    : 0.00
The remaining 0.10 is the OPP_OG / penalty noise floor — we keep an
explicit "non-listed" bucket so anytime-scorer probabilities don't sum
to 1 (a sim that the goal goes to no listed scorer is valid; e.g.,
defenders not in the registered prop list, or an own goal).

When historical goals + minutes are provided we fit per-player rates
(goals per 90) and convert them to shares conditional on minutes played.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np


_POSITION_PRIORS = {
    "GK": 0.0,
    "DF": 0.10,
    "MF": 0.25,
    "FW": 0.55,
}


@dataclass
class PlayerShareModel:
    """
    Goal/shot share weights per (team_id, player_id).

    Each player carries:
        weight: a non-negative float; share = weight / sum(weights_in_team)
        per90_goals: optional historical rate, used by the simulator to
            scale weight by expected minutes.
    """
    team_to_players: Dict[str, List[str]] = field(default_factory=dict)
    weight: Dict[str, float] = field(default_factory=dict)
    per90_goals: Dict[str, float] = field(default_factory=dict)
    other_share: Dict[str, float] = field(default_factory=dict)  # OG / non-listed bucket per team

    @classmethod
    def from_position_priors(
        cls,
        squads: Mapping[str, Sequence[Mapping[str, str]]],
        *,
        other_share: float = 0.10,
    ) -> "PlayerShareModel":
        """Cold-start using only listed positions.

        squads: {team_id: [{"player_id": str, "position": "GK|DF|MF|FW"}, ...]}
        """
        m = cls()
        for team_id, players in squads.items():
            ids = [str(p["player_id"]) for p in players]
            m.team_to_players[team_id] = ids
            for p in players:
                pid = str(p["player_id"])
                pos = str(p.get("position", "FW")).upper()
                m.weight[pid] = float(_POSITION_PRIORS.get(pos, 0.20))
            # normalize to (1 - other_share) within team
            tot = sum(m.weight[pid] for pid in ids) or 1.0
            target = 1.0 - other_share
            for pid in ids:
                m.weight[pid] = m.weight[pid] / tot * target
            m.other_share[team_id] = other_share
        return m

    @classmethod
    def fit(
        cls,
        history: Iterable[Mapping[str, object]],
        *,
        smoothing: float = 0.5,
        other_share: float = 0.08,
    ) -> "PlayerShareModel":
        """
        Fit per-player goal rates from historical match-level data.

        Each row in history must contain:
          team_id, player_id, position, goals, minutes
        Smoothing adds a Dirichlet pseudo-count derived from the position
        prior; this keeps small-sample players from dominating.
        """
        per_team: Dict[str, Dict[str, Dict[str, float]]] = {}
        for row in history:
            team = str(row["team_id"])
            pid = str(row["player_id"])
            pos = str(row.get("position", "FW")).upper()
            goals = float(row.get("goals", 0.0))
            minutes = float(row.get("minutes", 0.0))
            slot = per_team.setdefault(team, {}).setdefault(
                pid, {"goals": 0.0, "minutes": 0.0, "pos": pos, "prior": _POSITION_PRIORS.get(pos, 0.20)}
            )
            slot["goals"] += goals
            slot["minutes"] += minutes

        m = cls()
        for team, players in per_team.items():
            ids = sorted(players.keys())
            m.team_to_players[team] = ids
            raw_weights: Dict[str, float] = {}
            for pid in ids:
                p = players[pid]
                # smoothed per90: (goals + smoothing*prior) / ((minutes + smoothing*90) / 90)
                num = p["goals"] + smoothing * p["prior"]
                den = max((p["minutes"] + smoothing * 90.0) / 90.0, 1e-3)
                rate = num / den
                m.per90_goals[pid] = rate
                # weight = rate * expected minute share (default 1.0; minutes model adjusts at sim time)
                raw_weights[pid] = rate
            tot = sum(raw_weights.values())
            target = 1.0 - other_share
            if tot <= 0.0:
                # fallback to position priors
                tot = sum(players[pid]["prior"] for pid in ids) or 1.0
                for pid in ids:
                    m.weight[pid] = players[pid]["prior"] / tot * target
            else:
                for pid in ids:
                    m.weight[pid] = raw_weights[pid] / tot * target
            m.other_share[team] = other_share
        return m

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------
    def sample_scorer(
        self,
        rng: np.random.Generator,
        team_id: str,
        *,
        minutes_factor: Optional[Mapping[str, float]] = None,
    ) -> Optional[str]:
        """Sample a scoring player for `team_id`. Returns None for the OG/non-listed bucket."""
        ids = self.team_to_players.get(team_id, [])
        if not ids:
            return None
        weights = np.array(
            [
                self.weight.get(pid, 0.0) * (
                    minutes_factor[pid] if minutes_factor and pid in minutes_factor else 1.0
                )
                for pid in ids
            ],
            dtype=float,
        )
        other = float(self.other_share.get(team_id, 0.0))
        total = weights.sum() + other
        if total <= 0.0:
            return None
        r = rng.random() * total
        c = 0.0
        for pid, w in zip(ids, weights):
            c += w
            if r <= c:
                return pid
        return None  # other bucket
