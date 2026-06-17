"""Weather/climate edge scanner."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.clients.kalshi import KalshiClient
from src.clients.nws import NWSAPIError, NWSClient
from src.clients.open_meteo import OpenMeteoAPIError, OpenMeteoClient
from src.clients.weather_ai import WeatherAIClient, WeatherAIError
from src.clients.polymarket import PolymarketClient, PolymarketMarket
from src.config import settings
from src.jobs.cross_venue_runner import fetch_polymarket_markets
from src.paper.executor import PaperExecutor
from src.strategies.weather_edge import WeatherEstimate, estimate_contract, parse_weather_contract
from src.utils.orderbook_parser import parse_orderbook


SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_specialist_estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    recorded_unix INTEGER NOT NULL,
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    title TEXT,
    city TEXT,
    kind TEXT,
    threshold REAL,
    comparator TEXT,
    forecast_value REAL,
    sigma REAL,
    p_yes REAL,
    yes_bid REAL,
    yes_ask REAL,
    edge_yes REAL,
    edge_no REAL,
    recommendation TEXT,
    confidence REAL,
    ai_used INTEGER NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_weather_specialist_time
    ON weather_specialist_estimates(recorded_unix DESC);
CREATE INDEX IF NOT EXISTS idx_weather_specialist_rec
    ON weather_specialist_estimates(recommendation, confidence DESC);
"""


@dataclass(frozen=True)
class WeatherScanSummary:
    kalshi_scanned: int
    polymarket_scanned: int
    parsed_contracts: int
    forecasts_loaded: int
    estimates: int
    candidates: int
    model_forecasts_loaded: int = 0
    executed: int = 0
    execution_rejected: int = 0
    ai_used: int = 0
    ai_failed: int = 0
    run_id: str = ""

    def __str__(self) -> str:
        return (
            (f"run_id={self.run_id} " if self.run_id else "")
            + f"kalshi={self.kalshi_scanned} polymarket={self.polymarket_scanned} "
            f"parsed={self.parsed_contracts} forecasts={self.forecasts_loaded} "
            f"models={self.model_forecasts_loaded} estimates={self.estimates} candidates={self.candidates} "
            f"executed={self.executed} exec_rejected={self.execution_rejected} "
            f"ai_used={self.ai_used} ai_failed={self.ai_failed}"
        )


