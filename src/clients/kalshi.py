"""
Kalshi REST client.

Adapted from ryanfrigo/kalshi-ai-trading-bot (MIT) and trimmed to the
endpoints we actually use. RSA-PSS request signing per Kalshi v2 API spec:
https://trading-api.readme.io/reference/getting-started

Design notes:
- No logging framework dependency — plain stdlib logging
- All endpoints async
- Retries only on 429 / 5xx with exponential backoff
- Public market endpoints don't require auth
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from src.config import settings


logger = logging.getLogger(__name__)


class KalshiAPIError(Exception):
    pass


class KalshiClient:
    """Async Kalshi v2 API client (REST)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 5,
        backoff_factor: float = 0.5,
        rate_limit_delay: float = 0.2,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.api_key = api_key or settings.kalshi_api_key
        self.base_url = (base_url or settings.kalshi_base_url).rstrip("/")
        self.private_key_path = private_key_path or settings.kalshi_private_key_path
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.rate_limit_delay = rate_limit_delay
        self._private_key = None
        self._owns_client = http_client is None
        if http_client is None:
            try:
                import ssl
                import truststore
                ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                verify: object = ssl_ctx
            except ImportError:
                verify = True
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                verify=verify,
            )
        else:
            self._client = http_client

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _load_private_key(self) -> None:
        if self._private_key is not None:
            return
        path = Path(self.private_key_path)
        if not path.exists():
            raise KalshiAPIError(f"Private key not found: {self.private_key_path}")
        with open(path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(f.read(), password=None)

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        self._load_private_key()
        # Defense: Kalshi requires the FULL path from host root (including
        # /trade-api/v2 prefix) and NO query string. This is the most common
        # signing bug and produces silent 401s. Assert it loudly here.
        if "?" in path:
            raise KalshiAPIError(f"Sign path must not contain query string: {path}")
        if not (path.startswith("/trade-api/v2") or path.startswith("/trade-api/ws/v2")):
            raise KalshiAPIError(
                f"Sign path must start with /trade-api/v2: {path}"
            )
        message = (timestamp_ms + method.upper() + path).encode("utf-8")
        sig = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("utf-8")

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if require_auth:
            if not self.api_key:
                raise KalshiAPIError("KALSHI_API_KEY not set; cannot make authenticated request")
            ts = str(int(time.time() * 1000))
            headers.update({
                "KALSHI-ACCESS-KEY": self.api_key,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
            })

        body = json.dumps(json_body, separators=(",", ":")) if json_body else None
        if params:
            url = f"{url}?{urlencode(params)}"

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                if self.rate_limit_delay > 0:
                    await asyncio.sleep(self.rate_limit_delay)
                resp = await self._client.request(method, url, headers=headers, content=body)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_exc = e
                code = e.response.status_code
                if code == 429 or code >= 500:
                    sleep = self.backoff_factor * (2 ** attempt)
                    logger.warning("Kalshi %s %s -> %d, retry in %.2fs", method, path, code, sleep)
                    await asyncio.sleep(sleep)
                    continue
                raise KalshiAPIError(f"HTTP {code} {method} {path}: {e.response.text}")
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                sleep = self.backoff_factor * (2 ** attempt)
                logger.warning("Network error on %s %s: %s, retry in %.2fs", method, path, e, sleep)
                await asyncio.sleep(sleep)

        raise KalshiAPIError(f"Failed after {self.max_retries} retries: {last_exc}")

    # ------------------------------------------------------------------
    # Public market data (no auth needed)
    # ------------------------------------------------------------------
    async def get_market(self, ticker: str) -> Dict[str, Any]:
        return await self._request("GET", f"/trade-api/v2/markets/{ticker}", require_auth=False)

    async def get_markets(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        tickers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if cursor: params["cursor"] = cursor
        if event_ticker: params["event_ticker"] = event_ticker
        if series_ticker: params["series_ticker"] = series_ticker
        if status: params["status"] = status
        if tickers: params["tickers"] = ",".join(tickers)
        return await self._request("GET", "/trade-api/v2/markets", params=params, require_auth=False)

    async def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/trade-api/v2/markets/{ticker}/orderbook",
            params={"depth": depth}, require_auth=False,
        )

    # ------------------------------------------------------------------
    # Account (auth required)
    # ------------------------------------------------------------------
    async def get_balance(self) -> Dict[str, Any]:
        return await self._request("GET", "/trade-api/v2/portfolio/balance")

    async def get_positions(self, ticker: Optional[str] = None) -> Dict[str, Any]:
        params = {"ticker": ticker} if ticker else None
        return await self._request("GET", "/trade-api/v2/portfolio/positions", params=params)

    async def get_orders(self, ticker: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if ticker: params["ticker"] = ticker
        if status: params["status"] = status
        return await self._request("GET", "/trade-api/v2/portfolio/orders", params=params or None)

    async def place_order(
        self,
        ticker: str,
        client_order_id: str,
        side: str,             # "yes" or "no"
        action: str,           # "buy" or "sell"
        count: int,
        order_type: str = "limit",
        yes_price: Optional[int] = None,   # legacy cents (1-99)
        no_price: Optional[int] = None,
        yes_price_dollars: Optional[str] = None,  # new format, e.g. "0.520000"
        no_price_dollars: Optional[str] = None,
        time_in_force: Optional[str] = None,      # good_till_canceled | fill_or_kill | immediate_or_cancel
        post_only: bool = False,                  # maker-only (rejected if would cross)
        expiration_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Place an order. Kalshi deduplicates on client_order_id (409 = success).

        Note: as of 2025, market orders are gone — all orders are limits.
        Prefer post_only=True for fee-sensitive maker strategies."""
        body: Dict[str, Any] = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
        }
        if yes_price is not None: body["yes_price"] = yes_price
        if no_price is not None: body["no_price"] = no_price
        if yes_price_dollars is not None: body["yes_price_dollars"] = yes_price_dollars
        if no_price_dollars is not None: body["no_price_dollars"] = no_price_dollars
        if time_in_force is not None: body["time_in_force"] = time_in_force
        if post_only: body["post_only"] = True
        if expiration_ts is not None: body["expiration_ts"] = expiration_ts
        return await self._request("POST", "/trade-api/v2/portfolio/orders", json_body=body)

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "KalshiClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
