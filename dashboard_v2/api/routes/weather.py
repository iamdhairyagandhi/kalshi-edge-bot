"""Weather specialist estimates."""

from __future__ import annotations

from fastapi import APIRouter, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import (
    WeatherEstimateRow,
    WeatherRecommendationSummary,
    WeatherSnapshot,
)
from src.config import settings


router = APIRouter()


def _row(r) -> WeatherEstimateRow:
    return WeatherEstimateRow(
        id=int(r["id"]),
        run_id=r["run_id"],
        recorded_unix=int(r["recorded_unix"]),
        venue=r["venue"],
        market_id=r["market_id"],
        title=r["title"],
        city=r["city"],
        kind=r["kind"],
        threshold=r["threshold"],
        comparator=r["comparator"],
        forecast_value=r["forecast_value"],
        sigma=r["sigma"],
        p_yes=r["p_yes"],
        yes_bid=r["yes_bid"],
        yes_ask=r["yes_ask"],
        edge_yes=r["edge_yes"],
        edge_no=r["edge_no"],
        recommendation=r["recommendation"],
        confidence=r["confidence"],
        ai_used=bool(r["ai_used"]),
        notes=r["notes"],
    )


@router.get("/weather", response_model=WeatherSnapshot)
def weather(limit: int = Query(default=100, ge=1, le=500)) -> WeatherSnapshot:
    db = settings.db_path
    if not table_exists(db, "weather_specialist_estimates"):
        return WeatherSnapshot(latest_run_id=None, latest_recorded_unix=None, summary=[], rows=[])
    with ro_cursor(db) as cur:
        latest = cur.execute(
            """SELECT run_id, recorded_unix
               FROM weather_specialist_estimates
               ORDER BY recorded_unix DESC, id DESC
               LIMIT 1"""
        ).fetchone()
        if latest is None:
            return WeatherSnapshot(latest_run_id=None, latest_recorded_unix=None, summary=[], rows=[])
        summary_rows = cur.execute(
            """SELECT recommendation, COUNT(*) AS count
               FROM weather_specialist_estimates
               WHERE run_id = ?
               GROUP BY recommendation
               ORDER BY count DESC, recommendation""",
            (latest["run_id"],),
        ).fetchall()
        rows = cur.execute(
            """SELECT *
               FROM weather_specialist_estimates
               WHERE run_id = ?
               ORDER BY
                 CASE recommendation
                   WHEN 'buy_yes' THEN 0
                   WHEN 'buy_no' THEN 1
                   ELSE 2
                 END,
                 confidence DESC,
                 MAX(COALESCE(edge_yes, -99), COALESCE(edge_no, -99)) DESC
               LIMIT ?""",
            (latest["run_id"], limit),
        ).fetchall()
    return WeatherSnapshot(
        latest_run_id=latest["run_id"],
        latest_recorded_unix=int(latest["recorded_unix"]),
        summary=[
            WeatherRecommendationSummary(
                recommendation=r["recommendation"],
                count=int(r["count"]),
            )
            for r in summary_rows
        ],
        rows=[_row(r) for r in rows],
    )
