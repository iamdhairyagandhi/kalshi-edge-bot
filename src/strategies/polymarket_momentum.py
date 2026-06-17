"""Polymarket fresh momentum signal detection.

This strategy looks for recent buy pressure rather than older smart-money
consensus. It is deliberately simple: multiple wallets, multiple buys,
enough notional, and late-window VWAP above early-window VWAP.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.clients.polymarket import PolymarketMarket, PolymarketTrade


@dataclass(frozen=True)
class MomentumSignal:
    condition_id: str
    outcome_token_id: str
    outcome_index: int
    market_question: str
    outcome_label: str
    window_start_unix: int
    window_end_unix: int
    first_trade_unix: int
    last_trade_unix: int
    buy_trades: int
    unique_wallets: int
    buy_notional_usd: float
    vwap: float
    early_vwap: float
    late_vwap: float
    price_move: float

    @property
    def idempotency_key(self) -> str:
        parts = (
            "polymarket_momentum",
            self.condition_id,
            self.outcome_token_id,
            str(self.first_trade_unix),
            str(self.last_trade_unix),
        )
        return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _vwap(trades: Sequence[PolymarketTrade]) -> Optional[float]:
    size = sum(t.size_shares for t in trades)
    if size <= 0:
        return None
    return sum(t.price * t.size_shares for t in trades) / size


def detect_momentum_signals(
    *,
    trades_by_wallet: Dict[str, Sequence[PolymarketTrade]],
    markets: Dict[str, PolymarketMarket],
    now_unix: int,
    lookback_minutes: int,
    min_buy_trades: int,
    min_unique_wallets: int,
    min_notional_usd: float,
    min_price_move: float,
) -> List[MomentumSignal]:
    window_start = now_unix - lookback_minutes * 60
    buys_by_token: Dict[Tuple[str, str], List[PolymarketTrade]] = {}
    for trades in trades_by_wallet.values():
        for t in trades:
            if t.side != "BUY":
                continue
            if t.timestamp_unix < window_start or t.timestamp_unix > now_unix:
                continue
            if not t.condition_id or not t.outcome_token_id:
                continue
            buys_by_token.setdefault((t.condition_id, t.outcome_token_id), []).append(t)

    signals: List[MomentumSignal] = []
    for (condition_id, token_id), buys in buys_by_token.items():
        market = markets.get(condition_id)
        if market is None or market.closed or not market.accepting_orders:
            continue
        outcome = next((o for o in market.outcomes if o.token_id == token_id), None)
        if outcome is None:
            continue
        if len(buys) < min_buy_trades:
            continue
        wallets = {t.wallet.lower() for t in buys}
        if len(wallets) < min_unique_wallets:
            continue
        notional = sum(t.notional_usd for t in buys)
        if notional < min_notional_usd:
            continue
        ordered = sorted(buys, key=lambda t: t.timestamp_unix)
        split = max(1, len(ordered) // 2)
        early = ordered[:split]
        late = ordered[split:] or ordered[-1:]
        early_vwap = _vwap(early)
        late_vwap = _vwap(late)
        all_vwap = _vwap(ordered)
        if early_vwap is None or late_vwap is None or all_vwap is None:
            continue
        move = late_vwap - early_vwap
        if move < min_price_move:
            continue
        signals.append(MomentumSignal(
            condition_id=condition_id,
            outcome_token_id=token_id,
            outcome_index=outcome.index,
            market_question=market.question,
            outcome_label=outcome.label,
            window_start_unix=window_start,
            window_end_unix=now_unix,
            first_trade_unix=ordered[0].timestamp_unix,
            last_trade_unix=ordered[-1].timestamp_unix,
            buy_trades=len(ordered),
            unique_wallets=len(wallets),
            buy_notional_usd=notional,
            vwap=all_vwap,
            early_vwap=early_vwap,
            late_vwap=late_vwap,
            price_move=move,
        ))

    signals.sort(key=lambda s: (s.price_move, s.buy_notional_usd), reverse=True)
    return signals
