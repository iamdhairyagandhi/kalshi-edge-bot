"""Cross-venue Polymarket/Kalshi spread scanner."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from src.clients.kalshi import KalshiClient
from src.clients.polymarket import PolymarketClient
from src.config import settings
from src.jobs.arb_runner import fetch_open_markets
from src.strategies.cross_venue_spread import (
    CrossVenueSpread,
    build_spread,
    is_mve_combo_market,
    match_markets,
    parse_poly_top_book,
)
from src.utils.orderbook_parser import parse_orderbook


log = logging.getLogger(__name__)


DDL = """
CREATE TABLE IF NOT EXISTS cross_venue_scan_runs (
    run_id TEXT PRIMARY KEY,
    scanned_at_unix INTEGER NOT NULL,
    kalshi_markets INTEGER NOT NULL,
    kalshi_eligible_markets INTEGER NOT NULL DEFAULT 0,
    kalshi_excluded_mve INTEGER NOT NULL DEFAULT 0,
    polymarket_markets INTEGER NOT NULL,
    matched_markets INTEGER NOT NULL,
    books_checked INTEGER NOT NULL,
    candidates INTEGER NOT NULL,
    min_match_score REAL NOT NULL,
    min_spread REAL NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS cross_venue_spreads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    scanned_at_unix INTEGER NOT NULL,
    kalshi_ticker TEXT NOT NULL,
    kalshi_title TEXT NOT NULL,
    polymarket_condition_id TEXT NOT NULL,
    polymarket_question TEXT NOT NULL,
    polymarket_token_id TEXT NOT NULL,
    polymarket_outcome_index INTEGER NOT NULL,
    polymarket_outcome_label TEXT NOT NULL,
    match_score REAL NOT NULL,
    kalshi_yes_bid REAL NOT NULL,
    kalshi_yes_ask REAL NOT NULL,
    kalshi_yes_bid_size INTEGER NOT NULL,
    kalshi_yes_ask_size INTEGER NOT NULL,
    polymarket_yes_bid REAL NOT NULL,
    polymarket_yes_ask REAL NOT NULL,
    polymarket_yes_bid_size REAL NOT NULL,
    polymarket_yes_ask_size REAL NOT NULL,
    valuation_spread REAL NOT NULL,
    kalshi_bid_minus_poly_ask REAL NOT NULL,
    poly_bid_minus_kalshi_ask REAL NOT NULL,
    best_executable_spread REAL NOT NULL,
    direction TEXT NOT NULL,
    decision TEXT NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_cross_venue_spreads_run
    ON cross_venue_spreads(run_id, best_executable_spread DESC);
CREATE INDEX IF NOT EXISTS idx_cross_venue_runs_time
    ON cross_venue_scan_runs(scanned_at_unix DESC);
"""


@dataclass(frozen=True)
class CrossVenueSummary:
    run_id: str
    kalshi_markets: int
    kalshi_eligible_markets: int
    kalshi_excluded_mve: int
    polymarket_markets: int
    matched_markets: int
    books_checked: int
    candidates: int
    min_match_score: float
    min_spread: float
    notes: str = ""

    def __str__(self) -> str:
        bits = [
            f"run_id={self.run_id}",
            f"kalshi={self.kalshi_markets}",
            f"kalshi_eligible={self.kalshi_eligible_markets}",
            f"excluded_mve={self.kalshi_excluded_mve}",
            f"polymarket={self.polymarket_markets}",
            f"matched={self.matched_markets}",
            f"books={self.books_checked}",
            f"candidates={self.candidates}",
            f"min_match={self.min_match_score:.2f}",
            f"min_spread={self.min_spread:.3f}",
        ]
        if self.notes:
            bits.append(f"notes={self.notes}")
        return " ".join(bits)


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


def ensure_tables(db_path: str) -> None:
    with _conn(db_path) as c:
        c.executescript(DDL)
        cols = {r[1] for r in c.execute("PRAGMA table_info(cross_venue_scan_runs)").fetchall()}
        if "kalshi_eligible_markets" not in cols:
            c.execute(
                "ALTER TABLE cross_venue_scan_runs "
                "ADD COLUMN kalshi_eligible_markets INTEGER NOT NULL DEFAULT 0"
            )
        if "kalshi_excluded_mve" not in cols:
            c.execute(
                "ALTER TABLE cross_venue_scan_runs "
                "ADD COLUMN kalshi_excluded_mve INTEGER NOT NULL DEFAULT 0"
            )


def record_run(db_path: str, summary: CrossVenueSummary, spreads: list[CrossVenueSpread]) -> None:
    ensure_tables(db_path)
    scanned_at = int(time.time())
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR REPLACE INTO cross_venue_scan_runs(
                run_id, scanned_at_unix, kalshi_markets, kalshi_eligible_markets,
                kalshi_excluded_mve, polymarket_markets,
                matched_markets, books_checked, candidates, min_match_score,
                min_spread, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                summary.run_id, scanned_at, summary.kalshi_markets,
                summary.kalshi_eligible_markets, summary.kalshi_excluded_mve,
                summary.polymarket_markets, summary.matched_markets,
                summary.books_checked, summary.candidates,
                summary.min_match_score, summary.min_spread, summary.notes,
            ),
        )
        for s in spreads:
            m = s.match
            c.execute(
                """INSERT INTO cross_venue_spreads(
                    run_id, scanned_at_unix, kalshi_ticker, kalshi_title,
                    polymarket_condition_id, polymarket_question,
                    polymarket_token_id, polymarket_outcome_index,
                    polymarket_outcome_label, match_score,
                    kalshi_yes_bid, kalshi_yes_ask, kalshi_yes_bid_size,
                    kalshi_yes_ask_size, polymarket_yes_bid,
                    polymarket_yes_ask, polymarket_yes_bid_size,
                    polymarket_yes_ask_size, valuation_spread,
                    kalshi_bid_minus_poly_ask, poly_bid_minus_kalshi_ask,
                    best_executable_spread, direction, decision, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary.run_id, scanned_at, m.kalshi_ticker, m.kalshi_title,
                    m.polymarket_condition_id, m.polymarket_question,
                    m.polymarket_token_id, m.polymarket_outcome_index,
                    m.polymarket_outcome_label, m.score,
                    s.kalshi_yes_bid, s.kalshi_yes_ask, s.kalshi_yes_bid_size,
                    s.kalshi_yes_ask_size, s.polymarket_yes_bid,
                    s.polymarket_yes_ask, s.polymarket_yes_bid_size,
                    s.polymarket_yes_ask_size, s.valuation_spread,
                    s.kalshi_bid_minus_poly_ask, s.poly_bid_minus_kalshi_ask,
                    s.best_executable_spread, s.direction, s.decision, s.notes,
                ),
            )


