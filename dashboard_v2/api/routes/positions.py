"""Positions + fills endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import Fill, Position
from src.config import settings


router = APIRouter()


@router.get("/positions", response_model=List[Position])
def positions(venue: Optional[str] = Query(default=None)) -> List[Position]:
    db = settings.db_path
    if not table_exists(db, "paper_trades"):
        return []
    venue_clause = ""
    params: list = []
    if venue and venue.lower() != "all":
        venue_clause = " AND venue = ?"
        params.append(venue.lower())
    with ro_cursor(db) as cur:
        rows = cur.execute(
            f"""
            SELECT
              MIN(id) AS id,
              venue, ticker, side,
              SUM(CASE WHEN action='buy' THEN contracts ELSE -contracts END) AS net_contracts,
              SUM(CASE WHEN action='buy' THEN cost ELSE 0 END) AS total_buy_cost,
              SUM(CASE WHEN action='buy' THEN contracts ELSE 0 END) AS total_buy_contracts,
              MIN(placed_at) AS opened_at,
              CASE WHEN
                SUM(CASE WHEN action='buy' THEN contracts ELSE -contracts END) = 0
                THEN MAX(placed_at) ELSE NULL END AS closed_at,
              COALESCE(SUM(CASE WHEN action='sell' THEN cost - fees ELSE 0 END), 0)
                - COALESCE(SUM(CASE WHEN action='sell' THEN cost ELSE 0 END), 0) AS realized_pnl
            FROM paper_trades WHERE 1=1 {venue_clause}
            GROUP BY venue, ticker, side
            ORDER BY opened_at DESC
            """,
            params,
        ).fetchall()
    out = []
    for r in rows:
        contracts = int(r["net_contracts"] or 0)
        if contracts <= 0:
            continue
        avg = (r["total_buy_cost"] or 0.0) / max(1, r["total_buy_contracts"] or 1)
        out.append(Position(
            id=r["id"], venue=r["venue"], ticker=r["ticker"], side=r["side"],
            contracts=contracts, avg_price=avg, opened_at=r["opened_at"],
            closed_at=r["closed_at"], realized_pnl=r["realized_pnl"] or 0.0,
        ))
    return out


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
