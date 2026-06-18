"""
The Odds API client (https://the-odds-api.com).

Free tier: 500 requests / month, no SGP combined-price endpoint.

We expose the markets we need for the bet builder leg-by-leg:
  - h2h  (1X2)
  - totals  (over/under N goals)
  - btts  (both teams to score)
  - player_goal_scorer_anytime  (where bookmaker exposes it)
  - alternate_totals (line shopping)
  - cards markets are inconsistently named per book; we surface raw.

The free endpoint returns decimal odds across multiple bookmakers.
We expose a small, typed wrapper: `best_decimal(...)` picks the best price
across all responding books for a given market+selection.

Pinnacle prices (when present in the response) are surfaced separately
because they're our CLV benchmark.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

import httpx


_LOG = logging.getLogger(__name__)
_BASE = "https://api.the-odds-api.com/v4"


class OddsApiError(RuntimeError):
    """Raised when The Odds API returns a non-2xx or unexpected payload."""


@dataclass
class OddsApiClient:
    api_key: Optional[str] = None
    region: str = "eu"   # eu/us/uk/au; eu has Pinnacle.
    timeout_s: float = 12.0
    verify_ssl: bool = True
    base_url: str = _BASE
    user_agent: str = "kalshi-edge-bot/0.2"

    _client: Optional[httpx.Client] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("ODDS_API_KEY")

    # ------------------------------------------------------------------
    def _client_or_make(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_s,
                verify=self.verify_ssl,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "OddsApiClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # raw endpoints
    # ------------------------------------------------------------------
    def sports(self) -> List[dict]:
        return self._get("/sports")

    def odds(
        self,
        sport_key: str = "soccer_fifa_world_cup",
        markets: str = "h2h,totals,btts",
        bookmakers: Optional[str] = None,
        odds_format: str = "decimal",
    ) -> List[dict]:
        params: Dict[str, str] = {
            "regions": self.region,
            "markets": markets,
            "oddsFormat": odds_format,
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        return self._get(f"/sports/{sport_key}/odds", params=params)

    def events(self, sport_key: str) -> List[dict]:
        """Fetch upcoming events/fixtures for a sport."""
        return self._get(f"/sports/{sport_key}/events")

    def event_odds(
        self,
        sport_key: str,
        event_id: str,
        markets: str,
        bookmakers: Optional[str] = None,
    ) -> dict:
        params: Dict[str, str] = {
            "regions": self.region,
            "markets": markets,
            "oddsFormat": "decimal",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        out = self._get(
            f"/sports/{sport_key}/events/{event_id}/odds",
            params=params,
            expect_list=False,
        )
        return out  # type: ignore[return-value]

    def _get(self, path: str, *, params: Optional[Mapping[str, str]] = None, expect_list: bool = True):
        if not self.api_key:
            raise OddsApiError(
                "ODDS_API_KEY not set; pass api_key= or export the env var."
            )
        full_params = dict(params or {})
        full_params["apiKey"] = self.api_key
        url = f"{self.base_url}{path}"
        try:
            r = self._client_or_make().get(url, params=full_params)
        except httpx.HTTPError as e:
            raise OddsApiError(f"HTTP error: {e}") from e
        if r.status_code == 401:
            raise OddsApiError("401 unauthorized — bad API key")
        if r.status_code == 429:
            raise OddsApiError("429 rate-limited or monthly quota exhausted")
        if r.status_code >= 400:
            body = r.text[:300]
            if "<html" in body.lower() or "<!doctype html" in body.lower():
                raise OddsApiError(
                    f"{r.status_code}: HTML response instead of JSON "
                    "(likely captive portal, proxy login, blocked request, or provider outage)"
                )
            raise OddsApiError(f"{r.status_code}: {body}")
        try:
            data = r.json()
        except ValueError as e:
            raise OddsApiError(f"non-JSON response: {e}") from e
        if expect_list and not isinstance(data, list):
            raise OddsApiError(f"expected list, got {type(data).__name__}")
        return data

    # ------------------------------------------------------------------
    # convenience
    # ------------------------------------------------------------------
    @staticmethod
    def best_decimal(
        event: Mapping[str, object],
        market_key: str,
        outcome_name: str,
        *,
        ignore_books: Optional[List[str]] = None,
    ) -> Optional[float]:
        """Best (highest) decimal price for a (market, selection) across books."""
        ignore = set(ignore_books or [])
        best: Optional[float] = None
        for book in event.get("bookmakers", []) or []:
            if book.get("key") in ignore:
                continue
            for mkt in book.get("markets", []) or []:
                if mkt.get("key") != market_key:
                    continue
                for o in mkt.get("outcomes", []) or []:
                    if o.get("name") == outcome_name:
                        try:
                            d = float(o["price"])
                        except (KeyError, ValueError, TypeError):
                            continue
                        if best is None or d > best:
                            best = d
        return best

    @staticmethod
    def pinnacle_decimal(
        event: Mapping[str, object],
        market_key: str,
        outcome_name: str,
    ) -> Optional[float]:
        for book in event.get("bookmakers", []) or []:
            if book.get("key") != "pinnacle":
                continue
            for mkt in book.get("markets", []) or []:
                if mkt.get("key") != market_key:
                    continue
                for o in mkt.get("outcomes", []) or []:
                    if o.get("name") == outcome_name:
                        try:
                            return float(o["price"])
                        except (KeyError, ValueError, TypeError):
                            return None
        return None
