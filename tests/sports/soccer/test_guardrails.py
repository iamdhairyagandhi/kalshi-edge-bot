"""Tests for the Phase 10 guardrails engine."""

from __future__ import annotations

from src.sports.soccer.risk import (
    GuardrailConfig,
    GuardrailReport,
    evaluate_guardrails,
)
from src.sports.soccer.types import BetLeg


def _single_match_result_leg(side: str = "H") -> BetLeg:
    return BetLeg(kind="match_result", params={"side": side}, label=f"{side} win")


def _btts_leg(side: str = "Y") -> BetLeg:
    return BetLeg(kind="btts", params={"side": side}, label=f"BTTS {side}")


def _total_leg(side: str = "O", line: float = 2.5) -> BetLeg:
    return BetLeg(
        kind="total_goals",
        params={"side": side, "line": line},
        label=f"{side}{line}",
    )


def _scorer_leg() -> BetLeg:
    return BetLeg(
        kind="anytime_scorer",
        params={"player_id": "haaland"},
        label="Haaland AGS",
    )


def test_basic_single_leg_passes_all_hard_rules():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=10.0,
        source="live",
    )
    assert isinstance(r, GuardrailReport)
    assert r.allowed is True
    assert r.hard_fail_count == 0
    # Single-leg bets must NOT produce a parlay-rules check at all.
    assert all(not c.rule.startswith("parlay.") for c in r.checks)


def test_stake_zero_is_blocked():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=0.0,
        source="live",
    )
    assert r.allowed is False
    assert r.hard_fail_count >= 1
    assert any(c.rule == "stake_positive" and not c.passed for c in r.checks)


def test_min_odds_blocks_below_floor():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=1.20,  # below 1.30 default
        stake_usd=10.0,
        source="live",
    )
    assert r.allowed is False
    assert any(c.rule == "min_decimal_odds" and not c.passed for c in r.checks)


def test_min_odds_threshold_is_configurable():
    cfg = GuardrailConfig(min_decimal_odds=2.00)
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=1.80,
        stake_usd=10.0,
        source="live",
        config=cfg,
    )
    assert r.allowed is False
    assert any(c.rule == "min_decimal_odds" and not c.passed for c in r.checks)


def test_fixture_exposure_cap_blocks_overstake():
    cfg = GuardrailConfig(fixture_exposure_cap_usd=100.0)
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=60.0,
        source="live",
        open_exposure_by_fixture={"f1": 50.0},
        config=cfg,
    )
    assert r.allowed is False
    cap_check = next(c for c in r.checks if c.rule == "fixture_exposure_cap")
    assert cap_check.passed is False
    assert "110" in cap_check.detail


def test_fixture_exposure_cap_zero_disables_check():
    cfg = GuardrailConfig(fixture_exposure_cap_usd=0.0)
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=1000.0,
        source="live",
        open_exposure_by_fixture={"f1": 10000.0},
        config=cfg,
    )
    cap_check = next(c for c in r.checks if c.rule == "fixture_exposure_cap")
    assert cap_check.passed is True


def test_player_prop_blocked_without_lineup():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_scorer_leg()],
        placed_decimal_odds=2.40,
        stake_usd=5.0,
        source="live",
        lineup_confirmed=False,
    )
    assert r.allowed is False
    assert any(c.rule == "player_prop_lineup" and not c.passed for c in r.checks)


def test_player_prop_allowed_when_lineup_confirmed():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_scorer_leg()],
        placed_decimal_odds=2.40,
        stake_usd=5.0,
        source="live",
        lineup_confirmed=True,
    )
    pp = next(c for c in r.checks if c.rule == "player_prop_lineup")
    assert pp.passed is True


