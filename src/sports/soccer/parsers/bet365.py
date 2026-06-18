"""
Tolerant parser for Bet365-style slip text.

The user copies a slip from Bet365 (mobile, desktop, share sheet) into a
textarea and we extract:

- the legs (selection + market + decimal odds)
- the combined decimal odds for the parlay if present
- the stake / returns if present
- the slip type (single / multi / same-game multi)

We aim to recognise the most common shapes:

  Manchester City to Win
  Match Result - Manchester City vs Liverpool
  1.65

  Over 2.5
  Total Goals
  1.80

  Same Game Multi (4)
  Manchester City to Win
  Over 2.5 Goals
  Both Teams to Score - Yes
  Erling Haaland - Anytime Goalscorer
  $20 @ 8.50  →  $170.00 Returns

  Stake: $20.00   Returns: $170.00   Odds: 8.50

Decimal odds (``1.50``..``1000``), American odds (``+150``, ``-110``) and
common fractional odds (``11/4``) are all recognised.  Anything else is
stored as a raw, unrecognised line so the UI can show it to the user for
manual review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParsedBet365Leg:
    """One leg detected in a pasted Bet365 slip."""

    selection: str
    market: Optional[str] = None
    decimal_odds: Optional[float] = None
    raw_lines: List[str] = field(default_factory=list)
    # Best-effort taxonomy mapping into the internal `BetLeg` shape.
    # ``kind`` will be one of the keys in ``LEG_PREDICATES`` when we are
    # confident, or None for "unknown" legs the user must classify.
    kind: Optional[str] = None
    params: dict = field(default_factory=dict)
    # Optional team / player extracted from the selection text.
    team_hint: Optional[str] = None
    player_hint: Optional[str] = None
    line_hint: Optional[float] = None
    side_hint: Optional[str] = None


@dataclass
class ParsedBet365Slip:
    legs: List[ParsedBet365Leg] = field(default_factory=list)
    combined_decimal_odds: Optional[float] = None
    stake: Optional[float] = None
    returns: Optional[float] = None
    slip_type: str = "single"  # "single" | "multi" | "sgp"
    unrecognised_lines: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Odds parsers
# ---------------------------------------------------------------------------


_DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")
_AMERICAN_RE = re.compile(r"^([+-])(\d{2,5})$")
_FRACTIONAL_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")


def _try_parse_odds(token: str) -> Optional[float]:
    token = token.strip().replace(",", ".")
    if not token:
        return None
    m = _DECIMAL_RE.match(token)
    if m:
        v = float(token)
        # plausibility check: decimal odds 1.01..1000
        if 1.01 <= v <= 1000.0:
            return v
        return None
    m = _AMERICAN_RE.match(token)
    if m:
        sign, mag = m.group(1), int(m.group(2))
        if sign == "+":
            return 1.0 + mag / 100.0
        else:
            return 1.0 + 100.0 / mag
    m = _FRACTIONAL_RE.match(token)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den > 0:
            return 1.0 + num / den
    return None


def _money(token: str) -> Optional[float]:
    """Parse "$20" / "£15.50" / "20.00" → 20.0."""
    s = token.strip().lstrip("$£€").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Selection → leg taxonomy
# ---------------------------------------------------------------------------


_TOTAL_RE = re.compile(r"\b(over|under)\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_CORRECT_SCORE_RE = re.compile(r"\b(\d+)\s*[-–:]\s*(\d+)\b")
_BTTS_RE = re.compile(r"\bboth teams to score\b.*?\b(yes|no)\b", re.IGNORECASE)
_PLAYER_LINE_RE = re.compile(
    r"^(?P<player>[A-Z][A-Za-zÀ-ÖØ-öø-ÿ\.'’\-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ\.'’\-]+)+)\s*[-–]\s*"
    r"(?P<rest>.+)$"
)


def _classify_selection(selection: str, market: Optional[str]) -> ParsedBet365Leg:
    """Return a partially-populated ParsedBet365Leg with kind/params guesses."""
    leg = ParsedBet365Leg(selection=selection.strip(), market=market)
    text = selection.lower()
    market_text = (market or "").lower()

    # ---- BTTS -----------------------------------------------------------
    if "both teams to score" in text or "btts" in text or "both teams to score" in market_text:
        m = _BTTS_RE.search(text)
        side = m.group(1).lower() if m else ("no" if "no" in text else "yes")
        leg.kind = "btts"
        leg.params = {"side": side}
        leg.side_hint = side
        return leg

    # ---- Correct score --------------------------------------------------
    if "correct score" in text or "correct score" in market_text:
        m = _CORRECT_SCORE_RE.search(text)
        if m:
            leg.kind = "correct_score"
            leg.params = {"home": int(m.group(1)), "away": int(m.group(2))}
        return leg

    # ---- Total goals / cards / corners / shots / fouls ------------------
    m = _TOTAL_RE.search(text)
    if m:
        side = m.group(1).lower()
        line = float(m.group(2))
        leg.line_hint = line
        leg.side_hint = side
        # Pick the kind from the market description.
        kind = None
        m_text = (market or "") + " " + selection
        m_low = m_text.lower()
        if "corner" in m_low:
            kind = "total_corners"
        elif "card" in m_low:
            kind = "total_cards"
        elif "shots on target" in m_low or "sot" in m_low:
            kind = "total_shots_on_target"
        elif "shots" in m_low:
            kind = "total_shots"
        elif "foul" in m_low:
            kind = "total_fouls"
        else:
            kind = "total_goals"
        leg.kind = kind
        leg.params = {"line": line, "side": side}
        return leg

    # ---- Player props (anytime / first / last / yellow / red) -----------
    # Look for explicit player props in the market or selection text.
    m = _PLAYER_LINE_RE.match(selection)
    player_name = None
    rest = selection
    if m:
        player_name = m.group("player").strip()
        rest = m.group("rest").lower()
    rest_lower = rest.lower()
    combined = f"{selection} {market or ''}".lower()
    if "anytime" in combined or "anytime goalscorer" in combined:
        leg.kind = "anytime_scorer"
        if player_name:
            leg.player_hint = player_name
            leg.params = {"player_id": _slugify_name(player_name)}
        return leg
    if "first goalscorer" in combined or "first scorer" in combined:
        leg.kind = "first_scorer"
        if player_name:
            leg.player_hint = player_name
            leg.params = {"player_id": _slugify_name(player_name)}
        return leg
    if "last goalscorer" in combined or "last scorer" in combined:
        leg.kind = "last_scorer"
        if player_name:
            leg.player_hint = player_name
            leg.params = {"player_id": _slugify_name(player_name)}
        return leg
    if "yellow card" in combined or "to be booked" in combined:
        leg.kind = "player_yellow"
        if player_name:
            leg.player_hint = player_name
            leg.params = {"player_id": _slugify_name(player_name)}
        return leg
    if "red card" in combined or "to be sent off" in combined:
        leg.kind = "player_red"
        if player_name:
            leg.player_hint = player_name
            leg.params = {"player_id": _slugify_name(player_name)}
        return leg

    # ---- Match result ---------------------------------------------------
    if "draw" in text or text.strip() in {"draw", "the draw"}:
        leg.kind = "match_result"
        leg.params = {"side": "D"}
        leg.side_hint = "D"
        return leg
    if "to win" in text or "match winner" in market_text or "1x2" in market_text:
        # We can't tell H vs A without team mapping, but record team hint.
        team = text.replace("to win", "").strip().title()
        leg.kind = "match_result"
        leg.team_hint = team or None
        return leg

    # Fallback: leave kind=None; UI will ask user to classify.
    return leg


def _slugify_name(name: str) -> str:
    """Generate a stable, lowercased slug for a player name; useful when
    matching against the model's ``player_id`` field (which is also derived
    from the player's surname / display name)."""
    out = name.lower()
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


