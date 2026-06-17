"""
Overround arbitrage runner.

Main loop:
  1. Pull list of open binary markets from Kalshi
  2. For each, fetch the top-of-book orderbook (rate-limited)
  3. Scan with src.strategies.overround_arb
  4. For each opportunity, run pre-trade risk gates
  5. Execute via PaperExecutor (or live executor in v2)
  6. Sleep and repeat
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import List, Optional

from src.clients.kalshi import KalshiClient
from src.config import settings
from src.jobs.diagnostics import TradeDiagnostic, record_diagnostics
from src.paper.executor import PaperExecutor
from src.risk.gates import GateConfig, derive_event_family, evaluate_gates
from src.risk.invariants import (
    InvariantViolation,
    check_order_request,
    check_orderbook_sanity,
    check_portfolio_accounting,
)
from src.strategies.overround_arb import (
    ArbOpportunity, Orderbook, scan_orderbooks,
)
from src.utils.fees import taker_fee
from src.utils.orderbook_parser import parse_orderbook


logger = logging.getLogger(__name__)


def _market_title(market: dict) -> str:
    return str(market.get("title") or market.get("yes_sub_title") or market.get("ticker") or "")


async def fetch_open_markets(client: KalshiClient, max_markets: int) -> List[dict]:
    """Page through open markets until we have max_markets."""
    markets: List[dict] = []
    cursor: Optional[str] = None
    while len(markets) < max_markets:
        resp = await client.get_markets(limit=200, cursor=cursor, status="open")
        batch = resp.get("markets", [])
        if not batch:
            break
        markets.extend(batch)
        cursor = resp.get("cursor")
        if not cursor:
            break
    return markets[:max_markets]


async def fetch_orderbooks(
    client: KalshiClient, tickers: List[str], concurrency: int = 5
) -> List[Orderbook]:
    """Fetch top-of-book for many tickers with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)
    books: List[Orderbook] = []

    async def _one(t: str) -> None:
        async with sem:
            try:
                payload = await client.get_orderbook(t, depth=5)
                ob = parse_orderbook(t, payload)
                if ob is None:
                    return
                # Sanity-check the book before using it. Bad data here =
                # bad trades. Reject the whole book on any anomaly.
                report = check_orderbook_sanity(
                    ticker=ob.ticker,
                    yes_bid=ob.yes_best_bid, yes_ask=ob.yes_best_ask,
                    no_bid=ob.no_best_bid, no_ask=ob.no_best_ask,
                )
                if not report.passed:
                    logger.warning("Rejecting bad orderbook %s: %s", t, report.violations)
                    return
                books.append(ob)
            except Exception as e:
                logger.debug("orderbook fetch failed for %s: %s", t, e)

    await asyncio.gather(*(_one(t) for t in tickers))
    return books


def filter_and_execute(
    opps: List[ArbOpportunity],
    executor: PaperExecutor,
    markets_by_ticker: dict,
    gate_config: Optional[GateConfig] = None,
) -> int:
    """Run gates on each opportunity and execute if it passes. Returns # executed."""
    cfg = gate_config or GateConfig()
    executed = 0
    for opp in opps:
        market = markets_by_ticker.get(opp.ticker, {})
        volume_24h = float(market.get("volume_24h", 0))
        family = derive_event_family(opp.ticker)
        # For an arb leg, capital required = (yes_price + no_price) * contracts
        # since we're buying both sides for ~$1 of collateral per contract
        cost = (opp.yes_price + opp.no_price) * opp.contracts

        # Arb has near-certain payoff; "Kelly" is mostly irrelevant but we set it
        # to the edge / cost ratio as a meaningful proxy.
        kelly_proxy = opp.net_edge_per_contract / max(opp.yes_price + opp.no_price, 0.01)

        gate = evaluate_gates(
            portfolio=executor.portfolio.to_gate_state(),
            ticker=opp.ticker,
            event_family=family,
            proposed_cost=cost,
            market_volume_24h=volume_24h,
            kelly_fraction_value=kelly_proxy,
            config=cfg,
        )
        if not gate.passed:
            logger.info("ARB skipped %s: %s", opp.ticker, "; ".join(gate.failures))
            continue
        try:
            executor.execute_arb(opp)
            executed += 1
            logger.info(
                "ARB executed %s %s x%d net=$%.2f",
                opp.ticker, opp.direction, opp.contracts, opp.net_profit_total,
            )
        except Exception as e:
            logger.exception("Execution failed for %s: %s", opp.ticker, e)
    return executed


