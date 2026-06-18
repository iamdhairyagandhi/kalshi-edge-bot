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


# ----------------------------------------------------------------------
# soccer_bets — Phase 2 bet journal
# ----------------------------------------------------------------------
def _sample_legs():
    return [
        {
            "kind": "match_result",
            "params": {"side": "H"},
            "label": "Home win",
        },
    ]


def test_record_soccer_bet_round_trips(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    bid = s.record_soccer_bet(
        slip_id="slip-1",
        fixture_id="demo-1",
        match_label="France vs England",
        slip_type="single",
        title="France ML",
        legs=_sample_legs(),
        model_probability=0.55,
        fair_decimal_odds=1.82,
        placed_decimal_odds=2.10,
        stake_usd=20.0,
        expected_value_usd=2.5,
        edge=0.155,
        kelly_fraction=0.018,
        qualification_status="BETTABLE",
        qualification_checks=[
            {"label": "Price verified", "pass": True, "detail": "Live odds 2.10"},
        ],
        source="live",
        bookmaker="bet365",
        notes="initial bet",
        created_unix=1_700_000_000,
        placed_unix=1_700_000_000,
    )
    assert bid > 0
    row = s.soccer_bet_get(bid)
    assert row is not None
    assert row["fixture_id"] == "demo-1"
    assert row["status"] == "open"
    assert row["source"] == "live"
    assert row["placed_decimal_odds"] == 2.10
    assert row["qualification_status"] == "BETTABLE"
    # legs round-trip via JSON
    import json as _json
    assert _json.loads(row["legs_json"])[0]["kind"] == "match_result"


def test_soccer_bets_list_filters(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    common = dict(
        legs=_sample_legs(),
        model_probability=0.5, fair_decimal_odds=2.0,
        placed_decimal_odds=2.10, stake_usd=10.0,
        qualification_status="BETTABLE", source="live",
    )
    a = s.record_soccer_bet(fixture_id="A", match_label="A vs B", created_unix=1, placed_unix=1, **common)
    b = s.record_soccer_bet(fixture_id="A", match_label="A vs B", created_unix=2, placed_unix=2, **common)
    c = s.record_soccer_bet(fixture_id="C", match_label="C vs D", created_unix=3, placed_unix=3, **common)
    s.soccer_bet_update(b, status="won", pnl_usd=11.0)

    all_bets = s.soccer_bets_list(status="all")
    assert {row["id"] for row in all_bets} == {a, b, c}

    open_bets = s.soccer_bets_list(status="open")
    assert {row["id"] for row in open_bets} == {a, c}

    fixture_a = s.soccer_bets_list(status="all", fixture_id="A")
    assert {row["id"] for row in fixture_a} == {a, b}

    since_two = s.soccer_bets_list(status="all", since_unix=2)
    assert {row["id"] for row in since_two} == {b, c}


def test_soccer_bet_update_terminal_transition_locks(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    bid = s.record_soccer_bet(
        fixture_id="demo", legs=_sample_legs(),
        model_probability=0.4, fair_decimal_odds=2.5, placed_decimal_odds=2.6,
        stake_usd=10.0, qualification_status="BETTABLE", source="live",
        created_unix=10, placed_unix=10,
    )
    s.soccer_bet_update(bid, status="lost", pnl_usd=-10.0)
    row = s.soccer_bet_get(bid)
    assert row["status"] == "lost"
    assert row["pnl_usd"] == -10.0
    # settled_unix auto-stamped
    assert row["settled_unix"] is not None

    # Cannot un-settle a settled bet
    import pytest
    with pytest.raises(ValueError):
        s.soccer_bet_update(bid, status="open")
    # Cannot bounce between two terminal statuses either
    with pytest.raises(ValueError):
        s.soccer_bet_update(bid, status="won")


def test_soccer_bet_update_recomputes_clv(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    bid = s.record_soccer_bet(
        fixture_id="demo", legs=_sample_legs(),
        model_probability=0.5, fair_decimal_odds=2.0, placed_decimal_odds=2.10,
        stake_usd=10.0, qualification_status="BETTABLE", source="live",
        created_unix=1, placed_unix=1,
    )
    # Closing price tightened to 1.95 — that's positive CLV.
    s.soccer_bet_update(bid, closing_decimal=1.95, closing_source="pinnacle", closing_unix=2)
    row = s.soccer_bet_get(bid)
    # placed implied = 1/2.10 ≈ 0.4762, close implied = 1/1.95 ≈ 0.5128
    # CLV = (close_imp - placed_imp) / placed_imp ≈ +7.69%
    assert row["clv_pct"] is not None
    assert 0.075 < row["clv_pct"] < 0.080


def test_soccer_bets_open_exposure(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    s.record_soccer_bet(
        fixture_id="A", match_label="A vs B", legs=_sample_legs(),
        model_probability=0.5, fair_decimal_odds=2.0, placed_decimal_odds=2.10,
        stake_usd=20.0, qualification_status="BETTABLE", source="live",
        created_unix=1, placed_unix=1,
    )
    s.record_soccer_bet(
        fixture_id="A", match_label="A vs B", legs=_sample_legs(),
        model_probability=0.5, fair_decimal_odds=2.0, placed_decimal_odds=3.0,
        stake_usd=15.0, qualification_status="BETTABLE", source="live",
        created_unix=2, placed_unix=2,
    )
    bid = s.record_soccer_bet(
        fixture_id="C", match_label="C vs D", legs=_sample_legs(),
        model_probability=0.5, fair_decimal_odds=2.0, placed_decimal_odds=2.0,
        stake_usd=5.0, qualification_status="BETTABLE", source="live",
        created_unix=3, placed_unix=3,
    )
    s.soccer_bet_update(bid, status="won", pnl_usd=5.0)
    summary = s.soccer_bets_open_exposure()
    assert summary["open_count"] == 2
    assert summary["open_stake_usd"] == 35.0
    # 20*2.10 + 15*3.0 = 42 + 45 = 87
    assert summary["open_max_return_usd"] == 87.0
    matches = {row["match_label"]: row for row in summary["by_match"]}
    assert matches["A vs B"]["stake_usd"] == 35.0
    settled = s.soccer_bets_settled_summary()
    assert settled["won"] == 1
    assert settled["net_pnl_usd"] == 5.0


def test_record_soccer_bet_validates_inputs(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    import pytest
    with pytest.raises(ValueError):
        s.record_soccer_bet(
            fixture_id="x", legs=_sample_legs(),
            model_probability=0.4, fair_decimal_odds=2.5,
            placed_decimal_odds=0.9,  # invalid
            stake_usd=1.0, qualification_status="BETTABLE", source="live",
            created_unix=1, placed_unix=1,
        )
    with pytest.raises(ValueError):
        s.record_soccer_bet(
            fixture_id="x", legs=_sample_legs(),
            model_probability=0.4, fair_decimal_odds=2.5,
            placed_decimal_odds=2.5, stake_usd=1.0,
            qualification_status="BETTABLE",
            source="bogus",  # invalid
            created_unix=1, placed_unix=1,
        )

# ----------------------------------------------------------------------
# Phase 3 — CLV summary helper
# ----------------------------------------------------------------------
def _parlay_legs():
    return [
        {"kind": "btts", "params": {"side": "Y"}, "label": "BTTS Y"},
        {"kind": "total_goals", "params": {"side": "O", "line": 2.5}, "label": "O2.5"},
    ]


def test_soccer_bets_clv_summary_empty(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    summary = s.soccer_bets_clv_summary()
    assert summary["overall"]["count"] == 0
    assert summary["overall"]["count_with_clv"] == 0
    assert summary["overall"]["avg_clv_pct"] is None
    assert summary["overall"]["positive_clv_share"] is None
    assert summary["by_market"] == []
    assert summary["by_rating_bucket"] == []


def test_soccer_bets_clv_summary_buckets_by_market_and_rating(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    # 1) match_result, model 0.55 -> coinflip, placed 2.10, closing 2.00 -> +CLV
    b1 = s.record_soccer_bet(
        fixture_id="f1", match_label="A vs B", legs=_sample_legs(),
        model_probability=0.55, fair_decimal_odds=1.95,
        placed_decimal_odds=2.10, stake_usd=10.0,
        qualification_status="BETTABLE", source="pasted",
        created_unix=1, placed_unix=1,
    )
    # 2) parlay, model 0.30 -> underdog, placed 3.20, closing 3.50 -> -CLV
    b2 = s.record_soccer_bet(
        fixture_id="f1", match_label="A vs B", legs=_parlay_legs(),
        model_probability=0.30, fair_decimal_odds=3.50,
        placed_decimal_odds=3.20, stake_usd=5.0,
        qualification_status="BETTABLE", source="live", slip_type="parlay",
        created_unix=2, placed_unix=2,
    )
    # 3) match_result, model 0.65 -> favorite, NO closing
    s.record_soccer_bet(
        fixture_id="f2", match_label="C vs D", legs=_sample_legs(),
        model_probability=0.65, fair_decimal_odds=1.55,
        placed_decimal_odds=1.50, stake_usd=20.0,
        qualification_status="BETTABLE", source="manual",
        created_unix=3, placed_unix=3,
    )
    s.soccer_bet_update(b1, closing_decimal=2.00, closing_source="pinnacle")
    s.soccer_bet_update(b2, closing_decimal=3.50, closing_source="pinnacle")

    summary = s.soccer_bets_clv_summary()
    assert summary["overall"]["count"] == 3
    assert summary["overall"]["count_with_clv"] == 2
    assert summary["overall"]["positive_clv_share"] == 0.5
    assert summary["overall"]["avg_clv_pct"] is not None

    by_market = {b["bucket"]: b for b in summary["by_market"]}
    assert "match_result" in by_market
    assert "parlay" in by_market
    assert by_market["match_result"]["count"] == 2
    assert by_market["match_result"]["count_with_clv"] == 1
    # bet #1 placed 2.10 vs closing 2.00 -> implied 0.476 vs 0.500 -> +5%
    assert abs(by_market["match_result"]["avg_clv_pct"] - 0.05) < 1e-9
    # bet #2 placed 3.20 vs closing 3.50 -> implied 0.3125 vs 0.2857 -> -8.57%
    assert by_market["parlay"]["avg_clv_pct"] < 0

    by_rating = {b["bucket"]: b for b in summary["by_rating_bucket"]}
    # coinflip, underdog, favorite buckets all present
    assert {"coinflip", "underdog", "favorite"} <= set(by_rating.keys())
    # favorite has no closing line yet
    assert by_rating["favorite"]["count_with_clv"] == 0
    assert by_rating["favorite"]["avg_clv_pct"] is None
    # coinflip is positive, underdog is negative
    assert by_rating["coinflip"]["positive_clv_share"] == 1.0
    assert by_rating["underdog"]["positive_clv_share"] == 0.0

    by_source = {b["bucket"]: b for b in summary["by_source"]}
    assert by_source["pasted"]["count_with_clv"] == 1
    assert by_source["live"]["count_with_clv"] == 1
    assert by_source["manual"]["count_with_clv"] == 0


def test_soccer_bets_clv_summary_ignores_unparseable_legs(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    s.record_soccer_bet(
        fixture_id="x", match_label="x vs y", legs=[{"kind": "match_result", "params": {}}],
        model_probability=0.5, fair_decimal_odds=2.0, placed_decimal_odds=2.0,
        stake_usd=1.0, qualification_status="BETTABLE", source="live",
        created_unix=1, placed_unix=1,
    )
    # Corrupt the legs_json directly
    with s._conn() as c:
        c.execute("UPDATE soccer_bets SET legs_json = 'not-json'")
    summary = s.soccer_bets_clv_summary()
    by_market = {b["bucket"]: b for b in summary["by_market"]}
    assert "unknown" in by_market
