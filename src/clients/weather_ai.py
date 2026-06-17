"""Optional AI assessor for weather-market forecasts.

The AI layer is intentionally narrow: it reviews the NWS-derived forecast
features and returns small probability/uncertainty adjustments. It never
fetches weather data, invents observations, or directly decides trades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from src.clients.nws import HourlyForecast


class WeatherAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeatherAIAssessment:
    probability_adjustment: float
    uncertainty_multiplier: float
    confidence: float
    rationale: str


class WeatherAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 20.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._own_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._own_client:
            self._http.close()

    def assess(
        self,
        *,
        title: str,
        city: str,
        kind: str,
        threshold: float,
        comparator: str,
        forecast_value: float,
        baseline_probability: float,
        hourly: list[HourlyForecast],
    ) -> WeatherAIAssessment:
        prompt = _build_prompt(
            title=title,
            city=city,
            kind=kind,
            threshold=threshold,
            comparator=comparator,
            forecast_value=forecast_value,
            baseline_probability=baseline_probability,
            hourly=hourly,
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative weather prediction-market risk analyst. "
                        "Return only JSON. Do not invent weather data. Keep adjustments small."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            r = self._http.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            raw = r.json()
            content = raw["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            raise WeatherAIError(f"weather AI assessment failed: {e}") from e
        return _coerce_assessment(data)


def _build_prompt(
    *,
    title: str,
    city: str,
    kind: str,
    threshold: float,
    comparator: str,
    forecast_value: float,
    baseline_probability: float,
    hourly: list[HourlyForecast],
) -> str:
    rows = [
        {
            "start": h.start_time.isoformat(),
            "temp_f": h.temperature_f,
            "forecast": h.short_forecast,
            "precip_prob": h.probability_of_precipitation,
        }
        for h in hourly[:36]
    ]
    return json.dumps(
        {
            "task": (
                "Review the NWS hourly forecast features for a binary weather market. "
                "Return JSON with probability_adjustment between -0.05 and 0.05, "
                "uncertainty_multiplier between 0.8 and 1.5, confidence between 0 and 1, "
                "and a short rationale."
            ),
            "market_title": title,
            "city": city,
            "kind": kind,
            "threshold_f": threshold,
            "comparator": comparator,
            "nws_forecast_value_f": forecast_value,
            "baseline_probability_yes": baseline_probability,
            "hourly": rows,
        },
        separators=(",", ":"),
    )


def _coerce_assessment(data: dict[str, Any]) -> WeatherAIAssessment:
    try:
        adjustment = float(data.get("probability_adjustment", 0.0))
        multiplier = float(data.get("uncertainty_multiplier", 1.0))
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        raise WeatherAIError("weather AI returned non-numeric assessment")
    return WeatherAIAssessment(
        probability_adjustment=max(-0.05, min(0.05, adjustment)),
        uncertainty_multiplier=max(0.8, min(1.5, multiplier)),
        confidence=max(0.0, min(1.0, confidence)),
        rationale=str(data.get("rationale") or "")[:240],
    )
