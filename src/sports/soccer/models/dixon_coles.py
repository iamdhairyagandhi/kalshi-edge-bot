"""
Dixon-Coles bivariate-Poisson model for low-score correlated football scores.

References:
- Dixon, M.J. and Coles, S.G. (1997). Modelling Association Football
  Scores and Inefficiencies in the Football Betting Market. Applied Stats,
  46(2), 265-280.

Model:
    log(λ_h) = α_home + β_away + γ_homeadv
    log(λ_a) = α_away + β_home

    P(X=x, Y=y) = τ(x,y; λ_h, λ_a, ρ) · Pois(x|λ_h) · Pois(y|λ_a)

    τ(0,0) = 1 - λ_h λ_a ρ
    τ(0,1) = 1 + λ_h ρ
    τ(1,0) = 1 + λ_a ρ
    τ(1,1) = 1 - ρ
    τ(x,y) = 1 otherwise

Identifiability constraint: Σ α = 0 (sum-to-zero across teams).

Fit by MLE with optional time-decay weight w_i = exp(-ξ · Δt_days_i).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize

from src.sports.soccer.ratings.elo import EloTable


@dataclass
class DixonColesParams:
    teams: List[str]
    attack: Dict[str, float] = field(default_factory=dict)
    defense: Dict[str, float] = field(default_factory=dict)
    home_advantage: float = 0.25
    rho: float = -0.05
    log_likelihood: float = float("nan")
    n_matches: int = 0

    def lambdas(self, home: str, away: str, *, neutral: bool = False) -> Tuple[float, float]:
        ha = 0.0 if neutral else self.home_advantage
        lam_h = math.exp(self.attack[home] + self.defense[away] + ha)
        lam_a = math.exp(self.attack[away] + self.defense[home])
        return lam_h, lam_a


def _tau(x: int, y: int, lam_h: float, lam_a: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1.0 - lam_h * lam_a * rho
    if x == 0 and y == 1:
        return 1.0 + lam_h * rho
    if x == 1 and y == 0:
        return 1.0 + lam_a * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _log_pois(k: int, lam: float) -> float:
    if lam <= 0.0:
        return -1e12
    return k * math.log(lam) - lam - math.lgamma(k + 1)


def joint_score_log_pmf(
    x: int, y: int, lam_h: float, lam_a: float, rho: float
) -> float:
    """log P(X=x, Y=y) under the Dixon-Coles correction."""
    t = _tau(x, y, lam_h, lam_a, rho)
    if t <= 0.0:
        return -1e12
    return math.log(t) + _log_pois(x, lam_h) + _log_pois(y, lam_a)


def joint_score_matrix(
    lam_h: float,
    lam_a: float,
    rho: float,
    *,
    max_goals: int = 10,
) -> np.ndarray:
    """(max_goals+1, max_goals+1) matrix of joint score probabilities.

    Rows are home goals (0..max_goals), columns are away goals.
    """
    p = np.zeros((max_goals + 1, max_goals + 1), dtype=float)
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p[x, y] = math.exp(joint_score_log_pmf(x, y, lam_h, lam_a, rho))
    s = p.sum()
    if s > 0:
        p /= s
    return p


def derive_outcome_probs(score_matrix: np.ndarray) -> Dict[str, float]:
    """1X2 + BTTS + total goals 2.5 line marginals from the score matrix."""
    n = score_matrix.shape[0]
    home_win = float(np.tril(score_matrix, k=-1).sum())
    away_win = float(np.triu(score_matrix, k=1).sum())
    draw = float(np.trace(score_matrix))
    btts_yes = float(score_matrix[1:, 1:].sum())
    over_25 = 0.0
    for x in range(n):
        for y in range(n):
            if x + y >= 3:
                over_25 += float(score_matrix[x, y])
    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "btts_yes": btts_yes,
        "btts_no": 1.0 - btts_yes,
        "over_2_5": over_25,
        "under_2_5": 1.0 - over_25,
    }


@dataclass
class DixonColesModel:
    """Dixon-Coles fit + scoring helpers.

    Typical usage:
        m = DixonColesModel.fit(matches)
        score_matrix = m.score_matrix("ENG", "FRA", neutral=True)
        probs = m.outcome_probs("ENG", "FRA", neutral=True)
    """

    params: DixonColesParams

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------
    @classmethod
    def fit(
        cls,
        matches: Sequence[Mapping[str, object]],
        *,
        decay_per_day: float = 0.0,
        reference_unix: Optional[float] = None,
        ridge: float = 1e-3,
        max_iter: int = 200,
    ) -> "DixonColesModel":
        """MLE fit on a sequence of matches.

        Each match must expose: home_team_id, away_team_id, home_goals,
        away_goals, kickoff_unix (optional, for time decay), neutral_venue.
        """
        if not matches:
            raise ValueError("DixonColesModel.fit requires at least one match")
        teams = sorted({str(m["home_team_id"]) for m in matches} |
                       {str(m["away_team_id"]) for m in matches})
        idx = {t: i for i, t in enumerate(teams)}
        n_teams = len(teams)

        home = np.array([idx[str(m["home_team_id"])] for m in matches], dtype=int)
        away = np.array([idx[str(m["away_team_id"])] for m in matches], dtype=int)
        gh = np.array([int(m["home_goals"]) for m in matches], dtype=int)
        ga = np.array([int(m["away_goals"]) for m in matches], dtype=int)
        neutral = np.array(
            [bool(m.get("neutral_venue", False)) for m in matches], dtype=bool
        )

        if decay_per_day > 0.0:
            ref = (
                float(reference_unix)
                if reference_unix is not None
                else max(float(m.get("kickoff_unix", 0.0)) for m in matches)
            )
            ages_days = np.array(
                [
                    max(0.0, (ref - float(m.get("kickoff_unix", ref))) / 86400.0)
                    for m in matches
                ],
                dtype=float,
            )
            weights = np.exp(-decay_per_day * ages_days)
        else:
            weights = np.ones(len(matches), dtype=float)

        # Parameter vector: [α_1..α_{n-1}, β_1..β_n, γ, ρ]
        # α_n is fixed by sum-to-zero: α_n = -Σ_{i<n} α_i
        # n_params = (n_teams - 1) + n_teams + 2

        def unpack(theta: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float, float]:
            a_free = theta[: n_teams - 1]
            a_full = np.concatenate([a_free, np.array([-a_free.sum()])])
            b = theta[n_teams - 1 : n_teams - 1 + n_teams]
            gamma = float(theta[-2])
            rho = float(theta[-1])
            return a_full, b, gamma, rho

        # Vectorized log likelihood (Pois part) + per-match tau correction loop.
        def neg_log_lik(theta: np.ndarray) -> float:
            a, b, gamma, rho = unpack(theta)
            ha = np.where(neutral, 0.0, gamma)
            log_lh = a[home] + b[away] + ha
            log_la = a[away] + b[home]
            lam_h = np.exp(log_lh)
            lam_a = np.exp(log_la)
            # Pois log pmf
            ll = (
                gh * log_lh - lam_h - _logfact(gh) +
                ga * log_la - lam_a - _logfact(ga)
            )
            # tau correction (only matters for low scores)
            t = _tau_array(gh, ga, lam_h, lam_a, rho)
            # guard tau against non-positive values
            t = np.where(t <= 1e-12, 1e-12, t)
            ll = ll + np.log(t)
            ll = ll * weights
            # ridge on attack/defense to prevent divergence on small data
            reg = ridge * (np.sum(a ** 2) + np.sum(b ** 2))
            return float(-(ll.sum()) + reg)

        # init
        theta0 = np.zeros(n_teams - 1 + n_teams + 2)
        theta0[-2] = 0.25  # home advantage
        theta0[-1] = -0.05  # rho

        # ρ has to keep all τ positive: |ρ| <= min(1/λ_hλ_a, 1, 1/λ_h, 1/λ_a).
        # Hard bounds [-0.2, 0.2] are safe for typical λ in [0.5, 4.0].
        bounds = (
            [(-2.5, 2.5)] * (n_teams - 1)
            + [(-2.5, 2.5)] * n_teams
            + [(-0.5, 1.0), (-0.2, 0.2)]
        )

        res = minimize(
            neg_log_lik,
            theta0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": max_iter},
        )
        a, b, gamma, rho = unpack(res.x)
        params = DixonColesParams(
            teams=teams,
            attack={teams[i]: float(a[i]) for i in range(n_teams)},
            defense={teams[i]: float(b[i]) for i in range(n_teams)},
            home_advantage=float(gamma),
            rho=float(rho),
            log_likelihood=float(-res.fun),
            n_matches=int(len(matches)),
        )
        return cls(params=params)

    # ------------------------------------------------------------------
    # Pre-tournament priors from Elo (used when match data is sparse)
    # ------------------------------------------------------------------
    @classmethod
    def from_elo_priors(
        cls,
        elo: EloTable,
        *,
        league_avg_goals: float = 1.35,
        elo_to_strength_scale: float = 0.0025,
        rho: float = -0.05,
        home_advantage: float = 0.25,
    ) -> "DixonColesModel":
        """Cold-start params using Elo as the only signal.

        Centers attack/defense around 0 with sum-to-zero and translates
        Elo distance from the table mean into log-rate deltas.
        """
        teams = sorted(t for t, _ in elo.items())
        if not teams:
            raise ValueError("EloTable is empty")
        ratings = np.array([elo.get(t).rating for t in teams])
        centered = ratings - ratings.mean()
        # baseline attack so exp(α + β + γ) ≈ league_avg_goals when both teams are average
        base = math.log(max(league_avg_goals, 1e-3)) - home_advantage / 2.0
        attack = {t: float(centered[i] * elo_to_strength_scale + base / 2.0) for i, t in enumerate(teams)}
        defense = {t: float(-centered[i] * elo_to_strength_scale + base / 2.0) for i, t in enumerate(teams)}
        # enforce sum-to-zero on attack
        a_mean = sum(attack.values()) / len(teams)
        attack = {t: v - a_mean for t, v in attack.items()}
        params = DixonColesParams(
            teams=teams,
            attack=attack,
            defense=defense,
            home_advantage=home_advantage,
            rho=rho,
            log_likelihood=float("nan"),
            n_matches=0,
        )
        return cls(params=params)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------
    def lambdas(self, home: str, away: str, *, neutral: bool = False) -> Tuple[float, float]:
        return self.params.lambdas(home, away, neutral=neutral)

    def score_matrix(
        self, home: str, away: str, *, neutral: bool = False, max_goals: int = 10
    ) -> np.ndarray:
        lam_h, lam_a = self.lambdas(home, away, neutral=neutral)
        return joint_score_matrix(lam_h, lam_a, self.params.rho, max_goals=max_goals)

    def outcome_probs(
        self, home: str, away: str, *, neutral: bool = False, max_goals: int = 10
    ) -> Dict[str, float]:
        return derive_outcome_probs(self.score_matrix(home, away, neutral=neutral, max_goals=max_goals))


# ----------------------------------------------------------------------
# vectorized helpers
# ----------------------------------------------------------------------

def _logfact(k: np.ndarray) -> np.ndarray:
    """log(k!) for non-negative integer arrays."""
    return np.array([math.lgamma(int(v) + 1) for v in np.atleast_1d(k)])


def _tau_array(
    x: np.ndarray, y: np.ndarray, lam_h: np.ndarray, lam_a: np.ndarray, rho: float
) -> np.ndarray:
    out = np.ones_like(lam_h, dtype=float)
    mask_00 = (x == 0) & (y == 0)
    mask_01 = (x == 0) & (y == 1)
    mask_10 = (x == 1) & (y == 0)
    mask_11 = (x == 1) & (y == 1)
    out = np.where(mask_00, 1.0 - lam_h * lam_a * rho, out)
    out = np.where(mask_01, 1.0 + lam_h * rho, out)
    out = np.where(mask_10, 1.0 + lam_a * rho, out)
    out = np.where(mask_11, 1.0 - rho, out)
    return out
