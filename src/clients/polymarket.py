"""
Polymarket read-only client.

V1 scope:
- Markets (gamma-api): metadata, condition_id, outcome token IDs.
- Trades by wallet (data-api): for leaderboard + consensus detection.
- Leaderboard (lb-api): top wallets by P&L / volume.
- CLOB orderbook (clob.polymarket.com): used at signal time to mark
  paper fills at executable prices (NOT at the copied wallet's price).

What this client deliberately does NOT do (yet):
- Sign or submit orders. Live execution is a later phase.
- Subscribe to WebSocket trade streams. Polling is fine for v1.

Offline mode:
    If `settings.polymarket_replay_dir` is set OR a `replay_dir` is passed
    to the constructor, every HTTP call is replaced with a JSON fixture
    lookup. The path layout is:

        <replay_dir>/<endpoint_slug>.json

    where `endpoint_slug` is the request path with '/' replaced by '_'
    and any query string hashed. Tests build small fixture trees and
    inject them, so nothing in this client ever hits live Polymarket
    during pytest.

Normalized models:
- `PolymarketMarket`  — keyed by `condition_id` (immutable), carries the
  per-outcome token IDs we use for orderbook + position keys.
- `PolymarketTrade`   — wallet, condition_id, outcome_index, side
  ("BUY"/"SELL"), size (USDC), price, timestamp, tx_hash.
- `PolymarketWalletStats` — aggregate over a trade history slice.

We never key consensus on human-readable market slugs/titles (Polymarket
markets get renamed, multiple markets share titles, and YES/NO labels
swap). The unit of identity is always `(condition_id, outcome_token_id)`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import httpx

from src.config import settings


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalized data models — these are what the rest of the bot consumes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolymarketOutcome:
    index: int           # 0 = first listed outcome, 1 = second, etc.
    label: str           # e.g. "Yes", "No", or a multi-outcome label
    token_id: str        # ERC-1155 CLOB token id (decimal string)


@dataclass(frozen=True)
class PolymarketMarket:
    condition_id: str    # immutable identifier — primary key everywhere downstream
    question_id: Optional[str]
    slug: Optional[str]
    question: str
    closed: bool
    accepting_orders: bool
    outcomes: List[PolymarketOutcome]
    end_date_iso: Optional[str] = None
    volume_24h_usd: float = 0.0
    liquidity_usd: float = 0.0

    @property
    def is_binary(self) -> bool:
        return len(self.outcomes) == 2

    def outcome_by_index(self, idx: int) -> PolymarketOutcome:
        return self.outcomes[idx]


@dataclass(frozen=True)
class PolymarketTrade:
    """One filled trade by one wallet on one outcome token."""
    wallet: str
    condition_id: str
    outcome_index: int
    outcome_token_id: str
    side: str            # "BUY" or "SELL"
    size_shares: float   # # of outcome shares
    price: float         # USDC per share, 0..1
    timestamp_unix: int
    tx_hash: Optional[str] = None

    @property
    def notional_usd(self) -> float:
        return self.size_shares * self.price


@dataclass(frozen=True)
class PolymarketWalletStats:
    wallet: str
    n_trades: int
    n_markets: int
    n_resolved_markets: int
    realized_pnl_usd: float
    total_volume_usd: float
    first_trade_unix: Optional[int]
    last_trade_unix: Optional[int]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PolymarketAPIError(RuntimeError):
    pass


def _fixture_filename(endpoint: str, params: Optional[Mapping[str, Any]]) -> str:
    """Stable filename for a (endpoint, params) pair under the replay dir.

    We hash the query so fixtures don't pile up unbounded but stay
    deterministic. If `params` is empty, just use the slugged endpoint.
    """
    slug = endpoint.strip("/").replace("/", "_") or "root"
    if not params:
        return f"{slug}.json"
    q = json.dumps({k: params[k] for k in sorted(params)}, sort_keys=True, default=str)
    digest = hashlib.sha1(q.encode()).hexdigest()[:10]
    return f"{slug}__{digest}.json"


class PolymarketClient:
    """Async-friendly but currently sync — Polymarket endpoints are
    polling-friendly and we don't need overlap in v1."""

    def __init__(
        self,
        gamma_url: Optional[str] = None,
        data_url: Optional[str] = None,
        clob_url: Optional[str] = None,
        leaderboard_url: Optional[str] = None,
        replay_dir: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        timeout: float = 15.0,
    ):
        self.gamma_url = (gamma_url or settings.polymarket_gamma_url).rstrip("/")
        self.data_url = (data_url or settings.polymarket_data_url).rstrip("/")
        self.clob_url = (clob_url or settings.polymarket_clob_url).rstrip("/")
        self.leaderboard_url = (leaderboard_url or settings.polymarket_leaderboard_url).rstrip("/")
        self.replay_dir = Path(replay_dir or settings.polymarket_replay_dir or "") if (replay_dir or settings.polymarket_replay_dir) else None
        self._owns_client = http_client is None
        if http_client is not None:
            self._http: Optional[httpx.Client] = http_client
        elif self.replay_dir is not None:
            self._http = None  # never used in replay mode
        else:
            try:
                import truststore
                ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                verify: object = ctx
            except ImportError:
                verify = True
            self._http = httpx.Client(timeout=timeout, verify=verify)

    def close(self) -> None:
        if self._owns_client and self._http is not None:
            self._http.close()

    def __enter__(self) -> "PolymarketClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low-level GET — replay-aware
    # ------------------------------------------------------------------
    def _get_json(
        self,
        base: str,
        endpoint: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if self.replay_dir is not None:
            path = self.replay_dir / _fixture_filename(endpoint, params)
            if not path.exists():
                raise PolymarketAPIError(
                    f"Replay fixture missing for {endpoint} {dict(params or {})}: {path}"
                )
            with path.open() as f:
                return json.load(f)
        if self._http is None:
            raise PolymarketAPIError("HTTP client unavailable (no replay_dir and no http_client)")
        url = f"{base}{endpoint}"
        try:
            r = self._http.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise PolymarketAPIError(f"GET {url} failed: {e}") from e

    # ------------------------------------------------------------------
    # Markets (gamma-api)
    # ------------------------------------------------------------------
    def get_markets(self, *, active: bool = True, limit: int = 100, offset: int = 0) -> List[PolymarketMarket]:
        """List Polymarket markets. Returns normalized models."""
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if active:
            params["active"] = "true"
            params["closed"] = "false"
        payload = self._get_json(self.gamma_url, "/markets", params)
        return [self._parse_market(m) for m in payload]

    def get_market(self, condition_id: str) -> PolymarketMarket:
        payload = self._get_json(self.gamma_url, "/markets", {"condition_ids": condition_id})
        if not payload:
            raise PolymarketAPIError(f"Market not found: {condition_id}")
        return self._parse_market(payload[0])

    @staticmethod
    def _parse_market(m: Dict[str, Any]) -> PolymarketMarket:
        """Tolerate both the camelCase gamma shape and snake_case variants
        we see in fixtures. The fields here are the only ones the strategy
        depends on; we do not pretend to parse the whole gamma schema."""
        outcomes_raw = m.get("outcomes") or m.get("outcomePrices")
        token_ids = m.get("clobTokenIds") or m.get("clob_token_ids") or []
        if isinstance(outcomes_raw, str):
            outcomes_raw = json.loads(outcomes_raw)
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        outcomes: List[PolymarketOutcome] = []
        for i, label in enumerate(outcomes_raw or []):
            tid = str(token_ids[i]) if i < len(token_ids) else ""
            outcomes.append(PolymarketOutcome(index=i, label=str(label), token_id=tid))
        return PolymarketMarket(
            condition_id=str(m.get("conditionId") or m.get("condition_id") or ""),
            question_id=m.get("questionId") or m.get("question_id"),
            slug=m.get("slug"),
            question=str(m.get("question") or m.get("title") or ""),
            closed=bool(m.get("closed", False)),
            accepting_orders=bool(m.get("acceptingOrders", m.get("accepting_orders", True))),
            outcomes=outcomes,
            end_date_iso=m.get("endDate") or m.get("end_date"),
            volume_24h_usd=float(m.get("volume24hr", m.get("volume_24hr", 0.0)) or 0.0),
            liquidity_usd=float(m.get("liquidity", 0.0) or 0.0),
        )

    # ------------------------------------------------------------------
    # CLOB orderbook — used to mark paper fills at executable prices
    # ------------------------------------------------------------------
    def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        """Returns raw CLOB book payload: {bids: [...], asks: [...]}.
        The shape passes through unchanged because callers care about
        depth and only depth."""
        return self._get_json(self.clob_url, "/book", {"token_id": token_id})

    # ------------------------------------------------------------------
    # Wallet trades (data-api)
    # ------------------------------------------------------------------
    def get_wallet_trades(
        self,
        wallet: str,
        *,
        limit: int = 500,
        since_unix: Optional[int] = None,
    ) -> List[PolymarketTrade]:
        params: Dict[str, Any] = {"user": wallet, "limit": limit}
        if since_unix is not None:
            params["from"] = since_unix
        payload = self._get_json(self.data_url, "/trades", params)
        return [self._parse_trade(t) for t in payload]

    @staticmethod
    def _parse_trade(t: Dict[str, Any]) -> PolymarketTrade:
        return PolymarketTrade(
            wallet=str(t.get("proxyWallet") or t.get("user") or t.get("wallet") or "").lower(),
            condition_id=str(t.get("conditionId") or t.get("condition_id") or ""),
            outcome_index=int(t.get("outcomeIndex", t.get("outcome_index", 0))),
            outcome_token_id=str(t.get("asset") or t.get("token_id") or t.get("outcome_token_id") or ""),
            side=str(t.get("side", "BUY")).upper(),
            size_shares=float(t.get("size", t.get("size_shares", 0.0)) or 0.0),
            price=float(t.get("price", 0.0) or 0.0),
            timestamp_unix=int(t.get("timestamp", t.get("timestamp_unix", 0)) or 0),
            tx_hash=t.get("transactionHash") or t.get("tx_hash"),
        )

    # ------------------------------------------------------------------
    # Leaderboard (lb-api)
    # ------------------------------------------------------------------
    def get_leaderboard(
        self,
        *,
        window: str = "month",   # "day" | "week" | "month" | "all"
        metric: str = "profit",  # "profit" | "volume"
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Returns raw leaderboard rows. We do NOT trust the leaderboard's
        own ranking for cohort selection — `signals.smart_money` re-ranks
        these using its own criteria (Sharpe + min trades + recency etc.)
        and treats the leaderboard as a candidate pool only."""
        params = {"window": window, "metric": metric, "limit": limit}
        return self._get_json(self.leaderboard_url, "/leaderboard", params)
