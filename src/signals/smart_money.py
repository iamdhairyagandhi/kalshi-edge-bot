"""
Smart-money cohort selection for Polymarket.

The leaderboard endpoint gives us a candidate pool. We do NOT trust its
ranking, because:
- It's typically sorted by raw $PnL, which is dominated by one-shot luck.
- It doesn't separate realized from unrealized.
- It doesn't enforce minimum activity or recency.

This module re-ranks candidates using criteria that survived the
rubber-duck pass:
- Realized P&L only (settled markets weighted heavier).
- Minimum number of trades + minimum number of resolved markets.
- Recency decay so dormant whales drop out.
- Cap per-wallet by max-drawdown proxy (variance of daily P&L) so we
  prefer steadier wallets over jackpot bettors.

We deliberately do NOT implement sybil/cluster detection in v1 — that
needs on-chain co-funding graph data we don't have yet. We mitigate the
risk by requiring K ≥ 3 distinct wallets in the consensus strategy.

We also deliberately do NOT count one wallet multiple times when it
trades the same market with multiple fills. Per-wallet net position
direction in the window is what casts the "vote" (see
`strategies.consensus_copy`).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from src.clients.polymarket import PolymarketTrade, PolymarketWalletStats


@dataclass(frozen=True)
class WalletScore:
    wallet: str
    score: float                  # higher = better
    realized_pnl_usd: float
    n_trades: int
    n_resolved: int
    last_trade_unix: int
    pnl_stability: float          # 0..1 (1 = steady, 0 = jackpot/blowup)
    reasons_excluded: List[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.reasons_excluded


# ---------------------------------------------------------------------------
# Wallet stat computation
# ---------------------------------------------------------------------------


def compute_wallet_stats(
    wallet: str,
    trades: Sequence[PolymarketTrade],
    resolved_condition_ids: Optional[Iterable[str]] = None,
) -> PolymarketWalletStats:
    """Aggregate a wallet's trade history. Caller decides what counts as
    resolved; if None, we treat the set as empty (i.e. n_resolved=0)."""
    resolved = set(resolved_condition_ids or ())
    markets = {t.condition_id for t in trades}
    realized = 0.0
    # We can only compute realized P&L for resolved markets. For each
    # (wallet, condition_id, outcome_token_id) net position * settlement
    # price would be the right thing, but we don't have settlement here.
    # Approximation: realized = sum of (sell_proceeds - buy_costs) per
    # outcome token in resolved markets only. That collapses to net cash
    # flow per token, which is correct if the position is fully closed
    # (which is true for resolved markets).
    by_token: Dict[tuple, float] = defaultdict(float)
    for t in trades:
        if t.condition_id in resolved:
            sign = -1.0 if t.side == "BUY" else 1.0
            by_token[(t.condition_id, t.outcome_token_id)] += sign * t.notional_usd
    realized = sum(by_token.values())
    return PolymarketWalletStats(
        wallet=wallet.lower(),
        n_trades=len(trades),
        n_markets=len(markets),
        n_resolved_markets=len(markets & resolved),
        realized_pnl_usd=realized,
        total_volume_usd=sum(t.notional_usd for t in trades),
        first_trade_unix=min((t.timestamp_unix for t in trades), default=None),
        last_trade_unix=max((t.timestamp_unix for t in trades), default=None),
    )


def _pnl_stability(trades: Sequence[PolymarketTrade]) -> float:
    """Crude stability proxy: 1 - (stdev / mean) of per-day notional,
    clipped to [0, 1]. A wallet whose volume is concentrated in one day
    scores ~0; one with consistent daily activity scores ~1."""
    if not trades:
        return 0.0
    by_day: Dict[int, float] = defaultdict(float)
    for t in trades:
        day = t.timestamp_unix // 86400
        by_day[day] += t.notional_usd
    if len(by_day) < 2:
        return 0.0
    vals = list(by_day.values())
    mean = statistics.mean(vals)
    if mean <= 0:
        return 0.0
    cv = statistics.stdev(vals) / mean  # coefficient of variation
    return max(0.0, min(1.0, 1.0 - cv / 3.0))  # cv=3 → 0, cv=0 → 1


def _recency_weight(last_unix: int, now_unix: int, half_life_days: float = 14.0) -> float:
    """Exponential decay. A wallet inactive for `half_life_days` gets 0.5."""
    if last_unix <= 0:
        return 0.0
    age_days = max(0.0, (now_unix - last_unix) / 86400.0)
    return math.exp(-math.log(2.0) * age_days / max(0.1, half_life_days))


# ---------------------------------------------------------------------------
# Scoring & cohort selection
# ---------------------------------------------------------------------------


def score_wallet(
    wallet: str,
    trades: Sequence[PolymarketTrade],
    resolved_condition_ids: Iterable[str],
    *,
    now_unix: int,
    min_trades: int = 50,
    min_resolved: int = 20,
    recency_half_life_days: float = 14.0,
) -> WalletScore:
    """Compute the cohort-eligibility score for a single wallet."""
    stats = compute_wallet_stats(wallet, trades, resolved_condition_ids)
    stability = _pnl_stability(trades)
    recency = _recency_weight(stats.last_trade_unix or 0, now_unix, recency_half_life_days)

    excluded: List[str] = []
    if stats.n_trades < min_trades:
        excluded.append(f"n_trades<{min_trades}")
    if stats.n_resolved_markets < min_resolved:
        excluded.append(f"n_resolved<{min_resolved}")
    if stats.realized_pnl_usd <= 0:
        excluded.append("realized_pnl<=0")

    # Score: realized PnL, decayed by recency, dampened by jackpot variance.
    # log1p so a $5M wallet doesn't 1000x a $5k wallet on linear PnL alone.
    raw_pnl = max(0.0, stats.realized_pnl_usd)
    score = math.log1p(raw_pnl) * recency * (0.5 + 0.5 * stability)

    return WalletScore(
        wallet=wallet.lower(),
        score=score,
        realized_pnl_usd=stats.realized_pnl_usd,
        n_trades=stats.n_trades,
        n_resolved=stats.n_resolved_markets,
        last_trade_unix=stats.last_trade_unix or 0,
        pnl_stability=stability,
        reasons_excluded=excluded,
    )


def rank_wallets(
    candidate_to_trades: Dict[str, Sequence[PolymarketTrade]],
    resolved_condition_ids: Iterable[str],
    *,
    now_unix: int,
    min_trades: int = 50,
    min_resolved: int = 20,
    recency_half_life_days: float = 14.0,
) -> List[WalletScore]:
    """Score and rank a candidate pool. Ineligible wallets are kept in
    the result (with `reasons_excluded` populated) so the dashboard can
    show *why* they were dropped, but they sort below all eligible
    wallets regardless of score."""
    resolved_list = list(resolved_condition_ids)  # iterate multiple times
    scored = [
        score_wallet(
            w, trs, resolved_list,
            now_unix=now_unix,
            min_trades=min_trades, min_resolved=min_resolved,
            recency_half_life_days=recency_half_life_days,
        )
        for w, trs in candidate_to_trades.items()
    ]
    # Sort: eligible first (descending score), then ineligible.
    return sorted(scored, key=lambda s: (0 if s.eligible else 1, -s.score))


def select_cohort(ranked: Sequence[WalletScore], top_n: int) -> List[WalletScore]:
    """Pick the top-N eligible wallets, deterministically."""
    eligible = [s for s in ranked if s.eligible]
    return eligible[:top_n]
