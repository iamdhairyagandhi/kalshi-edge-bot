"""Phase 4 — Tests for the model calibration aggregator.

Covers the pure helper functions (`_calibration_bucket_stats`,
`_odds_bucket_for`, `_reliability_curve`, `_build_calibration_summary`)
and the public store method `soccer_bets_calibration_summary()`.
"""

from __future__ import annotations

import math

from src.sports.soccer.data.store import (
    SoccerStore,
    _build_calibration_summary,
    _calibration_bucket_stats,
    _odds_bucket_for,
    _reliability_curve,
)


def _legs(kind: str = "match_result") -> list[dict]:
    return [{"kind": kind, "label": "H", "params": {}}]


def _seed_settled_bet(
    store: SoccerStore,
    *,
    fixture_id: str,
    model_p: float,
    placed_odds: float,
    stake: float,
    won: bool,
    legs=None,
    slip_type: str | None = None,
    qualification_status: str = "BETTABLE",
    placed_unix: int = 1,
):
    bet_id = store.record_soccer_bet(
        fixture_id=fixture_id,
        match_label="A vs B",
        legs=legs or _legs(),
        model_probability=model_p,
        fair_decimal_odds=1.0 / model_p,
        placed_decimal_odds=placed_odds,
        stake_usd=stake,
        qualification_status=qualification_status,
        source="manual",
        slip_type=slip_type,
        created_unix=placed_unix,
        placed_unix=placed_unix,
    )
    pnl = stake * (placed_odds - 1.0) if won else -stake
    store.soccer_bet_update(
        bet_id, status="won" if won else "lost",
        pnl_usd=pnl, actual_return_usd=stake + pnl if won else 0.0,
        settled_unix=placed_unix + 100,
    )
    return bet_id


# ---- Pure helpers ---------------------------------------------------------


def test_odds_bucket_for_covers_full_range():
    assert _odds_bucket_for(1.20) == "le_1_50"
    assert _odds_bucket_for(1.50) == "1_50_2"
    assert _odds_bucket_for(1.99) == "1_50_2"
    assert _odds_bucket_for(2.00) == "2_3"
    assert _odds_bucket_for(2.99) == "2_3"
    assert _odds_bucket_for(3.00) == "3_5"
    assert _odds_bucket_for(4.99) == "3_5"
    assert _odds_bucket_for(5.00) == "5_10"
    assert _odds_bucket_for(9.99) == "5_10"
    assert _odds_bucket_for(10.00) == "ge_10"
    assert _odds_bucket_for(1000.0) == "ge_10"
    assert _odds_bucket_for(0.5) == "unknown"
    assert _odds_bucket_for(None) == "unknown"
    assert _odds_bucket_for("nope") == "unknown"


def test_calibration_bucket_stats_empty():
    s = _calibration_bucket_stats([])
    assert s["count"] == 0
    assert s["won"] == 0
    assert s["hit_rate"] is None
    assert s["brier"] is None
    assert s["log_loss"] is None
    assert s["roi"] is None


def test_calibration_bucket_stats_perfect_calibration_gives_low_brier():
    rows = [
        {"model_probability": 0.6, "placed_decimal_odds": 2.0,
         "stake_usd": 10.0, "pnl_usd": 10.0, "status": "won"},
        {"model_probability": 0.6, "placed_decimal_odds": 2.0,
         "stake_usd": 10.0, "pnl_usd": -10.0, "status": "lost"},
        {"model_probability": 0.6, "placed_decimal_odds": 2.0,
         "stake_usd": 10.0, "pnl_usd": 10.0, "status": "won"},
        {"model_probability": 0.6, "placed_decimal_odds": 2.0,
         "stake_usd": 10.0, "pnl_usd": 10.0, "status": "won"},
        {"model_probability": 0.6, "placed_decimal_odds": 2.0,
         "stake_usd": 10.0, "pnl_usd": -10.0, "status": "lost"},
    ]
    s = _calibration_bucket_stats(rows)
    assert s["count"] == 5
    assert s["won"] == 3
    assert math.isclose(s["hit_rate"], 0.6)
    assert math.isclose(s["avg_predicted"], 0.6)
    expected_brier = 3 * (0.4 ** 2) / 5 + 2 * (0.6 ** 2) / 5
    assert math.isclose(s["brier"], expected_brier, rel_tol=1e-9)
    assert math.isclose(s["total_stake_usd"], 50.0)
    # 3 wins at +10, 2 losses at -10 => +10 net
    assert math.isclose(s["net_pnl_usd"], 10.0)
    assert math.isclose(s["roi"], 10.0 / 50.0)


