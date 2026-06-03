"""Read-only SQLite helpers for the dashboard API.

We never write from the dashboard. Connections are opened with
``mode=ro`` so we cannot accidentally lock the runners' write path, and
each request gets its own short-lived connection (SQLite connections
are not thread-safe across asyncio tasks).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


def ro_connect(db_path: str) -> sqlite3.Connection:
    """Open SQLite in read-only mode. Raises if the file is missing."""
    p = Path(db_path)
    if not p.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    uri = f"file:{p.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def ro_cursor(db_path: str) -> Iterator[sqlite3.Cursor]:
    conn = ro_connect(db_path)
    try:
        yield conn.cursor()
    finally:
        conn.close()


def table_exists(db_path: str, table: str) -> bool:
    try:
        with ro_cursor(db_path) as cur:
            row = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            return row is not None
    except FileNotFoundError:
        return False


def columns(db_path: str, table: str) -> list[str]:
    """Best-effort schema introspection — used by the API to render
    panels that work on old (pre-migration) DBs."""
    with ro_cursor(db_path) as cur:
        return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
