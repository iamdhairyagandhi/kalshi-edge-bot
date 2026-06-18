"""Tests for the Phase 9 correlation engine."""

from __future__ import annotations

from typing import List

from src.sports.soccer.simulator.correlation import (
    build_correlation_report,
    compute_correlation_tax,
    detect_duplicate_exposure,
    explain_failure_modes,
    parlay_rule_check,
    rules_pass,
)
from src.sports.soccer.types import BetLeg, MatchSim


def _sim(home: int, away: int, **kwargs) -> MatchSim:
    return MatchSim(home_goals=home, away_goals=away, **kwargs)


# ---------------------------------------------------------------------------
# correlation tax
# ---------------------------------------------------------------------------


def test_correlation_tax_positive_when_book_underprices_joint():
    book_imp, tax, tax_pct = compute_correlation_tax(
        joint_probability=0.30,
        book_decimal_odds=2.50,  # implied 40%
    )
    assert book_imp is not None
    assert abs(book_imp - 0.4) < 1e-9
    assert abs(tax - 0.10) < 1e-9
    assert tax_pct is not None
    assert abs(tax_pct - (0.10 / 0.30)) < 1e-9


def test_correlation_tax_none_without_book():
    book_imp, tax, pct = compute_correlation_tax(
        joint_probability=0.42,
        book_decimal_odds=None,
    )
    assert book_imp is None
    assert tax is None
    assert pct is None


def test_correlation_tax_rejects_garbage_book_odds():
    book_imp, tax, pct = compute_correlation_tax(
        joint_probability=0.42,
        book_decimal_odds=0.5,
    )
    assert book_imp is None and tax is None and pct is None


# ---------------------------------------------------------------------------
# duplicate exposure
# ---------------------------------------------------------------------------


def test_duplicate_exposure_detects_same_outcome():
    legs = [
        BetLeg("match_result", {"side": "H"}, label="Home"),
        BetLeg("match_result", {"side": "H"}, label="Home #2"),
        BetLeg("total_goals", {"line": 2.5, "side": "over"}, label="O 2.5"),
    ]
    groups = detect_duplicate_exposure(legs)
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1]


def test_duplicate_exposure_ignores_different_lines():
    legs = [
        BetLeg("total_goals", {"line": 2.5, "side": "over"}),
        BetLeg("total_goals", {"line": 3.5, "side": "over"}),
    ]
    assert detect_duplicate_exposure(legs) == []


def test_duplicate_exposure_groups_player_legs():
    legs = [
        BetLeg("anytime_scorer", {"player_id": "kane"}),
        BetLeg("first_scorer", {"player_id": "kane"}),
        BetLeg("anytime_scorer", {"player_id": "kane"}, label="Kane 2"),
    ]
    groups = detect_duplicate_exposure(legs)
    # anytime/first are different kinds so different keys; two anytimes group.
    assert any(sorted(g) == [0, 2] for g in groups)


# ---------------------------------------------------------------------------
# parlay rules
# ---------------------------------------------------------------------------


def test_rules_fire_sgp_without_combined_price():
    legs = [
        BetLeg("match_result", {"side": "H"}, label="Home"),
        BetLeg("total_goals", {"line": 2.5, "side": "over"}, label="O 2.5"),
    ]
    results = parlay_rule_check(
        legs=legs,
        leg_probs=[0.5, 0.5],
        joint_probability=0.25,
        correlation_factor=1.0,
        edge=None,
        book_decimal_odds=None,
        source="none",
        same_game=True,
    )
    sgp = next(r for r in results if r.rule == "sgp_needs_combined_price")
    assert not sgp.passed
    assert sgp.severity == "hard"
    all_passed, hard_fail = rules_pass(results)
    assert not all_passed and hard_fail


def test_rules_pass_sgp_with_pasted_price():
    legs = [
        BetLeg("match_result", {"side": "H"}),
        BetLeg("total_goals", {"line": 2.5, "side": "over"}),
    ]
    results = parlay_rule_check(
        legs=legs,
        leg_probs=[0.5, 0.5],
        joint_probability=0.30,
        correlation_factor=1.2,
        edge=0.05,
        book_decimal_odds=3.0,
        source="pasted",
        same_game=True,
    )
    sgp = next(r for r in results if r.rule == "sgp_needs_combined_price")
    assert sgp.passed


def test_rules_lock_player_prop_without_lineup():
    legs = [
        BetLeg("anytime_scorer", {"player_id": "kane"}),
        BetLeg("match_result", {"side": "H"}),
    ]
    results = parlay_rule_check(
        legs=legs,
        leg_probs=[0.4, 0.55],
        joint_probability=0.25,
        correlation_factor=1.0,
        edge=0.06,
        book_decimal_odds=4.0,
        source="pasted",
        lineup_confirmed=False,
    )
    pp = next(r for r in results if r.rule == "player_prop_lineup")
    assert not pp.passed and pp.severity == "hard"


