"""Cohort snapshot — prefer persisted snapshots written by `copy_runner`,
fall back to deriving from signal history if no snapshots exist yet.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import List

from fastapi import APIRouter

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import CohortWallet
from src.config import settings


router = APIRouter()


def _from_snapshot(db: str) -> List[CohortWallet] | None:
    if not table_exists(db, "polymarket_cohort_snapshots"):
        return None
    with ro_cursor(db) as cur:
        latest = cur.execute(
            "SELECT cohort_version, MAX(snapshot_unix) AS ts "
            "FROM polymarket_cohort_snapshots GROUP BY cohort_version "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return None
        rows = cur.execute(
            "SELECT * FROM polymarket_cohort_snapshots WHERE cohort_version = ? "
            "ORDER BY rank ASC",
            (latest["cohort_version"],),
        ).fetchall()
        # First-seen across all snapshots for each wallet
        first_seen = {
            r["wallet"]: int(r["ts"]) for r in cur.execute(
                "SELECT wallet, MIN(snapshot_unix) AS ts "
                "FROM polymarket_cohort_snapshots GROUP BY wallet"
            ).fetchall()
        }
    return [CohortWallet(
        wallet=r["wallet"], rank=r["rank"], score=r["score"],
        realized_pnl_usd=r["realized_pnl_usd"], n_trades=r["n_trades"],
        n_resolved=r["n_resolved"], last_trade_unix=r["last_trade_unix"],
        pnl_stability=r["pnl_stability"],
        in_cohort_since_unix=first_seen.get(r["wallet"]),
    ) for r in rows]


def _from_signals(db: str) -> List[CohortWallet]:
    if not table_exists(db, "polymarket_consensus_signals"):
        return []
    with ro_cursor(db) as cur:
        latest = cur.execute(
            "SELECT cohort_version FROM polymarket_consensus_signals "
            "ORDER BY decision_unix DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return []
        ver = latest["cohort_version"]
        rows = cur.execute(
            "SELECT agreeing_wallets, decision_unix FROM polymarket_consensus_signals "
            "WHERE cohort_version = ?",
            (ver,),
        ).fetchall()
        first_seen_rows = cur.execute(
            "SELECT agreeing_wallets, MIN(decision_unix) AS first_seen "
            "FROM polymarket_consensus_signals GROUP BY agreeing_wallets"
        ).fetchall()
    wallet_count: Counter = Counter()
    last_seen: dict[str, int] = defaultdict(int)
    for r in rows:
        for w in (r["agreeing_wallets"] or "").split(","):
            wallet_count[w] += 1
            last_seen[w] = max(last_seen[w], int(r["decision_unix"]))
    first_seen: dict[str, int] = {}
    for r in first_seen_rows:
        for w in (r["agreeing_wallets"] or "").split(","):
            first_seen.setdefault(w, int(r["first_seen"]))
    ranked = sorted(wallet_count.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        CohortWallet(
            wallet=w, rank=i + 1, score=float(n),
            realized_pnl_usd=0.0, n_trades=0, n_resolved=0,
            last_trade_unix=last_seen.get(w, 0), pnl_stability=0.0,
            in_cohort_since_unix=first_seen.get(w),
        )
        for i, (w, n) in enumerate(ranked)
    ]


@router.get("/cohort", response_model=List[CohortWallet])
def cohort() -> List[CohortWallet]:
    db = settings.db_path
    snap = _from_snapshot(db)
    if snap is not None and snap:
        return snap
    return _from_signals(db)
