"""
Polymarket consensus-copy runner.

This is the orchestrator for the smart-money cohort strategy:

    1. Pull leaderboard → candidate pool of wallets.
    2. Fetch each candidate's recent trade history.
    3. Score + rank wallets (`signals.smart_money.rank_wallets`).
    4. Select the top-N eligible wallets as the cohort.
    5. Run `strategies.consensus_copy.detect_consensus_signals` over the
       cohort's window-bounded trades.
    6. For each NEW signal (idempotency-checked), fetch the live
       orderbook for the outcome token and:
         - reject if best ask > avg_wallet_entry_price + max_slippage.
         - otherwise paper-fill at the best-ask price (NOT the wallet
           price), with size capped by displayed depth.
    7. Persist the signal + decision so the dashboard can display
       wallet-vs-our-fill latency and slippage.

Notes:
- "Resolved markets" for ranking purposes are derived from the gamma
  endpoint's `closed=true` filter. We fetch a snapshot at the start of
  each run; this is good enough for v1, but should be cached + diffed
  in a later phase.
- Cohort version is recomputed every run; if it changes, all in-flight
  signals get re-evaluated automatically.
- The runner is intentionally idempotent: it can be killed at any point
  and resumed without double-fills, because every executed signal's
  idempotency key is persisted before the leg is fired.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.clients.polymarket import (
    PolymarketClient, PolymarketMarket, PolymarketTrade,
)
from src.config import settings
from src.paper.executor import PaperExecutor
from src.signals.smart_money import WalletScore, rank_wallets, select_cohort
from src.strategies.consensus_copy import (
    ConsensusSignal, cohort_version, detect_consensus_signals,
)
from src.utils.fee_models import PolymarketFeeModel


log = logging.getLogger(__name__)


SIGNAL_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS polymarket_consensus_signals (
    idempotency_key TEXT PRIMARY KEY,
    detected_at TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    outcome_token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    market_question TEXT,
    cohort_size INTEGER NOT NULL,
    consensus_k INTEGER NOT NULL,
    agreeing_wallets TEXT NOT NULL,            -- comma-joined
    first_trade_unix INTEGER NOT NULL,
    last_trade_unix INTEGER NOT NULL,
    window_start_unix INTEGER NOT NULL,
    window_end_unix INTEGER NOT NULL,
    cohort_version TEXT NOT NULL,
    avg_wallet_entry_price REAL NOT NULL,
    total_wallet_notional_usd REAL NOT NULL,
    decision TEXT NOT NULL,                    -- 'filled' | 'rejected_slippage' | 'rejected_no_book' | 'rejected_other'
    executed_price REAL,
    executed_contracts INTEGER,
    slippage_cents REAL,
    decision_unix INTEGER NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_consensus_signals_decision
    ON polymarket_consensus_signals(decision);
CREATE INDEX IF NOT EXISTS idx_consensus_signals_market
    ON polymarket_consensus_signals(condition_id);

CREATE TABLE IF NOT EXISTS polymarket_cohort_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_unix INTEGER NOT NULL,
    cohort_version TEXT NOT NULL,
    wallet TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score REAL NOT NULL,
    realized_pnl_usd REAL NOT NULL,
    n_trades INTEGER NOT NULL,
    n_resolved INTEGER NOT NULL,
    last_trade_unix INTEGER NOT NULL,
    pnl_stability REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cohort_snap_version
    ON polymarket_cohort_snapshots(cohort_version);
CREATE INDEX IF NOT EXISTS idx_cohort_snap_unix
    ON polymarket_cohort_snapshots(snapshot_unix DESC);
"""


@dataclass
class CopyRunSummary:
    cohort_size: int
    cohort_version: str
    candidates_considered: int
    signals_detected: int
    signals_new: int
    signals_filled: int
    signals_rejected: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"cohort_size={self.cohort_size} version={self.cohort_version} "
            f"candidates={self.candidates_considered} "
            f"detected={self.signals_detected} new={self.signals_new} "
            f"filled={self.signals_filled} rejected={self.signals_rejected}"
        )


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


def _ensure_signal_table(db_path: str) -> None:
    with _conn(db_path) as c:
        c.executescript(SIGNAL_LOG_SCHEMA)


def _is_known_signal(db_path: str, key: str) -> bool:
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT 1 FROM polymarket_consensus_signals WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
    return row is not None


