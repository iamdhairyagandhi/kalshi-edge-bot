"""
State-space updater for in-tournament rating shifts.

Pre-tournament Elo / Dixon-Coles params are priors; once real WC games are
played they need to update fast (your spec: "pre-tournament ratings die
by matchday 2"). We use a simple Kalman filter on (attack, defense) per
team:

  state_t = state_{t-1} + w,  w ~ N(0, Q)
  obs:  goals_for - goals_against in match -> y ~ N(state_diff, R)

This is a working approximation — not a full vector-state Kalman across
all teams — but for a 32-team tournament it has the right behavior:
fast adaptation when results are surprising, slow when they confirm priors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

import math


@dataclass
class _TeamState:
    attack: float = 0.0
    defense: float = 0.0
    p_attack: float = 0.04   # variance estimate
    p_defense: float = 0.04


@dataclass
class StateSpaceUpdater:
    """Per-team Kalman update for attack/defense log-rates."""
    process_var: float = 0.005     # Q: how much we let strength drift between matches
    obs_var: float = 0.5           # R: noisiness of one match outcome
    home_advantage: float = 0.25
    states: Dict[str, _TeamState] = field(default_factory=dict)

    @classmethod
    def from_dixon_coles(
        cls,
        attack: Mapping[str, float],
        defense: Mapping[str, float],
        *,
        home_advantage: float = 0.25,
        process_var: float = 0.005,
        obs_var: float = 0.5,
    ) -> "StateSpaceUpdater":
        s = cls(process_var=process_var, obs_var=obs_var, home_advantage=home_advantage)
        for tid in set(attack) | set(defense):
            s.states[tid] = _TeamState(
                attack=float(attack.get(tid, 0.0)),
                defense=float(defense.get(tid, 0.0)),
            )
        return s

    def get(self, team_id: str) -> _TeamState:
        return self.states.setdefault(team_id, _TeamState())

    def attack_defense(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        return (
            {tid: s.attack for tid, s in self.states.items()},
            {tid: s.defense for tid, s in self.states.items()},
        )

    def update_match(
        self,
        home_id: str,
        away_id: str,
        home_goals: int,
        away_goals: int,
        *,
        neutral_venue: bool = True,
    ) -> None:
        """Single Kalman update step for one observed match."""
        h = self.get(home_id)
        a = self.get(away_id)
        ha = 0.0 if neutral_venue else self.home_advantage

        # process step (drift)
        h.p_attack += self.process_var
        h.p_defense += self.process_var
        a.p_attack += self.process_var
        a.p_defense += self.process_var

        # Observed log-rates from the game (with smoothing for 0-goal counts)
        obs_log_h = math.log(max(home_goals, 0.5))
        obs_log_a = math.log(max(away_goals, 0.5))

        # Predicted log-rates from current state
        pred_h = h.attack + a.defense + ha
        pred_a = a.attack + h.defense

        # Innovations
        inno_h = obs_log_h - pred_h
        inno_a = obs_log_a - pred_a

        # Each innovation updates two parameters: the attacking team's
        # attack and the defending team's defense. Spread the gain
        # equally across them (Kalman gain on a 1D pseudo-state).
        k_h_att = h.p_attack / (h.p_attack + a.p_defense + self.obs_var)
        k_a_def = a.p_defense / (h.p_attack + a.p_defense + self.obs_var)
        k_a_att = a.p_attack / (a.p_attack + h.p_defense + self.obs_var)
        k_h_def = h.p_defense / (a.p_attack + h.p_defense + self.obs_var)

        h.attack += k_h_att * inno_h
        a.defense += k_a_def * inno_h
        a.attack += k_a_att * inno_a
        h.defense += k_h_def * inno_a

        # Variance updates
        h.p_attack *= (1.0 - k_h_att)
        a.p_defense *= (1.0 - k_a_def)
        a.p_attack *= (1.0 - k_a_att)
        h.p_defense *= (1.0 - k_h_def)
