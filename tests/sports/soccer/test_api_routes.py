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
    # Force the module to reload settings; importlib trick
    import importlib
    from src import config as cfg_mod
    importlib.reload(cfg_mod)
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
