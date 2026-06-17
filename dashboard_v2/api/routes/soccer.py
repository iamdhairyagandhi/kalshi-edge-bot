"""
Soccer/World Cup bet-builder API.

Routes:
  GET  /api/soccer/fixtures
  GET  /api/soccer/match/{fixture_id}
  POST /api/soccer/bet-builder
  GET  /api/soccer/odds/{fixture_id}
  GET  /api/soccer/calibration
  POST /api/soccer/seed-demo

The route module owns a singleton `SoccerEngine` that lazily builds the
model stack on first use. Engine state is stored in `app.state` so other
routes (or tests) can swap it out.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np
import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from src.config import settings
from src.sports.soccer.calibration.isotonic import (
    CalibrationLayer,
    PredictionRecord,
    brier_score,
    reliability_buckets,
)
from src.sports.soccer.data.odds_api import OddsApiClient, OddsApiError
from src.sports.soccer.data.statsbomb import StatsBombOpenData
from src.sports.soccer.data.store import SoccerStore
from src.sports.soccer.models.cards import CardsModel
from src.sports.soccer.models.dixon_coles import DixonColesModel
from src.sports.soccer.models.minutes import MinutesModel
from src.sports.soccer.models.player_share import PlayerShareModel
from src.sports.soccer.pricing.edge import report_edge
from src.sports.soccer.ratings.elo import EloTable
from src.sports.soccer.simulator.bet_builder import price_bet_builder
from src.sports.soccer.simulator.match_sim import (
    MatchSimulator,
    SimulationConfig,
)
from src.sports.soccer.types import BetLeg, Player, Team


_LOG = logging.getLogger("dashboard_v2.soccer")


router = APIRouter()


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------

class TeamOut(BaseModel):
    team_id: str
    name: str
    country: Optional[str] = None
    elo: float
    attack: float = 0.0
    defense: float = 0.0


class FixtureOut(BaseModel):
    fixture_id: str
    home_team_id: str
    home_team_name: Optional[str] = None
    away_team_id: str
    away_team_name: Optional[str] = None
    kickoff_unix: int
    competition: Optional[str] = None
    neutral_venue: bool = True
    referee_id: Optional[str] = None
    home_win: Optional[float] = None
    draw: Optional[float] = None
    away_win: Optional[float] = None
    over_2_5: Optional[float] = None
    btts_yes: Optional[float] = None


class TopScorerProb(BaseModel):
    player_id: str
    name: str
    anytime: float


class MatchSummaryOut(BaseModel):
    fixture: FixtureOut
    score_matrix: List[List[float]]
    outcome: Dict[str, float]
    top_scorer_probs: List[TopScorerProb]
    cards_distribution: Dict[str, float]


class BetLegIn(BaseModel):
    kind: str
    params: Dict[str, object] = Field(default_factory=dict)
    book_decimal_odds: Optional[float] = None
    label: Optional[str] = None


class BetBuilderIn(BaseModel):
    fixture_id: str
    legs: List[BetLegIn]
    book_decimal_odds: Optional[float] = None
    n_sims: int = Field(default=10000, ge=500, le=50000)
    persist: bool = False


class BetBuilderOut(BaseModel):
    fair_probability: float
    fair_decimal_odds: float
    n_sims: int
    leg_probabilities: List[float]
    independent_product: float
    correlation_factor: float
    book_decimal_odds: Optional[float] = None
    edge: Optional[float] = None
    kelly_fraction: Optional[float] = None
    recommendation: str
    notes: Optional[str] = None
    safety_score: float = 0.0
    risk_level: str = "unknown"
    risk_flags: List[str] = Field(default_factory=list)
    ai_review: Optional[str] = None


class CalibrationOut(BaseModel):
    market_type: str
    n_resolved: int
    brier: float
    reliability: List[Dict[str, float]]


class RecordPredictionIn(BaseModel):
    """Body for POST /api/soccer/predictions — let the user log a bet
    they actually took so we can grade it later for calibration + CLV."""
    fixture_id: str
    market_type: str  # e.g. "home_win" / "over_2_5" / "btts_yes"
    leg: Dict[str, object]  # canonical BetLeg dict
    fair_probability: float
    fair_decimal_odds: float
    book_decimal_odds: Optional[float] = None
    pinnacle_close_decimal: Optional[float] = None
    edge: Optional[float] = None
    kelly_fraction: Optional[float] = None
    recommendation: str = "manual_record"


class ResolveFixtureIn(BaseModel):
    home_goals: int
    away_goals: int


class ResolveResultOut(BaseModel):
    fixture_id: str
    home_goals: int
    away_goals: int
    predictions_graded: int
    bet_builder_graded: int
    skipped_unsupported_market: int
    calibration_records: int


class OddsLegOut(BaseModel):
    market_key: str
    selection: str
    best_decimal: Optional[float] = None
    pinnacle_decimal: Optional[float] = None
    book_count: int = 0


class OddsForFixtureOut(BaseModel):
    fixture_id: str
    legs: List[OddsLegOut]
    fetched_at_unix: int


class SoccerBookPriceOut(BaseModel):
    book: str
    decimal: float


class SoccerMarketEdgeOut(BaseModel):
    market_group: str
    market_key: str
    selection: str
    label: str
    model_probability: float
    fair_decimal_odds: float
    best_decimal: Optional[float] = None
    best_book: Optional[str] = None
    implied_probability: Optional[float] = None
    edge: Optional[float] = None
    kelly_fraction: float = 0.0
    recommendation: str
    book_count: int = 0
    prices: List[SoccerBookPriceOut] = Field(default_factory=list)
    no_vig_market_prob: Optional[float] = None
    edge_vs_market: Optional[float] = None


class SoccerParlayBlueprintOut(BaseModel):
    label: str
    legs: List[BetLegIn]
    fair_probability: float
    fair_decimal_odds: float
    correlation_factor: float
    safety_score: float
    risk_level: str
    risk_flags: List[str] = Field(default_factory=list)


class SoccerEdgeBoardOut(BaseModel):
    fixture_id: str
    generated_at_unix: int
    model_ready: bool
    odds_available: bool
    expected_home_goals: Optional[float] = None
    expected_away_goals: Optional[float] = None
    market_edges: List[SoccerMarketEdgeOut] = Field(default_factory=list)
    parlay_blueprints: List[SoccerParlayBlueprintOut] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class SoccerBetslipLegOut(BaseModel):
    kind: str
    label: str
    params: Dict[str, object] = Field(default_factory=dict)
    model_probability: Optional[float] = None
    fair_decimal_odds: Optional[float] = None
    best_decimal: Optional[float] = None
    best_book: Optional[str] = None
    edge: Optional[float] = None


class SoccerBetslipOut(BaseModel):
    slip_id: str
    fixture_id: str
    match_label: str
    kickoff_unix: int
    slip_type: str
    title: str
    legs: List[SoccerBetslipLegOut]
    fair_probability: float
    fair_decimal_odds: float
    book_decimal_odds: Optional[float] = None
    minimum_acceptable_decimal: float
    edge: Optional[float] = None
    kelly_fraction: float = 0.0
    stake_usd: float = 0.0
    safety_score: float = 0.0
    risk_level: str = "unknown"
    risk_flags: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    # scout_only=True means we have a model price but NO confirmed book
    # quote (e.g., corners/cards markets the Odds API free tier omits).
    # The UI should render these without a stake button and label them
    # "SCOUT" so the user knows to manually confirm a price before betting.
    scout_only: bool = False


class SoccerBetslipBatchOut(BaseModel):
    generated_at_unix: int
    bankroll: float
    max_slips: int
    total_suggested_stake_usd: float
    slips: List[SoccerBetslipOut]
    notes: List[str] = Field(default_factory=list)


class StatsBombCompetitionIn(BaseModel):
    competition_id: int
    season_id: int
    neutral: bool = True


class FitFromStatsBombIn(BaseModel):
    competitions: Optional[List[StatsBombCompetitionIn]] = None
    decay_per_day: Optional[float] = None


class OddsFixtureIngestOut(BaseModel):
    ok: bool
    sport_key: str
    events_seen: int
    fixtures_added: int
    unmatched: int
    odds_api_key_configured: bool


# Default ingest set: WC 2018, WC 2022, Euro 2020, Euro 2024.
_DEFAULT_STATSBOMB_COMPS: List[Dict[str, object]] = [
    {"competition_id": 43, "season_id": 3,   "neutral": True},   # WC 2018
    {"competition_id": 43, "season_id": 106, "neutral": True},   # WC 2022
    {"competition_id": 55, "season_id": 43,  "neutral": True},   # Euro 2020
    {"competition_id": 55, "season_id": 282, "neutral": True},   # Euro 2024
]


# ----------------------------------------------------------------------
# Engine — owns the model stack
# ----------------------------------------------------------------------

class SoccerEngine:
    """Holds the fitted model stack + simulator + store."""

    def __init__(self) -> None:
        self.store = SoccerStore(settings.soccer_db_path)
        self.elo: EloTable = EloTable()
        self.score_model: Optional[DixonColesModel] = None
        self.player_share = PlayerShareModel()
        self.minutes = MinutesModel()
        self.cards = CardsModel()
        self.squads: Dict[str, List[str]] = {}
        self.team_names: Dict[str, str] = {}
        self.player_names: Dict[str, str] = {}
        self.fitted_at_unix: Optional[int] = None
        self.fit_source: str = "uninitialized"
        # Per-market isotonic calibration. Identity by default; fit from
        # resolved predictions whenever the model is (re)fit, and again
        # after the resolution loop writes new outcomes.
        self.calibration = CalibrationLayer()
        self.calibration_fitted_at_unix: Optional[int] = None

    # --------------------------------------------------------------
    def is_ready(self) -> bool:
        return self.score_model is not None and len(self.squads) > 0

    # --------------------------------------------------------------
    def ensure_ready_from_store(self) -> bool:
        if self.is_ready():
            return True
        teams = self.store.teams_all()
        matches = self.store.historical_matches()
        if not teams or not matches:
            return False
        team_lookup = {
            str(t["id"]): {
                "name": str(t.get("name") or t["id"]),
                "country": str(t.get("country") or ""),
            }
            for t in teams
        }
        self._fit_models(matches, team_lookup, decay_per_day=settings.soccer_decay_per_day)
        self.fitted_at_unix = int(time.time())
        self.fit_source = "store"
        self.refit_calibration()
        return True

    # --------------------------------------------------------------
    def _fit_models(
        self,
        matches: List[Dict[str, object]],
        team_lookup: Dict[str, Dict[str, str]],
        *,
        decay_per_day: float,
    ) -> None:
        self.team_names = {tid: meta["name"] for tid, meta in team_lookup.items()}
        self.elo = EloTable()
        self.elo.fit(matches)
        self.score_model = DixonColesModel.fit(matches, decay_per_day=decay_per_day)

        squads_seed: Dict[str, List[Dict[str, str]]] = {}
        for tid, meta in team_lookup.items():
            name = meta["name"]
            squads_seed[tid] = [
                {"player_id": f"{tid}-fw1", "position": "FW", "name": f"{name} FW1"},
                {"player_id": f"{tid}-fw2", "position": "FW", "name": f"{name} FW2"},
                {"player_id": f"{tid}-mf1", "position": "MF", "name": f"{name} MF1"},
                {"player_id": f"{tid}-mf2", "position": "MF", "name": f"{name} MF2"},
                {"player_id": f"{tid}-df1", "position": "DF", "name": f"{name} DF1"},
                {"player_id": f"{tid}-gk",  "position": "GK", "name": f"{name} GK"},
            ]
        self.squads = {tid: [p["player_id"] for p in pls] for tid, pls in squads_seed.items()}
        self.player_names = {}
        for pls in squads_seed.values():
            for p in pls:
                self.player_names[p["player_id"]] = p["name"]
        self.player_share = PlayerShareModel.from_position_priors(squads_seed)
        all_pids = [p["player_id"] for pls in squads_seed.values() for p in pls]
        self.minutes = MinutesModel.from_starting_priors(
            {pid: {"start_prob": 0.85, "minutes_avg": 78.0} for pid in all_pids}
        )
        self.cards = CardsModel.from_priors(
            {pid: {"yellow_per90": 0.20, "red_per90": 0.005} for pid in all_pids},
        )

    # --------------------------------------------------------------
    def refit_calibration(self) -> int:
        """Refit the per-market isotonic calibration from resolved
        predictions in the store. Safe to call with zero records — the
        layer simply stays identity until each market accumulates ≥30
        resolved samples. Returns the count of resolved records read."""
        try:
            rows = self.store.predictions_resolved()
        except Exception:
            return 0
        records: List[PredictionRecord] = []
        for r in rows:
            try:
                records.append(PredictionRecord(
                    market_type=str(r["market_type"]),
                    fair_probability=float(r["fair_probability"]),
                    book_decimal_odds=r.get("book_decimal_odds"),
                    pinnacle_close_decimal=r.get("pinnacle_close_decimal"),
                    outcome=int(r["outcome"]) if r.get("outcome") is not None else None,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        self.calibration = CalibrationLayer()
        if records:
            try:
                self.calibration.fit(records)
            except Exception as exc:  # pragma: no cover
                _LOG.warning("calibration fit failed: %s", exc)
        self.calibration_fitted_at_unix = int(time.time())
        return len(records)

    # --------------------------------------------------------------
    def calibrate_market(self, market_type: str, p: float) -> float:
        """Apply the per-market isotonic calibration to a single
        probability. Returns p unchanged if the market hasn't been
        fitted yet (insufficient resolved samples)."""
        try:
            q = self.calibration.calibrate(market_type, float(p))
        except Exception:
            return float(p)
        if q != q or q <= 0.0 or q >= 1.0:  # NaN guard + clip
            return float(min(max(p, 1e-6), 1.0 - 1e-6))
        return float(q)

    # --------------------------------------------------------------
    # Canonical market_type strings — must match what the resolution
    # loop writes to predictions.market_type so the isotonic mapping
    # actually applies.
    _CALIBRATE_KEYS = (
        "home_win", "draw", "away_win",
        "over_2_5", "under_2_5",
        "over_1_5", "under_1_5",
        "over_3_5", "under_3_5",
        "btts_yes", "btts_no",
    )

    def calibrate_probs(self, probs: Dict[str, float]) -> Dict[str, float]:
        """Calibrate every recognised key in a probs dict and renormalise
        complementary 2-/3-way buckets so they still sum to ~1. Unknown
        keys are passed through untouched."""
        if not probs:
            return probs
        out: Dict[str, float] = dict(probs)
        for k in self._CALIBRATE_KEYS:
            if k in out and out[k] is not None:
                out[k] = self.calibrate_market(k, float(out[k]))
        # Renormalise the canonical complementary buckets.
        for bucket in (
            ("home_win", "draw", "away_win"),
            ("over_2_5", "under_2_5"),
            ("over_1_5", "under_1_5"),
            ("over_3_5", "under_3_5"),
            ("btts_yes", "btts_no"),
        ):
            present = [k for k in bucket if k in out and out[k] is not None]
            if len(present) < 2:
                continue
            s = sum(float(out[k]) for k in present)
            if s <= 0:
                continue
            for k in present:
                out[k] = float(out[k]) / s
        return out

    # --------------------------------------------------------------
    def seed_demo(self) -> Dict[str, object]:
        """Bootstrap with synthetic priors so the dashboard works without
        external data. Does not overwrite an already-fitted real model
        unless explicitly called."""
        rng = np.random.default_rng(7)
        teams = {
            "FRA": ("France", 2050.0),
            "ENG": ("England", 1990.0),
            "BRA": ("Brazil", 2100.0),
            "ARG": ("Argentina", 2080.0),
            "GER": ("Germany", 1900.0),
            "ESP": ("Spain", 1970.0),
            "PRT": ("Portugal", 1900.0),
            "NED": ("Netherlands", 1880.0),
            "USA": ("United States", 1810.0),
            "MEX": ("Mexico", 1820.0),
        }
        self.team_names = {k: v[0] for k, v in teams.items()}
        self.elo = EloTable({k: v[1] for k, v in teams.items()})
        # synthesize a year of qualifiers/friendlies
        team_ids = list(teams)
        matches = []
        now = int(time.time())
        for i in range(220):
            h, a = rng.choice(team_ids, size=2, replace=False)
            gap = (self.elo.get(h).rating - self.elo.get(a).rating) / 400.0
            lh = max(0.3, np.exp(0.4 * gap + 0.05))
            la = max(0.3, np.exp(-0.4 * gap))
            matches.append({
                "home_team_id": h,
                "away_team_id": a,
                "home_goals": int(rng.poisson(lh)),
                "away_goals": int(rng.poisson(la)),
                "kickoff_unix": now - int(rng.integers(0, 365 * 86400)),
                "neutral_venue": False,
                "competition": "QUALIFIER",
            })
        self.elo.fit(matches)
        self.score_model = DixonColesModel.fit(matches, decay_per_day=settings.soccer_decay_per_day)

        # squads
        squads_seed: Dict[str, List[Dict[str, str]]] = {
            "FRA": [
                {"player_id": "fra-mbappe", "position": "FW", "name": "Mbappé"},
                {"player_id": "fra-griezmann", "position": "FW", "name": "Griezmann"},
                {"player_id": "fra-tchouameni", "position": "MF", "name": "Tchouaméni"},
                {"player_id": "fra-rabiot", "position": "MF", "name": "Rabiot"},
                {"player_id": "fra-kounde", "position": "DF", "name": "Koundé"},
                {"player_id": "fra-upamecano", "position": "DF", "name": "Upamecano"},
                {"player_id": "fra-maignan", "position": "GK", "name": "Maignan"},
            ],
            "ENG": [
                {"player_id": "eng-kane", "position": "FW", "name": "Kane"},
                {"player_id": "eng-saka", "position": "FW", "name": "Saka"},
                {"player_id": "eng-bellingham", "position": "MF", "name": "Bellingham"},
                {"player_id": "eng-rice", "position": "MF", "name": "Rice"},
                {"player_id": "eng-stones", "position": "DF", "name": "Stones"},
                {"player_id": "eng-walker", "position": "DF", "name": "Walker"},
                {"player_id": "eng-pickford", "position": "GK", "name": "Pickford"},
            ],
            "BRA": [
                {"player_id": "bra-vinicius", "position": "FW", "name": "Vinicius Jr."},
                {"player_id": "bra-rodrygo", "position": "FW", "name": "Rodrygo"},
                {"player_id": "bra-neymar", "position": "FW", "name": "Neymar"},
                {"player_id": "bra-casemiro", "position": "MF", "name": "Casemiro"},
                {"player_id": "bra-marquinhos", "position": "DF", "name": "Marquinhos"},
                {"player_id": "bra-alisson", "position": "GK", "name": "Alisson"},
            ],
            "ARG": [
                {"player_id": "arg-messi", "position": "FW", "name": "Messi"},
                {"player_id": "arg-alvarez", "position": "FW", "name": "Álvarez"},
                {"player_id": "arg-mac-allister", "position": "MF", "name": "Mac Allister"},
                {"player_id": "arg-de-paul", "position": "MF", "name": "De Paul"},
                {"player_id": "arg-otamendi", "position": "DF", "name": "Otamendi"},
                {"player_id": "arg-martinez", "position": "GK", "name": "E. Martínez"},
            ],
        }
        # fill in basic squads for the rest
        for tid in team_ids:
            if tid in squads_seed:
                continue
            squads_seed[tid] = [
                {"player_id": f"{tid.lower()}-fw1", "position": "FW", "name": f"{tid} FW1"},
                {"player_id": f"{tid.lower()}-fw2", "position": "FW", "name": f"{tid} FW2"},
                {"player_id": f"{tid.lower()}-mf1", "position": "MF", "name": f"{tid} MF1"},
                {"player_id": f"{tid.lower()}-mf2", "position": "MF", "name": f"{tid} MF2"},
                {"player_id": f"{tid.lower()}-df1", "position": "DF", "name": f"{tid} DF1"},
                {"player_id": f"{tid.lower()}-df2", "position": "DF", "name": f"{tid} DF2"},
                {"player_id": f"{tid.lower()}-gk", "position": "GK", "name": f"{tid} GK"},
            ]
        self.squads = {tid: [p["player_id"] for p in pls] for tid, pls in squads_seed.items()}
        for pls in squads_seed.values():
            for p in pls:
                self.player_names[p["player_id"]] = p["name"]

        self.player_share = PlayerShareModel.from_position_priors(squads_seed)
        all_pids = [pid for pls in squads_seed.values() for p in pls for pid in [p["player_id"]]]
        self.minutes = MinutesModel.from_starting_priors(
            {pid: {"start_prob": 0.85, "minutes_avg": 78.0} for pid in all_pids}
        )
        self.cards = CardsModel.from_priors(
            {pid: {"yellow_per90": 0.20, "red_per90": 0.005} for pid in all_pids},
        )

        # demo fixtures
        kickoff = int(time.time()) + 3 * 86400
        fixtures = [
            {"fixture_id": "demo-fra-eng", "home_team_id": "FRA", "away_team_id": "ENG",
             "kickoff_unix": kickoff, "competition": "FIFA WC", "neutral_venue": True},
            {"fixture_id": "demo-bra-arg", "home_team_id": "BRA", "away_team_id": "ARG",
             "kickoff_unix": kickoff + 3600, "competition": "FIFA WC", "neutral_venue": True},
            {"fixture_id": "demo-ger-esp", "home_team_id": "GER", "away_team_id": "ESP",
             "kickoff_unix": kickoff + 86400, "competition": "FIFA WC", "neutral_venue": True},
            {"fixture_id": "demo-prt-ned", "home_team_id": "PRT", "away_team_id": "NED",
             "kickoff_unix": kickoff + 86400 + 3600, "competition": "FIFA WC", "neutral_venue": True},
        ]
        self.store.upsert_fixtures(fixtures)
        self.fitted_at_unix = int(time.time())
        self.fit_source = "demo"
        self.refit_calibration()
        return {"teams": len(self.team_names), "fixtures": len(fixtures), "matches_used": len(matches)}

    # --------------------------------------------------------------
    def fit_from_statsbomb(
        self,
        competitions: List[Dict[str, int]],
        *,
        cache_dir: Optional[str] = None,
        decay_per_day: Optional[float] = None,
    ) -> Dict[str, object]:
        """Pull StatsBomb open-data for the given (competition_id, season_id)
        list and refit Elo + Dixon-Coles from real matches.

        Each entry: {"competition_id": int, "season_id": int, "neutral": bool}
        """
        cdir = cache_dir or settings.soccer_data_cache_dir
        decay = decay_per_day if decay_per_day is not None else settings.soccer_decay_per_day
        all_matches: List[Dict[str, object]] = []
        team_lookup: Dict[str, Dict[str, str]] = {}
        sources: List[str] = []
        with StatsBombOpenData(cache_dir=cdir) as sb:
            for entry in competitions:
                cid = int(entry["competition_id"])
                sid = int(entry["season_id"])
                neutral = bool(entry.get("neutral", True))
                rows = sb.matches_for_dixon_coles(cid, sid, neutral_default=neutral)
                if not rows:
                    continue
                sources.append(f"{cid}/{sid} ({rows[0].get('competition','?')} {rows[0].get('season','?')})")
                for r in rows:
                    all_matches.append(r)
                    h_id = str(r["home_team_id"])
                    a_id = str(r["away_team_id"])
                    team_lookup.setdefault(h_id, {
                        "name": str(r.get("home_team_name") or h_id),
                        "country": str(r.get("home_country") or ""),
                    })
                    team_lookup.setdefault(a_id, {
                        "name": str(r.get("away_team_name") or a_id),
                        "country": str(r.get("away_country") or ""),
                    })
        if not all_matches:
            raise RuntimeError("statsbomb returned no matches for the requested competitions")

        # Persist historical matches + teams
        self.store.upsert_historical_matches(all_matches)
        teams_now = []
        for tid, meta in team_lookup.items():
            teams_now.append(Team(
                team_id=tid,
                name=meta["name"],
                country=meta["country"] or None,
                elo=1500.0,
                attack=0.0,
                defense=0.0,
            ))
        self.store.upsert_teams(teams_now, now_unix=int(time.time()))

        self._fit_models(all_matches, team_lookup, decay_per_day=decay)

        self.fitted_at_unix = int(time.time())
        self.fit_source = "statsbomb"
        deleted_synthetic_fixtures = self.store.delete_synthetic_fixtures()
        self.refit_calibration()

        return {
            "competitions": sources,
            "matches_used": len(all_matches),
            "teams": len(team_lookup),
            "decay_per_day": decay,
            "deleted_synthetic_fixtures": deleted_synthetic_fixtures,
        }

    # --------------------------------------------------------------
    def ingest_odds_fixtures(self, *, sport_key: str) -> Dict[str, object]:
        if not settings.odds_api_key:
            return {
                "sport_key": sport_key,
                "events_seen": 0,
                "fixtures_added": 0,
                "unmatched": 0,
                "odds_api_key_configured": False,
            }
        team_ids_by_name = {_norm_name(name): tid for tid, name in self.team_names.items()}
        fixtures: List[Dict[str, object]] = []
        unmatched = 0
        with OddsApiClient(api_key=settings.odds_api_key, region=settings.odds_api_region) as client:
            events = client.events(sport_key=sport_key)
        for ev in events:
            home_raw = str(ev.get("home_team") or "")
            away_raw = str(ev.get("away_team") or "")
            home_id = team_ids_by_name.get(_norm_name(home_raw))
            away_id = team_ids_by_name.get(_norm_name(away_raw))
            if not home_id or not away_id:
                unmatched += 1
                continue
            kickoff = _parse_iso_unix(str(ev.get("commence_time") or ""))
            if kickoff is None:
                unmatched += 1
                continue
            fixtures.append({
                "fixture_id": f"odds-{ev.get('id')}",
                "home_team_id": home_id,
                "away_team_id": away_id,
                "kickoff_unix": kickoff,
                "competition": sport_key,
                "neutral_venue": True,
            })
        if fixtures:
            self.store.upsert_fixtures(fixtures)
        return {
            "sport_key": sport_key,
            "events_seen": len(events),
            "fixtures_added": len(fixtures),
            "unmatched": unmatched,
            "odds_api_key_configured": True,
        }

    # --------------------------------------------------------------
    def simulator(self) -> MatchSimulator:
        if not self.is_ready():
            raise RuntimeError("SoccerEngine not initialized; call seed-demo or fit.")
        assert self.score_model is not None
        return MatchSimulator(
            score_model=self.score_model,
            player_share=self.player_share,
            minutes=self.minutes,
            cards=self.cards,
            squads=self.squads,
        )


def _engine(req: Request) -> SoccerEngine:
    eng = getattr(req.app.state, "soccer_engine", None)
    if eng is None:
        eng = SoccerEngine()
        req.app.state.soccer_engine = eng
    return eng


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@router.post("/soccer/seed-demo")
def seed_demo(request: Request) -> Dict[str, object]:
    eng = _engine(request)
    info = eng.seed_demo()
    return {"ok": True, "fit_source": eng.fit_source, **info}


@router.post("/soccer/fit-statsbomb")
def fit_statsbomb(
    request: Request,
    body: Optional[FitFromStatsBombIn] = Body(default=None),
) -> Dict[str, object]:
    """Pull StatsBomb open-data for the requested (competition, season)
    pairs (or the default WC18/WC22/EURO20/EURO24 set) and refit Elo +
    Dixon-Coles from real match data."""
    eng = _engine(request)
    comps: List[Dict[str, object]]
    if body is not None and body.competitions:
        comps = [c.model_dump() for c in body.competitions]
    else:
        comps = list(_DEFAULT_STATSBOMB_COMPS)
    try:
        info = eng.fit_from_statsbomb(
            comps,
            decay_per_day=(body.decay_per_day if body is not None else None),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"statsbomb_fetch_failed: {e}") from e
    return {"ok": True, "fit_source": eng.fit_source, **info}


@router.post("/soccer/ingest-odds-fixtures", response_model=OddsFixtureIngestOut)
def ingest_odds_fixtures(request: Request) -> Dict[str, object]:
    eng = _engine(request)
    if not eng.is_ready():
        raise HTTPException(status_code=409, detail="engine_not_initialized")
    try:
        info = eng.ingest_odds_fixtures(sport_key=settings.odds_api_sport_key)
    except OddsApiError as e:
        _LOG.warning("odds fixture ingest failed: %s", e)
        raise HTTPException(status_code=502, detail=f"odds_api_error: {e}") from e
    return {"ok": True, **info}


@router.get("/soccer/teams", response_model=List[TeamOut])
def teams(request: Request) -> List[TeamOut]:
    eng = _engine(request)
    eng.ensure_ready_from_store()
    out: List[TeamOut] = []
    if not eng.is_ready():
        return out
    for tid, name in sorted(eng.team_names.items()):
        out.append(TeamOut(
            team_id=tid,
            name=name,
            country=name,
            elo=eng.elo.get(tid).rating,
            attack=eng.score_model.params.attack.get(tid, 0.0) if eng.score_model else 0.0,
            defense=eng.score_model.params.defense.get(tid, 0.0) if eng.score_model else 0.0,
        ))
    return out


@router.get("/soccer/fixtures", response_model=List[FixtureOut])
def fixtures(request: Request, since_unix: Optional[int] = None, limit: int = 50) -> List[FixtureOut]:
    eng = _engine(request)
    eng.ensure_ready_from_store()
    since = since_unix if since_unix is not None else int(time.time()) - 3600
    rows = eng.store.fixtures_upcoming(since, limit=limit)
    out: List[FixtureOut] = []
    for r in rows:
        probs: Dict[str, float] = {}
        if eng.score_model is not None:
            try:
                probs = eng.score_model.outcome_probs(
                    r["home_team_id"], r["away_team_id"],
                    neutral=bool(r["neutral_venue"]),
                )
                probs = eng.calibrate_probs(probs)
            except KeyError:
                probs = {}
        out.append(FixtureOut(
            fixture_id=r["id"],
            home_team_id=r["home_team_id"],
            away_team_id=r["away_team_id"],
            home_team_name=eng.team_names.get(r["home_team_id"]),
            away_team_name=eng.team_names.get(r["away_team_id"]),
            kickoff_unix=int(r["kickoff_unix"]),
            competition=r.get("competition"),
            neutral_venue=bool(r["neutral_venue"]),
            referee_id=r.get("referee_id"),
            home_win=probs.get("home_win"),
            draw=probs.get("draw"),
            away_win=probs.get("away_win"),
            over_2_5=probs.get("over_2_5"),
            btts_yes=probs.get("btts_yes"),
        ))
    return out


@router.get("/soccer/match/{fixture_id}", response_model=MatchSummaryOut)
def match_summary(request: Request, fixture_id: str, n_sims: int = 5000) -> MatchSummaryOut:
    eng = _engine(request)
    eng.ensure_ready_from_store()
    if not eng.is_ready():
        raise HTTPException(status_code=409, detail="engine_not_initialized")
    rows = eng.store.fixtures_upcoming(0, limit=1000)
    fixture = next((r for r in rows if r["id"] == fixture_id), None)
    if fixture is None:
        raise HTTPException(status_code=404, detail="fixture_not_found")
    home_id = fixture["home_team_id"]
    away_id = fixture["away_team_id"]
    neutral = bool(fixture["neutral_venue"])
    assert eng.score_model is not None
    sm = eng.score_model.score_matrix(home_id, away_id, neutral=neutral, max_goals=8)
    outcome = eng.score_model.outcome_probs(home_id, away_id, neutral=neutral, max_goals=8)
    outcome = eng.calibrate_probs(outcome)
    sim = eng.simulator()
    sims = sim.simulate(home_id, away_id, neutral_venue=neutral,
                        config=SimulationConfig(n_sims=int(n_sims), seed=hash(fixture_id) & 0xFFFFFFFF))
    # top scorers
    scorer_counts: Dict[str, int] = {}
    for s in sims:
        for p, _m in s.home_scorers + s.away_scorers:
            scorer_counts[p] = scorer_counts.get(p, 0) + 1
    n = len(sims)
    top = sorted(scorer_counts.items(), key=lambda kv: -kv[1])[:12]
    top_scorer_probs = [
        TopScorerProb(player_id=pid, name=eng.player_names.get(pid, pid), anytime=c / n)
        for pid, c in top
    ]
    # cards
    cards_dist: Dict[str, float] = {}
    for thr in (2.5, 3.5, 4.5, 5.5, 6.5):
        cards_dist[f"over_{thr}"] = sum(1 for s in sims if s.total_cards > thr) / n
    return MatchSummaryOut(
        fixture=FixtureOut(
            fixture_id=fixture_id,
            home_team_id=home_id,
            home_team_name=eng.team_names.get(home_id),
            away_team_id=away_id,
            away_team_name=eng.team_names.get(away_id),
            kickoff_unix=int(fixture["kickoff_unix"]),
            competition=fixture.get("competition"),
            neutral_venue=neutral,
            referee_id=fixture.get("referee_id"),
            home_win=outcome.get("home_win"),
            draw=outcome.get("draw"),
            away_win=outcome.get("away_win"),
            over_2_5=outcome.get("over_2_5"),
            btts_yes=outcome.get("btts_yes"),
        ),
        score_matrix=sm.tolist(),
        outcome=outcome,
        top_scorer_probs=top_scorer_probs,
        cards_distribution=cards_dist,
    )


@router.get("/soccer/edge-board/{fixture_id}", response_model=SoccerEdgeBoardOut)
def edge_board(request: Request, fixture_id: str, n_sims: int = 8000) -> SoccerEdgeBoardOut:
    eng = _engine(request)
    eng.ensure_ready_from_store()
    if not eng.is_ready():
        raise HTTPException(status_code=409, detail="engine_not_initialized")
    rows = eng.store.fixtures_upcoming(0, limit=1000)
    fixture = next((r for r in rows if r["id"] == fixture_id), None)
    if fixture is None:
        raise HTTPException(status_code=404, detail="fixture_not_found")

    home_id = fixture["home_team_id"]
    away_id = fixture["away_team_id"]
    neutral = bool(fixture["neutral_venue"])
    home = eng.team_names.get(home_id, home_id)
    away = eng.team_names.get(away_id, away_id)
    assert eng.score_model is not None
    probs = eng.score_model.outcome_probs(home_id, away_id, neutral=neutral, max_goals=10)
    probs = eng.calibrate_probs(probs)
    lam_h, lam_a = eng.score_model.params.lambdas(home_id, away_id, neutral=neutral)

    event: Optional[Dict[str, object]] = None
    notes: List[str] = []
    if settings.odds_api_key:
        try:
            with OddsApiClient(api_key=settings.odds_api_key, region=settings.odds_api_region) as client:
                events = client.odds(sport_key=settings.odds_api_sport_key, markets="h2h,totals")
            event = _find_odds_event(events, fixture, home, away)
            if event is None:
                notes.append("No matching sportsbook event found for this fixture.")
        except OddsApiError as e:
            notes.append(f"Odds API unavailable: {e}")
    else:
        notes.append("ODDS_API_KEY is not configured.")

    specs = [
        ("1X2", "h2h", home, f"{home} win", probs["home_win"], None),
        ("1X2", "h2h", "Draw", "Draw", probs["draw"], None),
        ("1X2", "h2h", away, f"{away} win", probs["away_win"], None),
        ("Totals", "totals", "Over", "Over 2.5 goals", probs["over_2_5"], 2.5),
        ("Totals", "totals", "Under", "Under 2.5 goals", probs["under_2_5"], 2.5),
        ("BTTS", "btts", "Yes", "BTTS yes", probs["btts_yes"], None),
        ("BTTS", "btts", "No", "BTTS no", probs["btts_no"], None),
    ]
    # Pre-build de-vig vectors per market so every selection on the same
    # market shares one consensus probability.
    h2h_vec = _devig_vector(event, "h2h", [(home, None), ("Draw", None), (away, None)]) if event else None
    totals_vec = _devig_vector(event, "totals", [("Over", 2.5), ("Under", 2.5)]) if event else None
    btts_vec = _devig_vector(event, "btts", [("Yes", None), ("No", None)]) if event else None
    market_vec_for = {
        "h2h": h2h_vec,
        "totals": totals_vec,
        "btts": btts_vec,
    }
    edges: List[SoccerMarketEdgeOut] = []
    for group, market_key, selection, label, p_model, point in specs:
        prices = _book_prices(event, market_key, selection, point=point) if event else []
        best = max(prices, key=lambda x: x[1]) if prices else None
        best_decimal = best[1] if best else None
        rep = report_edge(
            float(p_model),
            best_decimal,
            min_edge=settings.soccer_min_edge,
            kelly_fraction=settings.soccer_kelly_fraction,
            kelly_cap=settings.soccer_kelly_cap,
            market_decimal_odds=market_vec_for.get(market_key),
        )
        edges.append(SoccerMarketEdgeOut(
            market_group=group,
            market_key=market_key,
            selection=selection if point is None else f"{selection} {point:g}",
            label=label,
            model_probability=float(p_model),
            fair_decimal_odds=rep.fair_decimal_odds,
            best_decimal=best_decimal,
            best_book=best[0] if best else None,
            implied_probability=(1.0 / best_decimal if best_decimal else None),
            edge=rep.edge,
            kelly_fraction=rep.kelly_fraction,
            recommendation=rep.recommendation,
            book_count=len(prices),
            prices=[SoccerBookPriceOut(book=b, decimal=d) for b, d in sorted(prices, key=lambda x: -x[1])[:6]],
            no_vig_market_prob=rep.no_vig_market_prob,
            edge_vs_market=rep.edge_vs_market,
        ))

    sims = eng.simulator().simulate(
        home_id,
        away_id,
        neutral_venue=neutral,
        config=SimulationConfig(n_sims=int(n_sims), seed=hash((fixture_id, "edge-board")) & 0xFFFFFFFF),
    )
    blueprints = _parlay_blueprints(probs=probs, sims=sims, home=home, away=away)
    return SoccerEdgeBoardOut(
        fixture_id=fixture_id,
        generated_at_unix=int(time.time()),
        model_ready=True,
        odds_available=event is not None,
        expected_home_goals=float(lam_h),
        expected_away_goals=float(lam_a),
        market_edges=edges,
        parlay_blueprints=blueprints,
        notes=notes,
    )


@router.get("/soccer/betslips", response_model=SoccerBetslipBatchOut)
def betslips(
    request: Request,
    max_slips: int = 12,
    bankroll: Optional[float] = None,
    min_edge: Optional[float] = None,
    n_sims: int = 2500,
    horizon_hours: Optional[int] = None,
) -> SoccerBetslipBatchOut:
    eng = _engine(request)
    eng.ensure_ready_from_store()
    if not eng.is_ready():
        raise HTTPException(status_code=409, detail="engine_not_initialized")
    assert eng.score_model is not None

    bankroll_value = float(bankroll if bankroll is not None else settings.starting_bankroll)
    edge_floor = float(min_edge if min_edge is not None else settings.soccer_min_edge)
    max_slips = max(1, min(int(max_slips), 40))
    now_unix = int(time.time())
    rows = eng.store.fixtures_upcoming(now_unix - 3600, limit=80)
    horizon_label: Optional[str] = None
    if horizon_hours is not None and horizon_hours > 0:
        horizon = max(1, min(int(horizon_hours), 24 * 30))
        cutoff = now_unix + horizon * 3600
        before = len(rows)
        rows = [r for r in rows if int(r["kickoff_unix"]) <= cutoff]
        horizon_label = (
            f"horizon={horizon}h kept {len(rows)}/{before} fixture(s)"
        )
    events: List[Dict[str, object]] = []
    notes: List[str] = []
    if horizon_label:
        notes.append(horizon_label)
    if settings.odds_api_key:
        try:
            with OddsApiClient(api_key=settings.odds_api_key, region=settings.odds_api_region) as client:
                events = client.odds(sport_key=settings.odds_api_sport_key, markets="h2h,totals")
        except OddsApiError as e:
            notes.append(f"Odds API unavailable: {e}")
    else:
        notes.append("ODDS_API_KEY is not configured; only model-price parlays can be generated.")

    slips: List[SoccerBetslipOut] = []
    for fixture in rows:
        try:
            slips.extend(_betslips_for_fixture(
                eng=eng,
                fixture=fixture,
                events=events,
                bankroll=bankroll_value,
                min_edge=edge_floor,
                n_sims=int(n_sims),
            ))
        except Exception as e:
            _LOG.warning("betslip generation skipped fixture %s: %s", fixture.get("id"), e)
            continue

    slips.extend(_cross_fixture_parlays(slips, bankroll=bankroll_value, min_edge=edge_floor))
    ranked = _ranked_mixed_slips(slips, max_slips=max_slips)
    final: List[SoccerBetslipOut] = []
    per_fixture: Dict[str, int] = {}
    total_stake = 0.0
    max_total_stake = bankroll_value * 0.08
    for slip in ranked:
        if len(final) >= max_slips:
            break
        if slip.slip_type != "cross_fixture_parlay" and per_fixture.get(slip.fixture_id, 0) >= 5:
            continue
        if slip.stake_usd > 0 and total_stake + slip.stake_usd > max_total_stake:
            remaining = max(0.0, max_total_stake - total_stake)
            if remaining <= 0:
                continue
            slip.stake_usd = remaining
        final.append(slip)
        if slip.slip_type != "cross_fixture_parlay":
            per_fixture[slip.fixture_id] = per_fixture.get(slip.fixture_id, 0) + 1
        total_stake += slip.stake_usd

    if not final:
        notes.append("No slips cleared the current edge/risk filters.")
    return SoccerBetslipBatchOut(
        generated_at_unix=int(time.time()),
        bankroll=bankroll_value,
        max_slips=max_slips,
        total_suggested_stake_usd=round(total_stake, 2),
        slips=final,
        notes=notes,
    )


@router.post("/soccer/bet-builder", response_model=BetBuilderOut)
def bet_builder(request: Request, payload: BetBuilderIn = Body(...)) -> BetBuilderOut:
    eng = _engine(request)
    eng.ensure_ready_from_store()
    if not eng.is_ready():
        raise HTTPException(status_code=409, detail="engine_not_initialized")
    rows = eng.store.fixtures_upcoming(0, limit=1000)
    fixture = next((r for r in rows if r["id"] == payload.fixture_id), None)
    if fixture is None:
        raise HTTPException(status_code=404, detail="fixture_not_found")
    sims = eng.simulator().simulate(
        fixture["home_team_id"], fixture["away_team_id"],
        neutral_venue=bool(fixture["neutral_venue"]),
        config=SimulationConfig(
            n_sims=int(payload.n_sims),
            seed=hash((payload.fixture_id, payload.n_sims)) & 0xFFFFFFFF,
        ),
    )
    legs = [BetLeg(kind=l.kind, params=dict(l.params), book_decimal_odds=l.book_decimal_odds, label=l.label)
            for l in payload.legs]
    quote = price_bet_builder(
        sims, legs,
        book_decimal_odds=payload.book_decimal_odds,
        min_edge=settings.soccer_min_edge,
        max_kelly=settings.soccer_kelly_cap,
    )
    if settings.soccer_ai_review_enabled and settings.openai_api_key:
        quote.ai_review = _soccer_ai_review(
            fixture=fixture,
            home_name=eng.team_names.get(fixture["home_team_id"], fixture["home_team_id"]),
            away_name=eng.team_names.get(fixture["away_team_id"], fixture["away_team_id"]),
            legs=legs,
            quote=quote,
        )
    if payload.persist:
        eng.store.record_bet_builder(
            fixture_id=payload.fixture_id,
            legs=[asdict(leg) for leg in legs],
            fair_probability=quote.fair_probability,
            book_decimal_odds=quote.book_decimal_odds,
            edge=quote.edge,
            kelly_fraction=quote.kelly_fraction,
            recommendation=quote.recommendation,
            recorded_unix=int(time.time()),
        )
    return BetBuilderOut(**asdict(quote))


@router.get("/soccer/odds/{fixture_id}", response_model=OddsForFixtureOut)
def odds(
    request: Request,
    fixture_id: str,
    legs: Optional[str] = None,
) -> OddsForFixtureOut:
    """
    Pull leg odds from The Odds API for a given fixture.

    `legs` is a comma-separated list of "<market_key>:<selection>" pairs, e.g.
        h2h:France,totals:Over 2.5,btts:Yes
    """
    eng = _engine(request)
    eng.ensure_ready_from_store()
    if not settings.odds_api_key:
        raise HTTPException(status_code=409, detail="odds_api_key_not_configured")
    rows = eng.store.fixtures_upcoming(0, limit=1000)
    fixture = next((r for r in rows if r["id"] == fixture_id), None)
    if fixture is None:
        raise HTTPException(status_code=404, detail="fixture_not_found")
    home = eng.team_names.get(fixture["home_team_id"], fixture["home_team_id"])
    away = eng.team_names.get(fixture["away_team_id"], fixture["away_team_id"])
    raw_legs = (legs or f"h2h:{home},h2h:Draw,h2h:{away},totals:Over 2.5,btts:Yes").split(",")
    parsed = []
    for spec in raw_legs:
        if ":" not in spec:
            continue
        mkt, sel = spec.split(":", 1)
        parsed.append((mkt.strip(), sel.strip()))
    out_legs: List[OddsLegOut] = []
    try:
        with OddsApiClient(api_key=settings.odds_api_key, region=settings.odds_api_region) as client:
            events = client.odds(
                sport_key=settings.odds_api_sport_key,
                markets=",".join(sorted({m for m, _ in parsed})) or "h2h,totals,btts",
            )
        # match by team names — name-based matching is fragile but works for the
        # WC where The Odds API uses the FIFA-style names.
        ev = next(
            (
                e for e in events
                if str(e.get("home_team", "")).lower() == home.lower()
                and str(e.get("away_team", "")).lower() == away.lower()
            ),
            None,
        )
        if ev is None:
            for m, s in parsed:
                out_legs.append(OddsLegOut(market_key=m, selection=s))
        else:
            for m, s in parsed:
                out_legs.append(OddsLegOut(
                    market_key=m,
                    selection=s,
                    best_decimal=OddsApiClient.best_decimal(ev, m, s),
                    pinnacle_decimal=OddsApiClient.pinnacle_decimal(ev, m, s),
                    book_count=len(ev.get("bookmakers", [])),
                ))
    except OddsApiError as e:
        _LOG.warning("odds api error: %s", e)
        raise HTTPException(status_code=502, detail=f"odds_api_error: {e}") from e
    return OddsForFixtureOut(fixture_id=fixture_id, legs=out_legs, fetched_at_unix=int(time.time()))


@router.get("/soccer/calibration", response_model=List[CalibrationOut])
def calibration(request: Request) -> List[CalibrationOut]:
    eng = _engine(request)
    rows = eng.store.predictions_resolved()
    by_market: Dict[str, List[PredictionRecord]] = {}
    for r in rows:
        rec = PredictionRecord(
            market_type=str(r["market_type"]),
            fair_probability=float(r["fair_probability"]),
            book_decimal_odds=r.get("book_decimal_odds"),
            pinnacle_close_decimal=r.get("pinnacle_close_decimal"),
            outcome=int(r["outcome"]) if r.get("outcome") is not None else None,
        )
        by_market.setdefault(rec.market_type, []).append(rec)
    out: List[CalibrationOut] = []
    for mkt, recs in sorted(by_market.items()):
        out.append(CalibrationOut(
            market_type=mkt,
            n_resolved=len(recs),
            brier=brier_score(recs),
            reliability=reliability_buckets(recs),
        ))
    return out


@router.post("/soccer/predictions")
def record_prediction(
    request: Request,
    payload: RecordPredictionIn,
) -> Dict[str, object]:
    """Persist a single pick the user actually took. Required input for
    the resolution loop and isotonic calibration — without this we have
    no resolved samples to fit on."""
    eng = _engine(request)
    pid = eng.store.record_prediction(
        fixture_id=payload.fixture_id,
        market_type=payload.market_type,
        leg=payload.leg,
        fair_probability=float(payload.fair_probability),
        fair_decimal_odds=float(payload.fair_decimal_odds),
        book_decimal_odds=payload.book_decimal_odds,
        pinnacle_close_decimal=payload.pinnacle_close_decimal,
        edge=payload.edge,
        kelly_fraction=payload.kelly_fraction,
        recommendation=payload.recommendation,
        recorded_unix=int(time.time()),
    )
    return {"ok": True, "prediction_id": pid}


@router.post("/soccer/fixtures/{fixture_id}/resolve", response_model=ResolveResultOut)
def resolve_fixture(
    request: Request,
    fixture_id: str,
    payload: ResolveFixtureIn,
) -> ResolveResultOut:
    """Mark a fixture as final with the given (home_goals, away_goals)
    and grade every recorded prediction + bet-builder quote against it.

    Markets we can score from the final score alone (1X2 / totals / BTTS
    / team total / correct score) get an outcome of 0 or 1. Player /
    cards / corners / shots markets need a stats feed we don't ingest
    yet and are skipped (outcome stays NULL).

    After grading we refit the calibration layer so the next probability
    surface uses the updated isotonic mapping."""
    eng = _engine(request)
    fixture = eng.store.fixture_get(fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="fixture_not_found")
    if payload.home_goals < 0 or payload.away_goals < 0:
        raise HTTPException(status_code=400, detail="goals_must_be_non_negative")

    hg = int(payload.home_goals)
    ag = int(payload.away_goals)

    # Persist the historical match so future Dixon-Coles / Elo refits
    # see it. (Idempotent via upsert.)
    eng.store.upsert_historical_matches([{
        "match_id": f"resolved-{fixture_id}",
        "home_team_id": fixture["home_team_id"],
        "away_team_id": fixture["away_team_id"],
        "home_goals": hg,
        "away_goals": ag,
        "kickoff_unix": int(fixture.get("kickoff_unix") or time.time()),
        "neutral_venue": bool(fixture.get("neutral_venue", False)),
        "competition": fixture.get("competition") or "",
    }])

    pred_graded = 0
    skipped = 0
    for row in eng.store.predictions_for_fixture(fixture_id):
        try:
            leg = json.loads(row.get("leg_json") or "{}")
        except (TypeError, ValueError):
            leg = {}
        outcome = _grade_market_type_from_score(
            str(row.get("market_type") or ""), leg, hg, ag,
        )
        if outcome is None:
            skipped += 1
            continue
        eng.store.set_prediction_outcome(int(row["id"]), int(outcome))
        pred_graded += 1

    bb_graded = 0
    for row in eng.store.bet_builder_for_fixture(fixture_id):
        try:
            legs = json.loads(row.get("legs_json") or "[]")
        except (TypeError, ValueError):
            legs = []
        leg_outcomes = [_resolve_leg(l, hg, ag) for l in legs]
        if not leg_outcomes or any(o is None for o in leg_outcomes):
            # If any leg can't be graded from the score alone, we can't
            # grade the parlay. Skip silently.
            continue
        eng.store.set_bet_builder_outcome(
            int(row["id"]),
            1 if all(o == 1 for o in leg_outcomes) else 0,
        )
        bb_graded += 1

    eng.store.fixture_set_status(fixture_id, "CLOSED")
    cal_n = eng.refit_calibration()

    return ResolveResultOut(
        fixture_id=fixture_id,
        home_goals=hg,
        away_goals=ag,
        predictions_graded=pred_graded,
        bet_builder_graded=bb_graded,
        skipped_unsupported_market=skipped,
        calibration_records=cal_n,
    )


@router.post("/soccer/fixtures/{fixture_id}/snapshot-closing-line")
def snapshot_closing_line(request: Request, fixture_id: str) -> Dict[str, object]:
    """Snapshot Pinnacle's current h2h / totals 2.5 / BTTS prices and
    write them onto every recorded prediction for this fixture so we
    can compute CLV after settlement. Call this within a couple of
    minutes of kickoff for the cleanest CLV signal."""
    eng = _engine(request)
    if not settings.odds_api_key:
        raise HTTPException(status_code=409, detail="odds_api_key_not_configured")
    fixture = eng.store.fixture_get(fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="fixture_not_found")
    home = eng.team_names.get(fixture["home_team_id"], fixture["home_team_id"])
    away = eng.team_names.get(fixture["away_team_id"], fixture["away_team_id"])
    try:
        with OddsApiClient(api_key=settings.odds_api_key, region=settings.odds_api_region) as client:
            events = client.odds(
                sport_key=settings.odds_api_sport_key, markets="h2h,totals,btts"
            )
    except OddsApiError as e:
        raise HTTPException(status_code=502, detail=f"odds_api_error: {e}") from e
    event = _find_odds_event(events, fixture, home, away)
    if event is None:
        return {"ok": False, "fixture_id": fixture_id, "updated": 0, "reason": "no_event_match"}

    def _pin(market_key: str, selection: str, point: Optional[float] = None) -> Optional[float]:
        return _best_price(event, market_key, selection, point=point, prefer_pinnacle=True)

    pin_map: Dict[str, Optional[float]] = {
        "home_win": _pin("h2h", home),
        "draw": _pin("h2h", "Draw"),
        "away_win": _pin("h2h", away),
        "over_2_5": _pin("totals", "Over", 2.5),
        "under_2_5": _pin("totals", "Under", 2.5),
        "btts_yes": _pin("btts", "Yes"),
        "btts_no": _pin("btts", "No"),
    }
    updated = 0
    for row in eng.store.predictions_for_fixture(fixture_id):
        mt = str(row.get("market_type") or "")
        d = pin_map.get(mt)
        if d is None:
            continue
        eng.store.set_prediction_closing_line(int(row["id"]), float(d))
        updated += 1
    return {
        "ok": True,
        "fixture_id": fixture_id,
        "updated": updated,
        "snapshot": {k: v for k, v in pin_map.items() if v is not None},
    }


def _norm_name(value: str) -> str:
    return " ".join(value.lower().replace(".", "").split())


def _parse_iso_unix(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _find_odds_event(
    events: List[Dict[str, object]],
    fixture: Dict[str, object],
    home_name: str,
    away_name: str,
) -> Optional[Dict[str, object]]:
    if str(fixture.get("id", "")).startswith("odds-"):
        event_id = str(fixture["id"])[5:]
        for e in events:
            if str(e.get("id")) == event_id:
                return e
    h = _norm_name(home_name)
    a = _norm_name(away_name)
    for e in events:
        eh = _norm_name(str(e.get("home_team") or ""))
        ea = _norm_name(str(e.get("away_team") or ""))
        if (eh == h and ea == a) or (eh == a and ea == h):
            return e
    return None


def _book_prices(
    event: Optional[Dict[str, object]],
    market_key: str,
    selection: str,
    *,
    point: Optional[float] = None,
) -> List[tuple[str, float]]:
    if not event:
        return []
    out: List[tuple[str, float]] = []
    for book in event.get("bookmakers", []) or []:
        book_key = str(book.get("key") or book.get("title") or "book")
        for market in book.get("markets", []) or []:
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []) or []:
                if not _outcome_matches(outcome, selection, point):
                    continue
                try:
                    price = float(outcome.get("price"))
                except (TypeError, ValueError):
                    continue
                if price > 1.0:
                    out.append((book_key, price))
    return out


def _best_price(
    event: Optional[Dict[str, object]],
    market_key: str,
    selection: str,
    *,
    point: Optional[float] = None,
    prefer_pinnacle: bool = False,
) -> Optional[float]:
    """Best decimal across books for one (market, selection). When
    `prefer_pinnacle=True` and Pinnacle has a price, return Pinnacle's
    price even if another book is higher — used to build the de-vig
    vector from a single sharp book."""
    prices = _book_prices(event, market_key, selection, point=point)
    if not prices:
        return None
    if prefer_pinnacle:
        for b, d in prices:
            if b.lower() == "pinnacle":
                return d
    return max(p for _, p in prices)


def _devig_vector(
    event: Optional[Dict[str, object]],
    market_key: str,
    selections: Sequence[tuple[str, Optional[float]]],
    *,
    prefer_pinnacle: bool = True,
) -> Optional[List[float]]:
    """Return decimal odds for every mutually-exclusive selection on a
    market (in the order given). All selections must resolve to a price
    for the de-vig calculation to be meaningful — if any is missing we
    return None and the caller falls back to raw-edge mode."""
    out: List[float] = []
    for sel, point in selections:
        d = _best_price(event, market_key, sel, point=point, prefer_pinnacle=prefer_pinnacle)
        if d is None or d <= 1.0:
            return None
        out.append(d)
    return out


def _outcome_matches(outcome: Dict[str, object], selection: str, point: Optional[float]) -> bool:
    raw = str(outcome.get("name") or "").strip().lower()
    wanted = selection.strip().lower()
    if raw != wanted:
        return False
    if point is None:
        return True
    try:
        return abs(float(outcome.get("point")) - float(point)) < 1e-9
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------
# Resolution loop helpers — used by POST /soccer/fixtures/{id}/resolve.
# We can only grade markets whose outcome is deterministic from the final
# (home_goals, away_goals) tuple. Player props, cards, corners, and any
# stat-level market need a separate stats feed and are skipped here.
# ----------------------------------------------------------------------

# Market types we know how to grade from just (hg, ag). Keep this list
# in sync with the canonical market_type strings used by record_prediction
# and SoccerEngine._CALIBRATE_KEYS.
_SCORE_DERIVABLE_MARKETS = {
    "match_result", "home_win", "draw", "away_win",
    "total_goals", "over_1_5", "under_1_5",
    "over_2_5", "under_2_5", "over_3_5", "under_3_5",
    "btts", "btts_yes", "btts_no",
    "team_total", "correct_score",
}


def _resolve_leg(leg: Dict[str, object], home_goals: int, away_goals: int) -> Optional[int]:
    """Grade a BetLeg dict from the final score. Returns 1 (hit), 0
    (miss), or None for markets that need stats we don't have."""
    kind = str(leg.get("kind") or "").lower()
    params = leg.get("params") or {}
    if not isinstance(params, dict):
        return None
    total = home_goals + away_goals

    if kind == "match_result":
        side = str(params.get("side", "")).upper()
        if home_goals > away_goals:
            return 1 if side in ("H", "HOME", "1") else 0
        if home_goals < away_goals:
            return 1 if side in ("A", "AWAY", "2") else 0
        return 1 if side in ("D", "DRAW", "X") else 0

    if kind == "total_goals":
        try:
            line = float(params.get("line"))
        except (TypeError, ValueError):
            return None
        side = str(params.get("side", "")).lower()
        # Exact-integer line is a push: books refund the stake. Treat as
        # void so the calibration layer doesn't learn a spurious miss.
        if abs(total - line) < 1e-9:
            return None
        if side == "over":
            return 1 if total > line else 0
        if side == "under":
            return 1 if total < line else 0
        return None

    if kind == "btts":
        side = str(params.get("side", "")).lower()
        both_scored = home_goals > 0 and away_goals > 0
        if side in ("yes", "y", "true"):
            return 1 if both_scored else 0
        if side in ("no", "n", "false"):
            return 1 if not both_scored else 0
        return None

    if kind == "team_total":
        team = str(params.get("team", "")).lower()
        try:
            line = float(params.get("line"))
        except (TypeError, ValueError):
            return None
        side = str(params.get("side", "")).lower()
        goals = home_goals if team in ("home", "h") else (
            away_goals if team in ("away", "a") else None
        )
        if goals is None:
            return None
        if abs(goals - line) < 1e-9:
            return None  # exact integer line pushes
        if side == "over":
            return 1 if goals > line else 0
        if side == "under":
            return 1 if goals < line else 0
        return None

    if kind == "correct_score":
        try:
            h = int(params.get("home"))
            a = int(params.get("away"))
        except (TypeError, ValueError):
            return None
        return 1 if (home_goals == h and away_goals == a) else 0

    # Anything else (anytime_scorer, first_scorer, player_yellow,
    # total_cards, total_corners, total_shots, ...) needs a stats feed
    # we don't ingest yet. Caller treats None as "skipped".
    return None