_HEADER_KEYWORDS = (
    "betslip", "bet slip", "ticket", "selection", "selections",
    "add to betslip", "edit bet", "place bet", "remove",
)


_STAKE_RE = re.compile(r"stake\s*[:\-]?\s*\$?([\d.,]+)", re.IGNORECASE)
_RETURNS_RE = re.compile(r"(returns|to return|payout)\s*[:\-]?\s*\$?([\d.,]+)", re.IGNORECASE)
_ODDS_RE = re.compile(r"(odds|price)\s*[:\-]?\s*([\d.]+)", re.IGNORECASE)
_AT_RE = re.compile(r"@\s*([\d.]+)")
_SGP_RE = re.compile(r"same\s*game\s*(multi|parlay)", re.IGNORECASE)
_MULTI_RE = re.compile(r"\b(double|treble|acca|accumulator|multi|parlay)\b", re.IGNORECASE)


def parse_bet365_slip(text: str) -> ParsedBet365Slip:
    """Best-effort parser for a Bet365 slip pasted as text.

    The parser walks the text line-by-line and groups consecutive non-header
    lines into legs. A leg ends when we hit a line that looks like odds
    (decimal/American/fractional) — that becomes the leg's price. The
    immediately preceding non-blank line is the selection, and the line
    before that (if any) is the market.
    """
    if not text:
        return ParsedBet365Slip(notes=["Empty slip text."])

    slip = ParsedBet365Slip()

    # ---- collect tagged lines ------------------------------------------
    raw_lines = [ln.strip() for ln in text.splitlines()]
    # drop empty lines but keep their separating effect via groups
    groups: List[List[str]] = []
    current: List[str] = []
    for ln in raw_lines:
        if not ln:
            if current:
                groups.append(current)
                current = []
            continue
        current.append(ln)
    if current:
        groups.append(current)

    # ---- detect overall slip metadata ----------------------------------
    flat = "\n".join(raw_lines)
    if _SGP_RE.search(flat):
        slip.slip_type = "sgp"
    elif _MULTI_RE.search(flat):
        slip.slip_type = "multi"

    sm = _STAKE_RE.search(flat)
    if sm:
        slip.stake = _money(sm.group(1))
    rm = _RETURNS_RE.search(flat)
    if rm:
        slip.returns = _money(rm.group(2))
    om = _ODDS_RE.search(flat)
    if om:
        slip.combined_decimal_odds = _try_parse_odds(om.group(2))
    if slip.combined_decimal_odds is None:
        # last-resort: "@ 8.50" pattern at the bottom of multi slips
        am = _AT_RE.search(flat)
        if am:
            slip.combined_decimal_odds = _try_parse_odds(am.group(1))

    # ---- parse legs group by group --------------------------------------
    for group in groups:
        leg = _try_extract_leg_from_group(group, slip)
        if leg is not None:
            slip.legs.append(leg)

    # If we still don't have a combined price for a multi/SGP, fall back to
    # the product of per-leg prices.
    if slip.combined_decimal_odds is None and len(slip.legs) >= 2:
        product = 1.0
        complete = True
        for leg in slip.legs:
            if leg.decimal_odds is None:
                complete = False
                break
            product *= leg.decimal_odds
        if complete:
            slip.combined_decimal_odds = product
            slip.notes.append(
                "Combined price not pasted; using product of leg prices "
                "(no SGP correlation discount applied)."
            )

    # Type fallback: 2+ legs without explicit "multi" header.
    if len(slip.legs) >= 2 and slip.slip_type == "single":
        slip.slip_type = "multi"

    return slip


