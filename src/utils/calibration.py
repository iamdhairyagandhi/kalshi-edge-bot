"""
Calibration tracking.

The single most important diagnostic for any probability-based strategy
is: when we said "70%", did the event happen 70% of the time?

We log every prediction with:
  - the probability we assigned
  - the actual realized outcome (0 or 1)
  - the strategy that produced it

Then we compute:
  - Brier score (lower = better; 0.25 = random for 50/50 events)
  - Reliability diagram (bucket predictions by 10% bins, compare to realized)
  - Per-strategy calibration so we can disable miscalibrated strategies

A strategy with great edge on paper but Brier > 0.25 is just gambling.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,           -- 'YES' or 'NO'
    predicted_prob REAL NOT NULL,  -- our P(side wins)
    price_at_decision REAL NOT NULL,
    decided_at TEXT NOT NULL,      -- ISO timestamp
    resolved_at TEXT,              -- ISO timestamp when known
    outcome INTEGER,               -- 1 if side won, 0 if lost, NULL if unresolved
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_predictions_strategy ON predictions(strategy);
CREATE INDEX IF NOT EXISTS idx_predictions_ticker ON predictions(ticker);
CREATE INDEX IF NOT EXISTS idx_predictions_resolved ON predictions(resolved_at);
"""


@dataclass
class CalibrationReport:
    strategy: str
    n_resolved: int
    brier_score: float                  # mean (pred - outcome)^2
    buckets: List[Dict[str, float]]     # [{lo, hi, n, mean_pred, realized_rate}, ...]


class CalibrationStore:
    def __init__(self, db_path: str = "data/calibration.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log_prediction(
        self, *, strategy: str, ticker: str, side: str,
        predicted_prob: float, price_at_decision: float,
        decided_at: str, notes: Optional[str] = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO predictions
                   (strategy, ticker, side, predicted_prob, price_at_decision, decided_at, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (strategy, ticker, side, predicted_prob, price_at_decision, decided_at, notes),
            )
            return cur.lastrowid

    def resolve(self, prediction_id: int, outcome: int, resolved_at: str) -> None:
        if outcome not in (0, 1):
            raise ValueError("outcome must be 0 or 1")
        with self._conn() as c:
            c.execute(
                "UPDATE predictions SET outcome = ?, resolved_at = ? WHERE id = ?",
                (outcome, resolved_at, prediction_id),
            )

    def report(self, strategy: Optional[str] = None, n_buckets: int = 10) -> CalibrationReport:
        sql = "SELECT predicted_prob, outcome FROM predictions WHERE outcome IS NOT NULL"
        params: tuple = ()
        if strategy:
            sql += " AND strategy = ?"
            params = (strategy,)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()

        n = len(rows)
        if n == 0:
            return CalibrationReport(strategy or "ALL", 0, float("nan"), [])

        brier = sum((r["predicted_prob"] - r["outcome"]) ** 2 for r in rows) / n

        # Reliability buckets
        buckets: List[Dict[str, float]] = []
        width = 1.0 / n_buckets
        for i in range(n_buckets):
            lo, hi = i * width, (i + 1) * width
            in_bucket = [r for r in rows if lo <= r["predicted_prob"] < hi or (i == n_buckets - 1 and r["predicted_prob"] == 1.0)]
            if not in_bucket:
                continue
            mean_pred = sum(r["predicted_prob"] for r in in_bucket) / len(in_bucket)
            realized = sum(r["outcome"] for r in in_bucket) / len(in_bucket)
            buckets.append({
                "lo": lo, "hi": hi, "n": float(len(in_bucket)),
                "mean_pred": mean_pred, "realized_rate": realized,
            })

        return CalibrationReport(
            strategy=strategy or "ALL",
            n_resolved=n,
            brier_score=brier,
            buckets=buckets,
        )