def _grade_market_type_from_score(
    market_type: str, leg: Dict[str, object], home_goals: int, away_goals: int
) -> Optional[int]:
    """Grade a market_type string. The legacy persisted-prediction path
    stores the canonical market_type (e.g. "over_2_5") rather than the
    raw leg kind, so we synthesise an equivalent BetLeg dict where
    needed."""
    mt = market_type.lower()
    if mt in ("home_win",):
        return _resolve_leg({"kind": "match_result", "params": {"side": "H"}}, home_goals, away_goals)
    if mt in ("draw",):
        return _resolve_leg({"kind": "match_result", "params": {"side": "D"}}, home_goals, away_goals)
    if mt in ("away_win",):
        return _resolve_leg({"kind": "match_result", "params": {"side": "A"}}, home_goals, away_goals)
    if mt.startswith("over_") or mt.startswith("under_"):
        # e.g. "over_2_5" -> side=over, line=2.5
        side, _, line_str = mt.partition("_")
        try:
            line = float(line_str.replace("_", "."))
        except ValueError:
            return None
        return _resolve_leg(
            {"kind": "total_goals", "params": {"line": line, "side": side}},
            home_goals, away_goals,
        )
    if mt == "btts_yes":
        return _resolve_leg({"kind": "btts", "params": {"side": "yes"}}, home_goals, away_goals)
    if mt == "btts_no":
        return _resolve_leg({"kind": "btts", "params": {"side": "no"}}, home_goals, away_goals)
    # Fall back to grading the stored leg directly.
    return _resolve_leg(leg, home_goals, away_goals)


