"""Portfolio + equity-curve endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import EquityPoint, PortfolioSnapshot
from src.clients.polymarket import PolymarketAPIError, PolymarketClient
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
            fees_paid=0.0, realized_pnl=0.0, bankroll=settings.starting_bankroll,
            n_open_positions=0, n_open_kalshi=0, n_open_polymarket=0,
        )

    where, params = _venue_clause(venue)
    momentum_join = ""
    momentum_fields = "NULL AS condition_id"
    if table_exists(db, "polymarket_momentum_signals"):
        momentum_join = """
            LEFT JOIN (
              SELECT outcome_token_id, MAX(condition_id) AS condition_id
              FROM polymarket_momentum_signals
              GROUP BY outcome_token_id
            ) pm ON p.ticker = pm.outcome_token_id
        """
        momentum_fields = "pm.condition_id AS condition_id"

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
            SELECT p.venue, p.ticker, p.side, {momentum_fields},
                SUM(CASE WHEN p.action='buy' THEN p.contracts ELSE -p.contracts END) AS net_contracts,
                SUM(CASE WHEN p.action='buy' THEN p.cost ELSE 0 END) AS total_buy_cost,
                SUM(CASE WHEN p.action='buy' THEN p.contracts ELSE 0 END) AS total_buy_contracts
            FROM paper_trades p
            {momentum_join}
            WHERE 1=1 {where.replace("venue", "p.venue")}
            GROUP BY p.venue, p.ticker, p.side
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

    mark_prices = _load_polymarket_mark_prices(pos_rows)
    open_cost = 0.0
    open_value = 0.0
    resolved_cash = 0.0
    realized_resolution_pnl = 0.0
    open_rows = []
    for p in pos_rows:
        row_cost = p["total_buy_cost"] * (p["net_contracts"] / max(1, p["total_buy_contracts"]))
        mark = mark_prices.get(str(p["ticker"]))
        if p["venue"] == "polymarket" and mark is not None and mark["closed"]:
            payout = float(p["net_contracts"]) * float(mark["price"])
            resolved_cash += payout
            realized_resolution_pnl += payout - row_cost
            continue
        open_cost += row_cost
        open_value += (
            float(p["net_contracts"]) * float(mark["price"])
            if p["venue"] == "polymarket" and mark is not None
            else row_cost
        )
        open_rows.append(p)

    n_open = len(open_rows)
    n_kalshi = sum(1 for p in open_rows if p["venue"] == "kalshi")
    n_poly = sum(1 for p in open_rows if p["venue"] == "polymarket")
    cash = settings.starting_bankroll + cash_delta + resolved_cash
    bankroll = cash + open_value

    return PortfolioSnapshot(
        starting_bankroll=settings.starting_bankroll,
        cash=cash,
        open_position_cost=open_cost,
        fees_paid=agg["total_fees"] or 0.0,
        realized_pnl=(realized["realized_pnl_approx"] or 0.0) + realized_resolution_pnl,
        unrealized_pnl=open_value - open_cost,
        bankroll=bankroll,
        n_open_positions=n_open,
        n_open_kalshi=n_kalshi,
        n_open_polymarket=n_poly,
    )


def _load_polymarket_mark_prices(rows) -> dict[str, dict[str, float | bool]]:
    token_to_condition = {
        str(r["ticker"]): str(r["condition_id"])
        for r in rows
        if r["venue"] == "polymarket" and r["condition_id"]
    }
    if not token_to_condition:
        return {}

    out: dict[str, dict[str, float | bool]] = {}
    client = PolymarketClient()
    try:
        for token_id, condition_id in token_to_condition.items():
            try:
                market = client.get_market(condition_id)
            except PolymarketAPIError:
                continue
            if not market.closed:
                price = _polymarket_live_sell_price(client, token_id)
                if price is not None:
                    out[token_id] = {"price": price, "closed": False}
                continue
            for outcome in market.outcomes:
                if outcome.token_id == token_id and outcome.price is not None:
                    out[token_id] = {"price": float(outcome.price), "closed": True}
                    break
    finally:
        client.close()
    return out


def _polymarket_live_sell_price(client: PolymarketClient, token_id: str) -> Optional[float]:
    try:
        book = client.get_orderbook(token_id)
    except PolymarketAPIError:
        return None
    bids = book.get("bids") or []
    if not bids:
        return None
    return max(float(b["price"]) for b in bids)


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
