"""
Parsers for sportsbook slip text (Bet365 today, others later).

These parsers are intentionally tolerant: real Bet365 slip text varies
between desktop / mobile / share buttons, and may include trailing UI text
like ``"Add to Betslip"`` or odds in fractional / American formats. The
parsers always return a structured ``ParsedSlip`` rather than raising on
unrecognised lines, so the API can show the user exactly what we picked up.
"""

from src.sports.soccer.parsers.bet365 import (
    ParsedBet365Slip,
    ParsedBet365Leg,
    parse_bet365_slip,
)
from src.sports.soccer.parsers.leg_matcher import (
    LegMatch,
    LegMatchResult,
    match_parsed_leg_to_betslip,
    match_parsed_slip,
)

__all__ = [
    "ParsedBet365Slip",
    "ParsedBet365Leg",
    "parse_bet365_slip",
    "LegMatch",
    "LegMatchResult",
    "match_parsed_leg_to_betslip",
    "match_parsed_slip",
]