def _parlay_blueprints(
    *,
    probs: Dict[str, float],
    sims: List[object],
    home: str,
    away: str,
) -> List[SoccerParlayBlueprintOut]:
    # Empirically tune the over/under lines per stat to whatever the sims
    # produce a roughly-50/50 split at — avoids surfacing legs whose
    # probabilities are stuck near 0 or 1.
    line_corners = _pick_line([s.total_corners for s in sims], default=9.5, candidates=[7.5, 8.5, 9.5, 10.5, 11.5])
    line_shots = _pick_line([s.total_shots for s in sims], default=24.5, candidates=[20.5, 22.5, 24.5, 26.5, 28.5])
    line_sot = _pick_line([s.total_shots_on_target_count for s in sims], default=8.5, candidates=[6.5, 7.5, 8.5, 9.5, 10.5])
    line_fouls = _pick_line([s.total_fouls for s in sims], default=20.5, candidates=[18.5, 20.5, 22.5, 24.5])
    line_cards = _pick_line([s.total_cards for s in sims], default=4.5, candidates=[2.5, 3.5, 4.5, 5.5])

    candidates: List[tuple[str, BetLeg]] = [
        (f"{home} win", BetLeg(kind="match_result", params={"side": "H"}, label=f"{home} win")),
        ("Draw", BetLeg(kind="match_result", params={"side": "D"}, label="Draw")),
        (f"{away} win", BetLeg(kind="match_result", params={"side": "A"}, label=f"{away} win")),
        ("Over 2.5 goals", BetLeg(kind="total_goals", params={"line": 2.5, "side": "over"}, label="Over 2.5 goals")),
        ("Under 2.5 goals", BetLeg(kind="total_goals", params={"line": 2.5, "side": "under"}, label="Under 2.5 goals")),
        ("BTTS yes", BetLeg(kind="btts", params={"side": "yes"}, label="BTTS yes")),
        ("BTTS no", BetLeg(kind="btts", params={"side": "no"}, label="BTTS no")),
        (f"Over {line_corners} corners", BetLeg(kind="total_corners", params={"line": line_corners, "side": "over"}, label=f"Over {line_corners} corners")),
        (f"Under {line_corners} corners", BetLeg(kind="total_corners", params={"line": line_corners, "side": "under"}, label=f"Under {line_corners} corners")),
        (f"Over {line_shots} shots", BetLeg(kind="total_shots", params={"line": line_shots, "side": "over"}, label=f"Over {line_shots} shots")),
        (f"Over {line_sot} shots on target", BetLeg(kind="total_shots_on_target", params={"line": line_sot, "side": "over"}, label=f"Over {line_sot} SOT")),
        (f"Over {line_fouls} fouls", BetLeg(kind="total_fouls", params={"line": line_fouls, "side": "over"}, label=f"Over {line_fouls} fouls")),
        (f"Over {line_cards} cards", BetLeg(kind="total_cards", params={"line": line_cards, "side": "over"}, label=f"Over {line_cards} cards")),
        (f"Under {line_cards} cards", BetLeg(kind="total_cards", params={"line": line_cards, "side": "under"}, label=f"Under {line_cards} cards")),
    ]
    single_prob = {
        "match_result:H": probs.get("home_win", 0.0),
        "match_result:D": probs.get("draw", 0.0),
        "match_result:A": probs.get("away_win", 0.0),
        "total_goals:over": probs.get("over_2_5", 0.0),
        "total_goals:under": probs.get("under_2_5", 0.0),
        "btts:yes": probs.get("btts_yes", 0.0),
        "btts:no": probs.get("btts_no", 0.0),
    }
    # For stat legs we estimate the single-leg prob from sims directly.
    for label_a, leg_a in candidates:
        k = key_func(leg_a)
        if k in single_prob:
            continue
        single_prob[k] = _leg_prob(sims, leg_a)

    priced: List[tuple[float, SoccerParlayBlueprintOut]] = []
    for i, (label_a, leg_a) in enumerate(candidates):
        for label_b, leg_b in candidates[i + 1:]:
            if leg_a.kind == leg_b.kind:
                continue
            # Skip parlays mixing two "wide" stat legs of similar kind to
            # avoid generating noisy combinations.
            if {leg_a.kind, leg_b.kind} <= {"total_shots", "total_shots_on_target", "total_corners"}:
                continue
            pa = single_prob.get(key_func(leg_a), 0.0)
            pb = single_prob.get(key_func(leg_b), 0.0)
            if pa < 0.30 or pb < 0.30:
                continue
            quote = price_bet_builder(sims, [leg_a, leg_b], min_edge=settings.soccer_min_edge)
            if quote.fair_probability < 0.12 or quote.risk_level in {"high", "lottery"}:
                continue
            priced.append((
                quote.safety_score + quote.fair_probability * 20.0,
                SoccerParlayBlueprintOut(
                    label=f"{label_a} + {label_b}",
                    legs=[
                        BetLegIn(kind=leg_a.kind, params=dict(leg_a.params), label=leg_a.label),
                        BetLegIn(kind=leg_b.kind, params=dict(leg_b.params), label=leg_b.label),
                    ],
                    fair_probability=quote.fair_probability,
                    fair_decimal_odds=quote.fair_decimal_odds,
                    correlation_factor=quote.correlation_factor,
                    safety_score=quote.safety_score,
                    risk_level=quote.risk_level,
                    risk_flags=quote.risk_flags,
                ),
            ))
    return [p for _score, p in sorted(priced, key=lambda x: -x[0])[:8]]


