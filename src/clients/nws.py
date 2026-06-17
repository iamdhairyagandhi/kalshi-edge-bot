"""Small National Weather Service API client.

The NWS API is public and works well for a first weather-market model:
`/points/{lat},{lon}` resolves a location to a forecast grid, and the
returned `forecastHourly` URL provides hourly point forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx


class NWSAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class HourlyForecast:
    start_time: datetime
    end_time: datetime
    temperature_f: float
    short_forecast: str
    probability_of_precipitation: Optional[float] = None


class NWSClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.weather.gov",
        user_agent: str = "kalshi-edge-bot/0.1 contact=local",
        timeout: float = 15.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._own_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/geo+json, application/json",
            },
        )

    def close(self) -> None:
        if self._own_client:
            self._http.close()

    def __enter__(self) -> "NWSClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_json(self, url_or_path: str) -> dict[str, Any]:
        url = url_or_path if url_or_path.startswith("http") else f"{self.base_url}{url_or_path}"
        try:
            r = self._http.get(url)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPError as e:
            raise NWSAPIError(f"GET {url} failed: {e}") from e
        except ValueError as e:
            raise NWSAPIError(f"GET {url} returned non-json payload") from e
        if not isinstance(payload, dict):
            raise NWSAPIError(f"GET {url} returned unexpected payload")
        return payload

    def hourly_forecast(self, lat: float, lon: float) -> list[HourlyForecast]:
        point = self._get_json(f"/points/{lat:.4f},{lon:.4f}")
        forecast_url = (
            point.get("properties", {}).get("forecastHourly")
            if isinstance(point.get("properties"), dict)
            else None
        )
        if not forecast_url:
            raise NWSAPIError(f"No forecastHourly URL for point {lat},{lon}")

        payload = self._get_json(str(forecast_url))
        periods = payload.get("properties", {}).get("periods", [])
        if not isinstance(periods, list):
            return []

        out: list[HourlyForecast] = []
        for p in periods:
            if not isinstance(p, dict):
                continue
            try:
                start = _parse_dt(str(p["startTime"]))
                end = _parse_dt(str(p["endTime"]))
                temp = float(p["temperature"])
            except (KeyError, TypeError, ValueError):
                continue
            pop = _nested_value(p, "probabilityOfPrecipitation", "value")
            out.append(
                HourlyForecast(
                    start_time=start,
                    end_time=end,
                    temperature_f=temp,
                    short_forecast=str(p.get("shortForecast") or ""),
                    probability_of_precipitation=float(pop) if pop is not None else None,
                )
            )
        return out


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _nested_value(obj: dict[str, Any], key: str, child: str) -> Any:
    v = obj.get(key)
    if isinstance(v, dict):
        return v.get(child)
    return None
