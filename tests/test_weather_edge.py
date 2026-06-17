from datetime import datetime, timedelta, timezone

import pytest

from src.clients.nws import HourlyForecast
from src.clients.weather_ai import WeatherAIAssessment
from src.strategies.weather_edge import (
    estimate_contract,
    parse_weather_contract,
    probability_threshold,
)


def test_probability_threshold_above():
    p = probability_threshold(mean=90, threshold=85, sigma=5, comparator="above")
    assert p == pytest.approx(0.8413, abs=1e-3)


def test_probability_threshold_below():
    p = probability_threshold(mean=70, threshold=75, sigma=5, comparator="below")
    assert p == pytest.approx(0.8413, abs=1e-3)


def test_parser_accepts_temperature_market():
    c = parse_weather_contract("Will New York high temperature be above 85 degrees today?", venue="kalshi", market_id="KXTEST")
    assert c is not None
    assert c.city == "new york"
    assert c.kind == "temp_high"
    assert c.timezone_name == "America/New_York"
    assert c.threshold == 85
    assert c.comparator == "above"


def test_parser_rejects_sports_hurricanes_false_positive():
    c = parse_weather_contract("Will the Carolina Hurricanes win the 2026 Stanley Cup?")
    assert c is None


def test_parser_handles_less_than_temperature_market():
    c = parse_weather_contract("Will the high temp in Austin be <90° on Jun 9, 2026?")
    assert c is not None
    assert c.city == "austin"
    assert c.comparator == "below"
    assert c.threshold == 90


def test_parser_handles_kalshi_city_aliases():
    c = parse_weather_contract("Will the **high temp in LA** be >78° on Jun 10, 2026? KXHIGHLAX-26JUN10-T78")
    assert c is not None
    assert c.city == "los angeles"
    assert c.kind == "temp_high"
    assert c.threshold == 78

    nola = parse_weather_contract("Will the minimum temperature be <70° on Jun 10, 2026? KXLOWTNOLA-26JUN10-T70")
    assert nola is not None
    assert nola.city == "new orleans"
    assert nola.kind == "temp_low"


def test_parser_rejects_range_bucket_until_supported():
    c = parse_weather_contract("Will the high temp in Austin be 96-97° on Jun 9, 2026?")
    assert c is None


def test_estimate_contract_buy_yes_edge():
    c = parse_weather_contract("Will Chicago high temperature be above 80 degrees today?", market_id="WX")
    assert c is not None
    now = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
    hourly = [
        HourlyForecast(
            start_time=now + timedelta(hours=i),
            end_time=now + timedelta(hours=i + 1),
            temperature_f=88,
            short_forecast="Sunny",
        )
        for i in range(6)
    ]
    est = estimate_contract(c, hourly, yes_ask=0.55, yes_bid=0.52, uncertainty_f=3, min_edge=0.08, now=now)
    assert est is not None
    assert est.p_yes == pytest.approx(0.99)
    assert est.recommendation == "buy_yes"
    assert est.confidence > 0.65


def test_estimate_uses_open_meteo_model_ensemble():
    c = parse_weather_contract("Will Chicago high temperature be above 85 degrees today?", market_id="WX")
    assert c is not None
    now = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
    nws = [
        HourlyForecast(
            start_time=now + timedelta(hours=i),
            end_time=now + timedelta(hours=i + 1),
            temperature_f=88,
            short_forecast="Sunny",
        )
        for i in range(12)
    ]
    models = {
        "open_meteo_best": [
            HourlyForecast(now + timedelta(hours=i), now + timedelta(hours=i + 1), 86, "model")
            for i in range(12)
        ],
        "noaa_gfs_hrrr": [
            HourlyForecast(now + timedelta(hours=i), now + timedelta(hours=i + 1), 84, "model")
            for i in range(12)
        ],
    }
    est = estimate_contract(
        c,
        nws,
        model_hourly=models,
        yes_ask=0.50,
        yes_bid=0.48,
        uncertainty_f=3,
        min_edge=0.02,
        min_confidence=0.0,
        now=now,
    )
    assert est is not None
    assert est.forecast_value == 86
    assert est.sigma > 3
    assert "open_meteo_best=86.0" in est.notes
    assert "noaa_gfs_hrrr=84.0" in est.notes


def test_estimate_uses_city_local_day():
    c = parse_weather_contract("Will the high temp in Chicago be >90° on Jun 9, 2026?", market_id="WX")
    assert c is not None
    # Chicago Jun 9 local day starts at 2026-06-09 05:00 UTC.
    hourly = [
        HourlyForecast(
            start_time=datetime(2026, 6, 9, hour, tzinfo=timezone.utc),
            end_time=datetime(2026, 6, 9, hour + 1, tzinfo=timezone.utc),
            temperature_f=temp,
            short_forecast="",
        )
        for hour, temp in [(3, 99), (6, 91), (7, 89)]
    ]
    est = estimate_contract(c, hourly, yes_ask=0.50, yes_bid=0.48, uncertainty_f=3)
    assert est is not None
    assert est.forecast_value == 91


def test_ai_assessment_can_adjust_probability_and_confidence():
    c = parse_weather_contract("Will Chicago high temperature be above 80 degrees today?", market_id="WX")
    assert c is not None
    now = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)
    hourly = [
        HourlyForecast(
            start_time=now + timedelta(hours=i),
            end_time=now + timedelta(hours=i + 1),
            temperature_f=82,
            short_forecast="Cloudy",
        )
        for i in range(12)
    ]
    baseline = estimate_contract(c, hourly, yes_ask=0.62, yes_bid=0.60, uncertainty_f=4, min_edge=0.02, now=now)
    adjusted = estimate_contract(
        c,
        hourly,
        yes_ask=0.62,
        yes_bid=0.60,
        uncertainty_f=4,
        min_edge=0.02,
        min_confidence=0.0,
        ai_assessment=WeatherAIAssessment(
            probability_adjustment=0.04,
            uncertainty_multiplier=0.9,
            confidence=0.9,
            rationale="cloud deck clearing supports warmer high",
        ),
        now=now,
    )
    assert baseline is not None
    assert adjusted is not None
    assert adjusted.p_yes > baseline.p_yes
    assert "AI adj=+0.040" in adjusted.notes