def key_func(leg: BetLeg) -> str:
    if leg.kind == "match_result":
        return f"match_result:{str(leg.params.get('side', '')).upper()}"
    if leg.kind in {"total_corners", "total_shots", "total_shots_on_target",
                    "total_fouls", "total_cards", "total_goals"}:
        return f"{leg.kind}:{str(leg.params.get('side', '')).lower()}:{leg.params.get('line','')}"
    if leg.kind in {"team_corners", "team_shots", "team_shots_on_target",
                    "team_fouls", "team_cards", "team_total"}:
        return (f"{leg.kind}:{leg.params.get('team','')}:"
                f"{str(leg.params.get('side','')).lower()}:{leg.params.get('line','')}")
    return f"{leg.kind}:{str(leg.params.get('side', '')).lower()}"


def _leg_prob(sims: List[object], leg: BetLeg) -> float:
    if not sims:
        return 0.0
    hits = 0
    for s in sims:
        from src.sports.soccer.simulator.bet_builder import _evaluate
        try:
            if _evaluate(s, leg):
                hits += 1
        except Exception:
            return 0.0
    return hits / len(sims)


def _pick_line(values: List[int], *, default: float, candidates: List[float]) -> float:
    """Pick the candidate line whose over-probability is closest to 0.55
    (slight bias toward favouring the under so we don't always recommend
    overs). Falls back to `default` if no values."""
    if not values:
        return default
    n = len(values)
    best_line = default
    best_score = float("inf")
    for line in candidates:
        over_p = sum(1 for v in values if v > line) / n
        # target 0.50-0.55 so the leg isn't a near-cert one way or the other
        score = abs(over_p - 0.53)
        if score < best_score:
            best_score = score
            best_line = line
    return best_line


