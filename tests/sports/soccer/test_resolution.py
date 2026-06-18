"""Tests for the resolution loop helpers in
dashboard_v2.api.routes.soccer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from dashboard_v2.api.routes.soccer import (  # noqa: E402
    _grade_market_type_from_score,
    _resolve_leg,
)


# ----------------------------------------------------------------------
# match_result
# ----------------------------------------------------------------------
def test_resolve_match_result_home_wins():
    assert _resolve_leg({"kind": "match_result", "params": {"side": "H"}}, 2, 1) == 1
    assert _resolve_leg({"kind": "match_result", "params": {"side": "A"}}, 2, 1) == 0
    assert _resolve_leg({"kind": "match_result", "params": {"side": "D"}}, 2, 1) == 0


def test_resolve_match_result_draw():
    assert _resolve_leg({"kind": "match_result", "params": {"side": "D"}}, 1, 1) == 1
    assert _resolve_leg({"kind": "match_result", "params": {"side": "H"}}, 1, 1) == 0


def test_resolve_match_result_away_wins():
    assert _resolve_leg({"kind": "match_result", "params": {"side": "A"}}, 0, 2) == 1


# ----------------------------------------------------------------------
# total_goals
# ----------------------------------------------------------------------
def test_resolve_total_goals_over():
    assert _resolve_leg({"kind": "total_goals", "params": {"line": 2.5, "side": "over"}}, 2, 1) == 1
    assert _resolve_leg({"kind": "total_goals", "params": {"line": 2.5, "side": "over"}}, 1, 1) == 0


def test_resolve_total_goals_under():
    assert _resolve_leg({"kind": "total_goals", "params": {"line": 2.5, "side": "under"}}, 1, 1) == 1
    assert _resolve_leg({"kind": "total_goals", "params": {"line": 2.5, "side": "under"}}, 2, 1) == 0


def test_resolve_total_goals_exact_line_returns_none():
    # 2.5 line can never push, but integer line CAN — verify push -> None.
    assert _resolve_leg({"kind": "total_goals", "params": {"line": 3, "side": "over"}}, 2, 1) is None


# ----------------------------------------------------------------------
# btts
# ----------------------------------------------------------------------
def test_resolve_btts_yes():
    assert _resolve_leg({"kind": "btts", "params": {"side": "yes"}}, 1, 1) == 1
    assert _resolve_leg({"kind": "btts", "params": {"side": "yes"}}, 2, 0) == 0


def test_resolve_btts_no():
    assert _resolve_leg({"kind": "btts", "params": {"side": "no"}}, 2, 0) == 1
    assert _resolve_leg({"kind": "btts", "params": {"side": "no"}}, 1, 1) == 0


# ----------------------------------------------------------------------
# team_total
# ----------------------------------------------------------------------
def test_resolve_team_total_home_over():
    leg = {"kind": "team_total", "params": {"team": "home", "line": 1.5, "side": "over"}}
    assert _resolve_leg(leg, 2, 0) == 1
    assert _resolve_leg(leg, 1, 3) == 0


def test_resolve_team_total_away_under():
    leg = {"kind": "team_total", "params": {"team": "away", "line": 1.5, "side": "under"}}
    assert _resolve_leg(leg, 5, 1) == 1
    assert _resolve_leg(leg, 5, 2) == 0


# ----------------------------------------------------------------------
# correct_score
# ----------------------------------------------------------------------
def test_resolve_correct_score():
    leg = {"kind": "correct_score", "params": {"home": 2, "away": 1}}
    assert _resolve_leg(leg, 2, 1) == 1
    assert _resolve_leg(leg, 1, 2) == 0
    assert _resolve_leg(leg, 2, 2) == 0


# ----------------------------------------------------------------------
# unsupported markets
# ----------------------------------------------------------------------
def test_resolve_anytime_scorer_returns_none_without_stats():
    assert _resolve_leg(
        {"kind": "anytime_scorer", "params": {"player_id": "kane"}}, 3, 2,
    ) is None


def test_resolve_total_cards_returns_none_without_stats():
    assert _resolve_leg(
        {"kind": "total_cards", "params": {"line": 4.5, "side": "over"}}, 1, 1,
    ) is None


def test_resolve_unknown_kind_returns_none():
    assert _resolve_leg({"kind": "made_up_market", "params": {}}, 1, 1) is None


def test_resolve_malformed_params_returns_none():
    assert _resolve_leg({"kind": "total_goals", "params": {"line": "abc", "side": "over"}}, 1, 1) is None
    assert _resolve_leg({"kind": "correct_score", "params": {"home": "x", "away": 1}}, 1, 1) is None


# ----------------------------------------------------------------------
# canonical market_type strings (the path the resolve endpoint uses for
# persisted predictions)
# ----------------------------------------------------------------------
def test_grade_market_type_from_score_home_win():
    assert _grade_market_type_from_score("home_win", {}, 2, 1) == 1
    assert _grade_market_type_from_score("home_win", {}, 1, 1) == 0


def test_grade_market_type_from_score_draw():
    assert _grade_market_type_from_score("draw", {}, 1, 1) == 1
    assert _grade_market_type_from_score("draw", {}, 2, 1) == 0


def test_grade_market_type_from_score_over_2_5():
    assert _grade_market_type_from_score("over_2_5", {}, 2, 1) == 1
    assert _grade_market_type_from_score("over_2_5", {}, 1, 1) == 0


def test_grade_market_type_from_score_under_2_5():
    assert _grade_market_type_from_score("under_2_5", {}, 1, 1) == 1
    assert _grade_market_type_from_score("under_2_5", {}, 2, 1) == 0


def test_grade_market_type_from_score_btts_yes():
    assert _grade_market_type_from_score("btts_yes", {}, 1, 1) == 1
    assert _grade_market_type_from_score("btts_yes", {}, 2, 0) == 0


def test_grade_market_type_from_score_falls_back_to_leg():
    # market_type not canonical -> fall back to grading the stored leg.
    leg = {"kind": "team_total", "params": {"team": "home", "line": 1.5, "side": "over"}}
    assert _grade_market_type_from_score("home_team_over_1_5", leg, 2, 0) == 1