async def run_once(
    *,
    kalshi: KalshiClient,
    polymarket: PolymarketClient,
    nws: NWSClient,
    open_meteo: Optional[OpenMeteoClient] = None,
    max_kalshi_markets: int = 500,
    max_polymarket_markets: int = 500,
    min_edge: float = 0.08,
    safety_buffer: float = 0.03,
    min_confidence: float = 0.70,
    ai_client: Optional[WeatherAIClient] = None,
    ai_limit: int = 5,
    executor: Optional[PaperExecutor] = None,
    execute: bool = False,
    notional_per_trade_usd: float = 5.0,
    db_path: Optional[str] = None,
) -> tuple[WeatherScanSummary, list[WeatherEstimate]]:
    run_id = hex(int(time.time() * 1000))[2:]
    raw_kalshi_task = asyncio.create_task(fetch_weather_kalshi_markets(kalshi, max_kalshi_markets))
    poly_markets = await asyncio.to_thread(fetch_polymarket_markets, polymarket, max_polymarket_markets)
    kalshi_markets = await raw_kalshi_task

    contracts = []
    for m in kalshi_markets:
        title = _kalshi_title(m)
        c = parse_weather_contract(title, venue="kalshi", market_id=str(m.get("ticker") or ""))
        if c is not None:
            contracts.append((c, m, None))

    for m in poly_markets:
        c = parse_weather_contract(m.question, venue="polymarket", market_id=m.condition_id)
        if c is not None:
            contracts.append((c, None, m))

    forecast_cache: dict[tuple[float, float], Any] = {}
    model_cache: dict[tuple[float, float], dict[str, list[Any]]] = {}
    estimates: list[WeatherEstimate] = []
    ai_used = 0
    ai_failed = 0
    for contract, kalshi_market, poly_market in contracts:
        key = (contract.lat, contract.lon)
        if key not in forecast_cache:
            try:
                forecast_cache[key] = await asyncio.to_thread(nws.hourly_forecast, contract.lat, contract.lon)
            except NWSAPIError:
                forecast_cache[key] = []
        if open_meteo is not None and key not in model_cache:
            try:
                model_cache[key] = (await asyncio.to_thread(open_meteo.forecast_set, contract.lat, contract.lon)).by_source()
            except OpenMeteoAPIError:
                model_cache[key] = {}
        hourly = forecast_cache[key]
        if not hourly:
            continue
        model_hourly = model_cache.get(key, {})
        bid, ask = await _best_yes(kalshi, polymarket, kalshi_market, poly_market)
        baseline = estimate_contract(
            contract,
            hourly,
            model_hourly=model_hourly,
            yes_bid=bid,
            yes_ask=ask,
            min_edge=min_edge,
            safety_buffer=safety_buffer,
            min_confidence=min_confidence,
        )
        if baseline is None:
            continue
        ai_assessment = None
        if ai_client is not None and ai_used < ai_limit and _worth_ai_review(baseline):
            try:
                ai_assessment = await asyncio.to_thread(
                    ai_client.assess,
                    title=contract.title,
                    city=contract.city,
                    kind=contract.kind,
                    threshold=contract.threshold,
                    comparator=contract.comparator,
                    forecast_value=baseline.forecast_value,
                    baseline_probability=baseline.p_yes,
                    hourly=list(hourly),
                )
                ai_used += 1
            except WeatherAIError:
                ai_failed += 1
        est = (
            estimate_contract(
                contract,
                hourly,
                model_hourly=model_hourly,
                yes_bid=bid,
                yes_ask=ask,
                min_edge=min_edge,
                safety_buffer=safety_buffer,
                min_confidence=min_confidence,
                ai_assessment=ai_assessment,
            )
            if ai_assessment is not None
            else baseline
        )
        if est is not None:
            estimates.append(est)

    estimates.sort(key=lambda e: max(e.edge_to_buy_yes or -99, e.edge_to_buy_no or -99), reverse=True)
    candidates = [e for e in estimates if e.recommendation != "observe"]
    executed = execution_rejected = 0
    if execute and executor is not None:
        executed, execution_rejected = _execute_weather_candidates(
            executor=executor,
            estimates=candidates,
            notional_per_trade_usd=notional_per_trade_usd,
            run_id=run_id,
        )
    if db_path:
        _record_estimates(db_path, run_id, estimates)
    return (
        WeatherScanSummary(
            kalshi_scanned=len(kalshi_markets),
            polymarket_scanned=len(poly_markets),
            parsed_contracts=len(contracts),
            forecasts_loaded=sum(1 for v in forecast_cache.values() if v),
            model_forecasts_loaded=sum(len(v) for v in model_cache.values()),
            estimates=len(estimates),
            candidates=len(candidates),
            executed=executed,
            execution_rejected=execution_rejected,
            ai_used=ai_used,
            ai_failed=ai_failed,
            run_id=run_id,
        ),
        estimates,
    )


def _execute_weather_candidates(
    *,
    executor: PaperExecutor,
    estimates: list[WeatherEstimate],
    notional_per_trade_usd: float,
    run_id: str,
) -> tuple[int, int]:
    executed = rejected = 0
    for est in estimates:
        if est.contract.venue != "kalshi":
            rejected += 1
            continue
        if est.recommendation == "buy_yes":
            side = "YES"
            price = est.market_yes_ask
            edge = est.edge_to_buy_yes
        elif est.recommendation == "buy_no":
            side = "NO"
            price = None if est.market_yes_bid is None else 1.0 - est.market_yes_bid
            edge = est.edge_to_buy_no
        else:
            continue
        if price is None or price <= 0 or price >= 1:
            rejected += 1
            continue
        contracts = int(notional_per_trade_usd / max(0.01, price))
        if contracts <= 0:
            rejected += 1
            continue
        if _has_open_weather_position(executor.db_path, est.contract.market_id, side):
            rejected += 1
            continue
        try:
            executor.execute_leg(
                strategy="weather_specialist",
                venue="kalshi",
                ticker=est.contract.market_id,
                side=side,
                action="buy",
                contracts=contracts,
                price=price,
                is_maker=False,
                notes=(
                    f"weather={run_id} rec={est.recommendation} "
                    f"p_yes={est.p_yes:.3f} edge={edge or 0:.3f} conf={est.confidence:.2f}"
                ),
            )
            executed += 1
        except Exception:
            rejected += 1
    return executed, rejected


def _has_open_weather_position(db_path: str, ticker: str, side: str) -> bool:
    if not Path(db_path).exists():
        return False
    with _conn(db_path) as c:
        row = c.execute(
            """SELECT 1 FROM paper_positions
               WHERE venue='kalshi' AND ticker=? AND side=? AND closed_at IS NULL
               LIMIT 1""",
            (ticker, side.upper()),
        ).fetchone()
    return row is not None