def _near_miss_diagnostics(
    books: List[Orderbook],
    markets_by_ticker: dict,
    *,
    min_net_edge_per_contract: float,
    safety_margin: float,
    limit: int = 10,
) -> List[TradeDiagnostic]:
    rows: List[TradeDiagnostic] = []
    scored: List[tuple[float, TradeDiagnostic]] = []
    for book in books:
        candidates = []
        contracts = min(book.yes_best_ask_size, book.no_best_ask_size, 50)
        if contracts > 0:
            fees = taker_fee(contracts, book.yes_best_ask) + taker_fee(contracts, book.no_best_ask)
            net = 1.0 - (book.yes_best_ask + book.no_best_ask) - (fees / contracts) - safety_margin
            candidates.append(("buy_both", net, book.yes_best_ask + book.no_best_ask))
        contracts = min(book.yes_best_bid_size, book.no_best_bid_size, 50)
        if contracts > 0:
            fees = taker_fee(contracts, book.yes_best_bid) + taker_fee(contracts, book.no_best_bid)
            net = (book.yes_best_bid + book.no_best_bid) - 1.0 - (fees / contracts) - safety_margin
            candidates.append(("sell_both", net, book.yes_best_bid + book.no_best_bid))
        if not candidates:
            market = markets_by_ticker.get(book.ticker, {})
            scored.append((
                999.0,
                TradeDiagnostic(
                    strategy="overround_arb",
                    venue="kalshi",
                    market_id=book.ticker,
                    market_title=_market_title(market),
                    decision="blocked",
                    reason="insufficient_two_sided_depth",
                    metric_name="two_sided_depth",
                    metric_value=0.0,
                    threshold_value=1.0,
                    details=(
                        f"yes_bid_size={book.yes_best_bid_size} no_bid_size={book.no_best_bid_size} "
                        f"yes_ask_size={book.yes_best_ask_size} no_ask_size={book.no_best_ask_size}"
                    ),
                ),
            ))
            continue
        side, net_edge, observed = max(candidates, key=lambda x: x[1])
        if net_edge >= min_net_edge_per_contract:
            continue
        gap = min_net_edge_per_contract - net_edge
        market = markets_by_ticker.get(book.ticker, {})
        scored.append((
            gap,
            TradeDiagnostic(
                strategy="overround_arb",
                venue="kalshi",
                market_id=book.ticker,
                market_title=_market_title(market),
                side=side,
                decision="near_miss",
                reason="edge_below_threshold",
                metric_name="net_edge_per_contract",
                metric_value=net_edge,
                threshold_value=min_net_edge_per_contract,
                observed_price=observed,
                details=f"gap={gap:.4f} safety_margin={safety_margin:.4f}",
            ),
        ))
    scored.sort(key=lambda x: x[0])
    rows.extend(row for _, row in scored[:limit])
    return rows


async def run_once(
    client: KalshiClient,
    executor: PaperExecutor,
    max_markets: int,
    gate_config: Optional[GateConfig] = None,
    min_net_edge_per_contract: Optional[float] = None,
    safety_margin: Optional[float] = None,
) -> dict:
    """Single scan pass. Returns summary stats."""
    # Invariant check on portfolio state BEFORE we do anything
    p = executor.portfolio
    open_cost = sum(pos.cost for pos in p.positions.values())
    inv = check_portfolio_accounting(
        starting_bankroll=p.starting_bankroll,
        cash=p.cash,
        open_positions_cost=open_cost,
        realized_pnl=p.realized_pnl,
    )
    inv.raise_if_failed()    # halts the loop on corruption

    markets = await fetch_open_markets(client, max_markets)
    tickers = [m["ticker"] for m in markets if m.get("ticker")]
    markets_by_ticker = {m["ticker"]: m for m in markets}

    logger.info("Fetching orderbooks for %d markets...", len(tickers))
    books = await fetch_orderbooks(client, tickers)
    logger.info("Got %d non-empty orderbooks", len(books))

    min_edge = (
        settings.kalshi_arb_min_net_edge
        if min_net_edge_per_contract is None
        else min_net_edge_per_contract
    )
    margin = (
        settings.kalshi_arb_safety_margin
        if safety_margin is None
        else safety_margin
    )
    opps = scan_orderbooks(
        books,
        min_net_edge_per_contract=min_edge,
        safety_margin=margin,
    )
    logger.info("Found %d arb opportunities", len(opps))

    try:
        record_diagnostics(
            executor.db_path,
            _near_miss_diagnostics(
                books,
                markets_by_ticker,
                min_net_edge_per_contract=min_edge,
                safety_margin=margin,
            ),
        )
    except sqlite3.Error as e:
        logger.warning("trade diagnostics write failed: %s", e)

    executed = filter_and_execute(opps, executor, markets_by_ticker, gate_config)

    return {
        "markets_scanned": len(tickers),
        "books_observed": len(books),
        "opportunities": len(opps),
        "executed": executed,
        "cash": executor.portfolio.cash,
        "realized_pnl": executor.portfolio.realized_pnl,
        "open_positions": len(executor.portfolio.positions),
    }


async def run_loop(
    stop_after_iterations: Optional[int] = None,
    *,
    max_markets: Optional[int] = None,
    min_net_edge_per_contract: Optional[float] = None,
    safety_margin: Optional[float] = None,
) -> None:
    client = KalshiClient()
    executor = PaperExecutor(settings.db_path, settings.starting_bankroll)
    logger.info(
        "Starting overround arb loop | paper=%s bankroll=$%.2f interval=%ds",
        settings.paper_trading, settings.starting_bankroll, settings.scan_interval_seconds,
    )
    iters = 0
    try:
        while True:
            try:
                summary = await run_once(
                    client,
                    executor,
                    max_markets or settings.max_markets_per_scan,
                    min_net_edge_per_contract=min_net_edge_per_contract,
                    safety_margin=safety_margin,
                )
                logger.info("SUMMARY %s", summary)
            except InvariantViolation as e:
                # HARD STOP. Bot state is corrupted; do not continue trading.
                logger.critical("INVARIANT VIOLATED, HALTING: %s", e)
                break
            except Exception:
                logger.exception("Scan iteration failed")
            iters += 1
            if stop_after_iterations is not None and iters >= stop_after_iterations:
                break
            await asyncio.sleep(settings.scan_interval_seconds)
    finally:
        await client.close()
