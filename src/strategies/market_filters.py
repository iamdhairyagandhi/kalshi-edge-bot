"""Market classification, preference scoring, and safety filters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


SPORT_TERMS = (
    " vs ",
    " vs.",
    "spread:",
    "o/u",
    "over/under",
    "ufc",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "atp",
    "wta",
    "itf",
    "open:",
    "championships:",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "hockey",
    "tennis",
)

LIVE_TERMS = (" live", "in-play", "in play", "1h", "2h", "q1", "q2", "q3", "q4", "set ")
POLITICS_TERMS = ("election", "senate", "house", "president", "trump", "biden", "congress", "mayor")
MACRO_TERMS = ("fed", "rate", "cpi", "inflation", "unemployment", "gdp", "recession")
CRYPTO_TERMS = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto")
WEATHER_TERMS = ("temperature", "weather", "rain", "snow", "hurricane", "tornado")
BUSINESS_TERMS = (
    "earnings",
    "revenue",
    "stock",
    "ipo",
    "merger",
    "acquisition",
    "tariff",
    "lawsuit",
    "bankruptcy",
    "sec ",
    "doj ",
    "approval",
)


@dataclass(frozen=True)
class MarketCategoryPolicy:
    """How suitable a market type is for automated paper trading."""

    score: float
    blocked: bool = False
    max_entry_premium_multiplier: float = 1.0


CATEGORY_POLICY: dict[str, MarketCategoryPolicy] = {
    # Slower, externally-modelable markets are where a bot has a better chance.
    "macro": MarketCategoryPolicy(score=1.00, max_entry_premium_multiplier=0.85),
    "politics": MarketCategoryPolicy(score=0.95, max_entry_premium_multiplier=0.85),
    "crypto": MarketCategoryPolicy(score=0.90, max_entry_premium_multiplier=1.00),
    "weather": MarketCategoryPolicy(score=0.80, max_entry_premium_multiplier=0.75),
    "business": MarketCategoryPolicy(score=0.75, max_entry_premium_multiplier=0.80),
    # Unknown markets are allowed, but need cleaner entry than preferred domains.
    "other": MarketCategoryPolicy(score=0.45, max_entry_premium_multiplier=0.55),
    # Sports/live games tend to be latency/information races. Keep them out by default.
    "sports": MarketCategoryPolicy(score=0.10, blocked=True, max_entry_premium_multiplier=0.0),
}


@dataclass(frozen=True)
class MarketFilterResult:
    category: str
    blocked: bool
    reason: Optional[str] = None
    preference_score: float = 0.0


def classify_market(question: str) -> str:
    text = f" {question or ''} ".lower()
    if any(term in text for term in POLITICS_TERMS):
        return "politics"
    if any(term in text for term in MACRO_TERMS):
        return "macro"
    if any(term in text for term in CRYPTO_TERMS):
        return "crypto"
    if any(term in text for term in WEATHER_TERMS):
        return "weather"
    if any(term in text for term in BUSINESS_TERMS):
        return "business"
    if any(term in text for term in SPORT_TERMS):
        return "sports"
    return "other"


def market_preference_score(question: str) -> float:
    category = classify_market(question)
    return CATEGORY_POLICY.get(category, CATEGORY_POLICY["other"]).score


def category_max_entry_premium(category: str, base_limit: float) -> float:
    policy = CATEGORY_POLICY.get(category, CATEGORY_POLICY["other"])
    return max(0.0, base_limit * policy.max_entry_premium_multiplier)


def market_filter(
    question: str,
    *,
    live_price: Optional[float] = None,
    negative_ev_categories: Optional[set[str]] = None,
) -> MarketFilterResult:
    text = f" {question or ''} ".lower()
    category = classify_market(question)
    policy = CATEGORY_POLICY.get(category, CATEGORY_POLICY["other"])
    if category in (negative_ev_categories or set()):
        return MarketFilterResult(
            category=category,
            blocked=True,
            reason="category_negative_ev",
            preference_score=policy.score,
        )
    if policy.blocked:
        return MarketFilterResult(
            category=category,
            blocked=True,
            reason=f"category_{category}",
            preference_score=policy.score,
        )
    if any(term in text for term in LIVE_TERMS):
        return MarketFilterResult(
            category=category,
            blocked=True,
            reason="live_event",
            preference_score=policy.score,
        )
    if live_price is not None and live_price >= 0.98:
        return MarketFilterResult(
            category=category,
            blocked=True,
            reason="chase_0_99",
            preference_score=policy.score,
        )
    return MarketFilterResult(category=category, blocked=False, preference_score=policy.score)
