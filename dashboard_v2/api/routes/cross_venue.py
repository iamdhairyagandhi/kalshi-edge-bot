"""Cross-venue spread diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import CrossVenueRun, CrossVenueSnapshot, CrossVenueSpreadRow
from src.config import settings


router = APIRouter()


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _run(row) -> CrossVenueRun:
    return CrossVenueRun(
        run_id=row["run_id"],
        scanned_at_unix=int(row["scanned_at_unix"]),
        kalshi_markets=int(row["kalshi_markets"]),
        kalshi_eligible_markets=int(_row_value(row, "kalshi_eligible_markets", 0) or 0),
        kalshi_excluded_mve=int(_row_value(row, "kalshi_excluded_mve", 0) or 0),
        polymarket_markets=int(row["polymarket_markets"]),
        matched_markets=int(row["matched_markets"]),
        books_checked=int(row["books_checked"]),
        candidates=int(row["candidates"]),
        min_match_score=float(row["min_match_score"]),
        min_spread=float(row["min_spread"]),
        notes=row["notes"],
    )


def _spread(row) -> CrossVenueSpreadRow:
    return CrossVenueSpreadRow(
        id=int(row["id"]),
        run_id=row["run_id"],
        scanned_at_unix=int(row["scanned_at_unix"]),
        kalshi_ticker=row["kalshi_ticker"],
        kalshi_title=row["kalshi_title"],
        polymarket_condition_id=row["polymarket_condition_id"],
        polymarket_question=row["polymarket_question"],
        polymarket_token_id=row["polymarket_token_id"],
        polymarket_outcome_index=int(row["polymarket_outcome_index"]),
        polymarket_outcome_label=row["polymarket_outcome_label"],
        match_score=float(row["match_score"]),
        kalshi_yes_bid=float(row["kalshi_yes_bid"]),
        kalshi_yes_ask=float(row["kalshi_yes_ask"]),
        polymarket_yes_bid=float(row["polymarket_yes_bid"]),
        polymarket_yes_ask=float(row["polymarket_yes_ask"]),
        valuation_spread=float(row["valuation_spread"]),
        best_executable_spread=float(row["best_executable_spread"]),
        direction=row["direction"],
        decision=row["decision"],
        notes=row["notes"],
    )


@router.get("/cross-venue", response_model=CrossVenueSnapshot)
def cross_venue(limit: int = Query(default=50, ge=1, le=500)) -> CrossVenueSnapshot:
    db = settings.db_path
    if not table_exists(db, "cross_venue_scan_runs"):
        return CrossVenueSnapshot(latest_run=None, spreads=[])
    with ro_cursor(db) as cur:
        run_row = cur.execute(
            "SELECT * FROM cross_venue_scan_runs ORDER BY scanned_at_unix DESC LIMIT 1"
        ).fetchone()
        if run_row is None:
            return CrossVenueSnapshot(latest_run=None, spreads=[])
        rows = cur.execute(
            "SELECT * FROM cross_venue_spreads WHERE run_id = ? "
            "ORDER BY best_executable_spread DESC LIMIT ?",
            (run_row["run_id"], limit),
        ).fetchall()
    return CrossVenueSnapshot(latest_run=_run(run_row), spreads=[_spread(r) for r in rows])
