"""
Convert Kalshi orderbook API responses into our internal Orderbook shape.

Kalshi orderbook response format (v2):
{
  "orderbook": {
    "yes": [[price_cents, size], [price_cents, size], ...],   # bids on YES (ASCENDING)
    "no":  [[price_cents, size], [price_cents, size], ...],   # bids on NO  (ASCENDING)
  }
}

Important quirks (verified against Kalshi/kalshi-starter-code-python and docs):

1. Both `yes` and `no` arrays are BIDS only. Asks are *derived* from the
   opposite side: yes_ask = 1 - best_no_bid.

2. Levels are sorted ASCENDING by price — the LAST element is the best bid.
   This is the opposite of most exchanges. Get it wrong and you'll trade on
   the worst price in the book.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.strategies.overround_arb import Orderbook


def _best_bid_level(levels: Optional[List[List[Any]]]) -> Optional[List[Any]]:
    """Best bid on Kalshi = highest price. Levels are sorted ascending, so
    that's the LAST element. Defensive: also sort if input is unexpected."""
    if not levels:
        return None
    # Trust the documented ordering (last = best) but verify by picking max.
    # This handles any pathological ordering without trusting the API blindly.
    try:
        return max(levels, key=lambda lv: float(lv[0]))
    except (ValueError, TypeError, IndexError):
        return levels[-1]


def _price_to_dollars(raw: Any) -> float:
    """Kalshi historically returned integer cents; post-Jan 2026 some fields
    are dollar strings like '0.420000'. Handle both."""
    if isinstance(raw, str):
        return float(raw)
    # int/float cents
    return float(raw) / 100.0


def parse_orderbook(ticker: str, payload: Dict[str, Any]) -> Optional[Orderbook]:
    """
    Build an Orderbook from a Kalshi /markets/{ticker}/orderbook response.
    Returns None if the book is empty (no bids on either side).
    """
    ob = payload.get("orderbook") or payload
    yes_bids = ob.get("yes") or []
    no_bids = ob.get("no") or []

    yes_top = _best_bid_level(yes_bids)
    no_top = _best_bid_level(no_bids)

    yes_bid_price = _price_to_dollars(yes_top[0]) if yes_top else 0.0
    yes_bid_size = int(float(yes_top[1])) if yes_top else 0
    no_bid_price = _price_to_dollars(no_top[0]) if no_top else 0.0
    no_bid_size = int(float(no_top[1])) if no_top else 0

    # Asks are derived from the opposite side's bids
    yes_ask_price = (1.0 - no_bid_price) if no_top else 1.0
    yes_ask_size = no_bid_size
    no_ask_price = (1.0 - yes_bid_price) if yes_top else 1.0
    no_ask_size = yes_bid_size

    if yes_bid_size == 0 and no_bid_size == 0:
        return None

    return Orderbook(
        ticker=ticker,
        yes_best_ask=yes_ask_price,
        yes_best_ask_size=yes_ask_size,
        no_best_ask=no_ask_price,
        no_best_ask_size=no_ask_size,
        yes_best_bid=yes_bid_price,
        yes_best_bid_size=yes_bid_size,
        no_best_bid=no_bid_price,
        no_best_bid_size=no_bid_size,
    )
