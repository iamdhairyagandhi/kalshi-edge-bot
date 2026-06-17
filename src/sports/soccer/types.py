"""
Plain dataclass types shared across the soccer pipeline.

Kept dependency-free (stdlib only) so the math layers, the simulator and
the API can all import them without pulling numpy or pandas at import time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    country: Optional[str] = None
    elo: float = 1500.0
    attack: float = 0.0
    defense: float = 0.0


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    team_id: str
    position: str = "FW"
    minutes_avg: float = 70.0
    start_prob: float = 0.8
    goal_share: float = 0.0
    shot_share: float = 0.0
    yellow_rate_per90: float = 0.15
    red_rate_per90: float = 0.005


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    home_team_id: str
    away_team_id: str
    kickoff_unix: int
    competition: str = "FIFA WC"
    neutral_venue: bool = True
    referee_id: Optional[str] = None


@dataclass(frozen=True)
class Referee:
    referee_id: str
    name: str
    cards_per_game_avg: float = 4.2
    strictness: float = 1.0


@dataclass
class MatchSim:
    """One Monte Carlo sample of a match."""
    home_goals: int
    away_goals: int
    home_scorers: List[Tuple[str, int]] = field(default_factory=list)
    away_scorers: List[Tuple[str, int]] = field(default_factory=list)
    yellow_cards: List[Tuple[str, int]] = field(default_factory=list)
    red_cards: List[Tuple[str, int]] = field(default_factory=list)
    minutes_played: Dict[str, int] = field(default_factory=dict)
    shots_on_target: Dict[str, int] = field(default_factory=dict)

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    @property
    def btts(self) -> bool:
        return self.home_goals > 0 and self.away_goals > 0

    @property
    def result(self) -> str:
        if self.home_goals > self.away_goals:
            return "H"
        if self.home_goals < self.away_goals:
            return "A"
        return "D"

    @property
    def first_scorer(self) -> Optional[str]:
        events = sorted(
            [(p, m, "H") for p, m in self.home_scorers]
            + [(p, m, "A") for p, m in self.away_scorers],
            key=lambda x: x[1],
        )
        return events[0][0] if events else None

    @property
    def last_scorer(self) -> Optional[str]:
        events = sorted(
            [(p, m, "H") for p, m in self.home_scorers]
            + [(p, m, "A") for p, m in self.away_scorers],
            key=lambda x: x[1],
        )
        return events[-1][0] if events else None

    @property
    def all_scorers(self) -> List[str]:
        return [p for p, _ in self.home_scorers] + [p for p, _ in self.away_scorers]

    @property
    def total_cards(self) -> int:
        return len(self.yellow_cards) + len(self.red_cards)


# ----------------------------------------------------------------------
# Bet-builder leg taxonomy
# ----------------------------------------------------------------------

LegKind = str  # "match_result" | "total_goals" | "btts" | "team_total" |
#                "correct_score" | "anytime_scorer" | "first_scorer" |
#                "last_scorer" | "player_yellow" | "player_red" |
#                "total_cards" | "player_shots_ot"


@dataclass(frozen=True)
class BetLeg:
    """One leg of a parlay/bet builder.

    `kind` selects the predicate; `params` are kind-specific. Kept as a
    plain dict so the API can serialize legs without per-kind schemas.

    Examples:
        BetLeg("match_result",      {"side": "H"})
        BetLeg("total_goals",       {"line": 2.5, "side": "over"})
        BetLeg("btts",              {"side": "yes"})
        BetLeg("team_total",        {"team": "home", "line": 1.5, "side": "over"})
        BetLeg("correct_score",     {"home": 2, "away": 1})
        BetLeg("anytime_scorer",    {"player_id": "kane"})
        BetLeg("first_scorer",      {"player_id": "mbappe"})
        BetLeg("last_scorer",       {"player_id": "saka"})
        BetLeg("player_yellow",     {"player_id": "casemiro"})
        BetLeg("total_cards",       {"line": 4.5, "side": "over"})
    """
    kind: LegKind
    params: Dict[str, object] = field(default_factory=dict)
    book_decimal_odds: Optional[float] = None  # optional per-leg book quote
    label: Optional[str] = None


@dataclass
class BetBuilderQuote:
    fair_probability: float
    fair_decimal_odds: float
    n_sims: int
    leg_probabilities: List[float]
    independent_product: float
    correlation_factor: float  # joint / Π(legs); <1 means negatively corr.
    book_decimal_odds: Optional[float] = None
    edge: Optional[float] = None  # decimal: book*p - 1
    kelly_fraction: Optional[float] = None
    recommendation: str = "no_bet"
    notes: Optional[str] = None
    safety_score: float = 0.0
    risk_level: str = "unknown"
    risk_flags: List[str] = field(default_factory=list)
    ai_review: Optional[str] = None
