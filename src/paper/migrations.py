"""
Schema migrations for the paper-trading SQLite database.

We use a minimal homegrown migrator (no Alembic) because the schema is
small and the runtime is single-process. Each migration is idempotent
and detects its own already-applied state by inspecting `PRAGMA
table_info`. Migrations are applied in order on every `PaperExecutor`
construction; they no-op on already-migrated DBs.
"""

from __future__ import annotations

import sqlite3
from typing import List


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def add_venue_columns(conn: sqlite3.Connection) -> None:
    """
    Add a `venue` column to paper_trades and paper_positions.

    Existing rows (which all came from the Kalshi-only era) default to
    'kalshi' so the dashboard and any analytics queries continue to make
    sense after the upgrade.
    """
    for table in ("paper_trades", "paper_positions"):
        if not _table_exists(conn, table):
            continue
        if "venue" not in _columns(conn, table):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN venue TEXT NOT NULL DEFAULT 'kalshi'"
            )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_venue ON {table}(venue)"
        )


def run_all(conn: sqlite3.Connection) -> None:
    """Apply every known migration in order. Each step is idempotent."""
    add_venue_columns(conn)
