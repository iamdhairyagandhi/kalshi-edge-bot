"""
Brier-based strategy kill switch.

Policy: every strategy logs predictions via src.utils.calibration.CalibrationStore.
After at least MIN_SAMPLES_BEFORE_EVALUATING predictions have resolved,
we compute Brier; if > MAX_BRIER, we disable the strategy.

We persist enable/disable state in the calibration sqlite db (same file).

Brier:  BS = (1/N) Σ (pred - outcome)²
    0.00 = perfect; 0.25 = coin flip; >0.25 = worse than random.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from src.utils.calibration import CalibrationStore


MIN_SAMPLES_BEFORE_EVALUATING = 30
MAX_BRIER = 0.25


SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_state (
    strategy TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_brier REAL,
    last_n_samples INTEGER,
    last_evaluated_at TEXT,
    disabled_reason TEXT
);
"""


@dataclass
class StrategyStatus:
    strategy: str
    enabled: bool
    last_brier: Optional[float]
    last_n_samples: Optional[int]
    disabled_reason: Optional[str]


@contextmanager
def _conn(db_path: str) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_enabled(db_path: str, strategy: str) -> bool:
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT enabled FROM strategy_state WHERE strategy = ?", (strategy,)
        ).fetchone()
        return True if row is None else bool(row[0])


def get_status(db_path: str, strategy: str) -> StrategyStatus:
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT enabled, last_brier, last_n_samples, disabled_reason "
            "FROM strategy_state WHERE strategy = ?", (strategy,)
        ).fetchone()
    if row is None:
        return StrategyStatus(strategy, True, None, None, None)
    return StrategyStatus(strategy, bool(row[0]), row[1], row[2], row[3])


def disable(db_path: str, strategy: str, reason: str,
             brier: Optional[float] = None, n: Optional[int] = None) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT INTO strategy_state(strategy, enabled, last_brier,
                  last_n_samples, last_evaluated_at, disabled_reason)
               VALUES (?, 0, ?, ?, ?, ?)
               ON CONFLICT(strategy) DO UPDATE SET
                  enabled = 0, last_brier = excluded.last_brier,
                  last_n_samples = excluded.last_n_samples,
                  last_evaluated_at = excluded.last_evaluated_at,
                  disabled_reason = excluded.disabled_reason""",
            (strategy, brier, n, _now_iso(), reason),
        )


def enable(db_path: str, strategy: str) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT INTO strategy_state(strategy, enabled)
               VALUES (?, 1)
               ON CONFLICT(strategy) DO UPDATE SET
                  enabled = 1, disabled_reason = NULL""",
            (strategy,),
        )


def evaluate_and_maybe_kill(
    db_path: str,
    strategy: str,
    *,
    min_samples: int = MIN_SAMPLES_BEFORE_EVALUATING,
    max_brier: float = MAX_BRIER,
) -> StrategyStatus:
    """Recompute Brier from resolved predictions; disable if over limit."""
    store = CalibrationStore(db_path)
    rep = store.report(strategy=strategy)
    n = rep.n_resolved
    if n < min_samples:
        return get_status(db_path, strategy)

    brier = rep.brier_score
    with _conn(db_path) as c:
        c.execute(
            """INSERT INTO strategy_state(strategy, enabled, last_brier,
                  last_n_samples, last_evaluated_at, disabled_reason)
               VALUES (?, 1, ?, ?, ?, NULL)
               ON CONFLICT(strategy) DO UPDATE SET
                  last_brier = excluded.last_brier,
                  last_n_samples = excluded.last_n_samples,
                  last_evaluated_at = excluded.last_evaluated_at""",
            (strategy, brier, n, _now_iso()),
        )
    if brier > max_brier:
        disable(db_path, strategy,
                reason=f"Brier {brier:.4f} > {max_brier} (n={n})",
                brier=brier, n=n)
    return get_status(db_path, strategy)
