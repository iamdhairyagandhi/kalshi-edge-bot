"""
Phase 10 — Strategy Guardrails / Auto-Block Rules.

Runs before a bet can be recorded to the journal. Independent of the
client-side qualification gate so that even a hand-crafted POST cannot
bypass the gate. Returns a structured :class:`GuardrailReport` that the
API attaches to the response (and persists alongside the bet) so the
journal can show *exactly* why a bet was blocked or allowed.

The engine is deliberately stdlib-only so it can be unit-tested without
spinning up FastAPI or SQLite.

Rules implemented in this phase:

- ``stake_positive``        — stake must be > 0 (hard).
- ``min_decimal_odds``      — placed odds >= configurable floor (hard).
- ``fixture_exposure_cap``  — open stake on this fixture + new stake <= cap (hard).
- ``player_prop_lineup``    — block player-prop legs unless lineup confirmed (hard).
- ``same_game_needs_price`` — same-game parlays need a real (live or pasted)
  combined price; we cannot price an SGP from manual odds alone (hard).
- ``parlay_rules``          — when the slip has 2+ legs we invoke Phase 9's
  ``parlay_rule_check`` and surface any hard fails (hard | warn).
- ``negative_clv_market``   — soft warning when the historical CLV summary
  shows this market has at least ``min_bets_for_clv_block`` settled bets
  with average CLV worse than ``negative_clv_threshold``.

Stale-odds, calibration-EV, and per-book reliability rules are deferred
to Phase 4 (calibration) and Phase 5 (odds feed) once their data sources
exist; the engine is structured so adding them later is a one-rule patch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional, Sequence

from src.sports.soccer.simulator.correlation import (
    PLAYER_PROP_LEGS,
    ParlayRuleResult,
    parlay_rule_check,
)
from src.sports.soccer.types import BetLeg


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GuardrailCheck:
    """One row in the guardrail report. ``rule`` is a stable machine id
    so the UI can re-render the same check after a refresh; ``label`` is
    a short human-readable name."""

    rule: str
    label: str
    passed: bool
    severity: str  # "hard" | "warn"
    detail: str


@dataclass
class GuardrailConfig:
    """Tunable thresholds. Defaults mirror the client-side qualifier so a
    bet that passes the dashboard is unlikely to be blocked at the API."""

    min_decimal_odds: float = 1.30
    fixture_exposure_cap_usd: float = 100.0
    negative_clv_threshold: float = -0.03   # avg CLV below this triggers warn
    min_bets_for_clv_block: int = 5
    min_high_var_edge: float = 0.12


@dataclass
class GuardrailReport:
    """Aggregate output of :func:`evaluate_guardrails`.

    The API converts this into a Pydantic model and either persists it
    alongside the bet or refuses to persist when ``hard_fail_count > 0``
    and the caller did not opt in to ``force``.
    """

    checks: List[GuardrailCheck] = field(default_factory=list)
    hard_fail_count: int = 0
    warn_count: int = 0
    blocking_reasons: List[str] = field(default_factory=list)
    parlay_rules: List[ParlayRuleResult] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """True iff there are no hard fails — the only thing the API
        actually enforces. Warnings are surfaced but do not block."""
        return self.hard_fail_count == 0


# ---------------------------------------------------------------------------
# Individual rule helpers (each returns a single GuardrailCheck)
# ---------------------------------------------------------------------------


def _check_stake_positive(stake_usd: float) -> GuardrailCheck:
    ok = stake_usd is not None and float(stake_usd) > 0.0
    return GuardrailCheck(
        rule="stake_positive",
        label="Stake positive",
        passed=ok,
        severity="hard",
        detail=(
            f"Stake ${float(stake_usd):.2f} is greater than zero." if ok
            else "Stake must be greater than zero."
        ),
    )


def _check_min_decimal_odds(
    placed_decimal_odds: float, floor: float
) -> GuardrailCheck:
    ok = placed_decimal_odds is not None and float(placed_decimal_odds) >= floor
    return GuardrailCheck(
        rule="min_decimal_odds",
        label="Minimum decimal odds",
        passed=ok,
        severity="hard",
        detail=(
            f"Placed {float(placed_decimal_odds):.2f} clears floor of {floor:.2f}."
            if ok else
            f"Placed {float(placed_decimal_odds):.2f} below required floor of {floor:.2f}."
        ),
    )


def _check_fixture_exposure_cap(
    *,
    stake_usd: float,
    fixture_id: str,
    open_exposure_by_fixture: Mapping[str, float],
    cap_usd: float,
) -> GuardrailCheck:
    existing = float(open_exposure_by_fixture.get(fixture_id, 0.0) or 0.0)
    total = existing + float(stake_usd or 0.0)
    ok = cap_usd <= 0 or total <= cap_usd
    return GuardrailCheck(
        rule="fixture_exposure_cap",
        label="Fixture exposure cap",
        passed=ok,
        severity="hard",
        detail=(
            f"Existing open ${existing:.2f} + new ${float(stake_usd):.2f} "
            f"= ${total:.2f} (cap ${cap_usd:.2f})."
            if cap_usd > 0 else
            "No exposure cap configured."
        ),
    )


def _check_player_prop_lineup(
    legs: Sequence[BetLeg], lineup_confirmed: bool
) -> Optional[GuardrailCheck]:
    has_prop = any(leg.kind in PLAYER_PROP_LEGS for leg in legs)
    if not has_prop:
        return None
    ok = bool(lineup_confirmed)
    return GuardrailCheck(
        rule="player_prop_lineup",
        label="Player-prop lineup confirmed",
        passed=ok,
        severity="hard",
        detail=(
            "Lineup is confirmed; player-prop legs allowed."
            if ok else
            "Player-prop leg detected but lineup is not confirmed — "
            "do not bet until starting XI is locked."
        ),
    )


def _check_same_game_needs_price(
    *,
    legs: Sequence[BetLeg],
    same_game: bool,
    source: str,
    book_decimal_odds: Optional[float],
) -> Optional[GuardrailCheck]:
    if len(legs) < 2 or not same_game:
        return None
    has_price = source in {"live", "pasted"} and book_decimal_odds is not None
    return GuardrailCheck(
        rule="same_game_needs_price",
        label="SGP combined price required",
        passed=has_price,
        severity="hard",
        detail=(
            f"Combined book price {float(book_decimal_odds):.2f} supplied via {source}."
            if has_price else
            "Same-game parlay needs a real combined book price (live or pasted) "
            "before it can be recorded — manual odds cannot account for SGP tax."
        ),
    )


def _check_negative_clv_market(
    *,
    legs: Sequence[BetLeg],
    clv_summary: Optional[Mapping[str, object]],
    threshold: float,
    min_bets: int,
) -> Optional[GuardrailCheck]:
    if not clv_summary:
        return None
    by_market = clv_summary.get("by_market") if isinstance(clv_summary, Mapping) else None
    if not isinstance(by_market, list) or not by_market:
        return None
    # Bet's market key: single-leg uses the kind, multi-leg is 'parlay'.
    market_key = "parlay" if len(legs) > 1 else (legs[0].kind if legs else "unknown")
    bucket: Optional[Mapping[str, object]] = None
    for b in by_market:
        if isinstance(b, Mapping) and str(b.get("bucket")) == market_key:
            bucket = b
            break
    if bucket is None:
        return None
    count_with_clv = int(bucket.get("count_with_clv") or 0)
    avg = bucket.get("avg_clv_pct")
    if count_with_clv < min_bets or avg is None:
        return None
    try:
        avg_f = float(avg)
    except (TypeError, ValueError):
        return None
    ok = avg_f >= threshold
    return GuardrailCheck(
        rule="negative_clv_market",
        label="Market CLV history",
        passed=ok,
        severity="warn",
        detail=(
            f"Market '{market_key}' avg CLV {avg_f * 100:+.1f}% over "
            f"{count_with_clv} settled bets is below {threshold * 100:+.1f}% — "
            "consider whether the model is beating this market."
            if not ok else
            f"Market '{market_key}' avg CLV {avg_f * 100:+.1f}% over "
            f"{count_with_clv} settled bets is acceptable."
        ),
    )


def _parlay_rules_to_checks(
    rules: Iterable[ParlayRuleResult],
) -> List[GuardrailCheck]:
    out: List[GuardrailCheck] = []
    for r in rules:
        if r.passed:
            # Passing parlay rules are informational; we still want them on
            # the report so the dashboard can show "all green" but they
            # never block.
            continue
        out.append(GuardrailCheck(
            rule=f"parlay.{r.rule}",
            label=f"Parlay rule: {r.rule.replace('_', ' ')}",
            passed=False,
            severity=r.severity,
            detail=r.detail,
        ))
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_guardrails(
    *,
    fixture_id: str,
    legs: Sequence[BetLeg],
    placed_decimal_odds: float,
    stake_usd: float,
    source: str = "manual",
    book_decimal_odds: Optional[float] = None,
    edge: Optional[float] = None,
    same_game: bool = True,
    lineup_confirmed: bool = False,
    leg_probs: Optional[Sequence[float]] = None,
    joint_probability: Optional[float] = None,
    correlation_factor: Optional[float] = None,
    open_exposure_by_fixture: Optional[Mapping[str, float]] = None,
    clv_summary: Optional[Mapping[str, object]] = None,
    config: Optional[GuardrailConfig] = None,
) -> GuardrailReport:
    """Run every Phase 10 rule against a bet candidate.

    The arguments are deliberately plain values so callers (the FastAPI
    route, tests, future CLI tools) don't need to construct a heavy
    request object. Pass ``open_exposure_by_fixture`` as a mapping of
    fixture_id → existing open stake; pass ``clv_summary`` as the dict
    returned by :func:`SoccerStore.soccer_bets_clv_summary` (or ``None``).
    """
    cfg = config or GuardrailConfig()
    checks: List[GuardrailCheck] = []

    checks.append(_check_stake_positive(stake_usd))
    checks.append(_check_min_decimal_odds(placed_decimal_odds, cfg.min_decimal_odds))
    checks.append(_check_fixture_exposure_cap(
        stake_usd=stake_usd,
        fixture_id=fixture_id,
        open_exposure_by_fixture=open_exposure_by_fixture or {},
        cap_usd=cfg.fixture_exposure_cap_usd,
    ))
    lineup_chk = _check_player_prop_lineup(legs, lineup_confirmed)
    if lineup_chk is not None:
        checks.append(lineup_chk)
    sgp_chk = _check_same_game_needs_price(
        legs=legs, same_game=same_game, source=source,
        book_decimal_odds=book_decimal_odds,
    )
    if sgp_chk is not None:
        checks.append(sgp_chk)
    clv_chk = _check_negative_clv_market(
        legs=legs, clv_summary=clv_summary,
        threshold=cfg.negative_clv_threshold,
        min_bets=cfg.min_bets_for_clv_block,
    )
    if clv_chk is not None:
        checks.append(clv_chk)

    # Parlay rules from Phase 9 — only meaningful when 2+ legs. We need
    # leg_probs + joint_probability to invoke; if the caller did not
    # provide them we skip the check rather than guess, but we surface a
    # warn so the operator knows the parlay layer wasn't evaluated.
    parlay_rules: List[ParlayRuleResult] = []
    if len(legs) >= 2:
        if (
            leg_probs is None
            or joint_probability is None
            or correlation_factor is None
        ):
            checks.append(GuardrailCheck(
                rule="parlay.not_evaluated",
                label="Parlay rules not evaluated",
                passed=False,
                severity="warn",
                detail=(
                    "Per-leg probabilities and joint probability were not "
                    "supplied; Phase 9 parlay rules were skipped."
                ),
            ))
        else:
            parlay_rules = parlay_rule_check(
                legs=list(legs),
                leg_probs=list(leg_probs),
                joint_probability=float(joint_probability),
                correlation_factor=float(correlation_factor),
                edge=edge,
                book_decimal_odds=book_decimal_odds,
                source=source,
                lineup_confirmed=lineup_confirmed,
                same_game=same_game,
                min_high_var_edge=cfg.min_high_var_edge,
            )
            checks.extend(_parlay_rules_to_checks(parlay_rules))

    hard = sum(1 for c in checks if not c.passed and c.severity == "hard")
    warn = sum(1 for c in checks if not c.passed and c.severity == "warn")
    blocking = [c.detail for c in checks if not c.passed and c.severity == "hard"]

    return GuardrailReport(
        checks=checks,
        hard_fail_count=hard,
        warn_count=warn,
        blocking_reasons=blocking,
        parlay_rules=parlay_rules,
    )
