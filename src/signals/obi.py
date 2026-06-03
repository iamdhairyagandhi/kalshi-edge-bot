"""Top-of-book imbalance signal (OBI, not OFI)."""

from __future__ import annotations

from dataclasses import dataclass

from src.strategies.overround_arb import Orderbook


@dataclass
class OBIReading:
    yes_obi: float
    no_obi: float
    bid_depth_yes: int
    ask_depth_yes: int


def compute_obi(book: Orderbook) -> OBIReading:
    yes_total = book.yes_best_bid_size + book.yes_best_ask_size
    yes_obi = ((book.yes_best_bid_size - book.yes_best_ask_size) / yes_total) \
        if yes_total > 0 else 0.0
    no_total = book.no_best_bid_size + book.no_best_ask_size
    no_obi = ((book.no_best_bid_size - book.no_best_ask_size) / no_total) \
        if no_total > 0 else 0.0
    return OBIReading(yes_obi, no_obi,
                      book.yes_best_bid_size, book.yes_best_ask_size)


def should_skip_post_yes_bid(book: Orderbook, threshold: float = -0.6) -> bool:
    return compute_obi(book).yes_obi < threshold


def should_skip_post_no_bid(book: Orderbook, threshold: float = -0.6) -> bool:
    return compute_obi(book).no_obi < threshold
