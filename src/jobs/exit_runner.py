"""Paper exit manager for open Polymarket positions."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from contextlib import contextmanager

from src.clients.polymarket import PolymarketAPIError, PolymarketClient
from src.config import settings
from src.jobs.momentum_runner import build_default_executor as build_momentum_executor
from src.paper.executor import PaperExecutor


@dataclass(frozen=True)
class ExitRunSummary:
    open_positions: int
    evaluated: int
    exited: int
    skipped: int
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"open={self.open_positions} evaluated={self.evaluated} "
            f"exited={self.exited} skipped={self.skipped}"
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


def _best_bid(book: Dict[str, Any]) -> Optional[Dict[str, float]]:
    bids = book.get("bids") or []
    if not bids:
        return None
    best = max(bids, key=lambda b: float(b["price"]))
    return {"price": float(best["price"]), "size": float(best["size"])}


def run_once(
    *,
    client: PolymarketClient,
    executor: PaperExecutor,
    db_path: str,
    take_profit_cents: Optional[float] = None,
    profit_capture: Optional[float] = None,
    stop_loss_cents: Optional[float] = None,
    stop_loss_fraction: Optional[float] = None,
    min_bid_size: Optional[float] = None,
) -> ExitRunSummary:
    take_profit_cents = settings.polymarket_exit_take_profit_cents if take_profit_cents is None else take_profit_cents
    profit_capture = settings.polymarket_exit_profit_capture if profit_capture is None else profit_capture
    stop_loss_cents = settings.polymarket_exit_stop_loss_cents if stop_loss_cents is None else stop_loss_cents
    stop_loss_fraction = settings.polymarket_exit_stop_loss_fraction if stop_loss_fraction is None else stop_loss_fraction
    min_bid_size = settings.polymarket_exit_min_bid_size if min_bid_size is None else min_bid_size

    positions = _load_open_polymarket_positions(db_path)
    evaluated = exited = skipped = 0
    notes: list[str] = []

    for p in positions:
        evaluated += 1
        try:
            bid = _best_bid(client.get_orderbook(p["ticker"]))
        except PolymarketAPIError as e:
            skipped += 1
            notes.append(f"{p['ticker'][:8]} no_book")
            continue
        if bid is None or bid["size"] < min_bid_size:
            skipped += 1
            notes.append(f"{p['ticker'][:8]} thin_bid")
            continue

        avg = float(p["avg_price"])
        contracts = int(p["net_contracts"])
        cost = avg * contracts
        current_value = bid["price"] * contracts
        unrealized = current_value - cost
        max_profit = contracts - cost
        reason = _exit_reason(
            entry=avg,
            bid=bid["price"],
            unrealized=unrealized,
            max_profit=max_profit,
            cost=cost,
            take_profit_cents=take_profit_cents,
            profit_capture=profit_capture,
            stop_loss_cents=stop_loss_cents,
            stop_loss_fraction=stop_loss_fraction,
        )
        if not reason:
            skipped += 1
            continue

        sell_contracts = min(contracts, int(bid["size"]))
        if sell_contracts <= 0:
            skipped += 1
            continue
        executor.execute_leg(
            strategy="polymarket_exit",
            venue="polymarket",
            ticker=str(p["ticker"]),
            side=str(p["side"]),
            action="sell",
            contracts=sell_contracts,
            price=bid["price"],
            is_maker=False,
            notes=f"{reason} entry={avg:.3f} bid={bid['price']:.3f}",
        )
        exited += 1
        notes.append(f"{p['ticker'][:8]} {reason} {avg:.3f}->{bid['price']:.3f}")

    return ExitRunSummary(
        open_positions=len(positions),
        evaluated=evaluated,
        exited=exited,
        skipped=skipped,
        notes="; ".join(notes[:5]),
    )


def build_default_executor() -> PaperExecutor:
    return build_momentum_executor()


def _load_open_polymarket_positions(db_path: str) -> list[sqlite3.Row]:
    with _conn(db_path) as c:
        return c.execute(
            """
            SELECT
              venue, ticker, side,
              SUM(CASE WHEN action='buy' THEN contracts ELSE -contracts END) AS net_contracts,
              SUM(CASE WHEN action='buy' THEN cost ELSE 0 END)
                / MAX(1, SUM(CASE WHEN action='buy' THEN contracts ELSE 0 END)) AS avg_price
            FROM paper_trades
            WHERE venue='polymarket'
            GROUP BY venue, ticker, side
            HAVING net_contracts > 0
            """
        ).fetchall()


def _exit_reason(
    *,
    entry: float,
    bid: float,
    unrealized: float,
    max_profit: float,
    cost: float,
    take_profit_cents: float,
    profit_capture: float,
    stop_loss_cents: float,
    stop_loss_fraction: float,
) -> Optional[str]:
    if bid - entry >= take_profit_cents:
        return "take_profit_cents"
    if max_profit > 0 and unrealized / max_profit >= profit_capture:
        return "take_profit_capture"
    if entry - bid >= stop_loss_cents:
        return "stop_loss_cents"
    if cost > 0 and unrealized <= -(cost * stop_loss_fraction):
        return "stop_loss_fraction"
    return None
