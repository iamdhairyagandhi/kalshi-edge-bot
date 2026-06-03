"""Polymarket consensus signal feed + latency histogram."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import ConsensusSignalRow, LatencyBucket
from src.config import settings


router = APIRouter()


def _row_to_signal(r) -> ConsensusSignalRow:
    wallets = (r["agreeing_wallets"] or "").split(",") if r["agreeing_wallets"] else []
    first = int(r["first_trade_unix"] or 0)
    decision_unix = int(r["decision_unix"] or 0)
    latency = max(0, decision_unix - first) if first else None
    return ConsensusSignalRow(
        idempotency_key=r["idempotency_key"], detected_at=r["detected_at"],
        condition_id=r["condition_id"], outcome_token_id=r["outcome_token_id"],
        outcome_index=r["outcome_index"], market_question=r["market_question"],
        cohort_size=r["cohort_size"], consensus_k=r["consensus_k"],
        agreeing_wallets=wallets, first_trade_unix=first,
        last_trade_unix=int(r["last_trade_unix"]),
        window_start_unix=int(r["window_start_unix"]),
        window_end_unix=int(r["window_end_unix"]),
        cohort_version=r["cohort_version"],
        avg_wallet_entry_price=float(r["avg_wallet_entry_price"]),
        total_wallet_notional_usd=float(r["total_wallet_notional_usd"]),
        decision=r["decision"], executed_price=r["executed_price"],
        executed_contracts=r["executed_contracts"],
        slippage_cents=r["slippage_cents"], decision_unix=decision_unix,
        notes=r["notes"], latency_seconds=latency,
    )


@router.get("/signals", response_model=List[ConsensusSignalRow])
def signals(
    decision: Optional[str] = Query(default=None, description="filled|rejected_slippage|rejected_no_book|rejected_other"),
    limit: int = Query(default=200, ge=1, le=2000),
) -> List[ConsensusSignalRow]:
    db = settings.db_path
    if not table_exists(db, "polymarket_consensus_signals"):
        return []
    where = ""
    params: list = []
    if decision:
        where = " WHERE decision = ?"
        params.append(decision)
    params.append(limit)
    with ro_cursor(db) as cur:
        rows = cur.execute(
            f"SELECT * FROM polymarket_consensus_signals{where} "
            f"ORDER BY decision_unix DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_signal(r) for r in rows]


@router.get("/latency", response_model=List[LatencyBucket])
def latency() -> List[LatencyBucket]:
    """Histogram buckets of (decision_unix - first_trade_unix) for filled signals."""
    db = settings.db_path
    if not table_exists(db, "polymarket_consensus_signals"):
        return []
    buckets_sec = [5, 15, 30, 60, 120, 300, 900, 3600, 10800, 86400]
    counts = [0] * len(buckets_sec)
    with ro_cursor(db) as cur:
        rows = cur.execute(
            "SELECT first_trade_unix, decision_unix FROM polymarket_consensus_signals "
            "WHERE decision='filled' AND first_trade_unix > 0"
        ).fetchall()
    for r in rows:
        latency = max(0, int(r["decision_unix"]) - int(r["first_trade_unix"]))
        for i, b in enumerate(buckets_sec):
            if latency <= b:
                counts[i] += 1
                break
    return [LatencyBucket(upper_seconds=b, count=c) for b, c in zip(buckets_sec, counts)]