def fetch_polymarket_markets(client: PolymarketClient, max_markets: int) -> list:
    markets = []
    offset = 0
    page = 100
    while len(markets) < max_markets:
        batch = client.get_markets(active=True, limit=min(page, max_markets - len(markets)), offset=offset)
        if not batch:
            break
        markets.extend(batch)
        offset += len(batch)
        if len(batch) < page:
            break
    return markets


async def run_once(
    kalshi_client: KalshiClient,
    polymarket_client: PolymarketClient,
    *,
    db_path: str = settings.db_path,
    max_kalshi_markets: int = 200,
    max_polymarket_markets: int = 200,
    min_match_score: float = 0.72,
    min_spread: float = 0.03,
    max_matches: int = 50,
    include_mve: bool = False,
) -> tuple[CrossVenueSummary, list[CrossVenueSpread]]:
    run_id = uuid.uuid4().hex[:12]
    kalshi_markets_task = asyncio.create_task(fetch_open_markets(kalshi_client, max_kalshi_markets))
    poly_markets = await asyncio.to_thread(fetch_polymarket_markets, polymarket_client, max_polymarket_markets)
    raw_kalshi_markets = await kalshi_markets_task
    excluded_mve = 0 if include_mve else sum(1 for m in raw_kalshi_markets if is_mve_combo_market(m))
    kalshi_markets = raw_kalshi_markets if include_mve else [
        m for m in raw_kalshi_markets if not is_mve_combo_market(m)
    ]

    matches = match_markets(
        kalshi_markets,
        poly_markets,
        min_score=min_match_score,
        max_matches=max_matches,
    )
    spreads: list[CrossVenueSpread] = []
    books_checked = 0
    for match in matches:
        try:
            k_payload = await kalshi_client.get_orderbook(match.kalshi_ticker, depth=5)
            k_book = parse_orderbook(match.kalshi_ticker, k_payload)
            p_payload = await asyncio.to_thread(polymarket_client.get_orderbook, match.polymarket_token_id)
            p_book = parse_poly_top_book(p_payload)
        except Exception as e:  # noqa: BLE001
            log.debug("cross-venue book fetch failed for %s/%s: %s", match.kalshi_ticker, match.polymarket_condition_id, e)
            continue
        if k_book is None or p_book is None:
            continue
        books_checked += 1
        spreads.append(build_spread(match, k_book, p_book, min_spread=min_spread))

    spreads.sort(key=lambda s: s.best_executable_spread, reverse=True)
    candidates = sum(1 for s in spreads if s.decision == "candidate")
    if not kalshi_markets and excluded_mve:
        notes = "no eligible Kalshi single markets after excluding MVE combo markets"
    elif not matches:
        notes = "no equivalent market text matches"
    elif not candidates:
        notes = "no strong executable spreads"
    else:
        notes = "candidate spread(s) found"
    summary = CrossVenueSummary(
        run_id=run_id,
        kalshi_markets=len(raw_kalshi_markets),
        kalshi_eligible_markets=len(kalshi_markets),
        kalshi_excluded_mve=excluded_mve,
        polymarket_markets=len(poly_markets),
        matched_markets=len(matches),
        books_checked=books_checked,
        candidates=candidates,
        min_match_score=min_match_score,
        min_spread=min_spread,
        notes=notes,
    )
    record_run(db_path, summary, spreads)
    return summary, spreads