def _betslips_for_fixture(
    *,
    eng: SoccerEngine,
    fixture: Dict[str, object],
    events: List[Dict[str, object]],
    bankroll: float,
    min_edge: float,
    n_sims: int,
) -> List[SoccerBetslipOut]:
    assert eng.score_model is not None
    home_id = fixture["home_team_id"]
    away_id = fixture["away_team_id"]
    neutral = bool(fixture["neutral_venue"])
    home = eng.team_names.get(home_id, home_id)
    away = eng.team_names.get(away_id, away_id)
    match_label = f"{home} vs {away}"
    probs = eng.score_model.outcome_probs(home_id, away_id, neutral=neutral, max_goals=10)
    probs = eng.calibrate_probs(probs)
    event = _find_odds_event(events, fixture, home, away)
    slips: List[SoccerBetslipOut] = []

    # Build the de-vig vector for each market once, so every selection
    # within a market shares the same no-vig consensus probability.
    h2h_market = [(home, None), ("Draw", None), (away, None)]
    totals_market = [("Over", 2.5), ("Under", 2.5)]
    h2h_vec = _devig_vector(event, "h2h", h2h_market) if event else None
    totals_vec = _devig_vector(event, "totals", totals_market) if event else None
    market_vectors = {
        ("h2h", home, None): h2h_vec,
        ("h2h", "Draw", None): h2h_vec,
        ("h2h", away, None): h2h_vec,
        ("totals", "Over", 2.5): totals_vec,
        ("totals", "Under", 2.5): totals_vec,
    }

    specs = [
        ("match_result", {"side": "H"}, "h2h", home, f"{home} win", probs["home_win"], None),
        ("match_result", {"side": "D"}, "h2h", "Draw", "Draw", probs["draw"], None),
        ("match_result", {"side": "A"}, "h2h", away, f"{away} win", probs["away_win"], None),
        ("total_goals", {"line": 2.5, "side": "over"}, "totals", "Over", "Over 2.5 goals", probs["over_2_5"], 2.5),
        ("total_goals", {"line": 2.5, "side": "under"}, "totals", "Under", "Under 2.5 goals", probs["under_2_5"], 2.5),
    ]
    for kind, params, market_key, selection, label, p_model, point in specs:
        prices = _book_prices(event, market_key, selection, point=point) if event else []
        if not prices:
            continue
        best_book, best_decimal = max(prices, key=lambda x: x[1])
        market_vec = market_vectors.get((market_key, selection, point))
        rep = report_edge(
            float(p_model),
            best_decimal,
            min_edge=min_edge,
            kelly_fraction=settings.soccer_kelly_fraction,
            kelly_cap=settings.soccer_kelly_cap,
            market_decimal_odds=market_vec,
        )
        if rep.edge is None or rep.edge < min_edge or rep.kelly_fraction <= 0:
            continue
        # When we have a de-vig vector, demand the model also beats the
        # no-vig market consensus by min_edge. Beating only the raw best
        # book price is largely just consuming the book's vig and is the
        # #1 trap retail bettors fall into.
        if rep.edge_vs_market is not None and rep.edge_vs_market < min_edge:
            continue
        confidence, warnings = _single_confidence(
            p_model=float(p_model),
            edge=float(rep.edge),
            book_count=len(prices),
            edge_vs_market=rep.edge_vs_market,
        )
        stake = round(bankroll * rep.kelly_fraction * confidence, 2)
        reasons = [
            f"Model {float(p_model) * 100:.1f}% vs raw market {100 / best_decimal:.1f}%.",
            f"Best live price {best_decimal:.2f} at {best_book}; fair price {rep.fair_decimal_odds:.2f}.",
            f"Kelly suggests {rep.kelly_fraction * 100:.2f}% bankroll before confidence haircut.",
        ]
        if rep.no_vig_market_prob is not None:
            reasons.insert(
                1,
                f"De-vig market consensus {rep.no_vig_market_prob * 100:.1f}% "
                f"(edge vs consensus {rep.edge_vs_market * 100:+.2f}%).",
            )
        slips.append(SoccerBetslipOut(
            slip_id=f"{fixture['id']}:{kind}:{selection}:{point or ''}",
            fixture_id=str(fixture["id"]),
            match_label=match_label,
            kickoff_unix=int(fixture["kickoff_unix"]),
            slip_type="single",
            title=f"{label} at {best_book}",
            legs=[SoccerBetslipLegOut(
                kind=kind,
                label=label,
                params=dict(params),
                model_probability=float(p_model),
                fair_decimal_odds=rep.fair_decimal_odds,
                best_decimal=best_decimal,
                best_book=best_book,
                edge=rep.edge,
            )],
            fair_probability=float(p_model),
            fair_decimal_odds=rep.fair_decimal_odds,
            book_decimal_odds=best_decimal,
            minimum_acceptable_decimal=round(rep.fair_decimal_odds * (1.0 + min_edge), 3),
            edge=rep.edge,
            kelly_fraction=rep.kelly_fraction,
            stake_usd=stake,
            safety_score=max(0.0, min(100.0, 78 + min(18, rep.edge * 100) - len(warnings) * 8)),
            risk_level="safer" if float(p_model) >= 0.35 and rep.edge >= 0.08 else "moderate",
            risk_flags=warnings,
            confidence=confidence,
            reasons=reasons,
            warnings=warnings,
        ))

    sims = eng.simulator().simulate(
        home_id,
        away_id,
        neutral_venue=neutral,
        config=SimulationConfig(n_sims=max(500, min(int(n_sims), 8000)), seed=hash((fixture["id"], "betslips")) & 0xFFFFFFFF),
    )

    # Stat-market singles (corners/shots/SOT/fouls/cards). The Odds API
    # free tier does not return these markets, so we publish them as
    # model-only suggestions with a minimum acceptable book price.
    stat_singles = _stat_market_singles(
        fixture=fixture,
        match_label=match_label,
        home=home,
        away=away,
        sims=sims,
        bankroll=bankroll,
        min_edge=min_edge,
    )
    slips.extend(stat_singles)

    blueprints = _parlay_blueprints(probs=probs, sims=sims, home=home, away=away)
    for bp in blueprints[:3]:
        if bp.risk_level not in {"safer", "moderate"} or bp.fair_probability < 0.14:
            continue
        min_price = bp.fair_decimal_odds * (1.0 + max(min_edge, 0.05))
        # SGP / model parlays carry NO confirmed book price (books quote
        # SGPs with their own correlation adjustment that we don't know).
        # Per P0-5 review: surface as SCOUT only — zero stake until the
        # user pastes a real SGP price into the bet builder and re-prices.
        slips.append(SoccerBetslipOut(
            slip_id=f"{fixture['id']}:parlay:{abs(hash(bp.label))}",
            fixture_id=str(fixture["id"]),
            match_label=match_label,
            kickoff_unix=int(fixture["kickoff_unix"]),
            slip_type="model_parlay",
            title=f"SCOUT · {bp.label}",
            legs=[
                SoccerBetslipLegOut(kind=l.kind, label=l.label or l.kind, params=dict(l.params))
                for l in bp.legs
            ],
            fair_probability=bp.fair_probability,
            fair_decimal_odds=bp.fair_decimal_odds,
            book_decimal_odds=None,
            minimum_acceptable_decimal=round(min_price, 3),
            edge=None,
            kelly_fraction=0.0,
            stake_usd=0.0,
            safety_score=bp.safety_score,
            risk_level="scout",
            risk_flags=bp.risk_flags,
            confidence=max(0.35, min(0.75, bp.safety_score / 100.0 - 0.15)),
            reasons=[
                "Generated from joint Monte Carlo (correct correlation), not independent-leg multiplication.",
                f"Only bet if sportsbook offers at least {min_price:.2f} combined odds.",
                f"Correlation factor {bp.correlation_factor:.2f}; fair probability {bp.fair_probability * 100:.1f}%.",
                "SCOUT: SGP price not auto-fetched — paste into bet builder before staking.",
            ],
            warnings=[
                "Books apply their own SGP correlation rebate; their quoted price will be lower than the leg product.",
                *bp.risk_flags,
            ],
            scout_only=True,
        ))
    return slips


