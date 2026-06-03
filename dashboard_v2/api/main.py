"""
FastAPI app for the kalshi-edge-bot dashboard.

Local-only by design: no auth, no writes, opens both paper and
calibration SQLite files in read-only mode. The runners write to the
same files concurrently; SQLite handles the read/write isolation.

Run:
    uvicorn dashboard_v2.api.main:app --reload --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dashboard_v2.api.routes import (
    cohort, markets, portfolio, positions, signals,
)
from dashboard_v2.api.stream import StreamHub, start_pollers


log = logging.getLogger("dashboard_v2")
hub = StreamHub()


def _migrate_db_if_present() -> None:
    """Bring legacy DBs up to current schema once at startup.

    The runners normally do this on construction, but the dashboard may
    be the first thing the user runs after pulling new code.
    """
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    from src.config import settings as _settings
    from src.paper.migrations import run_all as _run_all

    p = _Path(_settings.db_path)
    if not p.exists():
        return
    conn = _sqlite3.connect(str(p))
    try:
        _run_all(conn)
        conn.commit()
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _migrate_db_if_present()
    except Exception as e:  # noqa: BLE001
        log.warning("dashboard startup migration failed: %s", e)
    poll_task = asyncio.create_task(start_pollers(hub))
    try:
        yield
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="kalshi-edge-bot dashboard", lifespan=lifespan)

# Vite dev server runs on a different port; keep CORS permissive on localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(portfolio.router, prefix="/api", tags=["portfolio"])
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(signals.router, prefix="/api", tags=["signals"])
app.include_router(cohort.router, prefix="/api", tags=["cohort"])
app.include_router(markets.router, prefix="/api", tags=["markets"])


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# Mount WebSocket separately so it lives outside the /api prefix.
from dashboard_v2.api.stream import websocket_endpoint  # noqa: E402

app.add_api_websocket_route("/ws", websocket_endpoint)
app.state.hub = hub
