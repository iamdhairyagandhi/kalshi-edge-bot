"""Tests for the Bet365 paste parser + leg matcher."""

from __future__ import annotations

from src.sports.soccer.parsers.bet365 import parse_bet365_slip
from src.sports.soccer.parsers.leg_matcher import (
    match_parsed_leg_to_betslip,
    match_parsed_slip,
)


# ---------------------------------------------------------------------------
# parser — odds detection
# ---------------------------------------------------------------------------


def test_parse_single_leg_basic():
    text = (
        "Manchester City to Win\n"
        "Match Result\n"
        "1.65\n"
    )
    slip = parse_bet365_slip(text)
    assert len(slip.legs) == 1
    leg = slip.legs[0]
    assert leg.decimal_odds == 1.65
    assert leg.kind == "match_result"
    assert leg.team_hint and "manchester city" in leg.team_hint.lower()
    assert slip.slip_type == "single"


def test_parse_over_under_total_goals():
    text = (
        "Over 2.5\n"
        "Total Goals\n"
        "1.80\n"
    )
    slip = parse_bet365_slip(text)
    assert len(slip.legs) == 1
    leg = slip.legs[0]
    assert leg.kind == "total_goals"
    assert leg.params == {"line": 2.5, "side": "over"}
    assert leg.decimal_odds == 1.80


def test_parse_btts():
    text = (
        "Both Teams To Score - Yes\n"
        "1.75\n"
    )
    slip = parse_bet365_slip(text)
    leg = slip.legs[0]
    assert leg.kind == "btts"
    assert leg.params["side"] == "yes"


def test_parse_correct_score():
    text = (
        "Correct Score 2-1\n"
        "Manchester City vs Liverpool\n"
        "9.00\n"
    )
    slip = parse_bet365_slip(text)
    leg = slip.legs[0]
    assert leg.kind == "correct_score"
    assert leg.params == {"home": 2, "away": 1}


def test_parse_anytime_scorer():
    text = (
        "Erling Haaland - Anytime Goalscorer\n"
        "1.60\n"
    )
    slip = parse_bet365_slip(text)
    leg = slip.legs[0]
    assert leg.kind == "anytime_scorer"
    assert leg.player_hint == "Erling Haaland"
    assert leg.params.get("player_id") == "erling-haaland"


def test_parse_american_odds():
    text = (
        "Manchester City to Win\n"
        "-150\n"
    )
    slip = parse_bet365_slip(text)
    assert slip.legs[0].decimal_odds is not None
    # -150 American → 1 + 100/150 ≈ 1.6667
    assert abs(slip.legs[0].decimal_odds - (1 + 100 / 150)) < 1e-6


def test_parse_fractional_odds():
    text = (
        "Manchester City to Win\n"
        "11/4\n"
    )
    slip = parse_bet365_slip(text)
    assert slip.legs[0].decimal_odds is not None
    assert abs(slip.legs[0].decimal_odds - (1 + 11 / 4)) < 1e-6


# ---------------------------------------------------------------------------
# parser — multi / SGP
# ---------------------------------------------------------------------------


def test_parse_same_game_multi_with_combined_odds():
    text = (
        "Same Game Multi (4)\n\n"
        "Manchester City to Win\n"
        "1.65\n\n"
        "Over 2.5\n"
        "Total Goals\n"
        "1.80\n\n"
        "Both Teams To Score - Yes\n"
        "1.75\n\n"
        "Erling Haaland - Anytime Goalscorer\n"
        "1.60\n\n"
        "Stake: $20.00   Returns: $170.00   Odds: 8.50\n"
    )
    slip = parse_bet365_slip(text)
    assert slip.slip_type == "sgp"
    assert len(slip.legs) == 4
    assert slip.combined_decimal_odds == 8.50
    assert slip.stake == 20.0
    assert slip.returns == 170.0
    kinds = [leg.kind for leg in slip.legs]
    assert kinds == [
        "match_result", "total_goals", "btts", "anytime_scorer",
    ]


def test_parse_multi_falls_back_to_product_of_legs():
    text = (
        "Manchester City to Win\n"
        "2.00\n\n"
        "Over 2.5 Goals\n"
        "2.00\n"
    )
    slip = parse_bet365_slip(text)
    assert slip.slip_type == "multi"
    assert slip.combined_decimal_odds is not None
    assert abs(slip.combined_decimal_odds - 4.0) < 1e-6
    assert any("Combined price" in n for n in slip.notes)


def test_parse_empty_text():
    slip = parse_bet365_slip("")
    assert slip.legs == []
    assert slip.notes and "Empty slip text." in slip.notes[0]


def test_parse_ignores_header_lines():
    text = (
        "Betslip\n"
        "Selections\n"
        "Manchester City to Win\n"
        "1.65\n"
    )
    slip = parse_bet365_slip(text)
    assert len(slip.legs) == 1


# ---------------------------------------------------------------------------
# matcher
# ---------------------------------------------------------------------------


def test_matcher_exact_kind_and_params():
    parsed = parse_bet365_slip("Over 2.5\nTotal Goals\n1.80").legs[0]
    model_legs = [
        {"kind": "match_result", "label": "Home win", "params": {"side": "H"}},
        {"kind": "total_goals", "label": "Over 2.5 goals", "params": {"line": 2.5, "side": "over"}},
    ]
    idx, conf, issues = match_parsed_leg_to_betslip(parsed, model_legs)
    assert idx == 1
    assert conf >= 0.8
    assert issues == []


def test_matcher_returns_none_when_no_match():
    parsed = parse_bet365_slip("Manchester City to Win\nMatch Result\n1.65").legs[0]
    model_legs = [
        {"kind": "total_corners", "label": "Over 9.5 corners", "params": {"line": 9.5, "side": "over"}},
    ]
    idx, conf, issues = match_parsed_leg_to_betslip(parsed, model_legs)
    assert idx is None
    assert conf == 0.0


def test_matcher_greedy_avoids_double_matching():
    text = (
        "Manchester City to Win\n"
        "1.65\n\n"
        "Manchester City - Anytime Goalscorer\n"
        "Erling Haaland - Anytime Goalscorer\n"
        "1.60\n"
    )
    parsed_slip = parse_bet365_slip(text)
    model_legs = [
        {"kind": "anytime_scorer", "label": "Haaland anytime", "params": {"player_id": "erling-haaland"}},
        {"kind": "match_result", "label": "Manchester City win", "params": {"side": "H"}},
    ]
    result = match_parsed_slip(parsed_slip, model_legs)
    # Two legs matched, no duplicates.
    matched_model_indices = [m.matched_model_index for m in result.matches if m.matched_model_index is not None]
    assert len(matched_model_indices) == len(set(matched_model_indices))
    assert result.unmatched_model_legs == []


def test_matcher_flags_unmatched_legs():
    parsed_slip = parse_bet365_slip("Over 9.5\nTotal Corners\n2.10")
    model_legs = [
        {"kind": "match_result", "label": "Home win", "params": {"side": "H"}},
    ]
    result = match_parsed_slip(parsed_slip, model_legs)
    assert result.unmatched_parsed_legs == [0]
    assert result.unmatched_model_legs == [0]
