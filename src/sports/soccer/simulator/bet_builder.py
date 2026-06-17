"""
Bet-builder: turn an arbitrary list of legs into a fair price.

Each leg is a `BetLeg` whose `kind` selects a predicate over a `MatchSim`.
The fair joint probability is the empirical fraction of sims that satisfy
all legs simultaneously. The combined fair odds are 1/p.

We also report the leg-wise marginals and the implied joint-vs-product
correlation factor — useful to show users how much the bet builder is
exploiting (or not) the correlation books charge for.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from src.sports.soccer.types import BetBuilderQuote, BetLeg, MatchSim


LegPredicate = Callable[[MatchSim, dict], bool]


# ----------------------------------------------------------------------
# leg predicates
# ----------------------------------------------------------------------

def _match_result(sim: MatchSim, params: dict) -> bool:
    side = str(params.get("side", "H")).upper()[0]
    return sim.result == side


def _total_goals(sim: MatchSim, params: dict) -> bool:
    line = float(params.get("line", 2.5))
    side = str(params.get("side", "over")).lower()
    if side == "over":
        return sim.total_goals > line
    return sim.total_goals < line


def _btts(sim: MatchSim, params: dict) -> bool:
    side = str(params.get("side", "yes")).lower()
    return sim.btts if side == "yes" else not sim.btts


def _team_total(sim: MatchSim, params: dict) -> bool:
    line = float(params.get("line", 1.5))
    side = str(params.get("side", "over")).lower()
    team = str(params.get("team", "home")).lower()
    n = sim.home_goals if team == "home" else sim.away_goals
    if side == "over":
        return n > line
    return n < line


def _correct_score(sim: MatchSim, params: dict) -> bool:
    return sim.home_goals == int(params["home"]) and sim.away_goals == int(params["away"])


def _anytime_scorer(sim: MatchSim, params: dict) -> bool:
    return str(params["player_id"]) in sim.all_scorers


def _first_scorer(sim: MatchSim, params: dict) -> bool:
    return sim.first_scorer == str(params["player_id"])


def _last_scorer(sim: MatchSim, params: dict) -> bool:
    return sim.last_scorer == str(params["player_id"])


def _player_yellow(sim: MatchSim, params: dict) -> bool:
    pid = str(params["player_id"])
    return any(p == pid for p, _ in sim.yellow_cards)


def _player_red(sim: MatchSim, params: dict) -> bool:
    pid = str(params["player_id"])
    return any(p == pid for p, _ in sim.red_cards)


def _total_cards(sim: MatchSim, params: dict) -> bool:
    line = float(params.get("line", 4.5))
    side = str(params.get("side", "over")).lower()
    if side == "over":
        return sim.total_cards > line
    return sim.total_cards < line


LEG_PREDICATES: Dict[str, LegPredicate] = {
    "match_result": _match_result,
    "total_goals": _total_goals,
    "btts": _btts,
    "team_total": _team_total,
    "correct_score": _correct_score,
    "anytime_scorer": _anytime_scorer,
    "first_scorer": _first_scorer,
    "last_scorer": _last_scorer,
    "player_yellow": _player_yellow,
    "player_red": _player_red,
    "total_cards": _total_cards,
}


# ----------------------------------------------------------------------
# pricing
# ----------------------------------------------------------------------

def _evaluate(sim: MatchSim, leg: BetLeg) -> bool:
    pred = LEG_PREDICATES.get(leg.kind)
    if pred is None:
        raise ValueError(f"Unknown leg kind: {leg.kind}")
    return bool(pred(sim, dict(leg.params)))


def price_bet_builder(
    sims: Sequence[MatchSim],
    legs: Sequence[BetLeg],
    *,
    book_decimal_odds: Optional[float] = None,
    min_edge: float = 0.0,
    max_kelly: float = 0.25,
) -> BetBuilderQuote:
    """Price a parlay against a list of pre-computed simulations.

    Args:
        sims: output of `MatchSimulator.simulate`.
        legs: parlay legs. Empty -> trivial 1.0 fair probability.
        book_decimal_odds: combined book price for the parlay; when set,
            the quote includes edge and Kelly stake.
        min_edge: minimum decimal edge to recommend a bet.
        max_kelly: cap on Kelly fraction returned (fractional Kelly).
    """
    n = len(sims)
    if n == 0:
        return BetBuilderQuote(
            fair_probability=0.0,
            fair_decimal_odds=float("inf"),
            n_sims=0,
            leg_probabilities=[],
            independent_product=0.0,
            correlation_factor=1.0,
            book_decimal_odds=book_decimal_odds,
            recommendation="no_data",
            notes="No simulations provided",
        )

    if not legs:
        return BetBuilderQuote(
            fair_probability=1.0,
            fair_decimal_odds=1.0,
            n_sims=n,
            leg_probabilities=[],
            independent_product=1.0,
            correlation_factor=1.0,
            book_decimal_odds=book_decimal_odds,
            recommendation="no_bet",
            notes="No legs",
        )

    # Per-leg probabilities + joint
    leg_hits = [0] * len(legs)
    joint_hits = 0
    for sim in sims:
        all_pass = True
        for i, leg in enumerate(legs):
            ok = _evaluate(sim, leg)
            if ok:
                leg_hits[i] += 1
            else:
                all_pass = False
        if all_pass:
            joint_hits += 1

    leg_probs = [h / n for h in leg_hits]
    p_joint = joint_hits / n
    indep = 1.0
    for p in leg_probs:
        indep *= p
    corr_factor = (p_joint / indep) if indep > 0 else float("nan")

    fair_odds = (1.0 / p_joint) if p_joint > 0 else float("inf")

    edge: Optional[float] = None
    kelly: Optional[float] = None
    rec = "no_bet"
    notes: List[str] = []
    if book_decimal_odds is not None and book_decimal_odds > 1.0:
        edge = book_decimal_odds * p_joint - 1.0
        b = book_decimal_odds - 1.0
        if b > 0 and 0 < p_joint < 1:
            full_kelly = (b * p_joint - (1.0 - p_joint)) / b
            kelly = max(0.0, min(full_kelly, max_kelly))
        if edge > min_edge and (kelly or 0.0) > 0:
            rec = "bet"
        elif edge > 0:
            rec = "thin_edge"
            notes.append(
                f"Edge {edge:.3f} below threshold {min_edge:.3f}"
            )
        else:
            rec = "no_bet"
            notes.append("Book price implies no edge.")

    return BetBuilderQuote(
        fair_probability=p_joint,
        fair_decimal_odds=fair_odds,
        n_sims=n,
        leg_probabilities=leg_probs,
        independent_product=indep,
        correlation_factor=corr_factor,
        book_decimal_odds=book_decimal_odds,
        edge=edge,
        kelly_fraction=kelly,
        recommendation=rec,
        notes="; ".join(notes) if notes else None,
    )
