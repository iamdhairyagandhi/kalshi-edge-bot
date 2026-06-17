"""Trade blocker and near-miss diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import (
    TradeDiagnosticRow,
    TradeDiagnosticSnapshot,
    TradeDiagnosticSummary,
)
from src.config import settings


router = APIRouter()


def _row(r) -> TradeDiagnosticRow:
    return TradeDiagnosticRow(
        id=int(r["id"]),
        recorded_unix=int(r["recorded_unix"]),
        strategy=r["strategy"],
        venue=r["venue"],
        market_id=r["market_id"],
        market_title=r["market_title"],
        side=r["side"],
        decision=r["decision"],
        reason=r["reason"],
        metric_name=r["metric_name"],
        metric_value=r["metric_value"],
        threshold_value=r["threshold_value"],
        observed_price=r["observed_price"],
        reference_price=r["reference_price"],
        details=r["details"],
    )


@router.get("/diagnostics", response_model=TradeDiagnosticSnapshot)
def diagnostics(limit: int = Query(default=100, ge=1, le=1000)) -> TradeDiagnosticSnapshot:
    db = settings.db_path
    if not table_exists(db, "trade_diagnostics"):
        return TradeDiagnosticSnapshot(summary=[], rows=[])
    with ro_cursor(db) as cur:
        summary_rows = cur.execute(
            """SELECT strategy, venue, reason, COUNT(*) AS count
               FROM trade_diagnostics
               GROUP BY strategy, venue, reason
               ORDER BY count DESC, strategy, venue, reason"""
        ).fetchall()
        rows = cur.execute(
            "SELECT * FROM trade_diagnostics ORDER BY recorded_unix DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return TradeDiagnosticSnapshot(
        summary=[
            TradeDiagnosticSummary(
                strategy=r["strategy"],
                venue=r["venue"],
                reason=r["reason"],
                count=int(r["count"]),
            )
            for r in summary_rows
        ],
        rows=[_row(r) for r in rows],
    )
