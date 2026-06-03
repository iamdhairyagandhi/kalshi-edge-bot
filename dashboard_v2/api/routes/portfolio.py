"""Portfolio + equity-curve endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import EquityPoint, PortfolioSnapshot
from src.config import settings


router = APIRouter()


def _venue_clause(venue: Optional[str], col: str = "venue") -> tuple[str, list]:
    if not venue or venue.lower() == "all":
        return "", []
    return f" AND {col} = ?", [venue.lower()]


@router.get("/portfolio", response_model=PortfolioSnapshot)
def portfolio(venue: Optional[str] = Query(default=None)) -> PortfolioSnapshot:
    """Aggregate paper-portfolio state derived from the trade log."""
    db = settings.db_path
    if not table_exists(db, "paper_trades"):
        return PortfolioSnapshot(
            starting_bankroll=settings.starting_bankroll,
            cash=settings.starting_bankroll, open_position_cost=0.0,
            realized_pnl=0.0, bankroll=settings.starting_bankroll,
            n_open_positions=0, n_open_kalshi=0, n_open_polymarket=0,
        )

    where, params = _venue_clause(venue)
    with ro_cursor(db) as cur:
        # Sum fees and cost flows. Buys reduce cash, sells add cash.
        agg = cur.execute(
            f"""
            SELECT
              SUM(CASE WHEN action='buy'  THEN -(cost + fees) ELSE 0 END) AS buy_outflow,
              SUM(CASE WHEN action='sell' THEN (cost - fees)  ELSE 0 END) AS sell_inflow,
              SUM(fees) AS total_fees
            FROM paper_trades WHERE 1=1 {where}
            """,
            params,
        ).fetchone()
        cash_delta = (agg["buy_outflow"] or 0) + (agg["sell_inflow"] or 0)
        # Open positions: aggregate by (venue, ticker, side) - sum buys - sum sells.
        pos_rows = cur.execute(
            f"""
            SELECT venue, ticker, side,
                SUM(CASE WHEN action='buy' THEN contracts ELSE -contracts END) AS net_contracts,
                SUM(CASE WHEN action='buy' THEN cost ELSE 0 END) AS total_buy_cost,
                SUM(CASE WHEN action='buy' THEN contracts ELSE 0 END) AS total_buy_contracts
            FROM paper_trades WHERE 1=1 {where}
            GROUP BY venue, ticker, side
            HAVING net_contracts > 0
            """,
            params,
        ).fetchall()
        realized = cur.execute(
            f"""
            SELECT
              COALESCE(SUM(CASE WHEN action='sell' THEN cost - fees ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN action='sell' THEN
                  (SELECT AVG(price)*sells.contracts FROM paper_trades buys
                    WHERE buys.venue=sells.venue AND buys.ticker=sells.ticker AND buys.side=sells.side AND buys.action='buy')
                  ELSE 0 END), 0) AS realized_pnl_approx
            FROM paper_trades sells WHERE 1=1 {where}
            """,
            params,
        ).fetchone()

    open_cost = sum(p["total_buy_cost"] * (p["net_contracts"] / max(1, p["total_buy_contracts"])) for p in pos_rows)
    n_open = len(pos_rows)
    n_kalshi = sum(1 for p in pos_rows if p["venue"] == "kalshi")
    n_poly = sum(1 for p in pos_rows if p["venue"] == "polymarket")
    cash = settings.starting_bankroll + cash_delta
    bankroll = cash + open_cost

    return PortfolioSnapshot(
        starting_bankroll=settings.starting_bankroll,
        cash=cash,
        open_position_cost=open_cost,
        realized_pnl=(realized["realized_pnl_approx"] or 0.0),
        bankroll=bankroll,
        n_open_positions=n_open,
        n_open_kalshi=n_kalshi,
        n_open_polymarket=n_poly,
    )


@router.get("/equity", response_model=List[EquityPoint])
def equity(
    venue: Optional[str] = Query(default=None),
    bucket_seconds: int = Query(default=60, ge=1, le=86400),
    limit: int = Query(default=1000, ge=1, le=10000),
) -> List[EquityPoint]:
    """Equity curve derived from cumulative cash flow over time, bucketed."""
    db = settings.db_path
    if not table_exists(db, "paper_trades"):
        return []
    where, params = _venue_clause(venue)
    with ro_cursor(db) as cur:
        rows = cur.execute(
            f"""
            SELECT strftime('%s', placed_at) AS ts,
                   CASE WHEN action='buy'  THEN -(cost + fees)
                        WHEN action='sell' THEN (cost - fees)
                        ELSE 0 END AS delta
            FROM paper_trades WHERE 1=1 {where}
            ORDER BY placed_at ASC
            """,
            params,
        ).fetchall()
    if not rows:
        return []
    bucketed: dict[int, float] = {}
    cum = settings.starting_bankroll
    for r in rows:
        ts = int(r["ts"])
        cum += r["delta"]
        b = (ts // bucket_seconds) * bucket_seconds
        bucketed[b] = cum
    pts = [EquityPoint(timestamp_unix=b, equity=v, venue=venue) for b, v in sorted(bucketed.items())]
    return pts[-limit:]
