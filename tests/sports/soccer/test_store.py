"""Tests for the soccer SQLite store."""

from __future__ import annotations

from src.sports.soccer.data.store import SoccerStore
from src.sports.soccer.types import Player, Team


def test_upsert_and_read_back(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    s.upsert_teams([Team(team_id="FRA", name="France", country="FR", elo=2050)])
    s.upsert_players([Player(player_id="fra-mbappe", name="Mbappé", team_id="FRA", position="FW")])
    s.upsert_fixtures([{
        "fixture_id": "demo", "home_team_id": "FRA", "away_team_id": "ENG",
        "kickoff_unix": 1_900_000_000, "competition": "FIFA WC", "neutral_venue": True,
    }])
    teams = s.teams_all()
    assert any(t["id"] == "FRA" for t in teams)
    fix = s.fixtures_upcoming(0, limit=10)
    assert len(fix) == 1
    assert fix[0]["home_team_id"] == "FRA"
    pls = s.players_for_team("FRA")
    assert len(pls) == 1


def test_record_prediction_persists_and_round_trips(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    pid = s.record_prediction(
        fixture_id="demo",
        market_type="match_result",
        leg={"kind": "match_result", "params": {"side": "H"}},
        fair_probability=0.42,
        fair_decimal_odds=2.38,
        book_decimal_odds=2.40,
        pinnacle_close_decimal=2.20,
        edge=0.008,
        kelly_fraction=0.005,
        recommendation="thin_edge",
        recorded_unix=1700000000,
    )
    assert pid > 0


def test_upsert_is_idempotent_on_id(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    s.upsert_teams([Team(team_id="FRA", name="France", country="FR", elo=2000)])
    s.upsert_teams([Team(team_id="FRA", name="France", country="FR", elo=2100)])
    teams = {t["id"]: t for t in s.teams_all()}
    assert teams["FRA"]["elo"] == 2100.0
