"""Cross-venue Kalshi/Polymarket spread detection.

This is intentionally scanner-only. A positive cross-venue spread is not
automatically a trade unless the two market rules are equivalent and both
legs are executable at the observed prices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

from src.clients.polymarket import PolymarketMarket
from src.strategies.overround_arb import Orderbook


STOPWORDS = {
    "a", "an", "and", "are", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "the", "this", "to", "will", "win", "wins",
}
YES_LABELS = {"yes", "up", "true"}
NO_LABELS = {"no", "down", "false"}


@dataclass(frozen=True)
class MarketMatch:
    kalshi_ticker: str
    kalshi_title: str
    polymarket_condition_id: str
    polymarket_question: str
    polymarket_token_id: str
    polymarket_outcome_index: int
    polymarket_outcome_label: str
    score: float


@dataclass(frozen=True)
class PolyTopBook:
    bid: float
    bid_size: float
    ask: float
    ask_size: float


@dataclass(frozen=True)
class CrossVenueSpread:
    match: MarketMatch
    kalshi_yes_bid: float
    kalshi_yes_ask: float
    kalshi_yes_bid_size: int
    kalshi_yes_ask_size: int
    polymarket_yes_bid: float
    polymarket_yes_ask: float
    polymarket_yes_bid_size: float
    polymarket_yes_ask_size: float
    valuation_spread: float
    kalshi_bid_minus_poly_ask: float
    poly_bid_minus_kalshi_ask: float
    best_executable_spread: float
    direction: str
    decision: str
    notes: str


def market_title(m: dict[str, Any]) -> str:
    return str(
        m.get("title")
        or m.get("yes_sub_title")
        or m.get("subtitle")
        or m.get("event_title")
        or m.get("ticker")
        or ""
    )


def is_mve_combo_market(m: dict[str, Any]) -> bool:
    ticker = str(m.get("ticker") or "")
    event_ticker = str(m.get("event_ticker") or "")
    collection = str(m.get("mve_collection_ticker") or "")
    return (
        ticker.startswith("KXMVE")
        or event_ticker.startswith("KXMVE")
        or collection.startswith("KXMVE")
    )


def normalize_question(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> set[str]:
    return {t for t in normalize_question(text).split() if len(t) > 1 and t not in STOPWORDS}


def question_score(a: str, b: str) -> float:
    na = normalize_question(a)
    nb = normalize_question(b)
    if not na or not nb:
        return 0.0
    ta = tokens(na)
    tb = tokens(nb)
    overlap = len(ta & tb) / max(1, len(ta | tb))
    seq = SequenceMatcher(None, na, nb).ratio()
    return (0.65 * overlap) + (0.35 * seq)


def choose_yes_outcome(market: PolymarketMarket) -> Optional[tuple[int, str, str]]:
    if not market.is_binary:
        return None
    fallback: Optional[tuple[int, str, str]] = None
    for outcome in market.outcomes:
        label = normalize_question(outcome.label)
        if label in YES_LABELS:
            return outcome.index, outcome.label, outcome.token_id
        if label not in NO_LABELS and fallback is None:
            fallback = (outcome.index, outcome.label, outcome.token_id)
    return fallback


def match_markets(
    kalshi_markets: Iterable[dict[str, Any]],
    polymarket_markets: Iterable[PolymarketMarket],
    *,
    min_score: float = 0.72,
    max_matches: int = 50,
) -> list[MarketMatch]:
    candidates: list[MarketMatch] = []
    poly_binary = []
    for pm in polymarket_markets:
        if pm.closed or not pm.accepting_orders:
            continue
        yes = choose_yes_outcome(pm)
        if yes is None:
            continue
        poly_binary.append((pm, yes))

    for km in kalshi_markets:
        ticker = str(km.get("ticker") or "")
        kt = market_title(km)
        if not ticker or not kt:
            continue
        for pm, yes in poly_binary:
            score = question_score(kt, pm.question)
            if score < min_score:
                continue
            idx, label, token_id = yes
            if not token_id:
                continue
            candidates.append(MarketMatch(
                kalshi_ticker=ticker,
                kalshi_title=kt,
                polymarket_condition_id=pm.condition_id,
                polymarket_question=pm.question,
                polymarket_token_id=token_id,
                polymarket_outcome_index=idx,
                polymarket_outcome_label=label,
                score=score,
            ))

    candidates.sort(key=lambda m: m.score, reverse=True)
    return candidates[:max_matches]


def parse_poly_top_book(payload: dict[str, Any]) -> Optional[PolyTopBook]:
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    if not bids and not asks:
        return None

    def price(level: dict[str, Any]) -> float:
        return float(level.get("price", 0.0) or 0.0)

    def size(level: dict[str, Any]) -> float:
        return float(level.get("size", 0.0) or 0.0)

    best_bid = max(bids, key=price) if bids else None
    best_ask = min(asks, key=price) if asks else None
    return PolyTopBook(
        bid=price(best_bid) if best_bid else 0.0,
        bid_size=size(best_bid) if best_bid else 0.0,
        ask=price(best_ask) if best_ask else 1.0,
        ask_size=size(best_ask) if best_ask else 0.0,
    )


def build_spread(
    match: MarketMatch,
    kalshi_book: Orderbook,
    polymarket_book: PolyTopBook,
    *,
    min_spread: float = 0.03,
) -> CrossVenueSpread:
    valuation = kalshi_book.yes_best_ask - polymarket_book.ask
    k_bid_minus_p_ask = kalshi_book.yes_best_bid - polymarket_book.ask
    p_bid_minus_k_ask = polymarket_book.bid - kalshi_book.yes_best_ask
    if k_bid_minus_p_ask >= p_bid_minus_k_ask:
        best = k_bid_minus_p_ask
        direction = "buy_poly_sell_kalshi"
    else:
        best = p_bid_minus_k_ask
        direction = "buy_kalshi_sell_poly"

    notes = "executable spread meets threshold" if best >= min_spread else "below executable threshold"
    decision = "candidate" if best >= min_spread else "observe"
    return CrossVenueSpread(
        match=match,
        kalshi_yes_bid=kalshi_book.yes_best_bid,
        kalshi_yes_ask=kalshi_book.yes_best_ask,
        kalshi_yes_bid_size=kalshi_book.yes_best_bid_size,
        kalshi_yes_ask_size=kalshi_book.yes_best_ask_size,
        polymarket_yes_bid=polymarket_book.bid,
        polymarket_yes_ask=polymarket_book.ask,
        polymarket_yes_bid_size=polymarket_book.bid_size,
        polymarket_yes_ask_size=polymarket_book.ask_size,
        valuation_spread=valuation,
        kalshi_bid_minus_poly_ask=k_bid_minus_p_ask,
        poly_bid_minus_kalshi_ask=p_bid_minus_k_ask,
        best_executable_spread=best,
        direction=direction,
        decision=decision,
        notes=notes,
    )