def test_rules_high_var_needs_edge():
    legs = [
        BetLeg("correct_score", {"home": 2, "away": 1}),
    ]
    # Tiny edge → should fail; large edge → should pass.
    small_edge = parlay_rule_check(
        legs=legs, leg_probs=[0.06], joint_probability=0.06,
        correlation_factor=1.0, edge=0.02, book_decimal_odds=18.0,
        source="pasted", same_game=False, min_high_var_edge=0.10,
    )
    big_edge = parlay_rule_check(
        legs=legs, leg_probs=[0.06], joint_probability=0.06,
        correlation_factor=1.0, edge=0.20, book_decimal_odds=22.0,
        source="pasted", same_game=False, min_high_var_edge=0.10,
    )
    small = next(r for r in small_edge if r.rule == "high_var_needs_edge")
    big = next(r for r in big_edge if r.rule == "high_var_needs_edge")
    assert not small.passed
    assert big.passed


def test_rules_block_lottery_joint():
    legs = [
        BetLeg("correct_score", {"home": 3, "away": 2}),
        BetLeg("anytime_scorer", {"player_id": "kane"}),
    ]
    results = parlay_rule_check(
        legs=legs, leg_probs=[0.04, 0.5],
        joint_probability=0.02, correlation_factor=1.0,
        edge=0.4, book_decimal_odds=80.0,
        source="pasted", lineup_confirmed=True, same_game=True,
    )
    lottery = next(r for r in results if r.rule == "not_lottery_ticket")
    assert not lottery.passed and lottery.severity == "hard"


# ---------------------------------------------------------------------------
# failure mode explainer
# ---------------------------------------------------------------------------


def test_failure_modes_identify_dominant_leg():
    # Build sims where leg A (Home win) holds in 9/10 sims but leg B
    # (Over 2.5) holds only in 2/10.  The dominant failure should be B.
    sims: List[MatchSim] = []
    # 2 winning sims: Home wins, total > 2.5
    sims.append(_sim(3, 1))
    sims.append(_sim(2, 1))
    # 7 sims where Home wins but total ≤ 2.5  → only leg B fails
    for _ in range(7):
        sims.append(_sim(1, 0))
    # 1 sim where Home loses and total ≤ 2.5  → both fail
    sims.append(_sim(0, 1))

    legs = [
        BetLeg("match_result", {"side": "H"}, label="Home"),
        BetLeg("total_goals", {"line": 2.5, "side": "over"}, label="O 2.5"),
    ]
    modes, leg_failure_rates = explain_failure_modes(sims, legs, max_modes=5)
    assert sum(1 for m in modes if "O 2.5" in m.legs) >= 1
    # 8 sims failed; B failed in all 8, A in only 1.
    assert leg_failure_rates[1] > leg_failure_rates[0]
    # Top failure mode (single leg failing) should be "Over 2.5" alone (7/8).
    top = modes[0]
    assert top.legs == ["O 2.5"]
    assert abs(top.share - 7 / 8) < 1e-9


def test_failure_modes_handles_all_winning_sims():
    sims = [_sim(2, 0), _sim(3, 0)]
    legs = [BetLeg("match_result", {"side": "H"})]
    modes, rates = explain_failure_modes(sims, legs)
    assert modes == []
    assert rates == [0.0]


# ---------------------------------------------------------------------------
# end-to-end report
# ---------------------------------------------------------------------------


def test_build_correlation_report_end_to_end():
    sims = [
        _sim(2, 1), _sim(2, 0), _sim(1, 0), _sim(0, 0), _sim(0, 1),
    ]
    legs = [
        BetLeg("match_result", {"side": "H"}, label="Home"),
        BetLeg("total_goals", {"line": 2.5, "side": "over"}, label="O 2.5"),
    ]
    # leg A holds in sims 0,1,2 (3/5=0.6); leg B holds in sim 0 (1/5=0.2);
    # joint holds in sim 0 only (1/5=0.2).
    report = build_correlation_report(
        sims=sims,
        legs=legs,
        leg_probabilities=[0.6, 0.2],
        joint_probability=0.20,
        correlation_factor=0.20 / (0.6 * 0.2),
        independent_product=0.6 * 0.2,
        book_decimal_odds=4.0,
        edge=0.20 * 4.0 - 1.0,  # -0.20
        source="pasted",
        lineup_confirmed=True,
        same_game=True,
    )
    assert report.book_implied_probability == 0.25
    assert abs(report.correlation_tax - 0.05) < 1e-9
    assert report.parlay_rules  # populated
    # at least one rule should pass (sgp_needs_combined_price)
    assert any(r.passed for r in report.parlay_rules)
    # failure modes should exist (4 of 5 sims lost)
    assert report.failure_modes
    assert len(report.leg_failure_rates) == 2