def _stat_market_singles(
    *,
    fixture: Dict[str, object],
    match_label: str,
    home: str,
    away: str,
    sims: List[object],
    bankroll: float,
    min_edge: float,
) -> List[SoccerBetslipOut]:
    """Emit model-only singles for stat markets (corners, shots, SOT,
    fouls, cards) that the free Odds API tier does not cover. Each
    line is auto-tuned to land near a ~52% probability so the bet is
    realistic rather than a near-certainty."""
    if not sims:
        return []

    def _stat_values(key: str) -> List[int]:
        return [int(getattr(s, key)) for s in sims]

    markets = [
        ("total_corners", "total_corners", "Total corners", [7.5, 8.5, 9.5, 10.5, 11.5, 12.5], 9.5),
        ("total_shots", "total_shots", "Total shots", [18.5, 20.5, 22.5, 24.5, 26.5, 28.5], 24.5),
        ("total_shots_on_target", "total_shots_on_target_count", "Total shots on target", [5.5, 6.5, 7.5, 8.5, 9.5, 10.5], 8.5),
        ("total_fouls", "total_fouls", "Total fouls", [16.5, 18.5, 20.5, 22.5, 24.5], 20.5),
        ("total_cards", "total_cards", "Total cards", [2.5, 3.5, 4.5, 5.5, 6.5], 4.5),
    ]
    team_markets = [
        ("team_corners", "home_corners", "away_corners", "corners", [2.5, 3.5, 4.5, 5.5, 6.5], 4.5),
        ("team_shots", "home_shots", "away_shots", "shots", [8.5, 10.5, 12.5, 14.5], 12.5),
        ("team_shots_on_target", "home_shots_on_target", "away_shots_on_target", "shots on target", [2.5, 3.5, 4.5, 5.5], 4.5),
        ("team_fouls", "home_fouls", "away_fouls", "fouls", [7.5, 9.5, 11.5, 13.5], 10.5),
        ("team_cards", "home_yellow", "away_yellow", "cards", [0.5, 1.5, 2.5, 3.5], 1.5),
    ]

    out: List[SoccerBetslipOut] = []

    def _publish_total(kind: str, attr: str, label_prefix: str, candidates: List[float], default: float) -> None:
        values = _stat_values(attr)
        if not values:
            return
        for side in ("over", "under"):
            line = _line_near_target(values, candidates, default, side=side, target=0.55)
            p = _empirical_side_prob(values, line, side)
            if p < 0.50 or p > 0.78:
                continue
            slip = _build_stat_single(
                fixture=fixture,
                match_label=match_label,
                kind=kind,
                params={"line": line, "side": side},
                label=f"{side.capitalize()} {line} {label_prefix.lower()}",
                p_model=p,
                bankroll=bankroll,
                min_edge=min_edge,
            )
            if slip is not None:
                out.append(slip)

    def _publish_team(kind: str, home_attr: str, away_attr: str, suffix: str, candidates: List[float], default: float) -> None:
        for team_id, team_name, attr in (("home", home, home_attr), ("away", away, away_attr)):
            values = [int(getattr(s, attr)) for s in sims]
            if not values:
                continue
            for side in ("over", "under"):
                line = _line_near_target(values, candidates, default, side=side, target=0.55)
                p = _empirical_side_prob(values, line, side)
                if p < 0.52 or p > 0.78:
                    continue
                slip = _build_stat_single(
                    fixture=fixture,
                    match_label=match_label,
                    kind=kind,
                    params={"team": team_id, "line": line, "side": side},
                    label=f"{team_name} {side} {line} {suffix}",
                    p_model=p,
                    bankroll=bankroll,
                    min_edge=min_edge,
                )
                if slip is not None:
                    out.append(slip)

    for kind, attr, label, candidates, default in markets:
        _publish_total(kind, attr, label, candidates, default)
    for kind, h_attr, a_attr, suffix, candidates, default in team_markets:
        _publish_team(kind, h_attr, a_attr, suffix, candidates, default)
    # Keep at most 8 stat singles per fixture to avoid drowning the UI.
    return out[:8]