def test_calibration_bucket_stats_overconfident_high_brier():
    # Predicted 0.95 every time; only half win -> heavily miscalibrated.
    rows = [
        {"model_probability": 0.95, "placed_decimal_odds": 1.10,
         "stake_usd": 10.0, "pnl_usd": 1.0, "status": "won"},
        {"model_probability": 0.95, "placed_decimal_odds": 1.10,
         "stake_usd": 10.0, "pnl_usd": -10.0, "status": "lost"},
    ]
    s = _calibration_bucket_stats(rows)
    assert math.isclose(s["hit_rate"], 0.5)
    assert math.isclose(s["avg_predicted"], 0.95)
    # brier = ((1-0.95)^2 + (0-0.95)^2)/2 = (0.0025 + 0.9025)/2 = 0.4525
    assert math.isclose(s["brier"], 0.4525, rel_tol=1e-9)
    assert s["log_loss"] > 0.5  # heavy penalty for confident wrong call


def test_calibration_bucket_stats_ev_per_dollar_sign():
    # Positive-EV bet (model 0.6, price 2.0 -> implied 0.5)
    rows = [{"model_probability": 0.6, "placed_decimal_odds": 2.0,
             "stake_usd": 10.0, "pnl_usd": 10.0, "status": "won"}]
    s = _calibration_bucket_stats(rows)
    # ev = 0.6*1.0 - 0.4 = 0.2
    assert math.isclose(s["ev_per_dollar"], 0.2, rel_tol=1e-9)


def test_reliability_curve_groups_into_deciles():
    rows = [
        {"model_probability": 0.05, "placed_decimal_odds": 20.0,
         "stake_usd": 10.0, "pnl_usd": -10.0, "status": "lost"},
        {"model_probability": 0.45, "placed_decimal_odds": 2.2,
         "stake_usd": 10.0, "pnl_usd": -10.0, "status": "lost"},
        {"model_probability": 0.45, "placed_decimal_odds": 2.2,
         "stake_usd": 10.0, "pnl_usd": 12.0, "status": "won"},
        {"model_probability": 0.95, "placed_decimal_odds": 1.10,
         "stake_usd": 10.0, "pnl_usd": 1.0, "status": "won"},
    ]
    curve = _reliability_curve(rows)
    assert len(curve) == 10
    by_bucket = {p["bucket"]: p for p in curve}
    assert by_bucket["p_00_10"]["count"] == 1
    assert by_bucket["p_00_10"]["observed_hit_rate"] == 0.0
    assert by_bucket["p_40_50"]["count"] == 2
    assert math.isclose(by_bucket["p_40_50"]["observed_hit_rate"], 0.5)
    assert by_bucket["p_90_100"]["count"] == 1
    assert by_bucket["p_90_100"]["observed_hit_rate"] == 1.0
    # Empty buckets have None stats but the entry still exists.
    assert by_bucket["p_20_30"]["count"] == 0
    assert by_bucket["p_20_30"]["observed_hit_rate"] is None


def test_reliability_curve_includes_p_equal_one_in_top_bucket():
    rows = [
        {"model_probability": 1.0, "placed_decimal_odds": 1.05,
         "stake_usd": 10.0, "pnl_usd": 0.5, "status": "won"},
    ]
    curve = _reliability_curve(rows)
    by_bucket = {p["bucket"]: p for p in curve}
    assert by_bucket["p_90_100"]["count"] == 1


def test_build_calibration_summary_overall_shape():
    summary = _build_calibration_summary([])
    assert summary["overall"]["count"] == 0
    assert summary["by_market"] == []
    assert summary["by_rating_bucket"] == []
    assert summary["by_odds_bucket"] == []
    assert summary["by_slip_type"] == []
    assert summary["by_qualification_status"] == []
    # reliability curve always has 10 points
    assert len(summary["reliability_curve"]) == 10


# ---- Store integration ----------------------------------------------------


