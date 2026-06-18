"""
Phase 9 — Smart Bet Builder & Correlation Engine.

This module sits on top of the simulation-based bet-builder pricer and adds
the analyst-grade layer the user needs in order to decide whether a parlay
deserves real money:

- Correlation tax vs. the book's combined price.
- Parlay rules (max legs, no high-variance leg unless big edge, no SGP
  without combined book price, no duplicate exposure, etc.).
- Failure-mode explainer — which leg(s) most often killed the parlay across
  the simulations, expressed as plain English.

Kept dependency-free (stdlib only) so it can be unit tested without spinning
up the full FastAPI stack.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.sports.soccer.simulator.bet_builder import (
    HIGH_VARIANCE_LEGS,
    LEG_PREDICATES,
    MEDIUM_VARIANCE_LEGS,
)
from src.sports.soccer.types import BetLeg, MatchSim


# Leg kinds that depend on lineup confirmation.  Phase 7 will plumb a real
# lineup feed; until then the rule engine just emits a warning.
PLAYER_PROP_LEGS = {
    "anytime_scorer",
    "first_scorer",
    "last_scorer",
    "player_yellow",
    "player_red",
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParlayRuleResult:
    """One row in the parlay-rule check table."""

    rule: str
    passed: bool
    severity: str  # "hard" | "warn"
    detail: str


@dataclass
class FailureMode:
    """A common reason the parlay lost across simulations."""

    legs: List[str]            # leg labels (or kinds) that failed together
    share: float               # fraction of FAILING sims that matched (0..1)
    why: str                   # plain English


@dataclass
class CorrelationReport:
    """Full Phase 9 report returned by :func:`build_correlation_report`."""

    independent_product: float
    joint_probability: float
    correlation_factor: float          # joint / Π(leg)
    book_implied_probability: Optional[float] = None
    correlation_tax: Optional[float] = None
    correlation_tax_pct: Optional[float] = None
    parlay_rules: List[ParlayRuleResult] = field(default_factory=list)
    parlay_rules_passed: bool = True
    parlay_rules_hard_fail: bool = False
    failure_modes: List[FailureMode] = field(default_factory=list)
    leg_failure_rates: List[float] = field(default_factory=list)
    duplicate_exposure_groups: List[List[int]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leg_label(leg: BetLeg, idx: int) -> str:
    if leg.label:
        return leg.label
    return f"{leg.kind} #{idx + 1}"


def _book_implied(book_decimal_odds: Optional[float]) -> Optional[float]:
    if book_decimal_odds is None:
        return None
    if not (book_decimal_odds > 1.0):
        return None
    return 1.0 / book_decimal_odds


def _safe_div(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None
    return a / b


# ---------------------------------------------------------------------------
# Correlation tax
# ---------------------------------------------------------------------------


def compute_correlation_tax(
    *,
    joint_probability: float,
    book_decimal_odds: Optional[float],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return ``(book_implied, correlation_tax, correlation_tax_pct)``.

    - ``book_implied`` = 1 / book_decimal_odds.
    - ``correlation_tax`` = ``book_implied - joint_probability``.  A positive
      number means the book is pricing a *lower* combined probability than
      our simulation implies (i.e., they are charging a correlation premium).
      A negative number means the book is *underpaying* correlation — that's
      our edge.
    - ``correlation_tax_pct`` expresses the tax as a multiple of the joint
      probability so we can compare across price levels (e.g., +18% means
      the book is charging 18% more than fair).
    """
    book_imp = _book_implied(book_decimal_odds)
    if book_imp is None:
        return None, None, None
    tax = book_imp - joint_probability
    tax_pct = _safe_div(tax, joint_probability)
    return book_imp, tax, tax_pct


# ---------------------------------------------------------------------------
# Duplicate-exposure detector
# ---------------------------------------------------------------------------


