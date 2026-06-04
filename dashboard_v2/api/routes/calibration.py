"""Calibration + Brier endpoints, backed by src.utils.calibration."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import BrierReport
from src.config import settings


router = APIRouter()


@router.get("/calibration", response_model=List[BrierReport])
def calibration() -> List[BrierReport]:
    db = settings.calibration_db_path
    if not table_exists(db, "predictions"):
        return []
    out: list[BrierReport] = []
    with ro_cursor(db) as cur:
        strategies = [r[0] for r in cur.execute(
            "SELECT DISTINCT strategy FROM predictions WHERE outcome IS NOT NULL"
        ).fetchall()]
        for strat in strategies:
            rows = cur.execute(
                "SELECT predicted_prob, outcome FROM predictions "
                "WHERE strategy = ? AND outcome IS NOT NULL",
                (strat,),
            ).fetchall()
            n = len(rows)
            if n == 0:
                continue
            brier = sum((r["predicted_prob"] - r["outcome"]) ** 2 for r in rows) / n
            out.append(BrierReport(strategy=strat, n_resolved=n, brier_score=brier))
    out.sort(key=lambda b: b.brier_score)
    return out
