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
from typing import Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from src.config import settings
from src.sports.soccer.calibration.isotonic import (
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


class CalibrationOut(BaseModel):
    market_type: str
    n_resolved: int
    brier: float
    reliability: List[Dict[str, float]]


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


class StatsBombCompetitionIn(BaseModel):
    competition_id: int
    season_id: int
    neutral: bool = True


class FitFromStatsBombIn(BaseModel):
    competitions: Optional[List[StatsBombCompetitionIn]] = None
    decay_per_day: Optional[float] = None


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

    # --------------------------------------------------------------
    def is_ready(self) -> bool:
        return self.score_model is not None and len(self.squads) > 0

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

        # Fit ratings & score model on combined match set
        self.team_names = {tid: meta["name"] for tid, meta in team_lookup.items()}
        self.elo = EloTable()
        self.elo.fit(all_matches)
        self.score_model = DixonColesModel.fit(all_matches, decay_per_day=decay)

        # Build squads from priors only (deep player ingest is a phase-2 step:
        # we'd loop sb.lineups + sb.events for every match, ~5MB per events
        # file × hundreds of matches). For now, emit a generic per-team squad
        # so the bet-builder UI's player-leg dropdowns are populated.
        squads_seed: Dict[str, List[Dict[str, str]]] = {}
        for tid in team_lookup:
            squads_seed[tid] = [
                {"player_id": f"{tid}-fw1", "position": "FW", "name": f"{team_lookup[tid]['name']} FW1"},
                {"player_id": f"{tid}-fw2", "position": "FW", "name": f"{team_lookup[tid]['name']} FW2"},
                {"player_id": f"{tid}-mf1", "position": "MF", "name": f"{team_lookup[tid]['name']} MF1"},
                {"player_id": f"{tid}-mf2", "position": "MF", "name": f"{team_lookup[tid]['name']} MF2"},
                {"player_id": f"{tid}-df1", "position": "DF", "name": f"{team_lookup[tid]['name']} DF1"},
                {"player_id": f"{tid}-gk",  "position": "GK", "name": f"{team_lookup[tid]['name']} GK"},
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

        self.fitted_at_unix = int(time.time())
        self.fit_source = "statsbomb"

        # Seed a few "WC 2026 group-stage style" fixtures using real-data
        # team IDs so the dashboard's fixtures panel populates immediately
        # after refit. These are synthetic for now and will be replaced once
        # we wire The Odds API ingest of upcoming fixtures.
        top = sorted(self.elo.to_dict().items(), key=lambda kv: -kv[1])
        top_ids = [tid for tid, _ in top[:8]]
        if len(top_ids) >= 8:
            kickoff = int(time.time()) + 3 * 86400
            pairings = [
                (top_ids[0], top_ids[1]),
                (top_ids[2], top_ids[3]),
                (top_ids[4], top_ids[5]),
                (top_ids[6], top_ids[7]),
                (top_ids[0], top_ids[2]),
                (top_ids[1], top_ids[3]),
            ]
            fixtures = [
                {
                    "fixture_id": f"sb-{h}-{a}-{i}",
                    "home_team_id": h,
                    "away_team_id": a,
                    "kickoff_unix": kickoff + i * 86400,
                    "competition": "WC 2026 (sample)",
                    "neutral_venue": True,
                }
                for i, (h, a) in enumerate(pairings)
            ]
            self.store.upsert_fixtures(fixtures)

        return {
            "competitions": sources,
            "matches_used": len(all_matches),
            "teams": len(team_lookup),
            "decay_per_day": decay,
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


@router.get("/soccer/teams", response_model=List[TeamOut])
def teams(request: Request) -> List[TeamOut]:
    eng = _engine(request)
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


@router.post("/soccer/bet-builder", response_model=BetBuilderOut)
def bet_builder(request: Request, payload: BetBuilderIn = Body(...)) -> BetBuilderOut:
    eng = _engine(request)
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