def _leg_exposure_key(leg: BetLeg) -> Optional[Tuple[str, ...]]:
    """Identifies legs that bet on *the same outcome* so we can warn the user
    they are double-counting exposure inside a parlay (which the simulator
    will price correctly, but is still poor bankroll hygiene)."""

    p = dict(leg.params)
    side = str(p.get("side", "")).lower()
    team = str(p.get("team", "")).lower()
    line = p.get("line")
    line_key = f"{float(line):.2f}" if isinstance(line, (int, float)) else ""
    player = str(p.get("player_id", ""))

    k = leg.kind
    if k == "match_result":
        return ("match_result", str(p.get("side", "")).upper())
    if k in {"total_goals", "btts", "total_cards", "total_corners",
             "total_shots", "total_shots_on_target", "total_fouls"}:
        return (k, line_key, side)
    if k in {"team_total", "team_cards", "team_corners", "team_shots",
             "team_shots_on_target", "team_fouls"}:
        return (k, team, line_key, side)
    if k == "correct_score":
        return ("correct_score", str(p.get("home", "")), str(p.get("away", "")))
    if k in {"anytime_scorer", "first_scorer", "last_scorer",
             "player_yellow", "player_red"}:
        return (k, player)
    return None


def detect_duplicate_exposure(legs: Sequence[BetLeg]) -> List[List[int]]:
    """Return groups of leg indices that bet on the same outcome.

    Only groups with 2+ legs are returned.
    """
    seen: Dict[Tuple[str, ...], List[int]] = {}
    for i, leg in enumerate(legs):
        key = _leg_exposure_key(leg)
        if key is None:
            continue
        seen.setdefault(key, []).append(i)
    return [g for g in seen.values() if len(g) > 1]


# ---------------------------------------------------------------------------
# Parlay rule engine
# ---------------------------------------------------------------------------


