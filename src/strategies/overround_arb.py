"""
Overround Arbitrage Strategy.

EDGE THESIS
-----------
On a binary YES/NO market, a riskless synthetic costs $1 at settlement
(one of YES or NO pays $1, the other pays $0). Therefore, if at any moment:

    YES_best_ask + NO_best_ask < 1.00 - round_trip_fees - safety_margin

you can buy both sides and lock in a riskless profit. (Conversely, if the
sum of the *bids* exceeds $1 + fees + margin, you can sell both sides.)

In practice this only fires on thin / dislocated books and the size is
tiny, but it's:
  - structurally sound (no probability model required)
  - the cleanest possible sanity check that our plumbing works end-to-end
  - a useful liquidity-providing strategy when run as maker orders

REALISM CAVEATS
---------------
1. The two legs must both fill. If we take one side and the other
   moves, we're holding directional risk. We require IOC (immediate-or-cancel)
   semantics or we abort.
2. Order book depth at the inside is often 1-3 contracts. Size accordingly.
3. Resolution disputes are real on Kalshi (rare but exist). The "riskless"
   $1 sum assumes correct, undisputed settlement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.utils.fees import taker_fee, maker_fee


# Minimum net profit (cents per contract) required to fire. Kalshi taker
# fees on near-50¢ prices are ~1.75¢/side = 3.5¢ round trip, so anything
# under ~4¢ won't pay.
MIN_NET_EDGE_PER_CONTRACT = 0.02   # $0.02 = 2¢
SAFETY_MARGIN = 0.005              # $0.005 buffer for slippage / dispute risk
MIN_BOOK_DEPTH = 1                 # require at least N contracts on both insides


@dataclass
class Orderbook:
    """Minimal orderbook view for a single market."""
    ticker: str
    yes_best_ask: float
    yes_best_ask_size: int
    no_best_ask: float
    no_best_ask_size: int
    yes_best_bid: float = 0.0
    yes_best_bid_size: int = 0
    no_best_bid: float = 0.0
    no_best_bid_size: int = 0


@dataclass
class ArbOpportunity:
    ticker: str
    direction: str             # "buy_both" (sum of asks < 1) or "sell_both"
    contracts: int             # size we can execute
    yes_price: float
    no_price: float
    gross_edge_per_contract: float   # before fees
    fees_per_contract: float
    net_edge_per_contract: float
    net_profit_total: float


def find_buy_both_arb(book: Orderbook, max_contracts: int = 50) -> Optional[ArbOpportunity]:
    """
    Look for: YES_ask + NO_ask < 1.00 - fees - margin.
    Buying both sides locks $1 at settlement.
    """
    sum_asks = book.yes_best_ask + book.no_best_ask
    if sum_asks >= 1.0:
        return None

    contracts = min(book.yes_best_ask_size, book.no_best_ask_size, max_contracts)
    if contracts < MIN_BOOK_DEPTH:
        return None

    # Both legs are taker (we cross the spread).
    fees = taker_fee(contracts, book.yes_best_ask) + taker_fee(contracts, book.no_best_ask)
    fees_per = fees / contracts

    gross_edge_per = 1.0 - sum_asks
    net_edge_per = gross_edge_per - fees_per - SAFETY_MARGIN

    if net_edge_per < MIN_NET_EDGE_PER_CONTRACT:
        return None

    return ArbOpportunity(
        ticker=book.ticker,
        direction="buy_both",
        contracts=contracts,
        yes_price=book.yes_best_ask,
        no_price=book.no_best_ask,
        gross_edge_per_contract=gross_edge_per,
        fees_per_contract=fees_per,
        net_edge_per_contract=net_edge_per,
        net_profit_total=net_edge_per * contracts,
    )


def find_sell_both_arb(book: Orderbook, max_contracts: int = 50) -> Optional[ArbOpportunity]:
    """
    Look for: YES_bid + NO_bid > 1.00 + fees + margin.
    Selling both sides (hitting both bids) banks the overround.

    Note: 'selling' YES on Kalshi means buying NO at (1 - yes_bid), and
    vice versa. We model it as two separate buy-NO + buy-YES legs hitting
    the opposing bids. Equivalent economics; clearer accounting.
    """
    sum_bids = book.yes_best_bid + book.no_best_bid
    if sum_bids <= 1.0:
        return None

    contracts = min(book.yes_best_bid_size, book.no_best_bid_size, max_contracts)
    if contracts < MIN_BOOK_DEPTH:
        return None

    fees = taker_fee(contracts, book.yes_best_bid) + taker_fee(contracts, book.no_best_bid)
    fees_per = fees / contracts

    gross_edge_per = sum_bids - 1.0
    net_edge_per = gross_edge_per - fees_per - SAFETY_MARGIN

    if net_edge_per < MIN_NET_EDGE_PER_CONTRACT:
        return None

    return ArbOpportunity(
        ticker=book.ticker,
        direction="sell_both",
        contracts=contracts,
        yes_price=book.yes_best_bid,
        no_price=book.no_best_bid,
        gross_edge_per_contract=gross_edge_per,
        fees_per_contract=fees_per,
        net_edge_per_contract=net_edge_per,
        net_profit_total=net_edge_per * contracts,
    )


def scan_orderbooks(books: List[Orderbook], max_contracts: int = 50) -> List[ArbOpportunity]:
    """
    Return all arb opportunities sorted by net profit, descending.

    Note: On Kalshi, sum_asks < 1 and sum_bids > 1 are mathematically the
    SAME condition (because ask_yes = 1 - bid_no, ask_no = 1 - bid_yes,
    so sum_asks + sum_bids = 2). The two finders detect the same economic
    opportunity. We keep both for clarity / future multi-level books, but
    dedupe per ticker keeping whichever direction has higher net profit.
    """
    by_ticker: dict = {}
    for b in books:
        for finder in (find_buy_both_arb, find_sell_both_arb):
            opp = finder(b, max_contracts=max_contracts)
            if opp is None:
                continue
            existing = by_ticker.get(opp.ticker)
            if existing is None or opp.net_profit_total > existing.net_profit_total:
                by_ticker[opp.ticker] = opp
    opps = list(by_ticker.values())
    opps.sort(key=lambda o: o.net_profit_total, reverse=True)
    return opps