def test_same_game_parlay_requires_combined_price():
    legs = [_btts_leg("Y"), _total_leg("O", 2.5)]
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=legs,
        placed_decimal_odds=3.50,
        stake_usd=5.0,
        source="manual",                  # cannot price SGP from manual
        same_game=True,
        leg_probs=[0.55, 0.60],
        joint_probability=0.40,
        correlation_factor=1.15,
        edge=0.05,
    )
    assert r.allowed is False
    chk = next(c for c in r.checks if c.rule == "same_game_needs_price")
    assert chk.passed is False
    assert chk.severity == "hard"


def test_same_game_parlay_allowed_with_pasted_price():
    legs = [_btts_leg("Y"), _total_leg("O", 2.5)]
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=legs,
        placed_decimal_odds=3.50,
        stake_usd=5.0,
        source="pasted",
        book_decimal_odds=3.50,
        same_game=True,
        leg_probs=[0.55, 0.60],
        joint_probability=0.40,
        correlation_factor=1.15,
        edge=0.05,
    )
    chk = next(c for c in r.checks if c.rule == "same_game_needs_price")
    assert chk.passed is True


def test_parlay_without_probs_emits_not_evaluated_warning():
    legs = [_btts_leg("Y"), _total_leg("O", 2.5)]
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=legs,
        placed_decimal_odds=3.50,
        stake_usd=5.0,
        source="pasted",
        book_decimal_odds=3.50,
        same_game=True,
    )
    assert any(c.rule == "parlay.not_evaluated" and not c.passed for c in r.checks)


def test_phase9_parlay_rule_hard_fail_propagates_into_guardrails():
    # 7 high-variance legs on a same-game parlay forces multiple Phase 9
    # hard fails (max_legs, lottery, etc.) — guardrails must surface
    # them as hard blocks under the parlay.* namespace.
    legs = [_scorer_leg() for _ in range(7)]
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=legs,
        placed_decimal_odds=120.0,
        stake_usd=2.0,
        source="pasted",
        book_decimal_odds=120.0,
        same_game=True,
        lineup_confirmed=True,
        leg_probs=[0.20] * 7,
        joint_probability=1e-5,
        correlation_factor=1.0,
        edge=-0.10,
    )
    parlay_blocks = [c for c in r.checks if c.rule.startswith("parlay.") and not c.passed]
    assert parlay_blocks, "expected at least one parlay rule to surface as a guardrail"
    assert any(c.severity == "hard" for c in parlay_blocks)
    assert r.allowed is False


def test_negative_clv_market_emits_warn_only():
    clv_summary = {
        "by_market": [
            {"bucket": "match_result", "count_with_clv": 10, "avg_clv_pct": -0.05},
        ],
    }
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=10.0,
        source="live",
        clv_summary=clv_summary,
    )
    chk = next(c for c in r.checks if c.rule == "negative_clv_market")
    assert chk.severity == "warn"
    assert chk.passed is False
    # warnings must NOT block
    assert r.allowed is True
    assert r.warn_count >= 1


def test_negative_clv_market_skipped_when_sample_too_small():
    clv_summary = {
        "by_market": [
            {"bucket": "match_result", "count_with_clv": 2, "avg_clv_pct": -0.50},
        ],
    }
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=10.0,
        source="live",
        clv_summary=clv_summary,
    )
    assert all(c.rule != "negative_clv_market" for c in r.checks)


def test_negative_clv_market_skipped_when_no_history():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=2.10,
        stake_usd=10.0,
        source="live",
        clv_summary=None,
    )
    assert all(c.rule != "negative_clv_market" for c in r.checks)


def test_blocking_reasons_only_contain_hard_fails():
    r = evaluate_guardrails(
        fixture_id="f1",
        legs=[_single_match_result_leg()],
        placed_decimal_odds=1.10,
        stake_usd=0.0,
        source="live",
    )
    assert r.hard_fail_count >= 2
    assert len(r.blocking_reasons) == r.hard_fail_count
    # warnings stay separate
    assert all("acceptable" not in r.lower() for r in r.blocking_reasons)