def parlay_rule_check(
    *,
    legs: Sequence[BetLeg],
    leg_probs: Sequence[float],
    joint_probability: float,
    correlation_factor: float,
    edge: Optional[float],
    book_decimal_odds: Optional[float],
    source: str = "live",            # "live" | "pasted" | "manual" | "none"
    lineup_confirmed: bool = False,
    same_game: bool = True,
    min_high_var_edge: float = 0.12,
    duplicate_groups: Optional[Sequence[Sequence[int]]] = None,
) -> List[ParlayRuleResult]:
    """Run the Phase 9 parlay rules.

    `same_game` defaults to True because the existing pipeline only builds
    same-fixture parlays today; pass False if/when cross-game parlays are
    introduced.
    """
    out: List[ParlayRuleResult] = []
    n_legs = len(legs)

    # Rule 1 — max legs by risk tier.
    high_var = sum(1 for leg in legs if leg.kind in HIGH_VARIANCE_LEGS)
    med_var = sum(1 for leg in legs if leg.kind in MEDIUM_VARIANCE_LEGS)
    risk_tier = "safer"
    if high_var > 0:
        risk_tier = "high"
    elif med_var >= 2:
        risk_tier = "moderate"
    elif n_legs >= 4:
        risk_tier = "moderate"
    max_legs = {"safer": 3, "moderate": 4, "high": 5}[risk_tier]
    legs_ok = n_legs <= max_legs
    out.append(ParlayRuleResult(
        rule="max_legs",
        passed=legs_ok,
        severity="hard" if not legs_ok and n_legs > max_legs + 1 else "warn",
        detail=(
            f"{n_legs} leg(s) on a {risk_tier} parlay (cap {max_legs})."
            if not legs_ok else
            f"{n_legs} leg(s) within the {risk_tier}-tier cap of {max_legs}."
        ),
    ))

    # Rule 2 — high-variance leg only when edge clears a higher bar.
    if high_var > 0:
        if edge is None:
            out.append(ParlayRuleResult(
                rule="high_var_needs_edge",
                passed=False,
                severity="hard",
                detail=(
                    f"Contains {high_var} high-variance leg(s); "
                    "needs a real combined book price to validate edge."
                ),
            ))
        elif edge >= min_high_var_edge:
            out.append(ParlayRuleResult(
                rule="high_var_needs_edge",
                passed=True,
                severity="warn",
                detail=(
                    f"High-variance leg(s) cleared the +{min_high_var_edge:.2f} "
                    f"edge bar (edge {edge:+.2%})."
                ),
            ))
        else:
            out.append(ParlayRuleResult(
                rule="high_var_needs_edge",
                passed=False,
                severity="hard",
                detail=(
                    f"High-variance leg(s) but edge {edge:+.2%} below the "
                    f"+{min_high_var_edge:.2f} bar for prop parlays."
                ),
            ))

    # Rule 3 — no player prop before lineup confirmation.
    player_props = [leg for leg in legs if leg.kind in PLAYER_PROP_LEGS]
    if player_props:
        out.append(ParlayRuleResult(
            rule="player_prop_lineup",
            passed=bool(lineup_confirmed),
            severity="hard",
            detail=(
                f"{len(player_props)} player-prop leg(s) require confirmed lineups."
                if not lineup_confirmed else
                f"{len(player_props)} player-prop leg(s) with confirmed lineups."
            ),
        ))

    # Rule 4 — same-game parlay needs a combined book price.
    if same_game and n_legs >= 2:
        has_combined = source in {"live", "pasted"} and book_decimal_odds is not None
        out.append(ParlayRuleResult(
            rule="sgp_needs_combined_price",
            passed=has_combined,
            severity="hard",
            detail=(
                "Same-game parlay has a real combined book price."
                if has_combined else
                "Same-game parlay needs a pasted or live combined book price "
                "before it can be marked bettable."
            ),
        ))

    # Rule 5 — no duplicate exposure inside the same parlay.
    dup_groups = list(duplicate_groups) if duplicate_groups is not None else detect_duplicate_exposure(legs)
    if dup_groups:
        labels = [
            ", ".join(_leg_label(legs[i], i) for i in group)
            for group in dup_groups
        ]
        out.append(ParlayRuleResult(
            rule="no_duplicate_exposure",
            passed=False,
            severity="hard",
            detail=f"Duplicate exposure detected: {' | '.join(labels)}.",
        ))
    else:
        out.append(ParlayRuleResult(
            rule="no_duplicate_exposure",
            passed=True,
            severity="warn",
            detail="No two legs cover the same outcome.",
        ))

    # Rule 6 — joint probability is not lottery-low.
    if joint_probability < 0.03:
        out.append(ParlayRuleResult(
            rule="not_lottery_ticket",
            passed=False,
            severity="hard",
            detail=f"Joint probability {joint_probability:.2%} is lottery-ticket low.",
        ))
    elif joint_probability < 0.08:
        out.append(ParlayRuleResult(
            rule="not_lottery_ticket",
            passed=False,
            severity="warn",
            detail=f"Joint probability {joint_probability:.2%} is low; expect long losing runs.",
        ))
    else:
        out.append(ParlayRuleResult(
            rule="not_lottery_ticket",
            passed=True,
            severity="warn",
            detail=f"Joint probability {joint_probability:.2%}.",
        ))

    # Rule 7 — correlation factor sanity.
    if correlation_factor != correlation_factor:  # NaN
        pass
    elif correlation_factor < 0.65:
        out.append(ParlayRuleResult(
            rule="correlation_sanity",
            passed=False,
            severity="warn",
            detail=(
                f"Legs are negatively correlated (factor {correlation_factor:.2f}); "
                "joint probability is lower than the product of marginals."
            ),
        ))
    elif correlation_factor > 2.5:
        out.append(ParlayRuleResult(
            rule="correlation_sanity",
            passed=False,
            severity="warn",
            detail=(
                f"Parlay depends heavily on correlation (factor {correlation_factor:.2f}); "
                "if the books also price this correlation in, edge will collapse."
            ),
        ))
    else:
        out.append(ParlayRuleResult(
            rule="correlation_sanity",
            passed=True,
            severity="warn",
            detail=f"Correlation factor {correlation_factor:.2f} within normal range.",
        ))

    return out


def rules_pass(results: Sequence[ParlayRuleResult]) -> Tuple[bool, bool]:
    """Return ``(all_passed, any_hard_fail)``."""
    all_passed = all(r.passed for r in results)
    hard_fail = any((not r.passed) and r.severity == "hard" for r in results)
    return all_passed, hard_fail


# ---------------------------------------------------------------------------
# Failure-mode explainer
# ---------------------------------------------------------------------------


