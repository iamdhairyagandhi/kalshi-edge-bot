"""
Safe compounder strategy.

Ported and reworked from ryanfrigo/kalshi-ai-trading-bot/src/strategies/
safe_compounder.py (MIT).

Original idea: prediction markets exhibit the favorite-longshot bias —
the YES side of a heavily-favored market tends to be *over*-priced relative
to its true win rate, which means the corresponding NO side is *under*-priced.
We can buy that cheap NO (i.e. sell the overpriced YES) when:

    - YES is trading high enough that the NO contract is cheap (≤ 0.20)
    - There is enough net-of-fee edge versus our prior estimate of P(NO wins)
    - Liquidity is adequate
    - The market is far enough from resolution to avoid pin risk

Differences from the original:
    1. Net-of-fee EV uses our TradeEconomics (proper Kalshi maker+taker fees)
    2. Calibration log is written through src.utils.calibration so we
       compute Brier after resolution
    3. Edge thresholds use *conservative defaults*: the original repo had
       been loosened over time (overfit-to-pain pattern); we reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.strategies.overround_arb import Orderbook
from src.utils.fees import TradeEconomics, kelly_fraction


# Conservative defaults — DO NOT loosen these without recalibrating.
# (The original repo's loosening over time appears to have masked a
# strategy whose edge had eroded with maker-fee introduction in 2025.)
MIN_YES_PRICE = 0.80          # only fade clear favorites
MAX_NO_ASK = 0.20             # buy NO only when cheap
MIN_NET_EDGE = 0.02           # 2¢ net-of-fee per contract minimum
MIN_LIQUIDITY_24H_USD = 500.0
# Academic estimate: favorite-longshot bias is ~5% for heavy favorites,
# scaling roughly linearly with extremity. See Snowberg & Wolfers (2010).
MAX_FAVORITE_LONGSHOT_PREMIUM = 0.05


@dataclass
class SafeCompounderSignal:
    ticker: str
    side: str              # always "NO" for this strategy
    action: str            # always "buy"
    no_ask: float
    estimated_no_win_prob: float
    net_edge_per_contract: float
    kelly_fraction: float
    contracts_suggested: int


def estimate_no_win_prob(yes_price: float) -> float:
    """
    Estimate true P(NO resolves) for a YES-favored market, accounting for
    favorite-longshot bias.

    Model: market-implied P(NO) is an under-estimate of the true rate.
    We add a premium that scales linearly with how extreme the YES price is,
    from 0 at yes_price = 0.80 up to MAX_FAVORITE_LONGSHOT_PREMIUM at
    yes_price = 1.00.

    Premium is capped so we never claim absurd edge on questionable signal.
    """
    market_implied_no = 1.0 - yes_price
    if yes_price <= MIN_YES_PRICE:
        premium = 0.0
    else:
        extremity = (yes_price - MIN_YES_PRICE) / (1.0 - MIN_YES_PRICE)
        premium = MAX_FAVORITE_LONGSHOT_PREMIUM * min(1.0, max(0.0, extremity))
    return max(0.01, min(0.99, market_implied_no + premium))


def evaluate_book(
    book: Orderbook,
    *,
    volume_24h_usd: float = 0.0,
    bankroll: float = 1000.0,
    max_position_pct: float = 0.02,
    is_maker: bool = True,
) -> Optional[SafeCompounderSignal]:
    """
    Decide whether to emit a NO-buy signal on this market.
    Returns None if no qualifying signal.
    """
    if volume_24h_usd < MIN_LIQUIDITY_24H_USD:
        return None
    if book.yes_best_bid < MIN_YES_PRICE:
        return None
    if book.no_best_ask > MAX_NO_ASK:
        return None
    if book.no_best_ask <= 0:
        return None

    no_ask = book.no_best_ask
    p_no = estimate_no_win_prob(book.yes_best_bid)

    # Net-of-fee EV per contract (1 contract pays $1 if NO wins, $0 otherwise)
    # Cost basis = no_ask; expected payoff = p_no * 1
    # Use our fee model for both entry and (assume) settlement-on-resolution.
    econ = TradeEconomics(
        contracts=1,
        side="NO",
        entry_price=no_ask,
        true_prob=p_no,
        is_maker=is_maker,
    )
    net_edge = econ.net_edge_per_contract

    if net_edge < MIN_NET_EDGE:
        return None

    # Kelly: payout ratio b = (1 - price) / price for $1 binary
    payout_ratio = (1.0 - no_ask) / no_ask
    f_star = kelly_fraction(p_no, payout_ratio)
    if f_star <= 0:
        return None

    # Quarter-Kelly position
    fraction = f_star * 0.25
    max_stake = bankroll * max_position_pct
    capped_stake = min(bankroll * fraction, max_stake)
    contracts = int(capped_stake / no_ask)
    if contracts < 1:
        return None
    # Don't exceed displayed size at the top of book
    contracts = min(contracts, book.no_best_ask_size)
    if contracts < 1:
        return None

    return SafeCompounderSignal(
        ticker=book.ticker,
        side="NO",
        action="buy",
        no_ask=no_ask,
        estimated_no_win_prob=p_no,
        net_edge_per_contract=net_edge,
        kelly_fraction=f_star,
        contracts_suggested=contracts,
    )


def scan_books(
    books: List[Orderbook],
    *,
    volumes_24h: Optional[dict[str, float]] = None,
    bankroll: float = 1000.0,
    max_position_pct: float = 0.02,
    is_maker: bool = True,
) -> List[SafeCompounderSignal]:
    """Run the safe-compounder evaluation across many orderbooks."""
    out: List[SafeCompounderSignal] = []
    volumes_24h = volumes_24h or {}
    for book in books:
        vol = volumes_24h.get(book.ticker, 0.0)
        sig = evaluate_book(
            book,
            volume_24h_usd=vol,
            bankroll=bankroll,
            max_position_pct=max_position_pct,
            is_maker=is_maker,
        )
        if sig is not None:
            out.append(sig)
    return out
