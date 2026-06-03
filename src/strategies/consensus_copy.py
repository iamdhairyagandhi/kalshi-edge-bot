"""
Consensus-copy strategy for Polymarket.

Emits a `ConsensusSignal` when ≥K wallets in our cohort have, within a
rolling time window, taken a *net* long position on the same
`(condition_id, outcome_token_id)`.

Rubber-duck-driven correctness invariants:

1.  One wallet = one vote, regardless of how many fills they had. Multiple
    partials must NOT count as multiple votes.
2.  A wallet that buys then sells out within the window does NOT vote.
    Only net-long positions count.
3.  The vote direction is the sign of the wallet's net share count on
    the outcome token in the window, not the side of any individual fill.
4.  Signals are identified by an idempotency key
        (strategy, venue, condition_id, outcome_token_id,
         window_start_unix, cohort_version)
    so the runner can dedupe across polling cycles without keeping
    in-process state across restarts.
5.  Markets that are closed or not accepting orders are filtered out
    before we ever emit a signal — the executor would reject anyway,
    but we should not flood the dashboard with dead signals.
6.  Non-binary markets are allowed; the signal carries the
    outcome_index so the strategy is unambiguous about which side to copy.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.clients.polymarket import PolymarketMarket, PolymarketTrade


@dataclass(frozen=True)
class ConsensusSignal:
    """A 'K-of-N agreed long on the same outcome' event."""
    venue: str
    condition_id: str
    outcome_index: int
    outcome_token_id: str
    market_question: str
    cohort_size: int                      # N
    consensus_k: int                      # K
    agreeing_wallets: Tuple[str, ...]     # exactly K (sorted, deterministic)
    first_trade_unix: int                 # earliest of the agreeing wallets
    last_trade_unix: int                  # latest (becomes the "decision time" lower bound)
    window_start_unix: int
    window_end_unix: int
    cohort_version: str                   # hash of cohort wallet set
    avg_wallet_entry_price: float         # for monitoring slippage vs our fill
    total_wallet_notional_usd: float

    @property
    def idempotency_key(self) -> str:
        """Stable across runs; collisions only when the same window has
        re-emitted the same signal (which the runner should skip)."""
        parts = (
            "consensus_copy",
            self.venue,
            self.condition_id,
            self.outcome_token_id,
            str(self.window_start_unix),
            self.cohort_version,
        )
        return hashlib.sha1("|".join(parts).encode()).hexdigest()


def cohort_version(cohort_wallets: Iterable[str]) -> str:
    """Stable fingerprint of a cohort's wallet set. Two cohorts with the
    same members produce the same version regardless of insertion order."""
    sorted_wallets = sorted(w.lower() for w in cohort_wallets)
    return hashlib.sha1("|".join(sorted_wallets).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Per-wallet net position over the window
# ---------------------------------------------------------------------------


def _wallet_net_positions(
    trades: Sequence[PolymarketTrade],
    window_start_unix: int,
    window_end_unix: int,
) -> Dict[Tuple[str, str], Dict[str, float]]:
    """Aggregate per-wallet net shares per (condition_id, outcome_token_id)
    within the window. Returns:
        { (cond_id, token_id) : { wallet -> { 'net_shares', 'notional', 'first_ts', 'last_ts' } } }
    """
    out: Dict[Tuple[str, str], Dict[str, Dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"net_shares": 0.0, "notional": 0.0,
                                      "first_ts": float("inf"), "last_ts": 0.0,
                                      "weighted_price_num": 0.0, "abs_buy_size": 0.0})
    )
    for t in trades:
        if t.timestamp_unix < window_start_unix or t.timestamp_unix > window_end_unix:
            continue
        key = (t.condition_id, t.outcome_token_id)
        bucket = out[key][t.wallet.lower()]
        sign = 1.0 if t.side == "BUY" else -1.0
        bucket["net_shares"] += sign * t.size_shares
        bucket["notional"] += t.notional_usd
        bucket["first_ts"] = min(bucket["first_ts"], float(t.timestamp_unix))
        bucket["last_ts"] = max(bucket["last_ts"], float(t.timestamp_unix))
        if t.side == "BUY":
            bucket["weighted_price_num"] += t.price * t.size_shares
            bucket["abs_buy_size"] += t.size_shares
    return out


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------


def detect_consensus_signals(
    *,
    cohort_wallets: Sequence[str],
    cohort_trades: Dict[str, Sequence[PolymarketTrade]],   # wallet -> trades
    markets: Dict[str, PolymarketMarket],                  # condition_id -> market
    window_start_unix: int,
    window_end_unix: int,
    consensus_k: int,
    venue: str = "polymarket",
) -> List[ConsensusSignal]:
    """Return one ConsensusSignal per outcome token that crossed the K-of-N
    threshold within the window. Idempotency is the caller's responsibility
    (use `signal.idempotency_key` against a persistent set)."""
    if consensus_k < 2:
        raise ValueError("consensus_k must be >= 2 (1 wallet is not a cohort)")
    if consensus_k > len(cohort_wallets):
        raise ValueError(
            f"consensus_k ({consensus_k}) exceeds cohort size ({len(cohort_wallets)})"
        )

    cohort_set: Set[str] = {w.lower() for w in cohort_wallets}
    ver = cohort_version(cohort_set)

    # Normalize incoming `cohort_trades` to lowercase keys so callers don't
    # have to. We still only consider wallets in the cohort.
    normalized: Dict[str, Sequence[PolymarketTrade]] = {
        w.lower(): trs for w, trs in cohort_trades.items()
    }

    # Flatten all trades by the cohort within the window
    all_trades: List[PolymarketTrade] = []
    for w in cohort_set:
        all_trades.extend(normalized.get(w, ()) or [])

    by_outcome = _wallet_net_positions(all_trades, window_start_unix, window_end_unix)

    signals: List[ConsensusSignal] = []
    for (cond_id, token_id), wallets in by_outcome.items():
        # vote = wallets that ended the window with net LONG position
        voters = {
            w: stats for w, stats in wallets.items()
            if stats["net_shares"] > 0 and w in cohort_set
        }
        if len(voters) < consensus_k:
            continue

        # Filter out closed / non-tradeable markets
        mkt = markets.get(cond_id)
        if mkt is None or mkt.closed or not mkt.accepting_orders:
            continue

        # Resolve outcome_index from token_id (markets carry both)
        outcome_index = next(
            (o.index for o in mkt.outcomes if o.token_id == token_id), None
        )
        if outcome_index is None:
            # Token id we don't recognize on this market — skip safely.
            continue

        # Take the K wallets with the largest net-share votes (most committed).
        ranked = sorted(voters.items(),
                        key=lambda kv: (-kv[1]["net_shares"], kv[0]))
        agreeing = [w for w, _ in ranked[:consensus_k]]
        first_ts = int(min(voters[w]["first_ts"] for w in agreeing))
        last_ts = int(max(voters[w]["last_ts"] for w in agreeing))

        # Volume-weighted entry price across all buys by the K agreeing wallets.
        weighted_num = sum(voters[w]["weighted_price_num"] for w in agreeing)
        abs_buy_size = sum(voters[w]["abs_buy_size"] for w in agreeing)
        avg_price = (weighted_num / abs_buy_size) if abs_buy_size > 0 else 0.0
        total_notional = sum(voters[w]["notional"] for w in agreeing)

        signals.append(ConsensusSignal(
            venue=venue,
            condition_id=cond_id,
            outcome_index=outcome_index,
            outcome_token_id=token_id,
            market_question=mkt.question,
            cohort_size=len(cohort_set),
            consensus_k=consensus_k,
            agreeing_wallets=tuple(sorted(agreeing)),
            first_trade_unix=first_ts,
            last_trade_unix=last_ts,
            window_start_unix=window_start_unix,
            window_end_unix=window_end_unix,
            cohort_version=ver,
            avg_wallet_entry_price=avg_price,
            total_wallet_notional_usd=total_notional,
        ))

    # Sort newest-first so runners can process them in arrival order.
    signals.sort(key=lambda s: -s.last_trade_unix)
    return signals
