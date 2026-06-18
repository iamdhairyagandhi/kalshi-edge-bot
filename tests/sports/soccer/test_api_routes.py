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


# ----------------------------------------------------------------------
# Soccer Bet Journal — Phase 2
# ----------------------------------------------------------------------
def _post_bet(client, fid, **overrides):
    body = {
        "fixture_id": fid,
        "legs": [
            {
                "kind": "match_result",
                "label": "Home win",
                "params": {"side": "H"},
            },
        ],
        "model_probability": 0.55,
        "fair_decimal_odds": 1.82,
        "placed_decimal_odds": 2.10,
        "stake_usd": 20.0,
        "qualification_status": "BETTABLE",
        "source": "live",
        "match_label": "Home vs Away",
        "title": "Home ML",
        "slip_type": "single",
        "edge": 0.155,
        "kelly_fraction": 0.018,
        "expected_value_usd": 2.5,
    }
    body.update(overrides)
    return client.post("/api/soccer/bets", json=body)


def test_record_then_list_soccer_bet(client):
    seed = client.post("/api/soccer/seed-demo").json()
    assert seed["fit_source"] == "demo"
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]

    r = _post_bet(client, fid)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["ok"] is True
    bet = created["bet"]
    assert bet["status"] == "open"
    assert bet["fixture_id"] == fid
    assert bet["source"] == "live"

    listing = client.get("/api/soccer/bets").json()
    assert len(listing["bets"]) == 1
    assert listing["bets"][0]["id"] == bet["id"]
    assert listing["exposure"]["open_count"] == 1
    assert listing["exposure"]["open_stake_usd"] == 20.0
    # 20 * 2.10
    assert listing["exposure"]["open_max_return_usd"] == 42.0
    assert listing["settled"]["won"] == 0