def _line_near_target(values: List[int], candidates: List[float], default: float, *, side: str, target: float) -> float:
    """Pick the candidate line whose chosen-side probability sits closest
    to `target`. Prefers lines that produce a realistic edge."""
    if not values:
        return default
    n = len(values)
    best_line = default
    best_score = float("inf")
    for line in candidates:
        if side == "over":
            p = sum(1 for v in values if v > line) / n
        else:
            p = sum(1 for v in values if v < line) / n
        score = abs(p - target)
        if score < best_score:
            best_score = score
            best_line = line
    return best_line


def _empirical_side_prob(values: List[int], line: float, side: str) -> float:
    if not values:
        return 0.0
    n = len(values)
    if side == "over":
        return sum(1 for v in values if v > line) / n
    return sum(1 for v in values if v < line) / n


def _build_stat_single(
    *,
    fixture: Dict[str, object],
    match_label: str,
    kind: str,
    params: Dict[str, object],
    label: str,
    p_model: float,
    bankroll: float,
    min_edge: float,
) -> Optional[SoccerBetslipOut]:
    if p_model <= 0 or p_model >= 1:
        return None
    fair = 1.0 / p_model
    min_price = fair * (1.0 + max(min_edge, 0.05))
    # No confirmed book quote for these markets on the Odds API free tier.
    # Per pro-bettor review (P0-5): publish as SCOUT only — zero stake,
    # so the user must paste in a real price and re-price via the bet
    # builder before risking capital.
    safety = max(0.0, min(100.0, 50 + (p_model - 0.5) * 100))
    risk_level = "scout"
    risk_flags: List[str] = [
        "SCOUT — no confirmed sportsbook quote; the auto-tuned line maximises hit-rate, not edge.",
        "Paste the real book price into the bet builder before staking.",
    ]
    slug = f"{kind}:{params.get('team','total')}:{params.get('side','')}:{params.get('line','')}"
    return SoccerBetslipOut(
        slip_id=f"{fixture['id']}:{slug}",
        fixture_id=str(fixture["id"]),
        match_label=match_label,
        kickoff_unix=int(fixture["kickoff_unix"]),
        slip_type="model_single",
        title=f"SCOUT · {label}",
        legs=[SoccerBetslipLegOut(
            kind=kind,
            label=label,
            params=dict(params),
            model_probability=float(p_model),
            fair_decimal_odds=round(fair, 3),
        )],
        fair_probability=float(p_model),
        fair_decimal_odds=round(fair, 3),
        book_decimal_odds=None,
        minimum_acceptable_decimal=round(min_price, 3),
        edge=None,
        kelly_fraction=0.0,
        stake_usd=0.0,
        safety_score=safety,
        risk_level=risk_level,
        risk_flags=risk_flags,
        confidence=max(0.30, min(0.65, p_model - 0.10)),
        reasons=[
            f"Joint Monte Carlo gives {p_model * 100:.1f}% — fair price {fair:.2f}.",
            f"Only bet if the sportsbook quotes at least {min_price:.2f}.",
            "Auto-tuned line maximises hit-rate, not edge — confirm a real book quote before betting.",
        ],
        warnings=risk_flags,
        scout_only=True,
    )


