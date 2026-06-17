"""Open-Meteo forecast clients.

We use two public Open-Meteo views:
- `/v1/forecast` for the best-match multi-provider forecast
- `/v1/gfs` for NOAA GFS/HRRR forecasts in the United States
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from src.clients.nws import HourlyForecast


class OpenMeteoAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenMeteoForecastSet:
    best_match: list[HourlyForecast]
    noaa_gfs_hrrr: list[HourlyForecast]

    def by_source(self) -> dict[str, list[HourlyForecast]]:
        out: dict[str, list[HourlyForecast]] = {}
        if self.best_match:
            out["open_meteo_best"] = self.best_match
        if self.noaa_gfs_hrrr:
            out["noaa_gfs_hrrr"] = self.noaa_gfs_hrrr
        return out


class OpenMeteoClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.open-meteo.com",
        timeout: float = 15.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._own_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._own_client:
            self._http.close()

    def forecast_set(self, lat: float, lon: float, *, forecast_days: int = 3) -> OpenMeteoForecastSet:
        best = self._hourly("/v1/forecast", lat, lon, forecast_days=forecast_days)
        noaa = self._hourly("/v1/gfs", lat, lon, forecast_days=forecast_days)
        return OpenMeteoForecastSet(best_match=best, noaa_gfs_hrrr=noaa)

    def _hourly(self, path: str, lat: float, lon: float, *, forecast_days: int) -> list[HourlyForecast]:
        try:
            r = self._http.get(
                f"{self.base_url}{path}",
                params={
                    "latitude": f"{lat:.4f}",
                    "longitude": f"{lon:.4f}",
                    "hourly": "temperature_2m,precipitation_probability",
                    "temperature_unit": "fahrenheit",
                    "timezone": "UTC",
                    "forecast_days": str(max(1, min(16, forecast_days))),
                },
            )
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPError as e:
            raise OpenMeteoAPIError(f"GET {path} failed: {e}") from e
        except ValueError as e:
            raise OpenMeteoAPIError(f"GET {path} returned non-json payload") from e
        return _parse_hourly(payload)


def _parse_hourly(payload: dict[str, Any]) -> list[HourlyForecast]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    pops = hourly.get("precipitation_probability") or []
    if not isinstance(times, list) or not isinstance(temps, list):
        return []

    out: list[HourlyForecast] = []
    for i, raw_time in enumerate(times):
        try:
            start = datetime.fromisoformat(str(raw_time)).replace(tzinfo=timezone.utc)
            temp = float(temps[i])
        except (TypeError, ValueError, IndexError):
            continue
        pop = None
        try:
            if i < len(pops) and pops[i] is not None:
                pop = float(pops[i])
        except (TypeError, ValueError):
            pop = None
        out.append(
            HourlyForecast(
                start_time=start,
                end_time=start + timedelta(hours=1),
                temperature_f=temp,
                short_forecast="Open-Meteo model",
                probability_of_precipitation=pop,
            )
        )
    return out
