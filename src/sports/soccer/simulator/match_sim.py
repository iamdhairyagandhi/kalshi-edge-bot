"""
Joint Monte Carlo match simulator.

Per simulation step, in order:
  1. Sample per-player minutes for both squads.
  2. Sample team goal totals (home_goals, away_goals) from the
     Dixon-Coles joint distribution. We sample by drawing a uniform U
     from the precomputed CDF of the score matrix, rather than
     independent Poissons, so the τ low-score correlation is preserved.
  3. For each goal, sample a scoring player using the player share
     model weighted by minutes played.
  4. Sample cards events from the cards model.
  5. (Optional) sample shots-on-target per player from a per-90 rate.

Returns a `MatchSim` object the bet-builder can evaluate any leg against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np

from src.sports.soccer.models.cards import CardsModel
from src.sports.soccer.models.dixon_coles import (
    DixonColesModel,
    joint_score_matrix,
)
from src.sports.soccer.models.minutes import MinutesModel
from src.sports.soccer.models.player_share import PlayerShareModel
from src.sports.soccer.types import MatchSim


@dataclass
class SimulationConfig:
    n_sims: int = 10_000
    max_goals: int = 10
    seed: Optional[int] = None
    referee_id: Optional[str] = None


@dataclass
class MatchSimulator:
    """Owns the full layer stack and produces MatchSim arrays per fixture."""
    score_model: DixonColesModel
    player_share: PlayerShareModel
    minutes: MinutesModel
    cards: CardsModel
    squads: Dict[str, List[str]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def simulate(
        self,
        home_team_id: str,
        away_team_id: str,
        *,
        neutral_venue: bool = True,
        config: Optional[SimulationConfig] = None,
    ) -> List[MatchSim]:
        cfg = config or SimulationConfig()
        rng = np.random.default_rng(cfg.seed)
        # Pre-compute and flatten the joint score matrix once per match.
        sm = self.score_model.score_matrix(
            home_team_id, away_team_id, neutral=neutral_venue, max_goals=cfg.max_goals
        )
        flat = sm.flatten()
        cdf = np.cumsum(flat)
        cdf_total = cdf[-1] if cdf[-1] > 0 else 1.0

        home_squad = self.squads.get(home_team_id, [])
        away_squad = self.squads.get(away_team_id, [])

        out: List[MatchSim] = []
        for _ in range(cfg.n_sims):
            out.append(
                _simulate_one(
                    rng,
                    flat=flat,
                    cdf=cdf,
                    cdf_total=cdf_total,
                    side_dim=cfg.max_goals + 1,
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    home_squad=home_squad,
                    away_squad=away_squad,
                    player_share=self.player_share,
                    minutes_model=self.minutes,
                    cards_model=self.cards,
                    referee_id=cfg.referee_id,
                )
            )
        return out


def simulate_match(
    score_model: DixonColesModel,
    player_share: PlayerShareModel,
    minutes: MinutesModel,
    cards: CardsModel,
    squads: Mapping[str, Sequence[str]],
    home_team_id: str,
    away_team_id: str,
    *,
    neutral_venue: bool = True,
    config: Optional[SimulationConfig] = None,
) -> List[MatchSim]:
    """Functional shorthand for one-off use without holding a simulator."""
    sim = MatchSimulator(
        score_model=score_model,
        player_share=player_share,
        minutes=minutes,
        cards=cards,
        squads={k: list(v) for k, v in squads.items()},
    )
    return sim.simulate(home_team_id, away_team_id, neutral_venue=neutral_venue, config=config)


# ----------------------------------------------------------------------
# inner loop
# ----------------------------------------------------------------------

def _simulate_one(
    rng: np.random.Generator,
    *,
    flat: np.ndarray,
    cdf: np.ndarray,
    cdf_total: float,
    side_dim: int,
    home_team_id: str,
    away_team_id: str,
    home_squad: Sequence[str],
    away_squad: Sequence[str],
    player_share: PlayerShareModel,
    minutes_model: MinutesModel,
    cards_model: CardsModel,
    referee_id: Optional[str],
) -> MatchSim:
    # 1) minutes
    minutes_h = minutes_model.sample_minutes_played(rng, home_squad) if home_squad else {}
    minutes_a = minutes_model.sample_minutes_played(rng, away_squad) if away_squad else {}
    minutes_played = {**minutes_h, **minutes_a}
    factor_h = {pid: m / 95.0 for pid, m in minutes_h.items()}
    factor_a = {pid: m / 95.0 for pid, m in minutes_a.items()}

    # 2) score
    u = rng.random() * cdf_total
    idx = int(np.searchsorted(cdf, u, side="left"))
    idx = min(idx, len(flat) - 1)
    home_goals = idx // side_dim
    away_goals = idx % side_dim

    # 3) scorers
    home_scorers: List[tuple] = []
    away_scorers: List[tuple] = []
    for _ in range(home_goals):
        m = int(rng.integers(1, 95))
        pid = player_share.sample_scorer(rng, home_team_id, minutes_factor=factor_h)
        if pid is not None:
            home_scorers.append((pid, m))
    for _ in range(away_goals):
        m = int(rng.integers(1, 95))
        pid = player_share.sample_scorer(rng, away_team_id, minutes_factor=factor_a)
        if pid is not None:
            away_scorers.append((pid, m))

    # 4) cards
    cards = cards_model.sample_cards(rng, minutes_played, referee_id=referee_id)

    return MatchSim(
        home_goals=home_goals,
        away_goals=away_goals,
        home_scorers=home_scorers,
        away_scorers=away_scorers,
        yellow_cards=cards["yellow"],
        red_cards=cards["red"],
        minutes_played=minutes_played,
    )