def test_record_review_requires_notes(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = _post_bet(client, fid, qualification_status="REVIEW")
    assert r.status_code == 400
    r = _post_bet(client, fid, qualification_status="REVIEW", notes="confirmed manually")
    assert r.status_code == 200


def test_record_rejects_no_bet_status(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = _post_bet(client, fid, qualification_status="NO BET")
    assert r.status_code == 400
    r = _post_bet(client, fid, qualification_status="NEEDS PRICE")
    assert r.status_code == 400


def test_record_rejects_invalid_payload(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    # placed odds <= 1.0 fails Pydantic validation
    r = _post_bet(client, fid, placed_decimal_odds=0.95)
    assert r.status_code == 422
    # empty legs rejected
    r = _post_bet(client, fid, legs=[])
    assert r.status_code == 400
    # unknown source rejected
    r = _post_bet(client, fid, source="napkin")
    assert r.status_code == 400


def test_patch_bet_marks_won_and_records_pnl(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bet = _post_bet(client, fid).json()["bet"]
    bid = bet["id"]
    r = client.patch(f"/api/soccer/bets/{bid}", json={"status": "won"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "won"
    # 20 * (2.10 - 1) = 22.0 PnL
    assert abs(body["pnl_usd"] - 22.0) < 1e-6
    assert body["settled_unix"] is not None

    listing = client.get("/api/soccer/bets").json()
    assert listing["exposure"]["open_count"] == 0
    assert listing["settled"]["won"] == 1
    assert abs(listing["settled"]["net_pnl_usd"] - 22.0) < 1e-6


def test_patch_bet_terminal_lock(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bid = _post_bet(client, fid).json()["bet"]["id"]
    client.patch(f"/api/soccer/bets/{bid}", json={"status": "lost"})
    # Cannot reopen a lost bet
    r = client.patch(f"/api/soccer/bets/{bid}", json={"status": "open"})
    assert r.status_code == 400
    # And cannot bounce to a different terminal
    r = client.patch(f"/api/soccer/bets/{bid}", json={"status": "won"})
    assert r.status_code == 400


def test_patch_bet_cashed_out_uses_actual_return(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bid = _post_bet(client, fid).json()["bet"]["id"]
    r = client.patch(
        f"/api/soccer/bets/{bid}",
        json={"status": "cashed_out", "actual_return_usd": 26.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cashed_out"
    # 26.5 - 20.0 = 6.5
    assert abs(body["pnl_usd"] - 6.5) < 1e-6


def test_patch_bet_closing_decimal_recomputes_clv(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bid = _post_bet(client, fid).json()["bet"]["id"]
    r = client.patch(
        f"/api/soccer/bets/{bid}",
        json={"closing_decimal": 1.95, "closing_source": "pinnacle", "closing_unix": 1_700_000_500},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["clv_pct"] is not None
    assert 0.075 < body["clv_pct"] < 0.080


def test_resolve_auto_grades_open_bets(client):
    """End-to-end: seed -> place a home_win bet -> resolve 2-1 -> bet is
    auto-marked won with correct PnL."""
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bid = _post_bet(client, fid).json()["bet"]["id"]

    r = client.post(
        f"/api/soccer/fixtures/{fid}/resolve",
        json={"home_goals": 2, "away_goals": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["soccer_bets_graded"] == 1
    assert body["soccer_bets_skipped"] == 0

    listing = client.get("/api/soccer/bets").json()
    bet = listing["bets"][0]
    assert bet["id"] == bid
    assert bet["status"] == "won"
    assert abs(bet["pnl_usd"] - 22.0) < 1e-6
    assert bet["settled_unix"] is not None


def test_resolve_skips_open_bets_with_unsupported_legs(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    # Bet on an anytime_scorer leg that we can't grade from the score.
    # Pass lineup_confirmed=True to bypass the Phase 10 player-prop guard;
    # this test is about the resolution loop, not the gate.
    r = _post_bet(client, fid, legs=[
        {"kind": "anytime_scorer", "label": "Kane to score", "params": {"player_id": "kane"}},
    ], lineup_confirmed=True)
    assert r.status_code == 200, r.text

    r = client.post(
        f"/api/soccer/fixtures/{fid}/resolve",
        json={"home_goals": 2, "away_goals": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["soccer_bets_graded"] == 0
    assert body["soccer_bets_skipped"] == 1

    bet = client.get("/api/soccer/bets").json()["bets"][0]
    assert bet["status"] == "open"


def test_list_soccer_bets_status_filter(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    a = _post_bet(client, fid).json()["bet"]["id"]
    b = _post_bet(client, fid).json()["bet"]["id"]
    client.patch(f"/api/soccer/bets/{a}", json={"status": "lost"})

    open_only = client.get("/api/soccer/bets", params={"status": "open"}).json()
    assert {row["id"] for row in open_only["bets"]} == {b}

    all_bets = client.get("/api/soccer/bets", params={"status": "all"}).json()
    assert {row["id"] for row in all_bets["bets"]} == {a, b}


def test_patch_unknown_bet_returns_404(client):
    client.post("/api/soccer/seed-demo")
    r = client.patch("/api/soccer/bets/9999", json={"status": "won"})
    assert r.status_code == 404



# ---------------------------------------------------------------------------
# Phase 9 — Smart Bet Builder & Correlation Engine
# ---------------------------------------------------------------------------


def test_bet_builder_returns_phase9_fields(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = client.post("/api/soccer/bet-builder", json={
        "fixture_id": fid,
        "legs": [
            {"kind": "match_result", "params": {"side": "H"}, "label": "Home"},
            {"kind": "btts", "params": {"side": "yes"}, "label": "BTTS"},
        ],
        "n_sims": 2000,
        "book_decimal_odds": 4.0,
        "source": "pasted",
        "lineup_confirmed": True,
        "same_game": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Phase 9 fields present.
    assert "book_implied_probability" in body
    assert body["book_implied_probability"] is not None
    assert "correlation_tax" in body
    assert "parlay_rules" in body
    assert isinstance(body["parlay_rules"], list)
    assert any(r["rule"] == "sgp_needs_combined_price" for r in body["parlay_rules"])
    sgp_rule = next(r for r in body["parlay_rules"] if r["rule"] == "sgp_needs_combined_price")
    assert sgp_rule["passed"] is True  # we passed pasted+book_odds
    assert "failure_modes" in body
    assert "leg_failure_rates" in body
    assert len(body["leg_failure_rates"]) == 2


def test_bet_builder_flags_sgp_without_book_price(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = client.post("/api/soccer/bet-builder", json={
        "fixture_id": fid,
        "legs": [
            {"kind": "match_result", "params": {"side": "H"}},
            {"kind": "btts", "params": {"side": "yes"}},
        ],
        "n_sims": 1000,
        "source": "none",
    })
    assert r.status_code == 200
    body = r.json()
    sgp = next(r for r in body["parlay_rules"] if r["rule"] == "sgp_needs_combined_price")
    assert sgp["passed"] is False
    assert sgp["severity"] == "hard"
    assert body["parlay_rules_hard_fail"] is True


def test_bet_builder_flags_duplicate_exposure(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = client.post("/api/soccer/bet-builder", json={
        "fixture_id": fid,
        "legs": [
            {"kind": "match_result", "params": {"side": "H"}, "label": "Home"},
            {"kind": "match_result", "params": {"side": "H"}, "label": "Home 2"},
        ],
        "n_sims": 1000,
        "book_decimal_odds": 2.0,
        "source": "pasted",
    })
    assert r.status_code == 200
    body = r.json()
    dup = next(r for r in body["parlay_rules"] if r["rule"] == "no_duplicate_exposure")
    assert dup["passed"] is False
    assert body["duplicate_exposure_groups"] == [[0, 1]]


# ---------------------------------------------------------------------------
# Phase 6 — Bet365 paste workflow
# ---------------------------------------------------------------------------


def test_paste_bet365_parse_only(client):
    """No fixture_id / no model_legs → parser-only response."""
    text = (
        "Manchester City to Win\n"
        "Match Result\n"
        "1.65\n\n"
        "Over 2.5\n"
        "Total Goals\n"
        "1.80\n"
    )
    r = client.post("/api/soccer/paste/bet365", json={"slip_text": text})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["parsed"]["legs"]) == 2
    kinds = [leg["kind"] for leg in body["parsed"]["legs"]]
    assert kinds == ["match_result", "total_goals"]
    assert body["parsed"]["slip_type"] == "multi"
    assert body["matches"] == []
    assert body["reprice"] is None


def test_paste_bet365_matches_supplied_model_legs(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    text = (
        "Same Game Multi (2)\n\n"
        "Home Team to Win\n"
        "Match Result\n"
        "2.10\n\n"
        "Over 2.5\n"
        "Total Goals\n"
        "1.90\n\n"
        "Stake: $20.00   Returns: $80.00   Odds: 4.00\n"
    )
    r = client.post("/api/soccer/paste/bet365", json={
        "slip_text": text,
        "fixture_id": fid,
        "model_legs": [
            {"kind": "match_result", "label": "Home win", "params": {"side": "H"}},
            {"kind": "total_goals", "label": "Over 2.5 goals", "params": {"line": 2.5, "side": "over"}},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched_legs_count"] == 2
    assert body["overall_match_confidence"] > 0.5
    assert body["unmatched_parsed_legs"] == []
    assert body["unmatched_model_legs"] == []
    assert body["reprice"] is not None
    rep = body["reprice"]
    # Reprice carries Phase 9 metadata.
    assert "parlay_rules" in rep
    assert "correlation_tax" in rep
    # The combined book price was pasted, so SGP rule passes.
    sgp = next(rule for rule in rep["parlay_rules"] if rule["rule"] == "sgp_needs_combined_price")
    assert sgp["passed"] is True


def test_paste_bet365_flags_unmatched_pasted_leg(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    text = (
        "Over 9.5\n"
        "Total Corners\n"
        "2.10\n"
    )
    r = client.post("/api/soccer/paste/bet365", json={
        "slip_text": text,
        "fixture_id": fid,
        "model_legs": [
            {"kind": "match_result", "label": "Home win", "params": {"side": "H"}},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unmatched_parsed_legs"] == [0]
    assert body["unmatched_model_legs"] == [0]
    assert any("had no model match" in iss for iss in body["gate_blocking_issues"])
    assert body["reprice"] is None


def test_paste_bet365_reprice_handles_missing_fixture(client):
    """If the engine isn't ready, the API still returns parsed legs without
    crashing — the user can still see what we picked up."""
    r = client.post("/api/soccer/paste/bet365", json={
        "slip_text": "Manchester City to Win\n1.65\n",
        "fixture_id": "does-not-exist",
        "model_legs": [
            {"kind": "match_result", "label": "Home win", "params": {"side": "H"}},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # No reprice but parsed data + matches still present.
    assert body["reprice"] is None
    assert body["reprice_error"] in {"engine_not_initialized", "fixture_not_found"}
    assert len(body["parsed"]["legs"]) == 1


# ----------------------------------------------------------------------
# Phase 3 — Closing-line value tracking
# ----------------------------------------------------------------------
def test_snapshot_closing_attaches_clv(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = _post_bet(client, fid)
    assert r.status_code == 200
    bid = r.json()["bet"]["id"]

    # placed @ 2.10, closing @ 2.00 -> placed was BETTER -> +5% CLV
    r = client.post(
        f"/api/soccer/bets/{bid}/snapshot-closing",
        json={"closing_decimal": 2.00, "closing_source": "pinnacle"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["closing_decimal"] == 2.00
    assert body["closing_source"] == "pinnacle"
    assert body["closing_unix"] is not None
    assert body["clv_pct"] is not None
    assert abs(body["clv_pct"] - 0.05) < 1e-9


def test_snapshot_closing_rejects_unknown_bet(client):
    r = client.post(
        "/api/soccer/bets/9999/snapshot-closing",
        json={"closing_decimal": 1.95},
    )
    assert r.status_code == 404


def test_snapshot_closing_rejects_bad_odds(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bid = _post_bet(client, fid).json()["bet"]["id"]
    r = client.post(
        f"/api/soccer/bets/{bid}/snapshot-closing",
        json={"closing_decimal": 1.0},
    )
    assert r.status_code == 422


def test_clv_summary_lists_buckets_and_appears_on_list(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]

    # Two bets, one +CLV one -CLV
    b1 = _post_bet(client, fid).json()["bet"]["id"]
    # The parlay leg combo is fine but Phase 10 demands a combined book
    # price for SGPs — supply one.
    b2 = _post_bet(
        client, fid,
        legs=[
            {"kind": "btts", "params": {"side": "Y"}, "label": "BTTS Y"},
            {"kind": "total_goals", "params": {"side": "O", "line": 2.5}, "label": "O2.5"},
        ],
        slip_type="parlay",
        placed_decimal_odds=3.20,
        book_decimal_odds=3.20,
        source="pasted",
        model_probability=0.30,
    ).json()["bet"]["id"]

    client.post(
        f"/api/soccer/bets/{b1}/snapshot-closing",
        json={"closing_decimal": 2.00, "closing_source": "pinnacle"},
    )
    client.post(
        f"/api/soccer/bets/{b2}/snapshot-closing",
        json={"closing_decimal": 3.50, "closing_source": "pinnacle"},
    )

    # standalone summary endpoint
    summary = client.get("/api/soccer/bets/clv-summary").json()
    assert summary["overall"]["count"] == 2
    assert summary["overall"]["count_with_clv"] == 2
    assert summary["overall"]["positive_clv_share"] == 0.5
    by_market = {b["bucket"]: b for b in summary["by_market"]}
    assert "match_result" in by_market
    assert "parlay" in by_market

    # also present on /soccer/bets
    listing = client.get("/api/soccer/bets").json()
    assert listing["clv_summary"] is not None
    assert listing["clv_summary"]["overall"]["count_with_clv"] == 2


# ----------------------------------------------------------------------
# Phase 10 — Guardrails
# ----------------------------------------------------------------------
def test_record_bet_blocked_by_min_odds_without_force(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = _post_bet(client, fid, placed_decimal_odds=1.20)
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "guardrails_blocked"
    rules = {c["rule"] for c in body["guardrails"]["checks"] if not c["passed"]}
    assert "min_decimal_odds" in rules
    assert body["guardrails"]["hard_fail_count"] >= 1


def test_record_bet_allowed_with_force(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = _post_bet(client, fid, placed_decimal_odds=1.20, force=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["guardrails"] is not None
    assert body["guardrails"]["hard_fail_count"] >= 1
    # The block reason must still be visible to the user via the
    # persisted qualification snapshot.
    qchecks = body["bet"]["qualification_checks"] or []
    qrules = {str(c.get("rule") or c.get("label")) for c in qchecks}
    assert any("min_decimal_odds" in r for r in qrules)


def test_record_bet_player_prop_requires_lineup(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    legs = [{"kind": "anytime_scorer", "label": "Kane AGS", "params": {"player_id": "kane"}}]
    # Without lineup_confirmed -> blocked
    r = _post_bet(client, fid, legs=legs)
    assert r.status_code == 422
    rules = {c["rule"] for c in r.json()["guardrails"]["checks"] if not c["passed"]}
    assert "player_prop_lineup" in rules
    # With lineup_confirmed -> allowed
    r = _post_bet(client, fid, legs=legs, lineup_confirmed=True)
    assert r.status_code == 200, r.text


def test_record_bet_sgp_requires_combined_price(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    legs = [
        {"kind": "btts", "params": {"side": "Y"}, "label": "BTTS Y"},
        {"kind": "total_goals", "params": {"side": "O", "line": 2.5}, "label": "O2.5"},
    ]
    # Manual source, no book_decimal_odds -> blocked.
    r = _post_bet(client, fid, legs=legs, source="manual", placed_decimal_odds=3.20, slip_type="parlay")
    assert r.status_code == 422
    rules = {c["rule"] for c in r.json()["guardrails"]["checks"] if not c["passed"]}
    assert "same_game_needs_price" in rules


def test_record_bet_fixture_exposure_cap(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    # Default cap = $100. Two $40 bets pass, third pushes total to $120.
    for _ in range(2):
        r = _post_bet(client, fid, stake_usd=40.0)
        assert r.status_code == 200, r.text
    r = _post_bet(client, fid, stake_usd=40.0)
    assert r.status_code == 422
    rules = {c["rule"] for c in r.json()["guardrails"]["checks"] if not c["passed"]}
    assert "fixture_exposure_cap" in rules


def test_guardrails_preview_returns_same_shape(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    body = {
        "fixture_id": fid,
        "legs": [{"kind": "match_result", "label": "Home", "params": {"side": "H"}}],
        "placed_decimal_odds": 1.20,
        "stake_usd": 10.0,
        "source": "live",
        "edge": 0.05,
        "same_game": False,
    }
    r = client.post("/api/soccer/guardrails/preview", json=body)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["allowed"] is False
    rules = {c["rule"] for c in rep["checks"] if not c["passed"]}
    assert "min_decimal_odds" in rules


def test_guardrails_preview_allows_clean_bet(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    body = {
        "fixture_id": fid,
        "legs": [{"kind": "match_result", "label": "Home", "params": {"side": "H"}}],
        "placed_decimal_odds": 2.10,
        "stake_usd": 10.0,
        "source": "live",
        "edge": 0.05,
        "same_game": False,
    }
    r = client.post("/api/soccer/guardrails/preview", json=body)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["allowed"] is True
    assert rep["hard_fail_count"] == 0


def test_recorded_bet_returns_guardrails_block_audit(client):
    """When the bet passes guardrails, the guardrails block on the
    response shows the rules that ran (all passed)."""
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    r = _post_bet(client, fid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["guardrails"] is not None
    assert body["guardrails"]["allowed"] is True
    # Every persisted bet has at least the stake_positive + min_odds + cap checks.
    rules = {c["rule"] for c in body["guardrails"]["checks"]}
    assert {"stake_positive", "min_decimal_odds", "fixture_exposure_cap"} <= rules


# ----------------------------------------------------------------------
# Phase 4 — Calibration summary
# ----------------------------------------------------------------------
def test_calibration_summary_empty(client):
    client.post("/api/soccer/seed-demo")
    r = client.get("/api/soccer/bets/calibration-summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall"]["count"] == 0
    assert body["overall"]["hit_rate"] is None
    assert body["by_market"] == []
    assert len(body["reliability_curve"]) == 10
    assert {p["bucket"] for p in body["reliability_curve"]} == {
        "p_00_10","p_10_20","p_20_30","p_30_40","p_40_50",
        "p_50_60","p_60_70","p_70_80","p_80_90","p_90_100",
    }


def test_calibration_summary_with_settled_bets(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    a = _post_bet(client, fid, model_probability=0.55, placed_decimal_odds=2.10).json()["bet"]["id"]
    b = _post_bet(client, fid, model_probability=0.45, placed_decimal_odds=2.40, slip_type="value").json()["bet"]["id"]
    # We need open bets to be settled. Force-settle the second bet so we
    # don't trip the per-fixture exposure cap on b's record (default 100):
    # _post_bet stakes 20, so a + b open exposure = 40 is well under.
    client.patch(f"/api/soccer/bets/{a}", json={"status": "won"})
    client.patch(f"/api/soccer/bets/{b}", json={"status": "lost"})

    body = client.get("/api/soccer/bets/calibration-summary").json()
    assert body["overall"]["count"] == 2
    assert body["overall"]["won"] == 1
    assert body["overall"]["hit_rate"] == 0.5
    assert body["overall"]["brier"] is not None
    assert body["overall"]["log_loss"] is not None
    assert body["overall"]["roi"] is not None

    by_market = {row["bucket"]: row for row in body["by_market"]}
    assert "match_result" in by_market
    assert by_market["match_result"]["count"] == 2

    by_odds = {row["bucket"]: row for row in body["by_odds_bucket"]}
    # 2.10, 2.40 -> 2_3 bucket
    assert by_odds["2_3"]["count"] == 2

    by_qual = {row["bucket"]: row for row in body["by_qualification_status"]}
    assert by_qual["BETTABLE"]["count"] == 2

    # Reliability curve has the two predictions in their respective deciles.
    by_curve = {p["bucket"]: p for p in body["reliability_curve"]}
    assert by_curve["p_50_60"]["count"] == 1
    assert by_curve["p_40_50"]["count"] == 1


def test_calibration_summary_appears_on_bets_list(client):
    client.post("/api/soccer/seed-demo")
    fid = client.get("/api/soccer/fixtures").json()[0]["fixture_id"]
    bid = _post_bet(client, fid).json()["bet"]["id"]
    client.patch(f"/api/soccer/bets/{bid}", json={"status": "won"})

    listing = client.get("/api/soccer/bets").json()
    assert listing["calibration_summary"] is not None
    assert listing["calibration_summary"]["overall"]["count"] == 1
    assert listing["calibration_summary"]["overall"]["won"] == 1
