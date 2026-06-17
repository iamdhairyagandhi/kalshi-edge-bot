"""Tests for the soccer FastAPI route."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Use temp DB so tests are hermetic.
    monkeypatch.setenv("SOCCER_DB_PATH", str(tmp_path / "soccer.db"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    monkeypatch.setenv("CALIBRATION_DB_PATH", str(tmp_path / "calibration.db"))
    # Reload settings AND the routes module that imported settings into
    # its module namespace — otherwise every test reuses the first
    # test's DB path, which makes the resolution-loop tests cross-talk.
    import importlib
    from src import config as cfg_mod
    importlib.reload(cfg_mod)
    from dashboard_v2.api.routes import soccer as soccer_routes_mod
    importlib.reload(soccer_routes_mod)
    from dashboard_v2.api import main as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.app)


def test_seed_demo_then_fixtures_then_match_then_bet_builder(client):
    r = client.post("/api/soccer/seed-demo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["fit_source"] == "demo"
    assert body["teams"] >= 4
    assert body["fixtures"] >= 1

    r = client.get("/api/soccer/fixtures")
    assert r.status_code == 200
    fixtures = r.json()
    assert fixtures, "expected at least one fixture"
    fid = fixtures[0]["fixture_id"]
    # outcome probs are populated
    assert fixtures[0]["home_win"] is not None
    assert fixtures[0]["draw"] is not None
    assert 0.95 < (fixtures[0]["home_win"] + fixtures[0]["draw"] + fixtures[0]["away_win"]) <= 1.0001

    r = client.get(f"/api/soccer/match/{fid}", params={"n_sims": 2000})
    assert r.status_code == 200
    m = r.json()
    assert len(m["score_matrix"]) >= 7  # at least 7 rows
    assert m["top_scorer_probs"]
    assert "over_2.5" in m["cards_distribution"]

    r = client.post("/api/soccer/bet-builder", json={
        "fixture_id": fid,
        "legs": [
            {"kind": "match_result", "params": {"side": "H"}},
            {"kind": "btts", "params": {"side": "yes"}},
        ],
        "n_sims": 2000,
    })
    assert r.status_code == 200
    q = r.json()
    assert 0.0 <= q["fair_probability"] <= 1.0
    assert q["fair_decimal_odds"] >= 1.0
    assert len(q["leg_probabilities"]) == 2


def test_match_404_on_unknown_fixture(client):
    client.post("/api/soccer/seed-demo")
    r = client.get("/api/soccer/match/does-not-exist")
    assert r.status_code == 404


def test_bet_builder_409_when_engine_uninitialized(client):
    r = client.post("/api/soccer/bet-builder", json={
        "fixture_id": "demo-fra-eng",
        "legs": [{"kind": "btts", "params": {"side": "yes"}}],
    })
    assert r.status_code == 409


def test_betslips_today_horizon_filters_out_far_future_fixtures(client):
    """A small horizon (1h) should filter out the demo fixtures (which are
    seeded ~3 days out) entirely, leaving 0 slips. With no horizon (or a
    large horizon) the same call returns the same demo fixtures."""
    seed = client.post("/api/soccer/seed-demo").json()
    assert seed["fit_source"] == "demo"

    r = client.get("/api/soccer/betslips", params={"max_slips": 5, "horizon_hours": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["slips"] == []
    assert any("horizon=1h" in n for n in body["notes"]), body["notes"]

    r = client.get("/api/soccer/betslips", params={"max_slips": 5, "horizon_hours": 24 * 7})
    assert r.status_code == 200
    week = r.json()
    # demo fixtures exist a few days out; week horizon should keep them.
    assert any("horizon=" in n for n in week["notes"])

    r = client.get("/api/soccer/betslips", params={"max_slips": 5})
    assert r.status_code == 200
    full = r.json()
    # No horizon param -> no horizon note.
    assert not any(n.startswith("horizon=") for n in full["notes"])


def test_record_prediction_then_resolve_grades_correctly(client):
    """End-to-end resolution loop: seed -> record three predictions on a
    fixture -> resolve with a known scoreline -> verify outcomes were
    written correctly and the calibration layer was refit."""
    seed = client.post("/api/soccer/seed-demo").json()
    assert seed["fit_source"] == "demo"

    fixtures = client.get("/api/soccer/fixtures").json()
    assert fixtures, "expected at least one demo fixture"
    fid = fixtures[0]["fixture_id"]

    # Record three predictions: home_win, over_2_5, btts_yes.
    picks = [
        {"market_type": "home_win", "leg": {"kind": "match_result", "params": {"side": "H"}}},
        {"market_type": "over_2_5", "leg": {"kind": "total_goals", "params": {"line": 2.5, "side": "over"}}},
        {"market_type": "btts_yes", "leg": {"kind": "btts", "params": {"side": "yes"}}},
    ]
    for p in picks:
        r = client.post("/api/soccer/predictions", json={
            "fixture_id": fid,
            "market_type": p["market_type"],
            "leg": p["leg"],
            "fair_probability": 0.5,
            "fair_decimal_odds": 2.0,
            "book_decimal_odds": 2.10,
            "edge": 0.05,
            "kelly_fraction": 0.02,
            "recommendation": "bet",
        })
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    # Resolve fixture 2-1: home_win=HIT, over_2_5=HIT (3 goals), btts_yes=HIT.
    r = client.post(
        f"/api/soccer/fixtures/{fid}/resolve",
        json={"home_goals": 2, "away_goals": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["predictions_graded"] == 3
    assert body["bet_builder_graded"] == 0
    # All three are score-derivable, so nothing skipped.
    assert body["skipped_unsupported_market"] == 0

    # Calibration endpoint should now return resolved samples for each
    # market_type. Brier of a perfectly hit p=0.5 prediction is 0.25.
    cal = client.get("/api/soccer/calibration").json()
    seen = {row["market_type"]: row["n_resolved"] for row in cal}
    assert seen.get("home_win") == 1
    assert seen.get("over_2_5") == 1
    assert seen.get("btts_yes") == 1


def test_resolve_skips_unsupported_player_markets(client):
    """Player props (anytime_scorer, cards) can't be graded from the
    final score alone — they should be skipped, not silently graded 0."""
    seed = client.post("/api/soccer/seed-demo").json()
    assert seed["fit_source"] == "demo"

    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]

    # Record one supported and one unsupported pick.
    client.post("/api/soccer/predictions", json={
        "fixture_id": fid,
        "market_type": "home_win",
        "leg": {"kind": "match_result", "params": {"side": "H"}},
        "fair_probability": 0.5, "fair_decimal_odds": 2.0,
    })
    client.post("/api/soccer/predictions", json={
        "fixture_id": fid,
        "market_type": "anytime_scorer",
        "leg": {"kind": "anytime_scorer", "params": {"player_id": "kane"}},
        "fair_probability": 0.30, "fair_decimal_odds": 3.33,
    })

    r = client.post(
        f"/api/soccer/fixtures/{fid}/resolve",
        json={"home_goals": 2, "away_goals": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["predictions_graded"] == 1
    assert body["skipped_unsupported_market"] == 1


def test_resolve_unknown_fixture_404(client):
    client.post("/api/soccer/seed-demo")
    r = client.post(
        "/api/soccer/fixtures/does-not-exist/resolve",
        json={"home_goals": 1, "away_goals": 0},
    )
    assert r.status_code == 404


def test_resolve_rejects_negative_goals(client):
    seed = client.post("/api/soccer/seed-demo").json()
    assert seed["fit_source"] == "demo"
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = client.post(
        f"/api/soccer/fixtures/{fid}/resolve",
        json={"home_goals": -1, "away_goals": 0},
    )
    assert r.status_code == 400

