"""Positions + fills endpoints."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import Fill, Position
from src.clients.polymarket import PolymarketAPIError, PolymarketClient
from src.config import settings


router = APIRouter()

_MARKET_STATUS_TTL_SECONDS = 60
_MARKET_STATUS_CACHE: dict[str, tuple[float, dict[str, object]]] = {}


@router.get("/positions", response_model=List[Position])
def positions(venue: Optional[str] = Query(default=None)) -> List[Position]:
    db = settings.db_path
    if not table_exists(db, "paper_trades"):
        return []
    venue_clause = ""
    params: list = []
    if venue and venue.lower() != "all":
        venue_clause = " AND p.venue = ?"
        params.append(venue.lower())
    momentum_join = ""
    momentum_fields = "NULL AS condition_id, NULL AS market_title, NULL AS outcome_index"
    if table_exists(db, "polymarket_momentum_signals"):
        momentum_join = """
            LEFT JOIN (
              SELECT
                outcome_token_id,
                MAX(condition_id) AS condition_id,
                MAX(market_question) AS market_question,
                MAX(outcome_index) AS outcome_index
              FROM polymarket_momentum_signals
              WHERE market_question IS NOT NULL
              GROUP BY outcome_token_id
            ) pm ON p.ticker = pm.outcome_token_id
        """
        momentum_fields = (
            "pm.condition_id AS condition_id, "
            "pm.market_question AS market_title, pm.outcome_index AS outcome_index"
        )

    with ro_cursor(db) as cur:
        rows = cur.execute(
            f"""
            SELECT
              MIN(p.id) AS id,
              p.venue, p.ticker, p.side,
              {momentum_fields},
              SUM(CASE WHEN p.action='buy' THEN p.contracts ELSE -p.contracts END) AS net_contracts,
              SUM(CASE WHEN p.action='buy' THEN p.cost ELSE 0 END) AS total_buy_cost,
              SUM(CASE WHEN p.action='buy' THEN p.contracts ELSE 0 END) AS total_buy_contracts,
              MIN(p.placed_at) AS opened_at,
              CASE WHEN
                SUM(CASE WHEN p.action='buy' THEN p.contracts ELSE -p.contracts END) = 0
                THEN MAX(p.placed_at) ELSE NULL END AS closed_at,
              COALESCE(SUM(CASE WHEN p.action='sell' THEN p.cost - p.fees ELSE 0 END), 0)
                - COALESCE(SUM(CASE WHEN p.action='sell' THEN p.cost ELSE 0 END), 0) AS realized_pnl
            FROM paper_trades p
            {momentum_join}
            WHERE 1=1 {venue_clause}
            GROUP BY p.venue, p.ticker, p.side
            ORDER BY opened_at DESC
            """,
            params,
        ).fetchall()
    market_metadata = _load_market_metadata(rows)
    out = []
    for r in rows:
        contracts = int(r["net_contracts"] or 0)
        if contracts <= 0:
            continue
        avg = (r["total_buy_cost"] or 0.0) / max(1, r["total_buy_contracts"] or 1)
        cost = contracts * avg
        potential_payout = float(contracts)
        condition_id = r["condition_id"]
        position_status = "closed" if r["closed_at"] else "open"
        token_metadata = market_metadata.get(str(r["ticker"]), {})
        out.append(Position(
            id=r["id"], venue=r["venue"], ticker=r["ticker"], side=r["side"],
            condition_id=condition_id,
            market_title=r["market_title"],
            market_url=token_metadata.get("url"),
            outcome_index=r["outcome_index"],
            position_status=position_status,
            market_status=token_metadata.get("status", "unknown") if condition_id else "unknown",
            contracts=contracts, avg_price=avg, cost=cost,
            current_price=token_metadata.get("current_price"),
            current_value=(
                contracts * float(token_metadata["current_price"])
                if token_metadata.get("current_price") is not None else None
            ),
            unrealized_pnl=(
                contracts * float(token_metadata["current_price"]) - cost
                if token_metadata.get("current_price") is not None else None
            ),
            potential_payout=potential_payout, max_profit=potential_payout - cost,
            opened_at=r["opened_at"],
            closed_at=r["closed_at"], realized_pnl=r["realized_pnl"] or 0.0,
        ))
    return out


def _load_market_metadata(rows) -> dict[str, dict[str, Optional[str]]]:
    token_by_condition: dict[str, str] = {}
    for r in rows:
        if r["venue"] == "polymarket" and r["condition_id"]:
            token_by_condition[str(r["ticker"])] = str(r["condition_id"])
    if not token_by_condition:
        return {}

    now = time.time()
    out: dict[str, dict[str, Optional[str]]] = {}
    missing = []
    for token_id, condition_id in sorted(token_by_condition.items()):
        cache_key = f"{condition_id}:{token_id}"
        cached = _MARKET_STATUS_CACHE.get(cache_key)
        if cached and now - cached[0] < _MARKET_STATUS_TTL_SECONDS:
            out[token_id] = cached[1]
        else:
            missing.append((token_id, condition_id))

    if not missing:
        return out

    client = PolymarketClient()
    markets = {}
    try:
        for token_id, condition_id in missing:
            cache_key = f"{condition_id}:{token_id}"
            status = "unknown"
            url = None
            current_price = None
            try:
                if condition_id not in markets:
                    markets[condition_id] = client.get_market(condition_id)
                market = markets[condition_id]
                url = _polymarket_url(market.slug)
                status = _polymarket_position_status(
                    market.closed,
                    market.accepting_orders,
                    market.end_date_iso,
                    market.outcomes,
                    token_id,
                )
                current_price = _resolved_or_live_price(client, market.closed, market.outcomes, token_id)
            except PolymarketAPIError:
                status = _polymarket_book_fallback_status(client, token_id)
                current_price = _live_sell_price(client, token_id)
            metadata = {"status": status, "url": url, "current_price": current_price}
            _MARKET_STATUS_CACHE[cache_key] = (now, metadata)
            out[token_id] = metadata
    finally:
        client.close()
    return out


def _polymarket_url(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    return f"https://polymarket.com/event/{slug}"


def _resolved_or_live_price(client: PolymarketClient, closed: bool, outcomes, token_id: str) -> Optional[float]:
    if closed:
        for outcome in outcomes:
            if outcome.token_id == token_id and outcome.price is not None:
                return float(outcome.price)
        return None
    return _live_sell_price(client, token_id)


def _live_sell_price(client: PolymarketClient, token_id: Optional[str]) -> Optional[float]:
    if not token_id:
        return None
    try:
        book = client.get_orderbook(token_id)
    except PolymarketAPIError:
        return None
    bids = book.get("bids") or []
    if not bids:
        return None
    return max(float(b["price"]) for b in bids)


def _polymarket_position_status(
    closed: bool,
    accepting_orders: bool,
    end_date_iso: Optional[str],
    outcomes,
    token_id: Optional[str],
) -> str:
    if closed:
        for outcome in outcomes:
            if token_id and outcome.token_id == token_id and outcome.price is not None:
                if outcome.price >= 0.995:
                    return "won"
                if outcome.price <= 0.005:
                    return "lost"
                return "resolved_push"
        return "resolved"
    if end_date_iso:
        try:
            end = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            if end.astimezone(timezone.utc) < datetime.now(timezone.utc):
                return "expired"
        except ValueError:
            pass
    if not accepting_orders:
        return "paused"
    return "active"


def _polymarket_book_fallback_status(client: PolymarketClient, token_id: Optional[str]) -> str:
    if not token_id:
        return "unknown"
    try:
        client.get_orderbook(token_id)
    except PolymarketAPIError:
        return "unresolved"
    return "tradable_unknown"


@router.get("/fills", response_model=List[Fill])
def fills(
    venue: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> List[Fill]:
    db = settings.db_path
    if not table_exists(db, "paper_trades"):
        return []
    venue_clause = ""
    params: list = []
    if venue and venue.lower() != "all":
        venue_clause = " AND venue = ?"
        params.append(venue.lower())
    params.append(limit)
    with ro_cursor(db) as cur:
        rows = cur.execute(
            f"""
            SELECT id, placed_at, venue, strategy, ticker, side, action,
                   contracts, price, fees, cost, is_maker, notes
            FROM paper_trades WHERE 1=1 {venue_clause}
            ORDER BY id DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [Fill(
        id=r["id"], placed_at=r["placed_at"], venue=r["venue"],
        strategy=r["strategy"], ticker=r["ticker"], side=r["side"],
        action=r["action"], contracts=r["contracts"], price=r["price"],
        fees=r["fees"], cost=r["cost"], is_maker=bool(r["is_maker"]),
        notes=r["notes"],
    ) for r in rows]