def _try_extract_leg_from_group(group: List[str], slip: ParsedBet365Slip) -> Optional[ParsedBet365Leg]:
    """A single 'group' is a run of consecutive non-empty lines from the
    slip text. Common patterns:

    - selection / market / odds (3 lines)
    - selection / odds          (2 lines)
    - selection                 (1 line, odds elsewhere)
    """
    # Filter out boilerplate header lines.
    lines = [ln for ln in group if not _is_header(ln)]
    if not lines:
        return None

    # Find the odds line in this group (if any).
    odds_idx = None
    odds_value = None
    for i, ln in enumerate(lines):
        # Some lines have inline odds like "Manchester City to Win   1.65".
        # Only treat as inline-odds when there are enough prefix tokens to
        # look like a real selection (avoid eating "Over 2.5" / "Under 4.5").
        parts = ln.rsplit(maxsplit=1)
        if len(parts) == 2 and not parts[0].lower().startswith(("over", "under", "o ", "u ")):
            prefix_tokens = parts[0].split()
            if len(prefix_tokens) >= 3:
                v = _try_parse_odds(parts[1])
                if v is not None:
                    odds_idx = i
                    odds_value = v
                    lines[i] = parts[0].rstrip()
                    break
        v = _try_parse_odds(ln)
        if v is not None:
            odds_idx = i
            odds_value = v
            break

    if odds_idx is None:
        # No odds in this group; treat first non-trivial line as a selection
        # but skip lines that obviously belong to summary blocks
        # ("Stake: $20", "Returns:", "Odds:", "Same Game Multi (4)").
        first = lines[0]
        if any(k in first.lower() for k in ("stake:", "returns:", "odds:", "same game multi")):
            return None
        if len(first) < 4:
            slip.unrecognised_lines.append(first)
            return None
        # Use the lone line as selection; market/odds left None.
        leg = _classify_selection(first, market=lines[1] if len(lines) > 1 else None)
        leg.raw_lines = list(group)
        return leg

    # Standard 2-3 line shape: selection / market / odds (top to bottom).
    selection = ""
    market = None
    if odds_idx >= 2:
        selection = lines[0]
        market = lines[1]
    elif odds_idx == 1:
        selection = lines[0]
    elif odds_idx == 0 and len(lines) > 1:
        # odds came first (unusual) — selection on next line.
        selection = lines[1]
    else:
        # nothing usable; record and bail.
        slip.unrecognised_lines.extend(lines)
        return None

    leg = _classify_selection(selection, market=market)
    leg.decimal_odds = odds_value
    leg.raw_lines = list(group)
    return leg


def _is_header(line: str) -> bool:
    low = line.lower().strip().rstrip(":")
    if low in _HEADER_KEYWORDS:
        return True
    # Lines like "Same Game Multi (4)" contain header keywords; keep them
    # so we can detect slip type, but they shouldn't become a leg.
    if "betslip" in low or "selection" == low or low.startswith("edit "):
        return True
    return False
