"""Per-strategy kill switch backed by the same `strategy_state` table
used by src.risk.strategy_kill.

The dashboard is allowed to WRITE here (this is the one exception to
the read-only rule) so a human can manually halt/resume a strategy
from the UI. Path is gated to localhost by the uvicorn bind anyway.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import StrategyState
from src.config import settings
from src.risk.strategy_kill import SCHEMA


router = APIRouter()


class ToggleRequest(BaseModel):
    strategy: str
    enabled: bool
    reason: Optional[str] = None


def _ensure(db: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@router.get("/killswitch", response_model=List[StrategyState])
def killswitch() -> List[StrategyState]:
    db = settings.calibration_db_path
    if not table_exists(db, "strategy_state"):
        return []
    with ro_cursor(db) as cur:
        rows = cur.execute(
            "SELECT strategy, enabled, last_brier, last_n_samples, "
            "last_evaluated_at, disabled_reason FROM strategy_state ORDER BY strategy"
        ).fetchall()
    return [StrategyState(
        strategy=r["strategy"], enabled=bool(r["enabled"]),
        last_brier=r["last_brier"], last_n_samples=r["last_n_samples"],
        last_evaluated_at=r["last_evaluated_at"],
        disabled_reason=r["disabled_reason"],
    ) for r in rows]


@router.post("/killswitch", response_model=StrategyState)
def toggle(req: ToggleRequest) -> StrategyState:
    db = settings.calibration_db_path
    _ensure(db)
    conn = sqlite3.connect(db)
    try:
        if req.enabled:
            conn.execute(
                "INSERT INTO strategy_state(strategy, enabled) VALUES (?, 1) "
                "ON CONFLICT(strategy) DO UPDATE SET enabled = 1, disabled_reason = NULL",
                (req.strategy,),
            )
        else:
            conn.execute(
                """INSERT INTO strategy_state(strategy, enabled, last_evaluated_at, disabled_reason)
                   VALUES (?, 0, ?, ?)
                   ON CONFLICT(strategy) DO UPDATE SET
                     enabled = 0,
                     last_evaluated_at = excluded.last_evaluated_at,
                     disabled_reason = excluded.disabled_reason""",
                (req.strategy, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 req.reason or "manual halt from dashboard"),
            )
        conn.commit()
        row = conn.execute(
            "SELECT strategy, enabled, last_brier, last_n_samples, "
            "last_evaluated_at, disabled_reason FROM strategy_state WHERE strategy = ?",
            (req.strategy,),
        ).fetchone()
    finally:
        conn.close()
    return StrategyState(
        strategy=row[0], enabled=bool(row[1]), last_brier=row[2],
        last_n_samples=row[3], last_evaluated_at=row[4], disabled_reason=row[5],
    )
