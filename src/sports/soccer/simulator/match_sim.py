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
  5. Sample per-team match stats (corners, shots, shots on target,
     fouls) from per-team Poisson rates modulated by the team's
     attack/defense parameters (see _team_stat_rates).

Returns a `MatchSim` object the bet-builder can evaluate any leg against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from src.sports.soccer.models.cards import CardsModel
from src.sports.soccer.models.dixon_coles import (
    DixonColesModel,
    joint_score_matrix,
)
from src.sports.soccer.models.minutes import MinutesModel
from src.sports.soccer.models.player_share import PlayerShareModel
from src.sports.soccer.types import MatchSim


# League-average per-team-per-match rates. Conservative priors used when
# no per-team historical data is available. These are *per team*, not
# combined.
DEFAULT_CORNERS_RATE = 5.3       # ~10.6 total corners / match
DEFAULT_SHOTS_RATE = 12.5        # ~25 total shots / match
DEFAULT_SOT_RATIO = 0.36         # ~9 SOT / match (~4.5 per team)
DEFAULT_FOULS_RATE = 10.6        # ~21 total fouls / match
# Sensitivity of stat rates to team attack / opponent defense (in DC log
# space). Smaller than goal sensitivity because shot generation is less
# concentrated than goal scoring.
STAT_ATTACK_BETA = 0.35
STAT_DEFENSE_BETA = 0.25


@dataclass
class SimulationConfig:
    n_sims: int = 10_000
    max_goals: int = 10
    seed: Optional[int] = None
    referee_id: Optional[str] = None
    # Override rates if the caller has team-specific priors (e.g. fitted
    # from StatsBomb event data). Keys are team_ids; values are
    # {"corners": float, "shots": float, "sot_ratio": float, "fouls": float}.
    team_stat_overrides: Dict[str, Dict[str, float]] = field(default_factory=dict)


def _team_stat_rates(
    *,
    attack: float,
    opp_defense: float,
    score_model: DixonColesModel,
    overrides: Optional[Dict[str, float]] = None,
) -> Tuple[float, float, float, float]:
    """Return (lambda_corners, lambda_shots, sot_ratio, lambda_fouls)
    for one team given its attack parameter and the opponent's defense.

    Attack/defense come from the same DC parameter table. Higher attack
    and higher opp defense both raise shot/corner rates (we add them in
    log-space). Fouls are mostly possession-symmetric and only weakly
    modulated.
    """
    log_corners = math.log(DEFAULT_CORNERS_RATE) + STAT_ATTACK_BETA * attack + STAT_DEFENSE_BETA * opp_defense
    log_shots = math.log(DEFAULT_SHOTS_RATE) + STAT_ATTACK_BETA * attack + STAT_DEFENSE_BETA * opp_defense
    log_fouls = math.log(DEFAULT_FOULS_RATE) + 0.1 * opp_defense  # tougher defenses commit slightly more fouls
    lam_corners = math.exp(log_corners)
    lam_shots = math.exp(log_shots)
    sot_ratio = DEFAULT_SOT_RATIO
    lam_fouls = math.exp(log_fouls)
    if overrides:
        if "corners" in overrides:
            lam_corners = float(overrides["corners"])
        if "shots" in overrides:
            lam_shots = float(overrides["shots"])
        if "sot_ratio" in overrides:
            sot_ratio = float(overrides["sot_ratio"])
        if "fouls" in overrides:
            lam_fouls = float(overrides["fouls"])
    return lam_corners, lam_shots, sot_ratio, lam_fouls


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

        # Pre-compute per-team match-stat rates once per fixture.
        home_attack = float(self.score_model.params.attack.get(home_team_id, 0.0))
        home_defense = float(self.score_model.params.defense.get(home_team_id, 0.0))
        away_attack = float(self.score_model.params.attack.get(away_team_id, 0.0))
        away_defense = float(self.score_model.params.defense.get(away_team_id, 0.0))
        h_overrides = cfg.team_stat_overrides.get(home_team_id)
        a_overrides = cfg.team_stat_overrides.get(away_team_id)
        h_corners, h_shots, h_sot_ratio, h_fouls = _team_stat_rates(
            attack=home_attack, opp_defense=away_defense,
            score_model=self.score_model, overrides=h_overrides,
        )
        a_corners, a_shots, a_sot_ratio, a_fouls = _team_stat_rates(
            attack=away_attack, opp_defense=home_defense,
            score_model=self.score_model, overrides=a_overrides,
        )

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
                    h_corners_rate=h_corners,
                    a_corners_rate=a_corners,
                    h_shots_rate=h_shots,
                    a_shots_rate=a_shots,
                    h_sot_ratio=h_sot_ratio,
                    a_sot_ratio=a_sot_ratio,
                    h_fouls_rate=h_fouls,
                    a_fouls_rate=a_fouls,
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
    h_corners_rate: float,
    a_corners_rate: float,
    h_shots_rate: float,
    a_shots_rate: float,
    h_sot_ratio: float,
    a_sot_ratio: float,
    h_fouls_rate: float,
    a_fouls_rate: float,
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
    yellows = cards["yellow"]
    reds = cards["red"]
    # Bucket yellows by team for team-level card-count legs.
    home_squad_set = set(home_squad)
    away_squad_set = set(away_squad)
    home_yellow = sum(1 for pid, _ in yellows if pid in home_squad_set)
    away_yellow = sum(1 for pid, _ in yellows if pid in away_squad_set)

    # 5) per-team match stats (corners, shots, SOT, fouls)
    # Corners scale slightly with goals scored (attacking teams pile in)
    # but we keep this mild to avoid double-counting attack strength.
    h_corner_lam = h_corners_rate * (1.0 + 0.08 * home_goals)
    a_corner_lam = a_corners_rate * (1.0 + 0.08 * away_goals)
    home_corners = int(rng.poisson(h_corner_lam))
    away_corners = int(rng.poisson(a_corner_lam))
    # Shots also scale with goals (more shots when attacking is working).
    h_shot_lam = h_shots_rate * (1.0 + 0.10 * home_goals)
    a_shot_lam = a_shots_rate * (1.0 + 0.10 * away_goals)
    home_shots = int(rng.poisson(h_shot_lam))
    away_shots = int(rng.poisson(a_shot_lam))
    # SOT as Binomial(shots, ratio). Bounded by shots.
    home_sot = int(rng.binomial(home_shots, max(0.05, min(0.95, h_sot_ratio))))
    away_sot = int(rng.binomial(away_shots, max(0.05, min(0.95, a_sot_ratio))))
    # SOT must be >= goals (every goal is by definition on target).
    home_sot = max(home_sot, home_goals)
    away_sot = max(away_sot, away_goals)
    # Fouls: largely independent of goals; mild bump when defending more.
    home_fouls = int(rng.poisson(h_fouls_rate))
    away_fouls = int(rng.poisson(a_fouls_rate))

    return MatchSim(
        home_goals=home_goals,
        away_goals=away_goals,
        home_scorers=home_scorers,
        away_scorers=away_scorers,
        yellow_cards=yellows,
        red_cards=reds,
        minutes_played=minutes_played,
        home_corners=home_corners,
        away_corners=away_corners,
        home_shots=home_shots,
        away_shots=away_shots,
        home_shots_on_target=home_sot,
        away_shots_on_target=away_sot,
        home_fouls=home_fouls,
        away_fouls=away_fouls,
        home_yellow=home_yellow,
        away_yellow=away_yellow,
    )
