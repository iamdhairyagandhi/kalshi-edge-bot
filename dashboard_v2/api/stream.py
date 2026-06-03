"""WebSocket multiplex + background pollers.

The runners (`arb_runner`, `copy_runner`) write to SQLite. The dashboard
backend tails those tables by rowid and broadcasts new rows to all
connected WebSocket clients. This is intentionally simple — for v1 the
poll interval is 1 s, which is fine for paper trading; live trading
would push events from the runner directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Set

from fastapi import WebSocket, WebSocketDisconnect

from dashboard_v2.api.db import ro_cursor, table_exists
from src.config import settings


log = logging.getLogger(__name__)


class StreamHub:
    """In-process fan-out of dict events to all connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        log.info("ws client connected; %d total", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        log.info("ws client disconnected; %d total", len(self._clients))

    async def broadcast(self, event: Dict[str, Any]) -> None:
        if "ts_unix" not in event:
            event["ts_unix"] = int(time.time())
        payload = json.dumps(event, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


async def websocket_endpoint(ws: WebSocket) -> None:
    """Single endpoint serving every event type — clients filter by `type`."""
    hub: StreamHub = ws.app.state.hub
    await hub.connect(ws)
    try:
        # Greet client with a heartbeat so it knows the connection is live
        # before any data arrives.
        await ws.send_text(json.dumps({"type": "heartbeat", "ts_unix": int(time.time())}))
        while True:
            # We don't expect client messages, but if any arrive, just
            # echo a heartbeat so the connection stays warm through corp
            # proxies that idle out silent connections.
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=15)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "heartbeat", "ts_unix": int(time.time())}))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)


# ---------------------------------------------------------------------------
# Pollers
# ---------------------------------------------------------------------------


async def _tail_paper_trades(hub: StreamHub, last_seen_id: int = 0) -> int:
    """Push any rows added since `last_seen_id`. Returns new high-water mark."""
    db = settings.db_path
    if not table_exists(db, "paper_trades"):
        return last_seen_id
    new_high = last_seen_id
    with ro_cursor(db) as cur:
        rows = cur.execute(
            "SELECT id, placed_at, venue, strategy, ticker, side, action, "
            "contracts, price, is_maker, fees, cost, notes "
            "FROM paper_trades WHERE id > ? ORDER BY id ASC LIMIT 200",
            (last_seen_id,),
        ).fetchall()
    for r in rows:
        await hub.broadcast({
            "type": "fill",
            "payload": {
                "id": r["id"], "placed_at": r["placed_at"], "venue": r["venue"],
                "strategy": r["strategy"], "ticker": r["ticker"], "side": r["side"],
                "action": r["action"], "contracts": r["contracts"],
                "price": r["price"], "is_maker": bool(r["is_maker"]),
                "fees": r["fees"], "cost": r["cost"], "notes": r["notes"],
            },
        })
        new_high = max(new_high, r["id"])
    return new_high


async def _tail_signals(hub: StreamHub, last_seen_unix: int = 0) -> int:
    db = settings.db_path
    if not table_exists(db, "polymarket_consensus_signals"):
        return last_seen_unix
    new_high = last_seen_unix
    with ro_cursor(db) as cur:
        rows = cur.execute(
            "SELECT * FROM polymarket_consensus_signals "
            "WHERE decision_unix > ? ORDER BY decision_unix ASC LIMIT 200",
            (last_seen_unix,),
        ).fetchall()
    for r in rows:
        d = dict(r)
        d["agreeing_wallets"] = (d.get("agreeing_wallets") or "").split(",") if d.get("agreeing_wallets") else []
        first = int(d.get("first_trade_unix") or 0)
        decision_unix = int(d.get("decision_unix") or 0)
        d["latency_seconds"] = max(0, decision_unix - first) if first else None
        await hub.broadcast({"type": "signal", "payload": d})
        new_high = max(new_high, decision_unix)
    return new_high


async def start_pollers(hub: StreamHub) -> None:
    """Background loop — never returns until cancelled."""
    last_fill_id = 0
    last_signal_unix = 0
    # Seed the high-water marks to "now" so we only stream NEW activity;
    # the snapshot REST endpoints serve historical data.
    try:
        if table_exists(settings.db_path, "paper_trades"):
            with ro_cursor(settings.db_path) as cur:
                row = cur.execute("SELECT MAX(id) FROM paper_trades").fetchone()
                last_fill_id = (row[0] or 0)
        if table_exists(settings.db_path, "polymarket_consensus_signals"):
            with ro_cursor(settings.db_path) as cur:
                row = cur.execute(
                    "SELECT MAX(decision_unix) FROM polymarket_consensus_signals"
                ).fetchone()
                last_signal_unix = (row[0] or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("poller seed failed: %s", e)

    while True:
        try:
            last_fill_id = await _tail_paper_trades(hub, last_fill_id)
            last_signal_unix = await _tail_signals(hub, last_signal_unix)
        except FileNotFoundError:
            # DB not yet created — keep polling, runner may start later.
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("poller error: %s", e)
        await asyncio.sleep(1.0)
