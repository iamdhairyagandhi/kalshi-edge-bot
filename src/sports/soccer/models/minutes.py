"""
Minutes model.

Per-player distribution of (started?, minute_subbed_off, minute_subbed_on).
Without this layer every player prop is wrong because a striker subbed
at the 60th minute has roughly 2/3 the goal probability a naive model gives.

We approximate the minute distribution as:
  - Bernoulli(start_prob) for "starts the match"
  - If started: Beta-distributed minute-subbed-off in [45, 95] (heavier
    mass near 70-90'), with `prob_finishes_match` mass at 95 (bottom of injury
    time).
  - If not started: Beta-distributed minute-on in [45, 90] with
    `prob_unused_sub` mass that the player never enters.

The output of `sample_minutes_played` feeds the player-share goal sampler
and the cards rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

import numpy as np


@dataclass
class _PlayerMinutes:
    start_prob: float = 0.5
    prob_finishes_match: float = 0.6  # given start
    prob_unused_sub: float = 0.5      # given non-start
    avg_off_minute: float = 75.0      # if subbed off
    avg_on_minute: float = 70.0       # if subbed on
    sub_window_std: float = 12.0


@dataclass
class MinutesModel:
    """Per-player minute distribution. Defaults are sane for international squads."""
    players: Dict[str, _PlayerMinutes] = field(default_factory=dict)

    @classmethod
    def from_starting_priors(
        cls,
        priors: Mapping[str, Mapping[str, float]],
    ) -> "MinutesModel":
        """priors: {player_id: {"start_prob": float, "minutes_avg": float}}."""
        m = cls()
        for pid, vals in priors.items():
            sp = float(vals.get("start_prob", 0.5))
            avg = float(vals.get("minutes_avg", 60.0))
            # Map avg minutes to plausible sub-time and finish prob.
            if sp > 0.0:
                # If the player typically plays >85 mins when starting, they finish often.
                expected_when_start = avg / sp if sp > 0 else avg
                fin = max(0.05, min(0.95, (expected_when_start - 60.0) / 35.0))
                avg_off = max(50.0, min(95.0, expected_when_start - 5.0))
            else:
                fin = 0.3
                avg_off = 75.0
            m.players[pid] = _PlayerMinutes(
                start_prob=sp,
                prob_finishes_match=fin,
                avg_off_minute=avg_off,
                prob_unused_sub=max(0.0, 1.0 - sp) * 0.7,
                avg_on_minute=70.0,
            )
        return m

    def get(self, player_id: str) -> _PlayerMinutes:
        return self.players.setdefault(player_id, _PlayerMinutes())

    def sample_minutes_played(
        self,
        rng: np.random.Generator,
        player_ids,
    ) -> Dict[str, int]:
        """Sample integer minutes played per player for one match (0..95)."""
        out: Dict[str, int] = {}
        for pid in player_ids:
            p = self.get(pid)
            started = rng.random() < p.start_prob
            if started:
                if rng.random() < p.prob_finishes_match:
                    out[pid] = 95
                    continue
                # subbed off
                m = int(np.clip(rng.normal(p.avg_off_minute, p.sub_window_std), 30, 94))
                out[pid] = m
            else:
                if rng.random() < p.prob_unused_sub:
                    out[pid] = 0
                    continue
                m_on = int(np.clip(rng.normal(p.avg_on_minute, p.sub_window_std), 45, 90))
                out[pid] = max(0, 95 - m_on)
        return out

    def minutes_factor(
        self,
        player_ids,
        sample: Optional[Mapping[str, int]] = None,
    ) -> Dict[str, float]:
        """Minute fraction (0..1) used as a weight in the player-share sampler.

        If a per-sim minutes sample is provided, use it; otherwise fall
        back to the model's expected minutes per player.
        """
        out: Dict[str, float] = {}
        for pid in player_ids:
            if sample is not None and pid in sample:
                out[pid] = sample[pid] / 95.0
            else:
                p = self.get(pid)
                if p.start_prob > 0:
                    expected = p.start_prob * p.avg_off_minute + (1 - p.start_prob) * (
                        (1 - p.prob_unused_sub) * (95 - p.avg_on_minute)
                    )
                else:
                    expected = (1 - p.prob_unused_sub) * (95 - p.avg_on_minute)
                out[pid] = max(0.0, expected / 95.0)
        return out