def test_soccer_bets_calibration_summary_empty(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    summary = s.soccer_bets_calibration_summary()
    assert summary["overall"]["count"] == 0
    assert summary["overall"]["hit_rate"] is None
    assert summary["by_market"] == []
    assert len(summary["reliability_curve"]) == 10


def test_soccer_bets_calibration_summary_excludes_open_and_void(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    # Open bet — must not appear.
    s.record_soccer_bet(
        fixture_id="f1", match_label="A vs B", legs=_legs(),
        model_probability=0.6, fair_decimal_odds=1.66,
        placed_decimal_odds=2.0, stake_usd=10.0,
        qualification_status="BETTABLE", source="manual",
        created_unix=1, placed_unix=1,
    )
    # Settled won bet.
    _seed_settled_bet(
        s, fixture_id="f2", model_p=0.55, placed_odds=2.10,
        stake=10.0, won=True, placed_unix=2,
    )
    # Void bet — must not appear.
    void_id = s.record_soccer_bet(
        fixture_id="f3", match_label="A vs B", legs=_legs(),
        model_probability=0.4, fair_decimal_odds=2.5,
        placed_decimal_odds=2.5, stake_usd=10.0,
        qualification_status="BETTABLE", source="manual",
        created_unix=3, placed_unix=3,
    )
    s.soccer_bet_update(void_id, status="void", settled_unix=400)

    summary = s.soccer_bets_calibration_summary()
    assert summary["overall"]["count"] == 1
    assert summary["overall"]["won"] == 1
    assert summary["overall"]["hit_rate"] == 1.0


def test_soccer_bets_calibration_summary_full_buckets(tmp_path):
    s = SoccerStore(str(tmp_path / "soccer.db"))
    # Three single-leg match_result wins/losses + one parlay win.
    _seed_settled_bet(s, fixture_id="f1", model_p=0.55, placed_odds=2.10,
                      stake=10.0, won=True, slip_type="value", placed_unix=1)
    _seed_settled_bet(s, fixture_id="f2", model_p=0.45, placed_odds=2.40,
                      stake=10.0, won=False, slip_type="value", placed_unix=2)
    _seed_settled_bet(s, fixture_id="f3", model_p=0.80, placed_odds=1.30,
                      stake=10.0, won=True, slip_type="value", placed_unix=3)
    _seed_settled_bet(
        s, fixture_id="f4", model_p=0.35, placed_odds=2.95, stake=10.0,
        won=True,
        legs=[{"kind": "match_result", "label": "H"},
              {"kind": "totals", "label": "O2.5"}],
        slip_type="parlay", placed_unix=4,
    )

    summary = s.soccer_bets_calibration_summary()
    overall = summary["overall"]
    assert overall["count"] == 4
    assert overall["won"] == 3
    assert math.isclose(overall["hit_rate"], 0.75)
    assert overall["brier"] is not None
    assert overall["log_loss"] is not None
    assert overall["roi"] is not None

    by_market = {b["bucket"]: b for b in summary["by_market"]}
    assert by_market["match_result"]["count"] == 3
    assert by_market["parlay"]["count"] == 1
    assert by_market["match_result"]["won"] == 2

    by_rating = {b["bucket"]: b for b in summary["by_rating_bucket"]}
    # 0.55 -> coinflip, 0.45 -> coinflip, 0.80 -> heavy_fav, 0.35 -> underdog
    assert by_rating["coinflip"]["count"] == 2
    assert by_rating["heavy_fav"]["count"] == 1
    assert by_rating["underdog"]["count"] == 1

    by_odds = {b["bucket"]: b for b in summary["by_odds_bucket"]}
    # 2.10, 2.40 -> 2_3 ; 1.30 -> le_1_50 ; 2.95 -> 2_3
    assert by_odds["2_3"]["count"] == 3
    assert by_odds["le_1_50"]["count"] == 1

    by_slip = {b["bucket"]: b for b in summary["by_slip_type"]}
    assert by_slip["value"]["count"] == 3
    assert by_slip["parlay"]["count"] == 1

    by_qual = {b["bucket"]: b for b in summary["by_qualification_status"]}
    assert by_qual["BETTABLE"]["count"] == 4

    # Reliability curve still has 10 entries regardless.
    assert len(summary["reliability_curve"]) == 10
    by_curve = {p["bucket"]: p for p in summary["reliability_curve"]}
    assert by_curve["p_50_60"]["count"] == 1  # 0.55 fell here
    assert by_curve["p_80_90"]["count"] == 1  # 0.80 fell here
