"""
Cards model.

Per-player Poisson rate for yellow + red cards in a match, conditioned on:
  - player baseline (cards per 90)
  - minutes played (sample from MinutesModel)
  - referee strictness (multiplier; books underweight this)
  - opponent fouls drawn (optional team-level multiplier)

Reds are split into:
  - straight reds: tiny per-90 baseline
  - second-yellow reds: derived from the yellow rate (P(2y) ≈ 0.5 · λ_yel² for
    typical λ <= 0.5, but we use a conservative 0.05 · λ_yel cap)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np


@dataclass
class _PlayerCardsRate:
    yellow_per90: float = 0.18
    red_per90: float = 0.005


@dataclass
class CardsModel:
    """Cards rates per player + referee multiplier."""
    players: Dict[str, _PlayerCardsRate] = field(default_factory=dict)
    referee_multiplier: Dict[str, float] = field(default_factory=dict)
    league_yellow_per_team_per90: float = 1.6
    league_red_per_team_per90: float = 0.07

    @classmethod
    def from_priors(
        cls,
        player_rates: Mapping[str, Mapping[str, float]],
        referee_strictness: Optional[Mapping[str, float]] = None,
    ) -> "CardsModel":
        m = cls()
        for pid, r in player_rates.items():
            m.players[pid] = _PlayerCardsRate(
                yellow_per90=float(r.get("yellow_per90", 0.18)),
                red_per90=float(r.get("red_per90", 0.005)),
            )
        if referee_strictness:
            m.referee_multiplier = {k: float(v) for k, v in referee_strictness.items()}
        return m

    def player_rate(
        self, player_id: str, *, referee_id: Optional[str] = None
    ) -> Tuple[float, float]:
        """Return (yellow_per90, red_per90) for a player at the given referee."""
        p = self.players.get(player_id, _PlayerCardsRate())
        mult = float(self.referee_multiplier.get(referee_id or "", 1.0))
        return p.yellow_per90 * mult, p.red_per90 * mult

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------
    def sample_cards(
        self,
        rng: np.random.Generator,
        player_minutes: Mapping[str, int],
        *,
        referee_id: Optional[str] = None,
        team_yellow_floor_per90: Optional[Mapping[str, float]] = None,
    ) -> Dict[str, List[Tuple[str, int]]]:
        """Sample cards events for a single match.

        Returns:
            {"yellow": [(player_id, minute), ...],
             "red":    [(player_id, minute), ...]}

        team_yellow_floor_per90 (optional): {team_id: per90} — used as a
        soft guard so total yellow expectations don't fall below the league
        floor when individual player priors are too weak; not enforced
        per-team here, but exposed for callers to set on the simulator.
        """
        yellow: List[Tuple[str, int]] = []
        red: List[Tuple[str, int]] = []
        for pid, mins in player_minutes.items():
            if mins <= 0:
                continue
            ly, lr = self.player_rate(pid, referee_id=referee_id)
            scale = mins / 90.0
            n_y = rng.poisson(ly * scale)
            n_r = rng.poisson(lr * scale)
            for _ in range(int(n_y)):
                m = int(rng.uniform(1, max(2, mins)))
                yellow.append((pid, m))
            for _ in range(int(n_r)):
                m = int(rng.uniform(1, max(2, mins)))
                red.append((pid, m))
        return {"yellow": yellow, "red": red}