def _worth_ai_review(est: WeatherEstimate) -> bool:
    best_edge = max(est.edge_to_buy_yes or -99.0, est.edge_to_buy_no or -99.0)
    return best_edge >= -0.03 or est.confidence >= 0.75


def build_weather_ai_client() -> Optional[WeatherAIClient]:
    if not settings.weather_ai_enabled:
        return None
    if not settings.openai_api_key:
        return None
    return WeatherAIClient(api_key=settings.openai_api_key, model=settings.weather_ai_model)


@contextmanager
def _conn(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _record_estimates(db_path: str, run_id: str, estimates: list[WeatherEstimate]) -> None:
    with _conn(db_path) as c:
        c.executescript(SCHEMA)
        now = int(time.time())
        c.executemany(
            """INSERT INTO weather_specialist_estimates(
                run_id, recorded_unix, venue, market_id, title, city, kind,
                threshold, comparator, forecast_value, sigma, p_yes,
                yes_bid, yes_ask, edge_yes, edge_no, recommendation,
                confidence, ai_used, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    run_id,
                    now,
                    e.contract.venue,
                    e.contract.market_id,
                    e.contract.title,
                    e.contract.city,
                    e.contract.kind,
                    e.contract.threshold,
                    e.contract.comparator,
                    e.forecast_value,
                    e.sigma,
                    e.p_yes,
                    e.market_yes_bid,
                    e.market_yes_ask,
                    e.edge_to_buy_yes,
                    e.edge_to_buy_no,
                    e.recommendation,
                    e.confidence,
                    1 if "AI adj=" in e.notes else 0,
                    e.notes,
                )
                for e in estimates
            ],
        )


async def fetch_weather_kalshi_markets(client: KalshiClient, max_markets: int) -> list[dict[str, Any]]:
    series = await _weather_series(client)
    markets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in series:
        if len(markets) >= max_markets:
            break
        ticker = s.get("ticker")
        if not ticker:
            continue
        cursor = None
        while len(markets) < max_markets:
            resp = await client.get_markets(
                limit=min(200, max_markets - len(markets)),
                cursor=cursor,
                status="open",
                series_ticker=str(ticker),
            )
            batch = resp.get("markets", [])
            for m in batch:
                mt = str(m.get("ticker") or "")
                if mt and mt not in seen:
                    markets.append(m)
                    seen.add(mt)
            cursor = resp.get("cursor")
            if not cursor or not batch:
                break
    return markets


async def _weather_series(client: KalshiClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        resp = await client.get_series(limit=200, cursor=cursor)
        batch = resp.get("series", [])
        for s in batch:
            if not isinstance(s, dict):
                continue
            text = " ".join(
                str(s.get(k) or "")
                for k in ("category", "tags", "title", "ticker")
            ).lower()
            if "climate and weather" in text or any(
                k in text for k in ("daily temperature", "snow and rain", "hourly temperature")
            ):
                out.append(s)
        cursor = resp.get("cursor")
        if not cursor or not batch:
            break
    priority = ("KXHIGH", "KXLOW", "KXHIGHT", "KXLOWT", "KXRAIN", "KXSNOW", "KXTEMP")
    return sorted(out, key=lambda s: not str(s.get("ticker", "")).startswith(priority))


def _kalshi_title(market: dict[str, Any]) -> str:
    return " ".join(str(market.get(k) or "") for k in ("title", "subtitle", "ticker"))


async def _best_yes(
    kalshi: KalshiClient,
    polymarket: PolymarketClient,
    kalshi_market: Optional[dict[str, Any]],
    poly_market: Optional[PolymarketMarket],
) -> tuple[Optional[float], Optional[float]]:
    if kalshi_market is not None:
        ticker = kalshi_market.get("ticker")
        if not ticker:
            return None, None
        try:
            book = await kalshi.get_orderbook(str(ticker), depth=1)
        except Exception:
            return None, None
        parsed = parse_orderbook(str(ticker), book)
        if parsed is None:
            return None, None
        return parsed.yes_best_bid or None, parsed.yes_best_ask or None

    if poly_market is not None and poly_market.outcomes:
        token = poly_market.outcomes[0].token_id
        try:
            book = await asyncio.to_thread(polymarket.get_orderbook, token)
        except Exception:
            return None, None
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        return _poly_best(bids, max), _poly_best(asks, min)
    return None, None


def _poly_best(levels: list[Any], chooser: Any) -> Optional[float]:
    prices = []
    for level in levels:
        if isinstance(level, dict) and level.get("price") is not None:
            try:
                prices.append(float(level["price"]))
            except (TypeError, ValueError):
                pass
    return chooser(prices) if prices else None
