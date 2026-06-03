"""Cohort snapshot — derived from the persisted signal log.

We don't currently persist the cohort directly; instead we reconstruct
it from the most recent `cohort_version` in `polymarket_consensus_signals`
and the wallets that contributed to signals under that version. Good
enough for the dashboard; a dedicated `polymarket_cohort_snapshots`
table would be cleaner once a copy_runner finishes a real production
run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import List

from fastapi import APIRouter

from dashboard_v2.api.db import ro_cursor, table_exists
from dashboard_v2.api.models import CohortWallet
from src.config import settings


router = APIRouter()


@router.get("/cohort", response_model=List[CohortWallet])
def cohort() -> List[CohortWallet]:
    db = settings.db_path
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
        # First-seen timestamps for each wallet across the whole history
        first_seen_rows = cur.execute(
            "SELECT agreeing_wallets, MIN(decision_unix) AS first_seen "
            "FROM polymarket_consensus_signals GROUP BY agreeing_wallets"
        ).fetchall()
    wallet_count: Counter = Counter()
    last_seen: dict[str, int] = defaultdict(int)
    for r in rows:
        ws = (r["agreeing_wallets"] or "").split(",")
        for w in ws:
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
            last_trade_unix=last_seen.get(w, 0),
            pnl_stability=0.0,
            in_cohort_since_unix=first_seen.get(w),
        )
        for i, (w, n) in enumerate(ranked)
    ]
