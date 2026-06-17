"""Polymarket fresh momentum runner."""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from src.clients.polymarket import PolymarketClient, PolymarketMarket, PolymarketTrade
from src.config import settings
from src.jobs.copy_runner import _synthesize_markets_from_trades
from src.jobs.diagnostics import TradeDiagnostic, record_diagnostic
from src.paper.executor import PaperExecutor
from src.strategies.market_filters import category_max_entry_premium, market_filter
from src.strategies.polymarket_momentum import MomentumSignal, detect_momentum_signals
from src.utils.fee_models import PolymarketFeeModel


log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS polymarket_momentum_signals (
    idempotency_key TEXT PRIMARY KEY,
    detected_at_unix INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    outcome_token_id TEXT NOT NULL,
    outcome_index INTEGER NOT NULL,
    market_question TEXT,
    window_start_unix INTEGER NOT NULL,
    window_end_unix INTEGER NOT NULL,
    buy_trades INTEGER NOT NULL,
    unique_wallets INTEGER NOT NULL,
    buy_notional_usd REAL NOT NULL,
    vwap REAL NOT NULL,
    price_move REAL NOT NULL,
    decision TEXT NOT NULL,
    executed_price REAL,
    executed_contracts INTEGER,
    entry_premium REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_momentum_signals_decision
    ON polymarket_momentum_signals(decision);
"""


@dataclass(frozen=True)
class MomentumRunSummary:
    candidates_considered: int
    markets_available: int
    signals_detected: int
    signals_new: int
    signals_known: int
    signals_filled: int
    signals_rejected: int
    signals_observed: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"candidates={self.candidates_considered} markets={self.markets_available} "
            f"detected={self.signals_detected} new={self.signals_new} "
            f"known={self.signals_known} filled={self.signals_filled} "
            f"rejected={self.signals_rejected} observed={self.signals_observed}"
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


def _ensure_table(db_path: str) -> None:
    with _conn(db_path) as c:
        c.executescript(SCHEMA)


def _is_known(db_path: str, key: str) -> bool:
    with _conn(db_path) as c:
        return c.execute(
            "SELECT 1 FROM polymarket_momentum_signals WHERE idempotency_key = ?",
            (key,),
        ).fetchone() is not None


def _best_ask(book: Dict[str, Any]) -> Optional[Dict[str, float]]:
    asks = book.get("asks") or []
    if not asks:
        return None
    best = min(asks, key=lambda a: float(a["price"]))
    return {"price": float(best["price"]), "size": float(best["size"])}


def _record(
    db_path: str,
    sig: MomentumSignal,
    *,
    decision: str,
    executed_price: Optional[float],
    executed_contracts: Optional[int],
    entry_premium: Optional[float],
    notes: Optional[str],
    premium_threshold: Optional[float] = None,
    category: Optional[str] = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR REPLACE INTO polymarket_momentum_signals(
                idempotency_key, detected_at_unix, condition_id, outcome_token_id,
                outcome_index, market_question, window_start_unix, window_end_unix,
                buy_trades, unique_wallets, buy_notional_usd, vwap, price_move,
                decision, executed_price, executed_contracts, entry_premium, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig.idempotency_key, int(time.time()), sig.condition_id,
                sig.outcome_token_id, sig.outcome_index, sig.market_question,
                sig.window_start_unix, sig.window_end_unix,
                sig.buy_trades, sig.unique_wallets, sig.buy_notional_usd,
                sig.vwap, sig.price_move, decision, executed_price,
                executed_contracts, entry_premium, notes,
            ),
        )
    if decision != "filled":
        reason = decision.replace("rejected_", "")
        try:
            record_diagnostic(
                db_path,
                TradeDiagnostic(
                    strategy="polymarket_momentum",
                    venue="polymarket",
                    market_id=sig.condition_id,
                    market_title=sig.market_question,
                    side=str(sig.outcome_index),
                    decision=decision,
                    reason=reason,
                    metric_name="entry_premium" if entry_premium is not None else None,
                    metric_value=entry_premium,
                    threshold_value=premium_threshold if reason == "premium" else None,
                    observed_price=executed_price,
                    reference_price=sig.vwap,
                    details=f"category={category} {notes or ''}".strip() if category else notes,
                ),
            )
        except sqlite3.Error as e:
            log.warning("trade diagnostics write failed: %s", e)


def run_once(
    *,
    client: PolymarketClient,
    executor: PaperExecutor,
    db_path: str,
    candidate_wallets: Sequence[str],
    candidate_trades: Optional[Dict[str, List[PolymarketTrade]]],
    now_unix: int,
    lookback_minutes: int = None,
    min_buy_trades: int = None,
    min_unique_wallets: int = None,
    min_notional_usd: float = None,
    min_price_move: float = None,
    max_entry_premium: float = None,
    notional_per_signal_usd: float = 10.0,
    execute: Optional[bool] = None,
    exclude_terms: Optional[str] = None,
    require_positive_history: bool = True,
) -> MomentumRunSummary:
    lookback_minutes = lookback_minutes if lookback_minutes is not None else settings.polymarket_momentum_lookback_minutes
    min_buy_trades = min_buy_trades if min_buy_trades is not None else settings.polymarket_momentum_min_buy_trades
    min_unique_wallets = min_unique_wallets if min_unique_wallets is not None else settings.polymarket_momentum_min_wallets
    min_notional_usd = min_notional_usd if min_notional_usd is not None else settings.polymarket_momentum_min_notional_usd
    min_price_move = min_price_move if min_price_move is not None else settings.polymarket_momentum_min_price_move
    max_entry_premium = max_entry_premium if max_entry_premium is not None else settings.polymarket_momentum_max_entry_premium
    execute = settings.polymarket_momentum_execute if execute is None else execute
    excluded_terms = _parse_exclude_terms(
        settings.polymarket_momentum_exclude_terms if exclude_terms is None else exclude_terms
    )

    _ensure_table(db_path)
    trades_by_wallet: Dict[str, List[PolymarketTrade]] = {}
    if candidate_trades is not None:
        trades_by_wallet = {w.lower(): list(trs or []) for w, trs in candidate_trades.items()}
    else:
        for w in candidate_wallets:
            try:
                trades_by_wallet[w.lower()] = client.get_wallet_trades(w, limit=500)
            except Exception as e:  # noqa: BLE001
                log.warning("wallet %s: trades fetch failed: %s", w, e)
                trades_by_wallet[w.lower()] = []

    window_start = now_unix - lookback_minutes * 60
    recent_conditions = sorted({
        t.condition_id
        for trades in trades_by_wallet.values()
        for t in trades
        if window_start <= t.timestamp_unix <= now_unix and t.condition_id
    })
    markets = _synthesize_markets_from_trades(trades_by_wallet, condition_ids=recent_conditions)
    signals = detect_momentum_signals(
        trades_by_wallet=trades_by_wallet,
        markets=markets,
        now_unix=now_unix,
        lookback_minutes=lookback_minutes,
        min_buy_trades=min_buy_trades,
        min_unique_wallets=min_unique_wallets,
        min_notional_usd=min_notional_usd,
        min_price_move=min_price_move,
    )

    new = known = filled = rejected = 0
    observed = 0
    health_ok, health_note = (
        _momentum_health_allows_execution(client, db_path)
        if execute and require_positive_history
        else (bool(execute), "health gate bypassed" if execute else "observe-only")
    )
    for sig in signals:
        if _is_known(db_path, sig.idempotency_key):
            known += 1
            continue
        new += 1
        blocked_reason = _market_block_reason(sig, excluded_terms)
        if blocked_reason:
            _record(
                db_path, sig, decision=f"rejected_{blocked_reason}",
                executed_price=None, executed_contracts=None,
                entry_premium=None, premium_threshold=None, notes=blocked_reason,
            )
            rejected += 1
            continue
        try:
            ask = _best_ask(client.get_orderbook(sig.outcome_token_id))
        except Exception as e:  # noqa: BLE001
            _record(db_path, sig, decision="rejected_no_book", executed_price=None,
                    executed_contracts=None, entry_premium=None, premium_threshold=None, notes=str(e)[:200])
            rejected += 1
            continue
        if ask is None:
            _record(db_path, sig, decision="rejected_no_book", executed_price=None,
                    executed_contracts=None, entry_premium=None, premium_threshold=None, notes="empty asks")
            rejected += 1
            continue

        filt = market_filter(sig.market_question, live_price=ask["price"])
        if filt.blocked:
            _record(
                db_path, sig, decision=f"rejected_{filt.reason}",
                executed_price=ask["price"], executed_contracts=None,
                entry_premium=None, premium_threshold=None,
                notes=f"preference_score={filt.preference_score:.2f}",
                category=filt.category,
            )
            rejected += 1
            continue

        premium = ask["price"] - sig.vwap
        category_premium_limit = category_max_entry_premium(filt.category, max_entry_premium)
        if premium > category_premium_limit:
            _record(
                db_path, sig, decision="rejected_premium",
                executed_price=ask["price"], executed_contracts=None,
                entry_premium=premium,
                premium_threshold=category_premium_limit,
                notes=(
                    f"premium {premium:.4f} > {category_premium_limit:.4f} "
                    f"(base={max_entry_premium:.4f}, score={filt.preference_score:.2f})"
                ),
                category=filt.category,
            )
            rejected += 1
            continue

        contracts = int(min(notional_per_signal_usd / max(ask["price"], 0.01), ask["size"]))
        if contracts <= 0:
            _record(db_path, sig, decision="rejected_other", executed_price=ask["price"],
                    executed_contracts=0, entry_premium=premium, premium_threshold=None,
                    notes="size<=0", category=filt.category)
            rejected += 1
            continue

        if not execute or not health_ok:
            _record(
                db_path, sig, decision="observed",
                executed_price=ask["price"], executed_contracts=contracts,
                entry_premium=premium, premium_threshold=None,
                notes=health_note,
                category=filt.category,
            )
            observed += 1
            continue

        side_label = "YES" if sig.outcome_index == 0 else f"OUT{sig.outcome_index}"
        try:
            executor.execute_leg(
                strategy="polymarket_momentum",
                ticker=sig.outcome_token_id,
                side=side_label,
                action="buy",
                contracts=contracts,
                price=ask["price"],
                is_maker=False,
                venue="polymarket",
                notes=f"momentum={sig.idempotency_key[:8]} move={sig.price_move:.3f}",
            )
            _record(db_path, sig, decision="filled", executed_price=ask["price"],
                    executed_contracts=contracts, entry_premium=premium,
                    premium_threshold=None, notes=f"category={filt.category}")
            filled += 1
        except Exception as e:  # noqa: BLE001
            _record(db_path, sig, decision="rejected_other", executed_price=ask["price"],
                    executed_contracts=None, entry_premium=premium, premium_threshold=None,
                    notes=str(e)[:200], category=filt.category)
            rejected += 1

    return MomentumRunSummary(
        candidates_considered=len(candidate_wallets),
        markets_available=len(markets),
        signals_detected=len(signals),
        signals_new=new,
        signals_known=known,
        signals_filled=filled,
        signals_rejected=rejected,
        signals_observed=observed,
        notes="no fresh momentum" if not signals else health_note,
    )


def build_default_executor() -> PaperExecutor:
    return PaperExecutor(
        settings.db_path,
        settings.starting_bankroll,
        fee_models={"polymarket": PolymarketFeeModel(gas_usd=settings.polymarket_gas_usd)},
    )


def _parse_exclude_terms(raw: str) -> list[str]:
    return [term.strip().lower() for term in (raw or "").split(",") if term.strip()]


def _market_block_reason(sig: MomentumSignal, excluded_terms: Sequence[str]) -> Optional[str]:
    haystack = f"{sig.market_question} {sig.outcome_label}".lower()
    if any(term in haystack for term in excluded_terms):
        return "excluded_market"
    if sig.vwap <= 0.02 or sig.vwap >= 0.98:
        return "extreme_price"
    return None


def _momentum_health_allows_execution(client: PolymarketClient, db_path: str) -> tuple[bool, str]:
    min_resolved = settings.polymarket_momentum_min_resolved_to_execute
    min_pnl = settings.polymarket_momentum_min_realized_pnl_to_execute
    if min_resolved <= 0:
        return True, "health gate disabled"

    if not Path(db_path).exists():
        return False, f"health gate: need {min_resolved} resolved paper fills"

    with _conn(db_path) as c:
        rows = c.execute(
            """
            SELECT p.ticker, p.contracts, p.cost, s.condition_id
            FROM paper_trades p
            JOIN polymarket_momentum_signals s
              ON p.ticker = s.outcome_token_id
            WHERE p.venue='polymarket'
              AND p.strategy='polymarket_momentum'
              AND p.action='buy'
              AND s.decision='filled'
            ORDER BY p.id DESC
            LIMIT 500
            """
        ).fetchall()

    resolved = 0
    pnl = 0.0
    market_cache: dict[str, PolymarketMarket] = {}
    for row in rows:
        condition_id = str(row["condition_id"])
        token_id = str(row["ticker"])
        try:
            if condition_id not in market_cache:
                market_cache[condition_id] = client.get_market(condition_id)
            market = market_cache[condition_id]
        except Exception:  # noqa: BLE001
            continue
        if not market.closed:
            continue
        outcome = next((o for o in market.outcomes if o.token_id == token_id), None)
        if outcome is None or outcome.price is None:
            continue
        resolved += 1
        pnl += (float(row["contracts"]) * float(outcome.price)) - float(row["cost"])

    if resolved < min_resolved:
        return False, f"health gate: {resolved}/{min_resolved} resolved fills"
    if pnl < min_pnl:
        return False, f"health gate: realized pnl ${pnl:.2f} < ${min_pnl:.2f}"
    return True, f"health gate passed: {resolved} resolved, pnl ${pnl:.2f}"
