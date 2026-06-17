"""Persistent trade blocker / near-miss diagnostics.

The paper executor only records fills. This table records why a strategy
looked at a market but did not trade.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence


DDL = """
CREATE TABLE IF NOT EXISTS trade_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_unix INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_title TEXT,
    side TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    metric_name TEXT,
    metric_value REAL,
    threshold_value REAL,
    observed_price REAL,
    reference_price REAL,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_diag_time
    ON trade_diagnostics(recorded_unix DESC);
CREATE INDEX IF NOT EXISTS idx_trade_diag_reason
    ON trade_diagnostics(strategy, venue, reason);
"""


@dataclass(frozen=True)
class TradeDiagnostic:
    strategy: str
    venue: str
    market_id: str
    decision: str
    reason: str
    market_title: Optional[str] = None
    side: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    threshold_value: Optional[float] = None
    observed_price: Optional[float] = None
    reference_price: Optional[float] = None
    details: Optional[str] = None


@contextmanager
def _conn(db_path: str) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_table(db_path: str) -> None:
    with _conn(db_path) as c:
        c.executescript(DDL)


def record_diagnostics(db_path: str, rows: Sequence[TradeDiagnostic]) -> None:
    if not rows:
        return
    ensure_table(db_path)
    now = int(time.time())
    with _conn(db_path) as c:
        c.executemany(
            """INSERT INTO trade_diagnostics(
                recorded_unix, strategy, venue, market_id, market_title, side,
                decision, reason, metric_name, metric_value, threshold_value,
                observed_price, reference_price, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    now,
                    r.strategy,
                    r.venue,
                    r.market_id,
                    r.market_title,
                    r.side,
                    r.decision,
                    r.reason,
                    r.metric_name,
                    r.metric_value,
                    r.threshold_value,
                    r.observed_price,
                    r.reference_price,
                    r.details,
                )
                for r in rows
            ],
        )


def record_diagnostic(db_path: str, row: TradeDiagnostic) -> None:
    record_diagnostics(db_path, [row])