def _single_confidence(
    *,
    p_model: float,
    edge: float,
    book_count: int,
    edge_vs_market: Optional[float] = None,
) -> tuple[float, List[str]]:
    confidence = 0.72
    warnings: List[str] = []
    if edge >= 0.15:
        confidence += 0.08
    elif edge < 0.06:
        confidence -= 0.10
        warnings.append("edge is positive but thin")
    if p_model < 0.22:
        confidence -= 0.15
        warnings.append("lower-probability outcome")
    if p_model > 0.92:
        confidence -= 0.12
        warnings.append("near-certain model price can be fragile")
    if book_count < 3:
        confidence -= 0.10
        warnings.append("thin book coverage")
    if edge_vs_market is None:
        warnings.append("no de-vig consensus available (single-book market)")
        confidence -= 0.05
    elif edge_vs_market < 0.04:
        warnings.append("edge vs de-vig consensus is thin")
        confidence -= 0.05
    elif edge_vs_market > 0.10:
        confidence += 0.05
    return max(0.25, min(0.90, confidence)), warnings


def _cross_fixture_parlays(
    slips: List[SoccerBetslipOut],
    *,
    bankroll: float,
    min_edge: float,
) -> List[SoccerBetslipOut]:
    singles = [
        s for s in slips
        if s.slip_type == "single"
        and s.book_decimal_odds
        and s.edge is not None
        and s.edge >= min_edge
        and s.fair_probability >= 0.28
        and s.confidence >= 0.55
    ]
    singles = sorted(singles, key=_betslip_rank, reverse=True)
    out: List[SoccerBetslipOut] = []
    used_sets: set[tuple[str, ...]] = set()
    for size in (2, 3):
        pool = singles[:14]
        combos: List[SoccerBetslipOut] = []
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                group = [pool[i], pool[j]]
                if size == 3:
                    for k in range(j + 1, len(pool)):
                        combos.append(_make_cross_fixture_parlay(group + [pool[k]], bankroll, min_edge))
                else:
                    combos.append(_make_cross_fixture_parlay(group, bankroll, min_edge))
        for slip in sorted((c for c in combos if c is not None), key=_betslip_rank, reverse=True):
            key = tuple(sorted(l.label + l.kind for l in slip.legs))
            if key in used_sets:
                continue
            used_sets.add(key)
            out.append(slip)
            if len(out) >= 8:
                return out
    return out


def _make_cross_fixture_parlay(
    parts: List[SoccerBetslipOut],
    bankroll: float,
    min_edge: float,
) -> Optional[SoccerBetslipOut]:
    fixture_ids = {p.fixture_id for p in parts}
    if len(fixture_ids) != len(parts):
        return None
    p_model = 1.0
    book_decimal = 1.0
    for p in parts:
        p_model *= p.fair_probability
        if not p.book_decimal_odds:
            return None
        book_decimal *= p.book_decimal_odds
    if p_model <= 0:
        return None
    fair_decimal = 1.0 / p_model
    edge = p_model * book_decimal - 1.0
    if edge < min_edge:
        return None
    leg_out: List[SoccerBetslipLegOut] = []
    for p in parts:
        for leg in p.legs:
            leg_out.append(SoccerBetslipLegOut(
                kind=leg.kind,
                label=f"{p.match_label}: {leg.label}",
                params=dict(leg.params),
                model_probability=leg.model_probability,
                fair_decimal_odds=leg.fair_decimal_odds,
                best_decimal=leg.best_decimal,
                best_book=leg.best_book,
                edge=leg.edge,
            ))
    confidence = max(0.25, min(p.confidence for p in parts) - 0.08 * (len(parts) - 1))
    safety = max(0.0, min(100.0, min(p.safety_score for p in parts) - 10 * (len(parts) - 1)))
    risk = "safer" if len(parts) == 2 and safety >= 72 and p_model >= 0.18 else "moderate"
    if len(parts) == 3 or p_model < 0.12:
        risk = "high"
    stake_fraction = min(settings.soccer_kelly_cap * 0.35, 0.004)
    if risk == "high":
        stake_fraction *= 0.45
    stake = round(bankroll * stake_fraction * confidence, 2)
    title = " + ".join(p.title.split(" at ")[0] for p in parts)
    return SoccerBetslipOut(
        slip_id="cross:" + ":".join(p.slip_id for p in parts),
        fixture_id=",".join(p.fixture_id for p in parts),
        match_label=f"{len(parts)} matches",
        kickoff_unix=min(p.kickoff_unix for p in parts),
        slip_type="cross_fixture_parlay",
        title=title,
        legs=leg_out,
        fair_probability=p_model,
        fair_decimal_odds=fair_decimal,
        book_decimal_odds=book_decimal,
        minimum_acceptable_decimal=round(fair_decimal * (1.0 + max(min_edge, 0.06)), 3),
        edge=edge,
        kelly_fraction=stake_fraction,
        stake_usd=stake,
        safety_score=safety,
        risk_level=risk,
        risk_flags=[] if risk != "high" else ["multi-leg parlay variance"],
        confidence=confidence,
        reasons=[
            f"Combines {len(parts)} independent fixtures, so same-game correlation is avoided.",
            f"Combined live odds {book_decimal:.2f} vs fair {fair_decimal:.2f}.",
            f"Combined model probability {p_model * 100:.1f}%; edge {edge * 100:.1f}%.",
        ],
        warnings=[
            "Parlay assumes fixture outcomes are independent.",
            "Stake is haircut versus singles because variance compounds.",
        ],
    )


def _ranked_mixed_slips(slips: List[SoccerBetslipOut], *, max_slips: int) -> List[SoccerBetslipOut]:
    singles = sorted([s for s in slips if s.slip_type == "single"], key=_betslip_rank, reverse=True)
    same_game = sorted([s for s in slips if s.slip_type == "model_parlay"], key=_betslip_rank, reverse=True)
    cross = sorted([s for s in slips if s.slip_type == "cross_fixture_parlay"], key=_betslip_rank, reverse=True)
    stat_singles = sorted([s for s in slips if s.slip_type == "model_single"], key=_betslip_rank, reverse=True)
    out: List[SoccerBetslipOut] = []
    quotas = [
        (cross, max(2, max_slips // 4)),
        (same_game, max(2, max_slips // 4)),
        (singles, max(2, max_slips // 4)),
        (stat_singles, max(2, max_slips // 4)),
    ]
    for bucket, quota in quotas:
        for slip in bucket[:quota]:
            if slip not in out:
                out.append(slip)
    for slip in sorted(slips, key=_betslip_rank, reverse=True):
        if len(out) >= max_slips:
            break
        if slip not in out:
            out.append(slip)
    return out


def _betslip_rank(slip: SoccerBetslipOut) -> float:
    edge = slip.edge if slip.edge is not None else max(0.0, (slip.minimum_acceptable_decimal / slip.fair_decimal_odds) - 1.0)
    return (
        edge * 100.0
        + slip.safety_score * 0.45
        + slip.confidence * 20.0
        + (8.0 if slip.slip_type == "single" else 0.0)
        - len(slip.warnings) * 4.0
    )


def _soccer_ai_review(
    *,
    fixture: Dict[str, object],
    home_name: str,
    away_name: str,
    legs: List[BetLeg],
    quote: object,
) -> Optional[str]:
    payload = {
        "match": {
            "home": home_name,
            "away": away_name,
            "competition": fixture.get("competition"),
            "kickoff_unix": fixture.get("kickoff_unix"),
        },
        "legs": [
            {"kind": l.kind, "params": dict(l.params), "label": l.label}
            for l in legs
        ],
        "model_quote": {
            "fair_probability": getattr(quote, "fair_probability", None),
            "fair_decimal_odds": getattr(quote, "fair_decimal_odds", None),
            "leg_probabilities": getattr(quote, "leg_probabilities", None),
            "correlation_factor": getattr(quote, "correlation_factor", None),
            "book_decimal_odds": getattr(quote, "book_decimal_odds", None),
            "edge": getattr(quote, "edge", None),
            "kelly_fraction": getattr(quote, "kelly_fraction", None),
            "safety_score": getattr(quote, "safety_score", None),
            "risk_level": getattr(quote, "risk_level", None),
            "risk_flags": getattr(quote, "risk_flags", None),
            "recommendation": getattr(quote, "recommendation", None),
        },
        "task": (
            "Review this soccer parlay as a conservative risk analyst. "
            "Use only the supplied model numbers. Do not claim certainty. "
            "Return 2 short sentences: first the main risk, then whether this "
            "is safer, moderate, high risk, or lottery-style."
        ),
    }
    try:
        with httpx.Client(timeout=18.0) as client:
            r = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.soccer_ai_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a conservative sports betting risk reviewer. "
                                "You do not create probabilities. You explain model risk briefly."
                            ),
                        },
                        {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
                    ],
                },
            )
            r.raise_for_status()
            data = r.json()
            text = str(data["choices"][0]["message"]["content"]).strip()
            return text[:500]
    except Exception as e:
        _LOG.warning("soccer AI review failed: %s", e)
        return None
