"""Weather-market edge model.

This is deliberately conservative. It does not ask an LLM to invent a
forecast; it converts public weather forecasts into calibrated-ish binary
probabilities, then compares those probabilities against executable market
prices with a safety buffer.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal, Optional
from zoneinfo import ZoneInfo

from src.clients.weather_ai import WeatherAIAssessment
from src.clients.nws import HourlyForecast


MarketKind = Literal["temp_high", "temp_low", "precip"]


CITY_POINTS: dict[str, tuple[float, float]] = {
    "atlanta": (33.7490, -84.3880),
    "austin": (30.2672, -97.7431),
    "boston": (42.3601, -71.0589),
    "chicago": (41.8781, -87.6298),
    "dallas": (32.7767, -96.7970),
    "denver": (39.7392, -104.9903),
    "houston": (29.7604, -95.3698),
    "las vegas": (36.1716, -115.1391),
    "los angeles": (34.0522, -118.2437),
    "miami": (25.7617, -80.1918),
    "new orleans": (29.9511, -90.0715),
    "new york": (40.7128, -74.0060),
    "philadelphia": (39.9526, -75.1652),
    "phoenix": (33.4484, -112.0740),
    "san francisco": (37.7749, -122.4194),
    "seattle": (47.6062, -122.3321),
    "washington": (38.9072, -77.0369),
}

CITY_TIMEZONES: dict[str, str] = {
    "atlanta": "America/New_York",
    "austin": "America/Chicago",
    "boston": "America/New_York",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "denver": "America/Denver",
    "houston": "America/Chicago",
    "las vegas": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "miami": "America/New_York",
    "new orleans": "America/Chicago",
    "new york": "America/New_York",
    "philadelphia": "America/New_York",
    "phoenix": "America/Phoenix",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "washington": "America/New_York",
}

CITY_ALIASES: dict[str, str] = {
    "la": "los angeles",
    "lax": "los angeles",
    "nyc": "new york",
    "dc": "washington",
    "d.c.": "washington",
    "nola": "new orleans",
}


@dataclass(frozen=True)
class WeatherContract:
    venue: str
    market_id: str
    title: str
    kind: MarketKind
    city: str
    lat: float
    lon: float
    timezone_name: str
    threshold: float
    comparator: Literal["above", "below"]
    target_date: Optional[datetime] = None


@dataclass(frozen=True)
class WeatherEstimate:
    contract: WeatherContract
    forecast_value: float
    sigma: float
    p_yes: float
    market_yes_ask: Optional[float]
    market_yes_bid: Optional[float]
    edge_to_buy_yes: Optional[float]
    edge_to_buy_no: Optional[float]
    recommendation: Literal["buy_yes", "buy_no", "observe"]
    confidence: float
    notes: str


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probability_threshold(
    *,
    mean: float,
    threshold: float,
    sigma: float,
    comparator: Literal["above", "below"],
) -> float:
    if sigma <= 0:
        return float(mean > threshold) if comparator == "above" else float(mean < threshold)
    z = (threshold - mean) / sigma
    if comparator == "above":
        return 1.0 - normal_cdf(z)
    return normal_cdf(z)


def estimate_contract(
    contract: WeatherContract,
    hourly: Iterable[HourlyForecast],
    *,
    model_hourly: Optional[dict[str, Iterable[HourlyForecast]]] = None,
    yes_ask: Optional[float] = None,
    yes_bid: Optional[float] = None,
    uncertainty_f: float = 3.0,
    min_edge: float = 0.08,
    safety_buffer: float = 0.03,
    min_confidence: float = 0.65,
    ai_assessment: Optional[WeatherAIAssessment] = None,
    now: Optional[datetime] = None,
) -> Optional[WeatherEstimate]:
    now = now or datetime.now(timezone.utc)
    primary = _target_hours(list(hourly), contract.target_date, now, contract.timezone_name)
    if not primary:
        return None

    source_values = {"nws": _forecast_value(contract, primary)}
    if model_hourly:
        for source, rows in model_hourly.items():
            relevant = _target_hours(list(rows), contract.target_date, now, contract.timezone_name)
            if relevant:
                source_values[source] = _forecast_value(contract, relevant)

    values = list(source_values.values())
    forecast_value = statistics.median(values)
    if contract.kind in {"temp_high", "temp_low"}:
        if len(values) > 1:
            spread = statistics.pstdev(values)
            sigma = max(1.5, uncertainty_f * 0.75 + spread * 1.25)
        else:
            sigma = uncertainty_f
    else:
        sigma = 20.0
    if ai_assessment is not None:
        sigma *= ai_assessment.uncertainty_multiplier
    p_yes = probability_threshold(
        mean=forecast_value,
        threshold=contract.threshold,
        sigma=sigma,
        comparator=contract.comparator,
    )
    if ai_assessment is not None:
        p_yes += ai_assessment.probability_adjustment
    p_yes = max(0.01, min(0.99, p_yes))

    edge_yes = None if yes_ask is None else p_yes - yes_ask - safety_buffer
    no_ask = None if yes_bid is None else 1.0 - yes_bid
    edge_no = None if no_ask is None else (1.0 - p_yes) - no_ask - safety_buffer
    recommendation: Literal["buy_yes", "buy_no", "observe"] = "observe"
    if edge_yes is not None and edge_yes >= min_edge:
        recommendation = "buy_yes"
    if edge_no is not None and edge_no >= min_edge and (edge_yes is None or edge_no > edge_yes):
        recommendation = "buy_no"

    confidence = _confidence(
        forecast_value=forecast_value,
        threshold=contract.threshold,
        sigma=sigma,
        hours=len(primary),
        has_executable_price=yes_ask is not None or yes_bid is not None,
        sources=len(values),
    )
    if ai_assessment is not None:
        confidence = max(0.0, min(1.0, 0.70 * confidence + 0.30 * ai_assessment.confidence))
    if confidence < min_confidence:
        recommendation = "observe"

    ai_note = ""
    if ai_assessment is not None:
        ai_note = (
            f"; AI adj={ai_assessment.probability_adjustment:+.3f}, "
            f"unc_mult={ai_assessment.uncertainty_multiplier:.2f}, "
            f"ai_conf={ai_assessment.confidence:.2f}"
        )
        if ai_assessment.rationale:
            ai_note += f", {ai_assessment.rationale}"

    source_note = ", ".join(f"{k}={v:.1f}" for k, v in sorted(source_values.items()))
    return WeatherEstimate(
        contract=contract,
        forecast_value=forecast_value,
        sigma=sigma,
        p_yes=p_yes,
        market_yes_ask=yes_ask,
        market_yes_bid=yes_bid,
        edge_to_buy_yes=edge_yes,
        edge_to_buy_no=edge_no,
        recommendation=recommendation,
        confidence=confidence,
        notes=(
            f"ensemble {'min' if contract.kind == 'temp_low' else 'max'}={forecast_value:.1f}, "
            f"sources[{source_note}], sigma={sigma:.1f}, local_day={contract.timezone_name}"
            f"{ai_note}"
        ),
    )


def _forecast_value(contract: WeatherContract, relevant: list[HourlyForecast]) -> float:
    if contract.kind == "temp_low":
        return min(h.temperature_f for h in relevant)
    if contract.kind == "temp_high":
        return max(h.temperature_f for h in relevant)
    return max((h.probability_of_precipitation or 0.0) for h in relevant)


def parse_weather_contract(title: str, *, venue: str = "", market_id: str = "") -> Optional[WeatherContract]:
    text = _clean(title)
    if _looks_like_false_positive(text):
        return None

    city = _find_city(text)
    if not city:
        return None

    kind: Optional[MarketKind] = None
    if re.search(r"\b(high|max|over|above|hit|reach).*(temp|temperature|degrees?)", text) or " high " in f" {text} ":
        kind = "temp_high"
    if re.search(r"\b(low|min|under|below).*(temp|temperature|degrees?)", text) or " low " in f" {text} ":
        kind = "temp_low"
    # Precipitation contracts need station-specific accumulated observations,
    # not just hourly probability-of-precipitation. Keep v1 on temperature.
    if any(w in text for w in ("rain", "precip", "snow")):
        return None
    if kind is None:
        return None

    if _looks_like_range_bucket(text):
        return None

    threshold = _find_threshold(text)
    if threshold is None:
        return None

    comparator: Literal["above", "below"] = "above"
    if "<" in text or any(w in text for w in ("below", "under", "less than", "lower than")):
        comparator = "below"
    elif ">" in text:
        comparator = "above"
    elif kind == "temp_low" and any(w in text for w in ("at or below", "below")):
        comparator = "below"

    lat, lon = CITY_POINTS[city]
    return WeatherContract(
        venue=venue,
        market_id=market_id,
        title=title,
        kind=kind,
        city=city,
        lat=lat,
        lon=lon,
        timezone_name=CITY_TIMEZONES[city],
        threshold=threshold,
        comparator=comparator,
        target_date=_find_target_date(text),
    )


def _target_hours(
    hourly: list[HourlyForecast],
    target_date: Optional[datetime],
    now: datetime,
    timezone_name: str,
) -> list[HourlyForecast]:
    if target_date is None:
        end = now + timedelta(hours=30)
        return [h for h in hourly if h.start_time <= end and h.end_time >= now]
    local_zone = ZoneInfo(timezone_name)
    local_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=local_zone)
    start = local_start.astimezone(timezone.utc)
    end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    return [h for h in hourly if h.start_time < end and h.end_time > start]


def _confidence(
    *,
    forecast_value: float,
    threshold: float,
    sigma: float,
    hours: int,
    has_executable_price: bool,
    sources: int = 1,
) -> float:
    distance = abs(forecast_value - threshold)
    separation = min(1.0, distance / max(1.0, sigma * 2.0))
    coverage = min(1.0, hours / 12.0)
    price_quality = 1.0 if has_executable_price else 0.4
    source_quality = min(1.0, sources / 3.0)
    return max(0.0, min(1.0, 0.48 * separation + 0.24 * coverage + 0.14 * price_quality + 0.14 * source_quality))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _find_city(text: str) -> Optional[str]:
    for city in sorted(CITY_POINTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", text):
            return city
    for alias, city in sorted(CITY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return city
        if len(alias) >= 3 and alias in text:
            return city
    return None


def _find_threshold(text: str) -> Optional[float]:
    m = re.search(r"[<>]\s*(-?\d+(?:\.\d+)?)\s*(?:°|degrees?|f\b)?", text)
    if m:
        return float(m.group(1))
    m = re.search(
        r"(?:above|below|under|over|less than|greater than|at least|at most)\s*(-?\d+(?:\.\d+)?)",
        text,
    )
    if m:
        return float(m.group(1))
    candidates = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:°|degrees?|f\b|inches?|in\b)?", text)]
    plausible = [x for x in candidates if -40 <= x <= 130 and int(x) not in range(2000, 2100)]
    if not plausible:
        return None
    return plausible[0]


def _find_target_date(text: str) -> Optional[datetime]:
    months = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(20\d{2})\b", text)
    if not m:
        return None
    month = months.get(m.group(1))
    if not month:
        return None
    return datetime(int(m.group(3)), month, int(m.group(2)), tzinfo=timezone.utc)


def _looks_like_false_positive(text: str) -> bool:
    sports = ("hurricanes", "stanley cup", "wins by", "runs scored", "points scored")
    geo = ("ukraine", "russia", "nato")
    return any(w in text for w in sports + geo)


def _looks_like_range_bucket(text: str) -> bool:
    return bool(re.search(r"\b-?\d+(?:\.\d+)?\s*[-–]\s*-?\d+(?:\.\d+)?\s*°?", text))
