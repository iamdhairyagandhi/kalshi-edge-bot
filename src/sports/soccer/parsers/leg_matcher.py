"""
Match parsed Bet365 legs against model `SoccerBetslip` legs.

Given a `ParsedBet365Leg` (kind/params guess + free-text selection) and a
list of model legs from a real `SoccerBetslip`, score the best match and
return the chosen model leg (or None if nothing scores high enough).

The scoring is deliberately simple — exact kind match dominates, then
parameter equality (line / side / team / player), then fuzzy text overlap.
A confidence in ``[0, 1]`` is returned so the UI can colour the row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.sports.soccer.parsers.bet365 import ParsedBet365Leg, ParsedBet365Slip


@dataclass
class LegMatch:
    """Result of attempting to match one parsed leg against a model leg."""

    parsed_index: int
    matched_model_index: Optional[int]
    confidence: float
    reason: str
    issues: List[str] = field(default_factory=list)


@dataclass
class LegMatchResult:
    matches: List[LegMatch] = field(default_factory=list)
    # Indices of model legs that no parsed leg matched.
    unmatched_model_legs: List[int] = field(default_factory=list)
    # Indices of parsed legs that no model leg matched.
    unmatched_parsed_legs: List[int] = field(default_factory=list)
    # Overall confidence is the average per-leg confidence (or 0 if no matches).
    overall_confidence: float = 0.0
    # Total parsed legs that had a kind we recognise but no parameter match.
    ambiguous_count: int = 0


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def match_parsed_leg_to_betslip(
    parsed: ParsedBet365Leg,
    model_legs: Sequence[Dict[str, Any]],
) -> Tuple[Optional[int], float, List[str]]:
    """Score the best model-leg match for ``parsed``.

    Returns ``(best_index, confidence, issues)``. ``best_index`` is ``None``
    when no leg scores at all.
    """
    best_idx: Optional[int] = None
    best_score = 0.0
    best_issues: List[str] = []

    for i, model_leg in enumerate(model_legs):
        score, issues = _score_pair(parsed, model_leg)
        if score > best_score:
            best_score = score
            best_idx = i
            best_issues = issues

    if best_idx is None:
        return None, 0.0, ["No similar model leg found."]
    return best_idx, best_score, best_issues


def match_parsed_slip(
    parsed_slip: ParsedBet365Slip,
    model_legs: Sequence[Dict[str, Any]],
    *,
    min_confidence: float = 0.55,
) -> LegMatchResult:
    """Match every parsed leg against the model legs, greedily picking the
    highest-confidence match first and never matching the same model leg
    twice.
    """
    result = LegMatchResult()
    # Score the cartesian product, then greedy-pick.
    pairs: List[Tuple[float, int, int, List[str]]] = []
    for pi, parsed in enumerate(parsed_slip.legs):
        for mi, model in enumerate(model_legs):
            score, issues = _score_pair(parsed, model)
            if score > 0:
                pairs.append((score, pi, mi, issues))
    pairs.sort(reverse=True, key=lambda t: t[0])

    used_parsed: set = set()
    used_model: set = set()
    chosen: Dict[int, Tuple[int, float, List[str]]] = {}
    for score, pi, mi, issues in pairs:
        if pi in used_parsed or mi in used_model:
            continue
        if score < min_confidence:
            # We still record low-confidence attempts as a match suggestion
            # but mark them ambiguous.
            chosen[pi] = (mi, score, issues + ["Below confidence threshold."])
            result.ambiguous_count += 1
        else:
            chosen[pi] = (mi, score, issues)
        used_parsed.add(pi)
        used_model.add(mi)

    confidences: List[float] = []
    for pi in range(len(parsed_slip.legs)):
        if pi in chosen:
            mi, conf, issues = chosen[pi]
            result.matches.append(LegMatch(
                parsed_index=pi,
                matched_model_index=mi,
                confidence=conf,
                reason=_reason(parsed_slip.legs[pi], model_legs[mi], conf),
                issues=issues,
            ))
            confidences.append(conf)
        else:
            result.matches.append(LegMatch(
                parsed_index=pi,
                matched_model_index=None,
                confidence=0.0,
                reason="No similar model leg.",
                issues=["unmatched"],
            ))
            result.unmatched_parsed_legs.append(pi)
            confidences.append(0.0)

    for mi in range(len(model_legs)):
        if mi not in used_model:
            result.unmatched_model_legs.append(mi)

    if confidences:
        result.overall_confidence = sum(confidences) / len(confidences)
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _score_pair(parsed: ParsedBet365Leg, model_leg: Dict[str, Any]) -> Tuple[float, List[str]]:
    issues: List[str] = []
    score = 0.0
    model_kind = str(model_leg.get("kind") or "")
    model_params = dict(model_leg.get("params") or {})
    model_label = str(model_leg.get("label") or "")

    # ---- kind match (dominant signal) ----------------------------------
    if parsed.kind and parsed.kind == model_kind:
        score += 0.5
    elif parsed.kind and parsed.kind != model_kind:
        # Some categories are interchangeable in user pastes; tolerate
        # close mismatches with reduced score.
        if _related_kinds(parsed.kind, model_kind):
            score += 0.15
            issues.append(f"Market kind mismatch ({parsed.kind} vs {model_kind}); related.")
        else:
            return 0.0, [f"Market kind mismatch ({parsed.kind} vs {model_kind})."]
    elif not parsed.kind:
        # We weren't sure of the kind; only the text-overlap signal will
        # decide.
        pass

    # ---- params: line + side -------------------------------------------
    p_line = parsed.line_hint
    m_line = model_params.get("line")
    if p_line is not None and isinstance(m_line, (int, float)):
        if abs(float(m_line) - float(p_line)) < 1e-6:
            score += 0.2
        else:
            issues.append(f"Line mismatch ({p_line} vs {m_line}).")
            score -= 0.05
    elif p_line is None and isinstance(m_line, (int, float)):
        issues.append(f"Model line {m_line} not detected in slip text.")

    p_side = (parsed.side_hint or parsed.params.get("side") or "").lower()
    m_side = str(model_params.get("side") or "").lower()
    if p_side and m_side:
        if _equivalent_side(p_side, m_side):
            score += 0.15
        else:
            issues.append(f"Side mismatch ({p_side} vs {m_side}).")
            score -= 0.05

    # ---- team / player -------------------------------------------------
    if parsed.team_hint:
        team_text = _norm(parsed.team_hint)
        if team_text and (team_text in _norm(model_label) or team_text in _norm(str(model_params.get("team", "")))):
            score += 0.1
    if parsed.player_hint:
        player_text = _norm(parsed.player_hint)
        model_player = _norm(str(model_params.get("player_id", "")))
        if player_text and player_text == model_player:
            score += 0.2
        elif player_text and player_text in _norm(model_label):
            score += 0.1
        elif player_text and model_player:
            issues.append(f"Player mismatch ({parsed.player_hint} vs {model_params.get('player_id')}).")

    # ---- text overlap (cheap fuzzy) ------------------------------------
    sel = _norm(parsed.selection)
    label = _norm(model_label)
    if sel and label:
        sel_tokens = set(sel.split())
        label_tokens = set(label.split())
        if sel_tokens and label_tokens:
            jacc = len(sel_tokens & label_tokens) / max(1, len(sel_tokens | label_tokens))
            score += 0.1 * jacc

    return max(0.0, min(1.0, score)), issues


def _related_kinds(a: str, b: str) -> bool:
    families = [
        {"total_goals", "team_total"},
        {"total_cards", "team_cards"},
        {"total_corners", "team_corners"},
        {"total_shots", "team_shots"},
        {"total_shots_on_target", "team_shots_on_target"},
        {"total_fouls", "team_fouls"},
        {"anytime_scorer", "first_scorer", "last_scorer"},
        {"player_yellow", "player_red"},
    ]
    return any({a, b}.issubset(f) for f in families)


def _equivalent_side(a: str, b: str) -> bool:
    a = a.strip().lower()
    b = b.strip().lower()
    if a == b:
        return True
    eq = [
        {"o", "over"},
        {"u", "under"},
        {"y", "yes"},
        {"n", "no"},
        {"h", "home"},
        {"a", "away"},
        {"d", "draw"},
    ]
    return any({a, b}.issubset(g) for g in eq)


def _reason(parsed: ParsedBet365Leg, model_leg: Dict[str, Any], confidence: float) -> str:
    if confidence >= 0.85:
        return "Confident match on kind + params."
    if confidence >= 0.65:
        return "Likely match — verify line/side."
    if confidence >= 0.45:
        return "Possible match — check selection text."
    return "Low-confidence match; review manually."