def _evaluate_leg(sim: MatchSim, leg: BetLeg) -> bool:
    pred = LEG_PREDICATES.get(leg.kind)
    if pred is None:
        return False
    return bool(pred(sim, dict(leg.params)))


def explain_failure_modes(
    sims: Sequence[MatchSim],
    legs: Sequence[BetLeg],
    *,
    max_modes: int = 4,
) -> Tuple[List[FailureMode], List[float]]:
    """Inspect simulations that did *not* win the parlay and group them by
    which legs failed.

    Returns ``(failure_modes, leg_failure_rates)`` where:

    - ``failure_modes`` lists the most common groups of failing legs, each
      with the share of failing sims that hit that group and a plain-English
      ``why``.
    - ``leg_failure_rates`` is the marginal failure rate per leg (1 - p) but
      computed only over the simulations that lost the parlay; this makes
      it obvious which leg is the dominant killer.
    """
    if not sims or not legs:
        return [], [0.0] * len(legs)

    losing_combos: Counter = Counter()
    per_leg_failures = [0] * len(legs)
    losing_sims = 0
    for sim in sims:
        results = tuple(_evaluate_leg(sim, leg) for leg in legs)
        if all(results):
            continue
        losing_sims += 1
        failed_indices = tuple(i for i, r in enumerate(results) if not r)
        losing_combos[failed_indices] += 1
        for i in failed_indices:
            per_leg_failures[i] += 1

    if losing_sims == 0:
        return [], [0.0] * len(legs)

    leg_failure_rates = [c / losing_sims for c in per_leg_failures]

    modes: List[FailureMode] = []
    for combo, count in losing_combos.most_common(max_modes):
        share = count / losing_sims
        leg_labels = [_leg_label(legs[i], i) for i in combo]
        if len(combo) == 1:
            why = f"{leg_labels[0]} failed on its own."
        elif len(combo) == len(legs):
            why = "Every leg failed together — these legs likely move with the same outcome."
        else:
            why = f"{', '.join(leg_labels)} failed at the same time."
        modes.append(FailureMode(legs=leg_labels, share=share, why=why))
    return modes, leg_failure_rates


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_correlation_report(
    *,
    sims: Sequence[MatchSim],
    legs: Sequence[BetLeg],
    leg_probabilities: Sequence[float],
    joint_probability: float,
    correlation_factor: float,
    independent_product: float,
    book_decimal_odds: Optional[float],
    edge: Optional[float],
    source: str = "live",
    lineup_confirmed: bool = False,
    same_game: bool = True,
    min_high_var_edge: float = 0.12,
    max_failure_modes: int = 4,
) -> CorrelationReport:
    """Run every Phase 9 analysis in one pass."""

    book_imp, tax, tax_pct = compute_correlation_tax(
        joint_probability=joint_probability,
        book_decimal_odds=book_decimal_odds,
    )

    dup_groups = detect_duplicate_exposure(legs)
    rules = parlay_rule_check(
        legs=legs,
        leg_probs=leg_probabilities,
        joint_probability=joint_probability,
        correlation_factor=correlation_factor,
        edge=edge,
        book_decimal_odds=book_decimal_odds,
        source=source,
        lineup_confirmed=lineup_confirmed,
        same_game=same_game,
        min_high_var_edge=min_high_var_edge,
        duplicate_groups=dup_groups,
    )
    all_passed, hard_fail = rules_pass(rules)

    modes, leg_failure_rates = explain_failure_modes(
        sims=sims,
        legs=legs,
        max_modes=max_failure_modes,
    )

    return CorrelationReport(
        independent_product=independent_product,
        joint_probability=joint_probability,
        correlation_factor=correlation_factor,
        book_implied_probability=book_imp,
        correlation_tax=tax,
        correlation_tax_pct=tax_pct,
        parlay_rules=rules,
        parlay_rules_passed=all_passed,
        parlay_rules_hard_fail=hard_fail,
        failure_modes=modes,
        leg_failure_rates=leg_failure_rates,
        duplicate_exposure_groups=[list(g) for g in dup_groups],
    )
