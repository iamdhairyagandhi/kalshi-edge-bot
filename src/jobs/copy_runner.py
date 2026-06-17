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
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.clients.polymarket import (
    PolymarketClient, PolymarketMarket, PolymarketOutcome, PolymarketTrade,
)
from src.config import settings
from src.jobs.diagnostics import TradeDiagnostic, record_diagnostic
from src.paper.executor import PaperExecutor
from src.signals.smart_money import WalletScore, rank_wallets, select_cohort
from src.strategies.consensus_copy import (
    ConsensusSignal, cohort_version, detect_consensus_signals,
)
from src.strategies.market_filters import (
    category_max_entry_premium,
    classify_market,
    market_filter,
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

CREATE TABLE IF NOT EXISTS polymarket_pullback_watchlist (
    idempotency_key TEXT PRIMARY KEY,
    created_unix INTEGER NOT NULL,
    expires_unix INTEGER NOT NULL,
    tier TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    outcome_token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    market_question TEXT,
    side_label TEXT NOT NULL,
    avg_wallet_entry_price REAL NOT NULL,
    target_price REAL NOT NULL,
    max_slippage_cents REAL NOT NULL,
    notional_usd REAL NOT NULL,
    status TEXT NOT NULL,
    last_checked_unix INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_pullback_watchlist_status
    ON polymarket_pullback_watchlist(status, expires_unix);
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
    candidates_with_trades: int = 0
    eligible_wallets: int = 0
    recent_cohort_trades: int = 0
    recent_conditions: int = 0
    markets_available: int = 0
    signals_known: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"cohort_size={self.cohort_size} version={self.cohort_version} "
            f"candidates={self.candidates_considered} with_trades={self.candidates_with_trades} "
            f"eligible={self.eligible_wallets} recent_trades={self.recent_cohort_trades} "
            f"recent_conditions={self.recent_conditions} markets={self.markets_available} "
            f"detected={self.signals_detected} new={self.signals_new} "
            f"known={self.signals_known} filled={self.signals_filled} "
            f"rejected={self.signals_rejected}"
            + (f" notes={self.notes}" if self.notes else "")
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
    threshold_value: Optional[float] = None,
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
    reason = decision.replace("rejected_", "") if decision.startswith("rejected_") else decision
    try:
        record_diagnostic(
            db_path,
            TradeDiagnostic(
                strategy="consensus_copy",
                venue="polymarket",
                market_id=sig.condition_id,
                market_title=sig.market_question,
                side=str(sig.outcome_index),
                decision=decision,
                reason=reason,
                metric_name="slippage_cents" if slippage_cents is not None else None,
                metric_value=slippage_cents,
                threshold_value=threshold_value if reason in {"slippage", "watch_pullback"} else None,
                observed_price=executed_price,
                reference_price=sig.avg_wallet_entry_price,
                details=notes,
            ),
        )
    except sqlite3.Error as e:
        log.warning("trade diagnostics write failed: %s", e)


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


def _cohort_wallet_weights(cohort_scores: List[WalletScore], verified_wallets: set[str]) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for i, score in enumerate(cohort_scores):
        wallet = score.wallet.lower()
        if i < 10 and wallet in verified_wallets:
            weights[wallet] = 1.5
        elif wallet in verified_wallets:
            weights[wallet] = 1.0
        else:
            weights[wallet] = 0.35
    return weights


def _negative_ev_categories(db_path: str, min_closed: int = 3, max_pnl: float = -1.0) -> set[str]:
    if not Path(db_path).exists():
        return set()
    with _conn(db_path) as c:
        tables = {
            r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "paper_trades" not in tables:
            return set()
        momentum_join = (
            "LEFT JOIN polymarket_momentum_signals ms ON t.ticker = ms.outcome_token_id"
            if "polymarket_momentum_signals" in tables else
            "LEFT JOIN (SELECT NULL AS outcome_token_id, NULL AS market_question) ms ON 0"
        )
        consensus_join = (
            "LEFT JOIN polymarket_consensus_signals cs ON t.ticker = cs.outcome_token_id"
            if "polymarket_consensus_signals" in tables else
            "LEFT JOIN (SELECT NULL AS outcome_token_id, NULL AS market_question) cs ON 0"
        )
        rows = c.execute(
            f"""
            SELECT t.ticker, t.action, t.contracts, t.price, t.cost,
                   COALESCE(ms.market_question, cs.market_question) AS title
            FROM paper_trades t
            {momentum_join}
            {consensus_join}
            WHERE t.venue='polymarket'
            """
        ).fetchall()
    by_token: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        bucket = by_token.setdefault(str(r["ticker"]), {"buy": 0.0, "sell": 0.0, "title": r["title"], "closed": False})
        if r["title"] and not bucket["title"]:
            bucket["title"] = r["title"]
        if r["action"] == "buy":
            bucket["buy"] += float(r["cost"])
        elif r["action"] == "sell":
            bucket["sell"] += float(r["cost"])
            bucket["closed"] = True

    by_category: Dict[str, Dict[str, float]] = {}
    for bucket in by_token.values():
        if not bucket["closed"]:
            continue
        category = classify_market(bucket["title"] or "")
        cat = by_category.setdefault(category, {"n": 0.0, "pnl": 0.0})
        cat["n"] += 1
        cat["pnl"] += bucket["sell"] - bucket["buy"]
    return {
        category for category, stats in by_category.items()
        if stats["n"] >= min_closed and stats["pnl"] <= max_pnl
    }


def _record_pullback_watch(
    db_path: str,
    sig: ConsensusSignal,
    *,
    tier: str,
    side_label: str,
    target_price: float,
    max_slippage_cents: float,
    notional_usd: float,
    notes: str,
) -> None:
    now = int(time.time())
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR IGNORE INTO polymarket_pullback_watchlist(
                idempotency_key, created_unix, expires_unix, tier,
                condition_id, outcome_token_id, outcome_index, market_question,
                side_label, avg_wallet_entry_price, target_price,
                max_slippage_cents, notional_usd, status, last_checked_unix, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'watching', NULL, ?)""",
            (
                sig.idempotency_key, now,
                now + settings.polymarket_pullback_expiry_minutes * 60,
                tier, sig.condition_id, sig.outcome_token_id, sig.outcome_index,
                sig.market_question, side_label, sig.avg_wallet_entry_price,
                target_price, max_slippage_cents, notional_usd, notes,
            ),
        )


def _evaluate_pullback_watchlist(
    *,
    client: PolymarketClient,
    executor: PaperExecutor,
    db_path: str,
    now_unix: int,
) -> tuple[int, int]:
    with _conn(db_path) as c:
        c.execute(
            "UPDATE polymarket_pullback_watchlist SET status='expired', last_checked_unix=? "
            "WHERE status='watching' AND expires_unix < ?",
            (now_unix, now_unix),
        )
        rows = c.execute(
            """SELECT * FROM polymarket_pullback_watchlist
               WHERE status='watching' AND expires_unix >= ?
               ORDER BY created_unix ASC LIMIT 50""",
            (now_unix,),
        ).fetchall()

    evaluated = filled = 0
    negative_categories = _negative_ev_categories(db_path)
    for row in rows:
        evaluated += 1
        try:
            book = client.get_orderbook(row["outcome_token_id"])
        except Exception:  # noqa: BLE001
            continue
        ask = _best_ask(book)
        if ask is None:
            continue
        filt = market_filter(
            row["market_question"] or "",
            live_price=ask["price"],
            negative_ev_categories=negative_categories,
        )
        if filt.blocked:
            with _conn(db_path) as c:
                c.execute(
                    "UPDATE polymarket_pullback_watchlist SET status='blocked', last_checked_unix=?, notes=? WHERE idempotency_key=?",
                    (now_unix, filt.reason, row["idempotency_key"]),
                )
            continue
        if ask["price"] > float(row["target_price"]):
            with _conn(db_path) as c:
                c.execute(
                    "UPDATE polymarket_pullback_watchlist SET last_checked_unix=? WHERE idempotency_key=?",
                    (now_unix, row["idempotency_key"]),
                )
            continue
        contracts = int(min(float(row["notional_usd"]) / max(0.01, ask["price"]), ask["size"]))
        if contracts <= 0:
            continue
        executor.execute_leg(
            strategy="consensus_pullback",
            ticker=row["outcome_token_id"],
            side=row["side_label"],
            action="buy",
            contracts=contracts,
            price=ask["price"],
            is_maker=False,
            venue="polymarket",
            notes=f"{row['tier']} pullback target={float(row['target_price']):.3f}",
        )
        with _conn(db_path) as c:
            c.execute(
                "UPDATE polymarket_pullback_watchlist SET status='filled', last_checked_unix=?, notes=? WHERE idempotency_key=?",
                (now_unix, f"filled at {ask['price']:.3f}", row["idempotency_key"]),
            )
        filled += 1
    return evaluated, filled


def _synthesize_markets_from_trades(
    trades_by_wallet: Dict[str, List[PolymarketTrade]],
    *,
    condition_ids: Sequence[str],
) -> Dict[str, PolymarketMarket]:
    """Build a minimal live-trade market map from Data API trade rows.

    The consensus detector needs condition metadata and token->outcome_index
    mapping. Recent Data API trades already carry conditionId, asset,
    outcomeIndex, title, and outcome, so using them avoids a slow storm of
    one-by-one Gamma lookups. Tradability is still checked later by fetching
    the CLOB book before any paper fill is recorded.
    """
    wanted = set(condition_ids)
    grouped: Dict[str, Dict[int, PolymarketTrade]] = {}
    title_by_cond: Dict[str, str] = {}
    for trades in trades_by_wallet.values():
        for t in trades:
            if t.condition_id not in wanted or not t.outcome_token_id:
                continue
            grouped.setdefault(t.condition_id, {})[t.outcome_index] = t
            if t.market_title:
                title_by_cond[t.condition_id] = t.market_title

    out: Dict[str, PolymarketMarket] = {}
    for cond, by_index in grouped.items():
        outcomes = [
            PolymarketOutcome(
                index=idx,
                label=t.outcome_label or ("Yes" if idx == 0 else f"Outcome {idx}"),
                token_id=t.outcome_token_id,
            )
            for idx, t in sorted(by_index.items())
        ]
        if not outcomes:
            continue
        out[cond] = PolymarketMarket(
            condition_id=cond,
            question_id=None,
            slug=None,
            question=title_by_cond.get(cond, cond),
            closed=False,
            accepting_orders=True,
            outcomes=outcomes,
        )
    return out


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
    markets: Optional[Dict[str, PolymarketMarket]],
    now_unix: int,
    candidate_trades: Optional[Dict[str, List[PolymarketTrade]]] = None,
    leaderboard_pnl_by_wallet: Optional[Dict[str, float]] = None,
    top_n: int = None,
    probe_k: int = None,
    consensus_k: int = None,
    lookback_hours: int = None,
    fresh_signal_minutes: int = None,
    probe_max_slippage_cents: float = None,
    max_slippage_cents: float = None,
    probe_notional_usd: float = None,
    notional_per_signal_usd: float = 50.0,
) -> CopyRunSummary:
    """One end-to-end pass: rank, detect, decide, (paper) execute.

    `candidate_wallets`, `resolved_condition_ids`, and `markets` are
    passed in (rather than fetched here) so the function is unit-testable
    without HTTP and so a higher-level scheduler can cache them across
    cycles.
    """
    top_n = top_n if top_n is not None else settings.polymarket_top_n
    probe_k = probe_k if probe_k is not None else settings.polymarket_probe_k
    consensus_k = consensus_k if consensus_k is not None else settings.polymarket_consensus_k
    lookback_hours = lookback_hours if lookback_hours is not None else settings.polymarket_lookback_hours
    fresh_signal_minutes = fresh_signal_minutes if fresh_signal_minutes is not None else settings.polymarket_fresh_signal_minutes
    probe_max_slippage_cents = (
        probe_max_slippage_cents
        if probe_max_slippage_cents is not None
        else settings.polymarket_probe_max_slippage_cents
    )
    max_slippage_cents = max_slippage_cents if max_slippage_cents is not None else settings.polymarket_max_slippage_cents
    probe_notional_usd = probe_notional_usd if probe_notional_usd is not None else settings.polymarket_probe_notional_usd

    _ensure_signal_table(db_path)
    pullback_evaluated, pullback_filled = _evaluate_pullback_watchlist(
        client=client,
        executor=executor,
        db_path=db_path,
        now_unix=now_unix,
    )

    # Fetch each candidate's trade history unless the CLI/scheduler already
    # did it. Store under lowercase so downstream lookups hit consistently.
    win_start = now_unix - lookback_hours * 3600
    if candidate_trades is not None:
        trades_by_candidate: Dict[str, List[PolymarketTrade]] = {
            w.lower(): list(trs or []) for w, trs in candidate_trades.items()
        }
    else:
        trades_by_candidate = {}
        for w in candidate_wallets:
            try:
                trades_by_candidate[w.lower()] = client.get_wallet_trades(w, limit=500)
            except Exception as e:  # noqa: BLE001
                log.warning("wallet %s: trades fetch failed: %s", w, e)
                trades_by_candidate[w.lower()] = []

    # 2. Rank + select cohort
    ranked = rank_wallets(
        trades_by_candidate, resolved_condition_ids,
        now_unix=now_unix,
        min_trades=settings.polymarket_min_wallet_trades,
        min_resolved=settings.polymarket_min_wallet_resolved,
    )
    cohort_scores: List[WalletScore] = select_cohort(ranked, top_n=top_n)
    verified_wallets = {s.wallet for s in cohort_scores}
    if leaderboard_pnl_by_wallet and len(cohort_scores) < top_n:
        cohort_scores = _fill_with_leaderboard_profitable_wallets(
            cohort_scores,
            ranked,
            leaderboard_pnl_by_wallet,
            top_n=top_n,
            now_unix=now_unix,
        )
    cohort_wallets = [s.wallet for s in cohort_scores]
    wallet_weights = _cohort_wallet_weights(cohort_scores, verified_wallets)
    ver = cohort_version(cohort_wallets) if cohort_wallets else "empty"
    candidates_with_trades = sum(1 for trs in trades_by_candidate.values() if trs)
    eligible_wallets = sum(1 for s in ranked if s.eligible)

    # Persist cohort snapshot so the dashboard can show true smart-money rankings.
    if cohort_scores:
        try:
            _record_cohort_snapshot(db_path, ver, cohort_scores)
        except sqlite3.Error as e:
            log.warning("cohort snapshot write failed: %s", e)

    # 3. Detect consensus signals
    min_required_k = min(probe_k, consensus_k)
    if len(cohort_wallets) < min_required_k:
        log.info("cohort too small (%d) for probe K=%d / confirm K=%d", len(cohort_wallets), probe_k, consensus_k)
        return CopyRunSummary(
            cohort_size=len(cohort_wallets), cohort_version=ver,
            candidates_considered=len(candidate_wallets),
            signals_detected=0, signals_new=0, signals_filled=0, signals_rejected=0,
            candidates_with_trades=candidates_with_trades,
            eligible_wallets=eligible_wallets,
            notes=f"cohort too small for probe K={probe_k}",
        )

    cohort_trade_map = {w: trades_by_candidate.get(w, []) for w in cohort_wallets}
    recent_cohort_trades = [
        t
        for trs in cohort_trade_map.values()
        for t in trs
        if win_start <= t.timestamp_unix <= now_unix
    ]
    recent_condition_ids = sorted({
        t.condition_id for t in recent_cohort_trades if t.condition_id
    })
    if markets is None:
        markets = _synthesize_markets_from_trades(
            cohort_trade_map,
            condition_ids=recent_condition_ids,
        )
    else:
        markets = dict(markets)
    tiered_signals: List[tuple[str, ConsensusSignal, float, float]] = []
    if probe_k < consensus_k and len(cohort_wallets) >= probe_k:
        for sig in detect_consensus_signals(
            cohort_wallets=cohort_wallets,
            cohort_trades=cohort_trade_map,
            markets=markets,
            window_start_unix=win_start, window_end_unix=now_unix,
            consensus_k=probe_k,
            wallet_weights=wallet_weights,
        ):
            tiered_signals.append(("probe", sig, probe_max_slippage_cents, probe_notional_usd))
    if len(cohort_wallets) >= consensus_k:
        for sig in detect_consensus_signals(
            cohort_wallets=cohort_wallets,
            cohort_trades=cohort_trade_map,
            markets=markets,
            window_start_unix=win_start, window_end_unix=now_unix,
            consensus_k=consensus_k,
            wallet_weights=wallet_weights,
        ):
            tiered_signals.append(("confirm", sig, max_slippage_cents, notional_per_signal_usd))
    tiered_signals.sort(key=lambda item: (0 if item[0] == "confirm" else 1, -item[1].last_trade_unix))
    fresh_cutoff = now_unix - fresh_signal_minutes * 60
    fresh_signals: List[tuple[str, ConsensusSignal, float, float]] = []
    for tier, sig, tier_slippage, tier_notional in tiered_signals:
        if sig.last_trade_unix >= fresh_cutoff:
            fresh_signals.append((tier, sig, tier_slippage, tier_notional))
            continue
        try:
            record_diagnostic(
                db_path,
                TradeDiagnostic(
                    strategy="consensus_copy",
                    venue="polymarket",
                    market_id=sig.condition_id,
                    market_title=sig.market_question,
                    side=str(sig.outcome_index),
                    decision="blocked",
                    reason="stale_signal",
                    metric_name="signal_age_seconds",
                    metric_value=float(now_unix - sig.last_trade_unix),
                    threshold_value=float(fresh_signal_minutes * 60),
                    reference_price=sig.avg_wallet_entry_price,
                    details=f"last_trade_unix={sig.last_trade_unix}",
                ),
            )
        except sqlite3.Error as e:
            log.warning("trade diagnostics write failed: %s", e)

    # 4. For each new signal, fetch live book + decide
    new = 0
    filled = 0
    rejected = 0
    known = 0
    watched = 0
    negative_categories = _negative_ev_categories(db_path)
    for tier, sig, tier_slippage, tier_notional in fresh_signals:
        if _is_known_signal(db_path, sig.idempotency_key):
            known += 1
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

        filt = market_filter(
            sig.market_question,
            live_price=ask["price"],
            negative_ev_categories=negative_categories,
        )
        if filt.blocked:
            _record_signal_decision(
                db_path, sig, decision=f"rejected_{filt.reason}",
                executed_price=ask["price"], executed_contracts=None,
                slippage_cents=None,
                notes=f"category={filt.category}",
            )
            rejected += 1
            continue

        category_slippage = category_max_entry_premium(filt.category, tier_slippage)

        # Slippage measured against the AVERAGE wallet entry, NOT the
        # latest individual fill. This is the realistic copyable price.
        slippage = ask["price"] - sig.avg_wallet_entry_price
        if slippage > category_slippage:
            side_label = "YES" if sig.outcome_index == 0 else f"OUT{sig.outcome_index}"
            _record_pullback_watch(
                db_path,
                sig,
                tier=tier,
                side_label=side_label,
                target_price=sig.avg_wallet_entry_price + settings.polymarket_pullback_entry_cents,
                max_slippage_cents=category_slippage,
                notional_usd=tier_notional,
                notes=f"category={filt.category} ask {ask['price']:.4f} > pullback target",
            )
            _record_signal_decision(
                db_path, sig, decision="watch_pullback",
                executed_price=ask["price"], executed_contracts=None,
                slippage_cents=slippage,
                threshold_value=category_slippage,
                notes=(
                    f"{tier} category={filt.category} score={filt.preference_score:.2f} "
                    f"watch until ask <= {sig.avg_wallet_entry_price + settings.polymarket_pullback_entry_cents:.4f}"
                ),
            )
            watched += 1
            continue

        # Size: notional / price, capped by displayed depth.
        size_shares = int(min(
            tier_notional / max(0.01, ask["price"]),
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
                notes=f"{tier} sig={sig.idempotency_key[:8]} cond={sig.condition_id[:10]} cohort={ver}",
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
        signals_detected=len(tiered_signals), signals_new=new,
        signals_filled=filled, signals_rejected=rejected,
        candidates_with_trades=candidates_with_trades,
        eligible_wallets=eligible_wallets,
        recent_cohort_trades=len(recent_cohort_trades),
        recent_conditions=len(recent_condition_ids),
        markets_available=len(markets),
        signals_known=known,
        notes=(
            f"pullback filled={pullback_filled}; watched={watched}"
            if pullback_filled or watched
            else
            f"{len(tiered_signals) - len(fresh_signals)} stale signal(s) filtered"
            if tiered_signals and not fresh_signals
            else
            "no recent cohort consensus"
            if not tiered_signals and recent_cohort_trades
            else "no recent cohort trades"
            if not recent_cohort_trades
            else ""
        ),
    )
    log.info("copy_runner summary: %s", summary)
    return summary


def fetch_candidate_pool(client: PolymarketClient, top_n: int = 100) -> List[str]:
    wallets, _ = fetch_candidate_pool_with_pnl(client, top_n=top_n)
    return wallets


def fetch_candidate_pool_with_pnl(client: PolymarketClient, top_n: int = 100) -> tuple[List[str], Dict[str, float]]:
    """Pull multiple leaderboard views and return unique wallets + official PnL.

    Polymarket's public leaderboard currently returns at most 50 rows per
    request, so a 50-100 wallet cohort needs a broader candidate pool than
    one leaderboard page. We seed from profitable leaderboards first, then
    add volume leaderboards as extra candidates that still must pass our
    realized-PnL eligibility checks.
    """
    pool: List[str] = []
    pnl_by_wallet: Dict[str, float] = {}
    views = [
        ("month", "profit"),
        ("week", "profit"),
        ("all", "profit"),
        ("day", "profit"),
        ("month", "volume"),
        ("week", "volume"),
        ("all", "volume"),
    ]
    for window, metric in views:
        if len(pool) >= top_n:
            break
        try:
            rows = client.get_leaderboard(window=window, metric=metric, limit=50)
        except Exception as e:  # noqa: BLE001
            log.warning("leaderboard fetch failed window=%s metric=%s: %s", window, metric, e)
            continue
        for r in rows:
            w = (r.get("wallet") or r.get("proxyWallet") or "").lower()
            try:
                pnl = float(r.get("pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                pnl = 0.0
            if w and w not in pool:
                pool.append(w)
                pnl_by_wallet[w] = max(pnl_by_wallet.get(w, float("-inf")), pnl)
                if len(pool) >= top_n:
                    break
            elif w:
                pnl_by_wallet[w] = max(pnl_by_wallet.get(w, float("-inf")), pnl)
    return pool, {w: pnl for w, pnl in pnl_by_wallet.items() if pnl != float("-inf")}


def _fill_with_leaderboard_profitable_wallets(
    cohort_scores: List[WalletScore],
    ranked: List[WalletScore],
    leaderboard_pnl_by_wallet: Dict[str, float],
    *,
    top_n: int,
    now_unix: int,
) -> List[WalletScore]:
    selected = {s.wallet for s in cohort_scores}
    by_wallet = {s.wallet: s for s in ranked}
    fallback: List[WalletScore] = []
    for wallet, official_pnl in leaderboard_pnl_by_wallet.items():
        wallet_l = wallet.lower()
        if wallet_l in selected or official_pnl <= 0:
            continue
        base = by_wallet.get(wallet_l)
        if base is None or base.n_trades <= 0 or base.last_trade_unix <= 0:
            continue
        resolved_penalty = min(1.0, max(0.1, base.n_resolved / 10.0))
        stability_penalty = max(0.1, base.pnl_stability)
        fallback.append(WalletScore(
            wallet=wallet_l,
            score=math.log1p(official_pnl) * 0.20 * resolved_penalty * stability_penalty,
            realized_pnl_usd=official_pnl,
            n_trades=base.n_trades,
            n_resolved=base.n_resolved,
            last_trade_unix=base.last_trade_unix,
            pnl_stability=base.pnl_stability,
            reasons_excluded=[],
        ))
    fallback.sort(key=lambda s: s.score, reverse=True)
    return (cohort_scores + fallback)[:top_n]


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