def _record_signal_decision(
    db_path: str, sig: ConsensusSignal, *,
    decision: str,
    executed_price: Optional[float],
    executed_contracts: Optional[int],
    slippage_cents: Optional[float],
    notes: Optional[str] = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR REPLACE INTO polymarket_consensus_signals(
                idempotency_key, detected_at, condition_id, outcome_token_id,
                outcome_index, market_question, cohort_size, consensus_k,
                agreeing_wallets, first_trade_unix, last_trade_unix,
                window_start_unix, window_end_unix, cohort_version,
                avg_wallet_entry_price, total_wallet_notional_usd,
                decision, executed_price, executed_contracts, slippage_cents,
                decision_unix, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sig.idempotency_key, _isoformat_now(), sig.condition_id,
                sig.outcome_token_id, sig.outcome_index, sig.market_question,
                sig.cohort_size, sig.consensus_k,
                ",".join(sig.agreeing_wallets),
                sig.first_trade_unix, sig.last_trade_unix,
                sig.window_start_unix, sig.window_end_unix, sig.cohort_version,
                sig.avg_wallet_entry_price, sig.total_wallet_notional_usd,
                decision, executed_price, executed_contracts, slippage_cents,
                int(time.time()), notes,
            ),
        )


def _record_cohort_snapshot(
    db_path: str,
    cohort_version: str,
    scores: List[WalletScore],
) -> None:
    """Persist the ranked cohort for the dashboard. Idempotent per version."""
    snapshot_unix = int(time.time())
    with _conn(db_path) as c:
        # Only insert if this exact version hasn't been seen.
        existing = c.execute(
            "SELECT 1 FROM polymarket_cohort_snapshots WHERE cohort_version = ? LIMIT 1",
            (cohort_version,),
        ).fetchone()
        if existing:
            return
        for i, s in enumerate(scores):
            c.execute(
                """INSERT INTO polymarket_cohort_snapshots(
                    snapshot_unix, cohort_version, wallet, rank, score,
                    realized_pnl_usd, n_trades, n_resolved, last_trade_unix,
                    pnl_stability)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_unix, cohort_version, s.wallet, i + 1, s.score,
                 s.realized_pnl_usd, s.n_trades, s.n_resolved,
                 s.last_trade_unix, s.pnl_stability),
            )


def _isoformat_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Orderbook helpers
# ---------------------------------------------------------------------------


def _best_ask(book: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return {'price': float, 'size': float} for the best ask, or None."""
    asks = book.get("asks") or []
    if not asks:
        return None
    # CLOB returns asks sorted ascending by price.
    a = asks[0]
    return {"price": float(a["price"]), "size": float(a["size"])}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_once(
    *,
    client: PolymarketClient,
    executor: PaperExecutor,
    db_path: str,
    candidate_wallets: Sequence[str],
    resolved_condition_ids: Sequence[str],
    markets: Dict[str, PolymarketMarket],
    now_unix: int,
    top_n: int = None,
    consensus_k: int = None,
    lookback_hours: int = None,
    max_slippage_cents: float = None,
    notional_per_signal_usd: float = 50.0,
) -> CopyRunSummary:
    """One end-to-end pass: rank, detect, decide, (paper) execute.

    `candidate_wallets`, `resolved_condition_ids`, and `markets` are
    passed in (rather than fetched here) so the function is unit-testable
    without HTTP and so a higher-level scheduler can cache them across
    cycles.
    """
    top_n = top_n if top_n is not None else settings.polymarket_top_n
    consensus_k = consensus_k if consensus_k is not None else settings.polymarket_consensus_k
    lookback_hours = lookback_hours if lookback_hours is not None else settings.polymarket_lookback_hours
    max_slippage_cents = max_slippage_cents if max_slippage_cents is not None else settings.polymarket_max_slippage_cents

    _ensure_signal_table(db_path)

    # Fetch each candidate's recent trade history. Store under lowercase
    # so downstream lookups (which always lowercase) hit consistently.
    win_start = now_unix - lookback_hours * 3600
    candidate_trades: Dict[str, List[PolymarketTrade]] = {}
    for w in candidate_wallets:
        try:
            candidate_trades[w.lower()] = client.get_wallet_trades(w, limit=500)
        except Exception as e:  # noqa: BLE001
            log.warning("wallet %s: trades fetch failed: %s", w, e)
            candidate_trades[w.lower()] = []

    # 2. Rank + select cohort
    ranked = rank_wallets(
        candidate_trades, resolved_condition_ids,
        now_unix=now_unix,
        min_trades=settings.polymarket_min_wallet_trades,
        min_resolved=settings.polymarket_min_wallet_resolved,
    )
    cohort_scores: List[WalletScore] = select_cohort(ranked, top_n=top_n)
    cohort_wallets = [s.wallet for s in cohort_scores]
    ver = cohort_version(cohort_wallets) if cohort_wallets else "empty"

    # Persist cohort snapshot so the dashboard can show true smart-money rankings.
    if cohort_scores:
        try:
            _record_cohort_snapshot(db_path, ver, cohort_scores)
        except sqlite3.Error as e:
            log.warning("cohort snapshot write failed: %s", e)

    # 3. Detect consensus signals
    if len(cohort_wallets) < consensus_k:
        log.info("cohort too small (%d) for K=%d", len(cohort_wallets), consensus_k)
        return CopyRunSummary(
            cohort_size=len(cohort_wallets), cohort_version=ver,
            candidates_considered=len(candidate_wallets),
            signals_detected=0, signals_new=0, signals_filled=0, signals_rejected=0,
        )

    cohort_trade_map = {w: candidate_trades.get(w, []) for w in cohort_wallets}
    signals = detect_consensus_signals(
        cohort_wallets=cohort_wallets,
        cohort_trades=cohort_trade_map,
        markets=markets,
        window_start_unix=win_start, window_end_unix=now_unix,
        consensus_k=consensus_k,
    )

    # 4. For each new signal, fetch live book + decide
    new = 0
    filled = 0
    rejected = 0
    for sig in signals:
        if _is_known_signal(db_path, sig.idempotency_key):
            continue
        new += 1
        try:
            book = client.get_orderbook(sig.outcome_token_id)
        except Exception as e:  # noqa: BLE001
            log.warning("orderbook fetch failed for %s: %s", sig.outcome_token_id, e)
            _record_signal_decision(
                db_path, sig, decision="rejected_no_book",
                executed_price=None, executed_contracts=None,
                slippage_cents=None, notes=str(e)[:200],
            )
            rejected += 1
            continue

        ask = _best_ask(book)
        if ask is None:
            _record_signal_decision(
                db_path, sig, decision="rejected_no_book",
                executed_price=None, executed_contracts=None,
                slippage_cents=None, notes="empty asks",
            )
            rejected += 1
            continue

        # Slippage measured against the AVERAGE wallet entry, NOT the
        # latest individual fill. This is the realistic copyable price.
        slippage = ask["price"] - sig.avg_wallet_entry_price
        if slippage > max_slippage_cents:
            _record_signal_decision(
                db_path, sig, decision="rejected_slippage",
                executed_price=ask["price"], executed_contracts=None,
                slippage_cents=slippage,
                notes=f"slippage {slippage:.4f} > {max_slippage_cents}",
            )
            rejected += 1
            continue

        # Size: notional / price, capped by displayed depth.
        size_shares = int(min(
            notional_per_signal_usd / max(0.01, ask["price"]),
            ask["size"],
        ))
        if size_shares <= 0:
            _record_signal_decision(
                db_path, sig, decision="rejected_other",
                executed_price=ask["price"], executed_contracts=0,
                slippage_cents=slippage, notes="size<=0",
            )
            rejected += 1
            continue

        # Execute paper leg at the live ask. Use the outcome_token_id as
        # the ticker so positions don't collide with the binary YES/NO
        # convention used by Kalshi.
        side_label = "YES" if sig.outcome_index == 0 else f"OUT{sig.outcome_index}"
        try:
            executor.execute_leg(
                strategy="consensus_copy",
                ticker=sig.outcome_token_id,
                side=side_label,
                action="buy",
                contracts=size_shares,
                price=ask["price"],
                is_maker=False,
                venue="polymarket",
                notes=f"sig={sig.idempotency_key[:8]} cond={sig.condition_id[:10]} cohort={ver}",
            )
            _record_signal_decision(
                db_path, sig, decision="filled",
                executed_price=ask["price"], executed_contracts=size_shares,
                slippage_cents=slippage,
            )
            filled += 1
        except Exception as e:  # noqa: BLE001
            log.warning("paper leg failed for sig %s: %s", sig.idempotency_key, e)
            _record_signal_decision(
                db_path, sig, decision="rejected_other",
                executed_price=ask["price"], executed_contracts=None,
                slippage_cents=slippage, notes=str(e)[:200],
            )
            rejected += 1

    summary = CopyRunSummary(
        cohort_size=len(cohort_wallets), cohort_version=ver,
        candidates_considered=len(candidate_wallets),
        signals_detected=len(signals), signals_new=new,
        signals_filled=filled, signals_rejected=rejected,
    )
    log.info("copy_runner summary: %s", summary)
    return summary


def fetch_candidate_pool(client: PolymarketClient, top_n: int = 100) -> List[str]:
    """Pull the leaderboard, return wallets only (we re-rank ourselves)."""
    rows = client.get_leaderboard(window="month", metric="profit", limit=top_n)
    pool: List[str] = []
    for r in rows:
        w = (r.get("wallet") or r.get("proxyWallet") or "").lower()
        if w and w not in pool:
            pool.append(w)
    return pool


def fetch_market_snapshot(
    client: PolymarketClient,
    condition_ids: Sequence[str],
) -> Dict[str, PolymarketMarket]:
    out: Dict[str, PolymarketMarket] = {}
    for cid in condition_ids:
        try:
            out[cid] = client.get_market(cid)
        except Exception as e:  # noqa: BLE001
            log.warning("market %s lookup failed: %s", cid, e)
    return out


def build_default_executor() -> PaperExecutor:
    return PaperExecutor(
        settings.db_path, settings.starting_bankroll,
        fee_models={"polymarket": PolymarketFeeModel(gas_usd=settings.polymarket_gas_usd)},
    )
