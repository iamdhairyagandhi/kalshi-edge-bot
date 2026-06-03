"""Market metadata + orderbook proxy.

Note: live Polymarket calls may fail behind a corp network. The
frontend should handle the 503 / network-error case gracefully.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from src.clients.polymarket import PolymarketAPIError, PolymarketClient


router = APIRouter()


@router.get("/markets/polymarket/{condition_id}/book")
def polymarket_book(condition_id: str, outcome_index: int = 0) -> Dict[str, Any]:
    """Fetch the live CLOB book for a Polymarket outcome.

    Goes out to the live network. Returns 503 if unreachable.
    """
    client = PolymarketClient()
    try:
        market = client.get_market(condition_id)
        if outcome_index >= len(market.outcomes):
            raise HTTPException(404, f"outcome_index {outcome_index} out of range")
        token_id = market.outcomes[outcome_index].token_id
        book = client.get_orderbook(token_id)
        return {
            "condition_id": condition_id,
            "outcome_index": outcome_index,
            "outcome_label": market.outcomes[outcome_index].label,
            "token_id": token_id,
            "bids": book.get("bids", []),
            "asks": book.get("asks", []),
        }
    except PolymarketAPIError as e:
        raise HTTPException(503, f"polymarket unreachable: {e}")
    finally:
        client.close()
